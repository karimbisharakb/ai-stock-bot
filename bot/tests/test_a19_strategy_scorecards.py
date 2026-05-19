"""
Phase A19 — Personal strategy scorecards tests.

Coverage:
  - classify_ticker / classify_setup_type / classify_candidate (pure)
  - compute_thesis_completeness (pure)
  - compute_checklist_discipline_score (pure)
  - compute_validation_quality (pure)
  - compute_risk_adjusted_score (pure)
  - compute_confidence_score (pure)
  - compute_stress_sensitivity (pure)
  - compute_scorecard (pure)
  - compute_behavior_metrics (pure)
  - generate_recommendations (pure)
  - compute_all_scorecards / get_scorecard / get_scorecards_summary (DB)
  - API endpoints: GET /strategies/scorecards, /strategies/summary,
    /strategies/<strategy>
  - Sparse data (empty tables)
  - No trading calls in source
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
import strategy_scorecards as sc


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

    # Create the tables that strategy_scorecards reads
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS alpha_shadow_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL, scan_time TEXT NOT NULL,
            alpha_score REAL, alpha_tier TEXT, setup_type TEXT,
            predator_tier TEXT, filter_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS alpha_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL, scan_time TEXT NOT NULL,
            alpha_score REAL, alpha_tier TEXT, setup_type TEXT, source TEXT,
            return_5d REAL, max_gain REAL, max_drawdown REAL,
            status TEXT NOT NULL DEFAULT 'PENDING'
        );
        CREATE TABLE IF NOT EXISTS alpha_validation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outcome_id INTEGER NOT NULL, ticker TEXT NOT NULL,
            scan_time TEXT, setup_type TEXT, alpha_tier TEXT,
            behavior_class TEXT NOT NULL, validation_score REAL NOT NULL,
            confidence TEXT NOT NULL, computed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS position_theses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL UNIQUE, thesis_title TEXT DEFAULT '',
            thesis_text TEXT DEFAULT '', setup_type TEXT DEFAULT '',
            conviction_level TEXT DEFAULT 'MEDIUM', time_horizon TEXT DEFAULT 'MEDIUM',
            entry_reason TEXT DEFAULT '', expected_catalysts TEXT DEFAULT '',
            risk_factors TEXT DEFAULT '', invalidation_level REAL,
            target_level REAL, exit_plan TEXT DEFAULT '',
            status TEXT DEFAULT 'ACTIVE',
            next_review_at TEXT NOT NULL DEFAULT '2030-01-01',
            created_at TEXT NOT NULL DEFAULT '2026-01-01',
            updated_at TEXT NOT NULL DEFAULT '2026-01-01'
        );
        CREATE TABLE IF NOT EXISTS decision_checklists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checklist_id TEXT NOT NULL UNIQUE, ticker TEXT NOT NULL,
            decision_type TEXT NOT NULL, checklist_status TEXT DEFAULT 'DRAFT',
            checklist_completion REAL DEFAULT 0.0, blocking_items INTEGER DEFAULT 0,
            readiness TEXT DEFAULT 'NOT_READY', notes TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '2026-01-01',
            updated_at TEXT NOT NULL DEFAULT '2026-01-01'
        );
        CREATE TABLE IF NOT EXISTS portfolio_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL, is_stale INTEGER NOT NULL DEFAULT 0,
            reconciled_at TEXT NOT NULL DEFAULT '2026-01-01',
            quantity REAL DEFAULT 0, avg_cost REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS portfolio_stress_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS portfolio_stress_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL, scenario_type TEXT NOT NULL,
            estimated_loss_pct REAL NOT NULL,
            position_results_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT '2026-01-01'
        );
        CREATE TABLE IF NOT EXISTS market_regime_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL, overall_regime TEXT NOT NULL,
            regime_score REAL NOT NULL DEFAULT 50.0
        );
    """)
    conn.commit()
    conn.close()

    importlib.reload(sc)
    yield


# ---------------------------------------------------------------------------
# Tests: classify_ticker
# ---------------------------------------------------------------------------

class TestClassifyTicker:
    def test_spy_is_core_index(self):
        assert sc.classify_ticker("SPY") == "CORE_INDEX"

    def test_qqq_is_core_index(self):
        assert sc.classify_ticker("QQQ") == "CORE_INDEX"

    def test_vfv_to_is_core_index(self):
        assert sc.classify_ticker("VFV.TO") == "CORE_INDEX"

    def test_xiu_to_is_core_index(self):
        assert sc.classify_ticker("XIU.TO") == "CORE_INDEX"

    def test_xqq_to_is_core_index(self):
        assert sc.classify_ticker("XQQ.TO") == "CORE_INDEX"

    def test_xeqt_to_is_core_index(self):
        assert sc.classify_ticker("XEQT.TO") == "CORE_INDEX"

    def test_veqt_to_is_core_index(self):
        assert sc.classify_ticker("VEQT.TO") == "CORE_INDEX"

    def test_zqq_to_is_core_index(self):
        assert sc.classify_ticker("ZQQ.TO") == "CORE_INDEX"

    def test_hxs_to_is_core_index(self):
        assert sc.classify_ticker("HXS.TO") == "CORE_INDEX"

    def test_msft_is_growth(self):
        assert sc.classify_ticker("MSFT") == "GROWTH_COMPOUNDER"

    def test_aapl_is_growth(self):
        assert sc.classify_ticker("AAPL") == "GROWTH_COMPOUNDER"

    def test_amzn_is_growth(self):
        assert sc.classify_ticker("AMZN") == "GROWTH_COMPOUNDER"

    def test_goog_is_growth(self):
        assert sc.classify_ticker("GOOG") == "GROWTH_COMPOUNDER"

    def test_meta_is_growth(self):
        assert sc.classify_ticker("META") == "GROWTH_COMPOUNDER"

    def test_shop_to_is_growth(self):
        assert sc.classify_ticker("SHOP.TO") == "GROWTH_COMPOUNDER"

    def test_ry_to_is_growth(self):
        assert sc.classify_ticker("RY.TO") == "GROWTH_COMPOUNDER"

    def test_td_to_is_growth(self):
        assert sc.classify_ticker("TD.TO") == "GROWTH_COMPOUNDER"

    def test_enb_to_is_growth(self):
        assert sc.classify_ticker("ENB.TO") == "GROWTH_COMPOUNDER"

    def test_cnq_to_is_growth(self):
        assert sc.classify_ticker("CNQ.TO") == "GROWTH_COMPOUNDER"

    def test_nvda_is_ai_semi(self):
        assert sc.classify_ticker("NVDA") == "AI_SEMI_MOMENTUM"

    def test_amd_is_ai_semi(self):
        assert sc.classify_ticker("AMD") == "AI_SEMI_MOMENTUM"

    def test_tsm_is_ai_semi(self):
        assert sc.classify_ticker("TSM") == "AI_SEMI_MOMENTUM"

    def test_pltr_is_ai_semi(self):
        assert sc.classify_ticker("PLTR") == "AI_SEMI_MOMENTUM"

    def test_unknown_is_speculative(self):
        assert sc.classify_ticker("UNKNWN") == "SPECULATIVE_HIGH_VOL"

    def test_lowercase_handled(self):
        assert sc.classify_ticker("nvda") == "AI_SEMI_MOMENTUM"

    def test_empty_is_speculative(self):
        assert sc.classify_ticker("") == "SPECULATIVE_HIGH_VOL"


