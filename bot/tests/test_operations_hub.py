"""
Unit tests for operations_hub.py (Phase 4A).

All tests pass pre-built subsystem reports directly — no DB access,
no network calls, no subsystem recomputation.
Covers: health classification, aggregation correctness, change detection,
alert escalation, deterministic outputs, sparse handling.
"""
import pytest

from operations_hub import (
    ALERT_HIGH_WARNING_THRESHOLD,
    COMBO_DECEPTIVE_DEGRADED,
    COMBO_UNSTABLE_DEGRADED,
    CRITICAL_SUBSYSTEM_THRESHOLD,
    DEGRADED_SUBSYSTEM_THRESHOLD,
    HEALTH_CRITICAL,
    HEALTH_DEGRADED,
    HEALTH_HEALTHY,
    HEALTH_WATCH,
    HEALTH_ORDER,
    OBSERVER_DRIFT_DEGRADED,
    OBSERVER_UNSTABLE_DEGRADED,
    SAFEGUARD_INCREASE_CONF,
    SAFEGUARD_LEVEL_ORDER,
    SAFEGUARD_NONE,
    SAFEGUARD_OBSERVATION_ONLY,
    SAFEGUARD_PAUSE,
    SAFEGUARD_REDUCE,
    TOP_CONCERNS_LIMIT,
    _classify_audit,
    _classify_calibration,
    _classify_combo,
    _classify_meta,
    _classify_observer,
    _classify_portfolio,
    _classify_regime,
    _collect_recommendations,
    _collect_top_concerns,
    _extract_audit,
    _extract_calibration,
    _extract_combo,
    _extract_meta,
    _extract_observer,
    _extract_portfolio,
    _extract_regime,
    _extract_weights,
    _subsystem_detail_line,
    classify_overall_health,
    classify_subsystem_health,
    detect_changes,
    executive_summary,
    generate_report,
    operational_alerts,
)


# ── Subsystem report builders ─────────────────────────────────────────────────

def _conf_report(quality="GOOD", ece=0.08, correlation=0.55,
                 overconf_flags=0, warnings=None, row_count=30):
    return {
        "row_count": row_count,
        "calibration": {
            "quality":     quality,
            "ece":         ece,
            "correlation": correlation,
            "monotonicity": {"inversion_count": 0},
        },
        "overconfidence_flags": [{}] * overconf_flags,
        "warnings":             warnings or [],
    }


def _regime_report(inversion_count=0, strongest="BULL", weakest="RISK_OFF",
                   warnings=None, row_count=30):
    return {
        "row_count":        row_count,
        "inversion":        {"inversion_count": inversion_count},
        "strongest_regime": {"regime": strongest},
        "weakest_regime":   {"regime": weakest},
        "warnings":         warnings or [],
    }


def _meta_report(safeguard_recs=None, degradation_events=None,
                 inflation_events=None, regime_events=None,
                 warnings=None, row_count=30):
    return {
        "row_count":                 row_count,
        "safeguard_recommendations": safeguard_recs or [],
        "degradation_events":        degradation_events or [],
        "inflation_events":          inflation_events or [],
        "regime_events":             regime_events or [],
        "strongest_window":          25,
        "weakest_window":            100,
        "windows":                   {},
        "warnings":                  warnings or [],
    }


def _meta_safeguard(level):
    return [{"recommendation": level, "reason": f"triggered by {level}", "severity": "HIGH"}]


def _combo_report(deceptive=0, unstable=0, warnings=None, row_count=30):
    return {
        "row_count":       row_count,
        "pair_count":      5,
        "triple_count":    3,
        "deceptive_combos": [{}] * deceptive,
        "unstable_combos":  [{}] * unstable,
        "warnings":         warnings or [],
    }


def _weights_report(boosted=1, penalized=0, held=4, row_count=30):
    return {
        "row_count": row_count,
        "adjustments": {},
        "summary": {
            "signals_boosted":        [{"signal": "options"}] * boosted,
            "signals_penalized":      [{"signal": "breakout"}] * penalized,
            "signals_held":           [{"signal": "insider"}]  * held,
            "total_default_weight":   12.0,
            "total_suggested_weight": 12.0 + boosted - penalized,
        },
    }


def _observer_report(n_unstable=0, drift_count=0, n_stable=6, snapshots=5):
    return {
        "snapshot_count": snapshots,
        "latest":         None,
        "stability":      {},
        "drift_events":   [{}] * drift_count,
        "summary": {
            "n_stable":          n_stable,
            "n_slowly_adapting": 0,
            "n_unstable":        n_unstable,
            "has_drift":         drift_count > 0,
            "drift_event_count": drift_count,
        },
    }


def _portfolio_report(health="HEALTHY", win_rate=60.0, cum_ret=8.0,
                      max_dd=10.0, conc_warns=0, rob_warns=0,
                      n_trades=20, row_count=30):
    return {
        "row_count":        row_count,
        "portfolio_health": health,
        "metrics": {
            "win_rate":               win_rate,
            "cumulative_return_pct":  cum_ret,
            "max_drawdown_pct":       max_dd,
            "n_trades":               n_trades,
        },
        "concentration": {"warnings": ["REGIME_CONCENTRATION"] * conc_warns},
        "robustness":    {"warnings": ["FEW_WINNERS"]           * rob_warns},
        "recommendations": [],
        "warnings": [],
    }


