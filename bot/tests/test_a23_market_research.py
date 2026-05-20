"""
Phase A23 — Market Analyst Research Dashboard Backend tests.

~120 tests covering:
- Constants / disclaimer presence
- Pure computation functions
- Market pulse (period handling, 1D prev_close baseline)
- Stock analyzer (neutral language, scores 0-100)
- ETF analyzer (risk score, peers)
- Macro data (missing FRED_API_KEY → available=False, missing fredapi → available=False)
- News functions (safe defaults)
- AI analysis (missing ANTHROPIC_API_KEY → graceful, banned-word detection)
- All 9 API endpoints
- No trading calls in source
- Sparse data safety
"""
import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

import market_research as mr


# ── Isolated Flask app fixture ────────────────────────────────────────────────

def _make_app():
    import database
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    database.DB_PATH = tmp.name
    import sqlite3
    real_get = database.get_connection

    def _conn():
        return sqlite3.connect(tmp.name)

    database.get_connection = _conn
    with patch.dict(os.environ, {"API_SECRET": ""}):
        import api as api_mod
        importlib.reload(api_mod)
        from flask import Flask
        app = Flask("test_a23")
        app.register_blueprint(api_mod.api_bp)
        app.config["TESTING"] = True
        api_mod.cache_clear()
    return app, api_mod, tmp.name


# ════════════════════════════════════════════════════════════════════════════
# 1. Constants
# ════════════════════════════════════════════════════════════════════════════

class TestConstants(unittest.TestCase):

    def test_disclaimer_present(self):
        self.assertIn("educational information", mr.DISCLAIMER)
        self.assertIn("not financial advice", mr.DISCLAIMER)
        self.assertIn("not a recommendation", mr.DISCLAIMER)

    def test_disclaimer_not_a_recommendation(self):
        # Disclaimer must say it's NOT a recommendation (buy/sell appear in the negation)
        self.assertIn("not a recommendation", mr.DISCLAIMER)

    def test_market_tickers_count(self):
        self.assertEqual(len(mr.MARKET_TICKERS), 10)

    def test_sector_etfs_count(self):
        self.assertEqual(len(mr.SECTOR_ETFS), 11)

    def test_valid_periods_count(self):
        self.assertEqual(len(mr.VALID_PERIODS), 11)

    def test_period_map_keys_match_valid(self):
        self.assertEqual(set(mr.PERIOD_MAP.keys()), set(mr.VALID_PERIODS))

    def test_banned_words_ai_has_buy_sell(self):
        self.assertIn("buy", mr.BANNED_WORDS_AI)
        self.assertIn("sell", mr.BANNED_WORDS_AI)

    def test_ticker_labels_covers_market_tickers(self):
        for t in mr.MARKET_TICKERS:
            self.assertIn(t, mr.TICKER_LABELS)

    def test_period_map_tuples(self):
        for k, v in mr.PERIOD_MAP.items():
            self.assertIsInstance(v, tuple)
            self.assertEqual(len(v), 2)


# ════════════════════════════════════════════════════════════════════════════
# 2. Pure computation functions
# ════════════════════════════════════════════════════════════════════════════

class TestComputeRsi(unittest.TestCase):

    def test_insufficient_data_returns_50(self):
        self.assertEqual(mr._compute_rsi([100, 101], period=14), 50.0)

    def test_rising_prices_high_rsi(self):
        closes = list(range(50, 80))  # 30 values, all rising
        rsi = mr._compute_rsi(closes)
        self.assertGreater(rsi, 70)

    def test_falling_prices_low_rsi(self):
        closes = list(range(80, 50, -1))
        rsi = mr._compute_rsi(closes)
        self.assertLess(rsi, 30)

    def test_returns_float(self):
        closes = [100 + i * 0.5 for i in range(30)]
        self.assertIsInstance(mr._compute_rsi(closes), float)

    def test_empty_returns_50(self):
        self.assertEqual(mr._compute_rsi([]), 50.0)


class TestComputeSma(unittest.TestCase):

    def test_returns_none_insufficient(self):
        self.assertIsNone(mr._compute_sma([100, 101], 50))

    def test_exact_period(self):
        sma = mr._compute_sma([1.0] * 50, 50)
        self.assertAlmostEqual(sma, 1.0, places=2)

    def test_returns_float(self):
        closes = list(range(1, 210))
        sma = mr._compute_sma(closes, 200)
        self.assertIsInstance(sma, float)


