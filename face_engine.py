"""
face_engine.py
--------------
Handles ALL face recognition logic.

Two sources of known faces:
  1. known_faces/ folder  — images named EMP001.jpg, EMP002.jpg etc.
  2. Database             — faces registered via web UI (stored as base64 or file)

Flow:
  capture_and_recognize() → scans webcam OR receives image bytes
      ↓
  compare against known encodings
      ↓
  MATCH  → returns employee info
  NO MATCH → returns DENIED immediately

IMPORTANT:
  - Face must match with confidence > 0.55 (strict)
  - Multiple faces in frame → DENIED (security)
  - No face detected → DENIED
"""

import cv2
import face_recognition
import numpy as np
import os
from pathlib import Path
from sqlalchemy.orm import Session
from models import User

# ─────────────────────────────────────────────
# KNOWN FACES FOLDER
# ─────────────────────────────────────────────
KNOWN_FACES_DIR = Path(os.getenv("KNOWN_FACES_DIR", "./known_faces"))
TOLERANCE = 0.50   # Strictness: lower = stricter. 0.5 is high security.


def load_known_faces_from_folder() -> tuple[list, list]:
    """
    Scans the known_faces/ directory.
    Each image file should be named:  EMPLOYEE_ID.jpg  (e.g. EMP001.jpg)

    Returns:
        known_encodings : list of 128-d numpy arrays
        known_ids       : list of employee_id strings (filenames without extension)

    Supported formats: .jpg .jpeg .png .bmp
    """
    known_encodings = []
    known_ids = []

    if not KNOWN_FACES_DIR.exists():
        print(f"[WARN] known_faces/ directory not found at {KNOWN_FACES_DIR}")
        return known_encodings, known_ids

    supported = {".jpg", ".jpeg", ".png", ".bmp"}

    for img_path in KNOWN_FACES_DIR.iterdir():
        if img_path.suffix.lower() not in supported:
            continue

        employee_id = img_path.stem  # filename without extension = employee ID

        # Load image → RGB (face_recognition needs RGB, OpenCV loads BGR)
        img = face_recognition.load_image_file(str(img_path))

        # Find face locations in the image
        face_locs = face_recognition.face_locations(img, model="hog")

        if not face_locs:
            print(f"[WARN] No face found in {img_path.name} — skipping")
            continue

        if len(face_locs) > 1:
            print(f"[WARN] Multiple faces in {img_path.name} — using first face only")

        # Encode first (and ideally only) face
        encoding = face_recognition.face_encodings(img, [face_locs[0]])[0]
        known_encodings.append(encoding)
        known_ids.append(employee_id)
        print(f"[OK] Loaded face for: {employee_id}")

    print(f"[INFO] Loaded {len(known_encodings)} face(s) from known_faces/ folder")
    return known_encodings, known_ids


def load_known_faces_from_db(db: Session) -> tuple[list, list]:
    """
    Loads face encodings stored in the database (registered via web UI).

    Returns:
        known_encodings : list of 128-d numpy arrays
        known_ids       : list of employee_id strings
    """
    users = db.query(User).filter(User.face_encoding.isnot(None)).all()
    known_encodings = []
    known_ids = []

    for user in users:
        vec = np.array([float(x) for x in user.face_encoding.split(",")])
        known_encodings.append(vec)
        known_ids.append(user.employee_id)

    return known_encodings, known_ids


def load_all_known_faces(db: Session) -> tuple[list, list, dict]:
    """
    Merges known faces from BOTH folder and database.
    Folder takes priority (overwrites DB entry if same employee_id).

    Returns:
        all_encodings : merged list of 128-d arrays
        all_ids       : merged list of employee_ids
        id_to_name    : dict mapping employee_id → name (from DB if available)
    """
    folder_enc, folder_ids = load_known_faces_from_folder()
    db_enc, db_ids = load_known_faces_from_db(db)

    # Build ID→name map from DB
    users = db.query(User).all()
    id_to_name = {u.employee_id: u.name for u in users}

    # Merge — folder overrides DB for same ID
    merged_enc = list(db_enc)
    merged_ids = list(db_ids)

    for enc, eid in zip(folder_enc, folder_ids):
        if eid in merged_ids:
            idx = merged_ids.index(eid)
            merged_enc[idx] = enc   # folder version wins
        else:
            merged_enc.append(enc)
            merged_ids.append(eid)

    return merged_enc, merged_ids, id_to_name


