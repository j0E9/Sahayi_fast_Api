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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Float, Boolean, ForeignKey, Numeric,
    UniqueConstraint, CheckConstraint, Index, func,Date
)
from sqlalchemy.orm import relationship, Mapped, mapped_column
from .database import Base
from sqlalchemy import Enum
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

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True
    )

    is_platform: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True
    )

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

class WorkerAvailability(Base):
    __tablename__ = "worker_availability"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    worker_id: Mapped[int] = mapped_column(
        ForeignKey("user.id"),
        index=True,
        nullable=False
    )

    date: Mapped[datetime.date] = mapped_column(
        Date,
        nullable=False,
        index=True
    )

    start_time: Mapped[str] = mapped_column(String(5), nullable=False)  # "09:00"
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)    # "22:00"

    is_available: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )

    worker: Mapped["User"] = relationship("User")

from sqlalchemy import Time

class FutureBooking(Base):
    __tablename__ = "future_booking"

    id = Column(Integer, primary_key=True)
    worker_id = Column(ForeignKey("user.id"), nullable=False)
    provider_id = Column(ForeignKey("user.id"), nullable=False)

    date = Column(Date, nullable=False)

    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    status = Column(String(20), default="reserved", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

from sqlalchemy import Time

class WorkerBooking(Base):
    __tablename__ = "worker_bookings"

    id = Column(Integer, primary_key=True)
    worker_id = Column(Integer, index=True, nullable=False)
    giver_id = Column(Integer, index=True, nullable=False)

    date = Column(Date, index=True, nullable=False)

    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    status = Column(String(20), default="booked", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class BookingReport(Base):
    __tablename__ = "booking_report"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    booking_id: Mapped[int] = mapped_column(
        ForeignKey("booking.id"),
        nullable=False,
        index=True
    )

    reporter_id: Mapped[int] = mapped_column(
        ForeignKey("user.id"),
        nullable=False,
        index=True
    )

    reported_user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id"),
        nullable=False,
        index=True
    )

    severity_weight = Column(Integer, nullable=False)
    reporter_weight = Column(Float, nullable=False)
    final_weight = Column(Float, nullable=False)

    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    proof_url: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "booking_id",
            "reporter_id",
            name="uq_booking_report_once"
        ),
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

    #Reports
    # 🔥 Moderation (weighted, time-bounded)
    risk_score_30d = Column(Float, default=0.0, nullable=False)
    last_moderation_update = Column(DateTime)

    # 🔴 STRIKE MEMORY
    strike_count = Column(Integer, default=0, nullable=False)

    moderation_status = Column(
        Enum("normal", "limited", "suspended", "banned", name="moderation_status"),
        default="normal",
        nullable=False,
        index=True
    )

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

    # ---------------- WFH Escrow & Timing ----------------
    started_at = Column(DateTime, nullable=True)
    start_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    escrow_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True
    )

    escrow_locked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    escrow_released: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    # foreign keys
    job_id: Mapped[int | None] = mapped_column(ForeignKey("job.id"))
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))

    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)


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


    # ---------------- WFH specific ----------------
    expected_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    description = Column(Text, nullable=False)  # ✅ ADD THIS

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

    # ---------------- WFH update / revision ----------------
    update_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
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

    drive_eta_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    drive_timer_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    warn_stage = Column(Integer, default=0, nullable=False)  # 0..3
    warn_last_at = Column(DateTime, nullable=True)
    auto_cancelled = Column(Boolean, default=False, nullable=False)
    review_deadline = Column(DateTime, nullable=True)
    completion_requested_once: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True
    )

    booking_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="onsite",  # onsite | wfh
        index=True
    )

    # ---------------- PRICE STATE ----------------
    price_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="fixed",  # fixed | pending | proposed | confirmed
        index=True
    )

    cancel_window_closed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,  # Python-side default
        server_default="false",  # DB-side default
        nullable=False,
        index=True
    )

    # ---------------- LOCATION SHARING (WFH) ----------------

    location_request_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        default=None,
        index=True
    )
    # None | requested | approved | rejected

    location_shared_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True
    )

    location_rejected_reason: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )

    giver_commission_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        default=Decimal("0.00")
    )

    @property
    def revision_count(self) -> int:
        """
        Number of times job giver requested rework.
        Derived from project update history.
        """
        return sum(
            1 for u in (self.project_updates or [])
            if u.status == "revision_requested"
        )

    dispute = relationship(
        "WFHDispute",
        back_populates="booking",
        uselist=False,
        cascade="all, delete-orphan"
    )

    # relations (reciprocal relationship for Job added here)
    job: Mapped["Job | None"] = relationship("Job", back_populates="bookings", foreign_keys=[job_id])
    provider: Mapped["User | None"] = relationship("User", foreign_keys=[provider_id])
    worker: Mapped["User | None"] = relationship("User", foreign_keys=[worker_id])


    # ---------------- WFH PROJECT UPDATES ----------------
    project_updates: Mapped[list["WFHProjectUpdate"]] = relationship(
        "WFHProjectUpdate",
        back_populates="booking",
        cascade="all, delete-orphan",
        order_by="WFHProjectUpdate.created_at.desc()"
    )

