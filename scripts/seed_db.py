"""Create tables and seed the demo database from existing JSON + CSV files.

Run once to bootstrap. Idempotent — drops + recreates every table each
time (the demo doesn't need preserved history). Swap DROP/CREATE for
Alembic migrations when moving to prod.

Sources:
    data/companies/*.json        → companies table + company_rules
    data/synthetic/claims.csv    → employees + historical claims + verdicts

Everything the UI shows (employee dropdown, manager review queue, claims
history, analytics) is hydrated from the DB after this runs.
"""
from __future__ import annotations

import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from backend.db import Base, engine, SessionLocal  # noqa: E402
from backend.models_db import (  # noqa: E402
    AuditLog, Claim, Company, Employee, User, Verdict,
)

COMPANIES_DIR = ROOT / "data" / "companies"
CLAIMS_CSV = ROOT / "data" / "synthetic" / "claims.csv"


def _company_for_emp(eid: str, default: str = "ACME", split: int = 50) -> str:
    """EMP00000–49 → ACME, EMP00050+ → GLOBEX (matches employee_map.json)."""
    m = re.search(r"(\d+)$", eid)
    if not m:
        return default
    return default if int(m.group(1)) < split else "GLOBEX"


def reset_schema() -> None:
    print("Dropping + recreating tables...")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def seed_companies(db) -> dict[str, Company]:
    print("Seeding companies...")
    companies = {}
    for path in sorted(COMPANIES_DIR.glob("*.json")):
        if path.name == "employee_map.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        c = Company(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            rules_json=json.dumps(data),
        )
        db.add(c)
        companies[c.id] = c
    db.flush()
    print(f"  {len(companies)} companies: {list(companies.keys())}")
    return companies


def seed_employees(db, companies: dict[str, Company]) -> dict[str, Employee]:
    print("Seeding employees...")
    df = pd.read_csv(CLAIMS_CSV)
    emp_df = df[["employee_id", "department", "grade"]].drop_duplicates("employee_id")

    rng = random.Random(42)
    first_names = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh",
                   "Krishna", "Ishaan", "Rohan", "Anaya", "Diya", "Saanvi", "Ananya",
                   "Pari", "Aadya", "Avni", "Myra", "Siya", "Aarohi"]
    last_names = ["Sharma", "Patel", "Kumar", "Singh", "Mehta", "Iyer", "Reddy",
                  "Nair", "Gupta", "Shah", "Kulkarni", "Desai", "Rao", "Joshi"]

    employees = {}
    for _, row in emp_df.iterrows():
        eid = row["employee_id"]
        company_id = _company_for_emp(eid)
        if company_id not in companies:
            continue
        e = Employee(
            id=eid,
            name=f"{rng.choice(first_names)} {rng.choice(last_names)}",
            department=row["department"],
            grade=row["grade"],
            company_id=company_id,
        )
        db.add(e)
        employees[eid] = e
    db.flush()
    print(f"  {len(employees)} employees across {len(companies)} companies")
    return employees


def seed_fake_users(db, companies: dict[str, Company]) -> None:
    """Three canonical demo users per company — employee, manager, admin.
    No passwords; the UI role-picker drives which one is 'logged in'."""
    print("Seeding demo users...")
    for comp_id in companies:
        for role in ["employee", "manager", "admin"]:
            db.add(User(
                email=f"{role}@{comp_id.lower()}.demo",
                role=role,
                company_id=comp_id,
            ))
    db.flush()


