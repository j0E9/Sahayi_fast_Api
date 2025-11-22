# smoke_test.py
import requests
import sys
import os

BASE = os.getenv("APP_BASE", "http://127.0.0.1:8000")
PHONE = os.getenv("SMOKE_PHONE", "+919846636912")   # not used if password below
PASSWORD = os.getenv("SMOKE_PASSWORD", "paul9846")
BOOKING_ID = os.getenv("SMOKE_BOOKING_ID", "46")

def form_login(session: requests.Session):
    # GET login page to pick up any cookies
    session.get(BASE + "/login")
    resp = session.post(BASE + "/login", data={
        "method": "password",
        "phone": PHONE,
        "password": PASSWORD,
    })
    print("login ->", resp.status_code)
    print("cookies after login:", session.cookies.get_dict())
    return resp.ok

def smoke():
    s = requests.Session()
    s.headers.update({"User-Agent": "smoke-test/1.0"})
    if not form_login(s):
        print("Login failed; aborting.")
        return 2

    r = s.post(BASE + "/action/prepare", json={"action": "issue_warning", "booking_id": BOOKING_ID})
    print("prepare", r.status_code, r.text)
    if not r.ok:
        return 3
    token = r.json().get("action_token")
    r2 = s.post(BASE + "/action/issue_warning", headers={"x-action-token": token}, json={"booking_id": BOOKING_ID})
    print("issue", r2.status_code, r2.text)
    return 0 if r2.ok else 4

if __name__ == "__main__":
    sys.exit(smoke())
