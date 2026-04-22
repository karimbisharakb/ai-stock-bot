import os
import yfinance as yf
from anthropic import Anthropic
from dotenv import load_dotenv
import json

load_dotenv()
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── Edit this list to your liking ──────────────────────────────────────────
WATCHLIST = ["NVDA", "AAPL", "TSLA", "MSFT", "AMZN", "META", "GOOG", "AMD", "PLTR", "TSM"]
# ───────────────────────────────────────────────────────────────────────────

def get_stock_data(ticker):
    stock = yf.Ticker(ticker)

    # Price history
    hist_1y = stock.history(period="1y")
    hist_1m = stock.history(period="1mo")

    # Fundamentals
    info = stock.info

    # Financials
    try:
        financials = stock.financials.iloc[:, :2].to_string() if not stock.financials.empty else "N/A"
    except:
        financials = "N/A"

    # News headlines
    try:
        news = stock.news[:5]
        headlines = "\n".join([f"- {n['content']['title']}" for n in news]) if news else "No news"
    except:
        headlines = "No news available"

    # Price stats
    current_price = hist_1m['Close'].iloc[-1] if not hist_1m.empty else "N/A"
    price_1m_ago  = hist_1m['Close'].iloc[0]  if not hist_1m.empty else "N/A"
    price_1y_ago  = hist_1y['Close'].iloc[0]  if not hist_1y.empty else "N/A"

    try:
        change_1m = round(((current_price - price_1m_ago) / price_1m_ago) * 100, 2)
        change_1y = round(((current_price - price_1y_ago) / price_1y_ago) * 100, 2)
    except:
        change_1m = change_1y = "N/A"

    return {
        "ticker": ticker,
        "current_price": round(current_price, 2) if isinstance(current_price, float) else current_price,
        "change_1m_pct": change_1m,
        "change_1y_pct": change_1y,
        "pe_ratio": info.get("trailingPE", "N/A"),
        "forward_pe": info.get("forwardPE", "N/A"),
        "revenue_growth": info.get("revenueGrowth", "N/A"),
        "profit_margins": info.get("profitMargins", "N/A"),
        "debt_to_equity": info.get("debtToEquity", "N/A"),
        "52w_high": info.get("fiftyTwoWeekHigh", "N/A"),
        "52w_low": info.get("fiftyTwoWeekLow", "N/A"),
        "analyst_target": info.get("targetMeanPrice", "N/A"),
        "analyst_rating": info.get("recommendationKey", "N/A"),
        "market_cap": info.get("marketCap", "N/A"),
        "volume_avg": info.get("averageVolume", "N/A"),
        "beta": info.get("beta", "N/A"),
        "recent_prices": hist_1m['Close'].tail(10).round(2).tolist(),
        "financials": financials,
        "news_headlines": headlines,
    }

def analyze_all(watchlist):
    print(f"\n📡 Pulling data for {len(watchlist)} stocks...\n")
    all_data = []
    for ticker in watchlist:
        try:
            print(f"  Fetching {ticker}...")
            data = get_stock_data(ticker)
            all_data.append(data)
        except Exception as e:
            print(f"  ⚠️  Skipped {ticker}: {e}")

    print("\n🧠 Sending to Claude for analysis...\n")

    prompt = f"""
You are a professional stock analyst with access to the following data for {len(all_data)} stocks.

For each stock you have:
- Current price and % change over 1 month and 1 year
- P/E ratio (trailing and forward)
- Revenue growth, profit margins, debt/equity
- 52-week high and low
- Analyst consensus target price and rating
- Beta (volatility)
- Recent 10-day price trend
- Recent news headlines
- Financial statements (last 2 periods)

Here is the data:
{json.dumps(all_data, indent=2)}

Your job:
1. Rank ALL {len(all_data)} stocks from STRONGEST BUY to STRONGEST SELL
2. For each stock give:
   - Action: STRONG BUY / BUY / HOLD / SELL / STRONG SELL
   - Score: 1-10 (10 = strongest buy)
   - Price target (your estimate for 6-12 months)
   - 3-sentence reasoning covering fundamentals + momentum + risk
   - Key risk to watch

3. At the end, give a "TOP PICK" with 2-3 sentences on why it's your #1 conviction buy right now.

Be direct. No disclaimers. Treat this like you're advising a serious investor.
"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text

def main():
    print("=" * 60)
    print("         📈 AI INVESTING AGENT — FULL ANALYSIS")
    print("=" * 60)

    result = analyze_all(WATCHLIST)

    print(result)
    print("\n" + "=" * 60)

    # Save to file
    with open("analysis.txt", "w") as f:
        f.write(result)
    print("💾 Full analysis saved to analysis.txt")

if __name__ == "__main__":
    main()