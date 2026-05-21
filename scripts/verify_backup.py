#!/usr/bin/env python3
"""
Phase A29 — Verify a backup from the CLI.

Usage:
  python scripts/verify_backup.py <backup_id> [--json]

Exit codes:
  0  All checks passed (backup VERIFIED)
  1  One or more checks failed or backup not found
"""
import argparse
import json
import os
import sys

BOT_DIR = os.path.join(os.path.dirname(__file__), "..", "bot")
sys.path.insert(0, os.path.abspath(BOT_DIR))

from backup_manager import verify_backup

ANSI_GREEN  = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED    = "\033[31m"
ANSI_BOLD   = "\033[1m"
ANSI_RESET  = "\033[0m"


def _color(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{code}{text}{ANSI_RESET}"


def main() -> int:
    parser = argparse.ArgumentParser(description="investing-agent backup verifier")
    parser.add_argument("backup_id", help="Backup ID to verify (e.g. bk_a1b2c3d4e5f6)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON and exit")
    args = parser.parse_args()

    result = verify_backup(args.backup_id)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    ok = result.get("ok", False)
    overall_color = ANSI_GREEN if ok else ANSI_RED
    overall_icon  = "✓" if ok else "✗"

    print()
    print(_color(f"{overall_icon} Verification {'PASSED' if ok else 'FAILED'}", overall_color))

    if "error" in result:
        print(f"  Error: {result['error']}")

    for check in result.get("checks", []):
        passed = check.get("passed", False)
        icon   = "✓" if passed else "✗"
        color  = ANSI_GREEN if passed else ANSI_RED
        print(f"  {_color(icon, color)} {check.get('name')}: {check.get('detail')}")

    entry = result.get("manifest_entry", {})
    if entry:
        print()
        print(f"  Backup ID:  {entry.get('backup_id')}")
        print(f"  Type:       {entry.get('backup_type')}")
        print(f"  Status:     {entry.get('status')}")
        print(f"  Created at: {entry.get('created_at')}")
    print()

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
