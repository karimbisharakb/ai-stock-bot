import os, json, time, requests
from anthropic import Anthropic
from dotenv import load_dotenv
from datetime import datetime
import yfinance as yf

load_dotenv()
claude = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── Watchlist ──────────────────────────────────────────────────────────────
WATCHLIST = [
    "NVDA", "MSFT", "AAPL", "AMZN", "META", "GOOG",
    "QQQ", "SPY", "VFV.TO", "XQQ.TO",
    "SHOP.TO", "RY.TO", "TD.TO",
    "AMD", "PLTR", "TSM"
]

STOP_LOSS    = 0.08   # Sell if down 8%
TAKE_PROFIT  = 0.15   # Sell if up 15%
MAX_POSITIONS = 5
MIN_SCORE     = 7

# ── Portfolio persistence ──────────────────────────────────────────────────
def load_portfolio():
    if os.path.exists("stock_trades.json"):
        with open("stock_trades.json") as f:
            return json.load(f)
    return {
        "cash": 1000.0,
        "positions": [],
        "trades": [],
        "closed": [],
        "pnl": 0.0,
        "created": datetime.now().strftime("%Y-%m-%d")
    }

def save_portfolio(p):
    with open("stock_trades.json", "w") as f:
        json.dump(p, f, indent=2)

portfolio = load_portfolio()

# ── Questrade Auth ─────────────────────────────────────────────────────────
def get_questrade_token():
    token = os.getenv("QUESTRADE_REFRESH_TOKEN")
    if not token:
        return None, None
    try:
        r = requests.post("https://login.questrade.com/oauth2/token",
            params={"grant_type": "refresh_token", "refresh_token": token})
        if r.status_code != 200:
            print(f"  ⚠️  Questrade auth failed: {r.status_code}")
            return None, None
        data = r.json()
        if os.path.exists(".env"):
            env = open(".env").read()
            env = "\n".join(
                f"QUESTRADE_REFRESH_TOKEN={data['refresh_token']}"
                if l.startswith("QUESTRADE_REFRESH_TOKEN") else l
                for l in env.splitlines()
            )
            open(".env", "w").write(env)
        return data["access_token"], data["api_server"]
    except Exception as e:
        print(f"  ⚠️  Questrade error: {e}")
        return None, None

def get_symbol_id(ticker, access_token, api_server):
    headers = {"Authorization": f"Bearer {access_token}"}
    r = requests.get(f"{api_server}v1/symbols/search",
                     headers=headers, params={"prefix": ticker})
    symbols = r.json().get("symbols", [])
    if symbols:
        return symbols[0]["symbolId"]
    raise Exception(f"Symbol not found: {ticker}")

# ══════════════════════════════════════════════════════════════════════════
# STEP 1 — SCAN
# ══════════════════════════════════════════════════════════════════════════
def scan_stocks():
    print("\n📡 STEP 1 — Scanning stocks...")
    stocks = []
    for ticker in WATCHLIST:
        try:
            s    = yf.Ticker(ticker)
            info = s.info
            hist = s.history(period="3mo")
            if hist.empty:
                continue
            price    = round(hist["Close"].iloc[-1], 2)
            price_1m = hist["Close"].iloc[-22] if len(hist) > 22 else hist["Close"].iloc[0]
            price_3m = hist["Close"].iloc[0]
            stocks.append({
                "ticker":         ticker,
                "price":          price,
                "change_1m":      round(((price - price_1m) / price_1m) * 100, 2),
                "change_3m":      round(((price - price_3m) / price_3m) * 100, 2),
                "pe_ratio":       info.get("trailingPE", "N/A"),
                "forward_pe":     info.get("forwardPE", "N/A"),
                "revenue_growth": info.get("revenueGrowth", "N/A"),
                "profit_margin":  info.get("profitMargins", "N/A"),
                "52w_high":       info.get("fiftyTwoWeekHigh", "N/A"),
                "52w_low":        info.get("fiftyTwoWeekLow", "N/A"),
                "analyst_target": info.get("targetMeanPrice", "N/A"),
                "analyst_rating": info.get("recommendationKey", "N/A"),
                "beta":           info.get("beta", "N/A"),
                "market_cap":     info.get("marketCap", "N/A"),
            })
            print(f"  ✅ {ticker}: ${price} ({stocks[-1]['change_1m']:+.1f}% 1mo)")
        except Exception as e:
            print(f"  ⚠️  Skipped {ticker}: {e}")
    return stocks

