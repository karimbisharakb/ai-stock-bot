"""
Unit tests for confidence_validation.py.

All tests pass mock row dicts directly — no DB access, no network calls.
Expected values are computed analytically before asserting.
"""
import math
import pytest

from confidence_validation import (
    ECE_FAIR,
    ECE_GOOD,
    HIGH_CONFIDENCE_LO,
    OVERCONF_MARGIN,
    QUALITY_FAIR,
    QUALITY_GOOD,
    QUALITY_INSUFFICIENT,
    QUALITY_POOR,
    _DECADE_BOUNDS,
    _DECADE_MIDS,
    _DECADE_ORDER,
    _calibration_quality,
    _confidence_decade,
    _pearson,
    calibration_error,
    confidence_return_correlation,
    decade_stats,
    generate_report,
    monotonicity_analysis,
    overconfidence_flags,
)
from outcome_analytics import MIN_ROWS_FOR_STATS


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _row(
    confidence_pct=None,
    return_5d=None,
    return_20d=None,
    max_gain_pct=None,
    max_drawdown_pct=None,
):
    return {
        "confidence_pct":   confidence_pct,
        "return_5d":        return_5d,
        "return_20d":       return_20d,
        "max_gain_pct":     max_gain_pct,
        "max_drawdown_pct": max_drawdown_pct,
    }


def _win(conf):
    """A clearly winning row at the given confidence."""
    return _row(confidence_pct=conf, return_5d=5.0, max_gain_pct=8.0)


def _loss(conf):
    """A clearly losing row at the given confidence."""
    return _row(confidence_pct=conf, return_5d=-5.0, max_gain_pct=1.0)


def _make(n, conf, win=True):
    maker = _win if win else _loss
    return [maker(conf) for _ in range(n)]


# ── _confidence_decade ────────────────────────────────────────────────────────

class TestConfidenceDecade:
    def test_exact_boundaries(self):
        assert _confidence_decade(0.0)   == "0-9"
        assert _confidence_decade(9.9)   == "0-9"
        assert _confidence_decade(10.0)  == "10-19"
        assert _confidence_decade(50.0)  == "50-59"
        assert _confidence_decade(59.9)  == "50-59"
        assert _confidence_decade(60.0)  == "60-69"
        assert _confidence_decade(90.0)  == "90-100"
        assert _confidence_decade(100.0) == "90-100"

    def test_none_returns_none(self):
        assert _confidence_decade(None) is None

    def test_out_of_range_returns_none(self):
        assert _confidence_decade(-1.0)  is None
        assert _confidence_decade(100.1) is None

    def test_mid_values(self):
        assert _confidence_decade(75.0) == "70-79"
        assert _confidence_decade(84.9) == "80-89"
        assert _confidence_decade(95.0) == "90-100"

    def test_all_decades_reachable(self):
        for label, lo, hi, _ in [
            ("0-9",    0.0,  10.0,  4.5),
            ("10-19",  10.0, 20.0, 14.5),
            ("20-29",  20.0, 30.0, 24.5),
            ("30-39",  30.0, 40.0, 34.5),
            ("40-49",  40.0, 50.0, 44.5),
            ("50-59",  50.0, 60.0, 54.5),
            ("60-69",  60.0, 70.0, 64.5),
            ("70-79",  70.0, 80.0, 74.5),
            ("80-89",  80.0, 90.0, 84.5),
            ("90-100", 90.0, 100.0, 95.0),
        ]:
            assert _confidence_decade(lo) == label


# ── _pearson ──────────────────────────────────────────────────────────────────

