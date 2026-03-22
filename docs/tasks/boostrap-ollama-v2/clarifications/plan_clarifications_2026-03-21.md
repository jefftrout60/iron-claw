# Technical Clarifications — Podcast Summary Skill Plan
Date: 2026-03-21

---

**Q1 — Whisper model: quality vs. setup size**

whisper.cpp ships several model sizes. The choice affects transcription quality significantly for dense health/science content:

| Model | Size | Speed (Mac Metal) | Quality |
|-------|------|-------------------|---------|
| `base.en` | ~150MB | ~6x realtime | Good for clear speech; misses some jargon |
| `small.en` | ~470MB | ~4x realtime | Better jargon, still fast |
| `medium.en` | ~1.4GB | ~2x realtime | Very good; handles technical vocabulary |
| `large-v3` | ~2.9GB | ~1x realtime | Best quality; may be slow for 3hr Shawn Ryan episodes |

**Options:**
- A) Start with `base.en` — fast setup, fast transcription, slightly lower quality on health/science jargon
- B) Start with `small.en` — good balance, recommended sweet spot
- C) Start with `large-v3` — highest quality for your most important shows (Attia, Huberman), but ~1x realtime means a 2hr episode takes ~2 hrs. Overnight window supports this but barely for heavy nights.
- D) Per-show: `large-v3` for health shows (Attia, Huberman, Patrick), `base.en` for everything else

<response>
let's do B and C. but maybe make it configurable so I could switch. start with small.en for everything else, large-v3 for the three you list.
</response>

---

**Q2 — Cron trigger mechanism**

Two options for the 11PM nightly trigger:

**Option A: OpenClaw built-in cron tool (`config/cron/jobs.json`)**
- Pros: Stays inside the container ecosystem; OpenClaw agent runs the skill as a normal turn; agent can use all tools (web_fetch, browser, message) naturally; no Mac host config needed
- Cons: Agent turn must complete start-to-finish; long overnight runs (many Whisper episodes) may hit turn timeouts; OpenClaw's cron is less battle-tested for multi-hour jobs
- Pattern used by: productwatcher heartbeat

**Option B: Mac host system crontab (calls Python script directly)**
- Pros: Python script runs indefinitely with no timeout; proven for long batch jobs; whisper bridge and Python are already on the host; failures don't affect the agent
- Cons: Requires Mac host to be awake and crontab entry set up; separate from OpenClaw ecosystem; notification/email steps need to call OpenClaw gateway or use send_email.py directly
- Pattern used by: daily-report setup script

**Recommended**: Option B for the overnight batch processing (engine.py), Option A for the 6AM notification (lightweight agent turn that just sends a message).

<response>
recommendation is ok
</response>

---

**Q3 — Health knowledge store format**

For the health knowledge store (persisting summaries for future query):

**Option A: JSON file** (`health_knowledge.json`)
- Pros: Consistent with productwatcher vault pattern; simple; no dependencies; version-controllable
- Cons: Gets large over time (~28 health-relevant shows × ~1 episode/week = ~1,500 entries/year); full-file read/write on every update; harder to query without loading everything
- Best if: Future query interface is LLM-based (loads context window) rather than SQL

**Option B: SQLite** (`health_knowledge.db`)
- Pros: Efficient for large datasets; structured queries; easily supports "all episodes mentioning ApoB"; already used by OpenClaw internally (`memory/main.sqlite`)
- Cons: Slightly more setup; Python `sqlite3` is stdlib so no new dependencies; harder to inspect manually
- Best if: Future query interface will do structured lookups (biomarkers, protocols, date ranges)

<response>
i am thinking A as future queries will be LLM-based. I probably have less than 10 entries pre week
</response>

---

**Q4 — Phase 1 email behavior (before Whisper is set up)**

Phase 1 delivers summaries based on show notes/descriptions only. For shows like Attia, Huberman, and Rhonda Patrick, show notes can be quite sparse — the summary quality will be noticeably lower than Phase 2+.

**Options:**
- A) Ship Phase 1 as-is — summaries include source label "Show Notes Only" so you know why quality is lower; you start getting value immediately for hunting/commentary shows that have good show notes
- B) Hold Phase 1 emails until Phase 2 (Whisper) is also ready — get good quality summaries from day one, but wait longer to start
- C) Phase 1 emails for all shows with good show notes (hunting, All-In, Ferriss, Triggernometry); hold Attia/Huberman/Patrick/FoundMyFitness until Phase 2

<response>
hold everything until Phase 2
</response>

---

**Q5 — OPML import: one-time script or Telegram command?**

You have an OPML export from Overcast with 35 feeds already. For loading this initial list:

**Option A: One-time setup script** — `python3 scripts/importer.py feeds.opml` — run once from the Mac host, populates feeds.json, done
**Option B: Telegram command** — "import my podcast feeds" + paste OPML content or file path; agent runs the importer
**Option C: Both** — Setup script for the initial bulk import; Telegram "add podcast [name/URL/RSS]" for ongoing additions

<response>
C
</response>
