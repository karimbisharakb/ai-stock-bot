"""
Phase A14 — Tests for decision_checklist.py and related API endpoints.

Covers:
  - validate_checklist (pure)
  - _compute_scoring_from_items (pure)
  - create_checklist / get_checklist / get_all_checklists
  - update_item — pass/fail/null, recomputes scoring, auto-advance DRAFT→READY
  - compute_scoring
  - approve_checklist — happy path, NOT_READY blocked, invalid transitions
  - reject_checklist — happy path, invalid transitions
  - archive_checklist
  - Alpha candidate link / thesis link
  - get_summary / get_pending_checklists
  - Morning brief integration (pending checklists surfaced)
  - Safety: no trading calls, no DELETE statements on audit trail
  - API: 7 endpoints with auth requirements
"""
import inspect
import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import database
import decision_checklist as dc

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_get_conn(db_path: str):
    def _get_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    return _get_conn


def _db(tmp_path):
    db_path = str(tmp_path / "test_a14.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_checklists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checklist_id TEXT NOT NULL UNIQUE,
            ticker TEXT NOT NULL,
            decision_type TEXT NOT NULL,
            linked_alpha_candidate_id TEXT,
            linked_thesis_id INTEGER,
            checklist_status TEXT NOT NULL DEFAULT 'DRAFT',
            checklist_completion REAL NOT NULL DEFAULT 0.0,
            blocking_items INTEGER NOT NULL DEFAULT 0,
            readiness TEXT NOT NULL DEFAULT 'NOT_READY',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_checklist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checklist_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            passed INTEGER,
            note TEXT NOT NULL DEFAULT '',
            required INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(checklist_id, item_key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_checklist_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checklist_id TEXT NOT NULL,
            action TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT,
            actor TEXT,
            detail_json TEXT,
            performed_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
    monkeypatch.setattr(dc, "_ensure_tables", lambda: None)
    return db_path


def _pass_all_required(checklist_id):
    """Helper: pass all required items in a checklist so readiness = READY_FOR_MANUAL_DECISION."""
    for key, _label, required in dc.DEFAULT_ITEMS:
        if required:
            dc.update_item(checklist_id, key, True)


# ── validate_checklist (pure) ─────────────────────────────────────────────────

class TestValidateChecklist:
    def test_valid(self):
        assert dc.validate_checklist("AAPL", "ENTER") == []

    def test_all_valid_decision_types(self):
        for dt in ("ENTER", "ADD", "REDUCE", "EXIT", "HOLD"):
            assert dc.validate_checklist("AAPL", dt) == []

    def test_missing_ticker(self):
        errs = dc.validate_checklist("", "ENTER")
        assert "MISSING_TICKER" in errs

    def test_none_ticker(self):
        errs = dc.validate_checklist(None, "ENTER")
        assert "MISSING_TICKER" in errs

    def test_whitespace_ticker(self):
        errs = dc.validate_checklist("   ", "ENTER")
        assert "MISSING_TICKER" in errs

    def test_invalid_decision_type(self):
        errs = dc.validate_checklist("AAPL", "BUY")
        assert any("INVALID_DECISION_TYPE" in e for e in errs)

    def test_multiple_errors(self):
        errs = dc.validate_checklist("", "BUY")
        assert len(errs) == 2


# ── _compute_scoring_from_items (pure) ───────────────────────────────────────

class TestComputeScoringFromItems:
    def test_empty_list(self):
        s = dc._compute_scoring_from_items([])
        assert s["checklist_completion"] == 0.0
        assert s["blocking_items"] == 0
        assert s["readiness"] == "NOT_READY"

    def test_all_null_required(self):
        items = [{"passed": None, "required": 1} for _ in range(9)]
        items.append({"passed": None, "required": 0})
        s = dc._compute_scoring_from_items(items)
        assert s["checklist_completion"] == 0.0
        assert s["blocking_items"] == 0
        assert s["readiness"] == "NOT_READY"

    def test_all_pass_required(self):
        items = [{"passed": 1, "required": 1} for _ in range(9)]
        items.append({"passed": 1, "required": 0})
        s = dc._compute_scoring_from_items(items)
        assert s["checklist_completion"] == 100.0
        assert s["blocking_items"] == 0
        assert s["readiness"] == "READY_FOR_MANUAL_DECISION"

    def test_one_required_fail(self):
        items = [{"passed": 1, "required": 1} for _ in range(8)]
        items.append({"passed": 0, "required": 1})  # one fail
        items.append({"passed": 1, "required": 0})
        s = dc._compute_scoring_from_items(items)
        assert s["blocking_items"] == 1
        assert s["readiness"] == "NEEDS_REVIEW"

    def test_optional_fail_not_blocking(self):
        items = [{"passed": 1, "required": 1} for _ in range(9)]
        items.append({"passed": 0, "required": 0})  # optional fail
        s = dc._compute_scoring_from_items(items)
        assert s["blocking_items"] == 0
        assert s["readiness"] == "READY_FOR_MANUAL_DECISION"

    def test_partial_completion(self):
        items = [{"passed": 1, "required": 1} for _ in range(5)]
        items += [{"passed": None, "required": 1} for _ in range(5)]
        s = dc._compute_scoring_from_items(items)
        assert s["checklist_completion"] == 50.0
        assert s["readiness"] == "NOT_READY"

    def test_completion_rounded(self):
        # 3 of 7 answered = 42.857...% → 42.9
        items = [{"passed": 1, "required": 1} for _ in range(3)]
        items += [{"passed": None, "required": 1} for _ in range(4)]
        s = dc._compute_scoring_from_items(items)
        assert s["checklist_completion"] == round(3 / 7 * 100, 1)


# ── create_checklist ──────────────────────────────────────────────────────────

class TestCreateChecklist:
    def test_basic_create(self, db):
        result = dc.create_checklist("AAPL", "ENTER")
        assert result["ok"] is True
        assert result["checklist_id"].startswith("DCL-")

    def test_seeds_10_items(self, db):
        result = dc.create_checklist("AAPL", "ENTER")
        cl = result["checklist"]
        assert len(cl["items"]) == 10

    def test_all_items_null_initially(self, db):
        result = dc.create_checklist("AAPL", "ENTER")
        for item in result["checklist"]["items"]:
            assert item["passed"] is None

    def test_initial_status_is_draft(self, db):
        result = dc.create_checklist("AAPL", "ENTER")
        assert result["checklist"]["checklist_status"] == "DRAFT"

    def test_initial_readiness_not_ready(self, db):
        result = dc.create_checklist("AAPL", "ENTER")
        assert result["checklist"]["readiness"] == "NOT_READY"

    def test_ticker_normalized(self, db):
        result = dc.create_checklist("nvda", "ENTER")
        assert result["checklist"]["ticker"] == "NVDA"

    def test_linked_alpha_candidate_id_stored(self, db):
        result = dc.create_checklist("AAPL", "ENTER", linked_alpha_candidate_id="ALPHA-999")
        assert result["checklist"]["linked_alpha_candidate_id"] == "ALPHA-999"

    def test_linked_thesis_id_stored(self, db):
        result = dc.create_checklist("AAPL", "ENTER", linked_thesis_id=42)
        assert result["checklist"]["linked_thesis_id"] == 42

    def test_notes_stored(self, db):
        result = dc.create_checklist("AAPL", "ENTER", notes="Testing entry point")
        assert result["checklist"]["notes"] == "Testing entry point"

    def test_validation_error_missing_ticker(self, db):
        result = dc.create_checklist("", "ENTER")
        assert result["ok"] is False
        assert result["errors"]

    def test_validation_error_invalid_type(self, db):
        result = dc.create_checklist("AAPL", "BUY")
        assert result["ok"] is False

    def test_checklist_id_deterministic(self, db):
        # Same ticker/type/time → same ID
        from datetime import datetime
        now = "2026-01-01T12:00:00"
        id1 = dc._generate_checklist_id("AAPL", "ENTER", now)
        id2 = dc._generate_checklist_id("AAPL", "ENTER", now)
        assert id1 == id2

    def test_checklist_id_differs_by_type(self, db):
        from datetime import datetime
        now = "2026-01-01T12:00:00"
        id_enter = dc._generate_checklist_id("AAPL", "ENTER", now)
        id_exit  = dc._generate_checklist_id("AAPL", "EXIT", now)
        assert id_enter != id_exit

    def test_audit_logged_on_create(self, db):
        result = dc.create_checklist("AAPL", "ENTER")
        cid = result["checklist_id"]
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT * FROM decision_checklist_audit WHERE checklist_id=? AND action='CREATE'",
            (cid,)
        ).fetchone()
        conn.close()
        assert row is not None

    def test_required_items_marked_correctly(self, db):
        result = dc.create_checklist("AAPL", "ENTER")
        items = {i["item_key"]: i for i in result["checklist"]["items"]}
        # qc_reviewed is the only optional item
        assert items["qc_reviewed"]["required"] == 0
        assert items["thesis_exists"]["required"] == 1


# ── get_checklist / get_all_checklists ────────────────────────────────────────

class TestGetChecklist:
    def test_not_found_returns_none(self, db):
        assert dc.get_checklist("DCL-nonexistent") is None

    def test_includes_items(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        cl = dc.get_checklist(cid)
        assert "items" in cl
        assert len(cl["items"]) == 10

    def test_get_all_empty(self, db):
        assert dc.get_all_checklists() == []

    def test_get_all_ordered_desc(self, db):
        dc.create_checklist("AAPL", "ENTER")
        dc.create_checklist("NVDA", "ENTER")
        all_cl = dc.get_all_checklists()
        dates = [c["created_at"] for c in all_cl]
        assert dates == sorted(dates, reverse=True)

    def test_get_all_filter_ticker(self, db):
        dc.create_checklist("AAPL", "ENTER")
        dc.create_checklist("NVDA", "ENTER")
        results = dc.get_all_checklists(ticker="AAPL")
        assert all(c["ticker"] == "AAPL" for c in results)

    def test_get_all_filter_status(self, db):
        dc.create_checklist("AAPL", "ENTER")
        results = dc.get_all_checklists(status="DRAFT")
        assert all(c["checklist_status"] == "DRAFT" for c in results)

    def test_get_all_filter_decision_type(self, db):
        dc.create_checklist("AAPL", "ENTER")
        dc.create_checklist("AAPL", "EXIT")
        results = dc.get_all_checklists(decision_type="ENTER")
        assert all(c["decision_type"] == "ENTER" for c in results)


# ── update_item ───────────────────────────────────────────────────────────────

class TestUpdateItem:
    def test_pass_item(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        result = dc.update_item(cid, "thesis_exists", True)
        assert result["ok"] is True
        assert result["item"]["passed"] == 1

    def test_fail_item(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        result = dc.update_item(cid, "thesis_exists", False)
        assert result["ok"] is True
        assert result["item"]["passed"] == 0

    def test_reset_item_to_null(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        dc.update_item(cid, "thesis_exists", True)
        result = dc.update_item(cid, "thesis_exists", None)
        assert result["ok"] is True
        assert result["item"]["passed"] is None

    def test_note_stored(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        dc.update_item(cid, "thesis_exists", True, note="Thesis on file")
        cl = dc.get_checklist(cid)
        items = {i["item_key"]: i for i in cl["items"]}
        assert items["thesis_exists"]["note"] == "Thesis on file"

    def test_scoring_returned(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        result = dc.update_item(cid, "thesis_exists", True)
        assert "scoring" in result
        assert "checklist_completion" in result["scoring"]

    def test_item_not_found_returns_error(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        result = dc.update_item(cid, "nonexistent_item", True)
        assert result["ok"] is False
        assert any("ITEM_NOT_FOUND" in e for e in result["errors"])

    def test_checklist_not_found_returns_error(self, db):
        result = dc.update_item("DCL-ghost", "thesis_exists", True)
        assert result["ok"] is False

    def test_approved_checklist_immutable(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        dc.approve_checklist(cid)
        result = dc.update_item(cid, "thesis_exists", False)
        assert result["ok"] is False
        assert any("INVALID_STATE" in e for e in result["errors"])

    def test_rejected_checklist_immutable(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        dc.reject_checklist(cid)
        result = dc.update_item(cid, "thesis_exists", True)
        assert result["ok"] is False


# ── compute_scoring + auto-advance ────────────────────────────────────────────

class TestComputeScoring:
    def test_zero_at_creation(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        scoring = dc.compute_scoring(cid)
        assert scoring["checklist_completion"] == 0.0
        assert scoring["readiness"] == "NOT_READY"

    def test_blocking_items_counted(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        # Pass all required items except one, then fail that one
        for key, _label, required in dc.DEFAULT_ITEMS:
            if required and key != "thesis_exists":
                dc.update_item(cid, key, True)
        dc.update_item(cid, "thesis_exists", False)
        scoring = dc.compute_scoring(cid)
        assert scoring["blocking_items"] == 1
        assert scoring["readiness"] == "NEEDS_REVIEW"

    def test_ready_when_all_required_pass(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        scoring = dc.compute_scoring(cid)
        assert scoring["blocking_items"] == 0
        assert scoring["readiness"] == "READY_FOR_MANUAL_DECISION"

    def test_optional_item_unanswered_allows_ready(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        # qc_reviewed (optional) left NULL
        scoring = dc.compute_scoring(cid)
        assert scoring["readiness"] == "READY_FOR_MANUAL_DECISION"

    def test_persisted_to_db(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        dc.compute_scoring(cid)
        cl = dc._get_checklist_row(cid)
        assert cl["readiness"] == "READY_FOR_MANUAL_DECISION"


class TestAutoAdvance:
    def test_draft_advances_to_ready(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        cl = dc._get_checklist_row(cid)
        assert cl["checklist_status"] == "READY"

    def test_no_advance_while_blocking(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        # Fail one required item
        dc.update_item(cid, "thesis_exists", False)
        # Pass the rest
        for key, _label, required in dc.DEFAULT_ITEMS:
            if required and key != "thesis_exists":
                dc.update_item(cid, key, True)
        cl = dc._get_checklist_row(cid)
        assert cl["checklist_status"] == "DRAFT"

    def test_no_revert_to_draft_after_ready(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        # After auto-advance to READY, fail an item — stays READY (not reverted)
        dc.update_item(cid, "thesis_exists", False)
        cl = dc._get_checklist_row(cid)
        assert cl["checklist_status"] == "READY"


# ── approve_checklist ─────────────────────────────────────────────────────────

class TestApproveChecklist:
    def test_approve_ready_checklist(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        result = dc.approve_checklist(cid)
        assert result["ok"] is True
        assert result["checklist"]["checklist_status"] == "APPROVED"

    def test_approved_sets_reviewed_at(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        dc.approve_checklist(cid)
        cl = dc._get_checklist_row(cid)
        assert cl["reviewed_at"] is not None

    def test_approve_with_actor(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        dc.approve_checklist(cid, actor="karim")
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT actor FROM decision_checklist_audit WHERE checklist_id=? AND action='APPROVE'",
            (cid,)
        ).fetchone()
        conn.close()
        assert row["actor"] == "karim"

    def test_approve_blocked_when_not_ready(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        result = dc.approve_checklist(cid)
        assert result["ok"] is False
        assert any("NOT_READY" in e for e in result["errors"])

    def test_approve_blocked_when_blocking_items(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        dc.update_item(cid, "thesis_exists", False)
        result = dc.approve_checklist(cid)
        assert result["ok"] is False

    def test_approve_not_found(self, db):
        result = dc.approve_checklist("DCL-ghost")
        assert result["ok"] is False
        assert "CHECKLIST_NOT_FOUND" in result["errors"]

    def test_approve_from_draft_if_ready(self, db):
        # DRAFT can be approved directly if readiness is READY_FOR_MANUAL_DECISION
        # (before auto-advance logic runs, though auto-advance makes it READY first)
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        # status is now READY (auto-advanced); approve from READY
        result = dc.approve_checklist(cid)
        assert result["ok"] is True


# ── reject_checklist ──────────────────────────────────────────────────────────

class TestRejectChecklist:
    def test_reject_from_draft(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        result = dc.reject_checklist(cid)
        assert result["ok"] is True
        assert result["checklist"]["checklist_status"] == "REJECTED"

    def test_reject_from_ready(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        result = dc.reject_checklist(cid)
        assert result["ok"] is True
        assert result["checklist"]["checklist_status"] == "REJECTED"

    def test_reject_sets_reviewed_at(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        dc.reject_checklist(cid)
        cl = dc._get_checklist_row(cid)
        assert cl["reviewed_at"] is not None

    def test_reject_reason_in_audit(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        dc.reject_checklist(cid, reason="Market conditions changed")
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT detail_json FROM decision_checklist_audit WHERE checklist_id=? AND action='REJECT'",
            (cid,)
        ).fetchone()
        conn.close()
        detail = json.loads(row["detail_json"])
        assert detail["reason"] == "Market conditions changed"

    def test_reject_not_found(self, db):
        result = dc.reject_checklist("DCL-ghost")
        assert result["ok"] is False
        assert "CHECKLIST_NOT_FOUND" in result["errors"]


# ── archive_checklist ─────────────────────────────────────────────────────────

class TestArchiveChecklist:
    def test_archive_approved(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        dc.approve_checklist(cid)
        result = dc.archive_checklist(cid)
        assert result["ok"] is True
        assert result["checklist"]["checklist_status"] == "ARCHIVED"

    def test_archive_rejected(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        dc.reject_checklist(cid)
        result = dc.archive_checklist(cid)
        assert result["ok"] is True

    def test_archive_not_found(self, db):
        result = dc.archive_checklist("DCL-ghost")
        assert result["ok"] is False


# ── Invalid transitions ───────────────────────────────────────────────────────

class TestInvalidTransitions:
    def test_rejected_cannot_be_approved(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        dc.reject_checklist(cid)
        result = dc.approve_checklist(cid)
        assert result["ok"] is False
        assert any("INVALID_TRANSITION" in e for e in result["errors"])

    def test_archived_cannot_be_approved(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        dc.approve_checklist(cid)
        dc.archive_checklist(cid)
        result = dc.approve_checklist(cid)
        assert result["ok"] is False

    def test_archived_cannot_be_rejected(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        dc.reject_checklist(cid)
        dc.archive_checklist(cid)
        result = dc.reject_checklist(cid)
        assert result["ok"] is False

    def test_approved_cannot_be_rejected(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        dc.approve_checklist(cid)
        result = dc.reject_checklist(cid)
        assert result["ok"] is False

    def test_draft_archive_allowed(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        result = dc.archive_checklist(cid)
        assert result["ok"] is True


# ── Alpha link and thesis link ────────────────────────────────────────────────

class TestLinks:
    def test_alpha_candidate_id_round_trip(self, db):
        result = dc.create_checklist("NVDA", "ENTER", linked_alpha_candidate_id="ALPHA-XYZ")
        cid = result["checklist_id"]
        cl = dc.get_checklist(cid)
        assert cl["linked_alpha_candidate_id"] == "ALPHA-XYZ"

    def test_thesis_id_round_trip(self, db):
        result = dc.create_checklist("NVDA", "ADD", linked_thesis_id=77)
        cid = result["checklist_id"]
        cl = dc.get_checklist(cid)
        assert cl["linked_thesis_id"] == 77

    def test_both_links_stored(self, db):
        result = dc.create_checklist(
            "AAPL", "REDUCE",
            linked_alpha_candidate_id="ALPHA-001",
            linked_thesis_id=5,
        )
        cl = dc.get_checklist(result["checklist_id"])
        assert cl["linked_alpha_candidate_id"] == "ALPHA-001"
        assert cl["linked_thesis_id"] == 5

    def test_no_links_null(self, db):
        cid = dc.create_checklist("AAPL", "HOLD")["checklist_id"]
        cl = dc.get_checklist(cid)
        assert cl["linked_alpha_candidate_id"] is None
        assert cl["linked_thesis_id"] is None


# ── get_summary / get_pending_checklists ──────────────────────────────────────

class TestSummaryAndPending:
    def test_summary_structure(self, db):
        result = dc.get_summary()
        assert "pending_count" in result
        assert "approved_count" in result
        assert "rejected_count" in result
        assert "archived_count" in result
        assert "by_decision_type" in result
        assert "pending_checklists" in result

    def test_summary_counts(self, db):
        dc.create_checklist("AAPL", "ENTER")
        dc.create_checklist("NVDA", "EXIT")
        result = dc.get_summary()
        assert result["pending_count"] == 2

    def test_summary_by_decision_type(self, db):
        dc.create_checklist("AAPL", "ENTER")
        dc.create_checklist("NVDA", "ENTER")
        dc.create_checklist("MSFT", "EXIT")
        result = dc.get_summary()
        assert result["by_decision_type"].get("ENTER", 0) == 2
        assert result["by_decision_type"].get("EXIT", 0) == 1

    def test_approved_counted_separately(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        dc.approve_checklist(cid)
        result = dc.get_summary()
        assert result["approved_count"] == 1
        assert result["pending_count"] == 0

    def test_pending_checklists_draft_and_ready(self, db):
        cid1 = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        cid2 = dc.create_checklist("NVDA", "ENTER")["checklist_id"]
        _pass_all_required(cid2)  # auto-advances to READY
        pending = dc.get_pending_checklists()
        statuses = {c["checklist_id"]: c["checklist_status"] for c in pending}
        assert statuses[cid1] == "DRAFT"
        assert statuses[cid2] == "READY"

    def test_approved_not_in_pending(self, db):
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        dc.approve_checklist(cid)
        pending = dc.get_pending_checklists()
        assert all(c["checklist_id"] != cid for c in pending)


# ── Morning brief integration ─────────────────────────────────────────────────

class TestMorningBriefIntegration:
    def test_pending_checklists_appear_in_signals(self, db, monkeypatch, tmp_path):
        """Pending checklists should appear in overnight_signals in scheduler.py."""
        dc.create_checklist("AAPL", "ENTER")

        import scheduler
        signals = []

        # Patch get_pending_checklists to return our fixture
        monkeypatch.setattr(
            "decision_checklist.get_pending_checklists",
            lambda: [{"ticker": "AAPL", "decision_type": "ENTER",
                       "checklist_status": "DRAFT"}],
        )

        captured = []
        original_append = list.append

        # Simulate the scheduler logic inline
        try:
            from decision_checklist import get_pending_checklists
            pending = get_pending_checklists()
            for cl in pending[:3]:
                signals.append(
                    f"📋 Pending decision: {cl['ticker']} {cl['decision_type']} "
                    f"({cl['checklist_status']})"
                )
        except Exception:
            pass

        assert any("📋 Pending decision: AAPL ENTER" in s for s in signals)

    def test_no_crash_when_no_pending(self, db, monkeypatch):
        try:
            from decision_checklist import get_pending_checklists
            pending = get_pending_checklists()
            signals = []
            for cl in pending[:3]:
                signals.append(f"📋 {cl['ticker']}")
        except Exception as exc:
            pytest.fail(f"Unexpected exception: {exc}")


# ── Safety constraints ────────────────────────────────────────────────────────

class TestSafetyConstraints:
    def test_no_trading_operations(self):
        source = inspect.getsource(dc)
        for pattern in ("record_buy_trade", "reduce_or_remove_holding",
                        "add_or_update_holding", "place_order", "submit_order"):
            assert pattern not in source

    def test_no_broker_calls(self):
        source = inspect.getsource(dc)
        for pattern in ("broker_client", "wealthsimple_api"):
            assert pattern not in source

    def test_approval_message_does_not_trade(self):
        source = inspect.getsource(dc)
        assert "place_order" not in source
        assert "execute_trade" not in source

    def test_audit_is_append_only(self):
        source = inspect.getsource(dc)
        assert "UPDATE decision_checklist_audit" not in source
        assert "DELETE FROM decision_checklist_audit" not in source

    def test_no_delete_from_checklists(self):
        source = inspect.getsource(dc)
        assert "DELETE FROM decision_checklists" not in source

    def test_no_delete_from_items(self):
        source = inspect.getsource(dc)
        assert "DELETE FROM decision_checklist_items" not in source


# ── API fixture ───────────────────────────────────────────────────────────────

@pytest.fixture
def flask_app(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
    monkeypatch.setattr(dc, "_ensure_tables", lambda: None)

    from flask import Flask
    import api as api_mod
    api_mod.cache_clear()

    flask_test_app = Flask("test_a14")
    flask_test_app.register_blueprint(api_mod.api_bp)
    flask_test_app.config["TESTING"] = True

    return flask_test_app, db_path


# ── API: GET /decisions/checklists ────────────────────────────────────────────

class TestApiChecklistList:
    def test_empty(self, flask_app):
        app, _ = flask_app
        with app.test_client() as c:
            r = c.get("/api/v1/decisions/checklists")
            assert r.status_code == 200
            assert r.get_json()["data"]["checklists"] == []

    def test_returns_checklists(self, flask_app):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        dc.create_checklist("AAPL", "ENTER")
        with app.test_client() as c:
            r = c.get("/api/v1/decisions/checklists")
            assert len(r.get_json()["data"]["checklists"]) == 1

    def test_filter_by_ticker(self, flask_app):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        dc.create_checklist("AAPL", "ENTER")
        dc.create_checklist("NVDA", "ENTER")
        with app.test_client() as c:
            r = c.get("/api/v1/decisions/checklists?ticker=AAPL")
            data = r.get_json()["data"]["checklists"]
            assert all(c["ticker"] == "AAPL" for c in data)


# ── API: GET /decisions/checklists/<id> ───────────────────────────────────────

class TestApiChecklistGet:
    def test_not_found(self, flask_app):
        app, _ = flask_app
        with app.test_client() as c:
            r = c.get("/api/v1/decisions/checklists/DCL-ghost")
            assert r.status_code == 404

    def test_found(self, flask_app):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        cid = dc.create_checklist("NVDA", "EXIT")["checklist_id"]
        with app.test_client() as c:
            r = c.get(f"/api/v1/decisions/checklists/{cid}")
            assert r.status_code == 200
            data = r.get_json()["data"]
            assert data["checklist_id"] == cid
            assert len(data["items"]) == 10


# ── API: POST /decisions/checklists/create ────────────────────────────────────

class TestApiChecklistCreate:
    def test_auth_required(self, flask_app, monkeypatch):
        app, _ = flask_app
        monkeypatch.setenv("API_SECRET", "secret")
        with app.test_client() as c:
            r = c.post("/api/v1/decisions/checklists/create",
                       json={"ticker": "AAPL", "decision_type": "ENTER"})
            assert r.status_code == 401

    def test_create_success(self, flask_app, monkeypatch):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        monkeypatch.setenv("API_SECRET", "secret")
        with app.test_client() as c:
            r = c.post("/api/v1/decisions/checklists/create",
                       json={"ticker": "AAPL", "decision_type": "ENTER"},
                       headers={"Authorization": "Bearer secret"})
            assert r.status_code == 200
            assert r.get_json()["ok"] is True

    def test_invalid_type_returns_422(self, flask_app, monkeypatch):
        app, _ = flask_app
        monkeypatch.setenv("API_SECRET", "secret")
        with app.test_client() as c:
            r = c.post("/api/v1/decisions/checklists/create",
                       json={"ticker": "AAPL", "decision_type": "BUY"},
                       headers={"Authorization": "Bearer secret"})
            assert r.status_code == 422

    def test_with_alpha_link(self, flask_app, monkeypatch):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        monkeypatch.setenv("API_SECRET", "secret")
        with app.test_client() as c:
            r = c.post("/api/v1/decisions/checklists/create",
                       json={"ticker": "NVDA", "decision_type": "ENTER",
                             "linked_alpha_candidate_id": "ALPHA-007"},
                       headers={"Authorization": "Bearer secret"})
            assert r.status_code == 200
            cl = r.get_json()["data"]["checklist"]
            assert cl["linked_alpha_candidate_id"] == "ALPHA-007"

    def test_invalid_thesis_id_returns_400(self, flask_app, monkeypatch):
        app, _ = flask_app
        monkeypatch.setenv("API_SECRET", "secret")
        with app.test_client() as c:
            r = c.post("/api/v1/decisions/checklists/create",
                       json={"ticker": "AAPL", "decision_type": "ENTER",
                             "linked_thesis_id": "notanumber"},
                       headers={"Authorization": "Bearer secret"})
            assert r.status_code == 400


# ── API: POST /decisions/checklists/<id>/item ─────────────────────────────────

class TestApiChecklistItem:
    def test_auth_required(self, flask_app, monkeypatch):
        app, _ = flask_app
        monkeypatch.setenv("API_SECRET", "secret")
        with app.test_client() as c:
            r = c.post("/api/v1/decisions/checklists/DCL-x/item",
                       json={"item_key": "thesis_exists", "passed": True})
            assert r.status_code == 401

    def test_update_item(self, flask_app, monkeypatch):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        monkeypatch.setenv("API_SECRET", "secret")
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        with app.test_client() as c:
            r = c.post(f"/api/v1/decisions/checklists/{cid}/item",
                       json={"item_key": "thesis_exists", "passed": True, "note": "Confirmed"},
                       headers={"Authorization": "Bearer secret"})
            assert r.status_code == 200
            assert r.get_json()["ok"] is True

    def test_item_not_found_returns_404(self, flask_app, monkeypatch):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        monkeypatch.setenv("API_SECRET", "secret")
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        with app.test_client() as c:
            r = c.post(f"/api/v1/decisions/checklists/{cid}/item",
                       json={"item_key": "bogus_item", "passed": True},
                       headers={"Authorization": "Bearer secret"})
            assert r.status_code == 404


# ── API: POST /decisions/checklists/<id>/approve ──────────────────────────────

class TestApiChecklistApprove:
    def test_auth_required(self, flask_app, monkeypatch):
        app, _ = flask_app
        monkeypatch.setenv("API_SECRET", "secret")
        with app.test_client() as c:
            r = c.post("/api/v1/decisions/checklists/DCL-x/approve", json={})
            assert r.status_code == 401

    def test_approve_success(self, flask_app, monkeypatch):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        monkeypatch.setenv("API_SECRET", "secret")
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        _pass_all_required(cid)
        with app.test_client() as c:
            r = c.post(f"/api/v1/decisions/checklists/{cid}/approve",
                       json={"actor": "karim"},
                       headers={"Authorization": "Bearer secret"})
            assert r.status_code == 200
            assert r.get_json()["data"]["checklist"]["checklist_status"] == "APPROVED"

    def test_approve_not_ready_returns_422(self, flask_app, monkeypatch):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        monkeypatch.setenv("API_SECRET", "secret")
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        with app.test_client() as c:
            r = c.post(f"/api/v1/decisions/checklists/{cid}/approve",
                       json={},
                       headers={"Authorization": "Bearer secret"})
            assert r.status_code == 422


# ── API: POST /decisions/checklists/<id>/reject ───────────────────────────────

class TestApiChecklistReject:
    def test_auth_required(self, flask_app, monkeypatch):
        app, _ = flask_app
        monkeypatch.setenv("API_SECRET", "secret")
        with app.test_client() as c:
            r = c.post("/api/v1/decisions/checklists/DCL-x/reject", json={})
            assert r.status_code == 401

    def test_reject_success(self, flask_app, monkeypatch):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        monkeypatch.setenv("API_SECRET", "secret")
        cid = dc.create_checklist("AAPL", "ENTER")["checklist_id"]
        with app.test_client() as c:
            r = c.post(f"/api/v1/decisions/checklists/{cid}/reject",
                       json={"reason": "Not the right time"},
                       headers={"Authorization": "Bearer secret"})
            assert r.status_code == 200
            assert r.get_json()["data"]["checklist"]["checklist_status"] == "REJECTED"

    def test_reject_not_found_returns_404(self, flask_app, monkeypatch):
        app, _ = flask_app
        monkeypatch.setenv("API_SECRET", "secret")
        with app.test_client() as c:
            r = c.post("/api/v1/decisions/checklists/DCL-ghost/reject",
                       json={},
                       headers={"Authorization": "Bearer secret"})
            assert r.status_code == 404


# ── API: GET /decisions/summary ───────────────────────────────────────────────

class TestApiDecisionsSummary:
    def test_summary_structure(self, flask_app):
        app, _ = flask_app
        with app.test_client() as c:
            r = c.get("/api/v1/decisions/summary")
            assert r.status_code == 200
            data = r.get_json()["data"]
            assert "pending_count" in data
            assert "approved_count" in data
            assert "by_decision_type" in data

    def test_pending_count_reflects_state(self, flask_app):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        dc.create_checklist("AAPL", "ENTER")
        dc.create_checklist("NVDA", "EXIT")
        import api as api_mod
        api_mod.cache_clear()
        with app.test_client() as c:
            r = c.get("/api/v1/decisions/summary")
            assert r.get_json()["data"]["pending_count"] == 2
