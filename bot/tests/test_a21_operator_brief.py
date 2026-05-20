"""
Tests for Phase A21: Daily Operator Brief 2.0 (bot/operator_brief.py).
"""
import importlib
import json
import sys
import types

import pytest

import database
import operator_brief as ob


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_portfolio(n_positions=2, total_value=50000.0, cash=5000.0, pnl=1000.0):
    positions = [
        {
            "ticker":            f"T{i}",
            "quantity":          10.0,
            "avg_cost":          100.0,
            "market_price":      110.0,
            "market_value":      1100.0,
            "unrealized_pnl":    100.0,
            "unrealized_pnl_pct": 10.0,
        }
        for i in range(n_positions)
    ]
    return {
        "positions":  positions,
        "aggregates": {
            "total_portfolio_value": total_value,
            "cash":                  cash,
        },
    }


def _make_candidate(ticker="NVDA", tier="ALERT_READY", score=85.0):
    return {
        "ticker":        ticker,
        "alpha_score":   score,
        "alpha_tier":    "TIER_A",
        "setup_type":    "BREAKOUT_EXPANSION",
        "readiness_tier": tier,
        "reason":        f"Strong score for {ticker}",
    }


def _make_dry_run(ticker="AAPL", score=78.0):
    return {
        "ticker":         ticker,
        "readiness_tier": "PRE_ALERT",
        "alpha_score":    score,
        "status":         "DRY_RUN",
        "message_text":   f"Watch {ticker}: strong setup.",
    }


def _make_checklist(ticker="NVDA", decision_type="BUY"):
    return {
        "ticker":           ticker,
        "decision_type":    decision_type,
        "checklist_status": "DRAFT",
        "readiness":        "NOT_READY",
        "blocking_items":   2,
    }


def _make_review(ticker="AAPL"):
    return {
        "ticker":          ticker,
        "next_review_at":  "2026-01-01T00:00:00",
        "conviction_level": "HIGH",
    }


def _make_stress_run(worst_pct=-22.0):
    return {
        "run_id":         "STR-ABC",
        "worst_scenario": "MARKET_CRASH_20",
        "worst_loss_pct": worst_pct,
        "avg_loss_pct":   -10.0,
        "warnings":       ["Portfolio may be concentrated in correlated risk"],
        "created_at":     "2026-05-19T09:00:00",
    }


def _full_data():
    """Complete data dict with all sources populated."""
    return {
        "portfolio":         _make_portfolio(),
        "cash":              5000.0,
        "tfsa_room":         10000.0,
        "overnight_signals": ["NVDA: RSI rollover at 78", "AAPL: MA cross"],
        "alpha_candidates":  [
            _make_candidate("NVDA", "ALERT_READY", 88.0),
            _make_candidate("AAPL", "PRE_ALERT", 72.0),
            _make_candidate("MSFT", "PRE_ALERT", 68.0),
            _make_candidate("AMD",  "PRE_ALERT", 60.0),
        ],
        "dry_runs":          [_make_dry_run("AAPL"), _make_dry_run("MSFT")],
        "qc_summary": {
            "total_evaluated": 10,
            "allowed_count":   4,
            "suppressed_count": 6,
            "priority_candidates": 2,
            "avg_qc_score": 65.0,
        },
        "regime_ctx": {
            "available":          True,
            "overall_regime":     "NEUTRAL",
            "volatility_regime":  "NORMAL",
            "breadth_regime":     "NEUTRAL",
            "speculative_regime": "NEUTRAL",
            "regime_score":       55.0,
            "warnings":           [],
            "captured_at":        "2026-05-19T08:00:00",
        },
        "risk_report": {
            "cash_warning":           "Cash below minimum reserve",
            "drawdown_warning":       "NVDA is -18.0% below cost — review exit plan",
            "theme_warnings":         ["AI_TECH theme >40% of portfolio"],
            "concentration_warnings": [],
        },
        "stress_run": _make_stress_run(),
        "pending_checklists": [
            _make_checklist("NVDA", "BUY"),
            _make_checklist("AAPL", "SELL"),
            _make_checklist("MSFT", "BUY"),
            _make_checklist("AMD",  "BUY"),
        ],
        "due_reviews": {
            "due":            [_make_review("AAPL"), _make_review("MSFT")],
            "overdue":        [_make_review("AAPL")],
            "upcoming":       [_make_review("MSFT")],
            "due_count":      2,
            "overdue_count":  1,
            "upcoming_count": 1,
        },
        "thesis_warnings": {
            "missing_thesis":    ["NVDA"],
            "stale_thesis":      ["AMD"],
            "missing_exit_plan": [],
            "has_warnings":      True,
        },
        "scorecard_summary": {
            "top_strategies":    [{"strategy": "CORE_INDEX", "risk_adjusted_score": 75.0}],
            "bottom_strategies": [{"strategy": "SPECULATIVE_HIGH_VOL", "risk_adjusted_score": 25.0}],
            "priority_recommendations": [
                {"strategy": "SPECULATIVE_HIGH_VOL", "recommendation": "reduce_exposure",
                 "description": "Reduce exposure to this strategy"},
            ],
            "behavior_metrics": {},
            "computed_at": "2026-05-19T09:00:00",
        },
        "planner_snapshot": {
            "rebalance_urgency":       "MEDIUM",
            "priority_areas":          [
                {"bucket": "CORE_INDEX", "drift_pct": 8.0, "action": "INCREASE"},
            ],
            "cash_deployment_guidance": "Cash is above target — consider deploying into core ETFs.",
            "contribution_guidance":    "TFSA room available.",
            "risk_reduction_guidance":  "Risk score is moderate.",
            "regime":                   "NEUTRAL",
            "created_at":               "2026-05-19T08:30:00",
        },
    }


