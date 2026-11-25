import asyncio
import glob
import json
import logging
import math
import os
import re
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from lat_lon_parser import parse
from pydantic import BaseModel, Field, field_validator, model_validator

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import renderer  # noqa: E402
import tiles  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aesthetimap")
MAX_AGE_DAYS = float(os.getenv("MAX_CLEANUP_DAYS", "7"))
MAX_SIZE_MB = float(os.getenv("MAX_CLEANUP_MB", "1024"))
CLEANUP_INTERVAL_HOURS = float(os.getenv("CLEANUP_INTERVAL_HOURS", "6"))
MAX_SPAN_M = int(os.getenv("MAX_SPAN_M", "50000"))
MAX_QUEUED = int(os.getenv("MAX_QUEUED", "5"))
JOB_TIMEOUT_SECONDS = float(os.getenv("JOB_TIMEOUT_SECONDS", "600"))
STUCK_GRACE_SECONDS = float(os.getenv("STUCK_GRACE_SECONDS", "300"))
POSTERS_DIR = Path(renderer.POSTERS_DIR)
BUSY_MESSAGE = "The service is busy. Please try again in a minute."
TIMEOUT_MESSAGE = "The map took too long to generate. Try a smaller area."
THEME_COLOR_KEYS = frozenset({
    "bg", "text", "gradient_color", "water", "parks", "building", "road_motorway", "road_primary",
    "road_secondary", "road_tertiary", "road_residential", "road_default",
})


class GenerateRequest(BaseModel):
    city: str
    country: str
    latitude: Optional[str] = Field(default=None, max_length=40)
    longitude: Optional[str] = Field(default=None, max_length=40)
    theme: str = "terracotta"
    span: int = Field(default=20000, ge=500, le=MAX_SPAN_M)
    width: float = Field(default=12.0, ge=2, le=40, allow_inf_nan=False)
    height: float = Field(default=16.0, ge=2, le=40, allow_inf_nan=False)
    format: Literal["png", "svg", "pdf"] = "png"
    no_title: bool = False
    no_coords: bool = False
    gradient_tb: bool = False
    gradient_lr: bool = False
    text_position: Literal["bottom", "top", "center"] = "bottom"
    country_label: Optional[str] = None
    display_city: Optional[str] = None
    display_country: Optional[str] = None
    show_buildings: bool = False
    show_contours: bool = False
    show_heart: bool = False

    @field_validator("city", "country", "display_city", "display_country", "country_label")
    @classmethod
    def validate_label(cls, value, info):
        if value is None:
            return value
        if any(unicodedata.category(char) == "Cc" for char in value):
            raise ValueError("Control characters are not allowed")
        value = value.strip()
        if not value and info.field_name in {"display_city", "display_country", "country_label"}:
            return None
        if len(value) > 80 or (not value and info.field_name != "country"):
            raise ValueError("Labels must contain 1 to 80 characters")
        return value

    @field_validator("theme")
    @classmethod
    def validate_theme(cls, value):
        if value not in renderer.get_available_themes():
            raise ValueError("Unknown theme")
        return value

    @field_validator("latitude", "longitude")
    @classmethod
    def validate_coordinate(cls, value, info):
        if value is not None:
            limit = 85 if info.field_name == "latitude" else 180
            try:
                number = parse(value)
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError("Invalid coordinate") from exc
            if not math.isfinite(number) or not -limit <= number <= limit:
                raise ValueError("Coordinate is out of range")
        return value

    @model_validator(mode="after")
    def validate_location(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both latitude and longitude")
        if not self.country and self.latitude is None:
            raise ValueError("Country is required without coordinates")
        return self


@dataclass(eq=False)
class Job:
    id: str
    req: GenerateRequest
    events: asyncio.Queue = field(default_factory=asyncio.Queue)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    position: Optional[int] = None


jobs: dict[str, Job] = {}
pending: list[Job] = []
running_job: Optional[Job] = None
tasks: set[asyncio.Task] = set()
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="map-renderer")


def keep_task(coro):
    task = asyncio.create_task(coro)
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task


