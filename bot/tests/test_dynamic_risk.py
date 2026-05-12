"""
Unit tests for dynamic_risk.py (Phase 5C).

All tests pass data directly — no DB, no network.
Tests cover: escalation, de-escalation, safeguards, policy clamps,
cooldown enforcement, repeated-degradation lockdown, determinism,
sparse handling, and full report generation.
"""
import pytest
import dynamic_risk as dr
from dynamic_risk import (
    MODE_NORMAL, MODE_DEFENSIVE, MODE_REDUCED, MODE_CRITICAL, MODE_LOCKDOWN,
    MODE_ORDER,
    SAFEGUARD_FREEZE_ADAPTATION, SAFEGUARD_BLOCK_NEW_ENTRIES,
    SAFEGUARD_REDUCE_EXPOSURE, SAFEGUARD_LIQUIDATE_WEAKEST,
    SAFEGUARD_FORCE_OBSERVATION, SAFEGUARD_TIGHTEN_THRESHOLDS,
    DRAWDOWN_DEFENSIVE_PCT, DRAWDOWN_REDUCED_PCT, DRAWDOWN_CRITICAL_PCT,
    DRAWDOWN_LOCKDOWN_PCT,
    ECE_DEFENSIVE, ECE_REDUCED, ECE_CRITICAL,
    CHURN_DEFENSIVE, CHURN_REDUCED,
    VOL_DEFENSIVE, VOL_REDUCED,
    REPEATED_CRITICAL_THRESHOLD,
    MIN_COOLDOWN_ROWS, LOCKDOWN_COOLDOWN_ROWS,
    MAX_RISK_EVENTS, MAX_RECOMMENDATIONS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _inputs(**kwargs) -> dict:
    """Build a flat risk_inputs dict with healthy defaults."""
    base = {
        "hub_health":        "HEALTHY",
        "drawdown_pct":      0.0,
        "rolling_vol":       None,
        "exposure_pct":      50.0,
        "is_risk_off":       False,
        "ece":               None,
        "churn_rate":        0.0,
        "stability":         "STABLE",
        "readiness":         "LIMITED_TRIAL_READY",
        "adaptation_policy": "LIMITED_TRIAL",
        "n_shadow_rows":     40,
    }
    base.update(kwargs)
    return base


def _state(
    mode="NORMAL",
    mode_since_row=0,
    row_idx=0,
    consecutive_critical=0,
    risk_events=None,
) -> dict:
    s = dr.create_risk_state()
    s["mode"]                     = mode
    s["mode_since_row"]           = mode_since_row
    s["row_idx"]                  = row_idx
    s["consecutive_critical_hub"] = consecutive_critical
    s["risk_events"]              = risk_events or []
    return s


def _tick(state, ri, row_idx=None):
    """Convenience: call process_risk_tick and return the result."""
    idx = row_idx if row_idx is not None else (state["row_idx"] + 1)
    return dr.process_risk_tick(state, ri, idx)


# ── TestModeConstants ─────────────────────────────────────────────────────────

class TestModeConstants:
    def test_mode_strings(self):
        assert MODE_NORMAL    == "NORMAL"
        assert MODE_DEFENSIVE == "DEFENSIVE"
        assert MODE_REDUCED   == "REDUCED"
        assert MODE_CRITICAL  == "CRITICAL"
        assert MODE_LOCKDOWN  == "LOCKDOWN"

    def test_mode_order_ascending(self):
        levels = [MODE_ORDER[m] for m in
                  (MODE_NORMAL, MODE_DEFENSIVE, MODE_REDUCED, MODE_CRITICAL, MODE_LOCKDOWN)]
        assert levels == sorted(levels)

    def test_safeguard_strings_unique(self):
        sg = [
            SAFEGUARD_FREEZE_ADAPTATION, SAFEGUARD_BLOCK_NEW_ENTRIES,
            SAFEGUARD_REDUCE_EXPOSURE, SAFEGUARD_LIQUIDATE_WEAKEST,
            SAFEGUARD_FORCE_OBSERVATION, SAFEGUARD_TIGHTEN_THRESHOLDS,
        ]
        assert len(sg) == len(set(sg))

    def test_threshold_ordering(self):
        assert DRAWDOWN_DEFENSIVE_PCT < DRAWDOWN_REDUCED_PCT < DRAWDOWN_CRITICAL_PCT < DRAWDOWN_LOCKDOWN_PCT
        assert ECE_DEFENSIVE < ECE_REDUCED < ECE_CRITICAL
        assert CHURN_DEFENSIVE < CHURN_REDUCED
        assert VOL_DEFENSIVE < VOL_REDUCED


# ── TestCreateRiskState ───────────────────────────────────────────────────────

class TestCreateRiskState:
    def test_initial_mode_normal(self):
        assert dr.create_risk_state()["mode"] == MODE_NORMAL

    def test_initial_consecutive_critical_zero(self):
        assert dr.create_risk_state()["consecutive_critical_hub"] == 0

    def test_initial_risk_events_empty(self):
        assert dr.create_risk_state()["risk_events"] == []

    def test_initial_row_idx_zero(self):
        assert dr.create_risk_state()["row_idx"] == 0


# ── TestEvaluateRiskInputs ────────────────────────────────────────────────────

class TestEvaluateRiskInputs:
    def test_all_none_returns_safe_defaults(self):
        ri = dr.evaluate_risk_inputs()
        assert ri["hub_health"]    == "HEALTHY"
        assert ri["drawdown_pct"]  == 0.0
        assert ri["churn_rate"]    == 0.0
        assert ri["stability"]     == "STABLE"
        assert ri["is_risk_off"]   is False
        assert ri["ece"]           is None

    def test_hub_health_extracted(self):
        ri = dr.evaluate_risk_inputs(hub_report={"overall_health": "CRITICAL"})
        assert ri["hub_health"] == "CRITICAL"

    def test_paper_metrics_extracted(self):
        ri = dr.evaluate_risk_inputs(paper_metrics={
            "drawdown_pct": 15.0, "rolling_vol": 4.5,
            "exposure_pct": 80.0, "risk_off": True,
        })
        assert ri["drawdown_pct"] == 15.0
        assert ri["rolling_vol"]  == 4.5
        assert ri["is_risk_off"]  is True

    def test_shadow_report_extracted(self):
        ri = dr.evaluate_risk_inputs(shadow_report={
            "n_rows":     50,
            "comparison": {"churn_rate": 0.35},
            "stability":  {"overall": "UNSTABLE"},
            "readiness":  {"status": "STABLE_FOR_CONTROLLED_USE"},
        })
        assert ri["n_shadow_rows"] == 50
        assert ri["churn_rate"]    == 0.35
        assert ri["stability"]     == "UNSTABLE"
        assert ri["readiness"]     == "STABLE_FOR_CONTROLLED_USE"

    def test_ece_extracted_from_hub_subsystem(self):
        hub = {
            "overall_health": "WATCH",
            "subsystem_statuses": {
                "calibration": {"extracted": {"ece": 0.18}}
            },
        }
        ri = dr.evaluate_risk_inputs(hub_report=hub)
        assert ri["ece"] == pytest.approx(0.18)

    def test_adaptation_policy_extracted(self):
        ri = dr.evaluate_risk_inputs(
            adaptation_gate={"policy": "CONTROLLED_ACTIVE"}
        )
        assert ri["adaptation_policy"] == "CONTROLLED_ACTIVE"


# ── TestRequiredModeFromTriggers ──────────────────────────────────────────────

class TestRequiredModeFromTriggers:
    def test_healthy_inputs_normal(self):
        assert dr._required_mode_from_triggers(_inputs()) == MODE_NORMAL

    def test_drawdown_defensive(self):
        m = dr._required_mode_from_triggers(_inputs(drawdown_pct=DRAWDOWN_DEFENSIVE_PCT))
        assert m == MODE_DEFENSIVE

    def test_drawdown_reduced(self):
        m = dr._required_mode_from_triggers(_inputs(drawdown_pct=DRAWDOWN_REDUCED_PCT))
        assert m == MODE_REDUCED

    def test_drawdown_critical(self):
        m = dr._required_mode_from_triggers(_inputs(drawdown_pct=DRAWDOWN_CRITICAL_PCT))
        assert m == MODE_CRITICAL

    def test_drawdown_lockdown(self):
        m = dr._required_mode_from_triggers(_inputs(drawdown_pct=DRAWDOWN_LOCKDOWN_PCT))
        assert m == MODE_LOCKDOWN

    def test_hub_critical_requires_critical(self):
        m = dr._required_mode_from_triggers(_inputs(hub_health="CRITICAL"))
        assert m == MODE_CRITICAL

    def test_hub_degraded_requires_reduced(self):
        m = dr._required_mode_from_triggers(_inputs(hub_health="DEGRADED"))
        assert m == MODE_REDUCED

    def test_hub_watch_requires_defensive(self):
        m = dr._required_mode_from_triggers(_inputs(hub_health="WATCH"))
        assert m == MODE_DEFENSIVE

    def test_ece_defensive(self):
        m = dr._required_mode_from_triggers(_inputs(ece=ECE_DEFENSIVE))
        assert m == MODE_DEFENSIVE

    def test_ece_reduced(self):
        m = dr._required_mode_from_triggers(_inputs(ece=ECE_REDUCED))
        assert m == MODE_REDUCED

    def test_ece_critical(self):
        m = dr._required_mode_from_triggers(_inputs(ece=ECE_CRITICAL))
        assert m == MODE_CRITICAL

    def test_churn_defensive(self):
        m = dr._required_mode_from_triggers(_inputs(churn_rate=CHURN_DEFENSIVE))
        assert m == MODE_DEFENSIVE

    def test_churn_reduced(self):
        m = dr._required_mode_from_triggers(_inputs(churn_rate=CHURN_REDUCED))
        assert m == MODE_REDUCED

    def test_vol_defensive(self):
        m = dr._required_mode_from_triggers(_inputs(rolling_vol=VOL_DEFENSIVE))
        assert m == MODE_DEFENSIVE

    def test_vol_reduced(self):
        m = dr._required_mode_from_triggers(_inputs(rolling_vol=VOL_REDUCED))
        assert m == MODE_REDUCED

    def test_unstable_stability_defensive(self):
        m = dr._required_mode_from_triggers(_inputs(stability="UNSTABLE"))
        assert m == MODE_DEFENSIVE

    def test_risk_off_defensive(self):
        m = dr._required_mode_from_triggers(_inputs(is_risk_off=True))
        assert m == MODE_DEFENSIVE

    def test_worst_trigger_wins(self):
        # hub CRITICAL + drawdown DEFENSIVE → CRITICAL wins
        m = dr._required_mode_from_triggers(_inputs(
            hub_health="CRITICAL", drawdown_pct=DRAWDOWN_DEFENSIVE_PCT
        ))
        assert m == MODE_CRITICAL

    def test_multiple_triggers_worst_wins(self):
        m = dr._required_mode_from_triggers(_inputs(
            hub_health="DEGRADED",          # REDUCED
            drawdown_pct=DRAWDOWN_LOCKDOWN_PCT,  # LOCKDOWN
        ))
        assert m == MODE_LOCKDOWN


# ── TestComputeRiskMode ───────────────────────────────────────────────────────

class TestComputeRiskMode:
    def test_healthy_stays_normal(self):
        r = dr.compute_risk_mode(_inputs(), MODE_NORMAL, 0, 5)
        assert r["target_mode"] == MODE_NORMAL
        assert r["escalating"]  is False

    def test_high_drawdown_escalates(self):
        r = dr.compute_risk_mode(
            _inputs(drawdown_pct=DRAWDOWN_REDUCED_PCT),
            MODE_NORMAL, 0, 1,
        )
        assert r["target_mode"] == MODE_REDUCED
        assert r["escalating"]  is True

    def test_escalation_can_jump_multiple_levels(self):
        r = dr.compute_risk_mode(
            _inputs(drawdown_pct=DRAWDOWN_LOCKDOWN_PCT),
            MODE_NORMAL, 0, 1,
        )
        assert r["target_mode"] == MODE_LOCKDOWN

    def test_repeated_critical_hub_forces_lockdown(self):
        r = dr.compute_risk_mode(
            _inputs(hub_health="CRITICAL"),
            MODE_CRITICAL, 0, 1,
            consecutive_critical=REPEATED_CRITICAL_THRESHOLD,
        )
        assert r["target_mode"] == MODE_LOCKDOWN

    def test_deescalation_blocked_by_cooldown(self):
        # rows_in_mode = 5 < MIN_COOLDOWN_ROWS = 10
        r = dr.compute_risk_mode(_inputs(), MODE_DEFENSIVE, 0, 5)
        assert r["target_mode"] == MODE_DEFENSIVE
        assert r["deescalating"] is False
        assert r["cooldown_remaining"] > 0

    def test_deescalation_allowed_after_cooldown(self):
        r = dr.compute_risk_mode(
            _inputs(), MODE_DEFENSIVE, 0, MIN_COOLDOWN_ROWS,
        )
        assert r["deescalating"]  is True
        assert r["target_mode"]   == MODE_NORMAL

    def test_deescalation_one_step_only(self):
        # In LOCKDOWN with all triggers clear → drops to CRITICAL, not NORMAL
        r = dr.compute_risk_mode(
            _inputs(), MODE_LOCKDOWN, 0, LOCKDOWN_COOLDOWN_ROWS,
        )
        assert r["target_mode"] == MODE_CRITICAL

    def test_lockdown_cooldown_longer(self):
        # rows_in_mode = MIN_COOLDOWN_ROWS — not enough for LOCKDOWN
        r = dr.compute_risk_mode(
            _inputs(), MODE_LOCKDOWN, 0, MIN_COOLDOWN_ROWS,
        )
        assert r["target_mode"] == MODE_LOCKDOWN
        assert r["cooldown_remaining"] > 0


# ── TestApplyModePolicy ───────────────────────────────────────────────────────

class TestApplyModePolicy:
    def test_normal_all_multipliers_one(self):
        p = dr.apply_mode_policy(MODE_NORMAL)
        assert p["confidence_multiplier"]    == 1.0
        assert p["max_exposure_multiplier"]  == 1.0
        assert p["max_positions_multiplier"] == 1.0
        assert p["position_size_multiplier"] == 1.0
        assert p["score_threshold_delta"]    == 0.0
        assert p["adaptation_allowed"]       is True
        assert p["regime_suppression"]       is False

    def test_defensive_reduces_exposure(self):
        p = dr.apply_mode_policy(MODE_DEFENSIVE)
        assert p["max_exposure_multiplier"]  < 1.0
        assert p["position_size_multiplier"] < 1.0
        assert p["adaptation_allowed"]       is True

    def test_reduced_freezes_adaptation(self):
        p = dr.apply_mode_policy(MODE_REDUCED)
        assert p["adaptation_allowed"]  is False
        assert p["regime_suppression"]  is True

    def test_critical_exposure_below_reduced(self):
        p_reduced  = dr.apply_mode_policy(MODE_REDUCED)
        p_critical = dr.apply_mode_policy(MODE_CRITICAL)
        assert p_critical["max_exposure_multiplier"] < p_reduced["max_exposure_multiplier"]

    def test_lockdown_minimal_exposure(self):
        p = dr.apply_mode_policy(MODE_LOCKDOWN)
        assert p["max_exposure_multiplier"]  <= 0.10
        assert p["position_size_multiplier"] <= 0.10
        assert p["confidence_multiplier"]    >= 1.5
        assert p["adaptation_allowed"]       is False

    def test_policy_severity_monotone(self):
        modes = [MODE_NORMAL, MODE_DEFENSIVE, MODE_REDUCED, MODE_CRITICAL, MODE_LOCKDOWN]
        exps  = [dr.apply_mode_policy(m)["max_exposure_multiplier"] for m in modes]
        sizes = [dr.apply_mode_policy(m)["position_size_multiplier"] for m in modes]
        confs = [dr.apply_mode_policy(m)["confidence_multiplier"] for m in modes]
        assert exps  == sorted(exps,  reverse=True)   # decreasing
        assert sizes == sorted(sizes, reverse=True)
        assert confs == sorted(confs)                  # increasing

    def test_unknown_mode_returns_normal_policy(self):
        p = dr.apply_mode_policy("NONEXISTENT")
        assert p == dr.apply_mode_policy(MODE_NORMAL)

    def test_returns_copy_not_reference(self):
        p1 = dr.apply_mode_policy(MODE_NORMAL)
        p2 = dr.apply_mode_policy(MODE_NORMAL)
        p1["confidence_multiplier"] = 99.0
        assert p2["confidence_multiplier"] != 99.0


# ── TestDetermineSafeguards ───────────────────────────────────────────────────

class TestDetermineSafeguards:
    def test_normal_no_safeguards(self):
        sg = dr.determine_safeguards(MODE_NORMAL, _inputs())
        assert sg == []

    def test_defensive_tightens_and_reduces(self):
        sg = dr.determine_safeguards(MODE_DEFENSIVE, _inputs())
        assert SAFEGUARD_TIGHTEN_THRESHOLDS in sg
        assert SAFEGUARD_REDUCE_EXPOSURE    in sg

    def test_defensive_freezes_when_unstable(self):
        sg = dr.determine_safeguards(MODE_DEFENSIVE, _inputs(stability="UNSTABLE"))
        assert SAFEGUARD_FREEZE_ADAPTATION in sg

    def test_reduced_freezes_and_forces_observation(self):
        sg = dr.determine_safeguards(MODE_REDUCED, _inputs())
        assert SAFEGUARD_FREEZE_ADAPTATION in sg
        assert SAFEGUARD_FORCE_OBSERVATION in sg

    def test_critical_blocks_new_entries(self):
        sg = dr.determine_safeguards(MODE_CRITICAL, _inputs())
        assert SAFEGUARD_BLOCK_NEW_ENTRIES in sg

    def test_critical_liquidates_weakest(self):
        sg = dr.determine_safeguards(MODE_CRITICAL, _inputs())
        assert SAFEGUARD_LIQUIDATE_WEAKEST in sg

    def test_lockdown_all_major_safeguards(self):
        sg = dr.determine_safeguards(MODE_LOCKDOWN, _inputs())
        for s in (SAFEGUARD_FREEZE_ADAPTATION, SAFEGUARD_BLOCK_NEW_ENTRIES,
                  SAFEGUARD_REDUCE_EXPOSURE, SAFEGUARD_LIQUIDATE_WEAKEST,
                  SAFEGUARD_FORCE_OBSERVATION, SAFEGUARD_TIGHTEN_THRESHOLDS):
            assert s in sg

    def test_no_duplicates(self):
        sg = dr.determine_safeguards(MODE_LOCKDOWN, _inputs(stability="UNSTABLE"))
        assert len(sg) == len(set(sg))


# ── TestRecoveryReadiness ─────────────────────────────────────────────────────

class TestRecoveryReadiness:
    def test_normal_always_ready(self):
        r = dr.compute_recovery_readiness(_inputs(), MODE_NORMAL, 0, 0)
        assert r["ready"] is True

    def test_blocked_by_cooldown(self):
        r = dr.compute_recovery_readiness(_inputs(), MODE_DEFENSIVE, 0, 5)
        assert r["ready"] is False
        assert any("cooldown" in b for b in r["blockers"])

    def test_blocked_by_active_triggers(self):
        ri = _inputs(drawdown_pct=DRAWDOWN_REDUCED_PCT)
        r  = dr.compute_recovery_readiness(ri, MODE_REDUCED, 0, MIN_COOLDOWN_ROWS + 5)
        assert r["ready"] is False
        assert any("triggers_active" in b for b in r["blockers"])

    def test_ready_when_cooldown_elapsed_and_triggers_clear(self):
        r = dr.compute_recovery_readiness(_inputs(), MODE_DEFENSIVE, 0, MIN_COOLDOWN_ROWS)
        assert r["ready"] is True
        assert r["blockers"] == []

    def test_lockdown_requires_longer_cooldown(self):
        r = dr.compute_recovery_readiness(_inputs(), MODE_LOCKDOWN, 0, MIN_COOLDOWN_ROWS)
        assert r["ready"] is False
        assert r["cooldown_rows"] == LOCKDOWN_COOLDOWN_ROWS

    def test_rows_in_mode_computed(self):
        r = dr.compute_recovery_readiness(_inputs(), MODE_DEFENSIVE, 5, 12)
        assert r["rows_in_mode"] == 7


# ── TestEscalationCorrectness ─────────────────────────────────────────────────

class TestEscalationCorrectness:
    def test_drawdown_spike_escalates(self):
        s  = _state()
        r  = _tick(s, _inputs(drawdown_pct=DRAWDOWN_REDUCED_PCT), row_idx=1)
        assert r["new_state"]["mode"] == MODE_REDUCED
        assert r["mode_changed"]      is True

    def test_hub_critical_escalates_to_critical(self):
        s = _state()
        r = _tick(s, _inputs(hub_health="CRITICAL"), row_idx=1)
        assert r["new_state"]["mode"] == MODE_CRITICAL

    def test_calibration_collapse_escalates(self):
        s = _state()
        r = _tick(s, _inputs(ece=ECE_REDUCED), row_idx=1)
        assert r["new_state"]["mode"] == MODE_REDUCED

    def test_excessive_churn_escalates(self):
        s = _state()
        r = _tick(s, _inputs(churn_rate=CHURN_REDUCED), row_idx=1)
        assert r["new_state"]["mode"] == MODE_REDUCED

    def test_vol_expansion_escalates(self):
        s = _state()
        r = _tick(s, _inputs(rolling_vol=VOL_DEFENSIVE), row_idx=1)
        assert r["new_state"]["mode"] == MODE_DEFENSIVE

    def test_unstable_adaptive_escalates(self):
        s = _state()
        r = _tick(s, _inputs(stability="UNSTABLE"), row_idx=1)
        assert r["new_state"]["mode"] == MODE_DEFENSIVE

    def test_risk_off_flag_escalates(self):
        s = _state()
        r = _tick(s, _inputs(is_risk_off=True), row_idx=1)
        assert r["new_state"]["mode"] == MODE_DEFENSIVE

    def test_escalation_records_event(self):
        s = _state()
        r = _tick(s, _inputs(drawdown_pct=DRAWDOWN_REDUCED_PCT), row_idx=1)
        assert len(r["new_events"]) >= 1
        assert r["new_events"][0]["event_type"] == "ESCALATION"

    def test_escalation_event_has_triggers(self):
        s = _state()
        r = _tick(s, _inputs(drawdown_pct=DRAWDOWN_REDUCED_PCT), row_idx=1)
        evt = r["new_events"][0]
        assert any("drawdown" in t for t in evt["triggers"])


# ── TestDeescalationCorrectness ───────────────────────────────────────────────

class TestDeescalationCorrectness:
    def test_no_deescalation_before_cooldown(self):
        s = _state(mode=MODE_DEFENSIVE, mode_since_row=0)
        r = _tick(s, _inputs(), row_idx=MIN_COOLDOWN_ROWS - 1)
        assert r["new_state"]["mode"] == MODE_DEFENSIVE
        assert r["mode_changed"] is False

    def test_deescalation_after_cooldown(self):
        s = _state(mode=MODE_DEFENSIVE, mode_since_row=0)
        r = _tick(s, _inputs(), row_idx=MIN_COOLDOWN_ROWS)
        assert r["new_state"]["mode"] == MODE_NORMAL
        assert r["mode_changed"] is True

    def test_deescalation_one_step_from_critical(self):
        s = _state(mode=MODE_CRITICAL, mode_since_row=0)
        r = _tick(s, _inputs(), row_idx=MIN_COOLDOWN_ROWS)
        # CRITICAL → REDUCED (one step)
        assert r["new_state"]["mode"] == MODE_REDUCED

    def test_deescalation_blocked_by_trigger(self):
        # Cooldown elapsed but drawdown still elevated
        s  = _state(mode=MODE_DEFENSIVE, mode_since_row=0)
        ri = _inputs(drawdown_pct=DRAWDOWN_DEFENSIVE_PCT)
        r  = _tick(s, ri, row_idx=MIN_COOLDOWN_ROWS)
        assert r["new_state"]["mode"] == MODE_DEFENSIVE

    def test_deescalation_records_event(self):
        s = _state(mode=MODE_DEFENSIVE, mode_since_row=0)
        r = _tick(s, _inputs(), row_idx=MIN_COOLDOWN_ROWS)
        assert r["new_events"][0]["event_type"] == "DEESCALATION"

    def test_lockdown_deescalation_requires_longer_cooldown(self):
        s = _state(mode=MODE_LOCKDOWN, mode_since_row=0)
        r = _tick(s, _inputs(), row_idx=MIN_COOLDOWN_ROWS)
        assert r["new_state"]["mode"] == MODE_LOCKDOWN  # not yet

    def test_lockdown_deescalation_after_long_cooldown(self):
        s = _state(mode=MODE_LOCKDOWN, mode_since_row=0)
        r = _tick(s, _inputs(), row_idx=LOCKDOWN_COOLDOWN_ROWS)
        assert r["new_state"]["mode"] == MODE_CRITICAL  # one step down


# ── TestRepeatedDegradation ───────────────────────────────────────────────────

class TestRepeatedDegradation:
    def test_consecutive_critical_increments(self):
        s = _state()
        r = _tick(s, _inputs(hub_health="CRITICAL"), row_idx=1)
        assert r["new_state"]["consecutive_critical_hub"] == 1

    def test_consecutive_critical_resets_on_healthy(self):
        s = _state(consecutive_critical=2)
        r = _tick(s, _inputs(hub_health="HEALTHY"), row_idx=1)
        assert r["new_state"]["consecutive_critical_hub"] == 0

    def test_repeated_critical_triggers_lockdown(self):
        s = _state(
            mode=MODE_CRITICAL,
            mode_since_row=0,
            consecutive_critical=REPEATED_CRITICAL_THRESHOLD - 1,
        )
        # This tick brings consecutive to THRESHOLD → LOCKDOWN
        r = _tick(s, _inputs(hub_health="CRITICAL"), row_idx=1)
        assert r["new_state"]["mode"] == MODE_LOCKDOWN

    def test_below_threshold_no_lockdown(self):
        s = _state(
            mode=MODE_CRITICAL,
            consecutive_critical=REPEATED_CRITICAL_THRESHOLD - 2,
        )
        r = _tick(s, _inputs(hub_health="CRITICAL"), row_idx=1)
        assert r["new_state"]["mode"] == MODE_CRITICAL  # not yet lockdown


# ── TestSafeguardCoordination ─────────────────────────────────────────────────

class TestSafeguardCoordination:
    def test_normal_no_safeguards(self):
        s = _state()
        r = _tick(s, _inputs(), row_idx=1)
        assert r["active_safeguards"] == []

    def test_defensive_has_safeguards(self):
        s = _state()
        r = _tick(s, _inputs(drawdown_pct=DRAWDOWN_DEFENSIVE_PCT), row_idx=1)
        assert SAFEGUARD_TIGHTEN_THRESHOLDS in r["active_safeguards"]
        assert SAFEGUARD_REDUCE_EXPOSURE    in r["active_safeguards"]

    def test_lockdown_has_all_safeguards(self):
        s  = _state()
        r  = _tick(s, _inputs(drawdown_pct=DRAWDOWN_LOCKDOWN_PCT), row_idx=1)
        for sg in (
            SAFEGUARD_FREEZE_ADAPTATION, SAFEGUARD_BLOCK_NEW_ENTRIES,
            SAFEGUARD_REDUCE_EXPOSURE, SAFEGUARD_LIQUIDATE_WEAKEST,
            SAFEGUARD_FORCE_OBSERVATION, SAFEGUARD_TIGHTEN_THRESHOLDS,
        ):
            assert sg in r["active_safeguards"]

    def test_freeze_adaptation_in_reduced_mode(self):
        s = _state()
        r = _tick(s, _inputs(drawdown_pct=DRAWDOWN_REDUCED_PCT), row_idx=1)
        assert SAFEGUARD_FREEZE_ADAPTATION in r["active_safeguards"]

    def test_block_new_entries_not_in_reduced(self):
        s = _state()
        r = _tick(s, _inputs(drawdown_pct=DRAWDOWN_REDUCED_PCT), row_idx=1)
        assert SAFEGUARD_BLOCK_NEW_ENTRIES not in r["active_safeguards"]


# ── TestClampEnforcement ──────────────────────────────────────────────────────

class TestClampEnforcement:
    def test_all_multipliers_positive(self):
        for mode in (MODE_NORMAL, MODE_DEFENSIVE, MODE_REDUCED, MODE_CRITICAL, MODE_LOCKDOWN):
            p = dr.apply_mode_policy(mode)
            assert p["max_exposure_multiplier"]  > 0
            assert p["max_positions_multiplier"] > 0
            assert p["position_size_multiplier"] > 0
            assert p["confidence_multiplier"]    > 0

    def test_all_multipliers_le_one_in_non_normal(self):
        for mode in (MODE_DEFENSIVE, MODE_REDUCED, MODE_CRITICAL, MODE_LOCKDOWN):
            p = dr.apply_mode_policy(mode)
            assert p["max_exposure_multiplier"]  <= 1.0
            assert p["max_positions_multiplier"] <= 1.0
            assert p["position_size_multiplier"] <= 1.0

    def test_score_delta_non_negative(self):
        for mode in (MODE_NORMAL, MODE_DEFENSIVE, MODE_REDUCED, MODE_CRITICAL, MODE_LOCKDOWN):
            p = dr.apply_mode_policy(mode)
            assert p["score_threshold_delta"] >= 0.0

    def test_score_delta_escalates_with_mode(self):
        deltas = [dr.apply_mode_policy(m)["score_threshold_delta"] for m in
                  (MODE_NORMAL, MODE_DEFENSIVE, MODE_REDUCED, MODE_CRITICAL, MODE_LOCKDOWN)]
        assert deltas == sorted(deltas)

    def test_confidence_multiplier_ge_one(self):
        for mode in (MODE_NORMAL, MODE_DEFENSIVE, MODE_REDUCED, MODE_CRITICAL, MODE_LOCKDOWN):
            assert dr.apply_mode_policy(mode)["confidence_multiplier"] >= 1.0


# ── TestProcessRiskTick ───────────────────────────────────────────────────────

class TestProcessRiskTick:
    def test_row_idx_stored_in_new_state(self):
        s = _state()
        r = _tick(s, _inputs(), row_idx=7)
        assert r["new_state"]["row_idx"] == 7

    def test_input_state_not_mutated(self):
        s     = _state()
        orig  = s["mode"]
        _tick(s, _inputs(drawdown_pct=DRAWDOWN_LOCKDOWN_PCT), row_idx=1)
        assert s["mode"] == orig  # original unchanged

    def test_policy_returned_matches_mode(self):
        s = _state()
        r = _tick(s, _inputs(drawdown_pct=DRAWDOWN_REDUCED_PCT), row_idx=1)
        assert r["policy"] == dr.apply_mode_policy(MODE_REDUCED)

    def test_recommendations_non_empty_in_degraded_mode(self):
        s = _state()
        r = _tick(s, _inputs(drawdown_pct=DRAWDOWN_REDUCED_PCT), row_idx=1)
        assert len(r["recommendations"]) > 0

    def test_mode_unchanged_when_healthy(self):
        s = _state(mode=MODE_NORMAL)
        r = _tick(s, _inputs(), row_idx=1)
        assert r["mode_changed"] is False

    def test_risk_events_bounded(self):
        s = _state()
        for i in range(MAX_RISK_EVENTS + 10):
            s = _tick(s, _inputs(drawdown_pct=DRAWDOWN_DEFENSIVE_PCT), row_idx=i)["new_state"]
        assert len(s["risk_events"]) <= MAX_RISK_EVENTS


# ── TestCooldownBehavior ──────────────────────────────────────────────────────

class TestCooldownBehavior:
    def test_cannot_deescalate_before_cooldown(self):
        s = _state(mode=MODE_REDUCED, mode_since_row=0)
        for row in range(1, MIN_COOLDOWN_ROWS):
            s = _tick(s, _inputs(), row_idx=row)["new_state"]
        # Should still be in REDUCED after MIN_COOLDOWN_ROWS-1 rows
        assert s["mode"] == MODE_REDUCED

    def test_can_deescalate_exactly_at_cooldown(self):
        s = _state(mode=MODE_REDUCED, mode_since_row=0)
        r = _tick(s, _inputs(), row_idx=MIN_COOLDOWN_ROWS)
        assert r["new_state"]["mode"] == MODE_DEFENSIVE  # one step down

    def test_re_escalation_resets_since_row(self):
        # Escalate at row 5 → mode_since_row = 5
        s  = _state(mode=MODE_DEFENSIVE, mode_since_row=0)
        s2 = _tick(s, _inputs(drawdown_pct=DRAWDOWN_REDUCED_PCT), row_idx=5)["new_state"]
        assert s2["mode_since_row"] == 5

    def test_cooldown_remaining_reported(self):
        s = _state(mode=MODE_DEFENSIVE, mode_since_row=0)
        r = dr.compute_risk_mode(_inputs(), MODE_DEFENSIVE, 0, 3)
        assert r["cooldown_remaining"] == MIN_COOLDOWN_ROWS - 3


# ── TestReportGeneration ──────────────────────────────────────────────────────

class TestReportGeneration:
    def test_report_has_required_keys(self):
        r = dr.generate_report(dr.create_risk_state(), _inputs())
        for key in (
            "current_mode", "policy", "active_safeguards", "escalation_history",
            "stabilization_progress", "exposure_policy", "recovery_readiness",
            "operational_threats", "recommendations", "row_idx", "rows_in_mode",
        ):
            assert key in r

    def test_normal_state_clean_report(self):
        r = dr.generate_report(dr.create_risk_state(), _inputs())
        assert r["current_mode"]      == MODE_NORMAL
        assert r["active_safeguards"] == []
        assert r["operational_threats"] == []

    def test_escalation_history_only_escalate_deescalate(self):
        s = _state(risk_events=[
            {"event_type": "ESCALATION",    "from_mode": "NORMAL",    "to_mode": "DEFENSIVE", "row_idx": 1, "triggers": []},
            {"event_type": "TRIGGER_ACTIVE","from_mode": "DEFENSIVE", "to_mode": "DEFENSIVE", "row_idx": 2, "triggers": []},
            {"event_type": "DEESCALATION",  "from_mode": "DEFENSIVE", "to_mode": "NORMAL",    "row_idx": 3, "triggers": []},
        ])
        r = dr.generate_report(s, _inputs())
        types = [e["event_type"] for e in r["escalation_history"]]
        assert "TRIGGER_ACTIVE" not in types
        assert "ESCALATION"   in types
        assert "DEESCALATION" in types

    def test_exposure_policy_sub_dict(self):
        r = dr.generate_report(_state(mode=MODE_REDUCED), _inputs())
        ep = r["exposure_policy"]
        assert ep["max_exposure_multiplier"]  <= 0.60
        assert ep["max_positions_multiplier"] <= 0.70
        assert ep["position_size_multiplier"] <= 0.70

    def test_recommendations_present_in_degraded_mode(self):
        s = _state(mode=MODE_LOCKDOWN)
        r = dr.generate_report(s, _inputs(drawdown_pct=DRAWDOWN_LOCKDOWN_PCT))
        assert len(r["recommendations"]) > 0

    def test_rows_in_mode_computed(self):
        s = _state(mode=MODE_DEFENSIVE, mode_since_row=5, row_idx=15)
        r = dr.generate_report(s, _inputs())
        assert r["rows_in_mode"] == 10


# ── TestGenerateRecommendations ───────────────────────────────────────────────

class TestGenerateRecommendations:
    def test_normal_mode_no_recommendations(self):
        recs = dr.generate_recommendations(MODE_NORMAL, _inputs(), [])
        assert recs == []

    def test_lockdown_has_hold_recommendation(self):
        recs = dr.generate_recommendations(MODE_LOCKDOWN, _inputs(), [])
        assert any("lockdown" in r.lower() for r in recs)

    def test_unstable_signal_freeze_recommendation(self):
        recs = dr.generate_recommendations(MODE_DEFENSIVE, _inputs(stability="UNSTABLE"), [])
        assert any("unstable" in r.lower() or "freeze" in r.lower() for r in recs)

    def test_ece_triggers_calibration_recommendation(self):
        recs = dr.generate_recommendations(MODE_DEFENSIVE, _inputs(ece=ECE_DEFENSIVE + 0.01), [])
        assert any("calibration" in r.lower() or "ece" in r.lower() for r in recs)

    def test_drawdown_triggers_capital_preservation(self):
        recs = dr.generate_recommendations(MODE_CRITICAL, _inputs(drawdown_pct=DRAWDOWN_CRITICAL_PCT), [])
        assert any("drawdown" in r.lower() or "capital" in r.lower() for r in recs)

    def test_recommendations_bounded(self):
        recs = dr.generate_recommendations(
            MODE_LOCKDOWN,
            _inputs(drawdown_pct=DRAWDOWN_LOCKDOWN_PCT, ece=ECE_CRITICAL,
                    stability="UNSTABLE", churn_rate=CHURN_REDUCED, is_risk_off=True),
            list(dr._MODE_POLICY.keys()),
        )
        assert len(recs) <= MAX_RECOMMENDATIONS


# ── TestSparseHandling ────────────────────────────────────────────────────────

class TestSparseHandling:
    def test_process_tick_none_state_no_crash(self):
        r = dr.process_risk_tick(None, _inputs(), 0)
        assert "new_state" in r

    def test_process_tick_none_inputs_no_crash(self):
        r = dr.process_risk_tick(dr.create_risk_state(), None, 0)
        assert r["new_state"]["mode"] == MODE_NORMAL

    def test_evaluate_all_none_no_crash(self):
        ri = dr.evaluate_risk_inputs()
        assert ri["hub_health"] == "HEALTHY"

    def test_compute_risk_mode_none_inputs(self):
        r = dr.compute_risk_mode(None, MODE_NORMAL, 0, 1)
        assert r["target_mode"] == MODE_NORMAL

    def test_determine_safeguards_none_inputs(self):
        sg = dr.determine_safeguards(MODE_DEFENSIVE, None)
        assert isinstance(sg, list)

    def test_generate_report_no_crash_empty_state(self):
        r = dr.generate_report(dr.create_risk_state(), {})
        assert r["current_mode"] == MODE_NORMAL

    def test_rolling_vol_none_no_crash(self):
        r = _tick(dr.create_risk_state(), _inputs(rolling_vol=None), row_idx=1)
        assert r["new_state"]["mode"] == MODE_NORMAL

    def test_ece_none_no_crash(self):
        r = _tick(dr.create_risk_state(), _inputs(ece=None), row_idx=1)
        assert r["new_state"]["mode"] == MODE_NORMAL


# ── TestDeterminism ───────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_inputs_same_mode(self):
        ri = _inputs(drawdown_pct=DRAWDOWN_REDUCED_PCT)
        s  = dr.create_risk_state()
        r1 = _tick(s, ri, row_idx=1)
        r2 = _tick(s, ri, row_idx=1)
        assert r1["new_state"]["mode"] == r2["new_state"]["mode"]

    def test_same_sequence_same_events(self):
        ticks = [
            _inputs(drawdown_pct=DRAWDOWN_DEFENSIVE_PCT),
            _inputs(drawdown_pct=DRAWDOWN_REDUCED_PCT),
            _inputs(),
        ]
        def _run():
            s = dr.create_risk_state()
            for i, ri in enumerate(ticks, start=1):
                s = _tick(s, ri, row_idx=i)["new_state"]
            return s

        s1 = _run()
        s2 = _run()
        assert s1["mode"]       == s2["mode"]
        assert s1["row_idx"]    == s2["row_idx"]
        assert len(s1["risk_events"]) == len(s2["risk_events"])

    def test_copy_state_does_not_mutate_original(self):
        s  = dr.create_risk_state()
        r  = _tick(s, _inputs(drawdown_pct=DRAWDOWN_LOCKDOWN_PCT), row_idx=1)
        assert s["mode"] == MODE_NORMAL   # original untouched

    def test_required_mode_is_pure(self):
        ri = _inputs(drawdown_pct=DRAWDOWN_REDUCED_PCT)
        m1 = dr._required_mode_from_triggers(ri)
        m2 = dr._required_mode_from_triggers(ri)
        assert m1 == m2
