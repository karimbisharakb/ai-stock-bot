"""
Isolated unit tests for the Phase 1A options-signal redesign.

Tests cover:
  - Earnings proximity cap  (earnings ≤ 5d → max score = 1)
  - OI delta confirmation   (score >= 3 requires call OI >= 2× put OI)
  - Strike concentration    (+1 boost when top-3 strikes >= 70% of volume)
  - data_quality assignment (HIGH / MEDIUM / LOW)
  - Existing behaviour      (ratio tiers, Canadian bypass, no-data path)

All yfinance network calls are mocked — no internet required.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from predator import SignalResult, _score_options


# ─────────────────────────────────────────────
# Mock builders
# ─────────────────────────────────────────────

def _calls_df(rows: list[dict]) -> pd.DataFrame:
    """Build a calls DataFrame from a list of {strike, volume, openInterest} dicts."""
    return pd.DataFrame(rows, columns=["strike", "volume", "openInterest"])


def _puts_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["strike", "volume", "openInterest"])


def _make_chain(calls: list[dict], puts: list[dict]):
    chain = MagicMock()
    chain.calls = _calls_df(calls)
    chain.puts  = _puts_df(puts)
    return chain


def _make_ticker(
    expiries: list[str],
    chains: list,                 # parallel list of chain mocks
    info_price: float = 100.0,
    calendar=None,                # None = no earnings data
):
    """Build a yf.Ticker mock wired up for _score_options."""
    mock = MagicMock()
    mock.options = expiries
    mock.info = {
        "regularMarketPrice": info_price,
        "currentPrice":       info_price,
    }

    if calendar is not None:
        mock.calendar = calendar
    else:
        # Simulate t.calendar returning a DataFrame with no earnings
        empty_cal = MagicMock()
        empty_cal.empty = True
        mock.calendar = empty_cal

    chain_map = dict(zip(expiries, chains))
    mock.option_chain.side_effect = lambda exp: chain_map[exp]
    return mock


def _earnings_calendar(days_from_today: int) -> pd.DataFrame:
    """Return a t.calendar-shaped DataFrame with earnings N days from today."""
    earnings_date = date.today() + timedelta(days=days_from_today)
    return pd.DataFrame(
        {0: [earnings_date]},
        index=["Earnings Date"],
    )


def _future_expiry(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


# ─────────────────────────────────────────────
# Helpers — canned chain data
# ─────────────────────────────────────────────

def _high_ratio_chain(call_vol=3000, put_vol=500, call_oi=1000, put_oi=800):
    """Chain producing call/put vol ratio >= 3 (normally scores 3)."""
    calls = [{"strike": 110.0, "volume": call_vol, "openInterest": call_oi}]
    puts  = [{"strike":  90.0, "volume": put_vol,  "openInterest": put_oi}]
    return _make_chain(calls, puts)


def _moderate_ratio_chain(call_vol=2000, put_vol=900, call_oi=600, put_oi=900):
    """Chain producing call/put ratio >= 2 but < 3 (normally scores 1)."""
    calls = [{"strike": 105.0, "volume": call_vol, "openInterest": call_oi}]
    puts  = [{"strike":  95.0, "volume": put_vol,  "openInterest": put_oi}]
    return _make_chain(calls, puts)


# ─────────────────────────────────────────────
# Canadian ticker bypass
# ─────────────────────────────────────────────

class TestCanadianBypass:
    def test_canadian_ticker_returns_zero_low(self):
        result = _score_options("VFV.TO")
        assert result == SignalResult(0, "", "LOW")

    def test_mda_returns_zero_low(self):
        result = _score_options("MDA.TO")
        assert result == SignalResult(0, "", "LOW")


# ─────────────────────────────────────────────
# Return type
# ─────────────────────────────────────────────

class TestReturnType:
    def test_returns_signal_result_instance(self):
        with patch("predator.yf.Ticker") as mock_cls:
            mock_cls.return_value.options = []
            result = _score_options("NVDA")
        assert isinstance(result, SignalResult)

    def test_signal_result_unpacks_to_three_fields(self):
        score, reason, quality = SignalResult(2, "test", "MEDIUM")
        assert score == 2
        assert reason == "test"
        assert quality == "MEDIUM"


# ─────────────────────────────────────────────
# No-data paths
# ─────────────────────────────────────────────

class TestNoData:
    def test_no_expiries_returns_low(self):
        with patch("predator.yf.Ticker") as mock_cls:
            mock_cls.return_value.options = []
            result = _score_options("NVDA")
        assert result.score == 0
        assert result.data_quality == "LOW"

    def test_no_near_expiries_returns_low(self):
        # All expiries are >30 days out
        far_exp = _future_expiry(45)
        with patch("predator.yf.Ticker") as mock_cls:
            mock_cls.return_value.options = [far_exp]
            mock_cls.return_value.info = {}
            result = _score_options("NVDA")
        assert result.score == 0
        assert result.data_quality == "LOW"


# ─────────────────────────────────────────────
# Earnings proximity cap
# ─────────────────────────────────────────────

class TestEarningsProximityCap:
    def _ticker_with_earnings(self, days: int, chain):
        exp = _future_expiry(14)
        mock = _make_ticker(
            expiries=[exp],
            chains=[chain],
            calendar=_earnings_calendar(days),
        )
        return mock, exp

    def test_earnings_3d_away_caps_high_signal_at_1(self):
        # Normally this chain scores 3 (ratio >= 3, call OI >= 2× put OI)
        chain = _high_ratio_chain(call_vol=3000, put_vol=500, call_oi=2000, put_oi=800)
        mock, _ = self._ticker_with_earnings(days=3, chain=chain)

        with patch("predator.yf.Ticker", return_value=mock):
            result = _score_options("NVDA")

        assert result.score == 1
        assert result.data_quality == "LOW"
        assert "earnings" in result.reason.lower() or "≤5d" in result.reason

    def test_earnings_5d_away_is_still_capped(self):
        chain = _high_ratio_chain(call_vol=3000, put_vol=500, call_oi=2000, put_oi=800)
        mock, _ = self._ticker_with_earnings(days=5, chain=chain)

        with patch("predator.yf.Ticker", return_value=mock):
            result = _score_options("NVDA")

        assert result.score == 1
        assert result.data_quality == "LOW"

    def test_earnings_6d_away_is_not_capped(self):
        # 6 days is outside the 5-day window — no earnings cap applied
        chain = _high_ratio_chain(call_vol=3000, put_vol=500, call_oi=2000, put_oi=800)
        mock, _ = self._ticker_with_earnings(days=6, chain=chain)

        with patch("predator.yf.Ticker", return_value=mock):
            result = _score_options("NVDA")

        assert result.score >= 2  # not capped at 1

    def test_earnings_0d_away_still_capped_at_1(self):
        # Earnings today → still within the 5-day window
        chain = _high_ratio_chain(call_vol=3000, put_vol=500, call_oi=2000, put_oi=800)
        mock, _ = self._ticker_with_earnings(days=0, chain=chain)

        with patch("predator.yf.Ticker", return_value=mock):
            result = _score_options("NVDA")

        assert result.score == 1
        assert result.data_quality == "LOW"


# ─────────────────────────────────────────────
# OI delta confirmation
# ─────────────────────────────────────────────

class TestOIDeltaConfirmation:
    def _simple_ticker(self, chain):
        exp = _future_expiry(14)
        mock = _make_ticker(expiries=[exp], chains=[chain])
        return mock

    def test_score_3_without_oi_confirmation_capped_at_2(self):
        # ratio >= 3 → raw score 3; but call OI < 2× put OI → no confirmation → cap at 2
        chain = _high_ratio_chain(call_vol=3000, put_vol=500, call_oi=800, put_oi=900)
        mock = self._simple_ticker(chain)

        with patch("predator.yf.Ticker", return_value=mock):
            result = _score_options("NVDA")

        assert result.score == 2
        assert result.data_quality == "MEDIUM"
        assert "unconfirmed" in result.reason.lower()

    def test_score_3_with_oi_confirmation_stays_at_3(self):
        # ratio >= 3 and call OI >= 2× put OI → confirmed → score stays 3
        chain = _high_ratio_chain(call_vol=3000, put_vol=500, call_oi=2000, put_oi=800)
        mock = self._simple_ticker(chain)

        with patch("predator.yf.Ticker", return_value=mock):
            result = _score_options("NVDA")

        assert result.score == 3
        assert result.data_quality == "HIGH"
        assert "unconfirmed" not in result.reason.lower()

    def test_score_1_not_affected_by_oi_cap(self):
        # OI cap only applies when score >= 3; score = 1 is unaffected.
        # Use dispersed call volume (many strikes) so concentration boost does NOT fire.
        calls = [
            {"strike": 100.0, "volume": 500,  "openInterest": 100},
            {"strike": 102.0, "volume": 450,  "openInterest": 90},
            {"strike": 104.0, "volume": 400,  "openInterest": 80},
            {"strike": 106.0, "volume": 350,  "openInterest": 70},
            {"strike": 108.0, "volume": 300,  "openInterest": 60},
        ]
        puts = [{"strike": 95.0, "volume": 900, "openInterest": 900}]
        chain = _make_chain(calls, puts)
        mock = self._simple_ticker(chain)

        with patch("predator.yf.Ticker", return_value=mock):
            result = _score_options("NVDA")

        assert result.score == 1
        assert result.data_quality == "MEDIUM"

    def test_data_quality_high_requires_oi_confirmation(self):
        chain = _high_ratio_chain(call_vol=3000, put_vol=500, call_oi=2000, put_oi=800)
        mock = self._simple_ticker(chain)

        with patch("predator.yf.Ticker", return_value=mock):
            result = _score_options("NVDA")

        assert result.data_quality == "HIGH"

    def test_data_quality_medium_when_no_oi_confirmation(self):
        chain = _moderate_ratio_chain(call_vol=2000, put_vol=900, call_oi=300, put_oi=900)
        mock = self._simple_ticker(chain)

        with patch("predator.yf.Ticker", return_value=mock):
            result = _score_options("NVDA")

        assert result.data_quality == "MEDIUM"


# ─────────────────────────────────────────────
# Strike concentration boost
# ─────────────────────────────────────────────

class TestStrikeConcentrationBoost:
    def _ticker_with_calls(self, calls: list[dict], call_oi: float, put_oi: float):
        puts = [{"strike": 90.0, "volume": 500, "openInterest": put_oi}]
        chain = _make_chain(calls, puts)
        exp = _future_expiry(14)
        mock = _make_ticker(expiries=[exp], chains=[chain])
        return mock

    def test_concentrated_volume_boosts_score_from_1_to_2(self):
        # Elevated ratio (score 1), volume all in one strike → boost to 2
        # call OI < 2× put OI so no OI confirmation
        calls = [
            {"strike": 105.0, "volume": 1800, "openInterest": 400},  # dominant
            {"strike": 110.0, "volume":  100, "openInterest":  50},
            {"strike": 115.0, "volume":   50, "openInterest":  20},
            {"strike": 120.0, "volume":   50, "openInterest":  20},
        ]
        # total vol = 2000, top-3 = 1950 (97.5%) → concentrated
        # call_vol / put_vol = 2000/500 = 4.0... wait, put_vol in _moderate_ratio_chain
        # is 900. Let me build this directly.
        puts = [{"strike": 90.0, "volume": 900, "openInterest": 600}]
        chain = _make_chain(calls, puts)
        exp = _future_expiry(14)
        mock = _make_ticker(expiries=[exp], chains=[chain])

        with patch("predator.yf.Ticker", return_value=mock):
            result = _score_options("NVDA")

        # ratio = 2000/900 ≈ 2.2 → base score 1
        # concentration (top-3/total ≈ 97.5%) → +1 → score 2
        assert result.score == 2
        assert "concentration" in result.reason.lower() or "strike" in result.reason.lower()

    def test_dispersed_volume_no_boost(self):
        # Volume spread evenly across 10 strikes → no concentration boost
        calls = [
            {"strike": float(100 + i * 5), "volume": 200, "openInterest": 100}
            for i in range(10)
        ]
        puts = [{"strike": 90.0, "volume": 900, "openInterest": 600}]
        chain = _make_chain(calls, puts)
        exp = _future_expiry(14)
        mock = _make_ticker(expiries=[exp], chains=[chain])

        with patch("predator.yf.Ticker", return_value=mock):
            result = _score_options("NVDA")

        # total vol = 2000, put_vol = 900, ratio ≈ 2.2 → base score 1
        # top-3 vol = 600/2000 = 30% → not concentrated → no boost
        assert result.score == 1
        assert "concentration" not in result.reason.lower()

    def test_concentration_boost_cannot_exceed_3(self):
        # Raw score = 3 (ratio >= 3, OI confirmed) + concentration → capped at 3
        calls = [
            {"strike": 110.0, "volume": 3000, "openInterest": 2000},  # dominant
            {"strike": 115.0, "volume":  100, "openInterest":   50},
        ]
        puts = [{"strike": 90.0, "volume": 500, "openInterest": 800}]
        chain = _make_chain(calls, puts)
        exp = _future_expiry(14)
        mock = _make_ticker(expiries=[exp], chains=[chain])

        with patch("predator.yf.Ticker", return_value=mock):
            result = _score_options("NVDA")

        assert result.score <= 3

    def test_no_boost_when_base_score_zero(self):
        # Ratio < 2, no unusual — even with concentration, score stays 0
        calls = [
            {"strike": 105.0, "volume": 900, "openInterest": 300},  # concentrated
        ]
        puts = [{"strike": 90.0, "volume": 900, "openInterest": 300}]  # ratio = 1.0
        chain = _make_chain(calls, puts)
        exp = _future_expiry(14)
        mock = _make_ticker(expiries=[exp], chains=[chain])

        with patch("predator.yf.Ticker", return_value=mock):
            result = _score_options("NVDA")

        assert result.score == 0


# ─────────────────────────────────────────────
# Interaction: caps applied in correct order
# ─────────────────────────────────────────────

class TestCapInteractions:
    def test_earnings_cap_overrides_oi_confirmation(self):
        # Even with OI confirmed (quality would be HIGH), earnings cap forces LOW and score <= 1
        chain = _high_ratio_chain(call_vol=3000, put_vol=500, call_oi=2000, put_oi=800)
        exp = _future_expiry(14)
        mock = _make_ticker(
            expiries=[exp],
            chains=[chain],
            calendar=_earnings_calendar(2),
        )

        with patch("predator.yf.Ticker", return_value=mock):
            result = _score_options("NVDA")

        assert result.score <= 1
        assert result.data_quality == "LOW"

    def test_concentration_boost_then_oi_cap(self):
        # Base score 1 (ratio ≈ 2.2) + concentration boost → 2
        # OI cap only applies at >= 3, so score stays 2 with data_quality MEDIUM
        calls = [
            {"strike": 105.0, "volume": 1800, "openInterest": 300},
            {"strike": 110.0, "volume":   100, "openInterest":  30},
            {"strike": 115.0, "volume":   100, "openInterest":  30},
        ]
        puts = [{"strike": 90.0, "volume": 900, "openInterest": 600}]
        chain = _make_chain(calls, puts)
        exp = _future_expiry(14)
        mock = _make_ticker(expiries=[exp], chains=[chain])

        with patch("predator.yf.Ticker", return_value=mock):
            result = _score_options("NVDA")

        # call_oi = 360, put_oi = 600 → call_oi / put_oi < 2 → MEDIUM
        # score should be 2 (1 base + 1 concentration), not capped (only cap at >= 3)
        assert result.score == 2
        assert result.data_quality == "MEDIUM"
