"""
Phase A13 — Tests for position_journal.py and related API endpoints.

Covers:
  - validate_thesis / validate_journal_entry (pure functions)
  - upsert_thesis / get_thesis / get_all_theses / get_thesis_summaries
  - add_journal_entry / get_journal_entries
  - get_due_reviews / get_thesis_warnings / get_review_summary
  - Safety: no delete from theses/journal, no broker/trading calls
  - API: GET /portfolio/thesis, GET /portfolio/thesis/<ticker>,
         POST /portfolio/thesis/<ticker>/upsert,
         POST /portfolio/thesis/<ticker>/journal,
         GET /portfolio/reviews
  - GET /portfolio enrichment with thesis_summary
"""
import inspect
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import database
import position_journal as pj

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_get_conn(db_path: str):
    def _get_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    return _get_conn


def _db(tmp_path):
    db_path = str(tmp_path / "test_a13.db")
    conn = sqlite3.connect(db_path)
    # create position_theses
    conn.execute("""
        CREATE TABLE IF NOT EXISTS position_theses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL UNIQUE,
            thesis_title TEXT NOT NULL DEFAULT '',
            thesis_text TEXT NOT NULL DEFAULT '',
            setup_type TEXT NOT NULL DEFAULT '',
            conviction_level TEXT NOT NULL DEFAULT 'MEDIUM',
            time_horizon TEXT NOT NULL DEFAULT 'MEDIUM',
            entry_reason TEXT NOT NULL DEFAULT '',
            expected_catalysts TEXT NOT NULL DEFAULT '',
            risk_factors TEXT NOT NULL DEFAULT '',
            invalidation_level REAL,
            target_level REAL,
            exit_plan TEXT NOT NULL DEFAULT '',
            review_frequency_days INTEGER NOT NULL DEFAULT 30,
            next_review_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    # create position_journal
    conn.execute("""
        CREATE TABLE IF NOT EXISTS position_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            confidence_change TEXT,
            created_at TEXT NOT NULL
        )
    """)
    # create manual_portfolio_positions for warnings tests
    conn.execute("""
        CREATE TABLE IF NOT EXISTS manual_portfolio_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL UNIQUE,
            quantity REAL NOT NULL,
            avg_cost REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
    monkeypatch.setattr(pj, "_ensure_tables", lambda: None)
    return db_path


# ── validate_thesis (pure) ────────────────────────────────────────────────────

class TestValidateThesis:
    def test_valid_defaults(self):
        assert pj.validate_thesis("AAPL") == []

    def test_valid_all_combos(self):
        for cl in ("LOW", "MEDIUM", "HIGH"):
            for th in ("SHORT", "MEDIUM", "LONG"):
                for st in ("ACTIVE", "WATCH", "CLOSED", "ARCHIVED"):
                    assert pj.validate_thesis("X", cl, th, st) == []

    def test_missing_ticker(self):
        errs = pj.validate_thesis("")
        assert "MISSING_TICKER" in errs

    def test_invalid_conviction_level(self):
        errs = pj.validate_thesis("AAPL", conviction_level="ULTRA")
        assert any("INVALID_CONVICTION_LEVEL" in e for e in errs)

    def test_invalid_time_horizon(self):
        errs = pj.validate_thesis("AAPL", time_horizon="FOREVER")
        assert any("INVALID_TIME_HORIZON" in e for e in errs)

    def test_invalid_status(self):
        errs = pj.validate_thesis("AAPL", status="DELETED")
        assert any("INVALID_STATUS" in e for e in errs)

    def test_multiple_errors(self):
        errs = pj.validate_thesis("", conviction_level="X", time_horizon="Y", status="Z")
        assert len(errs) == 4

    def test_none_ticker(self):
        errs = pj.validate_thesis(None)
        assert "MISSING_TICKER" in errs

    def test_whitespace_ticker(self):
        errs = pj.validate_thesis("   ")
        assert "MISSING_TICKER" in errs


# ── validate_journal_entry (pure) ─────────────────────────────────────────────

