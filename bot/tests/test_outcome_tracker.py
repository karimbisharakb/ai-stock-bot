"""
Unit tests for outcome_tracker module.

Covers:
  - insert_pending_outcome()   — row creation, idempotency, field storage
  - _get_pending_outcomes()    — filters correctly by status
  - _compute_returns()         — return formulas, partial data, missing data
  - _evaluate_row()            — COMPLETE / STALE / PENDING transitions
  - evaluate_pending_outcomes() — batch processing, exception isolation
  - _signal_summary()          — active-only JSON, empty case

All yfinance calls are mocked. DB operations use a temporary SQLite file.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import sqlite3
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

import database
from database import init_db, run_migrations

import outcome_tracker
from outcome_tracker import (
    insert_pending_outcome,
    evaluate_pending_outcomes,
    _get_pending_outcomes,
    _compute_returns,
    _evaluate_row,
    _update_outcome,
    _signal_summary,
    PENDING,
    COMPLETE,
    STALE,
    STALE_THRESHOLD_DAYS,
)


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "test_outcomes.db")


@pytest.fixture()
def live_db(db_path):
    """Fully initialised + migrated DB in a temp dir."""
    with patch("database.DB_PATH", db_path):
        init_db()
        run_migrations()
    yield db_path


def _make_conn(db_path: str):
    """Return a sqlite3 connection with row_factory set (mirrors get_connection)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture()
def patch_db(live_db):
    """Redirect outcome_tracker's get_connection to the temp DB."""
    with patch("database.DB_PATH", live_db), \
         patch("outcome_tracker.get_connection", lambda: _make_conn(live_db)):
        yield live_db


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _sigs(opts=2, ins=1, brk=1):
    return {
        "options":       {"score": opts, "reason": ""},
        "insider":       {"score": ins,  "reason": ""},
        "breakout":      {"score": brk,  "reason": ""},
        "short_squeeze": {"score": 0,    "reason": ""},
        "catalyst":      {"score": 0,    "reason": ""},
        "institutional": {"score": 0,    "reason": ""},
    }


def _now_iso():
    return datetime.now().isoformat()


