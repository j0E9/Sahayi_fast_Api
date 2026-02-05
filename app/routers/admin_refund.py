from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Booking
from app.services.onsite_escrow import refund_onsite_escrow

router = APIRouter(tags=["admin-refund"])

@router.post("/admin/refund_onsite/{booking_id}")
def admin_refund_onsite(booking_id: int, db: Session = Depends(get_db)):
    booking = db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    ok = refund_onsite_escrow(db=db, booking=booking, reason="manual_fix_admin")
    db.commit()
    return {"success": ok, "booking_id": booking_id}
