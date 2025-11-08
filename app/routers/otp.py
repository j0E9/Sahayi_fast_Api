# app/routers/otp.py
from __future__ import annotations
from datetime import datetime, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import re
from fastapi import APIRouter, Request, Depends, BackgroundTasks, Body, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.settings import settings
from app.database import get_db
from app.models import User
from app.routers.auth import create_access_token

# Optional Twilio
try:
    from twilio.rest import Client as TwilioClient
    _twilio = (
        TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        if (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN)
        else None
    )
except Exception:
    _twilio = None

# Namespaced API (new paths under /otp/...)
router = APIRouter(prefix="/otp", tags=["otp"])
# Legacy API (exact old Flask paths at root, no prefix)
legacy_router = APIRouter(tags=["otp-legacy"])

# ---------------- Schemas ----------------
class EmailIn(BaseModel):
    email: EmailStr

class EmailVerifyIn(BaseModel):
    email: EmailStr
    otp: str

class PhoneIn(BaseModel):
    phone: str

class PhoneVerifyIn(BaseModel):
    phone: str
    otp: str

# ---------------- Helpers ----------------
def _now() -> datetime:
    return datetime.utcnow()

def _throttle(request: Request, key: str, seconds: int = 30):
    now = _now().timestamp()
    last = float(request.session.get(key, 0) or 0)
    if now - last < seconds:
        raise HTTPException(status_code=429, detail="Too many requests. Try again shortly.")
    request.session[key] = now

def _count_fail(request: Request, key: str, limit: int = 5):
    n = int(request.session.get(key, 0) or 0) + 1
    request.session[key] = n
    if n > limit:
        raise HTTPException(status_code=429, detail="Too many wrong attempts. Please request a new OTP.")


def _normalize_phone(phone: str) -> str:
    s = (phone or "").strip()
    digits = re.sub(r"\D", "", s)

    # +91xxxxxxxxxx
    if s.startswith("+") and digits.startswith("91") and len(digits) == 12:
        return "+91" + digits[-10:]

    # 91xxxxxxxxxx
    if digits.startswith("91") and len(digits) == 12:
        return "+91" + digits[-10:]

    # 0xxxxxxxxxx
    if digits.startswith("0") and len(digits) == 11:
        return "+91" + digits[-10:]

    # xxxxxxxxxx
    if len(digits) == 10:
        return "+91" + digits

    raise HTTPException(status_code=400, detail="Invalid Indian phone number")
def _send_email_sync(to_email: str, otp: str):
    msg = MIMEMultipart()
    msg["From"] = settings.GMAIL_USER
    msg["To"] = to_email
    msg["Subject"] = "Your Sahayi OTP Code"
    msg.attach(MIMEText(f"Your OTP code is: {otp}", "plain"))
    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login(settings.GMAIL_USER, settings.GMAIL_PASS)
    server.send_message(msg)
    server.quit()

def _schedule_email(background: BackgroundTasks, to_email: str, otp: str):
    background.add_task(_send_email_sync, to_email, otp)

def _send_sms_sync(to_phone: str, body: str):
    if not _twilio:
        print("[Twilio] not configured; skipping send")
        return None

    kwargs = {"to": to_phone, "body": body}

    # Prefer Messaging Service (recommended for India)
    if getattr(settings, "TWILIO_MESSAGING_SERVICE_SID", None):
        kwargs["messaging_service_sid"] = settings.TWILIO_MESSAGING_SERVICE_SID
    else:
        kwargs["from_"] = settings.TWILIO_PHONE

    # Optional: observe delivery lifecycle
    if getattr(settings, "TWILIO_STATUS_CALLBACK_URL", None):
        kwargs["status_callback"] = settings.TWILIO_STATUS_CALLBACK_URL

    msg = _twilio.messages.create(**kwargs)
    print(f"[Twilio] sent SID={msg.sid} to={to_phone}")
    return msg.sid


# ---------------- Email OTP ----------------
@router.post("/email/send")
def send_email_otp(data: EmailIn, request: Request, background: BackgroundTasks):
    otp = User.generate_otp()
    request.session["email"] = str(data.email)
    request.session["email_otp"] = otp
    request.session["email_otp_expiry"] = (_now() + timedelta(minutes=5)).isoformat()
    try:
        _schedule_email(background, str(data.email), otp)
        return {"success": True, "message": f"📩 Email OTP sent to {data.email}"}
    except Exception:
        return {"success": False, "message": "❌ Error sending email OTP"}

@router.post("/email/verify")
def verify_email_otp(data: EmailVerifyIn, request: Request):
    if (
        request.session.get("email") == str(data.email)
        and request.session.get("email_otp") == (data.otp or "").strip()
    ):
        expiry = request.session.get("email_otp_expiry")
        if not expiry or _now() > datetime.fromisoformat(expiry):
            return {"success": False, "message": "❌ OTP expired"}
        request.session["email_verified"] = True
        request.session.pop("email_otp", None)
        request.session.pop("email_otp_expiry", None)
        return {"success": True, "message": "✅ Email verified successfully"}
    return {"success": False, "message": "❌ Invalid or expired Email OTP"}

