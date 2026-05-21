"""
Phase A28 — System Release Check tests.

Covers:
- Module constants: STATUS_*, CHECK_*, REQUIRED_TABLES, REQUIRED_ROUTES
- Core checks: DB connection, migrations table, required tables, scheduler
- Route availability: all REQUIRED_ROUTES registered, missing route detected
- Notification safety: legacy/unified/alpha/EOD/weekly/NC flag checks
- Data health: negative quantities, duplicate manual holdings, shadow log
- Brief safety: generation OK, char limit, banned words caught
- Alpha safety: gate runs, delivery bridge blocked by default
- run_release_check(): structure, overall_status ladder, sections
- compact mode skips brief section
- _environment_summary() never leaks secrets
- get_route_list() structure
- get_flag_summary() structure
- API endpoints: GET /system/release-check, /system/routes, /system/flags
- No send_sms calls, no trading calls, never crashes on sparse data
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = tmp.name

    def _conn():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    return path, _conn


def _patch_db(conn_fn):
    import database
    return patch.object(database, "get_connection", conn_fn)


def _make_app():
    import database
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    database.DB_PATH = tmp.name

    def _conn():
        c = sqlite3.connect(tmp.name)
        c.row_factory = sqlite3.Row
        return c

    with patch.object(database, "get_connection", _conn):
        import sms_handler
        app = sms_handler.app
        app.config["TESTING"] = True
        return app, _conn, database


def _src():
    import system_release_check as src
    return src


# ── Constants ─────────────────────────────────────────────────────────────────

class TestConstants(unittest.TestCase):
    def setUp(self):
        self.src = _src()

    def test_status_constants_exist(self):
        for s in ("STATUS_HEALTHY", "STATUS_WATCH", "STATUS_DEGRADED", "STATUS_CRITICAL"):
            self.assertTrue(hasattr(self.src, s))

    def test_check_constants_exist(self):
        for c in ("CHECK_PASS", "CHECK_WARN", "CHECK_FAIL"):
            self.assertTrue(hasattr(self.src, c))

    def test_required_tables_is_frozenset(self):
        self.assertIsInstance(self.src.REQUIRED_TABLES, frozenset)

    def test_required_tables_not_empty(self):
        self.assertGreater(len(self.src.REQUIRED_TABLES), 5)

    def test_required_routes_is_list(self):
        self.assertIsInstance(self.src.REQUIRED_ROUTES, list)

    def test_required_routes_include_health(self):
        self.assertIn("/api/v1/health", self.src.REQUIRED_ROUTES)

    def test_required_routes_include_system_endpoints(self):
        for route in ("/api/v1/system/release-check",
                      "/api/v1/system/routes",
                      "/api/v1/system/flags"):
            self.assertIn(route, self.src.REQUIRED_ROUTES)

    def test_brief_banned_words_not_empty(self):
        self.assertGreater(len(self.src.BRIEF_BANNED_WORDS), 0)

    def test_explosion_is_banned(self):
        self.assertIn("explosion", self.src.BRIEF_BANNED_WORDS)


# ── Result helpers ────────────────────────────────────────────────────────────

class TestResultHelpers(unittest.TestCase):
    def setUp(self):
        self.src = _src()

    def test_pass_has_status_pass(self):
        r = self.src._pass("test_name", "detail")
        self.assertEqual(r["status"], self.src.CHECK_PASS)
        self.assertEqual(r["name"], "test_name")

    def test_warn_has_status_warn(self):
        r = self.src._warn("test_name", "detail")
        self.assertEqual(r["status"], self.src.CHECK_WARN)

    def test_fail_has_status_fail(self):
        r = self.src._fail("test_name", "detail")
        self.assertEqual(r["status"], self.src.CHECK_FAIL)

    def test_fix_optional(self):
        r = self.src._fail("x", "d", "fix this")
        self.assertEqual(r["fix"], "fix this")


# ── Core checks ───────────────────────────────────────────────────────────────

class TestCoreChecks(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        self.src = _src()

    def test_db_connection_pass(self):
        with _patch_db(self.conn_fn):
            result = self.src._check_db_connection()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_db_connection_fail_on_bad_db(self):
        def _bad():
            raise Exception("no db")
        with patch("database.get_connection", _bad):
            result = self.src._check_db_connection()
        self.assertEqual(result["status"], self.src.CHECK_FAIL)

    def test_required_tables_fail_when_missing(self):
        # Fresh empty DB has no tables
        with _patch_db(self.conn_fn):
            result = self.src._check_required_tables()
        self.assertEqual(result["status"], self.src.CHECK_FAIL)
        self.assertIn("Missing tables", result["detail"])

    def test_required_tables_pass_when_all_present(self):
        import database
        _, conn_fn = _make_db()
        conn = conn_fn()
        for table in self.src.REQUIRED_TABLES:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        with patch.object(database, "get_connection", conn_fn):
            result = self.src._check_required_tables()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_migrations_table_warn_on_empty_db(self):
        with _patch_db(self.conn_fn):
            result = self.src._check_migrations_table()
        # Empty DB: schema_version doesn't exist → should warn or fail
        self.assertIn(result["status"], (self.src.CHECK_WARN, self.src.CHECK_FAIL))

    def test_scheduler_safety_check_runs_without_crash(self):
        result = self.src._check_scheduler_safety()
        self.assertIn(result["status"], (self.src.CHECK_PASS, self.src.CHECK_WARN))

    def test_run_core_checks_returns_list(self):
        with _patch_db(self.conn_fn):
            results = self.src.run_core_checks()
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)


# ── Route availability ────────────────────────────────────────────────────────

class TestRouteChecks(unittest.TestCase):
    def setUp(self):
        self.src = _src()

    def test_check_route_availability_pass_with_all_routes(self):
        all_routes = list(self.src.REQUIRED_ROUTES)
        with patch.object(self.src, "_get_registered_routes", return_value=all_routes):
            result = self.src._check_route_availability()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_check_route_availability_fail_with_missing_route(self):
        partial = [r for r in self.src.REQUIRED_ROUTES if "health" not in r]
        with patch.object(self.src, "_get_registered_routes", return_value=partial):
            result = self.src._check_route_availability()
        self.assertEqual(result["status"], self.src.CHECK_FAIL)
        self.assertIn("Missing routes", result["detail"])

    def test_get_registered_routes_returns_list(self):
        routes = self.src._get_registered_routes()
        self.assertIsInstance(routes, list)

    def test_get_registered_routes_includes_health(self):
        routes = self.src._get_registered_routes()
        self.assertIn("/api/v1/health", routes)

    def test_run_route_checks_returns_list(self):
        results = self.src.run_route_checks()
        self.assertIsInstance(results, list)


# ── Notification safety ───────────────────────────────────────────────────────

class TestNotificationSafetyChecks(unittest.TestCase):
    def setUp(self):
        self.src = _src()

    def test_legacy_flag_pass_when_disabled(self):
        with patch.dict(os.environ, {"LEGACY_NOTIFICATIONS_ENABLED": "false"}):
            result = self.src._check_legacy_flag()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_legacy_flag_warn_when_enabled(self):
        with patch.dict(os.environ, {"LEGACY_NOTIFICATIONS_ENABLED": "true"}):
            result = self.src._check_legacy_flag()
        self.assertEqual(result["status"], self.src.CHECK_WARN)

    def test_unified_flag_pass_when_disabled(self):
        with patch.dict(os.environ, {"UNIFIED_NOTIFICATIONS_ENABLED": "false"}):
            result = self.src._check_unified_flag()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_unified_flag_warn_when_enabled(self):
        with patch.dict(os.environ, {"UNIFIED_NOTIFICATIONS_ENABLED": "true"}):
            result = self.src._check_unified_flag()
        self.assertEqual(result["status"], self.src.CHECK_WARN)

    def test_alpha_delivery_pass_when_disabled(self):
        mock_flags = {"enabled": False, "dry_run_only": True}
        with patch("alpha_notification_delivery.get_delivery_flags", return_value=mock_flags):
            result = self.src._check_alpha_delivery_flags()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_alpha_delivery_warn_when_dry_run_only(self):
        mock_flags = {"enabled": True, "dry_run_only": True}
        with patch("alpha_notification_delivery.get_delivery_flags", return_value=mock_flags):
            result = self.src._check_alpha_delivery_flags()
        self.assertEqual(result["status"], self.src.CHECK_WARN)

    def test_alpha_delivery_fail_when_live_sends_enabled(self):
        mock_flags = {"enabled": True, "dry_run_only": False}
        with patch("alpha_notification_delivery.get_delivery_flags", return_value=mock_flags):
            result = self.src._check_alpha_delivery_flags()
        self.assertEqual(result["status"], self.src.CHECK_FAIL)

    def test_eod_brief_flag_pass_when_disabled(self):
        with patch.dict(os.environ, {"EOD_BRIEF_ENABLED": "false"}):
            result = self.src._check_eod_brief_flag()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_eod_brief_flag_warn_when_enabled(self):
        with patch.dict(os.environ, {"EOD_BRIEF_ENABLED": "true"}):
            result = self.src._check_eod_brief_flag()
        self.assertEqual(result["status"], self.src.CHECK_WARN)

    def test_weekly_review_flag_pass_when_disabled(self):
        with patch.dict(os.environ, {"WEEKLY_REVIEW_ENABLED": "false"}):
            result = self.src._check_weekly_review_flag()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_weekly_review_flag_warn_when_enabled(self):
        with patch.dict(os.environ, {"WEEKLY_REVIEW_ENABLED": "true"}):
            result = self.src._check_weekly_review_flag()
        self.assertEqual(result["status"], self.src.CHECK_WARN)

    def test_notification_center_pass_when_enabled(self):
        with patch("notification_center.notification_center_enabled", return_value=True):
            result = self.src._check_notification_center_flag()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_notification_center_warn_when_disabled(self):
        with patch("notification_center.notification_center_enabled", return_value=False):
            result = self.src._check_notification_center_flag()
        self.assertEqual(result["status"], self.src.CHECK_WARN)

    def test_run_notification_safety_checks_returns_list(self):
        results = self.src.run_notification_safety_checks()
        self.assertIsInstance(results, list)
        self.assertGreaterEqual(len(results), 5)


# ── Data health ───────────────────────────────────────────────────────────────

class TestDataHealthChecks(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        self.src = _src()
        # Bootstrap holdings table
        conn = self.conn_fn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL UNIQUE,
                shares REAL NOT NULL,
                avg_cost REAL NOT NULL,
                date_added TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS manual_portfolio_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                account_id TEXT NOT NULL,
                shares REAL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alpha_shadow_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT
            )
        """)
        conn.commit()
        conn.close()

    def test_no_negative_quantities_pass(self):
        conn = self.conn_fn()
        conn.execute("INSERT INTO holdings (ticker, shares, avg_cost, date_added) VALUES ('AAPL', 10, 150, '2026-01-01')")
        conn.commit()
        conn.close()
        with _patch_db(self.conn_fn):
            result = self.src._check_no_negative_quantities()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_no_negative_quantities_fail_with_negative(self):
        conn = self.conn_fn()
        conn.execute("INSERT INTO holdings (ticker, shares, avg_cost, date_added) VALUES ('AAPL', -5, 150, '2026-01-01')")
        conn.commit()
        conn.close()
        with _patch_db(self.conn_fn):
            result = self.src._check_no_negative_quantities()
        self.assertEqual(result["status"], self.src.CHECK_FAIL)
        self.assertIn("AAPL", result["detail"])

    def test_no_duplicate_manual_holdings_pass(self):
        conn = self.conn_fn()
        conn.execute("INSERT INTO manual_portfolio_positions (ticker, account_id, shares, is_active) VALUES ('NVDA','acct1',5,1)")
        conn.commit()
        conn.close()
        with _patch_db(self.conn_fn):
            result = self.src._check_no_duplicate_active_manual_holdings()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_no_duplicate_manual_holdings_fail_with_dupes(self):
        conn = self.conn_fn()
        conn.execute("INSERT INTO manual_portfolio_positions (ticker, account_id, shares, is_active) VALUES ('NVDA','acct1',5,1)")
        conn.execute("INSERT INTO manual_portfolio_positions (ticker, account_id, shares, is_active) VALUES ('NVDA','acct1',3,1)")
        conn.commit()
        conn.close()
        with _patch_db(self.conn_fn):
            result = self.src._check_no_duplicate_active_manual_holdings()
        self.assertEqual(result["status"], self.src.CHECK_FAIL)

    def test_alpha_shadow_log_pass(self):
        with _patch_db(self.conn_fn):
            result = self.src._check_alpha_shadow_log_accessible()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_alpha_shadow_log_warn_on_missing_table(self):
        _, empty_fn = _make_db()
        with _patch_db(empty_fn):
            result = self.src._check_alpha_shadow_log_accessible()
        self.assertEqual(result["status"], self.src.CHECK_WARN)

    def test_run_data_health_checks_returns_list(self):
        with _patch_db(self.conn_fn):
            results = self.src.run_data_health_checks()
        self.assertIsInstance(results, list)