def _ago_iso(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat()


def _insert(patch_db, ticker="NVDA", alert_time=None, entry_price=100.0,
            confidence_pct=60.0, regime="BULL", tier="CONVICTION", signals=None):
    insert_pending_outcome(
        ticker=ticker,
        alert_time=alert_time or _now_iso(),
        entry_price=entry_price,
        confidence_pct=confidence_pct,
        regime=regime,
        tier=tier,
        signals=signals or _sigs(),
    )


def _fetch_row(db_path, ticker):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM predator_outcomes WHERE ticker = ?", (ticker,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _count_rows(db_path):
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM predator_outcomes").fetchone()[0]
    conn.close()
    return n


def _fake_closes(prices: list) -> pd.DataFrame:
    """Minimal yfinance-like DataFrame with just a Close column."""
    return pd.DataFrame({"Close": prices})


# ─────────────────────────────────────────────
# _signal_summary()
# ─────────────────────────────────────────────

class TestSignalSummary:
    def test_only_active_signals_included(self):
        sigs = _sigs(opts=3, ins=2, brk=0)
        summary = json.loads(_signal_summary(sigs))
        assert "options" in summary
        assert "insider" in summary
        assert "breakout" not in summary

    def test_empty_signals_returns_empty_json(self):
        result = _signal_summary({})
        assert json.loads(result) == {}

    def test_all_zero_scores_returns_empty_json(self):
        sigs = {k: {"score": 0} for k in ["options", "insider", "breakout"]}
        assert json.loads(_signal_summary(sigs)) == {}

    def test_scores_are_integers(self):
        sigs = _sigs(opts=3, ins=2)
        summary = json.loads(_signal_summary(sigs))
        for v in summary.values():
            assert isinstance(v, int)

    def test_returns_valid_json_string(self):
        result = _signal_summary(_sigs())
        parsed = json.loads(result)
        assert isinstance(parsed, dict)


# ─────────────────────────────────────────────
# insert_pending_outcome()
# ─────────────────────────────────────────────

class TestInsertPendingOutcome:
    def test_row_created_with_pending_status(self, patch_db):
        _insert(patch_db)
        row = _fetch_row(patch_db, "NVDA")
        assert row is not None
        assert row["outcome_status"] == PENDING

    def test_fields_stored_correctly(self, patch_db):
        t = _now_iso()
        _insert(patch_db, ticker="AAPL", alert_time=t,
                entry_price=200.0, confidence_pct=62.5,
                regime="NEUTRAL", tier="CONVICTION")
        row = _fetch_row(patch_db, "AAPL")
        assert row["ticker"]         == "AAPL"
        assert row["alert_time"]     == t
        assert abs(row["entry_price"]    - 200.0) < 0.01
        assert abs(row["confidence_pct"] - 62.5)  < 0.01
        assert row["regime"] == "NEUTRAL"
        assert row["tier"]   == "CONVICTION"

    def test_signal_summary_stored_as_json(self, patch_db):
        _insert(patch_db, signals=_sigs(opts=3, ins=2, brk=1))
        row = _fetch_row(patch_db, "NVDA")
        summary = json.loads(row["signal_summary"])
        assert summary.get("options")  == 3
        assert summary.get("insider")  == 2
        assert summary.get("breakout") == 1

    def test_idempotent_duplicate_insert(self, patch_db):
        t = _now_iso()
        _insert(patch_db, ticker="PLTR", alert_time=t)
        _insert(patch_db, ticker="PLTR", alert_time=t)  # duplicate
        conn = sqlite3.connect(patch_db)
        n = conn.execute(
            "SELECT COUNT(*) FROM predator_outcomes WHERE ticker='PLTR'"
        ).fetchone()[0]
        conn.close()
        assert n == 1

    def test_different_alert_times_create_separate_rows(self, patch_db):
        _insert(patch_db, ticker="AMD", alert_time=_ago_iso(2))
        _insert(patch_db, ticker="AMD", alert_time=_now_iso())
        conn = sqlite3.connect(patch_db)
        n = conn.execute(
            "SELECT COUNT(*) FROM predator_outcomes WHERE ticker='AMD'"
        ).fetchone()[0]
        conn.close()
        assert n == 2

    def test_return_fields_are_null_initially(self, patch_db):
        _insert(patch_db)
        row = _fetch_row(patch_db, "NVDA")
        for col in ("return_1d", "return_5d", "return_20d",
                    "max_gain_pct", "max_drawdown_pct", "evaluated_at"):
            assert row[col] is None

    def test_none_entry_price_stored_as_null(self, patch_db):
        _insert(patch_db, ticker="MARA", entry_price=None)
        row = _fetch_row(patch_db, "MARA")
        assert row["entry_price"] is None


# ─────────────────────────────────────────────
# _get_pending_outcomes()
# ─────────────────────────────────────────────

class TestGetPendingOutcomes:
    def test_returns_pending_rows(self, patch_db):
        _insert(patch_db, ticker="NVDA")
        rows = _get_pending_outcomes()
        tickers = [r["ticker"] for r in rows]
        assert "NVDA" in tickers

    def test_excludes_complete_rows(self, patch_db):
        _insert(patch_db, ticker="DONE")
        conn = sqlite3.connect(patch_db)
        conn.execute(
            "UPDATE predator_outcomes SET outcome_status='COMPLETE' WHERE ticker='DONE'"
        )
        conn.commit()
        conn.close()
        rows = _get_pending_outcomes()
        assert all(r["ticker"] != "DONE" for r in rows)

    def test_excludes_stale_rows(self, patch_db):
        _insert(patch_db, ticker="OLD")
        conn = sqlite3.connect(patch_db)
        conn.execute(
            "UPDATE predator_outcomes SET outcome_status='STALE' WHERE ticker='OLD'"
        )
        conn.commit()
        conn.close()
        rows = _get_pending_outcomes()
        assert all(r["ticker"] != "OLD" for r in rows)

    def test_returns_empty_list_when_no_pending(self, patch_db):
        assert _get_pending_outcomes() == []

    def test_multiple_pending_all_returned(self, patch_db):
        for t in ("A", "B", "C"):
            _insert(patch_db, ticker=t, alert_time=_now_iso())
        rows = _get_pending_outcomes()
        tickers = [r["ticker"] for r in rows]
        for t in ("A", "B", "C"):
            assert t in tickers

    def test_rows_ordered_oldest_first(self, patch_db):
        old_t = _ago_iso(3)
        new_t = _now_iso()
        _insert(patch_db, ticker="NEW", alert_time=new_t)
        _insert(patch_db, ticker="OLD", alert_time=old_t)
        rows = _get_pending_outcomes()
        assert rows[0]["alert_time"] <= rows[-1]["alert_time"]


# ─────────────────────────────────────────────
# _compute_returns()
# ─────────────────────────────────────────────

class TestComputeReturns:
    _ALERT = "2025-01-10T10:00:00"

    def _patch_yf(self, prices):
        mock = MagicMock()
        mock.Ticker.return_value.history.return_value = _fake_closes(prices)
        return patch("outcome_tracker.yf", mock)

    def test_return_1d_correct_formula(self):
        with self._patch_yf([105.0] + [100.0] * 25):
            r = _compute_returns("NVDA", 100.0, self._ALERT)
        assert r["return_1d"] == pytest.approx(5.0, abs=0.01)

    def test_return_5d_uses_fifth_close(self):
        # closes: 101, 102, 103, 104, 110, 100, ...
        closes = [101.0, 102.0, 103.0, 104.0, 110.0] + [100.0] * 20
        with self._patch_yf(closes):
            r = _compute_returns("NVDA", 100.0, self._ALERT)
        assert r["return_5d"] == pytest.approx(10.0, abs=0.01)

    def test_return_20d_uses_twentieth_close(self):
        closes = [100.0] * 19 + [120.0] + [100.0] * 5
        with self._patch_yf(closes):
            r = _compute_returns("NVDA", 100.0, self._ALERT)
        assert r["return_20d"] == pytest.approx(20.0, abs=0.01)

    def test_max_gain_uses_series_maximum(self):
        closes = [100.0, 130.0, 110.0] + [100.0] * 17
        with self._patch_yf(closes):
            r = _compute_returns("NVDA", 100.0, self._ALERT)
        assert r["max_gain_pct"] == pytest.approx(30.0, abs=0.01)

    def test_max_drawdown_uses_series_minimum(self):
        closes = [100.0, 90.0, 95.0] + [100.0] * 17
        with self._patch_yf(closes):
            r = _compute_returns("NVDA", 100.0, self._ALERT)
        assert r["max_drawdown_pct"] == pytest.approx(-10.0, abs=0.01)

    def test_partial_data_only_1d_available(self):
        with self._patch_yf([103.0]):
            r = _compute_returns("NVDA", 100.0, self._ALERT)
        assert "return_1d" in r
        assert "return_5d" not in r
        assert "return_20d" not in r

    def test_partial_data_five_days_available(self):
        with self._patch_yf([101.0, 102.0, 103.0, 104.0, 105.0]):
            r = _compute_returns("NVDA", 100.0, self._ALERT)
        assert "return_1d" in r
        assert "return_5d" in r
        assert "return_20d" not in r

    def test_empty_history_returns_empty_dict(self):
        with self._patch_yf([]):
            r = _compute_returns("NVDA", 100.0, self._ALERT)
        assert r == {}

    def test_yf_exception_returns_empty_dict(self):
        mock = MagicMock()
        mock.Ticker.return_value.history.side_effect = Exception("network error")
        with patch("outcome_tracker.yf", mock):
            r = _compute_returns("NVDA", 100.0, self._ALERT)
        assert r == {}

    def test_zero_entry_price_returns_empty_dict(self):
        r = _compute_returns("NVDA", 0.0, self._ALERT)
        assert r == {}

    def test_none_entry_price_returns_empty_dict(self):
        r = _compute_returns("NVDA", None, self._ALERT)
        assert r == {}

    def test_returns_rounded_to_two_decimal_places(self):
        with self._patch_yf([100.0 / 3 * 4] + [100.0] * 25):  # 133.33...
            r = _compute_returns("NVDA", 100.0, self._ALERT)
        assert r["return_1d"] == round(r["return_1d"], 2)

    def test_loss_scenario(self):
        with self._patch_yf([90.0] + [100.0] * 25):
            r = _compute_returns("NVDA", 100.0, self._ALERT)
        assert r["return_1d"] == pytest.approx(-10.0, abs=0.01)


# ─────────────────────────────────────────────
# _evaluate_row() — status transitions
# ─────────────────────────────────────────────

class TestEvaluateRow:
    def _row(self, db_path, ticker="NVDA", alert_time=None, entry_price=100.0):
        t = alert_time or _now_iso()
        _insert(db_path, ticker=ticker, alert_time=t, entry_price=entry_price)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM predator_outcomes WHERE ticker=?", (ticker,)
        ).fetchone()
        conn.close()
        return dict(row)

    def test_marks_complete_when_20d_return_available(self, patch_db):
        row = self._row(patch_db)
        full_returns = {
            "return_1d": 3.0, "return_5d": 7.0, "return_20d": 15.0,
            "max_gain_pct": 18.0, "max_drawdown_pct": -2.0,
        }
        with patch("outcome_tracker._compute_returns", return_value=full_returns):
            _evaluate_row(row)
        fetched = _fetch_row(patch_db, "NVDA")
        assert fetched["outcome_status"]  == COMPLETE
        assert fetched["return_20d"]      == pytest.approx(15.0)

    def test_marks_stale_when_old_and_no_20d_data(self, patch_db):
        old_time = _ago_iso(STALE_THRESHOLD_DAYS + 5)
        row = self._row(patch_db, ticker="OLD", alert_time=old_time)
        with patch("outcome_tracker._compute_returns", return_value={"return_1d": 2.0}):
            _evaluate_row(row)
        fetched = _fetch_row(patch_db, "OLD")
        assert fetched["outcome_status"] == STALE

    def test_stays_pending_with_partial_data_recent_alert(self, patch_db):
        row = self._row(patch_db, ticker="PARTIAL")
        partial_returns = {"return_1d": 3.0, "max_gain_pct": 4.0, "max_drawdown_pct": -1.0}
        with patch("outcome_tracker._compute_returns", return_value=partial_returns):
            _evaluate_row(row)
        fetched = _fetch_row(patch_db, "PARTIAL")
        assert fetched["outcome_status"] == PENDING
        assert fetched["return_1d"]      == pytest.approx(3.0)
        assert fetched["return_20d"]     is None

    def test_evaluated_at_set_on_update(self, patch_db):
        row = self._row(patch_db)
        with patch("outcome_tracker._compute_returns",
                   return_value={"return_20d": 10.0, "max_gain_pct": 12.0, "max_drawdown_pct": -3.0}):
            _evaluate_row(row)
        fetched = _fetch_row(patch_db, "NVDA")
        assert fetched["evaluated_at"] is not None

    def test_stale_preserves_previously_computed_returns(self, patch_db):
        old_time = _ago_iso(STALE_THRESHOLD_DAYS + 5)
        row = self._row(patch_db, ticker="PRESERVE", alert_time=old_time)
        # First partial update
        with patch("outcome_tracker._compute_returns",
                   return_value={"return_1d": 5.0, "max_gain_pct": 7.0, "max_drawdown_pct": -1.0}):
            _evaluate_row(row)
        # Re-fetch to get updated row (still STALE)
        conn = sqlite3.connect(patch_db)
        conn.row_factory = sqlite3.Row
        row2 = dict(conn.execute(
            "SELECT * FROM predator_outcomes WHERE ticker='PRESERVE'"
        ).fetchone())
        conn.close()
        # Partial returns should be preserved even in STALE row
        assert row2["return_1d"] == pytest.approx(5.0)

    def test_unparseable_alert_time_marked_stale(self, patch_db):
        _insert(patch_db, ticker="BADTIME")
        # Corrupt the alert_time
        conn = sqlite3.connect(patch_db)
        conn.execute(
            "UPDATE predator_outcomes SET alert_time='NOT-A-DATE' WHERE ticker='BADTIME'"
        )
        conn.commit()
        conn.close()
        conn2 = sqlite3.connect(patch_db)
        conn2.row_factory = sqlite3.Row
        row = dict(conn2.execute(
            "SELECT * FROM predator_outcomes WHERE ticker='BADTIME'"
        ).fetchone())
        conn2.close()
        with patch("outcome_tracker.get_connection", lambda: _make_conn(patch_db)):
            _evaluate_row(row)
        fetched = _fetch_row(patch_db, "BADTIME")
        assert fetched["outcome_status"] == STALE


