"""
Unit tests for strategy_replay.py (Phase 3B).

All tests pass mock rows directly — no DB access, no network calls.
Covers: replay determinism, counterfactual math, threshold sweep correctness,
sensitivity calculations, robustness detection, sparse handling.
"""
import json
import pytest

from strategy_replay import (
    BASELINE_CONVICTION_MIN_SIGS,
    BASELINE_CONVICTION_THRESHOLD,
    BASELINE_SPEC,
    CONFIDENCE_SWEEP,
    CONVICTION_ONLY_SPEC,
    DEFAULT_REGIME_PENALTIES,
    FRAGILE_SAMPLE_RATIO,
    FRAGILE_WIN_IMPROVEMENT_THRESHOLD,
    LOOSER_RISK_OFF_SPEC,
    MIN_RELIABLE_SAMPLE,
    NEUTRAL_PENALTY_SWEEP,
    NO_RISK_OFF_SPEC,
    OVERFIT_ALERT_REDUCTION_THRESHOLD,
    PRESET_SPECS,
    RISK_OFF_PENALTY_SWEEP,
    SIGNAL_NAMES,
    SIGNAL_WEIGHT_DELTAS,
    STRICT_CONFIDENCE_65_SPEC,
    STRICT_CONFIDENCE_70_SPEC,
    TIGHTER_NEUTRAL_SPEC,
    ReplaySpec,
    _replay_confidence,
    _replay_stats,
    _row_passes,
    _sig_scores,
    apply_replay,
    compare_replay,
    confidence_threshold_sweep,
    generate_recommendation,
    generate_report,
    penalty_sweep,
    replay_stats,
    robustness_analysis,
    sensitivity_summary,
    weight_perturbation_analysis,
)


# ── Row-building helpers ──────────────────────────────────────────────────────

def _sig(signals: dict) -> str:
    """Encode a signal dict to compact JSON string."""
    return json.dumps({k: v for k, v in signals.items() if v > 0})


def _row(
    regime="BULL",
    confidence_pct=60.0,
    return_5d=None,
    return_20d=None,
    max_drawdown_pct=None,
    signals: dict = None,
    tier="ALERT",
):
    return {
        "regime":           regime,
        "confidence_pct":   confidence_pct,
        "return_5d":        return_5d,
        "return_20d":       return_20d,
        "max_drawdown_pct": max_drawdown_pct,
        "signal_summary":   _sig(signals or {}),
        "tier":             tier,
    }


def _win(regime="BULL", conf=70.0, ret=5.0, dd=-2.0, signals=None):
    return _row(regime=regime, confidence_pct=conf, return_5d=ret,
                max_drawdown_pct=dd, signals=signals or {})


def _loss(regime="BULL", conf=50.0, ret=-3.0, dd=-5.0, signals=None):
    return _row(regime=regime, confidence_pct=conf, return_5d=ret,
                max_drawdown_pct=dd, signals=signals or {})


def _make(n, win=True, regime="BULL", conf=65.0, ret=None, dd=None, signals=None):
    if win:
        return [_win(regime=regime, conf=conf, ret=ret or 5.0,
                     dd=dd or -2.0, signals=signals or {}) for _ in range(n)]
    return [_loss(regime=regime, conf=conf, ret=ret or -3.0,
                  dd=dd or -5.0, signals=signals or {}) for _ in range(n)]


# ── TestConstants ─────────────────────────────────────────────────────────────

class TestConstants:
    def test_baseline_conviction_threshold(self):
        assert BASELINE_CONVICTION_THRESHOLD == 55.0

    def test_baseline_conviction_min_sigs(self):
        assert BASELINE_CONVICTION_MIN_SIGS == 3

    def test_default_regime_penalties_keys(self):
        assert set(DEFAULT_REGIME_PENALTIES.keys()) == {"BULL", "NEUTRAL", "RISK_OFF"}

    def test_default_regime_penalties_values(self):
        assert DEFAULT_REGIME_PENALTIES["BULL"]     == 1.00
        assert DEFAULT_REGIME_PENALTIES["NEUTRAL"]  == 0.90
        assert DEFAULT_REGIME_PENALTIES["RISK_OFF"] == 0.75

    def test_confidence_sweep_is_ascending(self):
        assert list(CONFIDENCE_SWEEP) == sorted(CONFIDENCE_SWEEP)

    def test_confidence_sweep_length(self):
        assert len(CONFIDENCE_SWEEP) == 9

    def test_signal_weight_deltas_contains_zero(self):
        assert 0.0 in SIGNAL_WEIGHT_DELTAS

    def test_min_reliable_sample(self):
        assert MIN_RELIABLE_SAMPLE == 10

    def test_fragile_win_improvement_threshold(self):
        assert FRAGILE_WIN_IMPROVEMENT_THRESHOLD == 5.0

    def test_overfit_alert_reduction_threshold(self):
        assert OVERFIT_ALERT_REDUCTION_THRESHOLD == 50.0

    def test_fragile_sample_ratio(self):
        assert FRAGILE_SAMPLE_RATIO == 0.30

    def test_preset_specs_count(self):
        assert len(PRESET_SPECS) == 7

    def test_signal_names_tuple(self):
        assert isinstance(SIGNAL_NAMES, tuple)
        assert "options" in SIGNAL_NAMES
        assert "insider" in SIGNAL_NAMES


# ── TestReplaySpec ────────────────────────────────────────────────────────────

