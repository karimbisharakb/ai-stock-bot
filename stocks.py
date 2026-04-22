import os, json, time, requests
from anthropic import Anthropic
from dotenv import load_dotenv
from datetime import datetime
import yfinance as yf

load_dotenv()
claude = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── Watchlist ─────────────────────────────────────────────────────────────────
WATCHLIST = [
    # US mega-cap growth
    "NVDA", "MSFT", "AAPL", "AMZN", "META", "GOOG", "AMD", "PLTR", "TSM",
    # US aggressive growth
    "CRWD", "SNOW", "ARM", "SMCI",
    # Crypto proxies (counted against a separate 20% bucket)
    "MSTR", "COIN", "RIOT", "MARA", "CLSK",
    # Canadian stocks
    "SHOP.TO", "RY.TO", "TD.TO", "ENB.TO", "CNQ.TO",
    # ETFs
    "QQQ", "SPY", "ARKK", "SOXL", "TQQQ", "VFV.TO", "XQQ.TO",
]

CRYPTO_PROXIES = {"MSTR", "COIN", "RIOT", "MARA", "CLSK"}

# Per-strategy exit levels
EXIT_RULES = {
    "SWING":  {"stop": 0.05, "target": 0.12, "trail_trigger": 0.15, "trail_pct": 0.08},
    "MEDIUM": {"stop": 0.08, "target": 0.25, "trail_trigger": 0.15, "trail_pct": 0.08},
    "LONG":   {"stop": 0.12, "target": 0.50, "trail_trigger": 0.15, "trail_pct": 0.08},
}

MAX_POSITIONS  = 5
MAX_CRYPTO_PCT = 0.20   # max 20% of total portfolio in crypto proxies
MIN_SCORE      = 7      # skip anything below this


# ── Technical indicator helpers ───────────────────────────────────────────────

def compute_rsi(close, period=14):
    """14-period RSI. Returns float or None on insufficient data / NaN."""
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    rsi   = 100 - (100 / (1 + rs))
    val   = rsi.iloc[-1]
    return round(float(val), 1) if val == val else None   # NaN guard


def compute_macd(close):
    """Returns (macd_line, signal_line, histogram) as floats."""
    ema12  = close.ewm(span=12, adjust=False).mean()
    ema26  = close.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist   = macd - signal
    return (
        round(float(macd.iloc[-1]),   4),
        round(float(signal.iloc[-1]), 4),
        round(float(hist.iloc[-1]),   4),
    )


def earnings_within_days(ticker, days=7):
    """True if the next earnings date is within `days` calendar days."""
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            return False
        # yfinance returns dict in newer versions, DataFrame in older ones
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date", [])
        elif hasattr(cal, "loc") and "Earnings Date" in cal.index:
            dates = list(cal.loc["Earnings Date"])
        else:
            return False
        if not dates:
            return False
        next_date = min(
            d.date() if hasattr(d, "date") else d
            for d in dates if d is not None
        )
        diff = (next_date - datetime.now().date()).days
        return 0 <= diff <= days
    except Exception:
        return False


# ── Portfolio helpers ─────────────────────────────────────────────────────────

def load_portfolio():
    if os.path.exists("stock_trades.json"):
        with open("stock_trades.json") as f:
            data = json.load(f)
        # Ensure new top-level fields exist for older portfolio files
        data.setdefault("closed",            [])
        data.setdefault("portfolio_history", [])
        data.setdefault("top_picks",         [])
        data.setdefault("regime",            "UNKNOWN")
        data.setdefault("metrics",           {})
        data.setdefault("pnl",               0.0)
        return data
    return {
        "cash":              1000.0,
        "positions":         [],
        "trades":            [],
        "closed":            [],
        "pnl":               0.0,
        "portfolio_history": [],
        "top_picks":         [],
        "regime":            "UNKNOWN",
        "metrics":           {},
        "created":           datetime.now().strftime("%Y-%m-%d"),
    }


def save_portfolio(p):
    with open("stock_trades.json", "w") as f:
        json.dump(p, f, indent=2)


def portfolio_total_value(portfolio, stock_map=None):
    """Cash + mark-to-market value of all open positions."""
    total = portfolio["cash"]
    for pos in portfolio.get("positions", []):
        price = (
            stock_map[pos["ticker"]]["price"]
            if stock_map and pos["ticker"] in stock_map
            else pos["price"]          # fall back to cost basis if no live price
        )
        total += pos["shares"] * price
    return round(total, 2)


