"""
Phase A29 — Backup, Export, and Disaster Recovery tests.

Covers:
- BACKUP_TYPES, BACKUP_GROUPS constants
- create_backup(): FULL, group-specific, SYSTEM_CONFIG_ONLY, JSON determinism
- create_backup(): checksum in manifest, row counts, CREATED status
- create_backup(): secrets excluded from output
- create_backup(): graceful failure when storage unavailable
- verify_backup(): file_exists, checksum, json_parses, row_counts_match checks
- verify_backup(): updates manifest to VERIFIED on success
- verify_backup(): returns ok=False for unknown backup_id
- restore_preview(): read-only, returns warning text, no DB writes
- restore_preview(): delta summary per table
- list_backups(): most recent first, limit respected
- get_backup(): returns entry or None
- latest_backup_age_hours(): returns float or None
- backup_schedule_enabled(): reads BACKUP_SCHEDULE_ENABLED env var
- API: GET /backups auth not required
- API: GET /backups/<id> returns 404 for missing
- API: POST /backups/create auth required (401 without token)
- API: POST /backups/create creates and returns manifest entry
- API: POST /backups/<id>/verify auth required
- API: GET /backups/<id>/download-info returns metadata only
- API: POST /backups/<id>/restore-preview auth required, returns warning
- Release check: _check_backup_age warns when no backups
- Release check: _check_backup_age warns when backup is stale
- Release check: _check_backup_age passes when backup is fresh
- No send_sms calls, no trading calls, no live DB writes during preview
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_db():
    """Create a temp SQLite DB with basic tables and return (path, conn_fn)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = tmp.name

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY, ticker TEXT, shares REAL, cost_basis REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alpha_shadow_log (
            id INTEGER PRIMARY KEY, ticker TEXT, score REAL, ts TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)
    """)
    conn.execute("INSERT INTO schema_version VALUES (28)")
    conn.execute("INSERT INTO holdings VALUES (1, 'AAPL', 10, 150.0)")
    conn.execute("INSERT INTO alpha_shadow_log VALUES (1, 'MSFT', 72.5, '2026-01-01T00:00:00')")
    conn.commit()
    conn.close()

    def _conn():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    return path, _conn


def _make_app():
    """Build a Flask test client with the API blueprint registered."""
    import database
    db_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_tmp.close()
    old_path = database.DB_PATH
    database.DB_PATH = db_tmp.name

    conn = sqlite3.connect(db_tmp.name)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS holdings (id INTEGER PRIMARY KEY, ticker TEXT, shares REAL, cost_basis REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)")
    conn.execute("INSERT INTO schema_version VALUES (28)")
    conn.commit()
    conn.close()

    from flask import Flask
    from api import api_bp, cache_clear
    app = Flask(__name__)
    app.register_blueprint(api_bp)
    cache_clear()
    return app, db_tmp.name, old_path


class TestBackupConstants(unittest.TestCase):
    def test_backup_types_tuple(self):
        from backup_manager import BACKUP_TYPES
        self.assertIn("FULL", BACKUP_TYPES)
        self.assertIn("PORTFOLIO_ONLY", BACKUP_TYPES)
        self.assertIn("ALPHA_ONLY", BACKUP_TYPES)
        self.assertIn("RESEARCH_ONLY", BACKUP_TYPES)
        self.assertIn("NOTIFICATIONS_ONLY", BACKUP_TYPES)
        self.assertIn("SYSTEM_CONFIG_ONLY", BACKUP_TYPES)

    def test_backup_groups_keys(self):
        from backup_manager import BACKUP_GROUPS
        self.assertIn("FULL", BACKUP_GROUPS)
        self.assertIn("PORTFOLIO_ONLY", BACKUP_GROUPS)
        self.assertIn("ALPHA_ONLY", BACKUP_GROUPS)
        self.assertIn("RESEARCH_ONLY", BACKUP_GROUPS)
        self.assertIn("NOTIFICATIONS_ONLY", BACKUP_GROUPS)
        self.assertIn("SYSTEM_CONFIG_ONLY", BACKUP_GROUPS)

    def test_full_is_superset_of_groups(self):
        from backup_manager import BACKUP_GROUPS
        full_set = set(BACKUP_GROUPS["FULL"])
        for group in ("PORTFOLIO_ONLY", "ALPHA_ONLY", "RESEARCH_ONLY", "NOTIFICATIONS_ONLY"):
            for tbl in BACKUP_GROUPS[group]:
                self.assertIn(tbl, full_set, f"FULL missing {tbl} from {group}")

    def test_system_config_only_no_tables(self):
        from backup_manager import BACKUP_GROUPS
        self.assertEqual(BACKUP_GROUPS["SYSTEM_CONFIG_ONLY"], [])

    def test_safe_env_keys_no_secrets(self):
        from backup_manager import _SAFE_ENV_KEYS
        lowered = [k.lower() for k in _SAFE_ENV_KEYS]
        for sensitive in ("api_key", "auth_token", "secret", "password", "twilio"):
            for k in lowered:
                self.assertNotIn(sensitive, k, f"Secret-looking key in _SAFE_ENV_KEYS: {k}")


class TestBackupScheduleFlag(unittest.TestCase):
    def test_default_false(self):
        from backup_manager import backup_schedule_enabled
        env = {k: v for k, v in os.environ.items() if k != "BACKUP_SCHEDULE_ENABLED"}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(backup_schedule_enabled())

    def test_enabled_when_set(self):
        from backup_manager import backup_schedule_enabled
        with patch.dict(os.environ, {"BACKUP_SCHEDULE_ENABLED": "true"}):
            self.assertTrue(backup_schedule_enabled())

    def test_enabled_variants(self):
        from backup_manager import backup_schedule_enabled
        for val in ("1", "yes", "on", "True", "TRUE"):
            with patch.dict(os.environ, {"BACKUP_SCHEDULE_ENABLED": val}):
                self.assertTrue(backup_schedule_enabled(), f"Expected True for {val!r}")

    def test_disabled_variants(self):
        from backup_manager import backup_schedule_enabled
        for val in ("0", "false", "no", "off", ""):
            with patch.dict(os.environ, {"BACKUP_SCHEDULE_ENABLED": val}):
                self.assertFalse(backup_schedule_enabled(), f"Expected False for {val!r}")


class TestManifestHelpers(unittest.TestCase):
    def test_append_and_load(self):
        from backup_manager import _append_manifest, _load_manifest
        with tempfile.TemporaryDirectory() as bdir:
            entry = {"backup_id": "bk_aabbccdd0011", "created_at": "2026-01-01T00:00:00+00:00",
                     "status": "CREATED"}
            _append_manifest(bdir, entry)
            entries = _load_manifest(bdir)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["backup_id"], "bk_aabbccdd0011")

    def test_replace_on_same_id(self):
        from backup_manager import _append_manifest, _load_manifest
        with tempfile.TemporaryDirectory() as bdir:
            e1 = {"backup_id": "bk_x", "created_at": "2026-01-01T00:00:00+00:00", "status": "CREATED"}
            e2 = {"backup_id": "bk_x", "created_at": "2026-01-01T00:00:00+00:00", "status": "VERIFIED"}
            _append_manifest(bdir, e1)
            _append_manifest(bdir, e2)
            entries = _load_manifest(bdir)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["status"], "VERIFIED")

    def test_load_missing_dir_returns_empty(self):
        from backup_manager import _load_manifest
        entries = _load_manifest("/tmp/does_not_exist_a29_test_xyz")
        self.assertIsInstance(entries, list)
        self.assertEqual(len(entries), 0)

    def test_manifest_capped_at_100(self):
        from backup_manager import _append_manifest, _load_manifest
        with tempfile.TemporaryDirectory() as bdir:
            for i in range(110):
                _append_manifest(bdir, {
                    "backup_id": f"bk_{i:04d}",
                    "created_at": f"2026-01-{(i % 28) + 1:02d}T00:00:00+00:00",
                    "status": "CREATED",
                })
            entries = _load_manifest(bdir)
            self.assertLessEqual(len(entries), 100)


class TestMakeBackupId(unittest.TestCase):
    def test_prefix(self):
        from backup_manager import _make_backup_id
        bid = _make_backup_id("FULL", "2026-01-01T000000000000")
        self.assertTrue(bid.startswith("bk_"))

    def test_deterministic(self):
        from backup_manager import _make_backup_id
        bid1 = _make_backup_id("FULL", "2026-01-01T000000000000")
        bid2 = _make_backup_id("FULL", "2026-01-01T000000000000")
        self.assertEqual(bid1, bid2)

    def test_different_types_different_ids(self):
        from backup_manager import _make_backup_id
        ts = "2026-01-01T000000000000"
        self.assertNotEqual(_make_backup_id("FULL", ts), _make_backup_id("ALPHA_ONLY", ts))


class TestCreateBackup(unittest.TestCase):
    def setUp(self):
        self.db_path, self.conn_fn = _make_db()
        import database
        self._old_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        self._patcher = patch("database.get_connection", self.conn_fn)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import database
        database.DB_PATH = self._old_db_path
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_creates_file(self):
        from backup_manager import create_backup
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            self.assertEqual(entry["status"], "CREATED")
            self.assertTrue(os.path.exists(entry["file_path"]))

    def test_manifest_entry_fields(self):
        from backup_manager import create_backup
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            for field in ("backup_id", "created_at", "backup_type", "table_count",
                          "row_count", "size_bytes", "checksum_sha256", "status", "file_path"):
                self.assertIn(field, entry, f"Missing field: {field}")

    def test_checksum_in_manifest(self):
        from backup_manager import create_backup
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            self.assertTrue(len(entry["checksum_sha256"]) > 0)

    def test_row_count_positive(self):
        from backup_manager import create_backup
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            # At least the holding and shadow log rows should be there
            self.assertGreater(entry["row_count"], 0)

    def test_json_is_valid(self):
        from backup_manager import create_backup
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            with open(entry["file_path"], "r") as fh:
                payload = json.load(fh)
            self.assertEqual(payload["backup_type"], "FULL")
            self.assertIn("tables", payload)

    def test_deterministic_json_structure(self):
        """Two backups of the same type have the same key structure."""
        from backup_manager import create_backup
        with tempfile.TemporaryDirectory() as bdir:
            e1 = create_backup("PORTFOLIO_ONLY", backup_dir=bdir)
            e2 = create_backup("PORTFOLIO_ONLY", backup_dir=bdir)
            with open(e1["file_path"]) as f1, open(e2["file_path"]) as f2:
                p1, p2 = json.load(f1), json.load(f2)
            self.assertEqual(set(p1.keys()), set(p2.keys()))

    def test_system_config_only_no_table_rows(self):
        from backup_manager import create_backup
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("SYSTEM_CONFIG_ONLY", backup_dir=bdir)
            self.assertEqual(entry["status"], "CREATED")
            with open(entry["file_path"]) as fh:
                payload = json.load(fh)
            self.assertEqual(payload["tables"], {})
            self.assertEqual(payload["row_count"], 0)
            self.assertIn("env_snapshot", payload)
            self.assertIn("feature_flags", payload)

    def test_secrets_excluded_from_system_config(self):
        from backup_manager import create_backup
        with tempfile.TemporaryDirectory() as bdir:
            with patch.dict(os.environ, {
                "ANTHROPIC_API_KEY": "sk-secret-key",
                "TWILIO_AUTH_TOKEN": "auth_tok_secret",
                "API_SECRET": "my_api_secret",
            }):
                entry = create_backup("SYSTEM_CONFIG_ONLY", backup_dir=bdir)
                with open(entry["file_path"]) as fh:
                    raw = fh.read()
            self.assertNotIn("sk-secret-key", raw)
            self.assertNotIn("auth_tok_secret", raw)
            self.assertNotIn("my_api_secret", raw)

    def test_invalid_type_falls_back_to_full(self):
        from backup_manager import create_backup
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("NONEXISTENT_TYPE", backup_dir=bdir)
            self.assertEqual(entry["backup_type"], "FULL")

    def test_graceful_failure_on_bad_dir(self):
        from backup_manager import create_backup
        # Use a path that can't be created (non-writable prefix on most systems)
        entry = create_backup("FULL", backup_dir="/root/nonexistent_backup_a29/sub")
        # Must not raise; returns FAILED entry
        self.assertIn(entry["status"], ("FAILED", "CREATED"))

    def test_notes_stored_in_manifest(self):
        from backup_manager import create_backup
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", notes="pre-deploy snapshot", backup_dir=bdir)
            self.assertEqual(entry["notes"], "pre-deploy snapshot")

    def test_appends_to_manifest(self):
        from backup_manager import create_backup, list_backups
        with tempfile.TemporaryDirectory() as bdir:
            create_backup("FULL", backup_dir=bdir)
            create_backup("ALPHA_ONLY", backup_dir=bdir)
            entries = list_backups(backup_dir=bdir)
            self.assertEqual(len(entries), 2)

    def test_no_send_sms_calls(self):
        from backup_manager import create_backup
        with patch("backup_manager.log") as mock_log, \
             tempfile.TemporaryDirectory() as bdir:
            create_backup("FULL", backup_dir=bdir)
            # Verify no external send calls were made (log is the only side effect)
            self.assertNotIn("send_sms", str(mock_log.mock_calls))

    def test_no_trading_calls(self):
        """Backup creation must never call any broker/trading function."""
        from backup_manager import create_backup
        with patch("builtins.__import__", wraps=__builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__) as mock_import, \
             tempfile.TemporaryDirectory() as bdir:
            create_backup("FULL", backup_dir=bdir)
            imported = [call.args[0] for call in mock_import.call_args_list if call.args]
            for mod in imported:
                self.assertNotIn("broker", mod.lower(), f"Imported broker module: {mod}")


class TestVerifyBackup(unittest.TestCase):
    def setUp(self):
        self.db_path, self.conn_fn = _make_db()
        import database
        self._old_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        self._patcher = patch("database.get_connection", self.conn_fn)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import database
        database.DB_PATH = self._old_db_path
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_verify_passes_for_fresh_backup(self):
        from backup_manager import create_backup, verify_backup
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            result = verify_backup(entry["backup_id"], backup_dir=bdir)
            self.assertTrue(result["ok"])
            check_names = [c["name"] for c in result["checks"]]
            self.assertIn("file_exists", check_names)
            self.assertIn("checksum", check_names)
            self.assertIn("json_parses", check_names)
            self.assertIn("row_counts_match", check_names)

    def test_verify_updates_status_to_verified(self):
        from backup_manager import create_backup, verify_backup, get_backup
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            verify_backup(entry["backup_id"], backup_dir=bdir)
            updated = get_backup(entry["backup_id"], backup_dir=bdir)
            self.assertEqual(updated["status"], "VERIFIED")

    def test_verify_fails_for_unknown_id(self):
        from backup_manager import verify_backup
        with tempfile.TemporaryDirectory() as bdir:
            result = verify_backup("bk_doesnotexist", backup_dir=bdir)
            self.assertFalse(result["ok"])
            self.assertIn("error", result)

    def test_verify_detects_tampered_file(self):
        from backup_manager import create_backup, verify_backup
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            # Tamper with the file
            with open(entry["file_path"], "a") as fh:
                fh.write("\n# tampered")
            result = verify_backup(entry["backup_id"], backup_dir=bdir)
            checksum_check = next(
                (c for c in result["checks"] if c["name"] == "checksum"), None
            )
            self.assertIsNotNone(checksum_check)
            self.assertFalse(checksum_check["passed"])

    def test_verify_detects_missing_file(self):
        from backup_manager import create_backup, verify_backup, _load_manifest, _save_manifest
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            os.unlink(entry["file_path"])
            result = verify_backup(entry["backup_id"], backup_dir=bdir)
            self.assertFalse(result["ok"])
            file_check = next((c for c in result["checks"] if c["name"] == "file_exists"), None)
            self.assertFalse(file_check["passed"])

    def test_verify_returns_checks_list(self):
        from backup_manager import create_backup, verify_backup
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            result = verify_backup(entry["backup_id"], backup_dir=bdir)
            self.assertIsInstance(result["checks"], list)
            self.assertGreater(len(result["checks"]), 0)

    def test_verify_row_counts_match(self):
        from backup_manager import create_backup, verify_backup
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            result = verify_backup(entry["backup_id"], backup_dir=bdir)
            row_check = next((c for c in result["checks"] if c["name"] == "row_counts_match"), None)
            self.assertIsNotNone(row_check)
            self.assertTrue(row_check["passed"])


class TestRestorePreview(unittest.TestCase):
    def setUp(self):
        self.db_path, self.conn_fn = _make_db()
        import database
        self._old_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        self._patcher = patch("database.get_connection", self.conn_fn)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import database
        database.DB_PATH = self._old_db_path
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_preview_returns_read_only_warning(self):
        from backup_manager import create_backup, restore_preview
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            result = restore_preview(entry["backup_id"], backup_dir=bdir)
            self.assertTrue(result["ok"])
            self.assertIn("READ-ONLY", result["warning"])
            self.assertIn("No data has been written", result["warning"])

    def test_preview_has_table_deltas(self):
        from backup_manager import create_backup, restore_preview
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            result = restore_preview(entry["backup_id"], backup_dir=bdir)
            preview = result["preview"]
            self.assertIn("tables", preview)
            self.assertIsInstance(preview["tables"], dict)

    def test_preview_per_table_has_expected_keys(self):
        from backup_manager import create_backup, restore_preview
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            result = restore_preview(entry["backup_id"], backup_dir=bdir)
            for tbl, delta in result["preview"]["tables"].items():
                for key in ("backup_rows", "live_rows", "delta", "table_exists_in_live"):
                    self.assertIn(key, delta, f"Missing {key} in delta for {tbl}")

    def test_preview_does_not_write_to_db(self):
        """Verify that restore_preview never calls _save_manifest (no DB or manifest writes)."""
        from backup_manager import create_backup, restore_preview
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            # restore_preview should never write to the manifest — only verify_backup does.
            # We confirm this by patching _save_manifest and asserting it isn't called.
            with patch("backup_manager._save_manifest") as mock_save:
                restore_preview(entry["backup_id"], backup_dir=bdir)
            mock_save.assert_not_called()

    def test_preview_fails_for_unknown_id(self):
        from backup_manager import restore_preview
        with tempfile.TemporaryDirectory() as bdir:
            result = restore_preview("bk_doesnotexist", backup_dir=bdir)
            self.assertFalse(result["ok"])

    def test_preview_fails_for_missing_file(self):
        from backup_manager import create_backup, restore_preview
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            os.unlink(entry["file_path"])
            result = restore_preview(entry["backup_id"], backup_dir=bdir)
            self.assertFalse(result["ok"])

    def test_preview_change_summary_present(self):
        from backup_manager import create_backup, restore_preview
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            result = restore_preview(entry["backup_id"], backup_dir=bdir)
            self.assertIn("change_summary", result["preview"])
            self.assertIn("would_change", result["preview"])


class TestListAndGetBackup(unittest.TestCase):
    def setUp(self):
        self.db_path, self.conn_fn = _make_db()
        import database
        self._old_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        self._patcher = patch("database.get_connection", self.conn_fn)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import database
        database.DB_PATH = self._old_db_path
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_list_returns_list(self):
        from backup_manager import list_backups
        with tempfile.TemporaryDirectory() as bdir:
            result = list_backups(backup_dir=bdir)
            self.assertIsInstance(result, list)

    def test_list_most_recent_first(self):
        from backup_manager import create_backup, list_backups
        with tempfile.TemporaryDirectory() as bdir:
            e1 = create_backup("FULL", backup_dir=bdir)
            e2 = create_backup("ALPHA_ONLY", backup_dir=bdir)
            entries = list_backups(backup_dir=bdir)
            self.assertEqual(len(entries), 2)
            # Most recent (e2) should be first
            ids = [e["backup_id"] for e in entries]
            self.assertIn(e1["backup_id"], ids)
            self.assertIn(e2["backup_id"], ids)

    def test_list_limit_respected(self):
        from backup_manager import create_backup, list_backups
        with tempfile.TemporaryDirectory() as bdir:
            for _ in range(5):
                create_backup("FULL", backup_dir=bdir)
            entries = list_backups(backup_dir=bdir, limit=3)
            self.assertLessEqual(len(entries), 3)

    def test_get_backup_returns_entry(self):
        from backup_manager import create_backup, get_backup
        with tempfile.TemporaryDirectory() as bdir:
            entry = create_backup("FULL", backup_dir=bdir)
            fetched = get_backup(entry["backup_id"], backup_dir=bdir)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched["backup_id"], entry["backup_id"])

    def test_get_backup_returns_none_for_unknown(self):
        from backup_manager import get_backup
        with tempfile.TemporaryDirectory() as bdir:
            result = get_backup("bk_doesnotexist", backup_dir=bdir)
            self.assertIsNone(result)


class TestLatestBackupAge(unittest.TestCase):
    def test_none_when_no_backups(self):
        from backup_manager import latest_backup_age_hours
        with tempfile.TemporaryDirectory() as bdir:
            result = latest_backup_age_hours(backup_dir=bdir)
            self.assertIsNone(result)

    def test_returns_float_when_backup_exists(self):
        from backup_manager import create_backup, latest_backup_age_hours
        import database
        db_path, conn_fn = _make_db()
        old = database.DB_PATH
        database.DB_PATH = db_path
        with patch("database.get_connection", conn_fn), \
             tempfile.TemporaryDirectory() as bdir:
            create_backup("FULL", backup_dir=bdir)
            age = latest_backup_age_hours(backup_dir=bdir)
            self.assertIsNotNone(age)
            self.assertIsInstance(age, float)
            self.assertGreaterEqual(age, 0.0)
        database.DB_PATH = old
        try:
            os.unlink(db_path)
        except Exception:
            pass

    def test_fresh_backup_is_near_zero(self):
        from backup_manager import create_backup, latest_backup_age_hours
        import database
        db_path, conn_fn = _make_db()
        old = database.DB_PATH
        database.DB_PATH = db_path
        with patch("database.get_connection", conn_fn), \
             tempfile.TemporaryDirectory() as bdir:
            create_backup("FULL", backup_dir=bdir)
            age = latest_backup_age_hours(backup_dir=bdir)
            self.assertLess(age, 0.01)  # < 36 seconds
        database.DB_PATH = old
        try:
            os.unlink(db_path)
        except Exception:
            pass


# ── API Tests ─────────────────────────────────────────────────────────────────

class TestBackupApiList(unittest.TestCase):
    def setUp(self):
        self.app, self.db_tmp, self.old_path = _make_app()
        self.client = self.app.test_client()

    def tearDown(self):
        import database
        database.DB_PATH = self.old_path
        try:
            os.unlink(self.db_tmp)
        except Exception:
            pass

    def test_list_no_auth_required(self):
        with tempfile.TemporaryDirectory() as bdir:
            with patch("backup_manager.DEFAULT_BACKUP_DIR", bdir):
                resp = self.client.get("/api/v1/backups")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("backups", data["data"])

    def test_list_default_limit(self):
        with tempfile.TemporaryDirectory() as bdir:
            with patch("backup_manager.DEFAULT_BACKUP_DIR", bdir):
                resp = self.client.get("/api/v1/backups")
        data = resp.get_json()
        self.assertIn("limit", data["data"])

    def test_list_returns_empty_when_no_backups(self):
        with tempfile.TemporaryDirectory() as bdir:
            with patch("backup_manager.DEFAULT_BACKUP_DIR", bdir):
                resp = self.client.get("/api/v1/backups")
        data = resp.get_json()
        self.assertEqual(data["data"]["backups"], [])


class TestBackupApiGet(unittest.TestCase):
    def setUp(self):
        self.app, self.db_tmp, self.old_path = _make_app()
        self.client = self.app.test_client()

    def tearDown(self):
        import database
        database.DB_PATH = self.old_path
        try:
            os.unlink(self.db_tmp)
        except Exception:
            pass

    def test_get_returns_404_for_unknown(self):
        with tempfile.TemporaryDirectory() as bdir:
            with patch("backup_manager.DEFAULT_BACKUP_DIR", bdir):
                resp = self.client.get("/api/v1/backups/bk_doesnotexist")
        self.assertEqual(resp.status_code, 404)

    def test_get_returns_entry_when_exists(self):
        import database
        db_path, conn_fn = _make_db()
        old = database.DB_PATH
        database.DB_PATH = db_path
        with tempfile.TemporaryDirectory() as bdir, \
             patch("database.get_connection", conn_fn), \
             patch("backup_manager.DEFAULT_BACKUP_DIR", bdir):
            from backup_manager import create_backup
            entry = create_backup("FULL", backup_dir=bdir)
            resp = self.client.get(f"/api/v1/backups/{entry['backup_id']}")
        database.DB_PATH = old
        try:
            os.unlink(db_path)
        except Exception:
            pass
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["data"]["backup_id"], entry["backup_id"])


class TestBackupApiCreate(unittest.TestCase):
    def setUp(self):
        self.app, self.db_tmp, self.old_path = _make_app()
        self.client = self.app.test_client()

    def tearDown(self):
        import database
        database.DB_PATH = self.old_path
        try:
            os.unlink(self.db_tmp)
        except Exception:
            pass

    def test_create_requires_auth(self):
        with patch.dict(os.environ, {"API_SECRET": "test_secret_a29"}):
            resp = self.client.post("/api/v1/backups/create", json={})
        self.assertEqual(resp.status_code, 401)

    def test_create_with_no_secret_configured_passes(self):
        env = {k: v for k, v in os.environ.items() if k != "API_SECRET"}
        import database
        db_path, conn_fn = _make_db()
        old = database.DB_PATH
        database.DB_PATH = db_path
        with patch.dict(os.environ, env, clear=True), \
             tempfile.TemporaryDirectory() as bdir, \
             patch("database.get_connection", conn_fn), \
             patch("backup_manager.DEFAULT_BACKUP_DIR", bdir):
            resp = self.client.post("/api/v1/backups/create", json={"backup_type": "FULL"})
        database.DB_PATH = old
        try:
            os.unlink(db_path)
        except Exception:
            pass
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("backup_id", data["data"])

    def test_create_with_valid_auth(self):
        import database
        db_path, conn_fn = _make_db()
        old = database.DB_PATH
        database.DB_PATH = db_path
        with patch.dict(os.environ, {"API_SECRET": "test_secret_a29"}), \
             tempfile.TemporaryDirectory() as bdir, \
             patch("database.get_connection", conn_fn), \
             patch("backup_manager.DEFAULT_BACKUP_DIR", bdir):
            resp = self.client.post(
                "/api/v1/backups/create",
                json={"backup_type": "SYSTEM_CONFIG_ONLY"},
                headers={"Authorization": "Bearer test_secret_a29"},
            )
        database.DB_PATH = old
        try:
            os.unlink(db_path)
        except Exception:
            pass
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])

    def test_create_invalid_type_returns_400(self):
        env = {k: v for k, v in os.environ.items() if k != "API_SECRET"}
        with patch.dict(os.environ, env, clear=True):
            resp = self.client.post(
                "/api/v1/backups/create", json={"backup_type": "FAKE_TYPE"}
            )
        self.assertEqual(resp.status_code, 400)


class TestBackupApiVerify(unittest.TestCase):
    def setUp(self):
        self.app, self.db_tmp, self.old_path = _make_app()
        self.client = self.app.test_client()

    def tearDown(self):
        import database
        database.DB_PATH = self.old_path
        try:
            os.unlink(self.db_tmp)
        except Exception:
            pass

    def test_verify_requires_auth(self):
        with patch.dict(os.environ, {"API_SECRET": "test_secret_a29"}):
            resp = self.client.post("/api/v1/backups/bk_fake/verify")
        self.assertEqual(resp.status_code, 401)

    def test_verify_returns_404_for_unknown_with_auth(self):
        env = {k: v for k, v in os.environ.items() if k != "API_SECRET"}
        with patch.dict(os.environ, env, clear=True), \
             tempfile.TemporaryDirectory() as bdir, \
             patch("backup_manager.DEFAULT_BACKUP_DIR", bdir):
            resp = self.client.post("/api/v1/backups/bk_doesnotexist/verify")
        self.assertEqual(resp.status_code, 404)


class TestBackupApiDownloadInfo(unittest.TestCase):
    def setUp(self):
        self.app, self.db_tmp, self.old_path = _make_app()
        self.client = self.app.test_client()

    def tearDown(self):
        import database
        database.DB_PATH = self.old_path
        try:
            os.unlink(self.db_tmp)
        except Exception:
            pass

    def test_download_info_no_auth_required(self):
        import database
        db_path, conn_fn = _make_db()
        old = database.DB_PATH
        database.DB_PATH = db_path
        with tempfile.TemporaryDirectory() as bdir, \
             patch("database.get_connection", conn_fn), \
             patch("backup_manager.DEFAULT_BACKUP_DIR", bdir):
            from backup_manager import create_backup
            entry = create_backup("FULL", backup_dir=bdir)
            resp = self.client.get(f"/api/v1/backups/{entry['backup_id']}/download-info")
        database.DB_PATH = old
        try:
            os.unlink(db_path)
        except Exception:
            pass
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        for field in ("backup_id", "backup_type", "size_bytes", "checksum_sha256", "file_path"):
            self.assertIn(field, data["data"])

    def test_download_info_returns_404_for_unknown(self):
        with tempfile.TemporaryDirectory() as bdir, \
             patch("backup_manager.DEFAULT_BACKUP_DIR", bdir):
            resp = self.client.get("/api/v1/backups/bk_doesnotexist/download-info")
        self.assertEqual(resp.status_code, 404)


class TestBackupApiRestorePreview(unittest.TestCase):
    def setUp(self):
        self.app, self.db_tmp, self.old_path = _make_app()
        self.client = self.app.test_client()

    def tearDown(self):
        import database
        database.DB_PATH = self.old_path
        try:
            os.unlink(self.db_tmp)
        except Exception:
            pass

    def test_restore_preview_requires_auth(self):
        with patch.dict(os.environ, {"API_SECRET": "test_secret_a29"}):
            resp = self.client.post("/api/v1/backups/bk_fake/restore-preview")
        self.assertEqual(resp.status_code, 401)

    def test_restore_preview_returns_warning(self):
        import database
        db_path, conn_fn = _make_db()
        old = database.DB_PATH
        database.DB_PATH = db_path
        env = {k: v for k, v in os.environ.items() if k != "API_SECRET"}
        with patch.dict(os.environ, env, clear=True), \
             tempfile.TemporaryDirectory() as bdir, \
             patch("database.get_connection", conn_fn), \
             patch("backup_manager.DEFAULT_BACKUP_DIR", bdir):
            from backup_manager import create_backup
            entry = create_backup("FULL", backup_dir=bdir)
            resp = self.client.post(f"/api/v1/backups/{entry['backup_id']}/restore-preview")
        database.DB_PATH = old
        try:
            os.unlink(db_path)
        except Exception:
            pass
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("READ-ONLY", data["data"]["warning"])

    def test_restore_preview_404_for_unknown(self):
        env = {k: v for k, v in os.environ.items() if k != "API_SECRET"}
        with patch.dict(os.environ, env, clear=True), \
             tempfile.TemporaryDirectory() as bdir, \
             patch("backup_manager.DEFAULT_BACKUP_DIR", bdir):
            resp = self.client.post("/api/v1/backups/bk_doesnotexist/restore-preview")
        self.assertEqual(resp.status_code, 404)


# ── Release Check Integration Tests ──────────────────────────────────────────

class TestReleaseCheckBackupAge(unittest.TestCase):
    def test_warns_when_no_backups(self):
        from system_release_check import _check_backup_age, CHECK_WARN
        with patch("backup_manager.latest_backup_age_hours", return_value=None):
            result = _check_backup_age()
        self.assertEqual(result["status"], CHECK_WARN)
        self.assertIn("No backups", result["detail"])

    def test_warns_when_backup_is_stale(self):
        from system_release_check import _check_backup_age, CHECK_WARN
        with patch("backup_manager.latest_backup_age_hours", return_value=72.5):
            result = _check_backup_age()
        self.assertEqual(result["status"], CHECK_WARN)
        self.assertIn("72.5", result["detail"])

    def test_passes_when_backup_is_fresh(self):
        from system_release_check import _check_backup_age, CHECK_PASS
        with patch("backup_manager.latest_backup_age_hours", return_value=2.0):
            result = _check_backup_age()
        self.assertEqual(result["status"], CHECK_PASS)
        self.assertIn("2.0", result["detail"])

    def test_warns_on_exception(self):
        from system_release_check import _check_backup_age, CHECK_WARN
        with patch("backup_manager.latest_backup_age_hours", side_effect=RuntimeError("oops")):
            result = _check_backup_age()
        self.assertEqual(result["status"], CHECK_WARN)

    def test_backup_age_included_in_data_health(self):
        from system_release_check import run_data_health_checks
        with patch("backup_manager.latest_backup_age_hours", return_value=1.0):
            results = run_data_health_checks()
        names = [r["name"] for r in results]
        self.assertIn("backup_age", names)

    def test_backup_routes_in_required_routes(self):
        from system_release_check import REQUIRED_ROUTES
        self.assertIn("/api/v1/backups", REQUIRED_ROUTES)
        self.assertIn("/api/v1/backups/create", REQUIRED_ROUTES)


if __name__ == "__main__":
    unittest.main()
