"""
Canonical schema for the unified notification gateway (Phase N1).

All notification flow passes through these typed structures.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AlertCandidate:
    """Normalized input to the alert gateway from any source system."""
    source: str                          # "predator" | "scanner" | "sell_monitor"
    ticker: str
    raw_score: Optional[float]           # additive signal sum — informational only
    adjusted_score: Optional[float]      # raw_score × (confidence / 100)
    confidence_pct: Optional[float]      # [0, 100]
    tier: Optional[str]                  # "WATCH" | "ALERT" | "CONVICTION" | None
    regime: Optional[str]                # "BULL" | "BEAR" | "NEUTRAL" | None
    active_signals: list[str]            # names of signals that fired
    suppressed_signals: list[str]        # names of signals suppressed by regime penalty
    urgency: Optional[str]               # sell_monitor only: "URGENT" | "WARNING" | "FYI"
    entry_price: Optional[float]
    stop_price: Optional[float]
    position_size_cad: Optional[float]
    risk_posture: Optional[str]          # "High" | "Medium" | "Low"
    metadata: dict = field(default_factory=dict)


@dataclass
class EligibilityResult:
    """Deterministic output of check_eligibility(). Pure — no side effects."""
    eligible: bool
    resolved_tier: str                   # final tier after resolution
    adjusted_score: float                # resolved value (0.0 when input is None)
    confidence_pct: float                # resolved value (0.0 when input is None)
    reasons: list[str]                   # why eligible
    suppression_reasons: list[str]       # why suppressed (empty if eligible)
    trigger_reason: str                  # human-readable why-triggered summary


@dataclass
class OutboundAlert:
    """Record of an alert that was (or would be) sent."""
    alert_id: str
    ticker: str
    source: str
    tier: str
    adjusted_score: float
    confidence_pct: float
    raw_score: Optional[float]
    active_signals: list[str]
    suppressed_signals: list[str]
    regime_context: Optional[str]
    risk_posture: Optional[str]
    trigger_reason: str
    formatted_message: str
    dry_run: bool
    shadow: bool
    sent_at: Optional[str]              # ISO datetime or None
    delivered: bool = False


@dataclass
class SuppressedAlert:
    """Record of a candidate that was evaluated but not sent."""
    ticker: str
    source: str
    suppression_reasons: list[str]
    resolved_tier: str
    adjusted_score: float
    confidence_pct: float
    evaluated_at: str                   # ISO datetime
