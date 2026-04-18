"""
DB schema and CRUD for ad generation jobs and their variants.

Tables:
  ad_generation_jobs     — one row per (user, campaign, adset, seed image) run
  ad_generation_variants — one row per generated image (one per suggestion)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).parent.parent / "app.db"

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_JOBS = """
CREATE TABLE IF NOT EXISTS ad_generation_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    campaign_id     TEXT NOT NULL,
    adset_id        TEXT NOT NULL,
    seed_ad_id      TEXT,
    seed_image_url  TEXT NOT NULL,
    headline        TEXT NOT NULL,
    short_text      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    suggestions     TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_CREATE_VARIANTS = """
CREATE TABLE IF NOT EXISTS ad_generation_variants (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id            INTEGER NOT NULL REFERENCES ad_generation_jobs(id),
    suggestion        TEXT NOT NULL,
    deapi_request_id  TEXT,
    status            TEXT NOT NULL DEFAULT 'submitted',
    result_url        TEXT,
    local_filename    TEXT,
    score             REAL,
    severity          TEXT,
    score_labels      TEXT,
    qa_status         TEXT,
    qa_corrections    TEXT,
    parent_variant_id INTEGER REFERENCES ad_generation_variants(id),
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_VARIANT_MIGRATIONS = [
    "ALTER TABLE ad_generation_variants ADD COLUMN qa_status TEXT",
    "ALTER TABLE ad_generation_variants ADD COLUMN qa_corrections TEXT",
    "ALTER TABLE ad_generation_variants ADD COLUMN parent_variant_id INTEGER REFERENCES ad_generation_variants(id)",
]


def _conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def ensure_tables(db_path: Path = DB_PATH) -> None:
    c = _conn(db_path)
    c.execute(_CREATE_JOBS)
    c.execute(_CREATE_VARIANTS)
    for stmt in _VARIANT_MIGRATIONS:
        try:
            c.execute(stmt)
        except Exception:
            pass  # column already exists
    c.commit()
    c.close()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def create_job(
    user_id: int,
    campaign_id: str,
    adset_id: str,
    seed_image_url: str,
    headline: str,
    short_text: str,
    seed_ad_id: str | None = None,
    db_path: Path = DB_PATH,
) -> int:
    ensure_tables(db_path)
    c = _conn(db_path)
    cur = c.execute(
        """
        INSERT INTO ad_generation_jobs
            (user_id, campaign_id, adset_id, seed_ad_id, seed_image_url, headline, short_text)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, campaign_id, adset_id, seed_ad_id, seed_image_url, headline, short_text),
    )
    job_id = cur.lastrowid
    c.commit()
    c.close()
    return job_id


def update_job(job_id: int, db_path: Path = DB_PATH, **fields: Any) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    c = _conn(db_path)
    c.execute(
        f"UPDATE ad_generation_jobs SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
        values,
    )
    c.commit()
    c.close()


def get_job(job_id: int, db_path: Path = DB_PATH) -> dict | None:
    c = _conn(db_path)
    row = c.execute("SELECT * FROM ad_generation_jobs WHERE id = ?", (job_id,)).fetchone()
    c.close()
    if row is None:
        return None
    d = dict(row)
    if d.get("suggestions"):
        d["suggestions"] = json.loads(d["suggestions"])
    return d


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------

def create_variant(
    job_id: int,
    suggestion: str,
    parent_variant_id: int | None = None,
    db_path: Path = DB_PATH,
) -> int:
    c = _conn(db_path)
    cur = c.execute(
        "INSERT INTO ad_generation_variants (job_id, suggestion, parent_variant_id) VALUES (?, ?, ?)",
        (job_id, suggestion, parent_variant_id),
    )
    vid = cur.lastrowid
    c.commit()
    c.close()
    return vid


def update_variant(variant_id: int, db_path: Path = DB_PATH, **fields: Any) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [variant_id]
    c = _conn(db_path)
    c.execute(
        f"UPDATE ad_generation_variants SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
        values,
    )
    c.commit()
    c.close()


def get_variants(job_id: int, db_path: Path = DB_PATH) -> list[dict]:
    c = _conn(db_path)
    rows = c.execute(
        "SELECT * FROM ad_generation_variants WHERE job_id = ? ORDER BY id",
        (job_id,),
    ).fetchall()
    c.close()
    result = []
    for row in rows:
        d = dict(row)
        if d.get("score_labels"):
            d["score_labels"] = json.loads(d["score_labels"])
        if d.get("qa_corrections"):
            d["qa_corrections"] = json.loads(d["qa_corrections"])
        result.append(d)
    return result
