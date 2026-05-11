"""
Unit tests for adaptive_weights.py.

All tests pass mock row dicts directly — no DB access, no network calls.
"""
import pytest

from adaptive_weights import (
    DEFAULT_WEIGHTS,
    MAX_ADJUSTMENT,
    MAX_RAW_ADJUSTMENT,
    MIN_WEIGHT,
    SHRINKAGE_K,
    _build_reason,
    _compute_signal_adjustment,
    _shrink,
    compute_weight_adjustments,
    generate_weight_report,
)
from outcome_analytics import MIN_ROWS_FOR_STATS


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _row(return_5d=None, max_gain_pct=None, signal_summary=None):
    return {
        "return_5d":        return_5d,
        "max_gain_pct":     max_gain_pct,
        "signal_summary":   signal_summary,
        "return_20d":       None,
        "max_drawdown_pct": None,
    }


def _win_row(sig=None):
    """A winning row with optional signal active."""
    summary = f'{{"{sig}":1}}' if sig else "{}"
    return _row(return_5d=5.0, signal_summary=summary)


def _loss_row(sig=None):
    """A losing row with optional signal inactive."""
    summary = f'{{"{sig}":1}}' if sig else "{}"
    return _row(return_5d=-5.0, signal_summary=summary)


def _make_rows(n, win=True, sig=None):
    maker = _win_row if win else _loss_row
    return [maker(sig=sig) for _ in range(n)]


def _eff_entry(n_active=10, lift=50.0, win_rate=70.0, avg_return_5d=3.0):
    """Synthetic effectiveness entry for one signal."""
    return {
        "active": {
            "n":             n_active,
            "win_rate":      win_rate,
            "avg_return_5d": avg_return_5d,
        },
        "silent": {"n": 10, "win_rate": 50.0},
        "lift":   lift,
    }


# ── _shrink ───────────────────────────────────────────────────────────────────

class TestShrink:
    def test_zero_at_n_zero(self):
        assert _shrink(0) == 0.0

    def test_half_at_shrinkage_k(self):
        assert _shrink(int(SHRINKAGE_K)) == pytest.approx(0.5, abs=1e-9)

    def test_approaches_one(self):
        # n = 100 × SHRINKAGE_K → factor > 0.99
        assert _shrink(int(SHRINKAGE_K * 100)) > 0.99

    def test_always_below_one(self):
        for n in (0, 1, 10, 100, 10_000):
            assert _shrink(n) < 1.0

    def test_monotone_increasing(self):
        factors = [_shrink(n) for n in range(0, 200, 10)]
        assert factors == sorted(factors)


# ── _compute_signal_adjustment: sparse / None-lift ────────────────────────────

class TestSparseProtection:
    def test_n_active_zero_gives_zero_adj(self):
        entry = _eff_entry(n_active=0, lift=100.0)
        result = _compute_signal_adjustment("options", entry)
        assert result["adjustment"]       == 0.0
        assert result["suggested_weight"] == DEFAULT_WEIGHTS["options"]
        assert result["clamped"]          is False

    def test_n_active_below_min_gives_zero_adj(self):
        entry = _eff_entry(n_active=MIN_ROWS_FOR_STATS - 1, lift=80.0)
        result = _compute_signal_adjustment("breakout", entry)
        assert result["adjustment"]       == 0.0
        assert result["suggested_weight"] == DEFAULT_WEIGHTS["breakout"]

    def test_none_lift_gives_zero_adj(self):
        entry = {
            "active": {"n": 20, "win_rate": None, "avg_return_5d": None},
            "silent": {"n": 10, "win_rate": None},
            "lift":   None,
        }
        result = _compute_signal_adjustment("catalyst", entry)
        assert result["adjustment"]       == 0.0
        assert result["suggested_weight"] == DEFAULT_WEIGHTS["catalyst"]

    def test_reason_mentions_insufficient_data(self):
        entry = _eff_entry(n_active=2, lift=90.0)
        result = _compute_signal_adjustment("insider", entry)
        assert "insufficient" in result["reason"]


