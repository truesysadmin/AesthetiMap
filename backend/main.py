import os
import glob
import json
import asyncio
import time
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, Dict
import sys

# Add root directory to sys.path to import renderer
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import renderer

app = FastAPI(title="AesthetiMap API")

MAX_AGE_DAYS = float(os.getenv("MAX_CLEANUP_DAYS", "7"))
MAX_SIZE_MB = float(os.getenv("MAX_CLEANUP_MB", "1024")) # default 1 GB
CLEANUP_INTERVAL_HOURS = float(os.getenv("CLEANUP_INTERVAL_HOURS", "6"))
NUM_WORKERS = int(os.getenv("NUM_WORKERS", "2"))
MAX_RETRIES = 3

# Task queue and event storage
task_queue = asyncio.Queue()
task_events: Dict[str, asyncio.Queue] = {}

async def worker():
    """Worker to process map generation tasks."""
    while True:
        task_data = await task_queue.get()
        task_id = task_data["task_id"]
        req = task_data["request"]
        event_queue = task_events.get(task_id)
        loop = asyncio.get_event_loop()

        def callback(message: str, progress: Optional[int] = None):
            if event_queue:
                data = {"type": "log", "message": message}
                if progress is not None:
                    data = {"type": "progress", "percent": progress, "message": message}
                loop.call_soon_threadsafe(lambda: event_queue.put_nowait(data))

        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if attempt > 1:
                    callback(f"⚠️ Attempt {attempt-1} failed. Retrying ({attempt}/{MAX_RETRIES})...")
                    await asyncio.sleep(2) # Small backoff

                # Run the heavy rendering in a thread pool to avoid blocking the event loop
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
                        show_buildings=req.show_buildings,
                        show_contours=req.show_contours,
                        callback=callback
                    )
                )
                
                if event_queue:
                    filename = os.path.basename(result_file)
                    await event_queue.put({"type": "done", "url": f"/api/posters/{filename}"})
                
                success = True
                break # Exit retry loop on success

            except Exception as e:
                print(f"Error in worker for task {task_id} (Attempt {attempt}): {e}")
                if attempt == MAX_RETRIES:
                    if event_queue:
                        await event_queue.put({"type": "error", "message": f"Failed after {MAX_RETRIES} attempts: {str(e)}"})
                # Continue to next attempt

        task_queue.task_done()
        # Clean up event queue after some time to allow the stream to finish
        await asyncio.sleep(10)
        if task_id in task_events:
            del task_events[task_id]

async def cleanup_loop():
    while True:
        try:
            for directory in ["posters", "cache"]:
                if not os.path.exists(directory):
                    continue
                
                # Check for age
                if MAX_AGE_DAYS > 0:
                    cutoff = time.time() - (MAX_AGE_DAYS * 86400)
                    for root, _, files in os.walk(directory):
                        for file in files:
                            path = os.path.join(root, file)
                            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                                os.remove(path)
                                print(f"Cleaned up old file: {path}")

                # Check for overall directory size
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
                        # Delete oldest files first
                        all_files.sort(key=lambda x: x[2])
                        for path, size, _ in all_files:
                            try:
                                os.remove(path)
                                total_size -= size
                                print(f"Deleted {path} to free space")
                                if total_size <= target_bytes:
                                    break
                            except OSError:
                                pass
        except Exception as e:
            print(f"Automatic cleanup error: {e}")
            
        await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(cleanup_loop())
    # Start workers
    for _ in range(NUM_WORKERS):
        asyncio.create_task(worker())

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

@app.get("/api/themes")
def get_themes():
    themes = []
    for f in glob.glob("themes/*.json"):
        try:
            with open(f, 'r', encoding='utf-8') as jf:
                data = json.load(jf)
                theme_id = os.path.basename(f).replace('.json', '')
                themes.append({
                    "id": theme_id,
                    "name": data.get("name", theme_id.replace('_', ' ').title())
                })
        except Exception as e:
            print(f"Error loading theme {f}: {e}")
            
    # Sort themes by name for a better UI experience
    return {"themes": sorted(themes, key=lambda x: x["name"])}

@app.post("/api/generate_map_stream")
async def generate_map_stream(req: GenerateRequest):
    # Create unique task ID
    task_id = f"task_{int(time.time() * 1000)}"
    
    # Initialize event queue for this task
    event_queue = asyncio.Queue()
    task_events[task_id] = event_queue
    
    # Add task to queue
    await task_queue.put({
        "task_id": task_id,
        "request": req
    })
    
    async def iter_output():
        # Inform the user they are in the queue
        yield json.dumps({"type": "log", "message": "Task queued, waiting for worker..."}) + "\n"
        
        while True:
            try:
                # Wait for events from the worker
                event = await asyncio.wait_for(event_queue.get(), timeout=300)
                yield json.dumps(event) + "\n"
                
                if event["type"] in ["done", "error"]:
                    break
            except asyncio.TimeoutError:
                yield json.dumps({"type": "error", "message": "Generation timed out."}) + "\n"
                break
            except Exception as e:
                yield json.dumps({"type": "error", "message": str(e)}) + "\n"
                break

    return StreamingResponse(iter_output(), media_type="application/x-ndjson")

@app.get("/api/posters/{filename}")
def get_poster(filename: str):
    file_path = os.path.join("posters", filename)
    if os.path.exists(file_path):
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Poster not found")
