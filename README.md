# AI Attendance System — Face + Fingerprint

## How it works
1. Person stands in front of webcam
2. System scans face → matches against known_faces/ images
3. If face recognized → prompts for fingerprint scan
4. If fingerprint matches → GRANTED, attendance marked
5. If face OR fingerprint fails → DENIED

## Supported Fingerprint Scanners
- ZKTeco ZK4500 (recommended, ~$30)
- DigitalPersona U.are.U 4500
- Any PyFingerprint-compatible sensor (R305/R307 via serial)
- Any libfprint-compatible USB scanner (Linux)

## Quick Start
```bash
# Install system deps (Linux)
sudo apt-get install libfprint-2-dev fprintd

# Start full system
docker-compose up --build

# API docs
http://localhost:8000/docs
```

## Folder Structure
```
attendance-system-v2/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── known_faces/          ← Put face images here (filename = employee_id.jpg)
│   ├── EMP001.jpg
│   ├── EMP002.jpg
│   └── ...
└── app/
    ├── main.py           ← FastAPI entry point
    ├── database.py       ← PostgreSQL connection
    ├── models.py         ← DB tables
    ├── face_engine.py    ← Face scan + match
    ├── fingerprint.py    ← Hardware fingerprint integration
    ├── two_factor.py     ← Face → Fingerprint flow
    ├── attendance.py     ← Mark + query attendance
    └── reports.py        ← Daily/CSV reports
```
