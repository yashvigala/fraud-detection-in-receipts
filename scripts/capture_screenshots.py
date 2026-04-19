"""Capture polished UI screenshots of every key page for the README.

Runs against the live backend on http://127.0.0.1:8765. Logs in as each
role, navigates to the relevant pages, and writes PNGs to screenshots/.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8765"
OUT = Path(__file__).resolve().parent.parent / "screenshots"
OUT.mkdir(exist_ok=True)

VIEWPORT = {"width": 1440, "height": 900}

SHOTS = [
    # (role, company, employee_id, path_after_login, filename, full_page, wait_ms)
    ("none",     None,    None,        "/",                     "01_landing.png",          True,  600),
    ("none",     None,    None,        "/login",                "02_login.png",            False, 600),
    ("employee", "ACME",  "EMP00003",  "/employee/dashboard",   "03_employee_dashboard.png", True, 1200),
    ("employee", "ACME",  "EMP00003",  "/employee/submit",      "04_submit_empty.png",     True,  900),
    ("manager",  "ACME",  None,        "/manager/queue",        "05_manager_queue.png",    True,  1500),
    ("admin",    "ACME",  None,        "/admin/dashboard",      "06_admin_dashboard.png",  True,  1800),
    ("admin",    "ACME",  None,        "/admin/onboarding",     "07_admin_rules.png",      True,  1500),
    ("admin",    "ACME",  None,        "/analytics",            "08_analytics.png",        True,  2000),
]


def login(page, role, company, employee_id):
    """Use the fake-auth API directly, then set the cookie in the browser."""
    if role == "none":
        return
    body = {"role": role, "company_id": company}
    if employee_id:
        body["employee_id"] = employee_id
    # Use the page's fetch so the cookie lands in the same browser context.
    resp = page.request.post(BASE + "/api/auth/login", data=json.dumps(body),
                              headers={"Content-Type": "application/json"})
    assert resp.ok, f"Login failed: {resp.status} {resp.text()}"


def pick_first_claim_id(page):
    """Grab any claim_id so we can also screenshot the claim-detail page."""
    resp = page.request.get(BASE + "/api/claims/queue?status=ALL&days=180")
    if not resp.ok:
        return None
    data = resp.json()
    claims = data.get("claims") or []
    return claims[0]["id"] if claims else None


def capture_shots():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
        page = context.new_page()

        for role, company, employee_id, path, filename, full, wait_ms in SHOTS:
            print(f"  -> {filename}  ({role}/{path})")
            # Re-login if the role changed
            if role != "none":
                login(page, role, company, employee_id)
            page.goto(BASE + path, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(wait_ms)
            page.screenshot(path=str(OUT / filename), full_page=full, type="png")

        # One extra: pick a real claim and screenshot the detail page
        login(page, "manager", "ACME", None)
        claim_id = pick_first_claim_id(page)
        if claim_id:
            print(f"  -> 09_claim_detail.png  (manager/claim/{claim_id})")
            page.goto(f"{BASE}/claim/{claim_id}", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1500)
            page.screenshot(path=str(OUT / "09_claim_detail.png"), full_page=True, type="png")

        browser.close()


if __name__ == "__main__":
    print(f"Writing screenshots to {OUT}/")
    capture_shots()
    print("Done.")
