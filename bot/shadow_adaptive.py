"""
Shadow adaptive scoring engine for the Predator scanner.
Phase 4C — read-only; does NOT mutate live scoring logic.

Runs adaptive weight recommendations in parallel with the live engine.
Measures whether adaptive adjustments improve outcomes before any live
adaptation occurs.  All public functions are pure (accept row lists and
pre-built dicts) — no DB access, no live engine mutation.

Key capabilities
----------------
  apply_shadow_weights         map live signal scores to shadow-adjusted scores
  simulate_shadow_score        total raw score under adaptive weights
  simulate_shadow_confidence   confidence estimate under shadow weights
  simulate_shadow_tier         tier classification under shadow confidence
  run_shadow_replay            per-alert live-vs-shadow delta comparison
  compare_live_vs_shadow       aggregate win-rate / return / calibration stats
  stability_analysis           oscillation, variance, overreaction detection
  rollout_readiness            readiness ladder evaluation
  generate_shadow_report       full hedge-fund-style comparison report

Readiness ladder
----------------
  NOT_READY                  insufficient data or regression detected
  OBSERVE_LONGER             promising but sample too small
  LIMITED_TRIAL_READY        consistent improvement + stable weights
  STABLE_FOR_CONTROLLED_USE  strong evidence + long track record

Stability labels
----------------
  STABLE    weight recommendations are consistent across snapshots
  WATCH     elevated variance or large single-step adjustments
  UNSTABLE  oscillating sign changes — do not promote
"""
import logging
import math
from typing import Optional

from adaptive_weights import DEFAULT_WEIGHTS
from outcome_analytics import MIN_ROWS_FOR_STATS, is_win

log = logging.getLogger(__name__)

# ── Signal universe ───────────────────────────────────────────────────────────

SIGNAL_NAMES: tuple = (
    "options", "insider", "short_squeeze", "catalyst", "institutional", "breakout"
)

# ── Live engine mirrors ───────────────────────────────────────────────────────

ALERT_THRESHOLD:              float = 6.0    # mirrors predator.py
CONVICTION_CONFIDENCE_MIN:    float = 55.0   # mirrors BASELINE_CONVICTION_THRESHOLD
CONVICTION_MIN_SIGNALS:       int   = 3      # mirrors BASELINE_CONVICTION_MIN_SIGS
STANDARD_CONFIDENCE_MIN:      float = 40.0

TIER_CONVICTION = "CONVICTION"
TIER_STANDARD   = "STANDARD"
TIER_ALERT      = "ALERT"

# ── Readiness ladder ──────────────────────────────────────────────────────────

READINESS_NOT_READY = "NOT_READY"
READINESS_OBSERVE   = "OBSERVE_LONGER"
READINESS_LIMITED   = "LIMITED_TRIAL_READY"
READINESS_STABLE    = "STABLE_FOR_CONTROLLED_USE"

READINESS_ORDER: dict = {
    READINESS_NOT_READY: 0,
    READINESS_OBSERVE:   1,
    READINESS_LIMITED:   2,
    READINESS_STABLE:    3,
}

# ── Weight stability labels ───────────────────────────────────────────────────

STABILITY_STABLE   = "STABLE"
STABILITY_WATCH    = "WATCH"
STABILITY_UNSTABLE = "UNSTABLE"

# ── Minimum data thresholds ───────────────────────────────────────────────────

MIN_ROWS_SHADOW:    int = 10   # minimum for any shadow analytics
MIN_ROWS_READINESS: int = 30   # minimum to consider LIMITED_TRIAL_READY
MIN_ROWS_STABLE:    int = 60   # minimum to consider STABLE_FOR_CONTROLLED_USE

# ── Stability thresholds ──────────────────────────────────────────────────────

OSCILLATION_FLIP_THRESHOLD: int   = 2     # ≥N sign changes across snapshots → oscillating
HIGH_VARIANCE_THRESHOLD:    float = 0.15  # std(suggested_weights) above this → watch
OVERREACTION_THRESHOLD:     float = 0.40  # |adj| above this → overreacting

# ── Churn / divergence ────────────────────────────────────────────────────────

CHURN_EXCESSIVE_THRESHOLD:  float = 0.20  # > 20% of alerts change inclusion
DIVERGENCE_LOG_THRESHOLD:   float = 15.0  # pp confidence divergence → log warning

# ── Improvement thresholds ────────────────────────────────────────────────────

WIN_RATE_IMPROVEMENT_MIN:    float = 2.0    # pp shadow improvement required
CALIBRATION_IMPROVEMENT_MIN: float = 0.005  # Brier reduction to count as improvement

# ── Change detection ──────────────────────────────────────────────────────────

