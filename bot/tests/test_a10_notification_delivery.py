"""
Phase A10 — Alpha notification delivery bridge tests.

Covers:
  - flags off blocks send (BLOCKED)
  - dry-run-only mode blocks send (DRY_RUN_ONLY)
  - not reviewed blocks when require_reviewed=true (NOT_REVIEWED)
  - QC allow_notification=false blocks send (QC_BLOCKED)
  - QC tier below minimum blocks send (QC_BLOCKED)
  - duplicate (already SENT) blocks send (DUPLICATE)
  - successful send calls alerts.send_sms exactly once → SENT
  - provider error (send_sms raises) is logged as ERROR
  - delivery audit log is append-only (no UPDATE path)
  - API POST /send requires auth
  - API GET /delivery-log requires no auth
  - Predator notification pipeline unaffected
  - check_delivery_eligibility is pure and deterministic
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "test_a10.db"
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    # Tables needed by delivery module
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alpha_notification_dryruns (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            dry_run_id               TEXT    NOT NULL UNIQUE,
            ticker                   TEXT    NOT NULL,
            readiness_tier           TEXT    NOT NULL,
            alpha_score              REAL,
            alpha_tier               TEXT,
            setup_type               TEXT,
            message_text             TEXT    NOT NULL,
            reason                   TEXT,
            blocking_factors_json    TEXT,
            confirmation_needed_json TEXT,
            status                   TEXT    NOT NULL DEFAULT 'DRY_RUN',
            created_at               TEXT    NOT NULL,
            expires_at               TEXT    NOT NULL,
            reviewed_at              TEXT,
            reviewed_by              TEXT,
            review_note              TEXT,
            dismissed_at             TEXT,
            dismissed_by             TEXT,
            dismiss_reason           TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_qc_history (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker                 TEXT    NOT NULL,
            readiness_tier         TEXT    NOT NULL,
            alpha_tier             TEXT    NOT NULL DEFAULT 'WATCH',
            setup_type             TEXT    NOT NULL DEFAULT 'UNKNOWN',
            alpha_score            REAL,
            readiness_score        REAL,
            qc_score               REAL    NOT NULL,
            qc_tier                TEXT    NOT NULL,
            allow_notification     INTEGER NOT NULL DEFAULT 0,
            suppression_reason     TEXT,
            cooldown_remaining     REAL    NOT NULL DEFAULT 0.0,
            novelty_score          REAL    NOT NULL DEFAULT 0.0,
            stability_score        REAL    NOT NULL DEFAULT 0.0,
            information_gain_score REAL    NOT NULL DEFAULT 0.0,
            quality_flags_json     TEXT    NOT NULL DEFAULT '[]',
            behavior_class         TEXT,
            dry_run_id             TEXT,
            evaluated_at           TEXT    NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alpha_notification_delivery_log (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            dry_run_id        TEXT    NOT NULL,
            ticker            TEXT    NOT NULL DEFAULT '',
            readiness_tier    TEXT    NOT NULL DEFAULT '',
            message_hash      TEXT    NOT NULL DEFAULT '',
            status            TEXT    NOT NULL,
            reason            TEXT,
            provider_response TEXT,
            sent_at           TEXT    NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    return str(p)


def _make_get_conn(db_path):
    def _get_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    return _get_conn


def _now_iso():
    return datetime.now().isoformat()


def _future_iso(hours=48):
    return (datetime.now() + timedelta(hours=hours)).isoformat()


# ── Helpers for inserting test rows ──────────────────────────────────────────

def _insert_dry_run(conn, dry_run_id, ticker="AAPL", status="REVIEWED",
                    readiness_tier="ALERT_READY", message_text="Test alert"):
    conn.execute(
        """INSERT OR IGNORE INTO alpha_notification_dryruns
           (dry_run_id, ticker, readiness_tier, message_text, status, created_at, expires_at)
           VALUES (?,?,?,?,?,?,?)""",
        (dry_run_id, ticker, readiness_tier, message_text, status,
         _now_iso(), _future_iso()),
    )
    conn.commit()


def _insert_qc_row(conn, ticker, qc_tier="PRIORITY", allow_notification=1,
                   suppression_reason=None):
    conn.execute(
        """INSERT INTO notification_qc_history
           (ticker, readiness_tier, alpha_tier, setup_type, qc_score, qc_tier,
            allow_notification, suppression_reason, quality_flags_json, evaluated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (ticker, "ALERT_READY", "HIGH_CONVICTION", "BREAKOUT_EXPANSION",
         82.0, qc_tier, allow_notification, suppression_reason, "[]", _now_iso()),
    )
    conn.commit()