def update_positions():
    for position, job in enumerate(pending, 1):
        if job.position != position:
            job.position = position
            job.events.put_nowait({"type": "progress", "percent": 1, "message": f"Waiting in queue (position {position})..."})


def start_job(job):
    global running_job
    running_job = job
    keep_task(run_generation_task(job))


def enqueue_job(req):
    if running_job is not None and len(pending) >= MAX_QUEUED:
        raise HTTPException(status_code=503, detail=BUSY_MESSAGE, headers={"Retry-After": "60"})
    job = Job(uuid4().hex, req)
    jobs[job.id] = job
    if running_job is None:
        start_job(job)
    else:
        pending.append(job)
        update_positions()
    return job


def cancel_job(job):
    job.cancel_event.set()
    if job in pending:
        pending.remove(job)
        jobs.pop(job.id, None)
        update_positions()


def error_message(exc, task_id):
    if isinstance(exc, renderer.MapDataUnavailable):
        return "The map data server is busy right now. Please try again in a few minutes."
    if isinstance(exc, renderer.CityNotFound):
        return "Could not find this city. Check the spelling or enter coordinates."
    if isinstance(exc, renderer.NoMapData):
        return "There are no roads in this area. Try a larger area or another place."
    return f"Generation failed. Reference: {task_id[:8]}"


def log_generation_error(exc, task_id, cancelled=False):
    context = "Cancelled generation" if cancelled else "Generation"
    if isinstance(exc, (renderer.MapDataUnavailable, renderer.CityNotFound, renderer.NoMapData)):
        detail = " ".join(str(exc).splitlines())
        logger.warning("%s %s failed: %s: %s", context, task_id, type(exc).__name__, detail)
    else:
        logger.exception("%s %s failed", context, task_id)


async def run_generation_task(job):
    global running_job
    loop = asyncio.get_running_loop()

    def deliver(message, progress):
        if not job.cancel_event.is_set():
            job.events.put_nowait({"type": "progress", "percent": progress, "message": message})

    def callback(message, progress=None):
        loop.call_soon_threadsafe(deliver, message, progress)

    def render():
        if job.cancel_event.is_set():
            raise renderer.GenerationCancelled()
        kwargs = job.req.model_dump()
        kwargs["output_format"] = kwargs.pop("format")
        return renderer.run_generator(**kwargs, font_family=None, callback=callback, cancel_check=job.cancel_event.is_set)

    worker = None
    try:
        logger.info("Starting generation %s", job.id)
        worker = loop.run_in_executor(executor, render)
        done, _ = await asyncio.wait({worker}, timeout=JOB_TIMEOUT_SECONDS)
        if not done:
            job.cancel_event.set()
            job.events.put_nowait({"type": "error", "message": TIMEOUT_MESSAGE})
            done, _ = await asyncio.wait({worker}, timeout=STUCK_GRACE_SECONDS)
            if not done:
                # A thread cannot be killed and it holds the only render slot: let Docker restart us.
                logger.critical("Generation %s is stuck, exiting", job.id)
                os._exit(1)
        result = await asyncio.shield(worker)
        if not job.cancel_event.is_set():
            filename = Path(result).name
            job.events.put_nowait({"type": "done", "url": f"/api/posters/{quote(filename, safe='')}"})
    except renderer.GenerationCancelled:
        pass
    except asyncio.CancelledError:
        job.cancel_event.set()
        # A cancelled asyncio future must never release the renderer's thread slot.
        if worker is not None:
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            try:
                worker.result()
            except renderer.GenerationCancelled:
                pass
            except Exception as exc:
                log_generation_error(exc, job.id, cancelled=True)
        raise
    except Exception as exc:
        log_generation_error(exc, job.id)
        if not job.cancel_event.is_set():
            job.events.put_nowait({"type": "error", "message": error_message(exc, job.id)})
    finally:
        job.events.put_nowait({"type": "cancelled"})
        jobs.pop(job.id, None)
        running_job = None
        if pending:
            start_job(pending.pop(0))
            update_positions()


