"""
Adaptive weight recommendations for the Predator scanner.
Phase 1F — read-only; does NOT mutate live scoring.

Reads completed predator_outcomes rows, computes lift (win-rate when a
signal is active vs. silent), and produces modest bounded adjustments to
the per-signal maximum-score weights defined in predator.py.

Adjustment pipeline per signal
--------------------------------
1. Extract lift  = active_win_rate − silent_win_rate  (from signal_effectiveness)
2. raw_adj       = lift / 100  ×  MAX_RAW_ADJUSTMENT
3. effective_adj = raw_adj × shrinkage(n_active)      ← regression-to-mean
4. adj           = clamp(effective_adj, ±MAX_ADJUSTMENT)
5. suggested     = clamp(default + adj, min_weight, default + MAX_ADJUSTMENT)

With few active-signal rows the shrinkage factor ≈ 0, so every adjustment
collapses back to 0.0 (fallback to defaults).  With many rows the shrinkage
fades and the lift evidence drives the nudge — but can never exceed
MAX_ADJUSTMENT regardless.

All public functions are pure (accept row lists, no I/O).
The one DB-touching function (_fetch_completed_outcomes import) is isolated
in generate_weight_report() and clearly labelled.
"""
import logging
from typing import Optional

from outcome_analytics import (
    MIN_ROWS_FOR_STATS,
    _fetch_completed_outcomes,
    signal_effectiveness,
)

log = logging.getLogger(__name__)

# ── Default weights — mirror of _SIGNAL_LABELS maxes in predator.py ──────────
DEFAULT_WEIGHTS: dict[str, float] = {
    "options":       3.0,
    "insider":       2.0,
    "short_squeeze": 2.0,
    "catalyst":      2.0,
    "institutional": 1.0,
    "breakout":      2.0,
}

# ── Tuning constants ──────────────────────────────────────────────────────────
MIN_WEIGHT:         float = 0.5   # absolute floor — no signal reduced below this
MAX_RAW_ADJUSTMENT: float = 0.8   # lift/100 × this = raw adj before shrink+clamp
MAX_ADJUSTMENT:     float = 0.5   # hard clamp on any final single-signal adj
SHRINKAGE_K:        float = 20.0  # Bayesian prior: n active rows for 50% trust


# ── Internal helpers ──────────────────────────────────────────────────────────

def _shrink(n: int) -> float:
    """
    Sample-size shrinkage factor in [0, 1).

    0.0 at n=0 (no trust — fall back to default).
    Approaches 1.0 as n → ∞ (full trust in evidence).
    Reaches 0.5 when n == SHRINKAGE_K.
    """
    return n / (n + SHRINKAGE_K)


def _build_reason(
    sig_name:      str,
    n_active:      int,
    lift:          Optional[float],
    avg_return_5d: Optional[float],
    final_adj:     float,
    clamped:       bool,
) -> str:
    """Build a human-readable explanation for an adjustment."""
    if n_active < MIN_ROWS_FOR_STATS or lift is None:
        return f"insufficient data (n_active={n_active}, need {MIN_ROWS_FOR_STATS})"

    direction  = "boost" if final_adj > 0 else ("penalty" if final_adj < 0 else "neutral")
    shrink_pct = round(_shrink(n_active) * 100)

    parts = [
        f"lift={lift:+.1f}%",
        f"n_active={n_active}",
        f"shrinkage={shrink_pct}%",
    ]
    if avg_return_5d is not None:
        parts.append(f"avg_5d={avg_return_5d:+.1f}%")
    if clamped:
        parts.append("clamped")

    return f"{direction} ({', '.join(parts)})"


