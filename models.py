"""
models.py
---------
Database tables for the attendance system.

Tables:
  users         — registered employees (face encoding + fingerprint info)
  attendance    — successful attendance records
  denial_log    — failed attempts (face or fingerprint rejection)
"""

from sqlalchemy import Column, Integer, String, DateTime, Text, Enum, Float
from sqlalchemy.sql import func
import enum
from database import Base


class MethodEnum(str, enum.Enum):
    face_fingerprint = "face+fingerprint"
    login = "login"


class StatusEnum(str, enum.Enum):
    present = "present"
    late = "late"


class User(Base):
    """
    Registered employee.

    face_encoding      : 128 comma-separated floats (from face_recognition library)
    fingerprint_slot   : slot index on R305/R307 sensor (0-161)
    fingerprint_template: base64 ZKTeco template (used for ZK4500 scanner)
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    employee_id = Column(String(50), unique=True, nullable=False, index=True)
    department = Column(String(100), nullable=True)

    # Face recognition
    face_encoding = Column(Text, nullable=True)       # "0.12,0.34,..." (128 values)

    # Fingerprint — only one of these will be populated depending on scanner type
    fingerprint_slot = Column(Integer, nullable=True)         # For R305/R307 serial sensors
    fingerprint_template = Column(Text, nullable=True)        # Base64 for ZKTeco USB scanners

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AttendanceRecord(Base):
    """
    Successful two-factor attendance event.
    Only created when BOTH face AND fingerprint pass.
    """
    __tablename__ = "attendance"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(String(50), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    method = Column(String(30), default="face+fingerprint")
    status = Column(String(20), default="present")     # present / late
    face_confidence = Column(Float, nullable=True)
    fingerprint_accuracy = Column(Integer, nullable=True)


class DenialLog(Base):
    """
    Logs every failed access attempt.
    step: 'face' if rejected at face scan, 'fingerprint' if face passed but finger failed.
    """
    __tablename__ = "denial_log"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(String(50), nullable=True)    # UNKNOWN if face not recognized
    name = Column(String(100), nullable=True)
    step = Column(String(20), nullable=False)          # "face" or "fingerprint"
    reason = Column(Text, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
