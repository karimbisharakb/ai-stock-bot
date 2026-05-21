"""
Tests for Phase A22: End-of-Day Review Brief (bot/eod_brief.py).
"""
import importlib
import json

import pytest

import database
import eod_brief as eb


# ── Fixtures / helpers ─────────────────────────────────────────────────────────

def _portfolio(n=2, total=50000.0, cash=5000.0):
    return {
        "positions": [
            {
                "ticker":             f"T{i}",
                "quantity":           10.0,
                "avg_cost":           100.0,
                "market_value":       1100.0,
                "unrealized_pnl":     100.0,
                "unrealized_pnl_pct": 10.0,
            }
            for i in range(n)
        ],
        "aggregates": {
            "total_portfolio_value": total,
            "cash":                  cash,
        },
    }


def _alpha_row(ticker="NVDA", score=85.0, tier="TIER_A", setup="BREAKOUT_EXPANSION", rtier="ALERT_READY"):
    return {"ticker": ticker, "alpha_score": score, "alpha_tier": tier,
            "setup_type": setup, "readiness_tier": rtier}


def _dryrun(ticker="AAPL", status="DRY_RUN"):
    return {"ticker": ticker, "status": status, "readiness_tier": "PRE_ALERT", "alpha_score": 72.0}


def _qc_row(allow=True):
    return {"ticker": "NVDA", "allow_notification": allow,
            "suppression_reason": None, "qc_score": 80.0}


def _regime_snap(regime="NEUTRAL", score=55.0, captured_at="2026-05-20T10:00:00"):
    return {"overall_regime": regime, "regime_score": score, "captured_at": captured_at}


def _checklist(ticker="NVDA", status="DRAFT"):
    return {"ticker": ticker, "decision_type": "ENTRY", "checklist_status": status,
            "checklist_completion": 0.4}


def _journal_entry(ticker="AAPL", entry_type="NOTE"):
    return {"ticker": ticker, "entry_type": entry_type, "created_at": "2026-05-20T12:00:00"}


def _proposal(kind="WEIGHT_CHANGE", status="PROPOSED"):
    return {"proposal_id": "P1", "status": status, "kind": kind}


def _outcome(ticker="NVDA", return_10d=5.0):
    return {"ticker": ticker, "alpha_tier": "TIER_A", "return_10d": return_10d, "return_5d": 3.0}


def _validation(ticker="NVDA", bc="STRONG_BULLISH"):
    return {"ticker": ticker, "behavior_class": bc, "validation_score": 80.0}


def _planner_snap(urgency="MEDIUM"):
    return {"snapshot_id": "PLN-123", "rebalance_urgency": urgency,
            "created_at": "2026-05-20T14:00:00"}


def _stress_run(scenario="MARKET_CRASH_20", pct=-22.0):
    return {"run_id": "STR-123", "created_at": "2026-05-20T09:00:00",
            "worst_scenario": scenario, "worst_loss_pct": pct}


def _pending_cl(ticker="NVDA"):
    return {"ticker": ticker, "checklist_status": "DRAFT", "decision_type": "ENTRY",
            "readiness": "NOT_READY", "blocking_items": 2}


def _due_review(ticker="AAPL"):
    return {"ticker": ticker, "next_review_at": "2026-01-01T00:00:00"}


def _full_data():
    return {
        "today_start":           "2026-05-20T00:00:00",
        "portfolio":             _portfolio(),
        "transactions_today":    [{"ticker": "NVDA", "type": "BUY", "shares": 5.0, "price_cad": 900.0}],
        "alpha_today":           [_alpha_row("NVDA"), _alpha_row("AAPL", 70.0, "TIER_B", rtier="PRE_ALERT"), _alpha_row("MSFT", 60.0, rtier="NOT_READY")],
        "top_candidates":        [_alpha_row("NVDA"), _alpha_row("AAPL", 70.0)],
        "dryruns_today":         [_dryrun("NVDA"), _dryrun("AAPL", "REVIEWED")],
        "qc_today":              [_qc_row(True), _qc_row(False), _qc_row(False)],
        "regime_snapshots_today": [_regime_snap("NEUTRAL", 55.0, "T1"), _regime_snap("RISK_ON", 70.0, "T2")],
        "stress_runs_today":     [_stress_run()],
        "checklists_today":      [_checklist("NVDA"), _checklist("AAPL")],
        "journal_today":         [_journal_entry("AAPL"), _journal_entry("MSFT", "CATALYST")],
        "proposals_today":       [_proposal()],
        "outcomes_today":        [_outcome("NVDA", 5.0), _outcome("AAPL", -2.0)],
        "validations_today":     [_validation("NVDA"), _validation("AAPL", "MODERATE_BULLISH")],
        "planner_today":         [_planner_snap()],
        "pending_checklists":    [_pending_cl("NVDA")],
        "due_reviews":           {"overdue": [_due_review("AAPL")], "due": [], "upcoming": []},
        "unreviewed_dryruns":    [_dryrun("TSLA")],
    }


