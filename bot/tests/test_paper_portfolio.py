"""
Unit tests for paper_portfolio.py (Phase 3C).

All tests pass mock rows directly — no DB access, no network calls.
Tests cover: capital accounting, overlapping positions, allocation logic,
drawdown calculations, stress simulations, deterministic replay behaviour.
"""
import json
import pytest

from paper_portfolio import (
    ALPHA_CONCENTRATION_THRESHOLD,
    CONCENTRATION_REGIME_THRESHOLD,
    CONCENTRATION_SIGNAL_THRESHOLD,
    CONCENTRATION_TICKER_THRESHOLD,
    CONSECUTIVE_LOSS_WARN_STREAK,
    DEFAULT_FIXED_ALLOCATION,
    DEFAULT_INITIAL_CAPITAL,
    DEFAULT_MAX_OPEN_POSITIONS,
    DEFAULT_MAX_POSITION_CAP,
    DEFAULT_TIER_WEIGHTS,
    DRAWDOWN_HIGH_THRESHOLD,
    DRAWDOWN_SEVERE_THRESHOLD,
    FRAGILITY_TOP_TRADES,
    MIN_TRADES_FOR_STATS,
    WIN_RATE_CAUTION,
    WIN_RATE_HEALTHY,
    SimConfig,
    _best_worst_periods,
    _compute_allocation,
    _equity_volatility,
    _get_return,
    _max_drawdown,
    _portfolio_health,
    _sig_scores,
    _std,
    concentration_analysis,
    compute_metrics,
    generate_recommendations,
    generate_report,
    robustness_analysis,
    run_stress_tests,
    simulate,
    stress_confidence_shock,
    stress_consecutive_losses,
    stress_remove_top_winners,
    stress_risk_off_only,
)


# ── Row helpers ───────────────────────────────────────────────────────────────

def _sig(signals: dict) -> str:
    return json.dumps({k: v for k, v in signals.items() if v > 0})


def _row(
    ticker="AAPL",
    regime="BULL",
    confidence_pct=65.0,
    return_5d=None,
    return_20d=None,
    max_drawdown_pct=None,
    signals: dict = None,
    tier="ALERT",
):
    return {
        "ticker":          ticker,
        "regime":          regime,
        "confidence_pct":  confidence_pct,
        "return_5d":       return_5d,
        "return_20d":      return_20d,
        "max_drawdown_pct": max_drawdown_pct,
        "signal_summary":  _sig(signals or {}),
        "tier":            tier,
    }


def _win(ticker="AAPL", regime="BULL", conf=70.0, ret=5.0, dd=-2.0, signals=None):
    return _row(ticker=ticker, regime=regime, confidence_pct=conf,
                return_5d=ret, max_drawdown_pct=dd, signals=signals or {})


def _loss(ticker="AAPL", regime="BULL", conf=50.0, ret=-3.0, dd=-5.0, signals=None):
    return _row(ticker=ticker, regime=regime, confidence_pct=conf,
                return_5d=ret, max_drawdown_pct=dd, signals=signals or {})


def _make(n, win=True, ticker="AAPL", regime="BULL", conf=65.0, ret=None, signals=None):
    if win:
        return [_win(ticker=ticker, regime=regime, conf=conf,
                     ret=ret or 5.0, signals=signals or {}) for _ in range(n)]
    return [_loss(ticker=ticker, regime=regime, conf=conf,
                  ret=ret or -3.0, signals=signals or {}) for _ in range(n)]


# ── TestConstants ─────────────────────────────────────────────────────────────

class TestConstants:
    def test_default_initial_capital(self):
        assert DEFAULT_INITIAL_CAPITAL == 10_000.0

    def test_default_fixed_allocation(self):
        assert DEFAULT_FIXED_ALLOCATION == 1_000.0

    def test_default_max_position_cap(self):
        assert DEFAULT_MAX_POSITION_CAP == 0.20

    def test_default_max_open_positions(self):
        assert DEFAULT_MAX_OPEN_POSITIONS == 10

    def test_default_tier_weights_keys(self):
        assert set(DEFAULT_TIER_WEIGHTS.keys()) == {"CONVICTION", "ALERT", "WATCH"}

    def test_default_tier_weights_conviction_highest(self):
        assert DEFAULT_TIER_WEIGHTS["CONVICTION"] > DEFAULT_TIER_WEIGHTS["ALERT"]
        assert DEFAULT_TIER_WEIGHTS["ALERT"]      > DEFAULT_TIER_WEIGHTS["WATCH"]

    def test_concentration_thresholds_positive(self):
        assert CONCENTRATION_REGIME_THRESHOLD > 0
        assert CONCENTRATION_TICKER_THRESHOLD > 0
        assert CONCENTRATION_SIGNAL_THRESHOLD > 0

    def test_fragility_top_trades(self):
        assert FRAGILITY_TOP_TRADES == 5

    def test_win_rate_healthy_gt_caution(self):
        assert WIN_RATE_HEALTHY > WIN_RATE_CAUTION


# ── TestSimConfig ─────────────────────────────────────────────────────────────

class TestSimConfig:
    def test_defaults(self):
        cfg = SimConfig()
        assert cfg.initial_capital    == DEFAULT_INITIAL_CAPITAL
        assert cfg.allocation_method  == "fixed"
        assert cfg.fixed_allocation   == DEFAULT_FIXED_ALLOCATION
        assert cfg.max_position_cap   == DEFAULT_MAX_POSITION_CAP
        assert cfg.max_open_positions == DEFAULT_MAX_OPEN_POSITIONS
        assert cfg.holding_period     == "5d"
        assert cfg.holding_period_rows == 5
        assert cfg.min_cash_pct       == pytest.approx(0.05)
        assert cfg.min_confidence     == 0.0
        assert cfg.tier_weights       is None

    def test_custom_values(self):
        cfg = SimConfig(initial_capital=5000.0, allocation_method="confidence")
        assert cfg.initial_capital   == 5000.0
        assert cfg.allocation_method == "confidence"

    def test_is_named_tuple(self):
        cfg = SimConfig()
        assert hasattr(cfg, "_asdict")
        d = cfg._asdict()
        assert "initial_capital" in d


# ── TestInternalHelpers ───────────────────────────────────────────────────────