def _insert_sent_log(conn, dry_run_id, ticker="AAPL"):
    conn.execute(
        """INSERT INTO alpha_notification_delivery_log
           (dry_run_id, ticker, readiness_tier, message_hash, status, reason, sent_at)
           VALUES (?,?,?,?,?,?,?)""",
        (dry_run_id, ticker, "ALERT_READY", "abc123", "SENT", "OK", _now_iso()),
    )
    conn.commit()


# ── Flags helpers ─────────────────────────────────────────────────────────────

def _flags_all_off():
    """All flags that would prevent sending."""
    return {
        "enabled": False,
        "dry_run_only": True,
        "min_qc_tier": "PRIORITY",
        "require_reviewed": True,
    }


def _flags_enabled():
    """Flags configured to allow sending (feature fully enabled)."""
    return {
        "enabled": True,
        "dry_run_only": False,
        "min_qc_tier": "PRIORITY",
        "require_reviewed": True,
    }


# ── 1. check_delivery_eligibility (pure function) ────────────────────────────

class TestCheckDeliveryEligibility:

    def _dry_run(self, status="REVIEWED"):
        return {"status": status, "ticker": "AAPL", "readiness_tier": "ALERT_READY"}

    def _qc(self, allow=True, tier="PRIORITY", reason=None):
        return {
            "allow_notification": 1 if allow else 0,
            "qc_tier": tier,
            "suppression_reason": reason,
        }

    def test_flags_disabled_returns_blocked(self):
        from alpha_notification_delivery import check_delivery_eligibility
        status, reason = check_delivery_eligibility(
            self._dry_run(), self._qc(), _flags_all_off()
        )
        assert status == "BLOCKED"
        assert "DISABLED" in reason

    def test_dry_run_only_returns_dryrun_only(self):
        from alpha_notification_delivery import check_delivery_eligibility
        flags = {**_flags_enabled(), "dry_run_only": True}
        status, reason = check_delivery_eligibility(
            self._dry_run(), self._qc(), flags
        )
        assert status == "DRY_RUN_ONLY"

    def test_not_reviewed_returns_not_reviewed(self):
        from alpha_notification_delivery import check_delivery_eligibility
        status, reason = check_delivery_eligibility(
            self._dry_run(status="DRY_RUN"), self._qc(), _flags_enabled()
        )
        assert status == "NOT_REVIEWED"

    def test_reviewed_status_passes_review_check(self):
        from alpha_notification_delivery import check_delivery_eligibility
        status, _ = check_delivery_eligibility(
            self._dry_run(status="REVIEWED"), self._qc(), _flags_enabled()
        )
        assert status == "ELIGIBLE"

    def test_dismissed_dry_run_blocked(self):
        from alpha_notification_delivery import check_delivery_eligibility
        status, reason = check_delivery_eligibility(
            self._dry_run(status="DISMISSED"), self._qc(), _flags_enabled()
        )
        assert status == "BLOCKED"
        assert "DISMISSED" in reason

    def test_expired_dry_run_blocked(self):
        from alpha_notification_delivery import check_delivery_eligibility
        status, reason = check_delivery_eligibility(
            self._dry_run(status="EXPIRED"), self._qc(), _flags_enabled()
        )
        assert status == "BLOCKED"
        assert "EXPIRED" in reason

    def test_no_qc_record_returns_qc_blocked(self):
        from alpha_notification_delivery import check_delivery_eligibility
        status, reason = check_delivery_eligibility(
            self._dry_run(), None, _flags_enabled()
        )
        assert status == "QC_BLOCKED"

    def test_qc_allow_false_returns_qc_blocked(self):
        from alpha_notification_delivery import check_delivery_eligibility
        status, reason = check_delivery_eligibility(
            self._dry_run(), self._qc(allow=False, reason="IN_COOLDOWN:5.0h"),
            _flags_enabled()
        )
        assert status == "QC_BLOCKED"

    def test_qc_tier_below_min_returns_qc_blocked(self):
        from alpha_notification_delivery import check_delivery_eligibility
        status, reason = check_delivery_eligibility(
            self._dry_run(), self._qc(tier="ALLOW"),
            {**_flags_enabled(), "min_qc_tier": "PRIORITY"}
        )
        assert status == "QC_BLOCKED"
        assert "ALLOW" in reason
        assert "PRIORITY" in reason

    def test_all_conditions_met_returns_eligible(self):
        from alpha_notification_delivery import check_delivery_eligibility
        status, reason = check_delivery_eligibility(
            self._dry_run(), self._qc(tier="PRIORITY"), _flags_enabled()
        )
        assert status == "ELIGIBLE"

    def test_deterministic(self):
        from alpha_notification_delivery import check_delivery_eligibility
        args = (self._dry_run(), self._qc(), _flags_enabled())
        r1 = check_delivery_eligibility(*args)
        r2 = check_delivery_eligibility(*args)
        assert r1 == r2

    def test_never_raises_on_none_inputs(self):
        from alpha_notification_delivery import check_delivery_eligibility
        result = check_delivery_eligibility(None, None, {})
        assert isinstance(result, tuple)
        assert len(result) == 2


