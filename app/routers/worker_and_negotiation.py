# app/routers/worker_and_negotiation.py
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, desc
from sqlalchemy.orm import Session
from app.security.tokens import decode_worker_link
from app.database import get_db
from app.models import (
    User, WorkerProfile, ShowcaseImage, Skill, Rating,
    Job, PriceNegotiation
)
from app.security.auth import get_current_user

router = APIRouter(tags=["worker", "negotiation"])
templates = Jinja2Templates(directory="app/templates")




# ==========================================================
# GET/POST /worker/{worker_id}  (renders view_worker.html)
# ==========================================================
@router.api_route("/worker/{token}", methods=["GET", "POST"], response_class=HTMLResponse)
def view_worker(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    # POST form fields for rating (only read on POST)
    stars: Optional[float] = Form(default=None),
    comment: Optional[str] = Form(default=None),
):
    try:
        payload = decode_worker_link(token)
    except Exception:
        raise HTTPException(status_code=404, detail="Invalid or expired link")

    worker_id = payload["w"]
    job_id = payload.get("j")
    skill_id = payload.get("s")

    user = db.get(User, worker_id)
    if not user:
        raise HTTPException(status_code=404, detail="Worker not found")

    worker_profile = user.worker_profile

    showcase_images = (
        db.query(ShowcaseImage)
        .filter(ShowcaseImage.user_id == user.id)
        .order_by(ShowcaseImage.uploaded_at.desc())
        .all()
    )
    profile = db.query(WorkerProfile).filter(WorkerProfile.user_id == user.id).first()

    # Query params (typed)
    job = db.get(Job, job_id) if job_id else None
    job_title = (job.title.strip().lower() if (job and job.title) else None)

    # Selected skill (fallback to first)
    selected_skill = None
    if skill_id:
        s = db.get(Skill, skill_id)
        if not s or s.user_id != user.id:
            skill_id = None
        else:
            selected_skill = s

    if not selected_skill:
        selected_skill = db.query(Skill).filter(Skill.user_id == user.id).first()

    is_custom = bool(selected_skill and (selected_skill.rate_type or "").strip().lower() == "custom")

    skills = db.query(Skill).filter(Skill.user_id == user.id).all()
    ratings = (
        db.query(Rating)
        .filter(Rating.worker_id == user.id)
        .order_by(Rating.timestamp.desc())
        .all()
    )
    avg_rating = (
        round(sum(r.stars for r in ratings) / len(ratings), 1) if ratings else "No ratings yet"
    )

    # POST: rating submission
    if request.method == "POST" and current_user.id != user.id:
        if stars is None:
            return JSONResponse({"error": "stars_required"}, status_code=400)
        new_rating = Rating(
            worker_id=user.id,
            job_giver_id=current_user.id,
            stars=float(stars),
            comment=comment or "",
        )
        db.add(new_rating)
        db.commit()

        # mimic Flask redirect(url_for(...))
        r = RedirectResponse(
            url=f"/worker/{token}",
            status_code=303,
        )

        return r

    return templates.TemplateResponse(
        "view_worker.html",
        {
            "request": request,
            "user": user,
            "skills": skills,
            "ratings": ratings,
            "avg_rating": avg_rating,
            "worker_profile": worker_profile,
            "showcase_images": showcase_images,
            "profile": profile,
            "selected_skill": selected_skill,
            "job_id": job_id,
            "job_title": job_title,
            "is_custom": is_custom,
            "current_user": current_user,
        },
    )


# ----------------------------
# Helper used by negotiation
# ----------------------------
def _get_or_create_neg(db: Session, provider_id: int, worker_id: int, job_id: Optional[int]):
    neg = (
        db.query(PriceNegotiation)
        .filter_by(provider_id=provider_id, worker_id=worker_id, job_id=job_id)
        .first()
    )
    if not neg:
        neg = PriceNegotiation(provider_id=provider_id, worker_id=worker_id, job_id=job_id)
        db.add(neg)
        db.commit()
    return neg


