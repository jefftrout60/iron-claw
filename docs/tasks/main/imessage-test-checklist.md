# iMessage Health Query — Manual QA Checklist

These are natural-language queries sent via iMessage to the agent. The agent's
health-query skill classifies each message by intent and routes it to the
appropriate `health_query.py` subcommand via exec. Mark **Pass** if the reply
contains the expected data shape in plain language. Mark **Fail** if the agent
returns an error, replies with raw JSON, says "I don't know", or routes to the
wrong subcommand.

---

## body-metrics

### Query 1
**Send:** "what's my current weight"
**Expected subcommand:** `body-metrics`
**Expected response shape:** Most recent weight reading in lbs with its date,
drawn from the `summary.latest` field. Should not be a generic statement — it
should state a specific number (e.g. "As of April 28 your weight was 187.4 lbs").
**Pass/Fail:** [ ]

### Query 2
**Send:** "show my body fat trend this month"
**Expected subcommand:** `body-metrics`
**Expected response shape:** A series of (date, fat_ratio_pct) readings covering
roughly the last 30 days, with a trend direction called out (rising, falling, or
stable). The reply should mention at least 2 data points and note the most recent
fat percentage.
**Pass/Fail:** [ ]

---

## activity

### Query 3
**Send:** "how many steps did I get this week"
**Expected subcommand:** `activity`
**Expected response shape:** Daily step counts for the last 7 days, with an
average or total called out. Each day should have a date and step count. The
agent should use `--days 7` internally.
**Pass/Fail:** [ ]

### Query 4
**Send:** "how much sunlight did I get today"
**Expected subcommand:** `activity`
**Expected response shape:** A `daylight_minutes` value for today (or the most
recent available date if today has not yet synced). The reply should state the
number of minutes and the date it applies to. If daylight data is absent from
the day's row, the agent should say so rather than invent a value.
**Pass/Fail:** [ ]

---

## workouts

### Query 5
**Send:** "what workouts did I do this week"
**Expected subcommand:** `workouts`
**Expected response shape:** A list of workout sessions from the last 7 days,
each with workout type (e.g. Strength Training, Running) and date. Duration in
minutes should appear if available. The agent should use `--days 7` internally.
**Pass/Fail:** [ ]

### Query 6
**Send:** "show my recent workouts"
**Expected subcommand:** `workouts`
**Expected response shape:** The last 5–10 workout sessions (default window is
30 days) with workout type and date for each. The reply should read as a
summary, not raw JSON, and may include a note on frequency or variety.
**Pass/Fail:** [ ]

---

## workout-exercises

### Query 7
**Send:** "what did I do at the gym last week"
**Expected subcommand:** `workout-exercises`
**Expected response shape:** Exercises grouped by session date, each showing
exercise name and sets/reps/weight where available. The agent should use
`--days 7` internally. A reply like "3 sets of 5 squats at 185 lbs" is the
right shape. If no exercise detail exists for the window, a plain "I don't have
any exercise detail for last week" is acceptable.
**Pass/Fail:** [ ]

### Query 8
**Send:** "show my squat history"
**Expected subcommand:** `workout-exercises`
**Expected response shape:** All squat entries across recent sessions, each
showing date, set number, reps, and weight. The agent must not filter by a
specific date — it should query a broad window (default 7 days) and let the
data show what's there. If squats appear under an exercise_name that does not
literally say "squat", the agent should still surface it if the data contains
it.
**Pass/Fail:** [ ]

---

## tags

### Query 9
**Send:** "how many sauna sessions did I have this month"
**Expected subcommand:** `tags`
**Expected response shape:** A count of sauna-type tags for the current month
(approximately 30 days). The reply should state a specific number (e.g. "You
had 8 sauna sessions this month") drawn from the `by_type` summary field in the
JSON. The agent should use `--days 30` and optionally `--type sauna` internally.
**Pass/Fail:** [ ]

### Query 10
**Send:** "show my recent tags"
**Expected subcommand:** `tags`
**Expected response shape:** Tagged events for the last 7 days (any type),
listing day and tag_type for each entry. If the user has multiple tag types
(e.g. sauna, alcohol, stress), they should all appear. If no tags exist for
that window, a plain "no tags found" response is acceptable.
**Pass/Fail:** [ ]

---

## sync-status

### Query 11
**Send:** "is my health data up to date"
**Expected subcommand:** `sync-status`
**Expected response shape:** A per-source sync summary stating the last synced
date/time for each resource (e.g. daily_summaries, sleep, heartrate, withings,
oura_tags). Should include an overall one-line assessment: either "Everything
looks current" or a callout of any source that is stale. Stale sources are
those in the automated set (daily_summaries, sleep, heartrate, oura_tags,
withings) with `days_ago > 2`.
**Pass/Fail:** [ ]

### Query 12
**Send:** "when did I last sync my Oura data"
**Expected subcommand:** `sync-status`
**Expected response shape:** Should surface the last_synced timestamps for
Oura-related resources (e.g. daily_summaries, sleep, oura_tags). The reply
should name at least one resource and give a specific date or relative time
(e.g. "Oura daily summaries last synced 2 days ago on April 28").

**What a stale response looks like:** If any Oura-related resource has
`stale: true` in the JSON (meaning it has not synced in more than 2 days),
the agent should explicitly mention it — e.g. "Your Oura sleep data hasn't
synced in 4 days — you may want to re-run the import." A stale flag should
never be silently omitted.
**Pass/Fail:** [ ]

---

## mood

### Query 13
**Send:** "how was my mood this week"
**Expected subcommand:** `mood`
**Expected response shape:** Daily mood entries (`kind: daily_mood`) for
approximately the last 7 days, each showing date and valence value. The agent
should call `mood --kind daily_mood` (the default) and synthesize the entries
into a brief narrative (e.g. "Your mood trended positive early in the week
with valence scores around 0.7, then dipped slightly on Thursday").

If the state_of_mind table is empty, the expected response is a plain "I don't
have mood data available right now" — not an error or raw JSON.
**Pass/Fail:** [ ]

### Query 14
**Send:** "show my state of mind lately"
**Expected subcommand:** `mood`
**Expected response shape:** Recent mood entries from the last 30 days (default
window) with dates and any labels or associations present in the data. The
reply should mention valence direction and any labels (e.g. "calm", "anxious")
if they appear in the JSON. If entries exist, at least two should be mentioned
with their dates.

If the state_of_mind table is empty, the expected response is a plain "I don't
have mood data available right now" — not an error or raw JSON.
**Pass/Fail:** [ ]

---

## First-Run Notes

**State of Mind data will be empty until Apple Health XML re-import.**
`HKStateOfMindSample` records are imported by `import-apple-health.py`. The
exact HealthKit key names for this record type are unconfirmed. If Queries 13
and 14 return empty results even after import, check the key names used inside
`import-apple-health.py` for `state_of_mind` rows and re-run the import after
correcting them.

**sync-status only shows sources that have been synced at least once.**
If a resource has never been synced it will not appear in the `sync_state`
table and will be absent from the response. Run each importer at least once
before testing Queries 11 and 12. Expected resources: `daily_summaries`,
`sleep`, `heartrate`, `oura_tags`, `withings`, `apple_health`, `evernote`,
`lab_results`.
