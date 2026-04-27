#!/usr/bin/env python3
"""
Unit tests for cost_summary.py

Run with:
    python3 -m unittest test_cost_summary -v
"""

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

# Ensure cost_summary is importable from this directory
sys.path.insert(0, str(Path(__file__).parent))
import cost_summary


def _write_jsonl(path: Path, records: list) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _set_mtime(path: Path, d: date) -> None:
    """Set file mtime to noon on the given date."""
    ts = time.mktime(d.timetuple())
    os.utime(path, (ts, ts))


class TestWeekRange(unittest.TestCase):
    def test_wednesday_gives_monday_start(self):
        wednesday = date(2026, 4, 22)
        start, end = cost_summary._week_range(wednesday)
        self.assertEqual(start, date(2026, 4, 20))  # Monday

    def test_wednesday_gives_sunday_end(self):
        wednesday = date(2026, 4, 22)
        start, end = cost_summary._week_range(wednesday)
        self.assertEqual(end, date(2026, 4, 26))  # Sunday

    def test_monday_is_own_start(self):
        monday = date(2026, 4, 20)
        start, _ = cost_summary._week_range(monday)
        self.assertEqual(start, monday)

    def test_sunday_is_own_end(self):
        sunday = date(2026, 4, 26)
        _, end = cost_summary._week_range(sunday)
        self.assertEqual(end, sunday)

    def test_week_span_is_always_seven_days(self):
        for day_offset in range(7):
            d = date(2026, 4, 20) + timedelta(days=day_offset)
            start, end = cost_summary._week_range(d)
            self.assertEqual((end - start).days, 6)


class TestMonthRange(unittest.TestCase):
    def test_start_is_first_of_month(self):
        mid_month = date(2026, 4, 22)
        start, _ = cost_summary._month_range(mid_month)
        self.assertEqual(start, date(2026, 4, 1))

    def test_end_is_today(self):
        mid_month = date(2026, 4, 22)
        _, end = cost_summary._month_range(mid_month)
        self.assertEqual(end, mid_month)

    def test_first_of_month_is_single_day_range(self):
        first = date(2026, 4, 1)
        start, end = cost_summary._month_range(first)
        self.assertEqual(start, end)


class TestSessionsDirEnvVar(unittest.TestCase):
    def test_env_var_overrides_inferred_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OPENCLAW_SESSIONS_DIR": tmp}):
                result = cost_summary._sessions_dir()
            self.assertEqual(result, Path(tmp))

    def test_env_var_path_returned_even_if_nonexistent(self):
        nonexistent = "/nonexistent/path/sessions"
        with patch.dict(os.environ, {"OPENCLAW_SESSIONS_DIR": nonexistent}):
            result = cost_summary._sessions_dir()
        self.assertEqual(result, Path(nonexistent))


class TestMissingSessionsDir(unittest.TestCase):
    def test_missing_dir_outputs_error_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does_not_exist"
            with patch.dict(os.environ, {"OPENCLAW_SESSIONS_DIR": str(missing)}):
                with self.assertRaises(SystemExit) as ctx:
                    cost_summary.summarize("week")
        self.assertEqual(ctx.exception.code, 1)

    def test_missing_dir_prints_error_key(self, capsys=None):
        """summarize exits with code 1 — error output is via print, not exception message."""
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does_not_exist"
            with patch.dict(os.environ, {"OPENCLAW_SESSIONS_DIR": str(missing)}):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    with self.assertRaises(SystemExit):
                        cost_summary.summarize("week")
        output = json.loads(buf.getvalue())
        self.assertIn("error", output)


