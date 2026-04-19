"""Online feature computation for a single new claim.

At training time, feature engineering is computed over the full synthetic
dataset in one pandas pass. At inference time, we need to compute the same
features for ONE new incoming claim against the historical record.

In production, 'history' lives in the PostgreSQL `claims` table. For this
demo, history = the synthetic claims CSV. Same contract either way.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


class FeatureStore:
    """In-memory feature store, hydrated from the DB at construction.

    Reads every claim from the ``claims`` table + every employee from the
    ``employees`` table into a pandas DataFrame on startup, then builds
    the same aggregate indexes the CSV-backed version used. The public
    interface (``known_employees``, ``engineer_online``, etc.) is
    unchanged — callers don't notice the swap.

    ``claims_csv`` is kept as a fallback so existing tests / scripts that
    pass a CSV path continue to work when the DB isn't seeded.
    """

    def __init__(self, claims_csv: str | Path | None = None):
        df = self._load_from_db()
        if df is None or df.empty:
            # Fallback for environments where the DB hasn't been seeded
            # (e.g. unit tests). Keeps the CSV code path alive.
            if claims_csv is None:
                raise RuntimeError(
                    "FeatureStore: no rows in DB and no claims_csv fallback given. "
                    "Run `python scripts/seed_db.py` first."
                )
            df = pd.read_csv(claims_csv)
            df["submitted_at"] = pd.to_datetime(df["submitted_at"], errors="coerce")
            df = df.dropna(subset=["submitted_at"])

        self._claims = df

        # Precompute per-category and per-employee means.
        self._category_mean = df.groupby("category")["amount"].mean().to_dict()
        self._employee_mean = df.groupby("employee_id")["amount"].mean().to_dict()
        self._vendor_counts = df["vendor"].value_counts().to_dict()
        self._global_mean = float(df["amount"].mean())

        # Index historical claims by (employee_id, vendor) for fast
        # duplicate-within-3-days queries.
        self._history_by_emp_vendor: dict[tuple, list[pd.Timestamp]] = {}
        for (emp, vendor), group in df.groupby(["employee_id", "vendor"]):
            self._history_by_emp_vendor[(emp, vendor)] = sorted(group["submitted_at"].tolist())

        self._last_claim_by_emp = (
            df.sort_values("submitted_at").groupby("employee_id")["submitted_at"].max().to_dict()
        )

    @staticmethod
    def _load_from_db() -> pd.DataFrame | None:
        """Pull employee + claim data out of the DB into a single DataFrame.
        Returns None if the DB isn't available (e.g. during seed)."""
        try:
            from .db import SessionLocal
            from .models_db import Claim, Employee
            from sqlalchemy import select
        except Exception:
            return None

        try:
            with SessionLocal() as db:
                # Check empty DB fast — no table scan if nothing to fetch.
                if not db.query(Claim.id).first():
                    return None
                rows = db.execute(
                    select(
                        Claim.id, Claim.employee_id, Claim.company_id,
                        Claim.vendor, Claim.category, Claim.amount,
                        Claim.submitted_at,
                        Employee.department, Employee.grade,
                    ).join(Employee, Claim.employee_id == Employee.id)
                ).all()
        except Exception:
            return None

        df = pd.DataFrame(rows, columns=[
            "claim_id", "employee_id", "company_id",
            "vendor", "category", "amount", "submitted_at",
            "department", "grade",
        ])
        if df.empty:
            return None
        df["submitted_at"] = pd.to_datetime(df["submitted_at"], errors="coerce")
        df = df.dropna(subset=["submitted_at"])
        return df

    def known_employees(self) -> list[dict]:
        emp_df = (
            self._claims[["employee_id", "department", "grade"]]
            .drop_duplicates("employee_id")
            .sort_values("employee_id")
        )
        return emp_df.to_dict(orient="records")

    def known_categories(self) -> list[str]:
        return sorted(self._claims["category"].dropna().unique().tolist())

    def known_vendors(self, limit: int = 50) -> list[str]:
        return [v for v, _ in sorted(self._vendor_counts.items(), key=lambda kv: -kv[1])][:limit]

    def engineer_online(
        self,
        claim: dict,
    ) -> pd.DataFrame:
        """Take a raw claim dict and return a one-row DataFrame with the
        exact feature columns the training preprocessor expects.

        ``claim`` must contain: employee_id, department, grade, vendor,
        category, amount, submitted_at (ISO string or datetime).
        """
        submitted_at = claim["submitted_at"]
        if isinstance(submitted_at, str):
            submitted_at = pd.to_datetime(submitted_at)
        amount = float(claim["amount"])
        employee_id = claim["employee_id"]
        vendor = claim["vendor"]
        category = claim["category"]

        # Behavioural / history features
        cat_mean = self._category_mean.get(category, self._global_mean)
        emp_mean = self._employee_mean.get(employee_id, self._global_mean)
        amount_vs_category_mean = amount / cat_mean if cat_mean else 1.0
        amount_vs_employee_mean = amount / emp_mean if emp_mean else 1.0

        last_ts = self._last_claim_by_emp.get(employee_id)
        if last_ts is None:
            days_since_last_claim = 9999.0
        else:
            delta = (submitted_at - last_ts).total_seconds() / 86400.0
            days_since_last_claim = max(0.0, min(9999.0, delta))

        vendor_frequency = int(self._vendor_counts.get(vendor, 0))

        # Count this employee's claims to this vendor in the last 3 days.
        history = self._history_by_emp_vendor.get((employee_id, vendor), [])
        cutoff = submitted_at - timedelta(days=3)
        vendor_repeat_count_3d = int(sum(1 for ts in history if ts >= cutoff))

        row = {
            "amount": amount,
            "amount_log": float(np.log1p(amount)),
            "amount_vs_category_mean": float(amount_vs_category_mean),
            "amount_vs_employee_mean": float(amount_vs_employee_mean),
            "day_of_week": int(submitted_at.dayofweek if hasattr(submitted_at, "dayofweek") else submitted_at.weekday()),
            "hour_of_day": int(submitted_at.hour),
            "is_weekend": int(submitted_at.weekday() >= 5),
            "is_off_hours": int(submitted_at.hour < 8 or submitted_at.hour > 20),
            "days_since_last_claim": float(days_since_last_claim),
            "vendor_frequency": vendor_frequency,
            "vendor_repeat_count_3d": vendor_repeat_count_3d,
            "category": category,
            "department": claim["department"],
            "grade": claim["grade"],
        }
        return pd.DataFrame([row])
