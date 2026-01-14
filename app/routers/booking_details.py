# app/routers/booking_details.py
from __future__ import annotations

from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, status, Body
from sqlalchemy.orm import Session, joinedload
from app.models import Rating
from app.database import get_db
from app.models import Booking, User, WorkerWarning



import secrets
def generate_otp(length: int = 6) -> str:
    """Return a numeric OTP of given length."""
    return "".join(str(secrets.randbelow(10)) for _ in range(length))

router = APIRouter(tags=["booking-details"])

# ---- session-based auth (Trust API style) ----
def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = db.get(User, int(uid))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user



def _payment_flags(booking: Booking) -> dict:
    return {
        "payment_required": bool(getattr(booking, "payment_required", True)),
        "payment_completed": bool(getattr(booking, "payment_completed", False)),
        "razorpay_status": (getattr(booking, "razorpay_status", "") or "").lower(),
    }


# ---- util: plain chat URL (avoid url_for name lookups) ----
def chat_url_for(booking_id: int) -> str:
    # Make sure your chat route actually matches this path
    return f"/chat/{booking_id}"

def _next_warning_allowed(db: Session, booking: Booking) -> tuple[bool, int]:
    """
    Returns (allowed, remaining) for issuing a warning.
    allowed=True only if total warnings so far < 3.
    remaining is how many warnings remain including the next one.
    """
    latest = (
        db.query(WorkerWarning)
        .filter(
            WorkerWarning.booking_id == booking.id,
            WorkerWarning.worker_id == booking.worker_id,
        )
        .order_by(WorkerWarning.created_at.desc())
        .first()
    )
    stage = latest.stage if latest else 0
    remaining = max(0, 3 - stage)   # warnings left BEFORE issuing the next
    return (stage < 3, remaining)

def has_giver_rated(db: Session, booking: Booking) -> bool:
    return db.query(Rating).filter(
        Rating.booking_id == booking.id,
        Rating.job_giver_id == booking.provider_id,
    ).first() is not None

def _find_pending_rating_booking_for_giver(db: Session, provider_id: int) -> Booking | None:
    """
    Return one Completed booking for which the provider (giver) has not yet
    submitted a rating and has not already been prompted for rating.
    Prefer the earliest completed booking first (FIFO).
    """
    q = (
        db.query(Booking)
        .filter(
            Booking.provider_id == provider_id,
            Booking.status == "Completed",
            Booking.rating_prompted_at == None  # only those not already prompted
        )
        .order_by(Booking.id.asc())
    )
    for b in q.all():
        if not has_giver_rated(db, b):
            return b
    return None



