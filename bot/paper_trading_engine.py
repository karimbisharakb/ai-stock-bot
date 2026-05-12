"""
Real-time paper trading engine for the Predator scanner.
Phase 5B — simulation only; no live brokerage, no real-money execution.

Processes alert rows one at a time and maintains persistent paper portfolio
state.  All public functions are pure: they accept state dicts and return
new state dicts without mutating inputs.  No DB access.  No network calls.
Replay-safe: the same row stream produces identical results every run.

Key capabilities
----------------
  create_state                  initialise empty portfolio
  process_alert                 advance state one alert row at a time
  check_risk_controls           enforce exposure / concentration / position limits
  compute_metrics               drawdown, Sharpe-like, exposure, win rate, vol
  generate_operational_alerts   drawdown / concentration / cash / exposure warnings
  generate_report               full portfolio snapshot
  replay                        deterministic batch replay over historical stream

Position lifecycle
------------------
  ENTRY          alert passes filters → fill simulated with slippage + fee
  EXIT           explicit signal close
  STOP_LOSS      current price ≤ stop_loss_price
  TAKE_PROFIT    current price ≥ take_profit_price
  FORCED_EXIT    max holding rows elapsed or risk-off liquidation
  EXPOSURE_BLOCK risk controls prevented entry
  RISK_REDUCTION risk-off mode entered; new entries blocked

Risk controls (all configurable via EngineConfig)
-------------
  max_exposure_pct    total position value / equity ceiling
  max_ticker_pct      single-ticker value / equity ceiling
  max_sector_pct      single-sector value / equity ceiling
  max_open_positions  simultaneous open position count
  risk_off_drawdown_pct  drawdown threshold → risk-off mode
"""
import logging
import math
from typing import NamedTuple, Optional

from shadow_adaptive import ALERT_THRESHOLD

log = logging.getLogger(__name__)

# ── Event types ───────────────────────────────────────────────────────────────

EVENT_ENTRY          = "ENTRY"
EVENT_EXIT           = "EXIT"
EVENT_STOP_LOSS      = "STOP_LOSS"
EVENT_TAKE_PROFIT    = "TAKE_PROFIT"
EVENT_FORCED_EXIT    = "FORCED_EXIT"
EVENT_EXPOSURE_BLOCK = "EXPOSURE_BLOCK"
EVENT_RISK_REDUCTION = "RISK_REDUCTION"

# ── Configuration defaults ────────────────────────────────────────────────────

DEFAULT_INITIAL_CASH:          float = 10_000.0
DEFAULT_SLIPPAGE_PCT:          float =  0.001      # 0.1 % per fill
DEFAULT_FEE_PER_TRADE:         float =  0.0
DEFAULT_MAX_EXPOSURE_PCT:      float =  0.90       # ≤ 90 % equity deployed
DEFAULT_MAX_TICKER_PCT:        float =  0.20       # ≤ 20 % equity per ticker
DEFAULT_MAX_SECTOR_PCT:        float =  0.40       # ≤ 40 % equity per sector
DEFAULT_MAX_OPEN_POSITIONS:    int   = 10
DEFAULT_STOP_LOSS_PCT:         float =  0.08       # 8 % below entry fill
DEFAULT_TAKE_PROFIT_PCT:       float =  0.20       # 20 % above entry fill
DEFAULT_MAX_HOLDING_ROWS:      int   = 20          # forced exit after N rows
DEFAULT_ALLOCATION_PCT:        float =  0.10       # 10 % of equity per position
DEFAULT_MIN_SCORE_TO_ENTER:    float = ALERT_THRESHOLD
DEFAULT_RISK_OFF_DRAWDOWN_PCT: float =  0.15       # 15 % drawdown → risk-off

# ── Operational alert thresholds ──────────────────────────────────────────────

ALERT_DRAWDOWN_PCT:         float = 15.0
ALERT_CONCENTRATION_PCT:    float = 30.0    # single ticker > 30 % of equity
ALERT_CASH_RESERVE_PCT:     float =  5.0    # cash < 5 % of equity
ALERT_VOL_SPIKE_MULTIPLIER: float =  2.0    # rolling vol > 2 × prior-period avg
ALERT_EXPOSURE_HIGH_PCT:    float = 85.0    # deployed > 85 %

