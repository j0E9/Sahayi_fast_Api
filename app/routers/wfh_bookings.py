# app/routers/wfh_bookings.py
from fastapi import APIRouter, Depends, HTTPException, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Booking, User, PlatformProfit, WalletTransaction,WFHDeliverable,WFHProjectUpdate,WFHDisputeFile,WFHDispute,WFHDisputeResponse
from fastapi.templating import Jinja2Templates
from app.services.wallet import add_ledger_row
from sqlalchemy import or_
from app.services.wfh_auto_cancel import enforce_cancel_window
from app.utils.IST_Time import ist_now
from decimal import Decimal
from app.services.platform_balance import increment_platform_balance
from sqlalchemy.orm import joinedload
from fastapi import UploadFile, File
import os
from pathlib import Path
import shutil
from datetime import datetime,timedelta
from PIL import Image
import pikepdf
from typing import List
from app.security.auth import get_current_user


router = APIRouter(tags=["wfh"])
templates = Jinja2Templates(directory="app/templates")

WFH_STORAGE_ROOT = Path("storage/wfh_jobs")
WFH_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)


MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB

BLOCKED_EXTENSIONS = {
    "exe","bat","cmd","sh","ps1","js","vbs","jar","msi",
    "php","py","rb","pl","html","svg"
}

ALLOWED_MIME = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "application/pdf",
    "application/zip"
}




def finalize_wfh_payout(db: Session, booking: Booking):
    if booking.escrow_released:
        return

    base = booking.escrow_amount or Decimal("0.00")

    # Example commissions
    giver_commission = (base * Decimal("0.05")).quantize(Decimal("0.01"))
    worker_commission = (base * Decimal("0.05")).quantize(Decimal("0.01"))

    platform_profit = giver_commission + worker_commission

    # worker only loses HIS 5%, not giver 5%
    worker_net = (base - worker_commission).quantize(Decimal("0.01"))

    commission_ref = f"wfh_commission_{booking.id}"
    escrow_ref = f"escrow_release_{booking.id}"
    payout_ref = f"wfh_complete_{booking.id}"

    # 1️⃣ PLATFORM COMMISSION (ONLY ONCE)
    if not db.query(PlatformProfit).filter_by(reference=commission_ref).first():
        db.add(
            PlatformProfit(
                booking_id=booking.id,
                type="commission",
                direction="credit",
                amount=platform_profit,
                giver_commission=giver_commission,
                worker_commission=worker_commission,
                on_hold=False,
                reference=commission_ref,
                meta={"booking_id": booking.id},
            )
        )
        increment_platform_balance(db, platform_profit)

    # 2️⃣ ESCROW RELEASE (REPORTING)
    if not db.query(PlatformProfit).filter_by(reference=escrow_ref).first():
        db.add(
            PlatformProfit(
                booking_id=booking.id,
                type="escrow_release",
                direction="debit",
                amount=base,
                on_hold=False,
                reference=escrow_ref,
                meta={"booking_id": booking.id},
            )
        )

    # 3️⃣ WORKER WALLET CREDIT
    if not db.query(WalletTransaction).filter_by(reference=payout_ref).first():
        add_ledger_row(
            db=db,
            user_id=booking.worker_id,
            amount_rupees=worker_net,
            kind="wfh_payout",
            reference=payout_ref,
            meta={"booking_id": booking.id},
        )

    booking.status = "WFH_COMPLETED"
    booking.escrow_locked = False
    booking.escrow_released = True

