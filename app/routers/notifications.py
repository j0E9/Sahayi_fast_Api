# app/routers/notifications.py
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from datetime import datetime
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Notification, Booking, User

router = APIRouter(prefix="", tags=["notifications"])

# --- Trust API-style session auth ---
def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = db.query(User).get(int(uid))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user

# --- Response schema (optional but nice) ---
class JobAlertOut(BaseModel):
    has_new_request: bool
    sender: str | None = None

@router.get("/check_job_alert", response_model=JobAlertOut)
def check_job_alert(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Pick the best ordering column your model has
    order_col = getattr(Notification, "timestamp", None) or \
                getattr(Notification, "created_at", None) or \
                Notification.id

    note = (
        db.query(Notification)
        .filter(
            Notification.recipient_id == current_user.id,
            Notification.is_read == False,            # noqa: E712
            Notification.action_type == "booking_request",
        )
        .order_by(order_col.desc())
        .first()
    )

    if note:
        sender_name = (
            getattr(getattr(note, "sender", None), "name", None) or "someone"
        )
        return JobAlertOut(has_new_request=True, sender=sender_name)

    return JobAlertOut(has_new_request=False, sender=None)
# REUSE your existing `router = APIRouter(...)` – do NOT redeclare it.

# ---- session-based auth helper (rename if you already have one) ----
def get_current_user_for_notifications(
    request: Request, db: Session = Depends(get_db)
) -> User:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = db.get(User, int(uid))  # SQLAlchemy 2.0 style
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user

# If you already have /notifications, change this path to /notifications/page
@router.get("/notifications", response_class=HTMLResponse)
def notifications_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_for_notifications),
):
    notes = (
        db.query(Notification)
        .filter(Notification.recipient_id == current_user.id)
        .order_by(Notification.timestamp.desc())
        .all()
    )

    html_notifications = []

    for n in notes:
        if not n.is_read:
            n.is_read = True  # mark read

        block = [
            '<div class="card mb-3">',
            '  <div class="card-body">',
            f'    <p class="card-text">{n.message}</p>',
        ]

        if n.action_type == "booking_request":
            booking = db.get(Booking, n.booking_id) if n.booking_id else None
            if booking and booking.status == "Pending":
                remaining = max(0, int((booking.expires_at - datetime.utcnow()).total_seconds()))
                block.append(
                    f"""
                    <div class="d-flex gap-2 mt-2">
                        <button onclick="respondNotification({n.id}, 'Accept')" class="btn btn-success btn-sm">Accept</button>
                        <button onclick="respondNotification({n.id}, 'Reject')" class="btn btn-danger btn-sm">Reject</button>
                    </div>
                    <small class="text-muted">
                        ⏳ Auto-rejects in <span id="countdown-{booking.id}">{remaining}</span>
                    </small>
                    <script>
                        function formatTime(seconds) {{
                            const m = Math.floor(seconds / 60);
                            const s = seconds % 60;
                            return `${{m}}:${{String(s).padStart(2, '0')}}`;
                        }}

                        let timeLeft{booking.id} = {remaining};
                        const timer{booking.id} = setInterval(() => {{
                            const el = document.getElementById("countdown-{booking.id}");
                            if (!el) return;
                            if (timeLeft{booking.id} <= 0) {{
                                clearInterval(timer{booking.id});
                                el.innerText = "0:00";
                                fetch("/respond_notification/{n.id}", {{
                                    method: "POST",
                                    headers: {{ "Content-Type": "application/json" }},
                                    body: JSON.stringify({{ response: "Reject" }})
                                }}).then(r => r.json()).then(() => location.reload());
                            }} else {{
                                el.innerText = formatTime(timeLeft{booking.id}--);
                            }}
                        }}, 1000);

                        // initialize immediately
                        document.getElementById("countdown-{booking.id}").innerText = formatTime(timeLeft{booking.id});
                    </script>
                    """
                )

        elif n.action_type == "auto_rejected":
            block.append(
                """
                <div class="alert alert-danger mt-2 p-2">
                    ❌ Booking auto-rejected because the worker did not respond in time.
                </div>
                """
            )

        elif n.action_type in ("payment_required", "waiting_payment"):
            booking = db.get(Booking, n.booking_id) if n.booking_id else None
            if booking:
                now = datetime.utcnow()
                if booking.status == "Token Paid":
                    block.append(
                        f"""
                        <div class="alert alert-success mt-2 p-2">
                            ✅ Token paid by {booking.provider.name}. Job confirmed.
                        </div>
                        """
                    )
                elif booking.expires_at < now:
                    block.append(
                        """
                        <div class="alert alert-danger mt-2 p-2">
                            ❌ Token not received in time. Booking cancelled.
                        </div>
                        """
                    )
                else:
                    if n.action_type == "payment_required" and current_user.id == booking.provider_id:
                        block.append(
                            f"""
                            <form action="/pay_token/{booking.token}" method="get" class="d-flex gap-2 mt-2">
                                <button type="submit" class="btn btn-warning btn-sm">💳 Pay Token Now</button>
                            </form>
                            """
                        )
                    elif n.action_type == "waiting_payment" and current_user.id == booking.worker_id:
                        block.append(
                            f"""
                            <form action="/waiting_for_payment/{booking.token}" method="get" class="d-flex gap-2 mt-2">
                                <button type="submit" class="btn btn-info btn-sm">⏳ Go to Waiting Page</button>
                            </form>
                            """
                        )

        block.append("  </div></div>")
        html_notifications.append("\n".join(block))

    db.commit()  # save read flags

    body = "\n".join(html_notifications) or "<p>No notifications yet.</p>"

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <title>Notifications - JobConnect</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
        <style>
            body {{ background-color: #f8f9fa; padding: 20px; }}
            .card {{ border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.05); }}
            .card-text {{ font-size: 16px; line-height: 1.5; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2 class="mb-4 text-center">🔔 Your Notifications</h2>
            {body}
        </div>
        <script>
            function respondNotification(noteId, response) {{
                fetch(`/respond_notification/${{noteId}}`, {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify({{ response }})
                }})
                .then(res => res.json())
                .then(data => {{
                    if (data.redirect) {{
                        window.location.href = data.redirect;
                    }} else if (data.status === "rejected" || data.status === "auto_rejected") {{
                        location.reload();
                    }} else if (data.error) {{
                        alert("Error: " + data.error);
                    }}
                }})
                .catch(err => {{
                    console.error("Request failed:", err);
                    alert("Something went wrong!");
                }});
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

# If you already have this path, skip or rename to /notifications/unread_count2
@router.get("/notifications/unread_count")
def notifications_unread_count(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_for_notifications),
):
    count = (
        db.query(Notification)
        .filter(
            Notification.recipient_id == current_user.id,
            Notification.is_read == False  # noqa: E712
        )
        .count()
    )
    return {"count": count}

