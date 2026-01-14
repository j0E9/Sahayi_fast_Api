# app/routers/realtime_jobs.py
from __future__ import annotations
from fastapi import Query
import math
import random
import time
import os
from typing import Optional
from fastapi import Body
from fastapi import APIRouter, Depends, HTTPException, Request, status, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload
import razorpay
from app.database import get_db
from app.models import (
    User, Booking, Notification, Message, WorkerProfile, WalletTransaction,WorkerWarning
)
from app.services.wallet import add_ledger_row
from decimal import Decimal
from razorpay.errors import SignatureVerificationError
import logging
from datetime import datetime, timedelta
from app.settings import settings
from sqlalchemy import and_
from sqlalchemy.orm import Session
from datetime import datetime
from sqlalchemy import or_
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


def estimate_drive_seconds_for_booking(booking: Booking) -> Optional[int]:
    """
    Compute a conservative ETA (seconds) from worker to provider using stored lat/lon.
    Returns None if we cannot compute.
    """
    w = booking.worker
    p = booking.provider
    if not (w and p and w.latitude and w.longitude and p.latitude and p.longitude):
        return None

    try:
        meters = haversine(float(w.latitude), float(w.longitude),
                           float(p.latitude), float(p.longitude))
    except Exception:
        return None

    avg_speed_m_s = 12.0  # ~43 km/h, same as /get_estimated_drive_time_by_booking
    if avg_speed_m_s <= 0:
        return None

    seconds = int(meters / avg_speed_m_s)
    if seconds > 24 * 3600:
        seconds = 24 * 3600
    if seconds < 0:
        seconds = 0
    return seconds


def _ensure_drive_timer_initialized(booking: Booking, db: Session) -> None:
    """
    Ensure drive_timer_started_at and drive_eta_seconds are set and persisted.
    """
    changed = False

    if not booking.drive_timer_started_at:
        booking.drive_timer_started_at = datetime.utcnow()
        changed = True

    # If you also use drive_eta_seconds, ensure it has a default:
    if booking.drive_eta_seconds is None:
        # For testing: treat ETA as "now" (0 seconds). Adjust as needed.
        booking.drive_eta_seconds = 0
        changed = True

    if changed:
        db.commit()
        db.refresh(booking)

from sqlalchemy import and_

