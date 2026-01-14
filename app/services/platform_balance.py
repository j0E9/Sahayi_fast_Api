# app/services/platform_balance.py
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models import PlatformBalance, PlatformProfit
from decimal import Decimal


def increment_platform_balance(db: Session, amount: Decimal):
    """
    Increment platform balance for NEW commission only.
    This must be called exactly once per successful commission.
    """

    balance = db.query(PlatformBalance).with_for_update().first()
    if not balance:
        balance = PlatformBalance(
            total_company_profit=Decimal("0.00"),
            total_worker_distributed=Decimal("0.00"),
            total_refunded=Decimal("0.00"),
            total_withdrawn=Decimal("0.00"),
            available_profit=Decimal("0.00"),
            bank_balance=Decimal("0.00"),
        )
        db.add(balance)
        db.flush()

    balance.total_company_profit += amount
    balance.available_profit += amount
    balance.bank_balance += amount


def recompute_platform_balance(db: Session):
    balance = db.query(PlatformBalance).first()
    if not balance:
        balance = PlatformBalance()
        db.add(balance)
        db.flush()

    balance.total_company_profit = (
        db.query(func.coalesce(func.sum(PlatformProfit.amount), 0))
        .filter(
            PlatformProfit.type == "commission",
            PlatformProfit.direction == "credit"
        )
        .scalar()
    )

    balance.total_refunded = (
        db.query(func.coalesce(func.sum(PlatformProfit.amount), 0))
        .filter(PlatformProfit.type == "refund")
        .scalar()
    )

    balance.total_withdrawn = (
        db.query(func.coalesce(func.sum(PlatformProfit.amount), 0))
        .filter(PlatformProfit.type == "withdrawal")
        .scalar()
    )

    # ✅ FIXED
    balance.total_worker_distributed = (
        db.query(func.coalesce(func.sum(PlatformProfit.amount), 0))
        .filter(
            PlatformProfit.type == "escrow_release",
            PlatformProfit.direction == "debit"
        )
        .scalar()
    )

    balance.available_profit = (
        balance.total_company_profit - balance.total_withdrawn
    )

    # ✅ FIXED
    balance.bank_balance = balance.available_profit