class TestCostAggregation(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.sessions_dir = Path(self.tmp_dir)
        # Pin "today" to a known Wednesday inside the week we'll use for mtimes
        self.today = date(2026, 4, 22)  # Wednesday

    def _patch(self):
        """Return a context manager stack that patches both _sessions_dir and date.today."""
        sessions_patch = patch.object(cost_summary, "_sessions_dir", return_value=self.sessions_dir)
        today_patch = patch("cost_summary.date")
        return sessions_patch, today_patch

    def _summarize(self, period: str) -> dict:
        with patch.object(cost_summary, "_sessions_dir", return_value=self.sessions_dir):
            with patch("cost_summary.date") as mock_date:
                mock_date.today.return_value = self.today
                mock_date.fromtimestamp.side_effect = date.fromtimestamp
                result = cost_summary.summarize(period)
        return result

    def _make_session(self, filename: str, records: list, on_date: date) -> Path:
        f = self.sessions_dir / filename
        _write_jsonl(f, records)
        _set_mtime(f, on_date)
        return f

    def test_sums_cost_total_from_assistant_messages(self):
        records = [
            {"type": "message", "message": {"role": "assistant", "usage": {"cost": {"total": 0.0042}}}},
            {"type": "message", "message": {"role": "assistant", "usage": {"cost": {"total": 0.0010}}}},
            {"type": "message", "message": {"role": "assistant", "usage": {"cost": {"total": 0.0008}}}},
        ]
        self._make_session("session1.jsonl", records, self.today)
        result = self._summarize("week")
        self.assertAlmostEqual(result["cost_usd"], round(0.0042 + 0.0010 + 0.0008, 4))

    def test_skips_user_messages(self):
        records = [
            {"type": "message", "message": {"role": "assistant", "usage": {"cost": {"total": 0.0050}}}},
            {"type": "message", "message": {"role": "user", "usage": {"cost": {"total": 99.99}}}},
        ]
        self._make_session("session2.jsonl", records, self.today)
        result = self._summarize("week")
        self.assertAlmostEqual(result["cost_usd"], 0.005)

    def test_skips_trajectory_files(self):
        records = [
            {"type": "message", "message": {"role": "assistant", "usage": {"cost": {"total": 5.0}}}},
        ]
        self._make_session("session-trajectory-abc.jsonl", records, self.today)
        result = self._summarize("week")
        self.assertEqual(result["cost_usd"], 0.0)
        self.assertEqual(result["sessions"], 0)

    def test_skips_malformed_json_lines_silently(self):
        f = self.sessions_dir / "session_bad.jsonl"
        f.write_text(
            '{"type": "message", "message": {"role": "assistant", "usage": {"cost": {"total": 0.001}}}}\n'
            "NOT VALID JSON !!!\n"
            '{"type": "session", "id": "xyz"}\n'
        )
        _set_mtime(f, self.today)
        result = self._summarize("week")
        self.assertAlmostEqual(result["cost_usd"], 0.001)

    def test_sessions_count_reflects_files_with_cost_records(self):
        # 2 files with assistant cost records, 1 without
        records_with_cost = [
            {"type": "message", "message": {"role": "assistant", "usage": {"cost": {"total": 0.001}}}},
        ]
        records_no_cost = [
            {"type": "session", "id": "abc"},
            {"type": "message", "message": {"role": "user", "usage": {"cost": {"total": 0.001}}}},
        ]
        self._make_session("s1.jsonl", records_with_cost, self.today)
        self._make_session("s2.jsonl", records_with_cost, self.today)
        self._make_session("s3.jsonl", records_no_cost, self.today)
        result = self._summarize("week")
        self.assertEqual(result["sessions"], 2)

    def test_zero_results_when_files_outside_window(self):
        # File exists but mtime is 3 weeks ago — outside the current week window
        three_weeks_ago = self.today - timedelta(weeks=3)
        records = [
            {"type": "message", "message": {"role": "assistant", "usage": {"cost": {"total": 9.99}}}},
        ]
        self._make_session("old_session.jsonl", records, three_weeks_ago)
        result = self._summarize("week")
        self.assertEqual(result["sessions"], 0)
        self.assertEqual(result["cost_usd"], 0.0)

    def test_multiple_sessions_sum_correctly(self):
        for i, cost in enumerate([0.01, 0.02, 0.03]):
            records = [
                {"type": "message", "message": {"role": "assistant", "usage": {"cost": {"total": cost}}}},
            ]
            self._make_session(f"s{i}.jsonl", records, self.today)
        result = self._summarize("week")
        self.assertEqual(result["sessions"], 3)
        self.assertAlmostEqual(result["cost_usd"], round(0.01 + 0.02 + 0.03, 4))

    def test_output_contains_required_keys(self):
        result = self._summarize("week")
        for key in ("period", "start", "end", "sessions", "cost_usd"):
            self.assertIn(key, result)

    def test_period_field_matches_requested_period(self):
        result_week = self._summarize("week")
        result_month = self._summarize("month")
        self.assertEqual(result_week["period"], "week")
        self.assertEqual(result_month["period"], "month")

    def test_week_start_date_in_output(self):
        result = self._summarize("week")
        # today=Wednesday 2026-04-22, so week starts Monday 2026-04-20
        self.assertEqual(result["start"], "2026-04-20")

    def test_week_end_date_in_output(self):
        result = self._summarize("week")
        # today=Wednesday 2026-04-22, so week ends Sunday 2026-04-26
        self.assertEqual(result["end"], "2026-04-26")

    def test_month_start_date_in_output(self):
        result = self._summarize("month")
        self.assertEqual(result["start"], "2026-04-01")

    def test_cost_usd_is_rounded_to_four_decimal_places(self):
        records = [
            {"type": "message", "message": {"role": "assistant", "usage": {"cost": {"total": 0.00001}}}},
        ]
        self._make_session("tiny.jsonl", records, self.today)
        result = self._summarize("week")
        # Should be rounded to 4 decimal places
        self.assertEqual(result["cost_usd"], round(result["cost_usd"], 4))

    def test_records_without_cost_field_do_not_crash(self):
        records = [
            {"type": "message", "message": {"role": "assistant", "usage": {}}},
            {"type": "message", "message": {"role": "assistant"}},
        ]
        self._make_session("nocost.jsonl", records, self.today)
        # Should not raise; assistant messages without cost contribute 0 but still set found=True
        result = self._summarize("week")
        self.assertEqual(result["cost_usd"], 0.0)

    def test_month_window_includes_today(self):
        records = [
            {"type": "message", "message": {"role": "assistant", "usage": {"cost": {"total": 0.005}}}},
        ]
        self._make_session("today.jsonl", records, self.today)
        result = self._summarize("month")
        self.assertGreater(result["sessions"], 0)

    def test_trajectory_in_name_excluded_regardless_of_content(self):
        """Even a valid cost-bearing trajectory file must be excluded."""
        records = [
            {"type": "message", "message": {"role": "assistant", "usage": {"cost": {"total": 100.0}}}},
        ]
        self._make_session("trajectory.jsonl", records, self.today)
        self._make_session("abc-trajectory-def.jsonl", records, self.today)
        result = self._summarize("week")
        self.assertEqual(result["cost_usd"], 0.0)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
