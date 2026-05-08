"""
Unit tests for:
  - ma200_recent_cross  (market_data.py) — redesigned breakout indicator
  - _score_breakout     (predator.py)    — 52-week-high proximity guard

All tests use synthetic pandas Series; yfinance calls are mocked.

Series construction convention used throughout:
  [HIGH] * H + [LOW] * L + [BREAK] * B
  MA200 at the cross point ≈ (HIGH*H + LOW*L) / 200 when H+L == 200.
  The cross is detected when price moves from LOW (< MA200) to BREAK (> MA200).
"""
import pandas as pd
import numpy as np
import pytest
from unittest.mock import patch

from market_data import ma200_recent_cross
from predator import _score_breakout


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _s(values) -> pd.Series:
    return pd.Series(values, dtype=float)


def _step(high_val, high_n, low_val, low_n, break_val, break_n) -> pd.Series:
    """Build a step-function price series: high section → low section → breakout."""
    return _s([high_val] * high_n + [low_val] * low_n + [break_val] * break_n)


# ─────────────────────────────────────────────
# Insufficient data
# ─────────────────────────────────────────────

class TestInsufficientData:
    def test_empty_series_returns_false(self):
        crossed, days = ma200_recent_cross(_s([]))
        assert crossed is False
        assert days == 0

    def test_exactly_200_bars_returns_false(self):
        # Need at least 201 bars for a single valid MA200 value
        crossed, days = ma200_recent_cross(_s([100.0] * 200))
        assert crossed is False

    def test_201_bars_does_not_crash(self):
        # Minimum valid input: should run without error
        crossed, days = ma200_recent_cross(_s([100.0] * 201))
        assert isinstance(crossed, bool)
        assert isinstance(days, int)


# ─────────────────────────────────────────────
# The false-positive case this redesign fixes
# ─────────────────────────────────────────────

class TestPermanentStateAbove:
    def test_price_equal_to_ma200_throughout_no_cross(self):
        # 220 bars all at 100 → MA200 == price throughout → never below → no cross
        crossed, days = ma200_recent_cross(_s([100.0] * 220))
        assert crossed is False

    def test_price_above_ma200_for_entire_lookback_no_cross(self):
        # First 50 bars at 100, next 170 bars at 110.
        # MA200 ≈ 107.5 throughout lookback window; price 110 > MA but was never below.
        closes = _s([100.0] * 50 + [110.0] * 170)
        crossed, days = ma200_recent_cross(closes)
        assert crossed is False

    def test_sustained_bull_run_no_cross(self):
        # Classic false positive: stock that's been above MA200 for a full year.
        # Price linearly rises from 100 to 200 over 220 bars; always above rolling MA200.
        prices = np.linspace(100, 200, 220)
        crossed, days = ma200_recent_cross(_s(prices))
        assert crossed is False


# ─────────────────────────────────────────────
# Valid recent cross detection
# ─────────────────────────────────────────────

class TestRecentCrossDetected:
    def test_cross_19_days_ago_detected(self):
        # 220 bars: [130]*100 + [70]*100 + [120]*20
        # MA200 at cross ≈ (130*100 + 70*100)/200 = 100
        # Bar 199 = 70 < 100 (below), bar 200 = 120 > 100 (above) → cross at i=1 in window
        # days_ago = (lookback+1 - 1) - 1 = lookback - 1 = 19
        closes = _step(130, 100, 70, 100, 120, 20)
        crossed, days = ma200_recent_cross(closes, lookback=20)
        assert crossed is True
        assert days == 19

    def test_cross_today_days_ago_zero(self):
        # 220 bars: [130]*100 + [70]*119 + [120]*1
        # The single bar at 120 is the most recent bar (today)
        closes = _step(130, 100, 70, 119, 120, 1)
        crossed, days = ma200_recent_cross(closes, lookback=20)
        assert crossed is True
        assert days == 0

    def test_cross_10_days_ago(self):
        # 220 bars: [130]*100 + [70]*110 + [120]*10
        # Cross happened 10 bars of 120 ago → days_ago = lookback - 1 - (n-1-10) ... let me verify:
        # n = lookback+1 = 21
        # prices_w[-21:] = [70]*10 + [120]*10... wait, 100+110+10=220
        # prices_w = last 21 = bars 199..219 = [70]*11 + [120]*10
        # Cross at i=11 (bar 199+11=210, first 120 bar):
        #   days_ago = (21-1) - 11 = 9
        # Hmm, that's 9 not 10. Let me recount.
        # Total = 100+110+10 = 220. Bars 0..219.
        # 120 section starts at bar 210. Last bar is 219.
        # prices_w (n=21): bars 199..219.
        # prices_w.iloc[0] = bar 199 = 70
        # prices_w.iloc[10] = bar 209 = 70
        # prices_w.iloc[11] = bar 210 = 120  ← first 120
        # i=11 (checking iloc[10]→iloc[11]):
        #   p_prev = 70, m_prev = MA200 at bar 209 ≈ (130*100+70*110)/220... no, 200-bar window
        #   MA200 at bar 209 = mean(bars 10..209) = mean([130]*90 + [70]*110) = (11700+7700)/200 = 97
        #   p_prev = 70 < 97 → below ✓
        #   p_curr = 120 > MA200 at bar 210 → above ✓
        #   days_ago = (20) - 11 = 9
        closes = _step(130, 100, 70, 110, 120, 10)
        crossed, days = ma200_recent_cross(closes, lookback=20)
        assert crossed is True
        # Cross is at the bar that is 9 days before the end of the window
        assert days == 9

    def test_cross_detected_with_smaller_lookback(self):
        # Cross happened 3 days ago; use lookback=5 to verify parameter respected
        # 220 bars: [130]*100 + [70]*116 + [120]*4
        closes = _step(130, 100, 70, 116, 120, 4)
        crossed, days = ma200_recent_cross(closes, lookback=5)
        assert crossed is True
        assert days == 3