# ── Bounded collection sizes ──────────────────────────────────────────────────

MAX_EVENTS:         int = 200
MAX_EQUITY_HISTORY: int = 500
MAX_CLOSED_TRADES:  int = 500
MAX_REPORT_EVENTS:  int =  20
MAX_OP_ALERTS:      int =  10

# ── Portfolio health thresholds ───────────────────────────────────────────────

HEALTH_DRAWDOWN_HEALTHY:   float = 10.0
HEALTH_DRAWDOWN_CAUTION:   float = 20.0
HEALTH_WIN_RATE_HEALTHY:   float = 55.0
HEALTH_WIN_RATE_CAUTION:   float = 45.0
MIN_TRADES_FOR_STATS:      int   =  5


# ── EngineConfig ──────────────────────────────────────────────────────────────

class EngineConfig(NamedTuple):
    """Immutable configuration for one paper-trading simulation run."""
    initial_cash:          float = DEFAULT_INITIAL_CASH
    slippage_pct:          float = DEFAULT_SLIPPAGE_PCT
    fee_per_trade:         float = DEFAULT_FEE_PER_TRADE
    max_exposure_pct:      float = DEFAULT_MAX_EXPOSURE_PCT
    max_ticker_pct:        float = DEFAULT_MAX_TICKER_PCT
    max_sector_pct:        float = DEFAULT_MAX_SECTOR_PCT
    max_open_positions:    int   = DEFAULT_MAX_OPEN_POSITIONS
    stop_loss_pct:         float = DEFAULT_STOP_LOSS_PCT
    take_profit_pct:       float = DEFAULT_TAKE_PROFIT_PCT
    max_holding_rows:      int   = DEFAULT_MAX_HOLDING_ROWS
    allocation_pct:        float = DEFAULT_ALLOCATION_PCT
    min_score_to_enter:    float = DEFAULT_MIN_SCORE_TO_ENTER
    risk_off_drawdown_pct: float = DEFAULT_RISK_OFF_DRAWDOWN_PCT


# ── State creation ────────────────────────────────────────────────────────────

def create_state(config: Optional[EngineConfig] = None) -> dict:
    """Return a fresh, empty portfolio state dict."""
    cfg = config or EngineConfig()
    return {
        "cash":           cfg.initial_cash,
        "positions":      {},          # ticker → position dict
        "closed_trades":  [],
        "events":         [],
        "equity_history": [],
        "row_idx":        0,
        "peak_equity":    cfg.initial_cash,
        "risk_off":       False,
    }


# ── Internal state helpers ────────────────────────────────────────────────────

def _copy_state(state: dict) -> dict:
    return {
        "cash":           state["cash"],
        "positions":      {k: dict(v) for k, v in state["positions"].items()},
        "closed_trades":  list(state["closed_trades"]),
        "events":         list(state["events"]),
        "equity_history": list(state["equity_history"]),
        "row_idx":        state["row_idx"],
        "peak_equity":    state["peak_equity"],
        "risk_off":       state["risk_off"],
    }


def _position_value(pos: dict, current_price: Optional[float] = None) -> float:
    price = current_price if current_price is not None else pos["entry_price"]
    return pos["shares"] * price


def _current_equity(
    state: dict,
    prices_map: Optional[dict] = None,
) -> float:
    pm = prices_map or {}
    pos_value = sum(
        _position_value(p, pm.get(t))
        for t, p in state["positions"].items()
    )
    return state["cash"] + pos_value


def _make_event(
    event_type: str,
    ticker:     str,
    price:      float,
    shares:     float,
    value:      float,
    pnl:        Optional[float],
    row_idx:    int,
    reason:     str,
) -> dict:
    return {
        "event_type": event_type,
        "ticker":     ticker,
        "price":      round(price, 6),
        "shares":     round(shares, 6),
        "value":      round(value, 4),
        "pnl":        round(pnl, 4) if pnl is not None else None,
        "row_idx":    row_idx,
        "reason":     reason,
    }


# ── Fill simulation ───────────────────────────────────────────────────────────

def _entry_fill(entry_price: float, slippage_pct: float) -> float:
    """Buy fill: price rises by slippage."""
    return round(entry_price * (1.0 + slippage_pct), 6)