def get_crypto_exposure(portfolio):
    """Total cost basis currently in crypto-proxy positions."""
    return sum(p.get("cost", 0) for p in portfolio.get("positions", [])
               if p["ticker"] in CRYPTO_PROXIES)


def compute_max_drawdown(history):
    """Peak-to-trough drawdown (%) from portfolio_history list."""
    values = [h["value"] for h in history if h.get("value")]
    if len(values) < 2:
        return 0.0
    peak, max_dd = values[0], 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd
    return round(max_dd * 100, 2)


def compute_metrics(portfolio):
    """Win rate, rolling-20 win rate, trade-Sharpe, max drawdown."""
    closed  = portfolio.get("closed", [])
    returns = [t.get("pnl_pct", 0) for t in closed]   # stored as % (e.g. 5.2)
    wins    = [r for r in returns if r > 0]

    win_rate   = round(len(wins) / len(returns) * 100, 1) if returns else None
    last20     = returns[-20:]
    rolling_wr = round(len([r for r in last20 if r > 0]) / len(last20) * 100, 1) if last20 else None

    sharpe = None
    if len(returns) >= 3:
        avg      = sum(returns) / len(returns)
        variance = sum((r - avg) ** 2 for r in returns) / len(returns)
        std      = variance ** 0.5
        if std > 0:
            sharpe = round((avg - 0.02) / std, 2)   # 0.02% risk-free per trade

    avg_return = round(sum(returns) / len(returns), 2) if returns else None
    max_dd     = compute_max_drawdown(portfolio.get("portfolio_history", []))

    underperforming = (
        rolling_wr is not None and rolling_wr < 40 and len(returns) >= 5
    )

    return {
        "win_rate":         win_rate,
        "rolling_win_rate": rolling_wr,
        "avg_return":       avg_return,
        "sharpe":           sharpe,
        "max_drawdown":     max_dd,
        "total_trades":     len(returns),
        "wins":             len(wins),
        "losses":           len(returns) - len(wins),
        "underperforming":  underperforming,
    }


def update_portfolio_history(portfolio, total_value):
    """Append today's total portfolio value; retain up to 365 entries."""
    history = portfolio.setdefault("portfolio_history", [])
    today   = datetime.now().strftime("%Y-%m-%d")
    if history and history[-1]["date"] == today:
        history[-1]["value"] = total_value
    else:
        history.append({"date": today, "value": total_value})
    portfolio["portfolio_history"] = history[-365:]


# ── Questrade auth ────────────────────────────────────────────────────────────

def get_questrade_token():
    token = os.getenv("QUESTRADE_REFRESH_TOKEN")
    if not token:
        return None, None
    try:
        r = requests.post(
            "https://login.questrade.com/oauth2/token",
            params={"grant_type": "refresh_token", "refresh_token": token},
            timeout=10,
        )
        if r.status_code != 200 or not r.text:
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
        print(f"  ⚠️  Questrade unavailable: {e}")
        return None, None


