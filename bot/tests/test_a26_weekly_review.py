"""
Phase A26 — Weekly Review and Accountability Report tests.

Covers:
- Constants: MODES, GRADES, REQUIRED_SECTIONS (20), BANNED_WORDS
- Week boundary: _parse_week_start and _week_end
- Weekly grade: A/B/C/D/F thresholds, deterministic, boundary conditions
- Accountability metrics: derived correctly from data
- Section builders: pure functions with empty/sparse data
- Compact format: ≤ COMPACT_MAX_CHARS, no banned words
- Scheduler disabled by default (WEEKLY_REVIEW_ENABLED=false)
- No duplicate sends (_already_sent_this_week, _mark_sent)
- Sparse-data safety: all collectors return safe defaults
- No trading calls in source
- API endpoints: GET /review/weekly, GET /review/weekly/history
- Deterministic output: same metrics → same grade
- Week label formatting
- History endpoint pagination
"""
import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

import weekly_review as wr


# ── Isolated DB fixture ────────────────────────────────────────────────────────

def _make_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = tmp.name

    def _conn():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    return path, _conn


def _patch_db(conn_fn):
    import database
    return patch.object(database, "get_connection", conn_fn)


# ── Flask test app fixture ─────────────────────────────────────────────────────

def _make_app():
    import database
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    database.DB_PATH = tmp.name

    def _conn():
        c = sqlite3.connect(tmp.name)
        c.row_factory = sqlite3.Row
        return c

    database.get_connection = _conn
    import api as api_mod
    importlib.reload(api_mod)
    from flask import Flask
    app = Flask("test_a26")
    app.register_blueprint(api_mod.api_bp)
    app.config["TESTING"] = True
    api_mod.cache_clear()
    return app, api_mod, tmp.name, _conn


# ════════════════════════════════════════════════════════════════════════════
# 1. Constants
# ════════════════════════════════════════════════════════════════════════════

class TestConstants(unittest.TestCase):

    def test_modes(self):
        self.assertEqual(wr.MODES, ["compact", "detailed", "debug"])

    def test_grades(self):
        self.assertEqual(set(wr.GRADES), {"A", "B", "C", "D", "F"})

    def test_required_sections_count(self):
        self.assertEqual(len(wr.REQUIRED_SECTIONS), 20)

    def test_required_sections_contain_all_spec(self):
        for key in [
            "portfolio_weekly_change", "alpha_generated", "alpha_improved",
            "alpha_failed", "validation_outcomes", "notification_activity",
            "qc_suppressions", "delivery_attempts", "checklist_discipline",
            "workflow_summary", "thesis_summary", "watchlist_changes",
            "scorecard_changes", "stress_test_changes", "planner_drift_changes",
            "regime_changes", "key_mistakes", "best_decisions",
            "missed_opportunities", "focus_next_week",
        ]:
            self.assertIn(key, wr.REQUIRED_SECTIONS, msg=f"Missing: {key}")

    def test_banned_words_non_empty(self):
        self.assertGreater(len(wr.BANNED_WORDS), 0)

    def test_compact_max_chars_positive(self):
        self.assertGreater(wr.COMPACT_MAX_CHARS, 0)

    def test_grade_thresholds_keys(self):
        for g in ("A", "B", "C", "D"):
            self.assertIn(g, wr.GRADE_THRESHOLDS)


# ════════════════════════════════════════════════════════════════════════════
# 2. Week boundary helpers
# ════════════════════════════════════════════════════════════════════════════

class TestWeekBoundary(unittest.TestCase):

    def test_parse_returns_monday_for_monday_input(self):
        # 2026-05-18 is a Monday
        result = wr._parse_week_start("2026-05-18")
        self.assertEqual(result, "2026-05-18")

    def test_parse_returns_monday_for_midweek_input(self):
        # 2026-05-20 is a Wednesday — should go back to 2026-05-18 (Monday)
        result = wr._parse_week_start("2026-05-20")
        self.assertEqual(result, "2026-05-18")

    def test_parse_returns_monday_for_sunday_input(self):
        # 2026-05-24 is a Sunday — should go back to 2026-05-18
        result = wr._parse_week_start("2026-05-24")
        self.assertEqual(result, "2026-05-18")

    def test_parse_no_arg_returns_string(self):
        result = wr._parse_week_start(None)
        self.assertIsInstance(result, str)
        self.assertRegex(result, r"^\d{4}-\d{2}-\d{2}$")

    def test_parse_invalid_input_returns_string(self):
        result = wr._parse_week_start("not-a-date")
        self.assertIsInstance(result, str)

    def test_week_end_is_7_days_after(self):
        end = wr._week_end("2026-05-18")
        self.assertEqual(end, "2026-05-25")

    def test_week_label_same_month(self):
        label = wr._week_label("2026-05-18", "2026-05-25")
        self.assertIn("May", label)
        self.assertIn("2026", label)

    def test_week_label_cross_month(self):
        label = wr._week_label("2026-05-25", "2026-06-01")
        # Should mention both months
        self.assertIn("2026", label)