# ---------------------------------------------------------------------------
# Tests: classify_setup_type
# ---------------------------------------------------------------------------

class TestClassifySetupType:
    def test_breakout_expansion(self):
        assert sc.classify_setup_type("BREAKOUT_EXPANSION") == "BREAKOUT_MOMENTUM"

    def test_squeeze_candidate(self):
        assert sc.classify_setup_type("SQUEEZE_CANDIDATE") == "SHORT_SQUEEZE"

    def test_catalyst_runup(self):
        assert sc.classify_setup_type("CATALYST_RUNUP") == "EVENT_CATALYST"

    def test_options_pressure(self):
        assert sc.classify_setup_type("OPTIONS_PRESSURE") == "SPECULATIVE_HIGH_VOL"

    def test_early_accumulation(self):
        assert sc.classify_setup_type("EARLY_ACCUMULATION") == "EARLY_ACCUMULATION"

    def test_high_risk_speculation(self):
        assert sc.classify_setup_type("HIGH_RISK_SPECULATION") == "SPECULATIVE_HIGH_VOL"

    def test_unknown_setup(self):
        assert sc.classify_setup_type("MYSTERY_SETUP") == "SPECULATIVE_HIGH_VOL"

    def test_empty_setup(self):
        assert sc.classify_setup_type("") == "SPECULATIVE_HIGH_VOL"

    def test_none_setup(self):
        assert sc.classify_setup_type(None) == "SPECULATIVE_HIGH_VOL"


# ---------------------------------------------------------------------------
# Tests: classify_candidate
# ---------------------------------------------------------------------------

class TestClassifyCandidate:
    def test_ticker_takes_priority_over_setup(self):
        # NVDA is AI_SEMI_MOMENTUM even if setup says BREAKOUT
        assert sc.classify_candidate("NVDA", "BREAKOUT_EXPANSION", None) == "AI_SEMI_MOMENTUM"

    def test_unknown_ticker_uses_setup(self):
        assert sc.classify_candidate("UNKWN", "BREAKOUT_EXPANSION", None) == "BREAKOUT_MOMENTUM"

    def test_unknown_ticker_unknown_setup_is_speculative(self):
        assert sc.classify_candidate("UNKWN", "MYSTERY", None) == "SPECULATIVE_HIGH_VOL"

    def test_none_setup_unknown_ticker(self):
        assert sc.classify_candidate("UNKWN", None, None) == "SPECULATIVE_HIGH_VOL"

    def test_spy_always_core(self):
        assert sc.classify_candidate("SPY", "SQUEEZE_CANDIDATE", "HIGH_CONVICTION") == "CORE_INDEX"


# ---------------------------------------------------------------------------
# Tests: compute_thesis_completeness
# ---------------------------------------------------------------------------

class TestComputeThesisCompleteness:
    def _full_thesis(self):
        return {
            "thesis_title":       "Strong AI tailwind play",
            "thesis_text":        "NVDA is leading the AI compute revolution with dominant GPU market share",
            "entry_reason":       "Breaking out of 6-month consolidation on high volume",
            "expected_catalysts": "Q3 earnings, new Blackwell GPU launch, hyperscaler capex",
            "risk_factors":       "Valuation multiple contraction, China export restrictions",
            "invalidation_level": 95.0,
            "target_level":       175.0,
            "exit_plan":          "Sell 50% at target, hold remainder with trailing stop",
        }

    def test_full_thesis_near_100(self):
        score = sc.compute_thesis_completeness(self._full_thesis())
        assert score >= 90.0

    def test_empty_thesis_is_zero(self):
        score = sc.compute_thesis_completeness({})
        assert score == 0.0

    def test_partial_thesis(self):
        thesis = {
            "thesis_title": "NVDA momentum play on GPU demand",
            "thesis_text":  "Strong demand from AI training market",
        }
        score = sc.compute_thesis_completeness(thesis)
        assert 0.0 < score < 100.0

    def test_very_short_text_not_counted(self):
        # Text < 10 chars should not count
        thesis = {"thesis_title": "NVDA", "thesis_text": "good"}
        score = sc.compute_thesis_completeness(thesis)
        assert score == 0.0

    def test_numeric_fields_counted(self):
        t1 = {"invalidation_level": 100.0, "thesis_text": "long enough thesis text here"}
        t2 = {"thesis_text": "long enough thesis text here"}
        assert sc.compute_thesis_completeness(t1) > sc.compute_thesis_completeness(t2)

    def test_returns_float(self):
        assert isinstance(sc.compute_thesis_completeness({}), float)

    def test_between_0_and_100(self):
        thesis = self._full_thesis()
        score = sc.compute_thesis_completeness(thesis)
        assert 0.0 <= score <= 100.0


