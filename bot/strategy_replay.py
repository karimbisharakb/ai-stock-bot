"""
Strategy replay and counterfactual analysis for the Predator scanner.
Phase 3B — read-only; does NOT modify live scoring logic.

Each completed-outcome row represents an alert that fired.  The replay
module asks: "Which historical alerts would have fired under different
rules, and how would the performance stats have looked?"

Because only alerted rows exist in predator_outcomes, the module can only
FILTER (exclude some rows from a baseline set) — it cannot ADD hypothetical
rows that were never alerted.  This is correct retrospective analysis.

Replay mechanics
----------------
1. Compute `replayed_confidence` for each row (may differ from original
   if regime_penalties or signal_confidence_deltas are specified).
2. Apply filter gates: suppress_regimes, min_confidence, require_signals,
   exclude_signals, min_active_signals.
3. Return the passing subset with confidence_pct set to replayed_confidence
   so downstream stats helpers compute calibration correctly.

Counterfactual presets (see PRESET_SPECS)
-----------------------------------------
  BASELINE              — all rows, original confidence
  STRICT_CONFIDENCE_65  — require confidence ≥ 65%
  STRICT_CONFIDENCE_70  — require confidence ≥ 70%
  NO_RISK_OFF           — exclude RISK_OFF regime rows entirely
  CONVICTION_ONLY       — require ≥ 3 active signals AND confidence ≥ 55%
  TIGHTER_NEUTRAL       — NEUTRAL regime penalty ×0.80 (stricter than default ×0.90)
  LOOSER_RISK_OFF       — RISK_OFF regime penalty ×0.90 (looser than default ×0.75)
"""
import json
import logging
from typing import NamedTuple, Optional

from confidence_validation import calibration_error
from outcome_analytics import (
    MIN_ROWS_FOR_STATS,
    _avg,
    _fetch_completed_outcomes,
    _win_rate,
)

log = logging.getLogger(__name__)

# ── Baseline scoring parameters (mirrors predator.py / market_regime.py) ─────
BASELINE_CONVICTION_THRESHOLD: float = 55.0   # CONVICTION_MIN_CONFIDENCE
BASELINE_CONVICTION_MIN_SIGS:  int   = 3      # CONVICTION_MIN_SIGNALS
DEFAULT_REGIME_PENALTIES: dict = {
    "BULL":     1.00,
    "NEUTRAL":  0.90,
    "RISK_OFF": 0.75,
}

# ── Signal universe ───────────────────────────────────────────────────────────
SIGNAL_NAMES: tuple = (
    "options", "insider", "short_squeeze", "catalyst", "institutional", "breakout"
)

# ── Sweep ranges ──────────────────────────────────────────────────────────────
CONFIDENCE_SWEEP: tuple = (40.0, 45.0, 50.0, 55.0, 60.0, 65.0, 70.0, 75.0, 80.0)
NEUTRAL_PENALTY_SWEEP:   tuple = (0.70, 0.80, 0.90, 1.00)
RISK_OFF_PENALTY_SWEEP:  tuple = (0.50, 0.60, 0.75, 0.90, 1.00)
SIGNAL_WEIGHT_DELTAS:    tuple = (-15.0, -10.0, -5.0, 0.0, 5.0, 10.0, 15.0)

# ── Robustness thresholds ─────────────────────────────────────────────────────
MIN_RELIABLE_SAMPLE:               int   = 10    # n < this → INSUFFICIENT_SAMPLE
FRAGILE_WIN_IMPROVEMENT_THRESHOLD: float = 5.0   # pp improvement on small sample
OVERFIT_ALERT_REDUCTION_THRESHOLD: float = 50.0  # % reduction → OVERFIT_REDUCTION
FRAGILE_SAMPLE_RATIO:              float = 0.30  # n < ratio × baseline_n → fragile


# ── ReplaySpec ────────────────────────────────────────────────────────────────

