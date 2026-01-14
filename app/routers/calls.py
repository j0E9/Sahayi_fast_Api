# app/routers/calls.py
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from app.security.tokens import decode_worker_link
from app.database import get_db
from app.models import User, WorkerProfile
from app.settings import settings  # ensure you expose TWILIO_* & SECRET_KEY here
from sqlalchemy.orm import joinedload
from app.models import Booking

router = APIRouter(tags=["calls"])

# ----- JIT Twilio client -----
def get_twilio() -> Client:
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_PHONE):
        raise RuntimeError("Twilio is not fully configured")
    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

# ----- Trust API style session auth -----
def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = db.get(User, int(uid))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user

# ----- Phone utils -----
def normalize_indian_number(number: str) -> str:
    if not number:
        raise ValueError("Empty phone number")
    s = number.strip()
    # keep + then digits, remove other symbols
    digits = re.sub(r"[^\d+]", "", s)

    if digits.startswith("+"):
        cleaned = "+" + re.sub(r"\D", "", digits[1:])
        # E.164 length check (up to 15 digits total)
        if 8 <= len(re.sub(r"\D", "", cleaned)) <= 15:
            return cleaned
        raise ValueError("Invalid E.164 number")

    digits = re.sub(r"\D", "", digits)
    if len(digits) == 10:
        return "+91" + digits
    if len(digits) == 11 and digits.startswith("0"):
        return "+91" + digits[1:]
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    raise ValueError("Unrecognizable phone format")

# ======================================================================
# POST /call_worker/{worker_id}
# ======================================================================
@router.post("/call_worker/{token}")
def call_worker(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):

    twilio = get_twilio()

    # Your Flask used WorkerProfile.query.get_or_404(worker_id).
    # In your schema WorkerProfile has user_id -> User.id, so fetch by user_id.
    try:
        payload = decode_worker_link(token)
    except Exception:
        return JSONResponse(
            {"status": "error", "message": "Invalid or expired worker link"},
            status_code=400,
        )

    worker_id = payload["w"]

    worker = db.get(User, worker_id)
    if not worker or not worker.phone:
        return JSONResponse(
            {"status": "error", "message": "Worker not found or has no phone number."},
            status_code=404,
        )

    if not getattr(current_user, "phone", None):
        return JSONResponse({"status": "error", "message": "Your phone number is not set."}, status_code=400)

    try:
        worker_phone = normalize_indian_number(worker.phone)
        caller_phone = normalize_indian_number(current_user.phone)
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid phone format."}, status_code=400)

    if worker_phone == caller_phone:
        return JSONResponse({"status": "error", "message": "Cannot call your own number."}, status_code=400)

    twiml = f"""
<Response>
  <Say voice="alice">You have an enquiry from Sahayi platform. Connecting the caller now.</Say>
  <Dial callerId="{settings.TWILIO_PHONE}">
    {caller_phone}
  </Dial>
</Response>
""".strip()

    try:
        call = twilio.calls.create(to=worker_phone, from_=settings.TWILIO_PHONE, twiml=twiml)
        return JSONResponse(
            {"status": "success", "message": "Call initiated", "twilio_sid": getattr(call, "sid", None)}, status_code=200
        )
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"Failed to initiate call: {e}"}, status_code=500)

# ======================================================================
# GET /call_status/{sid}
# ======================================================================
@router.get("/call_status/{sid}")
def call_status(
    sid: str,
    current_user: User = Depends(get_current_user),
):
    twilio = get_twilio()
    try:
        call = twilio.calls(sid).fetch()
        return {
            "status": call.status,
            "start_time": getattr(call, "start_time", None),
            "end_time": getattr(call, "end_time", None),
        }
    except TwilioRestException as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

# ======================================================================
# POST /call_cancel/{sid}
# ======================================================================
@router.post("/call_cancel/{sid}")
def call_cancel(
    sid: str,
    current_user: User = Depends(get_current_user),
):
    twilio = get_twilio()
    try:
        # Twilio "hang up": update status to 'completed'
        call = twilio.calls(sid).update(status="completed")
        return {"status": "success", "message": "Hangup requested", "new_status": call.status}
    except TwilioRestException as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@router.post("/initiate_call/{booking_id}")
def initiate_call(
    booking_id: int,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    twilio = get_twilio()

    # Load both sides
    booking = (
        db.query(Booking)
        .options(joinedload(Booking.provider), joinedload(Booking.worker))
        .get(booking_id)
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if me.id not in {booking.provider_id, booking.worker_id}:
        raise HTTPException(status_code=403, detail="Forbidden")

    # Call the initiator first so *their* phone rings immediately
    if me.id == booking.provider_id:
        caller = booking.provider
        target = booking.worker
    else:
        caller = booking.worker
        target = booking.provider

    if not caller or not target or not caller.phone or not target.phone:
        raise HTTPException(status_code=400, detail="Missing phone numbers")

    try:
        caller_num = normalize_indian_number(caller.phone)
        target_num = normalize_indian_number(target.phone)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid phone format")

    twiml = f"""
<Response>
  <Say voice="alice">Connecting you to your job partner.</Say>
  <Dial callerId="{settings.TWILIO_PHONE}">{target_num}</Dial>
</Response>
""".strip()

    try:
        call = twilio.calls.create(
            to=caller_num,                 # ring the initiator first
            from_=settings.TWILIO_PHONE,   # your Twilio number / verified caller ID
            twiml=twiml
        )
        return {"status": "success", "twilio_sid": getattr(call, "sid", None)}
    except TwilioRestException as e:
        # surface real cause to the UI
        raise HTTPException(status_code=502, detail=f"Twilio error {e.code}: {e.msg}")