def _empty_data():
    return {
        "portfolio": {}, "cash": 0.0, "tfsa_room": 0.0,
        "overnight_signals": [], "alpha_candidates": [], "dry_runs": [],
        "qc_summary": {}, "regime_ctx": {}, "risk_report": {}, "stress_run": None,
        "pending_checklists": [], "due_reviews": {}, "thesis_warnings": {},
        "scorecard_summary": {}, "planner_snapshot": None,
    }


# ── Constants ──────────────────────────────────────────────────────────────────

class TestConstants:
    def test_modes_list(self):
        assert ob.MODES == ["compact", "detailed", "debug"]

    def test_required_sections_count(self):
        assert len(ob.REQUIRED_SECTIONS) == 14

    def test_required_sections_contains_all(self):
        for key in [
            "portfolio_truth", "overnight_changes", "alpha_highlights",
            "dryrun_highlights", "qc_suppression_summary", "market_regime",
            "risk_warnings", "stress_worst_case", "checklists_due",
            "thesis_reviews_due", "scorecard_warnings", "planner_summary",
            "cash_tfsa_notes", "key_actions",
        ]:
            assert key in ob.REQUIRED_SECTIONS

    def test_banned_words_not_empty(self):
        assert len(ob.BANNED_WORDS) >= 5

    def test_banned_words_contains_moon(self):
        assert "moon" in ob.BANNED_WORDS

    def test_compact_max_chars(self):
        assert ob.COMPACT_MAX_CHARS == 1600

    def test_max_limits_positive(self):
        assert ob.MAX_ALPHA_HIGHLIGHTS   == 3
        assert ob.MAX_RISK_WARNINGS      == 3
        assert ob.MAX_CHECKLIST_ITEMS    == 3
        assert ob.MAX_PLANNER_NOTES      == 3
        assert ob.MAX_OVERNIGHT_SIGNALS  == 5
        assert ob.MAX_DRYRUN_HIGHLIGHTS  == 3
        assert ob.MAX_THESIS_REVIEWS     == 3
        assert ob.MAX_SCORECARD_WARNINGS == 3
        assert ob.MAX_KEY_ACTIONS        == 7


# ── Utility helpers ────────────────────────────────────────────────────────────

class TestCheckBannedWords:
    def test_no_banned_words(self):
        assert ob.check_banned_words("NVDA has strong momentum") == []

    def test_finds_moon(self):
        result = ob.check_banned_words("going to the moon")
        assert "moon" in result

    def test_case_insensitive(self):
        result = ob.check_banned_words("This will EXPLODE higher")
        assert "explode" in result

    def test_finds_multiple(self):
        result = ob.check_banned_words("moon explosion")
        assert "moon" in result
        assert "explosion" in result

    def test_empty_string(self):
        assert ob.check_banned_words("") == []

    def test_has_banned_word_true(self):
        assert ob._has_banned_word("it will rocket") is True

    def test_has_banned_word_false(self):
        assert ob._has_banned_word("steady upward trend") is False


class TestHelpers:
    def test_safe_truncate_short(self):
        assert ob._safe_truncate("hello", 10) == "hello"

    def test_safe_truncate_long(self):
        result = ob._safe_truncate("hello world", 5)
        assert result == "hello…"

    def test_safe_truncate_empty(self):
        assert ob._safe_truncate("", 10) == ""

    def test_safe_truncate_exact(self):
        assert ob._safe_truncate("hello", 5) == "hello"

    def test_sign_positive(self):
        assert ob._sign(5.0) == "+"

    def test_sign_zero(self):
        assert ob._sign(0.0) == "+"

    def test_sign_negative(self):
        assert ob._sign(-3.0) == ""


# ── Section builders ───────────────────────────────────────────────────────────

class TestPortfolioTruthSection:
    def test_empty_portfolio(self):
        result = ob._portfolio_truth_section({})
        assert result["position_count"] == 0
        assert result["total_value"] == 0.0
        assert result["unrealized_pnl"] == 0.0
        assert result["positions"] == []

    def test_with_positions(self):
        portfolio = _make_portfolio(n_positions=2, total_value=20000.0, cash=2000.0)
        result = ob._portfolio_truth_section(portfolio)
        assert result["position_count"] == 2
        assert result["total_value"] == 20000.0
        assert result["cash"] == 2000.0
        assert result["unrealized_pnl"] == 200.0  # 2 × 100
        assert result["unrealized_pnl_pct"] > 0

    def test_pnl_pct_zero_when_no_cost(self):
        portfolio = {
            "positions": [{"ticker": "X", "quantity": 0.0, "avg_cost": 0.0,
                           "market_value": 0.0, "unrealized_pnl": 0.0,
                           "unrealized_pnl_pct": 0.0}],
            "aggregates": {"total_portfolio_value": 0.0, "cash": 0.0},
        }
        result = ob._portfolio_truth_section(portfolio)
        assert result["unrealized_pnl_pct"] == 0.0

    def test_positions_list_structure(self):
        portfolio = _make_portfolio(n_positions=1)
        result = ob._portfolio_truth_section(portfolio)
        assert len(result["positions"]) == 1
        pos = result["positions"][0]
        assert "ticker" in pos
        assert "market_value" in pos
        assert "unrealized_pnl" in pos


