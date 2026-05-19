"""
Phase L2 — Shadow weight recommendations and policy simulation.

Observation-only: reads COMPLETE alpha_outcomes rows and produces
weight/threshold recommendations in shadow mode.  Never mutates live
Alpha scoring.  Never sends alerts.  Never affects Predator.

Public API:
  generate_recommendations_report() -> dict
  generate_shadow_policy_report()   -> dict
"""
import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_MIN_SAMPLES             = 10     # minimum complete rows for any recommendation
_HIGH_SCORE_THRESHOLD    = 6.0   # component "high" when score >= this
_DELTA_CAP               = 0.05  # max allowed weight change per component
_WIN_THRESHOLD           = 0.0   # return_5d > this = win
_FP_THRESHOLD            = -0.05 # return_5d < this = false positive
_LIFT_INCREASE_THRESHOLD = 1.30  # recommend weight increase if lift >= this
_LIFT_DECREASE_THRESHOLD = 0.70  # recommend weight decrease if lift <= this
_SHRINKAGE_N             = 20    # sample count for full recommendation confidence
_TIER_FP_LOOSE           = 0.40  # tier flagged "too loose" if FP rate > this

# Live scoring weights — read-only reference. Never mutated here.
_CURRENT_WEIGHTS: dict = {
    "relative_strength": 0.15,
    "acceleration":      0.15,
    "squeeze":           0.12,
    "catalyst":          0.15,
    "options":           0.13,
    "breakout":          0.15,
    "risk_reward":       0.10,
    "novelty":           0.05,
}
assert abs(sum(_CURRENT_WEIGHTS.values()) - 1.0) < 1e-9, "Current weights must sum to 1.0"

# Live tier thresholds — read-only reference.
_CURRENT_TIER_THRESHOLDS: dict = {
    "RARE_SETUP":      80.0,
    "HIGH_CONVICTION": 65.0,
    "STRONG_WATCH":    50.0,
    "WATCH":           35.0,
}

# Minimum eligible tier rank for "acted on" calculation in replay
_ELIGIBLE_TIER_RANK = 2  # STRONG_WATCH = rank 2


# ── Tier rank table (used in replay comparisons) ──────────────────────────────

_TIER_RANK: dict = {
    "IGNORE":          0,
    "WATCH":           1,
    "STRONG_WATCH":    2,
    "HIGH_CONVICTION": 3,
    "RARE_SETUP":      4,
}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _load_complete_outcomes() -> list:
    """Load COMPLETE outcome rows; try alpha_shadow_log for extra component data."""
    from database import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT o.*,
                   s.component_scores_json AS shadow_component_json
            FROM alpha_outcomes o
            LEFT JOIN alpha_shadow_log s
                   ON o.ticker = s.ticker AND o.scan_time = s.scan_time
            WHERE o.status = 'COMPLETE'
            ORDER BY o.scan_time DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("alpha_learning_engine: _load_complete_outcomes failed", exc_info=True)
        return []
    finally:
        conn.close()


def _parse_component_scores(row: dict) -> dict:
    """Extract component scores dict from a row. Returns {} on any failure."""
    for key in ("component_scores_json", "shadow_component_json"):
        raw = row.get(key)
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and parsed:
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
    return {}


def _safe_mean(values: list) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 6) if vals else None


def _safe_rate(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator, 4) if denominator > 0 else None


def _shrinkage_factor(n: int) -> float:
    """Linear interpolation: 0.0 at n=0, 1.0 at n=_SHRINKAGE_N."""
    return min(1.0, max(0.0, n / _SHRINKAGE_N))


def _confidence_level(n: int) -> str:
    if n >= _SHRINKAGE_N:
        return "HIGH"
    if n >= _MIN_SAMPLES:
        return "MEDIUM"
    return "LOW"