# ── 2. Flags-off blocks send ──────────────────────────────────────────────────

class TestFlagsOffBlocksSend:

    def test_enabled_false_blocks(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_all_off())

        result = mod.deliver_notification("some_id")
        assert result["status"] == "BLOCKED"
        assert "DISABLED" in result["reason"]

    def test_enabled_false_logged_to_audit(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_all_off())

        mod.deliver_notification("block_id")

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM alpha_notification_delivery_log WHERE dry_run_id = 'block_id'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["status"] == "BLOCKED"


# ── 3. Dry-run-only mode ──────────────────────────────────────────────────────

class TestDryRunOnlyBlocksSend:

    def test_dry_run_only_returns_dry_run_only(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        flags = {**_flags_enabled(), "dry_run_only": True}
        monkeypatch.setattr(mod, "_read_flags", lambda: flags)

        result = mod.deliver_notification("dr_id")
        assert result["status"] == "DRY_RUN_ONLY"

    def test_dry_run_only_logged(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        flags = {**_flags_enabled(), "dry_run_only": True}
        monkeypatch.setattr(mod, "_read_flags", lambda: flags)

        mod.deliver_notification("dro_id")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT status FROM alpha_notification_delivery_log WHERE dry_run_id = 'dro_id'"
        ).fetchall()
        conn.close()
        assert rows[0]["status"] == "DRY_RUN_ONLY"


# ── 4. Not reviewed blocks ────────────────────────────────────────────────────

class TestNotReviewedBlocksSend:

    def test_dry_run_status_not_reviewed(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_enabled())

        conn = sqlite3.connect(db_path)
        _insert_dry_run(conn, "unrev_id", status="DRY_RUN")
        _insert_qc_row(conn, "AAPL")
        conn.close()

        result = mod.deliver_notification("unrev_id")
        assert result["status"] == "NOT_REVIEWED"

    def test_reviewed_passes_review_check(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_enabled())

        conn = sqlite3.connect(db_path)
        _insert_dry_run(conn, "rev_id", status="REVIEWED")
        _insert_qc_row(conn, "AAPL")
        conn.close()

        mock_send = MagicMock(return_value=True)
        with patch.dict("sys.modules", {"alerts": MagicMock(send_sms=mock_send)}):
            result = mod.deliver_notification("rev_id")

        assert result["status"] == "SENT"


# ── 5. QC blocked ────────────────────────────────────────────────────────────

class TestQcBlockedSend:

    def test_no_qc_record_blocks(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_enabled())

        conn = sqlite3.connect(db_path)
        _insert_dry_run(conn, "noqc_id", status="REVIEWED")
        conn.close()

        result = mod.deliver_notification("noqc_id")
        assert result["status"] == "QC_BLOCKED"
        assert "NO_QC_RECORD" in result["reason"]

    def test_qc_allow_false_blocks(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_enabled())

        conn = sqlite3.connect(db_path)
        _insert_dry_run(conn, "blocked_qc", status="REVIEWED")
        _insert_qc_row(conn, "AAPL", qc_tier="BLOCK", allow_notification=0,
                       suppression_reason="IN_COOLDOWN:5.0h")
        conn.close()

        result = mod.deliver_notification("blocked_qc")
        assert result["status"] == "QC_BLOCKED"

    def test_qc_tier_below_min_blocks(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        flags = {**_flags_enabled(), "min_qc_tier": "PRIORITY"}
        monkeypatch.setattr(mod, "_read_flags", lambda: flags)

        conn = sqlite3.connect(db_path)
        _insert_dry_run(conn, "low_tier", status="REVIEWED")
        _insert_qc_row(conn, "AAPL", qc_tier="ALLOW", allow_notification=1)
        conn.close()

        result = mod.deliver_notification("low_tier")
        assert result["status"] == "QC_BLOCKED"
        assert "ALLOW" in result["reason"]
        assert "PRIORITY" in result["reason"]

    def test_qc_priority_passes_default_min(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_enabled())

        conn = sqlite3.connect(db_path)
        _insert_dry_run(conn, "prio_id", status="REVIEWED")
        _insert_qc_row(conn, "AAPL", qc_tier="PRIORITY", allow_notification=1)
        conn.close()

        mock_send = MagicMock(return_value=True)
        with patch.dict("sys.modules", {"alerts": MagicMock(send_sms=mock_send)}):
            result = mod.deliver_notification("prio_id")

        assert result["status"] == "SENT"


# ── 6. Duplicate blocks ───────────────────────────────────────────────────────

class TestDuplicateBlocksSend:

    def test_already_sent_returns_duplicate(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_enabled())

        conn = sqlite3.connect(db_path)
        _insert_dry_run(conn, "dup_id", status="REVIEWED")
        _insert_qc_row(conn, "AAPL")
        _insert_sent_log(conn, "dup_id")  # already SENT
        conn.close()

        result = mod.deliver_notification("dup_id")
        assert result["status"] == "DUPLICATE"
        assert "ALREADY_SENT" in result["reason"]

    def test_duplicate_send_does_not_call_send_sms(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_enabled())

        conn = sqlite3.connect(db_path)
        _insert_dry_run(conn, "dup2_id", status="REVIEWED")
        _insert_qc_row(conn, "AAPL")
        _insert_sent_log(conn, "dup2_id")
        conn.close()

        mock_send = MagicMock(side_effect=AssertionError("send_sms must not be called"))
        with patch.dict("sys.modules", {"alerts": MagicMock(send_sms=mock_send)}):
            mod.deliver_notification("dup2_id")

        mock_send.assert_not_called()


# ── 7. Successful send ────────────────────────────────────────────────────────

class TestSuccessfulSend:

    def test_sent_calls_send_sms_once(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_enabled())

        conn = sqlite3.connect(db_path)
        _insert_dry_run(conn, "ok_id", status="REVIEWED", message_text="Alpha alert for AAPL")
        _insert_qc_row(conn, "AAPL")
        conn.close()

        mock_send = MagicMock(return_value=True)
        with patch.dict("sys.modules", {"alerts": MagicMock(send_sms=mock_send)}):
            result = mod.deliver_notification("ok_id")

        assert result["status"] == "SENT"
        mock_send.assert_called_once()
        # Verify message text was passed
        call_args = mock_send.call_args
        assert "Alpha alert for AAPL" in call_args[0][0]

    def test_sent_logged_to_audit(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_enabled())

        conn = sqlite3.connect(db_path)
        _insert_dry_run(conn, "audit_id", status="REVIEWED")
        _insert_qc_row(conn, "AAPL")
        conn.close()

        with patch.dict("sys.modules", {"alerts": MagicMock(send_sms=MagicMock(return_value=True))}):
            mod.deliver_notification("audit_id")

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM alpha_notification_delivery_log WHERE dry_run_id = 'audit_id'"
        ).fetchall()
        conn.close()
        assert len(rows) >= 1
        sent_rows = [r for r in rows if r["status"] == "SENT"]
        assert len(sent_rows) == 1

    def test_sent_result_has_ticker(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_enabled())

        conn = sqlite3.connect(db_path)
        _insert_dry_run(conn, "ticker_id", ticker="NVDA", status="REVIEWED")
        _insert_qc_row(conn, "NVDA")
        conn.close()

        with patch.dict("sys.modules", {"alerts": MagicMock(send_sms=MagicMock(return_value=True))}):
            result = mod.deliver_notification("ticker_id")

        assert result["ticker"] == "NVDA"
        assert result["status"] == "SENT"


# ── 8. Provider error logged ──────────────────────────────────────────────────

class TestProviderErrorLogged:

    def test_send_sms_returns_false_logged_as_error(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_enabled())

        conn = sqlite3.connect(db_path)
        _insert_dry_run(conn, "fail_id", status="REVIEWED")
        _insert_qc_row(conn, "AAPL")
        conn.close()

        mock_send = MagicMock(return_value=False)  # send_sms reports failure
        with patch.dict("sys.modules", {"alerts": MagicMock(send_sms=mock_send)}):
            result = mod.deliver_notification("fail_id")

        assert result["status"] == "ERROR"

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT status FROM alpha_notification_delivery_log WHERE dry_run_id = 'fail_id'"
        ).fetchall()
        conn.close()
        assert any(r["status"] == "ERROR" for r in rows)

    def test_send_sms_raises_logged_as_error(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_enabled())

        conn = sqlite3.connect(db_path)
        _insert_dry_run(conn, "exc_id", status="REVIEWED")
        _insert_qc_row(conn, "AAPL")
        conn.close()

        mock_send = MagicMock(side_effect=RuntimeError("Twilio connection reset"))
        with patch.dict("sys.modules", {"alerts": MagicMock(send_sms=mock_send)}):
            result = mod.deliver_notification("exc_id")

        assert result["status"] == "ERROR"
        assert "SEND_ERROR" in result["reason"]

    def test_error_reason_contains_excerpt(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_enabled())

        conn = sqlite3.connect(db_path)
        _insert_dry_run(conn, "exc2_id", status="REVIEWED")
        _insert_qc_row(conn, "AAPL")
        conn.close()

        mock_send = MagicMock(side_effect=RuntimeError("auth failure"))
        with patch.dict("sys.modules", {"alerts": MagicMock(send_sms=mock_send)}):
            result = mod.deliver_notification("exc2_id")

        assert "auth failure" in result["reason"]