def _payload_for_booking(booking: Booking, viewer: User, db: Session) -> dict:
    """
    Returns the same payload shape your frontend already expects,
    computed for the given `booking` as seen by `viewer`.
    Mirrors feature parity with get_booking_details.
    """
    if not booking:
        return {"show": False, "message": "No active chat."}

    user_id = viewer.id

    # Close chat for terminal states (Rejected/Cancelled = hard close)
    if booking.status in ["Rejected", "Cancelled"]:
        if booking.worker:
            booking.worker.busy = False
        if booking.provider:
            booking.provider.busy = False
        db.commit()
        return {"show": False, "message": "No active chat. Job ended or rejected."}

    # Completed — allow rating popup for the giver
    if booking.status == "Completed":
        if booking.worker:
            booking.worker.busy = False
        if booking.provider:
            booking.provider.busy = False
        db.commit()

        is_giver = (booking.provider_id == user_id)
        is_worker = (booking.worker_id == user_id)

        name = booking.provider.name if is_worker else booking.worker.name
        lat = booking.provider.latitude if is_worker else booking.worker.latitude
        lon = booking.provider.longitude if is_worker else booking.worker.longitude
        map_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}" if lat and lon else ""

        if is_giver and not has_giver_rated(db, booking):
            try:
                booking.rating_prompted_at = datetime.utcnow()
                db.add(booking)
                db.commit()
            except Exception:
                db.rollback()
            return _rating_payload(booking, is_giver, name, map_url)

        return {"show": False, "message": "✅ Job completed."}

    # Pending → restrict giver; chat not visible yet
    if booking.status == "Pending":
        db.commit()
        return {"show": False, "message": "⏳ Waiting for worker response."}

    # Only active chats visible
    if booking.status not in ["Token Paid", "In Progress", "Extra Time"]:
        return {"show": False, "message": "No active chat. Payment not done or job ended."}

    is_giver = (booking.provider_id == user_id)
    is_worker = (booking.worker_id == user_id)

    # Warnings are now fully automatic from the backend.
    # We do NOT allow manual warn from the UI anymore.
    allowed, remaining = _next_warning_allowed(db, booking)  # kept only for info/logs
    can_warn_now = False


    # Counterpart info
    name = booking.provider.name if is_worker else booking.worker.name
    lat = booking.provider.latitude if is_worker else booking.worker.latitude
    lon = booking.provider.longitude if is_worker else booking.worker.longitude
    map_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}" if lat and lon else ""

    now = datetime.utcnow()
    extra_timer_started_at = getattr(booking, "extra_timer_started_at", None)
    extra_timer_stopped = bool(getattr(booking, "extra_timer_stopped", False))
    extra_timer_confirmed_stop = bool(getattr(booking, "extra_timer_confirmed_stop", False))
    extra_timer_stopped_by = getattr(booking, "extra_timer_stopped_by", None)
    extra_otp_code = getattr(booking, "extra_otp_code", None)
    extra_timer_requested = bool(getattr(booking, "extra_timer_requested", False))
    extra_otp_verified = bool(getattr(booking, "extra_otp_verified", False))

    # payment/order fields and proposed minutes
    extra_payment_completed = bool(getattr(booking, "extra_payment_completed", False))
    extra_razor_order_id = getattr(booking, "extra_razor_order_id", None)
    proposed_extra_minutes = getattr(booking, "proposed_extra_minutes", None)

    extra_timer_running = bool(extra_timer_started_at and (not extra_timer_stopped) and (not extra_timer_confirmed_stop))
    extra_duration_seconds = int((now - extra_timer_started_at).total_seconds()) if extra_timer_started_at else 0

    try:
        proposed_min = getattr(booking, "proposed_extra_minutes", None)
        proposed_min_int = int(proposed_min) if proposed_min is not None else None
    except Exception:
        proposed_min_int = None

    # explicit end time if exists
    ends_at = getattr(booking, "extra_timer_ends_at", None)

    # Auto-stop based on explicit ends_at
    if ends_at and not booking.extra_timer_stopped:
        if now >= ends_at:
            booking.extra_timer_confirmed_stop = True
            booking.extra_timer_stopped = True
            booking.status = "Completed"
            if booking.worker:
                booking.worker.busy = False
            if booking.provider:
                booking.provider.busy = False
            db.commit()

            if is_giver and not has_giver_rated(db, booking):
                return _rating_payload(booking, is_giver, name, map_url)

            return {"show": False, "message": "✅ Extra time ended — job completed."}

    extra_timer_pending = extra_timer_requested and (not extra_payment_completed)

    extra = {
        "extra_timer_started_at": extra_timer_started_at,
        "extra_timer_running": extra_timer_running,
        "extra_duration_seconds": extra_duration_seconds,
        "extra_timer_stopped": extra_timer_stopped,
        "extra_timer_stopped_by": extra_timer_stopped_by,
        "extra_timer_confirmed_stop": extra_timer_confirmed_stop,
        "extra_timer_requested": extra_timer_requested,
        "extra_timer_pending": extra_timer_pending,
        "extra_otp_code": extra_otp_code,
        "extra_otp_verified": extra_otp_verified,
        "extra_payment_completed": extra_payment_completed,
        "extra_razor_order_id": extra_razor_order_id,
        "proposed_extra_minutes": proposed_extra_minutes,
    }

    # role helper
    role_for_payload = {"self": "giver" if is_giver else "worker"} if 'is_giver' in locals() else None

    rate_type = (booking.rate_type or "").strip().lower()
    is_quantity_based = rate_type in ["per job", "per kilogram", "custom"]
    is_hourly = rate_type == "per hour"

    # Generate initial OTP to start the job (giver only)
    if is_giver and not getattr(booking, "otp_code", None):
        booking.otp_code = generate_otp()
        db.commit()

    # Quantity-based flow
    if is_quantity_based and (booking.completed_quantity or 0) >= (booking.quantity or 0):
        if not getattr(booking, "final_otp_code", None):
            booking.final_otp_code = generate_otp()
            db.commit()

        if getattr(booking, "final_otp_verified", False):
            booking.status = "Completed"
            if booking.worker:
                booking.worker.busy = False
            if booking.provider:
                booking.provider.busy = False
            db.commit()
            if is_giver and not has_giver_rated(db, booking):
                return _rating_payload(booking, is_giver, name, map_url)
            return {"show": False, "message": "✅ Job completed and verified."}

        return {
            "show": True,
            **_payment_flags(booking),
            "booking_id": booking.id,
            "completed_phase": True,
            "giver_name": name,
            "chat_url": chat_url_for(booking.id),
            "map_url": map_url,
            "final_otp_code": booking.final_otp_code if is_worker else None,
            "show_final_otp_input": is_giver and not getattr(booking, "final_otp_verified", False),
            "final_otp_verified": getattr(booking, "final_otp_verified", False),
            "rate_type": booking.rate_type,
            "quantity": booking.quantity,
            "completed_quantity": booking.completed_quantity,
            "chat_active": True,
            "can_issue_warning": False,
            "warnings_remaining": None,

        }

    # Hourly flow
    time_left = None
    if is_hourly and getattr(booking, "otp_verified", False) and getattr(booking, "otp_verified_time", None):
        duration_secs = (booking.quantity or 0) * 3600
        expiry_time = booking.otp_verified_time + timedelta(seconds=duration_secs)
        time_left = (expiry_time - now).total_seconds()

        if time_left <= 0:
            # Grace check (recent request)
            grace_seconds = 15
            extra_requested_now = getattr(booking, "extra_timer_requested", False)
            extra_requested_at = getattr(booking, "extra_timer_requested_at", None)
            recent_request = bool(
                extra_requested_now
                and extra_requested_at
                and (now - extra_requested_at).total_seconds() <= grace_seconds
            )

            # -----------------------------
            # IMPORTANT FIX: If payment done -> DO NOT end booking.
            # Start or show the extra timer instead.
            # -----------------------------
            if extra_payment_completed:
                # If extra timer isn't started yet, start it now and set an end time if proposed minutes exist
                if not getattr(booking, "extra_timer_started_at", None):
                    booking.extra_timer_started_at = datetime.utcnow()
                    try:
                        mins = int(getattr(booking, "proposed_extra_minutes", 0) or 0)
                    except Exception:
                        mins = 0
                    if mins > 0:
                        booking.extra_timer_ends_at = booking.extra_timer_started_at + timedelta(minutes=mins)
                    else:
                        booking.extra_timer_ends_at = None
                    # mark status to an "Extra Time" state so the chat remains active
                    booking.status = "Extra Time"
                    db.commit()

                # show running extra timer to frontend
                extra_elapsed = (datetime.utcnow() - booking.extra_timer_started_at).total_seconds() if booking.extra_timer_started_at else 0
                return {
                    "show": True,
                    **_payment_flags(booking),
                    "chat_active": True,
                    "booking_id": booking.id,
                    "extra_timer_running": True,
                    "extra_duration_seconds": int(extra_elapsed),
                    "giver_name": name,
                    "chat_url": chat_url_for(booking.id),
                    "map_url": map_url,
                    "stop_confirmed": getattr(booking, "extra_timer_confirmed_stop", False),
                    "show_stop_button": is_worker,
                    "can_issue_warning": False,
                    "warnings_remaining": None,

                }

            # If worker recently requested or request still flagged -> show provider input/payment flow
            if extra_requested_now or recent_request:
                proposed_minutes = getattr(booking, "proposed_extra_minutes", None)

                # Provider hasn't created order & payment not done -> show input UI
                if not getattr(booking, "extra_razor_order_id", None) and not extra_payment_completed:
                    return {
                        "show": True,
                        **_payment_flags(booking),
                        "booking_id": booking.id,
                        "giver_name": name,
                        "chat_url": chat_url_for(booking.id),
                        "map_url": map_url,
                        "extra_time_proposal": True,
                        "show_extra_time_input_to_giver": is_giver,
                        "proposed_minutes": proposed_minutes,
                        "extra_timer_pending": True,
                        "chat_active": True,
                        "time_left": 0,
                        "message": "Worker requested extra time — enter minutes and confirm to pay.",
                        **extra,
                        "role": {"self": "giver" if is_giver else "worker"},
                        "can_issue_warning": False,
                        "warnings_remaining": None,

                    }

                # If order created but payment not done -> show waiting-for-payment UI
                if getattr(booking, "extra_razor_order_id", None) and not extra_payment_completed:
                    return {
                        "show": True,
                        **_payment_flags(booking),
                        "booking_id": booking.id,
                        "extra_payment_required": True,
                        "extra_razor_order_id": getattr(booking, "extra_razor_order_id", None),
                        "proposed_minutes": getattr(booking, "proposed_extra_minutes", None),
                        "chat_active": True,
                        "message": "Waiting for extra-time payment to be completed by provider.",
                        "can_issue_warning": False,
                        "warnings_remaining": None,

                    }

                # Payment done case handled earlier; proceed to other extra checks...

                # If extra timer already started and not stopped -> return running view
                if booking.extra_timer_started_at and not getattr(booking, "extra_timer_stopped", False):
                    extra_elapsed = (datetime.utcnow() - booking.extra_timer_started_at).total_seconds()
                    return {
                        "show": True,
                        **_payment_flags(booking),
                        "chat_active": True,
                        "booking_id": booking.id,
                        "extra_timer_running": True,
                        "extra_duration_seconds": int(extra_elapsed),
                        "giver_name": name,
                        "chat_url": chat_url_for(booking.id),
                        "map_url": map_url,
                        "stop_confirmed": getattr(booking, "extra_timer_confirmed_stop", False),
                        "show_stop_button": is_worker,
                        "can_issue_warning": False,
                        "warnings_remaining": None,

                    }

                # If extra timer stopped & confirmed -> complete booking
                if getattr(booking, "extra_timer_confirmed_stop", False):
                    booking.status = "Completed"
                    if booking.worker:
                        booking.worker.busy = False
                    if booking.provider:
                        booking.provider.busy = False
                    db.commit()
                    if is_giver and not has_giver_rated(db, booking):
                        return _rating_payload(booking, is_giver, name, map_url)
                    return {"show": False, "message": "✅ Extra time completed. Job finished."}

                # Stop requested but not yet confirmed -> show confirm stop button to relevant party
                return {
                    "show": True,
                    "chat_active": False,
                    "extra_timer_stopped": True,
                    "booking_id": booking.id,
                    "show_confirm_stop_button": (
                        (is_worker and getattr(booking, "extra_timer_stopped_by", "") == "provider")
                        or (is_giver and getattr(booking, "extra_timer_stopped_by", "") == "worker")
                    ) and not getattr(booking, "extra_timer_confirmed_stop", False),
                    "giver_name": name,
                }

            # No extra timer requested and no payment -> expire chat and complete booking
            booking.status = "Completed"
            if booking.worker:
                booking.worker.busy = False
            if booking.provider:
                booking.provider.busy = False
            db.commit()
            if is_giver and not has_giver_rated(db, booking):
                return _rating_payload(booking, is_giver, name, map_url)
            return {"show": False, "message": "⛔ Chat expired. No extra time was requested."}

        # Less than 10 minutes left → offer extra time (worker)
        if time_left < 600 and not getattr(booking, "extra_timer_requested", False):
            return {
                "show": True,
                **_payment_flags(booking),
                "show_extra_timer_button": is_worker,
                "chat_url": chat_url_for(booking.id),
                "map_url": map_url,
                "giver_name": name,
                "booking_id": booking.id,
                "rate_type": booking.rate_type,
                "quantity": booking.quantity,
                "completed_quantity": booking.completed_quantity,
                "otp_code": booking.otp_code if is_giver else None,
                "show_otp_input": is_worker and getattr(booking, "worker_arrived", False) and not getattr(booking, "otp_verified", False),
                "show_reached_slider": is_worker and not getattr(booking, "worker_arrived", False),
                "otp_verified": getattr(booking, "otp_verified", False),
                "chat_active": True,
                "time_left": time_left,
                "can_issue_warning": False,
                "warnings_remaining": None,

            }

    # Final fallback (active chat)
    return {
        "show": True,
        **_payment_flags(booking),
        "giver_name": name,
        "booking_id": booking.id,
        "chat_url": chat_url_for(booking.id),
        "map_url": map_url,
        "otp_code": booking.otp_code if is_giver else None,
        "show_otp_input": is_worker and getattr(booking, "worker_arrived", False) and not getattr(booking, "otp_verified", False),
        "show_reached_slider": is_worker and not getattr(booking, "worker_arrived", False),
        "otp_verified": getattr(booking, "otp_verified", False),
        "chat_active": True,
        "time_left": time_left if is_hourly else None,
        "rate_type": booking.rate_type,
        "quantity": booking.quantity or 0,
        "completed_quantity": booking.completed_quantity or 0,
        "debug": {"is_worker": is_worker, "is_giver": is_giver},
        "can_issue_warning": False,
        "warnings_remaining": None,
        "completed": booking.status == "Completed",
        "rating_pending": (booking.status == "Completed") and (is_giver and not has_giver_rated(db, booking)),
        "role": {"self": "giver" if is_giver else "worker"},
        **extra,
        "extra_payment_completed": extra.get("extra_payment_completed", False),
        "extra_razor_order_id": extra.get("extra_razor_order_id", None),
        "proposed_extra_minutes": extra.get("proposed_extra_minutes", None),
    }



