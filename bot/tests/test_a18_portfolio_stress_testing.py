"""
Phase A18 — Portfolio scenario stress testing tests.

Coverage:
  - Pure functions: get_scenario_shock, stress_position, apply_scenario,
    compute_risk_level, compute_recommended_actions, compute_aggregate_report
  - DB functions: save_stress_run, get_stress_run, get_stress_history
  - Orchestration: run_stress_test (mocked portfolio)
  - API endpoints: GET /portfolio/stress, POST /portfolio/stress/run,
    GET /portfolio/stress/history
  - Immutability: no UPDATE/DELETE in source
  - Sparse data: empty portfolio, no regime, None positions
"""
import importlib
import json
import os
import sys

import pytest

# ---------------------------------------------------------------------------
# Path fixup
# ---------------------------------------------------------------------------

BOT_DIR = os.path.dirname(os.path.dirname(__file__))
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

import database
import portfolio_stress_testing as pst


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setattr(database, "DB_PATH", str(db_file))
    monkeypatch.setattr(database, "get_connection", lambda: __import__("sqlite3").connect(str(db_file), timeout=5, check_same_thread=False))

    import sqlite3
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    for ddl in pst._STRESS_DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()

    # Reload module so lazy imports re-bind to the patched DB
    importlib.reload(pst)

    # Patch the lazy-import inside the module to use the test DB connection
    def _patched_get_connection():
        import sqlite3
        c = sqlite3.connect(str(db_file), timeout=5, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(database, "get_connection", _patched_get_connection)
    yield


@pytest.fixture()
def sample_positions():
    return [
        {"ticker": "NVDA",   "market_value": 10000.0, "quantity": 10, "avg_cost": 800.0},
        {"ticker": "SPY",    "market_value":  5000.0, "quantity":  5, "avg_cost": 500.0},
        {"ticker": "VFV.TO", "market_value":  3000.0, "quantity": 15, "avg_cost": 100.0},
        {"ticker": "SHOP.TO","market_value":  2000.0, "quantity":  8, "avg_cost": 150.0},
        {"ticker": "AAPL",   "market_value":  4000.0, "quantity": 20, "avg_cost": 150.0},
    ]


@pytest.fixture()
def sample_portfolio(sample_positions):
    total = sum(p["market_value"] for p in sample_positions)
    return {
        "positions": sample_positions,
        "aggregates": {
            "cash": 1000.0,
            "total_portfolio_value": total + 1000.0,
        },
    }


# ---------------------------------------------------------------------------
# Tests: SCENARIO_TYPES and RISK_LEVELS constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_scenario_types_contains_all_11(self):
        expected = {
            "MARKET_PULLBACK_5", "MARKET_CORRECTION_10", "MARKET_CRASH_20",
            "TECH_SELL_OFF", "AI_SEMI_REVERSAL", "CRYPTO_RISK_OFF",
            "CANADA_UNDERPERFORMANCE", "USD_CAD_MOVE", "VOLATILITY_SPIKE",
            "ALPHA_FALSE_POSITIVE_CLUSTER", "CUSTOM",
        }
        assert set(pst.SCENARIO_TYPES) == expected

    def test_risk_levels_ordered(self):
        assert pst.RISK_LEVELS == ["LOW", "MODERATE", "HIGH", "SEVERE"]

    def test_scenario_types_is_list(self):
        assert isinstance(pst.SCENARIO_TYPES, list)


# ---------------------------------------------------------------------------
# Tests: get_scenario_shock
# ---------------------------------------------------------------------------

class TestGetScenarioShock:
    # Market-wide scenarios apply same shock to all tickers
    def test_market_pullback_5_nvda(self):
        assert pst.get_scenario_shock("NVDA", "MARKET_PULLBACK_5") == -5.0

    def test_market_pullback_5_spy(self):
        assert pst.get_scenario_shock("SPY", "MARKET_PULLBACK_5") == -5.0

    def test_market_correction_10_vfv(self):
        assert pst.get_scenario_shock("VFV.TO", "MARKET_CORRECTION_10") == -10.0

    def test_market_crash_20(self):
        assert pst.get_scenario_shock("AAPL", "MARKET_CRASH_20") == -20.0

    # TECH_SELL_OFF: AI_TECH theme -25%, others -8%
    def test_tech_sell_off_nvda(self):
        assert pst.get_scenario_shock("NVDA", "TECH_SELL_OFF") == -25.0

    def test_tech_sell_off_aapl(self):
        assert pst.get_scenario_shock("AAPL", "TECH_SELL_OFF") == -25.0

    def test_tech_sell_off_spy(self):  # ETF, not AI_TECH
        assert pst.get_scenario_shock("SPY", "TECH_SELL_OFF") == -8.0

    def test_tech_sell_off_vfv(self):  # broad market ETF
        assert pst.get_scenario_shock("VFV.TO", "TECH_SELL_OFF") == -8.0

    # AI_SEMI_REVERSAL: NVDA/AMD/TSM -35%, AI_TECH -20%, others -5%
    def test_ai_semi_reversal_nvda(self):
        assert pst.get_scenario_shock("NVDA", "AI_SEMI_REVERSAL") == -35.0

    def test_ai_semi_reversal_amd(self):
        assert pst.get_scenario_shock("AMD", "AI_SEMI_REVERSAL") == -35.0

    def test_ai_semi_reversal_tsm(self):
        assert pst.get_scenario_shock("TSM", "AI_SEMI_REVERSAL") == -35.0

    def test_ai_semi_reversal_msft(self):  # AI_TECH but not semi
        assert pst.get_scenario_shock("MSFT", "AI_SEMI_REVERSAL") == -20.0

    def test_ai_semi_reversal_spy(self):  # no theme match
        assert pst.get_scenario_shock("SPY", "AI_SEMI_REVERSAL") == -5.0

    # CRYPTO_RISK_OFF: speculative (individual stocks) -15%, ETFs -5%
    def test_crypto_risk_off_nvda(self):  # speculative
        assert pst.get_scenario_shock("NVDA", "CRYPTO_RISK_OFF") == -15.0

    def test_crypto_risk_off_spy(self):  # ETF
        assert pst.get_scenario_shock("SPY", "CRYPTO_RISK_OFF") == -5.0

    def test_crypto_risk_off_qqq(self):  # ETF
        assert pst.get_scenario_shock("QQQ", "CRYPTO_RISK_OFF") == -5.0

    # CANADA_UNDERPERFORMANCE: .TO -12%, USD 0%
    def test_canada_underperformance_vfv(self):
        assert pst.get_scenario_shock("VFV.TO", "CANADA_UNDERPERFORMANCE") == -12.0

    def test_canada_underperformance_spy(self):
        assert pst.get_scenario_shock("SPY", "CANADA_UNDERPERFORMANCE") == 0.0

    def test_canada_underperformance_nvda(self):
        assert pst.get_scenario_shock("NVDA", "CANADA_UNDERPERFORMANCE") == 0.0

    # USD_CAD_MOVE: CAD 0%, USD -8%
    def test_usd_cad_move_vfv(self):
        assert pst.get_scenario_shock("VFV.TO", "USD_CAD_MOVE") == 0.0

    def test_usd_cad_move_spy(self):
        assert pst.get_scenario_shock("SPY", "USD_CAD_MOVE") == -8.0

    def test_usd_cad_move_nvda(self):
        assert pst.get_scenario_shock("NVDA", "USD_CAD_MOVE") == -8.0

    # VOLATILITY_SPIKE: speculative -18%, ETFs -12%
    def test_volatility_spike_nvda(self):
        assert pst.get_scenario_shock("NVDA", "VOLATILITY_SPIKE") == -18.0

    def test_volatility_spike_spy(self):
        assert pst.get_scenario_shock("SPY", "VOLATILITY_SPIKE") == -12.0

    # ALPHA_FALSE_POSITIVE_CLUSTER: speculative -10%, ETFs -3%
    def test_alpha_fpc_nvda(self):
        assert pst.get_scenario_shock("NVDA", "ALPHA_FALSE_POSITIVE_CLUSTER") == -10.0

    def test_alpha_fpc_qqq(self):
        assert pst.get_scenario_shock("QQQ", "ALPHA_FALSE_POSITIVE_CLUSTER") == -3.0

    # CUSTOM: uses custom_overrides
    def test_custom_ticker_override(self):
        overrides = {"NVDA": -50.0, "_default": -10.0}
        assert pst.get_scenario_shock("NVDA", "CUSTOM", overrides) == -50.0

    def test_custom_default_fallback(self):
        overrides = {"NVDA": -50.0, "_default": -10.0}
        assert pst.get_scenario_shock("AAPL", "CUSTOM", overrides) == -10.0

    def test_custom_no_overrides_returns_zero(self):
        assert pst.get_scenario_shock("NVDA", "CUSTOM") == 0.0

    def test_custom_empty_overrides_returns_zero(self):
        assert pst.get_scenario_shock("NVDA", "CUSTOM", {}) == 0.0

    def test_custom_missing_ticker_no_default(self):
        overrides = {"AMD": -30.0}
        assert pst.get_scenario_shock("NVDA", "CUSTOM", overrides) == 0.0

    def test_unknown_scenario_returns_zero(self):
        assert pst.get_scenario_shock("NVDA", "NONEXISTENT_SCENARIO") == 0.0

    def test_ticker_case_insensitive(self):
        assert pst.get_scenario_shock("nvda", "MARKET_CRASH_20") == -20.0

    def test_shop_to_is_ai_tech(self):
        # SHOP.TO is in AI_TECH theme
        assert pst.get_scenario_shock("SHOP.TO", "TECH_SELL_OFF") == -25.0


# ---------------------------------------------------------------------------
# Tests: stress_position
# ---------------------------------------------------------------------------

class TestStressPosition:
    def test_basic_loss(self):
        pos = {"ticker": "NVDA", "market_value": 10000.0}
        result = pst.stress_position(pos, -25.0)
        assert result["ticker"] == "NVDA"
        assert result["market_value"] == 10000.0
        assert result["shock_pct"] == -25.0
        assert result["estimated_loss"] == pytest.approx(-2500.0)
        assert result["stressed_value"] == pytest.approx(7500.0)

    def test_zero_shock(self):
        pos = {"ticker": "VFV.TO", "market_value": 3000.0}
        result = pst.stress_position(pos, 0.0)
        assert result["estimated_loss"] == 0.0
        assert result["stressed_value"] == 3000.0

    def test_zero_market_value(self):
        pos = {"ticker": "AAPL", "market_value": 0.0}
        result = pst.stress_position(pos, -20.0)
        assert result["estimated_loss"] == 0.0
        assert result["stressed_value"] == 0.0

    def test_rounding(self):
        pos = {"ticker": "AMD", "market_value": 1000.33}
        result = pst.stress_position(pos, -10.0)
        assert result["estimated_loss"] == round(1000.33 * -10 / 100, 2)
        assert result["stressed_value"] == round(1000.33 + 1000.33 * -10 / 100, 2)

    def test_missing_market_value_defaults_zero(self):
        pos = {"ticker": "SPY"}
        result = pst.stress_position(pos, -5.0)
        assert result["market_value"] == 0.0
        assert result["estimated_loss"] == 0.0

    def test_all_keys_present(self):
        pos = {"ticker": "SPY", "market_value": 5000.0}
        result = pst.stress_position(pos, -10.0)
        assert set(result.keys()) == {"ticker", "market_value", "shock_pct", "estimated_loss", "stressed_value"}


# ---------------------------------------------------------------------------
# Tests: compute_risk_level
# ---------------------------------------------------------------------------

class TestComputeRiskLevel:
    def test_low_zero(self):
        assert pst.compute_risk_level(0.0) == "LOW"

    def test_low_near_threshold(self):
        assert pst.compute_risk_level(-4.9) == "LOW"

    def test_moderate_at_5(self):
        assert pst.compute_risk_level(-5.0) == "MODERATE"

    def test_moderate_near_10(self):
        assert pst.compute_risk_level(-9.9) == "MODERATE"

    def test_high_at_10(self):
        assert pst.compute_risk_level(-10.0) == "HIGH"

    def test_high_near_20(self):
        assert pst.compute_risk_level(-19.9) == "HIGH"

    def test_severe_at_20(self):
        assert pst.compute_risk_level(-20.0) == "SEVERE"

    def test_severe_beyond(self):
        assert pst.compute_risk_level(-50.0) == "SEVERE"

    def test_positive_loss_pct_treated_as_abs(self):
        # positive value should still map correctly
        assert pst.compute_risk_level(5.0) == "MODERATE"


# ---------------------------------------------------------------------------
# Tests: compute_recommended_actions
# ---------------------------------------------------------------------------

class TestComputeRecommendedActions:
    def test_severe_returns_actions(self):
        actions = pst.compute_recommended_actions("MARKET_CRASH_20", -20.0, "SEVERE")
        assert len(actions) >= 1
        assert any("equity" in a.lower() or "stop-loss" in a.lower() for a in actions)

    def test_high_returns_actions(self):
        actions = pst.compute_recommended_actions("MARKET_CORRECTION_10", -12.0, "HIGH")
        assert len(actions) >= 1

    def test_moderate_returns_actions(self):
        actions = pst.compute_recommended_actions("MARKET_PULLBACK_5", -7.0, "MODERATE")
        assert len(actions) >= 1

    def test_low_returns_list(self):
        actions = pst.compute_recommended_actions("MARKET_PULLBACK_5", -2.0, "LOW")
        assert isinstance(actions, list)

    def test_tech_sell_off_contains_tech_action(self):
        actions = pst.compute_recommended_actions("TECH_SELL_OFF", -20.0, "SEVERE")
        combined = " ".join(actions).lower()
        assert "technology" in combined or "tech" in combined

    def test_ai_semi_reversal_has_semi_action(self):
        actions = pst.compute_recommended_actions("AI_SEMI_REVERSAL", -18.0, "HIGH")
        combined = " ".join(actions).lower()
        assert "ai" in combined or "semi" in combined

    def test_volatility_spike_warns_speculative(self):
        actions = pst.compute_recommended_actions("VOLATILITY_SPIKE", -15.0, "HIGH")
        combined = " ".join(actions).lower()
        assert "speculative" in combined or "volatility" in combined

    def test_canada_underperformance_action(self):
        actions = pst.compute_recommended_actions("CANADA_UNDERPERFORMANCE", -8.0, "MODERATE")
        combined = " ".join(actions).lower()
        assert "cad" in combined or "canada" in combined or "usd" in combined

    def test_usd_cad_move_action(self):
        actions = pst.compute_recommended_actions("USD_CAD_MOVE", -6.0, "MODERATE")
        combined = " ".join(actions).lower()
        assert "currency" in combined or "cad" in combined or "usd" in combined

    def test_crypto_risk_off_action(self):
        actions = pst.compute_recommended_actions("CRYPTO_RISK_OFF", -10.0, "HIGH")
        combined = " ".join(actions).lower()
        assert "speculative" in combined or "risk" in combined

    def test_returns_list(self):
        actions = pst.compute_recommended_actions("MARKET_CRASH_20", -25.0, "SEVERE")
        assert isinstance(actions, list)


# ---------------------------------------------------------------------------
# Tests: apply_scenario
# ---------------------------------------------------------------------------

class TestApplyScenario:
    def test_market_pullback_5_all_lose_5pct(self, sample_positions):
        total = sum(p["market_value"] for p in sample_positions)
        result = pst.apply_scenario("MARKET_PULLBACK_5", sample_positions, 0.0, total)
        assert result["scenario_type"] == "MARKET_PULLBACK_5"
        assert result["estimated_loss_pct"] == pytest.approx(-5.0, abs=0.01)

    def test_market_crash_20_all_lose_20pct(self, sample_positions):
        total = sum(p["market_value"] for p in sample_positions)
        result = pst.apply_scenario("MARKET_CRASH_20", sample_positions, 0.0, total)
        assert result["estimated_loss_pct"] == pytest.approx(-20.0, abs=0.01)

    def test_position_results_count(self, sample_positions):
        total = sum(p["market_value"] for p in sample_positions)
        result = pst.apply_scenario("MARKET_PULLBACK_5", sample_positions, 0.0, total)
        assert len(result["position_results"]) == len(sample_positions)

    def test_result_has_required_keys(self, sample_positions):
        total = sum(p["market_value"] for p in sample_positions)
        result = pst.apply_scenario("MARKET_PULLBACK_5", sample_positions, 0.0, total)
        required = {"scenario_type", "estimated_loss_amount", "estimated_loss_pct",
                    "risk_level", "position_results", "recommended_actions"}
        assert required.issubset(set(result.keys()))

    def test_risk_level_in_valid_values(self, sample_positions):
        total = sum(p["market_value"] for p in sample_positions)
        result = pst.apply_scenario("MARKET_CRASH_20", sample_positions, 0.0, total)
        assert result["risk_level"] in pst.RISK_LEVELS

    def test_empty_positions(self):
        result = pst.apply_scenario("MARKET_CRASH_20", [], 1000.0, 1000.0)
        assert result["estimated_loss_pct"] == 0.0
        assert result["estimated_loss_amount"] == 0.0
        assert result["position_results"] == []

    def test_zero_total_value(self, sample_positions):
        result = pst.apply_scenario("MARKET_CRASH_20", sample_positions, 0.0, 0.0)
        assert result["estimated_loss_pct"] == 0.0

    def test_tech_sell_off_nvda_harder_than_spy(self, sample_positions):
        total = sum(p["market_value"] for p in sample_positions)
        result = pst.apply_scenario("TECH_SELL_OFF", sample_positions, 0.0, total)
        nvda_r = next(r for r in result["position_results"] if r["ticker"] == "NVDA")
        spy_r  = next(r for r in result["position_results"] if r["ticker"] == "SPY")
        assert nvda_r["shock_pct"] == -25.0
        assert spy_r["shock_pct"]  == -8.0

    def test_canada_underperformance_cad_only(self, sample_positions):
        total = sum(p["market_value"] for p in sample_positions)
        result = pst.apply_scenario("CANADA_UNDERPERFORMANCE", sample_positions, 0.0, total)
        for pr in result["position_results"]:
            if pr["ticker"].endswith(".TO"):
                assert pr["shock_pct"] == -12.0
            else:
                assert pr["shock_pct"] == 0.0

    def test_custom_scenario_uses_overrides(self, sample_positions):
        total = sum(p["market_value"] for p in sample_positions)
        overrides = {"NVDA": -50.0, "_default": -5.0}
        result = pst.apply_scenario("CUSTOM", sample_positions, 0.0, total, custom_overrides=overrides)
        nvda_r = next(r for r in result["position_results"] if r["ticker"] == "NVDA")
        spy_r  = next(r for r in result["position_results"] if r["ticker"] == "SPY")
        assert nvda_r["shock_pct"] == -50.0
        assert spy_r["shock_pct"]  == -5.0

    def test_estimated_loss_amount_sums_positions(self, sample_positions):
        total = sum(p["market_value"] for p in sample_positions)
        result = pst.apply_scenario("MARKET_PULLBACK_5", sample_positions, 0.0, total)
        expected_loss = sum(p["market_value"] * -5.0 / 100.0 for p in sample_positions)
        assert result["estimated_loss_amount"] == pytest.approx(expected_loss, abs=0.02)

    def test_recommended_actions_is_list(self, sample_positions):
        total = sum(p["market_value"] for p in sample_positions)
        result = pst.apply_scenario("MARKET_CRASH_20", sample_positions, 0.0, total)
        assert isinstance(result["recommended_actions"], list)


# ---------------------------------------------------------------------------
# Tests: compute_aggregate_report
# ---------------------------------------------------------------------------

class TestComputeAggregateReport:
    def _make_scenario(self, st, loss_pct, risk_level="MODERATE"):
        return {
            "scenario_type": st,
            "estimated_loss_pct": loss_pct,
            "estimated_loss_amount": loss_pct * 100,
            "risk_level": risk_level,
            "position_results": [],
            "recommended_actions": [],
        }

    def test_empty_returns_zero_scenario_count(self):
        report = pst.compute_aggregate_report([], 0.0, 0.0)
        assert report["scenario_count"] == 0
        assert report["worst_scenario"] is None
        assert report["warnings"] == []

    def test_single_scenario(self):
        scenarios = [self._make_scenario("MARKET_CRASH_20", -20.0, "SEVERE")]
        report = pst.compute_aggregate_report(scenarios, 1000.0, 20000.0)
        assert report["scenario_count"] == 1
        assert report["worst_scenario"] == "MARKET_CRASH_20"
        assert report["worst_loss_pct"] == -20.0

    def test_worst_scenario_is_most_negative(self):
        scenarios = [
            self._make_scenario("MARKET_PULLBACK_5",   -5.0,  "LOW"),
            self._make_scenario("MARKET_CRASH_20",     -20.0, "SEVERE"),
            self._make_scenario("TECH_SELL_OFF",       -12.0, "HIGH"),
        ]
        report = pst.compute_aggregate_report(scenarios, 1000.0, 50000.0)
        assert report["worst_scenario"] == "MARKET_CRASH_20"
        assert report["worst_loss_pct"] == -20.0

    def test_avg_loss_pct_computed(self):
        scenarios = [
            self._make_scenario("A", -5.0, "LOW"),
            self._make_scenario("B", -15.0, "HIGH"),
        ]
        report = pst.compute_aggregate_report(scenarios, 0.0, 10000.0)
        assert report["avg_loss_pct"] == pytest.approx(-10.0)

    def test_severe_warning_generated(self):
        scenarios = [self._make_scenario("MARKET_CRASH_20", -25.0, "SEVERE")]
        report = pst.compute_aggregate_report(scenarios, 0.0, 10000.0)
        assert len(report["warnings"]) >= 1
        combined = " ".join(report["warnings"]).upper()
        assert "SEVERE" in combined

    def test_multiple_severe_warning(self):
        scenarios = [
            self._make_scenario("A", -22.0, "SEVERE"),
            self._make_scenario("B", -25.0, "SEVERE"),
        ]
        report = pst.compute_aggregate_report(scenarios, 0.0, 10000.0)
        # At least 1 warning for the worst case and the multiple-severe case
        assert len(report["warnings"]) >= 1

    def test_scenarios_list_preserved(self):
        scenarios = [
            self._make_scenario("A", -5.0, "LOW"),
            self._make_scenario("B", -10.0, "MODERATE"),
        ]
        report = pst.compute_aggregate_report(scenarios, 0.0, 10000.0)
        assert len(report["scenarios"]) == 2

    def test_required_keys(self):
        scenarios = [self._make_scenario("MARKET_PULLBACK_5", -5.0, "LOW")]
        report = pst.compute_aggregate_report(scenarios, 0.0, 5000.0)
        required = {"scenario_count", "worst_scenario", "worst_loss_pct", "avg_loss_pct",
                    "scenarios", "warnings"}
        assert required.issubset(set(report.keys()))

    def test_high_count_warning(self):
        # 3+ HIGH/SEVERE should trigger a warning
        scenarios = [
            self._make_scenario("A", -15.0, "HIGH"),
            self._make_scenario("B", -18.0, "HIGH"),
            self._make_scenario("C", -22.0, "SEVERE"),
        ]
        report = pst.compute_aggregate_report(scenarios, 0.0, 10000.0)
        assert len(report["warnings"]) >= 1


# ---------------------------------------------------------------------------
# Tests: run_id generation
# ---------------------------------------------------------------------------

class TestRunIdGeneration:
    def test_format(self):
        rid = pst._run_id_from_params(50000.0, "2026-01-01T00:00:00+00:00")
        assert rid.startswith("STR-")
        assert len(rid) == 4 + 16

    def test_different_values_produce_different_ids(self):
        r1 = pst._run_id_from_params(50000.0, "2026-01-01T00:00:00+00:00")
        r2 = pst._run_id_from_params(60000.0, "2026-01-01T00:00:00+00:00")
        assert r1 != r2

    def test_same_values_same_id(self):
        r1 = pst._run_id_from_params(50000.0, "2026-01-01T00:00:00+00:00")
        r2 = pst._run_id_from_params(50000.0, "2026-01-01T00:00:00+00:00")
        assert r1 == r2

    def test_uppercase_hex(self):
        rid = pst._run_id_from_params(100.0, "2026-05-19T12:00:00+00:00")
        hex_part = rid[4:]
        assert hex_part == hex_part.upper()


# ---------------------------------------------------------------------------
# Tests: DB functions
# ---------------------------------------------------------------------------

class TestSaveAndGetStressRun:
    def _make_report(self, portfolio_value=50000.0):
        return {
            "portfolio_value":  portfolio_value,
            "cash":             1000.0,
            "position_count":   5,
            "scenario_count":   10,
            "worst_scenario":   "MARKET_CRASH_20",
            "worst_loss_pct":   -20.0,
            "avg_loss_pct":     -10.0,
            "warnings":         ["Some warning"],
        }

    def _make_scenario_results(self):
        return [
            {
                "scenario_type":         "MARKET_CRASH_20",
                "estimated_loss_pct":    -20.0,
                "estimated_loss_amount": -10000.0,
                "risk_level":            "SEVERE",
                "position_results":      [{"ticker": "NVDA", "market_value": 10000.0, "shock_pct": -20.0,
                                           "estimated_loss": -2000.0, "stressed_value": 8000.0}],
                "recommended_actions":   ["Reduce exposure"],
            },
        ]

    def test_save_and_retrieve(self):
        report    = self._make_report()
        scenarios = self._make_scenario_results()
        saved     = pst.save_stress_run(report, scenarios)
        assert saved["run_id"].startswith("STR-")

        run = pst.get_stress_run(saved["run_id"])
        assert run is not None
        assert run["run_id"] == saved["run_id"]
        assert run["portfolio_value"] == 50000.0
        assert run["worst_scenario"]  == "MARKET_CRASH_20"

    def test_scenario_events_persisted(self):
        report    = self._make_report()
        scenarios = self._make_scenario_results()
        saved     = pst.save_stress_run(report, scenarios)
        run       = pst.get_stress_run(saved["run_id"])
        assert len(run["scenario_events"]) == 1
        evt = run["scenario_events"][0]
        assert evt["scenario_type"] == "MARKET_CRASH_20"
        assert evt["risk_level"]    == "SEVERE"

    def test_position_results_json_deserialized(self):
        report    = self._make_report()
        scenarios = self._make_scenario_results()
        saved     = pst.save_stress_run(report, scenarios)
        run       = pst.get_stress_run(saved["run_id"])
        evt = run["scenario_events"][0]
        assert isinstance(evt["position_results"], list)
        assert evt["position_results"][0]["ticker"] == "NVDA"

    def test_recommended_actions_deserialized(self):
        report    = self._make_report()
        scenarios = self._make_scenario_results()
        saved     = pst.save_stress_run(report, scenarios)
        run       = pst.get_stress_run(saved["run_id"])
        evt = run["scenario_events"][0]
        assert isinstance(evt["recommended_actions"], list)

    def test_warnings_deserialized(self):
        report    = self._make_report()
        saved     = pst.save_stress_run(report, self._make_scenario_results())
        run       = pst.get_stress_run(saved["run_id"])
        assert isinstance(run["warnings"], list)
        assert "Some warning" in run["warnings"]

    def test_get_unknown_run_returns_none(self):
        assert pst.get_stress_run("STR-DOESNOTEXIST1234") is None

    def test_save_produces_unique_run_ids(self):
        import time
        report    = self._make_report(50000.0)
        scenarios = self._make_scenario_results()
        s1 = pst.save_stress_run(report, scenarios)
        time.sleep(0.01)
        s2 = pst.save_stress_run(report, scenarios)
        # Different timestamps → different run_ids
        assert s1["run_id"] != s2["run_id"]

    def test_save_multiple_scenarios(self):
        report = self._make_report()
        scenarios = [
            {"scenario_type": "MARKET_PULLBACK_5",  "estimated_loss_pct": -5.0,
             "estimated_loss_amount": -2500.0, "risk_level": "LOW",
             "position_results": [], "recommended_actions": []},
            {"scenario_type": "MARKET_CRASH_20",    "estimated_loss_pct": -20.0,
             "estimated_loss_amount": -10000.0, "risk_level": "SEVERE",
             "position_results": [], "recommended_actions": []},
        ]
        saved = pst.save_stress_run(report, scenarios)
        run   = pst.get_stress_run(saved["run_id"])
        assert len(run["scenario_events"]) == 2


class TestGetStressHistory:
    def _save_run(self, portfolio_value):
        report = {
            "portfolio_value": portfolio_value,
            "cash": 0.0,
            "position_count": 3,
            "scenario_count": 2,
            "worst_scenario": "MARKET_CRASH_20",
            "worst_loss_pct": -20.0,
            "avg_loss_pct": -10.0,
            "warnings": [],
        }
        scenarios = [
            {"scenario_type": "X", "estimated_loss_pct": -10.0,
             "estimated_loss_amount": -1000.0, "risk_level": "HIGH",
             "position_results": [], "recommended_actions": []},
        ]
        return pst.save_stress_run(report, scenarios)

    def test_returns_list(self):
        result = pst.get_stress_history()
        assert isinstance(result, list)

    def test_empty_db_returns_empty(self):
        assert pst.get_stress_history() == []

    def test_recent_run_appears(self):
        saved  = self._save_run(40000.0)
        history = pst.get_stress_history()
        assert len(history) == 1
        assert history[0]["run_id"] == saved["run_id"]

    def test_limit_respected(self):
        # Save 3 runs with different values
        for v in [10000.0, 20000.0, 30000.0]:
            self._save_run(v)
        history = pst.get_stress_history(limit=2)
        assert len(history) <= 2

    def test_newest_first(self):
        import time
        s1 = self._save_run(11111.11)
        time.sleep(0.01)
        s2 = self._save_run(22222.22)
        history = pst.get_stress_history(limit=10)
        run_ids = [r["run_id"] for r in history]
        # s2 was saved later, should appear first
        assert run_ids.index(s2["run_id"]) < run_ids.index(s1["run_id"])

    def test_history_excludes_events(self):
        self._save_run(50000.0)
        history = pst.get_stress_history()
        assert "scenario_events" not in history[0]

    def test_warnings_deserialized(self):
        self._save_run(50000.0)
        history = pst.get_stress_history()
        assert isinstance(history[0]["warnings"], list)

    def test_summary_deserialized(self):
        self._save_run(50000.0)
        history = pst.get_stress_history()
        assert isinstance(history[0]["summary"], dict)


# ---------------------------------------------------------------------------
# Tests: run_stress_test
# ---------------------------------------------------------------------------

class TestRunStressTest:
    def _mock_portfolio(self):
        return {
            "positions": [
                {"ticker": "NVDA",    "market_value": 10000.0, "quantity": 10, "avg_cost": 800.0},
                {"ticker": "SPY",     "market_value":  5000.0, "quantity":  5, "avg_cost": 500.0},
                {"ticker": "VFV.TO",  "market_value":  3000.0, "quantity": 15, "avg_cost": 100.0},
            ],
            "aggregates": {"cash": 500.0, "total_portfolio_value": 18500.0},
        }

    def test_returns_dict(self, monkeypatch):
        portfolio = self._mock_portfolio()
        result = pst.run_stress_test(portfolio=portfolio)
        assert isinstance(result, dict)

    def test_has_run_id(self, monkeypatch):
        result = pst.run_stress_test(portfolio=self._mock_portfolio())
        assert "run_id" in result
        assert result["run_id"].startswith("STR-")

    def test_has_created_at(self, monkeypatch):
        result = pst.run_stress_test(portfolio=self._mock_portfolio())
        assert "created_at" in result

    def test_scenario_count_is_10(self, monkeypatch):
        # 10 built-in scenarios (excluding CUSTOM)
        result = pst.run_stress_test(portfolio=self._mock_portfolio())
        assert result["scenario_count"] == 10

    def test_scenarios_list_has_10_entries(self, monkeypatch):
        result = pst.run_stress_test(portfolio=self._mock_portfolio())
        assert len(result["scenarios"]) == 10

    def test_worst_scenario_is_populated(self, monkeypatch):
        result = pst.run_stress_test(portfolio=self._mock_portfolio())
        assert result["worst_scenario"] is not None
        assert result["worst_scenario"] in pst.SCENARIO_TYPES

    def test_portfolio_value_in_result(self, monkeypatch):
        result = pst.run_stress_test(portfolio=self._mock_portfolio())
        assert result["portfolio_value"] == pytest.approx(18500.0)

    def test_position_count_in_result(self, monkeypatch):
        result = pst.run_stress_test(portfolio=self._mock_portfolio())
        assert result["position_count"] == 3

    def test_custom_scenarios_added(self, monkeypatch):
        overrides = [{"NVDA": -50.0, "_default": -10.0, "_label": "Bear Case"}]
        result = pst.run_stress_test(portfolio=self._mock_portfolio(), custom_scenarios=overrides)
        assert result["scenario_count"] == 11  # 10 standard + 1 custom

    def test_empty_portfolio(self, monkeypatch):
        portfolio = {"positions": [], "aggregates": {"cash": 1000.0, "total_portfolio_value": 1000.0}}
        result = pst.run_stress_test(portfolio=portfolio)
        assert result["position_count"] == 0
        assert result["worst_loss_pct"] == 0.0

    def test_regime_context_included(self, monkeypatch):
        regime = {"overall_regime": "RISK_OFF", "regime_score": 30.0, "available": True}
        result = pst.run_stress_test(portfolio=self._mock_portfolio(), regime_context=regime)
        assert "regime_context" in result
        assert result["regime_context"]["overall_regime"] == "RISK_OFF"

    def test_run_persisted_to_db(self, monkeypatch):
        result = pst.run_stress_test(portfolio=self._mock_portfolio())
        saved  = pst.get_stress_run(result["run_id"])
        assert saved is not None
        assert saved["run_id"] == result["run_id"]

    def test_stress_history_updated(self, monkeypatch):
        pst.run_stress_test(portfolio=self._mock_portfolio())
        history = pst.get_stress_history()
        assert len(history) == 1

    def test_portfolio_fetched_from_canonical_if_none(self, monkeypatch):
        mock_portfolio = self._mock_portfolio()

        def _mock_get_canonical():
            return mock_portfolio

        monkeypatch.setattr(
            "portfolio_reconciliation.get_canonical_portfolio",
            _mock_get_canonical,
            raising=False,
        )
        import portfolio_reconciliation
        monkeypatch.setattr(portfolio_reconciliation, "get_canonical_portfolio", _mock_get_canonical)

        result = pst.run_stress_test(portfolio=None)
        assert result["position_count"] == 3


# ---------------------------------------------------------------------------
# Tests: Immutability (no UPDATE/DELETE in source)
# ---------------------------------------------------------------------------

class TestImmutability:
    def test_no_update_in_source(self):
        import inspect
        source = inspect.getsource(pst)
        lines  = [l.strip().upper() for l in source.splitlines()]
        for line in lines:
            if line.startswith("#"):
                continue
            if line.startswith("UPDATE ") or " UPDATE " in line:
                pytest.fail(f"Found UPDATE statement in source: {line}")

    def test_no_delete_in_source(self):
        import inspect
        source = inspect.getsource(pst)
        lines  = [l.strip().upper() for l in source.splitlines()]
        for line in lines:
            if line.startswith("#"):
                continue
            if line.startswith("DELETE ") or " DELETE " in line:
                pytest.fail(f"Found DELETE statement in source: {line}")


# ---------------------------------------------------------------------------
# Tests: API endpoints
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(monkeypatch, tmp_path):
    db_file = tmp_path / "api_test.db"
    monkeypatch.setenv("DB_PATH",     str(db_file))
    monkeypatch.setenv("API_SECRET",  "")
    monkeypatch.setattr(database, "DB_PATH",        str(db_file))
    monkeypatch.setattr(database, "get_connection",
                        lambda: __import__("sqlite3").connect(str(db_file), timeout=5, check_same_thread=False))

    import sqlite3
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    for ddl in pst._STRESS_DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()

    importlib.reload(pst)

    def _patched_conn():
        import sqlite3
        c = sqlite3.connect(str(db_file), timeout=5, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(database, "get_connection", _patched_conn)

    import api
    importlib.reload(api)
    from flask import Flask
    flask_app = Flask(__name__)
    flask_app.register_blueprint(api.api_bp)
    flask_app.config["TESTING"] = True

    # Bust any cached responses between tests
    api._CACHE.clear()

    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


class TestApiPortfolioStressLatest:
    def test_returns_200(self, client):
        resp = client.get("/api/v1/portfolio/stress")
        assert resp.status_code == 200

    def test_ok_envelope(self, client):
        data = resp = client.get("/api/v1/portfolio/stress").get_json()
        assert data["ok"] is True

    def test_empty_db_run_is_none(self, client):
        data = client.get("/api/v1/portfolio/stress").get_json()
        assert data["data"]["run"] is None

    def test_with_saved_run(self, client, monkeypatch):
        portfolio = {
            "positions": [{"ticker": "NVDA", "market_value": 10000.0, "quantity": 5, "avg_cost": 800.0}],
            "aggregates": {"cash": 0.0, "total_portfolio_value": 10000.0},
        }
        pst.run_stress_test(portfolio=portfolio)
        import api
        api._CACHE.clear()
        data = client.get("/api/v1/portfolio/stress").get_json()
        assert data["data"]["run"] is not None
        assert data["data"]["run"]["run_id"].startswith("STR-")

    def test_cached_flag_present(self, client):
        data = client.get("/api/v1/portfolio/stress").get_json()
        assert "cached" in data["meta"]


class TestApiPortfolioStressRun:
    def _mock_portfolio(self):
        return {
            "positions": [
                {"ticker": "NVDA",   "market_value": 10000.0, "quantity": 10, "avg_cost": 800.0},
                {"ticker": "SPY",    "market_value":  5000.0, "quantity":  5, "avg_cost": 500.0},
            ],
            "aggregates": {"cash": 500.0, "total_portfolio_value": 15500.0},
        }

    def test_no_auth_secret_allows_request(self, client, monkeypatch):
        # API_SECRET="" → fails-open
        portfolio = self._mock_portfolio()
        monkeypatch.setattr(pst, "run_stress_test", lambda **kw: {
            "run_id": "STR-TEST", "created_at": "now", "scenario_count": 10,
            "worst_scenario": "MARKET_CRASH_20", "worst_loss_pct": -20.0,
            "avg_loss_pct": -10.0, "scenarios": [], "warnings": [],
            "portfolio_value": 15500.0, "cash": 500.0, "position_count": 2,
        })
        resp = client.post("/api/v1/portfolio/stress/run", json={})
        assert resp.status_code == 200

    def test_auth_rejection(self, client, monkeypatch):
        monkeypatch.setenv("API_SECRET", "real-secret")
        import api
        importlib.reload(api)
        resp = client.post(
            "/api/v1/portfolio/stress/run",
            json={},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_returns_report(self, client, monkeypatch):
        portfolio = self._mock_portfolio()
        monkeypatch.setattr(pst, "run_stress_test", lambda **kw: {
            "run_id": "STR-AAABBBCC12345678", "created_at": "2026-01-01T00:00:00+00:00",
            "scenario_count": 10, "worst_scenario": "MARKET_CRASH_20",
            "worst_loss_pct": -20.0, "avg_loss_pct": -10.0,
            "scenarios": [], "warnings": [],
            "portfolio_value": 15500.0, "cash": 500.0, "position_count": 2,
        })
        resp = client.post("/api/v1/portfolio/stress/run", json={})
        data = resp.get_json()
        assert data["ok"] is True
        assert "report" in data["data"]
        assert data["data"]["report"]["run_id"].startswith("STR-")

    def test_busts_cache(self, client, monkeypatch):
        import api
        api._CACHE["stress:latest"] = ({"run": None}, 9999999999.0)
        monkeypatch.setattr(pst, "run_stress_test", lambda **kw: {
            "run_id": "STR-NEWRUN12345678", "created_at": "2026-01-01T00:00:00+00:00",
            "scenario_count": 10, "worst_scenario": "MARKET_CRASH_20",
            "worst_loss_pct": -20.0, "avg_loss_pct": -10.0,
            "scenarios": [], "warnings": [],
            "portfolio_value": 15500.0, "cash": 500.0, "position_count": 2,
        })
        client.post("/api/v1/portfolio/stress/run", json={})
        assert "stress:latest" not in api._CACHE


class TestApiPortfolioStressHistory:
    def test_returns_200(self, client):
        resp = client.get("/api/v1/portfolio/stress/history")
        assert resp.status_code == 200

    def test_ok_envelope(self, client):
        data = client.get("/api/v1/portfolio/stress/history").get_json()
        assert data["ok"] is True

    def test_empty_db(self, client):
        data = client.get("/api/v1/portfolio/stress/history").get_json()
        assert data["data"]["runs"] == []
        assert data["data"]["total"] == 0

    def test_limit_param(self, client, monkeypatch):
        monkeypatch.setattr(pst, "get_stress_history",
                            lambda limit=20: [{"run_id": f"STR-{i}"} for i in range(limit)])
        resp = client.get("/api/v1/portfolio/stress/history?limit=3")
        data = resp.get_json()
        assert data["data"]["total"] == 3

    def test_limit_capped_at_100(self, client, monkeypatch):
        captured = {}
        original = pst.get_stress_history
        def _spy(limit=20):
            captured["limit"] = limit
            return []
        monkeypatch.setattr(pst, "get_stress_history", _spy)
        client.get("/api/v1/portfolio/stress/history?limit=999")
        assert captured.get("limit", 0) <= 100

    def test_with_runs_present(self, client, monkeypatch):
        portfolio = {
            "positions": [{"ticker": "SPY", "market_value": 5000.0, "quantity": 5, "avg_cost": 500.0}],
            "aggregates": {"cash": 0.0, "total_portfolio_value": 5000.0},
        }
        pst.run_stress_test(portfolio=portfolio)
        import api
        api._CACHE.clear()
        data = client.get("/api/v1/portfolio/stress/history").get_json()
        assert data["data"]["total"] == 1
        assert data["data"]["runs"][0]["run_id"].startswith("STR-")

    def test_cached_flag(self, client):
        data = client.get("/api/v1/portfolio/stress/history").get_json()
        assert "cached" in data["meta"]
