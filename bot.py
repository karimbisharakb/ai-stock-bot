import os, json, time, requests
from anthropic import Anthropic
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
claude = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

GAMMA_API = "https://gamma-api.polymarket.com"

# ── Paper portfolio ────────────────────────────────────────────────────────
portfolio = {
    "cash": 1000.0,
    "positions": [],
    "trades": [],
}

# ══════════════════════════════════════════════════════════════════════════
# STEP 1 — SCAN
# ══════════════════════════════════════════════════════════════════════════
def scan_markets():
    print("\n📡 STEP 1 — Scanning Polymarket...")
    r = requests.get(f"{GAMMA_API}/markets", params={
        "limit": 100, "active": "true", "closed": "false",
    })
    markets = r.json()
    tradeable = []
    for m in markets:
        try:
            volume    = float(m.get("volume24hr") or 0)
            liquidity = float(m.get("liquidity")  or 0)
            outcomes  = json.loads(m.get("outcomePrices", "[0.5,0.5]"))
            yes_price = float(outcomes[0])
            if volume > 5000 and liquidity > 2000 and 0.05 < yes_price < 0.95:
                tradeable.append({
                    "id":        m.get("id"),
                    "question":  m.get("question"),
                    "yes_price": round(yes_price, 4),
                    "no_price":  round(1 - yes_price, 4),
                    "volume":    round(volume),
                    "liquidity": round(liquidity),
                    "end_date":  m.get("endDate", "")[:10],
                })
        except:
            continue
    tradeable.sort(key=lambda x: x["volume"], reverse=True)
    print(f"  ✅ Found {len(tradeable)} tradeable markets")
    return tradeable[:15]

# ══════════════════════════════════════════════════════════════════════════
# STEP 2 — RESEARCH
# ══════════════════════════════════════════════════════════════════════════
def research_market(market):
    print(f"\n🔍 STEP 2 — Researching: {market['question'][:60]}...")
    prompt = f"""You are a prediction market research analyst.

Market: "{market['question']}"
YES price: {market['yes_price']:.1%}
Volume 24hr: ${market['volume']:,}
Closes: {market['end_date']}

Answer:
1. What is this market asking?
2. Key factors determining the outcome?
3. Current state of affairs?
4. Signals pointing YES vs NO?
5. Historical base rate for this type of event?

Be specific. No disclaimers."""

    resp = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    print("  ✅ Research done")
    return resp.content[0].text

# ══════════════════════════════════════════════════════════════════════════
# STEP 3 — PREDICT
# ══════════════════════════════════════════════════════════════════════════
def predict(market, research):
    print(f"\n🧠 STEP 3 — Predicting...")
    prompt = f"""You are a calibrated probability forecaster.

Market: "{market['question']}"
Market implied probability: {market['yes_price']:.1%}
Closes: {market['end_date']}

Research:
{research}

Return ONLY this JSON, no other text:
{{
  "p_true": 0.55,
  "direction": "YES",
  "confidence": "medium",
  "reasoning": "Two sentences max."
}}

Rules:
- p_true: your probability this resolves YES (0.01-0.99)
- direction: YES if p_true > {market['yes_price'] + 0.06:.2f}, NO if p_true < {market['yes_price'] - 0.06:.2f}, else SKIP
- confidence: low / medium / high
- Only choose YES or NO if confidence is medium or high"""

    resp = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}]
    )
    text = resp.content[0].text.strip()
    if "```" in text:
        text = text.split("```")[1].replace("json", "").strip()
    result = json.loads(text)
    edge = round(abs(result["p_true"] - market["yes_price"]), 4)
    print(f"  Market: {market['yes_price']:.1%} | Our call: {result['p_true']:.1%} | "
          f"Edge: {edge:.1%} | Signal: {result['direction']} ({result['confidence']})")
    return result, edge