# -------------------------------------------------
# 1️⃣ WFH BOOKINGS LIST
# -------------------------------------------------
@router.get("/wfh/bookings", response_class=HTMLResponse)
def worker_wfh_bookings(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bookings = (
        db.query(Booking)
        .filter(
            Booking.worker_id == current_user.id,
            Booking.booking_type == "wfh",
            Booking.status.in_(["WFH_PENDING_PRICE", "WFH_NEGOTIATING"]),
        )
        .order_by(Booking.id.desc())
        .all()
    )

    return templates.TemplateResponse(
        "worker_wfh_bookings.html",
        {"request": request, "bookings": bookings},
    )


@router.get("/api/wfh/context")
def wfh_context(
    request: Request,
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    if not user_id:
        return {"has_active_wfh": False}

    ACTIVE_STATUSES = (
        "WFH_PENDING_PRICE",
        "WFH_NEGOTIATING",
        "WFH_CONFIRMED",
        "WFH_IN_PROGRESS",
    )

    active_booking = (
        db.query(Booking)
        .filter(
            Booking.booking_type == "wfh",
            Booking.status.in_(ACTIVE_STATUSES),
            or_(
                Booking.worker_id == user_id,
                Booking.provider_id == user_id
            )
        )
        .order_by(Booking.id.desc())
        .first()
    )

    if not active_booking:
        return {"has_active_wfh": False}

    if active_booking.worker_id == user_id:
        redirect_url = f"/wfh/booking/{active_booking.id}"
    else:
        redirect_url = f"/wfh/giver/booking/{active_booking.id}"

    return {
        "has_active_wfh": True,
        "redirect_url": redirect_url
    }


@router.get("/wfh/booking/{booking_id}/status")
def wfh_booking_status(
    booking_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    auto_approve_reviews(db)

    booking = db.get(Booking, booking_id)

    if not booking or booking.booking_type != "wfh":
        raise HTTPException(status_code=404)

    if current_user.id not in [booking.worker_id, booking.provider_id]:
        raise HTTPException(status_code=403)

    enforce_cancel_window(booking)
    db.commit()

    # ✅ EXPIRE job_giver update request when deadline passes
    req = db.query(WFHProjectUpdate).filter(
        WFHProjectUpdate.booking_id == booking.id,
        WFHProjectUpdate.status == "requested",
        WFHProjectUpdate.request_origin == "job_giver",
        WFHProjectUpdate.request_deadline.isnot(None)
    ).first()

    if req:
        now = ist_now()
        if now >= req.request_deadline:
            req.status = "expired"
            req.request_deadline = None
            db.commit()
            db.refresh(booking)

    now = ist_now()
    deadline = booking.deadline

    early_cancel_allowed = (
            booking.status in ("WFH_CONFIRMED", "WFH_IN_PROGRESS")
            and booking.payment_completed
            and not booking.cancel_window_closed
    )

    final_cancel_allowed = (
            booking.status == "WFH_IN_PROGRESS"
            and booking.payment_completed
            and deadline
            and now >= deadline
            and current_user.id == booking.provider_id
            and not booking.completion_requested_once
    )

    return {
        "rate": booking.rate,
        "expected_price": booking.expected_price,
        "status": booking.status,
        "early_cancel_allowed": early_cancel_allowed,
        "final_cancel_allowed": final_cancel_allowed,
        "approval_allowed": approval_allowed(booking),
        "completion_requested": booking.status == "WFH_REVIEW_PENDING",
    }


@router.post("/wfh/booking/{booking_id}/extend-update")
def extend_update_deadline(
    booking_id: int,
    days: int = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    booking = db.get(Booking, booking_id)
    if not booking or booking.booking_type != "wfh":
        raise HTTPException(404)

    if current_user.id != booking.provider_id:
        raise HTTPException(403)

    req = db.query(WFHProjectUpdate).filter(
        WFHProjectUpdate.booking_id == booking_id,
        WFHProjectUpdate.request_origin == "job_giver",
        WFHProjectUpdate.status.in_(["requested", "expired"])
    ).order_by(WFHProjectUpdate.id.desc()).first()

    if not req:
        raise HTTPException(400, "No update request exists to extend")

    req.status = "requested"
    req.request_deadline = ist_now() + timedelta(days=int(days))

    db.commit()

    return RedirectResponse(f"/wfh/giver/booking/{booking_id}", status_code=303)


@router.post("/wfh/booking/{booking_id}/cancel-missed-update")
def cancel_due_to_missed_update(
    booking_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    booking = db.get(Booking, booking_id)
    if not booking or booking.booking_type != "wfh":
        raise HTTPException(404)

    if current_user.id != booking.provider_id:
        raise HTTPException(403)

    if not booking.payment_completed:
        raise HTTPException(400, "Payment not completed")

    # ✅ Must have expired update request
    expired = db.query(WFHProjectUpdate).filter(
        WFHProjectUpdate.booking_id == booking_id,
        WFHProjectUpdate.request_origin == "job_giver",
        WFHProjectUpdate.status == "expired"
    ).first()

    if not expired:
        raise HTTPException(400, "Update request is not expired yet")

    # ✅ Block refund cancel if worker submitted completion
    submitted_completion = db.query(WFHProjectUpdate).filter(
        WFHProjectUpdate.booking_id == booking_id,
        WFHProjectUpdate.status == "approval_requested"
    ).first()

    if submitted_completion:
        raise HTTPException(400, "Worker requested completion. Use dispute.")

    # ✅ Refund escrow (your existing service)
    from app.services.wfh_refund import refund_wfh_escrow

    refund_wfh_escrow(
        db=db,
        booking=booking,
        reason="missed_update_deadline",
    )

    booking.status = "WFH_CANCELLED"
    booking.escrow_locked = False

    write_audit(booking.id, "JOB_CANCELLED_MISSED_UPDATE_DEADLINE")

    db.commit()
    delete_wfh_files(db, booking.id)

    return RedirectResponse("/welcome", status_code=303)


def approval_allowed(booking: Booking) -> bool:
    if booking.status not in ("WFH_CONFIRMED", "WFH_IN_PROGRESS"):
        return False

    if not booking.deadline:
        return False

    # 🔥 FIX: fallback start time
    start_time = booking.started_at or booking.start_date
    if not start_time:
        return False

    now = ist_now()
    total = (booking.deadline - start_time).total_seconds()
    elapsed = (now - start_time).total_seconds()

    return elapsed >= total * 0.5


def ensure_system_update(db: Session, booking: Booking):
    if booking.status != "WFH_IN_PROGRESS":
        return

    if not approval_allowed(booking):
        return

    exists = db.query(WFHProjectUpdate).filter(
        WFHProjectUpdate.booking_id == booking.id,
        WFHProjectUpdate.status == "requested",
        WFHProjectUpdate.request_origin == "system"
    ).first()

    if exists:
        return

    db.add(
        WFHProjectUpdate(
            booking_id=booking.id,
            requested_by=booking.provider_id,
            status="requested",
            request_origin="system",
            request_deadline=None
        )
    )
    db.commit()



@router.get("/wfh/giver/bookings", response_class=HTMLResponse)
def giver_wfh_bookings(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bookings = (
        db.query(Booking)
        .filter(
            Booking.provider_id == current_user.id,
            Booking.booking_type == "wfh",
        )
        .order_by(Booking.id.desc())
        .all()
    )

    return templates.TemplateResponse(
        "giver_wfh_bookings.html",
        {
            "request": request,
            "bookings": bookings,
        },
    )

@router.get("/wfh/giver/booking/{booking_id}", response_class=HTMLResponse)
def giver_wfh_booking_detail(
    booking_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    booking = (
        db.query(Booking)
        .options(joinedload(Booking.project_updates))
        .filter(Booking.id == booking_id)
        .first()
    )

    if not booking or booking.booking_type != "wfh":
        raise HTTPException(status_code=404)

    if booking.provider_id != current_user.id:
        raise HTTPException(status_code=403)

    # 🔒 enforce once
    enforce_cancel_window(booking)
    db.commit()
    now = ist_now()
    deadline = booking.deadline

    # ✅ expire request if deadline passed (giver page)
    req = db.query(WFHProjectUpdate).filter(
        WFHProjectUpdate.booking_id == booking.id,
        WFHProjectUpdate.status == "requested",
        WFHProjectUpdate.request_origin == "job_giver",
        WFHProjectUpdate.request_deadline.isnot(None)
    ).first()

    if req and ist_now() >= req.request_deadline:
        req.status = "expired"
        req.request_deadline = None
        db.commit()
        db.refresh(booking)

    has_pending_update_request = (
            db.query(WFHProjectUpdate)
            .filter(
                WFHProjectUpdate.booking_id == booking.id,
                WFHProjectUpdate.status == "requested",
                WFHProjectUpdate.request_origin == "job_giver"
            )
            .first()
            is not None
    )

    ensure_system_update(db, booking)

    dispute = db.query(WFHDispute).filter(
        WFHDispute.booking_id == booking.id
    ).first()

    responses = []
    if dispute:
        responses = (
            db.query(WFHDisputeResponse)
            .options(joinedload(WFHDisputeResponse.files))
            .filter(WFHDisputeResponse.dispute_id == dispute.id)
            .order_by(WFHDisputeResponse.created_at.asc())
            .all()
        )

    return templates.TemplateResponse(
        "giver_wfh_booking_detail.html",
        {
            "request": request,
            "booking": booking,
            "worker": booking.worker,

            # ✅ ADD THESE THREE
            "dispute": dispute,
            "dispute_responses": responses,
            "current_user": current_user,

            "early_cancel_allowed": (
                    booking.status in ("WFH_CONFIRMED", "WFH_IN_PROGRESS")
                    and booking.payment_completed
                    and not booking.cancel_window_closed
            ),
            "final_cancel_allowed": (
                    booking.status == "WFH_IN_PROGRESS"
                    and booking.payment_completed
                    and deadline
                    and now >= deadline
                    and not booking.completion_requested_once
            ),
            "has_pending_update_request": has_pending_update_request,
            "request_update_allowed": approval_allowed(booking),
        },
    )


def pre_payment_cancel_allowed(booking: Booking, user_id: int) -> bool:
    return (
        not booking.payment_completed
        and booking.status in (
            "WFH_PENDING_PRICE",
            "WFH_NEGOTIATING",
            "WFH_CONFIRMED",
        )
        and user_id in (booking.worker_id, booking.provider_id)
    )


@router.post("/wfh/dispute/{dispute_id}/respond")
def respond_to_dispute(
    dispute_id: int,
    message: str = Form(...),
    proof: List[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dispute = db.get(WFHDispute, dispute_id)
    if not dispute:
        raise HTTPException(404)

    booking = db.get(Booking, dispute.booking_id)

    # ❌ Dispute opener cannot respond to their own dispute
    if current_user.id == dispute.raised_by:
        raise HTTPException(403, "You already raised this dispute")

    # ✅ Block multiple responses from same user
    already = db.query(WFHDisputeResponse).filter(
        WFHDisputeResponse.dispute_id == dispute.id,
        WFHDisputeResponse.user_id == current_user.id
    ).first()

    if already:
        raise HTTPException(400, "You already responded to this dispute")

    if current_user.id not in (booking.worker_id, booking.provider_id):
        raise HTTPException(403)

    response = WFHDisputeResponse(
        dispute_id=dispute.id,
        user_id=current_user.id,
        message=message
    )

    db.add(response)
    db.flush()  # get response.id

    if proof:
        for f in proof:
            if f and f.filename:
                path = save_dispute_file(
                    file=f,
                    booking=booking,
                    uploaded_by=current_user.id,
                    purpose="response"
                )

                db.add(
                    WFHDisputeFile(
                        dispute_id=dispute.id,
                        response_id=response.id,
                        file_url=path
                    )
                )

    write_audit(
        booking.id,
        f"DISPUTE_RESPONSE by user_id={current_user.id}"
    )

    db.commit()
    return RedirectResponse("/welcome", status_code=303)


@router.post("/wfh/booking/{booking_id}/cancel")
def cancel_wfh_booking(
    booking_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    booking = db.get(Booking, booking_id)

    if not booking or booking.booking_type != "wfh":
        raise HTTPException(404)

    # ✅ PRE-PAYMENT CANCEL (WORKER OR JOB GIVER)
    if pre_payment_cancel_allowed(booking, current_user.id):
        booking.status = "WFH_CANCELLED"
        write_audit(booking.id, "JOB_CANCELLED_BEFORE_PAYMENT")
        db.commit()
        delete_wfh_files(db, booking.id)
        return RedirectResponse("/welcome", status_code=303)


    # 🚫 BLOCK CANCELLATION AFTER COMPLETION REQUEST
    if booking.status == "WFH_COMPLETION_REQUESTED":
        raise HTTPException(
            status_code=400,
            detail="Completion requested. Cannot cancel now."
        )

    if booking.completion_requested_once:
        raise HTTPException(
            status_code=400,
            detail="Cancellation blocked: worker has already requested completion"
        )

    if booking.escrow_released:
        raise HTTPException(400, "Booking already cancelled")

    if not booking.payment_completed:
        raise HTTPException(400, "Payment not completed")

    if booking.status != "WFH_IN_PROGRESS":
        raise HTTPException(
            status_code=400,
            detail="Final cancellation not allowed after work submission"
        )

    enforce_cancel_window(booking)
    db.commit()

    now = ist_now()

    # -------- EARLY CANCEL (1/16 window, before deadline) --------
    if not booking.cancel_window_closed and (not booking.deadline or now < booking.deadline):
        if current_user.id not in (booking.provider_id, booking.worker_id):
            raise HTTPException(403)

    # -------- FINAL CANCEL (after deadline, provider only) --------
    else:
        if current_user.id != booking.provider_id:
            raise HTTPException(403, "Only job giver can cancel after deadline")

        if not booking.deadline or now < booking.deadline:
            raise HTTPException(400, "Final cancellation allowed only after deadline")

    # -------- REFUND (ONCE) --------
    from app.services.wfh_refund import refund_wfh_escrow

    refund_wfh_escrow(
        db=db,
        booking=booking,
        reason="manual_cancel",
    )

    write_audit(booking.id, "JOB_CANCELLED")

    db.commit()
    delete_wfh_files(db, booking.id)
    return RedirectResponse("/welcome", status_code=303)


@router.post("/wfh/booking/{booking_id}/request-revision")
def request_revision(
    booking_id: int,
    reason: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    booking = db.get(Booking, booking_id)

    if not booking or booking.booking_type != "wfh":
        raise HTTPException(404)

    if current_user.id != booking.provider_id:
        raise HTTPException(403)

    if booking.status not in ("WFH_REVIEW_PENDING", "WFH_REVISION_REQUESTED"):
        raise HTTPException(400, "Cannot request revision now")

    revision_count = db.query(WFHProjectUpdate).filter(
        WFHProjectUpdate.booking_id == booking.id,
        WFHProjectUpdate.status == "revision_requested"
    ).count()

    if revision_count >= 3:
        raise HTTPException(400, "Maximum revisions reached")

    # 🔴 DO NOT increment revision_count (it's derived)
    booking.status = "WFH_REVISION_REQUESTED"
    booking.review_deadline = None

    # Mark latest submitted update as revision_requested
    last_update = (
        db.query(WFHProjectUpdate)
        .filter(
            WFHProjectUpdate.booking_id == booking_id,
            WFHProjectUpdate.status == "approval_requested"
        )
        .order_by(WFHProjectUpdate.created_at.desc())
        .first()
    )

    if last_update:
        last_update.status = "revision_requested"

    write_audit(
        booking_id,
        "JOB_GIVER requested revision"
    )

    db.commit()

    return RedirectResponse(
        f"/wfh/giver/booking/{booking_id}",
        status_code=303
    )



@router.post("/wfh/booking/{booking_id}/request-location")
def request_location(
    booking_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    booking = db.get(Booking, booking_id)

    if not booking or booking.booking_type != "wfh":
        raise HTTPException(404)

    if current_user.id != booking.worker_id:
        raise HTTPException(403)

    if booking.location_request_status in ("requested", "approved"):
        return {"ok": True}

    booking.location_request_status = "requested"
    booking.location_rejected_reason = None
    db.commit()

    return {"ok": True}

@router.post("/wfh/booking/{booking_id}/reject-location")
def reject_location(
    booking_id: int,
    reason: str = Form("Not approved"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    booking = db.get(Booking, booking_id)

    if not booking or booking.booking_type != "wfh":
        raise HTTPException(404)

    if current_user.id != booking.provider_id:
        raise HTTPException(403)

    booking.location_request_status = "rejected"
    booking.location_rejected_reason = reason
    db.commit()

    return RedirectResponse(
        f"/wfh/giver/booking/{booking_id}",
        status_code=303
    )
@router.post("/wfh/booking/{booking_id}/approve-location")
def approve_location(
    booking_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    booking = db.get(Booking, booking_id)

    if not booking or booking.booking_type != "wfh":
        raise HTTPException(404)

    if current_user.id != booking.provider_id:
        raise HTTPException(403)

    if booking.location_request_status != "requested":
        raise HTTPException(400, "No pending location request")

    booking.location_request_status = "approved"
    booking.location_shared_at = ist_now()
    booking.location_rejected_reason = None

    db.commit()

    return RedirectResponse(
        f"/wfh/giver/booking/{booking_id}",
        status_code=303
    )


@router.post("/wfh/booking/{booking_id}/approve-complete")
def approve_wfh_completion(
    booking_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    booking = db.get(Booking, booking_id)

    if not booking or booking.booking_type != "wfh":
        raise HTTPException(404)

    if current_user.id != booking.provider_id:
        raise HTTPException(403)

    if booking.status != "WFH_REVIEW_PENDING":
        raise HTTPException(400, "Not in review state")

    if booking.escrow_released:
        raise HTTPException(400, "Already completed")

    finalize_wfh_payout(db, booking)

    write_audit(booking.id, "JOB_COMPLETED_APPROVED")

    db.commit()
    delete_wfh_files(db, booking.id)

    return RedirectResponse("/welcome", status_code=303)





@router.post("/wfh/giver/booking/{booking_id}/submit-price")
def submit_giver_price(
    booking_id: int,
    price: float = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    booking = db.get(Booking, booking_id)

    if not booking or booking.booking_type != "wfh":
        raise HTTPException(status_code=404)

    if booking.provider_id != current_user.id:
        raise HTTPException(status_code=403)

    booking.expected_price = Decimal(price)

    if booking.rate is None:
        booking.status = "WFH_NEGOTIATING"
    elif booking.rate == booking.expected_price:
        booking.status = "WFH_CONFIRMED"
    else:
        booking.status = "WFH_NEGOTIATING"

    db.commit()

    return RedirectResponse(
        url=f"/wfh/giver/booking/{booking_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/wfh", response_class=HTMLResponse)
def wfh_dashboard(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ACTIVE_STATUSES = (
        "WFH_PENDING_PRICE",
        "WFH_NEGOTIATING",
        "WFH_CONFIRMED",
        "WFH_IN_PROGRESS",
    )

    COMPLETED_STATUSES = ("WFH_COMPLETED",)
    CANCELLED_STATUSES = ("WFH_CANCELLED",)
    DISPUTED_STATUSES = ("WFH_DISPUTED",)

    # ---- JOB GIVER ----
    given_active = db.query(Booking).filter(
        Booking.provider_id == current_user.id,
        Booking.booking_type == "wfh",
        Booking.status.in_(ACTIVE_STATUSES),
    ).order_by(Booking.id.desc()).all()

    given_completed = db.query(Booking).filter(
        Booking.provider_id == current_user.id,
        Booking.booking_type == "wfh",
        Booking.status.in_(COMPLETED_STATUSES),
    ).order_by(Booking.id.desc()).all()

    given_cancelled = db.query(Booking).filter(
        Booking.provider_id == current_user.id,
        Booking.booking_type == "wfh",
        Booking.status.in_(CANCELLED_STATUSES),
    ).order_by(Booking.id.desc()).all()

    given_disputed = (
        db.query(Booking)
        .options(joinedload(Booking.dispute))
        .filter(
            Booking.provider_id == current_user.id,
            Booking.booking_type == "wfh",
            Booking.status.in_(DISPUTED_STATUSES),
        )
        .order_by(Booking.id.desc())
        .all()
    )

    # ---- WORKER ----
    received_active = db.query(Booking).filter(
        Booking.worker_id == current_user.id,
        Booking.booking_type == "wfh",
        Booking.status.in_(ACTIVE_STATUSES),
    ).order_by(Booking.id.desc()).all()

    received_completed = db.query(Booking).filter(
        Booking.worker_id == current_user.id,
        Booking.booking_type == "wfh",
        Booking.status.in_(COMPLETED_STATUSES),
    ).order_by(Booking.id.desc()).all()

    received_cancelled = db.query(Booking).filter(
        Booking.worker_id == current_user.id,
        Booking.booking_type == "wfh",
        Booking.status.in_(CANCELLED_STATUSES),
    ).order_by(Booking.id.desc()).all()

    received_disputed = (
        db.query(Booking)
        .options(joinedload(Booking.dispute))
        .filter(
            Booking.worker_id == current_user.id,
            Booking.booking_type == "wfh",
            Booking.status.in_(DISPUTED_STATUSES),
        )
        .order_by(Booking.id.desc())
        .all()
    )

    return templates.TemplateResponse(
        "wfh_dashboard.html",
        {
            "request": request,

            "given_active": given_active,
            "given_completed": given_completed,
            "given_cancelled": given_cancelled,
            "given_disputed": given_disputed,

            "received_active": received_active,
            "received_completed": received_completed,
            "received_cancelled": received_cancelled,
            "received_disputed": received_disputed,

            "has_worker_profile": bool(
                received_active
                or received_completed
                or received_cancelled
                or received_disputed
            ),
        },
    )



# -------------------------------------------------
# 2️⃣ WFH BOOKING DETAILS PAGE
# -------------------------------------------------
@router.get("/wfh/booking/{booking_id}", response_class=HTMLResponse)
def wfh_booking_detail(
        booking_id: int,
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
):
    booking = (
        db.query(Booking)
        .options(joinedload(Booking.project_updates))
        .filter(Booking.id == booking_id)
        .first()
    )

    if not booking or booking.booking_type != "wfh":
        raise HTTPException(status_code=404, detail="Booking not found")

    if booking.worker_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    # ✅ single, clean auto-cancel
    enforce_cancel_window(booking)
    db.commit()

    ensure_system_update(db, booking)

    dispute = db.query(WFHDispute).filter(
        WFHDispute.booking_id == booking.id
    ).first()

    responses = []
    if dispute:
        responses = (
            db.query(WFHDisputeResponse)
            .options(joinedload(WFHDisputeResponse.files))
            .filter(WFHDisputeResponse.dispute_id == dispute.id)
            .order_by(WFHDisputeResponse.created_at.asc())
            .all()
        )

    return templates.TemplateResponse(
        "worker_wfh_booking_detail.html",
        {
            "request": request,
            "booking": booking,
            "provider": booking.provider,

            # ✅ ADD THESE
            "dispute": dispute,
            "dispute_responses": responses,
            "current_user": current_user,

            "early_cancel_allowed": (
                    booking.status in ("WFH_CONFIRMED", "WFH_IN_PROGRESS")
                    and booking.payment_completed
                    and not booking.cancel_window_closed
            ),
            "approval_allowed": approval_allowed(booking),
        },
    )


# -------------------------------------------------
# 3️⃣ SUBMIT PRICE FOR WFH BOOKING
# -------------------------------------------------
@router.post("/wfh/booking/{booking_id}/submit-price")
def submit_wfh_price(
    booking_id: int,
    price: float = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    booking = db.get(Booking, booking_id)

    if not booking or booking.booking_type != "wfh":
        raise HTTPException(status_code=404)

    if booking.worker_id != current_user.id:
        raise HTTPException(status_code=403)

    booking.rate = Decimal(price)
    booking.rate_type = "fixed"

    # negotiation mode
    if booking.expected_price is None:
        booking.status = "WFH_NEGOTIATING"
    elif booking.rate == booking.expected_price:
        booking.status = "WFH_CONFIRMED"
    else:
        booking.status = "WFH_NEGOTIATING"

    db.commit()

    return RedirectResponse(
        url=f"/wfh/booking/{booking_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/wfh/booking/{booking_id}/submit-deliverable")
def submit_deliverable(
    booking_id: int,
    deliverable_type: str = Form(...),  # website | design | cake | etc
    message: str = Form(None),
    preview_url: str = Form(None),      # drive / github / website
    file_url: str = Form(None),         # later replace with upload
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    booking = db.get(Booking, booking_id)

    if not booking or booking.booking_type != "wfh":
        raise HTTPException(404)

    if booking.worker_id != current_user.id:
        raise HTTPException(403)

    if booking.status not in ("WFH_IN_PROGRESS", "WFH_REVISION_REQUESTED"):
        raise HTTPException(400, "Cannot submit work now")

    # 🔢 versioning
    last = (
        db.query(WFHDeliverable)
        .filter(WFHDeliverable.booking_id == booking_id)
        .order_by(WFHDeliverable.version.desc())
        .first()
    )
    next_version = 1 if not last else last.version + 1

    deliverable = WFHDeliverable(
        booking_id=booking_id,
        version=next_version,
        submitted_by=current_user.id,
        type=deliverable_type,
        message=message,
        preview_url=preview_url,
        file_url=file_url,
        status="submitted",
    )

    db.add(deliverable)

    # Deliverable submission is NOT approval
    booking.status = "WFH_IN_PROGRESS"

    db.commit()

    return RedirectResponse(
        f"/wfh/booking/{booking_id}",
        status_code=303
    )


def save_dispute_file(
    file: UploadFile,
    booking: Booking,        # ✅ we pass booking instead of booking_id
    uploaded_by: int,
    purpose: str = "response"  # "open" or "response"
) -> str:
    ext = Path(file.filename).suffix.lower().lstrip(".")

    if ext in BLOCKED_EXTENSIONS:
        raise HTTPException(400, "This file type is not allowed")

    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(400, "Unsupported file type")

    job_dir = get_job_dir(booking.id)

    # ✅ Detect role based on booking
    if uploaded_by == booking.worker_id:
        role_folder = f"worker_user_id_{booking.worker_id}"
    elif uploaded_by == booking.provider_id:
        role_folder = f"job_giver_user_id_{booking.provider_id}"
    else:
        role_folder = f"unknown_user_id_{uploaded_by}"

    folder = job_dir / "dispute" / role_folder
    folder.mkdir(parents=True, exist_ok=True)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_name = Path(file.filename).name.replace(" ", "_")

    filename = f"{purpose}_job{booking.id}_user{uploaded_by}_{ts}_{safe_name}"
    path = folder / filename

    # ✅ Save file with size control
    size = 0
    with open(path, "wb") as out:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_FILE_SIZE:
                os.remove(path)
                raise HTTPException(400, "File too large")
            out.write(chunk)

    # ✅ sanitize images
    if ext in {"jpg", "jpeg", "png", "webp", "gif"}:
        try:
            with Image.open(path) as img:
                img = img.convert("RGB")
                img.save(path, "JPEG", quality=90)
        except Exception:
            os.remove(path)
            raise HTTPException(400, "Unsafe image file")

    # ✅ sanitize PDFs
    if ext == "pdf":
        try:
            with pikepdf.open(path) as pdf:
                pdf.remove_unreferenced_resources()
                pdf.save(path)
        except Exception:
            os.remove(path)
            raise HTTPException(400, "Unsafe PDF file")

    return str(path)


@router.post("/wfh/update/{update_id}/comment")
def comment_on_update(
    update_id: int,
    reason: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    update = db.get(WFHProjectUpdate, update_id)
    if not update:
        raise HTTPException(404)

    booking = db.get(Booking, update.booking_id)

    if current_user.id != booking.provider_id:
        raise HTTPException(403)

    update.provider_comment = reason
    update.status = "commented"   # IMPORTANT: so form disappears

    job_dir = get_job_dir(booking.id)
    ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")

    comment_file = job_dir / "comments" / f"{ts}_giver_comment.txt"
    comment_file.write_text(reason, encoding="utf-8")

    write_audit(
        booking.id,
        "JOB_GIVER commented on update"
    )

    db.commit()

    return RedirectResponse(
        f"/wfh/giver/booking/{booking.id}",
        status_code=303
    )





@router.post("/wfh/booking/{booking_id}/dispute")
def open_dispute(
    booking_id: int,
    reason: str = Form(...),
    proof: List[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    booking = db.get(Booking, booking_id)

    if not booking or booking.booking_type != "wfh":
        raise HTTPException(404)

    if current_user.id not in (booking.worker_id, booking.provider_id):
        raise HTTPException(403)

    # ✅ require reason text
    if not reason or len(reason.strip()) < 10:
        raise HTTPException(400, "Dispute reason must be at least 10 characters")

    # ✅ require proof files
    if not proof or len(proof) == 0:
        raise HTTPException(400, "Please upload at least 1 proof file")


    # 🚫 prevent duplicate disputes
    existing = db.query(WFHDispute).filter(
        WFHDispute.booking_id == booking_id
    ).first()

    # ❌ BLOCK DISPUTE BEFORE PAYMENT
    if not booking.payment_completed:
        raise HTTPException(
            status_code=400,
            detail="Dispute allowed only after payment is completed"
        )

    # ✅ BLOCK dispute until job giver responds to worker update
    if not giver_responded_to_worker_update(db, booking_id, booking.worker_id):
        raise HTTPException(
            status_code=400,
            detail="Dispute can be raised only after Job Giver responds to your submitted update."
        )


    if existing:
        raise HTTPException(400, "Dispute already opened")

    dispute = WFHDispute(
        booking_id=booking_id,
        raised_by=current_user.id,
        reason=reason,
        created_at=ist_now()
    )
    db.add(dispute)
    db.flush()  # get dispute.id

    if proof:
        for f in proof:
            if f and f.filename:
                path = save_dispute_file(
                    file=f,
                    booking=booking,
                    uploaded_by=current_user.id,
                    purpose="open"
                )

                db.add(
                    WFHDisputeFile(
                        dispute_id=dispute.id,
                        file_url=path
                    )
                )

    booking.status = "WFH_DISPUTED"
    booking.escrow_locked = True

    write_audit(
        booking_id,
        f"DISPUTE_OPENED by user_id={current_user.id}"
    )

    db.commit()
    return RedirectResponse("/welcome", status_code=303)



def auto_approve_reviews(db: Session):
    now = ist_now()

    bookings = db.query(Booking).filter(
        Booking.status == "WFH_REVIEW_PENDING",
        Booking.review_deadline.isnot(None),
        Booking.review_deadline < now,
        Booking.escrow_locked == False
    ).all()

    for booking in bookings:
        finalize_wfh_payout(db, booking)

    db.commit()



@router.post("/wfh/giver/booking/{booking_id}/request-update")
def request_project_update(
    booking_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    booking = db.get(Booking, booking_id)

    if not booking or booking.booking_type != "wfh":
        raise HTTPException(404)

    if booking.provider_id != current_user.id:
        raise HTTPException(403)

    if booking.status != "WFH_IN_PROGRESS":
        raise HTTPException(400, "Job not in progress")

    # 🚫 BLOCK early abuse (50% rule)
    if not approval_allowed(booking):
        raise HTTPException(
            status_code=403,
            detail="Progress update can only be requested after 50% job time"
        )

    existing = db.query(WFHProjectUpdate).filter(
        WFHProjectUpdate.booking_id == booking_id,
        WFHProjectUpdate.status == "requested",
        WFHProjectUpdate.request_origin.in_(["system", "job_giver"])
    ).first()

    if existing:
        # 🔁 Convert system → job giver
        existing.request_origin = "job_giver"
        existing.request_deadline = ist_now() + timedelta(days=3)
    else:
        db.add(
            WFHProjectUpdate(
                booking_id=booking_id,
                requested_by=current_user.id,
                status="requested",
                request_origin="job_giver",
                request_deadline=ist_now() + timedelta(days=3)
            )
        )

    db.commit()

    return RedirectResponse(
        f"/wfh/giver/booking/{booking_id}",
        status_code=303
    )


@router.post("/wfh/booking/{booking_id}/submit-update")
def submit_project_update(
    booking_id: int,
    update_type: str = Form(...),
    message: str = Form(None),
    preview_url: str = Form(None),
    file: UploadFile = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    booking = db.get(Booking, booking_id)

    if booking.status == "WFH_REVIEW_PENDING":
        raise HTTPException(
            status_code=400,
            detail="Cannot submit updates while work is under review"
        )

    if not booking or booking.booking_type != "wfh":
        raise HTTPException(404)

    if booking.worker_id != current_user.id:
        raise HTTPException(403)

    requested = db.query(WFHProjectUpdate).filter(
        WFHProjectUpdate.booking_id == booking_id,
        WFHProjectUpdate.status == "requested",
        WFHProjectUpdate.request_origin == "job_giver"
    ).first()

    # ✅ Allow if job giver requested OR 50% reached OR revision mode
    if (
            booking.status != "WFH_REVISION_REQUESTED"
            and not requested
            and not approval_allowed(booking)
    ):
        raise HTTPException(
            status_code=403,
            detail="Update allowed only after job giver request or 50% of job time"
        )

    requested = db.query(WFHProjectUpdate).filter(
        WFHProjectUpdate.booking_id == booking_id,
        WFHProjectUpdate.status == "requested",
        WFHProjectUpdate.request_origin == "job_giver"

    ).first()

    # 🔥 FIX: allow worker updates after 50% time
    if (
            not requested
            and booking.status != "WFH_REVISION_REQUESTED"
            and not approval_allowed(booking)
    ):
        raise HTTPException(
            status_code=400,
            detail="Update allowed only after job giver request or 50% job time"
        )

    file_path = None

    if file and file.filename:
        ext = Path(file.filename).suffix.lower().lstrip(".")

        if ext in BLOCKED_EXTENSIONS:
            raise HTTPException(400, "This file type is not allowed")

        if file.content_type not in ALLOWED_MIME:
            raise HTTPException(400, "Unsupported file type")

        job_dir = get_job_dir(booking_id)

        if not booking.started_at:
            booking.started_at = ist_now()
            write_audit(booking.id, "JOB_STARTED")

        ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")

        filename = f"{ts}_worker_{Path(file.filename).name}"
        file_path = job_dir / "updates" / filename

        size = 0
        with open(file_path, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    os.remove(file_path)
                    raise HTTPException(400, "File too large")
                out.write(chunk)

        if ext in {"jpg", "jpeg", "png", "webp", "gif"}:
            try:
                with Image.open(file_path) as img:
                    img = img.convert("RGB")
                    img.save(file_path, "JPEG", quality=90)
            except Exception:
                os.remove(file_path)
                raise HTTPException(400, "Unsafe image")

        if ext == "pdf":
            try:
                with pikepdf.open(file_path) as pdf:
                    pdf.remove_unreferenced_resources()
                    pdf.save(file_path)
            except Exception:
                os.remove(file_path)
                raise HTTPException(400, "Unsafe PDF")

    if requested:
        requested.status = "responded"
        requested.request_deadline = None

    update = WFHProjectUpdate(
        booking_id=booking_id,
        requested_by=booking.provider_id,
        submitted_by=current_user.id,
        update_type=update_type,
        message=message,
        preview_url=preview_url,
        status="submitted",
        submitted_at=datetime.utcnow(),
    )

    if update_type == "milestone":
        booking.status = "WFH_REVIEW_PENDING"
        booking.review_deadline = ist_now() + timedelta(days=3)
        booking.completion_requested_once = True
        update.status = "approval_requested"

    else:
        booking.status = "WFH_IN_PROGRESS"

    db.add(update)

    if file_path:
        write_audit(
            booking_id,
            f"WORKER submitted update type={update_type} file={file_path.name}"
        )
    else:
        write_audit(
            booking_id,
            f"WORKER submitted update type={update_type} (no file)"
        )



    db.commit()

    return RedirectResponse(
        f"/wfh/booking/{booking_id}",
        status_code=303
    )


from fastapi.responses import FileResponse

from fastapi.responses import FileResponse

@router.get("/wfh/dispute/file/{file_id}")
def download_dispute_file(
    file_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    file = db.get(WFHDisputeFile, file_id)
    if not file:
        raise HTTPException(404)

    dispute = db.get(WFHDispute, file.dispute_id)
    if not dispute:
        raise HTTPException(404)

    booking = db.get(Booking, dispute.booking_id)

    # ✅ allow only dispute participants
    if current_user.id not in (booking.worker_id, booking.provider_id):
        raise HTTPException(403)

    path = file.file_url
    if not path or not os.path.exists(path):
        raise HTTPException(404, "File missing")

    ext = Path(path).suffix.lower()

    image_types = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    return FileResponse(
        path=path,
        filename=os.path.basename(path),
        media_type="image/*" if ext in image_types else "application/octet-stream",
        headers={
            "Content-Disposition": "inline" if ext in image_types else "attachment",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Referrer-Policy": "no-referrer"
        }
    )


@router.post("/wfh/update/{update_id}/approve")
def approve_update(
    update_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    update = db.get(WFHProjectUpdate, update_id)
    booking = db.get(Booking, update.booking_id)

    if current_user.id != booking.provider_id:
        raise HTTPException(403)

    if booking.status != "WFH_REVIEW_PENDING":
        raise HTTPException(400, "Not in review state")

    update.status = "approved"
    booking.status = "WFH_IN_PROGRESS"

    db.commit()
    return RedirectResponse(f"/wfh/giver/booking/{booking.id}", 303)

@router.post("/wfh/update/{update_id}/revision")
def request_update_revision(
    update_id: int,
    reason: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    update = db.get(WFHProjectUpdate, update_id)
    booking = db.get(Booking, update.booking_id)

    if current_user.id != booking.provider_id:
        raise HTTPException(403)

    if booking.status != "WFH_REVIEW_PENDING":
        raise HTTPException(400, "Not in review state")

    # ✅ COUNT revisions (server-side)
    revision_count = db.query(WFHProjectUpdate).filter(
        WFHProjectUpdate.booking_id == booking.id,
        WFHProjectUpdate.status == "revision_requested"
    ).count()

    # ✅ BLOCK AFTER 3
    if revision_count >= 3:
        raise HTTPException(
            status_code=400,
            detail="Maximum revision requests reached. Only approve or dispute is allowed."
        )

    update.provider_comment = reason
    update.status = "revision_requested"
    booking.status = "WFH_REVISION_REQUESTED"

    db.commit()
    return RedirectResponse(f"/wfh/giver/booking/{booking.id}", 303)



@router.get("/wfh/update/file/{update_id}")
def download_update_file(
    update_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    update = db.get(WFHProjectUpdate, update_id)
    if not update:
        raise HTTPException(404)

    booking = db.get(Booking, update.booking_id)

    # ✅ only worker or job giver can download
    if current_user.id not in (booking.worker_id, booking.provider_id):
        raise HTTPException(403)

    path = update.file_url
    if not path or not os.path.exists(path):
        raise HTTPException(404, "File missing")

    return FileResponse(
        path=path,
        filename=os.path.basename(path),
        media_type="application/octet-stream"
    )

def get_job_dir(booking_id: int) -> Path:
    base = WFH_STORAGE_ROOT / f"job_{booking_id}"
    (base / "updates").mkdir(parents=True, exist_ok=True)
    (base / "comments").mkdir(exist_ok=True)
    (base / "dispute").mkdir(exist_ok=True)
    (base / "audit.log").touch(exist_ok=True)
    return base

def giver_responded_to_worker_update(db: Session, booking_id: int, worker_id: int) -> bool:
    """
    Returns True if Job Giver responded to at least one worker update.
    Response means: approved / revision_requested / commented
    """

    responded = (
        db.query(WFHProjectUpdate)
        .filter(
            WFHProjectUpdate.booking_id == booking_id,
            WFHProjectUpdate.submitted_by == worker_id,
            WFHProjectUpdate.status.in_(["approved", "revision_requested", "commented"]),
        )
        .first()
    )

    return responded is not None



def write_audit(booking_id: int, message: str):
    log = get_job_dir(booking_id) / "audit.log"
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {message}\n")


def delete_wfh_files(db: Session, booking_id: int):
    booking = db.get(Booking, booking_id)
    if not booking:
        return

    if booking.status == "WFH_DISPUTED":
        return

    job_dir = WFH_STORAGE_ROOT / f"job_{booking_id}"
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)