# ── Brief safety ──────────────────────────────────────────────────────────────

class TestBriefSafetyChecks(unittest.TestCase):
    def setUp(self):
        self.src = _src()

    def test_daily_brief_pass_with_clean_output(self):
        with patch("operator_brief.generate_compact_brief", return_value="Clean brief output"):
            result = self.src._check_daily_brief()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_daily_brief_warn_on_exception(self):
        with patch("operator_brief.generate_compact_brief", side_effect=Exception("boom")):
            result = self.src._check_daily_brief()
        self.assertEqual(result["status"], self.src.CHECK_WARN)

    def test_daily_brief_warn_on_oversize(self):
        big_text = "x" * 5000
        with patch("operator_brief.generate_compact_brief", return_value=big_text):
            result = self.src._check_daily_brief()
        self.assertEqual(result["status"], self.src.CHECK_WARN)
        self.assertIn("chars", result["detail"])

    def test_daily_brief_fail_on_banned_word(self):
        with patch("operator_brief.generate_compact_brief",
                   return_value="This is a pre-explosion buy signal"):
            result = self.src._check_daily_brief()
        self.assertEqual(result["status"], self.src.CHECK_FAIL)
        self.assertIn("banned", result["detail"].lower())

    def test_eod_brief_pass_with_clean_output(self):
        with patch("eod_brief.generate_compact_eod", return_value="Clean EOD"):
            result = self.src._check_eod_brief()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_eod_brief_warn_on_exception(self):
        with patch("eod_brief.generate_compact_eod", side_effect=Exception("eod fail")):
            result = self.src._check_eod_brief()
        self.assertEqual(result["status"], self.src.CHECK_WARN)

    def test_weekly_review_pass_with_clean_output(self):
        mock_sections = {}
        mock_metrics = {}
        with patch("weekly_review._compute_review", return_value=(mock_sections, mock_metrics, "B", {}, "2026-05-21", "2026-05-18")):
            with patch("weekly_review.format_compact_weekly", return_value="Clean weekly review"):
                result = self.src._check_weekly_review()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_weekly_review_warn_on_exception(self):
        with patch("weekly_review._compute_review", side_effect=Exception("review fail")):
            result = self.src._check_weekly_review()
        self.assertEqual(result["status"], self.src.CHECK_WARN)

    def test_run_brief_safety_checks_returns_list(self):
        with patch("operator_brief.generate_compact_brief", return_value="ok"):
            with patch("eod_brief.generate_compact_eod", return_value="ok"):
                with patch("weekly_review._compute_review", return_value=({}, {}, "C", {}, "t", "w")):
                    with patch("weekly_review.format_compact_weekly", return_value="ok"):
                        results = self.src.run_brief_safety_checks()
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 3)

    def test_brief_banned_words_check_clean(self):
        result = self.src._check_brief_banned_words("Clean portfolio update", "test_brief")
        self.assertIsNone(result)

    def test_brief_banned_words_check_catches_explosion(self):
        result = self.src._check_brief_banned_words("PRE-EXPLOSION alert!", "test_brief")
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], self.src.CHECK_FAIL)


