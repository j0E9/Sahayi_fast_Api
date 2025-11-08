# app/routers/booking_actions.py
from __future__ import annotations

from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.database import get_db
from app.models import Booking, Notification, User

router = APIRouter(tags=["booking-actions"])

# --- Trust-API style session auth you’ve been using ---
def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = db.get(User, int(uid))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


@router.post("/auto_reject_booking/{booking_id}")
def auto_reject_booking(
    booking_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if booking.status != "Pending":
        return JSONResponse({"status": "already_handled"})

    booking.status = "Rejected"
    if booking.provider:
        booking.provider.busy = False
    db.flush()

    # notify worker
    notif_worker = Notification(
        recipient_id=booking.worker_id,
        sender_id=booking.provider_id,
        booking_id=booking.id,
        job_id=booking.job_id,
        message="⏳ You did not respond in time. Booking auto-rejected.",
        action_type="auto_rejected",
        is_read=False,
    )
    # notify provider
    worker_name = booking.worker.name if booking.worker else "Worker"
    notif_provider = Notification(
        recipient_id=booking.provider_id,
        sender_id=booking.worker_id,
        booking_id=booking.id,
        job_id=booking.job_id,
        message=f"❌ {worker_name} did not respond in time. Booking cancelled.",
        action_type="auto_rejected",
        is_read=False,
    )

    db.add_all([notif_worker, notif_provider])
    db.commit()
    return JSONResponse({"status": "auto_rejected"})


class RespondBody(BaseModel):
    response: str

@router.post("/respond_notification/{notification_id}")
def respond_notification(
    notification_id: int,
    body: RespondBody,                         # <-- FastAPI parses JSON into this
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    response_text = (body.response or "").capitalize()

    notif = db.get(Notification, notification_id)
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")

    if notif.recipient_id != current_user.id:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)

    if response_text not in ("Accept", "Reject"):
        return JSONResponse({"error": "Invalid response"}, status_code=400)

    booking = db.get(Booking, notif.booking_id) if notif.booking_id else None
    if not booking or booking.status != "Pending":
        return JSONResponse({"error": "Invalid or already handled"}, status_code=404)

    # ===== EXPIRED → auto reject =====
    if booking.expires_at <= datetime.utcnow():
        booking.status = "Rejected"
        if booking.provider:
            booking.provider.busy = False

        db.delete(notif)

        # notify giver
        if notif.sender_id:
            db.add(Notification(
                recipient_id=notif.sender_id,
                sender_id=current_user.id,
                booking_id=booking.id,
                job_id=booking.job_id,
                message=f"❌ {current_user.name} did not respond to your job request in time. Booking cancelled.",
                action_type="auto_rejected",
            ))

        # worker final note
        db.add(Notification(
            recipient_id=current_user.id,
            sender_id=notif.sender_id,
            booking_id=booking.id,
            job_id=booking.job_id,
            message="⏳ You did not respond in time. Booking auto-rejected.",
            action_type="auto_rejected",
        ))

        db.commit()
        return JSONResponse({"status": "auto_rejected"})

    # ===== ACCEPT =====
    if response_text == "Accept":
        booking.status = "Accepted"
        booking.expires_at = datetime.utcnow() + timedelta(minutes=5)
        if booking.worker:
            booking.worker.busy = True

        db.delete(notif)

        # duration text
        if (booking.rate_type or "").lower() == "per hour":
            hrs = int(booking.quantity or 0)
            mins = int(round(((booking.quantity or 0) - hrs) * 60))
            duration_str = f"{hrs} hr{'s' if hrs != 1 else ''} {mins} min{'s' if mins != 1 else ''}"
        elif (booking.rate_type or "").lower() in ("custom", "per custom"):
            duration_str = "custom"
        else:
            unit = (booking.rate_type or "unit").replace("per ", "")
            plural = "s" if (booking.quantity or 0) > 1 else ""
            duration_str = f"{booking.quantity} {unit}{plural}"

        # notify giver to pay
        if notif.sender_id:
            db.add(Notification(
                recipient_id=notif.sender_id,
                sender_id=current_user.id,
                job_id=notif.job_id,
                booking_id=booking.id,
                message=(f"{current_user.name} has accepted your job request for <b>{duration_str}</b>. "
                         f"Please pay the token within 5 minutes."),
                action_type="payment_required",
            ))

        # worker waiting status
        db.add(Notification(
            recipient_id=current_user.id,
            sender_id=notif.sender_id,
            job_id=notif.job_id,
            booking_id=booking.id,
            message=f"Waiting for {notif.sender.name if notif.sender else 'the job giver'} to pay the token.",
            action_type="waiting_payment",
        ))

        # auto-reject other pending bookings for this worker
        others = (
            db.query(Booking)
              .filter(
                  Booking.worker_id == current_user.id,
                  Booking.id != booking.id,
                  Booking.status == "Pending",
              )
              .all()
        )
        for b in others:
            b.status = "Rejected"

            db.add(Notification(
                recipient_id=current_user.id,
                sender_id=b.provider_id,
                job_id=b.job_id,
                booking_id=b.id,
                message=f"You rejected {b.provider.name}'s job request (auto-rejected because you accepted another job).",
                action_type="rejected",
            ))
            db.add(Notification(
                recipient_id=b.provider_id,
                sender_id=current_user.id,
                job_id=b.job_id,
                booking_id=b.id,
                message=f"{current_user.name} has rejected your job request (auto-rejected because they accepted another job).",
                action_type="rejected",
            ))
            if b.provider:
                b.provider.busy = False

        db.commit()
        return JSONResponse({"redirect": f"/waiting_for_payment/{booking.token}"})

    # ===== REJECT =====
    booking.status = "Rejected"
    if booking.provider:
        booking.provider.busy = False

    db.delete(notif)

    worker_msg = (
        f"You rejected {notif.sender.name}'s job request."
        if getattr(notif, "sender", None) else
        "You rejected a job request."
    )
    db.add(Notification(
        recipient_id=current_user.id,
        sender_id=notif.sender_id,
        job_id=notif.job_id,
        booking_id=booking.id,
        message=worker_msg,
        action_type="rejected",
    ))

    if notif.sender_id:
        db.add(Notification(
            recipient_id=notif.sender_id,
            sender_id=current_user.id,
            job_id=notif.job_id,
            booking_id=booking.id,
            message=f"{current_user.name} has rejected your job request.",
            action_type="rejected",
        ))

    db.commit()
    return JSONResponse({"status": "rejected"})