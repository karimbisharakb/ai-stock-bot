"""
Tests for bot/research_reports.py — Phase 4B.

Covers:
  - Primitive builders (_entry, _section, health_score, report_quality_score)
  - Trend detection and change comparison
  - Section builders (all 8)
  - Report assembly (_assemble)
  - All 8 public report builders
  - Determinism
  - Bounded output limits
  - Sparse / None input handling
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import research_reports as rr

# ── helpers ───────────────────────────────────────────────────────────────────

def _prev(score=80, sev=rr.SEV_INFO, sections=None):
    return {
        "health_score":     score,
        "overall_severity": sev,
        "sections":         sections or {},
    }


def _conf_report(
    quality="GOOD",
    ece=0.05,
    corr=0.40,
    overconfidence_flags=None,
    warnings=None,
    row_count=100,
):
    return {
        "calibration": {"quality": quality, "ece": ece, "correlation": corr},
        "overconfidence_flags": overconfidence_flags or [],
        "warnings": warnings or [],
        "row_count": row_count,
    }


def _regime_report(
    inversion_count=0,
    strongest="BULL",
    weakest="RISK_OFF",
    warnings=None,
    row_count=100,
):
    return {
        "inversion": {"inversion_count": inversion_count},
        "strongest_regime": {"regime": strongest},
        "weakest_regime":   {"regime": weakest},
        "warnings": warnings or [],
        "row_count": row_count,
    }


def _meta_report(
    safeguards=None,
    degradation_events=None,
    inflation_events=None,
    regime_events=None,
    strongest_window=None,
    weakest_window=None,
    warnings=None,
    row_count=100,
):
    return {
        "safeguard_recommendations": safeguards or [],
        "degradation_events": degradation_events or [],
        "inflation_events":   inflation_events or [],
        "regime_events":      regime_events or [],
        "strongest_window":   strongest_window,
        "weakest_window":     weakest_window,
        "warnings": warnings or [],
        "row_count": row_count,
    }


def _portfolio_report(
    health="HEALTHY",
    win_rate=60.0,
    cum_ret=5.0,
    max_dd=10.0,
    n_trades=20,
    conc_warnings=None,
    rob_warnings=None,
    row_count=100,
):
    return {
        "portfolio_health": health,
        "metrics": {
            "win_rate": win_rate,
            "cumulative_return_pct": cum_ret,
            "max_drawdown_pct": max_dd,
            "n_trades": n_trades,
        },
        "concentration": {"warnings": conc_warnings or []},
        "robustness":    {"warnings": rob_warnings or []},
        "row_count": row_count,
    }


def _weights_report(boosted=None, penalized=None, held=None, row_count=50):
    return {
        "summary": {
            "signals_boosted":   boosted or [],
            "signals_penalized": penalized or [],
            "signals_held":      held or [],
        },
        "row_count": row_count,
    }


def _observer_report(n_unstable=0, drift_count=0, has_drift=False):
    return {
        "summary": {
            "n_unstable":        n_unstable,
            "drift_event_count": drift_count,
            "has_drift":         has_drift,
        }
    }


def _audit_batch(high_count=0, anomaly_summary=None, count=10, tier_bd=None):
    return {
        "high_severity_count": high_count,
        "anomaly_summary":     anomaly_summary or {},
        "count":               count,
        "tier_breakdown":      tier_bd or {},
    }


def _replay_report(b_wr=55.0, b_n=50, wr_range=5.0, rob_warnings=None):
    return {
        "baseline":            {"win_rate": b_wr, "n": b_n},
        "counterfactuals":     {},
        "robustness_warnings": rob_warnings or [],
        "sensitivity":         {"win_rate_range": wr_range},
    }


# ── TestEntry ─────────────────────────────────────────────────────────────────

class TestEntry:
    def test_fields_present(self):
        e = rr._entry(rr.SEV_INFO, "cat", "title", "detail", 42)
        assert e["severity"] == rr.SEV_INFO
        assert e["category"] == "cat"
        assert e["title"]    == "title"
        assert e["detail"]   == "detail"
        assert e["metric"]   == 42

    def test_metric_defaults_none(self):
        e = rr._entry(rr.SEV_WATCH, "c", "t", "d")
        assert e["metric"] is None


# ── TestSection ───────────────────────────────────────────────────────────────

class TestSection:
    def test_empty_entries_severity_info(self):
        s = rr._section("s", [])
        assert s["severity"] == rr.SEV_INFO

    def test_max_severity_rolled_up(self):
        entries = [
            rr._entry(rr.SEV_INFO, "c", "t", "d"),
            rr._entry(rr.SEV_WARNING, "c", "t", "d"),
            rr._entry(rr.SEV_WATCH, "c", "t", "d"),
        ]
        s = rr._section("s", entries)
        assert s["severity"] == rr.SEV_WARNING

    def test_entries_capped_at_max(self):
        entries = [rr._entry(rr.SEV_INFO, "c", "t", "d") for _ in range(20)]
        s = rr._section("s", entries)
        assert len(s["entries"]) == rr.MAX_ENTRIES_PER_SECTION

    def test_summary_stored(self):
        s = rr._section("s", [], "my summary")
        assert s["summary"] == "my summary"

    def test_name_stored(self):
        s = rr._section("calibration", [])
        assert s["name"] == "calibration"

    def test_critical_dominates_over_warning(self):
        entries = [
            rr._entry(rr.SEV_WARNING, "c", "t", "d"),
            rr._entry(rr.SEV_CRITICAL, "c", "t", "d"),
        ]
        s = rr._section("s", entries)
        assert s["severity"] == rr.SEV_CRITICAL


# ── TestHealthScore ───────────────────────────────────────────────────────────

class TestHealthScore:
    def _sections_from_entries(self, entries):
        return {"s": rr._section("s", entries)}

    def test_no_entries_is_100_excellent(self):
        score, label = rr.health_score({})
        assert score == 100
        assert label == rr.HEALTH_EXCELLENT

    def test_all_info_is_100(self):
        entries = [rr._entry(rr.SEV_INFO, "c", "t", "d") for _ in range(5)]
        score, label = rr.health_score(self._sections_from_entries(entries))
        assert score == 100
        assert label == rr.HEALTH_EXCELLENT

    def test_critical_deducts_30(self):
        entries = [rr._entry(rr.SEV_CRITICAL, "c", "t", "d")]
        score, _ = rr.health_score(self._sections_from_entries(entries))
        assert score == 70

    def test_warning_deducts_15(self):
        entries = [rr._entry(rr.SEV_WARNING, "c", "t", "d")]
        score, _ = rr.health_score(self._sections_from_entries(entries))
        assert score == 85

    def test_watch_deducts_5(self):
        entries = [rr._entry(rr.SEV_WATCH, "c", "t", "d")]
        score, _ = rr.health_score(self._sections_from_entries(entries))
        assert score == 95

    def test_accumulates_across_entries(self):
        entries = [
            rr._entry(rr.SEV_CRITICAL, "c", "t", "d"),  # -30
            rr._entry(rr.SEV_WARNING, "c", "t", "d"),   # -15
        ]
        score, _ = rr.health_score(self._sections_from_entries(entries))
        assert score == 55

    def test_score_clamped_at_zero(self):
        entries = [rr._entry(rr.SEV_CRITICAL, "c", "t", "d") for _ in range(10)]
        score, label = rr.health_score(self._sections_from_entries(entries))
        assert score == 0
        assert label == rr.HEALTH_POOR

    def test_labels_excellent_threshold(self):
        # 2 WATCH entries = 100 - 10 = 90 → EXCELLENT
        entries = [rr._entry(rr.SEV_WATCH, "c", "t", "d") for _ in range(2)]
        score, label = rr.health_score(self._sections_from_entries(entries))
        assert score == 90
        assert label == rr.HEALTH_EXCELLENT

    def test_labels_good(self):
        # 2 WARNING = 100 - 30 = 70 → GOOD
        entries = [rr._entry(rr.SEV_WARNING, "c", "t", "d") for _ in range(2)]
        score, label = rr.health_score(self._sections_from_entries(entries))
        assert score == 70
        assert label == rr.HEALTH_GOOD

    def test_labels_fair(self):
        # 100 - 45 = 55 → FAIR (3 warning = -45)
        entries = [rr._entry(rr.SEV_WARNING, "c", "t", "d") for _ in range(3)]
        score, label = rr.health_score(self._sections_from_entries(entries))
        assert score == 55
        assert label == rr.HEALTH_FAIR

    def test_labels_poor(self):
        # 100 - 60 = 40 → POOR (4 warning = -60)
        entries = [rr._entry(rr.SEV_WARNING, "c", "t", "d") for _ in range(4)]
        score, label = rr.health_score(self._sections_from_entries(entries))
        assert score == 40
        assert label == rr.HEALTH_POOR


# ── TestReportQualityScore ────────────────────────────────────────────────────

class TestReportQualityScore:
    def test_no_sections_returns_poor(self):
        q = rr.report_quality_score({"sections": {}})
        assert q["score"] == 0
        assert q["label"] == rr.HEALTH_POOR

    def test_no_entries_returns_poor(self):
        q = rr.report_quality_score({
            "sections": {"s": {"entries": [], "severity": rr.SEV_INFO}}
        })
        assert q["score"] == 0
        assert q["label"] == rr.HEALTH_POOR

    def test_good_data_returns_high_score(self):
        entries = [rr._entry(rr.SEV_INFO, "c", "normal title", "all fine")]
        sections = {f"s{i}": {"entries": entries, "severity": rr.SEV_INFO}
                    for i in range(5)}
        q = rr.report_quality_score({"sections": sections})
        assert q["score"] >= 90

    def test_insufficient_title_reduces_score(self):
        entries = [rr._entry(rr.SEV_INFO, "c", "Insufficient calibration data", "detail")]
        sections = {"s": {"entries": entries, "severity": rr.SEV_INFO}}
        q_bad  = rr.report_quality_score({"sections": sections})
        entries2 = [rr._entry(rr.SEV_INFO, "c", "normal title", "detail")]
        sections2 = {"s": {"entries": entries2, "severity": rr.SEV_INFO}}
        q_good = rr.report_quality_score({"sections": sections2})
        assert q_bad["score"] < q_good["score"]

    def test_no_data_detail_reduces_score(self):
        entries = [rr._entry(rr.SEV_INFO, "c", "title", "no data available")]
        sections = {"s": {"entries": entries, "severity": rr.SEV_INFO}}
        q = rr.report_quality_score({"sections": sections})
        # data_quality drops; total should be reduced
        assert q["score"] < 100

    def test_section_coverage_uses_5_as_max(self):
        # 5+ sections → coverage = 100 %
        entries = [rr._entry(rr.SEV_INFO, "c", "t", "d")]
        sections = {f"s{i}": {"entries": entries, "severity": rr.SEV_INFO}
                    for i in range(5)}
        q5 = rr.report_quality_score({"sections": sections})
        # 1 section → coverage = 20 %
        sections1 = {"s0": sections["s0"]}
        q1 = rr.report_quality_score({"sections": sections1})
        assert q5["score"] > q1["score"]


# ── TestDetectTrends ──────────────────────────────────────────────────────────

class TestDetectTrends:
    def _s(self, sev):
        return {"severity": sev, "entries": [], "name": "s"}

    def test_no_previous_returns_empty(self):
        sections = {"cal": self._s(rr.SEV_WARNING)}
        assert rr.detect_trends(sections, None) == []

    def test_worsening_entry(self):
        current  = {"cal": {**self._s(rr.SEV_WARNING), "name": "cal"}}
        prev_sec = {"cal": {**self._s(rr.SEV_INFO),    "name": "cal"}}
        trends = rr.detect_trends(current, {"sections": prev_sec})
        assert any("worsening" in t for t in trends)
        assert any("cal" in t for t in trends)

    def test_improving_entry(self):
        current  = {"cal": {**self._s(rr.SEV_INFO),    "name": "cal"}}
        prev_sec = {"cal": {**self._s(rr.SEV_WARNING), "name": "cal"}}
        trends = rr.detect_trends(current, {"sections": prev_sec})
        assert any("improving" in t for t in trends)

    def test_stable_produces_no_entry(self):
        current  = {"cal": {**self._s(rr.SEV_INFO), "name": "cal"}}
        prev_sec = {"cal": {**self._s(rr.SEV_INFO), "name": "cal"}}
        trends = rr.detect_trends(current, {"sections": prev_sec})
        assert trends == []

    def test_section_missing_in_previous_skipped(self):
        current = {"new_sec": {**self._s(rr.SEV_CRITICAL), "name": "new_sec"}}
        trends = rr.detect_trends(current, {"sections": {}})
        assert trends == []

    def test_capped_at_max_trend_items(self):
        # Build 10 worsening sections
        current  = {f"s{i}": {**self._s(rr.SEV_CRITICAL), "name": f"s{i}"}
                    for i in range(10)}
        prev_sec = {f"s{i}": {**self._s(rr.SEV_INFO),     "name": f"s{i}"}
                    for i in range(10)}
        trends = rr.detect_trends(current, {"sections": prev_sec})
        assert len(trends) <= rr.MAX_TREND_ITEMS

    def test_direction_strings_include_before_after(self):
        current  = {"cal": {**self._s(rr.SEV_WARNING), "name": "cal"}}
        prev_sec = {"cal": {**self._s(rr.SEV_INFO),    "name": "cal"}}
        trends = rr.detect_trends(current, {"sections": prev_sec})
        assert rr.SEV_INFO    in trends[0]
        assert rr.SEV_WARNING in trends[0]


# ── TestCompareReportChanges ──────────────────────────────────────────────────

class TestCompareReportChanges:
    def test_small_delta_no_change(self):
        changes = rr.compare_report_changes(82, rr.SEV_INFO, _prev(score=80))
        score_changes = [c for c in changes if c["type"] == "HEALTH_SCORE_CHANGE"]
        assert score_changes == []

    def test_large_improvement(self):
        changes = rr.compare_report_changes(95, rr.SEV_INFO, _prev(score=80))
        sc = [c for c in changes if c["type"] == "HEALTH_SCORE_CHANGE"]
        assert sc and sc[0]["direction"] == "IMPROVED"
        assert sc[0]["delta"] == 15

    def test_large_worsening(self):
        changes = rr.compare_report_changes(65, rr.SEV_INFO, _prev(score=80))
        sc = [c for c in changes if c["type"] == "HEALTH_SCORE_CHANGE"]
        assert sc and sc[0]["direction"] == "WORSENED"

    def test_severity_escalated(self):
        changes = rr.compare_report_changes(80, rr.SEV_WARNING, _prev(sev=rr.SEV_INFO))
        sev_changes = [c for c in changes if c["type"] == "SEVERITY_CHANGE"]
        assert sev_changes and sev_changes[0]["direction"] == "ESCALATED"

    def test_severity_de_escalated(self):
        changes = rr.compare_report_changes(90, rr.SEV_INFO, _prev(sev=rr.SEV_WARNING))
        sev_changes = [c for c in changes if c["type"] == "SEVERITY_CHANGE"]
        assert sev_changes and sev_changes[0]["direction"] == "DE_ESCALATED"

    def test_no_severity_change_produces_no_entry(self):
        changes = rr.compare_report_changes(80, rr.SEV_INFO, _prev(sev=rr.SEV_INFO))
        sev_changes = [c for c in changes if c["type"] == "SEVERITY_CHANGE"]
        assert sev_changes == []

    def test_both_changes_detected(self):
        changes = rr.compare_report_changes(60, rr.SEV_CRITICAL,
                                            _prev(score=80, sev=rr.SEV_INFO))
        types = {c["type"] for c in changes}
        assert "HEALTH_SCORE_CHANGE" in types
        assert "SEVERITY_CHANGE" in types


# ── TestBuildCalibrationSection ───────────────────────────────────────────────

class TestBuildCalibrationSection:
    def test_poor_quality_is_warning(self):
        s = rr._build_calibration_section(_conf_report(quality="POOR"))
        assert s["severity"] == rr.SEV_WARNING

    def test_fair_quality_is_watch(self):
        s = rr._build_calibration_section(_conf_report(quality="FAIR", ece=0.05, corr=0.5))
        assert any(e["severity"] == rr.SEV_WATCH for e in s["entries"])

    def test_good_quality_is_info(self):
        e_list = [e for e in rr._build_calibration_section(
            _conf_report(quality="GOOD", ece=0.05, corr=0.5))["entries"]
                  if e["title"].startswith("Calibration quality")]
        assert e_list[0]["severity"] == rr.SEV_INFO

    def test_high_ece_is_warning(self):
        s = rr._build_calibration_section(_conf_report(ece=0.25))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs

    def test_moderate_ece_is_watch(self):
        s = rr._build_calibration_section(_conf_report(ece=0.15, corr=0.5))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs

    def test_low_ece_is_info(self):
        s = rr._build_calibration_section(_conf_report(ece=0.05, corr=0.5))
        ece_entries = [e for e in s["entries"] if "ECE" in e["title"]]
        assert ece_entries[0]["severity"] == rr.SEV_INFO

    def test_negative_corr_is_warning(self):
        s = rr._build_calibration_section(_conf_report(corr=-0.1))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs

    def test_weak_corr_is_watch(self):
        s = rr._build_calibration_section(_conf_report(corr=0.2, ece=0.05))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs

    def test_positive_corr_is_info(self):
        entries = rr._build_calibration_section(
            _conf_report(corr=0.5, ece=0.05))["entries"]
        corr_entries = [e for e in entries if "correlation" in e["title"]]
        assert corr_entries[0]["severity"] == rr.SEV_INFO

    def test_overconfidence_flags_adds_warning(self):
        s = rr._build_calibration_section(
            _conf_report(overconfidence_flags=["bucket_70", "bucket_80"]))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs

    def test_high_warning_in_warns_adds_warning(self):
        s = rr._build_calibration_section(
            _conf_report(warnings=["[HIGH] something bad"]))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs

    def test_no_ece_no_corr_still_returns_section(self):
        s = rr._build_calibration_section({"calibration": {"quality": "GOOD"},
                                            "overconfidence_flags": [],
                                            "warnings": []})
        assert "entries" in s


# ── TestBuildRegimeSection ────────────────────────────────────────────────────

class TestBuildRegimeSection:
    def test_no_inversions_is_info(self):
        s = rr._build_regime_section(_regime_report(inversion_count=0))
        inv_entries = [e for e in s["entries"] if "inversion" in e["title"].lower()]
        assert inv_entries[0]["severity"] == rr.SEV_INFO

    def test_inversions_is_warning(self):
        s = rr._build_regime_section(_regime_report(inversion_count=2))
        assert s["severity"] == rr.SEV_WARNING

    def test_strongest_weakest_entry_added(self):
        s = rr._build_regime_section(_regime_report())
        titles = [e["title"] for e in s["entries"]]
        assert any("Strongest regime" in t for t in titles)

    def test_high_warn_is_warning(self):
        s = rr._build_regime_section(
            _regime_report(warnings=["[HIGH] regime stability low"]))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs

    def test_medium_warn_is_watch(self):
        s = rr._build_regime_section(
            _regime_report(warnings=["[MEDIUM] slight anomaly"]))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs


# ── TestBuildMetaSection ──────────────────────────────────────────────────────

class TestBuildMetaSection:
    def _safe(self, name):
        return [{"recommendation": name}]

    def test_observation_only_is_critical(self):
        s = rr._build_meta_section(_meta_report(
            safeguards=self._safe("OBSERVATION_ONLY")))
        assert s["severity"] == rr.SEV_CRITICAL

    def test_pause_adaptive_is_warning(self):
        s = rr._build_meta_section(_meta_report(
            safeguards=self._safe("PAUSE_ADAPTIVE_ROLLOUT")))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs

    def test_other_safeguard_is_watch(self):
        s = rr._build_meta_section(_meta_report(
            safeguards=self._safe("REDUCE_AGGRESSIVENESS")))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs

    def test_no_safeguards_is_info(self):
        s = rr._build_meta_section(_meta_report())
        first = s["entries"][0]
        assert first["severity"] == rr.SEV_INFO

    def test_high_degradation_events_warning(self):
        degrad = [{"severity": "HIGH", "detail": "drop in win rate"}]
        s = rr._build_meta_section(_meta_report(degradation_events=degrad))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs

    def test_low_degradation_events_watch(self):
        degrad = [{"severity": "LOW", "detail": "minor drop"}]
        s = rr._build_meta_section(_meta_report(degradation_events=degrad))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs

    def test_inflation_events_watch(self):
        s = rr._build_meta_section(_meta_report(
            inflation_events=[{"detail": "conf rising"}]))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs

    def test_strongest_weakest_added_when_different(self):
        s = rr._build_meta_section(_meta_report(
            strongest_window=10, weakest_window=50))
        titles = [e["title"] for e in s["entries"]]
        assert any("Strongest" in t for t in titles)


# ── TestBuildPortfolioSection ─────────────────────────────────────────────────

class TestBuildPortfolioSection:
    def test_weak_health_is_warning(self):
        s = rr._build_portfolio_section(_portfolio_report(health="WEAK"))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs

    def test_caution_health_is_watch(self):
        s = rr._build_portfolio_section(_portfolio_report(health="CAUTION"))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs

    def test_healthy_is_info(self):
        e_list = [e for e in rr._build_portfolio_section(
            _portfolio_report(health="HEALTHY"))["entries"]
                  if "health" in e["title"].lower()]
        assert e_list[0]["severity"] == rr.SEV_INFO

    def test_high_drawdown_is_warning(self):
        s = rr._build_portfolio_section(_portfolio_report(max_dd=30.0))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs

    def test_elevated_drawdown_is_watch(self):
        s = rr._build_portfolio_section(_portfolio_report(max_dd=20.0))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs

    def test_low_win_rate_is_warning(self):
        s = rr._build_portfolio_section(_portfolio_report(win_rate=35.0))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs

    def test_below_avg_win_rate_is_watch(self):
        s = rr._build_portfolio_section(_portfolio_report(win_rate=45.0, max_dd=5.0))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs

    def test_good_win_rate_is_info(self):
        entries = rr._build_portfolio_section(
            _portfolio_report(win_rate=65.0, max_dd=5.0))["entries"]
        wr_entries = [e for e in entries if "win rate" in e["title"].lower()
                      and "Low" not in e["title"] and "Below" not in e["title"]]
        assert wr_entries[0]["severity"] == rr.SEV_INFO

    def test_negative_cumret_is_warning(self):
        s = rr._build_portfolio_section(_portfolio_report(cum_ret=-10.0))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs

    def test_positive_cumret_no_warning(self):
        s = rr._build_portfolio_section(_portfolio_report(cum_ret=5.0))
        cumret_warns = [e for e in s["entries"]
                        if "Negative cumulative" in e["title"]]
        assert cumret_warns == []

    def test_concentration_warnings_added(self):
        s = rr._build_portfolio_section(
            _portfolio_report(conc_warnings=["TICKER over-concentration"]))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs

    def test_robustness_warnings_added(self):
        s = rr._build_portfolio_section(
            _portfolio_report(rob_warnings=["ALPHA_CONCENTRATION"]))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs


# ── TestBuildAdaptiveSection ──────────────────────────────────────────────────

class TestBuildAdaptiveSection:
    def test_many_unstable_signals_warning(self):
        s = rr._build_adaptive_section(
            _weights_report(), _observer_report(n_unstable=5))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs

    def test_some_unstable_signals_watch(self):
        s = rr._build_adaptive_section(
            _weights_report(), _observer_report(n_unstable=2))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs

    def test_no_unstable_is_info(self):
        first = rr._build_adaptive_section(
            _weights_report(), _observer_report(n_unstable=0))["entries"][0]
        assert first["severity"] == rr.SEV_INFO

    def test_high_drift_count_warning(self):
        s = rr._build_adaptive_section(
            _weights_report(), _observer_report(drift_count=5))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs

    def test_low_drift_count_watch(self):
        s = rr._build_adaptive_section(
            _weights_report(), _observer_report(drift_count=2))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs

    def test_distribution_entry_always_present(self):
        s = rr._build_adaptive_section(
            _weights_report(boosted=["RSI"], penalized=["MACD"], held=["VOL"]),
            _observer_report())
        dist = [e for e in s["entries"] if "Boosted" in e["title"]]
        assert len(dist) == 1


# ── TestBuildAnomalySection ───────────────────────────────────────────────────

class TestBuildAnomalySection:
    def test_many_high_severity_warning(self):
        s = rr._build_anomaly_section(_audit_batch(high_count=5))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs

    def test_some_high_severity_watch(self):
        s = rr._build_anomaly_section(_audit_batch(high_count=1))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs

    def test_no_high_severity_is_info(self):
        first = rr._build_anomaly_section(
            _audit_batch(high_count=0))["entries"][0]
        assert first["severity"] == rr.SEV_INFO

    def test_anomaly_summary_adds_entry(self):
        s = rr._build_anomaly_section(
            _audit_batch(anomaly_summary={"UNEXPECTED_HIGH_CONF": 3}))
        assert len(s["entries"]) >= 2

    def test_tier_breakdown_adds_entry(self):
        s = rr._build_anomaly_section(
            _audit_batch(tier_bd={"CONVICTION": 5, "STANDARD": 10}))
        tier_entries = [e for e in s["entries"] if "Tier" in e["title"]]
        assert len(tier_entries) == 1


# ── TestBuildDegradationSection ───────────────────────────────────────────────

class TestBuildDegradationSection:
    def test_high_degradation_is_warning(self):
        s = rr._build_degradation_section(_meta_report(
            degradation_events=[{"severity": "HIGH", "detail": "win rate drop"}]))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs

    def test_low_degradation_is_watch(self):
        s = rr._build_degradation_section(_meta_report(
            degradation_events=[{"severity": "LOW", "detail": "minor"}]))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs

    def test_no_degradation_is_info(self):
        first = rr._build_degradation_section(
            _meta_report())["entries"][0]
        assert first["severity"] == rr.SEV_INFO

    def test_inflation_events_added(self):
        s = rr._build_degradation_section(_meta_report(
            inflation_events=[{"detail": "conf up"}]))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs

    def test_regime_events_added(self):
        s = rr._build_degradation_section(_meta_report(
            regime_events=[{"detail": "BULL weaker"}]))
        regime_entries = [e for e in s["entries"]
                          if "regime" in e["title"].lower()]
        assert len(regime_entries) >= 1

    def test_observation_only_is_critical(self):
        s = rr._build_degradation_section(_meta_report(
            safeguards=[{"recommendation": "OBSERVATION_ONLY"}]))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_CRITICAL in sevs

    def test_pause_adaptive_is_warning(self):
        s = rr._build_degradation_section(_meta_report(
            safeguards=[{"recommendation": "PAUSE_ADAPTIVE_ROLLOUT"}]))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs


# ── TestBuildReplaySection ────────────────────────────────────────────────────

class TestBuildReplaySection:
    def test_baseline_entry_always_present(self):
        s = rr._build_replay_section(_replay_report())
        baseline_entries = [e for e in s["entries"] if "Baseline" in e["title"]]
        assert len(baseline_entries) == 1

    def test_high_wr_range_is_watch(self):
        s = rr._build_replay_section(_replay_report(wr_range=25.0))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs

    def test_low_wr_range_is_info(self):
        wr_entries = [e for e in rr._build_replay_section(
            _replay_report(wr_range=5.0))["entries"]
                      if "Sensitivity" in e["title"]]
        assert wr_entries[0]["severity"] == rr.SEV_INFO

    def test_high_robustness_warning_is_warning(self):
        rob = [{"severity": "HIGH", "type": "ALPHA_CONCENTRATION", "detail": "top3", "n": 5}]
        s = rr._build_replay_section(_replay_report(rob_warnings=rob))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WARNING in sevs

    def test_low_robustness_warning_is_watch(self):
        rob = [{"severity": "LOW", "type": "FEW_WINNERS", "detail": "small", "n": 2}]
        s = rr._build_replay_section(_replay_report(rob_warnings=rob))
        sevs = [e["severity"] for e in s["entries"]]
        assert rr.SEV_WATCH in sevs

    def test_no_wr_range_still_returns_section(self):
        report = {"baseline": {"n": 10}, "robustness_warnings": [],
                  "sensitivity": {}, "counterfactuals": {}}
        s = rr._build_replay_section(report)
        assert "entries" in s


# ── TestAssemble ──────────────────────────────────────────────────────────────

class TestAssemble:
    def _good_sections(self):
        return {"cal": rr._section("cal",
                                   [rr._entry(rr.SEV_INFO, "c", "t", "d")])}

    def test_report_has_all_required_keys(self):
        r = rr._assemble("test_report", self._good_sections(), row_count=10)
        for k in ("report_type", "overall_severity", "health_score", "health_label",
                  "sections", "executive_commentary", "trend_summary", "recommendations",
                  "top_findings", "changes", "row_count", "quality"):
            assert k in r, f"missing key: {k}"

    def test_report_type_stored(self):
        r = rr._assemble("my_custom_report", {})
        assert r["report_type"] == "my_custom_report"

    def test_row_count_stored(self):
        r = rr._assemble("t", {}, row_count=42)
        assert r["row_count"] == 42

    def test_executive_commentary_is_string(self):
        r = rr._assemble("t", self._good_sections())
        assert isinstance(r["executive_commentary"], str)
        assert len(r["executive_commentary"]) > 0

    def test_trend_summary_empty_without_previous(self):
        r = rr._assemble("t", self._good_sections(), previous_report=None)
        assert r["trend_summary"] == []

    def test_trend_summary_populated_with_previous(self):
        prev = _prev(score=80, sections={
            "cal": {"severity": rr.SEV_CRITICAL, "entries": []}
        })
        sections = {"cal": rr._section("cal",
                                       [rr._entry(rr.SEV_INFO, "c", "t", "d")])}
        r = rr._assemble("t", sections, previous_report=prev)
        # cal improved (CRITICAL → INFO)
        assert any("improving" in t for t in r["trend_summary"])

    def test_changes_empty_without_previous(self):
        r = rr._assemble("t", self._good_sections(), previous_report=None)
        assert r["changes"] == []

    def test_changes_populated_with_previous(self):
        prev = _prev(score=50, sev=rr.SEV_WARNING)
        r = rr._assemble("t", self._good_sections(), previous_report=prev)
        types = {c["type"] for c in r["changes"]}
        assert "HEALTH_SCORE_CHANGE" in types or "SEVERITY_CHANGE" in types

    def test_recommendations_include_section_defaults_for_warning(self):
        sections = {"cal": rr._section("cal",
                                       [rr._entry(rr.SEV_WARNING, "c", "t", "d")])}
        r = rr._assemble("t", sections)
        assert any("cal" in rec.lower() or "CAL" in rec for rec in r["recommendations"])

    def test_top_findings_sorted_by_severity(self):
        sections = {
            "s1": rr._section("s1", [rr._entry(rr.SEV_INFO, "c", "A", "d")]),
            "s2": rr._section("s2", [rr._entry(rr.SEV_CRITICAL, "c", "B", "d")]),
        }
        r = rr._assemble("t", sections)
        assert r["top_findings"][0]["severity"] == rr.SEV_CRITICAL

    def test_quality_is_dict_with_score_label(self):
        r = rr._assemble("t", self._good_sections())
        assert "score" in r["quality"]
        assert "label" in r["quality"]

    def test_extra_recs_included(self):
        r = rr._assemble("t", {}, extra_recs=["Do something important"])
        assert "Do something important" in r["recommendations"]

    def test_recommendations_deduped(self):
        r = rr._assemble("t", {}, extra_recs=["same rec", "same rec"])
        assert r["recommendations"].count("same rec") == 1


# ── TestBoundedOutputs ────────────────────────────────────────────────────────

class TestBoundedOutputs:
    def test_entries_per_section_capped(self):
        entries = [rr._entry(rr.SEV_INFO, "c", f"t{i}", "d") for i in range(20)]
        s = rr._section("s", entries)
        assert len(s["entries"]) == rr.MAX_ENTRIES_PER_SECTION

    def test_top_findings_capped(self):
        sections = {f"s{i}": rr._section(f"s{i}",
                                         [rr._entry(rr.SEV_WARNING, "c", f"t{i}", "d")])
                    for i in range(20)}
        r = rr._assemble("t", sections)
        assert len(r["top_findings"]) <= rr.MAX_TOP_FINDINGS

    def test_recommendations_capped(self):
        recs = [f"rec {i}" for i in range(20)]
        r = rr._assemble("t", {}, extra_recs=recs)
        assert len(r["recommendations"]) <= rr.MAX_RECOMMENDATIONS

    def test_trend_items_capped(self):
        # 10 worsening sections
        current  = {f"s{i}": {"severity": rr.SEV_CRITICAL, "entries": [], "name": f"s{i}"}
                    for i in range(10)}
        prev_sec = {f"s{i}": {"severity": rr.SEV_INFO,     "entries": [], "name": f"s{i}"}
                    for i in range(10)}
        trends = rr.detect_trends(current, {"sections": prev_sec})
        assert len(trends) <= rr.MAX_TREND_ITEMS


# ── TestDeterminism ───────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_inputs_same_output(self):
        cr   = _conf_report(quality="FAIR", ece=0.15, corr=0.2)
        rr1  = rr.calibration_research_report(cr)
        rr2  = rr.calibration_research_report(cr)
        assert rr1["health_score"]  == rr2["health_score"]
        assert rr1["health_label"]  == rr2["health_label"]
        assert rr1["overall_severity"] == rr2["overall_severity"]
        assert rr1["executive_commentary"] == rr2["executive_commentary"]

    def test_executive_commentary_deterministic(self):
        sections = {
            "cal": rr._section("cal", [rr._entry(rr.SEV_WARNING, "c", "Problem", "detail")]),
        }
        c1 = rr._executive_commentary("my_report", sections, 85, "GOOD", rr.SEV_WARNING)
        c2 = rr._executive_commentary("my_report", sections, 85, "GOOD", rr.SEV_WARNING)
        assert c1 == c2


# ── TestSparseHandling ────────────────────────────────────────────────────────

class TestSparseHandling:
    def test_conf_report_empty_dict(self):
        s = rr._build_calibration_section({})
        assert "entries" in s

    def test_conf_report_missing_calibration_key(self):
        s = rr._build_calibration_section({"overconfidence_flags": [], "warnings": []})
        assert "entries" in s

    def test_regime_report_empty_dict(self):
        s = rr._build_regime_section({})
        assert "entries" in s

    def test_meta_report_empty_dict(self):
        s = rr._build_meta_section({})
        assert "entries" in s

    def test_portfolio_report_empty_dict(self):
        s = rr._build_portfolio_section({})
        assert "entries" in s

    def test_adaptive_section_both_empty(self):
        s = rr._build_adaptive_section({}, {})
        assert "entries" in s

    def test_anomaly_section_empty(self):
        s = rr._build_anomaly_section({})
        assert "entries" in s

    def test_degradation_section_empty(self):
        s = rr._build_degradation_section({})
        assert "entries" in s

    def test_replay_section_empty(self):
        s = rr._build_replay_section({})
        assert "entries" in s

    def test_daily_report_all_none(self):
        r = rr.daily_operational_report()
        assert r["report_type"] == "daily_operational_report"
        assert r["sections"] == {}

    def test_weekly_report_all_none(self):
        r = rr.weekly_performance_report()
        assert r["report_type"] == "weekly_performance_report"

    def test_calibration_report_none(self):
        r = rr.calibration_research_report()
        assert r["sections"] == {}

    def test_regime_report_none(self):
        r = rr.regime_research_report()
        assert r["sections"] == {}

    def test_portfolio_report_none(self):
        r = rr.portfolio_research_report()
        assert r["sections"] == {}

    def test_adaptive_report_both_none(self):
        r = rr.adaptive_recommendation_report()
        assert r["sections"] == {}

    def test_anomaly_report_none(self):
        r = rr.anomaly_research_report()
        assert r["sections"] == {}

    def test_degradation_report_none(self):
        r = rr.degradation_research_report()
        assert r["sections"] == {}


# ── TestPublicReportBuilders ──────────────────────────────────────────────────

class TestDailyOperationalReport:
    def test_all_sections_present_when_all_provided(self):
        r = rr.daily_operational_report(
            conf_report=_conf_report(),
            regime_report=_regime_report(),
            meta_report=_meta_report(),
            portfolio_report=_portfolio_report(),
        )
        assert "calibration" in r["sections"]
        assert "regime"       in r["sections"]
        assert "meta"         in r["sections"]
        assert "portfolio"    in r["sections"]

    def test_hub_section_added_when_provided(self):
        hub = {"overall_health": "WATCH", "operational_alerts": [], "recommendations": []}
        r = rr.daily_operational_report(hub_report=hub)
        assert "hub" in r["sections"]

    def test_hub_critical_escalates_section(self):
        hub = {"overall_health": "CRITICAL",
               "operational_alerts": ["[CRITICAL] system down"],
               "recommendations": []}
        r = rr.daily_operational_report(hub_report=hub)
        assert r["sections"]["hub"]["severity"] == rr.SEV_CRITICAL

    def test_row_count_derived_from_subreports(self):
        r = rr.daily_operational_report(conf_report=_conf_report(row_count=99))
        assert r["row_count"] == 99

    def test_hub_recommendations_included(self):
        hub = {"overall_health": "HEALTHY",
               "operational_alerts": [],
               "recommendations": ["Fix X"]}
        r = rr.daily_operational_report(hub_report=hub)
        assert "Fix X" in r["recommendations"]


class TestWeeklyPerformanceReport:
    def test_sections_built(self):
        r = rr.weekly_performance_report(
            meta_report=_meta_report(),
            portfolio_report=_portfolio_report(),
            conf_report=_conf_report(),
            replay_report=_replay_report(),
        )
        assert "degradation"  in r["sections"]
        assert "portfolio"    in r["sections"]
        assert "calibration"  in r["sections"]
        assert "replay"       in r["sections"]

    def test_report_type(self):
        r = rr.weekly_performance_report()
        assert r["report_type"] == "weekly_performance_report"


class TestCalibrationResearchReport:
    def test_section_present(self):
        r = rr.calibration_research_report(_conf_report())
        assert "calibration" in r["sections"]

    def test_poor_quality_report_is_warning_or_worse(self):
        r = rr.calibration_research_report(_conf_report(quality="POOR"))
        assert rr.SEV_ORDER[r["overall_severity"]] >= rr.SEV_ORDER[rr.SEV_WARNING]

    def test_previous_report_triggers_changes(self):
        prev = _prev(score=50, sev=rr.SEV_CRITICAL)
        r = rr.calibration_research_report(_conf_report(), previous_report=prev)
        assert len(r["changes"]) > 0


class TestRegimeResearchReport:
    def test_section_present(self):
        r = rr.regime_research_report(_regime_report())
        assert "regime" in r["sections"]

    def test_inversions_trigger_warning_or_worse(self):
        r = rr.regime_research_report(_regime_report(inversion_count=3))
        assert rr.SEV_ORDER[r["overall_severity"]] >= rr.SEV_ORDER[rr.SEV_WARNING]


class TestPortfolioResearchReport:
    def test_both_sections_present(self):
        r = rr.portfolio_research_report(
            portfolio_report=_portfolio_report(),
            replay_report=_replay_report(),
        )
        assert "portfolio" in r["sections"]
        assert "replay"    in r["sections"]

    def test_weak_portfolio_health_escalates_overall(self):
        r = rr.portfolio_research_report(
            portfolio_report=_portfolio_report(health="WEAK"))
        assert rr.SEV_ORDER[r["overall_severity"]] >= rr.SEV_ORDER[rr.SEV_WARNING]


class TestAdaptiveRecommendationReport:
    def test_section_added_when_data_present(self):
        r = rr.adaptive_recommendation_report(
            weights_report=_weights_report(boosted=["RSI"]),
            observer_report=_observer_report(n_unstable=1),
        )
        assert "adaptive" in r["sections"]

    def test_both_none_produces_empty_sections(self):
        r = rr.adaptive_recommendation_report()
        assert "adaptive" not in r["sections"]

    def test_high_drift_escalates(self):
        r = rr.adaptive_recommendation_report(
            weights_report=_weights_report(),
            observer_report=_observer_report(drift_count=5),
        )
        assert rr.SEV_ORDER[r["overall_severity"]] >= rr.SEV_ORDER[rr.SEV_WARNING]


class TestAnomalyResearchReport:
    def test_section_present(self):
        r = rr.anomaly_research_report(_audit_batch())
        assert "audit" in r["sections"]

    def test_many_high_anomalies_escalates(self):
        r = rr.anomaly_research_report(_audit_batch(high_count=5))
        assert rr.SEV_ORDER[r["overall_severity"]] >= rr.SEV_ORDER[rr.SEV_WARNING]


class TestDegradationResearchReport:
    def test_section_present(self):
        r = rr.degradation_research_report(_meta_report())
        assert "degradation" in r["sections"]

    def test_observation_only_is_critical(self):
        r = rr.degradation_research_report(_meta_report(
            safeguards=[{"recommendation": "OBSERVATION_ONLY"}]))
        assert r["overall_severity"] == rr.SEV_CRITICAL

    def test_no_degradation_is_info(self):
        r = rr.degradation_research_report(_meta_report())
        assert r["overall_severity"] == rr.SEV_INFO
