"""
Paper portfolio simulation for the Predator scanner.
Phase 3C — read-only analytics; does NOT place real orders.

Each row in predator_outcomes represents a completed alert.  The simulation
asks: "If we had allocated capital to each alert as it fired, what would the
portfolio performance have looked like?"

Capital model
-------------
1. Start with initial_capital in cash.
2. For each alert row (treated as a time step):
   a. Close any positions whose holding_period_rows have elapsed.
   b. If slot is available and row passes filters, open a position using
      the configured allocation method.
   c. Record equity (cash + open-position book value at entry price).
3. After the last row, close all remaining open positions.
4. Compute metrics from the equity curve and closed trades.

Open positions are marked at entry_capital during the holding period (no
intra-period mark-to-market — we don't have daily prices).  PnL is realised
at close using return_5d or return_20d from the alert row.  This means
drawdown is a lower bound; intra-period losses are not visible.

Allocation methods
------------------
"fixed"      — every position gets the same fixed_allocation dollar amount
"confidence" — confidence_base × confidence_pct / 100
"tier"       — fixed_allocation × tier_weight (CONVICTION=2×, ALERT=1×, WATCH=0.5×)

In all cases max_position_cap and min_cash_pct are enforced.
"""
import json
import logging
from typing import NamedTuple, Optional

from outcome_analytics import MIN_ROWS_FOR_STATS, _avg, _fetch_completed_outcomes

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_INITIAL_CAPITAL:     float = 10_000.0
DEFAULT_FIXED_ALLOCATION:    float =  1_000.0
DEFAULT_MAX_POSITION_CAP:    float =   0.20    # 20 % of portfolio per position
DEFAULT_MAX_OPEN_POSITIONS:  int   =    10
DEFAULT_HOLDING_PERIOD:      str   = "5d"
DEFAULT_HOLDING_PERIOD_ROWS: int   =     5
DEFAULT_MIN_CASH_PCT:        float =   0.05    # keep ≥ 5 % as cash reserve

DEFAULT_TIER_WEIGHTS: dict = {
    "CONVICTION": 2.0,
    "ALERT":      1.0,
    "WATCH":      0.5,
}

MIN_TRADES_FOR_STATS:            int   =  5
CONCENTRATION_REGIME_THRESHOLD:  float = 70.0   # % in one regime → warn
CONCENTRATION_TICKER_THRESHOLD:  float = 30.0   # % in one ticker  → warn
CONCENTRATION_SIGNAL_THRESHOLD:  float = 50.0   # % with same signal → warn
ALPHA_CONCENTRATION_THRESHOLD:   float = 50.0   # top-3 trades = >50 % of gains
FRAGILITY_TOP_TRADES:            int   =  5     # trades removed in fragility stress
DRAWDOWN_HIGH_THRESHOLD:         float = 20.0   # >20 % → CAUTION
DRAWDOWN_SEVERE_THRESHOLD:       float = 30.0   # >30 % → WEAK
WIN_RATE_HEALTHY:                float = 55.0
WIN_RATE_CAUTION:                float = 45.0
CONSECUTIVE_LOSS_WARN_STREAK:    int   =  5     # ≥ 5 consecutive losses → warn


# ── SimConfig ─────────────────────────────────────────────────────────────────

class SimConfig(NamedTuple):
    """
    Configuration for a single simulation run.  All fields are optional;
    defaults reproduce a baseline fixed-allocation portfolio.
    """
    initial_capital:     float          = DEFAULT_INITIAL_CAPITAL
    allocation_method:   str            = "fixed"   # "fixed" | "confidence" | "tier"
    fixed_allocation:    float          = DEFAULT_FIXED_ALLOCATION
    confidence_base:     float          = DEFAULT_FIXED_ALLOCATION
    tier_weights:        Optional[dict] = None
    max_position_cap:    float          = DEFAULT_MAX_POSITION_CAP
    max_open_positions:  int            = DEFAULT_MAX_OPEN_POSITIONS
    holding_period:      str            = DEFAULT_HOLDING_PERIOD
    holding_period_rows: int            = DEFAULT_HOLDING_PERIOD_ROWS
    min_cash_pct:        float          = DEFAULT_MIN_CASH_PCT
    min_confidence:      float          = 0.0


