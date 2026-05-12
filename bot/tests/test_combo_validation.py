"""
Unit tests for combo_validation.py (Phase 2C).

All tests pass mock rows directly — no DB access, no network calls.
Rows use signal_summary JSON strings so _active_signals() parses correctly.
"""
import json
import pytest

from combo_validation import (
    ALL_SIGNALS,
    COMBO_MIXED,
    COMBO_STABLE,
    COMBO_UNKNOWN,
    COMBO_UNSTABLE,
    CONSISTENCY_STABLE_THRESHOLD,
    CONSISTENCY_UNSTABLE_THRESHOLD,
    DECEPTIVE_DRAWDOWN_THRESHOLD,
    DECEPTIVE_HIGH_CONF_THRESHOLD,
    DECEPTIVE_LOTTERY_GAIN,
    DECEPTIVE_POOR_RETURN,
    DECEPTIVE_WIN_RATE_LOW,
    MIN_COMBO_ROWS,
    RISK_ADJ_DD_WEIGHT,
    _all_combos_of_size,
    _combo_key,
    _compute_combo,
    _consistency_label,
    _extended_stats,
    _risk_adj,
    deceptive_combo_detection,
    generate_report,
    pair_combo_stats,
    rank_combos,
    triple_combo_stats,
)
from market_regime import BULL, NEUTRAL, RISK_OFF
from outcome_analytics import MIN_ROWS_FOR_STATS


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _row(
    signals=(),
    regime=None,
    return_5d=None,
    return_20d=None,
    max_gain_pct=None,
    max_drawdown_pct=None,
    confidence_pct=None,
):
    """Build a mock outcome row with signal_summary JSON."""
    summary = {s: 1 for s in signals}
    return {
        "signal_summary":   json.dumps(summary),
        "regime":           regime,
        "return_5d":        return_5d,
        "return_20d":       return_20d,
        "max_gain_pct":     max_gain_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "confidence_pct":   confidence_pct,
    }


def _win_row(signals=(), regime=BULL, conf=70.0):
    return _row(
        signals=signals,
        regime=regime,
        return_5d=5.0,
        max_gain_pct=8.0,
        max_drawdown_pct=-2.0,
        confidence_pct=conf,
    )


def _loss_row(signals=(), regime=BULL, conf=50.0):
    return _row(
        signals=signals,
        regime=regime,
        return_5d=-3.0,
        max_gain_pct=1.0,
        max_drawdown_pct=-6.0,
        confidence_pct=conf,
    )


def _make_rows(n, signals=(), regime=BULL, win=True, conf=65.0):
    """Generate n identical win or loss rows."""
    maker = _win_row if win else _loss_row
    return [maker(signals=signals, regime=regime, conf=conf) for _ in range(n)]


# ── _combo_key ────────────────────────────────────────────────────────────────

class TestComboKey:
    def test_sorted_alphabetically(self):
        assert _combo_key(["breakout", "options"]) == "breakout+options"

    def test_already_sorted(self):
        assert _combo_key(["insider", "options"]) == "insider+options"

    def test_frozenset_input(self):
        assert _combo_key(frozenset(["catalyst", "breakout"])) == "breakout+catalyst"

    def test_triple(self):
        result = _combo_key(["short_squeeze", "options", "insider"])
        assert result == "insider+options+short_squeeze"

    def test_single_signal(self):
        assert _combo_key(["options"]) == "options"

    def test_deterministic(self):
        a = _combo_key(["c", "a", "b"])
        b = _combo_key(["b", "c", "a"])
        assert a == b == "a+b+c"


# ── _risk_adj ─────────────────────────────────────────────────────────────────

class TestRiskAdj:
    def test_basic_formula(self):
        # 5.0 - 0.5 * abs(-4.0) = 5.0 - 2.0 = 3.0
        assert _risk_adj(5.0, -4.0) == 3.0

    def test_no_drawdown_data(self):
        # dd=None → penalty=0 → score = return_5d
        assert _risk_adj(3.0, None) == 3.0

    def test_none_return_gives_none(self):
        assert _risk_adj(None, -4.0) is None

    def test_both_none_gives_none(self):
        assert _risk_adj(None, None) is None

    def test_negative_return_with_drawdown(self):
        # -2.0 - 0.5 * abs(-4.0) = -2.0 - 2.0 = -4.0
        assert _risk_adj(-2.0, -4.0) == -4.0

    def test_zero_return_with_drawdown(self):
        # 0.0 - 0.5 * 4.0 = -2.0
        assert _risk_adj(0.0, -4.0) == -2.0

    def test_positive_drawdown_value_treated_as_absolute(self):
        # If somehow dd is positive, abs() still handles it
        assert _risk_adj(5.0, 4.0) == pytest.approx(3.0)

    def test_dd_weight_constant(self):
        assert RISK_ADJ_DD_WEIGHT == pytest.approx(0.5)

    def test_rounded_to_4_decimals(self):
        result = _risk_adj(1.0, -3.0)
        assert result == round(1.0 - 0.5 * 3.0, 4)


# ── _extended_stats ───────────────────────────────────────────────────────────

