"""Database engine + session factory.

SQLAlchemy 2.0 style. Database URL is env-driven so swapping from SQLite
(dev) to PostgreSQL (prod) is just an environment variable change:

    export DATABASE_URL=postgresql+psycopg2://user:pw@host:5432/dbname

Default is a SQLite file at data/app.db — zero install, same ORM.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{_DEFAULT_DB_PATH.as_posix()}",
)

# SQLite needs ``check_same_thread=False`` because FastAPI worker threads
# share connections. Postgres doesn't need this kwarg, so we only apply it
# when the URL is SQLite. The rest of the engine config is identical for
# both backends — that's the point of SQLAlchemy.
_engine_kwargs: dict = {}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **_engine_kwargs, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                            expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    """Common SQLAlchemy declarative base for every model."""


def get_session():
    """FastAPI dependency-injection friendly session generator."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