# ── Internal helpers ──────────────────────────────────────────────────────────

def _std(values: list) -> Optional[float]:
    valid = [v for v in values if v is not None]
    if len(valid) < 2:
        return None
    n    = len(valid)
    mean = sum(valid) / n
    return (sum((x - mean) ** 2 for x in valid) / n) ** 0.5


def _get_return(row: dict, holding_period: str) -> Optional[float]:
    if holding_period == "20d":
        return row.get("return_20d")
    return row.get("return_5d")


def _sig_scores(row: dict) -> dict:
    try:
        raw = json.loads(row.get("signal_summary") or "{}")
        return {k: int(v or 0) for k, v in raw.items() if (v or 0) > 0}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _compute_allocation(row: dict, cfg: SimConfig, portfolio_value: float) -> float:
    method = cfg.allocation_method
    if method == "confidence":
        conf  = float(row.get("confidence_pct") or 0.0)
        alloc = cfg.confidence_base * conf / 100.0
    elif method == "tier":
        tier    = str(row.get("tier") or "WATCH")
        weights = cfg.tier_weights or DEFAULT_TIER_WEIGHTS
        alloc   = cfg.fixed_allocation * weights.get(tier, 1.0)
    else:
        alloc = cfg.fixed_allocation

    if portfolio_value > 0:
        alloc = min(alloc, portfolio_value * cfg.max_position_cap)

    return max(0.0, alloc)


def _max_drawdown(equity_curve: list) -> float:
    if len(equity_curve) < 2:
        return 0.0
    peak   = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return round(max_dd, 4)


def _equity_volatility(equity_curve: list) -> Optional[float]:
    if len(equity_curve) < 2:
        return None
    changes = [
        (equity_curve[i] / equity_curve[i - 1] - 1.0) * 100.0
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] > 0
    ]
    return _std(changes)


