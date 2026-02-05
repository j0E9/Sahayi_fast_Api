from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from app.database import get_db
# BookingReport used only to check existence (not moderation logic)
from app.models import Booking, User, Skill,BookingReport
from fastapi.templating import Jinja2Templates
from app.security.auth import get_current_user


templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["payment-history"])


def hours_for_booking(b: Booking) -> float:
    if b.job_duration_minutes:
        return round(b.job_duration_minutes / 60, 2)
    if b.started_at and b.end_date:
        return round((b.end_date - b.started_at).total_seconds() / 3600, 2)
    return 0.0


def payment_for_booking(b: Booking) -> float:
    if b.escrow_amount:
        return float(b.escrow_amount)
    if b.rate and b.quantity:
        return float(b.rate * b.quantity)
    return 0.0


def booking_date(b: Booking) -> Optional[datetime]:
    return b.end_date or b.started_at or b.start_date or b.expires_at


@router.get("/payment_history", response_class=HTMLResponse)
def payment_history(
    request: Request,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    giver_bookings = (
        db.query(Booking)
        .options(
            joinedload(Booking.worker).joinedload(User.worker_profile),
            joinedload(Booking.job)
        )
        .filter(
            Booking.provider_id == me.id,
            Booking.payment_completed.is_(True)
        )
        .order_by(Booking.id.desc())
        .all()
    )

    worker_bookings = (
        db.query(Booking)
        .options(
            joinedload(Booking.provider),
            joinedload(Booking.job)
        )
        .filter(
            Booking.worker_id == me.id,
            Booking.payment_completed.is_(True)
        )
        .order_by(Booking.id.desc())
        .all()
    )
    reported_booking_ids = {
        r.booking_id
        for r in db.query(BookingReport.booking_id)
        .filter(BookingReport.reporter_id == me.id)
        .all()
    }

    giver_rows = []
    for b in giver_bookings:
        dt = booking_date(b)

        # Resolve LIVE skill (if still exists)
        skill_id = None
        if b.worker and b.skill_name:
            skill = (
                db.query(Skill)
                .filter(
                    Skill.user_id == b.worker.id,
                    func.lower(Skill.name) == func.lower(b.skill_name)
                )
                .first()
            )
            if skill:
                skill_id = skill.id



        giver_rows.append({
            "booking_id": b.id,
            "worker_name": b.worker.name if b.worker else "—",
            "worker_code": (
                b.worker.worker_profile.worker_code
                if b.worker and b.worker.worker_profile
                else "—"
            ),
            "worker_user_id": b.worker.id if b.worker else None,

            # history-safe
            "skill_name": b.skill_name or "—",
            "skill_id": skill_id,

            "hours": hours_for_booking(b),
            "payment": payment_for_booking(b),
            "status": b.status,
            "date": dt.strftime("%Y-%m-%d %H:%M") if dt else "—",

            # 🔑 THIS WAS MISSING
            "can_report": b.id not in reported_booking_ids,

        })

    worker_rows = []
    for b in worker_bookings:
        dt = booking_date(b)
        worker_rows.append({
            "booking_id": b.id,
            "partner_name": b.provider.name if b.provider else "—",
            "job_title": b.job.title if b.job else "—",
            "hours": hours_for_booking(b),
            "payment": payment_for_booking(b),
            "status": b.status,
            "date": dt.strftime("%Y-%m-%d %H:%M") if dt else "—",
        })

    return templates.TemplateResponse(
        "payment_history.html",
        {
            "request": request,
            "giver_rows": giver_rows,
            "worker_rows": worker_rows,
        }
    )
