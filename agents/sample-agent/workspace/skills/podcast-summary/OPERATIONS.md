# Podcast Summary Skill — Operations Runbook

This document covers initial setup, configuration, and ongoing operations for the
`podcast-summary` skill. The batch engine runs on the Mac host (not inside Docker).
The OpenClaw agent handles only on-demand requests and the 6AM notification.

---

## Prerequisites

Before starting, ensure the following are installed on the Mac host:

| Dependency | Install | Notes |
|------------|---------|-------|
| Xcode Command Line Tools | `xcode-select --install` | Required for cmake |
| cmake | `brew install cmake` | Version 3.20+ recommended |
| ninja | `brew install ninja` | Faster builds |
| git | `brew install git` | Required to clone whisper.cpp |
| ffmpeg | `brew install ffmpeg` | Required for audio conversion (`--convert` flag) |
| Python 3.6+ | Pre-installed on macOS | Use `python3 --version` to verify |

**Platform requirement:** macOS with Apple Silicon (M1/M2/M3) for Metal GPU acceleration.
Whisper will run on Intel Mac but significantly slower (CPU only).

---

## Step 1: Build whisper.cpp

Run the setup script to clone, build, and download models:

```bash
# From the ironclaw repo root
bash agents/sample-agent/workspace/skills/podcast-summary/scripts/setup-whisper-bridge.sh
```

The script accepts optional arguments:
```bash
bash setup-whisper-bridge.sh \
  --whisper-dir /opt/whisper.cpp \     # where to clone (default: ~/whisper.cpp)
  --model-dir /opt/whisper.cpp/models \  # where to store models (default: <whisper-dir>/models)
  --port 18797                           # bridge port (default: 18797)
```

What the script does:
1. Clones `https://github.com/ggerganov/whisper.cpp` if not already present
2. Runs `cmake -G Ninja -B build -DCMAKE_BUILD_TYPE=Release`
3. Runs `cmake --build build --config Release --target whisper-server`
4. Downloads `ggml-small.en.bin` (default model, ~150MB)
5. Downloads `ggml-large-v3.bin` (health show model, ~3GB)
6. Installs the LaunchAgent plist to `~/Library/LaunchAgents/com.ironclaw.whisper-bridge.plist`
7. Loads the LaunchAgent: `launchctl load ~/Library/LaunchAgents/com.ironclaw.whisper-bridge.plist`

To manually load/unload the bridge:
```bash
launchctl load ~/Library/LaunchAgents/com.ironclaw.whisper-bridge.plist
launchctl unload ~/Library/LaunchAgents/com.ironclaw.whisper-bridge.plist
```

---

## Step 2: Verify Whisper Bridge

After setup, confirm the bridge is running:

```bash
bash agents/sample-agent/workspace/skills/podcast-summary/scripts/check-whisper-bridge.sh
```

Expected output:
```
[OK] whisper-server process is running (PID 12345)
[OK] Bridge responding at http://localhost:18797
[OK] small.en model loaded
```

Manual check:
```bash
curl -s http://localhost:18797/ | head -5
# Expected: HTML page with whisper.cpp web UI
```

If the bridge is not responding:
- Check `launchctl list | grep whisper` — should show `com.ironclaw.whisper-bridge`
- Check logs: `cat /tmp/whisper-bridge.log`
- Verify the binary path in the plist matches actual build output:
  `ls ~/whisper.cpp/build/bin/whisper-server`

---

## Step 3: Import Your Podcast Feeds

Export your podcast feeds from Overcast (or any app) as an OPML file, then import:

```bash
python3 agents/sample-agent/workspace/skills/podcast-summary/scripts/importer.py \
  /path/to/your/feeds.opml
```

What importer.py does:
- Parses all `<outline type="rss">` entries in the OPML
- Extracts show name (`text` attribute) and RSS URL (`xmlUrl` attribute)
- Creates `podcast_vault/feeds.json` with all feeds set to `state: "active"`
- Merge-safe: re-running with an updated OPML adds new feeds without overwriting existing state

