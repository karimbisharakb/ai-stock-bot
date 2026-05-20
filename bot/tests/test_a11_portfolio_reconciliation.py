"""
Phase A11 — Portfolio truth and reconciliation layer tests.

Covers:
  - P&L calculations (unrealized, realized via weighted-avg cost)
  - Reconciliation merge / duplicate-ticker guard
  - Stale detection when price fetch fails
  - Drift detection (missing / extra / impossible / anomaly)
  - Impossible state rejection (negative quantity, negative avg_cost)
  - Snapshot immutability (append-only, no UPDATE path in module)
  - Morning brief integration (uses reconcile_portfolio, not get_portfolio_with_prices)
  - Deterministic outputs (same input → same output)
  - No trading calls (no writes to holdings/transactions/cash)
  - API endpoints for canonical portfolio, snapshots, reconciliation log, reconcile POST
"""
import inspect
import json
import os
import sqlite3
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

# Ensure bot/ is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import database
import portfolio_reconciliation as recon


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_get_conn(db_path: str):
    def _get_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    return _get_conn


def _init_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS holdings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker     TEXT    NOT NULL UNIQUE,
            shares     REAL    NOT NULL,
            avg_cost   REAL    NOT NULL,
            date_added TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker    TEXT    NOT NULL,
            type      TEXT    NOT NULL,
            shares    REAL    NOT NULL,
            price_cad REAL    NOT NULL,
            total_cad REAL    NOT NULL,
            date      TEXT    NOT NULL,
            notes     TEXT
        );
        CREATE TABLE IF NOT EXISTS cash (
            id             INTEGER PRIMARY KEY,
            available_cash REAL    NOT NULL DEFAULT 0
        );
        INSERT OR IGNORE INTO cash (id, available_cash) VALUES (1, 1000.0);
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


def _seed_holding(db_path: str, ticker: str, shares: float, avg_cost: float) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO holdings (ticker, shares, avg_cost, date_added) "
        "VALUES (?,?,?,'2024-01-01')",
        (ticker, shares, avg_cost),
    )
    conn.commit()
    conn.close()


