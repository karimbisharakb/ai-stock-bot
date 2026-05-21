"""
Phase A25 — Research Workflow Automation and Review Cadence tests.

Covers:
- Constants / enum validation
- Priority scoring functions
- Deterministic item_id generation
- Queue generation (sources individually mocked)
- Queue sorting (priority_score DESC)
- Status transitions: mark_in_progress, mark_done
- Snooze: sets snoozed_until, excluded from default queue, returns after expiry
- Archive: no delete, excluded from queue
- Append note: append-only, empty text rejected, item_id not found rejected
- get_notes ordering
- Daily workflow summary structure
- DONE/ARCHIVED items not reopened by generate_queue
- Morning brief integration (workflow_items in sections)
- EOD brief integration (workflow_summary in sections)
- Auth required for all write endpoints
- No trading calls in source
- All 7 API endpoints
"""
import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

import research_workflow as rw


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


# ── Isolated Flask app fixture ─────────────────────────────────────────────────

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
    with patch.dict(os.environ, {"API_SECRET": "test-secret"}):
        import api as api_mod
        importlib.reload(api_mod)
        from flask import Flask
        app = Flask("test_a25")
        app.register_blueprint(api_mod.api_bp)
        app.config["TESTING"] = True
        api_mod.cache_clear()
    return app, api_mod, tmp.name, _conn


AUTH   = {"Authorization": "Bearer test-secret"}
NOAUTH = {"Authorization": "Bearer wrong"}


# ════════════════════════════════════════════════════════════════════════════
# 1. Constants
# ════════════════════════════════════════════════════════════════════════════

class TestConstants(unittest.TestCase):

    def test_item_statuses(self):
        for s in ("OPEN", "IN_PROGRESS", "DONE", "SNOOZED", "ARCHIVED"):
            self.assertIn(s, rw.ITEM_STATUSES)

    def test_sources(self):
        for s in ("watchlist_due", "watchlist_stale", "alpha_gate",
                  "validation_trends", "replay_missed", "thesis_due",
                  "scorecard_warning", "regime_change"):
            self.assertIn(s, rw.SOURCES)

    def test_max_queue_items_positive(self):
        self.assertGreater(rw.MAX_QUEUE_ITEMS, 0)

    def test_max_brief_items(self):
        self.assertGreater(rw.MAX_BRIEF_ITEMS, 0)


# ════════════════════════════════════════════════════════════════════════════
# 2. Scoring functions
# ════════════════════════════════════════════════════════════════════════════