# ── Direction of adjustments ──────────────────────────────────────────────────

class TestAdjustmentDirection:
    def test_positive_lift_gives_positive_adj(self):
        entry = _eff_entry(n_active=MIN_ROWS_FOR_STATS, lift=50.0)
        result = _compute_signal_adjustment("breakout", entry)
        assert result["adjustment"] > 0.0
        assert result["suggested_weight"] > DEFAULT_WEIGHTS["breakout"]

    def test_negative_lift_gives_negative_adj(self):
        entry = _eff_entry(n_active=MIN_ROWS_FOR_STATS, lift=-50.0)
        result = _compute_signal_adjustment("breakout", entry)
        assert result["adjustment"] < 0.0
        assert result["suggested_weight"] < DEFAULT_WEIGHTS["breakout"]

    def test_zero_lift_gives_zero_adj(self):
        entry = _eff_entry(n_active=MIN_ROWS_FOR_STATS, lift=0.0)
        result = _compute_signal_adjustment("catalyst", entry)
        assert result["adjustment"] == 0.0
        assert result["suggested_weight"] == DEFAULT_WEIGHTS["catalyst"]


# ── Regression to mean ────────────────────────────────────────────────────────

class TestRegressionToMean:
    """With the same lift, larger n should produce a larger (less shrunk) adj."""

    def _adj_for(self, n_active: int, lift: float = 80.0) -> float:
        entry = _eff_entry(n_active=n_active, lift=lift)
        return _compute_signal_adjustment("breakout", entry)["adjustment"]

    def test_small_n_less_than_large_n(self):
        adj_small = self._adj_for(MIN_ROWS_FOR_STATS)       # 5 rows
        adj_large = self._adj_for(MIN_ROWS_FOR_STATS * 10)  # 50 rows
        assert adj_large > adj_small

    def test_very_small_n_close_to_zero(self):
        # n = MIN_ROWS_FOR_STATS, shrink ≈ 5/25 = 0.2
        # raw = 0.8 * 0.8 = 0.64; eff = 0.64 * 0.2 = 0.128 < MAX_ADJUSTMENT
        adj = self._adj_for(MIN_ROWS_FOR_STATS, lift=100.0)
        assert 0.0 < adj < MAX_ADJUSTMENT * 0.5  # well below the cap

    def test_huge_n_near_cap(self):
        # n = 1000, shrink ≈ 1000/1020 ≈ 0.98
        # raw = 0.8 * 1.0 = 0.8; eff ≈ 0.784 > MAX_ADJUSTMENT → clamped
        result = _compute_signal_adjustment(
            "breakout", _eff_entry(n_active=1000, lift=100.0)
        )
        assert result["adjustment"] == MAX_ADJUSTMENT
        assert result["clamped"]    is True


# ── Clamp enforcement ─────────────────────────────────────────────────────────

