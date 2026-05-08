"""
Market data fetching via yfinance.
All technical indicators live here.
"""
import warnings
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


# ──────────────────────────────────────────────
# Indicators
# ──────────────────────────────────────────────

def compute_rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return round(float(val), 1) if not np.isnan(val) else 50.0


def compute_macd(closes: pd.Series):
    """Returns (macd_line, signal_line, histogram) — all most-recent values."""
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist  = macd - signal
    return float(macd.iloc[-1]), float(signal.iloc[-1]), float(hist.iloc[-1])


def macd_direction(closes: pd.Series) -> str:
    _, _, hist = compute_macd(closes)
    prev_macd, prev_signal, _ = compute_macd(closes.iloc[:-1])
    _, _, prev_hist = compute_macd(closes.iloc[:-1])
    return "▲" if hist > 0 else "▼"


def macd_bearish_crossover(closes: pd.Series) -> bool:
    """True if MACD just crossed below signal line."""
    m1, s1, _ = compute_macd(closes)
    m0, s0, _ = compute_macd(closes.iloc[:-1])
    return (m0 >= s0) and (m1 < s1)


def rsi_rolling_over(closes: pd.Series) -> bool:
    """True if RSI > 70 and declining over last 3 bars."""
    if len(closes) < 20:
        return False
    r_now  = compute_rsi(closes)
    r_prev = compute_rsi(closes.iloc[:-1])
    r_prev2 = compute_rsi(closes.iloc[:-2])
    return r_now > 65 and r_now < r_prev < r_prev2


def ma200_recent_cross(closes: pd.Series, lookback: int = 20) -> tuple[bool, int]:
    """Return (crossed, days_ago) if price crossed from below to above its
    200-day SMA within the last `lookback` trading days AND the most recent
    close is still strictly above the MA200.

    'days_ago' is 0 when the cross happened on the most recent bar, 1 for
    the previous bar, etc.  Only the most recent below→above transition is
    reported when there are multiple crossings in the window.

    Returns (False, 0) when:
      - insufficient history (< 201 bars)
      - most recent close is at or below MA200 (cross was reversed)
      - price has been above MA200 throughout the window (permanent state)
      - no upward crossing found in the window

    Crossover condition is strict (p_curr > m_curr); equality at the MA200
    is not considered a confirmed breakout.

    The extra bar before the window (n = lookback + 1 slice) allows detection
    of a cross that lands on the very first bar of the lookback window.
    Effective n is clamped to len(closes) so lookback > data never crashes.
    """
    if len(closes) < 201:
        return False, 0

    rolling = closes.rolling(200).mean()

    # Clamp so iloc[-n:] never over-slices when lookback exceeds available rows
    n = min(lookback + 1, len(closes))
    prices_w = closes.iloc[-n:]
    ma200_w  = rolling.iloc[-n:]

    # Fast exit: if current close is not strictly above MA200 the cross is
    # either reversed or was never a real breakout.
    last_ma200 = ma200_w.iloc[-1]
    if pd.isna(last_ma200) or prices_w.iloc[-1] <= last_ma200:
        return False, 0

    last_cross_days_ago = -1

    for i in range(1, n):
        m_prev = ma200_w.iloc[i - 1]
        m_curr = ma200_w.iloc[i]
        if pd.isna(m_prev) or pd.isna(m_curr):
            continue
        p_prev = prices_w.iloc[i - 1]
        p_curr = prices_w.iloc[i]
        # Strict >: price touching MA200 from below is not a confirmed breakout
        if p_prev < m_prev and p_curr > m_curr:
            last_cross_days_ago = (n - 1) - i  # 0 = today, 1 = yesterday, …

    if last_cross_days_ago >= 0:
        return True, last_cross_days_ago
    return False, 0


# ──────────────────────────────────────────────
# Single ticker data
# ──────────────────────────────────────────────