# ---------------------------------------------------------------------------
# Tests: compute_checklist_discipline_score
# ---------------------------------------------------------------------------

class TestComputeChecklistDisciplineScore:
    def test_empty_returns_none(self):
        assert sc.compute_checklist_discipline_score([]) is None

    def test_perfect_completion(self):
        checklists = [{"checklist_completion": 100.0, "blocking_items": 0}]
        assert sc.compute_checklist_discipline_score(checklists) == 100.0

    def test_zero_completion(self):
        checklists = [{"checklist_completion": 0.0, "blocking_items": 0}]
        assert sc.compute_checklist_discipline_score(checklists) == 0.0

    def test_avg_across_multiple(self):
        checklists = [
            {"checklist_completion": 80.0, "blocking_items": 0},
            {"checklist_completion": 60.0, "blocking_items": 0},
        ]
        result = sc.compute_checklist_discipline_score(checklists)
        assert result == pytest.approx(70.0)

    def test_blocking_items_penalize(self):
        cl_clean    = [{"checklist_completion": 80.0, "blocking_items": 0}]
        cl_blocked  = [{"checklist_completion": 80.0, "blocking_items": 3}]
        assert sc.compute_checklist_discipline_score(cl_blocked) < sc.compute_checklist_discipline_score(cl_clean)

    def test_penalty_capped_at_20(self):
        # 100 blocking items should still not drop below completion - 20
        checklists = [{"checklist_completion": 90.0, "blocking_items": 100}]
        result = sc.compute_checklist_discipline_score(checklists)
        assert result == pytest.approx(70.0)

    def test_score_never_below_zero(self):
        checklists = [{"checklist_completion": 10.0, "blocking_items": 50}]
        result = sc.compute_checklist_discipline_score(checklists)
        assert result >= 0.0

    def test_missing_fields_default_to_zero(self):
        result = sc.compute_checklist_discipline_score([{}])
        assert result == 0.0


# ---------------------------------------------------------------------------
# Tests: compute_validation_quality
# ---------------------------------------------------------------------------

class TestComputeValidationQuality:
    def test_empty_returns_none(self):
        assert sc.compute_validation_quality([]) is None

    def test_single_row(self):
        rows = [{"validation_score": 75.0}]
        assert sc.compute_validation_quality(rows) == 75.0

    def test_avg_of_multiple(self):
        rows = [{"validation_score": 60.0}, {"validation_score": 80.0}]
        assert sc.compute_validation_quality(rows) == pytest.approx(70.0)

    def test_ignores_none_scores(self):
        rows = [{"validation_score": 80.0}, {"validation_score": None}]
        result = sc.compute_validation_quality(rows)
        assert result == 80.0

    def test_all_none_returns_none(self):
        rows = [{"validation_score": None}]
        assert sc.compute_validation_quality(rows) is None


# ---------------------------------------------------------------------------
# Tests: compute_risk_adjusted_score
# ---------------------------------------------------------------------------

class TestComputeRiskAdjustedScore:
    def test_no_data_returns_none(self):
        assert sc.compute_risk_adjusted_score(None, None, None, None) is None

    def test_50pct_win_rate_no_return(self):
        # 50% win rate, no avg_return → starts at 50
        score = sc.compute_risk_adjusted_score(50.0, None, None, None)
        assert score == pytest.approx(50.0)

    def test_high_win_rate_boosts_score(self):
        score_high = sc.compute_risk_adjusted_score(80.0, None, None, None)
        score_low  = sc.compute_risk_adjusted_score(30.0, None, None, None)
        assert score_high > score_low

    def test_positive_return_boosts_score(self):
        s1 = sc.compute_risk_adjusted_score(50.0,  5.0, None, None)
        s2 = sc.compute_risk_adjusted_score(50.0, -5.0, None, None)
        assert s1 > s2

    def test_large_drawdown_reduces_score(self):
        s_safe = sc.compute_risk_adjusted_score(50.0, 0.0,  -2.0, None)
        s_risky= sc.compute_risk_adjusted_score(50.0, 0.0, -25.0, None)
        assert s_risky < s_safe

    def test_high_validation_boosts_score(self):
        s_high = sc.compute_risk_adjusted_score(50.0, 0.0, 0.0, 90.0)
        s_low  = sc.compute_risk_adjusted_score(50.0, 0.0, 0.0, 20.0)
        assert s_high > s_low

    def test_clamped_0_to_100(self):
        # Extreme positive
        s = sc.compute_risk_adjusted_score(100.0, 50.0, 0.0, 100.0)
        assert s <= 100.0
        # Extreme negative
        s = sc.compute_risk_adjusted_score(0.0, -50.0, -50.0, 0.0)
        assert s >= 0.0

    def test_returns_float(self):
        s = sc.compute_risk_adjusted_score(60.0, 3.0, -5.0, 70.0)
        assert isinstance(s, float)


# ---------------------------------------------------------------------------
# Tests: compute_confidence_score
# ---------------------------------------------------------------------------

class TestComputeConfidenceScore:
    def test_zero_outcomes(self):
        assert sc.compute_confidence_score(0) == 0.0

    def test_one_outcome(self):
        assert sc.compute_confidence_score(1) == 20.0

    def test_four_outcomes(self):
        assert sc.compute_confidence_score(4) == 20.0

    def test_five_outcomes(self):
        assert sc.compute_confidence_score(5) == 50.0

    def test_fifteen_outcomes(self):
        assert sc.compute_confidence_score(15) == 75.0

    def test_thirty_outcomes(self):
        assert sc.compute_confidence_score(30) == 100.0

    def test_large_count(self):
        assert sc.compute_confidence_score(100) == 100.0


# ---------------------------------------------------------------------------
# Tests: compute_stress_sensitivity
# ---------------------------------------------------------------------------

