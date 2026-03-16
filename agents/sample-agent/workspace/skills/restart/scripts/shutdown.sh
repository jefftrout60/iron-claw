#!/usr/bin/env bash
# Graceful shutdown — sends reply first, then kills container after short delay.
# Runs inside the container (EXEC_HOST=gateway), so kill 1 stops tini/the agent.

# Schedule the kill in background — gives OpenClaw time to send the reply
nohup bash -c 'sleep 4 && kill 1' >/dev/null 2>&1 &

echo "Shutdown scheduled in 4 seconds."