class ReplaySpec(NamedTuple):
    """
    Specification for a single replay run.

    All fields are optional; the defaults reproduce the baseline (no filtering,
    no confidence changes).

    Fields
    ------
    name                     : human-readable label for this spec
    description              : longer explanation (optional)
    min_confidence           : exclude rows where replayed_confidence < this
    suppress_regimes         : exclude rows whose regime is in this tuple
    regime_penalties         : {regime: factor} to re-apply instead of defaults;
                               None = use DEFAULT_REGIME_PENALTIES unchanged
    require_signals          : all named signals must be active (score > 0)
    exclude_signals          : any row where at least one of these is active
                               is excluded
    min_active_signals       : require at least this many active signals
    signal_confidence_deltas : {signal: pp_delta} — add pp_delta to confidence
                               for each row where that signal is active;
                               None = no adjustment
    """
    name:                     str   = "baseline"
    description:              str   = ""
    min_confidence:           float = 0.0
    suppress_regimes:         tuple = ()
    regime_penalties:         Optional[dict] = None
    require_signals:          tuple = ()
    exclude_signals:          tuple = ()
    min_active_signals:       int   = 0
    signal_confidence_deltas: Optional[dict] = None


# ── Pre-built spec presets ────────────────────────────────────────────────────

BASELINE_SPEC = ReplaySpec(
    name        = "baseline",
    description = "All completed alerts, original confidence — no extra filtering",
)

STRICT_CONFIDENCE_65_SPEC = ReplaySpec(
    name           = "strict_confidence_65",
    description    = "Require confidence ≥ 65% (vs default 55%)",
    min_confidence = 65.0,
)

STRICT_CONFIDENCE_70_SPEC = ReplaySpec(
    name           = "strict_confidence_70",
    description    = "Require confidence ≥ 70%",
    min_confidence = 70.0,
)

NO_RISK_OFF_SPEC = ReplaySpec(
    name             = "no_risk_off",
    description      = "Exclude RISK_OFF regime rows entirely",
    suppress_regimes = ("RISK_OFF",),
)

CONVICTION_ONLY_SPEC = ReplaySpec(
    name               = "conviction_only",
    description        = "CONVICTION-quality: ≥ 3 signals AND confidence ≥ 55%",
    min_confidence     = BASELINE_CONVICTION_THRESHOLD,
    min_active_signals = BASELINE_CONVICTION_MIN_SIGS,
)

TIGHTER_NEUTRAL_SPEC = ReplaySpec(
    name             = "tighter_neutral_penalty",
    description      = "NEUTRAL regime penalty ×0.80 (stricter than default ×0.90)",
    regime_penalties = {"BULL": 1.00, "NEUTRAL": 0.80, "RISK_OFF": 0.75},
)

LOOSER_RISK_OFF_SPEC = ReplaySpec(
    name             = "looser_risk_off_penalty",
    description      = "RISK_OFF regime penalty ×0.90 (looser than default ×0.75)",
    regime_penalties = {"BULL": 1.00, "NEUTRAL": 0.90, "RISK_OFF": 0.90},
)

PRESET_SPECS: tuple = (
    BASELINE_SPEC,
    STRICT_CONFIDENCE_65_SPEC,
    STRICT_CONFIDENCE_70_SPEC,
    NO_RISK_OFF_SPEC,
    CONVICTION_ONLY_SPEC,
    TIGHTER_NEUTRAL_SPEC,
    LOOSER_RISK_OFF_SPEC,
)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _sig_scores(row: dict) -> dict:
    """
    Parse signal_summary JSON and return {signal_name: score} for all active
    signals.  Returns an empty dict on any parse failure.
    """
    try:
        raw = json.loads(row.get("signal_summary") or "{}")
        return {k: int(v or 0) for k, v in raw.items() if (v or 0) > 0}
    except (json.JSONDecodeError, TypeError, AttributeError, ValueError):
        return {}