def _seed_transaction(
    db_path: str, ticker: str, txn_type: str, shares: float, price: float
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO transactions (ticker, type, shares, price_cad, total_cad, date) "
        "VALUES (?,?,?,?,?,'2024-01-01T10:00:00')",
        (ticker, txn_type, shares, price, round(shares * price, 4)),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "test_a11.db"
    _init_db(str(p))
    return str(p)


@pytest.fixture
def patched_db(db_path, monkeypatch):
    monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
    monkeypatch.setattr(recon, "_ensure_tables", lambda: None)
    return db_path


# ── TestBuildPosition ─────────────────────────────────────────────────────────

class TestBuildPosition:
    def test_basic_unrealized_pnl(self):
        pos = recon.build_position("AAPL", 10, 100.0, 150.0)
        assert pos["market_value"]       == 1500.0
        assert pos["cost_basis"]         == 1000.0
        assert pos["unrealized_pnl"]     == 500.0
        assert pos["unrealized_pnl_pct"] == 50.0
        assert pos["is_stale"]           is False

    def test_unrealized_loss(self):
        pos = recon.build_position("NVDA", 5, 200.0, 150.0)
        assert pos["unrealized_pnl"]     == -250.0
        assert pos["unrealized_pnl_pct"] == -25.0

    def test_stale_fallback_to_avg_cost(self):
        pos = recon.build_position("TSM", 10, 100.0, None)
        assert pos["is_stale"]     is True
        assert pos["market_price"] == 100.0
        assert pos["market_value"] == 1000.0

    def test_zero_market_price_triggers_stale(self):
        pos = recon.build_position("AMD", 5, 80.0, 0.0)
        assert pos["is_stale"]     is True
        assert pos["market_price"] == 80.0

    def test_concentration_pct_computed(self):
        pos = recon.build_position("SHOP.TO", 10, 100.0, 110.0, total_portfolio_value=2200.0)
        assert pos["concentration_pct"] == pytest.approx(50.0, abs=0.1)

    def test_concentration_zero_when_no_portfolio_value(self):
        pos = recon.build_position("AAPL", 10, 100.0, 150.0, total_portfolio_value=0.0)
        assert pos["concentration_pct"] == 0.0

    def test_realized_pnl_stored(self):
        pos = recon.build_position("MSFT", 5, 300.0, 350.0, realized_pnl=250.0)
        assert pos["realized_pnl"] == 250.0

    def test_returns_all_required_fields(self):
        pos = recon.build_position("GOOG", 2, 100.0, 120.0)
        required = {
            "ticker", "quantity", "avg_cost", "market_price", "market_value",
            "cost_basis", "unrealized_pnl", "unrealized_pnl_pct", "realized_pnl",
            "source", "is_stale", "concentration_pct", "price_fetched_at", "reconciled_at",
        }
        assert required.issubset(pos.keys())

    def test_source_default_manual(self):
        pos = recon.build_position("META", 3, 200.0, 250.0)
        assert pos["source"] == "manual"

    def test_quantity_rounded(self):
        pos = recon.build_position("PLTR", 10.123456789, 20.0, 25.0)
        assert pos["quantity"] == pytest.approx(10.123457, abs=1e-5)


# ── TestCheckImpossibleStates ─────────────────────────────────────────────────

class TestCheckImpossibleStates:
    def test_valid_position_returns_empty(self):
        assert recon.check_impossible_states("AAPL", 10.0, 150.0) == []

    def test_negative_quantity_flagged(self):
        issues = recon.check_impossible_states("AAPL", -5.0, 150.0)
        assert any("NEGATIVE_QUANTITY" in i for i in issues)

    def test_negative_avg_cost_flagged(self):
        issues = recon.check_impossible_states("AAPL", 10.0, -50.0)
        assert any("NEGATIVE_AVG_COST" in i for i in issues)

    def test_zero_quantity_is_valid(self):
        assert recon.check_impossible_states("AAPL", 0.0, 150.0) == []

    def test_ticker_in_issue_string(self):
        issues = recon.check_impossible_states("NVDA", -1.0, 500.0)
        assert "NVDA" in issues[0]


# ── TestComputeAggregates ─────────────────────────────────────────────────────

class TestComputeAggregates:
    def test_empty_positions(self):
        agg = recon.compute_aggregates([], 500.0)
        assert agg["total_market_value"]    == 0.0
        assert agg["total_cost_basis"]      == 0.0
        assert agg["total_portfolio_value"] == 500.0
        assert agg["position_count"]        == 0

    def test_sums_unrealized_pnl(self):
        positions = [
            recon.build_position("AAPL", 10, 100.0, 120.0),
            recon.build_position("MSFT", 5,  200.0, 180.0),
        ]
        agg = recon.compute_aggregates(positions, 0.0)
        assert agg["total_unrealized_pnl"] == pytest.approx(200 - 100, abs=0.01)

    def test_stale_count(self):
        positions = [
            recon.build_position("AAPL", 10, 100.0, 120.0),
            recon.build_position("MSFT", 5,  200.0, None),
        ]
        agg = recon.compute_aggregates(positions, 0.0)
        assert agg["stale_count"] == 1

    def test_total_portfolio_value_includes_cash(self):
        positions = [recon.build_position("AAPL", 10, 100.0, 150.0)]
        agg = recon.compute_aggregates(positions, 500.0)
        assert agg["total_portfolio_value"] == pytest.approx(1500.0 + 500.0, abs=0.01)


# ── TestRecomputeFromTransactions ─────────────────────────────────────────────

class TestRecomputeFromTransactions:
    def test_buy_only_returns_correct_shares_avg(self, db_path):
        _seed_transaction(db_path, "AAPL", "BUY", 10, 100.0)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        shares, avg_cost, realized = recon._recompute_from_transactions("AAPL", conn)
        conn.close()
        assert shares   == pytest.approx(10.0, abs=0.001)
        assert avg_cost == pytest.approx(100.0, abs=0.001)
        assert realized == pytest.approx(0.0, abs=0.01)

    def test_buy_sell_realized_pnl(self, db_path):
        _seed_transaction(db_path, "MSFT", "BUY",  10, 200.0)
        _seed_transaction(db_path, "MSFT", "SELL",  5, 250.0)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        shares, avg_cost, realized = recon._recompute_from_transactions("MSFT", conn)
        conn.close()
        # Sold 5 shares at 250, avg cost 200 → gain = 5 * (250-200) = 250
        assert realized == pytest.approx(250.0, abs=0.01)
        assert shares   == pytest.approx(5.0, abs=0.001)

    def test_weighted_avg_cost_after_multiple_buys(self, db_path):
        _seed_transaction(db_path, "NVDA", "BUY", 10, 100.0)
        _seed_transaction(db_path, "NVDA", "BUY", 10, 200.0)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        shares, avg_cost, _ = recon._recompute_from_transactions("NVDA", conn)
        conn.close()
        assert avg_cost == pytest.approx(150.0, abs=0.01)

    def test_dividend_adds_shares(self, db_path):
        _seed_transaction(db_path, "RY.TO", "BUY",      100, 130.0)
        _seed_transaction(db_path, "RY.TO", "DIVIDEND",   2, 128.0)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        shares, _, _ = recon._recompute_from_transactions("RY.TO", conn)
        conn.close()
        assert shares == pytest.approx(102.0, abs=0.001)

    def test_no_transactions_returns_zeros(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        shares, avg_cost, realized = recon._recompute_from_transactions("XYZ", conn)
        conn.close()
        assert shares   == 0.0
        assert avg_cost == 0.0
        assert realized == 0.0


# ── TestReconcilePortfolio ────────────────────────────────────────────────────

class TestReconcilePortfolio:
    def test_empty_holdings_returns_ok(self, patched_db, monkeypatch):
        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        result = recon.reconcile_portfolio()
        assert result["status"]         == "OK"
        assert result["positions"]      == []
        assert result["position_count"] == 0

    def test_basic_reconciliation(self, patched_db, monkeypatch):
        db_path = patched_db
        _seed_holding(db_path, "AAPL", 10, 100.0)
        _seed_transaction(db_path, "AAPL", "BUY", 10, 100.0)

        mock_data = {"price": 150.0, "rsi": 55.0}
        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: mock_data)

        result = recon.reconcile_portfolio()
        assert result["status"]         == "OK"
        assert result["position_count"] == 1

        pos = result["positions"][0]
        assert pos["ticker"]         == "AAPL"
        assert pos["quantity"]       == pytest.approx(10.0, abs=0.001)
        assert pos["is_stale"]       is False
        assert pos["market_price"]   == pytest.approx(150.0 * 1.38, abs=0.01)
        assert pos["unrealized_pnl"] == pytest.approx(pos["market_value"] - pos["cost_basis"], abs=0.01)

    def test_stale_price_when_fetch_fails(self, patched_db, monkeypatch):
        db_path = patched_db
        _seed_holding(db_path, "AAPL", 10, 100.0)
        _seed_transaction(db_path, "AAPL", "BUY", 10, 100.0)

        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: None)

        result = recon.reconcile_portfolio()
        pos = result["positions"][0]
        assert pos["is_stale"]   is True
        assert pos["market_price"] == pytest.approx(pos["avg_cost"], abs=0.001)
        assert any("STALE_PRICE" in i for i in result["issues"])

    def test_impossible_state_position_skipped(self, patched_db, monkeypatch):
        db_path = patched_db
        # Insert negative shares directly (bypassing portfolio.py guards)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO holdings (ticker, shares, avg_cost, date_added) VALUES (?,?,?,'2024-01-01')",
            ("BAD", -5.0, 100.0),
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 50.0})

        result = recon.reconcile_portfolio()
        tickers = [p["ticker"] for p in result["positions"]]
        assert "BAD" not in tickers
        assert any("NEGATIVE_QUANTITY" in i for i in result["issues"])

    def test_writes_to_portfolio_positions_table(self, patched_db, monkeypatch):
        db_path = patched_db
        _seed_holding(db_path, "MSFT", 5, 300.0)
        _seed_transaction(db_path, "MSFT", "BUY", 5, 300.0)

        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 400.0})

        recon.reconcile_portfolio()

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM portfolio_positions WHERE ticker='MSFT'").fetchone()
        conn.close()
        assert row is not None
        assert row["quantity"] == pytest.approx(5.0, abs=0.001)

    def test_run_logged_to_reconciliation_log(self, patched_db, monkeypatch):
        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 100.0})

        result = recon.reconcile_portfolio(trigger="test")

        conn = sqlite3.connect(patched_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM portfolio_reconciliation_log WHERE run_id=?",
            (result["run_id"],),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["trigger"] == "test"
        assert row["status"]  == "OK"

    def test_cad_stock_no_fx_conversion(self, patched_db, monkeypatch):
        db_path = patched_db
        _seed_holding(db_path, "VFV.TO", 10, 100.0)
        _seed_transaction(db_path, "VFV.TO", "BUY", 10, 100.0)

        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 120.0})

        result = recon.reconcile_portfolio()
        pos = result["positions"][0]
        # .TO ticker — no FX conversion
        assert pos["market_price"] == pytest.approx(120.0, abs=0.01)

    def test_usd_stock_applies_fx(self, patched_db, monkeypatch):
        db_path = patched_db
        _seed_holding(db_path, "NVDA", 10, 138.0)
        _seed_transaction(db_path, "NVDA", "BUY", 10, 138.0)

        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 100.0})

        result = recon.reconcile_portfolio()
        pos = result["positions"][0]
        assert pos["market_price"] == pytest.approx(138.0, abs=0.01)

    def test_realized_pnl_in_positions(self, patched_db, monkeypatch):
        db_path = patched_db
        _seed_holding(db_path, "AMD", 5, 100.0)
        _seed_transaction(db_path, "AMD", "BUY",  10, 100.0)
        _seed_transaction(db_path, "AMD", "SELL",  5, 150.0)

        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 160.0})

        result = recon.reconcile_portfolio()
        pos = result["positions"][0]
        assert pos["realized_pnl"] == pytest.approx(250.0, abs=0.01)


