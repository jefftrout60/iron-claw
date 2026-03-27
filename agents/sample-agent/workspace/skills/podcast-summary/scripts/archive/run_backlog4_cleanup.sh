#!/bin/bash
# Backlog4 cleanup — re-runs for episodes that failed or had bad transcripts.
#   1. ElkShape E465 — fell back to show_notes (413 segment errors, now fixed)
#   2. Rokcast Mule Deer — transcript only 1131 chars (segments 3-6 413'd)
#   3. Aliquot Arthur Brooks — query miss (now indexed in vault)
#   4. All-In Four CEOs — query miss (now indexed in vault), extended depth
AGENT="sample-agent"
SCRIPTS="/home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts"
export PYTHONPATH="$SCRIPTS"

LOG="/tmp/backlog4_cleanup.log"
exec > >(tee -a "$LOG") 2>&1

clear_cache() {
    local query="$1"
    echo "$(date -u '+%Y-%m-%dT%H:%M:%S') [cleanup] CLEAR CACHE: $query"
    BACKLOG_QUERY="$query" python3 -c "
import os, sys, json
sys.path.insert(0, os.environ['PYTHONPATH'])
import vault, on_demand

q = os.environ['BACKLOG_QUERY']
ep_path = vault.get_vault_path('episodes.json')
data = json.loads(open(ep_path).read())
ep = on_demand._find_episode_in_vault(q, data['episodes'])
if not ep:
    print(f'[cleanup] WARNING: no episode found for cache clear: {q}')
    sys.exit(0)
ep_path = vault.get_vault_path('episodes.json')
data = json.loads(open(ep_path).read())
for e in data['episodes']:
    if e['id'] == ep['id']:
        cleared = []
        for key in ('summary', 'summary_extended', 'source_quality', 'transcript'):
            if key in e:
                del e[key]
                cleared.append(key)
        print(f'[cleanup] Cleared {cleared} from: {e[\"title\"]}')
        break
import os as _os
tmp = str(ep_path) + '.tmp'
with open(tmp, 'w') as f:
    json.dump(data, f, indent=2)
_os.replace(tmp, str(ep_path))
" 2>&1
}

run_ep() {
    local query="$1"
    local depth="${2:-standard}"
    local style_override="${3:-}"
    echo "$(date -u '+%Y-%m-%dT%H:%M:%S') [cleanup] START: $query (depth=$depth, style=$style_override)"
    BACKLOG_QUERY="$query" BACKLOG_DEPTH="$depth" BACKLOG_STYLE="$style_override" python3 -c "
import os, on_demand
q = os.environ['BACKLOG_QUERY']
d = os.environ['BACKLOG_DEPTH']
st = os.environ.get('BACKLOG_STYLE') or None
result = on_demand.run(
    q, agent_name='$AGENT', depth=d,
    strategy_override=['fetch_openai_whisper', 'show_notes'],
    summary_style_override=st,
)
status = result.get('status','?')
title = result.get('episode_title', result.get('message','')[:80])
sq = result.get('source_quality','')
cached = result.get('cached', False)
print(f'[cleanup] {status}: {title} (sq={sq}, cached={cached})')
" 2>&1
    echo "$(date -u '+%Y-%m-%dT%H:%M:%S') [cleanup] DONE: $query"
    echo "---"
}

echo "=== BACKLOG4 CLEANUP START $(date -u) ==="

# 1. ElkShape E465 — clear show_notes fallback summary, re-run with fixed segmentation
clear_cache "ElkShape How Hunt Hard Without Losing Marriage"
run_ep "ElkShape How Hunt Hard Without Losing Marriage"

# 2. Rokcast Mule Deer — clear 1131-char transcript/summary, re-run
clear_cache "Rokcast Backcountry Mule Deer Braxton"
run_ep "Rokcast Backcountry Mule Deer Braxton"

# 3. Aliquot Arthur Brooks — fresh run (was a query miss, now indexed)
run_ep "FoundMyFitness Arthur Brooks Lasting Happiness"

# 4. All-In Four CEOs — fresh run, extended depth (was a query miss, now indexed)
run_ep "All-In Four CEOs Future AI CoreWeave Perplexity Mistral" extended

# 5. Hunt Backcountry MM 302 — re-run now that MM episodes use Q&A format
clear_cache "Hunt Backcountry MM 302 Bears Bivvies"
run_ep "Hunt Backcountry MM 302 Bears Bivvies"

# 6. All-In Psychedelics Bryan Johnson — query miss in backlog4 (now indexed)
run_ep "All-In Psychedelics Key Living Forever Bryan Johnson"

echo "=== BACKLOG4 CLEANUP DONE $(date -u) ==="
