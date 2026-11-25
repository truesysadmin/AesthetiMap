FROM python:3.12-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libgeos-dev libgdal-dev \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY backend/requirements.txt backend/requirements.txt
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gdal-bin curl make \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1001 app \
    && useradd --uid 1001 --gid 1001 --no-create-home app \
    && mkdir -p /app/cache /app/posters \
    && chown -R 1001:1001 /app/cache /app/posters
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLCONFIGDIR=/tmp/mpl \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache
COPY renderer.py markers.py marker_shapes.json tiles.py font_management.py ./
COPY backend/ backend/
COPY themes/ themes/
COPY fonts/ fonts/
USER 1001:1001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:8000/api/status || exit 1
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