WIN_RATE_DELTA_CHANGE_MIN: float = 3.0  # pp shift in win_rate_delta to record change

# ── Output bounds ─────────────────────────────────────────────────────────────

MAX_DELTAS_IN_REPORT:   int = 20
MAX_IMPROVEMENTS:       int = 5
MAX_REGRESSIONS:        int = 5
MAX_STABILITY_WARNINGS: int = 10
MAX_RECOMMENDATIONS:    int = 10


# ── Internal signal score extractor ──────────────────────────────────────────

def _live_signal_scores(row: dict) -> dict:
    """
    Extract per-signal live scores from a row dict.

    Tries individual score columns (score_options, score_insider, …) first.
    Falls back to parsing a flat ``signal_summary`` JSON string.
    Returns {signal_name: float} for all SIGNAL_NAMES; absent signals = 0.0.
    """
    has_cols = any(f"score_{s}" in row for s in SIGNAL_NAMES)
    if has_cols:
        return {sig: float(row.get(f"score_{sig}") or 0.0) for sig in SIGNAL_NAMES}

    import json
    try:
        raw = json.loads(row.get("signal_summary") or "{}")
        scores = {}
        for sig in SIGNAL_NAMES:
            val = raw.get(sig)
            if isinstance(val, dict):
                scores[sig] = float(val.get("score") or 0.0)
            else:
                scores[sig] = float(val or 0.0)
        return scores
    except (json.JSONDecodeError, TypeError, AttributeError, ValueError):
        return {sig: 0.0 for sig in SIGNAL_NAMES}


# ── Shadow simulation primitives ──────────────────────────────────────────────

def apply_shadow_weights(row: dict, weight_adjustments: dict) -> dict:
    """
    Apply adaptive weight adjustments to a row's live signal scores.

    Each signal's shadow score = live_score × (suggested_weight / default_weight).
    When suggested equals default (no adjustment) the shadow score is identical to live.

    Returns {signal_name: shadow_score} for all SIGNAL_NAMES.
    """
    live = _live_signal_scores(row)
    shadow: dict = {}

    for sig in SIGNAL_NAMES:
        live_s  = live.get(sig, 0.0)
        adj     = (weight_adjustments or {}).get(sig) or {}
        default = adj.get("default_weight") or DEFAULT_WEIGHTS.get(sig, 1.0)
        suggest = adj.get("suggested_weight") or default

        if default > 0 and live_s > 0:
            shadow[sig] = round(live_s * (suggest / default), 4)
        else:
            shadow[sig] = live_s

    return shadow


def simulate_shadow_score(row: dict, weight_adjustments: Optional[dict] = None) -> float:
    """Total raw shadow score: sum of all shadow-weighted signal scores."""
    return round(sum(apply_shadow_weights(row, weight_adjustments or {}).values()), 4)


def simulate_shadow_confidence(
    row: dict,
    weight_adjustments: Optional[dict] = None,
) -> float:
    """
    Estimate confidence under adaptive weights via proportional scaling.

    Derives the implicit regime factor from the live adjusted/raw score pair,
    applies it to the shadow raw score, then scales confidence proportionally.
    Clamped to [0.0, 100.0].
    """
    live_conf = float(row.get("confidence_pct") or 0.0)
    live_raw  = float(row.get("raw_score")       or 0.0)
    live_adj  = float(row.get("adjusted_score") or row.get("score") or 0.0)

    # Infer regime factor from live data
    regime_factor = live_adj / live_raw if live_raw > 0 else 1.0

    shadow_raw = simulate_shadow_score(row, weight_adjustments)
    shadow_adj = shadow_raw * regime_factor

    if live_adj > 0:
        shadow_conf = live_conf * (shadow_adj / live_adj)
    else:
        shadow_conf = live_conf

    return round(max(0.0, min(100.0, shadow_conf)), 4)


def simulate_shadow_tier(shadow_confidence: float, n_active_signals: int) -> str:
    """
    Classify an alert into a tier based on shadow confidence and active signal count.

    Mirrors the live tier logic without importing the live engine.
    """
    if (shadow_confidence >= CONVICTION_CONFIDENCE_MIN
            and n_active_signals >= CONVICTION_MIN_SIGNALS):
        return TIER_CONVICTION
    if shadow_confidence >= STANDARD_CONFIDENCE_MIN:
        return TIER_STANDARD
    return TIER_ALERT


# ── Per-alert delta ───────────────────────────────────────────────────────────