class TestOvernightSection:
    def test_empty(self):
        assert ob._overnight_section([]) == []

    def test_cap_at_max(self):
        signals = [f"T{i}: signal" for i in range(10)]
        result = ob._overnight_section(signals)
        assert len(result) <= ob.MAX_OVERNIGHT_SIGNALS

    def test_filters_banned_words(self):
        signals = ["NVDA: steady", "AAPL: will moon tonight"]
        result = ob._overnight_section(signals)
        assert len(result) == 1
        assert result[0] == "NVDA: steady"

    def test_preserves_order(self):
        signals = ["NVDA: first", "AAPL: second"]
        result = ob._overnight_section(signals)
        assert result[0] == "NVDA: first"


class TestAlphaHighlightsSection:
    def test_empty(self):
        assert ob._alpha_highlights_section([]) == []

    def test_filters_by_tier(self):
        candidates = [
            _make_candidate("NVDA", "ALERT_READY"),
            _make_candidate("AAPL", "NOT_READY"),
        ]
        result = ob._alpha_highlights_section(candidates)
        assert len(result) == 1
        assert result[0]["ticker"] == "NVDA"

    def test_cap_at_max(self):
        candidates = [_make_candidate(f"T{i}", "ALERT_READY") for i in range(10)]
        result = ob._alpha_highlights_section(candidates)
        assert len(result) <= ob.MAX_ALPHA_HIGHLIGHTS

    def test_includes_required_fields(self):
        result = ob._alpha_highlights_section([_make_candidate()])
        assert len(result) == 1
        c = result[0]
        assert "ticker" in c
        assert "alpha_score" in c
        assert "readiness_tier" in c
        assert "reason" in c

    def test_rare_alert_included(self):
        candidates = [_make_candidate("X", "RARE_ALERT")]
        result = ob._alpha_highlights_section(candidates)
        assert len(result) == 1

    def test_none_score_handled(self):
        c = _make_candidate()
        c["alpha_score"] = None
        result = ob._alpha_highlights_section([c])
        assert result[0]["alpha_score"] is None


class TestDryrunHighlightsSection:
    def test_empty(self):
        assert ob._dryrun_highlights_section([]) == []

    def test_cap_at_max(self):
        dry_runs = [_make_dry_run(f"T{i}") for i in range(10)]
        result = ob._dryrun_highlights_section(dry_runs)
        assert len(result) <= ob.MAX_DRYRUN_HIGHLIGHTS

    def test_message_preview_truncated(self):
        dr = _make_dry_run()
        dr["message_text"] = "A" * 200
        result = ob._dryrun_highlights_section([dr])
        assert len(result[0]["message_preview"]) <= 121  # 120 + ellipsis

    def test_required_fields(self):
        result = ob._dryrun_highlights_section([_make_dry_run()])
        c = result[0]
        assert "ticker" in c
        assert "readiness_tier" in c
        assert "status" in c
        assert "message_preview" in c


class TestQcSection:
    def test_empty_input(self):
        result = ob._qc_section({})
        assert result["total_evaluated"] == 0
        assert result["suppressed_count"] == 0

    def test_passes_fields(self):
        qc = {"total_evaluated": 10, "allowed_count": 4, "suppressed_count": 6,
              "priority_candidates": 2, "avg_qc_score": 65.0}
        result = ob._qc_section(qc)
        assert result["total_evaluated"] == 10
        assert result["suppressed_count"] == 6


class TestRegimeSection:
    def test_not_available(self):
        result = ob._regime_section({})
        assert result["available"] is False
        assert result["overall_regime"] == "NEUTRAL"

    def test_available(self):
        ctx = {"available": True, "overall_regime": "RISK_OFF",
               "regime_score": 30.0, "warnings": ["High VIX"]}
        result = ob._regime_section(ctx)
        assert result["available"] is True
        assert result["overall_regime"] == "RISK_OFF"
        assert result["regime_score"] == 30.0

    def test_warnings_capped_at_3(self):
        ctx = {"available": True, "warnings": ["w1", "w2", "w3", "w4", "w5"]}
        result = ob._regime_section(ctx)
        assert len(result["warnings"]) <= 3


class TestRiskWarningsSection:
    def test_empty_report(self):
        assert ob._risk_warnings_section({}) == []

    def test_cash_warning_first(self):
        report = {"cash_warning": "Cash low"}
        result = ob._risk_warnings_section(report)
        assert result[0] == "Cash low"

    def test_cap_at_max(self):
        report = {
            "cash_warning":           "A",
            "drawdown_warning":       "B",
            "theme_warnings":         ["C", "D", "E"],
            "concentration_warnings": ["F"],
        }
        result = ob._risk_warnings_section(report)
        assert len(result) <= ob.MAX_RISK_WARNINGS

    def test_none_drawdown_skipped(self):
        report = {"drawdown_warning": None, "cash_warning": "Cash low"}
        result = ob._risk_warnings_section(report)
        assert "Cash low" in result
        assert None not in result


