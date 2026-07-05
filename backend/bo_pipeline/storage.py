"""
Persistence for BO run results.

Table: bo_selections — one row per recommended combination per BO run.
pick_rank 1 = EI pick, 2 = fantasy pick.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "app.db"

_CREATE = """
CREATE TABLE IF NOT EXISTS bo_selections (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    seed_ad_id              TEXT NOT NULL,
    text_source_id          TEXT NOT NULL,
    pick_rank               INTEGER NOT NULL,
    combination_key         TEXT NOT NULL,
    combination             TEXT NOT NULL,
    selection_type          TEXT NOT NULL,
    ei_score                REAL,
    gpr_mean                REAL,
    gpr_std                 REAL,
    google_ad_resource_name TEXT,
    run_id                  TEXT,
    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

# Backstop against duplicate not-yet-pushed picks for the same combination —
# save_bo_run() deletes prior unpushed rows before inserting, so this should
# never trip in practice; it exists to turn a would-be race (two concurrent
# saves for the same seed/text_source) into a loud IntegrityError instead of
# silent duplicate rows. Pushed rows (google_ad_resource_name set) are exempt
# since they're permanent push history and may legitimately repeat a
# combination_key from a later run.
_UNIQUE_UNPUSHED_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_bo_selections_unpushed
ON bo_selections(seed_ad_id, text_source_id, combination_key)
WHERE google_ad_resource_name IS NULL
"""

_SCORED_OBS_CREATE = """
CREATE TABLE IF NOT EXISTS scored_observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    seed_ad_id      TEXT NOT NULL,
    combination_key TEXT NOT NULL,
    combination     TEXT NOT NULL,
    score           REAL NOT NULL,
    metric          TEXT NOT NULL DEFAULT 'synthetic',
    source          TEXT NOT NULL DEFAULT 'seed_script',
    text_vector     BLOB,
    image_vector    BLOB,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, seed_ad_id, combination_key, metric)
)
"""


def _conn(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    # Concurrent BO saves for the same ad should queue behind the writer lock
    # rather than fail immediately with "database is locked".
    c.execute("PRAGMA busy_timeout = 5000")
    return c


def ensure_table(db_path: Path = DB_PATH) -> None:
    c = _conn(db_path)
    c.execute(_CREATE)
    try:
        c.execute("ALTER TABLE bo_selections ADD COLUMN run_id TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    # Dedupe pre-existing duplicate unpushed picks (from before this fix
    # existed, e.g. concurrent/repeated BO runs) so the unique index below can
    # apply — keep the most recently inserted row per key.
    c.execute(
        """
        DELETE FROM bo_selections
        WHERE google_ad_resource_name IS NULL
          AND id NOT IN (
              SELECT MAX(id) FROM bo_selections
              WHERE google_ad_resource_name IS NULL
              GROUP BY seed_ad_id, text_source_id, combination_key
          )
        """
    )
    c.execute(_UNIQUE_UNPUSHED_INDEX)
    c.commit()
    c.close()


def ensure_scored_observations_table(db_path: Path = DB_PATH) -> None:
    c = _conn(db_path)
    c.execute(_SCORED_OBS_CREATE)
    c.commit()
    c.close()


def save_bo_run(
    seed_ad_id: str,
    text_source_id: str,
    picks: list[dict],
    db_path: Path = DB_PATH,
) -> list[int]:
    """
    Persist BO picks as a single run, tagged with a fresh run_id.

    Any not-yet-pushed picks from a prior run for the same
    (seed_ad_id, text_source_id) are deleted first, in the same transaction —
    a fresh run supersedes the old recommendations rather than piling up
    alongside them. Rows already pushed to a platform
    (google_ad_resource_name set) are left alone; they're permanent push
    history, not live recommendations.

    picks: list of dicts from run_bo() — each has combination_key, combination,
           selection_type, ei_score, gpr_mean, gpr_std.
    Returns list of inserted row IDs (one per pick).
    """
    ensure_table(db_path)
    run_id = uuid.uuid4().hex
    c = _conn(db_path)
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute(
            """DELETE FROM bo_selections
               WHERE seed_ad_id = ? AND text_source_id = ?
                 AND google_ad_resource_name IS NULL""",
            (seed_ad_id, text_source_id),
        )
        ids = []
        for rank, pick in enumerate(picks, start=1):
            cur = c.execute(
                """INSERT INTO bo_selections
                   (seed_ad_id, text_source_id, pick_rank, combination_key, combination,
                    selection_type, ei_score, gpr_mean, gpr_std, run_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    run_id,
                ),
            )
            ids.append(cur.lastrowid)
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    return ids


def get_latest_bo_run(
    seed_ad_id: str,
    text_source_id: str,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Return the most recent BO picks for (seed_ad_id, text_source_id),
    ordered by pick_rank. "Most recent" = the run_id shared by the
    highest-id row, since save_bo_run() inserts a whole run's rows together.
    """
    ensure_table(db_path)
    c = _conn(db_path)
    latest = c.execute(
        """SELECT run_id FROM bo_selections
           WHERE seed_ad_id = ? AND text_source_id = ?
           ORDER BY id DESC LIMIT 1""",
        (seed_ad_id, text_source_id),
    ).fetchone()
    if latest is None:
        c.close()
        return []
    rows = c.execute(
        """SELECT * FROM bo_selections
           WHERE seed_ad_id = ? AND text_source_id = ? AND run_id IS ?
           ORDER BY pick_rank""",
        (seed_ad_id, text_source_id, latest["run_id"]),
    ).fetchall()
    c.close()
    result = []
    for row in rows:
        d = dict(row)
        d["combination"] = json.loads(d["combination"])
        result.append(d)
    return result
