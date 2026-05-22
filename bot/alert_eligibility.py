"""
Deterministic alert eligibility function (Phase N1).

check_eligibility() is the single source of truth for whether a candidate
should produce an outbound notification.

Pure function: same inputs → same outputs. No DB access, no side effects.
"""
from __future__ import annotations
from alert_schema import AlertCandidate, EligibilityResult
from notification_policy import NotificationPolicy

# Sources that use urgency rather than tier for eligibility
_URGENCY_SOURCES = {"sell_monitor"}

# Urgency → synthetic tier mapping
_URGENCY_TO_TIER: dict[str, str] = {
    "URGENT":  "CONVICTION",
    "WARNING": "ALERT",
    "FYI":     "WATCH",
}


def _resolve_tier(candidate: AlertCandidate) -> str:
    """Return the effective tier for eligibility comparison.

    sell_monitor: maps urgency to a synthetic tier.
    All other sources: use the candidate's pre-computed tier field.
    """
    if candidate.source in _URGENCY_SOURCES:
        urgency = (candidate.urgency or "").upper()
        return _URGENCY_TO_TIER.get(urgency, "WATCH")
    return candidate.tier or "WATCH"


def _resolve_adjusted(candidate: AlertCandidate) -> float:
    if candidate.adjusted_score is None:
        return 0.0
    return max(0.0, float(candidate.adjusted_score))


def _resolve_confidence(candidate: AlertCandidate) -> float:
    if candidate.confidence_pct is None:
        return 0.0
    return max(0.0, min(100.0, float(candidate.confidence_pct)))


def _validate(candidate: AlertCandidate) -> list[str]:
    """Return validation failure reasons (empty list = valid)."""
    issues: list[str] = []
    if not candidate.ticker or not candidate.ticker.strip():
        issues.append("missing_ticker")
    if not candidate.source:
        issues.append("missing_source")
    if candidate.raw_score is not None and not (0 <= candidate.raw_score <= 10):
        issues.append(f"invalid_raw_score:{candidate.raw_score}")
    if candidate.adjusted_score is not None and candidate.adjusted_score < 0:
        issues.append(f"negative_adjusted_score:{candidate.adjusted_score:.2f}")
    if candidate.confidence_pct is not None and not (0 <= candidate.confidence_pct <= 100):
        issues.append(f"invalid_confidence_pct:{candidate.confidence_pct:.1f}")
    return issues


def _build_trigger_reason(
    candidate: AlertCandidate,
    resolved_tier: str,
    adjusted: float,
    confidence: float,
) -> str:
    """Build a human-readable why-triggered summary."""
    if candidate.source in _URGENCY_SOURCES:
        parts = [f"urgency:{candidate.urgency}"]
        if candidate.active_signals:
            parts.append(f"signals:[{', '.join(candidate.active_signals[:3])}]")
        return " | ".join(parts)

    parts = [f"tier:{resolved_tier}"]
    if adjusted > 0:
        parts.append(f"adj_score:{adjusted:.2f}")
    if confidence > 0:
        parts.append(f"confidence:{confidence:.0f}%")
    if candidate.regime and candidate.regime != "NEUTRAL":
        parts.append(f"regime:{candidate.regime}")
    if candidate.suppressed_signals:
        parts.append(f"suppressed:[{', '.join(candidate.suppressed_signals[:2])}]")
    if candidate.active_signals:
        parts.append(f"signals:[{', '.join(candidate.active_signals[:3])}]")
    return " | ".join(parts)


def check_eligibility(
    candidate: AlertCandidate,
    policy: NotificationPolicy,
) -> EligibilityResult:
    """Determine whether a candidate qualifies for an outbound notification.

    Returns EligibilityResult with eligible=True/False and full reasoning.
    Pure: no side effects, no DB access, fully deterministic.

    Decision order:
      1. Input validation — reject malformed candidates immediately.
      2. Tier gate — resolved tier must meet policy minimum.
      3. Optional secondary gates — adjusted score and confidence floors.
      4. Build trigger reason for eligible candidates.
    """
    suppression_reasons: list[str] = []
    reasons: list[str] = []

    # 1. Validate
    validation_errors = _validate(candidate)
    if validation_errors:
        return EligibilityResult(
            eligible=False,
            resolved_tier="WATCH",
            adjusted_score=0.0,
            confidence_pct=0.0,
            reasons=[],
            suppression_reasons=validation_errors,
            trigger_reason="",
        )

    # 2. Resolve effective values
    resolved_tier = _resolve_tier(candidate)
    adjusted      = _resolve_adjusted(candidate)
    confidence    = _resolve_confidence(candidate)

    # 3. Tier gate
    if not policy.tier_meets_minimum(resolved_tier):
        suppression_reasons.append(
            f"tier_below_minimum:{resolved_tier}<{policy.min_tier}"
        )
        return EligibilityResult(
            eligible=False,
            resolved_tier=resolved_tier,
            adjusted_score=adjusted,
            confidence_pct=confidence,
            reasons=[],
            suppression_reasons=suppression_reasons,
            trigger_reason="",
        )
    reasons.append(f"tier:{resolved_tier}")
    reasons.append(f"source:{candidate.source}")

    # 4. Optional secondary gates
    if policy.min_adjusted_score > 0 and adjusted < policy.min_adjusted_score:
        suppression_reasons.append(
            f"adjusted_score_below_floor:{adjusted:.2f}<{policy.min_adjusted_score:.2f}"
        )
        return EligibilityResult(
            eligible=False,
            resolved_tier=resolved_tier,
            adjusted_score=adjusted,
            confidence_pct=confidence,
            reasons=[],
            suppression_reasons=suppression_reasons,
            trigger_reason="",
        )

    if policy.min_confidence_pct > 0 and confidence < policy.min_confidence_pct:
        suppression_reasons.append(
            f"confidence_below_floor:{confidence:.1f}%<{policy.min_confidence_pct:.1f}%"
        )
        return EligibilityResult(
            eligible=False,
            resolved_tier=resolved_tier,
            adjusted_score=adjusted,
            confidence_pct=confidence,
            reasons=[],
            suppression_reasons=suppression_reasons,
            trigger_reason="",
        )

    # 5. Eligible — build trigger reason
    trigger_reason = _build_trigger_reason(candidate, resolved_tier, adjusted, confidence)

    return EligibilityResult(
        eligible=True,
        resolved_tier=resolved_tier,
        adjusted_score=adjusted,
        confidence_pct=confidence,
        reasons=reasons,
        suppression_reasons=[],
        trigger_reason=trigger_reason,
    )
