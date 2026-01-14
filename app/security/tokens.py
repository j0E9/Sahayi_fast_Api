# app/security/tokens.py
import json
from cryptography.fernet import Fernet
from fastapi import HTTPException
from app.settings import settings

fernet = Fernet(settings.FERNET_KEY.encode())

def encode_worker_link(worker_id: int, job_id: int | None, skill_id: int | None) -> str:
    payload = {"w": worker_id, "j": job_id, "s": skill_id}
    return fernet.encrypt(json.dumps(payload).encode()).decode()

def decode_worker_link(token: str) -> dict:
    try:
        return json.loads(fernet.decrypt(token.encode()).decode())
    except Exception:
        raise HTTPException(status_code=404, detail="Invalid or expired link")
