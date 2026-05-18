"""
Phase A3 — fetch_alpha_input() robustness tests.

All tests mock yfinance and market_data so no network calls are made.
The goal: fetch_alpha_input() returns an AlphaInput for every ticker that
has ≥5 bars of price history, even when info/options/short-interest are missing.

Patch targets:
  yfinance.Ticker           — yf is imported locally, so patch the real module
  market_data.get_ticker_data / market_data.ma200_recent_cross
  alpha_engine.fetch_alpha_input — alpha_shadow does 'from alpha_engine import'
"""
import os
import sqlite3
import sys
from unittest.mock import MagicMock, PropertyMock, patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from alpha_engine import AlphaInput, fetch_alpha_input


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_hist(n: int = 30, price: float = 100.0) -> pd.DataFrame:
    """Minimal OHLCV DataFrame with n rows."""
    prices = [price + i * 0.1 for i in range(n)]
    return pd.DataFrame({
        "Open":   prices,
        "High":   [p + 1.0 for p in prices],
        "Low":    [p - 1.0 for p in prices],
        "Close":  prices,
        "Volume": [1_000_000.0] * n,
    })


def _make_ticker(hist: pd.DataFrame, info: dict = None, options=(), raises_info=False):
    """Build a mock yf.Ticker whose history() returns hist."""
    tkr = MagicMock()
    tkr.history.return_value = hist
    tkr.options              = options
    tkr.calendar             = None

    if raises_info:
        type(tkr).info = PropertyMock(side_effect=Exception("HTTP 404"))
    else:
        type(tkr).info = PropertyMock(return_value=info if info is not None else {})

    return tkr


def _patch_yf(ticker_mock, vix_price=18.0):
    """
    Patch yfinance.Ticker (the actual module attribute — yf is imported locally).

    yf.Ticker(sym) routes:
      '^VIX'              → minimal mock with 2-bar VIX hist
      'SPY'/'QQQ'/'XUS.TO'→ 40-bar benchmark hist
      anything else       → ticker_mock
    """
    vix_df = _make_hist(2, price=vix_price)

    def _factory(sym):
        if sym == "^VIX":
            m = MagicMock()
            m.history.return_value = vix_df
            return m
        if sym in ("SPY", "QQQ", "XUS.TO"):
            m = MagicMock()
            m.history.return_value = _make_hist(40, price=500.0)
            return m
        return ticker_mock

    return patch("yfinance.Ticker", side_effect=_factory)


def _patch_mdata(price=100.0, rsi=55.0, ma200=95.0):
    """
    Return (patch_get_ticker_data, patch_ma200_cross) context managers.
    Both patch the real market_data module attributes.
    """
    closes = pd.Series([price - 2, price - 1, price])

    mdata = {
        "price":       price,
        "rsi":         rsi,
        "macd_val":    0.5,
        "macd_signal": 0.3,
        "ma50":        97.0,
        "ma200":       ma200,
        "closes":      closes,
    }

    return (
        patch("market_data.get_ticker_data", return_value=mdata),
        patch("market_data.ma200_recent_cross", return_value=(False, 0)),
    )


# ── Core: normal ticker with all optional fields ──────────────────────────────

class TestFetchAlphaInputHappyPath:
    def test_returns_alpha_input_not_none(self):
        hist = _make_hist(30)
        tkr  = _make_ticker(hist, info={"marketCap": 1_000_000_000,
                                        "fiftyTwoWeekHigh": 115.0,
                                        "fiftyTwoWeekLow": 85.0})
        p, m = _patch_mdata()
        with _patch_yf(tkr), p, m:
            result = fetch_alpha_input("NVDA")
        assert result is not None
        assert isinstance(result, AlphaInput)

    def test_ticker_field_set(self):
        tkr = _make_ticker(_make_hist(30))
        p, m = _patch_mdata()
        with _patch_yf(tkr), p, m:
            result = fetch_alpha_input("NVDA")
        assert result.ticker == "NVDA"

    def test_price_from_market_data(self):
        tkr = _make_ticker(_make_hist(30, price=250.0))
        p, m = _patch_mdata(price=250.0)
        with _patch_yf(tkr), p, m:
            result = fetch_alpha_input("NVDA")
        assert result.price == pytest.approx(250.0)

    def test_volume_populated(self):
        tkr = _make_ticker(_make_hist(30))
        p, m = _patch_mdata()
        with _patch_yf(tkr), p, m:
            result = fetch_alpha_input("NVDA")
        assert result.avg_volume_20d is not None
        assert result.avg_volume_20d > 0

    def test_rsi_from_market_data(self):
        tkr = _make_ticker(_make_hist(30))
        p, m = _patch_mdata(rsi=62.5)
        with _patch_yf(tkr), p, m:
            result = fetch_alpha_input("NVDA")
        assert result.rsi == pytest.approx(62.5)