class TestReplaySpec:
    def test_default_fields(self):
        s = ReplaySpec()
        assert s.name                     == "baseline"
        assert s.description              == ""
        assert s.min_confidence           == 0.0
        assert s.suppress_regimes         == ()
        assert s.regime_penalties         is None
        assert s.require_signals          == ()
        assert s.exclude_signals          == ()
        assert s.min_active_signals       == 0
        assert s.signal_confidence_deltas is None

    def test_custom_fields(self):
        s = ReplaySpec(
            name="custom",
            min_confidence=65.0,
            suppress_regimes=("RISK_OFF",),
            require_signals=("options",),
        )
        assert s.name == "custom"
        assert s.min_confidence == 65.0
        assert "RISK_OFF" in s.suppress_regimes
        assert "options" in s.require_signals

    def test_preset_baseline(self):
        assert BASELINE_SPEC.name == "baseline"
        assert BASELINE_SPEC.min_confidence == 0.0
        assert BASELINE_SPEC.regime_penalties is None

    def test_preset_strict_65(self):
        assert STRICT_CONFIDENCE_65_SPEC.min_confidence == 65.0

    def test_preset_strict_70(self):
        assert STRICT_CONFIDENCE_70_SPEC.min_confidence == 70.0

    def test_preset_no_risk_off(self):
        assert "RISK_OFF" in NO_RISK_OFF_SPEC.suppress_regimes

    def test_preset_conviction_only(self):
        assert CONVICTION_ONLY_SPEC.min_confidence == BASELINE_CONVICTION_THRESHOLD
        assert CONVICTION_ONLY_SPEC.min_active_signals == BASELINE_CONVICTION_MIN_SIGS

    def test_preset_tighter_neutral(self):
        assert TIGHTER_NEUTRAL_SPEC.regime_penalties["NEUTRAL"] == 0.80
        assert TIGHTER_NEUTRAL_SPEC.regime_penalties["BULL"]    == 1.00

    def test_preset_looser_risk_off(self):
        assert LOOSER_RISK_OFF_SPEC.regime_penalties["RISK_OFF"] == 0.90


# ── TestSigScores ─────────────────────────────────────────────────────────────

class TestSigScores:
    def test_empty_summary(self):
        assert _sig_scores({"signal_summary": "{}"}) == {}

    def test_none_summary(self):
        assert _sig_scores({"signal_summary": None}) == {}

    def test_missing_key(self):
        assert _sig_scores({}) == {}

    def test_active_signals_parsed(self):
        row = {"signal_summary": '{"options":3,"insider":2}'}
        result = _sig_scores(row)
        assert result == {"options": 3, "insider": 2}

    def test_zero_score_excluded(self):
        row = {"signal_summary": '{"options":2,"insider":0}'}
        result = _sig_scores(row)
        assert "insider" not in result
        assert result["options"] == 2

    def test_invalid_json_returns_empty(self):
        assert _sig_scores({"signal_summary": "not_json"}) == {}

    def test_non_string_summary(self):
        assert _sig_scores({"signal_summary": 12345}) == {}


# ── TestReplayConfidence ──────────────────────────────────────────────────────

class TestReplayConfidence:
    def test_baseline_spec_unchanged(self):
        row = _row(regime="BULL", confidence_pct=72.0)
        # BASELINE_SPEC has no regime_penalties → unchanged
        result = _replay_confidence(row, BASELINE_SPEC)
        assert result == 72.0

    def test_no_regime_penalties_unchanged(self):
        row = _row(regime="NEUTRAL", confidence_pct=60.0)
        spec = ReplaySpec(regime_penalties=None)
        assert _replay_confidence(row, spec) == 60.0

    def test_same_factor_unchanged(self):
        row = _row(regime="NEUTRAL", confidence_pct=72.0)
        spec = ReplaySpec(
            regime_penalties={"BULL": 1.0, "NEUTRAL": 0.90, "RISK_OFF": 0.75}
        )
        # new factor == orig factor → no change
        assert _replay_confidence(row, spec) == 72.0

    def test_tighter_neutral_lowers_confidence(self):
        # orig NEUTRAL factor = 0.90; new = 0.80
        # pre_penalty = 72.0 / 0.90 = 80.0; replayed = 80.0 * 0.80 = 64.0
        row = _row(regime="NEUTRAL", confidence_pct=72.0)
        spec = ReplaySpec(
            regime_penalties={"BULL": 1.00, "NEUTRAL": 0.80, "RISK_OFF": 0.75}
        )
        result = _replay_confidence(row, spec)
        assert abs(result - 64.0) < 0.01

    def test_looser_risk_off_raises_confidence(self):
        # orig RISK_OFF factor = 0.75; new = 0.90
        # pre_penalty = 60.0 / 0.75 = 80.0; replayed = 80.0 * 0.90 = 72.0
        row = _row(regime="RISK_OFF", confidence_pct=60.0)
        spec = ReplaySpec(
            regime_penalties={"BULL": 1.00, "NEUTRAL": 0.90, "RISK_OFF": 0.90}
        )
        result = _replay_confidence(row, spec)
        assert abs(result - 72.0) < 0.01

    def test_signal_delta_adds_to_confidence(self):
        row = _row(confidence_pct=60.0, signals={"options": 3})
        spec = ReplaySpec(signal_confidence_deltas={"options": 10.0})
        assert _replay_confidence(row, spec) == 70.0

    def test_signal_delta_inactive_signal_no_change(self):
        row = _row(confidence_pct=60.0, signals={})
        spec = ReplaySpec(signal_confidence_deltas={"options": 10.0})
        # options not active → no change
        assert _replay_confidence(row, spec) == 60.0

    def test_signal_delta_clamps_at_100(self):
        row = _row(confidence_pct=95.0, signals={"insider": 2})
        spec = ReplaySpec(signal_confidence_deltas={"insider": 15.0})
        assert _replay_confidence(row, spec) == 100.0

    def test_signal_delta_clamps_at_zero(self):
        row = _row(confidence_pct=5.0, signals={"breakout": 2})
        spec = ReplaySpec(signal_confidence_deltas={"breakout": -20.0})
        assert _replay_confidence(row, spec) == 0.0

    def test_multiple_signal_deltas(self):
        row = _row(confidence_pct=50.0, signals={"options": 2, "insider": 1})
        spec = ReplaySpec(signal_confidence_deltas={"options": 5.0, "insider": 3.0})
        assert _replay_confidence(row, spec) == 58.0

    def test_unknown_regime_uses_factor_1(self):
        # Unknown regime → orig_factor defaults to 1.0
        row = _row(regime="UNKNOWN", confidence_pct=60.0)
        spec = ReplaySpec(
            regime_penalties={"BULL": 1.0, "NEUTRAL": 0.9, "RISK_OFF": 0.75}
        )
        # orig=1.0, new=1.0 (key not in spec.regime_penalties, so new=orig) → no change
        result = _replay_confidence(row, spec)
        assert result == 60.0