def _exit_fill(exit_price: float, slippage_pct: float) -> float:
    """Sell fill: price falls by slippage."""
    return round(exit_price * (1.0 - slippage_pct), 6)


# ── Position management ───────────────────────────────────────────────────────

_EXIT_EVENT_TYPE = {
    "STOP_LOSS":   EVENT_STOP_LOSS,
    "TAKE_PROFIT": EVENT_TAKE_PROFIT,
    "MAX_HOLDING": EVENT_FORCED_EXIT,
    "RISK_OFF":    EVENT_FORCED_EXIT,
    "FORCED":      EVENT_FORCED_EXIT,
}


def _close_position(
    state:      dict,
    ticker:     str,
    exit_price: float,
    row_idx:    int,
    reason:     str,
    config:     EngineConfig,
) -> tuple:
    """
    Close one open position.  Returns (new_state, trade_dict).
    Appends the exit event to new_state["events"] and the trade to
    new_state["closed_trades"].
    """
    new = _copy_state(state)
    pos = new["positions"].pop(ticker)

    fill  = _exit_fill(exit_price, config.slippage_pct)
    procs = round(pos["shares"] * fill - config.fee_per_trade, 4)
    pnl   = round(procs - pos["entry_cash"], 4)
    ret   = round(pnl / pos["entry_cash"] * 100.0, 4) if pos["entry_cash"] > 0 else 0.0

    new["cash"] = round(new["cash"] + procs, 4)

    trade = {
        "ticker":       ticker,
        "sector":       pos.get("sector", ""),
        "entry_price":  pos["entry_price"],
        "exit_price":   exit_price,
        "fill_price":   fill,
        "shares":       pos["shares"],
        "entry_cash":   pos["entry_cash"],
        "exit_cash":    procs,
        "pnl":          pnl,
        "return_pct":   ret,
        "entry_row":    pos["entry_row"],
        "exit_row":     row_idx,
        "holding_rows": row_idx - pos["entry_row"],
        "exit_reason":  reason,
        "is_win":       pnl > 0,
    }

    evt_type = _EXIT_EVENT_TYPE.get(reason, EVENT_EXIT)
    evt = _make_event(evt_type, ticker, exit_price, pos["shares"], procs, pnl, row_idx, reason)

    new["events"]        = (new["events"]        + [evt])[-MAX_EVENTS:]
    new["closed_trades"] = (new["closed_trades"] + [trade])[-MAX_CLOSED_TRADES:]

    log.info(
        "paper_trading: %s %s exit=%.4f pnl=%.2f reason=%s",
        evt_type, ticker, exit_price, pnl, reason,
    )
    return new, trade


def check_risk_controls(
    state:         dict,
    ticker:        str,
    proposed_alloc: float,
    sector:        str         = "",
    config:        EngineConfig = None,
) -> dict:
    """
    Validate all risk constraints for a proposed entry.
    Returns {allowed: bool, blockers: [str]}.
    """
    cfg      = config or EngineConfig()
    blockers = []
    equity   = _current_equity(state)

    if equity <= 0:
        return {"allowed": False, "blockers": ["equity_zero"]}

    if ticker in state["positions"]:
        blockers.append(f"ticker_open:{ticker}")

    if len(state["positions"]) >= cfg.max_open_positions:
        blockers.append(f"max_positions:{cfg.max_open_positions}")

    ticker_val = _position_value(state["positions"][ticker]) if ticker in state["positions"] else 0.0
    if (proposed_alloc + ticker_val) / equity > cfg.max_ticker_pct:
        blockers.append(
            f"ticker_concentration:{round((proposed_alloc + ticker_val) / equity * 100, 1)}%"
        )

    if sector:
        sector_val = sum(
            _position_value(p)
            for p in state["positions"].values()
            if p.get("sector") == sector
        )
        if (sector_val + proposed_alloc) / equity > cfg.max_sector_pct:
            blockers.append(
                f"sector_concentration:{sector}:"
                f"{round((sector_val + proposed_alloc) / equity * 100, 1)}%"
            )

    deployed = sum(_position_value(p) for p in state["positions"].values())
    if (deployed + proposed_alloc) / equity > cfg.max_exposure_pct:
        blockers.append(
            f"max_exposure:{round((deployed + proposed_alloc) / equity * 100, 1)}%"
        )

    if proposed_alloc > state["cash"]:
        blockers.append(f"insufficient_cash:{round(state['cash'], 2)}")

    return {"allowed": len(blockers) == 0, "blockers": blockers}