class TestComputeStressSensitivity:
    def test_no_events_uses_default(self):
        result = sc.compute_stress_sensitivity("CORE_INDEX", [])
        assert result == sc._DEFAULT_STRESS_SENSITIVITY["CORE_INDEX"]

    def test_ai_semi_default_high(self):
        result = sc.compute_stress_sensitivity("AI_SEMI_MOMENTUM", [])
        assert result >= 20.0

    def test_cash_defensive_default_low(self):
        result = sc.compute_stress_sensitivity("CASH_DEFENSIVE", [])
        assert result <= 5.0

    def test_with_matching_position_results(self):
        events = [{
            "scenario_type": "AI_SEMI_REVERSAL",
            "estimated_loss_pct": -25.0,
            "position_results_json": json.dumps([
                {"ticker": "NVDA", "shock_pct": -35.0, "market_value": 10000.0,
                 "estimated_loss": -3500.0, "stressed_value": 6500.0},
                {"ticker": "AMD",  "shock_pct": -35.0, "market_value": 5000.0,
                 "estimated_loss": -1750.0, "stressed_value": 3250.0},
            ]),
        }]
        result = sc.compute_stress_sensitivity("AI_SEMI_MOMENTUM", events)
        assert result == pytest.approx(35.0)

    def test_no_matching_tickers_uses_default(self):
        events = [{
            "scenario_type": "MARKET_CRASH_20",
            "estimated_loss_pct": -20.0,
            "position_results_json": json.dumps([
                {"ticker": "SPY", "shock_pct": -20.0, "market_value": 5000.0,
                 "estimated_loss": -1000.0, "stressed_value": 4000.0},
            ]),
        }]
        result = sc.compute_stress_sensitivity("AI_SEMI_MOMENTUM", events)
        assert result == sc._DEFAULT_STRESS_SENSITIVITY["AI_SEMI_MOMENTUM"]

    def test_returns_float(self):
        result = sc.compute_stress_sensitivity("CORE_INDEX", [])
        assert isinstance(result, float)

    def test_unknown_strategy_returns_default(self):
        result = sc.compute_stress_sensitivity("NONEXISTENT", [])
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# Tests: compute_scorecard
# ---------------------------------------------------------------------------

class TestComputeScorecard:
    def _empty_data(self):
        return {
            "shadow_rows": [], "outcome_rows": [], "validation_rows": [],
            "thesis_rows": [], "checklist_rows": [], "position_rows": [],
            "stress_events": [],
        }

    def _make_outcome(self, return_5d, max_gain, max_drawdown):
        return {"return_5d": return_5d, "max_gain": max_gain,
                "max_drawdown": max_drawdown, "status": "COMPLETE"}

    def test_empty_data_returns_valid_dict(self):
        card = sc.compute_scorecard("CORE_INDEX", self._empty_data())
        assert isinstance(card, dict)
        assert card["strategy"] == "CORE_INDEX"

    def test_required_keys_present(self):
        card = sc.compute_scorecard("CORE_INDEX", self._empty_data())
        required = {
            "strategy", "total_candidates", "total_decisions",
            "active_positions", "closed_positions", "win_rate", "avg_return",
            "avg_max_gain", "avg_max_drawdown", "validation_quality",
            "false_positive_rate", "stress_sensitivity", "thesis_completeness",
            "checklist_discipline_score", "risk_adjusted_score",
            "confidence_score", "data_available",
        }
        assert required.issubset(set(card.keys()))

    def test_total_candidates_counts_shadow_rows(self):
        data = self._empty_data()
        data["shadow_rows"] = [{"ticker": "NVDA"}, {"ticker": "AMD"}]
        card = sc.compute_scorecard("AI_SEMI_MOMENTUM", data)
        assert card["total_candidates"] == 2

    def test_win_rate_computed(self):
        data = self._empty_data()
        data["outcome_rows"] = [
            self._make_outcome(5.0,  8.0, -2.0),
            self._make_outcome(3.0,  4.0, -1.5),
            self._make_outcome(-4.0, 2.0, -5.0),
        ]
        card = sc.compute_scorecard("SPECULATIVE_HIGH_VOL", data)
        assert card["win_rate"] == pytest.approx(66.7, abs=0.1)

    def test_avg_return_computed(self):
        data = self._empty_data()
        data["outcome_rows"] = [
            self._make_outcome(6.0, 10.0, -2.0),
            self._make_outcome(4.0,  6.0, -1.0),
        ]
        card = sc.compute_scorecard("BREAKOUT_MOMENTUM", data)
        assert card["avg_return"] == pytest.approx(5.0)

    def test_no_outcomes_gives_none_metrics(self):
        card = sc.compute_scorecard("CORE_INDEX", self._empty_data())
        assert card["win_rate"] is None
        assert card["avg_return"] is None
        assert card["risk_adjusted_score"] is None

    def test_false_positive_rate_from_negative_behaviors(self):
        data = self._empty_data()
        data["validation_rows"] = [
            {"behavior_class": "VOLATILITY_TRAP",  "validation_score": 30.0},
            {"behavior_class": "VALID_BREAKOUT",   "validation_score": 80.0},
            {"behavior_class": "FAILED_SQUEEZE",   "validation_score": 25.0},
        ]
        card = sc.compute_scorecard("SHORT_SQUEEZE", data)
        assert card["false_positive_rate"] == pytest.approx(66.7, abs=0.1)

    def test_active_positions_counted(self):
        data = self._empty_data()
        data["position_rows"] = [
            {"is_stale": 0}, {"is_stale": 0}, {"is_stale": 1}
        ]
        card = sc.compute_scorecard("CORE_INDEX", data)
        assert card["active_positions"] == 2
        assert card["closed_positions"] == 1

    def test_data_available_true_when_candidates(self):
        data = self._empty_data()
        data["shadow_rows"] = [{"ticker": "NVDA"}]
        card = sc.compute_scorecard("AI_SEMI_MOMENTUM", data)
        assert card["data_available"] is True

    def test_data_available_false_when_empty(self):
        card = sc.compute_scorecard("CASH_DEFENSIVE", self._empty_data())
        assert card["data_available"] is False

    def test_thesis_completeness_averaged(self):
        data = self._empty_data()
        data["thesis_rows"] = [
            {
                "thesis_title": "Strong AI tailwind play right here",
                "thesis_text":  "Long enough thesis text about the company",
                "entry_reason": "Breakout confirmed on volume",
                "expected_catalysts": "Earnings and product launch",
                "risk_factors": "Competition and regulation",
                "invalidation_level": 90.0, "target_level": 150.0,
                "exit_plan": "Sell at resistance with trailing stop",
            }
        ]
        card = sc.compute_scorecard("AI_SEMI_MOMENTUM", data)
        assert card["thesis_completeness"] is not None
        assert card["thesis_completeness"] > 0.0

    def test_confidence_score_from_outcomes(self):
        data = self._empty_data()
        data["outcome_rows"] = [self._make_outcome(5.0, 8.0, -2.0)] * 20
        card = sc.compute_scorecard("BREAKOUT_MOMENTUM", data)
        assert card["confidence_score"] >= 75.0

    def test_stress_sensitivity_has_value(self):
        card = sc.compute_scorecard("AI_SEMI_MOMENTUM", self._empty_data())
        assert card["stress_sensitivity"] is not None
        assert isinstance(card["stress_sensitivity"], float)


