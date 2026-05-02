# Weekly Health Brief — Research Reference

*Compiled 2026-05-02. Use this when writing your brief spec.*

---

## What Real Projects Do

### Simple Wearable Report (simplewearablereport.com)
Dedicated tool for turning Oura data into a shareable PDF (originally for sharing with doctors). Their format:
- **7-day summary window** — always a fixed period, never rolling
- **Personal baseline comparison** — compares you to *your own* 30-day average, not a population chart. This is the right model: "HRV 61 (your 30-day avg: 57)" lands better than just "61"
- **Metrics by category** — Sleep / Cardiovascular / Activity as sections, not a flat list
- **Trend indicators** — arrows and delta values, not raw numbers alone

Data fields they include:
- Total sleep, time in each stage (deep/REM/light)
- Resting HR, HRV (nightly)
- Respiratory rate, breathing disturbance index
- Skin temperature deviation from personal baseline
- Steps and activity

### Open Wearables (openwearables.io)
Open-source, MIT-licensed platform connecting Oura + Apple Health + Garmin + Whoop. Generates weekly coaching emails automatically. Key design principle: **personalized guidance across fitness, recovery, mental health, and nutrition** — it connects pillars rather than just reporting them side by side. Has an MCP server for LLM natural language queries.

### krumjahn/applehealth (GitHub)
Python project that generates weekly summaries from Apple Health export with unified Oura/Whoop analysis via AI. Confirmed: unified Oura + Apple Health analysis in one call is buildable and has working open-source examples.

---

## What Gets Included (and What Gets Dropped)

### Almost universal inclusions
- Oura readiness score + contributing factors (HRV, resting HR, sleep quality)
- Total sleep duration + sleep efficiency (not just time in bed)
- HRV — weekly average and trend direction vs prior week
- Resting heart rate — weekly average and trend
- Step count — weekly total or daily average
- Workout volume — hours by type, not granular sets/reps

### Common additions once v1 is stable
- Weight — rolling 7-day average (smooths daily noise; raw daily weight is anxiety-inducing)
- Active energy / calories burned
- HR zones / intensity — time in each zone per week, not per session
- Subjective mood/energy (your State of Mind plays this role)

### What reliably gets dropped after trying it
- **Daily weight** — too noisy, people switch to 7-day rolling average
- Individual sleep stage percentages (REM/deep/light breakdown) — people find the score more useful than the breakdown
- Per-session HRV readings — weekly average is what matters
- Specific per-session HR — people look at it once then stop reading it
- More than ~12 metrics total — decision fatigue, people stop reading the brief

---

## What Oura's Own Weekly Report Includes (and What's Missing)

**Oura sends** a weekly report each Sunday covering:
- Readiness, sleep, and activity trend charts for the week
- 7-day averages for resting HR and HRV
- Activity goal completion

**What people consistently say is missing from Oura's native report:**
- Workout context — Oura doesn't know your training was intentionally hard
- Subjective mood integration — no State of Mind correlation
- Cross-pillar "what drove what" narrative language
- Configurable content ("I don't care about daytime naps, stop showing them")
- Trends beyond 7 days (no rolling 30-day baseline comparison)
- Any explanation of correlation — just numbers side by side

This is the gap your brief fills. Your brief = Oura's weekly report + workout context + State of Mind + cross-pillar narrative + configurable over time.

---

## Formats That Work

**Best-rated structure across QS community:**

1. **3-5 headline numbers at top** — readiness avg, HRV avg, sleep avg, weight avg. Quick scan, no reading required.
2. **Short narrative paragraph per pillar** — 2-3 sentences from an LLM that contextualizes the numbers. Example: "HRV averaged 58, up 4 points from last week — your three rest days Tuesday-Thursday appear to be driving recovery."
3. **Trend arrows / delta** — `↑4` or `↓2` next to each metric
4. **Cross-pillar correlation section** — 1-3 observations the system noticed, e.g. "Your two lowest State of Mind days (Mon, Thu) followed your two lowest readiness mornings"
5. **Week-over-week comparison table** — current week vs prior week for 6-8 key metrics

**What doesn't land well:**
- Long narrative with numbers buried in prose — people skim and miss the numbers
- Too many metrics (>12) — decision fatigue, people stop reading
- No "so what" interpretation — raw numbers without context feel like a dashboard export
- HTML-heavy formatting — plain text with simple structure survives more email clients

---

## Most Useful Cross-Pillar Correlations (QS community consensus)