def _replay_confidence(row: dict, spec: ReplaySpec) -> float:
    """
    Compute the confidence that would apply to this row under `spec`.

    Steps (applied in order):
    1. Start with original confidence_pct.
    2. If spec.regime_penalties differs from defaults for this row's regime,
       un-apply the original factor and re-apply the new one.
    3. Add any per-signal confidence deltas for active signals.
    4. Clamp to [0.0, 100.0].
    """
    conf   = float(row.get("confidence_pct") or 0.0)
    regime = str(row.get("regime") or "BULL")

    # ── Step 2: regime penalty re-application ─────────────────────────────────
    if spec.regime_penalties is not None:
        orig_factor = DEFAULT_REGIME_PENALTIES.get(regime, 1.0)
        new_factor  = spec.regime_penalties.get(regime, orig_factor)
        if new_factor != orig_factor and orig_factor > 0:
            conf = round(conf / orig_factor * new_factor, 4)

    # ── Step 3: signal-level confidence adjustments ───────────────────────────
    if spec.signal_confidence_deltas:
        active = _sig_scores(row)
        for sig, delta in spec.signal_confidence_deltas.items():
            if active.get(sig, 0) > 0:
                conf += float(delta)

    return round(max(0.0, min(100.0, conf)), 4)


def _row_passes(row: dict, spec: ReplaySpec, replayed_conf: float) -> bool:
    """Return True if this row should be included under spec after re-filtering."""
    # Regime suppression
    if spec.suppress_regimes:
        if str(row.get("regime") or "") in spec.suppress_regimes:
            return False

    # Confidence threshold
    if replayed_conf < spec.min_confidence:
        return False

    # Signal requirements
    if spec.require_signals or spec.exclude_signals or spec.min_active_signals:
        active_names = frozenset(_sig_scores(row).keys())

        if spec.require_signals:
            if not all(s in active_names for s in spec.require_signals):
                return False

        if spec.exclude_signals:
            if any(s in active_names for s in spec.exclude_signals):
                return False

        if spec.min_active_signals > 0 and len(active_names) < spec.min_active_signals:
            return False

    return True


def _replay_stats(included: list, total_rows: list) -> dict:
    """
    Compute performance statistics for a replay-filtered subset.

    Returns a dict with standard metrics plus alert_reduction_pct.
    """
    n     = len(included)
    total = len(total_rows)
    alert_reduction = round((1.0 - n / total) * 100.0, 1) if total > 0 else 0.0

    r5d   = [r.get("return_5d")        for r in included]
    r20d  = [r.get("return_20d")       for r in included]
    dd    = [r.get("max_drawdown_pct") for r in included]
    confs = [r.get("confidence_pct")   for r in included]

    avg_r5d = _avg(r5d)
    avg_dd  = _avg(dd)
    risk_adj = (
        round(avg_r5d - 0.5 * abs(avg_dd), 4)
        if avg_r5d is not None and avg_dd is not None
        else None
    )

    return {
        "n":                  n,
        "total":              total,
        "alert_reduction_pct": alert_reduction,
        "win_rate":           _win_rate(included),
        "avg_return_5d":      avg_r5d,
        "avg_return_20d":     _avg(r20d),
        "avg_max_dd":         avg_dd,
        "avg_confidence":     _avg(confs),
        "calibration_error":  calibration_error(included),
        "risk_adj_score":     risk_adj,
    }


# ── Public replay API ─────────────────────────────────────────────────────────

def apply_replay(rows: list, spec: ReplaySpec) -> list:
    """
    Filter `rows` according to `spec` and return the passing subset.

    Each returned row is a copy with `confidence_pct` set to the replayed
    value so downstream stats helpers compute calibration correctly.
    """
    result = []
    for row in rows:
        replayed_conf = _replay_confidence(row, spec)
        if _row_passes(row, spec, replayed_conf):
            row_out = dict(row)
            row_out["confidence_pct"] = replayed_conf
            result.append(row_out)
    log.debug(
        "strategy_replay: apply_replay '%s' — %d/%d rows pass",
        spec.name, len(result), len(rows),
    )
    return result


def replay_stats(rows: list, spec: ReplaySpec) -> dict:
    """
    Apply spec to rows and return performance statistics for the filtered subset.

    Returns dict from _replay_stats plus the spec name.
    """
    included = apply_replay(rows, spec)
    stats = _replay_stats(included, rows)
    stats["name"] = spec.name
    return stats


# ── Counterfactual comparison ─────────────────────────────────────────────────