# ── TestRowPasses ─────────────────────────────────────────────────────────────

class TestRowPasses:
    def test_baseline_all_pass(self):
        row = _row(regime="BULL", confidence_pct=30.0)
        assert _row_passes(row, BASELINE_SPEC, 30.0) is True

    def test_suppress_regime_excluded(self):
        row = _row(regime="RISK_OFF")
        spec = ReplaySpec(suppress_regimes=("RISK_OFF",))
        assert _row_passes(row, spec, 60.0) is False

    def test_suppress_regime_other_pass(self):
        row = _row(regime="BULL")
        spec = ReplaySpec(suppress_regimes=("RISK_OFF",))
        assert _row_passes(row, spec, 60.0) is True

    def test_min_confidence_exact_pass(self):
        spec = ReplaySpec(min_confidence=65.0)
        assert _row_passes(_row(), spec, 65.0) is True

    def test_min_confidence_below_fails(self):
        spec = ReplaySpec(min_confidence=65.0)
        assert _row_passes(_row(), spec, 64.9) is False

    def test_require_signal_present_pass(self):
        row = _row(signals={"options": 3})
        spec = ReplaySpec(require_signals=("options",))
        assert _row_passes(row, spec, 60.0) is True

    def test_require_signal_missing_fail(self):
        row = _row(signals={})
        spec = ReplaySpec(require_signals=("options",))
        assert _row_passes(row, spec, 60.0) is False

    def test_require_multiple_signals_all_present(self):
        row = _row(signals={"options": 2, "insider": 1})
        spec = ReplaySpec(require_signals=("options", "insider"))
        assert _row_passes(row, spec, 60.0) is True

    def test_require_multiple_signals_one_missing(self):
        row = _row(signals={"options": 2})
        spec = ReplaySpec(require_signals=("options", "insider"))
        assert _row_passes(row, spec, 60.0) is False

    def test_exclude_signal_active_fail(self):
        row = _row(signals={"breakout": 2})
        spec = ReplaySpec(exclude_signals=("breakout",))
        assert _row_passes(row, spec, 60.0) is False

    def test_exclude_signal_inactive_pass(self):
        row = _row(signals={"options": 2})
        spec = ReplaySpec(exclude_signals=("breakout",))
        assert _row_passes(row, spec, 60.0) is True

    def test_min_active_signals_met(self):
        row = _row(signals={"options": 2, "insider": 1, "breakout": 2})
        spec = ReplaySpec(min_active_signals=3)
        assert _row_passes(row, spec, 60.0) is True

    def test_min_active_signals_not_met(self):
        row = _row(signals={"options": 2})
        spec = ReplaySpec(min_active_signals=3)
        assert _row_passes(row, spec, 60.0) is False

    def test_combined_filters_all_must_pass(self):
        row = _row(regime="BULL", confidence_pct=70.0, signals={"options": 2})
        spec = ReplaySpec(
            min_confidence=65.0,
            suppress_regimes=("RISK_OFF",),
            require_signals=("options",),
            min_active_signals=1,
        )
        assert _row_passes(row, spec, 70.0) is True

    def test_combined_filters_one_fails(self):
        row = _row(regime="BULL", confidence_pct=70.0, signals={})
        spec = ReplaySpec(
            min_confidence=65.0,
            require_signals=("options",),
        )
        assert _row_passes(row, spec, 70.0) is False


# ── TestApplyReplay ───────────────────────────────────────────────────────────