def _try_entry(
    state:         dict,
    row:           dict,
    current_price: Optional[float],
    sector:        str,
    config:        EngineConfig,
) -> tuple:
    """
    Attempt a new position entry.  Returns (new_state, [event]) — event list
    is empty if the row is filtered out silently (below score threshold), and
    contains an EXPOSURE_BLOCK event if a risk control blocked entry.
    """
    ticker = row.get("ticker", "")
    score  = float(row.get("adjusted_score") or row.get("score") or 0.0)

    if score < config.min_score_to_enter or not ticker:
        return state, []

    price = current_price if current_price is not None else float(row.get("price") or 100.0)
    if price <= 0:
        return state, []

    equity = _current_equity(state)
    alloc  = min(
        config.allocation_pct * equity,
        config.max_ticker_pct * equity,
        state["cash"] * 0.95,
    )
    if alloc <= 0:
        return state, []

    risk = check_risk_controls(state, ticker, alloc, sector, config)
    if not risk["allowed"]:
        evt = _make_event(
            EVENT_EXPOSURE_BLOCK, ticker, price, 0.0, round(alloc, 4),
            None, state["row_idx"], ";".join(risk["blockers"]),
        )
        new = _copy_state(state)
        new["events"] = (new["events"] + [evt])[-MAX_EVENTS:]
        log.info("paper_trading: EXPOSURE_BLOCK %s — %s", ticker, ";".join(risk["blockers"]))
        return new, [evt]

    fill_price = _entry_fill(price, config.slippage_pct)
    shares     = alloc / fill_price
    cost       = round(shares * fill_price + config.fee_per_trade, 4)

    pos = {
        "ticker":            ticker,
        "sector":            sector,
        "entry_price":       price,
        "fill_price":        fill_price,
        "shares":            round(shares, 6),
        "entry_cash":        cost,
        "entry_row":         state["row_idx"],
        "stop_loss_price":   round(fill_price * (1.0 - config.stop_loss_pct),  6),
        "take_profit_price": round(fill_price * (1.0 + config.take_profit_pct), 6),
    }

    new              = _copy_state(state)
    new["cash"]      = round(new["cash"] - cost, 4)
    new["positions"][ticker] = pos

    evt = _make_event(
        EVENT_ENTRY, ticker, price, round(shares, 6), round(alloc, 4),
        None, state["row_idx"], f"score={score:.1f}",
    )
    new["events"] = (new["events"] + [evt])[-MAX_EVENTS:]

    log.info(
        "paper_trading: ENTRY %s at %.4f shares=%.4f cost=%.2f",
        ticker, price, shares, cost,
    )
    return new, [evt]


def _check_sl_tp(
    state:         dict,
    ticker:        str,
    current_price: float,
    config:        EngineConfig,
) -> dict:
    if ticker not in state["positions"]:
        return state
    pos = state["positions"][ticker]
    if current_price <= pos["stop_loss_price"]:
        new, _ = _close_position(state, ticker, current_price, state["row_idx"], "STOP_LOSS", config)
        return new
    if current_price >= pos["take_profit_price"]:
        new, _ = _close_position(state, ticker, current_price, state["row_idx"], "TAKE_PROFIT", config)
        return new
    return state


def _check_sl_tp_all(
    state:      dict,
    prices_map: dict,
    config:     EngineConfig,
) -> dict:
    new = state
    for ticker, price in prices_map.items():
        if ticker in new["positions"]:
            new = _check_sl_tp(new, ticker, price, config)
    return new


def _check_max_holding(state: dict, config: EngineConfig) -> dict:
    expired = [
        t for t, p in state["positions"].items()
        if state["row_idx"] - p["entry_row"] >= config.max_holding_rows
    ]
    new = state
    for ticker in expired:
        if ticker in new["positions"]:
            pos = new["positions"][ticker]
            new, _ = _close_position(
                new, ticker, pos["entry_price"],
                new["row_idx"], "MAX_HOLDING", config,
            )
    return new