class TestValidateJournalEntry:
    def test_valid(self):
        assert pj.validate_journal_entry("AAPL", "NOTE", "Some text") == []

    def test_all_valid_types(self):
        for t in ("NOTE", "REVIEW", "THESIS_UPDATE", "RISK_UPDATE",
                  "CATALYST_UPDATE", "EXIT_PLAN_UPDATE"):
            assert pj.validate_journal_entry("AAPL", t, "x") == []

    def test_missing_ticker(self):
        errs = pj.validate_journal_entry("", "NOTE", "text")
        assert "MISSING_TICKER" in errs

    def test_invalid_entry_type(self):
        errs = pj.validate_journal_entry("AAPL", "BOGUS", "text")
        assert any("INVALID_ENTRY_TYPE" in e for e in errs)

    def test_empty_text(self):
        errs = pj.validate_journal_entry("AAPL", "NOTE", "")
        assert "EMPTY_TEXT" in errs

    def test_whitespace_text(self):
        errs = pj.validate_journal_entry("AAPL", "NOTE", "   ")
        assert "EMPTY_TEXT" in errs

    def test_multiple_errors(self):
        errs = pj.validate_journal_entry("", "BOGUS", "")
        assert len(errs) == 3


# ── upsert_thesis ─────────────────────────────────────────────────────────────

class TestUpsertThesis:
    def test_basic_insert(self, db):
        result = pj.upsert_thesis("AAPL", thesis_title="Apple growth play")
        assert result["ok"] is True
        assert result["ticker"] == "AAPL"
        assert result["thesis"] is not None

    def test_round_trip(self, db):
        pj.upsert_thesis("SHOP.TO", thesis_title="Shopify ecommerce")
        thesis = pj.get_thesis("SHOP.TO")
        assert thesis is not None
        assert thesis["thesis_title"] == "Shopify ecommerce"
        assert thesis["ticker"] == "SHOP.TO"

    def test_ticker_normalized_to_uppercase(self, db):
        pj.upsert_thesis("nvda", thesis_title="GPU play")
        thesis = pj.get_thesis("NVDA")
        assert thesis is not None

    def test_created_at_preserved_on_update(self, db):
        pj.upsert_thesis("AAPL", thesis_title="v1")
        t1 = pj.get_thesis("AAPL")
        pj.upsert_thesis("AAPL", thesis_title="v2")
        t2 = pj.get_thesis("AAPL")
        assert t1["created_at"] == t2["created_at"]

    def test_update_changes_title(self, db):
        pj.upsert_thesis("AAPL", thesis_title="old")
        pj.upsert_thesis("AAPL", thesis_title="new")
        thesis = pj.get_thesis("AAPL")
        assert thesis["thesis_title"] == "new"

    def test_update_appends_thesis_update_journal(self, db):
        pj.upsert_thesis("AAPL", thesis_title="v1")
        pj.upsert_thesis("AAPL", thesis_title="v2")
        entries = pj.get_journal_entries("AAPL")
        assert any(e["entry_type"] == "THESIS_UPDATE" for e in entries)

    def test_first_insert_no_auto_journal(self, db):
        pj.upsert_thesis("META", thesis_title="fresh")
        entries = pj.get_journal_entries("META")
        assert all(e["entry_type"] != "THESIS_UPDATE" for e in entries)

    def test_default_next_review_computed(self, db):
        pj.upsert_thesis("AAPL", review_frequency_days=14)
        thesis = pj.get_thesis("AAPL")
        expected = (datetime.now() + timedelta(days=14)).date().isoformat()
        assert thesis["next_review_at"][:10] == expected

    def test_explicit_next_review_honored(self, db):
        future = "2030-01-01T00:00:00"
        pj.upsert_thesis("AAPL", next_review_at=future)
        thesis = pj.get_thesis("AAPL")
        assert thesis["next_review_at"] == future

    def test_validation_errors_returned(self, db):
        result = pj.upsert_thesis("", conviction_level="WRONG")
        assert result["ok"] is False
        assert result["errors"]

    def test_archived_status_accepted(self, db):
        pj.upsert_thesis("AAPL", status="ARCHIVED")
        thesis = pj.get_thesis("AAPL")
        assert thesis["status"] == "ARCHIVED"

    def test_all_optional_fields_stored(self, db):
        pj.upsert_thesis(
            "AAPL",
            thesis_text="Long form text",
            setup_type="BREAKOUT",
            entry_reason="RSI dip",
            expected_catalysts="Earnings",
            risk_factors="Rate hikes",
            invalidation_level=140.0,
            target_level=220.0,
            exit_plan="Sell half at 200",
        )
        t = pj.get_thesis("AAPL")
        assert t["thesis_text"] == "Long form text"
        assert t["invalidation_level"] == 140.0
        assert t["target_level"] == 220.0
        assert t["exit_plan"] == "Sell half at 200"


