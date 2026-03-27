#!/bin/bash
# Backlog4 — 12 episodes from Mar 23-26, 2026.
# All via cloud Whisper. Special handling:
#   - Hunt Backcountry #573 (Galpin): deep_science style override
#   - All-In "Four CEOs" and TRIGGERnometry "Robert Pape": extended depth
#   - Tim Ferriss #859: Q&A title — qa_suffix auto-triggers
#   - FoundMyFitness Aliquot "Arthur Brooks": topic_map_section test
AGENT="sample-agent"
SCRIPTS="/home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts"
export PYTHONPATH="$SCRIPTS"

run_ep() {
    local query="$1"
    local depth="${2:-standard}"
    local style_override="${3:-}"
    echo "$(date -u '+%Y-%m-%dT%H:%M:%S') [backlog4] START: $query (depth=$depth, style=$style_override)"
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
print(f'[backlog4] {status}: {title} (sq={sq}, cached={cached})')
" 2>&1
    echo "$(date -u '+%Y-%m-%dT%H:%M:%S') [backlog4] DONE: $query"
    echo "---"
}

echo "=== BACKLOG4 START $(date -u) ==="

# 1. Hunt Backcountry #573 — Andy Galpin performance/science guest
#    Force deep_science style so it gets protocol/supplement treatment
run_ep "Hunt Backcountry #573 Everyday Habits Hunting Performance" "standard" "deep_science"

# 2. ElkShape S9 E465 — How to Hunt Hard Without Losing Your Marriage
run_ep "ElkShape How Hunt Hard Without Losing Marriage"

# 3. FoundMyFitness Aliquot — How To Build Lasting Happiness | Dr. Arthur Brooks
#    Key test for topic_map_section (three macronutrients, four idols, five-step protocol)
run_ep "Aliquot Arthur Brooks Lasting Happiness"

# 4. Rokcast — Backcountry Mule Deer Hunting with Braxton Hamilton
run_ep "Rokcast Backcountry Mule Deer Braxton"

# 5. All-In — Four CEOs on the Future of AI: CoreWeave, Perplexity, Mistral...
#    Extended — substantive AI industry discussion
run_ep "All-In Four CEOs Future AI CoreWeave Perplexity" "extended"

# 6. Hunt Backcountry MM 302 — Bears, Bivvies, Practice Questions & More
run_ep "Hunt Backcountry MM 302 Bears Bivvies"

# 7. Tim Ferriss #859 — Q&A with Tim: The Upcoming AI Tsunami and B...
#    qa_suffix will auto-trigger from "Q&A" in title
run_ep "Tim Ferriss #859 Q&A AI Tsunami"

# 8. Hornady Podcast Ep. 228 — Rifle Builds | Joe's Ultralight 6.5 PRC
run_ep "Hornady Podcast 228 Rifle Builds Ultralight 6.5 PRC"

# 9. MeatEater Ep. 853 — Turkeys Break the Internet, Tungsten Ammo...
run_ep "MeatEater #853 Turkeys Internet Tungsten Ammo"

# 10. Huberman Lab Essentials — Using Salt to Optimize Mental & Physical...
run_ep "Huberman Lab Essentials Salt Mental Physical"

# 11. All-In — Are Psychedelics the Key to Living Forever? (ft. Bryan Johnson)
run_ep "All-In Psychedelics Living Forever Bryan Johnson"

# 12. TRIGGERnometry — "This War Will FAIL" - Military Expert Prof Robert Pape
#     Extended — substantive geopolitical analysis
run_ep "TRIGGERnometry Robert Pape War FAIL" "extended"

echo "=== BACKLOG4 COMPLETE $(date -u) ==="
