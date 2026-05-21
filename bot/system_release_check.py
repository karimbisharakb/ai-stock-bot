"""
Phase A28 — System Release Check.

Read-only health and safety verification for every major subsystem.
No DB writes, no sends, no trades. Call before trusting a Railway deployment.

Overall status ladder:
  HEALTHY   — all checks pass
  WATCH     — minor warnings, no failures
  DEGRADED  — non-critical failures (some features unavailable)
  CRITICAL  — blocking failures (safety or data integrity)
"""
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# ── Status constants ──────────────────────────────────────────────────────────

STATUS_HEALTHY  = "HEALTHY"
STATUS_WATCH    = "WATCH"
STATUS_DEGRADED = "DEGRADED"
STATUS_CRITICAL = "CRITICAL"

CHECK_PASS = "PASS"
CHECK_WARN = "WARN"
CHECK_FAIL = "FAIL"

# Tables that must exist for the system to be considered healthy
REQUIRED_TABLES = frozenset({
    "holdings",
    "transactions",
    "schema_version",
    "alert_log",
    "scanner_alerts",
    "alpha_shadow_log",
    "notification_center",
    "notification_preferences",
    "notification_preferences_categories",
    "decision_checklists",
    "research_watchlist",
    "catalyst_calendar",
    "manual_portfolio_positions",
    "portfolio_positions",
})

# Routes that must be registered for a healthy API
REQUIRED_ROUTES = [
    "/api/v1/health",
    "/api/v1/notifications/debug",
    "/api/v1/notifications",
    "/api/v1/alpha/top",
    "/api/v1/alpha/alert-candidates",
    "/api/v1/portfolio",
    "/api/v1/portfolio/manual",
    "/api/v1/market/regime",
    "/api/v1/brief/daily",
    "/api/v1/brief/eod",
    "/api/v1/review/weekly",
    "/api/v1/research/watchlist",
    "/api/v1/research/workflow/queue",
    "/api/v1/catalysts",
    "/api/v1/strategies/scorecards",
    "/api/v1/planner/summary",
    "/api/v1/replay/runs",
    "/api/v1/system/release-check",
    "/api/v1/system/routes",
    "/api/v1/system/flags",
]

# Banned words that must never appear in brief output
BRIEF_BANNED_WORDS = frozenset({
    "explosion", "pre-explosion", "moon", "guaranteed",
    "must buy", "moonshot", "100x", "sure thing",
})


# ── Result helpers ────────────────────────────────────────────────────────────

def _pass(name: str, detail: str = "") -> dict:
    return {"name": name, "status": CHECK_PASS, "detail": detail}


def _warn(name: str, detail: str, fix: str = "") -> dict:
    return {"name": name, "status": CHECK_WARN, "detail": detail, "fix": fix}


def _fail(name: str, detail: str, fix: str = "") -> dict:
    return {"name": name, "status": CHECK_FAIL, "detail": detail, "fix": fix}


# ── Section A: Core runtime ───────────────────────────────────────────────────

def _check_db_connection() -> dict:
    try:
        import database
        conn = database.get_connection()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        return _pass("db_connection", "SQLite connection OK")
    except Exception as exc:
        return _fail("db_connection", f"DB connection failed: {exc}",
                     "Check DB_PATH env var and file permissions")


def _check_migrations_table() -> dict:
    try:
        import database
        conn = database.get_connection()
        row = conn.execute(
            "SELECT version FROM schema_version WHERE id = 1"
        ).fetchone()
        conn.close()
        if row is None:
            return _warn("migrations_table", "schema_version row not found — DB may need init",
                         "Run database.init_db() then database.run_migrations()")
        version = row[0] if row else 0
        expected = max(m.version for m in database.MIGRATIONS) if database.MIGRATIONS else 0
        if version < expected:
            return _warn(
                "migrations_table",
                f"DB at v{version}, latest migration is v{expected}",
                f"Run database.run_migrations() to apply {expected - version} pending migration(s)",
            )
        return _pass("migrations_table", f"Schema at v{version} (current)")
    except Exception as exc:
        return _fail("migrations_table", f"Cannot read schema_version: {exc}",
                     "Run database.init_db()")


