"""
Lightweight storage for assembled generated ads.

Two tables — no metadata (no user_id, campaign_id, etc.).
The outer application layer is responsible for associating stored ads
with campaigns, adsets, and users.

Tables:
  generated_ads       — one row per assembled ad
  generated_ad_slots  — one row per slot/value in the assembled ad
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).parent.parent / "app.db"

_CREATE_ADS = """
CREATE TABLE IF NOT EXISTS generated_ads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_ad_id  TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_CREATE_SLOTS = """
CREATE TABLE IF NOT EXISTS generated_ad_slots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_ad_id  INTEGER NOT NULL REFERENCES generated_ads(id),
    slot             TEXT NOT NULL,
    slot_index       INTEGER NOT NULL DEFAULT 0,
    value            TEXT NOT NULL,
    source           TEXT NOT NULL DEFAULT 'generated',
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def _conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def ensure_tables(db_path: Path = DB_PATH) -> None:
    c = _conn(db_path)
    c.execute(_CREATE_ADS)
    c.execute(_CREATE_SLOTS)
    c.commit()
    c.close()


def save_generated_ad(
    components: list[dict],
    source_ad_id: str | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """
    Persist a generated ad's component list.

    components — list of {slot, slot_index, value, source} dicts
                 (as produced by assembler.assemble_dynamic_ad)
    Returns the new generated_ad_id.
    """
    ensure_tables(db_path)
    c = _conn(db_path)
    cur = c.execute(
        "INSERT INTO generated_ads (source_ad_id) VALUES (?)",
        (source_ad_id,),
    )
    ad_id = cur.lastrowid
    c.executemany(
        """INSERT INTO generated_ad_slots
               (generated_ad_id, slot, slot_index, value, source)
           VALUES (?, ?, ?, ?, ?)""",
        [
            (ad_id, comp["slot"], comp["slot_index"], comp["value"], comp.get("source", "generated"))
            for comp in components
            if comp.get("value")
        ],
    )
    c.commit()
    c.close()
    return ad_id


def get_generated_ad(generated_ad_id: int, db_path: Path = DB_PATH) -> list[dict]:
    """
    Return all slot rows for a generated ad as a list of dicts.
    Empty list if the id doesn't exist.
    """
    c = _conn(db_path)
    rows = c.execute(
        """SELECT slot, slot_index, value, source
           FROM generated_ad_slots
           WHERE generated_ad_id = ?
           ORDER BY slot, slot_index""",
        (generated_ad_id,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_generated_slots_for_source(source_ad_id: str, db_path: Path = DB_PATH) -> list[dict]:
    """Return all slot rows across all generated ads with the given source_ad_id."""
    c = _conn(db_path)
    rows = c.execute(
        """SELECT gs.slot, gs.slot_index, gs.value
           FROM generated_ad_slots gs
           JOIN generated_ads ga ON ga.id = gs.generated_ad_id
           WHERE ga.source_ad_id = ?
           ORDER BY gs.slot, gs.slot_index""",
        (source_ad_id,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_generated_ad_meta(generated_ad_id: int, db_path: Path = DB_PATH) -> dict | None:
    """Return the generated_ads header row."""
    c = _conn(db_path)
    row = c.execute(
        "SELECT * FROM generated_ads WHERE id = ?",
        (generated_ad_id,),
    ).fetchone()
    c.close()
    return dict(row) if row else None
