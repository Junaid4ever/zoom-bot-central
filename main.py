# main.py
import asyncio
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import socketio
from pydantic import BaseModel
from typing import List, Optional

# ---------- Request Model ----------
class StartBotsRequest(BaseModel):
    meeting_code: str
    count: int
    passcode: Optional[str] = ""
    duration: Optional[int] = 2
    custom_names: Optional[List[str]] = []

# ---------- Socket.IO Server ----------
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*'
)

fastapi_app = FastAPI()
app = socketio.ASGIApp(sio, fastapi_app)  # <-- this is the ASGI app for uvicorn

# ---------- Global State ----------
connected_workers = 0
active_bots_count = 0

# ---------- Socket Events ----------
@sio.event
async def connect(sid, environ):
    global connected_workers
    connected_workers += 1
    print(f"✅ Worker Connected: {sid} (Total: {connected_workers})")

@sio.event
async def disconnect(sid):
    global connected_workers
    connected_workers -= 1
    print(f"❌ Worker Disconnected: {sid} (Total: {connected_workers})")

@sio.on('started')
async def on_started(sid, data):
    global active_bots_count
    active_bots_count = data.get('active', 0)
    print(f"🚀 Bots Started: {data}")

@sio.on('stopped')
async def on_stopped(sid, data):
    global active_bots_count
    active_bots_count = 0
    print(f"🛑 Bots Stopped")

@sio.on('status_update')
async def on_status_update(sid, data):
    global active_bots_count
    active_bots_count = data.get('active_bots', 0)

# ---------- FastAPI Routes ----------
@fastapi_app.get("/", response_class=HTMLResponse)
async def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Zoom Bot Central</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; background: #f0f2f5; }
            h1 { color: #2c3e50; }
            .card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            input, button, textarea { padding: 10px; margin: 5px 0; width: 100%; box-sizing: border-box; border: 1px solid #ddd; border-radius: 4px; }
            .btn { background: #3498db; color: white; border: none; cursor: pointer; font-weight: bold; }
            .btn-danger { background: #e74c3c; }
            .status { background: #e8f4f8; padding: 10px; border-radius: 4px; margin: 15px 0; }
            #log { background: #fff; padding: 10px; height: 150px; overflow-y: auto; border: 1px solid #ccc; white-space: pre-wrap; font-size: 13px; }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>🚀 Zoom Bot Central</h1>
            <div class="status" id="status">Loading...</div>
            <form id="botForm">
                <label>Meeting ID</label>
                <input type="text" id="meeting" placeholder="123456789" required>
                <label>Passcode (optional)</label>
                <input type="text" id="passcode" placeholder="Leave blank">
                <label>Number of Bots (Max 100)</label>
                <input type="number" id="count" value="5" min="1" max="100" required>
                <label>Duration (minutes)</label>
                <input type="number" id="duration" value="2" min="1" required>
                <label>Custom Names (one per line, optional)</label>
                <textarea id="names" rows="3" placeholder="Arjun Seth&#10;Mira bhai 😎&#10;Akash783"></textarea>
                <button type="submit" class="btn">▶ Start Bots</button>
            </form>
            <button id="stopBtn" class="btn btn-danger">⏹ Stop All Bots</button>
            <div id="log">Log will appear here...</div>
            <div style="margin-top:10px; color:#7f8c8d;">Active Workers: <span id="workerCount">0</span></div>
        </div>
        <script>
            const statusDiv = document.getElementById('status');
            const logDiv = document.getElementById('log');
            const form = document.getElementById('botForm');
            const stopBtn = document.getElementById('stopBtn');
            const workerSpan = document.getElementById('workerCount');

            async function fetchStatus() {
                try {
                    const res = await fetch('/status');
                    const data = await res.json();
                    statusDiv.innerHTML = `
                        <strong>Active Bots:</strong> ${data.active_bots} / 100 &nbsp;|&nbsp;
                        <strong>Workers:</strong> ${data.workers}
                    `;
                    workerSpan.textContent = data.workers;
                } catch(e) {}
            }

            function appendLog(msg) {
                const time = new Date().toLocaleTimeString();
                logDiv.innerHTML += `[${time}] ${msg}\\n`;
                logDiv.scrollTop = logDiv.scrollHeight;
            }

            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                const meeting = document.getElementById('meeting').value.trim();
                const passcode = document.getElementById('passcode').value.trim();
                const count = parseInt(document.getElementById('count').value);
                const duration = parseInt(document.getElementById('duration').value);
                const namesText = document.getElementById('names').value;
                const names = namesText.split('\\n').map(s => s.trim()).filter(Boolean);

                appendLog(`🚀 Sending ${count} bots...`);
                try {
                    const res = await fetch('/start', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ meeting_code: meeting, passcode, count, duration, custom_names: names })
                    });
                    const data = await res.json();
                    appendLog(`✅ ${data.message}`);
                } catch(e) { appendLog(`❌ ${e.message}`); }
                fetchStatus();
            });

            stopBtn.addEventListener('click', async () => {
                appendLog('⏹ Stopping...');
                try {
                    const res = await fetch('/stop', { method: 'POST' });
                    const data = await res.json();
                    appendLog(`✅ ${data.message}`);
                } catch(e) { appendLog(`❌ ${e.message}`); }
                fetchStatus();
            });

            setInterval(fetchStatus, 2000);
            fetchStatus();
        </script>
    </body>
    </html>
    """

@fastapi_app.post("/start")
async def start_bots(req: StartBotsRequest):
    global connected_workers
    if connected_workers == 0:
        raise HTTPException(503, "No Worker connected! Please run the Colab script first.")
    if req.count > 100:
        raise HTTPException(400, "Max 100 bots allowed.")
    
    # Emit to Worker via Socket.IO
    await sio.emit('start_bots', {
        'meeting_code': req.meeting_code,
        'passcode': req.passcode,
        'count': req.count,
        'duration': req.duration,
        'custom_names': req.custom_names
    })
    return {"message": f"Command sent! Starting {req.count} bots."}

@fastapi_app.post("/stop")
async def stop_bots():
    await sio.emit('stop_bots', {})
    return {"message": "Stop command sent to all Workers."}

@fastapi_app.get("/status")
async def status():
    global connected_workers, active_bots_count
    return {
        "active_bots": active_bots_count,
        "workers": connected_workers,
        "max_bots": 100
    }
