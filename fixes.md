# Docker Build & Runtime Fixes

## Summary

Five issues were identified and fixed to get the Docker stack (`attendance_app` + `attendance_mysql`) building cleanly and running without errors.

---

## Fix 1 — `face_recognition_models` crash on startup

**Symptom:** App container kept restarting with exit code 0. Logs showed:

```
Please install `face_recognition_models` with this command before using `face_recognition`:

pip install git+https://github.com/ageitgey/face_recognition_models
```

**Root cause:** The Dockerfile ran `pip install --upgrade setuptools`, which installed `setuptools==82.0.1`. This version removed `pkg_resources` as an importable module. The `face_recognition_models` package uses `from pkg_resources import resource_filename` in its `__init__.py`, which caused an `ImportError` at startup. The `face_recognition` library catches any exception on that import and calls `quit()`, causing a clean exit with code 0 — hence the restart loop with no traceback.

**Fix (`Dockerfile`):** Added a late step that downgrades setuptools to a version that still ships `pkg_resources`, placed *after* the expensive dlib/face-recognition compile steps to preserve Docker layer cache:

```dockerfile
# setuptools 80+ removed pkg_resources; face_recognition_models needs it
RUN pip install "setuptools>=60,<80"
```

---

## Fix 2 — MySQL startup race condition

**Symptom:** On a fresh `docker compose up`, the app would fail to connect to MySQL because MySQL wasn't ready to accept connections yet, even though the container had started.

**Root cause:** `depends_on: mysql` only waits for the MySQL *container* to start, not for the MySQL *server* inside it to be ready. MySQL takes 10–30 seconds to initialise its data directory on first boot.

**Fix (`docker-compose.yml`):** Added a healthcheck to the MySQL service and changed `depends_on` to use `condition: service_healthy`, so the app container only starts after MySQL is confirmed ready:

```yaml
mysql:
  healthcheck:
    test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-u", "admin", "-psecret"]
    interval: 5s
    timeout: 5s
    retries: 10
    start_period: 30s

app:
  depends_on:
    mysql:
      condition: service_healthy
```

---

## Fix 3 — No database connection retry

**Symptom:** Even with the healthcheck in place, `Base.metadata.create_all(bind=engine)` in `main.py` would throw an unhandled `OperationalError` if MySQL wasn't immediately reachable.

**Root cause:** `database.py` created the SQLAlchemy engine at import time with no retry logic. The first actual connection attempt (table creation at startup) had no fallback.

**Fix (`database.py`):** Added `wait_for_db()` — a retry loop that polls `SELECT 1` up to 20 times with 3-second delays before giving up. Also made the connection string read from environment variables instead of being hardcoded, and added `pool_pre_ping=True` to the engine:

```python
DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:3306/{DB_NAME}"
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

def wait_for_db(retries: int = 20, delay: int = 3) -> None:
    for attempt in range(1, retries + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:
            if attempt == retries:
                raise RuntimeError("Could not connect to database.") from exc
            time.sleep(delay)
```

**Fix (`main.py`):** Called `wait_for_db()` before `Base.metadata.create_all()`:

```python
wait_for_db()
Base.metadata.create_all(bind=engine)
```

---

## Fix 4 — `known_faces/` directory missing from image

**Symptom:** Every request to `/check-in` printed a warning:

```
[WARN] known_faces/ directory not found at known_faces
```

**Root cause:** The `known_faces/` directory exists locally but was not created in the Docker image. The code in `face_engine.py` handled the missing directory gracefully (printing a warning and returning empty) but it was noisy.

**Fix (`Dockerfile`):** Added a `mkdir` step after `COPY . .`:

```dockerfile
COPY . .
RUN mkdir -p known_faces
```

---

## Fix 5 — `.dockerignore` missing

**Symptom:** Build context included `__pycache__/`, `.git/`, build logs, and unused files, making the context transfer slow and polluting the image.

**Fix (`.dockerignore`):** Created `.dockerignore` to exclude non-essential files:

```
__pycache__/
*.pyc
*.pyo
.git/
build.log
uvicorn.log
packages.microsoft.gpg
apache.conf
```

---

## Final Dockerfile

```dockerfile
FROM python:3.10-slim-bookworm

WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake g++ \
    libglib2.0-0 libgl1 libsm6 libxext6 libxrender-dev \
    libjpeg-dev libpng-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip setuptools wheel
RUN pip install numpy==1.26.4
RUN pip install --no-cache-dir dlib==19.24.6
RUN pip install --no-cache-dir face-recognition==1.3.0

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Must come after all installs; setuptools 80+ drops pkg_resources
RUN pip install "setuptools>=60,<80"

COPY . .
RUN mkdir -p known_faces

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Verified Working State

```
NAME               STATUS                    PORTS
attendance_app     Up                        0.0.0.0:8000->8000/tcp
attendance_mysql   Up (healthy)              0.0.0.0:3307->3306/tcp
```

App logs (clean):
```
INFO:     Started server process [1]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

Endpoints confirmed responding:
- `GET /health` → 200
- `GET /employees` → 200
- `GET /attendance/today` → 200
