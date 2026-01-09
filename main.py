import subprocess
import json
import uuid
import asyncio
import os
import threading
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Settings
SCOPES = ["https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
CLIENT_SECRETS_FILE = "credentials.json"
REDIRECT_URI = "https://videotodrive.onrender.com/oauth/callback"

progress_data = {}
user_tokens = {}

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/login")
def login():
    flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    return {"auth_url": auth_url}

@app.get("/oauth/callback")
def oauth_callback(code: str):
    flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    flow.fetch_token(code=code)
    session_id = str(uuid.uuid4())
    user_tokens[session_id] = flow.credentials.to_json()
    html_content = f"<html><body><script>localStorage.setItem('session_id', '{session_id}'); window.location.href='/';</script></body></html>"
    return HTMLResponse(content=html_content)

@app.post("/check_session")
async def check_session(session_id: str = Form(...)):
    return {"status": "ok"} if session_id in user_tokens else JSONResponse(status_code=401, content={"status": "expired"})

@app.get("/progress/{task_id}")
async def progress_endpoint(task_id: str):
    async def event_generator():
        while True:
            data = progress_data.get(task_id, {"status": "Waiting...", "percent": 0})
            yield f"data: {json.dumps(data)}\n\n"
            if data.get("status") == "Completed" or "Error" in data.get("status"):
                break
            await asyncio.sleep(1)
    return StreamingResponse(event_generator(), media_type="text/event-stream")

def stream_to_drive(video_url, creds_json, task_id):
    process = None
    try:
        progress_data[task_id] = {"status": "Starting Cloud Stream...", "percent": 10}
        creds = Credentials.from_authorized_user_info(json.loads(creds_json))
        drive = build("drive", "v3", credentials=creds)

        # High-speed pipe command
        process = subprocess.Popen(
            ["yt-dlp", "-f", "best", "--no-part", "--no-buffer", "--user-agent", "Mozilla/5.0", "-o", "-", video_url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
        )

        # FIXED: resumable=False avoids the 'Illegal Seek' error on Linux/Render
        # Simple upload mode handles streams perfectly
        media = MediaIoBaseUpload(process.stdout, mimetype="video/mp4", resumable=False)
        file_metadata = {"name": f"CloudBolt_Video_{task_id}.mp4"}
        
        progress_data[task_id] = {"status": "Transferring Data (0-100%)...", "percent": 50}
        
        # Execute Simple Upload
        request = drive.files().create(body=file_metadata, media_body=media, fields="id")
        request.execute()

        progress_data[task_id] = {"status": "Completed", "percent": 100}
    except Exception as e:
        progress_data[task_id] = {"status": f"Error: {str(e)}", "percent": 0}
    finally:
        if process: process.kill()

@app.post("/upload")
async def upload(video_url: str = Form(...), session_id: str = Form(...)):
    if session_id not in user_tokens:
        return JSONResponse(status_code=401, content={"error": "Session Expired"})
    task_id = str(uuid.uuid4())[:8]
    threading.Thread(target=stream_to_drive, args=(video_url, user_tokens[session_id], task_id)).start()
    return {"task_id": task_id}