Dry run (see what would be imported without writing):
```bash
python3 importer.py /path/to/feeds.opml --dry-run
```

After import, manually update health tier and whisper model for known health shows in
`podcast_vault/feeds.json`. The following shows should be pre-configured:

| Show | health_tier | whisper_model |
|------|-------------|---------------|
| The Peter Attia Drive | always | large-v3 |
| Huberman Lab | always | large-v3 |
| FoundMyFitness Member's Feed | always | large-v3 |
| Valley to Peak Nutrition Podcast | always | null (default) |
| Barbell Shrugged | always | null (default) |
| Better Brain Fitness | always | null (default) |
| The Tim Ferriss Show | sometimes | null (default) |
| The Shawn Ryan Show | sometimes | null (default) |

---

## Step 4: Configure .env Keys

Add the following keys to `agents/sample-agent/.env`. Never commit real values to git.

### Phase 1 + 2 Keys (required before first nightly run)

| Key | Description | Example |
|-----|-------------|---------|
| `WHISPER_BRIDGE_URL` | URL of the whisper-server bridge | `http://localhost:18797` |
| `WHISPER_MODEL_DIR` | Absolute path to whisper.cpp models directory | `/Users/jeff/whisper.cpp/models` |
| `PODCAST_DIGEST_TO_EMAIL` | Email address to receive the nightly digest | `jeff@example.com` |
| `PODCAST_NOTIFICATION_CHAT_ID` | Telegram chat ID for the 6AM nudge notification | `123456789` |
| `PODCAST_SUMMARY_MODEL` | OpenAI model for summarization | `gpt-4o-mini` |

Note: `OPENAI_API_KEY`, `SMTP_FROM_EMAIL`, and `GMAIL_APP_PASSWORD` are reused from the
existing agent configuration — no duplication needed.

### Phase 3 Keys (required for Gmail newsletter ingestion)

| Key | Description | Notes |
|-----|-------------|-------|
| `GMAIL_IMAP_EMAIL` | Your Gmail address for IMAP access | Same account as SMTP sender |
| `GMAIL_IMAP_APP_PASSWORD` | Gmail App Password for IMAP | **App Password only** — not your regular Google password. Generate at myaccount.google.com → Security → App passwords |
| `ATTIA_SITE_EMAIL` | Email for peterattia.com member login | For fetching full newsletter content |
| `ATTIA_SITE_PASSWORD` | Password for peterattia.com | Stored in .env, never in code |
| `FOUNDMYFITNESS_SITE_EMAIL` | Email for foundmyfitness.com member login | For fetching full newsletter content |
| `FOUNDMYFITNESS_SITE_PASSWORD` | Password for foundmyfitness.com | Stored in .env, never in code |

---

## Step 5: Install System Crontab

The nightly batch engine must run via system crontab on the Mac host (not inside Docker).

Open your crontab:
```bash
crontab -e
```

Add this entry (replace paths with your actual paths):
```
0 23 * * * /usr/bin/python3 /Users/jeff/ironclaw/agents/sample-agent/workspace/skills/podcast-summary/scripts/engine.py >> /Users/jeff/ironclaw/agents/sample-agent/logs/podcast-engine-$(date +\%Y\%m\%d).log 2>&1
```

What this does:
- Runs every night at 11:00 PM
- Polls all active RSS feeds for new episodes
- Acquires transcripts, generates summaries, sends email digest
- Logs output (stdout + stderr) to a dated file in the agent's logs directory

Verify the entry is installed:
```bash
crontab -l
```

Test with dry-run (Step 7) before relying on the nightly cron.

---

## Step 6: Enable Skill in OpenClaw

Add the skill to `agents/sample-agent/config/openclaw.json` in the `skills.entries` array:

```json
{
  "skills": {
    "entries": [
      {
        "name": "podcast-summary",
        "enabled": true
      }
    ]
  }
}
```

Only modify `skills.entries` — do not add any other keys. Per the project constraints,
unsupported schema keys cause validation warnings on every restart.

Restart the agent to pick up the new skill:
```bash
./scripts/compose-up.sh sample-agent -d
```

