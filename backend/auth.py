"""Fake cookie-based auth.

Roles: employee / manager / admin. No password — the login page is a
role picker, and the chosen role is written to a ``demo_user`` cookie.
All API endpoints + pages inspect that cookie via ``get_current_user``.

Swap-out note for the viva: this module is ~60 lines. Replacing it with
real JWT is a matter of:
    1. Replacing set_demo_user_cookie() with a JWT signer
    2. Replacing get_current_user() with JWT verification via PyJWT
    3. Adding a real /api/auth/signup + bcrypt password hashing
"""
from __future__ import annotations

import base64
import json
from typing import Optional

from fastapi import HTTPException, Request, Response
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models_db import Company, Employee, User

COOKIE_NAME = "demo_user"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


def _encode(user: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(user).encode()).decode()


def _decode(raw: str) -> Optional[dict]:
    try:
        return json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
    except Exception:
        # Fallback: older plain-JSON cookies
        try:
            return json.loads(raw)
        except Exception:
            return None


def get_current_user(request: Request) -> Optional[dict]:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    return _decode(raw)


def require_user(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_role(*allowed: str):
    def _dep(request: Request) -> dict:
        user = require_user(request)
        if user.get("role") not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Role {user.get('role')!r} not allowed (need: {list(allowed)})"
            )
        return user
    return _dep


def login_as(response: Response, role: str, company_id: str, employee_id: str | None = None) -> dict:
    """Write the demo_user cookie. Called by /api/auth/login."""
    # Validate and enrich from DB
    with SessionLocal() as db:
        company = db.get(Company, company_id)
        if not company:
            raise HTTPException(status_code=400, detail=f"Unknown company: {company_id}")

        # For employee role, attach the picked employee (or pick first of company)
        if role == "employee":
            if employee_id:
                emp = db.get(Employee, employee_id)
                if not emp or emp.company_id != company_id:
                    raise HTTPException(status_code=400, detail="Invalid employee_id for this company")
            else:
                emp = db.query(Employee).filter(Employee.company_id == company_id).first()
                if not emp:
                    raise HTTPException(status_code=400, detail=f"No employees at {company_id}")
            user = {
                "email":       f"{emp.id.lower()}@{company_id.lower()}.demo",
                "role":        "employee",
                "company_id":  company_id,
                "company_name": company.name,
                "employee_id": emp.id,
                "employee_name": emp.name,
                "department":  emp.department,
                "grade":       emp.grade,
            }
        else:
            user = {
                "email":       f"{role}@{company_id.lower()}.demo",
                "role":        role,
                "company_id":  company_id,
                "company_name": company.name,
                "employee_id": None,
            }

    response.set_cookie(
        key=COOKIE_NAME,
        value=_encode(user),
        max_age=COOKIE_MAX_AGE,
        httponly=False,  # frontend reads it for personalisation; fine for demo
        samesite="lax",
    )
    return user


def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)