# ── info unavailable ──────────────────────────────────────────────────────────

class TestInfoUnavailable:
    def test_info_raises_still_returns_input(self):
        tkr = _make_ticker(_make_hist(30), raises_info=True)
        p, m = _patch_mdata()
        with _patch_yf(tkr), p, m:
            result = fetch_alpha_input("NVDA")
        assert result is not None

    def test_info_raises_52w_fields_none(self):
        tkr = _make_ticker(_make_hist(30), raises_info=True)
        p, m = _patch_mdata()
        with _patch_yf(tkr), p, m:
            result = fetch_alpha_input("NVDA")
        assert result.price_high_52w is None
        assert result.price_low_52w  is None

    def test_empty_info_still_returns_input(self):
        tkr = _make_ticker(_make_hist(30), info={})
        p, m = _patch_mdata()
        with _patch_yf(tkr), p, m:
            result = fetch_alpha_input("NVDA")
        assert result is not None

    def test_missing_short_interest_is_none(self):
        tkr = _make_ticker(_make_hist(30), info={})
        p, m = _patch_mdata()
        with _patch_yf(tkr), p, m:
            result = fetch_alpha_input("NVDA")
        assert result.short_percent_float is None

    def test_missing_market_cap_is_none(self):
        tkr = _make_ticker(_make_hist(30), info={})
        p, m = _patch_mdata()
        with _patch_yf(tkr), p, m:
            result = fetch_alpha_input("NVDA")
        assert result.market_cap_millions is None


# ── market_data unavailable → price from raw hist ─────────────────────────────

class TestMarketDataUnavailable:
    def test_market_data_raises_uses_hist_price(self):
        """price must come from yfinance history when market_data fails."""
        hist = _make_hist(30, price=200.0)
        tkr  = _make_ticker(hist)
        with _patch_yf(tkr), \
             patch("market_data.get_ticker_data", side_effect=RuntimeError("down")):
            result = fetch_alpha_input("NVDA")
        assert result is not None
        assert result.price == pytest.approx(hist["Close"].iloc[-1])

    def test_market_data_raises_rsi_is_none(self):
        tkr = _make_ticker(_make_hist(30))
        with _patch_yf(tkr), \
             patch("market_data.get_ticker_data", side_effect=RuntimeError("down")):
            result = fetch_alpha_input("NVDA")
        assert result.rsi is None


# ── Insufficient history → None ───────────────────────────────────────────────

class TestInsufficientHistory:
    def test_empty_dataframe_returns_none(self):
        tkr = _make_ticker(pd.DataFrame())
        p, m = _patch_mdata()
        with _patch_yf(tkr), p, m:
            assert fetch_alpha_input("NVDA") is None

    def test_fewer_than_5_bars_returns_none(self):
        tkr = _make_ticker(_make_hist(3))
        p, m = _patch_mdata()
        with _patch_yf(tkr), p, m:
            assert fetch_alpha_input("NVDA") is None

    def test_history_raises_returns_none(self):
        tkr = MagicMock()
        tkr.history.side_effect = Exception("connection reset")
        type(tkr).info = PropertyMock(return_value={})
        p, m = _patch_mdata()
        with _patch_yf(tkr), p, m:
            assert fetch_alpha_input("NVDA") is None

    def test_exactly_5_bars_returns_input(self):
        tkr = _make_ticker(_make_hist(5))
        p, m = _patch_mdata()
        with _patch_yf(tkr), p, m:
            result = fetch_alpha_input("NVDA")
        assert result is not None


# ── Sparse optional fields — AlphaInput still valid ──────────────────────────

