# app/main.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from fastapi.templating import Jinja2Templates
from .settings import settings
from .database import ensure_db_and_tables
from app.routers import pages
from app.routers import auth
from app.routers import otp,warnings,payment_history,warnings_check,auth_pages,bookings,booking_details,realtime_jobs,worker_profile
from app.routers import wallet,wallet_pages,location,notifications,welcome,jobs,calls,worker_and_negotiation,payments_calls,booking_actions
from pathlib import Path
from dotenv import load_dotenv
from app import actions
from app import dev_auth


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

app = FastAPI(title="Trust API (FastAPI + PostgreSQL)")

# Sessions
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")

# Ensure upload dir exists
settings.UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

# Create DB (if needed) + create tables on startup
@app.on_event("startup")
def _startup():
    ensure_db_and_tables()

# Routers
app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(otp.router)
app.include_router(warnings.router)
app.include_router(payment_history.router)
app.include_router(wallet.router)
app.include_router(wallet_pages.router)
app.include_router(warnings_check.router)
app.include_router(auth_pages.router)
app.include_router(location.router)
app.include_router(notifications.router)
app.include_router(welcome.router)
app.include_router(otp.legacy_router)
app.include_router(jobs.router)
app.include_router(bookings.router)
app.include_router(calls.router)
app.include_router(worker_and_negotiation.router)
app.include_router(booking_actions.router)
app.include_router(payments_calls.router)
app.include_router(booking_details.router)
app.include_router(realtime_jobs.router)
app.include_router(worker_profile.router)

# include the actions router so /action endpoints are active
app.include_router(actions.router)

# include dev token router only in dev (remove in production)
if settings.ALLOW_DEV_TOKENS:
    app.include_router(dev_auth.router)