# ════════════════════════════════════════════════════════════════════════════
# 3. Weekly grade
# ════════════════════════════════════════════════════════════════════════════

class TestComputeWeeklyGrade(unittest.TestCase):

    def _grade(self, **kwargs) -> str:
        metrics = {
            "review_completion_rate":         kwargs.get("review_completion_rate", 1.0),
            "overdue_review_count":           kwargs.get("overdue_review_count", 0),
            "checklist_discipline_score":     kwargs.get("checklist_discipline_score", 1.0),
            "ignored_high_priority_workflow": kwargs.get("ignored_high_priority_workflow", 0),
            "unreviewed_dry_runs":            kwargs.get("unreviewed_dry_runs", 0),
            "stale_theses":                   kwargs.get("stale_theses", 0),
            "alpha_false_positive_count":     kwargs.get("alpha_false_positive_count", 0),
            "missed_winner_count":            kwargs.get("missed_winner_count", 0),
            "risk_warnings_unresolved":       kwargs.get("risk_warnings_unresolved", 0),
        }
        return wr.compute_weekly_grade(metrics)

    def test_perfect_metrics_gives_a(self):
        self.assertEqual(self._grade(), "A")

    def test_many_overdue_drops_to_b(self):
        # 3 overdue = -15 → score 85 → B
        self.assertEqual(self._grade(overdue_review_count=3), "B")

    def test_many_overdue_and_stale_drops_to_c(self):
        # 4 overdue = -20, 1 stale = -5 → score 75 → B threshold
        # 4 overdue = -20, 2 stale = -10 → score 70 → B
        # Need more to drop to C: 4 overdue -20, 3 stale -15 → 65 → C
        self.assertEqual(self._grade(overdue_review_count=4, stale_theses=3), "C")

    def test_zero_completion_penalized(self):
        # 0 completion (-10) + 1 stale thesis (-5) → score 85 → B
        g = self._grade(review_completion_rate=0.0, stale_theses=1)
        self.assertIn(g, ["B", "C", "D", "F"])  # not A

    def test_low_discipline_penalized(self):
        # 0.2 discipline = -8; add 2 overdue (-10) → score 82 → B
        g = self._grade(checklist_discipline_score=0.2, overdue_review_count=2)
        self.assertIn(g, ["B", "C", "D", "F"])

    def test_many_false_positives_drops_grade(self):
        # 4 false positives = -16 → score 84 → B
        self.assertEqual(self._grade(alpha_false_positive_count=4), "B")

    def test_extreme_penalties_give_f(self):
        g = self._grade(
            overdue_review_count=5,
            stale_theses=5,
            unreviewed_dry_runs=5,
            alpha_false_positive_count=5,
            review_completion_rate=0.0,
            risk_warnings_unresolved=5,
        )
        self.assertEqual(g, "F")

    def test_deterministic_same_inputs_same_grade(self):
        metrics = {
            "review_completion_rate": 0.5,
            "overdue_review_count": 2,
            "checklist_discipline_score": 0.8,
            "ignored_high_priority_workflow": 1,
            "unreviewed_dry_runs": 1,
            "stale_theses": 1,
            "alpha_false_positive_count": 1,
            "missed_winner_count": 1,
            "risk_warnings_unresolved": 1,
        }
        g1 = wr.compute_weekly_grade(metrics)
        g2 = wr.compute_weekly_grade(metrics)
        self.assertEqual(g1, g2)

    def test_score_clamped_to_zero(self):
        # Worst possible inputs — grade must be F (no crash, no negative)
        g = self._grade(
            overdue_review_count=100,
            stale_theses=100,
            unreviewed_dry_runs=100,
            alpha_false_positive_count=100,
            missed_winner_count=100,
            review_completion_rate=0.0,
            checklist_discipline_score=0.0,
            risk_warnings_unresolved=100,
            ignored_high_priority_workflow=100,
        )
        self.assertEqual(g, "F")

    def test_returns_string(self):
        g = self._grade()
        self.assertIsInstance(g, str)


# ════════════════════════════════════════════════════════════════════════════
# 4. Accountability metrics
# ════════════════════════════════════════════════════════════════════════════

