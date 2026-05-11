"""
Unit tests for outcome_analytics.py.

All tests use mock row dicts — no DB access, no network calls.
"""
import pytest

from outcome_analytics import (
    MIN_ROWS_FOR_STATS,
    _active_signals,
    _avg,
    _bucket_stats,
    _confidence_bucket,
    _win_rate,
    best_worst_regimes,
    generate_report,
    is_win,
    overall_stats,
    signal_combo_ranking,
    signal_effectiveness,
    stats_by_confidence_bucket,
    stats_by_regime,
    stats_by_tier,
)


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _row(
    return_5d=None,
    max_gain_pct=None,
    return_1d=None,
    return_20d=None,
    max_drawdown_pct=None,
    confidence_pct=None,
    tier=None,
    regime=None,
    signal_summary=None,
):
    return {
        "return_5d":        return_5d,
        "max_gain_pct":     max_gain_pct,
        "return_1d":        return_1d,
        "return_20d":       return_20d,
        "max_drawdown_pct": max_drawdown_pct,
        "confidence_pct":   confidence_pct,
        "tier":             tier,
        "regime":           regime,
        "signal_summary":   signal_summary,
    }


def _make_rows(n, **kwargs):
    """Create n identical rows."""
    return [_row(**kwargs) for _ in range(n)]


# ── is_win ─────────────────────────────────────────────────────────────────────

class TestIsWin:
    def test_win_on_positive_return_5d(self):
        assert is_win(_row(return_5d=0.01)) is True

    def test_win_on_large_gain(self):
        assert is_win(_row(max_gain_pct=5.0)) is True

    def test_win_gain_above_threshold(self):
        assert is_win(_row(max_gain_pct=10.0)) is True

    def test_loss_zero_return_5d(self):
        # threshold_5d default is 0.0; equal is NOT a win
        assert is_win(_row(return_5d=0.0)) is False

    def test_loss_negative_return_5d(self):
        assert is_win(_row(return_5d=-5.0)) is False

    def test_loss_gain_below_threshold(self):
        assert is_win(_row(max_gain_pct=4.99)) is False

    def test_all_none_is_not_win(self):
        assert is_win(_row()) is False

    def test_custom_threshold_5d(self):
        assert is_win(_row(return_5d=2.0), threshold_5d=2.0) is False
        assert is_win(_row(return_5d=2.01), threshold_5d=2.0) is True

    def test_custom_threshold_gain(self):
        assert is_win(_row(max_gain_pct=3.0), threshold_gain=3.0) is True
        assert is_win(_row(max_gain_pct=2.99), threshold_gain=3.0) is False

    def test_win_when_either_condition_true(self):
        # 5d negative but gain fires
        assert is_win(_row(return_5d=-10.0, max_gain_pct=6.0)) is True
        # gain below threshold but 5d fires
        assert is_win(_row(return_5d=1.0, max_gain_pct=2.0)) is True


# ── _avg ──────────────────────────────────────────────────────────────────────

class TestAvg:
    def test_empty_list(self):
        assert _avg([]) is None

    def test_all_none(self):
        assert _avg([None, None]) is None

    def test_mixed_none(self):
        result = _avg([None, 2.0, None, 4.0])
        assert result == 3.0

    def test_single_value(self):
        assert _avg([7.5]) == 7.5

    def test_rounding(self):
        # (1/3 = 0.333...) should be rounded to 2dp
        result = _avg([0.0, 0.0, 1.0])
        assert result == 0.33

    def test_integers(self):
        assert _avg([10, 20, 30]) == 20.0


# ── _win_rate ─────────────────────────────────────────────────────────────────