# ── get_thesis / get_all_theses ───────────────────────────────────────────────

class TestGetThesis:
    def test_missing_returns_none(self, db):
        assert pj.get_thesis("NONEXISTENT") is None

    def test_lowercase_normalized(self, db):
        pj.upsert_thesis("AAPL")
        assert pj.get_thesis("aapl") is not None

    def test_get_all_empty(self, db):
        assert pj.get_all_theses() == []

    def test_get_all_ordered_by_ticker(self, db):
        for t in ("ZZZZZ", "AAAAA", "MMMMM"):
            pj.upsert_thesis(t)
        tickers = [t["ticker"] for t in pj.get_all_theses()]
        assert tickers == sorted(tickers)

    def test_get_all_status_filter(self, db):
        pj.upsert_thesis("AAPL", status="ACTIVE")
        pj.upsert_thesis("MSFT", status="WATCH")
        pj.upsert_thesis("GOOG", status="ARCHIVED")
        active = pj.get_all_theses(status="ACTIVE")
        assert all(t["status"] == "ACTIVE" for t in active)
        archived = pj.get_all_theses(status="ARCHIVED")
        assert len(archived) == 1

    def test_get_all_no_filter_returns_all(self, db):
        for s in ("ACTIVE", "WATCH", "CLOSED", "ARCHIVED"):
            pj.upsert_thesis(s, status=s)
        assert len(pj.get_all_theses()) == 4


# ── get_thesis_summaries ──────────────────────────────────────────────────────

class TestGetThesisSummaries:
    def test_empty_tickers_returns_empty(self, db):
        assert pj.get_thesis_summaries([]) == {}

    def test_missing_ticker_returns_none(self, db):
        result = pj.get_thesis_summaries(["UNKNOWN"])
        assert result["UNKNOWN"] is None

    def test_existing_ticker_has_summary(self, db):
        pj.upsert_thesis("AAPL", thesis_title="Apple", exit_plan="Sell on weakness",
                          conviction_level="HIGH", time_horizon="LONG")
        result = pj.get_thesis_summaries(["AAPL"])
        s = result["AAPL"]
        assert s is not None
        assert s["conviction_level"] == "HIGH"
        assert s["time_horizon"] == "LONG"
        assert s["has_exit_plan"] is True
        assert s["thesis_title"] == "Apple"

    def test_is_stale_false_for_new(self, db):
        pj.upsert_thesis("AAPL")
        result = pj.get_thesis_summaries(["AAPL"])
        assert result["AAPL"]["is_stale"] is False

    def test_is_stale_true_for_old(self, db, monkeypatch):
        pj.upsert_thesis("AAPL")
        # Manually back-date updated_at
        conn = sqlite3.connect(db)
        old_time = (datetime.now() - timedelta(days=91)).isoformat()
        conn.execute("UPDATE position_theses SET updated_at=? WHERE ticker='AAPL'", (old_time,))
        conn.commit()
        conn.close()
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db))
        result = pj.get_thesis_summaries(["AAPL"])
        assert result["AAPL"]["is_stale"] is True

    def test_mixed_tickers(self, db):
        pj.upsert_thesis("AAPL")
        result = pj.get_thesis_summaries(["AAPL", "NVDA"])
        assert result["AAPL"] is not None
        assert result["NVDA"] is None


# ── add_journal_entry ─────────────────────────────────────────────────────────

