"""
Phase A7 — Alpha alert candidate gate.

Deterministic gate that scores each Alpha candidate for alert readiness
using alpha score/tier, validation history, setup trap rates, component
data quality, and recent alert history.

Observation-only: never sends WhatsApp alerts, never mutates live scoring,
never affects Predator.

Public API:
  score_readiness(candidate, context)  -> dict   (pure — no DB)
  get_alert_candidates(limit)          -> list
  get_alert_gate_summary()             -> dict
"""
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# ── Readiness tiers ────────────────────────────────────────────────────────────

READINESS_TIERS = ("NOT_READY", "MONITOR", "PRE_ALERT", "ALERT_READY", "RARE_ALERT")

_READINESS_TIER_RANK: dict = {
    "NOT_READY":   0,
    "MONITOR":     1,
    "PRE_ALERT":   2,
    "ALERT_READY": 3,
    "RARE_ALERT":  4,
}

# Each Alpha tier caps the maximum readiness tier achievable
_TIER_MAX_READINESS: dict = {
    "IGNORE":          "NOT_READY",
    "WATCH":           "MONITOR",
    "STRONG_WATCH":    "PRE_ALERT",
    "HIGH_CONVICTION": "ALERT_READY",
    "RARE_SETUP":      "RARE_ALERT",
}

# Score thresholds for readiness tiers (score >= value → tier)
_READINESS_SCORE_THRESHOLDS: list = [
    (85.0, "RARE_ALERT"),
    (70.0, "ALERT_READY"),
    (55.0, "PRE_ALERT"),
    (35.0, "MONITOR"),
    ( 0.0, "NOT_READY"),
]

# Base readiness score range per alpha tier [low, high] at position 0.0 and 1.0 within tier
_TIER_SCORE_RANGE: dict = {
    "IGNORE":          (5.0,  22.0),
    "WATCH":           (22.0, 42.0),
    "STRONG_WATCH":    (45.0, 64.0),
    "HIGH_CONVICTION": (62.0, 78.0),
    "RARE_SETUP":      (78.0, 95.0),
}

# Alpha score bounds per tier (used to compute position within tier)
_ALPHA_TIER_BOUNDS: dict = {
    "IGNORE":          (0.0,  35.0),
    "WATCH":           (35.0, 50.0),
    "STRONG_WATCH":    (50.0, 65.0),
    "HIGH_CONVICTION": (65.0, 80.0),
    "RARE_SETUP":      (80.0, 100.0),
}

# ── Gate constants ─────────────────────────────────────────────────────────────

_HIGH_TRAP_RATE          = 0.40   # setup trap rate > this requires extra confirmation
_TRAP_PENALTY_SCALE      = 40.0   # penalty = (rate - threshold) * scale (max ~24 at 100%)
_MISSING_COMPONENT_PEN   = 3.0    # penalty per missing component
_VALIDATION_POSITIVE_BOOST = 6.0  # boost for SUSTAINED_TREND / VALID_BREAKOUT / INSTITUTIONAL_ACCUM
_VALIDATION_NEGATIVE_PEN  = 15.0  # penalty for VOLATILITY_TRAP / FAILED_SQUEEZE / SHORT_LIVED_SPIKE
_DUPLICATE_ALERT_PEN      = 20.0  # penalty if ticker alerted in last 24 h

_RECENT_ALERT_HOURS = 24


# ── Pure helpers ───────────────────────────────────────────────────────────────

def _score_to_readiness_tier(score: float) -> str:
    for threshold, tier in _READINESS_SCORE_THRESHOLDS:
        if score >= threshold:
            return tier
    return "NOT_READY"


def _cap_readiness_tier(tier: str, max_tier: str) -> str:
    rank     = _READINESS_TIER_RANK.get(tier, 0)
    max_rank = _READINESS_TIER_RANK.get(max_tier, 0)
    if rank <= max_rank:
        return tier
    for t, r in _READINESS_TIER_RANK.items():
        if r == max_rank:
            return t
    return "NOT_READY"


def _parse_components(candidate: dict) -> dict:
    raw = candidate.get("component_scores_json")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _tier_position(alpha_score: float, alpha_tier: str) -> float:
    """0.0–1.0 position of alpha_score within the tier's score band."""
    lo, hi = _ALPHA_TIER_BOUNDS.get(alpha_tier, (0.0, 100.0))
    if hi <= lo:
        return 0.5
    return min(1.0, max(0.0, (alpha_score - lo) / (hi - lo)))


