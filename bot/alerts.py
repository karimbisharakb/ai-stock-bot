"""
Outbound WhatsApp via Twilio sandbox.
Handles quiet hours, anti-spam, and all message formatting.
"""
import os
from datetime import datetime, date, timedelta
from typing import Optional
import pytz
from twilio.rest import Client
from database import get_connection

EASTERN = pytz.timezone("America/Toronto")

TWILIO_SID   = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
FROM_NUMBER  = "whatsapp:+14155238886"
TO_NUMBER    = "whatsapp:+12899718200"

URGENCY_RANK = {"FYI": 1, "WARNING": 2, "URGENT": 3}


# ──────────────────────────────────────────────
# Core send
# ──────────────────────────────────────────────

def _is_quiet_hours() -> bool:
    now = datetime.now(EASTERN)
    return now.hour >= 22 or now.hour < 7


def send_sms(message: str, bypass_quiet: bool = False) -> bool:
    if _is_quiet_hours() and not bypass_quiet:
        print(f"🔇 Quiet hours — suppressed: {message[:60]}…")
        return False
    try:
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        client.messages.create(body=message, from_=FROM_NUMBER, to=TO_NUMBER)
        print(f"📤 WhatsApp sent: {message[:80]}…")
        return True
    except Exception as e:
        print(f"⚠️  WhatsApp send failed: {e}")
        return False


# ──────────────────────────────────────────────
# Anti-spam
# ──────────────────────────────────────────────

def _is_snoozed(ticker: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT snoozed_until FROM snoozed_tickers WHERE ticker = ?", (ticker,)
    ).fetchone()
    conn.close()
    if row is None:
        return False
    return datetime.fromisoformat(row["snoozed_until"]) > datetime.now()


def can_send_alert(ticker: str, urgency: str) -> bool:
    if ticker and _is_snoozed(ticker):
        return False

    today_start = datetime.now(EASTERN).replace(hour=0, minute=0, second=0, microsecond=0)
    conn = get_connection()
    row = conn.execute(
        "SELECT urgency FROM alert_log WHERE ticker = ? AND sent_at >= ? ORDER BY sent_at DESC LIMIT 1",
        (ticker, today_start.isoformat()),
    ).fetchone()
    conn.close()

    if row is None:
        return True
    # Allow if new urgency is strictly higher
    return URGENCY_RANK.get(urgency, 0) > URGENCY_RANK.get(row["urgency"], 0)


def log_alert(ticker: Optional[str], urgency: str, message: str):
    conn = get_connection()
    conn.execute(
        "INSERT INTO alert_log (ticker, urgency, sent_at, message) VALUES (?,?,?,?)",
        (ticker, urgency, datetime.now().isoformat(), message[:500]),
    )
    conn.commit()
    conn.close()


def snooze_ticker(ticker: str, hours: int = 24):
    until = (datetime.now() + timedelta(hours=hours)).isoformat()
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO snoozed_tickers (ticker, snoozed_until) VALUES (?,?)",
        (ticker, until),
    )
    conn.commit()
    conn.close()


# ──────────────────────────────────────────────
# Message formatters
# ──────────────────────────────────────────────

def format_sell_alert(ticker: str, shares: float, avg_cost: float, data: dict, signals: list[str]) -> str:
    price    = data["price"]
    pct      = round((price / avg_cost - 1) * 100, 2)
    sign     = "+" if pct >= 0 else ""
    bullets  = "\n".join(f"• {s}" for s in signals)
    return (
        "----------------------------------------\n"
        f"🔴 URGENT SELL ALERT — {ticker}\n\n"
        f"You hold: {shares} shares @ avg ${avg_cost:.2f}\n"
        f"Current price: ${price:.2f}\n"
        f"Change today: {data['pct_1d']:+.1f}%\n\n"
        f"Why I think it will fall further:\n{bullets}\n\n"
        f"If sold now: {sign}{pct:.1f}%\n\n"
        f"⚡ Sell in Wealthsimple soon\n"
        "----------------------------------------"
    )


def format_warning_alert(ticker: str, shares: float, avg_cost: float, data: dict, signals: list[str]) -> str:
    price   = data["price"]
    pct     = round((price / avg_cost - 1) * 100, 2)
    sign    = "+" if pct >= 0 else ""
    bullet  = signals[0] if signals else "Signal forming"
    return (
        f"🟡 WARNING — {ticker}\n"
        f"{shares} sh @ ${avg_cost:.2f} → ${price:.2f} ({sign}{pct:.1f}%)\n"
        f"• {bullet}\n"
        f"Watch today."
    )


def format_market_crash_alert(changes: dict, at_risk: list[dict]) -> str:
    index_lines = "\n".join(f"  {k}: {v:+.1f}% today" for k, v in changes.items())
    risk_lines  = "\n".join(
        f"  • {r['ticker']:<10} — {r['risk']} risk ({r['reason']})" for r in at_risk
    )
    return (
        "----------------------------------------\n"
        "⚠️  MARKET DOWNTURN DETECTED\n\n"
        f"{index_lines}\n\n"
        "Your holdings at risk:\n"
        f"{risk_lines}\n\n"
        "Consider protecting your gains. Place sells\n"
        "manually in Wealthsimple if needed.\n"
        "----------------------------------------"
    )


