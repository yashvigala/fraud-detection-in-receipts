# policy_client.py
# FastAPI calls this to hit the Drools Spring Boot microservice.
# This is also where OCR JSON fields get mapped to ExpenseClaim fields.

import httpx
from datetime import datetime

POLICY_ENGINE_URL = "http://localhost:8080/api/policy/evaluate"

def build_claim_payload(ocr_json: dict, metadata: dict) -> dict:
    """
    Maps OCR output + upload metadata into the ExpenseClaim schema
    that Spring Boot/Drools expects.
    """
    submitted_date = ocr_json.get("date", metadata.get("timestamp", ""))
    
    # Detect weekend submission
    is_weekend = False
    try:
        d = datetime.fromisoformat(submitted_date)
        is_weekend = d.weekday() >= 5  # Saturday=5, Sunday=6
    except Exception:
        pass

    return {
        "claimId":              metadata.get("claim_id"),
        "employeeId":           metadata.get("employee_id"),
        "department":           metadata.get("department"),
        "expenseCategory":      ocr_json.get("category", "").upper(),
        "amount":               float(ocr_json.get("amount", 0)),
        "currency":             ocr_json.get("currency", "INR"),
        "submittedDate":        submitted_date,
        "vendor":               ocr_json.get("vendor"),
        "justificationText":    metadata.get("justification", ""),

        # Flags from metadata (employee sets these at submission time)
        "receiptAttached":      True,  # Always true if OCR succeeded
        "preApprovalAttached":  metadata.get("pre_approval_attached", False),
        "perDiem":              metadata.get("is_per_diem", False),
        "businessTrip":         metadata.get("is_business_trip", False),
        "commute":              metadata.get("is_commute", False),
        "weekendSubmission":    is_weekend,
        "teamMeal":             metadata.get("is_team_meal", False),
        "attendeeListAttached": metadata.get("attendee_list_attached", False),
        "mileageDocumented":    metadata.get("mileage_documented", False),
        "duplicateFlagged":     metadata.get("is_duplicate", False),
        "international":        metadata.get("is_international", False),

        # Transport-specific (from OCR or metadata)
        "fareClass":            metadata.get("fare_class", "ECONOMY").upper(),
        "rentalCarClass":       metadata.get("rental_car_class", "ECONOMY").upper(),
        "ratePerKm":            float(metadata.get("rate_per_km", 0)),
    }


async def call_policy_engine(ocr_json: dict, metadata: dict) -> dict:
    """
    Async call to Drools. Returns the policy JSON the decision layer expects.
    Called via asyncio.gather() in parallel with anomaly detection.
    """
    payload = build_claim_payload(ocr_json, metadata)
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(POLICY_ENGINE_URL, json=payload)
        resp.raise_for_status()
        return resp.json()

# ----------------------------------------------------------------
# EXAMPLE — what the response JSON looks like for a hotel > ₹6000
# ----------------------------------------------------------------
EXAMPLE_POLICY_RESPONSE = {
    "claim_id": "CLM_10291",
    "policy_engine_status": "REJECTED",      # → decision layer reads this
    "policy_decision": "HARD_FAIL",          # → decision layer reads this
    "hard_reject": True,                     # → if True, decision layer → REJECTED regardless of ML
    "violations_count": 2,
    "policy_score": 0,                       # 100 - 100 (R010) = 0
    "rule_hits": [
        {
            "rule_id": "R010",
            "severity": "HARD",
            "deduction": 100,
            "reason": "Hotel amount ₹7500 exceeds ₹6000/night cap"
        },
        {
            "rule_id": "R011",
            "severity": "SOFT",
            "deduction": 30,
            "reason": "Lodging requires pre-approval — flagged for manager review"
        }
    ],
    "explanations": [
        "Hotel amount ₹7500 exceeds ₹6000/night cap",
        "Lodging requires pre-approval — flagged for manager review"
    ]
}
