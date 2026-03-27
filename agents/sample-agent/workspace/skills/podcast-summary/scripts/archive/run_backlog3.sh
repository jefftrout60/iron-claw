#!/bin/bash
# Process 46-episode backlog via OpenAI cloud Whisper (fetch_openai_whisper).
# All episodes use strategy_override to bypass local whisper.cpp.
# Special episodes: extended depth and/or health_knowledge.json save.
AGENT="sample-agent"
SCRIPTS="/home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts"
export PYTHONPATH="$SCRIPTS"

run_ep() {
    local query="$1"
    local depth="${2:-standard}"
    local save_health="${3:-false}"
    echo "$(date -u '+%Y-%m-%dT%H:%M:%S') [backlog3] START: $query (depth=$depth, health=$save_health)"
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
print(f'[backlog3] {status}: {title} (sq={sq}, cached={cached})')
" 2>&1
    echo "$(date -u '+%Y-%m-%dT%H:%M:%S') [backlog3] DONE: $query"
    echo "---"
}

echo "=== BACKLOG3 START $(date -u) ==="

# 1. Shawn Ryan Show #290 - Zach Lahn - Cancer-Causing
run_ep "Shawn Ryan #290 Zach Lahn Cancer"

# 2. VOM Radio - Central Asia: Visions, Visas, and Venturing
run_ep "VOMRadio Central Asia Visions Visas Venturing"

# 3. Modern Day Sniper #132 - Francis Colon - The Game Within
run_ep "Modern Day Sniper #132 Francis Colon Game Within"

# 4. Hunt Backcountry MM 301 - Elk Advice, Optics, Hunting Bans
run_ep "Hunt Backcountry Elk Advice Optics Hunting Bans"

# 5. Shawn Ryan Show #288 - Shyam Sankar - Sleepwalking Into World War
run_ep "Shawn Ryan #288 Shyam Sankar Sleepwalking"

# 6. Modern Day Sniper #131 - Meet The Mentors
run_ep "Modern Day Sniper #131 Meet Mentors"

# 7. VOM Radio - MISSIONS: Reducing Barriers, Easing Burdens
run_ep "VOMRadio Missions Reducing Barriers Easing Burdens"

# 8. Barbell Shrugged #839 - Performance Brain Health Part 2 [extended + health]
run_ep "Barbell Shrugged #839 Performance Brain Health" "extended" "true"

# 9. Rokcast - James Yates: Leveraging Data for Archery [extended]
run_ep "Rokcast James Yates Leveraging Data Archery" "extended"

# 10. Hunting Dog Podcast - Training Dogs Without Birds - Charlie Thon
run_ep "Hunting Dog Training Dogs Without Birds"

# 11. VOM Radio - India: I Am Willing to Pay the Price
run_ep "VOMRadio India Willing Pay Price"

# 12. Modern Day Sniper #130 - Vincent Peak
run_ep "Modern Day Sniper #130 Vincent Peak"

# 13. Western Hunter #48 - NRL Road Hunters
run_ep "Western Hunter #48 NRL Road Hunters"

# 14. Hornady Podcast Ep 225 - Science of Copper Bullets
run_ep "Hornady Podcast Copper Bullets Science"

# 15. Valley to Peak Nutrition #124 - Hunt Expo + Toughbuck/Toughsheep
run_ep "Valley to Peak Nutrition #124 Hunt Expo Toughbuck"

# 16. ElkShape S9 E462 - Only LOSERS bowhunt elk
run_ep "ElkShape bowhunt elk LOSERS"

# 17. Hunt Backcountry #570 - Sheep Hunt Challenge + Q&A
run_ep "Hunt Backcountry #570 Sheep Hunt Challenge"

# 18. VOM Radio - Bible Access: More Persecuted Christians
run_ep "VOMRadio Bible Access Persecuted Christians"

# 19. Rokcast - Banning Hunting Technology in Idaho
run_ep "Rokcast Banning Hunting Technology Idaho"

# 20. Valley to Peak Nutrition #123 - Sayonara bars; meet pouches
run_ep "Valley to Peak Nutrition #123 bars pouches"

