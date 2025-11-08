# app/routers/realtime_jobs.py
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import (
    User, Booking, Notification, Message, WorkerProfile
)
# If you use Twilio in this file, import your client/TWILIO_PHONE as needed.
# from app.twilio import client, TWILIO_PHONE

router = APIRouter(tags=["realtime"])

# ---------- auth (Trust API style) ----------
def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = db.get(User, int(uid))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user

# ---------- helpers ----------
def generate_otp() -> str:
    return str(random.randint(100000, 999999))

def chat_url_for(booking_id: int) -> str:
    return f"/chat/{booking_id}"

def get_active_booking(db: Session, user_id: int) -> Optional[Booking]:
    return (
        db.query(Booking)
        .filter(
            Booking.status == "Token Paid",
            ((Booking.worker_id == user_id) | (Booking.provider_id == user_id))
        )
        .order_by(Booking.id.desc())
        .first()
    )

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # meters
    R = 6371000.0
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# ---------- Pydantic bodies ----------
class LatLonIn(BaseModel):
    latitude: float
    longitude: float

class OTPIn(BaseModel):
    otp: str

class ExtraConfirmOut(BaseModel):
    success: bool
    redirect_url: Optional[str] = None

class SendMessageIn(BaseModel):
    message: str
    booking_id: int

class UpdateQuantityIn(BaseModel):
    completed_quantity: int = Field(ge=0)

