from datetime import datetime, timedelta
from sqlalchemy import func
from app.models import Booking, BookingReport, User


def calculate_reporter_trust(db, reporter_id: int) -> float:
    user = db.get(User, reporter_id)
    if not user:
        return 0.3  # extreme fallback

    trust = 1.0

    # 1️⃣ Account age
    if user.created_at:
        days = (datetime.utcnow() - user.created_at).days
        if days < 7:
            trust *= 0.4
        elif days < 30:
            trust *= 0.7
        elif days > 180:
            trust *= 1.2

    # 2️⃣ Completed jobs as job giver
    completed_jobs = (
        db.query(Booking)
        .filter(
            Booking.provider_id == reporter_id,
            Booking.status == "completed"
        )
        .count()
    )

    if completed_jobs >= 20:
        trust *= 1.3
    elif completed_jobs >= 5:
        trust *= 1.1
    elif completed_jobs == 0:
        trust *= 0.6

    # 3️⃣ Report spam protection (last 30 days)
    recent_reports = (
        db.query(BookingReport)
        .filter(
            BookingReport.reporter_id == reporter_id,
            BookingReport.created_at >= datetime.utcnow() - timedelta(days=30)
        )
        .count()
    )

    if recent_reports >= 10:
        trust *= 0.5
    elif recent_reports >= 5:
        trust *= 0.7

    # Clamp hard limits
    return max(0.3, min(trust, 1.5))