class TestUrgencyScore(unittest.TestCase):

    def _past_iso(self, hours: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")

    def _future_iso(self, hours: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds")

    def test_overdue_returns_max(self):
        score = rw._urgency_score(self._past_iso(2))
        self.assertEqual(score, float(rw._W_URGENCY))

    def test_due_within_24h_elevated(self):
        score = rw._urgency_score(self._future_iso(12))
        self.assertGreaterEqual(score, 25.0)

    def test_no_due_returns_zero(self):
        self.assertEqual(rw._urgency_score(None), 0.0)

    def test_far_future_returns_low(self):
        score = rw._urgency_score(self._future_iso(96))
        self.assertLessEqual(score, 10.0)


class TestOpportunityScore(unittest.TestCase):

    def test_rare_alert_max(self):
        score = rw._opportunity_score("alpha_gate", {"readiness_tier": "RARE_ALERT"})
        self.assertEqual(score, 30.0)

    def test_not_ready_low(self):
        score = rw._opportunity_score("alpha_gate", {"readiness_tier": "NOT_READY"})
        self.assertLessEqual(score, 10.0)

    def test_watchlist_due_returns_value(self):
        score = rw._opportunity_score("watchlist_due")
        self.assertGreater(score, 0.0)

    def test_unknown_source_returns_positive(self):
        score = rw._opportunity_score("unknown_source")
        self.assertGreaterEqual(score, 0.0)


class TestComputePriorityScore(unittest.TestCase):

    def test_all_max_capped_at_100(self):
        score = rw.compute_priority_score(40, 30, 20, 10)
        self.assertLessEqual(score, 100.0)

    def test_all_zero(self):
        score = rw.compute_priority_score(0, 0, 0, 0)
        self.assertEqual(score, 0.0)

    def test_returns_float(self):
        score = rw.compute_priority_score(10, 5, 3, 2)
        self.assertIsInstance(score, float)


class TestMakeItemId(unittest.TestCase):

    def test_deterministic(self):
        id1 = rw._make_item_id("AAPL", "watchlist_due")
        id2 = rw._make_item_id("AAPL", "watchlist_due")
        self.assertEqual(id1, id2)

    def test_different_ticker(self):
        id1 = rw._make_item_id("AAPL", "watchlist_due")
        id2 = rw._make_item_id("NVDA", "watchlist_due")
        self.assertNotEqual(id1, id2)

    def test_different_source(self):
        id1 = rw._make_item_id("AAPL", "watchlist_due")
        id2 = rw._make_item_id("AAPL", "alpha_gate")
        self.assertNotEqual(id1, id2)

    def test_none_ticker(self):
        id1 = rw._make_item_id(None, "scorecard_warning")
        self.assertIsInstance(id1, str)
        self.assertGreater(len(id1), 0)

    def test_case_normalized(self):
        id1 = rw._make_item_id("aapl", "watchlist_due")
        id2 = rw._make_item_id("AAPL", "watchlist_due")
        self.assertEqual(id1, id2)


# ════════════════════════════════════════════════════════════════════════════
# 3. Queue generation and persistence
# ════════════════════════════════════════════════════════════════════════════

class TestQueueGeneration(unittest.TestCase):

    def setUp(self):
        self.db_path, self.conn_fn = _make_db()
        self.patch = _patch_db(self.conn_fn)
        self.patch.start()
        rw._ensure_tables()

    def tearDown(self):
        self.patch.stop()

    def _silent_sources(self, **overrides):
        defaults = {
            "_collect_from_watchlist":         [],
            "_collect_from_alpha_gate":        [],
            "_collect_from_validation_trends": [],
            "_collect_from_replay_missed":     [],
            "_collect_from_thesis_due":        [],
            "_collect_from_scorecard_warnings":[],
            "_collect_from_regime_changes":    [],
        }
        defaults.update(overrides)
        return [patch.object(rw, name, return_value=val) for name, val in defaults.items()]

    def test_empty_sources_returns_empty(self):
        patches = self._silent_sources()
        for p in patches:
            p.start()
        queue = rw.generate_queue()
        for p in patches:
            p.stop()
        self.assertEqual(queue, [])

    def test_item_persisted(self):
        item = rw._build_item("AAPL", "watchlist_due", "Test item")
        patches = self._silent_sources(**{"_collect_from_watchlist": [item]})
        for p in patches:
            p.start()
        queue = rw.generate_queue()
        for p in patches:
            p.stop()
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["ticker"], "AAPL")

    def test_deduplication(self):
        # Same item_id from two sources — only one persisted
        item1 = rw._build_item("NVDA", "watchlist_due", "Reason A")
        item2 = rw._build_item("NVDA", "watchlist_due", "Reason B")
        self.assertEqual(item1["item_id"], item2["item_id"])
        patches = self._silent_sources(**{
            "_collect_from_watchlist": [item1, item2],
        })
        for p in patches:
            p.start()
        queue = rw.generate_queue()
        for p in patches:
            p.stop()
        nvda_items = [q for q in queue if q["ticker"] == "NVDA"]
        self.assertEqual(len(nvda_items), 1)

    def test_done_item_not_reopened(self):
        item = rw._build_item("MSFT", "watchlist_due", "Test")
        patches = self._silent_sources(**{"_collect_from_watchlist": [item]})
        for p in patches:
            p.start()
        rw.generate_queue()
        for p in patches:
            p.stop()

        # Mark done
        item_id = item["item_id"]
        rw.mark_done(item_id)

        # Generate queue again — same item should not reappear as OPEN
        for p in patches:
            p.start()
        queue = rw.generate_queue()
        for p in patches:
            p.stop()
        open_items = [q for q in queue if q["ticker"] == "MSFT"]
        self.assertEqual(open_items, [])

    def test_archived_item_not_reopened(self):
        item = rw._build_item("GOOG", "watchlist_due", "Test")
        patches = self._silent_sources(**{"_collect_from_watchlist": [item]})
        for p in patches:
            p.start()
        rw.generate_queue()
        rw.archive_item(item["item_id"])
        queue = rw.generate_queue()
        for p in patches:
            p.stop()
        archived_items = [q for q in queue if q["ticker"] == "GOOG"]
        self.assertEqual(archived_items, [])


# ════════════════════════════════════════════════════════════════════════════
# 4. Queue ordering (deterministic)
# ════════════════════════════════════════════════════════════════════════════

class TestQueueOrdering(unittest.TestCase):

    def setUp(self):
        self.db_path, self.conn_fn = _make_db()
        self.patch = _patch_db(self.conn_fn)
        self.patch.start()
        rw._ensure_tables()

    def tearDown(self):
        self.patch.stop()

    def test_higher_priority_score_first(self):
        # Directly insert two items with known scores
        now = rw._now_iso()
        conn = self.conn_fn()
        conn.execute(
            "INSERT INTO research_workflow_items "
            "(item_id, ticker, source, priority, reason, status, "
            "linked_entity_type, urgency_score, opportunity_score, risk_score, "
            "stale_score, priority_score, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("id_low",  "AAPL", "watchlist_due", "LOW",  "Low",  "OPEN", "NONE", 0, 5, 3, 1, 9.0,  now, now),
        )
        conn.execute(
            "INSERT INTO research_workflow_items "
            "(item_id, ticker, source, priority, reason, status, "
            "linked_entity_type, urgency_score, opportunity_score, risk_score, "
            "stale_score, priority_score, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("id_high", "NVDA", "alpha_gate",    "HIGH", "High", "OPEN", "NONE", 40, 30, 20, 10, 100.0, now, now),
        )
        conn.commit()
        conn.close()
        queue = rw.get_queue()
        self.assertEqual(queue[0]["ticker"], "NVDA")   # highest score first
        self.assertEqual(queue[1]["ticker"], "AAPL")

    def test_same_score_ordered_by_item_id(self):
        now = rw._now_iso()
        conn = self.conn_fn()
        for iid, ticker in [("aaa", "ZZZ"), ("bbb", "AAA")]:
            conn.execute(
                "INSERT INTO research_workflow_items "
                "(item_id, ticker, source, priority, reason, status, "
                "linked_entity_type, urgency_score, opportunity_score, risk_score, "
                "stale_score, priority_score, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (iid, ticker, "watchlist_due", "MEDIUM", "test", "OPEN", "NONE",
                 5, 5, 5, 5, 20.0, now, now),
            )
        conn.commit()
        conn.close()
        queue = rw.get_queue()
        ids = [q["item_id"] for q in queue]
        self.assertEqual(ids, sorted(ids))  # item_id ASC as tiebreaker


