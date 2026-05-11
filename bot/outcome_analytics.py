"""
Outcome analytics for the Predator scanner.

Aggregates completed predator_outcomes rows to measure signal effectiveness,
win rates, and return statistics — enabling future adaptive weighting and
confidence calibration from real trade outcomes.

All public functions are read-only and deterministic.  They accept a list of
row dicts (pre-fetched from the DB) so they can be unit-tested without a live
database.  The one DB-touching function (_fetch_completed_outcomes) is kept
small and isolated.

"Win" definition (configurable):
    return_5d > threshold_5d  OR  max_gain_pct >= threshold_gain
    defaults: threshold_5d=0.0, threshold_gain=5.0
"""
import json
import logging
from typing import Optional

from database import get_connection

log = logging.getLogger(__name__)

# Minimum rows in a bucket before win-rate is considered reliable
MIN_ROWS_FOR_STATS = 5

_SIGNAL_NAMES = (
    "options",
    "insider",
    "short_squeeze",
    "catalyst",
    "institutional",
    "breakout",
)

_CONFIDENCE_BUCKETS = (
    ("LOW",    0.0,   40.0),
    ("MEDIUM", 40.0,  65.0),
    ("HIGH",   65.0, 101.0),
)


# ── Win definition ────────────────────────────────────────────────────────────

def is_win(
    row: dict,
    threshold_5d: float   = 0.0,
    threshold_gain: float = 5.0,
) -> bool:
    """
    Return True if the outcome qualifies as a win.

    A row wins when either:
      - return_5d  > threshold_5d   (closed higher within 5 trading days)
      - max_gain_pct >= threshold_gain (had a significant intraday gain)

    Rows with both values None are treated as unknown, not a win.
    """
    r5d  = row.get("return_5d")
    gain = row.get("max_gain_pct")
    if r5d  is not None and r5d  >  threshold_5d:
        return True
    if gain is not None and gain >= threshold_gain:
        return True
    return False


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _avg(values: list) -> Optional[float]:
    """Mean of non-None values; None if no valid data."""
    valid = [v for v in values if v is not None]
    return round(sum(valid) / len(valid), 2) if valid else None


def _win_rate(rows: list, **win_kwargs) -> Optional[float]:
    """
    Win rate as a percentage.  Returns None when n < MIN_ROWS_FOR_STATS
    to signal that the estimate is unreliable rather than returning a
    misleading number.
    """
    if len(rows) < MIN_ROWS_FOR_STATS:
        return None
    wins = sum(1 for r in rows if is_win(r, **win_kwargs))
    return round(wins / len(rows) * 100, 1)


def _confidence_bucket(pct: Optional[float]) -> str:
    """Classify a confidence percentage into LOW / MEDIUM / HIGH / UNKNOWN."""
    if pct is None:
        return "UNKNOWN"
    for label, lo, hi in _CONFIDENCE_BUCKETS:
        if lo <= pct < hi:
            return label
    return "UNKNOWN"


def _active_signals(row: dict) -> frozenset:
    """
    Parse signal_summary JSON and return the frozenset of signal names
    whose score is > 0.  Returns an empty frozenset on any parse failure.
    """
    try:
        summary = json.loads(row.get("signal_summary") or "{}")
        return frozenset(k for k, v in summary.items() if (v or 0) > 0)
    except (json.JSONDecodeError, TypeError, AttributeError):
        return frozenset()


def _bucket_stats(rows: list) -> dict:
    """Compute the standard stats dict for a bucket of outcome rows."""
    return {
        "n":              len(rows),
        "win_rate":       _win_rate(rows),
        "avg_return_5d":  _avg([r.get("return_5d")        for r in rows]),
        "avg_return_20d": _avg([r.get("return_20d")       for r in rows]),
        "avg_max_gain":   _avg([r.get("max_gain_pct")     for r in rows]),
        "avg_max_dd":     _avg([r.get("max_drawdown_pct") for r in rows]),
    }


def _group_by(rows: list, key_fn) -> dict:
    """Partition rows by key_fn and return a sorted dict of {key: [rows]}."""
    groups: dict = {}
    for row in rows:
        k = key_fn(row)
        groups.setdefault(k, []).append(row)
    return dict(sorted(groups.items()))


def _compute_lift(active_rows: list, silent_rows: list) -> Optional[float]:
    """
    Win-rate lift of the active group over the silent group.
    Positive = signal correlates with wins; negative = correlated with losses.
    Returns None when either group is too small for a reliable estimate.
    """
    wr_active = _win_rate(active_rows)
    wr_silent = _win_rate(silent_rows)
    if wr_active is None or wr_silent is None:
        return None
    return round(wr_active - wr_silent, 1)


# ── Data access ───────────────────────────────────────────────────────────────

