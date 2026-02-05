from decimal import Decimal
from sqlalchemy.orm import Session
from app.models import Booking, PlatformProfit, WalletTransaction
from app.services.wallet import add_ledger_row
from app.utils.IST_Time import ist_now
from app.services.platform_balance import recompute_platform_balance


def _wallet_tx_exists(db: Session, *, user_id: int, kind: str, reference: str) -> bool:
    return db.query(WalletTransaction).filter(
        WalletTransaction.user_id == user_id,
        WalletTransaction.kind == kind,
        WalletTransaction.reference == reference,
    ).first() is not None


def _platform_profit_exists(db: Session, reference: str) -> bool:
    return db.query(PlatformProfit).filter(
        PlatformProfit.reference == reference
    ).first() is not None


def refund_onsite_escrow(*, db: Session, booking: Booking, reason: str = "auto_cancel") -> bool:
    """
    ✅ Onsite cancel refund → ALWAYS credit job giver WALLET
    No Razorpay refund to bank.
    """

    # Safety checks
    if booking.booking_type in {"wfh"}:
        return False

    status = (booking.status or "").strip().lower()
    if status not in {"cancelled", "canceled"}:
        return False

    if not getattr(booking, "escrow_locked", False):
        return False

    if getattr(booking, "escrow_released", False):
        return False

    now = ist_now()
    refund_ref = f"onsite_refund_{booking.id}"


    # 🔒 Strong idempotency
    if _platform_profit_exists(db, refund_ref):
        booking.escrow_locked = False
        booking.escrow_released = True
        booking.payment_completed = True
        booking.payment_required = False
        booking.razorpay_status = "wallet_refunded"
        return True

    base = Decimal(str(booking.escrow_amount or 0)).quantize(Decimal("0.01"))
    giver_commission = Decimal("0.00")
    refund_total = base

    if refund_total <= 0:
        booking.escrow_locked = False
        booking.escrow_released = True
        return False

    # ✅ 1) Refund into job giver WALLET
    if not _wallet_tx_exists(db, user_id=booking.provider_id, kind="onsite_refund", reference=refund_ref):
        add_ledger_row(
            db=db,
            user_id=booking.provider_id,
            amount_rupees=refund_total,
            kind="onsite_refund",
            reference=refund_ref,
            meta={
                "booking_id": booking.id,
                "reason": reason,
                "refunded_at": now.isoformat(),
                "base_amount": str(base),
                "giver_commission_refunded": str(giver_commission),
                "refund_mode": "wallet_only",
            },
        )

    # ✅ 2) Reporting record
    if not _platform_profit_exists(db, refund_ref):
        db.add(
            PlatformProfit(
                booking_id=booking.id,
                type="refund",
                direction="debit",
                amount=refund_total,
                giver_commission=giver_commission,
                worker_commission=Decimal("0.00"),
                on_hold=False,
                reference=refund_ref,
                meta={
                    "booking_type": "onsite",
                    "reason": reason,
                    "refunded_at": now.isoformat(),
                    "refund_mode": "wallet_only",
                },
            )
        )

    # ✅ 3) Unlock escrow & finalize booking payment flags
    booking.escrow_locked = False
    booking.escrow_released = True
    booking.payment_completed = True
    booking.payment_required = False
    booking.razorpay_status = "wallet_refunded"

    db.flush()

    recompute_platform_balance(db)
    return True
