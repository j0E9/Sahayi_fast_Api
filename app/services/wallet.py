# app/services/wallet.py
import os, hmac, hashlib, json
from decimal import Decimal
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models import WalletTransaction, PayoutRequest

WALLET_HMAC_SECRET = os.getenv("WALLET_HMAC_SECRET", "change-this-secret")

def _row_sig(user_id: int, amount: Decimal, kind: str, reference: str | None,
             previous_hash: str | None, created_at: datetime) -> str:
    data = f"{user_id}|{amount}|{kind}|{reference or ''}|{previous_hash or ''}|{created_at.isoformat()}"
    return hmac.new(WALLET_HMAC_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()

def add_ledger_row(db: Session, *, user_id: int, amount_rupees: Decimal | int | float,
                   kind: str, reference: str | None, meta: dict | None = None) -> WalletTransaction:
    """Positive amount = credit; negative = debit."""
    prev = db.query(WalletTransaction)\
             .filter(WalletTransaction.user_id == user_id)\
             .order_by(WalletTransaction.id.desc()).first()
    created_at = datetime.utcnow()
    amt = Decimal(str(amount_rupees)).quantize(Decimal("0.01"))
    row = WalletTransaction(
        user_id=user_id,
        amount=amt,
        kind=kind,
        reference=reference,
        previous_hash=prev.row_hmac if prev else None,
        meta_json=json.dumps(meta or {}),
        created_at=created_at,
        row_hmac="",  # set next
    )
    row.row_hmac = _row_sig(row.user_id, row.amount, row.kind, row.reference,
                            row.previous_hash, created_at)
    db.add(row)
    return row

def compute_balance(db: Session, user_id: int) -> Decimal:
    total = db.query(func.coalesce(func.sum(WalletTransaction.amount), 0))\
              .filter(WalletTransaction.user_id == user_id).scalar()
    return Decimal(total).quantize(Decimal("1.00"))

def verify_chain(db: Session, user_id: int) -> bool:
    """Iterate newest→oldest and verify HMAC links."""
    rows = db.query(WalletTransaction)\
             .filter(WalletTransaction.user_id == user_id)\
             .order_by(WalletTransaction.id.desc()).all()
    next_prev = None
    for r in rows:
        sig = _row_sig(r.user_id, r.amount, r.kind, r.reference, r.previous_hash, r.created_at)
        if sig != r.row_hmac: return False
        if next_prev and next_prev != r.row_hmac: return False
        next_prev = r.previous_hash
    return True

def open_payout_request(db: Session, *, user_id: int, amount_rupees: int) -> PayoutRequest:
    from decimal import Decimal
    pr = PayoutRequest(
        user_id=user_id,
        amount=Decimal(str(amount_rupees)).quantize(Decimal("1.00")),
        status="pending",
        note="User requested withdrawal",
    )
    db.add(pr)
    return pr

# ================================
# Compatibility aliases (old -> new)
# Keep wallet_pages.py imports working without edits
# ================================

# old: get_user_balance -> new: compute_balance
def get_user_balance(db, user_id: int):
    return compute_balance(db, user_id)

# sometimes code calls wallet_balance(...)
def wallet_balance(db, user_id: int):
    return compute_balance(db, user_id)

# old: get_wallet_history -> helper using WalletTransaction
def get_wallet_history(db, user_id: int, limit: int | None = None):
    from app.models import WalletTransaction
    q = db.query(WalletTransaction)\
          .filter(WalletTransaction.user_id == user_id)\
          .order_by(WalletTransaction.id.desc())
    if limit:
        q = q.limit(int(limit))
    return q.all()

# some code might use get_transactions(...)
def get_transactions(db, user_id: int, limit: int | None = None):
    return get_wallet_history(db, user_id, limit)

# old: add_wallet_transaction -> new: add_ledger_row
def add_wallet_transaction(db, *, user_id: int, amount_rupees: int,
                           kind: str, reference: str | None = None,
                           meta: dict | None = None):
    return add_ledger_row(db, user_id=user_id, amount_rupees=amount_rupees,
                          kind=kind, reference=reference, meta=meta)

# old: add_wallet_ledger -> new: add_ledger_row
def add_wallet_ledger(db, *, user_id: int, amount_rupees: int,
                      kind: str, reference: str | None = None,
                      meta: dict | None = None):
    return add_ledger_row(db, user_id=user_id, amount_rupees=amount_rupees,
                          kind=kind, reference=reference, meta=meta)

# old: verify_wallet_chain -> new: verify_chain
def verify_wallet_chain(db, user_id: int) -> bool:
    return verify_chain(db, user_id)

# -----------------
# Amount utilities
# -----------------
from decimal import Decimal as _D

def _quantize_amount(value) -> Decimal:
    """
    Convert value (int/float/str/Decimal) to Decimal with 2 decimals.
    Safe for rupee amounts (e.g., 199 -> 199.00).
    """
    return _D(str(value)).quantize(_D("1.00"))

# public-friendly alias some modules may import
def quantize_amount(value) -> Decimal:
    return _quantize_amount(value)

def format_amount(value) -> str:
    """Format to '123.45' (string) consistently."""
    return f"{_quantize_amount(value):.2f}"

def rupees_to_paise_int(value) -> int:
    """₹ -> paise integer (e.g., 199.50 -> 19950)."""
    return int((_quantize_amount(value) * 100))

def paise_to_rupees_decimal(paise: int) -> Decimal:
    """paise int -> ₹ Decimal(2dp)."""
    return _quantize_amount(_D(paise) / _D(100))