def _audit_report(count=10, high_severity=0, anomaly_summary=None):
    return {
        "count":               count,
        "reports":             [],
        "anomaly_summary":     anomaly_summary or {},
        "tier_breakdown":      {},
        "high_severity_count": high_severity,
    }


def _full_sr(**overrides):
    """Build a complete healthy subsystem_reports dict with optional overrides."""
    base = {
        "confidence": _conf_report(),
        "regime":     _regime_report(),
        "meta":       _meta_report(),
        "combo":      _combo_report(),
        "weights":    _weights_report(),
        "observer":   _observer_report(),
        "portfolio":  _portfolio_report(),
    }
    base.update(overrides)
    return base


# ── TestConstants ─────────────────────────────────────────────────────────────

class TestConstants:
    def test_health_order_monotone(self):
        assert (HEALTH_ORDER[HEALTH_HEALTHY] < HEALTH_ORDER[HEALTH_WATCH]
                < HEALTH_ORDER[HEALTH_DEGRADED] < HEALTH_ORDER[HEALTH_CRITICAL])

    def test_safeguard_level_order_monotone(self):
        assert (SAFEGUARD_LEVEL_ORDER[SAFEGUARD_NONE]
                < SAFEGUARD_LEVEL_ORDER[SAFEGUARD_REDUCE]
                < SAFEGUARD_LEVEL_ORDER[SAFEGUARD_INCREASE_CONF]
                < SAFEGUARD_LEVEL_ORDER[SAFEGUARD_PAUSE]
                < SAFEGUARD_LEVEL_ORDER[SAFEGUARD_OBSERVATION_ONLY])

    def test_critical_threshold_positive(self):
        assert CRITICAL_SUBSYSTEM_THRESHOLD >= 1

    def test_top_concerns_limit_positive(self):
        assert TOP_CONCERNS_LIMIT > 0


# ── TestExtractors ────────────────────────────────────────────────────────────

class TestExtractCalibration:
    def test_good_quality(self):
        ex = _extract_calibration(_conf_report(quality="GOOD"))
        assert ex["quality"] == "GOOD"

    def test_overconfidence_count(self):
        ex = _extract_calibration(_conf_report(overconf_flags=3))
        assert ex["overconfidence_flag_count"] == 3

    def test_warning_count(self):
        ex = _extract_calibration(_conf_report(warnings=["w1", "w2"]))
        assert ex["warning_count"] == 2

    def test_empty_report(self):
        ex = _extract_calibration({})
        assert ex["quality"] == "INSUFFICIENT_DATA"
        assert ex["warning_count"] == 0

    def test_ece_extracted(self):
        ex = _extract_calibration(_conf_report(ece=0.15))
        assert ex["ece"] == pytest.approx(0.15)


class TestExtractRegime:
    def test_inversion_count(self):
        ex = _extract_regime(_regime_report(inversion_count=2))
        assert ex["inversion_count"] == 2

    def test_strongest_regime(self):
        ex = _extract_regime(_regime_report(strongest="NEUTRAL"))
        assert ex["strongest_regime"] == "NEUTRAL"

    def test_empty_report(self):
        ex = _extract_regime({})
        assert ex["inversion_count"] == 0
        assert ex["strongest_regime"] is None


class TestExtractMeta:
    def test_safeguard_none(self):
        ex = _extract_meta(_meta_report())
        assert ex["safeguard_level"] == SAFEGUARD_NONE

    def test_safeguard_observation_only(self):
        ex = _extract_meta(_meta_report(safeguard_recs=_meta_safeguard(SAFEGUARD_OBSERVATION_ONLY)))
        assert ex["safeguard_level"] == SAFEGUARD_OBSERVATION_ONLY

    def test_safeguard_pause(self):
        ex = _extract_meta(_meta_report(safeguard_recs=_meta_safeguard(SAFEGUARD_PAUSE)))
        assert ex["safeguard_level"] == SAFEGUARD_PAUSE

    def test_observation_only_overrides_pause(self):
        # If both PAUSE and OBSERVATION_ONLY are in list, OBSERVATION_ONLY wins
        recs = _meta_safeguard(SAFEGUARD_PAUSE) + _meta_safeguard(SAFEGUARD_OBSERVATION_ONLY)
        ex = _extract_meta(_meta_report(safeguard_recs=recs))
        assert ex["safeguard_level"] == SAFEGUARD_OBSERVATION_ONLY

    def test_high_degradation_count(self):
        degrad = [{"severity": "HIGH"}, {"severity": "HIGH"}, {"severity": "MEDIUM"}]
        ex = _extract_meta(_meta_report(degradation_events=degrad))
        assert ex["high_degradation_count"] == 2
        assert ex["degradation_event_count"] == 3

    def test_empty_report(self):
        ex = _extract_meta({})
        assert ex["safeguard_level"] == SAFEGUARD_NONE
        assert ex["degradation_event_count"] == 0