# ── Alpha safety ──────────────────────────────────────────────────────────────

class TestAlphaSafetyChecks(unittest.TestCase):
    def setUp(self):
        self.src = _src()

    def test_alpha_gate_runs_pass(self):
        with patch("alpha_alert_gate.get_alert_gate_summary", return_value={"ok": True}):
            result = self.src._check_alpha_gate_runs()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_alpha_gate_runs_warn_on_exception(self):
        with patch("alpha_alert_gate.get_alert_gate_summary", side_effect=Exception("gate fail")):
            result = self.src._check_alpha_gate_runs()
        self.assertEqual(result["status"], self.src.CHECK_WARN)

    def test_delivery_bridge_blocked_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ALPHA_NOTIFICATIONS_ENABLED", None)
            with patch("alpha_notification_delivery.check_delivery_eligibility",
                       return_value={"status": "BLOCKED", "reason": "ALPHA_NOTIFICATIONS_DISABLED"}):
                result = self.src._check_delivery_bridge_blocks_by_default()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_delivery_bridge_fail_when_enabled_unexpectedly(self):
        with patch("alpha_notification_delivery.check_delivery_eligibility",
                   return_value={"status": "ELIGIBLE"}):
            result = self.src._check_delivery_bridge_blocks_by_default()
        self.assertEqual(result["status"], self.src.CHECK_FAIL)

    def test_delivery_bridge_pass_on_dry_run_only(self):
        with patch("alpha_notification_delivery.check_delivery_eligibility",
                   return_value={"status": "DRY_RUN_ONLY"}):
            result = self.src._check_delivery_bridge_blocks_by_default()
        self.assertEqual(result["status"], self.src.CHECK_PASS)

    def test_run_alpha_safety_checks_returns_list(self):
        results = self.src.run_alpha_safety_checks()
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)


