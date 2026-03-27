---
name: podcast-summary
description: >
  Podcast episode summarization and discovery. Use when user asks for a summary of a specific
  podcast episode, wants to find an episode on a topic, requests a longer/shorter version of
  a summary, asks to add or remove a podcast from monitoring, or wants to update health store.
metadata:
  openclaw:
    emoji: "🎙️"
    requires:
      bins: ["bash", "python3", "curl"]
---

# Podcast Summary — EXECUTE THE MATCHING INTENT PIPELINE IN ORDER. DO NOT SKIP ANY STEP.

## Important: Nightly Batch vs On-Demand

The nightly digest (RSS polling, batch summarization, email digest) is handled automatically by
`engine.py` running on the Mac host via system crontab. **Do NOT attempt to trigger the nightly
batch from this skill.** This skill handles ON-DEMAND requests only.

All summaries are delivered via email — never inline in chat.

---

## Intent Classification

Read the user's message and identify which intent applies:

| Intent | Trigger examples |
|--------|-----------------|
| 1 — Specific episode | "summarize Peter Attia episode 224", "give me a summary of this URL: ..." |
| 2 — Topic search | "find a recent Huberman episode on the vagus nerve", "any Attia episodes on sleep?" |
| 3 — Re-summarize / extend | "give me more detail on that episode", "longer summary of X", "short version of Y" |
| 4 — Add / remove podcast | "add podcast X", "stop monitoring Y", "just get one episode from Z", "monitor this feed" |
| 5 — Style correction | "that summary was too short", "use science format for Attia", "give more detail on Huberman" |
| 6 — Health store override | "add that episode to health store", "that Hunt Backcountry episode was health-related" |

---

## Intent 1 — Specific Episode Summary

**Trigger:** User names a specific episode by show + number, title, or pastes a URL.

**Reply immediately. Do NOT run on_demand.py — the podcast watcher handles transcription independently.**

### STEP 1 — Reply to user immediately

Reply naturally with 2-4 sentences using what you know about this episode or show. Give the user a genuine brief preview — the episode topic, key theme, or what it covers. Then add one sentence noting a full detailed summary with timestamps is on its way to their email.

Do NOT include Apple Podcasts links or any other links. Do NOT run on_demand.py — the background pipeline handles transcription independently.

### STEP 2 — Log the request (exec, mandatory)

```
exec: bash /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/podcast-log.sh on_demand_start show="{show}" episode="{ep}"
```

---

## Intent 2 — Topic Search Summary

**Trigger:** Vague request naming a show and a topic, but not a specific episode.

**This is a two-tier pipeline. Both tiers MUST run.**

### STEP 1 — Log the request (exec, mandatory)

```
exec: bash /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/podcast-log.sh on_demand_start show="{show}" episode="topic:{keywords}"
```

### STEP 2 — Quick Telegram reply (reply, mandatory)

Tell the user: "Searching [Show] for episodes about '[topic]' — running Whisper transcription on the best match. Full summary coming to your email."

**Do NOT stop here. Step 3 is mandatory.**

### STEP 3 — Full transcription + email summary (exec, mandatory)

```
exec: python3 /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/on_demand.py --query "{show name} {keywords}" --agent sample-agent
```

### STEP 4 — Log completion (exec, mandatory)

```
exec: bash /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/podcast-log.sh on_demand_done show="{show}" episode="topic:{keywords}"
```

---

## Intent 3 — Re-Summarize / Extend

**Trigger:** User asks for more detail, a longer version, or a shorter version of an episode already
summarized.

### STEP 1 — Log the request (exec, mandatory)

```
exec: bash /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/podcast-log.sh on_demand_start episode="{id}" strategy="extended"
```

### STEP 2 — Run on_demand.py with extended depth (exec, mandatory)

```
exec: python3 /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/on_demand.py --query "{episode title or number}" --depth extended --agent sample-agent
```

### STEP 3 — Log completion and confirm to user (exec + reply, mandatory)

```
exec: bash /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/podcast-log.sh on_demand_done episode="{id}" strategy="extended"
```

Tell the user: "Extended summary queued — it will be in your email shortly."

---

## Intent 4 — Add / Remove Podcast

**Trigger:** User wants to add a new podcast to monitoring, stop monitoring a show, or get a
one-off episode from a show.

### STEP 1 — Log the request (exec, mandatory)

```
exec: bash /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/podcast-log.sh on_demand_start show="{name}" strategy="feed_management"
```

### STEP 2 — Run add_feed.py (exec, mandatory)

To add a new feed:
```
exec: python3 /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/add_feed.py --title "{show name}" --url "{rss url}"
```

To stop monitoring (run `--list` first to find the feed ID):
```
exec: python3 /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/add_feed.py --list
exec: python3 /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/add_feed.py --id "{feed_id}" --state inactive
```

To get just one episode (run `--list` first to find the feed ID):
```
exec: python3 /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/add_feed.py --list
exec: python3 /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/add_feed.py --id "{feed_id}" --state one-off
```

### STEP 3 — Confirm to user (reply, mandatory)

Relay the confirmation from add_feed.py output. Example:
- Added: "Done — [Show] added to monitoring. It will be included in tomorrow's digest."
- Removed: "Done — [Show] removed from monitoring."
- One-off: "Done — I'll grab one episode from [Show] on the next nightly run."

---

## Intent 5 — Style Correction

**Trigger:** User says a summary was "too short", "too long", "use science format", "give more detail
on [show]", or similar style feedback about a specific show.

### STEP 1 — Log the request (exec, mandatory)

```
exec: bash /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/podcast-log.sh on_demand_start show="{show}" strategy="style_correction"
```

### STEP 2 — Find feed ID (exec, mandatory)

```
exec: python3 /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/add_feed.py --list
```

### STEP 3 — Apply style (exec, mandatory)

```
exec: python3 /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/add_feed.py --id "{feed_id}" --style "{style}" --agent sample-agent
```

Note: `--style` flag is not yet implemented. Once available, supported values will be styles such as
`science`, `narrative`, `bullets`. Skip this step and inform the user if add_feed.py reports an
unknown flag.

### STEP 4 — Confirm to user (reply, mandatory)

Tell the user: "Got it — I'll use [style] format for [show] going forward."

---

## Intent 6 — Health Store Override

**Trigger:** User explicitly asks to add a specific episode to the health knowledge store,
overriding the automatic health tier classification.

### STEP 1 — Log the request (exec, mandatory)

```
exec: bash /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/podcast-log.sh health_tag episode="{id}" source_quality="user"
```

### STEP 2 — Run health_store_cmd.py (exec, mandatory)

```
exec: python3 /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/health_store_cmd.py --episode-id "{id}" --tagged-by user
```

### STEP 3 — Confirm to user (reply, mandatory)

Tell the user: "Added to health store — [Episode] is now in your health knowledge archive."

---

## Hard Rules

1. **NEVER skip on_demand.py.** A quick web preview does not replace Whisper transcription. Both must run.
2. **NEVER return a full summary inline in chat.** Detailed summaries go via email. A 2-3 sentence Telegram preview is fine; a full summary is not.
3. **NEVER attempt to trigger the nightly batch digest.** It runs automatically via system crontab.
4. **If on_demand.py fails or returns an error**, tell the user:
   "I had trouble finding that episode — try providing the RSS URL directly."
5. **Zero narration between steps.** Do not say "Let me look that up" or "I'm searching now." Just execute.
6. **Scripts run at:** `/home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/`
7. **Vault files are at:** `/home/openclaw/.openclaw/workspace/skills/podcast-summary/podcast_vault/`