class TestPearson:
    def test_perfect_positive_correlation(self):
        # xs=50,60,70,80,90  ys=1,2,3,4,5  → r=1.0
        xs = [50.0, 60.0, 70.0, 80.0, 90.0]
        ys = [1.0,   2.0,  3.0,  4.0,  5.0]
        r  = _pearson(xs, ys)
        assert r == pytest.approx(1.0, abs=1e-4)

    def test_perfect_negative_correlation(self):
        xs = [50.0, 60.0, 70.0, 80.0, 90.0]
        ys = [5.0,   4.0,  3.0,  2.0,  1.0]
        r  = _pearson(xs, ys)
        assert r == pytest.approx(-1.0, abs=1e-4)

    def test_zero_correlation(self):
        # cov = 0: symmetric xs with mirrored ys
        # xs=[60,70,80,90] mean=75; ys=[1,-1,-1,1] mean=0
        # cov = [(-15×1)+(-5×-1)+(5×-1)+(15×1)] / 4 = 0/4 = 0
        xs = [60.0, 70.0, 80.0, 90.0]
        ys = [1.0, -1.0, -1.0, 1.0]
        r  = _pearson(xs, ys)
        assert r == pytest.approx(0.0, abs=1e-9)

    def test_fewer_than_3_pairs_returns_none(self):
        assert _pearson([50.0, 60.0], [1.0, 2.0]) is None
        assert _pearson([50.0], [1.0])             is None
        assert _pearson([], [])                    is None

    def test_none_values_dropped(self):
        # 4 valid pairs after dropping Nones
        xs = [50.0, None, 70.0, 80.0, 90.0]
        ys = [1.0,   2.0,  3.0,  4.0,  5.0]
        r  = _pearson(xs, ys)
        assert r is not None  # 4 valid pairs → computable

    def test_zero_variance_x_returns_none(self):
        xs = [70.0, 70.0, 70.0, 70.0]
        ys = [1.0,   2.0,  3.0,  4.0]
        assert _pearson(xs, ys) is None

    def test_zero_variance_y_returns_none(self):
        xs = [50.0, 60.0, 70.0, 80.0]
        ys = [5.0,   5.0,  5.0,  5.0]
        assert _pearson(xs, ys) is None

    def test_result_bounded_in_minus1_to_1(self):
        import random
        rng = random.Random(42)
        xs  = [rng.uniform(0, 100) for _ in range(20)]
        ys  = [rng.uniform(-10, 10) for _ in range(20)]
        r   = _pearson(xs, ys)
        assert r is not None
        assert -1.0 <= r <= 1.0


# ── decade_stats ──────────────────────────────────────────────────────────────

class TestDecadeStats:
    def test_all_decades_present(self):
        result = decade_stats([])
        assert set(result.keys()) == set(_DECADE_ORDER)

    def test_empty_rows_all_zero(self):
        result = decade_stats([])
        for label in _DECADE_ORDER:
            assert result[label]["n"]        == 0
            assert result[label]["win_rate"] is None

    def test_correct_bucket_assignment(self):
        rows   = _make(MIN_ROWS_FOR_STATS, conf=75.0, win=True)
        result = decade_stats(rows)
        assert result["70-79"]["n"]        == MIN_ROWS_FOR_STATS
        assert result["70-79"]["win_rate"] == 100.0
        # Other decades empty
        for label in _DECADE_ORDER:
            if label != "70-79":
                assert result[label]["n"] == 0

    def test_win_rate_none_when_sparse(self):
        rows   = _make(MIN_ROWS_FOR_STATS - 1, conf=55.0, win=True)
        result = decade_stats(rows)
        assert result["50-59"]["n"]        == MIN_ROWS_FOR_STATS - 1
        assert result["50-59"]["win_rate"] is None

    def test_mean_confidence_computed(self):
        rows   = _make(MIN_ROWS_FOR_STATS, conf=63.0, win=True)
        result = decade_stats(rows)
        assert result["60-69"]["mean_confidence"] == pytest.approx(63.0, abs=1e-4)

    def test_mean_confidence_averaged_across_bucket(self):
        # Two different confidence values in same bucket
        rows = (
            [_win(61.0)] * 3 +
            [_win(69.0)] * 2
        )
        result = decade_stats(rows)
        # mean = (61*3 + 69*2) / 5 = (183 + 138) / 5 = 321/5 = 64.2
        assert result["60-69"]["mean_confidence"] == pytest.approx(64.2, abs=1e-3)

    def test_ordered_output(self):
        keys = list(decade_stats([]).keys())
        assert keys == _DECADE_ORDER

    def test_avg_fields_present(self):
        rows   = [_row(confidence_pct=75.0, return_5d=3.0, return_20d=6.0,
                       max_gain_pct=8.0, max_drawdown_pct=-2.0)] * MIN_ROWS_FOR_STATS
        result = decade_stats(rows)
        b      = result["70-79"]
        assert b["avg_return_5d"]  == pytest.approx(3.0, abs=1e-4)
        assert b["avg_return_20d"] == pytest.approx(6.0, abs=1e-4)
        assert b["avg_max_gain"]   == pytest.approx(8.0, abs=1e-4)
        assert b["avg_max_dd"]     == pytest.approx(-2.0, abs=1e-4)

    def test_deterministic(self):
        rows = _make(MIN_ROWS_FOR_STATS, conf=75.0, win=True)
        assert decade_stats(rows) == decade_stats(rows)


