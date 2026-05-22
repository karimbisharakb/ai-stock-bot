"""
Phase N1 — Tier eligibility tests.

Tests check_eligibility() as a pure function against all three sources,
all suppression paths, and edge cases in tier/urgency resolution.
No DB, no network, no mocking.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from alert_schema import AlertCandidate
from alert_eligibility import check_eligibility, _resolve_tier, _validate
from notification_policy import NotificationPolicy, TIER_RANK


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def predator_candidate(
    tier="CONVICTION", raw=8, adj=4.4, conf=55.0,
    regime="NEUTRAL", active=None, suppressed=None,
):
    return AlertCandidate(
        source="predator", ticker="NVDA",
        raw_score=raw, adjusted_score=adj, confidence_pct=conf,
        tier=tier, regime=regime,
        active_signals=active or ["options", "insider"],
        suppressed_signals=suppressed or [],
        urgency=None, entry_price=150.0, stop_price=136.5,
        position_size_cad=5000.0, risk_posture=None, metadata={},
    )


def scanner_candidate(tier="ALERT", raw=7, adj=4.9, conf=70.0):
    return AlertCandidate(
        source="scanner", ticker="BBAI",
        raw_score=raw, adjusted_score=adj, confidence_pct=conf,
        tier=tier, regime=None,
        active_signals=["strong bullish chatter", "RSI 58 bullish momentum"],
        suppressed_signals=[],
        urgency=None, entry_price=12.0, stop_price=None,
        position_size_cad=None, risk_posture=None, metadata={},
    )


def sell_candidate(urgency="URGENT", active=None):
    return AlertCandidate(
        source="sell_monitor", ticker="VFV.TO",
        raw_score=None, adjusted_score=None, confidence_pct=None,
        tier=None, regime=None,
        active_signals=active or ["Price broke below 50-day MA"],
        suppressed_signals=[],
        urgency=urgency, entry_price=158.0, stop_price=None,
        position_size_cad=None, risk_posture="High", metadata={},
    )


CONVICTION_POLICY = NotificationPolicy.conviction_only()
ALERT_POLICY      = NotificationPolicy.alert_and_above()


# ─────────────────────────────────────────────
# Tier resolution
# ─────────────────────────────────────────────

class TestTierResolution:
    def test_predator_uses_tier_field(self):
        assert _resolve_tier(predator_candidate("CONVICTION")) == "CONVICTION"
        assert _resolve_tier(predator_candidate("ALERT"))      == "ALERT"
        assert _resolve_tier(predator_candidate("WATCH"))      == "WATCH"

    def test_scanner_uses_tier_field(self):
        assert _resolve_tier(scanner_candidate("ALERT"))      == "ALERT"
        assert _resolve_tier(scanner_candidate("CONVICTION")) == "CONVICTION"

    def test_none_tier_resolves_to_watch(self):
        c = predator_candidate(tier=None)
        assert _resolve_tier(c) == "WATCH"

    def test_sell_monitor_urgent_maps_to_conviction(self):
        assert _resolve_tier(sell_candidate("URGENT")) == "CONVICTION"

    def test_sell_monitor_warning_maps_to_alert(self):
        assert _resolve_tier(sell_candidate("WARNING")) == "ALERT"

    def test_sell_monitor_fyi_maps_to_watch(self):
        assert _resolve_tier(sell_candidate("FYI")) == "WATCH"

    def test_sell_monitor_none_urgency_maps_to_watch(self):
        assert _resolve_tier(sell_candidate(urgency=None)) == "WATCH"


# ─────────────────────────────────────────────
# Conviction-only policy
# ─────────────────────────────────────────────

class TestConvictionOnlyPolicy:
    def test_conviction_predator_is_eligible(self):
        result = check_eligibility(predator_candidate("CONVICTION"), CONVICTION_POLICY)
        assert result.eligible is True
        assert result.resolved_tier == "CONVICTION"

    def test_alert_predator_is_suppressed(self):
        result = check_eligibility(predator_candidate("ALERT"), CONVICTION_POLICY)
        assert result.eligible is False
        assert any("tier_below_minimum" in r for r in result.suppression_reasons)

    def test_watch_predator_is_suppressed(self):
        result = check_eligibility(predator_candidate("WATCH"), CONVICTION_POLICY)
        assert result.eligible is False

    def test_urgent_sell_maps_to_conviction_eligible(self):
        result = check_eligibility(sell_candidate("URGENT"), CONVICTION_POLICY)
        assert result.eligible is True
        assert result.resolved_tier == "CONVICTION"

    def test_warning_sell_suppressed_by_conviction_policy(self):
        result = check_eligibility(sell_candidate("WARNING"), CONVICTION_POLICY)
        assert result.eligible is False


# ─────────────────────────────────────────────
# Alert-and-above policy
# ─────────────────────────────────────────────

class TestAlertAndAbovePolicy:
    def test_alert_scanner_is_eligible(self):
        result = check_eligibility(scanner_candidate("ALERT"), ALERT_POLICY)
        assert result.eligible is True

    def test_conviction_scanner_is_eligible(self):
        result = check_eligibility(scanner_candidate("CONVICTION"), ALERT_POLICY)
        assert result.eligible is True

    def test_watch_scanner_suppressed(self):
        result = check_eligibility(scanner_candidate("WATCH"), ALERT_POLICY)
        assert result.eligible is False

    def test_warning_sell_is_eligible(self):
        result = check_eligibility(sell_candidate("WARNING"), ALERT_POLICY)
        assert result.eligible is True


# ─────────────────────────────────────────────
# Secondary gates (adjusted score / confidence floors)
# ─────────────────────────────────────────────

class TestSecondaryGates:
    def test_adjusted_score_floor_suppresses_below_minimum(self):
        policy = NotificationPolicy(min_tier="ALERT", min_adjusted_score=5.0)
        c      = predator_candidate("ALERT", adj=3.0)
        result = check_eligibility(c, policy)
        assert result.eligible is False
        assert any("adjusted_score_below_floor" in r for r in result.suppression_reasons)

    def test_adjusted_score_floor_passes_above_minimum(self):
        policy = NotificationPolicy(min_tier="ALERT", min_adjusted_score=2.5)
        c      = predator_candidate("ALERT", adj=3.0)
        result = check_eligibility(c, policy)
        assert result.eligible is True

    def test_confidence_floor_suppresses_below_minimum(self):
        policy = NotificationPolicy(min_tier="ALERT", min_confidence_pct=60.0)
        c      = predator_candidate("ALERT", conf=40.0)
        result = check_eligibility(c, policy)
        assert result.eligible is False
        assert any("confidence_below_floor" in r for r in result.suppression_reasons)

    def test_confidence_floor_passes_at_threshold(self):
        policy = NotificationPolicy(min_tier="ALERT", min_confidence_pct=55.0)
        c      = predator_candidate("ALERT", conf=55.0)
        result = check_eligibility(c, policy)
        assert result.eligible is True

    def test_zero_secondary_floors_do_not_suppress(self):
        # Default floors of 0 must never suppress on their own
        policy = NotificationPolicy(min_tier="ALERT", min_adjusted_score=0.0, min_confidence_pct=0.0)
        c      = predator_candidate("ALERT", adj=0.1, conf=0.0)
        result = check_eligibility(c, policy)
        assert result.eligible is True


# ─────────────────────────────────────────────
# Eligibility reasoning
# ─────────────────────────────────────────────

class TestEligibilityReasoning:
    def test_eligible_result_contains_tier_in_reasons(self):
        result = check_eligibility(predator_candidate("CONVICTION"), CONVICTION_POLICY)
        assert any("tier:" in r for r in result.reasons)

    def test_eligible_result_has_trigger_reason(self):
        result = check_eligibility(predator_candidate("CONVICTION"), CONVICTION_POLICY)
        assert len(result.trigger_reason) > 0

    def test_suppressed_result_has_no_trigger_reason(self):
        result = check_eligibility(predator_candidate("WATCH"), CONVICTION_POLICY)
        assert result.trigger_reason == ""

    def test_trigger_reason_mentions_regime_when_bear(self):
        c      = predator_candidate("CONVICTION", regime="BEAR")
        result = check_eligibility(c, CONVICTION_POLICY)
        assert "BEAR" in result.trigger_reason

    def test_sell_monitor_trigger_reason_mentions_urgency(self):
        c      = sell_candidate("URGENT", active=["RSI rollover", "MACD bearish"])
        result = check_eligibility(c, CONVICTION_POLICY)
        assert "URGENT" in result.trigger_reason

    def test_adjusted_score_and_confidence_in_trigger_reason(self):
        c      = predator_candidate("CONVICTION", adj=4.4, conf=55.0)
        result = check_eligibility(c, CONVICTION_POLICY)
        assert "4.4" in result.trigger_reason
        assert "55" in result.trigger_reason


# ─────────────────────────────────────────────
# Tier rank ordering
# ─────────────────────────────────────────────

class TestTierRankOrdering:
    def test_conviction_rank_greater_than_alert(self):
        assert TIER_RANK["CONVICTION"] > TIER_RANK["ALERT"]

    def test_alert_rank_greater_than_watch(self):
        assert TIER_RANK["ALERT"] > TIER_RANK["WATCH"]

    def test_conviction_only_policy_blocks_alert(self):
        policy = NotificationPolicy.conviction_only()
        assert not policy.tier_meets_minimum("ALERT")
        assert not policy.tier_meets_minimum("WATCH")
        assert policy.tier_meets_minimum("CONVICTION")

    def test_alert_and_above_allows_both(self):
        policy = NotificationPolicy.alert_and_above()
        assert policy.tier_meets_minimum("ALERT")
        assert policy.tier_meets_minimum("CONVICTION")
        assert not policy.tier_meets_minimum("WATCH")
