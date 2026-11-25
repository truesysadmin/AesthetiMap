import asyncio
import json
import logging
from pathlib import Path
import re
import threading
import unicodedata

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from backend import main as backend
import renderer


def req(city="Kyiv"):
    return backend.GenerateRequest(city=city, country="Ukraine")


async def settle():
    for _ in range(1000):
        if not backend.tasks:
            return
        await asyncio.sleep(0.002)
    pytest.fail("generation tasks did not finish")


async def until(predicate):
    for _ in range(1000):
        if predicate():
            return
        await asyncio.sleep(0.002)
    pytest.fail("worker did not reach expected state")


@pytest.fixture(autouse=True)
def clean_jobs():
    assert not backend.running_job
    backend.jobs.clear()
    backend.pending.clear()
    backend.tasks.clear()
    yield
    assert not backend.running_job
    assert not backend.pending
    assert not backend.jobs


def events(job):
    result = []
    while not job.events.empty():
        result.append(job.events.get_nowait())
    return result


def test_queue_full_order_and_positions(monkeypatch):
    first_started = threading.Event()
    release = threading.Event()
    order = []

    def render(**kwargs):
        order.append(kwargs["city"])
        if kwargs["city"] == "first":
            first_started.set()
            assert release.wait(3)
        return "map.png"

    monkeypatch.setattr(renderer, "run_generator", render)
    monkeypatch.setattr(backend, "MAX_QUEUED", 2)

    async def scenario():
        first = backend.enqueue_job(req("first"))
        try:
            await until(first_started.is_set)
            second = backend.enqueue_job(req("second"))
            third = backend.enqueue_job(req("third"))
            assert re.fullmatch(r"[0-9a-f]{32}", first.id)
            assert (await backend.get_status()) == {"status": "ok", "running": 1, "queued": 2}
            assert events(second)[0]["message"] == "Waiting in queue (position 1)..."
            assert events(third)[0]["message"] == "Waiting in queue (position 2)..."
            with pytest.raises(HTTPException) as caught:
                backend.enqueue_job(req("fourth"))
            assert caught.value.status_code == 503
            assert caught.value.headers == {"Retry-After": "60"}
            assert caught.value.detail == backend.BUSY_MESSAGE
        finally:
            release.set()
            await settle()
        assert order == ["first", "second", "third"]
        assert any(event.get("message") == "Waiting in queue (position 1)..." for event in events(third))

    asyncio.run(scenario())


def test_cancel_queued_and_running_keeps_thread_slot(monkeypatch):
    started = threading.Event()
    cancelled = threading.Event()
    release = threading.Event()
    order = []

    def render(**kwargs):
        order.append(kwargs["city"])
        if kwargs["city"] == "first":
            started.set()
            assert cancelled.wait(3)
            assert kwargs["cancel_check"]()
            assert release.wait(3)
            raise renderer.GenerationCancelled()
        return "map.png"

    monkeypatch.setattr(renderer, "run_generator", render)

    async def scenario():
        first = backend.enqueue_job(req("first"))
        try:
            await until(started.is_set)
            second = backend.enqueue_job(req("cancelled"))
            third = backend.enqueue_job(req("third"))
            backend.cancel_job(second)
            backend.cancel_job(first)
            cancelled.set()
            await asyncio.sleep(0.02)
            assert backend.running_job is first
            assert backend.pending == [third]
            assert order == ["first"]
            assert not any(event["type"] == "error" for event in events(first))
        finally:
            cancelled.set()
            release.set()
            await settle()
        assert order == ["first", "third"]

    asyncio.run(scenario())