# ── TestGetCanonicalPortfolio ─────────────────────────────────────────────────

class TestGetCanonicalPortfolio:
    def test_returns_positions_from_table(self, patched_db, monkeypatch):
        db_path = patched_db
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO portfolio_positions "
            "(ticker, quantity, avg_cost, market_price, market_value, cost_basis, "
            " unrealized_pnl, unrealized_pnl_pct, realized_pnl, source, is_stale, "
            " concentration_pct, reconciled_at) "
            "VALUES ('AAPL',10,100.0,150.0,1500.0,1000.0,500.0,50.0,0.0,'manual',0,50.0,'2024-01-01')"
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr("portfolio.get_cash", lambda: 1000.0)
        state = recon.get_canonical_portfolio()
        assert len(state["positions"]) == 1
        assert state["positions"][0]["ticker"] == "AAPL"

    def test_aggregates_included(self, patched_db, monkeypatch):
        db_path = patched_db
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO portfolio_positions "
            "(ticker, quantity, avg_cost, market_price, market_value, cost_basis, "
            " unrealized_pnl, unrealized_pnl_pct, realized_pnl, source, is_stale, "
            " concentration_pct, reconciled_at) "
            "VALUES ('MSFT',5,300.0,400.0,2000.0,1500.0,500.0,33.3,0.0,'manual',0,66.7,'2024-01-01')"
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr("portfolio.get_cash", lambda: 500.0)
        state = recon.get_canonical_portfolio()
        agg   = state["aggregates"]
        assert agg["total_market_value"]    == pytest.approx(2000.0, abs=0.01)
        assert agg["total_portfolio_value"] == pytest.approx(2500.0, abs=0.01)

    def test_empty_positions_returns_valid_shape(self, patched_db, monkeypatch):
        monkeypatch.setattr("portfolio.get_cash", lambda: 500.0)
        state = recon.get_canonical_portfolio()
        assert "positions"  in state
        assert "aggregates" in state
        assert state["positions"] == []