class TestAddJournalEntry:
    def test_basic_note(self, db):
        pj.upsert_thesis("AAPL")
        result = pj.add_journal_entry("AAPL", "NOTE", "Price action looks bullish")
        assert result["ok"] is True
        assert result["entry"]["entry_type"] == "NOTE"

    def test_entry_stored(self, db):
        pj.upsert_thesis("AAPL")
        pj.add_journal_entry("AAPL", "NOTE", "First note")
        entries = pj.get_journal_entries("AAPL")
        assert len(entries) >= 1
        assert entries[0]["text"] == "First note"

    def test_review_advances_next_review_at(self, db):
        pj.upsert_thesis("AAPL", review_frequency_days=7)
        before = pj.get_thesis("AAPL")["next_review_at"]
        pj.add_journal_entry("AAPL", "REVIEW", "Reviewed everything")
        after = pj.get_thesis("AAPL")["next_review_at"]
        assert after > before

    def test_non_review_does_not_advance(self, db):
        pj.upsert_thesis("AAPL")
        before = pj.get_thesis("AAPL")["next_review_at"]
        pj.add_journal_entry("AAPL", "NOTE", "Just a note")
        after = pj.get_thesis("AAPL")["next_review_at"]
        assert after == before

    def test_validation_errors(self, db):
        result = pj.add_journal_entry("", "BOGUS", "")
        assert result["ok"] is False
        assert result["errors"]

    def test_tags_stored_as_json(self, db):
        pj.upsert_thesis("AAPL")
        pj.add_journal_entry("AAPL", "NOTE", "Tagged", tags=["bullish", "earnings"])
        entries = pj.get_journal_entries("AAPL")
        tags = json.loads(entries[0]["tags_json"])
        assert "bullish" in tags

    def test_confidence_change_stored(self, db):
        pj.upsert_thesis("AAPL")
        pj.add_journal_entry("AAPL", "NOTE", "Changed conviction",
                              confidence_change="LOW->HIGH")
        entries = pj.get_journal_entries("AAPL")
        assert entries[0]["confidence_change"] == "LOW->HIGH"

    def test_review_on_no_thesis_does_not_crash(self, db):
        result = pj.add_journal_entry("GHOST", "REVIEW", "Review for ghost ticker")
        assert result["ok"] is True


# ── get_journal_entries ───────────────────────────────────────────────────────

class TestGetJournalEntries:
    def test_ordered_desc(self, db):
        pj.upsert_thesis("AAPL")
        for i in range(3):
            pj.add_journal_entry("AAPL", "NOTE", f"entry {i}")
        entries = pj.get_journal_entries("AAPL")
        times = [e["created_at"] for e in entries]
        assert times == sorted(times, reverse=True)

    def test_limit_honored(self, db):
        pj.upsert_thesis("AAPL")
        for i in range(10):
            pj.add_journal_entry("AAPL", "NOTE", f"entry {i}")
        assert len(pj.get_journal_entries("AAPL", limit=3)) == 3

    def test_empty_returns_empty_list(self, db):
        assert pj.get_journal_entries("NOBODY") == []


# ── get_due_reviews ───────────────────────────────────────────────────────────