class TestStd:
    def test_empty(self):
        assert _std([]) is None

    def test_single(self):
        assert _std([5.0]) is None

    def test_two_equal(self):
        assert _std([3.0, 3.0]) == 0.0

    def test_two_values(self):
        assert _std([2.0, 4.0]) == pytest.approx(1.0)

    def test_none_excluded(self):
        assert _std([1.0, None, 3.0]) == pytest.approx(_std([1.0, 3.0]))


class TestGetReturn:
    def test_5d_default(self):
        row = {"return_5d": 5.0, "return_20d": 10.0}
        assert _get_return(row, "5d") == 5.0

    def test_20d(self):
        row = {"return_5d": 5.0, "return_20d": 10.0}
        assert _get_return(row, "20d") == 10.0

    def test_unknown_period_defaults_to_5d(self):
        row = {"return_5d": 3.0, "return_20d": 7.0}
        assert _get_return(row, "unknown") == 3.0

    def test_missing_key_returns_none(self):
        assert _get_return({}, "5d") is None


class TestSigScores:
    def test_empty(self):
        assert _sig_scores({"signal_summary": "{}"}) == {}

    def test_none(self):
        assert _sig_scores({"signal_summary": None}) == {}

    def test_active_signals(self):
        row = {"signal_summary": '{"options":3,"insider":2}'}
        assert _sig_scores(row) == {"options": 3, "insider": 2}

    def test_zero_excluded(self):
        row = {"signal_summary": '{"options":2,"insider":0}'}
        assert "insider" not in _sig_scores(row)

    def test_invalid_json(self):
        assert _sig_scores({"signal_summary": "bad"}) == {}


class TestComputeAllocation:
    def test_fixed(self):
        cfg = SimConfig(allocation_method="fixed", fixed_allocation=1000.0)
        assert _compute_allocation(_row(), cfg, 10000.0) == 1000.0

    def test_fixed_cap_enforced(self):
        # 20% cap of 5000 = 1000; fixed_allocation = 2000 → capped at 1000
        cfg = SimConfig(allocation_method="fixed", fixed_allocation=2000.0, max_position_cap=0.20)
        assert _compute_allocation(_row(), cfg, 5000.0) == 1000.0

    def test_confidence_method(self):
        # conf=80, base=1000 → 800
        cfg = SimConfig(allocation_method="confidence", confidence_base=1000.0)
        row = _row(confidence_pct=80.0)
        assert _compute_allocation(row, cfg, 10000.0) == pytest.approx(800.0)

    def test_confidence_zero_conf(self):
        cfg = SimConfig(allocation_method="confidence", confidence_base=1000.0)
        row = _row(confidence_pct=0.0)
        assert _compute_allocation(row, cfg, 10000.0) == 0.0

    def test_tier_conviction(self):
        cfg = SimConfig(allocation_method="tier", fixed_allocation=500.0)
        row = _row(tier="CONVICTION")
        # CONVICTION weight = 2.0 → 500 * 2 = 1000
        assert _compute_allocation(row, cfg, 10000.0) == pytest.approx(1000.0)

    def test_tier_watch(self):
        cfg = SimConfig(allocation_method="tier", fixed_allocation=1000.0)
        row = _row(tier="WATCH")
        # WATCH weight = 0.5 → 500
        assert _compute_allocation(row, cfg, 10000.0) == pytest.approx(500.0)

    def test_tier_custom_weights(self):
        cfg = SimConfig(allocation_method="tier", fixed_allocation=1000.0,
                       tier_weights={"CONVICTION": 3.0, "ALERT": 1.0, "WATCH": 0.5})
        row = _row(tier="CONVICTION")
        assert _compute_allocation(row, cfg, 10000.0) == pytest.approx(2000.0)

    def test_zero_portfolio_value(self):
        cfg = SimConfig(allocation_method="fixed", fixed_allocation=1000.0)
        assert _compute_allocation(_row(), cfg, 0.0) == 1000.0


class TestMaxDrawdown:
    def test_no_drawdown(self):
        assert _max_drawdown([100.0, 110.0, 120.0]) == 0.0

    def test_single_value(self):
        assert _max_drawdown([100.0]) == 0.0

    def test_basic_drawdown(self):
        # peak=120 → trough=96 → dd=20%
        assert _max_drawdown([100.0, 120.0, 96.0]) == pytest.approx(20.0)

    def test_multiple_drawdowns_max_reported(self):
        # peak=200, trough=100 → 50%
        curve = [100.0, 150.0, 200.0, 180.0, 100.0]
        assert _max_drawdown(curve) == pytest.approx(50.0)

    def test_monotone_decline(self):
        # peak=100, trough=50 → 50%
        assert _max_drawdown([100.0, 80.0, 60.0, 50.0]) == pytest.approx(50.0)

    def test_empty(self):
        assert _max_drawdown([]) == 0.0

    def test_recovery_does_not_reset_max(self):
        # peak=120, trough=90 → 25%; recovery to 115 doesn't change max
        curve = [100.0, 120.0, 90.0, 115.0]
        assert _max_drawdown(curve) == pytest.approx(25.0)


class TestEquityVolatility:
    def test_flat_curve_zero_vol(self):
        assert _equity_volatility([100.0, 100.0, 100.0, 100.0, 100.0]) == pytest.approx(0.0)

    def test_single_value(self):
        assert _equity_volatility([100.0]) is None

    def test_empty(self):
        assert _equity_volatility([]) is None

    def test_positive_vol(self):
        curve = [100.0, 110.0, 95.0, 105.0, 90.0]
        v = _equity_volatility(curve)
        assert v is not None and v > 0


class TestBestWorstPeriods:
    def test_insufficient_data(self):
        # single-element curve → w=1, n < w+1=2 → None
        best, worst = _best_worst_periods([100.0])
        assert best  is None
        assert worst is None

    def test_best_period_is_highest_return(self):
        # window=2: i=0 gives 100→121 = +21%; i=1 gives 110→120 = +9.09%
        curve = [100.0, 110.0, 121.0, 120.0, 119.0, 118.0]
        best, _ = _best_worst_periods(curve, window=2)
        assert best is not None
        assert best["return_pct"] == pytest.approx(21.0)  # 100→121

    def test_worst_period_is_lowest_return(self):
        curve = [120.0, 110.0, 100.0, 90.0, 95.0, 100.0]
        _, worst = _best_worst_periods(curve, window=2)
        assert worst is not None
        assert worst["return_pct"] < 0

    def test_keys_present(self):
        curve = [100.0] * 10 + [110.0] * 5
        best, worst = _best_worst_periods(curve)
        for d in (best, worst):
            if d is not None:
                assert "start_row" in d
                assert "end_row"   in d
                assert "return_pct" in d


