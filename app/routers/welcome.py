# app/routers/welcome.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.security.auth import get_current_user
from app.database import get_db
from app.models import User, Notification, WorkerProfile

router = APIRouter(tags=["welcome"])
templates = Jinja2Templates(directory="app/templates")




# app/routers/welcome.py

@router.get("/welcome", response_class=HTMLResponse)
def welcome(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    if not current_user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    unread_count = (
        db.query(Notification)
        .filter(
            Notification.recipient_id == current_user.id,
            Notification.is_read == False,  # noqa: E712
        )
        .count()
    )

    profile: WorkerProfile | None = (
        db.query(WorkerProfile)
        .filter(WorkerProfile.user_id == current_user.id)
        .first()
    )

    is_worker = bool(
        profile
        or getattr(current_user, "is_worker", False)
        or getattr(current_user, "role", "") == "worker"
        or getattr(current_user, "user_type", "") == "worker"
    )

    has_profile = bool(profile)

    return templates.TemplateResponse(
        "welcome.html",
        {
            "request": request,
            "current_user": current_user,
            "unread_count": unread_count,
            "is_worker": is_worker,
            "is_online": bool(profile and profile.is_online),
            "show_worker_toggle": bool(profile),
            "has_profile": has_profile,                     # 👈 add this
            "worker_profile_url": "/seek_job",              # 👈 optional convenience
            "create_worker_url": "/create_worker_profile",  # 👈 optional convenience
        },
    )
