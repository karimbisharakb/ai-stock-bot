"""
Phase A1: High-upside opportunity discovery — Alpha Engine.

Scores stocks 0–100 across 8 orthogonal components and classifies the setup
type. Surfaces rare asymmetric opportunities — not ordinary 6/10 setups.

Architecture:
  AlphaInput          — all raw signals; construct once and pass to score()
  ComponentScore      — per-component (0–10) with data-quality tag
  AlphaResult         — score, tier, setup type, full explanation
  AlphaEngine.score() — deterministic pure function: same input → same output
  fetch_alpha_input() — side-effectful network hydrator (yfinance + market_data)

Tier thresholds (gate checks apply on top of numeric score):
  IGNORE          alpha_score < 38
  WATCH           38 ≤ score < 52
  STRONG_WATCH    52 ≤ score < 66
  HIGH_CONVICTION 66 ≤ score < 80   (requires ≥3 components ≥ 6.0)
  RARE_SETUP      score ≥ 80        (requires ≥4 components ≥ 7.0, none below 3.0)

Score math:
  alpha_score = sum(component.score × component.weight) × 10
  Each weight is a fraction; weights sum to 1.0.
  Component score range: 0–10. Alpha score range: 0–100.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class AlphaInput:
    """All raw market signals for one ticker. Immutable after construction."""
    ticker: str

    # Price history (absolute, not fractional)
    price: float = 0.0
    price_5d_ago: Optional[float] = None
    price_20d_ago: Optional[float] = None
    price_60d_ago: Optional[float] = None
    price_high_52w: Optional[float] = None
    price_low_52w: Optional[float] = None

    # Volume
    volume_today: Optional[float] = None
    avg_volume_20d: Optional[float] = None
    avg_volume_5d: Optional[float] = None

    # Benchmark returns (fractional, e.g. 0.05 = +5%)
    spy_return_5d: Optional[float] = None
    spy_return_20d: Optional[float] = None
    spy_return_60d: Optional[float] = None
    qqq_return_5d: Optional[float] = None
    qqq_return_20d: Optional[float] = None
    qqq_return_60d: Optional[float] = None

    # Technical indicators
    rsi: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    ma_50: Optional[float] = None
    ma_200: Optional[float] = None
    ma_200_days_since_cross: Optional[int] = None  # days since price crossed above MA200

    # Short interest (fractions: 0.0–1.0)
    short_percent_float: Optional[float] = None
    days_to_cover: Optional[float] = None
    shares_float: Optional[float] = None

    # Options
    call_volume: Optional[float] = None
    put_volume: Optional[float] = None
    call_oi: Optional[float] = None
    put_oi: Optional[float] = None
    unusual_options: bool = False

    # Catalyst
    earnings_days: Optional[int] = None  # positive = upcoming, negative = past
    has_catalyst_news: bool = False
    news_sentiment: Optional[float] = None  # -1.0 to +1.0
    has_sec_8k: bool = False

    # Risk setup
    atr: Optional[float] = None
    stop_price: Optional[float] = None

    # Regime / macro
    regime: Optional[str] = None   # "BULL" | "NEUTRAL" | "BEAR"
    vix: Optional[float] = None

    # Novelty / staleness
    last_alerted_hours_ago: Optional[float] = None
    alert_count_30d: int = 0

    # Liquidity / classification flags
    market_cap_millions: Optional[float] = None
    is_canadian: bool = False
    penny_enabled: bool = False


@dataclass
class ComponentScore:
    name: str
    score: float        # 0–10
    weight: float
    reasons: list[str]
    data_quality: str   # "HIGH" | "MEDIUM" | "LOW" | "MISSING"


@dataclass
class AlphaResult:
    ticker: str
    alpha_score: float  # 0–100
    tier: str           # IGNORE / WATCH / STRONG_WATCH / HIGH_CONVICTION / RARE_SETUP
    setup_type: str     # see _SETUP_TYPES

    components: list[ComponentScore]

    # Per-component convenience scores (0–10)
    relative_strength_score: float
    acceleration_score: float
    squeeze_score: float
    catalyst_score: float
    options_score: float
    breakout_score: float
    risk_reward_score: float
    novelty_score: float

    # Explanation
    why_scored_high: list[str]
    what_could_invalidate: list[str]
    what_must_happen_next: list[str]
    risk_factors: list[str]
    expected_holding_window: str

    # Gate / filter metadata
    filtered: bool = False
    filter_reasons: list[str] = field(default_factory=list)
    tier_gate_applied: bool = False
    tier_gate_note: str = ""


# ── Constants ──────────────────────────────────────────────────────────────────

_WEIGHTS: dict[str, float] = {
    "relative_strength": 0.15,
    "acceleration":      0.15,
    "squeeze":           0.12,
    "catalyst":          0.15,
    "options":           0.13,
    "breakout":          0.15,
    "risk_reward":       0.10,
    "novelty":           0.05,
}
assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1.0"

_TIER_ORDER: list[tuple[float, str]] = [
    (80.0, "RARE_SETUP"),
    (66.0, "HIGH_CONVICTION"),
    (52.0, "STRONG_WATCH"),
    (38.0, "WATCH"),
    ( 0.0, "IGNORE"),
]

_SETUP_TYPES = frozenset({
    "EARLY_ACCUMULATION",
    "BREAKOUT_EXPANSION",
    "SQUEEZE_CANDIDATE",
    "CATALYST_RUNUP",
    "OPTIONS_PRESSURE",
    "HIGH_RISK_SPECULATION",
})

_HOLDING_WINDOWS: dict[str, str] = {
    "EARLY_ACCUMULATION":    "3–8 weeks (accumulation phase)",
    "BREAKOUT_EXPANSION":    "5–15 days (breakout momentum)",
    "SQUEEZE_CANDIDATE":     "3–21 days (squeeze event)",
    "CATALYST_RUNUP":        "1–10 days (into catalyst)",
    "OPTIONS_PRESSURE":      "1–5 days (options expiry window)",
    "HIGH_RISK_SPECULATION": "1–7 days (speculative, tight stop required)",
}

# Gate requirements for top tiers
_HIGH_CONVICTION_GATE  = 3     # minimum N components scoring ≥ floor
_HIGH_CONVICTION_FLOOR = 6.0
_RARE_SETUP_GATE       = 4
_RARE_SETUP_FLOOR      = 7.0
_RARE_SETUP_MIN        = 3.0   # no single component below this

# Hard-filter thresholds
_MIN_DAILY_DOLLAR_VOLUME = 500_000   # $500 K / day
_MIN_PRICE_DEFAULT       = 0.50
_MIN_MARKET_CAP_DEFAULT  = 50.0      # $50 M


# ── Internal helpers ───────────────────────────────────────────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, v))


def _pct_change(current: float, prev: Optional[float]) -> Optional[float]:
    if prev is None or prev <= 0 or current <= 0:
        return None
    return (current / prev) - 1.0


def _best_benchmark(spy: Optional[float], qqq: Optional[float]) -> Optional[float]:
    """Return whichever benchmark returned more (harder outperformance bar)."""
    candidates = [x for x in (spy, qqq) if x is not None]
    return max(candidates) if candidates else None


# ── Component scorers ──────────────────────────────────────────────────────────

def _score_relative_strength(inp: AlphaInput) -> ComponentScore:
    reasons: list[str] = []
    score = 0.0

    stock_r5  = _pct_change(inp.price, inp.price_5d_ago)
    stock_r20 = _pct_change(inp.price, inp.price_20d_ago)
    stock_r60 = _pct_change(inp.price, inp.price_60d_ago)

    bench_r5  = _best_benchmark(inp.spy_return_5d,  inp.qqq_return_5d)
    bench_r20 = _best_benchmark(inp.spy_return_20d, inp.qqq_return_20d)
    bench_r60 = _best_benchmark(inp.spy_return_60d, inp.qqq_return_60d)

    available = sum(1 for x in (stock_r5, stock_r20, stock_r60) if x is not None)

    if available == 0:
        return ComponentScore(
            "relative_strength", 4.0, _WEIGHTS["relative_strength"],
            ["No price history available — cannot confirm relative strength"], "MISSING",
        )

    outperform_count = 0

    if stock_r5 is not None and bench_r5 is not None:
        rs5 = stock_r5 - bench_r5
        if rs5 > 0.05:
            score += 3.5
            reasons.append(f"+{rs5*100:.1f}% vs benchmark (5d)")
            outperform_count += 1
        elif rs5 > 0.02:
            score += 1.5
            reasons.append(f"+{rs5*100:.1f}% vs benchmark (5d)")
            outperform_count += 1
        elif rs5 < -0.05:
            score -= 1.0
            reasons.append(f"Underperforming benchmark by {abs(rs5)*100:.1f}% (5d)")
    elif stock_r5 is not None:
        if stock_r5 > 0.05:
            score += 1.0
            reasons.append(f"5d return +{stock_r5*100:.1f}% (no benchmark available)")

    if stock_r20 is not None and bench_r20 is not None:
        rs20 = stock_r20 - bench_r20
        if rs20 > 0.10:
            score += 3.5
            reasons.append(f"+{rs20*100:.1f}% vs benchmark (20d)")
            outperform_count += 1
        elif rs20 > 0.05:
            score += 1.5
            reasons.append(f"+{rs20*100:.1f}% vs benchmark (20d)")
            outperform_count += 1
        elif rs20 < -0.10:
            score -= 1.0
    elif stock_r20 is not None:
        if stock_r20 > 0.10:
            score += 1.0
            reasons.append(f"20d return +{stock_r20*100:.1f}%")

    if stock_r60 is not None and bench_r60 is not None:
        rs60 = stock_r60 - bench_r60
        if rs60 > 0.15:
            score += 3.0
            reasons.append(f"+{rs60*100:.1f}% vs benchmark (60d)")
            outperform_count += 1
        elif rs60 > 0.08:
            score += 1.5
            reasons.append(f"+{rs60*100:.1f}% vs benchmark (60d)")
            outperform_count += 1
        elif rs60 < -0.15:
            score -= 1.0
    elif stock_r60 is not None:
        if stock_r60 > 0.15:
            score += 1.0
            reasons.append(f"60d return +{stock_r60*100:.1f}%")

    if outperform_count >= 3:
        score += 1.5
        reasons.append("Outperforming benchmark across all three timeframes")

    if not reasons:
        reasons.append("In-line with or underperforming benchmark")

    quality = "HIGH" if available >= 2 and bench_r5 is not None else "MEDIUM"
    return ComponentScore("relative_strength", _clamp(score), _WEIGHTS["relative_strength"], reasons, quality)


def _score_acceleration(inp: AlphaInput) -> ComponentScore:
    reasons: list[str] = []
    score = 0.0

    # Volume acceleration
    vol_ratio: Optional[float] = None
    if inp.volume_today and inp.avg_volume_20d and inp.avg_volume_20d > 0:
        vol_ratio = inp.volume_today / inp.avg_volume_20d
        if vol_ratio >= 5.0:
            score += 5.0
            reasons.append(f"Volume {vol_ratio:.1f}x average — extreme surge")
        elif vol_ratio >= 3.0:
            score += 3.5
            reasons.append(f"Volume {vol_ratio:.1f}x average — strong surge")
        elif vol_ratio >= 2.0:
            score += 2.0
            reasons.append(f"Volume {vol_ratio:.1f}x average")
        elif vol_ratio >= 1.5:
            score += 1.0
            reasons.append(f"Volume {vol_ratio:.1f}x average — elevated")
        elif vol_ratio < 0.7:
            score -= 0.5
            reasons.append(f"Volume below average ({vol_ratio:.1f}x)")

    # Price acceleration: recent 5d rate vs historical 20d rate
    stock_r5  = _pct_change(inp.price, inp.price_5d_ago)
    stock_r20 = _pct_change(inp.price, inp.price_20d_ago)
    if stock_r5 is not None and stock_r20 is not None:
        baseline = stock_r20 / 4  # expected 5d rate if linear through 20d period
        accel = stock_r5 - baseline
        if accel > 0.05:
            score += 2.5
            reasons.append(f"Price accelerating +{accel*100:.1f}% above trend")
        elif accel > 0.02:
            score += 1.5
            reasons.append("Price gaining momentum above recent trend")
        elif accel < -0.05:
            score -= 1.0
            reasons.append("Price decelerating — momentum fading")

    # RSI momentum zone (55–72 is ideal: trending, not overbought)
    if inp.rsi is not None:
        if 55 <= inp.rsi <= 72:
            score += 1.5
            reasons.append(f"RSI {inp.rsi:.0f} in momentum zone")
        elif inp.rsi > 72:
            score += 0.5
            reasons.append(f"RSI {inp.rsi:.0f} — extended, watch for reversal")
        elif inp.rsi < 40:
            score -= 0.5

    # MACD confirmation
    if inp.macd is not None and inp.macd_signal is not None:
        if inp.macd > inp.macd_signal and inp.macd > 0:
            score += 1.0
            reasons.append("MACD bullish above signal line")
        elif inp.macd < inp.macd_signal:
            score -= 0.5

    if not reasons:
        reasons.append("Insufficient data for acceleration analysis")

    quality: str
    if vol_ratio is not None:
        quality = "HIGH"
    elif stock_r5 is not None:
        quality = "MEDIUM"
    else:
        quality = "MISSING"

    return ComponentScore("acceleration", _clamp(score), _WEIGHTS["acceleration"], reasons, quality)


def _score_squeeze(inp: AlphaInput) -> ComponentScore:
    reasons: list[str] = []
    score = 0.0
    has_data = False

    if inp.short_percent_float is not None:
        has_data = True
        spf = inp.short_percent_float
        if spf >= 0.40:
            score += 5.0
            reasons.append(f"Extreme short interest: {spf*100:.0f}% of float")
        elif spf >= 0.25:
            score += 4.0
            reasons.append(f"High short interest: {spf*100:.0f}% of float")
        elif spf >= 0.15:
            score += 2.5
            reasons.append(f"Elevated short interest: {spf*100:.0f}% of float")
        elif spf >= 0.10:
            score += 1.0
            reasons.append(f"Short interest: {spf*100:.0f}% of float")

    if inp.days_to_cover is not None:
        has_data = True
        dtc = inp.days_to_cover
        if dtc >= 10:
            score += 3.0
            reasons.append(f"Days-to-cover {dtc:.1f} — severe squeeze pressure")
        elif dtc >= 5:
            score += 2.0
            reasons.append(f"Days-to-cover {dtc:.1f} — meaningful squeeze risk")
        elif dtc >= 3:
            score += 1.0
            reasons.append(f"Days-to-cover {dtc:.1f}")

    # Volume surging into short position amplifies squeeze potential
    if inp.short_percent_float is not None and inp.short_percent_float >= 0.15:
        if inp.volume_today and inp.avg_volume_20d and inp.avg_volume_20d > 0:
            vr = inp.volume_today / inp.avg_volume_20d
            if vr >= 2.0:
                score += 2.0
                reasons.append(f"Volume {vr:.1f}x avg surging into short position")

    if not has_data:
        return ComponentScore(
            "squeeze", 2.0, _WEIGHTS["squeeze"],
            ["Short interest data unavailable — squeeze potential unconfirmed"], "MISSING",
        )

    if not reasons:
        reasons.append("Minimal short interest — limited squeeze potential")

    return ComponentScore("squeeze", _clamp(score), _WEIGHTS["squeeze"], reasons, "HIGH")


def _score_catalyst(inp: AlphaInput) -> ComponentScore:
    reasons: list[str] = []
    score = 0.0

    if inp.earnings_days is not None:
        ed = inp.earnings_days
        if 1 <= ed <= 7:
            score += 5.0
            reasons.append(f"Earnings in {ed} day{'s' if ed != 1 else ''} — peak catalyst window")
        elif 8 <= ed <= 14:
            score += 3.5
            reasons.append(f"Earnings in {ed} days — building catalyst pressure")
        elif 15 <= ed <= 30:
            score += 2.0
            reasons.append(f"Earnings in {ed} days — early positioning window")
        # past earnings or > 30 days: no boost

    if inp.has_catalyst_news:
        score += 2.0
        if inp.news_sentiment is not None and inp.news_sentiment > 0.3:
            score += 1.5
            reasons.append(f"Strong positive catalyst news (sentiment {inp.news_sentiment:.2f})")
        elif inp.news_sentiment is not None and inp.news_sentiment > 0.0:
            score += 0.5
            reasons.append("Positive catalyst news")
        else:
            reasons.append("Catalyst news (sentiment unclear)")

    if inp.has_sec_8k:
        score += 2.0
        reasons.append("Recent SEC 8-K filing — material event")

    if not reasons:
        reasons.append("No catalyst identified")

    has_any = (inp.earnings_days is not None and inp.earnings_days > 0) or inp.has_catalyst_news or inp.has_sec_8k
    quality = "HIGH" if inp.earnings_days is not None else ("MEDIUM" if has_any else "LOW")
    return ComponentScore("catalyst", _clamp(score), _WEIGHTS["catalyst"], reasons, quality)


def _score_options(inp: AlphaInput) -> ComponentScore:
    reasons: list[str] = []
    score = 0.0

    if inp.unusual_options:
        score += 4.0
        reasons.append("Unusual options activity flagged")

    cp_ratio: Optional[float] = None
    if inp.call_volume and inp.put_volume and inp.put_volume > 0:
        cp_ratio = inp.call_volume / inp.put_volume
        if cp_ratio >= 5.0:
            score += 4.0
            reasons.append(f"Extreme call/put ratio: {cp_ratio:.1f}x")
        elif cp_ratio >= 3.0:
            score += 2.5
            reasons.append(f"High call/put ratio: {cp_ratio:.1f}x")
        elif cp_ratio >= 2.0:
            score += 1.5
            reasons.append(f"Call/put ratio {cp_ratio:.1f}x — bullish skew")
        elif cp_ratio <= 0.5:
            score -= 1.0
            reasons.append(f"Bearish options skew: {cp_ratio:.1f}x")
    elif inp.call_volume and not inp.put_volume:
        score += 0.5
        reasons.append("Call volume with no puts — limited context")

    # OI expansion
    if inp.call_oi and inp.put_oi and inp.put_oi > 0:
        oi_ratio = inp.call_oi / inp.put_oi
        if oi_ratio >= 3.0:
            score += 2.0
            reasons.append(f"Call OI {oi_ratio:.1f}x put OI — bullish positioning")
        elif oi_ratio >= 2.0:
            score += 1.0
            reasons.append(f"Call OI dominates ({oi_ratio:.1f}x put OI)")

    has_data = (
        any(x is not None for x in (inp.call_volume, inp.put_volume, inp.call_oi, inp.put_oi))
        or inp.unusual_options
    )

    if not has_data:
        return ComponentScore(
            "options", 1.0, _WEIGHTS["options"],
            ["No options data available — pressure unconfirmed"], "MISSING",
        )

    if not reasons:
        reasons.append("Options data present — no unusual activity detected")

    return ComponentScore("options", _clamp(score), _WEIGHTS["options"], reasons, "HIGH")


def _score_breakout(inp: AlphaInput) -> ComponentScore:
    reasons: list[str] = []
    score = 0.0

    # 52-week high proximity
    if inp.price_high_52w and inp.price_high_52w > 0 and inp.price > 0:
        pct_from_high = (inp.price_high_52w - inp.price) / inp.price_high_52w
        if pct_from_high <= 0.01:
            score += 4.0
            reasons.append("Within 1% of 52-week high — new high territory")
        elif pct_from_high <= 0.03:
            score += 3.0
            reasons.append(f"Within 3% of 52-week high")
        elif pct_from_high <= 0.07:
            score += 2.0
            reasons.append(f"Within 7% of 52-week high")
        elif pct_from_high <= 0.15:
            score += 1.0
            reasons.append(f"Within 15% of 52-week high")
        else:
            reasons.append(f"{pct_from_high*100:.0f}% below 52-week high — early stage")

    # MA200 relationship
    if inp.ma_200 is not None and inp.price > 0:
        if inp.price > inp.ma_200:
            if inp.ma_200_days_since_cross is not None and inp.ma_200_days_since_cross <= 20:
                score += 3.0
                reasons.append(f"Crossed above MA200 {inp.ma_200_days_since_cross}d ago — fresh breakout signal")
            else:
                score += 1.5
                reasons.append("Price above MA200 — breakout structure intact")
        else:
            score -= 1.0
            reasons.append("Price below MA200 — no uptrend structure")

    # MA50 adds confirmation
    if inp.ma_50 is not None and inp.price > 0:
        if inp.price > inp.ma_50:
            score += 1.0
            reasons.append("Price above MA50")
        else:
            score -= 0.5

    # Volume confirmation
    if inp.volume_today and inp.avg_volume_20d and inp.avg_volume_20d > 0:
        vr = inp.volume_today / inp.avg_volume_20d
        if vr >= 1.5:
            score += 1.5
            reasons.append(f"Volume {vr:.1f}x confirms breakout")

    if not reasons:
        reasons.append("Insufficient price structure data")

    has_data = inp.price_high_52w is not None or inp.ma_200 is not None
    quality = (
        "HIGH" if (inp.price_high_52w is not None and inp.ma_200 is not None)
        else ("MEDIUM" if has_data else "MISSING")
    )
    return ComponentScore("breakout", _clamp(score), _WEIGHTS["breakout"], reasons, quality)


def _score_risk_reward(inp: AlphaInput) -> ComponentScore:
    reasons: list[str] = []
    score = 0.0
    has_data = False

    entry = inp.price if inp.price > 0 else None

    # Stop distance
    stop_pct: Optional[float] = None
    if entry and inp.stop_price and 0 < inp.stop_price < entry:
        has_data = True
        stop_pct = (entry - inp.stop_price) / entry
        if stop_pct < 0.05:
            score += 2.0
            reasons.append(f"Tight stop: {stop_pct*100:.1f}% risk")
        elif stop_pct < 0.08:
            score += 1.5
            reasons.append(f"Reasonable stop: {stop_pct*100:.1f}% risk")
        elif stop_pct < 0.12:
            score += 0.5
            reasons.append(f"Moderate stop: {stop_pct*100:.1f}% risk")
        elif stop_pct >= 0.15:
            score -= 2.0
            reasons.append(f"Wide stop {stop_pct*100:.1f}% — unfavorable R:R")
        else:
            score -= 1.0
            reasons.append(f"Stop somewhat wide: {stop_pct*100:.1f}%")

    # Upside room — distance to 52-week high as proxy
    upside_pct: Optional[float] = None
    if entry and inp.price_high_52w and inp.price_high_52w > entry:
        has_data = True
        upside_pct = (inp.price_high_52w - entry) / entry
        if upside_pct > 0.30:
            score += 3.0
            reasons.append(f"Large upside: +{upside_pct*100:.0f}% to 52w high")
        elif upside_pct > 0.15:
            score += 2.0
            reasons.append(f"Upside room: +{upside_pct*100:.0f}% to 52w high")
        elif upside_pct > 0.08:
            score += 1.0
            reasons.append(f"Limited upside: +{upside_pct*100:.0f}% to 52w high")
        else:
            score -= 0.5
            reasons.append(f"Minimal upside: +{upside_pct*100:.0f}% to 52w high")

    # Reward-to-risk ratio
    if upside_pct is not None and stop_pct is not None and stop_pct > 0:
        rr = upside_pct / stop_pct
        if rr >= 4.0:
            score += 3.0
            reasons.append(f"Excellent R:R {rr:.1f}:1")
        elif rr >= 3.0:
            score += 2.0
            reasons.append(f"Strong R:R {rr:.1f}:1")
        elif rr >= 2.0:
            score += 1.0
            reasons.append(f"Acceptable R:R {rr:.1f}:1")
        elif rr < 1.5:
            score -= 1.5
            reasons.append(f"Poor R:R {rr:.1f}:1 — skip unless thesis is strong")

    # ATR penalty for erratic names
    if inp.atr and entry and entry > 0:
        atr_pct = inp.atr / entry
        if atr_pct > 0.08:
            score -= 1.0
            reasons.append(f"High ATR ({atr_pct*100:.0f}% of price) — erratic moves")

    if not has_data:
        reasons.append("No stop or upside target — position sizing unverified")
        return ComponentScore("risk_reward", 3.0, _WEIGHTS["risk_reward"], reasons, "LOW")

    if not reasons:
        reasons.append("Risk setup data present")

    quality = "HIGH" if (stop_pct is not None and upside_pct is not None) else "MEDIUM"
    return ComponentScore("risk_reward", _clamp(score), _WEIGHTS["risk_reward"], reasons, quality)


def _score_novelty(inp: AlphaInput) -> ComponentScore:
    reasons: list[str] = []

    if inp.last_alerted_hours_ago is None:
        score = 8.0
        reasons.append("No prior alert on record — fresh signal")
    elif inp.last_alerted_hours_ago >= 168:
        score = 7.0
        reasons.append(f"Last alerted {inp.last_alerted_hours_ago/24:.0f}d ago — signal may be fresh")
    elif inp.last_alerted_hours_ago >= 72:
        score = 6.0
        reasons.append(f"Last alerted {inp.last_alerted_hours_ago:.0f}h ago")
    elif inp.last_alerted_hours_ago >= 48:
        score = 5.0
        reasons.append(f"Last alerted {inp.last_alerted_hours_ago:.0f}h ago — cooling off")
    elif inp.last_alerted_hours_ago >= 24:
        score = 3.0
        reasons.append(f"Last alerted {inp.last_alerted_hours_ago:.0f}h ago — recent, edge fading")
    else:
        score = 1.0
        reasons.append(f"Last alerted {inp.last_alerted_hours_ago:.0f}h ago — stale repeat risk")

    # Accumulate penalty for repeatedly alerted tickers
    if inp.alert_count_30d >= 5:
        score = max(0.0, score - 3.0)
        reasons.append(f"Alerted {inp.alert_count_30d}x in 30 days — staleness penalty applied")
    elif inp.alert_count_30d >= 3:
        score = max(0.0, score - 1.5)
        reasons.append(f"Alerted {inp.alert_count_30d}x in 30 days")

    return ComponentScore("novelty", _clamp(score), _WEIGHTS["novelty"], reasons, "HIGH")


# ── Hard filters ───────────────────────────────────────────────────────────────

def _apply_filters(inp: AlphaInput) -> list[str]:
    """Return non-empty list of reasons if the ticker fails hard-quality gates."""
    reasons: list[str] = []

    if inp.price <= 0:
        reasons.append("invalid_price: zero or negative price")
        return reasons

    if not inp.penny_enabled:
        if inp.price < _MIN_PRICE_DEFAULT:
            reasons.append(f"penny_stock: ${inp.price:.2f} < ${_MIN_PRICE_DEFAULT:.2f} minimum")
        if inp.market_cap_millions is not None and inp.market_cap_millions < _MIN_MARKET_CAP_DEFAULT:
            reasons.append(f"micro_cap: ${inp.market_cap_millions:.0f}M < ${_MIN_MARKET_CAP_DEFAULT:.0f}M minimum")

    if inp.avg_volume_20d is not None:
        if inp.avg_volume_20d == 0:
            reasons.append("dead_ticker: zero average volume")
        else:
            daily_dollar = inp.avg_volume_20d * inp.price
            if daily_dollar < _MIN_DAILY_DOLLAR_VOLUME:
                reasons.append(
                    f"low_liquidity: ${daily_dollar:,.0f}/day < ${_MIN_DAILY_DOLLAR_VOLUME:,.0f} minimum"
                )

    return reasons


# ── Tier classifier ────────────────────────────────────────────────────────────

def _classify_tier(
    alpha_score: float,
    components: list[ComponentScore],
) -> tuple[str, bool, str]:
    """Return (tier, gate_applied, gate_note)."""
    scores = [c.score for c in components]

    base_tier = "IGNORE"
    for threshold, name in _TIER_ORDER:
        if alpha_score >= threshold:
            base_tier = name
            break

    if base_tier in ("IGNORE", "WATCH", "STRONG_WATCH"):
        return base_tier, False, ""

    if base_tier == "RARE_SETUP":
        above_rare    = sum(1 for s in scores if s >= _RARE_SETUP_FLOOR)
        min_component = min(scores)
        if above_rare >= _RARE_SETUP_GATE and min_component >= _RARE_SETUP_MIN:
            return "RARE_SETUP", False, ""
        # Try HIGH_CONVICTION gate
        above_hc = sum(1 for s in scores if s >= _HIGH_CONVICTION_FLOOR)
        if above_hc >= _HIGH_CONVICTION_GATE:
            note = (
                f"RARE_SETUP gate not met: need {_RARE_SETUP_GATE} components ≥ {_RARE_SETUP_FLOOR} "
                f"(have {above_rare}) and all ≥ {_RARE_SETUP_MIN} (min was {min_component:.1f})"
            )
            return "HIGH_CONVICTION", True, note
        note = "Neither RARE_SETUP nor HIGH_CONVICTION gates met — capped at STRONG_WATCH"
        return "STRONG_WATCH", True, note

    # HIGH_CONVICTION gate: ≥3 components ≥ 6.0
    above_hc = sum(1 for s in scores if s >= _HIGH_CONVICTION_FLOOR)
    if above_hc >= _HIGH_CONVICTION_GATE:
        return "HIGH_CONVICTION", False, ""
    note = (
        f"HIGH_CONVICTION gate not met: need {_HIGH_CONVICTION_GATE} components ≥ {_HIGH_CONVICTION_FLOOR}, "
        f"have {above_hc}"
    )
    return "STRONG_WATCH", True, note


# ── Setup type classifier ──────────────────────────────────────────────────────

_COMPONENT_TO_SETUP: dict[str, str] = {
    "relative_strength": "EARLY_ACCUMULATION",
    "acceleration":      "EARLY_ACCUMULATION",
    "squeeze":           "SQUEEZE_CANDIDATE",
    "catalyst":          "CATALYST_RUNUP",
    "options":           "OPTIONS_PRESSURE",
    "breakout":          "BREAKOUT_EXPANSION",
    "risk_reward":       "BREAKOUT_EXPANSION",
    "novelty":           "HIGH_RISK_SPECULATION",
}


def _classify_setup(inp: AlphaInput, scores: dict[str, float]) -> str:
    """Determine the dominant narrative for this setup."""
    # Hard-rule overrides (order matters)
    if scores["squeeze"] >= 7.0 and (inp.short_percent_float or 0.0) >= 0.20:
        return "SQUEEZE_CANDIDATE"
    if (
        scores["catalyst"] >= 7.0
        and inp.earnings_days is not None
        and 1 <= inp.earnings_days <= 14
    ):
        return "CATALYST_RUNUP"
    if scores["options"] >= 7.0 and inp.unusual_options:
        return "OPTIONS_PRESSURE"
    if scores["breakout"] >= 7.0:
        return "BREAKOUT_EXPANSION"
    if scores["relative_strength"] >= 6.0 and scores["acceleration"] >= 6.0:
        return "EARLY_ACCUMULATION"
    # Fallback: dominant component
    primary = max(scores, key=lambda k: scores[k])
    return _COMPONENT_TO_SETUP.get(primary, "HIGH_RISK_SPECULATION")


# ── Explanation generator ──────────────────────────────────────────────────────

def _build_explanation(
    inp: AlphaInput,
    scores: dict[str, float],
    setup_type: str,
    components: list[ComponentScore],
) -> tuple[list[str], list[str], list[str], list[str], str]:
    """Return (why_scored_high, invalidators, next_steps, risks, holding_window)."""

    # Why: reasons from components with score ≥ 6
    why: list[str] = []
    for comp in sorted(components, key=lambda c: c.score, reverse=True):
        if comp.score >= 6.0:
            why.extend(comp.reasons[:2])
    if not why:
        for comp in sorted(components, key=lambda c: c.score, reverse=True):
            if comp.score >= 4.0:
                why.extend(comp.reasons[:1])
    if not why:
        why.append("Multiple moderate signals aligned — thesis is preliminary")

    # Invalidators
    invalidate: list[str] = []
    if inp.earnings_days is not None and 1 <= inp.earnings_days <= 14:
        invalidate.append("Earnings miss invalidates setup immediately")
    if (inp.short_percent_float or 0.0) >= 0.15:
        invalidate.append("Failed squeeze — shorts don't cover, price stalls or reverses")
    if scores["breakout"] >= 6.0:
        invalidate.append("Breakout failure — price closes back below breakout level on volume")
    if inp.regime in ("BEAR", "RISK_OFF"):
        invalidate.append("Market deterioration — long setups fail in bear / risk-off regime")
    if scores["options"] >= 6.0:
        invalidate.append("Options activity proves to be hedging, not directional speculation")
    invalidate.append("Volume fails to confirm follow-through on next session")

    # Next steps
    next_steps: list[str] = []
    if setup_type == "BREAKOUT_EXPANSION":
        next_steps.append("Price must close above breakout level on volume ≥ 1.5x average")
        next_steps.append("Exit if it closes back inside base within 3 sessions")
    elif setup_type == "SQUEEZE_CANDIDATE":
        next_steps.append("Watch for accelerating price + volume as shorts are forced to cover")
        next_steps.append("Check short interest data weekly for change in short position")
    elif setup_type == "CATALYST_RUNUP":
        next_steps.append("Build position before catalyst date; plan exit before the event")
        next_steps.append("Reduce exposure into earnings — binary risk after event")
    elif setup_type == "OPTIONS_PRESSURE":
        next_steps.append("Confirm unusual sweeps repeat over 2+ sessions before adding")
        next_steps.append("Track option expiry date — peak pressure typically 3–7 days before")
    elif setup_type == "EARLY_ACCUMULATION":
        next_steps.append("Allow accumulation to develop — dip entries preferred")
        next_steps.append("Add to position if relative strength holds on market weakness")
    else:
        next_steps.append("Wait for volume and price confirmation before entering")
    next_steps.append("Set and respect stop immediately upon entry")

    # Risk factors
    risks: list[str] = []
    if inp.price > 0 and inp.atr and inp.atr / inp.price > 0.05:
        risks.append(f"High daily volatility — ATR is {inp.atr/inp.price*100:.0f}% of price")
    if (inp.short_percent_float or 0.0) >= 0.30:
        risks.append("Very high short interest — violent moves in both directions are possible")
    if inp.unusual_options:
        risks.append("Unusual options flow may reflect informed hedging, not bullish positioning")
    if inp.earnings_days is not None and 1 <= inp.earnings_days <= 7:
        risks.append("Binary earnings event within a week — large gap risk in either direction")
    if inp.regime == "BEAR":
        risks.append("Bear market regime — long setups face structural headwinds")
    if inp.vix is not None and inp.vix > 30:
        risks.append(f"Elevated VIX ({inp.vix:.0f}) — broad market stress, correlations spike")
    if scores["novelty"] <= 3.0:
        risks.append("Recent alert history — signal may be stale, edge degraded")
    if not risks:
        risks.append("Standard market risk — use a defined stop to limit downside")

    window = _HOLDING_WINDOWS.get(setup_type, "1–10 days (undefined setup)")
    return why, invalidate, next_steps, risks, window


# ── AlphaEngine ────────────────────────────────────────────────────────────────

class AlphaEngine:
    """Deterministic opportunity scorer. score() has no side effects."""

    def score(self, inp: AlphaInput) -> AlphaResult:
        """Score a single AlphaInput. Same input always produces same output."""
        # Hard filters
        filter_reasons = _apply_filters(inp)

        # Component scores
        components: list[ComponentScore] = [
            _score_relative_strength(inp),
            _score_acceleration(inp),
            _score_squeeze(inp),
            _score_catalyst(inp),
            _score_options(inp),
            _score_breakout(inp),
            _score_risk_reward(inp),
            _score_novelty(inp),
        ]

        # Weighted alpha score (0–100)
        alpha_score = round(sum(c.score * c.weight for c in components) * 10.0, 2)

        # Tier (with gate checks)
        tier, gate_applied, gate_note = _classify_tier(alpha_score, components)

        # Filter: cap tier when quality gates fail
        if filter_reasons and tier in ("HIGH_CONVICTION", "RARE_SETUP", "STRONG_WATCH"):
            gate_note = f"Tier capped to WATCH — filters: {'; '.join(filter_reasons)}"
            gate_applied = True
            tier = "WATCH"

        # Setup type
        score_map = {c.name: c.score for c in components}
        setup_type = _classify_setup(inp, score_map)

        # Explanation
        why, invalidate, next_steps, risks, window = _build_explanation(
            inp, score_map, setup_type, components
        )

        comp_by_name = {c.name: c.score for c in components}

        return AlphaResult(
            ticker=inp.ticker.upper(),
            alpha_score=alpha_score,
            tier=tier,
            setup_type=setup_type,
            components=components,
            relative_strength_score=comp_by_name["relative_strength"],
            acceleration_score=comp_by_name["acceleration"],
            squeeze_score=comp_by_name["squeeze"],
            catalyst_score=comp_by_name["catalyst"],
            options_score=comp_by_name["options"],
            breakout_score=comp_by_name["breakout"],
            risk_reward_score=comp_by_name["risk_reward"],
            novelty_score=comp_by_name["novelty"],
            why_scored_high=why,
            what_could_invalidate=invalidate,
            what_must_happen_next=next_steps,
            risk_factors=risks,
            expected_holding_window=window,
            filtered=bool(filter_reasons),
            filter_reasons=filter_reasons,
            tier_gate_applied=gate_applied,
            tier_gate_note=gate_note,
        )


# ── Data fetcher (network, side effects) ───────────────────────────────────────

def fetch_alpha_input(
    ticker: str,
    *,
    last_alerted_hours_ago: Optional[float] = None,
    alert_count_30d: int = 0,
    stop_price: Optional[float] = None,
    penny_enabled: bool = False,
    regime: Optional[str] = None,
) -> Optional[AlphaInput]:
    """Hydrate AlphaInput from yfinance and the local market_data module.

    Returns None ONLY when price history is truly unavailable (< 5 bars).
    Every other field — options, earnings, short interest, benchmarks — is
    best-effort: failure sets the field to None and the engine scores it MISSING.
    Each data source is isolated in its own try/except so one failing source
    never prevents the others from being collected.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.warning("fetch_alpha_input: yfinance not available — skipping %s", ticker)
        return None

    tkr = yf.Ticker(ticker)

    # ── info (best-effort) ────────────────────────────────────────────────────
    # yfinance 0.2+ raises for unlisted/delisted tickers; treat as empty dict.
    info: dict = {}
    try:
        raw = tkr.info
        if isinstance(raw, dict):
            info = raw
    except Exception as exc:
        log.info("fetch_alpha_input: %s info unavailable (%s) — continuing without it", ticker, exc)

    # ── Standard technical fields from market_data ────────────────────────────
    price = rsi = macd_v = macd_sig = ma_50 = ma_200 = None
    ma_200_cross_days = None

    try:
        from market_data import get_ticker_data, ma200_recent_cross
        mdata = get_ticker_data(ticker)
        if mdata:
            price    = mdata.get("price")
            rsi      = mdata.get("rsi")
            macd_v   = mdata.get("macd_val")
            macd_sig = mdata.get("macd_signal")
            ma_50    = mdata.get("ma50")
            ma_200   = mdata.get("ma200")
            closes_s = mdata.get("closes")
            if closes_s is not None:
                try:
                    crossed, days_ago = ma200_recent_cross(closes_s, lookback=30)
                    if crossed:
                        ma_200_cross_days = days_ago
                except Exception:
                    pass
    except Exception as exc:
        log.info("fetch_alpha_input: %s market_data unavailable (%s) — will use raw history", ticker, exc)

    # ── Raw 90-day history — THE ONE HARD REQUIREMENT ─────────────────────────
    # Without at least 5 bars there is nothing to score; return None.
    hist = None
    try:
        hist = tkr.history(period="90d")
    except Exception as exc:
        log.warning("fetch_alpha_input: %s history() raised — %s", ticker, exc)

    if hist is None or hist.empty or len(hist) < 5:
        log.info("fetch_alpha_input: %s — insufficient price history, returning None", ticker)
        return None

    # Price fallback from raw history when market_data was unavailable
    if price is None:
        try:
            price = float(hist["Close"].iloc[-1])
        except Exception:
            pass

    if price is None:
        log.info("fetch_alpha_input: %s — could not resolve price, returning None", ticker)
        return None

    # ── Price windows ─────────────────────────────────────────────────────────
    def _price_n_days_ago(n: int) -> Optional[float]:
        try:
            if len(hist) > n:
                return float(hist["Close"].iloc[-(n + 1)])
        except Exception:
            pass
        return None

    price_5d_ago  = _price_n_days_ago(5)
    price_20d_ago = _price_n_days_ago(20)
    price_60d_ago = _price_n_days_ago(60)

    # ── Volume ────────────────────────────────────────────────────────────────
    vol_today = avg_vol_20d = avg_vol_5d = None
    try:
        if "Volume" in hist.columns:
            vol_today   = float(hist["Volume"].iloc[-1])
            avg_vol_20d = float(hist["Volume"].tail(20).mean())
            avg_vol_5d  = float(hist["Volume"].tail(5).mean())
    except Exception as exc:
        log.info("fetch_alpha_input: %s volume parse failed (%s)", ticker, exc)

    # ── 52-week high/low ──────────────────────────────────────────────────────
    price_high_52w = info.get("fiftyTwoWeekHigh")
    price_low_52w  = info.get("fiftyTwoWeekLow")

    # ── Benchmark returns (all optional, each individually safe) ──────────────
    def _bench_return(sym: str, days: int) -> Optional[float]:
        try:
            bh = yf.Ticker(sym).history(period=f"{days + 10}d")
            if len(bh) <= days:
                return None
            old = float(bh["Close"].iloc[-(days + 1)])
            new = float(bh["Close"].iloc[-1])
            return (new / old - 1.0) if old > 0 else None
        except Exception:
            return None

    spy_r5, spy_r20, spy_r60 = (
        _bench_return("SPY", 5), _bench_return("SPY", 20), _bench_return("SPY", 60)
    )
    bench_sym = "XUS.TO" if ticker.upper().endswith((".TO", ".V")) else "QQQ"
    qqq_r5, qqq_r20, qqq_r60 = (
        _bench_return(bench_sym, 5), _bench_return(bench_sym, 20), _bench_return(bench_sym, 60)
    )

    # ── Short interest ────────────────────────────────────────────────────────
    short_pct     = info.get("shortPercentOfFloat")
    days_to_cover = info.get("shortRatio")
    shares_float  = info.get("floatShares")

    # ── Options (best-effort; Canadian tickers usually lack this) ─────────────
    call_vol = put_vol = call_oi = put_oi = None
    unusual_options = False
    try:
        expiries = tkr.options
        if expiries:
            chain = tkr.option_chain(expiries[0])
            if chain and hasattr(chain, "calls") and hasattr(chain, "puts"):
                call_vol = float(chain.calls["volume"].sum()) if "volume" in chain.calls.columns else None
                put_vol  = float(chain.puts["volume"].sum())  if "volume" in chain.puts.columns  else None
                call_oi  = float(chain.calls["openInterest"].sum()) if "openInterest" in chain.calls.columns else None
                put_oi   = float(chain.puts["openInterest"].sum())  if "openInterest" in chain.puts.columns  else None
                if call_vol and avg_vol_20d and avg_vol_20d > 0:
                    unusual_options = call_vol > (avg_vol_20d * 0.15)
    except Exception:
        pass

    # ── Earnings days (optional) ──────────────────────────────────────────────
    earnings_days: Optional[int] = None
    try:
        from datetime import date as _date
        cal = tkr.calendar
        if cal is not None and not (hasattr(cal, "empty") and cal.empty):
            import pandas as _pd
            if hasattr(cal, "loc") and "Earnings Date" in cal.index:
                ed = cal.loc["Earnings Date"].iloc[0]
            elif hasattr(cal, "iloc"):
                ed = cal.iloc[0, 0]
            else:
                ed = None
            if ed is not None:
                earnings_days = (_pd.Timestamp(ed).date() - _date.today()).days
    except Exception:
        pass

    # ── ATR (optional) ────────────────────────────────────────────────────────
    atr: Optional[float] = None
    try:
        if all(c in hist.columns for c in ("High", "Low", "Close")):
            highs      = hist["High"].tail(15).values
            lows       = hist["Low"].tail(15).values
            closes_arr = hist["Close"].tail(15).values
            trs = [
                max(highs[i] - lows[i],
                    abs(highs[i] - closes_arr[i - 1]),
                    abs(lows[i]  - closes_arr[i - 1]))
                for i in range(1, len(highs))
            ]
            atr = sum(trs) / len(trs) if trs else None
    except Exception:
        pass

    # ── Market cap (optional) ─────────────────────────────────────────────────
    mcap_raw = info.get("marketCap")
    market_cap_millions = mcap_raw / 1_000_000 if mcap_raw else None

    # ── VIX (optional) ────────────────────────────────────────────────────────
    vix: Optional[float] = None
    try:
        vix_hist = yf.Ticker("^VIX").history(period="2d")
        if not vix_hist.empty:
            vix = float(vix_hist["Close"].iloc[-1])
    except Exception:
        pass

    is_canadian = ticker.upper().endswith((".TO", ".V"))

    log.info(
        "fetch_alpha_input: built AlphaInput for %s — price=%.2f hist=%d bars"
        " rsi=%s ma200=%s short=%.0f%%",
        ticker, price, len(hist),
        f"{rsi:.1f}" if rsi is not None else "N/A",
        f"{ma_200:.2f}" if ma_200 is not None else "N/A",
        (short_pct or 0) * 100,
    )

    return AlphaInput(
        ticker=ticker,
        price=price,
        price_5d_ago=price_5d_ago,
        price_20d_ago=price_20d_ago,
        price_60d_ago=price_60d_ago,
        price_high_52w=price_high_52w,
        price_low_52w=price_low_52w,
        volume_today=vol_today,
        avg_volume_20d=avg_vol_20d,
        avg_volume_5d=avg_vol_5d,
        spy_return_5d=spy_r5,
        spy_return_20d=spy_r20,
        spy_return_60d=spy_r60,
        qqq_return_5d=qqq_r5,
        qqq_return_20d=qqq_r20,
        qqq_return_60d=qqq_r60,
        rsi=rsi,
        macd=macd_v,
        macd_signal=macd_sig,
        ma_50=ma_50,
        ma_200=ma_200,
        ma_200_days_since_cross=ma_200_cross_days,
        short_percent_float=short_pct,
        days_to_cover=days_to_cover,
        shares_float=shares_float,
        call_volume=call_vol,
        put_volume=put_vol,
        call_oi=call_oi,
        put_oi=put_oi,
        unusual_options=unusual_options,
        earnings_days=earnings_days,
        atr=atr,
        stop_price=stop_price,
        regime=regime,
        vix=vix,
        last_alerted_hours_ago=last_alerted_hours_ago,
        alert_count_30d=alert_count_30d,
        market_cap_millions=market_cap_millions,
        is_canadian=is_canadian,
        penny_enabled=penny_enabled,
    )