class TestClampEnforcement:
    def test_adj_never_exceeds_max_adjustment(self):
        # extreme lift, huge sample
        entry = _eff_entry(n_active=10_000, lift=100.0)
        for sig in DEFAULT_WEIGHTS:
            result = _compute_signal_adjustment(sig, entry)
            assert result["adjustment"] <= MAX_ADJUSTMENT
            assert result["adjustment"] >= -MAX_ADJUSTMENT

    def test_adj_never_below_negative_max(self):
        entry = _eff_entry(n_active=10_000, lift=-100.0)
        for sig in DEFAULT_WEIGHTS:
            result = _compute_signal_adjustment(sig, entry)
            assert result["adjustment"] >= -MAX_ADJUSTMENT

    def test_suggested_never_exceeds_default_plus_max(self):
        entry = _eff_entry(n_active=10_000, lift=100.0)
        for sig, default in DEFAULT_WEIGHTS.items():
            result = _compute_signal_adjustment(sig, entry)
            assert result["suggested_weight"] <= default + MAX_ADJUSTMENT + 1e-9

    def test_suggested_never_below_min_weight(self):
        entry = _eff_entry(n_active=10_000, lift=-100.0)
        for sig in DEFAULT_WEIGHTS:
            result = _compute_signal_adjustment(sig, entry)
            assert result["suggested_weight"] >= MIN_WEIGHT

    def test_clamped_flag_true_when_large_n_extreme_lift(self):
        # n=1000, lift=100 → effective_adj > MAX_ADJUSTMENT → clamped
        entry = _eff_entry(n_active=1000, lift=100.0)
        result = _compute_signal_adjustment("options", entry)
        assert result["clamped"] is True

    def test_clamped_flag_false_when_small_n_moderate_lift(self):
        # n=MIN_ROWS_FOR_STATS, lift=50 → effective_adj well within range
        entry = _eff_entry(n_active=MIN_ROWS_FOR_STATS, lift=50.0)
        result = _compute_signal_adjustment("insider", entry)
        assert result["clamped"] is False


# ── Exact value checks ────────────────────────────────────────────────────────

class TestExactValues:
    """Trace the formula to verify numerical correctness."""

    def test_known_adj_options(self):
        # lift=50, n_active=20 (= SHRINKAGE_K → shrink=0.5)
        # raw   = (50/100) × 0.8 = 0.4
        # eff   = 0.4 × 0.5 = 0.2
        # clamp → 0.2 (no clamp)
        # round to 3 dp → 0.2
        entry = _eff_entry(n_active=int(SHRINKAGE_K), lift=50.0)
        result = _compute_signal_adjustment("options", entry)
        assert result["adjustment"] == pytest.approx(0.2, abs=1e-6)
        assert result["suggested_weight"] == pytest.approx(
            DEFAULT_WEIGHTS["options"] + 0.2, abs=1e-6
        )

    def test_known_adj_negative(self):
        # lift=-50, n_active=SHRINKAGE_K → adj = -0.2
        entry = _eff_entry(n_active=int(SHRINKAGE_K), lift=-50.0)
        result = _compute_signal_adjustment("breakout", entry)
        assert result["adjustment"] == pytest.approx(-0.2, abs=1e-6)


# ── compute_weight_adjustments ────────────────────────────────────────────────

class TestComputeWeightAdjustments:
    def test_all_signals_present(self):
        rows   = []  # empty → all sparse → all default
        result = compute_weight_adjustments(rows)
        assert set(result.keys()) == set(DEFAULT_WEIGHTS.keys())

    def test_empty_rows_returns_defaults(self):
        result = compute_weight_adjustments([])
        for sig, default in DEFAULT_WEIGHTS.items():
            assert result[sig]["adjustment"]       == 0.0
            assert result[sig]["suggested_weight"] == default

    def test_result_keys_per_signal(self):
        rows   = _make_rows(MIN_ROWS_FOR_STATS, win=True, sig="breakout")
        result = compute_weight_adjustments(rows)
        for entry in result.values():
            for key in ("default_weight", "adjustment", "suggested_weight",
                        "n_active", "win_rate_active", "lift",
                        "avg_return_5d", "reason", "clamped"):
                assert key in entry, f"missing key: {key}"

    def test_signal_with_all_wins_boosted(self):
        # All rows have "options" active and win
        wins  = _make_rows(MIN_ROWS_FOR_STATS, win=True,  sig="options")
        # Equal number with "options" inactive and lose
        losses = _make_rows(MIN_ROWS_FOR_STATS, win=False, sig=None)
        result = compute_weight_adjustments(wins + losses)
        assert result["options"]["adjustment"] > 0.0

    def test_signal_with_all_losses_penalized(self):
        # Active rows all lose; silent rows all win
        actives = _make_rows(MIN_ROWS_FOR_STATS, win=False, sig="catalyst")
        silents = _make_rows(MIN_ROWS_FOR_STATS, win=True,  sig=None)
        result  = compute_weight_adjustments(actives + silents)
        assert result["catalyst"]["adjustment"] < 0.0