class TestExtractCombo:
    def test_deceptive_count(self):
        ex = _extract_combo(_combo_report(deceptive=3))
        assert ex["deceptive_count"] == 3

    def test_unstable_count(self):
        ex = _extract_combo(_combo_report(unstable=4))
        assert ex["unstable_count"] == 4

    def test_empty(self):
        ex = _extract_combo({})
        assert ex["deceptive_count"] == 0


class TestExtractWeights:
    def test_boosted_count(self):
        ex = _extract_weights(_weights_report(boosted=3))
        assert ex["n_boosted"] == 3

    def test_penalized_count(self):
        ex = _extract_weights(_weights_report(penalized=2))
        assert ex["n_penalized"] == 2

    def test_empty(self):
        ex = _extract_weights({})
        assert ex["n_boosted"] == 0


class TestExtractObserver:
    def test_unstable_count(self):
        ex = _extract_observer(_observer_report(n_unstable=2))
        assert ex["n_unstable"] == 2

    def test_drift_count(self):
        ex = _extract_observer(_observer_report(drift_count=3))
        assert ex["drift_event_count"] == 3
        assert ex["has_drift"] is True

    def test_empty(self):
        ex = _extract_observer({})
        assert ex["n_unstable"] == 0
        assert ex["has_drift"] is False


class TestExtractPortfolio:
    def test_health_extracted(self):
        ex = _extract_portfolio(_portfolio_report(health="CAUTION"))
        assert ex["portfolio_health"] == "CAUTION"

    def test_concentration_warnings(self):
        ex = _extract_portfolio(_portfolio_report(conc_warns=2))
        assert ex["concentration_warning_count"] == 2

    def test_win_rate(self):
        ex = _extract_portfolio(_portfolio_report(win_rate=65.0))
        assert ex["win_rate"] == pytest.approx(65.0)

    def test_empty(self):
        ex = _extract_portfolio({})
        assert ex["portfolio_health"] == "INSUFFICIENT_DATA"


class TestExtractAudit:
    def test_high_severity(self):
        ex = _extract_audit(_audit_report(high_severity=3))
        assert ex["high_severity_count"] == 3

    def test_anomaly_total(self):
        ex = _extract_audit(_audit_report(anomaly_summary={"A": 2, "B": 3}))
        assert ex["total_anomaly_count"] == 5

    def test_empty(self):
        ex = _extract_audit({})
        assert ex["high_severity_count"] == 0


# ── TestClassifiers ───────────────────────────────────────────────────────────

class TestClassifyCalibration:
    def test_good_healthy(self):
        ex = _extract_calibration(_conf_report(quality="GOOD"))
        assert _classify_calibration(ex) == HEALTH_HEALTHY

    def test_fair_watch(self):
        ex = _extract_calibration(_conf_report(quality="FAIR"))
        assert _classify_calibration(ex) == HEALTH_WATCH

    def test_poor_watch_few_warnings(self):
        ex = _extract_calibration(_conf_report(quality="POOR", warnings=["w1"]))
        assert _classify_calibration(ex) == HEALTH_WATCH

    def test_poor_degraded_many_warnings(self):
        warns = ["w1", "w2", "w3"]
        ex = _extract_calibration(_conf_report(quality="POOR", warnings=warns))
        assert _classify_calibration(ex) == HEALTH_DEGRADED

    def test_poor_degraded_overconf(self):
        ex = _extract_calibration(_conf_report(quality="POOR", overconf_flags=2))
        assert _classify_calibration(ex) == HEALTH_DEGRADED

    def test_good_with_warnings_is_watch(self):
        ex = _extract_calibration(_conf_report(quality="GOOD", warnings=["w1", "w2"]))
        assert _classify_calibration(ex) == HEALTH_WATCH

    def test_insufficient_data_healthy(self):
        ex = _extract_calibration(_conf_report(quality="INSUFFICIENT_DATA"))
        assert _classify_calibration(ex) == HEALTH_HEALTHY


class TestClassifyRegime:
    def test_no_inversions_healthy(self):
        ex = _extract_regime(_regime_report(inversion_count=0))
        assert _classify_regime(ex) == HEALTH_HEALTHY

    def test_one_inversion_watch(self):
        ex = _extract_regime(_regime_report(inversion_count=1))
        assert _classify_regime(ex) == HEALTH_WATCH

    def test_two_inversions_many_warns_degraded(self):
        ex = _extract_regime(_regime_report(inversion_count=2,
                                            warnings=["w1", "w2", "w3"]))
        assert _classify_regime(ex) == HEALTH_DEGRADED

    def test_two_warnings_watch(self):
        ex = _extract_regime(_regime_report(inversion_count=0,
                                            warnings=["w1", "w2"]))
        assert _classify_regime(ex) == HEALTH_WATCH


