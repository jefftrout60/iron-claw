# Podcast Knowledge — Show Metadata Reference

This file is the agent's reference for all monitored shows. Use it during on-demand requests to
understand each show's characteristics, preferred transcript strategy, and summary style before
calling on_demand.py.

---

## Summary Style Definitions

| Style | When to use | Depth |
|-------|-------------|-------|
| `deep_science` | Health/longevity science shows with dense research content | 3–4 paragraphs: key claims, protocols, studies cited, actionable takeaways |
| `long_form_interview` | Conversation-format shows; content depth varies by episode | 1–3 paragraphs content-adaptive — assess episode topic, scale accordingly |
| `commentary` | Opinion, politics, philosophy — arguments and positions | 1–2 paragraphs: key arguments and positions taken |
| `hunting_outdoor` | Hunting, fishing, backcountry — topic-breakdown format | Per-topic blurbs: Strategies · Gear Discussed · Guest Profile · Location/Species · Tips |
| `devotional` | Short-form religious/devotional content | 2–3 sentences: core scripture/theme and main point |

---

## Known Transcript Sources

| Show | Primary Source | Strategy | Notes |
|------|---------------|----------|-------|
| The Tim Ferriss Show | `tim.blog/*-transcript/` | `fetch_tim_blog` → `whisper_small` | Free public transcripts, highly reliable |
| Huberman Lab | `podscript.ai/podcasts/huberman-lab-podcast/[slug]` | `fetch_podscript_ai` → `whisper_large` | Third-party; 403 risk — falls through to Whisper |
| The Peter Attia Drive | `podcasts.happyscribe.com/the-peter-attia-drive/[slug]` | `fetch_happyscribe` → `whisper_large` | 403 risk; Whisper fallback with large-v3 |
| FoundMyFitness Member's Feed | Whisper only | `whisper_large` | Private audio; no public transcript source |
| All others | `podcast:transcript` tag in RSS | `check_transcript_tag` → `show_notes` → `whisper_small` | Check tag first; fall back to show notes |

---

## Show Registry

### Health / Longevity

---

**The Peter Attia Drive**
- Show ID: `peter-attia-drive`
- Health tier: `always`
- Summary style: `deep_science`
- Transcript strategy: `fetch_happyscribe` → `whisper_large`
- Notes: Private Supercast RSS. Dense longevity science — ApoB, cancer screening, sleep, exercise physiology, zone 2. Typically 2–3 hour episodes.

---

**Huberman Lab**
- Show ID: `huberman-lab`
- Health tier: `always`
- Summary style: `deep_science`
- Transcript strategy: `fetch_podscript_ai` → `whisper_large`
- Notes: Stanford neuroscience. Episodes are protocol-dense. Topics: dopamine, sleep, neuroplasticity, fitness, cold exposure. Typically 2–3 hours.

---

**FoundMyFitness Member's Feed**
- Show ID: `foundmyfitness-members-feed`
- Health tier: `always`
- Summary style: `deep_science`
- Transcript strategy: `whisper_large`
- Notes: Dr. Rhonda Patrick. Genetics, nutrition, longevity, sauna, omega-3. Private RSS feed. No public transcript — Whisper only.

---

**Valley to Peak Nutrition Podcast**
- Show ID: `valley-to-peak-nutrition-podcast`
- Health tier: `always`
- Summary style: `deep_science`
- Transcript strategy: `check_transcript_tag` → `whisper_small`
- Notes: Sports nutrition and performance. Often covers practical dietary interventions.

---

**Better Brain Fitness**
- Show ID: `better-brain-fitness`
- Health tier: `always`
- Summary style: `deep_science`
- Transcript strategy: `check_transcript_tag` → `whisper_small`
- Notes: Cognitive health, brain optimization, neurological wellness.

---

**Barbell Shrugged**
- Show ID: `barbell-shrugged`
- Health tier: `always`
- Summary style: `deep_science`
- Transcript strategy: `check_transcript_tag` → `whisper_small`
- Notes: Strength, performance, sports science. Crosses into health topics frequently.

---

### Long-Form Interview / Finance / Philosophy

---

**The Tim Ferriss Show**
- Show ID: `tim-ferriss-show`
- Health tier: `sometimes`
- Summary style: `long_form_interview`
- Transcript strategy: `fetch_tim_blog` → `whisper_small`
- Notes: Wide-ranging interviews. Health episodes (biohacking, longevity guests) should be health-tagged; most episodes are not. tim.blog publishes free transcripts — check these first.

---

**The Shawn Ryan Show**
- Show ID: `shawn-ryan-show`
- Health tier: `sometimes`
- Summary style: `long_form_interview`
- Transcript strategy: `check_transcript_tag` → `whisper_small`
- Notes: Military, special operations, resilience. Occasionally covers mental health, trauma, PTSD treatments — those episodes warrant health tagging.

---

**Invest Like the Best**
- Show ID: `invest-like-the-best`
- Health tier: `never`
- Summary style: `long_form_interview`
- Transcript strategy: `check_transcript_tag` → `whisper_small`
- Notes: Finance and investing. Patrick O'Shaughnessy. Typically 60–90 min episodes.

---

**The Winston Marshall Show**
- Show ID: `the-winston-marshall-show`
- Health tier: `never`
- Summary style: `long_form_interview`
- Transcript strategy: `check_transcript_tag` → `whisper_small`
- Notes: Culture, politics, ideas. Marsall (Mumford & Sons) interviews journalists, politicians, writers.

---

**All-In with Chamath Jason Sacks & Friedberg**
- Show ID: `all-in-with-chamath-jason-sacks-friedberg`
- Health tier: `never`
- Summary style: `commentary`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Tech, VC, politics, markets. Weekly roundtable. Show notes are typically good enough for commentary summaries.