def _shadow_row_delta(
    row:                dict,
    weight_adjustments: dict,
    idx:                int = 0,
) -> dict:
    """
    Compute live-vs-shadow delta for a single row.

    Infers the regime factor from live adjusted/raw scores so the shadow
    adjusted score uses the same regime penalty as the live alert.
    """
    live_adj  = float(row.get("adjusted_score") or row.get("score") or 0.0)
    live_raw  = float(row.get("raw_score")       or 0.0)
    live_conf = float(row.get("confidence_pct")  or 0.0)
    live_tier = str(row.get("tier")              or TIER_ALERT)
    regime    = str(row.get("regime")            or "BULL")

    regime_factor = live_adj / live_raw if live_raw > 0 else 1.0

    shadow_raw    = simulate_shadow_score(row, weight_adjustments)
    shadow_adj    = round(shadow_raw * regime_factor, 4)
    shadow_conf   = simulate_shadow_confidence(row, weight_adjustments)

    live_signals   = _live_signal_scores(row)
    shadow_signals = apply_shadow_weights(row, weight_adjustments)
    n_active_live   = sum(1 for v in live_signals.values()   if v > 0)
    n_active_shadow = sum(1 for v in shadow_signals.values() if v > 0)

    shadow_tier = simulate_shadow_tier(shadow_conf, n_active_shadow)

    live_included   = live_adj   >= ALERT_THRESHOLD
    shadow_included = shadow_adj >= ALERT_THRESHOLD

    score_delta = round(shadow_adj  - live_adj,  4)
    conf_delta  = round(shadow_conf - live_conf, 4)

    if abs(conf_delta) >= DIVERGENCE_LOG_THRESHOLD:
        log.warning(
            "shadow_adaptive: large confidence divergence for %s (idx=%d): "
            "live=%.1f%% shadow=%.1f%% delta=%+.1f%%",
            row.get("ticker", "?"), idx, live_conf, shadow_conf, conf_delta,
        )

    return {
        "idx":               idx,
        "ticker":            row.get("ticker"),
        "regime":            regime,
        "live_score":        live_adj,
        "shadow_score":      shadow_adj,
        "score_delta":       score_delta,
        "live_confidence":   live_conf,
        "shadow_confidence": shadow_conf,
        "confidence_delta":  conf_delta,
        "live_tier":         live_tier,
        "shadow_tier":       shadow_tier,
        "tier_changed":      live_tier != shadow_tier,
        "live_included":     live_included,
        "shadow_included":   shadow_included,
        "inclusion_changed": live_included != shadow_included,
        "return_5d":         row.get("return_5d"),
        "n_active_live":     n_active_live,
        "n_active_shadow":   n_active_shadow,
    }


# ── Shadow replay ─────────────────────────────────────────────────────────────

def run_shadow_replay(
    rows:               Optional[list] = None,
    weight_adjustments: Optional[dict] = None,
) -> dict:
    """
    Run per-alert shadow replay on all rows.

    Returns
    -------
    {
        "deltas":              list of per-alert delta dicts,
        "n_rows":              int,
        "n_inclusion_changes": int,
        "n_tier_changes":      int,
        "churn_rate":          float,
    }

    Because all input rows come from alerts that already fired, live_included
    is always True.  Shadow can only EXCLUDE rows (not add hypothetical ones).
    """
    _rows = rows or []
    if not _rows:
        return {
            "deltas": [], "n_rows": 0,
            "n_inclusion_changes": 0, "n_tier_changes": 0, "churn_rate": 0.0,
        }

    wa     = weight_adjustments or {}
    deltas = [_shadow_row_delta(row, wa, i) for i, row in enumerate(_rows)]

    n             = len(deltas)
    n_inclusion   = sum(1 for d in deltas if d["inclusion_changed"])
    n_tier        = sum(1 for d in deltas if d["tier_changed"])
    churn_rate    = round(n_inclusion / n, 4) if n > 0 else 0.0

    if churn_rate > CHURN_EXCESSIVE_THRESHOLD:
        log.warning(
            "shadow_adaptive: excessive alert churn %.1f%% "
            "(%d/%d rows changed inclusion status)",
            churn_rate * 100, n_inclusion, n,
        )

    return {
        "deltas":              deltas,
        "n_rows":              n,
        "n_inclusion_changes": n_inclusion,
        "n_tier_changes":      n_tier,
        "churn_rate":          churn_rate,
    }


# ── Statistical helpers ───────────────────────────────────────────────────────

def _brier(pairs: list) -> Optional[float]:
    """
    Brier score from (confidence_pct, is_win_int) pairs.

    Returns None when fewer than MIN_ROWS_SHADOW valid pairs.
    Lower is better (0 = perfect calibration).
    """
    valid = [
        ((conf / 100.0) - float(win)) ** 2
        for conf, win in pairs
        if conf is not None and win is not None
    ]
    if len(valid) < MIN_ROWS_SHADOW:
        return None
    return round(sum(valid) / len(valid), 6)