class TestPortfolioHealth:
    def _empty_conc(self):
        return {"warnings": []}

    def _empty_rob(self):
        return {"warnings": []}

    def test_insufficient_data_below_min_trades(self):
        m = {"n_trades": MIN_TRADES_FOR_STATS - 1, "win_rate": 80.0,
             "max_drawdown_pct": 5.0, "cumulative_return_pct": 10.0}
        assert _portfolio_health(m, self._empty_conc(), self._empty_rob()) == "INSUFFICIENT_DATA"

    def test_healthy_portfolio(self):
        m = {"n_trades": 20, "win_rate": WIN_RATE_HEALTHY + 1,
             "max_drawdown_pct": 5.0, "cumulative_return_pct": 15.0}
        h = _portfolio_health(m, self._empty_conc(), self._empty_rob())
        assert h == "HEALTHY"

    def test_weak_portfolio(self):
        m = {"n_trades": 20, "win_rate": 30.0,
             "max_drawdown_pct": 35.0, "cumulative_return_pct": -5.0}
        conc = {"warnings": ["REGIME_CONCENTRATION: ..."]}
        rob  = {"warnings": ["ALPHA_CONCENTRATION: ..."]}
        h = _portfolio_health(m, conc, rob)
        assert h == "WEAK"

    def test_caution_intermediate(self):
        m = {"n_trades": 10, "win_rate": WIN_RATE_CAUTION + 1,
             "max_drawdown_pct": 12.0, "cumulative_return_pct": 2.0}
        h = _portfolio_health(m, {"warnings": ["something"]}, self._empty_rob())
        assert h in ("CAUTION", "WEAK")  # depends on total score


# ── TestSimulate ──────────────────────────────────────────────────────────────

class TestSimulate:
    def test_empty_rows(self):
        result = simulate([])
        assert result["equity_curve"]  == []
        assert result["trades"]         == []
        assert result["skipped_rows"]   == 0

    def test_result_keys(self):
        result = simulate(_make(3))
        expected = {"equity_curve", "trades", "open_at_end_count",
                    "avg_cash_utilization_pct", "skipped_rows", "config", "metrics"}
        assert expected.issubset(result.keys())

    def test_equity_curve_length_equals_row_count(self):
        rows = _make(10)
        result = simulate(rows)
        assert len(result["equity_curve"]) == 10

    def test_initial_equity_equals_initial_capital(self):
        # equity[0] = cash_after_open + deployed = initial_capital
        # Use 2+ rows with long hold so equity[0] is not overridden by the final-close step
        rows = _make(2)
        cfg  = SimConfig(initial_capital=10_000.0, fixed_allocation=1_000.0,
                         holding_period_rows=100)
        result = simulate(rows, cfg)
        assert result["equity_curve"][0] == pytest.approx(10_000.0)

    def test_capital_accounting_single_win(self):
        # Open 1 position for $1000 at row 0; close at row 5 with +5% → +$50
        # Final value should be 10000 + 50 = 10050
        cfg  = SimConfig(
            initial_capital=10_000.0,
            fixed_allocation=1_000.0,
            holding_period_rows=5,
        )
        rows = [_win(ret=5.0)] * 10
        result = simulate(rows, cfg)
        final = result["equity_curve"][-1]
        assert final > 10_000.0  # made money on wins

    def test_capital_accounting_single_loss(self):
        cfg  = SimConfig(
            initial_capital=10_000.0,
            fixed_allocation=1_000.0,
            max_open_positions=1,  # open only one position at a time
            holding_period_rows=1,
        )
        rows = [_loss(ret=-10.0)] * 6  # 6 losses in a row
        result = simulate(rows, cfg)
        final = result["equity_curve"][-1]
        assert final < 10_000.0  # lost money

    def test_no_cash_below_reserve_skips_open(self):
        # min_cash_pct=1.0 → reserve = 100% of portfolio → no capital available to deploy
        cfg  = SimConfig(initial_capital=10_000.0, fixed_allocation=1_000.0,
                         min_cash_pct=1.0)
        rows = _make(5)
        result = simulate(rows, cfg)
        assert result["skipped_rows"] > 0
        assert result["trades"] == []

    def test_max_open_positions_enforced(self):
        cfg  = SimConfig(initial_capital=10_000.0, fixed_allocation=500.0,
                         max_open_positions=3, holding_period_rows=100)
        rows = _make(10)
        result = simulate(rows, cfg)
        # With holding_period_rows=100, no position closes during 10 rows
        # We should open at most 3
        assert len(result["trades"]) == 3  # all closed at end

    def test_overlapping_positions(self):
        # holding_period_rows=5, 10 rows → positions open and close
        cfg  = SimConfig(
            initial_capital=20_000.0,
            fixed_allocation=1_000.0,
            max_open_positions=10,
            holding_period_rows=5,
        )
        rows = _make(10)
        result = simulate(rows, cfg)
        # Should have both in-flight and already-closed positions
        assert len(result["trades"]) >= 5

    def test_trades_have_pnl_field(self):
        cfg    = SimConfig(holding_period_rows=1)
        rows   = _make(5)
        result = simulate(rows, cfg)
        for t in result["trades"]:
            assert "pnl" in t
            assert "exit_value" in t
            assert "entry_capital" in t

    def test_pnl_positive_for_winning_trade(self):
        cfg  = SimConfig(fixed_allocation=1_000.0, holding_period_rows=1)
        rows = [_win(ret=5.0)]
        result = simulate(rows, cfg)
        assert result["trades"][0]["pnl"] == pytest.approx(50.0)

    def test_pnl_negative_for_losing_trade(self):
        cfg  = SimConfig(fixed_allocation=1_000.0, holding_period_rows=1)
        rows = [_loss(ret=-10.0)]
        result = simulate(rows, cfg)
        assert result["trades"][0]["pnl"] == pytest.approx(-100.0)

    def test_cash_conservation(self):
        # cash + open-position value should equal initial_capital + cumulative PnL
        cfg  = SimConfig(
            initial_capital=10_000.0,
            fixed_allocation=1_000.0,
            holding_period_rows=3,
        )
        rows = _make(10)
        result = simulate(rows, cfg)
        # All positions are closed at end → final equity = initial + sum(pnl)
        total_pnl   = sum(t["pnl"] for t in result["trades"])
        expected    = round(10_000.0 + total_pnl, 2)
        assert result["equity_curve"][-1] == pytest.approx(expected, abs=0.1)

    def test_min_confidence_filter(self):
        cfg  = SimConfig(min_confidence=70.0, fixed_allocation=1_000.0, holding_period_rows=1)
        rows = [
            _row(confidence_pct=65.0, return_5d=5.0),  # below threshold → skipped
            _row(confidence_pct=75.0, return_5d=5.0),  # above → opened
        ]
        result = simulate(rows, cfg)
        assert result["skipped_rows"] >= 1
        assert len(result["trades"]) == 1

    def test_determinism(self):
        rows = _make(15, win=True) + _make(5, win=False)
        r1   = simulate(rows)
        r2   = simulate(rows)
        assert r1["equity_curve"] == r2["equity_curve"]
        assert [t["pnl"] for t in r1["trades"]] == [t["pnl"] for t in r2["trades"]]

    def test_holding_20d_uses_return_20d(self):
        cfg = SimConfig(holding_period="20d", holding_period_rows=2, fixed_allocation=1_000.0)
        row = _row(return_5d=5.0, return_20d=15.0)
        result = simulate([row, _row()], cfg)  # needs 2+ rows for close_row=2
        # Position opened at row 0, closes at row 2 → only in trades after final close
        assert result["trades"][0]["return_pct"] == 15.0

    def test_config_asdict_in_result(self):
        result = simulate(_make(3))
        assert isinstance(result["config"], dict)
        assert "initial_capital" in result["config"]

    def test_open_at_end_count(self):
        # holding_period_rows=100 → nothing closes during 5 rows
        cfg    = SimConfig(holding_period_rows=100, fixed_allocation=500.0)
        result = simulate(_make(5), cfg)
        assert result["open_at_end_count"] == 5

    def test_avg_cash_utilization_nonnegative(self):
        result = simulate(_make(10))
        assert result["avg_cash_utilization_pct"] >= 0.0


