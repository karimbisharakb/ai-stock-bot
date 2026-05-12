"""
Signal-combination analytics for the Predator scanner.
Phase 2C — read-only; does NOT modify live scoring.

Analyses how PAIRS and TRIPLES of signals perform together using SUBSET
matching: a row contributes to the "options+insider" pair if BOTH signals
are active, regardless of what other signals also fired.  This differs from
signal_combo_ranking() in outcome_analytics, which uses exact active-set
matching.

Pair count  : C(6,2) = 15
Triple count: C(6,3) = 20
Total       : 35 combos — inherently bounded; no explosion risk.

Ranking dimensions
------------------
    best/worst by win_rate
    best/worst by risk-adjusted score  (avg_return_5d − 0.5 × |avg_max_dd|)
    most stable   (smallest win-rate spread across regimes)
    most dangerous (most negative avg_max_dd)

Deceptive-combo types detected
------------------------------
    HIGH_CONF_POOR_RETURN  — avg_confidence ≥ threshold, avg_return_5d < 0
    LOTTERY_TICKET         — low win rate with large occasional gains
    HIGH_DRAWDOWN          — severe avg drawdown
"""
import logging
from itertools import combinations
from typing import Optional

from market_regime import BULL, NEUTRAL, RISK_OFF
from outcome_analytics import (
    MIN_ROWS_FOR_STATS,
    _SIGNAL_NAMES,
    _active_signals,
    _avg,
    _fetch_completed_outcomes,
    _win_rate,
)

log = logging.getLogger(__name__)

# ── Signal universe ───────────────────────────────────────────────────────────
ALL_SIGNALS: tuple = _SIGNAL_NAMES   # canonical order from outcome_analytics

# ── Consistency labels ────────────────────────────────────────────────────────
COMBO_STABLE   = "STABLE"    # win-rate spread ≤ CONSISTENCY_STABLE_THRESHOLD
COMBO_MIXED    = "MIXED"     # spread ≤ CONSISTENCY_UNSTABLE_THRESHOLD
COMBO_UNSTABLE = "UNSTABLE"  # spread > CONSISTENCY_UNSTABLE_THRESHOLD
COMBO_UNKNOWN  = "UNKNOWN"   # fewer than 2 regimes have enough data

# ── Thresholds ────────────────────────────────────────────────────────────────
MIN_COMBO_ROWS: int = MIN_ROWS_FOR_STATS  # minimum rows per combo for valid stats

# Regime win-rate spread classification
CONSISTENCY_STABLE_THRESHOLD:   float = 15.0  # ≤ this → STABLE
CONSISTENCY_UNSTABLE_THRESHOLD: float = 30.0  # > this → UNSTABLE (else MIXED)

# Risk-adjusted score parameters
RISK_ADJ_DD_WEIGHT: float = 0.5  # how much drawdown penalises the score

# Deceptive combo detection
DECEPTIVE_HIGH_CONF_THRESHOLD: float =  65.0  # avg_confidence ≥ this
DECEPTIVE_POOR_RETURN:         float =   0.0  # avg_return_5d < this → HIGH_CONF_POOR_RETURN
DECEPTIVE_WIN_RATE_LOW:        float =  40.0  # win_rate < this for LOTTERY_TICKET
DECEPTIVE_LOTTERY_GAIN:        float =   8.0  # avg_max_gain ≥ this for LOTTERY_TICKET
DECEPTIVE_DRAWDOWN_THRESHOLD:  float =  -8.0  # avg_max_dd < this → HIGH_DRAWDOWN


# ── Internal helpers ──────────────────────────────────────────────────────────

def _combo_key(combo) -> str:
    """Deterministic string key: '+'.join(sorted signal names)."""
    return "+".join(sorted(combo))


def _risk_adj(avg_return_5d: Optional[float], avg_max_dd: Optional[float]) -> Optional[float]:
    """
    Risk-adjusted score: avg_return_5d − RISK_ADJ_DD_WEIGHT × |avg_max_dd|

    Positive = returns exceed the drawdown-weighted penalty (acceptable risk).
    None when avg_return_5d is unknown.
    """
    if avg_return_5d is None:
        return None
    dd_penalty = abs(avg_max_dd) * RISK_ADJ_DD_WEIGHT if avg_max_dd is not None else 0.0
    return round(avg_return_5d - dd_penalty, 4)