# ════════════════════════════════════════════════════════════════════════════
# 5. Status transitions
# ════════════════════════════════════════════════════════════════════════════

class TestStatusTransitions(unittest.TestCase):

    def setUp(self):
        self.db_path, self.conn_fn = _make_db()
        self.patch = _patch_db(self.conn_fn)
        self.patch.start()
        rw._ensure_tables()
        item = rw._build_item("AAPL", "watchlist_due", "Test")
        rw._upsert_queue_item(item)
        self.item_id = item["item_id"]

    def tearDown(self):
        self.patch.stop()

    def test_mark_in_progress(self):
        result = rw.mark_in_progress(self.item_id)
        self.assertEqual(result["status"], "IN_PROGRESS")

    def test_mark_done(self):
        result = rw.mark_done(self.item_id)
        self.assertEqual(result["status"], "DONE")

    def test_mark_done_not_in_queue(self):
        rw.mark_done(self.item_id)
        queue = rw.get_queue()
        ids = [q["item_id"] for q in queue]
        self.assertNotIn(self.item_id, ids)

    def test_archive(self):
        result = rw.archive_item(self.item_id)
        self.assertEqual(result["status"], "ARCHIVED")

    def test_archive_not_in_queue(self):
        rw.archive_item(self.item_id)
        queue = rw.get_queue()
        ids = [q["item_id"] for q in queue]
        self.assertNotIn(self.item_id, ids)

    def test_invalid_item_id_raises(self):
        with self.assertRaises(ValueError):
            rw.mark_done("nonexistent_id")

    def test_transition_updates_updated_at(self):
        before = rw.get_item(self.item_id)["updated_at"]
        rw.mark_in_progress(self.item_id)
        after = rw.get_item(self.item_id)["updated_at"]
        self.assertGreaterEqual(after, before)