def _max_drawdown(returns: list) -> Optional[float]:
    """
    Max drawdown (positive %) from an ordered list of return_5d values.

    Uses a running-peak approach: tracks how far the cumulative PnL
    drops from its highest point.  Returns None on empty input.
    """
    if not returns:
        return None
    running = peak = max_dd = 0.0
    for r in returns:
        running += r
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 4)


def _avg_return(returns: list) -> Optional[float]:
    valid = [r for r in returns if r is not None]
    return round(sum(valid) / len(valid), 4) if len(valid) >= MIN_ROWS_SHADOW else None


def _win_rate(rows: list) -> Optional[float]:
    rows_with_outcome = [r for r in rows if r.get("return_5d") is not None]
    if len(rows_with_outcome) < MIN_ROWS_SHADOW:
        return None
    wins = sum(1 for r in rows_with_outcome if is_win(r))
    return round(wins / len(rows_with_outcome) * 100, 2)


def _empty_comparison() -> dict:
    return {
        "n_rows":                     0,
        "live_win_rate":              None,
        "shadow_win_rate":            None,
        "win_rate_delta":             None,
        "live_avg_return":            None,
        "shadow_avg_return":          None,
        "return_delta":               None,
        "live_alert_count":           0,
        "shadow_alert_count":         0,
        "alert_volume_delta":         0,
        "alert_volume_delta_pct":     0.0,
        "live_drawdown":              None,
        "shadow_drawdown":            None,
        "drawdown_delta":             None,
        "live_brier":                 None,
        "shadow_brier":               None,
        "calibration_delta":          None,
        "false_positive_reduction_est": None,
        "n_inclusion_changes":        0,
        "n_tier_changes":             0,
        "churn_rate":                 0.0,
    }


# ── Aggregate comparison ──────────────────────────────────────────────────────

def compare_live_vs_shadow(
    rows:               Optional[list] = None,
    weight_adjustments: Optional[dict] = None,
) -> dict:
    """
    Aggregate live-vs-shadow comparison statistics.

    Win rates and returns are computed only on rows with known ``return_5d``
    outcomes.  Shadow metrics use only rows where ``shadow_included = True``.
    Because all input rows originally alerted, live always includes all rows.

    Returns
    -------
    Comparison dict with keys:
        n_rows, live/shadow win_rate, return, drawdown, brier,
        alert_volume, false_positive_reduction_est, churn stats.
    """
    _rows = rows or []
    if not _rows:
        return _empty_comparison()

    replay  = run_shadow_replay(_rows, weight_adjustments)
    deltas  = replay["deltas"]
    n       = replay["n_rows"]

    # Build per-row shadow confidence lookup (by idx)
    shadow_conf_by_idx: dict = {d["idx"]: d["shadow_confidence"] for d in deltas}
    shadow_included_idxs: frozenset = frozenset(
        d["idx"] for d in deltas if d["shadow_included"]
    )

    # Rows with known outcomes
    known_rows         = [r for r in _rows if r.get("return_5d") is not None]
    shadow_known_rows  = [r for i, r in enumerate(_rows)
                          if i in shadow_included_idxs
                          and r.get("return_5d") is not None]

    live_wr   = _win_rate(known_rows)
    shadow_wr = _win_rate(shadow_known_rows)
    wr_delta  = (round(shadow_wr - live_wr, 2)
                 if live_wr is not None and shadow_wr is not None else None)

    live_returns   = [r["return_5d"] for r in known_rows]
    shadow_returns = [r["return_5d"] for r in shadow_known_rows]

    live_avg   = _avg_return(live_returns)
    shadow_avg = _avg_return(shadow_returns)
    ret_delta  = (round(shadow_avg - live_avg, 4)
                  if live_avg is not None and shadow_avg is not None else None)

    live_dd   = _max_drawdown(live_returns)
    shadow_dd = _max_drawdown(shadow_returns)
    dd_delta  = (round(shadow_dd - live_dd, 4)
                 if live_dd is not None and shadow_dd is not None else None)

    # Brier scores (live uses original confidence_pct)
    live_brier_pairs = [
        (float(r.get("confidence_pct") or 0.0), int(is_win(r)))
        for r in known_rows
    ]
    shadow_brier_pairs = [
        (shadow_conf_by_idx.get(i, 0.0), int(is_win(r)))
        for i, r in enumerate(_rows)
        if i in shadow_included_idxs and r.get("return_5d") is not None
    ]
    live_brier   = _brier(live_brier_pairs)
    shadow_brier = _brier(shadow_brier_pairs)
    cal_delta    = (round(shadow_brier - live_brier, 6)
                    if live_brier is not None and shadow_brier is not None else None)

    # Alert volume
    shadow_count     = len(shadow_included_idxs)
    live_count       = n
    vol_delta        = shadow_count - live_count
    vol_delta_pct    = round(vol_delta / live_count * 100, 2) if live_count > 0 else 0.0

    # False positive reduction estimate
    excluded_idxs = frozenset(
        d["idx"] for d in deltas if not d["shadow_included"]
    )
    live_losses     = [r for r in _rows if not is_win(r)
                       and r.get("return_5d") is not None]
    excluded_losses = [_rows[i] for i in excluded_idxs
                       if i < len(_rows)
                       and not is_win(_rows[i])
                       and _rows[i].get("return_5d") is not None]
    fp_reduction = (round(len(excluded_losses) / len(live_losses) * 100, 2)
                    if live_losses else None)

    return {
        "n_rows":                     n,
        "live_win_rate":              live_wr,
        "shadow_win_rate":            shadow_wr,
        "win_rate_delta":             wr_delta,
        "live_avg_return":            live_avg,
        "shadow_avg_return":          shadow_avg,
        "return_delta":               ret_delta,
        "live_alert_count":           live_count,
        "shadow_alert_count":         shadow_count,
        "alert_volume_delta":         vol_delta,
        "alert_volume_delta_pct":     vol_delta_pct,
        "live_drawdown":              live_dd,
        "shadow_drawdown":            shadow_dd,
        "drawdown_delta":             dd_delta,
        "live_brier":                 live_brier,
        "shadow_brier":               shadow_brier,
        "calibration_delta":          cal_delta,
        "false_positive_reduction_est": fp_reduction,
        "n_inclusion_changes":        replay["n_inclusion_changes"],
        "n_tier_changes":             replay["n_tier_changes"],
        "churn_rate":                 replay["churn_rate"],
    }


