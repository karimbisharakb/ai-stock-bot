"""
Confidence validation analytics for the Predator scanner.
Phase 2A — read-only; does NOT modify live scoring.

Measures whether stated confidence values predict real trade outcomes by
analysing win rates, returns, and drawdowns across 10-point confidence
decades.  Computes Expected Calibration Error (ECE), Pearson correlation,
monotonicity, and overconfidence flags.

All public functions are pure (accept row lists, no I/O) except
generate_report(), which optionally fetches completed outcomes from the DB.

Key concepts
------------
- Confidence decade   : 10-point interval (0-9, 10-19, ..., 90-100)
- ECE                 : Σ_b (n_b/N) × |win_frac_b − mean_conf_b|  in [0, 1]
                        Lower = better calibrated.
- Monotonicity        : win rate should increase across decades.
                        An inversion is where a higher decade has a lower
                        win rate than the decade below it.
- Overconfidence      : decade midpoint − win_rate > OVERCONF_MARGIN
                        (claiming more probability than delivered).
"""
import logging
from typing import Optional

from outcome_analytics import (
    MIN_ROWS_FOR_STATS,
    _avg,
    _fetch_completed_outcomes,
    _win_rate,
    is_win,
)

log = logging.getLogger(__name__)

# ── Decade definitions ────────────────────────────────────────────────────────
# Each entry: (label, lo_inclusive, hi_exclusive, midpoint)
# The last bucket uses hi=101.0 so confidence==100 falls inside it;
# its midpoint is set to 95.0 (midpoint of 90–100).
_DECADES: list = [
    ("0-9",    0.0,  10.0,  4.5),
    ("10-19",  10.0, 20.0, 14.5),
    ("20-29",  20.0, 30.0, 24.5),
    ("30-39",  30.0, 40.0, 34.5),
    ("40-49",  40.0, 50.0, 44.5),
    ("50-59",  50.0, 60.0, 54.5),
    ("60-69",  60.0, 70.0, 64.5),
    ("70-79",  70.0, 80.0, 74.5),
    ("80-89",  80.0, 90.0, 84.5),
    ("90-100", 90.0, 101.0, 95.0),
]

# Pre-built lookups for cheap repeated access
_DECADE_ORDER:  list  = [d[0] for d in _DECADES]           # ordered labels
_DECADE_BOUNDS: dict  = {d[0]: (d[1], d[2]) for d in _DECADES}  # label → (lo, hi)
_DECADE_MIDS:   dict  = {d[0]: d[3]         for d in _DECADES}  # label → midpoint

# ── Thresholds ────────────────────────────────────────────────────────────────
# Decades at or above this lower-bound are considered "high confidence".
HIGH_CONFIDENCE_LO: float = 70.0

# (decade_midpoint − win_rate) must exceed this to raise an overconfidence flag.
OVERCONF_MARGIN: float = 15.0

# ECE quality gates (ECE is in [0, 1])
ECE_GOOD: float = 0.10
ECE_FAIR: float = 0.20

# Calibration quality labels
QUALITY_GOOD             = "GOOD"
QUALITY_FAIR             = "FAIR"
QUALITY_POOR             = "POOR"
QUALITY_INSUFFICIENT     = "INSUFFICIENT_DATA"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _confidence_decade(pct: Optional[float]) -> Optional[str]:
    """
    Return the decade label for a confidence percentage.

    Returns None when pct is None, negative, or > 100 (out of range).
    The decade boundary rule is lo ≤ pct < hi (hi=101 for the last bucket).
    """
    if pct is None or pct < 0.0 or pct > 100.0:
        return None
    for label, lo, hi, _ in _DECADES:
        if lo <= pct < hi:
            return label
    return None


def _mean(values: list) -> Optional[float]:
    """Mean of a non-empty list of non-None floats; None if no valid data."""
    valid = [v for v in values if v is not None]
    return round(sum(valid) / len(valid), 4) if valid else None