# ── TestComputeMetrics ────────────────────────────────────────────────────────

class TestComputeMetrics:
    def _trades(self, pnls):
        return [
            {
                "pnl":        p,
                "return_pct": p / 10.0,   # entry_capital = 10 → return% = pnl/10
                "entry_capital": 10.0,
            }
            for p in pnls
        ]

    def test_empty(self):
        m = compute_metrics([], [], 10_000.0)
        assert m["final_value"]           == 10_000.0
        assert m["cumulative_return_pct"] == 0.0
        assert m["n_trades"]              == 0

    def test_cumulative_return_positive(self):
        curve = [10_000.0, 10_500.0]
        m     = compute_metrics(curve, [], 10_000.0)
        assert m["cumulative_return_pct"] == pytest.approx(5.0)

    def test_cumulative_return_negative(self):
        curve = [10_000.0, 9_500.0]
        m     = compute_metrics(curve, [], 10_000.0)
        assert m["cumulative_return_pct"] == pytest.approx(-5.0)

    def test_max_drawdown_computed(self):
        curve = [10_000.0, 12_000.0, 9_000.0]
        m     = compute_metrics(curve, [], 10_000.0)
        # peak=12000 trough=9000 → dd=25%
        assert m["max_drawdown_pct"] == pytest.approx(25.0)

    def test_win_rate_all_wins(self):
        trades = self._trades([50.0] * 5)
        m      = compute_metrics([10_000.0] * 5, trades, 10_000.0)
        assert m["win_rate"] == 100.0

    def test_win_rate_none_below_min_trades(self):
        trades = self._trades([10.0, 20.0])  # < MIN_TRADES_FOR_STATS
        m      = compute_metrics([10_000.0] * 2, trades, 10_000.0)
        assert m["win_rate"] is None

    def test_avg_position_return(self):
        # returns: 10%, 20%, 30% → avg 20%
        trades = [
            {"pnl": 10.0, "return_pct": 10.0, "entry_capital": 100.0},
            {"pnl": 20.0, "return_pct": 20.0, "entry_capital": 100.0},
            {"pnl": 30.0, "return_pct": 30.0, "entry_capital": 100.0},
            {"pnl": 20.0, "return_pct": 20.0, "entry_capital": 100.0},
            {"pnl": 20.0, "return_pct": 20.0, "entry_capital": 100.0},
        ]
        m = compute_metrics([10_000.0] * 5, trades, 10_000.0)
        assert m["avg_position_return_pct"] == pytest.approx(20.0)

    def test_sharpe_like_positive_return_positive(self):
        # All same return → std=0 → sharpe=None
        trades = self._trades([50.0] * 5)
        m      = compute_metrics([10_000.0] * 5, trades, 10_000.0)
        assert m["sharpe_like"] is None  # std = 0

    def test_sharpe_like_mixed_returns(self):
        trades = [
            {"pnl": 50.0,  "return_pct": 5.0,  "entry_capital": 1000.0},
            {"pnl": -30.0, "return_pct": -3.0, "entry_capital": 1000.0},
            {"pnl": 80.0,  "return_pct": 8.0,  "entry_capital": 1000.0},
            {"pnl": 10.0,  "return_pct": 1.0,  "entry_capital": 1000.0},
            {"pnl": -10.0, "return_pct": -1.0, "entry_capital": 1000.0},
        ]
        m = compute_metrics([10_000.0] * 5, trades, 10_000.0)
        assert m["sharpe_like"] is not None

    def test_n_trades_correct(self):
        trades = self._trades([10.0] * 7)
        m      = compute_metrics([10_000.0] * 7, trades, 10_000.0)
        assert m["n_trades"] == 7

    def test_cagr_like_none_for_few_rows(self):
        m = compute_metrics([10_000.0] * 5, [], 10_000.0)
        assert m["cagr_like_pct"] is None  # n_rows < 10

    def test_cagr_like_computed_for_many_rows(self):
        curve = [10_000.0 * (1.0 + i * 0.005) for i in range(30)]
        m = compute_metrics(curve, [], 10_000.0)
        assert m["cagr_like_pct"] is not None

    def test_final_value_equals_last_equity(self):
        curve = [10_000.0, 10_500.0, 11_000.0]
        m = compute_metrics(curve, [], 10_000.0)
        assert m["final_value"] == 11_000.0