def compare_replay(
    rows:          list,
    baseline_spec: ReplaySpec,
    alt_spec:      ReplaySpec,
) -> dict:
    """
    Compare baseline vs alternative replay configuration.

    Returns
    -------
    {
        "baseline":       stats_dict,
        "alternative":    stats_dict,
        "deltas":         {metric: delta_value | None},
        "recommendation": str,
    }
    """
    base_rows = apply_replay(rows, baseline_spec)
    alt_rows  = apply_replay(rows, alt_spec)

    base_stats = _replay_stats(base_rows, rows)
    alt_stats  = _replay_stats(alt_rows,  rows)
    alt_stats["name"] = alt_spec.name

    # Compute deltas (alt − baseline)
    delta_metrics = (
        "win_rate", "avg_return_5d", "avg_return_20d",
        "avg_max_dd", "avg_confidence", "risk_adj_score",
    )
    deltas = {}
    for m in delta_metrics:
        b = base_stats.get(m)
        a = alt_stats.get(m)
        deltas[m] = round(a - b, 4) if (b is not None and a is not None) else None

    rec = generate_recommendation(base_stats, alt_stats, alt_spec)

    log.info(
        "strategy_replay: compare '%s' vs '%s' — n_base=%d n_alt=%d",
        baseline_spec.name, alt_spec.name, len(base_rows), len(alt_rows),
    )

    return {
        "baseline":       base_stats,
        "alternative":    alt_stats,
        "deltas":         deltas,
        "recommendation": rec,
    }


# ── Sensitivity sweeps ────────────────────────────────────────────────────────

def confidence_threshold_sweep(
    rows:       list,
    thresholds: tuple = CONFIDENCE_SWEEP,
) -> list:
    """
    Compute replay stats at each confidence threshold.

    Returns a list of stats dicts (one per threshold), ascending by threshold.
    Each entry includes a ``threshold`` field.
    """
    results = []
    for thresh in thresholds:
        spec = ReplaySpec(
            name           = f"conf_threshold_{thresh:.0f}",
            min_confidence = thresh,
        )
        included = apply_replay(rows, spec)
        stats    = _replay_stats(included, rows)
        stats["threshold"] = thresh
        stats["name"]      = spec.name
        results.append(stats)
    log.debug(
        "strategy_replay: confidence_threshold_sweep — %d steps",
        len(results),
    )
    return results


def penalty_sweep(
    rows:              list,
    neutral_factors:   tuple = NEUTRAL_PENALTY_SWEEP,
    risk_off_factors:  tuple = RISK_OFF_PENALTY_SWEEP,
) -> list:
    """
    Sweep over alternative regime penalty factors.

    NEUTRAL and RISK_OFF factors are swept independently (other held at default).
    Each spec also applies min_confidence = BASELINE_CONVICTION_THRESHOLD so
    that rows dropping below the CONVICTION bar after re-penalisation are excluded.

    Returns a flat list of stats dicts with ``neutral_factor`` and
    ``risk_off_factor`` fields.
    """
    results = []

    # Sweep NEUTRAL (hold RISK_OFF at default)
    for nf in neutral_factors:
        spec = ReplaySpec(
            name             = f"neutral_{nf:.2f}",
            min_confidence   = BASELINE_CONVICTION_THRESHOLD,
            regime_penalties = {
                "BULL":     1.00,
                "NEUTRAL":  nf,
                "RISK_OFF": DEFAULT_REGIME_PENALTIES["RISK_OFF"],
            },
        )
        included = apply_replay(rows, spec)
        stats    = _replay_stats(included, rows)
        stats.update({
            "name":            spec.name,
            "neutral_factor":  nf,
            "risk_off_factor": DEFAULT_REGIME_PENALTIES["RISK_OFF"],
        })
        results.append(stats)

    # Sweep RISK_OFF (hold NEUTRAL at default)
    for rf in risk_off_factors:
        spec = ReplaySpec(
            name             = f"risk_off_{rf:.2f}",
            min_confidence   = BASELINE_CONVICTION_THRESHOLD,
            regime_penalties = {
                "BULL":     1.00,
                "NEUTRAL":  DEFAULT_REGIME_PENALTIES["NEUTRAL"],
                "RISK_OFF": rf,
            },
        )
        included = apply_replay(rows, spec)
        stats    = _replay_stats(included, rows)
        stats.update({
            "name":            spec.name,
            "neutral_factor":  DEFAULT_REGIME_PENALTIES["NEUTRAL"],
            "risk_off_factor": rf,
        })
        results.append(stats)

    log.debug(
        "strategy_replay: penalty_sweep — %d neutral + %d risk_off steps",
        len(neutral_factors), len(risk_off_factors),
    )
    return results