# ── run_release_check() ───────────────────────────────────────────────────────

class TestRunReleaseCheck(unittest.TestCase):
    def setUp(self):
        self.src = _src()

    def _mock_healthy_check(self):
        return [self.src._pass("mock", "all good")]

    def test_returns_required_keys(self):
        with patch.object(self.src, "_SECTION_RUNNERS", [("mock", self._mock_healthy_check)]):
            result = self.src.run_release_check()
        for key in ("overall_status", "checks_passed", "checks_warned", "checks_failed",
                    "checks_total", "warnings", "failures", "recommended_fixes",
                    "sections", "environment", "generated_at", "mode"):
            self.assertIn(key, result)

    def test_healthy_when_all_pass(self):
        with patch.object(self.src, "_SECTION_RUNNERS", [("mock", self._mock_healthy_check)]):
            result = self.src.run_release_check()
        self.assertEqual(result["overall_status"], self.src.STATUS_HEALTHY)

    def test_watch_when_warns(self):
        def _warn_checks():
            return [self.src._warn("mock", "minor issue")]
        with patch.object(self.src, "_SECTION_RUNNERS", [("mock", _warn_checks)]):
            result = self.src.run_release_check()
        self.assertEqual(result["overall_status"], self.src.STATUS_WATCH)

    def test_degraded_when_non_critical_fails(self):
        def _fail_checks():
            return [self.src._fail("some_feature", "not available")]
        with patch.object(self.src, "_SECTION_RUNNERS", [("mock", _fail_checks)]):
            result = self.src.run_release_check()
        self.assertIn(result["overall_status"],
                      (self.src.STATUS_DEGRADED, self.src.STATUS_CRITICAL))

    def test_critical_when_db_fails(self):
        def _fail_checks():
            return [self.src._fail("db_connection", "no database")]
        with patch.object(self.src, "_SECTION_RUNNERS", [("mock", _fail_checks)]):
            result = self.src.run_release_check()
        self.assertEqual(result["overall_status"], self.src.STATUS_CRITICAL)

    def test_failures_list_populated(self):
        def _fail_checks():
            return [self.src._fail("x", "broken")]
        with patch.object(self.src, "_SECTION_RUNNERS", [("mock", _fail_checks)]):
            result = self.src.run_release_check()
        self.assertEqual(len(result["failures"]), 1)
        self.assertEqual(result["failures"][0]["name"], "x")

    def test_compact_mode_skips_brief_section(self):
        # In compact mode, "brief_safety" section should not run
        ran_sections = []

        def _track(name):
            def _runner():
                ran_sections.append(name)
                return [self.src._pass(name)]
            return _runner

        runners = [
            ("core",   _track("core")),
            ("brief_safety", _track("brief_safety")),
            ("routes", _track("routes")),
        ]
        with patch.object(self.src, "_SECTION_RUNNERS", runners):
            self.src.run_release_check(mode="compact")
        self.assertNotIn("brief_safety", ran_sections)

    def test_full_mode_runs_brief_section(self):
        ran_sections = []

        def _track(name):
            def _runner():
                ran_sections.append(name)
                return [self.src._pass(name)]
            return _runner

        runners = [
            ("core",   _track("core")),
            ("brief_safety", _track("brief_safety")),
        ]
        with patch.object(self.src, "_SECTION_RUNNERS", runners):
            self.src.run_release_check(mode="full")
        self.assertIn("brief_safety", ran_sections)

    def test_section_runner_exception_captured(self):
        def _exploding():
            raise RuntimeError("runner crash")
        with patch.object(self.src, "_SECTION_RUNNERS", [("exploding", _exploding)]):
            result = self.src.run_release_check()
        # Should not raise; failure should be captured
        self.assertGreaterEqual(result["checks_failed"], 1)

    def test_mode_field_in_result(self):
        with patch.object(self.src, "_SECTION_RUNNERS", []):
            result = self.src.run_release_check(mode="compact")
        self.assertEqual(result["mode"], "compact")