# ---------------------------------------------------------------------------
# Tests: compute_behavior_metrics
# ---------------------------------------------------------------------------

class TestComputeBehaviorMetrics:
    def _make_card(self, strategy, candidates=0, ra_score=None, fp_rate=None,
                   dd=None, discipline=None, thesis=None):
        return {
            "strategy":                  strategy,
            "total_candidates":          candidates,
            "risk_adjusted_score":       ra_score,
            "false_positive_rate":       fp_rate,
            "avg_max_drawdown":          dd,
            "checklist_discipline_score":discipline,
            "thesis_completeness":       thesis,
        }

    def test_empty_scorecards(self):
        result = sc.compute_behavior_metrics([])
        assert isinstance(result, dict)
        assert result["overused"] == []

    def test_overused_detection(self):
        cards = [
            self._make_card("AI_SEMI_MOMENTUM",    candidates=25),
            self._make_card("BREAKOUT_MOMENTUM",   candidates=30),
            self._make_card("CORE_INDEX",          candidates=2),
        ]
        result = sc.compute_behavior_metrics(cards)
        assert "BREAKOUT_MOMENTUM"  in result["overused"]
        assert "AI_SEMI_MOMENTUM"   in result["overused"]
        assert "CORE_INDEX"         not in result["overused"]

    def test_underused_detection(self):
        cards = [
            self._make_card("SPACE_DEFENSE",     candidates=1),
            self._make_card("CRYPTO_BETA",       candidates=0),
            self._make_card("AI_SEMI_MOMENTUM",  candidates=20),
        ]
        result = sc.compute_behavior_metrics(cards)
        # Only strategies with candidates > 0 are "non_empty" but listed if ≤ 2
        assert "SPACE_DEFENSE" in result["underused"]

    def test_best_historical_by_score(self):
        cards = [
            self._make_card("AI_SEMI_MOMENTUM", candidates=10, ra_score=85.0),
            self._make_card("CORE_INDEX",       candidates=10, ra_score=60.0),
            self._make_card("SHORT_SQUEEZE",    candidates=5,  ra_score=20.0),
        ]
        result = sc.compute_behavior_metrics(cards)
        assert result["best_historical"][0] == "AI_SEMI_MOMENTUM"

    def test_worst_drawdowns_most_negative_first(self):
        cards = [
            self._make_card("AI_SEMI_MOMENTUM",  candidates=5, dd=-25.0),
            self._make_card("CORE_INDEX",        candidates=5, dd=-5.0),
            self._make_card("SHORT_SQUEEZE",     candidates=5, dd=-18.0),
        ]
        result = sc.compute_behavior_metrics(cards)
        assert result["worst_drawdowns"][0] == "AI_SEMI_MOMENTUM"

    def test_checklist_neglect(self):
        cards = [
            self._make_card("AI_SEMI_MOMENTUM",  candidates=5, discipline=30.0),
            self._make_card("CORE_INDEX",        candidates=5, discipline=90.0),
        ]
        result = sc.compute_behavior_metrics(cards)
        assert "AI_SEMI_MOMENTUM" in result["checklist_neglect"]
        assert "CORE_INDEX"       not in result["checklist_neglect"]

    def test_weak_thesis(self):
        cards = [
            self._make_card("SHORT_SQUEEZE",  candidates=5, thesis=20.0),
            self._make_card("CORE_INDEX",     candidates=5, thesis=85.0),
        ]
        result = sc.compute_behavior_metrics(cards)
        assert "SHORT_SQUEEZE" in result["weak_thesis"]
        assert "CORE_INDEX"    not in result["weak_thesis"]

    def test_repeated_false_positives(self):
        cards = [
            self._make_card("SPECULATIVE_HIGH_VOL", candidates=5, fp_rate=45.0),
            self._make_card("CORE_INDEX",           candidates=5, fp_rate=10.0),
        ]
        result = sc.compute_behavior_metrics(cards)
        assert "SPECULATIVE_HIGH_VOL" in result["repeated_false_positives"]
        assert "CORE_INDEX"           not in result["repeated_false_positives"]

    def test_required_keys_present(self):
        result = sc.compute_behavior_metrics([])
        required = {
            "overused", "underused", "best_historical", "worst_drawdowns",
            "checklist_neglect", "weak_thesis", "repeated_false_positives",
        }
        assert required.issubset(set(result.keys()))


# ---------------------------------------------------------------------------
# Tests: generate_recommendations
# ---------------------------------------------------------------------------