class TestComputeAccountabilityMetrics(unittest.TestCase):

    def _data(self, **kwargs) -> dict:
        return {
            "workflow": {
                "completed_this_week": kwargs.get("wf_done", 0),
                "overdue_count":       kwargs.get("wf_overdue", 0),
                "open_count":          kwargs.get("wf_open", 0),
                "ignored_high":        kwargs.get("wf_ignored", []),
                "high_open_count":     0,
                "overdue_items":       [],
            },
            "checklists": {
                "created_this_week": kwargs.get("cl_created", 0),
                "approved_this_week": kwargs.get("cl_approved", 0),
            },
            "thesis": {
                "stale_count": kwargs.get("stale", 0),
            },
            "dryruns": {
                "still_active": kwargs.get("unreviewed", 0),
            },
            "outcomes": {
                "false_positive_count": kwargs.get("fp", 0),
                "missed_winner_count":  kwargs.get("missed", 0),
                "completed": [],
            },
            "risk_warnings_unresolved": kwargs.get("risk", 0),
        }

    def test_perfect_completion_rate(self):
        data = self._data(wf_done=5, wf_overdue=0, wf_open=0)
        m = wr.compute_accountability_metrics(data)
        self.assertEqual(m["review_completion_rate"], 1.0)

    def test_zero_completion_rate(self):
        data = self._data(wf_done=0, wf_overdue=2, wf_open=3)
        m = wr.compute_accountability_metrics(data)
        self.assertEqual(m["review_completion_rate"], 0.0)

    def test_partial_completion_rate(self):
        data = self._data(wf_done=3, wf_overdue=1, wf_open=2)
        m = wr.compute_accountability_metrics(data)
        self.assertAlmostEqual(m["review_completion_rate"], 0.5, places=2)

    def test_no_checklists_discipline_is_one(self):
        data = self._data(cl_created=0, cl_approved=0)
        m = wr.compute_accountability_metrics(data)
        self.assertEqual(m["checklist_discipline_score"], 1.0)

    def test_full_checklist_discipline(self):
        data = self._data(cl_created=4, cl_approved=4)
        m = wr.compute_accountability_metrics(data)
        self.assertEqual(m["checklist_discipline_score"], 1.0)

    def test_half_checklist_discipline(self):
        data = self._data(cl_created=4, cl_approved=2)
        m = wr.compute_accountability_metrics(data)
        self.assertAlmostEqual(m["checklist_discipline_score"], 0.5, places=2)

    def test_stale_theses_passed_through(self):
        data = self._data(stale=3)
        m = wr.compute_accountability_metrics(data)
        self.assertEqual(m["stale_theses"], 3)

    def test_all_metric_keys_present(self):
        data = self._data()
        m = wr.compute_accountability_metrics(data)
        for key in [
            "review_completion_rate", "overdue_review_count",
            "checklist_discipline_score", "ignored_high_priority_workflow",
            "unreviewed_dry_runs", "stale_theses",
            "alpha_false_positive_count", "missed_winner_count",
            "risk_warnings_unresolved",
        ]:
            self.assertIn(key, m, msg=f"Missing metric: {key}")


# ════════════════════════════════════════════════════════════════════════════
# 5. Section builders (pure functions)
# ════════════════════════════════════════════════════════════════════════════

