"""
Phase A20 — Compounding planner tests.

Coverage:
  - classify_position_to_bucket (pure)
  - compute_current_allocation (pure)
  - compute_target_allocation (pure, regime/risk adjustments)
  - compute_drift (pure)
  - compute_rebalance_urgency (pure)
  - compute_priority_areas (pure)
  - compute_cash_deployment_guidance (pure)
  - compute_contribution_guidance (pure, TFSA handling)
  - compute_risk_reduction_guidance (pure)
  - compute_strategy_alignment_notes (pure)
  - compute_bucket_dollar_values (pure)
  - project_single_scenario (pure, math)
  - compute_all_projections (pure)
  - generate_planner_output (pure)
  - save_planner_snapshot / get_latest_planner_snapshot / get_planner_history (DB)
  - Immutable snapshots (no UPDATE/DELETE)
  - No trading calls in source
  - API: GET /planner/summary, GET /planner/projections, POST /planner/refresh
  - Auth on refresh
"""
import importlib
import json
import os
import sys

import pytest

BOT_DIR = os.path.dirname(os.path.dirname(__file__))
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

import database
import compounding_planner as cp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setattr(database, "DB_PATH", str(db_file))

    import sqlite3

    def _conn():
        c = sqlite3.connect(str(db_file), timeout=5, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(database, "get_connection", _conn)

    conn = sqlite3.connect(str(db_file))
    for ddl in cp._PLANNER_DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()

    importlib.reload(cp)
    yield


# ---------------------------------------------------------------------------
# Tests: constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_allocation_buckets_count(self):
        assert len(cp.ALLOCATION_BUCKETS) == 7

    def test_primary_buckets_count(self):
        assert len(cp.PRIMARY_BUCKETS) == 5

    def test_overlay_buckets_count(self):
        assert len(cp.OVERLAY_BUCKETS) == 2

    def test_allocation_buckets_is_primary_plus_overlay(self):
        assert set(cp.ALLOCATION_BUCKETS) == set(cp.PRIMARY_BUCKETS + cp.OVERLAY_BUCKETS)

    def test_rebalance_urgency_levels(self):
        assert cp.REBALANCE_URGENCY_LEVELS == ["NONE", "LOW", "MEDIUM", "HIGH"]

    def test_projection_scenarios_count(self):
        assert len(cp.PROJECTION_SCENARIOS) == 4

    def test_projection_horizons(self):
        assert set(cp.PROJECTION_HORIZONS) == {1, 3, 5, 10}

    def test_primary_targets_sum_to_100(self):
        total = sum(cp._DEFAULT_PRIMARY_TARGETS.values())
        assert abs(total - 100.0) < 0.01


# ---------------------------------------------------------------------------
# Tests: classify_position_to_bucket
# ---------------------------------------------------------------------------

class TestClassifyPositionToBucket:
    def test_spy_core_index(self):
        assert cp.classify_position_to_bucket("SPY") == "CORE_INDEX"

    def test_qqq_core_index(self):
        assert cp.classify_position_to_bucket("QQQ") == "CORE_INDEX"

    def test_vfv_to_core_index(self):
        assert cp.classify_position_to_bucket("VFV.TO") == "CORE_INDEX"

    def test_xeqt_to_core_index(self):
        assert cp.classify_position_to_bucket("XEQT.TO") == "CORE_INDEX"

    def test_msft_quality_growth(self):
        assert cp.classify_position_to_bucket("MSFT") == "QUALITY_GROWTH"

    def test_aapl_quality_growth(self):
        assert cp.classify_position_to_bucket("AAPL") == "QUALITY_GROWTH"

    def test_shop_to_quality_growth(self):
        assert cp.classify_position_to_bucket("SHOP.TO") == "QUALITY_GROWTH"

    def test_ry_to_quality_growth(self):
        assert cp.classify_position_to_bucket("RY.TO") == "QUALITY_GROWTH"

    def test_nvda_alpha_opportunity(self):
        assert cp.classify_position_to_bucket("NVDA") == "ALPHA_OPPORTUNITY"

    def test_amd_alpha_opportunity(self):
        assert cp.classify_position_to_bucket("AMD") == "ALPHA_OPPORTUNITY"

    def test_tsm_alpha_opportunity(self):
        assert cp.classify_position_to_bucket("TSM") == "ALPHA_OPPORTUNITY"

    def test_pltr_alpha_opportunity(self):
        assert cp.classify_position_to_bucket("PLTR") == "ALPHA_OPPORTUNITY"

    def test_unknown_speculative(self):
        assert cp.classify_position_to_bucket("UNKNWN") == "SPECULATIVE"

    def test_lowercase_handled(self):
        assert cp.classify_position_to_bucket("nvda") == "ALPHA_OPPORTUNITY"

    def test_empty_speculative(self):
        assert cp.classify_position_to_bucket("") == "SPECULATIVE"


# ---------------------------------------------------------------------------
# Tests: compute_current_allocation
# ---------------------------------------------------------------------------

class TestComputeCurrentAllocation:
    def _make_pos(self, ticker, mv):
        return {"ticker": ticker, "market_value": mv}

    def test_empty_portfolio_all_cash(self):
        alloc = cp.compute_current_allocation([], 1000.0, 1000.0)
        assert alloc["CASH_RESERVE"] == 100.0
        assert alloc["CORE_INDEX"] == 0.0

    def test_single_etf_position(self):
        positions = [self._make_pos("SPY", 9000.0)]
        alloc = cp.compute_current_allocation(positions, 1000.0, 10000.0)
        assert alloc["CORE_INDEX"] == pytest.approx(90.0)
        assert alloc["CASH_RESERVE"] == pytest.approx(10.0)

    def test_primary_buckets_sum_to_100(self):
        positions = [
            self._make_pos("SPY",  5000.0),
            self._make_pos("NVDA", 3000.0),
            self._make_pos("AAPL", 1000.0),
        ]
        alloc = cp.compute_current_allocation(positions, 1000.0, 10000.0)
        primary_sum = sum(alloc[b] for b in cp.PRIMARY_BUCKETS)
        assert abs(primary_sum - 100.0) < 0.5

    def test_canadian_overlay(self):
        positions = [
            self._make_pos("VFV.TO", 4000.0),
            self._make_pos("SPY",    6000.0),
        ]
        alloc = cp.compute_current_allocation(positions, 0.0, 10000.0)
        assert alloc["CANADIAN_EXPOSURE"] == pytest.approx(40.0)
        assert alloc["USD_EXPOSURE"] == pytest.approx(60.0)

    def test_all_cad_exposure_100(self):
        positions = [self._make_pos("VFV.TO", 9000.0)]
        alloc = cp.compute_current_allocation(positions, 0.0, 9000.0)
        assert alloc["CANADIAN_EXPOSURE"] == pytest.approx(100.0)
        assert alloc["USD_EXPOSURE"] == pytest.approx(0.0)

    def test_zero_total_no_crash(self):
        alloc = cp.compute_current_allocation([], 0.0, 0.0)
        assert isinstance(alloc, dict)

    def test_mixed_portfolio(self):
        positions = [
            self._make_pos("SPY",    3000.0),
            self._make_pos("NVDA",   2000.0),
            self._make_pos("MSFT",   2000.0),
            self._make_pos("PLTR",   1000.0),
        ]
        alloc = cp.compute_current_allocation(positions, 2000.0, 10000.0)
        assert alloc["CORE_INDEX"] == pytest.approx(30.0)
        assert alloc["ALPHA_OPPORTUNITY"] == pytest.approx(30.0)
        assert alloc["CASH_RESERVE"] == pytest.approx(20.0)

    def test_all_buckets_present(self):
        alloc = cp.compute_current_allocation([], 0.0, 1.0)
        for b in cp.ALLOCATION_BUCKETS:
            assert b in alloc


# ---------------------------------------------------------------------------
# Tests: compute_target_allocation
# ---------------------------------------------------------------------------

class TestComputeTargetAllocation:
    def test_neutral_regime_returns_near_defaults(self):
        target = cp.compute_target_allocation("NEUTRAL", 50.0, 0.0)
        # Should be close to defaults (may vary slightly after normalization)
        assert 25.0 <= target["CORE_INDEX"] <= 35.0
        assert 10.0 <= target["CASH_RESERVE"] <= 20.0

    def test_primary_buckets_sum_to_100(self):
        for regime in ("NEUTRAL", "RISK_OFF", "PANIC", "RISK_ON"):
            target = cp.compute_target_allocation(regime, 50.0, 0.0)
            total = sum(target[b] for b in cp.PRIMARY_BUCKETS)
            assert abs(total - 100.0) < 0.5, f"Failed for regime={regime}"

    def test_panic_increases_cash(self):
        neutral = cp.compute_target_allocation("NEUTRAL", 50.0, 0.0)
        panic   = cp.compute_target_allocation("PANIC",   50.0, 0.0)
        assert panic["CASH_RESERVE"] > neutral["CASH_RESERVE"]

    def test_panic_reduces_speculative(self):
        neutral = cp.compute_target_allocation("NEUTRAL", 50.0, 0.0)
        panic   = cp.compute_target_allocation("PANIC",   50.0, 0.0)
        assert panic["SPECULATIVE"] < neutral["SPECULATIVE"]

    def test_risk_off_increases_cash(self):
        neutral  = cp.compute_target_allocation("NEUTRAL",  50.0, 0.0)
        risk_off = cp.compute_target_allocation("RISK_OFF", 50.0, 0.0)
        assert risk_off["CASH_RESERVE"] > neutral["CASH_RESERVE"]

    def test_risk_on_reduces_cash(self):
        neutral = cp.compute_target_allocation("NEUTRAL", 50.0, 0.0)
        risk_on = cp.compute_target_allocation("RISK_ON", 50.0, 0.0)
        assert risk_on["CASH_RESERVE"] < neutral["CASH_RESERVE"]

    def test_high_risk_score_reduces_speculative(self):
        low  = cp.compute_target_allocation("NEUTRAL", 30.0, 0.0)
        high = cp.compute_target_allocation("NEUTRAL", 80.0, 0.0)
        assert high["SPECULATIVE"] < low["SPECULATIVE"]

    def test_high_risk_score_increases_core(self):
        low  = cp.compute_target_allocation("NEUTRAL", 20.0, 0.0)
        high = cp.compute_target_allocation("NEUTRAL", 80.0, 0.0)
        assert high["CORE_INDEX"] > low["CORE_INDEX"]

    def test_large_stress_reduces_speculative(self):
        safe   = cp.compute_target_allocation("NEUTRAL", 50.0,   0.0)
        stressed=cp.compute_target_allocation("NEUTRAL", 50.0, -25.0)
        assert stressed["SPECULATIVE"] < safe["SPECULATIVE"]

    def test_overlay_targets_present(self):
        target = cp.compute_target_allocation()
        assert "CANADIAN_EXPOSURE" in target
        assert "USD_EXPOSURE" in target

    def test_no_bucket_exceeds_80(self):
        for regime in ("NEUTRAL", "RISK_OFF", "PANIC", "RISK_ON"):
            target = cp.compute_target_allocation(regime, 20.0, -30.0)
            for b in cp.PRIMARY_BUCKETS:
                assert target[b] <= 80.0, f"Bucket {b} exceeded 80% for regime={regime}"

    def test_no_bucket_below_zero(self):
        target = cp.compute_target_allocation("PANIC", 90.0, -30.0)
        for b in cp.PRIMARY_BUCKETS:
            assert target[b] >= 0.0


# ---------------------------------------------------------------------------
# Tests: compute_drift
# ---------------------------------------------------------------------------

class TestComputeDrift:
    def test_no_drift_when_equal(self):
        alloc = {"CORE_INDEX": 30.0, "QUALITY_GROWTH": 25.0,
                 "ALPHA_OPPORTUNITY": 20.0, "SPECULATIVE": 10.0,
                 "CASH_RESERVE": 15.0, "CANADIAN_EXPOSURE": 30.0, "USD_EXPOSURE": 65.0}
        drift = cp.compute_drift(alloc, alloc)
        for bucket in cp.ALLOCATION_BUCKETS:
            assert drift[bucket] == pytest.approx(0.0)

    def test_positive_drift_when_over(self):
        current = {"CORE_INDEX": 40.0, "QUALITY_GROWTH": 25.0,
                   "ALPHA_OPPORTUNITY": 15.0, "SPECULATIVE": 5.0,
                   "CASH_RESERVE": 15.0, "CANADIAN_EXPOSURE": 30.0, "USD_EXPOSURE": 65.0}
        target  = {"CORE_INDEX": 30.0, "QUALITY_GROWTH": 25.0,
                   "ALPHA_OPPORTUNITY": 20.0, "SPECULATIVE": 10.0,
                   "CASH_RESERVE": 15.0, "CANADIAN_EXPOSURE": 30.0, "USD_EXPOSURE": 65.0}
        drift = cp.compute_drift(current, target)
        assert drift["CORE_INDEX"] == pytest.approx(10.0)
        assert drift["ALPHA_OPPORTUNITY"] == pytest.approx(-5.0)

    def test_all_buckets_present(self):
        alloc = {b: 0.0 for b in cp.ALLOCATION_BUCKETS}
        drift = cp.compute_drift(alloc, alloc)
        for b in cp.ALLOCATION_BUCKETS:
            assert b in drift

    def test_missing_current_bucket_treated_as_zero(self):
        current = {"CORE_INDEX": 30.0}
        target  = {"CORE_INDEX": 30.0, "QUALITY_GROWTH": 25.0,
                   "ALPHA_OPPORTUNITY": 20.0, "SPECULATIVE": 10.0,
                   "CASH_RESERVE": 15.0, "CANADIAN_EXPOSURE": 30.0, "USD_EXPOSURE": 65.0}
        drift = cp.compute_drift(current, target)
        assert drift["QUALITY_GROWTH"] == pytest.approx(-25.0)


# ---------------------------------------------------------------------------
# Tests: compute_rebalance_urgency
# ---------------------------------------------------------------------------

class TestComputeRebalanceUrgency:
    def _drift(self, core=0.0):
        return {b: 0.0 for b in cp.ALLOCATION_BUCKETS} | {"CORE_INDEX": core}

    def test_no_drift_is_none(self):
        assert cp.compute_rebalance_urgency(self._drift(0.0)) == "NONE"

    def test_small_drift_is_none(self):
        assert cp.compute_rebalance_urgency(self._drift(4.9)) == "NONE"

    def test_5_is_low(self):
        assert cp.compute_rebalance_urgency(self._drift(5.0)) == "LOW"

    def test_10_is_medium(self):
        assert cp.compute_rebalance_urgency(self._drift(10.0)) == "MEDIUM"

    def test_20_is_high(self):
        assert cp.compute_rebalance_urgency(self._drift(20.0)) == "HIGH"

    def test_negative_drift_uses_abs(self):
        assert cp.compute_rebalance_urgency(self._drift(-15.0)) == "MEDIUM"

    def test_urgency_in_valid_values(self):
        for d in [0.0, 4.9, 5.0, 10.0, 20.0, 25.0]:
            result = cp.compute_rebalance_urgency(self._drift(d))
            assert result in cp.REBALANCE_URGENCY_LEVELS


# ---------------------------------------------------------------------------
# Tests: compute_priority_areas
# ---------------------------------------------------------------------------

class TestComputePriorityAreas:
    def _drift(self, **kwargs):
        d = {b: 0.0 for b in cp.ALLOCATION_BUCKETS}
        d.update(kwargs)
        return d

    def test_no_drift_returns_empty(self):
        assert cp.compute_priority_areas(self._drift()) == []

    def test_small_drift_not_included(self):
        areas = cp.compute_priority_areas(self._drift(CORE_INDEX=2.0))
        assert areas == []

    def test_large_drift_included(self):
        areas = cp.compute_priority_areas(self._drift(CORE_INDEX=10.0))
        assert len(areas) == 1
        assert areas[0]["bucket"] == "CORE_INDEX"
        assert areas[0]["action"] == "REDUCE"

    def test_under_allocated_action_is_increase(self):
        areas = cp.compute_priority_areas(self._drift(SPECULATIVE=-8.0))
        assert areas[0]["action"] == "INCREASE"

    def test_sorted_by_abs_drift_desc(self):
        areas = cp.compute_priority_areas(self._drift(CORE_INDEX=15.0, SPECULATIVE=-8.0))
        assert areas[0]["drift_pct"] == 15.0
        assert areas[1]["drift_pct"] == -8.0

    def test_overlay_buckets_excluded(self):
        areas = cp.compute_priority_areas(self._drift(CANADIAN_EXPOSURE=20.0))
        tickers = [a["bucket"] for a in areas]
        assert "CANADIAN_EXPOSURE" not in tickers

    def test_all_keys_present(self):
        areas = cp.compute_priority_areas(self._drift(CORE_INDEX=10.0))
        assert "bucket" in areas[0]
        assert "drift_pct" in areas[0]
        assert "action" in areas[0]


# ---------------------------------------------------------------------------
# Tests: compute_cash_deployment_guidance
# ---------------------------------------------------------------------------

class TestComputeCashDeploymentGuidance:
    def _alloc(self, cash_pct, other_pct=0.0):
        return {b: 0.0 for b in cp.ALLOCATION_BUCKETS} | {
            "CASH_RESERVE": cash_pct, "CORE_INDEX": other_pct
        }

    def _target(self, cash_target=15.0):
        return {b: 0.0 for b in cp.ALLOCATION_BUCKETS} | {"CASH_RESERVE": cash_target}

    def _drift(self, core_drift=0.0):
        return {b: 0.0 for b in cp.ALLOCATION_BUCKETS} | {"CORE_INDEX": core_drift}

    def test_no_portfolio_data(self):
        result = cp.compute_cash_deployment_guidance(0, 0, {}, {}, {})
        assert "No portfolio data" in result

    def test_cash_above_target_with_underweight(self):
        current = self._alloc(25.0)
        target  = self._target(15.0)
        drift   = self._drift(-5.0)  # CORE_INDEX underweight
        result  = cp.compute_cash_deployment_guidance(2500, 10000, current, target, drift)
        assert "above" in result.lower() or "25%" in result

    def test_cash_below_target(self):
        current = self._alloc(5.0)
        target  = self._target(15.0)
        result  = cp.compute_cash_deployment_guidance(500, 10000, current, target, {})
        assert "below" in result.lower() or "5%" in result

    def test_cash_near_target(self):
        current = self._alloc(15.0)
        target  = self._target(15.0)
        result  = cp.compute_cash_deployment_guidance(1500, 10000, current, target, {})
        assert "near" in result.lower() or "target" in result.lower()

    def test_returns_string(self):
        result = cp.compute_cash_deployment_guidance(1000, 10000, self._alloc(10), self._target(), {})
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Tests: compute_contribution_guidance
# ---------------------------------------------------------------------------

class TestComputeContributionGuidance:
    def test_none_room(self):
        result = cp.compute_contribution_guidance(None, 0.0, 0.0)
        assert "not set" in result.lower() or "not" in result.lower()

    def test_zero_room(self):
        result = cp.compute_contribution_guidance(0.0, 0.0, 0.0)
        assert "no" in result.lower() or "room" in result.lower()

    def test_negative_room(self):
        result = cp.compute_contribution_guidance(-100.0, 0.0, 0.0)
        assert "no" in result.lower() or "review" in result.lower()

    def test_small_room(self):
        result = cp.compute_contribution_guidance(3000.0, 0.0, 0.0)
        assert "limited" in result.lower() or "3,000" in result or "3000" in result

    def test_moderate_room(self):
        result = cp.compute_contribution_guidance(15000.0, 0.0, 0.0)
        assert "moderate" in result.lower() or "15,000" in result

    def test_large_room(self):
        result = cp.compute_contribution_guidance(50000.0, 0.0, 0.0)
        assert "substantial" in result.lower() or "50,000" in result

    def test_no_tax_advice_claims(self):
        for room in [None, 0.0, 5000.0, 50000.0]:
            result = cp.compute_contribution_guidance(room, 0.0, 0.0)
            # Should NOT make specific tax claims
            assert "tax-free" not in result.lower() or "educational" in result.lower()

    def test_returns_string(self):
        result = cp.compute_contribution_guidance(10000.0, 500.0, 50000.0)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Tests: compute_risk_reduction_guidance
# ---------------------------------------------------------------------------

class TestComputeRiskReductionGuidance:
    def test_no_risk_report(self):
        result = cp.compute_risk_reduction_guidance(None, 50.0, "NEUTRAL")
        assert "no risk report" in result.lower() or "not available" in result.lower() or "50" in result

    def test_high_risk_score(self):
        report = {"portfolio_risk_score": 75.0, "recommended_actions": ["Reduce speculative exposure"]}
        result = cp.compute_risk_reduction_guidance(report, 75.0, "NEUTRAL")
        assert "elevated" in result.lower() or "75" in result

    def test_moderate_risk_score(self):
        report = {"portfolio_risk_score": 55.0, "recommended_actions": []}
        result = cp.compute_risk_reduction_guidance(report, 55.0, "NEUTRAL")
        assert "moderate" in result.lower() or "55" in result

    def test_low_risk_score(self):
        report = {"portfolio_risk_score": 30.0, "recommended_actions": []}
        result = cp.compute_risk_reduction_guidance(report, 30.0, "NEUTRAL")
        assert "acceptable" in result.lower() or "30" in result

    def test_risk_off_regime_mentioned(self):
        report = {"portfolio_risk_score": 75.0, "recommended_actions": ["Reduce"]}
        result = cp.compute_risk_reduction_guidance(report, 75.0, "RISK_OFF")
        assert "regime" in result.lower() or "risk" in result.lower()

    def test_returns_string(self):
        result = cp.compute_risk_reduction_guidance({}, 50.0, "NEUTRAL")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Tests: compute_strategy_alignment_notes
# ---------------------------------------------------------------------------

class TestComputeStrategyAlignmentNotes:
    def _alloc(self, core=30.0, quality=25.0, alpha=20.0, spec=10.0, cash=15.0):
        return {
            "CORE_INDEX": core, "QUALITY_GROWTH": quality,
            "ALPHA_OPPORTUNITY": alpha, "SPECULATIVE": spec, "CASH_RESERVE": cash,
            "CANADIAN_EXPOSURE": 30.0, "USD_EXPOSURE": 65.0,
        }

    def test_none_summary_returns_list_with_message(self):
        notes = cp.compute_strategy_alignment_notes(None, self._alloc(), self._alloc())
        assert isinstance(notes, list)
        assert len(notes) >= 1

    def test_with_top_strategy(self):
        summary = {"top_strategies": [{"strategy": "AI_SEMI_MOMENTUM", "risk_adjusted_score": 80.0}],
                   "behavior_metrics": {}}
        notes = cp.compute_strategy_alignment_notes(summary, self._alloc(), self._alloc())
        combined = " ".join(notes)
        assert "AI_SEMI_MOMENTUM" in combined or "strongest" in combined.lower()

    def test_overused_strategy_flagged(self):
        summary = {"top_strategies": [], "behavior_metrics": {"overused": ["BREAKOUT_MOMENTUM"]}}
        notes = cp.compute_strategy_alignment_notes(summary, self._alloc(), self._alloc())
        combined = " ".join(notes)
        assert "BREAKOUT_MOMENTUM" in combined or "overused" in combined.lower()

    def test_weak_thesis_flagged(self):
        summary = {"top_strategies": [], "behavior_metrics": {"weak_thesis": ["SHORT_SQUEEZE"]}}
        notes = cp.compute_strategy_alignment_notes(summary, self._alloc(), self._alloc())
        combined = " ".join(notes)
        assert "SHORT_SQUEEZE" in combined or "thesis" in combined.lower()

    def test_core_underweight_flagged(self):
        summary = {"top_strategies": [], "behavior_metrics": {}}
        current = self._alloc(core=10.0)  # very low core
        target  = self._alloc(core=30.0)  # target is higher
        notes   = cp.compute_strategy_alignment_notes(summary, current, target)
        combined = " ".join(notes)
        assert "CORE_INDEX" in combined or "index" in combined.lower()

    def test_returns_list(self):
        notes = cp.compute_strategy_alignment_notes(None, self._alloc(), self._alloc())
        assert isinstance(notes, list)

    def test_never_empty_list(self):
        notes = cp.compute_strategy_alignment_notes({}, self._alloc(), self._alloc())
        assert len(notes) >= 1


# ---------------------------------------------------------------------------
# Tests: compute_bucket_dollar_values
# ---------------------------------------------------------------------------

class TestComputeBucketDollarValues:
    def test_cash_in_cash_reserve(self):
        vals = cp.compute_bucket_dollar_values([], 5000.0)
        assert vals["CASH_RESERVE"] == pytest.approx(5000.0)

    def test_etf_in_core_index(self):
        positions = [{"ticker": "SPY", "market_value": 10000.0}]
        vals = cp.compute_bucket_dollar_values(positions, 0.0)
        assert vals["CORE_INDEX"] == pytest.approx(10000.0)

    def test_unknown_in_speculative(self):
        positions = [{"ticker": "XYZ", "market_value": 3000.0}]
        vals = cp.compute_bucket_dollar_values(positions, 0.0)
        assert vals["SPECULATIVE"] == pytest.approx(3000.0)

    def test_all_buckets_present(self):
        vals = cp.compute_bucket_dollar_values([], 0.0)
        for b in cp.PRIMARY_BUCKETS:
            assert b in vals

    def test_accumulates_multiple_positions(self):
        positions = [
            {"ticker": "SPY", "market_value": 5000.0},
            {"ticker": "QQQ", "market_value": 3000.0},
        ]
        vals = cp.compute_bucket_dollar_values(positions, 0.0)
        assert vals["CORE_INDEX"] == pytest.approx(8000.0)


# ---------------------------------------------------------------------------
# Tests: project_single_scenario
# ---------------------------------------------------------------------------

class TestProjectSingleScenario:
    def _bucket_vals(self, total=10000.0):
        return {
            "CORE_INDEX":        total * 0.30,
            "QUALITY_GROWTH":    total * 0.25,
            "ALPHA_OPPORTUNITY": total * 0.20,
            "SPECULATIVE":       total * 0.10,
            "CASH_RESERVE":      total * 0.15,
        }

    def test_base_1y_exceeds_starting(self):
        proj = cp.project_single_scenario(self._bucket_vals(), 0.0, "base", 1)
        assert proj["projected_value"] > proj["starting_value"]

    def test_contributions_increase_projected_value(self):
        p0 = cp.project_single_scenario(self._bucket_vals(), 0.0,    "base", 5)
        p1 = cp.project_single_scenario(self._bucket_vals(), 500.0,  "base", 5)
        assert p1["projected_value"] > p0["projected_value"]

    def test_conservative_lower_than_base(self):
        pcons = cp.project_single_scenario(self._bucket_vals(), 0.0, "conservative", 10)
        pbase = cp.project_single_scenario(self._bucket_vals(), 0.0, "base",         10)
        assert pbase["projected_value"] > pcons["projected_value"]

    def test_aggressive_higher_than_base(self):
        pbase = cp.project_single_scenario(self._bucket_vals(), 0.0, "base",         10)
        pagg  = cp.project_single_scenario(self._bucket_vals(), 0.0, "aggressive",   10)
        assert pagg["projected_value"] > pbase["projected_value"]

    def test_downside_lower_than_starting(self):
        proj = cp.project_single_scenario(self._bucket_vals(), 0.0, "downside", 1)
        # With negative returns and no contributions, value should fall
        assert proj["projected_value"] < proj["starting_value"]

    def test_required_keys_present(self):
        proj = cp.project_single_scenario(self._bucket_vals(), 500.0, "base", 3)
        required = {"scenario", "years", "starting_value", "projected_value",
                    "total_contributed", "contribution_impact",
                    "compounding_impact", "bucket_values"}
        assert required.issubset(set(proj.keys()))

    def test_years_in_result(self):
        proj = cp.project_single_scenario(self._bucket_vals(), 0.0, "base", 5)
        assert proj["years"] == 5

    def test_zero_portfolio_no_crash(self):
        proj = cp.project_single_scenario({b: 0.0 for b in cp.PRIMARY_BUCKETS}, 500.0, "base", 1)
        assert proj["projected_value"] >= 0.0

    def test_total_contributed_correct(self):
        mc   = 1000.0
        proj = cp.project_single_scenario(self._bucket_vals(), mc, "base", 3)
        assert proj["total_contributed"] == pytest.approx(mc * 12 * 3)

    def test_projected_value_never_negative(self):
        # Even downside scenario should not produce negative values
        proj = cp.project_single_scenario(self._bucket_vals(), 0.0, "downside", 10)
        assert proj["projected_value"] >= 0.0

    def test_bucket_values_all_present(self):
        proj = cp.project_single_scenario(self._bucket_vals(), 0.0, "base", 1)
        for b in cp.PRIMARY_BUCKETS:
            assert b in proj["bucket_values"]


# ---------------------------------------------------------------------------
# Tests: compute_all_projections
# ---------------------------------------------------------------------------

class TestComputeAllProjections:
    def _bvals(self, total=10000.0):
        each = total / len(cp.PRIMARY_BUCKETS)
        return {b: each for b in cp.PRIMARY_BUCKETS}

    def test_all_scenarios_present(self):
        result = cp.compute_all_projections(self._bvals(), 500.0)
        for s in cp.PROJECTION_SCENARIOS:
            assert s in result

    def test_each_scenario_has_4_horizons(self):
        result = cp.compute_all_projections(self._bvals(), 500.0)
        for s in cp.PROJECTION_SCENARIOS:
            assert len(result[s]) == 4

    def test_horizons_are_correct(self):
        result = cp.compute_all_projections(self._bvals(), 500.0)
        years  = [p["years"] for p in result["base"]]
        assert sorted(years) == sorted(cp.PROJECTION_HORIZONS)

    def test_monthly_contribution_in_result(self):
        result = cp.compute_all_projections(self._bvals(), 750.0)
        assert result["monthly_contribution"] == pytest.approx(750.0)

    def test_starting_value_in_result(self):
        result = cp.compute_all_projections(self._bvals(10000.0), 0.0)
        assert result["starting_value"] == pytest.approx(10000.0)


# ---------------------------------------------------------------------------
# Tests: generate_planner_output
# ---------------------------------------------------------------------------

class TestGeneratePlannerOutput:
    def _portfolio(self, positions=None, cash=1000.0, total=10000.0):
        return {
            "positions":  positions or [{"ticker": "SPY", "market_value": total - cash}],
            "aggregates": {"cash": cash, "total_portfolio_value": total},
        }

    def test_returns_dict(self):
        result = cp.generate_planner_output(
            self._portfolio(), {}, None, None, None, None
        )
        assert isinstance(result, dict)

    def test_required_keys_present(self):
        result = cp.generate_planner_output(self._portfolio(), {}, None, None, None, None)
        required = {
            "portfolio_value", "cash", "regime", "risk_score",
            "current_allocation", "target_allocation", "drift",
            "rebalance_urgency", "priority_areas",
            "cash_deployment_guidance", "contribution_guidance",
            "risk_reduction_guidance", "strategy_alignment_notes",
        }
        assert required.issubset(set(result.keys()))

    def test_regime_defaults_neutral(self):
        result = cp.generate_planner_output(self._portfolio(), {}, None, None, None, None)
        assert result["regime"] == "NEUTRAL"

    def test_regime_from_ctx(self):
        ctx = {"overall_regime": "RISK_OFF", "available": True}
        result = cp.generate_planner_output(self._portfolio(), {}, ctx, None, None, None)
        assert result["regime"] == "RISK_OFF"

    def test_risk_score_defaults_50(self):
        result = cp.generate_planner_output(self._portfolio(), {}, None, None, None, None)
        assert result["risk_score"] == pytest.approx(50.0)

    def test_urgency_in_valid_values(self):
        result = cp.generate_planner_output(self._portfolio(), {}, None, None, None, None)
        assert result["rebalance_urgency"] in cp.REBALANCE_URGENCY_LEVELS

    def test_strategy_alignment_is_list(self):
        result = cp.generate_planner_output(self._portfolio(), {}, None, None, None, None)
        assert isinstance(result["strategy_alignment_notes"], list)

    def test_priority_areas_is_list(self):
        result = cp.generate_planner_output(self._portfolio(), {}, None, None, None, None)
        assert isinstance(result["priority_areas"], list)

    def test_contribution_room_passed_to_guidance(self):
        settings = {"contribution_room": 50000.0}
        result   = cp.generate_planner_output(self._portfolio(), settings, None, None, None, None)
        assert "50,000" in result["contribution_guidance"] or "substantial" in result["contribution_guidance"].lower()


# ---------------------------------------------------------------------------
# Tests: DB functions
# ---------------------------------------------------------------------------

class TestSavePlannerSnapshot:
    def _output(self, portfolio_value=10000.0):
        return {
            "portfolio_value":          portfolio_value,
            "cash":                     1000.0,
            "regime":                   "NEUTRAL",
            "risk_score":               50.0,
            "rebalance_urgency":        "LOW",
            "current_allocation":       {"CORE_INDEX": 30.0},
            "target_allocation":        {"CORE_INDEX": 30.0},
            "drift":                    {"CORE_INDEX": 0.0},
            "priority_areas":           [],
            "cash_deployment_guidance": "Test guidance",
            "contribution_guidance":    "Test contribution",
            "risk_reduction_guidance":  "Test risk",
            "strategy_alignment_notes": ["Note 1"],
        }

    def _projections(self):
        return {"monthly_contribution": 500.0, "starting_value": 10000.0}

    def test_save_returns_dict_with_snapshot_id(self):
        saved = cp.save_planner_snapshot(self._output(), self._projections())
        assert "snapshot_id" in saved
        assert saved["snapshot_id"].startswith("PLN-")

    def test_retrieve_after_save(self):
        cp.save_planner_snapshot(self._output(), self._projections())
        snap = cp.get_latest_planner_snapshot()
        assert snap is not None
        assert snap["snapshot_id"].startswith("PLN-")

    def test_current_allocation_deserialized(self):
        cp.save_planner_snapshot(self._output(), self._projections())
        snap = cp.get_latest_planner_snapshot()
        assert isinstance(snap["current_allocation"], dict)

    def test_priority_areas_deserialized(self):
        cp.save_planner_snapshot(self._output(), self._projections())
        snap = cp.get_latest_planner_snapshot()
        assert isinstance(snap["priority_areas"], list)

    def test_strategy_notes_deserialized(self):
        cp.save_planner_snapshot(self._output(), self._projections())
        snap = cp.get_latest_planner_snapshot()
        assert isinstance(snap["strategy_alignment_notes"], list)

    def test_projections_deserialized(self):
        cp.save_planner_snapshot(self._output(), self._projections())
        snap = cp.get_latest_planner_snapshot()
        assert isinstance(snap["projections"], dict)

    def test_no_snapshot_returns_none(self):
        assert cp.get_latest_planner_snapshot() is None

    def test_get_planner_history_empty(self):
        assert cp.get_planner_history() == []

    def test_get_planner_history_after_save(self):
        cp.save_planner_snapshot(self._output(), self._projections())
        history = cp.get_planner_history()
        assert len(history) == 1

    def test_history_newest_first(self):
        import time
        cp.save_planner_snapshot(self._output(10000.0), self._projections())
        time.sleep(0.01)
        cp.save_planner_snapshot(self._output(20000.0), self._projections())
        history = cp.get_planner_history()
        assert history[0]["portfolio_value"] == pytest.approx(20000.0)

    def test_history_excludes_projections(self):
        cp.save_planner_snapshot(self._output(), self._projections())
        history = cp.get_planner_history()
        assert "projections" not in history[0]

    def test_history_limit_respected(self):
        for v in [10000.0, 20000.0, 30000.0]:
            cp.save_planner_snapshot(self._output(v), self._projections())
        history = cp.get_planner_history(limit=2)
        assert len(history) <= 2


# ---------------------------------------------------------------------------
# Tests: snapshot_id
# ---------------------------------------------------------------------------

class TestSnapshotId:
    def test_format(self):
        sid = cp._snapshot_id(50000.0, "2026-01-01T00:00:00+00:00")
        assert sid.startswith("PLN-")
        assert len(sid) == 4 + 16

    def test_different_values_different_ids(self):
        s1 = cp._snapshot_id(50000.0, "2026-01-01T00:00:00+00:00")
        s2 = cp._snapshot_id(60000.0, "2026-01-01T00:00:00+00:00")
        assert s1 != s2

    def test_same_values_same_id(self):
        s1 = cp._snapshot_id(50000.0, "2026-01-01T00:00:00+00:00")
        s2 = cp._snapshot_id(50000.0, "2026-01-01T00:00:00+00:00")
        assert s1 == s2

    def test_uppercase_hex(self):
        sid = cp._snapshot_id(100.0, "2026-05-19T12:00:00+00:00")
        hex_part = sid[4:]
        assert hex_part == hex_part.upper()


# ---------------------------------------------------------------------------
# Tests: immutability and no trading calls
# ---------------------------------------------------------------------------

class TestNoTradingAndImmutability:
    def test_no_trading_calls_in_source(self):
        import inspect
        source = inspect.getsource(cp)
        forbidden = [
            "place_order", "execute_trade", "submit_order", "buy_shares",
            "sell_shares", "market_order", "limit_order",
        ]
        for kw in forbidden:
            assert kw not in source.lower(), f"Found trading keyword: {kw!r}"

    def test_no_update_or_delete_in_source(self):
        import inspect
        source = inspect.getsource(cp)
        lines  = [l.strip().upper() for l in source.splitlines()]
        for line in lines:
            if line.startswith("#"):
                continue
            if line.startswith("DELETE "):
                pytest.fail(f"Found DELETE in source: {line[:80]}")
            if line.startswith("UPDATE ") and "DEFAULT" not in line:
                pytest.fail(f"Found UPDATE in source: {line[:80]}")

    def test_no_tax_advice_in_guidance(self):
        result = cp.compute_contribution_guidance(50000.0, 500.0, 100000.0)
        assert "tax-free" not in result.lower() or "educational" in result.lower()

    def test_no_legal_advice_in_guidance(self):
        result = cp.compute_contribution_guidance(50000.0, 0.0, 0.0)
        legal_keywords = ["legal advice", "tax advice", "consult a lawyer"]
        for kw in legal_keywords:
            assert kw not in result.lower()

    def test_no_broker_calls_in_source(self):
        import inspect
        source = inspect.getsource(cp).lower()
        assert "twilio" not in source
        assert "send_alert" not in source
        assert "send_whatsapp" not in source


# ---------------------------------------------------------------------------
# Tests: API endpoints
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(monkeypatch, tmp_path):
    db_file = tmp_path / "api_test.db"
    monkeypatch.setenv("DB_PATH",    str(db_file))
    monkeypatch.setenv("API_SECRET", "")
    monkeypatch.setattr(database, "DB_PATH", str(db_file))

    import sqlite3

    def _conn():
        c = sqlite3.connect(str(db_file), timeout=5, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(database, "get_connection", _conn)

    # Create planner_snapshots table
    conn = sqlite3.connect(str(db_file))
    for ddl in cp._PLANNER_DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()

    importlib.reload(cp)

    import api
    importlib.reload(api)
    from flask import Flask
    flask_app = Flask(__name__)
    flask_app.register_blueprint(api.api_bp)
    flask_app.config["TESTING"] = True
    api._CACHE.clear()
    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


class TestApiPlannerSummary:
    def test_returns_200(self, client):
        resp = client.get("/api/v1/planner/summary")
        assert resp.status_code == 200

    def test_ok_envelope(self, client):
        data = client.get("/api/v1/planner/summary").get_json()
        assert data["ok"] is True

    def test_no_snapshot_returns_none(self, client):
        data = client.get("/api/v1/planner/summary").get_json()
        assert data["data"]["snapshot"] is None

    def test_with_snapshot(self, client, monkeypatch):
        snap = {
            "snapshot_id": "PLN-AAABBB1234567890",
            "created_at": "2026-01-01T00:00:00+00:00",
            "portfolio_value": 10000.0,
            "rebalance_urgency": "LOW",
            "current_allocation": {}, "target_allocation": {}, "drift": {},
            "priority_areas": [], "strategy_alignment_notes": [], "projections": {},
        }
        monkeypatch.setattr(cp, "get_latest_planner_snapshot", lambda: snap)
        import api; api._CACHE.clear()
        data = client.get("/api/v1/planner/summary").get_json()
        assert data["data"]["snapshot"] is not None

    def test_cached_flag(self, client):
        data = client.get("/api/v1/planner/summary").get_json()
        assert "cached" in data["meta"]


class TestApiPlannerProjections:
    def test_returns_200(self, client):
        resp = client.get("/api/v1/planner/projections")
        assert resp.status_code == 200

    def test_ok_envelope(self, client):
        data = client.get("/api/v1/planner/projections").get_json()
        assert data["ok"] is True

    def test_no_snapshot_returns_null_projections(self, client):
        data = client.get("/api/v1/planner/projections").get_json()
        assert data["data"]["projections"] is None

    def test_cached_flag(self, client):
        data = client.get("/api/v1/planner/projections").get_json()
        assert "cached" in data["meta"]


class TestApiPlannerRefresh:
    def test_no_auth_secret_allows_request(self, client, monkeypatch):
        mock_result = {
            "snapshot_id": "PLN-TEST1234567890AB",
            "portfolio_value": 0.0, "cash": 0.0, "regime": "NEUTRAL",
            "risk_score": 50.0, "rebalance_urgency": "NONE",
            "current_allocation": {}, "target_allocation": {}, "drift": {},
            "priority_areas": [], "cash_deployment_guidance": "",
            "contribution_guidance": "", "risk_reduction_guidance": "",
            "strategy_alignment_notes": [], "projections": {},
            "created_at": "2026-01-01T00:00:00+00:00", "monthly_contribution": 500.0,
        }
        monkeypatch.setattr(cp, "run_planner", lambda monthly_contribution=500.0: mock_result)
        resp = client.post("/api/v1/planner/refresh", json={})
        assert resp.status_code == 200

    def test_returns_planner_in_data(self, client, monkeypatch):
        mock_result = {
            "snapshot_id": "PLN-AAABBBCCDDEEFF11",
            "portfolio_value": 10000.0, "rebalance_urgency": "LOW",
            "projections": {}, "monthly_contribution": 500.0,
            "cash": 0.0, "regime": "NEUTRAL", "risk_score": 50.0,
            "current_allocation": {}, "target_allocation": {}, "drift": {},
            "priority_areas": [], "cash_deployment_guidance": "",
            "contribution_guidance": "", "risk_reduction_guidance": "",
            "strategy_alignment_notes": [], "created_at": "2026-01-01T00:00:00+00:00",
        }
        monkeypatch.setattr(cp, "run_planner", lambda monthly_contribution=500.0: mock_result)
        resp = client.post("/api/v1/planner/refresh", json={})
        data = resp.get_json()
        assert data["ok"] is True
        assert "planner" in data["data"]

    def test_auth_rejection(self, client, monkeypatch):
        monkeypatch.setenv("API_SECRET", "real-secret")
        import api; importlib.reload(api)
        flask_app = client.application
        resp = client.post(
            "/api/v1/planner/refresh",
            json={},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_custom_contribution_passed(self, client, monkeypatch):
        captured = {}

        def _mock_run(monthly_contribution=500.0):
            captured["mc"] = monthly_contribution
            return {
                "snapshot_id": "PLN-AAABBBCCDDEEFF22",
                "portfolio_value": 0.0, "cash": 0.0, "regime": "NEUTRAL",
                "risk_score": 50.0, "rebalance_urgency": "NONE",
                "current_allocation": {}, "target_allocation": {}, "drift": {},
                "priority_areas": [], "cash_deployment_guidance": "",
                "contribution_guidance": "", "risk_reduction_guidance": "",
                "strategy_alignment_notes": [], "projections": {},
                "created_at": "2026-01-01T00:00:00+00:00", "monthly_contribution": monthly_contribution,
            }

        monkeypatch.setattr(cp, "run_planner", _mock_run)
        client.post("/api/v1/planner/refresh", json={"monthly_contribution": 1000.0})
        assert captured.get("mc") == pytest.approx(1000.0)

    def test_busts_cache(self, client, monkeypatch):
        import api
        api._CACHE["planner:summary"]     = ({"snapshot": None}, 9999999999.0)
        api._CACHE["planner:projections"] = ({"projections": None}, 9999999999.0)

        mock_result = {
            "snapshot_id": "PLN-AAABBBCCDDEEFF33",
            "portfolio_value": 0.0, "cash": 0.0, "regime": "NEUTRAL",
            "risk_score": 50.0, "rebalance_urgency": "NONE",
            "current_allocation": {}, "target_allocation": {}, "drift": {},
            "priority_areas": [], "cash_deployment_guidance": "",
            "contribution_guidance": "", "risk_reduction_guidance": "",
            "strategy_alignment_notes": [], "projections": {},
            "created_at": "2026-01-01T00:00:00+00:00", "monthly_contribution": 500.0,
        }
        monkeypatch.setattr(cp, "run_planner", lambda monthly_contribution=500.0: mock_result)
        client.post("/api/v1/planner/refresh", json={})
        assert "planner:summary"     not in api._CACHE
        assert "planner:projections" not in api._CACHE