class TestStressSection:
    def test_none_run(self):
        result = ob._stress_section(None)
        assert result["available"] is False

    def test_with_run(self):
        result = ob._stress_section(_make_stress_run(-22.0))
        assert result["available"] is True
        assert result["worst_scenario"] == "MARKET_CRASH_20"
        assert result["worst_loss_pct"] == -22.0

    def test_warnings_capped(self):
        run = _make_stress_run()
        run["warnings"] = ["w1", "w2", "w3", "w4"]
        result = ob._stress_section(run)
        assert len(result["warnings"]) <= 2


class TestChecklistsSection:
    def test_empty(self):
        assert ob._checklists_section([]) == []

    def test_cap_at_max(self):
        items = [_make_checklist(f"T{i}") for i in range(10)]
        result = ob._checklists_section(items)
        assert len(result) <= ob.MAX_CHECKLIST_ITEMS

    def test_required_fields(self):
        result = ob._checklists_section([_make_checklist()])
        c = result[0]
        assert "ticker" in c
        assert "decision_type" in c
        assert "checklist_status" in c
        assert "readiness" in c
        assert "blocking_items" in c


class TestThesisReviewsSection:
    def test_empty(self):
        result = ob._thesis_reviews_section({}, {})
        assert result["overdue_count"] == 0
        assert result["overdue"] == []

    def test_overdue_count(self):
        due_reviews = {"overdue": [_make_review("AAPL"), _make_review("MSFT")],
                       "upcoming": []}
        result = ob._thesis_reviews_section(due_reviews, {})
        assert result["overdue_count"] == 2

    def test_overdue_capped_at_max(self):
        overdue = [_make_review(f"T{i}") for i in range(10)]
        due_reviews = {"overdue": overdue, "upcoming": []}
        result = ob._thesis_reviews_section(due_reviews, {})
        assert len(result["overdue"]) <= ob.MAX_THESIS_REVIEWS

    def test_missing_thesis_capped(self):
        thesis_warnings = {"missing_thesis": [f"T{i}" for i in range(10)],
                           "stale_thesis": [], "missing_exit_plan": []}
        result = ob._thesis_reviews_section({}, thesis_warnings)
        assert len(result["missing_thesis"]) <= ob.MAX_THESIS_REVIEWS


class TestScorecardWarningsSection:
    def test_empty(self):
        assert ob._scorecard_warnings_section({}) == []

    def test_low_score_triggers_warning(self):
        summary = {"bottom_strategies": [{"strategy": "SPEC", "risk_adjusted_score": 25.0}],
                   "priority_recommendations": []}
        result = ob._scorecard_warnings_section(summary)
        assert len(result) >= 1
        assert "SPEC" in result[0]

    def test_high_score_not_flagged(self):
        summary = {"bottom_strategies": [{"strategy": "CORE", "risk_adjusted_score": 75.0}],
                   "priority_recommendations": []}
        result = ob._scorecard_warnings_section(summary)
        assert result == []

    def test_cap_at_max(self):
        recs = [
            {"strategy": f"S{i}", "recommendation": "reduce_exposure",
             "description": f"desc {i}"}
            for i in range(10)
        ]
        summary = {"bottom_strategies": [], "priority_recommendations": recs}
        result = ob._scorecard_warnings_section(summary)
        assert len(result) <= ob.MAX_SCORECARD_WARNINGS

    def test_deduplication(self):
        recs = [{"strategy": "SPEC", "recommendation": "reduce_exposure",
                 "description": "Reduce exposure"}]
        summary = {
            "bottom_strategies": [{"strategy": "SPEC", "risk_adjusted_score": 20.0}],
            "priority_recommendations": recs,
        }
        # Both would add "SPEC" — deduplicate
        result = ob._scorecard_warnings_section(summary)
        spec_entries = [w for w in result if w.startswith("SPEC")]
        assert len(spec_entries) <= 2  # at most one from each source


class TestPlannerSection:
    def test_no_snapshot(self):
        result = ob._planner_section(None)
        assert result["available"] is False

    def test_with_snapshot(self):
        snapshot = {
            "rebalance_urgency": "HIGH",
            "priority_areas":    [{"bucket": "CORE_INDEX", "drift_pct": 15.0}],
            "cash_deployment_guidance": "Deploy cash.",
            "contribution_guidance":    "Room available.",
            "risk_reduction_guidance":  "Reduce spec.",
            "regime":                   "NEUTRAL",
            "created_at":               "2026-05-19",
        }
        result = ob._planner_section(snapshot)
        assert result["available"] is True
        assert result["rebalance_urgency"] == "HIGH"

    def test_priority_areas_capped(self):
        snapshot = {
            "priority_areas": [{"bucket": f"B{i}"} for i in range(10)],
        }
        result = ob._planner_section(snapshot)
        assert len(result["priority_areas"]) <= ob.MAX_PLANNER_NOTES


class TestCashTfsaSection:
    def test_values(self):
        result = ob._cash_tfsa_section(1234.5, 9876.1)
        assert result["cash"] == 1234.5
        assert result["tfsa_room"] == 9876.1

    def test_zero_values(self):
        result = ob._cash_tfsa_section(0.0, 0.0)
        assert result["cash"] == 0.0
        assert result["tfsa_room"] == 0.0


