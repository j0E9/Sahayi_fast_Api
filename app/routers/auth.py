# app/routers/auth.py
from datetime import datetime, timedelta
from jose import jwt, JWTError
from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import OAuth2PasswordBearer
from app.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])

# -------------------------------
# JWT Config
# -------------------------------
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60  # 1 hour

# OAuth2 scheme (if you later add /auth/token endpoint)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# -------------------------------
# Token Utilities
# -------------------------------
def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """
    Create a JWT access token.
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str = Depends(oauth2_scheme)) -> dict:
    """
    Verify a JWT and return the decoded payload.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

# -------------------------------
# Simple Ping Test
# -------------------------------
@router.get("/ping")
def ping():
    return {"ok": True}

# -------------------------------
# Example Protected Route (Optional)
# -------------------------------
@router.get("/whoami")
def whoami(payload: dict = Depends(verify_token)):
    """
    Example protected route that shows the decoded JWT payload.
    """
    return {"decoded": payload}
