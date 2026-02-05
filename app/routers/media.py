# app/routers/media.py
from pathlib import Path
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from ..database import get_db
from app.security.auth import get_current_user
from ..models import WorkerProfile
from ..settings import settings

router = APIRouter(prefix="/media", tags=["media"])

# simple filename sanitizer (lite replacement for werkzeug.secure_filename)
def safe_filename(name: str) -> str:
    keep = [c if c.isalnum() or c in ("-", "_", ".", " ") else "_" for c in name]
    return "".join(keep).strip().replace(" ", "_")[:120]

@router.post("/upload_showcase", response_model=None)
def upload_showcase(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # ensure upload dir exists
    settings.UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

    filename = safe_filename(file.filename)
    dest_path = settings.UPLOAD_FOLDER / filename

    # write file to disk
    with dest_path.open("wb") as out:
        out.write(file.file.read())

    # fetch or create profile
    profile = db.query(WorkerProfile).filter(WorkerProfile.user_id == user.id).first()
    if not profile:
        profile = WorkerProfile(user_id=user.id)
        db.add(profile)

    lower = filename.lower()
    if any(lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif")):
        profile.photo = filename
    elif lower.endswith(".mp4"):
        profile.video = filename
    else:
        # not an allowed type (mirror your old behavior, but stricter)
        raise HTTPException(status_code=400, detail="Unsupported file type")

    db.commit()

    # mirror Flask: redirect to "seek_job" (adjust if your path differs)
    return RedirectResponse(url="/seek_job", status_code=303)
