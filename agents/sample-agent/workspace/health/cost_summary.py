#!/usr/bin/env python3
"""
cost_summary.py — Aggregate session JSONL costs for the current week or month.

Usage:
    python3 cost_summary.py --week    # Mon–Sun of current calendar week
    python3 cost_summary.py --month   # 1st through today of current calendar month

Output JSON:
    {"period": "week", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD",
     "sessions": N, "cost_usd": X.XX}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path


def _sessions_dir() -> Path:
    # 1. Explicit env var (useful for testing or non-standard layouts)
    env = os.environ.get("OPENCLAW_SESSIONS_DIR")
    if env:
        return Path(env)
    # 2. Host path: agents/sample-agent/config-runtime/agents/main/sessions
    host_path = Path(__file__).parents[2] / "config-runtime" / "agents" / "main" / "sessions"
    if host_path.exists():
        return host_path
    # 3. Container path
    container_path = Path("/home/openclaw/.openclaw/agents/main/sessions")
    return container_path


def _week_range(today: date) -> tuple[date, date]:
    start = today - timedelta(days=today.weekday())  # Monday
    end = start + timedelta(days=6)                  # Sunday
    return start, end


def _month_range(today: date) -> tuple[date, date]:
    start = today.replace(day=1)
    return start, today


def summarize(period: str) -> dict:
    today = date.today()
    if period == "week":
        start, end = _week_range(today)
    else:
        start, end = _month_range(today)

    start_ts = start.isoformat()
    sessions_dir = _sessions_dir()
    if not sessions_dir.exists():
        print(json.dumps({"error": f"sessions directory not found: {sessions_dir}"}))
        sys.exit(1)

    cost_total = 0.0
    session_count = 0

    for f in sessions_dir.glob("*.jsonl"):
        if "trajectory" in f.name:
            continue

        # Use mtime to skip files outside the window (fast pre-filter)
        mtime = date.fromtimestamp(f.stat().st_mtime)
        if not (start <= mtime <= end):
            continue

        session_cost = 0.0
        found = False
        try:
            for line in f.read_text(errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = rec.get("message")
                if not isinstance(msg, dict):
                    continue
                if msg.get("role") != "assistant":
                    continue
                usage = msg.get("usage") or {}
                cost_val = (usage.get("cost") or {}).get("total") or 0.0
                session_cost += float(cost_val)
                found = True
        except OSError:
            continue

        if found:
            cost_total += session_cost
            session_count += 1

    return {
        "period": period,
        "start": start_ts,
        "end": end.isoformat(),
        "sessions": session_count,
        "cost_usd": round(cost_total, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate session costs")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--week", action="store_true", help="Current calendar week (Mon–Sun)")
    group.add_argument("--month", action="store_true", help="Current calendar month to date")
    args = parser.parse_args()

    period = "week" if args.week else "month"
    print(json.dumps(summarize(period)))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