def _check_risk_off(state: dict, config: EngineConfig) -> dict:
    equity = _current_equity(state)
    if equity <= 0 or state["peak_equity"] <= 0:
        return state

    drawdown = (state["peak_equity"] - equity) / state["peak_equity"]

    if drawdown >= config.risk_off_drawdown_pct and not state["risk_off"]:
        new = _copy_state(state)
        new["risk_off"] = True
        evt = _make_event(
            EVENT_RISK_REDUCTION, "", 0.0, 0.0, 0.0,
            None, state["row_idx"], f"drawdown={drawdown:.1%}",
        )
        new["events"] = (new["events"] + [evt])[-MAX_EVENTS:]
        log.warning(
            "paper_trading: RISK_OFF entered drawdown=%.1f%%", drawdown * 100
        )
        return new

    if drawdown < config.risk_off_drawdown_pct * 0.5 and state["risk_off"]:
        new = _copy_state(state)
        new["risk_off"] = False
        return new

    return state


# ── Main processing loop ──────────────────────────────────────────────────────

def process_alert(
    state:         dict,
    row:           dict,
    current_price: Optional[float]  = None,
    prices_map:    Optional[dict]   = None,
    sector:        str              = "",
    config:        Optional[EngineConfig] = None,
) -> dict:
    """
    Advance portfolio state by one alert row.

    Order of operations per row:
      1. Check SL/TP for all positions (prices_map)
      2. Check SL/TP for current row ticker (current_price)
      3. Check max-holding expiration
      4. Update peak equity
      5. Check risk-off mode
      6. Attempt entry (skipped in risk-off)
      7. Record equity snapshot

    Returns a new state dict; input state is never mutated.
    """
    cfg = config or EngineConfig()
    new = _copy_state(state)
    new["row_idx"] += 1

    pm = prices_map or {}

    if pm:
        new = _check_sl_tp_all(new, pm, cfg)

    ticker = row.get("ticker", "")
    if ticker and current_price is not None and ticker in new["positions"]:
        new = _check_sl_tp(new, ticker, current_price, cfg)

    new = _check_max_holding(new, cfg)

    eq_pre = _current_equity(new, pm)
    if eq_pre > new["peak_equity"]:
        new["peak_equity"] = eq_pre

    new = _check_risk_off(new, cfg)

    effective_sector = sector or row.get("sector", "")
    if not new["risk_off"]:
        new, _ = _try_entry(new, row, current_price, effective_sector, cfg)

    eq = _current_equity(new, pm)
    if eq > new["peak_equity"]:
        new["peak_equity"] = eq

    snap = {
        "row_idx": new["row_idx"],
        "equity":  round(eq, 4),
        "cash":    round(new["cash"], 4),
        "n_open":  len(new["positions"]),
    }
    new["equity_history"] = (new["equity_history"] + [snap])[-MAX_EQUITY_HISTORY:]

    return new


# ── Analytics ─────────────────────────────────────────────────────────────────

def _rolling_returns(equity_history: list, n: int = 20) -> list:
    hist = equity_history[-(n + 1):]
    out  = []
    for i in range(1, len(hist)):
        prev = hist[i - 1]["equity"]
        curr = hist[i]["equity"]
        if prev > 0:
            out.append((curr - prev) / prev)
    return out


def _rolling_volatility(equity_history: list, n: int = 20) -> Optional[float]:
    rets = _rolling_returns(equity_history, n)
    if len(rets) < 3:
        return None
    mean = sum(rets) / len(rets)
    var  = sum((r - mean) ** 2 for r in rets) / len(rets)
    return round(math.sqrt(var) * 100.0, 4)


def _sharpe_like(equity_history: list, n: int = 40) -> Optional[float]:
    rets = _rolling_returns(equity_history, n)
    if len(rets) < 5:
        return None
    mean = sum(rets) / len(rets)
    std  = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets))
    if std == 0:
        return None
    return round(mean / std, 4)


