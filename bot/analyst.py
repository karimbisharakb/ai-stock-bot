"""Stock analysis via Claude + web search."""
import os
import logging
import traceback
import anthropic

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
_TOOLS = [{"type": "web_search_20250305", "name": "web_search"}]


def analyze_stock(ticker: str) -> str:
    """Return a WhatsApp-formatted analysis for ticker (under ~1400 chars)."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "❌ ANTHROPIC_API_KEY not configured."

    client = anthropic.Anthropic(api_key=api_key)
    ticker = ticker.upper().strip()

    prompt = (
        f"You are a concise financial analyst. Use web search to get current {ticker} data. "
        "Return ONLY this formatted message — no preamble, no trailing text:\n\n"
        f"📊 {ticker} ANALYSIS\n"
        "──────────────\n"
        "🏆 Overall: [0-100]/100\n"
        "⚡ Growth: [0-100]/100\n"
        "🛡️ Risk: [0-100]/100\n\n"
        "💰 KEY METRICS\n"
        "P/E: [val] [icon] | Fwd P/E: [val] [icon]\n"
        "Rev Growth: [val]% [icon] | Net Margin: [val]% [icon]\n"
        "ROE: [val]% [icon] | FCF Margin: [val]% [icon]\n\n"
        "🏰 MOAT: [1 sentence on competitive advantage]\n"
        "⚡ CATALYST: [1 sentence on key catalyst next 12 months]\n"
        "🚀 BULL: [1 sentence bull case]\n"
        "🐻 BEAR: [1 sentence bear case]\n\n"
        "🧠 VERDICT: [2-sentence summary with conviction level]\n\n"
        "Icon key: ✅ strong/good  ⚠️ neutral/average  ❌ weak/bad\n"
        "Total response MUST be under 1400 characters."
    )

    messages = [{"role": "user", "content": prompt}]

    try:
        return _run_loop(client, messages)
    except Exception:
        log.error("analyze_stock(%s) failed:\n%s", ticker, traceback.format_exc())
        return "❌ Analysis failed — please try again."


def _run_loop(client: anthropic.Anthropic, messages: list) -> str:
    for _ in range(8):
        resp = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            tools=_TOOLS,
            messages=messages,
        )

        if resp.stop_reason == "end_turn":
            text = next(
                (b.text.strip() for b in resp.content if hasattr(b, "text") and b.text.strip()),
                None,
            )
            return text or "❌ Analysis returned empty — try again."

        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": "Search executed.",
                }
                for b in resp.content
                if b.type == "tool_use"
            ]
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
        else:
            # Unexpected stop reason — return any text we have
            text = next(
                (b.text.strip() for b in resp.content if hasattr(b, "text") and b.text.strip()),
                None,
            )
            if text:
                return text
            break

    return "❌ Analysis timed out — please try again."