class TestApplyReplay:
    def test_baseline_returns_all(self):
        rows = _make(5, win=True)
        result = apply_replay(rows, BASELINE_SPEC)
        assert len(result) == 5

    def test_returns_copies_not_originals(self):
        rows = [_win(conf=70.0)]
        result = apply_replay(rows, BASELINE_SPEC)
        assert result[0] is not rows[0]

    def test_confidence_pct_updated_in_copy(self):
        row = _row(regime="NEUTRAL", confidence_pct=72.0)
        spec = ReplaySpec(
            regime_penalties={"BULL": 1.00, "NEUTRAL": 0.80, "RISK_OFF": 0.75}
        )
        result = apply_replay([row], spec)
        assert len(result) == 1
        assert abs(result[0]["confidence_pct"] - 64.0) < 0.01

    def test_original_row_unchanged_after_replay(self):
        row = _row(regime="NEUTRAL", confidence_pct=72.0)
        spec = ReplaySpec(
            regime_penalties={"BULL": 1.00, "NEUTRAL": 0.80, "RISK_OFF": 0.75}
        )
        apply_replay([row], spec)
        assert row["confidence_pct"] == 72.0  # original untouched

    def test_no_risk_off_excludes_risk_off(self):
        rows = _make(3, regime="BULL") + _make(2, regime="RISK_OFF")
        result = apply_replay(rows, NO_RISK_OFF_SPEC)
        assert len(result) == 3
        assert all(r["regime"] == "BULL" for r in result)

    def test_min_confidence_filter(self):
        rows = [
            _win(conf=50.0),
            _win(conf=65.0),
            _win(conf=70.0),
        ]
        spec = ReplaySpec(min_confidence=65.0)
        result = apply_replay(rows, spec)
        assert len(result) == 2
        assert all(r["confidence_pct"] >= 65.0 for r in result)

    def test_empty_rows_returns_empty(self):
        assert apply_replay([], BASELINE_SPEC) == []

    def test_determinism(self):
        rows = _make(10, win=True) + _make(5, win=False)
        r1 = apply_replay(rows, STRICT_CONFIDENCE_65_SPEC)
        r2 = apply_replay(rows, STRICT_CONFIDENCE_65_SPEC)
        assert [r["confidence_pct"] for r in r1] == [r["confidence_pct"] for r in r2]

    def test_conviction_only_filters_low_signal_count(self):
        rows = [
            _win(conf=60.0, signals={"options": 2, "insider": 1, "breakout": 2}),  # 3 sigs
            _win(conf=60.0, signals={"options": 2}),  # 1 sig
            _win(conf=60.0, signals={"options": 2, "insider": 1}),  # 2 sigs
        ]
        result = apply_replay(rows, CONVICTION_ONLY_SPEC)
        assert len(result) == 1

    def test_conviction_only_filters_low_confidence(self):
        rows = [
            _win(conf=54.0, signals={"options": 2, "insider": 1, "breakout": 2}),
            _win(conf=56.0, signals={"options": 2, "insider": 1, "breakout": 2}),
        ]
        result = apply_replay(rows, CONVICTION_ONLY_SPEC)
        assert len(result) == 1
        assert result[0]["confidence_pct"] == 56.0


# ── TestReplayStats ───────────────────────────────────────────────────────────

class TestReplayStats:
    def test_alert_reduction_pct_zero_when_all_included(self):
        rows = _make(5, win=True)
        stats = _replay_stats(rows, rows)
        assert stats["alert_reduction_pct"] == 0.0

    def test_alert_reduction_pct_correct(self):
        rows = _make(10, win=True)
        included = rows[:6]
        stats = _replay_stats(included, rows)
        assert stats["alert_reduction_pct"] == 40.0

    def test_n_and_total_correct(self):
        rows = _make(8, win=True)
        stats = _replay_stats(rows[:3], rows)
        assert stats["n"] == 3
        assert stats["total"] == 8

    def test_win_rate_none_below_min_rows(self):
        rows = _make(3, win=True)  # < MIN_ROWS_FOR_STATS=5
        stats = _replay_stats(rows, rows)
        assert stats["win_rate"] is None

    def test_win_rate_computed_with_sufficient_rows(self):
        rows = _make(5, win=True)
        stats = _replay_stats(rows, rows)
        assert stats["win_rate"] == 100.0

    def test_avg_return_5d(self):
        rows = [
            _win(ret=5.0),
            _win(ret=10.0),
            _win(ret=15.0),
            _win(ret=20.0),
            _win(ret=0.0),
        ]
        stats = _replay_stats(rows, rows)
        assert stats["avg_return_5d"] == pytest.approx(10.0)

    def test_avg_max_dd(self):
        rows = [
            _win(dd=-2.0),
            _win(dd=-4.0),
            _win(dd=-6.0),
            _win(dd=-8.0),
            _win(dd=-10.0),
        ]
        stats = _replay_stats(rows, rows)
        assert stats["avg_max_dd"] == pytest.approx(-6.0)

    def test_risk_adj_score(self):
        # avg_r5d=10.0, avg_dd=-6.0 → 10.0 - 0.5*6.0 = 7.0
        rows = [
            _win(ret=10.0, dd=-6.0),
            _win(ret=10.0, dd=-6.0),
            _win(ret=10.0, dd=-6.0),
            _win(ret=10.0, dd=-6.0),
            _win(ret=10.0, dd=-6.0),
        ]
        stats = _replay_stats(rows, rows)
        assert stats["risk_adj_score"] == pytest.approx(7.0)

    def test_risk_adj_none_when_no_returns(self):
        rows = [_row() for _ in range(5)]  # no return_5d
        stats = _replay_stats(rows, rows)
        assert stats["risk_adj_score"] is None

    def test_total_zero_no_division_error(self):
        stats = _replay_stats([], [])
        assert stats["alert_reduction_pct"] == 0.0

    def test_name_added_by_replay_stats(self):
        rows = _make(5)
        stats = replay_stats(rows, BASELINE_SPEC)
        assert stats["name"] == "baseline"


# ── TestCompareReplay ─────────────────────────────────────────────────────────