def _compute_signal_adjustment(sig_name: str, eff_entry: dict) -> dict:
    """
    Compute bounded weight adjustment for one signal.

    Parameters
    ----------
    sig_name  : signal key (must be in DEFAULT_WEIGHTS)
    eff_entry : one signal's value from signal_effectiveness() —
                {"active": {...}, "silent": {...}, "lift": float|None}

    Returns
    -------
    dict with keys:
        default_weight, adjustment, suggested_weight,
        n_active, win_rate_active, lift, avg_return_5d,
        reason, clamped
    """
    default    = DEFAULT_WEIGHTS[sig_name]
    max_weight = default + MAX_ADJUSTMENT
    min_weight = max(MIN_WEIGHT, default - MAX_ADJUSTMENT)

    active_stats    = eff_entry.get("active", {})
    n_active        = active_stats.get("n", 0)
    win_rate_active = active_stats.get("win_rate")
    avg_return_5d   = active_stats.get("avg_return_5d")
    lift            = eff_entry.get("lift")

    # ── Sparse-data fallback ──────────────────────────────────────────────────
    if n_active < MIN_ROWS_FOR_STATS or lift is None:
        log.debug(
            "adaptive_weights: %s — sparse (n_active=%d), holding default %.1f",
            sig_name, n_active, default,
        )
        return {
            "default_weight":   default,
            "adjustment":       0.0,
            "suggested_weight": default,
            "n_active":         n_active,
            "win_rate_active":  win_rate_active,
            "lift":             lift,
            "avg_return_5d":    avg_return_5d,
            "reason":           _build_reason(sig_name, n_active, lift, avg_return_5d, 0.0, False),
            "clamped":          False,
        }

    # ── Adjustment calculation ─────────────────────────────────────────────────
    raw_adj      = (lift / 100.0) * MAX_RAW_ADJUSTMENT          # proportional to lift
    effective_adj = raw_adj * _shrink(n_active)                 # regression-to-mean

    # Hard clamp on adjustment magnitude
    adj         = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, effective_adj))
    adj_clamped = abs(adj - effective_adj) > 1e-9

    # Clamp suggested weight to its valid range
    raw_suggested    = default + adj
    suggested        = max(min_weight, min(max_weight, raw_suggested))
    weight_clamped   = abs(suggested - raw_suggested) > 1e-9
    any_clamped      = adj_clamped or weight_clamped

    if adj_clamped:
        log.warning(
            "adaptive_weights: %s adjustment clamped %.4f → %.4f (MAX_ADJUSTMENT=%.2f)",
            sig_name, effective_adj, adj, MAX_ADJUSTMENT,
        )
    if weight_clamped:
        log.warning(
            "adaptive_weights: %s weight clamped %.4f → %.4f",
            sig_name, raw_suggested, suggested,
        )

    adj       = round(adj, 3)
    suggested = round(suggested, 3)

    log.info(
        "adaptive_weights: %-15s | lift=%+6.1f%% n_active=%3d shrink=%.2f "
        "raw=%.3f eff=%.3f adj=%+.3f suggested=%.3f",
        sig_name, lift, n_active, _shrink(n_active),
        raw_adj, effective_adj, adj, suggested,
    )

    return {
        "default_weight":   default,
        "adjustment":       adj,
        "suggested_weight": suggested,
        "n_active":         n_active,
        "win_rate_active":  win_rate_active,
        "lift":             lift,
        "avg_return_5d":    avg_return_5d,
        "reason":           _build_reason(sig_name, n_active, lift, avg_return_5d, adj, any_clamped),
        "clamped":          any_clamped,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def compute_weight_adjustments(rows: list) -> dict:
    """
    Compute bounded weight recommendations for all signals from outcome rows.

    Accepts pre-fetched rows (plain dicts from predator_outcomes) so the
    function is unit-testable without a live database.

    Returns
    -------
    dict keyed by signal name, each value a dict with:
        default_weight, adjustment, suggested_weight,
        n_active, win_rate_active, lift, avg_return_5d,
        reason, clamped
    """
    effectiveness = signal_effectiveness(rows)
    return {
        sig: _compute_signal_adjustment(sig, effectiveness.get(sig, {}))
        for sig in DEFAULT_WEIGHTS
    }


def generate_weight_report(rows: Optional[list] = None) -> dict:
    """
    Full adaptive weight report: default vs suggested weights + supporting stats.

    If rows is None, fetches COMPLETE outcomes from the database.
    Pass a list of row dicts to use pre-fetched data (e.g., in tests).

    Returns
    -------
    {
      "row_count": int,
      "adjustments": {
          signal_name: {
              default_weight, adjustment, suggested_weight,
              n_active, win_rate_active, lift, avg_return_5d,
              reason, clamped,
          }, ...
      },
      "summary": {
          "signals_boosted":          [ {signal, adjustment, suggested_weight, reason}, ... ],
          "signals_penalized":        [ {signal, adjustment, suggested_weight, reason}, ... ],
          "signals_held":             [ {signal, adjustment, suggested_weight, reason}, ... ],
          "total_default_weight":     float,
          "total_suggested_weight":   float,
      },
    }
    """
    if rows is None:
        rows = _fetch_completed_outcomes()

    n = len(rows)
    log.info("adaptive_weights: generating report on %d completed outcomes", n)

    if n < MIN_ROWS_FOR_STATS:
        log.warning(
            "adaptive_weights: only %d row(s) — all adjustments revert to defaults "
            "(need >= %d rows for any signal to adapt)",
            n, MIN_ROWS_FOR_STATS,
        )

    adjustments = compute_weight_adjustments(rows)

    boosted   = []
    penalized = []
    held      = []

    for sig, entry in adjustments.items():
        item = {
            "signal":           sig,
            "adjustment":       entry["adjustment"],
            "suggested_weight": entry["suggested_weight"],
            "reason":           entry["reason"],
        }
        if entry["adjustment"] > 0.0:
            boosted.append(item)
        elif entry["adjustment"] < 0.0:
            penalized.append(item)
        else:
            held.append(item)

    total_default   = round(sum(e["default_weight"]   for e in adjustments.values()), 3)
    total_suggested = round(sum(e["suggested_weight"] for e in adjustments.values()), 3)

    log.info(
        "adaptive_weights: report complete — boosted=%d penalized=%d held=%d "
        "total_default=%.2f total_suggested=%.2f",
        len(boosted), len(penalized), len(held),
        total_default, total_suggested,
    )

    return {
        "row_count":   n,
        "adjustments": adjustments,
        "summary": {
            "signals_boosted":        boosted,
            "signals_penalized":      penalized,
            "signals_held":           held,
            "total_default_weight":   total_default,
            "total_suggested_weight": total_suggested,
        },
    }
