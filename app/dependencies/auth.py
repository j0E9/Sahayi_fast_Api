#app\dependencies\auth.py
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )

    user = db.get(User, int(uid))
    if not user:
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )

    profile = user.worker_profile
    if profile and profile.moderation_status in ("suspended", "banned"):
        # 🔥 FORCE LOGOUT
        request.session.clear()

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account {profile.moderation_status}"
        )

    return user
