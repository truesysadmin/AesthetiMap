"""Keep all test outputs away from the application's persistent directories."""
import os
import tempfile
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["CACHE_DIR"] = tempfile.mkdtemp(prefix="aesthetimap-test-cache-")

import renderer  # noqa: E402
from backend import main as backend  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    posters = tmp_path / "posters"
    cache.mkdir()
    posters.mkdir()
    monkeypatch.setattr(renderer, "CACHE_DIR", cache)
    monkeypatch.setattr(renderer, "CACHE_DIR_PATH", str(cache))
    monkeypatch.setattr(renderer, "POSTERS_DIR", str(posters))
    monkeypatch.setattr(backend, "POSTERS_DIR", posters)


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    import requests

    def blocked(*args, **kwargs):
        raise AssertionError("Tests must not make network requests")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