def _extended_stats(rows: list) -> dict:
    """Standard bucket stats extended with avg_confidence and risk_adj_score."""
    r5d  = [r.get("return_5d")        for r in rows]
    r20d = [r.get("return_20d")       for r in rows]
    gain = [r.get("max_gain_pct")     for r in rows]
    dd   = [r.get("max_drawdown_pct") for r in rows]
    conf = [r.get("confidence_pct")   for r in rows]

    avg_r5d = _avg(r5d)
    avg_dd  = _avg(dd)

    return {
        "n":              len(rows),
        "win_rate":       _win_rate(rows),
        "avg_return_5d":  avg_r5d,
        "avg_return_20d": _avg(r20d),
        "avg_max_gain":   _avg(gain),
        "avg_max_dd":     avg_dd,
        "avg_confidence": _avg(conf),
        "risk_adj_score": _risk_adj(avg_r5d, avg_dd),
    }


def _consistency_label(spread: Optional[float]) -> str:
    if spread is None:
        return COMBO_UNKNOWN
    if spread <= CONSISTENCY_STABLE_THRESHOLD:
        return COMBO_STABLE
    if spread <= CONSISTENCY_UNSTABLE_THRESHOLD:
        return COMBO_MIXED
    return COMBO_UNSTABLE


def _compute_combo(rows: list, combo: frozenset) -> dict:
    """
    Compute all statistics for a signal combination using subset matching.

    A row matches the combo if every signal in the combo is in the row's
    active-signal set.  Stats are computed both overall and per-regime.

    Returns a dict suitable for inclusion in the combo_stats output.
    """
    key     = _combo_key(combo)
    signals = sorted(combo)
    matching = [r for r in rows if combo.issubset(_active_signals(r))]

    overall = _extended_stats(matching)

    # Per-regime breakdown (only the three known regimes)
    by_regime: dict = {}
    for regime in (BULL, NEUTRAL, RISK_OFF):
        regime_rows = [r for r in matching if r.get("regime") == regime]
        by_regime[regime] = _extended_stats(regime_rows)

    # Regime consistency — based on win-rate spread across regimes with valid data
    valid_wrs = [
        by_regime[r]["win_rate"]
        for r in (BULL, NEUTRAL, RISK_OFF)
        if by_regime[r]["win_rate"] is not None
    ]
    if len(valid_wrs) >= 2:
        spread = round(max(valid_wrs) - min(valid_wrs), 2)
    else:
        spread = None

    consistency = _consistency_label(spread)

    if consistency == COMBO_UNSTABLE:
        log.warning(
            "combo_validation: UNSTABLE combo %s | regime spread=%.1f%%",
            key, spread,
        )

    return {
        "combo":          signals,
        "key":            key,
        "size":           len(combo),
        **overall,
        "by_regime":      by_regime,
        "regime_spread":  spread,
        "consistency":    consistency,
    }


def _all_combos_of_size(size: int) -> list:
    """Generate all frozensets of ALL_SIGNALS of the given size."""
    return [frozenset(c) for c in combinations(ALL_SIGNALS, size)]


# ── Public analytics ──────────────────────────────────────────────────────────

def pair_combo_stats(rows: list, min_n: int = MIN_COMBO_ROWS) -> dict:
    """
    Statistics for all 15 signal pairs, keyed by sorted combo string.

    Only pairs with at least min_n matching rows are included.
    """
    result = {}
    for combo in _all_combos_of_size(2):
        entry = _compute_combo(rows, combo)
        if entry["n"] >= min_n:
            result[entry["key"]] = entry
    log.debug(
        "combo_validation: pair_combo_stats — %d/%d pairs qualified (min_n=%d)",
        len(result), len(_all_combos_of_size(2)), min_n,
    )
    return result


def triple_combo_stats(rows: list, min_n: int = MIN_COMBO_ROWS) -> dict:
    """
    Statistics for all 20 signal triples, keyed by sorted combo string.

    Only triples with at least min_n matching rows are included.
    """
    result = {}
    for combo in _all_combos_of_size(3):
        entry = _compute_combo(rows, combo)
        if entry["n"] >= min_n:
            result[entry["key"]] = entry
    log.debug(
        "combo_validation: triple_combo_stats — %d/%d triples qualified (min_n=%d)",
        len(result), len(_all_combos_of_size(3)), min_n,
    )
    return result


