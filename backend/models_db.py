"""SQLAlchemy ORM models.

Entities:
    Company       — tenant that has its own policy rules
    Employee      — individual who submits claims, belongs to a company
    User          — login account (not used yet; auth is faked, but table
                    reserved so a real JWT flow can drop in without changes)
    Claim         — one receipt submission. Every claim points at an
                    employee and a company; stores OCR output + metadata.
    Verdict       — the ML + Drools + decision-layer output for a claim.
                    Separate table because re-evaluations should leave an
                    audit trail rather than overwriting.
    AuditLog      — append-only log of every state change (submitted ->
                    flagged -> approved, or manager comments, etc.)

Design notes:
    * All primary keys are plain integer IDs for simplicity.
    * Foreign keys are indexed so listing claims-by-employee is O(log n).
    * JSON payloads are stored as TEXT (portable across SQLite + Postgres).
      Postgres users can swap TEXT for JSONB later for indexed queries.
    * Timestamps are timezone-naive local time. Swap to TIMESTAMPTZ when
      moving to Postgres if multi-region matters.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    # Rules stored as JSON text — same schema as data/companies/*.json.
    rules_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    employees: Mapped[list["Employee"]] = relationship(back_populates="company")
    claims: Mapped[list["Claim"]] = relationship(back_populates="company")


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # e.g. EMP00001
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    department: Mapped[str] = mapped_column(String(80), nullable=False)
    grade: Mapped[str] = mapped_column(String(40), nullable=False)
    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id"), nullable=False, index=True
    )

    company: Mapped[Company] = relationship(back_populates="employees")
    claims: Mapped[list["Claim"]] = relationship(back_populates="employee")


class User(Base):
    """Login account. Unused today (auth is faked), reserved for real JWT."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(20), default="employee")  # employee|manager|admin
    employee_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("employees.id"), nullable=True
    )
    company_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("companies.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )


class Claim(Base):
    """One receipt submission. Every /api/submit writes exactly one row."""
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # CLM_XXXXXXXXXX
    employee_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("employees.id"), nullable=False, index=True
    )
    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id"), nullable=False, index=True
    )

    # OCR + override fields — what the claim actually is.
    vendor: Mapped[Optional[str]] = mapped_column(String(400))
    category: Mapped[str] = mapped_column(String(80), index=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    justification: Mapped[str] = mapped_column(Text, default="")

    # Raw images, base64-encoded PNG. Storing base64 is wasteful at scale;
    # in production we'd write these to S3 and keep only the URL.
    original_png_base64: Mapped[str] = mapped_column(Text, default="")
    preprocessed_png_base64: Mapped[str] = mapped_column(Text, default="")

    # Raw OCR JSON and engineered feature row — kept verbatim so claim
    # detail page can reconstruct the entire pipeline state.
    ocr_json: Mapped[str] = mapped_column(Text, default="{}")
    engineered_features_json: Mapped[str] = mapped_column(Text, default="{}")

    # Drools / ML metadata captured at submit time.
    receipt_attached: Mapped[bool] = mapped_column(Boolean, default=True)
    pre_approval_attached: Mapped[bool] = mapped_column(Boolean, default=False)
    is_per_diem: Mapped[bool] = mapped_column(Boolean, default=False)
    is_business_trip: Mapped[bool] = mapped_column(Boolean, default=False)
    is_team_meal: Mapped[bool] = mapped_column(Boolean, default=False)
    attendee_list_attached: Mapped[bool] = mapped_column(Boolean, default=False)

    # Supplementary attachments uploaded with the claim.
    # JSON blob shape: {"pre_approval": {"name": "...", "mime": "...", "data_b64": "..."},
    #                   "attendee_list": {...}}. Empty dict when nothing attached.
    attachments_json: Mapped[str] = mapped_column(Text, default="{}")

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )

    employee: Mapped[Employee] = relationship(back_populates="claims")
    company: Mapped[Company] = relationship(back_populates="claims")
    verdicts: Mapped[list["Verdict"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan"
    )
    audits: Mapped[list["AuditLog"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan"
    )


class Verdict(Base):
    """Verdict history for a claim. Multiple rows per claim if re-evaluated."""
    __tablename__ = "verdicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    # Final classification
    final_status: Mapped[str] = mapped_column(String(20), index=True)  # VALID | SUSPICIOUS | REJECTED | FRAUDULENT
    action: Mapped[str] = mapped_column(String(30))  # AUTO_APPROVE | ... | AUTO_REJECT
    final_score: Mapped[float] = mapped_column(Float, default=0.0)

    # Component signals
    policy_status: Mapped[str] = mapped_column(String(20))  # APPROVED | FLAGGED | REJECTED
    policy_score: Mapped[int] = mapped_column(Integer, default=100)
    policy_json: Mapped[str] = mapped_column(Text, default="{}")      # full Drools payload

    ml_label: Mapped[str] = mapped_column(String(20))                 # NORMAL / SUSPICIOUS / ANOMALOUS
    ml_combined_score: Mapped[float] = mapped_column(Float, default=0.0)
    ml_if_score: Mapped[float] = mapped_column(Float, default=0.0)
    ml_ae_score: Mapped[float] = mapped_column(Float, default=0.0)
    ml_reconstruction_error: Mapped[float] = mapped_column(Float, default=0.0)
    anomaly_json: Mapped[str] = mapped_column(Text, default="{}")     # full ML payload

    reasons_json: Mapped[str] = mapped_column(Text, default="[]")     # generate_reasons() output
    decision_reasons_json: Mapped[str] = mapped_column(Text, default="[]")

    # Manager approval layer — stays NULL until a manager reviews.
    reviewer_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reviewer_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    claim: Mapped[Claim] = relationship(back_populates="verdicts")


class AuditLog(Base):
    """Append-only event log. Every state change records one row."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor: Mapped[str] = mapped_column(String(100), default="system")  # email or system
    event: Mapped[str] = mapped_column(String(80))  # SUBMITTED | FLAGGED | APPROVED | REJECTED | COMMENT
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )

    claim: Mapped[Claim] = relationship(back_populates="audits")
