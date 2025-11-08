# app/database.py
from __future__ import annotations
from urllib.parse import urlparse, urlunparse

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from .settings import settings

Base = declarative_base()

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def _is_postgres(url: str) -> bool:
    return url.startswith(("postgresql+psycopg://", "postgresql://"))

def _ensure_postgres_database():
    """
    Create the target Postgres database if it doesn't exist.
    Connects to the maintenance DB 'postgres' first.
    """
    parsed = urlparse(settings.DATABASE_URL)
    target_db = parsed.path.lstrip("/") or "postgres"

    admin_url = urlunparse(parsed._replace(path="/postgres"))
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")

    with admin_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": target_db},
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target_db}"'))
    admin_engine.dispose()

def ensure_db_and_tables():
    """
    Ensures DB exists (Postgres) and creates tables from models metadata.
    """
    if _is_postgres(settings.DATABASE_URL):
        _ensure_postgres_database()

    # Import models BEFORE create_all so metadata is populated
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
