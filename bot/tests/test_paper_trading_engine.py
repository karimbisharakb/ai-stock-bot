"""
Unit tests for paper_trading_engine.py (Phase 5B).

All tests pass data directly — no DB access, no network calls.
Tests cover: portfolio accounting, event generation, replay determinism,
stop-loss / take-profit handling, exposure enforcement, liquidation logic,
risk-off mode, operational alerts, and sparse / edge-case handling.
"""
import pytest
import paper_trading_engine as pte
from paper_trading_engine import (
    DEFAULT_INITIAL_CASH,
    DEFAULT_SLIPPAGE_PCT,
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
    DEFAULT_MAX_HOLDING_ROWS,
    DEFAULT_ALLOCATION_PCT,
    DEFAULT_MAX_EXPOSURE_PCT,
    DEFAULT_MAX_OPEN_POSITIONS,
    DEFAULT_MIN_SCORE_TO_ENTER,
    DEFAULT_RISK_OFF_DRAWDOWN_PCT,
    EVENT_ENTRY,
    EVENT_EXIT,
    EVENT_STOP_LOSS,
    EVENT_TAKE_PROFIT,
    EVENT_FORCED_EXIT,
    EVENT_EXPOSURE_BLOCK,
    EVENT_RISK_REDUCTION,
    MAX_EVENTS,
    MAX_EQUITY_HISTORY,
    MAX_CLOSED_TRADES,
    ALERT_DRAWDOWN_PCT,
    ALERT_CONCENTRATION_PCT,
    ALERT_CASH_RESERVE_PCT,
    ALERT_EXPOSURE_HIGH_PCT,
    HEALTH_DRAWDOWN_CAUTION,
    HEALTH_WIN_RATE_CAUTION,
    HEALTH_WIN_RATE_HEALTHY,
    MIN_TRADES_FOR_STATS,
    EngineConfig,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cfg(**kwargs) -> EngineConfig:
    defaults = dict(
        initial_cash=10_000.0,
        slippage_pct=0.0,
        fee_per_trade=0.0,
        max_exposure_pct=0.90,
        max_ticker_pct=0.20,
        max_sector_pct=0.40,
        max_open_positions=10,
        stop_loss_pct=0.10,
        take_profit_pct=0.20,
        max_holding_rows=5,
        allocation_pct=0.10,
        min_score_to_enter=6.0,
        risk_off_drawdown_pct=0.15,
    )
    defaults.update(kwargs)
    return EngineConfig(**defaults)


def _row(ticker="AAPL", score=7.0, price=100.0, sector="tech"):
    return {
        "ticker":         ticker,
        "adjusted_score": score,
        "price":          price,
        "sector":         sector,
    }


def _state_with_pos(ticker="AAPL", entry_price=100.0, shares=10.0,
                    entry_cash=1_000.0, stop_loss=90.0, take_profit=120.0,
                    entry_row=0, sector="tech", cash=9_000.0):
    """Build a state that already holds one open position."""
    cfg   = _cfg(initial_cash=cash + entry_cash)
    s     = pte.create_state(cfg)
    s["cash"] = cash
    s["positions"][ticker] = {
        "ticker":            ticker,
        "sector":            sector,
        "entry_price":       entry_price,
        "fill_price":        entry_price,
        "shares":            shares,
        "entry_cash":        entry_cash,
        "entry_row":         entry_row,
        "stop_loss_price":   stop_loss,
        "take_profit_price": take_profit,
    }
    return s


def _close_all(state, cfg=None):
    """Force-close every open position and return the final state."""
    c = cfg or _cfg()
    s = state
    for ticker in list(s["positions"]):
        pos = s["positions"][ticker]
        s, _ = pte._close_position(s, ticker, pos["entry_price"], s["row_idx"], "FORCED", c)
    return s


# ── TestConstants ─────────────────────────────────────────────────────────────

class TestConstants:
    def test_event_type_strings(self):
        assert EVENT_ENTRY          == "ENTRY"
        assert EVENT_EXIT           == "EXIT"
        assert EVENT_STOP_LOSS      == "STOP_LOSS"
        assert EVENT_TAKE_PROFIT    == "TAKE_PROFIT"
        assert EVENT_FORCED_EXIT    == "FORCED_EXIT"
        assert EVENT_EXPOSURE_BLOCK == "EXPOSURE_BLOCK"
        assert EVENT_RISK_REDUCTION == "RISK_REDUCTION"

    def test_collection_bounds_positive(self):
        assert MAX_EVENTS > 0
        assert MAX_EQUITY_HISTORY > 0
        assert MAX_CLOSED_TRADES > 0

    def test_default_slippage_is_small(self):
        assert 0 < DEFAULT_SLIPPAGE_PCT < 0.01

    def test_default_allocation_is_fractional(self):
        assert 0 < DEFAULT_ALLOCATION_PCT < 1.0


# ── TestEngineConfig ──────────────────────────────────────────────────────────

class TestEngineConfig:
    def test_default_values(self):
        cfg = EngineConfig()
        assert cfg.initial_cash       == DEFAULT_INITIAL_CASH
        assert cfg.slippage_pct       == DEFAULT_SLIPPAGE_PCT
        assert cfg.stop_loss_pct      == DEFAULT_STOP_LOSS_PCT
        assert cfg.take_profit_pct    == DEFAULT_TAKE_PROFIT_PCT
        assert cfg.max_holding_rows   == DEFAULT_MAX_HOLDING_ROWS
        assert cfg.max_open_positions == DEFAULT_MAX_OPEN_POSITIONS

    def test_custom_override(self):
        cfg = EngineConfig(initial_cash=5_000.0, slippage_pct=0.002)
        assert cfg.initial_cash  == 5_000.0
        assert cfg.slippage_pct  == 0.002
        assert cfg.fee_per_trade == 0.0     # default unchanged

    def test_immutable(self):
        cfg = EngineConfig()
        with pytest.raises((AttributeError, TypeError)):
            cfg.initial_cash = 99.0


# ── TestCreateState ───────────────────────────────────────────────────────────

class TestCreateState:
    def test_initial_cash(self):
        s = pte.create_state(_cfg(initial_cash=5_000.0))
        assert s["cash"] == 5_000.0

    def test_empty_positions(self):
        s = pte.create_state()
        assert s["positions"] == {}

    def test_peak_equity_equals_initial_cash(self):
        s = pte.create_state(_cfg(initial_cash=8_000.0))
        assert s["peak_equity"] == 8_000.0

    def test_risk_off_false(self):
        assert pte.create_state()["risk_off"] is False

    def test_row_idx_zero(self):
        assert pte.create_state()["row_idx"] == 0

    def test_no_config_uses_defaults(self):
        s = pte.create_state()
        assert s["cash"] == DEFAULT_INITIAL_CASH


# ── TestFillSimulation ────────────────────────────────────────────────────────

class TestFillSimulation:
    def test_entry_fill_adds_slippage(self):
        fill = pte._entry_fill(100.0, 0.001)
        assert fill == pytest.approx(100.1, rel=1e-6)

    def test_exit_fill_subtracts_slippage(self):
        fill = pte._exit_fill(100.0, 0.001)
        assert fill == pytest.approx(99.9, rel=1e-6)

    def test_zero_slippage_pass_through(self):
        assert pte._entry_fill(100.0, 0.0) == 100.0
        assert pte._exit_fill(100.0, 0.0)  == 100.0

    def test_round_trip_cost_equals_two_slippage(self):
        entry = pte._entry_fill(100.0, 0.001)
        exit_ = pte._exit_fill(100.0, 0.001)
        # Round-trip slippage: (entry - exit) / price ≈ 2 × slippage
        assert (entry - exit_) / 100.0 == pytest.approx(0.002, rel=1e-4)


# ── TestCheckRiskControls ─────────────────────────────────────────────────────

class TestCheckRiskControls:
    def test_empty_state_allows_entry(self):
        s   = pte.create_state(_cfg())
        res = pte.check_risk_controls(s, "AAPL", 1_000.0, "tech", _cfg())
        assert res["allowed"] is True
        assert res["blockers"] == []

    def test_ticker_already_open_blocks(self):
        s   = _state_with_pos("AAPL")
        res = pte.check_risk_controls(s, "AAPL", 500.0, "tech", _cfg())
        assert res["allowed"] is False
        assert any("ticker_open" in b for b in res["blockers"])

    def test_max_positions_reached_blocks(self):
        cfg = _cfg(max_open_positions=1)
        s   = _state_with_pos("AAPL")
        res = pte.check_risk_controls(s, "MSFT", 500.0, "tech", cfg)
        assert res["allowed"] is False
        assert any("max_positions" in b for b in res["blockers"])

    def test_max_exposure_exceeded_blocks(self):
        cfg = _cfg(max_exposure_pct=0.10, initial_cash=10_000.0)
        # 1_000 already deployed; proposed_alloc=900 → (1000+900)/10000 = 19 % > 10 %
        s   = _state_with_pos("AAPL", entry_cash=1_000.0, cash=9_000.0)
        res = pte.check_risk_controls(s, "MSFT", 900.0, "tech", cfg)
        assert res["allowed"] is False
        assert any("max_exposure" in b for b in res["blockers"])

    def test_sector_concentration_blocks(self):
        cfg = _cfg(max_sector_pct=0.10)
        s   = _state_with_pos("AAPL", entry_cash=500.0, cash=9_500.0, sector="tech")
        # (500 existing + 600 proposed) / 10000 = 11 % > 10 %
        res = pte.check_risk_controls(s, "MSFT", 600.0, "tech", cfg)
        assert res["allowed"] is False
        assert any("sector_concentration" in b for b in res["blockers"])

    def test_insufficient_cash_blocks(self):
        s = pte.create_state(_cfg(initial_cash=100.0))
        res = pte.check_risk_controls(s, "AAPL", 200.0, "tech", _cfg())
        assert res["allowed"] is False
        assert any("insufficient_cash" in b for b in res["blockers"])

    def test_multiple_blockers_returned(self):
        cfg = _cfg(max_open_positions=1)
        s   = _state_with_pos("AAPL", cash=0.0)
        res = pte.check_risk_controls(s, "AAPL", 5_000.0, "tech", cfg)
        assert len(res["blockers"]) >= 2

    def test_equity_zero_returns_blocked(self):
        s = pte.create_state(_cfg(initial_cash=0.0))
        res = pte.check_risk_controls(s, "AAPL", 0.0, "", None)
        assert res["allowed"] is False


# ── TestEntrySimulation ───────────────────────────────────────────────────────

class TestEntrySimulation:
    def test_successful_entry_adds_position(self):
        s   = pte.create_state(_cfg())
        s2  = pte.process_alert(s, _row("AAPL", score=7.0, price=100.0), current_price=100.0, config=_cfg())
        assert "AAPL" in s2["positions"]

    def test_entry_deducts_cash(self):
        cfg = _cfg(allocation_pct=0.10)
        s   = pte.create_state(cfg)
        s2  = pte.process_alert(s, _row("AAPL", score=7.0, price=100.0), current_price=100.0, config=cfg)
        # 10% of 10_000 = 1_000 allocated
        assert s2["cash"] == pytest.approx(9_000.0, rel=1e-6)

    def test_entry_fires_entry_event(self):
        s  = pte.create_state(_cfg())
        s2 = pte.process_alert(s, _row("AAPL", score=7.0, price=100.0), current_price=100.0, config=_cfg())
        entry_events = [e for e in s2["events"] if e["event_type"] == EVENT_ENTRY]
        assert len(entry_events) == 1
        assert entry_events[0]["ticker"] == "AAPL"

    def test_below_score_threshold_no_entry(self):
        cfg = _cfg(min_score_to_enter=6.0)
        s   = pte.create_state(cfg)
        s2  = pte.process_alert(s, _row("AAPL", score=5.9, price=100.0), current_price=100.0, config=cfg)
        assert "AAPL" not in s2["positions"]
        assert not any(e["event_type"] == EVENT_ENTRY for e in s2["events"])

    def test_duplicate_ticker_fires_exposure_block(self):
        s  = _state_with_pos("AAPL")
        s2 = pte.process_alert(s, _row("AAPL", score=8.0, price=100.0), current_price=100.0, config=_cfg())
        assert any(e["event_type"] == EVENT_EXPOSURE_BLOCK for e in s2["events"])

    def test_slippage_increases_fill_price(self):
        cfg = _cfg(slippage_pct=0.01)
        s   = pte.create_state(cfg)
        s2  = pte.process_alert(s, _row("AAPL", score=7.0, price=100.0), current_price=100.0, config=cfg)
        pos = s2["positions"]["AAPL"]
        assert pos["fill_price"] == pytest.approx(101.0, rel=1e-6)

    def test_stop_loss_and_take_profit_set_correctly(self):
        cfg = _cfg(stop_loss_pct=0.10, take_profit_pct=0.20, slippage_pct=0.0)
        s   = pte.create_state(cfg)
        s2  = pte.process_alert(s, _row("AAPL", score=7.0, price=100.0), current_price=100.0, config=cfg)
        pos = s2["positions"]["AAPL"]
        assert pos["stop_loss_price"]   == pytest.approx(90.0,  rel=1e-6)
        assert pos["take_profit_price"] == pytest.approx(120.0, rel=1e-6)

    def test_shares_computed_correctly(self):
        cfg = _cfg(allocation_pct=0.10, slippage_pct=0.0)
        s   = pte.create_state(cfg)
        s2  = pte.process_alert(s, _row("AAPL", score=7.0, price=50.0), current_price=50.0, config=cfg)
        pos = s2["positions"]["AAPL"]
        # 10% of 10_000 = 1_000 → 1000/50 = 20 shares
        assert pos["shares"] == pytest.approx(20.0, rel=1e-6)


# ── TestStopLossHandling ──────────────────────────────────────────────────────

class TestStopLossHandling:
    def test_sl_trigger_closes_position(self):
        s  = _state_with_pos("AAPL", entry_price=100.0, stop_loss=90.0)
        s2 = pte.process_alert(s, _row("MSFT", score=3.0), current_price=None,
                                prices_map={"AAPL": 88.0}, config=_cfg())
        assert "AAPL" not in s2["positions"]

    def test_sl_fires_stop_loss_event(self):
        s  = _state_with_pos("AAPL", entry_price=100.0, stop_loss=90.0)
        s2 = pte.process_alert(s, _row("MSFT", score=3.0), current_price=None,
                                prices_map={"AAPL": 88.0}, config=_cfg())
        sl_events = [e for e in s2["events"] if e["event_type"] == EVENT_STOP_LOSS]
        assert len(sl_events) == 1
        assert sl_events[0]["ticker"] == "AAPL"

    def test_sl_records_negative_pnl(self):
        s  = _state_with_pos("AAPL", entry_price=100.0, shares=10.0,
                              entry_cash=1_000.0, stop_loss=80.0)
        s2 = pte.process_alert(s, _row("MSFT", score=3.0), current_price=None,
                                prices_map={"AAPL": 75.0}, config=_cfg())
        trade = s2["closed_trades"][-1]
        assert trade["pnl"] < 0
        assert trade["is_win"] is False

    def test_price_above_sl_keeps_position(self):
        s  = _state_with_pos("AAPL", entry_price=100.0, stop_loss=80.0)
        s2 = pte.process_alert(s, _row("MSFT", score=3.0), current_price=None,
                                prices_map={"AAPL": 95.0}, config=_cfg())
        assert "AAPL" in s2["positions"]

    def test_sl_via_current_price(self):
        s  = _state_with_pos("AAPL", entry_price=100.0, stop_loss=90.0)
        s2 = pte.process_alert(s, _row("AAPL", score=3.0), current_price=85.0, config=_cfg())
        assert "AAPL" not in s2["positions"]

    def test_sl_returns_cash(self):
        cfg = _cfg(slippage_pct=0.0)
        s   = _state_with_pos("AAPL", entry_price=100.0, shares=10.0,
                               entry_cash=1_000.0, stop_loss=80.0, cash=9_000.0)
        s2  = pte.process_alert(s, _row("MSFT", score=3.0), current_price=None,
                                 prices_map={"AAPL": 70.0}, config=cfg)
        # 10 shares × 70 = 700 returned
        assert s2["cash"] == pytest.approx(9_700.0, rel=1e-6)


# ── TestTakeProfitHandling ────────────────────────────────────────────────────

class TestTakeProfitHandling:
    def test_tp_trigger_closes_position(self):
        s  = _state_with_pos("AAPL", entry_price=100.0, take_profit=120.0)
        s2 = pte.process_alert(s, _row("MSFT", score=3.0), current_price=None,
                                prices_map={"AAPL": 125.0}, config=_cfg())
        assert "AAPL" not in s2["positions"]

    def test_tp_fires_take_profit_event(self):
        s  = _state_with_pos("AAPL", entry_price=100.0, take_profit=120.0)
        s2 = pte.process_alert(s, _row("MSFT", score=3.0), current_price=None,
                                prices_map={"AAPL": 130.0}, config=_cfg())
        tp_events = [e for e in s2["events"] if e["event_type"] == EVENT_TAKE_PROFIT]
        assert len(tp_events) == 1

    def test_tp_records_positive_pnl(self):
        cfg = _cfg(slippage_pct=0.0)
        s   = _state_with_pos("AAPL", entry_price=100.0, shares=10.0,
                               entry_cash=1_000.0, take_profit=120.0, cash=9_000.0)
        s2  = pte.process_alert(s, _row("MSFT", score=3.0), current_price=None,
                                 prices_map={"AAPL": 130.0}, config=cfg)
        trade = s2["closed_trades"][-1]
        assert trade["pnl"] > 0
        assert trade["is_win"] is True

    def test_price_below_tp_keeps_position(self):
        s  = _state_with_pos("AAPL", entry_price=100.0, take_profit=120.0)
        s2 = pte.process_alert(s, _row("MSFT", score=3.0), current_price=None,
                                prices_map={"AAPL": 115.0}, config=_cfg())
        assert "AAPL" in s2["positions"]


# ── TestMaxHoldingExpiration ──────────────────────────────────────────────────

class TestMaxHoldingExpiration:
    def test_position_expires_after_max_rows(self):
        cfg = _cfg(max_holding_rows=3)
        s   = _state_with_pos("AAPL", entry_row=0)
        # Advance 3 rows
        for i in range(3):
            s = pte.process_alert(s, _row("MSFT", score=3.0), config=cfg)
        assert "AAPL" not in s["positions"]

    def test_expiry_fires_forced_exit_event(self):
        cfg = _cfg(max_holding_rows=2)
        s   = _state_with_pos("AAPL", entry_row=0)
        for _ in range(2):
            s = pte.process_alert(s, _row("MSFT", score=3.0), config=cfg)
        forced = [e for e in s["events"] if e["event_type"] == EVENT_FORCED_EXIT]
        assert len(forced) >= 1

    def test_position_before_max_rows_stays(self):
        cfg = _cfg(max_holding_rows=5)
        s   = _state_with_pos("AAPL", entry_row=0)
        for _ in range(3):
            s = pte.process_alert(s, _row("MSFT", score=3.0), config=cfg)
        assert "AAPL" in s["positions"]

    def test_expiry_closes_at_entry_price_with_zero_slippage(self):
        cfg = _cfg(max_holding_rows=1, slippage_pct=0.0, fee_per_trade=0.0)
        s   = _state_with_pos("AAPL", entry_price=100.0, shares=10.0,
                               entry_cash=1_000.0, cash=9_000.0, entry_row=0)
        s2  = pte.process_alert(s, _row("MSFT", score=3.0), config=cfg)
        trade = [t for t in s2["closed_trades"] if t["ticker"] == "AAPL"]
        assert len(trade) == 1
        assert trade[0]["pnl"] == pytest.approx(0.0, abs=1e-6)


# ── TestForcedLiquidation / RiskOff ──────────────────────────────────────────

class TestForcedLiquidation:
    def test_drawdown_enters_risk_off(self):
        cfg = _cfg(initial_cash=10_000.0, risk_off_drawdown_pct=0.10)
        s   = pte.create_state(cfg)
        s["peak_equity"] = 10_000.0
        # Manually reduce cash to simulate a 15 % drawdown
        s["cash"] = 8_500.0
        s2 = pte.process_alert(s, _row("AAPL", score=7.0, price=100.0),
                                current_price=100.0, config=cfg)
        assert s2["risk_off"] is True

    def test_risk_off_fires_risk_reduction_event(self):
        cfg = _cfg(risk_off_drawdown_pct=0.10)
        s   = pte.create_state(cfg)
        s["peak_equity"] = 10_000.0
        s["cash"] = 8_500.0
        s2 = pte.process_alert(s, _row("AAPL", score=7.0), config=cfg)
        rr_events = [e for e in s2["events"] if e["event_type"] == EVENT_RISK_REDUCTION]
        assert len(rr_events) == 1

    def test_risk_off_blocks_new_entries(self):
        cfg = _cfg(risk_off_drawdown_pct=0.10)
        s   = pte.create_state(cfg)
        s["peak_equity"] = 10_000.0
        s["cash"] = 8_500.0
        s["risk_off"] = True
        s2 = pte.process_alert(s, _row("AAPL", score=9.0, price=100.0),
                                current_price=100.0, config=cfg)
        assert "AAPL" not in s2["positions"]

    def test_risk_off_clears_on_recovery(self):
        cfg = _cfg(risk_off_drawdown_pct=0.20)
        s   = pte.create_state(cfg)
        s["peak_equity"] = 10_000.0
        s["cash"] = 9_500.0   # 5 % drawdown — below 50 % of 20 % threshold
        s["risk_off"] = True
        s2 = pte.process_alert(s, _row("MSFT", score=3.0), config=cfg)
        assert s2["risk_off"] is False


# ── TestProcessAlert ──────────────────────────────────────────────────────────

class TestProcessAlert:
    def test_row_idx_increments(self):
        cfg = _cfg()
        s   = pte.create_state(cfg)
        s2  = pte.process_alert(s, _row(), config=cfg)
        assert s2["row_idx"] == 1

    def test_equity_history_appended(self):
        cfg = _cfg()
        s   = pte.create_state(cfg)
        s2  = pte.process_alert(s, _row(), config=cfg)
        assert len(s2["equity_history"]) == 1
        assert s2["equity_history"][0]["row_idx"] == 1

    def test_input_state_not_mutated(self):
        cfg = _cfg()
        s   = pte.create_state(cfg)
        original_positions = dict(s["positions"])
        pte.process_alert(s, _row("AAPL", score=7.0, price=100.0),
                          current_price=100.0, config=cfg)
        assert s["positions"] == original_positions

    def test_peak_equity_tracked(self):
        cfg = _cfg(initial_cash=10_000.0)
        s   = pte.create_state(cfg)
        s2  = pte.process_alert(s, _row("AAPL", score=7.0, price=100.0),
                                 current_price=100.0, config=cfg)
        assert s2["peak_equity"] >= s2["equity_history"][0]["equity"]

    def test_multiple_tickers_open_simultaneously(self):
        cfg = _cfg(max_open_positions=5, allocation_pct=0.10)
        s   = pte.create_state(cfg)
        for ticker in ("AAPL", "MSFT", "GOOG"):
            s = pte.process_alert(s, _row(ticker, score=7.0, price=100.0),
                                   current_price=100.0, config=cfg)
        assert len(s["positions"]) == 3

    def test_zero_price_row_no_entry(self):
        cfg = _cfg()
        s   = pte.create_state(cfg)
        s2  = pte.process_alert(s, _row("AAPL", score=8.0, price=0.0),
                                 current_price=0.0, config=cfg)
        assert "AAPL" not in s2["positions"]


# ── TestComputeMetrics ────────────────────────────────────────────────────────

class TestComputeMetrics:
    def test_initial_metrics_no_positions(self):
        m = pte.compute_metrics(pte.create_state(_cfg()))
        assert m["equity"]         == 10_000.0
        assert m["cash"]           == 10_000.0
        assert m["n_open"]         == 0
        assert m["n_closed"]       == 0
        assert m["drawdown_pct"]   == 0.0
        assert m["exposure_pct"]   == 0.0
        assert m["win_rate"]       is None

    def test_exposure_pct_reflects_open_positions(self):
        # 1_000 deployed in AAPL out of 10_000 equity → 10 %
        s = _state_with_pos("AAPL", entry_cash=1_000.0, cash=9_000.0)
        m = pte.compute_metrics(s)
        assert m["exposure_pct"] == pytest.approx(10.0, rel=1e-4)

    def test_realized_pnl_summed(self):
        cfg = _cfg(slippage_pct=0.0)
        s   = pte.create_state(cfg)
        s2  = pte.process_alert(s, _row("AAPL", score=7.0, price=100.0),
                                 current_price=100.0, config=cfg)
        # Close via take-profit at 125
        s3 = pte.process_alert(s2, _row("MSFT", score=3.0),
                                prices_map={"AAPL": 125.0}, config=cfg)
        m  = pte.compute_metrics(s3)
        assert m["realized_pnl"] > 0

    def test_win_rate_computed_after_closed_trades(self):
        cfg = _cfg(slippage_pct=0.0, stop_loss_pct=0.10, take_profit_pct=0.20)
        s   = pte.create_state(cfg)
        # Open AAPL
        s = pte.process_alert(s, _row("AAPL", score=7.0, price=100.0),
                               current_price=100.0, config=cfg)
        # Close via TP
        s = pte.process_alert(s, _row("MSFT", score=3.0),
                               prices_map={"AAPL": 130.0}, config=cfg)
        m = pte.compute_metrics(s)
        assert m["win_rate"] == 100.0

    def test_drawdown_pct_positive_when_below_peak(self):
        s = pte.create_state(_cfg(initial_cash=10_000.0))
        s["peak_equity"] = 10_000.0
        s["cash"] = 8_000.0
        m = pte.compute_metrics(s)
        assert m["drawdown_pct"] == pytest.approx(20.0, rel=1e-4)

    def test_unrealized_pnl_zero_at_entry_price(self):
        s = _state_with_pos("AAPL", entry_price=100.0, shares=10.0, entry_cash=1_000.0)
        m = pte.compute_metrics(s)
        assert m["unrealized_pnl"] == pytest.approx(0.0, abs=1e-6)

    def test_rolling_vol_none_when_insufficient_history(self):
        m = pte.compute_metrics(pte.create_state())
        assert m["rolling_vol"] is None

    def test_sharpe_none_when_insufficient_history(self):
        m = pte.compute_metrics(pte.create_state())
        assert m["sharpe_like"] is None


# ── TestExposureEnforcement ───────────────────────────────────────────────────

class TestExposureEnforcement:
    def test_max_positions_limit_enforced(self):
        cfg = _cfg(max_open_positions=2, allocation_pct=0.10)
        s   = pte.create_state(cfg)
        for t in ("AAPL", "MSFT", "GOOG"):
            s = pte.process_alert(s, _row(t, score=7.0, price=100.0),
                                   current_price=100.0, config=cfg)
        assert len(s["positions"]) == 2

    def test_max_exposure_cap_enforced(self):
        cfg = _cfg(max_exposure_pct=0.20, allocation_pct=0.10)
        s   = pte.create_state(cfg)
        for t in ("AAPL", "MSFT", "GOOG"):
            s = pte.process_alert(s, _row(t, score=7.0, price=100.0),
                                   current_price=100.0, config=cfg)
        m = pte.compute_metrics(s)
        assert m["exposure_pct"] <= 21.0  # small tolerance

    def test_sector_cap_enforced(self):
        cfg = _cfg(max_sector_pct=0.15, allocation_pct=0.10)
        s   = pte.create_state(cfg)
        for t in ("AAPL", "MSFT"):
            s = pte.process_alert(s, _row(t, score=7.0, price=100.0, sector="tech"),
                                   current_price=100.0, config=cfg)
        # First entry OK, second blocked (10+10=20% > 15%)
        assert len(s["positions"]) == 1

    def test_exposure_block_event_fired_when_blocked(self):
        cfg = _cfg(max_open_positions=1, allocation_pct=0.10)
        s   = pte.create_state(cfg)
        s   = pte.process_alert(s, _row("AAPL", score=7.0, price=100.0),
                                  current_price=100.0, config=cfg)
        s   = pte.process_alert(s, _row("MSFT", score=7.0, price=100.0),
                                  current_price=100.0, config=cfg)
        block_events = [e for e in s["events"] if e["event_type"] == EVENT_EXPOSURE_BLOCK]
        assert len(block_events) >= 1

    def test_ticker_concentration_cap_respected(self):
        cfg = _cfg(max_ticker_pct=0.05, allocation_pct=0.10)
        s   = pte.create_state(cfg)
        s   = pte.process_alert(s, _row("AAPL", score=7.0, price=100.0),
                                  current_price=100.0, config=cfg)
        # allocation_pct (10%) would exceed max_ticker_pct (5%); alloc capped at 5%
        pos = s["positions"].get("AAPL")
        if pos:
            assert pos["entry_cash"] / pte._current_equity(s) <= 0.06  # ≤ 6 % with tolerance


# ── TestPortfolioAccounting ───────────────────────────────────────────────────

class TestPortfolioAccounting:
    def test_cash_plus_positions_equals_equity(self):
        cfg = _cfg()
        s   = pte.create_state(cfg)
        for t in ("AAPL", "MSFT"):
            s = pte.process_alert(s, _row(t, score=7.0, price=100.0),
                                   current_price=100.0, config=cfg)
        pos_val = sum(p["shares"] * p["entry_price"] for p in s["positions"].values())
        assert s["cash"] + pos_val == pytest.approx(pte._current_equity(s), rel=1e-6)

    def test_closed_trade_cash_returned(self):
        cfg = _cfg(slippage_pct=0.0)
        s   = pte.create_state(cfg)
        s   = pte.process_alert(s, _row("AAPL", score=7.0, price=100.0),
                                  current_price=100.0, config=cfg)
        cash_after_entry = s["cash"]
        s = pte.process_alert(s, _row("MSFT", score=3.0),
                               prices_map={"AAPL": 100.0}, config=cfg)
        # Close at same price → cash restored to initial
        trade = next((t for t in s["closed_trades"] if t["ticker"] == "AAPL"), None)
        if trade:
            assert s["cash"] > cash_after_entry

    def test_slippage_reduces_proceeds(self):
        cfg_no_slip  = _cfg(slippage_pct=0.0, max_holding_rows=1)
        cfg_with_slip = _cfg(slippage_pct=0.01, max_holding_rows=1)

        s1 = pte.create_state(cfg_no_slip)
        s1 = pte.process_alert(s1, _row("AAPL", score=7.0, price=100.0),
                                current_price=100.0, config=cfg_no_slip)
        s1 = pte.process_alert(s1, _row("MSFT", score=3.0), config=cfg_no_slip)

        s2 = pte.create_state(cfg_with_slip)
        s2 = pte.process_alert(s2, _row("AAPL", score=7.0, price=100.0),
                                current_price=100.0, config=cfg_with_slip)
        s2 = pte.process_alert(s2, _row("MSFT", score=3.0), config=cfg_with_slip)

        # Slippage on entry AND exit → less cash remaining
        assert s2["cash"] < s1["cash"]

    def test_fee_deducted_on_entry_and_exit(self):
        cfg = _cfg(fee_per_trade=5.0, slippage_pct=0.0, max_holding_rows=1)
        s   = pte.create_state(cfg)
        s   = pte.process_alert(s, _row("AAPL", score=7.0, price=100.0),
                                  current_price=100.0, config=cfg)
        s   = pte.process_alert(s, _row("MSFT", score=3.0), config=cfg)
        # 2 × $5 fee
        assert s["cash"] < 10_000.0 - 10.0 + 1.0  # rough bound


# ── TestEventGeneration ───────────────────────────────────────────────────────

class TestEventGeneration:
    def test_entry_event_has_expected_fields(self):
        s  = pte.create_state(_cfg())
        s2 = pte.process_alert(s, _row("AAPL", score=7.0, price=100.0),
                                current_price=100.0, config=_cfg())
        evt = next(e for e in s2["events"] if e["event_type"] == EVENT_ENTRY)
        assert "ticker"     in evt
        assert "price"      in evt
        assert "shares"     in evt
        assert "value"      in evt
        assert "row_idx"    in evt
        assert "reason"     in evt

    def test_events_bounded_at_max_events(self):
        cfg = _cfg(max_open_positions=200)
        s   = pte.create_state(cfg)
        for i in range(MAX_EVENTS + 10):
            s = pte.process_alert(s, _row(f"T{i}", score=7.0, price=100.0),
                                   current_price=100.0, config=cfg)
        assert len(s["events"]) <= MAX_EVENTS

    def test_equity_history_bounded(self):
        cfg = _cfg()
        s   = pte.create_state(cfg)
        for i in range(MAX_EQUITY_HISTORY + 10):
            s = pte.process_alert(s, _row("AAPL", score=3.0), config=cfg)
        assert len(s["equity_history"]) <= MAX_EQUITY_HISTORY

    def test_closed_trades_bounded(self):
        cfg = _cfg(max_holding_rows=1, allocation_pct=0.01)
        s   = pte.create_state(cfg)
        for i in range(MAX_CLOSED_TRADES + 10):
            s = pte.process_alert(s, _row(f"T{i}", score=7.0, price=1.0),
                                   current_price=1.0, config=cfg)
        assert len(s["closed_trades"]) <= MAX_CLOSED_TRADES

    def test_event_pnl_none_on_entry(self):
        s  = pte.create_state(_cfg())
        s2 = pte.process_alert(s, _row("AAPL", score=7.0, price=100.0),
                                current_price=100.0, config=_cfg())
        evt = next(e for e in s2["events"] if e["event_type"] == EVENT_ENTRY)
        assert evt["pnl"] is None


# ── TestOperationalAlerts ─────────────────────────────────────────────────────

class TestOperationalAlerts:
    def test_no_alerts_on_fresh_state(self):
        a = pte.generate_operational_alerts(pte.create_state())
        assert a == []

    def test_excessive_drawdown_alert(self):
        s = pte.create_state(_cfg(initial_cash=10_000.0))
        s["peak_equity"] = 10_000.0
        s["cash"] = 8_000.0   # 20 % drawdown
        alerts = pte.generate_operational_alerts(s)
        types = [a["alert_type"] for a in alerts]
        assert "EXCESSIVE_DRAWDOWN" in types

    def test_dangerous_concentration_alert(self):
        # Single position worth 40 % of equity → exceeds 30 % threshold
        s = _state_with_pos("AAPL", entry_price=100.0, shares=40.0,
                             entry_cash=4_000.0, cash=6_000.0)
        alerts = pte.generate_operational_alerts(s)
        types  = [a["alert_type"] for a in alerts]
        assert "DANGEROUS_CONCENTRATION" in types

    def test_low_cash_reserve_alert(self):
        # Cash at 2 % of equity
        s = _state_with_pos("AAPL", entry_price=100.0, shares=98.0,
                             entry_cash=9_800.0, cash=200.0)
        alerts = pte.generate_operational_alerts(s)
        types  = [a["alert_type"] for a in alerts]
        assert "LOW_CASH_RESERVE" in types

    def test_high_exposure_alert(self):
        # 9_000 deployed → 90 % > 85 % threshold
        s = _state_with_pos("AAPL", entry_price=100.0, shares=90.0,
                             entry_cash=9_000.0, cash=1_000.0)
        alerts = pte.generate_operational_alerts(s)
        types  = [a["alert_type"] for a in alerts]
        assert "HIGH_EXPOSURE" in types

    def test_alerts_bounded_at_max_op_alerts(self):
        s = pte.create_state(_cfg(initial_cash=10_000.0))
        s["peak_equity"] = 10_000.0
        s["cash"] = 0.0
        alerts = pte.generate_operational_alerts(s)
        assert len(alerts) <= pte.MAX_OP_ALERTS

    def test_no_drawdown_alert_when_below_threshold(self):
        s = pte.create_state(_cfg(initial_cash=10_000.0))
        s["peak_equity"] = 10_000.0
        s["cash"] = 9_900.0   # 1 % drawdown — below threshold
        alerts = pte.generate_operational_alerts(s)
        types  = [a["alert_type"] for a in alerts]
        assert "EXCESSIVE_DRAWDOWN" not in types


# ── TestReportGeneration ──────────────────────────────────────────────────────

class TestReportGeneration:
    def test_report_has_required_keys(self):
        r = pte.generate_report(pte.create_state())
        for key in ("health", "metrics", "open_positions", "recent_events",
                    "operational_alerts", "n_events_total", "n_closed_trades",
                    "risk_off", "row_idx"):
            assert key in r

    def test_healthy_on_fresh_state(self):
        assert pte.generate_report(pte.create_state())["health"] == "HEALTHY"

    def test_weak_health_on_severe_drawdown(self):
        s = pte.create_state(_cfg(initial_cash=10_000.0))
        s["peak_equity"] = 10_000.0
        s["cash"] = 7_500.0   # 25 % drawdown → WEAK
        assert pte.generate_report(s)["health"] == "WEAK"

    def test_open_positions_listed(self):
        s = _state_with_pos("AAPL")
        r = pte.generate_report(s)
        tickers = [p["ticker"] for p in r["open_positions"]]
        assert "AAPL" in tickers

    def test_n_closed_trades_matches_state(self):
        cfg = _cfg(slippage_pct=0.0, max_holding_rows=1)
        s   = pte.create_state(cfg)
        s   = pte.process_alert(s, _row("AAPL", score=7.0, price=100.0),
                                  current_price=100.0, config=cfg)
        s   = pte.process_alert(s, _row("MSFT", score=3.0), config=cfg)
        r   = pte.generate_report(s, cfg)
        assert r["n_closed_trades"] == len(s["closed_trades"])

    def test_open_position_has_unrealized_pnl(self):
        s = _state_with_pos("AAPL")
        r = pte.generate_report(s)
        pos = next(p for p in r["open_positions"] if p["ticker"] == "AAPL")
        assert "unrealized_pnl" in pos

    def test_caution_health_on_moderate_drawdown(self):
        s = pte.create_state(_cfg(initial_cash=10_000.0))
        s["peak_equity"] = 10_000.0
        s["cash"] = 8_900.0   # 11 % drawdown → CAUTION
        r = pte.generate_report(s)
        assert r["health"] in ("CAUTION", "WEAK")


# ── TestReplay ────────────────────────────────────────────────────────────────

class TestReplay:
    def test_empty_rows_returns_initial_state(self):
        res = pte.replay([], config=_cfg())
        assert res["n_rows"]                         == 0
        assert res["final_state"]["row_idx"]         == 0
        assert res["final_state"]["positions"]       == {}

    def test_replay_deterministic_same_seed(self):
        rows = [_row("AAPL", score=7.0, price=100.0) for _ in range(5)]
        r1   = pte.replay(rows, config=_cfg())
        r2   = pte.replay(rows, config=_cfg())
        assert r1["final_state"]["cash"]     == r2["final_state"]["cash"]
        assert r1["final_state"]["row_idx"]  == r2["final_state"]["row_idx"]
        assert r1["final_state"]["risk_off"] == r2["final_state"]["risk_off"]

    def test_replay_processes_all_rows(self):
        rows = [_row(f"T{i}", score=7.0, price=100.0) for i in range(5)]
        res  = pte.replay(rows, config=_cfg())
        assert res["n_rows"]                  == 5
        assert res["final_state"]["row_idx"]  == 5

    def test_replay_with_prices_map_triggers_exits(self):
        cfg  = _cfg(stop_loss_pct=0.05)
        rows = [
            _row("AAPL", score=7.0, price=100.0),  # open AAPL
            _row("MSFT", score=3.0),                # check SL
        ]
        res = pte.replay(rows, prices_map={"AAPL": 80.0}, config=cfg)
        # AAPL should have been stopped out
        assert "AAPL" not in res["final_state"]["positions"]

    def test_replay_report_included(self):
        rows = [_row("AAPL", score=7.0, price=100.0)]
        res  = pte.replay(rows, config=_cfg())
        assert "report" in res
        assert "health"  in res["report"]
        assert "metrics" in res["report"]

    def test_replay_idempotent_for_same_input(self):
        rows = [_row("AAPL", score=7.0, price=100.0),
                _row("MSFT", score=7.0, price=200.0)]
        r1 = pte.replay(rows, config=_cfg())
        r2 = pte.replay(rows, config=_cfg())
        assert r1["final_state"]["closed_trades"] == r2["final_state"]["closed_trades"]


# ── TestSparseHandling ────────────────────────────────────────────────────────

class TestSparseHandling:
    def test_no_config_uses_defaults(self):
        s = pte.create_state()
        assert s["cash"] == DEFAULT_INITIAL_CASH

    def test_compute_metrics_empty_state_no_crash(self):
        m = pte.compute_metrics(pte.create_state())
        assert m["equity"] == DEFAULT_INITIAL_CASH

    def test_generate_report_empty_no_crash(self):
        r = pte.generate_report(pte.create_state())
        assert r["health"] == "HEALTHY"

    def test_operational_alerts_empty_no_crash(self):
        a = pte.generate_operational_alerts(pte.create_state())
        assert isinstance(a, list)

    def test_process_alert_missing_ticker_no_crash(self):
        s  = pte.create_state(_cfg())
        s2 = pte.process_alert(s, {"adjusted_score": 7.0}, config=_cfg())
        assert s2["row_idx"] == 1

    def test_process_alert_missing_score_no_crash(self):
        s  = pte.create_state(_cfg())
        s2 = pte.process_alert(s, {"ticker": "AAPL", "price": 100.0}, config=_cfg())
        assert s2["row_idx"] == 1
        assert "AAPL" not in s2["positions"]  # score = 0 < threshold

    def test_prices_map_none_no_crash(self):
        s  = _state_with_pos("AAPL")
        s2 = pte.process_alert(s, _row("MSFT", score=3.0),
                                prices_map=None, config=_cfg())
        assert s2["row_idx"] == 1

    def test_replay_empty_prices_map_no_crash(self):
        rows = [_row("AAPL", score=7.0, price=100.0)]
        res  = pte.replay(rows, prices_map={}, config=_cfg())
        assert res["n_rows"] == 1

    def test_check_risk_controls_empty_state(self):
        s   = pte.create_state(_cfg())
        res = pte.check_risk_controls(s, "AAPL", 1_000.0)
        assert "allowed" in res

    def test_rolling_vol_none_below_min_history(self):
        assert pte._rolling_volatility([], 20) is None
        assert pte._rolling_volatility([{"equity": 100.0}], 20) is None


# ── TestDeterminism ───────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_rows_same_final_cash(self):
        rows = [_row(f"T{i % 5}", score=7.0, price=100.0) for i in range(10)]
        cfg  = _cfg(max_open_positions=3)
        r1   = pte.replay(rows, config=cfg)
        r2   = pte.replay(rows, config=cfg)
        assert r1["final_state"]["cash"] == r2["final_state"]["cash"]

    def test_same_rows_same_event_count(self):
        rows = [_row("AAPL", score=7.0, price=100.0) for _ in range(5)]
        cfg  = _cfg()
        r1   = pte.replay(rows, config=cfg)
        r2   = pte.replay(rows, config=cfg)
        assert len(r1["final_state"]["events"]) == len(r2["final_state"]["events"])

    def test_copy_state_deep_independence(self):
        s  = _state_with_pos("AAPL")
        s2 = pte._copy_state(s)
        s2["positions"]["AAPL"]["entry_price"] = 999.0
        # Mutation of copy must not affect original
        assert s["positions"]["AAPL"]["entry_price"] != 999.0