class WFHDispute(Base):
    __tablename__ = "wfh_dispute"

    id = Column(Integer, primary_key=True)
    booking_id = Column(ForeignKey("booking.id"), nullable=False)
    raised_by = Column(ForeignKey("user.id"), nullable=False)

    reason = Column(Text, nullable=False)
    status = Column(String(20), default="open")  # open / resolved

    created_at = Column(DateTime, default=datetime.utcnow)

    booking = relationship(
        "Booking",
        back_populates="dispute"
    )



class WFHProjectUpdate(Base):
    __tablename__ = "wfh_project_update"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("booking.id"), nullable=False, index=True)

    requested_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    submitted_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)

    update_type: Mapped[str] = mapped_column(String(30), default="progress")
    message: Mapped[str] = mapped_column(Text, nullable=True)
    provider_comment: Mapped[str | None] = mapped_column(Text)

    preview_url: Mapped[str | None] = mapped_column(String(255))
    file_url: Mapped[str | None] = mapped_column(String(255))

    status: Mapped[str] = mapped_column(String(20), default="requested", index=True)

    # 🔴 NEW — REQUIRED
    request_origin: Mapped[str] = mapped_column(
        Enum("system", "job_giver", name="wfh_update_origin"),
        nullable=False,
        default="job_giver",
        index=True
    )

    request_deadline: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)

    booking: Mapped["Booking"] = relationship(
        "Booking",
        back_populates="project_updates"
    )


class WFHDeliverable(Base):
    __tablename__ = "wfh_deliverable"

    id = Column(Integer, primary_key=True)
    booking_id = Column(ForeignKey("booking.id"), nullable=False)
    version = Column(Integer, default=1)  # v1, v2, v3
    submitted_by = Column(ForeignKey("user.id"), nullable=False)

    type = Column(String(20), nullable=False)
    # website | mobile_app | design | content | food | craft | other

    message = Column(Text, nullable=True)  # explanation / notes

    file_url = Column(String(255), nullable=True)
    preview_url = Column(String(255), nullable=True)

    status = Column(String(20), default="submitted")
    # submitted | revision_requested | approved

    created_at = Column(DateTime, default=datetime.utcnow)


class WFHDisputeResponse(Base):
    __tablename__ = "wfh_dispute_response"

    id = Column(Integer, primary_key=True)
    dispute_id = Column(ForeignKey("wfh_dispute.id"), nullable=False)
    user_id = Column(ForeignKey("user.id"), nullable=False)

    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    files = relationship(
        "WFHDisputeFile",
        primaryjoin="WFHDisputeResponse.id==WFHDisputeFile.response_id",
        cascade="all, delete-orphan"
    )


class WFHDisputeFile(Base):
    __tablename__ = "wfh_dispute_file"

    id = Column(Integer, primary_key=True)

    dispute_id = Column(
        ForeignKey("wfh_dispute.id"),
        nullable=False,
        index=True
    )

    response_id = Column(
        ForeignKey("wfh_dispute_response.id"),
        nullable=True
    )

    file_url = Column(String(255), nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

class PlatformProfit(Base):
    __tablename__ = "platform_profit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ---- Core reference ----
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("booking.id"), nullable=True
    )

    # ---- Financial classification ----
    type: Mapped[str] = mapped_column(
        String(32), nullable=False
        # values:
        # 'commission'
        # 'refund'
        # 'withdrawal'
        # 'escrow_hold'
        # 'escrow_release'
    )

    direction: Mapped[str] = mapped_column(
        String(8), nullable=False
        # 'credit' | 'debit'
    )

    # ---- Amounts ----
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    giver_commission: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    worker_commission: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    # ---- Escrow / Hold tracking ----
    on_hold: Mapped[bool] = mapped_column(default=False)
    hold_for_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id")
    )
    release_at: Mapped[datetime | None] = mapped_column(DateTime)

    # ---- Audit ----
    reference: Mapped[str] = mapped_column(String(64), unique=True)
    meta: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

class PlatformBalance(Base):
    __tablename__ = "platform_balance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    total_company_profit: Mapped[Decimal] = mapped_column(Numeric(14,2), default=0)
    total_worker_distributed: Mapped[Decimal] = mapped_column(Numeric(14,2), default=0)
    total_refunded: Mapped[Decimal] = mapped_column(Numeric(14,2), default=0)
    total_withdrawn: Mapped[Decimal] = mapped_column(Numeric(14,2), default=0)

    available_profit: Mapped[Decimal] = mapped_column(Numeric(14,2), default=0)
    bank_balance: Mapped[Decimal] = mapped_column(Numeric(14,2), default=0)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

class JobDistanceCache(Base):
    __tablename__ = "job_distance_cache"

    id = Column(Integer, primary_key=True)

    job_id = Column(
        Integer,
        ForeignKey("job.id", ondelete="CASCADE"),
        nullable=False
    )

    skill_id = Column(
        Integer,
        ForeignKey("skill.id", ondelete="CASCADE"),
        nullable=False
    )


    # cached worker location snapshot
    worker_lat = Column(Float, nullable=False)
    worker_lon = Column(Float, nullable=False)

    # cached user location snapshot
    user_lat = Column(Float, nullable=False)
    user_lon = Column(Float, nullable=False)

    # mapbox results
    distance_km = Column(Float, nullable=False)
    duration_min = Column(Float, nullable=True)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("job_id", "skill_id", name="uq_job_skill_distance"),
    )


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