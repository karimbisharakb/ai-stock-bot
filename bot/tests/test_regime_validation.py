"""
Unit tests for regime_validation.py.

All tests pass mock row dicts directly — no DB access, no network calls.
"""
import pytest

from regime_validation import (
    ALL_REGIMES,
    DEGRADATION_THRESHOLD,
    INVERSION_HIGH_THRESHOLD,
    REGIME_ORDER,
    _delta,
    _degradation_label,
    _pearson,
    _regime_bucket,
    generate_report,
    inversion_detection,
    regime_stats,
    regime_transition_stats,
    suppression_analysis,
)
from market_regime import BULL, NEUTRAL, RISK_OFF
from outcome_analytics import MIN_ROWS_FOR_STATS


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _row(
    regime=None,
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


def _win(regime, conf=70.0):
    return _row(regime=regime, return_5d=5.0, max_gain_pct=8.0, confidence_pct=conf)


def _loss(regime, conf=50.0):
    return _row(regime=regime, return_5d=-5.0, max_gain_pct=1.0, confidence_pct=conf)


def _make(n, regime, win=True, conf=65.0):
    maker = _win if win else _loss
    return [maker(regime, conf=conf) for _ in range(n)]


# ── _pearson ──────────────────────────────────────────────────────────────────

class TestPearsonHelper:
    def test_positive(self):
        xs = [50.0, 60.0, 70.0, 80.0, 90.0]
        ys = [1.0,   2.0,  3.0,  4.0,  5.0]
        assert _pearson(xs, ys) == pytest.approx(1.0, abs=1e-4)

    def test_negative(self):
        xs = [50.0, 60.0, 70.0, 80.0, 90.0]
        ys = [5.0,   4.0,  3.0,  2.0,  1.0]
        assert _pearson(xs, ys) == pytest.approx(-1.0, abs=1e-4)

    def test_too_few_returns_none(self):
        assert _pearson([50.0, 60.0], [1.0, 2.0]) is None

    def test_zero_variance_returns_none(self):
        assert _pearson([70.0, 70.0, 70.0, 70.0], [1.0, 2.0, 3.0, 4.0]) is None

    def test_none_values_skipped(self):
        xs = [50.0, None, 70.0, 80.0, 90.0]
        ys = [1.0,   2.0,  3.0,  4.0,  5.0]
        assert _pearson(xs, ys) is not None   # 4 valid pairs


# ── _delta ────────────────────────────────────────────────────────────────────

class TestDelta:
    def test_positive_delta(self):
        assert _delta(10.0, 15.0) == pytest.approx(5.0, abs=1e-6)

    def test_negative_delta(self):
        assert _delta(15.0, 10.0) == pytest.approx(-5.0, abs=1e-6)

    def test_zero_delta(self):
        assert _delta(10.0, 10.0) == pytest.approx(0.0, abs=1e-6)

    def test_none_a_returns_none(self):
        assert _delta(None, 10.0) is None

    def test_none_b_returns_none(self):
        assert _delta(10.0, None) is None

    def test_both_none_returns_none(self):
        assert _delta(None, None) is None


# ── _degradation_label ────────────────────────────────────────────────────────

class TestDegradationLabel:
    def test_none_gives_insufficient(self):
        assert _degradation_label(None) == "insufficient_data"

    def test_large_negative_gives_degrading(self):
        assert _degradation_label(-(DEGRADATION_THRESHOLD + 0.1)) == "degrading"

    def test_large_positive_gives_improving(self):
        assert _degradation_label(DEGRADATION_THRESHOLD + 0.1) == "improving"

    def test_within_threshold_gives_stable(self):
        assert _degradation_label(0.0)                 == "stable"
        assert _degradation_label(DEGRADATION_THRESHOLD)  == "stable"
        assert _degradation_label(-DEGRADATION_THRESHOLD) == "stable"


# ── regime_stats ──────────────────────────────────────────────────────────────

class TestRegimeStats:
    def test_all_known_regimes_present(self):
        result = regime_stats([])
        for r in (BULL, NEUTRAL, RISK_OFF):
            assert r in result

    def test_empty_rows_all_zero(self):
        result = regime_stats([])
        for r in (BULL, NEUTRAL, RISK_OFF):
            assert result[r]["n"]        == 0
            assert result[r]["win_rate"] is None

    def test_win_rate_correct(self):
        rows = _make(MIN_ROWS_FOR_STATS, BULL, win=True)
        result = regime_stats(rows)
        assert result[BULL]["win_rate"] == 100.0

    def test_win_rate_none_when_sparse(self):
        rows   = _make(MIN_ROWS_FOR_STATS - 1, BULL, win=True)
        result = regime_stats(rows)
        assert result[BULL]["win_rate"] is None

    def test_avg_return_5d_correct(self):
        rows = _make(MIN_ROWS_FOR_STATS, NEUTRAL, win=True)  # return_5d = 5.0
        result = regime_stats(rows)
        assert result[NEUTRAL]["avg_return_5d"] == pytest.approx(5.0, abs=1e-4)

    def test_avg_confidence_computed(self):
        rows = _make(MIN_ROWS_FOR_STATS, BULL, win=True, conf=72.0)
        result = regime_stats(rows)
        assert result[BULL]["avg_confidence"] == pytest.approx(72.0, abs=1e-4)

    def test_confidence_accuracy_computed_when_enough_data(self):
        # Positive correlation: higher conf → higher return_5d
        rows = [_row(regime=BULL, confidence_pct=float(c), return_5d=float(r))
                for c, r in [(50, 1), (60, 2), (70, 3), (80, 4), (90, 5)]]
        result = regime_stats(rows)
        assert result[BULL]["confidence_accuracy"] == pytest.approx(1.0, abs=1e-3)

    def test_unknown_regime_grouped(self):
        rows   = _make(MIN_ROWS_FOR_STATS, None, win=True)
        result = regime_stats(rows)
        assert "UNKNOWN" in result
        assert result["UNKNOWN"]["n"] == MIN_ROWS_FOR_STATS

    def test_correct_rows_counts(self):
        bull_rows    = _make(3, BULL)
        neutral_rows = _make(4, NEUTRAL)
        risk_rows    = _make(5, RISK_OFF)
        result = regime_stats(bull_rows + neutral_rows + risk_rows)
        assert result[BULL]["n"]     == 3
        assert result[NEUTRAL]["n"]  == 4
        assert result[RISK_OFF]["n"] == 5

    def test_all_fields_present(self):
        rows   = _make(MIN_ROWS_FOR_STATS, BULL, win=True)
        result = regime_stats(rows)
        for key in ("n", "win_rate", "avg_return_5d", "avg_return_20d",
                    "avg_max_gain", "avg_max_dd", "avg_confidence",
                    "confidence_accuracy"):
            assert key in result[BULL]

    def test_deterministic(self):
        rows = _make(MIN_ROWS_FOR_STATS, BULL, win=True)
        assert regime_stats(rows) == regime_stats(rows)


# ── suppression_analysis ──────────────────────────────────────────────────────

class TestSuppressionAnalysis:
    def test_structure_keys(self):
        result = suppression_analysis([])
        for key in ("bull_stats", "neutral_stats", "risk_off_stats",
                    "bull_neutral_delta", "bull_risk_off_delta",
                    "risk_off_detail"):
            assert key in result

    def test_delta_keys(self):
        result = suppression_analysis([])
        for delta_key in ("bull_neutral_delta", "bull_risk_off_delta"):
            for k in ("win_rate_delta", "return_5d_delta", "is_effective"):
                assert k in result[delta_key]

    def test_risk_off_detail_keys(self):
        result = suppression_analysis([])
        for k in ("loss_count", "gain_count", "unknown_count", "missed_gain_frac"):
            assert k in result["risk_off_detail"]

    def test_effective_when_bull_beats_risk_off(self):
        bull_rows = _make(MIN_ROWS_FOR_STATS, BULL,     win=True)
        risk_rows = _make(MIN_ROWS_FOR_STATS, RISK_OFF, win=False)
        result = suppression_analysis(bull_rows + risk_rows)
        assert result["bull_risk_off_delta"]["is_effective"] is True

    def test_ineffective_when_risk_off_beats_bull(self):
        bull_rows = _make(MIN_ROWS_FOR_STATS, BULL,     win=False)
        risk_rows = _make(MIN_ROWS_FOR_STATS, RISK_OFF, win=True)
        result = suppression_analysis(bull_rows + risk_rows)
        assert result["bull_risk_off_delta"]["is_effective"] is False

    def test_is_effective_none_when_sparse(self):
        # Only 2 rows in BULL → win_rate=None → is_effective=None
        bull_rows = _make(MIN_ROWS_FOR_STATS - 1, BULL,     win=True)
        risk_rows = _make(MIN_ROWS_FOR_STATS,     RISK_OFF, win=False)
        result = suppression_analysis(bull_rows + risk_rows)
        assert result["bull_risk_off_delta"]["is_effective"] is None

    def test_win_rate_delta_value(self):
        # BULL win_rate=100%, RISK_OFF win_rate=0%
        # delta = RISK_OFF - BULL = 0 - 100 = -100
        bull_rows = _make(MIN_ROWS_FOR_STATS, BULL,     win=True)
        risk_rows = _make(MIN_ROWS_FOR_STATS, RISK_OFF, win=False)
        result = suppression_analysis(bull_rows + risk_rows)
        assert result["bull_risk_off_delta"]["win_rate_delta"] == pytest.approx(-100.0, abs=0.1)

    def test_neutral_effectiveness(self):
        bull_rows    = _make(MIN_ROWS_FOR_STATS, BULL,    win=True)
        neutral_rows = _make(MIN_ROWS_FOR_STATS, NEUTRAL, win=False)
        result = suppression_analysis(bull_rows + neutral_rows)
        assert result["bull_neutral_delta"]["is_effective"] is True

    def test_risk_off_detail_loss_gain_count(self):
        # 3 losses and 2 gains in RISK_OFF
        losses = [_loss(RISK_OFF) for _ in range(3)]
        gains  = [_win(RISK_OFF)  for _ in range(2)]
        result = suppression_analysis(losses + gains)
        detail = result["risk_off_detail"]
        assert detail["loss_count"] == 3
        assert detail["gain_count"] == 2

    def test_missed_gain_frac_exact(self):
        # 2 gains + 3 losses → missed_gain_frac = 2/5 = 0.4
        losses = [_loss(RISK_OFF) for _ in range(3)]
        gains  = [_win(RISK_OFF)  for _ in range(2)]
        result = suppression_analysis(losses + gains)
        assert result["risk_off_detail"]["missed_gain_frac"] == pytest.approx(0.4, abs=1e-4)

    def test_missed_gain_frac_none_when_no_risk_off(self):
        result = suppression_analysis([])
        assert result["risk_off_detail"]["missed_gain_frac"] is None

    def test_unknown_count_for_none_returns(self):
        rows   = [_row(regime=RISK_OFF, return_5d=None) for _ in range(3)]
        result = suppression_analysis(rows)
        assert result["risk_off_detail"]["unknown_count"] == 3

    def test_deterministic(self):
        rows = _make(MIN_ROWS_FOR_STATS, BULL, win=True) + \
               _make(MIN_ROWS_FOR_STATS, RISK_OFF, win=False)
        assert suppression_analysis(rows) == suppression_analysis(rows)


# ── regime_transition_stats ───────────────────────────────────────────────────

class TestRegimeTransitionStats:
    def test_all_transitions_present(self):
        result = regime_transition_stats([])
        assert f"{BULL}→{NEUTRAL}"    in result
        assert f"{NEUTRAL}→{RISK_OFF}" in result
        assert f"{BULL}→{RISK_OFF}"   in result

    def test_transition_keys(self):
        result = regime_transition_stats([])
        for label in result:
            entry = result[label]
            for key in ("source_regime", "target_regime", "source_win_rate",
                        "target_win_rate", "win_rate_delta",
                        "return_5d_delta", "degradation"):
                assert key in entry

    def test_bull_to_risk_off_delta_correct(self):
        # BULL win_rate=100%, RISK_OFF win_rate=0%
        # BULL→RISK_OFF delta = 0 - 100 = -100
        bull_rows = _make(MIN_ROWS_FOR_STATS, BULL,     win=True)
        risk_rows = _make(MIN_ROWS_FOR_STATS, RISK_OFF, win=False)
        result = regime_transition_stats(bull_rows + risk_rows)
        t = result[f"{BULL}→{RISK_OFF}"]
        assert t["win_rate_delta"]  == pytest.approx(-100.0, abs=0.1)
        assert t["degradation"]     == "degrading"
        assert t["source_regime"]   == BULL
        assert t["target_regime"]   == RISK_OFF

    def test_insufficient_data_label_when_sparse(self):
        bull_rows = _make(MIN_ROWS_FOR_STATS - 1, BULL, win=True)  # → win_rate=None
        risk_rows = _make(MIN_ROWS_FOR_STATS,     RISK_OFF, win=False)
        result = regime_transition_stats(bull_rows + risk_rows)
        t = result[f"{BULL}→{RISK_OFF}"]
        assert t["degradation"] == "insufficient_data"
        assert t["win_rate_delta"] is None

    def test_stable_when_same_win_rate(self):
        both = _make(MIN_ROWS_FOR_STATS, BULL,    win=True) + \
               _make(MIN_ROWS_FOR_STATS, NEUTRAL, win=True)
        result = regime_transition_stats(both)
        t = result[f"{BULL}→{NEUTRAL}"]
        assert t["degradation"] == "stable"
        assert t["win_rate_delta"] == pytest.approx(0.0, abs=0.1)

    def test_improving_label_when_target_better(self):
        bull_rows    = _make(MIN_ROWS_FOR_STATS, BULL,    win=False)
        neutral_rows = _make(MIN_ROWS_FOR_STATS, NEUTRAL, win=True)
        result = regime_transition_stats(bull_rows + neutral_rows)
        t = result[f"{BULL}→{NEUTRAL}"]
        assert t["degradation"] == "improving"

    def test_return_5d_delta(self):
        # BULL avg_return=5.0, RISK_OFF avg_return=-5.0 → delta = -5 - 5 = -10
        bull_rows = _make(MIN_ROWS_FOR_STATS, BULL,     win=True)   # return_5d=5.0
        risk_rows = _make(MIN_ROWS_FOR_STATS, RISK_OFF, win=False)  # return_5d=-5.0
        result = regime_transition_stats(bull_rows + risk_rows)
        t = result[f"{BULL}→{RISK_OFF}"]
        assert t["return_5d_delta"] == pytest.approx(-10.0, abs=0.1)

    def test_deterministic(self):
        rows = _make(MIN_ROWS_FOR_STATS, BULL, win=True) + \
               _make(MIN_ROWS_FOR_STATS, NEUTRAL, win=False)
        assert regime_transition_stats(rows) == regime_transition_stats(rows)


# ── inversion_detection ───────────────────────────────────────────────────────

class TestInversionDetection:
    def _stats(self, wr_map: dict) -> dict:
        """Build minimal regime_stats dict from {regime: win_rate}."""
        base = {r: {"n": 0, "win_rate": None} for r in REGIME_ORDER}
        for regime, wr in wr_map.items():
            base[regime] = {"n": MIN_ROWS_FOR_STATS, "win_rate": wr}
        return base

    def test_no_inversion_correct_order(self):
        stats  = self._stats({BULL: 80.0, NEUTRAL: 60.0, RISK_OFF: 40.0})
        result = inversion_detection(stats)
        assert result["has_inversion"]   is False
        assert result["inversion_count"] == 0
        assert result["inversions"]      == []

    def test_equal_win_rates_not_inverted(self):
        stats  = self._stats({BULL: 60.0, NEUTRAL: 60.0, RISK_OFF: 60.0})
        result = inversion_detection(stats)
        assert result["has_inversion"] is False

    def test_bull_neutral_inversion(self):
        # NEUTRAL (70%) > BULL (50%) → inversion
        stats  = self._stats({BULL: 50.0, NEUTRAL: 70.0})
        result = inversion_detection(stats)
        assert result["has_inversion"]   is True
        assert result["inversion_count"] == 1
        inv = result["inversions"][0]
        assert inv["low_risk_regime"]  == BULL
        assert inv["high_risk_regime"] == NEUTRAL
        assert inv["delta"]            == pytest.approx(20.0, abs=0.1)

    def test_neutral_risk_off_inversion(self):
        stats  = self._stats({NEUTRAL: 40.0, RISK_OFF: 70.0})
        result = inversion_detection(stats)
        assert result["has_inversion"]   is True
        inv = result["inversions"][0]
        assert inv["low_risk_regime"]  == NEUTRAL
        assert inv["high_risk_regime"] == RISK_OFF

    def test_double_inversion(self):
        # All 3 pairs inverted: NEUTRAL>BULL, RISK_OFF>NEUTRAL, RISK_OFF>BULL
        stats  = self._stats({BULL: 40.0, NEUTRAL: 60.0, RISK_OFF: 80.0})
        result = inversion_detection(stats)
        assert result["inversion_count"] == 3

    def test_high_severity_above_threshold(self):
        # delta = 25 > INVERSION_HIGH_THRESHOLD (20) → HIGH
        stats  = self._stats({BULL: 40.0, NEUTRAL: 65.0})
        result = inversion_detection(stats)
        assert result["inversions"][0]["severity"] == "HIGH"

    def test_medium_severity_below_threshold(self):
        # delta = 15 ≤ INVERSION_HIGH_THRESHOLD → MEDIUM
        stats  = self._stats({BULL: 50.0, NEUTRAL: 65.0})
        result = inversion_detection(stats)
        assert result["inversions"][0]["severity"] == "MEDIUM"

    def test_sparse_regime_skipped(self):
        # NEUTRAL has None win_rate → BULL-NEUTRAL and NEUTRAL-RISK_OFF pairs skipped.
        # BULL-RISK_OFF pair is still checked (both have data).
        # BULL=50% < RISK_OFF=80% → BULL-RISK_OFF inversion fires.
        stats = {
            BULL:     {"n": MIN_ROWS_FOR_STATS, "win_rate": 50.0},
            NEUTRAL:  {"n": 2,                  "win_rate": None},  # sparse → pairs involving NEUTRAL skipped
            RISK_OFF: {"n": MIN_ROWS_FOR_STATS, "win_rate": 80.0},
        }
        result = inversion_detection(stats)
        # Only BULL-RISK_OFF fires
        assert result["inversion_count"] == 1
        assert result["inversions"][0]["low_risk_regime"]  == BULL
        assert result["inversions"][0]["high_risk_regime"] == RISK_OFF

    def test_inversion_delta_value(self):
        stats  = self._stats({BULL: 45.0, NEUTRAL: 70.0})
        result = inversion_detection(stats)
        assert result["inversions"][0]["delta"] == pytest.approx(25.0, abs=0.1)

    def test_deterministic(self):
        stats = self._stats({BULL: 60.0, NEUTRAL: 80.0, RISK_OFF: 30.0})
        assert inversion_detection(stats) == inversion_detection(stats)


# ── generate_report ────────────────────────────────────────────────────────────

class TestGenerateReport:
    def test_structure_keys(self):
        report = generate_report([])
        for key in ("row_count", "regime_stats", "suppression", "transitions",
                    "inversion", "strongest_regime", "weakest_regime", "warnings"):
            assert key in report

    def test_empty_rows(self):
        report = generate_report([])
        assert report["row_count"]         == 0
        assert report["strongest_regime"]  is None
        assert report["weakest_regime"]    is None
        assert report["inversion"]["has_inversion"] is False

    def test_row_count(self):
        rows   = _make(7, BULL, win=True)
        report = generate_report(rows)
        assert report["row_count"] == 7

    def test_strongest_weakest_populated(self):
        bull_rows = _make(MIN_ROWS_FOR_STATS, BULL,     win=True)
        risk_rows = _make(MIN_ROWS_FOR_STATS, RISK_OFF, win=False)
        report = generate_report(bull_rows + risk_rows)
        assert report["strongest_regime"]["regime"]  == BULL
        assert report["weakest_regime"]["regime"]    == RISK_OFF

    def test_inversion_generates_warning(self):
        # RISK_OFF outperforms BULL → inversion
        bull_rows = _make(MIN_ROWS_FOR_STATS, BULL,     win=False)
        risk_rows = _make(MIN_ROWS_FOR_STATS, RISK_OFF, win=True)
        report = generate_report(bull_rows + risk_rows)
        assert any("nversion" in w for w in report["warnings"])

    def test_sparse_regime_generates_warning(self):
        rows   = _make(MIN_ROWS_FOR_STATS - 1, BULL, win=True)
        report = generate_report(rows)
        assert any(BULL in w for w in report["warnings"])

    def test_ineffective_suppression_generates_warning(self):
        # RISK_OFF beats BULL → suppression is ineffective
        bull_rows = _make(MIN_ROWS_FOR_STATS, BULL,     win=False)
        risk_rows = _make(MIN_ROWS_FOR_STATS, RISK_OFF, win=True)
        report = generate_report(bull_rows + risk_rows)
        assert any("NEFFECTIVE" in w or "neffective" in w for w in report["warnings"])

    def test_warnings_is_list(self):
        report = generate_report([])
        assert isinstance(report["warnings"], list)

    def test_all_transitions_in_report(self):
        report = generate_report([])
        from market_regime import BULL, NEUTRAL, RISK_OFF
        for label in (f"{BULL}→{NEUTRAL}", f"{NEUTRAL}→{RISK_OFF}", f"{BULL}→{RISK_OFF}"):
            assert label in report["transitions"]

    def test_deterministic(self):
        rows = (
            _make(MIN_ROWS_FOR_STATS, BULL,     win=True) +
            _make(MIN_ROWS_FOR_STATS, NEUTRAL,  win=False) +
            _make(MIN_ROWS_FOR_STATS, RISK_OFF, win=False)
        )
        r1 = generate_report(rows)
        r2 = generate_report(rows)
        assert r1["row_count"]                         == r2["row_count"]
        assert r1["inversion"]                         == r2["inversion"]
        assert r1["strongest_regime"]                  == r2["strongest_regime"]
        assert r1["weakest_regime"]                    == r2["weakest_regime"]
        assert r1["warnings"]                          == r2["warnings"]
        assert r1["suppression"]["bull_risk_off_delta"] == \
               r2["suppression"]["bull_risk_off_delta"]

    def test_inversion_detection_in_report(self):
        bull_rows = _make(MIN_ROWS_FOR_STATS, BULL,     win=False)
        risk_rows = _make(MIN_ROWS_FOR_STATS, RISK_OFF, win=True)
        report = generate_report(bull_rows + risk_rows)
        assert report["inversion"]["has_inversion"] is True

    def test_transitions_show_degradation_when_ordered_correctly(self):
        # BULL wins, RISK_OFF loses → BULL→RISK_OFF should be "degrading"
        bull_rows = _make(MIN_ROWS_FOR_STATS, BULL,     win=True)
        risk_rows = _make(MIN_ROWS_FOR_STATS, RISK_OFF, win=False)
        report = generate_report(bull_rows + risk_rows)
        t = report["transitions"][f"{BULL}→{RISK_OFF}"]
        assert t["degradation"] == "degrading"


# ── REGIME_ORDER and constants ────────────────────────────────────────────────

class TestConstants:
    def test_regime_order_contains_all(self):
        assert set(REGIME_ORDER) == {BULL, NEUTRAL, RISK_OFF}

    def test_bull_first_in_order(self):
        assert REGIME_ORDER[0] == BULL

    def test_risk_off_last_in_order(self):
        assert REGIME_ORDER[-1] == RISK_OFF

    def test_degradation_threshold_positive(self):
        assert DEGRADATION_THRESHOLD > 0.0

    def test_inversion_high_threshold_above_degradation(self):
        assert INVERSION_HIGH_THRESHOLD > DEGRADATION_THRESHOLD
