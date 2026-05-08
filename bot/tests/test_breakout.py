"""
Unit tests for ma200_recent_cross — the redesigned breakout indicator.

All tests use synthetic pandas Series; no network calls.

Series construction convention used throughout:
  [HIGH] * H + [LOW] * L + [BREAK] * B
  MA200 at the cross point ≈ (HIGH*H + LOW*L) / 200 when H+L == 200.
  The cross is detected when price moves from LOW (< MA200) to BREAK (> MA200).
"""
import pandas as pd
import numpy as np
import pytest

from market_data import ma200_recent_cross


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
