# app/backfill_wallet_hmac.py
"""
Backfill script for wallet_transaction.previous_hash and row_hmac.

Place this file at: app/backfill_wallet_hmac.py
Run from project root (virtualenv active).

ENV:
  - WALLET_HMAC_SECRET  (must match your live app secret)
  - DRY_RUN (default=1) if 1/true/yes -> print only; if 0/false/no -> apply changes

Examples (PowerShell):
  $env:WALLET_HMAC_SECRET = "your_secret"
  $env:DRY_RUN = "1"
  python .\app\backfill_wallet_hmac.py

  # to apply
  $env:WALLET_HMAC_SECRET = "your_secret"
  $env:DRY_RUN = "0"
  python .\app\backfill_wallet_hmac.py
"""
import os
from decimal import Decimal
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import WalletTransaction
from app.services.wallet import compute_row_hmac

# interpret DRY_RUN env: truthy values -> do not commit; falsy -> apply
DRY_RUN = os.getenv("DRY_RUN", "1").strip().lower() in ("1", "true", "yes")

def main():
    db: Session = SessionLocal()

    # find users who have at least one row with missing/empty row_hmac
    bad_users = [
        uid
        for (uid,) in db.query(WalletTransaction.user_id)
        .filter((WalletTransaction.row_hmac == None) | (WalletTransaction.row_hmac == ""))
        .distinct()
    ]

    print("Users to fix:", bad_users)
    total_updates = 0

    for user_id in bad_users:
        print(f"\nProcessing user {user_id}")

        rows = (
            db.query(WalletTransaction)
            .filter(WalletTransaction.user_id == user_id)
            .order_by(WalletTransaction.id.asc())
            .all()
        )

        # start chain with empty previous hash (None / '')
        prev_hash = ""  # compute_row_hmac expects previous_hash or None

        for r in rows:
            # compute canonical HMAC using the row's created_at
            try:
                # amount stored in DB may be Decimal-like; ensure Decimal passed
                amount_dec = Decimal(r.amount)
            except Exception:
                amount_dec = Decimal("0.00")

            new_hmac = compute_row_hmac(
                user_id=r.user_id,
                amount=amount_dec,
                kind=r.kind,
                reference=(r.reference or None),
                previous_hash=(prev_hash or None),
                created_at=r.created_at,
                meta_json=(r.meta_json or None),
            )

            expected_prev = prev_hash or None
            needs_update = ( (r.row_hmac or "") != (new_hmac or "") ) or ( (r.previous_hash or None) != expected_prev )

            if needs_update:
                print(f"  will update id={r.id} prev_hash={str(prev_hash)[:8]} -> new_hmac={new_hmac[:8]}")
                if not DRY_RUN:
                    # store previous_hash as None if empty, else the string
                    r.previous_hash = expected_prev
                    r.row_hmac = new_hmac
                    db.add(r)
                    total_updates += 1

            # advance chain: next row's previous_hash should equal this row's HMAC
            prev_hash = new_hmac

        if not DRY_RUN:
            try:
                db.commit()
                print(f"  committed changes for user {user_id}")
            except Exception as e:
                db.rollback()
                print(f"  commit failed for user {user_id}: {e}")

    print(f"\nFinished. Updates written: {total_updates}")

if __name__ == "__main__":
    main()