def _classify_shadow_tier(
    score: float,
    shadow_thresholds: Optional[dict] = None,
) -> str:
    """Numeric-only tier classification (no gate checks) for shadow replay."""
    thresholds = shadow_thresholds or _CURRENT_TIER_THRESHOLDS
    for tier, threshold in sorted(thresholds.items(), key=lambda x: -x[1]):
        if score >= threshold:
            return tier
    return "IGNORE"


def _compute_shadow_weights(weight_recs: list) -> dict:
    """
    Apply shrunk weight deltas from recommendations to current weights.
    Renormalises so result sums to 1.0.  Clamps each delta to ±_DELTA_CAP.
    Returns current weights unchanged when no recs have non-zero deltas.
    """
    delta_map: dict = {}
    for rec in weight_recs:
        comp        = rec.get("component")
        shrunk_delta = rec.get("shrunk_delta", 0.0)
        if comp and comp in _CURRENT_WEIGHTS:
            clamped = max(-_DELTA_CAP, min(_DELTA_CAP, shrunk_delta))
            delta_map[comp] = clamped

    raw = {
        comp: max(0.001, _CURRENT_WEIGHTS[comp] + delta_map.get(comp, 0.0))
        for comp in _CURRENT_WEIGHTS
    }
    total = sum(raw.values())
    return {comp: round(w / total, 6) for comp, w in raw.items()}


# ── Validation integration ────────────────────────────────────────────────────

def _load_validations() -> dict:
    """
    Load alpha_validation rows and return a dict keyed by outcome_id.

    Returns {} when the table is empty or missing (safe — callers treat
    validation as optional evidence, not a hard requirement).
    """
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT outcome_id, behavior_class, validation_score FROM alpha_validation"
            ).fetchall()
            return {r["outcome_id"]: dict(r) for r in rows}
        finally:
            conn.close()
    except Exception:
        log.warning("alpha_learning_engine: _load_validations failed", exc_info=True)
        return {}