# ─────────────────────────────────────────────
# No cross scenarios
# ─────────────────────────────────────────────

class TestNoCross:
    def test_permanently_below_no_cross(self):
        # 220 bars: starts high, drops, stays below MA200 throughout lookback
        # [150]*100 + [70]*120 → lookback window is all 70, all below MA200
        closes = _step(150, 100, 70, 120, 70, 0)  # break_n=0 → just 220 bars of low
        # Actually _step with break_n=0 gives 100+120=220 bars
        # Rewrite without _step helper:
        closes = _s([150.0] * 100 + [70.0] * 120)
        crossed, days = ma200_recent_cross(closes, lookback=20)
        assert crossed is False

    def test_cross_outside_lookback_not_detected(self):
        # 240 bars: [130]*100 + [70]*100 + [120]*40
        # Cross happened at bar 200 (40 bars ago); with lookback=20 it's outside the window
        closes = _step(130, 100, 70, 100, 120, 40)
        crossed, days = ma200_recent_cross(closes, lookback=20)
        assert crossed is False

    def test_cross_just_outside_lookback_boundary(self):
        # Cross at exactly lookback+1 bars ago should NOT be detected
        # lookback=10, so cross must be <= 10 bars ago to count
        # Put cross at bar 11 ago: [130]*100 + [70]*109 + [120]*11
        closes = _step(130, 100, 70, 109, 120, 11)
        crossed, days = ma200_recent_cross(closes, lookback=10)
        # Cross at i=1 in 12-bar window → days_ago = 10. But lookback=10 → n=11.
        # prices_w = last 11 bars = [70]*0 + [120]*11... wait.
        # 100+109+11 = 220 bars. 120 section: bars 209..219 (11 bars).
        # prices_w (n=11): bars 209..219 = all 120.
        # No below→above in this all-120 window → crossed=False ✓
        assert crossed is False

    def test_cross_at_lookback_boundary_detected(self):
        # Cross at exactly lookback bars ago SHOULD be detected (the extra bar catches it)
        # lookback=10, cross happened at bar 10 ago: [130]*100 + [70]*110 + [120]*10
        closes = _step(130, 100, 70, 110, 120, 10)
        crossed, days = ma200_recent_cross(closes, lookback=10)
        # n=11, prices_w = last 11 bars = [70]*1 + [120]*10
        # i=1: p_prev=70, below MA; p_curr=120, above MA → cross!
        # days_ago = (11-1) - 1 = 9 (within lookback=10) ✓
        assert crossed is True
        assert days == 9


# ─────────────────────────────────────────────
# Most-recent cross is reported
# ─────────────────────────────────────────────

class TestMostRecentCross:
    def test_returns_most_recent_when_multiple_crossings(self):
        # Oscillation: cross up 15 days ago, then back below, then cross up 5 days ago
        # Build: [130]*100 + low section + oscillation within last 20 bars
        # Base: 200 bars to establish history, then:
        #   bars 200..204: 70 (below MA200 ~100)
        #   bar  205:      120 (first cross, 14 days ago from bar 219)
        #   bars 206..209: 70 (back below)
        #   bar  210:      120 (second cross, 9 days ago)
        #   bars 211..219: 120 (stays above)
        base = [130.0] * 100 + [70.0] * 100  # 200 bars, MA200 at cross ≈ 100
        oscillation = [70.0]*5 + [120.0]*1 + [70.0]*4 + [120.0]*10  # 20 bars
        closes = _s(base + oscillation)
        crossed, days = ma200_recent_cross(closes, lookback=20)
        assert crossed is True
        # Most recent cross was at bar 210, which is 9 bars before bar 219
        assert days == 9


