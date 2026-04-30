---
name: patterns-launchd-watchpaths-icloud
description: Use when auto-triggering a script when files arrive in an iCloud Drive folder, setting up file-based automation on macOS, or debugging why a launchd watcher isn't firing.
user-invocable: false
---

# launchd WatchPaths for iCloud Drive Automation

**Trigger**: launchd, WatchPaths, iCloud Drive watcher, file watcher, auto-import trigger, icloud automation
**Confidence**: high
**Created**: 2026-04-30
**Updated**: 2026-04-30
**Version**: 1

## Problem

You want a script to run automatically when a file arrives in an iCloud Drive folder — without
polling, without a daemon, without user interaction.

## Solution

Use launchd `WatchPaths` — macOS fires your script when the filesystem at the watched path
changes (file created, modified, or deleted).

### Key insight: launchd bypasses Terminal's Full Disk Access restriction

Terminal.app cannot read `~/Library/Mobile Documents/` (iCloud Drive) without Full Disk Access.
But launchd-spawned scripts run as the user account directly and have no such restriction.
This is why the watcher script works even though `ls ~/Library/Mobile Documents/` fails in Terminal.

## Plist Template

Install to `~/Library/LaunchAgents/com.ironclaw.your-watcher.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ironclaw.your-watcher</string>
    <key>WatchPaths</key>
    <array>
        <string>/Users/jeff/Library/Mobile Documents/com~apple~CloudDocs/Auto Export</string>
    </array>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/jeff/ironclaw/scripts/your-watcher.sh</string>
    </array>
    <key>StandardOutPath</key>
    <string>/Users/jeff/Library/Logs/ironclaw/your-watcher.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/jeff/Library/Logs/ironclaw/your-watcher.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

Install and load:
```bash
cp scripts/launchagents/com.ironclaw.your-watcher.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.ironclaw.your-watcher.plist
launchctl list | grep your-watcher  # should show "-  0  com.ironclaw.your-watcher"
```

Validate plist before installing: `plutil -lint com.ironclaw.your-watcher.plist`

## Watcher Script Pattern

WatchPaths fires on ANY change to the watched directory — including the archive `mv` your
script performs, iCloud metadata updates, and partial sync artifacts.

Guard against all of these:

```bash
#!/bin/bash
WATCH_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Auto Export"
LOG="$HOME/Library/Logs/ironclaw/your-watcher.log"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "$(dirname "$LOG")"

# Loop over all named automation subfolders
for SUBFOLDER in "Health DB export" "State of Mind"; do
    FOLDER="$WATCH_DIR/$SUBFOLDER"
    ARCHIVE_DIR="$FOLDER/archive"
    mkdir -p "$ARCHIVE_DIR"

    for f in "$FOLDER"/*.json; do
        [[ -f "$f" ]] || continue  # guard: glob matched nothing

        # Validate JSON before processing (handles partial iCloud sync)
        python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$f" 2>/dev/null || {
            echo "[$(date -Iseconds)] SKIP: incomplete JSON $f" >> "$LOG"
            continue
        }

        python3 "$REPO_DIR/scripts/your-importer.py" --file "$f" >> "$LOG" 2>&1
        if [[ $? -eq 0 ]]; then
            STAMP=$(date +%Y%m%d_%H%M%S)
            mv "$f" "$ARCHIVE_DIR/export_${STAMP}.json"
            echo "[$(date -Iseconds)] Archived export_${STAMP}.json" >> "$LOG"
        else
            echo "[$(date -Iseconds)] FAILED: left in place for retry" >> "$LOG"
        fi
    done
done
```

## Key Pitfalls

1. **Watch the PARENT folder, not a subfolder** — if each automation writes to its own subfolder,
   watch the parent. WatchPaths on a child folder doesn't fire for files in siblings.

2. **Archive goes INTO the watched path** — `mv` to an `archive/` subfolder triggers WatchPaths
   again. The `[[ -f "$f" ]] || continue` glob guard and the `.json` extension filter prevent
   processing the archived file again (it's already moved out of the top-level folder).

3. **iCloud delivers files in stages** — a large file may appear as 0 bytes before iCloud finishes
   syncing. JSON validation catches this: invalid/partial JSON → skip and leave for next trigger.

4. **RunAtLoad: false** — don't set this to true. The watcher fires on every system restart
   and you'd process the same archived files again. Let it trigger only on filesystem changes.

5. **Terminal can't verify** — `find ~/Library/Mobile\ Documents/...` returns nothing or errors
   in Terminal without Full Disk Access. Use `launchctl list | grep your-watcher` to confirm
   the job is loaded. Check the log file to verify it fired.

## Existing Implementation

- Plist: `scripts/launchagents/com.ironclaw.health-watch.plist`
- Script: `scripts/watch-health-import.sh`
- Watches: `~/Library/Mobile Documents/com~apple~CloudDocs/Auto Export/`
- Processes: `Health DB export/*.json` and `State of Mind/*.json`