def compute_validated_component_effectiveness(outcomes: list, validations: dict) -> dict:
    """
    Like compute_component_effectiveness but weights each outcome by its
    validation quality weight.

    POSITIVE_BEHAVIORS get 1.3-1.5x, NEGATIVE_BEHAVIORS get 0.4x,
    MIXED_BEHAVIORS get 0.75x, INCONCLUSIVE stays 1.0x.

    Falls back to compute_component_effectiveness when validations is empty.
    """
    if not validations:
        return compute_component_effectiveness(outcomes)

    from alpha_validation import VALIDATION_QUALITY_WEIGHTS

    if not outcomes:
        return {}

    all_r5 = [r.get("return_5d") for r in outcomes if r.get("return_5d") is not None]
    baseline_wins     = sum(1 for r in all_r5 if r > _WIN_THRESHOLD)
    baseline_win_rate = baseline_wins / len(all_r5) if all_r5 else 0.0

    per_comp: dict = {name: {
        "high_weighted_wins": 0.0, "high_weighted_fp": 0.0, "high_weighted_n": 0.0,
        "high_returns_weighted": [], "high_gains_weighted": [],
        "low_weighted_wins": 0.0, "low_weighted_n": 0.0, "low_returns_weighted": [],
        "active_n": 0, "missing_n": 0, "total_n": 0,
    } for name in _CURRENT_WEIGHTS}

    for row in outcomes:
        r5d      = row.get("return_5d")
        mg       = row.get("max_gain")
        cs       = _parse_component_scores(row)
        outcome_id = row.get("id")

        v = validations.get(outcome_id)
        quality_weight = VALIDATION_QUALITY_WEIGHTS.get(
            v["behavior_class"] if v else "INCONCLUSIVE", 1.0
        )

        for comp, b in per_comp.items():
            b["total_n"] += 1
            comp_data = cs.get(comp, {})
            score     = comp_data.get("score")
            dq        = comp_data.get("data_quality", "MISSING")

            if dq == "MISSING" or score is None:
                b["missing_n"] += 1
                continue

            b["active_n"] += 1

            if score >= _HIGH_SCORE_THRESHOLD:
                b["high_weighted_n"] += quality_weight
                if r5d is not None:
                    b["high_returns_weighted"].append((r5d, quality_weight))
                    if r5d > _WIN_THRESHOLD:
                        b["high_weighted_wins"] += quality_weight
                    if r5d < _FP_THRESHOLD:
                        b["high_weighted_fp"] += quality_weight
                if mg is not None:
                    b["high_gains_weighted"].append((mg, quality_weight))
            else:
                b["low_weighted_n"] += quality_weight
                if r5d is not None:
                    b["low_returns_weighted"].append((r5d, quality_weight))
                    if r5d > _WIN_THRESHOLD:
                        b["low_weighted_wins"] += quality_weight

    def _wmean(pairs: list) -> Optional[float]:
        if not pairs:
            return None
        total_w = sum(w for _, w in pairs)
        if total_w < 1e-9:
            return None
        return round(sum(v * w for v, w in pairs) / total_w, 6)

    def _wrate(n: float, d: float) -> Optional[float]:
        return round(n / d, 4) if d > 1e-9 else None

    result: dict = {}
    for comp, b in per_comp.items():
        n      = b["total_n"]
        hw_n   = b["high_weighted_n"]
        hr_w   = b["high_returns_weighted"]
        wwr_h  = _wrate(b["high_weighted_wins"], hw_n)
        wwr_l  = _wrate(b["low_weighted_wins"],  b["low_weighted_n"])
        lift: Optional[float] = None
        if wwr_h is not None and baseline_win_rate > 0:
            lift = round(wwr_h / baseline_win_rate, 4)

        result[comp] = {
            "active_count":                    b["active_n"],
            "high_count":                      int(b["high_weighted_n"]),
            "missing_rate":                    _safe_rate(b["missing_n"], n),
            "win_rate_when_high":              wwr_h,
            "win_rate_when_low":               wwr_l,
            "avg_return_when_high":            _wmean(hr_w),
            "avg_return_when_low":             _wmean(b["low_returns_weighted"]),
            "max_gain_when_high":              _wmean(b["high_gains_weighted"]),
            "false_positive_rate_when_high":   _wrate(b["high_weighted_fp"], hw_n),
            "lift":                            lift,
            "data_quality_penalty":            _safe_rate(b["missing_n"], n),
            "baseline_win_rate":               round(baseline_win_rate, 4),
            "validation_weighted":             True,
        }

    return result


# ── Core analytics ─────────────────────────────────────────────────────────────

