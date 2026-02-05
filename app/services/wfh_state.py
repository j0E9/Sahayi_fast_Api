from app.models import Booking, WFHDispute, WFHProjectUpdate

def get_wfh_state(db, booking: Booking) -> str:
    if booking.booking_type != "wfh":
        return "not_wfh"

    dispute = db.query(WFHDispute).filter_by(
        booking_id=booking.id
    ).first()
    if dispute:
        return "wfh_disputed"

    if booking.escrow_released:
        return "wfh_completed"

    if booking.payment_completed:
        revision = db.query(WFHProjectUpdate).filter(
            WFHProjectUpdate.booking_id == booking.id,
            WFHProjectUpdate.status == "revision_requested"
        ).first()
        if revision:
            return "wfh_revision_requested"

        review = db.query(WFHProjectUpdate).filter(
            WFHProjectUpdate.booking_id == booking.id,
            WFHProjectUpdate.status == "approval_requested"
        ).first()
        if review:
            return "wfh_review_pending"

        return "wfh_in_progress"

    return "wfh_pending_payment"