# 21. Hunt Backcountry #569 - ER Visits, Expo Season
run_ep "Hunt Backcountry #569 ER Visits Expo Season"

# 22. ElkShape S9 E461 - Pro Wolf, Pro Wolf Management...Both?
run_ep "ElkShape Pro Wolf Management"

# 23. Barbell Shrugged #837 - Fat Free Mass Index [extended + health]
run_ep "Barbell Shrugged #837 Fat Free Mass Index" "extended" "true"

# 24. Rokcast - Hunting Gear: Insights from Western Hunting Expo
run_ep "Rokcast Hunting Gear Western Hunting Expo Insights"

# 25. The American West - Ep 22: New West, Modern West, Public Lands
run_ep "American West New West Modern West Public Lands"

# 26. Hunt Backcountry MM 299 - This AND That
run_ep "Hunt Backcountry This That Monday Minisode"

# 27. VOM Radio - India: God Answers Prayer
run_ep "VOMRadio India God Answers Prayer"

# 28. Beyond the Kill EP 604 - Passion, Precision, Maximizing Potential
run_ep "Beyond the Kill Passion Precision Maximizing Potential"

# 29. Mindful Hunter EP 291 - Haters, Hangar Houses & 70-Inch Bulls
run_ep "Mindful Hunter Haters Hangar Houses 70-Inch Bulls"

# 30. Shawn Ryan Show #281 - Jeremy Slate - Fatal Decisions
run_ep "Shawn Ryan #281 Jeremy Slate Fatal Decisions"

# 31. Hunting Dog Podcast - Gear, trips and dogs for next season
run_ep "Hunting Dog Podcast Gear trips dogs season"

# 32. Hunt Backcountry MM 298 - Hunt Expo Recap, Military Packs
run_ep "Hunt Backcountry Hunt Expo Recap Military Packs"

# 33. Rokcast - Tenacity Firearms with Andrew Whitney
run_ep "Rokcast Tenacity Firearms Andrew Whitney"

# 34. Shawn Ryan Show #280 - Sarah Adams - If China Isn't #1 Threat
run_ep "Shawn Ryan #280 Sarah Adams China Threat"

# 35. TRIGGERnometry - You've Been Lied To About Masculinity - Scott Galloway
run_ep "TRIGGERnometry Scott Galloway Masculinity Lied"

# 36. Western Hunter #46 - High-Tech Rednecks
run_ep "Western Hunter #46 High-Tech Rednecks"

# 37. VOM Radio - All Missions Begins With Prayer
run_ep "VOMRadio All Missions Prayer"

# 38. TRIGGERnometry - Best Conversation About News, Opinion & Censorship
run_ep "TRIGGERnometry News Opinion Censorship Conversation"

# 39. VOM Radio - Imprisoned in Sudan: Privilege to Be Persecuted
run_ep "VOMRadio Sudan Imprisoned Privilege Persecuted"

# 40. Better Brain Fitness #89 - Neutralize Impact of Hearing Loss [extended + health]
run_ep "Better Brain Fitness #89 Hearing Loss" "extended" "true"

# 41. Rokcast TT#68 - Dry-Aging Wild Game - Chef John McGannon
run_ep "Rokcast Dry-Aging Wild Game McGannon"

# 42. Invest Like the Best #444 - Dan Wang - US vs China in 21st Century
run_ep "Invest Like the Best #444 Dan Wang China"

# 43. Hunt Backcountry #538 - Meat Science Deep-Dive + Q&A [extended, enumerate Q&A]
run_ep "Hunt Backcountry #538 Meat Science" "extended"

# 44. Hunting Dog Podcast - Everything about ticks
run_ep "Hunting Dog Podcast ticks"

# 45. Hunt Backcountry #508 - Meat Scientist on Wild Game Care
run_ep "Hunt Backcountry #508 Meat Scientist Wild Game Care"

# 46. Shawn Ryan Show #142 - Dale Stark - A-10 Warthog (~5 hours)
run_ep "Shawn Ryan #142 Dale Stark A-10 Warthog"

echo "=== BACKLOG3 COMPLETE $(date -u) ==="