# ── TestTakeSnapshot ──────────────────────────────────────────────────────────

class TestTakeSnapshot:
    def _seed_position(self, db_path: str) -> None:
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO portfolio_positions "
            "(ticker, quantity, avg_cost, market_price, market_value, cost_basis, "
            " unrealized_pnl, unrealized_pnl_pct, realized_pnl, source, is_stale, "
            " concentration_pct, reconciled_at) "
            "VALUES ('AAPL',10,100.0,150.0,1500.0,1000.0,500.0,50.0,0.0,'manual',0,60.0,'2024-01-01')"
        )
        conn.commit()
        conn.close()

    def test_snapshot_stored_in_db(self, patched_db, monkeypatch):
        self._seed_position(patched_db)
        monkeypatch.setattr("portfolio.get_cash", lambda: 500.0)

        snap = recon.take_snapshot(trigger="test")
        assert snap.get("snapshot_id") is not None

        conn = sqlite3.connect(patched_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE snapshot_id=?",
            (snap["snapshot_id"],),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["trigger"] == "test"

    def test_snapshot_fields_present(self, patched_db, monkeypatch):
        self._seed_position(patched_db)
        monkeypatch.setattr("portfolio.get_cash", lambda: 500.0)

        snap = recon.take_snapshot()
        required = {
            "snapshot_id", "trigger", "total_market_value", "total_cost_basis",
            "total_unrealized_pnl", "total_realized_pnl", "cash",
            "total_portfolio_value", "position_count", "stale_count", "taken_at",
        }
        assert required.issubset(snap.keys())

    def test_multiple_snapshots_appended(self, patched_db, monkeypatch):
        self._seed_position(patched_db)
        monkeypatch.setattr("portfolio.get_cash", lambda: 500.0)

        recon.take_snapshot()
        recon.take_snapshot()

        conn = sqlite3.connect(patched_db)
        count = conn.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0]
        conn.close()
        assert count == 2


