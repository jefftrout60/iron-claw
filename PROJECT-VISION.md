# Personal Health & Podcast Intelligence System — Project Vision

## End State

A personal AI assistant running on my home network, contactable from my phone,
that knows my health data, has digested my trusted expert sources, and can answer
specific questions about my health and training by cross-referencing both.
Runs quietly in the background, costs almost nothing, shuts down instantly on demand.

---

## System 1: Podcast Intelligence

**Purpose:** Keep me caught up on my podcast library. Build a personal reference
library from trusted expert sources that feeds System 2.

**Outputs:**
- Weekly email digest of new episodes across all 34 subscribed feeds with short summaries
- On-demand deep summaries for specific episodes (sauna-readable length)
- Complete back-catalog library built overnight for priority shows (Huberman, Attia,
  Rhonda Patrick first) — stored locally, queryable
- The library becomes a knowledge base input for System 2

**Sources:**
- 34 Overcast subscriptions (OPML exported)
- Private RSS feeds for Peter Attia (Supercast) and Rhonda Patrick (FoundMyFitness)
  — already contain personalized URLs with paid content
- YouTube captions for shows with strong YouTube presence (Elkshape, MeatEater,
  Shawn Ryan, Triggernometry, Winston Marshall)
- Whisper transcription for shows without transcripts (hunting/niche shows)

**Transcript strategy by show:**
- Full transcripts available: Tim Ferriss, Huberman, Attia (private feed),
  Rhonda Patrick (member feed), Philosophize This, All-In, Invest Like the Best
- YouTube captions: Elkshape, MeatEater, Shawn Ryan, Triggernometry, Winston Marshall,
  Barbell Shrugged
- Whisper jobs: Orvis, VOMRadio, Hunting Dog, Beyond the Kill, Eastmans' Elevated,
  Tundra Talk, Renewing Your Mind, Better Brain Fitness, Backcountry Hunting, Rokcast,
  Modern Day Sniper, Grace to You, Mindful Hunter, Valley to Peak, Hornady,
  Live Wild with Remi Warren, Just Thinking, Let Jaime Talk, The American West

---

## System 2: Personal Health Intelligence

**Purpose:** Answer specific health and training questions by cross-referencing
my personal data against my trusted expert knowledge base.

**Personal data sources:**
- Oura Ring (REST API — sleep, HRV, readiness, activity, skin temp)
- Blood test spreadsheet (decade of data, normalized, tabs by category:
  CBC, hormones, thyroid, metabolic, etc. — columns=dates, rows=markers)
- DEXA scans (PDFs)
- Workout data (TBD — Apple Health, Garmin, or Training Peaks)

**Expert knowledge base sources:**
- System 1 podcast library (primary)
- Examine.com (structured supplement database, has API)
- Consensus (AI-powered academic search, peer-reviewed research, has API)
- PubMed (free API, primary research if needed)
- Attia and Huberman written newsletters/articles
- Kindle highlights (existing KindleSync project)

**Example queries:**
- "My HRV has been trending down for 3 weeks — what does my training load look
  like and what does Attia say about HRV suppression?"
- "My last blood panel showed elevated ApoB — what protocol does Attia recommend
  and has anything changed in recent episodes?"
- "Compare my body composition DEXA trend over 2 years against Huberman's
  recommendations for someone doing 5/3/1 + Zone 2"
- "Based on my sleep data, am I getting enough deep sleep? What does Rhonda say
  about deep sleep and immune function?"
- "I'm peaking for a September elk hunt — what do my fitness markers say and what
  does Elkshape/Mountain Tough recommend for the final 6 weeks?"
- "What is Rhonda Patrick's recommended supplement protocol for joint health?"

---

## Infrastructure

**Platform:** IronClaw (Docker-hardened OpenClaw) on MacBook Pro (Intel, 2020)
  → migrate to M5 Max Mac when it arrives

**Model strategy:**
- Now (Intel Mac): Kimi K2.5 (Moonshot AI) — not OpenAI, OpenAI-compatible API,
  256K context window, ~$5-10/month for this workload
- M5 Max arrives: Switch to Ollama local (Qwen3 14B) — free, private, fast on
  Apple Silicon. Keep Kimi K2.5 as fallback for complex reasoning.

**Interface:**
- Telegram (primary, works from iPhone anywhere)
- iMessage via BlueBubbles (future, after stable)
- Master off switch: Telegram "shut down" command + desktop script

**Home automation:** Home Assistant on Geekom PC (separate, future integration)

---

## Build Sequence

1. ✅ GitHub account + fork kosar/iron-claw
2. ✅ Docker Desktop installed
3. ✅ IronClaw image built
4. 🔄 Configure and start sample agent (Moonshot API key, Telegram bot)
5. Customize SOUL.md, IDENTITY.md, USER.md for personal context
6. Build master off switch skill
7. /spectre:scope — design System 1 (Podcast Intelligence)
8. Build System 1 using /spectre:execute
9. Start overnight back-catalog runs (Huberman, Attia, Rhonda Patrick)
10. M5 Max arrives — migrate, switch to Ollama local
11. /spectre:scope — design System 2 (Health Intelligence)
12. Build System 2
13. Integrate Examine.com and Consensus as knowledge sources
14. iMessage via BlueBubbles (stretch)
15. Home Assistant integration (stretch)

---

## Key Constraints

- No OpenAI if avoidable
- All sensitive personal data stays local (never sent to cloud models)
- Master off switch always available
- Start locked down, grant access incrementally
- Don't build System 2 until System 1 library is populated enough to be useful
