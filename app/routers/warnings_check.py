# app/routers/warnings_check.py
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.models import Booking, WorkerWarning, User

router = APIRouter(tags=["warnings"])

def get_current_user_from_session(request: Request, db: Session) -> Optional[User]:
    uid = request.session.get("user_id")
    return db.get(User, uid) if uid else None

# @router.get("/worker_check_warning")
# def worker_check_warning(
#     request: Request,
#     booking_id: int = Query(...),
#     db: Session = Depends(get_db),
# ):
#     me = get_current_user_from_session(request, db)
#     if not me:
#         return {"warning": None}
#
#     booking = db.get(Booking, booking_id)
#     if not booking or me.id != booking.worker_id:
#         return {"warning": None}
#
#     warning = (
#         db.query(WorkerWarning)
#         .filter(
#             WorkerWarning.booking_id == booking.id,
#             WorkerWarning.worker_id == me.id,
#             WorkerWarning.acknowledged == False,  # noqa: E712
#         )
#         .order_by(WorkerWarning.created_at.desc())
#         .first()
#     )
#
#     if warning:
#         return {
#             "warning": {
#                 "id": warning.id,
#                 "message": warning.message,
#               # if `message` doesn't exist in your model, drop the line above
#                 "remaining": warning.remaining,
#             }
#         }
#     return {"warning": None}

# @router.post("/ack_warning")
# def ack_warning(
#     request: Request,
#     payload: dict,
#     db: Session = Depends(get_db),
# ):
#     me = get_current_user_from_session(request, db)
#     if not me:
#         raise HTTPException(status_code=403, detail="Unauthorized")
#
#     warning_id = payload.get("warning_id")
#     warning = db.get(WorkerWarning, warning_id)
#     if not warning or me.id != warning.worker_id:
#         raise HTTPException(status_code=403, detail="Unauthorized")
#
#     warning.acknowledged = True
#     db.add(warning)
#     db.commit()
#     return {"success": True}