def get_symbol_id(ticker, access_token, api_server):
    headers = {"Authorization": f"Bearer {access_token}"}
    r = requests.get(
        f"{api_server}v1/symbols/search", headers=headers, params={"prefix": ticker}
    )
    symbols = r.json().get("symbols", [])
    if symbols:
        return symbols[0]["symbolId"]
    raise Exception(f"Symbol not found: {ticker}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 0 — MARKET REGIME DETECTION
# SPY above MA200 + MA50 > MA200 → BULL (use full Kelly)
# SPY below MA200                → BEAR (cut sizes 50%)
# Mixed                          → SIDEWAYS (selective, 75% sizing)
# ══════════════════════════════════════════════════════════════════════════════

def detect_market_regime():
    print("\n🌍 STEP 0 — Market regime detection...")
    try:
        hist = yf.Ticker("SPY").history(period="1y")
        if hist.empty or len(hist) < 50:
            print("  ⚠️  Insufficient SPY data — defaulting SIDEWAYS")
            return "SIDEWAYS"

        price = float(hist["Close"].iloc[-1])
        ma50  = float(hist["Close"].rolling(50).mean().iloc[-1])
        ma200 = float(hist["Close"].rolling(200).mean().iloc[-1]) if len(hist) >= 200 else None

        if ma200:
            if price > ma200 and ma50 > ma200:
                regime = "BULL"
            elif price < ma200:
                regime = "BEAR"
            else:
                regime = "SIDEWAYS"
        else:
            regime = "BULL" if price > ma50 else "BEAR"

        emoji = {"BULL": "🟢", "BEAR": "🔴", "SIDEWAYS": "🟡"}[regime]
        ma200_str = f" | MA200 ${ma200:.2f}" if ma200 else ""
        print(f"  {emoji} {regime} — SPY ${price:.2f} | MA50 ${ma50:.2f}{ma200_str}")
        return regime
    except Exception as e:
        print(f"  ⚠️  Regime detection error: {e}")
        return "SIDEWAYS"


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — SCAN
# Pulls 1Y of history to compute all technical indicators and momentum.
# ══════════════════════════════════════════════════════════════════════════════

def scan_stocks():
    print("\n📡 STEP 1 — Scanning watchlist...")
    stocks = []

    for ticker in WATCHLIST:
        try:
            s    = yf.Ticker(ticker)
            info = s.info
            hist = s.history(period="1y")
            if hist.empty or len(hist) < 10:
                continue

            close  = hist["Close"]
            volume = hist["Volume"]
            price  = round(float(close.iloc[-1]), 2)

            def pct(days):
                if len(close) > days:
                    base = float(close.iloc[-(days + 1)])
                    return round(((price - base) / base) * 100, 2)
                return None

            # Moving averages
            ma50  = round(float(close.rolling(50).mean().iloc[-1]),  2) if len(close) >= 50  else None
            ma200 = round(float(close.rolling(200).mean().iloc[-1]), 2) if len(close) >= 200 else None

            # Volume trend: recent 5-day avg vs 20-day avg (>1 = rising)
            v5  = float(volume.iloc[-5:].mean())
            v20 = float(volume.iloc[-20:].mean())
            vol_trend = round(v5 / v20, 2) if v20 > 0 else 1.0

            # Technical indicators
            try:
                rsi_val = compute_rsi(close)
                macd_line, macd_signal, macd_hist = compute_macd(close)
            except Exception:
                rsi_val = macd_line = macd_signal = macd_hist = None

            has_earnings = earnings_within_days(ticker, 7)

            stocks.append({
                "ticker":          ticker,
                "price":           price,
                # Momentum (multiple timeframes)
                "change_1w":       pct(5),
                "change_1m":       pct(21),
                "change_3m":       pct(63),
                "change_6m":       pct(126),
                "change_1y":       pct(252),
                # Technical
                "rsi":             rsi_val,
                "macd_line":       macd_line,
                "macd_signal":     macd_signal,
                "macd_hist":       macd_hist,
                "ma50":            ma50,
                "ma200":           ma200,
                "vol_trend":       vol_trend,
                # Fundamental
                "pe_ratio":        info.get("trailingPE",    "N/A"),
                "forward_pe":      info.get("forwardPE",     "N/A"),
                "revenue_growth":  info.get("revenueGrowth", "N/A"),
                "profit_margin":   info.get("profitMargins", "N/A"),
                "debt_to_equity":  info.get("debtToEquity",  "N/A"),
                "52w_high":        info.get("fiftyTwoWeekHigh", "N/A"),
                "52w_low":         info.get("fiftyTwoWeekLow",  "N/A"),
                "analyst_target":  info.get("targetMeanPrice",  "N/A"),
                "analyst_rating":  info.get("recommendationKey","N/A"),
                "beta":            info.get("beta",       "N/A"),
                "market_cap":      info.get("marketCap",  "N/A"),
                # Catalyst flags
                "earnings_soon":   has_earnings,
                "is_crypto_proxy": ticker in CRYPTO_PROXIES,
            })
            print(f"  ✅ {ticker:<12} ${price:<10} "
                  f"1M: {pct(21):+.1f}%  RSI: {rsi_val}  "
                  f"MACD: {'▲' if (macd_hist or 0) > 0 else '▼'}  "
                  f"{'⚡earnings' if has_earnings else ''}")
        except Exception as e:
            print(f"  ⚠️  Skipped {ticker}: {e}")

    return stocks


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — MONITOR existing positions
# Checks strategy-specific stops, take profits, trailing stops, and AI thesis.
# ══════════════════════════════════════════════════════════════════════════════

def check_exit_rules(pos, current_price):
    """
    Returns (should_sell: bool, reason: str).
    Uses EXIT_RULES per strategy type.
    Trailing stop activates once position is up trail_trigger%; then
    trails by trail_pct% from the peak price.
    """
    strategy  = pos.get("strategy", "MEDIUM")
    rules     = EXIT_RULES[strategy]
    buy_price = pos["price"]
    pnl_pct   = (current_price - buy_price) / buy_price

    # Track all-time high for trailing stop
    trail_high = pos.get("trail_high", buy_price)
    if current_price > trail_high:
        pos["trail_high"] = current_price
        trail_high = current_price

    # Trailing stop: once up trail_trigger%, trail by trail_pct%
    max_gain = (trail_high - buy_price) / buy_price
    if max_gain >= rules["trail_trigger"]:
        trail_floor = trail_high * (1 - rules["trail_pct"])
        if current_price <= trail_floor:
            return True, (f"Trailing stop ${trail_floor:.2f} hit "
                          f"(peak +{max_gain:.1%} from ${buy_price})")

    if pnl_pct >= rules["target"]:
        return True, f"Take profit +{pnl_pct:.1%} (target {rules['target']:.0%})"
    if pnl_pct <= -rules["stop"]:
        return True, f"Stop loss {pnl_pct:.1%} (limit -{rules['stop']:.0%})"

    return False, ""


def claude_sell_check(position, current_stock, market_regime):
    """Ask Claude whether the original investment thesis is still intact."""
    buy_price = position["price"]
    pnl_pct   = (current_stock["price"] - buy_price) / buy_price
    days      = position.get("days_held", 0)

    prompt = f"""You are monitoring an open position. Should we HOLD or SELL?

Position: {position['ticker']} | Strategy: {position.get('strategy', 'MEDIUM')}
Bought: ${buy_price} | Now: ${current_stock['price']} | P&L: {pnl_pct:+.1%}
Days held: {days} | Score at entry: {position.get('score', 'N/A')}
Original thesis: {position.get('reasoning', 'N/A')}
Key risk noted at entry: {position.get('key_risk', 'N/A')}

Current signals:
  RSI: {current_stock.get('rsi', 'N/A')}
  MACD: {'bullish' if (current_stock.get('macd_hist') or 0) > 0 else 'bearish'}
  1M return: {current_stock.get('change_1m', 'N/A')}%
  3M return: {current_stock.get('change_3m', 'N/A')}%
  Analyst rating: {current_stock.get('analyst_rating', 'N/A')}
Market regime: {market_regime}

Reply ONLY with "HOLD" or one sentence explaining why to exit. No other text."""

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}],
    )
    result = resp.content[0].text.strip()
    return None if result.upper().startswith("HOLD") else result