class TestExtendedStats:
    def test_keys_present(self):
        rows = _make_rows(5, signals=("options",))
        stats = _extended_stats(rows)
        expected_keys = {
            "n", "win_rate", "avg_return_5d", "avg_return_20d",
            "avg_max_gain", "avg_max_dd", "avg_confidence", "risk_adj_score",
        }
        assert expected_keys == set(stats.keys())

    def test_n_equals_row_count(self):
        rows = _make_rows(7, signals=("options",))
        assert _extended_stats(rows)["n"] == 7

    def test_empty_rows(self):
        stats = _extended_stats([])
        assert stats["n"] == 0
        assert stats["win_rate"] is None
        assert stats["avg_return_5d"] is None
        assert stats["risk_adj_score"] is None

    def test_win_rate_all_wins(self):
        rows = _make_rows(10, signals=("options",), win=True)
        assert _extended_stats(rows)["win_rate"] == 100.0

    def test_win_rate_all_losses(self):
        rows = _make_rows(10, signals=("options",), win=False)
        assert _extended_stats(rows)["win_rate"] == 0.0

    def test_risk_adj_score_computed(self):
        rows = _make_rows(5, signals=("options",), win=True)
        stats = _extended_stats(rows)
        # win rows: return_5d=5.0, max_drawdown_pct=-2.0
        # risk_adj = 5.0 - 0.5 * abs(-2.0) = 4.0
        assert stats["risk_adj_score"] == pytest.approx(4.0, abs=0.01)

    def test_avg_confidence_computed(self):
        rows = _make_rows(5, signals=("options",), conf=70.0)
        assert _extended_stats(rows)["avg_confidence"] == pytest.approx(70.0)


# ── _consistency_label ────────────────────────────────────────────────────────

class TestConsistencyLabel:
    def test_none_gives_unknown(self):
        assert _consistency_label(None) == COMBO_UNKNOWN

    def test_zero_spread_is_stable(self):
        assert _consistency_label(0.0) == COMBO_STABLE

    def test_exact_stable_threshold_is_stable(self):
        assert _consistency_label(CONSISTENCY_STABLE_THRESHOLD) == COMBO_STABLE

    def test_just_above_stable_is_mixed(self):
        assert _consistency_label(CONSISTENCY_STABLE_THRESHOLD + 0.01) == COMBO_MIXED

    def test_exact_unstable_threshold_is_mixed(self):
        assert _consistency_label(CONSISTENCY_UNSTABLE_THRESHOLD) == COMBO_MIXED

    def test_just_above_unstable_is_unstable(self):
        assert _consistency_label(CONSISTENCY_UNSTABLE_THRESHOLD + 0.01) == COMBO_UNSTABLE

    def test_large_spread_is_unstable(self):
        assert _consistency_label(100.0) == COMBO_UNSTABLE

    def test_constants_are_correct_values(self):
        assert CONSISTENCY_STABLE_THRESHOLD == 15.0
        assert CONSISTENCY_UNSTABLE_THRESHOLD == 30.0


# ── _all_combos_of_size ───────────────────────────────────────────────────────

class TestAllCombosOfSize:
    def test_pair_count_is_15(self):
        # C(6,2) = 15
        combos = _all_combos_of_size(2)
        assert len(combos) == 15

    def test_triple_count_is_20(self):
        # C(6,3) = 20
        combos = _all_combos_of_size(3)
        assert len(combos) == 20

    def test_all_elements_from_all_signals(self):
        all_signals_set = set(ALL_SIGNALS)
        for combo in _all_combos_of_size(2):
            assert combo.issubset(all_signals_set)

    def test_returns_frozensets(self):
        combos = _all_combos_of_size(2)
        for c in combos:
            assert isinstance(c, frozenset)

    def test_all_pairs_are_unique(self):
        combos = _all_combos_of_size(2)
        assert len(combos) == len(set(combos))

    def test_all_triples_are_unique(self):
        combos = _all_combos_of_size(3)
        assert len(combos) == len(set(combos))

    def test_pair_size_is_2(self):
        for combo in _all_combos_of_size(2):
            assert len(combo) == 2

    def test_triple_size_is_3(self):
        for combo in _all_combos_of_size(3):
            assert len(combo) == 3


# ── _compute_combo ────────────────────────────────────────────────────────────

