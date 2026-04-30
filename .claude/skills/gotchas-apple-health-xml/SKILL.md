---
name: gotchas-apple-health-xml
description: Use when importing Apple Health XML export data, debugging missing calories or workout fields, hitting macOS file permission errors, or writing tests for the Apple Health importer.
user-invocable: false
---

# Apple Health XML Export — Import Gotchas

**Trigger**: apple health, export.xml, import-apple-health, iterparse, workout calories, HK identifiers, macOS privacy
**Confidence**: high
**Created**: 2026-04-30
**Updated**: 2026-04-30
**Version**: 1

## File size — iterparse is mandatory

Apple Health exports are 200MB–2GB. Never use `ET.parse()` — it loads the
entire file into memory. Always use iterparse with `elem.clear()`:

```python
for event, elem in ET.iterparse(filepath, events=("end",)):
    if elem.tag == "Record":
        rtype = elem.get("type")
        # process...
        elem.clear()  # CRITICAL — without this, memory grows unboundedly
```

## macOS privacy blocks Python from Downloads and Desktop

Python cannot read files from `~/Downloads` or `~/Desktop` even with
Terminal Full Disk Access granted. Symptom: `PermissionError: [Errno 1]`.

**Fix**: drag the export.xml into the repo folder (or any non-protected
location) and pass it via `--file /Users/jeff/ironclaw/export.xml`.
Long-term fix: iOS Shortcut → iCloud Drive drop folder.

Export location in import script: `~/Downloads/apple_health_export/export.xml`
(fixed default, `--file PATH` overrides).

## Calories are in WorkoutStatistics, not totalEnergyBurned

The `<Workout totalEnergyBurned="...">` attribute is often absent or stale.
Real calories are in a child element:

```xml
<WorkoutStatistics
  type="HKQuantityTypeIdentifierActiveEnergyBurned"
  sum="263.4"
  unit="Cal"/>
```

Parse it from children, not from the Workout element attribute:

```python
for stat in elem.findall("WorkoutStatistics"):
    if stat.get("type") == "HKQuantityTypeIdentifierActiveEnergyBurned":
        calories = round(float(stat.get("sum", 0)))
```

## BP records are paired by matching startDate

Systolic and diastolic are separate `<Record>` elements. Pair them by
matching `startDate` strings exactly — Apple Health always writes them with
identical timestamps for the same reading:

```python
bp_systolic[elem.get("startDate")] = float(elem.get("value"))
bp_diastolic[elem.get("startDate")] = float(elem.get("value"))
# flush pairs where both exist
```

## Key HKQuantityTypeIdentifier strings

| Data | Identifier |
|------|-----------|
| Weight | `HKQuantityTypeIdentifierBodyMass` (kg) |
| Body fat % | `HKQuantityTypeIdentifierBodyFatPercentage` (decimal: 0.155 = 15.5%) |
| BP systolic | `HKQuantityTypeIdentifierBloodPressureSystolic` |
| BP diastolic | `HKQuantityTypeIdentifierBloodPressureDiastolic` |
| Steps | `HKQuantityTypeIdentifierStepCount` |
| Daylight | `HKQuantityTypeIdentifierTimeInDaylight` |

Workouts use `<Workout workoutActivityType="HKWorkoutActivityType{Name}">` —
strip the prefix for storage: `workout_type = raw.replace("HKWorkoutActivityType", "")`.

## Steps: sum per day, don't take last value

Apple Health emits multiple StepCount records per day (iPhone + Watch +
third-party apps). Sum them all; don't just use the latest:

```python
steps_by_date[date_str] = steps_by_date.get(date_str, 0) + float(value)
```

## Writing tests for the importer

`ET.iterparse` requires a real file path — it won't accept `StringIO`. Use
`tempfile.NamedTemporaryFile` with the XML content written to it:

```python
import tempfile
from pathlib import Path

def _write_tmp_xml(xml: str) -> Path:
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
        f.write(xml.encode())
        return Path(f.name)
```

## Source priority: apple_health never overwrites withings_api or omron_csv

The upsert WHERE guard only allows apple_health rows to overwrite existing
apple_health rows — not rows from higher-priority sources:

```sql
ON CONFLICT(date, time) DO UPDATE SET weight_lbs = excluded.weight_lbs
WHERE body_metrics.source IS 'apple_health'
```

## Key files

- `scripts/import-apple-health.py` — the importer
- `scripts/test_apple_health.py` — 27 behavioral tests
- `agents/sample-agent/workspace/health/health_db.py` — body_metrics, activity_daily, workouts schemas