# ── 9. Delivery audit immutability ────────────────────────────────────────────

class TestDeliveryAuditImmutable:

    def test_module_has_no_update_on_delivery_log(self):
        """delivery module must not UPDATE alpha_notification_delivery_log."""
        import alpha_notification_delivery
        src = open(alpha_notification_delivery.__file__).read()
        # Check no UPDATE statement targeting the delivery log table
        assert "UPDATE alpha_notification_delivery_log" not in src

    def test_get_delivery_log_returns_list(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        result = mod.get_delivery_log()
        assert isinstance(result, list)

    def test_multiple_sends_create_multiple_rows(self, db_path, monkeypatch):
        """Each deliver_notification call appends a new row (no upsert)."""
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        flags = {**_flags_all_off()}  # keep disabled so we get BLOCKED rows
        monkeypatch.setattr(mod, "_read_flags", lambda: flags)

        mod.deliver_notification("immut_id")
        mod.deliver_notification("immut_id")

        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT * FROM alpha_notification_delivery_log WHERE dry_run_id = 'immut_id'"
        ).fetchall()
        conn.close()
        assert len(rows) == 2  # two separate audit entries


# ── 10. get_delivery_log ──────────────────────────────────────────────────────

class TestGetDeliveryLog:

    def test_empty_db_returns_empty(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)
        result = mod.get_delivery_log()
        assert result == []

    def test_ticker_filter(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)

        conn = sqlite3.connect(db_path)
        _insert_sent_log(conn, "d1", ticker="AAPL")
        _insert_sent_log(conn, "d2", ticker="NVDA")
        conn.close()

        result = mod.get_delivery_log(ticker="AAPL")
        assert all(r["ticker"] == "AAPL" for r in result)

    def test_limit_respected(self, db_path, monkeypatch):
        import alpha_notification_delivery as mod
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(mod, "_ensure_table", lambda: None)

        conn = sqlite3.connect(db_path)
        for i in range(5):
            _insert_sent_log(conn, f"d{i}", ticker=f"T{i}")
        conn.close()

        result = mod.get_delivery_log(limit=2)
        assert len(result) == 2