class TestGetDueReviews:
    def test_no_theses_returns_empty(self, db):
        result = pj.get_due_reviews()
        assert result["due"] == []
        assert result["due_count"] == 0

    def test_past_review_date_is_due(self, db):
        past = (datetime.now() - timedelta(days=1)).isoformat()
        pj.upsert_thesis("AAPL", next_review_at=past)
        result = pj.get_due_reviews()
        assert result["due_count"] == 1
        assert result["due"][0]["ticker"] == "AAPL"

    def test_overdue_more_than_7_days(self, db):
        overdue = (datetime.now() - timedelta(days=8)).isoformat()
        pj.upsert_thesis("AAPL", next_review_at=overdue)
        result = pj.get_due_reviews()
        assert result["overdue_count"] == 1

    def test_upcoming_within_7_days(self, db):
        upcoming = (datetime.now() + timedelta(days=3)).isoformat()
        pj.upsert_thesis("AAPL", next_review_at=upcoming)
        result = pj.get_due_reviews()
        assert result["upcoming_count"] == 1
        assert result["due_count"] == 0

    def test_future_beyond_7_days_ignored(self, db):
        far_future = (datetime.now() + timedelta(days=30)).isoformat()
        pj.upsert_thesis("AAPL", next_review_at=far_future)
        result = pj.get_due_reviews()
        assert result["due_count"] == 0
        assert result["upcoming_count"] == 0

    def test_archived_status_excluded(self, db):
        past = (datetime.now() - timedelta(days=1)).isoformat()
        pj.upsert_thesis("AAPL", next_review_at=past, status="ARCHIVED")
        result = pj.get_due_reviews()
        assert result["due_count"] == 0

    def test_closed_status_excluded(self, db):
        past = (datetime.now() - timedelta(days=1)).isoformat()
        pj.upsert_thesis("AAPL", next_review_at=past, status="CLOSED")
        result = pj.get_due_reviews()
        assert result["due_count"] == 0

    def test_watch_status_included(self, db):
        past = (datetime.now() - timedelta(days=1)).isoformat()
        pj.upsert_thesis("AAPL", next_review_at=past, status="WATCH")
        result = pj.get_due_reviews()
        assert result["due_count"] == 1


# ── get_thesis_warnings ───────────────────────────────────────────────────────

class TestGetThesisWarnings:
    def _add_manual_position(self, db, ticker):
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT OR REPLACE INTO manual_portfolio_positions "
            "(ticker, quantity, avg_cost, active) VALUES (?,?,?,1)",
            (ticker, 10, 100),
        )
        conn.commit()
        conn.close()

    def test_missing_thesis_detected(self, db, monkeypatch):
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db))
        self._add_manual_position(db, "AAPL")
        result = pj.get_thesis_warnings()
        assert "AAPL" in result["missing_thesis"]
        assert result["has_warnings"] is True

    def test_no_missing_when_thesis_exists(self, db, monkeypatch):
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db))
        self._add_manual_position(db, "AAPL")
        pj.upsert_thesis("AAPL", exit_plan="Sell at target")
        result = pj.get_thesis_warnings()
        assert "AAPL" not in result["missing_thesis"]

    def test_missing_exit_plan_detected(self, db, monkeypatch):
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db))
        pj.upsert_thesis("AAPL", exit_plan="")
        result = pj.get_thesis_warnings()
        assert "AAPL" in result["missing_exit_plan"]

    def test_stale_thesis_detected(self, db, monkeypatch):
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db))
        pj.upsert_thesis("AAPL")
        old = (datetime.now() - timedelta(days=91)).isoformat()
        conn = sqlite3.connect(db)
        conn.execute("UPDATE position_theses SET updated_at=? WHERE ticker='AAPL'", (old,))
        conn.commit()
        conn.close()
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db))
        result = pj.get_thesis_warnings()
        assert "AAPL" in result["stale_thesis"]

    def test_no_warnings_returns_false(self, db):
        result = pj.get_thesis_warnings()
        assert result["has_warnings"] is False


# ── get_review_summary ────────────────────────────────────────────────────────

class TestGetReviewSummary:
    def test_structure(self, db):
        result = pj.get_review_summary()
        assert "reviews" in result
        assert "warnings" in result
        assert "due_count" in result["reviews"]
        assert "missing_thesis" in result["warnings"]


# ── Safety tests ──────────────────────────────────────────────────────────────

class TestSafetyConstraints:
    def test_no_delete_from_position_theses(self):
        source = inspect.getsource(pj)
        assert "DELETE FROM position_theses" not in source

    def test_no_delete_from_position_journal(self):
        source = inspect.getsource(pj)
        assert "DELETE FROM position_journal" not in source

    def test_archive_is_only_removal_path(self):
        source = inspect.getsource(pj)
        assert "ARCHIVED" in source

    def test_no_broker_calls(self):
        source = inspect.getsource(pj)
        for pattern in ("place_order(", "submit_order(", "broker_client", "wealthsimple_api"):
            assert pattern not in source

    def test_no_trading_operations(self):
        source = inspect.getsource(pj)
        for pattern in ("record_buy_trade", "reduce_or_remove_holding",
                        "add_or_update_holding"):
            assert pattern not in source

    def test_append_only_journal(self):
        source = inspect.getsource(pj)
        assert "UPDATE position_journal" not in source
        assert "DELETE FROM position_journal" not in source