# ══════════════════════════════════════════════════════════════════════════
# STEP 4 — RISK
# ══════════════════════════════════════════════════════════════════════════
def risk_check(prediction, edge):
    print(f"\n🛡️  STEP 4 — Risk check...")
    cash   = portfolio["cash"]
    active = len(portfolio["positions"])

    if prediction["direction"] == "SKIP":
        return None, "Signal is SKIP"
    if prediction["confidence"] == "low":
        return None, "Confidence too low"
    if edge < 0.06:
        return None, "Edge too small (<6%)"
    if active >= 10:
        return None, "Max 10 positions reached"
    if cash < 50:
        return None, "Not enough cash"

    # Kelly Criterion (quarter Kelly, capped at 5%)
    p   = prediction["p_true"]
    q   = 1 - p
    b   = 1  # even odds simplified
    f   = max(0, (p * b - q) / b) * 0.25
    f   = min(f, 0.05)
    size = round(cash * f, 2)
    size = max(10, min(size, 75))

    total_at_risk = sum(t["size"] for t in portfolio["positions"])
    if (total_at_risk + size) / 1000 > 0.15:
        return None, "Would exceed 15% total exposure"

    print(f"  ✅ Approved — size: ${size}")
    return size, "OK"

# ══════════════════════════════════════════════════════════════════════════
# STEP 5 — EXECUTE (paper trade)
# ══════════════════════════════════════════════════════════════════════════
def execute(market, prediction, size):
    print(f"\n💸 STEP 5 — Paper trading...")
    direction = prediction["direction"]
    price     = market["yes_price"] if direction == "YES" else market["no_price"]
    contracts = round(size / price, 2)

    portfolio["cash"] -= size

    trade = {
        "time":      datetime.now().strftime("%H:%M:%S"),
        "question":  market["question"][:50],
        "direction": direction,
        "price":     price,
        "contracts": contracts,
        "size":      size,
        "p_true":    prediction["p_true"],
        "p_market":  market["yes_price"],
        "reasoning": prediction["reasoning"],
        "status":    "OPEN",
    }
    portfolio["positions"].append(trade)
    portfolio["trades"].append(trade)
    print(f"  ✅ PAPER TRADE: {direction} ${size} @ {price:.1%} — {contracts} contracts")

# ══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════
def summary():
    print("\n" + "═"*62)
    print("                   📊 SESSION SUMMARY")
    print("═"*62)
    print(f"  Cash remaining : ${portfolio['cash']:.2f}")
    print(f"  Open positions : {len(portfolio['positions'])}")
    print(f"  Total trades   : {len(portfolio['trades'])}")
    if portfolio["trades"]:
        print("\n  TRADES:")
        for t in portfolio["trades"]:
            print(f"  [{t['time']}] {t['direction']:3} ${t['size']:5} @ {t['price']:.0%}  "
                  f"edge={abs(t['p_true']-t['p_market']):.0%}  {t['question'][:35]}")
    with open("trades_log.json", "w") as f:
        json.dump(portfolio, f, indent=2)
    print("\n  💾 Saved to trades_log.json")
    print("═"*62)

# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════
def run():
    print("═"*62)
    print("   🤖 POLYMARKET AI BOT — PAPER TRADING MODE")
    print("═"*62)

    markets = scan_markets()
    if not markets:
        print("❌ No markets found.")
        return

    trades_made = 0
    for market in markets:
        print(f"\n{'─'*62}")
        print(f"📌 {market['question'][:65]}")

        research          = research_market(market)
        prediction, edge  = predict(market, research)

        if prediction["direction"] == "SKIP":
            print("  ⏭️  Skipping — edge too small")
            continue

        size, reason = risk_check(prediction, edge)
        if size is None:
            print(f"  ⏭️  Risk blocked: {reason}")
            continue

        execute(market, prediction, size)
        trades_made += 1
        time.sleep(0.5)

    print(f"\n✅ Done — {trades_made} paper trades made.")
    summary()

if __name__ == "__main__":
    run()