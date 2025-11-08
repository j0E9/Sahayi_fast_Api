# app/razor_client.py
import os
import razorpay

KID = os.getenv("RAZORPAY_KEY_ID")
SEC = os.getenv("RAZORPAY_KEY_SECRET")

if not KID or not SEC:
    raise RuntimeError("Razorpay keys missing. Check .env and load_dotenv placement in app/main.py")

# helpful one-time log on startup
print(f"[RAZORPAY] Using Key ID prefix: {KID[:12]} | Secret length: {len(SEC)}")

client = razorpay.Client(auth=(KID, SEC))
