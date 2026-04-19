"""Content-check uploaded claim attachments with Gemini.

When an employee ticks "Pre-approval attached" or "Attendee list attached"
and uploads a file, the frontend forces them to attach something — but
that file could be a cat picture. This module sends each attachment to
Gemini with a strict schema-locked prompt asking:

    "Does this file actually look like a <pre-approval | attendee list>?"

Gemini returns a JSON verdict. If it's a confident NO, the backend
coerces the matching flag to False — so policy rules that depend on the
attachment (e.g. ``pre_approval_attached: false`` → HARD reject) fire
correctly regardless of the user's claim.

The validation is best-effort: if Gemini is unreachable, quota'd out, or
returns garbage, we default to ``appears_valid: None`` (unknown) — and
the pipeline trusts the user. That keeps the demo resilient.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

# Per-kind expectation text that goes into the prompt. Keep each blurb
# tight — the whole prompt has to fit Gemini's context comfortably.
_EXPECTATIONS = {
    "pre_approval": (
        "A formal pre-approval document authorising an expense. Typically "
        "contains: approver's name + title (manager or director), the "
        "requestor's name, an approved expense category and/or amount "
        "limit, a date, and sometimes a signature (printed, scanned, or "
        "digital). It is NOT a receipt, selfie, random photo, or generic "
        "document."
    ),
    "attendee_list": (
        "A list of people who attended a business meeting or team meal. "
        "Typically contains: two or more names, often with roles or "
        "company affiliations, an event/meeting title or date, and may "
        "be handwritten, typed, or a screenshot of an email. It is NOT "
        "a receipt, a single selfie, or an unrelated photo."
    ),
}

_PROMPT_TEMPLATE = """You are an attachment validator for an expense-management system.

A user uploaded a file claiming it is a **{kind_label}**.

What we'd expect to see on a valid {kind_label}:
{expectation}

Return STRICTLY this JSON object (no prose, no markdown, no code fences):

{{
  "appears_valid":    <true or false>,
  "confidence":       <float between 0.0 and 1.0>,
  "document_type":    "<one-line description of what you actually see>",
  "reason":           "<one short sentence explaining your verdict>"
}}

Rules:
- If the file is clearly unrelated (cat photo, selfie, random screenshot,
  a receipt instead of the expected document, or blank): appears_valid=false.
- If the file clearly matches the expected document kind: appears_valid=true.
- If you genuinely cannot tell (blurry, very small, language barrier):
  appears_valid=false with confidence around 0.5.
- Be confident and decisive. Err on the side of rejection if uncertain."""


_KIND_LABEL = {
    "pre_approval":  "pre-approval document",
    "attendee_list": "attendee list",
}


def _configure() -> bool:
    key = os.environ.get("GEMINI_API_KEY")
    if not key or key.startswith("your_"):
        return False
    genai.configure(api_key=key)
    return True


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    i = text.find("{")
    if i < 0:
        raise ValueError("No JSON in response")
    return json.loads(text[i:])


async def validate_attachment(kind: str, content: bytes, mime_type: str) -> dict:
    """Validate a single attachment. Returns a dict with keys:

        appears_valid   : True | False | None  (None = validation skipped)
        confidence      : float 0..1
        document_type   : str  (what Gemini thinks it saw)
        reason          : str  (short explanation)
        service         : "gemini" | "skipped" | "error"

    Never raises — failures are captured in the ``service`` field so the
    caller can decide whether to trust it or not.
    """
    if kind not in _EXPECTATIONS:
        return {
            "appears_valid": None,
            "confidence": 0.0,
            "document_type": "(unknown attachment kind)",
            "reason":  f"No validator configured for kind={kind!r}",
            "service": "skipped",
        }

    if not content:
        return {
            "appears_valid": False, "confidence": 1.0,
            "document_type": "(empty file)",
            "reason": "Attachment was empty.",
            "service": "skipped",
        }

    if not _configure():
        return {
            "appears_valid": None, "confidence": 0.0,
            "document_type": "(validation unavailable)",
            "reason": "Gemini API key not configured; attachment accepted unverified.",
            "service": "skipped",
        }

    prompt = _PROMPT_TEMPLATE.format(
        kind_label=_KIND_LABEL.get(kind, kind),
        expectation=_EXPECTATIONS[kind],
    )

    try:
        model = genai.GenerativeModel(_MODEL)
        response = await model.generate_content_async(
            [
                prompt,
                {
                    "mime_type": mime_type or "application/octet-stream",
                    "data": base64.b64encode(content).decode("ascii"),
                },
            ],
            generation_config={"temperature": 0.1},
        )
        data = _extract_json(response.text)
    except Exception as e:  # noqa: BLE001 — validator must never take down the pipeline
        logger.warning("Attachment validation failed (%s): %s", kind, e)
        return {
            "appears_valid": None, "confidence": 0.0,
            "document_type": "(validator error)",
            "reason": f"Couldn't reach validator ({type(e).__name__}); attachment accepted unverified.",
            "service": "error",
        }

    return {
        "appears_valid": bool(data.get("appears_valid")),
        "confidence":    float(data.get("confidence", 0.0)),
        "document_type": str(data.get("document_type", "")),
        "reason":        str(data.get("reason", "")),
        "service":       "gemini",
    }