class TestSectionBuilders(unittest.TestCase):

    def _empty_data(self) -> dict:
        return {
            "portfolio":  {"available": False},
            "alpha":      {"generated_count": 0, "generated_tickers": [], "tier_distribution": {}, "improved": []},
            "outcomes":   {"completed": [], "completed_count": 0, "false_positive_count": 0, "positive_count": 0, "missed_winners": [], "missed_winner_count": 0},
            "dryruns":    {"created_this_week": 0, "reviewed_this_week": 0, "dismissed_this_week": 0, "still_active": 0},
            "qc":         {"evaluated_this_week": 0, "suppressed_this_week": 0, "allowed_this_week": 0, "suppression_rate": 0.0},
            "delivery":   {"sent_this_week": 0, "by_urgency": {}},
            "checklists": {"created_this_week": 0, "approved_this_week": 0, "rejected_this_week": 0, "pending_count": 0},
            "workflow":   {"completed_this_week": 0, "overdue_count": 0, "open_count": 0, "high_open_count": 0, "overdue_items": [], "ignored_high": []},
            "thesis":     {"reviews_completed_this_week": 0, "overdue_count": 0, "stale_count": 0},
            "watchlist":  {"updated_this_week": 0, "archived_this_week": 0, "total_active": 0},
            "scorecards": {"computed_this_week": 0, "top_strategy": None},
            "stress":     {"runs_this_week": 0, "worst_loss_pct": None},
            "planner":    {"runs_this_week": 0, "last_urgency": "NONE", "drift_changed": False},
            "regime":     {"snapshots_this_week": 0, "opening_regime": "NEUTRAL", "closing_regime": "NEUTRAL", "regime_changed": False},
            "risk_warnings_unresolved": 0,
        }

    def test_build_sections_returns_all_required(self):
        sections = wr.build_weekly_sections(self._empty_data())
        for key in wr.REQUIRED_SECTIONS:
            self.assertIn(key, sections, msg=f"Section missing: {key}")

    def test_portfolio_section_unavailable(self):
        s = wr._section_portfolio_weekly_change(self._empty_data())
        self.assertFalse(s["available"])

    def test_portfolio_section_available(self):
        data = self._empty_data()
        data["portfolio"] = {
            "available": True, "start_value": 50000.0, "end_value": 51000.0,
            "change_cad": 1000.0, "change_pct": 2.0,
        }
        s = wr._section_portfolio_weekly_change(data)
        self.assertTrue(s["available"])
        self.assertEqual(s["change_cad"], 1000.0)

    def test_alpha_generated_section_zero(self):
        s = wr._section_alpha_generated(self._empty_data())
        self.assertEqual(s["count"], 0)

    def test_validation_outcomes_section_counts(self):
        data = self._empty_data()
        data["outcomes"]["completed"] = [
            {"ticker": "AAPL", "return_5d": 5.0, "status": "COMPLETE"},
            {"ticker": "MSFT", "return_5d": -2.0, "status": "COMPLETE"},
        ]
        data["outcomes"]["completed_count"] = 2
        data["outcomes"]["false_positive_count"] = 1
        data["outcomes"]["positive_count"] = 1
        s = wr._section_validation_outcomes(data)
        self.assertEqual(s["completed_count"], 2)
        self.assertEqual(s["false_positive_count"], 1)
        self.assertEqual(s["positive_count"], 1)

    def test_alpha_failed_from_negative_outcomes(self):
        data = self._empty_data()
        data["outcomes"]["completed"] = [
            {"ticker": "NVDA", "return_5d": -3.0, "return_10d": None},
        ]
        s = wr._section_alpha_failed(data)
        self.assertEqual(s["count"], 1)

    def test_key_mistakes_negative_outcome(self):
        data = self._empty_data()
        data["outcomes"]["completed"] = [
            {"ticker": "AMD", "return_5d": -5.0, "return_10d": None},
        ]
        mistakes = wr._section_key_mistakes(data)
        self.assertEqual(len(mistakes), 1)
        self.assertEqual(mistakes[0]["type"], "negative_outcome")

    def test_key_mistakes_ignored_high(self):
        data = self._empty_data()
        data["workflow"]["ignored_high"] = [
            {"ticker": "SHOP.TO", "reason": "Big opportunity missed"},
        ]
        mistakes = wr._section_key_mistakes(data)
        self.assertTrue(any(m["type"] == "ignored_high_priority" for m in mistakes))

    def test_best_decisions_positive_outcome(self):
        data = self._empty_data()
        data["outcomes"]["completed"] = [
            {"ticker": "NVDA", "return_5d": 8.0, "return_10d": None},
        ]
        decisions = wr._section_best_decisions(data)
        self.assertTrue(any(d["type"] == "positive_outcome" for d in decisions))

    def test_best_decisions_workflow_done(self):
        data = self._empty_data()
        data["workflow"]["completed_this_week"] = 5
        decisions = wr._section_best_decisions(data)
        self.assertTrue(any(d["type"] == "research_discipline" for d in decisions))

    def test_missed_opportunities_from_replay(self):
        data = self._empty_data()
        data["outcomes"]["missed_winners"] = [
            {"ticker": "TSM", "return_5d": 12.0},
        ]
        missed = wr._section_missed_opportunities(data)
        self.assertEqual(len(missed), 1)
        self.assertEqual(missed[0]["type"], "missed_winner")

    def test_focus_next_week_no_items(self):
        focus = wr._section_focus_next_week(self._empty_data())
        self.assertIsInstance(focus, list)
        self.assertGreater(len(focus), 0)  # fallback message

    def test_focus_next_week_overdue_items(self):
        data = self._empty_data()
        data["workflow"]["overdue_items"] = [{"ticker": "AAPL", "reason": "Review"}]
        focus = wr._section_focus_next_week(data)
        self.assertTrue(any("workflow" in f.lower() or "overdue" in f.lower() for f in focus))

    def test_workflow_summary_section(self):
        data = self._empty_data()
        data["workflow"]["completed_this_week"] = 3
        data["workflow"]["overdue_count"] = 1
        s = wr._section_workflow_summary(data)
        self.assertEqual(s["completed_this_week"], 3)
        self.assertEqual(s["overdue_count"], 1)

    def test_regime_section(self):
        data = self._empty_data()
        data["regime"] = {
            "snapshots_this_week": 5,
            "opening_regime": "NEUTRAL",
            "closing_regime": "RISK_OFF",
            "regime_changed": True,
        }
        s = wr._section_regime_changes(data)
        self.assertTrue(s["regime_changed"])
        self.assertEqual(s["closing_regime"], "RISK_OFF")


# ════════════════════════════════════════════════════════════════════════════
# 6. Format compact
# ════════════════════════════════════════════════════════════════════════════

