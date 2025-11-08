# app/routers/auth_pages.py
from datetime import datetime, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from app.database import get_db
from app.models import User
from app.settings import settings

router = APIRouter(tags=["auth-pages"])
templates = Jinja2Templates(directory="app/templates")  # signup.html lives here

# ---------------------------
# Helpers
# ---------------------------
def _normalize_phone(phone: str) -> str:
    p = (phone or "").strip().replace(" ", "").replace("-", "")
    digits = "".join(c for c in p if c.isdigit())[-10:]
    if not digits:
        raise ValueError("invalid phone")
    return "+91" + digits

def _send_email_sync(to_email: str, otp: str):
    msg = MIMEMultipart()
    msg["From"] = settings.GMAIL_USER
    msg["To"] = to_email
    msg["Subject"] = "Your Sahayi OTP Code"
    msg.attach(MIMEText(f"Your OTP code is: {otp}", "plain"))
    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login(settings.GMAIL_USER, settings.GMAIL_PASS)
    server.send_message(msg)
    server.quit()

# ---------------------------
# /login  (GET inline HTML + POST password)
# ---------------------------
@router.get("/login", response_class=HTMLResponse)
def login_get():
    return HTMLResponse(LOGIN_HTML)

@router.post("/login")
def login_post(
    request: Request,
    db: Session = Depends(get_db),
    method: str = Form(None),
    phone: str = Form(""),
    password: str = Form(""),
):
    if method == "password":
        if not phone or not password:
            return RedirectResponse(url="/login?error=missing", status_code=303)

        try:
            phone_norm = _normalize_phone(phone)
        except Exception:
            return RedirectResponse(url="/login?error=badphone", status_code=303)

        user = (
            db.query(User)
            .filter((User.phone == phone_norm) | (User.phone == phone_norm.replace("+91", "")))
            .first()
        )
        if user and user.password and check_password_hash(user.password, password):
            # Trust API style: mark session as logged in
            request.session["user_id"] = user.id
            return RedirectResponse(url="/welcome", status_code=303)

        return RedirectResponse(url="/login?error=badcreds", status_code=303)

    # default = render
    return RedirectResponse(url="/login", status_code=303)

# ---------------------------
# Forgot Password (step 1)
# ---------------------------
@router.get("/forgot_password", response_class=HTMLResponse)
def forgot_password_get():
    return HTMLResponse(FORGOT_HTML)

