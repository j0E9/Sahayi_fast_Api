# app/routers/warnings.py
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from app.database import get_db
from app.models import Booking, WorkerWarning, Notification, User
from fastapi.templating import Jinja2Templates
from app.routers.auth import verify_token  # JWT -> returns payload with "sub"

# NOTE: no prefix -> paths are /issue_warning, /worker_check_warning, /ack_warning
router = APIRouter(tags=["warnings"])
templates = Jinja2Templates(directory="app/templates")


class AckWarningIn(BaseModel):
    booking_id: int
    warning_id: int

class IssueWarningIn(BaseModel):
    booking_id: int

# ---- Auth helper: load current user from JWT ----
def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    payload: dict | None = Depends(lambda: None),  # placeholder so FastAPI doesn't force JWT
) -> User:
    """
    Try JWT from Authorization header first (if verify_token wired via dependency in the route),
    otherwise fall back to session 'user_id' set by SessionMiddleware.
    """
    # 1) Try session fallback
    uid = request.session.get("user_id")
    if uid:
        user = db.get(User, int(uid))
        if user:
            return user

    # 2) If you want to also try JWT here, import and call your verify_token directly:
    # from app.routers.auth import verify_token
    try:
        # This assumes verify_token returns a dict payload with "sub"
        # and reads the Authorization header internally.
        from app.routers.auth import verify_token as _verify_token
        payload = _verify_token()  # will raise if not present/invalid
        uid = int(payload.get("sub"))
        user = db.get(User, uid)
        if user:
            return user
    except Exception:
        pass

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

# ---------------------------
# POST /issue_warning
# ---------------------------
@router.post("/issue_warning", status_code=200)
def issue_warning(
    body: IssueWarningIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    booking = db.get(Booking, body.booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Only the provider (giver) can warn
    if me.id != booking.provider_id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    # Latest warning stage for this booking/worker
    latest: Optional[WorkerWarning] = (
        db.query(WorkerWarning)
        .filter(
            WorkerWarning.booking_id == booking.id,
            WorkerWarning.worker_id == booking.worker_id,
        )
        .order_by(desc(WorkerWarning.created_at))
        .first()
    )
    current_stage = latest.stage if latest else 0
    if current_stage >= 3:
        raise HTTPException(status_code=400, detail="Max warnings reached")

    next_stage = current_stage + 1
    remaining = max(0, 3 - next_stage)

    # Create warning record
    warning = WorkerWarning(
        booking_id=booking.id,
        giver_id=booking.provider_id,
        worker_id=booking.worker_id,
        stage=next_stage,
        remaining=remaining,
    )
    db.add(warning)

    # Notify worker
    db.add(Notification(
        recipient_id=booking.worker_id,
        sender_id=booking.provider_id,
        booking_id=booking.id,
        message=f"⚠️ Warning {next_stage}/3: Your job giver has warned you for delay in arriving.",
        action_type="delay_warning"
    ))

    cancelled = False

    # ---- On 3rd warning: auto-cancel + optional refund ----
    if next_stage == 3:
        cancelled = True

        # capture paid state BEFORE changing status
        was_paid = (booking.status == "Token Paid") or (getattr(booking, "payment_status", "") == "paid")

        # normalize final status casing
        booking.status = "Cancelled"
        booking.extra_timer_requested = False
        booking.extra_timer_stopped = True
        booking.extra_timer_confirmed_stop = True
        booking.main_timer_paused = True
        booking.worker_arrived = False

        # refund (if paid)
        try:
            if was_paid:
                provider = booking.provider
                worker = booking.worker
                total_tokens = int((booking.rate or 0) * (booking.quantity or 0))

                worker_tokens = (worker.tokens if worker and getattr(worker, "tokens", None) is not None else 0)
                refund_amount = min(total_tokens, worker_tokens)

                if refund_amount > 0 and provider and worker:
                    worker.tokens = max(0, worker_tokens - refund_amount)
                    provider.tokens = (getattr(provider, "tokens", 0) or 0) + refund_amount

                    db.add(Notification(
                        recipient_id=provider.id,
                        sender_id=worker.id,
                        booking_id=booking.id,
                        job_id=booking.job_id,
                        message=f"Booking #{booking.id} auto-cancelled due to delay. {refund_amount} tokens refunded to you.",
                        action_type="refund_processed",
                        is_read=False
                    ))
                    db.add(Notification(
                        recipient_id=worker.id,
                        sender_id=provider.id,
                        booking_id=booking.id,
                        job_id=booking.job_id,
                        message=f"Booking #{booking.id} auto-cancelled due to delay. {refund_amount} tokens returned to the job giver.",
                        action_type="refund_processed",
                        is_read=False
                    ))
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Refund failed: {e}")

        # free both sides if model supports it
        if hasattr(booking, "worker") and booking.worker:
            booking.worker.busy = False
            if hasattr(booking.worker, "current_booking_id"):
                booking.worker.current_booking_id = None

        if hasattr(booking, "provider") and booking.provider:
            if hasattr(booking.provider, "busy"):
                booking.provider.busy = False
            if hasattr(booking.provider, "current_booking_id"):
                booking.provider.current_booking_id = None

        # notify both sides about cancellation
        db.add(Notification(
            recipient_id=booking.worker_id,
            sender_id=booking.provider_id,
            booking_id=booking.id,
            message="❌ Booking cancelled due to repeated delays (3 warnings).",
            action_type="booking_cancelled_by_warnings"
        ))
        db.add(Notification(
            recipient_id=booking.provider_id,
            sender_id=booking.provider_id,
            booking_id=booking.id,
            message="✅ You cancelled the booking after 3 warnings.",
            action_type="booking_cancelled_by_warnings"
        ))

    db.commit()
    db.refresh(warning)

    return {
        "success": True,
        "message": "Final warning issued; booking cancelled." if cancelled else "Warning issued",
        "warning": {"id": warning.id, "stage": warning.stage, "remaining": warning.remaining},
        "booking": {"id": booking.id, "status": booking.status},
        "cancelled": cancelled
    }

# ---------------------------
# GET /worker_check_warning?booking_id=...
# ---------------------------
@router.get("/worker_check_warning")
def worker_check_warning(
    booking_id: int = Query(...),
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    booking = db.get(Booking, booking_id)
    if not booking or me.id != booking.worker_id:
        return {"warning": None}

    warning = (
        db.query(WorkerWarning)
        .filter(
            WorkerWarning.booking_id == booking.id,
            WorkerWarning.worker_id == me.id,
            WorkerWarning.acknowledged == False,  # noqa: E712
        )
        .order_by(WorkerWarning.created_at.desc())
        .first()
    )

    if warning:
        return {
            "warning": {
                "id": warning.id,
                "message": getattr(warning, "message", f"⚠️ Warning {warning.stage}/3"),
                "remaining": warning.remaining,
            }
        }

    return {"warning": None}

# ---------------------------
# POST /ack_warning
# ---------------------------
@router.post("/ack_warning")
def ack_warning(
    body: AckWarningIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    warning = db.get(WorkerWarning, body.warning_id)
    if not warning or me.id != warning.worker_id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    warning.acknowledged = True
    db.commit()
    return {"success": True}
