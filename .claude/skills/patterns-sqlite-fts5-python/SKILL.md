---
name: patterns-sqlite-fts5-python
description: Use when adding SQLite to a Python project, implementing FTS5 full-text search, writing dedup/upsert logic, or debugging sqlite3 import errors in Python 3.9
user-invocable: false
---

# SQLite + FTS5 Patterns in Python

**Trigger**: SQLite, FTS5, sqlite3, health_db, INSERT OR IGNORE, dedup, rowcount, external content table, WAL mode, Python 3.9, union syntax, Path OR None
**Confidence**: high
**Created**: 2026-04-26
**Updated**: 2026-04-26
**Version**: 1

## Python 3.9 Compatibility

`Path | None` union syntax in annotations requires Python 3.10+. The Docker container and macOS system Python may be 3.9. Fix:

```python
# Add to top of any module using | union syntax in type annotations
from __future__ import annotations
```

Without this, `def foo(x: Path | None)` raises `TypeError: unsupported operand type(s) for |` on import in Python 3.9.

## Connection Setup (WAL Mode)

```python
import sqlite3

conn = sqlite3.connect(str(db_path))
conn.execute("PRAGMA journal_mode = WAL")   # concurrent reads while writing
conn.execute("PRAGMA synchronous = NORMAL") # safe with WAL, much faster than FULL
conn.execute("PRAGMA foreign_keys = ON")
conn.execute("PRAGMA cache_size = -32768")  # 32MB page cache
conn.row_factory = sqlite3.Row              # dict-like row access: row["column"]
```

WAL mode is critical when host scripts write while a Docker container reads the same file.

## FTS5 External Content Table (Auto-Syncing)

External content table + triggers means FTS stays in sync automatically — no manual maintenance needed. Use `CREATE TRIGGER IF NOT EXISTS` (not `executescript`) to avoid implicit transaction commits.

```sql
-- Source table
CREATE TABLE IF NOT EXISTS health_knowledge (
    id            TEXT PRIMARY KEY,
    episode_title TEXT NOT NULL,
    summary       TEXT NOT NULL
);

-- FTS5 virtual table pointing at source
CREATE VIRTUAL TABLE IF NOT EXISTS health_knowledge_fts USING fts5(
    episode_title, summary,
    content='health_knowledge', content_rowid='rowid',
    tokenize='porter unicode61'   -- stemming: sleep/sleeping/slept all match
);

-- Auto-sync triggers (use separate conn.execute() calls, not executescript)
CREATE TRIGGER IF NOT EXISTS hk_ai AFTER INSERT ON health_knowledge BEGIN
    INSERT INTO health_knowledge_fts(rowid, episode_title, summary)
    VALUES (new.rowid, new.episode_title, new.summary);
END;
CREATE TRIGGER IF NOT EXISTS hk_ad AFTER DELETE ON health_knowledge BEGIN
    INSERT INTO health_knowledge_fts(health_knowledge_fts, rowid, episode_title, summary)
    VALUES ('delete', old.rowid, old.episode_title, old.summary);
END;
CREATE TRIGGER IF NOT EXISTS hk_au AFTER UPDATE ON health_knowledge BEGIN
    INSERT INTO health_knowledge_fts(health_knowledge_fts, rowid, episode_title, summary)
    VALUES ('delete', old.rowid, old.episode_title, old.summary);
    INSERT INTO health_knowledge_fts(rowid, episode_title, summary)
    VALUES (new.rowid, new.episode_title, new.summary);
END;
```

Verify FTS5 is available: `conn.execute("SELECT sqlite_compileoption_used('ENABLE_FTS5')")` → returns 1.

## FTS5 Query Pattern

```python
rows = conn.execute("""
    SELECT hk.episode_title, hk.date, hk.show
    FROM health_knowledge_fts
    JOIN health_knowledge hk ON health_knowledge_fts.rowid = hk.rowid
    WHERE health_knowledge_fts MATCH ?
    ORDER BY rank
    LIMIT 10
""", ("ApoB OR lipoprotein",)).fetchall()
```

**Gotcha**: Don't alias the FTS table with a short alias that conflicts with FTS function names. Use the full table name in the `MATCH` clause:
```sql
-- WRONG — 'fts' conflicts with internal FTS function
FROM health_knowledge_fts fts WHERE fts MATCH ?

-- CORRECT — use full table name
FROM health_knowledge_fts WHERE health_knowledge_fts MATCH ?
```

## INSERT OR IGNORE Dedup Pattern

```python
cursor = conn.execute(
    "INSERT OR IGNORE INTO health_knowledge (id, show, ...) VALUES (?, ?, ...)",
    (entry_id, show, ...)
)
if cursor.rowcount == 0:
    # Duplicate — primary key OR unique index conflict
    return None
conn.commit()
return entry
```

**Important**: `INSERT OR IGNORE` fires on EITHER primary key OR any unique index. If your ID is derived from content (e.g., `md5(summary)[:8]`), two entries with different titles but identical summaries will collide on the PRIMARY KEY — not on a `(show, title, date)` unique index. Write distinct summaries in tests that verify title-based dedup.

## JSON Arrays in SQLite

SQLite has no native array type. Store as JSON string, deserialize on read:

```python
# Write
json.dumps(entry["topics"])  # → '["apob", "sleep", "vo2max"]'

# Read — always use 'or "[]"' guard in case the column is NULL
row_dict["topics"] = json.loads(row_dict.get("topics") or "[]")
```

## Idempotent Schema Init

Wrap all DDL in `IF NOT EXISTS` — safe to call on every `get_connection()`:

```python
def initialize_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS ...")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ...")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS ... USING fts5(...)")
    conn.execute("CREATE TRIGGER IF NOT EXISTS ...")
    conn.commit()  # commit all DDL together
```

## Nullable Dict Fields from APIs

API responses often have keys present but with `null` value. `.get("key", {})` does NOT guard against this:

```python
# WRONG — fails when key exists but value is null
val = rec.get("contributors", {}).get("field")

# CORRECT
val = (rec.get("contributors") or {}).get("field")
nested = rec.get("spo2_percentage") or {}
```

## Schema Migration with user_version

Add a migration runner before you need it — much cheaper than after data exists:

```python
MIGRATIONS = [
    # Version 1: add canonical_unit to lab_markers
    ["ALTER TABLE lab_markers ADD COLUMN canonical_unit TEXT"],
]

def migrate(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, stmts in enumerate(MIGRATIONS, start=1):
        if cur >= i:
            continue
        for s in stmts:
            conn.execute(s)
        conn.execute(f"PRAGMA user_version = {i}")
    conn.commit()
```
