# app/deps.py
from typing import Optional
from fastapi import Request, HTTPException, Depends, status
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import get_db
from .settings import settings
from .auth_utils import decode_access_token
from .models import User as DBUser   # your SQLAlchemy model

# Keep templates for pages.py and other routers
templates = Jinja2Templates(directory="app/templates")


# -----------------------------------------------------------
# INTERNAL HELPERS
# -----------------------------------------------------------

async def _user_from_session(request: Request, db: Session) -> Optional[DBUser]:
    """Load user using existing session cookie."""
    uid = request.session.get("user_id")
    if not uid:
        return None

    user = db.get(DBUser, uid)
    if not user:
        request.session.pop("user_id", None)
        return None

    return user


async def _user_from_bearer(request: Request, db: Session) -> Optional[DBUser]:
    """Load user using Authorization: Bearer <JWT>."""
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return None

    token = auth.split(" ", 1)[1]

    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid/expired access token"
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user = db.get(DBUser, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # Optional: token revocation support
    token_iat = payload.get("iat")
    if (
        getattr(user, "tokens_revoked_before", None)
        and token_iat
        and token_iat < user.tokens_revoked_before
    ):
        raise HTTPException(status_code=401, detail="Token revoked")

    return user


# -----------------------------------------------------------
# PUBLIC DEPENDENCIES
# -----------------------------------------------------------

async def get_current_user(
    request: Request,
    db: Session = Depends(get_db)
) -> DBUser:
    """
    Unified auth dependency for API endpoints:
    1) Try Bearer JWT token
    2) Fall back to session cookie
    """
    # Prefer JWT (for API security)
    user = await _user_from_bearer(request, db)
    if user:
        return user

    # Fall back to session cookie (for web pages)
    user = await _user_from_session(request, db)
    if user:
        return user

    # No valid auth → block
    raise HTTPException(status_code=401, detail="Not authenticated")


async def get_session_user(
    request: Request,
    db: Session = Depends(get_db)
) -> DBUser:
    """
    For page routes that must redirect to login instead of 401.
    """
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"}
        )

    user = db.get(DBUser, uid)
    if not user:
        request.session.pop("user_id", None)
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"}
        )

    return user