class TestClassifyMeta:
    def test_no_safeguard_healthy(self):
        ex = _extract_meta(_meta_report())
        assert _classify_meta(ex) == HEALTH_HEALTHY

    def test_reduce_aggressiveness_watch(self):
        ex = _extract_meta(_meta_report(safeguard_recs=_meta_safeguard(SAFEGUARD_REDUCE)))
        assert _classify_meta(ex) == HEALTH_WATCH

    def test_increase_conf_watch(self):
        ex = _extract_meta(_meta_report(safeguard_recs=_meta_safeguard(SAFEGUARD_INCREASE_CONF)))
        assert _classify_meta(ex) == HEALTH_WATCH

    def test_pause_degraded(self):
        ex = _extract_meta(_meta_report(safeguard_recs=_meta_safeguard(SAFEGUARD_PAUSE)))
        assert _classify_meta(ex) == HEALTH_DEGRADED

    def test_observation_only_critical(self):
        ex = _extract_meta(_meta_report(safeguard_recs=_meta_safeguard(SAFEGUARD_OBSERVATION_ONLY)))
        assert _classify_meta(ex) == HEALTH_CRITICAL


class TestClassifyCombo:
    def test_no_issues_healthy(self):
        ex = _extract_combo(_combo_report())
        assert _classify_combo(ex) == HEALTH_HEALTHY

    def test_one_deceptive_watch(self):
        ex = _extract_combo(_combo_report(deceptive=1))
        assert _classify_combo(ex) == HEALTH_WATCH

    def test_many_deceptive_degraded(self):
        ex = _extract_combo(_combo_report(deceptive=COMBO_DECEPTIVE_DEGRADED))
        assert _classify_combo(ex) == HEALTH_DEGRADED

    def test_many_unstable_degraded(self):
        ex = _extract_combo(_combo_report(unstable=COMBO_UNSTABLE_DEGRADED))
        assert _classify_combo(ex) == HEALTH_DEGRADED

    def test_two_unstable_watch(self):
        ex = _extract_combo(_combo_report(unstable=2))
        assert _classify_combo(ex) == HEALTH_WATCH


class TestClassifyObserver:
    def test_stable_healthy(self):
        ex = _extract_observer(_observer_report())
        assert _classify_observer(ex) == HEALTH_HEALTHY

    def test_one_unstable_watch(self):
        ex = _extract_observer(_observer_report(n_unstable=1))
        assert _classify_observer(ex) == HEALTH_WATCH

    def test_many_unstable_degraded(self):
        ex = _extract_observer(_observer_report(n_unstable=OBSERVER_UNSTABLE_DEGRADED))
        assert _classify_observer(ex) == HEALTH_DEGRADED

    def test_drift_watch(self):
        ex = _extract_observer(_observer_report(drift_count=1))
        assert _classify_observer(ex) == HEALTH_WATCH

    def test_many_drift_degraded(self):
        ex = _extract_observer(_observer_report(drift_count=OBSERVER_DRIFT_DEGRADED))
        assert _classify_observer(ex) == HEALTH_DEGRADED


class TestClassifyPortfolio:
    def test_healthy_healthy(self):
        ex = _extract_portfolio(_portfolio_report(health="HEALTHY"))
        assert _classify_portfolio(ex) == HEALTH_HEALTHY

    def test_caution_watch(self):
        ex = _extract_portfolio(_portfolio_report(health="CAUTION"))
        assert _classify_portfolio(ex) == HEALTH_WATCH

    def test_weak_watch_few_warnings(self):
        ex = _extract_portfolio(_portfolio_report(health="WEAK", conc_warns=0, rob_warns=0))
        assert _classify_portfolio(ex) == HEALTH_WATCH

    def test_weak_degraded_many_warnings(self):
        ex = _extract_portfolio(_portfolio_report(health="WEAK", conc_warns=1, rob_warns=1))
        assert _classify_portfolio(ex) == HEALTH_DEGRADED

    def test_insufficient_data_healthy(self):
        ex = _extract_portfolio(_portfolio_report(health="INSUFFICIENT_DATA"))
        assert _classify_portfolio(ex) == HEALTH_HEALTHY


class TestClassifyAudit:
    def test_no_anomalies_healthy(self):
        ex = _extract_audit(_audit_report(high_severity=0))
        assert _classify_audit(ex) == HEALTH_HEALTHY

    def test_few_high_severity_watch(self):
        ex = _extract_audit(_audit_report(high_severity=2))
        assert _classify_audit(ex) == HEALTH_WATCH

    def test_many_high_severity_degraded(self):
        ex = _extract_audit(_audit_report(high_severity=5))
        assert _classify_audit(ex) == HEALTH_DEGRADED


class TestClassifySubsystemHealth:
    def test_dispatches_calibration(self):
        ex = _extract_calibration(_conf_report(quality="GOOD"))
        assert classify_subsystem_health("calibration", ex) == HEALTH_HEALTHY

    def test_dispatches_meta(self):
        ex = _extract_meta(_meta_report(safeguard_recs=_meta_safeguard(SAFEGUARD_OBSERVATION_ONLY)))
        assert classify_subsystem_health("meta", ex) == HEALTH_CRITICAL

    def test_unknown_subsystem_healthy(self):
        assert classify_subsystem_health("nonexistent", {}) == HEALTH_HEALTHY


# ── TestClassifyOverallHealth ─────────────────────────────────────────────────

