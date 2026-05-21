#!/usr/bin/env python3
"""
Phase A28 — Release check CLI.

Run from the project root:
  python scripts/release_check.py [--compact] [--json]

Exit codes:
  0  HEALTHY / WATCH
  1  DEGRADED / CRITICAL
"""
import argparse
import json
import os
import sys

# Add bot/ to path so all modules are importable
BOT_DIR = os.path.join(os.path.dirname(__file__), "..", "bot")
sys.path.insert(0, os.path.abspath(BOT_DIR))

from system_release_check import (
    run_release_check,
    get_route_list,
    get_flag_summary,
    CHECK_PASS,
    CHECK_WARN,
    CHECK_FAIL,
    STATUS_HEALTHY,
    STATUS_WATCH,
)

ANSI_GREEN  = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED    = "\033[31m"
ANSI_BOLD   = "\033[1m"
ANSI_RESET  = "\033[0m"


def _color(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{code}{text}{ANSI_RESET}"


def _status_color(status: str) -> str:
    if status in (CHECK_PASS, STATUS_HEALTHY, STATUS_WATCH):
        color = ANSI_GREEN if status in (CHECK_PASS, STATUS_HEALTHY) else ANSI_YELLOW
    else:
        color = ANSI_RED
    return _color(status, color)


def _print_section(title: str) -> None:
    print()
    print(_color(f"── {title} {'─' * max(0, 50 - len(title))}", ANSI_BOLD))


def main() -> int:
    parser = argparse.ArgumentParser(description="investing-agent release health check")
    parser.add_argument("--compact", action="store_true",
                        help="Skip brief generation (faster)")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON and exit")
    args = parser.parse_args()

    mode = "compact" if args.compact else "full"
    report = run_release_check(mode=mode)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report["overall_status"] in (STATUS_HEALTHY, STATUS_WATCH) else 1

    # ── Overall status ────────────────────────────────────────────────────────
    overall = report["overall_status"]
    print()
    print(
        _color("investing-agent release check", ANSI_BOLD)
        + f"  [{_status_color(overall)}]"
        + f"  generated {report['generated_at']}"
    )
    print(
        f"  {report['checks_passed']} passed  "
        f"{report['checks_warned']} warned  "
        f"{report['checks_failed']} failed  "
        f"(total {report['checks_total']})"
    )

    # ── Failures ──────────────────────────────────────────────────────────────
    if report["failures"]:
        _print_section("FAILURES")
        for f in report["failures"]:
            print(f"  {_color('✗', ANSI_RED)} {f['name']}: {f['detail']}")

    # ── Warnings ──────────────────────────────────────────────────────────────
    if report["warnings"]:
        _print_section("Warnings")
        for w in report["warnings"]:
            print(f"  {_color('!', ANSI_YELLOW)} {w['name']}: {w['detail']}")

    # ── Recommended fixes ─────────────────────────────────────────────────────
    if report["recommended_fixes"]:
        _print_section("Recommended fixes")
        for i, fix in enumerate(report["recommended_fixes"], 1):
            print(f"  {i}. {fix}")

    # ── Notification safety summary ───────────────────────────────────────────
    _print_section("Notification safety")
    ns_results = report.get("sections", {}).get("notification_safety", [])
    for r in ns_results:
        icon = "✓" if r["status"] == CHECK_PASS else ("!" if r["status"] == CHECK_WARN else "✗")
        color = ANSI_GREEN if r["status"] == CHECK_PASS else (ANSI_YELLOW if r["status"] == CHECK_WARN else ANSI_RED)
        print(f"  {_color(icon, color)} {r['name']}: {r['detail']}")

    # ── Route summary ─────────────────────────────────────────────────────────
    _print_section("Route availability")
    routes_data = get_route_list()
    print(f"  Registered API routes: {routes_data.get('count', '?')}")
    print(f"  Required routes: {routes_data.get('required_count', '?')}")
    route_check = report.get("sections", {}).get("routes", [])
    for r in route_check:
        icon = "✓" if r["status"] == CHECK_PASS else "✗"
        color = ANSI_GREEN if r["status"] == CHECK_PASS else ANSI_RED
        print(f"  {_color(icon, color)} {r['detail']}")

    # ── Flag summary ──────────────────────────────────────────────────────────
    _print_section("Feature flags")
    flags_data = get_flag_summary()
    for key, val in flags_data.get("flags", {}).items():
        if "error" in key:
            print(f"  {_color('!', ANSI_YELLOW)} {key}: {val}")
        else:
            # Warn on any send-enabling flag being True
            is_dangerous = (
                key in ("legacy_notifications_enabled", "unified_notifications_enabled",
                        "alpha_alerts_enabled", "eod_brief_enabled", "weekly_review_enabled")
                and val is True
            ) or (
                key == "alpha_notifications_enabled" and val is True
            ) or (
                key == "alpha_notifications_dry_run_only" and val is False
            )
            color = ANSI_YELLOW if is_dangerous else ANSI_GREEN
            print(f"  {_color(str(val), color):8s}  {key}")

    print()
    print(f"Overall: {_status_color(overall)}")
    print()

    return 0 if overall in (STATUS_HEALTHY, STATUS_WATCH) else 1


if __name__ == "__main__":
    sys.exit(main())
