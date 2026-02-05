#app/security/auth.py
from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, WorkerProfile


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    uid = request.session.get("user_id")

    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = db.get(User, int(uid))
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Invalid session")

    profile = user.worker_profile

    if profile:
        if profile.moderation_status == "suspended":
            request.session.clear()  # 🔥 FORCE LOGOUT
            raise HTTPException(
                status_code=403,
                detail="account_suspended"
            )

        if profile.moderation_status == "banned":
            request.session.clear()  # 🔥 FORCE LOGOUT
            raise HTTPException(
                status_code=403,
                detail="account_banned"
            )

    return user