def _empty_data():
    return {
        "today_start": "2026-05-20T00:00:00",
        "portfolio": {}, "transactions_today": [], "alpha_today": [],
        "top_candidates": [], "dryruns_today": [], "qc_today": [],
        "regime_snapshots_today": [], "stress_runs_today": [],
        "checklists_today": [], "journal_today": [], "proposals_today": [],
        "outcomes_today": [], "validations_today": [], "planner_today": [],
        "pending_checklists": [], "due_reviews": {"overdue": [], "due": [], "upcoming": []},
        "unreviewed_dryruns": [],
    }


# ── Constants ──────────────────────────────────────────────────────────────────

class TestConstants:
    def test_modes(self):
        assert eb.MODES == ["compact", "detailed", "debug"]

    def test_required_sections_count(self):
        assert len(eb.REQUIRED_SECTIONS) == 15

    def test_required_sections_contains_all(self):
        for key in [
            "portfolio_change_summary", "alpha_candidates_today",
            "alert_readiness_changes", "dryrun_activity_today",
            "qc_suppressions_today", "regime_changes_today",
            "stress_risk_changes_today", "checklists_updated_today",
            "thesis_updates_today", "learning_changes_today",
            "outcomes_completed_today", "planner_updates_today",
            "tomorrow_watchlist", "unresolved_actions",
        ]:
            assert key in eb.REQUIRED_SECTIONS

    def test_banned_words_non_empty(self):
        assert len(eb.BANNED_WORDS) >= 5

    def test_banned_words_has_moon(self):
        assert "moon" in eb.BANNED_WORDS

    def test_compact_max_chars(self):
        assert eb.COMPACT_MAX_CHARS == 1600

    def test_max_limits(self):
        assert eb.MAX_ALPHA_CHANGES  == 3
        assert eb.MAX_RISK_CHANGES   == 3
        assert eb.MAX_UNRESOLVED     == 3
        assert eb.MAX_WATCHLIST      == 3
        assert eb.MAX_LEARNING_NOTES == 3


# ── Feature flag ───────────────────────────────────────────────────────────────

class TestFeatureFlag:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("EOD_BRIEF_ENABLED", raising=False)
        assert eb.eod_enabled() is False

    def test_enabled_when_set_true(self, monkeypatch):
        monkeypatch.setenv("EOD_BRIEF_ENABLED", "true")
        assert eb.eod_enabled() is True

    def test_case_insensitive_true(self, monkeypatch):
        monkeypatch.setenv("EOD_BRIEF_ENABLED", "TRUE")
        assert eb.eod_enabled() is True

    def test_false_string(self, monkeypatch):
        monkeypatch.setenv("EOD_BRIEF_ENABLED", "false")
        assert eb.eod_enabled() is False

    def test_empty_string(self, monkeypatch):
        monkeypatch.setenv("EOD_BRIEF_ENABLED", "")
        assert eb.eod_enabled() is False


# ── Utility helpers ────────────────────────────────────────────────────────────

class TestHelpers:
    def test_check_banned_words_empty(self):
        assert eb.check_banned_words("steady upward trend") == []

    def test_check_banned_words_moon(self):
        result = eb.check_banned_words("going to the moon")
        assert "moon" in result

    def test_check_banned_words_case_insensitive(self):
        assert "explode" in eb.check_banned_words("EXPLODE higher")

    def test_safe_truncate_short(self):
        assert eb._safe_truncate("hi", 10) == "hi"

    def test_safe_truncate_long(self):
        assert eb._safe_truncate("hello", 3) == "hel…"

    def test_safe_truncate_empty(self):
        assert eb._safe_truncate("", 10) == ""

    def test_sign_positive(self):
        assert eb._sign(1.0) == "+"

    def test_sign_negative(self):
        assert eb._sign(-1.0) == ""

    def test_today_start_format(self):
        ts = eb._today_start()
        assert "T00:00:00" in ts


# ── Section builders ───────────────────────────────────────────────────────────

class TestPortfolioChangeSection:
    def test_empty_portfolio(self):
        r = eb._portfolio_change_section({}, [])
        assert r["position_count"] == 0
        assert r["transactions_today"] == 0

    def test_with_positions(self):
        r = eb._portfolio_change_section(_portfolio(n=2, total=20000.0, cash=2000.0), [])
        assert r["position_count"] == 2
        assert r["total_value"] == 20000.0

    def test_transactions_counted(self):
        txns = [{"ticker": "NVDA", "type": "BUY", "shares": 5.0, "price_cad": 900.0}]
        r = eb._portfolio_change_section({}, txns)
        assert r["transactions_today"] == 1
        assert len(r["transactions"]) == 1

    def test_pnl_pct_zero_when_no_cost(self):
        p = {"positions": [{"ticker": "X", "quantity": 0.0, "avg_cost": 0.0,
                            "market_value": 0.0, "unrealized_pnl": 0.0}],
             "aggregates": {"total_portfolio_value": 0.0, "cash": 0.0}}
        r = eb._portfolio_change_section(p, [])
        assert r["unrealized_pnl_pct"] == 0.0