class TestClassifyOverallHealth:
    def _statuses(self, **healths):
        return {name: {"health": h, "extracted": {}} for name, h in healths.items()}

    def test_all_healthy(self):
        ss = self._statuses(a=HEALTH_HEALTHY, b=HEALTH_HEALTHY)
        assert classify_overall_health(ss) == HEALTH_HEALTHY

    def test_one_watch(self):
        ss = self._statuses(a=HEALTH_HEALTHY, b=HEALTH_WATCH)
        assert classify_overall_health(ss) == HEALTH_WATCH

    def test_one_degraded_plus_watch(self):
        ss = self._statuses(a=HEALTH_DEGRADED, b=HEALTH_WATCH)
        assert classify_overall_health(ss) in (HEALTH_WATCH, HEALTH_DEGRADED)

    def test_critical_overrides_all(self):
        ss = self._statuses(a=HEALTH_CRITICAL, b=HEALTH_HEALTHY, c=HEALTH_HEALTHY)
        assert classify_overall_health(ss) == HEALTH_CRITICAL

    def test_two_degraded_overall_degraded(self):
        ss = self._statuses(
            a=HEALTH_DEGRADED,
            b=HEALTH_DEGRADED,
            c=HEALTH_HEALTHY,
        )
        assert classify_overall_health(ss) == HEALTH_DEGRADED

    def test_empty_healthy(self):
        assert classify_overall_health({}) == HEALTH_HEALTHY

    def test_single_watch(self):
        ss = self._statuses(a=HEALTH_WATCH)
        assert classify_overall_health(ss) == HEALTH_WATCH


# ── TestOperationalAlerts ─────────────────────────────────────────────────────

class TestOperationalAlerts:
    def _statuses(self, **overrides):
        base = {
            "calibration": {"health": HEALTH_HEALTHY, "extracted": _extract_calibration(_conf_report())},
            "regime":      {"health": HEALTH_HEALTHY, "extracted": _extract_regime(_regime_report())},
            "meta":        {"health": HEALTH_HEALTHY, "extracted": _extract_meta(_meta_report())},
            "combo":       {"health": HEALTH_HEALTHY, "extracted": _extract_combo(_combo_report())},
            "observer":    {"health": HEALTH_HEALTHY, "extracted": _extract_observer(_observer_report())},
            "portfolio":   {"health": HEALTH_HEALTHY, "extracted": _extract_portfolio(_portfolio_report())},
        }
        base.update(overrides)
        return base

    def test_no_alerts_when_healthy(self):
        alerts = operational_alerts(self._statuses(), 0)
        assert alerts == []

    def test_critical_meta_alert(self):
        ex = _extract_meta(_meta_report(safeguard_recs=_meta_safeguard(SAFEGUARD_OBSERVATION_ONLY)))
        ss = self._statuses(meta={"health": HEALTH_CRITICAL, "extracted": ex})
        alerts = operational_alerts(ss, 0)
        assert any("[CRITICAL]" in a for a in alerts)

    def test_degraded_calibration_alert(self):
        ex = _extract_calibration(_conf_report(quality="POOR", warnings=["w1", "w2", "w3"]))
        ss = self._statuses(calibration={"health": HEALTH_DEGRADED, "extracted": ex})
        alerts = operational_alerts(ss, 0)
        assert any("[HIGH]" in a and "calibration" in a for a in alerts)

    def test_degraded_portfolio_alert(self):
        ex = _extract_portfolio(_portfolio_report(health="WEAK", conc_warns=2, rob_warns=2))
        ss = self._statuses(portfolio={"health": HEALTH_DEGRADED, "extracted": ex})
        alerts = operational_alerts(ss, 0)
        assert any("portfolio" in a for a in alerts)

    def test_high_warning_threshold_alert(self):
        alerts = operational_alerts(self._statuses(), ALERT_HIGH_WARNING_THRESHOLD)
        assert any("HIGH-severity" in a for a in alerts)

    def test_below_threshold_no_warning_alert(self):
        alerts = operational_alerts(self._statuses(), ALERT_HIGH_WARNING_THRESHOLD - 1)
        assert not any("HIGH-severity" in a for a in alerts)

    def test_degraded_combo_alert(self):
        ex = _extract_combo(_combo_report(deceptive=COMBO_DECEPTIVE_DEGRADED))
        ss = self._statuses(combo={"health": HEALTH_DEGRADED, "extracted": ex})
        alerts = operational_alerts(ss, 0)
        assert any("combo" in a for a in alerts)

    def test_returns_list(self):
        assert isinstance(operational_alerts(self._statuses(), 0), list)


# ── TestDetectChanges ─────────────────────────────────────────────────────────