---

### Commentary / Politics / Philosophy

---

**TRIGGERnometry**
- Show ID: `triggernometry`
- Health tier: `never`
- Summary style: `commentary`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Political commentary and interviews. Konstantin Kisin and Francis Foster.

---

**Philosophize This!**
- Show ID: `philosophize-this`
- Health tier: `never`
- Summary style: `commentary`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Solo episodes exploring philosophy chronologically. Dense but accessible. Show notes are brief — Whisper fallback may be needed for full summary.

---

**Just Thinking Podcast**
- Show ID: `just-thinking-podcast`
- Health tier: `never`
- Summary style: `commentary`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Reformed Christian thought, apologetics, culture commentary.

---

**Let Jaime Talk Podcast**
- Show ID: `let-jaime-talk-podcast`
- Health tier: `never`
- Summary style: `commentary`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Culture, politics, conservative commentary.

---

**The American West**
- Show ID: `the-american-west`
- Health tier: `never`
- Summary style: `commentary`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: History, culture, and stories of the American West.

---

### Devotional / Religious

---

**Renewing Your Mind**
- Show ID: `renewing-your-mind`
- Health tier: `never`
- Summary style: `devotional`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: R.C. Sproul / Ligonier Ministries. Short daily episodes. Show notes include scripture references and outline.

---

**Ask Ligonier**
- Show ID: `ask-ligonier`
- Health tier: `never`
- Summary style: `devotional`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Ligonier Ministries Q&A format. Short episodes answering theological questions.

---

**Grace to You Radio Podcast**
- Show ID: `grace-to-you-radio-podcast`
- Health tier: `never`
- Summary style: `devotional`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: John MacArthur. Daily expository Bible teaching. Short episodes.

---

### Hunting / Fishing / Outdoors

---

**The Orvis Fly-Fishing Podcast**
- Show ID: `orvis-fly-fishing-podcast`
- Health tier: `never`
- Summary style: `hunting_outdoor`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Fly fishing tips, destinations, technique, gear. Weekly. Tom Rosenbauer host.

---

**VOMRadio**
- Show ID: `vomradio`
- Health tier: `never`
- Summary style: `hunting_outdoor`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Voices of the Outdoors. Hunting, conservation, wildlife.

---

**The Hunting Dog Podcast**
- Show ID: `the-hunting-dog-podcast`
- Health tier: `never`
- Summary style: `hunting_outdoor`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Bird dogs, training, upland hunting. Breed-specific content.

---

**Beyond the Kill**
- Show ID: `beyond-the-kill`
- Health tier: `never`
- Summary style: `hunting_outdoor`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Hunting culture, conservation, lifestyle beyond the harvest.

---

**Eastmans' Elevated**
- Show ID: `eastmans-elevated`
- Health tier: `never`
- Summary style: `hunting_outdoor`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Western big game hunting. Elk, mule deer, antelope. Tag applications, scouting, gear.

---

**ElkShape**
- Show ID: `elkshape`
- Health tier: `never`
- Summary style: `hunting_outdoor`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Elk hunting focused. Dan Staton. Archery, fitness for hunting, elk behavior.

---

**Tundra Talk Podcast**
- Show ID: `tundra-talk-podcast`
- Health tier: `never`
- Summary style: `hunting_outdoor`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Alaska and remote wilderness hunting. Caribou, moose, Dall sheep.

---

**Western Hunter**
- Show ID: `western-hunter`
- Health tier: `never`
- Summary style: `hunting_outdoor`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: DIY western big game hunting. Tag draws, scouting, equipment for backcountry.

---

**Backcountry Hunting Podcast**
- Show ID: `backcountry-hunting-podcast`
- Health tier: `never`
- Summary style: `hunting_outdoor`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Self-guided backcountry and wilderness hunting. Pack-in/pack-out adventures.

---

**Rokcast**
- Show ID: `rokcast`
- Health tier: `never`
- Summary style: `hunting_outdoor`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Sitka Gear / Rok Media. High-country hunting, gear reviews, conservation.

---

**Modern Day Sniper Podcast**
- Show ID: `modern-day-sniper-podcast`
- Health tier: `never`
- Summary style: `hunting_outdoor`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Long-range precision rifle. Competition shooting, ballistics, equipment. Crossover with hunting at distance.

---

**The Hornady Podcast**
- Show ID: `the-hornady-podcast`
- Health tier: `never`
- Summary style: `hunting_outdoor`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Hornady Manufacturing. Ammunition, reloading, ballistics, firearms discussion.

---

**The Hunt Backcountry Podcast**
- Show ID: `the-hunt-backcountry-podcast`
- Health tier: `never`
- Summary style: `hunting_outdoor`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Kifaru / backcountry hunting. Fitness, gear, wilderness navigation. Some fitness-focused episodes may warrant health store consideration — use per-episode override.

---

**Live Wild with Remi Warren**
- Show ID: `live-wild-with-remi-warren`
- Health tier: `never`
- Summary style: `hunting_outdoor`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Solo Hunts TV / Remi Warren. Wild game, wilderness skills, hunting culture.

---

**The MeatEater Podcast**
- Show ID: `the-meateater-podcast`
- Health tier: `never`
- Summary style: `hunting_outdoor`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Steven Rinella. Wide-ranging — hunting, conservation, food, wildlife biology. High-quality show notes.

---

**The Mindful Hunter Podcast**
- Show ID: `the-mindful-hunter-podcast`
- Health tier: `never`
- Summary style: `hunting_outdoor`
- Transcript strategy: `check_transcript_tag` → `show_notes`
- Notes: Hunting with intention, ethics, connection to land and food. Reflective tone.

---

**The American West** (see Commentary section above)

---

**Barbell Shrugged** (see Health section above)

---
