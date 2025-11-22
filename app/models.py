# app/models.py
from __future__ import annotations

from datetime import datetime
import secrets
import random
import string
from math import radians, sin, cos, sqrt, atan2
from decimal import Decimal
from sqlalchemy import Integer, Float, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Float, Boolean, ForeignKey, Numeric,
    UniqueConstraint, CheckConstraint, Index, func
)
from sqlalchemy.orm import relationship, Mapped, mapped_column
from .database import Base

# ------------------ Models ------------------

class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password: Mapped[str] = mapped_column(String(255), nullable=True)
    location: Mapped[str] = mapped_column(String(100), nullable=False)
    contact: Mapped[str] = mapped_column(String(100), default="Not Provided")
    about: Mapped[str | None] = mapped_column(String(500))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    state: Mapped[str | None] = mapped_column(String(100))
    zipcode: Mapped[str | None] = mapped_column(String(20))
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)
    busy: Mapped[bool] = mapped_column(Boolean, default=False)
    email_otp: Mapped[str | None] = mapped_column(String(6))
    email_otp_expiry: Mapped[datetime | None] = mapped_column(DateTime)
    phone_otp: Mapped[str | None] = mapped_column(String(6))
    phone_otp_expiry: Mapped[datetime | None] = mapped_column(DateTime)

    # relationships
    skills: Mapped[list["Skill"]] = relationship("Skill", back_populates="user", cascade="all,delete-orphan")
    worker_profile: Mapped[WorkerProfile | None] = relationship("WorkerProfile", back_populates="user", uselist=False)
    jobs: Mapped[list["Job"]] = relationship("Job", back_populates="user", cascade="all,delete-orphan")
    wallet_transactions: Mapped[list["WalletTransaction"]] = relationship("WalletTransaction", back_populates="user")
    payout_requests: Mapped[list["PayoutRequest"]] = relationship("PayoutRequest", back_populates="user")

    # helpers
    def distance_to(self, lat: float, lon: float) -> float:
        if self.latitude is None or self.longitude is None:
            return float("inf")
        R = 6371.0  # km
        lat1, lon1 = radians(self.latitude), radians(self.longitude)
        lat2, lon2 = radians(lat), radians(lon)
        dlon, dlat = lon2 - lon1, lat2 - lat1
        a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        return R * c

    @staticmethod
    def generate_otp() -> str:
        return "".join(random.choices(string.digits, k=6))

    # In plain SQLAlchemy, avoid query access inside models.
    # Use a helper on services or pass a Session to check worker profile:
    # def is_worker(self, db: Session) -> bool: return db.query(WorkerProfile).filter_by(user_id=self.id).first() is not None


class Skill(Base):
    __tablename__ = "skill"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rate: Mapped[str] = mapped_column(String(100), nullable=False)
    rate_type: Mapped[str | None] = mapped_column(String(50))
    location: Mapped[str | None] = mapped_column(String(100))
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    category: Mapped[str | None] = mapped_column(String(50), index=True)
    user: Mapped["User"] = relationship("User", back_populates="skills")


class Job(Base):
    __tablename__ = "job"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500))
    location: Mapped[str | None] = mapped_column(String(200))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))

    user: Mapped["User"] = relationship("User", back_populates="jobs")
    bookings: Mapped[list["Booking"]] = relationship("Booking", back_populates="job")


