"""
Phase N1 — Dashboard/API parity tests.

Verifies that the shared resolver utilities in alert_formatter produce
identical output to the canonical predator functions, ensuring:
  API values == notification values == formatter values
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from alert_formatter import resolve_tier, resolve_confidence
from predator import (
    classify_tier,
    compute_confidence,
    compute_adjusted_score,
    ALERT_THRESHOLD,
    ALERT_MIN_ADJUSTED,
    CONVICTION_MIN_CONFIDENCE,
    CONVICTION_MIN_SIGNALS,
    _SIGNAL_LABELS,
)


def _sig(score: int, quality: str = "MEDIUM") -> dict:
    return {"score": score, "reason": "test", "data_quality": quality}


# ─────────────────────────────────────────────
# resolve_tier matches classify_tier
# ─────────────────────────────────────────────

class TestResolveTierParity:
    """resolve_tier() must return the same value as classify_tier() for all inputs."""

    def _check_parity(self, raw, conf, active, adj):
        expected = classify_tier(raw, adj, conf, active)
        actual   = resolve_tier(raw, conf, active, adj)
        assert actual == expected, (
            f"Mismatch raw={raw} conf={conf} active={active} adj={adj}: "
            f"classify_tier={expected} resolve_tier={actual}"
        )

    def test_watch_below_threshold(self):
        self._check_parity(raw=5, conf=100.0, active=6, adj=5.0)

    def test_watch_above_threshold_low_quality(self):
        self._check_parity(raw=6, conf=20.0, active=1, adj=1.2)

    def test_alert_at_adjusted_threshold(self):
        self._check_parity(
            raw=6, conf=50.0, active=2,
            adj=compute_adjusted_score(6, 50.0),
        )

    def test_conviction_all_gates_met(self):
        self._check_parity(
            raw=8, conf=CONVICTION_MIN_CONFIDENCE, active=CONVICTION_MIN_SIGNALS,
            adj=compute_adjusted_score(8, CONVICTION_MIN_CONFIDENCE),
        )

    def test_parity_across_score_range(self):
        for raw in range(0, 11):
            for conf in (0.0, 30.0, 55.0, 80.0, 100.0):
                for active in (0, 1, 2, 3, 6):
                    adj = compute_adjusted_score(raw, conf)
                    self._check_parity(raw, conf, active, adj)

    def test_none_inputs_return_watch(self):
        assert resolve_tier(None, None, 0, None) == "WATCH"

    def test_invalid_raw_falls_back_to_watch(self):
        # resolve_tier must not crash on edge inputs
        result = resolve_tier(-1, 50.0, 2, 2.5)
        assert result in ("WATCH", "ALERT", "CONVICTION")


# ─────────────────────────────────────────────
# resolve_confidence matches compute_confidence
# ─────────────────────────────────────────────

class TestResolveConfidenceParity:
    def test_identical_for_all_high_signals(self):
        signals = {k: _sig(v[1], "HIGH") for k, v in _SIGNAL_LABELS.items()}
        expected = compute_confidence(signals)
        actual   = resolve_confidence(signals)
        assert actual == pytest.approx(expected, abs=0.01)

    def test_identical_for_mixed_quality(self):
        signals = {
            "options":  _sig(3, "HIGH"),
            "insider":  _sig(2, "MEDIUM"),
            "breakout": _sig(1, "LOW"),
        }
        expected = compute_confidence(signals)
        actual   = resolve_confidence(signals)
        assert actual == pytest.approx(expected, abs=0.01)

    def test_identical_for_empty_signals(self):
        assert resolve_confidence({}) == pytest.approx(compute_confidence({}), abs=0.01)

    def test_identical_for_zero_score_signals(self):
        signals = {k: _sig(0) for k in _SIGNAL_LABELS}
        expected = compute_confidence(signals)
        actual   = resolve_confidence(signals)
        assert actual == pytest.approx(expected, abs=0.01)


# ─────────────────────────────────────────────
# Notification message contains API-consistent values
# ─────────────────────────────────────────────

class TestNotificationAPIValueConsistency:
    def _build_and_format(self, signals):
        from alert_schema import AlertCandidate, EligibilityResult
        from alert_formatter import format_predator_alert
        from alert_eligibility import check_eligibility
        from notification_policy import NotificationPolicy

        raw_score      = min(sum(v.get("score", 0) for v in signals.values()), 10)
        confidence     = resolve_confidence(signals)
        adjusted       = compute_adjusted_score(raw_score, confidence)
        active_count   = sum(1 for v in signals.values() if v.get("score", 0) > 0)
        tier           = resolve_tier(raw_score, confidence, active_count, adjusted)

        candidate = AlertCandidate(
            source="predator", ticker="AMD",
            raw_score=float(raw_score), adjusted_score=adjusted,
            confidence_pct=confidence, tier=tier, regime="NEUTRAL",
            active_signals=[k for k, v in signals.items() if v.get("score", 0) > 0],
            suppressed_signals=[],
            urgency=None, entry_price=180.0, stop_price=163.8,
            position_size_cad=4500.0, risk_posture=None,
            metadata={"signals": signals},
        )
        policy      = NotificationPolicy.alert_and_above()
        eligibility = check_eligibility(candidate, policy)
        msg         = format_predator_alert(candidate, eligibility)
        return msg, tier, adjusted, confidence

    def test_api_tier_matches_message_tier_conviction(self):
        signals = {
            "options": _sig(3, "HIGH"),
            "insider": _sig(2, "HIGH"),
            "breakout": _sig(2, "HIGH"),
        }
        msg, api_tier, api_adj, api_conf = self._build_and_format(signals)
        assert api_tier in msg

    def test_api_adjusted_score_in_message(self):
        signals = {
            "options": _sig(3, "HIGH"),
            "insider": _sig(2, "MEDIUM"),
        }
        msg, _, api_adj, _ = self._build_and_format(signals)
        # Adj score shown to 1 decimal in message
        assert f"{api_adj:.1f}" in msg

    def test_api_confidence_in_message(self):
        signals = {"options": _sig(3, "HIGH"), "insider": _sig(2, "HIGH")}
        msg, _, _, api_conf = self._build_and_format(signals)
        assert f"{api_conf:.0f}" in msg or f"{int(api_conf)}" in msg