# ── calibration_error ─────────────────────────────────────────────────────────

class TestCalibrationError:
    def test_none_when_empty(self):
        assert calibration_error([]) is None

    def test_none_when_all_sparse(self):
        rows = _make(MIN_ROWS_FOR_STATS - 1, conf=75.0, win=True)
        assert calibration_error(rows) is None

    def test_exact_ece_one_bucket_all_wins(self):
        # Bucket "50-59", mean_conf=55, all wins → win_frac=1.0
        # ECE = (n/n) × |1.0 - 0.55| = 0.45
        rows = _make(MIN_ROWS_FOR_STATS, conf=55.0, win=True)
        ece  = calibration_error(rows)
        assert ece == pytest.approx(0.45, abs=1e-4)

    def test_exact_ece_one_bucket_all_losses(self):
        # Bucket "70-79", mean_conf=75, all losses → win_frac=0.0
        # ECE = 1.0 × |0.0 - 0.75| = 0.75
        rows = _make(MIN_ROWS_FOR_STATS, conf=75.0, win=False)
        ece  = calibration_error(rows)
        assert ece == pytest.approx(0.75, abs=1e-4)

    def test_perfect_calibration(self):
        # If confidence ≈ win fraction, ECE should be near zero
        # Bucket "80-89": mean_conf=85%, and 85% of rows win
        # We can't achieve exact 85% with whole rows easily,
        # but 5 rows all winning at 100% confidence would have ECE=0.05
        # Instead: bucket "90-100" (mid=95), all wins → ECE = |1.0 - 0.95| = 0.05
        rows = _make(MIN_ROWS_FOR_STATS, conf=95.0, win=True)
        ece  = calibration_error(rows)
        assert ece == pytest.approx(0.05, abs=1e-4)

    def test_two_buckets_weighted_average(self):
        # Bucket 1: "50-59" n=5, mean_conf=55, win_frac=1.0 → error=0.45
        # Bucket 2: "70-79" n=5, mean_conf=75, win_frac=0.0 → error=0.75
        # N=10, ECE = (5/10)×0.45 + (5/10)×0.75 = 0.225 + 0.375 = 0.60
        rows = _make(MIN_ROWS_FOR_STATS, conf=55.0, win=True) + \
               _make(MIN_ROWS_FOR_STATS, conf=75.0, win=False)
        ece = calibration_error(rows)
        assert ece == pytest.approx(0.60, abs=1e-4)

    def test_sparse_buckets_excluded(self):
        # One qualifying bucket + one sparse bucket
        # Only the qualifying bucket should contribute to ECE
        qualifying = _make(MIN_ROWS_FOR_STATS, conf=55.0, win=True)   # ece=0.45
        sparse     = _make(MIN_ROWS_FOR_STATS - 1, conf=85.0, win=False)
        ece        = calibration_error(qualifying + sparse)
        # sparse bucket excluded → ECE = 0.45
        assert ece == pytest.approx(0.45, abs=1e-4)

    def test_ece_in_zero_to_one(self):
        rows = _make(MIN_ROWS_FOR_STATS, conf=50.0, win=False)
        ece  = calibration_error(rows)
        assert 0.0 <= ece <= 1.0

    def test_deterministic(self):
        rows = _make(MIN_ROWS_FOR_STATS, conf=65.0, win=True)
        assert calibration_error(rows) == calibration_error(rows)


# ── confidence_return_correlation ──────────────────────────────────────────────

