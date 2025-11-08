# app/routers/payments_calls.py
from __future__ import annotations

from datetime import datetime
import os, hmac, hashlib
from hmac import compare_digest
from fastapi import APIRouter, Depends, HTTPException, Request, status, Body, Header
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import Booking, Notification, User, WalletTransaction
from app.razor_client import client as razor  # single shared Razorpay client
from urllib.parse import urlparse
# Wallet services
from app.services.wallet import add_ledger_row, compute_balance

# --- Config / constants ---
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
RZP_TIMEOUT = 20  # seconds for SDK calls



import os
from urllib.parse import urlparse
from fastapi import HTTPException, Request

ALLOWED_ORIGIN_HOSTS = {
    "yourdomain.com",
    "www.yourdomain.com",
    "yourdomain.in",
    "www.yourdomain.in",
}

DEV_MODE = os.getenv("ENV", "dev").lower() in {"dev", "local", "debug"}
DEV_HOSTS = {"localhost", "127.0.0.1"}
DEV_TUNNEL_SUFFIXES = (".trycloudflare.com", ".ngrok-free.app", ".ngrok.io")

def _host_allowed(host: str, request_host: str) -> bool:
    if not host:
        return False
    host = host.lower()
    request_host = (request_host or "").lower()
    if host in ALLOWED_ORIGIN_HOSTS:
        return True
    if DEV_MODE:
        if host == request_host:
            return True
        if host in DEV_HOSTS:
            return True
        if any(host.endswith(suf) for suf in DEV_TUNNEL_SUFFIXES):
            return True
    return False

def _enforce_same_origin(request: Request):
    req_host = (request.url.hostname or "").lower()
    origin  = request.headers.get("Origin") or ""
    referer = request.headers.get("Referer") or ""
    if origin and _host_allowed(urlparse(origin).hostname or "", req_host):
        return
    if referer and _host_allowed(urlparse(referer).hostname or "", req_host):
        return
    raise HTTPException(status_code=403, detail="Bad origin")



templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["payments-calls"])

# Twilio placeholders
client = None
TWILIO_PHONE = ""


# -------- Auth ----------
def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = db.get(User, int(uid))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