def deceptive_combo_detection(combo_stats: dict) -> list:
    """
    Detect combos with potentially misleading characteristics.

    Three deceptive types:
        HIGH_CONF_POOR_RETURN  — confidence signals quality, returns say otherwise
        LOTTERY_TICKET         — rare wins but occasionally large; can tempt over-trading
        HIGH_DRAWDOWN          — severe drawdowns regardless of win rate

    Returns a list of flag dicts sorted by severity (HIGH first), then combo key.
    """
    flags = []

    for key, entry in combo_stats.items():
        wr   = entry.get("win_rate")
        ret5 = entry.get("avg_return_5d")
        dd   = entry.get("avg_max_dd")
        conf = entry.get("avg_confidence")
        gain = entry.get("avg_max_gain")
        n    = entry.get("n", 0)

        if n < MIN_COMBO_ROWS:
            log.debug("combo_validation: skipping sparse combo %s (n=%d)", key, n)
            continue

        base = {
            "combo":          key,
            "signals":        entry.get("combo", []),
            "n":              n,
            "win_rate":       wr,
            "avg_return_5d":  ret5,
            "avg_max_dd":     dd,
            "avg_confidence": conf,
        }

        # ── HIGH_CONF_POOR_RETURN ─────────────────────────────────────────────
        if (conf is not None and conf >= DECEPTIVE_HIGH_CONF_THRESHOLD
                and ret5 is not None and ret5 < DECEPTIVE_POOR_RETURN):
            severity = "HIGH" if ret5 < -5.0 else "MEDIUM"
            flags.append({
                **base,
                "type":     "HIGH_CONF_POOR_RETURN",
                "detail":   (
                    f"avg_confidence={conf:.1f}% suggests quality but "
                    f"avg_return_5d={ret5:.2f}%"
                ),
                "severity": severity,
            })
            log.warning(
                "combo_validation: HIGH_CONF_POOR_RETURN %s — %s",
                severity, key,
            )

        # ── LOTTERY_TICKET ─────────────────────────────────────────────────────
        if (wr is not None and wr < DECEPTIVE_WIN_RATE_LOW
                and gain is not None and gain >= DECEPTIVE_LOTTERY_GAIN):
            flags.append({
                **base,
                "type":     "LOTTERY_TICKET",
                "detail":   (
                    f"win_rate={wr:.1f}% is low but avg_max_gain={gain:.1f}% "
                    "may create false confidence"
                ),
                "severity": "MEDIUM",
            })
            log.warning("combo_validation: LOTTERY_TICKET %s", key)

        # ── HIGH_DRAWDOWN ──────────────────────────────────────────────────────
        if dd is not None and dd < DECEPTIVE_DRAWDOWN_THRESHOLD:
            severity = "HIGH" if dd < DECEPTIVE_DRAWDOWN_THRESHOLD * 2 else "MEDIUM"
            flags.append({
                **base,
                "type":     "HIGH_DRAWDOWN",
                "detail":   f"avg_max_dd={dd:.2f}% indicates severe drawdown risk",
                "severity": severity,
            })
            log.warning(
                "combo_validation: HIGH_DRAWDOWN %s — %s (dd=%.2f%%)",
                severity, key, dd,
            )

    # Sort: HIGH severity first, then alphabetically by combo key
    flags.sort(key=lambda f: (0 if f["severity"] == "HIGH" else 1, f["combo"]))
    return flags


def rank_combos(combo_stats: dict, top_n: int = 5) -> dict:
    """
    Produce ranked views over a combo_stats dict.

    All lists are sorted deterministically (primary metric, then key as tie-break).
    top_n caps the length of each ranked list.

    Returns
    -------
    {
        "best_by_win_rate":   [ combo_entry, ... ],
        "worst_by_win_rate":  [ combo_entry, ... ],
        "best_by_risk_adj":   [ combo_entry, ... ],
        "worst_by_risk_adj":  [ combo_entry, ... ],
        "most_stable":        [ combo_entry, ... ],
        "most_dangerous":     [ combo_entry, ... ],
    }
    """
    entries = list(combo_stats.values())

    # ── Win rate ──────────────────────────────────────────────────────────────
    by_wr = sorted(
        [e for e in entries if e.get("win_rate") is not None],
        key=lambda e: (-e["win_rate"], e["key"]),
    )
    worst_wr = sorted(
        [e for e in entries if e.get("win_rate") is not None],
        key=lambda e: (e["win_rate"], e["key"]),
    )

    # ── Risk-adjusted score ───────────────────────────────────────────────────
    by_ra = sorted(
        [e for e in entries if e.get("risk_adj_score") is not None],
        key=lambda e: (-e["risk_adj_score"], e["key"]),
    )
    worst_ra = sorted(
        [e for e in entries if e.get("risk_adj_score") is not None],
        key=lambda e: (e["risk_adj_score"], e["key"]),
    )

    # ── Most stable (smallest regime_spread) ─────────────────────────────────
    by_stable = sorted(
        [e for e in entries if e.get("regime_spread") is not None],
        key=lambda e: (e["regime_spread"], e["key"]),
    )

    # ── Most dangerous (most negative avg_max_dd) ─────────────────────────────
    by_danger = sorted(
        [e for e in entries if e.get("avg_max_dd") is not None],
        key=lambda e: (e["avg_max_dd"], e["key"]),
    )

    return {
        "best_by_win_rate":  by_wr[:top_n],
        "worst_by_win_rate": worst_wr[:top_n],
        "best_by_risk_adj":  by_ra[:top_n],
        "worst_by_risk_adj": worst_ra[:top_n],
        "most_stable":       by_stable[:top_n],
        "most_dangerous":    by_danger[:top_n],
    }


