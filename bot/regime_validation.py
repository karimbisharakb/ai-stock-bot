"""
Regime effectiveness analytics for the Predator scanner.
Phase 2B — read-only; does NOT modify live scoring.

Validates whether regime filtering (BULL / NEUTRAL / RISK_OFF) improves
trade outcomes, detects inverted regime logic, measures suppression
effectiveness, and summarises cross-regime performance transitions.

All public functions are pure (accept row lists, no I/O) except
generate_report(), which optionally fetches completed outcomes from the DB.

Key concepts
------------
- Suppression effectiveness  : does RISK_OFF have worse outcomes than BULL?
                                If yes, penalising confidence in RISK_OFF is
                                warranted; if no, the filter may be harmful.
- Inversion                  : a higher-risk regime outperforms a lower-risk
                                one (e.g. RISK_OFF win_rate > BULL win_rate).
- Transition degradation      : cross-sectional win-rate delta between
                                consecutive regime pairs
                                (BULL→NEUTRAL, NEUTRAL→RISK_OFF, BULL→RISK_OFF).
- Confidence accuracy         : Pearson(confidence_pct, return_5d) per regime;
                                measures whether confidence remains predictive
                                under each market condition.
"""
import logging
from typing import Optional

from market_regime import BULL, NEUTRAL, RISK_OFF
from outcome_analytics import (
    MIN_ROWS_FOR_STATS,
    _avg,
    _fetch_completed_outcomes,
    _group_by,
    _win_rate,
    is_win,
)

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
# Expected ordering from best to worst performing regime.
REGIME_ORDER: tuple = (BULL, NEUTRAL, RISK_OFF)

# All known regime labels (rows outside this set go into "UNKNOWN" bucket).
ALL_REGIMES: tuple = (BULL, NEUTRAL, RISK_OFF)

# Transitions to analyse (source → target in degradation direction).
_TRANSITIONS: list = [
    (BULL,    NEUTRAL,  f"{BULL}→{NEUTRAL}"),
    (NEUTRAL, RISK_OFF, f"{NEUTRAL}→{RISK_OFF}"),
    (BULL,    RISK_OFF, f"{BULL}→{RISK_OFF}"),
]

# Win-rate delta above which a transition is considered degrading or improving.
DEGRADATION_THRESHOLD: float = 5.0

# Inversion win-rate delta above which severity is upgraded to HIGH.
INVERSION_HIGH_THRESHOLD: float = 20.0


# ── Internal helpers ──────────────────────────────────────────────────────────