class TestAlphaCandidatesSection:
    def test_empty(self):
        r = eb._alpha_candidates_section([])
        assert r["scored_today"] == 0
        assert r["top_candidates"] == []

    def test_count(self):
        rows = [_alpha_row(f"T{i}", 80.0 - i) for i in range(5)]
        r = eb._alpha_candidates_section(rows)
        assert r["scored_today"] == 5

    def test_top_candidates_capped(self):
        rows = [_alpha_row(f"T{i}", 80.0 - i) for i in range(10)]
        r = eb._alpha_candidates_section(rows)
        assert len(r["top_candidates"]) <= eb.MAX_ALPHA_CHANGES

    def test_tier_distribution(self):
        rows = [_alpha_row("A", 90.0, "TIER_A"), _alpha_row("B", 70.0, "TIER_B")]
        r = eb._alpha_candidates_section(rows)
        assert r["tier_distribution"].get("TIER_A") == 1
        assert r["tier_distribution"].get("TIER_B") == 1

    def test_sorted_by_score_desc(self):
        rows = [_alpha_row("LOW", 50.0), _alpha_row("HIGH", 90.0)]
        r = eb._alpha_candidates_section(rows)
        assert r["top_candidates"][0]["ticker"] == "HIGH"


class TestAlertReadinessSection:
    def test_empty(self):
        r = eb._alert_readiness_section([])
        assert r["alert_eligible_count"] == 0
        assert r["alert_ready_count"] == 0

    def test_counts_eligible_tiers(self):
        rows = [
            _alpha_row("A", rtier="ALERT_READY"),
            _alpha_row("B", rtier="PRE_ALERT"),
            _alpha_row("C", rtier="NOT_READY"),
        ]
        r = eb._alert_readiness_section(rows)
        assert r["alert_eligible_count"] == 2
        assert r["alert_ready_count"] == 1

    def test_alert_ready_tickers_capped(self):
        rows = [_alpha_row(f"T{i}", rtier="ALERT_READY") for i in range(10)]
        r = eb._alert_readiness_section(rows)
        assert len(r["alert_ready_tickers"]) <= eb.MAX_ALPHA_CHANGES


class TestDryrunActivitySection:
    def test_empty(self):
        r = eb._dryrun_activity_section([])
        assert r["created_today"] == 0
        assert r["reviewed_today"] == 0

    def test_status_counts(self):
        drs = [_dryrun("A", "DRY_RUN"), _dryrun("B", "REVIEWED"), _dryrun("C", "DISMISSED")]
        r = eb._dryrun_activity_section(drs)
        assert r["created_today"] == 3
        assert r["reviewed_today"] == 1
        assert r["dismissed_today"] == 1
        assert r["still_active"] == 1

    def test_top_tickers_capped(self):
        drs = [_dryrun(f"T{i}") for i in range(10)]
        r = eb._dryrun_activity_section(drs)
        assert len(r["top_tickers"]) <= eb.MAX_OVERNIGHT_DRS


class TestQcSuppressionsSection:
    def test_empty(self):
        r = eb._qc_suppressions_section([])
        assert r["evaluated_today"] == 0
        assert r["suppression_rate"] == 0.0

    def test_counts(self):
        rows = [_qc_row(True), _qc_row(False), _qc_row(False)]
        r = eb._qc_suppressions_section(rows)
        assert r["evaluated_today"] == 3
        assert r["allowed_today"] == 1
        assert r["suppressed_today"] == 2

    def test_suppression_rate_calculation(self):
        rows = [_qc_row(True), _qc_row(False)]
        r = eb._qc_suppressions_section(rows)
        assert r["suppression_rate"] == 50.0


class TestRegimeChangesSection:
    def test_empty(self):
        r = eb._regime_changes_section([])
        assert r["snapshots_today"] == 0
        assert r["regime_changed"] is False

    def test_single_snapshot(self):
        r = eb._regime_changes_section([_regime_snap("NEUTRAL", 55.0)])
        assert r["snapshots_today"] == 1
        assert r["closing_regime"] == "NEUTRAL"
        assert r["regime_changed"] is False

    def test_regime_change_detected(self):
        snaps = [_regime_snap("NEUTRAL", 55.0, "T1"), _regime_snap("RISK_ON", 70.0, "T2")]
        r = eb._regime_changes_section(snaps)
        assert r["regime_changed"] is True
        assert r["opening_regime"] == "NEUTRAL"
        assert r["closing_regime"] == "RISK_ON"

    def test_no_change_same_regime(self):
        snaps = [_regime_snap("NEUTRAL", 50.0, "T1"), _regime_snap("NEUTRAL", 55.0, "T2")]
        r = eb._regime_changes_section(snaps)
        assert r["regime_changed"] is False