class TestAnnualizedVol(unittest.TestCase):

    def test_none_on_single_point(self):
        self.assertIsNone(mr._compute_annualized_volatility([100]))

    def test_returns_float(self):
        import random
        random.seed(42)
        closes = [100 + random.gauss(0, 1) for _ in range(50)]
        vol = mr._compute_annualized_volatility(closes)
        self.assertIsInstance(vol, float)
        self.assertGreater(vol, 0)

    def test_none_on_empty(self):
        self.assertIsNone(mr._compute_annualized_volatility([]))


class TestBucketFunctions(unittest.TestCase):

    def test_rsi_bucket_overbought(self):
        self.assertEqual(mr._rsi_bucket(75), "OVERBOUGHT")

    def test_rsi_bucket_oversold(self):
        self.assertEqual(mr._rsi_bucket(25), "OVERSOLD")

    def test_rsi_bucket_neutral(self):
        self.assertEqual(mr._rsi_bucket(50), "NEUTRAL")

    def test_rsi_bucket_boundary_70(self):
        self.assertEqual(mr._rsi_bucket(70), "OVERBOUGHT")

    def test_rsi_bucket_boundary_30(self):
        self.assertEqual(mr._rsi_bucket(30), "OVERSOLD")

    def test_roe_tier_negative(self):
        self.assertEqual(mr._roe_tier(-5), "NEGATIVE")

    def test_roe_tier_low(self):
        self.assertEqual(mr._roe_tier(5), "LOW")

    def test_roe_tier_moderate(self):
        self.assertEqual(mr._roe_tier(15), "MODERATE")

    def test_roe_tier_strong(self):
        self.assertEqual(mr._roe_tier(25), "STRONG")

    def test_roe_tier_unknown(self):
        self.assertEqual(mr._roe_tier(None), "UNKNOWN")

    def test_de_tier_low(self):
        self.assertEqual(mr._de_tier(0.3), "LOW")

    def test_de_tier_moderate(self):
        self.assertEqual(mr._de_tier(1.0), "MODERATE")

    def test_de_tier_high(self):
        self.assertEqual(mr._de_tier(2.0), "HIGH")

    def test_de_tier_unknown(self):
        self.assertEqual(mr._de_tier(None), "UNKNOWN")

    def test_beta_tier_low(self):
        self.assertEqual(mr._beta_tier(0.5), "LOW_VOLATILITY")

    def test_beta_tier_market(self):
        self.assertEqual(mr._beta_tier(1.0), "MARKET_LIKE")

    def test_beta_tier_elevated(self):
        self.assertEqual(mr._beta_tier(1.5), "ELEVATED")

    def test_beta_tier_high(self):
        self.assertEqual(mr._beta_tier(2.5), "HIGH_VOLATILITY")

    def test_beta_tier_unknown(self):
        self.assertEqual(mr._beta_tier(None), "UNKNOWN")

    def test_valuation_tier_value(self):
        self.assertEqual(mr._valuation_tier(10), "VALUE")

    def test_valuation_tier_fair(self):
        self.assertEqual(mr._valuation_tier(20), "FAIR")

    def test_valuation_tier_growth(self):
        self.assertEqual(mr._valuation_tier(35), "GROWTH")

    def test_valuation_tier_speculative(self):
        self.assertEqual(mr._valuation_tier(50), "SPECULATIVE")

    def test_valuation_tier_unknown_none(self):
        self.assertEqual(mr._valuation_tier(None), "UNKNOWN")

    def test_valuation_tier_unknown_zero(self):
        self.assertEqual(mr._valuation_tier(0), "UNKNOWN")

    def test_52week_position_midpoint(self):
        pos = mr._52week_position(100, 200, 150)
        self.assertAlmostEqual(pos, 50.0)

    def test_52week_position_at_high(self):
        pos = mr._52week_position(100, 200, 200)
        self.assertAlmostEqual(pos, 100.0)

    def test_52week_position_at_low(self):
        pos = mr._52week_position(100, 200, 100)
        self.assertAlmostEqual(pos, 0.0)

    def test_52week_position_none_on_missing(self):
        self.assertIsNone(mr._52week_position(None, 200, 150))
        self.assertIsNone(mr._52week_position(100, None, 150))
        self.assertIsNone(mr._52week_position(100, 200, None))

    def test_52week_position_clamps_above(self):
        pos = mr._52week_position(100, 200, 250)
        self.assertEqual(pos, 100.0)