def auto_warn_and_cancel_if_due(booking: Booking, db: Session) -> None:
    if booking.booking_type == "wfh":
        return

    """
    Server-side scheduler for automatic warnings + auto-cancel.

    Uses WorkerWarning history to determine current stage:

      stage 0: no WorkerWarning yet          -> first warning
      stage 1: last warning.stage == 1       -> second warning
      stage 2: last warning.stage == 2       -> third/final warning
      stage 3: last warning.stage == 3       -> auto-cancel booking

    Delays (for now, kept short for testing):
      - Stage 0 -> 1 : 0.05 minutes after drive_timer_started_at
      - Stage 1 -> 2 : 0.05 minutes after last warning
      - Stage 2 -> 3 : 0.05 minutes after last warning
      - Stage 3 -> cancel : 0.05 minutes after last warning
    """

    logger.info(
        "warn_check booking=%s status=%s auto_cancelled=%s otp_verified=%s worker_arrived=%s",
        booking.id,
        booking.status,
        booking.auto_cancelled,
        getattr(booking, "otp_verified", None),
        getattr(booking, "worker_arrived", None),
    )

    # ---- Guard conditions ----
    if booking.status != "Token Paid":
        logger.info("auto_warn: skip (status != 'Token Paid') booking=%s", booking.id)
        return
    if booking.auto_cancelled:
        logger.info("auto_warn: skip (already auto_cancelled) booking=%s", booking.id)
        return
    if getattr(booking, "otp_verified", False) or getattr(booking, "worker_arrived", False):
        logger.info("auto_warn: skip (otp_verified or worker_arrived) booking=%s", booking.id)
        return

    # Make sure timer fields exist
    _ensure_drive_timer_initialized(booking, db)

    now = datetime.utcnow()

    # ---- Determine current stage from last WorkerWarning ----
    last_warning: WorkerWarning | None = (
        db.query(WorkerWarning)
        .filter(
            WorkerWarning.booking_id == booking.id,
            WorkerWarning.worker_id == booking.worker_id,
        )
        .order_by(WorkerWarning.stage.desc(), WorkerWarning.id.desc())
        .first()
    )

    if last_warning is None:
        stage = 0
        reference = booking.drive_timer_started_at or now
    else:
        stage = last_warning.stage  # 1..3 in your model
        reference = last_warning.created_at or now

    # Normalize stage to 0..3 internally
    # stage 0: no warnings yet
    # stage 1: first warning already sent
    # stage 2: second warning already sent
    # stage 3: third warning already sent -> auto-cancel next
    logger.info(
        "auto_warn: booking=%s current_stage=%s reference=%s",
        booking.id, stage, reference.isoformat()
    )

    # ---- Delays (minutes) – keep short for testing ----
    delays = {
        0: 1,   # first warning after ETA start
        1: 1,   # second warning after first
        2: 1,   # third warning after second
        3: 1,   # auto-cancel after third
    }

    delay_minutes = delays.get(stage)
    if delay_minutes is None:
        logger.info("auto_warn: invalid stage=%s booking=%s", stage, booking.id)
        return

    trigger_time = reference + timedelta(minutes=delay_minutes)
    logger.info(
        "auto_warn_timing booking=%s stage=%s now=%s trigger=%s",
        booking.id, stage, now.isoformat(), trigger_time.isoformat()
    )

    # Not yet time
    if now < trigger_time:
        return

    def _create_warning(next_stage: int, message: str, remaining: int) -> None:
        warning = WorkerWarning(
            booking_id=booking.id,
            giver_id=booking.provider_id,
            worker_id=booking.worker_id,
            stage=next_stage,            # 1, 2, 3
            remaining=remaining,
            message=message,
            acknowledged=False,          # explicitly unacknowledged
        )
        db.add(warning)

        # Optional: still keep these on Booking if you want, but they are no longer required
        booking.warn_stage = next_stage
        booking.warn_last_at = now

        try:
            db.add(Notification(
                recipient_id=booking.worker_id,
                sender_id=booking.provider_id,
                booking_id=booking.id,
                message=message,
                action_type="warn_worker",
                is_read=False,
            ))
        except Exception:
            logger.exception("auto_warn: failed to create Notification")

    # ---- Execute action for the current stage ----
    if stage == 0:
        logger.info("auto_warn: stage 0 -> create first warning booking=%s", booking.id)
        _create_warning(
            next_stage=1,
            message="⚠️ You are late to reach the job location. Please reach as soon as possible.",
            remaining=2,
        )
        db.commit()
        return

    if stage == 1:
        logger.info("auto_warn: stage 1 -> create second warning booking=%s", booking.id)
        _create_warning(
            next_stage=2,
            message="⚠️ Second reminder: you are still late. Please reach immediately or the booking may be cancelled.",
            remaining=1,
        )
        db.commit()
        return

    if stage == 2:
        logger.info("auto_warn: stage 2 -> create third warning booking=%s", booking.id)
        _create_warning(
            next_stage=3,
            message="⚠️ Final warning: this booking will be cancelled automatically in 5 minutes if you do not reach.",
            remaining=0,
        )
        db.commit()
        return

    if stage == 3:
        logger.info("auto_warn: stage 3 -> auto-cancel booking=%s", booking.id)

        booking.auto_cancelled = True
        booking.status = "Cancelled"
        booking.expires_at = now

        # 🔥 CRITICAL CLEANUP
        booking.payment_required = False
        booking.payment_completed = False

        if booking.provider:
            booking.provider.busy = False

        if booking.worker:
            booking.worker.busy = False

        try:
            db.add(Notification(
                recipient_id=booking.worker_id,
                sender_id=booking.provider_id,
                booking_id=booking.id,
                message="❌ Booking cancelled automatically due to delay in arriving.",
                action_type="booking_auto_cancelled",
                is_read=False,
            ))
        except Exception:
            logger.exception("auto_warn: failed to create auto_cancel Notification")

        db.commit()
        return