class TestStressRiskSection:
    def test_empty(self):
        r = eb._stress_risk_section([])
        assert r.get("runs_today", 0) == 0

    def test_with_run(self):
        r = eb._stress_risk_section([_stress_run("MARKET_CRASH_20", -22.0)])
        assert r["runs_today"] == 1
        assert r["worst_loss_pct"] == -22.0
        assert r["worst_scenario"] == "MARKET_CRASH_20"

    def test_picks_worst_pct(self):
        runs = [_stress_run("A", -10.0), _stress_run("B", -25.0)]
        r = eb._stress_risk_section(runs)
        assert r["worst_loss_pct"] == -25.0


class TestChecklistsSection:
    def test_empty(self):
        r = eb._checklists_section([])
        assert r["created_today"] == 0

    def test_count_and_tickers(self):
        r = eb._checklists_section([_checklist("NVDA"), _checklist("AAPL")])
        assert r["created_today"] == 2
        assert "NVDA" in r["tickers"]

    def test_by_status(self):
        r = eb._checklists_section([_checklist("A", "DRAFT"), _checklist("B", "READY")])
        assert r["by_status"].get("DRAFT") == 1
        assert r["by_status"].get("READY") == 1

    def test_tickers_capped(self):
        r = eb._checklists_section([_checklist(f"T{i}") for i in range(10)])
        assert len(r["tickers"]) <= eb.MAX_ALPHA_CHANGES


class TestThesisUpdatesSection:
    def test_empty(self):
        r = eb._thesis_updates_section([])
        assert r["entries_today"] == 0

    def test_counts_entries(self):
        r = eb._thesis_updates_section([_journal_entry("A", "NOTE"), _journal_entry("B", "CATALYST")])
        assert r["entries_today"] == 2

    def test_groups_by_type(self):
        r = eb._thesis_updates_section([_journal_entry("A", "NOTE"), _journal_entry("B", "NOTE")])
        assert r["by_type"].get("NOTE") == 2

    def test_unique_tickers(self):
        entries = [_journal_entry("AAPL"), _journal_entry("AAPL"), _journal_entry("MSFT")]
        r = eb._thesis_updates_section(entries)
        assert len(set(r["tickers"])) == len(r["tickers"])

    def test_tickers_capped(self):
        entries = [_journal_entry(f"T{i}") for i in range(10)]
        r = eb._thesis_updates_section(entries)
        assert len(r["tickers"]) <= eb.MAX_ALPHA_CHANGES


class TestLearningChangesSection:
    def test_empty(self):
        r = eb._learning_changes_section([])
        assert r["proposals_today"] == 0

    def test_count(self):
        r = eb._learning_changes_section([_proposal(), _proposal("THRESHOLD_CHANGE")])
        assert r["proposals_today"] == 2

    def test_by_status(self):
        r = eb._learning_changes_section([_proposal(status="PROPOSED"), _proposal(status="APPROVED")])
        assert r["by_status"].get("PROPOSED") == 1
        assert r["by_status"].get("APPROVED") == 1

    def test_kinds_capped(self):
        props = [_proposal(f"K{i}") for i in range(10)]
        r = eb._learning_changes_section(props)
        assert len(r.get("kinds", [])) <= eb.MAX_LEARNING_NOTES


class TestOutcomesSection:
    def test_empty(self):
        r = eb._outcomes_section([], [])
        assert r["completed_today"] == 0
        assert r["validations_today"] == 0

    def test_counts(self):
        r = eb._outcomes_section([_outcome(), _outcome("AAPL", -2.0)], [_validation()])
        assert r["completed_today"] == 2
        assert r["validations_today"] == 1

    def test_behavior_summary(self):
        vals = [_validation("A", "STRONG_BULLISH"), _validation("B", "FAILED_SQUEEZE")]
        r = eb._outcomes_section([], vals)
        assert r["behavior_summary"].get("STRONG_BULLISH") == 1
        assert r["behavior_summary"].get("FAILED_SQUEEZE") == 1

    def test_tickers_capped(self):
        outcomes = [_outcome(f"T{i}") for i in range(10)]
        r = eb._outcomes_section(outcomes, [])
        assert len(r["tickers"]) <= eb.MAX_LEARNING_NOTES


class TestPlannerUpdatesSection:
    def test_empty(self):
        r = eb._planner_updates_section([])
        assert r.get("runs_today", 0) == 0

    def test_with_run(self):
        r = eb._planner_updates_section([_planner_snap("HIGH")])
        assert r["runs_today"] == 1
        assert r["last_urgency"] == "HIGH"

    def test_last_snapshot(self):
        snaps = [_planner_snap("NONE"), _planner_snap("MEDIUM")]
        r = eb._planner_updates_section(snaps)
        assert r["last_urgency"] == "MEDIUM"