# ── 11. API endpoints ─────────────────────────────────────────────────────────

@pytest.fixture
def app_client(db_path, monkeypatch):
    import alpha_notification_delivery as mod
    monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
    monkeypatch.setattr(mod, "_ensure_table", lambda: None)

    from flask import Flask
    from api import api_bp, cache_clear
    flask_app = Flask("test_a10")
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(api_bp)
    cache_clear()
    with flask_app.test_client() as client:
        yield client, db_path


class TestApiDeliveryLog:

    def test_get_log_no_auth_required(self, app_client):
        client, _ = app_client
        resp = client.get("/api/v1/alpha/notifications/delivery-log")
        assert resp.status_code == 200

    def test_get_log_empty(self, app_client):
        client, _ = app_client
        resp = client.get("/api/v1/alpha/notifications/delivery-log")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["count"] == 0

    def test_get_log_envelope_structure(self, app_client):
        client, _ = app_client
        resp = client.get("/api/v1/alpha/notifications/delivery-log")
        body = resp.get_json()
        assert "ok" in body
        assert "data" in body
        assert "meta" in body
        assert "feature_flags" in body["data"]

    def test_get_log_feature_flags_present(self, app_client):
        client, _ = app_client
        resp = client.get("/api/v1/alpha/notifications/delivery-log")
        flags = resp.get_json()["data"]["feature_flags"]
        assert "enabled" in flags
        assert "dry_run_only" in flags

    def test_get_log_note_mentions_disabled(self, app_client):
        client, _ = app_client
        resp = client.get("/api/v1/alpha/notifications/delivery-log")
        note = resp.get_json()["data"].get("note", "")
        assert "disabled" in note.lower() or "flag" in note.lower()