class TestWinRate:
    def test_returns_none_when_sparse(self):
        rows = _make_rows(MIN_ROWS_FOR_STATS - 1, return_5d=1.0)
        assert _win_rate(rows) is None

    def test_returns_none_when_empty(self):
        assert _win_rate([]) is None

    def test_exactly_min_rows(self):
        rows = _make_rows(MIN_ROWS_FOR_STATS, return_5d=1.0)
        result = _win_rate(rows)
        assert result == 100.0

    def test_all_wins(self):
        rows = _make_rows(10, return_5d=5.0)
        assert _win_rate(rows) == 100.0

    def test_all_losses(self):
        rows = _make_rows(10, return_5d=-1.0)
        assert _win_rate(rows) == 0.0

    def test_half_wins(self):
        wins   = _make_rows(5, return_5d=2.0)
        losses = _make_rows(5, return_5d=-2.0)
        assert _win_rate(wins + losses) == 50.0

    def test_rounding(self):
        # 1/3 → 33.3%
        wins   = _make_rows(1, return_5d=1.0)
        losses = _make_rows(2, return_5d=-1.0)
        # total = 3 rows but 3 < MIN_ROWS_FOR_STATS (5) → None
        result = _win_rate(wins + losses)
        if MIN_ROWS_FOR_STATS <= 3:
            assert result == pytest.approx(33.3, abs=0.1)
        else:
            assert result is None

    def test_custom_thresholds_forwarded(self):
        rows = _make_rows(MIN_ROWS_FOR_STATS, return_5d=5.0)
        # With threshold_5d=5.0, return_5d=5.0 is NOT a win (need >5.0)
        assert _win_rate(rows, threshold_5d=5.0) == 0.0


# ── _confidence_bucket ────────────────────────────────────────────────────────

class TestConfidenceBucket:
    def test_low(self):
        assert _confidence_bucket(0.0)  == "LOW"
        assert _confidence_bucket(39.9) == "LOW"

    def test_medium(self):
        assert _confidence_bucket(40.0) == "MEDIUM"
        assert _confidence_bucket(64.9) == "MEDIUM"

    def test_high(self):
        assert _confidence_bucket(65.0)  == "HIGH"
        assert _confidence_bucket(100.0) == "HIGH"

    def test_unknown_on_none(self):
        assert _confidence_bucket(None) == "UNKNOWN"

    def test_boundary_40(self):
        # exactly 40.0 → MEDIUM (lo <= pct < hi uses 40.0 as MEDIUM lo)
        assert _confidence_bucket(40.0) == "MEDIUM"

    def test_boundary_65(self):
        assert _confidence_bucket(65.0) == "HIGH"


# ── _active_signals ───────────────────────────────────────────────────────────

class TestActiveSignals:
    def test_parses_active_signals(self):
        row = _row(signal_summary='{"options":2,"insider":0,"breakout":3}')
        result = _active_signals(row)
        assert result == frozenset({"options", "breakout"})

    def test_empty_json(self):
        assert _active_signals(_row(signal_summary="{}")) == frozenset()

    def test_none_summary(self):
        assert _active_signals(_row(signal_summary=None)) == frozenset()

    def test_invalid_json(self):
        assert _active_signals(_row(signal_summary="not-json")) == frozenset()

    def test_all_zero(self):
        row = _row(signal_summary='{"options":0,"breakout":0}')
        assert _active_signals(row) == frozenset()

    def test_all_active(self):
        row = _row(signal_summary='{"options":1,"breakout":1,"catalyst":1}')
        result = _active_signals(row)
        assert result == frozenset({"options", "breakout", "catalyst"})


# ── _bucket_stats ─────────────────────────────────────────────────────────────

class TestBucketStats:
    def test_empty_bucket(self):
        stats = _bucket_stats([])
        assert stats["n"] == 0
        assert stats["win_rate"] is None
        assert stats["avg_return_5d"] is None

    def test_keys_present(self):
        rows  = _make_rows(MIN_ROWS_FOR_STATS, return_5d=2.0, max_gain_pct=6.0)
        stats = _bucket_stats(rows)
        assert set(stats.keys()) == {
            "n", "win_rate", "avg_return_5d", "avg_return_20d",
            "avg_max_gain", "avg_max_dd",
        }

    def test_correct_values(self):
        rows = _make_rows(MIN_ROWS_FOR_STATS, return_5d=4.0, return_20d=8.0,
                          max_gain_pct=10.0, max_drawdown_pct=-3.0)
        stats = _bucket_stats(rows)
        assert stats["n"]              == MIN_ROWS_FOR_STATS
        assert stats["win_rate"]       == 100.0
        assert stats["avg_return_5d"]  == 4.0
        assert stats["avg_return_20d"] == 8.0
        assert stats["avg_max_gain"]   == 10.0
        assert stats["avg_max_dd"]     == -3.0


