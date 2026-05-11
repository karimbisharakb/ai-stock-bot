"""
Unit tests for confidence_calibration module.

Covers:
  - _correlation_penalty()  — per-pair diminishing returns
  - _conflict_penalty()     — contradictory combination detection
  - _agreement_boost()      — multi-category alignment reward
  - calibrate_confidence()  — end-to-end: bounds, determinism, combined effect

All functions are pure. No network calls, no mocking required.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from confidence_calibration import (
    calibrate_confidence,
    _correlation_penalty,
    _conflict_penalty,
    _agreement_boost,
    _CORRELATED_PAIRS,
    _SIGNAL_CATEGORIES,
    _AGREEMENT_BOOST_TABLE,
    _AGREEMENT_BOOST_CAP,
)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _sigs(**kwargs) -> dict:
    """
    Build a signals dict from keyword score values.
    Unknown keys are silently ignored by the calibration functions.
    Missing keys default to score=0.
    """
    base = {
        "options":       0,
        "insider":       0,
        "short_squeeze": 0,
        "catalyst":      0,
        "institutional": 0,
        "breakout":      0,
    }
    base.update(kwargs)
    return {k: {"score": v, "reason": "test"} for k, v in base.items()}


# ─────────────────────────────────────────────
# _correlation_penalty()
# ─────────────────────────────────────────────

class TestCorrelationPenalty:
    def test_no_signals_no_penalty(self):
        assert _correlation_penalty(_sigs()) == 0.0

    def test_single_signal_no_penalty(self):
        # Only one side of any pair fires — no correlation
        assert _correlation_penalty(_sigs(options=3))       == 0.0
        assert _correlation_penalty(_sigs(short_squeeze=2)) == 0.0
        assert _correlation_penalty(_sigs(institutional=1)) == 0.0
        assert _correlation_penalty(_sigs(insider=2))       == 0.0
        assert _correlation_penalty(_sigs(breakout=2))      == 0.0

    def test_options_short_squeeze_pair_fires(self):
        penalty = _correlation_penalty(_sigs(options=2, short_squeeze=1))
        assert penalty == pytest.approx(2.5)

    def test_institutional_insider_pair_fires(self):
        penalty = _correlation_penalty(_sigs(institutional=1, insider=2))
        assert penalty == pytest.approx(3.0)

    def test_breakout_options_pair_fires(self):
        penalty = _correlation_penalty(_sigs(breakout=2, options=2))
        assert penalty == pytest.approx(2.0)

    def test_two_pairs_fire_penalties_additive(self):
        # options+squeeze (2.5) AND institutional+insider (3.0)
        sigs = _sigs(options=2, short_squeeze=1, institutional=1, insider=2)
        assert _correlation_penalty(sigs) == pytest.approx(5.5)

    def test_all_three_pairs_fire(self):
        # options+squeeze (2.5) + institutional+insider (3.0) + breakout+options (2.0)
        sigs = _sigs(options=3, short_squeeze=2, institutional=1, insider=2, breakout=2)
        assert _correlation_penalty(sigs) == pytest.approx(7.5)

    def test_zero_score_side_does_not_trigger_pair(self):
        # options=0, short_squeeze=2 → pair does not fire
        assert _correlation_penalty(_sigs(options=0, short_squeeze=2)) == 0.0
        assert _correlation_penalty(_sigs(options=2, short_squeeze=0)) == 0.0

    def test_returns_float(self):
        assert isinstance(_correlation_penalty(_sigs(options=1, short_squeeze=1)), float)

    def test_penalty_is_non_negative(self):
        for opts in range(4):
            for sq in range(3):
                assert _correlation_penalty(_sigs(options=opts, short_squeeze=sq)) >= 0.0

    def test_penalty_increases_with_more_pairs(self):
        p1 = _correlation_penalty(_sigs(options=2, short_squeeze=1))
        p2 = _correlation_penalty(_sigs(options=2, short_squeeze=1, institutional=1, insider=1))
        assert p2 > p1


# ─────────────────────────────────────────────
# _conflict_penalty()
# ─────────────────────────────────────────────

class TestConflictPenalty:
    def test_no_signals_no_conflict(self):
        assert _conflict_penalty(_sigs()) == 0.0

    # Conflict A: options without price action
    def test_conflict_a_options_spike_no_price_action(self):
        sigs = _sigs(options=2, breakout=0, short_squeeze=0)
        assert _conflict_penalty(sigs) == pytest.approx(3.0)

    def test_conflict_a_options_at_threshold(self):
        sigs = _sigs(options=2)
        assert _conflict_penalty(sigs) == pytest.approx(3.0)

    def test_conflict_a_options_above_threshold(self):
        sigs = _sigs(options=3)
        assert _conflict_penalty(sigs) == pytest.approx(3.0)

    def test_conflict_a_resolved_by_breakout(self):
        sigs = _sigs(options=3, breakout=1)
        assert _conflict_penalty(sigs) == 0.0

    def test_conflict_a_resolved_by_short_squeeze(self):
        sigs = _sigs(options=3, short_squeeze=1)
        assert _conflict_penalty(sigs) == 0.0

    def test_conflict_a_not_triggered_below_threshold(self):
        # options=1 is below the threshold of 2
        sigs = _sigs(options=1, breakout=0, short_squeeze=0)
        assert _conflict_penalty(sigs) == 0.0

    # Conflict B: isolated catalyst
    def test_conflict_b_isolated_catalyst(self):
        sigs = _sigs(catalyst=1, options=0, insider=0, institutional=0)
        assert _conflict_penalty(sigs) == pytest.approx(2.0)

    def test_conflict_b_catalyst_resolved_by_options(self):
        sigs = _sigs(catalyst=1, options=1)
        assert _conflict_penalty(sigs) == 0.0

    def test_conflict_b_catalyst_resolved_by_insider(self):
        sigs = _sigs(catalyst=2, insider=1)
        assert _conflict_penalty(sigs) == 0.0

    def test_conflict_b_catalyst_resolved_by_institutional(self):
        sigs = _sigs(catalyst=1, institutional=1)
        assert _conflict_penalty(sigs) == 0.0

    def test_conflict_b_no_catalyst_no_penalty(self):
        sigs = _sigs(catalyst=0)
        assert _conflict_penalty(sigs) == 0.0

    # Conflict C: strong squeeze without options
    def test_conflict_c_squeeze_without_options(self):
        sigs = _sigs(short_squeeze=2, options=0)
        assert _conflict_penalty(sigs) == pytest.approx(2.0)

    def test_conflict_c_squeeze_resolved_by_options(self):
        sigs = _sigs(short_squeeze=2, options=1)
        assert _conflict_penalty(sigs) == 0.0

    def test_conflict_c_not_triggered_below_threshold(self):
        # short_squeeze=1 is below the threshold of 2
        sigs = _sigs(short_squeeze=1, options=0)
        assert _conflict_penalty(sigs) == 0.0

    def test_conflict_c_higher_squeeze_still_2pt_penalty(self):
        # penalty is flat regardless of how far above threshold
        sigs = _sigs(short_squeeze=3, options=0)
        assert _conflict_penalty(sigs) == pytest.approx(2.0)

    # Stacked conflicts
    def test_multiple_conflicts_additive(self):
        # Conflict A (opts=2, brk=0, sq=0) + Conflict B (cat=1, opts=0 → no, opts=2 not 0)
        # Actually opts=2 resolves B. Let's use sq=2 for C and cat alone for B
        # Conflict B: cat=1, opts=0, ins=0, inst=0 → +2.0
        # Conflict C: sq=2, opts=0 → +2.0
        sigs = _sigs(catalyst=1, short_squeeze=2, options=0)
        assert _conflict_penalty(sigs) == pytest.approx(4.0)

    def test_returns_float(self):
        assert isinstance(_conflict_penalty(_sigs(options=3)), float)

    def test_penalty_is_non_negative(self):
        for o in range(4):
            for b in range(3):
                for s in range(3):
                    assert _conflict_penalty(_sigs(options=o, breakout=b, short_squeeze=s)) >= 0.0


# ─────────────────────────────────────────────
# _agreement_boost()
# ─────────────────────────────────────────────

class TestAgreementBoost:
    def test_no_signals_no_boost(self):
        assert _agreement_boost(_sigs()) == 0.0

    def test_one_signal_one_category_no_boost(self):
        assert _agreement_boost(_sigs(options=2))      == 0.0
        assert _agreement_boost(_sigs(breakout=2))     == 0.0
        assert _agreement_boost(_sigs(catalyst=1))     == 0.0
        assert _agreement_boost(_sigs(institutional=1)) == 0.0

    def test_two_signals_same_category_no_boost(self):
        # institutional + insider both belong to "smart_money" → 1 category
        assert _agreement_boost(_sigs(institutional=1, insider=2)) == 0.0

    def test_two_signals_same_volatility_category_no_boost(self):
        # options + short_squeeze both in "volatility" → 1 category
        assert _agreement_boost(_sigs(options=2, short_squeeze=1)) == 0.0

    def test_two_distinct_categories_gives_boost(self):
        # smart_money (insider) + technical (breakout)
        boost = _agreement_boost(_sigs(insider=2, breakout=2))
        assert boost == pytest.approx(_AGREEMENT_BOOST_TABLE[2])

    def test_two_categories_volatility_and_fundamental(self):
        boost = _agreement_boost(_sigs(options=2, catalyst=1))
        assert boost == pytest.approx(_AGREEMENT_BOOST_TABLE[2])

    def test_three_distinct_categories_gives_higher_boost(self):
        # volatility (options) + smart_money (insider) + technical (breakout)
        boost = _agreement_boost(_sigs(options=2, insider=2, breakout=2))
        assert boost == pytest.approx(_AGREEMENT_BOOST_TABLE[3])

    def test_three_categories_boost_higher_than_two(self):
        b2 = _agreement_boost(_sigs(options=2, insider=2))
        b3 = _agreement_boost(_sigs(options=2, insider=2, breakout=2))
        assert b3 > b2

    def test_four_distinct_categories_gives_max_boost(self):
        # volatility (options) + smart_money (institutional) + technical (breakout) + fundamental (catalyst)
        boost = _agreement_boost(_sigs(options=2, institutional=1, breakout=2, catalyst=1))
        assert boost == pytest.approx(_AGREEMENT_BOOST_TABLE[4])

    def test_boost_capped_at_maximum(self):
        boost = _agreement_boost(_sigs(options=2, institutional=1, breakout=2, catalyst=1))
        assert boost <= _AGREEMENT_BOOST_CAP

    def test_boost_non_negative(self):
        for opts in range(4):
            for ins in range(3):
                for brk in range(3):
                    assert _agreement_boost(_sigs(options=opts, insider=ins, breakout=brk)) >= 0.0

    def test_boost_monotone_by_category_count(self):
        b1 = _agreement_boost(_sigs(options=1))
        b2 = _agreement_boost(_sigs(options=1, insider=1))
        b3 = _agreement_boost(_sigs(options=1, insider=1, breakout=1))
        b4 = _agreement_boost(_sigs(options=1, insider=1, breakout=1, catalyst=1))
        assert b1 <= b2 <= b3 <= b4

    def test_returns_float(self):
        assert isinstance(_agreement_boost(_sigs(options=2, breakout=2)), float)

    def test_extra_signal_in_existing_category_does_not_increase_boost(self):
        # Adding short_squeeze (same category as options) should not change boost
        b_without = _agreement_boost(_sigs(options=2, insider=2))
        b_with    = _agreement_boost(_sigs(options=2, insider=2, short_squeeze=1))
        assert b_without == b_with


# ─────────────────────────────────────────────
# calibrate_confidence() — integration
# ─────────────────────────────────────────────

class TestCalibrateConfidence:
    # Determinism
    def test_deterministic_same_input_same_output(self):
        sigs = _sigs(options=2, short_squeeze=1, breakout=2)
        r1 = calibrate_confidence(60.0, sigs)
        r2 = calibrate_confidence(60.0, sigs)
        assert r1 == r2

    def test_deterministic_empty_signals(self):
        assert calibrate_confidence(55.0, _sigs()) == calibrate_confidence(55.0, _sigs())

    # Passthrough
    def test_no_signals_returns_raw_confidence(self):
        assert calibrate_confidence(50.0, _sigs()) == 50.0

    def test_bull_scenario_unchanged_without_penalties(self):
        # 2 distinct-category signals, no correlations, no conflicts
        sigs = _sigs(insider=2, breakout=2)
        raw  = 55.0
        cal  = calibrate_confidence(raw, sigs)
        # boost applied (+2.0) but no penalties
        assert cal == pytest.approx(raw + _AGREEMENT_BOOST_TABLE[2], abs=0.01)

    # Correlation reduces confidence
    def test_correlated_pair_lowers_confidence(self):
        # options=1 is below Conflict-A threshold (2), so no conflict penalty on solo.
        # Adding short_squeeze=1 makes the correlated pair fire (−2.5).
        # Both are in the same "volatility" category so no agreement boost offsets it.
        solo_sigs = _sigs(options=1)
        pair_sigs = _sigs(options=1, short_squeeze=1)
        cal_solo  = calibrate_confidence(60.0, solo_sigs)
        cal_pair  = calibrate_confidence(60.0, pair_sigs)
        assert cal_pair < cal_solo

    def test_correlated_pair_reduces_by_expected_amount(self):
        # Only options + short_squeeze firing, no conflict, category count = 1 (volatility only)
        sigs = _sigs(options=2, short_squeeze=1)
        raw  = 60.0
        cal  = calibrate_confidence(raw, sigs)
        # correlation penalty = 2.5, no conflict, no boost (1 category)
        assert cal == pytest.approx(raw - 2.5, abs=0.01)

    def test_institutional_insider_reduces_by_expected_amount(self):
        # smart_money pair + no other signals → corr=3.0, no conflict, no boost (1 cat)
        sigs = _sigs(institutional=1, insider=2)
        raw  = 70.0
        cal  = calibrate_confidence(raw, sigs)
        assert cal == pytest.approx(raw - 3.0, abs=0.01)

    # Conflict reduces confidence
    def test_conflict_reduces_confidence(self):
        no_conflict = _sigs(options=3, breakout=2)   # breakout resolves conflict A
        with_conflict = _sigs(options=3, breakout=0, short_squeeze=0)  # conflict A
        cal_no  = calibrate_confidence(60.0, no_conflict)
        cal_yes = calibrate_confidence(60.0, with_conflict)
        assert cal_yes < cal_no

    def test_isolated_catalyst_reduces_confidence(self):
        with_support   = _sigs(catalyst=1, options=1)  # no conflict
        without_support = _sigs(catalyst=1)             # conflict B
        cal_with    = calibrate_confidence(55.0, with_support)
        cal_without = calibrate_confidence(55.0, without_support)
        assert cal_without < cal_with

    def test_isolated_catalyst_penalty_amount(self):
        # Conflict B only (cat=1, no options/insider/inst), no corr, 1 category → no boost
        sigs = _sigs(catalyst=1)
        raw  = 40.0
        cal  = calibrate_confidence(raw, sigs)
        assert cal == pytest.approx(raw - 2.0, abs=0.01)

    # Agreement boost increases confidence
    def test_agreement_boost_increases_confidence_over_no_boost(self):
        one_cat  = _sigs(options=2)            # 1 category
        two_cat  = _sigs(options=2, insider=1) # 2 categories
        cal_one  = calibrate_confidence(50.0, one_cat)
        cal_two  = calibrate_confidence(50.0, two_cat)
        assert cal_two > cal_one

    def test_three_category_agreement_higher_than_two(self):
        two_cat   = _sigs(options=2, insider=2)
        three_cat = _sigs(options=2, insider=2, breakout=2)
        cal_two   = calibrate_confidence(50.0, two_cat)
        cal_three = calibrate_confidence(50.0, three_cat)
        assert cal_three > cal_two

    # Bounds
    def test_output_never_exceeds_100(self):
        sigs = _sigs(options=2, institutional=1, breakout=2, catalyst=1)
        assert calibrate_confidence(100.0, sigs) <= 100.0

    def test_output_never_below_zero(self):
        sigs = _sigs(options=3, short_squeeze=2, institutional=1, insider=2,
                     catalyst=1, breakout=0)
        assert calibrate_confidence(0.0, sigs) >= 0.0

    def test_large_penalties_clamp_at_zero(self):
        # Worst-case penalties on very low confidence
        sigs = _sigs(options=3, short_squeeze=2, institutional=1, insider=2, catalyst=1)
        cal = calibrate_confidence(5.0, sigs)
        assert cal >= 0.0

    def test_max_boost_clamps_at_100(self):
        # Perfect confidence with max boost should clamp cleanly
        sigs = _sigs(options=2, institutional=1, breakout=2, catalyst=1)
        assert calibrate_confidence(99.0, sigs) <= 100.0

    # Return type
    def test_returns_float(self):
        assert isinstance(calibrate_confidence(60.0, _sigs()), float)

    def test_rounds_to_two_decimal_places(self):
        result = calibrate_confidence(60.0, _sigs(options=2, short_squeeze=1))
        assert result == round(result, 2)

    # Net effect: combined scenario
    def test_combined_correlation_and_boost_net_effect(self):
        # options+squeeze correlated (−2.5) but 2 categories get boost (+2.0)
        # wait: options+squeeze are both "volatility" → 1 category → no boost
        # So net = −2.5, calibrated = 60 − 2.5 = 57.5
        sigs = _sigs(options=2, short_squeeze=1)
        raw  = 60.0
        cal  = calibrate_confidence(raw, sigs)
        assert cal == pytest.approx(57.5, abs=0.01)

    def test_full_conviction_scenario_net_effect(self):
        # options=3 (HIGH), insider=2 (HIGH), breakout=2
        # correlation: breakout+options (−2.0), no institutional+insider pair (inst=0)
        # conflict: none (breakout > 0 resolves A)
        # agreement: volatility (options) + smart_money (insider) + technical (breakout) = 3 cats → +3.5
        # net = −2.0 + 3.5 = +1.5
        sigs = _sigs(options=3, insider=2, breakout=2)
        raw  = 55.8
        cal  = calibrate_confidence(raw, sigs)
        assert cal == pytest.approx(55.8 + 1.5, abs=0.01)

    def test_smart_money_cluster_net_effect(self):
        # institutional=1 + insider=2 → correlation penalty −3.0, 1 category → no boost
        sigs = _sigs(institutional=1, insider=2)
        raw  = 62.0
        cal  = calibrate_confidence(raw, sigs)
        assert cal == pytest.approx(62.0 - 3.0, abs=0.01)


# ─────────────────────────────────────────────
# Confidence bounds — exhaustive
# ─────────────────────────────────────────────

class TestConfidenceBounds:
    def test_output_always_in_0_100_range(self):
        """Exhaustive check across many signal combinations and confidence inputs."""
        combos = [
            _sigs(options=o, insider=i, breakout=b, catalyst=c,
                  short_squeeze=s, institutional=inst)
            for o   in (0, 1, 2, 3)
            for i   in (0, 1, 2)
            for b   in (0, 1, 2)
            for c   in (0, 1, 2)
            for s   in (0, 1, 2)
            for inst in (0, 1)
        ]
        for raw in (0.0, 10.0, 25.0, 50.0, 75.0, 90.0, 100.0):
            for sigs in combos:
                cal = calibrate_confidence(raw, sigs)
                assert 0.0 <= cal <= 100.0, \
                    f"out of range: cal={cal} raw={raw} sigs={sigs}"