class TestSparseOptionalFields:
    def _result_no_options_no_earnings(self):
        hist = _make_hist(30)
        tkr  = _make_ticker(hist, info={})
        tkr.options  = ()
        tkr.calendar = None
        p, m = _patch_mdata()
        with _patch_yf(tkr), p, m:
            return fetch_alpha_input("NVDA")

    def test_no_options_returns_input(self):
        assert self._result_no_options_no_earnings() is not None

    def test_no_options_fields_are_none_or_false(self):
        r = self._result_no_options_no_earnings()
        assert r.call_volume    is None
        assert r.put_volume     is None
        assert r.unusual_options is False

    def test_no_earnings_returns_input(self):
        assert self._result_no_options_no_earnings() is not None

    def test_no_earnings_field_is_none(self):
        assert self._result_no_options_no_earnings().earnings_days is None

    def test_price_windows_from_hist(self):
        r = self._result_no_options_no_earnings()
        assert r.price_5d_ago  is not None
        assert r.price_20d_ago is not None

    def test_atr_computed(self):
        r = self._result_no_options_no_earnings()
        assert r.atr is not None and r.atr > 0

    def test_is_canadian_true_for_dot_to(self):
        tkr = _make_ticker(_make_hist(30), info={})
        p, m = _patch_mdata()
        with _patch_yf(tkr), p, m:
            result = fetch_alpha_input("VFV.TO")
        assert result is not None
        assert result.is_canadian is True

    def test_is_canadian_false_for_us(self):
        tkr = _make_ticker(_make_hist(30), info={})
        p, m = _patch_mdata()
        with _patch_yf(tkr), p, m:
            result = fetch_alpha_input("NVDA")
        assert result.is_canadian is False


# ── Shadow manager persists row with sparse input ─────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS alpha_shadow_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL,
    scan_time TEXT NOT NULL, alpha_score REAL, alpha_tier TEXT,
    setup_type TEXT, predator_tier TEXT, predator_score REAL,
    tier_match INTEGER NOT NULL DEFAULT 0, filter_reason TEXT,
    component_scores_json TEXT, explanation TEXT
)
"""


def _row_conn(conn):
    conn.row_factory = sqlite3.Row
    return conn


class TestShadowManagerWithSparseInput:
    def _db(self, path):
        conn = sqlite3.connect(path)
        conn.execute(_CREATE_TABLE)
        conn.commit()
        conn.close()
        return path

    def test_persists_row_when_all_optional_fields_none(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = self._db(str(tmp_path / "t.db"))
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))

        sparse = AlphaInput(ticker="NVDA", price=100.0)

        from alpha_shadow import AlphaShadowManager
        # Patch at alpha_engine level (run_shadow_score does: from alpha_engine import fetch_alpha_input)
        with patch("alpha_engine.fetch_alpha_input", return_value=sparse):
            AlphaShadowManager().run_shadow_score("NVDA", {"tier": "WATCH", "score": 5.0})

        conn = sq.connect(path)
        row  = conn.execute("SELECT * FROM alpha_shadow_log WHERE ticker='NVDA'").fetchone()
        conn.close()
        assert row is not None

    def test_persists_row_when_info_unavailable_but_history_works(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = self._db(str(tmp_path / "t.db"))
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))

        no_info_input = AlphaInput(
            ticker="NVDA", price=250.0,
            price_5d_ago=240.0, price_20d_ago=230.0,
            avg_volume_20d=5_000_000.0,
            rsi=58.0, ma_200=230.0,
            price_high_52w=None, short_percent_float=None, market_cap_millions=None,
        )

        from alpha_shadow import AlphaShadowManager
        with patch("alpha_engine.fetch_alpha_input", return_value=no_info_input):
            AlphaShadowManager().run_shadow_score("NVDA", {"tier": "ALERT", "score": 7.5})

        conn = sq.connect(path)
        count = conn.execute(
            "SELECT COUNT(*) FROM alpha_shadow_log WHERE ticker='NVDA'"
        ).fetchone()[0]
        conn.close()
        assert count == 1

    def test_alpha_score_stored_correctly(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = self._db(str(tmp_path / "t.db"))
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))

        inp = AlphaInput(ticker="MSFT", price=400.0)
        from alpha_shadow import AlphaShadowManager
        with patch("alpha_engine.fetch_alpha_input", return_value=inp):
            AlphaShadowManager().run_shadow_score("MSFT", {"tier": "CONVICTION", "score": 9.0})

        conn = sq.connect(path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM alpha_shadow_log WHERE ticker='MSFT'").fetchone()
        conn.close()
        assert row is not None
        # alpha_score should be populated (engine computes from AlphaInput)
        assert row["alpha_score"] is not None
        assert row["predator_tier"] == "CONVICTION"
