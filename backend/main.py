import os
import glob
import json
import asyncio
import time
import traceback
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse, RedirectResponse
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, Dict
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from starlette.middleware.sessions import SessionMiddleware
from datetime import timedelta

from fastapi.security import OAuth2PasswordRequestForm
from fastapi import Depends, status
import backend.database as database
import backend.auth as auth
import backend.oauth as oauth_setup
from sqlalchemy.orm import Session

# Add root directory to sys.path to import renderer
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import renderer

MAX_AGE_DAYS = float(os.getenv("MAX_CLEANUP_DAYS", "7"))
MAX_SIZE_MB = float(os.getenv("MAX_CLEANUP_MB", "1024"))
CLEANUP_INTERVAL_HOURS = float(os.getenv("CLEANUP_INTERVAL_HOURS", "6"))

# Global storage for task events to allow streaming
task_events: Dict[str, asyncio.Queue] = {}

async def cleanup_loop():
    """Background loop to clean up old posters and cache files."""
    print(f"🧹 [PID {os.getpid()}] Cleanup loop started.")
    while True:
        try:
            for directory in ["posters", "cache"]:
                if not os.path.exists(directory):
                    continue
                
                if MAX_AGE_DAYS > 0:
                    cutoff = time.time() - (MAX_AGE_DAYS * 86400)
                    for root, _, files in os.walk(directory):
                        for file in files:
                            path = os.path.join(root, file)
                            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                                os.remove(path)
                                print(f"Cleaned up old file: {path}")

                if MAX_SIZE_MB > 0:
                    total_size = 0
                    all_files = []
                    for root, _, files in os.walk(directory):
                        for file in files:
                            path = os.path.join(root, file)
                            if os.path.isfile(path):
                                size = os.path.getsize(path)
                                total_size += size
                                all_files.append((path, size, os.path.getmtime(path)))
                    
                    target_bytes = MAX_SIZE_MB * 1024 * 1024
                    if total_size > target_bytes:
                        all_files.sort(key=lambda x: x[2])
                        for path, size, _ in all_files:
                            try:
                                os.remove(path)
                                total_size -= size
                                if total_size <= target_bytes:
                                    break
                            except OSError:
                                pass
        except Exception as e:
            print(f"Automatic cleanup error: {e}")
            
        await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"🚀 [PID {os.getpid()}] Starting backend lifespan...")
    asyncio.create_task(cleanup_loop())
    yield
    print(f"🛑 [PID {os.getpid()}] Backend lifespan shutting down...")

app = FastAPI(title="AesthetiMap API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=auth.SECRET_KEY)

class GenerateRequest(BaseModel):
    city: str
    country: str
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    theme: str = "terracotta"
    span: int = 20000
    width: float = 12.0
    height: float = 16.0
    format: str = "png"
    no_title: bool = False
    no_coords: bool = False
    gradient_tb: bool = False
    gradient_lr: bool = False
    text_position: str = "bottom"
    country_label: Optional[str] = None
    display_city: Optional[str] = None
    display_country: Optional[str] = None
    show_buildings: bool = False
    show_contours: bool = False
    poi_emoji: Optional[str] = None
    poi_size: int = 25

class UserCreate(BaseModel):
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

@app.post("/api/auth/register", response_model=dict)
def register_user(user: UserCreate, db: Session = Depends(database.get_db)):
    db_user = auth.get_user_by_email(db, email=user.email)
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    hashed_password = auth.get_password_hash(user.password)
    new_user = database.User(email=user.email, hashed_password=hashed_password, tier=database.UserTier.free)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "User registered successfully"}