# ══════════════════════════════════════════════════════════════════════════
# STEP 2 — MONITOR: Check existing positions
# ══════════════════════════════════════════════════════════════════════════
def monitor_positions(stock_map, access_token, api_server, account_id, live=False):
    print("\n👁️  STEP 2 — Monitoring positions...")
    if not portfolio["positions"]:
        print("  No open positions to monitor.")
        return

    for pos in portfolio["positions"][:]:
        ticker = pos["ticker"]
        if ticker not in stock_map:
            continue

        current_price = stock_map[ticker]["price"]
        buy_price     = pos["price"]
        shares        = pos["shares"]
        pnl_pct       = (current_price - buy_price) / buy_price
        pnl_dollar    = round((current_price - buy_price) * shares, 2)

        print(f"  {ticker}: bought @${buy_price} → now @${current_price} "
              f"({pnl_pct:+.1%}) PnL: ${pnl_dollar:+.2f}")

        should_sell = False
        reason      = ""

        if pnl_pct >= TAKE_PROFIT:
            should_sell = True
            reason = f"🎯 Take profit triggered (+{pnl_pct:.1%})"
        elif pnl_pct <= -STOP_LOSS:
            should_sell = True
            reason = f"🛑 Stop loss triggered ({pnl_pct:.1%})"

        # Also ask Claude if we should sell
        if not should_sell:
            sell_signal = claude_sell_check(pos, stock_map[ticker])
            if sell_signal:
                should_sell = True
                reason = f"🤖 AI sell signal: {sell_signal}"

        if should_sell:
            print(f"  → SELLING {ticker}: {reason}")
            execute_sell(pos, current_price, pnl_dollar, pnl_pct, reason,
                        access_token, api_server, account_id, live)

def claude_sell_check(position, current_stock):
    prompt = f"""You are managing a stock position.

Stock: {position['ticker']}
Bought at: ${position['price']}
Current price: ${current_stock['price']}
Shares held: {position['shares']}
P&L: {((current_stock['price'] - position['price']) / position['price']):.1%}
Original target: ${position.get('target', 'N/A')}
Original reasoning: {position.get('reasoning', 'N/A')}
1 month change: {current_stock['change_1m']}%
Analyst rating: {current_stock['analyst_rating']}

Should we SELL this position now?
Return ONLY one of:
- "HOLD" if we should keep it
- One sentence reason to sell if we should sell it

Be decisive."""

    resp = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}]
    )
    result = resp.content[0].text.strip()
    if result.upper().startswith("HOLD"):
        return None
    return result

# ══════════════════════════════════════════════════════════════════════════
# STEP 3 — RESEARCH
# ══════════════════════════════════════════════════════════════════════════
def research_stock(stock):
    print(f"\n🔍 STEP 3 — Researching {stock['ticker']}...")
    prompt = f"""You are a professional stock analyst.

Stock: {stock['ticker']}
Price: ${stock['price']} | 1mo: {stock['change_1m']}% | 3mo: {stock['change_3m']}%
P/E: {stock['pe_ratio']} | Forward P/E: {stock['forward_pe']}
Revenue growth: {stock['revenue_growth']} | Margins: {stock['profit_margin']}
52w range: ${stock['52w_low']} - ${stock['52w_high']}
Analyst target: ${stock['analyst_target']} | Rating: {stock['analyst_rating']}
Beta: {stock['beta']}

Analyze:
1. Undervalued, fair, or overvalued?
2. Strongest bullish signals?
3. Key risks?
4. 6-12 month outlook?
Be direct. No disclaimers."""

    resp = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )
    print(f"  ✅ Done")
    return resp.content[0].text

# ══════════════════════════════════════════════════════════════════════════
# STEP 4 — PREDICT
# ══════════════════════════════════════════════════════════════════════════
def predict_stock(stock, research):
    print(f"\n🧠 STEP 4 — Predicting {stock['ticker']}...")
    upside = "N/A"
    if stock["analyst_target"] != "N/A" and stock["price"]:
        upside = round(((stock["analyst_target"] - stock["price"]) / stock["price"]) * 100, 1)

    prompt = f"""You are making a trading decision.

Stock: {stock['ticker']} at ${stock['price']}
Analyst target: ${stock['analyst_target']} (upside: {upside}%)
Rating: {stock['analyst_rating']}

Research:
{research}

Return ONLY this JSON:
{{
  "action": "BUY or HOLD or SELL",
  "conviction": "low or medium or high",
  "score": 8,
  "price_target": 220.00,
  "stop_loss": 185.00,
  "expected_return_6mo": 0.15,
  "reasoning": "Two sentences.",
  "key_risk": "One sentence."
}}"""

    resp = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    text = resp.content[0].text.strip()
    if "```" in text:
        text = text.split("```")[1].replace("json", "").strip()
    result = json.loads(text)
    print(f"  {result['action']} | Score: {result['score']}/10 | "
          f"Target: ${result['price_target']} | Stop: ${result.get('stop_loss','N/A')}")
    return result

