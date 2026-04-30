#!/usr/bin/env python3
"""
One-time migration: reads Withings + Oura secrets from .env,
writes them to macOS Keychain, then removes them from .env.

Run once from repo root:
  python3 scripts/migrate-secrets-to-keychain.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from keychain import kc_set

_ENV_PATH = Path(__file__).parent.parent / "agents/sample-agent/.env"

# Keys to migrate: (env_var_name, keychain_service, keychain_account)
MIGRATIONS = [
    ("WITHINGS_CLIENT_ID",      "com.ironclaw.withings", "client_id"),
    ("WITHINGS_CLIENT_SECRET",  "com.ironclaw.withings", "client_secret"),
    ("WITHINGS_ACCESS_TOKEN",   "com.ironclaw.withings", "access_token"),
    ("WITHINGS_REFRESH_TOKEN",  "com.ironclaw.withings", "refresh_token"),
    ("WITHINGS_TOKEN_EXPIRY",   "com.ironclaw.withings", "token_expiry"),
    ("OURA_PERSONAL_ACCESS_TOKEN", "com.ironclaw.oura", "access_token"),
]

def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, val = s.partition("=")
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env

def rewrite_env_without(path: Path, remove_keys: set[str]) -> None:
    lines = path.read_text().splitlines()
    new_lines = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key = s.partition("=")[0].strip()
            if key in remove_keys:
                continue
        new_lines.append(line)
    path.write_text("\n".join(new_lines) + "\n")

def main() -> None:
    env = load_env(_ENV_PATH)
    migrated = []
    missing = []

    for env_key, service, account in MIGRATIONS:
        val = env.get(env_key, "")
        if not val:
            missing.append(env_key)
            continue
        kc_set(service, account, val)
        migrated.append(env_key)
        print(f"  ✓ {env_key} → Keychain {service}/{account}")

    if missing:
        print(f"\nSkipped (not in .env): {', '.join(missing)}")

    remove_keys = {m[0] for m in MIGRATIONS if m[0] in migrated}
    rewrite_env_without(_ENV_PATH, remove_keys)
    print(f"\nRemoved {len(remove_keys)} key(s) from .env")
    print("Migration complete. Verify with:")
    print("  security find-generic-password -s com.ironclaw.withings -a access_token -w")
    print("  security find-generic-password -s com.ironclaw.oura -a access_token -w")

if __name__ == "__main__":
    main()