class TestCompareReplay:
    def _rows(self):
        return (
            _make(5, win=True, conf=70.0)
            + _make(5, win=True, conf=50.0)
            + _make(3, win=False, conf=70.0)
            + _make(2, win=False, conf=50.0)
        )

    def test_keys_present(self):
        result = compare_replay(_make(5), BASELINE_SPEC, NO_RISK_OFF_SPEC)
        assert set(result.keys()) == {"baseline", "alternative", "deltas", "recommendation"}

    def test_baseline_n_equals_total(self):
        rows = _make(5)
        result = compare_replay(rows, BASELINE_SPEC, NO_RISK_OFF_SPEC)
        assert result["baseline"]["n"] == 5

    def test_alternative_n_leq_baseline_n(self):
        rows = _make(5, regime="BULL") + _make(3, regime="RISK_OFF")
        result = compare_replay(rows, BASELINE_SPEC, NO_RISK_OFF_SPEC)
        assert result["alternative"]["n"] <= result["baseline"]["n"]

    def test_no_risk_off_excludes_risk_off_rows(self):
        rows = _make(5, regime="BULL") + _make(3, regime="RISK_OFF")
        result = compare_replay(rows, BASELINE_SPEC, NO_RISK_OFF_SPEC)
        assert result["alternative"]["n"] == 5

    def test_deltas_computed_correctly(self):
        rows = self._rows()
        result = compare_replay(rows, BASELINE_SPEC, STRICT_CONFIDENCE_65_SPEC)
        deltas = result["deltas"]
        assert "win_rate" in deltas
        assert "avg_return_5d" in deltas
        assert "risk_adj_score" in deltas

    def test_delta_none_when_insufficient_data(self):
        rows = _make(2)  # below MIN_ROWS_FOR_STATS
        result = compare_replay(rows, BASELINE_SPEC, STRICT_CONFIDENCE_70_SPEC)
        # win_rate will be None for both → delta should be None
        assert result["deltas"]["win_rate"] is None

    def test_recommendation_is_string(self):
        rows = self._rows()
        result = compare_replay(rows, BASELINE_SPEC, STRICT_CONFIDENCE_65_SPEC)
        assert isinstance(result["recommendation"], str)
        assert len(result["recommendation"]) > 0

    def test_determinism(self):
        rows = self._rows()
        r1 = compare_replay(rows, BASELINE_SPEC, NO_RISK_OFF_SPEC)
        r2 = compare_replay(rows, BASELINE_SPEC, NO_RISK_OFF_SPEC)
        assert r1["deltas"] == r2["deltas"]


# ── TestConfidenceThresholdSweep ──────────────────────────────────────────────

class TestConfidenceThresholdSweep:
    def test_returns_one_entry_per_threshold(self):
        rows = _make(10, win=True, conf=70.0)
        results = confidence_threshold_sweep(rows, thresholds=(50.0, 60.0, 70.0))
        assert len(results) == 3

    def test_threshold_field_present(self):
        rows = _make(10, win=True, conf=70.0)
        results = confidence_threshold_sweep(rows, thresholds=(60.0, 70.0))
        thresholds = [r["threshold"] for r in results]
        assert thresholds == [60.0, 70.0]

    def test_n_decreases_or_stays_as_threshold_rises(self):
        rows = [_win(conf=c) for c in [50.0, 55.0, 60.0, 65.0, 70.0, 75.0, 80.0]]
        results = confidence_threshold_sweep(rows, thresholds=(50.0, 60.0, 70.0, 80.0))
        ns = [r["n"] for r in results]
        # n must be non-increasing
        assert all(ns[i] >= ns[i + 1] for i in range(len(ns) - 1))

    def test_empty_rows(self):
        results = confidence_threshold_sweep([], thresholds=(50.0, 60.0))
        assert len(results) == 2
        assert all(r["n"] == 0 for r in results)

    def test_default_sweep_uses_confidence_sweep_constant(self):
        rows = _make(5, conf=70.0)
        results = confidence_threshold_sweep(rows)
        assert len(results) == len(CONFIDENCE_SWEEP)

    def test_name_field_present(self):
        rows = _make(5)
        results = confidence_threshold_sweep(rows, thresholds=(60.0,))
        assert "name" in results[0]

    def test_threshold_above_all_confs_gives_zero_n(self):
        rows = [_win(conf=50.0) for _ in range(5)]
        results = confidence_threshold_sweep(rows, thresholds=(99.0,))
        assert results[0]["n"] == 0

    def test_threshold_at_zero_includes_all(self):
        rows = _make(7, conf=60.0)
        results = confidence_threshold_sweep(rows, thresholds=(0.0,))
        assert results[0]["n"] == 7


# ── TestPenaltySweep ──────────────────────────────────────────────────────────

class TestPenaltySweep:
    def _mixed_rows(self):
        return (
            _make(5, regime="BULL",     conf=60.0, win=True)
            + _make(5, regime="NEUTRAL", conf=60.0, win=True)
            + _make(5, regime="RISK_OFF", conf=60.0, win=True)
        )

    def test_length_is_neutral_plus_risk_off(self):
        rows = self._mixed_rows()
        results = penalty_sweep(
            rows,
            neutral_factors=(0.80, 0.90),
            risk_off_factors=(0.70, 0.75),
        )
        assert len(results) == 4  # 2 neutral + 2 risk_off

    def test_neutral_factor_field_present(self):
        rows = self._mixed_rows()
        results = penalty_sweep(rows, neutral_factors=(0.80,), risk_off_factors=())
        assert results[0]["neutral_factor"] == 0.80

    def test_risk_off_factor_field_present(self):
        rows = self._mixed_rows()
        results = penalty_sweep(rows, neutral_factors=(), risk_off_factors=(0.75,))
        assert results[0]["risk_off_factor"] == 0.75

    def test_tighter_neutral_excludes_below_threshold(self):
        # conf=60.0, NEUTRAL, new_factor=0.80 → 60/0.90*0.80 ≈ 53.3 < 55.0 threshold
        rows = _make(10, regime="NEUTRAL", conf=60.0, win=True)
        results = penalty_sweep(rows, neutral_factors=(0.80,), risk_off_factors=())
        # these rows have conf=60 → replayed ≈53.3 < BASELINE_CONVICTION_THRESHOLD=55.0
        # so all excluded from min_confidence filter
        assert results[0]["n"] < 10

    def test_default_factors_uses_constants(self):
        rows = self._mixed_rows()
        results = penalty_sweep(rows)
        expected_len = len(NEUTRAL_PENALTY_SWEEP) + len(RISK_OFF_PENALTY_SWEEP)
        assert len(results) == expected_len

    def test_empty_rows_no_error(self):
        results = penalty_sweep([], neutral_factors=(0.80,), risk_off_factors=(0.75,))
        assert len(results) == 2
        assert all(r["n"] == 0 for r in results)