# ── Stability analysis ────────────────────────────────────────────────────────

def stability_analysis(
    snapshot_history: Optional[list] = None,
) -> dict:
    """
    Analyse stability of adaptive weight recommendations across snapshots.

    Each entry in snapshot_history is a ``compute_weight_adjustments()`` result:
    ``{signal_name: {adjustment, suggested_weight, ...}}``.
    Oldest snapshot first.

    Returns
    -------
    {
        "n_snapshots": int,
        "per_signal":  {signal: {oscillations, weight_std, max_adj, label}},
        "overall":     "STABLE" | "WATCH" | "UNSTABLE",
        "warnings":    list of warning strings (capped at MAX_STABILITY_WARNINGS),
    }
    """
    history = snapshot_history or []
    n       = len(history)

    if n == 0:
        return {
            "n_snapshots": 0,
            "per_signal":  {},
            "overall":     STABILITY_STABLE,
            "warnings":    [],
        }

    per_signal: dict = {}
    warnings:   list = []
    any_unstable = False
    any_watch    = False

    for sig in SIGNAL_NAMES:
        adjs      = [float((h.get(sig) or {}).get("adjustment",       0.0)) for h in history]
        suggested = [float((h.get(sig) or {}).get("suggested_weight",
                     DEFAULT_WEIGHTS.get(sig, 1.0)))                         for h in history]

        # Oscillation: count consecutive sign changes (ignoring zeros)
        oscillations = 0
        for i in range(1, len(adjs)):
            a, b = adjs[i - 1], adjs[i]
            if (a > 0 and b < 0) or (a < 0 and b > 0):
                oscillations += 1

        # Variance of suggested weights across snapshots
        if len(suggested) >= 2:
            mean_s   = sum(suggested) / len(suggested)
            variance = sum((s - mean_s) ** 2 for s in suggested) / len(suggested)
            weight_std = round(math.sqrt(variance), 4)
        else:
            weight_std = 0.0

        max_adj = round(max(abs(a) for a in adjs), 4)

        # Classify signal stability
        if oscillations >= OSCILLATION_FLIP_THRESHOLD:
            label        = STABILITY_UNSTABLE
            any_unstable = True
            warnings.append(
                f"[UNSTABLE] {sig}: oscillating weight recommendation "
                f"({oscillations} sign changes across {n} snapshot(s))"
            )
        elif weight_std > HIGH_VARIANCE_THRESHOLD or max_adj > OVERREACTION_THRESHOLD:
            label     = STABILITY_WATCH
            any_watch = True
            if weight_std > HIGH_VARIANCE_THRESHOLD:
                warnings.append(
                    f"[WATCH] {sig}: high weight variance "
                    f"(std={weight_std:.3f} > {HIGH_VARIANCE_THRESHOLD})"
                )
            if max_adj > OVERREACTION_THRESHOLD:
                warnings.append(
                    f"[WATCH] {sig}: large single-step adjustment "
                    f"(max_adj={max_adj:.3f} > {OVERREACTION_THRESHOLD})"
                )
        else:
            label = STABILITY_STABLE

        per_signal[sig] = {
            "oscillations": oscillations,
            "weight_std":   weight_std,
            "max_adj":      max_adj,
            "label":        label,
        }

    if any_unstable:
        overall = STABILITY_UNSTABLE
    elif any_watch:
        overall = STABILITY_WATCH
    else:
        overall = STABILITY_STABLE

    if overall in (STABILITY_WATCH, STABILITY_UNSTABLE):
        log.warning(
            "shadow_adaptive: weight stability is %s across %d snapshot(s)",
            overall, n,
        )

    return {
        "n_snapshots": n,
        "per_signal":  per_signal,
        "overall":     overall,
        "warnings":    warnings[:MAX_STABILITY_WARNINGS],
    }


