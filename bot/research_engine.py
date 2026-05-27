"""
Research Engine — AI-powered market research suite.

Provides:
  - Multi-analyst AI personas (Value, Momentum, Risk, Macro)
  - Multi-ticker comparison
  - Reddit/social sentiment scanner (no API key required)
  - News impact analyzer
  - Daily market brief generator
  - Sector heatmap data

All heavy operations are cached with a 5-minute TTL.
"""
import logging
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional

import pytz
import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

log = logging.getLogger(__name__)

EASTERN = pytz.timezone("America/New_York")

# ─────────────────────────────────────────────────────────────
# In-memory cache (5-min TTL)
# ─────────────────────────────────────────────────────────────

_CACHE: dict = {}
_CACHE_TTL = 300  # seconds


def _cached(key: str, factory):
    entry = _CACHE.get(key)
    if entry and time.time() - entry["ts"] < _CACHE_TTL:
        return entry["data"]
    data = factory()
    _CACHE[key] = {"data": data, "ts": time.time()}
    return data


# ─────────────────────────────────────────────────────────────
# AI Personas
# ─────────────────────────────────────────────────────────────

PERSONAS = {
    "VALUE": {
        "name": "Value Investor",
        "emoji": "🏰",
        "color": "positive",
        "intro": "I analyze businesses through the lens of Warren Buffett and Charlie Munger. I focus on durable competitive moats, free cash flow, intrinsic value, and margin of safety. Ask me about any ticker or investment idea.",
        "system": (
            "You are a seasoned value investor trained in the Buffett/Munger tradition. "
            "You focus on: business moat (durable competitive advantage), intrinsic value calculation "
            "using DCF or owner earnings, free cash flow yield, return on invested capital, margin of safety, "
            "and long-term compounding. You are skeptical of high-multiple growth stocks unless the moat is exceptional. "
            "Always remind the user this is educational analysis, not financial advice. "
            "Be concise: 3-4 paragraphs max. Use specific numbers from the data when available."
        ),
    },
    "MOMENTUM": {
        "name": "Momentum Trader",
        "emoji": "⚡",
        "color": "accent",
        "intro": "I read price action, volume, and momentum indicators. I spot breakouts, trend continuations, and relative strength setups. Give me a ticker and I'll tell you what the chart is saying.",
        "system": (
            "You are an expert technical momentum trader. You focus on: price action and trend direction, "
            "volume confirmation of moves, breakout setups above key resistance, relative strength vs sector and SPY, "
            "RSI momentum (50 cross = trend change, 70 = overbought, 30 = oversold), MACD crossovers, "
            "52-week high/low positioning, and moving average relationships (price vs 50/200 MA). "
            "Give specific entry zones, stop-loss levels, and upside targets when analyzing setups. "
            "Always remind the user this is educational analysis, not financial advice. "
            "Be concise and actionable. Use bullet points for key levels."
        ),
    },
    "RISK": {
        "name": "Risk Analyst",
        "emoji": "🛡️",
        "color": "warning",
        "intro": "I quantify what can go wrong. I analyze volatility, drawdown risk, correlation, position sizing, and tail risks. Before you buy, I'll tell you the downside scenarios.",
        "system": (
            "You are a risk management specialist focused on capital preservation. "
            "You analyze: historical volatility and beta, maximum drawdown in bear markets, "
            "concentration risk and correlation to existing holdings, position sizing recommendations "
            "(Kelly criterion or 2% rule), tail risks (sector headwinds, regulatory risk, balance sheet stress), "
            "hedging strategies (put options, inverse ETFs, sector rotation), and liquidity risk. "
            "Always calculate the worst-case scenario and how much capital could be lost. "
            "Remind users that past volatility doesn't guarantee future outcomes. "
            "Be specific about numbers: 'the stock dropped 65% in 2022' not 'the stock can drop significantly'."
        ),
    },
    "MACRO": {
        "name": "Macro Strategist",
        "emoji": "🌍",
        "color": "purple",
        "intro": "I connect individual stocks to the big picture. Interest rates, currency moves, sector rotation, geopolitics — I find how macro forces affect your portfolio. Ask me about any macro theme or how it affects specific tickers.",
        "system": (
            "You are a macro strategist who connects individual securities to broader economic forces. "
            "You focus on: Federal Reserve policy and rate sensitivity, USD strength/weakness and currency impact, "
            "sector rotation (growth vs value, cyclical vs defensive), commodity cycles (oil, gold, copper), "
            "yield curve shape and recession signals, geopolitical risk and supply chain impacts, "
            "emerging market contagion, and fiscal stimulus effects. "
            "When analyzing a stock, always place it in macro context: who benefits from rising rates? "
            "Who benefits from USD weakness? Which sectors lead in each economic regime? "
            "Always remind users this is educational macro commentary, not investment advice."
        ),
    },
}


