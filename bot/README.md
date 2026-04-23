# TFSA Trading Bot

SMS-based stock signal bot for your Wealthsimple TFSA. Monitors your holdings,
texts you sell alerts, and gives ranked buy recommendations when you deposit cash.
No auto-trading — you place all trades manually.

---

## Setup

### 1. Install dependencies

```bash
cd bot
pip install -r requirements.txt
```

### 2. Configure .env

Copy `.env.example` to `.env` and fill in all values:

```
TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxx
TWILIO_PHONE_NUMBER=+1xxxxxxxxxx   ← your Twilio number
MY_PHONE_NUMBER=+1xxxxxxxxxx       ← your personal number
ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Initialize the database

```bash
python -c "from database import init_db; init_db()"
```

### 4. Start the bot locally

```bash
python main.py
```

### 5. Expose webhook with ngrok (local dev)

In a separate terminal:

```bash
ngrok http 5001
```

Copy the `https://xxxx.ngrok.io` URL, then go to:
- **Twilio Console** → Phone Numbers → your number → Messaging
- Set **"A message comes in"** webhook to:  
  `https://xxxx.ngrok.io/webhook`  (POST)

---

## Deploying to Railway (24/7)

1. Push this repo to GitHub
2. Create a new Railway project → "Deploy from GitHub repo"
3. Set environment variables in Railway dashboard (same as .env)
4. Railway auto-assigns a public URL — use it as your Twilio webhook:  
   `https://your-app.up.railway.app/webhook`

---

## SMS Commands

| Text | What it does |
|------|-------------|
| `BOUGHT 3 VFV.TO at 162.50` | Adds/updates a position |
| `SOLD 2 SHOP.TO at 125.00` | Records a sale, logs P&L |
| `PORTFOLIO` | Full holdings summary |
| `500` | Get buy recommendations for $500 budget |
| `ROOM 7000` | Update your TFSA contribution room |
| `IGNORE VFV.TO` | Snooze alerts for 24 hours |
| `HELP` | Command list |

---

## Automated Alerts

- **8:45 AM ET** — Morning summary with portfolio, overnight signals, market outlook
- **Every 15 min** (market hours) — Sell signal monitoring for all holdings
- **Immediate** — Urgent sell alerts when 2+ signals fire on a holding
- **Market crash** — Texts when S&P 500 or TSX drops 1.5%+ in one day

### Sell signals monitored:
- RSI > 65 and rolling over
- MACD bearish crossover
- Price below 50-day or 200-day MA
- Volume spike (2×) with price drop
- Single-day drop ≥ 3%
- Broad market down ≥ 1.5%
- Earnings within 7 days

---

## Project Structure

```
bot/
  main.py          ← local entry point
  wsgi.py          ← gunicorn entry point
  sms_handler.py   ← Flask webhook + command parsing
  alerts.py        ← Twilio outbound + anti-spam
  strategy.py      ← buy signal scoring (Claude-powered)
  portfolio.py     ← SQLite read/write
  market_data.py   ← yfinance indicators
  sell_monitor.py  ← sell signal loop
  scheduler.py     ← APScheduler jobs
  database.py      ← SQLite init
  portfolio.db     ← SQLite file (git-ignored)
  .env             ← credentials (git-ignored)
```
