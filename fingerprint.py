"""
fingerprint.py
--------------
Real fingerprint scanner integration.

Supports THREE scanner types — auto-detected:

  TYPE A: ZKTeco ZK4500 / DigitalPersona U.are.U 4500
          → Uses libfprint (Linux) or pyzkfp (Windows)
          → Connected via USB

  TYPE B: R305 / R307 Optical Sensor
          → Connected via USB-to-Serial (COM port / /dev/ttyUSB0)
          → Uses pyfingerprint library

  TYPE C: Windows-only ZK SDK
          → Uses pyzkfp (ZKTeco official Python SDK)

HOW FINGERPRINT MATCHING WORKS:
  1. On registration: scanner captures fingerprint → sensor returns a template
     (a compact binary representation of the fingerprint's minutiae points)
  2. On verification: scanner captures live print → compares to stored template
  3. Match score returned (0–100). We require ≥ 75 to grant access.

WIRING for R305/R307 (serial):
  Scanner TX → USB adapter RX
  Scanner RX → USB adapter TX
  Scanner VCC → 3.3V or 5V
  Scanner GND → GND

Install dependencies:
  pip install pyfingerprint        # for R305/R307 serial sensors
  pip install pyzkfp               # for ZKTeco USB scanners (Windows)
  sudo apt install libfprint-2-dev # for DigitalPersona (Linux)
"""

import os
import time
import base64
from enum import Enum
from sqlalchemy.orm import Session
from models import User

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

# Set FINGERPRINT_TYPE in environment or .env file
# Options: "ZKTECO" | "SERIAL_R305" | "LIBFPRINT" | "SIMULATE"
SCANNER_TYPE = os.getenv("FINGERPRINT_TYPE", "SIMULATE")

# For serial scanners (R305/R307):
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")   # Linux: /dev/ttyUSB0, Windows: COM3
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "57600"))

MATCH_THRESHOLD = int(os.getenv("FP_THRESHOLD", "75"))    # 0-100, higher = stricter


class FingerprintResult(Enum):
    GRANTED = "granted"
    DENIED = "denied"
    ERROR = "error"
    TIMEOUT = "timeout"


# ─────────────────────────────────────────────
# TYPE A: ZKTeco USB Scanner (pyzkfp / Windows)
# ─────────────────────────────────────────────

class ZKTecoScanner:
    """
    Wraps the ZKTeco SDK for ZK4500 and similar USB fingerprint scanners.
    Requires: pip install pyzkfp
    Works on Windows. For Linux, use LibFPrint instead.
    """

    def __init__(self):
        try:
            from pyzkfp import ZKFP2
            self.sdk = ZKFP2()
            self.sdk.Init()
            count = self.sdk.GetDeviceCount()
            if count == 0:
                raise RuntimeError("No ZKTeco scanner found. Is it plugged in?")
            self.sdk.OpenDevice(0)
            print(f"[ZKTeco] Scanner ready. Devices found: {count}")
        except ImportError:
            raise ImportError("pyzkfp not installed. Run: pip install pyzkfp")

    def capture_template(self, timeout_seconds: int = 15) -> bytes | None:
        """
        Waits for a finger to be placed, captures fingerprint template.
        Returns template as bytes, or None on timeout/failure.
        """
        print("[ZKTeco] Place finger on scanner...")
        start = time.time()

        while time.time() - start < timeout_seconds:
            tmp, img = self.sdk.AcquireFingerprint()
            if tmp:
                print("[ZKTeco] Fingerprint captured.")
                return tmp   # raw template bytes
            time.sleep(0.1)

        print("[ZKTeco] Timeout — no finger detected.")
        return None

    def match(self, template1: bytes, template2: bytes) -> int:
        """
        Compares two templates.
        Returns match score (0-100). Score ≥ MATCH_THRESHOLD = match.
        """
        score = self.sdk.DBMatch(template1, template2)
        return score

    def close(self):
        self.sdk.CloseDevice()
        self.sdk.Terminate()


