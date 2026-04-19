"""
Storage for ad text combination embeddings.

One table: ad_text_combination_embeddings
  - Keyed on (source_id, combination_key) for idempotent upsert
  - source_id is a caller-provided string (e.g., an ad_id or generated_ad_id)
  - combination_key is the deterministic JSON of one slot-value combo
  - vector is raw float32 bytes (numpy .tobytes())

No application metadata (no user_id, campaign_id) — the outer layer handles that.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

DB_PATH = Path(__file__).parent.parent / "app.db"

_CREATE = """
CREATE TABLE IF NOT EXISTS ad_text_combination_embeddings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id        TEXT NOT NULL,
    combination_key  TEXT NOT NULL,
    vector           BLOB NOT NULL,
    model            TEXT,
    embedded_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source_id, combination_key)
)
"""

_UPSERT = """
INSERT INTO ad_text_combination_embeddings
    (source_id, combination_key, vector, model)
VALUES (?, ?, ?, ?)
ON CONFLICT (source_id, combination_key) DO UPDATE SET
    vector      = excluded.vector,
    model       = excluded.model,
    embedded_at = datetime('now')
"""


def _conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def ensure_table(db_path: Path = DB_PATH) -> None:
    c = _conn(db_path)
    c.execute(_CREATE)
    c.commit()
    c.close()


def save_embedding(
    source_id: str,
    combination_key: str,
    vector: np.ndarray,
    model: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    ensure_table(db_path)
    c = _conn(db_path)
    c.execute(_UPSERT, (source_id, combination_key, vector.tobytes(), model))
    c.commit()
    c.close()


def save_embeddings_batch(
    source_id: str,
    rows: list[tuple[str, np.ndarray, str | None]],  # (combination_key, vector, model)
    db_path: Path = DB_PATH,
) -> None:
    """Insert or replace multiple embeddings in a single transaction."""
    ensure_table(db_path)
    c = _conn(db_path)
    c.executemany(
        _UPSERT,
        [(source_id, key, vec.tobytes(), model) for key, vec, model in rows],
    )
    c.commit()
    c.close()


def get_embeddings_for_source(
    source_id: str,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Return all embeddings stored for a source_id.

    Each dict has:
      combination_key  — str (the deterministic JSON key)
      combination      — dict (the parsed key, for convenience)
      vector           — np.ndarray (float32)
      model            — str | None
      embedded_at      — str
    """
    c = _conn(db_path)
    rows = c.execute(
        """SELECT combination_key, vector, model, embedded_at
           FROM ad_text_combination_embeddings
           WHERE source_id = ?
           ORDER BY id""",
        (source_id,),
    ).fetchall()
    c.close()

    result = []
    for row in rows:
        result.append({
            "combination_key": row["combination_key"],
            "combination":     json.loads(row["combination_key"]),
            "vector":          np.frombuffer(row["vector"], dtype=np.float32),
            "model":           row["model"],
            "embedded_at":     row["embedded_at"],
        })
    return result


def count_embeddings_for_source(source_id: str, db_path: Path = DB_PATH) -> int:
    c = _conn(db_path)
    n = c.execute(
        "SELECT COUNT(*) FROM ad_text_combination_embeddings WHERE source_id = ?",
        (source_id,),
    ).fetchone()[0]
    c.close()
    return n
