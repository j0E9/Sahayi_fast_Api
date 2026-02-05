# fastapi_app/app/routers/report.py

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Booking, BookingReport
from app.services.moderation import recalc_worker_moderation
import uuid
import os
from app.services.reporter_trust import calculate_reporter_trust
from datetime import datetime
router = APIRouter(tags=["report"])


@router.post("/report-worker")
async def report_worker(
    request: Request,
    booking_id: int = Form(...),
    reason: str = Form(...),
    description: str | None = Form(None),
    proof: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    reporter_id = request.session.get("user_id")
    if not reporter_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    booking = db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Only job giver can report
    if booking.provider_id != reporter_id:
        raise HTTPException(status_code=403, detail="Not allowed")

    # Prevent duplicate reports
    exists = (
        db.query(BookingReport)
        .filter_by(booking_id=booking_id, reporter_id=reporter_id)
        .first()
    )
    if exists:
        raise HTTPException(status_code=400, detail="Already reported")

    # -------- Upload proof --------
    # -------- Structured proof storage --------
    proof_url = None

    if proof:
        ext = proof.filename.rsplit(".", 1)[-1].lower()
        safe_ext = ext if ext in ["jpg", "jpeg", "png", "mp4", "mov"] else "bin"

        base_dir = os.path.join(
            "uploads",
            "reports",
            f"worker_{booking.worker_id}",
            f"booking_{booking.id}",
            f"reporter_{reporter_id}",
        )

        os.makedirs(base_dir, exist_ok=True)

        filename = f"proof_{uuid.uuid4().hex}.{safe_ext}"
        path = os.path.join(base_dir, filename)

        with open(path, "wb") as f:
            f.write(await proof.read())

        proof_url = path

        import json

        meta = {
            "worker_id": booking.worker_id,
            "booking_id": booking.id,
            "reporter_id": reporter_id,
            "reason": reason,
            "uploaded_at": datetime.utcnow().isoformat(),
            "file": filename,
        }

        with open(os.path.join(base_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

    # -------- Weight calculation --------
    SEVERITY = {
        "Fraud / scam": 5,
        "Did not arrive": 3,
        "Arrived late": 2,
        "Unprofessional behavior": 2,
        "Poor quality work": 1,
        "Other": 1,
    }

    severity_weight = SEVERITY.get(reason, 1)

    # TODO: replace with trust score later
    reporter_weight = calculate_reporter_trust(db, reporter_id)

    final_weight = severity_weight * reporter_weight
    final_weight = min(final_weight, 7.0)

    report = BookingReport(
        booking_id=booking_id,
        reporter_id=reporter_id,
        reported_user_id=booking.worker_id,
        reason=reason,
        description=description,
        proof_url=proof_url,
        severity_weight=severity_weight,
        reporter_weight=reporter_weight,
        final_weight=final_weight,
    )

    db.add(report)
    db.commit()

    # 🔥 Centralized moderation recalculation
    recalc_worker_moderation(db, booking.worker_id)



    return JSONResponse({
        "success": True
    })

