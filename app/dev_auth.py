# app/routers/dev_auth.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.settings import settings
from app.auth_utils import create_access_token

router = APIRouter(prefix="/_dev", tags=["dev"])

class DevIssue(BaseModel):
    user_id: str
    scopes: str = ""

@router.post("/issue_token")
async def issue_token(body: DevIssue):
    if not settings.ALLOW_DEV_TOKENS:
        raise HTTPException(status_code=403, detail="Dev tokens disabled")
    token = create_access_token(body.user_id, scopes=body.scopes)
    return {"access_token": token, "token_type": "bearer"}
