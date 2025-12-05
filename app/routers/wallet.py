# app/routers/wallet.py
from __future__ import annotations
import os, hmac, hashlib
from hmac import compare_digest
from fastapi import APIRouter, Depends, HTTPException, Request, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, WalletTransaction, PayoutRequest
from app.services.wallet import compute_balance, verify_chain, add_ledger_row, open_payout_request
from app.razor_client import client as razor   # shared client
from urllib.parse import urlparse
from decimal import Decimal

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(prefix="", tags=["wallet"])

RZP_TIMEOUT = 20  # seconds
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")



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



# Reuse your session auth
def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("user_id")
    if not uid: raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.get(User, int(uid))
    if not user: raise HTTPException(status_code=401, detail="User not found")
    return user

@router.get("/wallet", response_class=HTMLResponse)
def wallet_home(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    bal = compute_balance(db, current_user.id)
    ok = verify_chain(db, current_user.id)
    txns = (
        db.query(WalletTransaction)
          .filter(WalletTransaction.user_id == current_user.id)
          .order_by(WalletTransaction.id.desc())
          .limit(20).all()
    )
    return templates.TemplateResponse("wallet.html", {
        "request": request,
        "balance": str(bal),
        "integrity_ok": ok,
        "txns": txns,
        "current_user": current_user,
        "has_razor": True,  # shared client exists if app booted correctly
    })

@router.get("/payment_history", response_class=HTMLResponse)
def wallet_history(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    txns = (
        db.query(WalletTransaction)
          .filter(WalletTransaction.user_id == current_user.id)
          .order_by(WalletTransaction.id.desc())
          .all()
    )
    return templates.TemplateResponse("wallet_history.html", {
        "request": request,
        "txns": txns,
        "current_user": current_user,
    })

# ---- Add Money (Razorpay) ----
@router.post("/wallet/create_order")
def wallet_create_order(payload: dict = Body(...),
                        request: Request = None,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    _enforce_same_origin(request)

    amount_str = str(payload.get("amount_rupees", "0")).strip()
    try:
        amount_rupees = Decimal(amount_str).quantize(Decimal("0.01"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid amount")

    if amount_rupees <= 0 or amount_rupees > Decimal("200000"):
        raise HTTPException(status_code=400, detail="Invalid amount")

    order = razor.order.create({
        "amount": int(amount_rupees * 100),  # paise
        "currency": "INR",
        "receipt": f"wallet_topup_{current_user.id}",
        "notes": {"user_id": str(current_user.id), "kind": "wallet_topup"},
        "payment_capture": 1,
    }, timeout=RZP_TIMEOUT)

    return {
        "key_id": RAZORPAY_KEY_ID,
        "order_id": order["id"],
        "amount": order["amount"],
        "currency": "INR",
    }


@router.post("/wallet/verify_topup")
def wallet_verify_topup(payload: dict = Body(...),
                        request: Request = None,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    _enforce_same_origin(request)

    for k in ("razorpay_payment_id","razorpay_order_id","razorpay_signature"):
        if not payload.get(k):
            raise HTTPException(status_code=400, detail=f"Missing {k}")

    p_id = payload["razorpay_payment_id"]
    o_id = payload["razorpay_order_id"]
    sig  = payload["razorpay_signature"]

    # 1) Signature (constant-time)
    data = f"{o_id}|{p_id}".encode()
    expected = hmac.new((RAZORPAY_KEY_SECRET or "").encode(), data, hashlib.sha256).hexdigest()
    if not compare_digest(expected, sig):
        raise HTTPException(status_code=400, detail="Invalid signature")

    # 2) Fetch authoritative info from Razorpay
    order = razor.order.fetch(o_id, timeout=RZP_TIMEOUT)
    pay   = razor.payment.fetch(p_id, timeout=RZP_TIMEOUT)

    # 3) Sanity: linkage + status
    if pay.get("order_id") != order.get("id"):
        raise HTTPException(status_code=400, detail="Payment/order mismatch")
    if pay.get("status") != "captured":
        raise HTTPException(status_code=400, detail="Payment not captured")

    # 4) Ownership
    notes = order.get("notes") or {}
    if str(notes.get("user_id")) != str(current_user.id) or notes.get("kind") != "wallet_topup":
        raise HTTPException(status_code=403, detail="Order not owned by this user")

    # 5) Amount from Razorpay (authoritative)
    amount_paise = int(pay.get("amount", 0) or 0)
    amount_rupees = (Decimal(amount_paise) / Decimal("100")).quantize(Decimal("0.01"))
    if amount_rupees <= 0:
        raise HTTPException(status_code=400, detail="Zero/invalid amount")

    # 6) Idempotent ledger credit
    try:
        add_ledger_row(
            db, user_id=current_user.id, amount_rupees=amount_rupees,
            kind="wallet_topup", reference=p_id, meta={"order_id": o_id}
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"success": True}


# ---- Withdraw (create payout request; debit happens when paid) ----
@router.post("/wallet/request_withdraw")
def wallet_request_withdraw(payload: dict = Body(...),
                            request: Request = None,
                            db: Session = Depends(get_db),
                            current_user: User = Depends(get_current_user)):
    _enforce_same_origin(request)

    amt_str = str(payload.get("amount_rupees", "0")).strip()
    try:
        amt = Decimal(amt_str).quantize(Decimal("0.01"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid amount")

    if amt <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount")

    try:
        bal = compute_balance(db, current_user.id)  # should return Decimal
        if amt > bal:
            raise HTTPException(status_code=400, detail="Insufficient balance")

        pr = open_payout_request(db, user_id=current_user.id, amount_rupees=amt)
        add_ledger_row(
            db, user_id=current_user.id, amount_rupees=-amt,
            kind="withdraw_hold", reference=f"payoutreq:{pr.id}", meta={}
        )

        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"success": True, "request_id": pr.id}
