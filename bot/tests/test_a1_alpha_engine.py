"""
Phase A1 — Alpha Engine tests.

No network calls. All inputs use synthetic AlphaInput objects.

Coverage:
  - Each of the 8 scoring components
  - Hard filters (liquidity, penny, dead ticker)
  - Tier classification and gate logic
  - Setup type classification
  - Explanation generation
  - Integration: strong vs mediocre vs stale setups
  - Determinism
  - Sparse / None field handling
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from alpha_engine import (
    AlphaEngine,
    AlphaInput,
    AlphaResult,
    ComponentScore,
    _apply_filters,
    _classify_tier,
    _classify_setup,
    _score_relative_strength,
    _score_acceleration,
    _score_squeeze,
    _score_catalyst,
    _score_options,
    _score_breakout,
    _score_risk_reward,
    _score_novelty,
    _WEIGHTS,
    _HOLDING_WINDOWS,
    _SETUP_TYPES,
)

ENGINE = AlphaEngine()


# ─────────────────────────────────────────────
# Builder helpers
# ─────────────────────────────────────────────

def _base() -> AlphaInput:
    """Minimal valid input — passes filters, all signals neutral/missing."""
    return AlphaInput(
        ticker="TEST",
        price=50.0,
        market_cap_millions=200.0,
        avg_volume_20d=200_000,   # 200K × $50 = $10M daily dollar volume
    )


def _strong_setup() -> AlphaInput:
    """Strong multi-signal setup expected to reach HIGH_CONVICTION or RARE_SETUP."""
    return AlphaInput(
        ticker="NVDA",
        price=60.0,
        price_5d_ago=56.0,         # +7.1% in 5d
        price_20d_ago=50.0,        # +20% in 20d
        price_60d_ago=35.0,        # +71% in 60d
        price_high_52w=100.0,      # 67% upside to 52w high
        price_low_52w=30.0,
        volume_today=5_000_000,
        avg_volume_20d=1_000_000,  # 5x volume surge
        avg_volume_5d=1_200_000,
        spy_return_5d=0.01,
        spy_return_20d=0.03,
        spy_return_60d=0.05,
        qqq_return_5d=0.015,
        qqq_return_20d=0.04,
        qqq_return_60d=0.06,
        rsi=65.0,
        macd=0.5,
        macd_signal=0.3,
        ma_50=55.0,
        ma_200=52.0,
        ma_200_days_since_cross=15,
        short_percent_float=0.28,
        days_to_cover=6.0,
        earnings_days=10,
        has_catalyst_news=True,
        news_sentiment=0.6,
        unusual_options=True,
        call_volume=200_000,
        put_volume=30_000,
        call_oi=500_000,
        put_oi=100_000,
        stop_price=55.0,           # ~8% stop
        market_cap_millions=500.0,
        last_alerted_hours_ago=None,
    )


def _mediocre_setup() -> AlphaInput:
    """Flat stock, underperforming, no catalyst, no squeeze, no options data."""
    return AlphaInput(
        ticker="FLAT",
        price=50.0,
        price_5d_ago=50.0,
        price_20d_ago=52.0,        # down 4% in 20d
        price_60d_ago=53.0,        # down 6% in 60d
        price_high_52w=80.0,
        volume_today=800_000,
        avg_volume_20d=1_000_000,  # below average
        spy_return_5d=0.02,
        spy_return_20d=0.05,
        qqq_return_5d=0.03,
        qqq_return_20d=0.06,
        market_cap_millions=200.0,
        last_alerted_hours_ago=None,
    )


def _options_only_setup() -> AlphaInput:
    """Max options signals, everything else missing/neutral."""
    return AlphaInput(
        ticker="OPT",
        price=50.0,
        market_cap_millions=500.0,
        avg_volume_20d=200_000,
        unusual_options=True,
        call_volume=500_000,
        put_volume=50_000,    # 10x call/put
        call_oi=1_000_000,
        put_oi=200_000,       # 5x OI
        last_alerted_hours_ago=None,
    )


# ─────────────────────────────────────────────
# Relative Strength scorer
# ─────────────────────────────────────────────

class TestRelativeStrength:
    def test_missing_price_history_returns_low_score(self):
        comp = _score_relative_strength(_base())
        assert comp.score <= 5.0
        assert comp.data_quality == "MISSING"

    def test_strong_outperformance_scores_high(self):
        inp = AlphaInput(
            ticker="X",
            price=100.0,
            price_5d_ago=92.0,   # +8.7%
            price_20d_ago=80.0,  # +25%
            price_60d_ago=65.0,  # +53.8%
            spy_return_5d=0.01,
            spy_return_20d=0.03,
            spy_return_60d=0.05,
        )
        comp = _score_relative_strength(inp)
        assert comp.score >= 7.0

    def test_underperformance_scores_low(self):
        inp = AlphaInput(
            ticker="X",
            price=95.0,
            price_5d_ago=100.0,   # -5%
            price_20d_ago=100.0,  # -5%
            price_60d_ago=100.0,
            spy_return_5d=0.03,
            spy_return_20d=0.05,
            spy_return_60d=0.08,
        )
        comp = _score_relative_strength(inp)
        assert comp.score < 3.0

    def test_all_three_timeframes_outperform_adds_bonus(self):
        inp = AlphaInput(
            ticker="X",
            price=100.0,
            price_5d_ago=95.0,    # +5.3%
            price_20d_ago=85.0,   # +17.6%
            price_60d_ago=70.0,   # +42.9%
            spy_return_5d=0.01,
            spy_return_20d=0.02,
            spy_return_60d=0.03,
        )
        comp = _score_relative_strength(inp)
        assert any("all three timeframes" in r.lower() for r in comp.reasons)

    def test_only_one_timeframe_no_all_timeframe_bonus(self):
        inp = AlphaInput(
            ticker="X",
            price=100.0,
            price_5d_ago=95.0,    # +5.3% vs SPY +1% → outperforms
            spy_return_5d=0.01,
        )
        comp = _score_relative_strength(inp)
        assert not any("all three timeframes" in r.lower() for r in comp.reasons)

    def test_score_clamped_to_10(self):
        inp = AlphaInput(
            ticker="X",
            price=200.0,
            price_5d_ago=100.0,   # +100%
            price_20d_ago=80.0,   # +150%
            price_60d_ago=60.0,   # +233%
            spy_return_5d=-0.10,
            spy_return_20d=-0.10,
            spy_return_60d=-0.10,
        )
        comp = _score_relative_strength(inp)
        assert comp.score <= 10.0

    def test_reasons_non_empty(self):
        comp = _score_relative_strength(_base())
        assert len(comp.reasons) > 0

    def test_weight_correct(self):
        comp = _score_relative_strength(_base())
        assert comp.weight == _WEIGHTS["relative_strength"]


# ─────────────────────────────────────────────
# Acceleration scorer
# ─────────────────────────────────────────────

class TestAcceleration:
    def test_5x_volume_scores_high(self):
        inp = AlphaInput(
            ticker="X", price=50.0,
            volume_today=5_000_000, avg_volume_20d=1_000_000,
        )
        comp = _score_acceleration(inp)
        assert comp.score >= 5.0

    def test_normal_volume_scores_low(self):
        inp = AlphaInput(
            ticker="X", price=50.0,
            volume_today=1_000_000, avg_volume_20d=1_000_000,
        )
        comp = _score_acceleration(inp)
        assert comp.score < 3.0

    def test_below_average_volume_reduces_score(self):
        inp_normal = AlphaInput(
            ticker="X", price=50.0,
            volume_today=1_000_000, avg_volume_20d=1_000_000,
        )
        inp_low = AlphaInput(
            ticker="X", price=50.0,
            volume_today=500_000, avg_volume_20d=1_000_000,
        )
        assert _score_acceleration(inp_low).score <= _score_acceleration(inp_normal).score

    def test_price_acceleration_adds_bonus(self):
        inp = AlphaInput(
            ticker="X", price=100.0,
            price_5d_ago=92.0,   # +8.7% in 5d
            price_20d_ago=90.0,  # +11.1% in 20d → 5d rate = 11.1/4 = 2.8%
        )                        # accel = 8.7% - 2.8% = 5.9% > 5% → bonus
        comp = _score_acceleration(inp)
        assert any("accelerat" in r.lower() for r in comp.reasons)

    def test_rsi_momentum_zone_adds_bonus(self):
        inp = AlphaInput(ticker="X", price=50.0, rsi=65.0)
        comp = _score_acceleration(inp)
        assert any("RSI" in r for r in comp.reasons)

    def test_macd_bullish_adds_bonus(self):
        inp = AlphaInput(ticker="X", price=50.0, macd=0.5, macd_signal=0.2)
        comp = _score_acceleration(inp)
        assert any("MACD" in r for r in comp.reasons)

    def test_missing_volume_and_price_data(self):
        comp = _score_acceleration(_base())
        assert isinstance(comp.score, float)
        assert comp.data_quality in ("MISSING", "MEDIUM", "LOW")

    def test_score_clamped(self):
        inp = AlphaInput(
            ticker="X", price=50.0,
            volume_today=100_000_000, avg_volume_20d=100_000,  # 1000x
            rsi=68.0, macd=1.0, macd_signal=0.1,
        )
        comp = _score_acceleration(inp)
        assert 0.0 <= comp.score <= 10.0


# ─────────────────────────────────────────────
# Squeeze scorer
# ─────────────────────────────────────────────

class TestSqueeze:
    def test_missing_short_data_returns_low(self):
        comp = _score_squeeze(_base())
        assert comp.score == 2.0
        assert comp.data_quality == "MISSING"

    def test_low_short_interest_scores_low(self):
        inp = AlphaInput(ticker="X", price=50.0, short_percent_float=0.05)
        comp = _score_squeeze(inp)
        assert comp.score < 3.0

    def test_high_short_interest_scores_high(self):
        inp = AlphaInput(ticker="X", price=50.0, short_percent_float=0.35)
        comp = _score_squeeze(inp)
        assert comp.score >= 4.0

    def test_extreme_short_scores_highest(self):
        inp = AlphaInput(ticker="X", price=50.0, short_percent_float=0.45)
        comp = _score_squeeze(inp)
        assert comp.score >= 5.0
        assert any("Extreme" in r for r in comp.reasons)

    def test_days_to_cover_adds_bonus(self):
        inp_dtc = AlphaInput(
            ticker="X", price=50.0,
            short_percent_float=0.20, days_to_cover=8.0,
        )
        inp_no_dtc = AlphaInput(
            ticker="X", price=50.0,
            short_percent_float=0.20,
        )
        assert _score_squeeze(inp_dtc).score > _score_squeeze(inp_no_dtc).score

    def test_volume_surge_into_short_amplifies_score(self):
        inp_vol = AlphaInput(
            ticker="X", price=50.0,
            short_percent_float=0.25,
            volume_today=3_000_000, avg_volume_20d=1_000_000,
        )
        inp_flat = AlphaInput(
            ticker="X", price=50.0,
            short_percent_float=0.25,
            volume_today=1_000_000, avg_volume_20d=1_000_000,
        )
        assert _score_squeeze(inp_vol).score > _score_squeeze(inp_flat).score

    def test_score_clamped(self):
        inp = AlphaInput(
            ticker="X", price=50.0,
            short_percent_float=0.60, days_to_cover=15.0,
            volume_today=5_000_000, avg_volume_20d=1_000_000,
        )
        comp = _score_squeeze(inp)
        assert 0.0 <= comp.score <= 10.0


# ─────────────────────────────────────────────
# Catalyst scorer
# ─────────────────────────────────────────────

class TestCatalyst:
    def test_no_catalyst_scores_neutral(self):
        """Phase A3: no catalyst data → neutral 5.0, quality MISSING."""
        comp = _score_catalyst(_base())
        assert comp.score == 5.0
        assert comp.data_quality == "MISSING"

    def test_earnings_in_1_to_7_days_scores_highest(self):
        inp = AlphaInput(ticker="X", price=50.0, earnings_days=3)
        comp = _score_catalyst(inp)
        assert comp.score >= 5.0

    def test_earnings_in_8_to_14_days_scores_medium(self):
        inp = AlphaInput(ticker="X", price=50.0, earnings_days=10)
        comp = _score_catalyst(inp)
        assert 3.0 <= comp.score <= 7.0

    def test_earnings_in_15_to_30_days_scores_lower(self):
        inp_10 = AlphaInput(ticker="X", price=50.0, earnings_days=10)
        inp_25 = AlphaInput(ticker="X", price=50.0, earnings_days=25)
        assert _score_catalyst(inp_10).score > _score_catalyst(inp_25).score

    def test_past_earnings_no_boost(self):
        inp = AlphaInput(ticker="X", price=50.0, earnings_days=-5)
        comp = _score_catalyst(inp)
        assert comp.score == 0.0

    def test_positive_news_sentiment_adds_bonus(self):
        inp_pos = AlphaInput(
            ticker="X", price=50.0,
            has_catalyst_news=True, news_sentiment=0.7,
        )
        inp_neg = AlphaInput(
            ticker="X", price=50.0,
            has_catalyst_news=True, news_sentiment=-0.2,
        )
        assert _score_catalyst(inp_pos).score > _score_catalyst(inp_neg).score

    def test_sec_8k_scores_non_missing(self):
        """Phase A3: has_sec_8k=True means catalyst data present — not MISSING."""
        inp_8k = AlphaInput(ticker="X", price=50.0, has_sec_8k=True)
        comp   = _score_catalyst(inp_8k)
        assert comp.data_quality != "MISSING"
        assert comp.score == 2.0  # only 8-K bonus

    def test_score_clamped(self):
        inp = AlphaInput(
            ticker="X", price=50.0,
            earnings_days=2, has_catalyst_news=True,
            news_sentiment=0.9, has_sec_8k=True,
        )
        comp = _score_catalyst(inp)
        assert 0.0 <= comp.score <= 10.0


# ─────────────────────────────────────────────
# Options scorer
# ─────────────────────────────────────────────

class TestOptions:
    def test_missing_data_returns_neutral(self):
        """Phase A3: no options data → neutral 5.0, quality MISSING."""
        comp = _score_options(_base())
        assert comp.score == 5.0
        assert comp.data_quality == "MISSING"

    def test_unusual_options_flag_boosts_score(self):
        inp = AlphaInput(
            ticker="X", price=50.0,
            unusual_options=True,
            call_volume=100_000, put_volume=100_000,  # neutral cp_ratio
        )
        comp_unusual = _score_options(inp)
        inp_normal   = AlphaInput(
            ticker="X", price=50.0,
            call_volume=100_000, put_volume=100_000,
        )
        assert comp_unusual.score > _score_options(inp_normal).score

    def test_high_call_put_ratio_scores_high(self):
        inp = AlphaInput(
            ticker="X", price=50.0,
            call_volume=500_000, put_volume=50_000,  # 10x
        )
        comp = _score_options(inp)
        assert comp.score >= 4.0

    def test_extreme_call_put_ratio_highest_bonus(self):
        inp = AlphaInput(
            ticker="X", price=50.0,
            call_volume=1_000_000, put_volume=100_000,  # 10x ≥ 5
        )
        comp = _score_options(inp)
        assert any("Extreme" in r for r in comp.reasons)

    def test_bearish_skew_penalizes(self):
        inp_bear = AlphaInput(
            ticker="X", price=50.0,
            call_volume=50_000, put_volume=200_000,  # 0.25x < 0.5
        )
        inp_bull = AlphaInput(
            ticker="X", price=50.0,
            call_volume=200_000, put_volume=50_000,
        )
        assert _score_options(inp_bear).score < _score_options(inp_bull).score

    def test_oi_expansion_adds_bonus(self):
        inp_oi = AlphaInput(
            ticker="X", price=50.0,
            call_oi=300_000, put_oi=50_000,  # 6x — has data, not MISSING
        )
        comp = _score_options(inp_oi)
        assert comp.score >= 2.0
        assert comp.data_quality == "HIGH"  # OI present → confirmed data, not MISSING

    def test_score_clamped(self):
        inp = AlphaInput(
            ticker="X", price=50.0,
            unusual_options=True,
            call_volume=1_000_000, put_volume=10_000,
            call_oi=5_000_000, put_oi=100_000,
        )
        comp = _score_options(inp)
        assert 0.0 <= comp.score <= 10.0


# ─────────────────────────────────────────────
# Breakout scorer
# ─────────────────────────────────────────────

class TestBreakout:
    def test_near_52w_high_scores_high(self):
        inp = AlphaInput(
            ticker="X", price=99.0,
            price_high_52w=100.0,  # within 1%
        )
        comp = _score_breakout(inp)
        assert comp.score >= 4.0
        assert any("52-week high" in r for r in comp.reasons)

    def test_far_from_52w_high_scores_low(self):
        inp = AlphaInput(
            ticker="X", price=50.0,
            price_high_52w=100.0,  # 50% below
        )
        comp = _score_breakout(inp)
        assert comp.score < 3.0

    def test_fresh_ma200_cross_scores_high(self):
        inp = AlphaInput(
            ticker="X", price=100.0,
            ma_200=90.0, ma_200_days_since_cross=10,
        )
        comp = _score_breakout(inp)
        assert comp.score >= 3.0
        assert any("fresh breakout" in r.lower() or "crossed above" in r.lower() for r in comp.reasons)

    def test_old_ma200_cross_lower_than_fresh(self):
        inp_fresh = AlphaInput(
            ticker="X", price=100.0,
            ma_200=90.0, ma_200_days_since_cross=5,
        )
        inp_old = AlphaInput(
            ticker="X", price=100.0,
            ma_200=90.0, ma_200_days_since_cross=None,  # long-established
        )
        assert _score_breakout(inp_fresh).score > _score_breakout(inp_old).score

    def test_below_ma200_penalizes(self):
        inp = AlphaInput(ticker="X", price=80.0, ma_200=100.0)
        comp = _score_breakout(inp)
        assert any("below MA200" in r for r in comp.reasons)

    def test_volume_confirmation_adds_bonus(self):
        inp_vol = AlphaInput(
            ticker="X", price=100.0,
            ma_200=90.0,
            volume_today=2_000_000, avg_volume_20d=1_000_000,
        )
        inp_no = AlphaInput(ticker="X", price=100.0, ma_200=90.0)
        assert _score_breakout(inp_vol).score > _score_breakout(inp_no).score

    def test_missing_data_returns_low(self):
        comp = _score_breakout(_base())
        assert comp.score == 0.0

    def test_score_clamped(self):
        inp = AlphaInput(
            ticker="X", price=100.0,
            price_high_52w=100.5,  # at 52w high
            ma_200=80.0, ma_200_days_since_cross=3,
            ma_50=90.0,
            volume_today=5_000_000, avg_volume_20d=1_000_000,
        )
        comp = _score_breakout(inp)
        assert 0.0 <= comp.score <= 10.0


# ─────────────────────────────────────────────
# Risk/Reward scorer
# ─────────────────────────────────────────────

class TestRiskReward:
    def test_no_data_returns_neutral(self):
        """Phase A3: no stop, no 52w high, no ATR → neutral 5.0, quality MISSING."""
        comp = _score_risk_reward(_base())
        assert comp.score == 5.0
        assert comp.data_quality == "MISSING"

    def test_tight_stop_scores_better_than_wide_stop(self):
        inp_tight = AlphaInput(
            ticker="X", price=100.0,
            stop_price=96.0,        # 4% stop
            price_high_52w=150.0,
        )
        inp_wide = AlphaInput(
            ticker="X", price=100.0,
            stop_price=80.0,        # 20% stop
            price_high_52w=150.0,
        )
        assert _score_risk_reward(inp_tight).score > _score_risk_reward(inp_wide).score

    def test_wide_stop_penalized(self):
        inp = AlphaInput(
            ticker="X", price=100.0,
            stop_price=83.0,   # 17% stop
        )
        comp = _score_risk_reward(inp)
        assert any("Wide stop" in r or "wide" in r.lower() for r in comp.reasons)

    def test_large_upside_scores_high(self):
        inp = AlphaInput(
            ticker="X", price=50.0,
            stop_price=47.0,       # 6% stop
            price_high_52w=100.0,  # 100% upside
        )
        comp = _score_risk_reward(inp)
        assert comp.score >= 5.0

    def test_favorable_reward_to_risk_adds_bonus(self):
        inp = AlphaInput(
            ticker="X", price=100.0,
            stop_price=96.0,       # 4% stop
            price_high_52w=150.0,  # 50% upside → RR = 12.5:1
        )
        comp = _score_risk_reward(inp)
        assert any("R:R" in r for r in comp.reasons)

    def test_poor_reward_to_risk_penalized(self):
        inp = AlphaInput(
            ticker="X", price=100.0,
            stop_price=95.0,       # 5% stop
            price_high_52w=106.0,  # 6% upside → RR = 1.2:1 < 1.5
        )
        comp = _score_risk_reward(inp)
        assert any("Poor R:R" in r or "poor" in r.lower() for r in comp.reasons)

    def test_high_atr_penalizes(self):
        inp_high_atr = AlphaInput(ticker="X", price=100.0, atr=12.0)  # 12% ATR
        inp_low_atr  = AlphaInput(ticker="X", price=100.0, atr=2.0)   # 2% ATR
        assert _score_risk_reward(inp_high_atr).score <= _score_risk_reward(inp_low_atr).score


# ─────────────────────────────────────────────
# Novelty scorer
# ─────────────────────────────────────────────

class TestNovelty:
    def test_never_alerted_scores_high(self):
        inp = AlphaInput(ticker="X", price=50.0, last_alerted_hours_ago=None)
        comp = _score_novelty(inp)
        assert comp.score >= 7.0

    def test_very_recent_alert_scores_low(self):
        inp = AlphaInput(ticker="X", price=50.0, last_alerted_hours_ago=6.0)
        comp = _score_novelty(inp)
        assert comp.score <= 2.0

    def test_recent_alert_scores_lower_than_old(self):
        inp_recent = AlphaInput(ticker="X", price=50.0, last_alerted_hours_ago=20.0)
        inp_old    = AlphaInput(ticker="X", price=50.0, last_alerted_hours_ago=100.0)
        assert _score_novelty(inp_recent).score < _score_novelty(inp_old).score

    def test_repeated_alerts_penalized(self):
        inp_many = AlphaInput(
            ticker="X", price=50.0,
            last_alerted_hours_ago=None, alert_count_30d=5,
        )
        inp_zero = AlphaInput(
            ticker="X", price=50.0,
            last_alerted_hours_ago=None, alert_count_30d=0,
        )
        assert _score_novelty(inp_many).score < _score_novelty(inp_zero).score

    def test_five_alerts_in_30d_heavy_penalty(self):
        inp = AlphaInput(
            ticker="X", price=50.0,
            last_alerted_hours_ago=None, alert_count_30d=5,
        )
        comp = _score_novelty(inp)
        assert any("staleness penalty" in r.lower() for r in comp.reasons)

    def test_score_clamped(self):
        inp = AlphaInput(
            ticker="X", price=50.0,
            last_alerted_hours_ago=1.0, alert_count_30d=10,
        )
        comp = _score_novelty(inp)
        assert 0.0 <= comp.score <= 10.0


# ─────────────────────────────────────────────
# Hard filters
# ─────────────────────────────────────────────

class TestFilters:
    def test_penny_stock_filtered_by_default(self):
        inp = AlphaInput(ticker="X", price=0.30, market_cap_millions=200.0, avg_volume_20d=200_000)
        reasons = _apply_filters(inp)
        assert any("penny_stock" in r for r in reasons)

    def test_penny_allowed_when_enabled(self):
        inp = AlphaInput(
            ticker="X", price=0.30,
            market_cap_millions=200.0, avg_volume_20d=200_000,
            penny_enabled=True,
        )
        reasons = _apply_filters(inp)
        assert not any("penny_stock" in r for r in reasons)

    def test_micro_cap_filtered_by_default(self):
        inp = AlphaInput(ticker="X", price=5.0, market_cap_millions=20.0, avg_volume_20d=200_000)
        reasons = _apply_filters(inp)
        assert any("micro_cap" in r for r in reasons)

    def test_micro_cap_allowed_when_penny_enabled(self):
        inp = AlphaInput(
            ticker="X", price=5.0,
            market_cap_millions=20.0, avg_volume_20d=200_000,
            penny_enabled=True,
        )
        reasons = _apply_filters(inp)
        assert not any("micro_cap" in r for r in reasons)

    def test_low_liquidity_filtered(self):
        # $1.00 × 100 shares = $100/day, way below $500K threshold
        inp = AlphaInput(ticker="X", price=1.0, avg_volume_20d=100.0, market_cap_millions=200.0)
        reasons = _apply_filters(inp)
        assert any("low_liquidity" in r for r in reasons)

    def test_dead_ticker_filtered(self):
        inp = AlphaInput(ticker="X", price=5.0, avg_volume_20d=0.0)
        reasons = _apply_filters(inp)
        assert any("dead_ticker" in r for r in reasons)

    def test_zero_price_filtered(self):
        inp = AlphaInput(ticker="X", price=0.0)
        reasons = _apply_filters(inp)
        assert any("invalid_price" in r for r in reasons)

    def test_valid_liquid_ticker_passes_all_filters(self):
        reasons = _apply_filters(_strong_setup())
        assert reasons == []

    def test_no_volume_data_skips_liquidity_check(self):
        inp = AlphaInput(ticker="X", price=5.0, market_cap_millions=200.0, avg_volume_20d=None)
        reasons = _apply_filters(inp)
        assert not any("liquidity" in r for r in reasons)


# ─────────────────────────────────────────────
# Tier classification and gates
# ─────────────────────────────────────────────

class TestTierClassification:
    def _comps(self, scores: list[float]) -> list[ComponentScore]:
        names = ["relative_strength", "acceleration", "squeeze", "catalyst",
                 "options", "breakout", "risk_reward", "novelty"]
        return [
            ComponentScore(n, s, _WEIGHTS[n], [f"score {s}"], "HIGH")
            for n, s in zip(names, scores)
        ]

    def test_score_below_35_is_ignore(self):
        tier, _, _ = _classify_tier(30.0, self._comps([3.0]*8))
        assert tier == "IGNORE"

    def test_score_35_to_49_is_watch(self):
        tier, _, _ = _classify_tier(44.0, self._comps([4.4]*8))
        assert tier == "WATCH"

    def test_score_50_to_64_is_strong_watch(self):
        tier, _, _ = _classify_tier(58.0, self._comps([5.8]*8))
        assert tier == "STRONG_WATCH"

    def test_high_conviction_gate_requires_3_strong_components(self):
        # Score 68 but only 2 components ≥ 6.0 → demoted to STRONG_WATCH
        scores = [8.0, 8.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0]
        tier, gate_applied, _ = _classify_tier(68.0, self._comps(scores))
        assert tier == "STRONG_WATCH"
        assert gate_applied is True

    def test_high_conviction_gate_passes_with_3_strong_components(self):
        scores = [8.0, 7.0, 6.0, 5.0, 5.0, 5.0, 5.0, 5.0]
        tier, gate_applied, _ = _classify_tier(68.0, self._comps(scores))
        assert tier == "HIGH_CONVICTION"
        assert gate_applied is False

    def test_rare_setup_gate_requires_4_components_at_7(self):
        # Score 82 but only 3 components ≥ 7.0
        scores = [9.0, 9.0, 8.0, 5.0, 5.0, 5.0, 5.0, 5.0]
        tier, gate_applied, _ = _classify_tier(82.0, self._comps(scores))
        assert tier != "RARE_SETUP"
        assert gate_applied is True

    def test_rare_setup_gate_passes_with_4_components_and_no_low(self):
        scores = [9.0, 8.0, 8.0, 7.0, 8.0, 5.0, 5.0, 5.0]
        tier, gate_applied, _ = _classify_tier(82.0, self._comps(scores))
        assert tier == "RARE_SETUP"
        assert gate_applied is False

    def test_rare_setup_fails_when_any_component_below_3(self):
        scores = [10.0, 9.0, 9.0, 8.0, 8.0, 8.0, 2.0, 8.0]  # rr = 2.0 < 3.0
        tier, gate_applied, _ = _classify_tier(85.0, self._comps(scores))
        assert tier != "RARE_SETUP"
        assert gate_applied is True

    def test_gate_note_is_informative_when_demoted(self):
        scores = [8.0, 8.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0]
        _, _, note = _classify_tier(68.0, self._comps(scores))
        assert len(note) > 0


# ─────────────────────────────────────────────
# Setup type classification
# ─────────────────────────────────────────────

class TestSetupType:
    def _scores(self, **overrides) -> dict[str, float]:
        base = {n: 3.0 for n in _WEIGHTS}
        base.update(overrides)
        return base

    def test_high_squeeze_score_and_short_interest_is_squeeze(self):
        inp = AlphaInput(ticker="X", price=50.0, short_percent_float=0.25)
        s = self._scores(squeeze=8.0)
        assert _classify_setup(inp, s) == "SQUEEZE_CANDIDATE"

    def test_squeeze_hard_rule_not_triggered_without_short_interest(self):
        """Without short_percent_float ≥ 0.20 the hard override doesn't fire.
        If another component scores higher, that setup type wins instead."""
        inp = AlphaInput(ticker="X", price=50.0, short_percent_float=None)
        s = self._scores(squeeze=8.0, breakout=9.0)  # breakout beats squeeze in fallback
        assert _classify_setup(inp, s) == "BREAKOUT_EXPANSION"

    def test_imminent_earnings_is_catalyst_runup(self):
        inp = AlphaInput(ticker="X", price=50.0, earnings_days=7)
        s = self._scores(catalyst=8.0)
        assert _classify_setup(inp, s) == "CATALYST_RUNUP"

    def test_catalyst_hard_rule_not_triggered_with_distant_earnings(self):
        """earnings_days > 14 doesn't trigger the catalyst hard override.
        If another component scores higher, that setup type wins instead."""
        inp = AlphaInput(ticker="X", price=50.0, earnings_days=30)
        s = self._scores(catalyst=8.0, breakout=9.0)  # breakout beats catalyst
        assert _classify_setup(inp, s) != "CATALYST_RUNUP"

    def test_unusual_options_with_high_score_is_options_pressure(self):
        inp = AlphaInput(ticker="X", price=50.0, unusual_options=True)
        s = self._scores(options=8.0)
        assert _classify_setup(inp, s) == "OPTIONS_PRESSURE"

    def test_high_breakout_score_is_breakout_expansion(self):
        inp = AlphaInput(ticker="X", price=50.0)
        s = self._scores(breakout=8.0)
        assert _classify_setup(inp, s) == "BREAKOUT_EXPANSION"

    def test_combined_rs_and_accel_is_early_accumulation(self):
        inp = AlphaInput(ticker="X", price=50.0)
        s = self._scores(relative_strength=7.0, acceleration=7.0)
        assert _classify_setup(inp, s) == "EARLY_ACCUMULATION"

    def test_setup_type_in_valid_set(self):
        result = ENGINE.score(_strong_setup())
        assert result.setup_type in _SETUP_TYPES


# ─────────────────────────────────────────────
# Integration: strong vs mediocre vs stale
# ─────────────────────────────────────────────

class TestIntegration:
    def test_strong_setup_scores_above_66(self):
        result = ENGINE.score(_strong_setup())
        assert result.alpha_score >= 66.0

    def test_strong_setup_reaches_high_conviction_or_rare(self):
        result = ENGINE.score(_strong_setup())
        assert result.tier in ("HIGH_CONVICTION", "RARE_SETUP")

    def test_mediocre_setup_does_not_reach_strong_watch(self):
        result = ENGINE.score(_mediocre_setup())
        assert result.tier in ("IGNORE", "WATCH")

    def test_mediocre_setup_scores_below_52(self):
        result = ENGINE.score(_mediocre_setup())
        assert result.alpha_score < 52.0

    def test_high_options_alone_not_enough_to_alert(self):
        result = ENGINE.score(_options_only_setup())
        assert result.tier in ("IGNORE", "WATCH")

    def test_high_options_alone_scores_below_52(self):
        result = ENGINE.score(_options_only_setup())
        assert result.alpha_score < 52.0

    def test_strong_relative_strength_improves_score(self):
        base = AlphaInput(
            ticker="X", price=100.0,
            price_5d_ago=100.0,   # flat
            price_20d_ago=100.0,
            spy_return_5d=0.02,
            spy_return_20d=0.04,
            market_cap_millions=200.0,
            avg_volume_20d=200_000,
        )
        strong_rs = AlphaInput(
            ticker="X", price=100.0,
            price_5d_ago=90.0,    # +11%
            price_20d_ago=75.0,   # +33%
            price_60d_ago=55.0,   # +82%
            spy_return_5d=0.01,
            spy_return_20d=0.02,
            spy_return_60d=0.03,
            market_cap_millions=200.0,
            avg_volume_20d=200_000,
        )
        assert ENGINE.score(strong_rs).alpha_score > ENGINE.score(base).alpha_score

    def test_risk_reward_penalty_reduces_score(self):
        tight_stop = AlphaInput(
            ticker="X", price=100.0,
            stop_price=96.0,       # 4% stop
            price_high_52w=150.0,  # 50% upside
            market_cap_millions=200.0,
            avg_volume_20d=200_000,
        )
        wide_stop = AlphaInput(
            ticker="X", price=100.0,
            stop_price=75.0,       # 25% stop
            price_high_52w=150.0,
            market_cap_millions=200.0,
            avg_volume_20d=200_000,
        )
        assert ENGINE.score(tight_stop).alpha_score > ENGINE.score(wide_stop).alpha_score

    def test_stale_repeated_alert_penalized(self):
        fresh = AlphaInput(
            ticker="X", price=50.0,
            market_cap_millions=200.0, avg_volume_20d=200_000,
            last_alerted_hours_ago=None, alert_count_30d=0,
        )
        stale = AlphaInput(
            ticker="X", price=50.0,
            market_cap_millions=200.0, avg_volume_20d=200_000,
            last_alerted_hours_ago=6.0, alert_count_30d=6,
        )
        assert ENGINE.score(fresh).alpha_score > ENGINE.score(stale).alpha_score

    def test_stale_setup_has_lower_novelty_score(self):
        stale = AlphaInput(
            ticker="X", price=50.0,
            last_alerted_hours_ago=10.0, alert_count_30d=5,
        )
        result = ENGINE.score(stale)
        assert result.novelty_score < 3.0


# ─────────────────────────────────────────────
# Determinism
# ─────────────────────────────────────────────

class TestDeterminism:
    def test_same_input_same_alpha_score(self):
        inp = _strong_setup()
        assert ENGINE.score(inp).alpha_score == ENGINE.score(inp).alpha_score

    def test_same_input_same_tier(self):
        inp = _strong_setup()
        assert ENGINE.score(inp).tier == ENGINE.score(inp).tier

    def test_same_input_same_setup_type(self):
        inp = _strong_setup()
        assert ENGINE.score(inp).setup_type == ENGINE.score(inp).setup_type

    def test_stable_across_five_calls(self):
        inp = _strong_setup()
        scores = [ENGINE.score(inp).alpha_score for _ in range(5)]
        assert len(set(scores)) == 1

    def test_different_inputs_different_scores(self):
        assert ENGINE.score(_strong_setup()).alpha_score != ENGINE.score(_mediocre_setup()).alpha_score


# ─────────────────────────────────────────────
# Sparse / None field handling
# ─────────────────────────────────────────────

class TestSparseData:
    def test_minimal_input_does_not_crash(self):
        result = ENGINE.score(AlphaInput(ticker="X", price=50.0))
        assert isinstance(result, AlphaResult)

    def test_all_optional_none_does_not_crash(self):
        inp = AlphaInput(ticker="X", price=10.0, market_cap_millions=100.0)
        result = ENGINE.score(inp)
        assert result.alpha_score >= 0.0
        assert result.tier in ("IGNORE", "WATCH", "STRONG_WATCH", "HIGH_CONVICTION", "RARE_SETUP")

    def test_zero_price_input_is_filtered(self):
        result = ENGINE.score(AlphaInput(ticker="X", price=0.0))
        assert result.filtered is True

    def test_none_rsi_does_not_crash(self):
        inp = AlphaInput(ticker="X", price=50.0, rsi=None)
        assert ENGINE.score(inp).alpha_score >= 0.0

    def test_none_short_interest_handled(self):
        inp = AlphaInput(ticker="X", price=50.0, short_percent_float=None)
        assert ENGINE.score(inp).squeeze_score == 2.0

    def test_none_earnings_days_handled(self):
        """Phase A3: earnings_days=None with no other catalyst → neutral 5.0."""
        inp = AlphaInput(ticker="X", price=50.0, earnings_days=None)
        assert ENGINE.score(inp).catalyst_score == 5.0

    def test_none_options_handled(self):
        """Phase A3: no options data → neutral 5.0."""
        inp = AlphaInput(ticker="X", price=50.0, call_volume=None, put_volume=None)
        assert ENGINE.score(inp).options_score == 5.0

    def test_none_stop_price_handled(self):
        """Phase A3: no stop, no 52w high, no ATR → neutral 5.0."""
        inp = AlphaInput(ticker="X", price=50.0, stop_price=None)
        assert ENGINE.score(inp).risk_reward_score == 5.0

    def test_alpha_score_always_in_range(self):
        for inp in [_base(), _strong_setup(), _mediocre_setup(), _options_only_setup()]:
            result = ENGINE.score(inp)
            assert 0.0 <= result.alpha_score <= 100.0


# ─────────────────────────────────────────────
# AlphaResult structure
# ─────────────────────────────────────────────

class TestResultStructure:
    def test_ticker_uppercased(self):
        result = ENGINE.score(AlphaInput(ticker="nvda", price=50.0))
        assert result.ticker == "NVDA"

    def test_eight_components_always_returned(self):
        result = ENGINE.score(_base())
        assert len(result.components) == 8

    def test_component_names_match_weights(self):
        result = ENGINE.score(_base())
        names = {c.name for c in result.components}
        assert names == set(_WEIGHTS.keys())

    def test_why_scored_high_not_empty(self):
        result = ENGINE.score(_strong_setup())
        assert len(result.why_scored_high) > 0

    def test_risk_factors_always_present(self):
        for inp in [_base(), _strong_setup(), _mediocre_setup()]:
            result = ENGINE.score(inp)
            assert len(result.risk_factors) > 0

    def test_holding_window_always_set(self):
        result = ENGINE.score(_strong_setup())
        assert len(result.expected_holding_window) > 0

    def test_holding_window_matches_setup_type(self):
        result = ENGINE.score(_strong_setup())
        expected = _HOLDING_WINDOWS.get(result.setup_type)
        if expected:
            assert result.expected_holding_window == expected

    def test_what_could_invalidate_non_empty(self):
        result = ENGINE.score(_strong_setup())
        assert len(result.what_could_invalidate) > 0

    def test_what_must_happen_next_non_empty(self):
        result = ENGINE.score(_strong_setup())
        assert len(result.what_must_happen_next) > 0

    def test_filter_reasons_empty_when_passes(self):
        result = ENGINE.score(_strong_setup())
        assert result.filter_reasons == []

    def test_filter_reasons_set_when_fails(self):
        result = ENGINE.score(AlphaInput(ticker="X", price=0.20))
        assert len(result.filter_reasons) > 0

    def test_filtered_flag_true_when_fails(self):
        result = ENGINE.score(AlphaInput(ticker="X", price=0.20))
        assert result.filtered is True


# ─────────────────────────────────────────────
# Canada / US compatibility
# ─────────────────────────────────────────────

class TestCanadaUSCompatibility:
    def test_canadian_ticker_identified(self):
        inp = AlphaInput(ticker="VFV.TO", price=100.0)
        assert inp.is_canadian is False  # default; fetch_alpha_input sets it

    def test_engine_handles_canadian_input(self):
        inp = AlphaInput(
            ticker="VFV.TO",
            price=100.0,
            market_cap_millions=5000.0,
            avg_volume_20d=500_000,
            is_canadian=True,
        )
        result = ENGINE.score(inp)
        assert isinstance(result, AlphaResult)
        assert result.ticker == "VFV.TO"

    def test_us_ticker_handled(self):
        inp = AlphaInput(ticker="AAPL", price=180.0, market_cap_millions=2_000_000.0)
        result = ENGINE.score(inp)
        assert result.ticker == "AAPL"


# ─────────────────────────────────────────────
# Weight sanity
# ─────────────────────────────────────────────

class TestWeights:
    def test_weights_sum_to_one(self):
        assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9

    def test_all_eight_weights_defined(self):
        expected = {
            "relative_strength", "acceleration", "squeeze", "catalyst",
            "options", "breakout", "risk_reward", "novelty",
        }
        assert set(_WEIGHTS.keys()) == expected

    def test_component_weights_match_constants(self):
        result = ENGINE.score(_base())
        for comp in result.components:
            assert comp.weight == _WEIGHTS[comp.name]