class TestKeyActionsSection:
    def _call(self, **overrides):
        defaults = dict(
            regime_section={"available": False, "overall_regime": "NEUTRAL", "regime_score": 50.0, "warnings": []},
            risk_warnings=[],
            alpha_highlights=[],
            checklists_due=[],
            thesis_reviews={"overdue_count": 0, "overdue": [], "upcoming": [], "missing_thesis": []},
            planner_section={"available": False},
            scorecard_warnings=[],
        )
        defaults.update(overrides)
        return ob._key_actions_section(**defaults)

    def test_empty_data_returns_empty(self):
        result = self._call()
        assert isinstance(result, list)
        assert len(result) == 0

    def test_risk_off_regime_adds_action(self):
        result = self._call(
            regime_section={"available": True, "overall_regime": "RISK_OFF", "regime_score": 25.0, "warnings": []}
        )
        assert any("RISK_OFF" in a for a in result)

    def test_risk_on_regime_adds_action(self):
        result = self._call(
            regime_section={"available": True, "overall_regime": "RISK_ON", "regime_score": 75.0, "warnings": []}
        )
        assert any("RISK_ON" in a for a in result)

    def test_alpha_highlights_adds_watch(self):
        result = self._call(alpha_highlights=[_make_candidate("NVDA", "ALERT_READY", 88.0)])
        assert any("NVDA" in a and "Watch" in a for a in result)

    def test_pending_checklist_adds_action(self):
        result = self._call(checklists_due=[_make_checklist("NVDA", "BUY")])
        assert any("NVDA" in a and "checklist" in a.lower() for a in result)

    def test_overdue_thesis_adds_action(self):
        result = self._call(
            thesis_reviews={"overdue_count": 1, "overdue": [_make_review("AAPL")], "upcoming": [], "missing_thesis": []}
        )
        assert any("AAPL" in a for a in result)

    def test_high_urgency_adds_rebalance_note(self):
        result = self._call(
            planner_section={"available": True, "rebalance_urgency": "HIGH", "priority_areas": []}
        )
        assert any("rebalanc" in a.lower() for a in result)

    def test_none_urgency_no_rebalance_note(self):
        result = self._call(
            planner_section={"available": True, "rebalance_urgency": "NONE", "priority_areas": []}
        )
        assert not any("rebalanc" in a.lower() for a in result)

    def test_max_actions(self):
        result = self._call(
            regime_section={"available": True, "overall_regime": "RISK_OFF", "regime_score": 20.0, "warnings": []},
            risk_warnings=["w1", "w2"],
            alpha_highlights=[_make_candidate(f"T{i}", "ALERT_READY") for i in range(3)],
            checklists_due=[_make_checklist(f"T{i}") for i in range(3)],
            thesis_reviews={"overdue_count": 2, "overdue": [_make_review("A"), _make_review("B")], "upcoming": [], "missing_thesis": []},
            planner_section={"available": True, "rebalance_urgency": "HIGH", "priority_areas": []},
            scorecard_warnings=["w1"],
        )
        assert len(result) <= ob.MAX_KEY_ACTIONS

    def test_no_trade_verbs(self):
        result = self._call(
            risk_warnings=["Position A is too large"],
            alpha_highlights=[_make_candidate("NVDA", "ALERT_READY")],
        )
        for action in result:
            lower = action.lower()
            assert "buy" not in lower
            assert "sell" not in lower
            assert "order" not in lower


# ── build_sections ─────────────────────────────────────────────────────────────

class TestBuildSections:
    def test_all_required_keys_present_empty(self):
        sections = ob.build_sections(_empty_data())
        for key in ob.REQUIRED_SECTIONS:
            assert key in sections, f"Missing section: {key}"

    def test_all_required_keys_present_full(self):
        sections = ob.build_sections(_full_data())
        for key in ob.REQUIRED_SECTIONS:
            assert key in sections, f"Missing section: {key}"

    def test_portfolio_truth_is_dict(self):
        sections = ob.build_sections(_empty_data())
        assert isinstance(sections["portfolio_truth"], dict)

    def test_key_actions_is_list(self):
        sections = ob.build_sections(_full_data())
        assert isinstance(sections["key_actions"], list)

    def test_alpha_highlights_capped(self):
        data = _empty_data()
        data["alpha_candidates"] = [_make_candidate(f"T{i}", "ALERT_READY") for i in range(10)]
        sections = ob.build_sections(data)
        assert len(sections["alpha_highlights"]) <= ob.MAX_ALPHA_HIGHLIGHTS

    def test_risk_warnings_capped(self):
        data = _empty_data()
        data["risk_report"] = {
            "cash_warning": "A", "drawdown_warning": "B",
            "theme_warnings": ["C", "D"], "concentration_warnings": ["E"],
        }
        sections = ob.build_sections(data)
        assert len(sections["risk_warnings"]) <= ob.MAX_RISK_WARNINGS


# ── format_compact_brief ───────────────────────────────────────────────────────