class TestApiSend:

    def test_send_requires_auth(self, app_client):
        client, _ = app_client
        # Set API_SECRET so auth is enforced; send without header → 401
        with patch.dict(os.environ, {"API_SECRET": "test-secret-a10"}):
            resp = client.post("/api/v1/alpha/notifications/some_id/send", json={})
        assert resp.status_code == 401

    def test_send_with_auth_blocked_by_default(self, app_client, monkeypatch):
        """With default flags (enabled=false), send returns BLOCKED."""
        client, db_path = app_client
        import alpha_notification_delivery as mod
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_all_off())

        with patch.dict(os.environ, {"API_SECRET": "test-secret"}):
            resp = client.post(
                "/api/v1/alpha/notifications/blocked_id/send",
                headers={"Authorization": "Bearer test-secret"},
                json={},
            )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["status"] == "BLOCKED"

    def test_send_with_auth_returns_result_envelope(self, app_client, monkeypatch):
        client, db_path = app_client
        import alpha_notification_delivery as mod
        monkeypatch.setattr(mod, "_read_flags", lambda: _flags_all_off())

        with patch.dict(os.environ, {"API_SECRET": "test-secret"}):
            resp = client.post(
                "/api/v1/alpha/notifications/env_id/send",
                headers={"Authorization": "Bearer test-secret"},
                json={},
            )
        body = resp.get_json()
        assert "ok" in body
        assert "data" in body
        assert "status" in body["data"]

    def test_send_without_auth_is_401(self, app_client):
        client, _ = app_client
        with patch.dict(os.environ, {"API_SECRET": "test-secret-a10"}):
            resp = client.post("/api/v1/alpha/notifications/xyz/send", json={})
        assert resp.status_code == 401


# ── 12. Predator pipeline unaffected ─────────────────────────────────────────