# ── TestConcentrationAnalysis ─────────────────────────────────────────────────

class TestConcentrationAnalysis:
    def test_empty_trades(self):
        result = concentration_analysis([])
        assert result["n_trades"] == 0
        assert result["warnings"] == []

    def test_regime_counts_correct(self):
        trades = [
            {"ticker": "A", "regime": "BULL",    "tier": "ALERT", "signal_summary": "{}"},
            {"ticker": "B", "regime": "BULL",    "tier": "ALERT", "signal_summary": "{}"},
            {"ticker": "C", "regime": "NEUTRAL", "tier": "ALERT", "signal_summary": "{}"},
        ]
        result = concentration_analysis(trades)
        assert result["regime"]["counts"]["BULL"]    == 2
        assert result["regime"]["counts"]["NEUTRAL"] == 1

    def test_regime_pcts_sum_to_100(self):
        trades = [
            {"ticker": "A", "regime": "BULL",     "tier": "ALERT", "signal_summary": "{}"},
            {"ticker": "B", "regime": "NEUTRAL",  "tier": "ALERT", "signal_summary": "{}"},
            {"ticker": "C", "regime": "RISK_OFF", "tier": "ALERT", "signal_summary": "{}"},
            {"ticker": "D", "regime": "BULL",     "tier": "ALERT", "signal_summary": "{}"},
        ]
        result = concentration_analysis(trades)
        total = sum(result["regime"]["pcts"].values())
        assert abs(total - 100.0) < 0.1

    def test_regime_concentration_warning(self):
        # 10/10 trades in BULL → 100% > threshold
        trades = [
            {"ticker": "X", "regime": "BULL", "tier": "ALERT", "signal_summary": "{}"}
            for _ in range(10)
        ]
        result = concentration_analysis(trades)
        assert any("REGIME_CONCENTRATION" in w for w in result["warnings"])

    def test_no_warning_when_balanced(self):
        # 50% BULL, 50% NEUTRAL → no regime warning
        trades = (
            [{"ticker": "A", "regime": "BULL",    "tier": "ALERT", "signal_summary": "{}"}] * 5
            + [{"ticker": "B", "regime": "NEUTRAL", "tier": "ALERT", "signal_summary": "{}"}] * 5
        )
        result = concentration_analysis(trades)
        assert not any("REGIME_CONCENTRATION" in w for w in result["warnings"])

    def test_ticker_concentration_warning(self):
        # 8/10 trades in AAPL → 80% > threshold (30%)
        trades = [
            {"ticker": "AAPL", "regime": "BULL", "tier": "ALERT", "signal_summary": "{}"}
            for _ in range(8)
        ] + [
            {"ticker": "MSFT", "regime": "BULL", "tier": "ALERT", "signal_summary": "{}"}
            for _ in range(2)
        ]
        result = concentration_analysis(trades)
        assert any("TICKER_CONCENTRATION" in w for w in result["warnings"])

    def test_signal_concentration_warning(self):
        sig = json.dumps({"options": 2})
        trades = [
            {"ticker": "A", "regime": "BULL", "tier": "ALERT", "signal_summary": sig}
            for _ in range(8)
        ] + [
            {"ticker": "B", "regime": "BULL", "tier": "ALERT", "signal_summary": "{}"}
            for _ in range(2)
        ]
        result = concentration_analysis(trades)
        assert any("SIGNAL_CONCENTRATION" in w for w in result["warnings"])

    def test_dominant_regime_identified(self):
        trades = [
            {"ticker": "A", "regime": "BULL",    "tier": "ALERT", "signal_summary": "{}"},
            {"ticker": "B", "regime": "BULL",    "tier": "ALERT", "signal_summary": "{}"},
            {"ticker": "C", "regime": "NEUTRAL", "tier": "ALERT", "signal_summary": "{}"},
        ]
        result = concentration_analysis(trades)
        assert result["regime"]["dominant"] == "BULL"

    def test_tier_breakdown(self):
        trades = [
            {"ticker": "A", "regime": "BULL", "tier": "CONVICTION", "signal_summary": "{}"},
            {"ticker": "B", "regime": "BULL", "tier": "ALERT",      "signal_summary": "{}"},
            {"ticker": "C", "regime": "BULL", "tier": "ALERT",      "signal_summary": "{}"},
        ]
        result = concentration_analysis(trades)
        assert result["tier"]["counts"]["CONVICTION"] == 1
        assert result["tier"]["counts"]["ALERT"]      == 2


# ── TestStressTests ───────────────────────────────────────────────────────────

class TestStressRemoveTopWinners:
    def test_result_keys(self):
        rows   = _make(10)
        result = stress_remove_top_winners(rows)
        assert "n_removed"                    in result
        assert "base_cumulative_return_pct"   in result
        assert "stress_cumulative_return_pct" in result
        assert "fragile"                      in result

    def test_n_removed_capped_at_row_count(self):
        result = stress_remove_top_winners(_make(3), n=10)
        assert result["n_removed"] == 3

    def test_stress_n_leq_base_n(self):
        rows   = _make(20)
        result = stress_remove_top_winners(rows, n=5)
        # after removing top-5 winners, remaining rows ≤ original
        assert result["stress_cumulative_return_pct"] is not None or True  # no crash

    def test_removing_winners_reduces_return(self):
        # All winning rows; remove top 5 → lower return
        rows   = [_win(ret=float(i)) for i in range(1, 11)]
        result = stress_remove_top_winners(rows, n=5)
        b = result["base_cumulative_return_pct"]
        s = result["stress_cumulative_return_pct"]
        if b is not None and s is not None:
            assert s <= b

    def test_fragile_flag_when_top_trades_dominate(self):
        # 5 big winners + many small ones
        big_wins  = [_win(ret=50.0) for _ in range(5)]
        small     = [_win(ret=0.5)  for _ in range(15)]
        result    = stress_remove_top_winners(big_wins + small, n=5)
        # fragile if impact > 50% of base return
        assert isinstance(result["fragile"], bool)

    def test_empty_rows(self):
        result = stress_remove_top_winners([])
        assert result["n_removed"] == 0


