# Scope: Podcast Summary Skill
Date: 2026-03-21
Branch: boostrap-ollama-v2

---

## The Problem

Jeff listens to ~28 actively-monitored podcasts across health/longevity, hunting/outdoors, finance, and philosophy. Episodes accumulate faster than listening time allows. Today there is no way to triage — you either listen to everything, or you guess what's worth your time. The cost of not solving this is a growing backlog, missed high-value content, and no persistent record of health/longevity knowledge from the shows you do consume.

A secondary problem: health-relevant content from podcasts and newsletters (Attia, Huberman, Rhonda Patrick) is consumed and lost — never available for future cross-referencing, comparison against personal lab results, or protocol synthesis.

---

## Target Users

**Primary:** Jeff — one user, personal deployment on existing IronClaw/OpenClaw agent infrastructure with Telegram + iMessage channels and Gmail.

**Secondary (future):** The health knowledge store is designed to support future multi-source queries ("what do Attia and Huberman say about ApoB?") — the data model should anticipate that use case even though the query interface is out of scope for v1.

---

## Success Criteria

- Wake up to an email digest summarizing every new podcast episode from the prior day — no manual action required
- Each summary is good enough to make a confident keep/delete decision for the Overcast queue
- Health/longevity content is captured to a persistent store and survives agent restarts
- On-demand: ask about a specific episode (or a vague topic) and get a summary within reasonable time
- Summary quality is transparent — every summary states how it was generated
- The system self-improves: learns transcript availability per show, learns preferred summary style per show via feedback

---

## User Experience

### Daily Flow
1. 11:00 PM — Nightly cron fires. Skill checks all active RSS feeds for new episodes since last run.
2. Overnight — For each new episode: attempt transcript acquisition (published transcript → scrape → Whisper fallback), generate adaptive summary, tag and store health-relevant content.
3. Also overnight — Poll Gmail IMAP for unread emails labeled "health-newsletters" (Attia + Rhonda Patrick). Fetch full content (follow member links if needed). Summarize and store to health DB. Mark emails as read.
4. 6:00 AM — Send Telegram/iMessage notification: "Hey Jeff, processed 3 new podcasts overnight — Attia, All-In, Orvis Fly Fishing. Enjoy your digest!" (or "Still processing — check back soon" if not finished).
5. Morning — Email digest lands in inbox. One summary card per episode. One-liner for newsletters ("2 health newsletters archived to health store"). Health-relevant summaries are also written to the health knowledge store.

### On-Demand Flow
- **Specific episode:** "Give me a summary of Peter Attia episode 224" or paste a podcast URL → skill finds/fetches/transcribes → emails summary
- **Vague/semantic:** "Give me a summary of a recent Huberman episode on the vagus nerve" → skill searches episode titles/descriptions across Huberman's feed → finds best match → summarizes → emails
- **Re-summarize:** "You gave me a short summary of Triggernometry episode 42 — give me a full 3–5 paragraph version" → skill re-processes with deeper depth setting
- All on-demand requests come via Telegram or iMessage

### Summary Style — Adaptive Per Show
IronClaw auto-classifies summary style on first encounter. User can correct via Telegram and the skill learns:
- **Deep health/science** (Attia, Huberman, Rhonda Patrick, FoundMyFitness, Valley to Peak, Barbell Shrugged): 3–4 paragraphs covering key claims, protocols, studies cited, actionable takeaways
- **Long-form interview** (Tim Ferriss, Shawn Ryan, Winston Marshall, Invest Like the Best): Content-adaptive — assess episode depth, scale accordingly (1 para to 3+ paras)
- **Commentary/politics/philosophy** (All-In, Triggernometry, Philosophize This!, Just Thinking, TRIGGERnometry): 1–2 paragraphs, key arguments and positions
- **Hunting/outdoors** (MeatEater, Backcountry Hunting, Western Hunter, ElkShape, Live Wild, etc.): Topic-breakdown format — key topics with a short blurb each (elk strategies, gear discussed, guest profile, location/species, tips)
- **Short-form/devotional** (Renewing Your Mind, Ask Ligonier, Grace to You): 2–3 sentences, core topic and scripture/theme