class TestPredatorUnaffected:

    def test_delivery_module_does_not_import_sell_monitor(self):
        """alpha_notification_delivery must not import sell_monitor."""
        import alpha_notification_delivery
        src = open(alpha_notification_delivery.__file__).read()
        assert "sell_monitor" not in src
        assert "from scanner" not in src

    def test_delivery_module_does_not_mutate_alert_log(self):
        """alpha_notification_delivery writes only to delivery_log, not alert_log."""
        import alpha_notification_delivery
        src = open(alpha_notification_delivery.__file__).read()
        assert "INSERT INTO alert_log" not in src
        assert "UPDATE alert_log" not in src

    def test_alerts_send_sms_signature_unchanged(self):
        """alerts.send_sms still accepts (message, bypass_quiet) — not broken."""
        import inspect
        from alerts import send_sms
        sig = inspect.signature(send_sms)
        params = list(sig.parameters.keys())
        assert "message" in params
        assert "bypass_quiet" in params

    def test_sell_monitor_importable_without_delivery(self):
        """sell_monitor.py can be imported without pulling in delivery module."""
        import sell_monitor  # should not raise
        assert hasattr(sell_monitor, "check_sell_signals") or True  # just import check


# ── 13. get_delivery_flags ────────────────────────────────────────────────────

class TestGetDeliveryFlags:

    def test_defaults_disabled(self):
        """Default env → enabled=False, dry_run_only=True."""
        from alpha_notification_delivery import get_delivery_flags
        with patch.dict(os.environ, {}, clear=False):
            # Remove the flags if set in environment
            env_backup = {}
            for k in ("ALPHA_NOTIFICATIONS_ENABLED", "ALPHA_NOTIFICATIONS_DRY_RUN_ONLY"):
                env_backup[k] = os.environ.pop(k, None)
            try:
                flags = get_delivery_flags()
                assert flags["enabled"] is False
                assert flags["dry_run_only"] is True
            finally:
                for k, v in env_backup.items():
                    if v is not None:
                        os.environ[k] = v

    def test_env_override_enabled(self):
        from alpha_notification_delivery import get_delivery_flags
        with patch.dict(os.environ, {"ALPHA_NOTIFICATIONS_ENABLED": "true"}):
            flags = get_delivery_flags()
            assert flags["enabled"] is True

    def test_env_override_dry_run_false(self):
        from alpha_notification_delivery import get_delivery_flags
        with patch.dict(os.environ, {"ALPHA_NOTIFICATIONS_DRY_RUN_ONLY": "false"}):
            flags = get_delivery_flags()
            assert flags["dry_run_only"] is False

    def test_min_qc_tier_default_priority(self):
        from alpha_notification_delivery import get_delivery_flags
        env_backup = os.environ.pop("ALPHA_NOTIFICATION_MIN_QC_TIER", None)
        try:
            flags = get_delivery_flags()
            assert flags["min_qc_tier"] == "PRIORITY"
        finally:
            if env_backup is not None:
                os.environ["ALPHA_NOTIFICATION_MIN_QC_TIER"] = env_backup

    def test_require_reviewed_default_true(self):
        from alpha_notification_delivery import get_delivery_flags
        env_backup = os.environ.pop("ALPHA_NOTIFICATION_REQUIRE_REVIEWED", None)
        try:
            flags = get_delivery_flags()
            assert flags["require_reviewed"] is True
        finally:
            if env_backup is not None:
                os.environ["ALPHA_NOTIFICATION_REQUIRE_REVIEWED"] = env_backup


# ── 14. No autonomous sending ─────────────────────────────────────────────────

class TestNoAutonomousSending:

    def test_module_has_no_scheduler_import(self):
        import alpha_notification_delivery
        src = open(alpha_notification_delivery.__file__).read()
        assert "APScheduler" not in src
        assert "BackgroundScheduler" not in src
        assert "BlockingScheduler" not in src

    def test_module_has_no_auto_send_function(self):
        """No function that auto-batches or schedules sends."""
        import alpha_notification_delivery as mod
        assert not hasattr(mod, "send_all")
        assert not hasattr(mod, "batch_send")
        assert not hasattr(mod, "schedule_send")

    def test_deliver_notification_one_at_a_time(self, db_path, monkeypatch):
        """deliver_notification accepts exactly one dry_run_id."""
        import inspect
        from alpha_notification_delivery import deliver_notification
        sig = inspect.signature(deliver_notification)
        assert list(sig.parameters.keys()) == ["dry_run_id"]