# -------- /check_token_status/<token> ----------
@router.get("/check_token_status/{token}")
def check_token_status(
    token: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.query(Booking).filter_by(token=token).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if current_user.id != booking.worker_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    return {"paid": booking.status == "Token Paid"}


# -------- /pay_token/<token> (GET) ----------
@router.get("/pay_token/{token}", response_class=HTMLResponse)
def pay_token_get(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.query(Booking).filter_by(token=token).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if current_user.id != booking.provider_id:
        return HTMLResponse("Unauthorized", status_code=403)

    if booking.status == "Token Paid":
        return HTMLResponse('<script>window.location.replace("/welcome");</script>', status_code=200)

    if booking.expires_at and booking.expires_at < datetime.utcnow():
        booking.status = "Cancelled"
        db.commit()
        return HTMLResponse(
            '<script>alert("⛔ Token payment time expired. Booking cancelled.");'
            'window.location.replace("/welcome");</script>'
        )

    if booking.rate is None or booking.quantity is None:
        return HTMLResponse(
            '<script>alert("❌ Rate or quantity is missing for this booking.");'
            'window.location.replace("/welcome");</script>'
        )

    total_tokens = int(booking.rate * booking.quantity)
    remaining = max(0, int((booking.expires_at - datetime.utcnow()).total_seconds())) if booking.expires_at else 0

    resp = templates.TemplateResponse(
        "pay_token.html",
        {
            "request": request,
            "booking": booking,
            "time_left": remaining,
            "total_tokens": total_tokens,
            "current_user": current_user,
        },
    )
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


# -------- /pay_token/<token> (POST - legacy local transfer from wallet) ----------
@router.post("/pay_token/{token}", response_class=HTMLResponse)
def pay_token_post(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _enforce_same_origin(request)

    booking = db.query(Booking).filter_by(token=token).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if current_user.id != booking.provider_id:
        return HTMLResponse("Unauthorized", status_code=403)

    provider = booking.provider
    worker = booking.worker

    if booking.status == "Token Paid":
        return HTMLResponse('<script>window.location.replace("/welcome");</script>')

    if booking.expires_at and booking.expires_at < datetime.utcnow():
        booking.status = "Cancelled"
        db.commit()
        return HTMLResponse(
            '<script>alert("⛔ Token payment time expired. Booking cancelled.");'
            'window.location.replace("/welcome");</script>'
        )

    if booking.rate is None or booking.quantity is None:
        return HTMLResponse(
            '<script>alert("❌ Rate or quantity is missing for this booking.");'
            'window.location.replace("/welcome");</script>'
        )

    total_tokens = int(booking.rate * booking.quantity)

    # Manual path uses wallet balance
    balance = compute_balance(db, provider.id)
    if balance < total_tokens:
        return HTMLResponse(
            f'<script>alert("❌ Insufficient balance! You need {total_tokens}, but only have {int(balance)}.");'
            "window.history.back();</script>"
        )

    # Atomic debit/credit + flags + notify
    with db.begin():
        _mark_booking_paid(
            db=db,
            booking=booking,
            provider=provider,
            worker=worker,
            total_tokens=total_tokens,
            payment_id=f"manual_{booking.id}",
            order_id=None,
            method="manual",
        )
        db.add(Notification(
            recipient_id=worker.id,
            sender_id=provider.id,
            booking_id=booking.id,
            message=f"✅ {provider.name} paid {total_tokens} tokens. You can now start chatting.",
            action_type="payment_completed",
            is_read=False,
        ))

    return HTMLResponse('<script>alert("✅ Payment Successful.");window.location.replace("/welcome");</script>')


# -------- /initiate_call/<booking_id> ----------
@router.post("/initiate_call/{booking_id}")
def initiate_call(
    booking_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if booking.provider_id == current_user.id:
        partner = booking.worker
    elif booking.worker_id == current_user.id:
        partner = booking.provider
    else:
        return JSONResponse({"status": "error", "message": "Not part of this booking"}, status_code=403)

    if not partner or not getattr(partner, "phone", None) or not getattr(current_user, "phone", None):
        return JSONResponse({"status": "error", "message": "Phone numbers missing"}, status_code=400)

    def format_number(num: str) -> str:
        n = (num or "").strip()
        if n.startswith("+91"): return n
        if n.startswith("0"):   return "+91" + n[1:]
        return "+91" + n[-10:]

    partner_phone = format_number(partner.phone)
    caller_phone = format_number(current_user.phone)

    try:
        if client is None or not TWILIO_PHONE:
            return JSONResponse({"status": "success", "message": "Simulated call (Twilio not configured)"})
        call = client.calls.create(
            to=partner_phone,
            from_=TWILIO_PHONE,
            twiml=f"""
            <Response>
                <Say voice="alice">
                    You have a call request from Sahayi platform. Connecting now.
                </Say>
                <Dial callerId="{TWILIO_PHONE}">{caller_phone}</Dial>
            </Response>
            """,
        )
        return {"status": "success", "message": "Call initiated"}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# -------- /check_booking_status ----------
@router.get("/check_booking_status")
def check_booking_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Case-insensitive status check to match DB values reliably
    live_statuses = {"token paid", "in progress", "accepted"}
    active = (
        db.query(Booking)
        .filter(
            func.lower(Booking.status).in_(live_statuses),
            ((Booking.provider_id == current_user.id) | (Booking.worker_id == current_user.id)),
        )
        .first()
    )
    return {"live": bool(active)}


# -------- Razorpay: Create Order ----------
@router.post("/razorpay/create_order/{token}")
def create_razorpay_order(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _enforce_same_origin(request)

    booking = db.query(Booking).filter_by(token=token).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if current_user.id != booking.provider_id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    if booking.status == "Token Paid":
        raise HTTPException(status_code=400, detail="Already paid")

    if booking.expires_at and booking.expires_at < datetime.utcnow():
        booking.status = "Cancelled"
        db.commit()
        raise HTTPException(status_code=400, detail="Payment time expired. Booking cancelled")

    if booking.rate is None or booking.quantity is None:
        raise HTTPException(status_code=400, detail="Missing rate/quantity")

    total_rupees = int(booking.rate * booking.quantity)
    amount_paise = total_rupees * 100

    # Reuse existing order if amount matches
    if getattr(booking, "razor_order_id", None):
        try:
            existing = razor.order.fetch(booking.razor_order_id, timeout=RZP_TIMEOUT)
            if int(existing.get("amount", 0)) == amount_paise and existing.get("status") in ("created", "attempted"):
                return {
                    "key_id": RAZORPAY_KEY_ID,
                    "order_id": existing["id"],
                    "amount": existing["amount"],
                    "currency": existing.get("currency", "INR"),
                    "display_amount": total_rupees,
                }
        except Exception:
            pass

    order = razor.order.create({
        "amount": amount_paise,
        "currency": "INR",
        "receipt": f"booking_{booking.id}",
        "notes": {"booking_id": str(booking.id), "token": token},
        "payment_capture": 1,
    }, timeout=RZP_TIMEOUT)

    booking.razor_order_id = order["id"]
    booking.payment_required = True            # flag for UI
    booking.payment_completed = False
    booking.razorpay_status = "created"        # helpful for UI/debug
    db.add(booking)
    db.commit()

    return {
        "key_id": RAZORPAY_KEY_ID,
        "order_id": order["id"],
        "amount": order["amount"],
        "currency": order["currency"],
        "display_amount": total_rupees,
    }


# -------- Razorpay: Verify Payment ----------
@router.post("/razorpay/verify_payment")
def verify_razorpay_payment(
    body: dict = Body(...),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _enforce_same_origin(request)

    p_id = body.get("razorpay_payment_id")
    o_id = body.get("razorpay_order_id")
    sig  = body.get("razorpay_signature")
    token = body.get("token")
    if not all([p_id, o_id, sig, token]):
        raise HTTPException(status_code=400, detail="Missing payment params")

    # Lock booking row to avoid concurrent double-marking
    booking = (
        db.query(Booking)
        .filter(Booking.token == token)
        .with_for_update()  # write lock
        .first()
    )

    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if current_user.id != booking.provider_id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    if booking.status == "Token Paid":
        return {
            "success": True, "message": "Already marked paid",
            "payment_completed": True, "payment_required": False,
            "razorpay_status": "captured",
        }

    # 1) HMAC signature check
    data = f"{o_id}|{p_id}".encode()
    expected = hmac.new((RAZORPAY_KEY_SECRET or "").encode(), data, hashlib.sha256).hexdigest()
    if not compare_digest(expected, sig):
        raise HTTPException(status_code=400, detail="Invalid Razorpay signature")

    # 2) Fetch authoritative order + payment
    order = razor.order.fetch(o_id, timeout=RZP_TIMEOUT)
    pay   = razor.payment.fetch(p_id, timeout=RZP_TIMEOUT)

    # 3) Linkage + captured status
    if pay.get("order_id") != o_id:
        raise HTTPException(status_code=400, detail="Payment/order mismatch")
    if pay.get("status") != "captured":
        raise HTTPException(status_code=400, detail="Payment not captured")

    # 4) Amount/currency match against booking
    if booking.rate is None or booking.quantity is None:
        raise HTTPException(status_code=400, detail="Missing rate/quantity")
    expected_amount = int(booking.rate * booking.quantity) * 100
    if int(order.get("amount", 0)) != expected_amount or order.get("currency") != "INR":
        raise HTTPException(status_code=400, detail="Amount/currency mismatch")

    provider = booking.provider
    worker   = booking.worker
    total_tokens = expected_amount // 100

    # 5) Atomic credit + flags + notification
    # 5) Atomic credit + flags + notification
    try:
        if booking.payment_completed:
            return {
                "success": True,
                "message": "Already marked paid",
                "payment_completed": True,
                "payment_required": False,
                "razorpay_status": "captured",
            }

        _mark_booking_paid(
            db=db,
            booking=booking,
            provider=provider,
            worker=worker,
            total_tokens=total_tokens,
            payment_id=p_id,
            order_id=o_id,
            method="razorpay",
        )
        db.add(Notification(
            recipient_id=worker.id,
            sender_id=provider.id,
            booking_id=booking.id,
            message=f"✅ {provider.name} paid {total_tokens} tokens via Razorpay. You can now start chatting.",
            action_type="payment_completed",
            is_read=False,
        ))

        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "success": True,
        "message": "Payment verified and tokens transferred",
        "payment_completed": True,
        "payment_required": False,
        "razorpay_status": "captured",
    }


# -------- Razorpay: Webhook ----------
@router.post("/razorpay/webhook")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(None),
    db: Session = Depends(get_db),
):
    if not RAZORPAY_WEBHOOK_SECRET:
        return JSONResponse({"error": "Webhook secret not set"}, status_code=500)

    raw = await request.body()
    calc = hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not compare_digest(calc, (x_razorpay_signature or "")):
        return JSONResponse({"error": "Invalid signature"}, status_code=400)

    evt = await request.json()
    etype = (evt.get("event") or "").lower()

    # Idempotent server-side confirmation on payment.captured
    if etype == "payment.captured":
        pay = evt.get("payload", {}).get("payment", {}).get("entity", {}) or {}
        p_id = pay.get("id")
        o_id = pay.get("order_id")
        amount = int(pay.get("amount", 0) or 0)

        # Lock the booking row
        booking = (
            db.query(Booking)
            .filter(Booking.razor_order_id == o_id)
            .with_for_update()  # write lock
            .first()
        )

        if booking:
            expected = int((booking.rate or 0) * (booking.quantity or 0)) * 100
            if expected == amount and not booking.payment_completed:
                provider = booking.provider
                worker   = booking.worker
                total_tokens = expected // 100
                with db.begin():
                    if not booking.payment_completed:
                        _mark_booking_paid(
                            db=db,
                            booking=booking,
                            provider=provider,
                            worker=worker,
                            total_tokens=total_tokens,
                            payment_id=p_id, order_id=o_id, method="razorpay",
                        )
                        if provider and worker:
                            db.add(Notification(
                                recipient_id=worker.id,
                                sender_id=provider.id,
                                booking_id=booking.id,
                                message=f"✅ {provider.name} paid {total_tokens} tokens via Razorpay. You can now start chatting.",
                                action_type="payment_completed",
                                is_read=False,
                            ))

    return {"ok": True}


# --- helpers ---------------------------------------------------------------
def _already_recorded(db: Session, *, user_id: int, kind: str, reference: str) -> bool:
    """Idempotency guard: has this (user, kind, reference) been saved?"""
    return db.query(WalletTransaction).filter(
        WalletTransaction.user_id == user_id,
        WalletTransaction.kind == kind,
        WalletTransaction.reference == reference,
    ).first() is not None


def _credit_worker_only(db: Session, *, worker: User, amount: int, reference: str, booking: Booking, order_id: str | None, method: str):
    """Credit worker once (no giver debit)."""
    if _already_recorded(db, user_id=worker.id, kind="booking_payment_credit", reference=reference):
        return
    add_ledger_row(
        db=db,
        user_id=worker.id,
        amount_rupees=int(amount),
        kind="booking_payment_credit",
        reference=reference,
        meta={"booking_id": booking.id, "order_id": order_id, "method": method},
    )


def _debit_giver_and_credit_worker(db: Session, *, provider: User, worker: User, amount: int, reference: str, booking: Booking, order_id: str | None, method: str):
    """Manual flow: debit giver wallet and credit worker wallet (idempotent per side)."""
    if not _already_recorded(db, user_id=provider.id, kind="booking_payment_debit", reference=reference):
        add_ledger_row(
            db=db,
            user_id=provider.id,
            amount_rupees=-int(amount),
            kind="booking_payment_debit",
            reference=reference,
            meta={"booking_id": booking.id, "order_id": order_id, "method": method},
        )
    if not _already_recorded(db, user_id=worker.id, kind="booking_payment_credit", reference=reference):
        add_ledger_row(
            db=db,
            user_id=worker.id,
            amount_rupees=int(amount),
            kind="booking_payment_credit",
            reference=reference,
            meta={"booking_id": booking.id, "order_id": order_id, "method": method},
        )


def _mark_booking_paid(
    db: Session,
    booking: Booking,
    provider: User,
    worker: User,
    total_tokens: int,
    *,
    payment_id: str,
    order_id: str | None,
    method: str,  # "razorpay" | "manual"
):
    """
    Booking paid:
      - razorpay: CREDIT ONLY worker wallet (bank → company; giver wallet stays untouched)
      - manual: DEBIT giver wallet and CREDIT worker wallet
      - set booking flags & references
    """
    if method == "razorpay":
        _credit_worker_only(
            db, worker=worker, amount=total_tokens,
            reference=payment_id, booking=booking, order_id=order_id, method=method
        )
    else:
        _debit_giver_and_credit_worker(
            db, provider=provider, worker=worker, amount=total_tokens,
            reference=payment_id, booking=booking, order_id=order_id, method=method
        )

    # Flags your UI reads
    booking.status = "Token Paid"
    booking.payment_completed = True
    booking.payment_required = False
    booking.razorpay_status = "captured" if method == "razorpay" else "manual"

    # References
    booking.razor_payment_id = payment_id
    if order_id:
        booking.razor_order_id = order_id