class TestGenerateRecommendations:
    def _card(self, **kwargs):
        defaults = {
            "strategy": "AI_SEMI_MOMENTUM",
            "total_candidates": 20, "win_rate": 55.0, "avg_return": 3.0,
            "avg_max_drawdown": -8.0, "false_positive_rate": 20.0,
            "thesis_completeness": 70.0, "checklist_discipline_score": 70.0,
            "risk_adjusted_score": 55.0, "confidence_score": 75.0,
            "active_positions": 1,
        }
        defaults.update(kwargs)
        return defaults

    def test_low_candidates_gives_monitor_only(self):
        card = self._card(total_candidates=3)
        recs = sc.generate_recommendations("AI_SEMI_MOMENTUM", card)
        assert "monitor_only" in recs

    def test_monitor_only_no_other_recs(self):
        card = self._card(total_candidates=2)
        recs = sc.generate_recommendations("AI_SEMI_MOMENTUM", card)
        assert recs == ["monitor_only"]

    def test_high_fp_rate_requires_checklist(self):
        card = self._card(false_positive_rate=50.0, confidence_score=75.0)
        recs = sc.generate_recommendations("AI_SEMI_MOMENTUM", card)
        assert "require_stricter_checklist" in recs

    def test_weak_thesis_recommends_improve(self):
        card = self._card(thesis_completeness=25.0)
        recs = sc.generate_recommendations("AI_SEMI_MOMENTUM", card)
        assert "improve_thesis_quality" in recs

    def test_large_drawdown_smaller_sizing(self):
        card = self._card(avg_max_drawdown=-20.0)
        recs = sc.generate_recommendations("AI_SEMI_MOMENTUM", card)
        assert "use_smaller_sizing" in recs

    def test_risk_off_and_drawdown_avoid(self):
        card = self._card(avg_max_drawdown=-14.0)
        recs = sc.generate_recommendations("AI_SEMI_MOMENTUM", card, regime="RISK_OFF")
        assert "avoid_during_risk_off" in recs

    def test_normal_regime_no_avoid_rec(self):
        card = self._card(avg_max_drawdown=-14.0)
        recs = sc.generate_recommendations("AI_SEMI_MOMENTUM", card, regime="NEUTRAL")
        assert "avoid_during_risk_off" not in recs

    def test_strong_score_no_positions_increase_focus(self):
        card = self._card(risk_adjusted_score=75.0, active_positions=0, win_rate=65.0,
                          confidence_score=75.0)
        recs = sc.generate_recommendations("AI_SEMI_MOMENTUM", card)
        assert "increase_focus" in recs

    def test_promote_to_core(self):
        card = self._card(risk_adjusted_score=85.0, win_rate=70.0, confidence_score=75.0)
        recs = sc.generate_recommendations("AI_SEMI_MOMENTUM", card)
        assert "promote_to_core" in recs

    def test_weak_score_reduce_exposure(self):
        card = self._card(risk_adjusted_score=20.0, confidence_score=75.0, win_rate=50.0)
        recs = sc.generate_recommendations("AI_SEMI_MOMENTUM", card)
        assert "reduce_exposure" in recs

    def test_low_win_rate_reduce(self):
        card = self._card(win_rate=25.0, confidence_score=75.0, risk_adjusted_score=40.0)
        recs = sc.generate_recommendations("AI_SEMI_MOMENTUM", card)
        assert "reduce_exposure" in recs

    def test_returns_list(self):
        card = self._card()
        recs = sc.generate_recommendations("CORE_INDEX", card)
        assert isinstance(recs, list)

    def test_all_recs_are_known_keys(self):
        card = self._card(false_positive_rate=50.0, thesis_completeness=20.0,
                          avg_max_drawdown=-20.0, risk_adjusted_score=20.0,
                          confidence_score=75.0)
        recs = sc.generate_recommendations("AI_SEMI_MOMENTUM", card, regime="PANIC")
        for r in recs:
            assert r in sc.RECOMMENDATIONS, f"Unknown recommendation key: {r!r}"

    def test_no_recommendations_defaults_monitor_only(self):
        # Low data, moderate everything else
        card = self._card(total_candidates=3)
        recs = sc.generate_recommendations("AI_SEMI_MOMENTUM", card)
        assert "monitor_only" in recs


# ---------------------------------------------------------------------------
# Tests: STRATEGY_TYPES and RECOMMENDATIONS constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_strategy_types_count(self):
        assert len(sc.STRATEGY_TYPES) == 11

    def test_all_strategies_present(self):
        expected = {
            "CORE_INDEX", "GROWTH_COMPOUNDER", "AI_SEMI_MOMENTUM",
            "SPACE_DEFENSE", "CRYPTO_BETA", "SHORT_SQUEEZE",
            "EVENT_CATALYST", "BREAKOUT_MOMENTUM", "EARLY_ACCUMULATION",
            "SPECULATIVE_HIGH_VOL", "CASH_DEFENSIVE",
        }
        assert set(sc.STRATEGY_TYPES) == expected

    def test_recommendations_keys(self):
        expected = {
            "increase_focus", "reduce_exposure", "require_stricter_checklist",
            "improve_thesis_quality", "use_smaller_sizing", "avoid_during_risk_off",
            "monitor_only", "promote_to_core",
        }
        assert set(sc.RECOMMENDATIONS.keys()) == expected


# ---------------------------------------------------------------------------
# Tests: DB functions — compute_all_scorecards
# ---------------------------------------------------------------------------