# ── Rollout readiness ─────────────────────────────────────────────────────────

def rollout_readiness(
    comparison: Optional[dict] = None,
    stability:  Optional[dict] = None,
    n_rows:     int             = 0,
) -> dict:
    """
    Evaluate readiness to roll out adaptive weights to live production.

    Applies the readiness ladder:
      - Blockers (any → NOT_READY): unstable weights, shadow win-rate regression
      - Insufficient data (< MIN_ROWS_SHADOW → NOT_READY regardless)
      - Marginal data (< MIN_ROWS_READINESS → OBSERVE_LONGER)
      - Consistent improvement + stable + large sample → STABLE_FOR_CONTROLLED_USE
      - Consistent improvement + stable → LIMITED_TRIAL_READY
      - Otherwise → OBSERVE_LONGER

    Returns {status, reasons, blockers}.
    """
    comp  = comparison or {}
    stab  = stability  or {}
    n     = n_rows or comp.get("n_rows") or 0

    reasons:  list = []
    blockers: list = []

    # ── Minimum data gate ──────────────────────────────────────────────────────
    if n < MIN_ROWS_SHADOW:
        blockers.append(
            f"Insufficient data: {n} rows (need ≥ {MIN_ROWS_SHADOW} for shadow analytics)"
        )
        return {"status": READINESS_NOT_READY, "reasons": reasons, "blockers": blockers}

    # ── Stability check ────────────────────────────────────────────────────────
    stab_overall = stab.get("overall") or STABILITY_STABLE
    if stab_overall == STABILITY_UNSTABLE:
        blockers.append(
            "Weight recommendations are UNSTABLE — oscillating or excessive variance"
        )
    elif stab_overall == STABILITY_WATCH:
        reasons.append("Weight stability is WATCH — monitor before promoting")
    else:
        reasons.append("Weight recommendations are STABLE")

    # ── Win rate comparison ────────────────────────────────────────────────────
    wr_delta = comp.get("win_rate_delta")
    if wr_delta is not None:
        if wr_delta < 0:
            blockers.append(
                f"Shadow win rate regresses live by {abs(wr_delta):.1f}pp"
            )
        elif wr_delta >= WIN_RATE_IMPROVEMENT_MIN:
            reasons.append(f"Shadow win rate improves live by {wr_delta:.1f}pp")
        else:
            reasons.append(f"Shadow win rate improvement is marginal ({wr_delta:+.1f}pp)")
    else:
        reasons.append("Win rate comparison unavailable (insufficient outcome data)")

    # ── Return and calibration notes ───────────────────────────────────────────
    ret_delta = comp.get("return_delta")
    if ret_delta is not None:
        direction = "improves" if ret_delta >= 0 else "regresses"
        reasons.append(
            f"Shadow avg return {direction} live by {abs(ret_delta):.2f}%"
        )

    cal_delta = comp.get("calibration_delta")
    if cal_delta is not None:
        if cal_delta < -CALIBRATION_IMPROVEMENT_MIN:
            reasons.append(
                f"Shadow calibration improves (Brier delta {cal_delta:+.4f})"
            )
        elif cal_delta > CALIBRATION_IMPROVEMENT_MIN:
            reasons.append(
                f"Shadow calibration worsens (Brier delta {cal_delta:+.4f})"
            )

    # ── Churn note ─────────────────────────────────────────────────────────────
    churn = comp.get("churn_rate") or 0.0
    if churn > CHURN_EXCESSIVE_THRESHOLD:
        reasons.append(
            f"Alert churn is high ({churn * 100:.1f}%) — "
            "recalibrate weight sensitivity before rollout"
        )

    # ── Determine status ───────────────────────────────────────────────────────
    if blockers:
        status = READINESS_NOT_READY
    elif n < MIN_ROWS_READINESS:
        status = READINESS_OBSERVE
        reasons.append(
            f"Sample size {n} rows — continue observing "
            f"(need ≥ {MIN_ROWS_READINESS})"
        )
    elif (wr_delta is not None
          and wr_delta >= WIN_RATE_IMPROVEMENT_MIN
          and stab_overall == STABILITY_STABLE):
        status = READINESS_STABLE if n >= MIN_ROWS_STABLE else READINESS_LIMITED
    else:
        status = READINESS_OBSERVE

    log.info(
        "shadow_adaptive: rollout_readiness=%s n=%d wr_delta=%s stability=%s",
        status, n, wr_delta, stab_overall,
    )
    if status in (READINESS_LIMITED, READINESS_STABLE):
        log.info(
            "shadow_adaptive: readiness upgraded to %s — review all blockers "
            "before any live changes",
            status,
        )

    return {"status": status, "reasons": reasons, "blockers": blockers}