def seed_historical_claims(db, companies: dict[str, Company],
                           employees: dict[str, Employee]) -> None:
    """Import the synthetic claims.csv as past submitted claims so the
    manager queue and employee 'my claims' view have something to show."""
    print("Seeding historical claims + verdicts...")
    df = pd.read_csv(CLAIMS_CSV)
    df["submitted_at"] = pd.to_datetime(df["submitted_at"], errors="coerce")
    df = df.dropna(subset=["submitted_at"])

    # Remap the original 2024-2025 dates into the LAST 180 days so that
    # admin-dashboard + analytics windows (30/90/365 days) see data.
    # Keeps the relative ordering of submissions intact.
    from datetime import datetime, timedelta
    end = datetime.now()
    start_window = end - timedelta(days=180)
    df = df.sort_values("submitted_at").reset_index(drop=True)
    orig_min, orig_max = df["submitted_at"].min(), df["submitted_at"].max()
    span_days = (orig_max - orig_min).days or 1
    window_days = (end - start_window).days
    df["submitted_at"] = df["submitted_at"].apply(
        lambda t: start_window + timedelta(
            days=(t - orig_min).total_seconds() / 86400.0 * window_days / span_days
        )
    )

    rng = random.Random(2024)
    n_claims = 0
    n_verdicts = 0

    for _, row in df.iterrows():
        eid = row["employee_id"]
        emp = employees.get(eid)
        if emp is None:
            continue

        # Use the fraud label to synthesise a plausible verdict for past claims.
        # This gives the manager queue + analytics charts real-looking data
        # without having to re-run the whole pipeline on thousands of rows.
        is_fraud = int(row.get("is_fraud", 0))
        if is_fraud:
            final_status = rng.choice(["REJECTED", "FRAUDULENT", "SUSPICIOUS"])
        else:
            final_status = rng.choices(
                ["VALID", "SUSPICIOUS"], weights=[0.9, 0.1]
            )[0]
        action = {
            "VALID":      "AUTO_APPROVE",
            "SUSPICIOUS": "MANAGER_REVIEW",
            "REJECTED":   "AUTO_REJECT",
            "FRAUDULENT": "MANUAL_REVIEW",
        }[final_status]

        claim_id = f"CLM_SEED_{n_claims:07d}"
        claim = Claim(
            id=claim_id,
            employee_id=eid,
            company_id=emp.company_id,
            vendor=row.get("vendor") or None,
            category=row.get("category") or "Other",
            amount=float(row.get("amount") or 0),
            currency="INR",
            submitted_at=row["submitted_at"].to_pydatetime(),
            receipt_attached=True,
        )
        db.add(claim)

        verdict = Verdict(
            claim_id=claim_id,
            final_status=final_status,
            action=action,
            final_score=round(rng.random() if is_fraud else rng.random() * 0.3, 3),
            policy_status={"VALID": "APPROVED", "SUSPICIOUS": "FLAGGED",
                           "REJECTED": "REJECTED", "FRAUDULENT": "FLAGGED"}[final_status],
            policy_score=100 if final_status == "VALID" else rng.randint(0, 70),
            ml_label={"VALID": "NORMAL", "SUSPICIOUS": "SUSPICIOUS",
                      "REJECTED": "ANOMALOUS", "FRAUDULENT": "ANOMALOUS"}[final_status],
            ml_combined_score=round(
                (rng.random() * 0.4 + 0.5) if is_fraud else rng.random() * 0.3, 3
            ),
        )
        db.add(verdict)

        db.add(AuditLog(
            claim_id=claim_id,
            actor="system",
            event="SUBMITTED",
            detail=f"seeded from synthetic dataset; fraud_type={row.get('fraud_type') or 'none'}",
            created_at=row["submitted_at"].to_pydatetime(),
        ))

        n_claims += 1
        n_verdicts += 1
        if n_claims % 2000 == 0:
            db.flush()
            print(f"    ...{n_claims} rows")

    db.flush()
    print(f"  {n_claims} claims + {n_verdicts} verdicts seeded")


def main() -> int:
    reset_schema()
    with SessionLocal() as db:
        companies = seed_companies(db)
        employees = seed_employees(db, companies)
        seed_fake_users(db, companies)
        seed_historical_claims(db, companies, employees)
        db.commit()
    print("\n[OK] Database seeded. File size:", end=" ")
    p = Path(ROOT / "data" / "app.db")
    if p.exists():
        print(f"{p.stat().st_size / (1024*1024):.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