class Rating(Base):
    __tablename__ = "rating"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    booking_id: Mapped[int] = mapped_column(ForeignKey("booking.id"), nullable=False)  # 👈 ADD
    worker_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    job_giver_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)

    stars: Mapped[float] = mapped_column(Float, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    rater: Mapped["User"] = relationship("User", foreign_keys=[job_giver_id])

    __table_args__ = (
        UniqueConstraint("booking_id", "job_giver_id", name="uq_rating_booking_giver"),  # 👈 ADD
    )

class WorkerProfile(Base):
    __tablename__ = "worker_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    worker_code: Mapped[str | None] = mapped_column(String(20), unique=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))

    # Personal details
    age: Mapped[int | None] = mapped_column(Integer)
    gender: Mapped[str | None] = mapped_column(String(10))
    qualification: Mapped[str | None] = mapped_column(String(100))
    experience: Mapped[str | None] = mapped_column(String(100))
    about: Mapped[str | None] = mapped_column(Text)

    # Location
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    state: Mapped[str | None] = mapped_column(String(100))
    zipcode: Mapped[str | None] = mapped_column(String(20))

    # Media
    photo: Mapped[str | None] = mapped_column(String(200))
    video: Mapped[str | None] = mapped_column(String(200))

    # Bank details
    bank_name: Mapped[str | None] = mapped_column(String(100))
    branch: Mapped[str | None] = mapped_column(String(100))
    ifsc: Mapped[str | None] = mapped_column(String(20))
    account_number: Mapped[str | None] = mapped_column(String(50))

    # ID Proof
    id_front: Mapped[str | None] = mapped_column(String(200))
    id_back: Mapped[str | None] = mapped_column(String(200))
    pan_card: Mapped[str | None] = mapped_column(String(200))

    # Status
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)
    is_worker: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship("User", back_populates="worker_profile")


class IdentityProof(Base):
    __tablename__ = "identity_proof"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    proof_type: Mapped[str | None] = mapped_column(String(50))     # PAN / AADHAAR
    proof_number: Mapped[str | None] = mapped_column(String(50))
    proof_file: Mapped[str | None] = mapped_column(String(200))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Booking(Base):
    __tablename__ = "booking"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, default=lambda: secrets.token_hex(16)
    )

    # foreign keys
    job_id: Mapped[int | None] = mapped_column(ForeignKey("job.id"))
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))

    status: Mapped[str] = mapped_column(String(20), default="pending")

    # core fields
    rate: Mapped[float | None] = mapped_column(Float)
    rate_type: Mapped[str | None] = mapped_column(String(20))
    quantity: Mapped[float | None] = mapped_column(Float)
    completed_quantity: Mapped[float] = mapped_column(Float, default=0)
    skill_name: Mapped[str | None] = mapped_column(String(100))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    popup_shown_to_worker: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    popup_shown_to_provider: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    otp_code: Mapped[str | None] = mapped_column(String(6))
    otp_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    otp_verified_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    job_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verify_completion_otp: Mapped[str | None] = mapped_column(String(10))
    final_otp_code: Mapped[str | None] = mapped_column(String(10))
    final_otp_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Main payment fields
    razor_order_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    razor_payment_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    razor_currency: Mapped[str | None] = mapped_column(String(10))
    razor_amount: Mapped[float | None] = mapped_column(Float)
    payment_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    payment_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    razorpay_status: Mapped[str | None] = mapped_column(String(32))

    # ---------------- Minimal Extra-time fields ----------------
    extra_timer_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extra_timer_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Minutes the giver entered (server must persist this)
    proposed_extra_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Extra payment / order (Razorpay)
    extra_razor_order_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    extra_razor_payment_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    extra_razor_amount: Mapped[float | None] = mapped_column(Float, nullable=True)

    # DB column that exists in your DB: extra_timer_payment_done
    extra_timer_payment_done: Mapped[bool] = mapped_column(
        Boolean, name="extra_timer_payment_done", default=False, nullable=False
    )

    @property
    def extra_payment_completed(self) -> bool:
        return bool(getattr(self, "extra_timer_payment_done", False))

    @extra_payment_completed.setter
    def extra_payment_completed(self, val: bool) -> None:
        self.extra_timer_payment_done = bool(val)

    # authoritative timer fields
    extra_timer_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    extra_timer_ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # fields expected by booking_details / other routes
    extra_timer_stopped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extra_timer_confirmed_stop: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extra_timer_stopped_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)

    main_timer_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    worker_arrived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # relations (reciprocal relationship for Job added here)
    job: Mapped["Job | None"] = relationship("Job", back_populates="bookings", foreign_keys=[job_id])
    provider: Mapped["User | None"] = relationship("User", foreign_keys=[provider_id])
    worker: Mapped["User | None"] = relationship("User", foreign_keys=[worker_id])