def _fetch_completed_outcomes() -> list:
    """Return all COMPLETE outcome rows as plain dicts."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM predator_outcomes WHERE outcome_status = 'COMPLETE'"
        ).fetchall()
        result = [dict(r) for r in rows]
        log.debug("outcome_analytics: fetched %d completed rows", len(result))
        return result
    finally:
        conn.close()


# ── Public analytics functions ────────────────────────────────────────────────

def overall_stats(rows: list) -> dict:
    """Overall win rate and return statistics across all rows."""
    if not rows:
        return {"n": 0, "status": "no_data"}
    stats = _bucket_stats(rows)
    stats["win_count"] = sum(1 for r in rows if is_win(r))
    return stats


def stats_by_tier(rows: list) -> dict:
    """Win rate and return stats grouped by conviction tier."""
    groups = _group_by(rows, lambda r: r.get("tier") or "UNKNOWN")
    return {tier: _bucket_stats(grp) for tier, grp in groups.items()}


def stats_by_regime(rows: list) -> dict:
    """Win rate and return stats grouped by market regime."""
    groups = _group_by(rows, lambda r: r.get("regime") or "UNKNOWN")
    return {regime: _bucket_stats(grp) for regime, grp in groups.items()}


def stats_by_confidence_bucket(rows: list) -> dict:
    """Win rate and return stats for LOW / MEDIUM / HIGH confidence buckets."""
    groups = _group_by(rows, lambda r: _confidence_bucket(r.get("confidence_pct")))
    return {bucket: _bucket_stats(grp) for bucket, grp in groups.items()}


def signal_effectiveness(rows: list) -> dict:
    """
    For each signal, compare win rates when the signal was active vs silent.

    Returns a dict keyed by signal name, each containing:
      active  — stats for rows where this signal fired
      silent  — stats for rows where this signal did not fire
      lift    — win-rate difference (active − silent); None if too few data points
    """
    result = {}
    for sig in _SIGNAL_NAMES:
        active_rows = [r for r in rows if sig in _active_signals(r)]
        silent_rows = [r for r in rows if sig not in _active_signals(r)]
        result[sig] = {
            "active": _bucket_stats(active_rows),
            "silent": _bucket_stats(silent_rows),
            "lift":   _compute_lift(active_rows, silent_rows),
        }
    return result


def signal_combo_ranking(rows: list, min_n: int = MIN_ROWS_FOR_STATS) -> dict:
    """
    Group rows by their exact set of active signals and rank by win rate.

    Only combos with at least min_n rows are included in the ranking.
    Tie-breaking is alphabetical on the combo's string representation,
    making the output fully deterministic.

    Returns:
        ranked    — list of combo dicts sorted by win_rate descending
        strongest — combo dict with highest win rate (or None)
        weakest   — combo dict with lowest win rate (or None)
    """
    groups: dict = {}
    for row in rows:
        key = tuple(sorted(_active_signals(row)))  # deterministic hashable key
        groups.setdefault(key, []).append(row)

    entries = []
    for combo_key, combo_rows in groups.items():
        if len(combo_rows) < min_n:
            continue
        wr = _win_rate(combo_rows)
        entries.append({
            "signals":        list(combo_key),
            "n":              len(combo_rows),
            "win_rate":       wr,
            "avg_return_5d":  _avg([r.get("return_5d")    for r in combo_rows]),
            "avg_return_20d": _avg([r.get("return_20d")   for r in combo_rows]),
            "avg_max_gain":   _avg([r.get("max_gain_pct") for r in combo_rows]),
        })

    # Deterministic sort: win_rate descending, then combo repr ascending for ties
    entries.sort(key=lambda e: (-(e["win_rate"] or 0.0), str(e["signals"])))

    return {
        "ranked":    entries,
        "strongest": entries[0]  if entries else None,
        "weakest":   entries[-1] if entries else None,
    }


def best_worst_regimes(rows: list) -> dict:
    """
    Identify the best and worst regimes by win rate.

    Only regimes with enough rows to produce a non-None win rate are ranked.
    """
    by_regime = stats_by_regime(rows)

    ranked = sorted(
        ((regime, stats["win_rate"])
         for regime, stats in by_regime.items()
         if stats.get("win_rate") is not None),
        key=lambda x: (-x[1], x[0]),  # win_rate desc, name asc for ties
    )

    return {
        "best":      {"regime": ranked[0][0],  "win_rate": ranked[0][1]}  if ranked else None,
        "worst":     {"regime": ranked[-1][0], "win_rate": ranked[-1][1]} if ranked else None,
        "by_regime": by_regime,
    }


def generate_report(rows: Optional[list] = None) -> dict:
    """
    Generate a complete analytics report.

    If rows is None, fetches COMPLETE outcomes from the DB.
    All analytics sections are included; sections with insufficient data
    will contain None win-rate values rather than fabricated numbers.
    """
    if rows is None:
        rows = _fetch_completed_outcomes()

    n = len(rows)
    log.info("outcome_analytics: generating report on %d completed outcomes", n)

    if n < MIN_ROWS_FOR_STATS:
        log.warning(
            "outcome_analytics: only %d row(s) — win-rate estimates require >= %d rows; "
            "averages are still reported where data exists",
            n, MIN_ROWS_FOR_STATS,
        )

    report = {
        "row_count":            n,
        "overall":              overall_stats(rows),
        "by_tier":              stats_by_tier(rows),
        "by_regime":            stats_by_regime(rows),
        "by_confidence":        stats_by_confidence_bucket(rows),
        "signal_effectiveness": signal_effectiveness(rows),
        "combo_ranking":        signal_combo_ranking(rows),
        "regime_ranking":       best_worst_regimes(rows),
    }

    log.info(
        "outcome_analytics: report complete | n=%d win_rate=%s avg_5d=%s avg_20d=%s",
        n,
        report["overall"].get("win_rate"),
        report["overall"].get("avg_return_5d"),
        report["overall"].get("avg_return_20d"),
    )

    return report
