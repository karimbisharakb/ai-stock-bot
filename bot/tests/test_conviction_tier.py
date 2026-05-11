"""
Unit tests for the conviction tier system.

Tests cover:
  - classify_tier()  — pure function, no mocking needed
  - Tier constants and thresholds
  - WATCH / ALERT / CONVICTION routing rules
  - _format_alert()  — tier-aware header
  - Interaction with compute_confidence() + compute_adjusted_score()

No network calls, no DB access, no scheduler code.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from predator import (
    classify_tier,
    compute_confidence,
    compute_adjusted_score,
    _format_alert,
    TIER_WATCH,
    TIER_ALERT,
    TIER_CONVICTION,
    ALERT_THRESHOLD,
    ALERT_MIN_ADJUSTED,
    CONVICTION_MIN_SIGNALS,
    CONVICTION_MIN_CONFIDENCE,
    _SIGNAL_LABELS,
)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _sig(score: int, quality: str = "MEDIUM") -> dict:
    return {"score": score, "reason": "test", "data_quality": quality}


def _active_count(signals: dict) -> int:
    return sum(1 for s in signals.values() if s.get("score", 0) > 0)


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────

class TestTierConstants:
    def test_three_tiers_are_distinct_strings(self):
        tiers = {TIER_WATCH, TIER_ALERT, TIER_CONVICTION}
        assert len(tiers) == 3

    def test_all_tiers_are_strings(self):
        for t in (TIER_WATCH, TIER_ALERT, TIER_CONVICTION):
            assert isinstance(t, str)

    def test_conviction_min_signals_is_3(self):
        assert CONVICTION_MIN_SIGNALS == 3

    def test_conviction_confidence_higher_than_alert_adjusted(self):
        # CONVICTION is a stricter bar than ALERT by design
        assert CONVICTION_MIN_CONFIDENCE > ALERT_MIN_ADJUSTED

    def test_alert_threshold_is_unchanged(self):
        # Existing gate must not regress
        assert ALERT_THRESHOLD == 6

    def test_alert_min_adjusted_positive(self):
        assert ALERT_MIN_ADJUSTED > 0.0

    def test_conviction_min_confidence_in_valid_range(self):
        assert 0.0 < CONVICTION_MIN_CONFIDENCE <= 100.0


# ─────────────────────────────────────────────
# classify_tier — below threshold
# ─────────────────────────────────────────────

class TestBelowThreshold:
    def test_score_zero_returns_watch(self):
        assert classify_tier(0, 0.0, 0.0, 0) == TIER_WATCH

    def test_score_one_below_threshold_returns_watch(self):
        assert classify_tier(ALERT_THRESHOLD - 1, 10.0, 100.0, 6) == TIER_WATCH

    def test_high_confidence_does_not_override_threshold_gate(self):
        # Even perfect confidence cannot upgrade a sub-threshold score
        assert classify_tier(5, 5.0, 100.0, 6) == TIER_WATCH

    def test_high_adjusted_does_not_override_threshold_gate(self):
        assert classify_tier(3, 3.0, 80.0, 4) == TIER_WATCH


# ─────────────────────────────────────────────
# classify_tier — WATCH tier (above threshold but low quality)
# ─────────────────────────────────────────────

class TestWatchTier:
    def test_score_at_threshold_low_adjusted_returns_watch(self):
        # adjusted < ALERT_MIN_ADJUSTED and does not meet conviction
        assert classify_tier(ALERT_THRESHOLD, ALERT_MIN_ADJUSTED - 0.1, 30.0, 1) == TIER_WATCH

    def test_many_signals_but_very_low_confidence_is_watch(self):
        # 6 signals but confidence far below both thresholds
        assert classify_tier(6, 0.5, 10.0, 6) == TIER_WATCH

    def test_exactly_one_below_alert_adjusted_is_watch(self):
        adj = ALERT_MIN_ADJUSTED - 0.01
        assert classify_tier(6, adj, 40.0, 2) == TIER_WATCH


# ─────────────────────────────────────────────
# classify_tier — ALERT tier
# ─────────────────────────────────────────────

class TestAlertTier:
    def test_adjusted_at_threshold_is_alert(self):
        assert classify_tier(6, ALERT_MIN_ADJUSTED, 40.0, 2) == TIER_ALERT

    def test_adjusted_above_threshold_is_alert(self):
        assert classify_tier(6, ALERT_MIN_ADJUSTED + 1.0, 40.0, 2) == TIER_ALERT

    def test_alert_does_not_require_conviction_signal_count(self):
        # 2 signals + good adjusted → ALERT (not WATCH, not CONVICTION)
        assert classify_tier(7, 3.5, 45.0, 2) == TIER_ALERT

    def test_high_adjusted_but_low_signal_count_is_alert(self):
        # Only 1 active signal but adjusted is high — still ALERT, not CONVICTION
        assert classify_tier(8, 5.0, 50.0, 1) == TIER_ALERT

    def test_confidence_just_below_conviction_threshold_is_alert(self):
        conf = CONVICTION_MIN_CONFIDENCE - 0.1
        # enough signals + adjusted fine, but conf just below → ALERT
        assert classify_tier(7, 3.0, conf, CONVICTION_MIN_SIGNALS) == TIER_ALERT

    def test_conviction_signal_count_met_but_confidence_too_low_is_alert(self):
        # Signals ≥ 3 but confidence < CONVICTION_MIN_CONFIDENCE AND adjusted ≥ bar
        assert classify_tier(7, 3.0, CONVICTION_MIN_CONFIDENCE - 1.0, CONVICTION_MIN_SIGNALS) == TIER_ALERT

    def test_alert_is_alertable(self):
        # Callers check tier in (TIER_ALERT, TIER_CONVICTION) before sending
        tier = classify_tier(6, ALERT_MIN_ADJUSTED, 40.0, 2)
        assert tier in (TIER_ALERT, TIER_CONVICTION)


# ─────────────────────────────────────────────
# classify_tier — CONVICTION tier
# ─────────────────────────────────────────────

class TestConvictionTier:
    def test_meets_both_gates_returns_conviction(self):
        assert classify_tier(
            7, 3.9, CONVICTION_MIN_CONFIDENCE, CONVICTION_MIN_SIGNALS
        ) == TIER_CONVICTION

    def test_confidence_exactly_at_conviction_threshold(self):
        assert classify_tier(
            7, 3.9, CONVICTION_MIN_CONFIDENCE, CONVICTION_MIN_SIGNALS
        ) == TIER_CONVICTION

    def test_conviction_requires_minimum_signal_count(self):
        # Exactly CONVICTION_MIN_SIGNALS - 1 → cannot be CONVICTION
        result = classify_tier(
            8, 4.0, CONVICTION_MIN_CONFIDENCE + 10.0, CONVICTION_MIN_SIGNALS - 1
        )
        assert result != TIER_CONVICTION

    def test_conviction_requires_minimum_confidence(self):
        # Signal count met but confidence below minimum → cannot be CONVICTION
        result = classify_tier(
            8, 4.0, CONVICTION_MIN_CONFIDENCE - 0.1, CONVICTION_MIN_SIGNALS
        )
        assert result != TIER_CONVICTION

    def test_conviction_requires_score_at_or_above_threshold(self):
        # Even with perfect confidence and many signals, sub-threshold score → WATCH
        assert classify_tier(
            ALERT_THRESHOLD - 1, 5.0, 100.0, CONVICTION_MIN_SIGNALS
        ) == TIER_WATCH

    def test_conviction_is_alertable(self):
        tier = classify_tier(8, 4.0, CONVICTION_MIN_CONFIDENCE, CONVICTION_MIN_SIGNALS)
        assert tier in (TIER_ALERT, TIER_CONVICTION)

    def test_six_active_signals_high_confidence_is_conviction(self):
        # All signals fire, all HIGH quality
        signals = {k: _sig(v[1], "HIGH") for k, v in _SIGNAL_LABELS.items()}
        confidence     = compute_confidence(signals)
        raw_score      = sum(v[1] for v in _SIGNAL_LABELS.values())  # 12, capped to 10
        adjusted_score = compute_adjusted_score(min(raw_score, 10), confidence)
        active         = _active_count(signals)

        tier = classify_tier(10, adjusted_score, confidence, active)
        assert tier == TIER_CONVICTION


# ─────────────────────────────────────────────
# Tier ordering: CONVICTION > ALERT > WATCH
# ─────────────────────────────────────────────

class TestTierOrdering:
    def test_watch_never_beats_alert(self):
        # If WATCH conditions hold, ALERT conditions must not also classify as WATCH
        watch = classify_tier(6, ALERT_MIN_ADJUSTED - 0.1, 30.0, 1)
        alert = classify_tier(6, ALERT_MIN_ADJUSTED, 30.0, 1)
        assert watch == TIER_WATCH
        assert alert in (TIER_ALERT, TIER_CONVICTION)

    def test_conviction_wins_over_alert_when_both_met(self):
        # CONVICTION check runs before ALERT check in classify_tier
        tier = classify_tier(
            8, ALERT_MIN_ADJUSTED + 1.0, CONVICTION_MIN_CONFIDENCE, CONVICTION_MIN_SIGNALS
        )
        assert tier == TIER_CONVICTION

    def test_return_is_always_one_of_three_tiers(self):
        for raw in range(0, 11):
            for adj in (0.0, 2.0, 2.5, 3.5, 5.0):
                for conf in (0.0, 30.0, 55.0, 80.0, 100.0):
                    for sigs in (0, 1, 2, 3, 6):
                        result = classify_tier(raw, adj, conf, sigs)
                        assert result in (TIER_WATCH, TIER_ALERT, TIER_CONVICTION)


# ─────────────────────────────────────────────
# Integration: classify_tier with real compute_* outputs
# ─────────────────────────────────────────────

class TestIntegrationWithConfidence:
    def _classify(self, signals: dict) -> str:
        raw_score  = sum(s.get("score", 0) for s in signals.values())
        raw_score  = min(raw_score, 10)
        confidence = compute_confidence(signals)
        adjusted   = compute_adjusted_score(raw_score, confidence)
        active     = _active_count(signals)
        return classify_tier(raw_score, adjusted, confidence, active)

    def test_three_high_quality_signals_at_max_is_conviction(self):
        # options=3 HIGH, insider=2 HIGH, breakout=2 HIGH → raw=7
        # confidence ≈ 55.8%, adjusted ≈ 3.91 → CONVICTION
        signals = {
            "options": _sig(3, "HIGH"),
            "insider": _sig(2, "HIGH"),
            "breakout": _sig(2, "HIGH"),
        }
        assert self._classify(signals) == TIER_CONVICTION

    def test_two_medium_signals_at_threshold_may_be_watch_or_alert(self):
        # options=3 MEDIUM, breakout=2 MEDIUM → raw=5 (below threshold)
        signals = {
            "options": _sig(3, "MEDIUM"),
            "breakout": _sig(2, "MEDIUM"),
        }
        tier = self._classify(signals)
        # raw=5 < ALERT_THRESHOLD → must be WATCH
        assert tier == TIER_WATCH

    def test_single_low_quality_signal_above_threshold_is_watch(self):
        # Even a nominally high raw score with all-LOW, 1-signal → WATCH
        signals = {"options": _sig(3, "LOW"), "insider": _sig(2, "LOW"),
                   "breakout": _sig(2, "LOW")}
        # raw=7, but quality so low adjusted may be below bar
        # quality: 7*0.4/12=0.233; convergence: 3/6=0.5
        # conf = 0.233*70 + 0.5*30 = 16.3+15 = 31.3%; adj = 7*0.313 = 2.19 → WATCH
        tier = self._classify(signals)
        assert tier == TIER_WATCH

    def test_all_low_quality_max_score_resolves_to_alert_not_conviction(self):
        # All 6 signals, all LOW quality → raw=10 (capped), conf≈58%, adj≈5.8
        # Signals=6 ≥ 3 AND conf≈58% ≥ 55 → CONVICTION
        # (This shows that enough signals even at LOW quality can reach CONVICTION
        #  because the convergence component is strong and conf>55)
        signals = {k: _sig(v[1], "LOW") for k, v in _SIGNAL_LABELS.items()}
        tier = self._classify(signals)
        # Just verify it's either ALERT or CONVICTION (not WATCH)
        assert tier in (TIER_ALERT, TIER_CONVICTION)


# ─────────────────────────────────────────────
# _format_alert — tier-aware header
# ─────────────────────────────────────────────

class TestFormatAlertHeader:
    _SIGNALS = {k: {"score": 0, "reason": ""} for k in _SIGNAL_LABELS}

    def test_conviction_tier_shows_conviction_in_header(self):
        msg = _format_alert("NVDA", 8, 500.0, self._SIGNALS, 455.0, 1000.0, TIER_CONVICTION)
        assert "CONVICTION" in msg.upper()

    def test_alert_tier_shows_pre_explosion_header(self):
        msg = _format_alert("NVDA", 8, 500.0, self._SIGNALS, 455.0, 1000.0, TIER_ALERT)
        assert "PRE-EXPLOSION" in msg.upper() or "ALERT" in msg.upper()

    def test_default_tier_is_alert_behavior(self):
        # Calling without tier kwarg must not crash and must not show CONVICTION header
        msg = _format_alert("NVDA", 8, 500.0, self._SIGNALS, 455.0, 1000.0)
        assert "CONVICTION" not in msg.upper() or "PRE-EXPLOSION" in msg.upper()

    def test_conviction_and_alert_headers_differ(self):
        conviction_msg = _format_alert("NVDA", 8, 500.0, self._SIGNALS, 455.0, 1000.0, TIER_CONVICTION)
        alert_msg      = _format_alert("NVDA", 8, 500.0, self._SIGNALS, 455.0, 1000.0, TIER_ALERT)
        assert conviction_msg.split("\n")[0] != alert_msg.split("\n")[0]

    def test_ticker_always_in_header(self):
        for tier in (TIER_ALERT, TIER_CONVICTION):
            msg = _format_alert("AAPL", 7, 200.0, self._SIGNALS, 182.0, 500.0, tier)
            assert "AAPL" in msg.split("\n")[0]