# ---------------- Phone OTP (sign-up) ----------------
@router.post("/phone/send")
def send_phone_otp(data: PhoneIn, request: Request, db: Session = Depends(get_db)):
    phone = _normalize_phone(data.phone)
    exists = db.query(User).filter(or_(User.phone == phone, User.phone == phone.replace("+91", ""))).first()
    if exists:
        return {"success": False, "message": "⚠️ Phone already registered, please login instead."}

    otp = User.generate_otp()
    request.session["signup_phone"] = phone
    request.session["signup_phone_otp"] = otp
    request.session["signup_phone_otp_expiry"] = (_now() + timedelta(minutes=5)).isoformat()
    try:
        _send_sms_sync(phone, f"Your Sign-Up OTP code is: {otp}")
        return {"success": True, "message": f"📩 OTP sent to {phone}"}
    except Exception:
        return {"success": False, "message": "❌ Error sending OTP"}

@router.post("/phone/verify")
def verify_phone_otp(data: PhoneVerifyIn, request: Request):
    phone = _normalize_phone(data.phone)
    otp = (data.otp or "").strip()
    if request.session.get("signup_phone") == phone and request.session.get("signup_phone_otp") == otp:
        expiry = request.session.get("signup_phone_otp_expiry")
        if not expiry or _now() > datetime.fromisoformat(expiry):
            return {"success": False, "message": "❌ OTP expired"}
        request.session["phone_verified"] = True
        request.session.pop("signup_phone_otp", None)
        request.session.pop("signup_phone_otp_expiry", None)
        return {"success": True, "message": "✅ Phone verified successfully"}
    return {"success": False, "message": "❌ Invalid Phone OTP"}

# ---------------- Phone OTP (login) ----------------
@router.post("/phone/login/send")
def send_phone_otp_login(data: PhoneIn, request: Request, db: Session = Depends(get_db)):
    phone = _normalize_phone(data.phone)
    user = db.query(User).filter(or_(User.phone == phone, User.phone == phone.replace("+91", ""))).first()
    if not user:
        return {"success": False, "message": "❌ No account found with this phone"}

    otp = User.generate_otp()
    user.phone_otp = otp
    user.phone_otp_expiry = _now() + timedelta(minutes=5)
    db.add(user); db.commit()

    request.session["login_phone"] = phone
    request.session["login_phone_otp"] = otp
    request.session["login_phone_otp_expiry"] = user.phone_otp_expiry.isoformat()
    try:
        _send_sms_sync(phone, f"Your Login OTP code is: {otp}")
        return {"success": True, "message": f"📩 Login OTP sent to {phone}"}
    except Exception:
        return {"success": False, "message": "❌ Error sending OTP"}

@router.post("/phone/login/verify")
def verify_phone_otp_login(data: PhoneVerifyIn, request: Request, db: Session = Depends(get_db)):
    phone = _normalize_phone(data.phone)
    otp = (data.otp or "").strip()

    if request.session.get("login_phone") != phone or request.session.get("login_phone_otp") != otp:
        return {"success": False, "message": "❌ Invalid OTP"}

    expiry = request.session.get("login_phone_otp_expiry")
    if not expiry or _now() > datetime.fromisoformat(expiry):
        return {"success": False, "message": "❌ OTP expired"}

    user = db.query(User).filter(or_(User.phone == phone, User.phone == phone.replace("+91", ""))).first()
    if not user:
        return {"success": False, "message": "❌ No account found with this phone"}

    request.session["user_id"] = user.id  # Trust API session login
    token = create_access_token({"sub": str(user.id)})

    # cleanup
    for k in ("login_phone", "login_phone_otp", "login_phone_otp_expiry"):
        request.session.pop(k, None)

    return {"success": True, "message": "✅ OTP verified, logged in!", "access_token": token, "token_type": "bearer"}

# -------- Legacy (no prefix) exact Flask paths --------
@legacy_router.post("/send_phone_otp", include_in_schema=False)
def legacy_send_phone_otp(payload: PhoneIn = Body(...), request: Request = None, db: Session = Depends(get_db)):
    return send_phone_otp(payload, request, db)

@legacy_router.post("/verify_phone_otp", include_in_schema=False)
def legacy_verify_phone_otp(payload: PhoneVerifyIn = Body(...), request: Request = None):
    return verify_phone_otp(payload, request)

@legacy_router.post("/send_phone_otp_login", include_in_schema=False)
def legacy_send_phone_otp_login(payload: PhoneIn = Body(...), request: Request = None, db: Session = Depends(get_db)):
    return send_phone_otp_login(payload, request, db)

@legacy_router.post("/verify_phone_otp_login", include_in_schema=False)
def legacy_verify_phone_otp_login(payload: PhoneVerifyIn = Body(...), request: Request = None, db: Session = Depends(get_db)):
    return verify_phone_otp_login(payload, request, db)

@legacy_router.post("/send_email_otp", include_in_schema=False)
def legacy_send_email_otp(payload: EmailIn = Body(...), request: Request = None, background: BackgroundTasks = None):
    return send_email_otp(payload, request, background)

@legacy_router.post("/verify_email_otp", include_in_schema=False)
def legacy_verify_email_otp(payload: EmailVerifyIn = Body(...), request: Request = None):
    return verify_email_otp(payload, request)
