"""
Market Analyst Research Dashboard Backend — Phase A23.

Provides educational market data, technical/fundamental analysis, ETF analysis,
macro indicators, news, and AI-generated commentary (educational only).

DISCLAIMER: All responses include a disclaimer that this is educational information
only and not financial advice or a recommendation to buy or sell any security.

No buy/sell/order language anywhere in generated content.
"""
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

DISCLAIMER = (
    "This is educational information only. "
    "It is not financial advice and is not a recommendation to buy or sell any security."
)

MARKET_TICKERS = [
    "^GSPC",    # S&P 500
    "^NDX",     # Nasdaq-100
    "^DJI",     # Dow Jones
    "^RUT",     # Russell 2000
    "^VIX",     # VIX
    "^TNX",     # 10-Year Treasury Yield
    "GC=F",     # Gold Futures
    "CL=F",     # Crude Oil Futures
    "BTC-USD",  # Bitcoin
    "DX-Y.NYB", # US Dollar Index
]

TICKER_LABELS = {
    "^GSPC":    "S&P 500",
    "^NDX":     "Nasdaq-100",
    "^DJI":     "Dow Jones",
    "^RUT":     "Russell 2000",
    "^VIX":     "VIX",
    "^TNX":     "10-Year Treasury Yield",
    "GC=F":     "Gold",
    "CL=F":     "Crude Oil",
    "BTC-USD":  "Bitcoin",
    "DX-Y.NYB": "US Dollar Index",
}

SECTOR_ETFS = {
    "XLK":  "Technology",
    "XLF":  "Financial",
    "XLE":  "Energy",
    "XLV":  "Health Care",
    "XLY":  "Consumer Discretionary",
    "XLP":  "Consumer Staples",
    "XLI":  "Industrials",
    "XLB":  "Materials",
    "XLRE": "Real Estate",
    "XLU":  "Utilities",
    "XLC":  "Communication Services",
}

