"""
Phase N1 — Notification render stability tests.

Verifies that all formatters and the gateway produce non-crashing, well-formed
output across a wide range of input combinations. No DB, no network.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
import pytest
from alert_schema import AlertCandidate, EligibilityResult
from alert_formatter import format_predator_alert, format_sell_alert_unified, format_alert
from notification_policy import NotificationPolicy


_DIV = "─" * 44


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _elig(tier="CONVICTION", adj=4.4, conf=55.0, trigger="tier:CONVICTION"):
    return EligibilityResult(
        eligible=True, resolved_tier=tier,
        adjusted_score=adj, confidence_pct=conf,
        reasons=[f"tier:{tier}"], suppression_reasons=[],
        trigger_reason=trigger,
    )


def _pred(tier="CONVICTION", adj=4.4, conf=55.0, regime=None, active=None,
          suppressed=None, entry=150.0, stop=136.5, position=5000.0):
    return AlertCandidate(
        source="predator", ticker="NVDA",
        raw_score=8.0, adjusted_score=adj, confidence_pct=conf,
        tier=tier, regime=regime,
        active_signals=active if active is not None else ["options", "insider"],
        suppressed_signals=suppressed or [],
        urgency=None, entry_price=entry, stop_price=stop,
        position_size_cad=position, risk_posture=None,
        metadata={"signals": {
            "options": {"score": 3, "reason": "unusual calls", "data_quality": "HIGH"},
            "insider": {"score": 2, "reason": "3 insiders", "data_quality": "MEDIUM"},
        }},
    )


def _sell(urgency="URGENT", active=None, pct_1d=-2.1):
    return AlertCandidate(
        source="sell_monitor", ticker="VFV.TO",
        raw_score=None, adjusted_score=None, confidence_pct=None,
        tier=None, regime=None,
        active_signals=active if active is not None else ["Price below 50-day MA"],
        suppressed_signals=[],
        urgency=urgency, entry_price=158.0, stop_price=None,
        position_size_cad=None, risk_posture="High",
        metadata={"pct_1d": pct_1d},
    )


# ─────────────────────────────────────────────
# Predator formatter — does not crash
# ─────────────────────────────────────────────

class TestPredatorFormatterStability:
    @pytest.mark.parametrize("tier", ["CONVICTION", "ALERT", "WATCH"])
    def test_all_tiers_render(self, tier):
        msg = format_predator_alert(_pred(tier=tier), _elig(tier=tier))
        assert len(msg.strip()) > 0

    @pytest.mark.parametrize("regime", [None, "BULL", "BEAR", "NEUTRAL"])
    def test_all_regimes_render(self, regime):
        msg = format_predator_alert(
            _pred(regime=regime, suppressed=["short_squeeze"] if regime == "BEAR" else []),
            _elig(),
        )
        assert len(msg.strip()) > 0

    def test_missing_entry_renders(self):
        msg = format_predator_alert(_pred(entry=None, stop=None, position=None), _elig())
        assert "NVDA" in msg

    def test_zero_adj_score_renders(self):
        msg = format_predator_alert(_pred(adj=0.0), _elig(adj=0.0))
        assert len(msg.strip()) > 0

    def test_empty_active_signals_renders(self):
        msg = format_predator_alert(_pred(active=[]), _elig())
        assert "NVDA" in msg

    def test_many_active_signals_renders(self):
        sigs = ["options", "insider", "breakout", "short_squeeze", "catalyst", "institutional"]
        msg  = format_predator_alert(_pred(active=sigs), _elig())
        assert len(msg.strip()) > 0

    def test_output_contains_advisory_footer(self):
        msg = format_predator_alert(_pred(), _elig())
        assert "Advisory only" in msg

    def test_output_is_string(self):
        msg = format_predator_alert(_pred(), _elig())
        assert isinstance(msg, str)

    def test_long_reason_does_not_crash(self):
        sigs_meta = {"options": {"score": 3, "reason": "x" * 200, "data_quality": "HIGH"}}
        c   = _pred(active=["options"])
        c.metadata["signals"] = sigs_meta
        msg = format_predator_alert(c, _elig())
        assert len(msg) > 0

    @pytest.mark.parametrize("adj,conf", [
        (0.0, 0.0), (2.5, 50.0), (5.0, 100.0), (10.0, 100.0),
    ])
    def test_score_ranges_render(self, adj, conf):
        msg = format_predator_alert(_pred(adj=adj, conf=conf), _elig(adj=adj, conf=conf))
        assert len(msg.strip()) > 0


# ─────────────────────────────────────────────
# Sell formatter — does not crash
# ─────────────────────────────────────────────

class TestSellFormatterStability:
    @pytest.mark.parametrize("urgency", ["URGENT", "WARNING", "FYI"])
    def test_all_urgency_levels_render(self, urgency):
        e   = _elig("CONVICTION" if urgency == "URGENT" else "ALERT", 0.0, 0.0, f"urgency:{urgency}")
        msg = format_sell_alert_unified(_sell(urgency=urgency), e, shares=15, avg_cost=162.5)
        assert len(msg.strip()) > 0

    def test_zero_avg_cost_no_crash(self):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg = format_sell_alert_unified(_sell(), e, shares=15, avg_cost=0.0)
        assert "VFV.TO" in msg

    def test_zero_shares_renders(self):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg = format_sell_alert_unified(_sell(), e, shares=0, avg_cost=162.5)
        assert len(msg.strip()) > 0

    def test_no_active_signals_renders(self):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg = format_sell_alert_unified(_sell(active=[]), e, shares=10, avg_cost=160.0)
        assert "VFV.TO" in msg

    def test_positive_pct_1d_renders(self):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg = format_sell_alert_unified(_sell(pct_1d=+1.5), e, shares=10, avg_cost=160.0)
        assert len(msg.strip()) > 0

    def test_output_contains_advisory_footer(self):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg = format_sell_alert_unified(_sell(), e, shares=10, avg_cost=160.0)
        assert "Advisory only" in msg


# ─────────────────────────────────────────────
# Gateway dry-run stability
# ─────────────────────────────────────────────

class TestGatewayDryRunStability:
    def _dry_gateway(self, db_path):
        import alert_gateway as _gw_mod
        import alert_audit as _audit_mod
        import sqlite3 as sq

        def _get_conn():
            c = sq.connect(db_path, timeout=10)
            c.row_factory = sq.Row
            return c

        _gw_mod.get_connection    = _get_conn
        _audit_mod.get_connection = _get_conn

        from alert_gateway import AlertGateway
        return AlertGateway(NotificationPolicy.dry_run_mode(), send_fn=lambda m: True)

    def _setup_db(self, path):
        conn = sqlite3.connect(path)
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
        conn.execute("""
            CREATE TABLE notification_suppressed_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT, source TEXT, suppression_reasons TEXT,
                resolved_tier TEXT, adjusted_score REAL, confidence_pct REAL,
                evaluated_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    def test_dry_run_conviction_does_not_crash(self, tmp_path):
        db_path = str(tmp_path / "t.db")
        self._setup_db(db_path)
        gw     = self._dry_gateway(db_path)
        result = gw.submit(_pred())
        assert result is not None
        assert result.dry_run is True

    def test_dry_run_watch_returns_none(self, tmp_path):
        db_path = str(tmp_path / "t.db")
        self._setup_db(db_path)
        gw = self._dry_gateway(db_path)
        c  = _pred(tier="WATCH")
        # min_tier=ALERT → WATCH is suppressed
        result = gw.submit(c)
        assert result is None

    def test_dry_run_sell_urgent_does_not_crash(self, tmp_path):
        db_path = str(tmp_path / "t.db")
        self._setup_db(db_path)
        gw     = self._dry_gateway(db_path)
        result = gw.submit(_sell("URGENT"), shares=10, avg_cost=160.0)
        assert result is not None

    def test_dry_run_with_sparse_candidate_does_not_crash(self, tmp_path):
        db_path = str(tmp_path / "t.db")
        self._setup_db(db_path)
        gw = self._dry_gateway(db_path)
        c  = AlertCandidate(
            source="scanner", ticker="BBAI",
            raw_score=7.0, adjusted_score=4.9, confidence_pct=70.0,
            tier="ALERT", regime=None,
            active_signals=["RSI bullish", "MACD positive"],
            suppressed_signals=[],
            urgency=None, entry_price=12.0, stop_price=None,
            position_size_cad=None, risk_posture=None, metadata={},
        )
        result = gw.submit(c)
        assert result is not None
