# app/utils/audit.py
from app.models import ActionAudit
from sqlalchemy.orm import Session

def log_action(
    db: Session,
    *,
    user_id: str,
    action: str,
    booking_id: str = None,
    jti: str = None,
    ip: str = None,
    success: bool = False,
    detail: str = None
):
    audit = ActionAudit(
        user_id=user_id,
        action=action,
        booking_id=booking_id,
        jti=jti,
        ip=ip,
        success=success,
        detail=detail
    )
    db.add(audit)
    db.commit()