# ─────────────────────────────────────────────
# evaluate_pending_outcomes() — batch behaviour
# ─────────────────────────────────────────────

class TestEvaluatePendingOutcomes:
    def test_processes_pending_rows(self, patch_db):
        for t in ("A", "B"):
            _insert(patch_db, ticker=t, alert_time=_now_iso())
        full = {"return_20d": 10.0, "max_gain_pct": 12.0, "max_drawdown_pct": -2.0}
        with patch("outcome_tracker._compute_returns", return_value=full), \
             patch("time.sleep"):  # skip the 0.5s sleeps in tests
            evaluate_pending_outcomes()
        for t in ("A", "B"):
            row = _fetch_row(patch_db, t)
            assert row["outcome_status"] == COMPLETE

    def test_skips_complete_rows(self, patch_db):
        _insert(patch_db, ticker="DONE", alert_time=_now_iso())
        conn = sqlite3.connect(patch_db)
        conn.execute(
            "UPDATE predator_outcomes SET outcome_status='COMPLETE' WHERE ticker='DONE'"
        )
        conn.commit()
        conn.close()
        call_count = [0]
        orig = outcome_tracker._compute_returns
        def counting_compute(*a, **kw):
            call_count[0] += 1
            return orig(*a, **kw)
        with patch("outcome_tracker._compute_returns", side_effect=counting_compute), \
             patch("time.sleep"):
            evaluate_pending_outcomes()
        assert call_count[0] == 0

    def test_no_op_when_no_pending_rows(self, patch_db):
        # Should not raise and should not make any yfinance calls
        with patch("outcome_tracker._compute_returns") as mock_compute, \
             patch("time.sleep"):
            evaluate_pending_outcomes()
        mock_compute.assert_not_called()

    def test_exception_in_one_row_does_not_stop_others(self, patch_db):
        for t in ("GOOD", "BAD"):
            _insert(patch_db, ticker=t, alert_time=_now_iso())
        good_returns = {"return_20d": 5.0, "max_gain_pct": 7.0, "max_drawdown_pct": -1.0}
        call_count = [0]
        def side_effect(ticker, *a, **kw):
            call_count[0] += 1
            if ticker == "BAD":
                raise RuntimeError("simulated failure")
            return good_returns
        with patch("outcome_tracker._compute_returns", side_effect=side_effect), \
             patch("time.sleep"):
            evaluate_pending_outcomes()
        good_row = _fetch_row(patch_db, "GOOD")
        assert good_row["outcome_status"] == COMPLETE
        assert call_count[0] == 2  # both tickers attempted

    def test_idempotent_double_call(self, patch_db):
        _insert(patch_db, ticker="IDEM", alert_time=_now_iso())
        full = {"return_20d": 8.0, "max_gain_pct": 10.0, "max_drawdown_pct": -1.0}
        with patch("outcome_tracker._compute_returns", return_value=full), \
             patch("time.sleep"):
            evaluate_pending_outcomes()
            evaluate_pending_outcomes()  # second call should be a no-op
        conn = sqlite3.connect(patch_db)
        n = conn.execute(
            "SELECT COUNT(*) FROM predator_outcomes WHERE ticker='IDEM'"
        ).fetchone()[0]
        conn.close()
        assert n == 1
        row = _fetch_row(patch_db, "IDEM")
        assert row["outcome_status"] == COMPLETE

    def test_caps_at_max_eval_per_run(self, patch_db):
        from outcome_tracker import MAX_EVAL_PER_RUN
        # Insert MAX+5 rows
        for i in range(MAX_EVAL_PER_RUN + 5):
            _insert(patch_db, ticker=f"T{i:03d}", alert_time=_now_iso())
        call_count = [0]
        def counting_compute(*a, **kw):
            call_count[0] += 1
            return {}
        with patch("outcome_tracker._compute_returns", side_effect=counting_compute), \
             patch("time.sleep"):
            evaluate_pending_outcomes()
        assert call_count[0] == MAX_EVAL_PER_RUN