def compute_component_effectiveness(outcomes: list) -> dict:
    """
    For each scoring component, compute effectiveness stats from COMPLETE outcomes.

    A component is "high" when its score >= _HIGH_SCORE_THRESHOLD.
    Lift = win_rate_when_high / baseline_win_rate.

    Returns dict keyed by component name.
    """
    if not outcomes:
        return {}

    # Baseline win rate across all complete outcomes with return_5d
    all_r5 = [r.get("return_5d") for r in outcomes if r.get("return_5d") is not None]
    baseline_wins     = sum(1 for r in all_r5 if r > _WIN_THRESHOLD)
    baseline_win_rate = baseline_wins / len(all_r5) if all_r5 else 0.0

    per_comp: dict = {name: {
        "high_returns": [], "low_returns": [],
        "high_gains": [],
        "high_wins": 0, "high_fp": 0, "high_n": 0,
        "low_wins": 0,  "low_n": 0,
        "active_n": 0,  "missing_n": 0, "total_n": 0,
    } for name in _CURRENT_WEIGHTS}

    for row in outcomes:
        r5d = row.get("return_5d")
        mg  = row.get("max_gain")
        cs  = _parse_component_scores(row)

        for comp, b in per_comp.items():
            b["total_n"] += 1
            comp_data = cs.get(comp, {})
            score     = comp_data.get("score")
            dq        = comp_data.get("data_quality", "MISSING")

            if dq == "MISSING" or score is None:
                b["missing_n"] += 1
                continue

            b["active_n"] += 1

            if score >= _HIGH_SCORE_THRESHOLD:
                b["high_n"] += 1
                if r5d is not None:
                    b["high_returns"].append(r5d)
                    if r5d > _WIN_THRESHOLD:
                        b["high_wins"] += 1
                    if r5d < _FP_THRESHOLD:
                        b["high_fp"] += 1
                if mg is not None:
                    b["high_gains"].append(mg)
            else:
                b["low_n"] += 1
                if r5d is not None:
                    b["low_returns"].append(r5d)
                    if r5d > _WIN_THRESHOLD:
                        b["low_wins"] += 1

    result: dict = {}
    for comp, b in per_comp.items():
        n              = b["total_n"]
        hr             = b["high_returns"]
        win_rate_high  = _safe_rate(b["high_wins"], len(hr))
        win_rate_low   = _safe_rate(b["low_wins"],  len(b["low_returns"]))
        lift: Optional[float] = None
        if win_rate_high is not None and baseline_win_rate > 0:
            lift = round(win_rate_high / baseline_win_rate, 4)

        result[comp] = {
            "active_count":                    b["active_n"],
            "high_count":                      b["high_n"],
            "missing_rate":                    _safe_rate(b["missing_n"], n),
            "win_rate_when_high":              win_rate_high,
            "win_rate_when_low":               win_rate_low,
            "avg_return_when_high":            _safe_mean(hr),
            "avg_return_when_low":             _safe_mean(b["low_returns"]),
            "max_gain_when_high":              _safe_mean(b["high_gains"]),
            "false_positive_rate_when_high":   _safe_rate(b["high_fp"], len(hr)),
            "lift":                            lift,
            "data_quality_penalty":            _safe_rate(b["missing_n"], n),
            "baseline_win_rate":               round(baseline_win_rate, 4),
        }

    return result


def compute_setup_effectiveness(outcomes: list) -> dict:
    """
    Per-setup-type effectiveness from COMPLETE outcomes.

    best_window = whichever of 5d/10d/20d has highest avg return.
    """
    if not outcomes:
        return {}

    buckets: dict = defaultdict(lambda: {
        "r5": [], "r10": [], "r20": [], "drawdowns": [],
        "wins": 0, "fp": 0, "count": 0,
    })

    for row in outcomes:
        setup = row.get("setup_type") or "UNKNOWN"
        r5    = row.get("return_5d")
        r10   = row.get("return_10d")
        r20   = row.get("return_20d")
        dd    = row.get("max_drawdown")
        b     = buckets[setup]
        b["count"] += 1
        if r5 is not None:
            b["r5"].append(r5)
            if r5 > _WIN_THRESHOLD:
                b["wins"] += 1
            if r5 < _FP_THRESHOLD:
                b["fp"] += 1
        if r10 is not None:
            b["r10"].append(r10)
        if r20 is not None:
            b["r20"].append(r20)
        if dd is not None:
            b["drawdowns"].append(dd)

    result: dict = {}
    for setup, b in buckets.items():
        avg5  = _safe_mean(b["r5"])
        avg10 = _safe_mean(b["r10"])
        avg20 = _safe_mean(b["r20"])

        window_avgs = {w: v for w, v in (("5d", avg5), ("10d", avg10), ("20d", avg20)) if v is not None}
        best_window = max(window_avgs, key=lambda k: window_avgs[k]) if window_avgs else None

        result[setup] = {
            "count":               b["count"],
            "win_rate":            _safe_rate(b["wins"], len(b["r5"])),
            "avg_return_5d":       avg5,
            "avg_return_10d":      avg10,
            "avg_return_20d":      avg20,
            "avg_drawdown":        _safe_mean(b["drawdowns"]),
            "false_positive_rate": _safe_rate(b["fp"], len(b["r5"])),
            "best_window":         best_window,
        }

    return result


