#!/usr/bin/env python3
"""
scripts/fix_wallet_hmacs.py

Usage:
  # dry-run (prints what would be changed)
  python scripts/fix_wallet_hmacs.py

  # apply changes
  python scripts/fix_wallet_hmacs.py --apply

  # only fix a single user
  python scripts/fix_wallet_hmacs.py --user 2 --apply
"""
import sys
import argparse
from app.database import get_db
from app.services.wallet import _row_sig
from app.models import WalletTransaction
from decimal import Decimal

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="actually update DB")
    p.add_argument("--user", type=int, help="only fix this user id")
    args = p.parse_args()

    gen = get_db()
    db = next(gen)
    try:
        # find users with missing row_hmac
        q = db.query(WalletTransaction.user_id).filter((WalletTransaction.row_hmac == None) | (WalletTransaction.row_hmac == "") )
        if args.user:
            q = q.filter(WalletTransaction.user_id == int(args.user))
        users = sorted(set([r[0] for r in q.distinct().all()]))

        if not users:
            print("Nothing to fix.")
            return

        print("Users to fix:", users)
        total_updated = 0
        for uid in users:
            print("Processing user", uid)
            rows = db.query(WalletTransaction).filter(WalletTransaction.user_id == uid).order_by(WalletTransaction.id.asc()).all()
            prev_hash = None
            updated_for_user = 0
            for r in rows:
                # keep prev_hash in sync with DB values we encounter (respect any already-correct hmacs)
                if r.row_hmac:
                    prev_hash = r.row_hmac
                    continue

                # compute expected row_hmac using your app helper
                # ensure amount is Decimal and passed exactly as stored
                amt = r.amount
                # r.created_at is used as-is by _row_sig in app; pass it through
                expected = _row_sig(r.user_id, amt, r.kind, r.reference, prev_hash, r.created_at)

                print(f"  will update id={r.id} prev_hash={'<none>' if not prev_hash else prev_hash[:8]} -> new_row_hmac={expected[:8]}...")

                if args.apply:
                    # update previous_hash column (if it doesn't already match)
                    if (r.previous_hash or "") != (prev_hash or ""):
                        r.previous_hash = prev_hash or ""
                    r.row_hmac = expected
                    db.add(r)
                    updated_for_user += 1
                    total_updated += 1
                # set prev_hash to the newly computed hmac so next row chains correctly
                prev_hash = expected

            if args.apply:
                # commit after each user so partial progress is saved
                db.commit()
            print(f"  done user {uid}, rows updated (this run) = {updated_for_user}")

        print("finished. total_updated:", total_updated)
    finally:
        try:
            db.close()
        except:
            pass

if __name__ == "__main__":
    main()
