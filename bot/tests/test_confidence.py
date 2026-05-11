"""
Unit tests for compute_confidence() and compute_adjusted_score().

Both functions are pure and deterministic — no mocking needed.

Design recap:
  confidence = (quality_component × 0.70 + convergence_component × 0.30) × 100

  quality_component   = sum(score_i × quality_weight_i) / 12
                        quality_weight: HIGH=1.0, MEDIUM=0.7, LOW=0.4

  convergence_component = active_signal_count / 6

  adjusted_score = raw_score × confidence / 100
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from predator import (
    compute_confidence,
    compute_adjusted_score,
    _SIGNAL_LABELS,
    _SIGNAL_MAX_TOTAL,
    _QUALITY_WEIGHT,
)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _sig(score: int, quality: str = "MEDIUM", reason: str = "") -> dict:
    return {"score": score, "reason": reason, "data_quality": quality}


def _all_signals(score_fn, quality: str = "HIGH") -> dict:
    """Build a full 6-signal dict using score_fn(max_score) for each signal."""
    return {
        k: _sig(score_fn(v[1]), quality)
        for k, v in _SIGNAL_LABELS.items()
    }


# ─────────────────────────────────────────────
# Module constants sanity
# ─────────────────────────────────────────────

class TestConstants:
    def test_signal_max_total_is_12(self):
        assert _SIGNAL_MAX_TOTAL == 12

    def test_quality_weights_present(self):
        for key in ("HIGH", "MEDIUM", "LOW"):
            assert key in _QUALITY_WEIGHT

    def test_quality_ordering(self):
        assert _QUALITY_WEIGHT["HIGH"] > _QUALITY_WEIGHT["MEDIUM"] > _QUALITY_WEIGHT["LOW"]

    def test_all_weights_in_unit_interval(self):
        for w in _QUALITY_WEIGHT.values():
            assert 0.0 < w <= 1.0


# ─────────────────────────────────────────────
# compute_adjusted_score
# ─────────────────────────────────────────────

class TestComputeAdjustedScore:
    def test_zero_score_always_zero(self):
        assert compute_adjusted_score(0, 100.0) == 0.0
        assert compute_adjusted_score(0, 0.0)   == 0.0

    def test_zero_confidence_always_zero(self):
        assert compute_adjusted_score(10, 0.0) == 0.0
        assert compute_adjusted_score(5,  0.0) == 0.0

    def test_full_confidence_returns_raw_score(self):
        assert compute_adjusted_score(10, 100.0) == 10.0
        assert compute_adjusted_score(7,  100.0) == 7.0

    def test_half_confidence_halves_score(self):
        assert compute_adjusted_score(8, 50.0) == 4.0
        assert compute_adjusted_score(6, 50.0) == 3.0

    def test_result_is_float(self):
        result = compute_adjusted_score(8, 72.0)
        assert isinstance(result, float)

    def test_result_rounds_to_two_decimal_places(self):
        # 5 × 33.3 / 100 = 1.665 → rounds to 1.67
        result = compute_adjusted_score(5, 33.3)
        assert result == round(5 * 33.3 / 100, 2)

    def test_does_not_exceed_raw_score(self):
        for raw in range(0, 11):
            for conf in (0.0, 25.0, 50.0, 75.0, 100.0):
                assert compute_adjusted_score(raw, conf) <= raw


# ─────────────────────────────────────────────
# compute_confidence — edge cases
# ─────────────────────────────────────────────

class TestComputeConfidenceEdgeCases:
    def test_empty_signals_returns_zero(self):
        assert compute_confidence({}) == 0.0

    def test_all_zero_scores_returns_zero(self):
        signals = {k: _sig(0) for k in _SIGNAL_LABELS}
        assert compute_confidence(signals) == 0.0

    def test_return_type_is_float(self):
        assert isinstance(compute_confidence({}), float)

    def test_result_in_valid_range(self):
        for signals in [
            {},
            _all_signals(lambda m: m, "HIGH"),
            _all_signals(lambda m: 0, "LOW"),
            {"options": _sig(1, "LOW")},
        ]:
            c = compute_confidence(signals)
            assert 0.0 <= c <= 100.0

    def test_unknown_signal_key_ignored(self):
        # Keys not in _SIGNAL_LABELS should not affect result
        c_without = compute_confidence({"options": _sig(3, "HIGH")})
        c_with    = compute_confidence({"options": _sig(3, "HIGH"), "ghost": _sig(5, "HIGH")})
        assert c_without == c_with

    def test_signal_with_missing_score_treated_as_zero(self):
        c = compute_confidence({"options": {"reason": "blah"}})  # no "score" key
        assert c == 0.0


# ─────────────────────────────────────────────
# compute_confidence — ceiling / floor
# ─────────────────────────────────────────────

class TestComputeConfidenceBounds:
    def test_all_signals_max_score_high_quality_returns_100(self):
        signals = _all_signals(lambda m: m, "HIGH")
        assert compute_confidence(signals) == 100.0

    def test_result_never_exceeds_100(self):
        # Over-score every signal beyond its stated max
        signals = {k: _sig(99, "HIGH") for k in _SIGNAL_LABELS}
        assert compute_confidence(signals) <= 100.0

    def test_single_low_quality_signal_returns_low_confidence(self):
        signals = {"options": _sig(1, "LOW")}
        c = compute_confidence(signals)
        # quality: 1*0.4/12 = 0.033; convergence: 1/6 = 0.167
        # raw = 0.033*0.7 + 0.167*0.3 = 0.023+0.05 = 0.073 → 7.3
        assert c < 15.0


# ─────────────────────────────────────────────
# compute_confidence — quality component
# ─────────────────────────────────────────────

class TestQualityComponent:
    def test_high_quality_beats_medium_at_same_score(self):
        c_high   = compute_confidence({"options": _sig(2, "HIGH")})
        c_medium = compute_confidence({"options": _sig(2, "MEDIUM")})
        assert c_high > c_medium

    def test_medium_quality_beats_low_at_same_score(self):
        c_medium = compute_confidence({"options": _sig(2, "MEDIUM")})
        c_low    = compute_confidence({"options": _sig(2, "LOW")})
        assert c_medium > c_low

    def test_high_quality_beats_low_quality(self):
        c_high = compute_confidence({"options": _sig(3, "HIGH")})
        c_low  = compute_confidence({"options": _sig(3, "LOW")})
        assert c_high > c_low

    def test_missing_data_quality_defaults_to_medium(self):
        c_explicit = compute_confidence({"options": {"score": 2, "reason": "", "data_quality": "MEDIUM"}})
        c_missing  = compute_confidence({"options": {"score": 2, "reason": ""}})
        assert c_explicit == c_missing

    def test_none_data_quality_defaults_to_medium(self):
        c_none   = compute_confidence({"options": {"score": 2, "data_quality": None}})
        c_medium = compute_confidence({"options": _sig(2, "MEDIUM")})
        assert c_none == c_medium

    def test_quality_component_formula(self):
        # options max = 3, HIGH weight = 1.0
        # quality_component = 3 * 1.0 / 12 = 0.25
        # convergence = 1/6 ≈ 0.1667
        # raw = 0.25*0.7 + 0.1667*0.3 = 0.175 + 0.05 = 0.225
        # confidence = 22.5
        signals = {"options": _sig(3, "HIGH")}
        c = compute_confidence(signals)
        assert c == pytest.approx(22.5, abs=0.2)


# ─────────────────────────────────────────────
# compute_confidence — convergence component
# ─────────────────────────────────────────────

class TestConvergenceComponent:
    def test_more_active_signals_increases_confidence(self):
        # Same quality/score per active signal, but different count
        one_signal  = {"options":  _sig(1, "MEDIUM")}
        two_signals = {"options":  _sig(1, "MEDIUM"),
                       "insider":  _sig(1, "MEDIUM")}
        three_signals = {"options": _sig(1, "MEDIUM"),
                         "insider": _sig(1, "MEDIUM"),
                         "breakout": _sig(1, "MEDIUM")}
        c1 = compute_confidence(one_signal)
        c2 = compute_confidence(two_signals)
        c3 = compute_confidence(three_signals)
        assert c1 < c2 < c3

    def test_six_active_signals_max_convergence(self):
        # All 6 fire with score=1/LOW so quality component is small,
        # but convergence component should be at its maximum (1.0).
        signals = {k: _sig(1, "LOW") for k in _SIGNAL_LABELS}
        c = compute_confidence(signals)
        # convergence = 1.0 → contributes 30 pts to the max possible
        # quality: 6*0.4/12 = 0.2 → 20% → contributes 14 pts
        # total ≈ 44
        assert c > 40.0

    def test_convergence_independent_of_quality(self):
        # Fixing quality to HIGH throughout: adding an extra LOW signal
        # still raises confidence (convergence beats nothing).
        base  = {"options": _sig(3, "HIGH"), "insider": _sig(2, "HIGH")}
        extra = dict(base, **{"breakout": _sig(1, "LOW")})
        assert compute_confidence(extra) > compute_confidence(base)

    def test_all_six_max_score_medium_quality(self):
        signals = _all_signals(lambda m: m, "MEDIUM")
        c = compute_confidence(signals)
        # quality: 12*0.7/12 = 0.7 → 70%; convergence: 6/6 = 1.0 → 30%
        # confidence = 0.7*70 + 1.0*30 = 49+30 = 79
        assert c == pytest.approx(79.0, abs=0.5)

    def test_all_six_max_score_low_quality(self):
        signals = _all_signals(lambda m: m, "LOW")
        c = compute_confidence(signals)
        # quality: 12*0.4/12 = 0.4 → 40%; convergence: 6/6 = 1.0
        # confidence = 0.4*70 + 1.0*30 = 28+30 = 58
        assert c == pytest.approx(58.0, abs=0.5)


# ─────────────────────────────────────────────
# compute_confidence — realistic scenarios
# ─────────────────────────────────────────────

class TestRealisticScenarios:
    def test_strong_signal_two_sources_high_quality(self):
        # Options max + insider max, both HIGH — plausible 8-pt raw score
        signals = {
            "options":  _sig(3, "HIGH"),
            "insider":  _sig(2, "HIGH"),
        }
        c = compute_confidence(signals)
        # quality: (3+2)*1.0/12 = 0.417; convergence: 2/6 = 0.333
        # raw = 0.417*0.7 + 0.333*0.3 = 0.292 + 0.1 = 0.392 → 39.2
        assert 35.0 < c < 45.0

    def test_four_converging_signals_mixed_quality(self):
        signals = {
            "options":       _sig(3, "HIGH"),
            "insider":       _sig(2, "HIGH"),
            "breakout":      _sig(2, "MEDIUM"),
            "short_squeeze": _sig(1, "MEDIUM"),
        }
        c = compute_confidence(signals)
        # quality: (3*1.0 + 2*1.0 + 2*0.7 + 1*0.7)/12 = (3+2+1.4+0.7)/12 = 7.1/12 = 0.592
        # convergence: 4/6 = 0.667
        # raw = 0.592*0.7 + 0.667*0.3 = 0.414 + 0.2 = 0.614 → 61.4
        assert 55.0 < c < 70.0

    def test_high_raw_score_low_quality_yields_low_confidence(self):
        # All signals fire but all LOW quality — inflated score, low confidence
        signals = _all_signals(lambda m: m, "LOW")
        c_low = compute_confidence(signals)
        signals_high = _all_signals(lambda m: m, "HIGH")
        c_high = compute_confidence(signals_high)
        assert c_low < c_high
        # Low quality should produce < 65% confidence even with full score
        assert c_low < 65.0

    def test_adjusted_score_ranks_quality_over_raw_points(self):
        # Ticker A: raw=7, 3 signals all HIGH
        #   quality:     (3+2+2)*1.0/12 = 0.583
        #   convergence: 3/6 = 0.5
        #   confidence ≈ 0.583*70 + 0.5*30 = 55.8
        #   adjusted  ≈ 7 * 0.558 = 3.91
        conf_a = compute_confidence({
            "options":  _sig(3, "HIGH"),
            "insider":  _sig(2, "HIGH"),
            "breakout": _sig(2, "HIGH"),
        })
        # Ticker B: raw=9, 4 signals all LOW
        #   quality:     (3+2+2+2)*0.4/12 = 0.3
        #   convergence: 4/6 = 0.667
        #   confidence ≈ 0.3*70 + 0.667*30 = 41.0
        #   adjusted  ≈ 9 * 0.41 = 3.69
        conf_b = compute_confidence({
            "options":  _sig(3, "LOW"),
            "insider":  _sig(2, "LOW"),
            "breakout": _sig(2, "LOW"),
            "catalyst": _sig(2, "LOW"),
        })
        adj_a = compute_adjusted_score(7, conf_a)
        adj_b = compute_adjusted_score(9, conf_b)
        # A has fewer raw points but all-HIGH quality beats B's inflated LOW-quality score
        assert adj_a > adj_b
