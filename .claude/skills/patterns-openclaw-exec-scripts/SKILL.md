---
name: patterns-openclaw-exec-scripts
description: Use when writing a new Python script that will be exec'd by an OpenClaw agent via SKILL.md, adding a new workspace script, or figuring out container vs host paths for exec calls
user-invocable: false
---

# Pattern: Exec-able Workspace Scripts for OpenClaw Agents

**Trigger**: openclaw exec script, workspace script, SKILL.md exec, python3 exec, health_query pattern, cost_summary pattern, exec-able script, workspace health script, sys.path sibling import, JSON stdout, container path
**Confidence**: high
**Created**: 2026-04-27
**Updated**: 2026-04-27
**Version**: 1

OpenClaw skills exec Python scripts inside a Docker container via SKILL.md `exec:` calls. Scripts need to import sibling modules without pip install, return structured JSON the agent reads from stdout, handle errors so the agent can act on them, and work on the host for testing.

## The Standard Template

```python
#!/usr/bin/env python3
from __future__ import annotations  # Python 3.9 compat — required for X | Y union syntax

import argparse
import json
import sys
from pathlib import Path

# Sibling import — works at both host and container paths
sys.path.insert(0, str(Path(__file__).parent))
import health_db  # or whatever sibling module lives in the same directory


def _out(data: dict) -> None:
    print(json.dumps(data))


def _err(msg: str) -> None:
    print(json.dumps({"error": msg}))
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    lt = sub.add_parser("lab-trend")
    lt.add_argument("--marker", required=True)
    lt.add_argument("--months", type=int, default=12)

    args = parser.parse_args()
    if args.command == "lab-trend":
        _out(lab_trend(args.marker, args.months))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise           # preserve exit codes from _err()
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
```

## The 5 Non-Negotiable Rules

1. **JSON to stdout only** — agent reads stdout and synthesizes prose; never print anything else
2. **Exit 0 on success, exit 1 with `{"error": "..."}` on failure** — agent checks exit code
3. **`sys.path.insert(0, str(Path(__file__).parent))`** — no pip install in container; sibling imports only
4. **`from __future__ import annotations`** — `X | Y` union syntax in type hints raises `TypeError` on Python 3.9 without it
5. **Re-raise `SystemExit`** — without this, `_err()`'s `sys.exit(1)` gets swallowed by the catch-all `except Exception`

## SKILL.md Exec Paths (Container)

```yaml
# workspace/health/ scripts:
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py lab-trend --marker "{marker}" --months 12

# workspace/skills/{name}/scripts/:
exec: python3 /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/on_demand.py --query "{query}"

# workspace/scripts/ (shared utilities):
exec: bash /home/openclaw/.openclaw/workspace/scripts/send-email.sh jeff@armantrouts.net "Subject" /tmp/body.txt
```

## Path Resolution for Host-Relative Reads

When a script needs to find a directory at a path that differs between host and container, use a 3-tier fallback:

```python
def _sessions_dir() -> Path:
    # 1. Explicit env var override (testing or non-standard layout)
    env = os.environ.get("OPENCLAW_SESSIONS_DIR")
    if env:
        return Path(env)
    # 2. Host path — resolve relative to this file's location
    host_path = Path(__file__).parents[2] / "config-runtime/agents/main/sessions"
    if host_path.exists():
        return host_path
    # 3. Container hardcoded fallback
    return Path("/home/openclaw/.openclaw/agents/main/sessions")
```

**Parent depth from `workspace/health/script.py`:**
- `parents[0]` = `workspace/health/`
- `parents[1]` = `workspace/`
- `parents[2]` = `agents/sample-agent/`  ← agent root

## Email Body: Write to Temp File, Not Stdin

`send-email.sh` supports `-` for stdin, but the OpenClaw exec tool may not pipe stdin to the subprocess. Always write body to a temp file first:

```yaml
# In SKILL.md — Step N (write tool):
write: /tmp/health_weekly.txt
content: {full synthesized email body}

# Then exec:
exec: bash /home/openclaw/.openclaw/workspace/scripts/send-email.sh jeff@armantrouts.net "Subject" /tmp/health_weekly.txt
```

## Testing Pattern

Call functions directly — never via subprocess. Monkey-patch `get_connection`:

```python
import sqlite3
import unittest
import health_query
import health_db

class TestLabTrend(unittest.TestCase):
    def setUp(self):
        # Do NOT pass ":memory:" to get_connection — it calls Path.mkdir() on the arg
        self.conn = sqlite3.connect(":memory:")
        health_db.initialize_schema(self.conn)
        # Patch before each test so the function under test uses the in-memory DB
        health_query.health_db.get_connection = lambda *a, **kw: self.conn

    def test_happy_path(self):
        # Insert fixture data directly via self.conn, then call the function
        result = health_query.lab_trend("ferritin", 12)
        self.assertEqual(result["marker"], "Ferritin (ng/mL)")

    def test_unknown_marker_exits(self):
        with self.assertRaises(SystemExit):
            health_query.lab_trend("doesnotexist", 12)
```

**Gotcha**: `health_db.get_connection(":memory:")` fails because it unconditionally calls `Path(db_path).parent.mkdir()`. Use `sqlite3.connect(":memory:")` + `health_db.initialize_schema(conn)` directly instead.