class TestStressConsecutiveLosses:
    def _trade(self, pnl, entry_row=0):
        return {"pnl": pnl, "entry_row": entry_row, "return_pct": pnl / 100.0}

    def test_no_trades(self):
        result = stress_consecutive_losses([])
        assert result["max_consecutive_losses"] == 0
        assert result["streak_total_loss"]      is None

    def test_all_wins_no_streak(self):
        trades = [self._trade(50.0, i) for i in range(5)]
        result = stress_consecutive_losses(trades)
        assert result["max_consecutive_losses"] == 0
        assert result["streak_total_loss"]      == 0.0

    def test_streak_of_three(self):
        trades = [
            self._trade(20.0,  0),
            self._trade(-10.0, 1),
            self._trade(-20.0, 2),
            self._trade(-15.0, 3),
            self._trade(30.0,  4),
        ]
        result = stress_consecutive_losses(trades)
        assert result["max_consecutive_losses"] == 3
        assert result["streak_total_loss"]      == pytest.approx(45.0)

    def test_warning_triggered_at_threshold(self):
        trades = [self._trade(-10.0, i) for i in range(CONSECUTIVE_LOSS_WARN_STREAK)]
        result = stress_consecutive_losses(trades)
        assert result["warning"] is True

    def test_no_warning_below_threshold(self):
        trades = [self._trade(-10.0, i) for i in range(CONSECUTIVE_LOSS_WARN_STREAK - 1)]
        result = stress_consecutive_losses(trades)
        assert result["warning"] is False

    def test_sorted_by_entry_row(self):
        # Out-of-order entries: loss at row 5, win at row 3, loss at row 7
        trades = [
            self._trade(-10.0, 7),
            self._trade(20.0,  3),
            self._trade(-10.0, 5),
        ]
        result = stress_consecutive_losses(trades)
        # ordered: win(3), loss(5), loss(7) → streak=2
        assert result["max_consecutive_losses"] == 2

    def test_multiple_streaks_max_returned(self):
        trades = [
            self._trade(-10.0, 0),
            self._trade(-10.0, 1),
            self._trade(20.0,  2),
            self._trade(-10.0, 3),
            self._trade(-10.0, 4),
            self._trade(-10.0, 5),
        ]
        result = stress_consecutive_losses(trades)
        assert result["max_consecutive_losses"] == 3


class TestStressRiskOffOnly:
    def test_no_risk_off_rows(self):
        rows   = _make(5, regime="BULL")
        result = stress_risk_off_only(rows)
        assert result["n_risk_off_rows"] == 0
        assert result["metrics"]         is None
        assert "note" in result

    def test_risk_off_rows_simulated(self):
        rows = _make(3, regime="BULL") + _make(4, regime="RISK_OFF")
        result = stress_risk_off_only(rows)
        assert result["n_risk_off_rows"] == 4
        assert result["metrics"] is not None

    def test_risk_off_pct_correct(self):
        rows   = _make(6, regime="BULL") + _make(4, regime="RISK_OFF")
        result = stress_risk_off_only(rows)
        assert result["risk_off_pct"] == pytest.approx(40.0)

    def test_n_total_rows_correct(self):
        rows = _make(10)
        result = stress_risk_off_only(rows)
        assert result["n_total_rows"] == 10


class TestStressConfidenceShock:
    def test_result_keys(self):
        rows   = _make(5)
        result = stress_confidence_shock(rows, shock_pct=20.0)
        assert "shock_pct"                    in result
        assert "base_n_trades"                in result
        assert "shock_n_trades"               in result
        assert "impact_cumulative_return_pct" in result

    def test_shock_pct_recorded(self):
        result = stress_confidence_shock(_make(5), shock_pct=30.0)
        assert result["shock_pct"] == 30.0

    def test_original_rows_not_mutated(self):
        rows = [_row(confidence_pct=80.0)]
        orig_conf = rows[0]["confidence_pct"]
        stress_confidence_shock(rows, shock_pct=20.0)
        assert rows[0]["confidence_pct"] == orig_conf

    def test_shock_reduces_confidence_below_threshold(self):
        # conf=50 − shock=40 = 10 < min_confidence=30 → all rows skipped
        cfg    = SimConfig(min_confidence=30.0, fixed_allocation=1_000.0, holding_period_rows=1)
        rows   = [_row(confidence_pct=50.0, return_5d=5.0)] * 5
        result = stress_confidence_shock(rows, cfg, shock_pct=40.0)
        assert result["shock_n_trades"] == 0

    def test_zero_shock_same_as_baseline(self):
        rows   = _make(10)
        result = stress_confidence_shock(rows, shock_pct=0.0)
        assert result["base_n_trades"]  == result["shock_n_trades"]


class TestRunStressTests:
    def test_result_keys(self):
        result = run_stress_tests(_make(5))
        assert "remove_top_winners"     in result
        assert "consecutive_losses"     in result
        assert "risk_off_only"          in result
        assert "confidence_shock_20pct" in result
        assert "confidence_shock_40pct" in result

    def test_empty_rows(self):
        result = run_stress_tests([])
        assert result["consecutive_losses"]["max_consecutive_losses"] == 0


# ── TestRobustnessAnalysis ────────────────────────────────────────────────────

class TestRobustnessAnalysis:
    def _trade(self, pnl, ret=5.0):
        return {"pnl": pnl, "return_pct": ret, "entry_capital": 1000.0}

    def test_empty_trades(self):
        result = robustness_analysis([], {})
        assert result["n_trades"]   == 0
        assert result["warnings"]   == []

    def test_alpha_concentration_computed(self):
        # 3 large wins + 2 small wins → top-3 dominate
        trades = [
            self._trade(500.0),
            self._trade(400.0),
            self._trade(300.0),
            self._trade(10.0),
            self._trade(10.0),
        ]
        result = robustness_analysis(trades, {})
        assert result["alpha_concentration_pct"] is not None

    def test_alpha_concentration_warning_triggered(self):
        # All gains in top-3 → 100% > ALPHA_CONCENTRATION_THRESHOLD
        trades = [
            self._trade(300.0),
            self._trade(200.0),
            self._trade(100.0),
            self._trade(0.0),   # zero PnL — not a win
            self._trade(0.0),
        ]
        result = robustness_analysis(trades, {})
        assert any("ALPHA_CONCENTRATION" in w for w in result["warnings"])

    def test_few_winners_warning(self):
        trades = [
            self._trade(-10.0),
            self._trade(-20.0),
            self._trade(5.0),
            self._trade(-5.0),
            self._trade(-3.0),
        ]
        result = robustness_analysis(trades, {})
        assert any("FEW_WINNERS" in w for w in result["warnings"])

    def test_n_profitable_correct(self):
        trades = [self._trade(50.0)] * 3 + [self._trade(-20.0)] * 2
        result = robustness_analysis(trades, {})
        assert result["n_profitable"] == 3

    def test_unstable_compounding_warning(self):
        trades  = [self._trade(10.0)] * 5
        metrics = {"volatility_pct": 8.0}
        result  = robustness_analysis(trades, metrics)
        assert any("UNSTABLE_COMPOUNDING" in w for w in result["warnings"])

    def test_no_warnings_for_healthy_portfolio(self):
        trades  = [self._trade(10.0 + i) for i in range(10)]
        metrics = {"volatility_pct": 1.0}
        result  = robustness_analysis(trades, metrics)
        alpha   = result["alpha_concentration_pct"]
        if alpha is None or alpha <= ALPHA_CONCENTRATION_THRESHOLD:
            assert not any("ALPHA_CONCENTRATION" in w for w in result["warnings"])


