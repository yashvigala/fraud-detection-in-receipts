"""Import custom rules from two sources:

    1. A JSON file or paste — trivial; just parse + validate.
    2. A PDF of the company's expense policy — harder: we ask Gemini
       Flash to read the PDF and propose a JSON rule array matching
       our schema. The admin reviews Gemini's proposal and picks which
       to save, so the LLM never writes directly into the audit trail.

Gemini accepts binary PDFs via ``inline_data`` with mime type
``application/pdf``. The prompt below pins the schema tightly.
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

import google.generativeai as genai
from dotenv import load_dotenv

from .custom_rules import ALLOWED_PREDICATES, validate_rule
from .ocr import OcrUnavailable  # reuse the "not configured" sentinel

load_dotenv()

_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

_PROMPT = f"""You are an expense-policy parser. Read the attached company
expense policy document and extract every enforceable rule as a JSON array.

Each rule MUST conform to this schema:

{{
  "id":        "<short uppercase identifier, e.g. R001, LODGING_CAP>",
  "name":     "<short human-readable name>",
  "severity": "HARD" | "SOFT",
  "enabled":   true,
  "when":     {{ <one or more of the predicates below, ALL combined with AND> }},
  "deduction": <integer 0-100, how much policy-score to deduct when fired>,
  "message":  "<plain-English reason shown to the employee; can use {{amount}}, {{vendor}}, {{category}} placeholders>"
}}

Supported "when" predicates (use ONLY these keys):
{sorted(ALLOWED_PREDICATES)}

Rules on the rules:
- HARD = auto-reject the claim. SOFT = flag for manager review.
- If the policy says "up to X", translate as amount_lte / amount_gt accordingly.
- If it restricts a category to certain departments, emit one rule per forbidden department
  using department + category predicates + message "X cannot claim Y".
- Keep messages under 140 characters.
- If no rules can be extracted, return an empty array: [].

Return ONLY the JSON array. No prose, no markdown.
"""


def _configure() -> None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key or key == "your_key_here":
        raise OcrUnavailable("GEMINI_API_KEY not configured")
    genai.configure(api_key=key)


def _extract_json(text: str) -> list[dict]:
    """Pull the JSON array out of the LLM response, even if it wraps it
    in code fences or adds chatter (we don't trust it not to)."""
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1)
    # Find the first '[' and try to parse from there.
    i = text.find("[")
    if i < 0:
        raise ValueError("No JSON array in response")
    data = json.loads(text[i:])
    if not isinstance(data, list):
        raise ValueError("Response is not a JSON array")
    return data


def parse_policy_pdf(pdf_bytes: bytes) -> list[dict]:
    """Send a policy PDF to Gemini + return the parsed, validated list
    of custom rules. Invalid entries are silently dropped; we return a
    `_parse_errors` list instead? No — raise ValueError with details,
    so the admin sees why."""
    _configure()
    model = genai.GenerativeModel(_MODEL)
    response = model.generate_content(
        [
            _PROMPT,
            {
                "mime_type": "application/pdf",
                "data": base64.b64encode(pdf_bytes).decode("ascii"),
            },
        ],
        generation_config={"temperature": 0.1},
    )
    raw = _extract_json(response.text)
    return _normalise(raw)


def parse_policy_json_text(text: str) -> list[dict]:
    """Accept JSON text in any of these shapes and return a normalised list:
        1. An array of rules:              [{...}, {...}]
        2. A wrapper object with a list:   {"rules": [...]} or {"custom_rules": [...]}
        3. A single rule object:           {"id": ..., "when": ..., "message": ...}
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty input")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e.msg} at line {e.lineno}, col {e.colno}") from e

    if isinstance(data, list):
        pass  # already a list of rules
    elif isinstance(data, dict):
        if "rules" in data and isinstance(data["rules"], list):
            data = data["rules"]
        elif "custom_rules" in data and isinstance(data["custom_rules"], list):
            data = data["custom_rules"]
        elif "id" in data and "when" in data:
            # Single rule object — wrap it so the evaluator sees a list.
            data = [data]
        else:
            raise ValueError(
                "Unrecognised shape. Expected one of: "
                "an array of rules, "
                '{"rules": [...]}, '
                'or a single rule object with id/when/message fields.'
            )
    else:
        raise ValueError("Expected a JSON object or array, got " + type(data).__name__)

    return _normalise(data)


def _normalise(rules: list) -> list[dict]:
    """Coerce, validate, and dedupe IDs."""
    out: list[dict] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            errors.append(f"#{i}: not an object")
            continue
        # Fill sensible defaults
        r.setdefault("enabled", True)
        r.setdefault("deduction", 100 if r.get("severity") == "HARD" else 30)
        # Uppercase the ID and severity
        if "id" in r and isinstance(r["id"], str):
            r["id"] = r["id"].strip().upper()
        if "severity" in r and isinstance(r["severity"], str):
            r["severity"] = r["severity"].strip().upper()
        ok, err = validate_rule(r)
        if not ok:
            errors.append(f"#{i} ({r.get('id','?')}): {err}")
            continue
        if r["id"] in seen_ids:
            errors.append(f"#{i}: duplicate id {r['id']!r}")
            continue
        seen_ids.add(r["id"])
        out.append(r)
    if errors and not out:
        raise ValueError("No valid rules: " + "; ".join(errors))
    # Attach errors so the caller can surface warnings in the UI.
    setattr(_normalise, "_last_errors", errors)
    return out