class TestTomorrowWatchlistSection:
    def test_empty(self):
        assert eb._tomorrow_watchlist_section([]) == []

    def test_cap_at_max(self):
        cands = [_alpha_row(f"T{i}") for i in range(10)]
        r = eb._tomorrow_watchlist_section(cands)
        assert len(r) <= eb.MAX_WATCHLIST

    def test_prioritises_alert_ready(self):
        cands = [
            _alpha_row("LOW", 30.0, rtier="NOT_READY"),
            _alpha_row("HIGH", 90.0, rtier="ALERT_READY"),
        ]
        r = eb._tomorrow_watchlist_section(cands)
        assert r[0]["ticker"] == "HIGH"

    def test_required_fields(self):
        r = eb._tomorrow_watchlist_section([_alpha_row()])
        assert len(r) == 1
        assert "ticker" in r[0]
        assert "alpha_score" in r[0]
        assert "readiness_tier" in r[0]


class TestUnresolvedActionsSection:
    def test_empty(self):
        r = eb._unresolved_actions_section([], {}, [])
        assert r == []

    def test_pending_checklist_adds_action(self):
        r = eb._unresolved_actions_section([_pending_cl("NVDA")], {}, [])
        assert any("NVDA" in a for a in r)

    def test_overdue_review_adds_action(self):
        r = eb._unresolved_actions_section([], {"overdue": [_due_review("AAPL")]}, [])
        assert any("AAPL" in a for a in r)

    def test_unreviewed_dryrun_adds_action(self):
        r = eb._unresolved_actions_section([], {}, [_dryrun("TSLA")])
        assert any("TSLA" in a for a in r)

    def test_capped_at_max(self):
        cls = [_pending_cl(f"T{i}") for i in range(10)]
        r = eb._unresolved_actions_section(cls, {"overdue": [_due_review("A")]}, [_dryrun("B")])
        assert len(r) <= eb.MAX_UNRESOLVED

    def test_no_trade_verbs(self):
        r = eb._unresolved_actions_section([_pending_cl("NVDA")],
                                           {"overdue": [_due_review("AAPL")]}, [])
        for action in r:
            lower = action.lower()
            assert "buy" not in lower
            assert "sell" not in lower
            assert "order" not in lower


# ── build_eod_sections ─────────────────────────────────────────────────────────

class TestBuildEodSections:
    def test_all_sections_present_empty(self):
        sections = eb.build_eod_sections(_empty_data())
        for key in eb.REQUIRED_SECTIONS:
            assert key in sections, f"Missing: {key}"

    def test_all_sections_present_full(self):
        sections = eb.build_eod_sections(_full_data())
        for key in eb.REQUIRED_SECTIONS:
            assert key in sections, f"Missing: {key}"

    def test_portfolio_change_is_dict(self):
        s = eb.build_eod_sections(_empty_data())
        assert isinstance(s["portfolio_change_summary"], dict)

    def test_tomorrow_watchlist_is_list(self):
        s = eb.build_eod_sections(_full_data())
        assert isinstance(s["tomorrow_watchlist"], list)

    def test_unresolved_is_list(self):
        s = eb.build_eod_sections(_full_data())
        assert isinstance(s["unresolved_actions"], list)

    def test_alpha_cap_respected(self):
        data = _empty_data()
        data["alpha_today"] = [_alpha_row(f"T{i}", 80.0 - i) for i in range(10)]
        s = eb.build_eod_sections(data)
        assert len(s["alpha_candidates_today"]["top_candidates"]) <= eb.MAX_ALPHA_CHANGES


# ── format_compact_eod ─────────────────────────────────────────────────────────

class TestFormatCompactEod:
    def test_returns_string(self):
        r = eb.format_compact_eod(eb.build_eod_sections(_empty_data()))
        assert isinstance(r, str)

    def test_length_bounded(self):
        r = eb.format_compact_eod(eb.build_eod_sections(_full_data()))
        assert len(r) <= eb.COMPACT_MAX_CHARS

    def test_no_banned_words(self):
        r = eb.format_compact_eod(eb.build_eod_sections(_full_data()))
        assert eb.check_banned_words(r) == []

    def test_contains_eod_header(self):
        r = eb.format_compact_eod(eb.build_eod_sections(_empty_data()))
        assert "EOD REVIEW" in r

    def test_contains_portfolio(self):
        r = eb.format_compact_eod(eb.build_eod_sections(_full_data()))
        assert "PORTFOLIO" in r

    def test_watch_tomorrow_shown(self):
        data = _empty_data()
        data["top_candidates"] = [_alpha_row("NVDA", 90.0, rtier="ALERT_READY")]
        r = eb.format_compact_eod(eb.build_eod_sections(data))
        assert "NVDA" in r

    def test_unresolved_shown(self):
        data = _empty_data()
        data["pending_checklists"] = [_pending_cl("NVDA")]
        r = eb.format_compact_eod(eb.build_eod_sections(data))
        assert "UNRESOLVED" in r

    def test_empty_no_crash(self):
        r = eb.format_compact_eod(eb.build_eod_sections(_empty_data()))
        assert len(r) > 0

    def test_custom_generated_at(self):
        r = eb.format_compact_eod(eb.build_eod_sections(_empty_data()),
                                   generated_at="May 20, 2026 16:15 ET")
        assert "May 20, 2026" in r

    def test_no_trade_commands(self):
        r = eb.format_compact_eod(eb.build_eod_sections(_full_data()))
        lower = r.lower()
        assert "buy" not in lower
        assert "sell" not in lower
        assert "order" not in lower

    def test_regime_change_shown(self):
        data = _empty_data()
        data["regime_snapshots_today"] = [
            _regime_snap("NEUTRAL", 55.0, "T1"),
            _regime_snap("RISK_ON", 70.0, "T2"),
        ]
        r = eb.format_compact_eod(eb.build_eod_sections(data))
        assert "REGIME" in r


