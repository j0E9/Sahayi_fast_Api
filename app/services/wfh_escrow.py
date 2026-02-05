# app/services/wfh_escrow.py
from decimal import Decimal
from sqlalchemy.orm import Session
from app.services.wallet import add_ledger_row
from app.models import Booking, PlatformProfit, WalletTransaction, User
from app.utils.IST_Time import ist_now
from app.services.platform_balance import recompute_platform_balance
from sqlalchemy import func

COMMISSION_RATE_GIVER = Decimal("0.05")
COMMISSION_RATE_WORKER = Decimal("0.05")


def _platform_profit_exists(db: Session, reference: str) -> bool:
    return db.query(PlatformProfit).filter(
        PlatformProfit.reference == reference
    ).first() is not None


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


def release_expired_wfh_escrows(db: Session):
    now = ist_now()

    bookings = (
        db.query(Booking)
        .filter(
            Booking.booking_type == "wfh",
            Booking.status == "WFH_IN_PROGRESS",
            Booking.escrow_locked == True,
            Booking.escrow_released == False,
            Booking.end_date <= now,
        )
        .all()
    )
    released_any = False

    for booking in bookings:
        release_ref = f"escrow_release_{booking.id}"
        commission_ref = f"wfh_commission_{booking.id}"

        with db.begin():
            # 🔒 Strong idempotency
            if _wallet_tx_exists(db, user_id=booking.worker_id, kind="wfh_escrow_release", reference=release_ref):
                # already paid worker → just ensure booking is finalized
                booking.escrow_locked = False
                booking.escrow_released = True
                booking.status = "WFH_COMPLETED"
                released_any = True

                # ✅ ALSO ensure platform commission exists (repair case)
                if not _platform_profit_exists(db, commission_ref):
                    base = Decimal(str(booking.escrow_amount or 0)).quantize(Decimal("0.01"))
                    if base > 0:
                        giver_commission = (base * COMMISSION_RATE_GIVER).quantize(Decimal("0.01"))
                        worker_commission = (base * COMMISSION_RATE_WORKER).quantize(Decimal("0.01"))
                        platform_profit = (giver_commission + worker_commission).quantize(Decimal("0.01"))

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
                                meta={
                                    "source": "wfh_auto_completion_repair",
                                    "completed_at": now.isoformat(),
                                },
                            )
                        )

                        platform_user = _get_platform_user(db)
                        add_ledger_row(
                            db=db,
                            user_id=platform_user.id,
                            amount_rupees=platform_profit,
                            kind="platform_commission",
                            reference=commission_ref,
                            meta={"booking_id": booking.id},
                        )

                continue

            base = Decimal(str(booking.escrow_amount or 0)).quantize(Decimal("0.01"))
            if base <= 0:
                continue

            giver_commission = (base * COMMISSION_RATE_GIVER).quantize(Decimal("0.01"))
            worker_commission = (base * COMMISSION_RATE_WORKER).quantize(Decimal("0.01"))
            platform_profit = (giver_commission + worker_commission).quantize(Decimal("0.01"))
            worker_net = (base - worker_commission).quantize(Decimal("0.01"))

            # 1️⃣ Pay worker (safe because we already checked above)
            add_ledger_row(
                db=db,
                user_id=booking.worker_id,
                amount_rupees=worker_net,
                kind="wfh_escrow_release",
                reference=release_ref,
                meta={"booking_id": booking.id},
            )

            # 2️⃣ Escrow release (reporting) — idempotent
            if not _platform_profit_exists(db, release_ref):
                db.add(
                    PlatformProfit(
                        booking_id=booking.id,
                        type="escrow_release",
                        direction="debit",
                        amount=base,
                        giver_commission=Decimal("0.00"),
                        worker_commission=Decimal("0.00"),
                        on_hold=False,
                        hold_for_user_id=booking.worker_id,
                        reference=release_ref,
                        meta={
                            "released_at": now.isoformat(),
                            "auto": True,
                        },
                    )
                )

            # 3️⃣ Platform commission (reporting + wallet)
            if not _platform_profit_exists(db, commission_ref):
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
                        meta={
                            "source": "wfh_auto_completion",
                            "completed_at": now.isoformat(),
                        },
                    )
                )

                platform_user = _get_platform_user(db)
                add_ledger_row(
                    db=db,
                    user_id=platform_user.id,
                    amount_rupees=platform_profit,
                    kind="platform_commission",
                    reference=commission_ref,
                    meta={"booking_id": booking.id},
                )

            # 4️⃣ Finalize booking
            booking.escrow_locked = False
            booking.escrow_released = True
            booking.status = "WFH_COMPLETED"
            released_any = True

        # 5️⃣ Recompute platform totals
    if released_any:
        recompute_platform_balance(db)