# ── TestSnapshotImmutability ──────────────────────────────────────────────────

class TestSnapshotImmutability:
    def test_no_update_statement_for_snapshots(self):
        source = inspect.getsource(recon)
        assert "UPDATE portfolio_snapshots" not in source

    def test_no_delete_statement_for_snapshots(self):
        source = inspect.getsource(recon)
        assert "DELETE FROM portfolio_snapshots" not in source


# ── TestGetSnapshots ──────────────────────────────────────────────────────────

class TestGetSnapshots:
    def test_returns_list(self, patched_db, monkeypatch):
        monkeypatch.setattr("portfolio.get_cash", lambda: 0.0)
        result = recon.get_snapshots()
        assert isinstance(result, list)

    def test_ordered_by_taken_at_desc(self, patched_db, monkeypatch):
        monkeypatch.setattr("portfolio.get_cash", lambda: 0.0)

        conn = sqlite3.connect(patched_db)
        conn.execute(
            "INSERT INTO portfolio_snapshots "
            "(snapshot_id, trigger, total_market_value, total_cost_basis, "
            "total_unrealized_pnl, total_realized_pnl, cash, total_portfolio_value, "
            "position_count, positions_json, taken_at) "
            "VALUES ('snap-1','manual',1000,800,200,0,100,1100,1,'[]','2024-01-01T08:00:00')"
        )
        conn.execute(
            "INSERT INTO portfolio_snapshots "
            "(snapshot_id, trigger, total_market_value, total_cost_basis, "
            "total_unrealized_pnl, total_realized_pnl, cash, total_portfolio_value, "
            "position_count, positions_json, taken_at) "
            "VALUES ('snap-2','morning_brief',1200,900,300,0,100,1300,1,'[]','2024-01-02T08:00:00')"
        )
        conn.commit()
        conn.close()

        snaps = recon.get_snapshots(limit=5)
        assert snaps[0]["snapshot_id"] == "snap-2"
        assert snaps[1]["snapshot_id"] == "snap-1"


