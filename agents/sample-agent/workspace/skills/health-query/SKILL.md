---
name: health-query
description: >
  Personal health intelligence. Use when the user asks about their lab results,
  Oura ring metrics, health trends, or health questions referencing trusted
  sources (Attia, Huberman, Patrick). Also handles weekly health summary email.
metadata:
  openclaw:
    requires:
      bins: ["bash", "python3"]
---

# Health Query — EXECUTE THE MATCHING INTENT PIPELINE IN ORDER. DO NOT SKIP ANY STEP.

## MANDATORY RULES — VIOLATIONS BREAK THE SKILL

- **FORBIDDEN: memory_search for lab results or Oura data.** Lab results and Oura metrics are stored in health.db — they do NOT exist in memory. `memory_search` will always return nothing for these queries. Use exec ONLY.
- **FORBIDDEN: answering health data questions without running exec first.** You do not know the user's lab values or Oura scores. You MUST exec health_query.py to retrieve them.
- **MANDATORY: exec health_query.py for every Intent 1 and Intent 2 query**, no exceptions.

## Intent Classification

Read the user's message and identify which intent applies:

| Intent | Trigger examples | Action |
|--------|-----------------|--------|
| 1 — Lab query | "what's my ferritin", "show me my A1c trend", "my cholesterol", "LDL over the last year" | exec lab-trend, synthesize |
| 2 — Oura query | "how's my HRV", "last week's sleep", "readiness score", "Oura", "recovery score", "how did I sleep" | exec oura-window, synthesize |
| 3 — Knowledge search | "what does Attia say about X", "trusted sources on Y", "what does the research say about Z", "what do my podcast summaries say about X", "what have I learned from podcasts about Y", "what do my summaries say" | exec search, web fallback if thin |
| 4 — Web contrast | "do a web search on that", "contrast with web", "what does the internet say" | web_search, contrast prior trusted answer |
| 5 — Weekly summary on-demand | "give me my weekly summary", "weekly health report", "send my health summary" | pipeline: oura-window + cost + email |
| 6 — Set up weekly cron | "set up my weekly summary", "schedule weekly health email", "automate weekly summary" | register Sunday 6 PM cron via cron tool |
| 7 — BP entry | message matches pattern NNN/NN or NNN/NN NN (a reading, e.g. "133/68 55") | ask now-or-past, then exec bp-log |
| 8 — BP query | "my blood pressure", "BP readings", "systolic", "diastolic", "BP trend", "blood pressure last N days" | exec blood-pressure, synthesize |
| 9 — Body metrics | "my weight", "body fat", "lean mass", "fat percentage", "weight trend", "how much do I weigh" | exec body-metrics, synthesize |
| 10 — Activity | "my steps", "steps this week", "how active", "time outside", "daylight" | exec activity, synthesize |
| 11 — Workouts | "my workouts", "did I exercise", "gym this week", "workout summary", "training" | exec workouts, synthesize |
| 12 — Workout exercises | "what did I do at the gym", "my exercises", "sets and reps", "strength training detail" | exec workout-exercises, synthesize |
| 13 — Tags | "my sauna days", "Oura tags", "tag trends", "sauna this month" | exec tags, synthesize |

---

## Intent 1 — Lab Query

**Trigger:** User asks about a specific lab marker — current value, trend, or whether it's in range.

### STEP 1 — Query the lab database (exec, mandatory)

**YOU DO NOT KNOW THE USER'S LAB VALUES. DO NOT ANSWER WITHOUT RUNNING THIS EXEC. There is no other source of this data.**

Extract the marker name from the user's message. Use 12 months as the default lookback unless the user specifies otherwise.

```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py lab-trend --marker "{marker}" --months {n}
```

Examples:
- "what's my ferritin" → `--marker ferritin --months 12`
- "show me A1c over the last 6 months" → `--marker A1c --months 6`

### STEP 2 — Synthesize and reply (reply, mandatory)

Parse the JSON output and reply in natural language. Include:
- Most recent value with date and units
- Trend direction over the period (rising, falling, stable)
- Reference range context — is the value in range, borderline, or flagged?
- Any notable inflection points if there are multiple readings

**If exec returns an error or the marker is not found:** Tell the user plainly — "I don't have any results for [marker] in the database — check the spelling or let me know the exact name used in your labs."

**Hard rule: NEVER reply with raw JSON. NEVER mention script names, exec, or file paths.**

---

## Intent 2 — Oura Query

**Trigger:** User asks about sleep, HRV, readiness, recovery, resting heart rate, or any Oura ring metric.

### STEP 1 — Query Oura data (exec, mandatory)

**YOU DO NOT KNOW THE USER'S OURA METRICS. DO NOT ANSWER WITHOUT RUNNING THIS EXEC. There is no other source of this data.**

