#!/usr/bin/env python3
"""
apply_razorpay_webhook_patch.py

Corrected version for running from inside:
fastapi_app/app/

- Backs up routers/payments_calls.py -> payments_calls.py.bak
- Backs up routers/realtime_jobs.py -> realtime_jobs.py.bak
- Replaces consolidated webhook in payments_calls.py
- Removes duplicate webhook in realtime_jobs.py
"""

import re
from pathlib import Path
import sys

# Because script is inside "app/", root is current directory.
ROOT = Path(".")        # fastapi_app/app/
PAYMENTS = ROOT / "routers" / "payments_calls.py"
REALTIME = ROOT / "routers" / "realtime_jobs.py"

def abort(msg):
    print("ERROR:", msg, file=sys.stderr)
    sys.exit(1)

if not PAYMENTS.exists():
    abort(f"payments_calls.py not found at: {PAYMENTS}")

if not REALTIME.exists():
    print("WARNING: realtime_jobs.py not found:", REALTIME)

def backup(p: Path):
    bak = p.with_suffix(p.suffix + ".bak")
    if not bak.exists():
        bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        print("Backup created:", bak)
    else:
        print("Backup already exists:", bak)

# Consolidated webhook code
CONSOLIDATED = r'''
@router.post("/razorpay/webhook")
async def razorpay_webhook(request: Request, x_razorpay_signature: str = Header(None), db: Session = Depends(get_db)):
    """
    Consolidated Razorpay webhook.
    """
    if not RAZORPAY_WEBHOOK_SECRET:
        logger.error("Webhook secret not configured")
        return JSONResponse({"error": "Webhook secret not configured"}, status_code=500)

    raw = await request.body()
    calc = hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not compare_digest(calc, (x_razorpay_signature or "")):
        logger.warning("Invalid razorpay webhook signature")
        return JSONResponse({"error": "Invalid signature"}, status_code=400)

    try:
        payload = await request.json()
    except Exception as e:
        logger.exception("Failed to parse webhook JSON: %s", e)
        return JSONResponse({"error": "Bad payload"}, status_code=400)

    evt = (payload.get("event") or "").lower()
    if evt != "payment.captured":
        return {"ok": True}

    entity = payload.get("payload", {}).get("payment", {}).get("entity", {}) or {}
    payment_id = entity.get("id")
    order_id = entity.get("order_id")
    amount_paise = int(entity.get("amount") or 0)
    notes = entity.get("notes") or {}

    logger.info("Webhook received payment=%s order=%s amount_paise=%s", payment_id, order_id, amount_paise)

    # Find booking
    booking = None
    if notes.get("booking_id"):
        try:
            booking = db.get(Booking, int(notes["booking_id"]))
        except:
            booking = None

    if not booking and order_id:
        booking = (
            db.query(Booking)
            .filter(
                (Booking.razor_order_id == order_id) |
                (Booking.extra_razor_order_id == order_id)
            )
            .with_for_update()
            .first()
        )

    if not booking:
        logger.info("Webhook no booking for order=%s", order_id)
        return {"ok": True}

    is_extra = (booking.extra_razor_order_id == order_id) or bool(notes.get("extra_minutes"))

    try:
        with db.begin():
            from decimal import Decimal
            rupees = (Decimal(amount_paise) / Decimal(100)).quantize(Decimal("0.01"))

            if not is_extra:
                expected_r = Decimal(int((booking.rate or 0) * (booking.quantity or 0)))
                if int(expected_r * 100) == amount_paise and not booking.payment_completed:
                    _mark_booking_paid(
                        db=db,
                        booking=booking,
                        provider=booking.provider,
                        worker=booking.worker,
                        total_tokens=int(expected_r),
                        payment_id=payment_id,
                        order_id=order_id,
                        method="razorpay",
                    )
                    logger.info("Booking token payment marked paid")
            else:
                reference = payment_id
                add_ledger_row(
                    db=db,
                    user_id=booking.worker_id,
                    amount_rupees=str(rupees),
                    kind="extra_payment",
                    reference=reference,
                    meta={"booking_id": booking.id, "order_id": order_id}
                )

                booking.extra_payment_completed = True
                booking.extra_razor_payment_id = payment_id
                booking.extra_razor_order_id = order_id
                booking.extra_razor_amount = float(rupees)

                if notes.get("extra_minutes") and not booking.proposed_extra_minutes:
                    booking.proposed_extra_minutes = int(notes.get("extra_minutes"))

                logger.info("Extra payment credited for booking=%s", booking.id)

    except Exception as e:
        logger.exception("Webhook error: %s", e)
        return JSONResponse({"error": "internal error"}, status_code=500)

    return {"ok": True}
'''.lstrip("\n")

def replace_webhook_in_payments():
    text = PAYMENTS.read_text(encoding="utf-8")

    pattern = re.compile(
        r'@router\.post\("/razorpay/webhook"\)[\s\S]*?return\s+\{\s*"ok"\s*:\s*True\s*\}',
        re.DOTALL
    )

    if pattern.search(text):
        print("Existing webhook found — replacing...")
        text = pattern.sub(CONSOLIDATED, text)
    else:
        print("No webhook found — inserting before '# --- helpers'")
        insert_at = text.find("# --- helpers")
        if insert_at == -1:
            text += "\n\n" + CONSOLIDATED
        else:
            text = text[:insert_at] + CONSOLIDATED + "\n\n" + text[insert_at:]

    PAYMENTS.write_text(text, encoding="utf-8")
    print("payments_calls.py updated")

def remove_webhook_from_realtime():
    if not REALTIME.exists():
        return

    text = REALTIME.read_text(encoding="utf-8")

    pattern = re.compile(
        r'@router\.post\("/razorpay/webhook"\)[\s\S]*?return\s+\{\s*"ok"\s*:\s*True\s*\}',
        re.DOTALL
    )

    if pattern.search(text):
        print("Removing duplicate webhook from realtime_jobs.py...")
        text = pattern.sub("", text)
        REALTIME.write_text(text, encoding="utf-8")
    else:
        print("No webhook inside realtime_jobs.py")

def main():
    backup(PAYMENTS)
    backup(REALTIME)

    replace_webhook_in_payments()
    remove_webhook_from_realtime()

    print("\n✔ Patch applied successfully.\nRestart your FastAPI server now.")

if __name__ == "__main__":
    main()
