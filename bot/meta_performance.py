"""
Meta-performance analytics for the Predator scanner.
Phase 2D — read-only; does NOT modify live scoring.

Tracks rolling performance windows (last 10/25/50/100 alerts), detects engine
degradation, confidence inflation, and regime deterioration.  Produces safeguard
recommendations when thresholds are exceeded.

Row ordering assumption
-----------------------
Rows are assumed to be in chronological order, oldest first.  All rolling
windows take rows[-N:] so the slice always covers the MOST RECENT N alerts.

Safeguard recommendation ladder (by HIGH-severity event count)
--------------------------------------------------------------
    1+  → REDUCE_AGGRESSIVENESS
    1+  inflation event → INCREASE_CONFIDENCE_THRESHOLD  (independently)
    3+  → PAUSE_ADAPTIVE_ROLLOUT
    5+  → OBSERVATION_ONLY
"""
import logging
from typing import Optional

from confidence_validation import calibration_error
from market_regime import BULL, NEUTRAL, RISK_OFF
from outcome_analytics import (
    MIN_ROWS_FOR_STATS,
    _avg,
    _fetch_completed_outcomes,
    _win_rate,
)

log = logging.getLogger(__name__)

# ── Rolling window sizes ──────────────────────────────────────────────────────
WINDOWS: tuple = (10, 25, 50, 100)

# ── Degradation thresholds (shorter window is this much worse than longer) ────
DEGRAD_WIN_RATE_THRESHOLD:    float = 10.0  # pp   → MEDIUM
DEGRAD_WIN_RATE_HIGH:         float = 20.0  # pp   → HIGH
DEGRAD_RETURN_THRESHOLD:      float =  1.0  # %    → MEDIUM
DEGRAD_RETURN_HIGH:           float =  3.0  # %    → HIGH
DEGRAD_CALIBRATION_THRESHOLD: float =  0.05  # ECE → MEDIUM
DEGRAD_CALIBRATION_HIGH:      float =  0.10  # ECE → HIGH
DEGRAD_DRAWDOWN_THRESHOLD:    float =  2.0  # % more negative → MEDIUM
DEGRAD_DRAWDOWN_HIGH:         float =  5.0  # %   → HIGH

# ── Confidence inflation thresholds ───────────────────────────────────────────
INFLATION_CONF_RISE:    float = 5.0   # pp confidence gain while returns fall
INFLATION_CONF_HIGH:    float = 10.0  # pp → HIGH severity
INFLATION_RETURN_FALL:  float = 1.0   # % return drop required to co-trigger

# ── Regime deterioration thresholds ──────────────────────────────────────────
REGIME_DEGRAD_THRESHOLD: float = 15.0  # pp regime win-rate drop → MEDIUM
REGIME_DEGRAD_HIGH:      float = 25.0  # pp → HIGH

# ── Safeguard trigger counts (count of HIGH-severity events) ──────────────────
SAFEGUARD_REDUCE_HIGH_COUNT:    int = 1  # any HIGH → REDUCE_AGGRESSIVENESS
SAFEGUARD_PAUSE_HIGH_COUNT:     int = 3  # 3+ HIGH  → PAUSE_ADAPTIVE_ROLLOUT
SAFEGUARD_CRITICAL_HIGH_COUNT:  int = 5  # 5+ HIGH  → OBSERVATION_ONLY


# ── Internal helpers ──────────────────────────────────────────────────────────

def _pearson(xs: list, ys: list) -> Optional[float]:
    """
    Pearson correlation of paired lists; None-valued pairs are dropped.
    Returns None when fewer than 3 valid pairs remain or variance is zero.
    """
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None
    xs_v = [p[0] for p in pairs]
    ys_v = [p[1] for p in pairs]
    mx = sum(xs_v) / n
    my = sum(ys_v) / n
    cov   = sum((x - mx) * (y - my) for x, y in pairs) / n
    var_x = sum((x - mx) ** 2 for x in xs_v) / n
    var_y = sum((y - my) ** 2 for y in ys_v) / n
    if var_x == 0.0 or var_y == 0.0:
        return None
    return round(cov / ((var_x * var_y) ** 0.5), 4)


def _std(values: list) -> Optional[float]:
    """Population stddev of non-None values; None when fewer than 2 valid."""
    valid = [v for v in values if v is not None]
    if len(valid) < 2:
        return None
    mean = sum(valid) / len(valid)
    variance = sum((x - mean) ** 2 for x in valid) / len(valid)
    return variance ** 0.5


