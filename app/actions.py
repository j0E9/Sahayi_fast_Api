# app/routes/actions.py
import json
from typing import Optional, List
from app.routers.booking_details import _next_warning_allowed
from fastapi import APIRouter, Depends, Request, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.redis_client import r, REDIS_URL
import logging
import traceback
from app.deps import get_current_user, get_db
from app.auth_utils import create_action_token, decode_action_token, TokenExpiredError, TokenInvalidError
from app.settings import settings
from app.models import Booking

router = APIRouter(prefix="/action")
logger = logging.getLogger("uvicorn.error")

class PrepareBody(BaseModel):
    action: str
    booking_id: str


# Allowed actions — keep this list tight and explicit
_ALLOWED_ACTIONS: List[str] = ["issue_warning"]


def _parse_booking_id(booking_id_raw: Optional[str]) -> int:
    """
    Convert booking id passed as string to int for DB queries.
    Raise ValueError if invalid — callers should handle and return 400.
    """
    if booking_id_raw is None:
        raise ValueError("empty booking id")
    if isinstance(booking_id_raw, int):
        return booking_id_raw
    s = str(booking_id_raw).strip()
    if not s:
        raise ValueError("empty booking id")
    return int(s)


def _user_owns_booking_or_is_admin(db: Session, booking_id: int, current_user) -> bool:
    """
    Check ownership using a variety of possible ownership fields.
    booking_id must be an integer for DB queries.
    """
    booking: Optional[Booking] = db.query(Booking).filter_by(id=booking_id).one_or_none()
    if not booking:
        return False

    # include provider_id and worker_id because your DB uses them
    owner_fields = (
        "user_id", "owner_id", "giver_id", "creator_id",
        "provider_id", "worker_id",
    )

    for f in owner_fields:
        if hasattr(booking, f):
            owner_val = getattr(booking, f)
            if owner_val is None:
                continue
            # compare as strings to avoid type mismatches
            if str(owner_val) == str(current_user.id):
                return True

    # fallback: allow admins if user model supports that attribute
    if getattr(current_user, "is_admin", False):
        return True

    return False