def get_ticker_data(ticker: str):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1y")
        if hist.empty or len(hist) < 30:
            return None

        closes  = hist["Close"]
        volumes = hist["Volume"]
        price   = round(float(closes.iloc[-1]), 2)

        ma50  = round(float(closes.tail(50).mean()), 2) if len(closes) >= 50 else None
        ma200 = round(float(closes.tail(200).mean()), 2) if len(closes) >= 200 else None

        rsi = compute_rsi(closes)
        macd_val, macd_sig, macd_hist = compute_macd(closes)
        macd_dir = "▲" if macd_hist > 0 else "▼"

        vol_today = float(volumes.iloc[-1])
        vol_avg20 = float(volumes.tail(20).mean())
        vol_ratio = round(vol_today / vol_avg20, 2) if vol_avg20 > 0 else 1.0

        pct_1d  = round((closes.iloc[-1] / closes.iloc[-2] - 1) * 100, 2) if len(closes) >= 2 else 0
        pct_1m  = round((closes.iloc[-1] / closes.iloc[-22] - 1) * 100, 1) if len(closes) >= 22 else 0
        pct_3m  = round((closes.iloc[-1] / closes.iloc[-66] - 1) * 100, 1) if len(closes) >= 66 else 0

        # Earnings within 7 days
        earnings_soon = False
        try:
            cal = t.calendar
            if cal is not None and not cal.empty:
                if "Earnings Date" in cal.index:
                    ed = cal.loc["Earnings Date"].iloc[0]
                    if hasattr(ed, "date"):
                        ed = ed.date()
                    days_to = (ed - datetime.now().date()).days
                    earnings_soon = 0 <= days_to <= 7
        except Exception:
            pass

        # Bearish crossover / rsi rolling over
        bearish_cross = macd_bearish_crossover(closes)
        rsi_over = rsi_rolling_over(closes)

        # Below MA checks
        below_ma50  = ma50  is not None and price < ma50
        below_ma200 = ma200 is not None and price < ma200

        # Volume spike with drop
        vol_spike_drop = vol_ratio >= 2.0 and pct_1d < -1.0

        return {
            "ticker":         ticker,
            "price":          price,
            "rsi":            rsi,
            "macd_val":       round(macd_val, 4),
            "macd_signal":    round(macd_sig, 4),
            "macd_hist":      round(macd_hist, 4),
            "macd_dir":       macd_dir,
            "ma50":           ma50,
            "ma200":          ma200,
            "vol_ratio":      vol_ratio,
            "pct_1d":         pct_1d,
            "pct_1m":         pct_1m,
            "pct_3m":         pct_3m,
            "earnings_soon":  earnings_soon,
            # pre-computed signals
            "bearish_cross":  bearish_cross,
            "rsi_rolling_over": rsi_over,
            "below_ma50":     below_ma50,
            "below_ma200":    below_ma200,
            "vol_spike_drop": vol_spike_drop,
            "closes":         closes,   # keep series for further analysis
        }
    except Exception as e:
        print(f"  ⚠️  {ticker}: {e}")
        return None


def get_market_regime():
    """Returns (regime, spy_price, ma50, ma200)."""
    try:
        spy = yf.Ticker("SPY")
        hist = spy.history(period="1y")["Close"]
        price = float(hist.iloc[-1])
        ma50  = float(hist.tail(50).mean())
        ma200 = float(hist.tail(200).mean()) if len(hist) >= 200 else None

        if ma200 and price > ma200 and ma50 > ma200:
            regime = "BULL"
        elif ma200 and price < ma200:
            regime = "BEAR"
        else:
            regime = "SIDEWAYS"
        return regime, round(price, 2), round(ma50, 2), round(ma200, 2) if ma200 else None
    except Exception:
        return "SIDEWAYS", 0, None, None


def get_index_day_change() -> dict:
    """Returns day % change for SPY and XIU.TO (proxy for TSX)."""
    result = {}
    for sym, label in [("SPY", "S&P 500"), ("XIU.TO", "TSX")]:
        try:
            hist = yf.Ticker(sym).history(period="5d")["Close"]
            if len(hist) >= 2:
                pct = round((hist.iloc[-1] / hist.iloc[-2] - 1) * 100, 2)
                result[label] = pct
        except Exception:
            result[label] = 0.0
    return result
