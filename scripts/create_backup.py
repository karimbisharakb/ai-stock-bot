#!/usr/bin/env python3
"""
Phase A29 — Create a backup from the CLI.

Usage:
  python scripts/create_backup.py [--type FULL] [--notes "..."] [--json]

Exit codes:
  0  CREATED (backup file written successfully)
  1  FAILED  (storage error or exception)
"""
import argparse
import json
import os
import sys

BOT_DIR = os.path.join(os.path.dirname(__file__), "..", "bot")
sys.path.insert(0, os.path.abspath(BOT_DIR))

from backup_manager import create_backup, BACKUP_TYPES

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
    parser = argparse.ArgumentParser(description="investing-agent backup creator")
    parser.add_argument(
        "--type", dest="backup_type", default="FULL",
        choices=list(BACKUP_TYPES),
        help=f"Backup type (default: FULL). Options: {', '.join(BACKUP_TYPES)}",
    )
    parser.add_argument("--notes", default=None, help="Optional annotation for this backup")
    parser.add_argument("--json", action="store_true", help="Output raw JSON and exit")
    args = parser.parse_args()

    entry = create_backup(backup_type=args.backup_type, notes=args.notes)

    if args.json:
        print(json.dumps(entry, indent=2))
        return 0 if entry.get("status") == "CREATED" else 1

    ok = entry.get("status") == "CREATED"
    color = ANSI_GREEN if ok else ANSI_RED
    icon  = "✓" if ok else "✗"

    print()
    print(_color(f"{icon} Backup {entry.get('status')}", color))
    print(f"  ID:         {entry.get('backup_id')}")
    print(f"  Type:       {entry.get('backup_type')}")
    print(f"  Tables:     {entry.get('table_count')}")
    print(f"  Rows:       {entry.get('row_count')}")
    print(f"  Size:       {entry.get('size_bytes', 0):,} bytes")
    print(f"  Checksum:   {(entry.get('checksum_sha256') or '')[:16]}…")
    print(f"  File:       {entry.get('file_path')}")
    print(f"  Created at: {entry.get('created_at')}")
    if entry.get("notes"):
        print(f"  Notes:      {entry['notes']}")
    print()

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