def _pearson(xs: list, ys: list) -> Optional[float]:
    """
    Pearson correlation for paired lists; None-pairs are dropped.
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


def _regime_bucket(rows: list) -> dict:
    """
    Extended stats for a group of same-regime rows.
    Includes avg_confidence and confidence_accuracy (Pearson r).
    """
    confs   = [r.get("confidence_pct") for r in rows]
    returns = [r.get("return_5d")       for r in rows]
    return {
        "n":                    len(rows),
        "win_rate":             _win_rate(rows),
        "avg_return_5d":        _avg([r.get("return_5d")        for r in rows]),
        "avg_return_20d":       _avg([r.get("return_20d")       for r in rows]),
        "avg_max_gain":         _avg([r.get("max_gain_pct")     for r in rows]),
        "avg_max_dd":           _avg([r.get("max_drawdown_pct") for r in rows]),
        "avg_confidence":       _avg(confs),
        "confidence_accuracy":  _pearson(confs, returns),
    }


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """b − a; None when either operand is None."""
    if a is None or b is None:
        return None
    return round(b - a, 2)


def _degradation_label(delta: Optional[float]) -> str:
    """Classify a win-rate delta into a human-readable label."""
    if delta is None:
        return "insufficient_data"
    if delta < -DEGRADATION_THRESHOLD:
        return "degrading"
    if delta > DEGRADATION_THRESHOLD:
        return "improving"
    return "stable"


# ── Public analytics ──────────────────────────────────────────────────────────

def regime_stats(rows: list) -> dict:
    """
    Win rate, return, drawdown, and confidence statistics per regime.

    All three known regimes (BULL, NEUTRAL, RISK_OFF) are always present.
    An "UNKNOWN" key is added if any rows have a missing/unrecognised regime.
    Regimes with fewer than MIN_ROWS_FOR_STATS rows have win_rate=None.

    Returns an ordered dict: BULL, NEUTRAL, RISK_OFF, then UNKNOWN (if present).
    """
    groups = _group_by(rows, lambda r: r.get("regime") or "UNKNOWN")
    result: dict = {}
    for regime in REGIME_ORDER:
        result[regime] = _regime_bucket(groups.get(regime, []))
    if "UNKNOWN" in groups:
        result["UNKNOWN"] = _regime_bucket(groups["UNKNOWN"])
    return result


def suppression_analysis(rows: list) -> dict:
    """
    Assess whether regime penalties produce better trade outcomes.

    Effectiveness is measured as win_rate(BULL) − win_rate(RISK_OFF).
    A positive value means BULL outcomes are better than RISK_OFF outcomes,
    confirming that applying a RISK_OFF penalty is warranted.

    Also breaks down RISK_OFF outcomes into gains, losses, and unknowns so
    callers can estimate missed-gain risk vs avoided-loss benefit.

    Returns
    -------
    {
        "bull_stats":            {...},          # _regime_bucket for BULL
        "neutral_stats":         {...},          # _regime_bucket for NEUTRAL
        "risk_off_stats":        {...},          # _regime_bucket for RISK_OFF

        "bull_neutral_delta": {
            "win_rate_delta":    float | None,   # NEUTRAL wr − BULL wr
            "return_5d_delta":   float | None,
            "is_effective":      bool  | None,   # True when BULL > NEUTRAL
        },
        "bull_risk_off_delta": {
            "win_rate_delta":    float | None,   # RISK_OFF wr − BULL wr
            "return_5d_delta":   float | None,
            "is_effective":      bool  | None,   # True when BULL > RISK_OFF
        },

        "risk_off_detail": {
            "loss_count":        int,            # rows where return_5d < 0
            "gain_count":        int,            # rows where return_5d > 0
            "unknown_count":     int,            # rows where return_5d is None
            "missed_gain_frac":  float | None,   # gain_count / (gain+loss)
        },
    }
    """
    groups   = _group_by(rows, lambda r: r.get("regime") or "UNKNOWN")
    bull_r   = groups.get(BULL,     [])
    neutral_r = groups.get(NEUTRAL, [])
    risk_r   = groups.get(RISK_OFF, [])

    bull_stats    = _regime_bucket(bull_r)
    neutral_stats = _regime_bucket(neutral_r)
    risk_stats    = _regime_bucket(risk_r)

    bull_wr    = bull_stats["win_rate"]
    neutral_wr = neutral_stats["win_rate"]
    risk_wr    = risk_stats["win_rate"]

    bull_ret    = bull_stats["avg_return_5d"]
    neutral_ret = neutral_stats["avg_return_5d"]
    risk_ret    = risk_stats["avg_return_5d"]

    # Effectiveness: positive = BULL outperforms → penalty is warranted
    bn_wr_delta  = _delta(bull_wr,    neutral_wr)   # should be negative (NEUTRAL worse)
    bro_wr_delta = _delta(bull_wr,    risk_wr)      # should be negative (RISK_OFF worse)

    def _is_effective(bull_wr_, other_wr_):
        if bull_wr_ is None or other_wr_ is None:
            return None
        return bull_wr_ > other_wr_

    # RISK_OFF outcome breakdown
    ro_losses  = [r for r in risk_r if (r.get("return_5d") or 0.0) < 0]
    ro_gains   = [r for r in risk_r if (r.get("return_5d") or 0.0) > 0]
    ro_unknown = [r for r in risk_r if r.get("return_5d") is None]

    gain_n  = len(ro_gains)
    loss_n  = len(ro_losses)
    total_g_l = gain_n + loss_n
    missed_gain_frac = round(gain_n / total_g_l, 4) if total_g_l > 0 else None

    if bro_wr_delta is not None:
        effective = _is_effective(bull_wr, risk_wr)
        label = "effective" if effective else ("inverted" if effective is False else "unknown")
        log.info(
            "regime_validation: suppression %s — "
            "BULL=%.1f%% RISK_OFF=%.1f%% Δ=%.1f%%",
            label,
            bull_wr if bull_wr is not None else 0.0,
            risk_wr if risk_wr is not None else 0.0,
            bro_wr_delta,
        )

    return {
        "bull_stats":         bull_stats,
        "neutral_stats":      neutral_stats,
        "risk_off_stats":     risk_stats,
        "bull_neutral_delta": {
            "win_rate_delta":  bn_wr_delta,
            "return_5d_delta": _delta(bull_ret, neutral_ret),
            "is_effective":    _is_effective(bull_wr, neutral_wr),
        },
        "bull_risk_off_delta": {
            "win_rate_delta":  bro_wr_delta,
            "return_5d_delta": _delta(bull_ret, risk_ret),
            "is_effective":    _is_effective(bull_wr, risk_wr),
        },
        "risk_off_detail": {
            "loss_count":       loss_n,
            "gain_count":       gain_n,
            "unknown_count":    len(ro_unknown),
            "missed_gain_frac": missed_gain_frac,
        },
    }


def regime_transition_stats(rows: list) -> dict:
    """
    Cross-sectional performance delta between consecutive regime pairs.

    For each transition direction, computes the win-rate and 5d-return delta
    (target − source) and assigns a degradation label.

    Note: this is cross-sectional (group averages), not a true time-series
    transition — it measures how outcomes differ BETWEEN regimes, which
    approximates what happens when the market moves from one regime to another.

    Returns
    -------
    {
        "BULL→NEUTRAL": {
            "source_regime":   str,
            "target_regime":   str,
            "source_win_rate": float | None,
            "target_win_rate": float | None,
            "win_rate_delta":  float | None,   # target − source
            "return_5d_delta": float | None,
            "degradation":     "degrading" | "improving" | "stable" | "insufficient_data",
        },
        "NEUTRAL→RISK_OFF": { ... },
        "BULL→RISK_OFF":    { ... },
    }
    """
    groups = _group_by(rows, lambda r: r.get("regime") or "UNKNOWN")
    stats  = {r: _regime_bucket(groups.get(r, [])) for r in REGIME_ORDER}

    result: dict = {}
    for src, tgt, label in _TRANSITIONS:
        src_wr  = stats[src]["win_rate"]
        tgt_wr  = stats[tgt]["win_rate"]
        src_ret = stats[src]["avg_return_5d"]
        tgt_ret = stats[tgt]["avg_return_5d"]

        wr_delta  = _delta(src_wr,  tgt_wr)
        ret_delta = _delta(src_ret, tgt_ret)
        degradation = _degradation_label(wr_delta)

        result[label] = {
            "source_regime":   src,
            "target_regime":   tgt,
            "source_win_rate": src_wr,
            "target_win_rate": tgt_wr,
            "win_rate_delta":  wr_delta,
            "return_5d_delta": ret_delta,
            "degradation":     degradation,
        }

        if wr_delta is not None and degradation == "improving":
            log.warning(
                "regime_validation: unexpected improvement %s → %s "
                "(Δ=+%.1f%%) — regime order may be inverted",
                src, tgt, wr_delta,
            )

    return result


def inversion_detection(stats: dict) -> dict:
    """
    Detect regimes where a higher-risk label outperforms a lower-risk one.

    Expected order by win rate: BULL ≥ NEUTRAL ≥ RISK_OFF.
    An inversion occurs when a higher-risk regime has a strictly higher
    win rate than a lower-risk regime (adjacent in REGIME_ORDER).

    Parameters
    ----------
    stats : output of regime_stats() — keyed by regime label

    Returns
    -------
    {
        "has_inversion":   bool,
        "inversion_count": int,
        "inversions": [
            {
                "low_risk_regime":  str,
                "high_risk_regime": str,
                "low_risk_wr":      float,
                "high_risk_wr":     float,
                "delta":            float,   # high_risk − low_risk (positive = inversion)
                "severity":         "HIGH" | "MEDIUM",
            },
            ...
        ],
    }
    """
    # All ordered pairs where the second carries more risk than the first.
    # Includes non-adjacent BULL vs RISK_OFF so the most extreme inversion
    # (the one the requirements explicitly call out) is always detected.
    _pairs = [
        (BULL,    NEUTRAL),
        (NEUTRAL, RISK_OFF),
        (BULL,    RISK_OFF),
    ]

    inversions = []

    for low_risk, high_risk in _pairs:

        low_bucket  = stats.get(low_risk,  {})
        high_bucket = stats.get(high_risk, {})

        low_wr  = low_bucket.get("win_rate")
        high_wr = high_bucket.get("win_rate")

        if low_wr is None or high_wr is None:
            continue  # insufficient data → skip

        # Inversion: higher-risk regime beats lower-risk regime
        if high_wr > low_wr:
            delta    = round(high_wr - low_wr, 2)
            severity = "HIGH" if delta > INVERSION_HIGH_THRESHOLD else "MEDIUM"
            inversions.append({
                "low_risk_regime":  low_risk,
                "high_risk_regime": high_risk,
                "low_risk_wr":      low_wr,
                "high_risk_wr":     high_wr,
                "delta":            delta,
                "severity":         severity,
            })
            log.warning(
                "regime_validation: INVERSION %s | %s (%.1f%%) > %s (%.1f%%) "
                "Δ=+%.1f%% — regime logic may be inverted",
                severity, high_risk, high_wr, low_risk, low_wr, delta,
            )

    return {
        "has_inversion":   len(inversions) > 0,
        "inversion_count": len(inversions),
        "inversions":      inversions,
    }


def _build_warnings(
    stats:       dict,
    suppression: dict,
    inv:         dict,
    transitions: dict,
) -> list:
    """Collect human-readable warning strings for the report."""
    warnings = []

    # Sparse-regime warnings
    for regime in ALL_REGIMES:
        n = stats.get(regime, {}).get("n", 0)
        if 0 < n < MIN_ROWS_FOR_STATS:
            warnings.append(
                f"Regime {regime} has only {n} row(s) "
                f"(need {MIN_ROWS_FOR_STATS} for reliable win rate)"
            )

    # Inversion warnings
    for inv_entry in inv["inversions"]:
        warnings.append(
            f"Regime inversion ({inv_entry['severity']}): "
            f"{inv_entry['high_risk_regime']} ({inv_entry['high_risk_wr']:.1f}%) "
            f"outperforms {inv_entry['low_risk_regime']} ({inv_entry['low_risk_wr']:.1f}%), "
            f"Δ=+{inv_entry['delta']:.1f}%"
        )

    # Suppression effectiveness
    bro = suppression.get("bull_risk_off_delta", {})
    bro_eff = bro.get("is_effective")
    if bro_eff is False:
        delta = bro.get("win_rate_delta")
        warnings.append(
            f"RISK_OFF suppression appears INEFFECTIVE: "
            f"RISK_OFF outperforms BULL"
            + (f" by {-delta:.1f}%" if delta is not None else "")
        )

    # Unexpected improvement in transitions
    for label, t in transitions.items():
        if t["degradation"] == "improving":
            warnings.append(
                f"Unexpected improvement in transition {label}: "
                f"target win_rate {t['target_win_rate']:.1f}% > "
                f"source win_rate {t['source_win_rate']:.1f}%"
            )

    return warnings


def generate_report(rows: Optional[list] = None) -> dict:
    """
    Full regime effectiveness report.

    If rows is None, fetches COMPLETE outcomes from the DB.

    Returns
    -------
    {
        "row_count":             int,
        "regime_stats":          { regime: {...} },
        "suppression":           { ... },
        "transitions":           { transition_label: {...} },
        "inversion":             { has_inversion, inversion_count, inversions },
        "strongest_regime":      { regime, win_rate }  | None,
        "weakest_regime":        { regime, win_rate }  | None,
        "warnings":              [ str, ... ],
    }
    """
    if rows is None:
        rows = _fetch_completed_outcomes()

    n = len(rows)
    log.info("regime_validation: generating report on %d completed outcomes", n)

    if n < MIN_ROWS_FOR_STATS:
        log.warning(
            "regime_validation: only %d row(s) — regime metrics require >= %d rows",
            n, MIN_ROWS_FOR_STATS,
        )

    stats       = regime_stats(rows)
    suppression = suppression_analysis(rows)
    transitions = regime_transition_stats(rows)
    inv         = inversion_detection(stats)
    warnings    = _build_warnings(stats, suppression, inv, transitions)

    # Strongest / weakest by win rate (known regimes only)
    eligible = [
        {"regime": r, "win_rate": stats[r]["win_rate"], "n": stats[r]["n"]}
        for r in REGIME_ORDER
        if stats[r].get("win_rate") is not None
    ]
    eligible_sorted = sorted(eligible, key=lambda x: (-x["win_rate"], x["regime"]))
    strongest = eligible_sorted[0]  if eligible_sorted else None
    weakest   = eligible_sorted[-1] if eligible_sorted else None

    log.info(
        "regime_validation: BULL n=%d wr=%s | NEUTRAL n=%d wr=%s | "
        "RISK_OFF n=%d wr=%s | inversions=%d warnings=%d",
        stats[BULL]["n"],    stats[BULL]["win_rate"],
        stats[NEUTRAL]["n"], stats[NEUTRAL]["win_rate"],
        stats[RISK_OFF]["n"],stats[RISK_OFF]["win_rate"],
        inv["inversion_count"], len(warnings),
    )

    return {
        "row_count":        n,
        "regime_stats":     stats,
        "suppression":      suppression,
        "transitions":      transitions,
        "inversion":        inv,
        "strongest_regime": strongest,
        "weakest_regime":   weakest,
        "warnings":         warnings,
    }