# ─────────────────────────────────────────────
# RECOGNIZE FROM IMAGE BYTES (API call)
# ─────────────────────────────────────────────

def recognize_from_image(image_bytes: bytes, db: Session) -> dict:
    """
    Takes raw image bytes (from webcam snapshot or uploaded file).
    Compares against all known faces.

    Returns:
        {
          "granted": True/False,
          "employee_id": "EMP001",   # only if granted
          "name": "Ayesha Khan",     # only if granted
          "confidence": 0.91,        # only if granted
          "reason": "..."            # only if denied
        }
    """
    # Load all known faces
    all_enc, all_ids, id_to_name = load_all_known_faces(db)

    if not all_enc:
        return {"granted": False, "reason": "No registered faces in the system."}

    # Decode image bytes → numpy array
    np_arr = np.frombuffer(image_bytes, np.uint8)
    img_bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if img_bgr is None:
        return {"granted": False, "reason": "Invalid image data."}

    rgb_img = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

    # Detect faces
    face_locations = face_recognition.face_locations(rgb_img, model="hog")

    if not face_locations:
        return {"granted": False, "reason": "No face detected in image."}

    if len(face_locations) > 1:
        return {"granted": False, "reason": "Multiple faces detected. Only one person at a time."}

    # Encode detected face
    face_encodings = face_recognition.face_encodings(rgb_img, face_locations)
    if not face_encodings:
        return {"granted": False, "reason": "Could not encode face."}

    live_encoding = face_encodings[0]

    # Compare against known faces
    distances = face_recognition.face_distance(all_enc, live_encoding)
    best_idx = int(np.argmin(distances))
    best_distance = distances[best_idx]
    confidence = round(1.0 - best_distance, 3)

    if best_distance > TOLERANCE:
        return {
            "granted": False,
            "reason": f"Face not recognized. Closest match confidence: {confidence:.0%} (required ≥ {1-TOLERANCE:.0%})"
        }

    employee_id = all_ids[best_idx]
    name = id_to_name.get(employee_id, employee_id)

    return {
        "granted": True,
        "employee_id": employee_id,
        "name": name,
        "confidence": confidence,
    }


# ─────────────────────────────────────────────
# LIVE WEBCAM SCAN (standalone / CLI use)
# ─────────────────────────────────────────────

def capture_frame_from_webcam(camera_index: int = 0) -> bytes:
    """
    Opens webcam, captures one frame, returns it as JPEG bytes.
    Used by the two_factor.py module to grab a frame for recognition.
    """
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        raise RuntimeError("Cannot open webcam. Is it connected?")

    ret, frame = cap.read()
    cap.release()

    if not ret:
        raise RuntimeError("Failed to capture frame from webcam.")

    _, buffer = cv2.imencode(".jpg", frame)
    return buffer.tobytes()


def run_live_display(db: Session, camera_index: int = 0):
    """
    Opens a live webcam window.
    Draws GREEN box + name for recognized faces.
    Draws RED box + DENIED for unknown faces.

    Press Q to quit.
    This is for standalone demo / kiosk mode.
    """
    all_enc, all_ids, id_to_name = load_all_known_faces(db)
    cap = cv2.VideoCapture(camera_index)
    print("[INFO] Live recognition started. Press Q to quit.")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Shrink for speed, process, scale coords back
        small = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
        rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

        face_locs = face_recognition.face_locations(rgb_small)
        face_encs = face_recognition.face_encodings(rgb_small, face_locs)

        for (top, right, bottom, left), enc in zip(face_locs, face_encs):
            top, right, bottom, left = top*4, right*4, bottom*4, left*4

            distances = face_recognition.face_distance(all_enc, enc) if all_enc else []
            recognized = False
            label = "DENIED"
            color = (0, 0, 220)   # red for denied

            if len(distances) > 0:
                best_idx = int(np.argmin(distances))
                if distances[best_idx] <= TOLERANCE:
                    eid = all_ids[best_idx]
                    name = id_to_name.get(eid, eid)
                    conf = round((1 - distances[best_idx]) * 100)
                    label = f"{name} ({conf}%)"
                    color = (0, 200, 50)   # green for granted
                    recognized = True

            # Draw bounding box
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            cv2.rectangle(frame, (left, bottom - 38), (right, bottom), color, cv2.FILLED)
            cv2.putText(frame, label, (left + 6, bottom - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 1)

        cv2.imshow("AI Attendance — Face Scanner (Q to quit)", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