# ══════════════════════════════════════════════════════════════════════════
# STEP 5 — RISK
# ══════════════════════════════════════════════════════════════════════════
def risk_check(stock, prediction):
    print(f"\n🛡️  STEP 5 — Risk check {stock['ticker']}...")
    if prediction["action"] != "BUY":
        return None, f"Signal is {prediction['action']}"
    if prediction["conviction"] == "low":
        return None, "Conviction too low"
    if prediction["score"] < MIN_SCORE:
        return None, f"Score too low ({prediction['score']}/10)"
    if len(portfolio["positions"]) >= MAX_POSITIONS:
        return None, "Max positions reached"
    if portfolio["cash"] < 50:
        return None, "Not enough cash"
    held = [p for p in portfolio["positions"] if p["ticker"] == stock["ticker"]]
    if held:
        return None, "Already holding"

    size = round(portfolio["cash"] * 0.20, 2)
    size = max(50, min(size, 200))
    print(f"  ✅ Approved — size: ${size}")
    return size, "OK"

# ══════════════════════════════════════════════════════════════════════════
# STEP 6 — EXECUTE BUY
# ══════════════════════════════════════════════════════════════════════════
def execute_buy(stock, prediction, size, access_token, api_server, account_id, live=False):
    print(f"\n💸 STEP 6 — {'LIVE' if live else 'Paper'} BUY {stock['ticker']}...")
    shares = round(size / stock["price"], 4)
    if shares < 0.001:
        print("  ⚠️  Too small")
        return

    cost = round(shares * stock["price"], 2)

    trade = {
        "time":       datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ticker":     stock["ticker"],
        "action":     "BUY",
        "shares":     shares,
        "price":      stock["price"],
        "cost":       cost,
        "target":     prediction["price_target"],
        "stop_loss":  prediction.get("stop_loss"),
        "score":      prediction["score"],
        "reasoning":  prediction["reasoning"],
        "key_risk":   prediction["key_risk"],
        "status":     "OPEN",
        "live":       live,
    }

    if live:
        try:
            headers   = {"Authorization": f"Bearer {access_token}"}
            symbol_id = get_symbol_id(stock["ticker"], access_token, api_server)
            order = {
                "accountNumber":   account_id,
                "symbolId":        symbol_id,
                "quantity":        int(shares),
                "limitPrice":      stock["price"],
                "orderType":       "Limit",
                "timeInForce":     "Day",
                "action":          "Buy",
                "primaryRoute":    "AUTO",
                "secondaryRoute":  "AUTO",
            }
            r = requests.post(
                f"{api_server}v1/accounts/{account_id}/orders",
                headers=headers, json=order
            )
            if r.status_code == 200:
                print(f"  ✅ LIVE ORDER: {shares} x {stock['ticker']} @ ${stock['price']}")
            else:
                print(f"  ❌ Order failed: {r.text}")
                return
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return

    portfolio["cash"] -= cost
    portfolio["positions"].append(trade)
    portfolio["trades"].append(trade)
    print(f"  ✅ {'LIVE' if live else 'PAPER'} BUY: {shares} x {stock['ticker']} "
          f"@ ${stock['price']} = ${cost}")