VALID_PERIODS = ["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "10Y", "Max"]

PERIOD_MAP = {
    "1D":  ("1d",  "5m"),
    "5D":  ("5d",  "30m"),
    "1M":  ("1mo", "1d"),
    "3M":  ("3mo", "1d"),
    "6M":  ("6mo", "1d"),
    "YTD": ("ytd", "1d"),
    "1Y":  ("1y",  "1d"),
    "3Y":  ("3y",  "1wk"),
    "5Y":  ("5y",  "1wk"),
    "10Y": ("10y", "1mo"),
    "Max": ("max", "1mo"),
}

# AI banned words — checked before returning any AI-generated text
BANNED_WORDS_AI = [
    "buy", "sell", "must", "guaranteed", "moon", "explosion", "sure thing",
]

# Peer ETF groups for comparison
ETF_PEERS = {
    "XLK":  ["XLC", "IGV", "SOXX"],
    "XLF":  ["KBE", "KIE", "IAI"],
    "XLE":  ["OIH", "XOP", "AMLP"],
    "XLV":  ["IBB", "XBI", "IHI"],
    "XLY":  ["IBUY", "XRT", "PEJ"],
    "XLP":  ["VDC", "FSTA"],
    "XLI":  ["ITA", "XTN"],
    "XLB":  ["VAW", "PDBC"],
    "XLRE": ["VNQ", "IYR"],
    "XLU":  ["IDU", "FUTY"],
    "XLC":  ["XLK", "IGV"],
    "QQQ":  ["SPY", "IWM", "XLK"],
    "SPY":  ["QQQ", "IWM", "VTI"],
    "IWM":  ["SPY", "QQQ", "VBR"],
    "GLD":  ["SLV", "IAU", "PDBC"],
    "TLT":  ["IEF", "BND", "AGG"],
}


# ── Pure computation functions ────────────────────────────────────────────────

def _compute_rsi(closes, period: int = 14) -> float:
    """Wilder RSI. Returns 50.0 on insufficient data."""
    try:
        import pandas as pd
        import numpy as np
        s = pd.Series(closes, dtype=float)
        delta = s.diff().dropna()
        if len(delta) < period:
            return 50.0
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        last_gain = float(avg_gain.iloc[-1])
        last_loss = float(avg_loss.iloc[-1])
        if last_loss == 0:
            return 100.0 if last_gain > 0 else 50.0
        rs = last_gain / last_loss
        val = 100 - (100 / (1 + rs))
        return round(float(val), 1) if not (val != val) else 50.0
    except Exception:
        return 50.0


def _compute_sma(closes, period: int) -> Optional[float]:
    """Simple moving average of last `period` closes. None on insufficient data."""
    try:
        import pandas as pd
        s = pd.Series(closes, dtype=float).dropna()
        if len(s) < period:
            return None
        return round(float(s.rolling(period).mean().iloc[-1]), 4)
    except Exception:
        return None


def _compute_annualized_volatility(closes) -> Optional[float]:
    """Annualized volatility (std of log returns × sqrt(252)). None on < 2 points."""
    try:
        import pandas as pd
        import numpy as np
        s = pd.Series(closes, dtype=float).dropna()
        if len(s) < 2:
            return None
        log_returns = np.log(s / s.shift(1)).dropna()
        vol = float(log_returns.std()) * (252 ** 0.5)
        return round(vol, 4)
    except Exception:
        return None


def _rsi_bucket(rsi: float) -> str:
    if rsi >= 70:
        return "OVERBOUGHT"
    if rsi <= 30:
        return "OVERSOLD"
    return "NEUTRAL"


def _roe_tier(roe_pct: Optional[float]) -> str:
    if roe_pct is None:
        return "UNKNOWN"
    if roe_pct < 0:
        return "NEGATIVE"
    if roe_pct < 10:
        return "LOW"
    if roe_pct < 20:
        return "MODERATE"
    return "STRONG"


def _de_tier(de: Optional[float]) -> str:
    if de is None:
        return "UNKNOWN"
    if de < 0.5:
        return "LOW"
    if de < 1.5:
        return "MODERATE"
    return "HIGH"


def _beta_tier(beta: Optional[float]) -> str:
    if beta is None:
        return "UNKNOWN"
    if beta < 0.8:
        return "LOW_VOLATILITY"
    if beta < 1.2:
        return "MARKET_LIKE"
    if beta < 1.8:
        return "ELEVATED"
    return "HIGH_VOLATILITY"


def _valuation_tier(pe: Optional[float]) -> str:
    if pe is None or pe <= 0:
        return "UNKNOWN"
    if pe < 15:
        return "VALUE"
    if pe < 25:
        return "FAIR"
    if pe < 40:
        return "GROWTH"
    return "SPECULATIVE"


def _52week_position(lo: Optional[float], hi: Optional[float], price: Optional[float]) -> Optional[float]:
    """Position of price within 52-week range [lo, hi] as 0-100 pct. None if missing."""
    try:
        if lo is None or hi is None or price is None:
            return None
        rng = hi - lo
        if rng <= 0:
            return None
        return round(min(100.0, max(0.0, (price - lo) / rng * 100)), 1)
    except Exception:
        return None


def _technical_strength(
    price: Optional[float],
    sma200: Optional[float],
    sma50: Optional[float],
    rsi: float,
    pos52: Optional[float],
) -> int:
    """Technical strength score 0-100.

    price vs 200DMA:  30 pts
    SMA50 vs SMA200:  20 pts
    RSI position:     25 pts
    52-week position: 25 pts
    """
    score = 0

    # price vs 200DMA (30 pts)
    if price is not None and sma200 is not None:
        pct_above = (price - sma200) / sma200 * 100
        if pct_above > 5:
            score += 30
        elif pct_above > 0:
            score += 20
        elif pct_above > -5:
            score += 10

    # SMA50 vs SMA200 — golden/death cross (20 pts)
    if sma50 is not None and sma200 is not None:
        if sma50 > sma200:
            score += 20
        elif sma50 > sma200 * 0.97:
            score += 10

    # RSI position (25 pts)
    if rsi >= 55:
        score += 25
    elif rsi >= 45:
        score += 15
    elif rsi >= 35:
        score += 8

    # 52-week position (25 pts)
    if pos52 is not None:
        if pos52 >= 75:
            score += 25
        elif pos52 >= 50:
            score += 15
        elif pos52 >= 25:
            score += 8

    return min(100, score)


def _fundamental_quality(
    pe: Optional[float],
    roe_pct: Optional[float],
    de: Optional[float],
    beta: Optional[float],
) -> int:
    """Fundamental quality score 0-100.

    P/E:  25 pts
    ROE:  30 pts
    D/E:  25 pts
    Beta: 20 pts
    """
    score = 0

    # P/E (25 pts) — moderate PE preferred
    if pe is not None and pe > 0:
        if 10 <= pe <= 25:
            score += 25
        elif 25 < pe <= 40:
            score += 15
        elif pe < 10:
            score += 18  # cheap but may signal risk

    # ROE (30 pts)
    if roe_pct is not None:
        if roe_pct >= 20:
            score += 30
        elif roe_pct >= 10:
            score += 20
        elif roe_pct >= 0:
            score += 8

    # D/E (25 pts) — lower is generally safer
    if de is not None:
        if de < 0.5:
            score += 25
        elif de < 1.0:
            score += 18
        elif de < 2.0:
            score += 10

    # Beta (20 pts) — market-like preferred
    if beta is not None:
        if 0.8 <= beta <= 1.2:
            score += 20
        elif 0.5 <= beta < 0.8:
            score += 15
        elif 1.2 < beta <= 1.8:
            score += 10

    return min(100, score)


def _compute_etf_risk_score(closes) -> int:
    """ETF risk score 0-100 (higher = riskier).

    Annualized volatility: 70 pts weight
    Max drawdown:          30 pts weight
    """
    try:
        import pandas as pd
        import numpy as np
        s = pd.Series(closes, dtype=float).dropna()
        if len(s) < 5:
            return 50

        # Annualized volatility component (0-70)
        log_returns = np.log(s / s.shift(1)).dropna()
        vol = float(log_returns.std()) * (252 ** 0.5)
        # Map vol range [0, 0.8+] → [0, 70]
        vol_score = min(70, int(vol / 0.8 * 70))

        # Max drawdown component (0-30)
        roll_max = s.cummax()
        drawdown = (s - roll_max) / roll_max
        max_dd = abs(float(drawdown.min()))
        # Map max drawdown [0, 0.6+] → [0, 30]
        dd_score = min(30, int(max_dd / 0.6 * 30))

        return vol_score + dd_score
    except Exception:
        return 50


def _to_sparkline(hist, max_points: int = 100) -> list:
    """Convert yfinance history DataFrame to [{t: iso, v: float}, ...] list."""
    try:
        import pandas as pd
        import numpy as np
        if hist is None or len(hist) == 0:
            return []
        closes = hist["Close"].dropna()
        if len(closes) == 0:
            return []
        # Downsample if needed
        step = max(1, len(closes) // max_points)
        sampled = closes.iloc[::step]
        result = []
        for ts, v in sampled.items():
            try:
                if isinstance(ts, pd.Timestamp):
                    t = ts.isoformat()
                else:
                    t = str(ts)
                fv = float(v)
                if fv == fv:  # nan check
                    result.append({"t": t, "v": round(fv, 4)})
            except Exception:
                continue
        return result[:max_points]
    except Exception:
        return []


# ── Data functions ────────────────────────────────────────────────────────────

def get_ticker_snapshot(ticker: str, period: str = "1D") -> dict:
    """Return price snapshot for a single ticker.

    For 1D, baseline is previousClose (not intraday open) so return% is overnight-to-now.
    """
    result = {
        "ticker": ticker,
        "label": TICKER_LABELS.get(ticker, ticker),
        "period": period,
        "price": None,
        "prev_close": None,
        "change_pct": None,
        "sparkline": [],
        "disclaimer": DISCLAIMER,
    }
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
        price = info.get("regularMarketPrice") or info.get("currentPrice") or info.get("ask")
        prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")

        if price is None:
            period_yf, interval = PERIOD_MAP.get(period, ("1d", "5m"))
            hist = t.history(period=period_yf, interval=interval, auto_adjust=True)
            if len(hist) > 0:
                price = float(hist["Close"].iloc[-1])

        p = period if period in PERIOD_MAP else "1D"
        period_yf, interval = PERIOD_MAP[p]
        hist = t.history(period=period_yf, interval=interval, auto_adjust=True)

        sparkline = _to_sparkline(hist)
        if len(hist) > 0 and price is None:
            price = float(hist["Close"].iloc[-1])

        if p == "1D":
            baseline = prev_close
        else:
            if len(hist) > 0:
                baseline = float(hist["Close"].iloc[0])
            else:
                baseline = prev_close

        change_pct = None
        if price is not None and baseline is not None and baseline != 0:
            change_pct = round((price - baseline) / baseline * 100, 2)

        result["price"] = round(float(price), 4) if price is not None else None
        result["prev_close"] = round(float(prev_close), 4) if prev_close is not None else None
        result["change_pct"] = change_pct
        result["sparkline"] = sparkline
    except Exception as e:
        log.warning("get_ticker_snapshot(%s): %s", ticker, e)
    return result


def get_market_pulse(period: str = "1D") -> dict:
    """Return snapshots for all 10 market tickers + 11 sector ETFs."""
    if period not in VALID_PERIODS:
        period = "1D"

    market = []
    for ticker in MARKET_TICKERS:
        try:
            snap = get_ticker_snapshot(ticker, period)
            market.append(snap)
        except Exception as e:
            log.warning("get_market_pulse: %s error: %s", ticker, e)
            market.append({
                "ticker": ticker, "label": TICKER_LABELS.get(ticker, ticker),
                "period": period, "price": None, "prev_close": None,
                "change_pct": None, "sparkline": [],
            })

    sectors = []
    for ticker, name in SECTOR_ETFS.items():
        try:
            snap = get_ticker_snapshot(ticker, period)
            snap["sector_name"] = name
            sectors.append(snap)
        except Exception as e:
            log.warning("get_market_pulse sector %s: %s", ticker, e)
            sectors.append({
                "ticker": ticker, "sector_name": name, "label": ticker,
                "period": period, "price": None, "prev_close": None,
                "change_pct": None, "sparkline": [],
            })

    return {
        "period": period,
        "market": market,
        "sectors": sectors,
        "disclaimer": DISCLAIMER,
    }


def get_sector_performance(period: str = "1D") -> dict:
    """Return sector ETF performance summary."""
    if period not in VALID_PERIODS:
        period = "1D"
    sectors = []
    for ticker, name in SECTOR_ETFS.items():
        try:
            snap = get_ticker_snapshot(ticker, period)
            sectors.append({
                "ticker": ticker,
                "sector_name": name,
                "change_pct": snap.get("change_pct"),
                "price": snap.get("price"),
            })
        except Exception:
            sectors.append({"ticker": ticker, "sector_name": name, "change_pct": None, "price": None})

    # Sort by change_pct descending (None last)
    sectors.sort(key=lambda x: (x["change_pct"] is None, -(x["change_pct"] or 0)))
    return {"period": period, "sectors": sectors, "disclaimer": DISCLAIMER}


def get_stock_analysis(ticker: str, period: str = "1Y") -> dict:
    """Return full technical + fundamental analysis for a stock ticker.

    Technical score 0-100, fundamental quality score 0-100.
    Neutral at-a-glance language only.
    """
    if period not in VALID_PERIODS:
        period = "1Y"

    result = {
        "ticker": ticker.upper(),
        "period": period,
        "name": None,
        "sector": None,
        "industry": None,
        "quote": {
            "price": None,
            "prev_close": None,
            "change_pct": None,
            "market_cap": None,
            "volume": None,
            "avg_volume": None,
        },
        "fundamentals": {
            "pe_ratio": None,
            "forward_pe": None,
            "roe_pct": None,
            "debt_equity": None,
            "beta": None,
            "dividend_yield": None,
            "roe_tier": "UNKNOWN",
            "de_tier": "UNKNOWN",
            "beta_tier": "UNKNOWN",
            "valuation_tier": "UNKNOWN",
            "fundamental_score": 0,
        },
        "technicals": {
            "rsi": None,
            "rsi_bucket": "NEUTRAL",
            "sma50": None,
            "sma200": None,
            "week52_low": None,
            "week52_high": None,
            "week52_position": None,
            "annualized_volatility": None,
            "technical_score": 0,
        },
        "sparkline": [],
        "disclaimer": DISCLAIMER,
    }

    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}

        result["name"] = info.get("longName") or info.get("shortName")
        result["sector"] = info.get("sector")
        result["industry"] = info.get("industry")

        price = info.get("regularMarketPrice") or info.get("currentPrice")
        prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
        change_pct = None
        if price and prev_close and prev_close != 0:
            change_pct = round((price - prev_close) / prev_close * 100, 2)

        result["quote"] = {
            "price": _safe_float(price),
            "prev_close": _safe_float(prev_close),
            "change_pct": change_pct,
            "market_cap": info.get("marketCap"),
            "volume": info.get("regularMarketVolume") or info.get("volume"),
            "avg_volume": info.get("averageVolume"),
        }

        pe = _safe_float(info.get("trailingPE"))
        fwd_pe = _safe_float(info.get("forwardPE"))
        roe_raw = info.get("returnOnEquity")
        roe_pct = round(float(roe_raw) * 100, 2) if roe_raw is not None else None
        de = _safe_float(info.get("debtToEquity"))
        if de is not None:
            de = de / 100.0 if de > 10 else de  # yfinance sometimes returns as percentage
        beta = _safe_float(info.get("beta"))
        div_yield = _safe_float(info.get("dividendYield"))

        result["fundamentals"] = {
            "pe_ratio": pe,
            "forward_pe": fwd_pe,
            "roe_pct": roe_pct,
            "debt_equity": de,
            "beta": beta,
            "dividend_yield": div_yield,
            "roe_tier": _roe_tier(roe_pct),
            "de_tier": _de_tier(de),
            "beta_tier": _beta_tier(beta),
            "valuation_tier": _valuation_tier(pe),
            "fundamental_score": _fundamental_quality(pe, roe_pct, de, beta),
        }

        # Technicals from history
        period_yf, interval = PERIOD_MAP.get(period, ("1y", "1d"))
        hist = t.history(period=period_yf, interval=interval, auto_adjust=True)
        sparkline = _to_sparkline(hist)
        result["sparkline"] = sparkline

        closes = list(hist["Close"].dropna()) if len(hist) > 0 else []

        rsi = _compute_rsi(closes)
        sma50 = _compute_sma(closes, 50)
        sma200 = _compute_sma(closes, 200)
        ann_vol = _compute_annualized_volatility(closes)
        w52_low = _safe_float(info.get("fiftyTwoWeekLow"))
        w52_high = _safe_float(info.get("fiftyTwoWeekHigh"))
        pos52 = _52week_position(w52_low, w52_high, price)

        result["technicals"] = {
            "rsi": rsi,
            "rsi_bucket": _rsi_bucket(rsi),
            "sma50": sma50,
            "sma200": sma200,
            "week52_low": w52_low,
            "week52_high": w52_high,
            "week52_position": pos52,
            "annualized_volatility": ann_vol,
            "technical_score": _technical_strength(price, sma200, sma50, rsi, pos52),
        }

    except Exception as e:
        log.warning("get_stock_analysis(%s): %s", ticker, e)

    return result


def get_etf_analysis(ticker: str, period: str = "1Y") -> dict:
    """Return ETF analysis: returns table, risk score, holdings summary, peers."""
    if period not in VALID_PERIODS:
        period = "1Y"

    result = {
        "ticker": ticker.upper(),
        "period": period,
        "name": None,
        "category": None,
        "expense_ratio": None,
        "aum": None,
        "quote": {
            "price": None,
            "prev_close": None,
            "change_pct": None,
        },
        "returns": {},
        "risk": {
            "annualized_volatility": None,
            "risk_score": 50,
        },
        "top_holdings": [],
        "peers": ETF_PEERS.get(ticker.upper(), []),
        "sparkline": [],
        "disclaimer": DISCLAIMER,
    }

    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}

        result["name"] = info.get("longName") or info.get("shortName")
        result["category"] = info.get("category") or info.get("fundFamily")
        result["expense_ratio"] = _safe_float(info.get("annualReportExpenseRatio") or info.get("totalExpenseRatio"))
        result["aum"] = info.get("totalAssets")

        price = info.get("regularMarketPrice") or info.get("navPrice") or info.get("currentPrice")
        prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
        change_pct = None
        if price and prev_close and prev_close != 0:
            change_pct = round((price - prev_close) / prev_close * 100, 2)

        result["quote"] = {
            "price": _safe_float(price),
            "prev_close": _safe_float(prev_close),
            "change_pct": change_pct,
        }

        # Returns table across multiple periods
        returns = {}
        for p in ["1D", "5D", "1M", "3M", "6M", "1Y"]:
            try:
                snap = get_ticker_snapshot(ticker, p)
                returns[p] = snap.get("change_pct")
            except Exception:
                returns[p] = None
        result["returns"] = returns

        # Risk from 1Y history
        period_yf, interval = PERIOD_MAP.get(period, ("1y", "1d"))
        hist = t.history(period=period_yf, interval=interval, auto_adjust=True)
        result["sparkline"] = _to_sparkline(hist)

        closes = list(hist["Close"].dropna()) if len(hist) > 0 else []
        ann_vol = _compute_annualized_volatility(closes)
        risk_score = _compute_etf_risk_score(closes)

        result["risk"] = {
            "annualized_volatility": ann_vol,
            "risk_score": risk_score,
        }

        # Top holdings
        try:
            holdings = t.funds_data.top_holdings if hasattr(t, "funds_data") else None
            if holdings is not None and len(holdings) > 0:
                top = []
                for row in holdings.head(10).itertuples():
                    top.append({
                        "ticker": getattr(row, "Symbol", None) or getattr(row, "Ticker", None),
                        "name": getattr(row, "Name", None),
                        "weight_pct": _safe_float(getattr(row, "Holding Percent", None) or getattr(row, "holdingPercent", None)),
                    })
                result["top_holdings"] = top
        except Exception:
            pass

    except Exception as e:
        log.warning("get_etf_analysis(%s): %s", ticker, e)

    return result