def _sharpe_like(rows: list) -> Optional[float]:
    """
    Simplified Sharpe: avg_return_5d / std(return_5d).

    Gives a risk-adjusted signal quality measure.  None when fewer than 3
    rows have return data, or when std is zero (all returns identical).
    """
    vals = [r.get("return_5d") for r in rows if r.get("return_5d") is not None]
    if len(vals) < 3:
        return None
    mean = sum(vals) / len(vals)
    std  = (sum((x - mean) ** 2 for x in vals) / len(vals)) ** 0.5
    if std == 0.0:
        return None
    return round(mean / std, 4)


def _regime_win_rate(rows: list, regime: str) -> Optional[float]:
    """Win rate for rows filtered to the given regime label."""
    return _win_rate([r for r in rows if r.get("regime") == regime])


# ── Public rolling-window analytics ──────────────────────────────────────────

def window_stats(rows: list, window: int) -> dict:
    """
    Statistics for the most recent `window` rows.

    Takes rows[-window:] so newer alerts always shadow older ones.
    If fewer than `window` rows exist, uses all available rows.

    Returns
    -------
    {
        "window":            int,
        "n":                 int,        # actual rows in this window
        "win_rate":          float|None,
        "avg_return_5d":     float|None,
        "avg_return_20d":    float|None,
        "avg_max_dd":        float|None,
        "avg_confidence":    float|None,
        "calibration_error": float|None, # ECE in [0,1]
        "sharpe_like":       float|None,
        "conf_return_corr":  float|None, # Pearson(confidence, return_5d)
    }
    """
    slice_ = rows[-window:] if len(rows) >= window else rows[:]
    n = len(slice_)

    return {
        "window":            window,
        "n":                 n,
        "win_rate":          _win_rate(slice_),
        "avg_return_5d":     _avg([r.get("return_5d")        for r in slice_]),
        "avg_return_20d":    _avg([r.get("return_20d")       for r in slice_]),
        "avg_max_dd":        _avg([r.get("max_drawdown_pct") for r in slice_]),
        "avg_confidence":    _avg([r.get("confidence_pct")   for r in slice_]),
        "calibration_error": calibration_error(slice_),
        "sharpe_like":       _sharpe_like(slice_),
        "conf_return_corr":  _pearson(
            [r.get("confidence_pct") for r in slice_],
            [r.get("return_5d")      for r in slice_],
        ),
    }


def rolling_windows(rows: list) -> dict:
    """
    Stats for all four rolling windows, keyed by window size (int).

    Always returns a dict with all four WINDOWS as keys; a window where
    n = 0 still appears with all stats set to None.
    """
    return {w: window_stats(rows, w) for w in WINDOWS}


# ── Degradation detection ─────────────────────────────────────────────────────