class TestFormatCompactBrief:
    def test_returns_string(self):
        sections = ob.build_sections(_empty_data())
        result = ob.format_compact_brief(sections)
        assert isinstance(result, str)

    def test_length_bounded(self):
        sections = ob.build_sections(_full_data())
        result = ob.format_compact_brief(sections)
        assert len(result) <= ob.COMPACT_MAX_CHARS

    def test_no_banned_words(self):
        sections = ob.build_sections(_full_data())
        result = ob.format_compact_brief(sections)
        assert ob.check_banned_words(result) == []

    def test_contains_portfolio_header(self):
        sections = ob.build_sections(_full_data())
        result = ob.format_compact_brief(sections)
        assert "PORTFOLIO" in result

    def test_contains_regime(self):
        sections = ob.build_sections(_full_data())
        result = ob.format_compact_brief(sections)
        assert "REGIME" in result

    def test_contains_daily_brief(self):
        sections = ob.build_sections(_empty_data())
        result = ob.format_compact_brief(sections)
        assert "DAILY BRIEF" in result

    def test_alpha_watch_shown_when_present(self):
        data = _empty_data()
        data["alpha_candidates"] = [_make_candidate("NVDA", "ALERT_READY", 90.0)]
        sections = ob.build_sections(data)
        result = ob.format_compact_brief(sections)
        assert "NVDA" in result

    def test_risk_flags_shown_when_present(self):
        data = _empty_data()
        data["risk_report"] = {"cash_warning": "Cash is low", "drawdown_warning": None,
                                "theme_warnings": [], "concentration_warnings": []}
        sections = ob.build_sections(data)
        result = ob.format_compact_brief(sections)
        assert "RISK FLAGS" in result

    def test_empty_data_no_crash(self):
        sections = ob.build_sections(_empty_data())
        result = ob.format_compact_brief(sections)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_today_section_shown_when_actions_exist(self):
        data = _empty_data()
        data["regime_ctx"] = {"available": True, "overall_regime": "RISK_OFF",
                               "regime_score": 20.0, "warnings": []}
        sections = ob.build_sections(data)
        result = ob.format_compact_brief(sections)
        assert "TODAY" in result

    def test_custom_generated_at(self):
        sections = ob.build_sections(_empty_data())
        result = ob.format_compact_brief(sections, generated_at="May 19, 2026 08:45 ET")
        assert "May 19, 2026" in result


# ── format_detailed_brief ──────────────────────────────────────────────────────

class TestFormatDetailedBrief:
    def test_returns_dict(self):
        sections = ob.build_sections(_full_data())
        result = ob.format_detailed_brief(sections)
        assert isinstance(result, dict)

    def test_mode_is_detailed(self):
        sections = ob.build_sections(_full_data())
        result = ob.format_detailed_brief(sections)
        assert result["mode"] == "detailed"

    def test_has_generated_at(self):
        sections = ob.build_sections(_full_data())
        result = ob.format_detailed_brief(sections)
        assert "generated_at" in result

    def test_all_required_sections_present(self):
        sections = ob.build_sections(_full_data())
        result = ob.format_detailed_brief(sections)
        for key in ob.REQUIRED_SECTIONS:
            assert key in result, f"Missing key: {key}"

    def test_custom_generated_at(self):
        sections = ob.build_sections(_empty_data())
        result = ob.format_detailed_brief(sections, generated_at="2026-05-19T08:45:00")
        assert result["generated_at"] == "2026-05-19T08:45:00"

    def test_empty_data_no_crash(self):
        sections = ob.build_sections(_empty_data())
        result = ob.format_detailed_brief(sections)
        assert isinstance(result, dict)


# ── format_debug_brief ─────────────────────────────────────────────────────────

class TestFormatDebugBrief:
    def test_mode_is_debug(self):
        sections = ob.build_sections(_full_data())
        data = _full_data()
        result = ob.format_debug_brief(sections, data)
        assert result["mode"] == "debug"

    def test_has_data_sources(self):
        sections = ob.build_sections(_full_data())
        data = _full_data()
        result = ob.format_debug_brief(sections, data)
        assert "data_sources" in result

    def test_data_sources_keys(self):
        sections = ob.build_sections(_full_data())
        data = _full_data()
        result = ob.format_debug_brief(sections, data)
        ds = result["data_sources"]
        assert "portfolio_available" in ds
        assert "alpha_candidates_count" in ds
        assert "regime_available" in ds
        assert "stress_run_available" in ds
        assert "planner_snapshot_available" in ds

    def test_alpha_count_matches(self):
        data = _full_data()
        data["alpha_candidates"] = [_make_candidate() for _ in range(5)]
        sections = ob.build_sections(data)
        result = ob.format_debug_brief(sections, data)
        assert result["data_sources"]["alpha_candidates_count"] == 5

    def test_empty_data(self):
        data = _empty_data()
        sections = ob.build_sections(data)
        result = ob.format_debug_brief(sections, data)
        assert result["data_sources"]["portfolio_available"] is False
        assert result["data_sources"]["stress_run_available"] is False


# ── generate_brief ─────────────────────────────────────────────────────────────

class TestGenerateBrief:
    def setup_method(self):
        import operator_brief as _ob
        # Patch collect_brief_data to return controlled data
        self._orig = _ob.collect_brief_data

    def teardown_method(self):
        import operator_brief as _ob
        _ob.collect_brief_data = self._orig

    def _patch_collect(self, data):
        import operator_brief as _ob
        _ob.collect_brief_data = lambda: data

    def test_compact_returns_string(self):
        self._patch_collect(_full_data())
        result = ob.generate_brief(mode="compact")
        assert isinstance(result, str)

    def test_detailed_returns_dict(self):
        self._patch_collect(_full_data())
        result = ob.generate_brief(mode="detailed")
        assert isinstance(result, dict)
        assert result["mode"] == "detailed"

    def test_debug_returns_dict_with_sources(self):
        self._patch_collect(_full_data())
        result = ob.generate_brief(mode="debug")
        assert isinstance(result, dict)
        assert "data_sources" in result

    def test_invalid_mode_defaults_to_detailed(self):
        self._patch_collect(_empty_data())
        result = ob.generate_brief(mode="bogus")
        assert isinstance(result, dict)
        assert result["mode"] == "detailed"

    def test_compact_length_bounded(self):
        self._patch_collect(_full_data())
        result = ob.generate_brief(mode="compact")
        assert len(result) <= ob.COMPACT_MAX_CHARS