def _best_worst_periods(equity_curve: list, window: int = 10) -> tuple:
    n = len(equity_curve)
    w = min(window, max(1, n // 3))
    if n < w + 1:
        return None, None

    best_gain  = None
    worst_loss = None
    best_start = worst_start = 0

    for i in range(n - w):
        sv  = equity_curve[i]
        ev  = equity_curve[i + w]
        chg = (ev / sv - 1.0) * 100.0 if sv > 0 else 0.0

        if best_gain is None or chg > best_gain:
            best_gain  = chg
            best_start = i
        if worst_loss is None or chg < worst_loss:
            worst_loss = chg
            worst_start = i

    return (
        {
            "start_row":  best_start,
            "end_row":    best_start + w,
            "return_pct": round(best_gain,  2) if best_gain  is not None else None,
        },
        {
            "start_row":  worst_start,
            "end_row":    worst_start + w,
            "return_pct": round(worst_loss, 2) if worst_loss is not None else None,
        },
    )


def _portfolio_health(
    metrics:       dict,
    concentration: dict,
    robustness:    dict,
) -> str:
    n = metrics.get("n_trades") or 0
    if n < MIN_TRADES_FOR_STATS:
        return "INSUFFICIENT_DATA"

    score = 0
    wr = metrics.get("win_rate") or 0.0
    if wr >= WIN_RATE_HEALTHY:
        score += 2
    elif wr >= WIN_RATE_CAUTION:
        score += 1

    dd = metrics.get("max_drawdown_pct") or 0.0
    if dd <= DRAWDOWN_HIGH_THRESHOLD:
        score += 2
    elif dd <= DRAWDOWN_SEVERE_THRESHOLD:
        score += 1

    if (metrics.get("cumulative_return_pct") or 0.0) > 0:
        score += 1

    if not concentration.get("warnings"):
        score += 1
    if not robustness.get("warnings"):
        score += 1

    if score >= 6:
        return "HEALTHY"
    if score >= 4:
        return "CAUTION"
    return "WEAK"


# ── Core simulation ───────────────────────────────────────────────────────────

def simulate(rows: list, config: Optional[SimConfig] = None) -> dict:
    """
    Run a paper portfolio simulation.

    Rows are processed in order.  Each represents one alert (time step).
    Positions close after `holding_period_rows` subsequent steps using the
    return stored in the alert row (`return_5d` or `return_20d`).

    Returns
    -------
    {
        "equity_curve":             list[float],
        "trades":                   list[dict],
        "open_at_end_count":        int,
        "avg_cash_utilization_pct": float,
        "skipped_rows":             int,
        "config":                   dict,
        "metrics":                  dict,
    }
    """
    cfg = config or SimConfig()

    cash:          float = cfg.initial_capital
    open_pos:      list  = []
    closed_trades: list  = []
    equity_curve:  list  = []
    util_sum:      float = 0.0
    skipped:       int   = 0

    for i, row in enumerate(rows):
        # ── Close expired positions ──────────────────────────────────────────
        still_open = []
        for pos in open_pos:
            if pos["close_row"] <= i:
                ret   = pos["return_pct"] or 0.0
                cv    = round(pos["entry_capital"] * (1.0 + ret / 100.0), 4)
                cash += cv
                pos.update({
                    "exit_value": cv,
                    "pnl":        round(cv - pos["entry_capital"], 4),
                    "exit_row":   i,
                })
                closed_trades.append(pos)
                log.debug(
                    "paper_portfolio: closed %s row=%d pnl=%.2f",
                    pos["ticker"], i, pos["pnl"],
                )
            else:
                still_open.append(pos)
        open_pos = still_open

        # ── Try to open new position ──────────────────────────────────────────
        conf     = float(row.get("confidence_pct") or 0.0)
        can_open = (
            len(open_pos) < cfg.max_open_positions
            and conf >= cfg.min_confidence
        )

        if can_open:
            portfolio_value = cash + sum(p["entry_capital"] for p in open_pos)
            alloc           = _compute_allocation(row, cfg, portfolio_value)
            min_reserve     = portfolio_value * cfg.min_cash_pct
            alloc           = min(alloc, max(0.0, cash - min_reserve))

            if alloc > 0:
                cash -= alloc
                open_pos.append({
                    "entry_row":      i,
                    "close_row":      i + cfg.holding_period_rows,
                    "ticker":         str(row.get("ticker") or "UNKNOWN"),
                    "entry_capital":  round(alloc, 4),
                    "return_pct":     _get_return(row, cfg.holding_period),
                    "regime":         str(row.get("regime") or "BULL"),
                    "tier":           str(row.get("tier") or "WATCH"),
                    "confidence_pct": conf,
                    "signal_summary": row.get("signal_summary") or "{}",
                })
            else:
                skipped += 1
        else:
            skipped += 1

        # ── Record equity (open positions marked at entry_capital) ─────────
        deployed  = sum(p["entry_capital"] for p in open_pos)
        equity    = cash + deployed
        equity_curve.append(round(equity, 4))
        util_sum += (deployed / equity * 100.0) if equity > 0 else 0.0

    # ── Close remaining open positions ────────────────────────────────────────
    open_at_end = len(open_pos)
    for pos in open_pos:
        ret   = pos["return_pct"] or 0.0
        cv    = round(pos["entry_capital"] * (1.0 + ret / 100.0), 4)
        cash += cv
        pos.update({
            "exit_value": cv,
            "pnl":        round(cv - pos["entry_capital"], 4),
            "exit_row":   len(rows),
        })
        closed_trades.append(pos)

    # Replace last equity point with true final value (all positions closed)
    if equity_curve:
        equity_curve[-1] = round(cash, 4)

    avg_util = round(util_sum / len(rows), 2) if rows else 0.0
    metrics  = compute_metrics(equity_curve, closed_trades, cfg.initial_capital)

    log.info(
        "paper_portfolio: simulate — rows=%d trades=%d "
        "final=%.2f util=%.1f%% skipped=%d",
        len(rows), len(closed_trades), cash, avg_util, skipped,
    )

    return {
        "equity_curve":             equity_curve,
        "trades":                   closed_trades,
        "open_at_end_count":        open_at_end,
        "avg_cash_utilization_pct": avg_util,
        "skipped_rows":             skipped,
        "config":                   cfg._asdict(),
        "metrics":                  metrics,
    }


# ── Portfolio metrics ─────────────────────────────────────────────────────────

def compute_metrics(
    equity_curve:   list,
    trades:         list,
    initial_capital: float,
) -> dict:
    """
    Compute portfolio-level metrics from an equity curve and list of trades.

    Returns
    -------
    {
        "final_value":              float,
        "cumulative_return_pct":    float,
        "cagr_like_pct":            float | None,
        "max_drawdown_pct":         float,
        "sharpe_like":              float | None,
        "win_rate":                 float | None,
        "avg_position_return_pct":  float | None,
        "n_trades":                 int,
        "volatility_pct":           float | None,
    }
    """
    n_rows      = len(equity_curve)
    final_value = equity_curve[-1] if equity_curve else initial_capital
    cum_ret     = round((final_value / initial_capital - 1.0) * 100.0, 4) if initial_capital > 0 else 0.0

    # CAGR-like: treat each row as one trading day; annualise to 252 days
    cagr_like = None
    if n_rows >= 10 and initial_capital > 0 and final_value > 0:
        years = n_rows / 252.0
        cagr_like = round(((final_value / initial_capital) ** (1.0 / years) - 1.0) * 100.0, 4)

    max_dd  = _max_drawdown(equity_curve)
    vol     = _equity_volatility(equity_curve)

    closed       = [t for t in trades if t.get("pnl") is not None]
    wins         = [t for t in closed if (t.get("pnl") or 0.0) > 0]
    n_trades     = len(closed)
    win_rate     = round(len(wins) / n_trades * 100.0, 2) if n_trades >= MIN_TRADES_FOR_STATS else None

    rets         = [t.get("return_pct") for t in closed if t.get("return_pct") is not None]
    avg_ret      = _avg(rets)
    std_ret      = _std(rets)
    sharpe_like  = (
        round(avg_ret / std_ret, 4)
        if (avg_ret is not None and std_ret is not None and std_ret > 0)
        else None
    )

    return {
        "final_value":             round(final_value, 2),
        "initial_capital":         initial_capital,
        "cumulative_return_pct":   cum_ret,
        "cagr_like_pct":           cagr_like,
        "max_drawdown_pct":        max_dd,
        "sharpe_like":             sharpe_like,
        "win_rate":                win_rate,
        "avg_position_return_pct": round(avg_ret, 4) if avg_ret is not None else None,
        "n_trades":                n_trades,
        "volatility_pct":          round(vol, 4) if vol is not None else None,
    }


# ── Concentration analysis ────────────────────────────────────────────────────

def concentration_analysis(trades: list) -> dict:
    """
    Measure regime, ticker, signal, and tier concentration in a trade set.

    Returns
    -------
    {
        "n_trades":  int,
        "regime":    {"counts": ..., "pcts": ..., "dominant": str},
        "ticker":    {"counts": ..., "pcts": ..., "top": str},
        "signal":    {"counts": ..., "pcts": ..., "top": str | None},
        "tier":      {"counts": ..., "pcts": ...},
        "warnings":  list[str],
    }
    """
    n = len(trades)
    if n == 0:
        return {
            "n_trades": 0,
            "regime":   {"counts": {}, "pcts": {}, "dominant": None},
            "ticker":   {"counts": {}, "pcts": {}, "top": None},
            "signal":   {"counts": {}, "pcts": {}, "top": None},
            "tier":     {"counts": {}, "pcts": {}},
            "warnings": [],
        }

    def _tally(items):
        counts = {}
        for x in items:
            counts[x] = counts.get(x, 0) + 1
        pcts = {k: round(v / n * 100.0, 1) for k, v in counts.items()}
        return counts, pcts

    regime_counts, regime_pcts = _tally(
        [str(t.get("regime") or "UNKNOWN") for t in trades]
    )
    ticker_counts, ticker_pcts = _tally(
        [str(t.get("ticker") or "UNKNOWN") for t in trades]
    )
    tier_counts, tier_pcts = _tally(
        [str(t.get("tier") or "UNKNOWN") for t in trades]
    )

    # Signal concentration: count how many trades have each signal active
    sig_counts: dict = {}
    for t in trades:
        for s in _sig_scores({"signal_summary": t.get("signal_summary")}):
            sig_counts[s] = sig_counts.get(s, 0) + 1
    sig_pcts = {s: round(c / n * 100.0, 1) for s, c in sig_counts.items()}

    dominant_regime = max(regime_pcts, key=regime_pcts.get) if regime_pcts else None
    top_ticker      = max(ticker_pcts, key=ticker_pcts.get) if ticker_pcts else None
    top_signal      = max(sig_pcts,    key=sig_pcts.get)    if sig_pcts    else None

    warnings = []
    if dominant_regime and regime_pcts.get(dominant_regime, 0) > CONCENTRATION_REGIME_THRESHOLD:
        warnings.append(
            f"REGIME_CONCENTRATION: {dominant_regime} represents "
            f"{regime_pcts[dominant_regime]:.0f}% of trades"
        )
    if top_ticker and ticker_pcts.get(top_ticker, 0) > CONCENTRATION_TICKER_THRESHOLD:
        warnings.append(
            f"TICKER_CONCENTRATION: {top_ticker} represents "
            f"{ticker_pcts[top_ticker]:.0f}% of trades"
        )
    if top_signal and sig_pcts.get(top_signal, 0) > CONCENTRATION_SIGNAL_THRESHOLD:
        warnings.append(
            f"SIGNAL_CONCENTRATION: {top_signal} signal in "
            f"{sig_pcts[top_signal]:.0f}% of trades"
        )

    for w in warnings:
        log.warning("paper_portfolio: %s", w)

    return {
        "n_trades": n,
        "regime":   {"counts": regime_counts, "pcts": regime_pcts, "dominant": dominant_regime},
        "ticker":   {"counts": ticker_counts, "pcts": ticker_pcts, "top": top_ticker},
        "signal":   {"counts": sig_counts,    "pcts": sig_pcts,    "top": top_signal},
        "tier":     {"counts": tier_counts,   "pcts": tier_pcts},
        "warnings": warnings,
    }


# ── Stress simulations ────────────────────────────────────────────────────────

def stress_remove_top_winners(
    rows:   list,
    config: Optional[SimConfig] = None,
    n:      int = FRAGILITY_TOP_TRADES,
) -> dict:
    """
    Re-simulate without the top-N rows by return.

    Measures how much of the portfolio's return is attributable to a small
    number of exceptional trades.
    """
    cfg     = config or SimConfig()
    ret_key = "return_20d" if cfg.holding_period == "20d" else "return_5d"
    actual_n = min(n, len(rows))

    top_indices = {
        idx
        for idx, _ in sorted(
            enumerate(rows),
            key=lambda x: (x[1].get(ret_key) or 0.0),
            reverse=True,
        )[:actual_n]
    }
    filtered_rows = [r for i, r in enumerate(rows) if i not in top_indices]

    base_result   = simulate(rows, cfg)
    stress_result = simulate(filtered_rows, cfg)

    base_cr   = base_result["metrics"].get("cumulative_return_pct")
    stress_cr = stress_result["metrics"].get("cumulative_return_pct")
    impact    = (
        round(stress_cr - base_cr, 2)
        if (base_cr is not None and stress_cr is not None)
        else None
    )
    fragile = (
        impact is not None
        and base_cr is not None
        and base_cr > 0
        and impact < -(base_cr * 0.50)
    )
    if fragile:
        log.warning(
            "paper_portfolio: FRAGILE_ALPHA — removing top-%d trades "
            "cuts return by %.1f pp",
            actual_n, abs(impact),
        )

    return {
        "n_removed":                    actual_n,
        "base_cumulative_return_pct":   base_cr,
        "stress_cumulative_return_pct": stress_cr,
        "impact_cumulative_return_pct": impact,
        "base_win_rate":                base_result["metrics"].get("win_rate"),
        "stress_win_rate":              stress_result["metrics"].get("win_rate"),
        "fragile":                      fragile,
    }


def stress_consecutive_losses(trades: list) -> dict:
    """
    Find the longest consecutive-loss streak and total capital lost in it.
    """
    ordered = sorted(trades, key=lambda t: (t.get("entry_row") or 0))
    closed  = [t for t in ordered if t.get("pnl") is not None]

    if not closed:
        return {
            "max_consecutive_losses": 0,
            "streak_total_loss":      None,
            "n_trades":               0,
            "warning":                False,
        }

    max_streak = cur_streak = 0
    max_loss   = cur_loss   = 0.0

    for t in closed:
        pnl = t.get("pnl") or 0.0
        if pnl < 0:
            cur_streak += 1
            cur_loss   += abs(pnl)
            if cur_streak > max_streak:
                max_streak = cur_streak
                max_loss   = cur_loss
        else:
            cur_streak = 0
            cur_loss   = 0.0

    warn = max_streak >= CONSECUTIVE_LOSS_WARN_STREAK
    if warn:
        log.warning(
            "paper_portfolio: CONSECUTIVE_LOSS_STREAK=%d loss=%.2f",
            max_streak, max_loss,
        )

    return {
        "max_consecutive_losses": max_streak,
        "streak_total_loss":      round(max_loss, 2),
        "n_trades":               len(closed),
        "warning":                warn,
    }


def stress_risk_off_only(
    rows:   list,
    config: Optional[SimConfig] = None,
) -> dict:
    """Simulate using only RISK_OFF regime rows."""
    cfg           = config or SimConfig()
    risk_off_rows = [r for r in rows if str(r.get("regime") or "") == "RISK_OFF"]
    n_total       = len(rows)
    n_ro          = len(risk_off_rows)

    if not risk_off_rows:
        return {
            "n_risk_off_rows": 0,
            "n_total_rows":    n_total,
            "risk_off_pct":    0.0,
            "metrics":         None,
            "note":            "No RISK_OFF rows found",
        }

    result = simulate(risk_off_rows, cfg)
    return {
        "n_risk_off_rows": n_ro,
        "n_total_rows":    n_total,
        "risk_off_pct":    round(n_ro / n_total * 100.0, 1) if n_total > 0 else 0.0,
        "metrics":         result["metrics"],
    }


def stress_confidence_shock(
    rows:      list,
    config:    Optional[SimConfig] = None,
    shock_pct: float = 20.0,
) -> dict:
    """
    Re-simulate with all confidence_pct values reduced by shock_pct.

    For confidence-weighted allocation this reduces position sizes.
    For fixed allocation it only matters if min_confidence is set.
    """
    cfg = config or SimConfig()
    shocked = []
    for r in rows:
        r2 = dict(r)
        r2["confidence_pct"] = max(0.0, float(r2.get("confidence_pct") or 0.0) - shock_pct)
        shocked.append(r2)

    base_result  = simulate(rows,    cfg)
    shock_result = simulate(shocked, cfg)

    base_cr  = base_result["metrics"].get("cumulative_return_pct")
    shock_cr = shock_result["metrics"].get("cumulative_return_pct")
    impact   = (
        round(shock_cr - base_cr, 2)
        if (base_cr is not None and shock_cr is not None)
        else None
    )

    return {
        "shock_pct":                    shock_pct,
        "base_n_trades":                base_result["metrics"].get("n_trades"),
        "shock_n_trades":               shock_result["metrics"].get("n_trades"),
        "base_cumulative_return_pct":   base_cr,
        "shock_cumulative_return_pct":  shock_cr,
        "impact_cumulative_return_pct": impact,
    }


def run_stress_tests(
    rows:   list,
    config: Optional[SimConfig] = None,
) -> dict:
    """Run all built-in stress tests and return a combined results dict."""
    cfg        = config or SimConfig()
    base_sim   = simulate(rows, cfg)

    return {
        "remove_top_winners":        stress_remove_top_winners(rows, cfg),
        "consecutive_losses":        stress_consecutive_losses(base_sim["trades"]),
        "risk_off_only":             stress_risk_off_only(rows, cfg),
        "confidence_shock_20pct":    stress_confidence_shock(rows, cfg, shock_pct=20.0),
        "confidence_shock_40pct":    stress_confidence_shock(rows, cfg, shock_pct=40.0),
    }


# ── Robustness analysis ───────────────────────────────────────────────────────

def robustness_analysis(trades: list, metrics: dict) -> dict:
    """
    Detect structural weaknesses in the simulated portfolio.

    Warning types
    -------------
    ALPHA_CONCENTRATION   top-3 trades account for > ALPHA_CONCENTRATION_THRESHOLD %
                          of total positive PnL
    FEW_WINNERS           fewer than MIN_TRADES_FOR_STATS profitable trades
    UNSTABLE_COMPOUNDING  equity volatility > 5 % per step (very lumpy returns)
    """
    closed = [t for t in trades if t.get("pnl") is not None]
    n      = len(closed)

    if n == 0:
        return {
            "alpha_concentration_pct": None,
            "n_profitable":            0,
            "n_trades":                0,
            "warnings":                [],
        }

    pnls         = sorted([t.get("pnl") or 0.0 for t in closed], reverse=True)
    total_gains  = sum(p for p in pnls if p > 0) or 0.0
    top3_gains   = sum(pnls[:3]) if len(pnls) >= 3 else sum(p for p in pnls if p > 0)
    alpha_conc   = round(top3_gains / total_gains * 100.0, 1) if total_gains > 0 else None

    n_profitable = len([t for t in closed if (t.get("pnl") or 0.0) > 0])

    warnings = []
    if alpha_conc is not None and alpha_conc > ALPHA_CONCENTRATION_THRESHOLD:
        msg = (
            f"ALPHA_CONCENTRATION: top-3 trades account for {alpha_conc:.0f}% of gains — "
            "portfolio return is fragile"
        )
        warnings.append(msg)
        log.warning("paper_portfolio: %s", msg)

    if n_profitable < MIN_TRADES_FOR_STATS:
        msg = f"FEW_WINNERS: only {n_profitable} profitable trade(s)"
        warnings.append(msg)
        log.warning("paper_portfolio: %s", msg)

    vol = metrics.get("volatility_pct")
    if vol is not None and vol > 5.0:
        msg = f"UNSTABLE_COMPOUNDING: equity volatility {vol:.1f}%/step — lumpy compounding"
        warnings.append(msg)
        log.warning("paper_portfolio: %s", msg)

    return {
        "alpha_concentration_pct": alpha_conc,
        "n_profitable":            n_profitable,
        "n_trades":                n,
        "warnings":                warnings,
    }


# ── Recommendations ───────────────────────────────────────────────────────────

def generate_recommendations(
    metrics:       dict,
    concentration: dict,
    robustness:    dict,
) -> list:
    """
    Return a list of plain-English recommendation strings.

    Deterministic: same inputs always produce the same list.
    """
    recs = []
    n    = metrics.get("n_trades") or 0

    if n == 0:
        recs.append(
            "INSUFFICIENT_DATA: No trades executed — "
            "check allocation parameters, min_confidence, and data quality"
        )
        return recs

    wr = metrics.get("win_rate")
    if wr is not None and wr < 40.0:
        recs.append(
            "INCREASE_CONFIDENCE_THRESHOLD: Win rate below 40% — "
            "consider requiring min_confidence ≥ 65%"
        )

    dd = metrics.get("max_drawdown_pct") or 0.0
    if dd > DRAWDOWN_SEVERE_THRESHOLD:
        recs.append(
            "REDUCE_POSITION_SIZE: Max drawdown exceeded "
            f"{DRAWDOWN_SEVERE_THRESHOLD:.0f}% — reduce fixed_allocation "
            "or lower max_position_cap"
        )
    elif dd > DRAWDOWN_HIGH_THRESHOLD:
        recs.append(
            "MONITOR_DRAWDOWN: Max drawdown exceeded "
            f"{DRAWDOWN_HIGH_THRESHOLD:.0f}% — consider tighter max_position_cap"
        )

    for w in concentration.get("warnings") or []:
        if "REGIME_CONCENTRATION" in w:
            recs.append("DIVERSIFY_REGIME_EXPOSURE: " + w.split(": ", 1)[-1])
        elif "TICKER_CONCENTRATION" in w:
            recs.append("TIGHTEN_TICKER_EXPOSURE: " + w.split(": ", 1)[-1])
        elif "SIGNAL_CONCENTRATION" in w:
            recs.append("DIVERSIFY_SIGNAL_EXPOSURE: " + w.split(": ", 1)[-1])

    for w in robustness.get("warnings") or []:
        if "ALPHA_CONCENTRATION" in w:
            recs.append(
                "BROADEN_ALPHA_SOURCES: Portfolio gains concentrated in few trades — "
                "increase max_open_positions or diversify signal mix"
            )
        elif "FEW_WINNERS" in w:
            recs.append(
                "REVIEW_SIGNAL_QUALITY: Too few winning trades — "
                "review signal scoring thresholds or require higher-quality setups"
            )
        elif "UNSTABLE_COMPOUNDING" in w:
            recs.append(
                "SMOOTH_POSITION_SIZING: High equity volatility — "
                "use confidence-weighted allocation to size positions more evenly"
            )

    return recs


# ── Report builder ────────────────────────────────────────────────────────────

def generate_report(
    rows:   Optional[list] = None,
    config: Optional[SimConfig] = None,
) -> dict:
    """
    Full paper portfolio simulation report.

    If rows is None, fetches all COMPLETE outcomes from the DB.
    Pass a pre-fetched list to avoid DB access (e.g. in tests).

    Returns
    -------
    {
        "row_count":           int,
        "config":              dict,
        "metrics":             dict,
        "equity_curve_summary": dict,
        "best_period":         dict | None,
        "worst_period":        dict | None,
        "concentration":       dict,
        "robustness":          dict,
        "stress_tests":        dict,
        "recommendations":     list[str],
        "portfolio_health":    str,
        "warnings":            list[str],
    }
    """
    if rows is None:
        rows = _fetch_completed_outcomes()

    cfg = config or SimConfig()
    n   = len(rows)
    log.info("paper_portfolio: generating report on %d completed outcomes", n)

    if n < MIN_TRADES_FOR_STATS:
        log.warning(
            "paper_portfolio: only %d row(s) — most stats will be sparse "
            "(need >= %d for win rate)",
            n, MIN_TRADES_FOR_STATS,
        )

    sim          = simulate(rows, cfg)
    trades       = sim["trades"]
    metrics      = sim["metrics"]
    equity_curve = sim["equity_curve"]

    concentration = concentration_analysis(trades)
    robustness_r  = robustness_analysis(trades, metrics)
    stress        = run_stress_tests(rows, cfg)
    recs          = generate_recommendations(metrics, concentration, robustness_r)
    health        = _portfolio_health(metrics, concentration, robustness_r)
    best_p, wrst_p = _best_worst_periods(equity_curve)

    warnings = list(concentration.get("warnings") or []) + list(robustness_r.get("warnings") or [])

    log.info(
        "paper_portfolio: report done — health=%s trades=%d "
        "cum_return=%.2f%% drawdown=%.2f%% warnings=%d",
        health,
        metrics.get("n_trades", 0),
        metrics.get("cumulative_return_pct", 0.0),
        metrics.get("max_drawdown_pct",       0.0),
        len(warnings),
    )

    return {
        "row_count": n,
        "config":    cfg._asdict(),
        "metrics":   metrics,
        "equity_curve_summary": {
            "length":  len(equity_curve),
            "initial": cfg.initial_capital,
            "final":   metrics.get("final_value"),
            "peak":    max(equity_curve) if equity_curve else None,
            "trough":  min(equity_curve) if equity_curve else None,
        },
        "best_period":      best_p,
        "worst_period":     wrst_p,
        "concentration":    concentration,
        "robustness":       robustness_r,
        "stress_tests":     stress,
        "recommendations":  recs,
        "portfolio_health": health,
        "warnings":         warnings,
    }