def degradation_detection(windows: dict) -> list:
    """
    Detect performance deterioration by comparing consecutive rolling windows.

    Each shorter window is compared to the next-larger window.  Four metrics
    are checked per pair: win_rate, avg_return_5d, calibration_error, avg_max_dd.

    Returns events sorted: HIGH severity first, then type, then short_window.
    """
    events = []
    sizes  = sorted(windows.keys())
    pairs  = [(sizes[i], sizes[i + 1]) for i in range(len(sizes) - 1)]

    for short_w, long_w in pairs:
        short = windows[short_w]
        long_ = windows[long_w]

        # ── Win rate ──────────────────────────────────────────────────────────
        s_wr, l_wr = short.get("win_rate"), long_.get("win_rate")
        if s_wr is not None and l_wr is not None:
            delta = round(s_wr - l_wr, 2)
            if delta < -DEGRAD_WIN_RATE_THRESHOLD:
                sev = "HIGH" if delta < -DEGRAD_WIN_RATE_HIGH else "MEDIUM"
                events.append({
                    "type":         "WIN_RATE_DETERIORATION",
                    "short_window": short_w,
                    "long_window":  long_w,
                    "short_value":  s_wr,
                    "long_value":   l_wr,
                    "delta":        delta,
                    "severity":     sev,
                    "detail": (
                        f"Last-{short_w} win rate {s_wr:.1f}% "
                        f"vs last-{long_w} {l_wr:.1f}% (Δ={delta:+.1f}pp)"
                    ),
                })
                log.warning(
                    "meta_performance: WIN_RATE_DETERIORATION %s "
                    "last-%d=%.1f%% last-%d=%.1f%%",
                    sev, short_w, s_wr, long_w, l_wr,
                )

        # ── Average 5-day return ──────────────────────────────────────────────
        s_ret, l_ret = short.get("avg_return_5d"), long_.get("avg_return_5d")
        if s_ret is not None and l_ret is not None:
            delta = round(s_ret - l_ret, 2)
            if delta < -DEGRAD_RETURN_THRESHOLD:
                sev = "HIGH" if delta < -DEGRAD_RETURN_HIGH else "MEDIUM"
                events.append({
                    "type":         "RETURN_DETERIORATION",
                    "short_window": short_w,
                    "long_window":  long_w,
                    "short_value":  s_ret,
                    "long_value":   l_ret,
                    "delta":        delta,
                    "severity":     sev,
                    "detail": (
                        f"Last-{short_w} avg return {s_ret:.2f}% "
                        f"vs last-{long_w} {l_ret:.2f}% (Δ={delta:+.2f}%)"
                    ),
                })
                log.warning(
                    "meta_performance: RETURN_DETERIORATION %s "
                    "last-%d=%.2f%% last-%d=%.2f%%",
                    sev, short_w, s_ret, long_w, l_ret,
                )

        # ── Calibration (ECE) ─────────────────────────────────────────────────
        s_ece, l_ece = short.get("calibration_error"), long_.get("calibration_error")
        if s_ece is not None and l_ece is not None:
            delta = round(s_ece - l_ece, 4)
            if delta > DEGRAD_CALIBRATION_THRESHOLD:
                sev = "HIGH" if delta > DEGRAD_CALIBRATION_HIGH else "MEDIUM"
                events.append({
                    "type":         "CALIBRATION_WORSENING",
                    "short_window": short_w,
                    "long_window":  long_w,
                    "short_value":  s_ece,
                    "long_value":   l_ece,
                    "delta":        delta,
                    "severity":     sev,
                    "detail": (
                        f"Last-{short_w} ECE {s_ece:.4f} "
                        f"vs last-{long_w} {l_ece:.4f} (Δ={delta:+.4f})"
                    ),
                })
                log.warning(
                    "meta_performance: CALIBRATION_WORSENING %s "
                    "last-%d=%.4f last-%d=%.4f",
                    sev, short_w, s_ece, long_w, l_ece,
                )

        # ── Drawdown ──────────────────────────────────────────────────────────
        s_dd, l_dd = short.get("avg_max_dd"), long_.get("avg_max_dd")
        if s_dd is not None and l_dd is not None:
            delta = round(s_dd - l_dd, 2)  # negative → shorter window is worse
            if delta < -DEGRAD_DRAWDOWN_THRESHOLD:
                sev = "HIGH" if delta < -DEGRAD_DRAWDOWN_HIGH else "MEDIUM"
                events.append({
                    "type":         "DRAWDOWN_WORSENING",
                    "short_window": short_w,
                    "long_window":  long_w,
                    "short_value":  s_dd,
                    "long_value":   l_dd,
                    "delta":        delta,
                    "severity":     sev,
                    "detail": (
                        f"Last-{short_w} avg drawdown {s_dd:.2f}% "
                        f"vs last-{long_w} {l_dd:.2f}% (Δ={delta:+.2f}%)"
                    ),
                })
                log.warning(
                    "meta_performance: DRAWDOWN_WORSENING %s "
                    "last-%d=%.2f%% last-%d=%.2f%%",
                    sev, short_w, s_dd, long_w, l_dd,
                )

    events.sort(key=lambda e: (0 if e["severity"] == "HIGH" else 1, e["type"], e["short_window"]))
    return events


# ── Confidence inflation ──────────────────────────────────────────────────────