# ── Determinism ───────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_rows_same_adjustments(self):
        rows = (
            _make_rows(MIN_ROWS_FOR_STATS, win=True,  sig="breakout") +
            _make_rows(MIN_ROWS_FOR_STATS, win=False, sig=None)
        )
        r1 = compute_weight_adjustments(rows)
        r2 = compute_weight_adjustments(rows)
        for sig in DEFAULT_WEIGHTS:
            assert r1[sig]["adjustment"]       == r2[sig]["adjustment"]
            assert r1[sig]["suggested_weight"] == r2[sig]["suggested_weight"]

    def test_generate_report_deterministic(self):
        rows = (
            _make_rows(MIN_ROWS_FOR_STATS, win=True,  sig="insider") +
            _make_rows(MIN_ROWS_FOR_STATS, win=False, sig=None)
        )
        r1 = generate_weight_report(rows)
        r2 = generate_weight_report(rows)
        assert r1["row_count"]                           == r2["row_count"]
        assert r1["summary"]["total_suggested_weight"]   == r2["summary"]["total_suggested_weight"]
        for sig in DEFAULT_WEIGHTS:
            assert (r1["adjustments"][sig]["adjustment"] ==
                    r2["adjustments"][sig]["adjustment"])


# ── generate_weight_report ────────────────────────────────────────────────────

class TestGenerateWeightReport:
    def test_structure(self):
        report = generate_weight_report([])
        assert "row_count"   in report
        assert "adjustments" in report
        assert "summary"     in report
        for key in ("signals_boosted", "signals_penalized", "signals_held",
                    "total_default_weight", "total_suggested_weight"):
            assert key in report["summary"]

    def test_row_count(self):
        rows   = _make_rows(7, win=True, sig="breakout")
        report = generate_weight_report(rows)
        assert report["row_count"] == 7

    def test_empty_rows_all_held(self):
        report = generate_weight_report([])
        assert len(report["summary"]["signals_boosted"])   == 0
        assert len(report["summary"]["signals_penalized"]) == 0
        assert len(report["summary"]["signals_held"])      == len(DEFAULT_WEIGHTS)

    def test_total_default_weight_matches_defaults(self):
        report = generate_weight_report([])
        expected = round(sum(DEFAULT_WEIGHTS.values()), 3)
        assert report["summary"]["total_default_weight"] == expected

    def test_boosted_signal_appears_in_boosted_list(self):
        # options active + winning → should be boosted
        actives = _make_rows(MIN_ROWS_FOR_STATS, win=True,  sig="options")
        silents = _make_rows(MIN_ROWS_FOR_STATS, win=False, sig=None)
        report  = generate_weight_report(actives + silents)
        boosted_sigs = {e["signal"] for e in report["summary"]["signals_boosted"]}
        assert "options" in boosted_sigs

    def test_penalized_signal_appears_in_penalized_list(self):
        # catalyst active + losing → penalized
        actives = _make_rows(MIN_ROWS_FOR_STATS, win=False, sig="catalyst")
        silents = _make_rows(MIN_ROWS_FOR_STATS, win=True,  sig=None)
        report  = generate_weight_report(actives + silents)
        penalized_sigs = {e["signal"] for e in report["summary"]["signals_penalized"]}
        assert "catalyst" in penalized_sigs

    def test_held_signals_have_zero_adjustment(self):
        report = generate_weight_report([])
        for entry in report["summary"]["signals_held"]:
            assert entry["adjustment"] == 0.0

    def test_boosted_signals_have_positive_adjustment(self):
        actives = _make_rows(MIN_ROWS_FOR_STATS, win=True,  sig="breakout")
        silents = _make_rows(MIN_ROWS_FOR_STATS, win=False, sig=None)
        report  = generate_weight_report(actives + silents)
        for entry in report["summary"]["signals_boosted"]:
            assert entry["adjustment"] > 0.0

    def test_penalized_signals_have_negative_adjustment(self):
        actives = _make_rows(MIN_ROWS_FOR_STATS, win=False, sig="insider")
        silents = _make_rows(MIN_ROWS_FOR_STATS, win=True,  sig=None)
        report  = generate_weight_report(actives + silents)
        for entry in report["summary"]["signals_penalized"]:
            assert entry["adjustment"] < 0.0

    def test_total_suggested_changes_when_signal_boosted(self):
        actives = _make_rows(MIN_ROWS_FOR_STATS, win=True,  sig="breakout")
        silents = _make_rows(MIN_ROWS_FOR_STATS, win=False, sig=None)
        report  = generate_weight_report(actives + silents)
        assert (report["summary"]["total_suggested_weight"] !=
                report["summary"]["total_default_weight"])

    def test_sparse_report_all_default_weights(self):
        rows   = _make_rows(MIN_ROWS_FOR_STATS - 1, win=True, sig="options")
        report = generate_weight_report(rows)
        for sig, entry in report["adjustments"].items():
            assert entry["adjustment"]       == 0.0
            assert entry["suggested_weight"] == DEFAULT_WEIGHTS[sig]

    def test_summary_items_have_required_keys(self):
        actives = _make_rows(MIN_ROWS_FOR_STATS, win=True, sig="breakout")
        silents = _make_rows(MIN_ROWS_FOR_STATS, win=False, sig=None)
        report  = generate_weight_report(actives + silents)
        for category in ("signals_boosted", "signals_penalized", "signals_held"):
            for item in report["summary"][category]:
                for key in ("signal", "adjustment", "suggested_weight", "reason"):
                    assert key in item, f"{category} item missing key: {key}"


