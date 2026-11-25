# Repository Guidelines

## Project Structure & Module Organization

AesthetiMap is a full-stack application for generating artistic map posters using OpenStreetMap data.

- **`renderer.py`**: Core rendering engine. Uses `osmnx` for data retrieval and `matplotlib` for generating posters. Supports multiple themes and high-resolution exports.
- **`backend/`**: FastAPI service that wraps `renderer.py` into a web API. Handles asynchronous generation tasks, file cleanup, and static asset serving.
- **`frontend/`**: Vite-powered single-page application. Provides a visual interface for configuring map parameters (city, theme, layout) and previewing results.
- **`themes/`**: JSON configuration files defining color palettes and styling rules for the renderer.
- **`fonts/`**: Typography resources used by the renderer for poster labels.
- **`posters/`**: Default output directory for generated images (PNG, SVG, PDF).
- **`cache/`**: Local storage for OpenStreetMap data to optimize repeated rendering requests.

## Build, Test, and Development Commands

### Full Stack (Docker)
Run the development stack (Backend on :8000, Frontend on :8080):
```bash
docker compose -f docker-compose.dev.yml up -d --build
```

### Backend & CLI
Run the renderer directly from the root:
```bash
python renderer.py --city "Kyiv" --country "Ukraine" --theme gold_on_porcelain
```

Run the FastAPI backend locally:
```bash
pip install -r requirements.txt
python backend/main.py
```

### Frontend
Develop the UI:
```bash
cd frontend
npm install
npm run dev
```

### Validation
```bash
pip install -r backend/requirements.txt -r requirements.txt -r requirements-dev.txt
flake8 --max-line-length=160
pytest -q
cd frontend && npm ci && npm run build
```

## Coding Style & Naming Conventions

### Python
- Use **FastAPI** for API endpoints.
- Core logic should reside in `renderer.py` to maintain CLI compatibility.
- Follow existing patterns for theme application and data fetching via `osmnx`.

### JavaScript
- Lightweight **Vite** setup with vanilla JS.
- Keep UI components simple and focused on interacting with the backend API.

## Commit & Pull Request Guidelines
Follow the conventional commit format observed in the repository:
- `feat:` for new features (e.g., `feat: adding shadows and topography`)
- `refactor:` for code restructuring (e.g., `refactor: rename Map Radius to Span`)
- `fix:` for bug fixes
- `docs:` for documentation updates

All text in all files have to be in English
