"""Decision layer — combines Drools policy result + ML anomaly result into
one final verdict.

Adapted from `files/decision_layer.py` with minor shape changes so it
matches our ML output. The core logic (rule-override principle,
weighted final score, reason-code aggregation) is preserved verbatim.

Contract:
    make_decision(policy, anomaly) ->
        {claim_id, final_status, final_score, action,
         decision_reason[], policy_summary{}, anomaly_summary{}}

    * final_status  : VALID | SUSPICIOUS | REJECTED | FRAUDULENT
    * action        : AUTO_APPROVE | MANAGER_REVIEW | MANUAL_REVIEW | AUTO_REJECT
    * final_score   : 0.0 (clean) → 1.0 (definite fraud)

Design principle: **Hard policy violations always win**. If Drools flagged
any HARD rule, the final_status is REJECTED regardless of how the ML
models scored the claim. ML never downgrades a policy-hard rejection.
This is the spec's "rule override logic".
"""
from __future__ import annotations


def make_decision(policy: dict, anomaly: dict) -> dict:
    # --- Extract policy signals ---
    hard_reject      = policy.get("hard_reject", False)
    policy_decision  = policy.get("policy_decision", "PASS")
    violations_count = policy.get("violations_count", 0)
    policy_score     = policy.get("policy_score", 100)
    explanations     = policy.get("explanations", [])

    # --- Extract ML signals ---
    anomaly_label    = anomaly.get("anomaly_label", "NORMAL")
    combined_score   = float(anomaly.get("combined_anomaly_score", 0.0))
    confidence       = float(anomaly.get("confidence", 0.0))
    top_features     = anomaly.get("top_features", [])

    # --- Decide the final status by combining both ---
    reason_codes: list[str] = []

    if hard_reject:
        # Rule 1 — hard reject always wins.
        final_status = "REJECTED"
        action       = "AUTO_REJECT"
        reason_codes = [f"POLICY_HARD_FAIL:{r}" for r in explanations]
        final_score  = 0.0

    elif anomaly_label == "ANOMALOUS" and policy_decision == "SOFT_FAIL":
        # Rule 2 — hard anomaly + soft policy = probable fraud.
        final_status = "FRAUDULENT"
        action       = "MANUAL_REVIEW"
        reason_codes = [
            *(f"ANOMALY_HIGH:{f.get('name', f)}" for f in top_features),
            *(f"POLICY_SOFT_FAIL:{r}" for r in explanations),
        ]
        final_score = round((1 - policy_score / 100) * 0.4 + combined_score * 0.6, 3)

    elif policy_decision == "PASS" and anomaly_label == "ANOMALOUS":
        # Rule 3 — policy clean but ML flagged: worth human eyes.
        final_status = "SUSPICIOUS"
        action       = "MANUAL_REVIEW"
        reason_codes = [f"ANOMALY_ONLY:{f.get('name', f)}" for f in top_features]
        final_score  = round(combined_score * 0.8, 3)

    elif policy_decision == "SOFT_FAIL" and anomaly_label in ("NORMAL", "SUSPICIOUS"):
        # Rule 4 — soft policy breach, ML not alarming.
        final_status = "SUSPICIOUS"
        action       = "MANAGER_REVIEW"
        reason_codes = [f"POLICY_SOFT:{r}" for r in explanations]
        final_score  = round((1 - policy_score / 100) * 0.6 + combined_score * 0.4, 3)

    else:
        # Rule 5 — everything clean.
        final_status = "VALID"
        action       = "AUTO_APPROVE"
        reason_codes = ["CLEAN"]
        final_score  = round(combined_score * 0.3, 3)

    return {
        "claim_id":       policy.get("claim_id"),
        "final_status":   final_status,
        "final_score":    final_score,
        "action":         action,
        "decision_reason": reason_codes,
        "policy_summary": {
            "decision":         policy_decision,
            "violations_count": violations_count,
            "policy_score":     policy_score,
            "hard_reject":      hard_reject,
            "service_available": policy.get("service_available", True),
        },
        "anomaly_summary": {
            "label":            anomaly_label,
            "combined_score":   combined_score,
            "confidence":       confidence,
            "top_features":     top_features,
        },
    }
