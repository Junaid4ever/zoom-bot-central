# minimal test - Railway
import os
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {"ok": True, "msg": "central alive"}

@app.get("/status")
def status():
    return {"workers": {}, "running_tasks": {}, "total_free_capacity": 0}

@app.get("/health")
def health():
    return {"ok": True}