def _rating_payload(booking: Booking, is_giver: bool, name: str, map_url: str) -> dict:
    """Payload that tells the frontend to show the rating popup to the giver."""
    return {
        "show": True,
        "completed": True,
        "rating_pending": is_giver and True,           # force popup for giver
        "role": {"self": "giver" if is_giver else "worker"},
        "booking_id": booking.id,
        "giver_name": name,
        "chat_url": chat_url_for(booking.id),
        "map_url": map_url,
    }


@router.get("/get_active_bookings")
def get_active_bookings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = (
        db.query(Booking)
        .options(joinedload(Booking.provider), joinedload(Booking.worker))
        .filter(
            ( (Booking.provider_id == current_user.id) | (Booking.worker_id == current_user.id) )
            & (Booking.status.in_(["Token Paid", "In Progress", "Extra Time"]))
        )
        .order_by(Booking.id.desc())
    )

    items = []
    for b in q.all():
        # partner name relative to viewer
        is_giver = (b.provider_id == current_user.id)
        partner = b.worker.name if is_giver else b.provider.name
        items.append({
            "booking_id": b.id,
            "partner": partner,
            "status": b.status,
            "role": "giver" if is_giver else "worker"
        })
    return {"items": items}

@router.post("/get_booking_details_by_id/{booking_id}")
def get_booking_details_by_id(
    booking_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = (
        db.query(Booking)
        .options(joinedload(Booking.provider), joinedload(Booking.worker))
        .filter(Booking.id == booking_id)
        .first()
    )
    if not booking:
        return {"show": False, "message": "No such booking."}

    # Optional: security guard — make sure viewer is party to this booking
    if booking.provider_id != current_user.id and booking.worker_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not allowed")

    return _payload_for_booking(booking, current_user, db)


@router.post("/get_booking_details")
def get_booking_details(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):

    if getattr(current_user, "id", None):
        try:
            pending = _find_pending_rating_booking_for_giver(db, provider_id=current_user.id)
            if pending:

                pending = (
                    db.query(Booking)
                    .options(joinedload(Booking.provider), joinedload(Booking.worker))
                    .filter(Booking.id == pending.id)
                    .first()
                )
                if pending:
                    return _payload_for_booking(pending, current_user, db)
        except Exception:
            db.rollback()

    booking = (
        db.query(Booking)
        .options(joinedload(Booking.provider), joinedload(Booking.worker))
        .filter((Booking.worker_id == current_user.id) | (Booking.provider_id == current_user.id))
        .order_by(Booking.id.desc())
        .first()
    )
    if not booking:
        return {"show": False, "message": "No active chat."}
    return _payload_for_booking(booking, current_user, db)


@router.post("/rate_worker")
def rate_worker(
    data: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking_id = data.get("booking_id")
    stars      = data.get("stars")
    comment    = (data.get("comment") or "").strip()

    if not booking_id or stars is None:
        return {"success": False, "message": "Missing booking_id or stars."}

    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        return {"success": False, "message": "Booking not found."}

    # only job giver can rate
    if booking.provider_id != current_user.id:
        return {"success": False, "message": "Not authorized."}

    # prevent duplicate for THIS booking
    exists = db.query(Rating).filter(
        Rating.booking_id == booking.id,
        Rating.job_giver_id == booking.provider_id,
    ).first()
    if exists:
        return {"success": False, "message": "You already rated this job."}

    db.add(Rating(
        booking_id=booking.id,  # <-- IMPORTANT
        job_giver_id=booking.provider_id,
        worker_id=booking.worker_id,
        stars=float(stars),
        comment=comment or None,
    ))

    db.commit()
    return {"success": True, "message": "Rating saved."}
