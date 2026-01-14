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

type_label_map = {
    "booking_request": "Booking Request",
    "auto_rejected": "Auto Rejected",
    "payment_required": "Payment Required",
    "waiting_payment": "Waiting for Payment",
    "token_paid": "Payment Successful",
    "payment_expired": "Payment Cancelled",
    "wfh_request": "WFH Booking",
}

type_icon_map = {
    "booking_request": "📩",
    "auto_rejected": "❌",
    "payment_required": "💳",
    "waiting_payment": "⏳",
    "token_paid": "✅",
    "payment_expired": "❌",
    "wfh_request": "🏠",
}

type_badge_class_map = {
    "booking_request": "bg-primary",
    "auto_rejected": "bg-danger",
    "payment_required": "bg-warning text-dark",
    "waiting_payment": "bg-info text-dark",
    "token_paid": "bg-success",
    "payment_expired": "bg-danger",
    "wfh_request": "bg-warning text-dark",
}


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
    order_col = getattr(Notification, "timestamp", None) or \
                getattr(Notification, "created_at", None) or \
                Notification.id

    note = (
        db.query(Notification)
        .filter(
            Notification.recipient_id == current_user.id,
            Notification.is_read == False,   # noqa
            Notification.action_type == "booking_request",
        )
        .order_by(order_col.desc())
        .first()
    )

    if not note:
        return JobAlertOut(has_new_request=False)

    sender = "someone"

    if note.booking_id:
        booking = db.get(Booking, note.booking_id)
        if booking and booking.booking_type == "wfh":
            sender = "WFH Booking"
        elif note.sender:
            sender = note.sender.name

    return JobAlertOut(has_new_request=True, sender=sender)

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