class TestComputeAllScorecards:
    def _insert_shadow(self, conn, ticker, setup_type="BREAKOUT_EXPANSION", tier="STRONG_WATCH"):
        conn.execute(
            "INSERT INTO alpha_shadow_log (ticker, scan_time, alpha_tier, setup_type) VALUES (?,?,?,?)",
            (ticker, "2026-01-01T12:00:00", tier, setup_type)
        )
        conn.commit()

    def _insert_outcome(self, conn, ticker, return_5d, max_gain, max_drawdown):
        conn.execute(
            "INSERT INTO alpha_outcomes (ticker, scan_time, return_5d, max_gain, max_drawdown, status) "
            "VALUES (?,?,?,?,?,'COMPLETE')",
            (ticker, "2026-01-01T12:00:00", return_5d, max_gain, max_drawdown)
        )
        conn.commit()

    @pytest.fixture()
    def conn(self, tmp_path):
        import sqlite3
        db_file = list(tmp_path.iterdir())[0] if list(tmp_path.iterdir()) else tmp_path / "test.db"
        # Use the monkeypatched DB via database module
        return database.get_connection()

    def test_returns_dict_with_scorecards_key(self):
        result = sc.compute_all_scorecards()
        assert "scorecards" in result
        assert isinstance(result["scorecards"], list)

    def test_returns_all_strategy_types(self):
        result = sc.compute_all_scorecards()
        strategies = {c["strategy"] for c in result["scorecards"]}
        assert strategies == set(sc.STRATEGY_TYPES)

    def test_returns_behavior_metrics(self):
        result = sc.compute_all_scorecards()
        assert "behavior_metrics" in result

    def test_returns_computed_at(self):
        result = sc.compute_all_scorecards()
        assert "computed_at" in result

    def test_empty_db_all_data_available_false(self):
        result = sc.compute_all_scorecards()
        for card in result["scorecards"]:
            assert card["data_available"] is False

    def test_with_shadow_rows(self):
        conn = database.get_connection()
        conn.execute(
            "INSERT INTO alpha_shadow_log (ticker, scan_time, alpha_tier, setup_type) VALUES (?,?,?,?)",
            ("NVDA", "2026-01-01T12:00:00", "HIGH_CONVICTION", "BREAKOUT_EXPANSION")
        )
        conn.commit()
        conn.close()

        result = sc.compute_all_scorecards()
        ai_card = next(c for c in result["scorecards"] if c["strategy"] == "AI_SEMI_MOMENTUM")
        assert ai_card["total_candidates"] == 1

    def test_recommendations_included(self):
        result = sc.compute_all_scorecards()
        for card in result["scorecards"]:
            assert "recommendations" in card
            assert isinstance(card["recommendations"], list)


# ---------------------------------------------------------------------------
# Tests: get_scorecard
# ---------------------------------------------------------------------------

class TestGetScorecard:
    def test_valid_strategy_returns_dict(self):
        result = sc.get_scorecard("CORE_INDEX")
        assert isinstance(result, dict)
        assert result["strategy"] == "CORE_INDEX"

    def test_unknown_strategy_returns_none(self):
        assert sc.get_scorecard("NONEXISTENT") is None

    def test_all_strategy_types_retrievable(self):
        for strategy in sc.STRATEGY_TYPES:
            card = sc.get_scorecard(strategy)
            assert card is not None
            assert card["strategy"] == strategy

    def test_includes_behavior_metrics(self):
        card = sc.get_scorecard("CORE_INDEX")
        assert "behavior_metrics" in card

    def test_includes_computed_at(self):
        card = sc.get_scorecard("CORE_INDEX")
        assert "computed_at" in card


# ---------------------------------------------------------------------------
# Tests: get_scorecards_summary
# ---------------------------------------------------------------------------

class TestGetScoreboardSummary:
    def test_returns_dict(self):
        result = sc.get_scorecards_summary()
        assert isinstance(result, dict)

    def test_required_keys(self):
        result = sc.get_scorecards_summary()
        required = {
            "total_strategies", "strategies_with_data", "top_strategies",
            "bottom_strategies", "behavior_metrics", "priority_recommendations",
            "computed_at",
        }
        assert required.issubset(set(result.keys()))

    def test_total_strategies_is_11(self):
        result = sc.get_scorecards_summary()
        assert result["total_strategies"] == 11

    def test_strategies_with_data_is_zero_empty_db(self):
        result = sc.get_scorecards_summary()
        assert result["strategies_with_data"] == 0

    def test_top_strategies_list(self):
        result = sc.get_scorecards_summary()
        assert isinstance(result["top_strategies"], list)

    def test_priority_recs_excludes_monitor_only(self):
        # With empty DB, all strategies get monitor_only → no priority recs
        result = sc.get_scorecards_summary()
        for rec in result["priority_recommendations"]:
            assert rec["recommendation"] != "monitor_only"

    def test_priority_recs_max_10(self):
        result = sc.get_scorecards_summary()
        assert len(result["priority_recommendations"]) <= 10


# ---------------------------------------------------------------------------
# Tests: no trading calls
# ---------------------------------------------------------------------------

