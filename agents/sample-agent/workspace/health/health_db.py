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


SCHEMA_VERSION = 7


def initialize_schema(conn: sqlite3.Connection) -> None:
    """
    Create all tables, indexes, FTS virtual table, and triggers if they do not
    already exist.  Safe to call repeatedly — every statement uses IF NOT EXISTS.

    Schema versions (PRAGMA user_version):
      0 → 1 : original tables (health_knowledge, lab_*, oura_*, blood_pressure)
      1 → 2 : body_metrics
      2 → 3 : activity_daily, workouts
      3 → 4 : workout_exercises, oura_tags
      4 → 5 : state_of_mind
      5 → 6 : lab_results.in_range_flag, health_knowledge.enrichment_status + topics_text, FTS rebuild
      6 → 7 : workouts.min_hr, workouts.intensity_met
    """
    _version = conn.execute("PRAGMA user_version").fetchone()[0]
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

    # Stamp version 1 for any DB that has the original tables but no version yet
    if _version < 1:
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        _version = 1

    # ---------- v2: body_metrics ----------------------------------------
    if _version < 2:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS body_metrics (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                time            TEXT,
                weight_lbs      REAL,
                fat_ratio_pct   REAL,
                fat_mass_lbs    REAL,
                lean_mass_lbs   REAL,
                muscle_mass_lbs REAL,
                source          TEXT DEFAULT 'withings_api',
                fetched_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_body_metrics_datetime
            ON body_metrics(date, time)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_body_metrics_date
            ON body_metrics(date)
        """)
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        _version = 2

    # ---------- v3: activity_daily + workouts ---------------------------
    if _version < 3:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS activity_daily (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                date             TEXT NOT NULL UNIQUE,
                steps            INTEGER,
                daylight_minutes REAL,
                source           TEXT DEFAULT 'apple_health',
                fetched_at       TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );

            CREATE TABLE IF NOT EXISTS workouts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                start_time      TEXT,
                end_time        TEXT,
                workout_type    TEXT NOT NULL,
                duration_min    REAL,
                calories        REAL,
                avg_hr          INTEGER,
                max_hr          INTEGER,
                effort_rating   TEXT,
                source          TEXT DEFAULT 'apple_health',
                notes           TEXT,
                fetched_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_workouts_key
            ON workouts(date, start_time, workout_type);

            CREATE INDEX IF NOT EXISTS idx_workouts_date
            ON workouts(date);
        """)
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
        _version = 3

    # ---------- v4: workout_exercises + oura_tags -----------------------
    if _version < 4:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS workout_exercises (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                workout_id      INTEGER REFERENCES workouts(id) ON DELETE CASCADE,
                workout_date    TEXT NOT NULL,
                exercise_name   TEXT NOT NULL,
                set_number      INTEGER,
                reps            INTEGER,
                weight_lbs      REAL,
                notes           TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_workout_exercises_date
            ON workout_exercises(workout_date);

            CREATE TABLE IF NOT EXISTS oura_tags (
                id          TEXT PRIMARY KEY,
                day         TEXT NOT NULL,
                tag_type    TEXT NOT NULL,
                start_time  TEXT,
                end_time    TEXT,
                comment     TEXT,
                fetched_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE INDEX IF NOT EXISTS idx_oura_tags_day
            ON oura_tags(day);
        """)
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        _version = 4

    # ---------- v5: state_of_mind ----------------------------------------
    if _version < 5:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS state_of_mind (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                date         TEXT NOT NULL,
                logged_at    TEXT,
                kind         TEXT DEFAULT 'daily_mood',
                valence      REAL,
                arousal      REAL,
                labels       TEXT,
                associations TEXT,
                source       TEXT DEFAULT 'apple_health',
                imported_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_state_of_mind
            ON state_of_mind(date, kind, logged_at);
            CREATE INDEX IF NOT EXISTS idx_state_of_mind_date
            ON state_of_mind(date);
        """)
        conn.execute("PRAGMA user_version = 5")
        conn.commit()
        _version = 5

    # ---------- v6: in_range_flag, enrichment_status, topics_text + FTS rebuild --
    if _version < 6:
        # executescript cannot guard against duplicate column errors, so use
        # individual try/except calls — same pattern as canonical_unit above
        for _stmt in [
            "ALTER TABLE lab_results ADD COLUMN in_range_flag TEXT",
            "ALTER TABLE health_knowledge ADD COLUMN enrichment_status TEXT",
            "ALTER TABLE health_knowledge ADD COLUMN topics_text TEXT",
        ]:
            try:
                conn.execute(_stmt)
            except sqlite3.OperationalError:
                pass
        # Rebuild FTS to include topics_text as third indexed column
        conn.executescript("""
            DROP TABLE IF EXISTS health_knowledge_fts;
            CREATE VIRTUAL TABLE health_knowledge_fts USING fts5(
                episode_title, summary, topics_text,
                content='health_knowledge', content_rowid='rowid',
                tokenize='porter unicode61'
            );
            DROP TRIGGER IF EXISTS hk_ai;
            DROP TRIGGER IF EXISTS hk_ad;
            DROP TRIGGER IF EXISTS hk_au;
        """)
        # Recreate triggers with 3 columns — each trigger uses conn.execute()
        # because executescript misparses trigger bodies that contain semicolons
        conn.execute("""CREATE TRIGGER IF NOT EXISTS hk_ai AFTER INSERT ON health_knowledge BEGIN
            INSERT INTO health_knowledge_fts(rowid, episode_title, summary, topics_text)
            VALUES (new.rowid, new.episode_title, new.summary, COALESCE(new.topics_text, ''));
        END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS hk_ad AFTER DELETE ON health_knowledge BEGIN
            INSERT INTO health_knowledge_fts(health_knowledge_fts, rowid, episode_title, summary, topics_text)
            VALUES ('delete', old.rowid, old.episode_title, old.summary, COALESCE(old.topics_text, ''));
        END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS hk_au AFTER UPDATE ON health_knowledge BEGIN
            INSERT INTO health_knowledge_fts(health_knowledge_fts, rowid, episode_title, summary, topics_text)
            VALUES ('delete', old.rowid, old.episode_title, old.summary, COALESCE(old.topics_text, ''));
            INSERT INTO health_knowledge_fts(rowid, episode_title, summary, topics_text)
            VALUES (new.rowid, new.episode_title, new.summary, COALESCE(new.topics_text, ''));
        END""")
        # Repopulate FTS from base table (required after DROP + recreate)
        conn.execute("""
            INSERT INTO health_knowledge_fts(rowid, episode_title, summary, topics_text)
            SELECT rowid, episode_title, summary, COALESCE(topics_text, '')
            FROM health_knowledge
        """)
        # Backfill enrichment_status from existing topics data
        conn.execute("""
            UPDATE health_knowledge
            SET enrichment_status = CASE
                WHEN topics IS NOT NULL AND topics != '[]' THEN 'done'
                ELSE 'pending'
            END
            WHERE enrichment_status IS NULL
        """)
        conn.execute("PRAGMA user_version = 6")
        conn.commit()
        _version = 6

    # Backfill topics_text for rows that have topics JSON but no topics_text yet.
    # Must run after the v6 migration block so the column exists.
    import json as _json
    _rows = conn.execute(
        "SELECT rowid, topics FROM health_knowledge WHERE topics_text IS NULL AND topics IS NOT NULL"
    ).fetchall()
    for _row in _rows:
        try:
            _tags = _json.loads(_row[1])
            _topics_text = ' '.join(_tags) if isinstance(_tags, list) else ''
        except Exception:
            _topics_text = ''
        conn.execute(
            "UPDATE health_knowledge SET topics_text = ? WHERE rowid = ?",
            (_topics_text, _row[0]),
        )
    if _rows:
        conn.commit()

    # ---------- v7: workouts.min_hr + workouts.intensity_met ---------------
    if _version < 7:
        for _col, _type in (("min_hr", "INTEGER"), ("intensity_met", "REAL")):
            try:
                conn.execute(f"ALTER TABLE workouts ADD COLUMN {_col} {_type}")
            except sqlite3.OperationalError as _e:
                if "duplicate column name" not in str(_e):
                    raise
        conn.execute("PRAGMA user_version = 7")
        conn.commit()
        _version = 7

    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION, (
        f"Schema migration incomplete: DB is at v{conn.execute('PRAGMA user_version').fetchone()[0]} "
        f"but SCHEMA_VERSION={SCHEMA_VERSION}"
    )


# ---------------------------------------------------------------------------
# sync_state helpers
# ---------------------------------------------------------------------------

def get_last_synced(conn: sqlite3.Connection, resource: str, default: str = None) -> str | None:
    """Return the last_synced value for a resource, or default if not found."""
    row = conn.execute(
        "SELECT last_synced FROM sync_state WHERE resource = ?", (resource,)
    ).fetchone()
    return row[0] if row else default


def set_last_synced(conn: sqlite3.Connection, resource: str, value: str) -> None:
    """Upsert the last_synced timestamp for a resource."""
    conn.execute(
        "INSERT OR REPLACE INTO sync_state (resource, last_synced) VALUES (?, ?)",
        (resource, value),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# HRV helpers
# ---------------------------------------------------------------------------

def backfill_daily_hrv(conn: sqlite3.Connection) -> None:
    """
    Populate oura_daily.avg_hrv_rmssd from the best sleep session per day.

    Session selection: prefer type = 'long_sleep'; fall back to the session
    with the longest total_sleep_sec.  Idempotent — re-derives from sessions
    on each call, safe to run repeatedly.
    """
    conn.execute("""
        UPDATE oura_daily
        SET avg_hrv_rmssd = (
            SELECT avg_hrv FROM oura_sleep_sessions
            WHERE oura_sleep_sessions.day = oura_daily.day
              AND avg_hrv IS NOT NULL
            ORDER BY
                CASE WHEN type = 'long_sleep' THEN 0 ELSE 1 END,
                total_sleep_sec DESC
            LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1 FROM oura_sleep_sessions
            WHERE oura_sleep_sessions.day = oura_daily.day
              AND avg_hrv IS NOT NULL
        )
    """)
    conn.commit()