def get_next_warning_eta_seconds(booking: Booking, db: Session) -> Optional[int]:
    if booking.booking_type == "wfh":
        return None

    """
    Compute seconds until the next automatic warn/cancel event
    for this booking, based on the same logic as auto_warn_and_cancel_if_due.
    Returns:
      - >= 0 : seconds until next stage (warning or cancel)
      - None : no further warnings/cancellations scheduled
    """

    # Same guard conditions as auto_warn_and_cancel_if_due
    if booking.status != "Token Paid":
        return None
    if booking.auto_cancelled:
        return None
    if getattr(booking, "otp_verified", False) or getattr(booking, "worker_arrived", False):
        return None

    _ensure_drive_timer_initialized(booking, db)

    now = datetime.utcnow()

    last_warning: WorkerWarning | None = (
        db.query(WorkerWarning)
        .filter(
            WorkerWarning.booking_id == booking.id,
            WorkerWarning.worker_id == booking.worker_id,
        )
        .order_by(WorkerWarning.stage.desc(), WorkerWarning.id.desc())
        .first()
    )

    if last_warning is None:
        stage = 0
        reference = booking.drive_timer_started_at or now
    else:
        stage = last_warning.stage
        reference = last_warning.created_at or now

    # Same delays as in auto_warn_and_cancel_if_due
    delays = {
        0: 1,   # minutes until first warning
        1: 1,   # minutes until second warning
        2: 1,   # minutes until third warning
        3: 1,   # minutes until auto-cancel
    }

    delay_minutes = delays.get(stage)
    if delay_minutes is None:
        return None

    trigger_time = reference + timedelta(minutes=delay_minutes)
    seconds = int((trigger_time - now).total_seconds())
    return max(0, seconds)