# ══════════════════════════════════════════════════════════════════════════
# EXECUTE SELL
# ══════════════════════════════════════════════════════════════════════════
def execute_sell(position, current_price, pnl_dollar, pnl_pct,
                 reason, access_token, api_server, account_id, live=False):
    ticker = position["ticker"]
    shares = position["shares"]
    proceeds = round(shares * current_price, 2)

    if live:
        try:
            headers   = {"Authorization": f"Bearer {access_token}"}
            symbol_id = get_symbol_id(ticker, access_token, api_server)
            order = {
                "accountNumber":  account_id,
                "symbolId":       symbol_id,
                "quantity":       int(shares),
                "limitPrice":     current_price,
                "orderType":      "Limit",
                "timeInForce":    "Day",
                "action":         "Sell",
                "primaryRoute":   "AUTO",
                "secondaryRoute": "AUTO",
            }
            r = requests.post(
                f"{api_server}v1/accounts/{account_id}/orders",
                headers=headers, json=order
            )
            if r.status_code != 200:
                print(f"  ❌ Sell order failed: {r.text}")
                return
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return

    portfolio["cash"]    += proceeds
    portfolio["pnl"]     = portfolio.get("pnl", 0) + pnl_dollar
    portfolio["positions"] = [p for p in portfolio["positions"] if p["ticker"] != ticker]

    closed = {**position,
        "sell_price":  current_price,
        "sell_time":   datetime.now().strftime("%Y-%m-%d %H:%M"),
        "pnl_dollar":  pnl_dollar,
        "pnl_pct":     round(pnl_pct * 100, 2),
        "sell_reason": reason,
        "status":      "CLOSED",
    }
    portfolio["closed"].append(closed)
    portfolio["trades"].append({**closed, "action": "SELL"})

    emoji = "🟢" if pnl_dollar > 0 else "🔴"
    print(f"  {emoji} SOLD {ticker}: ${position['price']} → ${current_price} "
          f"= {pnl_pct:+.1%} (${pnl_dollar:+.2f})")

# ══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════
def summary():
    total_pnl    = portfolio.get("pnl", 0)
    closed_trades = portfolio.get("closed", [])
    wins  = len([t for t in closed_trades if t.get("pnl_dollar", 0) > 0])
    losses= len([t for t in closed_trades if t.get("pnl_dollar", 0) < 0])
    win_rate = f"{(wins/(wins+losses)*100):.0f}%" if (wins+losses) > 0 else "N/A"

    print("\n" + "═"*62)
    print("                📊 PORTFOLIO SUMMARY")
    print("═"*62)
    print(f"  Cash          : ${portfolio['cash']:.2f}")
    print(f"  Open positions: {len(portfolio['positions'])}")
    print(f"  Total trades  : {len(portfolio['trades'])}")
    print(f"  Closed trades : {len(closed_trades)}")
    print(f"  Realized PnL  : ${total_pnl:+.2f}")
    print(f"  Win rate      : {win_rate} ({wins}W / {losses}L)")

    if portfolio["positions"]:
        print("\n  OPEN:")
        for p in portfolio["positions"]:
            print(f"  • {p['ticker']:<10} {p['shares']} shares @ ${p['price']} → target ${p['target']}")

    if closed_trades:
        print("\n  CLOSED:")
        for p in closed_trades[-5:]:
            emoji = "🟢" if p.get("pnl_dollar",0) > 0 else "🔴"
            print(f"  {emoji} {p['ticker']:<10} {p.get('pnl_pct',0):+.1f}% (${p.get('pnl_dollar',0):+.2f})")

    save_portfolio(portfolio)
    print("\n  💾 Saved to stock_trades.json")
    print("═"*62)

# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════
def run(live=False):
    print("═"*62)
    mode = "🔴 LIVE TRADING" if live else "📄 PAPER TRADING"
    print(f"   🤖 AI STOCK BROKER — {mode}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("═"*62)

    access_token, api_server = get_questrade_token()
account_id = os.getenv("QUESTRADE_ACCOUNT_ID")
if not access_token:
    print("⚠️  Running without Questrade — analysis only mode")
    live = False

    # Scan all stocks
    stocks     = scan_stocks()
    stock_map  = {s["ticker"]: s for s in stocks}

    # Monitor & sell existing positions
    monitor_positions(stock_map, access_token, api_server, account_id, live)

    # Find new buys
    trades_made = 0
    for stock in stocks:
        print(f"\n{'─'*62}")
        print(f"📌 {stock['ticker']} — ${stock['price']}")

        research   = research_stock(stock)
        prediction = predict_stock(stock, research)
        size, reason = risk_check(stock, prediction)

        if size is None:
            print(f"  ⏭️  Skipped: {reason}")
            continue

        execute_buy(stock, prediction, size, access_token, api_server, account_id, live)
        trades_made += 1
        time.sleep(0.5)

    print(f"\n✅ Done — {trades_made} new trades.")
    summary()

if __name__ == "__main__":
    run(live=False)  # ← Change to True for real money