def monitor_positions(portfolio, stock_map, market_regime,
                      access_token, api_server, account_id, live=False):
    print("\n👁️  STEP 2 — Monitoring positions...")
    if not portfolio["positions"]:
        print("  No open positions.")
        return

    for pos in portfolio["positions"][:]:
        ticker = pos["ticker"]
        if ticker not in stock_map:
            continue

        current       = stock_map[ticker]
        current_price = current["price"]
        buy_price     = pos["price"]
        shares        = pos["shares"]
        pnl_pct       = (current_price - buy_price) / buy_price
        pnl_dollar    = round((current_price - buy_price) * shares, 2)

        # Persist live metrics back into the position for the dashboard
        pos["last_price"]   = current_price
        pos["last_pnl_pct"] = round(pnl_pct * 100, 2)
        try:
            pos["days_held"] = (
                datetime.now() - datetime.strptime(pos["time"][:10], "%Y-%m-%d")
            ).days
        except Exception:
            pos["days_held"] = 0

        strategy = pos.get("strategy", "MEDIUM")
        print(f"  {ticker} [{strategy}] @${buy_price} → ${current_price} "
              f"({pnl_pct:+.1%}) PnL ${pnl_dollar:+.2f} | {pos['days_held']}d")

        should_sell, reason = check_exit_rules(pos, current_price)

        if not should_sell:
            ai_reason = claude_sell_check(pos, current, market_regime)
            if ai_reason:
                should_sell = True
                reason = f"AI thesis break: {ai_reason}"

        if should_sell:
            print(f"  → SELL {ticker}: {reason}")
            execute_sell(pos, current_price, pnl_dollar, pnl_pct,
                         reason, portfolio, access_token, api_server, account_id, live)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — RESEARCH
# Full-signal prompt: momentum, technical, fundamental, catalysts.
# ══════════════════════════════════════════════════════════════════════════════