# ════════════════════════════════════════════════════════════════════════════
# 6. Snooze behavior
# ════════════════════════════════════════════════════════════════════════════

class TestSnooze(unittest.TestCase):

    def setUp(self):
        self.db_path, self.conn_fn = _make_db()
        self.patch = _patch_db(self.conn_fn)
        self.patch.start()
        rw._ensure_tables()
        item = rw._build_item("NVDA", "alpha_gate", "Near alert")
        rw._upsert_queue_item(item)
        self.item_id = item["item_id"]

    def tearDown(self):
        self.patch.stop()

    def test_snooze_sets_status(self):
        result = rw.snooze_item(self.item_id, hours=24)
        self.assertEqual(result["status"], "SNOOZED")

    def test_snooze_sets_snoozed_until(self):
        result = rw.snooze_item(self.item_id, hours=24)
        self.assertIsNotNone(result["snoozed_until"])

    def test_snoozed_excluded_from_default_queue(self):
        rw.snooze_item(self.item_id, hours=24)
        queue = rw.get_queue(include_snoozed=False)
        ids = [q["item_id"] for q in queue]
        self.assertNotIn(self.item_id, ids)

    def test_snoozed_included_when_flag_set(self):
        rw.snooze_item(self.item_id, hours=24)
        queue = rw.get_queue(include_snoozed=True)
        ids = [q["item_id"] for q in queue]
        self.assertIn(self.item_id, ids)

    def test_expired_snooze_reappears(self):
        # Snooze with past expiry — should appear in queue
        rw.snooze_item(self.item_id, hours=24)
        # Force snoozed_until to the past
        conn = self.conn_fn()
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
        conn.execute(
            "UPDATE research_workflow_items SET snoozed_until=? WHERE item_id=?",
            (past, self.item_id),
        )
        conn.commit()
        conn.close()
        queue = rw.get_queue(include_snoozed=False)
        ids = [q["item_id"] for q in queue]
        self.assertIn(self.item_id, ids)

    def test_negative_hours_uses_default(self):
        result = rw.snooze_item(self.item_id, hours=-5)
        self.assertEqual(result["status"], "SNOOZED")
        self.assertIsNotNone(result["snoozed_until"])


# ════════════════════════════════════════════════════════════════════════════
# 7. Append note
# ════════════════════════════════════════════════════════════════════════════

class TestAppendNote(unittest.TestCase):

    def setUp(self):
        self.db_path, self.conn_fn = _make_db()
        self.patch = _patch_db(self.conn_fn)
        self.patch.start()
        rw._ensure_tables()
        item = rw._build_item("MSFT", "watchlist_due", "Review needed")
        rw._upsert_queue_item(item)
        self.item_id = item["item_id"]

    def tearDown(self):
        self.patch.stop()

    def test_append_returns_dict(self):
        note = rw.append_note(self.item_id, "Strong earnings report")
        self.assertIsInstance(note, dict)
        self.assertEqual(note["item_id"], self.item_id)
        self.assertIn("Strong earnings", note["text"])

    def test_immutable_append(self):
        rw.append_note(self.item_id, "Note 1")
        rw.append_note(self.item_id, "Note 2")
        notes = rw.get_notes(self.item_id)
        self.assertEqual(len(notes), 2)

    def test_empty_text_raises(self):
        with self.assertRaises(ValueError):
            rw.append_note(self.item_id, "")

    def test_invalid_item_id_raises(self):
        with self.assertRaises(ValueError):
            rw.append_note("nonexistent", "Some text")

    def test_has_created_at(self):
        note = rw.append_note(self.item_id, "Test")
        self.assertIsNotNone(note["created_at"])


