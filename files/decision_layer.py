# decision_layer.py
# Pure Python. Stateless + deterministic.
# Receives policy JSON + anomaly JSON, produces final verdict.

from typing import Optional

def make_decision(policy: dict, anomaly: dict) -> dict:
    """
    Combines Drools policy result + ML anomaly result into a single verdict.
    Rule: hard policy violations ALWAYS override ML score.
    """
    hard_reject      = policy.get("hard_reject", False)
    policy_decision  = policy.get("policy_decision", "PASS")
    violations_count = policy.get("violations_count", 0)
    policy_score     = policy.get("policy_score", 100)
    explanations     = policy.get("explanations", [])

    anomaly_label    = anomaly.get("anomaly_label", "NORMAL")
    combined_score   = anomaly.get("combined_anomaly_score", 0.0)
    confidence       = anomaly.get("confidence", 0.0)
    top_features     = anomaly.get("top_features", [])

    reason_codes = []
    final_score  = 0.0

    # --- Rule 1: Hard reject always wins ---
    if hard_reject:
        final_status = "REJECTED"
        action       = "AUTO_REJECT"
        reason_codes = [f"POLICY_HARD_FAIL:{r}" for r in explanations]
        final_score  = 0.0

    # --- Rule 2: Hard anomaly + soft policy = Fraudulent ---
    elif anomaly_label == "ANOMALOUS" and policy_decision == "SOFT_FAIL":
        final_status = "FRAUDULENT"
        action       = "MANUAL_REVIEW"
        reason_codes = [f"ANOMALY_HIGH:{f}" for f in top_features]
        reason_codes += [f"POLICY_SOFT_FAIL:{r}" for r in explanations]
        final_score  = round((1 - policy_score / 100) * 0.4 + combined_score * 0.6, 3)

    # --- Rule 3: Policy clean but ML flags anomaly ---
    elif policy_decision == "PASS" and anomaly_label == "ANOMALOUS":
        final_status = "SUSPICIOUS"
        action       = "MANUAL_REVIEW"
        reason_codes = [f"ANOMALY_ONLY:{f}" for f in top_features]
        final_score  = round(combined_score * 0.8, 3)

    # --- Rule 4: Soft policy violations only ---
    elif policy_decision == "SOFT_FAIL" and anomaly_label in ("NORMAL", "SUSPICIOUS"):
        final_status = "SUSPICIOUS"
        action       = "MANAGER_REVIEW"
        reason_codes = [f"POLICY_SOFT:{r}" for r in explanations]
        final_score  = round((1 - policy_score / 100) * 0.6 + combined_score * 0.4, 3)

    # --- Rule 5: Both clean ---
    else:
        final_status = "VALID"
        action       = "AUTO_APPROVE"
        reason_codes = ["CLEAN"]
        final_score  = round(combined_score * 0.3, 3)

    return {
        "claim_id":       policy.get("claim_id"),
        "final_status":   final_status,       # VALID / SUSPICIOUS / REJECTED / FRAUDULENT
        "final_score":    final_score,         # 0.0 (clean) → 1.0 (definite fraud)
        "action":         action,              # AUTO_APPROVE / MANAGER_REVIEW / MANUAL_REVIEW / AUTO_REJECT
        "decision_reason": reason_codes,
        "policy_summary": {
            "decision":         policy_decision,
            "violations_count": violations_count,
            "policy_score":     policy_score,
            "hard_reject":      hard_reject,
        },
        "anomaly_summary": {
            "label":            anomaly_label,
            "combined_score":   combined_score,
            "confidence":       confidence,
            "top_features":     top_features,
        }
    }