def compute_tier_calibration(outcomes: list) -> dict:
    """
    Analyze whether each tier is well-calibrated from COMPLETE outcomes.

    Returns dict keyed by tier name; includes assessment list.
    """
    if not outcomes:
        return {}

    buckets: dict = defaultdict(lambda: {"returns": [], "wins": 0, "fp": 0, "count": 0})

    for row in outcomes:
        tier = row.get("alpha_tier") or "UNKNOWN"
        r5   = row.get("return_5d")
        b    = buckets[tier]
        b["count"] += 1
        if r5 is not None:
            b["returns"].append(r5)
            if r5 > _WIN_THRESHOLD:
                b["wins"] += 1
            if r5 < _FP_THRESHOLD:
                b["fp"] += 1

    result: dict = {}
    for tier, b in buckets.items():
        n      = b["count"]
        rn     = len(b["returns"])
        fp     = _safe_rate(b["fp"], rn)
        wr     = _safe_rate(b["wins"], rn)
        avg_r  = _safe_mean(b["returns"])
        assessment = []

        if fp is not None and fp > _TIER_FP_LOOSE:
            assessment.append(
                f"TOO_LOOSE: false-positive rate {fp:.0%} exceeds {_TIER_FP_LOOSE:.0%} threshold"
            )
        if n < 10 and tier in ("HIGH_CONVICTION", "RARE_SETUP"):
            assessment.append(
                f"TOO_RARE: only {n} outcomes — insufficient data to calibrate this tier"
            )
        if wr is not None and tier == "HIGH_CONVICTION" and wr < 0.45:
            assessment.append(
                f"UNDERPREDICTIVE: win rate {wr:.0%} below 45% expected for HIGH_CONVICTION"
            )
        if not assessment:
            assessment.append("OK")

        result[tier] = {
            "count":               n,
            "win_rate":            wr,
            "avg_return_5d":       avg_r,
            "false_positive_rate": fp,
            "assessment":          assessment,
        }

    return result


# ── Recommendation engine ─────────────────────────────────────────────────────

def recommend_weights(component_eff: dict) -> list:
    """
    Produce weight recommendations from component effectiveness data.

    Safety guarantees:
    - _MIN_SAMPLES required for any non-KEEP recommendation
    - Raw delta clamped to ±_DELTA_CAP
    - Shrinkage toward 0 when n < _SHRINKAGE_N
    - Never mutates _CURRENT_WEIGHTS
    - Sorted: INCREASE → DECREASE → KEEP

    Returns list of recommendation dicts.
    """
    recs = []

    for comp, eff in component_eff.items():
        current_weight = _CURRENT_WEIGHTS.get(comp, 0.0)
        lift           = eff.get("lift")
        high_count     = eff.get("high_count", 0)
        missing_rate   = eff.get("missing_rate") or 0.0
        fp_rate        = eff.get("false_positive_rate_when_high") or 0.0
        wr_high        = eff.get("win_rate_when_high")

        if high_count < _MIN_SAMPLES:
            recs.append({
                "component":      comp,
                "action":         "KEEP",
                "current_weight": round(current_weight, 4),
                "raw_delta":      0.0,
                "shrunk_delta":   0.0,
                "confidence":     "LOW",
                "reason":         (
                    f"Insufficient samples: {high_count} high-score rows "
                    f"(need {_MIN_SAMPLES})"
                ),
                "risk":           "Cannot assess component effectiveness without more data",
                "sample_size":    high_count,
            })
            continue

        sf = _shrinkage_factor(high_count)

        if lift is None:
            action    = "KEEP"
            raw_delta = 0.0
            reason    = "Lift could not be computed (no baseline win rate)"
            risk      = "No actionable signal"

        elif lift >= _LIFT_INCREASE_THRESHOLD:
            raw_delta = _DELTA_CAP
            if missing_rate > 0.50:
                raw_delta *= (1.0 - missing_rate)
            action = "INCREASE"
            wr_str = f"{wr_high:.0%}" if wr_high is not None else "N/A"
            reason = (
                f"Lift {lift:.2f}x baseline — high-score rows outperform significantly; "
                f"win rate {wr_str}"
            )
            if fp_rate > 0.30:
                risk = f"Warning: FP rate {fp_rate:.0%} is elevated — increase with caution"
            else:
                risk = "Higher weight increases sensitivity to this component"

        elif lift <= _LIFT_DECREASE_THRESHOLD:
            raw_delta = -_DELTA_CAP
            action    = "DECREASE"
            wr_str    = f"{wr_high:.0%}" if wr_high is not None else "N/A"
            reason    = (
                f"Lift {lift:.2f}x baseline — high-score rows underperform; "
                f"win rate {wr_str}"
            )
            risk      = "Reducing weight may miss some true positives in edge cases"

        else:
            raw_delta = 0.0
            action    = "KEEP"
            reason    = (
                f"Lift {lift:.2f}x baseline — within acceptable range "
                f"[{_LIFT_DECREASE_THRESHOLD}–{_LIFT_INCREASE_THRESHOLD}]"
            )
            risk = "No action required"

        shrunk_delta = round(max(-_DELTA_CAP, min(_DELTA_CAP, raw_delta * sf)), 6)
        confidence   = _confidence_level(high_count)

        recs.append({
            "component":      comp,
            "action":         action,
            "current_weight": round(current_weight, 4),
            "raw_delta":      round(raw_delta, 6),
            "shrunk_delta":   shrunk_delta,
            "confidence":     confidence,
            "reason":         reason,
            "risk":           risk,
            "sample_size":    high_count,
        })

    _order = {"INCREASE": 0, "DECREASE": 1, "KEEP": 2}
    recs.sort(key=lambda r: (_order.get(r["action"], 3), -r["sample_size"]))
    return recs


