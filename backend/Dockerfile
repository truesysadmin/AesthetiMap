# ----- Stage 1: Build dependencies -----
FROM python:3.12-slim AS builder

WORKDIR /app
RUN apt-get update && apt-get install -y \
    build-essential \
    libgeos-dev \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements first to cache them
COPY backend/requirements.txt /app/backend_requirements.txt
COPY requirements.txt /app/requirements.txt

# Install as user to easily copy later
RUN pip install --no-cache-dir --user -r /app/backend_requirements.txt -r /app/requirements.txt

# ----- Stage 2: Slim runtime -----
FROM python:3.12-slim

WORKDIR /app

# Only install runtime binaries (elevation requires gdal-bin and curl)
RUN apt-get update && apt-get install -y \
    gdal-bin \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy python packages from builder
COPY --from=builder /root/.local /root/.local

# Ensure path includes user installed bins
ENV PATH=/root/.local/bin:$PATH

# Clean up pycache to save more space
RUN find /root/.local -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# Copy project files
COPY . .

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
