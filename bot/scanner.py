"""
Proactive stock discovery scanner.
Runs every 30 minutes — discovers stocks via four methods:
  1. StockTwits trending (existing)
  2. SEC EDGAR 8-K keyword scan (new)
  3. Small-cap low-float momentum (new)
  4. NewsAPI catalyst keyword scan (new)
Scores each on sentiment + momentum + news, sends WhatsApp alert if score >= 7
and not already alerted in the last 24 hours.
"""
import os
import re
import time
import logging
import requests
from datetime import datetime, timedelta, date

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
SEC_EFTS_URL        = "https://efts.sec.gov/LATEST/search-index"
SEC_TICKERS_URL     = "https://www.sec.gov/files/company_tickers.json"
SEC_USER_AGENT      = "investing-bot karim.bishara.kb@gmail.com"

ALERT_THRESHOLD = 7
MAX_TICKERS     = 15   # top N from StockTwits trending list

# ── Small-cap watchlist for low-float momentum scan ──────────────────────────
SMALL_CAP_WATCHLIST = [
    # Biotech / pharma — catalyst-driven
    "NKTR", "SAGE", "AKBA", "RXRX", "CRSP", "EDIT", "BEAM", "NTLA",
    "FATE", "HALO", "PRCT", "NUVL", "JANX", "BLUE", "CERT", "INSP",
    "ACAD", "SAVA", "ARQT", "FGEN",
    # Quantum / AI / tech
    "IONQ", "RGTI", "QBTS", "QUBT", "SOUN", "BBAI",
    # Clean energy / EV charging
    "CHPT", "BLNK", "EVGO", "PLUG", "BE", "NOVA", "CLSK", "ARRY", "AMRC",
    # Fintech / consumer
    "HOOD", "LMND", "OPEN", "UPST", "GDRX", "HIMS", "AFRM",
    # Crypto mining
    "MARA", "RIOT", "CORZ", "HUT", "BTBT", "WULF",
    # Space / aviation
    "ACHR", "JOBY", "SPCE",
]

# ── SEC EDGAR catalyst keywords ───────────────────────────────────────────────
SEC_KEYWORDS = [
    "contract awarded",
    "FDA approval",
    "government contract",
    "partnership",
    "exclusive license",
]

# ── NewsAPI catalyst search terms ─────────────────────────────────────────────
NEWS_CATALYST_TERMS = [
    "FDA approval",
    "government contract",
    "partnership announced",
    "exclusive deal",
]

# Common English words / acronyms that look like tickers — filtered out
_TICKER_STOPWORDS = {
    "CEO", "CFO", "COO", "CTO", "IPO", "ETF", "FDA", "SEC", "WHO",
    "CDC", "AI", "US", "UK", "EU", "UN", "IT", "AT", "BE", "TO",
    "OR", "IN", "ON", "AS", "BY", "OF", "AN", "IS", "AM", "PM",
}

_vader = SentimentIntensityAnalyzer()

# Module-level SEC ticker map cache
_sec_ticker_map: dict[str, str] = {}   # normalised entity name → ticker
_sec_cache_ts: float = 0.0
_SEC_CACHE_TTL = 6 * 3600              # refresh every 6 hours


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
# StockTwits helpers (existing)
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
    score   = 0
    reasons = []

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

    if data["macd_hist"] > 0:
        score += 2
        reasons.append("MACD bullish")

    news_compound = _fetch_news_sentiment(ticker)
    if news_compound is not None:
        if news_compound >= 0.3:
            score += 2
            reasons.append("positive news")
        elif news_compound >= 0.1:
            score += 1
            reasons.append("neutral-positive news")

    if data["pct_1d"] >= 1.0:
        score += 1
        reasons.append(f"+{data['pct_1d']}% today")

    score  = min(score, 10)
    reason = ", ".join(reasons) if reasons else "mixed signals"
    return score, reason, data["price"]


# ──────────────────────────────────────────────
# SEC EDGAR scanner
# ──────────────────────────────────────────────

