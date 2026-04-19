"""Multi-tenant company ruleset storage.

Each company's rules live in a single JSON file under ``data/companies/``.
This mirrors the project spec's "Rules live in .drl files loaded at startup
— no code changes needed to update a spending limit" — with the difference
that we store JSON rather than Drools DRL syntax (a full Drools integration
is the next step but out of scope for the FYP demo).

Employee-to-company assignment is driven by ``employee_map.json``. For the
synthetic dataset the simple rule is: employees numbered below the split
belong to the default company, the rest to the other company.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field, field
from pathlib import Path
from typing import Any

_COMPANIES_DIR = Path(__file__).resolve().parent.parent / "data" / "companies"
_MAP_FILE = _COMPANIES_DIR / "employee_map.json"


@dataclass
class CompanyRules:
    id: str
    name: str
    description: str
    grade_daily_limit: dict[str, float]
    category_daily_limit: dict[str, float]
    category_restrictions: dict[str, list[str]]
    round_number_threshold: float
    suspicious_threshold: float
    anomalous_threshold: float
    # Free-form list of per-company rules authored in JSON (see
    # backend/custom_rules.py for the schema). Always a list.
    custom_rules: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "grade_daily_limit": self.grade_daily_limit,
            "category_daily_limit": self.category_daily_limit,
            "category_restrictions": self.category_restrictions,
            "round_number_threshold": self.round_number_threshold,
            "suspicious_threshold": self.suspicious_threshold,
            "anomalous_threshold": self.anomalous_threshold,
            "custom_rules": self.custom_rules,
        }


class CompanyStore:
    """Thread-safe store of per-company rulesets, backed by the DB.

    On construction, loads every row from the ``companies`` table into an
    in-memory cache (same as the old JSON loader). Writes go to the DB
    and refresh the cache. The employee → company mapping used to come
    from a JSON file; now it comes from the ``employees.company_id``
    column.
    """

    def __init__(self, root: Path = _COMPANIES_DIR) -> None:
        # ``root`` kept for backward-compat; no longer used.
        self._root = root
        self._lock = threading.RLock()
        self._companies: dict[str, CompanyRules] = {}
        self._employee_company: dict[str, str] = {}
        self._reload()

    # --- persistence -----------------------------------------------------
    def _reload(self) -> None:
        # Late import so importing backend.companies doesn't require the DB
        # layer at module-load time (useful for scripts like generate_synthetic).
        from .db import SessionLocal
        from .models_db import Company, Employee

        with self._lock, SessionLocal() as db:
            self._companies.clear()
            self._employee_company.clear()
            for c in db.query(Company).all():
                data = json.loads(c.rules_json) if c.rules_json else {}
                # Rules JSON holds everything we need; fall back to table
                # columns for name/description if missing.
                data.setdefault("id", c.id)
                data.setdefault("name", c.name)
                data.setdefault("description", c.description or "")
                data.setdefault("custom_rules", [])
                # Drop any keys CompanyRules doesn't know about (forward-compat)
                allowed = {
                    "id","name","description",
                    "grade_daily_limit","category_daily_limit","category_restrictions",
                    "round_number_threshold","suspicious_threshold","anomalous_threshold",
                    "custom_rules",
                }
                data = {k: v for k, v in data.items() if k in allowed}
                self._companies[c.id] = CompanyRules(**data)
            for e in db.query(Employee.id, Employee.company_id).all():
                self._employee_company[e.id] = e.company_id

    def _save_company(self, c: CompanyRules) -> None:
        from .db import SessionLocal
        from .models_db import Company

        with SessionLocal() as db:
            row = db.get(Company, c.id)
            if row is None:
                row = Company(id=c.id, name=c.name, description=c.description)
                db.add(row)
            row.name = c.name
            row.description = c.description
            row.rules_json = json.dumps(c.as_dict())
            db.commit()

    # --- public API ------------------------------------------------------
    def list_companies(self) -> list[dict[str, str]]:
        with self._lock:
            return [
                {"id": c.id, "name": c.name, "description": c.description}
                for c in self._companies.values()
            ]

    def get(self, company_id: str) -> CompanyRules:
        with self._lock:
            if company_id not in self._companies:
                raise KeyError(company_id)
            return self._companies[company_id]

    def update_rules(self, company_id: str, incoming: dict[str, Any]) -> CompanyRules:
        """Replace the ruleset for ``company_id``. Validates required keys
        and writes to disk. Returns the updated rules."""
        with self._lock:
            existing = self._companies.get(company_id)
            if existing is None:
                raise KeyError(company_id)

            merged = existing.as_dict()
            # Only overwrite keys the client actually sent — safer than
            # forcing the client to resend the whole document.
            for key in (
                "name", "description",
                "grade_daily_limit", "category_daily_limit",
                "category_restrictions",
                "round_number_threshold",
                "suspicious_threshold", "anomalous_threshold",
                "custom_rules",
            ):
                if key in incoming:
                    merged[key] = incoming[key]

            _validate_rules(merged)
            updated = CompanyRules(**merged)
            self._save_company(updated)
            self._companies[company_id] = updated
            return updated

    # --- employee <-> company mapping -----------------------------------
    def company_for_employee(self, employee_id: str) -> str:
        # Prefer the DB-backed lookup (populated in _reload()).
        if employee_id in self._employee_company:
            return self._employee_company[employee_id]
        # Legacy fallback — the old employee_map.json rule.
        cfg = getattr(self, "_employee_map_cfg", {}) or {}
        default = cfg.get("default_company", next(iter(self._companies), "ACME"))
        rule = cfg.get("rule", "numeric_half")
        if rule == "numeric_half":
            m = re.search(r"(\d+)$", employee_id)
            if not m:
                return default
            idx = int(m.group(1))
            split = int(cfg.get("split_at", 50))
            if idx < split:
                return default
            # Pick any non-default as the 'other' bucket.
            others = [cid for cid in self._companies if cid != default]
            return others[0] if others else default
        return default


def _validate_rules(d: dict[str, Any]) -> None:
    """Raise ValueError if rules look malformed."""
    required = {
        "grade_daily_limit", "category_daily_limit", "category_restrictions",
        "round_number_threshold", "suspicious_threshold", "anomalous_threshold",
    }
    missing = required - set(d.keys())
    if missing:
        raise ValueError(f"Rules missing required keys: {sorted(missing)}")

    for grade, limit in d["grade_daily_limit"].items():
        if not isinstance(limit, (int, float)) or limit < 0:
            raise ValueError(f"grade_daily_limit[{grade!r}] must be a non-negative number")
    for cat, limit in d["category_daily_limit"].items():
        if not isinstance(limit, (int, float)) or limit < 0:
            raise ValueError(f"category_daily_limit[{cat!r}] must be a non-negative number")
    for cat, depts in d["category_restrictions"].items():
        if not isinstance(depts, list) or not all(isinstance(x, str) for x in depts):
            raise ValueError(f"category_restrictions[{cat!r}] must be a list of strings")
    for key in ("suspicious_threshold", "anomalous_threshold"):
        v = d[key]
        if not isinstance(v, (int, float)) or not (0.0 <= v <= 1.0):
            raise ValueError(f"{key} must be between 0 and 1")
    if d["suspicious_threshold"] >= d["anomalous_threshold"]:
        raise ValueError("suspicious_threshold must be strictly less than anomalous_threshold")

    # Custom rules: optional; if present, each must validate.
    from .custom_rules import validate_rule
    crs = d.get("custom_rules") or []
    if not isinstance(crs, list):
        raise ValueError("custom_rules must be a list")
    for i, r in enumerate(crs):
        ok, err = validate_rule(r)
        if not ok:
            raise ValueError(f"custom_rules[{i}] ({r.get('id','?')}): {err}")
    # Ensure rule IDs are unique.
    ids = [r.get("id") for r in crs if isinstance(r, dict)]
    if len(ids) != len(set(ids)):
        dupes = [i for i in ids if ids.count(i) > 1]
        raise ValueError(f"Duplicate custom_rules IDs: {sorted(set(dupes))}")
