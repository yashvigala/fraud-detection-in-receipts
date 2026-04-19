"""Persist /api/submit output (claim + verdict + audit) to the DB.

This is the bridge between the async submit pipeline and the relational
schema. Called once per successful submission.
"""
from __future__ import annotations

import json
from datetime import datetime

from .db import SessionLocal
from .models_db import AuditLog, Claim, Verdict


def persist_submission(
    *,
    claim_id: str,
    employee_id: str,
    company_id: str,
    claim_dict: dict,
    ocr_dict: dict | None,
    engineered_features: dict,
    policy_result: dict,
    anomaly_result: dict,
    decision: dict,
    reasons: list,
    original_png_base64: str,
    preprocessed_png_base64: str,
    metadata_flags: dict,
    attachments: dict | None = None,
) -> None:
    """Write one Claim + one Verdict + one AuditLog row."""
    with SessionLocal() as db:
        claim = Claim(
            id=claim_id,
            employee_id=employee_id,
            company_id=company_id,
            vendor=claim_dict.get("vendor"),
            category=claim_dict.get("category", "Other"),
            amount=float(claim_dict.get("amount", 0)),
            currency=(ocr_dict or {}).get("currency") or "INR",
            justification=metadata_flags.get("justification_text", ""),
            original_png_base64=original_png_base64,
            preprocessed_png_base64=preprocessed_png_base64,
            ocr_json=json.dumps(ocr_dict or {}, default=str),
            engineered_features_json=json.dumps(engineered_features, default=str),
            receipt_attached=bool(metadata_flags.get("receipt_attached", True)),
            pre_approval_attached=bool(metadata_flags.get("pre_approval_attached", False)),
            is_per_diem=bool(metadata_flags.get("is_per_diem", False)),
            is_business_trip=bool(metadata_flags.get("is_business_trip", False)),
            is_team_meal=bool(metadata_flags.get("is_team_meal", False)),
            attendee_list_attached=bool(metadata_flags.get("attendee_list_attached", False)),
            attachments_json=json.dumps(attachments or {}, default=str),
            submitted_at=datetime.now(),
        )
        db.add(claim)

        verdict = Verdict(
            claim_id=claim_id,
            final_status=decision.get("final_status", "VALID"),
            action=decision.get("action", "AUTO_APPROVE"),
            final_score=float(decision.get("final_score", 0.0)),
            policy_status=policy_result.get("policy_engine_status", "APPROVED"),
            policy_score=int(policy_result.get("policy_score", 100)),
            policy_json=json.dumps(policy_result, default=str),
            ml_label=anomaly_result.get("anomaly_label", "NORMAL"),
            ml_combined_score=float(anomaly_result.get("combined_anomaly_score", 0.0)),
            ml_if_score=float(anomaly_result.get("isolation_forest", {}).get("anomaly_score", 0.0)),
            ml_ae_score=float(anomaly_result.get("autoencoder", {}).get("anomaly_score", 0.0)),
            ml_reconstruction_error=float(anomaly_result.get("autoencoder", {}).get("reconstruction_error", 0.0)),
            anomaly_json=json.dumps(anomaly_result, default=str),
            reasons_json=json.dumps(reasons, default=str),
            decision_reasons_json=json.dumps(decision.get("decision_reason", []), default=str),
        )
        db.add(verdict)

        db.add(AuditLog(
            claim_id=claim_id,
            actor=f"employee:{employee_id}",
            event="SUBMITTED",
            detail=f"status={verdict.final_status}; action={verdict.action}; policy={verdict.policy_status}",
        ))
        db.commit()


def review_claim(
    *,
    claim_id: str,
    reviewer_email: str,
    action: str,  # "APPROVE" or "REJECT"
    comment: str = "",
) -> Verdict:
    """Manager approves or rejects a claim. Updates the latest verdict
    + appends an audit log entry."""
    with SessionLocal() as db:
        verdict = (
            db.query(Verdict)
            .filter(Verdict.claim_id == claim_id)
            .order_by(Verdict.created_at.desc())
            .first()
        )
        if verdict is None:
            raise ValueError(f"No verdict found for claim {claim_id}")

        now = datetime.now()
        if action.upper() == "APPROVE":
            verdict.final_status = "VALID"
            verdict.action = "MANUALLY_APPROVED"
        elif action.upper() == "REJECT":
            verdict.final_status = "REJECTED"
            verdict.action = "MANUALLY_REJECTED"
        else:
            raise ValueError(f"Unknown action: {action}")
        verdict.reviewer_comment = comment
        verdict.reviewed_at = now

        db.add(AuditLog(
            claim_id=claim_id,
            actor=reviewer_email,
            event=action.upper() + "D",
            detail=comment or f"manager {action.lower()} with no comment",
            created_at=now,
        ))
        db.commit()
        db.refresh(verdict)
        return verdict