---

## Step 7: Test Dry Run

Before the first real nightly run, verify everything is wired correctly:

```bash
python3 agents/sample-agent/workspace/skills/podcast-summary/scripts/engine.py --dry-run
```

Expected output:
- Lists all active feeds found in `podcast_vault/feeds.json`
- For each feed: shows new episodes found since last check (or "no new episodes")
- Prints what would be summarized and emailed
- Writes nothing, sends nothing

If feeds.json is empty or missing, run the importer first (Step 3).

---

## Gmail Setup (Phase 3)

Before enabling Gmail newsletter ingestion, set up the label and filter in Gmail:

### Create the health-newsletters label

1. In Gmail, go to Settings → Labels → Create new label
2. Name it: `health-newsletters`
3. Check "Show in IMAP" — this is required for IMAP polling to find it

### Create a filter for newsletters

1. In Gmail, go to Settings → Filters and Blocked Addresses → Create a new filter
2. From: `newsletter@peterattia.com` OR `rhonda@foundmyfitness.com`
   (adjust to match the actual sender addresses of your newsletters)
3. Action: Apply label `health-newsletters`, skip inbox (optional)
4. Click "Create filter"

The nightly engine will poll for UNSEEN emails in this label, process them, and move them to
Gmail Trash after successful processing. Emails are never permanently deleted — they remain
in Trash for 30 days per Gmail's policy.

---

## Adding a New Podcast

Via Telegram, send one of these messages to the agent:

```
Monitor this podcast: [Show Name] — [RSS URL]
Add podcast: [Show Name]
Stop monitoring: [Show Name]
Just get one episode from: [Show Name]
```

The agent will call `add_feed.py` and confirm. The change takes effect on the next nightly run.

You can also add directly by editing `podcast_vault/feeds.json` and adding an entry:
```json
{
  "id": "show-slug",
  "title": "Show Name",
  "rss_url": "https://feeds.example.com/show.rss",
  "state": "active",
  "summary_style": null,
  "health_tier": "never",
  "whisper_model": null,
  "transcript_strategy": []
}
```

---

## Troubleshooting

### Whisper bridge not responding

```bash
# Check if process is running
launchctl list | grep whisper

# Check the log
cat /tmp/whisper-bridge.log

# Manually start to see errors
~/whisper.cpp/build/bin/whisper-server \
  -m ~/whisper.cpp/models/ggml-small.en.bin \
  --host 0.0.0.0 --port 18797 --convert -t 8

# Reload the LaunchAgent
launchctl unload ~/Library/LaunchAgents/com.ironclaw.whisper-bridge.plist
launchctl load ~/Library/LaunchAgents/com.ironclaw.whisper-bridge.plist
```

### Email digest not arriving

1. Check the cron log: `cat agents/sample-agent/logs/podcast-engine-$(date +%Y%m%d).log`
2. Check `PODCAST_DIGEST_TO_EMAIL` is set correctly in `.env`
3. Verify `SMTP_FROM_EMAIL` and `GMAIL_APP_PASSWORD` are correct (same keys used by send-email skill)
4. Check spam folder — first digest may be filtered
5. Test SMTP directly: `python3 workspace/skills/send-email/scripts/send_email.py --to you@example.com --subject "Test" --body "Test"`

### Feed failing (401/403/timeout)

1. Check `podcast_vault/feeds.json` for the feed's `last_error` field after a failed run
2. Private feeds (Attia Supercast, FoundMyFitness): verify the private RSS URL is still valid
   in your podcast app — these URLs rotate occasionally
3. Update the `rss_url` field directly in `podcast_vault/feeds.json`
4. Set `state: "inactive"` for any feed you want to stop monitoring

### Cron not running

1. Verify: `crontab -l` — entry should be visible
2. Check macOS cron permissions: System Settings → Privacy & Security → Full Disk Access → cron
3. Test by running the script manually with the exact command from crontab
4. Check `/var/log/system.log` for cron errors: `grep CRON /var/log/system.log | tail -20`
