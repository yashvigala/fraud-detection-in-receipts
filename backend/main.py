"""FastAPI backend — full app with auth, per-role pages, and all APIs.

Routes
------
Public:
    GET  /                       landing page
    GET  /login                  login page
    POST /api/auth/login         sets cookie
    POST /api/auth/logout        clears cookie
    GET  /api/auth/me            current user

Employee:
    GET  /employee/dashboard     employee dashboard (my claims)
    GET  /employee/submit        submit receipt
    GET  /api/claims/mine        list my claims

Manager / Admin:
    GET  /manager/queue          review queue
    GET  /claim/{claim_id}       claim detail
    GET  /api/claims/queue       flagged claims
    GET  /api/claims/{id}        claim detail JSON
    POST /api/claims/{id}/review manager decision
    GET  /analytics              charts page
    GET  /api/analytics/summary  chart data

Admin only:
    GET  /admin/dashboard        stats + trend
    GET  /admin/onboarding       rule editor (existing /admin -> redirected)
    GET  /api/admin/stats        high-level stats
    GET  /api/reports/pdf        expense report PDF
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import cv2
import joblib
import numpy as np
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, desc, func, or_
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.image_preprocessing import preprocess_image  # noqa: E402
from src.models import Ensemble  # noqa: E402

from .auth import (  # noqa: E402
    get_current_user, login_as, logout as auth_logout, require_role, require_user,
)
from .companies import CompanyStore  # noqa: E402
from .db import SessionLocal  # noqa: E402
from .decision_layer import make_decision  # noqa: E402
from .explain import classify, final_verdict, generate_reasons  # noqa: E402
from .features_online import FeatureStore  # noqa: E402
from .models_db import AuditLog, Claim, Company, Employee, Verdict  # noqa: E402
from .notifications import send_reupload_call  # noqa: E402
from .ocr import OcrUnavailable, ocr_receipt  # noqa: E402
from .attachment_validator import validate_attachment  # noqa: E402
from .persistence import persist_submission, review_claim  # noqa: E402
from .policy_client import call_policy_engine  # noqa: E402

_OCR_CONF_THRESHOLD = float(os.environ.get("OCR_CONFIDENCE_CALL_THRESHOLD", "0.4"))

# Short human labels used in attachment-coercion toasts.
_KIND_LABEL_SHORT = {
    "pre_approval":  "pre-approval document",
    "attendee_list": "attendee list",
}

logger = logging.getLogger("backend")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
PROCESSED_DIR = ROOT / "data" / "processed"
SYNTHETIC_CLAIMS = ROOT / "data" / "synthetic" / "claims.csv"
STATIC_DIR = Path(__file__).resolve().parent / "static"
PAGES_DIR = STATIC_DIR / "pages"

app = FastAPI(title="ExpenseAI", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


class _State:
    ensemble: Ensemble | None = None
    preprocessor: Any = None
    feature_store: FeatureStore | None = None
    companies: CompanyStore | None = None


STATE = _State()


def _load_once() -> None:
    if STATE.ensemble is None:
        STATE.ensemble = Ensemble.load(str(MODELS_DIR / "ensemble.joblib"))
    if STATE.preprocessor is None:
        STATE.preprocessor = joblib.load(PROCESSED_DIR / "preprocessor.joblib")
    if STATE.feature_store is None:
        STATE.feature_store = FeatureStore(SYNTHETIC_CLAIMS)
    if STATE.companies is None:
        STATE.companies = CompanyStore()


# =========================================================================
# AUTH + PAGE ROUTES
# =========================================================================

def _page(name: str) -> FileResponse:
    return FileResponse(PAGES_DIR / name)


@app.get("/")
async def page_landing(): return _page("landing.html")

@app.get("/login")
async def page_login(): return _page("login.html")

@app.get("/employee/dashboard")
async def page_employee_dashboard(): return _page("employee_dashboard.html")

@app.get("/employee/submit")
async def page_submit(): return _page("submit.html")

@app.get("/manager/queue")
async def page_manager_queue(): return _page("manager_queue.html")

@app.get("/claim/{claim_id}")
async def page_claim_detail(claim_id: str): return _page("claim_detail.html")

@app.get("/admin/dashboard")
async def page_admin_dashboard(): return _page("admin_dashboard.html")

@app.get("/admin/onboarding")
async def page_admin_onboarding(): return _page("admin_onboarding.html")

@app.get("/admin")
async def page_admin_redir(): return RedirectResponse(url="/admin/onboarding")

@app.get("/analytics")
async def page_analytics(): return _page("analytics.html")


# =========================================================================
# AUTH API
# =========================================================================

@app.post("/api/auth/login")
async def api_login(response: Response, body: dict):
    role = body.get("role")
    company_id = body.get("company_id")
    employee_id = body.get("employee_id")
    if role not in ("employee", "manager", "admin"):
        raise HTTPException(status_code=400, detail="Invalid role")
    if not company_id:
        raise HTTPException(status_code=400, detail="company_id required")
    user = login_as(response, role=role, company_id=company_id, employee_id=employee_id)
    return {"ok": True, "user": user}


@app.post("/api/auth/logout")
async def api_logout(response: Response):
    auth_logout(response)
    return {"ok": True}


@app.get("/api/auth/me")
async def api_me(request: Request):
    u = get_current_user(request)
    return {"user": u}


@app.get("/api/auth/options")
async def api_login_options():
    """Companies + sample employees for the login picker."""
    _load_once()
    with SessionLocal() as db:
        companies = db.query(Company).all()
        out = []
        for c in companies:
            emps = (
                db.query(Employee)
                .filter(Employee.company_id == c.id)
                .order_by(Employee.id)
                .limit(12)
                .all()
            )
            out.append({
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "sample_employees": [
                    {"id": e.id, "name": e.name, "department": e.department, "grade": e.grade}
                    for e in emps
                ],
            })
    return {"companies": out}


# =========================================================================
# CORE CONFIG API (for the submit page dropdowns, etc.)
# =========================================================================

@app.get("/api/config")
async def config():
    _load_once()
    fs = STATE.feature_store
    cs = STATE.companies
    employees = fs.known_employees()
    for e in employees:
        e["company_id"] = cs.company_for_employee(e["employee_id"])
    return JSONResponse({
        "employees": employees,
        "categories": fs.known_categories(),
        "vendors": fs.known_vendors(),
        "companies": cs.list_companies(),
    })


# =========================================================================
# COMPANY RULES API (existing admin page — unchanged)
# =========================================================================

@app.get("/api/companies")
async def api_list_companies():
    _load_once()
    return JSONResponse({"companies": STATE.companies.list_companies()})


@app.get("/api/companies/{company_id}")
async def api_get_company(company_id: str):
    _load_once()
    try:
        rules = STATE.companies.get(company_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown company: {company_id}")
    return JSONResponse(rules.as_dict())


@app.put("/api/companies/{company_id}")
async def api_update_company(company_id: str, payload: dict):
    _load_once()
    try:
        updated = STATE.companies.update_rules(company_id, payload)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown company: {company_id}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(updated.as_dict())


# ---------------- Custom-rule management ----------------

@app.get("/api/companies/{company_id}/custom-rules")
async def api_custom_rules_list(company_id: str, user: dict = Depends(require_role("admin"))):
    _load_once()
    try:
        rules = STATE.companies.get(company_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown company")
    if company_id != user["company_id"]:
        raise HTTPException(status_code=403, detail="Cross-company edits not allowed")
    return {"custom_rules": rules.custom_rules}


@app.post("/api/companies/{company_id}/custom-rules")
async def api_custom_rules_add(company_id: str, body: dict,
                               user: dict = Depends(require_role("admin"))):
    """Add a single rule or replace the whole array if ``replace=true``."""
    _load_once()
    if company_id != user["company_id"]:
        raise HTTPException(status_code=403, detail="Cross-company edits not allowed")
    try:
        existing = STATE.companies.get(company_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown company")

    from .custom_rules import validate_rule

    if body.get("replace"):
        new_rules = list(body.get("rules") or [])
    else:
        one = body.get("rule") or body
        new_rules = list(existing.custom_rules)
        # If the ID already exists, update it; otherwise append.
        new_rules = [r for r in new_rules if r.get("id") != one.get("id")]
        new_rules.append(one)

    # Validate each (server-side safety net; UI validates too).
    for r in new_rules:
        ok, err = validate_rule(r)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Rule {r.get('id','?')}: {err}")

    updated = STATE.companies.update_rules(company_id, {"custom_rules": new_rules})
    return {"custom_rules": updated.custom_rules}


@app.delete("/api/companies/{company_id}/custom-rules/{rule_id}")
async def api_custom_rules_delete(company_id: str, rule_id: str,
                                  user: dict = Depends(require_role("admin"))):
    _load_once()
    if company_id != user["company_id"]:
        raise HTTPException(status_code=403, detail="Cross-company edits not allowed")
    existing = STATE.companies.get(company_id)
    new_rules = [r for r in existing.custom_rules if r.get("id") != rule_id]
    if len(new_rules) == len(existing.custom_rules):
        raise HTTPException(status_code=404, detail=f"No custom rule with id {rule_id!r}")
    updated = STATE.companies.update_rules(company_id, {"custom_rules": new_rules})
    return {"custom_rules": updated.custom_rules}


@app.post("/api/companies/{company_id}/custom-rules/{rule_id}/toggle")
async def api_custom_rules_toggle(company_id: str, rule_id: str,
                                  user: dict = Depends(require_role("admin"))):
    _load_once()
    if company_id != user["company_id"]:
        raise HTTPException(status_code=403, detail="Cross-company edits not allowed")
    existing = STATE.companies.get(company_id)
    out, found = [], False
    for r in existing.custom_rules:
        if r.get("id") == rule_id:
            r = dict(r)
            r["enabled"] = not bool(r.get("enabled", True))
            found = True
        out.append(r)
    if not found:
        raise HTTPException(status_code=404, detail=f"No custom rule with id {rule_id!r}")
    updated = STATE.companies.update_rules(company_id, {"custom_rules": out})
    return {"custom_rules": updated.custom_rules}


@app.post("/api/companies/{company_id}/custom-rules/import-json")
async def api_custom_rules_import_json(
    company_id: str, body: dict,
    user: dict = Depends(require_role("admin")),
):
    """Parse a JSON blob (file contents or pasted text) and propose rules.
    Does NOT save automatically — returns the proposed list so the UI
    can show a preview. Admin then POSTs to /custom-rules with replace=true."""
    from .rules_import import parse_policy_json_text, _normalise
    if company_id != user["company_id"]:
        raise HTTPException(status_code=403, detail="Cross-company edits not allowed")
    try:
        rules = parse_policy_json_text(body.get("text", ""))
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    errors = getattr(_normalise, "_last_errors", [])
    return {"proposed_rules": rules, "warnings": errors}


@app.post("/api/companies/{company_id}/custom-rules/import-pdf")
async def api_custom_rules_import_pdf(
    company_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(require_role("admin")),
):
    """Upload a PDF policy; Gemini parses it to a JSON rule array.
    Returns the proposal for review (does NOT save)."""
    from .rules_import import parse_policy_pdf, _normalise
    if company_id != user["company_id"]:
        raise HTTPException(status_code=403, detail="Cross-company edits not allowed")
    pdf_bytes = await file.read()
    if len(pdf_bytes) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="PDF too large (max 15 MB)")
    if not pdf_bytes[:4] == b"%PDF":
        raise HTTPException(status_code=400, detail="File doesn't look like a PDF")
    try:
        rules = parse_policy_pdf(pdf_bytes)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Could not parse policy PDF: {type(e).__name__}: {e}",
        )
    errors = getattr(_normalise, "_last_errors", [])
    return {"proposed_rules": rules, "warnings": errors}


# =========================================================================
# CLAIM LISTING API
# =========================================================================

def _verdict_dict(v: Verdict) -> dict:
    return {
        "id":             v.id,
        "final_status":   v.final_status,
        "action":         v.action,
        "final_score":    v.final_score,
        "policy_status":  v.policy_status,
        "policy_score":   v.policy_score,
        "ml_label":       v.ml_label,
        "ml_combined_score": v.ml_combined_score,
        "reviewer_comment":  v.reviewer_comment,
        "reviewed_at":    v.reviewed_at.isoformat() if v.reviewed_at else None,
        "created_at":     v.created_at.isoformat(),
    }


def _claim_dict(c: Claim, with_verdict: bool = True, with_employee: bool = True) -> dict:
    d = {
        "id":           c.id,
        "employee_id":  c.employee_id,
        "company_id":   c.company_id,
        "vendor":       c.vendor,
        "category":     c.category,
        "amount":       c.amount,
        "currency":     c.currency,
        "justification": c.justification,
        "submitted_at": c.submitted_at.isoformat(),
    }
    if with_employee and c.employee is not None:
        d["employee_name"] = c.employee.name
        d["department"]    = c.employee.department
        d["grade"]         = c.employee.grade
    if with_verdict and c.verdicts:
        latest = c.verdicts[-1]
        d["verdict"] = _verdict_dict(latest)

    # Summarise attachment state for queue rendering.
    att_summary = _attachments_summary(c.attachments_json)
    d["has_attachments"] = bool(att_summary)
    d["flagged_attachments"] = [
        k for k, a in att_summary.items()
        if (a.get("validation") or {}).get("appears_valid") is False
    ]
    return d


@app.get("/api/claims/mine")
async def api_my_claims(user: dict = Depends(require_role("employee"))):
    with SessionLocal() as db:
        claims = (
            db.query(Claim)
            .filter(Claim.employee_id == user["employee_id"])
            .order_by(desc(Claim.submitted_at))
            .limit(100)
            .all()
        )
        return {"claims": [_claim_dict(c) for c in claims]}


@app.get("/api/claims/queue")
async def api_queue(
    request: Request,
    status: str = "ALL",
    dept: str = "ALL",
    days: int = 90,
):
    user = require_role("manager", "admin")(request)
    with SessionLocal() as db:
        since = datetime.now() - timedelta(days=days)
        q = (
            db.query(Claim)
            .join(Verdict, Verdict.claim_id == Claim.id)
            .filter(Claim.company_id == user["company_id"])
            .filter(Claim.submitted_at >= since)
        )
        if status != "ALL":
            q = q.filter(Verdict.final_status == status)
        if dept != "ALL":
            q = q.join(Employee, Employee.id == Claim.employee_id).filter(Employee.department == dept)
        claims = q.order_by(desc(Claim.submitted_at)).limit(200).all()
        return {"claims": [_claim_dict(c) for c in claims]}


@app.get("/api/claims/{claim_id}")
async def api_claim_detail(claim_id: str, user: dict = Depends(require_user)):
    with SessionLocal() as db:
        c = db.get(Claim, claim_id)
        if not c:
            raise HTTPException(status_code=404, detail="Claim not found")
        # Employees can only see their own; manager/admin see their company's.
        if user["role"] == "employee" and c.employee_id != user.get("employee_id"):
            raise HTTPException(status_code=403, detail="Not your claim")
        if user["role"] in ("manager", "admin") and c.company_id != user["company_id"]:
            raise HTTPException(status_code=403, detail="Not your company")

        verdicts = sorted(c.verdicts, key=lambda v: v.created_at)
        audits = sorted(c.audits, key=lambda a: a.created_at)

        latest = verdicts[-1] if verdicts else None
        return {
            "claim": _claim_dict(c),
            "verdict": _verdict_dict(latest) if latest else None,
            "ocr_json": json.loads(c.ocr_json or "{}"),
            "engineered_features": json.loads(c.engineered_features_json or "{}"),
            "policy_json": json.loads(latest.policy_json or "{}") if latest else {},
            "anomaly_json": json.loads(latest.anomaly_json or "{}") if latest else {},
            "reasons": json.loads(latest.reasons_json or "[]") if latest else [],
            "decision_reasons": json.loads(latest.decision_reasons_json or "[]") if latest else [],
            "audit": [{
                "actor": a.actor, "event": a.event, "detail": a.detail,
                "created_at": a.created_at.isoformat(),
            } for a in audits],
            "original_png_base64": c.original_png_base64,
            "preprocessed_png_base64": c.preprocessed_png_base64,
            "flags": {
                "receipt_attached":      c.receipt_attached,
                "pre_approval_attached": c.pre_approval_attached,
                "is_per_diem":           c.is_per_diem,
                "is_business_trip":      c.is_business_trip,
                "is_team_meal":          c.is_team_meal,
                "attendee_list_attached": c.attendee_list_attached,
            },
            "attachments": _attachments_summary(c.attachments_json),
        }


def _attachments_summary(raw: str) -> dict:
    """Return names/sizes/mimes + validation verdict for the claim-detail
    API. We don't ship the full base64 blob unless the dedicated download
    endpoint is hit."""
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    out = {}
    for k, v in data.items():
        if isinstance(v, dict) and "data_b64" in v:
            out[k] = {
                "name": v.get("name"),
                "mime": v.get("mime"),
                "size": v.get("size"),
                "validation": v.get("validation") or None,
            }
    return out


@app.post("/api/claims/{claim_id}/attachments/{kind}/override")
async def api_attachment_override(
    claim_id: str, kind: str, body: dict,
    user: dict = Depends(require_role("manager", "admin")),
):
    """Manager or admin manually overrides the validator's verdict on an
    attachment. If ``is_valid=true``, the matching claim flag flips back to
    True (so policy re-evaluates as attached). If ``is_valid=false``, we
    keep it coerced as-is and just log the confirmation."""
    if kind not in ("pre_approval", "attendee_list"):
        raise HTTPException(status_code=400, detail="Unknown attachment kind")

    is_valid = bool(body.get("is_valid"))
    comment = str(body.get("comment") or "").strip()

    with SessionLocal() as db:
        claim = db.get(Claim, claim_id)
        if not claim:
            raise HTTPException(status_code=404, detail="Claim not found")
        if claim.company_id != user["company_id"]:
            raise HTTPException(status_code=403, detail="Not your company")

        attachments = json.loads(claim.attachments_json or "{}")
        if kind not in attachments:
            raise HTTPException(
                status_code=404,
                detail=f"No {kind} attachment on this claim to override"
            )

        # Stamp the override onto the attachment's validation dict.
        att = attachments[kind]
        att_val = att.get("validation") or {}
        att["validation"] = {
            **att_val,
            "appears_valid":      is_valid,
            "manually_overridden": True,
            "override_by":         user["email"],
            "override_at":         datetime.now().isoformat(),
            "override_comment":    comment,
            "reason": f"Manually {'approved' if is_valid else 'rejected'} by {user['email']}: {comment or '(no comment)'}",
        }
        claim.attachments_json = json.dumps(attachments, default=str)

        # Flip the DB boolean so the NEXT policy read sees it as attached.
        if kind == "pre_approval":
            claim.pre_approval_attached = is_valid
        elif kind == "attendee_list":
            claim.attendee_list_attached = is_valid

        db.add(AuditLog(
            claim_id=claim_id,
            actor=user["email"],
            event="ATTACHMENT_OVERRIDE",
            detail=f"{kind} manually marked {'VALID' if is_valid else 'INVALID'}"
                   + (f" — '{comment}'" if comment else ""),
        ))
        db.commit()

    return {
        "ok": True,
        "kind": kind,
        "is_valid": is_valid,
        "claim_id": claim_id,
        "new_flag": {
            "pre_approval_attached":  claim.pre_approval_attached,
            "attendee_list_attached": claim.attendee_list_attached,
        }[f"{kind}_attached"],
    }


@app.get("/api/claims/{claim_id}/attachment/{kind}")
async def api_claim_attachment(claim_id: str, kind: str, user: dict = Depends(require_user)):
    """Stream the raw attachment back to the browser (downloadable)."""
    if kind not in ("pre_approval", "attendee_list"):
        raise HTTPException(status_code=400, detail="Unknown attachment kind")
    with SessionLocal() as db:
        c = db.get(Claim, claim_id)
        if not c:
            raise HTTPException(status_code=404, detail="Claim not found")
        if user["role"] == "employee" and c.employee_id != user.get("employee_id"):
            raise HTTPException(status_code=403, detail="Not your claim")
        if user["role"] in ("manager", "admin") and c.company_id != user["company_id"]:
            raise HTTPException(status_code=403, detail="Not your company")
        data = json.loads(c.attachments_json or "{}").get(kind)
    if not data:
        raise HTTPException(status_code=404, detail=f"No {kind} attachment on this claim")
    raw = base64.b64decode(data["data_b64"])
    from io import BytesIO
    return StreamingResponse(
        BytesIO(raw),
        media_type=data.get("mime", "application/octet-stream"),
        headers={"Content-Disposition": f'inline; filename="{data.get("name", kind)}"'},
    )


@app.post("/api/claims/{claim_id}/review")
async def api_review(claim_id: str, body: dict, user: dict = Depends(require_role("manager", "admin"))):
    action = body.get("action", "").upper()
    comment = body.get("comment", "")
    if action not in ("APPROVE", "REJECT"):
        raise HTTPException(status_code=400, detail="action must be APPROVE or REJECT")
    try:
        verdict = review_claim(
            claim_id=claim_id, reviewer_email=user["email"],
            action=action, comment=comment,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True, "verdict": _verdict_dict(verdict)}


# =========================================================================
# STATS + ANALYTICS
# =========================================================================

@app.get("/api/admin/stats")
async def api_admin_stats(user: dict = Depends(require_role("admin", "manager"))):
    with SessionLocal() as db:
        thirty_ago = datetime.now() - timedelta(days=30)
        comp = user["company_id"]

        total_claims = db.query(func.count(Claim.id)).filter(
            Claim.company_id == comp, Claim.submitted_at >= thirty_ago
        ).scalar() or 0
        total_amount = db.query(func.coalesce(func.sum(Claim.amount), 0.0)).filter(
            Claim.company_id == comp, Claim.submitted_at >= thirty_ago
        ).scalar()

        status_counts = dict(
            db.query(Verdict.final_status, func.count(Verdict.id))
            .join(Claim, Claim.id == Verdict.claim_id)
            .filter(Claim.company_id == comp, Claim.submitted_at >= thirty_ago)
            .group_by(Verdict.final_status).all()
        )

        by_dept = db.query(
            Employee.department, func.coalesce(func.sum(Claim.amount), 0.0), func.count(Claim.id)
        ).join(Claim, Claim.employee_id == Employee.id).filter(
            Claim.company_id == comp, Claim.submitted_at >= thirty_ago
        ).group_by(Employee.department).all()

        review_queue_size = db.query(func.count(Verdict.id)).join(
            Claim, Claim.id == Verdict.claim_id
        ).filter(
            Claim.company_id == comp,
            Verdict.final_status.in_(["SUSPICIOUS", "FRAUDULENT"]),
            Verdict.reviewed_at.is_(None),
            Claim.submitted_at >= thirty_ago,
        ).scalar() or 0

        return {
            "period_days": 30,
            "total_claims": int(total_claims),
            "total_amount": float(total_amount),
            "valid_count":      int(status_counts.get("VALID", 0)),
            "suspicious_count": int(status_counts.get("SUSPICIOUS", 0)),
            "rejected_count":   int(status_counts.get("REJECTED", 0)),
            "fraudulent_count": int(status_counts.get("FRAUDULENT", 0)),
            "review_queue_size": int(review_queue_size),
            "by_department": [
                {"department": d, "total_amount": float(amt), "count": int(n)}
                for (d, amt, n) in by_dept
            ],
        }


@app.get("/api/analytics/summary")
async def api_analytics(user: dict = Depends(require_role("admin", "manager")), days: int = 90):
    with SessionLocal() as db:
        since = datetime.now() - timedelta(days=days)
        comp = user["company_id"]

        # Trend: claims + fraud rate per week
        rows = db.execute(_sql_trend(), {"comp": comp, "since": since}).all()
        trend = [{
            "week":     str(r[0])[:10],
            "claims":   int(r[1]),
            "flagged":  int(r[2]),
            "amount":   float(r[3]),
        } for r in rows]

        # Fraud rate by department
        by_dept = db.query(
            Employee.department,
            func.count(Claim.id),
            func.sum(
                (Verdict.final_status.in_(["SUSPICIOUS", "REJECTED", "FRAUDULENT"])).cast(
                    __import__("sqlalchemy").Integer()
                )
            ),
        ).join(Claim, Claim.employee_id == Employee.id
        ).join(Verdict, Verdict.claim_id == Claim.id
        ).filter(Claim.company_id == comp, Claim.submitted_at >= since
        ).group_by(Employee.department).all()

        fraud_by_dept = [
            {"department": d, "count": int(n), "flagged": int(f or 0),
             "fraud_rate": float((f or 0) / n) if n else 0.0}
            for (d, n, f) in by_dept
        ]

        # Final-status distribution (for pie chart)
        status_dist = dict(
            db.query(Verdict.final_status, func.count(Verdict.id))
            .join(Claim, Claim.id == Verdict.claim_id)
            .filter(Claim.company_id == comp, Claim.submitted_at >= since)
            .group_by(Verdict.final_status).all()
        )

        # Top categories by spend
        top_cats = db.query(
            Claim.category, func.sum(Claim.amount), func.count(Claim.id)
        ).filter(
            Claim.company_id == comp, Claim.submitted_at >= since
        ).group_by(Claim.category).order_by(desc(func.sum(Claim.amount))).limit(8).all()

        return {
            "period_days": days,
            "trend": trend,
            "fraud_by_department": fraud_by_dept,
            "status_distribution": {k: int(v) for k, v in status_dist.items()},
            "top_categories": [
                {"category": cat, "total": float(tot), "count": int(n)}
                for (cat, tot, n) in top_cats
            ],
        }


def _sql_trend():
    """SQLite and Postgres both understand strftime(); abstract it."""
    from sqlalchemy import text
    if SessionLocal.kw["bind"].dialect.name == "sqlite":
        return text("""
            SELECT strftime('%Y-%W', submitted_at) AS week,
                   COUNT(c.id),
                   SUM(CASE WHEN v.final_status IN ('SUSPICIOUS','REJECTED','FRAUDULENT') THEN 1 ELSE 0 END),
                   COALESCE(SUM(c.amount), 0)
            FROM claims c
            JOIN verdicts v ON v.claim_id = c.id
            WHERE c.company_id = :comp AND c.submitted_at >= :since
            GROUP BY week
            ORDER BY week
        """)
    # Postgres
    return text("""
        SELECT to_char(date_trunc('week', submitted_at), 'IYYY-IW') AS week,
               COUNT(c.id),
               SUM(CASE WHEN v.final_status IN ('SUSPICIOUS','REJECTED','FRAUDULENT') THEN 1 ELSE 0 END),
               COALESCE(SUM(c.amount), 0)
        FROM claims c
        JOIN verdicts v ON v.claim_id = c.id
        WHERE c.company_id = :comp AND c.submitted_at >= :since
        GROUP BY week
        ORDER BY week
    """)


# =========================================================================
# PDF REPORT
# =========================================================================

@app.get("/api/reports/pdf")
async def api_pdf_report(user: dict = Depends(require_role("admin", "manager")), days: int = 30):
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    )

    with SessionLocal() as db:
        since = datetime.now() - timedelta(days=days)
        comp = user["company_id"]
        comp_name = db.get(Company, comp).name

        total_claims = db.query(func.count(Claim.id)).filter(
            Claim.company_id == comp, Claim.submitted_at >= since
        ).scalar() or 0
        total_amount = db.query(func.coalesce(func.sum(Claim.amount), 0.0)).filter(
            Claim.company_id == comp, Claim.submitted_at >= since
        ).scalar()
        status_counts = dict(
            db.query(Verdict.final_status, func.count(Verdict.id))
            .join(Claim, Claim.id == Verdict.claim_id)
            .filter(Claim.company_id == comp, Claim.submitted_at >= since)
            .group_by(Verdict.final_status).all()
        )
        flagged = (
            db.query(Claim, Verdict)
            .join(Verdict, Verdict.claim_id == Claim.id)
            .filter(
                Claim.company_id == comp, Claim.submitted_at >= since,
                Verdict.final_status.in_(["SUSPICIOUS", "REJECTED", "FRAUDULENT"]),
            )
            .order_by(desc(Claim.submitted_at)).limit(40).all()
        )

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=f"{comp_name} — Expense Report",
                            leftMargin=18*mm, rightMargin=18*mm,
                            topMargin=18*mm, bottomMargin=18*mm)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName="Helvetica-Bold",
                        fontSize=20, leading=24, textColor=colors.HexColor("#1F1E1D"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                        fontSize=13, leading=18, textColor=colors.HexColor("#C15F3C"),
                        spaceBefore=14, spaceAfter=6)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName="Helvetica",
                          fontSize=10, leading=14, textColor=colors.HexColor("#1F1E1D"))
    muted = ParagraphStyle("muted", parent=body, textColor=colors.HexColor("#87827A"), fontSize=9)

    story = []
    story.append(Paragraph(f"{comp_name} — Expense Report", h1))
    story.append(Paragraph(f"Period: last {days} days &nbsp; • &nbsp; Generated: {datetime.now().strftime('%d %b %Y, %H:%M')}", muted))
    story.append(Spacer(1, 14))

    # Summary table
    story.append(Paragraph("Summary", h2))
    data = [
        ["Total claims", f"{total_claims:,}"],
        ["Total spend", f"\u20B9 {total_amount:,.2f}"],
        ["Valid",       f"{status_counts.get('VALID', 0):,}"],
        ["Suspicious",  f"{status_counts.get('SUSPICIOUS', 0):,}"],
        ["Rejected",    f"{status_counts.get('REJECTED', 0):,}"],
        ["Fraudulent",  f"{status_counts.get('FRAUDULENT', 0):,}"],
    ]
    tbl = Table(data, colWidths=[70*mm, 50*mm])
    tbl.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#E8E5DF")),
        ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("TEXTCOLOR", (0,0), (0,-1), colors.HexColor("#52504B")),
        ("TEXTCOLOR", (1,0), (1,-1), colors.HexColor("#1F1E1D")),
        ("ALIGN", (1,0), (1,-1), "RIGHT"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#F6F4EE")),
        ("LEFTPADDING", (0,0), (-1,-1), 10),
        ("RIGHTPADDING", (0,0), (-1,-1), 10),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(tbl)

    # Flagged items
    story.append(Paragraph("Flagged claims", h2))
    if not flagged:
        story.append(Paragraph("No flagged claims in this period.", muted))
    else:
        hdr = ["Claim ID", "Date", "Employee", "Category", "Amount", "Status"]
        rows = [hdr]
        for c, v in flagged[:40]:
            rows.append([
                c.id,
                c.submitted_at.strftime("%d %b %Y"),
                c.employee_id,
                c.category,
                f"\u20B9 {c.amount:,.0f}",
                v.final_status,
            ])
        t = Table(rows, colWidths=[28*mm, 24*mm, 28*mm, 28*mm, 28*mm, 28*mm], repeatRows=1)
        ts = TableStyle([
            ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#E8E5DF")),
            ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
            ("FONTSIZE", (0,0), (-1,-1), 8.5),
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#F6F4EE")),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("TEXTCOLOR", (0,0), (-1,0), colors.HexColor("#52504B")),
            ("ALIGN", (4,1), (4,-1), "RIGHT"),
            ("TEXTCOLOR", (-1,1), (-1,-1), colors.HexColor("#A84F30")),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("LEFTPADDING", (0,0), (-1,-1), 6),
            ("RIGHTPADDING", (0,0), (-1,-1), 6),
            ("TOPPADDING", (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ])
        t.setStyle(ts)
        story.append(t)

    story.append(Spacer(1, 18))
    story.append(Paragraph(
        "Generated by ExpenseAI. Drools policy engine + Isolation Forest + Autoencoder.", muted
    ))
    doc.build(story)
    buf.seek(0)

    filename = f"expense_report_{comp}_{datetime.now().strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        buf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# =========================================================================
# CORE SUBMISSION PIPELINE (Image -> OCR -> Drools || ML -> decision -> DB)
# =========================================================================

def _image_bytes_to_np(data: bytes) -> np.ndarray:
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="Could not decode image")
    return img


def _encode_png_base64(image: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("cv2.imencode failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _truthy(v: str) -> bool:
    if not v: return False
    return v.lower() in ("on", "true", "1", "yes", "y")


def _run_ml_inference(claim: dict):
    engineered = STATE.feature_store.engineer_online(claim)
    X = STATE.preprocessor.transform(engineered)
    anomaly = STATE.ensemble.score_one(X)
    return engineered, X, anomaly


@app.post("/api/submit")
async def api_submit(
    request: Request,
    file: UploadFile = File(...),
    employee_id: Optional[str] = Form(None),
    override_category: str = Form(""),
    override_amount: str = Form(""),
    override_vendor: str = Form(""),
    phone_number: str = Form(""),
    receipt_attached: str = Form("on"),
    pre_approval_attached: str = Form(""),
    is_per_diem: str = Form(""),
    is_business_trip: str = Form(""),
    is_team_meal: str = Form(""),
    attendee_list_attached: str = Form(""),
    justification_text: str = Form(""),
    # Supplementary attachments (optional). If the user ticks the corresponding
    # checkbox but doesn't upload the file, the boolean is silently coerced
    # to false — so the policy engine sees the claim as unattached and
    # rules like "pre_approval_attached: false" still fire.
    pre_approval_file: Optional[UploadFile] = File(None),
    attendee_list_file: Optional[UploadFile] = File(None),
):
    _load_once()
    user = get_current_user(request)

    # Identify the employee: prefer the logged-in employee, else form override.
    if user and user.get("role") == "employee":
        employee_id = user["employee_id"]
    if not employee_id:
        raise HTTPException(status_code=400, detail="employee_id required")

    with SessionLocal() as db:
        emp = db.get(Employee, employee_id)
        if not emp:
            raise HTTPException(status_code=400, detail=f"Unknown employee {employee_id}")
        department = emp.department
        grade = emp.grade
        company_id_resolved = emp.company_id

    raw = await file.read()
    notifications: list[dict] = []

    try:
        original = _image_bytes_to_np(raw)
    except HTTPException as e:
        if phone_number:
            res = send_reupload_call(phone_number, reason="Unreadable image upload")
            notifications.append({"trigger":"IMAGE_DECODE_FAILED","placed":res.placed,
                                  "sid":res.sid,"error":res.error})
        return JSONResponse({
            "error": "We couldn't open the file as an image. Please upload a JPG or PNG.",
            "notifications": notifications,
        }, status_code=e.status_code)

    cleaned = preprocess_image(original)
    _, buf = cv2.imencode(".png", cleaned)
    cleaned_bytes = buf.tobytes()

    try:
        ocr = ocr_receipt(cleaned_bytes, mime_type="image/png")
        ocr_dict = {"vendor":ocr.vendor,"date":ocr.date,"amount":ocr.amount,
                    "tax":ocr.tax,"currency":ocr.currency,"category":ocr.category,
                    "line_items":ocr.line_items,"confidence":ocr.confidence}
        ocr_error = None
    except OcrUnavailable as e:
        ocr_dict, ocr_error = None, str(e)
    except Exception as e:  # noqa
        ocr_dict, ocr_error = None, f"OCR failed: {type(e).__name__}: {e}"

    if (phone_number and ocr_dict is not None
            and (ocr_dict.get("confidence") or 0) < _OCR_CONF_THRESHOLD):
        conf = ocr_dict.get("confidence") or 0
        res = send_reupload_call(
            phone_number,
            reason=f"OCR confidence only {int(conf*100)}%, likely a blurry photo",
        )
        notifications.append({"trigger":"OCR_LOW_CONFIDENCE","confidence":conf,
                              "threshold":_OCR_CONF_THRESHOLD,
                              "placed":res.placed,"sid":res.sid,"error":res.error})

    # Stitch the final claim
    vendor   = (override_vendor or (ocr_dict or {}).get("vendor") or "Unknown")
    category = (override_category or (ocr_dict or {}).get("category") or "Other")
    amount_str = override_amount.strip()
    if amount_str:
        try: amount = float(amount_str)
        except ValueError:
            return JSONResponse({"error": "Invalid amount override"}, status_code=400)
    elif ocr_dict and ocr_dict.get("amount") is not None:
        amount = float(ocr_dict["amount"])
    else:
        return JSONResponse({
            "error": "We couldn't read a total amount. Please enter it below and try again.",
            "ocr_error": ocr_error,
            "original_png_base64": _encode_png_base64(original),
            "preprocessed_png_base64": _encode_png_base64(cleaned),
        }, status_code=400)

    claim_id = f"CLM_{uuid.uuid4().hex[:10].upper()}"
    claim = {
        "claim_id": claim_id, "employee_id": employee_id,
        "department": department, "grade": grade,
        "vendor": vendor, "category": category, "amount": amount,
        "submitted_at": datetime.now().isoformat(),
    }

    rules = STATE.companies.get(company_id_resolved)

    # Read attachment files (if provided) and coerce checkbox → actual-attached.
    # If the checkbox was ticked without a file, we set the boolean to FALSE
    # so the policy engine never treats unvalidated self-declarations as
    # attached. The UI surfaces a note about this so users see their own click
    # didn't count.
    attachments: dict[str, dict] = {}
    coercion_notes: list[str] = []

    async def _read_attachment(upload: Optional[UploadFile], key: str, label: str, checked: bool) -> bool:
        """Return the final boolean after verifying the file is really attached."""
        has_file = upload is not None and upload.filename
        if has_file:
            data = await upload.read()
            if data:
                attachments[key] = {
                    "name": upload.filename,
                    "mime": upload.content_type or "application/octet-stream",
                    "size": len(data),
                    "data_b64": base64.b64encode(data).decode("ascii"),
                }
                return True  # file present → flag stays/becomes true
        if checked and not has_file:
            coercion_notes.append(
                f"You ticked '{label}' but didn't upload the file — treating as not attached."
            )
        return False

    pre_approval_bool  = await _read_attachment(pre_approval_file, "pre_approval",
                                                "Pre-approval attached", _truthy(pre_approval_attached))
    attendee_list_bool = await _read_attachment(attendee_list_file, "attendee_list",
                                                "Attendee list attached", _truthy(attendee_list_attached))

    # --- Validate each attachment with Gemini. An employee can still
    # upload a cat photo for pre-approval; the validator classifies each
    # file and we coerce the flag to False if Gemini is confident it's
    # NOT actually the document the user claimed it was.
    validation_tasks = [
        validate_attachment(
            k,
            base64.b64decode(att["data_b64"]),
            att.get("mime") or "application/octet-stream",
        )
        for k, att in attachments.items()
    ]
    validation_results = (
        await asyncio.gather(*validation_tasks) if validation_tasks else []
    )
    for (k, att), v in zip(list(attachments.items()), validation_results):
        att["validation"] = v
        # Coerce only when Gemini is reasonably confident the file isn't what
        # was claimed. Unknown / low-confidence verdicts don't penalise the user.
        CONF_THRESHOLD = 0.6
        if v.get("appears_valid") is False and v.get("confidence", 0) >= CONF_THRESHOLD:
            label = "Pre-approval" if k == "pre_approval" else "Attendee list"
            coercion_notes.append(
                f"The file you uploaded for '{label}' doesn't look like a valid "
                f"{_KIND_LABEL_SHORT.get(k, k)} ({v.get('reason','')}). "
                "Treating as not attached."
            )
            if k == "pre_approval":
                pre_approval_bool = False
            elif k == "attendee_list":
                attendee_list_bool = False

    drools_meta = {
        "claim_id": claim_id, "employee_id": employee_id,
        "department": department, "grade": grade,
        "override_amount": str(amount), "override_vendor": vendor,
        "override_category": category, "submitted_at": claim["submitted_at"],
        "receipt_attached":       _truthy(receipt_attached),
        "pre_approval_attached":  pre_approval_bool,
        "is_per_diem":            _truthy(is_per_diem),
        "is_business_trip":       _truthy(is_business_trip),
        "is_team_meal":           _truthy(is_team_meal),
        "attendee_list_attached": attendee_list_bool,
        "justification_text":     justification_text,
    }

    # Parallel: Drools + ML
    policy_task = asyncio.create_task(call_policy_engine(ocr_dict, drools_meta))
    ml_task = asyncio.create_task(asyncio.to_thread(_run_ml_inference, claim))
    policy_json, (engineered, X, anomaly) = await asyncio.gather(policy_task, ml_task)

    combined = float(anomaly.get("combined_anomaly_score", 0.0))
    anomaly["anomaly_label"] = classify(combined, rules)

    feature_row = {k: (v.item() if hasattr(v, "item") else v)
                   for k, v in engineered.iloc[0].to_dict().items()}

    reasons = generate_reasons(claim=claim, engineered=feature_row,
                               anomaly=anomaly, rules=rules,
                               claim_flags=drools_meta)
    anomaly["anomaly_label"] = final_verdict(combined, reasons, rules)

    decision = make_decision(policy_json, anomaly)

    # PERSIST
    persist_submission(
        claim_id=claim_id, employee_id=employee_id, company_id=company_id_resolved,
        claim_dict=claim, ocr_dict=ocr_dict, engineered_features=feature_row,
        policy_result=policy_json, anomaly_result=anomaly, decision=decision,
        reasons=reasons,
        original_png_base64=_encode_png_base64(original),
        preprocessed_png_base64=_encode_png_base64(cleaned),
        metadata_flags=drools_meta,
        attachments=attachments,
    )

    # Surface the "ticked-but-no-file" coercions as toast-style warnings
    # so the user sees their checkbox didn't count.
    for note in coercion_notes:
        notifications.append({
            "trigger": "ATTACHMENT_COERCED",
            "placed": False,
            "error": note,
        })

    return JSONResponse({
        "original_png_base64":    _encode_png_base64(original),
        "preprocessed_png_base64": _encode_png_base64(cleaned),
        "ocr": ocr_dict, "ocr_error": ocr_error,
        "claim": claim,
        "company": {"id": rules.id, "name": rules.name, "description": rules.description},
        "engineered_features": feature_row,
        "transformed_shape": list(X.shape),
        "anomaly_result": anomaly,
        "policy_result": policy_json,
        "decision": decision,
        "reasons": reasons,
        "notifications": notifications,
        "coercion_notes": coercion_notes,
        "attached": {
            "receipt":       True,
            # Reflect what the POLICY engine saw — so if validation coerced
            # a flag to false, this matches. File-on-disk is a separate
            # concept visible in the attachments section of claim detail.
            "pre_approval":  drools_meta["pre_approval_attached"],
            "attendee_list": drools_meta["attendee_list_attached"],
        },
    })


# Mount static last so routes above win.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