# ── Report helpers ────────────────────────────────────────────────────────────

def _generate_recommendations(
    comparison: dict,
    stability:  dict,
    readiness:  dict,
) -> list:
    """Build deduplicated actionable recommendations from all sub-analyses."""
    recs: list = []

    status       = readiness.get("status") or READINESS_NOT_READY
    stab_overall = stability.get("overall") or STABILITY_STABLE

    if status == READINESS_NOT_READY:
        recs.append("Continue shadow observation — do not promote adaptive weights yet")

    for b in (readiness.get("blockers") or []):
        recs.append(f"BLOCKER: {b}")

    if stab_overall == STABILITY_UNSTABLE:
        recs.append("Freeze unstable signal weights until oscillation resolves")
    elif stab_overall == STABILITY_WATCH:
        recs.append("Reduce adaptive aggressiveness — weight variance is elevated")

    for sig, info in (stability.get("per_signal") or {}).items():
        if info.get("label") == STABILITY_UNSTABLE:
            recs.append(f"Freeze signal '{sig}' — oscillating weight recommendations")

    wr_delta = comparison.get("win_rate_delta")
    if wr_delta is not None and wr_delta < 0:
        recs.append(
            "Investigate shadow win-rate regression before any live rollout"
        )

    n = comparison.get("n_rows") or 0
    if MIN_ROWS_SHADOW <= n < MIN_ROWS_READINESS:
        recs.append(
            f"Increase sample size before rollout decision "
            f"(current={n}, target={MIN_ROWS_READINESS})"
        )

    churn = comparison.get("churn_rate") or 0.0
    if churn > CHURN_EXCESSIVE_THRESHOLD:
        recs.append(
            f"Alert churn {churn * 100:.1f}% is excessive — "
            "recalibrate weight sensitivity"
        )

    if status in (READINESS_LIMITED, READINESS_STABLE):
        recs.append(
            f"Rollout readiness: {status} — begin controlled A/B test "
            "with ≤10% of alerts under close monitoring"
        )

    return list(dict.fromkeys(recs))[:MAX_RECOMMENDATIONS]


def _compare_vs_previous(
    comparison: dict,
    readiness:  dict,
    previous_report: Optional[dict],
) -> list:
    """Detect significant changes vs a previous shadow report snapshot."""
    if not previous_report:
        return []

    changes:   list = []
    prev_comp  = previous_report.get("comparison")  or {}
    prev_ready = previous_report.get("readiness")   or {}

    # Win rate delta shift
    curr_wr = comparison.get("win_rate_delta")
    prev_wr = prev_comp.get("win_rate_delta")
    if curr_wr is not None and prev_wr is not None:
        delta_of_delta = round(curr_wr - prev_wr, 2)
        if abs(delta_of_delta) >= WIN_RATE_DELTA_CHANGE_MIN:
            changes.append({
                "type":      "WIN_RATE_DELTA_CHANGE",
                "from":      prev_wr,
                "to":        curr_wr,
                "delta":     delta_of_delta,
                "direction": "IMPROVED" if delta_of_delta > 0 else "WORSENED",
            })

    # Readiness status change
    curr_status = readiness.get("status") or READINESS_NOT_READY
    prev_status = prev_ready.get("status") or READINESS_NOT_READY
    if curr_status != prev_status:
        curr_order = READINESS_ORDER.get(curr_status, 0)
        prev_order = READINESS_ORDER.get(prev_status, 0)
        changes.append({
            "type":      "READINESS_CHANGE",
            "from":      prev_status,
            "to":        curr_status,
            "direction": "PROMOTED" if curr_order > prev_order else "DEMOTED",
        })
        if curr_order > prev_order:
            log.info(
                "shadow_adaptive: readiness PROMOTED %s → %s",
                prev_status, curr_status,
            )

    # Stability change
    curr_stab = (previous_report.get("stability") or {}).get("overall")
    # (previous stability is in previous_report["stability"]["overall"])
    # Current stability is passed separately; compare if previous exists
    if curr_stab and curr_stab != (previous_report.get("stability") or {}).get("overall"):
        pass  # will be captured via readiness change or stability warnings

    return changes


