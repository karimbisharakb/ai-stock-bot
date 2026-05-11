"""
Market regime evaluator for the Predator scanner.

Classifies current market conditions as BULL / NEUTRAL / RISK_OFF using:
  - SPY and QQQ position relative to their 200-day MA
  - Short-term trend (SPY price vs 20-day SMA)
  - VIX level (optional — degrades gracefully when unavailable)

Integration point: run_predator() calls get_market_regime() once per scan run,
then passes the result to apply_regime_penalty() for each scored ticker.

Scanning is never fully disabled — data failures default to NEUTRAL.
"""
import copy
import logging
from typing import NamedTuple, Optional

import yfinance as yf

log = logging.getLogger(__name__)

# ── Regime state constants ────────────────────────────────────────────────────
BULL     = "BULL"
NEUTRAL  = "NEUTRAL"
RISK_OFF = "RISK_OFF"

# ── Penalty multipliers and thresholds ───────────────────────────────────────
_NEUTRAL_PENALTY         = 0.90    # confidence × this in NEUTRAL  (−10%)
_RISK_OFF_PENALTY        = 0.75    # confidence × this in RISK_OFF (−25%)
_BREAKOUT_SUPPRESS_SCORE = 1       # suppress breakout if score ≤ this in RISK_OFF
_VIX_HIGH                = 25.0    # elevated fear → may downgrade BULL → NEUTRAL
_VIX_EXTREME             = 35.0    # extreme fear → always RISK_OFF


class MarketRegime(NamedTuple):
    state:            str            # BULL | NEUTRAL | RISK_OFF
    spy_above_200ma:  bool
    qqq_above_200ma:  bool
    short_term_trend: str            # UP | FLAT | DOWN
    vix:              Optional[float]
    reason:           str


# ── Pure classification logic (fully unit-testable, no I/O) ──────────────────

def _classify(
    spy_above: bool,
    qqq_above: bool,
    trend:     str,
    vix:       Optional[float],
) -> tuple:
    """
    Classify regime from pre-fetched indicators.

    Decision tree:
      1. VIX ≥ 35  → RISK_OFF  (extreme fear overrides everything)
      2. Both above 200MA and trend ≠ DOWN:
             VIX < 25 (or absent) → BULL
             VIX ≥ 25             → NEUTRAL  (fear moderates bull signal)
      3. Both below 200MA         → RISK_OFF
      4. Trend=DOWN + VIX ≥ 25   → RISK_OFF
      5. Trend=DOWN (low/no VIX) → NEUTRAL
      6. Mixed MA signal          → NEUTRAL

    Returns (state: str, reason: str).
    """
    # 1. Extreme fear override
    if vix is not None and vix >= _VIX_EXTREME:
        return RISK_OFF, f"VIX={vix:.1f} — extreme fear override"

    above_count = int(spy_above) + int(qqq_above)

    # 2. Both indices healthy
    if above_count == 2 and trend != "DOWN":
        if vix is None or vix < _VIX_HIGH:
            return BULL, "SPY+QQQ above 200MA, trend not down, VIX calm"
        return NEUTRAL, f"SPY+QQQ above 200MA but VIX={vix:.1f} elevated"

    # 3. Both indices broken
    if above_count == 0:
        return RISK_OFF, "SPY and QQQ both below 200MA"

    # 4–5. Downward short-term trend
    if trend == "DOWN":
        if vix is not None and vix >= _VIX_HIGH:
            return RISK_OFF, f"Trend=DOWN with VIX={vix:.1f}"
        return NEUTRAL, "Short-term trend DOWN — weakening momentum"

    # 6. Mixed MA signal (one above, one below), trend not DOWN
    which = "SPY" if spy_above else "QQQ"
    return NEUTRAL, f"Only {which} above 200MA — mixed signal"


# ── Data fetchers (module-level so they can be patched in tests) ──────────────

def _fetch_above_200ma(symbol: str) -> Optional[bool]:
    """Return True/False if symbol is above its 200-day MA; None on failure."""
    try:
        hist = yf.Ticker(symbol).history(period="1y")["Close"]
        if len(hist) < 200:
            return None
        return bool(float(hist.iloc[-1]) > float(hist.tail(200).mean()))
    except Exception as exc:
        log.warning("market_regime: %s 200MA fetch failed: %s", symbol, exc)
        return None


