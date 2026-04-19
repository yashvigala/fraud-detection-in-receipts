"""Generate a synthetic expense-claims dataset with fraud labels.

SROIE gives you real receipt text but has no 'fraud / not fraud' labels.
For training Isolation Forest and the Autoencoder you need a labelled
corpus. We synthesise one that mirrors the structure of real corporate
claims and injects realistic fraud patterns:

    * Amount inflation   — claim amount 5-15x the employee's usual spend.
    * Duplicate receipts — same vendor + same amount within 3 days.
    * Off-hours submit   — late-night or weekend submissions.
    * Category mismatch  — Entertainment claimed by a non-sales employee.
    * Round-number bias  — suspiciously round amounts (5000, 10000).

These are the exact patterns the Drools policy engine and the anomaly
models are meant to catch, so synthetic fraud gives us a ground-truth
label to evaluate precision/recall against.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

# Reproducibility
_FAKER = Faker("en_IN")
Faker.seed(42)

CATEGORIES = [
    "Food",
    "Travel",
    "Lodging",
    "Entertainment",
    "Office Supplies",
    "Fuel",
    "Client Meals",
    "Training",
]

DEPARTMENTS = ["Engineering", "Sales", "HR", "Finance", "Operations", "Marketing"]
GRADES = ["Junior", "Mid", "Senior", "Director"]

# Per-category plausible amount ranges in INR. Mean, std.
CATEGORY_STATS: dict[str, tuple[float, float]] = {
    "Food": (350, 150),
    "Travel": (1200, 600),
    "Lodging": (3500, 1500),
    "Entertainment": (2000, 1000),
    "Office Supplies": (800, 400),
    "Fuel": (1500, 500),
    "Client Meals": (2500, 1000),
    "Training": (5000, 2000),
}

# Categories that only certain departments should be claiming.
CATEGORY_RESTRICTIONS = {
    "Entertainment": {"Sales", "Marketing"},
    "Client Meals": {"Sales", "Marketing"},
}

GRADE_DAILY_LIMIT = {
    "Junior": 1500,
    "Mid": 3000,
    "Senior": 6000,
    "Director": 15000,
}


@dataclass
class Employee:
    employee_id: str
    name: str
    department: str
    grade: str


def _make_employees(n: int, rng: random.Random) -> list[Employee]:
    return [
        Employee(
            employee_id=f"EMP{str(i).zfill(5)}",
            name=_FAKER.name(),
            department=rng.choice(DEPARTMENTS),
            grade=rng.choice(GRADES),
        )
        for i in range(n)
    ]


def _random_datetime(start: datetime, end: datetime, rng: random.Random) -> datetime:
    delta = end - start
    offset = rng.randint(0, int(delta.total_seconds()))
    return start + timedelta(seconds=offset)


def _normal_claim(
    employee: Employee,
    rng: random.Random,
    start: datetime,
    end: datetime,
    vendor_pool: list[str],
) -> dict:
    """A plausible, policy-abiding claim."""
    # Keep category consistent with department restrictions most of the time.
    allowed_categories = [
        c for c in CATEGORIES
        if c not in CATEGORY_RESTRICTIONS
        or employee.department in CATEGORY_RESTRICTIONS[c]
    ]
    category = rng.choice(allowed_categories)
    mean, std = CATEGORY_STATS[category]
    amount = max(50.0, rng.gauss(mean, std))

    # Cap by grade so normal claims respect the spending limit.
    amount = min(amount, GRADE_DAILY_LIMIT[employee.grade])

    submitted = _random_datetime(start, end, rng)
    # Normal claims mostly submitted on weekdays during business hours.
    if submitted.weekday() >= 5 or submitted.hour < 8 or submitted.hour > 20:
        submitted = submitted.replace(
            hour=rng.randint(9, 18),
            minute=rng.randint(0, 59),
        )
        # Shift to a weekday if needed.
        while submitted.weekday() >= 5:
            submitted -= timedelta(days=1)

    return {
        "claim_id": None,  # filled in later
        "employee_id": employee.employee_id,
        "department": employee.department,
        "grade": employee.grade,
        "vendor": rng.choice(vendor_pool),
        "category": category,
        "amount": round(amount, 2),
        "submitted_at": submitted,
        "is_fraud": 0,
        "fraud_type": None,
    }


def _inject_fraud(claim: dict, rng: random.Random) -> dict:
    """Mutate a normal claim into one of several fraud patterns."""
    fraud_types = [
        "amount_inflation",
        "off_hours",
        "round_number",
        "category_mismatch",
        "limit_breach",
    ]
    fraud = rng.choice(fraud_types)

    if fraud == "amount_inflation":
        claim["amount"] = round(claim["amount"] * rng.uniform(5, 15), 2)
    elif fraud == "off_hours":
        ts: datetime = claim["submitted_at"]
        claim["submitted_at"] = ts.replace(
            hour=rng.choice([1, 2, 3, 23]),
            minute=rng.randint(0, 59),
        )
    elif fraud == "round_number":
        claim["amount"] = float(rng.choice([5000, 10000, 15000, 20000, 25000]))
    elif fraud == "category_mismatch":
        # A department claims a restricted category it shouldn't.
        restricted = list(CATEGORY_RESTRICTIONS.keys())
        claim["category"] = rng.choice(restricted)
    elif fraud == "limit_breach":
        claim["amount"] = round(
            GRADE_DAILY_LIMIT[claim["grade"]] * rng.uniform(1.5, 3.0),
            2,
        )

    claim["is_fraud"] = 1
    claim["fraud_type"] = fraud
    return claim


def _inject_duplicates(df: pd.DataFrame, n_duplicates: int, rng: random.Random) -> pd.DataFrame:
    """Clone a handful of rows with a time jitter < 3 days — the
    Drools duplicate-detection rule's exact signature."""
    if n_duplicates == 0 or df.empty:
        return df

    picks = df.sample(n=min(n_duplicates, len(df)), random_state=rng.randint(0, 10_000))
    dupes = picks.copy()
    dupes["submitted_at"] = dupes["submitted_at"].apply(
        lambda ts: ts + timedelta(hours=rng.randint(1, 48))
    )
    dupes["is_fraud"] = 1
    dupes["fraud_type"] = "duplicate"
    return pd.concat([df, dupes], ignore_index=True)