@router.post("/prepare")
async def prepare_action(body: PrepareBody, current_user = Depends(get_current_user), db: Session = Depends(get_db)):
    # Validate requested action
    if body.action not in _ALLOWED_ACTIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported action requested")

    # Parse booking id and confirm booking exists & permission
    try:
        booking_id_int = _parse_booking_id(body.booking_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid booking_id")

    booking = db.query(Booking).filter_by(id=booking_id_int).one_or_none()
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    if not _user_owns_booking_or_is_admin(db, booking_id_int, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this booking")

    # ----------------- SERVER POLICY CHECK: is warning allowed now? -----------------
    # Uses the same logic as your UI decision (so UI + server are consistent).
    try:
        allowed, remaining = _next_warning_allowed(db, booking)
    except Exception:
        # if the policy check itself fails for some reason, deny to be safe
        logger.exception("Failed to evaluate warning policy for booking %s", booking_id_int)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Warning not allowed")

    # disallow if not allowed or worker already arrived (UI hides button when worker_arrived)
    if not allowed or getattr(booking, "worker_arrived", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Warning not allowed right now (server policy)"
        )
    # -------------------------------------------------------------------------------

    # Create action token (includes a jti)
    try:
        # token stores booking id as string for compatibility with current clients
        token = create_action_token(current_user.id, body.action, str(booking_id_int))
    except Exception as e:
        logger.error("Failed to create action token: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to create action token: {str(e)[:200]}")

    # Decode freshly-created token to extract jti and persist it server-side (single-use)
    try:
        payload = decode_action_token(token)
    except TokenExpiredError:
        logger.exception("Token expired immediately after creation (time skew?)")
        raise HTTPException(status_code=500, detail="Failed to decode freshly created token (expired?)")
    except TokenInvalidError:
        logger.exception("Token invalid immediately after creation (encoding/secret mismatch?)")
        raise HTTPException(status_code=500, detail="Failed to decode freshly created token (invalid)")
    except Exception as e:
        logger.error("Unexpected error decoding freshly created token: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to decode freshly created token")

    jti = payload.get("jti")
    if not jti:
        logger.error("Created token missing jti payload. token_payload=%s", payload)
        raise HTTPException(status_code=500, detail="action token missing jti")

    # Persist jti in Redis with TTL
    try:
        meta = {"user_id": str(current_user.id), "action": body.action, "booking_id": str(booking_id_int)}
        r.setex(f"action_jti:{jti}", int(settings.ACTION_TOKEN_EXPIRE_SECONDS), json.dumps(meta))
        logger.debug("Persisted action_jti:%s -> %s (ttl=%s)", jti, meta, settings.ACTION_TOKEN_EXPIRE_SECONDS)
    except Exception as e:
        logger.error("Failed to persist action jti to Redis: %s\n%s", e, traceback.format_exc())
        try:
            logger.error("Redis URL: %s", REDIS_URL)
        except Exception:
            logger.exception("Couldn't read REDIS_URL for debug")
        raise HTTPException(status_code=500, detail="Failed to persist action token (server error)")

    return {"action_token": token, "expires_in": int(settings.ACTION_TOKEN_EXPIRE_SECONDS)}



@router.post("/issue_warning")
async def issue_warning(request: Request, current_user = Depends(get_current_user), db: Session = Depends(get_db)):
    # client must include x-action-token header
    action_token = request.headers.get("x-action-token")
    if not action_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing action token")

    # decode and handle token-specific errors explicitly
    try:
        payload = decode_action_token(action_token)
    except TokenExpiredError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Action token expired")
    except TokenInvalidError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid action token")
    except Exception:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid/expired action token")

    # Validate association: token sub must be current user
    if str(payload.get("sub")) != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Action token user mismatch")

    # Validate action name matches expected endpoint
    if payload.get("action") != "issue_warning":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Action token mismatch")

    # booking_id from token (token may store as string)
    booking_id_raw = payload.get("booking_id")
    if not booking_id_raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Action token missing booking_id")

    try:
        booking_id_int = _parse_booking_id(booking_id_raw)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid booking_id in token")

    # --- SINGLE-USE CHECK: ensure jti exists in Redis and consume it ---
    jti = payload.get("jti")
    if not jti:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing jti in token")

    key = f"action_jti:{jti}"
    meta_raw = r.get(key)
    if not meta_raw:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Action token invalid or already used")

    try:
        meta = json.loads(meta_raw)
    except Exception:
        r.delete(key)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Action token metadata invalid")

    if str(meta.get("user_id")) != str(current_user.id) or meta.get("action") != payload.get("action"):
        r.delete(key)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Action token metadata mismatch")

    # consume jti
    r.delete(key)

    # Double-check booking exists and still belongs to user / has expected state
    booking = db.query(Booking).filter_by(id=booking_id_int).one_or_none()
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    if not _user_owns_booking_or_is_admin(db, booking_id_int, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to act on this booking")

    # TODO: perform the warning action (write to DB / send notification / audit log)
    return {"success": True, "booking_id": booking_id_int}


@router.post("/__diag_prepare", include_in_schema=False)
async def diag_prepare(
    booking_id: str = Query(...),
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = {"ok": False, "step": None, "error": None, "token_payload": None, "redis_key": None}
    try:
        result["step"] = "check_booking"
        try:
            booking_id_int = _parse_booking_id(booking_id)
        except ValueError:
            result["error"] = "invalid_booking_id"
            return result

        booking = db.query(Booking).filter_by(id=booking_id_int).one_or_none()
        if not booking:
            result["error"] = "booking_not_found"
            return result

        if not _user_owns_booking_or_is_admin(db, booking_id_int, current_user):
            result["error"] = "not_authorized"
            return result

        result["step"] = "create_token"
        token = create_action_token(current_user.id, "issue_warning", str(booking_id_int))
        result["token"] = token

        result["step"] = "decode_token"
        try:
            payload = decode_action_token(token)
            result["token_payload"] = payload
        except Exception as e:
            result["error"] = f"decode_failed: {repr(e)}"
            return result

        jti = payload.get("jti")
        if not jti:
            result["error"] = "missing_jti"
            result["token_payload"] = payload
            return result

        result["step"] = "persist_jti"
        meta = {"user_id": str(current_user.id), "action": "issue_warning", "booking_id": str(booking_id_int)}
        try:
            r.setex(f"action_jti:{jti}", int(settings.ACTION_TOKEN_EXPIRE_SECONDS), json.dumps(meta))
            result["redis_key"] = f"action_jti:{jti}"
            result["ok"] = True
            result["step"] = "done"
            return result
        except Exception as e:
            result["error"] = "redis_set_failed"
            result["redis_error"] = str(e)
            result["redis_trace"] = traceback.format_exc()
            try:
                result["redis_url"] = REDIS_URL
            except Exception:
                result["redis_url"] = "<unknown>"
            return result

    except Exception as e:
        result["error"] = "unexpected"
        result["err_repr"] = repr(e)
        result["trace"] = traceback.format_exc()
        return result