class TestComputeCombo:
    def test_subset_matching_includes_superset_rows(self):
        """Row with options+insider+breakout matches combo {options, insider}."""
        combo = frozenset({"options", "insider"})
        rows = _make_rows(5, signals=("options", "insider", "breakout"), win=True)
        result = _compute_combo(rows, combo)
        assert result["n"] == 5

    def test_subset_matching_excludes_missing_signal(self):
        """Row with only options active does NOT match {options, insider}."""
        combo = frozenset({"options", "insider"})
        rows = _make_rows(5, signals=("options",), win=True)
        result = _compute_combo(rows, combo)
        assert result["n"] == 0

    def test_mixed_rows_only_matching_counted(self):
        combo = frozenset({"options", "insider"})
        matching = _make_rows(6, signals=("options", "insider"), win=True)
        non_matching = _make_rows(4, signals=("options",), win=True)
        result = _compute_combo(matching + non_matching, combo)
        assert result["n"] == 6

    def test_key_is_sorted_combo_string(self):
        combo = frozenset({"insider", "options"})
        result = _compute_combo([], combo)
        assert result["key"] == "insider+options"

    def test_signals_list_is_sorted(self):
        combo = frozenset({"breakout", "options", "catalyst"})
        result = _compute_combo([], combo)
        assert result["combo"] == sorted(["breakout", "options", "catalyst"])

    def test_size_field_equals_combo_length(self):
        combo = frozenset({"options", "insider", "breakout"})
        result = _compute_combo([], combo)
        assert result["size"] == 3

    def test_by_regime_has_three_regimes(self):
        combo = frozenset({"options", "insider"})
        result = _compute_combo([], combo)
        assert set(result["by_regime"].keys()) == {BULL, NEUTRAL, RISK_OFF}

    def test_per_regime_stats_correct(self):
        combo = frozenset({"options", "insider"})
        bull_rows = _make_rows(5, signals=("options", "insider"), regime=BULL, win=True)
        risk_rows = _make_rows(5, signals=("options", "insider"), regime=RISK_OFF, win=False)
        result = _compute_combo(bull_rows + risk_rows, combo)
        assert result["by_regime"][BULL]["win_rate"] == 100.0
        assert result["by_regime"][RISK_OFF]["win_rate"] == 0.0

    def test_regime_spread_computed(self):
        combo = frozenset({"options", "insider"})
        bull_rows = _make_rows(5, signals=("options", "insider"), regime=BULL, win=True)
        risk_rows = _make_rows(5, signals=("options", "insider"), regime=RISK_OFF, win=False)
        result = _compute_combo(bull_rows + risk_rows, combo)
        # spread = 100.0 - 0.0 = 100.0
        assert result["regime_spread"] == pytest.approx(100.0)

    def test_consistency_stable_when_spread_low(self):
        combo = frozenset({"options", "insider"})
        bull_rows = _make_rows(5, signals=("options", "insider"), regime=BULL, win=True)
        neutral_rows = _make_rows(5, signals=("options", "insider"), regime=NEUTRAL, win=True)
        result = _compute_combo(bull_rows + neutral_rows, combo)
        assert result["consistency"] == COMBO_STABLE

    def test_consistency_unstable_when_spread_high(self):
        combo = frozenset({"options", "insider"})
        bull_rows = _make_rows(5, signals=("options", "insider"), regime=BULL, win=True)
        risk_rows = _make_rows(5, signals=("options", "insider"), regime=RISK_OFF, win=False)
        result = _compute_combo(bull_rows + risk_rows, combo)
        assert result["consistency"] == COMBO_UNSTABLE

    def test_consistency_unknown_when_only_one_regime_valid(self):
        combo = frozenset({"options", "insider"})
        # Only BULL has enough rows for win_rate; NEUTRAL+RISK_OFF empty → win_rate None
        bull_rows = _make_rows(5, signals=("options", "insider"), regime=BULL, win=True)
        result = _compute_combo(bull_rows, combo)
        # Only one valid win_rate → spread=None → UNKNOWN
        assert result["consistency"] == COMBO_UNKNOWN

    def test_regime_spread_none_when_only_one_valid_regime(self):
        combo = frozenset({"options", "insider"})
        bull_rows = _make_rows(5, signals=("options", "insider"), regime=BULL)
        result = _compute_combo(bull_rows, combo)
        assert result["regime_spread"] is None

    def test_overall_stats_include_all_keys(self):
        combo = frozenset({"options", "insider"})
        result = _compute_combo([], combo)
        for key in ("n", "win_rate", "avg_return_5d", "avg_return_20d",
                    "avg_max_gain", "avg_max_dd", "avg_confidence", "risk_adj_score"):
            assert key in result

    def test_no_rows_gives_zero_n(self):
        combo = frozenset({"options", "insider"})
        result = _compute_combo([], combo)
        assert result["n"] == 0
        assert result["win_rate"] is None


# ── pair_combo_stats ──────────────────────────────────────────────────────────

class TestPairComboStats:
    def test_max_possible_pairs_is_15(self):
        """Total possible pairs is C(6,2)=15."""
        # Need lots of rows with all signals active to qualify all pairs
        rows = _make_rows(10, signals=ALL_SIGNALS, win=True)
        result = pair_combo_stats(rows)
        assert len(result) == 15

    def test_sparse_pairs_excluded(self):
        """Pairs with fewer than min_n rows are excluded."""
        rows = _make_rows(4, signals=("options", "insider"), win=True)
        result = pair_combo_stats(rows, min_n=5)
        assert "insider+options" not in result

    def test_qualified_pair_included(self):
        rows = _make_rows(5, signals=("options", "insider"), win=True)
        result = pair_combo_stats(rows, min_n=5)
        assert "insider+options" in result

    def test_keys_are_sorted_strings(self):
        rows = _make_rows(10, signals=ALL_SIGNALS, win=True)
        result = pair_combo_stats(rows)
        for key in result:
            parts = key.split("+")
            assert parts == sorted(parts), f"Key not sorted: {key}"

    def test_each_result_has_size_2(self):
        rows = _make_rows(10, signals=ALL_SIGNALS)
        result = pair_combo_stats(rows)
        for entry in result.values():
            assert entry["size"] == 2

    def test_returns_dict(self):
        assert isinstance(pair_combo_stats([]), dict)

    def test_empty_rows_returns_empty(self):
        assert pair_combo_stats([]) == {}

    def test_min_n_custom(self):
        rows = _make_rows(3, signals=("options", "insider"), win=True)
        result = pair_combo_stats(rows, min_n=3)
        assert "insider+options" in result

    def test_deterministic(self):
        rows = _make_rows(10, signals=ALL_SIGNALS, win=True)
        a = pair_combo_stats(rows)
        b = pair_combo_stats(rows)
        assert a == b