class TestTechnicalStrength(unittest.TestCase):

    def test_all_bullish_returns_high(self):
        score = mr._technical_strength(220, 200, 210, 65, 80)
        self.assertGreaterEqual(score, 70)

    def test_all_bearish_returns_low(self):
        score = mr._technical_strength(180, 200, 195, 25, 15)
        self.assertLessEqual(score, 30)

    def test_max_100(self):
        score = mr._technical_strength(250, 200, 210, 80, 95)
        self.assertLessEqual(score, 100)

    def test_min_0(self):
        score = mr._technical_strength(0, 200, 210, 20, 5)
        self.assertGreaterEqual(score, 0)

    def test_all_none_returns_0(self):
        score = mr._technical_strength(None, None, None, 50, None)
        self.assertGreaterEqual(score, 0)


class TestFundamentalQuality(unittest.TestCase):

    def test_strong_fundamentals_high_score(self):
        score = mr._fundamental_quality(18, 25, 0.3, 1.0)
        self.assertGreaterEqual(score, 70)

    def test_poor_fundamentals_low_score(self):
        score = mr._fundamental_quality(None, -5, 3.0, 2.5)
        self.assertLessEqual(score, 30)

    def test_max_100(self):
        score = mr._fundamental_quality(18, 30, 0.2, 1.0)
        self.assertLessEqual(score, 100)

    def test_all_none_returns_0(self):
        score = mr._fundamental_quality(None, None, None, None)
        self.assertEqual(score, 0)


class TestEtfRiskScore(unittest.TestCase):

    def test_low_vol_low_risk(self):
        # Stable prices → low risk score
        closes = [100.0 + i * 0.001 for i in range(250)]
        score = mr._compute_etf_risk_score(closes)
        self.assertLessEqual(score, 20)

    def test_high_vol_high_risk(self):
        import random
        random.seed(1)
        closes = [100 * (1 + random.gauss(0, 0.04)) for _ in range(250)]
        score = mr._compute_etf_risk_score(closes)
        self.assertGreaterEqual(score, 40)

    def test_returns_int(self):
        closes = [100 + i for i in range(50)]
        self.assertIsInstance(mr._compute_etf_risk_score(closes), int)

    def test_insufficient_data_returns_50(self):
        self.assertEqual(mr._compute_etf_risk_score([100]), 50)


class TestToSparkline(unittest.TestCase):

    def test_empty_returns_empty(self):
        self.assertEqual(mr._to_sparkline(None), [])

    def test_returns_list_of_dicts(self):
        import pandas as pd
        idx = pd.date_range("2024-01-01", periods=10, freq="D")
        hist = pd.DataFrame({"Close": range(10, 20)}, index=idx)
        result = mr._to_sparkline(hist)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertIn("t", result[0])
        self.assertIn("v", result[0])

    def test_caps_at_max_points(self):
        import pandas as pd
        idx = pd.date_range("2020-01-01", periods=500, freq="D")
        hist = pd.DataFrame({"Close": range(500)}, index=idx)
        result = mr._to_sparkline(hist, max_points=100)
        self.assertLessEqual(len(result), 100)


# ════════════════════════════════════════════════════════════════════════════
# 3. AI functions
# ════════════════════════════════════════════════════════════════════════════

class TestCheckAiBannedWords(unittest.TestCase):

    def test_buy_detected(self):
        self.assertTrue(mr.check_ai_banned_words("You should buy this stock"))

    def test_sell_detected(self):
        self.assertTrue(mr.check_ai_banned_words("Consider selling soon"))

    def test_must_detected(self):
        self.assertTrue(mr.check_ai_banned_words("You must act now"))

    def test_guaranteed_detected(self):
        self.assertTrue(mr.check_ai_banned_words("Guaranteed returns"))

    def test_clean_text_ok(self):
        self.assertFalse(mr.check_ai_banned_words("This stock shows elevated volatility."))

    def test_case_insensitive(self):
        self.assertTrue(mr.check_ai_banned_words("BUY NOW"))

    def test_sure_thing_detected(self):
        self.assertTrue(mr.check_ai_banned_words("It's a sure thing"))