# ─────────────────────────────────────────────
# TYPE B: R305/R307 Serial Sensor (pyfingerprint)
# ─────────────────────────────────────────────

class SerialFingerprintScanner:
    """
    Wraps pyfingerprint for R305/R307 optical sensors connected via serial.
    These sensors store templates ON the sensor itself (up to 162 templates on R305).

    We use the sensor's built-in "search" to match against stored templates.

    Requires: pip install pyfingerprint
    """

    def __init__(self):
        try:
            from pyfingerprint.pyfingerprint import PyFingerprint
            self.fp = PyFingerprint(SERIAL_PORT, SERIAL_BAUD, 0xFFFFFFFF, 0x00000000)
            if not self.fp.verifyPassword():
                raise RuntimeError("Fingerprint sensor password verification failed.")
            print(f"[R305] Connected on {SERIAL_PORT}. Templates stored: {self.fp.getTemplateCount()}")
        except ImportError:
            raise ImportError("pyfingerprint not installed. Run: pip install pyfingerprint")

    def enroll_finger(self, position: int) -> bool:
        """
        Enrolls a new fingerprint at a given position (0-indexed slot on sensor).
        Prompts user to place finger TWICE for accuracy.
        Returns True on success.
        """
        print(f"[R305] Enrolling fingerprint at position {position}...")

        # First scan
        print("[R305] Place finger on scanner (scan 1)...")
        while not self.fp.readImage():
            pass
        self.fp.convertImage(0x01)

        print("[R305] Remove finger, then place again (scan 2)...")
        time.sleep(2)
        while not self.fp.readImage():
            pass
        self.fp.convertImage(0x02)

        # Compare the two scans
        if self.fp.compareCharacteristics() == 0:
            print("[R305] Fingerprints do not match during enrollment.")
            return False

        # Create template and store at position
        self.fp.createTemplate()
        self.fp.storeTemplate(position, 0x01)
        print(f"[R305] Fingerprint enrolled at position {position}.")
        return True

    def search_finger(self, timeout_seconds: int = 15) -> dict:
        """
        Waits for a finger, then searches all stored templates for a match.

        Returns:
          { "found": True, "position": 3, "accuracy": 87 }
          { "found": False, "reason": "..." }
        """
        print("[R305] Place finger on scanner for verification...")
        start = time.time()

        while time.time() - start < timeout_seconds:
            if self.fp.readImage():
                self.fp.convertImage(0x01)
                result = self.fp.searchTemplate()
                position, accuracy = result

                if position == -1:
                    return {"found": False, "reason": "No matching fingerprint found."}

                if accuracy < MATCH_THRESHOLD:
                    return {"found": False, "reason": f"Match score {accuracy} below threshold {MATCH_THRESHOLD}."}

                return {"found": True, "position": position, "accuracy": accuracy}
            time.sleep(0.1)

        return {"found": False, "reason": "Timeout — no finger detected."}

    def get_template_count(self) -> int:
        return self.fp.getTemplateCount()


# ─────────────────────────────────────────────
# TYPE C: libfprint (Linux — DigitalPersona etc.)
# ─────────────────────────────────────────────

class LibFPrintScanner:
    """
    Uses fprintd (Linux daemon) via subprocess / D-Bus for
    DigitalPersona U.are.U and other libfprint-compatible scanners.

    Requires:
      sudo apt install fprintd libfprint-2-dev
      fprintd-enroll  (to register fingerprints on first use)

    This class calls fprintd-verify as a subprocess for simplicity.
    For production, use python-dbus to talk to fprintd directly.
    """

    def verify_fingerprint(self, username: str, timeout_seconds: int = 15) -> dict:
        """
        Calls fprintd-verify for the given system username.
        Returns success/failure.

        NOTE: The system user must have their fingerprint enrolled via:
              fprintd-enroll <username>
        """
        import subprocess
        print(f"[libfprint] Verifying fingerprint for: {username}")

        try:
            result = subprocess.run(
                ["fprintd-verify", username],
                timeout=timeout_seconds,
                capture_output=True,
                text=True
            )
            output = result.stdout + result.stderr

            if "verify-match" in output or result.returncode == 0:
                return {"found": True, "accuracy": 100}
            else:
                return {"found": False, "reason": "Fingerprint verification failed."}

        except subprocess.TimeoutExpired:
            return {"found": False, "reason": "Timeout waiting for fingerprint."}
        except FileNotFoundError:
            return {"found": False, "reason": "fprintd not installed. Run: sudo apt install fprintd"}


