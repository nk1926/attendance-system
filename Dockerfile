FROM python:3.10-slim-bookworm

WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive

# Only what face_recognition + opencv absolutely need
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    g++ \
    libglib2.0-0 \
    libgl1 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libjpeg-dev \
    libpng-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip setuptools wheel

# numpy first — dlib needs it at build time
RUN pip install numpy==1.26.4

# dlib 19.24.6 works with modern CMake (19.24.2 breaks on CMake >= 3.27)
RUN pip install --no-cache-dir dlib==19.24.6

# face recognition stack
RUN pip install --no-cache-dir face-recognition==1.3.0

# everything else
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# setuptools 80+ removed pkg_resources; face_recognition_models needs it
RUN pip install "setuptools>=60,<80"

COPY . .

RUN mkdir -p known_faces

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