def compute_metrics(
    state:      dict,
    prices_map: Optional[dict] = None,
) -> dict:
    """
    Compute live portfolio metrics.  Positions are marked at current_price if
    available in prices_map, otherwise at entry_price.
    """
    pm     = prices_map or {}
    equity = _current_equity(state, pm)
    cash   = state["cash"]
    closed = state["closed_trades"]
    hist   = state["equity_history"]

    peak = state["peak_equity"]
    drawdown_pct = ((peak - equity) / peak * 100.0) if peak > 0 else 0.0

    pos_value    = equity - cash
    exposure_pct = (pos_value / equity * 100.0) if equity > 0 else 0.0

    n_closed = len(closed)
    n_wins   = sum(1 for t in closed if t.get("is_win"))
    win_rate = (n_wins / n_closed * 100.0) if n_closed > 0 else None

    realized_pnl   = sum(t.get("pnl", 0.0) for t in closed)
    unrealized_pnl = sum(
        _position_value(p, pm.get(t)) - p["entry_cash"]
        for t, p in state["positions"].items()
    )

    return {
        "equity":         round(equity, 4),
        "cash":           round(cash,   4),
        "n_open":         len(state["positions"]),
        "n_closed":       n_closed,
        "realized_pnl":   round(realized_pnl,   4),
        "unrealized_pnl": round(unrealized_pnl, 4),
        "total_pnl":      round(realized_pnl + unrealized_pnl, 4),
        "drawdown_pct":   round(drawdown_pct,  4),
        "exposure_pct":   round(exposure_pct,  4),
        "win_rate":       round(win_rate, 2) if win_rate is not None else None,
        "rolling_vol":    _rolling_volatility(hist, 20),
        "sharpe_like":    _sharpe_like(hist, 40),
        "row_idx":        state["row_idx"],
        "risk_off":       state["risk_off"],
    }


# ── Portfolio health ──────────────────────────────────────────────────────────

def _portfolio_health(metrics: dict) -> str:
    dd = metrics.get("drawdown_pct", 0.0)
    wr = metrics.get("win_rate")
    n  = metrics.get("n_closed", 0)

    if dd >= HEALTH_DRAWDOWN_CAUTION:
        return "WEAK"
    if wr is not None and n >= MIN_TRADES_FOR_STATS and wr < HEALTH_WIN_RATE_CAUTION:
        return "WEAK"
    if dd >= HEALTH_DRAWDOWN_HEALTHY:
        return "CAUTION"
    if wr is not None and n >= MIN_TRADES_FOR_STATS and wr < HEALTH_WIN_RATE_HEALTHY:
        return "CAUTION"
    return "HEALTHY"


# ── Operational alerts ────────────────────────────────────────────────────────

def generate_operational_alerts(
    state:      dict,
    config:     Optional[EngineConfig] = None,
    prices_map: Optional[dict]         = None,
) -> list:
    """
    Scan the current portfolio state for operational risk conditions.
    Returns a list of alert dicts (up to MAX_OP_ALERTS).
    """
    cfg     = config or EngineConfig()
    pm      = prices_map or {}
    metrics = compute_metrics(state, pm)
    alerts  = []

    # Excessive drawdown
    if metrics["drawdown_pct"] >= ALERT_DRAWDOWN_PCT:
        severity = "HIGH" if metrics["drawdown_pct"] >= HEALTH_DRAWDOWN_CAUTION else "MEDIUM"
        alerts.append({
            "alert_type": "EXCESSIVE_DRAWDOWN",
            "severity":   severity,
            "message":    (
                f"Drawdown {metrics['drawdown_pct']:.1f}% — "
                f"risk-off threshold {cfg.risk_off_drawdown_pct * 100:.0f}%"
            ),
            "value": metrics["drawdown_pct"],
        })

    # Dangerous single-ticker concentration
    eq = metrics["equity"]
    if eq > 0:
        for ticker, pos in state["positions"].items():
            val  = _position_value(pos, pm.get(ticker))
            conc = val / eq * 100.0
            if conc >= ALERT_CONCENTRATION_PCT:
                alerts.append({
                    "alert_type": "DANGEROUS_CONCENTRATION",
                    "severity":   "HIGH",
                    "message":    f"{ticker} is {conc:.1f}% of equity",
                    "value":      round(conc, 2),
                    "ticker":     ticker,
                })

    # Low cash reserve
    cash_pct = metrics["cash"] / eq * 100.0 if eq > 0 else 100.0
    if cash_pct < ALERT_CASH_RESERVE_PCT:
        alerts.append({
            "alert_type": "LOW_CASH_RESERVE",
            "severity":   "MEDIUM",
            "message":    (
                f"Cash {cash_pct:.1f}% of equity — "
                f"below {ALERT_CASH_RESERVE_PCT}% reserve"
            ),
            "value": round(cash_pct, 2),
        })

    # High exposure
    if metrics["exposure_pct"] >= ALERT_EXPOSURE_HIGH_PCT:
        alerts.append({
            "alert_type": "HIGH_EXPOSURE",
            "severity":   "MEDIUM",
            "message":    (
                f"Exposure {metrics['exposure_pct']:.1f}% — "
                f"cap {cfg.max_exposure_pct * 100:.0f}%"
            ),
            "value": metrics["exposure_pct"],
        })

    # Volatility spike: recent 20-row vol > 2× prior-period 20-row vol
    hist      = state["equity_history"]
    vol_now   = _rolling_volatility(hist[-20:],    20) if len(hist) >= 4  else None
    vol_prior = _rolling_volatility(hist[-40:-20], 20) if len(hist) >= 24 else None
    if vol_now is not None and vol_prior is not None and vol_prior > 0:
        if vol_now > vol_prior * ALERT_VOL_SPIKE_MULTIPLIER:
            alerts.append({
                "alert_type": "VOLATILITY_SPIKE",
                "severity":   "MEDIUM",
                "message":    (
                    f"Rolling vol {vol_now:.2f}% is "
                    f"{vol_now / vol_prior:.1f}× prior {vol_prior:.2f}%"
                ),
                "value": vol_now,
            })

    return alerts[:MAX_OP_ALERTS]


