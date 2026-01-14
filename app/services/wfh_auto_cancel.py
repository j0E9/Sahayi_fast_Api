# app/services/wfh_auto_cancel.py
from sqlalchemy.orm import Session
from app.models import Booking
from app.utils.IST_Time import ist_now

ACTIVE_STATUSES = ("WFH_CONFIRMED", "WFH_IN_PROGRESS")


def cancel_if_expired(db: Session, booking: Booking) -> bool:
    """
    Automatically cancels a WFH booking AFTER deadline and refunds escrow.

    Returns:
        True  -> cancellation happened
        False -> no action taken
    """

    # Already final → do nothing
    if booking.status in ("WFH_CANCELLED", "WFH_COMPLETED"):
        return False

    # Always enforce cancel window first
    enforce_cancel_window(booking)

    deadline = booking.deadline
    now = ist_now()

    if (
        not deadline
        or now < deadline
        or booking.status not in ACTIVE_STATUSES
    ):
        db.commit()  # persist cancel_window_closed if changed
        return False

    refunded = False

    if booking.escrow_amount and not booking.escrow_released:
        from app.services.wfh_refund import refund_wfh_escrow

        refunded = refund_wfh_escrow(
            db=db,
            booking=booking,
            reason="auto_cancel",
        )

    db.commit()
    return refunded


def enforce_cancel_window(booking: Booking) -> None:
    """
    Controls ONLY the early-cancellation window (1/16th of total duration).

    - Runs only after payment
    - Never cancels automatically
    - Never refunds
    - Only closes the early-cancel window
    """

    if booking.cancel_window_closed:
        return

    if not booking.payment_completed:
        return

    if not booking.started_at or not booking.deadline:
        return

    # Safety: invalid time range
    if booking.deadline <= booking.started_at:
        booking.cancel_window_closed = True
        return

    total_duration = booking.deadline - booking.started_at
    cancel_until = booking.started_at + (total_duration / 16)

    now = ist_now()

    if now > cancel_until:
        booking.cancel_window_closed = True
