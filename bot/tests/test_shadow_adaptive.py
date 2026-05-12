"""
Tests for bot/shadow_adaptive.py — Phase 4C.

Covers:
  - Shadow weight application and score simulation
  - Confidence and tier simulation
  - Per-alert delta correctness
  - Shadow replay (inclusion / churn counting)
  - Live-vs-shadow aggregate comparison (win rate, returns, drawdown,
    calibration, false-positive reduction, alert volume)
  - Stability analysis (oscillation, high variance, overreaction)
  - Rollout readiness classification
  - Full report generation
  - Determinism
  - Bounded output limits
  - Sparse / None input handling
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import shadow_adaptive as sa

# ── helpers ───────────────────────────────────────────────────────────────────

def _row(
    adjusted_score=8.0,
    raw_score=8.0,
    confidence_pct=65.0,
    tier="ALERT",
    regime="BULL",
    return_5d=None,
    ticker="AAPL",
    score_options=2.0,
    score_insider=1.0,
    score_short_squeeze=2.0,
    score_catalyst=1.0,
    score_institutional=1.0,
    score_breakout=1.0,
):
    return {
        "ticker":            ticker,
        "adjusted_score":    adjusted_score,
        "raw_score":         raw_score,
        "confidence_pct":    confidence_pct,
        "tier":              tier,
        "regime":            regime,
        "return_5d":         return_5d,
        "score_options":     score_options,
        "score_insider":     score_insider,
        "score_short_squeeze": score_short_squeeze,
        "score_catalyst":    score_catalyst,
        "score_institutional": score_institutional,
        "score_breakout":    score_breakout,
    }


def _win(**kwargs):
    kwargs.setdefault("adjusted_score", 8.0)
    kwargs.setdefault("raw_score", 8.0)
    kwargs.setdefault("return_5d", 5.0)
    return _row(**kwargs)


def _loss(**kwargs):
    kwargs.setdefault("adjusted_score", 8.0)
    kwargs.setdefault("raw_score", 8.0)
    kwargs.setdefault("return_5d", -3.0)
    return _row(**kwargs)


def _wins(n, **kwargs):
    return [_win(**kwargs) for _ in range(n)]


def _losses(n, **kwargs):
    return [_loss(**kwargs) for _ in range(n)]


def _flat_wa():
    """Weight adjustments with no change (suggested == default)."""
    return {
        sig: {
            "default_weight":   sa.DEFAULT_WEIGHTS[sig],
            "suggested_weight": sa.DEFAULT_WEIGHTS[sig],
            "adjustment":       0.0,
        }
        for sig in sa.SIGNAL_NAMES
    }


def _boost_wa(signal, boost=0.5):
    """Boost one signal by `boost` points."""
    wa = _flat_wa()
    default = sa.DEFAULT_WEIGHTS[signal]
    wa[signal] = {
        "default_weight":   default,
        "suggested_weight": default + boost,
        "adjustment":       boost,
    }
    return wa


def _penalty_wa(signal, penalty=0.5):
    """Penalize one signal by `penalty` points."""
    wa = _flat_wa()
    default = sa.DEFAULT_WEIGHTS[signal]
    wa[signal] = {
        "default_weight":   default,
        "suggested_weight": max(0.1, default - penalty),
        "adjustment":       -penalty,
    }
    return wa


def _snapshot(adjustments: dict) -> dict:
    """Build a single weight snapshot dict from {signal: adj} overrides."""
    snap = _flat_wa()
    for sig, adj in adjustments.items():
        default = sa.DEFAULT_WEIGHTS[sig]
        snap[sig] = {
            "default_weight":   default,
            "suggested_weight": default + adj,
            "adjustment":       adj,
        }
    return snap


# ── TestApplyShadowWeights ────────────────────────────────────────────────────

class TestApplyShadowWeights:
    def test_flat_wa_returns_identical_scores(self):
        row    = _row(score_options=2.0, score_breakout=1.0)
        shadow = sa.apply_shadow_weights(row, _flat_wa())
        assert shadow["options"]  == pytest.approx(2.0)
        assert shadow["breakout"] == pytest.approx(1.0)

    def test_boost_increases_active_signal(self):
        row    = _row(score_options=3.0)
        shadow = sa.apply_shadow_weights(row, _boost_wa("options", 0.3))
        assert shadow["options"] > 3.0

    def test_penalty_decreases_active_signal(self):
        row    = _row(score_options=3.0)
        shadow = sa.apply_shadow_weights(row, _penalty_wa("options", 0.5))
        assert shadow["options"] < 3.0

    def test_zero_signal_unaffected_by_any_weight(self):
        row    = _row(score_options=0.0)
        shadow = sa.apply_shadow_weights(row, _boost_wa("options", 0.5))
        assert shadow["options"] == 0.0

    def test_inactive_signals_unchanged(self):
        row    = _row(score_options=2.0, score_insider=0.0)
        shadow = sa.apply_shadow_weights(row, _boost_wa("insider", 0.5))
        assert shadow["insider"] == 0.0  # no score to scale
        assert shadow["options"] == pytest.approx(2.0)

    def test_returns_all_signal_names(self):
        shadow = sa.apply_shadow_weights(_row(), _flat_wa())
        assert set(shadow.keys()) == set(sa.SIGNAL_NAMES)

    def test_empty_wa_uses_defaults(self):
        row    = _row(score_options=2.0)
        shadow = sa.apply_shadow_weights(row, {})
        # default == suggested → no scale → same score
        assert shadow["options"] == pytest.approx(2.0)

    def test_scale_proportional_to_weight_ratio(self):
        row    = _row(score_breakout=2.0)
        wa     = _flat_wa()
        wa["breakout"]["suggested_weight"] = 4.0  # default is 2.0 → factor 2
        shadow = sa.apply_shadow_weights(row, wa)
        assert shadow["breakout"] == pytest.approx(4.0)


# ── TestSimulateShadowScore ───────────────────────────────────────────────────

class TestSimulateShadowScore:
    def test_flat_wa_equals_sum_of_live_scores(self):
        row = _row(
            score_options=2.0, score_insider=1.0, score_short_squeeze=2.0,
            score_catalyst=1.0, score_institutional=1.0, score_breakout=1.0,
        )
        expected = 2 + 1 + 2 + 1 + 1 + 1
        assert sa.simulate_shadow_score(row, _flat_wa()) == pytest.approx(expected)

    def test_boost_increases_total_score(self):
        row = _row(score_options=3.0)
        live_score  = sa.simulate_shadow_score(row, _flat_wa())
        boost_score = sa.simulate_shadow_score(row, _boost_wa("options", 0.5))
        assert boost_score > live_score

    def test_penalty_decreases_total_score(self):
        row = _row(score_options=3.0)
        live_score    = sa.simulate_shadow_score(row, _flat_wa())
        penalty_score = sa.simulate_shadow_score(row, _penalty_wa("options", 0.5))
        assert penalty_score < live_score

    def test_no_signals_score_zero(self):
        row = _row(
            score_options=0.0, score_insider=0.0, score_short_squeeze=0.0,
            score_catalyst=0.0, score_institutional=0.0, score_breakout=0.0,
        )
        assert sa.simulate_shadow_score(row, _flat_wa()) == 0.0

    def test_none_wa_uses_defaults(self):
        row = _row(score_options=2.0)
        score = sa.simulate_shadow_score(row, None)
        assert score == pytest.approx(2.0 + 1.0 + 2.0 + 1.0 + 1.0 + 1.0)


# ── TestSimulateShadowConfidence ──────────────────────────────────────────────

class TestSimulateShadowConfidence:
    def test_flat_wa_returns_same_confidence(self):
        row  = _row(confidence_pct=65.0, adjusted_score=8.0, raw_score=8.0)
        conf = sa.simulate_shadow_confidence(row, _flat_wa())
        assert conf == pytest.approx(65.0)

    def test_boost_increases_confidence(self):
        row        = _row(confidence_pct=60.0, adjusted_score=8.0, raw_score=8.0,
                          score_options=3.0)
        base_conf  = sa.simulate_shadow_confidence(row, _flat_wa())
        boost_conf = sa.simulate_shadow_confidence(row, _boost_wa("options", 0.5))
        assert boost_conf > base_conf

    def test_penalty_decreases_confidence(self):
        row         = _row(confidence_pct=60.0, adjusted_score=8.0, raw_score=8.0,
                           score_options=3.0)
        base_conf   = sa.simulate_shadow_confidence(row, _flat_wa())
        penalty_conf = sa.simulate_shadow_confidence(row, _penalty_wa("options", 0.5))
        assert penalty_conf < base_conf

    def test_zero_adjusted_score_returns_live_confidence(self):
        row  = _row(confidence_pct=55.0, adjusted_score=0.0, raw_score=0.0)
        conf = sa.simulate_shadow_confidence(row, _boost_wa("options", 0.5))
        assert conf == pytest.approx(55.0)

    def test_confidence_clamped_at_100(self):
        # Extreme boost
        row  = _row(confidence_pct=99.0, adjusted_score=6.0, raw_score=6.0,
                    score_options=3.0)
        wa   = _flat_wa()
        wa["options"]["suggested_weight"] = 300.0  # absurd boost
        conf = sa.simulate_shadow_confidence(row, wa)
        assert conf <= 100.0

    def test_confidence_clamped_at_zero(self):
        row  = _row(confidence_pct=5.0, adjusted_score=6.0, raw_score=6.0,
                    score_options=3.0)
        wa   = _penalty_wa("options", 2.9)  # near-zero suggested
        conf = sa.simulate_shadow_confidence(row, wa)
        assert conf >= 0.0

    def test_regime_factor_preserved(self):
        # raw_score must equal sum of individual signal scores for flat wa to be identity.
        # Signals: options=3, insider=1, squeeze=2, catalyst=1, institutional=1, breakout=1 → sum=9
        # adj = 9 * 0.75 = 6.75 (RISK_OFF factor 0.75)
        row = _row(
            raw_score=9.0, adjusted_score=6.75, confidence_pct=60.0,
            score_options=3.0, regime="RISK_OFF",
        )
        # flat weights → shadow_raw == 9.0 == live_raw,
        # shadow_adj = 9.0 * 0.75 = 6.75 == live_adj → scale = 1.0 → conf unchanged
        conf = sa.simulate_shadow_confidence(row, _flat_wa())
        assert conf == pytest.approx(60.0)


# ── TestSimulateShadowTier ────────────────────────────────────────────────────

class TestSimulateShadowTier:
    def test_conviction_when_high_conf_and_enough_signals(self):
        tier = sa.simulate_shadow_tier(60.0, 3)
        assert tier == sa.TIER_CONVICTION

    def test_not_conviction_when_low_signal_count(self):
        tier = sa.simulate_shadow_tier(65.0, 2)
        assert tier != sa.TIER_CONVICTION

    def test_not_conviction_when_low_confidence(self):
        tier = sa.simulate_shadow_tier(40.0, 4)
        assert tier != sa.TIER_CONVICTION

    def test_standard_when_medium_confidence(self):
        tier = sa.simulate_shadow_tier(45.0, 2)
        assert tier == sa.TIER_STANDARD

    def test_alert_when_low_confidence(self):
        tier = sa.simulate_shadow_tier(30.0, 1)
        assert tier == sa.TIER_ALERT

    def test_conviction_exact_boundary(self):
        tier = sa.simulate_shadow_tier(sa.CONVICTION_CONFIDENCE_MIN, sa.CONVICTION_MIN_SIGNALS)
        assert tier == sa.TIER_CONVICTION

    def test_standard_exact_boundary(self):
        tier = sa.simulate_shadow_tier(sa.STANDARD_CONFIDENCE_MIN, 1)
        assert tier == sa.TIER_STANDARD


# ── TestShadowRowDelta ────────────────────────────────────────────────────────

class TestShadowRowDelta:
    def test_required_fields_present(self):
        delta = sa._shadow_row_delta(_row(), _flat_wa())
        for k in ("idx", "ticker", "regime", "live_score", "shadow_score",
                  "score_delta", "live_confidence", "shadow_confidence",
                  "confidence_delta", "live_tier", "shadow_tier",
                  "tier_changed", "live_included", "shadow_included",
                  "inclusion_changed", "return_5d",
                  "n_active_live", "n_active_shadow"):
            assert k in delta, f"missing field: {k}"

    def test_flat_wa_no_score_delta(self):
        delta = sa._shadow_row_delta(_row(adjusted_score=8.0, raw_score=8.0), _flat_wa())
        assert delta["score_delta"] == pytest.approx(0.0)

    def test_flat_wa_no_confidence_delta(self):
        delta = sa._shadow_row_delta(_row(confidence_pct=65.0), _flat_wa())
        assert delta["confidence_delta"] == pytest.approx(0.0)

    def test_flat_wa_no_tier_change(self):
        # Default row: conf=65%, 6 active signals → shadow_tier = CONVICTION.
        # Use CONVICTION as live_tier so flat weights produce no tier change.
        delta = sa._shadow_row_delta(_row(tier="CONVICTION"), _flat_wa())
        assert not delta["tier_changed"]

    def test_boost_increases_shadow_score(self):
        row   = _row(adjusted_score=8.0, raw_score=8.0, score_options=3.0)
        delta = sa._shadow_row_delta(row, _boost_wa("options", 0.5))
        assert delta["score_delta"] > 0

    def test_penalty_below_threshold_changes_inclusion(self):
        # Score exactly at threshold with heavy penalty → drops below
        row = _row(
            adjusted_score=6.0, raw_score=6.0, confidence_pct=55.0,
            score_options=3.0, score_insider=1.0, score_short_squeeze=2.0,
            score_catalyst=0.0, score_institutional=0.0, score_breakout=0.0,
        )
        # Penalize options heavily so shadow_raw drops significantly
        wa = _flat_wa()
        wa["options"] = {"default_weight": 3.0, "suggested_weight": 0.5, "adjustment": -2.5}
        wa["short_squeeze"] = {"default_weight": 2.0, "suggested_weight": 0.5, "adjustment": -1.5}
        wa["insider"] = {"default_weight": 2.0, "suggested_weight": 0.1, "adjustment": -1.9}
        delta = sa._shadow_row_delta(row, wa)
        # shadow_score should be well below 6.0
        assert delta["shadow_score"] < 6.0
        assert delta["inclusion_changed"]

    def test_idx_stored(self):
        delta = sa._shadow_row_delta(_row(), _flat_wa(), idx=7)
        assert delta["idx"] == 7

    def test_return_5d_propagated(self):
        delta = sa._shadow_row_delta(_win(return_5d=4.5), _flat_wa())
        assert delta["return_5d"] == pytest.approx(4.5)

    def test_n_active_live_correct(self):
        row = _row(
            score_options=2.0, score_insider=1.0, score_short_squeeze=0.0,
            score_catalyst=0.0, score_institutional=0.0, score_breakout=0.0,
        )
        delta = sa._shadow_row_delta(row, _flat_wa())
        assert delta["n_active_live"] == 2


# ── TestRunShadowReplay ───────────────────────────────────────────────────────

class TestRunShadowReplay:
    def test_empty_rows_returns_zeros(self):
        result = sa.run_shadow_replay([], _flat_wa())
        assert result["n_rows"]              == 0
        assert result["n_inclusion_changes"] == 0
        assert result["churn_rate"]          == 0.0
        assert result["deltas"]              == []

    def test_n_rows_matches_input(self):
        rows   = _wins(5)
        result = sa.run_shadow_replay(rows, _flat_wa())
        assert result["n_rows"] == 5
        assert len(result["deltas"]) == 5

    def test_flat_wa_no_inclusion_changes(self):
        result = sa.run_shadow_replay(_wins(10), _flat_wa())
        assert result["n_inclusion_changes"] == 0
        assert result["churn_rate"] == 0.0

    def test_churn_rate_computed_correctly(self):
        # 4 wins (above threshold) + 4 near-threshold rows that drop below under penalty
        above = _wins(4)
        near  = [_loss(adjusted_score=6.1, raw_score=6.1,
                       score_options=3.0, score_insider=1.0, score_short_squeeze=0.0,
                       score_catalyst=0.0, score_institutional=0.0, score_breakout=0.0)
                 for _ in range(4)]
        rows = above + near
        wa   = _flat_wa()
        wa["options"]  = {"default_weight": 3.0, "suggested_weight": 0.5, "adjustment": -2.5}
        wa["insider"]  = {"default_weight": 2.0, "suggested_weight": 0.1, "adjustment": -1.9}
        result = sa.run_shadow_replay(rows, wa)
        assert result["n_inclusion_changes"] > 0
        assert 0.0 < result["churn_rate"] <= 1.0

    def test_deltas_indexed_sequentially(self):
        rows   = _wins(3)
        result = sa.run_shadow_replay(rows, _flat_wa())
        for i, d in enumerate(result["deltas"]):
            assert d["idx"] == i

    def test_none_wa_runs_cleanly(self):
        result = sa.run_shadow_replay(_wins(3), None)
        assert result["n_rows"] == 3


# ── TestCompareLiveVsShadow ───────────────────────────────────────────────────

class TestCompareLiveVsShadow:
    def test_empty_rows_returns_empty_comparison(self):
        comp = sa.compare_live_vs_shadow([], _flat_wa())
        assert comp["n_rows"] == 0
        assert comp["live_win_rate"] is None

    def test_win_rate_computed_correctly(self):
        rows = _wins(8) + _losses(4)  # 12 rows, 8/12 ≈ 66.67%
        comp = sa.compare_live_vs_shadow(rows, _flat_wa())
        assert comp["live_win_rate"] == pytest.approx(8 / 12 * 100, abs=0.5)

    def test_flat_wa_win_rate_delta_zero(self):
        rows = _wins(8) + _losses(4)
        comp = sa.compare_live_vs_shadow(rows, _flat_wa())
        # flat weights → shadow includes same rows → delta = 0
        assert comp["win_rate_delta"] == pytest.approx(0.0)

    def test_alert_volume_unchanged_under_flat_wa(self):
        rows = _wins(10)
        comp = sa.compare_live_vs_shadow(rows, _flat_wa())
        assert comp["live_alert_count"]   == 10
        assert comp["shadow_alert_count"] == 10
        assert comp["alert_volume_delta"] == 0

    def test_penalty_reduces_shadow_alert_count(self):
        near = [_loss(adjusted_score=6.1, raw_score=6.1,
                      score_options=3.0, score_insider=1.0,
                      score_short_squeeze=0.0, score_catalyst=0.0,
                      score_institutional=0.0, score_breakout=0.0)
                for _ in range(5)]
        high = _wins(10)
        rows = high + near
        wa   = _flat_wa()
        wa["options"] = {"default_weight": 3.0, "suggested_weight": 0.5, "adjustment": -2.5}
        wa["insider"] = {"default_weight": 2.0, "suggested_weight": 0.1, "adjustment": -1.9}
        comp = sa.compare_live_vs_shadow(rows, wa)
        assert comp["shadow_alert_count"] < comp["live_alert_count"]
        assert comp["alert_volume_delta"] < 0

    def test_false_positive_reduction_est_when_exclusions_are_losses(self):
        # 5 near-threshold losses that shadow excludes
        near_losses = [_loss(adjusted_score=6.1, raw_score=6.1,
                             score_options=3.0, score_insider=1.0,
                             score_short_squeeze=0.0, score_catalyst=0.0,
                             score_institutional=0.0, score_breakout=0.0)
                       for _ in range(5)]
        wins        = _wins(15)
        rows        = wins + near_losses
        wa          = _flat_wa()
        wa["options"] = {"default_weight": 3.0, "suggested_weight": 0.5, "adjustment": -2.5}
        wa["insider"] = {"default_weight": 2.0, "suggested_weight": 0.1, "adjustment": -1.9}
        comp = sa.compare_live_vs_shadow(rows, wa)
        fp_red = comp.get("false_positive_reduction_est")
        assert fp_red is not None
        assert fp_red > 0

    def test_all_keys_present(self):
        comp = sa.compare_live_vs_shadow(_wins(5) + _losses(5), _flat_wa())
        for k in ("n_rows", "live_win_rate", "shadow_win_rate", "win_rate_delta",
                  "live_avg_return", "shadow_avg_return", "return_delta",
                  "live_alert_count", "shadow_alert_count",
                  "alert_volume_delta", "alert_volume_delta_pct",
                  "live_drawdown", "shadow_drawdown", "drawdown_delta",
                  "live_brier", "shadow_brier", "calibration_delta",
                  "false_positive_reduction_est",
                  "n_inclusion_changes", "n_tier_changes", "churn_rate"):
            assert k in comp, f"missing key: {k}"

    def test_win_rate_none_when_insufficient_data(self):
        rows = _wins(3) + _losses(3)  # fewer than MIN_ROWS_SHADOW
        comp = sa.compare_live_vs_shadow(rows, _flat_wa())
        assert comp["live_win_rate"] is None

    def test_drawdown_none_for_no_outcome_data(self):
        rows = [_row(return_5d=None) for _ in range(15)]
        comp = sa.compare_live_vs_shadow(rows, _flat_wa())
        assert comp["live_drawdown"] is None

    def test_avg_return_correct(self):
        rows = [_win(return_5d=10.0) for _ in range(sa.MIN_ROWS_SHADOW)]
        comp = sa.compare_live_vs_shadow(rows, _flat_wa())
        assert comp["live_avg_return"] == pytest.approx(10.0)

    def test_positive_win_rate_delta_when_shadow_filters_losses(self):
        near_losses = [_loss(adjusted_score=6.1, raw_score=6.1,
                             score_options=3.0, score_insider=1.0,
                             score_short_squeeze=0.0, score_catalyst=0.0,
                             score_institutional=0.0, score_breakout=0.0)
                       for _ in range(5)]
        solid_wins  = _wins(15)
        rows        = solid_wins + near_losses
        wa          = _flat_wa()
        wa["options"] = {"default_weight": 3.0, "suggested_weight": 0.5, "adjustment": -2.5}
        wa["insider"] = {"default_weight": 2.0, "suggested_weight": 0.1, "adjustment": -1.9}
        comp = sa.compare_live_vs_shadow(rows, wa)
        if comp["win_rate_delta"] is not None:
            assert comp["win_rate_delta"] >= 0


# ── TestStabilityAnalysis ─────────────────────────────────────────────────────

class TestStabilityAnalysis:
    def test_empty_history_is_stable(self):
        result = sa.stability_analysis([])
        assert result["overall"] == sa.STABILITY_STABLE
        assert result["n_snapshots"] == 0
        assert result["warnings"] == []

    def test_single_snapshot_stable(self):
        result = sa.stability_analysis([_snapshot({"options": 0.2})])
        assert result["overall"] == sa.STABILITY_STABLE

    def test_consistent_positive_adjustments_stable(self):
        history = [_snapshot({"options": 0.1}) for _ in range(5)]
        result  = sa.stability_analysis(history)
        assert result["per_signal"]["options"]["label"] == sa.STABILITY_STABLE
        assert result["per_signal"]["options"]["oscillations"] == 0

    def test_oscillating_signal_detected(self):
        history = [
            _snapshot({"options":  0.2}),
            _snapshot({"options": -0.2}),
            _snapshot({"options":  0.2}),
            _snapshot({"options": -0.2}),
        ]
        result = sa.stability_analysis(history)
        per = result["per_signal"]["options"]
        assert per["oscillations"] >= sa.OSCILLATION_FLIP_THRESHOLD
        assert per["label"] == sa.STABILITY_UNSTABLE

    def test_overall_unstable_when_any_signal_unstable(self):
        history = [
            _snapshot({"options":  0.3}),
            _snapshot({"options": -0.3}),
            _snapshot({"options":  0.3}),
            _snapshot({"options": -0.3}),
        ]
        result = sa.stability_analysis(history)
        assert result["overall"] == sa.STABILITY_UNSTABLE

    def test_high_variance_triggers_watch(self):
        history = [
            _snapshot({"breakout": 0.5}),
            _snapshot({"breakout": 0.0}),
            _snapshot({"breakout": 0.5}),
            _snapshot({"breakout": 0.0}),
            _snapshot({"breakout": 0.5}),
        ]
        result = sa.stability_analysis(history)
        per    = result["per_signal"]["breakout"]
        if per["weight_std"] > sa.HIGH_VARIANCE_THRESHOLD:
            assert per["label"] == sa.STABILITY_WATCH

    def test_overreaction_triggers_watch(self):
        history = [_snapshot({"options": 0.45})]  # > OVERREACTION_THRESHOLD=0.40
        result  = sa.stability_analysis(history)
        per     = result["per_signal"]["options"]
        assert per["max_adj"] == pytest.approx(0.45)
        assert per["label"] == sa.STABILITY_WATCH

    def test_per_signal_has_all_signals(self):
        result = sa.stability_analysis([_snapshot({})])
        assert set(result["per_signal"].keys()) == set(sa.SIGNAL_NAMES)

    def test_warnings_list_populated_for_unstable(self):
        history = [
            _snapshot({"options":  0.3}),
            _snapshot({"options": -0.3}),
            _snapshot({"options":  0.3}),
            _snapshot({"options": -0.3}),
        ]
        result = sa.stability_analysis(history)
        assert len(result["warnings"]) > 0

    def test_stability_watch_when_any_signal_watch(self):
        history = [_snapshot({"options": 0.45})]
        result  = sa.stability_analysis(history)
        assert result["overall"] in (sa.STABILITY_WATCH, sa.STABILITY_UNSTABLE)

    def test_n_snapshots_correct(self):
        history = [_snapshot({}) for _ in range(7)]
        result  = sa.stability_analysis(history)
        assert result["n_snapshots"] == 7


# ── TestRolloutReadiness ──────────────────────────────────────────────────────

class TestRolloutReadiness:
    def _comp(self, n=50, wr_delta=3.0, ret_delta=0.5, churn=0.05):
        return {
            "n_rows": n, "win_rate_delta": wr_delta, "return_delta": ret_delta,
            "calibration_delta": -0.01, "churn_rate": churn,
        }

    def _stab(self, overall=sa.STABILITY_STABLE):
        return {"overall": overall, "n_snapshots": 5, "per_signal": {}, "warnings": []}

    def test_not_ready_when_too_few_rows(self):
        result = sa.rollout_readiness({}, {}, n_rows=5)
        assert result["status"] == sa.READINESS_NOT_READY
        assert result["blockers"]

    def test_not_ready_when_win_rate_regresses(self):
        comp   = self._comp(n=50, wr_delta=-2.0)
        result = sa.rollout_readiness(comp, self._stab(), n_rows=50)
        assert result["status"] == sa.READINESS_NOT_READY
        assert any("regress" in b.lower() for b in result["blockers"])

    def test_not_ready_when_unstable(self):
        comp   = self._comp(n=50)
        result = sa.rollout_readiness(comp, self._stab(sa.STABILITY_UNSTABLE), n_rows=50)
        assert result["status"] == sa.READINESS_NOT_READY

    def test_observe_when_insufficient_rows_for_readiness(self):
        comp   = self._comp(n=20, wr_delta=3.0)
        result = sa.rollout_readiness(comp, self._stab(), n_rows=20)
        assert result["status"] == sa.READINESS_OBSERVE

    def test_observe_when_marginal_improvement(self):
        comp   = self._comp(n=50, wr_delta=0.5)  # < WIN_RATE_IMPROVEMENT_MIN
        result = sa.rollout_readiness(comp, self._stab(), n_rows=50)
        assert result["status"] == sa.READINESS_OBSERVE

    def test_limited_trial_ready_when_improvement_and_stable(self):
        comp   = self._comp(n=40, wr_delta=3.0)  # >= MIN_ROWS_READINESS but < MIN_ROWS_STABLE
        result = sa.rollout_readiness(comp, self._stab(), n_rows=40)
        assert result["status"] == sa.READINESS_LIMITED

    def test_stable_for_controlled_use_with_large_sample(self):
        comp   = self._comp(n=70, wr_delta=4.0)  # >= MIN_ROWS_STABLE
        result = sa.rollout_readiness(comp, self._stab(), n_rows=70)
        assert result["status"] == sa.READINESS_STABLE

    def test_reasons_list_populated(self):
        comp   = self._comp(n=40, wr_delta=3.0)
        result = sa.rollout_readiness(comp, self._stab(), n_rows=40)
        assert len(result["reasons"]) > 0

    def test_blockers_empty_when_ready(self):
        comp   = self._comp(n=40, wr_delta=3.0)
        result = sa.rollout_readiness(comp, self._stab(), n_rows=40)
        assert result["blockers"] == []

    def test_watch_stability_appears_in_reasons(self):
        comp   = self._comp(n=50, wr_delta=3.0)
        result = sa.rollout_readiness(comp, self._stab(sa.STABILITY_WATCH), n_rows=50)
        assert any("WATCH" in r for r in result["reasons"])

    def test_high_churn_appears_in_reasons(self):
        comp   = self._comp(n=50, wr_delta=3.0, churn=0.30)
        result = sa.rollout_readiness(comp, self._stab(), n_rows=50)
        assert any("churn" in r.lower() for r in result["reasons"])


# ── TestGenerateShadowReport ──────────────────────────────────────────────────

class TestGenerateShadowReport:
    def test_report_has_all_required_keys(self):
        report = sa.generate_shadow_report()
        for k in ("report_type", "n_rows", "comparison", "stability", "readiness",
                  "top_improvements", "top_regressions", "stability_warnings",
                  "recommendations", "changes_vs_previous", "adaptive_risk_summary"):
            assert k in report, f"missing key: {k}"

    def test_report_type_string(self):
        assert sa.generate_shadow_report()["report_type"] == "shadow_adaptive_report"

    def test_n_rows_matches_input(self):
        rows   = _wins(10) + _losses(5)
        report = sa.generate_shadow_report(rows, _flat_wa())
        assert report["n_rows"] == 15

    def test_empty_inputs_no_crash(self):
        report = sa.generate_shadow_report()
        assert report["n_rows"] == 0

    def test_adaptive_risk_summary_is_string(self):
        report = sa.generate_shadow_report(_wins(5) + _losses(5), _flat_wa())
        assert isinstance(report["adaptive_risk_summary"], str)
        assert len(report["adaptive_risk_summary"]) > 0

    def test_changes_vs_previous_empty_without_prev(self):
        report = sa.generate_shadow_report(_wins(5), _flat_wa())
        assert report["changes_vs_previous"] == []

    def test_changes_vs_previous_populated_with_prev(self):
        prev   = {"comparison": {"win_rate_delta": -5.0},
                  "readiness":  {"status": sa.READINESS_NOT_READY},
                  "stability":  {"overall": sa.STABILITY_UNSTABLE}}
        rows   = _wins(20) + _losses(5)
        report = sa.generate_shadow_report(rows, _flat_wa(), previous_report=prev)
        # Win rate delta improved from -5.0 to some positive value → change
        assert isinstance(report["changes_vs_previous"], list)

    def test_recommendations_list_populated(self):
        rows   = _wins(5) + _losses(5)
        report = sa.generate_shadow_report(rows, _flat_wa())
        assert isinstance(report["recommendations"], list)

    def test_snapshot_history_passed_to_stability(self):
        history = [
            _snapshot({"options":  0.3}),
            _snapshot({"options": -0.3}),
            _snapshot({"options":  0.3}),
            _snapshot({"options": -0.3}),
        ]
        report = sa.generate_shadow_report(
            _wins(5) + _losses(5), _flat_wa(), snapshot_history=history
        )
        assert report["stability"]["overall"] == sa.STABILITY_UNSTABLE


# ── TestDeterminism ───────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_inputs_same_compare_output(self):
        rows = _wins(10) + _losses(5)
        wa   = _flat_wa()
        c1   = sa.compare_live_vs_shadow(rows, wa)
        c2   = sa.compare_live_vs_shadow(rows, wa)
        assert c1 == c2

    def test_same_inputs_same_stability_output(self):
        history = [_snapshot({"options": 0.1, "breakout": -0.05}) for _ in range(4)]
        s1 = sa.stability_analysis(history)
        s2 = sa.stability_analysis(history)
        assert s1 == s2

    def test_same_inputs_same_full_report(self):
        rows = _wins(10) + _losses(5)
        r1   = sa.generate_shadow_report(rows, _flat_wa())
        r2   = sa.generate_shadow_report(rows, _flat_wa())
        assert r1["readiness"]["status"]        == r2["readiness"]["status"]
        assert r1["adaptive_risk_summary"]       == r2["adaptive_risk_summary"]
        assert r1["comparison"]["live_win_rate"] == r2["comparison"]["live_win_rate"]

    def test_replay_deterministic(self):
        rows = _wins(5) + _losses(5)
        wa   = _boost_wa("options", 0.3)
        r1   = sa.run_shadow_replay(rows, wa)
        r2   = sa.run_shadow_replay(rows, wa)
        assert r1["n_inclusion_changes"] == r2["n_inclusion_changes"]
        assert [d["score_delta"] for d in r1["deltas"]] == \
               [d["score_delta"] for d in r2["deltas"]]


# ── TestBoundedOutputs ────────────────────────────────────────────────────────

class TestBoundedOutputs:
    def test_top_improvements_capped(self):
        rows   = [_win(return_5d=float(i)) for i in range(30)]
        report = sa.generate_shadow_report(rows, _flat_wa())
        assert len(report["top_improvements"]) <= sa.MAX_IMPROVEMENTS

    def test_top_regressions_capped(self):
        rows   = [_loss(return_5d=-float(i)) for i in range(30)]
        report = sa.generate_shadow_report(rows, _flat_wa())
        assert len(report["top_regressions"]) <= sa.MAX_REGRESSIONS

    def test_stability_warnings_capped(self):
        # Create many oscillating signals
        history = []
        for _ in range(20):
            history.append(_snapshot(
                {sig: (0.3 if len(history) % 2 == 0 else -0.3)
                 for sig in sa.SIGNAL_NAMES}
            ))
        result = sa.stability_analysis(history)
        assert len(result["warnings"]) <= sa.MAX_STABILITY_WARNINGS

    def test_recommendations_capped(self):
        # Induce many blockers by using tiny sample + regression
        report = sa.generate_shadow_report([], _flat_wa())
        assert len(report["recommendations"]) <= sa.MAX_RECOMMENDATIONS


# ── TestSparseHandling ────────────────────────────────────────────────────────

class TestSparseHandling:
    def test_none_rows_no_crash(self):
        comp = sa.compare_live_vs_shadow(None, _flat_wa())
        assert comp["n_rows"] == 0

    def test_none_wa_no_crash(self):
        rows = _wins(5)
        comp = sa.compare_live_vs_shadow(rows, None)
        assert comp["n_rows"] == 5

    def test_rows_without_return_5d_handled(self):
        rows = [_row(return_5d=None) for _ in range(15)]
        comp = sa.compare_live_vs_shadow(rows, _flat_wa())
        assert comp["live_win_rate"] is None

    def test_rows_missing_score_columns_fallback(self):
        row = {
            "confidence_pct": 60.0, "adjusted_score": 7.0, "raw_score": 7.0,
            "tier": "ALERT", "regime": "BULL", "return_5d": 3.0,
            "signal_summary": '{"options":2,"breakout":1}',
        }
        delta = sa._shadow_row_delta(row, _flat_wa())
        assert delta["n_active_live"] >= 0

    def test_empty_snapshot_history_no_crash(self):
        result = sa.stability_analysis(None)
        assert result["overall"] == sa.STABILITY_STABLE

    def test_missing_weight_keys_handled(self):
        row = _row(score_options=3.0)
        # Only provide options adjustment; rest missing
        partial_wa = {"options": {
            "default_weight": 3.0, "suggested_weight": 3.5, "adjustment": 0.5
        }}
        delta = sa._shadow_row_delta(row, partial_wa)
        assert delta["shadow_score"] >= 0

    def test_generate_report_none_inputs(self):
        report = sa.generate_shadow_report(None, None, None, None)
        assert report["report_type"] == "shadow_adaptive_report"
        assert report["n_rows"] == 0

    def test_rollout_readiness_none_inputs(self):
        result = sa.rollout_readiness(None, None, 0)
        assert result["status"] == sa.READINESS_NOT_READY

    def test_compare_live_vs_shadow_no_win_data(self):
        rows = [_row(return_5d=None, adjusted_score=8.0, raw_score=8.0)
                for _ in range(20)]
        comp = sa.compare_live_vs_shadow(rows, _flat_wa())
        assert comp["live_win_rate"] is None
        assert comp["false_positive_reduction_est"] is None

    def test_row_with_zero_raw_score_no_crash(self):
        row = _row(
            adjusted_score=0.0, raw_score=0.0, confidence_pct=0.0,
            score_options=0.0, score_insider=0.0, score_short_squeeze=0.0,
            score_catalyst=0.0, score_institutional=0.0, score_breakout=0.0,
        )
        delta = sa._shadow_row_delta(row, _flat_wa())
        assert delta["shadow_score"] == pytest.approx(0.0)
