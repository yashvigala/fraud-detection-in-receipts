"""Per-company custom rules — authored as JSON, evaluated in Python.

Each company has a ``custom_rules`` list (alongside the existing
grade/category limits in the rules JSON). A rule has:

    id         — short identifier, shown in the admin UI + reason codes
    name       — human-readable name
    severity   — HARD (auto-reject) or SOFT (flag for review)
    enabled    — toggle without deleting
    when       — flat dict of conditions (all AND-ed)
    deduction  — 0-100 policy-score penalty
    message    — shown to the employee when the rule fires (supports
                 {amount}, {vendor}, {category} placeholders)

Supported ``when`` predicates:

    category              : str (exact match)
    category_in           : list[str] (any of)
    department            : str
    department_in         : list[str]
    grade                 : str
    grade_in              : list[str]
    amount_gt / amount_lt / amount_gte / amount_lte : number
    amount_is_round_multiple_of : number (e.g. 500 → multiples of 500)
    vendor_contains       : str (substring, case-insensitive)
    is_business_trip      : bool
    is_per_diem           : bool
    is_team_meal          : bool
    is_international      : bool
    pre_approval_attached : bool
    receipt_attached      : bool
    justification_missing : bool (true = justification is empty)
    hour_gt / hour_lt     : int (submission hour)
    is_weekend            : bool

This small DSL covers ~90% of real-world rules; richer expressions can
be added later without changing the evaluator's contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ALLOWED_SEVERITIES = ("HARD", "SOFT")
ALLOWED_PREDICATES = {
    "category", "category_in", "department", "department_in",
    "grade", "grade_in",
    "amount_gt", "amount_lt", "amount_gte", "amount_lte",
    "amount_is_round_multiple_of",
    "vendor_contains",
    "is_business_trip", "is_per_diem", "is_team_meal", "is_international",
    "pre_approval_attached", "receipt_attached",
    "justification_missing",
    "hour_gt", "hour_lt", "is_weekend",
}


def validate_rule(r: dict) -> tuple[bool, str]:
    """Return (ok, error_message)."""
    if not isinstance(r, dict):
        return False, "Rule must be an object"
    for req in ("id", "name", "severity", "when", "message"):
        if req not in r:
            return False, f"Missing field: {req!r}"
    if r["severity"] not in ALLOWED_SEVERITIES:
        return False, f"severity must be one of {ALLOWED_SEVERITIES}"
    if not isinstance(r["when"], dict) or not r["when"]:
        return False, "when must be a non-empty object of predicates"
    for k in r["when"]:
        if k not in ALLOWED_PREDICATES:
            return False, f"Unknown predicate {k!r}. Supported: {sorted(ALLOWED_PREDICATES)}"
    if "deduction" in r and not isinstance(r["deduction"], (int, float)):
        return False, "deduction must be a number"
    if "enabled" in r and not isinstance(r["enabled"], bool):
        return False, "enabled must be a boolean"
    return True, ""


def _cond_matches(cond: dict, ctx: dict) -> bool:
    """Evaluate the ``when`` block against a claim context."""
    for k, v in cond.items():
        got = ctx.get(_context_key(k))
        if k == "category":                       ok = got == v
        elif k == "category_in":                  ok = got in (v or [])
        elif k == "department":                   ok = got == v
        elif k == "department_in":                ok = got in (v or [])
        elif k == "grade":                        ok = got == v
        elif k == "grade_in":                     ok = got in (v or [])
        elif k == "amount_gt":                    ok = (got or 0) > v
        elif k == "amount_gte":                   ok = (got or 0) >= v
        elif k == "amount_lt":                    ok = (got or 0) < v
        elif k == "amount_lte":                   ok = (got or 0) <= v
        elif k == "amount_is_round_multiple_of":
            a = got or 0
            ok = a > 0 and float(a) == round(a) and (a % v == 0)
        elif k == "vendor_contains":
            ok = bool(got) and v.lower() in str(got).lower()
        elif k == "is_business_trip":             ok = bool(got) == bool(v)
        elif k == "is_per_diem":                  ok = bool(got) == bool(v)
        elif k == "is_team_meal":                 ok = bool(got) == bool(v)
        elif k == "is_international":             ok = bool(got) == bool(v)
        elif k == "pre_approval_attached":        ok = bool(got) == bool(v)
        elif k == "receipt_attached":             ok = bool(got) == bool(v)
        elif k == "justification_missing":
            empty = not ctx.get("justification_text") or not ctx["justification_text"].strip()
            ok = empty == bool(v)
        elif k == "hour_gt":                      ok = (got or 0) > v
        elif k == "hour_lt":                      ok = (got or 0) < v
        elif k == "is_weekend":                   ok = bool(got) == bool(v)
        else:
            return False  # unknown predicate → rule can't fire (safer)
        if not ok:
            return False
    return True


def _context_key(predicate: str) -> str:
    """Map a predicate key to the context field it reads from."""
    map_ = {
        "category": "category", "category_in": "category",
        "department": "department", "department_in": "department",
        "grade": "grade", "grade_in": "grade",
        "amount_gt": "amount", "amount_lt": "amount",
        "amount_gte": "amount", "amount_lte": "amount",
        "amount_is_round_multiple_of": "amount",
        "vendor_contains": "vendor",
        "is_business_trip": "is_business_trip", "is_per_diem": "is_per_diem",
        "is_team_meal": "is_team_meal", "is_international": "is_international",
        "pre_approval_attached": "pre_approval_attached",
        "receipt_attached": "receipt_attached",
        "justification_missing": "justification_text",
        "hour_gt": "hour_of_day", "hour_lt": "hour_of_day",
        "is_weekend": "is_weekend",
    }
    return map_.get(predicate, predicate)


def evaluate(
    rules: list[dict],
    claim: dict,
    engineered: dict,
    flags: dict,
) -> list[dict]:
    """Return a list of reason dicts for every rule that fired.

    The returned dicts are shaped like explain.py's Reason dataclass so
    they slot directly into the dashboard reasons list.
    """
    # Build context once.
    ctx = {
        "category":              claim.get("category"),
        "department":            claim.get("department"),
        "grade":                 claim.get("grade"),
        "amount":                float(claim.get("amount") or 0),
        "vendor":                claim.get("vendor"),
        "justification_text":    flags.get("justification_text", ""),
        "is_business_trip":      bool(flags.get("is_business_trip")),
        "is_per_diem":           bool(flags.get("is_per_diem")),
        "is_team_meal":          bool(flags.get("is_team_meal")),
        "is_international":      bool(flags.get("is_international")),
        "pre_approval_attached": bool(flags.get("pre_approval_attached")),
        "receipt_attached":      bool(flags.get("receipt_attached", True)),
        "hour_of_day":           int(engineered.get("hour_of_day") or 0),
        "is_weekend":            bool(engineered.get("is_weekend")),
    }

    reasons = []
    for r in rules or []:
        if not r.get("enabled", True):
            continue
        ok, _ = validate_rule(r)
        if not ok:
            continue
        if not _cond_matches(r["when"], ctx):
            continue

        # Interpolate placeholders in the message
        message = str(r["message"])
        try:
            message = message.format(
                amount=f"\u20b9{ctx['amount']:,.2f}",
                vendor=ctx["vendor"] or "—",
                category=ctx["category"] or "—",
                department=ctx["department"] or "—",
                grade=ctx["grade"] or "—",
            )
        except (KeyError, IndexError):
            pass  # leave the raw message if placeholders are malformed

        sev = r["severity"]
        reasons.append({
            "severity":    "HIGH" if sev == "HARD" else "MEDIUM",
            "code":        f"CUSTOM_{r['id']}",
            "message":     message,
            "source":      "policy",
            "tech_detail": f"custom rule {r['id']!r} ({sev}); deduction={r.get('deduction', 100 if sev == 'HARD' else 30)}",
        })
    return reasons
