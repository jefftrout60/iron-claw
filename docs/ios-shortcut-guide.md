# iOS Shortcut Guide — Health Auto Export to Mac

This guide wires the **Health Auto Export** iOS app to the Mac-side launchd watcher so that
Apple Health data flows automatically to the local health database each morning.

**Flow:** iPhone (Health Auto Export) → iCloud Drive → Mac (`WatchPaths` launchd job) → `import-apple-health-json.py` → `health.db`

---

## Section 1: Install and Configure Health Auto Export

### Install

Search the App Store for **"Health Auto Export"** by **Lybron Sobers**.

> Privacy label: "Data Not Collected" — the app exports directly to iCloud Drive under your own
> Apple ID; no data goes to the developer's servers.

### Grant HealthKit permissions

On first launch the app asks which categories to read. Enable:

| Category | Why |
|----------|-----|
| Body Measurements | Weight, body fat, lean mass |
| Activity | Steps, active energy, daylight time |
| Workouts | Workout sessions with heart rate |
| Sleep | Sleep stages |
| Mindfulness | State of Mind entries |

If you skip a category now, go to **Settings → Privacy & Security → Health → Health Auto Export**
to add it later.

### Configure the export

In the Health Auto Export app:

1. Tap the **export / settings** screen (gear or export icon — exact UI varies by app version).
2. Set **Format** to **JSON**.
3. Set **Destination** to **iCloud Drive** and choose the folder name **`Health`**.
   - The app creates `iCloud Drive/Health/` if it does not exist.
4. Set **Date Range**:
   - First run: **Last 90 days** (pulls historical baseline)
   - Ongoing: **Last 7 days** (daily incremental; the importer skips rows already in the DB)

---

## Section 2: Create the iOS Shortcut Automation

### Build the Shortcut

1. Open the **Shortcuts** app on iPhone.
2. Tap the **+** (New Shortcut) button.
3. Tap **Add Action** and search for **"Health Auto Export"**.
4. Select the **Export** action from Health Auto Export.
5. Configure the action to match your export settings from Section 1 (JSON, iCloud Drive, Health folder).
6. Tap the Shortcut name at the top and rename it something like **"Health Export"**.
7. Tap **Done**.

### Create the daily automation

1. In Shortcuts, tap the **Automation** tab at the bottom.
2. Tap **+** → **New Automation**.
3. Choose **Time of Day**.
4. Set time: **6:00 AM**, repeat: **Daily**.
5. Tap **Next**.
6. Tap **Add Action** and search for your **"Health Export"** shortcut, or use **Run Shortcut**.
7. Toggle **"Ask Before Running"** to **off** (so it fires silently in the background).
8. Tap **Done**.

> **Note:** iOS may still show a notification banner when the automation runs. That is expected.
> The Shortcut itself runs without requiring any tap.

---

## Section 3: Verify First Export

Before relying on the automation, do a manual run to confirm the file path is correct.

1. Open Shortcuts → **My Shortcuts** → tap **"Health Export"** to run it manually.
2. On the Mac, open Terminal and run:

```bash
ls ~/Library/Mobile\ Documents/com~apple~CloudDocs/Health/
```

3. Within 1–2 minutes a `.json` file should appear (iCloud sync time varies; ensure both devices are on Wi-Fi).

If the folder does not exist yet, iCloud has not synced. See Section 6 Troubleshooting.

---

## Section 4: Install Mac Watcher (launchd)

Run these commands from the ironclaw repo root (typically `~/ironclaw`):

```bash
mkdir -p ~/Library/Logs/ironclaw
cp scripts/launchagents/com.ironclaw.health-watch.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.ironclaw.health-watch.plist
```

**What this does:**

- The plist registers a `WatchPaths` job with launchd.
- launchd watches `~/Library/Mobile Documents/com~apple~CloudDocs/Health/` for any filesystem change.
- When a `.json` file appears, launchd runs `scripts/watch-health-import.sh`, which validates and
  imports each file, then moves it to an `archive/` subfolder.
- Output is logged to `~/Library/Logs/ironclaw/health-watch.log`.

**Verify the job loaded:**

```bash
launchctl list | grep health-watch
```

Expected output (second column is exit status, 0 = last run succeeded or not yet run):

```
-   0   com.ironclaw.health-watch
```

---

## Section 5: Verify End-to-End

1. Run the **Health Export** Shortcut manually on iPhone.
2. Wait 1–2 minutes for iCloud to sync the file to the Mac.
3. On the Mac, tail the watcher log:

```bash
tail -f ~/Library/Logs/ironclaw/health-watch.log
```

Expected output when import succeeds:

```
[2026-04-30T06:02:14-07:00] Found: .../Health/export.json
[2026-04-30T06:02:15-07:00] Imported: 847 rows (activity_daily=365, body_metrics=120, workouts=362)
[2026-04-30T06:02:15-07:00] Archived to export_20260430_060215.json
```

If you see the "Found" line but no "Imported" line, check Section 6 for import errors.

---

## Section 6: Troubleshooting

### File not appearing in iCloud Drive

1. On iPhone: **Settings → [your name] → iCloud → iCloud Drive** — confirm iCloud Drive is on.
2. On iPhone: **Settings → [your name] → iCloud** → scroll to **Health Auto Export** — confirm it
   is toggled on for iCloud.
3. Both devices must be on Wi-Fi and have iCloud signed in with the same Apple ID.
4. Force a sync on Mac: open **Finder → iCloud Drive** and wait for the spinner to clear.

### Import failed / JSON parse error

The importer logs the error line to `health-watch.log`. Common causes:

- **Partial sync artifact** — iCloud wrote an incomplete file. The watcher skips files that fail
  JSON validation and leaves them in place. Re-run the Shortcut on iPhone; the next sync will
  overwrite or add a fresh file.
- **Schema mismatch** — metric field names in a new app version differ from `METRIC_MAP` in
  `import-apple-health-json.py`. Check the log for `UnknownMetric` warnings and update the map.

To retry manually after fixing:

```bash
python3 ~/ironclaw/scripts/import-apple-health-json.py \
  --file ~/Library/Mobile\ Documents/com~apple~CloudDocs/Health/export.json
```

### Watcher not firing

```bash
launchctl list | grep health-watch
```

- **No output** — job is not loaded. Re-run the `launchctl load` command from Section 4.
- **Non-zero exit code in second column** — last run failed. Check the log:

```bash
tail -50 ~/Library/Logs/ironclaw/health-watch.log
```

- **Job loaded but never triggered** — confirm the iCloud path on this Mac:

```bash
ls ~/Library/Mobile\ Documents/com~apple~CloudDocs/Health/
```

  If the path does not exist, iCloud Drive has not synced the folder. Create it manually and place
  a test file to verify WatchPaths fires:

```bash
mkdir -p ~/Library/Mobile\ Documents/com~apple~CloudDocs/Health/
touch ~/Library/Mobile\ Documents/com~apple~CloudDocs/Health/test.json
```

  Then confirm the watcher log shows an entry (the test file will be skipped as invalid JSON, which
  is expected — you're just verifying WatchPaths fires).

### Unloading or reloading the job

```bash
# Unload
launchctl unload ~/Library/LaunchAgents/com.ironclaw.health-watch.plist

# Reload after editing the plist
launchctl unload ~/Library/LaunchAgents/com.ironclaw.health-watch.plist
cp ~/ironclaw/scripts/launchagents/com.ironclaw.health-watch.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.ironclaw.health-watch.plist
```
