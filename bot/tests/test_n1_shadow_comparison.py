"""
Phase N1 — Shadow comparison tests.

Tests compare_shadow() logic:
  - Never sends regardless of eligibility
  - Logs mismatches when legacy and unified disagree
  - Returns OutboundAlert with shadow=True when unified would send
  - Returns None when unified would suppress
  - Mismatch log captures both decisions
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
import pytest
from alert_schema import AlertCandidate
from notification_policy import NotificationPolicy


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _conviction_candidate(ticker="NVDA"):
    return AlertCandidate(
        source="predator", ticker=ticker,
        raw_score=8.0, adjusted_score=4.4, confidence_pct=55.0,
        tier="CONVICTION", regime="NEUTRAL",
        active_signals=["options", "insider", "breakout"],
        suppressed_signals=[],
        urgency=None, entry_price=150.0, stop_price=136.5,
        position_size_cad=5000.0, risk_posture=None, metadata={},
    )


def _watch_candidate(ticker="BYND"):
    return AlertCandidate(
        source="predator", ticker=ticker,
        raw_score=5.0, adjusted_score=1.5, confidence_pct=30.0,
        tier="WATCH", regime="NEUTRAL",
        active_signals=["breakout"],
        suppressed_signals=[],
        urgency=None, entry_price=10.0, stop_price=None,
        position_size_cad=None, risk_posture=None, metadata={},
    )


def _setup_db(path):
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE notification_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id TEXT NOT NULL, ticker TEXT NOT NULL,
            source TEXT NOT NULL, tier TEXT NOT NULL,
            adjusted_score REAL, confidence_pct REAL, raw_score REAL,
            active_signals TEXT, suppressed_signals TEXT,
            regime_context TEXT, risk_posture TEXT, trigger_reason TEXT,
            formatted_message TEXT,
            dry_run INTEGER NOT NULL DEFAULT 0,
            shadow INTEGER NOT NULL DEFAULT 0,
            delivered INTEGER NOT NULL DEFAULT 0,
            sent_at TEXT, evaluated_at TEXT NOT NULL
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
def gw_with_db(tmp_path):
    import alert_gateway as _gw_mod
    import alert_audit as _audit_mod
    import sqlite3 as _sq

    db_path = str(tmp_path / "test.db")
    _setup_db(db_path)

    def _get_conn():
        c = _sq.connect(db_path, timeout=10)
        c.row_factory = _sq.Row
        return c

    _gw_mod.get_connection    = _get_conn
    _audit_mod.get_connection = _get_conn

    sends = []
    from alert_gateway import AlertGateway
    gw = AlertGateway(NotificationPolicy.shadow_mode_policy(), send_fn=lambda m: sends.append(m) or True)
    return gw, sends, db_path


# ─────────────────────────────────────────────
# Shadow mode never sends
# ─────────────────────────────────────────────

class TestShadowNeverSends:
    def test_shadow_submit_does_not_call_send_fn(self, gw_with_db):
        gw, sends, _ = gw_with_db
        gw.submit(_conviction_candidate())
        assert sends == []

    def test_shadow_compare_does_not_call_send_fn(self, gw_with_db):
        gw, sends, _ = gw_with_db
        gw.compare_shadow(_conviction_candidate(), legacy_would_send=True)
        assert sends == []

    def test_shadow_result_has_shadow_true(self, gw_with_db):
        gw, _, _ = gw_with_db
        result    = gw.submit(_conviction_candidate())
        assert result is not None
        assert result.shadow is True

    def test_shadow_result_delivered_false(self, gw_with_db):
        gw, _, _ = gw_with_db
        result    = gw.submit(_conviction_candidate())
        assert result is not None
        assert result.delivered is False


# ─────────────────────────────────────────────
# compare_shadow return values
# ─────────────────────────────────────────────

class TestCompareShadowReturnValues:
    def test_returns_outbound_when_unified_would_send(self, gw_with_db):
        gw, _, _ = gw_with_db
        result    = gw.compare_shadow(_conviction_candidate(), legacy_would_send=True)
        assert result is not None

    def test_returns_none_when_unified_would_suppress_watch(self, gw_with_db):
        gw, _, _ = gw_with_db
        result    = gw.compare_shadow(_watch_candidate(), legacy_would_send=False)
        assert result is None

    def test_shadow_result_has_correct_tier(self, gw_with_db):
        gw, _, _ = gw_with_db
        result    = gw.compare_shadow(_conviction_candidate(), legacy_would_send=True)
        assert result.tier == "CONVICTION"

    def test_shadow_result_has_trigger_reason(self, gw_with_db):
        gw, _, _ = gw_with_db
        result    = gw.compare_shadow(_conviction_candidate(), legacy_would_send=True)
        assert len(result.trigger_reason) > 0


# ─────────────────────────────────────────────
# Mismatch detection
# ─────────────────────────────────────────────

class TestMismatchDetection:
    def _count_mismatches(self, db_path):
        import sqlite3 as sq
        conn = sq.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM notification_audit_log "
            "WHERE shadow=1 AND trigger_reason LIKE 'MISMATCH%'"
        ).fetchone()[0]
        conn.close()
        return count

    def test_no_mismatch_when_both_agree_send(self, gw_with_db):
        gw, _, db_path = gw_with_db
        gw.compare_shadow(_conviction_candidate(), legacy_would_send=True)
        assert self._count_mismatches(db_path) == 0

    def test_no_mismatch_when_both_agree_suppress(self, gw_with_db):
        gw, _, db_path = gw_with_db
        gw.compare_shadow(_watch_candidate(), legacy_would_send=False)
        assert self._count_mismatches(db_path) == 0

    def test_mismatch_logged_when_legacy_sends_unified_suppresses(self, gw_with_db):
        """Legacy would send a WATCH candidate, unified correctly suppresses it."""
        gw, _, db_path = gw_with_db
        # Unified policy requires ALERT+, so WATCH is suppressed
        gw.compare_shadow(_watch_candidate(), legacy_would_send=True)
        assert self._count_mismatches(db_path) == 1

    def test_mismatch_logged_when_unified_sends_legacy_suppresses(self, gw_with_db):
        """Legacy suppressed a CONVICTION candidate, unified correctly sends it."""
        gw, _, db_path = gw_with_db
        gw.compare_shadow(_conviction_candidate(), legacy_would_send=False)
        assert self._count_mismatches(db_path) == 1

    def test_multiple_mismatches_accumulate(self, gw_with_db):
        gw, _, db_path = gw_with_db
        gw.compare_shadow(_watch_candidate(ticker="A"), legacy_would_send=True)
        gw.compare_shadow(_watch_candidate(ticker="B"), legacy_would_send=True)
        assert self._count_mismatches(db_path) == 2


# ─────────────────────────────────────────────
# AlertAuditLog mismatch method directly
# ─────────────────────────────────────────────

class TestAuditMismatchMethod:
    def _audit_with_db(self, db_path):
        import alert_audit as _mod
        import sqlite3 as sq

        def _get_conn():
            c = sq.connect(db_path, timeout=10)
            c.row_factory = sq.Row
            return c

        _mod.get_connection = _get_conn
        from alert_audit import AlertAuditLog
        return AlertAuditLog()

    def test_no_row_when_decisions_agree(self, tmp_path):
        db_path = str(tmp_path / "t.db")
        import sqlite3 as sq
        conn = sq.connect(db_path)
        conn.execute("""
            CREATE TABLE notification_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id TEXT, ticker TEXT, source TEXT, tier TEXT,
                adjusted_score REAL, confidence_pct REAL, raw_score REAL,
                active_signals TEXT, suppressed_signals TEXT,
                regime_context TEXT, risk_posture TEXT, trigger_reason TEXT,
                formatted_message TEXT,
                dry_run INTEGER DEFAULT 0, shadow INTEGER DEFAULT 0,
                delivered INTEGER DEFAULT 0, sent_at TEXT, evaluated_at TEXT
            )
        """)
        conn.commit()
        conn.close()

        audit = self._audit_with_db(db_path)
        from alert_schema import EligibilityResult
        e = EligibilityResult(True, "CONVICTION", 4.4, 55.0, [], [], "tier:CONVICTION")
        audit.log_mismatch(_conviction_candidate(), True, True, e)  # both agree → no row

        c = sq.connect(db_path)
        count = c.execute("SELECT COUNT(*) FROM notification_audit_log WHERE shadow=1").fetchone()[0]
        c.close()
        assert count == 0