# ── API fixture ───────────────────────────────────────────────────────────────

@pytest.fixture
def flask_app(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
    monkeypatch.setattr(pj, "_ensure_tables", lambda: None)

    from flask import Flask
    import api as api_mod
    api_mod.cache_clear()

    flask_test_app = Flask("test_a13")
    flask_test_app.register_blueprint(api_mod.api_bp)
    flask_test_app.config["TESTING"] = True

    return flask_test_app, db_path


# ── API: GET /portfolio/thesis ────────────────────────────────────────────────

class TestApiThesisList:
    def test_empty(self, flask_app):
        app, _ = flask_app
        with app.test_client() as c:
            r = c.get("/api/v1/portfolio/thesis")
            assert r.status_code == 200
            data = r.get_json()
            assert data["ok"] is True
            assert data["data"]["theses"] == []

    def test_with_theses(self, flask_app):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        pj.upsert_thesis("AAPL")
        pj.upsert_thesis("MSFT")
        with app.test_client() as c:
            r = c.get("/api/v1/portfolio/thesis")
            assert r.status_code == 200
            theses = r.get_json()["data"]["theses"]
            assert len(theses) == 2

    def test_status_filter(self, flask_app):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        pj.upsert_thesis("AAPL", status="ACTIVE")
        pj.upsert_thesis("MSFT", status="ARCHIVED")
        with app.test_client() as c:
            r = c.get("/api/v1/portfolio/thesis?status=ACTIVE")
            data = r.get_json()["data"]["theses"]
            assert all(t["status"] == "ACTIVE" for t in data)


# ── API: GET /portfolio/thesis/<ticker> ───────────────────────────────────────

class TestApiThesisGet:
    def test_not_found(self, flask_app):
        app, _ = flask_app
        with app.test_client() as c:
            r = c.get("/api/v1/portfolio/thesis/GHOST")
            assert r.status_code == 404

    def test_found(self, flask_app):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        pj.upsert_thesis("NVDA", thesis_title="GPU play")
        with app.test_client() as c:
            r = c.get("/api/v1/portfolio/thesis/NVDA")
            assert r.status_code == 200
            data = r.get_json()["data"]
            assert data["thesis"]["thesis_title"] == "GPU play"
            assert "journal" in data

    def test_lowercase_ticker_normalized(self, flask_app):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        pj.upsert_thesis("AAPL")
        with app.test_client() as c:
            r = c.get("/api/v1/portfolio/thesis/aapl")
            assert r.status_code == 200


# ── API: POST /portfolio/thesis/<ticker>/upsert ───────────────────────────────

class TestApiThesisUpsert:
    def test_auth_required(self, flask_app, monkeypatch):
        app, _ = flask_app
        monkeypatch.setenv("API_SECRET", "secret")
        with app.test_client() as c:
            r = c.post("/api/v1/portfolio/thesis/AAPL/upsert",
                       json={"thesis_title": "test"})
            assert r.status_code == 401

    def test_upsert_creates_thesis(self, flask_app, monkeypatch):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        monkeypatch.setenv("API_SECRET", "secret")
        with app.test_client() as c:
            r = c.post("/api/v1/portfolio/thesis/AAPL/upsert",
                       json={"thesis_title": "Apple AI play"},
                       headers={"Authorization": "Bearer secret"})
            assert r.status_code == 200
            assert r.get_json()["ok"] is True

    def test_invalid_conviction_level_returns_422(self, flask_app, monkeypatch):
        app, _ = flask_app
        monkeypatch.setenv("API_SECRET", "secret")
        with app.test_client() as c:
            r = c.post("/api/v1/portfolio/thesis/AAPL/upsert",
                       json={"conviction_level": "ULTRA"},
                       headers={"Authorization": "Bearer secret"})
            assert r.status_code == 422

    def test_invalid_numeric_field_returns_400(self, flask_app, monkeypatch):
        app, _ = flask_app
        monkeypatch.setenv("API_SECRET", "secret")
        with app.test_client() as c:
            r = c.post("/api/v1/portfolio/thesis/AAPL/upsert",
                       json={"invalidation_level": "notanumber"},
                       headers={"Authorization": "Bearer secret"})
            assert r.status_code == 400


# ── API: POST /portfolio/thesis/<ticker>/journal ──────────────────────────────

class TestApiThesisJournal:
    def test_auth_required(self, flask_app, monkeypatch):
        app, _ = flask_app
        monkeypatch.setenv("API_SECRET", "secret")
        with app.test_client() as c:
            r = c.post("/api/v1/portfolio/thesis/AAPL/journal",
                       json={"entry_type": "NOTE", "text": "hi"})
            assert r.status_code == 401

    def test_creates_entry(self, flask_app, monkeypatch):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        monkeypatch.setenv("API_SECRET", "secret")
        pj.upsert_thesis("AAPL")
        with app.test_client() as c:
            r = c.post("/api/v1/portfolio/thesis/AAPL/journal",
                       json={"entry_type": "NOTE", "text": "Earnings beat"},
                       headers={"Authorization": "Bearer secret"})
            assert r.status_code == 200
            assert r.get_json()["ok"] is True

    def test_invalid_entry_type_returns_422(self, flask_app, monkeypatch):
        app, _ = flask_app
        monkeypatch.setenv("API_SECRET", "secret")
        with app.test_client() as c:
            r = c.post("/api/v1/portfolio/thesis/AAPL/journal",
                       json={"entry_type": "BOGUS", "text": "some text"},
                       headers={"Authorization": "Bearer secret"})
            assert r.status_code == 422


# ── API: GET /portfolio/reviews ───────────────────────────────────────────────

class TestApiReviews:
    def test_structure(self, flask_app):
        app, _ = flask_app
        with app.test_client() as c:
            r = c.get("/api/v1/portfolio/reviews")
            assert r.status_code == 200
            data = r.get_json()["data"]
            assert "reviews" in data
            assert "warnings" in data

    def test_due_reviews_surfaced(self, flask_app):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)
        past = (datetime.now() - timedelta(days=2)).isoformat()
        pj.upsert_thesis("AAPL", next_review_at=past)
        with app.test_client() as c:
            r = c.get("/api/v1/portfolio/reviews")
            data = r.get_json()["data"]
            assert data["reviews"]["due_count"] >= 1


