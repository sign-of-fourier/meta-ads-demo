"""
Tests for Chunk 2 DB migrations: platform columns and google_connections table.

Covers:
- Fresh DB has all new columns and google_connections table
- Migrations are idempotent on a DB that already has the old schema
"""

from __future__ import annotations

import os
import sqlite3

import pytest

os.environ.setdefault("META_APP_ID", "test_app_id")
os.environ.setdefault("META_APP_SECRET", "test_app_secret")
os.environ.setdefault("META_REDIRECT_URI", "http://localhost:8000/auth/meta/callback")
os.environ.setdefault("JWT_SECRET", "test_secret")

import main as m
from main import init_db


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(m, "DB_PATH", db_file)
    return db_file


def _columns(db_path, table):
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    conn.close()
    return {row[1] for row in rows}  # row[1] is column name


def _tables(db_path):
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    conn.close()
    return {row[0] for row in rows}


def test_fresh_db_has_platform_columns(tmp_db):
    init_db()
    assert "platform" in _columns(tmp_db, "ad_insights")
    assert "platform" in _columns(tmp_db, "ad_creative_structures")


def test_fresh_db_has_provider_column_on_oauth_states(tmp_db):
    init_db()
    assert "provider" in _columns(tmp_db, "oauth_states")


def test_fresh_db_has_google_connections_table(tmp_db):
    init_db()
    assert "google_connections" in _tables(tmp_db)


def test_google_connections_schema(tmp_db):
    init_db()
    cols = _columns(tmp_db, "google_connections")
    assert cols == {"user_id", "customer_id", "login_customer_id", "refresh_token", "customer_name", "connected_at"}


def test_migrations_idempotent_on_existing_db(tmp_db):
    """Running init_db() twice must not raise and must leave schema intact."""
    init_db()
    init_db()
    assert "platform" in _columns(tmp_db, "ad_insights")
    assert "platform" in _columns(tmp_db, "ad_creative_structures")
    assert "provider" in _columns(tmp_db, "oauth_states")
    assert "google_connections" in _tables(tmp_db)


def test_migrations_idempotent_on_pre_existing_db(tmp_db):
    """Simulate an old DB (no platform/provider columns) and verify migrations apply cleanly."""
    # Create the old schema manually — without the new columns
    conn = sqlite3.connect(str(tmp_db))
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            pw_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE oauth_states (
            state TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE ad_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ad_account_id TEXT NOT NULL,
            level TEXT NOT NULL,
            object_id TEXT NOT NULL,
            date TEXT NOT NULL,
            impressions INTEGER, clicks INTEGER, spend REAL,
            ctr REAL, cpm REAL, cpc REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE ad_creative_structures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ad_account_id TEXT NOT NULL,
            campaign_id TEXT NOT NULL,
            adset_id TEXT NOT NULL,
            ad_id TEXT NOT NULL,
            creative_type TEXT NOT NULL,
            slot TEXT NOT NULL,
            slot_index INTEGER NOT NULL DEFAULT 0,
            value TEXT,
            ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (user_id, ad_id, slot, slot_index)
        );
    """)
    conn.commit()
    conn.close()

    # init_db() should apply all migrations without raising
    init_db()

    assert "platform" in _columns(tmp_db, "ad_insights")
    assert "platform" in _columns(tmp_db, "ad_creative_structures")
    assert "provider" in _columns(tmp_db, "oauth_states")
    assert "google_connections" in _tables(tmp_db)
