#!/bin/bash
AGENT="sample-agent"
SCRIPTS="/home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts"
LOG="${SCRIPTS}/backlog2_output.log"
export PYTHONPATH="$SCRIPTS"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%S) [backlog] $*"; }

run_ep() {
    local query="$1"
    local depth="${2:-standard}"
    log "START: ${query} (depth=${depth})" | tee -a "$LOG"
    result=$(python3 -c "
import sys, on_demand
result = on_demand.run('${query}', agent_name='${AGENT}', depth='${depth}')
sq = result.get('source_quality','')
cached = result.get('cached', False)
err = result.get('error','')
if err:
    print(f'[backlog] error: {err} (sq={sq}, cached={cached})')
else:
    title = result.get('title','?')
    print(f'[backlog] ok: {title} (sq={sq}, cached={cached})')
" 2>&1)
    echo "$result" | tee -a "$LOG"
    log "DONE: ${query}" | tee -a "$LOG"
    echo "---" | tee -a "$LOG"
}

echo "=== BACKLOG2 START $(date -u) ===" | tee -a "$LOG"

run_ep "Winston Marshall Show Michael Shermer" "standard"
run_ep "Winston Marshall Show Lionel Shriver" "standard"
run_ep "Winston Marshall Show Panyi Miklos Population Collapse" "standard"

echo "=== BACKLOG2 COMPLETE $(date -u) ===" | tee -a "$LOG"