@app.post("/api/auth/token", response_model=Token)
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(database.get_db)):
    user = auth.get_user_by_email(db, email=form_data.username)
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth.create_access_token(
        data={"sub": user.email, "tier": user.tier.value}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/api/auth/login/{provider}")
async def oauth_login(provider: str, request: Request):
    client = oauth_setup.oauth.create_client(provider)
    if not client:
        raise HTTPException(status_code=400, detail=f"Provider {provider} not supported or not configured")
    redirect_uri = request.url_for('oauth_callback', provider=provider)
    return await client.authorize_redirect(request, str(redirect_uri))

@app.get("/api/auth/callback/{provider}")
async def oauth_callback(provider: str, request: Request, db: Session = Depends(database.get_db)):
    client = oauth_setup.oauth.create_client(provider)
    if not client:
        raise HTTPException(status_code=400, detail="Provider not supported")
        
    try:
        token = await client.authorize_access_token(request)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Failed to authenticate with provider")

    email = None
    provider_id = None

    if provider == 'google':
        user_info = token.get('userinfo')
        if not user_info:
            raise HTTPException(status_code=400, detail="Failed to get user info from Google")
        email = user_info.get("email")
        provider_id = user_info.get("sub")
    elif provider == 'github':
        resp = await client.get('user', token=token)
        profile = resp.json()
        resp_emails = await client.get('user/emails', token=token)
        emails = resp_emails.json()
        primary_email = next((e['email'] for e in emails if e.get('primary')), None)
        email = profile.get("email") or primary_email
        provider_id = str(profile.get("id"))
        
    if not email:
        raise HTTPException(status_code=400, detail="Cannot retrieve email from provider")

    db_user = auth.get_user_by_email(db, email=email)
    if not db_user:
        db_user = database.User(email=email, auth_provider=provider, provider_id=provider_id, tier=database.UserTier.free)
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
    else:
        if not db_user.provider_id:
            db_user.auth_provider = provider
            db_user.provider_id = provider_id
            db.commit()

    access_token_expires = timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth.create_access_token(
        data={"sub": db_user.email, "tier": db_user.tier.value}, expires_delta=access_token_expires
    )
    return RedirectResponse(f"/?token={access_token}")

@app.get("/api/users/me")
def read_users_me(current_user: database.User = Depends(auth.get_current_active_user)):
    return {"email": current_user.email, "tier": current_user.tier.value}

@app.get("/api/users/history")
def get_user_history(current_user: database.User = Depends(auth.get_current_active_user), db: Session = Depends(database.get_db)):
    history_entries = db.query(database.GenerationHistory).filter(database.GenerationHistory.user_id == current_user.id).order_by(database.GenerationHistory.id.desc()).all()
    res = []
    for entry in history_entries:
        res.append({
            "id": entry.id,
            "filename": entry.filename,
            "city_name": entry.city_name,
            "country_name": entry.country_name,
            "theme": entry.theme,
            "created_at": entry.created_at.isoformat()
        })
    return {"history": res}

@app.get("/api/themes")
def get_themes():
    themes = []
    for f in glob.glob("themes/*.json"):
        try:
            with open(f, 'r', encoding='utf-8') as jf:
                data = json.load(jf)
                theme_id = Path(f).stem
                themes.append({
                    "id": theme_id,
                    "name": data.get("name", theme_id.replace('_', ' ').title()),
                    "description": data.get("description", "")
                })
        except Exception as e:
            print(f"Error loading theme {f}: {e}")
            
    return {"themes": sorted(themes, key=lambda x: x["name"])}

@app.get("/api/status")
async def get_status():
    return {
        "pid": os.getpid(),
        "active_tasks": len(task_events)
    }

async def run_generation_task(task_id: str, req: GenerateRequest, event_queue: asyncio.Queue, user_id: Optional[int] = None):
    """Actual worker task that runs in the background of the same process."""
    print(f"🛠️ [PID {os.getpid()}] Starting task {task_id} for {req.city}...")
    loop = asyncio.get_event_loop()
    
    try:
        def callback(message: str, progress: Optional[int] = None):
            data = {"type": "progress", "percent": progress, "message": message}
            loop.call_soon_threadsafe(lambda: event_queue.put_nowait(data))

        # Run the heavy rendering in a thread
        result_file = await loop.run_in_executor(
            None,
            lambda: renderer.run_generator(
                city=req.city,
                country=req.country,
                theme=req.theme,
                span=req.span,
                width=req.width,
                height=req.height,
                output_format=req.format,
                latitude=req.latitude,
                longitude=req.longitude,
                no_title=req.no_title,
                no_coords=req.no_coords,
                gradient_tb=req.gradient_tb,
                gradient_lr=req.gradient_lr,
                text_position=req.text_position,
                country_label=req.country_label,
                display_city=req.display_city,
                display_country=req.display_country,
                font_family=None,
                show_buildings=req.show_buildings,
                show_contours=req.show_contours,
                poi_emoji=req.poi_emoji,
                poi_size=req.poi_size,
                callback=callback
            )
        )
        
        filename = Path(result_file).name
        
        if user_id:
            with database.SessionLocal() as session:
                history = database.GenerationHistory(
                    user_id=user_id,
                    filename=filename,
                    city_name=req.city,
                    country_name=req.country,
                    theme=req.theme
                )
                session.add(history)
                session.commit()
                
        await event_queue.put({"type": "done", "url": f"/api/posters/{filename}"})
        print(f"✅ [PID {os.getpid()}] Task {task_id} finished.")

    except Exception as e:
        print(f"❌ [PID {os.getpid()}] Task {task_id} failed: {e}")
        traceback.print_exc()
        await event_queue.put({"type": "error", "message": str(e)})
    finally:
        # We keep the events for a bit so the stream can finish reading
        await asyncio.sleep(10)
        if task_id in task_events:
            del task_events[task_id]

@app.post("/api/generate_map_stream")
async def generate_map_stream(req: GenerateRequest, current_user: Optional[database.User] = Depends(auth.get_current_user)):
    user_tier = current_user.tier.value if current_user else "anonymous"

    # Enforce constraints based on user_tier
    if user_tier == "anonymous":
        if req.format in ["svg", "pdf"]:
            raise HTTPException(status_code=403, detail="SVG/PDF requires a Free or Premium account.")
        req.show_buildings = False
        req.show_contours = False
        req.poi_emoji = None

    premium_themes = ["kintsugi", "aurora_borealis"]
    if user_tier in ["anonymous", "free"]:
        if req.theme in premium_themes:
            raise HTTPException(status_code=403, detail="This theme is a Premium feature.")
        if req.poi_emoji:
            raise HTTPException(status_code=403, detail="Custom Map Markers are a Premium feature.")

    task_id = f"task_{int(time.time() * 1000)}"
    event_queue = asyncio.Queue()
    task_events[task_id] = event_queue
    
    # Start the task IMMEDIATELY in the same process
    user_id = current_user.id if current_user else None
    asyncio.create_task(run_generation_task(task_id, req, event_queue, user_id))
    print(f"➕ [PID {os.getpid()}] Task {task_id} started directly.")
    
    async def iter_output():
        # Inform the user
        yield json.dumps({"type": "progress", "percent": 1, "message": "Starting generation..."}) + "\n"
        
        while True:
            try:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=15)
                    yield json.dumps(event) + "\n"
                    if event["type"] in ["done", "error"]:
                        break
                except asyncio.TimeoutError:
                    yield json.dumps({"type": "ping"}) + "\n"
                    continue
            except Exception as e:
                yield json.dumps({"type": "error", "message": str(e)}) + "\n"
                break
                
    return StreamingResponse(iter_output(), media_type="application/x-ndjson")

@app.get("/api/posters/{filename}")
def get_poster(filename: str):
    path = os.path.join("posters", filename)
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(status_code=404, detail="Poster not found")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
