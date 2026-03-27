---
name: gotchas-podcast-vault-race-condition
description: Use when manually importing episodes into episodes.json or wondering why recently added episodes disappeared from the vault
user-invocable: false
---

# Gotcha: Vault Race Condition Clobbers Manual Episode Imports

**Trigger**: vault import, episodes.json, missing episodes, episode disappeared, manual import
**Confidence**: high
**Created**: 2026-03-27
**Updated**: 2026-03-27
**Version**: 1

## Symptom

You manually import episodes into `episodes.json` (e.g. via `rss_poller.poll()` + vault write), confirm they're there, then run `on_demand.run()` or a backlog script — and the episodes vanish. Subsequent queries return "No episode found matching."

## Root Cause

`on_demand.run()` does a **read-modify-write** cycle on `episodes.json`: it loads the full vault, updates the episode record (adds `summary`, `source_quality`, etc.), then writes the whole file back. If a backlog script runs multiple episodes concurrently (or sequentially while your import is in flight), the second write overwrites the first — including any episodes you added manually.

The vault has no locking mechanism. Any concurrent write wins.

## Solution

**Always import AFTER all backlog scripts finish.**

```bash
# Wait for backlog to complete, then import
docker exec sample-agent_secure python3 -c "
import sys, json, os
sys.path.insert(0, '/home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts')
import rss_poller, vault

feeds_path = vault.get_vault_path('feeds.json')
feeds_data = json.loads(open(feeds_path).read())
ep_path = vault.get_vault_path('episodes.json')
ep_data = json.loads(open(ep_path).read())
existing_ids = {ep['id'] for ep in ep_data.get('episodes', [])}

# Use a temporary cutoff to get recent episodes the watcher missed
TARGET_FEED_IDS = ['all-in-with-chamath-jason-sacks-friedberg']  # adjust as needed
for f in feeds_data['feeds']:
    if f['id'] in TARGET_FEED_IDS:
        f_copy = {**f, 'last_episode_pub_date': '2026-03-22T00:00:00+00:00'}
        for ep in rss_poller.poll(f_copy):
            if ep['id'] not in existing_ids:
                ep_data['episodes'].insert(0, ep)
                existing_ids.add(ep['id'])

tmp = str(ep_path) + '.tmp'
with open(tmp, 'w') as fh:
    json.dump(ep_data, fh, indent=2)
os.replace(tmp, str(ep_path))
"
```

## Prevention

- Check `docker exec sample-agent_secure ps aux | grep on_demand` before importing
- Watch `/tmp/backlog*.log` and wait for the `=== DONE ===` line
- If you must import mid-run, verify episodes are still present after the script finishes and re-import if needed
- Long-term fix: add file locking (`fcntl.flock`) to `vault.save_vault()`
