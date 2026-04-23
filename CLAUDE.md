# investing-agent — AI Stock Bot for Wealthsimple TFSA

## What This Project Is

An AI-powered WhatsApp bot that monitors a Wealthsimple TFSA (Tax-Free Savings Account) and sends advisory alerts. **Advisory only — no auto-trading, no auto-buying, no order execution.** The user logs trades manually via WhatsApp commands. The bot tracks holdings in SQLite, monitors for sell signals every 15 minutes, scans StockTwits for trending opportunities every 30 minutes, and sends a morning portfolio summary every weekday at 8:45 AM ET.

---

## Architecture

Two distinct systems live in this repo:

### 1. Legacy scripts (root level)
Standalone, one-off analysis tools for experimentation and reporting. Not deployed.

### 2. Production bot (`bot/`)
A 24/7 Flask + APScheduler service deployed on Railway that accepts WhatsApp commands via Twilio and proactively sends market alerts.

---

## Project Structure

### Root level

| File | Purpose |
|------|---------|
| `Procfile` | **Deprecated root-level Procfile** — Railway uses `bot/Procfile` instead |
| `requirements.txt` | Root-level deps for legacy scripts only |
| `.env` | API keys — git-ignored |
| `.gitignore` | Excludes `.env`, `venv/`, `__pycache__/`, `bot/.env`, `.claude/` |
| `main.py` | Fetches 1-year stock data for a watchlist, sends to Claude, saves text report to `analysis.txt` |
| `stocks.py` | Extended analysis with financials, news, earnings; manages a paper portfolio with trailing stops and position limits |
| `bot.py` | Flask server (port 8080) serving `/api/data` JSON endpoint and an HTML dashboard |
| `app.py` | Simpler Flask variant that asks Claude for action ratings on a stock list |
| `scheduler.py` | Legacy daily scheduler (uses `schedule` lib, not APScheduler) that runs `stocks.py` at 9 AM |
| `write_dash.py` | Generates `dashboard.html` with KPI cards, position tables, trade log, allocation chart |
| `stock_trades.json` | Paper portfolio state: cash, open positions, closed trades, history |
| `trades_log.json` | Trade history log |
| `analysis.txt` | Output file where `main.py` saves analysis |
| `index.html` / `templates/index.html` | Web dashboard UI |

### `bot/` — production system

| File | Purpose |
|------|---------|
| `main.py` | **Local dev entry point** — initializes DB, starts APScheduler, runs Flask on port 5001 |
| `wsgi.py` | **Gunicorn entry point for Railway** — calls `init_db()` and `start_scheduler()` at import time, exports `app`; the `sys.path` fix lives here so Railway can find bot/ modules from the project root |
| `Procfile` | Railway command: `web: gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 120 wsgi:app` |
| `sms_handler.py` | Flask webhook at `POST /webhook` — receives Twilio WhatsApp messages, parses commands, returns TwiML; also exposes `/health` for uptime checks |
| `alerts.py` | Outbound Twilio WhatsApp sender — enforces quiet hours (22:00–07:00 ET), anti-spam deduplication per ticker per day, logs all alerts to DB |
| `scheduler.py` | APScheduler background jobs: morning summary (8:45 AM ET weekdays), sell monitor (every 15 min market hours), stock scanner (every 30 min Mon–Fri 7 AM–8 PM ET) |
| `scanner.py` | Proactive discovery: fetches StockTwits trending tickers, scores each 0–10, sends WhatsApp alert if score ≥ 7 and not alerted in last 24 h |
| `sell_monitor.py` | Checks holdings for sell signals (RSI rollover, MACD bearish cross, MA breaks, volume spikes, >3% drops) and broad market crashes (S&P 500 or TSX down ≥1.5%) |
| `strategy.py` | Claude-powered signal scoring and sell detection; defines the 24-ticker WATCHLIST (Canadian ETFs, Canadian stocks, US growth, US ETFs); `get_sell_signals()` returns urgency: URGENT / WARNING / FYI / None |
| `market_data.py` | Fetches prices and computes RSI (14-period), MACD, 50/200-day MAs, 1-day % change, volume ratio via yfinance; `get_index_day_change()` for S&P 500, TSX, NASDAQ, VIX |
| `portfolio.py` | SQLite CRUD for holdings (weighted-average cost basis), transactions, TFSA room, cash, realized P&L |
| `database.py` | SQLite initialization and connection; creates 8 tables: `holdings`, `transactions`, `tfsa_info`, `cash`, `alert_log`, `snoozed_tickers`, `scanner_alerts`, `portfolio_history` |
| `requirements.txt` | Pinned production deps: anthropic, yfinance, Flask, APScheduler, twilio, pytz, pandas, numpy, gunicorn, vaderSentiment, newsapi-python, python-dotenv |
| `.env` / `.env.example` | Credentials — `.env` is git-ignored |
| `portfolio.db` | SQLite database — git-ignored |
| `README.md` | Full setup guide, command reference, alert descriptions |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Web framework | Flask 3.x |
| WSGI server | Gunicorn (1 worker, timeout 120 s) |
| Hosting | Railway (`ai-stock-bot-production.up.railway.app`) |
| Messaging | Twilio WhatsApp Sandbox |
| Database | SQLite (`portfolio.db`) |
| Background jobs | APScheduler 3.x (BlockingScheduler in Railway, BackgroundScheduler locally) |
| Market data | yfinance |
| Sentiment analysis | VADER (vaderSentiment) |
| Social trending | StockTwits public API |
| News sentiment | NewsAPI (newsapi-python) |
| AI analysis | Anthropic Claude (claude-sonnet-4-6) via `anthropic` SDK |