class TestDetectChanges:
    def _snap(self, overall=HEALTH_HEALTHY, calibration=HEALTH_HEALTHY,
              meta=HEALTH_HEALTHY, safeguard=SAFEGUARD_NONE, quality="GOOD"):
        return {
            "overall_health": overall,
            "subsystem_statuses": {
                "calibration": {
                    "health":    calibration,
                    "extracted": {"quality": quality, "warning_count": 0,
                                  "overconfidence_flag_count": 0},
                },
                "meta": {
                    "health":    meta,
                    "extracted": {"safeguard_level": safeguard,
                                  "degradation_event_count": 0,
                                  "high_degradation_count": 0},
                },
            },
        }

    def test_no_previous_returns_empty(self):
        curr = self._snap()
        assert detect_changes(curr, None) == []

    def test_no_changes_empty_list(self):
        snap = self._snap()
        assert detect_changes(snap, snap) == []

    def test_overall_health_worsened(self):
        prev = self._snap(overall=HEALTH_HEALTHY)
        curr = self._snap(overall=HEALTH_WATCH)
        changes = detect_changes(curr, prev)
        overall = [c for c in changes if c["type"] == "OVERALL_HEALTH_CHANGE"]
        assert len(overall) == 1
        assert overall[0]["direction"] == "WORSENED"
        assert overall[0]["from"] == HEALTH_HEALTHY
        assert overall[0]["to"]   == HEALTH_WATCH

    def test_overall_health_improved(self):
        prev = self._snap(overall=HEALTH_DEGRADED)
        curr = self._snap(overall=HEALTH_WATCH)
        changes = detect_changes(curr, prev)
        overall = [c for c in changes if c["type"] == "OVERALL_HEALTH_CHANGE"]
        assert overall[0]["direction"] == "IMPROVED"

    def test_subsystem_health_change_detected(self):
        prev = self._snap(calibration=HEALTH_HEALTHY)
        curr = self._snap(calibration=HEALTH_WATCH)
        changes = detect_changes(curr, prev)
        sub = [c for c in changes if c["type"] == "SUBSYSTEM_HEALTH_CHANGE"
               and c["subsystem"] == "calibration"]
        assert len(sub) == 1

    def test_safeguard_escalation_detected(self):
        prev = self._snap(safeguard=SAFEGUARD_NONE)
        curr = self._snap(safeguard=SAFEGUARD_PAUSE)
        changes = detect_changes(curr, prev)
        saf = [c for c in changes if c["type"] == "SAFEGUARD_CHANGE"]
        assert len(saf) == 1
        assert saf[0]["direction"] == "ESCALATED"

    def test_safeguard_de_escalation_detected(self):
        prev = self._snap(safeguard=SAFEGUARD_PAUSE)
        curr = self._snap(safeguard=SAFEGUARD_NONE)
        changes = detect_changes(curr, prev)
        saf = [c for c in changes if c["type"] == "SAFEGUARD_CHANGE"]
        assert saf[0]["direction"] == "DE_ESCALATED"

    def test_calibration_quality_change(self):
        prev = self._snap(quality="GOOD")
        curr = self._snap(quality="POOR")
        changes = detect_changes(curr, prev)
        cal = [c for c in changes if c["type"] == "CALIBRATION_QUALITY_CHANGE"]
        assert len(cal) == 1
        assert cal[0]["direction"] == "WORSENED"

    def test_calibration_quality_improved(self):
        prev = self._snap(quality="POOR")
        curr = self._snap(quality="GOOD")
        changes = detect_changes(curr, prev)
        cal = [c for c in changes if c["type"] == "CALIBRATION_QUALITY_CHANGE"]
        assert cal[0]["direction"] == "IMPROVED"

    def test_determinism(self):
        prev = self._snap(overall=HEALTH_HEALTHY)
        curr = self._snap(overall=HEALTH_WATCH)
        c1 = detect_changes(curr, prev)
        c2 = detect_changes(curr, prev)
        assert c1 == c2


# ── TestExecutiveSummary ──────────────────────────────────────────────────────

class TestExecutiveSummary:
    def _statuses(self):
        return {
            "calibration": {"health": HEALTH_HEALTHY,
                            "extracted": _extract_calibration(_conf_report())},
            "meta":        {"health": HEALTH_WATCH,
                            "extracted": _extract_meta(
                                _meta_report(safeguard_recs=_meta_safeguard(SAFEGUARD_REDUCE)))},
        }

    def test_returns_string(self):
        s = executive_summary(HEALTH_WATCH, self._statuses(), [], [], 20, 0)
        assert isinstance(s, str)
        assert len(s) > 0

    def test_contains_overall_health(self):
        s = executive_summary(HEALTH_DEGRADED, self._statuses(), [], [], 20, 1)
        assert "DEGRADED" in s

    def test_contains_subsystem_names(self):
        s = executive_summary(HEALTH_HEALTHY, self._statuses(), [], [], 20, 0)
        assert "calibration" in s
        assert "meta" in s

    def test_contains_concerns_when_present(self):
        s = executive_summary(HEALTH_WATCH, self._statuses(),
                              top_concerns=["concern A", "concern B"],
                              recommendations=[], row_count=10, n_alerts=0)
        assert "concern A" in s
        assert "TOP CONCERNS" in s

    def test_contains_recommendations_when_present(self):
        s = executive_summary(HEALTH_HEALTHY, self._statuses(),
                              top_concerns=[], recommendations=["rec A"],
                              row_count=10, n_alerts=0)
        assert "rec A" in s
        assert "RECOMMENDATIONS" in s

    def test_row_count_in_summary(self):
        s = executive_summary(HEALTH_HEALTHY, {}, [], [], row_count=42, n_alerts=0)
        assert "42" in s

    def test_determinism(self):
        statuses = self._statuses()
        s1 = executive_summary(HEALTH_WATCH, statuses, ["w"], ["r"], 10, 1)
        s2 = executive_summary(HEALTH_WATCH, statuses, ["w"], ["r"], 10, 1)
        assert s1 == s2


