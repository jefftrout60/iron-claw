---
name: gotchas-compose-up-cooldown
description: Use when a config change to openclaw.json isn't taking effect after compose-up.sh, a skill isn't triggering after being enabled, or the agent is ignoring a config update
user-invocable: false
---

# Gotcha: compose-up.sh Cooldown Skips Config Sync

**Trigger**: compose-up.sh, config not applied, skill not loading, openclaw.json change ignored, docker restart, cooldown, config-runtime stale, skill not triggering, enabled skill not firing
**Confidence**: high
**Created**: 2026-04-27
**Updated**: 2026-04-27
**Version**: 1

## Symptom

You add or change something in `agents/{name}/config/openclaw.json` (new skill entry, config flag, tool toggle), run `./scripts/compose-up.sh {name} -d`, and the change has no effect. The agent behaves as if the config was never updated. Skills don't fire, settings aren't applied.

## Root Cause

`compose-up.sh` has a cooldown guard — if the container was started recently it prints `Container started Xs ago — skipping restart (cooldown)` and does NOT restart the container. The sync from `config/` → `config-runtime/` still happens, but the running process never reloads the updated config because the container wasn't restarted.

The output looks successful:
```
[sample-agent] Starting compose (container: sample-agent_secure, port: 18792)...
 Container sample-agent_secure Running
```
"Running" just means the container is up — not that it restarted with new config.

## Solution

Force a restart directly, bypassing the cooldown:

```bash
docker restart sample-agent_secure
```

Then verify the gateway came back up:

```bash
./scripts/test-gateway-http.sh sample-agent
```

If you also need to verify the specific config change landed in config-runtime:

```bash
python3 -c "import json; cfg=json.load(open('agents/sample-agent/config-runtime/openclaw.json')); print(json.dumps(cfg.get('skills', {}), indent=2))"
```

## Prevention

After any `config/openclaw.json` change, check the compose-up output for the `skipping restart (cooldown)` line. If you see it, follow with `docker restart {container_name}` immediately. The container name is in `agents/{name}/agent.conf` as `AGENT_CONTAINER`.

Never assume a new skill is active until verified: ask the agent a question that should trigger it, or check `config-runtime/openclaw.json` directly.
