---
name: gotchas-cloud-whisper-413-segments
description: Use when cloud Whisper transcription fails with 413 errors, segments return no text, or high-bitrate episodes fall back to show_notes unexpectedly
user-invocable: false
---

# Gotcha: Cloud Whisper 413 Errors on High-Bitrate Episodes

**Trigger**: 413, segment, whisper, show_notes fallback, fetch_openai_whisper, transcript failed
**Confidence**: high
**Created**: 2026-03-27
**Updated**: 2026-03-27
**Version**: 1

## Symptom

Backlog script runs. Some segments transcribe fine, then you see:
```
WARNING _transcribe_segment_openai: HTTP 413: Maximum content size limit (26214400) exceeded
WARNING fetch_openai_whisper: segment N returned no text
```
Episode ends up with `sq=show_notes` and only a few hundred chars of transcript.

## Root Cause

OpenAI Whisper API has a **25MB hard limit per file**. The original code split audio at a fixed 15-minute interval regardless of bitrate. High-bitrate episodes (e.g. ElkShape E465: 164MB, ~300kbps) produce 34MB segments — well over the limit. Low-bitrate episodes (typical podcast: ~128kbps) produce ~10MB segments and were fine, masking the bug.

## Solution

**Fixed in `transcript_fetcher.py` (2026-03-27)**: `_split_audio_ffmpeg()` now uses `ffprobe` to get duration, then calculates segment duration dynamically to target `_OPENAI_WHISPER_TARGET_SEGMENT_MB = 20` MB per segment, capped at 15 minutes.

```python
# Current logic in _split_audio_ffmpeg()
duration = _get_audio_duration_secs(input_path)  # ffprobe
if duration and duration > 0:
    target_bytes = _OPENAI_WHISPER_TARGET_SEGMENT_MB * 1024 * 1024  # 20MB
    calculated = int(target_bytes * duration / file_size)
    segment_secs = max(60, min(calculated, _OPENAI_WHISPER_SEGMENT_MINUTES * 60))
```

Log line confirms fix is active:
```
INFO _split_audio_ffmpeg: file=164.3 MB, duration=4307s, target segment=524s
```

## Prevention

If you see `sq=show_notes` on an episode you expected to transcribe via cloud Whisper, check the log for `413` errors. Re-run after the fix — clear the cached summary first:

```python
# Clear cached summary so on_demand re-runs transcription
for key in ('summary', 'summary_extended', 'source_quality', 'transcript'):
    if key in episode_record:
        del episode_record[key]
```