def confidence_inflation(windows: dict) -> list:
    """
    Detect confidence rising while returns fall.

    Two checks:
        CONFIDENCE_INFLATION       — shortest vs longest window divergence
        CONFIDENCE_MONOTONE_DRIFT  — confidence monotone-decreasing as window
                                     grows across at least 3 sizes (inflation pattern)

    Returns events sorted by severity (HIGH first).
    """
    events = []
    sizes  = sorted(windows.keys())

    if len(sizes) < 2:
        return events

    short_w, long_w = sizes[0], sizes[-1]
    short = windows[short_w]
    long_ = windows[long_w]

    s_conf = short.get("avg_confidence")
    l_conf = long_.get("avg_confidence")
    s_ret  = short.get("avg_return_5d")
    l_ret  = long_.get("avg_return_5d")

    # ── Primary: inflation pair ───────────────────────────────────────────────
    if (s_conf is not None and l_conf is not None
            and s_ret is not None and l_ret is not None):
        conf_delta   = round(s_conf - l_conf, 2)
        return_delta = round(s_ret  - l_ret,  2)

        if conf_delta > INFLATION_CONF_RISE and return_delta < -INFLATION_RETURN_FALL:
            sev = "HIGH" if conf_delta > INFLATION_CONF_HIGH else "MEDIUM"
            events.append({
                "type":             "CONFIDENCE_INFLATION",
                "short_window":     short_w,
                "long_window":      long_w,
                "conf_delta":       conf_delta,
                "return_delta":     return_delta,
                "short_confidence": s_conf,
                "long_confidence":  l_conf,
                "short_return":     s_ret,
                "long_return":      l_ret,
                "severity":         sev,
                "detail": (
                    f"Confidence +{conf_delta:.1f}pp in last-{short_w} ({s_conf:.1f}%) "
                    f"vs last-{long_w} ({l_conf:.1f}%) "
                    f"while return fell {return_delta:.2f}%"
                ),
            })
            log.warning(
                "meta_performance: CONFIDENCE_INFLATION %s — "
                "conf +%.1fpp, return %.2f%%",
                sev, conf_delta, return_delta,
            )

    # ── Secondary: monotone confidence drift across ≥ 3 windows ─────────────
    conf_by_size = [
        (w, windows[w].get("avg_confidence"))
        for w in sizes
        if windows[w].get("avg_confidence") is not None
    ]
    if len(conf_by_size) >= 3:
        conf_vals = [c for _, c in conf_by_size]
        # Confidence should generally be stable; monotone decreasing from
        # short→long means the most recent alerts carry inflated confidence.
        is_monotone = all(
            conf_vals[i] >= conf_vals[i + 1]
            for i in range(len(conf_vals) - 1)
        )
        total_drift = round(conf_vals[0] - conf_vals[-1], 2)
        if is_monotone and total_drift > INFLATION_CONF_RISE:
            events.append({
                "type":      "CONFIDENCE_MONOTONE_DRIFT",
                "conf_delta": total_drift,
                "by_window":  {w: c for w, c in conf_by_size},
                "severity":  "MEDIUM",
                "detail": (
                    "Confidence drifting upward in recent windows: "
                    + ", ".join(f"last-{w}={c:.1f}%" for w, c in conf_by_size)
                ),
            })
            log.warning(
                "meta_performance: CONFIDENCE_MONOTONE_DRIFT +%.1fpp across windows",
                total_drift,
            )

    events.sort(key=lambda e: (0 if e["severity"] == "HIGH" else 1, e["type"]))
    return events


# ── Regime deterioration ──────────────────────────────────────────────────────

