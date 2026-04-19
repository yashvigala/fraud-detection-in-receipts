"""HTTP client for the Drools Spring Boot policy microservice.

Responsibilities:
    1. Build the ExpenseClaim JSON payload that Drools expects from the
       FastAPI-side inputs (OCR output + upload metadata).
    2. POST it to http://localhost:8080/api/policy/evaluate.
    3. Hand back the JSON response to the caller.

All calls are async — in the submit endpoint we fire the Drools call in
parallel with the ML anomaly call via asyncio.gather(), matching the
project spec's "Steps 3+4 in parallel" note.

Graceful degradation: if the Drools service is not running, the client
returns a synthetic ``PASS`` response with a flag so the decision layer
can still produce a verdict based on ML alone.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

POLICY_ENGINE_URL = os.environ.get(
    "POLICY_ENGINE_URL", "http://localhost:8080/api/policy/evaluate"
)

# Map our UI-friendly category names to the uppercase enum values the DRL
# rules pattern-match against. Any category not in this map passes through
# unchanged (upper-cased), meaning no category-specific rules fire — which
# is fine; they'll still be evaluated by the cross-cutting rules
# (amount caps, receipt required, etc.).
CATEGORY_MAP = {
    "Food": "MEAL",
    "Client Meals": "MEAL",
    "Travel": "AIR_TRAVEL",
    "Lodging": "LODGING",
    "Entertainment": "ENTERTAINMENT",
    "Office Supplies": "OFFICE_SUPPLIES",
    "Fuel": "MILEAGE",
    "Training": "TRAINING",
}


def _normalise_category(category: str | None) -> str:
    if not category:
        return "OTHER"
    if category in CATEGORY_MAP:
        return CATEGORY_MAP[category]
    return category.upper().replace(" ", "_")


def build_claim_payload(
    ocr_json: dict | None,
    metadata: dict,
) -> dict[str, Any]:
    """Assemble the ExpenseClaim JSON body the Drools service expects.

    ``ocr_json``: the structured dict returned by Gemini OCR (may be None
    if OCR failed and the caller is relying on override fields).

    ``metadata``: form fields from /api/submit — employee_id, department,
    grade, vendor/amount/category overrides, and the context checkboxes
    (receipt_attached, pre_approval_attached, is_per_diem, etc.).
    """
    ocr_json = ocr_json or {}

    amount = (
        float(metadata.get("override_amount"))
        if metadata.get("override_amount")
        else float(ocr_json.get("amount") or 0)
    )
    vendor = metadata.get("override_vendor") or ocr_json.get("vendor") or "Unknown"
    category_raw = metadata.get("override_category") or ocr_json.get("category") or "Other"
    category = _normalise_category(category_raw)

    submitted_date = ocr_json.get("date") or metadata.get("submitted_at") or ""

    # Figure out whether the submission happened on a weekend — the DRL
    # rule R080 uses this flag.
    is_weekend = False
    ts_for_weekend = metadata.get("submitted_at") or submitted_date
    if ts_for_weekend:
        try:
            d = datetime.fromisoformat(ts_for_weekend)
            is_weekend = d.weekday() >= 5
        except (TypeError, ValueError):
            pass

    return {
        "claimId":              metadata.get("claim_id", ""),
        "employeeId":           metadata.get("employee_id", ""),
        "department":           metadata.get("department", ""),
        "expenseCategory":      category,
        "amount":               amount,
        "currency":             ocr_json.get("currency") or "INR",
        "submittedDate":        submitted_date,
        "vendor":               vendor,
        "justificationText":    metadata.get("justification_text", ""),

        # Context flags from the UI checkboxes (default to sensible values).
        "receiptAttached":      bool(metadata.get("receipt_attached", True)),
        "preApprovalAttached":  bool(metadata.get("pre_approval_attached", False)),
        "perDiem":              bool(metadata.get("is_per_diem", False)),
        "businessTrip":         bool(metadata.get("is_business_trip", False)),
        "commute":              bool(metadata.get("is_commute", False)),
        "weekendSubmission":    is_weekend,
        "teamMeal":             bool(metadata.get("is_team_meal", False)),
        "attendeeListAttached": bool(metadata.get("attendee_list_attached", False)),
        "mileageDocumented":    bool(metadata.get("mileage_documented", False)),
        "duplicateFlagged":     bool(metadata.get("is_duplicate", False)),
        "international":        bool(metadata.get("is_international", False)),

        # Transport-specific fields — defaults are fine unless the user
        # is submitting a travel-class or car-rental claim.
        "fareClass":            (metadata.get("fare_class") or "ECONOMY").upper(),
        "rentalCarClass":       (metadata.get("rental_car_class") or "ECONOMY").upper(),
        "ratePerKm":            float(metadata.get("rate_per_km") or 0),
    }


async def call_policy_engine(
    ocr_json: dict | None,
    metadata: dict,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Call the Drools service. Returns the policy JSON.

    Never raises — on any failure (service down, timeout, parse error) we
    return a synthetic 'PASS / service_unavailable' response so the
    decision layer can still run against the ML result alone.
    """
    payload = build_claim_payload(ocr_json, metadata)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(POLICY_ENGINE_URL, json=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as e:
        logger.warning("Drools service unreachable: %s", e)
        return {
            "claim_id":             payload["claimId"],
            "policy_engine_status": "APPROVED",
            "policy_decision":      "PASS",
            "hard_reject":          False,
            "violations_count":     0,
            "policy_score":         100,
            "rule_hits":            [],
            "explanations":         [],
            "service_available":    False,
            "service_error":        str(e),
        }
