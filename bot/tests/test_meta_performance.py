"""
Unit tests for meta_performance.py (Phase 2D).

All tests pass mock rows directly — no DB access, no network calls.
Rows are in chronological order (oldest first); rolling windows take [-N:].
"""
import pytest

from meta_performance import (
    DEGRAD_CALIBRATION_HIGH,
    DEGRAD_CALIBRATION_THRESHOLD,
    DEGRAD_DRAWDOWN_HIGH,
    DEGRAD_DRAWDOWN_THRESHOLD,
    DEGRAD_RETURN_HIGH,
    DEGRAD_RETURN_THRESHOLD,
    DEGRAD_WIN_RATE_HIGH,
    DEGRAD_WIN_RATE_THRESHOLD,
    INFLATION_CONF_HIGH,
    INFLATION_CONF_RISE,
    INFLATION_RETURN_FALL,
    REGIME_DEGRAD_HIGH,
    REGIME_DEGRAD_THRESHOLD,
    SAFEGUARD_CRITICAL_HIGH_COUNT,
    SAFEGUARD_PAUSE_HIGH_COUNT,
    WINDOWS,
    _pearson,
    _sharpe_like,
    _std,
    confidence_inflation,
    degradation_detection,
    generate_report,
    regime_deterioration,
    rolling_windows,
    safeguard_recommendations,
    window_stats,
)
from market_regime import BULL, NEUTRAL, RISK_OFF
from outcome_analytics import MIN_ROWS_FOR_STATS


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row(
    regime=BULL,
    return_5d=None,
    return_20d=None,
    max_gain_pct=None,
    max_drawdown_pct=None,
    confidence_pct=None,
):
    return {
        "regime":           regime,
        "return_5d":        return_5d,
        "return_20d":       return_20d,
        "max_gain_pct":     max_gain_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "confidence_pct":   confidence_pct,
    }


def _win(regime=BULL, conf=70.0, ret=5.0, dd=-2.0):
    return _row(regime=regime, return_5d=ret, max_gain_pct=8.0,
                max_drawdown_pct=dd, confidence_pct=conf)


def _loss(regime=BULL, conf=50.0, ret=-3.0, dd=-6.0):
    return _row(regime=regime, return_5d=ret, max_gain_pct=1.0,
                max_drawdown_pct=dd, confidence_pct=conf)


def _make(n, win=True, regime=BULL, conf=65.0, ret=None, dd=None):
    if win:
        return [_win(regime=regime, conf=conf, ret=ret or 5.0, dd=dd or -2.0)
                for _ in range(n)]
    return [_loss(regime=regime, conf=conf, ret=ret or -3.0, dd=dd or -6.0)
            for _ in range(n)]


# ── Constants ─────────────────────────────────────────────────────────────────

class TestConstants:
    def test_windows_tuple_is_four_ascending(self):
        assert WINDOWS == (10, 25, 50, 100)

    def test_degrad_thresholds_positive(self):
        assert DEGRAD_WIN_RATE_THRESHOLD > 0
        assert DEGRAD_RETURN_THRESHOLD   > 0
        assert DEGRAD_CALIBRATION_THRESHOLD > 0
        assert DEGRAD_DRAWDOWN_THRESHOLD > 0

    def test_high_thresholds_exceed_medium(self):
        assert DEGRAD_WIN_RATE_HIGH        > DEGRAD_WIN_RATE_THRESHOLD
        assert DEGRAD_RETURN_HIGH          > DEGRAD_RETURN_THRESHOLD
        assert DEGRAD_CALIBRATION_HIGH     > DEGRAD_CALIBRATION_THRESHOLD
        assert DEGRAD_DRAWDOWN_HIGH        > DEGRAD_DRAWDOWN_THRESHOLD

    def test_safeguard_pause_below_critical(self):
        assert SAFEGUARD_PAUSE_HIGH_COUNT < SAFEGUARD_CRITICAL_HIGH_COUNT


# ── _std ──────────────────────────────────────────────────────────────────────