# ── GET /portfolio includes thesis_summary ────────────────────────────────────

class TestPortfolioThesisEnrichment:
    def test_thesis_summary_field_present(self, flask_app, monkeypatch):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)

        import portfolio_reconciliation as pr

        def _fake_canonical():
            return {
                "positions": [{"ticker": "AAPL", "quantity": 10, "market_price": 200}],
                "aggregates": {},
            }

        monkeypatch.setattr(pr, "get_canonical_portfolio", _fake_canonical)

        import api as api_mod
        api_mod.cache_clear()

        with app.test_client() as c:
            r = c.get("/api/v1/portfolio")
            assert r.status_code == 200
            positions = r.get_json()["data"]["positions"]
            assert len(positions) == 1
            assert "thesis_summary" in positions[0]

    def test_thesis_summary_none_when_no_thesis(self, flask_app, monkeypatch):
        app, db_path = flask_app
        import database as db_mod
        db_mod.get_connection = _make_get_conn(db_path)

        import portfolio_reconciliation as pr

        def _fake_canonical():
            return {
                "positions": [{"ticker": "NVDA", "quantity": 5, "market_price": 900}],
                "aggregates": {},
            }

        monkeypatch.setattr(pr, "get_canonical_portfolio", _fake_canonical)

        import api as api_mod
        api_mod.cache_clear()

        with app.test_client() as c:
            r = c.get("/api/v1/portfolio")
            assert r.status_code == 200
            positions = r.get_json()["data"]["positions"]
            assert positions[0]["thesis_summary"] is None
