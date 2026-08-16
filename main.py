# ============================================
# ZOOM BOT CENTRAL SERVER - FINAL
# ============================================
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import socketio
import uvicorn
from datetime import datetime
from typing import Dict, List, Optional
import uuid

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
socket_app = socketio.ASGIApp(sio, app)

# ======================
# DATA
# ======================
workers: Dict[str, dict] = {}
running_tasks: Dict[str, dict] = {}          # task_id -> info
active_meetings: Dict[str, dict] = {}        # meeting_code -> info
kill_history: List[dict] = []                # record of killed meetings

class StartBotRequest(BaseModel):
    meeting_code: str
    passcode: str = ""
    bot_count: int
    duration_minutes: int = 10
    name_type: str = "indian"
    custom_names: Optional[List[str]] = None

class KillMeetingRequest(BaseModel):
    meeting_code: str

# ======================
# SOCKET EVENTS
# ======================
@sio.event
async def connect(sid, environ):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Connected: {sid}")

@sio.event
async def disconnect(sid):
    for wid, info in list(workers.items()):
        if info.get("sid") == sid:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Worker offline: {wid}")
            del workers[wid]
            break

@sio.event
async def register_worker(sid, data):
    worker_id = data.get("worker_id")
    max_capacity = data.get("max_capacity", 4)
    workers[worker_id] = {
        "sid": sid,
        "max_capacity": max_capacity,
        "free_capacity": max_capacity,
        "last_seen": datetime.now().isoformat()
    }
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Registered: {worker_id} | Cap: {max_capacity}")
    await sio.emit("registered", {"status": "ok"}, to=sid)

@sio.event
async def update_capacity(sid, data):
    worker_id = data.get("worker_id")
    free = data.get("free_capacity", 0)
    if worker_id in workers:
        workers[worker_id]["free_capacity"] = free
        workers[worker_id]["last_seen"] = datetime.now().isoformat()

@sio.event
async def task_completed(sid, data):
    task_id = data.get("task_id")
    worker_id = data.get("worker_id")
    bots_completed = data.get("bots_completed", 0)

    if task_id in running_tasks:
        meeting = running_tasks[task_id].get("meeting_code")
        if meeting in active_meetings:
            active_meetings[meeting]["running_bots"] = max(0, active_meetings[meeting].get("running_bots", 0) - bots_completed)
            if active_meetings[meeting]["running_bots"] <= 0:
                active_meetings[meeting]["status"] = "completed"
        del running_tasks[task_id]

    if worker_id in workers:
        workers[worker_id]["free_capacity"] = min(
            workers[worker_id]["max_capacity"],
            workers[worker_id]["free_capacity"] + bots_completed
        )

# ======================
# API
# ======================
@app.get("/")
async def root():
    return {
        "message": "Zoom Bot Central Server",
        "workers_online": len(workers),
        "total_free_capacity": sum(w["free_capacity"] for w in workers.values())
    }

@app.get("/status")
@app.get("/api/status")
async def status():
    total_capacity = sum(w["max_capacity"] for w in workers.values())
    total_free = sum(w["free_capacity"] for w in workers.values())
    running_bots = sum(m.get("running_bots", 0) for m in active_meetings.values())

    return {
        "workers": workers,
        "registered_workers": [
            {"worker_id": wid, "capacity": info["max_capacity"], "free": info["free_capacity"]}
            for wid, info in workers.items()
        ],
        "total_capacity": total_capacity,
        "total_free_capacity": total_free,
        "active_meetings": active_meetings,
        "running_bots": running_bots,
        "running_tasks": running_tasks,
        "kill_history": kill_history[-20:]   # last 20 records
    }

@app.post("/api/start-bots")
async def start_bots(req: StartBotRequest):
    if req.bot_count < 1 or req.bot_count > 300:
        raise HTTPException(400, "Bot count must be 1-300")

    total_free = sum(w["free_capacity"] for w in workers.values())
    if total_free < 1:
        raise HTTPException(503, "No free workers available")

    task_id = str(uuid.uuid4())[:8]
    remaining = req.bot_count
    assigned = []

    sorted_workers = sorted(workers.items(), key=lambda x: x[1]["free_capacity"], reverse=True)

    for worker_id, info in sorted_workers:
        if remaining <= 0:
            break
        if info["free_capacity"] <= 0:
            continue

        take = min(remaining, info["free_capacity"])
        remaining -= take
        info["free_capacity"] -= take

        await sio.emit("new_task", {
            "task_id": task_id,
            "meeting_code": req.meeting_code,
            "passcode": req.passcode,
            "bot_count": take,
            "duration_minutes": req.duration_minutes,
            "name_type": req.name_type,
            "custom_names": req.custom_names
        }, to=info["sid"])

        assigned.append({"worker": worker_id, "bots": take})

    running_tasks[task_id] = {
        "meeting_code": req.meeting_code,
        "bot_count": req.bot_count - remaining,
        "assigned": assigned,
        "started_at": datetime.now().isoformat()
    }

    # Active meeting record
    if req.meeting_code not in active_meetings:
        active_meetings[req.meeting_code] = {
            "meeting_code": req.meeting_code,
            "total_bots": req.bot_count - remaining,
            "running_bots": req.bot_count - remaining,
            "started_at": datetime.now().isoformat(),
            "status": "running",
            "duration_minutes": req.duration_minutes
        }
    else:
        active_meetings[req.meeting_code]["running_bots"] += (req.bot_count - remaining)
        active_meetings[req.meeting_code]["total_bots"] += (req.bot_count - remaining)
        active_meetings[req.meeting_code]["status"] = "running"

    return {
        "success": True,
        "task_id": task_id,
        "message": f"{req.bot_count - remaining} bots started",
        "total_bots": req.bot_count - remaining,
        "assigned": assigned,
        "pending": remaining
    }

@app.post("/api/kill-meeting")
@app.post("/api/terminate")
async def kill_meeting(req: KillMeetingRequest = None):
    meeting_code = req.meeting_code if req else None

    # Send kill signal to all workers
    for worker_id, info in workers.items():
        await sio.emit("terminate", {
            "meeting_code": meeting_code   # None = kill all
        }, to=info["sid"])

        # Restore capacity
        info["free_capacity"] = info["max_capacity"]

    # Record
    if meeting_code and meeting_code in active_meetings:
        record = {
            "meeting_code": meeting_code,
            "bots": active_meetings[meeting_code].get("total_bots", 0),
            "killed_at": datetime.now().isoformat(),
            "status": "killed"
        }
        kill_history.append(record)
        active_meetings[meeting_code]["status"] = "killed"
        active_meetings[meeting_code]["running_bots"] = 0

    # Clear running tasks of this meeting
    to_delete = [tid for tid, t in running_tasks.items() if t.get("meeting_code") == meeting_code]
    for tid in to_delete:
        del running_tasks[tid]

    return {
        "success": True,
        "message": f"Kill signal sent for meeting {meeting_code}" if meeting_code else "All bots terminated",
        "record": record if meeting_code else None
    }

if __name__ == "__main__":
    uvicorn.run(socket_app, host="0.0.0.0", port=8000)
