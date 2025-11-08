# app/routers/wallet_pages.py
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, WalletTransaction, PayoutRequest
from app.routers.auth import verify_token
from app.services.wallet import (
    get_user_balance,
    add_wallet_transaction,
    _quantize_amount,   # if you prefer to keep this private, copy logic locally
)

# Inline Jinja rendering
from jinja2 import Environment, BaseLoader, select_autoescape

router = APIRouter(tags=["wallet"])
env = Environment(loader=BaseLoader(), autoescape=select_autoescape(["html", "xml"]))

# -----------------------------
# Auth helper: current user
# -----------------------------
def get_current_user(
    payload: dict = Depends(verify_token),
    db: Session = Depends(get_db),
) -> User:
    try:
        uid = int(payload.get("sub"))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    user = db.get(User, uid)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

# -----------------------------
# /wallet (HTML)
# -----------------------------
@router.get("/wallet", response_class=HTMLResponse)
def wallet_view(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    balance = get_user_balance(db, me.id)

    txs: List[WalletTransaction] = (
        db.query(WalletTransaction)
        .filter(WalletTransaction.user_id == me.id)
        .order_by(WalletTransaction.created_at.desc())
        .limit(50)
        .all()
    )
    payouts: List[PayoutRequest] = (
        db.query(PayoutRequest)
        .filter(PayoutRequest.user_id == me.id)
        .order_by(PayoutRequest.created_at.desc())
        .limit(20)
        .all()
    )

    html = """
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8"/>
      <title>Wallet — Sahayi</title>
      <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body class="p-3">
      <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-3">
          <h3>Wallet</h3>
          <a class="btn btn-sm btn-outline-secondary" href="/welcome">Back</a>
        </div>

        <div class="card mb-3">
          <div class="card-body d-flex justify-content-between align-items-center">
            <div><strong>Balance</strong><div class="text-muted">Available</div></div>
            <div style="font-size:24px;">₹{{ balance }}</div>
            <div>
              <a class="btn btn-success btn-sm" href="/wallet/deposit">Deposit</a>
              <a class="btn btn-warning btn-sm" href="/wallet/withdraw">Withdraw</a>
            </div>
          </div>
        </div>

        <div class="row">
          <div class="col-md-7">
            <h5>Recent Transactions</h5>
            <div class="table-responsive">
              <table class="table table-sm">
                <thead><tr><th>#</th><th>Kind</th><th>Amount</th><th>Ref</th><th>When</th></tr></thead>
                <tbody>
                  {% for t in txs %}
                    <tr>
                      <td>{{ t.id }}</td>
                      <td>{{ t.kind }}</td>
                      <td>{{ "%.2f"|format(t.amount) }}</td>
                      <td>{{ t.reference or "-" }}</td>
                      <td>{{ t.created_at.strftime("%Y-%m-%d %H:%M") }}</td>
                    </tr>
                  {% endfor %}
                </tbody>
              </table>
            </div>
          </div>

          <div class="col-md-5">
            <h5>Payout Requests</h5>
            <ul class="list-group">
              {% for p in payouts %}
                <li class="list-group-item d-flex justify-content-between align-items-center">
                  ₹{{ "%.2f"|format(p.amount) }} <small class="text-muted">({{ p.status }})</small>
                </li>
              {% endfor %}
            </ul>
          </div>
        </div>
      </div>
    </body>
    </html>
    """
    tmpl = env.from_string(html)
    return HTMLResponse(tmpl.render(balance=balance, txs=txs, payouts=payouts))

# -----------------------------
# /wallet/deposit (GET/POST)
# -----------------------------
@router.get("/wallet/deposit", response_class=HTMLResponse)
def wallet_deposit_form(
    me: User = Depends(get_current_user),
):
    html = """
    <!doctype html><html><head><meta charset="utf-8"><title>Deposit</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="p-3">
      <div class="container">
        <h4>Deposit</h4>
        <form method="post">
          <div class="mb-2"><label>Amount</label><input name="amount" class="form-control" placeholder="Amount in INR"></div>
          <button class="btn btn-primary">Deposit (dev)</button>
          <a href="/wallet" class="btn btn-link">Cancel</a>
        </form>
        <p class="text-muted mt-2">In production integrate a payment processor and only credit after server-side confirmation (webhook).</p>
      </div>
    </body></html>
    """
    return HTMLResponse(env.from_string(html).render())

@router.post("/wallet/deposit")
def wallet_deposit(
    amount: str = Form(...),
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    try:
        amt = _quantize_amount(Decimal(amount))
    except (InvalidOperation, ValueError):
        # mimic flash+redirect by simple query param message
        return RedirectResponse(url="/wallet/deposit?error=invalid_amount", status_code=303)

    if amt <= 0:
        return RedirectResponse(url="/wallet/deposit?error=amount_must_be_positive", status_code=303)

    # DEV ONLY: direct credit
    add_wallet_transaction(
        db=db,
        user_id=me.id,
        amount=amt,
        kind="deposit",
        reference="manual-dev",
        metadata="manual deposit (dev)",
    )
    return RedirectResponse(url="/wallet", status_code=303)

# -----------------------------
# /wallet/withdraw (GET/POST)
# -----------------------------
@router.get("/wallet/withdraw", response_class=HTMLResponse)
def wallet_withdraw_form(
    me: User = Depends(get_current_user),
):
    html = """
    <!doctype html><html><head><meta charset="utf-8"><title>Withdraw</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="p-3">
      <div class="container">
        <h4>Withdraw / Payout</h4>
        <form method="post">
          <div class="mb-2"><label>Amount</label><input name="amount" class="form-control" placeholder="Amount in INR"></div>
          <div class="mb-2"><label>Note</label><input name="note" class="form-control" placeholder="Bank details or note"></div>
          <button class="btn btn-warning">Request Payout</button>
          <a href="/wallet" class="btn btn-link">Cancel</a>
        </form>
        <p class="text-muted mt-2">Your request will be reviewed. Once paid, admin updates the payout status and records 'payout' tx with external_ref.</p>
      </div>
    </body></html>
    """
    return HTMLResponse(env.from_string(html).render())

@router.post("/wallet/withdraw")
def wallet_withdraw(
    amount: str = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    # parse & validate
    try:
        amt = _quantize_amount(Decimal(amount))
    except (InvalidOperation, ValueError):
        return RedirectResponse(url="/wallet/withdraw?error=invalid_amount", status_code=303)

    if amt <= 0:
        return RedirectResponse(url="/wallet/withdraw?error=amount_must_be_positive", status_code=303)

    balance = get_user_balance(db, me.id)
    if amt > balance:
        return RedirectResponse(url="/wallet/withdraw?error=insufficient_balance", status_code=303)

    # Create payout request (pending)
    pr = PayoutRequest(user_id=me.id, amount=amt, status="pending", note=note)
    db.add(pr)
    db.commit()
    db.refresh(pr)

    # Optional hold (recommended): prevent double-spend
    add_wallet_transaction(
        db=db,
        user_id=me.id,
        amount=-amt,
        kind="withdrawal_hold",
        reference=f"payoutreq:{pr.id}",
        metadata="hold for payout request",
    )

    return RedirectResponse(url="/wallet", status_code=303)

# -----------------------------
# Admin pages
# -----------------------------
def require_admin(me: User = Depends(get_current_user)) -> User:
    # Adjust according to your model (e.g., me.role == "admin" or me.is_admin flag)
    if not getattr(me, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin only")
    return me

@router.get("/admin/payouts", response_class=HTMLResponse)
def admin_payouts_list(
    db: Session = Depends(get_db),
    me: User = Depends(require_admin),
):
    prs: List[PayoutRequest] = (
        db.query(PayoutRequest).order_by(PayoutRequest.created_at.desc()).limit(200).all()
    )
    html = """<!doctype html><html><head>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="p-3"><div class="container"><h4>Payout Requests</h4><ul class="list-group">
    {% for p in prs %}
      <li class='list-group-item d-flex justify-content-between align-items-center'>
        <span>#{{ p.id }} — User {{ p.user_id }} — ₹{{ "%.2f"|format(p.amount) }} — {{ p.status }}</span>
        <span>
          <a class="btn btn-sm btn-success" href="/admin/payouts/{{ p.id }}/approve">Approve</a>
        </span>
      </li>
    {% endfor %}
    </ul></div></body></html>"""
    return HTMLResponse(env.from_string(html).render(prs=prs))

@router.get("/admin/payouts/{pr_id}/approve")
def admin_payouts_approve(
    pr_id: int,
    db: Session = Depends(get_db),
    me: User = Depends(require_admin),
):
    pr = db.get(PayoutRequest, pr_id)
    if not pr:
        raise HTTPException(status_code=404, detail="Payout not found")
    if pr.status != "pending":
        # just send back to list if not pending
        return RedirectResponse(url="/admin/payouts?error=not_pending", status_code=303)

    # mark paid
    pr.status = "paid"
    pr.processed_at = datetime.utcnow()
    pr.external_ref = "manual-admin-paid"
    db.add(pr)
    db.commit()

    # IMPORTANT: we already deducted with 'withdrawal_hold' at request time.
    # Do NOT add another negative transaction here.
    return RedirectResponse(url="/admin/payouts?ok=paid", status_code=303)
