"""
NotificationPolicy — configurable dispatch policy for the unified gateway (Phase N1).

Defaults represent the conservative, conviction-only production baseline.
All fields are overridable for testing and staged rollout.
"""
from __future__ import annotations
from dataclasses import dataclass

TIER_RANK: dict[str, int] = {"WATCH": 0, "ALERT": 1, "CONVICTION": 2}

DEFAULT_MIN_TIER          = "CONVICTION"
DEFAULT_DEDUP_WINDOW_H    = 24
DEFAULT_RATE_LIMIT        = 10          # max outbound alerts per rolling hour
DEFAULT_STALE_WINDOW_MIN  = 90          # candidates older than N min are rejected


@dataclass
class NotificationPolicy:
    """
    Declarative dispatch policy.

    min_tier            — minimum tier that may produce an outbound alert.
    min_adjusted_score  — optional secondary floor (0 = disabled).
    min_confidence_pct  — optional secondary floor (0 = disabled).
    dry_run             — evaluate eligibility, never call send_fn.
    shadow_mode         — run alongside legacy; evaluate and log, never send.
    dedup_window_hours  — duplicate suppression window per ticker per source.
    rate_limit_per_hour — max outbound alerts per rolling hour (all tickers combined).
    stale_window_minutes — reject candidates whose score data is older than N minutes.
    """
    min_tier:              str   = DEFAULT_MIN_TIER
    min_adjusted_score:    float = 0.0
    min_confidence_pct:    float = 0.0
    dry_run:               bool  = False
    shadow_mode:           bool  = False
    dedup_window_hours:    int   = DEFAULT_DEDUP_WINDOW_H
    rate_limit_per_hour:   int   = DEFAULT_RATE_LIMIT
    stale_window_minutes:  int   = DEFAULT_STALE_WINDOW_MIN

    def tier_rank(self, tier: str) -> int:
        return TIER_RANK.get(tier, -1)

    def tier_meets_minimum(self, tier: str) -> bool:
        return self.tier_rank(tier) >= self.tier_rank(self.min_tier)

    # ── Named constructors ─────────────────────────────────────────────────────

    @classmethod
    def conviction_only(cls) -> "NotificationPolicy":
        """Conservative production default: conviction-tier alerts only."""
        return cls(min_tier="CONVICTION")

    @classmethod
    def alert_and_above(cls) -> "NotificationPolicy":
        """Allow ALERT and CONVICTION tiers."""
        return cls(min_tier="ALERT")

    @classmethod
    def dry_run_mode(cls) -> "NotificationPolicy":
        """Evaluate eligibility without sending anything."""
        return cls(min_tier="ALERT", dry_run=True)

    @classmethod
    def shadow_mode_policy(cls) -> "NotificationPolicy":
        """Shadow evaluation: run gateway, log comparisons, never send."""
        return cls(min_tier="ALERT", shadow_mode=True)