def recommend_thresholds(tier_calibration: dict) -> list:
    """
    Produce tier threshold recommendations from calibration.

    Returns list of recommendation dicts.
    """
    recs = []

    for tier, cal in tier_calibration.items():
        threshold = _CURRENT_TIER_THRESHOLDS.get(tier)
        if threshold is None:
            continue

        n          = cal.get("count", 0)
        assessment = cal.get("assessment", [])
        fp         = cal.get("false_positive_rate")
        confidence = _confidence_level(n)

        if any("TOO_LOOSE" in a for a in assessment):
            recs.append({
                "tier":              tier,
                "action":            "TIGHTEN",
                "current_threshold": threshold,
                "suggested_delta":   +2.5,
                "confidence":        confidence,
                "reason":            (
                    f"False-positive rate {fp:.0%} exceeds {_TIER_FP_LOOSE:.0%} threshold"
                    if fp is not None else "Too-loose diagnosis from calibration"
                ),
                "risk":              "Tightening may exclude some genuine winners near the boundary",
            })
        elif any("TOO_RARE" in a for a in assessment) and tier in ("HIGH_CONVICTION", "RARE_SETUP"):
            recs.append({
                "tier":              tier,
                "action":            "LOOSEN",
                "current_threshold": threshold,
                "suggested_delta":   -2.5,
                "confidence":        "LOW",
                "reason":            f"Only {n} outcomes observed — threshold may be too restrictive",
                "risk":              "Lowering threshold risks admitting lower-quality setups",
            })
        else:
            recs.append({
                "tier":              tier,
                "action":            "KEEP",
                "current_threshold": threshold,
                "suggested_delta":   0.0,
                "confidence":        confidence,
                "reason":            f"Tier appears calibrated (n={n})",
                "risk":              "No action required",
            })

    return recs


# ── Shadow policy replay ───────────────────────────────────────────────────────