def research_stock(stock, market_regime):
    print(f"\n🔍 STEP 3 — Researching {stock['ticker']}...")

    price     = stock["price"]
    ma50      = stock.get("ma50")
    ma200     = stock.get("ma200")
    macd_hist = stock.get("macd_hist") or 0

    if ma50 and ma200:
        if price > ma200 and price > ma50:
            ma_label = "above both MAs — bullish structure"
        elif price < ma200:
            ma_label = "below 200-MA — bearish"
        else:
            ma_label = "between MAs — mixed"
    else:
        ma_label = "insufficient history"

    prompt = f"""You are an elite hedge fund analyst. Sharp, specific, no disclaimers.

STOCK: {stock['ticker']} — ${price}
MARKET REGIME: {market_regime}

MOMENTUM (trailing returns):
  1W: {stock.get('change_1w','N/A')}%  1M: {stock.get('change_1m','N/A')}%  3M: {stock.get('change_3m','N/A')}%  6M: {stock.get('change_6m','N/A')}%  1Y: {stock.get('change_1y','N/A')}%

TECHNICAL SIGNALS:
  RSI(14): {stock.get('rsi','N/A')} | MACD histogram: {'positive ▲' if macd_hist > 0 else 'negative ▼'} ({macd_hist})
  MA50: ${ma50 or 'N/A'} | MA200: ${ma200 or 'N/A'} | Structure: {ma_label}
  Volume trend (5d vs 20d avg): {stock.get('vol_trend','N/A')}x

FUNDAMENTALS:
  P/E: {stock.get('pe_ratio','N/A')} | Fwd P/E: {stock.get('forward_pe','N/A')}
  Revenue growth: {stock.get('revenue_growth','N/A')} | Margins: {stock.get('profit_margin','N/A')} | D/E: {stock.get('debt_to_equity','N/A')}
  52w range: ${stock.get('52w_low','N/A')} – ${stock.get('52w_high','N/A')}
  Analyst consensus: ${stock.get('analyst_target','N/A')} target | Rating: {stock.get('analyst_rating','N/A')} | Beta: {stock.get('beta','N/A')}

CATALYST FLAGS:
  Earnings within 7 days: {'YES ⚠️' if stock.get('earnings_soon') else 'No'}
  Crypto proxy: {'Yes (20% portfolio cap)' if stock.get('is_crypto_proxy') else 'No'}

Give a 4-sentence analysis covering:
1. Technical setup quality (breakout/breakdown/range/trend strength)
2. Momentum trajectory — accelerating, peaking, or fading?
3. Fundamental value vs growth quality
4. Single most important catalyst or risk for next 30–90 days"""

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    print("  ✅ Done")
    return resp.content[0].text


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — PREDICT
# Classifies strategy type, sets conviction, scores, returns structured JSON.
# ══════════════════════════════════════════════════════════════════════════════

def predict_stock(stock, research, market_regime):
    print(f"\n🧠 STEP 4 — Predicting {stock['ticker']}...")

    upside = "N/A"
    if stock["analyst_target"] != "N/A" and stock["price"]:
        upside = round(
            ((stock["analyst_target"] - stock["price"]) / stock["price"]) * 100, 1
        )

    prompt = f"""You are making a precise trading decision for an aggressive growth portfolio.

Stock: {stock['ticker']} @ ${stock['price']}
Market regime: {market_regime}  (BEAR → only score ≥9 BUYs | SIDEWAYS → only score ≥8 BUYs | BULL → full Kelly)
RSI: {stock.get('rsi','N/A')} | MACD: {'bullish' if (stock.get('macd_hist') or 0) > 0 else 'bearish'}
Analyst target: ${stock.get('analyst_target','N/A')} (upside: {upside}%) | Rating: {stock.get('analyst_rating','N/A')}
Earnings within 7 days: {'YES — halve size if high conviction, SKIP if medium' if stock.get('earnings_soon') else 'No'}
Crypto proxy (20% total portfolio cap): {'YES' if stock.get('is_crypto_proxy') else 'No'}

Research:
{research}

Return ONLY this JSON (no markdown fences, no extra text):
{{
  "action": "BUY",
  "strategy": "MEDIUM",
  "conviction": "high",
  "score": 8,
  "price_target": 220.00,
  "stop_loss": 185.00,
  "expected_return_pct": 20.0,
  "hold_days_estimate": 60,
  "reasoning": "Two sentences max.",
  "key_risk": "One sentence.",
  "earnings_play": false
}}

Field rules:
- action: BUY / HOLD / SKIP
- strategy: SWING (1-14d momentum/breakout) | MEDIUM (1-6mo undervalued growth) | LONG (6mo+ compounder)
- conviction: high (score 9-10) | medium (score 7-8) | low (score ≤6, must be SKIP)
- score: integer 1-10
- earnings_play: true only when buying with earnings ≤7 days away"""

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=350,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    if "```" in text:
        text = text.split("```")[1].replace("json", "").strip()

    result = json.loads(text)
    print(f"  {result['action']} [{result.get('strategy','?')}] "
          f"score {result['score']}/10 | "
          f"target ${result['price_target']} | stop ${result.get('stop_loss','N/A')}")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — RISK / POSITION SIZING
