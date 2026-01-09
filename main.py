import os
import uuid
import shutil
from fastapi import FastAPI, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from yt_dlp import YoutubeDL

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = FastAPI()
templates = Jinja2Templates(directory="templates")

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CLIENT_SECRETS_FILE = "credentials.json"
REDIRECT_URI = "http://localhost:8000/oauth/callback"

os.makedirs("temp", exist_ok=True)
user_tokens = {}

# ---------------- HOME ----------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ---------------- GOOGLE LOGIN ----------------
@app.get("/login")
def login():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    return {"auth_url": auth_url}


@app.get("/oauth/callback")
def oauth_callback(code: str):
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    flow.fetch_token(code=code)
    creds = flow.credentials

    session_id = str(uuid.uuid4())
    user_tokens[session_id] = creds.to_json()

    return {
        "message": "Login successful",
        "session_id": session_id
    }


# ---------------- BACKGROUND TASK ----------------
def download_and_upload(video_url: str, creds_json: str):
    temp_id = str(uuid.uuid4())
    temp_path = f"temp/{temp_id}.mp4"

    ydl_opts = {
        "outtmpl": temp_path,
        "format": "best",
        "quiet": True
    }

    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

    creds = Credentials.from_authorized_user_info(eval(creds_json))
    drive = build("drive", "v3", credentials=creds)

    media = MediaFileUpload(temp_path, resumable=True)
    file_metadata = {"name": os.path.basename(temp_path)}

    drive.files().create(
        body=file_metadata,
        media_body=media
    ).execute()

    shutil.rmtree("temp")
    os.makedirs("temp", exist_ok=True)


# ---------------- UPLOAD ----------------
@app.post("/upload")
def upload(
    background_tasks: BackgroundTasks,
    video_url: str = Form(...),
    session_id: str = Form(...)
):
    if session_id not in user_tokens:
        return {"error": "Not logged in"}

    background_tasks.add_task(
        download_and_upload,
        video_url,
        user_tokens[session_id]
    )

    return {"status": "Upload started"}
