"""
Stock analysis: real-time metrics from yfinance + qualitative analysis from Claude.
Every step is logged so Railway logs expose the real failure point.
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
    """Pull key fundamentals from yfinance. Always returns a complete dict; fields
    default to 'N/A' if unavailable so Claude still runs regardless."""
    log.info("analyst: fetching yfinance info for %s", ticker)
    info: dict = {}
    try:
        t    = yf.Ticker(ticker)
        info = t.info or {}
        log.info(
            "analyst: yfinance OK for %s — %d keys, price=%s, pe=%s, name=%s",
            ticker,
            len(info),
            info.get("currentPrice") or info.get("regularMarketPrice"),
            info.get("trailingPE"),
            info.get("shortName") or info.get("longName"),
        )
    except Exception:
        log.error(
            "analyst: yfinance FAILED for %s — proceeding with N/A metrics\n%s",
            ticker,
            traceback.format_exc(),
        )

    def _pct(val) -> str:
        if val is None:
            return "N/A"
        try:
            return f"{float(val) * 100:.1f}%"
        except Exception:
            return "N/A"

    def _fmt(val, digits: int = 1) -> str:
        if val is None:
            return "N/A"
        try:
            return str(round(float(val), digits))
        except Exception:
            return "N/A"

    fcf = info.get("freeCashflow")
    rev = info.get("totalRevenue")
    fcf_margin = (fcf / rev) if (fcf and rev) else None

    metrics = {
        "price":        _fmt(info.get("currentPrice") or info.get("regularMarketPrice"), 2),
        "pe":           _fmt(info.get("trailingPE"), 1),
        "fwd_pe":       _fmt(info.get("forwardPE"), 1),
        "rev_growth":   _pct(info.get("revenueGrowth")),
        "gross_margin": _pct(info.get("grossMargins")),
        "net_margin":   _pct(info.get("profitMargins")),
        "roe":          _pct(info.get("returnOnEquity")),
        "fcf_margin":   _pct(fcf_margin),
        "eps_growth":   _pct(info.get("earningsGrowth")),
        "sector":       info.get("sector") or "Unknown",
        "industry":     info.get("industry") or "Unknown",
        "name":         info.get("shortName") or info.get("longName") or ticker,
    }
    log.info("analyst: metrics for %s — %s", ticker, metrics)
    return metrics


# ──────────────────────────────────────────────
# Claude analysis
# ──────────────────────────────────────────────

def analyze_stock(ticker: str) -> str:
    """Return a WhatsApp-formatted analysis for ticker (under ~1400 chars)."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("analyst: ANTHROPIC_API_KEY not set")
        return "❌ ANTHROPIC_API_KEY not configured."

    ticker = ticker.upper().strip()
    log.info("analyst: starting analysis for %s", ticker)

    m = _fetch_metrics(ticker)

    prompt = (
        f"You are a sharp financial analyst. Analyze {ticker} ({m['name']}) "
        f"in sector: {m['sector']} / {m['industry']}.\n\n"
        "Live metrics from yfinance (use N/A where shown — fill in from "
        "your knowledge if the value is available):\n"
        f"  Price: ${m['price']}\n"
        f"  P/E: {m['pe']}  |  Fwd P/E: {m['fwd_pe']}\n"
        f"  Rev Growth (1Y): {m['rev_growth']}\n"
        f"  Gross Margin: {m['gross_margin']}  |  Net Margin: {m['net_margin']}\n"
        f"  ROE: {m['roe']}  |  FCF Margin: {m['fcf_margin']}\n"
        f"  EPS Growth: {m['eps_growth']}\n\n"
        "Return ONLY the formatted message below — no preamble, no extra text:\n\n"
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

    log.info("analyst: calling Claude model=%s prompt_len=%d", MODEL, len(prompt))
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        log.info(
            "analyst: Claude response received stop_reason=%s blocks=%d",
            resp.stop_reason,
            len(resp.content),
        )
        text = next(
            (b.text.strip() for b in resp.content if hasattr(b, "text") and b.text.strip()),
            None,
        )
        if text:
            log.info("analyst: returning %d chars for %s", len(text), ticker)
            return text
        log.warning("analyst: Claude returned no text blocks for %s", ticker)
        return "❌ Analysis returned empty — try again."

    except anthropic.APIStatusError as e:
        log.error(
            "analyst: Anthropic API error status=%s message=%s\n%s",
            e.status_code, e.message, traceback.format_exc(),
        )
        return f"❌ Analysis failed (API {e.status_code}) — try again."
    except Exception:
        log.error("analyst: unexpected error for %s\n%s", ticker, traceback.format_exc())
        return "❌ Analysis failed — please try again."
