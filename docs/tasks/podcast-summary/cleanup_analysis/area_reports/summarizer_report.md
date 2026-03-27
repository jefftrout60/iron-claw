# Summarizer Style Consistency — Investigation Report

**File**: summarizer.py
**Date**: 2026-03-27

## Ground Truth: Active Style Set

All three internal data structures agree on **7 active styles**:

| Style | `valid_styles` | `_build_prompt()` | `_TRANSCRIPT_LIMITS` |
|---|---|---|---|
| deep_science | L498 | L230 | L377 |
| long_form_interview | L499 | L258 | L378 |
| commentary | L500 | L272 | L379 |
| hunting_outdoor | L501 | L296 | L380 |
| orvis_fly_fishing | L502 | L309 | L381 |
| meateater | L503 | L325 | L382 |
| devotional | L504 | L349 | L383 |

---

## Finding #1 — `classify_show_style()` Docstring (Stale Count + Stale List)

**Lines**: L479, L482, L495

Says "five summary style categories" and lists only 5 (omits meateater, orvis_fly_fishing). Three lines need updating: count word ("five" → "seven") on L479 and L495, and the style list on L482.

**Category: SAFE_TO_UPDATE** | Confidence: High (doc drift only)

---

## Finding #2 — `summarize()` Docstring (Stale Style List)

**Lines**: L409-410

`summary_style` arg docs list only 5 styles, omit meateater and orvis_fly_fishing. Production is unaffected (no validation at call site).

**Category: SAFE_TO_UPDATE** | Confidence: High (doc drift only)

---

## Finding #3 — CLI `sub_sum` `--style` choices (Missing 2 Styles)

**Lines**: L571-572

```python
choices=["deep_science", "long_form_interview", "commentary", "hunting_outdoor", "devotional"]
```

Missing `meateater` and `orvis_fly_fishing`. argparse will reject `--style meateater` in CLI test mode with hard error. **Production unaffected** (production calls `summarize()` directly, bypasses argparse).

**Category: SAFE_TO_UPDATE** | Confidence: High

---

## Finding #4 — CLI Legacy Flat-Args `--style` choices (Missing 2 Styles)

**Lines**: L588-589

Same gap as Finding #3 in the backwards-compatibility parser path.

**Category: SAFE_TO_UPDATE** | Confidence: High

---

## Finding #5 — LLM Classification Prompt Missing 2 Styles (FUNCTIONAL)

**Lines**: L524-529

The prompt sent to the LLM for auto-classification only lists 5 styles:
```
deep_science, long_form_interview, commentary, hunting_outdoor, devotional
```

`meateater` and `orvis_fly_fishing` are **never mentioned** to the LLM. The LLM cannot return a value it was never offered. The `valid_styles` set at L497-505 correctly includes them, and the partial-match guard at L542-544 would accept them — but those guards are unreachable via the LLM path.

**Production consequence**: If `classify_show_style()` is ever called for MeatEater or Orvis shows (i.e., a feed has no `summary_style` set in feeds.json), the LLM will silently classify them as `hunting_outdoor` or `long_form_interview`. No error thrown. Wrong summaries produced.

**Category: NEEDS_VALIDATION** — Confirm whether these shows always have `summary_style` pre-assigned in feeds.json (in which case auto-classification is never triggered), or whether a new MeatEater/Orvis feed could be added without a style, hitting this bug.

**Proposed fix if auto-classification is intended**:
Add to the LLM prompt:
```
"meateater (MeatEater Podcast — hunting, wild food, conservation with Steve Rinella), "
"orvis_fly_fishing (Orvis fly-fishing podcast with Tom Rosenbauer), "
```

---

## Summary

| # | Lines | Type | Category |
|---|---|---|---|
| 1 | L479, L482, L495 | Stale count + list in classify_show_style() docstring | SAFE_TO_UPDATE |
| 2 | L409-410 | Stale style list in summarize() docstring | SAFE_TO_UPDATE |
| 3 | L571-572 | CLI sub_sum --style choices missing meateater, orvis_fly_fishing | SAFE_TO_UPDATE |
| 4 | L588-589 | CLI legacy --style choices missing meateater, orvis_fly_fishing | SAFE_TO_UPDATE |
| 5 | L524-529 | LLM classification prompt omits meateater + orvis_fly_fishing | NEEDS_VALIDATION |

**Finding #5 is the only one with real production impact.**
