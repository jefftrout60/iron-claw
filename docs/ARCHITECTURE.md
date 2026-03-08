# Architecture

Design choices that matter when contributing or debugging. For full technical detail see [IronClaw-TheoryOfOperation.md](../IronClaw-TheoryOfOperation.md) in the repo root.

## Config vs runtime

- **Host** holds the source of truth: `config/`, `workspace/`, `.env`. You only edit there.
- The container **never** mounts or writes to `config/`. It mounts **config-runtime**, which is a copy/sync of `config/` produced by `compose-up.sh` on every start.
- Exclusions in the sync keep sessions, memory DB, and other container-written state so they persist across restarts. Everything else is refreshed from `config/`, so a bad run cannot corrupt the canonical config.

## Exec: gateway vs sandbox

OpenClaw’s **exec** tool can run commands in:

- **gateway** — Same process as the gateway (same container). No extra isolation.
- **sandbox** — OpenClaw spawns a **separate Docker container** to run the command. Requires Docker inside the environment where the gateway runs.

Our agent containers do **not** have Docker installed and do not mount the host Docker socket. So if we set `tools.exec.host: "sandbox"`, OpenClaw would try to run `docker` and fail (e.g. `spawn docker ENOENT`). For agents that run in this environment (e.g. on a Raspberry Pi), we set **EXEC_HOST=gateway** in `agent.conf`. Then `compose-up.sh` injects `tools.exec.host: "gateway"` and `agents.defaults.sandbox.mode: "off"` into config-runtime on every start. Exec runs in the same container as the gateway; the **container** is the isolation boundary.

For agents where the host might have Docker available for future use, the script injects `host: "sandbox"`; in the current layout they still run inside our container, so they would hit the same “no Docker” limit unless we expose Docker. The source of truth is `agent.conf` (e.g. `EXEC_HOST=gateway`); the script applies it every run so config-runtime cannot drift.

## Workspace path rule

Any file the agent creates (e.g. via **write**) and then passes to **exec**, or any path one exec produces and a later exec must read, must live **under the workspace** (the mounted `workspace/` directory). Both the write tool and exec see the same mount, so the path is valid for both. Do not use `/tmp` for cross-tool files; skills are written to assume workspace paths (e.g. send-email body file, image-gen output). This is documented in agent guidelines (AGENTS.md) as Rule 6b.

## Credentials

Secrets live in the agent’s `.env` on the host. The container gets them via Docker’s `env_file` and `environment`. When exec runs in gateway mode, it normally inherits that environment. The send-email skill tries env vars first (`SMTP_FROM_EMAIL`, `GMAIL_APP_PASSWORD`); if either is missing (e.g. in some exec code paths), it falls back to `workspace/skills/send-email/.env` if that file exists. You can create that file manually from your agent `.env` if needed; it is gitignored.

## Self-healing

We do not rely on the container or OpenClaw to “remember” the right exec host or sandbox mode. On **every** `compose-up`, the script injects into config-runtime:

- The agent’s port (from `agent.conf`).
- For agents with **EXEC_HOST=gateway**: `sandbox.mode: "off"` and `tools.exec.host: "gateway"` (and `security: "full"`, `ask: "off"`).
- For other agents: `tools.exec.host: "sandbox"` (and the same security/ask settings).

So even if config-runtime were edited or OpenClaw wrote something back, the next compose-up restores the intended state. The host and the scripts own the policy.

## Security (container)

Same for every agent: read-only root filesystem (writable only on mounted volumes), all capabilities dropped, no-new-privileges, non-root (UID 1000), port bound inside the container with host mapping (e.g. `127.0.0.1:${AGENT_PORT}`). Init (tini) reaps zombies. Resource limits come from `agent.conf`.

## Protected settings

Do not remove or change these; they are load-bearing:

- **gateway.bind: "lan"** — Required so the gateway listens on a non-loopback address inside the container; otherwise Docker port mapping cannot deliver traffic.
- **gateway.mode: "local"** — OpenClaw requires this to start.
- **controlUi** when bind is lan — OpenClaw may require an allowed origin or `dangerouslyAllowHostHeaderOriginFallback` for non-loopback; see Raspberry Pi runbook.

See [CLAUDE.md](../CLAUDE.md) in the repo root for the full protected-settings list and rationale.