# ── TestWeightPerturbationAnalysis ────────────────────────────────────────────

class TestWeightPerturbationAnalysis:
    def test_returns_one_per_delta(self):
        rows = _make(5, signals={"options": 2})
        results = weight_perturbation_analysis(rows, "options", deltas=(-5.0, 0.0, 5.0))
        assert len(results) == 3

    def test_delta_and_signal_fields(self):
        rows = _make(5, signals={"options": 2})
        results = weight_perturbation_analysis(rows, "options", deltas=(5.0,))
        assert results[0]["signal"] == "options"
        assert results[0]["delta"] == 5.0

    def test_zero_delta_same_as_baseline(self):
        rows = _make(10, conf=70.0, signals={"options": 2})
        base_stats = replay_stats(rows, BASELINE_SPEC)
        results = weight_perturbation_analysis(rows, "options", deltas=(0.0,))
        zero_stats = results[0]
        assert zero_stats["n"] == base_stats["n"]

    def test_positive_delta_increases_confidence(self):
        rows = [_win(conf=60.0, signals={"options": 2}) for _ in range(5)]
        results = weight_perturbation_analysis(rows, "options", deltas=(10.0,))
        result = apply_replay(rows, ReplaySpec(signal_confidence_deltas={"options": 10.0}))
        for r in result:
            assert r["confidence_pct"] == 70.0

    def test_inactive_signal_no_change_on_confidence(self):
        rows = [_win(conf=60.0, signals={}) for _ in range(5)]
        results = weight_perturbation_analysis(rows, "options", deltas=(10.0,))
        # options not active → confidence unchanged → all rows still included
        assert results[0]["n"] == 5

    def test_default_deltas_uses_constant(self):
        rows = _make(5)
        results = weight_perturbation_analysis(rows, "options")
        assert len(results) == len(SIGNAL_WEIGHT_DELTAS)

    def test_empty_rows(self):
        results = weight_perturbation_analysis([], "options", deltas=(0.0,))
        assert results[0]["n"] == 0


# ── TestRobustnessAnalysis ────────────────────────────────────────────────────

class TestRobustnessAnalysis:
    def _bstats(self, n=50, wr=60.0):
        return {"n": n, "win_rate": wr, "alert_reduction_pct": 0.0}

    def test_no_warnings_for_solid_results(self):
        baseline = self._bstats(n=50, wr=60.0)
        solid = [{"n": 20, "win_rate": 62.0, "alert_reduction_pct": 20.0, "name": "ok"}]
        warnings = robustness_analysis(baseline, solid)
        assert warnings == []

    def test_insufficient_sample_high_severity(self):
        baseline = self._bstats()
        results = [{"n": 5, "win_rate": 80.0, "alert_reduction_pct": 0.0, "name": "tiny"}]
        warnings = robustness_analysis(baseline, results)
        types = [w["type"] for w in warnings]
        assert "INSUFFICIENT_SAMPLE" in types
        w = next(w for w in warnings if w["type"] == "INSUFFICIENT_SAMPLE")
        assert w["severity"] == "HIGH"

    def test_insufficient_sample_threshold(self):
        baseline = self._bstats()
        below = [{"n": MIN_RELIABLE_SAMPLE - 1, "win_rate": 50.0, "alert_reduction_pct": 0.0, "name": "x"}]
        at    = [{"n": MIN_RELIABLE_SAMPLE,     "win_rate": 50.0, "alert_reduction_pct": 0.0, "name": "y"}]
        assert any(w["type"] == "INSUFFICIENT_SAMPLE" for w in robustness_analysis(baseline, below))
        assert not any(w["type"] == "INSUFFICIENT_SAMPLE" for w in robustness_analysis(baseline, at))

    def test_fragile_improvement_detected(self):
        # baseline n=50, wr=60.0; result n=10 (<50*0.30=15), wr=70.0 (>60+5)
        baseline = self._bstats(n=50, wr=60.0)
        results = [{"n": 10, "win_rate": 70.0, "alert_reduction_pct": 80.0, "name": "fragile"}]
        warnings = robustness_analysis(baseline, results)
        types = [w["type"] for w in warnings]
        assert "FRAGILE_IMPROVEMENT" in types
        w = next(w for w in warnings if w["type"] == "FRAGILE_IMPROVEMENT")
        assert w["severity"] == "HIGH"

    def test_fragile_improvement_threshold(self):
        # win improvement exactly at threshold (5pp) should NOT trigger
        baseline = self._bstats(n=50, wr=60.0)
        exact = [{"n": 5, "win_rate": 65.0, "alert_reduction_pct": 90.0, "name": "edge"}]
        warnings = robustness_analysis(baseline, exact)
        assert not any(w["type"] == "FRAGILE_IMPROVEMENT" for w in warnings)

    def test_overfit_alert_reduction_medium(self):
        baseline = self._bstats(n=50)
        results = [{"n": 20, "win_rate": 62.0, "alert_reduction_pct": 60.0, "name": "overfit"}]
        warnings = robustness_analysis(baseline, results)
        types = [w["type"] for w in warnings]
        assert "OVERFIT_ALERT_REDUCTION" in types
        w = next(w for w in warnings if w["type"] == "OVERFIT_ALERT_REDUCTION")
        assert w["severity"] == "MEDIUM"

    def test_overfit_threshold(self):
        baseline = self._bstats()
        at    = [{"n": 20, "win_rate": 60.0, "alert_reduction_pct": 50.1, "name": "a"}]
        below = [{"n": 20, "win_rate": 60.0, "alert_reduction_pct": 50.0, "name": "b"}]
        assert any(w["type"] == "OVERFIT_ALERT_REDUCTION" for w in robustness_analysis(baseline, at))
        assert not any(w["type"] == "OVERFIT_ALERT_REDUCTION" for w in robustness_analysis(baseline, below))

    def test_high_severity_sorted_first(self):
        baseline = self._bstats()
        results = [
            {"n": 5, "win_rate": 80.0, "alert_reduction_pct": 55.0, "name": "a"},
        ]
        warnings = robustness_analysis(baseline, results)
        high_first = [w for w in warnings if w["severity"] == "HIGH"]
        medium = [w for w in warnings if w["severity"] == "MEDIUM"]
        if high_first and medium:
            assert warnings.index(high_first[0]) < warnings.index(medium[0])

    def test_empty_results_no_warnings(self):
        assert robustness_analysis(self._bstats(), []) == []

    def test_multiple_configs_multiple_warnings(self):
        baseline = self._bstats(n=50, wr=60.0)
        results = [
            {"n": 2,  "win_rate": 80.0, "alert_reduction_pct": 96.0, "name": "tiny"},
            {"n": 20, "win_rate": 61.0, "alert_reduction_pct": 60.0, "name": "overfit"},
        ]
        warnings = robustness_analysis(baseline, results)
        types = {w["type"] for w in warnings}
        assert "INSUFFICIENT_SAMPLE" in types
        assert "OVERFIT_ALERT_REDUCTION" in types


