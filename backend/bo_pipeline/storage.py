"""
Persistence for BO run results.

Table: bo_selections — one row per recommended combination per BO run.
pick_rank 1 = EI pick, 2 = fantasy pick.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "app.db"

_CREATE = """
CREATE TABLE IF NOT EXISTS bo_selections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    seed_ad_id      TEXT NOT NULL,
    text_source_id  TEXT NOT NULL,
    pick_rank       INTEGER NOT NULL,
    combination_key TEXT NOT NULL,
    combination     TEXT NOT NULL,
    selection_type  TEXT NOT NULL,
    ei_score        REAL,
    gpr_mean        REAL,
    gpr_std         REAL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def _conn(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def ensure_table(db_path: Path = DB_PATH) -> None:
    c = _conn(db_path)
    c.execute(_CREATE)
    c.commit()
    c.close()


def save_bo_run(
    seed_ad_id: str,
    text_source_id: str,
    picks: list[dict],
    db_path: Path = DB_PATH,
) -> list[int]:
    """
    Persist BO picks. Returns list of inserted row IDs (one per pick).
    picks: list of dicts from run_bo() — each has combination_key, combination,
           selection_type, ei_score, gpr_mean, gpr_std.
    """
    ensure_table(db_path)
    c = _conn(db_path)
    ids = []
    for rank, pick in enumerate(picks, start=1):
        cur = c.execute(
            """INSERT INTO bo_selections
               (seed_ad_id, text_source_id, pick_rank, combination_key, combination,
                selection_type, ei_score, gpr_mean, gpr_std)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                seed_ad_id,
                text_source_id,
                rank,
                pick["combination_key"],
                json.dumps(pick["combination"]),
                pick["selection_type"],
                pick.get("ei_score"),
                pick.get("gpr_mean"),
                pick.get("gpr_std"),
            ),
        )
        ids.append(cur.lastrowid)
    c.commit()
    c.close()
    return ids


def get_latest_bo_run(
    seed_ad_id: str,
    text_source_id: str,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Return the most recent BO picks for (seed_ad_id, text_source_id),
    ordered by pick_rank.
    """
    ensure_table(db_path)
    c = _conn(db_path)
    latest_ts = c.execute(
        """SELECT MAX(created_at) FROM bo_selections
           WHERE seed_ad_id = ? AND text_source_id = ?""",
        (seed_ad_id, text_source_id),
    ).fetchone()[0]
    if latest_ts is None:
        c.close()
        return []
    rows = c.execute(
        """SELECT * FROM bo_selections
           WHERE seed_ad_id = ? AND text_source_id = ? AND created_at = ?
           ORDER BY pick_rank""",
        (seed_ad_id, text_source_id, latest_ts),
    ).fetchall()
    c.close()
    result = []
    for row in rows:
        d = dict(row)
        d["combination"] = json.loads(d["combination"])
        result.append(d)
    return result
