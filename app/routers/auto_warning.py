# app/services/auto_warning.py
from datetime import datetime, timedelta, timezone
from app.models import Booking, Notification
from app.settings import settings

def _utcnow():
    return datetime.now(timezone.utc)

def apply_auto_warning_and_cancel(db, booking: Booking) -> None:
    """
    Move booking through warning stages & auto-cancel if needed.
    Safe to call on every poll / API hit.
    """
    now = _utcnow()

    # stop if already finished
    if booking.status in ("completed", "cancelled"):
        return

    # stop if arrival confirmed or OTP verified flags exist
    if getattr(booking, "arrival_confirmed", False) or getattr(booking, "otp_verified", False):
        return

    if not booking.drive_started_at or not booking.drive_seconds:
        return  # nothing to schedule against

    drive_eta = booking.drive_started_at + timedelta(seconds=booking.drive_seconds)

    # thresholds
    t1 = drive_eta + timedelta(minutes=10)
    t2 = t1 + timedelta(minutes=20)
    t3 = t2 + timedelta(minutes=10)
    t_cancel = t3 + timedelta(minutes=5)

    stage = booking.warn_stage or 0

    def _notify_worker(message: str, booking: Booking):
        worker = booking.worker
        provider = booking.provider
        if not worker:
            return
        db.add(Notification(
            recipient_id=worker.id,
            sender_id=provider.id if provider else None,
            booking_id=booking.id,
            message=message,
            action_type="delay_warning",
            is_read=False,
        ))

    # stage 0 -> 1
    if stage < 1 and now >= t1:
        _notify_worker("⚠ You are late. Please reach the job location as soon as possible.", booking)
        booking.warn_stage = 1
        booking.warn_last_at = now
        db.add(booking)
        db.commit()
        db.refresh(booking)
        stage = 1

    # stage 1 -> 2
    if stage < 2 and now >= t2:
        _notify_worker("⚠ Second reminder: You are still late. Please reach immediately.", booking)
        booking.warn_stage = 2
        booking.warn_last_at = now
        db.add(booking)
        db.commit()
        db.refresh(booking)
        stage = 2

    # stage 2 -> 3 (final warning)
    if stage < 3 and now >= t3:
        _notify_worker("⛔ Final warning: Booking will be cancelled in 5 minutes if you do not arrive.", booking)
        booking.warn_stage = 3
        booking.warn_last_at = now
        db.add(booking)
        db.commit()
        db.refresh(booking)
        stage = 3

    # auto cancel after final warning + 5 minutes
    if stage >= 3 and now >= t_cancel and booking.status != "cancelled":
        booking.status = "cancelled"
        booking.auto_cancelled = True
        db.add(booking)

        # notify both sides
        provider = booking.provider
        worker = booking.worker

        if provider:
            db.add(Notification(
                recipient_id=provider.id,
                sender_id=None,
                booking_id=booking.id,
                message="❌ Booking cancelled automatically because the Sahayi did not arrive on time.",
                action_type="booking_cancelled_auto",
                is_read=False,
            ))
        if worker:
            db.add(Notification(
                recipient_id=worker.id,
                sender_id=None,
                booking_id=booking.id,
                message="❌ Booking cancelled automatically because you did not reach the job location in time.",
                action_type="booking_cancelled_auto",
                is_read=False,
            ))

        db.commit()