# Kelly Criterion with conviction multiplier and regime adjustment.
#
# Conviction caps:
#   High (score 9-10): up to 25% of portfolio
#   Medium (score 7-8): up to 15% of portfolio
#   Low (≤6): skip
#
# Regime scaling:
#   BULL     → 1.0×  (full Kelly)
#   SIDEWAYS → 0.75× (trim sizing)
#   BEAR     → 0.50× (half sizing, only high conviction)
# ══════════════════════════════════════════════════════════════════════════════

def risk_check(stock, prediction, portfolio, market_regime):
    print(f"\n🛡️  STEP 5 — Risk check {stock['ticker']}...")

    if prediction["action"] in ("HOLD", "SKIP"):
        return None, f"Signal is {prediction['action']}"
    if prediction["score"] < MIN_SCORE:
        return None, f"Score {prediction['score']}/10 below minimum ({MIN_SCORE})"
    if prediction["conviction"] == "low":
        return None, "Low conviction — skip"
    if len(portfolio["positions"]) >= MAX_POSITIONS:
        return None, "Max positions reached (5)"
    if portfolio["cash"] < 50:
        return None, "Insufficient cash (< $50)"
    if any(p["ticker"] == stock["ticker"] for p in portfolio["positions"]):
        return None, "Already holding"

    # Earnings gate: medium conviction + earnings ≤7 days → skip
    if stock.get("earnings_soon") and prediction["conviction"] == "medium":
        return None, "Earnings ≤7 days with medium conviction — wait for report"

    total_value = portfolio_total_value(portfolio)

    # Kelly Criterion (simplified):
    # Edge derived from score → win probability; payoff = reward / stop-distance
    stop_dist = abs(
        stock["price"] - prediction.get("stop_loss", stock["price"] * 0.92)
    ) / stock["price"]
    reward  = max(prediction.get("expected_return_pct", 15) / 100, 0.05)
    p_win   = min(0.85, 0.40 + (prediction["score"] - 5) * 0.05)  # 7→60% 10→75%
    p_loss  = 1 - p_win
    payoff  = reward / max(stop_dist, 0.01)
    kelly_f = max(0.0, (p_win * payoff - p_loss) / payoff)

    # Conviction caps and regime scaling
    if prediction["conviction"] == "high" and prediction["score"] >= 9:
        max_pct, multiplier = 0.25, 1.0
    else:
        max_pct, multiplier = 0.15, 0.75

    regime_scale = {"BULL": 1.0, "SIDEWAYS": 0.75, "BEAR": 0.50}.get(market_regime, 0.75)

    # Quarter-Kelly × conviction × regime
    f    = kelly_f * multiplier * regime_scale * 0.25
    f    = min(f, max_pct)
    size = round(total_value * f, 2)
    size = max(50.0, min(size, portfolio["cash"] * 0.95))

    # Crypto allocation guard
    if stock.get("is_crypto_proxy"):
        crypto_exp = get_crypto_exposure(portfolio)
        if (crypto_exp + size) / total_value > MAX_CRYPTO_PCT:
            return None, f"Would exceed {MAX_CRYPTO_PCT:.0%} crypto allocation"

    # Earnings play: halve size for high-conviction buy ahead of earnings
    if stock.get("earnings_soon"):
        size = round(size * 0.5, 2)
        print(f"  ⚡ Earnings play — halved size to ${size}")

    if size < 50:
        return None, "Sized position too small after adjustments"

    print(f"  ✅ Approved — [{prediction['strategy']}] Kelly {kelly_f:.1%} "
          f"→ {f*100:.1f}% of portfolio = ${size}")
    return size, "OK"


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — EXECUTE BUY
# ══════════════════════════════════════════════════════════════════════════════