def format_trade_recommendations(budget: float, recs: list[dict], tfsa_room: float) -> str:
    if not recs:
        return (
            f"📈 Budget: ${budget:,.2f} CAD\n"
            "No strong buy signals right now. Check back during market hours."
        )

    # Show the exchange rate used (take from first rec if available)
    rate_note = ""
    if recs and recs[0].get("is_us") and recs[0].get("usdcad"):
        rate_note = f"💱 USD/CAD: {recs[0]['usdcad']:.4f} + 1.5% WS fee\n"

    lines = [
        "----------------------------------------",
        "📈 WEALTHSIMPLE TFSA TRADE ALERT",
        f"Budget: ${budget:,.2f} CAD",
        f"TFSA Room Left: ${tfsa_room:,.2f} CAD",
        rate_note.rstrip(),
        "",
    ]

    for i, r in enumerate(recs[:3], 1):
        is_us = r.get("is_us", False)
        if is_us:
            price_line = (
                f"${r['price']:.2f} USD "
                f"(~${r['price_cad']:.2f} CAD incl. 1.5% FX fee)"
            )
            target_line = f"${r['target']:.2f} USD (+{r['upside']:.1f}%)"
            stop_line   = f"${r['stop']:.2f} USD"
        else:
            price_line  = f"${r['price_cad']:.2f} CAD"
            target_line = f"${r['target']:.2f} CAD (+{r['upside']:.1f}%)"
            stop_line   = f"${r['stop']:.2f} CAD"

        lines += [
            f"#{i} BUY — {r['ticker']}",
            f"Price:  {price_line}",
            f"Shares: {r['shares']} = ${r['cost']:.2f} CAD total",
            f"Signal: {r['reasoning'][:80]}",
            f"Target: {target_line}",
            f"Stop:   {stop_line}",
            f"Strategy: {r['strategy']}",
            f"Confidence: {r['confidence']}",
            "",
        ]

    if tfsa_room > 0 and budget > tfsa_room:
        lines.append(f"⚠️  Budget ${budget:,.2f} exceeds TFSA room ${tfsa_room:,.2f} CAD!")
    lines += [
        "⚠️  Place trades manually in Wealthsimple",
        "⚠️  ETFs settle T+1 after purchase",
        "----------------------------------------",
    ]
    return "\n".join(lines)


def format_morning_summary(holdings_with_prices: list[dict], overnight_signals: list[str], cash: float, room: float) -> str:
    today = datetime.now(EASTERN).strftime("%B %-d, %Y")

    if holdings_with_prices:
        pos_lines = []
        for r in holdings_with_prices:
            sign = "+" if r["gain"] >= 0 else ""
            pos_lines.append(
                f"  {r['ticker']:<10} {r['shares']} sh  ${r['current_price']:.2f}  "
                f"{sign}${r['gain']:.2f} ({sign}{r['gain_pct']:.1f}%)"
            )
        total_val  = cash + sum(r["curr_value"] for r in holdings_with_prices)
        total_gain = sum(r["gain"] for r in holdings_with_prices)
        total_cost = sum(r["shares"] * r["avg_cost"] for r in holdings_with_prices)
        total_pct  = round(total_gain / total_cost * 100, 2) if total_cost > 0 else 0
        sign = "+" if total_gain >= 0 else ""
        portfolio_block = "\n".join(pos_lines)
        metrics = (
            f"\n  Total Value:    ${total_val:,.2f}\n"
            f"  Total P&L:      {sign}${total_gain:,.2f} ({sign}{total_pct:.1f}%)\n"
            f"  TFSA Room Left: ${room:,.2f}"
        )
    else:
        portfolio_block = "  (no positions)"
        metrics = f"\n  Cash: ${cash:,.2f}\n  TFSA Room Left: ${room:,.2f}"

    if overnight_signals:
        sig_block = "\n".join(f"  {s}" for s in overnight_signals)
    else:
        sig_block = "  ✅ No signals overnight — all positions stable."

    ticker_count = len(holdings_with_prices) + len([t for t in ["VFV.TO","XIU.TO","NVDA","MSFT","AAPL","AMZN","META","GOOG","AMD","TSM","SHOP.TO","PLTR"] if True])
    opens_in = _minutes_to_open()

    return (
        "----------------------------------------\n"
        f"☀️  GOOD MORNING — TFSA DAILY BRIEF\n"
        f"{today}\n\n"
        "YOUR PORTFOLIO:\n"
        f"{portfolio_block}"
        f"{metrics}\n\n"
        "OVERNIGHT SIGNALS:\n"
        f"{sig_block}\n\n"
        f"📡 Monitoring {len(WATCHLIST_REF)} tickers today...\n"
        f"{opens_in}\n"
        "----------------------------------------"
    )


# lazy import to avoid circular
def _minutes_to_open():
    now = datetime.now(EASTERN)
    open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now >= open_time:
        return "Market is open."
    delta = open_time - now
    mins = int(delta.total_seconds() / 60)
    return f"Market opens in {mins} minutes."


# Injected by scheduler to avoid circular import
WATCHLIST_REF = []