class TestFormatCompact(unittest.TestCase):

    def _make_sections(self) -> dict:
        return {k: {} for k in wr.REQUIRED_SECTIONS}

    def _full_sections(self) -> dict:
        """A minimal but realistic sections dict."""
        return {
            "portfolio_weekly_change": {"available": False},
            "alpha_generated":        {"count": 5, "tickers": ["AAPL"], "tier_distribution": {}},
            "alpha_improved":         {"count": 1, "improved": []},
            "alpha_failed":           {"count": 0, "failed": []},
            "validation_outcomes":    {"completed_count": 2, "false_positive_count": 0, "positive_count": 2, "outcomes": []},
            "notification_activity":  {"created_this_week": 3, "reviewed_this_week": 2, "dismissed_this_week": 1, "still_active": 0},
            "qc_suppressions":        {"evaluated_this_week": 10, "suppressed_this_week": 3, "allowed_this_week": 7, "suppression_rate": 30.0},
            "delivery_attempts":      {"sent_this_week": 2, "by_urgency": {}},
            "checklist_discipline":   {"created_this_week": 1, "approved_this_week": 1, "rejected_this_week": 0, "pending_count": 0},
            "workflow_summary":       {"completed_this_week": 4, "overdue_count": 0, "open_count": 2, "high_open_count": 0, "overdue_items": []},
            "thesis_summary":         {"reviews_completed_this_week": 1, "overdue_count": 0, "stale_count": 0},
            "watchlist_changes":      {"updated_this_week": 2, "archived_this_week": 0, "total_active": 10},
            "scorecard_changes":      {"computed_this_week": 3, "top_strategy": None},
            "stress_test_changes":    {"runs_this_week": 1, "worst_loss_pct": -12.5},
            "planner_drift_changes":  {"runs_this_week": 1, "last_urgency": "LOW", "drift_changed": False},
            "regime_changes":         {"snapshots_this_week": 3, "opening_regime": "NEUTRAL", "closing_regime": "NEUTRAL", "regime_changed": False},
            "key_mistakes":           [],
            "best_decisions":         [{"type": "research_discipline", "ticker": None, "description": "Completed 4 items"}],
            "missed_opportunities":   [],
            "focus_next_week":        ["Maintain current cadence"],
        }

    def _metrics(self) -> dict:
        return {
            "review_completion_rate": 0.8,
            "overdue_review_count": 0,
            "checklist_discipline_score": 1.0,
            "ignored_high_priority_workflow": 0,
            "unreviewed_dry_runs": 0,
            "stale_theses": 0,
            "alpha_false_positive_count": 0,
            "missed_winner_count": 0,
            "risk_warnings_unresolved": 0,
        }

    def test_compact_within_max_chars(self):
        text = wr.format_compact_weekly(
            self._full_sections(), self._metrics(), "A", "2026-05-18"
        )
        self.assertLessEqual(len(text), wr.COMPACT_MAX_CHARS)

    def test_compact_returns_string(self):
        text = wr.format_compact_weekly(
            self._full_sections(), self._metrics(), "B", "2026-05-18"
        )
        self.assertIsInstance(text, str)

    def test_compact_contains_grade(self):
        text = wr.format_compact_weekly(
            self._full_sections(), self._metrics(), "B", "2026-05-18"
        )
        self.assertIn("B", text)

    def test_compact_no_banned_words(self):
        text = wr.format_compact_weekly(
            self._full_sections(), self._metrics(), "A", "2026-05-18"
        )
        found = wr.check_banned_words(text)
        self.assertEqual(found, [], msg=f"Banned words found: {found}")

    def test_compact_with_portfolio_data(self):
        sections = self._full_sections()
        sections["portfolio_weekly_change"] = {
            "available": True, "start_value": 50000.0, "end_value": 51000.0,
            "change_cad": 1000.0, "change_pct": 2.0,
        }
        text = wr.format_compact_weekly(sections, self._metrics(), "A", "2026-05-18")
        self.assertIn("PORTFOLIO", text)

    def test_format_detailed_has_required_keys(self):
        result = wr.format_detailed_weekly(
            self._full_sections(), self._metrics(), "A", "2026-05-18"
        )
        self.assertEqual(result["mode"], "detailed")
        self.assertIn("grade", result)
        self.assertIn("week_start", result)
        self.assertIn("accountability_metrics", result)
        for key in wr.REQUIRED_SECTIONS:
            self.assertIn(key, result, msg=f"Missing key: {key}")

    def test_format_debug_has_data_sources(self):
        data = {"portfolio": {}, "alpha": {}, "outcomes": {}, "dryruns": {},
                "qc": {}, "delivery": {}, "checklists": {}, "workflow": {},
                "thesis": {}, "regime": {}, "risk_warnings_unresolved": 0}
        result = wr.format_debug_weekly(
            self._full_sections(), self._metrics(), "A", data, "2026-05-18"
        )
        self.assertEqual(result["mode"], "debug")
        self.assertIn("data_sources", result)


# ════════════════════════════════════════════════════════════════════════════
# 7. Scheduler disabled by default
# ════════════════════════════════════════════════════════════════════════════

class TestSchedulerFlag(unittest.TestCase):

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WEEKLY_REVIEW_ENABLED", None)
            self.assertFalse(wr.weekly_review_enabled())

    def test_enabled_when_set_true(self):
        with patch.dict(os.environ, {"WEEKLY_REVIEW_ENABLED": "true"}):
            self.assertTrue(wr.weekly_review_enabled())

    def test_disabled_when_set_false(self):
        with patch.dict(os.environ, {"WEEKLY_REVIEW_ENABLED": "false"}):
            self.assertFalse(wr.weekly_review_enabled())

    def test_case_insensitive(self):
        with patch.dict(os.environ, {"WEEKLY_REVIEW_ENABLED": "TRUE"}):
            self.assertTrue(wr.weekly_review_enabled())


# ════════════════════════════════════════════════════════════════════════════
# 8. Duplicate-send suppression
# ════════════════════════════════════════════════════════════════════════════