@router.get("/dev_list_warnings/{booking_id}")
def dev_list_warnings(
    booking_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    DEV: list all WorkerWarning rows for this booking so we can see
    what worker_id / acknowledged values they have.
    """
    rows = (
        db.query(WorkerWarning)
        .filter(WorkerWarning.booking_id == booking_id)
        .order_by(WorkerWarning.id.asc())
        .all()
    )

    out = []
    for w in rows:
        out.append({
            "id": w.id,
            "booking_id": w.booking_id,
            "giver_id": w.giver_id,
            "worker_id": w.worker_id,
            "stage": w.stage,
            "remaining": w.remaining,
            "acknowledged": w.acknowledged,
            "created_at": w.created_at.isoformat() if w.created_at else None,
        })
    return out




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
    client_nonce: Optional[str] = None


class UpdateQuantityIn(BaseModel):
    completed_quantity: int = Field(ge=0)

logger = logging.getLogger("app.realtime.webhook")


@router.post("/razorpay/verify_extra_payment")
def verify_extra_payment(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Called from Razorpay Checkout success handler for EXTRA TIME.
    Expects JSON body with:
      - razorpay_order_id
      - razorpay_payment_id
      - razorpay_signature
      - booking_id
      - extra_minutes
    """

    # 1) Verify Razorpay signature
    try:
        client = razorpay.Client(
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
        )
        client.utility.verify_payment_signature(
            {
                "razorpay_order_id": payload["razorpay_order_id"],
                "razorpay_payment_id": payload["razorpay_payment_id"],
                "razorpay_signature": payload["razorpay_signature"],
            }
        )
    except KeyError:
        return {"success": False, "message": "Missing Razorpay fields"}
    except SignatureVerificationError:
        return {"success": False, "message": "Signature verification failed"}

    # 2) Extract booking + minutes from payload
    booking_id = int(payload.get("booking_id") or 0)
    extra_minutes = int(payload.get("extra_minutes") or 0)
    if booking_id <= 0 or extra_minutes <= 0:
        return {"success": False, "message": "Invalid booking or minutes"}

    # 3) Load booking and validate
    booking = db.get(Booking, booking_id)
    if not booking:
        return {"success": False, "message": "Booking not found"}

    if booking.booking_type == "wfh":
        return {
            "success": False,
            "message": "Extra payment not allowed for work-from-home jobs"
        }

    # 4) Start extra timer only AFTER validation
    timer_result = start_extra_timer(
        payload={"booking_id": booking_id, "extra_minutes": extra_minutes},
        db=db,
        current_user=current_user,
    )
    if not timer_result.get("success"):
        return timer_result

    # 4) CREDIT worker wallet for the extra-time amount
    booking = db.get(Booking, booking_id)
    if not booking or not booking.worker:
        return {"success": False, "message": "Booking/worker not found"}

    worker = booking.worker

    # Amount stored when order was created (in rupees)
    amount_rupees = Decimal(str(booking.extra_razor_amount or 0)).quantize(Decimal("0.01"))
    if amount_rupees <= 0:
        # Timer is started but there is no monetary amount to record
        return timer_result

    payment_id = payload["razorpay_payment_id"]
    order_id = payload["razorpay_order_id"]

    # Idempotency: do not credit twice for the same Razorpay payment
    existing = (
        db.query(WalletTransaction)
        .filter(
            WalletTransaction.user_id == worker.id,
            WalletTransaction.kind == "Extra_time_payment",
            WalletTransaction.reference == payment_id,
        )
        .first()
    )
    if not existing:
        add_ledger_row(
            db=db,
            user_id=worker.id,
            amount_rupees=amount_rupees,
            kind="Extra_time_payment",  # same kind as main booking credits
            reference=payment_id,
            meta={
                "booking_id": booking.id,
                "order_id": order_id,
                "method": "razorpay_extra",
                "extra_minutes": extra_minutes,
            },
        )
        db.commit()

    return timer_result



@router.post("/verify_worker_location/{booking_id}")
def verify_worker_location(
    booking_id: int,
    payload: LatLonIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if booking.booking_type == "wfh":
        raise HTTPException(
            status_code=400,
            detail="Location verification not applicable for work-from-home jobs"
        )

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
    """
    Called by the worker. Instead of OTP flow, mark a proposal so the giver UI
    will show an input for entering extra minutes and Confirm/Cancel.
    """
    booking = (
        db.query(Booking)
        .filter(Booking.status == "Token Paid", Booking.worker_id == current_user.id)
        .order_by(Booking.id.desc())
        .first()
    )

    if not booking:
        return {"success": False, "message": "No active booking."}

    if booking.booking_type == "wfh":
        return {
            "success": False,
            "message": "Extra time not allowed for work-from-home jobs"
        }

    if booking.extra_timer_requested:
        return {"success": False, "message": "Already requested."}

    # Mark a proposal — UI will present minutes input to the provider (giver).
    booking.extra_timer_requested = True
    booking.extra_timer_requested_at = datetime.utcnow()
    booking.main_timer_paused = True

    # Make sure any previous extra-payment flags are cleared
    booking.extra_payment_completed = False
    booking.extra_razor_order_id = None
    booking.proposed_extra_minutes = None
    booking.extra_razor_amount = None

    db.commit()

    # Optionally: create a Notification for provider (if you have that table/flow)
    try:
        if booking.provider:
            db.add(Notification(
                recipient_id=booking.provider.id,
                sender_id=current_user.id,
                booking_id=booking.id,
                message=f"🔔 Worker requested extra time. Enter minutes and confirm to pay.",
                action_type="extra_time_requested",
                is_read=False,
            ))
            db.commit()
    except Exception:
        db.rollback()

    return {"success": True, "message": "Extra time requested — waiting for provider input."}


@router.get("/get_estimated_drive_time_by_booking")
def get_estimated_drive_time_by_booking(
    booking_id: int = Query(..., alias="booking_id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return a rough ETA (seconds) for the booking's worker -> provider.
    Returns {"seconds": <int>} when calculable, or {"seconds": None} when unknown.
    """
    booking = db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if booking.booking_type == "wfh":
        return {"seconds": None}


    # Find coords for worker -> provider (both must exist)
    w = booking.worker
    p = booking.provider
    if not (w and p and getattr(w, "latitude", None) and getattr(w, "longitude", None)
            and getattr(p, "latitude", None) and getattr(p, "longitude", None)):
        # front-end should fallback when this is None
        return {"seconds": None}

    try:
        lat1 = float(w.latitude)
        lon1 = float(w.longitude)
        lat2 = float(p.latitude)
        lon2 = float(p.longitude)
    except Exception:
        return {"seconds": None}

    meters = haversine(lat1, lon1, lat2, lon2)  # your helper returns meters
    # Conservative average speed (m/s). Adjust to your needs.
    avg_speed_m_s = 12.0  # ~43 km/h
    if avg_speed_m_s <= 0:
        return {"seconds": None}
    seconds = int(meters / avg_speed_m_s)

    # safety clamp
    if seconds > 24 * 3600:
        seconds = 24 * 3600

    return {"seconds": seconds}


@router.post("/razorpay/create_order_for_extra/{booking_id}")
def create_order_for_extra(
    booking_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 1) Read minutes safely
    minutes_raw = payload.get("extra_minutes")
    try:
        minutes = int(minutes_raw)
    except (TypeError, ValueError):
        return {"success": False, "message": "Invalid minutes"}

    booking = db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if booking.booking_type == "wfh":
        return {
            "success": False,
            "message": "Extra time is not applicable for work-from-home jobs"
        }

    # Only provider (giver) can pay
    if current_user.id != booking.provider_id:
        raise HTTPException(status_code=403, detail="Not authorized")

    if minutes <= 0:
        return {"success": False, "message": "Invalid minutes"}

    # --- NEW: compute per-minute rate from your hourly rate ---
    rate_type = (booking.rate_type or "").strip().lower()
    rate_rupees = Decimal(str(booking.rate or 0))

    if getattr(booking, "rate_per_minute", None):
        # if you already have it in DB, reuse
        price_per_minute_inr = Decimal(str(booking.rate_per_minute))
    elif rate_type == "per hour":
        # convert per-hour to per-minute
        price_per_minute_inr = (rate_rupees / Decimal("60")).quantize(Decimal("0.01"))
        # optionally persist it for later reuse:
        if hasattr(booking, "rate_per_minute"):
            booking.rate_per_minute = float(price_per_minute_inr)
    else:
        # fallback: treat rate as total for job_duration_minutes
        duration_minutes = getattr(booking, "job_duration_minutes", 0) or 60
        price_per_minute_inr = (rate_rupees / Decimal(str(duration_minutes))).quantize(Decimal("0.01"))

    amount_in_inr = (Decimal(str(minutes)) * price_per_minute_inr).quantize(Decimal("0.01"))
    amount_paise = int(amount_in_inr * 100)

    # Safety check so we don’t crash with empty keys
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Razorpay keys not configured on server",
        )

    # 4) Create Razorpay order for the extra-time amount
    client = razorpay.Client(
        auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
    )
    order = client.order.create(
        {
            "amount": amount_paise,
            "currency": "INR",
            "payment_capture": 1,
            "notes": {
                "booking_id": str(booking_id),
                "extra_minutes": str(minutes),
            },
        }
    )

    # 5) Persist order details on the booking
    booking.proposed_extra_minutes = minutes
    booking.extra_razor_order_id = order["id"]
    booking.extra_razor_amount = float(amount_in_inr)  # store rupees for wallet credit
    booking.extra_payment_completed = False
    db.commit()

    # 6) Return data for Razorpay Checkout.js on frontend
    return {
        "success": True,
        "order_id": order["id"],
        "amount": order["amount"],
        "currency": order["currency"],
        "key_id": settings.RAZORPAY_KEY_ID,
    }


@router.post("/verify_extra_timer_otp")
def verify_extra_timer_otp(
    payload: OTPIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Deprecated: OTP-based extra timer verification has been removed from the
    active flow. The new flow uses provider input + payment and then calls
    /start_extra_timer. This endpoint remains for backwards compatibility but
    will return a deprecation response.
    """
    return {"success": False, "message": "Deprecated. OTP flow removed. Use provider-confirmation and payment flow."}




@router.post("/start_extra_timer")
def start_extra_timer(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Start the extra timer.

    Expected payload: { "booking_id": <int>, "extra_minutes": <int> }

    Behaviour:
    - Persist proposed minutes and mark extra_payment_completed.
    - If main session has ended (or not hourly), start extra timer now.
    - Otherwise return will_start_after_main=True and do NOT start timer.
    """
    booking_id = int(payload.get("booking_id") or 0)
    extra_minutes = int(payload.get("extra_minutes") or 0)

    booking = db.get(Booking, booking_id)
    if not booking:
        return {"success": False, "message": "Booking not found."}

    if booking.booking_type == "wfh":
        return {
            "success": False,
            "message": "Extra timer not allowed for work-from-home jobs"
        }

    # Only provider or worker participants allowed to call
    if current_user.id not in (booking.provider_id, booking.worker_id):
        raise HTTPException(status_code=403, detail="Not part of booking")

    # Persist minutes (if provided)
    if extra_minutes and extra_minutes > 0:
        booking.proposed_extra_minutes = extra_minutes

    # Mark payment completed (webhook is preferred; this keeps parity with client flow)
    booking.extra_payment_completed = True
    # Clear the "requested" flag — we are either starting or scheduling it
    booking.extra_timer_requested = False
    booking.main_timer_paused = False
    db.commit()

    # If already started, return ok
    if getattr(booking, "extra_timer_started_at", None):
        return {"success": True, "message": "Already started.", "proposed_minutes": booking.proposed_extra_minutes}

    # Compute remaining main session time for hourly jobs
    main_time_left = 0
    if getattr(booking, "otp_verified", False) and getattr(booking, "otp_verified_time", None) and (booking.rate_type or "").strip().lower() == "per hour":
        duration_secs = (booking.quantity or 0) * 3600
        expiry_time = booking.otp_verified_time + timedelta(seconds=duration_secs)
        now = datetime.utcnow()
        main_time_left = (expiry_time - now).total_seconds()

    # If main session already ended (or this isn't hourly / otp flow), start extra timer now.
    if main_time_left <= 0:
        booking.extra_timer_started_at = datetime.utcnow()

        # NEW — set authoritative end time
        try:
            mins = int(getattr(booking, "proposed_extra_minutes", 0) or 0)
        except Exception:
            mins = 0

        if mins > 0:
            booking.extra_timer_ends_at = booking.extra_timer_started_at + timedelta(minutes=mins)
        else:
            booking.extra_timer_ends_at = None

        db.commit()
        return {
            "success": True,
            "message": "Extra timer started.",
            "proposed_minutes": booking.proposed_extra_minutes,
            "extra_timer_ends_at": booking.extra_timer_ends_at.isoformat() if booking.extra_timer_ends_at else None
        }

    # Otherwise, signal frontend that payment is recorded and extra will start later
    return {
        "success": True,
        "message": "Payment recorded; extra will start after the current session ends.",
        "will_start_after_main": True,
        "main_time_left_seconds": int(main_time_left),
        "proposed_minutes": booking.proposed_extra_minutes,
    }


@router.post("/cancel_extra_time")
def cancel_extra_time(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Called by provider to cancel an extra-time proposal.
    Expects: { "booking_id": <int> }
    """
    booking_id = int(payload.get("booking_id") or 0)
    booking = db.get(Booking, booking_id)
    if not booking:
        return {"success": False, "message": "Booking not found."}

    # only provider can cancel
    if current_user.id != booking.provider_id:
        return {"success": False, "message": "Not authorized."}

    # Clear extra time negotiation fields
    booking.extra_timer_requested = False
    booking.extra_timer_requested_at = None
    booking.proposed_extra_minutes = None
    booking.extra_razor_order_id = None
    booking.extra_razor_payment_id = None
    booking.extra_razor_amount = None
    booking.extra_payment_completed = False
    booking.main_timer_paused = False
    db.commit()
    return {"success": True, "message": "Extra time cancelled."}




@router.get("/pay_extra_amount/{booking_id}/{amount}", response_class=HTMLResponse)
def pay_extra_amount_get(
    booking_id: int,
    amount: float,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

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
    booking = db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
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

    # ensure booking exists and user is a participant
    booking = db.get(Booking, payload.booking_id)
    if not booking:
        return {"status": "error", "message": "Booking not found"}
    if current_user.id not in (booking.provider_id, booking.worker_id):
        raise HTTPException(status_code=403, detail="Not part of booking")

    client_nonce = (payload.client_nonce or "").strip() or None

    try:
        # 1) If client_nonce supplied and DB has client_nonce column, try to find existing message
        if client_nonce and hasattr(Message, "client_nonce"):
            existing = (
                db.query(Message)
                .filter(
                    Message.booking_id == payload.booking_id,
                    Message.sender_id == current_user.id,
                    getattr(Message, "client_nonce") == client_nonce
                )
                .first()
            )
            if existing:
                ts_field = getattr(existing, "timestamp", None) or getattr(existing, "created_at", None)
                ts_ms = int(ts_field.timestamp() * 1000) if ts_field else int(datetime.utcnow().timestamp() * 1000)
                return {
                    "status": "ok",
                    "message": {
                        "id": existing.id,
                        "booking_id": existing.booking_id,
                        "sender_id": existing.sender_id,
                        "text": existing.text,
                        "client_nonce": getattr(existing, "client_nonce", None),
                        "ts": ts_ms
                    }
                }

        # 2) Fallback duplicate guard: identical text from same sender within last 5 seconds
        # IMPORTANT: only apply this fallback when client_nonce was NOT provided.
        recent_dup = None
        if not client_nonce:
            fallback_window_seconds = 5
            if hasattr(Message, "timestamp"):
                recent_cutoff = datetime.utcnow() - timedelta(seconds=fallback_window_seconds)
                recent_dup = (
                    db.query(Message)
                    .filter(
                        Message.booking_id == payload.booking_id,
                        Message.sender_id == current_user.id,
                        Message.text == payload.message,
                        getattr(Message, "timestamp") >= recent_cutoff
                    )
                    .order_by(getattr(Message, "timestamp").desc())
                    .first()
                )
            else:
                recent_dup = (
                    db.query(Message)
                    .filter(
                        Message.booking_id == payload.booking_id,
                        Message.sender_id == current_user.id,
                        Message.text == payload.message
                    )
                    .order_by(Message.id.desc())
                    .first()
                )

        if recent_dup:
            ts_field = getattr(recent_dup, "timestamp", None) or getattr(recent_dup, "created_at", None)
            ts_ms = int(ts_field.timestamp() * 1000) if ts_field else None
            return {
                "status": "ok",
                "message": {
                    "id": recent_dup.id,
                    "booking_id": recent_dup.booking_id,
                    "sender_id": recent_dup.sender_id,
                    "text": recent_dup.text,
                    "client_nonce": getattr(recent_dup, "client_nonce", None),
                    "ts": ts_ms
                }
            }

        # 3) Create new message row (use client_nonce if provided)
        kwargs = {
            "booking_id": payload.booking_id,
            "sender_id": current_user.id,
            "text": payload.message,
        }
        if client_nonce and hasattr(Message, "client_nonce"):
            kwargs["client_nonce"] = client_nonce

        m = Message(**kwargs)
        db.add(m)
        db.commit()
        db.refresh(m)

        ts_field = getattr(m, "timestamp", None) or getattr(m, "created_at", None)
        ts_ms = int(ts_field.timestamp() * 1000) if ts_field else int(datetime.utcnow().timestamp() * 1000)

        return {
            "status": "ok",
            "message": {
                "id": m.id,
                "booking_id": m.booking_id,
                "sender_id": m.sender_id,
                "text": m.text,
                "client_nonce": getattr(m, "client_nonce", None),
                "ts": ts_ms
            }
        }
    except Exception:
        db.rollback()
        return {"status": "error", "message": "Internal server error"}


@router.get("/get_messages/{booking_id}")
def get_messages(
    booking_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if current_user.id not in (booking.provider_id, booking.worker_id):
        raise HTTPException(status_code=403, detail="Not allowed")

    order_col = Message.timestamp if hasattr(Message, "timestamp") else Message.id
    msgs = (
        db.query(Message)
        .filter_by(booking_id=booking_id)
        .order_by(order_col.asc())
        .all()
    )

    out = []
    for m in msgs:
        ts_field = getattr(m, "timestamp", None) or getattr(m, "created_at", None)
        ts_ms = int(ts_field.timestamp() * 1000) if ts_field else None
        out.append({
            "id": m.id,
            "sender_id": m.sender_id,
            "text": m.text,
            "client_nonce": getattr(m, "client_nonce", None),
            "ts": ts_ms
        })
    return out



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
            ((Booking.worker_id == current_user.id) |
             (Booking.provider_id == current_user.id)),
            Booking.status == "Token Paid",
            Booking.payment_completed == True
        )
        .first()
    )

    if not booking:
        return {"show_chat_icon": False}
    if booking.booking_type == "wfh":
        # Block interaction until price is confirmed
        if not getattr(booking, "price_confirmed", False):
            return {
                "show_chat_icon": False,
                "wfh_pending": True,
                "booking_id": booking.id,
            }

        return {
            "show_chat_icon": True,
            "booking_id": booking.id,
            "otp_code": None,
            "show_otp_input": False,
            "show_otp": False,
        }

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
    if not booking:
        return {"success": False, "message": "No active booking found"}

    if booking.booking_type == "wfh":
        return {
            "success": False,
            "message": "OTP not required for work-from-home jobs"
        }

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
    booking = db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
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




@router.get("/worker_check_warning")
def worker_check_warning(
    booking_id: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.get(Booking, booking_id)
    if not booking:
        return {"warning": None, "next_warning_in_seconds": None}

    if booking.booking_type == "wfh":
        return {"warning": None, "next_warning_in_seconds": None}

    # Only worker involved in this booking can see warnings
    if current_user.id != booking.worker_id:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Evaluate automatic warn / cancel schedule (may create new warning / cancel)
    auto_warn_and_cancel_if_due(booking, db)

    # Recompute next warning ETA *after* scheduler runs
    next_eta = get_next_warning_eta_seconds(booking, db)

    # Fetch latest, *unacknowledged* warning for this booking+worker
    warning = (
        db.query(WorkerWarning)
        .filter(
            WorkerWarning.booking_id == booking.id,
            WorkerWarning.worker_id == current_user.id,
            or_(
                WorkerWarning.acknowledged == False,
                WorkerWarning.acknowledged.is_(None),
            ),
        )
        .order_by(WorkerWarning.id.desc())
        .first()
    )

    if not warning:
        return {
            "warning": None,
            "next_warning_in_seconds": next_eta,
        }

    return {
        "warning": {
            "id": warning.id,
            "stage": warning.stage,
            "remaining": warning.remaining,
            "message": warning.message,
            "created_at": warning.created_at.isoformat(),
        },
        "next_warning_in_seconds": next_eta,
    }




class AckWarningIn(BaseModel):
    booking_id: int
    warning_id: int

@router.post("/ack_warning")
def ack_warning(
    payload: AckWarningIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    warning = db.get(WorkerWarning, payload.warning_id)
    if not warning or warning.booking_id != payload.booking_id:
        return {"success": False, "message": "Warning not found."}

    if current_user.id != warning.worker_id:
        raise HTTPException(status_code=403, detail="Not authorized")

    warning.acknowledged = True
    db.commit()
    return {"success": True}