In rough order of how often people cite them as insightful:

1. **HRV vs training load** — did hard weeks suppress recovery? (your version: HRV vs workout intensity/hours)
2. **Sleep quality vs next-day readiness** — how predictive is your sleep?
3. **Subjective mood vs readiness** — does physiology predict subjective state, or does subjective state lead physiology? (State of Mind vs Oura Readiness)
4. **Resting HR trend vs illness/overtraining** — elevated resting HR is often the earliest warning signal
5. **Weight trend vs active energy** — expenditure correlation
6. **Steps/activity vs sleep quality** — did light active days hurt sleep?

### Research-backed findings (from published studies)
- HRV (RMSSD specifically) correlates with better self-reported sleep, lower fatigue, and reduced stress
- **Sleep-wake regularity + HRV together predict mood** better than either alone — this is the scientific basis for your State of Mind vs Oura Readiness correlation feature
- Low weekly sleep-wake regularity predicted poor mood independently of sleep *duration* — consistency matters more than total hours

---

## Suggested Format for Your Brief (Draft Template)

```
SUBJECT: Weekly Health Brief — May 5–11

━━ THIS WEEK AT A GLANCE ━━
Readiness   72 avg   (↑6 vs last week)
HRV         61 ms    (↑4 vs last week)
Sleep       7h 22m   (→ flat)
Weight      183.4    (↓1.2 lbs | 4-week trend: ↓3.1)
Steps       8,420/day

━━ TRAINING ━━
Zone 2 cardio:    2h 40m  (3 sessions)
Strength:         1h 30m  (2 sessions)
Avg intensity:    4.2 METs (moderate)

━━ MIND & RECOVERY ━━
State of Mind:  6.2/10 avg  (↑0.4 vs last week)
Oura tags:      3× sauna, 1× nap
Notable: 2 low SoM days followed your 2 lowest readiness mornings

━━ TRENDS (4-week) ━━
HRV (weekly avg):      54 → 57 → 58 → 61   ↑ recovering well
Resting HR (weekly):   52 → 51 → 51 → 50   ↑ solid adaptation
Weight (4-wk):        184.6 → 184.1 → 183.8 → 183.4

━━ OBSERVATIONS ━━
• Hard training days (Mon, Wed, Fri) preceded 2 low-readiness mornings
• Time in daylight: 47 min avg — below 60-min target 4 of 7 days
• SoM valence trending positive despite training load — recovery working
```

---

## Questions to Answer in Your Spec

When writing your spec, the research suggests these are the decisions that matter most:

1. **Baseline window** — compare this week to "last week" only, or to a 4-week rolling average? (Most people prefer 4-week for HRV/weight, 1-week for everything else)
2. **Weight display** — daily, 7-day rolling average, or both? (QS community consensus: 7-day average only)
3. **Workout summary granularity** — total time by type, or also include avg HR and intensity? What counts as "Zone 2" vs "vigorous"?
4. **State of Mind** — single weekly average, or surface the specific days with notable high/low scores?
5. **Correlations** — computed observations ("your low SoM days followed low readiness") or just side-by-side data? The former is more valuable but requires more build effort
6. **Section order** — what do you want your eye to land on first? (Most people: recovery score, then training, then trends)
7. **Oura tags** — include them in the brief, or only surface notable ones (sick, travel, sauna)?
8. **What's enough for v1** — which sections are non-negotiable and which can wait for v2?

---

## Data Available in health.db (confirmed 2026-05-02)

| Section | Source table | Notes |
|---------|-------------|-------|
| Oura readiness/sleep/HRV | `oura_daily` | 3,140 days, 2017–present |
| Oura tags (sauna, nap, sick…) | `oura_tags` | Dec 2024–present |
| Sleep detail (REM/deep/light) | `oura_sleep_sessions` | With 5-min HRV series |
| Weight | `body_metrics` | Withings API, use `source='withings_api'` only |
| Steps + daylight | `activity_daily` | 3,836 days, 2015–present |
| Workouts (type/duration/avg HR) | `workouts` | 1,890 workouts, 2016–present |
| Workout intensity (METs) | `workouts.intensity_met` | JSON Workout export format only |
| State of Mind | `state_of_mind` | Only 1 record so far — will build over time |

**HR zones** are not in the DB. The Workout export's `heartRateData` field covers post-workout cool-down only, not the workout itself. Avg/max/min HR per session is available. True time-in-zone is a future item.