class Notification(Base):
    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    sender_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    message: Mapped[str] = mapped_column(String(255), nullable=False)
    job_id: Mapped[int | None] = mapped_column(Integer)
    action_type: Mapped[str | None] = mapped_column(String(50))  # booking_request / accepted / rejected
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("booking.id"))

    recipient: Mapped["User"] = relationship("User", foreign_keys=[recipient_id])
    sender: Mapped["User | None"] = relationship("User", foreign_keys=[sender_id])


class ShowcaseImage(Base):
    __tablename__ = "showcase_image"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    image_url: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Message(Base):
    __tablename__ = "message"   # <- singular, matches DB

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("booking.id"), nullable=False)  # <- booking (singular)
    sender_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)      # <- user (singular)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    client_nonce: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)



class SavedLocation(Base):
    __tablename__ = "saved_location"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # "Home", "Work"
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    state: Mapped[str | None] = mapped_column(String(100))
    zipcode: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WorkerWarning(Base):
    __tablename__ = "worker_warning"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("booking.id"), nullable=False)
    giver_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    worker_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    stage: Mapped[int] = mapped_column(Integer, default=1)  # 1..3
    remaining: Mapped[int] = mapped_column(Integer, default=3)
    message: Mapped[str] = mapped_column(String(255), default="Your job giver has warned you for delay in arriving.")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class CallLog(Base):
    __tablename__ = "call_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    caller_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    worker_profile_id: Mapped[int | None] = mapped_column(ForeignKey("worker_profile.id"))
    twilio_sid: Mapped[str | None] = mapped_column(String(80))
    to_number: Mapped[str | None] = mapped_column(String(40))
    from_number: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str | None] = mapped_column(String(50))   # initiated / failed / completed
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    caller: Mapped["User | None"] = relationship("User", foreign_keys=[caller_id])
    worker_profile: Mapped["WorkerProfile | None"] = relationship("WorkerProfile", foreign_keys=[worker_profile_id])


class WalletTransaction(Base):
    __tablename__ = "wallet_transaction"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    previous_hash: Mapped[str | None] = mapped_column(String(128))
    row_hmac: Mapped[str] = mapped_column(String(128), nullable=False)
    # store under DB column name "metadata" but Python attribute "meta_json"
    meta_json: Mapped[str | None] = mapped_column("metadata", String(1000))

    __table_args__ = (
        CheckConstraint("amount <> 0", name="ck_wallet_amount_nonzero"),
        # NEW: idempotency guard
        UniqueConstraint("user_id", "kind", "reference", name="uq_wallet_txn_user_kind_ref"),  # (NEW)
        Index("ix_wallet_kind_ref", "kind", "reference"),  # helpful for lookups (NEW)
    )

    user: Mapped["User"] = relationship("User", back_populates="wallet_transactions")


class PayoutRequest(Base):
    __tablename__ = "payout_request"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/approved/paid/rejected/failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)
    external_ref: Mapped[str | None] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(String(500))

    user: Mapped["User"] = relationship("User", back_populates="payout_requests")


class PriceNegotiation(Base):
    __tablename__ = "price_negotiation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True, nullable=False)
    worker_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True, nullable=False)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("job.id"))
    giver_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    worker_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open/confirmed/cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint("provider_id", "worker_id", "job_id", name="uq_price_neg_triplet"),
    )


class ActionAudit(Base):
    __tablename__ = "action_audit"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    user_id = Column(String, nullable=True)        # store as string to be flexible
    action = Column(String(64), nullable=False)
    booking_id = Column(String(64), nullable=True)
    jti = Column(String(64), nullable=True, index=True)
    ip = Column(String(45), nullable=True)
    success = Column(Boolean, nullable=False, default=False)
    detail = Column(Text, nullable=True)           # store error messages or extra metadata