class TestConfidenceReturnCorrelation:
    def test_positive_correlation(self):
        rows = [_row(confidence_pct=c, return_5d=r) for c, r in
                [(50, 1.0), (60, 2.0), (70, 3.0), (80, 4.0), (90, 5.0)]]
        corr = confidence_return_correlation(rows)
        assert corr == pytest.approx(1.0, abs=1e-3)

    def test_negative_correlation(self):
        rows = [_row(confidence_pct=c, return_5d=r) for c, r in
                [(50, 5.0), (60, 4.0), (70, 3.0), (80, 2.0), (90, 1.0)]]
        corr = confidence_return_correlation(rows)
        assert corr == pytest.approx(-1.0, abs=1e-3)

    def test_none_when_fewer_than_3(self):
        rows = [_row(confidence_pct=50, return_5d=1.0),
                _row(confidence_pct=60, return_5d=2.0)]
        assert confidence_return_correlation(rows) is None

    def test_none_when_no_return_5d(self):
        rows = [_row(confidence_pct=70) for _ in range(5)]
        assert confidence_return_correlation(rows) is None

    def test_none_values_excluded(self):
        # 3 valid pairs + 2 rows with None return
        rows = [
            _row(confidence_pct=50, return_5d=1.0),
            _row(confidence_pct=60, return_5d=2.0),
            _row(confidence_pct=70, return_5d=3.0),
            _row(confidence_pct=80, return_5d=None),
            _row(confidence_pct=90, return_5d=None),
        ]
        corr = confidence_return_correlation(rows)
        assert corr is not None  # 3 valid pairs → computable

    def test_bounded(self):
        rows = [_row(confidence_pct=float(i*10), return_5d=float(i))
                for i in range(1, 10)]
        corr = confidence_return_correlation(rows)
        assert corr is not None
        assert -1.0 <= corr <= 1.0

    def test_deterministic(self):
        rows = [_row(confidence_pct=float(c), return_5d=float(r))
                for c, r in [(50, 1), (60, 2), (70, 3), (80, 4), (90, 5)]]
        assert confidence_return_correlation(rows) == confidence_return_correlation(rows)


# ── monotonicity_analysis ──────────────────────────────────────────────────────

class TestMonotonicityAnalysis:
    def _stats_from(self, decade_win_rates: dict) -> dict:
        """Build a minimal stats dict from {decade: win_rate}."""
        stats = {label: {"n": 0, "win_rate": None} for label in _DECADE_ORDER}
        for label, wr in decade_win_rates.items():
            stats[label] = {"n": MIN_ROWS_FOR_STATS, "win_rate": wr}
        return stats

    def test_perfectly_monotone(self):
        stats  = self._stats_from({"50-59": 40.0, "60-69": 55.0, "70-79": 70.0})
        result = monotonicity_analysis(stats)
        assert result["is_monotone"]     is True
        assert result["inversion_count"] == 0
        assert result["inversions"]      == []

    def test_equal_win_rates_monotone(self):
        # Non-strictly increasing: ties are OK
        stats  = self._stats_from({"50-59": 60.0, "60-69": 60.0, "70-79": 60.0})
        result = monotonicity_analysis(stats)
        assert result["is_monotone"] is True

    def test_one_inversion(self):
        # 60-69 (80%) > 70-79 (50%) — inversion
        stats  = self._stats_from({"50-59": 40.0, "60-69": 80.0, "70-79": 50.0})
        result = monotonicity_analysis(stats)
        assert result["is_monotone"]     is False
        assert result["inversion_count"] == 1
        assert result["inversions"][0]["low_decade"]  == "60-69"
        assert result["inversions"][0]["high_decade"] == "70-79"
        assert result["inversions"][0]["delta"]       == pytest.approx(-30.0, abs=0.1)

    def test_multiple_inversions(self):
        # 50-59→60-69: OK; 60-69→70-79: inversion; 70-79→80-89: inversion
        stats  = self._stats_from({
            "50-59": 30.0,
            "60-69": 70.0,
            "70-79": 40.0,
            "80-89": 20.0,
        })
        result = monotonicity_analysis(stats)
        assert result["inversion_count"] == 2

    def test_empty_stats_all_none(self):
        stats  = {label: {"n": 0, "win_rate": None} for label in _DECADE_ORDER}
        result = monotonicity_analysis(stats)
        assert result["is_monotone"]     is True
        assert result["buckets_analyzed"] == 0

    def test_single_bucket(self):
        stats  = self._stats_from({"70-79": 65.0})
        result = monotonicity_analysis(stats)
        assert result["is_monotone"]      is True
        assert result["inversion_count"]  == 0
        assert result["buckets_analyzed"] == 1

    def test_buckets_analyzed_count(self):
        stats  = self._stats_from({"50-59": 50.0, "70-79": 60.0, "90-100": 70.0})
        result = monotonicity_analysis(stats)
        assert result["buckets_analyzed"] == 3

    def test_inversion_delta_sign(self):
        stats  = self._stats_from({"70-79": 80.0, "80-89": 60.0})
        result = monotonicity_analysis(stats)
        assert result["inversions"][0]["delta"] < 0

    def test_deterministic(self):
        stats = self._stats_from({"50-59": 40.0, "60-69": 80.0, "70-79": 50.0})
        assert monotonicity_analysis(stats) == monotonicity_analysis(stats)

    def test_non_adjacent_decades_gapped(self):
        # 50-59 and 80-89 only — no consecutive pair between them, so no inversion
        stats  = self._stats_from({"50-59": 70.0, "80-89": 40.0})
        result = monotonicity_analysis(stats)
        # These aren't adjacent in the valid list — but they ARE the consecutive
        # pair in the valid-only list: [("50-59", 70), ("80-89", 40)] → inversion
        assert result["inversion_count"] == 1


