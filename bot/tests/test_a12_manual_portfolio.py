"""
Phase A12 — Manual portfolio control and account setup tests.

Covers:
  - validate_position: all invalid inputs rejected
  - validate_account_settings: all invalid inputs rejected
  - upsert_position: insert, update, re-activate, validation failures
  - deactivate_position: mark inactive, not-found error, no delete
  - get_manual_positions: active-only vs include_inactive
  - get_account_settings / update_account_settings: partial updates, validation
  - get_manual_portfolio: combined view
  - reconcile_manual: canonical positions updated, snapshot taken, cash synced
  - inactive positions excluded from reconcile
  - audit log: append-only, every write logged
  - no trading calls (source check)
  - no destructive deletes in module source
  - API endpoints: GET /portfolio/manual, POST upsert, POST deactivate,
                   POST /portfolio/manual/account, POST /portfolio/reconcile/manual
  - auth required for all write endpoints
"""
import inspect
import json
import os
import sqlite3
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import database
import manual_portfolio as mp


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _make_get_conn(db_path: str):
    def _get_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    return _get_conn


def _init_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS manual_portfolio_positions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker       TEXT    NOT NULL UNIQUE,
            quantity     REAL    NOT NULL,
            avg_cost     REAL    NOT NULL,
            realized_pnl REAL    NOT NULL DEFAULT 0.0,
            account_type TEXT    NOT NULL DEFAULT 'TFSA',
            currency     TEXT    NOT NULL DEFAULT 'CAD',
            note         TEXT    NOT NULL DEFAULT '',
            active       INTEGER NOT NULL DEFAULT 1,
            created_at   TEXT    NOT NULL,
            updated_at   TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS manual_account_settings (
            id                INTEGER PRIMARY KEY CHECK (id = 1),
            account_name      TEXT    NOT NULL DEFAULT '',
            account_type      TEXT    NOT NULL DEFAULT 'TFSA',
            base_currency     TEXT    NOT NULL DEFAULT 'CAD',
            available_cash    REAL    NOT NULL DEFAULT 0.0,
            contribution_room REAL,
            notes             TEXT    NOT NULL DEFAULT '',
            updated_at        TEXT    NOT NULL
        );
        INSERT OR IGNORE INTO manual_account_settings
            (id, account_name, account_type, base_currency, available_cash,
             contribution_room, notes, updated_at)
        VALUES (1,'','TFSA','CAD',0.0,NULL,'','2024-01-01T00:00:00');
        CREATE TABLE IF NOT EXISTS manual_portfolio_audit_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            action         TEXT    NOT NULL,
            subject        TEXT    NOT NULL,
            old_value_json TEXT,
            new_value_json TEXT,
            performed_at   TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cash (
            id             INTEGER PRIMARY KEY,
            available_cash REAL    NOT NULL DEFAULT 0
        );
        INSERT OR IGNORE INTO cash (id, available_cash) VALUES (1, 0.0);
        CREATE TABLE IF NOT EXISTS portfolio_positions (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker             TEXT    NOT NULL UNIQUE,
            quantity           REAL    NOT NULL,
            avg_cost           REAL    NOT NULL,
            market_price       REAL,
            market_value       REAL,
            cost_basis         REAL,
            unrealized_pnl     REAL,
            unrealized_pnl_pct REAL,
            realized_pnl       REAL    NOT NULL DEFAULT 0.0,
            source             TEXT    NOT NULL DEFAULT 'manual',
            is_stale           INTEGER NOT NULL DEFAULT 0,
            concentration_pct  REAL    NOT NULL DEFAULT 0.0,
            price_fetched_at   TEXT,
            reconciled_at      TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id           TEXT    NOT NULL UNIQUE,
            trigger               TEXT    NOT NULL DEFAULT 'manual',
            total_market_value    REAL    NOT NULL,
            total_cost_basis      REAL    NOT NULL,
            total_unrealized_pnl  REAL    NOT NULL,
            total_realized_pnl    REAL    NOT NULL,
            cash                  REAL    NOT NULL,
            total_portfolio_value REAL    NOT NULL,
            position_count        INTEGER NOT NULL,
            stale_count           INTEGER NOT NULL DEFAULT 0,
            positions_json        TEXT    NOT NULL,
            taken_at              TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS portfolio_reconciliation_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          TEXT    NOT NULL UNIQUE,
            trigger         TEXT    NOT NULL DEFAULT 'manual',
            positions_found INTEGER NOT NULL DEFAULT 0,
            issues_found    INTEGER NOT NULL DEFAULT 0,
            issues_json     TEXT    NOT NULL DEFAULT '[]',
            duration_ms     REAL,
            status          TEXT    NOT NULL DEFAULT 'OK',
            reconciled_at   TEXT    NOT NULL
        );
    """)
    conn.commit()
    conn.close()


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "test_a12.db"
    _init_db(str(p))
    return str(p)


@pytest.fixture
def patched_db(db_path, monkeypatch):
    gc = _make_get_conn(db_path)
    monkeypatch.setattr(database, "get_connection", gc)
    monkeypatch.setattr(mp, "_ensure_tables", lambda: None)
    return db_path


# ── TestValidatePosition ──────────────────────────────────────────────────────

class TestValidatePosition:
    def test_valid_position_returns_empty(self):
        assert mp.validate_position("AAPL", 10, 150.0, "TFSA", "USD") == []

    def test_missing_ticker_flagged(self):
        errs = mp.validate_position("", 10, 150.0)
        assert "MISSING_TICKER" in errs

    def test_whitespace_ticker_flagged(self):
        errs = mp.validate_position("   ", 10, 150.0)
        assert "MISSING_TICKER" in errs

    def test_negative_quantity_flagged(self):
        errs = mp.validate_position("AAPL", -5, 150.0)
        assert any("NEGATIVE_QUANTITY" in e for e in errs)

    def test_negative_avg_cost_flagged(self):
        errs = mp.validate_position("AAPL", 10, -100.0)
        assert any("IMPOSSIBLE_AVG_COST" in e for e in errs)

    def test_unsupported_currency_flagged(self):
        errs = mp.validate_position("AAPL", 10, 150.0, "TFSA", "EUR")
        assert any("UNSUPPORTED_CURRENCY" in e for e in errs)

    def test_unsupported_account_type_flagged(self):
        errs = mp.validate_position("AAPL", 10, 150.0, "MARGIN", "CAD")
        assert any("UNSUPPORTED_ACCOUNT_TYPE" in e for e in errs)

    def test_zero_quantity_is_valid(self):
        assert mp.validate_position("AAPL", 0, 150.0) == []

    def test_zero_avg_cost_is_valid(self):
        assert mp.validate_position("AAPL", 10, 0.0) == []

    def test_all_supported_account_types(self):
        for at in ("TFSA", "CASH", "RRSP", "OTHER"):
            assert mp.validate_position("AAPL", 1, 10.0, at, "CAD") == []

    def test_all_supported_currencies(self):
        for cur in ("CAD", "USD"):
            assert mp.validate_position("AAPL", 1, 10.0, "TFSA", cur) == []


# ── TestValidateAccountSettings ───────────────────────────────────────────────

class TestValidateAccountSettings:
    def test_valid_returns_empty(self):
        assert mp.validate_account_settings("TFSA", "CAD", 5000.0) == []

    def test_unsupported_account_type(self):
        errs = mp.validate_account_settings("SAVINGS", "CAD", 100.0)
        assert any("UNSUPPORTED_ACCOUNT_TYPE" in e for e in errs)

    def test_unsupported_currency(self):
        errs = mp.validate_account_settings("TFSA", "GBP", 100.0)
        assert any("UNSUPPORTED_CURRENCY" in e for e in errs)

    def test_negative_cash(self):
        errs = mp.validate_account_settings("TFSA", "CAD", -500.0)
        assert any("NEGATIVE_CASH" in e for e in errs)

    def test_zero_cash_valid(self):
        assert mp.validate_account_settings("TFSA", "CAD", 0.0) == []


# ── TestUpsertPosition ────────────────────────────────────────────────────────

class TestUpsertPosition:
    def test_basic_insert(self, patched_db):
        result = mp.upsert_position("AAPL", 10, 150.0, account_type="TFSA", currency="USD")
        assert result["ok"]    is True
        assert result["ticker"] == "AAPL"
        assert result["position"]["quantity"] == pytest.approx(10.0, abs=0.001)

    def test_ticker_normalized_to_uppercase(self, patched_db):
        result = mp.upsert_position("aapl", 5, 100.0)
        assert result["ticker"] == "AAPL"

    def test_update_existing_position(self, patched_db):
        mp.upsert_position("MSFT", 10, 300.0)
        result = mp.upsert_position("MSFT", 15, 320.0)
        assert result["ok"] is True
        assert result["position"]["quantity"] == pytest.approx(15.0, abs=0.001)
        assert result["position"]["avg_cost"] == pytest.approx(320.0, abs=0.001)

    def test_created_at_preserved_on_update(self, patched_db):
        first = mp.upsert_position("NVDA", 5, 400.0)
        second = mp.upsert_position("NVDA", 8, 450.0)
        assert first["position"]["created_at"] == second["position"]["created_at"]

    def test_reactivate_inactive_position(self, patched_db):
        mp.upsert_position("AMD", 5, 100.0)
        mp.deactivate_position("AMD")
        result = mp.upsert_position("AMD", 10, 120.0)
        assert result["ok"] is True
        assert result["position"]["active"] == 1

    def test_validation_failure_returns_errors(self, patched_db):
        result = mp.upsert_position("AAPL", -5, 100.0)
        assert result["ok"]    is False
        assert result["errors"] != []

    def test_invalid_currency_rejected(self, patched_db):
        result = mp.upsert_position("AAPL", 5, 100.0, currency="JPY")
        assert result["ok"] is False

    def test_invalid_account_type_rejected(self, patched_db):
        result = mp.upsert_position("AAPL", 5, 100.0, account_type="401K")
        assert result["ok"] is False

    def test_audit_log_written_on_insert(self, patched_db):
        mp.upsert_position("GOOG", 3, 200.0)
        logs = mp.get_audit_log()
        assert any(
            l["action"] == "UPSERT_POSITION" and l["subject"] == "GOOG"
            for l in logs
        )

    def test_audit_log_old_value_null_on_first_insert(self, patched_db):
        mp.upsert_position("TSM", 5, 150.0)
        logs = mp.get_audit_log()
        entry = next(l for l in logs if l["subject"] == "TSM")
        assert entry["old_value_json"] is None

    def test_audit_log_old_value_set_on_update(self, patched_db):
        mp.upsert_position("META", 10, 300.0)
        mp.upsert_position("META", 12, 320.0)
        logs = [l for l in mp.get_audit_log() if l["subject"] == "META"]
        update_entry = logs[0]  # most recent first
        assert update_entry["old_value_json"] is not None

    def test_stored_in_db(self, patched_db):
        mp.upsert_position("PLTR", 100, 25.0)
        conn = sqlite3.connect(patched_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM manual_portfolio_positions WHERE ticker='PLTR'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["quantity"] == pytest.approx(100.0, abs=0.001)


# ── TestDeactivatePosition ────────────────────────────────────────────────────

class TestDeactivatePosition:
    def test_deactivate_existing_position(self, patched_db):
        mp.upsert_position("AAPL", 10, 150.0)
        result = mp.deactivate_position("AAPL")
        assert result["ok"] is True

    def test_position_marked_inactive_in_db(self, patched_db):
        mp.upsert_position("MSFT", 5, 300.0)
        mp.deactivate_position("MSFT")
        conn = sqlite3.connect(patched_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT active FROM manual_portfolio_positions WHERE ticker='MSFT'"
        ).fetchone()
        conn.close()
        assert row["active"] == 0

    def test_position_not_deleted(self, patched_db):
        mp.upsert_position("NVDA", 5, 400.0)
        mp.deactivate_position("NVDA")
        conn = sqlite3.connect(patched_db)
        count = conn.execute(
            "SELECT COUNT(*) FROM manual_portfolio_positions WHERE ticker='NVDA'"
        ).fetchone()[0]
        conn.close()
        assert count == 1

    def test_deactivate_nonexistent_returns_error(self, patched_db):
        result = mp.deactivate_position("FAKE")
        assert result["ok"]    is False
        assert "POSITION_NOT_FOUND" in result.get("error", "")

    def test_deactivate_audit_logged(self, patched_db):
        mp.upsert_position("AMD", 5, 100.0)
        mp.deactivate_position("AMD")
        logs = mp.get_audit_log()
        assert any(l["action"] == "DEACTIVATE_POSITION" and l["subject"] == "AMD" for l in logs)

    def test_ticker_normalized(self, patched_db):
        mp.upsert_position("SHOP.TO", 10, 90.0)
        result = mp.deactivate_position("shop.to")
        assert result["ok"] is True


# ── TestGetManualPositions ────────────────────────────────────────────────────

class TestGetManualPositions:
    def test_active_only_by_default(self, patched_db):
        mp.upsert_position("AAPL",  10, 150.0)
        mp.upsert_position("MSFT",  5,  300.0)
        mp.deactivate_position("MSFT")

        positions = mp.get_manual_positions()
        tickers = [p["ticker"] for p in positions]
        assert "AAPL" in tickers
        assert "MSFT" not in tickers

    def test_include_inactive_returns_all(self, patched_db):
        mp.upsert_position("AAPL",  10, 150.0)
        mp.upsert_position("MSFT",  5,  300.0)
        mp.deactivate_position("MSFT")

        positions = mp.get_manual_positions(include_inactive=True)
        tickers = [p["ticker"] for p in positions]
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    def test_empty_when_no_positions(self, patched_db):
        assert mp.get_manual_positions() == []

    def test_ordered_by_ticker(self, patched_db):
        mp.upsert_position("NVDA", 5, 400.0)
        mp.upsert_position("AAPL", 10, 150.0)
        positions = mp.get_manual_positions()
        tickers = [p["ticker"] for p in positions]
        assert tickers == sorted(tickers)


# ── TestAccountSettings ───────────────────────────────────────────────────────

class TestAccountSettings:
    def test_get_returns_defaults(self, patched_db):
        settings = mp.get_account_settings()
        assert settings["account_type"] == "TFSA"
        assert settings["base_currency"] == "CAD"

    def test_update_account_name(self, patched_db):
        result = mp.update_account_settings(account_name="My Wealthsimple TFSA")
        assert result["ok"] is True
        assert result["settings"]["account_name"] == "My Wealthsimple TFSA"

    def test_update_available_cash(self, patched_db):
        result = mp.update_account_settings(available_cash=5000.0)
        assert result["ok"] is True
        assert result["settings"]["available_cash"] == pytest.approx(5000.0, abs=0.01)

    def test_update_contribution_room(self, patched_db):
        result = mp.update_account_settings(contribution_room=15000.0)
        assert result["ok"] is True
        assert result["settings"]["contribution_room"] == pytest.approx(15000.0, abs=0.01)

    def test_partial_update_preserves_other_fields(self, patched_db):
        mp.update_account_settings(account_name="Test Account", available_cash=1000.0)
        result = mp.update_account_settings(available_cash=2000.0)
        assert result["settings"]["account_name"] == "Test Account"

    def test_invalid_account_type_rejected(self, patched_db):
        result = mp.update_account_settings(account_type="MARGIN")
        assert result["ok"] is False

    def test_invalid_currency_rejected(self, patched_db):
        result = mp.update_account_settings(base_currency="EUR")
        assert result["ok"] is False

    def test_negative_cash_rejected(self, patched_db):
        result = mp.update_account_settings(available_cash=-500.0)
        assert result["ok"] is False

    def test_audit_logged_on_update(self, patched_db):
        mp.update_account_settings(available_cash=3000.0)
        logs = mp.get_audit_log()
        assert any(l["action"] == "UPDATE_ACCOUNT_SETTINGS" for l in logs)

    def test_update_notes(self, patched_db):
        result = mp.update_account_settings(notes="Wealthsimple TFSA account")
        assert result["ok"] is True
        assert "Wealthsimple" in result["settings"]["notes"]


# ── TestGetManualPortfolio ────────────────────────────────────────────────────

class TestGetManualPortfolio:
    def test_returns_positions_and_settings(self, patched_db):
        mp.upsert_position("AAPL", 10, 150.0)
        portfolio = mp.get_manual_portfolio()
        assert "positions"        in portfolio
        assert "account_settings" in portfolio

    def test_only_active_positions(self, patched_db):
        mp.upsert_position("AAPL", 10, 150.0)
        mp.upsert_position("MSFT",  5, 300.0)
        mp.deactivate_position("MSFT")
        portfolio = mp.get_manual_portfolio()
        tickers = [p["ticker"] for p in portfolio["positions"]]
        assert "AAPL" in tickers
        assert "MSFT" not in tickers


# ── TestReconcileManual ───────────────────────────────────────────────────────

class TestReconcileManual:
    def test_basic_reconcile(self, patched_db, monkeypatch):
        mp.upsert_position("AAPL", 10, 150.0, currency="USD")
        mp.update_account_settings(available_cash=1000.0)

        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 200.0})
        monkeypatch.setattr("portfolio.get_cash", lambda: 1000.0)

        result = mp.reconcile_manual()
        assert result["status"]         == "OK"
        assert result["position_count"] == 1

    def test_inactive_positions_excluded(self, patched_db, monkeypatch):
        mp.upsert_position("AAPL", 10, 150.0)
        mp.upsert_position("MSFT",  5, 300.0)
        mp.deactivate_position("MSFT")

        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 200.0})
        monkeypatch.setattr("portfolio.get_cash", lambda: 0.0)

        result = mp.reconcile_manual()
        tickers = [p["ticker"] for p in result["positions"]]
        assert "AAPL" in tickers
        assert "MSFT" not in tickers

    def test_canonical_positions_updated(self, patched_db, monkeypatch):
        mp.upsert_position("NVDA", 5, 400.0, currency="USD")

        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 500.0})
        monkeypatch.setattr("portfolio.get_cash", lambda: 0.0)

        mp.reconcile_manual()

        conn = sqlite3.connect(patched_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM portfolio_positions WHERE ticker='NVDA'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["source"] == "manual"

    def test_snapshot_created(self, patched_db, monkeypatch):
        mp.upsert_position("AAPL", 10, 150.0)
        mp.update_account_settings(available_cash=500.0)

        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 200.0})
        monkeypatch.setattr("portfolio.get_cash", lambda: 500.0)

        result = mp.reconcile_manual()
        assert result.get("snapshot_id") is not None

        conn = sqlite3.connect(patched_db)
        conn.row_factory = sqlite3.Row
        snap = conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE snapshot_id=?",
            (result["snapshot_id"],),
        ).fetchone()
        conn.close()
        assert snap is not None
        assert snap["trigger"] == "manual_reconcile"

    def test_cash_synced_to_canonical(self, patched_db, monkeypatch):
        mp.update_account_settings(available_cash=7500.0)
        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 100.0})
        monkeypatch.setattr("portfolio.get_cash", lambda: 7500.0)

        mp.reconcile_manual()

        conn = sqlite3.connect(patched_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT available_cash FROM cash WHERE id=1").fetchone()
        conn.close()
        assert row["available_cash"] == pytest.approx(7500.0, abs=0.01)

    def test_stale_price_included_with_flag(self, patched_db, monkeypatch):
        mp.upsert_position("AAPL", 10, 150.0)
        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: None)
        monkeypatch.setattr("portfolio.get_cash", lambda: 0.0)

        result = mp.reconcile_manual()
        assert any("STALE_PRICE" in i for i in result["issues"])
        assert result["positions"][0]["is_stale"] is True

    def test_reconcile_logged_to_recon_log(self, patched_db, monkeypatch):
        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 100.0})
        monkeypatch.setattr("portfolio.get_cash", lambda: 0.0)

        result = mp.reconcile_manual()

        conn = sqlite3.connect(patched_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM portfolio_reconciliation_log WHERE run_id=?",
            (result["run_id"],),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["trigger"] == "manual_api"

    def test_usd_price_converted_to_cad(self, patched_db, monkeypatch):
        mp.upsert_position("NVDA", 5, 550.0, currency="USD")
        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 400.0})
        monkeypatch.setattr("portfolio.get_cash", lambda: 0.0)

        result = mp.reconcile_manual()
        pos = result["positions"][0]
        assert pos["market_price"] == pytest.approx(400.0 * 1.38, abs=0.01)

    def test_cad_price_not_converted(self, patched_db, monkeypatch):
        mp.upsert_position("VFV.TO", 10, 100.0, currency="CAD")
        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 120.0})
        monkeypatch.setattr("portfolio.get_cash", lambda: 0.0)

        result = mp.reconcile_manual()
        pos = result["positions"][0]
        assert pos["market_price"] == pytest.approx(120.0, abs=0.01)

    def test_account_settings_in_result(self, patched_db, monkeypatch):
        mp.update_account_settings(account_name="My TFSA")
        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 100.0})
        monkeypatch.setattr("portfolio.get_cash", lambda: 0.0)

        result = mp.reconcile_manual()
        assert result.get("account_settings", {}).get("account_name") == "My TFSA"


# ── TestAuditLog ──────────────────────────────────────────────────────────────

class TestAuditLog:
    def test_audit_log_append_only_no_update(self):
        source = inspect.getsource(mp)
        assert "UPDATE manual_portfolio_audit_log" not in source

    def test_audit_log_append_only_no_delete(self):
        source = inspect.getsource(mp)
        assert "DELETE FROM manual_portfolio_audit_log" not in source

    def test_audit_log_returned_desc(self, patched_db):
        mp.upsert_position("AAPL", 10, 150.0)
        mp.upsert_position("MSFT",  5, 300.0)
        logs = mp.get_audit_log()
        assert len(logs) >= 2
        assert logs[0]["performed_at"] >= logs[1]["performed_at"]

    def test_every_upsert_logged(self, patched_db):
        mp.upsert_position("AAPL", 10, 150.0)
        mp.upsert_position("AAPL", 12, 160.0)
        logs = [l for l in mp.get_audit_log() if l["action"] == "UPSERT_POSITION" and l["subject"] == "AAPL"]
        assert len(logs) == 2

    def test_every_deactivate_logged(self, patched_db):
        mp.upsert_position("NVDA", 5, 400.0)
        mp.deactivate_position("NVDA")
        logs = [l for l in mp.get_audit_log() if l["action"] == "DEACTIVATE_POSITION"]
        assert len(logs) == 1

    def test_account_update_logged(self, patched_db):
        mp.update_account_settings(available_cash=1000.0)
        logs = [l for l in mp.get_audit_log() if l["action"] == "UPDATE_ACCOUNT_SETTINGS"]
        assert len(logs) == 1
        assert logs[0]["subject"] == "account"


# ── TestNoTradingCalls ────────────────────────────────────────────────────────

class TestNoTradingCalls:
    def test_no_record_buy_trade(self):
        assert "record_buy_trade(" not in inspect.getsource(mp)

    def test_no_reduce_or_remove(self):
        assert "reduce_or_remove_holding(" not in inspect.getsource(mp)

    def test_no_add_or_update_holding(self):
        assert "add_or_update_holding(" not in inspect.getsource(mp)

    def test_no_destructive_delete_on_positions(self):
        assert "DELETE FROM manual_portfolio_positions" not in inspect.getsource(mp)

    def test_no_broker_writes(self):
        source = inspect.getsource(mp)
        for pattern in ("place_order(", "submit_order(", "broker_client", "wealthsimple_api"):
            assert pattern not in source


# ── TestApiEndpoints ──────────────────────────────────────────────────────────

@pytest.fixture
def app_client(monkeypatch):
    from flask import Flask
    from api import api_bp, cache_clear
    cache_clear()
    flask_app = Flask("test_a12")
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(api_bp)
    with flask_app.test_client() as c:
        yield c


class TestApiManualGet:
    def test_returns_ok_shape(self, app_client, monkeypatch):
        monkeypatch.setattr(
            "manual_portfolio.get_manual_portfolio",
            lambda: {"positions": [], "account_settings": {}},
        )
        resp = app_client.get("/api/v1/portfolio/manual")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True

    def test_no_auth_required(self, app_client, monkeypatch):
        monkeypatch.setattr(
            "manual_portfolio.get_manual_portfolio",
            lambda: {"positions": [], "account_settings": {}},
        )
        with patch.dict(os.environ, {"API_SECRET": "secret"}):
            resp = app_client.get("/api/v1/portfolio/manual")
        assert resp.status_code == 200


class TestApiManualUpsert:
    def test_requires_auth(self, app_client):
        with patch.dict(os.environ, {"API_SECRET": "secret"}):
            resp = app_client.post(
                "/api/v1/portfolio/manual/positions/upsert",
                json={"ticker": "AAPL", "quantity": 10, "avg_cost": 150.0},
            )
        assert resp.status_code == 401

    def test_missing_ticker_returns_400(self, app_client):
        with patch.dict(os.environ, {"API_SECRET": ""}):
            resp = app_client.post(
                "/api/v1/portfolio/manual/positions/upsert",
                json={"quantity": 10, "avg_cost": 150.0},
            )
        assert resp.status_code == 400

    def test_valid_upsert_succeeds(self, app_client, monkeypatch):
        monkeypatch.setattr(
            "manual_portfolio.upsert_position",
            lambda **kw: {"ok": True, "ticker": "AAPL", "position": {}},
        )
        with patch.dict(os.environ, {"API_SECRET": ""}):
            resp = app_client.post(
                "/api/v1/portfolio/manual/positions/upsert",
                json={"ticker": "AAPL", "quantity": 10, "avg_cost": 150.0},
            )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_validation_error_returns_422(self, app_client, monkeypatch):
        monkeypatch.setattr(
            "manual_portfolio.upsert_position",
            lambda **kw: {"ok": False, "errors": ["NEGATIVE_QUANTITY:-5"], "ticker": "AAPL"},
        )
        with patch.dict(os.environ, {"API_SECRET": ""}):
            resp = app_client.post(
                "/api/v1/portfolio/manual/positions/upsert",
                json={"ticker": "AAPL", "quantity": -5, "avg_cost": 150.0},
            )
        assert resp.status_code == 422


class TestApiManualDeactivate:
    def test_requires_auth(self, app_client):
        with patch.dict(os.environ, {"API_SECRET": "secret"}):
            resp = app_client.post("/api/v1/portfolio/manual/positions/AAPL/deactivate")
        assert resp.status_code == 401

    def test_not_found_returns_404(self, app_client, monkeypatch):
        monkeypatch.setattr(
            "manual_portfolio.deactivate_position",
            lambda ticker: {"ok": False, "error": "POSITION_NOT_FOUND", "ticker": ticker},
        )
        with patch.dict(os.environ, {"API_SECRET": ""}):
            resp = app_client.post("/api/v1/portfolio/manual/positions/FAKE/deactivate")
        assert resp.status_code == 404

    def test_success_returns_ok(self, app_client, monkeypatch):
        monkeypatch.setattr(
            "manual_portfolio.deactivate_position",
            lambda ticker: {"ok": True, "ticker": ticker},
        )
        with patch.dict(os.environ, {"API_SECRET": ""}):
            resp = app_client.post("/api/v1/portfolio/manual/positions/AAPL/deactivate")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True


class TestApiManualAccount:
    def test_requires_auth(self, app_client):
        with patch.dict(os.environ, {"API_SECRET": "secret"}):
            resp = app_client.post(
                "/api/v1/portfolio/manual/account",
                json={"available_cash": 5000},
            )
        assert resp.status_code == 401

    def test_valid_update_succeeds(self, app_client, monkeypatch):
        monkeypatch.setattr(
            "manual_portfolio.update_account_settings",
            lambda **kw: {"ok": True, "settings": {"available_cash": 5000.0}},
        )
        with patch.dict(os.environ, {"API_SECRET": ""}):
            resp = app_client.post(
                "/api/v1/portfolio/manual/account",
                json={"available_cash": 5000},
            )
        assert resp.status_code == 200

    def test_invalid_cash_returns_400(self, app_client):
        with patch.dict(os.environ, {"API_SECRET": ""}):
            resp = app_client.post(
                "/api/v1/portfolio/manual/account",
                json={"available_cash": "not-a-number"},
            )
        assert resp.status_code == 400

    def test_validation_error_returns_422(self, app_client, monkeypatch):
        monkeypatch.setattr(
            "manual_portfolio.update_account_settings",
            lambda **kw: {"ok": False, "errors": ["UNSUPPORTED_ACCOUNT_TYPE:MARGIN"]},
        )
        with patch.dict(os.environ, {"API_SECRET": ""}):
            resp = app_client.post(
                "/api/v1/portfolio/manual/account",
                json={"account_type": "MARGIN"},
            )
        assert resp.status_code == 422


class TestApiReconcileManual:
    def test_requires_auth(self, app_client):
        with patch.dict(os.environ, {"API_SECRET": "secret"}):
            resp = app_client.post("/api/v1/portfolio/reconcile/manual")
        assert resp.status_code == 401

    def test_triggers_reconciliation(self, app_client, monkeypatch):
        called = []

        def _mock():
            called.append(True)
            return {"status": "OK", "positions": [], "position_count": 0, "issues": []}

        monkeypatch.setattr("manual_portfolio.reconcile_manual", _mock)
        with patch.dict(os.environ, {"API_SECRET": ""}):
            resp = app_client.post("/api/v1/portfolio/reconcile/manual")
        assert resp.status_code == 200
        assert called

    def test_with_valid_auth(self, app_client, monkeypatch):
        monkeypatch.setattr(
            "manual_portfolio.reconcile_manual",
            lambda: {"status": "OK", "positions": [], "position_count": 0, "issues": []},
        )
        with patch.dict(os.environ, {"API_SECRET": "mysecret"}):
            resp = app_client.post(
                "/api/v1/portfolio/reconcile/manual",
                headers={"Authorization": "Bearer mysecret"},
            )
        assert resp.status_code == 200