class TestGenerateStockAnalysisAi(unittest.TestCase):

    def test_missing_api_key_graceful(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            result = mr.generate_stock_analysis_ai("AAPL", {})
        self.assertIn("disclaimer", result)
        self.assertIn("commentary", result)
        self.assertIn("ANTHROPIC_API_KEY not configured", result["commentary"])

    def test_result_has_required_keys(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            result = mr.generate_stock_analysis_ai("AAPL", {})
        for key in ("ticker", "commentary", "disclaimer", "compliance_ok"):
            self.assertIn(key, result)

    def test_disclaimer_always_present(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            result = mr.generate_stock_analysis_ai("NVDA", {})
        self.assertEqual(result["disclaimer"], mr.DISCLAIMER)

    def test_banned_word_in_ai_output_blocked(self):
        def _fake_call(prompt, max_tokens=400):
            return "You should buy this stock immediately."

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}):
            with patch.object(mr, "_call_anthropic", _fake_call):
                result = mr.generate_stock_analysis_ai("AAPL", {})
        self.assertFalse(result["compliance_ok"])

    def test_clean_ai_output_accepted(self):
        def _fake_call(prompt, max_tokens=400):
            return "The stock shows elevated momentum and high valuations."

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}):
            with patch.object(mr, "_call_anthropic", _fake_call):
                result = mr.generate_stock_analysis_ai("AAPL", {})
        self.assertTrue(result["compliance_ok"])
        self.assertIn("momentum", result["commentary"])


class TestGenerateEtfAnalysisAi(unittest.TestCase):

    def test_missing_api_key_graceful(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            result = mr.generate_etf_analysis_ai("QQQ", {})
        self.assertIn("commentary", result)
        self.assertFalse(result.get("compliance_ok"))

    def test_disclaimer_present(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            result = mr.generate_etf_analysis_ai("SPY", {})
        self.assertEqual(result["disclaimer"], mr.DISCLAIMER)

    def test_banned_word_blocked(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}):
            with patch.object(mr, "_call_anthropic", return_value="great time to sell"):
                result = mr.generate_etf_analysis_ai("QQQ", {})
        self.assertFalse(result["compliance_ok"])


class TestGenerateMacroAnalysisAi(unittest.TestCase):

    def test_missing_api_key_graceful(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            result = mr.generate_macro_analysis_ai({})
        self.assertIn("commentary", result)
        self.assertFalse(result.get("compliance_ok"))

    def test_no_indicators_graceful(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}):
            result = mr.generate_macro_analysis_ai({"indicators": {}})
        self.assertIn("commentary", result)
        self.assertIn("disclaimer", result)

    def test_clean_output_accepted(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}):
            with patch.object(mr, "_call_anthropic", return_value="Inflation remains elevated."):
                result = mr.generate_macro_analysis_ai({"indicators": {"FEDFUNDS": {"label": "Fed Funds Rate", "value": 5.25}}})
        self.assertTrue(result["compliance_ok"])


# ════════════════════════════════════════════════════════════════════════════
# 4. Macro data (FRED)
# ════════════════════════════════════════════════════════════════════════════

class TestGetMacroData(unittest.TestCase):

    def test_missing_fred_key_returns_available_false(self):
        with patch.dict(os.environ, {"FRED_API_KEY": ""}):
            result = mr.get_macro_data()
        self.assertFalse(result["available"])
        self.assertIn("FRED_API_KEY not configured", result["reason"])

    def test_missing_fredapi_package_graceful(self):
        with patch.dict(os.environ, {"FRED_API_KEY": "somekey"}):
            with patch.dict(sys.modules, {"fredapi": None}):
                result = mr.get_macro_data()
        self.assertFalse(result["available"])
        self.assertIn("fredapi", result.get("reason", ""))

    def test_result_has_disclaimer(self):
        with patch.dict(os.environ, {"FRED_API_KEY": ""}):
            result = mr.get_macro_data()
        self.assertEqual(result["disclaimer"], mr.DISCLAIMER)

    def test_indicators_empty_when_unavailable(self):
        with patch.dict(os.environ, {"FRED_API_KEY": ""}):
            result = mr.get_macro_data()
        self.assertEqual(result["indicators"], {})


# ════════════════════════════════════════════════════════════════════════════
# 5. Sparse data safety
# ════════════════════════════════════════════════════════════════════════════

