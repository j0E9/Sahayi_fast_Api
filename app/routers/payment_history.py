# app/routers/payment_history.py
from datetime import datetime
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Booking, User
from app.routers.auth import verify_token  # decodes JWT and returns payload
from app.utils.payments import hours_for_booking, payment_for_booking

# Use Jinja2 directly to render an inline template string
from jinja2 import Environment, BaseLoader, select_autoescape

router = APIRouter(prefix="", tags=["payments"])

# ---- Auth helper: load current user from JWT ----
def get_current_user(
    payload: dict = Depends(verify_token),
    db: Session = Depends(get_db),
) -> User:
    try:
        uid = int(payload.get("sub"))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    user = db.get(User, uid)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

# ---- Helper: choose the date to display ----
def _when_for_booking(b: Booking) -> Optional[datetime]:
    # same semantics as your Flask code
    if getattr(b, "otp_verified_time", None):
        return b.otp_verified_time
    if getattr(b, "expires_at", None):
        return b.expires_at
    if getattr(b, "job", None) and hasattr(b.job, "timestamp") and b.job.timestamp:
        return b.job.timestamp
    return None

HTML_TMPL = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>History — Sahayi</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { background: #f7f9fc; }
    .card { border-radius: 14px; box-shadow: 0 6px 16px rgba(0,0,0,0.06); }
    th { white-space: nowrap; }
    .pill { padding: 2px 8px; border-radius: 999px; font-size: 12px; }
    .pill-pending { background:#fff3cd; color:#8a6d3b; }
    .pill-accepted { background:#d1e7dd; color:#0f5132; }
    .pill-declined, .pill-cancelled { background:#f8d7da; color:#842029; }
  </style>
</head>
<body class="p-3 p-md-4">
  <div class="container-xxl">
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h2 class="mb-0">History</h2>
      <a href="/welcome" class="btn btn-sm btn-outline-secondary">← Back</a>
    </div>

    <!-- As Job Giver -->
    <div class="card mb-4">
      <div class="card-body">
        <h5 class="card-title mb-3">As Job Giver</h5>
        {% if giver_rows %}
        <div class="table-responsive">
          <table class="table align-middle">
            <thead>
              <tr>
                <th>Booking</th>
                <th>Worker</th>
                <th>Worker ID</th>
                <th>Job</th>
                <th>Rate</th>
                <th>Rate Type</th>
                <th>Hours</th>
                <th>Payment</th>
                <th>Status</th>
                <th>Date</th>
              </tr>
            </thead>
            <tbody>
              {% for r in giver_rows %}
              <tr>
                <td>#{{ r.booking_id }}</td>
                <td>{{ r.worker_name }}</td>
                <td>{{ r.worker_id }}</td>
                <td>{{ r.job_title }}</td>
                <td>₹{{ "%.2f"|format(r.rate or 0) }}</td>
                <td>{{ r.rate_type }}</td>
                <td>{{ "%.2f"|format(r.hours) }}</td>
                <td><strong>₹{{ "%.2f"|format(r.payment) }}</strong></td>
                <td>
                  {% set st = (r.status or '').lower() %}
                  <span class="pill {% if st=='pending' %}pill-pending{% elif st in ['accepted','completed'] %}pill-accepted{% else %}pill-cancelled{% endif %}">
                    {{ r.status }}
                  </span>
                </td>
                <td>{{ r.date }}</td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
        {% else %}
          <div class="text-muted">No bookings as a job giver yet.</div>
        {% endif %}
      </div>
    </div>

    <!-- As Worker -->
    <div class="card mb-4">
      <div class="card-body">
        <h5 class="card-title mb-3">As Worker</h5>
        {% if worker_rows %}
        <div class="table-responsive">
          <table class="table align-middle">
            <thead>
              <tr>
                <th>Booking</th>
                <th>Partner (Giver)</th>
                <th>Partner ID</th>
                <th>Job</th>
                <th>Rate</th>
                <th>Rate Type</th>
                <th>Hours</th>
                <th>Payment</th>
                <th>Status</th>
                <th>Date</th>
              </tr>
            </thead>
            <tbody>
              {% for r in worker_rows %}
              <tr>
                <td>#{{ r.booking_id }}</td>
                <td>{{ r.partner_name }}</td>
                <td>{{ r.partner_id }}</td>
                <td>{{ r.job_title }}</td>
                <td>₹{{ "%.2f"|format(r.rate or 0) }}</td>
                <td>{{ r.rate_type }}</td>
                <td>{{ "%.2f"|format(r.hours) }}</td>
                <td><strong>₹{{ "%.2f"|format(r.payment) }}</strong></td>
                <td>
                  {% set st = (r.status or '').lower() %}
                  <span class="pill {% if st=='pending' %}pill-pending{% elif st in ['accepted','completed'] %}pill-accepted{% else %}pill-cancelled{% endif %}">
                    {{ r.status }}
                  </span>
                </td>
                <td>{{ r.date }}</td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
        {% else %}
          <div class="text-muted">No bookings as a worker yet.</div>
        {% endif %}
      </div>
    </div>
  </div>
</body>
</html>
"""

env = Environment(
    loader=BaseLoader(),
    autoescape=select_autoescape(["html", "xml"]),
)

@router.get("/payment_history", response_class=HTMLResponse)
def payment_history(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    # As Job Giver
    giver_bookings: List[Booking] = (
        db.query(Booking)
        .options(joinedload(Booking.worker), joinedload(Booking.provider), joinedload(Booking.job))
        .filter(Booking.provider_id == me.id)
        .order_by(Booking.id.desc())
        .all()
    )

    # As Worker
    worker_bookings: List[Booking] = (
        db.query(Booking)
        .options(joinedload(Booking.worker), joinedload(Booking.provider), joinedload(Booking.job))
        .filter(Booking.worker_id == me.id)
        .order_by(Booking.id.desc())
        .all()
    )

    def row_for_giver(b: Booking) -> Dict[str, Any]:
        worker = b.worker.name if getattr(b, "worker", None) else "—"
        worker_id = b.worker.id if getattr(b, "worker", None) else "—"
        hours = hours_for_booking(b)
        payment = payment_for_booking(b)
        when = _when_for_booking(b)
        when_str = when.strftime("%Y-%m-%d %H:%M") if when else "—"
        return {
            "booking_id": b.id,
            "token": getattr(b, "token", None),
            "worker_name": worker,
            "worker_id": worker_id,
            "job_title": b.job.title if getattr(b, "job", None) else "—",
            "rate": b.rate,
            "rate_type": b.rate_type or "—",
            "hours": hours,
            "payment": payment,
            "status": b.status,
            "date": when_str,
        }

    def row_for_worker(b: Booking) -> Dict[str, Any]:
        giver = b.provider.name if getattr(b, "provider", None) else "—"
        giver_id = b.provider.id if getattr(b, "provider", None) else "—"
        hours = hours_for_booking(b)
        payment = payment_for_booking(b)
        when = _when_for_booking(b)
        when_str = when.strftime("%Y-%m-%d %H:%M") if when else "—"
        return {
            "booking_id": b.id,
            "token": getattr(b, "token", None),
            "partner_name": giver,
            "partner_id": giver_id,
            "job_title": b.job.title if getattr(b, "job", None) else "—",
            "rate": b.rate,
            "rate_type": b.rate_type or "—",
            "hours": hours,
            "payment": payment,
            "status": b.status,
            "date": when_str,
        }

    giver_rows = [row_for_giver(b) for b in giver_bookings]
    worker_rows = [row_for_worker(b) for b in worker_bookings]

    template = env.from_string(HTML_TMPL)
    html = template.render(giver_rows=giver_rows, worker_rows=worker_rows)
    return HTMLResponse(content=html, status_code=200)