# ─────────────────────────────────────────────
# _update_outcome() — COALESCE preserves existing values
# ─────────────────────────────────────────────

class TestUpdateOutcome:
    def test_coalesce_preserves_existing_return_1d(self, patch_db):
        _insert(patch_db, ticker="NVDA")
        conn = sqlite3.connect(patch_db)
        row_id = conn.execute(
            "SELECT id FROM predator_outcomes WHERE ticker='NVDA'"
        ).fetchone()[0]
        conn.close()

        # First update sets return_1d = 5.0
        with patch("outcome_tracker.get_connection", lambda: _make_conn(patch_db)):
            _update_outcome(row_id, PENDING, {"return_1d": 5.0})

        # Second update passes return_1d=None — should preserve 5.0
        with patch("outcome_tracker.get_connection", lambda: _make_conn(patch_db)):
            _update_outcome(row_id, PENDING, {})

        fetched = _fetch_row(patch_db, "NVDA")
        assert fetched["return_1d"] == pytest.approx(5.0)

    def test_new_value_overwrites_existing(self, patch_db):
        _insert(patch_db, ticker="NVDA")
        conn = sqlite3.connect(patch_db)
        row_id = conn.execute(
            "SELECT id FROM predator_outcomes WHERE ticker='NVDA'"
        ).fetchone()[0]
        conn.close()

        with patch("outcome_tracker.get_connection", lambda: _make_conn(patch_db)):
            _update_outcome(row_id, PENDING, {"return_1d": 3.0})
            _update_outcome(row_id, COMPLETE, {"return_1d": 4.0})

        fetched = _fetch_row(patch_db, "NVDA")
        assert fetched["return_1d"]      == pytest.approx(4.0)
        assert fetched["outcome_status"] == COMPLETE