def weight_perturbation_analysis(
    rows:    list,
    signal:  str,
    deltas:  tuple = SIGNAL_WEIGHT_DELTAS,
) -> list:
    """
    Test how adding confidence_pp to rows where `signal` is active changes
    the outcome stats.

    A delta of +5 means: "if we had trusted this signal 5 confidence-points
    more, which rows would qualify and how would they perform?"

    Returns a list of stats dicts with ``signal`` and ``delta`` fields.
    """
    results = []
    for delta in deltas:
        spec = ReplaySpec(
            name                     = f"{signal}_delta_{delta:+.0f}pp",
            signal_confidence_deltas = {signal: delta},
        )
        included = apply_replay(rows, spec)
        stats    = _replay_stats(included, rows)
        stats.update({"name": spec.name, "signal": signal, "delta": delta})
        results.append(stats)
    log.debug(
        "strategy_replay: weight_perturbation_analysis '%s' — %d deltas",
        signal, len(deltas),
    )
    return results


# ── Robustness analysis ───────────────────────────────────────────────────────

def robustness_analysis(
    baseline_stats: dict,
    results:        list,
) -> list:
    """
    Detect fragile improvements, small-sample fake alpha, and overfitting.

    Each entry in `results` should be a stats dict (from replay_stats or a
    sweep) containing ``n``, ``win_rate``, ``alert_reduction_pct``, and an
    optional ``name`` key.

    Warning types
    -------------
    INSUFFICIENT_SAMPLE     n < MIN_RELIABLE_SAMPLE — improvements unreliable
    FRAGILE_IMPROVEMENT     win_rate improved > FRAGILE_WIN_IMPROVEMENT_THRESHOLD
                            on n < FRAGILE_SAMPLE_RATIO × baseline_n
    OVERFIT_ALERT_REDUCTION alert_reduction_pct > OVERFIT_ALERT_REDUCTION_THRESHOLD

    Returns a list of warning dicts sorted: HIGH severity first, then type.
    """
    b_wr = baseline_stats.get("win_rate") or 0.0
    b_n  = max(baseline_stats.get("n") or 1, 1)

    warnings = []

    for r in results:
        n           = r.get("n") or 0
        wr          = r.get("win_rate")
        reduction   = r.get("alert_reduction_pct") or 0.0
        name        = r.get("name") or "?"

        if n < MIN_RELIABLE_SAMPLE:
            warnings.append({
                "type":     "INSUFFICIENT_SAMPLE",
                "config":   name,
                "n":        n,
                "severity": "HIGH",
                "detail":   (
                    f"'{name}' has only {n} alert(s) — "
                    f"win rate estimates are unreliable (need ≥ {MIN_RELIABLE_SAMPLE})"
                ),
            })
            log.warning(
                "strategy_replay: INSUFFICIENT_SAMPLE '%s' n=%d", name, n,
            )

        if (wr is not None and b_wr is not None
                and wr - b_wr > FRAGILE_WIN_IMPROVEMENT_THRESHOLD
                and n < b_n * FRAGILE_SAMPLE_RATIO):
            warnings.append({
                "type":            "FRAGILE_IMPROVEMENT",
                "config":          name,
                "n":               n,
                "baseline_n":      b_n,
                "win_rate_delta":  round(wr - b_wr, 2),
                "severity":        "HIGH",
                "detail": (
                    f"'{name}' shows +{wr - b_wr:.1f}pp win rate improvement "
                    f"but only {n}/{b_n} alerts — may be cherry-picking"
                ),
            })
            log.warning(
                "strategy_replay: FRAGILE_IMPROVEMENT '%s' delta=+%.1fpp n=%d",
                name, wr - b_wr, n,
            )

        if reduction > OVERFIT_ALERT_REDUCTION_THRESHOLD:
            warnings.append({
                "type":            "OVERFIT_ALERT_REDUCTION",
                "config":          name,
                "alert_reduction": reduction,
                "n":               n,
                "severity":        "MEDIUM",
                "detail": (
                    f"'{name}' excluded {reduction:.0f}% of alerts — "
                    "extreme filtering may be over-optimised"
                ),
            })

    warnings.sort(key=lambda w: (0 if w["severity"] == "HIGH" else 1, w["type"], w.get("config", "")))
    return warnings


