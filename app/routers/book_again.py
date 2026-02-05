# app/routers/book_again.py
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Skill
from app.routers.jobs import get_current_user
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["book-again"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/book-again", response_class=HTMLResponse)
def book_again(
    request: Request,
    worker_id: int,
    skill_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skill = (
        db.query(Skill)
        .filter(
            Skill.id == skill_id,
            Skill.user_id == worker_id
        )
        .first()
    )

    if not skill:
        raise HTTPException(status_code=404, detail="Invalid or unavailable skill")

    return templates.TemplateResponse(
        "confirm_booking.html",
        {
            "request": request,
            "worker": skill.user,
            "selected_skill": skill,
            "is_custom": False,
            "agreed_price": None,
        }
    )