# ── triple_combo_stats ────────────────────────────────────────────────────────

class TestTripleComboStats:
    def test_max_possible_triples_is_20(self):
        """Total possible triples is C(6,3)=20."""
        rows = _make_rows(10, signals=ALL_SIGNALS, win=True)
        result = triple_combo_stats(rows)
        assert len(result) == 20

    def test_sparse_triples_excluded(self):
        rows = _make_rows(4, signals=("options", "insider", "breakout"), win=True)
        result = triple_combo_stats(rows, min_n=5)
        assert "breakout+insider+options" not in result

    def test_qualified_triple_included(self):
        rows = _make_rows(5, signals=("options", "insider", "breakout"), win=True)
        result = triple_combo_stats(rows, min_n=5)
        assert "breakout+insider+options" in result

    def test_each_result_has_size_3(self):
        rows = _make_rows(10, signals=ALL_SIGNALS)
        result = triple_combo_stats(rows)
        for entry in result.values():
            assert entry["size"] == 3

    def test_returns_dict(self):
        assert isinstance(triple_combo_stats([]), dict)

    def test_empty_rows_returns_empty(self):
        assert triple_combo_stats([]) == {}

    def test_deterministic(self):
        rows = _make_rows(10, signals=ALL_SIGNALS, win=True)
        a = triple_combo_stats(rows)
        b = triple_combo_stats(rows)
        assert a == b


# ── deceptive_combo_detection ─────────────────────────────────────────────────

