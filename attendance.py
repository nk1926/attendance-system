"""
attendance.py
-------------
Marks successful attendance + logs denials.
"""

from datetime import datetime, date
from sqlalchemy.orm import Session
from sqlalchemy import func
from models import AttendanceRecord, DenialLog


def check_already_marked(employee_id: str, db: Session) -> bool:
    today = date.today()
    existing = db.query(AttendanceRecord).filter(
        AttendanceRecord.employee_id == employee_id,
        func.date(AttendanceRecord.timestamp) == today
    ).first()
    return existing is not None


def mark_attendance(employee_id: str, name: str, method: str, db: Session,
                    face_confidence: float = None, fingerprint_accuracy: int = None) -> dict:
    """
    Saves a successful attendance record.
    Marks 'late' if check-in is after 09:00.
    Prevents duplicate entries for the same day.
    """
    if check_already_marked(employee_id, db):
        return {
            "success": False,
            "message": f"{name} already marked present today."
        }

    now = datetime.now()
    status = "late" if now.hour >= 9 else "present"

    record = AttendanceRecord(
        employee_id=employee_id,
        name=name,
        method=method,
        status=status,
        face_confidence=face_confidence,
        fingerprint_accuracy=fingerprint_accuracy,
        timestamp=now
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return {
        "success": True,
        "status": status,
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


def log_denial(employee_id: str, name: str, step: str, reason: str, db: Session):
    """
    Logs every denied attempt — face rejection OR fingerprint rejection.
    Useful for security auditing.
    """
    log = DenialLog(
        employee_id=employee_id,
        name=name,
        step=step,
        reason=reason,
    )
    db.add(log)
    db.commit()


def get_today_records(db: Session) -> list:
    today = date.today()
    records = db.query(AttendanceRecord).filter(
        func.date(AttendanceRecord.timestamp) == today
    ).order_by(AttendanceRecord.timestamp.desc()).all()

    return [
        {
            "employee_id": r.employee_id,
            "name": r.name,
            "time": r.timestamp.strftime("%H:%M:%S"),
            "status": r.status,
            "method": r.method,
            "face_confidence": f"{r.face_confidence:.0%}" if r.face_confidence else None,
            "fingerprint_accuracy": r.fingerprint_accuracy,
        }
        for r in records
    ]


def get_today_denials(db: Session) -> list:
    today = date.today()
    logs = db.query(DenialLog).filter(
        func.date(DenialLog.timestamp) == today
    ).order_by(DenialLog.timestamp.desc()).all()

    return [
        {
            "employee_id": l.employee_id,
            "name": l.name,
            "step_failed": l.step,
            "reason": l.reason,
            "time": l.timestamp.strftime("%H:%M:%S"),
        }
        for l in logs
    ]