def _build_warnings(
    pair_stats:   dict,
    triple_stats: dict,
    deceptive:    list,
    unstable:     list,
) -> list:
    """Collect human-readable warning strings."""
    warnings = []

    # Sparse-data notices (combos that had some rows but fell below min_n)
    # Already filtered out by pair/triple_combo_stats, so we note overall sparsity
    all_qualified = len(pair_stats) + len(triple_stats)
    if all_qualified == 0:
        warnings.append(
            f"No combos qualified (need >= {MIN_COMBO_ROWS} matching rows per combo)"
        )

    # Unstable combos
    for entry in unstable:
        spread = entry.get("regime_spread")
        warnings.append(
            f"Unstable combo {entry['key']}: regime win-rate spread={spread:.1f}%"
        )

    # Deceptive combos
    for flag in deceptive:
        severity = flag["severity"]
        warnings.append(
            f"Deceptive combo [{severity}] {flag['combo']} ({flag['type']}): "
            f"{flag['detail']}"
        )

    return warnings


def generate_report(rows: Optional[list] = None) -> dict:
    """
    Full signal-combination analytics report.

    If rows is None, fetches COMPLETE outcomes from the DB.
    Pass a pre-fetched list to avoid DB access (e.g. in tests).

    Returns
    -------
    {
        "row_count":               int,
        "pair_count":              int,    # qualified pairs
        "triple_count":            int,    # qualified triples
        "pair_stats":              { key: combo_entry },
        "triple_stats":            { key: combo_entry },
        "rankings": {
            "best_by_win_rate":   [...],
            "worst_by_win_rate":  [...],
            "best_by_risk_adj":   [...],
            "worst_by_risk_adj":  [...],
            "most_stable":        [...],
            "most_dangerous":     [...],
        },
        "deceptive_combos":        [ flag_dict, ... ],
        "unstable_combos":         [ combo_entry, ... ],
        "regime_dependent_combos": [ combo_entry, ... ],
        "warnings":                [ str, ... ],
    }
    """
    if rows is None:
        rows = _fetch_completed_outcomes()

    n = len(rows)
    log.info("combo_validation: generating report on %d completed outcomes", n)

    if n < MIN_COMBO_ROWS:
        log.warning(
            "combo_validation: only %d row(s) — most combo stats will be empty "
            "(need >= %d matching rows per combo)",
            n, MIN_COMBO_ROWS,
        )

    pairs   = pair_combo_stats(rows)
    triples = triple_combo_stats(rows)
    all_s   = {**pairs, **triples}

    rankings  = rank_combos(all_s)
    deceptive = deceptive_combo_detection(all_s)

    # Unstable = UNSTABLE consistency with a known spread
    unstable = sorted(
        [e for e in all_s.values()
         if e.get("consistency") == COMBO_UNSTABLE
         and e.get("regime_spread") is not None],
        key=lambda e: (-e["regime_spread"], e["key"]),
    )

    # Regime-dependent = UNSTABLE or MIXED
    regime_dependent = sorted(
        [e for e in all_s.values()
         if e.get("consistency") in (COMBO_UNSTABLE, COMBO_MIXED)],
        key=lambda e: (-(e.get("regime_spread") or 0), e["key"]),
    )

    warnings = _build_warnings(pairs, triples, deceptive, unstable)

    log.info(
        "combo_validation: %d pairs, %d triples | "
        "deceptive=%d unstable=%d regime_dependent=%d warnings=%d",
        len(pairs), len(triples),
        len(deceptive), len(unstable), len(regime_dependent), len(warnings),
    )

    return {
        "row_count":               n,
        "pair_count":              len(pairs),
        "triple_count":            len(triples),
        "pair_stats":              pairs,
        "triple_stats":            triples,
        "rankings":                rankings,
        "deceptive_combos":        deceptive,
        "unstable_combos":         unstable,
        "regime_dependent_combos": regime_dependent,
        "warnings":                warnings,
    }