class TestDeceptiveComboDetection:
    def _make_entry(
        self,
        key="options+insider",
        n=10,
        win_rate=70.0,
        avg_return_5d=2.0,
        avg_max_dd=-3.0,
        avg_confidence=50.0,
        avg_max_gain=3.0,
        signals=None,
    ):
        return {
            key: {
                "combo":          signals or key.split("+"),
                "key":            key,
                "size":           len(key.split("+")),
                "n":              n,
                "win_rate":       win_rate,
                "avg_return_5d":  avg_return_5d,
                "avg_max_dd":     avg_max_dd,
                "avg_confidence": avg_confidence,
                "avg_max_gain":   avg_max_gain,
                "risk_adj_score": avg_return_5d - 0.5 * abs(avg_max_dd),
                "by_regime":      {},
                "regime_spread":  None,
                "consistency":    COMBO_UNKNOWN,
            }
        }

    def test_high_conf_poor_return_detected(self):
        stats = self._make_entry(avg_confidence=70.0, avg_return_5d=-2.0)
        flags = deceptive_combo_detection(stats)
        types = [f["type"] for f in flags]
        assert "HIGH_CONF_POOR_RETURN" in types

    def test_high_conf_poor_return_severity_medium(self):
        # return_5d = -2 (>= -5), so MEDIUM
        stats = self._make_entry(avg_confidence=70.0, avg_return_5d=-2.0)
        flags = deceptive_combo_detection(stats)
        flag = next(f for f in flags if f["type"] == "HIGH_CONF_POOR_RETURN")
        assert flag["severity"] == "MEDIUM"

    def test_high_conf_poor_return_severity_high(self):
        # return_5d = -6.0 (< -5), so HIGH
        stats = self._make_entry(avg_confidence=70.0, avg_return_5d=-6.0)
        flags = deceptive_combo_detection(stats)
        flag = next(f for f in flags if f["type"] == "HIGH_CONF_POOR_RETURN")
        assert flag["severity"] == "HIGH"

    def test_high_conf_poor_return_not_triggered_when_conf_low(self):
        stats = self._make_entry(avg_confidence=60.0, avg_return_5d=-2.0)
        flags = deceptive_combo_detection(stats)
        types = [f["type"] for f in flags]
        assert "HIGH_CONF_POOR_RETURN" not in types

    def test_high_conf_poor_return_not_triggered_when_return_positive(self):
        stats = self._make_entry(avg_confidence=70.0, avg_return_5d=1.0)
        flags = deceptive_combo_detection(stats)
        types = [f["type"] for f in flags]
        assert "HIGH_CONF_POOR_RETURN" not in types

    def test_high_conf_poor_return_threshold_exact(self):
        # conf exactly at threshold, return < 0 → triggers
        stats = self._make_entry(
            avg_confidence=DECEPTIVE_HIGH_CONF_THRESHOLD,
            avg_return_5d=DECEPTIVE_POOR_RETURN - 0.1,
        )
        flags = deceptive_combo_detection(stats)
        types = [f["type"] for f in flags]
        assert "HIGH_CONF_POOR_RETURN" in types

    def test_lottery_ticket_detected(self):
        stats = self._make_entry(win_rate=30.0, avg_max_gain=10.0)
        flags = deceptive_combo_detection(stats)
        types = [f["type"] for f in flags]
        assert "LOTTERY_TICKET" in types

    def test_lottery_ticket_severity_is_medium(self):
        stats = self._make_entry(win_rate=30.0, avg_max_gain=10.0)
        flags = deceptive_combo_detection(stats)
        flag = next(f for f in flags if f["type"] == "LOTTERY_TICKET")
        assert flag["severity"] == "MEDIUM"

    def test_lottery_ticket_not_triggered_when_win_rate_high(self):
        stats = self._make_entry(win_rate=50.0, avg_max_gain=10.0)
        flags = deceptive_combo_detection(stats)
        types = [f["type"] for f in flags]
        assert "LOTTERY_TICKET" not in types

    def test_lottery_ticket_not_triggered_when_gain_low(self):
        stats = self._make_entry(win_rate=30.0, avg_max_gain=5.0)
        flags = deceptive_combo_detection(stats)
        types = [f["type"] for f in flags]
        assert "LOTTERY_TICKET" not in types

    def test_lottery_ticket_threshold_exact(self):
        # win_rate just under threshold, gain exactly at threshold → triggers
        stats = self._make_entry(
            win_rate=DECEPTIVE_WIN_RATE_LOW - 0.1,
            avg_max_gain=DECEPTIVE_LOTTERY_GAIN,
        )
        flags = deceptive_combo_detection(stats)
        types = [f["type"] for f in flags]
        assert "LOTTERY_TICKET" in types

    def test_high_drawdown_detected(self):
        stats = self._make_entry(avg_max_dd=-10.0)
        flags = deceptive_combo_detection(stats)
        types = [f["type"] for f in flags]
        assert "HIGH_DRAWDOWN" in types

    def test_high_drawdown_severity_medium(self):
        # dd=-10.0; threshold=-8; threshold*2=-16; -10 > -16 → MEDIUM
        stats = self._make_entry(avg_max_dd=-10.0)
        flags = deceptive_combo_detection(stats)
        flag = next(f for f in flags if f["type"] == "HIGH_DRAWDOWN")
        assert flag["severity"] == "MEDIUM"

    def test_high_drawdown_severity_high(self):
        # dd=-18.0; threshold*2=-16; -18 < -16 → HIGH
        stats = self._make_entry(avg_max_dd=-18.0)
        flags = deceptive_combo_detection(stats)
        flag = next(f for f in flags if f["type"] == "HIGH_DRAWDOWN")
        assert flag["severity"] == "HIGH"

    def test_high_drawdown_not_triggered_when_mild(self):
        stats = self._make_entry(avg_max_dd=-5.0)
        flags = deceptive_combo_detection(stats)
        types = [f["type"] for f in flags]
        assert "HIGH_DRAWDOWN" not in types

    def test_high_drawdown_threshold_exact(self):
        # dd exactly at threshold → NOT triggered (must be strictly less)
        stats = self._make_entry(avg_max_dd=DECEPTIVE_DRAWDOWN_THRESHOLD)
        flags = deceptive_combo_detection(stats)
        types = [f["type"] for f in flags]
        assert "HIGH_DRAWDOWN" not in types

    def test_just_below_high_drawdown_threshold_triggers(self):
        stats = self._make_entry(avg_max_dd=DECEPTIVE_DRAWDOWN_THRESHOLD - 0.01)
        flags = deceptive_combo_detection(stats)
        types = [f["type"] for f in flags]
        assert "HIGH_DRAWDOWN" in types

    def test_sparse_combo_skipped(self):
        stats = self._make_entry(
            n=MIN_COMBO_ROWS - 1,
            avg_confidence=80.0,
            avg_return_5d=-5.0,
        )
        flags = deceptive_combo_detection(stats)
        assert flags == []

    def test_sorted_high_severity_first(self):
        stats = {}
        stats["a+b"] = {
            "combo": ["a", "b"],
            "key": "a+b",
            "n": 10,
            "win_rate": 70.0,
            "avg_return_5d": -6.0,   # HIGH_CONF_POOR_RETURN HIGH
            "avg_max_dd": -3.0,
            "avg_confidence": 70.0,
            "avg_max_gain": 3.0,
            "risk_adj_score": -7.5,
            "by_regime": {},
            "regime_spread": None,
            "consistency": COMBO_UNKNOWN,
        }
        stats["c+d"] = {
            "combo": ["c", "d"],
            "key": "c+d",
            "n": 10,
            "win_rate": 30.0,
            "avg_return_5d": 2.0,
            "avg_max_dd": -3.0,
            "avg_confidence": 50.0,
            "avg_max_gain": 12.0,   # LOTTERY_TICKET MEDIUM
            "risk_adj_score": 0.5,
            "by_regime": {},
            "regime_spread": None,
            "consistency": COMBO_UNKNOWN,
        }
        flags = deceptive_combo_detection(stats)
        assert flags[0]["severity"] == "HIGH"
        assert flags[-1]["severity"] == "MEDIUM"

    def test_sorted_alphabetically_within_same_severity(self):
        stats = {}
        for k, conf, ret in [("z+z2", 70.0, -2.0), ("a+b", 70.0, -2.0)]:
            stats[k] = {
                "combo": k.split("+"),
                "key": k,
                "n": 10,
                "win_rate": 70.0,
                "avg_return_5d": ret,
                "avg_max_dd": -3.0,
                "avg_confidence": conf,
                "avg_max_gain": 3.0,
                "risk_adj_score": -2.0,
                "by_regime": {},
                "regime_spread": None,
                "consistency": COMBO_UNKNOWN,
            }
        flags = deceptive_combo_detection(stats)
        hcpr_flags = [f for f in flags if f["type"] == "HIGH_CONF_POOR_RETURN"]
        assert hcpr_flags[0]["combo"] == "a+b"
        assert hcpr_flags[1]["combo"] == "z+z2"

    def test_multiple_types_same_combo(self):
        """A combo can trigger multiple deceptive types at once."""
        stats = self._make_entry(
            avg_confidence=70.0,
            avg_return_5d=-1.0,
            win_rate=30.0,
            avg_max_gain=10.0,
            avg_max_dd=-12.0,
        )
        flags = deceptive_combo_detection(stats)
        types = {f["type"] for f in flags}
        assert "HIGH_CONF_POOR_RETURN" in types
        assert "LOTTERY_TICKET" in types
        assert "HIGH_DRAWDOWN" in types

    def test_returns_list(self):
        assert isinstance(deceptive_combo_detection({}), list)

    def test_empty_input_returns_empty(self):
        assert deceptive_combo_detection({}) == []

    def test_flag_contains_required_fields(self):
        stats = self._make_entry(avg_confidence=70.0, avg_return_5d=-2.0)
        flags = deceptive_combo_detection(stats)
        flag = flags[0]
        for field in ("combo", "signals", "n", "win_rate", "avg_return_5d",
                      "avg_max_dd", "avg_confidence", "type", "detail", "severity"):
            assert field in flag, f"Missing field: {field}"


