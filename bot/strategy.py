"""
Strategy engine: signal scoring, sell-signal detection, and trade recommendations.
Uses Claude for deep research + prediction on each candidate.
"""
import os
import json
import anthropic
from market_data import get_ticker_data, get_market_regime

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL  = "claude-sonnet-4-6"

# ──────────────────────────────────────────────
# Watchlist — TFSA-friendly (Canadian ETFs first)
# ──────────────────────────────────────────────
WATCHLIST = [
    # Canadian ETFs (no US withholding tax in TFSA)
    "VFV.TO",   # S&P 500 in CAD
    "XIU.TO",   # TSX 60
    "XQQ.TO",   # NASDAQ in CAD
    "XEQT.TO",  # All-equity global
    "VEQT.TO",  # Vanguard all-equity
    "ZQQ.TO",   # BMO NASDAQ
    "HXS.TO",   # S&P 500 swap (tax-efficient)
    # Canadian stocks
    "SHOP.TO",
    "RY.TO",
    "TD.TO",
    "ENB.TO",
    "CNQ.TO",
    # US growth stocks
    "NVDA",
    "MSFT",
    "AAPL",
    "AMZN",
    "META",
    "GOOG",
    "AMD",
    "PLTR",
    "TSM",
    # US ETFs
    "QQQ",
    "SPY",
]

CRYPTO_PROXIES = set()  # No crypto proxies in TFSA build


# ──────────────────────────────────────────────
# Sell signal detection
# ──────────────────────────────────────────────

def get_sell_signals(ticker: str, avg_cost: float) -> tuple[list[str], str]:
    """
    Returns (signals_list, urgency).
    urgency: 'URGENT' | 'WARNING' | 'FYI' | None
    """
    data = get_ticker_data(ticker)
    if data is None:
        return [], None

    signals = []

    if data["rsi_rolling_over"]:
        signals.append(f"RSI {data['rsi']} rolling over (overbought)")

    if data["bearish_cross"]:
        signals.append("MACD bearish crossover confirmed")

    if data["below_ma50"]:
        signals.append(f"Price broke below 50-day MA (${data['ma50']})")

    if data["below_ma200"]:
        signals.append(f"Price broke below 200-day MA (${data['ma200']})")

    if data["vol_spike_drop"]:
        signals.append(f"Heavy sell volume ({data['vol_ratio']}x avg) on -{abs(data['pct_1d'])}% day")

    if data["pct_1d"] <= -3.0:
        signals.append(f"Single-day drop of {data['pct_1d']}%")

    if data["earnings_soon"]:
        signals.append("Earnings within 7 days — elevated risk")

    count = len(signals)
    if count >= 2:
        urgency = "URGENT"
    elif count == 1:
        urgency = "WARNING"
    else:
        urgency = None

    return signals, urgency, data


# ──────────────────────────────────────────────
# Buy opportunity ranking
# ──────────────────────────────────────────────