# ── Recommendation generation ─────────────────────────────────────────────────

def generate_recommendation(
    baseline_stats: dict,
    alt_stats:      dict,
    spec:           ReplaySpec,
) -> str:
    """
    Generate a one-sentence recommendation comparing baseline vs alternative.

    Deterministic: same inputs always produce the same string.
    """
    b_wr      = baseline_stats.get("win_rate")
    a_wr      = alt_stats.get("win_rate")
    a_n       = alt_stats.get("n") or 0
    reduction = alt_stats.get("alert_reduction_pct") or 0.0

    if b_wr is None or a_wr is None:
        return (
            f"'{spec.name}': insufficient data for win-rate comparison "
            f"(baseline n={baseline_stats.get('n', 0)}, alt n={a_n})."
        )

    delta     = round(a_wr - b_wr, 1)
    direction = "improved" if delta > 0 else ("unchanged" if delta == 0 else "worsened")

    return (
        f"'{spec.name}': win rate {direction} {delta:+.1f}pp "
        f"({a_wr:.1f}% vs {b_wr:.1f}% baseline) "
        f"with {reduction:.0f}% fewer alerts ({a_n} alerts). "
        f"{spec.description}".strip()
    )


# ── Sensitivity summary ───────────────────────────────────────────────────────

def sensitivity_summary(sweep_results: list) -> dict:
    """
    Summarise the range and trend across a sweep (e.g., confidence_threshold_sweep).

    Returns
    -------
    {
        "n_configs":      int,
        "min_win_rate":   float | None,
        "max_win_rate":   float | None,
        "win_rate_range": float | None,
        "best_config":    stats_dict | None,
        "worst_config":   stats_dict | None,
        "monotone_trend": bool | None,   # True if win rate moves monotonically
    }
    """
    if not sweep_results:
        return {
            "n_configs": 0, "min_win_rate": None, "max_win_rate": None,
            "win_rate_range": None, "best_config": None, "worst_config": None,
            "monotone_trend": None,
        }

    valid = [r for r in sweep_results if r.get("win_rate") is not None]

    if not valid:
        return {
            "n_configs": len(sweep_results), "min_win_rate": None,
            "max_win_rate": None, "win_rate_range": None,
            "best_config": None, "worst_config": None, "monotone_trend": None,
        }

    wrs   = [r["win_rate"] for r in valid]
    best  = max(valid,  key=lambda r: r["win_rate"])
    worst = min(valid,  key=lambda r: r["win_rate"])
    mn    = round(min(wrs), 2)
    mx    = round(max(wrs), 2)

    # Monotone trend: win rates non-decreasing or non-increasing
    if len(wrs) >= 2:
        non_dec = all(wrs[i] <= wrs[i + 1] for i in range(len(wrs) - 1))
        non_inc = all(wrs[i] >= wrs[i + 1] for i in range(len(wrs) - 1))
        monotone = non_dec or non_inc
    else:
        monotone = None

    return {
        "n_configs":      len(sweep_results),
        "min_win_rate":   mn,
        "max_win_rate":   mx,
        "win_rate_range": round(mx - mn, 2),
        "best_config":    best,
        "worst_config":   worst,
        "monotone_trend": monotone,
    }


# ── Report builder ────────────────────────────────────────────────────────────