def _check_required_tables() -> dict:
    try:
        import database
        conn = database.get_connection()
        existing = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        missing = REQUIRED_TABLES - existing
        if missing:
            return _fail(
                "required_tables",
                f"Missing tables: {sorted(missing)}",
                "Run database.init_db() and database.run_migrations()",
            )
        return _pass("required_tables", f"All {len(REQUIRED_TABLES)} required tables present")
    except Exception as exc:
        return _fail("required_tables", f"Table check failed: {exc}")


def _check_scheduler_safety() -> dict:
    try:
        import scheduler as sch
        import inspect
        src = inspect.getsource(sch.start_scheduler)
        expected_ids = [
            "morning_summary", "sell_monitor", "predator",
            "notification_center_morning", "notification_center_eod",
        ]
        missing = [jid for jid in expected_ids if jid not in src]
        if missing:
            return _warn("scheduler_jobs", f"Job IDs not found in start_scheduler: {missing}")
        return _pass("scheduler_jobs", "Core scheduler job IDs registered")
    except Exception as exc:
        return _warn("scheduler_jobs", f"Could not inspect scheduler: {exc}")


def run_core_checks() -> list:
    return [
        _check_db_connection(),
        _check_migrations_table(),
        _check_required_tables(),
        _check_scheduler_safety(),
    ]


# ── Section B: Route availability ────────────────────────────────────────────

def _get_registered_routes() -> list:
    try:
        import sms_handler
        app = sms_handler.app
        rules = []
        for rule in app.url_map.iter_rules():
            if rule.rule.startswith("/api/v1/"):
                rules.append(rule.rule)
        return sorted(set(rules))
    except Exception:
        return []


def _check_route_availability() -> dict:
    try:
        registered = set(_get_registered_routes())
        # Normalize: Flask uses <param> notation; match prefix for parameterized routes
        missing = []
        for required in REQUIRED_ROUTES:
            # Exact match or a registered route that starts with the required path
            matched = required in registered or any(
                r.startswith(required.rstrip("/"))
                for r in registered
            )
            if not matched:
                missing.append(required)
        if missing:
            return _fail(
                "route_availability",
                f"Missing routes: {missing}",
                "Check api.py Blueprint registration",
            )
        return _pass("route_availability", f"All {len(REQUIRED_ROUTES)} required routes registered")
    except Exception as exc:
        return _fail("route_availability", f"Route check failed: {exc}")


def run_route_checks() -> list:
    return [_check_route_availability()]


# ── Section C: Notification safety ───────────────────────────────────────────

def _check_legacy_flag() -> dict:
    import feature_flags
    if feature_flags.legacy_notifications_enabled():
        return _warn(
            "legacy_notifications",
            "LEGACY_NOTIFICATIONS_ENABLED=true — PRE-EXPLOSION path active",
            "Set LEGACY_NOTIFICATIONS_ENABLED=false unless rollback is intentional",
        )
    return _pass("legacy_notifications", "Legacy notification path disabled (default)")


def _check_unified_flag() -> dict:
    import feature_flags
    if feature_flags.unified_notifications_enabled():
        return _warn(
            "unified_notifications",
            "UNIFIED_NOTIFICATIONS_ENABLED=true — gateway is active",
            "Verify this is intentional before deploying",
        )
    return _pass("unified_notifications", "Unified gateway inactive (default)")


def _check_alpha_delivery_flags() -> dict:
    try:
        from alpha_notification_delivery import get_delivery_flags
        flags = get_delivery_flags()
        enabled = flags.get("enabled", False)
        dry_run = flags.get("dry_run_only", True)

        if enabled and not dry_run:
            return _fail(
                "alpha_delivery",
                "ALPHA_NOTIFICATIONS_ENABLED=true AND dry_run_only=false — real sends active",
                "Set ALPHA_NOTIFICATIONS_DRY_RUN_ONLY=true or ALPHA_NOTIFICATIONS_ENABLED=false",
            )
        if enabled and dry_run:
            return _warn(
                "alpha_delivery",
                "Alpha notifications enabled in dry-run-only mode",
                "Confirm dry-run is intentional",
            )
        return _pass("alpha_delivery", "Alpha delivery blocked (ALPHA_NOTIFICATIONS_ENABLED=false)")
    except Exception as exc:
        return _warn("alpha_delivery", f"Could not inspect delivery flags: {exc}")


