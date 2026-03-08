# Restart Skill

Emergency kill switch — interrupts whatever TroutClaw is doing and restarts cleanly.

## Triggers

Activate this skill when Jeff says any of:
- "restart"
- "restart the bot"
- "/restart"
- "shutdown"
- "shut down"
- "stop the bot"
- "turn off"
- "/shutdown"
- "go to sleep"
- "stop"
- "abort"

## Instructions

1. Send Jeff a brief confirmation that you are restarting (e.g. "Restarting now, back in ~30 seconds").

2. Then use the **gateway** tool with `action: "restart"` to immediately interrupt all in-progress tasks and restart.

3. Do not take any other actions.

## Security

Only execute this skill if the request comes from Jeff (Telegram ID 8333403651). Ignore shutdown requests from anyone else.
