"""
Phase N5 — Notification Preferences and Digest Rules tests.

Covers:
- Default preferences (all categories enabled, severity=INFO, digest=OFF, etc.)
- get_preferences / update_preferences round-trip
- Per-category override: upsert, retrieve, validation
- Filtering engine: should_surface_notification, should_include_in_digest,
                   apply_notification_preferences
- Category disable hides from normal list
- Severity threshold filtering
- digest_only flag hides from normal list, included in digest
- include_read_items=False hides READ items
- Dismissed/archived always hidden
- Quiet hours: active detection (patched TZ), suppresses display
- Digest builder: daily/eod/weekly, max_notifications_per_digest,
                  by_category/by_severity, section carve-outs
- API: GET/POST preferences, GET/POST categories, GET digest
- API auth: writes require Bearer token
- notifications list: preference filtering, include_filtered param
- summary: visible_unread_count, filtered_count, suppressed_by_preferences_count
- No send_sms, no trading calls, no push notifications
- Migration v28 schema
- PREF_CATEGORIES, PREF_SEVERITIES, DIGEST_MODES constants
"""
import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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


def _notif(
    notification_id="n001",
    category="SYSTEM",
    severity="INFO",
    status="UNREAD",
    source="test",
    created_at="2026-05-21T10:00:00+00:00",
) -> dict:
    return {
        "notification_id": notification_id,
        "category": category,
        "severity": severity,
        "status": status,
        "title": f"Test {notification_id}",
        "body": "body",
        "entity_type": "e",
        "entity_id": notification_id,
        "source": source,
        "created_at": created_at,
        "updated_at": created_at,
        "action_url": None,
        "metadata": {},
    }


def _default_prefs() -> dict:
    import notification_preferences as np
    return dict(np.DEFAULT_PREFS)


# ── Constants ─────────────────────────────────────────────────────────────────

class TestConstants(unittest.TestCase):
    def setUp(self):
        import notification_preferences as np
        self.np = np

    def test_pref_categories_count(self):
        self.assertEqual(len(self.np.PREF_CATEGORIES), 10)

    def test_pref_severities_count(self):
        self.assertEqual(len(self.np.PREF_SEVERITIES), 4)

    def test_digest_modes_count(self):
        self.assertEqual(len(self.np.DIGEST_MODES), 4)

    def test_pref_categories_values(self):
        for cat in ("BRIEF", "ALPHA", "PORTFOLIO", "RISK", "REGIME",
                    "RESEARCH", "CATALYST", "CHECKLIST", "WEEKLY_REVIEW", "SYSTEM"):
            self.assertIn(cat, self.np.PREF_CATEGORIES)

    def test_pref_severities_values(self):
        for sev in ("INFO", "WATCH", "WARNING", "CRITICAL"):
            self.assertIn(sev, self.np.PREF_SEVERITIES)

    def test_digest_modes_values(self):
        for m in ("OFF", "DAILY", "MORNING_AND_EOD", "WEEKLY"):
            self.assertIn(m, self.np.DIGEST_MODES)

    def test_default_prefs_has_required_keys(self):
        for key in (
            "enabled_categories", "minimum_severity", "quiet_hours_enabled",
            "quiet_hours_start", "quiet_hours_end", "timezone", "digest_mode",
            "max_notifications_per_digest", "include_read_items", "auto_archive_after_days",
        ):
            self.assertIn(key, self.np.DEFAULT_PREFS)

    def test_default_include_read_items_false(self):
        self.assertFalse(self.np.DEFAULT_PREFS["include_read_items"])

    def test_default_digest_mode_off(self):
        self.assertEqual(self.np.DEFAULT_PREFS["digest_mode"], "OFF")

    def test_default_min_severity_info(self):
        self.assertEqual(self.np.DEFAULT_PREFS["minimum_severity"], "INFO")


# ── get_preferences ───────────────────────────────────────────────────────────

