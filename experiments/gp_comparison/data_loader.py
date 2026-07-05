"""
Load Meta and Google RSA ad candidates from app.db.

Returns raw feature vectors — no synthetic scores, no pipeline assumptions.
  Meta candidates:   text_vec (1536) || image_vec (1536) = 3072-dim float32
  Google candidates: text_vec (1536-dim float32 only)
"""

import sqlite3
import numpy as np
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "backend" / "app.db"

# Ads confirmed to have both text-combination embeddings and image embeddings in app.db
META_AD_IDS   = ["120212001", "120244804530430577"]
GOOGLE_AD_IDS = ["807593163250", "721808699", "7654321000", "7654321001"]
USER_ID = 1


def _conn(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c


def _vec(blob) -> np.ndarray | None:
    if blob is None:
        return None
    return np.frombuffer(blob, dtype=np.float32).copy()


def load_candidates(db_path: Path = DB_PATH) -> tuple[list[dict], list[dict]]:
    """
    Returns (meta_candidates, google_candidates).

    Each dict:
      vec      — np.ndarray float32 (3072-dim for Meta, 1536-dim for Google)
      platform — "meta" | "google"
      ad_id    — str
    """
    c = _conn(db_path)
    meta_candidates   = []
    google_candidates = []

    for ad_id in META_AD_IDS:
        text_rows = c.execute(
            "SELECT combination_key, vector FROM ad_text_combination_embeddings WHERE source_id = ?",
            (ad_id,),
        ).fetchall()
        img_rows = c.execute(
            "SELECT vector FROM ad_image_embeddings WHERE user_id = ? AND ad_id = ? ORDER BY slot_index",
            (USER_ID, ad_id),
        ).fetchall()

        text_vecs = [_vec(r["vector"]) for r in text_rows]
        img_vecs  = [_vec(r["vector"]) for r in img_rows]
        text_vecs = [v for v in text_vecs if v is not None]
        img_vecs  = [v for v in img_vecs  if v is not None]

        for tv in text_vecs:
            for iv in img_vecs:
                meta_candidates.append({
                    "vec":      np.concatenate([tv, iv]),
                    "platform": "meta",
                    "ad_id":    ad_id,
                })

    for ad_id in GOOGLE_AD_IDS:
        rows = c.execute(
            "SELECT combination_key, vector FROM ad_text_combination_embeddings WHERE source_id = ?",
            (ad_id,),
        ).fetchall()
        for r in rows:
            v = _vec(r["vector"])
            if v is not None:
                google_candidates.append({
                    "vec":      v,
                    "platform": "google",
                    "ad_id":    ad_id,
                })

    c.close()
    return meta_candidates, google_candidates
