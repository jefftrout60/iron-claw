# Redundant Local Imports — Investigation Report

**Files**: whisper_client.py, on_demand.py
**Date**: 2026-03-27

## Finding #1 — `whisper_client.py` L92: `import os as _os` inside `_load_env()`

**Evidence:**
- Module-level `import os` at L21
- Local `import os as _os` at L92 (first line of `_load_env()`)
- `_os` used exactly **twice**, both on L98:
  ```python
  env_from_environ = {k: _os.environ[k] for k in _KNOWN_KEYS if k in _os.environ}
  ```
- Module-level `os` is never shadowed. Rest of file uses `os.environ.get(...)` directly at L397, L403.
- Local import does not reimport; creates a local name bound to the same object.

**Verdict: SAFE_TO_REMOVE**
- Remove line 92: `import os as _os`
- Replace `_os.environ[k]` and `k in _os.environ` on L98 with `os.environ[k]` and `k in os.environ`
- **Confidence: 100%**

---

## Finding #2 — `on_demand.py` L557: `import re as _re` inside `main()`

**Evidence:**
- Module-level `import re` at L20
- Local `import re as _re` at L557, inside `main()`
- `_re` used exactly **once**, on L558:
  ```python
  _ep_match = _re.search(r"\b(\d{1,4})\b", args.query)
  ```
- Module-level `re` used extensively throughout the file (L89, L91, L94, L108, L122-124, L171, L177, L189, L233, L480, L483). Never reassigned or shadowed.

**Verdict: SAFE_TO_REMOVE**
- Remove line 557: `import re as _re`
- Replace `_re.search(r"\b(\d{1,4})\b", args.query)` on L558 with `re.search(...)`
- **Confidence: 100%**

---

## Scan: Other Local Imports in Both Files

All other deferred imports in `on_demand.py` are **intentional — KEEP**:

| Line | Import | Reason |
|------|--------|--------|
| L168 | `import rss_poller` | Sibling module, deferred to avoid startup-time failures |
| L297 | `import vault` | Same pattern |
| L298 | `import transcript_fetcher` | Same |
| L299 | `import summarizer` | Same |
| L300 | `import digest_emailer` | Same |
| L446 | `import health_store` | Conditional — only imported on health-store path |

No other non-top-level imports in `whisper_client.py`.

---

## Summary

| File | Line | Import | Category |
|------|------|--------|----------|
| `whisper_client.py` | 92 | `import os as _os` | SAFE_TO_REMOVE |
| `on_demand.py` | 557 | `import re as _re` | SAFE_TO_REMOVE |
| `on_demand.py` | 168, 297–300, 446 | sibling modules | KEEP |