# ── generate_compact_brief ─────────────────────────────────────────────────────

class TestGenerateCompactBrief:
    def test_returns_string(self, monkeypatch):
        monkeypatch.setattr(ob, "collect_brief_data", lambda: _empty_data())
        result = ob.generate_compact_brief()
        assert isinstance(result, str)

    def test_never_raises_on_exception(self, monkeypatch):
        def _boom():
            raise RuntimeError("simulated failure")
        monkeypatch.setattr(ob, "collect_brief_data", _boom)
        result = ob.generate_compact_brief()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_fallback_contains_brief(self, monkeypatch):
        monkeypatch.setattr(ob, "generate_brief", lambda **kw: (_ for _ in ()).throw(Exception("fail")))
        result = ob.generate_compact_brief()
        assert isinstance(result, str)


# ── Morning scheduler integration ──────────────────────────────────────────────

class TestMorningSchedulerIntegration:
    def test_morning_summary_job_calls_generate_compact_brief(self, monkeypatch):
        """morning_summary_job should use operator_brief.generate_compact_brief."""
        import operator_brief as _ob
        import alerts
        import scheduler

        called = []
        monkeypatch.setattr(_ob, "generate_compact_brief", lambda: called.append(1) or "MOCK BRIEF")
        monkeypatch.setattr(alerts, "send_sms", lambda msg, bypass_quiet=False: True)
        monkeypatch.setattr(alerts, "log_alert", lambda *a, **kw: None)

        scheduler.morning_summary_job()
        assert called, "generate_compact_brief was not called"

    def test_morning_summary_job_sends_brief(self, monkeypatch):
        import operator_brief as _ob
        import alerts
        import scheduler

        sent = []
        monkeypatch.setattr(_ob, "generate_compact_brief", lambda: "TEST BRIEF")
        monkeypatch.setattr(alerts, "send_sms", lambda msg, bypass_quiet=False: sent.append(msg) or True)
        monkeypatch.setattr(alerts, "log_alert", lambda *a, **kw: None)

        scheduler.morning_summary_job()
        assert sent == ["TEST BRIEF"]

    def test_morning_summary_job_sends_exactly_one_message(self, monkeypatch):
        import operator_brief as _ob
        import alerts
        import scheduler

        sent = []
        monkeypatch.setattr(_ob, "generate_compact_brief", lambda: "BRIEF")
        monkeypatch.setattr(alerts, "send_sms", lambda msg, bypass_quiet=False: sent.append(msg) or True)
        monkeypatch.setattr(alerts, "log_alert", lambda *a, **kw: None)

        scheduler.morning_summary_job()
        assert len(sent) == 1

    def test_morning_summary_job_no_crash_if_send_fails(self, monkeypatch):
        import operator_brief as _ob
        import alerts
        import scheduler

        monkeypatch.setattr(_ob, "generate_compact_brief", lambda: "BRIEF")
        monkeypatch.setattr(alerts, "send_sms", lambda msg, bypass_quiet=False: False)
        monkeypatch.setattr(alerts, "log_alert", lambda *a, **kw: None)

        scheduler.morning_summary_job()  # must not raise


# ── No trading calls ───────────────────────────────────────────────────────────

class TestNoTradingCalls:
    def test_operator_brief_source_no_buy_order(self):
        """operator_brief.py must not contain broker/order function calls."""
        import inspect
        source = inspect.getsource(ob)
        # Check for actual call patterns, not plain words that appear in comments
        forbidden_calls = [
            "place_order(", "submit_order(", "create_order(",
            "execute_trade(", "wealthsimple.buy", "wealthsimple.sell",
            "send_alert(", "send_whatsapp(",
        ]
        for term in forbidden_calls:
            assert term not in source, f"Forbidden call found in operator_brief: {term}"

    def test_key_actions_no_buy_sell_verbs(self):
        data = _full_data()
        sections = ob.build_sections(data)
        actions = sections["key_actions"]
        for action in actions:
            lower = action.lower()
            assert "buy" not in lower
            assert "sell" not in lower
            assert "order" not in lower

    def test_compact_brief_no_buy_sell_verbs(self):
        sections = ob.build_sections(_full_data())
        text = ob.format_compact_brief(sections)
        assert "buy" not in text.lower()
        assert "sell" not in text.lower()
        assert "order" not in text.lower()


# ── Prioritization limits ──────────────────────────────────────────────────────