Default to the last 7 days unless the user specifies a different window.

```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py oura-window --all --days {n}
```

Examples:
- "how's my HRV this week" → `--all --days 7`
- "last 30 days of sleep" → `--all --days 30`
- "yesterday's readiness" → `--all --days 1`

### STEP 2 — Synthesize and reply (reply, mandatory)

Parse the JSON output and reply in natural language. Write a narrative covering:
- Overview of the period: how sleep, readiness, and activity trended
- Any notable metrics (a night with very low sleep score, an HRV spike or dip, elevated resting HR)
- 1–2 sentence takeaway on what stands out

**If exec returns an error or no data:** Tell the user — "The Oura data may not have synced yet — try again in a bit."

**Hard rule: NEVER reply with raw JSON. NEVER mention script names, exec, or file paths.**

---

## Intent 3 — Knowledge Search (Trusted Sources + Optional Web Fallback)

**Trigger:** User asks what Attia, Huberman, or Patrick say about a topic, or asks what the research says about a health subject.

### STEP 1 — Search trusted sources (exec, mandatory)

```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py search --query "{query}"
```

Extract the health topic or question as the query string.

### STEP 2 — Evaluate results

- **If `count >= 3` AND the results contain relevant snippets:** Answer from trusted sources only. Skip web search unless the user explicitly asks for it.
- **If `count == 0` OR `count < 2`:** Also run `web_search` with the same query (go to Step 3).

### STEP 3 — Web fallback if trusted sources are thin (conditional)

Run `web_search` with the same health query.

### STEP 4 — Synthesize and reply (reply, mandatory)

- **Trusted sources only:** Attribute the answer to the relevant sources (e.g. "Attia's take on this…") and give a clear, direct synthesis.
- **Both sources have content:** Reply in two segments — "**Trusted sources say:** …" then "**Web adds:** …"
- **Web only:** Lead with the web findings, then note: "My trusted source archive doesn't cover this topic yet."

**Hard rule: NEVER reply with raw JSON. NEVER mention script names, exec, or file paths.**

---

## Intent 4 — Web Contrast

**Trigger:** Follow-up request after the user has already received a trusted-source answer and now wants the broader web perspective. Phrases like "do a web search on that", "what does the internet say", "contrast with web".

### STEP 1 — Run web search (web_search, mandatory)

Use the same health topic extracted from the conversation context. Do NOT re-run `health_query.py search` — the trusted source answer is already in context.

```
web_search: {same health topic from conversation context}
```

### STEP 2 — Contrast and reply (reply, mandatory)

Explicitly contrast the two perspectives:
- What the trusted sources said
- What the broader web says
- Note any conflicts or areas of agreement

**Hard rule: NEVER mention script names, exec, or file paths.**

---

## Intent 5 — Weekly Health Summary (On-Demand)

**Trigger:** User asks for their weekly health summary or wants it emailed.

This is a sequential pipeline. All steps are mandatory. Do not skip any step.

### STEP 1 — Fetch Oura data (exec, mandatory)

```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py oura-window --all --days 7
```

Save the output — it is used in Step 3 to build the email body.

### STEP 2 — Fetch blood pressure data (exec, mandatory)

```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py blood-pressure --days 7
```

Save the output — it is used in Step 4 to build the BP section of the email body.

### STEP 3 — Fetch month-to-date AI spend (exec, mandatory)

```
exec: python3 /home/openclaw/.openclaw/workspace/health/cost_summary.py --month
```

Save the cost total — it goes in the email footer.

### STEP 4 — Synthesize the email body (mandatory)

Build a plain-text email with this structure:

```
Subject: Weekly Health Summary — {start date} to {end date}

OURA METRICS

Day        | Sleep | Readiness | Activity | HRV  | RHR
-----------|-------|-----------|----------|------|----
{one row per day from the 7-day window}

PATTERNS THIS WEEK

{2–3 sentence narrative on what the data shows — what stands out, any trends worth noting}

BLOOD PRESSURE

{7-day session averages: one line per session showing date and avg reading (e.g., "Apr 21: 138/82")}
{one-line trend: improving / stable / worsening based on the period}

SUGGESTIONS FOR NEXT WEEK

- {suggestion 1 based on the data}
- {suggestion 2 based on the data}
- {suggestion 3 based on the data}

---
Month-to-date AI spend: ${cost_usd}
```

Use the Oura data from Step 1, BP data from Step 2, and the cost figure from Step 3. Derive the date range from the data. If the BP exec returned no data for the 7-day window, omit the BLOOD PRESSURE section entirely.

### STEP 5 — Write the email body to a temp file (write, mandatory)

Use the `write` tool to write the synthesized email body (from Step 4) to a temp file:

```
write: /tmp/health_weekly.txt
content: {full email body from Step 4}
```