@router.post("/forgot_password")
def forgot_password_post(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return RedirectResponse(url="/forgot_password?error=noaccount", status_code=303)

    otp = User.generate_otp()
    request.session["forgot_email"] = email
    request.session["forgot_otp"] = otp
    request.session["forgot_otp_expiry"] = (datetime.utcnow() + timedelta(minutes=5)).isoformat()

    _send_email_sync(email, otp)
    return RedirectResponse(url="/forgot_password_step2?ok=sent", status_code=303)

# ---------------------------
# Forgot Password (step 2)
# ---------------------------
@router.get("/forgot_password_step2", response_class=HTMLResponse)
def forgot_password_step2_get():
    return HTMLResponse(FP2_HTML)

@router.post("/forgot_password_step2")
def forgot_password_step2_post(
    request: Request,
    otp: str = Form(...),
    db: Session = Depends(get_db),
):
    email = request.session.get("forgot_email")
    otp_session = request.session.get("forgot_otp")
    expiry = request.session.get("forgot_otp_expiry")

    if not email or not otp_session or not expiry:
        return RedirectResponse(url="/forgot_password?error=session", status_code=303)

    if datetime.utcnow() > datetime.fromisoformat(expiry):
        for k in ("forgot_email", "forgot_otp", "forgot_otp_expiry"):
            request.session.pop(k, None)
        return RedirectResponse(url="/forgot_password?error=expired", status_code=303)

    if otp.strip() == otp_session:
        user = db.query(User).filter(User.email == email).first()
        if user:
            request.session["user_id"] = user.id
            for k in ("forgot_email", "forgot_otp", "forgot_otp_expiry"):
                request.session.pop(k, None)
            return RedirectResponse(url="/welcome", status_code=303)

    return RedirectResponse(url="/forgot_password_step2?error=badotp", status_code=303)

# ---------------------------
# Sign Up (GET uses your template; POST supports JSON/form)
# ---------------------------
@router.get("/sign_up", response_class=HTMLResponse)
def sign_up_get(request: Request):
    # Renders app/templates/signup.html
    return templates.TemplateResponse("signup.html", {"request": request})

@router.post("/sign_up")
async def sign_up_post(
    request: Request,
    db: Session = Depends(get_db),
):
    # Accept JSON or form
    if request.headers.get("content-type", "").startswith("application/json"):
        data = await request.json()
        phone = data.get("phone")
        name = data.get("name")
        password = data.get("password")
    else:
        form = await request.form()
        phone = form.get("phone")
        name = form.get("name")
        password = form.get("password")

    if not phone or not name or not password:
        return JSONResponse({"success": False, "message": "Phone, name and password are required."}, status_code=400)

    try:
        phone_norm = _normalize_phone(phone)
    except Exception:
        return JSONResponse({"success": False, "message": "Invalid phone format."}, status_code=400)

    # OTP verification must be done by your OTP routes beforehand
    if not request.session.get("phone_verified") or request.session.get("signup_phone") != phone_norm:
        return JSONResponse({"success": False, "message": "Phone OTP not verified or session expired."}, status_code=400)

    # duplicate check
    exists = db.query(User).filter(User.phone == phone_norm).first()
    if exists:
        return JSONResponse({"success": False, "message": "Account already exists with this phone number."}, status_code=400)

    # create user
    user = User(
        email=None,
        name=name,
        password=generate_password_hash(password),
        phone=phone_norm,
        location="Not Provided",
        contact="Not Provided",
        busy=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Login (Trust API session)
    request.session["user_id"] = user.id

    # cleanup OTP session keys
    for k in ("signup_phone", "signup_phone_otp", "signup_phone_otp_expiry", "phone_verified"):
        request.session.pop(k, None)

    return JSONResponse({"success": True, "message": "Account created successfully!"}, status_code=200)

# ---------------------------
# Inline HTML (kept as in your Flask)
# ---------------------------
LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <title>Login - Sahayi</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { height: 100vh; display: flex; justify-content: center; align-items: center;
               font-family: 'Segoe UI', sans-serif;
               background: linear-gradient(135deg, #263d61, #43cea2);}
        .login-box { background: rgba(255,255,255,0.98); padding: 28px; border-radius: 18px;
                     box-shadow: 0 10px 25px rgba(0,0,0,0.12); max-width: 480px; width: 100%; }
        .nav-tabs .nav-link.active { background: #007bff; color: white; border-radius: 10px;}
        .form-control { border-radius: 10px; padding: 12px;}
        .btn-custom { background: #007bff; color: white; border-radius: 10px;
                      padding: 12px; font-size: 16px; width: 100%; margin-top: 10px;}
        .btn-otp { background: #f39c12; color: white; border: none; border-radius: 8px;
                   padding: 8px 12px; font-size: 14px; margin-left: 8px; cursor: pointer;}
        .text-muted { margin-top: 15px; font-size: 14px; text-align: center;}
        .text-muted a { color: #007bff; text-decoration: none; }
        .otp-msg { margin-top: 6px; font-size: 14px; min-height:18px; }
        .small { font-size: 13px; }
        @media (max-width:520px){ .login-box{ padding:18px; } }
    </style>
</head>
<body>
<div class="login-box">
    <ul class="nav nav-tabs mb-3" id="loginTabs" role="tablist">
        <li class="nav-item">
            <button class="nav-link active" id="password-tab" data-bs-toggle="tab" data-bs-target="#passwordLogin" type="button" role="tab">Password</button>
        </li>
        <li class="nav-item">
            <button class="nav-link" id="otp-tab" data-bs-toggle="tab" data-bs-target="#otpLogin" type="button" role="tab">OTP</button>
        </li>
    </ul>

    <div class="tab-content">
        <!-- Password Login -->
        <div class="tab-pane fade show active" id="passwordLogin" role="tabpanel">
            <form method="POST" id="passwordForm">
                <input type="hidden" name="method" value="password">
                <div class="mb-3">
                    <label class="small">Phone number</label>
                    <div class="input-group">
                        <span class="input-group-text">+91</span>
                        <input type="tel" name="phone" id="pwPhone" class="form-control" placeholder="10-digit mobile" required>
                    </div>
                </div>
                <div class="mb-3">
                    <label class="small">Password</label>
                    <input type="password" name="password" id="pwPassword" class="form-control" placeholder="Password" required>
                </div>
                <button type="submit" class="btn-custom">Login with password</button>
            </form>
            <div class="text-muted small mt-2">Forgot password? <a href="/forgot_password">Reset</a></div>
        </div>

        <!-- OTP Login -->
        <div class="tab-pane fade" id="otpLogin" role="tabpanel">
            <form id="otpForm" onsubmit="return false;">
                <div class="mb-3">
                    <label class="small">Phone number</label>
                    <div class="input-group">
                        <span class="input-group-text">+91</span>
                        <input type="tel" id="otpPhone" class="form-control" placeholder="10-digit mobile" required>
                        <button type="button" class="btn-otp" id="sendOtpBtn">Send OTP</button>
                    </div>
                </div>

                <div class="mb-3">
                    <label class="small">OTP</label>
                    <input type="text" id="otpInput" class="form-control" placeholder="Enter OTP">
                    <div id="otpMsg" class="otp-msg"></div>
                </div>

                <div class="d-grid gap-2">
                    <button type="button" class="btn-custom" id="verifyOtpBtn">Login with OTP</button>
                </div>
            </form>
        </div>
    </div>

    <div class="text-muted small mt-3">Don’t have an account? <a href="/sign_up">Sign up</a></div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
function normalizePhoneForSend(value){
  const digits = (value||'').replace(/\\D/g,'').slice(-10);
  return '+91' + digits;
}
const sendOtpBtn = document.getElementById('sendOtpBtn');
const otpPhone = document.getElementById('otpPhone');
const otpInput = document.getElementById('otpInput');
const otpMsg = document.getElementById('otpMsg');
const verifyOtpBtn = document.getElementById('verifyOtpBtn');

sendOtpBtn.addEventListener('click', async () => {
    otpMsg.textContent = '';
    const raw = otpPhone.value.trim();
    if (!/^\\d{10}$/.test(raw)) { otpMsg.style.color='red'; otpMsg.textContent='Enter 10-digit mobile'; return; }
    const phone = normalizePhoneForSend(raw);
    sendOtpBtn.disabled = true;
    sendOtpBtn.innerText = 'Sending...';
    try {
        const res = await fetch('/send_phone_otp_login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({phone})
        });
        const data = await res.json();
        otpMsg.style.color = data.success ? 'green' : 'red';
        otpMsg.textContent = data.message || (data.success ? 'OTP sent' : 'Failed to send OTP');
    } catch (err) {
        console.error(err);
        otpMsg.style.color='red';
        otpMsg.textContent='Network error. Try again.';
    } finally {
        sendOtpBtn.disabled = false;
        sendOtpBtn.innerText = 'Send OTP';
    }
});

verifyOtpBtn.addEventListener('click', async () => {
    otpMsg.textContent = '';
    const raw = otpPhone.value.trim();
    const code = (otpInput.value || '').trim();
    if (!/^\\d{10}$/.test(raw)) { otpMsg.style.color='red'; otpMsg.textContent='Enter 10-digit mobile'; return; }
    if (!/^[0-9]{4,6}$/.test(code)) { otpMsg.style.color='red'; otpMsg.textContent='Enter valid OTP'; return; }
    const phone = normalizePhoneForSend(raw);
    verifyOtpBtn.disabled = true;
    verifyOtpBtn.innerText = 'Verifying...';
    try {
        const res = await fetch('/verify_phone_otp_login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({phone, otp: code})
        });
        const data = await res.json();
        otpMsg.style.color = data.success ? 'green' : 'red';
        otpMsg.textContent = data.message || (data.success ? 'Logged in' : 'Invalid OTP');
        if (data.success) {
            window.location.href = '/welcome';
        }
    } catch (err) {
        console.error(err);
        otpMsg.style.color='red';
        otpMsg.textContent='Network error. Try again.';
    } finally {
        verifyOtpBtn.disabled = false;
        verifyOtpBtn.innerText = 'Login with OTP';
    }
});
</script>
</body>
</html>
"""

FORGOT_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8"><title>Forgot Password</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { height:100vh; display:flex; justify-content:center; align-items:center; background: linear-gradient(135deg, #263d61, #43cea2); font-family: 'Segoe UI', sans-serif; }
        .box { background: rgba(255,255,255,0.95); padding:30px; border-radius:20px; max-width:400px; width:100%; }
        .form-control { border-radius:10px; padding:12px; margin-bottom:15px; }
        .btn-custom { background:#007bff; color:white; border-radius:10px; width:100%; padding:12px; }
    </style>
</head>
<body>
    <div class="box">
        <h3>Forgot Password</h3>
        <form method="POST">
            <input type="email" name="email" class="form-control" placeholder="Enter your registered email" required>
            <button type="submit" class="btn-custom">Send OTP</button>
        </form>
        <div style="margin-top:15px;text-align:center;">
            <a href="/login">Back to Login</a>
        </div>
    </div>
</body>
</html>
"""

FP2_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8"><title>Verify OTP</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { height:100vh; display:flex; justify-content:center; align-items:center; background: linear-gradient(135deg, #263d61, #43cea2); font-family: 'Segoe UI', sans-serif; }
        .box { background: rgba(255,255,255,0.95); padding:30px; border-radius:20px; max-width:400px; width:100%; }
        .form-control { border-radius:10px; padding:12px; margin-bottom:15px; }
        .btn-custom { background:#007bff; color:white; border-radius:10px; width:100%; padding:12px; }
    </style>
</head>
<body>
    <div class="box">
        <h3>Verify OTP</h3>
        <form method="POST">
            <input type="text" name="otp" class="form-control" placeholder="Enter OTP" required>
            <button type="submit" class="btn-custom">Verify & Login</button>
        </form>
        <div style="margin-top:15px;text-align:center;">
            <a href="/login">Back to Login</a>
        </div>
    </div>
</body>
</html>
"""
