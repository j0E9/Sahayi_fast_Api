# app/routers/payments_calls.py
from __future__ import annotations


import os, hmac, hashlib
from hmac import compare_digest
from fastapi import APIRouter, Depends, HTTPException, Request, status, Body, Header
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from app.database import get_db
from app.models import (
    Booking,
    Notification,
    User,
    WalletTransaction,
    PlatformProfit,
)
from app.security.auth import get_current_user
from app.services.platform_balance import increment_platform_balance
from app.razor_client import client as razor  # single shared Razorpay client
# Wallet services
from app.services.wallet import add_ledger_row, compute_balance
from app.utils.IST_Time import ist_now
# --- Config / constants ---
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
RZP_TIMEOUT = 20  # seconds for SDK calls
from urllib.parse import urlparse
from decimal import Decimal
from app.services.onsite_escrow import refund_onsite_escrow



ALLOWED_ORIGIN_HOSTS = {
    "yourdomain.com",
    "www.yourdomain.com",
    "yourdomain.in",
    "www.yourdomain.in",
}

DEV_MODE = os.getenv("ENV", "dev").lower() in {"dev", "local", "debug"}
DEV_HOSTS = {"localhost", "127.0.0.1"}
DEV_TUNNEL_SUFFIXES = (".trycloudflare.com", ".ngrok-free.app", ".ngrok.io")
COMMISSION_RATE_GIVER = Decimal("0.05")   # 5% from job giver
COMMISSION_RATE_WORKER = Decimal("0.05")  # 5% from worker
# Make sure there is a User row with this ID (e.g. admin/company account).
def _get_platform_user(db: Session) -> User:
    platform_user = (
        db.query(User)
        .filter(User.is_platform == True)
        .with_for_update()
        .first()
    )
    if not platform_user:
        raise RuntimeError("Platform user not configured")
    return platform_user



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

def _get_base_amount(booking: Booking) -> Decimal:
    """
    Returns the BASE job value (before commission)
    - normal jobs: rate * quantity
    - WFH jobs: agreed rate only
    """
    if booking.booking_type == "wfh":
        if booking.rate is None:
            raise HTTPException(400, "WFH price not confirmed")
        return Decimal(str(booking.rate)).quantize(Decimal("0.01"))

    # non-WFH
    if booking.rate is None or booking.quantity is None:
        raise HTTPException(400, "Missing rate or quantity")
    return (Decimal(str(booking.rate)) * Decimal(str(booking.quantity))).quantize(Decimal("0.01"))


templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["payments-calls"])