# ── overall_stats ─────────────────────────────────────────────────────────────

class TestOverallStats:
    def test_empty_returns_no_data(self):
        result = overall_stats([])
        assert result["n"] == 0
        assert result["status"] == "no_data"

    def test_win_count_present(self):
        wins   = _make_rows(3, return_5d=2.0)
        losses = _make_rows(MIN_ROWS_FOR_STATS, return_5d=-1.0)
        stats  = overall_stats(wins + losses)
        assert stats["win_count"] == 3

    def test_win_rate_with_enough_rows(self):
        rows  = _make_rows(MIN_ROWS_FOR_STATS, return_5d=3.0)
        stats = overall_stats(rows)
        assert stats["win_rate"] == 100.0

    def test_sparse_win_rate_none(self):
        rows  = _make_rows(MIN_ROWS_FOR_STATS - 1, return_5d=3.0)
        stats = overall_stats(rows)
        assert stats["win_rate"] is None


# ── stats_by_tier ─────────────────────────────────────────────────────────────

class TestStatsByTier:
    def test_groups_correctly(self):
        rows = (
            _make_rows(MIN_ROWS_FOR_STATS, tier="CONVICTION", return_5d=5.0) +
            _make_rows(MIN_ROWS_FOR_STATS, tier="ALERT",      return_5d=-1.0)
        )
        result = stats_by_tier(rows)
        assert "CONVICTION" in result
        assert "ALERT"      in result
        assert result["CONVICTION"]["win_rate"] == 100.0
        assert result["ALERT"]["win_rate"]      == 0.0

    def test_unknown_tier_grouped(self):
        rows   = _make_rows(MIN_ROWS_FOR_STATS, tier=None, return_5d=1.0)
        result = stats_by_tier(rows)
        assert "UNKNOWN" in result


# ── stats_by_regime ───────────────────────────────────────────────────────────

class TestStatsByRegime:
    def test_groups_by_regime(self):
        rows = (
            _make_rows(MIN_ROWS_FOR_STATS, regime="BULL",     return_5d=3.0) +
            _make_rows(MIN_ROWS_FOR_STATS, regime="RISK_OFF",  return_5d=-2.0)
        )
        result = stats_by_regime(rows)
        assert "BULL"     in result
        assert "RISK_OFF" in result

    def test_none_regime_grouped_as_unknown(self):
        rows   = _make_rows(MIN_ROWS_FOR_STATS, regime=None)
        result = stats_by_regime(rows)
        assert "UNKNOWN" in result


# ── stats_by_confidence_bucket ────────────────────────────────────────────────

class TestStatsByConfidenceBucket:
    def test_groups_into_buckets(self):
        rows = (
            _make_rows(MIN_ROWS_FOR_STATS, confidence_pct=30.0,  return_5d=1.0) +
            _make_rows(MIN_ROWS_FOR_STATS, confidence_pct=55.0,  return_5d=2.0) +
            _make_rows(MIN_ROWS_FOR_STATS, confidence_pct=80.0,  return_5d=3.0)
        )
        result = stats_by_confidence_bucket(rows)
        assert "LOW"    in result
        assert "MEDIUM" in result
        assert "HIGH"   in result

    def test_none_confidence_grouped_as_unknown(self):
        rows   = _make_rows(MIN_ROWS_FOR_STATS, confidence_pct=None)
        result = stats_by_confidence_bucket(rows)
        assert "UNKNOWN" in result


# ── signal_effectiveness ──────────────────────────────────────────────────────