# ── TestGenerateRecommendation ────────────────────────────────────────────────

class TestGenerateRecommendation:
    def _bstats(self, n=20, wr=60.0):
        return {"n": n, "win_rate": wr}

    def _astats(self, n=15, wr=65.0):
        return {"n": n, "win_rate": wr, "alert_reduction_pct": 25.0}

    def test_returns_string(self):
        rec = generate_recommendation(self._bstats(), self._astats(), STRICT_CONFIDENCE_65_SPEC)
        assert isinstance(rec, str)
        assert len(rec) > 0

    def test_contains_spec_name(self):
        rec = generate_recommendation(self._bstats(), self._astats(), STRICT_CONFIDENCE_65_SPEC)
        assert STRICT_CONFIDENCE_65_SPEC.name in rec

    def test_improvement_direction_improved(self):
        rec = generate_recommendation(
            self._bstats(wr=60.0),
            self._astats(wr=65.0),
            STRICT_CONFIDENCE_65_SPEC,
        )
        assert "improved" in rec

    def test_worsened_direction(self):
        rec = generate_recommendation(
            self._bstats(wr=70.0),
            self._astats(wr=60.0),
            STRICT_CONFIDENCE_65_SPEC,
        )
        assert "worsened" in rec

    def test_insufficient_data_message(self):
        rec = generate_recommendation(
            {"n": 3, "win_rate": None},
            {"n": 2, "win_rate": None},
            STRICT_CONFIDENCE_65_SPEC,
        )
        assert "insufficient" in rec.lower()

    def test_determinism(self):
        b = self._bstats()
        a = self._astats()
        r1 = generate_recommendation(b, a, STRICT_CONFIDENCE_65_SPEC)
        r2 = generate_recommendation(b, a, STRICT_CONFIDENCE_65_SPEC)
        assert r1 == r2


# ── TestSensitivitySummary ────────────────────────────────────────────────────