# Twilio placeholders
client = None
TWILIO_PHONE = ""




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

    if booking.booking_type == "wfh":
        raise HTTPException(
            status_code=400,
            detail="WFH payments are handled via Razorpay after price confirmation"
        )

    if current_user.id != booking.provider_id:
        return HTMLResponse("Unauthorized", status_code=403)

    if booking.status == "Token Paid":
        return HTMLResponse('<script>window.location.replace("/welcome");</script>', status_code=200)

    if booking.expires_at and booking.expires_at < datetime.utcnow():
        booking.status = "Cancelled"
        booking.payment_required = False
        booking.payment_completed = False

        # ✅ Refund if Razorpay escrow exists
        try:
            if (
                    booking.booking_type in {"onsite", "realtime"}
                    and getattr(booking, "escrow_locked", False) is True
                    and getattr(booking, "escrow_released", False) is False
            ):
                db.flush()
                refund_onsite_escrow(db=db, booking=booking, reason="timeout_cancel")


        except Exception as e:
            print("ONSITE REFUND FAILED:", e)

        if booking.provider:
            booking.provider.busy = False
        if booking.worker:
            booking.worker.busy = False

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

    rate = Decimal(str(booking.rate or 0))
    qty = Decimal(str(booking.quantity or 0))

    # Base job value (what worker earns before worker 5% is subtracted)
    base_amount = (rate * qty).quantize(Decimal("0.01"))

    # 5% commission that *giver* pays extra
    giver_commission = (base_amount * COMMISSION_RATE_GIVER).quantize(Decimal("0.01"))

    # Total amount that job giver actually pays
    total_payable = (base_amount + giver_commission).quantize(Decimal("0.01"))

    remaining = max(0, int((booking.expires_at - datetime.utcnow()).total_seconds())) if booking.expires_at else 0

    resp = templates.TemplateResponse(
        "pay_token.html",
        {
            "request": request,
            "booking": booking,
            "time_left": remaining,
            # keep old name if you still need it anywhere
            "total_tokens": base_amount,
            "base_amount": base_amount,
            "giver_commission": giver_commission,
            "total_payable": total_payable,
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

    if booking.booking_type == "wfh":
        raise HTTPException(
            status_code=400,
            detail="WFH payments are handled via Razorpay after price confirmation"
        )

    if current_user.id != booking.provider_id:
        return HTMLResponse("Unauthorized", status_code=403)

    provider = booking.provider
    worker = booking.worker

    if booking.status == "Token Paid":
        return HTMLResponse('<script>window.location.replace("/welcome");</script>')

    if booking.expires_at and booking.expires_at < datetime.utcnow():
        booking.status = "Cancelled"
        booking.payment_required = False
        booking.payment_completed = False

        # ✅ Refund if Razorpay escrow exists
        try:
            if (
                    booking.booking_type != "wfh"
                    and getattr(booking, "escrow_locked", False) is True
                    and getattr(booking, "escrow_released", False) is False
            ):
                db.flush()
                refund_onsite_escrow(db=db, booking=booking, reason="timeout_cancel")

        except Exception:
            pass

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

    rate = Decimal(str(booking.rate or 0))
    qty = Decimal(str(booking.quantity or 0))
    base_amount = (rate * qty).quantize(Decimal("0.01"))  # base job value

    # Commission: 5% extra debit from giver on main job
    giver_commission = (base_amount * COMMISSION_RATE_GIVER).quantize(Decimal("0.01"))
    total_debit_provider = (base_amount + giver_commission).quantize(Decimal("0.01"))

    balance = compute_balance(db, provider.id)  # should be Decimal
    if balance < total_debit_provider:
        return HTMLResponse(
            f'<script>alert("❌ Insufficient balance! You need {total_debit_provider}, but only have {balance}.");'
            "window.history.back();</script>"
        )

    # Atomic debit/credit + flags + notify
    with db.begin():
        # Pass BASE amount to helper; it will apply 5%+5% logic
        _mark_booking_paid(
            db=db,
            booking=booking,
            provider=provider,
            worker=worker,
            total_tokens=base_amount,
            payment_id=f"manual_{booking.id}",
            order_id=None,
            method="manual",
        )
        db.add(Notification(
            recipient_id=worker.id,
            sender_id=provider.id,
            booking_id=booking.id,
            message=f"✅ {provider.name} paid {base_amount} tokens. You can now start chatting.",
            action_type="payment_completed",
            is_read=False,
        ))
        try:
            _notify_next_pending_for_provider(db, provider_id=provider.id, exclude_booking_id=booking.id)
        except Exception:
            pass

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
        booking.payment_required = False
        booking.payment_completed = False

        # ✅ Refund if Razorpay escrow exists
        try:
            if (
                    booking.booking_type != "wfh"
                    and getattr(booking, "escrow_locked", False) is True
                    and getattr(booking, "escrow_released", False) is False
            ):
                db.flush()
                refund_onsite_escrow(db=db, booking=booking, reason="timeout_cancel")
        except Exception:
            pass

        db.commit()
        raise HTTPException(status_code=400, detail="Payment time expired. Booking cancelled")

    base_rupees = _get_base_amount(booking)


    giver_commission = (base_rupees * COMMISSION_RATE_GIVER).quantize(Decimal("0.01"))
    total_charge_rupees = (base_rupees + giver_commission).quantize(Decimal("0.01"))

    amount_paise = int(total_charge_rupees * 100)

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
                    "display_amount": total_charge_rupees,
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
    booking.payment_required = True
    booking.payment_completed = False
    booking.razorpay_status = "created"
    db.add(booking)
    db.commit()

    return {
        "key_id": RAZORPAY_KEY_ID,
        "order_id": order["id"],
        "amount": order["amount"],
        "currency": order["currency"],
        "display_amount": total_charge_rupees,
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

    booking = (
        db.query(Booking)
        .filter(Booking.token == token)
        .with_for_update()
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

    data = f"{o_id}|{p_id}".encode()
    expected = hmac.new((RAZORPAY_KEY_SECRET or "").encode(), data, hashlib.sha256).hexdigest()
    if not compare_digest(expected, sig):
        raise HTTPException(status_code=400, detail="Invalid Razorpay signature")

    order = razor.order.fetch(o_id, timeout=RZP_TIMEOUT)
    pay   = razor.payment.fetch(p_id, timeout=RZP_TIMEOUT)

    if pay.get("order_id") != o_id:
        raise HTTPException(status_code=400, detail="Payment/order mismatch")
    if pay.get("status") != "captured":
        raise HTTPException(status_code=400, detail="Payment not captured")

    base_rupees = _get_base_amount(booking)

    giver_commission = (base_rupees * COMMISSION_RATE_GIVER).quantize(Decimal("0.01"))
    total_charge_rupees = (base_rupees + giver_commission).quantize(Decimal("0.01"))

    expected_amount = int(total_charge_rupees * 100)

    if int(order.get("amount", 0)) != expected_amount or order.get("currency") != "INR":
        raise HTTPException(status_code=400, detail="Amount/currency mismatch")

    provider = booking.provider
    worker   = booking.worker
    total_tokens = base_rupees  # BASE booking value

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

        try:
            _notify_next_pending_for_provider(db, provider_id=provider.id, exclude_booking_id=booking.id)
        except Exception:
            pass

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
        amount = int(pay.get("amount", 0) or 0)  # paise from Razorpay

        # Lock the booking row
        booking = (
            db.query(Booking)
            .filter(Booking.razor_order_id == o_id)
            .with_for_update()  # write lock
            .first()
        )

        if booking:
            # --- use Decimal, same logic as /razorpay/verify_payment ---
            base_rupees = _get_base_amount(booking)
            giver_commission = (base_rupees * COMMISSION_RATE_GIVER).quantize(Decimal("0.01"))
            total_charge_rupees = (base_rupees + giver_commission).quantize(Decimal("0.01"))
            expected = int(total_charge_rupees * 100)

            if expected == amount and not booking.payment_completed:
                provider = booking.provider
                worker   = booking.worker
                total_tokens = base_rupees  # BASE value for commission split

                with db.begin():
                    if not booking.payment_completed:
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
                        if provider and worker:
                            db.add(Notification(
                                recipient_id=worker.id,
                                sender_id=provider.id,
                                booking_id=booking.id,
                                message=(
                                    f"✅ {provider.name} paid {total_tokens} "
                                    f"tokens via Razorpay. You can now start chatting."
                                ),
                                action_type="payment_completed",
                                is_read=False,
                            ))

                            # --- NEW: surface next pending payment for this provider ---
                            try:
                                _notify_next_pending_for_provider(
                                    db,
                                    provider_id=provider.id,
                                    exclude_booking_id=booking.id,
                                )
                            except Exception:
                                pass

    return {"ok": True}



# --- helpers ---------------------------------------------------------------
def _already_recorded(db: Session, *, user_id: int, kind: str, reference: str) -> bool:
    """Idempotency guard: has this (user, kind, reference) been saved?"""
    return db.query(WalletTransaction).filter(
        WalletTransaction.user_id == user_id,
        WalletTransaction.kind == kind,
        WalletTransaction.reference == reference,
    ).first() is not None

def _platform_profit_exists(db: Session, reference: str) -> bool:
    return db.query(PlatformProfit).filter(
        PlatformProfit.reference == reference
    ).first() is not None


def _credit_worker_only(
    db: Session,
    *,
    worker: User,
    amount: float,  # BASE amount
    reference: str,
    booking: Booking,
    order_id: str | None,
    method: str,
):
    """
    Razorpay main booking flow:
      - Bank → company
      - CREDIT worker wallet with (base - 5% worker commission)
      - Giver 5% was already included in the Razorpay charge amount.
      - Store total 10% (5% + 5%) as platform_commission for company.
    """
    # Guard: only do this once per payment per worker
    if _already_recorded(db, user_id=worker.id, kind="booking_payment_credit", reference=reference):
        return

    base = Decimal(str(amount)).quantize(Decimal("0.01"))
    worker_commission = (base * COMMISSION_RATE_WORKER).quantize(Decimal("0.01"))
    worker_net = (base - worker_commission).quantize(Decimal("0.01"))

    # 1) Credit worker (base - 5%)
    add_ledger_row(
        db=db,
        user_id=worker.id,
        amount_rupees=worker_net,
        kind="booking_payment_credit",
        reference=reference,
        meta={
            "booking_id": booking.id,
            "order_id": order_id,
            "method": method,
            "base_amount": str(base),
            "worker_commission": str(worker_commission),
        },
    )

    # 2) Record platform profit (5% from giver + 5% from worker)
    platform_user = _get_platform_user(db)
    if platform_user and not _already_recorded(
        db, user_id=platform_user.id, kind="platform_commission", reference=reference
    ):
        giver_commission = (base * COMMISSION_RATE_GIVER).quantize(Decimal("0.01"))
        platform_profit = (giver_commission + worker_commission).quantize(Decimal("0.01"))
        # --- Platform profit record (NON-WFH, Razorpay) ---
        ref = f"commission_{booking.id}_{reference}"
        db.add(
            PlatformProfit(
                booking_id=booking.id,
                type="commission",
                direction="credit",
                amount=platform_profit,
                giver_commission=giver_commission,
                worker_commission=worker_commission,
                on_hold=False,
                reference=ref,
                meta={
                    "method": method,
                    "order_id": order_id,
                    "payment_ref": reference,
                },
            )
        )

        increment_platform_balance(db, platform_profit)

        add_ledger_row(
            db=db,
            user_id=platform_user.id,
            amount_rupees=platform_profit,
            kind="platform_commission",
            reference=reference,
            meta={
                "booking_id": booking.id,
                "order_id": order_id,
                "method": method,
                "base_amount": str(base),
                "giver_commission": str(giver_commission),
                "worker_commission": str(worker_commission),
            },
        )


def _debit_giver_and_credit_worker(
    db: Session,
    *,
    provider: User,
    worker: User,
    amount: float,  # BASE amount
    reference: str,
    booking: Booking,
    order_id: str | None,
    method: str,
):
    """
    Manual wallet main booking flow:
      - DEBIT giver with (base + 5%)
      - CREDIT worker with (base - 5%)
      - 10% difference (5% + 5%) is platform commission, stored in wallet_transaction.
    """
    base = Decimal(str(amount)).quantize(Decimal("0.01"))
    giver_commission = (base * COMMISSION_RATE_GIVER).quantize(Decimal("0.01"))
    worker_commission = (base * COMMISSION_RATE_WORKER).quantize(Decimal("0.01"))

    provider_debit = (base + giver_commission).quantize(Decimal("0.01"))
    worker_net = (base - worker_commission).quantize(Decimal("0.01"))
    platform_profit = (giver_commission + worker_commission).quantize(Decimal("0.01"))
    # --- Platform profit record (NON-WFH, manual) ---


    # 1) Debit giver
    if not _already_recorded(db, user_id=provider.id, kind="booking_payment_debit", reference=reference):
        add_ledger_row(
            db=db,
            user_id=provider.id,
            amount_rupees=-provider_debit,
            kind="booking_payment_debit",
            reference=reference,
            meta={
                "booking_id": booking.id,
                "order_id": order_id,
                "method": method,
                "base_amount": str(base),
                "giver_commission": str(giver_commission),
            },
        )

    # 2) Credit worker
    if not _already_recorded(db, user_id=worker.id, kind="booking_payment_credit", reference=reference):
        add_ledger_row(
            db=db,
            user_id=worker.id,
            amount_rupees=worker_net,
            kind="booking_payment_credit",
            reference=reference,
            meta={
                "booking_id": booking.id,
                "order_id": order_id,
                "method": method,
                "base_amount": str(base),
                "worker_commission": str(worker_commission),
            },
        )

    # 3) Record platform profit
    platform_user = _get_platform_user(db)
    if platform_user and not _already_recorded(
        db, user_id=platform_user.id, kind="platform_commission", reference=reference
    ):
        add_ledger_row(
            db=db,
            user_id=platform_user.id,
            amount_rupees=platform_profit,
            kind="platform_commission",
            reference=reference,
            meta={
                "booking_id": booking.id,
                "order_id": order_id,
                "method": method,
                "base_amount": str(base),
                "giver_commission": str(giver_commission),
                "worker_commission": str(worker_commission),
            },
        )

        # 2️⃣ Reporting table
        ref = f"commission_{booking.id}_{reference}"
        if not _platform_profit_exists(db, ref):
            db.add(
                PlatformProfit(
                    booking_id=booking.id,
                    type="commission",
                    direction="credit",
                    amount=platform_profit,
                    giver_commission=giver_commission,
                    worker_commission=worker_commission,
                    on_hold=False,
                    reference=ref,
                    meta={"method": method},
                )
            )
            increment_platform_balance(db, platform_profit)


def _mark_booking_paid(
    db: Session,
    booking: Booking,
    provider: User,
    worker: User,
    total_tokens: float,
    *,
    payment_id: str,
    order_id: str | None,
    method: str,
):
    """
    For WFH:
      - DO NOT credit worker
      - Lock funds in escrow
    For non-WFH:
      - Existing behaviour unchanged
    """

    # ---------- WFH SPECIAL HANDLING ----------
    if booking.booking_type == "wfh":
        now = ist_now()

        booking.status = "WFH_IN_PROGRESS"

        if not booking.started_at:
            booking.started_at = now
            booking.start_date = now

        if not booking.end_date:
            if booking.deadline and booking.deadline > now:
                booking.end_date = booking.deadline
            else:
                booking.end_date = now + timedelta(days=1)

        booking.payment_completed = True
        booking.payment_required = False

        # ✅ SET ESCROW FIRST
        booking.escrow_amount = Decimal(str(total_tokens))
        booking.escrow_locked = True
        booking.escrow_released = False

        db.flush()  # 🔥 REQUIRED

        # ✅ THEN RECORD ESCROW
        ref = f"escrow_hold_{booking.id}"
        if not _platform_profit_exists(db, ref):
            db.add(
                PlatformProfit(
                    booking_id=booking.id,
                    type="escrow_hold",
                    direction="credit",
                    amount=booking.escrow_amount,

                    # ✅ REQUIRED — escrow has NO commission yet
                    giver_commission=Decimal("0.00"),
                    worker_commission=Decimal("0.00"),

                    on_hold=True,
                    hold_for_user_id=booking.worker_id,
                    release_at=booking.end_date,
                    reference=ref,
                    meta={
                        "booking_type": "wfh",
                        "deadline": booking.deadline.isoformat() if booking.deadline else None,
                    },
                )
            )

        return

    # ---------- NON-WFH → ESCROW HOLD (Razorpay) ----------
    base = Decimal(str(total_tokens)).quantize(Decimal("0.01"))
    giver_commission = (base * COMMISSION_RATE_GIVER).quantize(Decimal("0.01"))
    booking.giver_commission_amount = giver_commission

    # ✅ If payment came from Razorpay bank account,
    # money is already received by company. Lock it in escrow.
    if method == "razorpay":
        booking.escrow_amount = base
        booking.escrow_locked = True
        booking.escrow_released = False

        # reporting escrow record
        db.flush()
        ref = f"escrow_hold_{booking.id}_{payment_id}"
        if not _platform_profit_exists(db, ref):
            db.add(
                PlatformProfit(
                    booking_id=booking.id,
                    type="escrow_hold",
                    direction="credit",
                    amount=base,
                    giver_commission=Decimal("0.00"),
                    worker_commission=Decimal("0.00"),
                    on_hold=True,
                    hold_for_user_id=booking.worker_id,
                    release_at=None,
                    reference=ref,
                    meta={"booking_type": "onsite", "method": "razorpay", "order_id": order_id},
                )
            )

        booking.status = "Token Paid"
        booking.payment_completed = True
        booking.payment_required = False
        booking.razorpay_status = "captured"
        booking.razor_payment_id = payment_id
        if order_id:
            booking.razor_order_id = order_id

        return

    # ✅ Manual wallet flow can stay as-is (if you still use it)
    _debit_giver_and_credit_worker(
        db,
        provider=provider,
        worker=worker,
        amount=base,
        reference=payment_id,
        booking=booking,
        order_id=order_id,
        method=method,
    )

    booking.status = "Token Paid"
    booking.payment_completed = True
    booking.payment_required = False
    booking.razorpay_status = "manual"
    booking.razor_payment_id = payment_id
    if order_id:
        booking.razor_order_id = order_id



def release_onsite_escrow_on_completion(db: Session, booking: Booking) -> None:
    """
    Release escrow for an onsite booking ONLY when status becomes Completed.
    Pays worker (base - 5% worker commission) and records platform commission (giver 5% + worker 5%).
    Idempotent: safe to call multiple times.
    """
    if booking.booking_type == "wfh":
        return  # WFH handled elsewhere

    if not getattr(booking, "escrow_locked", False):
        return

    if getattr(booking, "escrow_released", False):
        return

    base = Decimal(str(booking.escrow_amount or 0)).quantize(Decimal("0.01"))
    if base <= 0:
        return

    worker_commission = (base * COMMISSION_RATE_WORKER).quantize(Decimal("0.01"))
    giver_commission  = (base * COMMISSION_RATE_GIVER).quantize(Decimal("0.01"))
    worker_net = (base - worker_commission).quantize(Decimal("0.01"))
    platform_profit = (giver_commission + worker_commission).quantize(Decimal("0.01"))

    worker = booking.worker
    platform_user = _get_platform_user(db)

    # reference key for idempotency
    ref = f"onsite_escrow_release_{booking.id}"

    # 1) Credit worker
    if worker and not _already_recorded(db, user_id=worker.id, kind="escrow_release_credit", reference=ref):
        add_ledger_row(
            db=db,
            user_id=worker.id,
            amount_rupees=worker_net,
            kind="escrow_release_credit",
            reference=ref,
            meta={
                "booking_id": booking.id,
                "base_amount": str(base),
                "worker_commission": str(worker_commission),
            },
        )

    # 2) Credit platform commission
    if platform_user and not _already_recorded(db, user_id=platform_user.id, kind="platform_commission", reference=ref):
        add_ledger_row(
            db=db,
            user_id=platform_user.id,
            amount_rupees=platform_profit,
            kind="platform_commission",
            reference=ref,
            meta={
                "booking_id": booking.id,
                "base_amount": str(base),
                "giver_commission": str(giver_commission),
                "worker_commission": str(worker_commission),
            },
        )

        pp_ref = f"commission_{booking.id}_{ref}"
        if not _platform_profit_exists(db, pp_ref):
            db.add(
                PlatformProfit(
                    booking_id=booking.id,
                    type="commission",
                    direction="credit",
                    amount=platform_profit,
                    giver_commission=giver_commission,
                    worker_commission=worker_commission,
                    on_hold=False,
                    reference=pp_ref,
                    meta={"booking_type": "onsite", "source": "escrow_release"},
                )
            )
            increment_platform_balance(db, platform_profit)

    # 3) Mark escrow released
    booking.escrow_released = True
    booking.escrow_locked = False
    booking.escrow_released_at = ist_now() if hasattr(booking, "escrow_released_at") else None


# --- NEW helper: surface next pending payment for provider ------------------
def _notify_next_pending_for_provider(db: Session, *, provider_id: int, exclude_booking_id: int | None = None) -> bool:
    """
    If the provider has another booking which is payment_required==True and payment_completed==False,
    create a Notification so the frontend can auto-open the pay page for that booking.
    Returns True if a notification was created.
    """
    try:
        q = (
            db.query(Booking)
            .filter(
                Booking.provider_id == provider_id,
                Booking.payment_required == True,
                Booking.payment_completed == False,
            )
        )
        if exclude_booking_id:
            q = q.filter(Booking.id != exclude_booking_id)

        # sensible ordering: earliest expiring first, fallback to id asc
        try:
            next_pending = q.order_by(Booking.expires_at.asc().nulls_last(), Booking.id.asc()).first()
        except Exception:
            # fallback for SQLAlchemy versions without nulls_last()
            next_pending = q.order_by(func.coalesce(Booking.expires_at, datetime.max).asc(), Booking.id.asc()).first()

        if not next_pending:
            return False

        db.add(Notification(
            recipient_id=provider_id,
            sender_id=provider_id,
            booking_id=next_pending.id,
            message=f"🔔 Pending token payment for booking #{next_pending.id}. Click to pay.",
            action_type="payment_required",
            is_read=False,
        ))
        return True
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return False
