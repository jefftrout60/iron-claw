#!/usr/bin/env bash
# rotate-container-secrets.sh — Interactively rotate OPENAI_API_KEY, TELEGRAM_BOT_TOKEN,
# and GMAIL_APP_PASSWORD in agents/sample-agent/.env.
#
# Usage: ./scripts/rotate-container-secrets.sh
#   (must be run from the repo root)
#
# Behavior:
#   1. Verify agents/sample-agent/.env exists
#   2. Prompt for each new value (masked); Enter without typing skips that key
#   3. Stop the container before writing any changes
#   4. Update .env in-place with Python regex (consistent across macOS/Linux)
#   5. Restart the container
#   6. Print confirmation showing only the last 4 chars of each updated key

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/agents/sample-agent/.env"
AGENT_NAME="sample-agent"

# ── Preflight ────────────────────────────────────────────────────────────────

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: $ENV_FILE not found." >&2
  echo "  Create it from agents/sample-agent/.env.example and fill in real values." >&2
  exit 1
fi

echo "Rotating container secrets for agent: $AGENT_NAME"
echo "  .env: $ENV_FILE"
echo ""

# ── Collect new values (masked prompts) ──────────────────────────────────────

declare -A NEW_VALS
declare -a KEY_ORDER=(OPENAI_API_KEY TELEGRAM_BOT_TOKEN GMAIL_APP_PASSWORD)

for key in "${KEY_ORDER[@]}"; do
  read -rsp "  New value for $key (Enter to skip): " val
  echo ""
  NEW_VALS["$key"]="$val"
done

# Check if anything was actually provided
any_provided=false
for key in "${KEY_ORDER[@]}"; do
  [[ -n "${NEW_VALS[$key]}" ]] && any_provided=true
done

if [[ "$any_provided" == false ]]; then
  echo "No keys provided — nothing to do."
  exit 0
fi

# ── Stop container before touching .env ──────────────────────────────────────

echo ""
echo "Stopping container $AGENT_NAME..."
docker compose -p "$AGENT_NAME" down

# ── Apply updates to .env via Python regex ────────────────────────────────────

echo "Updating $ENV_FILE..."

for key in "${KEY_ORDER[@]}"; do
  val="${NEW_VALS[$key]}"
  [[ -z "$val" ]] && continue  # skip if user pressed Enter without typing

  python3 - "$ENV_FILE" "$key" "$val" <<'PYEOF'
import re, sys, os

env_path, key, new_val = sys.argv[1], sys.argv[2], sys.argv[3]

with open(env_path, 'r') as f:
    content = f.read()

# Match KEY=<anything up to end of line>, including optional leading #
pattern = r'^(' + re.escape(key) + r')=.*$'
updated, count = re.subn(pattern, lambda m: m.group(1) + '=' + new_val, content, flags=re.MULTILINE)

if count == 0:
    # Key not present — append it
    updated = content.rstrip('\n') + '\n' + key + '=' + new_val + '\n'

tmp = env_path + '.tmp'
with open(tmp, 'w') as f:
    f.write(updated)
os.replace(tmp, env_path)
PYEOF

done

# ── Restart container ─────────────────────────────────────────────────────────

echo "Restarting container $AGENT_NAME..."
"$SCRIPT_DIR/compose-up.sh" "$AGENT_NAME" -d

# ── Confirmation — last 4 chars only, never full values ───────────────────────

echo ""
echo "Rotation complete:"
for key in "${KEY_ORDER[@]}"; do
  val="${NEW_VALS[$key]}"
  if [[ -z "$val" ]]; then
    echo "  $key: skipped (unchanged)"
  else
    suffix="${val: -4}"
    echo "  $key updated: ...${suffix}"
  fi
done