# ── format_detailed_eod ────────────────────────────────────────────────────────

class TestFormatDetailedEod:
    def test_returns_dict(self):
        r = eb.format_detailed_eod(eb.build_eod_sections(_full_data()))
        assert isinstance(r, dict)

    def test_mode_is_detailed(self):
        r = eb.format_detailed_eod(eb.build_eod_sections(_full_data()))
        assert r["mode"] == "detailed"

    def test_has_generated_at(self):
        r = eb.format_detailed_eod(eb.build_eod_sections(_full_data()))
        assert "generated_at" in r

    def test_all_sections_present(self):
        r = eb.format_detailed_eod(eb.build_eod_sections(_full_data()))
        for key in eb.REQUIRED_SECTIONS:
            assert key in r

    def test_empty_no_crash(self):
        r = eb.format_detailed_eod(eb.build_eod_sections(_empty_data()))
        assert isinstance(r, dict)


# ── format_debug_eod ───────────────────────────────────────────────────────────

class TestFormatDebugEod:
    def test_mode_is_debug(self):
        data = _full_data()
        r = eb.format_debug_eod(eb.build_eod_sections(data), data)
        assert r["mode"] == "debug"

    def test_has_data_sources(self):
        data = _full_data()
        r = eb.format_debug_eod(eb.build_eod_sections(data), data)
        assert "data_sources" in r

    def test_data_sources_keys(self):
        data = _full_data()
        r = eb.format_debug_eod(eb.build_eod_sections(data), data)
        ds = r["data_sources"]
        assert "portfolio_available" in ds
        assert "alpha_today_count" in ds
        assert "dryruns_today_count" in ds
        assert "today_start" in ds

    def test_alpha_count_matches(self):
        data = _empty_data()
        data["alpha_today"] = [_alpha_row() for _ in range(4)]
        r = eb.format_debug_eod(eb.build_eod_sections(data), data)
        assert r["data_sources"]["alpha_today_count"] == 4


# ── generate_eod_brief ─────────────────────────────────────────────────────────

class TestGenerateEodBrief:
    def setup_method(self):
        import eod_brief as _eb
        self._orig = _eb.collect_eod_data

    def teardown_method(self):
        import eod_brief as _eb
        _eb.collect_eod_data = self._orig

    def _patch(self, data):
        import eod_brief as _eb
        _eb.collect_eod_data = lambda: data

    def test_compact_returns_string(self):
        self._patch(_full_data())
        r = eb.generate_eod_brief(mode="compact")
        assert isinstance(r, str)

    def test_detailed_returns_dict(self):
        self._patch(_full_data())
        r = eb.generate_eod_brief(mode="detailed")
        assert isinstance(r, dict)
        assert r["mode"] == "detailed"

    def test_debug_has_data_sources(self):
        self._patch(_full_data())
        r = eb.generate_eod_brief(mode="debug")
        assert "data_sources" in r

    def test_invalid_mode_defaults_to_detailed(self):
        self._patch(_empty_data())
        r = eb.generate_eod_brief(mode="bogus")
        assert isinstance(r, dict)
        assert r["mode"] == "detailed"

    def test_compact_bounded(self):
        self._patch(_full_data())
        r = eb.generate_eod_brief(mode="compact")
        assert len(r) <= eb.COMPACT_MAX_CHARS


# ── generate_compact_eod ───────────────────────────────────────────────────────

