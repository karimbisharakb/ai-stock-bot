"""
Flask webhook for inbound Twilio SMS.
Parses commands and replies via TwiML.
"""
import os
import re
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

import portfolio
import alerts
from database import get_connection

app = Flask(__name__)

MY_NUMBER = os.getenv("MY_PHONE_NUMBER", "")


# ──────────────────────────────────────────────
# Webhook
# ──────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    from_number = request.form.get("From", "").strip()
    body        = request.form.get("Body", "").strip()

    resp = MessagingResponse()

    # Only process messages from the owner
    if _normalize(from_number) != _normalize(MY_NUMBER):
        return str(resp)

    reply = handle_command(body)
    if reply:
        resp.message(reply)

    return str(resp)


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}, 200


def _normalize(num: str) -> str:
    return re.sub(r"\D", "", num)


# ──────────────────────────────────────────────
# Command router
# ──────────────────────────────────────────────

def handle_command(text: str) -> str:
    text = text.strip()
    upper = text.upper()

    # BOUGHT <shares> <ticker> at <price>
    m = re.match(
        r"BOUGHT\s+([\d.]+)\s+([A-Z.]+(?:\.TO)?)\s+(?:AT|@)\s+\$?([\d.]+)",
        upper,
    )
    if m:
        return _cmd_bought(m.group(2), float(m.group(1)), float(m.group(3)))

    # SOLD <shares> <ticker> at <price>
    m = re.match(
        r"SOLD\s+([\d.]+)\s+([A-Z.]+(?:\.TO)?)\s+(?:AT|@)\s+\$?([\d.]+)",
        upper,
    )
    if m:
        return _cmd_sold(m.group(2), float(m.group(1)), float(m.group(3)))

    # PORTFOLIO / HOLDINGS
    if upper in ("PORTFOLIO", "HOLDINGS", "P"):
        return portfolio.format_portfolio_summary()

    # ROOM <amount>
    m = re.match(r"ROOM\s+\$?([\d,]+(?:\.\d+)?)", upper)
    if m:
        amount = float(m.group(1).replace(",", ""))
        return _cmd_room(amount)

    # IGNORE <ticker>
    m = re.match(r"IGNORE\s+([A-Z.]+(?:\.TO)?)", upper)
    if m:
        return _cmd_ignore(m.group(1))

    # HELP
    if upper == "HELP":
        return _cmd_help()

    # Budget — plain number or "I added $500" / "I funded 500"
    m = re.match(
        r"(?:I\s+(?:ADDED|FUNDED|DEPOSITED|HAVE)\s+)?\$?([\d,]+(?:\.\d+)?)",
        upper,
    )
    if m:
        amount = float(m.group(1).replace(",", ""))
        if amount >= 50:
            return _cmd_budget(amount)

    return (
        "❓ Command not recognized.\n"
        "Text HELP to see available commands."
    )


# ──────────────────────────────────────────────
# Handlers
# ──────────────────────────────────────────────

def _cmd_bought(ticker: str, shares: float, price: float) -> str:
    result = portfolio.add_or_update_holding(ticker, shares, price)
    return (
        f"✅ Added {shares} {ticker} @ ${price:.2f}.\n"
        f"Avg cost updated to ${result['avg_cost']:.2f} "
        f"({result['shares']} shares total)."
    )


def _cmd_sold(ticker: str, shares: float, price: float) -> str:
    result = portfolio.reduce_or_remove_holding(ticker, shares, price)
    if "error" in result:
        return f"❌ {result['error']}"
    sign = "+" if result["gain_loss"] >= 0 else ""
    return (
        f"✅ SOLD {shares} {ticker} @ ${price:.2f}\n"
        f"Gain/Loss: {sign}${result['gain_loss']:.2f} ({sign}{result['pct']:.1f}%)\n"
        f"Cash freed up: ${result['proceeds']:.2f}\n"
        f"Text me a budget when ready to redeploy."
    )


def _cmd_room(amount: float) -> str:
    portfolio.set_tfsa_room(amount)
    return f"✅ TFSA room updated to ${amount:,.2f}"


def _cmd_ignore(ticker: str) -> str:
    alerts.snooze_ticker(ticker, hours=24)
    return f"🔕 {ticker} alerts snoozed for 24 hours"


def _cmd_budget(amount: float) -> str:
    portfolio.add_cash(amount)
    cash  = portfolio.get_cash()
    room  = portfolio.get_tfsa_room()
    holdings = portfolio.get_holdings()
    existing_tickers = [h["ticker"] for h in holdings]

    # Warn about TFSA room
    warning = ""
    if room > 0 and amount > room:
        warning = f"⚠️  ${amount:,.2f} exceeds your TFSA room of ${room:,.2f}!\n\n"

    # Run strategy (this takes a moment)
    try:
        from strategy import rank_buy_opportunities
        recs = rank_buy_opportunities(amount, existing_tickers)
    except Exception as e:
        return f"{warning}✅ ${amount:,.2f} added to cash.\n⚠️  Strategy error: {e}"

    return warning + alerts.format_trade_recommendations(amount, recs, room)


def _cmd_help() -> str:
    return (
        "📋 AVAILABLE COMMANDS:\n\n"
        "BOUGHT 3 VFV.TO at 162.50\n"
        "SOLD 2 SHOP.TO at 125.00\n"
        "PORTFOLIO  — view all holdings\n"
        "500  — get buy recs for $500 budget\n"
        "ROOM 7000  — update TFSA room\n"
        "IGNORE VFV.TO  — snooze 24h alerts\n"
        "HELP  — this message"
    )