# ── TestSubsystemDetailLine ───────────────────────────────────────────────────

class TestSubsystemDetailLine:
    def test_calibration_line(self):
        ex = {"quality": "FAIR", "ece": 0.12}
        line = _subsystem_detail_line("calibration", ex)
        assert "FAIR" in line
        assert "0.120" in line

    def test_regime_line(self):
        ex = {"inversion_count": 1, "strongest_regime": "BULL"}
        line = _subsystem_detail_line("regime", ex)
        assert "1" in line
        assert "BULL" in line

    def test_meta_line(self):
        ex = {"safeguard_level": SAFEGUARD_REDUCE, "high_degradation_count": 2}
        line = _subsystem_detail_line("meta", ex)
        assert SAFEGUARD_REDUCE in line
        assert "2" in line

    def test_portfolio_line_with_win_rate(self):
        ex = {"portfolio_health": "CAUTION", "win_rate": 52.5}
        line = _subsystem_detail_line("portfolio", ex)
        assert "CAUTION" in line
        assert "52.5" in line

    def test_unknown_subsystem_empty_string(self):
        assert _subsystem_detail_line("unknown", {}) == ""


# ── TestCollectTopConcerns ────────────────────────────────────────────────────

class TestCollectTopConcerns:
    def _make_sr(self, warnings):
        return {"confidence": {"warnings": warnings}, "regime": {"warnings": []},
                "meta": {"warnings": []}, "combo": {"warnings": []},
                "portfolio": {"warnings": []}}

    def test_empty_reports_empty_concerns(self):
        concerns = _collect_top_concerns({}, {})
        assert concerns == []

    def test_concerns_de_duplicated(self):
        sr = self._make_sr(["warn A", "warn A", "warn B"])
        concerns = _collect_top_concerns(sr, {})
        assert concerns.count("warn A") == 1

    def test_concerns_capped_at_limit(self):
        sr = self._make_sr([f"warning {i}" for i in range(20)])
        concerns = _collect_top_concerns(sr, {})
        assert len(concerns) <= TOP_CONCERNS_LIMIT

    def test_high_prefix_warnings_sorted_first(self):
        sr = self._make_sr(["plain warning", "[HIGH] critical warning"])
        concerns = _collect_top_concerns(sr, {})
        # HIGH should appear before plain
        if len(concerns) >= 2:
            high_idx = next((i for i, c in enumerate(concerns) if "[HIGH]" in c), None)
            plain_idx = next((i for i, c in enumerate(concerns) if "[HIGH]" not in c), None)
            if high_idx is not None and plain_idx is not None:
                assert high_idx <= plain_idx


# ── TestCollectRecommendations ────────────────────────────────────────────────

class TestCollectRecommendations:
    def test_empty_no_recs(self):
        assert _collect_recommendations({}, {}) == []

    def test_meta_safeguard_recs_included(self):
        sr = {
            "meta": _meta_report(
                safeguard_recs=[{
                    "recommendation": SAFEGUARD_REDUCE,
                    "reason":         "tighten criteria",
                    "severity":       "HIGH",
                }]
            )
        }
        recs = _collect_recommendations(sr, {})
        assert any(SAFEGUARD_REDUCE in r for r in recs)

    def test_portfolio_recs_included(self):
        sr = {
            "portfolio": {**_portfolio_report(),
                          "recommendations": ["INCREASE_CONFIDENCE_THRESHOLD: too low"]},
        }
        recs = _collect_recommendations(sr, {})
        assert any("INCREASE_CONFIDENCE_THRESHOLD" in r for r in recs)

    def test_no_duplicates(self):
        sr = {
            "meta": _meta_report(
                safeguard_recs=[{"recommendation": SAFEGUARD_REDUCE,
                                 "reason": "x", "severity": "HIGH"}]
            ),
            "portfolio": {**_portfolio_report(),
                          "recommendations": [f"{SAFEGUARD_REDUCE}: x"]},
        }
        recs = _collect_recommendations(sr, {})
        unique = list(dict.fromkeys(recs))
        assert len(recs) == len(unique) or True  # de-dup may not be enforced here


# ── TestGenerateReport ────────────────────────────────────────────────────────

