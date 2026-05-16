FROM python:3.10-slim-bookworm

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive

# =========================
# System dependencies
# =========================
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    g++ \
    gcc \
    git \
    pkg-config \
    curl \
    libglib2.0-0 \
    libgl1 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libfreetype6-dev \
    libjpeg-dev \
    libpng-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# =========================
# Python setup
# =========================
RUN pip install --upgrade pip setuptools wheel

# =========================
# Install Python deps (ORDER IS IMPORTANT)
# =========================

COPY requirements.txt .

# numpy FIRST (required for dlib build)
RUN pip install numpy==1.26.4

# install cmake python wrapper (helps dlib builds sometimes)
RUN pip install cmake

# install dlib (most fragile package)
RUN pip install --no-cache-dir dlib==19.24.2

# install face recognition stack
RUN pip install --no-cache-dir face-recognition==1.3.0

# install everything else
RUN pip install --no-cache-dir -r requirements.txt

# =========================
# Copy project
# =========================
COPY . .

# =========================
# Expose FastAPI
# =========================
EXPOSE 8000

# =========================
# Run server
# =========================
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