def _check_eod_brief_flag() -> dict:
    enabled = os.getenv("EOD_BRIEF_ENABLED", "false").lower() == "true"
    if enabled:
        return _warn("eod_brief_flag", "EOD_BRIEF_ENABLED=true — EOD brief will send via WhatsApp")
    return _pass("eod_brief_flag", "EOD brief WhatsApp send disabled (default)")


def _check_weekly_review_flag() -> dict:
    enabled = os.getenv("WEEKLY_REVIEW_ENABLED", "false").lower() == "true"
    if enabled:
        return _warn("weekly_review_flag", "WEEKLY_REVIEW_ENABLED=true — weekly review will send via WhatsApp")
    return _pass("weekly_review_flag", "Weekly review WhatsApp send disabled (default)")


def _check_notification_center_flag() -> dict:
    try:
        from notification_center import notification_center_enabled
        state = notification_center_enabled()
        if state:
            return _pass("notification_center", "Notification center enabled (stores to DB only, no sends)")
        return _warn("notification_center", "NOTIFICATION_CENTER_ENABLED=false — inbox generation off")
    except Exception as exc:
        return _warn("notification_center", f"Could not check notification center flag: {exc}")


def run_notification_safety_checks() -> list:
    return [
        _check_legacy_flag(),
        _check_unified_flag(),
        _check_alpha_delivery_flags(),
        _check_eod_brief_flag(),
        _check_weekly_review_flag(),
        _check_notification_center_flag(),
    ]


# ── Section D: Data health ────────────────────────────────────────────────────

def _check_no_negative_quantities() -> dict:
    try:
        import database
        conn = database.get_connection()
        try:
            rows = conn.execute(
                "SELECT ticker, shares FROM holdings WHERE shares < 0"
            ).fetchall()
        finally:
            conn.close()
        if rows:
            tickers = [r[0] for r in rows]
            return _fail(
                "no_negative_quantities",
                f"Holdings with negative shares: {tickers}",
                "Run portfolio reconciliation to fix corrupted holding rows",
            )
        return _pass("no_negative_quantities", "No negative share quantities")
    except Exception as exc:
        return _warn("no_negative_quantities", f"Could not check quantities: {exc}")


def _check_no_duplicate_active_manual_holdings() -> dict:
    try:
        import database
        conn = database.get_connection()
        try:
            # manual_portfolio_positions table may have ticker+account_id as composite key
            rows = conn.execute("""
                SELECT ticker, account_id, COUNT(*) as cnt
                FROM manual_portfolio_positions
                WHERE is_active = 1
                GROUP BY ticker, account_id
                HAVING cnt > 1
            """).fetchall()
        finally:
            conn.close()
        if rows:
            dupes = [(r[0], r[1]) for r in rows]
            return _fail(
                "no_duplicate_manual_holdings",
                f"Duplicate active manual holdings for (ticker, account): {dupes}",
                "Deactivate duplicate rows via POST /api/v1/portfolio/manual/position/deactivate",
            )
        return _pass("no_duplicate_manual_holdings", "No duplicate active manual holdings")
    except Exception as exc:
        return _warn("no_duplicate_manual_holdings", f"Could not check manual holdings: {exc}")


def _check_alpha_shadow_log_accessible() -> dict:
    try:
        import database
        conn = database.get_connection()
        try:
            conn.execute("SELECT COUNT(*) FROM alpha_shadow_log").fetchone()
        finally:
            conn.close()
        return _pass("alpha_shadow_log", "alpha_shadow_log readable")
    except Exception as exc:
        return _warn("alpha_shadow_log", f"Cannot read alpha_shadow_log: {exc}")