def _adaptive_risk_summary(
    comparison: dict,
    stability:  dict,
    readiness:  dict,
) -> str:
    """Deterministic human-readable risk narrative for the shadow report."""
    lines = ["Shadow Adaptive Scoring Engine — Risk Summary"]

    status = readiness.get("status") or READINESS_NOT_READY
    stab   = stability.get("overall") or STABILITY_STABLE
    n      = comparison.get("n_rows") or 0
    wr_d   = comparison.get("win_rate_delta")

    lines.append(
        f"Rollout readiness: {status}  |  Weight stability: {stab}  |  rows={n}"
    )

    if wr_d is not None:
        direction = "improvement" if wr_d >= 0 else "regression"
        lines.append(f"Win rate {direction}: {wr_d:+.1f}pp vs live engine")
    else:
        lines.append("Win rate comparison: insufficient outcome data")

    blockers = readiness.get("blockers") or []
    if blockers:
        lines.append("Blockers: " + "; ".join(blockers[:3]))

    if stab != STABILITY_STABLE:
        n_snap = stability.get("n_snapshots") or 0
        lines.append(
            f"Weight stability concern: {stab} across {n_snap} snapshot(s)"
        )

    return "\n".join(lines)


# ── Full report ───────────────────────────────────────────────────────────────

def generate_shadow_report(
    rows:               Optional[list] = None,
    weight_adjustments: Optional[dict] = None,
    snapshot_history:   Optional[list] = None,
    previous_report:    Optional[dict] = None,
) -> dict:
    """
    Generate a full shadow adaptive scoring report.

    Orchestrates comparison, stability, and readiness analyses then packages
    them into a single structured report dict.  All inputs are optional;
    missing data is handled gracefully.

    Returns
    -------
    {
        "report_type":           "shadow_adaptive_report",
        "n_rows":                int,
        "comparison":            dict,
        "stability":             dict,
        "readiness":             dict,
        "top_improvements":      list (capped at MAX_IMPROVEMENTS),
        "top_regressions":       list (capped at MAX_REGRESSIONS),
        "stability_warnings":    list (capped at MAX_STABILITY_WARNINGS),
        "recommendations":       list (capped at MAX_RECOMMENDATIONS),
        "changes_vs_previous":   list,
        "adaptive_risk_summary": str,
    }
    """
    _rows = rows or []
    _wa   = weight_adjustments or {}

    comparison = compare_live_vs_shadow(_rows, _wa)
    stability  = stability_analysis(snapshot_history)
    readiness  = rollout_readiness(comparison, stability, len(_rows))

    # Per-alert deltas for improvement / regression lists
    replay     = run_shadow_replay(_rows, _wa)
    all_deltas = replay.get("deltas") or []

    outcome_deltas = [d for d in all_deltas if d.get("return_5d") is not None]

    top_improvements = sorted(
        outcome_deltas, key=lambda d: d.get("confidence_delta", 0.0), reverse=True
    )[:MAX_IMPROVEMENTS]

    top_regressions = sorted(
        outcome_deltas, key=lambda d: d.get("confidence_delta", 0.0)
    )[:MAX_REGRESSIONS]

    recs     = _generate_recommendations(comparison, stability, readiness)
    changes  = _compare_vs_previous(comparison, readiness, previous_report)
    summary  = _adaptive_risk_summary(comparison, stability, readiness)

    log.info(
        "shadow_adaptive: report generated — readiness=%s stability=%s n_rows=%d",
        readiness["status"], stability["overall"], len(_rows),
    )

    return {
        "report_type":           "shadow_adaptive_report",
        "n_rows":                len(_rows),
        "comparison":            comparison,
        "stability":             stability,
        "readiness":             readiness,
        "top_improvements":      top_improvements,
        "top_regressions":       top_regressions,
        "stability_warnings":    stability.get("warnings", [])[:MAX_STABILITY_WARNINGS],
        "recommendations":       recs,
        "changes_vs_previous":   changes,
        "adaptive_risk_summary": summary,
    }
