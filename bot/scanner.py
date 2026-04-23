"""
Proactive stock discovery scanner.
Runs every 30 minutes — fetches StockTwits trending tickers, scores each on
sentiment + momentum + news, and sends a WhatsApp alert for score >= 7 not
already alerted in the last 24 hours.
"""
import os
import logging
import requests
from datetime import datetime, timedelta

import pytz
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from database import get_connection
from alerts import send_sms
from market_data import get_ticker_data

log = logging.getLogger(__name__)

EASTERN      = pytz.timezone("America/Toronto")
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")

STOCKTWITS_TRENDING = "https://api.stocktwits.com/api/2/trending/symbols.json"
STOCKTWITS_STREAM   = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"

ALERT_THRESHOLD = 7
MAX_TICKERS     = 15   # top N from trending list

_vader = SentimentIntensityAnalyzer()


# ──────────────────────────────────────────────
# Dedup guard
# ──────────────────────────────────────────────

def _already_alerted(ticker: str) -> bool:
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM scanner_alerts WHERE ticker = ? AND sent_at >= ?",
        (ticker, cutoff),
    ).fetchone()
    conn.close()
    return row is not None


def _record_alert(ticker: str, score: float, reason: str):
    conn = get_connection()
    conn.execute(
        "INSERT INTO scanner_alerts (ticker, score, sent_at, reason) VALUES (?,?,?,?)",
        (ticker, score, datetime.now().isoformat(), reason[:500]),
    )
    conn.commit()
    conn.close()


# ──────────────────────────────────────────────
# Data fetchers
# ──────────────────────────────────────────────

def _fetch_trending_tickers() -> list[str]:
    try:
        r = requests.get(STOCKTWITS_TRENDING, timeout=10)
        r.raise_for_status()
        symbols = r.json().get("symbols", [])
        return [s["symbol"] for s in symbols[:MAX_TICKERS]]
    except Exception as e:
        log.error("StockTwits trending fetch failed: %s", e)
        return []


def _fetch_stocktwits_sentiment(ticker: str) -> float | None:
    """Returns average VADER compound score across recent messages, or None."""
    try:
        url = STOCKTWITS_STREAM.format(ticker=ticker)
        r = requests.get(url, timeout=10)
        if r.status_code == 429:
            log.warning("StockTwits rate-limited for %s", ticker)
            return None
        if not r.ok:
            return None
        messages = [m["body"] for m in r.json().get("messages", []) if m.get("body")]
        if not messages:
            return None
        scores = [_vader.polarity_scores(m)["compound"] for m in messages]
        return sum(scores) / len(scores)
    except Exception as e:
        log.warning("StockTwits stream failed for %s: %s", ticker, e)
        return None


def _fetch_news_sentiment(ticker: str) -> float | None:
    """Returns average VADER compound score across recent headlines, or None."""
    if not NEWS_API_KEY:
        return None
    try:
        r = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q":        ticker,
                "apiKey":   NEWS_API_KEY,
                "pageSize": 5,
                "sortBy":   "publishedAt",
                "language": "en",
            },
            timeout=10,
        )
        r.raise_for_status()
        texts = [
            f"{a.get('title', '')} {a.get('description', '')}".strip()
            for a in r.json().get("articles", [])
            if a.get("title")
        ]
        if not texts:
            return None
        scores = [_vader.polarity_scores(t)["compound"] for t in texts]
        return sum(scores) / len(scores)
    except Exception as e:
        log.warning("NewsAPI failed for %s: %s", ticker, e)
        return None


# ──────────────────────────────────────────────
# Scoring  (max 10 pts)
#   StockTwits sentiment   0–3
#   RSI momentum           0–2
#   MACD                   0–2
#   News sentiment         0–2
#   1-day price action     0–1
# ──────────────────────────────────────────────

def _score_ticker(ticker: str) -> tuple[int, str, float] | None:
    """Returns (score, reason, price) or None if data unavailable."""
    score   = 0
    reasons = []

    # ── StockTwits sentiment ─────────────────────
    st_compound = _fetch_stocktwits_sentiment(ticker)
    if st_compound is not None:
        if st_compound >= 0.5:
            score += 3
            reasons.append(f"strong bullish chatter ({st_compound:+.2f})")
        elif st_compound >= 0.2:
            score += 2
            reasons.append(f"bullish chatter ({st_compound:+.2f})")
        elif st_compound >= 0.05:
            score += 1
            reasons.append(f"slightly bullish chatter ({st_compound:+.2f})")

    # ── Price momentum via market_data ───────────
    data = get_ticker_data(ticker)
    if data is None:
        log.info("Scanner: no price data for %s — skip", ticker)
        return None

    rsi = data["rsi"]
    if 50 <= rsi <= 68:
        score += 2
        reasons.append(f"RSI {rsi} bullish momentum")
    elif 40 <= rsi < 50:
        score += 1
        reasons.append(f"RSI {rsi} recovering")
    elif rsi < 35:
        score += 1
        reasons.append(f"RSI {rsi} oversold")

    # ── MACD ─────────────────────────────────────
    if data["macd_hist"] > 0:
        score += 2
        reasons.append("MACD bullish")

    # ── News sentiment ───────────────────────────
    news_compound = _fetch_news_sentiment(ticker)
    if news_compound is not None:
        if news_compound >= 0.3:
            score += 2
            reasons.append("positive news")
        elif news_compound >= 0.1:
            score += 1
            reasons.append("neutral-positive news")

    # ── 1-day price action ───────────────────────
    if data["pct_1d"] >= 1.0:
        score += 1
        reasons.append(f"+{data['pct_1d']}% today")

    score  = min(score, 10)
    reason = ", ".join(reasons) if reasons else "mixed signals"
    return score, reason, data["price"]


# ──────────────────────────────────────────────
# Main job (called by scheduler)
# ──────────────────────────────────────────────

def run_scanner():
    log.info("📡 Scanner running @ %s", datetime.now(EASTERN).strftime("%H:%M"))

    tickers = _fetch_trending_tickers()
    if not tickers:
        log.warning("Scanner: no trending tickers returned")
        return

    log.info("Scanner: checking %d tickers: %s", len(tickers), tickers)

    for ticker in tickers:
        if _already_alerted(ticker):
            log.info("Scanner: %s already alerted in last 24h — skipping", ticker)
            continue

        try:
            result = _score_ticker(ticker)
        except Exception:
            log.exception("Scanner: unhandled error scoring %s", ticker)
            continue

        if result is None:
            continue

        score, reason, price = result
        log.info("Scanner: %s score=%d reason=%s", ticker, score, reason)

        if score >= ALERT_THRESHOLD:
            msg = (
                f"📈 OPPORTUNITY: ${ticker} — Score {score}/10\n"
                f"{reason}\n"
                f"Price: ${price:.2f}"
            )
            if send_sms(msg):
                _record_alert(ticker, score, reason)
                log.info("Scanner alert sent for %s (score %d)", ticker, score)