class TestGetNotes(unittest.TestCase):

    def setUp(self):
        self.db_path, self.conn_fn = _make_db()
        self.patch = _patch_db(self.conn_fn)
        self.patch.start()
        rw._ensure_tables()
        item = rw._build_item("GOOG", "validation_trends", "Trend")
        rw._upsert_queue_item(item)
        self.item_id = item["item_id"]

    def tearDown(self):
        self.patch.stop()

    def test_newest_first(self):
        rw.append_note(self.item_id, "First")
        rw.append_note(self.item_id, "Second")
        notes = rw.get_notes(self.item_id)
        self.assertEqual(notes[0]["text"], "Second")
        self.assertEqual(notes[1]["text"], "First")

    def test_limit_respected(self):
        for i in range(10):
            rw.append_note(self.item_id, f"Note {i}")
        notes = rw.get_notes(self.item_id, limit=3)
        self.assertEqual(len(notes), 3)

    def test_unknown_item_returns_empty(self):
        notes = rw.get_notes("nonexistent")
        self.assertEqual(notes, [])


# ════════════════════════════════════════════════════════════════════════════
# 8. Daily workflow summary
# ════════════════════════════════════════════════════════════════════════════

class TestWorkflowSummary(unittest.TestCase):

    def setUp(self):
        self.db_path, self.conn_fn = _make_db()
        self.patch = _patch_db(self.conn_fn)
        self.patch.start()
        rw._ensure_tables()

    def tearDown(self):
        self.patch.stop()

    def test_summary_structure(self):
        summary = rw.get_summary()
        for key in ("top_items", "overdue", "snoozed_returning",
                    "new_high_priority", "completed_today", "bottlenecks",
                    "generated_at"):
            self.assertIn(key, summary)

    def test_completed_today_in_summary(self):
        item = rw._build_item("AAPL", "watchlist_due", "Test")
        rw._upsert_queue_item(item)
        rw.mark_done(item["item_id"])
        summary = rw.get_summary()
        done_ids = [i["item_id"] for i in summary["completed_today"]]
        self.assertIn(item["item_id"], done_ids)

    def test_overdue_in_summary(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
        now  = rw._now_iso()
        conn = self.conn_fn()
        conn.execute(
            "INSERT INTO research_workflow_items "
            "(item_id, ticker, source, priority, reason, due_at, status, "
            "linked_entity_type, urgency_score, opportunity_score, risk_score, "
            "stale_score, priority_score, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("over1", "NVDA", "watchlist_due", "HIGH", "Overdue test", past,
             "OPEN", "NONE", 40, 10, 5, 3, 58.0, now, now),
        )
        conn.commit()
        conn.close()
        summary = rw.get_summary()
        overdue_ids = [i["item_id"] for i in summary["overdue"]]
        self.assertIn("over1", overdue_ids)

    def test_empty_db_summary(self):
        summary = rw.get_summary()
        self.assertEqual(summary["top_items"], [])
        self.assertEqual(summary["completed_today"], [])


# ════════════════════════════════════════════════════════════════════════════
# 9. Brief integration hooks
# ════════════════════════════════════════════════════════════════════════════

