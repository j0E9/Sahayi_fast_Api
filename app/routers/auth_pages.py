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
from app.models import WorkerProfile
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

def _check_login_allowed(db: Session, user: User):
    profile = user.worker_profile
    if not profile:
        return

    if profile.moderation_status == "suspended":
        raise ValueError("account_suspended")

    if profile.moderation_status == "banned":
        raise ValueError("account_banned")



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
def login_get(request: Request):
    error = request.query_params.get("error")

    error_block = ""

    if error == "account_suspended":
        error_block = """
        <div style="margin-bottom:16px; padding:14px; border-radius:10px;
                    background:#fff3cd; color:#856404; font-size:14px; text-align:center;">
            <strong>⚠️ Account Suspended</strong><br>
            Your account is temporarily suspended.<br><br>
            <a href="/support" style="
                display:inline-block;
                padding:8px 14px;
                background:#856404;
                color:white;
                border-radius:6px;
                text-decoration:none;
                font-size:13px;">
                Contact Support
            </a>
        </div>
        """

    elif error == "account_banned":
        error_block = """
        <div style="margin-bottom:16px; padding:14px; border-radius:10px;
                    background:#f8d7da; color:#721c24; font-size:14px; text-align:center;">
            <strong>⛔ Account Banned</strong><br>
            Your account has been permanently banned.<br><br>
                <a href="/support" style="
                display:inline-block;
                padding:8px 14px;
                background:#721c24;
                color:white;
                border-radius:6px;
                text-decoration:none;
                font-size:13px;">
                Contact Support
            </a>
        </div>
        """


    return HTMLResponse(
        LOGIN_HTML.replace("{{ERROR_BLOCK}}", error_block)
    )



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
            try:
                _check_login_allowed(db, user)
            except ValueError as e:
                return RedirectResponse(
                    url=f"/login?error={str(e)}",
                    status_code=303
                )

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
            try:
                _check_login_allowed(db, user)
            except ValueError as e:
                return RedirectResponse(
                    url=f"/login?error={str(e)}",
                    status_code=303
                )

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
LOGIN_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <title>Login · Sahayi</title>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">

  <!-- Bootstrap -->
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">

  <!-- Lottie -->
  <script src="https://unpkg.com/lottie-web@5.12.2/build/player/lottie.min.js"></script>

  <style>
    body {
      min-height: 100vh;
      display: flex;
      justify-content: center;
      align-items: center;
      background: radial-gradient(circle at top, #0b1a3a, #020617);
      font-family: Inter, system-ui, sans-serif;
    }

    .wrapper {
      width: 100%;
      max-width: 420px;
      padding: 16px;
    }

    /* ---------- ALERT ---------- */
    .alert-box {
      background: #fef3c7;
      border-radius: 12px;
      padding: 14px;
      font-size: 14px;
      margin-bottom: 16px;
      text-align: center;
    }

    /* ---------- BRAND ---------- */
    .brand {
      text-align: center;
      margin-bottom: 22px;
    }

    .brand h1 {
      color: #ffffff;
      font-weight: 600;
      margin-bottom: 4px;
    }

    .brand p {
      color: #94a3b8;
      font-size: 14px;
      margin: 0;
    }

    /* ---------- WORKER WALK TRACK ---------- */
    .worker-track {
      position: relative;
      width: 100%;
      height: 110px;
      overflow: hidden;
      margin: 6px 0 2px;
    }

    #workerRun {
      position: absolute;
      left: -160px;
      width: 200px;
      height: 110px;
      animation: walkAcross 12s linear infinite;
    }

    @keyframes walkAcross {
      0% {
        transform: translateX(-160px);
      }
      100% {
        transform: translateX(520px);
      }
    }

    /* ---------- CARD ---------- */
    .card {
      border-radius: 16px;
      padding: 26px;
      border: none;
      box-shadow: 0 20px 40px rgba(0,0,0,.35);
    }

    .tabs {
      display: flex;
      gap: 6px;
      margin-bottom: 20px;
    }

    .tabs button {
      flex: 1;
      padding: 10px;
      border-radius: 10px;
      border: none;
      background: #f1f5f9;
      font-size: 14px;
      font-weight: 500;
      color: #334155;
    }

    .tabs button.active {
      background: #0f172a;
      color: #fff;
    }

    label {
      font-size: 13px;
      color: #475569;
      margin-bottom: 6px;
    }

    .form-control {
      padding: 12px;
      border-radius: 10px;
      font-size: 14px;
    }

    .form-control:focus {
      border-color: #2563eb;
      box-shadow: 0 0 0 2px rgba(37,99,235,.15);
    }

    .btn-primary {
      margin-top: 10px;
      padding: 12px;
      border-radius: 10px;
      font-weight: 500;
      background: #0f172a;
      border: none;
    }

    .meta {
      text-align: center;
      font-size: 13px;
      margin-top: 14px;
    }

    .meta a {
      color: #38bdf8;
      text-decoration: none;
      font-weight: 500;
    }
  </style>
</head>

<body>

<div class="wrapper">

  {{ERROR_BLOCK}}

  <!-- BRAND -->
  <div class="brand">
    <h1>Sahayi</h1>

    <!-- WALKING WORKER -->
    <div class="worker-track">
      <div id="workerRun"></div>
    </div>

    <p>Trusted local services</p>
  </div>

  <!-- LOGIN CARD -->
  <div class="card">

    <div class="tabs">
      <button id="pwTab" class="active" onclick="showTab('pw')">Password</button>
      <button id="otpTab" onclick="showTab('otp')">OTP</button>
    </div>

    <!-- PASSWORD -->
    <form method="POST" id="pw">
      <input type="hidden" name="method" value="password">

      <div class="mb-3">
        <label>Phone number</label>
        <div class="input-group">
          <span class="input-group-text">+91</span>
          <input name="phone" class="form-control" placeholder="10-digit mobile" required>
        </div>
      </div>

      <div class="mb-3">
        <label>Password</label>
        <input type="password" name="password" class="form-control" required>
      </div>

      <button class="btn btn-primary w-100">Login</button>

      <div class="meta">
        <a href="/forgot_password">Forgot password?</a>
      </div>
    </form>

    <!-- OTP -->
    <div id="otp" style="display:none;">
      <div class="mb-3">
        <label>Phone number</label>
        <input id="otpPhone" class="form-control" placeholder="10-digit mobile">
      </div>

      <div class="mb-3">
        <label>OTP</label>
        <input id="otpInput" class="form-control">
      </div>

      <button class="btn btn-primary w-100" id="verifyOtpBtn">
        Verify & Login
      </button>
    </div>

  </div>

  <div class="meta">
    New here? <a href="/sign_up">Create account</a>
  </div>

</div>

<script>
function showTab(tab){
  pw.style.display = tab==='pw' ? 'block' : 'none';
  otp.style.display = tab==='otp' ? 'block' : 'none';
  pwTab.classList.toggle('active', tab==='pw');
  otpTab.classList.toggle('active', tab==='otp');
}

/* Load worker animation (ONCE) */
const workerAnim = lottie.loadAnimation({
  container: document.getElementById('workerRun'),
  renderer: 'svg',
  loop: true,
  autoplay: true,
  path: '/static/lottie/walking-office-man.json'
});

/* Natural walking pace */
workerAnim.setSpeed(0.85);

/* Pause animation while typing (premium UX) */
document.querySelectorAll('input').forEach(input => {
  input.addEventListener('focus', () => workerAnim.pause());
  input.addEventListener('blur', () => workerAnim.play());
});
</script>

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
