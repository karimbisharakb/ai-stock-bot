"""
Tests for bot/controlled_adaptation.py — Phase 5A.

Covers:
  - apply_controlled_weights: per-signal clamp, portfolio cap, allowlist
  - check_adaptation_gate: each blocker independently, policy derivation
  - check_rollback_triggers: each trigger type, severity ordering
  - compute_adaptation_step: bounds, ready_to_apply flag, allowlist
  - record_adaptation_entry: field correctness, delta computation
  - analyze_adaptation_impact: counts, drift, top signals
  - evaluate_rollout_confidence: scoring formula, clamping
  - generate_adaptation_report: all required keys, integration
  - Cooldown / rollback-lockout scheduler behavior
  - Determinism
  - Bounded outputs
  - Sparse / None handling
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import controlled_adaptation as ca
from adaptive_weights import DEFAULT_WEIGHTS
from shadow_adaptive import (
    READINESS_NOT_READY, READINESS_OBSERVE, READINESS_LIMITED, READINESS_STABLE,
    STABILITY_STABLE, STABILITY_WATCH, STABILITY_UNSTABLE,
    SIGNAL_NAMES,
)

# ── helpers ───────────────────────────────────────────────────────────────────

def _shadow_report(
    readiness=READINESS_LIMITED,
    stability=STABILITY_STABLE,
    n_rows=40,
    win_rate_delta=3.0,
):
    return {
        "readiness":  {"status": readiness},
        "stability":  {"overall": stability, "per_signal": {
            sig: {"label": STABILITY_STABLE, "oscillations": 0,
                  "weight_std": 0.0, "max_adj": 0.05}
            for sig in SIGNAL_NAMES
        }},
        "n_rows": n_rows,
        "comparison": {
            "n_rows": n_rows,
            "live_win_rate":    60.0,
            "shadow_win_rate":  63.0,
            "win_rate_delta":   win_rate_delta,
            "live_brier":       0.24,
            "shadow_brier":     0.22,
            "calibration_delta": -0.02,
            "churn_rate":       0.05,
            "live_drawdown":    5.0,
            "shadow_drawdown":  4.0,
        },
    }


def _hub(health="HEALTHY"):
    return {"overall_health": health}


def _comp(
    live_wr=60.0, live_brier=0.24, live_dd=5.0, churn=0.05
):
    return {
        "live_win_rate":    live_wr,
        "live_brier":       live_brier,
        "live_drawdown":    live_dd,
        "churn_rate":       churn,
    }


def _prior_comp(
    live_wr=60.0, live_brier=0.22, live_dd=4.0
):
    return {
        "live_win_rate":  live_wr,
        "live_brier":     live_brier,
        "live_drawdown":  live_dd,
    }


def _wa(adjustments: dict) -> dict:
    """Build weight_adjustments dict from {signal: adjustment} overrides."""
    result = {}
    for sig in SIGNAL_NAMES:
        default = DEFAULT_WEIGHTS[sig]
        adj     = adjustments.get(sig, 0.0)
        result[sig] = {
            "default_weight":   default,
            "adjustment":       adj,
            "suggested_weight": default + adj,
        }
    return result


def _history_entry(
    prior=None,
    applied=None,
    policy=ca.POLICY_LIMITED_TRIAL,
    rollback=False,
    cause=None,
    timestamp="",
):
    defaults = {sig: DEFAULT_WEIGHTS[sig] for sig in SIGNAL_NAMES}
    p = prior   or defaults
    a = applied or {sig: DEFAULT_WEIGHTS[sig] + 0.05 for sig in SIGNAL_NAMES}
    return ca.record_adaptation_entry(
        prior_weights=p, applied_weights=a,
        policy=policy, reason="test",
        timestamp=timestamp, rollback=rollback, rollback_cause=cause,
    )


def _rollback_entry():
    return _history_entry(rollback=True, cause="WIN_RATE_REGRESSION")


# ── TestApplyControlledWeights ────────────────────────────────────────────────

class TestApplyControlledWeights:
    def test_no_adjustments_returns_defaults(self):
        result = ca.apply_controlled_weights({})
        for sig in SIGNAL_NAMES:
            assert result["applied_weights"][sig] == pytest.approx(DEFAULT_WEIGHTS[sig])

    def test_small_adjustment_passes_through(self):
        proposed = {sig: DEFAULT_WEIGHTS[sig] + 0.05 for sig in SIGNAL_NAMES}
        result   = ca.apply_controlled_weights(proposed)
        assert result["applied_weights"]["options"] == pytest.approx(
            DEFAULT_WEIGHTS["options"] + 0.05
        )

    def test_per_signal_max_adjustment_clamped(self):
        # Request +0.50 but limit is LIVE_MAX_ADJUSTMENT_PER_SIGNAL = 0.10
        proposed = {sig: DEFAULT_WEIGHTS[sig] + 0.50 for sig in SIGNAL_NAMES}
        result   = ca.apply_controlled_weights(proposed)
        for sig in SIGNAL_NAMES:
            delta = result["per_signal_delta"][sig]
            assert abs(delta) <= ca.LIVE_MAX_ADJUSTMENT_PER_SIGNAL + 1e-9

    def test_per_signal_clamped_signals_reported(self):
        proposed = {"options": DEFAULT_WEIGHTS["options"] + 0.50}
        result   = ca.apply_controlled_weights(proposed)
        assert "options" in result["clamped_signals"]

    def test_total_portfolio_cap_enforced(self):
        # Max per-signal × 6 signals = 0.60 > TOTAL_PORTFOLIO_ADJUSTMENT_CAP (0.30)
        proposed = {sig: DEFAULT_WEIGHTS[sig] + 0.10 for sig in SIGNAL_NAMES}
        result   = ca.apply_controlled_weights(proposed)
        assert result["total_portfolio_delta"] <= ca.TOTAL_PORTFOLIO_ADJUSTMENT_CAP + 1e-9
        assert result["clamped_by_portfolio"]

    def test_portfolio_cap_not_hit_when_few_signals_adjusted(self):
        # Only one signal adjusted by 0.05
        proposed = {"options": DEFAULT_WEIGHTS["options"] + 0.05}
        result   = ca.apply_controlled_weights(proposed)
        assert not result["clamped_by_portfolio"]

    def test_allowlist_blocks_unlisted_signals(self):
        proposed = {sig: DEFAULT_WEIGHTS[sig] + 0.08 for sig in SIGNAL_NAMES}
        result   = ca.apply_controlled_weights(proposed, allowlist=["options"])
        # Only options should be adapted
        for sig in SIGNAL_NAMES:
            if sig != "options":
                assert result["applied_weights"][sig] == pytest.approx(DEFAULT_WEIGHTS[sig])
        assert result["applied_weights"]["options"] > DEFAULT_WEIGHTS["options"]

    def test_allowlist_blocked_list_populated(self):
        result = ca.apply_controlled_weights({}, allowlist=["options"])
        blocked = set(result["allowlist_blocked"])
        assert "options" not in blocked
        assert all(sig in blocked for sig in SIGNAL_NAMES if sig != "options")

    def test_min_live_weight_floor_respected(self):
        # Request -1.0 from each signal (should hit MIN_LIVE_WEIGHT)
        proposed = {sig: 0.0 for sig in SIGNAL_NAMES}
        result   = ca.apply_controlled_weights(proposed)
        for sig in SIGNAL_NAMES:
            assert result["applied_weights"][sig] >= ca.MIN_LIVE_WEIGHT - 1e-9

    def test_all_keys_present(self):
        result = ca.apply_controlled_weights({})
        for k in ("applied_weights", "per_signal_delta", "clamped_signals",
                  "total_portfolio_delta", "clamped_by_portfolio", "allowlist_blocked"):
            assert k in result, f"missing key: {k}"

    def test_none_proposed_returns_defaults(self):
        result = ca.apply_controlled_weights(None)
        for sig in SIGNAL_NAMES:
            assert result["applied_weights"][sig] == pytest.approx(DEFAULT_WEIGHTS[sig])


# ── TestCheckAdaptationGate ───────────────────────────────────────────────────

class TestCheckAdaptationGate:
    def test_blocked_when_readiness_too_low(self):
        sr = _shadow_report(readiness=READINESS_NOT_READY)
        g  = ca.check_adaptation_gate(sr)
        assert not g["allowed"]
        assert any("readiness" in b.lower() for b in g["blockers"])

    def test_blocked_when_hub_critical(self):
        sr = _shadow_report(readiness=READINESS_LIMITED)
        g  = ca.check_adaptation_gate(sr, _hub("CRITICAL"))
        assert not g["allowed"]
        assert any("CRITICAL" in b for b in g["blockers"])

    def test_hub_degraded_does_not_block(self):
        sr = _shadow_report(readiness=READINESS_LIMITED)
        g  = ca.check_adaptation_gate(sr, _hub("DEGRADED"))
        # DEGRADED → reason not blocker
        assert "DEGRADED" not in " ".join(g["blockers"])
        # might still be blocked by other conditions, but not DEGRADED alone
        assert any("DEGRADED" in r for r in g["reasons"])

    def test_blocked_when_stability_unstable(self):
        sr = _shadow_report(readiness=READINESS_LIMITED, stability=STABILITY_UNSTABLE)
        g  = ca.check_adaptation_gate(sr)
        assert not g["allowed"]
        assert any("UNSTABLE" in b for b in g["blockers"])

    def test_blocked_when_insufficient_rows(self):
        sr = _shadow_report(readiness=READINESS_LIMITED, n_rows=5)
        g  = ca.check_adaptation_gate(sr)
        assert not g["allowed"]
        assert any("rows" in b.lower() for b in g["blockers"])

    def test_blocked_when_in_cooldown(self):
        sr = _shadow_report(readiness=READINESS_LIMITED)
        g  = ca.check_adaptation_gate(sr, rows_since_last=10,
                                       cooldown_rows=ca.DEFAULT_COOLDOWN_ROWS)
        assert not g["allowed"]
        assert any("cooldown" in b.lower() for b in g["blockers"])

    def test_not_blocked_when_cooldown_elapsed(self):
        sr = _shadow_report(readiness=READINESS_LIMITED)
        g  = ca.check_adaptation_gate(sr, rows_since_last=25,
                                       cooldown_rows=ca.DEFAULT_COOLDOWN_ROWS)
        # cooldown should not block now (25 >= 20)
        cooldown_blocker = any("cooldown" in b.lower() for b in g["blockers"])
        assert not cooldown_blocker

    def test_blocked_when_in_rollback_lockout(self):
        sr = _shadow_report(readiness=READINESS_LIMITED)
        g  = ca.check_adaptation_gate(sr, rows_since_rollback=10,
                                       rollback_lockout_rows=ca.DEFAULT_ROLLBACK_LOCKOUT_ROWS)
        assert not g["allowed"]
        assert any("lockout" in b.lower() for b in g["blockers"])

    def test_allowed_when_all_conditions_met(self):
        sr = _shadow_report(readiness=READINESS_LIMITED)
        g  = ca.check_adaptation_gate(sr, rows_since_last=25, rows_since_rollback=50)
        assert g["allowed"]
        assert g["blockers"] == []

    def test_policy_limited_trial_when_limited_ready(self):
        sr = _shadow_report(readiness=READINESS_LIMITED)
        g  = ca.check_adaptation_gate(sr, rows_since_last=25, rows_since_rollback=50)
        assert g["policy"] == ca.POLICY_LIMITED_TRIAL

    def test_policy_controlled_active_when_stable(self):
        sr = _shadow_report(readiness=READINESS_STABLE)
        g  = ca.check_adaptation_gate(sr, rows_since_last=25, rows_since_rollback=50)
        assert g["policy"] == ca.POLICY_CONTROLLED_ACTIVE

    def test_policy_observation_only_when_readiness_low_but_observe(self):
        sr = _shadow_report(readiness=READINESS_OBSERVE)
        g  = ca.check_adaptation_gate(sr)
        assert g["policy"] == ca.POLICY_OBSERVATION_ONLY

    def test_policy_disabled_when_readiness_not_ready(self):
        sr = _shadow_report(readiness=READINESS_NOT_READY)
        g  = ca.check_adaptation_gate(sr)
        assert g["policy"] == ca.POLICY_DISABLED

    def test_reasons_list_populated_when_passing(self):
        sr = _shadow_report(readiness=READINESS_LIMITED)
        g  = ca.check_adaptation_gate(sr)
        assert len(g["reasons"]) > 0


# ── TestCheckRollbackTriggers ─────────────────────────────────────────────────

class TestCheckRollbackTriggers:
    def test_no_triggers_when_clean(self):
        result = ca.check_rollback_triggers(_comp(), None, _hub(), _prior_comp())
        assert not result["should_rollback"]
        assert result["severity"] == "NONE"

    def test_calibration_worsening_triggers_rollback(self):
        # brier increases from 0.20 to 0.24 → delta = +0.04 > threshold 0.02
        curr  = _comp(live_brier=0.24)
        prior = _prior_comp(live_brier=0.20)
        result = ca.check_rollback_triggers(curr, None, None, prior)
        assert result["should_rollback"]
        types = [t["type"] for t in result["triggers"]]
        assert "CALIBRATION_WORSENING" in types

    def test_win_rate_regression_triggers_rollback(self):
        # drops from 65% → 55% = 10pp drop > threshold 5pp
        curr  = _comp(live_wr=55.0)
        prior = _prior_comp(live_wr=65.0)
        result = ca.check_rollback_triggers(curr, None, None, prior)
        assert result["should_rollback"]
        types = [t["type"] for t in result["triggers"]]
        assert "WIN_RATE_REGRESSION" in types

    def test_drawdown_spike_triggers_rollback(self):
        # drawdown increases from 5% to 12% → +7pp > threshold 5pp
        curr  = _comp(live_dd=12.0)
        prior = _prior_comp(live_dd=5.0)
        result = ca.check_rollback_triggers(curr, None, None, prior)
        assert result["should_rollback"]
        types = [t["type"] for t in result["triggers"]]
        assert "DRAWDOWN_SPIKE" in types

    def test_unstable_churn_triggers_rollback(self):
        curr   = _comp(churn=0.35)  # > 0.30
        result = ca.check_rollback_triggers(curr, None, None, None)
        assert result["should_rollback"]
        types = [t["type"] for t in result["triggers"]]
        assert "UNSTABLE_CHURN" in types

    def test_hub_critical_triggers_rollback(self):
        result = ca.check_rollback_triggers({}, None, _hub("CRITICAL"), None)
        assert result["should_rollback"]
        types = [t["type"] for t in result["triggers"]]
        assert "OPERATIONAL_CRITICAL" in types

    def test_weight_instability_triggers_rollback(self):
        stab   = {"overall": STABILITY_UNSTABLE}
        result = ca.check_rollback_triggers({}, stab, None, None)
        assert result["should_rollback"]
        types = [t["type"] for t in result["triggers"]]
        assert "WEIGHT_INSTABILITY" in types

    def test_severity_critical_dominates(self):
        result = ca.check_rollback_triggers({}, None, _hub("CRITICAL"), None)
        assert result["severity"] == "CRITICAL"

    def test_severity_high_when_no_critical(self):
        curr  = _comp(live_brier=0.24)
        prior = _prior_comp(live_brier=0.20)
        result = ca.check_rollback_triggers(curr, None, _hub("HEALTHY"), prior)
        assert result["severity"] == "HIGH"

    def test_triggers_capped_at_max(self):
        # Trigger all 6 conditions simultaneously
        curr  = _comp(live_wr=50.0, live_brier=0.30, live_dd=15.0, churn=0.35)
        prior = _prior_comp(live_wr=65.0, live_brier=0.22, live_dd=5.0)
        stab  = {"overall": STABILITY_UNSTABLE}
        result = ca.check_rollback_triggers(curr, stab, _hub("CRITICAL"), prior)
        assert len(result["triggers"]) <= ca.MAX_TRIGGER_ENTRIES


# ── TestComputeAdaptationStep ─────────────────────────────────────────────────

class TestComputeAdaptationStep:
    def test_all_keys_present(self):
        step = ca.compute_adaptation_step(_wa({}), _shadow_report())
        for k in ("proposed_weights", "clamped_weights", "per_signal_delta",
                  "clamped_signals", "total_portfolio_delta",
                  "clamped_by_portfolio", "allowlist_blocked", "ready_to_apply"):
            assert k in step, f"missing key: {k}"

    def test_ready_to_apply_false_when_not_ready(self):
        step = ca.compute_adaptation_step(
            _wa({}), _shadow_report(readiness=READINESS_NOT_READY)
        )
        assert not step["ready_to_apply"]

    def test_ready_to_apply_true_when_limited_ready(self):
        step = ca.compute_adaptation_step(
            _wa({}), _shadow_report(readiness=READINESS_LIMITED)
        )
        assert step["ready_to_apply"]

    def test_proposed_weights_match_suggested_from_wa(self):
        wa   = _wa({"options": 0.30})  # suggested = 3.30
        step = ca.compute_adaptation_step(wa, _shadow_report())
        assert step["proposed_weights"]["options"] == pytest.approx(
            DEFAULT_WEIGHTS["options"] + 0.30
        )

    def test_clamp_applied_to_large_adjustment(self):
        wa   = _wa({"options": 0.50})  # far above LIVE_MAX_ADJUSTMENT_PER_SIGNAL
        step = ca.compute_adaptation_step(wa, _shadow_report())
        delta = step["per_signal_delta"]["options"]
        assert abs(delta) <= ca.LIVE_MAX_ADJUSTMENT_PER_SIGNAL + 1e-9
        assert "options" in step["clamped_signals"]

    def test_allowlist_restricts_signals(self):
        wa   = _wa({"options": 0.08, "breakout": 0.08})
        step = ca.compute_adaptation_step(wa, _shadow_report(),
                                          allowlist=["options"])
        assert step["clamped_weights"]["breakout"] == pytest.approx(DEFAULT_WEIGHTS["breakout"])
        assert "breakout" in step["allowlist_blocked"]

    def test_no_wa_returns_default_weights(self):
        step = ca.compute_adaptation_step(None, _shadow_report())
        for sig in SIGNAL_NAMES:
            assert step["clamped_weights"][sig] == pytest.approx(DEFAULT_WEIGHTS[sig])


# ── TestRecordAdaptationEntry ─────────────────────────────────────────────────

class TestRecordAdaptationEntry:
    def _defaults(self):
        return {sig: DEFAULT_WEIGHTS[sig] for sig in SIGNAL_NAMES}

    def test_all_fields_present(self):
        entry = ca.record_adaptation_entry(
            self._defaults(), self._defaults(),
            ca.POLICY_LIMITED_TRIAL, "test reason",
        )
        for k in ("timestamp", "policy", "prior_weights", "applied_weights",
                  "delta_weights", "reason", "rollback", "rollback_cause"):
            assert k in entry, f"missing field: {k}"

    def test_delta_weights_computed_correctly(self):
        prior   = self._defaults()
        applied = dict(prior)
        applied["options"] = DEFAULT_WEIGHTS["options"] + 0.07
        entry   = ca.record_adaptation_entry(prior, applied, ca.POLICY_LIMITED_TRIAL, "r")
        assert entry["delta_weights"]["options"] == pytest.approx(0.07, abs=1e-5)
        assert entry["delta_weights"]["insider"] == pytest.approx(0.0, abs=1e-9)

    def test_rollback_flag_stored(self):
        entry = ca.record_adaptation_entry(
            self._defaults(), self._defaults(),
            ca.POLICY_LIMITED_TRIAL, "r", rollback=True,
        )
        assert entry["rollback"] is True

    def test_rollback_cause_stored(self):
        entry = ca.record_adaptation_entry(
            self._defaults(), self._defaults(),
            ca.POLICY_LIMITED_TRIAL, "r",
            rollback=True, rollback_cause="DRAWDOWN_SPIKE",
        )
        assert entry["rollback_cause"] == "DRAWDOWN_SPIKE"

    def test_non_rollback_has_none_cause(self):
        entry = ca.record_adaptation_entry(
            self._defaults(), self._defaults(), ca.POLICY_LIMITED_TRIAL, "r"
        )
        assert entry["rollback"]       is False
        assert entry["rollback_cause"] is None

    def test_timestamp_stored(self):
        entry = ca.record_adaptation_entry(
            self._defaults(), self._defaults(),
            ca.POLICY_LIMITED_TRIAL, "r", timestamp="2026-01-01T00:00:00",
        )
        assert entry["timestamp"] == "2026-01-01T00:00:00"


# ── TestAnalyzeAdaptationImpact ───────────────────────────────────────────────

class TestAnalyzeAdaptationImpact:
    def test_empty_history_all_zeros(self):
        result = ca.analyze_adaptation_impact([], {})
        assert result["n_adaptations"]     == 0
        assert result["n_rollbacks"]       == 0
        assert result["total_weight_drift"] == 0.0

    def test_n_adaptations_counted(self):
        hist   = [_history_entry() for _ in range(3)]
        result = ca.analyze_adaptation_impact(hist, {})
        assert result["n_adaptations"] == 3

    def test_n_rollbacks_counted(self):
        hist   = [_history_entry() for _ in range(2)] + [_rollback_entry()]
        result = ca.analyze_adaptation_impact(hist, {})
        assert result["n_rollbacks"]   == 1
        assert result["n_adaptations"] == 2

    def test_weight_drift_summed(self):
        # Each entry adjusts all 6 signals by +0.05 → drift per entry = 6 × 0.05 = 0.30
        hist   = [_history_entry() for _ in range(2)]
        result = ca.analyze_adaptation_impact(hist, {})
        assert result["total_weight_drift"] == pytest.approx(0.60, abs=1e-4)

    def test_signals_most_adapted_sorted(self):
        hist   = [_history_entry() for _ in range(3)]
        result = ca.analyze_adaptation_impact(hist, {})
        drifts = [v for _, v in result["signals_most_adapted"]]
        assert drifts == sorted(drifts, reverse=True)

    def test_performance_impact_from_comparison(self):
        comp   = {"win_rate_delta": 2.5, "calibration_delta": -0.01, "churn_rate": 0.05}
        result = ca.analyze_adaptation_impact([], comp)
        assert result["performance_impact"] == pytest.approx(2.5)

    def test_all_keys_present(self):
        result = ca.analyze_adaptation_impact([], {})
        for k in ("n_adaptations", "n_rollbacks", "n_history_entries",
                  "total_weight_drift", "avg_delta_magnitude",
                  "signals_most_adapted", "sig_drift",
                  "performance_impact", "calibration_delta", "churn_rate"):
            assert k in result, f"missing key: {k}"


# ── TestEvaluateRolloutConfidence ─────────────────────────────────────────────

class TestEvaluateRolloutConfidence:
    def test_zero_when_nothing_provided(self):
        result = ca.evaluate_rollout_confidence(None, None, None)
        assert result["rollout_confidence"] >= 0

    def test_gate_open_adds_50(self):
        gate  = {"allowed": True, "policy": ca.POLICY_LIMITED_TRIAL,
                 "blockers": [], "reasons": []}
        stab  = _shadow_report(stability=STABILITY_STABLE)
        result = ca.evaluate_rollout_confidence(gate, stab, [])
        assert result["rollout_confidence"] >= 50

    def test_gate_blocked_no_50_bonus(self):
        gate  = {"allowed": False, "policy": ca.POLICY_DISABLED,
                 "blockers": ["test"], "reasons": []}
        result = ca.evaluate_rollout_confidence(gate, _shadow_report(readiness=READINESS_NOT_READY), [])
        assert result["rollout_confidence"] < 50

    def test_stable_readiness_adds_30(self):
        gate   = {"allowed": True, "policy": ca.POLICY_CONTROLLED_ACTIVE,
                  "blockers": [], "reasons": []}
        shadow = _shadow_report(readiness=READINESS_STABLE, stability=STABILITY_STABLE)
        # Pass one non-rollback entry so the "+5 no recent rollbacks" bonus fires
        hist   = [_history_entry(rollback=False)]
        result = ca.evaluate_rollout_confidence(gate, shadow, hist)
        # 50 (gate) + 30 (stable readiness) + 15 (stable weights) + 5 (no rollbacks) = 100
        assert result["rollout_confidence"] == 100

    def test_recent_rollbacks_reduce_score(self):
        gate   = {"allowed": True, "policy": ca.POLICY_LIMITED_TRIAL,
                  "blockers": [], "reasons": []}
        shadow = _shadow_report(readiness=READINESS_LIMITED)
        hist   = [_rollback_entry(), _rollback_entry()]
        result = ca.evaluate_rollout_confidence(gate, shadow, hist)
        # Rollbacks subtract 10 × n each
        # Without rollbacks: 50 + 20 + 15 + 5 = 90
        # With 2 rollbacks:  50 + 20 + 15 - 20 = 65
        assert result["rollout_confidence"] < 90

    def test_score_clamped_to_100(self):
        gate   = {"allowed": True, "policy": ca.POLICY_CONTROLLED_ACTIVE,
                  "blockers": [], "reasons": []}
        shadow = _shadow_report(readiness=READINESS_STABLE, stability=STABILITY_STABLE)
        result = ca.evaluate_rollout_confidence(gate, shadow, [_history_entry()])
        assert result["rollout_confidence"] <= 100

    def test_score_clamped_to_zero(self):
        gate   = {"allowed": False, "policy": ca.POLICY_DISABLED,
                  "blockers": ["b"], "reasons": []}
        shadow = _shadow_report(readiness=READINESS_NOT_READY, stability=STABILITY_UNSTABLE)
        hist   = [_rollback_entry() for _ in range(10)]
        result = ca.evaluate_rollout_confidence(gate, shadow, hist)
        assert result["rollout_confidence"] >= 0

    def test_active_policy_from_gate(self):
        gate   = {"allowed": True, "policy": ca.POLICY_LIMITED_TRIAL,
                  "blockers": [], "reasons": []}
        result = ca.evaluate_rollout_confidence(gate, _shadow_report(), [])
        assert result["active_policy"] == ca.POLICY_LIMITED_TRIAL

    def test_reasons_list_populated(self):
        result = ca.evaluate_rollout_confidence(None, _shadow_report(), [])
        assert len(result["reasons"]) > 0


# ── TestGenerateAdaptationReport ──────────────────────────────────────────────

class TestGenerateAdaptationReport:
    def test_all_required_keys_present(self):
        report = ca.generate_adaptation_report()
        for k in ("report_type", "rollout_status", "gate", "rollback_check",
                  "adaptation_step", "impact", "rollout_confidence",
                  "history", "active_adjustments", "safeguard_status",
                  "recommendations", "changes_vs_previous"):
            assert k in report, f"missing key: {k}"

    def test_report_type(self):
        assert ca.generate_adaptation_report()["report_type"] == "controlled_adaptation_report"

    def test_empty_inputs_no_crash(self):
        report = ca.generate_adaptation_report()
        assert report["rollout_status"] == ca.POLICY_DISABLED

    def test_rollback_reflected_in_safeguard_status(self):
        curr  = _comp(live_brier=0.30)
        prior = _prior_comp(live_brier=0.22)
        report = ca.generate_adaptation_report(
            shadow_report=_shadow_report(),
            comparison=curr, prior_comparison=prior,
        )
        assert report["rollback_check"]["should_rollback"]
        assert report["safeguard_status"]["rollback_active"]

    def test_adaptation_step_present_when_wa_given(self):
        report = ca.generate_adaptation_report(
            shadow_report=_shadow_report(),
            weight_adjustments=_wa({"options": 0.05}),
        )
        assert report["adaptation_step"] is not None

    def test_adaptation_step_none_when_no_wa(self):
        report = ca.generate_adaptation_report(shadow_report=_shadow_report())
        assert report["adaptation_step"] is None

    def test_history_capped_at_max(self):
        big_hist = [_history_entry() for _ in range(30)]
        report   = ca.generate_adaptation_report(history=big_hist)
        assert len(report["history"]) <= ca.MAX_HISTORY_ENTRIES

    def test_recommendations_capped(self):
        report = ca.generate_adaptation_report()
        assert len(report["recommendations"]) <= ca.MAX_RECOMMENDATIONS

    def test_changes_vs_previous_empty_without_prev(self):
        report = ca.generate_adaptation_report(shadow_report=_shadow_report())
        assert report["changes_vs_previous"] == []

    def test_policy_change_detected(self):
        prev = {
            "rollout_status":     ca.POLICY_DISABLED,
            "rollout_confidence": {"rollout_confidence": 0},
        }
        report = ca.generate_adaptation_report(
            shadow_report=_shadow_report(readiness=READINESS_LIMITED),
            rows_since_last=25, rows_since_rollback=50,
            previous_report=prev,
        )
        policy_changes = [c for c in report["changes_vs_previous"]
                          if c["type"] == "POLICY_CHANGE"]
        assert len(policy_changes) == 1
        assert policy_changes[0]["direction"] == "PROMOTED"

    def test_rollout_status_matches_gate_policy(self):
        sr     = _shadow_report(readiness=READINESS_LIMITED)
        report = ca.generate_adaptation_report(
            shadow_report=sr, rows_since_last=25, rows_since_rollback=50
        )
        assert report["rollout_status"] == report["gate"]["policy"]


# ── TestCooldownBehavior ──────────────────────────────────────────────────────

class TestCooldownBehavior:
    def test_blocked_within_cooldown(self):
        sr = _shadow_report(readiness=READINESS_LIMITED)
        g  = ca.check_adaptation_gate(sr, rows_since_last=5, cooldown_rows=20)
        assert not g["allowed"]
        assert any("cooldown" in b.lower() for b in g["blockers"])

    def test_allowed_after_cooldown(self):
        sr = _shadow_report(readiness=READINESS_LIMITED)
        g  = ca.check_adaptation_gate(sr, rows_since_last=25, cooldown_rows=20)
        cooldown_blocker = any("cooldown" in b.lower() for b in g["blockers"])
        assert not cooldown_blocker

    def test_rollback_lockout_blocks_adaptation(self):
        sr = _shadow_report(readiness=READINESS_LIMITED)
        g  = ca.check_adaptation_gate(sr, rows_since_rollback=5,
                                       rollback_lockout_rows=40)
        assert not g["allowed"]
        assert any("lockout" in b.lower() for b in g["blockers"])

    def test_rollback_lockout_lifted_after_period(self):
        sr = _shadow_report(readiness=READINESS_LIMITED)
        g  = ca.check_adaptation_gate(sr, rows_since_rollback=50,
                                       rollback_lockout_rows=40)
        lockout_blocker = any("lockout" in b.lower() for b in g["blockers"])
        assert not lockout_blocker


# ── TestDeterminism ───────────────────────────────────────────────────────────

class TestDeterminism:
    def test_apply_controlled_weights_deterministic(self):
        proposed = {sig: DEFAULT_WEIGHTS[sig] + 0.08 for sig in SIGNAL_NAMES}
        r1 = ca.apply_controlled_weights(proposed)
        r2 = ca.apply_controlled_weights(proposed)
        assert r1 == r2

    def test_check_adaptation_gate_deterministic(self):
        sr = _shadow_report(readiness=READINESS_LIMITED)
        g1 = ca.check_adaptation_gate(sr, rows_since_last=25)
        g2 = ca.check_adaptation_gate(sr, rows_since_last=25)
        assert g1 == g2

    def test_generate_report_deterministic(self):
        sr   = _shadow_report()
        hist = [_history_entry()]
        r1   = ca.generate_adaptation_report(shadow_report=sr, history=hist)
        r2   = ca.generate_adaptation_report(shadow_report=sr, history=hist)
        assert r1["rollout_status"]              == r2["rollout_status"]
        assert r1["rollout_confidence"]          == r2["rollout_confidence"]
        assert r1["rollback_check"]["should_rollback"] == \
               r2["rollback_check"]["should_rollback"]


# ── TestBoundedOutputs ────────────────────────────────────────────────────────

class TestBoundedOutputs:
    def test_history_entries_capped(self):
        hist   = [_history_entry() for _ in range(30)]
        report = ca.generate_adaptation_report(history=hist)
        assert len(report["history"]) <= ca.MAX_HISTORY_ENTRIES

    def test_trigger_list_capped(self):
        # All 6 triggers firing at once
        curr  = _comp(live_wr=50.0, live_brier=0.30, live_dd=15.0, churn=0.35)
        prior = _prior_comp(live_wr=65.0, live_brier=0.22, live_dd=5.0)
        stab  = {"overall": STABILITY_UNSTABLE}
        result = ca.check_rollback_triggers(curr, stab, _hub("CRITICAL"), prior)
        assert len(result["triggers"]) <= ca.MAX_TRIGGER_ENTRIES

    def test_recommendations_capped(self):
        # Build a report with many potential recommendations
        sr     = _shadow_report(readiness=READINESS_NOT_READY, stability=STABILITY_UNSTABLE,
                                 n_rows=5)
        hist   = [_rollback_entry() for _ in range(5)]
        report = ca.generate_adaptation_report(shadow_report=sr, history=hist)
        assert len(report["recommendations"]) <= ca.MAX_RECOMMENDATIONS


# ── TestSparseHandling ────────────────────────────────────────────────────────

class TestSparseHandling:
    def test_none_shadow_report_no_crash(self):
        g = ca.check_adaptation_gate(None)
        assert not g["allowed"]

    def test_none_hub_report_no_crash(self):
        sr = _shadow_report(readiness=READINESS_LIMITED)
        g  = ca.check_adaptation_gate(sr, None)
        assert isinstance(g["allowed"], bool)

    def test_none_comparison_no_crash(self):
        result = ca.check_rollback_triggers(None, None, None, None)
        assert result["should_rollback"] is False

    def test_none_history_impact_no_crash(self):
        result = ca.analyze_adaptation_impact(None, {})
        assert result["n_adaptations"] == 0

    def test_none_wa_compute_step_returns_defaults(self):
        step = ca.compute_adaptation_step(None, _shadow_report())
        for sig in SIGNAL_NAMES:
            assert step["clamped_weights"][sig] == pytest.approx(DEFAULT_WEIGHTS[sig])

    def test_generate_report_all_none(self):
        report = ca.generate_adaptation_report()
        assert report["report_type"] == "controlled_adaptation_report"

    def test_rollout_confidence_none_inputs(self):
        result = ca.evaluate_rollout_confidence(None, None, None)
        assert 0 <= result["rollout_confidence"] <= 100

    def test_missing_fields_in_shadow_report_handled(self):
        partial_sr = {"n_rows": 40}  # missing readiness, stability
        g = ca.check_adaptation_gate(partial_sr)
        assert isinstance(g["allowed"], bool)

    def test_current_adjustments_none_weights(self):
        adj = ca._current_adjustments(None)
        for sig in SIGNAL_NAMES:
            assert adj[sig]["delta"] == pytest.approx(0.0)