def run_data_health_checks() -> list:
    return [
        _check_no_negative_quantities(),
        _check_no_duplicate_active_manual_holdings(),
        _check_alpha_shadow_log_accessible(),
    ]


# ── Section E: Brief safety ───────────────────────────────────────────────────

_MAX_BRIEF_CHARS = 4096


def _check_brief_banned_words(text: str, brief_name: str) -> Optional[dict]:
    lower = text.lower()
    hits = [w for w in BRIEF_BANNED_WORDS if w in lower]
    if hits:
        return _fail(
            f"{brief_name}_banned_words",
            f"Banned words in {brief_name} output: {hits}",
            "Review brief generator for banned-word leakage",
        )
    return None


def _check_daily_brief() -> dict:
    try:
        from operator_brief import generate_compact_brief
        text = generate_compact_brief()
        if not isinstance(text, str):
            return _fail("daily_brief", "generate_compact_brief() returned non-string")
        if len(text) > _MAX_BRIEF_CHARS:
            return _warn(
                "daily_brief",
                f"Brief is {len(text)} chars (limit {_MAX_BRIEF_CHARS})",
                "Check for runaway loops in collect_brief_data()",
            )
        banned = _check_brief_banned_words(text, "daily_brief")
        if banned:
            return banned
        return _pass("daily_brief", f"Daily brief generated OK ({len(text)} chars)")
    except Exception as exc:
        return _warn("daily_brief", f"Brief generation raised: {exc}",
                     "Check operator_brief.py and its data collectors")


def _check_eod_brief() -> dict:
    try:
        from eod_brief import generate_compact_eod
        text = generate_compact_eod()
        if not isinstance(text, str):
            return _fail("eod_brief", "generate_compact_eod() returned non-string")
        if len(text) > _MAX_BRIEF_CHARS:
            return _warn("eod_brief", f"EOD brief is {len(text)} chars (limit {_MAX_BRIEF_CHARS})")
        banned = _check_brief_banned_words(text, "eod_brief")
        if banned:
            return banned
        return _pass("eod_brief", f"EOD brief generated OK ({len(text)} chars)")
    except Exception as exc:
        return _warn("eod_brief", f"EOD brief generation raised: {exc}")


def _check_weekly_review() -> dict:
    try:
        from weekly_review import _compute_review, format_compact_weekly
        sections, metrics, grade, data, generated_at, ws = _compute_review(None)
        text = format_compact_weekly(sections, metrics, grade, ws, generated_at)
        if not isinstance(text, str):
            return _fail("weekly_review", "format_compact_weekly() returned non-string")
        if len(text) > _MAX_BRIEF_CHARS:
            return _warn("weekly_review", f"Weekly review is {len(text)} chars (limit {_MAX_BRIEF_CHARS})")
        banned = _check_brief_banned_words(text, "weekly_review")
        if banned:
            return banned
        return _pass("weekly_review", f"Weekly review generated OK (grade {grade}, {len(text)} chars)")
    except Exception as exc:
        return _warn("weekly_review", f"Weekly review generation raised: {exc}")


def run_brief_safety_checks() -> list:
    return [
        _check_daily_brief(),
        _check_eod_brief(),
        _check_weekly_review(),
    ]


# ── Section F: Alpha safety ───────────────────────────────────────────────────

def _check_alpha_gate_runs() -> dict:
    try:
        from alpha_alert_gate import get_alert_gate_summary
        summary = get_alert_gate_summary()
        if not isinstance(summary, dict):
            return _warn("alpha_gate", "get_alert_gate_summary() returned unexpected type")
        return _pass("alpha_gate", "Alert gate summary loaded OK")
    except Exception as exc:
        return _warn("alpha_gate", f"Alert gate check raised: {exc}")