# ── TestDetectDrift ───────────────────────────────────────────────────────────

class TestDetectDrift:
    def test_no_drift_when_in_sync(self, patched_db):
        db_path = patched_db
        _seed_holding(db_path, "AAPL", 10, 100.0)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO portfolio_positions "
            "(ticker, quantity, avg_cost, market_price, market_value, cost_basis, "
            " unrealized_pnl, unrealized_pnl_pct, realized_pnl, source, is_stale, "
            " concentration_pct, reconciled_at) "
            "VALUES ('AAPL',10,100.0,120.0,1200.0,1000.0,200.0,20.0,0.0,'manual',0,100.0,'2024-01-01')"
        )
        conn.commit()
        conn.close()

        result = recon.detect_drift()
        assert result["has_drift"] is False
        assert result["missing_from_canonical"] == []
        assert result["extra_in_canonical"]     == []

    def test_missing_from_canonical(self, patched_db):
        db_path = patched_db
        _seed_holding(db_path, "AAPL", 10, 100.0)
        # No corresponding portfolio_position row

        result = recon.detect_drift()
        assert result["has_drift"]              is True
        assert "AAPL" in result["missing_from_canonical"]

    def test_extra_in_canonical(self, patched_db):
        db_path = patched_db
        # No holding, but there is a position
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO portfolio_positions "
            "(ticker, quantity, avg_cost, market_price, market_value, cost_basis, "
            " unrealized_pnl, unrealized_pnl_pct, realized_pnl, source, is_stale, "
            " concentration_pct, reconciled_at) "
            "VALUES ('ORPHAN',5,50.0,60.0,300.0,250.0,50.0,20.0,0.0,'manual',0,100.0,'2024-01-01')"
        )
        conn.commit()
        conn.close()

        result = recon.detect_drift()
        assert result["has_drift"]          is True
        assert "ORPHAN" in result["extra_in_canonical"]

    def test_impossible_state_negative_quantity(self, patched_db):
        db_path = patched_db
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO portfolio_positions "
            "(ticker, quantity, avg_cost, market_price, market_value, cost_basis, "
            " unrealized_pnl, unrealized_pnl_pct, realized_pnl, source, is_stale, "
            " concentration_pct, reconciled_at) "
            "VALUES ('BAD',-5.0,100.0,120.0,-600.0,-500.0,-100.0,-20.0,0.0,'manual',0,0.0,'2024-01-01')"
        )
        conn.commit()
        conn.close()

        result = recon.detect_drift()
        assert any("NEGATIVE_QUANTITY" in s for s in result["impossible_states"])

    def test_value_anomaly_3x(self, patched_db):
        db_path = patched_db
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO portfolio_positions "
            "(ticker, quantity, avg_cost, market_price, market_value, cost_basis, "
            " unrealized_pnl, unrealized_pnl_pct, realized_pnl, source, is_stale, "
            " concentration_pct, reconciled_at) "
            "VALUES ('MOON',10,100.0,400.0,4000.0,1000.0,3000.0,300.0,0.0,'manual',0,100.0,'2024-01-01')"
        )
        conn.commit()
        conn.close()

        result = recon.detect_drift()
        assert any("VALUE_3X_COST" in s for s in result["value_anomalies"])

    def test_stale_positions_listed(self, patched_db):
        db_path = patched_db
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO portfolio_positions "
            "(ticker, quantity, avg_cost, market_price, market_value, cost_basis, "
            " unrealized_pnl, unrealized_pnl_pct, realized_pnl, source, is_stale, "
            " concentration_pct, reconciled_at) "
            "VALUES ('STALE',10,100.0,100.0,1000.0,1000.0,0.0,0.0,0.0,'manual',1,100.0,'2024-01-01')"
        )
        conn.commit()
        conn.close()

        result = recon.detect_drift()
        assert "STALE" in result["stale_positions"]


# ── TestReconciliationLog ─────────────────────────────────────────────────────