# ── Reason strings ────────────────────────────────────────────────────────────

class TestReasonStrings:
    def test_sparse_reason_string(self):
        reason = _build_reason("options", 0, None, None, 0.0, False)
        assert "insufficient" in reason.lower()

    def test_boost_reason_contains_lift(self):
        reason = _build_reason("options", 20, 50.0, 3.0, 0.2, False)
        assert "lift=" in reason
        assert "boost" in reason

    def test_penalty_reason_contains_lift(self):
        reason = _build_reason("catalyst", 20, -40.0, -1.5, -0.15, False)
        assert "lift=" in reason
        assert "penalty" in reason

    def test_clamped_flag_appears_in_reason(self):
        reason = _build_reason("breakout", 100, 90.0, 5.0, 0.5, True)
        assert "clamped" in reason

    def test_reason_has_n_active(self):
        reason = _build_reason("insider", 25, 30.0, 1.0, 0.1, False)
        assert "n_active=25" in reason

    def test_reason_has_shrinkage(self):
        reason = _build_reason("institutional", 20, 20.0, 0.5, 0.05, False)
        assert "shrinkage=" in reason


# ── DEFAULT_WEIGHTS sanity ────────────────────────────────────────────────────

class TestDefaultWeights:
    def test_all_signals_present(self):
        for sig in ("options", "insider", "short_squeeze",
                    "catalyst", "institutional", "breakout"):
            assert sig in DEFAULT_WEIGHTS

    def test_all_positive(self):
        assert all(w > 0 for w in DEFAULT_WEIGHTS.values())

    def test_institutional_lowest(self):
        assert DEFAULT_WEIGHTS["institutional"] == min(DEFAULT_WEIGHTS.values())

    def test_options_highest(self):
        assert DEFAULT_WEIGHTS["options"] == max(DEFAULT_WEIGHTS.values())