def rank_buy_opportunities(budget: float, existing_tickers: list[str]) -> list[dict]:
    """
    Scan watchlist, run a single Claude batch analysis, return top 3 BUY candidates.
    Falls back to momentum ranking if Claude is unavailable.
    """
    regime, spy_price, ma50, ma200 = get_market_regime()
    regime_emoji = {"BULL": "🟢", "BEAR": "🔴", "SIDEWAYS": "🟡"}.get(regime, "⚪")
    print(f"  {regime_emoji} Market: {regime} | SPY ${spy_price}")

    # Gather market data for all watchlist tickers not already held
    raw_candidates = []
    for ticker in WATCHLIST:
        if ticker in existing_tickers:
            continue
        print(f"  Scanning {ticker}…")
        data = get_ticker_data(ticker)
        if data is None:
            continue
        # Skip only catastrophic downtrends
        if data["pct_1m"] < -25:
            continue
        raw_candidates.append({"ticker": ticker, "data": data})

    if not raw_candidates:
        return []

    # Pre-rank by momentum score to give Claude the best candidates
    def _momentum(c):
        d = c["data"]
        rsi_bonus  = 1.0 if 40 <= d["rsi"] <= 70 else 0.3
        macd_bonus = 1.5 if d["macd_dir"] == "▲" else 0.0
        above_ma50 = 1.0 if not d["below_ma50"] else 0.0
        mom = max(0.0, d["pct_1m"])
        return mom * rsi_bonus + macd_bonus + above_ma50

    raw_candidates.sort(key=_momentum, reverse=True)
    top = raw_candidates[:8]

    # Single Claude call — analyze all top candidates at once
    lines = []
    for i, c in enumerate(top, 1):
        d = c["data"]
        lines.append(
            f"{i}. {c['ticker']}: ${d['price']}, RSI={d['rsi']}, MACD={d['macd_dir']}, "
            f"1M={d['pct_1m']:+.1f}%, 3M={d['pct_3m']:+.1f}%, "
            f"MA50={'above' if not d['below_ma50'] else 'below'}, "
            f"MA200={'above' if not d['below_ma200'] else 'below'}"
        )

    prompt = (
        f"Market: {regime} | SPY ${spy_price} | Budget: ${budget:.0f}\n"
        f"Portfolio type: Canadian TFSA\n\n"
        "Candidates:\n" + "\n".join(lines) + "\n\n"
        "Pick the 3 BEST buy opportunities. For each provide:\n"
        "- Realistic price target (+8-15%), stop loss (-7%), "
        "strategy (SWING/MEDIUM/LONG), one-sentence reasoning, confidence (HIGH/MEDIUM/LOW)\n\n"
        "Return ONLY a valid JSON array — no extra text:\n"
        '[{"rank":1,"ticker":"X","target":0.0,"stop":0.0,'
        '"strategy":"MEDIUM","reasoning":"...","confidence":"HIGH"}]'
    )

    picks = []
    try:
        r = client.messages.create(
            model=MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = r.content[0].text.strip()
        start = text.find("[")
        end   = text.rfind("]") + 1
        if start != -1 and end > start:
            picks = json.loads(text[start:end])
    except Exception as e:
        print(f"  ⚠️  Claude error: {e} — using momentum fallback")

    # Fallback: use top 3 by momentum if Claude failed or returned nothing
    if not picks:
        for i, c in enumerate(top[:3], 1):
            d = c["data"]
            picks.append({
                "rank":       i,
                "ticker":     c["ticker"],
                "target":     round(d["price"] * 1.10, 2),
                "stop":       round(d["price"] * 0.93, 2),
                "strategy":   "MEDIUM",
                "reasoning":  f"Top momentum: {d['pct_1m']:+.1f}% in 1M, RSI {d['rsi']}, MACD {d['macd_dir']}",
                "confidence": "MEDIUM",
            })

    # Build result objects
    ticker_map = {c["ticker"]: c for c in top}
    results = []
    for pick in picks[:3]:
        ticker = pick.get("ticker", "")
        c = ticker_map.get(ticker)
        if c is None:
            continue
        d      = c["data"]
        price  = d["price"]
        target = float(pick.get("target") or price * 1.10)
        stop   = float(pick.get("stop")   or price * 0.93)
        upside = round((target / price - 1) * 100, 1)
        shares = max(1, int(budget / price))
        cost   = round(shares * price, 2)
        conf   = pick.get("confidence", "MEDIUM").upper()
        confidence_label = {
            "HIGH":   "🟢 High",
            "MEDIUM": "🟡 Medium",
            "LOW":    "⚪ Low",
        }.get(conf, "🟡 Medium")

        results.append({
            "ticker":     ticker,
            "price":      price,
            "score":      9 if conf == "HIGH" else 7,
            "strategy":   pick.get("strategy", "MEDIUM"),
            "target":     target,
            "stop":       stop,
            "upside":     upside,
            "shares":     shares,
            "cost":       cost,
            "reasoning":  pick.get("reasoning", "")[:120],
            "confidence": confidence_label,
            "regime":     regime,
        })

    return results