def get_macro_data() -> dict:
    """Return macro indicators from FRED.

    Handles missing FRED_API_KEY and missing fredapi package gracefully.
    """
    result = {
        "available": False,
        "reason": None,
        "indicators": {},
        "disclaimer": DISCLAIMER,
    }

    fred_key = os.environ.get("FRED_API_KEY", "")
    if not fred_key:
        result["reason"] = "FRED_API_KEY not configured"
        return result

    try:
        from fredapi import Fred  # type: ignore
    except ImportError:
        result["reason"] = "fredapi package not installed"
        return result

    try:
        fred = Fred(api_key=fred_key)

        series_map = {
            "FEDFUNDS":  "Federal Funds Rate (%)",
            "CPIAUCSL":  "CPI YoY Inflation (%)",
            "UNRATE":    "Unemployment Rate (%)",
            "GDP":       "GDP Growth (annualized %)",
            "T10YIE":    "10-Year Breakeven Inflation (%)",
            "BAMLH0A0HYM2": "High Yield Spread (bps)",
            "DTWEXBGS":  "USD Trade-Weighted Index",
            "M2SL":      "M2 Money Supply ($B)",
        }

        indicators = {}
        for series_id, label in series_map.items():
            try:
                s = fred.get_series_latest_release(series_id)
                if s is not None and len(s) > 0:
                    latest = s.dropna()
                    if len(latest) > 0:
                        indicators[series_id] = {
                            "label": label,
                            "value": round(float(latest.iloc[-1]), 4),
                            "date": str(latest.index[-1].date()),
                        }
            except Exception as e:
                log.debug("FRED %s: %s", series_id, e)
                indicators[series_id] = {"label": label, "value": None, "date": None}

        result["available"] = True
        result["indicators"] = indicators
    except Exception as e:
        log.warning("get_macro_data FRED error: %s", e)
        result["reason"] = "FRED data fetch failed"

    return result