def get_persona(persona_key: str) -> dict:
    return PERSONAS.get(persona_key.upper(), PERSONAS["VALUE"])


# ─────────────────────────────────────────────────────────────
# Claude helper
# ─────────────────────────────────────────────────────────────

def _call_claude(system: str, user_msg: str, max_tokens: int = 600) -> str:
    try:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return "⚠️ ANTHROPIC_API_KEY not configured."
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.warning("_call_claude error: %s", e)
        return f"⚠️ Analysis temporarily unavailable: {str(e)[:80]}"


# ─────────────────────────────────────────────────────────────
# yfinance helpers
# ─────────────────────────────────────────────────────────────

def _safe_price(ticker: str) -> float:
    try:
        t = yf.Ticker(ticker)
        return round(float(t.fast_info.last_price or 0), 2)
    except Exception:
        return 0.0


def _safe_change_pct(ticker: str) -> float:
    try:
        hist = yf.Ticker(ticker).history(period="2d")["Close"]
        if len(hist) >= 2:
            return round((hist.iloc[-1] / hist.iloc[-2] - 1) * 100, 2)
    except Exception:
        pass
    return 0.0


def _fetch_ticker_metrics(ticker: str) -> dict:
    """Fetch comprehensive yfinance metrics for one ticker. Safe for ThreadPoolExecutor."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        hist = t.history(period="1y")

        price = round(float(t.fast_info.last_price or info.get("regularMarketPrice", 0) or 0), 2)
        prev_close = round(float(info.get("regularMarketPreviousClose", price) or price), 2)
        change_pct = round((price / prev_close - 1) * 100, 2) if prev_close else 0.0

        # 52-week range
        closes = hist["Close"] if not hist.empty else None
        week52_low = round(float(closes.min()), 2) if closes is not None else 0.0
        week52_high = round(float(closes.max()), 2) if closes is not None else 0.0

        # RSI
        rsi = 50.0
        if closes is not None and len(closes) >= 15:
            from market_data import compute_rsi
            rsi = round(compute_rsi(closes), 1)

        # Market cap
        market_cap = info.get("marketCap") or info.get("totalAssets") or 0
        market_cap_b = round(market_cap / 1e9, 2) if market_cap else 0.0

        return {
            "ticker": ticker,
            "price": price,
            "change_pct": change_pct,
            "market_cap_b": market_cap_b,
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "rev_growth": round((info.get("revenueGrowth") or 0) * 100, 1),
            "net_margin": round((info.get("profitMargins") or 0) * 100, 1),
            "roe": round((info.get("returnOnEquity") or 0) * 100, 1),
            "beta": info.get("beta"),
            "dividend_yield": round((info.get("dividendYield") or 0) * 100, 2),
            "week52_low": week52_low,
            "week52_high": week52_high,
            "analyst_rating": info.get("recommendationKey", "N/A"),
            "rsi": rsi,
            "sector": info.get("sector", ""),
            "name": info.get("shortName", ticker),
            "error": None,
        }
    except Exception as exc:
        log.warning("_fetch_ticker_metrics %s: %s", ticker, exc)
        return {"ticker": ticker, "price": 0, "change_pct": 0, "error": str(exc)[:60]}


# ─────────────────────────────────────────────────────────────
# Multi-ticker comparison
# ─────────────────────────────────────────────────────────────

def compare_tickers(tickers: list, perspective: str = "ALL") -> dict:
    """
    Returns a side-by-side comparison of up to 5 tickers.
    perspective: "FUNDAMENTALS" | "TECHNICALS" | "RISK" | "ALL"
    """
    tickers = [t.upper().strip() for t in tickers[:5] if t.strip()]
    if not tickers:
        return {"error": "No tickers provided"}

    with ThreadPoolExecutor(max_workers=min(len(tickers), 5)) as pool:
        metrics_list = list(pool.map(_fetch_ticker_metrics, tickers))

    metrics_by_ticker = {m["ticker"]: m for m in metrics_list}

    # Build comparison rows
    def _fmt(val, fmt="{:.1f}", default="N/A"):
        try:
            return fmt.format(float(val)) if val is not None else default
        except Exception:
            return default

    rows = []
    fields = [
        ("Price", "price", "${:.2f}", "green"),
        ("Market Cap ($B)", "market_cap_b", "{:.1f}B", None),
        ("P/E Ratio", "pe_ratio", "{:.1f}x", None),
        ("Fwd P/E", "forward_pe", "{:.1f}x", None),
        ("Rev Growth", "rev_growth", "{:+.1f}%", "green"),
        ("Net Margin", "net_margin", "{:.1f}%", "green"),
        ("ROE", "roe", "{:.1f}%", "green"),
        ("Beta", "beta", "{:.2f}", None),
        ("Div Yield", "dividend_yield", "{:.2f}%", None),
        ("RSI", "rsi", "{:.0f}", None),
        ("52w Range", None, None, None),
        ("Day Chg", "change_pct", "{:+.2f}%", "green"),
        ("Analyst Rating", "analyst_rating", "{}", None),
    ]

    for label, field, fmt, prefer in fields:
        if field == "rsi" and perspective == "FUNDAMENTALS":
            continue
        if field in ("pe_ratio", "forward_pe", "rev_growth", "net_margin", "roe") and perspective == "TECHNICALS":
            continue

        row_vals = {}
        for t in tickers:
            m = metrics_by_ticker.get(t, {})
            if field is None:  # 52w range
                lo = m.get("week52_low", 0)
                hi = m.get("week52_high", 0)
                row_vals[t] = f"${lo:.2f}–${hi:.2f}" if lo else "N/A"
            elif fmt:
                v = m.get(field)
                row_vals[t] = fmt.format(v) if v is not None else "N/A"
            else:
                row_vals[t] = str(m.get(field, "N/A"))

        # Find best value for highlighting
        best_ticker = None
        if prefer == "green" and field:
            best_val = -999
            for t in tickers:
                m = metrics_by_ticker.get(t, {})
                v = m.get(field)
                if v is not None and v > best_val:
                    best_val = v
                    best_ticker = t

        rows.append({
            "label": label,
            "values": row_vals,
            "best_ticker": best_ticker,
        })

    # Claude synthesis
    summary_lines = []
    for t in tickers:
        m = metrics_by_ticker.get(t, {})
        name = m.get("name", t)
        summary_lines.append(
            f"{t} ({name}): price=${m.get('price', 0):.2f}, "
            f"P/E={m.get('pe_ratio', 'N/A')}, "
            f"rev_growth={m.get('rev_growth', 0):+.1f}%, "
            f"margin={m.get('net_margin', 0):.1f}%, "
            f"RSI={m.get('rsi', 50):.0f}, "
            f"analyst={m.get('analyst_rating', 'N/A')}"
        )

    persona_key = {
        "FUNDAMENTALS": "VALUE",
        "TECHNICALS": "MOMENTUM",
        "RISK": "RISK",
        "ALL": "VALUE",
    }.get(perspective.upper(), "VALUE")

    persona = get_persona(persona_key)
    user_msg = (
        f"Compare these {len(tickers)} securities from a {perspective.lower()} perspective:\n\n"
        + "\n".join(summary_lines)
        + "\n\nWhich is the best buy right now and why? Keep response to 3-4 sentences."
    )
    verdict = _call_claude(persona["system"], user_msg, max_tokens=300)

    return {
        "tickers": tickers,
        "perspective": perspective,
        "rows": rows,
        "metrics": [metrics_by_ticker.get(t, {}) for t in tickers],
        "verdict": verdict,
        "verdict_persona": persona["name"],
    }


# ─────────────────────────────────────────────────────────────
# Reddit / Social sentiment scanner
# ─────────────────────────────────────────────────────────────

# Known non-ticker words to filter out
_COMMON_WORDS = {
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN", "HER",
    "WAS", "ONE", "OUR", "OUT", "DAY", "GET", "HAS", "HIM", "HIS", "HOW",
    "ITS", "NEW", "NOW", "OLD", "SEE", "TWO", "WAY", "WHO", "BOY", "DID",
    "ITS", "LET", "PUT", "SAY", "SHE", "TOO", "USE", "FDA", "CEO", "IPO",
    "SEC", "FED", "GDP", "CPI", "PCE", "ETF", "SPX", "VIX", "DXY", "USD",
    "CAD", "EUR", "GBP", "YEN", "BTC", "ETH", "NFT", "DCA", "RSI", "ATH",
    "IMO", "TBH", "WSB", "LOL", "TLDR", "DD", "OTM", "ITM", "ATM", "IV",
    "YOLO", "FOMO", "HODL", "DYOR", "MOON", "BULL", "BEAR", "BUY", "SELL",
    "PUTS", "CALL", "CALLS", "PUT", "STOCK", "STOCKS", "SHARE", "SHARES",
    "MARKET", "TRADE", "TRADING", "INVEST", "HOLD", "LONG", "SHORT",
    "GAIN", "LOSS", "PROFIT", "CASH", "FUND", "DEBT", "RATE", "COST",
    "WEEK", "YEAR", "MONTH", "THAT", "THIS", "WITH", "FROM", "HAVE", "THEY",
    "WILL", "BEEN", "MORE", "ALSO", "INTO", "THAN", "THEN", "WHEN", "DOWN",
    "WHAT", "SOME", "WOULD", "MAKE", "LIKE", "JUST", "KNOW", "TAKE", "GOOD",
    "BEST", "HIGH", "WELL", "EVEN", "BACK", "ONLY", "COME", "VERY", "AFTER",
    "STILL", "FIRST", "MOST", "NEXT", "OVER", "THINK", "THERE", "ABOUT",
    "PRICE", "SAID", "AFTER", "WHERE", "BEING", "EVERY", "SINCE", "OVER",
    "MANY", "BOTH", "SAME", "DOES", "DONE", "MORE", "MUCH", "SUCH",
}

_TICKER_RE = re.compile(r'\b([A-Z]{1,5}(?:\.TO)?)\b')


def _extract_tickers_from_text(text: str) -> list:
    """Extract plausible stock tickers from text."""
    text_upper = text.upper()
    raw = _TICKER_RE.findall(text_upper)
    return [t for t in raw if t not in _COMMON_WORDS and 2 <= len(t.rstrip(".TO")) <= 5]


def scan_social_trending(max_items: int = 20) -> list:
    """
    Scrape Reddit top posts for trending tickers (no API key required).
    Returns top 20 tickers by mention count with sentiment scores.
    """
    try:
        import requests
        from bs4 import BeautifulSoup

        sia = SentimentIntensityAnalyzer()
        subreddits = [
            ("wallstreetbets", "r/wallstreetbets"),
            ("stocks", "r/stocks"),
            ("investing", "r/investing"),
        ]
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; InvestingBot/1.0; +https://github.com)"
        }

        mention_counter = Counter()
        sentiment_accum: dict = {}
        post_samples: dict = {}

        for sub_name, sub_label in subreddits:
            try:
                url = f"https://www.reddit.com/r/{sub_name}/hot.json?limit=75&t=day"
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                posts = data.get("data", {}).get("children", [])

                for post in posts:
                    post_data = post.get("data", {})
                    title = post_data.get("title", "")
                    score = post_data.get("score", 0)
                    url_str = "https://reddit.com" + post_data.get("permalink", "")

                    if not title or score < 10:
                        continue

                    tickers = _extract_tickers_from_text(title)
                    sent = sia.polarity_scores(title)["compound"]

                    # Weight mentions by post score (capped at 100 to prevent outliers)
                    weight = min(score, 100)
                    for t in tickers:
                        mention_counter[t] += weight
                        if t not in sentiment_accum:
                            sentiment_accum[t] = []
                        sentiment_accum[t].append(sent)
                        if t not in post_samples or score > post_samples[t].get("score", 0):
                            post_samples[t] = {
                                "title": title[:120],
                                "url": url_str,
                                "score": score,
                                "subreddit": sub_label,
                            }
            except Exception as e:
                log.warning("reddit scan %s: %s", sub_name, e)
                continue

        # Build results
        results = []
        for ticker, count in mention_counter.most_common(max_items):
            sentiments = sentiment_accum.get(ticker, [0])
            avg_sent = sum(sentiments) / len(sentiments)
            sample = post_samples.get(ticker, {})

            if avg_sent >= 0.1:
                sentiment_label = "bullish"
            elif avg_sent <= -0.1:
                sentiment_label = "bearish"
            else:
                sentiment_label = "neutral"

            results.append({
                "ticker": ticker,
                "mention_count": count,
                "sentiment_score": round(avg_sent, 3),
                "sentiment_label": sentiment_label,
                "sample_title": sample.get("title", ""),
                "sample_url": sample.get("url", ""),
                "subreddit": sample.get("subreddit", ""),
                "scanned_at": datetime.now(EASTERN).isoformat(),
            })

        log.info("social_trending: found %d trending tickers", len(results))
        return results

    except Exception as e:
        log.error("scan_social_trending error: %s", e)
        return []


# ─────────────────────────────────────────────────────────────
# News impact analyzer
# ─────────────────────────────────────────────────────────────

def analyze_news_impact(headline: str, holdings: list) -> dict:
    """
    Given a news headline and list of user's holdings,
    returns which holdings are affected and how.
    """
    if not holdings:
        return {"headline": headline, "affected": [], "summary": "No holdings to analyze."}

    tickers_str = ", ".join(h if isinstance(h, str) else h.get("ticker", "") for h in holdings)
    system = (
        "You are a financial news analyst. Given a news headline and a list of stock holdings, "
        "identify which holdings are directly or indirectly affected. "
        "For each affected holding, state: (1) direction (POSITIVE/NEGATIVE/NEUTRAL), "
        "(2) confidence (HIGH/MEDIUM/LOW), (3) one-sentence reason. "
        "If none are affected, say so. Format as JSON array: "
        '[{"ticker": "X", "direction": "POSITIVE", "confidence": "HIGH", "reason": "..."}]. '
        "Only include actually affected tickers. Max 3-4 sentences total summary."
    )
    user_msg = f"Headline: '{headline}'\n\nMy holdings: {tickers_str}\n\nAnalyze the impact."

    raw = _call_claude(system, user_msg, max_tokens=400)

    # Try to extract JSON from the response
    affected = []
    try:
        import json
        # Find JSON array in response
        m = re.search(r'\[.*?\]', raw, re.DOTALL)
        if m:
            affected = json.loads(m.group())
    except Exception:
        pass

    # Extract non-JSON summary
    summary = re.sub(r'\[.*?\]', '', raw, flags=re.DOTALL).strip()
    if not summary:
        summary = raw[:200]

    return {
        "headline": headline,
        "affected": affected,
        "summary": summary,
        "analyzed_at": datetime.now(EASTERN).isoformat(),
    }


# ─────────────────────────────────────────────────────────────
# Daily market brief generator
# ─────────────────────────────────────────────────────────────

def generate_market_brief() -> dict:
    """
    Generate a 5-sentence AI market brief.
    Cached for 5 minutes.
    """
    def _build():
        # Key indices
        indices = {
            "S&P 500": ("^GSPC", "spy"),
            "NASDAQ": ("^IXIC", "qqq"),
            "VIX": ("^VIX", "vix"),
            "TSX": ("^GSPTSE", "tsx"),
            "DXY": ("DX-Y.NYB", "dxy"),
        }
        commodities = {
            "Gold": "GLD",
            "Oil": "USO",
        }

        key_metrics = {}
        for name, (sym, _) in indices.items():
            price = _safe_price(sym)
            change = _safe_change_pct(sym)
            key_metrics[name] = {"price": price, "change_pct": change}

        for name, sym in commodities.items():
            price = _safe_price(sym)
            change = _safe_change_pct(sym)
            key_metrics[name] = {"price": price, "change_pct": change}

        # Top gainers & losers from a representative large-cap universe
        universe = [
            "AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META", "TSLA", "AVGO", "BRK-B",
            "JPM", "V", "MA", "UNH", "JNJ", "HD", "PG", "XOM", "CVX", "BAC",
            "AMD", "PLTR", "SHOP", "SOFI", "COIN", "MSTR",
        ]

        changes = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_safe_change_pct, sym): sym for sym in universe[:20]}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    changes[sym] = fut.result()
                except Exception:
                    changes[sym] = 0.0

        sorted_changes = sorted(changes.items(), key=lambda x: x[1], reverse=True)
        top_gainers = sorted_changes[:5]
        top_losers = sorted_changes[-5:]

        # Sector performance
        sector_etfs = {
            "Technology": "XLK",
            "Finance": "XLF",
            "Energy": "XLE",
            "Healthcare": "XLV",
            "Consumer Disc.": "XLY",
        }
        sector_changes = {}
        for sector_name, sym in sector_etfs.items():
            sector_changes[sector_name] = _safe_change_pct(sym)

        # Build Claude prompt
        spy_chg = key_metrics.get("S&P 500", {}).get("change_pct", 0)
        nasdaq_chg = key_metrics.get("NASDAQ", {}).get("change_pct", 0)
        vix_price = key_metrics.get("VIX", {}).get("price", 20)
        tsx_chg = key_metrics.get("TSX", {}).get("change_pct", 0)
        dxy_chg = key_metrics.get("DXY", {}).get("change_pct", 0)
        gold_chg = key_metrics.get("Gold", {}).get("change_pct", 0)
        oil_chg = key_metrics.get("Oil", {}).get("change_pct", 0)

        gainers_str = ", ".join(f"{s} {c:+.1f}%" for s, c in top_gainers)
        losers_str = ", ".join(f"{s} {c:+.1f}%" for s, c in top_losers)
        sector_str = ", ".join(f"{k} {v:+.1f}%" for k, v in sector_changes.items())

        prompt = (
            f"Today's market snapshot (write a 5-sentence market brief):\n\n"
            f"S&P 500: {spy_chg:+.2f}% | NASDAQ: {nasdaq_chg:+.2f}% | TSX: {tsx_chg:+.2f}%\n"
            f"VIX: {vix_price:.1f} | DXY: {dxy_chg:+.2f}% | Gold: {gold_chg:+.2f}% | Oil: {oil_chg:+.2f}%\n"
            f"Top gainers: {gainers_str}\n"
            f"Top losers: {losers_str}\n"
            f"Sector performance: {sector_str}\n\n"
            "Write a 5-sentence market brief for a Canadian TFSA investor. "
            "Sentence 1: Overall market tone. "
            "Sentence 2: Standout movers. "
            "Sentence 3: Sector rotation. "
            "Sentence 4: Macro signal (VIX/USD/commodities). "
            "Sentence 5: Key watchout for the session. "
            "Be specific and analytical. No fluff. No disclaimer needed."
        )

        brief_text = _call_claude(
            "You are a concise market analyst writing a daily brief for sophisticated investors.",
            prompt,
            max_tokens=350,
        )

        return {
            "brief_text": brief_text,
            "key_metrics": key_metrics,
            "top_gainers": [{"ticker": s, "change_pct": c} for s, c in top_gainers],
            "top_losers": [{"ticker": s, "change_pct": c} for s, c in top_losers],
            "sector_changes": sector_changes,
            "generated_at": datetime.now(EASTERN).isoformat(),
        }

    return _cached("market_brief", _build)


# ─────────────────────────────────────────────────────────────
# Sector heatmap
# ─────────────────────────────────────────────────────────────

SECTOR_ETFS = [
    ("Technology", "XLK", "NVDA"),
    ("Financials", "XLF", "JPM"),
    ("Healthcare", "XLV", "UNH"),
    ("Energy", "XLE", "XOM"),
    ("Consumer Disc.", "XLY", "TSLA"),
    ("Industrials", "XLI", "CAT"),
    ("Materials", "XLB", "LIN"),
    ("Utilities", "XLU", "NEE"),
    ("Real Estate", "XLRE", "AMT"),
    ("Consumer Staples", "XLP", "PG"),
    ("Communication", "XLC", "GOOG"),
]


def get_sector_heatmap() -> list:
    """Returns all 11 S&P 500 sectors with daily % change and top stock."""
    def _build():
        results = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {
                pool.submit(_safe_change_pct, etf): (name, etf, top_stock)
                for name, etf, top_stock in SECTOR_ETFS
            }
            for fut in as_completed(futures):
                name, etf, top_stock = futures[fut]
                try:
                    pct = fut.result()
                except Exception:
                    pct = 0.0
                # Get top stock change
                try:
                    top_pct = _safe_change_pct(top_stock)
                except Exception:
                    top_pct = 0.0
                results.append({
                    "sector_name": name,
                    "etf": etf,
                    "change_pct": pct,
                    "top_stock": top_stock,
                    "top_stock_change": top_pct,
                })

        results.sort(key=lambda x: x["change_pct"], reverse=True)
        return results

    return _cached("sector_heatmap", _build)


# ─────────────────────────────────────────────────────────────
# Persona chat (single-turn with context)
# ─────────────────────────────────────────────────────────────

def persona_chat(message: str, persona_key: str, ticker: Optional[str] = None,
                 history: Optional[list] = None) -> str:
    """
    Send a message to the specified AI persona.
    history: list of {"role": "user"|"assistant", "content": str}
    """
    persona = get_persona(persona_key)
    system = persona["system"]

    # Build message list
    messages = []
    if history:
        for h in history[-6:]:  # last 3 turns for context
            messages.append({"role": h["role"], "content": h["content"]})

    # Add ticker context if provided
    if ticker:
        ticker_data = _fetch_ticker_metrics(ticker.upper())
        ticker_ctx = (
            f"\n\n[Live data for {ticker}: price=${ticker_data.get('price', 'N/A')}, "
            f"day_chg={ticker_data.get('change_pct', 0):+.2f}%, "
            f"P/E={ticker_data.get('pe_ratio', 'N/A')}, "
            f"RSI={ticker_data.get('rsi', 'N/A')}, "
            f"analyst={ticker_data.get('analyst_rating', 'N/A')}]"
        )
        messages.append({"role": "user", "content": message + ticker_ctx})
    else:
        messages.append({"role": "user", "content": message})

    try:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return "⚠️ ANTHROPIC_API_KEY not configured."
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system=system,
            messages=messages,
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.warning("persona_chat error: %s", e)
        return f"⚠️ Response unavailable: {str(e)[:80]}"