# ── rank_combos ───────────────────────────────────────────────────────────────

class TestRankCombos:
    def _make_entry(self, key, win_rate=None, risk_adj_score=None,
                    regime_spread=None, avg_max_dd=None):
        return {
            "combo":          key.split("+"),
            "key":            key,
            "size":           len(key.split("+")),
            "n":              10,
            "win_rate":       win_rate,
            "avg_return_5d":  1.0,
            "avg_return_20d": 1.0,
            "avg_max_gain":   3.0,
            "avg_max_dd":     avg_max_dd,
            "avg_confidence": 60.0,
            "risk_adj_score": risk_adj_score,
            "by_regime":      {},
            "regime_spread":  regime_spread,
            "consistency":    COMBO_STABLE,
        }

    def _make_stats(self, specs):
        return {s[0]: self._make_entry(*s) for s in specs}

    def test_returns_six_keys(self):
        stats = self._make_stats([
            ("a+b", 80.0, 4.0, 5.0, -2.0),
        ])
        result = rank_combos(stats)
        assert set(result.keys()) == {
            "best_by_win_rate", "worst_by_win_rate",
            "best_by_risk_adj", "worst_by_risk_adj",
            "most_stable", "most_dangerous",
        }

    def test_best_win_rate_descending(self):
        stats = self._make_stats([
            ("a+b", 80.0, 4.0, 5.0, -2.0),
            ("c+d", 60.0, 3.0, 10.0, -3.0),
            ("e+f", 90.0, 5.0, 3.0, -1.0),
        ])
        result = rank_combos(stats)
        wrs = [e["win_rate"] for e in result["best_by_win_rate"]]
        assert wrs == sorted(wrs, reverse=True)

    def test_worst_win_rate_ascending(self):
        stats = self._make_stats([
            ("a+b", 80.0, 4.0, 5.0, -2.0),
            ("c+d", 60.0, 3.0, 10.0, -3.0),
            ("e+f", 90.0, 5.0, 3.0, -1.0),
        ])
        result = rank_combos(stats)
        wrs = [e["win_rate"] for e in result["worst_by_win_rate"]]
        assert wrs == sorted(wrs)

    def test_best_risk_adj_descending(self):
        stats = self._make_stats([
            ("a+b", 80.0, 4.0, 5.0, -2.0),
            ("c+d", 60.0, 2.0, 10.0, -3.0),
            ("e+f", 90.0, 6.0, 3.0, -1.0),
        ])
        result = rank_combos(stats)
        ras = [e["risk_adj_score"] for e in result["best_by_risk_adj"]]
        assert ras == sorted(ras, reverse=True)

    def test_worst_risk_adj_ascending(self):
        stats = self._make_stats([
            ("a+b", 80.0, 4.0, 5.0, -2.0),
            ("c+d", 60.0, 2.0, 10.0, -3.0),
            ("e+f", 90.0, 6.0, 3.0, -1.0),
        ])
        result = rank_combos(stats)
        ras = [e["risk_adj_score"] for e in result["worst_by_risk_adj"]]
        assert ras == sorted(ras)

    def test_most_stable_ascending_spread(self):
        stats = self._make_stats([
            ("a+b", 80.0, 4.0, 5.0, -2.0),
            ("c+d", 60.0, 3.0, 20.0, -3.0),
            ("e+f", 90.0, 5.0, 1.0, -1.0),
        ])
        result = rank_combos(stats)
        spreads = [e["regime_spread"] for e in result["most_stable"]]
        assert spreads == sorted(spreads)

    def test_most_dangerous_most_negative_dd(self):
        stats = self._make_stats([
            ("a+b", 80.0, 4.0, 5.0, -2.0),
            ("c+d", 60.0, 3.0, 10.0, -8.0),
            ("e+f", 90.0, 5.0, 3.0, -1.0),
        ])
        result = rank_combos(stats)
        dds = [e["avg_max_dd"] for e in result["most_dangerous"]]
        assert dds == sorted(dds)  # most negative first

    def test_top_n_caps_results(self):
        specs = [(f"s{i}+t{i}", float(i * 10), float(i), float(i), float(-i))
                 for i in range(1, 8)]
        stats = self._make_stats(specs)
        result = rank_combos(stats, top_n=3)
        for key in result:
            assert len(result[key]) <= 3

    def test_top_n_default_is_5(self):
        specs = [(f"s{i}+t{i}", float(i * 10), float(i), float(i), float(-i))
                 for i in range(1, 8)]
        stats = self._make_stats(specs)
        result = rank_combos(stats)
        for key in result:
            assert len(result[key]) <= 5

    def test_tie_broken_by_key(self):
        stats = {
            "a+b": self._make_entry("a+b", win_rate=80.0),
            "c+d": self._make_entry("c+d", win_rate=80.0),
        }
        result = rank_combos(stats)
        keys = [e["key"] for e in result["best_by_win_rate"]]
        assert keys == sorted(keys)  # alphabetical tie-break

    def test_none_win_rate_excluded_from_win_rate_ranking(self):
        stats = {
            "a+b": self._make_entry("a+b", win_rate=None, risk_adj_score=3.0,
                                    regime_spread=5.0, avg_max_dd=-2.0),
            "c+d": self._make_entry("c+d", win_rate=80.0, risk_adj_score=4.0,
                                    regime_spread=3.0, avg_max_dd=-1.0),
        }
        result = rank_combos(stats)
        keys_in_wr = [e["key"] for e in result["best_by_win_rate"]]
        assert "a+b" not in keys_in_wr
        assert "c+d" in keys_in_wr

    def test_none_risk_adj_excluded_from_risk_adj_ranking(self):
        stats = {
            "a+b": self._make_entry("a+b", win_rate=80.0, risk_adj_score=None,
                                    regime_spread=5.0, avg_max_dd=-2.0),
            "c+d": self._make_entry("c+d", win_rate=70.0, risk_adj_score=4.0,
                                    regime_spread=3.0, avg_max_dd=-1.0),
        }
        result = rank_combos(stats)
        keys_in_ra = [e["key"] for e in result["best_by_risk_adj"]]
        assert "a+b" not in keys_in_ra

    def test_empty_stats_returns_empty_lists(self):
        result = rank_combos({})
        for lst in result.values():
            assert lst == []

    def test_deterministic(self):
        specs = [(f"s{i}+t{i}", float(i * 10), float(i), float(i), float(-i))
                 for i in range(1, 6)]
        stats = self._make_stats(specs)
        a = rank_combos(stats)
        b = rank_combos(stats)
        assert a == b