def _normalise_entity(name: str) -> str:
    """Strip common legal suffixes for fuzzy company-name matching."""
    name = name.upper().strip()
    name = re.sub(
        r"\b(INC\.?|CORP\.?|LLC\.?|LTD\.?|CO\.?|GROUP|HOLDINGS?|PLC|NV|SA|LP|L\.P\.)\b",
        "",
        name,
    )
    return re.sub(r"\s+", " ", name).strip()


def _load_sec_ticker_map() -> dict[str, str]:
    """Return {normalised_entity_name: ticker}, cached for 6 h."""
    global _sec_ticker_map, _sec_cache_ts
    if _sec_ticker_map and (time.time() - _sec_cache_ts) < _SEC_CACHE_TTL:
        return _sec_ticker_map

    try:
        r = requests.get(
            SEC_TICKERS_URL,
            headers={"User-Agent": SEC_USER_AGENT},
            timeout=15,
        )
        r.raise_for_status()
        raw = r.json()
        mapping: dict[str, str] = {}
        for entry in raw.values():
            title  = entry.get("title", "")
            ticker = entry.get("ticker", "").upper().strip()
            if title and ticker:
                mapping[_normalise_entity(title)] = ticker
                mapping[title.upper().strip()] = ticker   # also keep full name
        _sec_ticker_map = mapping
        _sec_cache_ts   = time.time()
        log.info("SEC ticker map loaded: %d entries", len(mapping))
    except Exception as e:
        log.warning("SEC ticker map fetch failed: %s", e)

    return _sec_ticker_map


def _fetch_sec_edgar_tickers() -> list[tuple[str, str]]:
    """
    Scan today's 8-K filings on SEC EDGAR for catalyst keywords.
    Returns [(ticker, discovery_reason)].
    """
    today     = date.today().isoformat()
    ticker_map = _load_sec_ticker_map()
    if not ticker_map:
        return []

    found: dict[str, str] = {}   # ticker → reason

    for keyword in SEC_KEYWORDS:
        try:
            r = requests.get(
                SEC_EFTS_URL,
                params={
                    "q":         f'"{keyword}"',
                    "dateRange": "custom",
                    "startdt":   today,
                    "enddt":     today,
                    "forms":     "8-K",
                },
                headers={"User-Agent": SEC_USER_AGENT},
                timeout=15,
            )
            r.raise_for_status()
            hits = r.json().get("hits", {}).get("hits", [])

            for hit in hits[:10]:
                entity = hit.get("_source", {}).get("entity_name", "").upper().strip()
                if not entity:
                    continue

                ticker = ticker_map.get(entity) or ticker_map.get(_normalise_entity(entity))
                if ticker and ticker not in found:
                    found[ticker] = f"SEC 8-K: {keyword}"
                    log.info("SEC EDGAR hit: %s → %s (%s)", entity, ticker, keyword)

        except Exception as e:
            log.warning("SEC EDGAR fetch failed for %r: %s", keyword, e)

    return list(found.items())


# ──────────────────────────────────────────────
# Low-float momentum scanner
# ──────────────────────────────────────────────

def _scan_low_float_momentum() -> list[tuple[str, str]]:
    """
    Check SMALL_CAP_WATCHLIST for unusual volume (≥3× 20-day avg) combined
    with positive price action today.  Returns [(ticker, reason)].
    """
    results: list[tuple[str, str]] = []
    for ticker in SMALL_CAP_WATCHLIST:
        try:
            data = get_ticker_data(ticker)
            if data is None:
                continue
            vol_ratio = data.get("vol_ratio", 1.0)
            pct       = data.get("pct_1d", 0.0)
            if vol_ratio >= 3.0 and pct > 0:
                reason = f"vol {vol_ratio:.1f}× avg, +{pct:.1f}% today"
                results.append((ticker, reason))
                log.info("Low-float momentum: %s — %s", ticker, reason)
        except Exception as e:
            log.warning("Low-float scan error for %s: %s", ticker, e)
    return results