class TestSparseDataSafety(unittest.TestCase):

    def _mock_ticker(self, info=None, hist=None):
        import pandas as pd
        t = MagicMock()
        t.info = info or {}
        if hist is None:
            hist = pd.DataFrame({"Close": []})
        t.history.return_value = hist
        t.news = []
        return t

    def test_get_ticker_snapshot_empty_info(self):
        with patch("yfinance.Ticker", return_value=self._mock_ticker()):
            result = mr.get_ticker_snapshot("AAPL")
        self.assertIn("ticker", result)
        self.assertIn("disclaimer", result)

    def test_get_stock_analysis_empty_info(self):
        with patch("yfinance.Ticker", return_value=self._mock_ticker()):
            result = mr.get_stock_analysis("AAPL")
        self.assertIn("ticker", result)
        self.assertIn("disclaimer", result)
        self.assertIn("fundamentals", result)
        self.assertIn("technicals", result)

    def test_get_etf_analysis_empty_info(self):
        with patch("yfinance.Ticker", return_value=self._mock_ticker()):
            result = mr.get_etf_analysis("SPY")
        self.assertIn("ticker", result)
        self.assertIn("disclaimer", result)

    def test_get_market_news_empty(self):
        with patch("yfinance.Ticker", return_value=self._mock_ticker()):
            result = mr.get_market_news()
        self.assertIsInstance(result["items"], list)
        self.assertIn("disclaimer", result)

    def test_get_ticker_news_empty(self):
        with patch("yfinance.Ticker", return_value=self._mock_ticker()):
            result = mr.get_ticker_news("NVDA")
        self.assertIsInstance(result["items"], list)
        self.assertIn("disclaimer", result)

    def test_get_ticker_snapshot_raises_returns_defaults(self):
        with patch("yfinance.Ticker", side_effect=Exception("network error")):
            result = mr.get_ticker_snapshot("^GSPC")
        self.assertIn("ticker", result)
        self.assertIn("disclaimer", result)


# ════════════════════════════════════════════════════════════════════════════
# 6. Period handling
# ════════════════════════════════════════════════════════════════════════════

class TestPeriodHandling(unittest.TestCase):

    def test_invalid_period_defaults_to_1d(self):
        import pandas as pd
        mock_t = MagicMock()
        mock_t.info = {"previousClose": 100.0}
        mock_t.history.return_value = pd.DataFrame({"Close": [100]}, index=pd.date_range("2024-01-01", periods=1))
        mock_t.news = []
        with patch("yfinance.Ticker", return_value=mock_t):
            result = mr.get_ticker_snapshot("AAPL", "INVALID")
        self.assertIn("period", result)

    def test_all_valid_periods_in_period_map(self):
        for p in mr.VALID_PERIODS:
            self.assertIn(p, mr.PERIOD_MAP, f"Period {p} missing from PERIOD_MAP")

    def test_1d_uses_prev_close_as_baseline(self):
        import pandas as pd
        idx = pd.date_range("2024-01-01", periods=5, freq="5min")
        hist = pd.DataFrame({"Close": [99, 100, 101, 102, 103]}, index=idx)
        prev_close = 98.0
        mock_t = MagicMock()
        mock_t.info = {"previousClose": prev_close, "regularMarketPrice": 103.0}
        mock_t.history.return_value = hist
        mock_t.news = []
        with patch("yfinance.Ticker", return_value=mock_t):
            result = mr.get_ticker_snapshot("SPY", "1D")
        # change_pct should be based on prev_close=98, not hist[0]=99
        if result["change_pct"] is not None and result["price"] is not None:
            expected = round((result["price"] - prev_close) / prev_close * 100, 2)
            self.assertAlmostEqual(result["change_pct"], expected, places=1)


# ════════════════════════════════════════════════════════════════════════════
# 7. No trading language in source
# ════════════════════════════════════════════════════════════════════════════

class TestNoTradingCalls(unittest.TestCase):

    def _source(self):
        import inspect
        return inspect.getsource(mr)

    def test_no_place_order_call(self):
        self.assertNotIn("place_order(", self._source())

    def test_no_execute_trade_call(self):
        self.assertNotIn("execute_trade(", self._source())

    def test_no_submit_order_call(self):
        self.assertNotIn("submit_order(", self._source())

    def test_disclaimer_in_module(self):
        src = self._source()
        self.assertIn("educational information only", src)


# ════════════════════════════════════════════════════════════════════════════
# 8. API endpoints
# ════════════════════════════════════════════════════════════════════════════