def generate_shadow_policy_replay(shadow_weights: dict, outcomes: list) -> dict:
    """
    Replay COMPLETE outcomes using shadow weights and compare tiers.

    expected_fp_reduction        = fraction of old FPs that would be downgraded
    expected_missed_winner_risk  = fraction of old winners that would be downgraded
    changed_candidates           = rows where shadow_tier != old_tier (capped at 50)
    """
    empty = {
        "total_replayed": 0,
        "tier_unchanged": 0,
        "tier_upgraded":  0,
        "tier_downgraded": 0,
        "expected_fp_reduction":        None,
        "expected_missed_winner_risk":  None,
        "changed_candidates":           [],
    }
    if not outcomes:
        return empty

    unchanged = upgraded = downgraded = 0
    old_fps = old_wins = fp_caught = win_lost = 0
    changed: list = []

    for row in outcomes:
        cs = _parse_component_scores(row)
        if not cs:
            continue

        # Recompute score under shadow weights
        shadow_score = round(
            sum(
                info.get("score", 0.0) * shadow_weights.get(comp, _CURRENT_WEIGHTS.get(comp, 0.0))
                for comp, info in cs.items()
            ) * 10.0,
            2,
        )

        old_tier    = row.get("alpha_tier") or "IGNORE"
        shadow_tier = _classify_shadow_tier(shadow_score)
        r5d         = row.get("return_5d")

        old_rank = _TIER_RANK.get(old_tier, 0)
        new_rank = _TIER_RANK.get(shadow_tier, 0)

        # Track FP / win counts in old system (acted on = STRONG_WATCH+)
        if old_rank >= _ELIGIBLE_TIER_RANK:
            if r5d is not None and r5d < _FP_THRESHOLD:
                old_fps += 1
            if r5d is not None and r5d > _WIN_THRESHOLD:
                old_wins += 1

        if new_rank > old_rank:
            upgraded  += 1
        elif new_rank < old_rank:
            downgraded += 1
            if old_rank >= _ELIGIBLE_TIER_RANK:
                if r5d is not None and r5d < _FP_THRESHOLD:
                    fp_caught += 1
                if r5d is not None and r5d > _WIN_THRESHOLD:
                    win_lost  += 1
        else:
            unchanged += 1

        if old_tier != shadow_tier:
            changed.append({
                "ticker":       row.get("ticker"),
                "scan_time":    row.get("scan_time"),
                "old_tier":     old_tier,
                "shadow_tier":  shadow_tier,
                "old_score":    row.get("alpha_score"),
                "shadow_score": shadow_score,
                "return_5d":    r5d,
                "is_winner":    (r5d > _WIN_THRESHOLD)   if r5d is not None else None,
                "is_fp":        (r5d < _FP_THRESHOLD)    if r5d is not None else None,
            })

    total = unchanged + upgraded + downgraded
    return {
        "total_replayed":             total,
        "tier_unchanged":             unchanged,
        "tier_upgraded":              upgraded,
        "tier_downgraded":            downgraded,
        "expected_fp_reduction":      round(fp_caught / old_fps,  4) if old_fps  > 0 else None,
        "expected_missed_winner_risk": round(win_lost  / old_wins, 4) if old_wins > 0 else None,
        "changed_candidates":         changed[:50],
    }


# ── Public report generators ───────────────────────────────────────────────────