class TestNoTradingCalls:
    def test_no_trade_execution_in_source(self):
        import inspect
        source = inspect.getsource(sc)
        trading_keywords = [
            "place_order", "execute_trade", "submit_order", "buy_shares",
            "sell_shares", "market_order", "limit_order", "trade_execution",
        ]
        for kw in trading_keywords:
            assert kw not in source.lower(), f"Found trading keyword in source: {kw!r}"

    def test_no_update_or_delete_in_source(self):
        import inspect
        source = inspect.getsource(sc)
        lines  = [l.strip().upper() for l in source.splitlines()]
        for line in lines:
            if line.startswith("#"):
                continue
            if "UPDATE " in line and "updated_at" not in line.lower():
                if not line.startswith("'") and not line.startswith('"'):
                    pass  # SQL string literals are fine
            # Check only for actual SQL statements (not variable names)
            if line.startswith("DELETE ") or (line.startswith("UPDATE ") and "DEFAULT" not in line):
                pytest.fail(f"Found potentially mutating SQL in source: {line[:80]}")

    def test_no_alerts_sent_in_source(self):
        import inspect
        source = inspect.getsource(sc)
        assert "send_alert" not in source
        assert "send_whatsapp" not in source
        assert "twilio" not in source.lower()


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

    # Create minimal required tables
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS alpha_shadow_log (id INTEGER PRIMARY KEY, ticker TEXT, scan_time TEXT, alpha_tier TEXT, setup_type TEXT, filter_reason TEXT);
        CREATE TABLE IF NOT EXISTS alpha_outcomes (id INTEGER PRIMARY KEY, ticker TEXT, scan_time TEXT, alpha_tier TEXT, setup_type TEXT, return_5d REAL, max_gain REAL, max_drawdown REAL, status TEXT DEFAULT 'PENDING');
        CREATE TABLE IF NOT EXISTS alpha_validation (id INTEGER PRIMARY KEY, outcome_id INTEGER, ticker TEXT, scan_time TEXT, setup_type TEXT, alpha_tier TEXT, behavior_class TEXT, validation_score REAL, confidence TEXT, computed_at TEXT);
        CREATE TABLE IF NOT EXISTS position_theses (id INTEGER PRIMARY KEY, ticker TEXT, thesis_title TEXT DEFAULT '', thesis_text TEXT DEFAULT '', setup_type TEXT DEFAULT '', conviction_level TEXT DEFAULT 'MEDIUM', time_horizon TEXT DEFAULT 'MEDIUM', entry_reason TEXT DEFAULT '', expected_catalysts TEXT DEFAULT '', risk_factors TEXT DEFAULT '', invalidation_level REAL, target_level REAL, exit_plan TEXT DEFAULT '', status TEXT DEFAULT 'ACTIVE', next_review_at TEXT DEFAULT '2030-01-01', created_at TEXT DEFAULT '2026-01-01', updated_at TEXT DEFAULT '2026-01-01');
        CREATE TABLE IF NOT EXISTS decision_checklists (id INTEGER PRIMARY KEY, checklist_id TEXT UNIQUE, ticker TEXT, decision_type TEXT, checklist_status TEXT DEFAULT 'DRAFT', checklist_completion REAL DEFAULT 0.0, blocking_items INTEGER DEFAULT 0, readiness TEXT DEFAULT 'NOT_READY', notes TEXT DEFAULT '', created_at TEXT DEFAULT '2026-01-01', updated_at TEXT DEFAULT '2026-01-01');
        CREATE TABLE IF NOT EXISTS portfolio_positions (id INTEGER PRIMARY KEY, ticker TEXT, is_stale INTEGER DEFAULT 0, reconciled_at TEXT DEFAULT '2026-01-01', quantity REAL DEFAULT 0, avg_cost REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS portfolio_stress_runs (id INTEGER PRIMARY KEY, run_id TEXT UNIQUE, created_at TEXT);
        CREATE TABLE IF NOT EXISTS portfolio_stress_events (id INTEGER PRIMARY KEY, run_id TEXT, scenario_type TEXT, estimated_loss_pct REAL, position_results_json TEXT DEFAULT '[]', created_at TEXT DEFAULT '2026-01-01');
        CREATE TABLE IF NOT EXISTS market_regime_snapshots (id INTEGER PRIMARY KEY, captured_at TEXT, overall_regime TEXT, regime_score REAL DEFAULT 50.0);
    """)
    conn.commit()
    conn.close()

    importlib.reload(sc)

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


class TestApiStrategiesScoreCards:
    def test_returns_200(self, client):
        resp = client.get("/api/v1/strategies/scorecards")
        assert resp.status_code == 200

    def test_ok_envelope(self, client):
        data = client.get("/api/v1/strategies/scorecards").get_json()
        assert data["ok"] is True

    def test_scorecards_in_data(self, client):
        data = client.get("/api/v1/strategies/scorecards").get_json()
        assert "scorecards" in data["data"]
        assert len(data["data"]["scorecards"]) == 11

    def test_behavior_metrics_in_data(self, client):
        data = client.get("/api/v1/strategies/scorecards").get_json()
        assert "behavior_metrics" in data["data"]

    def test_cached_flag(self, client):
        data = client.get("/api/v1/strategies/scorecards").get_json()
        assert "cached" in data["meta"]


class TestApiStrategiesSummary:
    def test_returns_200(self, client):
        resp = client.get("/api/v1/strategies/summary")
        assert resp.status_code == 200

    def test_ok_envelope(self, client):
        data = client.get("/api/v1/strategies/summary").get_json()
        assert data["ok"] is True

    def test_total_strategies_is_11(self, client):
        data = client.get("/api/v1/strategies/summary").get_json()
        assert data["data"]["total_strategies"] == 11

    def test_top_strategies_list(self, client):
        data = client.get("/api/v1/strategies/summary").get_json()
        assert isinstance(data["data"]["top_strategies"], list)

    def test_priority_recs_list(self, client):
        data = client.get("/api/v1/strategies/summary").get_json()
        assert isinstance(data["data"]["priority_recommendations"], list)

    def test_cached_flag(self, client):
        data = client.get("/api/v1/strategies/summary").get_json()
        assert "cached" in data["meta"]


class TestApiStrategyDetail:
    def test_returns_200_valid_strategy(self, client):
        resp = client.get("/api/v1/strategies/CORE_INDEX")
        assert resp.status_code == 200

    def test_returns_404_unknown(self, client):
        resp = client.get("/api/v1/strategies/NONEXISTENT_STRATEGY")
        assert resp.status_code == 404

    def test_ok_envelope_valid(self, client):
        data = client.get("/api/v1/strategies/AI_SEMI_MOMENTUM").get_json()
        assert data["ok"] is True

    def test_scorecard_in_data(self, client):
        data = client.get("/api/v1/strategies/AI_SEMI_MOMENTUM").get_json()
        assert "scorecard" in data["data"]
        assert data["data"]["scorecard"]["strategy"] == "AI_SEMI_MOMENTUM"

    def test_all_strategy_types_return_200(self, client):
        for strategy in sc.STRATEGY_TYPES:
            resp = client.get(f"/api/v1/strategies/{strategy}")
            assert resp.status_code == 200, f"Expected 200 for {strategy}"

    def test_cached_flag(self, client):
        data = client.get("/api/v1/strategies/CORE_INDEX").get_json()
        assert "cached" in data["meta"]