def _build_warnings(
    robustness_warnings: list,
    counterfactuals:     dict,
) -> list:
    """Collect top-level human-readable warning strings."""
    warnings = []

    for w in robustness_warnings:
        if w.get("severity") == "HIGH":
            warnings.append(f"[HIGH] {w['type']}: {w['detail']}")

    for name, cf in counterfactuals.items():
        delta_wr = (cf.get("deltas") or {}).get("win_rate")
        if delta_wr is not None and abs(delta_wr) >= 5.0:
            direction = "improved" if delta_wr > 0 else "worsened"
            warnings.append(
                f"Counterfactual '{name}' {direction} win rate by {delta_wr:+.1f}pp"
            )

    return warnings


def generate_report(rows: Optional[list] = None) -> dict:
    """
    Full strategy replay and counterfactual analysis report.

    If rows is None, fetches all COMPLETE outcomes from the DB.
    Pass a pre-fetched list to avoid DB access (e.g. in tests).

    Returns
    -------
    {
        "row_count":           int,
        "baseline":            stats_dict,
        "counterfactuals": {
            "strict_65":       compare_replay_result,
            "strict_70":       compare_replay_result,
            "no_risk_off":     compare_replay_result,
            "conviction_only": compare_replay_result,
            "tighter_neutral": compare_replay_result,
            "looser_risk_off": compare_replay_result,
        },
        "confidence_sweep":    [ stats_dict, ... ],
        "sensitivity":         sensitivity_summary_dict,
        "best_config":         stats_dict | None,
        "worst_config":        stats_dict | None,
        "robustness_warnings": [ warning_dict, ... ],
        "recommendations":     [ str, ... ],
        "warnings":            [ str, ... ],
    }
    """
    if rows is None:
        rows = _fetch_completed_outcomes()

    n = len(rows)
    log.info("strategy_replay: generating report on %d completed outcomes", n)

    if n < MIN_ROWS_FOR_STATS:
        log.warning(
            "strategy_replay: only %d row(s) — most stats will be sparse "
            "(need >= %d rows for win rate)",
            n, MIN_ROWS_FOR_STATS,
        )

    # Baseline (all rows, no additional filtering)
    baseline_rows  = apply_replay(rows, BASELINE_SPEC)
    baseline_stats = _replay_stats(baseline_rows, rows)
    baseline_stats["name"] = BASELINE_SPEC.name

    # Counterfactual comparisons
    counterfactuals = {
        "strict_65":       compare_replay(rows, BASELINE_SPEC, STRICT_CONFIDENCE_65_SPEC),
        "strict_70":       compare_replay(rows, BASELINE_SPEC, STRICT_CONFIDENCE_70_SPEC),
        "no_risk_off":     compare_replay(rows, BASELINE_SPEC, NO_RISK_OFF_SPEC),
        "conviction_only": compare_replay(rows, BASELINE_SPEC, CONVICTION_ONLY_SPEC),
        "tighter_neutral": compare_replay(rows, BASELINE_SPEC, TIGHTER_NEUTRAL_SPEC),
        "looser_risk_off": compare_replay(rows, BASELINE_SPEC, LOOSER_RISK_OFF_SPEC),
    }

    # Confidence sweep and sensitivity
    conf_sweep = confidence_threshold_sweep(rows)
    sensitivity = sensitivity_summary(conf_sweep)

    best_config  = sensitivity.get("best_config")
    worst_config = sensitivity.get("worst_config")

    # Robustness analysis on sweep results
    robustness = robustness_analysis(baseline_stats, conf_sweep)

    # Recommendations from counterfactuals
    recommendations = [
        cf["recommendation"]
        for cf in counterfactuals.values()
        if cf.get("recommendation")
    ]

    warnings = _build_warnings(robustness, counterfactuals)

    log.info(
        "strategy_replay: report done — baseline_n=%d counterfactuals=%d "
        "sweep=%d robustness=%d warnings=%d",
        baseline_stats.get("n", 0), len(counterfactuals),
        len(conf_sweep), len(robustness), len(warnings),
    )

    return {
        "row_count":           n,
        "baseline":            baseline_stats,
        "counterfactuals":     counterfactuals,
        "confidence_sweep":    conf_sweep,
        "sensitivity":         sensitivity,
        "best_config":         best_config,
        "worst_config":        worst_config,
        "robustness_warnings": robustness,
        "recommendations":     recommendations,
        "warnings":            warnings,
    }