def execute_buy(stock, prediction, size, portfolio,
                access_token, api_server, account_id, live=False):
    print(f"\n💸 STEP 6 — {'LIVE' if live else 'Paper'} BUY {stock['ticker']}...")

    shares = round(size / stock["price"], 4)
    if shares < 0.001:
        print("  ⚠️  Position too small")
        return
    cost = round(shares * stock["price"], 2)

    trade = {
        "time":          datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ticker":        stock["ticker"],
        "action":        "BUY",
        "strategy":      prediction.get("strategy", "MEDIUM"),
        "shares":        shares,
        "price":         stock["price"],
        "cost":          cost,
        "target":        prediction["price_target"],
        "stop_loss":     prediction.get("stop_loss"),
        "score":         prediction["score"],
        "conviction":    prediction["conviction"],
        "reasoning":     prediction["reasoning"],
        "key_risk":      prediction["key_risk"],
        "earnings_play": prediction.get("earnings_play", False),
        "trail_high":    stock["price"],   # highest observed price for trailing stop
        "last_price":    stock["price"],
        "last_pnl_pct":  0.0,
        "days_held":     0,
        "status":        "OPEN",
        "live":          live,
    }

    if live:
        try:
            headers   = {"Authorization": f"Bearer {access_token}"}
            symbol_id = get_symbol_id(stock["ticker"], access_token, api_server)
            order = {
                "accountNumber":  account_id,
                "symbolId":       symbol_id,
                "quantity":       int(shares),
                "limitPrice":     stock["price"],
                "orderType":      "Limit",
                "timeInForce":    "Day",
                "action":         "Buy",
                "primaryRoute":   "AUTO",
                "secondaryRoute": "AUTO",
            }
            r = requests.post(
                f"{api_server}v1/accounts/{account_id}/orders",
                headers=headers, json=order,
            )
            if r.status_code == 200:
                print(f"  ✅ LIVE ORDER: {int(shares)} × {stock['ticker']} @ ${stock['price']}")
            else:
                print(f"  ❌ Order failed: {r.text}")
                return
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return

    portfolio["cash"] -= cost
    portfolio["positions"].append(trade)
    portfolio["trades"].append(trade)
    print(f"  ✅ {'LIVE' if live else 'PAPER'} BUY: {shares} × {stock['ticker']} "
          f"@ ${stock['price']} = ${cost} [{prediction.get('strategy','?')}]")


# ══════════════════════════════════════════════════════════════════════════════
# EXECUTE SELL
# ══════════════════════════════════════════════════════════════════════════════

def execute_sell(position, current_price, pnl_dollar, pnl_pct,
                 reason, portfolio, access_token, api_server, account_id, live=False):
    ticker   = position["ticker"]
    shares   = position["shares"]
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
                headers=headers, json=order,
            )
            if r.status_code != 200:
                print(f"  ❌ Sell order failed: {r.text}")
                return
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return

    portfolio["cash"]     += proceeds
    portfolio["pnl"]       = portfolio.get("pnl", 0) + pnl_dollar
    portfolio["positions"] = [p for p in portfolio["positions"] if p["ticker"] != ticker]

    closed = {
        **position,
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
          f"= {pnl_pct:+.1%} (${pnl_dollar:+.2f}) [{position.get('strategy','?')}]")


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