---

## Deployment

- **Platform:** Railway
- **Live URL:** `https://ai-stock-bot-production.up.railway.app`
- **Auto-deploy:** pushes to `main` branch on GitHub (`karimbisharakb/ai-stock-bot`) trigger automatic Railway redeploy
- **Entry point:** `bot/wsgi.py` via `bot/Procfile`
- **Port:** Railway injects `$PORT`; gunicorn binds to `0.0.0.0:$PORT`

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API access |
| `TWILIO_ACCOUNT_SID` | Twilio account identifier |
| `TWILIO_AUTH_TOKEN` | Twilio auth secret |
| `TWILIO_PHONE_NUMBER` | Twilio sandbox number that sends messages: `+14155238886` |
| `MY_PHONE_NUMBER` | User's WhatsApp number that receives alerts (must match Twilio filter) |
| `NEWS_API_KEY` | NewsAPI key for news sentiment in scanner |

---

## WhatsApp Sandbox Details

- **Sandbox number:** `+1 415 523 8886`
- **Join code:** `join independent-dangerous`
- **Expiry:** sandbox sessions expire every **72 hours** — user must rejoin by sending the join code to stay connected
- **Webhook URL:** `https://ai-stock-bot-production.up.railway.app/webhook`

---

## WhatsApp Commands

| Command | What it does |
|---------|-------------|
| `BOUGHT AAPL 10 150.00` | Records a buy: 10 shares of AAPL at $150.00 |
| `SOLD AAPL 5 180.00` | Records a sell: 5 shares of AAPL at $180.00, calculates realized P&L |
| `PORTFOLIO` | Shows all holdings with current prices, % gain/loss, total value |
| `ROOM` or `ROOM 5000` | Gets current TFSA contribution room, or sets it to 5000 |
| `IGNORE AAPL` | Snoozes alerts for AAPL for 24 hours |
| `HELP` | Lists all available commands |
| `500` (any number) | Sets available cash balance to $500 |

---

## Scheduler Jobs

| Job | Schedule | What it does |
|-----|----------|-------------|
| Morning summary | 8:45 AM ET, weekdays | Retrieves holdings, cash, TFSA room, overnight WARNING signals; sends formatted WhatsApp summary |
| Sell monitor | Every 15 min, 9:30–16:00 ET weekdays | Checks each holding for technical sell signals and market-wide crashes |
| Stock scanner | Every 30 min, 7:00–20:00 ET weekdays | Scans StockTwits trending tickers, scores each, alerts if score ≥ 7 |

---

## Scanner Scoring (0–10)

| Signal | Max Points |
|--------|-----------|
| StockTwits sentiment (VADER) | 3 |
| RSI momentum | 2 |
| MACD | 2 |
| News sentiment (NewsAPI + VADER) | 2 |
| 1-day price action | 1 |

Alert fires if **score ≥ 7** and ticker has not been alerted in the last 24 hours.

---

## Known Fixes Applied

### Gunicorn port fix
Railway injects `$PORT` dynamically. The `bot/Procfile` must use `--bind 0.0.0.0:$PORT` — hardcoding a port causes Railway deployment to fail health checks.

### Phone filter bug fix
The webhook originally crashed when `MY_PHONE_NUMBER` was unset or the incoming `From` field was in a different format. Fix: normalize both numbers before comparing, and add a try/except guard so a filter failure returns an error TwiML instead of a 500.

### sys.path fix for imports from root
Railway runs gunicorn from the project root (`/app`), not from `bot/`. Without `sys.path.insert(0, os.path.dirname(__file__))` in `bot/wsgi.py`, all `from bot.X import Y` imports fail. The fix adds the `bot/` directory itself to `sys.path` at import time so modules can be imported by bare name (`from sms_handler import ...`).

---

## TFSA Watchlist (strategy.py)

**Canadian ETFs:** VFV.TO, XIU.TO, XQQ.TO, XEQT.TO, VEQT.TO, ZQQ.TO, HXS.TO  
**Canadian stocks:** SHOP.TO, RY.TO, TD.TO, ENB.TO, CNQ.TO  
**US growth:** NVDA, MSFT, AAPL, AMZN, META, GOOG, AMD, PLTR, TSM  
**US ETFs:** QQQ, SPY
