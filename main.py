import subprocess
import json
import uuid
import asyncio
import os
import threading
import requests
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

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
        progress_data[task_id] = {"status": "Initializing Engine...", "percent": 5}
        creds_data = json.loads(creds_json)
        access_token = creds_data['token']

        # 1. Start yt-dlp to get the video stream
        process = subprocess.Popen(
            ["yt-dlp", "-f", "best", "--no-part", "--no-buffer", "-o", "-", video_url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
        )

        # 2. Metadata for Google Drive
        metadata = {
            'name': f'CloudBolt_Video_{task_id}.mp4',
            'mimeType': 'video/mp4'
        }

        # 3. Use Requests with a custom generator to avoid "Illegal Seek"
        # This is the "Open Source" trick for unlimited streaming
        def file_generator():
            while True:
                chunk = process.stdout.read(512 * 1024) # 512KB chunks
                if not chunk:
                    break
                yield chunk

        headers = {"Authorization": f"Bearer {access_token}"}
        
        # We use a Multipart Upload approach that doesn't require Content-Length
        # This is why it works for all URLs regardless of size
        progress_data[task_id] = {"status": "Streaming Byte-by-Byte...", "percent": 50}
        
        files = {
            'metadata': (None, json.dumps(metadata), 'application/json; charset=UTF-8'),
            'file': (metadata['name'], file_generator(), 'video/mp4')
        }

        # Sending the request - This will stay open until yt-dlp finishes
        r = requests.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            headers=headers,
            files=files,
            stream=True
        )

        if r.status_code in [200, 201]:
            progress_data[task_id] = {"status": "Completed", "percent": 100}
        else:
            progress_data[task_id] = {"status": f"Cloud Error: {r.status_code}", "percent": 0}

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