class TestDuplicateSend(unittest.TestCase):

    def setUp(self):
        _, self.conn_fn = _make_db()
        self._db_patch = _patch_db(self.conn_fn)
        self._db_patch.start()

    def tearDown(self):
        self._db_patch.stop()

    def test_not_already_sent(self):
        self.assertFalse(wr._already_sent_this_week("2026-05-18"))

    def test_mark_sent_and_check(self):
        wr._mark_sent("2026-05-18", "A")
        self.assertTrue(wr._already_sent_this_week("2026-05-18"))

    def test_different_week_not_sent(self):
        wr._mark_sent("2026-05-18", "B")
        self.assertFalse(wr._already_sent_this_week("2026-05-25"))

    def test_mark_sent_twice_does_not_error(self):
        wr._mark_sent("2026-05-18", "A")
        wr._mark_sent("2026-05-18", "B")  # INSERT OR REPLACE
        self.assertTrue(wr._already_sent_this_week("2026-05-18"))

    def test_get_review_history_empty(self):
        history = wr.get_review_history()
        self.assertEqual(history, [])

    def test_get_review_history_returns_entries(self):
        wr._mark_sent("2026-05-18", "A")
        wr._mark_sent("2026-05-11", "B")
        history = wr.get_review_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["week_start"], "2026-05-18")  # newest first

    def test_history_includes_grade(self):
        wr._mark_sent("2026-05-18", "C")
        history = wr.get_review_history()
        self.assertEqual(history[0]["grade"], "C")


# ════════════════════════════════════════════════════════════════════════════
# 9. Sparse-data safety
# ════════════════════════════════════════════════════════════════════════════

class TestSparseDataSafe(unittest.TestCase):

    def setUp(self):
        _, self.conn_fn = _make_db()
        self._db_patch = _patch_db(self.conn_fn)
        self._db_patch.start()

    def tearDown(self):
        self._db_patch.stop()

    def _run_collector(self, fn, *args):
        """Call collector with empty DB — must not raise."""
        try:
            result = fn(*args)
            self.assertIsInstance(result, dict)
        except Exception as exc:
            self.fail(f"{fn.__name__} raised {exc}")

    def test_portfolio_sparse(self):
        self._run_collector(wr._collect_portfolio_change, "2026-05-18", "2026-05-25")

    def test_alpha_sparse(self):
        self._run_collector(wr._collect_alpha_activity, "2026-05-18", "2026-05-25")

    def test_dryruns_sparse(self):
        self._run_collector(wr._collect_dryruns, "2026-05-18", "2026-05-25")

    def test_qc_sparse(self):
        self._run_collector(wr._collect_qc, "2026-05-18", "2026-05-25")

    def test_delivery_sparse(self):
        self._run_collector(wr._collect_delivery, "2026-05-18", "2026-05-25")

    def test_checklists_sparse(self):
        self._run_collector(wr._collect_checklists, "2026-05-18", "2026-05-25")

    def test_workflow_sparse(self):
        self._run_collector(wr._collect_workflow, "2026-05-18", "2026-05-25")

    def test_thesis_sparse(self):
        self._run_collector(wr._collect_thesis, "2026-05-18", "2026-05-25")

    def test_watchlist_sparse(self):
        self._run_collector(wr._collect_watchlist, "2026-05-18", "2026-05-25")

    def test_regime_sparse(self):
        self._run_collector(wr._collect_regime, "2026-05-18", "2026-05-25")

    def test_collect_weekly_data_sparse(self):
        """Full collection must not raise even with empty DB."""
        try:
            data = wr.collect_weekly_data("2026-05-18", "2026-05-25")
            self.assertIsInstance(data, dict)
        except Exception as exc:
            self.fail(f"collect_weekly_data raised {exc}")

    def test_generate_weekly_review_sparse(self):
        """Full pipeline must not raise with empty DB."""
        try:
            result = wr.generate_weekly_review(mode="detailed", week_start_str="2026-05-18")
            self.assertIsInstance(result, dict)
        except Exception as exc:
            self.fail(f"generate_weekly_review raised {exc}")

    def test_generate_compact_weekly_never_raises(self):
        try:
            text = wr.generate_compact_weekly()
            self.assertIsInstance(text, str)
        except Exception as exc:
            self.fail(f"generate_compact_weekly raised {exc}")


# ════════════════════════════════════════════════════════════════════════════
# 10. No trading calls in source
# ════════════════════════════════════════════════════════════════════════════

class TestNoTradingCalls(unittest.TestCase):

    def setUp(self):
        self.source = Path(wr.__file__).read_text()

    def test_no_place_order(self):
        self.assertNotIn("place_order", self.source)

    def test_no_execute_trade(self):
        self.assertNotIn("execute_trade", self.source)

    def test_no_broker_module(self):
        self.assertNotIn("import broker", self.source)

    def test_no_send_order(self):
        self.assertNotIn("send_order", self.source)

    def test_no_auto_buy(self):
        self.assertNotIn("auto_buy", self.source)


# ════════════════════════════════════════════════════════════════════════════
# 11. Deterministic output
# ════════════════════════════════════════════════════════════════════════════