# ---------- routes ----------
@router.post("/verify_worker_location/{booking_id}")
def verify_worker_location(
    booking_id: int,
    payload: LatLonIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.get(Booking, booking_id) or HTTPException(404)
    if current_user.id != booking.worker_id:
        raise HTTPException(403, "Unauthorized")

    if not (booking.provider and booking.provider.latitude and booking.provider.longitude):
        return {"status": "error", "message": "Provider location not available"}

    distance = haversine(payload.latitude, payload.longitude, booking.provider.latitude, booking.provider.longitude)
    if distance <= 50:
        booking.worker_arrived = True
        db.commit()
        return {"status": "success", "message": "Worker reached location", "allow_otp": True}
    return {"status": "error", "message": "You are not within 50m radius", "allow_otp": False}

@router.post("/request_extra_time")
def request_extra_time(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = (
        db.query(Booking)
        .filter(Booking.status == "Token Paid", Booking.worker_id == current_user.id)
        .order_by(Booking.id.desc())
        .first()
    )
    if not booking:
        return {"success": False, "message": "No active booking."}
    if booking.extra_timer_requested:
        return {"success": False, "message": "Already requested."}

    booking.extra_timer_requested = True
    booking.extra_timer_requested_at = datetime.utcnow()
    booking.main_timer_paused = True
    db.commit()
    return {"success": True}

@router.post("/verify_extra_timer_otp")
def verify_extra_timer_otp(
    payload: OTPIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = (
        db.query(Booking)
        .filter(Booking.status == "Token Paid", Booking.worker_id == current_user.id)
        .order_by(Booking.id.desc())
        .first()
    )
    if not booking or not payload.otp:
        return {"success": False, "message": "Invalid request."}

    if booking.extra_otp_code == payload.otp:
        booking.extra_otp_verified = True
        booking.extra_timer_started_at = datetime.utcnow()
        booking.main_timer_paused = False
        db.commit()
        return {"success": True}

    return {"success": False, "message": "Incorrect OTP."}

@router.post("/start_extra_timer")
def start_extra_timer(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = get_active_booking(db, current_user.id)
    if not booking:
        return {"success": False, "message": "No booking found."}
    if not booking.extra_otp_verified:
        return {"success": False, "message": "OTP not verified yet."}
    if booking.extra_timer_started_at:
        return {"success": True, "message": "Already started."}

    booking.extra_timer_started_at = datetime.utcnow()
    db.commit()
    return {"success": True}

@router.post("/stop_extra_timer")
def stop_extra_timer(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = get_active_booking(db, current_user.id)
    if not booking or not booking.extra_timer_started_at or booking.extra_timer_stopped:
        return {"success": False}

    booking.extra_timer_stopped_by = "worker" if current_user.id == booking.worker_id else "provider"
    booking.extra_timer_stopped = True
    db.commit()
    return {"success": True}

@router.post("/confirm_stop_extra_timer", response_model=ExtraConfirmOut)
def confirm_stop_extra_timer(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = get_active_booking(db, current_user.id)
    if not booking or not booking.extra_timer_stopped:
        return ExtraConfirmOut(success=False)

    extra_duration_seconds = (datetime.utcnow() - booking.extra_timer_started_at).total_seconds()
    extra_hours = extra_duration_seconds / 3600.0
    rate = booking.rate or 0.0
    amount_due = round(float(rate) * extra_hours, 2)

    booking.extra_timer_confirmed_stop = True
    db.commit()

    return ExtraConfirmOut(success=True, redirect_url=f"/pay_extra_amount/{booking.id}/{amount_due}")

@router.get("/pay_extra_amount/{booking_id}/{amount}", response_class=HTMLResponse)
def pay_extra_amount_get(
    booking_id: int,
    amount: float,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.get(Booking, booking_id) or HTTPException(404)
    if current_user.id != booking.provider_id:
        raise HTTPException(403, "Unauthorized")

    extra_hours = None
    if booking.extra_timer_started_at:
        extra_duration_seconds = (datetime.utcnow() - booking.extra_timer_started_at).total_seconds()
        extra_hours = extra_duration_seconds / 3600.0
    else:
        extra_hours = (amount / float(booking.rate)) if booking.rate else 0.0

    # Render your Jinja template:
    # Ensure you have templates configured in main and a pay_extra_amount.html file.
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="app/templates")
    response = templates.TemplateResponse(
        "pay_extra_amount.html",
        {"request": request, "amount": amount, "booking": booking, "extra_hours": extra_hours},
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@router.post("/pay_extra_amount/{booking_id}/{amount}")
def pay_extra_amount_post(
    booking_id: int,
    amount: float,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.get(Booking, booking_id) or HTTPException(404)
    provider = booking.provider
    worker = booking.worker

    if current_user.id != provider.id:
        raise HTTPException(403, "Unauthorized")

    # round down to whole tokens if needed (align with your token semantics)
    tokens_needed = int(amount)

    if provider.tokens < tokens_needed:
        # Return simple JS like your Flask, or a JSON error your frontend handles
        return HTMLResponse(
            "<script>alert('❌ Not enough tokens'); history.back();</script>",
            status_code=400
        )

    provider.tokens -= tokens_needed
    worker.tokens += tokens_needed
    booking.status = "Completed"
    booking.chat_expired = True  # if you have this column
    db.commit()

    # Redirect to welcome
    return HTMLResponse("<script>location.replace('/welcome');</script>")

@router.post("/send_message")
def send_message(
    payload: SendMessageIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not payload.message:
        return {"status": "error", "message": "No message text"}
    if not payload.booking_id:
        return {"status": "error", "message": "No booking_id"}

    try:
        msg = Message(booking_id=payload.booking_id, sender_id=current_user.id, text=payload.message)
        db.add(msg)
        db.commit()
        return {"status": "ok"}
    except Exception:
        db.rollback()
        return {"status": "error", "message": "Internal server error"}

@router.get("/get_messages/{booking_id}")
def get_messages(
    booking_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    msgs = (
        db.query(Message)
        .filter_by(booking_id=booking_id)
        .order_by(Message.timestamp.asc())
        .all()
    )
    return [
        {
            "sender_id": m.sender_id,
            "text": m.text,
            "timestamp": m.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for m in msgs
    ]

@router.post("/update_completed_quantity")
def update_completed_quantity(
    payload: UpdateQuantityIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = (
        db.query(Booking)
        .filter_by(status="Token Paid", worker_id=current_user.id)
        .order_by(Booking.id.desc())
        .first()
    )
    if not booking:
        return {"success": False, "message": "No active booking."}

    if payload.completed_quantity > (booking.quantity or 0):
        return {"success": False, "message": "Cannot exceed total quantity."}

    booking.completed_quantity = payload.completed_quantity
    if payload.completed_quantity == (booking.quantity or 0) and not getattr(booking, "verify_completion_otp", None):
        booking.verify_completion_otp = generate_otp()
    db.commit()

    return {
        "success": True,
        "otp_generated": payload.completed_quantity == (booking.quantity or 0),
        "otp": booking.verify_completion_otp,
    }

@router.post("/verify_final_otp")
def verify_final_otp(
    payload: OTPIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = (
        db.query(Booking)
        .filter_by(provider_id=current_user.id, status="Token Paid")
        .order_by(Booking.id.desc())
        .first()
    )
    if not booking:
        return {"success": False, "message": "Booking not found."}

    if booking.final_otp_code == payload.otp:
        booking.final_otp_verified = True
        db.commit()
        return {"success": True}
    return {"success": False, "message": "Invalid OTP."}

@router.get("/get_chat_status")
def get_chat_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = (
        db.query(Booking)
        .filter(
            ((Booking.worker_id == current_user.id) | (Booking.provider_id == current_user.id)),
            Booking.status == "Token Paid",
        )
        .first()
    )
    if not booking:
        return {"show_chat_icon": False}

    is_giver = booking.provider_id == current_user.id
    is_worker = booking.worker_id == current_user.id

    if is_giver and not getattr(booking, "otp_code", None):
        booking.otp_code = generate_otp()
        db.commit()

    return {
        "show_chat_icon": True,
        "booking_id": booking.id,
        "otp_code": booking.otp_code if is_giver else None,
        "show_otp_input": is_worker and not getattr(booking, "otp_verified", False),
        "show_otp": is_giver and not getattr(booking, "otp_verified", False),
    }

@router.post("/verify_otp")
def verify_otp(
    payload: OTPIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = (
        db.query(Booking)
        .filter_by(worker_id=current_user.id, status="Token Paid")
        .order_by(Booking.id.desc())
        .first()
    )
    if not booking or not getattr(booking, "otp_code", None):
        return {"success": False, "message": "No valid booking found."}

    if payload.otp == booking.otp_code and not getattr(booking, "otp_verified", False):
        booking.otp_verified = True
        booking.otp_verified_time = datetime.utcnow()
        db.commit()

        duration = getattr(booking, "job_duration_minutes", 0) or 0
        expiry_time = booking.otp_verified_time + timedelta(minutes=duration)
        now = datetime.utcnow()
        time_left = int((expiry_time - now).total_seconds()) if now < expiry_time else 0

        return {"success": True, "chat_active": time_left > 0, "time_left": time_left}

    return {"success": False, "message": "Invalid OTP."}

# ---- chat page (Jinja) ----
from fastapi.templating import Jinja2Templates
templates = Jinja2Templates(directory="app/templates")

@router.get("/chat/{booking_id}", response_class=HTMLResponse)
def chat(
    booking_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.get(Booking, booking_id) or HTTPException(404)
    if current_user.id not in [booking.worker_id, booking.provider_id]:
        raise HTTPException(403, "Forbidden")
    return templates.TemplateResponse("chat.html", {"request": request, "booking": booking})

@router.post("/update_worker_status")
def update_worker_status(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_online = bool(payload.get("online", False))
    profile = db.query(WorkerProfile).filter_by(user_id=current_user.id).first()
    if not profile:
        return {"success": False, "message": "Profile not found"}
    profile.is_online = is_online
    db.commit()
    return {"success": True, "is_online": is_online}
