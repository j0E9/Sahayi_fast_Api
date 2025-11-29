# app/services/wallet.py
import os
import hmac
import hashlib
import json
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models import WalletTransaction, PayoutRequest
from decimal import Decimal, InvalidOperation
from datetime import datetime
from sqlalchemy.exc import IntegrityError

WALLET_HMAC_SECRET = os.getenv("WALLET_HMAC_SECRET", "change-this-secret")


def _row_sig(user_id: int, amount: Decimal, kind: str, reference: str | None,
             previous_hash: str | None, created_at: datetime) -> str:
    """
    Deterministic HMAC for a wallet row. Keep exactly same format across code.
    """
    # Normalize amount string to keep determinism (Decimal -> string preserves precision)
    amt = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    data = f"{int(user_id)}|{amt}|{kind}|{reference or ''}|{previous_hash or ''}|{created_at.isoformat()}"
    return hmac.new(WALLET_HMAC_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()


def add_ledger_row(db: Session, *, user_id: int, amount_rupees, kind: str,
                   reference: str | None = None, meta: dict | None = None):
    """
    Idempotent insertion of a wallet ledger row. Does NOT commit.
    - Serializes per-user via pg_advisory_xact_lock to avoid race on previous_hash.
    - Maintains HMAC chain: previous_hash -> row_hmac.
    - Returns the WalletTransaction instance (existing or newly created).
    """
    # --- normalize amount ---
    try:
        amt = Decimal(str(amount_rupees))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid amount_rupees: {amount_rupees}") from exc

    amt = amt.quantize(Decimal("0.01"))
    if amt == Decimal("0.00"):
        raise ValueError("amount_rupees cannot be zero")

    # --- idempotency pre-check (fast path) ---
    if reference:
        existing = (
            db.query(WalletTransaction)
            .filter(
                WalletTransaction.user_id == user_id,
                WalletTransaction.kind == kind,
                WalletTransaction.reference == reference
            )
            .first()
        )
        if existing:
            return existing

    # --- acquire per-user advisory xact lock to serialize wallet writes ---
    try:
        # use user_id as lock key (int). pg_advisory_xact_lock releases at tx end.
        db.execute("SELECT pg_advisory_xact_lock(:k)", {"k": int(user_id)})
    except Exception:
        # If Postgres-specific call fails (unlikely on Postgres), continue without it.
        pass

    # --- re-check idempotency after lock to avoid race ---
    if reference:
        existing = (
            db.query(WalletTransaction)
            .filter(
                WalletTransaction.user_id == user_id,
                WalletTransaction.kind == kind,
                WalletTransaction.reference == reference
            )
            .first()
        )
        if existing:
            return existing

    # --- get previous row_hmac for this user AFTER lock ---
    prev = (
        db.query(WalletTransaction)
        .filter(WalletTransaction.user_id == user_id)
        .order_by(WalletTransaction.id.desc())
        .with_for_update(read=True)
        .first()
    )
    previous_hash = getattr(prev, "row_hmac", None) or None

    created_at = datetime.utcnow()

    # metadata JSON string -> note: your model maps DB column "metadata" to Python attr `meta_json`
    meta_json = json.dumps(meta, separators=(",", ":"), ensure_ascii=False) if meta else None

    # compute row_hmac using canonical _row_sig
    row_hmac = _row_sig(user_id, amt, kind, reference, previous_hash, created_at)

    # Build WalletTransaction object - use attribute names from your models:
    wt = WalletTransaction(
        user_id=user_id,
        amount=amt,
        kind=kind,
        reference=reference,
        created_at=created_at,
        previous_hash=previous_hash,
        row_hmac=row_hmac,
        meta_json=meta_json,   # **important**: model attribute is meta_json (DB column named "metadata")
    )

    db.add(wt)
    try:
        # flush to assign id / detect constraint errors
        db.flush()
    except IntegrityError:
        # likely concurrent insert with same (user_id, kind, reference) unique constraint
        db.rollback()
        if reference:
            existing = (
                db.query(WalletTransaction)
                .filter(
                    WalletTransaction.user_id == user_id,
                    WalletTransaction.kind == kind,
                    WalletTransaction.reference == reference
                )
                .first()
            )
            if existing:
                return existing
        # could not resolve, re-raise
        raise

    return wt


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
        if sig != r.row_hmac:
            return False
        if next_prev and next_prev != r.row_hmac:
            return False
        next_prev = r.previous_hash
    return True


def compute_row_hmac(*, user_id: int, amount: Decimal,
                     kind: str, reference: str | None = None,
                     previous_hash: str | None = None,
                     created_at: datetime | None = None,
                     meta_json: str | None = None) -> str:
    """
    Backwards-compatible compute helper used by maintenance scripts.

    Primary canonical HMAC used by add_ledger_row is _row_sig which encodes:
      f"{user_id}|{amount}|{kind}|{reference or ''}|{previous_hash or ''}|{created_at.isoformat()}"

    This wrapper:
    - If created_at provided -> use _row_sig(created_at)
    - If created_at not provided, falls back to a deterministic pseudo-created_at (1970-01-01)
      to avoid a second unrelated HMAC algorithm. Prefer callers to pass created_at.
    """
    # Normalize amount to Decimal
    try:
        amt = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    except Exception:
        amt = Decimal("0.00")

    if created_at is not None:
        return _row_sig(int(user_id), amt, kind, reference, previous_hash, created_at)

    # Fallback deterministic created_at (legacy callers). Using epoch keeps single algorithm.
    pseudo_created = datetime(1970, 1, 1)
    return _row_sig(int(user_id), amt, kind, reference, previous_hash, pseudo_created)


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
# ================================

def get_user_balance(db, user_id: int):
    return compute_balance(db, user_id)


def wallet_balance(db, user_id: int):
    return compute_balance(db, user_id)


def get_wallet_history(db, user_id: int, limit: int | None = None):
    from app.models import WalletTransaction
    q = db.query(WalletTransaction)\
          .filter(WalletTransaction.user_id == user_id)\
          .order_by(WalletTransaction.id.desc())
    if limit:
        q = q.limit(int(limit))
    return q.all()


def get_transactions(db, user_id: int, limit: int | None = None):
    return get_wallet_history(db, user_id, limit)


def add_wallet_transaction(db, *, user_id: int, amount_rupees: int,
                           kind: str, reference: str | None = None,
                           meta: dict | None = None):
    return add_ledger_row(db, user_id=user_id, amount_rupees=amount_rupees,
                          kind=kind, reference=reference, meta=meta)


def add_wallet_ledger(db, *, user_id: int, amount_rupees: int,
                      kind: str, reference: str | None = None,
                      meta: dict | None = None):
    return add_ledger_row(db, user_id=user_id, amount_rupees=amount_rupees,
                          kind=kind, reference=reference, meta=meta)


def verify_wallet_chain(db, user_id: int) -> bool:
    return verify_chain(db, user_id)


# -----------------
# Amount utilities
# -----------------
from decimal import Decimal as _D


def _quantize_amount(value) -> Decimal:
    return _D(str(value)).quantize(_D("1.00"))


def quantize_amount(value) -> Decimal:
    return _quantize_amount(value)


def format_amount(value) -> str:
    return f"{_quantize_amount(value):.2f}"


def rupees_to_paise_int(value) -> int:
    return int((_quantize_amount(value) * 100))


def paise_to_rupees_decimal(paise: int) -> Decimal:
    return _quantize_amount(_D(paise) / _D(100))