# ── overconfidence_flags ───────────────────────────────────────────────────────

class TestOverconfidenceFlags:
    def _stats_for(self, conf, wr):
        """Minimal decade_stats with one populated bucket."""
        stats = {label: {"n": 0, "win_rate": None, "avg_max_dd": None}
                 for label in _DECADE_ORDER}
        label = _confidence_decade(conf)
        stats[label] = {
            "n":        MIN_ROWS_FOR_STATS,
            "win_rate": wr,
            "avg_max_dd": -2.0,
        }
        return stats

    def test_no_flags_when_calibrated(self):
        # mid=84.5, win_rate=75.0 → gap=9.5 < OVERCONF_MARGIN=15
        rows  = _make(MIN_ROWS_FOR_STATS, conf=84.0, win=True)
        stats = self._stats_for(84.0, 75.0)
        flags = overconfidence_flags(rows, stats)
        assert len(flags) == 0

    def test_flag_raised_when_gap_exceeds_margin(self):
        # mid=84.5, win_rate=60.0 → gap=24.5 > 15
        rows  = _make(MIN_ROWS_FOR_STATS, conf=84.0, win=False)
        stats = self._stats_for(84.0, 60.0)
        flags = overconfidence_flags(rows, stats)
        assert len(flags) >= 1
        assert flags[0]["decade"]     == "80-89"
        assert flags[0]["overconf_gap"] > OVERCONF_MARGIN

    def test_is_high_confidence_true_for_high_bucket(self):
        # 80-89 → lo=80 >= HIGH_CONFIDENCE_LO=70
        rows  = _make(MIN_ROWS_FOR_STATS, conf=85.0, win=False)
        stats = self._stats_for(85.0, 50.0)
        flags = overconfidence_flags(rows, stats)
        if flags:
            hc_flag = next(f for f in flags if f["decade"] == "80-89")
            assert hc_flag["is_high_confidence"] is True

    def test_is_high_confidence_false_for_low_bucket(self):
        # 50-59 → lo=50 < HIGH_CONFIDENCE_LO=70
        rows  = _make(MIN_ROWS_FOR_STATS, conf=55.0, win=False)
        stats = self._stats_for(55.0, 30.0)  # gap = 54.5-30 = 24.5 > 15
        flags = overconfidence_flags(rows, stats)
        if flags:
            flag = next(f for f in flags if f["decade"] == "50-59")
            assert flag["is_high_confidence"] is False

    def test_sparse_bucket_not_flagged(self):
        rows  = _make(MIN_ROWS_FOR_STATS - 1, conf=85.0, win=False)
        stats = {label: {"n": 0, "win_rate": None, "avg_max_dd": None}
                 for label in _DECADE_ORDER}
        stats["80-89"] = {
            "n":         MIN_ROWS_FOR_STATS - 1,
            "win_rate":  None,    # None = sparse → no flag
            "avg_max_dd": None,
        }
        flags = overconfidence_flags(rows, stats)
        assert all(f["decade"] != "80-89" for f in flags)

    def test_sorted_by_gap_descending(self):
        stats = {label: {"n": 0, "win_rate": None, "avg_max_dd": None}
                 for label in _DECADE_ORDER}
        # Two buckets with different gaps
        stats["70-79"] = {"n": MIN_ROWS_FOR_STATS, "win_rate": 40.0, "avg_max_dd": -2.0}
        # mid=74.5 - 40 = 34.5
        stats["80-89"] = {"n": MIN_ROWS_FOR_STATS, "win_rate": 20.0, "avg_max_dd": -3.0}
        # mid=84.5 - 20 = 64.5
        rows  = []
        flags = overconfidence_flags(rows, stats)
        gaps  = [f["overconf_gap"] for f in flags]
        assert gaps == sorted(gaps, reverse=True)

    def test_high_severity_for_large_gap(self):
        # gap = 84.5 - 0.0 = 84.5 > OVERCONF_MARGIN * 2 = 30.0 → HIGH
        rows  = _make(MIN_ROWS_FOR_STATS, conf=85.0, win=False)
        stats = self._stats_for(85.0, 0.0)
        flags = overconfidence_flags(rows, stats)
        if flags:
            assert flags[0]["severity"] == "HIGH"

    def test_medium_severity_for_moderate_gap(self):
        # gap = 84.5 - 65 = 19.5 → > 15 (MEDIUM since < 30)
        rows  = _make(MIN_ROWS_FOR_STATS, conf=85.0, win=True)
        stats = self._stats_for(85.0, 65.0)
        flags = overconfidence_flags(rows, stats)
        if flags:
            assert flags[0]["severity"] == "MEDIUM"

    def test_deterministic(self):
        rows  = _make(MIN_ROWS_FOR_STATS, conf=85.0, win=False)
        stats = self._stats_for(85.0, 50.0)
        assert overconfidence_flags(rows, stats) == overconfidence_flags(rows, stats)


