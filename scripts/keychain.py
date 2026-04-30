#!/usr/bin/env python3
"""
macOS Keychain helpers for ironclaw secrets.

Uses the `security` CLI so no third-party dependencies are needed.
All ironclaw secrets use service names prefixed with `com.ironclaw.`.

Usage:
    from keychain import kc_get, kc_set, kc_delete

    kc_set("com.ironclaw.withings", "access_token", "abc123")
    token = kc_get("com.ironclaw.withings", "access_token")
    kc_delete("com.ironclaw.withings", "access_token")
"""

from __future__ import annotations

import subprocess
import sys


def kc_get(service: str, account: str) -> str:
    """Read a secret from Keychain. Returns empty string if not found."""
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def kc_set(service: str, account: str, value: str) -> None:
    """Write a secret to Keychain, replacing any existing value."""
    # Delete silently first — `security` has no upsert
    subprocess.run(
        ["security", "delete-generic-password", "-s", service, "-a", account],
        capture_output=True,
    )
    subprocess.run(
        ["security", "add-generic-password", "-s", service, "-a", account, "-w", value],
        check=True,
        capture_output=True,
    )


def kc_delete(service: str, account: str) -> None:
    """Remove a secret from Keychain (silent if not found)."""
    subprocess.run(
        ["security", "delete-generic-password", "-s", service, "-a", account],
        capture_output=True,
    )


def kc_require(service: str, account: str, setup_hint: str = "") -> str:
    """Read a secret; exit with a helpful message if missing."""
    val = kc_get(service, account)
    if not val:
        hint = f" — {setup_hint}" if setup_hint else ""
        print(f"Error: Keychain missing {service}/{account}{hint}", file=sys.stderr)
        sys.exit(1)
    return val
