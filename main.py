"""
main.py
-------
FastAPI application — AI Attendance System with Face + Fingerprint.

Routes:
  GET  /                        — Serve the HTML dashboard
  POST /register/face           — Register face from image upload or known_faces/ folder
  POST /register/fingerprint    — Enroll fingerprint for an employee (triggers scanner)
  POST /check-in                — Full two-factor check (face image → fingerprint scanner)
  GET  /attendance/today        — Today's successful attendance records
  GET  /attendance/denials      — Today's denied attempts (security log)
  GET  /employees               — List all registered employees
  GET  /health                  — Health check

Run locally:
  uvicorn main:app --reload --port 8000

With Docker:
  docker-compose up --build
"""

from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import numpy as np

from database import engine, get_db, Base
from models import User
from face_engine import load_all_known_faces, recognize_from_image, KNOWN_FACES_DIR
from fingerprint import get_scanner, SCANNER_TYPE
from two_factor import run_two_factor_check
from attendance import get_today_records, get_today_denials

# Create all DB tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="AI Attendance System — Face + Fingerprint",
    description="Two-factor attendance: face recognition → fingerprint verification",
    version="2.0.0",
)

# Allow the dashboard to call the API from the browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve everything inside static/ at /static/...
app.mount("/static", StaticFiles(directory="static"), name="static")


# ─────────────────────────────────────────────
# DASHBOARD (serves index.html)
# ─────────────────────────────────────────────

@app.get("/")
def serve_dashboard():
    """Serve the HTML attendance dashboard."""
    return FileResponse("static/index.html")


# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "running",
        "fingerprint_scanner": SCANNER_TYPE,
        "known_faces_dir": str(KNOWN_FACES_DIR),
    }


# ─────────────────────────────────────────────
# REGISTER FACE (via web UI upload)
# ─────────────────────────────────────────────

@app.post("/register/face")
async def register_face(
    name: str = Form(...),
    employee_id: str = Form(...),
    department: str = Form(""),
    image: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Register a new employee's face by uploading a clear photo.
    The face encoding is extracted and saved to the database.

    Alternatively, place [employee_id].jpg in the known_faces/ folder —
    it will be picked up automatically at check-in time without this API call.
    """
    import face_recognition as fr
    import cv2

    image_bytes = await image.read()
    np_arr = np.frombuffer(image_bytes, np.uint8)
    img_bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    rgb_img = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

    face_locs = fr.face_locations(rgb_img, model="hog")
    if not face_locs:
        raise HTTPException(400, "No face detected in the uploaded image.")
    if len(face_locs) > 1:
        raise HTTPException(400, "Multiple faces detected. Upload a single-person photo.")

    encoding = fr.face_encodings(rgb_img, [face_locs[0]])[0]
    encoding_str = ",".join(map(str, encoding.tolist()))

    # Save or update in DB
    existing = db.query(User).filter(User.employee_id == employee_id).first()
    if existing:
        existing.face_encoding = encoding_str
        existing.name = name
        existing.department = department
    else:
        user = User(name=name, employee_id=employee_id,
                    department=department, face_encoding=encoding_str)
        db.add(user)

    db.commit()

    # Also save image to known_faces/ folder for folder-based recognition
    KNOWN_FACES_DIR.mkdir(parents=True, exist_ok=True)
    save_path = KNOWN_FACES_DIR / f"{employee_id}.jpg"
    cv2.imwrite(str(save_path), img_bgr)

    return {
        "success": True,
        "message": f"Face registered for {name} ({employee_id}). Image saved to known_faces/.",
    }


# ─────────────────────────────────────────────
# REGISTER FINGERPRINT (triggers physical scanner)
# ─────────────────────────────────────────────

@app.post("/register/fingerprint")
def register_fingerprint(
    employee_id: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Enroll an employee's fingerprint.
    This triggers the physical scanner — the employee must place their finger.

    For R305/R307: stores template on the sensor itself, saves slot number to DB.
    For ZKTeco: captures and stores template in the database as base64.
    For SIMULATE: marks as registered without actual scan.
    """
    user = db.query(User).filter(User.employee_id == employee_id).first()
    if not user:
        raise HTTPException(404, f"Employee {employee_id} not found. Register face first.")

    scanner = get_scanner()

    if SCANNER_TYPE == "SERIAL_R305":
        used_slots = [u.fingerprint_slot for u in db.query(User).all() if u.fingerprint_slot is not None]
        slot = 0
        while slot in used_slots:
            slot += 1

        success = scanner.enroll_finger(slot)
        if not success:
            raise HTTPException(400, "Fingerprint enrollment failed. Try again.")

        user.fingerprint_slot = slot
        db.commit()
        return {"success": True, "message": f"Fingerprint enrolled at sensor slot {slot}."}

    elif SCANNER_TYPE == "ZKTECO":
        import base64
        template = scanner.capture_template()
        scanner.close()
        if not template:
            raise HTTPException(400, "No fingerprint detected. Try again.")

        user.fingerprint_template = base64.b64encode(template).decode()
        db.commit()
        return {"success": True, "message": "ZKTeco fingerprint template saved."}

    else:
        user.fingerprint_slot = 0
        db.commit()
        return {"success": True, "message": f"Fingerprint registered (mode: {SCANNER_TYPE})."}


# ─────────────────────────────────────────────
# CHECK-IN (MAIN ENDPOINT — face image + scanner)
# ─────────────────────────────────────────────

@app.post("/check-in")
async def check_in(
    image: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Main attendance endpoint.

    1. Receive webcam image
    2. Run face recognition against known_faces/ + DB
    3. If face matches → trigger fingerprint scanner
    4. If fingerprint matches → mark attendance
    5. If EITHER fails → deny and log

    Returns full result with granted/denied, step that failed (if any),
    employee info, and confidence scores.
    """
    image_bytes = await image.read()
    result = run_two_factor_check(image_bytes=image_bytes, db=db)

    status_code = 200 if result["granted"] else 403
    return JSONResponse(content=result, status_code=status_code)


# ─────────────────────────────────────────────
# TODAY'S ATTENDANCE
# ─────────────────────────────────────────────

@app.get("/attendance/today")
def today_attendance(db: Session = Depends(get_db)):
    """Returns all successful attendance records for today."""
    return {"records": get_today_records(db)}


# ─────────────────────────────────────────────
# TODAY'S DENIAL LOG (security)
# ─────────────────────────────────────────────

@app.get("/attendance/denials")
def today_denials(db: Session = Depends(get_db)):
    """Returns all denied access attempts for today (face or fingerprint failures)."""
    return {"denials": get_today_denials(db)}


# ─────────────────────────────────────────────
# LIST REGISTERED EMPLOYEES
# ─────────────────────────────────────────────

@app.get("/employees")
def list_employees(db: Session = Depends(get_db)):
    users = db.query(User).all()
    return {
        "employees": [
            {
                "employee_id": u.employee_id,
                "name": u.name,
                "department": u.department,
                "face_registered": u.face_encoding is not None,
                "fingerprint_registered": u.fingerprint_slot is not None or u.fingerprint_template is not None,
            }
            for u in users
        ]
    }
