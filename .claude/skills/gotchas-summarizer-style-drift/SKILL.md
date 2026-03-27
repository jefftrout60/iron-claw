---
name: gotchas-summarizer-style-drift
description: Use when adding a new summary style to the podcast summarizer, or debugging why a show is being summarized with the wrong style
user-invocable: false
---

# Gotcha: Summary Style Drift — 7 Locations Must Stay in Sync

**Trigger**: adding style, new show style, summarizer drift, classify wrong style, silent misclassification, meateater, orvis_fly_fishing
**Confidence**: high
**Created**: 2026-03-27
**Updated**: 2026-03-27
**Version**: 1

## Symptom

A show is summarized with the wrong style — no error, no log warning. The style auto-classification silently picks `hunting_outdoor` or `long_form_interview` instead of the new style you added.

Or: `python3 summarizer.py --style your_new_style --title "Test"` fails with argparse error: `invalid choice`.

## Root Cause

Adding a new style to `_build_prompt()` is not enough. There are **7 locations in `summarizer.py`** that must all be updated:

| # | Location | What it does | File:Line |
|---|----------|--------------|-----------|
| 1 | `_build_prompt()` | Renders the actual summary prompt | `summarizer.py:230+` |
| 2 | `valid_styles` set | Validates/accepts LLM classification responses | `summarizer.py:497-505` |
| 3 | `_TRANSCRIPT_LIMITS` dict | Sets char truncation limit for the style | `summarizer.py:376-384` |
| 4 | LLM classification prompt text | Tells the LLM this style exists as an option | `summarizer.py:524-532` |
| 5 | `summarize()` docstring | Args docs listing valid styles | `summarizer.py:409-410` |
| 6 | `classify_show_style()` docstring | Return value docs | `summarizer.py:479-495` |
| 7 | CLI `--style` choices (both parsers) | Enables testing the style via CLI | `summarizer.py:571-572, 588-589` |

**Location #4 is the most dangerous**: If the LLM classification prompt doesn't name the style, the model cannot return it — `valid_styles` accepting it is irrelevant. The misclassification is completely silent.

This is exactly what happened with `meateater` and `orvis_fly_fishing` — both were in `_build_prompt()` and `valid_styles` but absent from the LLM prompt and CLI choices.

## Solution

When adding a new style, update ALL 7 locations:

```python
# 1. Add elif branch in _build_prompt()
elif summary_style == "your_new_style":
    system = "..."
    user = "..." + show_extra + topic_map_section + list_suffix + extended_suffix + qa_suffix

# 2. Add to valid_styles in classify_show_style()
valid_styles = {
    ...,
    "your_new_style",
}

# 3. Add to _TRANSCRIPT_LIMITS
_TRANSCRIPT_LIMITS: dict[str, int] = {
    ...,
    "your_new_style": 12000,
}

# 4. Add to LLM user prompt string (CRITICAL — without this, auto-classify is blind)
user = (
    "Classify this podcast into one of these categories: "
    ...
    "your_new_style (brief description for the LLM), "
    ...
)

# 5 & 6. Update docstrings in summarize() and classify_show_style()

# 7. Add to CLI choices (both parsers)
choices=["deep_science", ..., "your_new_style", "devotional"]
```

## Prevention

Run `test_summarizer.py` after adding a style. Two tests catch gaps:

- `TestBuildPromptStyleCoverage` — fails if your style isn't handled in `_build_prompt()`
- `TestClassifyShowStylePromptContents` — fails if your style is missing from the LLM prompt

```bash
cd agents/sample-agent/workspace/skills/podcast-summary/scripts
python3 -m unittest test_summarizer.TestBuildPromptStyleCoverage test_summarizer.TestClassifyShowStylePromptContents -v
```

Add a new test to `TestBuildPromptStyleCoverage` when you add a new style:

```python
def test_build_prompt_your_new_style_returns_tuple(self):
    system, user = _make_prompt("your_new_style")
    self.assertIsInstance(system, str)
    self.assertTrue(len(system) > 0)
    self.assertIsInstance(user, str)
    self.assertTrue(len(user) > 0)
```