def regime_deterioration(rows: list, windows: dict) -> list:
    """
    Detect per-regime degradation by comparing the most recent rolling window
    against overall history.

    Checks:
        BULL_WEAKENING             — recent BULL win rate declining
        NEUTRAL_WEAKENING          — recent NEUTRAL win rate declining
        RISK_OFF_OUTPERFORMING_BULL — regime inversion in the recent window

    Returns events sorted by severity (HIGH first), then type.
    """
    events = []
    if not rows:
        return events

    sizes = sorted(windows.keys())
    if not sizes:
        return events

    short_w    = sizes[0]
    short_rows = rows[-short_w:] if len(rows) >= short_w else rows[:]

    # ── Per-regime weakening (recent vs overall) ──────────────────────────────
    for regime, event_type in (
        (BULL,    "BULL_WEAKENING"),
        (NEUTRAL, "NEUTRAL_WEAKENING"),
    ):
        recent_wr  = _regime_win_rate(short_rows, regime)
        overall_wr = _regime_win_rate(rows,       regime)

        if recent_wr is not None and overall_wr is not None:
            delta = round(recent_wr - overall_wr, 2)
            if delta < -REGIME_DEGRAD_THRESHOLD:
                sev = "HIGH" if delta < -REGIME_DEGRAD_HIGH else "MEDIUM"
                events.append({
                    "type":            event_type,
                    "regime":          regime,
                    "recent_win_rate": recent_wr,
                    "overall_win_rate": overall_wr,
                    "delta":           delta,
                    "severity":        sev,
                    "detail": (
                        f"{regime} win rate: last-{short_w}={recent_wr:.1f}% "
                        f"vs overall={overall_wr:.1f}% (Δ={delta:+.1f}pp)"
                    ),
                })
                log.warning(
                    "meta_performance: %s %s — recent=%.1f%% overall=%.1f%%",
                    event_type, sev, recent_wr, overall_wr,
                )

    # ── RISK_OFF outperforming BULL in recent window ──────────────────────────
    recent_bull_wr    = _regime_win_rate(short_rows, BULL)
    recent_risk_off_wr = _regime_win_rate(short_rows, RISK_OFF)

    if recent_bull_wr is not None and recent_risk_off_wr is not None:
        delta = round(recent_risk_off_wr - recent_bull_wr, 2)
        if delta > REGIME_DEGRAD_THRESHOLD:
            sev = "HIGH" if delta > REGIME_DEGRAD_HIGH else "MEDIUM"
            events.append({
                "type":              "RISK_OFF_OUTPERFORMING_BULL",
                "regime":            RISK_OFF,
                "bull_win_rate":     recent_bull_wr,
                "risk_off_win_rate": recent_risk_off_wr,
                "delta":             delta,
                "severity":          sev,
                "detail": (
                    f"RISK_OFF {recent_risk_off_wr:.1f}% > BULL {recent_bull_wr:.1f}% "
                    f"in last-{short_w} window (Δ={delta:+.1f}pp) — "
                    "regime suppression may be misfiring"
                ),
            })
            log.warning(
                "meta_performance: RISK_OFF_OUTPERFORMING_BULL %s — "
                "risk_off=%.1f%% bull=%.1f%%",
                sev, recent_risk_off_wr, recent_bull_wr,
            )

    events.sort(key=lambda e: (0 if e["severity"] == "HIGH" else 1, e["type"]))
    return events


# ── Safeguard recommendations ─────────────────────────────────────────────────

def safeguard_recommendations(
    degrad_events:   list,
    inflate_events:  list,
    regime_events:   list,
) -> list:
    """
    Translate event counts and severities into actionable recommendations.

    Ladder (cumulative; each level adds a stricter recommendation):
        Any event                → REDUCE_AGGRESSIVENESS
        Any inflation event      → INCREASE_CONFIDENCE_THRESHOLD
        3+ HIGH-severity events  → PAUSE_ADAPTIVE_ROLLOUT
        5+ HIGH-severity events  → OBSERVATION_ONLY

    Returns recs in the order above so the caller can take the most severe
    that applies.
    """
    all_events  = degrad_events + inflate_events + regime_events
    high_events = [e for e in all_events if e.get("severity") == "HIGH"]
    high_count  = len(high_events)

    recs = []

    if all_events:
        top_types = list(dict.fromkeys(e["type"] for e in all_events))[:3]
        recs.append({
            "recommendation": "REDUCE_AGGRESSIVENESS",
            "reason": (
                f"{len(all_events)} performance warning(s) detected — "
                "tighten entry criteria"
            ),
            "triggered_by": top_types,
            "severity": "HIGH" if high_count >= SAFEGUARD_REDUCE_HIGH_COUNT else "MEDIUM",
        })

    if inflate_events:
        recs.append({
            "recommendation": "INCREASE_CONFIDENCE_THRESHOLD",
            "reason": (
                "Confidence inflating while returns are falling — "
                "raise minimum confidence threshold"
            ),
            "triggered_by": list(dict.fromkeys(e["type"] for e in inflate_events)),
            "severity": (
                "HIGH"
                if any(e.get("severity") == "HIGH" for e in inflate_events)
                else "MEDIUM"
            ),
        })

    if high_count >= SAFEGUARD_PAUSE_HIGH_COUNT:
        recs.append({
            "recommendation": "PAUSE_ADAPTIVE_ROLLOUT",
            "reason": (
                f"{high_count} HIGH-severity events suggest adaptive weights "
                "may be compounding degradation"
            ),
            "triggered_by": list(dict.fromkeys(e["type"] for e in high_events))[:5],
            "severity": "HIGH",
        })

    if high_count >= SAFEGUARD_CRITICAL_HIGH_COUNT:
        recs.append({
            "recommendation": "OBSERVATION_ONLY",
            "reason": (
                f"{high_count} HIGH-severity events indicate critical degradation — "
                "suspend action-based alerts"
            ),
            "triggered_by": list(dict.fromkeys(e["type"] for e in high_events))[:5],
            "severity": "HIGH",
        })

    log.info(
        "meta_performance: safeguard recs=%d (high_events=%d)",
        len(recs), high_count,
    )
    return recs