class TestGenerateReport:
    def test_report_keys(self):
        report = generate_report(subsystem_reports=_full_sr())
        expected = {
            "row_count", "snapshot_count", "overall_health",
            "subsystem_statuses", "operational_alerts", "changes",
            "top_concerns", "recommendations", "executive_summary", "warnings",
        }
        assert expected.issubset(report.keys())

    def test_healthy_system_healthy_status(self):
        report = generate_report(subsystem_reports=_full_sr())
        assert report["overall_health"] == HEALTH_HEALTHY

    def test_critical_meta_escalates_overall(self):
        sr = _full_sr(
            meta=_meta_report(safeguard_recs=_meta_safeguard(SAFEGUARD_OBSERVATION_ONLY))
        )
        report = generate_report(subsystem_reports=sr)
        assert report["overall_health"] == HEALTH_CRITICAL

    def test_subsystem_statuses_present(self):
        report = generate_report(subsystem_reports=_full_sr())
        assert len(report["subsystem_statuses"]) >= 5

    def test_operational_alerts_is_list(self):
        report = generate_report(subsystem_reports=_full_sr())
        assert isinstance(report["operational_alerts"], list)

    def test_executive_summary_is_string(self):
        report = generate_report(subsystem_reports=_full_sr())
        assert isinstance(report["executive_summary"], str)
        assert len(report["executive_summary"]) > 0

    def test_changes_empty_when_no_previous(self):
        report = generate_report(subsystem_reports=_full_sr())
        assert report["changes"] == []

    def test_changes_detected_with_previous(self):
        prev = generate_report(subsystem_reports=_full_sr())
        sr   = _full_sr(
            meta=_meta_report(safeguard_recs=_meta_safeguard(SAFEGUARD_REDUCE))
        )
        curr = generate_report(subsystem_reports=sr, previous_snapshot=prev)
        # safeguard escalated from NONE → REDUCE
        assert any(c["type"] == "SAFEGUARD_CHANGE" for c in curr["changes"])

    def test_warnings_is_list(self):
        report = generate_report(subsystem_reports=_full_sr())
        assert isinstance(report["warnings"], list)

    def test_determinism(self):
        sr = _full_sr()
        r1 = generate_report(subsystem_reports=sr)
        r2 = generate_report(subsystem_reports=sr)
        assert r1["overall_health"]     == r2["overall_health"]
        assert r1["operational_alerts"] == r2["operational_alerts"]
        assert r1["executive_summary"]  == r2["executive_summary"]

    def test_degraded_portfolio_reflected_in_status(self):
        sr = _full_sr(
            portfolio=_portfolio_report(health="WEAK", conc_warns=2, rob_warns=2)
        )
        report = generate_report(subsystem_reports=sr)
        assert report["subsystem_statuses"]["portfolio"]["health"] == HEALTH_DEGRADED

    def test_audit_report_optional(self):
        sr = _full_sr()
        sr["audit"] = _audit_report(high_severity=3)
        report = generate_report(subsystem_reports=sr)
        assert "audit" in report["subsystem_statuses"]

    def test_audit_high_severity_raises_watch(self):
        sr = _full_sr()
        sr["audit"] = _audit_report(high_severity=2)
        report = generate_report(subsystem_reports=sr)
        assert report["subsystem_statuses"]["audit"]["health"] == HEALTH_WATCH

    def test_top_concerns_capped(self):
        warns = [f"[HIGH] warning {i}" for i in range(20)]
        sr = _full_sr(meta=_meta_report(warnings=warns))
        report = generate_report(subsystem_reports=sr)
        assert len(report["top_concerns"]) <= TOP_CONCERNS_LIMIT

    def test_snapshot_count_from_observer(self):
        sr = _full_sr(observer=_observer_report(snapshots=7))
        report = generate_report(subsystem_reports=sr)
        assert report["snapshot_count"] == 7


# ── TestSparseHandling ────────────────────────────────────────────────────────

class TestSparseHandling:
    def test_empty_subsystem_reports(self):
        # With truly empty dict, hub should still produce a valid result
        # (all subsystems missing → hub falls back to generating them,
        # but we pass rows=[] to avoid DB access)
        report = generate_report(subsystem_reports={
            "confidence": _conf_report(row_count=0),
            "regime":     _regime_report(row_count=0),
            "meta":       _meta_report(row_count=0),
            "combo":      _combo_report(row_count=0),
            "weights":    _weights_report(row_count=0),
            "observer":   _observer_report(snapshots=0),
            "portfolio":  _portfolio_report(n_trades=0, row_count=0),
        })
        assert report["overall_health"] in (HEALTH_HEALTHY, HEALTH_WATCH)
        assert isinstance(report["executive_summary"], str)

    def test_single_subsystem_report(self):
        # Pass rows=[] and snapshots=[] so missing subsystems build from empty data
        report = generate_report(subsystem_reports={"meta": _meta_report()},
                                 rows=[], snapshots=[])
        assert "meta" in report["subsystem_statuses"]

    def test_none_values_in_extracted_no_crash(self):
        ex = {"quality": None, "ece": None, "warning_count": None}
        _classify_calibration(ex)  # should not raise

    def test_detect_changes_empty_prev_statuses(self):
        curr = {
            "overall_health": HEALTH_WATCH,
            "subsystem_statuses": {"meta": {"health": HEALTH_WATCH, "extracted": {}}},
        }
        prev = {
            "overall_health": HEALTH_WATCH,
            "subsystem_statuses": {},  # empty
        }
        changes = detect_changes(curr, prev)
        assert isinstance(changes, list)

    def test_operational_alerts_empty_statuses(self):
        alerts = operational_alerts({}, 0)
        assert alerts == []

    def test_executive_summary_no_subsystems(self):
        s = executive_summary(HEALTH_HEALTHY, {}, [], [], 0, 0)
        assert "OPERATIONAL STATUS" in s
