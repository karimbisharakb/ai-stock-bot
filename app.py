from flask import Flask, jsonify, send_from_directory
import yfinance as yf
from anthropic import Anthropic
from dotenv import load_dotenv
import os, json

load_dotenv()
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
app = Flask(__name__)

WATCHLIST = ["NVDA", "AAPL", "TSLA", "MSFT", "AMZN", "META", "GOOG", "AMD", "PLTR", "TSM"]

def get_stock_data(ticker):
    stock = yf.Ticker(ticker)
    hist = stock.history(period="1mo")
    info = stock.info
    try:
        news = stock.news[:3]
        headlines = [n['content']['title'] for n in news]
    except:
        headlines = []

    current = hist['Close'].iloc[-1]
    prev    = hist['Close'].iloc[0]
    change  = round(((current - prev) / prev) * 100, 2)

    return {
        "ticker": ticker,
        "current_price": round(current, 2),
        "change_1m_pct": change,
        "pe_ratio": info.get("trailingPE", "N/A"),
        "forward_pe": info.get("forwardPE", "N/A"),
        "revenue_growth": info.get("revenueGrowth", "N/A"),
        "profit_margins": info.get("profitMargins", "N/A"),
        "52w_high": info.get("fiftyTwoWeekHigh", "N/A"),
        "52w_low": info.get("fiftyTwoWeekLow", "N/A"),
        "analyst_target": info.get("targetMeanPrice", "N/A"),
        "analyst_rating": info.get("recommendationKey", "N/A"),
        "beta": info.get("beta", "N/A"),
        "market_cap": info.get("marketCap", "N/A"),
        "prices": hist['Close'].round(2).tolist(),
        "dates": hist.index.strftime('%b %d').tolist(),
        "news": headlines,
    }

@app.route("/")
def index():
    from flask import Response
    html = open("templates/index.html", encoding="utf-8").read()
    return Response(html, status=200, mimetype="text/html; charset=utf-8")
@app.route("/api/data")
def data():
    all_data = []
    for ticker in WATCHLIST:
        try:
            all_data.append(get_stock_data(ticker))
        except Exception as e:
            print(f"Skipped {ticker}: {e}")

    prompt = f"""
You are a professional stock analyst. Analyze these {len(all_data)} stocks and return ONLY a JSON array.

Data: {json.dumps(all_data, indent=2)}

Return ONLY this JSON format, no other text:
[
  {{
    "ticker": "NVDA",
    "action": "STRONG BUY",
    "score": 9,
    "price_target": 250,
    "summary": "One paragraph reasoning.",
    "key_risk": "One sentence risk."
  }}
]

Actions must be one of: STRONG BUY, BUY, HOLD, SELL, STRONG SELL
Sort by score descending.
"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text
    # Strip markdown if present
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]

    analysis = json.loads(text.strip())

    # Merge price data into analysis
    price_map = {d["ticker"]: d for d in all_data}
    for item in analysis:
        item.update(price_map.get(item["ticker"], {}))

    return jsonify(analysis)

if __name__ == "__main__":
    app.run(debug=True, port=8080)