def get_remaining_seconds(booking):
    if not booking:
        return None

    # WFH bookings never have timers
    if booking.booking_type == "wfh":
        return None

    if not booking.expires_at:
        return None

    return max(
        0,
        int((booking.expires_at - datetime.utcnow()).total_seconds())
    )


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
        was_unread = not n.is_read
        if not n.is_read:
            n.is_read = True  # mark read

        base_type = n.action_type or "general"
        if n.action_type == "booking_request" and n.booking_id:
            booking_check = db.get(Booking, n.booking_id)
            if booking_check and booking_check.booking_type == "wfh":
                base_type = "wfh_request"



        # ---- derive payment state (for correct label) ----
        booking = None
        payment_state = None
        now = None

        if base_type in ("payment_required", "waiting_payment") and n.booking_id:
            booking = db.get(Booking, n.booking_id)
            if booking:
                now = datetime.utcnow()
                if booking.status == "Token Paid":
                    payment_state = "paid"
                elif booking.expires_at and booking.expires_at < now:
                    payment_state = "expired"
                else:
                    payment_state = "pending"

        # effective type for display (badge & icon)
        display_type = base_type
        if payment_state == "paid":
            display_type = "token_paid"
        elif payment_state == "expired":
            display_type = "payment_expired"

        type_label = type_label_map.get(display_type, "Notification")
        type_icon = type_icon_map.get(display_type, "🔔")
        type_badge_class = type_badge_class_map.get(display_type, "bg-secondary")

        read_class = "notification-unread" if was_unread else "notification-read"

        timestamp_str = ""
        if getattr(n, "timestamp", None):
            timestamp_str = n.timestamp.strftime("%d %b %Y, %I:%M %p")

        block = [
            f'<div class="notification-card card mb-3 shadow-sm {read_class}" '
            f'     data-type="{base_type}" data-read={"false" if was_unread else "true"}>',
            '  <div class="card-body d-flex flex-column flex-md-row gap-3 align-items-start">',
            '    <div class="notif-icon flex-shrink-0 d-flex align-items-center justify-content-center rounded-circle">',
            f'      <span class="fs-4">{type_icon}</span>',
            '    </div>',
            '    <div class="flex-grow-1">',
            '      <div class="d-flex justify-content-between align-items-center mb-1 flex-wrap gap-2">',
            f'        <span class="badge {type_badge_class} rounded-pill px-3 py-1">{type_label}</span>',
            '      </div>',
            f'      <p class="card-text mb-1">{n.message}</p>',
        ]

        if timestamp_str:
            block.append(
                f'      <small class="text-muted">Received on {timestamp_str}</small>'
            )

        # --- context-specific UI blocks ---
        if base_type in ("booking_request", "wfh_request"):
            booking_req = db.get(Booking, n.booking_id) if n.booking_id else None

            # ---------------- ONSITE BOOKING (has timer) ----------------
            if (
                    booking_req
                    and booking_req.booking_type != "wfh"
                    and booking_req.status == "Pending"
                    and booking_req.expires_at
            ):
                remaining = max(
                    0,
                    int((booking_req.expires_at - datetime.utcnow()).total_seconds()),
                )

                block.append(
                    f"""
                    <div class="mt-3">
                        <div class="d-flex flex-wrap gap-2 align-items-center">
                            <button onclick="respondNotification({n.id}, 'Accept')" class="btn btn-success btn-sm">
                                Accept
                            </button>
                            <button onclick="respondNotification({n.id}, 'Reject')" class="btn btn-outline-danger btn-sm">
                                Reject
                            </button>
                            <div class="ms-md-3 small text-muted d-flex align-items-center gap-1">
                                ⏳ <span>Auto-rejects in</span>
                                <span class="fw-semibold" id="countdown-{booking_req.id}">
                                    {remaining}
                                </span>
                            </div>
                        </div>
                    </div>

                    <script>
                        function formatTime_{booking_req.id}(seconds) {{
                            const m = Math.floor(seconds / 60);
                            const s = seconds % 60;
                            return `${{m}}:${{String(s).padStart(2, '0')}}`;
                        }}

                        let timeLeft{booking_req.id} = {remaining};
                        const timer{booking_req.id} = setInterval(() => {{
                            const el = document.getElementById("countdown-{booking_req.id}");
                            if (!el) return;

                            if (timeLeft{booking_req.id} <= 0) {{
                                clearInterval(timer{booking_req.id});
                                el.innerText = "0:00";
                                fetch("/respond_notification/{n.id}", {{
                                    method: "POST",
                                    headers: {{ "Content-Type": "application/json" }},
                                    body: JSON.stringify({{ response: "Reject" }})
                                }}).then(() => location.reload());
                            }} else {{
                                el.innerText = formatTime_{booking_req.id}(timeLeft{booking_req.id}--);
                            }}
                        }}, 1000);

                        document.getElementById("countdown-{booking_req.id}")
                                .innerText = formatTime_{booking_req.id}(timeLeft{booking_req.id});
                    </script>
                    """
                )

            # ---------------- WFH BOOKING (NO TIMER) ----------------
            elif booking_req and booking_req.booking_type == "wfh":
                block.append(
                    """
                    <div class="mt-3">
                        <span class="badge bg-warning text-dark mb-2">
                            🏠 Work From Home – Price Pending
                        </span>
                        <div>
                            <a href="/worker/wfh-bookings" class="btn btn-warning btn-sm">
                                Open WFH Requests
                            </a>
                        </div>
                    </div>
                    """
                )



        elif base_type == "auto_rejected":
            block.append(
                """
                <div class="mt-3 alert alert-danger border-0 py-2 mb-0">
                    ❌ Booking auto-rejected because the worker did not respond in time.
                </div>
                """
            )


        elif base_type in ("payment_required", "waiting_payment"):
            # Use the booking/payment_state we computed above
            if booking:
                if payment_state == "paid":
                    block.append(
                        f"""
                        <div class="mt-3 alert alert-success border-0 py-2 mb-0">
                            ✅ Token paid by {booking.provider.name}. Job confirmed.
                        </div>
                        """
                    )
                elif payment_state == "expired":
                    block.append(
                        """
                        <div class="mt-3 alert alert-danger border-0 py-2 mb-0">
                            ❌ Token not received in time. Booking cancelled.
                        </div>
                        """
                    )
                else:  # pending
                    if n.action_type == "payment_required" and current_user.id == booking.provider_id:
                        block.append(
                            f"""
                            <div class="mt-3 d-flex flex-wrap gap-2">
                                <form action="/pay_token/{booking.token}" method="get" class="d-inline-block">
                                    <button type="submit" class="btn btn-warning btn-sm">
                                        💳 Pay Token Now
                                    </button>
                                </form>
                            </div>
                            """
                        )
                    elif n.action_type == "waiting_payment" and current_user.id == booking.worker_id:
                        block.append(
                            f"""
                            <div class="mt-3 d-flex flex-wrap gap-2">
                                <form action="/waiting_for_payment/{booking.token}" method="get" class="d-inline-block">
                                    <button type="submit" class="btn btn-info btn-sm">
                                        ⏳ Go to Waiting Page
                                    </button>
                                </form>
                            </div>
                            """
                        )

        block.append("    </div>")
        block.append("  </div>")
        block.append("</div>")
        html_notifications.append("\n".join(block))

    db.commit()

    body = "\n".join(html_notifications) or """
        <div class="text-center text-muted py-5">
            <h5 class="fw-semibold mb-2">No notifications yet</h5>
            <p class="mb-0">You will see booking and payment updates here.</p>
        </div>
    """

    # keep the same HTML shell + CSS + JS you already had
    # (omitted here for brevity) – only the loop above needed fixing
    ...


    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <title>Notifications - JobConnect</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
        <style>
            body {{
                background: radial-gradient(circle at top left, #e0f2ff, #f8f9fa 45%, #f1f3f5);
                min-height: 100vh;
                padding: 24px 12px;
                font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            }}
            .notifications-wrapper {{
                max-width: 900px;
                margin: 0 auto;
            }}
            .notifications-header {{
                text-align: center;
                margin-bottom: 24px;
            }}
            .notifications-header h2 {{
                font-weight: 700;
                letter-spacing: 0.02em;
            }}
            .notifications-header p {{
                color: #6c757d;
                margin: 0;
            }}
            .filter-pill {{
                border-radius: 999px;
                padding: 6px 14px;
                border: 1px solid #dee2e6;
                background-color: #ffffff;
                font-size: 0.85rem;
                cursor: pointer;
                transition: all 0.15s ease-in-out;
            }}
            .filter-pill:hover {{
                background-color: #f1f3f5;
            }}
            .filter-pill.active {{
                background-color: #0d6efd;
                color: #ffffff;
                border-color: #0d6efd;
                box-shadow: 0 0.25rem 0.5rem rgba(13,110,253,0.25);
            }}
            .notification-card {{
                border-radius: 16px;
                border: 1px solid rgba(0,0,0,0.03);
                transition: transform 0.12s ease-out, box-shadow 0.12s ease-out, border-color 0.12s ease-out;
                background-color: #ffffff;
            }}
            .notification-card.notification-unread {{
                border-color: #0d6efd33;
                box-shadow: 0 0.5rem 1rem rgba(13,110,253,0.05);
            }}
            .notification-card:hover {{
                transform: translateY(-2px);
                box-shadow: 0 0.7rem 1.2rem rgba(15,23,42,0.08);
            }}
            .notif-icon {{
                width: 44px;
                height: 44px;
                background: linear-gradient(135deg, #eef4ff, #edf2ff);
                border: 1px solid #e0e7ff;
            }}
            .card-text {{
                font-size: 0.95rem;
                line-height: 1.5;
            }}
            @media (max-width: 576px) {{
                body {{
                    padding: 16px 8px;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="notifications-wrapper">
            <div class="notifications-header">
                <h2 class="mb-2">🔔 Your Notifications</h2>
                <p>Stay up to date with booking requests, auto-rejections, and payment status.</p>
            </div>

            <div class="d-flex flex-wrap justify-content-center gap-2 mb-4">
                <button class="filter-pill active" data-filter="all">All</button>
                <button class="filter-pill" data-filter="unread">Unread</button>
                <button class="filter-pill" data-filter="booking_request">Bookings</button>
                <button class="filter-pill" data-filter="payments">Payments</button>
            </div>

            {body}
        </div>

        <script>
            // Respond to booking Accept/Reject
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
                    }} else {{
                        location.reload();
                    }}
                }})
                .catch(err => {{
                    console.error("Request failed:", err);
                    alert("Something went wrong!");
                }});
            }}

            // Simple client-side filters
            (function() {{
                const pills = document.querySelectorAll(".filter-pill");
                const cards = document.querySelectorAll(".notification-card");

                function applyFilter(filter) {{
                    cards.forEach(card => {{
                        const type = card.getAttribute("data-type");
                        const isRead = card.getAttribute("data-read") === "true";

                        let show = true;
                        if (filter === "unread") {{
                            show = !isRead;
                        }} else if (filter === "booking_request") {{
                            show = (type === "booking_request" || type === "wfh_request");
                        }}
                        else if (filter === "payments") {{
                            show = (type === "payment_required" || type === "waiting_payment");
                        }}

                        card.style.display = show ? "" : "none";
                    }});
                }}

                pills.forEach(pill => {{
                    pill.addEventListener("click", () => {{
                        pills.forEach(p => p.classList.remove("active"));
                        pill.classList.add("active");
                        const filter = pill.getAttribute("data-filter");
                        applyFilter(filter);
                    }});
                }});
            }})();
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

