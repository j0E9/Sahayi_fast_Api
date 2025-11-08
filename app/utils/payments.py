# app/utils/payments.py
from datetime import datetime

def hours_for_booking(b) -> float:
    """
    Best-effort total hours:
    - Uses scheduled job_duration_minutes.
    - If extra timer started, includes elapsed time since start.
    - If later you add extra_timer_stopped_at, use that instead of utcnow().
    """
    base = (getattr(b, "job_duration_minutes", 0) or 0) / 60.0
    extra = 0.0
    if getattr(b, "extra_timer_started_at", None):
        extra = max(
            0.0,
            (datetime.utcnow() - b.extra_timer_started_at).total_seconds() / 3600.0,
        )
    return round(base + extra, 2)


def payment_for_booking(b) -> float:
    """
    Heuristic payment calculator:
    - hourly/per_hour/hour → rate * hours
    - fixed/total/package → rate
    - unit/per_unit/quantity → rate * completed_quantity or quantity
    - fallback → rate * hours
    """
    rate = getattr(b, "rate", 0.0) or 0.0
    rt = (getattr(b, "rate_type", "") or "").lower()
    hours = hours_for_booking(b)

    if rt in {"hour", "hourly", "per_hour"}:
        return round(rate * hours, 2)
    elif rt in {"fixed", "total", "package"}:
        return round(rate, 2)
    elif rt in {"unit", "per_unit", "quantity"}:
        qty = getattr(b, "completed_quantity", None) or getattr(b, "quantity", 0.0) or 0.0
        return round(rate * qty, 2)
    else:
        return round(rate * hours, 2)