class TestDeterministicOutput(unittest.TestCase):

    def test_same_metrics_same_grade_always(self):
        metrics = {
            "review_completion_rate": 0.6,
            "overdue_review_count": 2,
            "checklist_discipline_score": 0.75,
            "ignored_high_priority_workflow": 1,
            "unreviewed_dry_runs": 0,
            "stale_theses": 1,
            "alpha_false_positive_count": 0,
            "missed_winner_count": 0,
            "risk_warnings_unresolved": 1,
        }
        grades = {wr.compute_weekly_grade(metrics) for _ in range(10)}
        self.assertEqual(len(grades), 1)

    def test_week_start_always_monday(self):
        # Any day in a given week should return same Monday
        days = ["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21",
                "2026-05-22", "2026-05-23", "2026-05-24"]
        mondays = {wr._parse_week_start(d) for d in days}
        self.assertEqual(mondays, {"2026-05-18"})


# ════════════════════════════════════════════════════════════════════════════
# 12. API endpoints
# ════════════════════════════════════════════════════════════════════════════

class TestApiWeeklyReview(unittest.TestCase):

    def setUp(self):
        self.app, self.api_mod, self.db_path, self.conn_fn = _make_app()
        self.client = self.app.test_client()

    def tearDown(self):
        self.api_mod.cache_clear()

    def test_get_weekly_review_200(self):
        with patch("weekly_review.collect_weekly_data") as mock_collect:
            mock_collect.return_value = {
                "week_start": "2026-05-18", "week_end": "2026-05-25",
                "portfolio": {"available": False},
                "alpha": {"generated_count": 0, "generated_tickers": [], "tier_distribution": {}, "improved": []},
                "outcomes": {"completed": [], "completed_count": 0, "false_positive_count": 0, "positive_count": 0, "missed_winners": [], "missed_winner_count": 0},
                "dryruns": {"created_this_week": 0, "reviewed_this_week": 0, "dismissed_this_week": 0, "still_active": 0},
                "qc": {"evaluated_this_week": 0, "suppressed_this_week": 0, "allowed_this_week": 0, "suppression_rate": 0.0},
                "delivery": {"sent_this_week": 0, "by_urgency": {}},
                "checklists": {"created_this_week": 0, "approved_this_week": 0, "rejected_this_week": 0, "pending_count": 0},
                "workflow": {"completed_this_week": 0, "overdue_count": 0, "open_count": 0, "high_open_count": 0, "overdue_items": [], "ignored_high": []},
                "thesis": {"reviews_completed_this_week": 0, "overdue_count": 0, "stale_count": 0},
                "watchlist": {"updated_this_week": 0, "archived_this_week": 0, "total_active": 0},
                "scorecards": {"computed_this_week": 0, "top_strategy": None},
                "stress": {"runs_this_week": 0, "worst_loss_pct": None},
                "planner": {"runs_this_week": 0, "last_urgency": "NONE", "drift_changed": False},
                "regime": {"snapshots_this_week": 0, "opening_regime": "NEUTRAL", "closing_regime": "NEUTRAL", "regime_changed": False},
                "risk_warnings_unresolved": 0,
            }
            resp = self.client.get("/api/v1/review/weekly")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data["ok"])

    def test_get_weekly_review_week_start_param(self):
        with patch("weekly_review.collect_weekly_data") as mock_collect:
            mock_collect.return_value = {
                "week_start": "2026-05-11", "week_end": "2026-05-18",
                "portfolio": {"available": False},
                "alpha": {"generated_count": 0, "generated_tickers": [], "tier_distribution": {}, "improved": []},
                "outcomes": {"completed": [], "completed_count": 0, "false_positive_count": 0, "positive_count": 0, "missed_winners": [], "missed_winner_count": 0},
                "dryruns": {"created_this_week": 0, "reviewed_this_week": 0, "dismissed_this_week": 0, "still_active": 0},
                "qc": {"evaluated_this_week": 0, "suppressed_this_week": 0, "allowed_this_week": 0, "suppression_rate": 0.0},
                "delivery": {"sent_this_week": 0, "by_urgency": {}},
                "checklists": {"created_this_week": 0, "approved_this_week": 0, "rejected_this_week": 0, "pending_count": 0},
                "workflow": {"completed_this_week": 0, "overdue_count": 0, "open_count": 0, "high_open_count": 0, "overdue_items": [], "ignored_high": []},
                "thesis": {"reviews_completed_this_week": 0, "overdue_count": 0, "stale_count": 0},
                "watchlist": {"updated_this_week": 0, "archived_this_week": 0, "total_active": 0},
                "scorecards": {"computed_this_week": 0, "top_strategy": None},
                "stress": {"runs_this_week": 0, "worst_loss_pct": None},
                "planner": {"runs_this_week": 0, "last_urgency": "NONE", "drift_changed": False},
                "regime": {"snapshots_this_week": 0, "opening_regime": "NEUTRAL", "closing_regime": "NEUTRAL", "regime_changed": False},
                "risk_warnings_unresolved": 0,
            }
            resp = self.client.get("/api/v1/review/weekly?week_start=2026-05-11")
            self.assertEqual(resp.status_code, 200)

    def test_get_weekly_review_compact_mode(self):
        with patch("weekly_review.collect_weekly_data") as mock_collect:
            mock_collect.return_value = {
                "week_start": "2026-05-18", "week_end": "2026-05-25",
                "portfolio": {"available": False},
                "alpha": {"generated_count": 0, "generated_tickers": [], "tier_distribution": {}, "improved": []},
                "outcomes": {"completed": [], "completed_count": 0, "false_positive_count": 0, "positive_count": 0, "missed_winners": [], "missed_winner_count": 0},
                "dryruns": {"created_this_week": 0, "reviewed_this_week": 0, "dismissed_this_week": 0, "still_active": 0},
                "qc": {"evaluated_this_week": 0, "suppressed_this_week": 0, "allowed_this_week": 0, "suppression_rate": 0.0},
                "delivery": {"sent_this_week": 0, "by_urgency": {}},
                "checklists": {"created_this_week": 0, "approved_this_week": 0, "rejected_this_week": 0, "pending_count": 0},
                "workflow": {"completed_this_week": 0, "overdue_count": 0, "open_count": 0, "high_open_count": 0, "overdue_items": [], "ignored_high": []},
                "thesis": {"reviews_completed_this_week": 0, "overdue_count": 0, "stale_count": 0},
                "watchlist": {"updated_this_week": 0, "archived_this_week": 0, "total_active": 0},
                "scorecards": {"computed_this_week": 0, "top_strategy": None},
                "stress": {"runs_this_week": 0, "worst_loss_pct": None},
                "planner": {"runs_this_week": 0, "last_urgency": "NONE", "drift_changed": False},
                "regime": {"snapshots_this_week": 0, "opening_regime": "NEUTRAL", "closing_regime": "NEUTRAL", "regime_changed": False},
                "risk_warnings_unresolved": 0,
            }
            resp = self.client.get("/api/v1/review/weekly?mode=compact")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data["ok"])
            # compact mode wraps text in {mode, text}
            self.assertIn("text", data["data"])


