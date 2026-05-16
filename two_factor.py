"""
two_factor.py
-------------
Orchestrates the full two-factor attendance flow:

  STEP 1 — Face Recognition
    → Image received (webcam snapshot or upload)
    → Compared against known_faces/ folder + DB
    → PASS: employee identified, move to Step 2
    → FAIL: DENIED immediately, no fingerprint prompt

  STEP 2 — Fingerprint Verification
    → Prompts scanner
    → Employee places finger
    → PASS: attendance marked as GRANTED
    → FAIL: DENIED, attendance marked as REJECTED

All results are logged to the database regardless of outcome.
"""

import time
from datetime import datetime
from sqlalchemy.orm import Session

from face_engine import recognize_from_image
from fingerprint import verify_fingerprint
from attendance import mark_attendance, log_denial


def run_two_factor_check(image_bytes: bytes, db: Session) -> dict:
    """
    Full two-factor check for a single attendance attempt.

    Args:
        image_bytes : JPEG/PNG bytes from webcam or file upload
        db          : SQLAlchemy database session

    Returns a result dict:
        {
          "granted": True/False,
          "step_failed": "face" | "fingerprint" | None,
          "employee_id": "EMP001",
          "name": "Ayesha Khan",
          "face_confidence": 0.91,
          "fingerprint_accuracy": 87,
          "reason": "...",          # present only if denied
          "timestamp": "...",
        }
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*50}")
    print(f"[TWO-FACTOR] Attendance check started at {timestamp}")

    # ════════════════════════════════════════
    # STEP 1: FACE RECOGNITION
    # ════════════════════════════════════════
    print("[STEP 1] Running face recognition...")
    face_result = recognize_from_image(image_bytes, db)

    if not face_result["granted"]:
        # Face not recognized — DENY immediately
        reason = face_result.get("reason", "Face not recognized.")
        print(f"[STEP 1] DENIED — {reason}")

        # Log denial to DB
        log_denial(
            employee_id="UNKNOWN",
            name="Unknown",
            step="face",
            reason=reason,
            db=db
        )

        return {
            "granted": False,
            "step_failed": "face",
            "reason": reason,
            "timestamp": timestamp,
        }

    employee_id = face_result["employee_id"]
    name = face_result["name"]
    face_confidence = face_result["confidence"]

    print(f"[STEP 1] PASSED — {name} ({employee_id}) | Confidence: {face_confidence:.0%}")

    # ════════════════════════════════════════
    # STEP 2: FINGERPRINT VERIFICATION
    # ════════════════════════════════════════
    print(f"[STEP 2] Face recognized. Prompting fingerprint for {name}...")
    fp_result = verify_fingerprint(employee_id=employee_id, db=db)

    if not fp_result["granted"]:
        reason = fp_result.get("reason", "Fingerprint verification failed.")
        print(f"[STEP 2] DENIED — {reason}")

        # Log failed fingerprint attempt
        log_denial(
            employee_id=employee_id,
            name=name,
            step="fingerprint",
            reason=reason,
            db=db
        )

        return {
            "granted": False,
            "step_failed": "fingerprint",
            "employee_id": employee_id,
            "name": name,
            "face_confidence": face_confidence,
            "reason": reason,
            "timestamp": timestamp,
        }

    fingerprint_accuracy = fp_result.get("accuracy", 0)
    print(f"[STEP 2] PASSED — Fingerprint match | Accuracy: {fingerprint_accuracy}")

    # ════════════════════════════════════════
    # BOTH PASSED — MARK ATTENDANCE
    # ════════════════════════════════════════
    attendance_result = mark_attendance(
        employee_id=employee_id,
        name=name,
        method="face+fingerprint",
        db=db
    )

    print(f"[GRANTED] Attendance marked for {name} — Status: {attendance_result.get('status', 'present')}")
    print(f"{'='*50}\n")

    return {
        "granted": True,
        "step_failed": None,
        "employee_id": employee_id,
        "name": name,
        "face_confidence": face_confidence,
        "fingerprint_accuracy": fingerprint_accuracy,
        "attendance_status": attendance_result.get("status", "present"),
        "simulated_fp": fp_result.get("simulated", False),
        "timestamp": timestamp,
    }
