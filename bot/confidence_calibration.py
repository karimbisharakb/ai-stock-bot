"""
Confidence calibration layer for the Predator scanner.

Operates on already-computed signal scores to produce a calibrated confidence
value that better reflects signal independence, conflicts, and agreement.
Does not rewrite any scoring engine — it post-processes their outputs only.

Three adjustments are applied in sequence:
  1. Correlation penalty  — diminishing returns for signal pairs that share
                            the same underlying factor (e.g. options + squeeze)
  2. Conflict penalty     — penalize contradictory signal combinations
                            (e.g. unusual options flow with no price action)
  3. Agreement boost      — reward genuine multi-category alignment
                            (independent rationales arriving at the same conclusion)

All functions are pure: deterministic, no I/O, no side effects.
Output is always clamped to [0, 100].
"""
import logging
from typing import Optional

log = logging.getLogger(__name__)

# ── Correlated pairs ──────────────────────────────────────────────────────────
# (signal_a, signal_b, penalty_points)
# Penalty applied when BOTH signals have score > 0.
# Rationale: correlated signals may reflect the same underlying factor so
# their combined confidence contribution deserves diminishing returns.

_CORRELATED_PAIRS: list = [
    ("options",       "short_squeeze", 2.5),  # both reflect volatility momentum
    ("institutional", "insider",       3.0),  # both are "smart money" flows
    ("breakout",      "options",       2.0),  # same momentum event can drive both
]

# ── Signal categories (for agreement boost) ───────────────────────────────────
# Signals in different categories represent independent economic rationales.
# When signals from multiple distinct categories all fire, confidence quality
# improves because convergence of independent evidence is harder to fake.

_SIGNAL_CATEGORIES: dict = {
    "smart_money":  frozenset(["institutional", "insider"]),
    "volatility":   frozenset(["options",        "short_squeeze"]),
    "technical":    frozenset(["breakout"]),
    "fundamental":  frozenset(["catalyst"]),
}

# Boost by number of active independent categories (2, 3, 4)
_AGREEMENT_BOOST_TABLE: dict = {2: 2.0, 3: 3.5, 4: 5.0}
_AGREEMENT_BOOST_CAP = 5.0  # hard cap regardless of table


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sig_score(signals: dict, name: str) -> int:
    """Safely extract a signal's integer score, defaulting to 0."""
    return int(signals.get(name, {}).get("score") or 0)


def _correlation_penalty(signals: dict) -> float:
    """
    Return total correlation penalty for the given signals.

    For each configured correlated pair where both signals have score > 0,
    add the pair's penalty.  Signals with score == 0 do not trigger the pair.
    """
    total = 0.0
    for sig_a, sig_b, penalty in _CORRELATED_PAIRS:
        if _sig_score(signals, sig_a) > 0 and _sig_score(signals, sig_b) > 0:
            total += penalty
            log.debug(
                "calibration: correlated pair (%s, %s) — penalty=−%.1f",
                sig_a, sig_b, penalty,
            )
    return total


def _conflict_penalty(signals: dict) -> float:
    """
    Return total conflict penalty for contradictory signal combinations.

    Conflicts detected (all require specific score thresholds):
      A. Options spike (≥ 2) without any price action  (breakout=0, squeeze=0)
         → Unusual call flow not backed by actual price movement
      B. Isolated catalyst (> 0) with no positioning   (options=0, insider=0, inst=0)
         → Event-driven signal no one is positioning for
      C. Strong squeeze (≥ 2) without options activity (options=0)
         → High-short-float thesis unconfirmed by options speculation
    """
    opts  = _sig_score(signals, "options")
    brk   = _sig_score(signals, "breakout")
    sq    = _sig_score(signals, "short_squeeze")
    cat   = _sig_score(signals, "catalyst")
    ins   = _sig_score(signals, "insider")
    inst  = _sig_score(signals, "institutional")

    total = 0.0

    # A: options activity without any price confirmation
    if opts >= 2 and brk == 0 and sq == 0:
        total += 3.0
        log.debug("calibration: conflict A — options without price action, penalty=−3.0")

    # B: catalyst with no smart-money or options positioning
    if cat > 0 and opts == 0 and ins == 0 and inst == 0:
        total += 2.0
        log.debug("calibration: conflict B — isolated catalyst, penalty=−2.0")

    # C: strong squeeze signal but options market is silent
    if sq >= 2 and opts == 0:
        total += 2.0
        log.debug("calibration: conflict C — squeeze without options, penalty=−2.0")

    return total


def _agreement_boost(signals: dict) -> float:
    """
    Return confidence boost for multi-category signal agreement.

    Count how many distinct signal categories have at least one active signal.
    The more independent categories that converge, the higher the boost.
    Boost is capped at _AGREEMENT_BOOST_CAP.
    """
    active_categories = sum(
        1
        for members in _SIGNAL_CATEGORIES.values()
        if any(_sig_score(signals, name) > 0 for name in members)
    )

    boost = _AGREEMENT_BOOST_TABLE.get(active_categories, 0.0)
    # For counts beyond the table (shouldn't happen with 4 categories, but safe)
    if active_categories > max(_AGREEMENT_BOOST_TABLE):
        boost = _AGREEMENT_BOOST_CAP

    boost = min(boost, _AGREEMENT_BOOST_CAP)

    if boost > 0:
        log.debug(
            "calibration: %d active categories → agreement boost=+%.1f",
            active_categories, boost,
        )
    return boost


def calibrate_confidence(raw_confidence: float, signals: dict) -> float:
    """
    Apply calibration adjustments and return a clamped, calibrated confidence.

    Parameters
    ----------
    raw_confidence : float
        Confidence produced by compute_confidence(), in [0, 100].
    signals : dict
        Signal dict from _score_ticker() — each key maps to a sub-dict
        with at minimum a ``"score"`` field.

    Returns
    -------
    float
        Calibrated confidence in [0.0, 100.0], rounded to 2 decimal places.
        Identical to ``raw_confidence`` when no adjustments apply.
    """
    corr    = _correlation_penalty(signals)
    conflict = _conflict_penalty(signals)
    boost   = _agreement_boost(signals)

    adjustment = boost - corr - conflict
    calibrated = max(0.0, min(100.0, raw_confidence + adjustment))
    calibrated = round(calibrated, 2)

    if adjustment != 0.0:
        log.info(
            "calibration: raw=%.1f%% → cal=%.1f%% "
            "(agree=+%.2f corr=−%.2f conflict=−%.2f net=%+.2f)",
            raw_confidence, calibrated,
            boost, corr, conflict, adjustment,
        )
    else:
        log.debug("calibration: no adjustments (raw=%.1f%%)", raw_confidence)

    return calibrated
