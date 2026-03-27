#!/bin/bash
# Run remaining backlog episodes via cloud (OpenAI Whisper)
AGENT="sample-agent"
SCRIPTS="/home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts"
export PYTHONPATH="$SCRIPTS"

run_ep() {
    local query="$1"
    local depth="${2:-standard}"
    echo "$(date -u '+%Y-%m-%dT%H:%M:%S') [backlog] START: $query (depth=$depth)"
    python3 -c "
import on_demand
result = on_demand.run('${query}', agent_name='${AGENT}', depth='${depth}')
status = result.get('status','?')
title = result.get('episode_title', result.get('message','')[:80])
sq = result.get('source_quality','')
cached = result.get('cached', False)
print(f'[backlog] {status}: {title} (sq={sq}, cached={cached})')
" 2>&1
    echo "$(date -u '+%Y-%m-%dT%H:%M:%S') [backlog] DONE: $query"
    echo "---"
}

echo "=== BACKLOG START $(date -u) ==="

# MeatEater #849 (standard, cloud - has summary_paragraphs=12)
run_ep "MeatEater Podcast #849" "standard"

# Orvis (standard, cloud - uses orvis_fly_fishing Q&A style)
run_ep "Orvis Fly Fishing Blake Katchur Mayfly" "standard"
run_ep "Orvis Fly Fishing Tom Rosenbauer 50 Years" "standard"
run_ep "Orvis Fly Fishing John McPhee" "standard"
run_ep "Orvis Fly Fishing Mike Pease" "standard"
run_ep "Orvis Fly Fishing Brian Slusser" "standard"

# Let Jaime Talk (extended, cloud) - #44 confirmed done, only need #4, #5, #6
run_ep "Let Jaime Talk #4" "extended"
run_ep "Let Jaime Talk #5" "extended"
run_ep "Let Jaime Talk #6" "extended"

# Winston Marshall (standard, cloud)
run_ep "Winston Marshall Show British Deep State Woke" "standard"
run_ep "Winston Marshall Show Allister Heath" "standard"
run_ep "Winston Marshall Show Haviv Rettig Gur" "standard"
run_ep "Winston Marshall Show Raymond Ibrahim" "standard"
run_ep "Winston Marshall Show Michael Shermer" "standard"
run_ep "Winston Marshall Show Lionel Shriver" "standard"
run_ep "Winston Marshall Show Panyi Miklos Population Collapse" "standard"

echo "=== BACKLOG COMPLETE $(date -u) ==="