def _build_confirmation_needs(setup_type: str, alpha_tier: str, components: dict,
                               blocking_factors: list) -> list:
    """Return list of confirmation signals needed before alerting."""
    needed = []
    setup_upper = (setup_type or "").upper()

    # Always needed when not yet in top tier
    if alpha_tier in ("WATCH", "STRONG_WATCH"):
        needed.append("relative_strength_continues")

    if "BREAKOUT" in setup_upper:
        needed.append("price_holds_breakout_level")

    if "CATALYST" in setup_upper:
        needed.append("catalyst_confirmation")

    if "OPTIONS" in setup_upper or "PRESSURE" in setup_upper:
        needed.append("options_activity_persists")

    if "SQUEEZE" in setup_upper:
        needed.append("volatility_cools_down")

    if "ACCUMULATION" in setup_upper or "EARLY" in setup_upper:
        needed.append("volume_confirmation")

    # If blockers exist add confirmations
    if any("trap rate" in b.lower() for b in blocking_factors):
        for c in ("price_holds_breakout_level", "volume_confirmation"):
            if c not in needed:
                needed.append(c)

    if any("validation" in b.lower() for b in blocking_factors):
        needed.append("risk_reward_improves")

    # Data quality gaps → general volume confirmation
    if any("missing" in b.lower() for b in blocking_factors):
        if "volume_confirmation" not in needed:
            needed.append("volume_confirmation")

    return list(dict.fromkeys(needed))  # dedup, preserve order


def _suggested_wait_window(readiness_tier: str, confirmation_needed: list) -> str:
    windows = {
        "NOT_READY":   "Tier upgrade needed — re-evaluate in 3–7 days",
        "MONITOR":     "Monitor 3–5 days for sustained performance",
        "PRE_ALERT":   "Check again in 1–2 days — confirm price holds and volume sustains",
        "ALERT_READY": "Verify within 24 hours before alerting",
        "RARE_ALERT":  "High priority — verify immediately",
    }
    base = windows.get(readiness_tier, "No guidance available")
    if confirmation_needed:
        short = confirmation_needed[0].replace("_", " ")
        base += f"; primary check: {short}"
    return base


def _build_reason(alpha_tier: str, final_tier: str, adjustments: list, capped: bool) -> str:
    parts = [f"Alpha tier {alpha_tier!r} → readiness {final_tier!r}"]
    if capped:
        parts.append(f"(capped by tier)")
    if adjustments:
        parts.append("Adjustments: " + ", ".join(adjustments[:4]))
    return "; ".join(parts)


# ── Core gate function (pure — no DB) ────────────────────────────────────────

def score_readiness(candidate: dict, context: Optional[dict] = None) -> dict:
    """
    Score a single Alpha candidate for alert readiness.

    Pure function — no DB access.  Never raises.

    Args:
        candidate: dict with alpha_score, alpha_tier, setup_type,
                   component_scores_json (JSON str), ticker, scan_time.
        context:   optional dict with:
                     trap_rates           : dict(setup_type → float)
                     validation_summary   : dict(ticker → behavior_class)
                     recent_alert_tickers : set[str]

    Returns dict with alert_ready, readiness_score, readiness_tier, etc.
    """
    try:
        return _score_readiness_inner(candidate, context or {})
    except Exception as exc:
        log.warning("alpha_alert_gate: score_readiness failed for %s: %s",
                    candidate.get("ticker"), exc, exc_info=True)
        return {
            "ticker":               candidate.get("ticker", ""),
            "alpha_score":          candidate.get("alpha_score", 0.0),
            "alpha_tier":           candidate.get("alpha_tier", "IGNORE"),
            "setup_type":           candidate.get("setup_type"),
            "readiness_score":      0.0,
            "readiness_tier":       "NOT_READY",
            "alert_ready":          False,
            "reason":               f"Gate evaluation error: {exc}",
            "blocking_factors":     [f"Gate error: {exc}"],
            "confirmation_needed":  [],
            "suggested_wait_window": "Re-evaluate after fixing gate error",
            "scan_time":            candidate.get("scan_time"),
        }


