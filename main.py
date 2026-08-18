# main.py (minimal – बस यह चेक करें कि App deploy हो रहा है)
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "message": "Zoom Worker Server is running"}
