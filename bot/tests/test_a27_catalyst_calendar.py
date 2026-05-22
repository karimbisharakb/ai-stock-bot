"""
Phase A27 — Catalyst Calendar and Event Tracker tests.

Covers:
- Constants: CATALYST_TYPES, CONFIDENCE_LEVELS, IMPORTANCE_LEVELS, SOURCES, STATUSES
- Deterministic catalyst_id (_make_catalyst_id)
- CRUD: upsert_catalyst, get_catalyst, mark_completed, archive_catalyst
- Calendar queries: get_upcoming, get_overdue, get_by_ticker
- Auto-generators: _collect_macro_placeholders, generate_catalysts
- Brief hooks: get_brief_catalysts, get_completed_today, get_weekly_catalyst_summary
- Summary: get_catalyst_summary
- Status transitions: UPCOMING → COMPLETED / ARCHIVED; terminal states not reopened
- Sparse-data safety: collectors return defaults on DB failure
- No trading calls in source
- API endpoints: GET /catalysts, GET /catalysts/summary, GET /catalysts/<id>
  POST /catalysts/upsert (auth), POST /catalysts/<id>/complete, /archive
- Brief integrations: operator_brief, eod_brief, weekly_review REQUIRED_SECTIONS
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

import catalyst_calendar as cc


# ── Isolated DB helpers ───────────────────────────────────────────────────────

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


def _make_app(test_instance=None):
    import database
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    _orig_db_path = database.DB_PATH
    _orig_get_conn = database.get_connection
    database.DB_PATH = tmp.name

    def _conn():
        c = sqlite3.connect(tmp.name)
        c.row_factory = sqlite3.Row
        return c

    database.get_connection = _conn

    def _restore():
        database.DB_PATH = _orig_db_path
        database.get_connection = _orig_get_conn

    if test_instance is not None:
        test_instance.addCleanup(_restore)

    import api as api_mod
    importlib.reload(api_mod)
    from flask import Flask
    app = Flask("test_a27")
    app.register_blueprint(api_mod.api_bp)
    app.config["TESTING"] = True
    api_mod.cache_clear()
    return app, api_mod, tmp.name, _conn


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _future(days: int = 7) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def _past(days: int = 3) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


# ── Constants ─────────────────────────────────────────────────────────────────

class TestConstants(unittest.TestCase):
    def test_catalyst_types_nonempty(self):
        self.assertGreater(len(cc.CATALYST_TYPES), 0)

    def test_required_types_present(self):
        required = {"EARNINGS", "MACRO", "FDA_REGULATORY", "THESIS_REVIEW",
                    "WATCHLIST_REVIEW", "ALPHA_CONFIRMATION", "PORTFOLIO_RISK", "OTHER"}
        self.assertTrue(required.issubset(cc.CATALYST_TYPES))

    def test_confidence_levels(self):
        self.assertEqual(cc.CONFIDENCE_LEVELS, frozenset({"LOW", "MEDIUM", "HIGH"}))

    def test_importance_levels(self):
        self.assertEqual(cc.IMPORTANCE_LEVELS, frozenset({"LOW", "MEDIUM", "HIGH"}))

    def test_sources(self):
        required = {"alpha", "thesis", "watchlist", "macro", "manual", "research"}
        self.assertTrue(required.issubset(cc.SOURCES))

    def test_statuses(self):
        self.assertEqual(cc.STATUSES, frozenset({"UPCOMING", "COMPLETED", "MISSED", "ARCHIVED"}))

    def test_macro_events_list_nonempty(self):
        self.assertGreater(len(cc._MACRO_EVENTS), 0)

    def test_macro_events_are_tuples_of_4(self):
        for event in cc._MACRO_EVENTS:
            self.assertEqual(len(event), 4, msg=f"Event {event!r} should be 4-tuple")


# ── Deterministic IDs ─────────────────────────────────────────────────────────

class TestMakeCatalystId(unittest.TestCase):
    def test_returns_16_chars(self):
        cid = cc._make_catalyst_id("AAPL", "EARNINGS", "manual")
        self.assertEqual(len(cid), 16)

    def test_deterministic_same_inputs(self):
        a = cc._make_catalyst_id("AAPL", "EARNINGS", "manual")
        b = cc._make_catalyst_id("AAPL", "EARNINGS", "manual")
        self.assertEqual(a, b)

    def test_different_tickers_give_different_ids(self):
        a = cc._make_catalyst_id("AAPL", "EARNINGS", "manual")
        b = cc._make_catalyst_id("MSFT", "EARNINGS", "manual")
        self.assertNotEqual(a, b)

    def test_different_types_give_different_ids(self):
        a = cc._make_catalyst_id("AAPL", "EARNINGS", "manual")
        b = cc._make_catalyst_id("AAPL", "MACRO", "manual")
        self.assertNotEqual(a, b)

    def test_extra_param_differentiates(self):
        a = cc._make_catalyst_id("AAPL", "EARNINGS", "manual", extra="Q1")
        b = cc._make_catalyst_id("AAPL", "EARNINGS", "manual", extra="Q2")
        self.assertNotEqual(a, b)

    def test_none_ticker_normalised(self):
        a = cc._make_catalyst_id(None, "MACRO", "macro")
        b = cc._make_catalyst_id("NONE", "MACRO", "macro")
        self.assertEqual(a, b)

    def test_ticker_uppercased(self):
        a = cc._make_catalyst_id("aapl", "EARNINGS", "manual")
        b = cc._make_catalyst_id("AAPL", "EARNINGS", "manual")
        self.assertEqual(a, b)


# ── CRUD ─────────────────────────────────────────────────────────────────────

class TestUpsertAndGet(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        self.patcher = _patch_db(self.conn_fn)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_upsert_creates_record(self):
        result = cc.upsert_catalyst(
            ticker="AAPL", title="AAPL Q1 earnings", catalyst_type="EARNINGS",
            date=_future(7), source="manual",
        )
        self.assertEqual(result["ticker"], "AAPL")
        self.assertEqual(result["status"], "UPCOMING")
        self.assertIn("catalyst_id", result)

    def test_upsert_returns_valid_id(self):
        result = cc.upsert_catalyst(title="Test", catalyst_type="MACRO",
                                    date=_future(5), source="manual")
        self.assertEqual(len(result["catalyst_id"]), 16)

    def test_upsert_idempotent_same_id(self):
        r1 = cc.upsert_catalyst(ticker="MSFT", title="MSFT earnings",
                                 catalyst_type="EARNINGS", date=_future(10), source="manual")
        r2 = cc.upsert_catalyst(ticker="MSFT", title="MSFT earnings",
                                 catalyst_type="EARNINGS", date=_future(10), source="manual")
        self.assertEqual(r1["catalyst_id"], r2["catalyst_id"])

    def test_upsert_updates_existing(self):
        r1 = cc.upsert_catalyst(ticker="NVDA", title="NVDA earnings",
                                 catalyst_type="EARNINGS", date=_future(3),
                                 source="manual", importance="LOW")
        r2 = cc.upsert_catalyst(ticker="NVDA", title="NVDA earnings",
                                 catalyst_type="EARNINGS", date=_future(3),
                                 source="manual", importance="HIGH")
        self.assertEqual(r1["catalyst_id"], r2["catalyst_id"])
        self.assertEqual(r2["importance"], "HIGH")

    def test_get_catalyst_found(self):
        r = cc.upsert_catalyst(ticker="AMD", title="AMD investor day",
                                catalyst_type="INVESTOR_DAY", date=_future(14), source="manual")
        fetched = cc.get_catalyst(r["catalyst_id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["ticker"], "AMD")

    def test_get_catalyst_not_found(self):
        result = cc.get_catalyst("nonexistent1234")
        self.assertIsNone(result)

    def test_upsert_invalid_type_fallback_to_other(self):
        r = cc.upsert_catalyst(title="Bad type", catalyst_type="INVALID_XYZ",
                                date=_future(1), source="manual")
        self.assertEqual(r["catalyst_type"], "OTHER")

    def test_upsert_invalid_importance_fallback(self):
        r = cc.upsert_catalyst(title="Bad imp", importance="EXTREME",
                                date=_future(1), source="manual")
        self.assertEqual(r["importance"], "MEDIUM")

    def test_upsert_ticker_uppercased(self):
        r = cc.upsert_catalyst(ticker="aapl", title="Lower ticker",
                                catalyst_type="EARNINGS", date=_future(5), source="manual")
        self.assertEqual(r["ticker"], "AAPL")


# ── Status transitions ────────────────────────────────────────────────────────

class TestTransitions(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        self.patcher = _patch_db(self.conn_fn)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_mark_completed(self):
        r = cc.upsert_catalyst(title="Complete me", catalyst_type="MACRO",
                                date=_future(1), source="manual")
        done = cc.mark_completed(r["catalyst_id"])
        self.assertEqual(done["status"], "COMPLETED")

    def test_archive_catalyst(self):
        r = cc.upsert_catalyst(title="Archive me", catalyst_type="OTHER",
                                date=_future(2), source="manual")
        archived = cc.archive_catalyst(r["catalyst_id"])
        self.assertEqual(archived["status"], "ARCHIVED")

    def test_transition_not_found_raises(self):
        with self.assertRaises(ValueError):
            cc.mark_completed("does_not_exist")

    def test_completed_not_returned_in_upcoming(self):
        r = cc.upsert_catalyst(title="Done catalyst", catalyst_type="EARNINGS",
                                date=_future(5), source="manual")
        cc.mark_completed(r["catalyst_id"])
        upcoming = cc.get_upcoming(days=30)
        cids = [c["catalyst_id"] for c in upcoming]
        self.assertNotIn(r["catalyst_id"], cids)

    def test_archived_not_returned_in_upcoming(self):
        r = cc.upsert_catalyst(title="Archived", catalyst_type="EARNINGS",
                                date=_future(5), source="manual")
        cc.archive_catalyst(r["catalyst_id"])
        upcoming = cc.get_upcoming(days=30)
        cids = [c["catalyst_id"] for c in upcoming]
        self.assertNotIn(r["catalyst_id"], cids)


# ── Calendar queries ──────────────────────────────────────────────────────────

class TestCalendarQueries(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        self.patcher = _patch_db(self.conn_fn)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_get_upcoming_empty_db(self):
        result = cc.get_upcoming(days=30)
        self.assertIsInstance(result, list)
        self.assertEqual(result, [])

    def test_get_upcoming_returns_future_items(self):
        cc.upsert_catalyst(title="Near event", catalyst_type="MACRO",
                            date=_future(5), source="manual")
        result = cc.get_upcoming(days=30)
        self.assertEqual(len(result), 1)

    def test_get_upcoming_filters_by_days(self):
        cc.upsert_catalyst(title="Soon", catalyst_type="MACRO",
                            date=_future(3), source="manual")
        cc.upsert_catalyst(title="Far", catalyst_type="MACRO",
                            date=_future(60), source="manual")
        result = cc.get_upcoming(days=10)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Soon")

    def test_get_upcoming_ticker_filter(self):
        cc.upsert_catalyst(ticker="AAPL", title="Apple event", catalyst_type="EARNINGS",
                            date=_future(5), source="manual")
        cc.upsert_catalyst(ticker="MSFT", title="MS event", catalyst_type="EARNINGS",
                            date=_future(5), source="manual")
        result = cc.get_upcoming(days=30, ticker="AAPL")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ticker"], "AAPL")

    def test_get_upcoming_importance_filter(self):
        cc.upsert_catalyst(title="High event", catalyst_type="MACRO",
                            date=_future(3), source="manual", importance="HIGH")
        cc.upsert_catalyst(title="Low event", catalyst_type="MACRO",
                            date=_future(4), source="manual", importance="LOW")
        result = cc.get_upcoming(days=30, importance="HIGH")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["importance"], "HIGH")

    def test_get_overdue_empty(self):
        result = cc.get_overdue()
        self.assertIsInstance(result, list)

    def test_get_overdue_returns_past_upcoming(self):
        cc.upsert_catalyst(title="Overdue", catalyst_type="EARNINGS",
                            date=_past(2), source="manual")
        result = cc.get_overdue()
        self.assertGreater(len(result), 0)
        self.assertTrue(all(c["date"] < _today() for c in result))

    def test_get_by_ticker_empty(self):
        result = cc.get_by_ticker("ZZZDNE")
        self.assertIsInstance(result, list)
        self.assertEqual(result, [])

    def test_get_by_ticker_returns_all_statuses(self):
        cc.upsert_catalyst(ticker="TSM", title="TSM earnings", catalyst_type="EARNINGS",
                            date=_future(5), source="manual")
        result = cc.get_by_ticker("TSM")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ticker"], "TSM")


# ── Macro placeholders ────────────────────────────────────────────────────────

class TestMacroPlaceholders(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        self.patcher = _patch_db(self.conn_fn)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_collect_macro_returns_list(self):
        result = cc._collect_macro_placeholders()
        self.assertIsInstance(result, list)

    def test_macro_items_have_required_fields(self):
        items = cc._collect_macro_placeholders()
        for item in items:
            for field in ("title", "catalyst_type", "date", "source", "importance"):
                self.assertIn(field, item, msg=f"Missing {field!r} in macro item")

    def test_macro_source_is_macro(self):
        items = cc._collect_macro_placeholders()
        for item in items:
            self.assertEqual(item["source"], "macro")

    def test_macro_type_is_macro(self):
        items = cc._collect_macro_placeholders()
        for item in items:
            self.assertEqual(item["catalyst_type"], "MACRO")

    def test_macro_dates_are_upcoming(self):
        items = cc._collect_macro_placeholders()
        today = _today()
        ninety_days = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")
        for item in items:
            self.assertGreaterEqual(item["date"], today)
            self.assertLessEqual(item["date"], ninety_days)


# ── Brief hooks ───────────────────────────────────────────────────────────────

class TestBriefHooks(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        self.patcher = _patch_db(self.conn_fn)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_get_brief_catalysts_empty(self):
        result = cc.get_brief_catalysts()
        self.assertIsInstance(result, list)

    def test_get_brief_catalysts_returns_max_items(self):
        for i in range(10):
            cc.upsert_catalyst(title=f"Event {i}", catalyst_type="MACRO",
                                date=_future(i + 1), source="manual")
        result = cc.get_brief_catalysts(limit=3)
        self.assertLessEqual(len(result), 3)

    def test_get_brief_catalysts_high_importance_first(self):
        cc.upsert_catalyst(title="Low event", catalyst_type="MACRO",
                            date=_future(1), source="manual", importance="LOW")
        cc.upsert_catalyst(title="High event", catalyst_type="MACRO",
                            date=_future(2), source="manual", importance="HIGH")
        result = cc.get_brief_catalysts(limit=5)
        self.assertGreater(len(result), 0)
        self.assertEqual(result[0]["importance"], "HIGH")

    def test_get_completed_today_empty(self):
        result = cc.get_completed_today()
        self.assertIsInstance(result, list)

    def test_get_completed_today_returns_completed_items(self):
        r = cc.upsert_catalyst(title="Done today", catalyst_type="EARNINGS",
                                date=_today(), source="manual")
        cc.mark_completed(r["catalyst_id"])
        result = cc.get_completed_today()
        cids = [c["catalyst_id"] for c in result]
        self.assertIn(r["catalyst_id"], cids)

    def test_get_weekly_catalyst_summary_returns_dict(self):
        week_start = _today()
        week_end   = _future(7)
        result = cc.get_weekly_catalyst_summary(week_start, week_end)
        self.assertIsInstance(result, dict)

    def test_get_weekly_catalyst_summary_has_required_keys(self):
        result = cc.get_weekly_catalyst_summary(_today(), _future(7))
        for key in ("completed_this_week", "active_this_week", "high_importance_count"):
            self.assertIn(key, result)

    def test_get_weekly_catalyst_summary_counts(self):
        week_start = _today()
        week_end   = _future(7)
        cc.upsert_catalyst(title="This week", catalyst_type="MACRO",
                            date=_future(3), source="manual", importance="HIGH")
        result = cc.get_weekly_catalyst_summary(week_start, week_end)
        self.assertGreaterEqual(result["active_this_week"], 1)
        self.assertGreaterEqual(result["high_importance_count"], 1)


# ── Catalyst summary ──────────────────────────────────────────────────────────

class TestCatalystSummary(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        self.patcher = _patch_db(self.conn_fn)
        self.patcher.start()
        self.alpha_patcher = patch("catalyst_calendar.get_alert_candidates",
                                   return_value=[], create=True)
        self.alpha_patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.alpha_patcher.stop()

    def test_get_catalyst_summary_returns_dict(self):
        result = cc.get_catalyst_summary()
        self.assertIsInstance(result, dict)

    def test_get_catalyst_summary_has_required_keys(self):
        result = cc.get_catalyst_summary()
        required = {"this_week_count", "next_week_count", "high_importance_count",
                    "portfolio_catalysts", "alpha_catalysts", "overdue_count",
                    "overdue_catalysts", "missing_thesis_dates"}
        for key in required:
            self.assertIn(key, result, msg=f"Missing key {key!r}")

    def test_catalyst_summary_overdue_count(self):
        cc.upsert_catalyst(title="Overdue", catalyst_type="EARNINGS",
                            date=_past(5), source="manual")
        result = cc.get_catalyst_summary()
        self.assertGreaterEqual(result["overdue_count"], 1)


# ── Generate catalysts ────────────────────────────────────────────────────────

class TestGenerateCatalysts(unittest.TestCase):
    def setUp(self):
        _, self.conn_fn = _make_db()
        self.patcher = _patch_db(self.conn_fn)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_generate_catalysts_returns_list(self):
        result = cc.generate_catalysts()
        self.assertIsInstance(result, list)

    def test_generate_catalysts_does_not_raise(self):
        try:
            cc.generate_catalysts()
        except Exception as exc:
            self.fail(f"generate_catalysts raised: {exc}")


# ── Sparse-data safety ────────────────────────────────────────────────────────

class TestSparseDataSafe(unittest.TestCase):
    def test_get_brief_catalysts_never_raises(self):
        import database
        with patch.object(database, "get_connection", side_effect=Exception("db down")):
            result = cc.get_brief_catalysts()
        self.assertEqual(result, [])

    def test_get_completed_today_never_raises(self):
        import database
        with patch.object(database, "get_connection", side_effect=Exception("db down")):
            result = cc.get_completed_today()
        self.assertEqual(result, [])

    def test_get_weekly_summary_never_raises(self):
        import database
        with patch.object(database, "get_connection", side_effect=Exception("db down")):
            result = cc.get_weekly_catalyst_summary("2026-01-01", "2026-01-08")
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("completed_this_week", 0), 0)


# ── No trading calls ──────────────────────────────────────────────────────────

class TestNoTradingCalls(unittest.TestCase):
    def test_no_order_placement_keywords(self):
        source = Path(BOT_DIR / "catalyst_calendar.py").read_text()
        forbidden = ["place_order", "execute_trade", "buy_order", "sell_order",
                     "submit_order", "auto_buy", "auto_sell"]
        for kw in forbidden:
            self.assertNotIn(kw, source,
                             msg=f"Forbidden trading keyword {kw!r} found in catalyst_calendar.py")


# ── API endpoints ─────────────────────────────────────────────────────────────

class TestApiCatalysts(unittest.TestCase):
    def setUp(self):
        self.app, self.api, self.db_path, self.conn_fn = _make_app(self)
        self.client = self.app.test_client()

        import database
        database.DB_PATH = self.db_path
        database.get_connection = self.conn_fn

        import catalyst_calendar as cc_mod
        importlib.reload(cc_mod)

    def test_get_catalysts_ok(self):
        resp = self.client.get("/api/v1/catalysts")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("catalysts", data["data"])

    def test_get_catalysts_days_param(self):
        resp = self.client.get("/api/v1/catalysts?days=7")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["data"]["days"], 7)

    def test_get_catalysts_summary_ok(self):
        import database
        with patch.object(database, "get_connection", self.conn_fn):
            with patch("alpha_alert_gate.get_alert_candidates", return_value=[], create=True):
                resp = self.client.get("/api/v1/catalysts/summary")
        self.assertEqual(resp.status_code, 200)

    def test_get_catalyst_not_found(self):
        resp = self.client.get("/api/v1/catalysts/doesnotexist1234")
        self.assertEqual(resp.status_code, 404)

    def test_upsert_requires_auth_when_secret_set(self):
        with patch.dict(os.environ, {"API_SECRET": "mysecret"}, clear=False):
            resp = self.client.post("/api/v1/catalysts/upsert",
                                    json={"title": "test", "date": _future(1)})
        self.assertEqual(resp.status_code, 401)

    def test_upsert_with_auth(self):
        with patch.dict(os.environ, {"API_SECRET": "testsecret"}):
            importlib.reload(self.api)
            app2 = self.app.__class__("test_a27_auth")
            app2.register_blueprint(self.api.api_bp)
            app2.config["TESTING"] = True
            client2 = app2.test_client()
            resp = client2.post(
                "/api/v1/catalysts/upsert",
                json={"title": "FOMC meeting", "catalyst_type": "MACRO",
                      "date": _future(5), "source": "manual"},
                headers={"Authorization": "Bearer testsecret"},
            )
            self.assertEqual(resp.status_code, 200)

    def test_complete_not_found(self):
        # With no API_SECRET set, auth fails-open; endpoint returns 404
        resp = self.client.post("/api/v1/catalysts/notfound1234/complete")
        self.assertEqual(resp.status_code, 404)

    def test_archive_not_found(self):
        # With no API_SECRET set, auth fails-open; endpoint returns 404
        resp = self.client.post("/api/v1/catalysts/notfound1234/archive")
        self.assertEqual(resp.status_code, 404)


# ── Brief integration: operator_brief REQUIRED_SECTIONS ──────────────────────

class TestOperatorBriefIntegration(unittest.TestCase):
    def test_upcoming_catalysts_in_required_sections(self):
        import operator_brief as ob
        self.assertIn("upcoming_catalysts", ob.REQUIRED_SECTIONS)

    def test_required_sections_count(self):
        import operator_brief as ob
        self.assertEqual(len(ob.REQUIRED_SECTIONS), 16)

    def test_build_sections_includes_upcoming_catalysts(self):
        import operator_brief as ob
        empty_data: dict = {
            "portfolio": {}, "overnight_signals": [], "alpha_candidates": [],
            "dry_runs": [], "qc_summary": {}, "regime_ctx": {},
            "risk_report": {}, "stress_run": None, "pending_checklists": [],
            "due_reviews": {"due": [], "overdue": [], "upcoming": [], "missing_thesis": []},
            "thesis_warnings": {}, "scorecard_summary": {},
            "planner_snapshot": None, "cash": 0.0, "tfsa_room": 0.0,
            "workflow_items": [], "upcoming_catalysts": [],
        }
        sections = ob.build_sections(empty_data)
        self.assertIn("upcoming_catalysts", sections)

    def test_collect_brief_data_has_upcoming_catalysts_key(self):
        import operator_brief as ob
        with patch("catalyst_calendar.get_brief_catalysts", return_value=[]):
            with patch("portfolio_reconciliation.reconcile_portfolio", return_value={}):
                with patch("portfolio.get_cash", return_value=0.0):
                    with patch("portfolio.get_tfsa_room", return_value=0.0):
                        data = ob.collect_brief_data()
        self.assertIn("upcoming_catalysts", data)


# ── Brief integration: eod_brief REQUIRED_SECTIONS ───────────────────────────

class TestEodBriefIntegration(unittest.TestCase):
    def test_catalyst_changes_today_in_required_sections(self):
        import eod_brief as eb
        self.assertIn("catalyst_changes_today", eb.REQUIRED_SECTIONS)

    def test_required_sections_count(self):
        import eod_brief as eb
        self.assertEqual(len(eb.REQUIRED_SECTIONS), 16)

    def test_catalyst_changes_section_empty(self):
        import eod_brief as eb
        result = eb._catalyst_changes_section([])
        self.assertEqual(result["completed_count"], 0)
        self.assertEqual(result["completed_today"], [])

    def test_catalyst_changes_section_with_items(self):
        import eod_brief as eb
        items = [
            {"ticker": "AAPL", "title": "Q1 earnings", "catalyst_type": "EARNINGS",
             "importance": "HIGH"},
        ]
        result = eb._catalyst_changes_section(items)
        self.assertEqual(result["completed_count"], 1)
        self.assertEqual(len(result["completed_today"]), 1)


# ── Brief integration: weekly_review REQUIRED_SECTIONS ───────────────────────

class TestWeeklyReviewIntegration(unittest.TestCase):
    def test_catalyst_summary_in_required_sections(self):
        import weekly_review as wr
        self.assertIn("catalyst_summary", wr.REQUIRED_SECTIONS)

    def test_required_sections_count(self):
        import weekly_review as wr
        self.assertEqual(len(wr.REQUIRED_SECTIONS), 21)

    def test_section_catalyst_summary_empty(self):
        import weekly_review as wr
        result = wr._section_catalyst_summary({})
        self.assertIn("active_count", result)
        self.assertIn("completed_count", result)
        self.assertIn("high_importance_count", result)
        self.assertEqual(result["completed_count"], 0)

    def test_section_catalyst_summary_with_data(self):
        import weekly_review as wr
        data = {
            "catalysts": {
                "active_this_week": 3,
                "completed_this_week": 2,
                "high_importance_count": 1,
            }
        }
        result = wr._section_catalyst_summary(data)
        self.assertEqual(result["active_count"], 3)
        self.assertEqual(result["completed_count"], 2)
        self.assertEqual(result["high_importance_count"], 1)

    def test_collect_catalysts_for_week_returns_dict(self):
        import weekly_review as wr
        with patch("catalyst_calendar.get_weekly_catalyst_summary", return_value={}):
            result = wr._collect_catalysts_for_week("2026-05-18", "2026-05-25")
        self.assertIsInstance(result, dict)

    def test_collect_catalysts_for_week_safe_on_error(self):
        import weekly_review as wr
        with patch("weekly_review.get_weekly_catalyst_summary", side_effect=Exception("fail"),
                   create=True):
            result = wr._collect_catalysts_for_week("2026-05-18", "2026-05-25")
        self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()