def _score_readiness_inner(candidate: dict, ctx: dict) -> dict:
    ticker      = candidate.get("ticker", "")
    alpha_tier  = candidate.get("alpha_tier") or "IGNORE"
    alpha_score = float(candidate.get("alpha_score") or 0.0)
    setup_type  = candidate.get("setup_type") or "UNKNOWN"

    # Base readiness score from tier + position within tier
    position = _tier_position(alpha_score, alpha_tier)
    r_lo, r_hi = _TIER_SCORE_RANGE.get(alpha_tier, (5.0, 22.0))
    readiness_score = r_lo + position * (r_hi - r_lo)

    adjustments: list    = []
    blocking_factors: list = []

    # ── 1. Data quality ────────────────────────────────────────────────────────
    components = _parse_components(candidate)
    if components:
        missing = [k for k, v in components.items()
                   if isinstance(v, dict) and v.get("data_quality") == "MISSING"]
        if missing:
            penalty = min(len(missing) * _MISSING_COMPONENT_PEN, 15.0)
            readiness_score -= penalty
            adjustments.append(
                f"-{penalty:.0f} (missing: {', '.join(missing[:3])})"
            )
            blocking_factors.append(
                f"Missing component data: {', '.join(missing[:3])}"
            )
    else:
        # No component info at all
        penalty = 8.0
        readiness_score -= penalty
        adjustments.append(f"-{penalty:.0f} (no component data)")
        blocking_factors.append("No component data available")

    # ── 2. Setup trap rate ─────────────────────────────────────────────────────
    trap_rates = ctx.get("trap_rates") or {}
    trap_rate  = trap_rates.get(setup_type)
    if trap_rate is not None and trap_rate > _HIGH_TRAP_RATE:
        penalty = min((trap_rate - _HIGH_TRAP_RATE) * _TRAP_PENALTY_SCALE, 25.0)
        readiness_score -= penalty
        adjustments.append(f"-{penalty:.0f} (trap rate {trap_rate:.0%} for {setup_type})")
        blocking_factors.append(
            f"High trap rate for {setup_type}: {trap_rate:.0%} > {_HIGH_TRAP_RATE:.0%}"
        )

    # ── 3. Validation history ──────────────────────────────────────────────────
    validation_summary = ctx.get("validation_summary") or {}
    behavior = validation_summary.get(ticker)
    if behavior in ("SUSTAINED_TREND", "VALID_BREAKOUT", "INSTITUTIONAL_ACCUMULATION"):
        readiness_score += _VALIDATION_POSITIVE_BOOST
        adjustments.append(f"+{_VALIDATION_POSITIVE_BOOST:.0f} (validated: {behavior})")
    elif behavior in ("VOLATILITY_TRAP", "FAILED_SQUEEZE", "SHORT_LIVED_SPIKE"):
        readiness_score -= _VALIDATION_NEGATIVE_PEN
        adjustments.append(f"-{_VALIDATION_NEGATIVE_PEN:.0f} (validation: {behavior})")
        blocking_factors.append(f"Recent validation: {behavior}")

    # ── 4. Recent duplicate alert ──────────────────────────────────────────────
    recent_alert_tickers = ctx.get("recent_alert_tickers") or set()
    if ticker in recent_alert_tickers:
        readiness_score -= _DUPLICATE_ALERT_PEN
        adjustments.append(f"-{_DUPLICATE_ALERT_PEN:.0f} (recent duplicate)")
        blocking_factors.append("Recent duplicate alert within last 24 hours")

    # ── 5. Clamp ───────────────────────────────────────────────────────────────
    readiness_score = max(0.0, min(100.0, readiness_score))

    # ── 6. Map score → readiness tier, then cap by alpha tier ─────────────────
    raw_tier  = _score_to_readiness_tier(readiness_score)
    max_tier  = _TIER_MAX_READINESS.get(alpha_tier, "NOT_READY")
    final_tier = _cap_readiness_tier(raw_tier, max_tier)
    capped     = _READINESS_TIER_RANK.get(final_tier, 0) < _READINESS_TIER_RANK.get(raw_tier, 0)

    if capped:
        blocking_factors.append(
            f"Alpha tier {alpha_tier!r} caps readiness at {max_tier!r}"
        )

    # ── 7. Confirmation framework ──────────────────────────────────────────────
    confirmation_needed = _build_confirmation_needs(setup_type, alpha_tier, components,
                                                    blocking_factors)

    return {
        "ticker":                ticker,
        "alpha_score":           round(alpha_score, 1),
        "alpha_tier":            alpha_tier,
        "setup_type":            setup_type,
        "readiness_score":       round(readiness_score, 1),
        "readiness_tier":        final_tier,
        "alert_ready":           final_tier in ("ALERT_READY", "RARE_ALERT"),
        "reason":                _build_reason(alpha_tier, final_tier, adjustments, capped),
        "blocking_factors":      blocking_factors,
        "confirmation_needed":   confirmation_needed,
        "suggested_wait_window": _suggested_wait_window(final_tier, confirmation_needed),
        "scan_time":             candidate.get("scan_time"),
    }


# ── DB helpers ────────────────────────────────────────────────────────────────

def _fetch_latest_candidates(limit: int) -> list:
    """One latest alpha_shadow_log row per ticker, alpha_score IS NOT NULL, score DESC."""
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT a.*
                FROM alpha_shadow_log a
                INNER JOIN (
                    SELECT ticker, MAX(scan_time) AS latest_scan
                    FROM alpha_shadow_log
                    WHERE alpha_score IS NOT NULL
                    GROUP BY ticker
                ) latest ON a.ticker = latest.ticker AND a.scan_time = latest.latest_scan
                ORDER BY a.alpha_score DESC, a.ticker ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        log.warning("alpha_alert_gate: _fetch_latest_candidates failed", exc_info=True)
        return []