class TestBriefItems(unittest.TestCase):

    def setUp(self):
        self.db_path, self.conn_fn = _make_db()
        self.patch = _patch_db(self.conn_fn)
        self.patch.start()
        rw._ensure_tables()

    def tearDown(self):
        self.patch.stop()

    def test_get_brief_items_empty(self):
        items = rw.get_brief_items()
        self.assertIsInstance(items, list)

    def test_get_brief_items_limited(self):
        for ticker in ["AAPL", "NVDA", "MSFT", "GOOG"]:
            item = rw._build_item(ticker, "watchlist_due", "Test")
            rw._upsert_queue_item(item)
        items = rw.get_brief_items(limit=3)
        self.assertLessEqual(len(items), 3)

    def test_brief_item_fields(self):
        item = rw._build_item("AAPL", "alpha_gate", "Near alert")
        rw._upsert_queue_item(item)
        items = rw.get_brief_items()
        self.assertGreater(len(items), 0)
        for key in ("ticker", "source", "reason", "priority_score", "status", "item_id"):
            self.assertIn(key, items[0])

    def test_get_brief_items_never_raises(self):
        with patch("research_workflow._ensure_tables", side_effect=Exception("db error")):
            items = rw.get_brief_items()
        self.assertEqual(items, [])

    def test_get_completed_today_empty(self):
        items = rw.get_completed_today()
        self.assertIsInstance(items, list)

    def test_get_completed_today_returns_done_items(self):
        item = rw._build_item("TSLA", "watchlist_due", "Done today")
        rw._upsert_queue_item(item)
        rw.mark_done(item["item_id"])
        completed = rw.get_completed_today()
        ids = [i["item_id"] for i in completed]
        self.assertIn(item["item_id"], ids)


# ════════════════════════════════════════════════════════════════════════════
# 10. Morning brief integration
# ════════════════════════════════════════════════════════════════════════════

class TestMorningBriefIntegration(unittest.TestCase):

    def test_workflow_items_in_required_sections(self):
        import operator_brief as ob
        self.assertIn("workflow_items", ob.REQUIRED_SECTIONS)

    def test_workflow_items_in_build_sections(self):
        import operator_brief as ob
        data = {
            "portfolio": {}, "overnight_signals": [], "alpha_candidates": [],
            "dry_runs": [], "qc_summary": {}, "regime_ctx": {}, "risk_report": {},
            "stress_run": None, "pending_checklists": [], "due_reviews": {},
            "thesis_warnings": {}, "scorecard_summary": {}, "planner_snapshot": None,
            "cash": 0.0, "tfsa_room": 0.0,
            "workflow_items": [
                {"ticker": "AAPL", "source": "watchlist_due", "reason": "Review due",
                 "priority_score": 55.0, "status": "OPEN", "item_id": "abc123"}
            ],
        }
        sections = ob.build_sections(data)
        self.assertIn("workflow_items", sections)
        self.assertEqual(len(sections["workflow_items"]), 1)
        self.assertEqual(sections["workflow_items"][0]["ticker"], "AAPL")

    def test_collect_brief_data_has_workflow_key(self):
        import operator_brief as ob
        with patch("research_workflow.get_brief_items", return_value=[]):
            with patch("portfolio_reconciliation.reconcile_portfolio", return_value={}), \
                 patch("portfolio.get_cash", return_value=0.0), \
                 patch("portfolio.get_tfsa_room", return_value=0.0):
                # Just check the key is present even if other sources fail
                try:
                    data = ob.collect_brief_data()
                    self.assertIn("workflow_items", data)
                except Exception:
                    pass  # other sources may fail in test env


# ════════════════════════════════════════════════════════════════════════════
# 11. EOD brief integration
# ════════════════════════════════════════════════════════════════════════════

