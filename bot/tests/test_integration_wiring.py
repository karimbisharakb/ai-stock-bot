"""
Integration wiring tests — confidence + conviction tier → DB persistence.

Covers:
  - _record_alert() persists all v2 columns into predator_alerts
  - _batch_upsert_latest() persists all v2 columns into predator_latest
  - WATCH tier: goes to predator_latest only, NOT predator_alerts
  - ALERT tier: goes to both tables with correct column values
  - CONVICTION tier: goes to both tables with tier="CONVICTION"
  - Backward compat: old-format result dicts (no confidence key) produce NULL
  - _score_ticker() result dict contains all expected new keys

No network calls. All external dependencies are patched.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
import pytest
from unittest.mock import patch, MagicMock

import database
from database import init_db, run_migrations

import predator
from predator import (
    _record_alert,
    _batch_upsert_latest,
    SignalResult,
    TIER_WATCH,
    TIER_ALERT,
    TIER_CONVICTION,
    ALERT_THRESHOLD,
)


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "test_wiring.db")


@pytest.fixture()
def wired_db(db_path):
    """Fully initialised + migrated DB in a temp directory."""
    def _conn():
        c = sqlite3.connect(db_path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    with patch("database.DB_PATH", db_path), \
         patch("database.get_connection", _conn):
        init_db()
        run_migrations()
    yield db_path


@pytest.fixture()
def patch_db(wired_db):
    """Redirect get_connection() to the temp DB for the duration of the test."""
    real_connect = sqlite3.connect

    def _connect(path=None, **kw):
        return real_connect(wired_db, **kw)

    with patch("database.DB_PATH", wired_db), \
         patch("predator.get_connection", lambda: real_connect(wired_db)):
        yield wired_db


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _signals(opts=2, ins=1, sq=0, cat=0, inst=0, brk=1,
             opts_q="MEDIUM", ins_q="MEDIUM"):
    return {
        "options":       {"score": opts, "reason": "test", "data_quality": opts_q},
        "insider":       {"score": ins,  "reason": "test", "data_quality": ins_q},
        "short_squeeze": {"score": sq,   "reason": "test"},
        "catalyst":      {"score": cat,  "reason": "test"},
        "institutional": {"score": inst, "reason": "test"},
        "breakout":      {"score": brk,  "reason": "test"},
    }


def _fetch_alert(db_path, ticker):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM predator_alerts WHERE ticker = ?", (ticker,)
    ).fetchone()
    conn.close()
    return row


def _fetch_latest(db_path, ticker):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM predator_latest WHERE ticker = ?", (ticker,)
    ).fetchone()
    conn.close()
    return row


# ─────────────────────────────────────────────
# _record_alert — v2 column persistence
# ─────────────────────────────────────────────

class TestRecordAlert:
    def test_confidence_pct_persisted(self, patch_db):
        _record_alert(
            "NVDA", 7, _signals(), 500.0, 455.0, 1000.0,
            confidence_pct=62.5, adjusted_score=4.375, raw_score=7,
            tier=TIER_ALERT,
        )
        row = _fetch_alert(patch_db, "NVDA")
        assert row is not None
        assert abs(row["confidence_pct"] - 62.5) < 0.01

    def test_adjusted_score_persisted(self, patch_db):
        _record_alert(
            "NVDA", 7, _signals(), 500.0, 455.0, 1000.0,
            confidence_pct=62.5, adjusted_score=4.375, raw_score=7,
            tier=TIER_ALERT,
        )
        row = _fetch_alert(patch_db, "NVDA")
        assert abs(row["adjusted_score"] - 4.375) < 0.01

    def test_raw_score_persisted(self, patch_db):
        _record_alert(
            "NVDA", 7, _signals(), 500.0, 455.0, 1000.0,
            raw_score=9, tier=TIER_ALERT,
        )
        row = _fetch_alert(patch_db, "NVDA")
        assert row["raw_score"] == 9.0

    def test_tier_alert_persisted(self, patch_db):
        _record_alert("AMD", 6, _signals(), 120.0, 109.2, 300.0,
                      tier=TIER_ALERT)
        row = _fetch_alert(patch_db, "AMD")
        assert row["tier"] == TIER_ALERT

    def test_tier_conviction_persisted(self, patch_db):
        _record_alert("PLTR", 7, _signals(), 30.0, 27.3, 750.0,
                      tier=TIER_CONVICTION)
        row = _fetch_alert(patch_db, "PLTR")
        assert row["tier"] == TIER_CONVICTION

    def test_default_tier_is_alert(self, patch_db):
        _record_alert("MSFT", 6, _signals(), 400.0, 364.0, 800.0)
        row = _fetch_alert(patch_db, "MSFT")
        assert row["tier"] == TIER_ALERT

    def test_default_confidence_is_zero(self, patch_db):
        _record_alert("AAPL", 6, _signals(), 200.0, 182.0, 500.0)
        row = _fetch_alert(patch_db, "AAPL")
        assert row["confidence_pct"] == 0.0
        assert row["adjusted_score"] == 0.0
        assert row["raw_score"]      == 0.0

    def test_per_signal_scores_persisted(self, patch_db):
        sigs = _signals(opts=3, ins=2, sq=1, cat=0, inst=0, brk=2)
        _record_alert("AAPL", 8, sigs, 200.0, 182.0, 500.0,
                      confidence_pct=55.0, adjusted_score=4.4,
                      raw_score=8, tier=TIER_CONVICTION)
        row = _fetch_alert(patch_db, "AAPL")
        assert row["score_options"]       == 3.0
        assert row["score_insider"]       == 2.0
        assert row["score_short_squeeze"] == 1.0
        assert row["score_catalyst"]      == 0.0
        assert row["score_institutional"] == 0.0
        assert row["score_breakout"]      == 2.0

    def test_zero_signals_do_not_crash(self, patch_db):
        sigs = _signals(opts=0, ins=0, sq=0, cat=0, inst=0, brk=0)
        _record_alert("ZERO", 0, sigs, 50.0, 45.5, 100.0)
        row = _fetch_alert(patch_db, "ZERO")
        assert row is not None
        assert row["score_options"] == 0.0


# ─────────────────────────────────────────────
# _batch_upsert_latest — v2 column persistence
# ─────────────────────────────────────────────

class TestBatchUpsertLatest:
    def _result(self, ticker, score=7, confidence=55.0, adjusted=3.85,
                raw=7, tier=TIER_ALERT, price=100.0):
        return {
            "ticker":         ticker,
            "score":          score,
            "raw_score":      raw,
            "price":          price,
            "signals":        _signals(),
            "confidence":     confidence,
            "adjusted_score": adjusted,
            "active_signals": 3,
            "tier":           tier,
        }

    def test_confidence_pct_written(self, patch_db):
        _batch_upsert_latest([self._result("NVDA", confidence=61.0)])
        row = _fetch_latest(patch_db, "NVDA")
        assert row is not None
        assert abs(row["confidence_pct"] - 61.0) < 0.01

    def test_adjusted_score_written(self, patch_db):
        _batch_upsert_latest([self._result("NVDA", adjusted=4.27)])
        row = _fetch_latest(patch_db, "NVDA")
        assert abs(row["adjusted_score"] - 4.27) < 0.01

    def test_raw_score_written(self, patch_db):
        _batch_upsert_latest([self._result("NVDA", raw=9)])
        row = _fetch_latest(patch_db, "NVDA")
        assert row["raw_score"] == 9.0

    def test_alert_tier_written(self, patch_db):
        _batch_upsert_latest([self._result("NVDA", tier=TIER_ALERT)])
        row = _fetch_latest(patch_db, "NVDA")
        assert row["tier"] == TIER_ALERT

    def test_conviction_tier_written(self, patch_db):
        _batch_upsert_latest([self._result("PLTR", tier=TIER_CONVICTION)])
        row = _fetch_latest(patch_db, "PLTR")
        assert row["tier"] == TIER_CONVICTION

    def test_watch_tier_written(self, patch_db):
        _batch_upsert_latest([self._result("GME", tier=TIER_WATCH,
                                           score=6, confidence=20.0, adjusted=1.2)])
        row = _fetch_latest(patch_db, "GME")
        assert row is not None
        assert row["tier"] == TIER_WATCH

    def test_per_signal_scores_written(self, patch_db):
        r = self._result("AMD")
        r["signals"] = _signals(opts=3, ins=2, sq=1, cat=0, inst=0, brk=2)
        _batch_upsert_latest([r])
        row = _fetch_latest(patch_db, "AMD")
        assert row["score_options"]  == 3.0
        assert row["score_insider"]  == 2.0
        assert row["score_breakout"] == 2.0
        # _sig_score returns None for score=0 (same as absent signal → NULL in DB)
        assert row["score_catalyst"] is None

    def test_old_format_produces_null_new_columns(self, patch_db):
        old_result = {
            "ticker":  "MARA",
            "score":   7,
            "price":   20.0,
            "signals": _signals(),
            # NO: confidence, adjusted_score, raw_score, tier, active_signals
        }
        _batch_upsert_latest([old_result])
        row = _fetch_latest(patch_db, "MARA")
        assert row is not None
        assert row["confidence_pct"]  is None
        assert row["adjusted_score"]  is None
        assert row["raw_score"]       is None
        assert row["tier"]            is None

    def test_upsert_overwrites_previous_scan(self, patch_db):
        r1 = self._result("NVDA", score=6, confidence=40.0)
        r2 = self._result("NVDA", score=9, confidence=72.0)
        _batch_upsert_latest([r1])
        _batch_upsert_latest([r2])
        conn = sqlite3.connect(patch_db)
        count = conn.execute(
            "SELECT COUNT(*) FROM predator_latest WHERE ticker='NVDA'"
        ).fetchone()[0]
        conn.close()
        assert count == 1
        assert _fetch_latest(patch_db, "NVDA")["score"] == 9

    def test_multiple_tickers_in_one_batch(self, patch_db):
        results = [
            self._result("A", score=7),
            self._result("B", score=8),
            self._result("C", score=6),
        ]
        _batch_upsert_latest(results)
        for ticker in ("A", "B", "C"):
            assert _fetch_latest(patch_db, ticker) is not None

    def test_empty_list_does_not_crash(self, patch_db):
        _batch_upsert_latest([])  # should silently return


# ─────────────────────────────────────────────
# Routing: WATCH → latest only; ALERT/CONVICTION → both tables
# ─────────────────────────────────────────────

class TestTierRouting:
    """
    Simulates the routing block from run_predator() without calling it directly
    (which would require a full scheduler + live data stack).
    """

    def _route(self, tier, score, db_path, ticker="TICK"):
        signals    = _signals()
        price      = 100.0
        stop       = round(price * 0.91, 2)
        position   = 1000.0
        confidence = 60.0
        adjusted   = round(score * confidence / 100, 2)
        raw        = float(score)

        result = {
            "ticker":         ticker,
            "score":          score,
            "raw_score":      raw,
            "price":          price,
            "signals":        signals,
            "confidence":     confidence,
            "adjusted_score": adjusted,
            "active_signals": 3,
            "tier":           tier,
        }

        # Exact routing logic from run_predator()
        if score >= ALERT_THRESHOLD:
            if tier in (TIER_ALERT, TIER_CONVICTION):
                _record_alert(
                    ticker, score, signals, price, stop, position,
                    confidence_pct=confidence,
                    adjusted_score=adjusted,
                    raw_score=raw,
                    tier=tier,
                )

        _batch_upsert_latest([result])

    def test_alert_tier_written_to_both_tables(self, patch_db):
        self._route(TIER_ALERT, ALERT_THRESHOLD, patch_db, "ALERT_BOTH")
        assert _fetch_alert(patch_db,  "ALERT_BOTH") is not None
        assert _fetch_latest(patch_db, "ALERT_BOTH") is not None

    def test_conviction_tier_written_to_both_tables(self, patch_db):
        self._route(TIER_CONVICTION, 8, patch_db, "CONV_BOTH")
        assert _fetch_alert(patch_db,  "CONV_BOTH") is not None
        assert _fetch_latest(patch_db, "CONV_BOTH") is not None

    def test_watch_tier_written_only_to_latest(self, patch_db):
        self._route(TIER_WATCH, ALERT_THRESHOLD, patch_db, "WATCHONLY")
        assert _fetch_alert(patch_db,  "WATCHONLY") is None
        assert _fetch_latest(patch_db, "WATCHONLY") is not None

    def test_sub_threshold_not_written_to_predator_alerts(self, patch_db):
        self._route(TIER_ALERT, ALERT_THRESHOLD - 1, patch_db, "SUBTHRESH")
        assert _fetch_alert(patch_db,  "SUBTHRESH") is None
        assert _fetch_latest(patch_db, "SUBTHRESH") is not None

    def test_alert_row_has_correct_tier(self, patch_db):
        self._route(TIER_CONVICTION, 8, patch_db, "TIER_CONV")
        row = _fetch_alert(patch_db, "TIER_CONV")
        assert row["tier"] == TIER_CONVICTION

    def test_alert_row_has_correct_confidence(self, patch_db):
        self._route(TIER_ALERT, 7, patch_db, "CONF_CHECK")
        row = _fetch_alert(patch_db, "CONF_CHECK")
        assert abs(row["confidence_pct"] - 60.0) < 0.01

    def test_alert_row_has_correct_adjusted_score(self, patch_db):
        self._route(TIER_ALERT, 7, patch_db, "ADJ_CHECK")
        row = _fetch_alert(patch_db, "ADJ_CHECK")
        assert abs(row["adjusted_score"] - 4.2) < 0.01

    def test_latest_row_stores_watch_tier(self, patch_db):
        self._route(TIER_WATCH, ALERT_THRESHOLD, patch_db, "LATEST_WATCH")
        row = _fetch_latest(patch_db, "LATEST_WATCH")
        assert row["tier"] == TIER_WATCH


# ─────────────────────────────────────────────
# _score_ticker() — result dict completeness
# ─────────────────────────────────────────────

class TestScoreTickerResultKeys:
    """
    Verify _score_ticker() returns a dict with all expected keys.
    Sub-scorers and external data are patched to safe zero-score defaults.
    """

    _EXPECTED_KEYS = {
        "ticker", "score", "raw_score", "price", "signals",
        "confidence", "adjusted_score", "active_signals", "tier",
    }

    @pytest.fixture()
    def patched_result(self):
        zero_sig  = SignalResult(score=0, reason="", data_quality="MEDIUM")
        fake_data = {
            "price": 100.0, "rsi": 55.0, "macd": 0.5, "ma50": 95.0,
            "ma200": 90.0, "volume_ratio": 1.2, "pct_change_1d": 1.5,
            "short_float": 0.05, "shares_float": 1e8, "closes": None,
        }
        with patch("predator.get_ticker_data", return_value=fake_data), \
             patch("predator._score_options",       return_value=zero_sig), \
             patch("predator._score_insider",       return_value=zero_sig), \
             patch("predator._score_short_squeeze", return_value=(0, "")), \
             patch("predator._score_catalyst",      return_value=(0, "")), \
             patch("predator._score_institutional", return_value=(0, "")), \
             patch("predator._score_breakout",      return_value=(0, "")):
            return predator._score_ticker("NVDA")

    def test_result_is_not_none(self, patched_result):
        assert patched_result is not None

    def test_all_expected_keys_present(self, patched_result):
        assert self._EXPECTED_KEYS.issubset(patched_result.keys())

    def test_ticker_matches_input(self, patched_result):
        assert patched_result["ticker"] == "NVDA"

    def test_score_is_numeric(self, patched_result):
        assert isinstance(patched_result["score"], (int, float))

    def test_raw_score_is_numeric(self, patched_result):
        assert isinstance(patched_result["raw_score"], (int, float))

    def test_confidence_in_valid_range(self, patched_result):
        assert 0.0 <= patched_result["confidence"] <= 100.0

    def test_adjusted_score_does_not_exceed_score(self, patched_result):
        assert patched_result["adjusted_score"] <= patched_result["score"]

    def test_tier_is_valid(self, patched_result):
        assert patched_result["tier"] in (TIER_WATCH, TIER_ALERT, TIER_CONVICTION)

    def test_signals_options_has_data_quality(self, patched_result):
        assert "data_quality" in patched_result["signals"]["options"]

    def test_signals_insider_has_data_quality(self, patched_result):
        assert "data_quality" in patched_result["signals"]["insider"]

    def test_returns_none_when_no_data(self):
        with patch("predator.get_ticker_data", return_value=None):
            result = predator._score_ticker("BADTICKER")
        assert result is None
