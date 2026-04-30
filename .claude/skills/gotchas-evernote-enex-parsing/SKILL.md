---
name: gotchas-evernote-enex-parsing
description: Use when parsing Evernote ENEX exports, debugging why notes aren't being found, fixing date derivation for workout notes, or writing tests for the Evernote importer.
user-invocable: false
---

# Evernote ENEX Parsing — Gotchas

**Trigger**: Evernote, ENEX, enex parse, workout notes, note export, created date, CDATA, DOCTYPE, Week training plan
**Confidence**: high
**Created**: 2026-04-30
**Updated**: 2026-04-30
**Version**: 1

## The `<created>` date is the template creation date, not when you filled it in

ENEX `<created>` timestamps reflect when the note was *originally created*,
not when it was last updated. For recurring template notes (weekly training
plans), every note shows the same creation date (e.g. 2022-11-18) even if
the content was written in 2026.

**Fix**: derive the actual date from the note *title*, not from `<created>`.
Our workout notes encode the date as `"Week WWYY Training Plan"` where WW =
ISO week number and YY = 2-digit year:

```python
import re
from datetime import date

m = re.search(r'Week (\d{2})(\d{2})', title, re.IGNORECASE)
week_num, year = int(m.group(1)), 2000 + int(m.group(2))
monday = date.fromisocalendar(year, week_num, 1)
# "Week 0125" → ISO week 1 of 2025 → date(2024, 12, 30)
# "Week 1826" → ISO week 18 of 2026 → date(2026, 4, 27)
```

**Gotcha**: ISO week 1 of 2025 starts December 30, 2024 — not January 6.
`date.fromisocalendar(2025, 1, 1)` returns `date(2024, 12, 30)`.

## DOCTYPE causes `xml.etree.ElementTree` to fail or make network requests

ENEX files include a DOCTYPE declaration referencing Evernote's DTD:
```xml
<!DOCTYPE en-export SYSTEM "http://xml.evernote.com/pub/evernote-export4.dtd">
```

Strip it before parsing — ET can't resolve external DTDs and will either
fail or hang trying to fetch the URL:

```python
import re
import xml.etree.ElementTree as ET

text = Path(filepath).read_text(encoding="utf-8")
text = re.sub(r"<!DOCTYPE[^>]*>", "", text)
root = ET.fromstring(text)
```

## `<content>` is CDATA-wrapped ENML — two-pass parse required

The note body is ENML (XHTML subset) wrapped in a CDATA block:
```xml
<content><![CDATA[<?xml version="1.0"?>
<en-note><table>...</table></en-note>]]></content>
```

`ET.findtext("content")` returns the CDATA string as plain text (ET strips
the CDATA markers). Parse that string a second time with `html.parser` to
extract table rows:

```python
from html.parser import HTMLParser

content_html = note.findtext("content") or ""
parser = TableParser()  # subclass of HTMLParser
parser.feed(content_html)
rows = parser.rows
```

## Day cells contain concatenated day name + date

The day column in the workout table looks like `"Monday12/30"` or
`"Tuesday1/1sauna"` — not just the day name. Use regex to extract:

```python
day_match = re.match(
    r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday)',
    row[0].strip().lower()
)
if not day_match:
    continue  # header row or unrecognized
day_name = day_match.group(1)
```

## CDATA in test XML must be written as a CDATA section

When writing synthetic ENEX in tests, the `<content>` value must use CDATA
notation — otherwise ET parses the ENML as child XML elements and
`findtext("content")` returns `None`:

```python
# CORRECT — ET reads CDATA as text content
f"<content><![CDATA[<en-note><table>...</table></en-note>]]></content>"

# WRONG — ET parses <en-note> as a child element, not text
f"<content><en-note><table>...</table></en-note></content>"
```

## Export flow

Evernote API access has been discontinued for new applications. Export is
manual only:
1. Tag all workout notes in Evernote
2. Filter by tag, select all, File → Export Notes → ENEX format
3. Pass to importer: `python3 scripts/import-evernote-workouts.py --file workouts.enex`

Export is idempotent — re-importing deletes and rewrites exercises for each
workout_date, so running twice is safe.

## Key files

- `scripts/import-evernote-workouts.py` — the importer
- `scripts/test_evernote_workouts.py` — 40 behavioral tests
- `agents/sample-agent/workspace/health/health_db.py` — workout_exercises schema
