# Scope Clarifications — Podcast Summary Skill
Date: 2026-03-21
Branch: boostrap-ollama-v2

## Confirmed Boundaries So Far

**IN:** OPML import, active/inactive/one-off show states, 11PM daily check, overnight processing, email digest, morning Telegram/iMessage nudge, adaptive summary depth per show type, sourcing transparency, local Whisper, transcript learning/caching, on-demand single-episode summaries, health content storage
**OUT:** Overcast/Spotify queue automation, health DB query interface, Telegram inline summaries

---

## Remaining Clarifications

**Q1 — Morning notification timing**

Processing runs overnight starting at 11PM and may take a while (Whisper on long episodes). Should the morning Telegram/iMessage notification fire at a fixed time (e.g., 7:00 AM regardless of when processing finished), or as soon as overnight processing completes (which could be 1AM or 5AM depending on episode count)?

<response>
fire at 6AM if procesing is complete. if not complete, fire at 6AM to say "in progress"
</response>

---

**Q2 — On-demand single episode: how do you provide it?**

When you say "give me a summary of this episode I just heard about from a friend," how would you typically specify it?
- Just the show name + episode number/title ("Peter Attia episode 224")
- A URL (e.g., a podcast link someone texted you)
- Either — the skill should handle both

And should this on-demand request come via Telegram, or also iMessage?

<response>
could be either. wonder if something more vague is possible? i.e. - "give me a summary of a recent Huberman podcast that covered the vagus nerve"
</response>

---

**Q3 — Show type / summary style classification**

Summary style adapts by show (paragraphs for Attia/Huberman, topic-breakdown for hunting shows, short blurb for Triggernometry, etc.). Should the skill:
a) Auto-detect based on show category/content on first run and remember it
b) Start with your manually set defaults per show and let it learn from there
c) You don't care — just let it figure it out and you'll correct it via Telegram if it gets it wrong

<response>
since it is possible I would want to change on the fly sometime in the future, lets let IronClaw figure it out and I can provide correction via Telegram so it can learn from my feedback. Also, one thing I should be able to request is "hey, you just gave me a short summary of Triggernometry episode yy on topic x, but that was super interesting, please provide a 3-5 paragraph summary with more details for that one show"
</response>

---

**Q4 — Health content tagging**

For the health retention store, should "health-relevant" be:
a) Whole shows you flag (e.g., Attia, Huberman, Rhonda Patrick, Barbell Shrugged always = health)
b) Per-episode, auto-detected by content (a Ferriss episode about anti-aging = health; his episode about a children's book author = not)
c) Both — show-level defaults with per-episode override

Also: should the health store keep the full summary text, or structured extracts (claims, protocols, biomarkers mentioned)?

<response>
for the second question, full summary text. for the first, ideally I would not have to flag them individually every time. maybe start with a list of "always", "sometimes" and "never". but just like the previous answer, I should be able to say something like "you just did a summary of the Hunt Backcountry podcast and it covered training for mountain hunts, please add that one to the health store"
</response>

---

**Q5 — Subscription transcript access**

Your OPML already contains private RSS feed URLs for Attia (Supercast) and Rhonda Patrick (FoundMyFitness member feed) — so audio is accessible. But their detailed transcripts live on their subscriber websites (behind login). For transcript-quality summaries on those shows, should the skill:
a) Use Whisper on the audio from the private RSS (no website login needed)
b) Also try to scrape the transcript from the website (requires storing site credentials separately from RSS)
c) Whisper is fine for those — website scraping is too fragile

<response>
hard to answer this one given your 'c' choice. how fragile is website scraping? if it is relible enough, then probably a scrape as that would be quicker. and if it is a full transcript, then we are done. if not, then may need to also Whisper and merge the summary. we also don't know if they have some detection software on their site that might prevent a scrape, so we should test this. obviously, if a scrape fails, the backup should be Whisper.
</response>