def generate_recommendations_report() -> dict:
    """
    Full shadow weight recommendations report.

    Never raises.  Never mutates live scoring weights.  Never sends alerts.
    """
    errors: list = []

    outcomes: list = []
    try:
        outcomes = _load_complete_outcomes()
    except Exception as exc:
        errors.append(f"load_outcomes error: {exc}")

    n = len(outcomes)
    sample_size_warning: Optional[str] = None
    if n == 0:
        sample_size_warning = (
            "No COMPLETE outcomes yet — recommendations require at least "
            f"{_MIN_SAMPLES} complete rows"
        )
    elif n < _MIN_SAMPLES:
        sample_size_warning = (
            f"Only {n} COMPLETE outcomes — all recommendations have LOW confidence; "
            f"need {_MIN_SAMPLES}+"
        )

    validations: dict = {}
    try:
        validations = _load_validations()
    except Exception as exc:
        errors.append(f"load_validations error: {exc}")

    component_eff: dict = {}
    validation_weighted = False
    try:
        if validations:
            component_eff = compute_validated_component_effectiveness(outcomes, validations)
            validation_weighted = True
        else:
            component_eff = compute_component_effectiveness(outcomes)
    except Exception as exc:
        errors.append(f"component_effectiveness error: {exc}")

    setup_eff: dict = {}
    try:
        setup_eff = compute_setup_effectiveness(outcomes)
    except Exception as exc:
        errors.append(f"setup_effectiveness error: {exc}")

    tier_cal: dict = {}
    try:
        tier_cal = compute_tier_calibration(outcomes)
    except Exception as exc:
        errors.append(f"tier_calibration error: {exc}")

    weight_recs: list = []
    try:
        weight_recs = recommend_weights(component_eff)
    except Exception as exc:
        errors.append(f"weight_recommendations error: {exc}")

    threshold_recs: list = []
    try:
        threshold_recs = recommend_thresholds(tier_cal)
    except Exception as exc:
        errors.append(f"threshold_recommendations error: {exc}")

    top_changes = [
        r for r in weight_recs
        if r["action"] != "KEEP" and r["confidence"] in ("HIGH", "MEDIUM")
    ]

    return {
        "generated_at":              datetime.now().isoformat(),
        "note":                      "Shadow mode only — no live weight changes applied automatically",
        "total_complete_outcomes":   n,
        "total_validations":         len(validations),
        "validation_weighted":       validation_weighted,
        "sample_size_warning":       sample_size_warning,
        "errors":                    errors,
        "current_weights":           dict(_CURRENT_WEIGHTS),
        "current_tier_thresholds":   dict(_CURRENT_TIER_THRESHOLDS),
        "component_effectiveness":   component_eff,
        "setup_effectiveness":       setup_eff,
        "tier_calibration":          tier_cal,
        "weight_recommendations":    weight_recs,
        "threshold_recommendations": threshold_recs,
        "top_changes":               top_changes,
    }


def generate_shadow_policy_report() -> dict:
    """
    Shadow policy simulation: apply recommended weights and replay past outcomes.

    Never raises.  Never affects live scoring.
    """
    errors: list = []

    outcomes: list = []
    try:
        outcomes = _load_complete_outcomes()
    except Exception as exc:
        errors.append(f"load_outcomes error: {exc}")

    component_eff: dict = {}
    try:
        component_eff = compute_component_effectiveness(outcomes)
    except Exception as exc:
        errors.append(f"component_effectiveness error: {exc}")

    weight_recs: list = []
    try:
        weight_recs = recommend_weights(component_eff)
    except Exception as exc:
        errors.append(f"weight_recommendations error: {exc}")

    shadow_weights: dict = dict(_CURRENT_WEIGHTS)
    try:
        shadow_weights = _compute_shadow_weights(weight_recs)
    except Exception as exc:
        errors.append(f"shadow_weights error: {exc}")

    replay: dict = {}
    try:
        replay = generate_shadow_policy_replay(shadow_weights, outcomes)
    except Exception as exc:
        errors.append(f"shadow_replay error: {exc}")
        replay = {"total_replayed": 0}

    return {
        "generated_at":  datetime.now().isoformat(),
        "note":          "Shadow policy simulation — no live changes applied",
        "errors":        errors,
        "current_weights": dict(_CURRENT_WEIGHTS),
        "shadow_weights":  shadow_weights,
        "weight_deltas": {
            comp: round(
                shadow_weights.get(comp, 0.0) - _CURRENT_WEIGHTS.get(comp, 0.0), 6
            )
            for comp in _CURRENT_WEIGHTS
        },
        "shadow_weights_sum_to_one": abs(sum(shadow_weights.values()) - 1.0) < 1e-4,
        "replay_stats":  replay,
    }
