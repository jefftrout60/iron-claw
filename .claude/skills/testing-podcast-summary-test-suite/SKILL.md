---
name: testing-podcast-summary-test-suite
description: Use when running, adding to, or debugging the podcast summary test suite
user-invocable: false
---

# Test Suite: Podcast Summary Scripts

**Trigger**: test_summarizer, test_episode_utils, podcast tests, unittest, 105 tests
**Confidence**: high
**Created**: 2026-03-27
**Updated**: 2026-03-27
**Version**: 1

## Location

```
agents/sample-agent/workspace/skills/podcast-summary/scripts/
├── test_summarizer.py      # 32 tests — summarizer.py behaviors
└── test_episode_utils.py   # 73 tests — on_demand.py + transcript_fetcher.py
```

## Run Command

```bash
cd agents/sample-agent/workspace/skills/podcast-summary/scripts
python3 -m unittest test_summarizer test_episode_utils -v
```

- stdlib `unittest` only — no pytest, no extra dependencies
- Runs in ~15ms

## Test Classes

### test_summarizer.py (32 tests)

| Class | Count | What it covers |
|-------|-------|----------------|
| `TestBuildPromptStyleCoverage` | 7 | Every style returns a valid (str, str) tuple from `_build_prompt()` |
| `TestBuildPromptExtendedDepth` | 3 | Extended suffix injection |
| `TestBuildPromptQATitle` | 3 | Q&A suffix injection from title |
| `TestBuildPromptShowNotes` | 5 | Topic map boundary behavior at 200 chars |
| `TestBuildPromptShowExtraInstructions` | 6 | Per-show custom instructions (rokcast, triggernometry) |
| `TestParseArgsStyleChoices` | 4 | CLI `--style` regression for all 7 styles |
| `TestClassifyShowStylePromptContents` | 4 | LLM classification prompt contains all 7 style names |

### test_episode_utils.py (73 tests)

| Class | Count | What it covers |
|-------|-------|----------------|
| `TestIsUrl` | 5 | `_is_url()` detection |
| `TestIsEpisodeNumberQuery` | 8 | Episode number query parsing |
| `TestMatchByTitle` | 7 | Title-based episode matching |
| `TestMatchByNumber` | 6 | Number-based episode matching |
| `TestFindEpisodeInVault` | 8 | Full routing logic in `_find_episode_in_vault()` |
| `TestEpisodeNumberMatchHelper` | 7 | `_episode_number_match()` predicate |
| `TestStripHtml` | 12 | HTML stripping and entity decoding |
| `TestStripVttTimestamps` | 7 | VTT/SRT timestamp removal |
| `TestMakeSlug` | 10 | Slug generation from episode titles |

## When Adding a New Summary Style

These two classes are the style-drift sentinels — run them after any style change:

```bash
python3 -m unittest test_summarizer.TestBuildPromptStyleCoverage test_summarizer.TestClassifyShowStylePromptContents -v
```

Add a new test to `TestBuildPromptStyleCoverage` for each new style:

```python
def test_build_prompt_your_new_style_returns_tuple(self):
    system, user = _make_prompt("your_new_style")
    self.assertIsInstance(system, str)
    self.assertTrue(len(system) > 0)
    self.assertIsInstance(user, str)
    self.assertTrue(len(user) > 0)
```
