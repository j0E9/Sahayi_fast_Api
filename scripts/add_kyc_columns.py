"""Add missing KYC columns to the existing `user` table if they don't exist.

Run with the repo venv python:
  & '.\.venv\Scripts\python.exe' scripts\add_kyc_columns.py
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.database import engine
from sqlalchemy import text

stmts = [
    'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS kyc_status VARCHAR(20);',
    'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS aadhaar_file_path VARCHAR(255);',
    'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS live_photo_path VARCHAR(255);',
    'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS verified_name VARCHAR(200);',
  'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS is_worker BOOLEAN DEFAULT FALSE;',
]

with engine.begin() as conn:
    for s in stmts:
        print('Executing:', s)
        conn.execute(text(s))

print('Done: attempted to add KYC columns (no-op if already present).')