class TestGenerateCompactEod:
    def test_returns_string(self, monkeypatch):
        monkeypatch.setattr(eb, "collect_eod_data", lambda: _empty_data())
        r = eb.generate_compact_eod()
        assert isinstance(r, str)

    def test_never_raises(self, monkeypatch):
        monkeypatch.setattr(eb, "collect_eod_data", lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        r = eb.generate_compact_eod()
        assert isinstance(r, str)
        assert len(r) > 0

    def test_fallback_contains_eod(self, monkeypatch):
        monkeypatch.setattr(eb, "generate_eod_brief", lambda **kw: (_ for _ in ()).throw(Exception("fail")))
        r = eb.generate_compact_eod()
        assert "EOD" in r or "Brief" in r


# ── Scheduler integration ──────────────────────────────────────────────────────

class TestEodBriefScheduler:
    def test_eod_brief_job_skips_when_disabled(self, monkeypatch):
        import eod_brief as _eb
        import alerts
        import scheduler

        monkeypatch.setattr(_eb, "eod_enabled", lambda: False)
        sent = []
        monkeypatch.setattr(alerts, "send_sms", lambda msg, bypass_quiet=False: sent.append(msg) or True)
        monkeypatch.setattr(alerts, "log_alert", lambda *a, **kw: None)

        scheduler.eod_brief_job()
        assert sent == [], "Should not send when flag is disabled"

    def test_eod_brief_job_sends_when_enabled(self, monkeypatch):
        import eod_brief as _eb
        import alerts
        import scheduler

        monkeypatch.setattr(_eb, "eod_enabled", lambda: True)
        monkeypatch.setattr(_eb, "generate_compact_eod", lambda: "EOD BRIEF MSG")
        sent = []
        monkeypatch.setattr(alerts, "send_sms", lambda msg, bypass_quiet=False: sent.append(msg) or True)
        monkeypatch.setattr(alerts, "log_alert", lambda *a, **kw: None)

        scheduler.eod_brief_job()
        assert sent == ["EOD BRIEF MSG"]

    def test_eod_brief_job_sends_exactly_one_message(self, monkeypatch):
        import eod_brief as _eb
        import alerts
        import scheduler

        monkeypatch.setattr(_eb, "eod_enabled", lambda: True)
        monkeypatch.setattr(_eb, "generate_compact_eod", lambda: "MSG")
        sent = []
        monkeypatch.setattr(alerts, "send_sms", lambda msg, bypass_quiet=False: sent.append(msg) or True)
        monkeypatch.setattr(alerts, "log_alert", lambda *a, **kw: None)

        scheduler.eod_brief_job()
        assert len(sent) == 1

    def test_eod_brief_job_does_not_raise_on_error(self, monkeypatch):
        import eod_brief as _eb
        import scheduler

        monkeypatch.setattr(_eb, "eod_enabled", lambda: True)
        monkeypatch.setattr(_eb, "generate_compact_eod", lambda: (_ for _ in ()).throw(RuntimeError("fail")))

        scheduler.eod_brief_job()  # must not raise

    def test_eod_brief_job_separate_from_morning(self, monkeypatch):
        """EOD job must not call morning brief's generate_compact_brief."""
        import operator_brief as _ob
        import eod_brief as _eb
        import alerts
        import scheduler

        morning_called = []
        monkeypatch.setattr(_ob, "generate_compact_brief", lambda: morning_called.append(1) or "MORNING")
        monkeypatch.setattr(_eb, "eod_enabled", lambda: True)
        monkeypatch.setattr(_eb, "generate_compact_eod", lambda: "EOD")
        monkeypatch.setattr(alerts, "send_sms", lambda msg, bypass_quiet=False: True)
        monkeypatch.setattr(alerts, "log_alert", lambda *a, **kw: None)

        scheduler.eod_brief_job()
        assert not morning_called, "EOD job must not call morning brief"

    def test_scheduler_has_eod_brief_in_source(self):
        import scheduler
        import inspect
        source = inspect.getsource(scheduler.start_scheduler)
        assert "eod_brief" in source


# ── No trading calls ───────────────────────────────────────────────────────────

class TestNoTradingCalls:
    def test_source_no_broker_calls(self):
        import inspect
        source = inspect.getsource(eb)
        forbidden = [
            "place_order(", "submit_order(", "create_order(",
            "execute_trade(", "wealthsimple.buy", "wealthsimple.sell",
            "send_alert(", "send_whatsapp(",
        ]
        for term in forbidden:
            assert term not in source, f"Forbidden: {term}"

    def test_unresolved_no_trade_verbs(self):
        r = eb._unresolved_actions_section(
            [_pending_cl("NVDA")],
            {"overdue": [_due_review("AAPL")]},
            [_dryrun("TSLA")],
        )
        for act in r:
            lower = act.lower()
            assert "buy" not in lower
            assert "sell" not in lower
            assert "order" not in lower


# ── Sparse-data safety ────────────────────────────────────────────────────────

class TestSparseDataSafety:
    def _all_sections(self, sections):
        return all(k in sections for k in eb.REQUIRED_SECTIONS)

    def test_empty_portfolio(self):
        s = eb.build_eod_sections(_empty_data())
        assert self._all_sections(s)

    def test_no_alpha_today(self):
        data = _empty_data()
        data["alpha_today"] = []
        s = eb.build_eod_sections(data)
        assert s["alpha_candidates_today"]["scored_today"] == 0

    def test_no_regime_snapshots(self):
        s = eb.build_eod_sections(_empty_data())
        assert s["regime_changes_today"]["snapshots_today"] == 0

    def test_no_stress_runs(self):
        s = eb.build_eod_sections(_empty_data())
        assert s["stress_risk_changes_today"].get("runs_today", 0) == 0

    def test_compact_empty_no_crash(self):
        r = eb.format_compact_eod(eb.build_eod_sections(_empty_data()))
        assert isinstance(r, str) and len(r) <= eb.COMPACT_MAX_CHARS

    def test_detailed_empty_no_crash(self):
        r = eb.format_detailed_eod(eb.build_eod_sections(_empty_data()))
        assert isinstance(r, dict)


# ── Prioritization caps ────────────────────────────────────────────────────────

class TestPrioritizationCaps:
    def test_alpha_candidates_max_3(self):
        data = _empty_data()
        data["alpha_today"] = [_alpha_row(f"T{i}", 90.0 - i) for i in range(20)]
        s = eb.build_eod_sections(data)
        assert len(s["alpha_candidates_today"]["top_candidates"]) == eb.MAX_ALPHA_CHANGES

    def test_tomorrow_watchlist_max_3(self):
        data = _empty_data()
        data["top_candidates"] = [_alpha_row(f"T{i}") for i in range(20)]
        s = eb.build_eod_sections(data)
        assert len(s["tomorrow_watchlist"]) <= eb.MAX_WATCHLIST

    def test_unresolved_max_3(self):
        data = _empty_data()
        data["pending_checklists"] = [_pending_cl(f"T{i}") for i in range(10)]
        data["due_reviews"] = {"overdue": [_due_review(f"U{i}") for i in range(5)], "due": [], "upcoming": []}
        s = eb.build_eod_sections(data)
        assert len(s["unresolved_actions"]) <= eb.MAX_UNRESOLVED

    def test_outcomes_tickers_max_3(self):
        data = _empty_data()
        data["outcomes_today"] = [_outcome(f"T{i}") for i in range(10)]
        s = eb.build_eod_sections(data)
        assert len(s["outcomes_completed_today"]["tickers"]) <= eb.MAX_LEARNING_NOTES


# ── Banned words ───────────────────────────────────────────────────────────────

class TestBannedWords:
    def test_compact_output_no_banned(self):
        s = eb.build_eod_sections(_full_data())
        text = eb.format_compact_eod(s)
        assert eb.check_banned_words(text) == []

    def test_individual_check_moon(self):
        assert "moon" in eb.check_banned_words("to the moon")

    def test_individual_check_clean(self):
        assert eb.check_banned_words("steady upward trend in NVDA") == []


# ── API endpoint ──────────────────────────────────────────────────────────────

@pytest.fixture()
def app(monkeypatch, tmp_path):
    import sqlite3
    import importlib
    import api
    import eod_brief as _eb

    db_file = tmp_path / "api_test.db"
    monkeypatch.setenv("DB_PATH",    str(db_file))
    monkeypatch.setenv("API_SECRET", "")
    monkeypatch.setattr(database, "DB_PATH", str(db_file))

    def _conn():
        c = sqlite3.connect(str(db_file), timeout=5, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(database, "get_connection", _conn)
    monkeypatch.setattr(_eb, "collect_eod_data", lambda: _full_data())

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


class TestApiEodBrief:
    def test_get_returns_200(self, client):
        assert client.get("/api/v1/brief/eod").status_code == 200

    def test_ok_envelope(self, client):
        body = json.loads(client.get("/api/v1/brief/eod").data)
        assert body["ok"] is True

    def test_mode_detailed(self, client):
        body = json.loads(client.get("/api/v1/brief/eod?mode=detailed").data)
        assert body["data"]["mode"] == "detailed"

    def test_mode_compact_returns_brief(self, client):
        body = json.loads(client.get("/api/v1/brief/eod?mode=compact").data)
        assert "brief" in body["data"]
        assert isinstance(body["data"]["brief"], str)

    def test_mode_debug_has_data_sources(self, client):
        body = json.loads(client.get("/api/v1/brief/eod?mode=debug").data)
        assert "data_sources" in body["data"]

    def test_invalid_mode_defaults_to_detailed(self, client):
        body = json.loads(client.get("/api/v1/brief/eod?mode=bogus").data)
        assert body["data"]["mode"] == "detailed"

    def test_cached_second_call(self, client):
        client.get("/api/v1/brief/eod?mode=detailed")
        body2 = json.loads(client.get("/api/v1/brief/eod?mode=detailed").data)
        assert body2["meta"]["cached"] is True

    def test_compact_mode_has_mode_key(self, client):
        body = json.loads(client.get("/api/v1/brief/eod?mode=compact").data)
        assert body["data"]["mode"] == "compact"

    def test_compact_length_bounded(self, client):
        body = json.loads(client.get("/api/v1/brief/eod?mode=compact").data)
        assert len(body["data"]["brief"]) <= eb.COMPACT_MAX_CHARS

    def test_api_works_regardless_of_feature_flag(self, client, monkeypatch):
        import eod_brief as _eb
        monkeypatch.setattr(_eb, "eod_enabled", lambda: False)
        assert client.get("/api/v1/brief/eod").status_code == 200