# ─────────────────────────────────────────────
# Regression tests
# ─────────────────────────────────────────────

class TestRegressions:
    def test_cross_then_fall_back_below_returns_false(self):
        # Cross occurred within lookback but price has since fallen back below MA200.
        # "Still above" guard must invalidate the cross.
        # [130]*100 + [70]*100 + [120]*10 + [70]*10 = 220 bars
        # Cross at bar 200; last 10 bars back at 70 (below MA200 ≈ 100).
        base = [130.0] * 100 + [70.0] * 100
        breakout = [120.0] * 10
        reversal = [70.0] * 10
        closes = _s(base + breakout + reversal)
        crossed, days = ma200_recent_cross(closes, lookback=20)
        assert crossed is False

    def test_price_equal_to_ma200_not_a_breakout(self):
        # Price touches MA200 exactly from below — strict > means this is NOT a breakout.
        # Construct: [130]*100 + [70]*100 + [x] where x == MA200 at that bar.
        # MA200 at bar 200 = mean(bars 1..200) = (99*130 + 100*70 + x) / 200
        # Solving x = (19870 + x)/200 → x = 19870/199 ≈ 99.85
        equal_val = 19870.0 / 199.0  # price that exactly equals MA200 at that bar
        closes = _s([130.0] * 100 + [70.0] * 100 + [equal_val])
        crossed, days = ma200_recent_cross(closes, lookback=20)
        # Caught by both the "still above" guard (equal → <=) and strict crossover (> not >=)
        assert crossed is False
        assert days == 0

    def test_lookback_larger_than_dataset_does_not_crash(self):
        # 210 bars total, lookback=500 → n clamped to min(501, 210) = 210.
        # Should return a valid result without IndexError.
        closes = _s([130.0] * 100 + [70.0] * 100 + [120.0] * 10)
        crossed, days = ma200_recent_cross(closes, lookback=500)
        assert isinstance(crossed, bool)
        assert isinstance(days, int)
        # Cross at bar 200 is within the clamped window, last close 120 > MA200 ≈ 100
        assert crossed is True


# ─────────────────────────────────────────────
# _score_breakout — 52-week-high proximity guard
# ─────────────────────────────────────────────

def _make_data(price: float, ma200: float = None, vol_ratio: float = 1.0,
               pct_1d: float = 0.0) -> dict:
    """Minimal data dict for _score_breakout; closes series is all-same (no MA200 cross)."""
    return {
        "price":     price,
        "ma200":     ma200 if ma200 is not None else price * 0.9,
        "vol_ratio": vol_ratio,
        "pct_1d":    pct_1d,
        # Flat series → price always == MA200-of-flat → no recent cross fires
        "closes":    _s([price] * 220),
    }


class TestScoreBreakout52WkHigh:
    def test_stale_52wk_high_below_current_price_no_score(self):
        # fiftyTwoWeekHigh is stale and below current price.
        # Old code: (140 - 150) / 140 * 100 = -7.1 ≤ 3 → would score. Fixed: must not score.
        data = _make_data(price=150.0)
        mock_info = {"fiftyTwoWeekHigh": 140.0}  # stale: 52wk high < current price

        with patch("predator.yf.Ticker") as mock_cls:
            mock_cls.return_value.info = mock_info
            score, reason = _score_breakout("FAKE", data)

        assert score == 0
        assert "52wk" not in reason.lower()

    def test_price_within_3pct_of_52wk_high_scores(self):
        # Valid case: current price is 1.5% below the 52-week high.
        data = _make_data(price=98.5)
        mock_info = {"fiftyTwoWeekHigh": 100.0}  # pct_from_high = 1.5%

        with patch("predator.yf.Ticker") as mock_cls:
            mock_cls.return_value.info = mock_info
            score, reason = _score_breakout("FAKE", data)

        assert score >= 1
        assert "52wk" in reason.lower()

    def test_price_exactly_at_52wk_high_scores(self):
        # current == high_52w → pct_from_high = 0, clamped to 0 → within 3% → scores.
        data = _make_data(price=100.0)
        mock_info = {"fiftyTwoWeekHigh": 100.0}

        with patch("predator.yf.Ticker") as mock_cls:
            mock_cls.return_value.info = mock_info
            score, reason = _score_breakout("FAKE", data)

        assert score >= 1
        assert "52wk" in reason.lower()

    def test_price_well_below_52wk_high_no_proximity_score(self):
        # Current price is 20% below the 52-week high → outside 3% band.
        data = _make_data(price=80.0)
        mock_info = {"fiftyTwoWeekHigh": 100.0}  # pct_from_high = 20%

        with patch("predator.yf.Ticker") as mock_cls:
            mock_cls.return_value.info = mock_info
            score, reason = _score_breakout("FAKE", data)

        assert score == 0