### Summary Card Format (per episode in digest)
```
[Show Name] — [Episode Title]
[Source: Published Transcript | Whisper (local) | Show Notes]
[Date]

[Adaptive summary body]

Listen: [RSS/episode link]
```

---

## Scope Boundaries

### ✅ IN — v1

**Podcast Monitoring**
- OPML import for initial feed list (35 feeds from Overcast)
- RSS feed polling — daily check for new episodes
- Show state management: `active` (monitor ongoing) | `one-off` (get this episode, then done) | `inactive` (done, skip)
- Add/remove shows via Telegram ("monitor this show" / "stop monitoring X" / "just get this one episode")

**Transcript Acquisition Pipeline (per episode)**
- Check if published transcript available (cached knowledge per show)
- Scrape published transcript if available (try first — faster than Whisper)
- Subscription-protected transcripts: use stored site credentials + browser tool to fetch member pages
- Fall back to local Whisper (whisper.cpp on Mac host via HTTP bridge) if scrape unavailable or fails
- Final fallback: show notes/description only
- Cache transcript availability per show so future episodes skip failed strategies

**Summarization**
- Adaptive summary style auto-classified per show; correctable via Telegram feedback
- Summary depth overridable on-demand ("give me a longer summary of that episode")
- Every summary labeled with its source (Published Transcript / Whisper / Show Notes)

**Delivery**
- Daily email digest — all new episode summaries + newsletter one-liner
- 6:00 AM Telegram/iMessage nudge with episode count (or "still processing" if running)
- On-demand summaries delivered via email

**On-Demand Requests**
- Specific episode by name/number or URL
- Vague/semantic: "recent Huberman on vagus nerve" → search feed → best match → summarize
- Re-summarize at different depth on request

**Health Newsletter Ingestion**
- Gmail IMAP polling for label "health-newsletters" during nightly run
- Self-contained emails: summarize directly from body
- Teaser emails (Attia-style): follow member link, fetch full content using stored credentials, then summarize
- Summaries stored to health knowledge store; not emailed separately
- Daily digest includes one-liner: "2 health newsletters archived"

**Health Knowledge Store**
- Show-level classification: `always` (Attia, Huberman, Rhonda Patrick, Barbell Shrugged, Valley to Peak) | `sometimes` (Ferriss, Shawn Ryan) | `never` (hunting shows, etc.)
- Per-episode override: "add that Hunt Backcountry training episode to the health store"
- Stored content: full summary text + metadata (show, episode, date, source quality, topics detected)
- Schema anticipates future query interface (cross-show topic search, biomarker mentions, protocol extraction)

**Configuration & Credentials**
- Private RSS URLs stored per show (Attia Supercast, FoundMyFitness already in OPML)
- Site login credentials stored securely in `.env` (Attia website, FoundMyFitness website)
- Gmail IMAP credentials (reuse existing Gmail setup)
- Whisper bridge config (host URL, model selection)

### ❌ OUT — v1

- Overcast queue automation (no API available)
- Spotify queue management
- Health DB query interface ("what does Attia say about ApoB?") — store only
- Telegram/iMessage inline summaries (email only for summaries)
- Multi-user support
- Non-English podcasts
- Video podcast support

### ⚠️ MAYBE / FUTURE

- Health DB query interface via Telegram/iMessage (natural language queries across stored health content + personal lab results)
- Overcast/podcast app integration if an API becomes available
- Additional health newsletter sources beyond Attia + Rhonda Patrick
- Automatic lab result ingestion to health store
- Weekly "health insights" synthesis email (cross-show patterns, protocol recommendations)

---

## Constraints

- **Platform:** Runs as an IronClaw OpenClaw skill inside Docker container; scripts execute in container at `/home/openclaw/.openclaw/workspace/skills/`
- **Available bins:** `bash`, `python3`, `node`, `curl`, `openssl` — NO `jq` (use `python3` for all JSON)
- **Whisper:** Runs on Mac host via HTTP bridge (whisper.cpp + Metal GPU); skill calls `host.docker.internal:{port}`
- **Email sending:** Existing `send-email.sh` (Gmail SMTP via curl) already in workspace
- **No `web_search`:** `web_fetch` only (no Brave/Perplexity key); transcript discovery via direct URL patterns, not search
- **Persistent storage:** JSON/markdown files in `workspace/skills/podcast-summary/` — no external DB, survives container restarts via volume mount
- **Processing time:** Whisper on a 2-hour episode ≈ 5–15 min on Mac Metal; plan for overnight window (11PM–6AM = 7 hours). At ~10 min/episode worst case, supports ~40 episodes per night comfortably.
- **Model:** Primary `openai/gpt-5-mini`; Ollama fallback for summarization if needed