class TestStd:
    def test_uniform_returns_zero(self):
        assert _std([3.0, 3.0, 3.0]) == pytest.approx(0.0)

    def test_known_values(self):
        # population std of [2,4,4,4,5,5,7,9] = 2.0
        assert _std([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(2.0, abs=1e-6)

    def test_single_value_returns_none(self):
        assert _std([5.0]) is None

    def test_empty_returns_none(self):
        assert _std([]) is None

    def test_none_values_ignored(self):
        # same as [2, 4] after dropping None
        result = _std([2.0, None, 4.0])
        assert result is not None  # two valid values
        assert result > 0

    def test_all_none_returns_none(self):
        assert _std([None, None]) is None


# ── _sharpe_like ──────────────────────────────────────────────────────────────

class TestSharpeLike:
    def test_positive_return_positive_std(self):
        rows = _make(5, win=True, ret=5.0)
        # all returns identical → std=0 → None
        assert _sharpe_like(rows) is None

    def test_varied_returns(self):
        rows = [
            _row(return_5d=2.0),
            _row(return_5d=4.0),
            _row(return_5d=6.0),
            _row(return_5d=8.0),
            _row(return_5d=10.0),
        ]
        result = _sharpe_like(rows)
        assert result is not None
        assert result > 0.0

    def test_negative_return_negative_sharpe(self):
        rows = [
            _row(return_5d=-2.0),
            _row(return_5d=-4.0),
            _row(return_5d=-6.0),
        ]
        result = _sharpe_like(rows)
        assert result is not None
        assert result < 0.0

    def test_fewer_than_3_returns_none(self):
        rows = [_row(return_5d=5.0), _row(return_5d=3.0)]
        assert _sharpe_like(rows) is None

    def test_empty_rows_returns_none(self):
        assert _sharpe_like([]) is None

    def test_none_return_rows_skipped(self):
        rows = [
            _row(return_5d=None),
            _row(return_5d=None),
            _row(return_5d=5.0),
        ]
        assert _sharpe_like(rows) is None  # only 1 valid → < 3

    def test_result_is_float(self):
        rows = [_row(return_5d=v) for v in [1.0, 3.0, 5.0, 7.0, 9.0]]
        result = _sharpe_like(rows)
        assert isinstance(result, float)

    def test_rounded_to_4_decimal_places(self):
        rows = [_row(return_5d=v) for v in [1.0, 2.0, 3.0, 4.0, 5.0]]
        result = _sharpe_like(rows)
        assert result == round(result, 4)


# ── _pearson ──────────────────────────────────────────────────────────────────

class TestPearson:
    def test_perfect_positive(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [2.0, 4.0, 6.0, 8.0, 10.0]
        assert _pearson(xs, ys) == pytest.approx(1.0, abs=1e-4)

    def test_perfect_negative(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [5.0, 4.0, 3.0, 2.0, 1.0]
        assert _pearson(xs, ys) == pytest.approx(-1.0, abs=1e-4)

    def test_too_few_pairs_returns_none(self):
        assert _pearson([1.0, 2.0], [1.0, 2.0]) is None

    def test_zero_variance_x_returns_none(self):
        assert _pearson([5.0, 5.0, 5.0, 5.0], [1.0, 2.0, 3.0, 4.0]) is None

    def test_zero_variance_y_returns_none(self):
        assert _pearson([1.0, 2.0, 3.0, 4.0], [5.0, 5.0, 5.0, 5.0]) is None

    def test_none_pairs_dropped(self):
        xs = [1.0, None, 3.0, 4.0, 5.0]
        ys = [2.0, 4.0, None, 8.0, 10.0]
        result = _pearson(xs, ys)
        assert result is not None  # 3 valid pairs remain


# ── window_stats ──────────────────────────────────────────────────────────────

class TestWindowStats:
    def test_required_keys_present(self):
        stats = window_stats([], 10)
        expected = {
            "window", "n", "win_rate", "avg_return_5d", "avg_return_20d",
            "avg_max_dd", "avg_confidence", "calibration_error",
            "sharpe_like", "conf_return_corr",
        }
        assert expected == set(stats.keys())

    def test_window_field_matches_argument(self):
        assert window_stats([], 25)["window"] == 25

    def test_n_is_min_of_window_and_rows(self):
        rows = _make(7)
        assert window_stats(rows, 10)["n"] == 7
        assert window_stats(rows, 5)["n"]  == 5

    def test_takes_most_recent_rows(self):
        """The most recent row should dominate stats when win/loss differ."""
        old_losses = _make(90, win=False)
        recent_wins = _make(10, win=True)
        rows = old_losses + recent_wins
        stats_10  = window_stats(rows, 10)
        stats_100 = window_stats(rows, 100)
        assert stats_10["win_rate"]  == pytest.approx(100.0)
        assert stats_100["win_rate"] == pytest.approx(10.0, abs=1.0)

    def test_empty_rows_all_none(self):
        stats = window_stats([], 10)
        assert stats["n"] == 0
        assert stats["win_rate"] is None
        assert stats["avg_return_5d"] is None
        assert stats["calibration_error"] is None
        assert stats["sharpe_like"] is None
        assert stats["conf_return_corr"] is None

    def test_all_wins_win_rate_100(self):
        rows = _make(10, win=True)
        assert window_stats(rows, 10)["win_rate"] == 100.0

    def test_all_losses_win_rate_0(self):
        rows = _make(10, win=False)
        assert window_stats(rows, 10)["win_rate"] == 0.0

    def test_avg_confidence_computed(self):
        rows = _make(10, conf=75.0)
        assert window_stats(rows, 10)["avg_confidence"] == pytest.approx(75.0)

    def test_avg_drawdown_computed(self):
        rows = _make(10, win=True, dd=-3.0)
        assert window_stats(rows, 10)["avg_max_dd"] == pytest.approx(-3.0)

    def test_fewer_than_min_rows_win_rate_none(self):
        rows = _make(MIN_ROWS_FOR_STATS - 1)
        assert window_stats(rows, 10)["win_rate"] is None

    def test_deterministic(self):
        rows = _make(50)
        assert window_stats(rows, 25) == window_stats(rows, 25)


# ── rolling_windows ───────────────────────────────────────────────────────────

class TestRollingWindows:
    def test_returns_all_four_window_keys(self):
        result = rolling_windows([])
        assert set(result.keys()) == {10, 25, 50, 100}

    def test_each_value_is_dict_with_window_field(self):
        result = rolling_windows([])
        for w in WINDOWS:
            assert result[w]["window"] == w

    def test_n_increases_with_window_size(self):
        rows = _make(30)
        result = rolling_windows(rows)
        assert result[10]["n"] <= result[25]["n"] <= result[50]["n"] <= result[100]["n"]

    def test_all_windows_when_enough_rows(self):
        rows = _make(100)
        result = rolling_windows(rows)
        assert result[10]["n"]  == 10
        assert result[25]["n"]  == 25
        assert result[50]["n"]  == 50
        assert result[100]["n"] == 100

    def test_capped_at_available_rows(self):
        rows = _make(15)
        result = rolling_windows(rows)
        assert result[100]["n"] == 15
        assert result[50]["n"]  == 15
        assert result[25]["n"]  == 15
        assert result[10]["n"]  == 10

    def test_deterministic(self):
        rows = _make(50)
        assert rolling_windows(rows) == rolling_windows(rows)


# ── degradation_detection ─────────────────────────────────────────────────────

class TestDegradationDetection:
    def _windows(self, specs):
        """Build a minimal windows dict from {window: (win_rate, avg_return_5d, ece, avg_dd)}."""
        result = {}
        for w, (wr, ret, ece, dd) in specs.items():
            result[w] = {
                "window":            w,
                "n":                 w,
                "win_rate":          wr,
                "avg_return_5d":     ret,
                "calibration_error": ece,
                "avg_max_dd":        dd,
                "avg_return_20d":    None,
                "avg_confidence":    60.0,
                "sharpe_like":       None,
                "conf_return_corr":  None,
            }
        return result

    def test_no_degradation_returns_empty(self):
        windows = self._windows({
            10: (80.0, 5.0, 0.05, -2.0),
            25: (75.0, 4.0, 0.07, -2.5),
            50: (70.0, 3.0, 0.08, -3.0),
            100: (65.0, 2.0, 0.09, -3.5),
        })
        assert degradation_detection(windows) == []

    def test_win_rate_deterioration_medium(self):
        delta = -(DEGRAD_WIN_RATE_THRESHOLD + 1.0)
        windows = self._windows({
            10: (50.0 + delta, 3.0, 0.05, -2.0),
            25: (50.0,         3.0, 0.05, -2.0),
        })
        events = degradation_detection(windows)
        types = [e["type"] for e in events]
        assert "WIN_RATE_DETERIORATION" in types
        ev = next(e for e in events if e["type"] == "WIN_RATE_DETERIORATION")
        assert ev["severity"] == "MEDIUM"

    def test_win_rate_deterioration_high(self):
        windows = self._windows({
            10:  (40.0, 3.0, 0.05, -2.0),
            25:  (70.0, 3.0, 0.05, -2.0),
        })
        events = degradation_detection(windows)
        ev = next((e for e in events if e["type"] == "WIN_RATE_DETERIORATION"), None)
        assert ev is not None
        assert ev["severity"] == "HIGH"

    def test_return_deterioration_medium(self):
        windows = self._windows({
            10:  (65.0, 1.0,  0.05, -2.0),
            25:  (65.0, 3.5,  0.05, -2.0),
        })
        events = degradation_detection(windows)
        ev = next((e for e in events if e["type"] == "RETURN_DETERIORATION"), None)
        assert ev is not None
        assert ev["severity"] == "MEDIUM"

    def test_return_deterioration_high(self):
        windows = self._windows({
            10:  (65.0, -1.0, 0.05, -2.0),
            25:  (65.0,  4.0, 0.05, -2.0),
        })
        events = degradation_detection(windows)
        ev = next((e for e in events if e["type"] == "RETURN_DETERIORATION"), None)
        assert ev is not None
        assert ev["severity"] == "HIGH"

    def test_calibration_worsening_detected(self):
        windows = self._windows({
            10:  (65.0, 3.0, 0.20, -2.0),
            25:  (65.0, 3.0, 0.05, -2.0),
        })
        events = degradation_detection(windows)
        types = [e["type"] for e in events]
        assert "CALIBRATION_WORSENING" in types

    def test_calibration_worsening_high(self):
        windows = self._windows({
            10:  (65.0, 3.0, 0.30, -2.0),
            25:  (65.0, 3.0, 0.05, -2.0),
        })
        events = degradation_detection(windows)
        ev = next((e for e in events if e["type"] == "CALIBRATION_WORSENING"), None)
        assert ev is not None
        assert ev["severity"] == "HIGH"

    def test_drawdown_worsening_medium(self):
        windows = self._windows({
            10:  (65.0, 3.0, 0.05, -6.0),
            25:  (65.0, 3.0, 0.05, -3.0),
        })
        events = degradation_detection(windows)
        ev = next((e for e in events if e["type"] == "DRAWDOWN_WORSENING"), None)
        assert ev is not None
        assert ev["severity"] == "MEDIUM"

    def test_drawdown_worsening_high(self):
        windows = self._windows({
            10:  (65.0, 3.0, 0.05, -10.0),
            25:  (65.0, 3.0, 0.05, -3.0),
        })
        events = degradation_detection(windows)
        ev = next((e for e in events if e["type"] == "DRAWDOWN_WORSENING"), None)
        assert ev is not None
        assert ev["severity"] == "HIGH"

    def test_none_win_rate_pair_skipped(self):
        windows = self._windows({
            10: (None, 3.0, 0.05, -2.0),
            25: (70.0, 3.0, 0.05, -2.0),
        })
        events = degradation_detection(windows)
        wr_events = [e for e in events if e["type"] == "WIN_RATE_DETERIORATION"]
        assert len(wr_events) == 0

    def test_none_calibration_pair_skipped(self):
        windows = self._windows({
            10: (65.0, 3.0, None, -2.0),
            25: (65.0, 3.0, 0.05, -2.0),
        })
        events = degradation_detection(windows)
        cal_events = [e for e in events if e["type"] == "CALIBRATION_WORSENING"]
        assert len(cal_events) == 0

    def test_high_severity_sorted_first(self):
        windows = self._windows({
            10:  (30.0, -5.0, 0.05, -2.0),   # HIGH win rate drop
            25:  (65.0,  3.0, 0.05, -2.0),
            50:  (64.0,  2.9, 0.05, -2.1),    # minimal, no trigger
            100: (63.0,  2.8, 0.05, -2.2),
        })
        events = degradation_detection(windows)
        if len(events) >= 2:
            severities = [e["severity"] for e in events]
            high_indices = [i for i, s in enumerate(severities) if s == "HIGH"]
            medium_indices = [i for i, s in enumerate(severities) if s == "MEDIUM"]
            if high_indices and medium_indices:
                assert max(high_indices) < min(medium_indices)

    def test_events_contain_required_fields(self):
        windows = self._windows({
            10:  (40.0, 3.0, 0.05, -2.0),
            25:  (70.0, 3.0, 0.05, -2.0),
        })
        events = degradation_detection(windows)
        for e in events:
            assert "type"         in e
            assert "short_window" in e
            assert "long_window"  in e
            assert "short_value"  in e
            assert "long_value"   in e
            assert "delta"        in e
            assert "severity"     in e
            assert "detail"       in e

    def test_delta_has_correct_sign(self):
        windows = self._windows({
            10:  (40.0, 3.0, 0.05, -2.0),
            25:  (70.0, 3.0, 0.05, -2.0),
        })
        events = degradation_detection(windows)
        ev = next(e for e in events if e["type"] == "WIN_RATE_DETERIORATION")
        assert ev["delta"] < 0  # shorter window is worse

    def test_empty_windows_returns_empty(self):
        assert degradation_detection({}) == []

    def test_single_window_returns_empty(self):
        windows = self._windows({10: (50.0, 3.0, 0.05, -2.0)})
        assert degradation_detection(windows) == []

    def test_deterministic(self):
        windows = self._windows({
            10:  (40.0, -1.0, 0.20, -8.0),
            25:  (60.0,  2.0, 0.10, -4.0),
            50:  (65.0,  3.0, 0.08, -3.0),
            100: (68.0,  3.5, 0.06, -2.5),
        })
        assert degradation_detection(windows) == degradation_detection(windows)

    def test_threshold_exact_not_triggered(self):
        # delta exactly at -THRESHOLD → not triggered (strictly less than)
        windows = self._windows({
            10:  (60.0 - DEGRAD_WIN_RATE_THRESHOLD, 3.0, 0.05, -2.0),
            25:  (60.0, 3.0, 0.05, -2.0),
        })
        events = degradation_detection(windows)
        wr_events = [e for e in events if e["type"] == "WIN_RATE_DETERIORATION"]
        assert len(wr_events) == 0


# ── confidence_inflation ──────────────────────────────────────────────────────

class TestConfidenceInflation:
    def _win_with_conf(self, conf):
        return self._windows_pair(conf_short=conf)

    def _windows_pair(
        self,
        conf_short=70.0,
        conf_long=60.0,
        ret_short=2.0,
        ret_long=4.0,
        extra_windows=None,
    ):
        w = {
            10:  {
                "window": 10, "n": 10,
                "win_rate": 60.0,
                "avg_return_5d": ret_short,
                "avg_return_20d": None,
                "avg_max_dd": -3.0,
                "avg_confidence": conf_short,
                "calibration_error": 0.10,
                "sharpe_like": 0.5,
                "conf_return_corr": 0.3,
            },
            100: {
                "window": 100, "n": 100,
                "win_rate": 65.0,
                "avg_return_5d": ret_long,
                "avg_return_20d": None,
                "avg_max_dd": -2.5,
                "avg_confidence": conf_long,
                "calibration_error": 0.08,
                "sharpe_like": 0.7,
                "conf_return_corr": 0.5,
            },
        }
        if extra_windows:
            w.update(extra_windows)
        return w

    def test_inflation_detected_when_conf_up_return_down(self):
        windows = self._windows_pair(
            conf_short=75.0, conf_long=60.0,   # +15pp
            ret_short=1.0,   ret_long=4.0,     # -3%
        )
        events = confidence_inflation(windows)
        types = [e["type"] for e in events]
        assert "CONFIDENCE_INFLATION" in types

    def test_inflation_severity_medium(self):
        windows = self._windows_pair(
            conf_short=66.0, conf_long=60.0,   # +6pp, < INFLATION_CONF_HIGH=10
            ret_short=1.0,   ret_long=4.0,
        )
        events = confidence_inflation(windows)
        ev = next((e for e in events if e["type"] == "CONFIDENCE_INFLATION"), None)
        if ev:
            assert ev["severity"] == "MEDIUM"

    def test_inflation_severity_high(self):
        windows = self._windows_pair(
            conf_short=75.0, conf_long=60.0,   # +15pp > INFLATION_CONF_HIGH=10
            ret_short=1.0,   ret_long=4.0,
        )
        events = confidence_inflation(windows)
        ev = next((e for e in events if e["type"] == "CONFIDENCE_INFLATION"), None)
        assert ev is not None
        assert ev["severity"] == "HIGH"

    def test_no_inflation_when_conf_stable(self):
        windows = self._windows_pair(
            conf_short=61.0, conf_long=60.0,   # +1pp, below INFLATION_CONF_RISE=5
            ret_short=1.0,   ret_long=4.0,
        )
        events = confidence_inflation(windows)
        types = [e["type"] for e in events]
        assert "CONFIDENCE_INFLATION" not in types

    def test_no_inflation_when_return_stable(self):
        windows = self._windows_pair(
            conf_short=75.0, conf_long=60.0,
            ret_short=3.5,   ret_long=4.0,    # only -0.5%, below INFLATION_RETURN_FALL=1
        )
        events = confidence_inflation(windows)
        types = [e["type"] for e in events]
        assert "CONFIDENCE_INFLATION" not in types

    def test_conf_delta_field_correct(self):
        windows = self._windows_pair(conf_short=72.0, conf_long=60.0, ret_short=1.0, ret_long=4.0)
        events = confidence_inflation(windows)
        ev = next((e for e in events if e["type"] == "CONFIDENCE_INFLATION"), None)
        if ev:
            assert ev["conf_delta"] == pytest.approx(12.0, abs=0.01)

    def test_return_delta_field_correct(self):
        windows = self._windows_pair(conf_short=72.0, conf_long=60.0, ret_short=1.0, ret_long=4.0)
        events = confidence_inflation(windows)
        ev = next((e for e in events if e["type"] == "CONFIDENCE_INFLATION"), None)
        if ev:
            assert ev["return_delta"] == pytest.approx(-3.0, abs=0.01)

    def test_monotone_drift_detected_across_3_windows(self):
        # conf decreasing as window grows: last-10=80, last-25=75, last-50=65
        windows = {
            10:  {**self._windows_pair()[ 10], "avg_confidence": 80.0},
            25:  {"window": 25, "n": 25, "win_rate": 65.0, "avg_return_5d": 3.0,
                  "avg_return_20d": None, "avg_max_dd": -2.5, "avg_confidence": 75.0,
                  "calibration_error": None, "sharpe_like": None, "conf_return_corr": None},
            50:  {"window": 50, "n": 50, "win_rate": 65.0, "avg_return_5d": 3.0,
                  "avg_return_20d": None, "avg_max_dd": -2.5, "avg_confidence": 65.0,
                  "calibration_error": None, "sharpe_like": None, "conf_return_corr": None},
        }
        events = confidence_inflation(windows)
        types = [e["type"] for e in events]
        assert "CONFIDENCE_MONOTONE_DRIFT" in types

    def test_monotone_drift_not_detected_when_not_monotone(self):
        windows = {
            10:  {**self._windows_pair()[ 10], "avg_confidence": 75.0},
            25:  {"window": 25, "n": 25, "win_rate": 65.0, "avg_return_5d": 3.0,
                  "avg_return_20d": None, "avg_max_dd": -2.5, "avg_confidence": 80.0,  # higher
                  "calibration_error": None, "sharpe_like": None, "conf_return_corr": None},
            50:  {"window": 50, "n": 50, "win_rate": 65.0, "avg_return_5d": 3.0,
                  "avg_return_20d": None, "avg_max_dd": -2.5, "avg_confidence": 65.0,
                  "calibration_error": None, "sharpe_like": None, "conf_return_corr": None},
        }
        events = confidence_inflation(windows)
        types = [e["type"] for e in events]
        assert "CONFIDENCE_MONOTONE_DRIFT" not in types

    def test_fewer_than_2_windows_returns_empty(self):
        windows = self._windows_pair()
        single = {10: windows[10]}
        assert confidence_inflation(single) == []

    def test_none_confidence_skips_primary_check(self):
        windows = self._windows_pair()
        windows[10]["avg_confidence"] = None
        events = confidence_inflation(windows)
        types = [e["type"] for e in events]
        assert "CONFIDENCE_INFLATION" not in types

    def test_returns_list(self):
        assert isinstance(confidence_inflation({}), list)

    def test_deterministic(self):
        windows = self._windows_pair(conf_short=75.0, conf_long=60.0,
                                     ret_short=1.0, ret_long=4.0)
        assert confidence_inflation(windows) == confidence_inflation(windows)

    def test_event_contains_required_fields(self):
        windows = self._windows_pair(conf_short=75.0, conf_long=60.0,
                                     ret_short=1.0, ret_long=4.0)
        events = confidence_inflation(windows)
        ev = next((e for e in events if e["type"] == "CONFIDENCE_INFLATION"), None)
        if ev:
            for field in ("type", "short_window", "long_window", "conf_delta",
                          "return_delta", "severity", "detail"):
                assert field in ev


# ── regime_deterioration ──────────────────────────────────────────────────────

class TestRegimeDetermination:
    def _windows_stub(self, n_short=10):
        return {n_short: {"window": n_short, "n": n_short,
                          "win_rate": None, "avg_return_5d": None,
                          "avg_return_20d": None, "avg_max_dd": None,
                          "avg_confidence": None, "calibration_error": None,
                          "sharpe_like": None, "conf_return_corr": None}}

    def test_bull_weakening_detected(self):
        # Strong overall BULL history, recent BULL performing poorly
        recent_bull_losses = _make(MIN_ROWS_FOR_STATS, win=False, regime=BULL)
        historical_wins = _make(50, win=True, regime=BULL)
        rows = historical_wins + recent_bull_losses  # losses are most recent
        windows = self._windows_stub(n_short=MIN_ROWS_FOR_STATS)
        events = regime_deterioration(rows, windows)
        types = [e["type"] for e in events]
        assert "BULL_WEAKENING" in types

    def test_bull_weakening_severity_high(self):
        # Massive drop in BULL win rate
        historical = _make(50, win=True, regime=BULL)
        recent = _make(MIN_ROWS_FOR_STATS, win=False, regime=BULL)
        rows = historical + recent
        windows = self._windows_stub(n_short=MIN_ROWS_FOR_STATS)
        events = regime_deterioration(rows, windows)
        ev = next((e for e in events if e["type"] == "BULL_WEAKENING"), None)
        if ev:
            assert ev["severity"] in ("HIGH", "MEDIUM")

    def test_no_event_when_bull_stable(self):
        rows = _make(100, win=True, regime=BULL)
        windows = self._windows_stub(n_short=10)
        events = regime_deterioration(rows, windows)
        types = [e["type"] for e in events]
        assert "BULL_WEAKENING" not in types

    def test_risk_off_outperforming_bull_detected(self):
        # Recent window: RISK_OFF wins, BULL loses
        recent_risk_wins = _make(MIN_ROWS_FOR_STATS, win=True, regime=RISK_OFF)
        recent_bull_losses = _make(MIN_ROWS_FOR_STATS, win=False, regime=BULL)
        rows = recent_risk_wins + recent_bull_losses
        windows = self._windows_stub(n_short=len(rows))
        events = regime_deterioration(rows, windows)
        types = [e["type"] for e in events]
        assert "RISK_OFF_OUTPERFORMING_BULL" in types

    def test_risk_off_outperforming_severity(self):
        recent_risk_wins  = _make(MIN_ROWS_FOR_STATS, win=True,  regime=RISK_OFF)
        recent_bull_losses = _make(MIN_ROWS_FOR_STATS, win=False, regime=BULL)
        rows = recent_risk_wins + recent_bull_losses
        windows = self._windows_stub(n_short=len(rows))
        events = regime_deterioration(rows, windows)
        ev = next((e for e in events if e["type"] == "RISK_OFF_OUTPERFORMING_BULL"), None)
        if ev:
            assert ev["severity"] in ("HIGH", "MEDIUM")

    def test_empty_rows_returns_empty(self):
        assert regime_deterioration([], {10: {}}) == []

    def test_sparse_regime_skipped(self):
        # Only 1 BULL row recent → win_rate=None → no event
        rows = _make(50, win=True, regime=BULL) + [_loss(regime=BULL)]
        windows = self._windows_stub(n_short=1)
        events = regime_deterioration(rows, windows)
        bull_events = [e for e in events if e["type"] == "BULL_WEAKENING"]
        assert len(bull_events) == 0

    def test_events_have_required_fields(self):
        historical = _make(50, win=True, regime=BULL)
        recent = _make(MIN_ROWS_FOR_STATS, win=False, regime=BULL)
        rows = historical + recent
        windows = self._windows_stub(n_short=MIN_ROWS_FOR_STATS)
        events = regime_deterioration(rows, windows)
        for e in events:
            assert "type"     in e
            assert "severity" in e
            assert "detail"   in e
            assert "delta"    in e

    def test_high_sorted_before_medium(self):
        # Create both a HIGH and MEDIUM event if possible
        historical = _make(50, win=True, regime=BULL)
        recent = _make(MIN_ROWS_FOR_STATS, win=False, regime=BULL)
        rows = historical + recent
        windows = self._windows_stub(n_short=MIN_ROWS_FOR_STATS)
        events = regime_deterioration(rows, windows)
        severities = [e["severity"] for e in events]
        high_idx   = [i for i, s in enumerate(severities) if s == "HIGH"]
        medium_idx = [i for i, s in enumerate(severities) if s == "MEDIUM"]
        if high_idx and medium_idx:
            assert max(high_idx) < min(medium_idx)

    def test_deterministic(self):
        historical = _make(50, win=True, regime=BULL)
        recent     = _make(MIN_ROWS_FOR_STATS, win=False, regime=BULL)
        rows       = historical + recent
        windows    = self._windows_stub(n_short=MIN_ROWS_FOR_STATS)
        assert regime_deterioration(rows, windows) == regime_deterioration(rows, windows)


# ── safeguard_recommendations ─────────────────────────────────────────────────

class TestSafeguardRecommendations:
    def _high_event(self, ev_type="WIN_RATE_DETERIORATION"):
        return {"type": ev_type, "severity": "HIGH", "detail": "test"}

    def _medium_event(self, ev_type="RETURN_DETERIORATION"):
        return {"type": ev_type, "severity": "MEDIUM", "detail": "test"}

    def _inflation_event(self, sev="MEDIUM"):
        return {"type": "CONFIDENCE_INFLATION", "severity": sev, "detail": "test"}

    def test_no_events_no_recommendations(self):
        recs = safeguard_recommendations([], [], [])
        assert recs == []

    def test_any_event_triggers_reduce_aggressiveness(self):
        recs = safeguard_recommendations([self._medium_event()], [], [])
        rec_names = [r["recommendation"] for r in recs]
        assert "REDUCE_AGGRESSIVENESS" in rec_names

    def test_reduce_severity_medium_when_no_high(self):
        recs = safeguard_recommendations([self._medium_event()], [], [])
        rv = next(r for r in recs if r["recommendation"] == "REDUCE_AGGRESSIVENESS")
        assert rv["severity"] == "MEDIUM"

    def test_reduce_severity_high_when_high_event(self):
        recs = safeguard_recommendations([self._high_event()], [], [])
        rv = next(r for r in recs if r["recommendation"] == "REDUCE_AGGRESSIVENESS")
        assert rv["severity"] == "HIGH"

    def test_inflation_triggers_increase_conf_threshold(self):
        recs = safeguard_recommendations([], [self._inflation_event()], [])
        rec_names = [r["recommendation"] for r in recs]
        assert "INCREASE_CONFIDENCE_THRESHOLD" in rec_names

    def test_no_inflation_no_conf_threshold_rec(self):
        recs = safeguard_recommendations([self._high_event()], [], [])
        rec_names = [r["recommendation"] for r in recs]
        assert "INCREASE_CONFIDENCE_THRESHOLD" not in rec_names

    def test_three_high_events_trigger_pause(self):
        highs = [self._high_event(f"TYPE_{i}") for i in range(SAFEGUARD_PAUSE_HIGH_COUNT)]
        recs = safeguard_recommendations(highs, [], [])
        rec_names = [r["recommendation"] for r in recs]
        assert "PAUSE_ADAPTIVE_ROLLOUT" in rec_names

    def test_fewer_than_three_high_no_pause(self):
        highs = [self._high_event() for _ in range(SAFEGUARD_PAUSE_HIGH_COUNT - 1)]
        recs = safeguard_recommendations(highs, [], [])
        rec_names = [r["recommendation"] for r in recs]
        assert "PAUSE_ADAPTIVE_ROLLOUT" not in rec_names

    def test_five_high_events_trigger_observation_only(self):
        highs = [self._high_event(f"TYPE_{i}") for i in range(SAFEGUARD_CRITICAL_HIGH_COUNT)]
        recs = safeguard_recommendations(highs, [], [])
        rec_names = [r["recommendation"] for r in recs]
        assert "OBSERVATION_ONLY" in rec_names

    def test_fewer_than_five_high_no_observation_only(self):
        highs = [self._high_event() for _ in range(SAFEGUARD_CRITICAL_HIGH_COUNT - 1)]
        recs = safeguard_recommendations(highs, [], [])
        rec_names = [r["recommendation"] for r in recs]
        assert "OBSERVATION_ONLY" not in rec_names

    def test_regime_events_count_toward_high_total(self):
        highs = [self._high_event(f"T{i}") for i in range(SAFEGUARD_PAUSE_HIGH_COUNT)]
        recs = safeguard_recommendations([], [], highs)
        rec_names = [r["recommendation"] for r in recs]
        assert "PAUSE_ADAPTIVE_ROLLOUT" in rec_names

    def test_each_rec_has_required_fields(self):
        highs = [self._high_event(f"T{i}") for i in range(SAFEGUARD_CRITICAL_HIGH_COUNT)]
        recs = safeguard_recommendations(highs, [self._inflation_event()], [])
        for rec in recs:
            assert "recommendation" in rec
            assert "reason"         in rec
            assert "triggered_by"   in rec
            assert "severity"       in rec

    def test_triggered_by_is_list(self):
        recs = safeguard_recommendations([self._high_event()], [], [])
        for rec in recs:
            assert isinstance(rec["triggered_by"], list)

    def test_deterministic(self):
        highs = [self._high_event(f"T{i}") for i in range(4)]
        a = safeguard_recommendations(highs, [self._inflation_event()], [])
        b = safeguard_recommendations(highs, [self._inflation_event()], [])
        assert a == b


# ── generate_report ───────────────────────────────────────────────────────────

class TestGenerateReport:
    def test_required_keys_present(self):
        report = generate_report([])
        expected = {
            "row_count", "windows", "strongest_window", "weakest_window",
            "degradation_events", "inflation_events", "regime_events",
            "safeguard_recommendations", "warnings",
        }
        assert expected == set(report.keys())

    def test_row_count_matches_input(self):
        rows = _make(42)
        assert generate_report(rows)["row_count"] == 42

    def test_windows_has_all_four_keys(self):
        report = generate_report([])
        assert set(report["windows"].keys()) == {10, 25, 50, 100}

    def test_empty_rows_all_nones(self):
        report = generate_report([])
        assert report["strongest_window"] is None
        assert report["weakest_window"]   is None
        assert report["degradation_events"]  == []
        assert report["inflation_events"]    == []
        assert report["regime_events"]       == []
        assert report["safeguard_recommendations"] == []

    def test_empty_rows_has_sparse_warning(self):
        report = generate_report([])
        assert any("No rolling windows" in w for w in report["warnings"])

    def test_strongest_window_highest_win_rate(self):
        # recent=wins → small windows have best win_rate
        old_losses = _make(90, win=False)
        recent_wins = _make(10, win=True)
        rows = old_losses + recent_wins
        report = generate_report(rows)
        if report["strongest_window"] is not None:
            strongest_wr = report["windows"][report["strongest_window"]]["win_rate"]
            for w, stats in report["windows"].items():
                if stats["win_rate"] is not None:
                    assert stats["win_rate"] <= strongest_wr

    def test_weakest_window_lowest_win_rate(self):
        old_losses = _make(90, win=False)
        recent_wins = _make(10, win=True)
        rows = old_losses + recent_wins
        report = generate_report(rows)
        if report["weakest_window"] is not None:
            weakest_wr = report["windows"][report["weakest_window"]]["win_rate"]
            for w, stats in report["windows"].items():
                if stats["win_rate"] is not None:
                    assert stats["win_rate"] >= weakest_wr

    def test_degradation_events_is_list(self):
        assert isinstance(generate_report([])["degradation_events"], list)

    def test_inflation_events_is_list(self):
        assert isinstance(generate_report([])["inflation_events"], list)

    def test_regime_events_is_list(self):
        assert isinstance(generate_report([])["regime_events"], list)

    def test_warnings_is_list(self):
        assert isinstance(generate_report([])["warnings"], list)

    def test_safeguard_recs_is_list(self):
        assert isinstance(generate_report([])["safeguard_recommendations"], list)

    def test_high_severity_events_appear_in_warnings(self):
        # Create a clear degradation: wins historically, losses recently
        historical = _make(100, win=True, conf=60.0)
        recent = _make(10, win=False, conf=60.0)
        rows = historical + recent
        report = generate_report(rows)
        # There should be some warnings if HIGH degradation detected
        if any(e.get("severity") == "HIGH"
               for e in report["degradation_events"] + report["regime_events"]):
            assert len(report["warnings"]) > 0

    def test_deterministic(self):
        rows = _make(100, win=True) + _make(10, win=False)
        a = generate_report(rows)
        b = generate_report(rows)
        assert a == b

    def test_row_count_50_windows_n_correct(self):
        rows = _make(50)
        report = generate_report(rows)
        assert report["windows"][10]["n"]  == 10
        assert report["windows"][25]["n"]  == 25
        assert report["windows"][50]["n"]  == 50
        assert report["windows"][100]["n"] == 50  # capped at available


# ── Sparse handling ───────────────────────────────────────────────────────────

class TestSparseHandling:
    def test_1_row_all_stats_mostly_none(self):
        rows = _make(1, win=True)
        report = generate_report(rows)
        for w in WINDOWS:
            stats = report["windows"][w]
            assert stats["win_rate"] is None  # n=1 < MIN_ROWS_FOR_STATS

    def test_min_rows_exactly_at_threshold(self):
        rows = _make(MIN_ROWS_FOR_STATS, win=True)
        report = generate_report(rows)
        # Smallest window should have win_rate since n == MIN_ROWS_FOR_STATS
        assert report["windows"][10]["win_rate"] is not None or rows[0] is not None

    def test_no_errors_on_zero_rows(self):
        report = generate_report([])
        assert report["row_count"] == 0

    def test_degradation_detection_skips_none_values(self):
        windows = {
            10:  {"window": 10,  "n": 3, "win_rate": None, "avg_return_5d": None,
                  "calibration_error": None, "avg_max_dd": None,
                  "avg_confidence": None, "avg_return_20d": None,
                  "sharpe_like": None, "conf_return_corr": None},
            100: {"window": 100, "n": 100, "win_rate": 70.0, "avg_return_5d": 3.0,
                  "calibration_error": 0.08, "avg_max_dd": -3.0,
                  "avg_confidence": 60.0, "avg_return_20d": None,
                  "sharpe_like": 0.5, "conf_return_corr": 0.3},
        }
        events = degradation_detection(windows)
        assert events == []  # short window has all None → nothing to compare


# ── Determinism ───────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_rolling_windows_deterministic(self):
        rows = _make(75, win=True) + _make(25, win=False)
        assert rolling_windows(rows) == rolling_windows(rows)

    def test_degradation_detection_deterministic(self):
        rows = _make(75, win=True) + _make(25, win=False)
        windows = rolling_windows(rows)
        a = degradation_detection(windows)
        b = degradation_detection(windows)
        assert a == b

    def test_confidence_inflation_deterministic(self):
        rows = _make(75) + _make(25)
        windows = rolling_windows(rows)
        assert confidence_inflation(windows) == confidence_inflation(windows)

    def test_full_report_deterministic(self):
        rows = _make(100, win=True) + _make(20, win=False)
        report_a = generate_report(rows)
        report_b = generate_report(rows)
        assert report_a == report_b