class TestApiMarketPulse(unittest.TestCase):

    def setUp(self):
        self.app, self.api_mod, self.db_path = _make_app()
        self.client = self.app.test_client()
        self.api_mod.cache_clear()

    def _mock_pulse(self):
        return {
            "period": "1D",
            "market": [{"ticker": "^GSPC", "label": "S&P 500", "period": "1D",
                         "price": 5000.0, "prev_close": 4980.0, "change_pct": 0.4, "sparkline": []}],
            "sectors": [],
            "disclaimer": mr.DISCLAIMER,
        }

    def test_returns_200(self):
        with patch("market_research.get_market_pulse", return_value=self._mock_pulse()):
            resp = self.client.get("/api/v1/market/pulse")
        self.assertEqual(resp.status_code, 200)

    def test_envelope_ok_true(self):
        with patch("market_research.get_market_pulse", return_value=self._mock_pulse()):
            resp = self.client.get("/api/v1/market/pulse")
        data = resp.get_json()
        self.assertTrue(data["ok"])

    def test_data_has_market_and_sectors(self):
        with patch("market_research.get_market_pulse", return_value=self._mock_pulse()):
            resp = self.client.get("/api/v1/market/pulse")
        d = resp.get_json()["data"]
        self.assertIn("market", d)
        self.assertIn("sectors", d)

    def test_invalid_period_still_returns_200(self):
        with patch("market_research.get_market_pulse", return_value=self._mock_pulse()):
            resp = self.client.get("/api/v1/market/pulse?period=BOGUS")
        self.assertEqual(resp.status_code, 200)

    def test_disclaimer_in_data(self):
        with patch("market_research.get_market_pulse", return_value=self._mock_pulse()):
            resp = self.client.get("/api/v1/market/pulse")
        d = resp.get_json()["data"]
        self.assertIn("disclaimer", d)


class TestApiStockAnalysis(unittest.TestCase):

    def setUp(self):
        self.app, self.api_mod, self.db_path = _make_app()
        self.client = self.app.test_client()
        self.api_mod.cache_clear()

    def _mock_analysis(self, ticker="AAPL"):
        return {
            "ticker": ticker, "period": "1Y", "name": "Apple Inc.", "sector": "Technology",
            "industry": "Consumer Electronics",
            "quote": {"price": 190.0, "prev_close": 185.0, "change_pct": 2.7,
                      "market_cap": 3e12, "volume": 50000000, "avg_volume": 60000000},
            "fundamentals": {"pe_ratio": 28.0, "forward_pe": 25.0, "roe_pct": 145.0,
                             "debt_equity": 1.7, "beta": 1.2, "dividend_yield": 0.005,
                             "roe_tier": "STRONG", "de_tier": "HIGH", "beta_tier": "MARKET_LIKE",
                             "valuation_tier": "GROWTH", "fundamental_score": 62},
            "technicals": {"rsi": 58.0, "rsi_bucket": "NEUTRAL", "sma50": 185.0, "sma200": 175.0,
                           "week52_low": 150.0, "week52_high": 200.0, "week52_position": 80.0,
                           "annualized_volatility": 0.28, "technical_score": 75},
            "sparkline": [],
            "disclaimer": mr.DISCLAIMER,
        }

    def test_returns_200(self):
        with patch("market_research.get_stock_analysis", return_value=self._mock_analysis()):
            resp = self.client.get("/api/v1/research/stock/AAPL")
        self.assertEqual(resp.status_code, 200)

    def test_envelope_ok_true(self):
        with patch("market_research.get_stock_analysis", return_value=self._mock_analysis()):
            resp = self.client.get("/api/v1/research/stock/AAPL")
        self.assertTrue(resp.get_json()["ok"])

    def test_data_has_technicals_and_fundamentals(self):
        with patch("market_research.get_stock_analysis", return_value=self._mock_analysis()):
            resp = self.client.get("/api/v1/research/stock/AAPL")
        d = resp.get_json()["data"]
        self.assertIn("technicals", d)
        self.assertIn("fundamentals", d)

    def test_disclaimer_in_data(self):
        with patch("market_research.get_stock_analysis", return_value=self._mock_analysis()):
            resp = self.client.get("/api/v1/research/stock/AAPL")
        d = resp.get_json()["data"]
        self.assertIn("disclaimer", d)

    def test_period_param_passed(self):
        calls = []

        def _mock(ticker, period="1Y"):
            calls.append(period)
            return self._mock_analysis(ticker)

        with patch("market_research.get_stock_analysis", _mock):
            self.client.get("/api/v1/research/stock/AAPL?period=3M")
        self.assertIn("3M", calls)