### STEP 6 — Send the email (exec, mandatory)

```
exec: bash /home/openclaw/.openclaw/workspace/scripts/send-email.sh jeff@armantrouts.net "Weekly Health Summary" /tmp/health_weekly.txt
```

### STEP 7 — Confirm to user (reply, mandatory)

Reply via iMessage: "Weekly summary sent to your email."

**If any step fails:** Tell the user plainly what went wrong (e.g. "I couldn't reach the Oura data — the sync may be behind") without exposing internals. Do not send a partial email.

---

## Intent 7 — Blood Pressure Entry (logging a new reading)

**Trigger:** User's message matches a blood pressure reading pattern — numbers in NNN/NN or NNN/NN NN format (e.g. "133/68", "133/68 55", "118/78 62").

### STEP 1 — Confirm timing (reply, mandatory)

Ask ONE question before logging:

"Is this reading from right now, or a past date?"

Do NOT offer other options. Do NOT provide clinical commentary at this stage. Wait for the reply.

### STEP 2 — Resolve timestamp

- If user replies "y", "yes", "now", or similar → use current date and time
- If user replies a date/time (e.g. "4/20/26 9:30am", "apr 20 0930") → parse it into YYYY-MM-DD and HH:MM

### STEP 3 — Log via exec (exec, mandatory)

```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py bp-log --systolic {sys} --diastolic {dia} --pulse {pulse} --date {YYYY-MM-DD} --time {HH:MM}
```

Omit `--pulse` if not provided.

### STEP 4 — Confirm (reply, mandatory)

Reply briefly: "Logged — {sys}/{dia}, pulse {pulse} on {date} at {time}."

Optionally offer (non-blocking, no question mark required): "I can pull your recent BP trend if you'd like."

**Hard rules:**
- NEVER provide medical advice, symptom warnings, or "seek care" language
- NEVER ask "want me to log this?" — just confirm timing and log
- NEVER reply with raw JSON

---

## Intent 8 — Blood Pressure Query

**Trigger:** User asks about their blood pressure history or trends.

### STEP 1 — Query blood pressure data (exec, mandatory)

**YOU DO NOT KNOW THE USER'S BLOOD PRESSURE READINGS. DO NOT ANSWER WITHOUT RUNNING THIS EXEC. There is no other source of this data.**

Default to the last 30 days unless the user specifies a different window.

```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py blood-pressure --days 30
```

Examples:
- "show me my BP last 60 days" → `--days 60`
- "blood pressure since January" → `--start 2026-01-01`

### STEP 2 — Synthesize and reply (reply, mandatory)

Parse the JSON output and reply in natural language. Include:
- Most recent session: date, average reading (e.g. "133/68")
- Trend direction over the period (improving, stable, worsening)
- Overall period average systolic and diastolic
- If any session had notably elevated readings, mention it matter-of-factly (no advice, no urgency)

**Hard rules: NEVER reply with raw JSON. NEVER mention script paths. NEVER provide medical advice or symptom warnings.**

**If exec returns an error or no data:** "I don't have any blood pressure readings in that window."

---

## Intent 6 — Set Up Weekly Summary Cron

**Trigger:** User asks to schedule, automate, or set up the weekly health summary email. Phrases like "set up my weekly summary", "schedule weekly health email", "automate my weekly summary".

### STEP 1 — Register the cron task (cron tool, mandatory)

Use the built-in `cron` tool to register a recurring task:

```
cron: every Sunday at 18:00
task: Send my weekly health summary to email
```

The task wording intentionally matches Intent 5 trigger phrases so the cron re-entry correctly routes to the weekly summary pipeline.

This fires the Intent 5 pipeline automatically every Sunday at 6 PM. The cron task runs the full pipeline: Oura data → cost summary → email synthesis → send → iMessage confirmation.

### STEP 2 — Confirm to user (reply, mandatory)

Reply via iMessage: "Done — your weekly health summary is scheduled for every Sunday at 6 PM. The first one will arrive this Sunday (or next Sunday if it's already past 6 PM today)."

**Hard rule: NEVER mention cron tool names, exec calls, or script paths in user-facing replies.**

---

## Intent 9 — Body Metrics

**Trigger:** User asks about weight, body fat, lean mass, fat percentage, or weight trend.

### STEP 1 — Query body composition data (exec, mandatory)

**YOU DO NOT KNOW THE USER'S BODY COMPOSITION DATA. DO NOT ANSWER WITHOUT
RUNNING THIS EXEC. There is no other source of this data.**

Default to 90 days unless the user specifies a different window.

```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py body-metrics --days 90
```

Examples:
- "what's my weight trend" → `--days 90`
- "body fat last 6 months" → `--days 180`

### STEP 2 — Synthesize and reply (reply, mandatory)