# ==========================================
# POST /negotiation/open
# ==========================================
@router.post("/negotiation/open")
def negotiation_open(
    request: Request,
    data: dict,  # FastAPI will parse JSON body into dict
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        payload = decode_worker_link(data["worker_token"])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid worker link")

    worker_user_id = payload["w"]
    job_id = payload.get("j")

    neg = _get_or_create_neg(db, current_user.id, worker_user_id, job_id)

    neg.status = "open"
    neg.giver_price = None
    neg.worker_price = None
    db.commit()

    return {"ok": True, "negotiation_id": neg.id, "status": neg.status}


# ==========================================
# POST /negotiation/offer
# ==========================================
@router.post("/negotiation/offer")
def negotiation_offer(
    request: Request,
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    neg_id = int(data.get("negotiation_id"))
    role = data.get("role")  # 'giver' or 'worker'

    # robust decimal parse
    price_raw = str(data.get("price", "0"))
    price = Decimal(price_raw).quantize(Decimal("0.01"))

    neg = db.get(PriceNegotiation, neg_id)
    if not neg:
        raise HTTPException(status_code=404, detail="Negotiation not found")

    # permission checks
    if role == "giver" and neg.provider_id != current_user.id:
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    if role == "worker" and neg.worker_id != current_user.id:
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

    if role == "giver":
        neg.giver_price = price
    else:
        neg.worker_price = price

    if neg.giver_price is not None and neg.worker_price is not None and Decimal(neg.giver_price) == Decimal(neg.worker_price):
        neg.status = "confirmed"
    else:
        neg.status = "open"

    db.commit()
    return {
        "ok": True,
        "status": neg.status,
        "giver_price": str(neg.giver_price) if neg.giver_price is not None else None,
        "worker_price": str(neg.worker_price) if neg.worker_price is not None else None,
    }


# ==========================================
# GET /negotiation/status?negotiation_id=...
# ==========================================
@router.get("/negotiation/status")
def negotiation_status(
    request: Request,
    negotiation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    neg = db.get(PriceNegotiation, negotiation_id)
    if not neg:
        raise HTTPException(status_code=404, detail="Negotiation not found")

    # Only either side can read it
    if current_user.id not in (neg.provider_id, neg.worker_id):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

    return {
        "ok": True,
        "status": neg.status,
        "giver_price": str(neg.giver_price) if neg.giver_price is not None else None,
        "worker_price": str(neg.worker_price) if neg.worker_price is not None else None,
    }


# ==========================================
# POST /negotiation/cancel
# ==========================================
@router.post("/negotiation/cancel")
def negotiation_cancel(
    request: Request,
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from datetime import datetime

    neg_id = int(data.get("negotiation_id"))
    neg = db.get(PriceNegotiation, neg_id)
    if not neg:
        raise HTTPException(status_code=404, detail="Negotiation not found")

    if current_user.id not in (neg.provider_id, neg.worker_id):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

    neg.status = "cancelled"
    neg.cancelled_at = datetime.utcnow()
    db.commit()

    return {"ok": True, "cancelled_at": neg.cancelled_at.isoformat()}


# ==========================================
# GET /negotiation/pending
# ==========================================
@router.get("/negotiation/pending")
def negotiation_pending(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    neg = (
        db.query(PriceNegotiation)
        .filter(
            PriceNegotiation.worker_id == current_user.id,
            PriceNegotiation.status == "open",
        )
        .order_by(desc(PriceNegotiation.updated_at))
        .first()
    )
    if not neg:
        return {"ok": True, "negotiation": None}

    giver = db.get(User, neg.provider_id)
    job = db.get(Job, neg.job_id) if neg.job_id else None

    return {
        "ok": True,
        "negotiation": {
            "id": neg.id,
            "status": neg.status,
            "giver_price": str(neg.giver_price) if neg.giver_price is not None else None,
            "worker_price": str(neg.worker_price) if neg.worker_price is not None else None,
            "job_title": (job.title if job and job.title else "Custom job"),
            "giver_name": (giver.name if giver else "Job Giver"),
        },
    }


# ==========================================
# GET /negotiation/check?worker_id=&job_id=
# ==========================================
@router.get("/negotiation/check")
def negotiation_check(
    request: Request,
    worker_token: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        payload = decode_worker_link(worker_token)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid worker link")

    worker_id = payload["w"]
    job_id = payload.get("j")

    neg = (
        db.query(PriceNegotiation)
        .filter_by(
            provider_id=current_user.id,
            worker_id=worker_id,
            job_id=job_id,
        )
        .first()
    )

    if not neg:
        return {"ok": True, "found": False}

    return {
        "ok": True,
        "found": True,
        "negotiation_id": neg.id,
        "status": neg.status,
        "giver_price": str(neg.giver_price) if neg.giver_price else None,
        "worker_price": str(neg.worker_price) if neg.worker_price else None,
    }
