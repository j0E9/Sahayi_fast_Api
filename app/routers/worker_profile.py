# app/routers/worker_profile.py
from __future__ import annotations

import os
import random
import shutil
from typing import List, Optional

from fastapi import (
    APIRouter, Depends, HTTPException, Request, UploadFile, File, Form, status
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from werkzeug.utils import secure_filename  # pip install werkzeug

from app.database import get_db
from app.models import (
    User, WorkerProfile, Skill, ShowcaseImage, Rating
)

router = APIRouter(tags=["worker_profile"])
templates = Jinja2Templates(directory="app/templates")

# Adjust this if your uploads path differs
UPLOAD_ROOT = "app/static/uploads"
os.makedirs(UPLOAD_ROOT, exist_ok=True)

# ---------- Trust API session auth ----------
def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = db.get(User, int(uid))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


# ---------- helpers ----------
def generate_unique_worker_id(db: Session) -> str:
    def is_valid(id_str: str) -> bool:
        # reject if 4 or more repeating digits
        for ch in set(id_str):
            if id_str.count(ch) >= 4:
                return False
        return True

    digits = 8
    # Try many 8-digit attempts
    for _ in range(10000):
        candidate = str(random.randint(10 ** (digits - 1), 10 ** digits - 1))
        if is_valid(candidate) and not db.query(WorkerProfile).filter_by(worker_code=candidate).first():
            return candidate

    # fallback to 9+ digits
    digits += 1
    while True:
        candidate = str(random.randint(10 ** (digits - 1), 10 ** digits - 1))
        if is_valid(candidate) and not db.query(WorkerProfile).filter_by(worker_code=candidate).first():
            return candidate


def allowed_file(filename: str, extensions: set[str]) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in extensions


IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
VIDEO_EXTS = {"mp4", "mov", "m4v", "webm", "mkv"}


def save_upload(user_id: int, file: UploadFile) -> Optional[str]:
    if not file or not file.filename:
        return None
    filename = secure_filename(file.filename)
    # Prefix with user id to reduce collisions
    final_name = f"{user_id}_{filename}"
    dest_path = os.path.join(UPLOAD_ROOT, final_name)
    with open(dest_path, "wb") as out:
        shutil.copyfileobj(file.file, out)
    return final_name


# ---------- routes ----------
@router.get("/seek_job", response_class=HTMLResponse)
def seek_job(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = current_user
    profile = db.query(WorkerProfile).filter_by(user_id=user.id).first()
    if not profile:
        # mirror your behavior: redirect to create page
        return RedirectResponse(url="/create_worker_profile", status_code=status.HTTP_303_SEE_OTHER)

    # Ratings
    ratings = (
        db.query(Rating)
        .filter_by(worker_id=user.id)
        .order_by(Rating.timestamp.desc())
        .all()
    )
    if ratings:
        avg = round(sum(r.stars for r in ratings) / len(ratings), 1)
        avg_rating = f"{avg} ★"
    else:
        avg_rating = "No ratings yet"

    # Skills list string
    skills = db.query(Skill).filter_by(user_id=user.id).all()
    skills_str = ", ".join(s.name for s in skills) if skills else "None"

    # Showcase
    showcase = db.query(ShowcaseImage).filter_by(user_id=user.id).order_by(ShowcaseImage.uploaded_at.desc()).all()
    video_item = profile.video  # filename or None

    photo_url = f"/static/uploads/{profile.photo}" if profile.photo else "/static/default_profile.jpg"
    gender = profile.gender or "Not specified"

    # Render a mini page (you already have a full HTML string in Flask;
    # if you prefer a template, create one & pass all vars below).
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <title>My Worker Profile - JobConnect</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
        <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&display=swap" rel="stylesheet">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css" rel="stylesheet">
        <style>
            body {{
                font-family: 'Poppins', sans-serif;
                background: linear-gradient(to right, #f0f4f8, #ffffff);
                color: #333;
            }}
            .profile-card {{
                background: #fff;
                border-radius: 12px;
                padding: 30px;
                box-shadow: 0 4px 15px rgba(0, 0, 0, 0.05);
            }}
            .profile-photo {{
                width: 130px; height: 130px; object-fit: cover;
                border-radius: 50%; border: 3px solid #007bff;
            }}
        </style>
    </head>
    <body>
        <div class="container my-5">
            <div class="profile-card">
                <div class="d-flex flex-row flex-wrap align-items-start justify-content-between">
                    <div>
                        <h2 class="mb-2">👤 {user.name}</h2>
                        <p><b>Worker ID:</b> {profile.worker_code}</p>
                        <p><b>Gender:</b> {gender}</p>
                        <p><b>Age:</b> {profile.age}</p>
                        <p><b>Phone:</b> {user.phone}</p>
                        <p><b>Qualification:</b> {profile.qualification}</p>
                        <p><b>Experience:</b> {profile.experience}</p>
                        <p><b>Skills:</b> {skills_str}</p>
                    </div>
                    <div class="ms-auto text-end">
                        <img src="{photo_url}" class="profile-photo shadow-sm" alt="Profile Photo">
                    </div>
                </div>
                <hr>
                <div class="mt-3">
                    <h5>About Me</h5>
                    <p>{profile.about or ""}</p>
                </div>
                <div class="mt-4">
                    <h5>⭐ Ratings</h5>
                    <p><b>Average:</b> {avg_rating}</p>
                    <ul>
                        {''.join(f"<li><b>{r.stars} ★</b> - {r.comment or ''}</li>" for r in ratings)}
                    </ul>
                </div>
                <div class="mt-4">
                    <h5>🎥 Showcase</h5>
                    {"".join(f'<img class="me-2 mb-2" src="/static/uploads/{img.image_url}" width="150">' for img in showcase) if showcase else "<p>No showcase items uploaded yet.</p>"}
                    {"<div class='mt-2'><video width='300' controls><source src='/static/uploads/"+video_item+"' type='video/mp4'></video></div>" if video_item else ""}
                </div>
                <div class="mt-4 text-end">
                    <a href="/edit_worker_profile" class="btn btn-outline-primary">✏️ Edit Profile</a>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)


@router.get("/create_worker_profile", response_class=HTMLResponse)
def create_worker_profile_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = db.query(WorkerProfile).filter_by(user_id=current_user.id).first()
    if existing:
        return RedirectResponse(url="/seek_job", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("create_worker_profile.html", {"request": request})


@router.post("/create_worker_profile")
async def create_worker_profile_post(
    request: Request,
    age: str = Form(...),
    gender: str = Form(...),
    qualification: str = Form(...),
    experience: str = Form(...),
    about: str = Form(...),

    bank_name: str = Form(...),
    branch: str = Form(...),
    ifsc: str = Form(...),
    account_number: str = Form(...),

    # ✅ Match your HTML fields name="skills[]", "rates[]", "rate_types[]"
    skills: list[str] = Form(default_factory=list, alias="skills[]"),
    rates: list[str] = Form(default_factory=list, alias="rates[]"),
    rate_types: list[str] = Form(default_factory=list, alias="rate_types[]"),

    photo: UploadFile | None = File(None),
    id_front: UploadFile | None = File(None),
    id_back: UploadFile | None = File(None),
    pan_card: UploadFile | None = File(None),

    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Guard: already has a profile → send to profile page
    if db.query(WorkerProfile).filter_by(user_id=current_user.id).first():
        return RedirectResponse(url="/seek_job", status_code=status.HTTP_303_SEE_OTHER)

    worker_code = generate_unique_worker_id(db)

    # Save uploads (images only for these fields)
    photo_name = save_upload(current_user.id, photo) if photo and allowed_file(photo.filename, IMAGE_EXTS) else None
    id_front_name = save_upload(current_user.id, id_front) if id_front and allowed_file(id_front.filename, IMAGE_EXTS) else None
    id_back_name = save_upload(current_user.id, id_back) if id_back and allowed_file(id_back.filename, IMAGE_EXTS) else None
    pan_name = save_upload(current_user.id, pan_card) if pan_card and allowed_file(pan_card.filename, IMAGE_EXTS) else None

    # Create profile
    profile = WorkerProfile(
        user_id=current_user.id,
        worker_code=worker_code,
        age=age,
        gender=gender,
        qualification=qualification,
        experience=experience,
        about=about,
        bank_name=bank_name,
        branch=branch,
        ifsc=ifsc,
        account_number=account_number,
        photo=photo_name,
        id_front=id_front_name,
        id_back=id_back_name,
        pan_card=pan_name,
    )
    db.add(profile)

    # Normalize and insert skills (zip keeps array lengths aligned)
    for name, rate, rt in zip(skills, rates, rate_types):
        n = (name or "").strip().lower()
        r = (rate or "").strip()
        t = (rt or "").strip()
        if n and r:
            db.add(Skill(name=n, rate=r, rate_type=t, user_id=current_user.id))

    db.commit()
    return RedirectResponse(url="/welcome", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/edit_worker_profile", response_class=HTMLResponse)
def edit_worker_profile_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    profile = db.query(WorkerProfile).filter_by(user_id=current_user.id).first()
    if not profile:
        raise HTTPException(404, "No profile found")

    showcase_images = db.query(ShowcaseImage).filter_by(user_id=current_user.id).all()
    skills = db.query(Skill).filter_by(user_id=current_user.id).all()

    return templates.TemplateResponse(
        "edit_worker_profile.html",
        {"request": request, "profile": profile, "skills": skills, "showcase_images": showcase_images},
    )


@router.post("/edit_worker_profile")
async def edit_worker_profile_post(
    request: Request,
    qualification: str = Form(...),
    experience: str = Form(...),
    about: str = Form(...),
    gender: Optional[str] = Form(None),
    locality: Optional[str] = Form(None),
    city: Optional[str] = Form(None),
    state: Optional[str] = Form(None),
    zipcode: Optional[str] = Form(None),

    # Multiple showcase images: name="showcase_images"
    showcase_images: List[UploadFile] = File(default=[]),
    # Single optional video: name="video"
    video: UploadFile | None = File(None),

    # Skills replace set:
    skills: List[str] = Form([]),
    rates: List[str] = Form([]),
    rate_types: List[str] = Form([]),

    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    profile = db.query(WorkerProfile).filter_by(user_id=current_user.id).first()
    if not profile:
        return HTMLResponse('{"success": false, "error": "No profile found"}', status_code=404)

    # Update basics
    profile.gender = gender
    profile.qualification = qualification
    profile.experience = experience
    profile.about = about
    profile.locality = locality
    profile.city = city
    profile.state = state
    profile.zipcode = zipcode

    full_location = ", ".join(filter(None, [locality, city, state, zipcode]))

    # Handle profile video (replace if provided)
    if video and video.filename and allowed_file(video.filename, VIDEO_EXTS):
        # delete old if exists
        if profile.video:
            old_path = os.path.join(UPLOAD_ROOT, profile.video)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass
        saved = save_upload(current_user.id, video)
        profile.video = saved

    # Handle showcase images (append new)
    for img in showcase_images:
        if img and img.filename and allowed_file(img.filename, IMAGE_EXTS):
            saved = save_upload(current_user.id, img)
            if saved:
                db.add(ShowcaseImage(user_id=current_user.id, image_url=saved))

    # Replace skills
    db.query(Skill).filter_by(user_id=current_user.id).delete()
    for name, rate, rt in zip(skills, rates, rate_types):
        name = (name or "").strip().lower()
        rate = (rate or "").strip()
        rt = (rt or "").strip()
        if name and rate:
            db.add(Skill(
                name=name, rate=rate, rate_type=rt,
                location=full_location, user_id=current_user.id
            ))

    db.commit()
    # Mirror Flask: return JSON with redirect
    return {"success": True, "redirect": "/seek_job"}


@router.post("/delete_media/{media_type}/{media_id}")
def delete_media(
    media_type: str,
    media_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if media_type not in {"image", "video"}:
        raise HTTPException(400, "Invalid media type")

    if media_type == "video":
        profile = db.get(WorkerProfile, media_id)
        if not profile or profile.user_id != current_user.id:
            raise HTTPException(404, "Video not found or not permitted")
        if profile.video:
            path = os.path.join(UPLOAD_ROOT, profile.video)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
            profile.video = None
            db.commit()
        return RedirectResponse(url="/edit_worker_profile", status_code=status.HTTP_303_SEE_OTHER)

    # image
    img = db.get(ShowcaseImage, media_id)
    if not img or img.user_id != current_user.id:
        raise HTTPException(404, "Image not found or not permitted")
    path = os.path.join(UPLOAD_ROOT, img.image_url)
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass
    db.delete(img)
    db.commit()
    return RedirectResponse(url="/edit_worker_profile", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/logout")
def logout(request: Request):
    # Clear the session like Flask's logout_user()
    request.session.clear()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