def _fetch_spy_trend() -> str:
    """UP / FLAT / DOWN — SPY price vs its 20-day SMA (±1.5% band)."""
    try:
        hist = yf.Ticker("SPY").history(period="3mo")["Close"]
        if len(hist) < 20:
            return "FLAT"
        ma20  = float(hist.tail(20).mean())
        price = float(hist.iloc[-1])
        pct   = (price / ma20 - 1) * 100
        if pct > 1.5:
            return "UP"
        if pct < -1.5:
            return "DOWN"
        return "FLAT"
    except Exception as exc:
        log.warning("market_regime: SPY trend fetch failed: %s", exc)
        return "FLAT"


def _fetch_vix() -> Optional[float]:
    """Return latest VIX close, or None on any failure."""
    try:
        hist = yf.Ticker("^VIX").history(period="5d")["Close"]
        if hist.empty:
            return None
        return round(float(hist.iloc[-1]), 1)
    except Exception as exc:
        log.debug("market_regime: VIX fetch failed (non-critical): %s", exc)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def get_market_regime() -> MarketRegime:
    """
    Fetch SPY, QQQ, and VIX data and return a classified MarketRegime.

    Failures degrade gracefully:
      - single data source failure → treated as worst-case for that input
      - both index fetches fail    → returns NEUTRAL (scanning continues)
      - complete unexpected error  → returns NEUTRAL
    """
    try:
        spy_above = _fetch_above_200ma("SPY")
        qqq_above = _fetch_above_200ma("QQQ")
        trend     = _fetch_spy_trend()
        vix       = _fetch_vix()
    except Exception as exc:
        log.warning("market_regime: unexpected error (%s) — defaulting to NEUTRAL", exc)
        return MarketRegime(NEUTRAL, False, False, "FLAT", None,
                            "fetch error — defaulting to NEUTRAL")

    if spy_above is None and qqq_above is None:
        log.warning("market_regime: no SPY/QQQ 200MA data — defaulting to NEUTRAL")
        return MarketRegime(NEUTRAL, False, False, trend, vix,
                            "no index data — defaulting to NEUTRAL")

    safe_spy = spy_above if spy_above is not None else False
    safe_qqq = qqq_above if qqq_above is not None else False

    state, reason = _classify(safe_spy, safe_qqq, trend, vix)

    vix_str = f"{vix:.1f}" if vix is not None else "n/a"
    log.info(
        "market_regime: %s | SPY>200MA=%s QQQ>200MA=%s trend=%s VIX=%s | %s",
        state, safe_spy, safe_qqq, trend, vix_str, reason,
    )

    return MarketRegime(
        state=state,
        spy_above_200ma=safe_spy,
        qqq_above_200ma=safe_qqq,
        short_term_trend=trend,
        vix=vix,
        reason=reason,
    )


def apply_regime_penalty(
    confidence: float,
    signals:    dict,
    regime:     MarketRegime,
) -> tuple:
    """
    Apply market-regime-aware confidence and signal penalties.

    Returns (penalized_confidence, modified_signals_copy, suppressed_count).

    Penalties by regime:
        BULL     — no change (pass-through)
        NEUTRAL  — confidence × 0.90  (−10%)
        RISK_OFF — confidence × 0.75  (−25%)
                   + suppress weak breakout signals (score ≤ 1)

    The returned signals dict is always a deep copy — the input is never mutated.
    Suppressed signals have their score zeroed and reason prefixed with
    "[suppressed — RISK_OFF]" so downstream code and logs can identify them.
    """
    modified   = copy.deepcopy(signals)
    suppressed = 0

    if regime.state == BULL:
        log.debug("market_regime: BULL — no penalty applied")
        return round(confidence, 2), modified, 0

    if regime.state == NEUTRAL:
        penalized = round(confidence * _NEUTRAL_PENALTY, 2)
        log.info(
            "market_regime: NEUTRAL penalty — confidence %.1f%% → %.1f%% (×%.2f)",
            confidence, penalized, _NEUTRAL_PENALTY,
        )
        return penalized, modified, 0

    # RISK_OFF — confidence penalty + weak-breakout suppression
    brk_score = int(modified.get("breakout", {}).get("score") or 0)
    if brk_score <= _BREAKOUT_SUPPRESS_SCORE:
        orig = modified.get("breakout", {})
        modified["breakout"] = {
            **orig,
            "score": 0,
            "reason": f"[suppressed — RISK_OFF] {orig.get('reason', '')}".strip(),
        }
        suppressed += 1

    penalized = round(confidence * _RISK_OFF_PENALTY, 2)
    log.info(
        "market_regime: RISK_OFF penalty — confidence %.1f%% → %.1f%% (×%.2f); "
        "suppressed %d signal(s) | %s",
        confidence, penalized, _RISK_OFF_PENALTY, suppressed, regime.reason,
    )

    return penalized, modified, suppressed
