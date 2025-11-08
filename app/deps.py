# app/deps.py
from fastapi import Request, HTTPException, Depends, status
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from .database import get_db
from .settings import settings
from .models import User  # define later as SQLAlchemy model

templates = Jinja2Templates(directory="app/templates")

def current_user(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.get(User, uid)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
def get_session_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("user_id")
    if not uid:
        # redirect-like behavior
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    user = db.get(User, uid)
    if not user:
        request.session.pop("user_id", None)
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user