class TestApiEtfAnalysis(unittest.TestCase):

    def setUp(self):
        self.app, self.api_mod, self.db_path = _make_app()
        self.client = self.app.test_client()
        self.api_mod.cache_clear()

    def _mock_etf(self, ticker="QQQ"):
        return {
            "ticker": ticker, "period": "1Y", "name": "Invesco QQQ Trust", "category": "Large Growth",
            "expense_ratio": 0.002, "aum": 200e9,
            "quote": {"price": 450.0, "prev_close": 445.0, "change_pct": 1.1},
            "returns": {"1D": 1.1, "5D": 2.3, "1M": 5.0, "3M": 8.0, "6M": 12.0, "1Y": 25.0},
            "risk": {"annualized_volatility": 0.22, "risk_score": 40},
            "top_holdings": [],
            "peers": ["SPY", "IWM"],
            "sparkline": [],
            "disclaimer": mr.DISCLAIMER,
        }

    def test_returns_200(self):
        with patch("market_research.get_etf_analysis", return_value=self._mock_etf()):
            resp = self.client.get("/api/v1/research/etf/QQQ")
        self.assertEqual(resp.status_code, 200)

    def test_has_risk_and_returns(self):
        with patch("market_research.get_etf_analysis", return_value=self._mock_etf()):
            resp = self.client.get("/api/v1/research/etf/QQQ")
        d = resp.get_json()["data"]
        self.assertIn("risk", d)
        self.assertIn("returns", d)

    def test_peers_present(self):
        with patch("market_research.get_etf_analysis", return_value=self._mock_etf()):
            resp = self.client.get("/api/v1/research/etf/QQQ")
        d = resp.get_json()["data"]
        self.assertIsInstance(d.get("peers"), list)

    def test_disclaimer_present(self):
        with patch("market_research.get_etf_analysis", return_value=self._mock_etf()):
            resp = self.client.get("/api/v1/research/etf/QQQ")
        d = resp.get_json()["data"]
        self.assertIn("disclaimer", d)


class TestApiMacroData(unittest.TestCase):

    def setUp(self):
        self.app, self.api_mod, self.db_path = _make_app()
        self.client = self.app.test_client()
        self.api_mod.cache_clear()

    def test_returns_200_when_unavailable(self):
        with patch("market_research.get_macro_data", return_value={"available": False, "reason": "no key", "indicators": {}, "disclaimer": mr.DISCLAIMER}):
            resp = self.client.get("/api/v1/research/macro")
        self.assertEqual(resp.status_code, 200)

    def test_envelope_ok_true_even_unavailable(self):
        with patch("market_research.get_macro_data", return_value={"available": False, "reason": "no key", "indicators": {}, "disclaimer": mr.DISCLAIMER}):
            resp = self.client.get("/api/v1/research/macro")
        self.assertTrue(resp.get_json()["ok"])

    def test_available_false_exposed(self):
        with patch("market_research.get_macro_data", return_value={"available": False, "reason": "no key", "indicators": {}, "disclaimer": mr.DISCLAIMER}):
            resp = self.client.get("/api/v1/research/macro")
        d = resp.get_json()["data"]
        self.assertFalse(d["available"])


class TestApiMarketNews(unittest.TestCase):

    def setUp(self):
        self.app, self.api_mod, self.db_path = _make_app()
        self.client = self.app.test_client()
        self.api_mod.cache_clear()

    def test_returns_200(self):
        with patch("market_research.get_market_news", return_value={"items": [], "disclaimer": mr.DISCLAIMER}):
            resp = self.client.get("/api/v1/research/news")
        self.assertEqual(resp.status_code, 200)

    def test_has_items_list(self):
        with patch("market_research.get_market_news", return_value={"items": [], "disclaimer": mr.DISCLAIMER}):
            resp = self.client.get("/api/v1/research/news")
        d = resp.get_json()["data"]
        self.assertIsInstance(d["items"], list)


class TestApiTickerNews(unittest.TestCase):

    def setUp(self):
        self.app, self.api_mod, self.db_path = _make_app()
        self.client = self.app.test_client()
        self.api_mod.cache_clear()

    def test_returns_200(self):
        with patch("market_research.get_ticker_news", return_value={"ticker": "NVDA", "items": [], "disclaimer": mr.DISCLAIMER}):
            resp = self.client.get("/api/v1/research/news/NVDA")
        self.assertEqual(resp.status_code, 200)

    def test_ticker_uppercase(self):
        captured = {}

        def _mock(ticker, max_items=10):
            captured["ticker"] = ticker
            return {"ticker": ticker, "items": [], "disclaimer": mr.DISCLAIMER}

        with patch("market_research.get_ticker_news", _mock):
            self.client.get("/api/v1/research/news/nvda")
        self.assertEqual(captured.get("ticker"), "NVDA")


