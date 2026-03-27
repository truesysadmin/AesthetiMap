import os
import subprocess
import glob
import json
import asyncio
import time
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

app = FastAPI(title="AesthetiMap API")

MAX_AGE_DAYS = float(os.getenv("MAX_CLEANUP_DAYS", "7"))
MAX_SIZE_MB = float(os.getenv("MAX_CLEANUP_MB", "1024")) # default 1 GB
CLEANUP_INTERVAL_HOURS = float(os.getenv("CLEANUP_INTERVAL_HOURS", "6"))

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
    distance: int = 18000
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

@app.get("/api/themes")
def get_themes():
    themes = []
    for f in glob.glob("themes/*.json"):
        theme_name = os.path.basename(f).replace('.json', '')
        themes.append(theme_name)
    return {"themes": sorted(themes)}

@app.post("/api/generate_map_stream")
def generate_map_stream(req: GenerateRequest):
    def iter_output():
        cmd = ["python", "-u", "renderer.py", "-c", req.city, "-C", req.country]
        
        if req.latitude and req.longitude:
            cmd.extend(["-lat", req.latitude, "-long", req.longitude])
            
        cmd.extend([
            "-t", req.theme,
            "-d", str(req.distance),
            "-W", str(req.width),
            "-H", str(req.height),
            "-f", req.format
        ])
        
        if req.no_title:
            cmd.append("--no-title")
        if req.no_coords:
            cmd.append("--no-coords")
        if req.gradient_tb:
            cmd.append("--gradient-tb")
        if req.gradient_lr:
            cmd.append("--gradient-lr")
        if req.text_position and req.text_position != "bottom":
            cmd.extend(["--text-position", req.text_position])
        if req.country_label:
            cmd.extend(["--country-label", req.country_label])
        if req.display_city:
            cmd.extend(["-dc", req.display_city])
        if req.display_country:
            cmd.extend(["-dC", req.display_country])
            
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        
        for line in iter(process.stdout.readline, ""):
            if line:
                clean_line = line.replace('\r', '').replace('\n', '')
                if clean_line.strip():
                    if clean_line.startswith("[PROGRESS]"):
                        parts = clean_line.replace("[PROGRESS] ", "").split("|", 1)
                        if len(parts) == 2:
                            yield json.dumps({
                                "type": "progress", 
                                "percent": int(parts[0].strip()),
                                "message": parts[1].strip()
                            }) + "\n"
                        continue
                        
                    yield json.dumps({"type": "log", "message": clean_line}) + "\n"
        
        process.wait()
        
        if process.returncode != 0:
            yield json.dumps({"type": "error", "message": "Script execution failed."}) + "\n"
            return
            
        # Search for the recently created poster with the exact pattern:
        # {city_slug}_{theme_name}_{timestamp}.{ext}
        # Note: city_slug might be multiple words connected by underscores.
        # To be safe, we just use glob pattern matching the theme and extension.
        search_pattern = f"posters/*_{req.theme}_*.{req.format}"
        files = glob.glob(search_pattern)
        files.sort(key=os.path.getmtime, reverse=True)
        
        if not files:
            yield json.dumps({"type": "error", "message": f"File not found with pattern: {search_pattern}"}) + "\n"
        else:
            latest_file = os.path.basename(files[0])
            yield json.dumps({"type": "done", "url": f"/api/posters/{latest_file}"}) + "\n"

    return StreamingResponse(iter_output(), media_type="application/x-ndjson")

@app.get("/api/posters/{filename}")
def get_poster(filename: str):
    file_path = os.path.join("posters", filename)
    if os.path.exists(file_path):
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Poster not found")