# ── generate_report ───────────────────────────────────────────────────────────

class TestGenerateReport:
    def test_structure_keys(self):
        report = generate_report([])
        expected = {
            "row_count", "pair_count", "triple_count",
            "pair_stats", "triple_stats", "rankings",
            "deceptive_combos", "unstable_combos",
            "regime_dependent_combos", "warnings",
        }
        assert expected == set(report.keys())

    def test_rankings_subkeys(self):
        report = generate_report([])
        assert set(report["rankings"].keys()) == {
            "best_by_win_rate", "worst_by_win_rate",
            "best_by_risk_adj", "worst_by_risk_adj",
            "most_stable", "most_dangerous",
        }

    def test_row_count_matches_input(self):
        rows = _make_rows(8, signals=("options",))
        report = generate_report(rows)
        assert report["row_count"] == 8

    def test_empty_rows_zero_counts(self):
        report = generate_report([])
        assert report["pair_count"] == 0
        assert report["triple_count"] == 0
        assert report["pair_stats"] == {}
        assert report["triple_stats"] == {}

    def test_empty_rows_has_no_qualified_combos_warning(self):
        report = generate_report([])
        assert any("No combos qualified" in w for w in report["warnings"])

    def test_pair_count_matches_pair_stats_length(self):
        rows = _make_rows(10, signals=ALL_SIGNALS, win=True)
        report = generate_report(rows)
        assert report["pair_count"] == len(report["pair_stats"])

    def test_triple_count_matches_triple_stats_length(self):
        rows = _make_rows(10, signals=ALL_SIGNALS, win=True)
        report = generate_report(rows)
        assert report["triple_count"] == len(report["triple_stats"])

    def test_all_combos_qualify_when_enough_rows(self):
        rows = _make_rows(10, signals=ALL_SIGNALS, win=True)
        report = generate_report(rows)
        assert report["pair_count"] == 15
        assert report["triple_count"] == 20

    def test_unstable_combos_have_unstable_consistency(self):
        bull_rows = _make_rows(5, signals=("options", "insider"), regime=BULL, win=True)
        risk_rows = _make_rows(5, signals=("options", "insider"), regime=RISK_OFF, win=False)
        rows = bull_rows + risk_rows
        report = generate_report(rows)
        for entry in report["unstable_combos"]:
            assert entry["consistency"] == COMBO_UNSTABLE

    def test_regime_dependent_includes_mixed_and_unstable(self):
        rows = _make_rows(10, signals=ALL_SIGNALS, win=True)
        report = generate_report(rows)
        for entry in report["regime_dependent_combos"]:
            assert entry["consistency"] in (COMBO_UNSTABLE, COMBO_MIXED)

    def test_unstable_warning_message_present(self):
        bull_rows = _make_rows(5, signals=("options", "insider"), regime=BULL, win=True)
        risk_rows = _make_rows(5, signals=("options", "insider"), regime=RISK_OFF, win=False)
        rows = bull_rows + risk_rows
        report = generate_report(rows)
        spread_warnings = [w for w in report["warnings"] if "Unstable combo" in w]
        assert len(spread_warnings) >= 1

    def test_unstable_sorted_by_spread_descending(self):
        report = generate_report(_make_rows(10, signals=ALL_SIGNALS))
        spreads = [e.get("regime_spread") for e in report["unstable_combos"]
                   if e.get("regime_spread") is not None]
        assert spreads == sorted(spreads, reverse=True)

    def test_deceptive_combos_is_list(self):
        report = generate_report([])
        assert isinstance(report["deceptive_combos"], list)

    def test_warnings_is_list(self):
        report = generate_report([])
        assert isinstance(report["warnings"], list)

    def test_pair_stats_values_are_dicts(self):
        rows = _make_rows(10, signals=ALL_SIGNALS)
        report = generate_report(rows)
        for entry in report["pair_stats"].values():
            assert isinstance(entry, dict)

    def test_deterministic(self):
        rows = _make_rows(10, signals=ALL_SIGNALS, win=True)
        a = generate_report(rows)
        b = generate_report(rows)
        assert a == b