def _check_alpha_top_loadable() -> dict:
    try:
        from alpha_shadow import get_shadow_manager
        manager = get_shadow_manager()
        if manager is None:
            return _warn("alpha_top", "get_shadow_manager() returned None")
        return _pass("alpha_top", "Alpha shadow manager accessible")
    except Exception as exc:
        return _warn("alpha_top", f"Alpha shadow manager raised: {exc}")


def _check_delivery_bridge_blocks_by_default() -> dict:
    try:
        from alpha_notification_delivery import check_delivery_eligibility
        # Pass a fake dry_run_id that won't exist — eligibility check should
        # return BLOCKED (ALPHA_NOTIFICATIONS_DISABLED) without any side effects.
        os.environ.pop("ALPHA_NOTIFICATIONS_ENABLED", None)
        result = check_delivery_eligibility("__release_check_probe__")
        status = result.get("status", "")
        if status in ("BLOCKED", "DRY_RUN_ONLY"):
            return _pass(
                "delivery_bridge_default",
                f"Delivery bridge blocks by default (status={status})",
            )
        return _fail(
            "delivery_bridge_default",
            f"Delivery bridge returned unexpected status={status} with no env flags set",
            "Ensure ALPHA_NOTIFICATIONS_ENABLED defaults to false in alpha_notification_delivery.py",
        )
    except Exception as exc:
        return _warn("delivery_bridge_default", f"Delivery bridge check raised: {exc}")


def run_alpha_safety_checks() -> list:
    return [
        _check_alpha_gate_runs(),
        _check_alpha_top_loadable(),
        _check_delivery_bridge_blocks_by_default(),
    ]


# ── Orchestrator ──────────────────────────────────────────────────────────────

_SECTION_RUNNERS = [
    ("core",                  run_core_checks),
    ("routes",                run_route_checks),
    ("notification_safety",   run_notification_safety_checks),
    ("data_health",           run_data_health_checks),
    ("brief_safety",          run_brief_safety_checks),
    ("alpha_safety",          run_alpha_safety_checks),
]


def _overall_status(results: list) -> str:
    statuses = {r["status"] for r in results}
    if CHECK_FAIL in statuses:
        # Distinguish critical (safety/data) from degraded (feature unavailable)
        critical_names = {
            "db_connection", "required_tables", "no_negative_quantities",
            "delivery_bridge_default", "alpha_delivery",
        }
        if any(r["status"] == CHECK_FAIL and r["name"] in critical_names for r in results):
            return STATUS_CRITICAL
        return STATUS_DEGRADED
    if CHECK_WARN in statuses:
        return STATUS_WATCH
    return STATUS_HEALTHY


def _environment_summary() -> dict:
    """Safe environment snapshot — no secrets."""
    return {
        "LEGACY_NOTIFICATIONS_ENABLED":      os.getenv("LEGACY_NOTIFICATIONS_ENABLED", "(unset)"),
        "UNIFIED_NOTIFICATIONS_ENABLED":     os.getenv("UNIFIED_NOTIFICATIONS_ENABLED", "(unset)"),
        "ALPHA_SHADOW_ENABLED":              os.getenv("ALPHA_SHADOW_ENABLED", "(unset)"),
        "ALPHA_ALERTS_ENABLED":              os.getenv("ALPHA_ALERTS_ENABLED", "(unset)"),
        "ALPHA_NOTIFICATIONS_ENABLED":       os.getenv("ALPHA_NOTIFICATIONS_ENABLED", "(unset)"),
        "ALPHA_NOTIFICATIONS_DRY_RUN_ONLY":  os.getenv("ALPHA_NOTIFICATIONS_DRY_RUN_ONLY", "(unset)"),
        "EOD_BRIEF_ENABLED":                 os.getenv("EOD_BRIEF_ENABLED", "(unset)"),
        "WEEKLY_REVIEW_ENABLED":             os.getenv("WEEKLY_REVIEW_ENABLED", "(unset)"),
        "NOTIFICATION_CENTER_ENABLED":       os.getenv("NOTIFICATION_CENTER_ENABLED", "(unset)"),
        "API_SECRET":                        "***" if os.getenv("API_SECRET") else "(unset)",
        "RAILWAY_ENVIRONMENT":               os.getenv("RAILWAY_ENVIRONMENT", "(local)"),
    }


