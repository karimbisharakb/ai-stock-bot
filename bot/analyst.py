"""
Stock analysis: real-time metrics from yfinance + qualitative analysis from Claude.
No web_search tool — avoids API compatibility issues while still giving live numbers.
"""
import os
import logging
import traceback
import anthropic
import yfinance as yf

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"


# ──────────────────────────────────────────────
# yfinance metrics
# ──────────────────────────────────────────────

def _fetch_metrics(ticker: str) -> dict:
    """Pull key fundamentals from yfinance. Returns dict with all fields."""
    try:
        info = yf.Ticker(ticker).info
    except Exception as e:
        log.warning("yfinance info failed for %s: %s", ticker, e)
        info = {}

    def _pct(val) -> str:
        if val is None:
            return "N/A"
        try:
            return f"{float(val) * 100:.1f}%"
        except Exception:
            return "N/A"

    def _round(val, digits=1) -> str:
        if val is None:
            return "N/A"
        try:
            return str(round(float(val), digits))
        except Exception:
            return "N/A"

    fcf = info.get("freeCashflow")
    rev = info.get("totalRevenue")
    fcf_margin = (fcf / rev) if (fcf and rev) else None

    return {
        "price":        _round(info.get("currentPrice") or info.get("regularMarketPrice"), 2),
        "pe":           _round(info.get("trailingPE"), 1),
        "fwd_pe":       _round(info.get("forwardPE"), 1),
        "rev_growth":   _pct(info.get("revenueGrowth")),
        "gross_margin": _pct(info.get("grossMargins")),
        "net_margin":   _pct(info.get("profitMargins")),
        "roe":          _pct(info.get("returnOnEquity")),
        "fcf_margin":   _pct(fcf_margin),
        "eps_growth":   _pct(info.get("earningsGrowth")),
        "market_cap":   info.get("marketCap"),
        "sector":       info.get("sector", ""),
        "industry":     info.get("industry", ""),
        "name":         info.get("shortName") or info.get("longName") or ticker,
    }


# ──────────────────────────────────────────────
# Claude analysis
# ──────────────────────────────────────────────

def analyze_stock(ticker: str) -> str:
    """Return a WhatsApp-formatted analysis for ticker (under ~1400 chars)."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "❌ ANTHROPIC_API_KEY not configured."

    ticker = ticker.upper().strip()
    m = _fetch_metrics(ticker)

    prompt = (
        f"You are a sharp financial analyst. Analyze {ticker} ({m['name']}) "
        f"in sector: {m['sector']} / {m['industry']}.\n\n"
        f"Live metrics from yfinance:\n"
        f"  Price: ${m['price']}\n"
        f"  P/E: {m['pe']}  |  Fwd P/E: {m['fwd_pe']}\n"
        f"  Rev Growth (1Y): {m['rev_growth']}\n"
        f"  Gross Margin: {m['gross_margin']}  |  Net Margin: {m['net_margin']}\n"
        f"  ROE: {m['roe']}  |  FCF Margin: {m['fcf_margin']}\n"
        f"  EPS Growth: {m['eps_growth']}\n\n"
        "Using the metrics above plus your knowledge of this company, "
        "return ONLY the following formatted message — no preamble, no extra text:\n\n"
        f"📊 {ticker} ANALYSIS\n"
        "──────────────\n"
        "🏆 Overall: [0-100]/100\n"
        "⚡ Growth: [0-100]/100\n"
        "🛡️ Risk: [0-100]/100\n\n"
        "💰 KEY METRICS\n"
        f"P/E: {m['pe']} [icon] | Fwd P/E: {m['fwd_pe']} [icon]\n"
        f"Rev Growth: {m['rev_growth']} [icon] | Net Margin: {m['net_margin']} [icon]\n"
        f"ROE: {m['roe']} [icon] | FCF Margin: {m['fcf_margin']} [icon]\n\n"
        "🏰 MOAT: [1 sentence on competitive advantage]\n"
        "⚡ CATALYST: [1 sentence on key catalyst next 12 months]\n"
        "🚀 BULL: [1 sentence bull case]\n"
        "🐻 BEAR: [1 sentence bear case]\n\n"
        "🧠 VERDICT: [2-sentence summary with conviction level]\n\n"
        "Replace [icon] with ✅ (strong) ⚠️ (neutral) or ❌ (weak). "
        "Total response MUST stay under 1400 characters."
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next(
            (b.text.strip() for b in resp.content if hasattr(b, "text") and b.text.strip()),
            None,
        )
        return text or "❌ Analysis returned empty — try again."

    except Exception:
        log.error("analyze_stock(%s) failed:\n%s", ticker, traceback.format_exc())
        return "❌ Analysis failed — please try again."
