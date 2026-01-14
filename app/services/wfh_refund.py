from decimal import Decimal
from sqlalchemy.orm import Session
from app.models import PlatformProfit, WalletTransaction, User
from app.services.wallet import add_ledger_row
from app.utils.IST_Time import ist_now
from app.services.platform_balance import recompute_platform_balance


def _wallet_tx_exists(db: Session, *, user_id: int, kind: str, reference: str) -> bool:
    return db.query(WalletTransaction).filter(
        WalletTransaction.user_id == user_id,
        WalletTransaction.kind == kind,
        WalletTransaction.reference == reference,
    ).first() is not None


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


def refund_wfh_escrow(*, db: Session, booking, reason: str):
    """
    Unified WFH escrow refund.
    reason: 'manual_cancel' | 'auto_cancel'
    """

    refund_ref = f"wfh_refund_{reason}_{booking.id}"
    commission_ref = f"wfh_commission_{booking.id}"

    # 🔒 Strong idempotency
    if db.query(PlatformProfit).filter(
        PlatformProfit.reference == refund_ref
    ).first():
        return False

    base = booking.escrow_amount or Decimal("0.00")

    # 🔍 Fetch original commission (single source of truth)
    commission = db.query(PlatformProfit).filter(
        PlatformProfit.reference == commission_ref,
        PlatformProfit.type == "commission",
    ).first()

    giver_commission = commission.giver_commission if commission else Decimal("0.00")
    worker_commission = commission.worker_commission if commission else Decimal("0.00")
    platform_profit = giver_commission + worker_commission

    # 1️⃣ Refund provider (idempotent)
    if not _wallet_tx_exists(
        db,
        user_id=booking.provider_id,
        kind="wfh_refund",
        reference=refund_ref,
    ):
        add_ledger_row(
            db=db,
            user_id=booking.provider_id,
            amount_rupees=base,
            kind="wfh_refund",
            reference=refund_ref,
            meta={
                "booking_id": booking.id,
                "reason": reason,
            },
        )

    # 2️⃣ Reverse platform wallet commission (if existed)
    if platform_profit > 0:
        platform_user = _get_platform_user(db)
        add_ledger_row(
            db=db,
            user_id=platform_user.id,
            amount_rupees=-platform_profit,
            kind="platform_commission_reversal",
            reference=refund_ref,
            meta={
                "booking_id": booking.id,
                "original_commission_ref": commission_ref,
            },
        )

    # 3️⃣ Reporting record
    db.add(
        PlatformProfit(
            booking_id=booking.id,
            type="refund",
            direction="debit",
            amount=base,
            giver_commission=giver_commission,
            worker_commission=worker_commission,
            on_hold=False,
            hold_for_user_id=booking.provider_id,
            reference=refund_ref,
            meta={
                "reason": reason,
                "refunded_at": ist_now().isoformat(),
                "commission_reversed": bool(platform_profit),
                "commission_reference": commission_ref,
            },
        )
    )

    # 4️⃣ Final booking state
    booking.status = "WFH_CANCELLED"
    booking.escrow_locked = False
    booking.escrow_released = True

    # 5️⃣ Recompute platform totals (NO commit here)
    recompute_platform_balance(db)

    return True
