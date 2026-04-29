# Scope Clarifications: Apple Health / Multi-Source Health Data Import
Date: 2026-04-29

## Confirmed Scope So Far

**IN**: Apple Health XML backfill (BP, steps, daylight, workout summaries), Withings API cron (weight, fat mass, lean mass), Evernote workout detail (weekly planned/actual table), Oura tags (if API supports it)
**OUT**: Oura main metrics (already synced), height, iMessage weight logging, correlation query features (separate effort)

---

## Remaining Questions

**Q1 — Body fat% source in Apple Health**
You said Health shows body fat%, BMI, lean body mass, AND weight — but Withings doesn't pass fat/lean to Health. Check the source: open Health → Browse → Body Measurements → Body Fat Percentage → tap any entry → scroll to "Source" at the bottom. What app is listed?

This matters because if Withings IS passing BF% somehow, we can use Health XML for everything and skip the Withings API. If it's a different app (or manual), we need Withings API separately.

<response>
Withings is the source. Previously it was Qardio but that scale broke and I got the Withings. But Withings doesn't import to Helth the muscle and fat % breakdowns, so probably need to go direct
</response>

---

**Q2 — Evernote access**
For the weekly workout tables: do you want to export these manually (Evernote → Export → HTML/ENEX, then a one-time parser), or is ongoing automatic sync a requirement? Also: roughly how many weeks of notes exist, and are they all in one notebook?

<response>
I have years of notes. But we don't need to go back far. Maybe from Jan 1, 2025. a future task coiuold be to get everything in there for all time. I may have to do a manual export becasue when we were doing the KindleSync project we wanted to write book highlights into Evernote but they are not giving our API keys anymore
</response>

---

**Q3 — Workout detail priority**
Your Evernote table has Planned vs Actual columns. When there's a discrepancy (you substituted an exercise or skipped something), which matters more for future queries — what you planned, what you actually did, or both? And do the table cells contain enough detail (exercise name, sets, reps, weight) to be useful, or are some cells vague?

<response>
What I actually did for sure. I think they are useful. I usually paste in a screenshot of my APple Watch workout summary as well (time, calories, avg heart rate, and an effort rating)
</response>

---

**Q4 — Ongoing Apple Health sync**
For ongoing (post-backfill) Apple Health data, the two options are:
- **iOS Shortcut** — runs on your phone, exports XML to iCloud Drive, cron on Mac picks it up (automatic but requires phone to be unlocked/nearby)
- **Withings API + manual Health export** — use Withings API for body composition, manual Health XML export quarterly for the rest (steps, daylight, BP if Omron starts syncing)

Which is more important to automate: the body composition data, or the activity/workout data?

<response>
both?
</response>

---

**Q5 — Scope of "workouts" pillar**
Two sub-questions:
a) Apple Watch workout types you actually log: mostly strength training? Cardio? Both? (affects schema)
b) Should the Evernote import cover historical notes (backfill all past weeks) or just going forward from a cutoff date?

<response>most common are: functional strength training, indoor cycle, cross training, hiking, elliptical, outdoor run, elliptical, indoor rowing, stair steppper, indoor run, outdoor cycle)
</response>