class TestPrioritizationLimits:
    def test_alpha_max_3_when_many_candidates(self):
        data = _empty_data()
        data["alpha_candidates"] = [_make_candidate(f"T{i}", "ALERT_READY") for i in range(20)]
        sections = ob.build_sections(data)
        assert len(sections["alpha_highlights"]) == ob.MAX_ALPHA_HIGHLIGHTS

    def test_risk_warnings_max_3(self):
        data = _empty_data()
        data["risk_report"] = {
            "cash_warning": "A", "drawdown_warning": "B",
            "theme_warnings": ["C", "D", "E", "F"],
            "concentration_warnings": ["G"],
        }
        sections = ob.build_sections(data)
        assert len(sections["risk_warnings"]) == ob.MAX_RISK_WARNINGS

    def test_checklists_max_3(self):
        data = _empty_data()
        data["pending_checklists"] = [_make_checklist(f"T{i}") for i in range(10)]
        sections = ob.build_sections(data)
        assert len(sections["checklists_due"]) == ob.MAX_CHECKLIST_ITEMS

    def test_overnight_max_5(self):
        data = _empty_data()
        data["overnight_signals"] = [f"T{i}: signal" for i in range(20)]
        sections = ob.build_sections(data)
        assert len(sections["overnight_changes"]) == ob.MAX_OVERNIGHT_SIGNALS

    def test_dryrun_max_3(self):
        data = _empty_data()
        data["dry_runs"] = [_make_dry_run(f"T{i}") for i in range(10)]
        sections = ob.build_sections(data)
        assert len(sections["dryrun_highlights"]) == ob.MAX_DRYRUN_HIGHLIGHTS


# ── Sparse-data safety ────────────────────────────────────────────────────────

class TestSparseDataSafety:
    def _all_sections_present(self, sections):
        return all(k in sections for k in ob.REQUIRED_SECTIONS)

    def test_empty_portfolio(self):
        data = _empty_data()
        sections = ob.build_sections(data)
        assert self._all_sections_present(sections)

    def test_none_stress_run(self):
        data = _empty_data()
        data["stress_run"] = None
        sections = ob.build_sections(data)
        assert sections["stress_worst_case"]["available"] is False

    def test_none_planner_snapshot(self):
        data = _empty_data()
        data["planner_snapshot"] = None
        sections = ob.build_sections(data)
        assert sections["planner_summary"]["available"] is False

    def test_empty_regime_ctx(self):
        data = _empty_data()
        data["regime_ctx"] = {}
        sections = ob.build_sections(data)
        assert sections["market_regime"]["available"] is False

    def test_compact_brief_empty_no_crash(self):
        sections = ob.build_sections(_empty_data())
        result = ob.format_compact_brief(sections)
        assert isinstance(result, str)
        assert len(result) <= ob.COMPACT_MAX_CHARS

    def test_detailed_brief_empty_no_crash(self):
        sections = ob.build_sections(_empty_data())
        result = ob.format_detailed_brief(sections)
        assert isinstance(result, dict)


# ── API endpoint ──────────────────────────────────────────────────────────────

@pytest.fixture()
def app(monkeypatch, tmp_path):
    """Isolated Flask app with a temp DB — no production DB touched."""
    import sqlite3
    import importlib
    import api
    import operator_brief as _ob

    db_file = tmp_path / "api_test.db"
    monkeypatch.setenv("DB_PATH",    str(db_file))
    monkeypatch.setenv("API_SECRET", "")
    monkeypatch.setattr(database, "DB_PATH", str(db_file))

    def _conn():
        c = sqlite3.connect(str(db_file), timeout=5, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(database, "get_connection", _conn)

    # Patch collect_brief_data to return controlled data — no real DB queries
    monkeypatch.setattr(_ob, "collect_brief_data", lambda: _full_data())

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


class TestApiDailyBrief:
    def test_get_default_returns_200(self, client):
        resp = client.get("/api/v1/brief/daily")
        assert resp.status_code == 200

    def test_get_default_returns_ok_true(self, client):
        resp = client.get("/api/v1/brief/daily")
        body = json.loads(resp.data)
        assert body["ok"] is True

    def test_mode_detailed_returns_dict(self, client):
        resp = client.get("/api/v1/brief/daily?mode=detailed")
        body = json.loads(resp.data)
        assert isinstance(body["data"], dict)
        assert body["data"]["mode"] == "detailed"

    def test_mode_compact_returns_brief_key(self, client):
        resp = client.get("/api/v1/brief/daily?mode=compact")
        body = json.loads(resp.data)
        assert "brief" in body["data"]
        assert isinstance(body["data"]["brief"], str)

    def test_mode_debug_returns_data_sources(self, client):
        resp = client.get("/api/v1/brief/daily?mode=debug")
        body = json.loads(resp.data)
        assert "data_sources" in body["data"]

    def test_invalid_mode_defaults_to_detailed(self, client):
        resp = client.get("/api/v1/brief/daily?mode=invalid")
        body = json.loads(resp.data)
        assert body["data"]["mode"] == "detailed"

    def test_cached_response(self, client):
        client.get("/api/v1/brief/daily?mode=detailed")
        resp2 = client.get("/api/v1/brief/daily?mode=detailed")
        body2 = json.loads(resp2.data)
        assert body2["meta"]["cached"] is True

    def test_compact_length_bounded(self, client):
        resp = client.get("/api/v1/brief/daily?mode=compact")
        body = json.loads(resp.data)
        brief_text = body["data"]["brief"]
        assert len(brief_text) <= ob.COMPACT_MAX_CHARS

    def test_compact_mode_has_mode_key(self, client):
        resp = client.get("/api/v1/brief/daily?mode=compact")
        body = json.loads(resp.data)
        assert body["data"]["mode"] == "compact"
