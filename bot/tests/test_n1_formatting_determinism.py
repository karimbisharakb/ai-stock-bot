"""
Phase N1 — Formatting determinism tests.

Tests that format_predator_alert() and format_sell_alert_unified() are:
  - deterministic (same inputs → same output)
  - complete (contain tier, adj score, confidence, signals, trigger reason)
  - free of legacy "PRE-EXPLOSION ALERT" and raw additive score presentation
  - robust with missing / None fields
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from alert_schema import AlertCandidate, EligibilityResult
from alert_formatter import (
    format_predator_alert,
    format_sell_alert_unified,
    format_alert,
    interpret_suppression_reasons,
)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _elig(tier="CONVICTION", adj=4.4, conf=55.0, trigger="tier:CONVICTION | adj_score:4.40"):
    return EligibilityResult(
        eligible=True,
        resolved_tier=tier,
        adjusted_score=adj,
        confidence_pct=conf,
        reasons=[f"tier:{tier}"],
        suppression_reasons=[],
        trigger_reason=trigger,
    )


def _predator_cand(tier="CONVICTION", adj=4.4, conf=55.0, regime=None, signals=None,
                   suppressed=None):
    raw_sigs = signals or {
        "options":  {"score": 3, "reason": "unusual calls", "data_quality": "HIGH"},
        "insider":  {"score": 2, "reason": "3 insiders", "data_quality": "MEDIUM"},
        "breakout": {"score": 2, "reason": "near 52wk high", "data_quality": "MEDIUM"},
    }
    return AlertCandidate(
        source="predator", ticker="NVDA",
        raw_score=8.0, adjusted_score=adj, confidence_pct=conf,
        tier=tier, regime=regime,
        active_signals=["options", "insider", "breakout"],
        suppressed_signals=suppressed or [],
        urgency=None, entry_price=150.0, stop_price=136.5,
        position_size_cad=5000.0, risk_posture=None,
        metadata={"signals": raw_sigs},
    )


def _sell_cand(urgency="URGENT"):
    return AlertCandidate(
        source="sell_monitor", ticker="VFV.TO",
        raw_score=None, adjusted_score=None, confidence_pct=None,
        tier=None, regime=None,
        active_signals=["Price broke below 50-day MA", "MACD bearish cross"],
        suppressed_signals=[],
        urgency=urgency, entry_price=158.0, stop_price=None,
        position_size_cad=None, risk_posture="High",
        metadata={"pct_1d": -2.1},
    )


# ─────────────────────────────────────────────
# Determinism
# ─────────────────────────────────────────────

class TestDeterminism:
    def test_predator_same_inputs_same_output(self):
        c, e = _predator_cand(), _elig()
        assert format_predator_alert(c, e) == format_predator_alert(c, e)

    def test_sell_same_inputs_same_output(self):
        c, e = _sell_cand(), _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        out1 = format_sell_alert_unified(c, e, shares=15, avg_cost=162.5)
        out2 = format_sell_alert_unified(c, e, shares=15, avg_cost=162.5)
        assert out1 == out2

    def test_predator_output_stable_across_calls(self):
        c, e = _predator_cand(), _elig()
        outputs = [format_predator_alert(c, e) for _ in range(5)]
        assert len(set(outputs)) == 1  # all identical

    def test_format_alert_dispatcher_predator(self):
        c, e   = _predator_cand(), _elig()
        direct = format_predator_alert(c, e)
        via_fn = format_alert(c, e)
        assert direct == via_fn

    def test_format_alert_dispatcher_sell_monitor(self):
        c, e   = _sell_cand(), _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        direct = format_sell_alert_unified(c, e, shares=10, avg_cost=160.0)
        via_fn = format_alert(c, e, shares=10, avg_cost=160.0)
        assert direct == via_fn


# ─────────────────────────────────────────────
# Required content — predator
# ─────────────────────────────────────────────

class TestPredatorAlertContent:
    def test_ticker_in_output(self):
        msg = format_predator_alert(_predator_cand(), _elig())
        assert "NVDA" in msg

    def test_tier_label_in_output(self):
        msg = format_predator_alert(_predator_cand(), _elig("CONVICTION"))
        assert "CONVICTION" in msg

    def test_adjusted_score_in_output(self):
        msg = format_predator_alert(_predator_cand(adj=4.4), _elig(adj=4.4))
        assert "4.4" in msg

    def test_confidence_in_output(self):
        msg = format_predator_alert(_predator_cand(conf=55.0), _elig(conf=55.0))
        assert "55" in msg

    def test_adjusted_score_shown(self):
        msg = format_predator_alert(_predator_cand(), _elig())
        assert "Adjusted score:" in msg

    def test_active_signals_in_output(self):
        msg = format_predator_alert(_predator_cand(), _elig())
        assert "Options" in msg or "options" in msg.lower()

    def test_signals_section_in_output(self):
        e   = _elig(trigger="tier:CONVICTION | adj_score:4.40 | confidence:55%")
        msg = format_predator_alert(_predator_cand(), e)
        assert "Why this alert:" in msg

    def test_entry_price_in_output(self):
        msg = format_predator_alert(_predator_cand(), _elig())
        assert "150.00" in msg

    def test_stop_price_in_output(self):
        msg = format_predator_alert(_predator_cand(), _elig())
        assert "136.5" in msg or "136" in msg

    def test_position_size_in_output(self):
        msg = format_predator_alert(_predator_cand(), _elig())
        assert "5,000" in msg or "5000" in msg

    def test_regime_shown_when_bear(self):
        c   = _predator_cand(regime="BEAR")
        msg = format_predator_alert(c, _elig())
        assert "Weak" in msg or "Market:" in msg

    def test_suppressed_signals_shown_when_present(self):
        c   = _predator_cand(regime="BEAR", suppressed=["short_squeeze"])
        msg = format_predator_alert(c, _elig())
        assert "squeeze" in msg.lower() or "Watch:" in msg


# ─────────────────────────────────────────────
# Legacy format not present — predator
# ─────────────────────────────────────────────

class TestLegacyFormatAbsent:
    def test_no_pre_explosion_header(self):
        msg = format_predator_alert(_predator_cand(), _elig())
        assert "PRE-EXPLOSION" not in msg.upper()

    def test_no_legacy_score_slash_ten_as_trigger(self):
        # "Score: 8/10" in isolation (first line after header) must not appear
        msg = format_predator_alert(_predator_cand(), _elig())
        first_lines = "\n".join(msg.split("\n")[:3])
        assert "Score: 8/10" not in first_lines

    def test_alert_tier_has_no_pre_explosion_header(self):
        c   = _predator_cand(tier="ALERT")
        msg = format_predator_alert(c, _elig("ALERT", adj=3.0))
        assert "PRE-EXPLOSION" not in msg.upper()


# ─────────────────────────────────────────────
# Sell-monitor content
# ─────────────────────────────────────────────

class TestSellAlertContent:
    def test_ticker_in_output(self):
        c, e = _sell_cand(), _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg  = format_sell_alert_unified(c, e, shares=15, avg_cost=162.5)
        assert "VFV.TO" in msg

    def test_urgency_label_in_output(self):
        c, e = _sell_cand("URGENT"), _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg  = format_sell_alert_unified(c, e, shares=15, avg_cost=162.5)
        assert "URGENT" in msg

    def test_warning_label_in_output(self):
        c, e = _sell_cand("WARNING"), _elig("ALERT", 0.0, 0.0, "urgency:WARNING")
        msg  = format_sell_alert_unified(c, e, shares=15, avg_cost=162.5)
        assert "WARNING" in msg

    def test_shares_and_avg_cost_in_output(self):
        c, e = _sell_cand(), _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg  = format_sell_alert_unified(c, e, shares=15, avg_cost=162.50)
        assert "15" in msg
        assert "162.50" in msg

    def test_active_signals_in_output(self):
        c, e = _sell_cand(), _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg  = format_sell_alert_unified(c, e, shares=15, avg_cost=162.5)
        assert "50-day" in msg or "MACD" in msg

    def test_wealthsimple_action_prompt(self):
        c, e = _sell_cand(), _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg  = format_sell_alert_unified(c, e, shares=15, avg_cost=162.5)
        assert "Wealthsimple" in msg


# ─────────────────────────────────────────────
# Suppression reason interpreter
# ─────────────────────────────────────────────

class TestSuppressionReasonInterpreter:
    def test_snoozed_reason(self):
        reasons = interpret_suppression_reasons(snoozed=True)
        assert any("snoozed" in r for r in reasons)

    def test_quiet_hours_reason(self):
        reasons = interpret_suppression_reasons(quiet_hours=True)
        assert any("quiet" in r for r in reasons)

    def test_duplicate_reason(self):
        reasons = interpret_suppression_reasons(duplicate=True)
        assert any("duplicate" in r for r in reasons)

    def test_tier_below_min_reason(self):
        reasons = interpret_suppression_reasons(
            tier_below_min=True, tier_label="ALERT", min_tier_label="CONVICTION"
        )
        assert any("ALERT" in r for r in reasons)

    def test_multiple_reasons_combined(self):
        reasons = interpret_suppression_reasons(snoozed=True, quiet_hours=True, duplicate=True)
        assert len(reasons) == 3

    def test_no_flags_returns_empty(self):
        assert interpret_suppression_reasons() == []
