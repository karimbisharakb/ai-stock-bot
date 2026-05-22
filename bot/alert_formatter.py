"""
Unified institutional alert formatter (Phase N1).

Replaces:
  - predator._format_alert()          "🎯 PRE-EXPLOSION ALERT" / "🚀 CONVICTION"
  - scanner._send_alert()             "🔍 HIDDEN GEM ALERT"
  - scanner score/10 raw additive presentation

Shared utilities for tier resolution, confidence resolution, and
suppression reason interpretation are also here so API / dashboard /
notifications always read from the same functions.
"""
from __future__ import annotations
from typing import Optional
from alert_schema import AlertCandidate, EligibilityResult

_DIV = "─" * 44

_TIER_EMOJI = {"CONVICTION": "🔥", "ALERT": "⚡", "WATCH": "👁"}
_TIER_LABEL = {"CONVICTION": "CONVICTION", "ALERT": "ALERT", "WATCH": "WATCH"}

_REGIME_DESC = {
    "BULL":    "bull market",
    "BEAR":    "bear market — signals penalised",
    "NEUTRAL": "neutral regime",
}


# ── Shared resolvers ───────────────────────────────────────────────────────────

def resolve_tier(
    raw_score: Optional[float],
    confidence_pct: Optional[float],
    active_signal_count: int,
    adjusted_score: Optional[float],
) -> str:
    """Shared tier resolver — delegates to predator.classify_tier() for parity.

    Returns "WATCH" on any import or value error so callers stay safe.
    """
    try:
        from predator import classify_tier
        raw  = int(raw_score)        if raw_score        is not None else 0
        adj  = float(adjusted_score) if adjusted_score   is not None else 0.0
        conf = float(confidence_pct) if confidence_pct   is not None else 0.0
        return classify_tier(raw, adj, conf, active_signal_count)
    except Exception:
        return "WATCH"


def resolve_confidence(signals: dict) -> float:
    """Shared confidence resolver — delegates to predator.compute_confidence()."""
    try:
        from predator import compute_confidence
        return compute_confidence(signals)
    except Exception:
        return 0.0


def interpret_suppression_reasons(
    *,
    snoozed: bool         = False,
    quiet_hours: bool     = False,
    duplicate: bool       = False,
    tier_below_min: bool  = False,
    tier_label: str       = "",
    min_tier_label: str   = "",
    stale: bool           = False,
    rate_limited: bool    = False,
    extra: Optional[list[str]] = None,
) -> list[str]:
    """Return human-readable suppression reasons."""
    reasons: list[str] = []
    if snoozed:
        reasons.append("ticker snoozed by operator")
    if quiet_hours:
        reasons.append("quiet hours active (22:00–07:00 ET)")
    if duplicate:
        reasons.append("duplicate within 24 h window")
    if tier_below_min:
        reasons.append(f"tier {tier_label!r} below minimum {min_tier_label!r}")
    if stale:
        reasons.append("candidate data is stale")
    if rate_limited:
        reasons.append("outbound rate limit reached")
    if extra:
        reasons.extend(extra)
    return reasons


# ── Predator / scanner formatter ───────────────────────────────────────────────

def format_predator_alert(
    candidate: AlertCandidate,
    eligibility: EligibilityResult,
) -> str:
    """Plain-English buy/scanner alert. Delegates to Phase N2 formatter."""
    from alert_formatter_n2 import format_buy_alert
    return format_buy_alert(candidate, eligibility)


# ── Sell-monitor formatter ─────────────────────────────────────────────────────

def format_sell_alert_unified(
    candidate: AlertCandidate,
    eligibility: EligibilityResult,
    shares: float,
    avg_cost: float,
) -> str:
    """Plain-English sell alert. Delegates to Phase N2 formatter."""
    from alert_formatter_n2 import format_sell_alert
    return format_sell_alert(candidate, eligibility, shares, avg_cost)


# ── Dispatch ───────────────────────────────────────────────────────────────────

def format_alert(
    candidate: AlertCandidate,
    eligibility: EligibilityResult,
    **kwargs,
) -> str:
    """Route to the correct formatter by source."""
    if candidate.source == "sell_monitor":
        return format_sell_alert_unified(
            candidate, eligibility,
            shares=kwargs.get("shares", 0.0),
            avg_cost=kwargs.get("avg_cost", 0.0),
        )
    return format_predator_alert(candidate, eligibility)