class TestSensitivitySummary:
    def test_empty_input(self):
        result = sensitivity_summary([])
        assert result["n_configs"] == 0
        assert result["min_win_rate"] is None
        assert result["max_win_rate"] is None

    def test_no_valid_win_rates(self):
        results = [{"n": 5, "win_rate": None, "name": "x"}]
        result = sensitivity_summary(results)
        assert result["best_config"] is None

    def test_n_configs_correct(self):
        results = [
            {"win_rate": 60.0, "n": 10},
            {"win_rate": 65.0, "n": 8},
            {"win_rate": 70.0, "n": 5},
        ]
        r = sensitivity_summary(results)
        assert r["n_configs"] == 3

    def test_min_max_win_rate(self):
        results = [
            {"win_rate": 60.0, "n": 10},
            {"win_rate": 70.0, "n": 8},
            {"win_rate": 50.0, "n": 5},
        ]
        r = sensitivity_summary(results)
        assert r["min_win_rate"] == 50.0
        assert r["max_win_rate"] == 70.0

    def test_win_rate_range(self):
        results = [
            {"win_rate": 60.0, "n": 10},
            {"win_rate": 80.0, "n": 5},
        ]
        r = sensitivity_summary(results)
        assert r["win_rate_range"] == pytest.approx(20.0)

    def test_best_config_is_max_win_rate(self):
        results = [
            {"win_rate": 60.0, "n": 10, "name": "low"},
            {"win_rate": 80.0, "n": 5,  "name": "high"},
        ]
        r = sensitivity_summary(results)
        assert r["best_config"]["name"] == "high"

    def test_worst_config_is_min_win_rate(self):
        results = [
            {"win_rate": 60.0, "n": 10, "name": "mid"},
            {"win_rate": 40.0, "n": 5,  "name": "low"},
        ]
        r = sensitivity_summary(results)
        assert r["worst_config"]["name"] == "low"

    def test_monotone_trend_increasing(self):
        results = [{"win_rate": 50.0}, {"win_rate": 60.0}, {"win_rate": 70.0}]
        r = sensitivity_summary(results)
        assert r["monotone_trend"] is True

    def test_monotone_trend_decreasing(self):
        results = [{"win_rate": 70.0}, {"win_rate": 60.0}, {"win_rate": 50.0}]
        r = sensitivity_summary(results)
        assert r["monotone_trend"] is True

    def test_non_monotone_trend(self):
        results = [{"win_rate": 50.0}, {"win_rate": 70.0}, {"win_rate": 60.0}]
        r = sensitivity_summary(results)
        assert r["monotone_trend"] is False

    def test_single_entry_monotone_none(self):
        results = [{"win_rate": 60.0}]
        r = sensitivity_summary(results)
        assert r["monotone_trend"] is None


# ── TestGenerateReport ────────────────────────────────────────────────────────

class TestGenerateReport:
    def _rows(self, n=20):
        return (
            _make(n // 2, win=True, regime="BULL", conf=65.0)
            + _make(n // 2, win=False, regime="NEUTRAL", conf=55.0)
        )

    def test_report_keys(self):
        rows = self._rows(20)
        report = generate_report(rows)
        expected = {
            "row_count", "baseline", "counterfactuals", "confidence_sweep",
            "sensitivity", "best_config", "worst_config",
            "robustness_warnings", "recommendations", "warnings",
        }
        assert expected.issubset(report.keys())

    def test_row_count_correct(self):
        rows = self._rows(20)
        report = generate_report(rows)
        assert report["row_count"] == 20

    def test_counterfactuals_keys(self):
        rows = self._rows(20)
        report = generate_report(rows)
        expected_keys = {
            "strict_65", "strict_70", "no_risk_off",
            "conviction_only", "tighter_neutral", "looser_risk_off",
        }
        assert expected_keys == set(report["counterfactuals"].keys())

    def test_baseline_n_equals_row_count(self):
        rows = self._rows(20)
        report = generate_report(rows)
        assert report["baseline"]["n"] == 20

    def test_confidence_sweep_length(self):
        rows = self._rows(20)
        report = generate_report(rows)
        assert len(report["confidence_sweep"]) == len(CONFIDENCE_SWEEP)

    def test_recommendations_is_list_of_strings(self):
        rows = self._rows(20)
        report = generate_report(rows)
        assert isinstance(report["recommendations"], list)
        for rec in report["recommendations"]:
            assert isinstance(rec, str)

    def test_determinism(self):
        rows = self._rows(20)
        r1 = generate_report(rows)
        r2 = generate_report(rows)
        assert r1["row_count"] == r2["row_count"]
        assert r1["baseline"]["n"] == r2["baseline"]["n"]

    def test_empty_rows(self):
        report = generate_report([])
        assert report["row_count"] == 0
        assert report["baseline"]["n"] == 0

    def test_warnings_is_list(self):
        rows = self._rows(20)
        report = generate_report(rows)
        assert isinstance(report["warnings"], list)

    def test_sensitivity_contains_n_configs(self):
        rows = self._rows(20)
        report = generate_report(rows)
        assert "n_configs" in report["sensitivity"]


# ── TestSparseHandling ────────────────────────────────────────────────────────

class TestSparseHandling:
    def test_single_row_no_errors(self):
        rows = [_win()]
        report = generate_report(rows)
        assert report["row_count"] == 1
        assert report["baseline"]["win_rate"] is None  # n < MIN_ROWS_FOR_STATS

    def test_all_none_returns(self):
        rows = [_row() for _ in range(5)]  # no returns set
        stats = _replay_stats(rows, rows)
        assert stats["avg_return_5d"] is None
        assert stats["risk_adj_score"] is None

    def test_missing_signal_summary(self):
        row = {"regime": "BULL", "confidence_pct": 60.0}
        assert _sig_scores(row) == {}

    def test_missing_confidence_pct_treated_as_zero(self):
        row = {"regime": "BULL"}
        result = _replay_confidence(row, BASELINE_SPEC)
        assert result == 0.0

    def test_missing_regime_not_suppressed(self):
        # _row_passes uses `or ""` so a missing regime becomes "" not "BULL"
        row = {"confidence_pct": 70.0}
        spec = ReplaySpec(suppress_regimes=("BULL",))
        # regime = str(row.get("regime") or "") = "" → NOT in ("BULL",) → passes
        result = _row_passes(row, spec, 70.0)
        assert result is True

    def test_compare_replay_empty_rows(self):
        result = compare_replay([], BASELINE_SPEC, NO_RISK_OFF_SPEC)
        assert result["baseline"]["n"] == 0
        assert result["alternative"]["n"] == 0

    def test_penalty_sweep_with_no_factors(self):
        rows = _make(5)
        results = penalty_sweep(rows, neutral_factors=(), risk_off_factors=())
        assert results == []
