#!/usr/bin/env python3
"""
SQLite schema owner and connection manager for health.db.

Resolves the DB path relative to this file so the same code works on the
Mac host and inside the Docker container (same relative structure on both
sides of the volume mount).

Usage:
    import health_db
    conn = health_db.get_connection()   # opens / initialises DB, returns sqlite3.Connection
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# health.db lives alongside this file in workspace/health/
_HEALTH_DIR = Path(__file__).parent


def get_db_path() -> Path:
    """Return absolute path to workspace/health/health.db."""
    return _HEALTH_DIR / "health.db"


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """
    Open health.db, apply performance PRAGMAs, initialise schema (idempotent),
    and return the connection with sqlite3.Row factory enabled.

    WAL mode is critical for concurrent reads (e.g. agent queries) while
    Oura sync is writing.
    """
    if db_path is None:
        db_path = get_db_path()

    # Ensure parent directory exists (first run on a fresh workspace)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA cache_size = -32768")  # 32 MB page cache
    conn.execute("PRAGMA busy_timeout = 5000")   # wait up to 5s on writer collision
    conn.row_factory = sqlite3.Row

    initialize_schema(conn)
    return conn


def initialize_schema(conn: sqlite3.Connection) -> None:
    """
    Create all tables, indexes, FTS virtual table, and triggers if they do not
    already exist.  Safe to call repeatedly — every statement uses IF NOT EXISTS.
    """
    # ---------- health_knowledge ----------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS health_knowledge (
            id              TEXT PRIMARY KEY,
            show            TEXT NOT NULL,
            episode_title   TEXT NOT NULL,
            episode_number  TEXT,
            date            TEXT NOT NULL,
            source          TEXT NOT NULL,
            source_quality  TEXT,
            topics          TEXT,
            summary         TEXT NOT NULL,
            tagged_by       TEXT,
            imported_at     TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_hk_dedup
        ON health_knowledge(show, episode_title, date)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_hk_date
        ON health_knowledge(date)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_hk_show
        ON health_knowledge(show, date)
    """)

    # ---------- FTS5 virtual table + triggers ---------------------------
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS health_knowledge_fts USING fts5(
            episode_title, summary,
            content='health_knowledge', content_rowid='rowid',
            tokenize='porter unicode61'
        )
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS hk_ai AFTER INSERT ON health_knowledge BEGIN
            INSERT INTO health_knowledge_fts(rowid, episode_title, summary)
            VALUES (new.rowid, new.episode_title, new.summary);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS hk_ad AFTER DELETE ON health_knowledge BEGIN
            INSERT INTO health_knowledge_fts(health_knowledge_fts, rowid, episode_title, summary)
            VALUES ('delete', old.rowid, old.episode_title, old.summary);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS hk_au AFTER UPDATE ON health_knowledge BEGIN
            INSERT INTO health_knowledge_fts(health_knowledge_fts, rowid, episode_title, summary)
            VALUES ('delete', old.rowid, old.episode_title, old.summary);
            INSERT INTO health_knowledge_fts(rowid, episode_title, summary)
            VALUES (new.rowid, new.episode_title, new.summary);
        END
    """)

    # ---------- lab_markers ---------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lab_markers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL UNIQUE,
            canonical_unit  TEXT,
            aliases         TEXT
        )
    """)
    # Add canonical_unit to existing DBs (idempotent — ignored if already present)
    try:
        conn.execute("ALTER TABLE lab_markers ADD COLUMN canonical_unit TEXT")
    except sqlite3.OperationalError:
        pass

    # Add raw_transcript to existing DBs (idempotent — ignored if already present)
    try:
        conn.execute("ALTER TABLE health_knowledge ADD COLUMN raw_transcript TEXT")
    except sqlite3.OperationalError:
        pass

    # ---------- lab_results ---------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lab_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            marker_id       INTEGER NOT NULL REFERENCES lab_markers(id),
            date            TEXT NOT NULL,
            value           REAL NOT NULL,
            reference_low   REAL,
            reference_high  REAL,
            source_sheet    TEXT,
            imported_at     TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_lab_marker_date
        ON lab_results(marker_id, date)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_lab_date
        ON lab_results(date)
    """)

    # ---------- oura_daily ----------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oura_daily (
            id                      TEXT PRIMARY KEY,
            day                     TEXT NOT NULL UNIQUE,
            sleep_score             INTEGER,
            readiness_score         INTEGER,
            activity_score          INTEGER,
            steps                   INTEGER,
            active_calories         INTEGER,
            total_calories          INTEGER,
            avg_hrv_rmssd           REAL,
            resting_heart_rate      INTEGER,
            temp_deviation          REAL,
            spo2_avg                REAL,
            spo2_min                REAL,
            stress_high_seconds     INTEGER,
            recovery_high_seconds   INTEGER,
            stress_day_summary      TEXT,
            resilience_level        TEXT,
            contributors_json       TEXT,
            fetched_at              TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_oura_day
        ON oura_daily(day)
    """)

    # ---------- oura_sleep_sessions -------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oura_sleep_sessions (
            id                  TEXT PRIMARY KEY,
            day                 TEXT NOT NULL,
            type                TEXT NOT NULL,
            bedtime_start       TEXT NOT NULL,
            bedtime_end         TEXT NOT NULL,
            total_sleep_sec     INTEGER,
            deep_sleep_sec      INTEGER,
            light_sleep_sec     INTEGER,
            rem_sleep_sec       INTEGER,
            awake_sec           INTEGER,
            efficiency          INTEGER,
            latency_sec         INTEGER,
            avg_hrv             REAL,
            avg_heart_rate      REAL,
            lowest_heart_rate   INTEGER,
            hr_5min             TEXT,
            hrv_5min            TEXT,
            sleep_phase_5min    TEXT,
            fetched_at          TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sleep_day
        ON oura_sleep_sessions(day)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sleep_type
        ON oura_sleep_sessions(day, type)
    """)

    # ---------- oura_heartrate ------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oura_heartrate (
            timestamp   TEXT NOT NULL,
            bpm         INTEGER NOT NULL,
            source      TEXT NOT NULL,
            day         TEXT GENERATED ALWAYS AS (substr(timestamp, 1, 10)) STORED,
            PRIMARY KEY (timestamp, source)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_hr_day
        ON oura_heartrate(day)
    """)

    # ---------- sync_state ----------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_state (
            resource    TEXT PRIMARY KEY,
            last_synced TEXT NOT NULL,
            next_token  TEXT
        )
    """)

    # ---------- blood_pressure ------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS blood_pressure (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            time        TEXT NOT NULL,
            systolic    INTEGER NOT NULL,
            diastolic   INTEGER NOT NULL,
            pulse       INTEGER,
            source      TEXT DEFAULT 'manual',
            notes       TEXT,
            imported_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_bp_datetime
        ON blood_pressure(date, time)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_bp_date
        ON blood_pressure(date)
    """)

    conn.commit()