# ── Aggregation math ──────────────────────────────────────────────────────────

class TestAggregationMath:
    def test_win_rate_exact_60_percent(self):
        """6 wins out of 10 rows → 60.0%."""
        combo = frozenset({"options", "insider"})
        wins = _make_rows(6, signals=("options", "insider"), win=True)
        losses = _make_rows(4, signals=("options", "insider"), win=False)
        result = _compute_combo(wins + losses, combo)
        assert result["win_rate"] == pytest.approx(60.0, abs=0.1)

    def test_avg_return_5d_correct(self):
        """Win rows have return_5d=5.0; exact average should be 5.0."""
        combo = frozenset({"options", "insider"})
        rows = _make_rows(5, signals=("options", "insider"), win=True)
        result = _compute_combo(rows, combo)
        assert result["avg_return_5d"] == pytest.approx(5.0)

    def test_regime_spread_exact(self):
        """BULL 100% wins, NEUTRAL 60% wins (3/5) → spread = 40.0."""
        combo = frozenset({"options", "insider"})
        bull_rows = _make_rows(5, signals=("options", "insider"), regime=BULL, win=True)
        neutral_wins = _make_rows(3, signals=("options", "insider"), regime=NEUTRAL, win=True)
        neutral_losses = _make_rows(2, signals=("options", "insider"), regime=NEUTRAL, win=False)
        rows = bull_rows + neutral_wins + neutral_losses
        result = _compute_combo(rows, combo)
        assert result["regime_spread"] == pytest.approx(40.0, abs=0.2)

    def test_risk_adj_score_in_output(self):
        """Risk-adjusted score = avg_return_5d - 0.5 * |avg_max_dd|."""
        combo = frozenset({"options", "insider"})
        rows = _make_rows(5, signals=("options", "insider"), win=True)
        result = _compute_combo(rows, combo)
        # win rows: return_5d=5.0, max_drawdown_pct=-2.0
        expected = 5.0 - 0.5 * 2.0
        assert result["risk_adj_score"] == pytest.approx(expected, abs=0.01)

    def test_subset_overlap_not_double_counted(self):
        """A row with A+B+C matching combo A+B should not appear twice."""
        combo = frozenset({"options", "insider"})
        rows = _make_rows(5, signals=("options", "insider", "breakout"), win=True)
        result = _compute_combo(rows, combo)
        assert result["n"] == 5  # not 10 or some inflated count