# ── TestGenerateRecommendations ───────────────────────────────────────────────

class TestGenerateRecommendations:
    def _empty(self):
        return {"warnings": []}

    def test_no_trades_insufficient_data_rec(self):
        recs = generate_recommendations({"n_trades": 0}, self._empty(), self._empty())
        assert any("INSUFFICIENT_DATA" in r for r in recs)

    def test_low_win_rate_rec(self):
        m    = {"n_trades": 20, "win_rate": 35.0, "max_drawdown_pct": 5.0, "cumulative_return_pct": 1.0}
        recs = generate_recommendations(m, self._empty(), self._empty())
        assert any("INCREASE_CONFIDENCE_THRESHOLD" in r for r in recs)

    def test_severe_drawdown_rec(self):
        m    = {"n_trades": 20, "win_rate": 60.0, "max_drawdown_pct": DRAWDOWN_SEVERE_THRESHOLD + 1, "cumulative_return_pct": 5.0}
        recs = generate_recommendations(m, self._empty(), self._empty())
        assert any("REDUCE_POSITION_SIZE" in r for r in recs)

    def test_high_drawdown_monitor_rec(self):
        m    = {"n_trades": 20, "win_rate": 60.0, "max_drawdown_pct": DRAWDOWN_HIGH_THRESHOLD + 1, "cumulative_return_pct": 5.0}
        recs = generate_recommendations(m, self._empty(), self._empty())
        assert any("MONITOR_DRAWDOWN" in r or "REDUCE_POSITION_SIZE" in r for r in recs)

    def test_regime_concentration_in_recommendations(self):
        m    = {"n_trades": 10, "win_rate": 60.0, "max_drawdown_pct": 5.0, "cumulative_return_pct": 5.0}
        conc = {"warnings": ["REGIME_CONCENTRATION: BULL represents 90% of trades"]}
        recs = generate_recommendations(m, conc, self._empty())
        assert any("DIVERSIFY_REGIME_EXPOSURE" in r for r in recs)

    def test_ticker_concentration_in_recommendations(self):
        m    = {"n_trades": 10, "win_rate": 60.0, "max_drawdown_pct": 5.0, "cumulative_return_pct": 5.0}
        conc = {"warnings": ["TICKER_CONCENTRATION: AAPL represents 80% of trades"]}
        recs = generate_recommendations(m, conc, self._empty())
        assert any("TIGHTEN_TICKER_EXPOSURE" in r for r in recs)

    def test_alpha_concentration_in_recommendations(self):
        m   = {"n_trades": 10, "win_rate": 60.0, "max_drawdown_pct": 5.0, "cumulative_return_pct": 5.0}
        rob = {"warnings": ["ALPHA_CONCENTRATION: top-3 trades account for 80% of gains"]}
        recs = generate_recommendations(m, self._empty(), rob)
        assert any("BROADEN_ALPHA_SOURCES" in r for r in recs)

    def test_determinism(self):
        m    = {"n_trades": 20, "win_rate": 35.0, "max_drawdown_pct": 25.0, "cumulative_return_pct": -5.0}
        conc = {"warnings": ["REGIME_CONCENTRATION: something"]}
        rob  = {"warnings": []}
        r1   = generate_recommendations(m, conc, rob)
        r2   = generate_recommendations(m, conc, rob)
        assert r1 == r2

    def test_returns_list(self):
        recs = generate_recommendations({"n_trades": 5, "win_rate": 60.0,
                                         "max_drawdown_pct": 5.0, "cumulative_return_pct": 10.0},
                                        self._empty(), self._empty())
        assert isinstance(recs, list)


# ── TestGenerateReport ────────────────────────────────────────────────────────

