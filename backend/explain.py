"""Reason-code generation for claim verdicts (multi-tenant).

Each reason has:

    severity     : HIGH | MEDIUM | LOW | INFO
    code         : machine-readable identifier (for audit log / analytics)
    message      : plain-English sentence shown on the dashboard
    tech_detail  : optional technical explanation (z-scores, raw residuals
                   etc.) — shown only when the user toggles 'Show details'
    source       : 'policy' | 'behavioural' | 'ml' | 'context'

All policy rules come from a CompanyRules object passed in by the caller,
so different client companies get different verdicts for the same claim.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .companies import CompanyRules

FEATURE_PLAIN = {
    "num__amount": ("the claim amount", "The claim amount"),
    "num__amount_log": ("the claim amount", "The claim amount"),
    "num__amount_vs_category_mean": (
        "the amount compared to other claims in this category",
        "The amount compared to other claims in this category",
    ),
    "num__amount_vs_employee_mean": (
        "the amount compared to this employee's usual spending",
        "The amount compared to this employee's usual spending",
    ),
    "num__day_of_week": ("the day of submission", "The day of submission"),
    "num__hour_of_day": ("the time of submission", "The time of submission"),
    "num__is_weekend": ("the weekend-vs-weekday pattern", "The weekend-vs-weekday pattern"),
    "num__is_off_hours": ("the hour-of-day pattern", "The hour-of-day pattern"),
    "num__days_since_last_claim": (
        "the gap since the employee's previous claim",
        "The gap since the employee's previous claim",
    ),
    "num__vendor_frequency": (
        "how often this vendor appears in prior claims",
        "How often this vendor appears in prior claims",
    ),
    "num__vendor_repeat_count_3d": (
        "the number of recent claims from the same vendor",
        "The number of recent claims from the same vendor",
    ),
}

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class Reason:
    severity: str
    code: str
    message: str
    source: str
    tech_detail: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fmt_money(x: float) -> str:
    return f"₹{x:,.2f}"


def _magnitude_word(sd: float) -> str:
    if sd >= 7.0:
        return "far"
    if sd >= 5.0:
        return "substantially"
    if sd >= 3.5:
        return "significantly"
    return "somewhat"


def _friendly_feature(feature_name: str) -> tuple[str, str]:
    if feature_name in FEATURE_PLAIN:
        return FEATURE_PLAIN[feature_name]
    if feature_name.startswith("cat__"):
        stripped = feature_name[5:].replace("_", " ")
        return (f"this claim's {stripped}", f"This claim's {stripped}")
    clean = feature_name.replace("num__", "").replace("_", " ")
    return (clean, clean.capitalize())


def _residual_plain_message(feature_name: str, residual: float, direction: str) -> str:
    mag = _magnitude_word(residual)

    if feature_name in ("num__amount", "num__amount_log"):
        if direction == "higher":
            return f"The claim amount is {mag} higher than we'd expect for a normal claim like this."
        return f"The claim amount is {mag} lower than we'd expect for a normal claim like this."

    if feature_name == "num__amount_vs_category_mean":
        if direction == "higher":
            return f"This amount is {mag} above what the company usually spends on this category."
        return f"This amount is {mag} below what the company usually spends on this category."

    if feature_name == "num__amount_vs_employee_mean":
        if direction == "higher":
            return f"This amount is {mag} above this employee's usual spending pattern."
        return f"This amount is {mag} below this employee's usual spending pattern."

    if feature_name == "num__vendor_frequency":
        if direction == "lower":
            return f"This vendor is {mag} less familiar to our records than a typical vendor."
        return f"This vendor appears {mag} more often in our records than a typical vendor."

    if feature_name == "num__days_since_last_claim":
        if direction == "lower":
            return f"This claim was submitted {mag} sooner after the employee's last claim than normal."
        return f"The gap since this employee's previous claim is {mag} longer than normal."

    if feature_name == "num__vendor_repeat_count_3d":
        return "There's an unusual pattern of recent claims from the same vendor by this employee."

    _, upper = _friendly_feature(feature_name)
    if direction == "higher":
        return f"{upper} is {mag} higher than we'd expect for a normal claim."
    return f"{upper} is {mag} lower than we'd expect for a normal claim."


def classify(combined_score: float, rules: "CompanyRules") -> str:
    """Map a combined anomaly score to a verdict using the company's cutoffs.

    NOTE: this only looks at the ML score. The canonical verdict also
    considers rule hits — use ``final_verdict`` once reasons are built.
    """
    if combined_score >= rules.anomalous_threshold:
        return "ANOMALOUS"
    if combined_score >= rules.suspicious_threshold:
        return "SUSPICIOUS"
    return "NORMAL"


def final_verdict(combined_score: float, reasons: list[dict], rules: "CompanyRules") -> str:
    """Combine ML score with rule hits into the final verdict.

    Implements the spec's rule-override principle: hard policy violations
    can force a claim into SUSPICIOUS/ANOMALOUS even when the ML score is
    low. Rules always escalate; they never downgrade an ML-flagged claim.
    """
    ml_label = classify(combined_score, rules)

    high_policy = sum(1 for r in reasons if r["severity"] == "HIGH" and r["source"] == "policy")
    high_behavioural = sum(1 for r in reasons if r["severity"] == "HIGH" and r["source"] == "behavioural")
    medium_policy = sum(1 for r in reasons if r["severity"] == "MEDIUM" and r["source"] == "policy")

    # Two or more HIGH policy breaches, or HIGH policy + HIGH behavioural,
    # is categorical fraud — escalate to ANOMALOUS regardless of ML score.
    if high_policy >= 2 or (high_policy >= 1 and high_behavioural >= 1):
        rule_label = "ANOMALOUS"
    elif high_policy >= 1:
        # A single HIGH policy violation alone => SUSPICIOUS at minimum.
        rule_label = "SUSPICIOUS"
    elif medium_policy >= 2:
        rule_label = "SUSPICIOUS"
    else:
        rule_label = "NORMAL"

    # Take the more severe of ML vs rules — never downgrade.
    order = {"NORMAL": 0, "SUSPICIOUS": 1, "ANOMALOUS": 2}
    return ml_label if order[ml_label] >= order[rule_label] else rule_label


def generate_reasons(
    claim: dict,
    engineered: dict,
    anomaly: dict,
    rules: "CompanyRules",
    claim_flags: Optional[dict] = None,
) -> list[dict]:
    """Produce a ranked list of reason dicts for the dashboard.

    ``rules`` is the CompanyRules for the employee's company — the policy
    layer reads all its limits from there, so different companies produce
    different verdicts for the same claim.
    """
    reasons: list[Reason] = []

    amount = float(claim["amount"])
    category = claim["category"]
    department = claim["department"]
    grade = claim["grade"]
    vendor = claim["vendor"]

    # ------------------------------------------------------------------
    # POLICY-LIKE SIGNALS — all from the company's ruleset
    # ------------------------------------------------------------------
    grade_limit = rules.grade_daily_limit.get(grade)
    if grade_limit is not None and amount > grade_limit:
        factor = amount / grade_limit
        reasons.append(Reason(
            severity="HIGH",
            code="GRADE_LIMIT_EXCEEDED",
            message=(
                f"This claim is {factor:.1f}× over the daily spending limit for a "
                f"{grade}-grade employee at {rules.name} ({_fmt_money(grade_limit)} per day)."
            ),
            tech_detail=f"claim_amount={amount:.2f}, grade_limit={grade_limit}, over_by_factor={factor:.2f}",
            source="policy",
        ))

    category_limit = rules.category_daily_limit.get(category)
    if category_limit is not None and amount > category_limit:
        factor = amount / category_limit
        reasons.append(Reason(
            severity="HIGH" if factor >= 2.0 else "MEDIUM",
            code="CATEGORY_LIMIT_EXCEEDED",
            message=(
                f"This claim is {factor:.1f}× over {rules.name}'s per-claim cap "
                f"for {category} ({_fmt_money(category_limit)})."
            ),
            tech_detail=f"amount={amount:.2f}, category_limit={category_limit}, over_by_factor={factor:.2f}",
            source="policy",
        ))

    allowed_depts = rules.category_restrictions.get(category)
    if allowed_depts is not None and department not in allowed_depts:
        allowed = ", ".join(sorted(allowed_depts))
        reasons.append(Reason(
            severity="HIGH",
            code="CATEGORY_NOT_ALLOWED_FOR_DEPT",
            message=(
                f"{department} employees at {rules.name} aren't allowed to claim "
                f"'{category}' expenses — this category is restricted to {allowed}."
            ),
            tech_detail=f"department={department}, category={category}, allowed={sorted(allowed_depts)}",
            source="policy",
        ))

    if (
        amount >= rules.round_number_threshold
        and amount == round(amount)
        and amount % 500 == 0
    ):
        reasons.append(Reason(
            severity="MEDIUM",
            code="ROUND_NUMBER_AMOUNT",
            message=(
                f"The amount ({_fmt_money(amount)}) is a very round figure — "
                "fabricated receipts often have round amounts, so this is worth a quick check."
            ),
            tech_detail=(
                f"amount={amount}, company threshold={rules.round_number_threshold}, "
                f"ends in 00 and is a multiple of 500"
            ),
            source="policy",
        ))

    # ------------------------------------------------------------------
    # BEHAVIOURAL SIGNALS (engineered features; same for every company)
    # ------------------------------------------------------------------
    a_vs_cat = engineered.get("amount_vs_category_mean", 1.0)
    if a_vs_cat >= 3.0:
        reasons.append(Reason(
            severity="HIGH" if a_vs_cat >= 5.0 else "MEDIUM",
            code="AMOUNT_HIGH_VS_CATEGORY",
            message=(
                f"This claim is about {a_vs_cat:.1f}× larger than the typical "
                f"{category} claim across all companies."
            ),
            tech_detail=f"amount_vs_category_mean={a_vs_cat:.3f}",
            source="behavioural",
        ))

    a_vs_emp = engineered.get("amount_vs_employee_mean", 1.0)
    if a_vs_emp >= 3.0:
        reasons.append(Reason(
            severity="HIGH" if a_vs_emp >= 5.0 else "MEDIUM",
            code="AMOUNT_HIGH_VS_EMPLOYEE",
            message=(
                f"This claim is about {a_vs_emp:.1f}× larger than what this "
                "employee usually spends per claim."
            ),
            tech_detail=f"amount_vs_employee_mean={a_vs_emp:.3f}",
            source="behavioural",
        ))

    is_off_hours = int(engineered.get("is_off_hours", 0))
    hour = int(engineered.get("hour_of_day", 12))
    if is_off_hours:
        reasons.append(Reason(
            severity="MEDIUM",
            code="SUBMITTED_OFF_HOURS",
            message=(
                f"Submitted at {hour:02d}:00 — outside normal working hours "
                "(08:00–20:00). Most genuine claims are filed during business hours."
            ),
            tech_detail=f"hour_of_day={hour}",
            source="behavioural",
        ))

    is_weekend = int(engineered.get("is_weekend", 0))
    dow = int(engineered.get("day_of_week", 0))
    if is_weekend:
        reasons.append(Reason(
            severity="LOW",
            code="SUBMITTED_ON_WEEKEND",
            message=f"Submitted on a {DAY_NAMES[dow]} — weekend filings are unusual.",
            tech_detail=f"day_of_week={dow} ({DAY_NAMES[dow]})",
            source="behavioural",
        ))

    vrc = int(engineered.get("vendor_repeat_count_3d", 0))
    if vrc >= 1:
        reasons.append(Reason(
            severity="HIGH",
            code="DUPLICATE_VENDOR_3D",
            message=(
                f"This employee has already claimed from '{vendor}' "
                f"{vrc} time(s) in the past 3 days — this might be a duplicate submission."
            ),
            tech_detail=f"vendor={vendor!r}, vendor_repeat_count_3d={vrc}",
            source="behavioural",
        ))

    dsl = float(engineered.get("days_since_last_claim", 9999.0))
    if dsl < 1.0 and dsl < 9999.0:
        hours = dsl * 24
        reasons.append(Reason(
            severity="LOW",
            code="RAPID_RESUBMISSION",
            message=(
                f"This claim was submitted just {hours:.1f} hours after the "
                "employee's previous claim — faster than normal."
            ),
            tech_detail=f"days_since_last_claim={dsl:.4f}",
            source="behavioural",
        ))

    # ------------------------------------------------------------------
    # CUSTOM RULES (company-authored JSON rules)
    # ------------------------------------------------------------------
    from .custom_rules import evaluate as _eval_custom
    custom_reasons = _eval_custom(
        getattr(rules, "custom_rules", []) or [],
        claim, engineered, claim_flags or {},
    )
    for cr in custom_reasons:
        reasons.append(Reason(
            severity=cr["severity"], code=cr["code"],
            message=cr["message"], source=cr["source"],
            tech_detail=cr["tech_detail"],
        ))

    # ------------------------------------------------------------------
    # ML SIGNALS
    # ------------------------------------------------------------------
    combined = float(anomaly.get("combined_anomaly_score", 0.0))
    if_s = float(anomaly.get("isolation_forest", {}).get("anomaly_score", 0.0))
    ae_s = float(anomaly.get("autoencoder", {}).get("anomaly_score", 0.0))

    if combined >= rules.anomalous_threshold:
        reasons.append(Reason(
            severity="HIGH",
            code="ML_HIGHLY_ANOMALOUS",
            message=(
                f"Our fraud-detection AI is highly confident this claim looks unusual "
                f"(score {int(combined * 100)}/100, {rules.name}'s threshold "
                f"{int(rules.anomalous_threshold * 100)}). Recommend detailed review."
            ),
            tech_detail=f"combined={combined:.3f} (IF={if_s:.3f}, AE={ae_s:.3f}); anomalous_cutoff={rules.anomalous_threshold}",
            source="ml",
        ))
    elif combined >= rules.suspicious_threshold:
        reasons.append(Reason(
            severity="MEDIUM",
            code="ML_MODERATELY_ANOMALOUS",
            message=(
                f"Our fraud-detection AI finds this claim moderately unusual "
                f"(score {int(combined * 100)}/100, {rules.name}'s threshold "
                f"{int(rules.suspicious_threshold * 100)}). Worth a second look."
            ),
            tech_detail=f"combined={combined:.3f} (IF={if_s:.3f}, AE={ae_s:.3f}); suspicious_cutoff={rules.suspicious_threshold}",
            source="ml",
        ))

    if if_s >= 0.7 and ae_s >= 0.7:
        reasons.append(Reason(
            severity="HIGH",
            code="BOTH_MODELS_AGREE",
            message=(
                "Two independent AI checks both flagged this claim — "
                "stronger signal than either alone."
            ),
            tech_detail=f"isolation_forest={if_s:.3f}, autoencoder={ae_s:.3f}",
            source="ml",
        ))

    RESIDUAL_THRESHOLD = 2.5
    for i, feat in enumerate(anomaly.get("top_features", [])[:3]):
        residual = float(feat.get("residual", 0.0))
        if residual < RESIDUAL_THRESHOLD:
            continue
        name = feat["name"]
        actual = float(feat.get("actual", 0.0))
        expected = float(feat.get("expected", 0.0))
        direction = "higher" if actual > expected else "lower"
        severity = "MEDIUM" if (i == 0 and residual >= 4.0) else "LOW"
        reasons.append(Reason(
            severity=severity,
            code=f"ML_TOP_RESIDUAL_{i + 1}",
            message=_residual_plain_message(name, residual, direction),
            tech_detail=(
                f"Feature '{name}' — observed z-score={actual:+.2f}, "
                f"expected z-score={expected:+.2f}, residual={residual:.2f} SDs. "
                "(Features are standardised: training mean=0, SD=1. Anything > 3 SDs is statistically unusual.)"
            ),
            source="ml",
        ))

    if not reasons:
        reasons.append(Reason(
            severity="INFO",
            code="NO_FLAGS",
            message="Nothing unusual detected — this claim looks typical for this employee and category.",
            tech_detail=None,
            source="context",
        ))

    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
    source_order = {"policy": 0, "behavioural": 1, "ml": 2, "context": 3}
    reasons.sort(key=lambda r: (severity_order[r.severity], source_order[r.source]))

    return [r.as_dict() for r in reasons]