def summary(portfolio):
    metrics  = portfolio.get("metrics", {})
    regime   = portfolio.get("regime",  "UNKNOWN")
    closed   = portfolio.get("closed",  [])
    history  = portfolio.get("portfolio_history", [])
    current_value = history[-1]["value"] if history else portfolio["cash"]
    total_return  = round(((current_value - 1000) / 1000) * 100, 2)

    print("\n" + "═" * 64)
    print("                  📊 PORTFOLIO SUMMARY")
    print("═" * 64)
    print(f"  Market Regime  : {regime}")
    print(f"  Portfolio Value: ${current_value:.2f} ({total_return:+.2f}% vs $1,000)")
    print(f"  Cash           : ${portfolio['cash']:.2f}")
    print(f"  Open Positions : {len(portfolio['positions'])}")
    print(f"  Closed Trades  : {len(closed)}")
    print(f"  Realized PnL   : ${portfolio.get('pnl', 0):+.2f}")
    print(f"  Win Rate       : {metrics.get('win_rate', 'N/A')}%  "
          f"(rolling 20: {metrics.get('rolling_win_rate', 'N/A')}%)")
    print(f"  Avg Return/Trade: {metrics.get('avg_return', 'N/A')}%")
    print(f"  Sharpe Ratio   : {metrics.get('sharpe', 'N/A')}")
    print(f"  Max Drawdown   : {metrics.get('max_drawdown', 0):.1f}%")

    if metrics.get("underperforming"):
        print("\n  ⚠️  WARNING: Rolling win rate below 40% — strategy review needed")

    if portfolio["positions"]:
        print("\n  OPEN POSITIONS:")
        for p in portfolio["positions"]:
            pnl_str = f"{p.get('last_pnl_pct', 0):+.1f}%"
            stop    = p.get("stop_loss", "N/A")
            print(f"  • {p['ticker']:<10} [{p.get('strategy','?'):6}] "
                  f"{p['shares']} @ ${p['price']} → ${p.get('target','?')} "
                  f"stop ${stop}  P&L {pnl_str}  {p.get('days_held',0)}d held")

    if portfolio.get("top_picks"):
        print("\n  TOP WATCHLIST PICKS (not yet bought):")
        for pick in portfolio["top_picks"]:
            print(f"  ★ {pick.get('ticker','?'):<10} {pick.get('score','?')}/10 "
                  f"[{pick.get('strategy','?')}]  "
                  f"{pick.get('reasoning','')[:70]}...")

    if closed:
        print("\n  RECENT CLOSED:")
        for p in closed[-5:]:
            emoji = "🟢" if p.get("pnl_dollar", 0) > 0 else "🔴"
            print(f"  {emoji} {p['ticker']:<10} {p.get('pnl_pct',0):+.1f}% "
                  f"(${p.get('pnl_dollar',0):+.2f})  [{p.get('strategy','?')}]  "
                  f"{p.get('sell_reason','')[:40]}")

    save_portfolio(portfolio)
    print("\n  💾 Saved to stock_trades.json")
    print("═" * 64)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def run(live=False):
    print("═" * 64)
    mode = "🔴 LIVE TRADING" if live else "📄 PAPER TRADING"
    print(f"   🤖 AI HEDGE FUND — {mode}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("═" * 64)

    portfolio = load_portfolio()

    # Questrade auth
    access_token, api_server = get_questrade_token()
    account_id = os.getenv("QUESTRADE_ACCOUNT_ID")
    if not access_token:
        print("⚠️  No Questrade connection — paper mode only")
        live = False

    # Step 0: market regime
    regime = detect_market_regime()
    portfolio["regime"] = regime

    # Step 1: scan full watchlist
    stocks    = scan_stocks()
    stock_map = {s["ticker"]: s for s in stocks}

    # Step 2: monitor and potentially exit existing positions
    if portfolio["positions"]:
        monitor_positions(portfolio, stock_map, regime,
                          access_token, api_server, account_id, live)

    # Steps 3-6: research → predict → size → execute for each candidate
    all_predictions = []
    trades_made = 0

    for stock in stocks:
        print(f"\n{'─' * 64}")
        print(f"📌 {stock['ticker']} — ${stock['price']}")

        research   = research_stock(stock, regime)
        prediction = predict_stock(stock, research, regime)
        all_predictions.append({**prediction, "ticker": stock["ticker"]})

        size, reason = risk_check(stock, prediction, portfolio, regime)
        if size is None:
            print(f"  ⏭️  Skipped: {reason}")
            continue

        execute_buy(stock, prediction, size, portfolio,
                    access_token, api_server, account_id, live)
        trades_made += 1
        time.sleep(0.5)

    # Save top-3 picks by score for the dashboard (even if not purchased)
    held = {p["ticker"] for p in portfolio["positions"]}
    top_picks = sorted(
        [p for p in all_predictions
         if p.get("action") == "BUY" and p["ticker"] not in held],
        key=lambda x: x["score"],
        reverse=True,
    )[:3]
    portfolio["top_picks"] = [
        {k: p[k] for k in
         ["ticker", "score", "action", "strategy", "conviction",
          "reasoning", "price_target", "key_risk"] if k in p}
        for p in top_picks
    ]

    # Update portfolio history and recompute metrics
    total_value = portfolio_total_value(portfolio, stock_map)
    update_portfolio_history(portfolio, total_value)
    portfolio["metrics"]  = compute_metrics(portfolio)
    portfolio["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"\n✅ Done — {trades_made} new trade(s).")
    summary(portfolio)


if __name__ == "__main__":
    run(live=False)