class TestApiWeeklyHistory(unittest.TestCase):

    def setUp(self):
        self.app, self.api_mod, self.db_path, self.conn_fn = _make_app()
        self.client = self.app.test_client()
        # Patch DB so weekly_review uses the test DB
        import database
        self._db_patch = patch.object(database, "get_connection", self.conn_fn)
        self._db_patch.start()

    def tearDown(self):
        self._db_patch.stop()
        self.api_mod.cache_clear()

    def test_history_empty(self):
        resp = self.client.get("/api/v1/review/weekly/history")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["data"]["count"], 0)

    def test_history_with_entries(self):
        wr._mark_sent("2026-05-18", "A")
        self.api_mod.cache_clear()
        resp = self.client.get("/api/v1/review/weekly/history")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["data"]["count"], 1)
        self.assertEqual(data["data"]["history"][0]["grade"], "A")


# ════════════════════════════════════════════════════════════════════════════
# 13. Banned-word filter
# ════════════════════════════════════════════════════════════════════════════

class TestBannedWords(unittest.TestCase):

    def test_check_banned_words_finds_hype(self):
        found = wr.check_banned_words("This stock is going to the moon!")
        self.assertIn("to the moon", found)

    def test_check_banned_words_clean_text(self):
        found = wr.check_banned_words("Portfolio review completed this week.")
        self.assertEqual(found, [])

    def test_compact_output_clean_by_design(self):
        sections = {k: {} for k in wr.REQUIRED_SECTIONS}
        sections["portfolio_weekly_change"] = {"available": False}
        sections["workflow_summary"] = {"completed_this_week": 0, "overdue_count": 0,
                                        "open_count": 0, "high_open_count": 0, "overdue_items": []}
        sections["regime_changes"] = {"snapshots_this_week": 0, "opening_regime": "NEUTRAL",
                                      "closing_regime": "NEUTRAL", "regime_changed": False}
        sections["alpha_generated"] = {"count": 0, "tickers": [], "tier_distribution": {}}
        sections["validation_outcomes"] = {"completed_count": 0, "false_positive_count": 0,
                                           "positive_count": 0, "outcomes": []}
        sections["focus_next_week"] = ["Maintain current cadence"]
        # Fill remaining
        for k in wr.REQUIRED_SECTIONS:
            if k not in sections:
                sections[k] = []

        metrics = {k: 0 for k in [
            "review_completion_rate", "overdue_review_count",
            "checklist_discipline_score", "ignored_high_priority_workflow",
            "unreviewed_dry_runs", "stale_theses",
            "alpha_false_positive_count", "missed_winner_count",
            "risk_warnings_unresolved",
        ]}
        metrics["review_completion_rate"] = 1.0
        metrics["checklist_discipline_score"] = 1.0

        text = wr.format_compact_weekly(sections, metrics, "A", "2026-05-18")
        found = wr.check_banned_words(text)
        self.assertEqual(found, [], msg=f"Banned words in compact output: {found}")


if __name__ == "__main__":
    unittest.main()
