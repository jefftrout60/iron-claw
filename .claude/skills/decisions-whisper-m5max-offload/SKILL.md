---
name: decisions-whisper-m5max-offload
description: Use when working on whisper_client.py, transcript_fetcher.py, or planning Whisper transcription infrastructure
user-invocable: false
---

# Whisper Offload to M5 Max

**Trigger**: whisper, M5 Max, transcription, faster-whisper, WHISPER_BASE_URL, whisper_client, offload, local whisper
**Confidence**: high
**Created**: 2026-03-28
**Updated**: 2026-03-28
**Version**: 1

## Decision

Run a local Whisper HTTP server on the M5 Max. Point `whisper_client.py` at it via a
configurable `WHISPER_BASE_URL` env var. Defaults to OpenAI when unset.

## Context

- Intel MacBook runs IronClaw/Docker as a dedicated always-on server (no sleep)
- M5 Max is daily driver — also hosts the Whisper server on LAN
- Current cloud Whisper costs ~$0.006/min and has 413 errors on large files
- M5 Max Neural Engine runs `large-v3` significantly faster than cloud, no API cost

## Architecture

```
Intel Mac (Docker / IronClaw)      M5 Max
──────────────────────────────     ──────────────────────────
engine.py / on_demand.py      →    faster-whisper-server
transcript_fetcher.py         →    (OpenAI-compatible API)
whisper_client.py             →    POST /v1/audio/transcriptions
  WHISPER_BASE_URL=            ←    transcript text returned
  http://m5max.local:8080
```

## Implementation Plan

**Step 1 — M5 Max: run faster-whisper-server**
```bash
docker run -d --name whisper-server \
  -p 8080:8080 \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  fedirz/faster-whisper-server:latest-cpu \
  --model large-v3
```
(Use `-latest-metal` variant if available for Neural Engine acceleration)

**Step 2 — whisper_client.py: make base URL configurable**
```python
# In whisper_client.py, replace hardcoded OpenAI URL with:
base_url = os.environ.get("WHISPER_BASE_URL", "https://api.openai.com")
# Use base_url when building the transcriptions endpoint
```

**Step 3 — Intel Mac .env: add**
```
WHISPER_BASE_URL=http://m5max.local:8080
```
(Or use the M5 Max's LAN IP if mDNS isn't reliable across Docker bridge)

## Consequences

- No more 413 errors on large audio files (local has no size limit)
- No Whisper API costs
- Audio never leaves LAN
- Intel Mac Docker container needs network access to M5 Max LAN IP
- If M5 Max is unavailable, fall back to `WHISPER_BASE_URL` unset = cloud OpenAI

## Ollama + MLX on M5 Max (April 2026)

Ollama v0.19 (preview, released 2026-03-31) switched Apple Silicon backend to Apple's MLX framework:
- **2x faster token generation** vs previous Ollama
- **4x faster than M4 Pro/Max**
- M5 Max has 40 GPU cores with Neural Accelerators — MLX exploits all of them
- 128GB unified memory runs Llama 4 Maverick (Q4_K_M, ~40GB) fully in RAM at ~18-25 tok/sec

**Implication for IronClaw:** Once MLX support broadens beyond Qwen3.5-A3B to Llama/other models, route nightly podcast summarization and health intelligence queries through local Ollama on M5 Max instead of OpenAI. Zero cost, full privacy, no API limits.

**Current status:** v0.19 preview supports Qwen3.5-35B-A3B only. Watch for broader model support before switching summarization pipeline.

## Why Intel Mac Stays as the Server

Intel Mac becomes a dedicated always-on IronClaw/Docker host (no sleep).
The Docker-zombie / stale-PID-lock issues were caused by the Mac sleeping during
extended periods. A dedicated server that never sleeps eliminates that entire
failure class.