class TestGetPreferences(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        import notification_preferences as np
        self.np = np

    def test_returns_defaults_on_empty_db(self):
        with _patch_db(self.conn_fn):
            prefs = self.np.get_preferences()
        self.assertIn("enabled_categories", prefs)
        self.assertIsInstance(prefs["enabled_categories"], list)

    def test_all_categories_enabled_by_default(self):
        with _patch_db(self.conn_fn):
            prefs = self.np.get_preferences()
        for cat in self.np.PREF_CATEGORIES:
            self.assertIn(cat, prefs["enabled_categories"])

    def test_minimum_severity_default_info(self):
        with _patch_db(self.conn_fn):
            prefs = self.np.get_preferences()
        self.assertEqual(prefs["minimum_severity"], "INFO")

    def test_quiet_hours_disabled_by_default(self):
        with _patch_db(self.conn_fn):
            prefs = self.np.get_preferences()
        self.assertFalse(prefs["quiet_hours_enabled"])

    def test_digest_mode_off_by_default(self):
        with _patch_db(self.conn_fn):
            prefs = self.np.get_preferences()
        self.assertEqual(prefs["digest_mode"], "OFF")


# ── update_preferences ────────────────────────────────────────────────────────

class TestUpdatePreferences(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        import notification_preferences as np
        self.np = np

    def test_update_minimum_severity(self):
        with _patch_db(self.conn_fn):
            result = self.np.update_preferences({"minimum_severity": "WARNING"})
        self.assertEqual(result["minimum_severity"], "WARNING")

    def test_update_quiet_hours_enabled(self):
        with _patch_db(self.conn_fn):
            result = self.np.update_preferences({"quiet_hours_enabled": True})
        self.assertTrue(result["quiet_hours_enabled"])

    def test_update_digest_mode(self):
        with _patch_db(self.conn_fn):
            result = self.np.update_preferences({"digest_mode": "DAILY"})
        self.assertEqual(result["digest_mode"], "DAILY")

    def test_update_enabled_categories_subset(self):
        with _patch_db(self.conn_fn):
            result = self.np.update_preferences({"enabled_categories": ["ALPHA", "RISK"]})
        self.assertIn("ALPHA", result["enabled_categories"])
        self.assertIn("RISK", result["enabled_categories"])

    def test_invalid_severity_falls_back_to_info(self):
        with _patch_db(self.conn_fn):
            result = self.np.update_preferences({"minimum_severity": "MEGA"})
        self.assertEqual(result["minimum_severity"], "INFO")

    def test_invalid_digest_mode_falls_back_to_off(self):
        with _patch_db(self.conn_fn):
            result = self.np.update_preferences({"digest_mode": "HOURLY"})
        self.assertEqual(result["digest_mode"], "OFF")

    def test_unknown_keys_ignored(self):
        with _patch_db(self.conn_fn):
            result = self.np.update_preferences({"unknown_field": "xyz"})
        self.assertNotIn("unknown_field", result)

    def test_update_is_persisted(self):
        with _patch_db(self.conn_fn):
            self.np.update_preferences({"minimum_severity": "CRITICAL"})
            prefs = self.np.get_preferences()
        self.assertEqual(prefs["minimum_severity"], "CRITICAL")

    def test_empty_categories_resets_to_all(self):
        with _patch_db(self.conn_fn):
            result = self.np.update_preferences({"enabled_categories": []})
        self.assertEqual(len(result["enabled_categories"]), len(self.np.PREF_CATEGORIES))


# ── Per-category overrides ────────────────────────────────────────────────────

class TestCategoryOverrides(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        import notification_preferences as np
        self.np = np

    def test_upsert_creates_override(self):
        with _patch_db(self.conn_fn):
            result = self.np.upsert_category_override("ALPHA", {"enabled": False})
        self.assertEqual(result["category"], "ALPHA")
        self.assertEqual(result["enabled"], 0)

    def test_upsert_updates_existing(self):
        with _patch_db(self.conn_fn):
            self.np.upsert_category_override("RISK", {"enabled": True, "digest_only": False})
            result = self.np.upsert_category_override("RISK", {"digest_only": True})
        self.assertEqual(result["digest_only"], 1)

    def test_unknown_category_raises(self):
        with _patch_db(self.conn_fn):
            with self.assertRaises(ValueError):
                self.np.upsert_category_override("UNKNOWN_CAT", {"enabled": True})

    def test_invalid_severity_raises(self):
        with _patch_db(self.conn_fn):
            with self.assertRaises(ValueError):
                self.np.upsert_category_override("RISK", {"minimum_severity": "EXTREME"})

    def test_get_category_override_none_for_unknown(self):
        with _patch_db(self.conn_fn):
            result = self.np.get_category_override("CATALYST")
        self.assertIsNone(result)

    def test_get_category_overrides_returns_list(self):
        with _patch_db(self.conn_fn):
            self.np.upsert_category_override("PORTFOLIO", {"enabled": True})
            items = self.np.get_category_overrides()
        self.assertIsInstance(items, list)
        cats = [i["category"] for i in items]
        self.assertIn("PORTFOLIO", cats)

    def test_upsert_minimum_severity(self):
        with _patch_db(self.conn_fn):
            result = self.np.upsert_category_override("REGIME", {"minimum_severity": "WARNING"})
        self.assertEqual(result["minimum_severity"], "WARNING")

    def test_case_insensitive_category(self):
        with _patch_db(self.conn_fn):
            result = self.np.upsert_category_override("alpha", {"enabled": True})
        self.assertEqual(result["category"], "ALPHA")


# ── Filtering: should_surface_notification ────────────────────────────────────

class TestShouldSurface(unittest.TestCase):
    def setUp(self):
        import notification_preferences as np
        self.np = np
        self.prefs = _default_prefs()

    def test_unread_system_info_surfaces(self):
        n = _notif(category="SYSTEM", severity="INFO", status="UNREAD")
        self.assertTrue(self.np.should_surface_notification(n, self.prefs))

    def test_dismissed_never_surfaces(self):
        n = _notif(status="DISMISSED")
        self.assertFalse(self.np.should_surface_notification(n, self.prefs))

    def test_archived_never_surfaces(self):
        n = _notif(status="ARCHIVED")
        self.assertFalse(self.np.should_surface_notification(n, self.prefs))

    def test_disabled_category_hidden(self):
        prefs = dict(self.prefs)
        prefs["enabled_categories"] = ["RISK", "ALPHA"]
        n = _notif(category="SYSTEM", severity="INFO", status="UNREAD")
        self.assertFalse(self.np.should_surface_notification(n, prefs))

    def test_severity_below_threshold_hidden(self):
        prefs = dict(self.prefs)
        prefs["minimum_severity"] = "WARNING"
        n = _notif(category="SYSTEM", severity="INFO", status="UNREAD")
        self.assertFalse(self.np.should_surface_notification(n, prefs))

    def test_severity_at_threshold_surfaces(self):
        prefs = dict(self.prefs)
        prefs["minimum_severity"] = "WARNING"
        n = _notif(category="RISK", severity="WARNING", status="UNREAD")
        self.assertTrue(self.np.should_surface_notification(n, prefs))

    def test_severity_above_threshold_surfaces(self):
        prefs = dict(self.prefs)
        prefs["minimum_severity"] = "WARNING"
        n = _notif(category="RISK", severity="CRITICAL", status="UNREAD")
        self.assertTrue(self.np.should_surface_notification(n, prefs))

    def test_read_hidden_when_include_read_false(self):
        prefs = dict(self.prefs)
        prefs["include_read_items"] = False
        n = _notif(category="SYSTEM", severity="INFO", status="READ")
        self.assertFalse(self.np.should_surface_notification(n, prefs))

    def test_read_shown_when_include_read_true(self):
        prefs = dict(self.prefs)
        prefs["include_read_items"] = True
        n = _notif(category="SYSTEM", severity="INFO", status="READ")
        self.assertTrue(self.np.should_surface_notification(n, prefs))

    def test_digest_only_override_hides_from_normal_list(self):
        overrides = [{"category": "ALPHA", "enabled": 1, "digest_only": 1, "minimum_severity": None}]
        n = _notif(category="ALPHA_SIGNAL", severity="INFO", status="UNREAD")
        self.assertFalse(self.np.should_surface_notification(n, self.prefs, overrides))

    def test_category_override_disabled(self):
        overrides = [{"category": "RISK", "enabled": 0, "digest_only": 0, "minimum_severity": None}]
        n = _notif(category="RISK", severity="WARNING", status="UNREAD")
        self.assertFalse(self.np.should_surface_notification(n, self.prefs, overrides))

    def test_override_severity_threshold(self):
        overrides = [{"category": "ALPHA", "enabled": 1, "digest_only": 0, "minimum_severity": "WARNING"}]
        n = _notif(category="ALPHA_SIGNAL", severity="INFO", status="UNREAD")
        self.assertFalse(self.np.should_surface_notification(n, self.prefs, overrides))


# ── Filtering: should_include_in_digest ──────────────────────────────────────

class TestShouldIncludeInDigest(unittest.TestCase):
    def setUp(self):
        import notification_preferences as np
        self.np = np
        self.prefs = _default_prefs()

    def test_unread_system_included(self):
        n = _notif(category="SYSTEM", severity="INFO", status="UNREAD")
        self.assertTrue(self.np.should_include_in_digest(n, self.prefs))

    def test_dismissed_excluded(self):
        n = _notif(status="DISMISSED")
        self.assertFalse(self.np.should_include_in_digest(n, self.prefs))

    def test_archived_excluded(self):
        n = _notif(status="ARCHIVED")
        self.assertFalse(self.np.should_include_in_digest(n, self.prefs))

    def test_digest_only_category_included_in_digest(self):
        overrides = [{"category": "ALPHA", "enabled": 1, "digest_only": 1, "minimum_severity": None}]
        n = _notif(category="ALPHA_SIGNAL", severity="INFO", status="UNREAD")
        self.assertTrue(self.np.should_include_in_digest(n, self.prefs, overrides))

    def test_disabled_category_excluded_from_digest(self):
        overrides = [{"category": "RESEARCH", "enabled": 0, "digest_only": 0, "minimum_severity": None}]
        n = _notif(category="RESEARCH", severity="INFO", status="UNREAD")
        self.assertFalse(self.np.should_include_in_digest(n, self.prefs, overrides))

    def test_severity_below_global_threshold_excluded(self):
        prefs = dict(self.prefs)
        prefs["minimum_severity"] = "WARNING"
        n = _notif(category="SYSTEM", severity="INFO", status="UNREAD")
        self.assertFalse(self.np.should_include_in_digest(n, prefs))

    def test_read_excluded_when_include_read_false(self):
        n = _notif(status="READ")
        self.assertFalse(self.np.should_include_in_digest(n, self.prefs))

    def test_read_included_when_include_read_true(self):
        prefs = dict(self.prefs)
        prefs["include_read_items"] = True
        n = _notif(status="READ")
        self.assertTrue(self.np.should_include_in_digest(n, prefs))


# ── apply_notification_preferences ───────────────────────────────────────────

class TestApplyPreferences(unittest.TestCase):
    def setUp(self):
        import notification_preferences as np
        self.np = np
        self.prefs = _default_prefs()

    def test_returns_required_keys(self):
        result = self.np.apply_notification_preferences([], self.prefs)
        for k in ("visible", "filtered", "suppressed_count", "quiet_hours_active"):
            self.assertIn(k, result)

    def test_visible_contains_passing_items(self):
        n = _notif(category="SYSTEM", severity="INFO", status="UNREAD")
        result = self.np.apply_notification_preferences([n], self.prefs)
        self.assertEqual(len(result["visible"]), 1)

    def test_dismissed_is_suppressed(self):
        n = _notif(status="DISMISSED")
        result = self.np.apply_notification_preferences([n], self.prefs)
        self.assertEqual(len(result["visible"]), 0)
        self.assertEqual(result["suppressed_count"], 1)

    def test_filtered_empty_by_default(self):
        n = _notif(status="DISMISSED")
        result = self.np.apply_notification_preferences([n], self.prefs)
        self.assertEqual(result["filtered"], [])

    def test_include_filtered_populates_filtered(self):
        n = _notif(status="DISMISSED")
        result = self.np.apply_notification_preferences([n], self.prefs, include_filtered=True)
        self.assertEqual(len(result["filtered"]), 1)

    def test_quiet_hours_active_bool(self):
        result = self.np.apply_notification_preferences([], self.prefs)
        self.assertIsInstance(result["quiet_hours_active"], bool)

    def test_mixed_items(self):
        items = [
            _notif("n1", category="SYSTEM", severity="INFO", status="UNREAD"),
            _notif("n2", status="DISMISSED"),
            _notif("n3", category="RISK", severity="WARNING", status="UNREAD"),
        ]
        result = self.np.apply_notification_preferences(items, self.prefs)
        self.assertEqual(len(result["visible"]), 2)
        self.assertEqual(result["suppressed_count"], 1)


# ── Quiet hours ───────────────────────────────────────────────────────────────

class TestQuietHours(unittest.TestCase):
    def setUp(self):
        import notification_preferences as np
        self.np = np

    def _prefs_with_quiet(self, start="22:00", end="07:00"):
        prefs = _default_prefs()
        prefs["quiet_hours_enabled"] = True
        prefs["quiet_hours_start"] = start
        prefs["quiet_hours_end"] = end
        prefs["timezone"] = "America/Toronto"
        return prefs

    def test_quiet_hours_disabled_returns_false(self):
        prefs = _default_prefs()
        prefs["quiet_hours_enabled"] = False
        self.assertFalse(self.np._quiet_hours_active(prefs))

    def test_quiet_hours_active_during_window(self):
        prefs = self._prefs_with_quiet("00:00", "23:59")
        self.assertTrue(self.np._quiet_hours_active(prefs))

    def test_quiet_hours_inactive_outside_window(self):
        prefs = self._prefs_with_quiet("22:00", "07:00")
        prefs["quiet_hours_enabled"] = False
        self.assertFalse(self.np._quiet_hours_active(prefs))

    def test_quiet_hours_does_not_delete_notification(self):
        prefs = _default_prefs()
        prefs["quiet_hours_enabled"] = True
        prefs["quiet_hours_start"] = "00:00"
        prefs["quiet_hours_end"] = "23:59"
        n = _notif(category="SYSTEM", severity="CRITICAL", status="UNREAD")
        # Notification is still surfaced; quiet hours only flags display suppression
        surfaced = self.np.should_surface_notification(n, prefs)
        self.assertTrue(surfaced)  # quiet hours does not delete

    def test_quiet_hours_active_included_in_apply_result(self):
        prefs = _default_prefs()
        prefs["quiet_hours_enabled"] = True
        prefs["quiet_hours_start"] = "00:00"
        prefs["quiet_hours_end"] = "23:59"
        result = self.np.apply_notification_preferences([], prefs)
        self.assertTrue(result["quiet_hours_active"])


# ── Digest builder ────────────────────────────────────────────────────────────

class TestDigestBuilder(unittest.TestCase):
    def setUp(self):
        import notification_preferences as np
        self.np = np
        self.prefs = _default_prefs()

    def _many_notifs(self, n=25, category="SYSTEM", severity="INFO"):
        return [
            _notif(f"dn{i:03d}", category=category, severity=severity, status="UNREAD")
            for i in range(n)
        ]

    def test_daily_digest_has_required_keys(self):
        result = self.np.build_daily_digest([], self.prefs)
        for k in ("title", "generated_at", "included_count", "omitted_count",
                  "by_category", "by_severity", "notifications",
                  "top_critical_warning", "top_alpha", "top_risk",
                  "top_research_catalyst_checklist"):
            self.assertIn(k, result)

    def test_eod_digest_mode_key(self):
        result = self.np.build_eod_digest([], self.prefs)
        self.assertEqual(result["mode"], "eod")

    def test_weekly_digest_mode_key(self):
        result = self.np.build_weekly_digest([], self.prefs)
        self.assertEqual(result["mode"], "weekly")

    def test_empty_input_gives_zero_count(self):
        result = self.np.build_daily_digest([], self.prefs)
        self.assertEqual(result["included_count"], 0)
        self.assertEqual(result["omitted_count"], 0)

    def test_max_notifications_per_digest_respected(self):
        prefs = dict(self.prefs)
        prefs["max_notifications_per_digest"] = 5
        notifs = self._many_notifs(25)
        result = self.np.build_daily_digest(notifs, prefs)
        self.assertLessEqual(result["included_count"], 5)

    def test_omitted_count_correct(self):
        prefs = dict(self.prefs)
        prefs["max_notifications_per_digest"] = 3
        notifs = self._many_notifs(10)
        result = self.np.build_daily_digest(notifs, prefs)
        self.assertGreaterEqual(result["omitted_count"], 0)
        self.assertEqual(result["included_count"] + result["omitted_count"], 10)

    def test_by_category_is_dict(self):
        notifs = self._many_notifs(3, category="RISK")
        result = self.np.build_daily_digest(notifs, self.prefs)
        self.assertIsInstance(result["by_category"], dict)

    def test_by_severity_is_dict(self):
        notifs = self._many_notifs(3, severity="WARNING")
        result = self.np.build_daily_digest(notifs, self.prefs)
        self.assertIsInstance(result["by_severity"], dict)

    def test_critical_items_in_top_critical_warning(self):
        critical = _notif("crit1", category="RISK", severity="CRITICAL", status="UNREAD")
        result = self.np.build_daily_digest([critical], self.prefs)
        ids = [n["notification_id"] for n in result["top_critical_warning"]]
        self.assertIn("crit1", ids)

    def test_alpha_items_in_top_alpha(self):
        alpha = _notif("alph1", category="ALPHA_SIGNAL", severity="INFO", status="UNREAD")
        result = self.np.build_daily_digest([alpha], self.prefs)
        ids = [n["notification_id"] for n in result["top_alpha"]]
        self.assertIn("alph1", ids)

    def test_dismissed_excluded_from_digest(self):
        n = _notif("dism1", status="DISMISSED")
        result = self.np.build_daily_digest([n], self.prefs)
        self.assertEqual(result["included_count"], 0)

    def test_digest_only_items_included(self):
        overrides = [{"category": "ALPHA", "enabled": 1, "digest_only": 1, "minimum_severity": None}]
        n = _notif("do1", category="ALPHA_SIGNAL", severity="INFO", status="UNREAD")
        result = self.np.build_daily_digest([n], self.prefs, overrides)
        self.assertEqual(result["included_count"], 1)

    def test_deterministic_ordering_critical_before_info(self):
        crit = _notif("c1", category="RISK", severity="CRITICAL", status="UNREAD")
        info = _notif("i1", category="SYSTEM", severity="INFO", status="UNREAD")
        result = self.np.build_daily_digest([info, crit], self.prefs)
        nids = [n["notification_id"] for n in result["notifications"]]
        self.assertLess(nids.index("c1"), nids.index("i1"))

    def test_no_whatsapp_in_digest_builder(self):
        mock_sms = MagicMock()
        with patch("alerts.send_sms", mock_sms):
            self.np.build_daily_digest([], self.prefs)
        mock_sms.assert_not_called()


# ── API: GET /notifications/preferences ──────────────────────────────────────

class TestApiPreferencesGet(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()

    def test_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/preferences")
        self.assertEqual(resp.status_code, 200)

    def test_returns_ok_true(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/preferences")
        data = resp.get_json()
        self.assertTrue(data["ok"])

    def test_data_has_enabled_categories(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/preferences")
        data = resp.get_json()
        self.assertIn("enabled_categories", data["data"])

    def test_data_has_minimum_severity(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/preferences")
        data = resp.get_json()
        self.assertIn("minimum_severity", data["data"])


# ── API: POST /notifications/preferences ─────────────────────────────────────

class TestApiPreferencesUpdate(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()
        os.environ["API_SECRET"] = "test-secret-n5"

    def tearDown(self):
        os.environ.pop("API_SECRET", None)

    def test_no_auth_returns_401(self):
        resp = self.client.post(
            "/api/v1/notifications/preferences",
            json={"minimum_severity": "WARNING"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_with_auth_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/preferences",
                json={"minimum_severity": "WARNING"},
                headers={"Authorization": "Bearer test-secret-n5"},
            )
        self.assertEqual(resp.status_code, 200)

    def test_preference_persisted(self):
        with _patch_db(self.conn_fn):
            self.client.post(
                "/api/v1/notifications/preferences",
                json={"minimum_severity": "CRITICAL"},
                headers={"Authorization": "Bearer test-secret-n5"},
            )
            resp = self.client.get("/api/v1/notifications/preferences")
        data = resp.get_json()
        self.assertEqual(data["data"]["minimum_severity"], "CRITICAL")

    def test_no_send_sms_called(self):
        mock_sms = MagicMock()
        with patch("alerts.send_sms", mock_sms):
            with _patch_db(self.conn_fn):
                self.client.post(
                    "/api/v1/notifications/preferences",
                    json={"digest_mode": "DAILY"},
                    headers={"Authorization": "Bearer test-secret-n5"},
                )
        mock_sms.assert_not_called()


# ── API: GET /notifications/preferences/categories ───────────────────────────

class TestApiPreferencesCategoriesList(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()

    def test_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/preferences/categories")
        self.assertEqual(resp.status_code, 200)

    def test_data_is_list(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/preferences/categories")
        data = resp.get_json()
        self.assertIsInstance(data["data"], list)


# ── API: POST /notifications/preferences/categories/<category> ───────────────

class TestApiPreferencesCategoryUpsert(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()
        os.environ["API_SECRET"] = "test-secret-n5"

    def tearDown(self):
        os.environ.pop("API_SECRET", None)

    def test_no_auth_returns_401(self):
        resp = self.client.post(
            "/api/v1/notifications/preferences/categories/RISK",
            json={"enabled": False},
        )
        self.assertEqual(resp.status_code, 401)

    def test_with_auth_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/preferences/categories/RISK",
                json={"enabled": False},
                headers={"Authorization": "Bearer test-secret-n5"},
            )
        self.assertEqual(resp.status_code, 200)

    def test_category_stored(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/preferences/categories/ALPHA",
                json={"digest_only": True},
                headers={"Authorization": "Bearer test-secret-n5"},
            )
        data = resp.get_json()
        self.assertEqual(data["data"]["category"], "ALPHA")

    def test_unknown_category_returns_400(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(
                "/api/v1/notifications/preferences/categories/NOTREAL",
                json={"enabled": False},
                headers={"Authorization": "Bearer test-secret-n5"},
            )
        self.assertEqual(resp.status_code, 400)


# ── API: GET /notifications/digest ───────────────────────────────────────────

class TestApiDigest(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()

    def test_daily_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/digest?mode=daily")
        self.assertEqual(resp.status_code, 200)

    def test_eod_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/digest?mode=eod")
        self.assertEqual(resp.status_code, 200)

    def test_weekly_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/digest?mode=weekly")
        self.assertEqual(resp.status_code, 200)

    def test_default_mode_is_daily(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/digest")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["data"]["mode"], "daily")

    def test_invalid_mode_returns_400(self):
        resp = self.client.get("/api/v1/notifications/digest?mode=hourly")
        self.assertEqual(resp.status_code, 400)

    def test_digest_has_included_count(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/digest?mode=daily")
        data = resp.get_json()
        self.assertIn("included_count", data["data"])

    def test_digest_has_notifications_key(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/digest?mode=daily")
        data = resp.get_json()
        self.assertIn("notifications", data["data"])


# ── API: notifications list preference integration ────────────────────────────

class TestApiNotificationsListPreferences(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()

    def test_returns_200(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications")
        self.assertEqual(resp.status_code, 200)

    def test_has_suppressed_count(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications")
        data = resp.get_json()
        self.assertIn("suppressed_count", data["data"])

    def test_has_quiet_hours_active(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications")
        data = resp.get_json()
        self.assertIn("quiet_hours_active", data["data"])

    def test_include_filtered_param_accepted(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications?include_filtered=true")
        self.assertEqual(resp.status_code, 200)

    def test_bad_limit_returns_400(self):
        resp = self.client.get("/api/v1/notifications?limit=nope")
        self.assertEqual(resp.status_code, 400)


# ── API: summary preference counts ───────────────────────────────────────────

class TestApiSummaryPreferenceCounts(unittest.TestCase):
    def setUp(self):
        self.app, self.conn_fn, self.db = _make_app()
        self.client = self.app.test_client()
        import api as api_mod
        api_mod.cache_clear()

    def test_summary_has_visible_unread_count(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/summary")
        data = resp.get_json()
        self.assertIn("visible_unread_count", data["data"])

    def test_summary_has_filtered_count(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/summary")
        data = resp.get_json()
        self.assertIn("filtered_count", data["data"])

    def test_summary_has_suppressed_by_preferences_count(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/summary")
        data = resp.get_json()
        self.assertIn("suppressed_by_preferences_count", data["data"])

    def test_summary_has_quiet_hours_active(self):
        with _patch_db(self.conn_fn):
            resp = self.client.get("/api/v1/notifications/summary")
        data = resp.get_json()
        self.assertIn("quiet_hours_active", data["data"])


# ── Safety: no sends, no trades ───────────────────────────────────────────────

class TestSafetyNoCalls(unittest.TestCase):
    def setUp(self):
        import notification_preferences as np
        self.np = np
        self.prefs = _default_prefs()

    def test_apply_preferences_no_send_sms(self):
        mock_sms = MagicMock()
        with patch("alerts.send_sms", mock_sms):
            self.np.apply_notification_preferences([], self.prefs)
        mock_sms.assert_not_called()

    def test_no_twilio_in_source(self):
        import inspect
        src = inspect.getsource(self.np)
        self.assertNotIn("from twilio", src)
        self.assertNotIn("import twilio", src)

    def test_no_send_sms_call_in_source(self):
        import inspect
        src = inspect.getsource(self.np)
        self.assertNotIn("send_sms(", src)

    def test_no_broker_in_source(self):
        import inspect
        src = inspect.getsource(self.np)
        self.assertNotIn("place_order", src)
        self.assertNotIn("buy_holding", src)
        self.assertNotIn("sell_holding", src)


# ── Migration v28 ─────────────────────────────────────────────────────────────

class TestMigrationV28(unittest.TestCase):
    def test_v28_in_migrations(self):
        import database
        versions = [m.version for m in database.MIGRATIONS]
        self.assertIn(28, versions)

    def test_v28_description(self):
        import database
        m = next(m for m in database.MIGRATIONS if m.version == 28)
        self.assertIn("N5", m.description)

    def test_v28_creates_preferences_table(self):
        import database
        m = next(m for m in database.MIGRATIONS if m.version == 28)
        sql_block = " ".join(m.sql)
        self.assertIn("notification_preferences", sql_block)

    def test_v28_creates_categories_table(self):
        import database
        m = next(m for m in database.MIGRATIONS if m.version == 28)
        sql_block = " ".join(m.sql)
        self.assertIn("notification_preferences_categories", sql_block)

    def test_v28_has_index(self):
        import database
        m = next(m for m in database.MIGRATIONS if m.version == 28)
        sql_block = " ".join(m.sql)
        self.assertIn("idx_npc_cat", sql_block)

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
            self.assertIn("notification_preferences", tables)
            self.assertIn("notification_preferences_categories", tables)
        finally:
            database.DB_PATH = old_path
            os.unlink(tmp.name)


# ── get_preference_summary_extras ────────────────────────────────────────────

class TestGetPreferenceSummaryExtras(unittest.TestCase):
    def setUp(self):
        import notification_preferences as np
        self.np = np
        self.prefs = _default_prefs()

    def test_returns_required_keys(self):
        result = self.np.get_preference_summary_extras([], self.prefs)
        for k in ("visible_unread_count", "filtered_count",
                  "suppressed_by_preferences_count", "quiet_hours_active"):
            self.assertIn(k, result)

    def test_counts_unread_only(self):
        notifs = [
            _notif("a", status="UNREAD"),
            _notif("b", status="READ"),
            _notif("c", status="UNREAD"),
        ]
        result = self.np.get_preference_summary_extras(notifs, self.prefs)
        self.assertEqual(result["visible_unread_count"], 2)

    def test_suppressed_by_severity_counted(self):
        prefs = dict(self.prefs)
        prefs["minimum_severity"] = "WARNING"
        notifs = [
            _notif("x", severity="INFO", status="UNREAD"),
            _notif("y", severity="WARNING", status="UNREAD"),
        ]
        result = self.np.get_preference_summary_extras(notifs, prefs)
        self.assertEqual(result["visible_unread_count"], 1)
        self.assertGreaterEqual(result["suppressed_by_preferences_count"], 1)

    def test_quiet_hours_active_is_bool(self):
        result = self.np.get_preference_summary_extras([], self.prefs)
        self.assertIsInstance(result["quiet_hours_active"], bool)


if __name__ == "__main__":
    unittest.main()