def test_timeout_keeps_slot_until_worker_finishes(monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def render(**kwargs):
        started.set()
        assert release.wait(3)
        assert kwargs["cancel_check"]()
        raise renderer.GenerationCancelled()

    monkeypatch.setattr(renderer, "run_generator", render)
    monkeypatch.setattr(backend, "JOB_TIMEOUT_SECONDS", 0.02)

    async def scenario():
        job = backend.enqueue_job(req())
        try:
            await until(started.is_set)
            await until(job.cancel_event.is_set)
            assert backend.running_job is job
            assert {"type": "error", "message": backend.TIMEOUT_MESSAGE} in events(job)
        finally:
            release.set()
            await settle()

    asyncio.run(scenario())


def test_stuck_worker_exits_process(monkeypatch):
    release = threading.Event()
    exits = []

    def render(**kwargs):
        release.wait(3)
        raise renderer.GenerationCancelled()

    def fake_exit(code):
        exits.append(code)
        release.set()

    monkeypatch.setattr(renderer, "run_generator", render)
    monkeypatch.setattr(backend, "JOB_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(backend, "STUCK_GRACE_SECONDS", 0.02)
    monkeypatch.setattr(backend.os, "_exit", fake_exit)

    async def scenario():
        backend.enqueue_job(req())
        await until(lambda: exits)
        await settle()

    asyncio.run(scenario())
    assert exits == [1]


@pytest.mark.parametrize("exception,message", [
    (renderer.MapDataUnavailable, "The map data server is busy right now. Please try again in a few minutes."),
    (renderer.CityNotFound, "Could not find this city. Check the spelling or enter coordinates."),
    (renderer.NoMapData, "There are no roads in this area. Try a larger area or another place."),
    (RuntimeError, None),
])
def test_errors_are_sanitized(monkeypatch, exception, message, caplog):
    def fail(**kwargs):
        raise exception("private exception detail")

    monkeypatch.setattr(renderer, "run_generator", fail)

    async def scenario():
        job = backend.enqueue_job(req())
        await settle()
        error = next(event for event in events(job) if event["type"] == "error")
        assert error["message"] == (message or f"Generation failed. Reference: {job.id[:8]}")
        assert "private exception detail" not in json.dumps(error)

    asyncio.run(scenario())
    assert "private exception detail" in caplog.text
    records = [record for record in caplog.records if "failed" in record.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == (logging.ERROR if exception is RuntimeError else logging.WARNING)
    assert bool(records[0].exc_info) == (exception is RuntimeError)


@pytest.mark.parametrize("exception", [renderer.MapDataUnavailable, renderer.CityNotFound, renderer.NoMapData, RuntimeError])
def test_cancelled_task_logs_worker_errors(monkeypatch, caplog, exception):
    started = threading.Event()
    release = threading.Event()

    def fail(**kwargs):
        started.set()
        assert release.wait(3)
        raise exception("first line\nsecond line")

    monkeypatch.setattr(renderer, "run_generator", fail)

    async def scenario():
        backend.enqueue_job(req())
        task = next(iter(backend.tasks))
        try:
            await until(started.is_set)
            task.cancel()
            await asyncio.sleep(0)
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await settle()

    asyncio.run(scenario())
    records = [record for record in caplog.records if "Cancelled generation" in record.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == (logging.ERROR if exception is RuntimeError else logging.WARNING)
    assert bool(records[0].exc_info) == (exception is RuntimeError)
    if exception is not RuntimeError:
        assert "\n" not in records[0].getMessage()


def test_config(monkeypatch):
    client = TestClient(backend.app)
    monkeypatch.delenv("PUBLIC_TILES_URL", raising=False)
    assert client.get("/api/config").json() == {"tiles_url": backend.tiles.TILES_URL, "max_span": backend.MAX_SPAN_M}
    monkeypatch.setenv("PUBLIC_TILES_URL", "https://tiles.example/planet")
    assert client.get("/api/config").json() == {"tiles_url": "https://tiles.example/planet", "max_span": backend.MAX_SPAN_M}


def test_theme_colors_allowlist_and_fallback(monkeypatch, tmp_path):
    theme = {
        "name": "Test", "description": "A palette", "bg": "#Ab12cD", "text": "#123456",
        "road_tertiary": "#abcdef", "road_residential": "#FEDCBA", "road_default": "invalid",
        "building": "#fff", "water": "red", "parks": 123, "gradient_color": "#123456\n",
        "road_primary": "#234567", "road_secondary": "#345678", "road_motorway": "#456789",
        "unknown": "#ffffff",
    }
    (tmp_path / "test.json").write_text(json.dumps(theme))
    monkeypatch.setattr(renderer, "THEMES_DIR", str(tmp_path))
    response = TestClient(backend.app).get("/api/themes")
    assert response.status_code == 200
    assert response.json() == {"themes": [{
        "id": "test", "name": "Test", "description": "A palette",
        "colors": {
            "bg": "#Ab12cD", "text": "#123456", "road_tertiary": "#abcdef", "road_residential": "#FEDCBA",
            "road_primary": "#234567", "road_secondary": "#345678", "road_motorway": "#456789",
            "road_default": "#abcdef", "building": "#FEDCBA",
        },
    }]}
    theme.update(road_default="#111111", building="#222222", water="#333333", parks="#444444", gradient_color="#555555")
    (tmp_path / "test.json").write_text(json.dumps(theme))
    colors = TestClient(backend.app).get("/api/themes").json()["themes"][0]["colors"]
    assert colors["road_default"] == "#111111"
    assert colors["building"] == "#222222"
    assert colors["water"] == "#333333"
    assert colors["parks"] == "#444444"
    assert colors["gradient_color"] == "#555555"


def test_all_themes_have_valid_colors_and_no_emoji_names():
    themes = TestClient(backend.app).get("/api/themes").json()["themes"]
    assert themes
    for theme in themes:
        assert theme["colors"]
        assert theme["colors"].keys() <= backend.THEME_COLOR_KEYS
        assert all(re.fullmatch(r"#[0-9A-Fa-f]{6}", value) for value in theme["colors"].values())
        assert not any(unicodedata.category(char) in {"So", "Sk"} or re.match(r"[\ufe0e\ufe0f\U0001f1e6-\U0001f1ff]", char)
                       for char in theme["name"])


def test_stream_headers_encoded_url_and_generator_close(monkeypatch):
    def render(**kwargs):
        return "map with # spaces.svg"

    monkeypatch.setattr(renderer, "run_generator", render)

    async def scenario():
        response = await backend.generate_map_stream(req())
        assert response.headers["X-Accel-Buffering"] == "no"
        assert response.headers["Cache-Control"] == "no-store"
        output = [json.loads(chunk) async for chunk in response.body_iterator]
        assert output[-1] == {"type": "done", "url": "/api/posters/map%20with%20%23%20spaces.svg"}
        await settle()

    asyncio.run(scenario())


def test_stream_close_cancels_waiting_job(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(renderer, "run_generator", lambda **kwargs: release.wait(3) or "map.png")

    async def scenario():
        first = backend.enqueue_job(req())
        try:
            response = await backend.generate_map_stream(req("second"))
            waiting = backend.pending[0]
            assert json.loads(await anext(response.body_iterator))["percent"] == 1
            await response.body_iterator.aclose()
            assert waiting.cancel_event.is_set()
            assert waiting not in backend.pending
        finally:
            backend.cancel_job(first)
            release.set()
            await settle()

    asyncio.run(scenario())


@pytest.mark.parametrize("name", [
    "../outside.png", "nested/map.png", "nested\\map.png", ".hidden.png", "map.txt", "missing.png", "directory.png", "bad\x00.png",
])
def test_poster_invalid_names(name):
    (Path(backend.POSTERS_DIR) / "directory.png").mkdir(exist_ok=True)
    with pytest.raises(HTTPException) as caught:
        backend.get_poster(name)
    assert caught.value.status_code == 404


def test_poster_symlink_escape(tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"image")
    (Path(backend.POSTERS_DIR) / "escape.png").symlink_to(outside)
    with pytest.raises(HTTPException):
        backend.get_poster("escape.png")


@pytest.mark.parametrize("extension", ["png", "pdf", "svg"])
def test_poster_headers(extension):
    name = f"map.{extension}"
    (Path(backend.POSTERS_DIR) / name).write_bytes(b"poster")
    response = TestClient(backend.app).get(f"/api/posters/{name}")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    if extension == "svg":
        assert response.headers["Content-Security-Policy"] == "default-src 'none'; style-src 'unsafe-inline'"


def test_status_docs_and_no_cors():
    client = TestClient(backend.app)
    assert client.get("/api/status").json() == {"status": "ok", "running": 0, "queued": 0}
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
    response = client.get("/api/status", headers={"Origin": "https://other.example"})
    assert "access-control-allow-origin" not in response.headers


def test_invalid_request_returns_422():
    response = TestClient(backend.app).post("/api/generate_map_stream", json={"city": "Kyiv", "country": "Ukraine", "theme": "../terracotta"})
    assert response.status_code == 422


def test_running_stream_disconnect_cancels_worker(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def render(**kwargs):
        started.set()
        kwargs["callback"]("Starting", 1)
        assert release.wait(3)
        assert kwargs["cancel_check"]()
        raise renderer.GenerationCancelled()

    monkeypatch.setattr(renderer, "run_generator", render)

    async def scenario():
        response = await backend.generate_map_stream(req())
        job = backend.running_job
        try:
            await until(started.is_set)
            assert json.loads(await anext(response.body_iterator))["type"] == "progress"
            await response.body_iterator.aclose()
            assert job.cancel_event.is_set()
            assert backend.running_job is job
        finally:
            release.set()
            await settle()

    asyncio.run(scenario())


def test_disconnect_before_stream_iteration(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(renderer, "run_generator", lambda **kwargs: release.wait(3) or "map.png")

    async def scenario():
        response = await backend.generate_map_stream(req())
        job = backend.running_job

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                raise OSError("client disconnected")

        try:
            try:
                await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
            except Exception:
                pass
            assert job.cancel_event.is_set()
        finally:
            release.set()
            await settle()

    asyncio.run(scenario())


def test_cleanup_skips_dotfiles_and_continues_after_unreadable(monkeypatch):
    root = Path(backend.POSTERS_DIR)
    hidden = root / ".hidden.png"
    unreadable = root / "unreadable.png"
    ordinary = root / "old.png"
    for path in (hidden, unreadable, ordinary):
        path.write_bytes(b"poster")
        backend.os.utime(path, (1, 1))
    original_stat = Path.stat

    def stat(path, *args, **kwargs):
        if path == unreadable:
            raise PermissionError("unreadable")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    backend.cleanup_files()
    assert hidden.exists()
    assert not ordinary.exists()