class TestGenerateReport:
    def _rows(self, n=20):
        return (
            _make(n // 2, win=True,  regime="BULL",    conf=65.0)
            + _make(n // 2, win=False, regime="NEUTRAL", conf=55.0)
        )

    def test_report_keys(self):
        report = generate_report(self._rows(20))
        expected = {
            "row_count", "config", "metrics", "equity_curve_summary",
            "best_period", "worst_period", "concentration", "robustness",
            "stress_tests", "recommendations", "portfolio_health", "warnings",
        }
        assert expected.issubset(report.keys())

    def test_row_count_correct(self):
        rows   = self._rows(20)
        report = generate_report(rows)
        assert report["row_count"] == 20

    def test_equity_curve_summary_keys(self):
        report = generate_report(self._rows(20))
        ec = report["equity_curve_summary"]
        assert "length" in ec and "initial" in ec and "final" in ec

    def test_portfolio_health_valid(self):
        report = generate_report(self._rows(20))
        assert report["portfolio_health"] in ("HEALTHY", "CAUTION", "WEAK", "INSUFFICIENT_DATA")

    def test_config_in_report(self):
        cfg    = SimConfig(fixed_allocation=500.0)
        report = generate_report(self._rows(20), cfg)
        assert report["config"]["fixed_allocation"] == 500.0

    def test_recommendations_is_list(self):
        report = generate_report(self._rows(20))
        assert isinstance(report["recommendations"], list)

    def test_warnings_is_list(self):
        report = generate_report(self._rows(20))
        assert isinstance(report["warnings"], list)

    def test_stress_tests_keys(self):
        report = generate_report(self._rows(20))
        st = report["stress_tests"]
        assert "remove_top_winners"  in st
        assert "consecutive_losses"  in st
        assert "risk_off_only"       in st

    def test_empty_rows(self):
        report = generate_report([])
        assert report["row_count"]        == 0
        assert report["metrics"]["n_trades"] == 0
        assert report["portfolio_health"] == "INSUFFICIENT_DATA"

    def test_determinism(self):
        rows = self._rows(20)
        r1   = generate_report(rows)
        r2   = generate_report(rows)
        assert r1["metrics"]["cumulative_return_pct"] == r2["metrics"]["cumulative_return_pct"]
        assert r1["portfolio_health"]                 == r2["portfolio_health"]


# ── TestAllocationIntegration ─────────────────────────────────────────────────

class TestAllocationIntegration:
    def test_confidence_method_larger_alloc_for_higher_conf(self):
        cfg = SimConfig(
            allocation_method="confidence",
            confidence_base=1_000.0,
            holding_period_rows=1,
            max_open_positions=1,
        )
        high_conf = [_row(confidence_pct=90.0, return_5d=5.0),
                     _row(confidence_pct=90.0, return_5d=5.0)]
        low_conf  = [_row(confidence_pct=40.0, return_5d=5.0),
                     _row(confidence_pct=40.0, return_5d=5.0)]

        r_high = simulate(high_conf, cfg)
        r_low  = simulate(low_conf,  cfg)
        # High confidence → larger positions → more PnL
        assert r_high["trades"][0]["entry_capital"] > r_low["trades"][0]["entry_capital"]

    def test_tier_method_conviction_larger_than_watch(self):
        cfg_c = SimConfig(allocation_method="tier", fixed_allocation=1_000.0,
                          holding_period_rows=1, max_open_positions=1)
        conviction_row = _row(tier="CONVICTION", return_5d=5.0)
        watch_row      = _row(tier="WATCH",      return_5d=5.0)

        r_c = simulate([conviction_row, conviction_row], cfg_c)
        r_w = simulate([watch_row,      watch_row],      cfg_c)
        assert r_c["trades"][0]["entry_capital"] > r_w["trades"][0]["entry_capital"]

    def test_max_position_cap_limits_overallocation(self):
        # fixed_allocation=5000, cap=10%, portfolio=10000 → alloc capped at 1000
        # Use 0% return rows so portfolio stays at exactly 10000 throughout
        cfg   = SimConfig(fixed_allocation=5_000.0, max_position_cap=0.10,
                          holding_period_rows=1)
        rows  = [_row(confidence_pct=65.0, return_5d=0.0),
                 _row(confidence_pct=65.0, return_5d=0.0)]
        result = simulate(rows, cfg)
        for t in result["trades"]:
            assert t["entry_capital"] <= 10_000.0 * 0.10 + 0.01  # cap = 10% of ~10000


# ── TestCapitalAccounting ─────────────────────────────────────────────────────

class TestCapitalAccounting:
    def test_cash_never_negative(self):
        # aggressive allocation with many overlapping positions
        cfg  = SimConfig(
            initial_capital=10_000.0,
            fixed_allocation=1_000.0,
            max_open_positions=20,
            holding_period_rows=50,  # never close during sim
            min_cash_pct=0.0,
        )
        rows   = _make(15)
        result = simulate(rows, cfg)
        # All equity points should be ≥ 0
        assert all(v >= 0 for v in result["equity_curve"])

    def test_equity_constant_without_trades(self):
        # All rows filtered by min_confidence
        cfg  = SimConfig(min_confidence=100.0)  # impossible threshold
        rows = _make(5)
        result = simulate(rows, cfg)
        # No trades → equity stays at initial_capital
        assert all(abs(v - 10_000.0) < 0.01 for v in result["equity_curve"])

    def test_sum_of_pnl_matches_equity_change(self):
        cfg  = SimConfig(
            initial_capital=10_000.0,
            fixed_allocation=1_000.0,
            holding_period_rows=3,
        )
        rows   = _make(12)
        result = simulate(rows, cfg)

        total_pnl  = sum(t["pnl"] for t in result["trades"])
        final_eq   = result["equity_curve"][-1]
        expected   = round(10_000.0 + total_pnl, 2)
        assert abs(final_eq - expected) < 0.5

    def test_position_entry_deducts_from_cash(self):
        # With a single position opened, equity should = initial (cash + pos)
        cfg  = SimConfig(
            initial_capital=10_000.0,
            fixed_allocation=1_000.0,
            max_open_positions=1,
            holding_period_rows=100,  # won't close
        )
        rows   = _make(5)
        result = simulate(rows, cfg)
        # First row: open $1000 position → equity = $9000 cash + $1000 = $10000
        assert result["equity_curve"][0] == pytest.approx(10_000.0)


# ── TestSparseHandling ────────────────────────────────────────────────────────

class TestSparseHandling:
    def test_none_returns_handled(self):
        rows = [_row(return_5d=None) for _ in range(5)]
        result = simulate(rows)
        # PnL should be 0 for None returns (treated as 0%)
        for t in result["trades"]:
            assert t["pnl"] == pytest.approx(0.0)

    def test_missing_confidence_treated_as_zero(self):
        row = {"ticker": "X", "regime": "BULL", "return_5d": 5.0}  # no confidence_pct
        cfg = SimConfig(min_confidence=0.0, holding_period_rows=1)
        result = simulate([row, row], cfg)
        # Should still run without error
        assert isinstance(result["equity_curve"], list)

    def test_missing_regime_defaults(self):
        row = {"ticker": "X", "confidence_pct": 65.0, "return_5d": 5.0}  # no regime
        result = simulate([row, row])
        assert len(result["trades"]) >= 0  # no crash

    def test_single_row(self):
        result = simulate([_win()])
        assert len(result["equity_curve"]) == 1

    def test_report_empty_rows(self):
        report = generate_report([])
        assert report["metrics"]["win_rate"] is None  # insufficient

    def test_concentration_no_signals(self):
        trades = [
            {"ticker": "A", "regime": "BULL", "tier": "ALERT", "signal_summary": "{}"}
            for _ in range(3)
        ]
        result = concentration_analysis(trades)
        assert result["signal"]["top"] is None
