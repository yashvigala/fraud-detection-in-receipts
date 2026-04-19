"""Twilio voice-call notification on upload failures.

Placed when:
    * The uploaded file fails to decode as an image, or
    * OCR returns a confidence below the configured threshold (blurry photo).

The call uses inline TwiML to speak a short message — no TwiML app hosting
needed. Graceful degradation: if Twilio credentials are missing, the module
records why and returns without raising.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class TwilioUnavailable(Exception):
    """Twilio not configured, or mandatory fields missing."""


@dataclass
class CallResult:
    placed: bool
    sid: Optional[str] = None
    reason: Optional[str] = None
    error: Optional[str] = None


def _required(name: str) -> str:
    v = os.environ.get(name)
    if not v or v.startswith("your_"):
        raise TwilioUnavailable(f"Missing env var: {name}")
    return v


def send_reupload_call(
    to_number: str,
    reason: str,
) -> CallResult:
    """Place a voice call asking the user to re-upload the receipt.

    Parameters
    ----------
    to_number : str
        E.164-formatted destination phone number (e.g. +919876543210).
        Twilio trial accounts can only call **verified** numbers — make sure
        the number is verified in the Twilio console beforehand.
    reason : str
        One-line reason, spoken to the user. Keep under ~120 chars.
    """
    if not to_number:
        return CallResult(placed=False, reason=reason, error="No phone number provided")

    try:
        account_sid = _required("TWILIO_ACCOUNT_SID")
        auth_token = _required("TWILIO_AUTH_TOKEN")
        from_number = _required("TWILIO_FROM_NUMBER")
    except TwilioUnavailable as e:
        logger.warning("Twilio skipped: %s", e)
        return CallResult(placed=False, reason=reason, error=str(e))

    try:
        from twilio.rest import Client
    except ImportError:
        return CallResult(placed=False, reason=reason, error="twilio package not installed")

    # Inline TwiML — no public TwiML URL required. Escape the reason to keep
    # XML valid even if it contains quotes.
    safe_reason = (reason or "").replace("&", "and").replace("<", " ").replace(">", " ")
    twiml = (
        "<Response>"
        "<Say voice=\"alice\">"
        "Hello. This is an automated notification from your Expense Fraud demo. "
        "Your receipt upload failed."
        f" {safe_reason}."
        " Please return to the dashboard and re-upload a clearer image."
        " Thank you."
        "</Say>"
        "</Response>"
    )

    try:
        client = Client(account_sid, auth_token)
        call = client.calls.create(
            to=to_number,
            from_=from_number,
            twiml=twiml,
        )
    except Exception as e:  # noqa: BLE001 — Twilio raises TwilioRestException
        logger.exception("Twilio call failed")
        return CallResult(placed=False, reason=reason, error=f"{type(e).__name__}: {e}")

    logger.info("Twilio call placed: sid=%s to=%s", call.sid, to_number)
    return CallResult(placed=True, sid=call.sid, reason=reason)