def get_market_news(max_items: int = 20) -> dict:
    """Return recent market news via yfinance (SPY as proxy). Always returns a list."""
    result = {
        "items": [],
        "disclaimer": DISCLAIMER,
    }
    try:
        import yfinance as yf
        t = yf.Ticker("SPY")
        news = t.news or []
        items = []
        for item in news[:max_items]:
            try:
                items.append({
                    "title": item.get("content", {}).get("title") or item.get("title"),
                    "publisher": item.get("content", {}).get("provider", {}).get("displayName") or item.get("publisher"),
                    "url": None,
                    "published_at": item.get("content", {}).get("pubDate") or item.get("providerPublishTime"),
                    "summary": item.get("content", {}).get("summary") or item.get("summary"),
                })
            except Exception:
                continue
        result["items"] = items
    except Exception as e:
        log.warning("get_market_news: %s", e)
    return result


def get_ticker_news(ticker: str, max_items: int = 10) -> dict:
    """Return recent news for a specific ticker via yfinance."""
    result = {
        "ticker": ticker.upper(),
        "items": [],
        "disclaimer": DISCLAIMER,
    }
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        news = t.news or []
        items = []
        for item in news[:max_items]:
            try:
                items.append({
                    "title": item.get("content", {}).get("title") or item.get("title"),
                    "publisher": item.get("content", {}).get("provider", {}).get("displayName") or item.get("publisher"),
                    "url": None,
                    "published_at": item.get("content", {}).get("pubDate") or item.get("providerPublishTime"),
                    "summary": item.get("content", {}).get("summary") or item.get("summary"),
                })
            except Exception:
                continue
        result["items"] = items
    except Exception as e:
        log.warning("get_ticker_news(%s): %s", ticker, e)
    return result


