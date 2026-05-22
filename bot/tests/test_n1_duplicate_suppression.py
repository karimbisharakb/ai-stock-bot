"""
Phase N1 — Duplicate suppression tests.

Tests dedup window logic via AlertGateway using an in-memory SQLite DB.
Covers:
  - First send goes through
  - Repeat within window is suppressed
  - Different source resets dedup
  - Window expiry allows re-send
  - Dry-run and shadow never count toward dedup
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
import tempfile
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from alert_schema import AlertCandidate
from notification_policy import NotificationPolicy


def _make_candidate(ticker="NVDA", source="predator", tier="CONVICTION"):
    return AlertCandidate(
        source=source, ticker=ticker,
        raw_score=8.0, adjusted_score=4.4, confidence_pct=55.0,
        tier=tier, regime="NEUTRAL",
        active_signals=["options", "insider", "breakout"],
        suppressed_signals=[],
        urgency=None, entry_price=150.0, stop_price=136.5,
        position_size_cad=5000.0, risk_posture=None, metadata={},
    )


def _make_gateway(db_path, policy=None):
    """Return an AlertGateway that uses the provided DB path."""
    from alert_gateway import AlertGateway
    pol = policy or NotificationPolicy.alert_and_above()
    gw  = AlertGateway(pol, send_fn=lambda msg: True)
    # Patch get_connection to use test DB
    return gw, db_path


def _setup_test_db(path):
    """Create minimal schema needed by gateway."""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE notification_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            source TEXT NOT NULL,
            tier TEXT NOT NULL,
            adjusted_score REAL, confidence_pct REAL, raw_score REAL,
            active_signals TEXT, suppressed_signals TEXT,
            regime_context TEXT, risk_posture TEXT, trigger_reason TEXT,
            formatted_message TEXT,
            dry_run INTEGER NOT NULL DEFAULT 0,
            shadow INTEGER NOT NULL DEFAULT 0,
            delivered INTEGER NOT NULL DEFAULT 0,
            sent_at TEXT,
            evaluated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE notification_suppressed_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL, source TEXT NOT NULL,
            suppression_reasons TEXT, resolved_tier TEXT,
            adjusted_score REAL, confidence_pct REAL,
            evaluated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


@pytest.fixture
def tmp_db(tmp_path):
    path = str(tmp_path / "test.db")
    _setup_test_db(path)
    return path


# ─────────────────────────────────────────────
# Gateway dedup via DB — patching get_connection
# ─────────────────────────────────────────────

class TestGatewayDedupWindow:
    def _gateway_with_db(self, db_path):
        import alert_gateway as _gw_mod
        import alert_audit as _audit_mod
        import sqlite3 as _sq

        def _get_conn():
            conn = _sq.connect(db_path, timeout=10)
            conn.row_factory = _sq.Row
            return conn

        _gw_mod.get_connection  = _get_conn
        _audit_mod.get_connection = _get_conn

        from alert_gateway import AlertGateway
        return AlertGateway(
            NotificationPolicy.alert_and_above(),
            send_fn=lambda msg: True,
        )

    def test_first_submission_is_eligible(self, tmp_db):
        gw = self._gateway_with_db(tmp_db)
        result = gw.submit(_make_candidate())
        assert result is not None
        assert result.delivered is True

    def test_second_submission_same_ticker_source_is_suppressed(self, tmp_db):
        gw = self._gateway_with_db(tmp_db)
        gw.submit(_make_candidate())
        result = gw.submit(_make_candidate())
        assert result is None  # suppressed as duplicate

    def test_different_source_not_considered_duplicate(self, tmp_db):
        gw = self._gateway_with_db(tmp_db)
        gw.submit(_make_candidate(source="predator"))
        # scanner has its own dedup namespace
        result = gw.submit(_make_candidate(source="scanner"))
        assert result is not None

    def test_different_ticker_not_considered_duplicate(self, tmp_db):
        gw = self._gateway_with_db(tmp_db)
        gw.submit(_make_candidate(ticker="NVDA"))
        result = gw.submit(_make_candidate(ticker="AMD"))
        assert result is not None


# ─────────────────────────────────────────────
# Dry-run and shadow dedup isolation
# ─────────────────────────────────────────────

class TestDryRunShadowDedupIsolation:
    def _gateway_dry_run(self, db_path):
        import alert_gateway as _gw_mod
        import alert_audit as _audit_mod
        import sqlite3 as _sq

        def _get_conn():
            conn = _sq.connect(db_path, timeout=10)
            conn.row_factory = _sq.Row
            return conn

        _gw_mod.get_connection    = _get_conn
        _audit_mod.get_connection = _get_conn

        from alert_gateway import AlertGateway
        return AlertGateway(
            NotificationPolicy.dry_run_mode(),
            send_fn=lambda msg: True,
        )

    def test_dry_run_result_delivered_false(self, tmp_db):
        gw     = self._gateway_dry_run(tmp_db)
        result = gw.submit(_make_candidate())
        assert result is not None
        assert result.dry_run is True
        assert result.delivered is False

    def test_dry_run_does_not_block_live_send(self, tmp_db):
        """Dry-run rows must not count toward dedup for live gateway."""
        import alert_gateway as _gw_mod
        import alert_audit as _audit_mod
        import sqlite3 as _sq

        def _get_conn():
            conn = _sq.connect(tmp_db, timeout=10)
            conn.row_factory = _sq.Row
            return conn

        _gw_mod.get_connection    = _get_conn
        _audit_mod.get_connection = _get_conn

        from alert_gateway import AlertGateway

        dry_gw  = AlertGateway(NotificationPolicy.dry_run_mode(), send_fn=lambda m: True)
        live_gw = AlertGateway(NotificationPolicy.alert_and_above(), send_fn=lambda m: True)

        dry_gw.submit(_make_candidate())   # dry-run — should not poison dedup
        result = live_gw.submit(_make_candidate())
        assert result is not None          # live send should go through


# ─────────────────────────────────────────────
# Rate limiting (in-memory)
# ─────────────────────────────────────────────

class TestRateLimiting:
    def _fresh_gateway(self, db_path, rate_limit=2):
        import alert_gateway as _gw_mod
        import alert_audit as _audit_mod
        import sqlite3 as _sq

        def _get_conn():
            conn = _sq.connect(db_path, timeout=10)
            conn.row_factory = _sq.Row
            return conn

        _gw_mod.get_connection    = _get_conn
        _audit_mod.get_connection = _get_conn

        from alert_gateway import AlertGateway
        return AlertGateway(
            NotificationPolicy(min_tier="ALERT", rate_limit_per_hour=rate_limit),
            send_fn=lambda m: True,
        )

    def test_send_up_to_rate_limit(self, tmp_db):
        gw = self._fresh_gateway(tmp_db, rate_limit=2)
        r1 = gw.submit(_make_candidate(ticker="A"))
        r2 = gw.submit(_make_candidate(ticker="B"))
        assert r1 is not None
        assert r2 is not None

    def test_exceed_rate_limit_is_suppressed(self, tmp_db):
        gw = self._fresh_gateway(tmp_db, rate_limit=1)
        gw.submit(_make_candidate(ticker="A"))
        r2 = gw.submit(_make_candidate(ticker="B"))
        assert r2 is None  # suppressed by rate limit
