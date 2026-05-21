"""
Phase N4 — Unified In-App Notification Center tests.

Covers:
- Module constants (CATEGORIES, SEVERITIES, STATUSES, BANNED_WORDS)
- feature flag default (True) and env overrides
- _make_notification_id determinism
- _upsert_notification create + idempotent update
- get_notification / get_notifications with filters
- User actions: mark_read, mark_unread, dismiss, archive,
                mark_all_read, archive_read
- generate_notifications: runs generators, respects feature flag
- Banned-word sanitization in body
- get_summary: counts, by_category, by_severity, top_notifications
- API endpoints: GET /notifications, GET /notifications/summary,
  GET /notifications/<id>, POST /notifications/generate (auth),
  POST /notifications/<id>/read (auth), /unread, /dismiss, /archive,
  POST /notifications/mark-all-read, POST /notifications/archive-read
- Scheduler: _run_notification_center respects flag
- No WhatsApp sends, no trade execution, no push notifications
- Each generator is individually wrapped (sparse-data safety)
- Deterministic IDs: same inputs → same ID
- Migration v27 schema
"""
import importlib
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


def _patch_db(conn_fn):
    import database
    return patch.object(database, "get_connection", conn_fn)


def _seed_notification(conn_fn, notification_id="abc123", status="UNREAD",
                       category="SYSTEM", severity="INFO"):
    conn = conn_fn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notification_center (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_id TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL DEFAULT 'SYSTEM',
                severity TEXT NOT NULL DEFAULT 'INFO',
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                entity_type TEXT,
                entity_id TEXT,
                source TEXT NOT NULL DEFAULT 'system',
                status TEXT NOT NULL DEFAULT 'UNREAD',
                action_url TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute(
            """INSERT OR IGNORE INTO notification_center
               (notification_id, category, severity, title, body, source, status,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (notification_id, category, severity, "Test title", "Test body",
             "system", status, "2026-05-21T10:00:00+00:00", "2026-05-21T10:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()


# ── Constants ─────────────────────────────────────────────────────────────────

class TestConstants(unittest.TestCase):
    def setUp(self):
        import notification_center as nc
        self.nc = nc

    def test_categories_count(self):
        self.assertEqual(len(self.nc.CATEGORIES), 10)

    def test_severities_count(self):
        self.assertEqual(len(self.nc.SEVERITIES), 4)

    def test_statuses_count(self):
        self.assertEqual(len(self.nc.STATUSES), 4)

    def test_banned_words_is_frozenset(self):
        self.assertIsInstance(self.nc.BANNED_WORDS, frozenset)

    def test_explosion_banned(self):
        self.assertIn("explosion", self.nc.BANNED_WORDS)

    def test_categories_values(self):
        for cat in ("PORTFOLIO", "ALPHA_SIGNAL", "RISK", "MARKET", "RESEARCH",
                    "REGIME", "CATALYST", "SYSTEM", "PERFORMANCE", "COMPLIANCE"):
            self.assertIn(cat, self.nc.CATEGORIES)

    def test_severities_values(self):
        for sev in ("CRITICAL", "WARNING", "INFO", "DEBUG"):
            self.assertIn(sev, self.nc.SEVERITIES)

    def test_statuses_values(self):
        for st in ("UNREAD", "READ", "ARCHIVED", "DISMISSED"):
            self.assertIn(st, self.nc.STATUSES)


# ── Feature flag ──────────────────────────────────────────────────────────────

class TestFeatureFlag(unittest.TestCase):
    def _import(self):
        import notification_center as nc
        return nc

    def test_enabled_by_default(self):
        nc = self._import()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NOTIFICATION_CENTER_ENABLED", None)
            self.assertTrue(nc.notification_center_enabled())

    def test_disabled_via_env_false(self):
        nc = self._import()
        with patch.dict(os.environ, {"NOTIFICATION_CENTER_ENABLED": "false"}):
            self.assertFalse(nc.notification_center_enabled())

    def test_disabled_via_env_zero(self):
        nc = self._import()
        with patch.dict(os.environ, {"NOTIFICATION_CENTER_ENABLED": "0"}):
            self.assertFalse(nc.notification_center_enabled())

    def test_enabled_via_env_true(self):
        nc = self._import()
        with patch.dict(os.environ, {"NOTIFICATION_CENTER_ENABLED": "true"}):
            self.assertTrue(nc.notification_center_enabled())

    def test_enabled_via_env_one(self):
        nc = self._import()
        with patch.dict(os.environ, {"NOTIFICATION_CENTER_ENABLED": "1"}):
            self.assertTrue(nc.notification_center_enabled())

    def test_unknown_falls_back_to_default_true(self):
        nc = self._import()
        with patch.dict(os.environ, {"NOTIFICATION_CENTER_ENABLED": "maybe"}):
            self.assertTrue(nc.notification_center_enabled())


# ── Deterministic ID ──────────────────────────────────────────────────────────

class TestMakeNotificationId(unittest.TestCase):
    def _nc(self):
        import notification_center as nc
        return nc

    def test_same_inputs_same_id(self):
        nc = self._nc()
        a = nc._make_notification_id("src", "SYSTEM", "ticker", "AAPL")
        b = nc._make_notification_id("src", "SYSTEM", "ticker", "AAPL")
        self.assertEqual(a, b)

    def test_different_inputs_different_id(self):
        nc = self._nc()
        a = nc._make_notification_id("src", "SYSTEM", "ticker", "AAPL")
        b = nc._make_notification_id("src", "SYSTEM", "ticker", "MSFT")
        self.assertNotEqual(a, b)

    def test_id_is_16_chars(self):
        nc = self._nc()
        nid = nc._make_notification_id("src", "PORTFOLIO", "holding", "NVDA")
        self.assertEqual(len(nid), 16)

    def test_id_is_hex(self):
        import re
        nc = self._nc()
        nid = nc._make_notification_id("src", "MARKET", "index", "^GSPC")
        self.assertRegex(nid, r"^[0-9a-f]{16}$")


# ── Upsert and Get ────────────────────────────────────────────────────────────

class TestUpsertAndGet(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        import notification_center as nc
        self.nc = nc

    def test_upsert_creates_record(self):
        with _patch_db(self.conn_fn):
            nid = self.nc._make_notification_id("test", "SYSTEM", "e", "1")
            result = self.nc._upsert_notification(
                notification_id=nid,
                category="SYSTEM",
                severity="INFO",
                title="Hello",
                body="World",
                entity_type="e",
                entity_id="1",
                source="test",
            )
        self.assertEqual(result["notification_id"], nid)
        self.assertEqual(result["status"], "UNREAD")
        self.assertEqual(result["title"], "Hello")

    def test_upsert_idempotent_preserves_status(self):
        with _patch_db(self.conn_fn):
            nid = self.nc._make_notification_id("test", "SYSTEM", "e", "2")
            self.nc._upsert_notification(
                notification_id=nid, category="SYSTEM", severity="INFO",
                title="First", body="body", entity_type="e", entity_id="2", source="test",
            )
            self.nc.mark_read(nid)
            # second upsert should preserve READ status
            self.nc._upsert_notification(
                notification_id=nid, category="SYSTEM", severity="INFO",
                title="Updated", body="body2", entity_type="e", entity_id="2", source="test",
            )
            record = self.nc.get_notification(nid)
        self.assertEqual(record["status"], "READ")
        self.assertEqual(record["title"], "Updated")

    def test_get_notification_returns_none_for_unknown(self):
        with _patch_db(self.conn_fn):
            result = self.nc.get_notification("nonexistent1234")
        self.assertIsNone(result)

    def test_metadata_round_trips(self):
        with _patch_db(self.conn_fn):
            nid = self.nc._make_notification_id("test", "RISK", "e", "3")
            self.nc._upsert_notification(
                notification_id=nid, category="RISK", severity="WARNING",
                title="Risk", body="body", entity_type="e", entity_id="3",
                source="test", metadata={"score": 42, "tier": "HIGH"},
            )
            record = self.nc.get_notification(nid)
        self.assertEqual(record["metadata"]["score"], 42)
        self.assertEqual(record["metadata"]["tier"], "HIGH")


# ── get_notifications filters ─────────────────────────────────────────────────

class TestGetNotificationsFilters(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        import notification_center as nc
        self.nc = nc

    def _seed(self, suffix, category, severity, status="UNREAD"):
        nid = f"testid{suffix:04d}00"
        with _patch_db(self.conn_fn):
            self.nc._upsert_notification(
                notification_id=nid, category=category, severity=severity,
                title=f"T{suffix}", body="b", entity_type="e",
                entity_id=str(suffix), source="test",
            )
            if status != "UNREAD":
                self.nc._set_status(nid, status)
        return nid

    def test_no_filter_returns_all(self):
        for i in range(3):
            self._seed(i, "SYSTEM", "INFO")
        with _patch_db(self.conn_fn):
            items = self.nc.get_notifications()
        self.assertGreaterEqual(len(items), 3)

    def test_status_filter(self):
        nid = self._seed(10, "SYSTEM", "INFO")
        with _patch_db(self.conn_fn):
            self.nc.mark_read(nid)
            unread = self.nc.get_notifications(status="UNREAD")
            read = self.nc.get_notifications(status="READ")
        self.assertNotIn(nid, [n["notification_id"] for n in unread])
        self.assertIn(nid, [n["notification_id"] for n in read])

    def test_category_filter(self):
        self._seed(20, "RISK", "WARNING")
        with _patch_db(self.conn_fn):
            risk = self.nc.get_notifications(category="RISK")
        for n in risk:
            self.assertEqual(n["category"], "RISK")

    def test_severity_filter(self):
        self._seed(30, "MARKET", "CRITICAL")
        with _patch_db(self.conn_fn):
            crits = self.nc.get_notifications(severity="CRITICAL")
        for n in crits:
            self.assertEqual(n["severity"], "CRITICAL")

    def test_limit_respected(self):
        for i in range(100, 110):
            self._seed(i, "SYSTEM", "INFO")
        with _patch_db(self.conn_fn):
            items = self.nc.get_notifications(limit=3)
        self.assertLessEqual(len(items), 3)

    def test_offset_paginates(self):
        for i in range(200, 210):
            self._seed(i, "PERFORMANCE", "INFO")
        with _patch_db(self.conn_fn):
            page1 = self.nc.get_notifications(category="PERFORMANCE", limit=5, offset=0)
            page2 = self.nc.get_notifications(category="PERFORMANCE", limit=5, offset=5)
        ids1 = {n["notification_id"] for n in page1}
        ids2 = {n["notification_id"] for n in page2}
        self.assertTrue(ids1.isdisjoint(ids2))


# ── User actions ──────────────────────────────────────────────────────────────

class TestUserActions(unittest.TestCase):
    def setUp(self):
        self.path, self.conn_fn = _make_db()
        _seed_notification(self.conn_fn)
        import notification_center as nc
        self.nc = nc

    def test_mark_read(self):
        with _patch_db(self.conn_fn):
            result = self.nc.mark_read("abc123")
        self.assertEqual(result["status"], "READ")

    def test_mark_unread(self):
        with _patch_db(self.conn_fn):
            self.nc.mark_read("abc123")
            result = self.nc.mark_unread("abc123")
        self.assertEqual(result["status"], "UNREAD")

    def test_dismiss(self):
        with _patch_db(self.conn_fn):
            result = self.nc.dismiss("abc123")
        self.assertEqual(result["status"], "DISMISSED")

    def test_archive(self):
        with _patch_db(self.conn_fn):
            result = self.nc.archive("abc123")
        self.assertEqual(result["status"], "ARCHIVED")

    def test_set_status_raises_for_unknown(self):
        with _patch_db(self.conn_fn):
            with self.assertRaises(ValueError):
                self.nc._set_status("does_not_exist", "READ")

    def test_mark_all_read_returns_count(self):
        _seed_notification(self.conn_fn, "nid2", status="UNREAD")
        _seed_notification(self.conn_fn, "nid3", status="UNREAD")
        with _patch_db(self.conn_fn):
            count = self.nc.mark_all_read()
        self.assertGreaterEqual(count, 1)

    def test_mark_all_read_changes_status(self):
        with _patch_db(self.conn_fn):
            self.nc.mark_all_read()
            items = self.nc.get_notifications(status="UNREAD")
        self.assertEqual(len(items), 0)

    def test_archive_read_returns_count(self):
        with _patch_db(self.conn_fn):
            self.nc.mark_all_read()
            count = self.nc.archive_read()
        self.assertGreaterEqual(count, 1)

    def test_archive_read_moves_only_read(self):
        _seed_notification(self.conn_fn, "unr1", status="UNREAD")
        with _patch_db(self.conn_fn):
            self.nc.mark_read("abc123")
            self.nc.archive_read()
            unread = self.nc.get_notifications(status="UNREAD")
        ids = [n["notification_id"] for n in unread]
        self.assertNotIn("abc123", ids)


# ── Banned-word sanitization ──────────────────────────────────────────────────

class TestBannedWordSanitization(unittest.TestCase):
    def setUp(self):
        import notification_center as nc
        self.nc = nc

    def test_explosion_redacted(self):
        body = "This is a PRE-EXPLOSION alert for AAPL"
        cleaned = self.nc._sanitize_body(body)
        self.assertNotIn("explosion", cleaned.lower())

    def test_moon_redacted(self):
        body = "AAPL to the moon!"
        cleaned = self.nc._sanitize_body(body)
        self.assertNotIn("moon", cleaned.lower())

    def test_clean_body_unchanged(self):
        body = "AAPL down 3.1% today. Review your position."
        cleaned = self.nc._sanitize_body(body)
        self.assertEqual(body, cleaned)


# ── generate_notifications ────────────────────────────────────────────────────

class TestGenerateNotifications(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        import notification_center as nc
        self.nc = nc

    def test_returns_dict_with_generated_key(self):
        with _patch_db(self.conn_fn):
            result = self.nc.generate_notifications()
        self.assertIn("generated", result)
        self.assertIn("generated_at", result)

    def test_skips_when_flag_disabled(self):
        with patch.dict(os.environ, {"NOTIFICATION_CENTER_ENABLED": "false"}):
            with _patch_db(self.conn_fn):
                result = self.nc.generate_notifications()
        self.assertTrue(result.get("skipped"))

    def test_generated_count_is_integer(self):
        with _patch_db(self.conn_fn):
            result = self.nc.generate_notifications()
        self.assertIsInstance(result["generated"], int)

    def test_no_whatsapp_calls(self):
        mock_sms = MagicMock()
        with patch("alerts.send_sms", mock_sms):
            with _patch_db(self.conn_fn):
                self.nc.generate_notifications()
        mock_sms.assert_not_called()

    def test_generator_failure_does_not_crash_generation(self):
        def _bad_gen():
            raise RuntimeError("explode")

        original = list(self.nc._GENERATORS)
        self.nc._GENERATORS.insert(0, _bad_gen)
        try:
            with _patch_db(self.conn_fn):
                result = self.nc.generate_notifications()
        finally:
            self.nc._GENERATORS[:] = original
        self.assertIn("errors", result)
        self.assertGreaterEqual(result["errors"], 1)

    def test_deterministic_ids_no_duplicates(self):
        mock_gen = MagicMock(return_value=[{
            "notification_id": "fixed_id_1234",
            "category": "SYSTEM",
            "severity": "INFO",
            "title": "Test",
            "body": "body",
            "entity_type": "e",
            "entity_id": "1",
            "source": "test_gen",
        }])
        original = list(self.nc._GENERATORS)
        self.nc._GENERATORS[:] = [mock_gen]
        try:
            with _patch_db(self.conn_fn):
                r1 = self.nc.generate_notifications()
                r2 = self.nc.generate_notifications()
        finally:
            self.nc._GENERATORS[:] = original
        self.assertEqual(r1["generated"], 1)
        self.assertEqual(r2["generated"], 1)

    def test_no_trade_execution(self):
        import portfolio as p_mod
        buy_mock = MagicMock()
        sell_mock = MagicMock()
        with patch.object(p_mod, "buy_holding", buy_mock, create=True):
            with patch.object(p_mod, "sell_holding", sell_mock, create=True):
                with _patch_db(self.conn_fn):
                    self.nc.generate_notifications()
        buy_mock.assert_not_called()
        sell_mock.assert_not_called()


# ── get_summary ───────────────────────────────────────────────────────────────

class TestGetSummary(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        import notification_center as nc
        self.nc = nc

    def _seed(self, nid, category, severity, status="UNREAD"):
        with _patch_db(self.conn_fn):
            self.nc._upsert_notification(
                notification_id=nid, category=category, severity=severity,
                title="T", body="b", entity_type="e", entity_id=nid, source="test",
            )
            if status != "UNREAD":
                self.nc._set_status(nid, status)

    def test_summary_has_required_keys(self):
        with _patch_db(self.conn_fn):
            s = self.nc.get_summary()
        for key in ("unread_count", "critical_count", "warning_count",
                    "by_category", "by_severity", "top_notifications",
                    "stale_notification_count", "generated_at"):
            self.assertIn(key, s)

    def test_unread_count_correct(self):
        self._seed("sum01", "SYSTEM", "INFO", "UNREAD")
        self._seed("sum02", "RISK", "WARNING", "READ")
        with _patch_db(self.conn_fn):
            s = self.nc.get_summary()
        self.assertGreaterEqual(s["unread_count"], 1)

    def test_critical_count(self):
        self._seed("crit01", "MARKET", "CRITICAL", "UNREAD")
        with _patch_db(self.conn_fn):
            s = self.nc.get_summary()
        self.assertGreaterEqual(s["critical_count"], 1)

    def test_warning_count(self):
        self._seed("warn01", "RISK", "WARNING", "UNREAD")
        with _patch_db(self.conn_fn):
            s = self.nc.get_summary()
        self.assertGreaterEqual(s["warning_count"], 1)

    def test_by_category_is_dict(self):
        self._seed("catd01", "PORTFOLIO", "INFO", "UNREAD")
        with _patch_db(self.conn_fn):
            s = self.nc.get_summary()
        self.assertIsInstance(s["by_category"], dict)

    def test_by_severity_is_dict(self):
        with _patch_db(self.conn_fn):
            s = self.nc.get_summary()
        self.assertIsInstance(s["by_severity"], dict)

    def test_top_notifications_is_list(self):
        with _patch_db(self.conn_fn):
            s = self.nc.get_summary()
        self.assertIsInstance(s["top_notifications"], list)

    def test_archived_not_in_unread_count(self):
        self._seed("arch01", "SYSTEM", "INFO", "ARCHIVED")
        with _patch_db(self.conn_fn):
            s = self.nc.get_summary()
        for n in s["top_notifications"]:
            self.assertNotEqual(n["notification_id"], "arch01")

    def test_dismissed_not_in_summary(self):
        self._seed("dism01", "SYSTEM", "INFO", "DISMISSED")
        with _patch_db(self.conn_fn):
            s = self.nc.get_summary()
        all_top_ids = [n["notification_id"] for n in s["top_notifications"]]
        self.assertNotIn("dism01", all_top_ids)


# ── Sparse-data safety ────────────────────────────────────────────────────────

class TestSparseDataSafe(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        import notification_center as nc
        self.nc = nc

    def test_portfolio_gen_fails_gracefully(self):
        with _patch_db(self.conn_fn):
            with patch("portfolio.get_holdings", side_effect=Exception("no db")):
                result = self.nc._gen_portfolio_value_change()
        self.assertEqual(result, [])

    def test_sell_signal_gen_fails_gracefully(self):
        with _patch_db(self.conn_fn):
            result = self.nc._gen_sell_signal_active()
        self.assertIsInstance(result, list)

    def test_alpha_top_gen_fails_gracefully(self):
        with _patch_db(self.conn_fn):
            result = self.nc._gen_alpha_top_opportunity()
        self.assertIsInstance(result, list)

    def test_regime_gen_fails_gracefully(self):
        with _patch_db(self.conn_fn):
            result = self.nc._gen_regime_change()
        self.assertIsInstance(result, list)

    def test_risk_guardrail_gen_fails_gracefully(self):
        with _patch_db(self.conn_fn):
            result = self.nc._gen_risk_guardrail_breach()
        self.assertIsInstance(result, list)

    def test_catalyst_gen_fails_gracefully(self):
        with _patch_db(self.conn_fn):
            result = self.nc._gen_upcoming_catalyst()
        self.assertIsInstance(result, list)

    def test_stress_test_gen_fails_gracefully(self):
        with _patch_db(self.conn_fn):
            result = self.nc._gen_stress_test_severe()
        self.assertIsInstance(result, list)

    def test_market_index_gen_fails_gracefully(self):
        with _patch_db(self.conn_fn):
            result = self.nc._gen_market_index_drop()
        self.assertIsInstance(result, list)

    def test_weekly_grade_gen_fails_gracefully(self):
        with _patch_db(self.conn_fn):
            result = self.nc._gen_weekly_grade_degraded()
        self.assertIsInstance(result, list)


# ── API: GET /notifications ───────────────────────────────────────────────────

class TestApiNotificationsList(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()

    def test_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications")
        self.assertEqual(resp.status_code, 200)

    def test_returns_ok_true(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications")
        data = resp.get_json()
        self.assertTrue(data["ok"])

    def test_data_has_notifications_key(self):
        # N5 updated response to wrap notifications in a dict
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications")
        data = resp.get_json()
        self.assertIn("notifications", data["data"])
        self.assertIsInstance(data["data"]["notifications"], list)

    def test_bad_limit_returns_400(self):
        resp = self.client.get("/api/v1/notifications?limit=abc")
        self.assertEqual(resp.status_code, 400)

    def test_filter_params_accepted(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications?status=UNREAD&category=RISK&severity=WARNING")
        self.assertEqual(resp.status_code, 200)


# ── API: GET /notifications/summary ──────────────────────────────────────────

class TestApiNotificationsSummary(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()

    def test_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/summary")
        self.assertEqual(resp.status_code, 200)

    def test_has_unread_count(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/summary")
        data = resp.get_json()
        self.assertIn("unread_count", data["data"])

    def test_has_by_category(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/summary")
        data = resp.get_json()
        self.assertIn("by_category", data["data"])


# ── API: GET /notifications/<id> ──────────────────────────────────────────────

class TestApiNotificationGet(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()

    def test_unknown_id_returns_404(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/doesnotexist")
        self.assertEqual(resp.status_code, 404)

    def test_known_id_returns_200(self):
        _seed_notification(self.conn_fn, "known001")
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/known001")
        self.assertEqual(resp.status_code, 200)

    def test_known_id_has_notification_id_field(self):
        _seed_notification(self.conn_fn, "known002")
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/known002")
        data = resp.get_json()
        self.assertEqual(data["data"]["notification_id"], "known002")


# ── API: POST /notifications/generate ────────────────────────────────────────

class TestApiNotificationsGenerate(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()
        os.environ["API_SECRET"] = "test-secret-n4"

    def tearDown(self):
        os.environ.pop("API_SECRET", None)

    def test_no_auth_returns_401(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post("/api/v1/notifications/generate")
        self.assertEqual(resp.status_code, 401)

    def test_with_auth_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/generate",
                headers={"Authorization": "Bearer test-secret-n4"},
            )
        self.assertEqual(resp.status_code, 200)

    def test_result_has_generated_key(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/generate",
                headers={"Authorization": "Bearer test-secret-n4"},
            )
        data = resp.get_json()
        self.assertIn("generated", data["data"])

    def test_no_whatsapp_sent(self):
        mock_sms = MagicMock(return_value=True)
        with patch("alerts.send_sms", mock_sms):
            with _patch_db(self.conn_fn):
                self.client.post(
                    "/api/v1/notifications/generate",
                    headers={"Authorization": "Bearer test-secret-n4"},
                )
        mock_sms.assert_not_called()


# ── API: POST /notifications/<id>/read ───────────────────────────────────────

class TestApiNotificationRead(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()
        os.environ["API_SECRET"] = "test-secret-n4"
        _seed_notification(self.conn_fn, "rn001")

    def tearDown(self):
        os.environ.pop("API_SECRET", None)

    def test_no_auth_returns_401(self):
        resp = self.client.post("/api/v1/notifications/rn001/read")
        self.assertEqual(resp.status_code, 401)

    def test_mark_read_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/rn001/read",
                headers={"Authorization": "Bearer test-secret-n4"},
            )
        self.assertEqual(resp.status_code, 200)

    def test_mark_read_status_is_read(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/rn001/read",
                headers={"Authorization": "Bearer test-secret-n4"},
            )
        data = resp.get_json()
        self.assertEqual(data["data"]["status"], "READ")

    def test_unknown_id_returns_404(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/nosuchnotif/read",
                headers={"Authorization": "Bearer test-secret-n4"},
            )
        self.assertEqual(resp.status_code, 404)


# ── API: POST /notifications/<id>/unread ─────────────────────────────────────

class TestApiNotificationUnread(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()
        os.environ["API_SECRET"] = "test-secret-n4"
        _seed_notification(self.conn_fn, "unr001")

    def tearDown(self):
        os.environ.pop("API_SECRET", None)

    def test_no_auth_returns_401(self):
        resp = self.client.post("/api/v1/notifications/unr001/unread")
        self.assertEqual(resp.status_code, 401)

    def test_mark_unread_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/unr001/unread",
                headers={"Authorization": "Bearer test-secret-n4"},
            )
        self.assertEqual(resp.status_code, 200)

    def test_status_is_unread(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/unr001/unread",
                headers={"Authorization": "Bearer test-secret-n4"},
            )
        data = resp.get_json()
        self.assertEqual(data["data"]["status"], "UNREAD")


# ── API: POST /notifications/<id>/dismiss ────────────────────────────────────

class TestApiNotificationDismiss(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()
        os.environ["API_SECRET"] = "test-secret-n4"
        _seed_notification(self.conn_fn, "dism001")

    def tearDown(self):
        os.environ.pop("API_SECRET", None)

    def test_dismiss_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/dism001/dismiss",
                headers={"Authorization": "Bearer test-secret-n4"},
            )
        self.assertEqual(resp.status_code, 200)

    def test_status_is_dismissed(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/dism001/dismiss",
                headers={"Authorization": "Bearer test-secret-n4"},
            )
        data = resp.get_json()
        self.assertEqual(data["data"]["status"], "DISMISSED")

    def test_unknown_returns_404(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/badid/dismiss",
                headers={"Authorization": "Bearer test-secret-n4"},
            )
        self.assertEqual(resp.status_code, 404)


# ── API: POST /notifications/<id>/archive ────────────────────────────────────

class TestApiNotificationArchive(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()
        os.environ["API_SECRET"] = "test-secret-n4"
        _seed_notification(self.conn_fn, "arch001")

    def tearDown(self):
        os.environ.pop("API_SECRET", None)

    def test_archive_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/arch001/archive",
                headers={"Authorization": "Bearer test-secret-n4"},
            )
        self.assertEqual(resp.status_code, 200)

    def test_status_is_archived(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/arch001/archive",
                headers={"Authorization": "Bearer test-secret-n4"},
            )
        data = resp.get_json()
        self.assertEqual(data["data"]["status"], "ARCHIVED")


# ── API: POST /notifications/mark-all-read ───────────────────────────────────

class TestApiMarkAllRead(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()
        os.environ["API_SECRET"] = "test-secret-n4"
        for i in range(3):
            _seed_notification(self.conn_fn, f"mar{i:03d}", status="UNREAD")

    def tearDown(self):
        os.environ.pop("API_SECRET", None)

    def test_no_auth_returns_401(self):
        resp = self.client.post("/api/v1/notifications/mark-all-read")
        self.assertEqual(resp.status_code, 401)

    def test_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/mark-all-read",
                headers={"Authorization": "Bearer test-secret-n4"},
            )
        self.assertEqual(resp.status_code, 200)

    def test_returns_marked_read_count(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/mark-all-read",
                headers={"Authorization": "Bearer test-secret-n4"},
            )
        data = resp.get_json()
        self.assertIn("marked_read", data["data"])
        self.assertIsInstance(data["data"]["marked_read"], int)


# ── API: POST /notifications/archive-read ────────────────────────────────────

class TestApiArchiveRead(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()
        os.environ["API_SECRET"] = "test-secret-n4"
        _seed_notification(self.conn_fn, "arc_r001", status="READ")

    def tearDown(self):
        os.environ.pop("API_SECRET", None)

    def test_no_auth_returns_401(self):
        resp = self.client.post("/api/v1/notifications/archive-read")
        self.assertEqual(resp.status_code, 401)

    def test_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/archive-read",
                headers={"Authorization": "Bearer test-secret-n4"},
            )
        self.assertEqual(resp.status_code, 200)

    def test_returns_archived_count(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/archive-read",
                headers={"Authorization": "Bearer test-secret-n4"},
            )
        data = resp.get_json()
        self.assertIn("archived", data["data"])
        self.assertGreaterEqual(data["data"]["archived"], 1)


# ── Scheduler ─────────────────────────────────────────────────────────────────

class TestSchedulerJob(unittest.TestCase):
    def _scheduler(self):
        import scheduler as sch
        return sch

    def test_run_notification_center_skips_when_disabled(self):
        sch = self._scheduler()
        with patch.dict(os.environ, {"NOTIFICATION_CENTER_ENABLED": "false"}):
            sch._run_notification_center()  # should not raise

    def test_run_notification_center_calls_generate(self):
        sch = self._scheduler()
        mock_gen = MagicMock(return_value={"generated": 0, "errors": 0, "generated_at": "x", "skipped": False})
        with patch.dict(os.environ, {"NOTIFICATION_CENTER_ENABLED": "true"}):
            with patch("notification_center.generate_notifications", mock_gen):
                sch._run_notification_center()
        mock_gen.assert_called_once()

    def test_run_notification_center_non_fatal(self):
        sch = self._scheduler()
        with patch.dict(os.environ, {"NOTIFICATION_CENTER_ENABLED": "true"}):
            with patch("notification_center.generate_notifications", side_effect=Exception("boom")):
                sch._run_notification_center()  # must not raise

    def test_scheduler_has_morning_job(self):
        sch = self._scheduler()
        import inspect
        src = inspect.getsource(sch.start_scheduler)
        self.assertIn("notification_center_morning", src)

    def test_scheduler_has_eod_job(self):
        sch = self._scheduler()
        import inspect
        src = inspect.getsource(sch.start_scheduler)
        self.assertIn("notification_center_eod", src)


# ── Database migration v27 ────────────────────────────────────────────────────

class TestMigrationV27(unittest.TestCase):
    def test_migration_v27_in_list(self):
        import database
        versions = [m.version for m in database.MIGRATIONS]
        self.assertIn(27, versions)

    def test_migration_v27_creates_table(self):
        import database
        m = next(m for m in database.MIGRATIONS if m.version == 27)
        sql_block = " ".join(m.sql)
        self.assertIn("notification_center", sql_block)

    def test_migration_v27_has_indexes(self):
        import database
        m = next(m for m in database.MIGRATIONS if m.version == 27)
        index_sql = " ".join(m.sql)
        self.assertIn("idx_nc_status", index_sql)
        self.assertIn("idx_nc_category", index_sql)

    def test_migration_runs_on_fresh_db(self):
        import database
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        old_path = database.DB_PATH
        database.DB_PATH = tmp.name
        try:
            database.init_db()
            database.run_migrations()
            conn = database.get_connection()
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            conn.close()
            self.assertIn("notification_center", tables)
        finally:
            database.DB_PATH = old_path
            os.unlink(tmp.name)


# ── No push notifications ─────────────────────────────────────────────────────

class TestNoPushNotifications(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        import notification_center as nc
        self.nc = nc

    def test_generate_does_not_call_send_sms(self):
        mock_sms = MagicMock(return_value=True)
        with patch("alerts.send_sms", mock_sms):
            with _patch_db(self.conn_fn):
                self.nc.generate_notifications()
        mock_sms.assert_not_called()

    def test_no_twilio_import_in_notification_center(self):
        import notification_center as nc
        import inspect
        src = inspect.getsource(nc)
        self.assertNotIn("from twilio", src)
        self.assertNotIn("import twilio", src)

    def test_no_send_sms_call_in_notification_center(self):
        import notification_center as nc
        import inspect
        src = inspect.getsource(nc)
        self.assertNotIn("send_sms(", src)


if __name__ == "__main__":
    unittest.main()