class TestSignalEffectiveness:
    def test_known_signals_present(self):
        rows   = _make_rows(10, return_5d=1.0,
                            signal_summary='{"options":1}')
        result = signal_effectiveness(rows)
        for sig in ("options", "insider", "short_squeeze",
                    "catalyst", "institutional", "breakout"):
            assert sig in result

    def test_lift_structure(self):
        rows   = _make_rows(10, return_5d=1.0,
                            signal_summary='{"options":1}')
        result = signal_effectiveness(rows)
        entry  = result["options"]
        assert "active" in entry
        assert "silent" in entry
        assert "lift"   in entry

    def test_lift_none_when_too_few_rows(self):
        # Only 2 rows — both groups will be < MIN_ROWS_FOR_STATS
        rows   = [
            _row(return_5d=1.0, signal_summary='{"options":1}'),
            _row(return_5d=1.0, signal_summary='{}'),
        ]
        result = signal_effectiveness(rows)
        assert result["options"]["lift"] is None

    def test_active_vs_silent_split(self):
        active_rows = _make_rows(MIN_ROWS_FOR_STATS, return_5d=5.0,
                                 signal_summary='{"breakout":2}')
        silent_rows = _make_rows(MIN_ROWS_FOR_STATS, return_5d=-2.0,
                                 signal_summary='{}')
        result = signal_effectiveness(active_rows + silent_rows)
        assert result["breakout"]["active"]["win_rate"] == 100.0
        assert result["breakout"]["silent"]["win_rate"] == 0.0
        assert result["breakout"]["lift"]               == 100.0


# ── signal_combo_ranking ──────────────────────────────────────────────────────

class TestSignalComboRanking:
    def test_empty_rows(self):
        result = signal_combo_ranking([])
        assert result["ranked"]   == []
        assert result["strongest"] is None
        assert result["weakest"]   is None

    def test_min_n_filter(self):
        # 2 rows for a combo — with min_n=3 should be excluded
        rows   = [_row(return_5d=5.0, signal_summary='{"options":1}')] * 2
        result = signal_combo_ranking(rows, min_n=3)
        assert result["ranked"] == []

    def test_min_n_included(self):
        rows   = [_row(return_5d=5.0, signal_summary='{"options":1}')] * 2
        result = signal_combo_ranking(rows, min_n=2)
        assert len(result["ranked"]) == 1

    def test_sorted_by_win_rate_descending(self):
        good = _make_rows(MIN_ROWS_FOR_STATS, return_5d=5.0,
                          signal_summary='{"breakout":1}')
        bad  = _make_rows(MIN_ROWS_FOR_STATS, return_5d=-5.0,
                          signal_summary='{"catalyst":1}')
        result = signal_combo_ranking(good + bad)
        assert result["ranked"][0]["win_rate"]  >= result["ranked"][-1]["win_rate"]
        assert result["strongest"]["win_rate"]  == 100.0
        assert result["weakest"]["win_rate"]    == 0.0

    def test_deterministic_ordering(self):
        # Calling twice with same input should produce identical ordering
        rows = (
            _make_rows(MIN_ROWS_FOR_STATS, return_5d=5.0,
                       signal_summary='{"options":1}') +
            _make_rows(MIN_ROWS_FOR_STATS, return_5d=5.0,
                       signal_summary='{"breakout":1}')
        )
        r1 = signal_combo_ranking(rows)
        r2 = signal_combo_ranking(rows)
        assert r1["ranked"] == r2["ranked"]

    def test_tie_broken_alphabetically(self):
        # Both combos have 100% win rate; tie-break should be alphabetical
        rows_a = _make_rows(MIN_ROWS_FOR_STATS, return_5d=5.0,
                            signal_summary='{"options":1}')
        rows_b = _make_rows(MIN_ROWS_FOR_STATS, return_5d=5.0,
                            signal_summary='{"breakout":1}')
        result = signal_combo_ranking(rows_a + rows_b)
        # str(["breakout"]) < str(["options"]) alphabetically
        assert result["ranked"][0]["signals"] == ["breakout"]
        assert result["ranked"][1]["signals"] == ["options"]

    def test_combo_fields(self):
        rows   = _make_rows(MIN_ROWS_FOR_STATS, return_5d=3.0, return_20d=6.0,
                            max_gain_pct=8.0, signal_summary='{"insider":1}')
        result = signal_combo_ranking(rows, min_n=MIN_ROWS_FOR_STATS)
        entry  = result["ranked"][0]
        assert "signals"        in entry
        assert "n"              in entry
        assert "win_rate"       in entry
        assert "avg_return_5d"  in entry
        assert "avg_return_20d" in entry
        assert "avg_max_gain"   in entry


