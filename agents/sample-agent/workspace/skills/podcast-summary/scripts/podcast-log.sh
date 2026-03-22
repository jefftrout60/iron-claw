#!/usr/bin/env bash
# podcast-log.sh — Structured logging for Podcast Summary skill
#
# Usage: podcast-log.sh <event> [key=value ...]
#
# Events:
#   engine_start              — Nightly engine run initiated
#   rss_poll                  — RSS feed polled for new episodes
#   episode_found             — New episode discovered in feed
#   transcript_fetch          — Transcript acquisition attempt
#   transcript_strategy_failed — One strategy failed, trying next
#   whisper_start             — Whisper transcription started
#   whisper_done              — Whisper transcription complete
#   summarize                 — LLM summarization call
#   health_tag                — Episode tagged for health store
#   digest_send               — Nightly digest email sent
#   newsletter_fetch          — Gmail health-newsletter fetched
#   newsletter_store          — Newsletter stored to health_knowledge.json
#   engine_done               — Nightly engine run complete
#   on_demand_start           — On-demand request initiated
#   on_demand_done            — On-demand request complete
#   error                     — Unrecoverable failure
#
# Key=value pairs (use what applies):
#   show="The Peter Attia Drive"
#   episode="Episode 224"
#   strategy="fetch_happyscribe|whisper_large|whisper_small|show_notes|published_transcript"
#   duration_seconds=1245
#   source_quality="published_transcript|whisper_large|whisper_small|show_notes"
#   error_msg="free text description of failure"
#   count=3

set -uo pipefail

LOG_DIR="/tmp/openclaw"
LOG_FILE="${LOG_DIR}/podcast-summary.log"

mkdir -p "$LOG_DIR" 2>/dev/null || true

EVENT="${1:-unknown}"
shift 2>/dev/null || true

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ" 2>/dev/null || date -u +"%Y-%m-%dT%H:%M:%SZ")

# Build JSON using python3 — jq is not available in the container
JSON="{\"timestamp\":\"${TIMESTAMP}\",\"event\":\"${EVENT}\""

for ARG in "$@"; do
  KEY="${ARG%%=*}"
  VALUE="${ARG#*=}"
  [ "$KEY" = "$ARG" ] && continue
  # Use python3 to safely escape the value and determine numeric vs string type
  ENTRY=$(python3 -c "
import json, sys
key = sys.argv[1]
val = sys.argv[2]
if val.lstrip('-').isdigit():
    print(',\"' + key + '\":' + val)
else:
    print(',\"' + key + '\":' + json.dumps(val))
" "$KEY" "$VALUE" 2>/dev/null || true)
  JSON="${JSON}${ENTRY}"
done

JSON="${JSON}}"

echo "$JSON" >> "$LOG_FILE" 2>/dev/null
echo "$JSON"