---

## Integration

**Touches:**
- `workspace/skills/send-email/` — email delivery
- `workspace/skills/daily-report/` — pattern reference for cron + logging
- `workspace/skills/restaurant-scout/` — pattern reference for knowledge file + logging
- `workspace/skills/productwatcher/` — pattern reference for vault/state management
- Whisper host bridge (new host service, similar to piglow-signal bridge pattern)
- Gmail IMAP (new, uses existing Gmail credentials from `.env`)
- Cron tool for 11PM nightly trigger + 6AM notification

**Avoids:**
- `openclaw.json` schema changes (all config in skill files, not gateway config)
- Modifying existing skills

**External dependencies:**
- whisper.cpp binary on Mac host
- Gmail IMAP access (existing account)
- Private RSS feed URLs (already in OPML for Attia, Rhonda Patrick)
- Subscriber site credentials for Attia + FoundMyFitness (new, stored in `.env`)

---

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Email delivery for summaries | Yes (not Telegram) | Summaries are multi-paragraph; email is the right reading format |
| Daily digest vs per-episode emails | Digest | Cleaner inbox; you wake up to one email not many |
| Morning notification | 6AM Telegram/iMessage | Fixed time regardless of processing completion; "in progress" message if not done |
| Whisper deployment | Local (Mac host bridge) | Free, fast with Metal, no per-minute cost for long episodes |
| Transcript strategy | Scrape first → Whisper fallback | Scrape is faster when available; Whisper is reliable universal fallback |
| Summary style | Auto-classify + Telegram feedback | Reduces upfront config; self-correcting over time |
| Health content tagging | always/sometimes/never list + per-episode override | Reduces per-episode manual work; flexible for edge cases |
| Health store content | Full summary text + metadata | Richer context for future queries; structured metadata enables topic/biomarker search |
| Newsletter ingestion | Gmail IMAP + "health-newsletters" label | Reuses existing Gmail credentials; no new email address required |
| Newsletter delivery | Health store only (digest one-liner) | You already have the email; second email is redundant noise |
| Subscription transcript access | Scrape member site first, Whisper fallback | Transcripts are higher quality than Whisper; scraping needs validation testing |
| On-demand requests | Telegram or iMessage, result via email | Consistent with summary delivery channel |
| Vague episode search | Supported ("recent Huberman on vagus nerve") | High-value UX; search episode titles/descriptions across feed |

---

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Anti-scraping on Attia/FoundMyFitness websites | Medium | Test early; browser tool handles JS-heavy sites; Whisper is reliable fallback |
| Whisper processing time exceeds 7-hour overnight window on heavy nights | Low | ~40 episode capacity; typical nights are 2–5 new episodes across 28 shows |
| RSS feed URL changes for private feeds | Medium | Monitor for 401/403 errors; alert via Telegram if feed fails |
| Gmail IMAP rate limiting | Low | Single nightly poll; well within limits |
| Summary quality on show-notes-only fallback | High for some shows | Transparent sourcing label sets expectations; drives motivation to get transcripts working |
| Scope creep: health DB query gets pulled into v1 | Medium | Explicitly OUT; keep it as a data structure design concern only |

---

## Next Steps

**Complexity: L (Large)**

Hard stops triggered: new service component (Whisper bridge), external integrations (Gmail IMAP, subscriber sites, whisper.cpp host service), new data model (health store), multiple pipeline stages.

Recommended path: `/spectre:plan` → `/spectre:create_tasks` with phased delivery:
- **Phase 1:** RSS polling + show notes summaries + email digest (core loop, no Whisper yet)
- **Phase 2:** Whisper bridge + transcript scraping pipeline
- **Phase 3:** Health store + newsletter ingestion
- **Phase 4:** On-demand requests + semantic episode search
