---
concept: Health DB Hardening Sprint
date: 2026-05-01
boundaries_confirmed: true
---

# Scope Clarifications — Health DB Hardening Sprint

## Confirmed boundaries (from conversation)

Items proposed by Jeff: 14 items across ops, DB integrity, sync reliability, search quality, observability, agent behavior.

## Decisions made

| Question | Answer | Decided by |
|----------|--------|------------|
| Items #2 (XML key names) and #10 (XML behavioral tests) — same sprint? | Yes, both. Export first, tests after. | Jeff |
| Units registry depth — column only or full importer wiring? | Full wiring into lab importers | Jeff ("better to wire into all importers?") |
| Oura partial failure — incremental or full refactor? | Full fix — refactor fetch_all return semantics | Jeff |
| Rule 6c scope — routing only or also AGENTS.md? | Claude's call — touches health_query.py routing + AGENTS.md Rule 6c wording | Claude |
| DATA_CARD.md — manual or auto-generated? | Auto-generated per sync | Jeff |
| Sprint time budget | No constraint — work until done | Jeff |

## Claude's calls (Rule 6c + units registry)

**Rule 6c "ambiguous" definition**:
- Default to "now": bare number ("185.2"), "this morning", "today", "just now"
- Ask once: explicit but ambiguous temporal reference ("the other day", "last week", "Tuesday")
- Use directly: clear past date ("185.2 on April 15th", "yesterday 185.2")

**Units registry scope**: Lab importers only. Oura/Withings/Apple Health use standardized units and don't have the cross-provider ambiguity problem. Risk is specifically lab provider switching (Quest → Labcorp, etc.).

<response></response>