Parse the JSON output and reply in natural language. Include:
- Most recent weight and body fat percentage with date
- Trend direction over the period (gaining, losing, stable)
- Lean mass direction if available

**Hard rules: NEVER reply with raw JSON. NEVER mention script paths. NEVER provide medical advice.**

**If exec returns an error or no data:** "I don't have any body composition data in that window."

---

## Intent 10 — Activity

**Trigger:** User asks about steps, activity level, time outside, or daylight exposure.

### STEP 1 — Query activity data (exec, mandatory)

**YOU DO NOT KNOW THE USER'S ACTIVITY DATA. DO NOT ANSWER WITHOUT
RUNNING THIS EXEC. There is no other source of this data.**

Default to 14 days unless the user specifies a different window.

```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py activity --days 14
```

Examples:
- "how many steps this week" → `--days 7`
- "activity last month" → `--days 30`

### STEP 2 — Synthesize and reply (reply, mandatory)

Parse the JSON output and reply in natural language. Include:
- Average daily steps over the period
- Best and lowest step days if notable
- Time outside or daylight exposure if present in the data

**Hard rules: NEVER reply with raw JSON. NEVER mention script paths. NEVER provide medical advice.**

**If exec returns an error or no data:** "I don't have any activity data in that window."

---

## Intent 11 — Workouts

**Trigger:** User asks about workout history, exercise frequency, gym sessions, or training summary.

### STEP 1 — Query workout data (exec, mandatory)

**YOU DO NOT KNOW THE USER'S WORKOUT HISTORY. DO NOT ANSWER WITHOUT
RUNNING THIS EXEC. There is no other source of this data.**

Default to 30 days unless the user specifies a different window.

```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py workouts --days 30
```

Examples:
- "did I work out this week" → `--days 7`
- "my training last 2 months" → `--days 60`

### STEP 2 — Synthesize and reply (reply, mandatory)

Parse the JSON output and reply in natural language. Include:
- Number of workout sessions in the period
- Types of workouts if available (strength, cardio, etc.)
- Any trend in frequency or duration

**Hard rules: NEVER reply with raw JSON. NEVER mention script paths. NEVER provide medical advice.**

**If exec returns an error or no data:** "I don't have any workout data in that window."

---

## Intent 12 — Workout Exercises

**Trigger:** User asks about specific exercises performed, sets and reps, strength training detail, or what they did at the gym.

### STEP 1 — Query workout exercise data (exec, mandatory)

**YOU DO NOT KNOW THE USER'S EXERCISE DETAIL. DO NOT ANSWER WITHOUT
RUNNING THIS EXEC. There is no other source of this data.**

Default to 7 days unless the user specifies a different window.

```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py workout-exercises --days 7
```

Examples:
- "what did I do at the gym yesterday" → `--days 1`
- "my exercises this week" → `--days 7`

### STEP 2 — Synthesize and reply (reply, mandatory)

Parse the JSON output and reply in natural language. Include:
- Exercises performed with sets and reps where available
- Workout date(s) covered
- Any progression notes if present in the data

**Hard rules: NEVER reply with raw JSON. NEVER mention script paths. NEVER provide medical advice.**

**If exec returns an error or no data:** "I don't have any exercise detail in that window."

---

## Intent 13 — Tags

**Trigger:** User asks about Oura tags, sauna days, alcohol tags, or any tagged activity trend.

### STEP 1 — Query tag data (exec, mandatory)

**YOU DO NOT KNOW THE USER'S OURA TAGS. DO NOT ANSWER WITHOUT
RUNNING THIS EXEC. There is no other source of this data.**

Default to 30 days unless the user specifies a different window.

```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py tags --days 30
```

Examples:
- "my sauna days this month" → `--days 30`
- "how often did I have alcohol in the last 60 days" → `--days 60`

### STEP 2 — Synthesize and reply (reply, mandatory)

Parse the JSON output and reply in natural language. Include:
- Frequency of each tag type over the period
- Any notable patterns (e.g. "12 sauna sessions in 30 days")
- Trend if the same tag appears across multiple months

**Hard rules: NEVER reply with raw JSON. NEVER mention script paths. NEVER provide medical advice.**

**If exec returns an error or no data:** "I don't have any tag data in that window."

---

## Hard Rules

1. **NEVER reply with raw JSON.** Always synthesize exec output into natural language.
2. **NEVER mention tool names, exec calls, or script paths** in user-facing replies.
3. **If exec fails with an error**, tell the user plainly what went wrong without exposing internals.
4. **Zero narration between steps.** Do not say "Let me look that up" or "Running the query now." Just execute.
5. **Scripts run at:** `/home/openclaw/.openclaw/workspace/health/`
6. **Send-email script is at:** `/home/openclaw/.openclaw/workspace/scripts/send-email.sh`