# ── _calibration_quality ──────────────────────────────────────────────────────

class TestCalibrationQuality:
    def _mono(self, inversions=0, analyzed=3):
        return {
            "is_monotone":     inversions == 0,
            "inversion_count": inversions,
            "inversions":      [{}] * inversions,
            "buckets_analyzed": analyzed,
        }

    def test_insufficient_when_ece_none(self):
        assert _calibration_quality(None, self._mono()) == QUALITY_INSUFFICIENT

    def test_good_when_low_ece_and_monotone(self):
        assert _calibration_quality(ECE_GOOD - 0.01, self._mono(0)) == QUALITY_GOOD

    def test_not_good_when_not_monotone(self):
        quality = _calibration_quality(ECE_GOOD - 0.01, self._mono(1))
        assert quality != QUALITY_GOOD

    def test_fair_when_low_ece_with_one_inversion(self):
        assert _calibration_quality(ECE_GOOD - 0.01, self._mono(1)) == QUALITY_FAIR

    def test_fair_when_moderate_ece_monotone(self):
        ece = (ECE_GOOD + ECE_FAIR) / 2
        assert _calibration_quality(ece, self._mono(0)) == QUALITY_FAIR

    def test_poor_when_high_ece_and_multiple_inversions(self):
        assert _calibration_quality(ECE_FAIR + 0.05, self._mono(3)) == QUALITY_POOR

    def test_fair_when_high_ece_but_monotone(self):
        # ECE > ECE_FAIR but is_monotone → one condition is True → FAIR
        assert _calibration_quality(ECE_FAIR + 0.05, self._mono(0)) == QUALITY_FAIR


# ── generate_report ────────────────────────────────────────────────────────────