# ── Environment summary ───────────────────────────────────────────────────────

class TestEnvironmentSummary(unittest.TestCase):
    def setUp(self):
        self.src = _src()

    def test_api_secret_masked(self):
        with patch.dict(os.environ, {"API_SECRET": "super-secret-value"}):
            env = self.src._environment_summary()
        self.assertEqual(env["API_SECRET"], "***")
        self.assertNotIn("super-secret-value", str(env))

    def test_api_secret_unset_shows_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("API_SECRET", None)
            env = self.src._environment_summary()
        self.assertEqual(env["API_SECRET"], "(unset)")

    def test_no_secret_values_leaked(self):
        with patch.dict(os.environ, {
            "API_SECRET": "leaked-secret",
            "ANTHROPIC_API_KEY": "sk-ant-12345",
            "TWILIO_AUTH_TOKEN": "twilio-secret",
        }):
            env = self.src._environment_summary()
        env_str = json.dumps(env)
        self.assertNotIn("leaked-secret", env_str)
        self.assertNotIn("sk-ant-12345", env_str)
        self.assertNotIn("twilio-secret", env_str)

    def test_environment_has_flag_keys(self):
        env = self.src._environment_summary()
        for key in ("LEGACY_NOTIFICATIONS_ENABLED", "ALPHA_NOTIFICATIONS_ENABLED",
                    "EOD_BRIEF_ENABLED", "WEEKLY_REVIEW_ENABLED"):
            self.assertIn(key, env)