def cleanup_files():
    for directory in (POSTERS_DIR, Path(renderer.CACHE_DIR)):
        all_files = []
        cutoff = time.time() - MAX_AGE_DAYS * 86400
        for root, directories, filenames in os.walk(directory):
            directories[:] = [name for name in directories if not name.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                path = Path(root) / name
                try:
                    if not path.is_file():
                        continue
                    stat = path.stat()
                    if MAX_AGE_DAYS > 0 and stat.st_mtime < cutoff:
                        path.unlink()
                    else:
                        all_files.append((stat.st_mtime, path, stat.st_size))
                except OSError:
                    logger.warning("Could not clean file %r", str(path), exc_info=True)
        total = sum(size for _, _, size in all_files)
        if MAX_SIZE_MB > 0:
            for _, path, size in sorted(all_files):
                if total <= MAX_SIZE_MB * 1024 * 1024:
                    break
                try:
                    path.unlink()
                    total -= size
                except OSError:
                    logger.warning("Could not clean file %r", str(path), exc_info=True)


async def cleanup_loop():
    while True:
        try:
            await asyncio.to_thread(cleanup_files)
        except Exception:
            logger.exception("Automatic cleanup failed")
        await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup = keep_task(cleanup_loop())
    yield
    cleanup.cancel()
    for job in list(jobs.values()):
        cancel_job(job)
    await asyncio.gather(*list(tasks), return_exceptions=True)


app = FastAPI(title="AesthetiMap API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/api/config")
def get_config():
    return {"tiles_url": os.getenv("PUBLIC_TILES_URL", tiles.TILES_URL), "max_span": MAX_SPAN_M}


@app.get("/api/themes")
def get_themes():
    themes = []
    for filename in glob.glob(os.path.join(renderer.THEMES_DIR, "*.json")):
        try:
            with open(filename, encoding="utf-8") as source:
                data = json.load(source)
            theme_id = Path(filename).stem
            colors = {key: value for key, value in data.items()
                      if key in THEME_COLOR_KEYS and isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value)}
            for key, fallback in (("road_default", "road_tertiary"), ("building", "road_residential")):
                if key not in colors and fallback in colors:
                    colors[key] = colors[fallback]
            themes.append({"id": theme_id, "name": data.get("name", theme_id.replace("_", " ").title()),
                           "description": data.get("description", ""), "colors": colors})
        except Exception:
            logger.exception("Error loading theme %r", filename)
    return {"themes": sorted(themes, key=lambda theme: theme["name"])}


@app.get("/api/status")
async def get_status():
    return {"status": "ok", "running": int(running_job is not None), "queued": len(pending)}


class JobStreamingResponse(StreamingResponse):
    def __init__(self, job, *args, **kwargs):
        self.job = job
        super().__init__(*args, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            cancel_job(self.job)


@app.post("/api/generate_map_stream")
async def generate_map_stream(req: GenerateRequest):
    job = enqueue_job(req)

    async def iter_output():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(job.events.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield json.dumps({"type": "ping"}) + "\n"
                    continue
                if event["type"] == "cancelled":
                    break
                yield json.dumps(event) + "\n"
                if event["type"] in {"done", "error"}:
                    break
        finally:
            cancel_job(job)

    return JobStreamingResponse(job, iter_output(), media_type="application/x-ndjson",
                                headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"})


@app.get("/api/posters/{filename}")
def get_poster(filename: str):
    if (not filename or filename.startswith(".") or "/" in filename or "\\" in filename
            or Path(filename).suffix not in {".png", ".svg", ".pdf"}):
        raise HTTPException(status_code=404, detail="Poster not found")
    try:
        root = Path(POSTERS_DIR).resolve()
        path = (root / filename).resolve()
        valid = path.is_relative_to(root) and path.is_file()
    except (OSError, ValueError, RuntimeError):
        valid = False
    if not valid:
        raise HTTPException(status_code=404, detail="Poster not found")
    headers = {"X-Content-Type-Options": "nosniff"}
    if path.suffix == ".svg":
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return FileResponse(path, headers=headers)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