def _build_gate_context() -> dict:
    """Load trap rates, validation summary, and recent alert tickers."""
    trap_rates: dict         = {}
    validation_summary: dict = {}
    recent_alert_tickers: set = set()

    try:
        from alpha_validation import get_validations, compute_setup_trap_rates
        validations = get_validations(limit=500)
        if validations:
            # Most recent validated behavior per ticker (iterate oldest→newest)
            for v in reversed(validations):
                validation_summary[v["ticker"]] = v["behavior_class"]
            trap_rates = compute_setup_trap_rates(validations)
    except Exception:
        log.warning("alpha_alert_gate: validation context load failed", exc_info=True)

    try:
        from database import get_connection
        cutoff = (datetime.now() - timedelta(hours=_RECENT_ALERT_HOURS)).isoformat()
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT ticker FROM alert_log WHERE sent_at > ?", (cutoff,)
            ).fetchall()
            recent_alert_tickers = {r["ticker"] for r in rows}
        except Exception:
            pass  # alert_log may not exist in all environments
        finally:
            conn.close()
    except Exception:
        log.warning("alpha_alert_gate: alert_log context load failed", exc_info=True)

    return {
        "trap_rates":            trap_rates,
        "validation_summary":    validation_summary,
        "recent_alert_tickers":  recent_alert_tickers,
    }


# ── Public read operations ────────────────────────────────────────────────────

def get_alert_candidates(limit: int = 50) -> list:
    """
    Score all latest Alpha candidates for alert readiness.

    Returns list sorted by readiness_score DESC.
    Never raises — returns [] on any failure.
    """
    try:
        candidates = _fetch_latest_candidates(limit)
        if not candidates:
            return []
        context = _build_gate_context()
        results = [score_readiness(c, context) for c in candidates]
        results.sort(key=lambda x: -x["readiness_score"])
        return results
    except Exception:
        log.warning("alpha_alert_gate: get_alert_candidates failed", exc_info=True)
        return []


def get_alert_gate_summary() -> dict:
    """
    Aggregate gate analytics across all current Alpha candidates.

    Never raises.  Returns safe empty structure when no data.
    """
    empty = {
        "total_evaluated":            0,
        "readiness_distribution":     {},
        "alert_ready_count":          0,
        "near_alert_count":           0,
        "top_alert_ready":            [],
        "top_blockers":               [],
        "top_confirmations_needed":   [],
        "rejected_due_to_validation": 0,
        "rejected_due_to_trap_risk":  0,
        "note":                       "No Alpha candidates found",
        "generated_at":               datetime.now().isoformat(),
    }

    try:
        candidates = get_alert_candidates(limit=100)
    except Exception as exc:
        return {**empty, "note": f"Query failed: {exc}"}

    if not candidates:
        return empty

    dist: dict = defaultdict(int)
    for c in candidates:
        dist[c["readiness_tier"]] += 1

    alert_ready = [c for c in candidates if c["alert_ready"]]
    pre_alert   = [c for c in candidates if c["readiness_tier"] == "PRE_ALERT"]

    blocker_counts: dict = defaultdict(int)
    for c in candidates:
        for b in c["blocking_factors"]:
            key = b.split(":")[0].strip()
            blocker_counts[key] += 1

    conf_counts: dict = defaultdict(int)
    for c in candidates:
        for cf in c["confirmation_needed"]:
            conf_counts[cf] += 1

    rejected_validation = sum(
        1 for c in candidates
        if any("validation" in b.lower() for b in c["blocking_factors"])
    )
    rejected_trap = sum(
        1 for c in candidates
        if any("trap rate" in b.lower() for b in c["blocking_factors"])
    )

    return {
        "total_evaluated":   len(candidates),
        "readiness_distribution": dict(dist),
        "alert_ready_count": len(alert_ready),
        "near_alert_count":  len(pre_alert),
        "top_alert_ready":   [
            {"ticker": c["ticker"], "readiness_score": c["readiness_score"],
             "alpha_tier": c["alpha_tier"], "setup_type": c["setup_type"]}
            for c in alert_ready[:5]
        ],
        "top_blockers": [
            {"factor": k, "count": v}
            for k, v in sorted(blocker_counts.items(), key=lambda x: -x[1])[:5]
        ],
        "top_confirmations_needed": [
            {"confirmation": k, "count": v}
            for k, v in sorted(conf_counts.items(), key=lambda x: -x[1])[:5]
        ],
        "rejected_due_to_validation": rejected_validation,
        "rejected_due_to_trap_risk":  rejected_trap,
        "note":         "Simulation only — no WhatsApp alerts sent",
        "generated_at": datetime.now().isoformat(),
    }