# ──────────────────────────────────────────────
# News catalyst scanner
# ──────────────────────────────────────────────

_TICKER_PATTERNS = [
    re.compile(r"\((?:NASDAQ|NYSE|AMEX|OTC):\s*([A-Z]{1,6})\)"),
    re.compile(r"\$([A-Z]{2,6})\b"),
    re.compile(r"\b([A-Z]{2,5})\s*\((?:NASDAQ|NYSE|AMEX|OTC)\)"),
]


def _extract_tickers(text: str) -> list[str]:
    tickers: set[str] = set()
    for pattern in _TICKER_PATTERNS:
        tickers.update(pattern.findall(text))
    return [t for t in tickers if t not in _TICKER_STOPWORDS]


def _scan_news_catalysts() -> list[tuple[str, str]]:
    """
    Search NewsAPI for catalyst keywords in the last 24 h and extract
    ticker symbols from headlines / descriptions.
    Returns [(ticker, reason)].
    """
    if not NEWS_API_KEY:
        return []

    since = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    found: dict[str, str] = {}

    for term in NEWS_CATALYST_TERMS:
        try:
            r = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q":        term,
                    "apiKey":   NEWS_API_KEY,
                    "pageSize": 10,
                    "sortBy":   "publishedAt",
                    "language": "en",
                    "from":     since,
                },
                timeout=10,
            )
            r.raise_for_status()
            for article in r.json().get("articles", []):
                text    = f"{article.get('title', '')} {article.get('description', '')}"
                tickers = _extract_tickers(text)
                for t in tickers:
                    if t not in found:
                        found[t] = f"News catalyst: {term}"
                        log.info("News catalyst: %s — %s", t, term)
        except Exception as e:
            log.warning("News catalyst scan failed for %r: %s", term, e)

    return list(found.items())


# ──────────────────────────────────────────────
# Alert formatter
# ──────────────────────────────────────────────

def _send_alert(ticker: str, score: int, price: float, discovery: str, score_reason: str):
    prefix  = "🔍 HIDDEN GEM ALERT" if "StockTwits" not in discovery else "📈 OPPORTUNITY"
    msg = (
        f"{prefix}: ${ticker} — Score {score}/10\n"
        f"Found via: {discovery}\n"
        f"{score_reason}\n"
        f"Price: ${price:.2f}"
    )
    if send_sms(msg):
        _record_alert(ticker, score, f"{discovery} | {score_reason}")
        log.info("Alert sent for %s (score %d via %s)", ticker, score, discovery)


# ──────────────────────────────────────────────
# Main job (called by scheduler)
# ──────────────────────────────────────────────

def run_scanner():
    log.info("📡 Scanner running @ %s", datetime.now(EASTERN).strftime("%H:%M"))

    # ── 1. Collect candidates from all sources ──────────────────────────
    candidates: dict[str, str] = {}   # ticker → discovery_source

    # StockTwits
    for t in _fetch_trending_tickers():
        candidates.setdefault(t, "StockTwits trending")

    # SEC EDGAR
    for t, reason in _fetch_sec_edgar_tickers():
        candidates.setdefault(t, reason)

    # Low-float momentum
    for t, reason in _scan_low_float_momentum():
        candidates.setdefault(t, reason)

    # News catalysts
    for t, reason in _scan_news_catalysts():
        candidates.setdefault(t, reason)

    if not candidates:
        log.warning("Scanner: no candidates found across all sources")
        return

    log.info("Scanner: %d unique candidates to score", len(candidates))

    # ── 2. Score and alert ──────────────────────────────────────────────
    for ticker, discovery in candidates.items():
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

        score, score_reason, price = result
        log.info("Scanner: %s score=%d source=%r reason=%s", ticker, score, discovery, score_reason)

        if score >= ALERT_THRESHOLD:
            _send_alert(ticker, score, price, discovery, score_reason)
