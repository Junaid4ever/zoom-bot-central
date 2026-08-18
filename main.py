# ============================================
# ZOOM BOT CENTRAL - Railway
# ============================================
import uuid
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import socketio

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
asgi_app = socketio.ASGIApp(sio, other_asgi_app=app)

workers = {}          # worker_id -> {sid, max_capacity, free_capacity, last_seen}
running_tasks = {}    # task_id -> task info

class StartBotRequest(BaseModel):
    meeting_code: str
    passcode: str = ""
    bot_count: int = 10
    duration_minutes: int = 120
    name_type: str = "indian"          # indian | english | custom
    custom_names: Optional[List[str]] = None

class TerminateRequest(BaseModel):
    meeting_code: Optional[str] = None

@sio.event
async def connect(sid, environ):
    print(f"Client connected: {sid}")

@sio.event
async def disconnect(sid):
    for wid, info in list(workers.items()):
        if info.get("sid") == sid:
            del workers[wid]
            print(f"Worker removed: {wid}")
            break

@sio.event
async def register_worker(sid, data):
    wid = data.get("worker_id")
    max_cap = int(data.get("max_capacity", 10))
    workers[wid] = {
        "sid": sid,
        "max_capacity": max_cap,
        "free_capacity": max_cap,
        "last_seen": datetime.now().isoformat()
    }
    await sio.emit("registered", {"worker_id": wid}, to=sid)
    print(f"Registered: {wid} capacity={max_cap}")

@sio.event
async def update_capacity(sid, data):
    wid = data.get("worker_id")
    if wid in workers:
        workers[wid]["free_capacity"] = int(data.get("free_capacity", 0))
        workers[wid]["last_seen"] = datetime.now().isoformat()

@sio.event
async def task_completed(sid, data):
    tid = data.get("task_id")
    if tid in running_tasks:
        del running_tasks[tid]
    print(f"Task completed: {tid}")

@app.get("/status")
@app.get("/api/status")
async def status():
    total_free = sum(w["free_capacity"] for w in workers.values())
    return {
        "workers": workers,
        "running_tasks": running_tasks,
        "total_free_capacity": total_free
    }

@app.post("/api/start-bots")
async def start_bots(req: StartBotRequest):
    if req.bot_count < 1:
        raise HTTPException(400, "bot_count must be >= 1")

    remaining = req.bot_count
    assigned = []

    # distribute across workers by free capacity
    sorted_workers = sorted(
        workers.items(),
        key=lambda x: x[1]["free_capacity"],
        reverse=True
    )

    for wid, info in sorted_workers:
        if remaining <= 0:
            break
        free = info["free_capacity"]
        if free <= 0:
            continue
        give = min(free, remaining)
        task_id = str(uuid.uuid4())[:8]
        payload = {
            "task_id": task_id,
            "meeting_code": req.meeting_code.strip().replace(" ", ""),
            "passcode": req.passcode or "",
            "bot_count": give,
            "duration_minutes": req.duration_minutes,
            "name_type": req.name_type,
            "custom_names": req.custom_names
        }
        await sio.emit("new_task", payload, to=info["sid"])
        running_tasks[task_id] = {
            "task_id": task_id,
            "meeting_code": payload["meeting_code"],
            "bot_count": give,
            "worker_id": wid,
            "started_at": datetime.now().isoformat(),
            "name_type": req.name_type
        }
        workers[wid]["free_capacity"] = max(0, free - give)
        assigned.append({"worker": wid, "bots": give, "task_id": task_id})
        remaining -= give

    if not assigned:
        raise HTTPException(503, "No free capacity. Start Colab worker first.")

    return {
        "success": True,
        "message": f"Started {req.bot_count - remaining} bots",
        "assigned": assigned,
        "remaining_unassigned": remaining
    }

@app.post("/api/terminate")
@app.post("/api/kill-meeting")
async def terminate(req: TerminateRequest = None):
    meeting = req.meeting_code if req else None
    await sio.emit("terminate", {"meeting_code": meeting})
    # clear tasks
    if meeting:
        for tid in list(running_tasks.keys()):
            if running_tasks[tid].get("meeting_code") == meeting:
                del running_tasks[tid]
    else:
        running_tasks.clear()
    # restore capacity
    for wid in workers:
        workers[wid]["free_capacity"] = workers[wid]["max_capacity"]
    return {"success": True, "message": "Terminate sent"}

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return open("dashboard.html", "r", encoding="utf-8").read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(asgi_app, host="0.0.0.0", port=8000)
