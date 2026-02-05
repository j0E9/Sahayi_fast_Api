# app/services/moderation.py

from datetime import datetime, timedelta
from sqlalchemy import func
from app.models import BookingReport, Booking, WorkerProfile


def recalc_worker_moderation(db, worker_id: int):
    profile = db.query(WorkerProfile).filter_by(user_id=worker_id).first()
    if not profile:
        return

    # 🔒 HARD LOCK — banned users never auto-recover
    if profile.moderation_status == "banned":
        return

    since = datetime.utcnow() - timedelta(days=30)

    # 1️⃣ Sum weighted reports (last 30 days)
    risk = (
        db.query(func.coalesce(func.sum(BookingReport.final_weight), 0.0))
        .filter(
            BookingReport.reported_user_id == worker_id,
            BookingReport.created_at >= since
        )
        .scalar()
    )

    # 2️⃣ Count completed jobs (last 30 days)
    jobs = (
        db.query(Booking)
        .filter(
            Booking.worker_id == worker_id,
            Booking.status == "completed",
            Booking.end_date >= since
        )
        .count()
    )

    jobs = max(jobs, 1)  # avoid division abuse

    # 3️⃣ Normalize risk
    # Grace buffer for high-volume workers
    effective_jobs = max(jobs, 5)
    ratio = risk / effective_jobs



    # 4️⃣ Determine moderation status (balanced thresholds)

    if risk < 5:
        status = "normal"

    elif risk < 10:
        status = "normal"  # or "watch" if you add it later

    elif risk < 18:
        status = "limited"

    elif risk < 30:
        status = "suspended"

    else:
        status = "banned"

    # Optional ratio-based fast escalation
    if risk >= 10 and ratio >= 2.0 and status == "limited":
        status = "suspended"

    # 🔴 STRIKE MEMORY LOGIC

    if status in ("suspended", "banned") and profile.moderation_status not in ("suspended", "banned"):
        profile.strike_count += 1

    MAX_STRIKES = 3
    if profile.strike_count >= MAX_STRIKES:
        status = "banned"

    # Persist
    profile.risk_score_30d = risk
    profile.moderation_status = status
    profile.last_moderation_update = datetime.utcnow()

    db.commit()