# ─────────────────────────────────────────────
# SIMULATE MODE (no hardware — for testing)
# ─────────────────────────────────────────────

class SimulatedScanner:
    """
    Simulates a fingerprint scanner for development/testing.
    Always returns success after a 2-second "scan" delay.
    Replace with a real scanner class in production.
    """

    def verify(self, employee_id: str) -> dict:
        print(f"[SIM] Simulating fingerprint scan for {employee_id}...")
        time.sleep(2)
        print("[SIM] Simulated fingerprint MATCH.")
        return {"found": True, "accuracy": 95, "simulated": True}


# ─────────────────────────────────────────────
# UNIFIED INTERFACE — used by two_factor.py
# ─────────────────────────────────────────────

def get_scanner():
    """
    Returns the appropriate scanner instance based on FINGERPRINT_TYPE env var.
    Set FINGERPRINT_TYPE in your .env or docker-compose.yml.
    """
    if SCANNER_TYPE == "ZKTECO":
        return ZKTecoScanner()
    elif SCANNER_TYPE == "SERIAL_R305":
        return SerialFingerprintScanner()
    elif SCANNER_TYPE == "LIBFPRINT":
        return LibFPrintScanner()
    else:
        print(f"[WARN] FINGERPRINT_TYPE='{SCANNER_TYPE}' — using SIMULATE mode.")
        return SimulatedScanner()


def verify_fingerprint(employee_id: str, db: Session) -> dict:
    """
    Main entry point called by two_factor.py after face recognition passes.

    Looks up what fingerprint slot the employee is registered at (from DB),
    then triggers the scanner to verify.

    Returns:
      { "granted": True, "accuracy": 87 }
      { "granted": False, "reason": "..." }
    """
    # Look up employee fingerprint registration
    user = db.query(User).filter(User.employee_id == employee_id).first()

    if not user:
        return {"granted": False, "reason": "Employee not found in database."}

    if not user.fingerprint_slot and SCANNER_TYPE != "SIMULATE":
        return {"granted": False, "reason": "No fingerprint registered for this employee."}

    scanner = get_scanner()

    # ── ZKTeco: template stored in DB, match on device ──
    if SCANNER_TYPE == "ZKTECO":
        live_template = scanner.capture_template()
        if not live_template:
            return {"granted": False, "reason": "No fingerprint detected. Timeout."}

        stored_template = base64.b64decode(user.fingerprint_template)
        score = scanner.match(live_template, stored_template)
        scanner.close()

        if score >= MATCH_THRESHOLD:
            return {"granted": True, "accuracy": score}
        return {"granted": False, "reason": f"Fingerprint mismatch. Score: {score}/{MATCH_THRESHOLD}"}

    # ── R305/R307: template stored ON sensor, search by slot ──
    elif SCANNER_TYPE == "SERIAL_R305":
        result = scanner.search_finger()
        if result["found"] and result.get("position") == user.fingerprint_slot:
            return {"granted": True, "accuracy": result["accuracy"]}
        return {"granted": False, "reason": result.get("reason", "Fingerprint mismatch.")}

    # ── libfprint: verify against system enrollment ──
    elif SCANNER_TYPE == "LIBFPRINT":
        result = scanner.verify_fingerprint(username=employee_id)
        if result["found"]:
            return {"granted": True, "accuracy": 100}
        return {"granted": False, "reason": result["reason"]}

    # ── Simulated ──
    else:
        result = scanner.verify(employee_id)
        return {"granted": True, "accuracy": result["accuracy"], "simulated": True}