# ── AI functions ──────────────────────────────────────────────────────────────

def check_ai_banned_words(text: str) -> bool:
    """Return True if text contains any banned word (case-insensitive)."""
    lower = text.lower()
    for word in BANNED_WORDS_AI:
        if word.lower() in lower:
            return True
    return False


_AI_COMPLIANCE_FALLBACK = (
    "Analysis could not be completed due to compliance review. "
    + DISCLAIMER
)

_SYSTEM_PROMPT = (
    "You are a financial data analyst providing educational commentary. "
    "You describe market conditions factually and objectively. "
    "You do not provide financial advice, trading recommendations, or price targets. "
    "You never use the words buy, sell, must, guaranteed, moon, explosion, or 'sure thing'. "
    "All commentary is strictly educational."
)


def _call_anthropic(prompt: str, max_tokens: int = 400) -> Optional[str]:
    """Call Claude for educational commentary. Returns None on any error."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text if msg.content else None
    except Exception as e:
        log.warning("_call_anthropic: %s", e)
        return None


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def generate_stock_analysis_ai(ticker: str, analysis_data: dict) -> dict:
    """Generate educational AI commentary for a stock. No buy/sell language.

    Returns {"commentary": str, "disclaimer": str, "compliance_ok": bool}
    """
    result = {
        "ticker": ticker.upper(),
        "commentary": _AI_COMPLIANCE_FALLBACK,
        "disclaimer": DISCLAIMER,
        "compliance_ok": False,
    }

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        result["commentary"] = f"AI analysis unavailable: ANTHROPIC_API_KEY not configured. {DISCLAIMER}"
        return result

    try:
        tech = analysis_data.get("technicals", {})
        fund = analysis_data.get("fundamentals", {})
        quote = analysis_data.get("quote", {})

        prompt = (
            f"Provide 2-3 sentences of educational commentary about {ticker.upper()} "
            f"based on these indicators. Do not provide financial advice or recommendations.\n"
            f"Price: {quote.get('price')}, Change: {quote.get('change_pct')}%\n"
            f"RSI: {tech.get('rsi')} ({tech.get('rsi_bucket')})\n"
            f"Technical score: {tech.get('technical_score')}/100\n"
            f"P/E: {fund.get('pe_ratio')}, ROE: {fund.get('roe_pct')}%, "
            f"Valuation tier: {fund.get('valuation_tier')}\n"
            f"Fundamental score: {fund.get('fundamental_score')}/100"
        )

        text = _call_anthropic(prompt)
        if text is None:
            result["commentary"] = f"AI analysis temporarily unavailable. {DISCLAIMER}"
            return result

        if check_ai_banned_words(text):
            return result  # compliance_ok=False, fallback text

        result["commentary"] = text
        result["compliance_ok"] = True
    except Exception as e:
        log.warning("generate_stock_analysis_ai(%s): %s", ticker, e)
        result["commentary"] = f"AI analysis error. {DISCLAIMER}"

    return result


def generate_etf_analysis_ai(ticker: str, analysis_data: dict) -> dict:
    """Generate educational AI commentary for an ETF."""
    result = {
        "ticker": ticker.upper(),
        "commentary": _AI_COMPLIANCE_FALLBACK,
        "disclaimer": DISCLAIMER,
        "compliance_ok": False,
    }

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        result["commentary"] = f"AI analysis unavailable: ANTHROPIC_API_KEY not configured. {DISCLAIMER}"
        return result

    try:
        risk = analysis_data.get("risk", {})
        returns = analysis_data.get("returns", {})
        quote = analysis_data.get("quote", {})

        prompt = (
            f"Provide 2-3 sentences of educational commentary about the ETF {ticker.upper()} "
            f"based on these indicators. Do not provide financial advice.\n"
            f"Price: {quote.get('price')}, Today's change: {quote.get('change_pct')}%\n"
            f"Risk score: {risk.get('risk_score')}/100, "
            f"Annualized volatility: {risk.get('annualized_volatility')}\n"
            f"Returns: 1M={returns.get('1M')}%, 3M={returns.get('3M')}%, "
            f"1Y={returns.get('1Y')}%"
        )

        text = _call_anthropic(prompt)
        if text is None:
            result["commentary"] = f"AI analysis temporarily unavailable. {DISCLAIMER}"
            return result

        if check_ai_banned_words(text):
            return result

        result["commentary"] = text
        result["compliance_ok"] = True
    except Exception as e:
        log.warning("generate_etf_analysis_ai(%s): %s", ticker, e)
        result["commentary"] = f"AI analysis error. {DISCLAIMER}"

    return result


def generate_macro_analysis_ai(macro_data: dict) -> dict:
    """Generate educational AI commentary for macro indicators."""
    result = {
        "commentary": _AI_COMPLIANCE_FALLBACK,
        "disclaimer": DISCLAIMER,
        "compliance_ok": False,
    }

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        result["commentary"] = f"AI analysis unavailable: ANTHROPIC_API_KEY not configured. {DISCLAIMER}"
        return result

    try:
        indicators = macro_data.get("indicators", {})
        if not indicators:
            result["commentary"] = f"No macro data available for analysis. {DISCLAIMER}"
            return result

        summary_parts = []
        for series_id, data in list(indicators.items())[:6]:
            if data.get("value") is not None:
                summary_parts.append(f"{data['label']}: {data['value']}")

        prompt = (
            "Provide 2-3 sentences of educational commentary about current macroeconomic "
            "conditions based on these indicators. Do not provide financial advice.\n"
            + "\n".join(summary_parts)
        )

        text = _call_anthropic(prompt)
        if text is None:
            result["commentary"] = f"AI analysis temporarily unavailable. {DISCLAIMER}"
            return result

        if check_ai_banned_words(text):
            return result

        result["commentary"] = text
        result["compliance_ok"] = True
    except Exception as e:
        log.warning("generate_macro_analysis_ai: %s", e)
        result["commentary"] = f"AI analysis error. {DISCLAIMER}"

    return result
