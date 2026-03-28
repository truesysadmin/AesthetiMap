import os
import glob
import json
import asyncio
import time
import traceback
import concurrent.futures
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, Dict
import sys
from contextlib import asynccontextmanager

# Add root directory to sys.path to import renderer
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import renderer

MAX_AGE_DAYS = float(os.getenv("MAX_CLEANUP_DAYS", "7"))
MAX_SIZE_MB = float(os.getenv("MAX_CLEANUP_MB", "1024"))
CLEANUP_INTERVAL_HOURS = float(os.getenv("CLEANUP_INTERVAL_HOURS", "6"))
NUM_WORKERS = int(os.getenv("NUM_WORKERS", "2"))
MAX_RETRIES = 3

# Task queue and event storage
task_queue = asyncio.Queue()
task_events: Dict[str, asyncio.Queue] = {}

# Use a ThreadPoolExecutor for rendering
# We will use asyncio.to_thread in newer Python or run_in_executor
import threading

async def worker():
    """Worker to process map generation tasks."""
    print(f"👷 [PID {os.getpid()}][Thread {threading.get_ident()}] Worker process initialized and waiting for tasks...")
    while True:
        try:
            print(f"🔍 [PID {os.getpid()}] Worker checking queue... (Queue size: {task_queue.qsize()})")
            # Simple check for tasks to keep the worker loop responsive
            try:
                task_data = await asyncio.wait_for(task_queue.get(), timeout=10)
            except asyncio.TimeoutError:
                continue
                
            task_id = task_data.get("task_id")
            print(f"📦 [PID {os.getpid()}] Worker picked up task {task_id}. Queue size: {task_queue.qsize()}")
            
            if not task_id:
                task_queue.task_done()
                continue
                
            req = task_data["request"]
            event_queue = task_events.get(task_id)
            loop = asyncio.get_event_loop()
            
            print(f"🛠️ [PID {os.getpid()}] Starting heavy rendering for {task_id}...")
            
            try:
                # Use a callback that puts data into the event queue
                def thread_safe_callback(message: str, progress: Optional[int] = None):
                    if event_queue:
                        data = {"type": "progress", "percent": progress, "message": message}
                        loop.call_soon_threadsafe(lambda: event_queue.put_nowait(data))

                # Use run_in_executor with default (Thread) executor
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
                        callback=thread_safe_callback
                    )
                )
                
                if event_queue:
                    filename = os.path.basename(result_file)
                    await event_queue.put({"type": "done", "url": f"/api/posters/{filename}"})
                
                print(f"✅ [PID {os.getpid()}] Task {task_id} completed successfully.")

            except Exception as e:
                print(f"❌ [PID {os.getpid()}] Error in rendering for task {task_id}: {e}")
                traceback.print_exc()
                if event_queue:
                    await event_queue.put({"type": "error", "message": f"Rendering failed: {str(e)}"})

            task_queue.task_done()
            await asyncio.sleep(5)
            if task_id in task_events:
                del task_events[task_id]
        except Exception as global_e:
            print(f"CRITICAL: [PID {os.getpid()}] Worker encountered global error: {global_e}")
            traceback.print_exc()
            await asyncio.sleep(1)

async def cleanup_loop():
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
    print(f"🚀 [PID {os.getpid()}] Starting backend lifespan with {NUM_WORKERS} workers...")
    asyncio.create_task(cleanup_loop())
    for i in range(NUM_WORKERS):
        print(f"👷 [PID {os.getpid()}] Starting worker {i+1}...")
        asyncio.create_task(worker())
    yield
    print(f"🛑 [PID {os.getpid()}] Backend lifespan shutting down...")
    executor.shutdown()

app = FastAPI(title="AesthetiMap API", lifespan=lifespan)

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
            
    return {"themes": sorted(themes, key=lambda x: x["name"])}

@app.get("/api/status")
async def get_status():
    return {
        "queue_size": task_queue.qsize(),
        "num_workers": NUM_WORKERS,
        "pid": os.getpid(),
        "task_events_count": len(task_events)
    }

@app.post("/api/generate_map_stream")
async def generate_map_stream(req: GenerateRequest):
    task_id = f"task_{int(time.time() * 1000)}"
    event_queue = asyncio.Queue()
    task_events[task_id] = event_queue
    
    await task_queue.put({
        "task_id": task_id,
        "request": req
    })
    print(f"➕ [PID {os.getpid()}] Task {task_id} added to queue. Queue size: {task_queue.qsize()}")
    
    async def iter_output():
        yield json.dumps({"type": "progress", "percent": 1, "message": "Task queued, waiting for worker..."}) + "\n"
        
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
    uvicorn.run(app, host="0.0.0.0", port=8000, workers=1)
