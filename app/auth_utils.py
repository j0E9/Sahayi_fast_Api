# app/auth_utils.py
"""JWT helpers: create & decode access/action tokens with improved error handling.

This module does NOT raise HTTPExceptions directly so it can be used in non-FastAPI
contexts (e.g. unit tests). Callers (deps/routes) should catch the specific
exceptions below and translate them to HTTP responses (401/403) as appropriate.
"""
from __future__ import annotations

import time
from typing import Dict, Any, Optional, List
from uuid import uuid4

import jwt
from jwt import (
    ExpiredSignatureError as PyJWTExpiredSignatureError,
    InvalidTokenError as PyJWTInvalidTokenError,
)

from app.settings import settings


# --- Custom exceptions for callers to handle ---
class TokenError(Exception):
    """Base class for token issues."""


class TokenExpiredError(TokenError):
    """Token has expired."""


class TokenInvalidError(TokenError):
    """Token is invalid (signature, malformed, wrong claims, etc.)."""


# --- Token creation helpers ---
def _now() -> int:
    return int(time.time())


def create_access_token(subject: str, scopes: str = "", extra_claims: Optional[Dict[str, Any]] = None) -> str:
    """
    Create a signed access token.

    :param subject: the 'sub' claim (usually user id)
    :param scopes: a space-separated scope string (or empty)
    :param extra_claims: optional dict of additional claims to include
    :return: JWT string
    """
    iat = _now()
    payload: Dict[str, Any] = {
        "sub": str(subject),
        "iat": iat,
        "exp": iat + int(settings.ACCESS_TOKEN_EXPIRE_SECONDS),
        "scope": scopes or "",
    }
    if extra_claims:
        payload.update(extra_claims)

    token = jwt.encode(payload, settings.ACCESS_TOKEN_SECRET, algorithm=settings.ACCESS_TOKEN_ALGORITHM)
    # PyJWT may return bytes on older versions
    return token if isinstance(token, str) else token.decode()


def create_action_token(user_id: str, action: str, booking_id: str, extra_claims: Optional[Dict[str, Any]] = None) -> str:
    """
    Create a short-lived action token tied to a specific booking/action.
    This token now includes a single-use 'jti' claim (UUID) which callers
    should persist server-side (Redis or DB) and consume when the token is used.
    """
    iat = _now()
    jti = str(uuid4())
    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "action": action,
        "booking_id": str(booking_id),
        "jti": jti,
        "iat": iat,
        "exp": iat + int(settings.ACTION_TOKEN_EXPIRE_SECONDS),
    }
    if extra_claims:
        payload.update(extra_claims)

    token = jwt.encode(payload, settings.ACTION_TOKEN_SECRET, algorithm=settings.ACTION_TOKEN_ALGORITHM)
    return token if isinstance(token, str) else token.decode()


# --- Token decoding helpers (raise clear exceptions) ---
def _decode(
    token: str,
    secret: str,
    algorithms: List[str],
    audience: Optional[str] = None,
    issuer: Optional[str] = None,
    leeway: int = 0,
) -> Dict[str, Any]:
    """
    Internal decoder that converts PyJWT exceptions into our own exceptions.

    Callers should catch TokenExpiredError and TokenInvalidError.
    """
    try:
        options = {"require_sub": False}
        # Only include audience/issuer if provided
        payload = jwt.decode(
            token,
            secret,
            algorithms=algorithms,
            audience=audience if audience else None,
            issuer=issuer if issuer else None,
            options=options,
            leeway=leeway,
        )
        # Optional: ensure 'sub' exists (most of our flows depend on it)
        if "sub" not in payload:
            raise TokenInvalidError("token payload missing 'sub' claim")
        return payload
    except PyJWTExpiredSignatureError as exc:
        raise TokenExpiredError(str(exc)) from exc
    except PyJWTInvalidTokenError as exc:
        raise TokenInvalidError(str(exc)) from exc


def decode_access_token(token: str, audience: Optional[str] = None, issuer: Optional[str] = None, leeway: int = 0) -> Dict[str, Any]:
    """
    Decode an access token and return the payload.

    :param token: JWT string
    :param audience: optional expected aud claim
    :param issuer: optional expected iss claim
    :param leeway: seconds of clock skew allowed
    :raises TokenExpiredError, TokenInvalidError
    """
    return _decode(
        token,
        secret=settings.ACCESS_TOKEN_SECRET,
        algorithms=[settings.ACCESS_TOKEN_ALGORITHM],
        audience=audience,
        issuer=issuer,
        leeway=leeway,
    )


def decode_action_token(token: str, audience: Optional[str] = None, issuer: Optional[str] = None, leeway: int = 0) -> Dict[str, Any]:
    """
    Decode an action token and return the payload.

    :raises TokenExpiredError, TokenInvalidError
    """
    return _decode(
        token,
        secret=settings.ACTION_TOKEN_SECRET,
        algorithms=[settings.ACTION_TOKEN_ALGORITHM],
        audience=audience,
        issuer=issuer,
        leeway=leeway,
    )