class TestGenerateReport:
    def test_structure_keys(self):
        report = generate_report([])
        for key in ("row_count", "decade_stats", "calibration",
                    "overconfidence_flags", "strongest_bucket",
                    "weakest_bucket", "warnings"):
            assert key in report

    def test_calibration_keys(self):
        report = generate_report([])
        for key in ("ece", "correlation", "monotonicity", "quality"):
            assert key in report["calibration"]

    def test_empty_rows(self):
        report = generate_report([])
        assert report["row_count"]           == 0
        assert report["strongest_bucket"]    is None
        assert report["weakest_bucket"]      is None
        assert report["calibration"]["ece"]  is None
        assert report["calibration"]["quality"] == QUALITY_INSUFFICIENT

    def test_row_count(self):
        rows   = _make(8, conf=75.0, win=True)
        report = generate_report(rows)
        assert report["row_count"] == 8

    def test_all_decades_in_decade_stats(self):
        report = generate_report([])
        assert set(report["decade_stats"].keys()) == set(_DECADE_ORDER)

    def test_strongest_weakest_populated_with_enough_data(self):
        good = _make(MIN_ROWS_FOR_STATS, conf=75.0, win=True)
        bad  = _make(MIN_ROWS_FOR_STATS, conf=55.0, win=False)
        report = generate_report(good + bad)
        assert report["strongest_bucket"] is not None
        assert report["weakest_bucket"]   is not None
        assert report["strongest_bucket"]["label"] == "70-79"
        assert report["weakest_bucket"]["label"]   == "50-59"

    def test_warnings_list_type(self):
        report = generate_report([])
        assert isinstance(report["warnings"], list)

    def test_inversion_generates_warning(self):
        # Build rows that will create an inversion:
        # 60-69 all wins, 70-79 all losses → monotonicity violation
        rows = (
            _make(MIN_ROWS_FOR_STATS, conf=65.0, win=True) +
            _make(MIN_ROWS_FOR_STATS, conf=75.0, win=False)
        )
        report = generate_report(rows)
        assert any("nversion" in w for w in report["warnings"])

    def test_sparse_bucket_generates_warning(self):
        # 2 rows in a bucket → sparse warning
        rows   = _make(MIN_ROWS_FOR_STATS - 1, conf=75.0, win=True)
        report = generate_report(rows)
        assert any("70-79" in w for w in report["warnings"])

    def test_overconfidence_generates_warning(self):
        # High confidence, very low win rate → overconfidence flag → warning
        rows = _make(MIN_ROWS_FOR_STATS, conf=85.0, win=False)
        report = generate_report(rows)
        assert any("verconfidence" in w for w in report["warnings"])

    def test_negative_correlation_generates_warning(self):
        # Confidence increasing, returns decreasing → negative correlation
        rows = [_row(confidence_pct=float(c), return_5d=float(r))
                for c, r in [(50, 5), (60, 4), (70, 3), (80, 2), (90, 1),
                             (55, 4.5), (65, 3.5), (75, 2.5), (85, 1.5), (95, 0.5)]]
        report = generate_report(rows)
        assert any("orrelation" in w for w in report["warnings"])

    def test_deterministic(self):
        rows = (
            _make(MIN_ROWS_FOR_STATS, conf=65.0, win=True) +
            _make(MIN_ROWS_FOR_STATS, conf=75.0, win=False)
        )
        r1 = generate_report(rows)
        r2 = generate_report(rows)
        assert r1["row_count"]                       == r2["row_count"]
        assert r1["calibration"]["quality"]          == r2["calibration"]["quality"]
        assert r1["calibration"]["ece"]              == r2["calibration"]["ece"]
        assert r1["calibration"]["monotonicity"]     == r2["calibration"]["monotonicity"]
        assert r1["strongest_bucket"]                == r2["strongest_bucket"]
        assert r1["overconfidence_flags"]            == r2["overconfidence_flags"]
        assert r1["warnings"]                        == r2["warnings"]

    def test_good_quality_when_well_calibrated(self):
        # Confidence 90-100, all wins → ECE = |1.0 - ~0.95| = 0.05 < ECE_GOOD
        rows   = _make(MIN_ROWS_FOR_STATS, conf=95.0, win=True)
        report = generate_report(rows)
        # Only one bucket → trivially monotone; ECE ≈ 0.05
        assert report["calibration"]["quality"] in (QUALITY_GOOD, QUALITY_FAIR)


# ── Decade lookup tables sanity ────────────────────────────────────────────────

class TestDecadeTables:
    def test_decade_order_is_ascending(self):
        los = [_DECADE_BOUNDS[label][0] for label in _DECADE_ORDER]
        assert los == sorted(los)

    def test_decade_mids_within_bounds(self):
        for label in _DECADE_ORDER:
            lo, hi = _DECADE_BOUNDS[label]
            mid = _DECADE_MIDS[label]
            assert lo <= mid <= min(hi, 100.0)

    def test_decades_cover_0_to_100(self):
        assert _DECADE_BOUNDS[_DECADE_ORDER[0]][0]  == 0.0
        assert _DECADE_BOUNDS[_DECADE_ORDER[-1]][1] == 101.0
