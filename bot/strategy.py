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

def _research_ticker(ticker: str, data: dict) -> str:
    prompt = (
        f"Stock: {ticker}\n"
        f"Price: ${data['price']} | RSI: {data['rsi']} | MACD: {data['macd_dir']} "
        f"| 1M: {data['pct_1m']:+.1f}% | 3M: {data['pct_3m']:+.1f}%\n"
        f"MA50: {data['ma50']} | MA200: {data['ma200']}\n\n"
        "Write a 3-sentence hedge fund analysis: (1) technical setup, "
        "(2) momentum trajectory, (3) key catalyst or risk in next 30 days."
    )
    try:
        r = client.messages.create(
            model=MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return r.content[0].text.strip()
    except Exception:
        return ""


def _predict_ticker(ticker: str, data: dict, research: str):
    prompt = (
        f"Stock: {ticker}\n"
        f"Research: {research}\n"
        f"Price: ${data['price']} | RSI: {data['rsi']} | MACD: {data['macd_dir']} "
        f"| 1M: {data['pct_1m']:+.1f}%\n\n"
        "Return ONLY valid JSON:\n"
        '{"action":"BUY|SKIP","score":1-10,"target":float,"stop":float,'
        '"strategy":"SWING|MEDIUM|LONG","reasoning":"one sentence"}'
    )
    try:
        r = client.messages.create(
            model=MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        text = r.content[0].text.strip()
        start = text.find("{")
        end   = text.rfind("}") + 1
        return json.loads(text[start:end])
    except Exception:
        return None


def rank_buy_opportunities(budget: float, existing_tickers: list[str]) -> list[dict]:
    """
    Scan watchlist, run Claude analysis, return top ranked BUY candidates
    that fit within budget and diversify away from existing holdings.
    """
    regime, spy_price, ma50, ma200 = get_market_regime()
    regime_emoji = {"BULL": "🟢", "BEAR": "🔴", "SIDEWAYS": "🟡"}.get(regime, "⚪")
    print(f"  {regime_emoji} Market: {regime} | SPY ${spy_price}")

    candidates = []

    for ticker in WATCHLIST:
        if ticker in existing_tickers:
            continue  # already hold it

        print(f"  Scanning {ticker}…")
        data = get_ticker_data(ticker)
        if data is None:
            continue

        # Quick filter — only scan tickers with reasonable momentum
        if data["rsi"] < 30 or data["rsi"] > 85:
            continue
        if data["pct_1m"] < -15:
            continue

        research = _research_ticker(ticker, data)
        pred = _predict_ticker(ticker, data, research)
        if pred is None or pred.get("action") != "BUY":
            continue

        score = pred.get("score", 0)
        if score < 7:
            continue

        target = pred.get("target", data["price"] * 1.1)
        stop   = pred.get("stop", data["price"] * 0.92)
        upside = round((target / data["price"] - 1) * 100, 1)

        # Shares that fit in budget (full position)
        shares = int(budget / data["price"])
        if shares < 1:
            shares = 1
        cost = round(shares * data["price"], 2)

        # Confidence label
        if score >= 9:
            confidence = "🟢 High"
        elif score >= 7:
            confidence = "🟡 Medium"
        else:
            confidence = "⚪ Low"

        candidates.append({
            "ticker":     ticker,
            "price":      data["price"],
            "score":      score,
            "strategy":   pred.get("strategy", "MEDIUM"),
            "target":     target,
            "stop":       stop,
            "upside":     upside,
            "shares":     shares,
            "cost":       cost,
            "reasoning":  pred.get("reasoning", research[:120]),
            "confidence": confidence,
            "regime":     regime,
        })

    # Sort by score descending
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:5]