# ── Report generation ─────────────────────────────────────────────────────────

def generate_report(
    state:           dict,
    config:          Optional[EngineConfig] = None,
    prices_map:      Optional[dict]         = None,
    n_recent_events: int                    = MAX_REPORT_EVENTS,
) -> dict:
    """
    Full portfolio snapshot report.

    Returns {health, metrics, open_positions, recent_events,
             operational_alerts, n_events_total, n_closed_trades,
             risk_off, row_idx}.
    """
    cfg     = config or EngineConfig()
    pm      = prices_map or {}
    metrics = compute_metrics(state, pm)
    health  = _portfolio_health(metrics)

    open_positions = []
    for ticker, pos in state["positions"].items():
        curr       = pm.get(ticker, pos["entry_price"])
        curr_value = _position_value(pos, curr)
        open_positions.append({
            "ticker":            ticker,
            "sector":            pos.get("sector", ""),
            "entry_price":       pos["entry_price"],
            "current_price":     curr,
            "shares":            pos["shares"],
            "entry_cash":        pos["entry_cash"],
            "current_value":     round(curr_value, 4),
            "unrealized_pnl":    round(curr_value - pos["entry_cash"], 4),
            "stop_loss_price":   pos["stop_loss_price"],
            "take_profit_price": pos["take_profit_price"],
            "holding_rows":      state["row_idx"] - pos["entry_row"],
        })

    return {
        "health":             health,
        "metrics":            metrics,
        "open_positions":     open_positions,
        "recent_events":      state["events"][-n_recent_events:],
        "operational_alerts": generate_operational_alerts(state, cfg, pm),
        "n_events_total":     len(state["events"]),
        "n_closed_trades":    len(state["closed_trades"]),
        "risk_off":           state["risk_off"],
        "row_idx":            state["row_idx"],
    }


# ── Deterministic replay ──────────────────────────────────────────────────────

def replay(
    rows:       list,
    prices_map: Optional[dict]         = None,
    config:     Optional[EngineConfig] = None,
) -> dict:
    """
    Deterministic batch replay over a historical alert stream.

    prices_map  optional {ticker: float} of static prices used for SL/TP
                checks throughout the replay.  Each row may also carry a
                "price" field used as current_price if not in prices_map.

    Returns {final_state, report, n_rows}.
    """
    cfg   = config or EngineConfig()
    state = create_state(cfg)
    pm    = prices_map or {}

    for row in rows:
        # Entry price comes from the row itself; prices_map is only for SL/TP checks
        current_price = float(row["price"]) if row.get("price") else None
        state         = process_alert(
            state,
            row,
            current_price=current_price,
            prices_map=pm,
            config=cfg,
        )

    return {
        "final_state": state,
        "report":      generate_report(state, cfg, pm),
        "n_rows":      len(rows),
    }
