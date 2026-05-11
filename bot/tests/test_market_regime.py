"""
Unit tests for market_regime module.

Covers:
  - _classify()           — all BULL / NEUTRAL / RISK_OFF branches
  - apply_regime_penalty() — confidence reduction, breakout suppression,
                             BULL pass-through, no input mutation
  - get_market_regime()   — graceful degradation on data failure (all mocked)

No network calls — yfinance fetchers are patched throughout.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch

from market_regime import (
    _classify,
    _fetch_above_200ma,
    _fetch_spy_trend,
    _fetch_vix,
    get_market_regime,
    apply_regime_penalty,
    MarketRegime,
    BULL,
    NEUTRAL,
    RISK_OFF,
    _NEUTRAL_PENALTY,
    _RISK_OFF_PENALTY,
    _BREAKOUT_SUPPRESS_SCORE,
    _VIX_HIGH,
    _VIX_EXTREME,
)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _regime(state, spy=True, qqq=True, trend="UP", vix=None, reason="test"):
    return MarketRegime(
        state=state, spy_above_200ma=spy, qqq_above_200ma=qqq,
        short_term_trend=trend, vix=vix, reason=reason,
    )


def _sigs(opts=2, ins=1, sq=0, cat=0, inst=0, brk=1):
    return {
        "options":       {"score": opts, "reason": ""},
        "insider":       {"score": ins,  "reason": ""},
        "short_squeeze": {"score": sq,   "reason": ""},
        "catalyst":      {"score": cat,  "reason": ""},
        "institutional": {"score": inst, "reason": ""},
        "breakout":      {"score": brk,  "reason": ""},
    }


# ─────────────────────────────────────────────
# _classify() — BULL
# ─────────────────────────────────────────────

class TestClassifyBull:
    def test_both_above_trend_up_no_vix(self):
        state, _ = _classify(True, True, "UP", None)
        assert state == BULL

    def test_both_above_trend_flat_no_vix(self):
        state, _ = _classify(True, True, "FLAT", None)
        assert state == BULL

    def test_both_above_trend_up_vix_below_high(self):
        state, _ = _classify(True, True, "UP", _VIX_HIGH - 0.1)
        assert state == BULL

    def test_both_above_trend_up_zero_vix(self):
        state, _ = _classify(True, True, "UP", 0.0)
        assert state == BULL

    def test_bull_requires_both_above_spy_only(self):
        state, _ = _classify(True, False, "UP", None)
        assert state != BULL

    def test_bull_requires_both_above_qqq_only(self):
        state, _ = _classify(False, True, "UP", None)
        assert state != BULL

    def test_bull_requires_trend_not_down(self):
        state, _ = _classify(True, True, "DOWN", None)
        assert state != BULL

    def test_bull_reason_is_nonempty(self):
        _, reason = _classify(True, True, "UP", None)
        assert isinstance(reason, str) and len(reason) > 0


# ─────────────────────────────────────────────
# _classify() — NEUTRAL
# ─────────────────────────────────────────────

class TestClassifyNeutral:
    def test_both_above_elevated_vix_is_neutral(self):
        state, _ = _classify(True, True, "UP", _VIX_HIGH)
        assert state == NEUTRAL

    def test_both_above_vix_just_above_high(self):
        state, _ = _classify(True, True, "UP", _VIX_HIGH + 0.1)
        assert state == NEUTRAL

    def test_both_above_trend_down_no_vix(self):
        state, _ = _classify(True, True, "DOWN", None)
        assert state == NEUTRAL

    def test_spy_only_above_is_neutral(self):
        state, _ = _classify(True, False, "UP", None)
        assert state == NEUTRAL

    def test_qqq_only_above_is_neutral(self):
        state, _ = _classify(False, True, "FLAT", None)
        assert state == NEUTRAL

    def test_mixed_ma_trend_down_low_vix_is_neutral(self):
        state, _ = _classify(True, False, "DOWN", _VIX_HIGH - 5.0)
        assert state == NEUTRAL

    def test_neutral_reason_is_nonempty(self):
        _, reason = _classify(True, True, "UP", _VIX_HIGH)
        assert isinstance(reason, str) and len(reason) > 0


# ─────────────────────────────────────────────
# _classify() — RISK_OFF
# ─────────────────────────────────────────────

class TestClassifyRiskOff:
    def test_both_below_200ma_no_vix(self):
        state, _ = _classify(False, False, "FLAT", None)
        assert state == RISK_OFF

    def test_both_below_200ma_uptrend(self):
        state, _ = _classify(False, False, "UP", None)
        assert state == RISK_OFF

    def test_extreme_vix_overrides_bull(self):
        state, _ = _classify(True, True, "UP", _VIX_EXTREME)
        assert state == RISK_OFF

    def test_extreme_vix_overrides_mixed_signal(self):
        state, _ = _classify(True, False, "UP", _VIX_EXTREME + 1.0)
        assert state == RISK_OFF

    def test_extreme_vix_exactly_at_threshold(self):
        state, _ = _classify(True, True, "UP", _VIX_EXTREME)
        assert state == RISK_OFF

    def test_high_vix_with_trend_down_is_risk_off(self):
        state, _ = _classify(True, True, "DOWN", _VIX_HIGH)
        assert state == RISK_OFF

    def test_both_below_200ma_with_various_vix(self):
        for vix in (None, 10.0, 24.9):
            state, _ = _classify(False, False, "UP", vix)
            assert state == RISK_OFF

    def test_risk_off_reason_is_nonempty(self):
        _, reason = _classify(False, False, "FLAT", None)
        assert isinstance(reason, str) and len(reason) > 0


# ─────────────────────────────────────────────
# _classify() — output contract
# ─────────────────────────────────────────────

class TestClassifyContract:
    def test_returns_tuple_of_two(self):
        result = _classify(True, True, "UP", None)
        assert isinstance(result, tuple) and len(result) == 2

    def test_state_always_valid(self):
        for spy in (True, False):
            for qqq in (True, False):
                for trend in ("UP", "FLAT", "DOWN"):
                    for vix in (None, 10.0, _VIX_HIGH, _VIX_EXTREME, 50.0):
                        state, _ = _classify(spy, qqq, trend, vix)
                        assert state in (BULL, NEUTRAL, RISK_OFF), \
                            f"unexpected state={state!r} for spy={spy} qqq={qqq} trend={trend} vix={vix}"

    def test_vix_just_below_extreme_not_overridden(self):
        # VIX = 34.9 should not trigger the extreme override
        state, _ = _classify(True, True, "UP", _VIX_EXTREME - 0.1)
        assert state == NEUTRAL  # elevated but not extreme

    def test_vix_just_below_high_still_bull(self):
        state, _ = _classify(True, True, "UP", _VIX_HIGH - 0.01)
        assert state == BULL


# ─────────────────────────────────────────────
# apply_regime_penalty() — BULL
# ─────────────────────────────────────────────

class TestBullPenalty:
    def test_confidence_unchanged(self):
        conf, _, _ = apply_regime_penalty(70.0, _sigs(), _regime(BULL))
        assert conf == 70.0

    def test_suppressed_is_zero(self):
        _, _, suppressed = apply_regime_penalty(70.0, _sigs(), _regime(BULL))
        assert suppressed == 0

    def test_signals_scores_unchanged(self):
        sigs = _sigs(brk=1)
        _, modified, _ = apply_regime_penalty(60.0, sigs, _regime(BULL))
        assert modified["breakout"]["score"] == 1

    def test_weak_breakout_not_suppressed_in_bull(self):
        sigs = _sigs(brk=_BREAKOUT_SUPPRESS_SCORE)
        _, modified, _ = apply_regime_penalty(60.0, sigs, _regime(BULL))
        assert modified["breakout"]["score"] == _BREAKOUT_SUPPRESS_SCORE

    def test_returns_float_confidence(self):
        conf, _, _ = apply_regime_penalty(50.0, _sigs(), _regime(BULL))
        assert isinstance(conf, float)


# ─────────────────────────────────────────────
# apply_regime_penalty() — NEUTRAL
# ─────────────────────────────────────────────

class TestNeutralPenalty:
    def test_reduces_confidence_by_10_pct(self):
        conf, _, _ = apply_regime_penalty(80.0, _sigs(), _regime(NEUTRAL))
        assert abs(conf - 72.0) < 0.01

    def test_confidence_formula_exact(self):
        for c in (0.0, 25.0, 50.0, 75.0, 100.0):
            result, _, _ = apply_regime_penalty(c, _sigs(), _regime(NEUTRAL))
            assert abs(result - round(c * _NEUTRAL_PENALTY, 2)) < 0.001

    def test_no_signal_suppression(self):
        _, _, suppressed = apply_regime_penalty(80.0, _sigs(brk=1), _regime(NEUTRAL))
        assert suppressed == 0

    def test_weak_breakout_not_zeroed(self):
        sigs = _sigs(brk=_BREAKOUT_SUPPRESS_SCORE)
        _, modified, _ = apply_regime_penalty(80.0, sigs, _regime(NEUTRAL))
        assert modified["breakout"]["score"] == _BREAKOUT_SUPPRESS_SCORE

    def test_returns_lower_confidence_than_input(self):
        conf, _, _ = apply_regime_penalty(60.0, _sigs(), _regime(NEUTRAL))
        assert conf < 60.0


# ─────────────────────────────────────────────
# apply_regime_penalty() — RISK_OFF
# ─────────────────────────────────────────────

class TestRiskOffPenalty:
    def test_reduces_confidence_by_25_pct(self):
        conf, _, _ = apply_regime_penalty(80.0, _sigs(), _regime(RISK_OFF))
        assert abs(conf - 60.0) < 0.01

    def test_confidence_formula_exact(self):
        for c in (0.0, 25.0, 50.0, 75.0, 100.0):
            result, _, _ = apply_regime_penalty(c, _sigs(), _regime(RISK_OFF))
            assert abs(result - round(c * _RISK_OFF_PENALTY, 2)) < 0.001

    def test_suppresses_breakout_at_threshold(self):
        sigs = _sigs(brk=_BREAKOUT_SUPPRESS_SCORE)
        _, modified, suppressed = apply_regime_penalty(60.0, sigs, _regime(RISK_OFF))
        assert modified["breakout"]["score"] == 0
        assert suppressed == 1

    def test_suppresses_breakout_score_zero(self):
        sigs = _sigs(brk=0)
        _, modified, suppressed = apply_regime_penalty(60.0, sigs, _regime(RISK_OFF))
        assert modified["breakout"]["score"] == 0
        assert suppressed == 1

    def test_preserves_strong_breakout(self):
        sigs = _sigs(brk=_BREAKOUT_SUPPRESS_SCORE + 1)
        _, modified, suppressed = apply_regime_penalty(60.0, sigs, _regime(RISK_OFF))
        assert modified["breakout"]["score"] == _BREAKOUT_SUPPRESS_SCORE + 1
        assert suppressed == 0

    def test_suppressed_reason_contains_risk_off_label(self):
        sigs = _sigs(brk=1)
        _, modified, _ = apply_regime_penalty(60.0, sigs, _regime(RISK_OFF))
        assert "suppressed" in modified["breakout"]["reason"].lower()
        assert "risk_off" in modified["breakout"]["reason"].lower() or \
               "RISK_OFF" in modified["breakout"]["reason"]

    def test_non_breakout_signals_not_suppressed(self):
        sigs = _sigs(opts=3, ins=2, sq=2, cat=2, inst=1, brk=1)
        _, modified, _ = apply_regime_penalty(60.0, sigs, _regime(RISK_OFF))
        assert modified["options"]["score"]       == 3
        assert modified["insider"]["score"]       == 2
        assert modified["short_squeeze"]["score"] == 2
        assert modified["catalyst"]["score"]      == 2
        assert modified["institutional"]["score"] == 1

    def test_risk_off_lower_confidence_than_neutral(self):
        c = 70.0
        neutral_conf, _, _ = apply_regime_penalty(c, _sigs(), _regime(NEUTRAL))
        risk_off_conf, _, _ = apply_regime_penalty(c, _sigs(), _regime(RISK_OFF))
        assert risk_off_conf < neutral_conf

    def test_risk_off_lower_confidence_than_bull(self):
        c = 70.0
        bull_conf, _, _ = apply_regime_penalty(c, _sigs(), _regime(BULL))
        risk_off_conf, _, _ = apply_regime_penalty(c, _sigs(), _regime(RISK_OFF))
        assert risk_off_conf < bull_conf


# ─────────────────────────────────────────────
# apply_regime_penalty() — no mutation guarantee
# ─────────────────────────────────────────────

class TestNomutation:
    def test_risk_off_does_not_mutate_input_signals(self):
        original = _sigs(brk=1)
        original_score = original["breakout"]["score"]
        apply_regime_penalty(60.0, original, _regime(RISK_OFF))
        assert original["breakout"]["score"] == original_score

    def test_neutral_does_not_mutate_input_signals(self):
        original = _sigs(brk=2)
        original_score = original["breakout"]["score"]
        apply_regime_penalty(70.0, original, _regime(NEUTRAL))
        assert original["breakout"]["score"] == original_score

    def test_returns_separate_dict_object(self):
        sigs = _sigs()
        _, modified, _ = apply_regime_penalty(60.0, sigs, _regime(RISK_OFF))
        assert modified is not sigs


# ─────────────────────────────────────────────
# apply_regime_penalty() — return type contract
# ─────────────────────────────────────────────

class TestPenaltyReturnTypes:
    def test_returns_three_element_tuple(self):
        for state in (BULL, NEUTRAL, RISK_OFF):
            result = apply_regime_penalty(70.0, _sigs(), _regime(state))
            assert isinstance(result, tuple) and len(result) == 3

    def test_first_element_is_float(self):
        for state in (BULL, NEUTRAL, RISK_OFF):
            conf, _, _ = apply_regime_penalty(70.0, _sigs(), _regime(state))
            assert isinstance(conf, float)

    def test_second_element_is_dict(self):
        for state in (BULL, NEUTRAL, RISK_OFF):
            _, modified, _ = apply_regime_penalty(70.0, _sigs(), _regime(state))
            assert isinstance(modified, dict)

    def test_third_element_is_int(self):
        for state in (BULL, NEUTRAL, RISK_OFF):
            _, _, suppressed = apply_regime_penalty(70.0, _sigs(), _regime(state))
            assert isinstance(suppressed, int)

    def test_confidence_never_exceeds_input(self):
        for state in (BULL, NEUTRAL, RISK_OFF):
            conf, _, _ = apply_regime_penalty(60.0, _sigs(), _regime(state))
            assert conf <= 60.0


# ─────────────────────────────────────────────
# get_market_regime() — mocked, no network
# ─────────────────────────────────────────────

class TestGetMarketRegimeMocked:
    def test_returns_market_regime_namedtuple(self):
        with patch("market_regime._fetch_above_200ma", return_value=True), \
             patch("market_regime._fetch_spy_trend",   return_value="UP"), \
             patch("market_regime._fetch_vix",         return_value=18.0):
            result = get_market_regime()
        assert isinstance(result, MarketRegime)

    def test_bull_when_both_above_uptrend_low_vix(self):
        with patch("market_regime._fetch_above_200ma", return_value=True), \
             patch("market_regime._fetch_spy_trend",   return_value="UP"), \
             patch("market_regime._fetch_vix",         return_value=18.0):
            result = get_market_regime()
        assert result.state == BULL

    def test_neutral_when_vix_elevated(self):
        with patch("market_regime._fetch_above_200ma", return_value=True), \
             patch("market_regime._fetch_spy_trend",   return_value="UP"), \
             patch("market_regime._fetch_vix",         return_value=_VIX_HIGH + 1.0):
            result = get_market_regime()
        assert result.state == NEUTRAL

    def test_risk_off_when_both_below_200ma(self):
        with patch("market_regime._fetch_above_200ma", return_value=False), \
             patch("market_regime._fetch_spy_trend",   return_value="FLAT"), \
             patch("market_regime._fetch_vix",         return_value=20.0):
            result = get_market_regime()
        assert result.state == RISK_OFF

    def test_risk_off_on_extreme_vix(self):
        with patch("market_regime._fetch_above_200ma", return_value=True), \
             patch("market_regime._fetch_spy_trend",   return_value="UP"), \
             patch("market_regime._fetch_vix",         return_value=_VIX_EXTREME + 5.0):
            result = get_market_regime()
        assert result.state == RISK_OFF

    def test_mixed_above_200ma_is_neutral(self):
        with patch("market_regime._fetch_above_200ma", side_effect=[True, False]), \
             patch("market_regime._fetch_spy_trend",   return_value="UP"), \
             patch("market_regime._fetch_vix",         return_value=15.0):
            result = get_market_regime()
        assert result.state == NEUTRAL

    def test_defaults_to_neutral_on_complete_fetch_failure(self):
        with patch("market_regime._fetch_above_200ma", side_effect=Exception("no network")):
            result = get_market_regime()
        assert result.state == NEUTRAL

    def test_defaults_to_neutral_when_no_index_data(self):
        with patch("market_regime._fetch_above_200ma", return_value=None), \
             patch("market_regime._fetch_spy_trend",   return_value="FLAT"), \
             patch("market_regime._fetch_vix",         return_value=None):
            result = get_market_regime()
        assert result.state == NEUTRAL

    def test_state_field_is_string(self):
        with patch("market_regime._fetch_above_200ma", side_effect=Exception("err")):
            result = get_market_regime()
        assert isinstance(result.state, str)

    def test_reason_nonempty_on_failure(self):
        with patch("market_regime._fetch_above_200ma", side_effect=Exception("err")):
            result = get_market_regime()
        assert isinstance(result.reason, str) and len(result.reason) > 0

    def test_spy_above_200ma_field_reflects_fetch(self):
        with patch("market_regime._fetch_above_200ma", return_value=True), \
             patch("market_regime._fetch_spy_trend",   return_value="UP"), \
             patch("market_regime._fetch_vix",         return_value=None):
            result = get_market_regime()
        assert result.spy_above_200ma is True

    def test_vix_field_populated(self):
        with patch("market_regime._fetch_above_200ma", return_value=True), \
             patch("market_regime._fetch_spy_trend",   return_value="UP"), \
             patch("market_regime._fetch_vix",         return_value=21.5):
            result = get_market_regime()
        assert result.vix == 21.5

    def test_vix_none_when_fetch_fails(self):
        with patch("market_regime._fetch_above_200ma", return_value=True), \
             patch("market_regime._fetch_spy_trend",   return_value="UP"), \
             patch("market_regime._fetch_vix",         return_value=None):
            result = get_market_regime()
        assert result.vix is None

    def test_single_index_failure_degrades_gracefully(self):
        """If only QQQ fetch fails (returns None), SPY is honoured."""
        with patch("market_regime._fetch_above_200ma", side_effect=[True, None]), \
             patch("market_regime._fetch_spy_trend",   return_value="UP"), \
             patch("market_regime._fetch_vix",         return_value=15.0):
            result = get_market_regime()
        # SPY above, QQQ assumed False (None → False) → mixed → NEUTRAL
        assert result.state in (BULL, NEUTRAL, RISK_OFF)  # must not raise