def run_release_check(mode: str = "full") -> dict:
    """
    Run all system checks and return a structured report.

    mode='compact' skips brief safety checks (faster, no DB-heavy calls).
    """
    generated_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")

    all_results: list = []
    sections_run: dict = {}

    runners = _SECTION_RUNNERS
    if mode == "compact":
        runners = [(name, fn) for name, fn in runners if name != "brief_safety"]

    for section_name, runner_fn in runners:
        try:
            section_results = runner_fn()
        except Exception as exc:
            section_results = [_fail(
                f"{section_name}_runner",
                f"Section runner raised: {traceback.format_exc()}",
                "Investigate system_release_check.py",
            )]
        sections_run[section_name] = section_results
        all_results.extend(section_results)

    overall = _overall_status(all_results)
    passed  = [r for r in all_results if r["status"] == CHECK_PASS]
    warned  = [r for r in all_results if r["status"] == CHECK_WARN]
    failed  = [r for r in all_results if r["status"] == CHECK_FAIL]

    fixes = [r.get("fix", "") for r in failed if r.get("fix")]
    fixes += [r.get("fix", "") for r in warned if r.get("fix")]
    fixes = [f for f in fixes if f]

    return {
        "overall_status":    overall,
        "checks_passed":     len(passed),
        "checks_warned":     len(warned),
        "checks_failed":     len(failed),
        "checks_total":      len(all_results),
        "warnings":          [{"name": r["name"], "detail": r["detail"]} for r in warned],
        "failures":          [{"name": r["name"], "detail": r["detail"]} for r in failed],
        "recommended_fixes": fixes,
        "sections":          sections_run,
        "environment":       _environment_summary(),
        "generated_at":      generated_at,
        "mode":              mode,
    }


def get_route_list() -> dict:
    """Return a structured list of all registered API routes."""
    try:
        routes = _get_registered_routes()
        return {
            "routes":       routes,
            "count":        len(routes),
            "required":     REQUIRED_ROUTES,
            "required_count": len(REQUIRED_ROUTES),
        }
    except Exception as exc:
        return {"routes": [], "count": 0, "error": str(exc)}


def get_flag_summary() -> dict:
    """Return all feature flags with their current runtime values."""
    flags: dict = {}
    try:
        import feature_flags as ff
        flags["legacy_notifications_enabled"]  = ff.legacy_notifications_enabled()
        flags["unified_notifications_enabled"] = ff.unified_notifications_enabled()
        flags["shadow_compare_enabled"]        = ff.shadow_compare_enabled()
        flags["alpha_shadow_enabled"]          = ff.alpha_shadow_enabled()
        flags["alpha_alerts_enabled"]          = ff.alpha_alerts_enabled()
    except Exception as exc:
        flags["feature_flags_error"] = str(exc)

    try:
        from eod_brief import eod_enabled
        flags["eod_brief_enabled"] = eod_enabled()
    except Exception:
        flags["eod_brief_enabled"] = os.getenv("EOD_BRIEF_ENABLED", "false").lower() == "true"

    try:
        from weekly_review import weekly_review_enabled
        flags["weekly_review_enabled"] = weekly_review_enabled()
    except Exception:
        flags["weekly_review_enabled"] = os.getenv("WEEKLY_REVIEW_ENABLED", "false").lower() == "true"

    try:
        from notification_center import notification_center_enabled
        flags["notification_center_enabled"] = notification_center_enabled()
    except Exception:
        flags["notification_center_enabled"] = None

    try:
        from alpha_notification_delivery import get_delivery_flags
        delivery = get_delivery_flags()
        flags["alpha_notifications_enabled"]      = delivery.get("enabled", False)
        flags["alpha_notifications_dry_run_only"] = delivery.get("dry_run_only", True)
    except Exception as exc:
        flags["alpha_delivery_flags_error"] = str(exc)

    return {
        "flags":        flags,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
    }
