"""
Phase N1 — Sparse / invalid input tests.

Verifies that all gateway functions handle:
  - None scores (sparse data)
  - Invalid score ranges
  - Empty signal lists
  - Missing optional fields
  - Zero-confidence candidates
  - Malformed tickers

All tested functions must either return a valid result or return an
ineligible EligibilityResult — they must never raise unhandled exceptions.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from alert_schema import AlertCandidate, EligibilityResult
from alert_eligibility import check_eligibility, _validate
from alert_formatter import format_predator_alert, format_sell_alert_unified
from notification_policy import NotificationPolicy


POLICY = NotificationPolicy.alert_and_above()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _cand(**kwargs):
    defaults = dict(
        source="predator", ticker="NVDA",
        raw_score=8.0, adjusted_score=4.4, confidence_pct=55.0,
        tier="CONVICTION", regime="NEUTRAL",
        active_signals=["options", "insider"],
        suppressed_signals=[],
        urgency=None, entry_price=150.0, stop_price=136.5,
        position_size_cad=5000.0, risk_posture=None, metadata={},
    )
    defaults.update(kwargs)
    return AlertCandidate(**defaults)


def _elig():
    return EligibilityResult(
        eligible=True, resolved_tier="CONVICTION",
        adjusted_score=4.4, confidence_pct=55.0,
        reasons=["tier:CONVICTION"], suppression_reasons=[],
        trigger_reason="tier:CONVICTION",
    )


# ─────────────────────────────────────────────
# Input validation
# ─────────────────────────────────────────────

class TestInputValidation:
    def test_missing_ticker_is_rejected(self):
        result = check_eligibility(_cand(ticker=""), POLICY)
        assert result.eligible is False
        assert any("missing_ticker" in r for r in result.suppression_reasons)

    def test_whitespace_ticker_is_rejected(self):
        result = check_eligibility(_cand(ticker="   "), POLICY)
        assert result.eligible is False

    def test_missing_source_is_rejected(self):
        result = check_eligibility(_cand(source=""), POLICY)
        assert result.eligible is False
        assert any("missing_source" in r for r in result.suppression_reasons)

    def test_raw_score_above_10_is_rejected(self):
        result = check_eligibility(_cand(raw_score=11.0), POLICY)
        assert result.eligible is False
        assert any("invalid_raw_score" in r for r in result.suppression_reasons)

    def test_negative_raw_score_is_rejected(self):
        result = check_eligibility(_cand(raw_score=-1.0), POLICY)
        assert result.eligible is False

    def test_negative_adjusted_score_is_rejected(self):
        result = check_eligibility(_cand(adjusted_score=-0.5), POLICY)
        assert result.eligible is False

    def test_confidence_above_100_is_rejected(self):
        result = check_eligibility(_cand(confidence_pct=101.0), POLICY)
        assert result.eligible is False

    def test_confidence_below_0_is_rejected(self):
        result = check_eligibility(_cand(confidence_pct=-5.0), POLICY)
        assert result.eligible is False


# ─────────────────────────────────────────────
# Sparse / None fields are handled gracefully
# ─────────────────────────────────────────────

class TestSparseFieldHandling:
    def test_none_raw_score_does_not_crash(self):
        result = check_eligibility(_cand(raw_score=None), POLICY)
        assert isinstance(result, EligibilityResult)

    def test_none_adjusted_score_resolves_to_zero(self):
        result = check_eligibility(_cand(adjusted_score=None), POLICY)
        assert result.adjusted_score == 0.0

    def test_none_confidence_resolves_to_zero(self):
        result = check_eligibility(_cand(confidence_pct=None), POLICY)
        assert result.confidence_pct == 0.0

    def test_none_tier_resolves_to_watch(self):
        result = check_eligibility(_cand(tier=None), POLICY)
        assert result.resolved_tier == "WATCH"

    def test_empty_active_signals_does_not_crash(self):
        result = check_eligibility(_cand(active_signals=[]), POLICY)
        assert isinstance(result, EligibilityResult)

    def test_none_regime_does_not_crash(self):
        result = check_eligibility(_cand(regime=None), POLICY)
        assert isinstance(result, EligibilityResult)

    def test_zero_confidence_eligible_when_no_floor(self):
        policy = NotificationPolicy(min_tier="ALERT", min_confidence_pct=0.0)
        result = check_eligibility(_cand(confidence_pct=0.0, tier="CONVICTION"), policy)
        assert result.eligible is True

    def test_sell_monitor_none_urgency_maps_to_watch(self):
        c      = _cand(source="sell_monitor", tier=None, urgency=None,
                       raw_score=None, adjusted_score=None, confidence_pct=None)
        result = check_eligibility(c, POLICY)
        # WATCH < min_tier=ALERT → suppressed
        assert result.eligible is False


# ─────────────────────────────────────────────
# Formatter sparse handling
# ─────────────────────────────────────────────

class TestFormatterSparseHandling:
    def test_format_predator_no_entry_price(self):
        c   = _cand(entry_price=None, stop_price=None, position_size_cad=None)
        msg = format_predator_alert(c, _elig())
        assert "NVDA" in msg   # must not crash, must contain ticker

    def test_format_predator_empty_signals(self):
        c   = _cand(active_signals=[], metadata={})
        msg = format_predator_alert(c, _elig())
        assert len(msg) > 0

    def test_format_predator_none_regime(self):
        c   = _cand(regime=None)
        msg = format_predator_alert(c, _elig())
        assert "NVDA" in msg

    def test_format_predator_with_suppressed_signals(self):
        c   = _cand(regime="BEAR", suppressed_signals=["short_squeeze", "catalyst"])
        msg = format_predator_alert(c, _elig())
        assert len(msg) > 0

    def test_format_sell_zero_avg_cost(self):
        c   = AlertCandidate(
            source="sell_monitor", ticker="VFV.TO",
            raw_score=None, adjusted_score=None, confidence_pct=None,
            tier=None, regime=None,
            active_signals=["Price below 50d MA"],
            suppressed_signals=[],
            urgency="URGENT", entry_price=158.0, stop_price=None,
            position_size_cad=None, risk_posture=None, metadata={"pct_1d": -2.1},
        )
        e   = EligibilityResult(True, "CONVICTION", 0.0, 0.0, [], [], "urgency:URGENT")
        msg = format_sell_alert_unified(c, e, shares=15, avg_cost=0.0)
        assert "VFV.TO" in msg   # must not divide-by-zero or crash

    def test_format_sell_empty_signals(self):
        c   = AlertCandidate(
            source="sell_monitor", ticker="VFV.TO",
            raw_score=None, adjusted_score=None, confidence_pct=None,
            tier=None, regime=None,
            active_signals=[], suppressed_signals=[],
            urgency="WARNING", entry_price=158.0, stop_price=None,
            position_size_cad=None, risk_posture=None, metadata={},
        )
        e   = EligibilityResult(True, "ALERT", 0.0, 0.0, [], [], "urgency:WARNING")
        msg = format_sell_alert_unified(c, e, shares=10, avg_cost=162.5)
        assert len(msg) > 0

    def test_format_produces_non_empty_output_always(self):
        c   = _cand(
            raw_score=None, adjusted_score=None, confidence_pct=None,
            tier="CONVICTION", entry_price=None, stop_price=None,
            position_size_cad=None, active_signals=[], metadata={},
        )
        e   = EligibilityResult(True, "CONVICTION", 0.0, 0.0, [], [], "tier:CONVICTION")
        msg = format_predator_alert(c, e)
        assert len(msg.strip()) > 0


# ─────────────────────────────────────────────
# _validate helper coverage
# ─────────────────────────────────────────────

class TestValidateHelper:
    def test_valid_candidate_no_issues(self):
        issues = _validate(_cand())
        assert issues == []

    def test_multiple_issues_all_reported(self):
        issues = _validate(_cand(ticker="", source="", raw_score=12.0))
        assert len(issues) >= 2

    def test_boundary_raw_score_10_valid(self):
        assert _validate(_cand(raw_score=10.0)) == []

    def test_boundary_raw_score_0_valid(self):
        assert _validate(_cand(raw_score=0.0)) == []

    def test_boundary_confidence_100_valid(self):
        assert _validate(_cand(confidence_pct=100.0)) == []

    def test_boundary_confidence_0_valid(self):
        assert _validate(_cand(confidence_pct=0.0)) == []