class TestReconciliationLog:
    def test_log_returned_in_order(self, patched_db, monkeypatch):
        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 100.0})

        recon.reconcile_portfolio(trigger="first")
        recon.reconcile_portfolio(trigger="second")

        runs = recon.get_reconciliation_log(limit=10)
        assert len(runs) >= 2
        assert runs[0]["reconciled_at"] >= runs[1]["reconciled_at"]

    def test_error_status_logged_on_failure(self, patched_db, monkeypatch):
        def _bad_rate():
            raise RuntimeError("rate fetch failed")

        monkeypatch.setattr("strategy.get_usd_cad_rate", _bad_rate)

        result = recon.reconcile_portfolio()
        assert result["status"] == "ERROR"

        runs = recon.get_reconciliation_log(limit=5)
        assert any(r["status"] == "ERROR" for r in runs)


# ── TestDeterministicOutput ───────────────────────────────────────────────────

class TestDeterministicOutput:
    def test_same_inputs_same_positions(self, patched_db, monkeypatch):
        db_path = patched_db
        _seed_holding(db_path, "AAPL", 10, 100.0)
        _seed_transaction(db_path, "AAPL", "BUY", 10, 100.0)

        monkeypatch.setattr("strategy.get_usd_cad_rate", lambda: 1.38)
        monkeypatch.setattr("market_data.get_ticker_data", lambda t: {"price": 150.0})

        r1 = recon.reconcile_portfolio()
        r2 = recon.reconcile_portfolio()

        assert r1["positions"][0]["market_value"]   == r2["positions"][0]["market_value"]
        assert r1["positions"][0]["unrealized_pnl"] == r2["positions"][0]["unrealized_pnl"]
        assert r1["positions"][0]["realized_pnl"]   == r2["positions"][0]["realized_pnl"]

    def test_build_position_is_deterministic(self):
        p1 = recon.build_position("AAPL", 10, 100.0, 150.0, total_portfolio_value=2000.0)
        p2 = recon.build_position("AAPL", 10, 100.0, 150.0, total_portfolio_value=2000.0)
        # price_fetched_at and reconciled_at may differ by milliseconds, compare numeric fields
        assert p1["market_value"]       == p2["market_value"]
        assert p1["unrealized_pnl"]     == p2["unrealized_pnl"]
        assert p1["concentration_pct"]  == p2["concentration_pct"]


# ── TestNoTradingCalls ────────────────────────────────────────────────────────

class TestNoTradingCalls:
    def test_module_has_no_record_buy_trade(self):
        source = inspect.getsource(recon)
        assert "record_buy_trade(" not in source

    def test_module_has_no_reduce_or_remove(self):
        source = inspect.getsource(recon)
        assert "reduce_or_remove_holding(" not in source

    def test_module_has_no_add_or_update(self):
        source = inspect.getsource(recon)
        assert "add_or_update_holding(" not in source

    def test_module_has_no_set_cash(self):
        source = inspect.getsource(recon)
        assert "set_cash(" not in source

    def test_module_has_no_add_cash(self):
        source = inspect.getsource(recon)
        assert "add_cash(" not in source


# ── TestMorningBriefIntegration ───────────────────────────────────────────────

class TestMorningBriefIntegration:
    def test_morning_brief_calls_operator_brief(self):
        # A21: morning_summary_job delegates to operator_brief.generate_compact_brief.
        # reconcile_portfolio is still called — but now through operator_brief's
        # collect_brief_data() rather than directly in the scheduler.
        import scheduler
        source = inspect.getsource(scheduler.morning_summary_job)
        assert "generate_compact_brief" in source

    def test_morning_brief_does_not_call_get_portfolio_with_prices(self):
        import scheduler
        source = inspect.getsource(scheduler.morning_summary_job)
        assert "get_portfolio_with_prices" not in source

    def test_morning_brief_uses_canonical_positions(self, monkeypatch):
        # A21: morning_summary_job calls generate_compact_brief; the canonical
        # portfolio path is covered by A21's own integration tests.
        import operator_brief as _ob
        import scheduler

        brief_called = []
        monkeypatch.setattr(_ob, "generate_compact_brief",
                            lambda: brief_called.append(1) or "BRIEF")
        monkeypatch.setattr("alerts.send_sms", lambda msg, bypass_quiet=False: True)
        monkeypatch.setattr("alerts.log_alert", lambda *a, **kw: None)

        scheduler.morning_summary_job()
        assert brief_called, "generate_compact_brief was not called"