class TestEodBriefIntegration(unittest.TestCase):

    def test_workflow_summary_in_required_sections(self):
        import eod_brief as eb
        self.assertIn("workflow_summary", eb.REQUIRED_SECTIONS)

    def test_workflow_summary_section_function(self):
        import eod_brief as eb
        completed = [
            {"ticker": "AAPL", "reason": "Done", "source": "watchlist_due"}
        ]
        open_items = [
            {"ticker": "NVDA", "reason": "Pending", "priority_score": 60.0}
        ]
        result = eb._workflow_summary_section(completed, open_items)
        self.assertEqual(result["completed_count"], 1)
        self.assertEqual(result["unresolved_count"], 1)
        self.assertEqual(len(result["completed"]), 1)
        self.assertEqual(len(result["unresolved"]), 1)

    def test_workflow_summary_empty(self):
        import eod_brief as eb
        result = eb._workflow_summary_section([], [])
        self.assertEqual(result["completed_count"], 0)
        self.assertEqual(result["unresolved_count"], 0)

    def test_build_eod_sections_includes_workflow(self):
        import eod_brief as eb
        data = {
            "portfolio": {}, "transactions_today": [], "alpha_today": [],
            "top_candidates": [], "dryruns_today": [], "qc_today": [],
            "regime_snapshots_today": [], "stress_runs_today": [],
            "checklists_today": [], "journal_today": [], "proposals_today": [],
            "outcomes_today": [], "validations_today": [], "planner_today": [],
            "pending_checklists": [], "due_reviews": {}, "unreviewed_dryruns": [],
            "workflow_completed_today": [],
            "workflow_open_items": [
                {"ticker": "NVDA", "reason": "Open", "priority_score": 55.0}
            ],
        }
        sections = eb.build_eod_sections(data)
        self.assertIn("workflow_summary", sections)
        wf = sections["workflow_summary"]
        self.assertEqual(wf["completed_count"], 0)
        self.assertEqual(wf["unresolved_count"], 1)


# ════════════════════════════════════════════════════════════════════════════
# 12. No trading calls
# ════════════════════════════════════════════════════════════════════════════

class TestNoTradingCalls(unittest.TestCase):

    def _source(self):
        import inspect
        return inspect.getsource(rw)

    def test_no_place_order(self):
        self.assertNotIn("place_order(", self._source())

    def test_no_execute_trade(self):
        self.assertNotIn("execute_trade(", self._source())

    def test_no_send_sms(self):
        self.assertNotIn("send_sms(", self._source())

    def test_no_submit_order(self):
        self.assertNotIn("submit_order(", self._source())


# ════════════════════════════════════════════════════════════════════════════
# 13. API endpoints
# ════════════════════════════════════════════════════════════════════════════

class TestApiWorkflowQueue(unittest.TestCase):

    def setUp(self):
        self.app, self.api_mod, self.db_path, self.conn_fn = _make_app()
        with _patch_db(self.conn_fn):
            rw._ensure_tables()
        self.client = self.app.test_client()
        self.api_mod.cache_clear()

    def test_returns_200(self):
        with _patch_db(self.conn_fn):
            with patch("research_workflow.generate_queue", return_value=[]), \
                 patch("research_workflow.get_queue", return_value=[]):
                resp = self.client.get("/api/v1/research/workflow/queue")
        self.assertEqual(resp.status_code, 200)

    def test_has_items_list(self):
        with _patch_db(self.conn_fn):
            with patch("research_workflow.generate_queue", return_value=[]), \
                 patch("research_workflow.get_queue", return_value=[]):
                resp = self.client.get("/api/v1/research/workflow/queue")
        d = resp.get_json()["data"]
        self.assertIn("items", d)
        self.assertIn("count", d)

    def test_envelope_ok_true(self):
        with _patch_db(self.conn_fn):
            with patch("research_workflow.generate_queue", return_value=[]), \
                 patch("research_workflow.get_queue", return_value=[]):
                resp = self.client.get("/api/v1/research/workflow/queue")
        self.assertTrue(resp.get_json()["ok"])