# ── best_worst_regimes ────────────────────────────────────────────────────────

class TestBestWorstRegimes:
    def test_empty_rows(self):
        result = best_worst_regimes([])
        assert result["best"]  is None
        assert result["worst"] is None

    def test_identifies_best_and_worst(self):
        bull_rows = _make_rows(MIN_ROWS_FOR_STATS, regime="BULL",    return_5d=5.0)
        bear_rows = _make_rows(MIN_ROWS_FOR_STATS, regime="RISK_OFF", return_5d=-5.0)
        result    = best_worst_regimes(bull_rows + bear_rows)
        assert result["best"]["regime"]  == "BULL"
        assert result["worst"]["regime"] == "RISK_OFF"

    def test_by_regime_key_present(self):
        rows   = _make_rows(MIN_ROWS_FOR_STATS, regime="NEUTRAL", return_5d=1.0)
        result = best_worst_regimes(rows)
        assert "NEUTRAL" in result["by_regime"]

    def test_excludes_sparse_regimes_from_ranking(self):
        # sparse regime (< MIN_ROWS_FOR_STATS) has None win_rate → excluded
        sparse = _make_rows(MIN_ROWS_FOR_STATS - 1, regime="BULL", return_5d=5.0)
        result = best_worst_regimes(sparse)
        assert result["best"]  is None
        assert result["worst"] is None

    def test_single_qualified_regime(self):
        rows   = _make_rows(MIN_ROWS_FOR_STATS, regime="BULL", return_5d=5.0)
        result = best_worst_regimes(rows)
        assert result["best"]["regime"]  == "BULL"
        assert result["worst"]["regime"] == "BULL"


# ── generate_report ───────────────────────────────────────────────────────────

class TestGenerateReport:
    def test_all_sections_present(self):
        rows   = _make_rows(MIN_ROWS_FOR_STATS, return_5d=2.0, tier="CONVICTION",
                            regime="BULL", confidence_pct=70.0,
                            signal_summary='{"options":1}')
        report = generate_report(rows)
        for key in ("row_count", "overall", "by_tier", "by_regime",
                    "by_confidence", "signal_effectiveness",
                    "combo_ranking", "regime_ranking"):
            assert key in report, f"Missing key: {key}"

    def test_row_count(self):
        rows   = _make_rows(MIN_ROWS_FOR_STATS, return_5d=1.0)
        report = generate_report(rows)
        assert report["row_count"] == MIN_ROWS_FOR_STATS

    def test_empty_rows(self):
        report = generate_report([])
        assert report["row_count"] == 0
        assert report["overall"]["status"] == "no_data"

    def test_sparse_still_returns_structure(self):
        # Below MIN_ROWS_FOR_STATS — should not crash
        rows   = _make_rows(MIN_ROWS_FOR_STATS - 1, return_5d=2.0, tier="ALERT")
        report = generate_report(rows)
        assert report["row_count"] == MIN_ROWS_FOR_STATS - 1
        assert report["overall"]["win_rate"] is None

    def test_deterministic(self):
        rows   = _make_rows(MIN_ROWS_FOR_STATS, return_5d=3.0, regime="BULL",
                            tier="CONVICTION", confidence_pct=80.0,
                            signal_summary='{"breakout":1}')
        r1 = generate_report(rows)
        r2 = generate_report(rows)
        assert r1["row_count"]                       == r2["row_count"]
        assert r1["overall"]["win_rate"]             == r2["overall"]["win_rate"]
        assert r1["combo_ranking"]["ranked"]         == r2["combo_ranking"]["ranked"]
        assert r1["regime_ranking"]["best"]          == r2["regime_ranking"]["best"]
