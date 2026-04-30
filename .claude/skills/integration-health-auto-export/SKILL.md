---
name: integration-health-auto-export
description: Use when integrating Health Auto Export iOS app, configuring automations for iCloud Drive sync, parsing the exported JSON/hae files, or debugging why metrics aren't appearing in exports.
user-invocable: false
---

# Health Auto Export (Liftcode) iOS App Integration

**Trigger**: Health Auto Export, iCloud Drive health sync, hae file, HealthAutoExport json, State of Mind export
**Confidence**: high
**Created**: 2026-04-30
**Updated**: 2026-04-30
**Version**: 1

## What it is

Health Auto Export (by Lybron Sobers, App Store) exports Apple Health data to iCloud Drive
as JSON on a schedule. Used in ironclaw as the automated daily sync path replacing manual
Apple Health XML exports. Privacy: "Data Not Collected" confirmed.

## Critical Architecture: Two Automation Types

The app has TWO distinct automation behaviors:

### Type 1: Named Automations (what we use)
Configure Data Type → name the automation → files land in their own iCloud folder.

**Output**: `iCloud Drive/Auto Export/{automation-name}/HealthAutoExport-YYYY-MM-DD.json`
**Mac path**: `~/Library/Mobile Documents/com~apple~CloudDocs/Auto Export/{automation-name}/`
**Format**: Combined JSON with all selected metrics for that date range.

### Type 2: AutoSync (per-metric .hae files)
Older mechanism. Produces per-metric subfolders under `AutoSync/HealthMetrics/`.
**Files are .hae format** (LZFSE-compressed JSON, not plain JSON). Do not use this path.

## JSON Schema (Named Automation, Export Version v2)

### Health Metrics file
```json
{
  "data": {
    "metrics": [
      {
        "name": "step_count",
        "units": "count",
        "data": [
          {"date": "2026-04-30 00:26:00 -0600", "qty": 31, "source": "trout watch"}
        ]
      },
      {
        "name": "heart_rate",
        "units": "count/min",
        "data": [
          {"Min": 55, "Avg": 55, "Max": 55, "date": "...", "source": "..."}
        ]
      }
    ]
  }
}
```
- `date` field: `"YYYY-MM-DD HH:MM:SS ±HHMM"` — extract date with `entry["date"][:10]`
- `qty` field for most metrics; `Min/Avg/Max` for heart_rate
- No `workouts` key in this format — workouts are separate `.hae` files in AutoSync

### State of Mind file (separate automation required)
```json
{
  "data": {
    "stateOfMind": [
      {
        "kind": "momentary_emotion",
        "labels": ["excited"],
        "associations": ["hobbies", "tasks"],
        "id": "BB2B3CB5-...",
        "start": "2026-04-30T18:26:49Z",
        "end": "2026-04-30T18:26:49Z",
        "valence": 0.272,
        "valenceClassification": "slightly_pleasant"
      }
    ]
  }
}
```
- `start`/`end`: ISO 8601 UTC. Extract date: `entry["start"][:10]`
- No `arousal` field — Apple does not export it despite HealthKit having it
- `kind`: `"daily_mood"` or `"momentary_emotion"` (string, not int)

## State of Mind Requires a SEPARATE Automation

**Critical**: State of Mind is NOT a health metric and does NOT appear in "Select Health Metrics."
It requires its own automation in the app with Data Type = "State of Mind".

Setup:
1. Create automation: Data Type = "Health Metrics", Automation Type = iCloud Drive → named `Health DB export`
2. Create automation: Data Type = "State of Mind", Automation Type = iCloud Drive → named `State of Mind`
3. iOS Shortcuts: 6:00 AM runs Health DB export, 6:05 AM runs State of Mind
4. The 5-minute gap allows the user to log their morning State of Mind before the export fires

## METRIC_MAP (confirmed metric names as of 2026-04-30)

| Metric name in JSON | Maps to | Notes |
|---------------------|---------|-------|
| `step_count` | `activity_daily.steps` | Sum by date |
| `time_in_daylight` | `activity_daily.daylight_minutes` | Sum by date (minutes) |
| `body_mass` | `body_metrics.weight_lbs` | US locale = lbs |
| `body_fat_percentage` | `body_metrics.fat_ratio` | May be decimal (0.185) or percent (18.5) |
| `lean_body_mass` | `body_metrics.lean_mass_lbs` | lbs |
| `active_energy` | (no column yet) | kcal, summed by date |

Unrecognized metric names are silently skipped — update `METRIC_MAP` at top of `scripts/import-apple-health-json.py`.

## iCloud Drive Terminal Access

Terminal.app lacks Full Disk Access by default and **cannot** read iCloud Drive directly.
`ls ~/Library/Mobile Documents/...` returns "Operation not permitted" in Terminal.

**Workaround**: launchd-spawned scripts run as the user account directly and bypass
this restriction. The watcher (`watch-health-import.sh`) works fine even though Terminal can't.

To grant Terminal access: System Settings → Privacy & Security → Full Disk Access → Terminal.

## Key Files
- `scripts/import-apple-health-json.py` — JSON importer (METRIC_MAP at top, easy to update)
- `scripts/watch-health-import.sh` — launchd watcher, scans both automation folders
- `scripts/launchagents/com.ironclaw.health-watch.plist` — WatchPaths plist, watches parent `Auto Export/`
- `docs/ios-shortcut-guide.md` — step-by-step setup guide for Health Auto Export + Shortcuts
