"""Gemini Flash 2.0 OCR integration.

Calls Google AI Studio's Gemini Flash 2.0 model with a preprocessed receipt
image and a strict JSON-schema prompt. Matches the spec: "structured prompt
instructing it to return specific fields in JSON".

Requires a GEMINI_API_KEY in the environment (or a .env file at repo root).
"""
from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Any

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

_MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
_PROMPT = """You are an OCR engine extracting structured data from receipt images.

Return STRICTLY a JSON object with these exact fields (no prose, no markdown):

{
  "vendor": "<merchant / store / restaurant name, as a string; null if unreadable>",
  "date": "<ISO 8601 date YYYY-MM-DD; null if no date visible>",
  "amount": <final total paid as a number; null if unreadable>,
  "tax": <tax amount as a number; 0 if not shown>,
  "currency": "<ISO 4217 currency code, e.g. INR, USD, EUR; null if not shown>",
  "category": "<ONE of: Food, Travel, Lodging, Entertainment, Office Supplies, Fuel, Client Meals, Training, Other>",
  "line_items": [
    {"name": "<item name>", "price": <number>, "quantity": <number>}
  ],
  "confidence": <float in [0, 1]: your own confidence in this extraction>
}

Rules:
- Infer category from vendor name and items if not explicitly stated.
- Normalise the date to ISO 8601 (YYYY-MM-DD).
- Use null (not a guessed value) if a field is unreadable.
- Return ONLY the JSON object. No explanation before or after.
"""


class OcrUnavailable(Exception):
    """Raised when the Gemini API is not configured."""


@dataclass
class OcrResult:
    vendor: str | None
    date: str | None
    amount: float | None
    tax: float | None
    currency: str | None
    category: str | None
    line_items: list[dict[str, Any]]
    confidence: float
    raw: dict[str, Any]


def _configure() -> None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key or key == "your_key_here":
        raise OcrUnavailable(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and "
            "paste your key from https://aistudio.google.com/app/apikey"
        )
    genai.configure(api_key=key)


def _extract_json(text: str) -> dict[str, Any]:
    """Strip any code fences / prose Gemini might wrap around its JSON."""
    text = text.strip()
    # Remove ```json ... ``` fences.
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    return json.loads(text)


def ocr_receipt(image_bytes: bytes, mime_type: str = "image/png") -> OcrResult:
    """Send the image to Gemini and parse its structured JSON response."""
    _configure()
    model = genai.GenerativeModel(_MODEL_NAME)

    response = model.generate_content(
        [
            _PROMPT,
            {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode("utf-8")},
        ],
        generation_config={"temperature": 0.1},
    )
    data = _extract_json(response.text)

    return OcrResult(
        vendor=data.get("vendor"),
        date=data.get("date"),
        amount=float(data["amount"]) if data.get("amount") is not None else None,
        tax=float(data.get("tax") or 0.0),
        currency=data.get("currency"),
        category=data.get("category"),
        line_items=data.get("line_items") or [],
        confidence=float(data.get("confidence") or 0.0),
        raw=data,
    )