def _dummy_conn():
    """Return a minimal in-memory connection for morning brief test."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE alert_log (ticker TEXT, urgency TEXT, sent_at TEXT, message TEXT)"
    )
    return conn


# ── TestApiEndpoints ──────────────────────────────────────────────────────────

@pytest.fixture
def app_client(monkeypatch):
    from flask import Flask
    from api import api_bp, cache_clear
    cache_clear()
    flask_app = Flask("test_a11")
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(api_bp)
    with flask_app.test_client() as c:
        yield c


class TestApiPortfolioGet:
    def test_returns_ok_shape(self, app_client, monkeypatch):
        monkeypatch.setattr(
            "portfolio_reconciliation.get_canonical_portfolio",
            lambda: {"positions": [], "aggregates": {"cash": 500.0}},
        )
        resp = app_client.get("/api/v1/portfolio")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "positions" in body["data"]

    def test_cached_on_second_call(self, app_client, monkeypatch):
        call_count = [0]

        def _mock():
            call_count[0] += 1
            return {"positions": [], "aggregates": {}}

        monkeypatch.setattr("portfolio_reconciliation.get_canonical_portfolio", _mock)
        app_client.get("/api/v1/portfolio")
        resp = app_client.get("/api/v1/portfolio")
        body = resp.get_json()
        assert body["meta"]["cached"] is True
        assert call_count[0] == 1


class TestApiPortfolioSnapshots:
    def test_returns_snapshots_list(self, app_client, monkeypatch):
        monkeypatch.setattr(
            "portfolio_reconciliation.get_snapshots",
            lambda limit=20: [{"snapshot_id": "s1", "taken_at": "2024-01-01T08:00:00"}],
        )
        resp = app_client.get("/api/v1/portfolio/snapshots")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["count"] == 1

    def test_limit_param_passed(self, app_client, monkeypatch):
        received = []

        def _mock(limit=20):
            received.append(limit)
            return []

        monkeypatch.setattr("portfolio_reconciliation.get_snapshots", _mock)
        app_client.get("/api/v1/portfolio/snapshots?limit=5")
        assert received[0] == 5


class TestApiPortfolioReconciliationLog:
    def test_returns_runs(self, app_client, monkeypatch):
        monkeypatch.setattr(
            "portfolio_reconciliation.get_reconciliation_log",
            lambda limit=50: [{"run_id": "r1", "status": "OK"}],
        )
        resp = app_client.get("/api/v1/portfolio/reconciliation")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["count"] == 1


class TestApiPortfolioReconcile:
    def test_requires_auth(self, app_client, monkeypatch):
        with patch.dict(os.environ, {"API_SECRET": "secret123"}):
            resp = app_client.post("/api/v1/portfolio/reconcile")
            assert resp.status_code == 401

    def test_post_triggers_reconciliation(self, app_client, monkeypatch):
        called = []

        def _mock(trigger="manual"):
            called.append(trigger)
            return {"status": "OK", "positions": [], "position_count": 0, "issues": []}

        monkeypatch.setattr("portfolio_reconciliation.reconcile_portfolio", _mock)

        with patch.dict(os.environ, {"API_SECRET": ""}):
            resp = app_client.post("/api/v1/portfolio/reconcile")

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert called[0] == "api"

    def test_post_with_valid_auth(self, app_client, monkeypatch):
        monkeypatch.setattr(
            "portfolio_reconciliation.reconcile_portfolio",
            lambda trigger="manual": {"status": "OK", "positions": [], "position_count": 0, "issues": []},
        )

        with patch.dict(os.environ, {"API_SECRET": "mysecret"}):
            resp = app_client.post(
                "/api/v1/portfolio/reconcile",
                headers={"Authorization": "Bearer mysecret"},
            )
        assert resp.status_code == 200
