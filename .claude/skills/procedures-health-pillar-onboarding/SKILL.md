---
name: procedures-health-pillar-onboarding
description: Use when adding a new personal health data type to health.db (blood pressure, DEXA, macros, workouts, etc.) and wiring it so the agent queries it correctly via iMessage
user-invocable: false
---

# Procedure: Adding a New Health Data Pillar

**Trigger**: add health pillar, new health data, blood pressure, DEXA, macros, workouts, new oura endpoint, new lab type, health db table, health_query.py subcommand, agents.md health rule, model ignores exec, agent answers from memory
**Confidence**: high
**Created**: 2026-04-27
**Updated**: 2026-04-28
**Version**: 2

When adding a new personal health data type (blood pressure, DEXA, macros, workouts, etc.), there are four required wiring steps. Skipping any one of them results in the agent either ignoring the data or responding from training data instead of the database.

## When to Use

Any time you add a new personal health data type to health.db that Jeff should be able to query via iMessage.

## Prerequisites

- New table exists in `health_db.py` schema (or new column on existing table)
- Data imported (or import script written and run)
- `health_query.py` ready to be extended with a new subcommand or flag

## Steps

### 1. Add the subcommand to `health_query.py`

Add a new argparse subcommand that queries the new table and outputs JSON to stdout. Follow the existing pattern:

```python
bp = sub.add_parser("blood-pressure")
bp.add_argument("--days", type=int, default=30)

# in main():
elif args.command == "blood-pressure":
    _out(blood_pressure(args.days))
```

Test on host first, then verify inside the container:

```bash
# Host
python3.13 workspace/health/health_query.py blood-pressure --days 30

# Container
docker exec sample-agent_secure python3 \
  /home/openclaw/.openclaw/workspace/health/health_query.py blood-pressure --days 30
```

### 2. Add the intent to `health-query/SKILL.md`

Add a row to the Intent Classification table and a full Intent section. Include the mandatory stop line — without it the model may skip the exec:

```markdown
| 7 — Blood pressure | "what's my blood pressure", "BP trend", "systolic" | exec blood-pressure, synthesize |
```

```markdown
### STEP 1 — Query blood pressure data (exec, mandatory)

**YOU DO NOT KNOW THE USER'S BLOOD PRESSURE READINGS. DO NOT ANSWER WITHOUT
RUNNING THIS EXEC. There is no other source of this data.**

exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py blood-pressure --days {n}
```

### 3. Add Rule 6b entry in `AGENTS.md` — THIS IS THE CRITICAL STEP

**Without this step, GPT-5-mini will answer from training data or say "I don't have that on file" — even with a perfect SKILL.md.**

The model treats SKILL.md as context it can choose to follow. AGENTS.md rules with explicit forbidden phrases are treated as hard behavioral constraints. GPT-5-mini conflates "I know about blood pressure as a concept" with "I know Jeff's personal blood pressure readings" and will skip exec unless AGENTS.md makes the fallback responses explicitly forbidden.

Find the `### Rule 6b` section in `workspace/AGENTS.md` and add to the REQUIRED exec block:

```markdown
For {data type} ("trigger phrase 1", "trigger phrase 2", ...):
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py {subcommand} --days {n}
```

Also add the data type's "I don't have that" variant to the **FORBIDDEN responses** list if needed.

### 4. Write unit tests in `test_health_query.py`

Add tests for the new subcommand using the in-memory SQLite fixture pattern — happy path, error case, empty table. See existing tests for the fixture setup (monkey-patch `health_db.get_connection`, use `sqlite3.connect(":memory:")` directly).

### 5. Sync and restart

```bash
# After editing workspace/ files:
./scripts/compose-up.sh sample-agent -d

# If cooldown blocks the restart (watch for "skipping restart (cooldown)"):
docker restart sample-agent_secure

# Verify gateway is up:
./scripts/test-gateway-http.sh sample-agent
```

## Verification

Ask the agent a trigger phrase for the new data type via iMessage. Check logs to confirm `tool=exec` fires — not `memory_search` and not silence:

```bash
./scripts/check-logs.sh sample-agent 80 | grep "tool start"
```

- `tool=exec` → working correctly
- `tool=memory_search` → AGENTS.md rule missing or not synced
- no tool start at all → model answered from training data; AGENTS.md rule needed

## The Core Gotcha

**SKILL.md alone is insufficient for personal health data queries.** The model treats SKILL.md as optional guidance for tasks it knows it can't do inline (like podcast transcription). For health topics it has training knowledge about (A1c, blood pressure, HRV), it will skip exec and answer conceptually — unless AGENTS.md makes the fallback responses explicitly forbidden with exact exec paths.

This was confirmed in production: health-query SKILL.md with MANDATORY/FORBIDDEN language was not enough. Adding Rule 6b to AGENTS.md with forbidden response phrases and exact exec commands fixed it.

---

## iMessage Entry Flow (Rule 6c Pattern)

If the data type supports iMessage-based logging (user texts a reading directly), you need a **second** AGENTS.md rule beyond Rule 6b — Rule 6c covers entry, not just querying.

### The Two Failure Modes Are Different

| Rule | Covers | Symptom without it |
|------|--------|-------------------|
| 6b | **Queries** ("show me my BP") | Agent answers from training data or says "I don't have that" |
| 6c | **Entry** ("133/68 55") | Agent asks permission to log AND gives clinical advice |

### What GPT-5-mini Does Wrong on Entry

When a user texts a reading like `133/68 55`, the model by default:
1. **Asks permission**: "I can log this — want me to?" (Rule 6 violation)
2. **Gives clinical advice**: "133 is mildly elevated — seek care if you have dizziness..."

SKILL.md Intent instructions do NOT prevent either. Both require Rule 6c in AGENTS.md.

### Rule 6c Template

```markdown
### Rule 6c: {Data type} entry — ask timing first, log immediately, no advice

When a message contains a {data type} pattern (e.g. "NNN/NN NN" for BP):

**STEP 1** — Ask ONE question: "Is this reading from right now, or a past date?"
**STEP 2** — After reply: y/now/yes → current timestamp; date string → parse it
**STEP 3** — Log via exec:
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py {log-subcommand} --field1 {v} --date {YYYY-MM-DD} --time {HH:MM}
**STEP 4** — Confirm only: "Logged — {values} on {date} at {time}."

**FORBIDDEN:**
- "Say 'log' to save this reading" — DO NOT ask permission
- Any clinical interpretation, normal/elevated commentary, or medical advice
- "Seek care", symptom warnings of any kind
```

### health_query.py log Subcommand Pattern

Add a `{data}-log` subcommand alongside the `{data}` query subcommand:

```python
lg = sub.add_parser("{data}-log")
lg.add_argument("--field1", type=int, required=True)
lg.add_argument("--date", dest="reading_date", required=True)
lg.add_argument("--time", dest="reading_time", required=True)
# ... other fields
```

Use `source='imessage'` and `ON CONFLICT(...) DO UPDATE` for safe re-entry.

### Generalizes to All iMessage-Logged Metrics

Every future iMessage-based entry type (weight, macros, blood glucose, etc.) needs:
1. A `{data}-log` subcommand in `health_query.py`
2. An entry intent in `health-query/SKILL.md` (separate from the query intent)
3. A Rule 6c block in `AGENTS.md` with data-specific forbidden phrases