def _pearson(xs: list, ys: list) -> Optional[float]:
    """
    Pearson correlation coefficient for paired (x, y) lists.

    Pairs where either value is None are dropped before calculation.
    Returns None when fewer than 3 valid pairs remain or either variable
    has zero variance (constant column → correlation is undefined).
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


def _group_by_decade(rows: list) -> dict:
    """
    Partition rows by their confidence decade label.

    Rows with None or out-of-range confidence_pct are placed in the
    "UNKNOWN" bucket and excluded from calibration calculations.
    """
    groups: dict = {}
    for row in rows:
        label = _confidence_decade(row.get("confidence_pct"))
        key   = label if label is not None else "UNKNOWN"
        groups.setdefault(key, []).append(row)
    return groups


def _decade_bucket_stats(decade_rows: list) -> dict:
    """Extended bucket stats for a decade: includes mean_confidence."""
    confs = [r.get("confidence_pct") for r in decade_rows]
    return {
        "n":               len(decade_rows),
        "win_rate":        _win_rate(decade_rows),
        "avg_return_5d":   _avg([r.get("return_5d")        for r in decade_rows]),
        "avg_return_20d":  _avg([r.get("return_20d")       for r in decade_rows]),
        "avg_max_gain":    _avg([r.get("max_gain_pct")     for r in decade_rows]),
        "avg_max_dd":      _avg([r.get("max_drawdown_pct") for r in decade_rows]),
        "mean_confidence": _mean(confs),
    }


# ── Public analytics ──────────────────────────────────────────────────────────

def decade_stats(rows: list) -> dict:
    """
    Win rate, return, and drawdown statistics grouped by confidence decade.

    All ten decades are always present in the output.  Decades with no rows
    have n=0 and None for all numeric fields.  Decades with fewer than
    MIN_ROWS_FOR_STATS rows have win_rate=None (unreliable estimate).

    Returns a dict ordered from "0-9" to "90-100".
    """
    groups = _group_by_decade(rows)
    result = {}
    for label in _DECADE_ORDER:
        decade_rows = groups.get(label, [])
        result[label] = _decade_bucket_stats(decade_rows)
    return result


def calibration_error(rows: list) -> Optional[float]:
    """
    Expected Calibration Error (ECE) in [0, 1].

    ECE = Σ_b (n_b / N) × |win_frac_b − mean_conf_b|

    where:
      n_b          = number of rows in decade b
      N            = total rows in decades that contribute to ECE
                     (those with win_rate != None, i.e. n_b >= MIN_ROWS_FOR_STATS)
      win_frac_b   = win_rate_b / 100           (normalised to [0, 1])
      mean_conf_b  = mean(confidence_pct) / 100 (normalised to [0, 1])

    Returns None when fewer than MIN_ROWS_FOR_STATS total qualifying rows exist.
    """
    groups = _group_by_decade(rows)

    qualifying: list = []   # (n, win_frac, mean_conf_frac)
    for label in _DECADE_ORDER:
        decade_rows = groups.get(label, [])
        wr = _win_rate(decade_rows)
        if wr is None:
            continue  # too sparse — exclude this decade from ECE
        confs = [r.get("confidence_pct") for r in decade_rows
                 if r.get("confidence_pct") is not None]
        if not confs:
            continue
        mean_conf = sum(confs) / len(confs)
        qualifying.append((len(decade_rows), wr / 100.0, mean_conf / 100.0))

    n_total = sum(q[0] for q in qualifying)
    if n_total < MIN_ROWS_FOR_STATS:
        return None

    ece = sum((n / n_total) * abs(wf - mc) for n, wf, mc in qualifying)
    return round(ece, 4)


def confidence_return_correlation(rows: list) -> Optional[float]:
    """
    Pearson correlation between confidence_pct and return_5d.

    Interpretation:
      > 0   higher confidence predicts better 5-day returns (good calibration)
      ≈ 0   confidence has no predictive power
      < 0   higher confidence predicts worse returns (overconfidence signal)

    Returns None when fewer than 3 rows have both values present.
    """
    confs   = [r.get("confidence_pct") for r in rows]
    returns = [r.get("return_5d")       for r in rows]
    result  = _pearson(confs, returns)
    if result is not None:
        log.debug(
            "confidence_validation: correlation(confidence, return_5d)=%.4f (n=%d)",
            result, sum(1 for c, r in zip(confs, returns) if c is not None and r is not None),
        )
    return result


def monotonicity_analysis(stats: dict) -> dict:
    """
    Check whether win rates increase non-decreasingly across confidence decades.

    An inversion occurs when a higher decade has a strictly lower win rate
    than the decade immediately below it (among decades with enough data).

    Parameters
    ----------
    stats : output of decade_stats() — keyed by decade label

    Returns
    -------
    {
        "is_monotone":      bool,
        "inversion_count":  int,
        "inversions":       [ {low_decade, high_decade, low_wr, high_wr, delta}, ... ],
        "buckets_analyzed": int,
    }
    """
    # Build ordered list of (label, win_rate) for decades with valid win_rate
    valid = [
        (label, stats[label]["win_rate"])
        for label in _DECADE_ORDER
        if label in stats and stats[label].get("win_rate") is not None
    ]

    inversions = []
    for i in range(len(valid) - 1):
        label_lo, wr_lo = valid[i]
        label_hi, wr_hi = valid[i + 1]
        if wr_hi < wr_lo:
            delta = round(wr_hi - wr_lo, 1)
            inversions.append({
                "low_decade":  label_lo,
                "high_decade": label_hi,
                "low_wr":      wr_lo,
                "high_wr":     wr_hi,
                "delta":       delta,
            })
            log.warning(
                "confidence_validation: inversion — %s (%.1f%%) > %s (%.1f%%) Δ=%.1f%%",
                label_lo, wr_lo, label_hi, wr_hi, delta,
            )

    return {
        "is_monotone":      len(inversions) == 0,
        "inversion_count":  len(inversions),
        "inversions":       inversions,
        "buckets_analyzed": len(valid),
    }


def overconfidence_flags(rows: list, stats: dict) -> list:
    """
    Identify decades where confidence materially overstates actual win rate.

    A flag is raised when, for a decade with enough rows:
      decade_midpoint − win_rate > OVERCONF_MARGIN

    Only decades whose lower bound >= HIGH_CONFIDENCE_LO are flagged as
    "high-confidence overconfidence"; lower decades are flagged as
    "overconfidence" without the high-confidence qualifier.

    Parameters
    ----------
    rows  : raw outcome rows (used for overall win rate baseline)
    stats : output of decade_stats()

    Returns
    -------
    List of flag dicts sorted by overconfidence gap descending:
        { decade, midpoint, win_rate, overconf_gap, is_high_confidence,
          avg_max_dd, severity }
    """
    overall_wr = _win_rate(rows)  # baseline; may be None if rows is sparse

    flags = []
    for label in _DECADE_ORDER:
        bucket = stats.get(label, {})
        wr     = bucket.get("win_rate")
        n      = bucket.get("n", 0)

        if wr is None or n < MIN_ROWS_FOR_STATS:
            continue

        mid  = _DECADE_MIDS[label]
        gap  = round(mid - wr, 1)   # positive = overconfident

        if gap <= OVERCONF_MARGIN:
            continue

        lo, _  = _DECADE_BOUNDS[label]
        is_hc  = lo >= HIGH_CONFIDENCE_LO
        severity = "HIGH" if gap > OVERCONF_MARGIN * 2 else "MEDIUM"

        flag = {
            "decade":              label,
            "midpoint":            mid,
            "win_rate":            wr,
            "overall_win_rate":    overall_wr,
            "overconf_gap":        gap,
            "is_high_confidence":  is_hc,
            "avg_max_dd":          bucket.get("avg_max_dd"),
            "severity":            severity,
        }
        flags.append(flag)
        log.warning(
            "confidence_validation: overconfidence %s in decade %s "
            "(mid=%.1f%% win_rate=%.1f%% gap=%.1f%%)",
            severity, label, mid, wr, gap,
        )

    flags.sort(key=lambda f: -f["overconf_gap"])
    return flags


def _calibration_quality(ece: Optional[float], mono: dict) -> str:
    """Classify calibration quality from ECE and monotonicity."""
    if ece is None:
        return QUALITY_INSUFFICIENT
    if ece <= ECE_GOOD and mono["is_monotone"]:
        return QUALITY_GOOD
    if ece <= ECE_FAIR or mono["inversion_count"] <= 1:
        return QUALITY_FAIR
    return QUALITY_POOR


def _build_warnings(
    stats:   dict,
    mono:    dict,
    oconf:   list,
    ece:     Optional[float],
    corr:    Optional[float],
) -> list:
    """Collect human-readable warning strings for the report."""
    warnings = []

    # Sparse-data warnings (have rows but not enough for win_rate)
    for label in _DECADE_ORDER:
        bucket = stats.get(label, {})
        n = bucket.get("n", 0)
        if 0 < n < MIN_ROWS_FOR_STATS:
            warnings.append(
                f"Bucket {label} has only {n} row(s) "
                f"(need {MIN_ROWS_FOR_STATS} for reliable win rate)"
            )

    # Monotonicity inversions
    for inv in mono["inversions"]:
        warnings.append(
            f"Inversion: {inv['low_decade']} ({inv['low_wr']:.1f}%) "
            f"> {inv['high_decade']} ({inv['high_wr']:.1f}%), "
            f"Δ={inv['delta']:.1f}%"
        )

    # Overconfidence
    for flag in oconf:
        label = flag["decade"]
        qualifier = "high-confidence " if flag["is_high_confidence"] else ""
        warnings.append(
            f"Overconfidence ({flag['severity']}) in {label}: "
            f"stated {flag['midpoint']:.1f}% but win_rate={flag['win_rate']:.1f}% "
            f"(gap={flag['overconf_gap']:.1f}%) [{qualifier}bucket]"
        )

    # Negative correlation
    if corr is not None and corr < -0.10:
        warnings.append(
            f"Negative confidence-return correlation ({corr:.3f}): "
            "higher confidence is associated with worse returns"
        )

    # ECE quality
    if ece is not None and ece > ECE_FAIR:
        warnings.append(
            f"Poor calibration (ECE={ece:.3f} > {ECE_FAIR:.2f}): "
            "confidence values are unreliable predictors of outcome"
        )

    return warnings


def generate_report(rows: Optional[list] = None) -> dict:
    """
    Full confidence validation report.

    If rows is None, fetches COMPLETE outcomes from the DB.
    Pass a list of pre-fetched row dicts to avoid DB access (e.g. in tests).

    Returns
    -------
    {
        "row_count":            int,
        "decade_stats":         { decade_label: { n, win_rate, avg_return_5d,
                                                   avg_return_20d, avg_max_gain,
                                                   avg_max_dd, mean_confidence } },
        "calibration": {
            "ece":              float | None,
            "correlation":      float | None,
            "monotonicity":     { is_monotone, inversion_count, inversions,
                                  buckets_analyzed },
            "quality":          "GOOD" | "FAIR" | "POOR" | "INSUFFICIENT_DATA",
        },
        "overconfidence_flags": [ { decade, midpoint, win_rate, ... } ],
        "strongest_bucket":     { label, win_rate, n }  | None,
        "weakest_bucket":       { label, win_rate, n }  | None,
        "warnings":             [ str, ... ],
    }
    """
    if rows is None:
        rows = _fetch_completed_outcomes()

    n = len(rows)
    log.info("confidence_validation: generating report on %d completed outcomes", n)

    if n < MIN_ROWS_FOR_STATS:
        log.warning(
            "confidence_validation: only %d row(s) — calibration metrics require "
            ">= %d rows; most fields will be None",
            n, MIN_ROWS_FOR_STATS,
        )

    stats    = decade_stats(rows)
    ece      = calibration_error(rows)
    corr     = confidence_return_correlation(rows)
    mono     = monotonicity_analysis(stats)
    oconf    = overconfidence_flags(rows, stats)
    quality  = _calibration_quality(ece, mono)

    # Strongest / weakest buckets among those with enough data
    eligible = [
        {"label": label, "win_rate": stats[label]["win_rate"], "n": stats[label]["n"]}
        for label in _DECADE_ORDER
        if stats[label].get("win_rate") is not None
    ]
    eligible_sorted = sorted(eligible, key=lambda x: (-x["win_rate"], x["label"]))
    strongest = eligible_sorted[0]  if eligible_sorted else None
    weakest   = eligible_sorted[-1] if eligible_sorted else None

    warnings = _build_warnings(stats, mono, oconf, ece, corr)

    log.info(
        "confidence_validation: ECE=%s corr=%s quality=%s "
        "inversions=%d overconf_flags=%d warnings=%d",
        f"{ece:.4f}" if ece is not None else "None",
        f"{corr:.4f}" if corr is not None else "None",
        quality,
        mono["inversion_count"],
        len(oconf),
        len(warnings),
    )

    return {
        "row_count":   n,
        "decade_stats": stats,
        "calibration": {
            "ece":         ece,
            "correlation": corr,
            "monotonicity": mono,
            "quality":     quality,
        },
        "overconfidence_flags": oconf,
        "strongest_bucket":     strongest,
        "weakest_bucket":       weakest,
        "warnings":             warnings,
    }