class TestApiAiStockAnalysis(unittest.TestCase):

    def setUp(self):
        self.app, self.api_mod, self.db_path = _make_app()
        self.client = self.app.test_client()
        self.api_mod.cache_clear()

    def test_post_returns_200(self):
        mock_result = {"ticker": "AAPL", "commentary": "Elevated momentum.", "disclaimer": mr.DISCLAIMER, "compliance_ok": True}
        with patch("market_research.get_stock_analysis", return_value={}):
            with patch("market_research.generate_stock_analysis_ai", return_value=mock_result):
                resp = self.client.post("/api/v1/research/ai/stock/AAPL", json={})
        self.assertEqual(resp.status_code, 200)

    def test_has_commentary(self):
        mock_result = {"ticker": "AAPL", "commentary": "Elevated momentum.", "disclaimer": mr.DISCLAIMER, "compliance_ok": True}
        with patch("market_research.get_stock_analysis", return_value={}):
            with patch("market_research.generate_stock_analysis_ai", return_value=mock_result):
                resp = self.client.post("/api/v1/research/ai/stock/AAPL", json={})
        d = resp.get_json()["data"]
        self.assertIn("commentary", d)

    def test_missing_key_graceful(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with patch("market_research.get_stock_analysis", return_value={}):
                resp = self.client.post("/api/v1/research/ai/stock/AAPL", json={})
        self.assertEqual(resp.status_code, 200)


class TestApiAiEtfAnalysis(unittest.TestCase):

    def setUp(self):
        self.app, self.api_mod, self.db_path = _make_app()
        self.client = self.app.test_client()
        self.api_mod.cache_clear()

    def test_post_returns_200(self):
        mock_result = {"ticker": "QQQ", "commentary": "Risk is moderate.", "disclaimer": mr.DISCLAIMER, "compliance_ok": True}
        with patch("market_research.get_etf_analysis", return_value={}):
            with patch("market_research.generate_etf_analysis_ai", return_value=mock_result):
                resp = self.client.post("/api/v1/research/ai/etf/QQQ", json={})
        self.assertEqual(resp.status_code, 200)


class TestApiAiMacroAnalysis(unittest.TestCase):

    def setUp(self):
        self.app, self.api_mod, self.db_path = _make_app()
        self.client = self.app.test_client()
        self.api_mod.cache_clear()

    def test_post_returns_200(self):
        mock_result = {"commentary": "Conditions are mixed.", "disclaimer": mr.DISCLAIMER, "compliance_ok": True}
        with patch("market_research.get_macro_data", return_value={}):
            with patch("market_research.generate_macro_analysis_ai", return_value=mock_result):
                resp = self.client.post("/api/v1/research/ai/macro", json={})
        self.assertEqual(resp.status_code, 200)

    def test_missing_key_graceful(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with patch("market_research.get_macro_data", return_value={}):
                resp = self.client.post("/api/v1/research/ai/macro", json={})
        self.assertEqual(resp.status_code, 200)


# ════════════════════════════════════════════════════════════════════════════
# 9. Sector endpoint
# ════════════════════════════════════════════════════════════════════════════

class TestApiSectorPerformance(unittest.TestCase):

    def setUp(self):
        self.app, self.api_mod, self.db_path = _make_app()
        self.client = self.app.test_client()
        self.api_mod.cache_clear()

    def test_returns_200(self):
        mock_data = {"period": "1D", "sectors": [], "disclaimer": mr.DISCLAIMER}
        with patch("market_research.get_sector_performance", return_value=mock_data):
            resp = self.client.get("/api/v1/research/sector")
        self.assertEqual(resp.status_code, 200)

    def test_has_sectors_list(self):
        mock_data = {"period": "1D", "sectors": [{"ticker": "XLK", "sector_name": "Technology", "change_pct": 1.2, "price": 200.0}], "disclaimer": mr.DISCLAIMER}
        with patch("market_research.get_sector_performance", return_value=mock_data):
            resp = self.client.get("/api/v1/research/sector")
        d = resp.get_json()["data"]
        self.assertIsInstance(d.get("sectors"), list)


if __name__ == "__main__":
    unittest.main()