# ── Report builder ────────────────────────────────────────────────────────────

def _build_warnings(
    windows:      dict,
    degrad_events: list,
    inflate_events: list,
    regime_events: list,
    safeguards:    list,
) -> list:
    """Collect human-readable warning strings for the report."""
    warnings = []

    n_valid = sum(1 for s in windows.values() if s.get("win_rate") is not None)
    if n_valid == 0:
        warnings.append(
            f"No rolling windows have enough data for win rate "
            f"(need >= {MIN_ROWS_FOR_STATS} rows)"
        )

    for e in degrad_events:
        if e.get("severity") == "HIGH":
            warnings.append(f"[HIGH] {e['type']}: {e['detail']}")

    for e in inflate_events:
        sev = e.get("severity", "MEDIUM")
        warnings.append(f"[{sev}] {e['type']}: {e['detail']}")

    for e in regime_events:
        if e.get("severity") == "HIGH":
            warnings.append(f"[HIGH] {e['type']}: {e['detail']}")

    for rec in safeguards:
        if rec.get("severity") == "HIGH":
            warnings.append(
                f"SAFEGUARD {rec['recommendation']}: {rec['reason']}"
            )

    return warnings


def generate_report(rows: Optional[list] = None) -> dict:
    """
    Full meta-performance analytics report.

    If rows is None, fetches all COMPLETE outcomes from the DB.
    Pass a pre-fetched list to avoid DB access (e.g. in tests).

    Returns
    -------
    {
        "row_count":                int,
        "windows": {
            10:  { window_stats },
            25:  { window_stats },
            50:  { window_stats },
            100: { window_stats },
        },
        "strongest_window":          int|None,  # window size with highest win_rate
        "weakest_window":            int|None,  # window size with lowest win_rate
        "degradation_events":        [ event_dict, ... ],
        "inflation_events":          [ event_dict, ... ],
        "regime_events":             [ event_dict, ... ],
        "safeguard_recommendations": [ rec_dict, ... ],
        "warnings":                  [ str, ... ],
    }
    """
    if rows is None:
        rows = _fetch_completed_outcomes()

    n = len(rows)
    log.info("meta_performance: generating report on %d completed outcomes", n)

    if n < MIN_ROWS_FOR_STATS:
        log.warning(
            "meta_performance: only %d row(s) — most stats will be sparse "
            "(need >= %d rows for win rate)",
            n, MIN_ROWS_FOR_STATS,
        )

    windows    = rolling_windows(rows)
    degrad     = degradation_detection(windows)
    inflate    = confidence_inflation(windows)
    regime_ev  = regime_deterioration(rows, windows)
    safeguards = safeguard_recommendations(degrad, inflate, regime_ev)
    warnings   = _build_warnings(windows, degrad, inflate, regime_ev, safeguards)

    # Strongest / weakest window by win_rate (skip None)
    valid_wrs = {
        w: stats["win_rate"]
        for w, stats in windows.items()
        if stats["win_rate"] is not None
    }
    strongest = max(valid_wrs, key=lambda w: valid_wrs[w]) if valid_wrs else None
    weakest   = min(valid_wrs, key=lambda w: valid_wrs[w]) if valid_wrs else None

    log.info(
        "meta_performance: degradation=%d inflate=%d regime=%d "
        "safeguards=%d strongest_window=%s weakest_window=%s",
        len(degrad), len(inflate), len(regime_ev),
        len(safeguards), strongest, weakest,
    )

    return {
        "row_count":                n,
        "windows":                  windows,
        "strongest_window":         strongest,
        "weakest_window":           weakest,
        "degradation_events":       degrad,
        "inflation_events":         inflate,
        "regime_events":            regime_ev,
        "safeguard_recommendations": safeguards,
        "warnings":                 warnings,
    }