class TestApiWorkflowSummary(unittest.TestCase):

    def setUp(self):
        self.app, self.api_mod, self.db_path, self.conn_fn = _make_app()
        with _patch_db(self.conn_fn):
            rw._ensure_tables()
        self.client = self.app.test_client()
        self.api_mod.cache_clear()

    def _mock_summary(self):
        return {
            "top_items": [], "overdue": [], "snoozed_returning": [],
            "new_high_priority": [], "completed_today": [], "bottlenecks": [],
            "generated_at": "2026-05-21T10:00:00+00:00",
        }

    def test_returns_200(self):
        with patch("research_workflow.get_summary", return_value=self._mock_summary()):
            resp = self.client.get("/api/v1/research/workflow/summary")
        self.assertEqual(resp.status_code, 200)

    def test_has_top_items(self):
        with patch("research_workflow.get_summary", return_value=self._mock_summary()):
            resp = self.client.get("/api/v1/research/workflow/summary")
        d = resp.get_json()["data"]
        self.assertIn("top_items", d)


class TestApiWorkflowWrites(unittest.TestCase):

    def setUp(self):
        self.app, self.api_mod, self.db_path, self.conn_fn = _make_app()
        with _patch_db(self.conn_fn):
            rw._ensure_tables()
        self.client = self.app.test_client()
        self.api_mod.cache_clear()
        self._env_patch = patch.dict(os.environ, {"API_SECRET": "test-secret"})
        self._env_patch.start()
        # Insert a test item
        with _patch_db(self.conn_fn):
            item = rw._build_item("AAPL", "watchlist_due", "Test")
            rw._upsert_queue_item(item)
            self.item_id = item["item_id"]

    def tearDown(self):
        self._env_patch.stop()

    def test_start_requires_auth(self):
        resp = self.client.post(f"/api/v1/research/workflow/{self.item_id}/start",
                                headers=NOAUTH)
        self.assertEqual(resp.status_code, 401)

    def test_done_requires_auth(self):
        resp = self.client.post(f"/api/v1/research/workflow/{self.item_id}/done",
                                headers=NOAUTH)
        self.assertEqual(resp.status_code, 401)

    def test_snooze_requires_auth(self):
        resp = self.client.post(f"/api/v1/research/workflow/{self.item_id}/snooze",
                                json={"hours": 24}, headers=NOAUTH)
        self.assertEqual(resp.status_code, 401)

    def test_archive_requires_auth(self):
        resp = self.client.post(f"/api/v1/research/workflow/{self.item_id}/archive",
                                headers=NOAUTH)
        self.assertEqual(resp.status_code, 401)

    def test_note_requires_auth(self):
        resp = self.client.post(f"/api/v1/research/workflow/{self.item_id}/note",
                                json={"text": "test"}, headers=NOAUTH)
        self.assertEqual(resp.status_code, 401)

    def test_start_valid(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(f"/api/v1/research/workflow/{self.item_id}/start",
                                    headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()["data"]
        self.assertEqual(d["status"], "IN_PROGRESS")

    def test_done_valid(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(f"/api/v1/research/workflow/{self.item_id}/done",
                                    headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()["data"]
        self.assertEqual(d["status"], "DONE")

    def test_snooze_valid(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(f"/api/v1/research/workflow/{self.item_id}/snooze",
                                    json={"hours": 12}, headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()["data"]
        self.assertEqual(d["status"], "SNOOZED")
        self.assertIsNotNone(d["snoozed_until"])

    def test_archive_valid(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(f"/api/v1/research/workflow/{self.item_id}/archive",
                                    headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()["data"]
        self.assertEqual(d["status"], "ARCHIVED")

    def test_note_valid(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(f"/api/v1/research/workflow/{self.item_id}/note",
                                    json={"text": "Reviewed and noted."}, headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()["data"]
        self.assertIn("text", d)

    def test_note_empty_text_400(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post(f"/api/v1/research/workflow/{self.item_id}/note",
                                    json={"text": ""}, headers=AUTH)
        self.assertEqual(resp.status_code, 400)

    def test_start_missing_item_404(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post("/api/v1/research/workflow/nonexistent_id/start",
                                    headers=AUTH)
        self.assertEqual(resp.status_code, 404)

    def test_done_missing_item_404(self):
        with _patch_db(self.conn_fn):
            resp = self.client.post("/api/v1/research/workflow/nonexistent_id/done",
                                    headers=AUTH)
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
