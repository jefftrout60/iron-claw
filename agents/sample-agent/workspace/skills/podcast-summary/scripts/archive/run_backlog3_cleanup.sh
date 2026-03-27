#!/bin/bash
# Cleanup run after backlog3:
# 1. Re-run Rokcast Hunting Gear Expo with Rokcast instruction active (clear cached extended summary first)
# 2. Better queries for ElkShape E461 and Hunt Backcountry MM 299
# 3. Re-run Mindful Hunter EP 291 (failed due to disk space)
AGENT="sample-agent"
SCRIPTS="/home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts"
export PYTHONPATH="$SCRIPTS"

run_ep() {
    local query="$1"
    local depth="${2:-standard}"
    local save_health="${3:-false}"
    echo "$(date -u '+%Y-%m-%dT%H:%M:%S') [cleanup] START: $query (depth=$depth)"
    BACKLOG_QUERY="$query" BACKLOG_DEPTH="$depth" BACKLOG_HEALTH="$save_health" python3 -c "
import os, on_demand
q = os.environ['BACKLOG_QUERY']
d = os.environ['BACKLOG_DEPTH']
sh = os.environ.get('BACKLOG_HEALTH', 'false') == 'true'
result = on_demand.run(
    q, agent_name='$AGENT', depth=d,
    strategy_override=['fetch_openai_whisper', 'show_notes'],
    save_to_health=sh,
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

echo "=== CLEANUP START $(date -u) ==="

# Step 1: Clear cached extended summary for Rokcast Hunting Gear Expo
# so it re-summarizes with the new Rokcast gear instruction.
echo "Clearing cached extended summary for Rokcast Hunting Gear Expo..."
python3 -c "
import sys
sys.path.insert(0, '$SCRIPTS')
import vault
ep_path = vault.get_vault_path('episodes.json')
ep_data = vault.load_vault(ep_path)
cleared = 0
for ep in ep_data['episodes']:
    title = ep.get('title', '').lower()
    if 'western hunting expo' in title and 'gear' in title:
        ep.pop('summary_extended', None)
        ep.pop('summary', None)
        cleared += 1
        print(f'Cleared: {ep[\"title\"]}')
if cleared:
    vault.save_vault(ep_path, ep_data)
    print(f'Cleared {cleared} episode(s)')
else:
    print('No matching episode found in vault (will re-transcribe from scratch)')
" 2>&1
echo "---"

# Step 2: Rokcast Hunting Gear Expo — extended + Rokcast gear instruction active
run_ep "Rokcast Hunting Gear Western Hunting Expo" "extended"

# Step 3: ElkShape E461 — better query (fewer words, no "management" ambiguity)
run_ep "ElkShape Pro Wolf Both"

# Step 4: Hunt Backcountry MM 299 — use episode number to match title
run_ep "Hunt Backcountry 299 This"

# Step 5: Mindful Hunter EP 291 — retry after disk space freed
run_ep "Mindful Hunter Haters Hangar Houses 70-Inch Bulls"

# Step 6: Dale Stark A-10 Warthog — clear show_notes cache and re-run via cloud Whisper
echo "Clearing cached summary for Dale Stark A-10 Warthog..."
python3 -c "
import sys
sys.path.insert(0, '$SCRIPTS')
import vault
ep_path = vault.get_vault_path('episodes.json')
ep_data = vault.load_vault(ep_path)
cleared = 0
for ep in ep_data['episodes']:
    title = ep.get('title', '').lower()
    if 'dale stark' in title or 'a-10 warthog' in title:
        ep.pop('summary', None)
        ep.pop('summary_extended', None)
        cleared += 1
        print(f'Cleared: {ep[\"title\"]}')
if cleared:
    vault.save_vault(ep_path, ep_data)
    print(f'Cleared {cleared} episode(s)')
else:
    print('No matching episode found in vault')
" 2>&1
echo "---"

run_ep "Shawn Ryan #142 Dale Stark A-10 Warthog"

echo "=== CLEANUP COMPLETE $(date -u) ==="
