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
from itertools import zip_longest  # add this import at the top
from app.database import get_db
from app.models import (
    User, WorkerProfile, Skill, ShowcaseImage, Rating
)
from pathlib import Path
import uuid

router = APIRouter(tags=["worker_profile"])
templates = Jinja2Templates(directory="app/templates")

# Adjust this if your uploads path differs
UPLOAD_ROOT = "app/static/uploads/users"
os.makedirs(UPLOAD_ROOT, exist_ok=True)



def get_user_dir(user_id: int, category: str) -> Path:
    """
    category examples:
    - profile
    - ids
    - showcase
    - video
    """
    base = Path(UPLOAD_ROOT) / f"user_{user_id}" / category
    base.mkdir(parents=True, exist_ok=True)
    return base


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


def save_upload(user_id: int, file: UploadFile, category: str) -> Optional[str]:
    if not file or not file.filename:
        return None

    ext = os.path.splitext(file.filename)[1].lower()
    safe_name = f"{uuid.uuid4().hex}{ext}"

    user_dir = get_user_dir(user_id, category)
    dest_path = user_dir / safe_name

    with open(dest_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    # return RELATIVE path for DB
    return f"users/user_{user_id}/{category}/{safe_name}"

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
        avg_rating_text = f"{avg} / 5"
    else:
        avg_rating_text = "No ratings yet"

    # Skills
    skills = db.query(Skill).filter_by(user_id=user.id).all()
    skills_str = ", ".join(s.name for s in skills) if skills else "None"
    skill_chips = "".join(
        f"<span class='skill-pill'>{(s.name or '').title()}</span>"
        for s in skills
    )

    # Ratings list HTML
    ratings_html = ""
    for r in ratings:
        date_str = r.timestamp.strftime("%d %b %Y") if getattr(r, "timestamp", None) else ""
        comment = (r.comment or "").replace("<", "&lt;").replace(">", "&gt;")
        ratings_html += f"""
        <div class="rating-row">
          <div class="rating-score">
            <span class="rating-score-main">{r.stars:.1f}</span>
            <span class="rating-score-sub">★</span>
          </div>
          <div class="rating-row-body">
            <div class="rating-row-top">
              <span class="rating-row-name">{comment or "No comment"}</span>
            </div>
            <div class="rating-row-meta">{date_str}</div>
          </div>
        </div>
        """

    # Showcase
    showcase = (
        db.query(ShowcaseImage)
        .filter_by(user_id=user.id)
        .order_by(ShowcaseImage.uploaded_at.desc())
        .all()
    )
    video_item = profile.video

    photo_url = f"/static/uploads/{profile.photo}" if profile.photo else "/static/default_profile.jpg"
    gender = profile.gender or "Not specified"

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <title>My Worker Profile - Sahayi</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
        <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&display=swap" rel="stylesheet">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css" rel="stylesheet">
        <style>
            :root {{
              --sahayi-blue: #2563eb;
              --sahayi-blue-soft: #dbeafe;
              --sahayi-bg: #f3f4f6;
            }}

            * {{
              box-sizing: border-box;
            }}

            body {{
                margin: 0;
                font-family: 'Poppins', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
                background:
                    radial-gradient(circle at 0% 0%, #e0f2fe 0, #f5f3ff 40%, #f9fafb 75%, #eef2ff 100%);
                color: #111827;
            }}
            body::before {{
                content: "";
                position: fixed;
                inset: -120px;
                background:
                    radial-gradient(circle at 10% 20%, rgba(59,130,246,0.16) 0, transparent 40%),
                    radial-gradient(circle at 80% 10%, rgba(16,185,129,0.16) 0, transparent 45%),
                    radial-gradient(circle at 50% 90%, rgba(139,92,246,0.1) 0, transparent 45%);
                z-index: -1;
            }}

            .shell {{
              max-width: 980px;
              padding-inline: .75rem;
            }}

            /* HERO */
            .profile-hero {{
                border-radius: 30px;
                background: linear-gradient(135deg, #0ea5e9, #2563eb);
                color: #f9fafb;
                padding: 1.6rem 1.7rem 1.4rem;
                box-shadow: 0 22px 55px rgba(15,23,42,0.45);
                position: relative;
                overflow: hidden;
                margin-bottom: 1.8rem;
            }}
            .profile-hero::after {{
                content: "";
                position: absolute;
                right: -40px;
                top: -40px;
                width: 180px;
                height: 180px;
                border-radius: 999px;
                border: 18px solid rgba(191,219,254,0.4);
                opacity: .9;
            }}
            .profile-photo-wrap {{
                position: relative;
                z-index: 1;
            }}
            .profile-photo {{
                width: 96px;
                height: 96px;
                border-radius: 999px;
                object-fit: cover;
                border: 4px solid rgba(255,255,255,0.98);
                box-shadow: 0 18px 40px rgba(15,23,42,0.65);
            }}
            .hero-name-row {{
                display: flex;
                align-items: center;
                flex-wrap: wrap;
                gap: .35rem .6rem;
            }}
            .hero-name-row h1 {{
                font-size: 1.3rem;
                margin: 0;
            }}
            .code-pill {{
                display: inline-flex;
                align-items: center;
                gap: .3rem;
                padding: .16rem .7rem;
                border-radius: 999px;
                background: rgba(15,23,42,0.35);
                font-size: .8rem;
            }}
            .status-pill {{
                display: inline-flex;
                align-items: center;
                gap: .35rem;
                padding: .26rem .8rem;
                border-radius: 999px;
                background: rgba(15,23,42,0.88);
                font-size: .78rem;
            }}
            .status-dot {{
                width: 8px; height: 8px;
                border-radius: 999px;
                background: #22c55e;
            }}
            .hero-subline {{
                font-size: .8rem;
                opacity: .96;
            }}

            .hero-actions {{
                display: flex;
                gap: .5rem;
                justify-content: flex-end;
                margin-top: .6rem;
            }}
            .hero-actions .btn-sm {{
                border-radius: 999px;
                font-size: .78rem;
                padding-inline: .95rem;
            }}

            /* STACKED CARDS */
            .section-card {{
                border-radius: 22px;
                background: linear-gradient(135deg, #ffffff, #f9fafb);
                box-shadow:
                  0 14px 34px rgba(15,23,42,0.08),
                  0 0 0 1px rgba(148,163,184,0.12);
                padding: 1.15rem 1.35rem 1.05rem;
                margin-top: 1.25rem;
                position: relative;
                overflow: hidden;
            }}
            .section-card::before {{
                content: "";
                position: absolute;
                inset-inline: 16px;
                top: 0;
                height: 3px;
                border-radius: 999px;
                background: linear-gradient(90deg, rgba(59,130,246,0.45), rgba(16,185,129,0.4));
                opacity: .55;
            }}
            .section-title {{
                font-size: .92rem;
                font-weight: 600;
                display: flex;
                align-items: center;
                gap: .45rem;
                margin-bottom: .55rem;
                margin-top: .1rem;
            }}
            .section-title-icon {{
                width: 22px;
                height: 22px;
                border-radius: 999px;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                font-size: .9rem;
                background: #eff6ff;
                color: var(--sahayi-blue);
            }}

            .stat-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
                gap: .7rem;
                margin-top: .3rem;
            }}
            .stat-chip {{
                border-radius: 16px;
                border: 1px solid #e5e7eb;
                padding: .6rem .8rem;
                background: #f9fafb;
                font-size: .8rem;
            }}
            .stat-label {{
                text-transform: uppercase;
                letter-spacing: .08em;
                font-size: .7rem;
                color: #9ca3af;
            }}
            .stat-value {{
                font-weight: 600;
                color: #111827;
            }}

            .skill-pill {{
                display: inline-flex;
                align-items: center;
                padding: .3rem .85rem;
                border-radius: 999px;
                font-size: .8rem;
                background: #eff6ff;
                color: #1e293b;
                margin: .18rem .3rem .18rem 0;
                box-shadow: 0 4px 8px rgba(148,163,184,0.3);
            }}

            /* RATINGS */
            .rating-top {{
                display: flex;
                align-items: center;
                gap: 1rem;
                margin-bottom: .65rem;
            }}
            .rating-badge {{
                width: 70px;
                height: 70px;
                border-radius: 999px;
                background: linear-gradient(145deg, #3b82f6, #1d4ed8);
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                color: #fff;
                font-weight: 700;
                box-shadow: 0 15px 35px rgba(37,99,235,0.35);
                font-size: .98rem;
            }}
            .rating-badge span.small {{
                font-size: .66rem;
                font-weight: 500;
                opacity: .92;
            }}
            .rating-count-text {{
                font-size: .8rem;
                color: #6b7280;
            }}

            .rating-list {{
                max-height: 260px;
                overflow-y: auto;
                padding-right: .25rem;
                margin-top: .15rem;
            }}
            .rating-row {{
                display: flex;
                gap: .7rem;
                padding: .55rem .7rem;
                border-radius: 16px;
                background: #f9fafb;
                border: 1px solid #e5e7eb;
                font-size: .8rem;
                margin-bottom: .45rem;
            }}
            .rating-score {{
                min-width: 48px;
                height: 48px;
                border-radius: 999px;
                background: #111827;
                color: #facc15;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
            }}
            .rating-score-main {{
                font-size: .98rem;
                font-weight: 600;
            }}
            .rating-score-sub {{
                font-size: .7rem;
                color: #e5e7eb;
            }}
            .rating-row-body {{
                flex: 1;
            }}
            .rating-row-name {{
                font-weight: 500;
            }}
            .rating-row-meta {{
                font-size: .7rem;
                color: #6b7280;
                margin-top: .08rem;
            }}

            /* SHOWCASE */
            .showcase-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
                gap: .7rem;
                margin-top: .25rem;
            }}
            .showcase-thumb {{
                border-radius: 14px;
                overflow: hidden;
                border: 1px solid #e5e7eb;
                background: #f3f4f6;
                box-shadow: 0 10px 18px rgba(15,23,42,0.12);
            }}
            .showcase-thumb img {{
                width: 100%;
                height: 96px;
                object-fit: cover;
                display: block;
            }}

            .video-wrap {{
                border-radius: 16px;
                overflow: hidden;
                border: 1px solid #e5e7eb;
                background: #020617;
                margin-top: .9rem;
            }}

            @media (max-width: 576px) {{
                .profile-hero {{
                    border-radius: 0 0 30px 30px;
                    margin-left: -.75rem;
                    margin-right: -.75rem;
                    margin-bottom: 1.5rem;
                }}
                .hero-actions {{
                    justify-content: flex-start;
                    margin-top: .7rem;
                }}
                .section-card {{
                    border-radius: 20px;
                    padding-inline: 1.05rem;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container shell py-3 py-md-4">

            <!-- HERO -->
            <section class="profile-hero">
                <div class="row g-3 align-items-center">
                    <div class="col-auto profile-photo-wrap">
                        <img src="{photo_url}" class="profile-photo" alt="Profile Photo">
                    </div>
                    <div class="col">
                        <div class="hero-name-row mb-1">
                            <h1>{user.name}</h1>
                            <span class="code-pill">
                                <i class="bi bi-hash"></i> {profile.worker_code}
                            </span>
                        </div>
                        <div class="hero-subline mb-1">
                            <span class="me-3"><strong>Gender:</strong> {gender}</span>
                            <span class="me-3"><strong>Age:</strong> {profile.age or "N/A"}</span>
                        </div>
                        <div class="hero-subline">
                            <i class="bi bi-telephone me-1"></i>{user.phone or "N/A"}
                        </div>
                    </div>
                    <div class="col-12 col-md-auto text-end d-flex flex-column align-items-start align-items-md-end mt-2 mt-md-0">
                        <span class="status-pill mb-2">
                            <span class="status-dot"></span>
                            <span>Available on Sahayi</span>
                        </span>
                        <div class="hero-actions">
                            <a href="/edit_worker_profile" class="btn btn-light btn-sm">
                                <i class="bi bi-pencil-square me-1"></i>Edit
                            </a>
                            <a href="/provide_job" class="btn btn-outline-light btn-sm">
                                <i class="bi bi-eye me-1"></i>Preview search
                            </a>
                        </div>
                    </div>
                </div>
            </section>

            <!-- BASIC INFO -->
            <section class="section-card">
                <div class="section-title">
                    <span class="section-title-icon"><i class="bi bi-badge-ad"></i></span>
                    Profile summary
                </div>
                <div class="stat-grid">
                    <div class="stat-chip">
                        <div class="stat-label">Qualification</div>
                        <div class="stat-value">{profile.qualification or "N/A"}</div>
                    </div>
                    <div class="stat-chip">
                        <div class="stat-label">Experience</div>
                        <div class="stat-value">{profile.experience or "N/A"}</div>
                    </div>
                    <div class="stat-chip">
                        <div class="stat-label">Skills</div>
                        <div class="stat-value">{skills_str}</div>
                    </div>
                </div>
            </section>

            <!-- ABOUT -->
            <section class="section-card">
                <div class="section-title">
                    <span class="section-title-icon"><i class="bi bi-chat-square-text"></i></span>
                    About me
                </div>
                <p class="mb-0 small">
                    {(profile.about or "You have not added an about section yet.").replace("<","&lt;").replace(">","&gt;")}
                </p>
            </section>

            <!-- SKILLS -->
            <section class="section-card">
                <div class="section-title">
                    <span class="section-title-icon"><i class="bi bi-stars"></i></span>
                    Skills & rates
                </div>
                <div class="mb-1">
                    {skill_chips or "<span class='text-muted small'>No skills added yet.</span>"}
                </div>
            </section>

            <!-- RATINGS -->
            <section class="section-card">
                <div class="section-title">
                    <span class="section-title-icon"><i class="bi bi-star-half"></i></span>
                    Ratings
                </div>
                <div class="rating-top">
                    <div class="rating-badge">
                        <div>{avg_rating_text}</div>
                        <span class="small">Average</span>
                    </div>
                    <div class="rating-count-text">
                        {len(ratings)} rating{"s" if len(ratings) != 1 else ""} received
                    </div>
                </div>
                <div class="rating-list">
                    {ratings_html or "<p class='text-muted small mb-0'>You do not have any ratings yet.</p>"}
                </div>
            </section>

            <!-- SHOWCASE -->
            <section class="section-card mb-4">
                <div class="section-title">
                    <span class="section-title-icon"><i class="bi bi-collection-play"></i></span>
                    Showcase
                </div>
                {"<div class='showcase-grid'>" + "".join(
                    f"<div class='showcase-thumb'><img src='/static/uploads/{img.image_url}' alt='Showcase'></div>"
                    for img in showcase
                ) + "</div>" if showcase else "<p class='text-muted small mb-1'>No images uploaded yet.</p>"}
                {("<div class='video-wrap'><video class='w-100' controls><source src='/static/uploads/" + video_item + "' type='video/mp4'></video></div>" if video_item else "")}
            </section>

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

    # arrays from form
    skills: list[str] = Form(default_factory=list, alias="skills[]"),
    rates: list[str] = Form(default_factory=list, alias="rates[]"),
    rate_types: list[str] = Form(default_factory=list, alias="rate_types[]"),
    skill_categories: list[str] = Form(default_factory=list, alias="skill_categories[]"),  # ✅ NEW
    wf_scopes: list[str] = Form(default_factory=list, alias="wf_scope[]"),                # ✅ NEW


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
    photo_name = save_upload(current_user.id, photo, "profile") if photo and allowed_file(photo.filename,IMAGE_EXTS) else None
    id_front_name = save_upload(current_user.id, id_front, "ids") if id_front and allowed_file(id_front.filename,IMAGE_EXTS) else None
    id_back_name = save_upload(current_user.id, id_back, "ids") if id_back and allowed_file(id_back.filename,IMAGE_EXTS) else None
    pan_name = save_upload(current_user.id, pan_card, "ids") if pan_card and allowed_file(pan_card.filename,IMAGE_EXTS) else None

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

    # Insert skills with category; zip_longest tolerates length mismatches
    for name, rate, rt, cat in zip_longest(skills, rates, rate_types, skill_categories, fillvalue=None):
        n = (name or "").strip().lower()
        r = (rate or "").strip()
        t = (rt or "").strip() or None
        c = (cat or "Other").strip()

        if not n:
            continue

        if c.lower() in ("work from home", "wfh", "remote", "sahayi from home"):
            # WFH → no rate
            db.add(Skill(
                name=n,
                category=c,
                rate=None,
                rate_type=None,
                user_id=current_user.id
            ))
        else:
            if not r:
                continue
            db.add(Skill(
                name=n,
                category=c,
                rate=r,
                rate_type=t,
                user_id=current_user.id
            ))

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

    # Skills replace set (repeated keys)
    skills: List[str] = Form([]),
    rates: List[str] = Form([]),
    rate_types: List[str] = Form([]),
    categories: List[str] = Form([]),  # existing
    wf_scopes: List[str] = Form([]),   # ✅ NEW


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
        if profile.video:
            old_path = os.path.join("app/static/uploads", profile.video)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass

        saved = save_upload(current_user.id, video, "video")
        profile.video = saved

    # Handle showcase images (append new)
    for img in showcase_images:
        if img and img.filename and allowed_file(img.filename, IMAGE_EXTS):
            saved = save_upload(current_user.id, img, "showcase")
            if saved:
                db.add(ShowcaseImage(user_id=current_user.id, image_url=saved))

    # Replace skills (now includes category)
    db.query(Skill).filter_by(user_id=current_user.id).delete(synchronize_session=False)

    # Insert / replace skills with category and optional WFH scope
    # use zip_longest so mismatched list lengths don't crash
    for name, rate, rt, cat, wf_scope in zip_longest(
            skills, rates, rate_types, categories, wf_scopes, fillvalue=None):

        n = (name or "").strip()
        r = (rate or "").strip()
        t = (rt or "").strip() or None
        c = (cat or "Other").strip()
        wf = (wf_scope or "").strip()

        if not n:
            # skip empty skill names
            continue

        # Work From Home handling: allow missing rate and interpret scope
        location_field = None
        if c.lower() == "work from home" or c.lower() == "workfromhome":
            # interpret wf scope:
            # - 'everywhere' => location_field = 'everywhere'
            # - numeric => 'radius:<n>'
            if wf:
                if wf.lower() == "everywhere":
                    location_field = "everywhere"
                else:
                    try:
                        # allow floats/ints as radius
                        num = float(wf)
                        location_field = f"radius:{num}"
                    except Exception:
                        location_field = wf  # whatever was posted (fallback)
            else:
                # default scope when none provided
                location_field = "everywhere"

            # provide safe fallback rate/rate_type so DB non-null constraints won't reject
            # WFH has NO predefined price
            r = None
            t = None


        # Non-WFH: require a rate to be present (existing behavior). If no rate given, skip to match earlier logic
        else:
            if not r:
                # skip skills with no rate for non-WFH categories (preserves previous behaviour)
                continue

        # Persist normalized skill (lowercasing name preserved? keep original casing if you prefer)
        db.add(Skill(
            name=n.lower(),       # you used .lower() earlier; keep consistent
            category=c,
            rate=r,
            rate_type=t,
            location=location_field or None,
            user_id=current_user.id
        ))


    db.commit()
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
            path = os.path.join("app/static/uploads", profile.video)
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
    path = os.path.join("app/static/uploads", img.image_url)
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