# ── get_route_list() ──────────────────────────────────────────────────────────

class TestGetRouteList(unittest.TestCase):
    def setUp(self):
        self.src = _src()

    def test_returns_required_keys(self):
        result = self.src.get_route_list()
        for key in ("routes", "count", "required", "required_count"):
            self.assertIn(key, result)

    def test_routes_is_list(self):
        result = self.src.get_route_list()
        self.assertIsInstance(result["routes"], list)

    def test_count_matches_routes_length(self):
        result = self.src.get_route_list()
        self.assertEqual(result["count"], len(result["routes"]))

    def test_required_count_correct(self):
        result = self.src.get_route_list()
        self.assertEqual(result["required_count"], len(self.src.REQUIRED_ROUTES))

    def test_route_list_deterministic(self):
        r1 = self.src.get_route_list()
        r2 = self.src.get_route_list()
        self.assertEqual(r1["routes"], r2["routes"])


# ── get_flag_summary() ────────────────────────────────────────────────────────

class TestGetFlagSummary(unittest.TestCase):
    def setUp(self):
        self.src = _src()

    def test_returns_required_keys(self):
        result = self.src.get_flag_summary()
        self.assertIn("flags", result)
        self.assertIn("generated_at", result)

    def test_flags_is_dict(self):
        result = self.src.get_flag_summary()
        self.assertIsInstance(result["flags"], dict)

    def test_includes_legacy_flag(self):
        result = self.src.get_flag_summary()
        self.assertIn("legacy_notifications_enabled", result["flags"])

    def test_includes_alpha_delivery_flags(self):
        result = self.src.get_flag_summary()
        self.assertIn("alpha_notifications_enabled", result["flags"])

    def test_eod_brief_default_false(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EOD_BRIEF_ENABLED", None)
            result = self.src.get_flag_summary()
        self.assertFalse(result["flags"].get("eod_brief_enabled", True))

    def test_weekly_review_default_false(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WEEKLY_REVIEW_ENABLED", None)
            result = self.src.get_flag_summary()
        self.assertFalse(result["flags"].get("weekly_review_enabled", True))


# ── API endpoints ─────────────────────────────────────────────────────────────

class TestApiReleaseCheck(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()

    def test_returns_200(self):
        resp = self.client.get("/api/v1/system/release-check")
        self.assertEqual(resp.status_code, 200)

    def test_returns_ok_true(self):
        resp = self.client.get("/api/v1/system/release-check")
        data = resp.get_json()
        self.assertTrue(data["ok"])

    def test_has_overall_status(self):
        resp = self.client.get("/api/v1/system/release-check")
        data = resp.get_json()
        self.assertIn("overall_status", data["data"])

    def test_compact_mode_accepted(self):
        resp = self.client.get("/api/v1/system/release-check?mode=compact")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["data"]["mode"], "compact")

    def test_full_mode_accepted(self):
        resp = self.client.get("/api/v1/system/release-check?mode=full")
        self.assertEqual(resp.status_code, 200)

    def test_invalid_mode_falls_back_to_full(self):
        resp = self.client.get("/api/v1/system/release-check?mode=garbage")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["data"]["mode"], "full")

    def test_no_send_sms_called(self):
        mock_sms = MagicMock()
        with patch("alerts.send_sms", mock_sms):
            self.client.get("/api/v1/system/release-check")
        mock_sms.assert_not_called()


class TestApiSystemRoutes(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()

    def test_returns_200(self):
        resp = self.client.get("/api/v1/system/routes")
        self.assertEqual(resp.status_code, 200)

    def test_has_routes_key(self):
        resp = self.client.get("/api/v1/system/routes")
        data = resp.get_json()
        self.assertIn("routes", data["data"])

    def test_has_count_key(self):
        resp = self.client.get("/api/v1/system/routes")
        data = resp.get_json()
        self.assertIn("count", data["data"])


class TestApiSystemFlags(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()

    def test_returns_200(self):
        resp = self.client.get("/api/v1/system/flags")
        self.assertEqual(resp.status_code, 200)

    def test_has_flags_key(self):
        resp = self.client.get("/api/v1/system/flags")
        data = resp.get_json()
        self.assertIn("flags", data["data"])

    def test_api_secret_not_in_response(self):
        with patch.dict(os.environ, {"API_SECRET": "should-not-appear"}):
            resp = self.client.get("/api/v1/system/flags")
        body = resp.get_data(as_text=True)
        self.assertNotIn("should-not-appear", body)

    def test_no_trading_calls(self):
        import portfolio as p_mod
        buy_mock = MagicMock()
        with patch.object(p_mod, "buy_holding", buy_mock, create=True):
            self.client.get("/api/v1/system/flags")
        buy_mock.assert_not_called()


# ── Safety: no sends, no trades ───────────────────────────────────────────────

class TestSafety(unittest.TestCase):
    def setUp(self):
        self.src = _src()

    def test_run_release_check_no_send_sms(self):
        mock_sms = MagicMock()
        with patch("alerts.send_sms", mock_sms):
            with patch.object(self.src, "_SECTION_RUNNERS", []):
                self.src.run_release_check()
        mock_sms.assert_not_called()

    def test_no_twilio_in_source(self):
        import inspect
        src = inspect.getsource(self.src)
        self.assertNotIn("from twilio", src)
        self.assertNotIn("import twilio", src)

    def test_no_send_sms_in_source(self):
        import inspect
        src = inspect.getsource(self.src)
        self.assertNotIn("send_sms(", src)

    def test_no_buy_holding_in_source(self):
        import inspect
        src = inspect.getsource(self.src)
        self.assertNotIn("buy_holding(", src)

    def test_no_sell_holding_in_source(self):
        import inspect
        src = inspect.getsource(self.src)
        self.assertNotIn("sell_holding(", src)

    def test_never_crashes_api_on_exception(self):
        def _exploding():
            raise RuntimeError("catastrophic failure")
        with patch.object(self.src, "_SECTION_RUNNERS", [("crash", _exploding)]):
            result = self.src.run_release_check()
        self.assertIn("overall_status", result)

    def test_sparse_data_safe_all_checks(self):
        _, empty_fn = _make_db()
        with _patch_db(empty_fn):
            # All individual checks must not raise; they return warn/fail gracefully
            for fn in (
                self.src._check_db_connection,
                self.src._check_migrations_table,
                self.src._check_required_tables,
                self.src._check_no_negative_quantities,
                self.src._check_no_duplicate_active_manual_holdings,
                self.src._check_alpha_shadow_log_accessible,
            ):
                result = fn()
                self.assertIn(result["status"],
                              (self.src.CHECK_PASS, self.src.CHECK_WARN, self.src.CHECK_FAIL))


if __name__ == "__main__":
    unittest.main()