def generate_claims(
    n_normal: int = 5000,
    n_fraud: int = 500,
    n_employees: int = 100,
    n_vendors: int = 80,
    start_date: str = "2024-01-01",
    end_date: str = "2025-12-31",
    seed: int = 42,
) -> pd.DataFrame:
    """Generate a synthetic claims DataFrame with ground-truth fraud labels.

    Returns
    -------
    pd.DataFrame with columns:
        claim_id, employee_id, department, grade, vendor, category,
        amount, submitted_at, is_fraud, fraud_type
    """
    rng = random.Random(seed)
    np.random.seed(seed)

    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)

    employees = _make_employees(n_employees, rng)
    vendor_pool = [_FAKER.company() for _ in range(n_vendors)]

    rows: list[dict] = []
    for _ in range(n_normal):
        emp = rng.choice(employees)
        rows.append(_normal_claim(emp, rng, start, end, vendor_pool))

    # Produce slightly fewer outright fraudulent rows because duplicates
    # are appended below and also count as fraud.
    n_outright = max(0, n_fraud - n_fraud // 5)
    for _ in range(n_outright):
        emp = rng.choice(employees)
        base = _normal_claim(emp, rng, start, end, vendor_pool)
        rows.append(_inject_fraud(base, rng))

    df = pd.DataFrame(rows)
    df = _inject_duplicates(df, n_fraud // 5, rng)

    # Shuffle so fraud isn't clustered at the end.
    df = df.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    df["claim_id"] = [f"CLM_{str(i).zfill(6)}" for i in range(len(df))]
    # Move claim_id to the front.
    cols = ["claim_id"] + [c for c in df.columns if c != "claim_id"]
    df = df[cols]
    return df


def save_claims(df: pd.DataFrame, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    return out_path
