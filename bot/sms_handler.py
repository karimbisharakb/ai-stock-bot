"""
Flask webhook for inbound Twilio WhatsApp messages.
Parses commands and replies via TwiML.
"""
import os
import re
import logging
import traceback
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

import portfolio
import alerts
from analyst import analyze_stock
from database import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

app = Flask(__name__)

MY_WHATSAPP = "whatsapp:+12899718200"


# ──────────────────────────────────────────────
# Webhook
# ──────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    from_number = request.form.get("From", "").strip()
    body        = request.form.get("Body", "").strip()
    log.info("Webhook hit: from=%r body=%r", from_number, body[:120])

    resp = MessagingResponse()

    if from_number != MY_WHATSAPP:
        log.warning("Rejected: from=%r expected=%r", from_number, MY_WHATSAPP)
        return str(resp)

    try:
        reply = handle_command(body)
        if reply:
            resp.message(reply)
            log.info("Reply queued (%d chars)", len(reply))
        else:
            log.warning("handle_command returned empty for body=%r", body)
    except Exception:
        log.error("Unhandled error handling command:\n%s", traceback.format_exc())
        resp.message("❌ Internal error — check Railway logs.")

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

    # BOUGHT <shares> <ticker> at <price> [USD|CAD]
    m = re.match(
        r"BOUGHT\s+([\d.]+)\s+([A-Z.]+(?:\.TO)?)\s+(?:AT|@)\s+\$?([\d.]+)(?:\s+(USD|CAD))?",
        upper,
    )
    if m:
        ticker   = m.group(2)
        currency = m.group(4) or ("CAD" if ticker.endswith(".TO") else "USD")
        return _cmd_bought(ticker, float(m.group(1)), float(m.group(3)), currency)

    # DIVIDEND <shares> <ticker> at <price>
    m = re.match(
        r"DIVIDEND\s+([\d.]+)\s+([A-Z.]+(?:\.TO)?)\s+(?:AT|@)\s+\$?([\d.]+)",
        upper,
    )
    if m:
        return _cmd_dividend(m.group(2), float(m.group(1)), float(m.group(3)))

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

    # ANALYZE / ANALYSE <ticker>
    m = re.match(r"ANALY[ZS]E\s+([A-Z.]+(?:\.TO)?)", upper)
    if m:
        return _cmd_analyze(m.group(1))

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

def _cmd_bought(ticker: str, shares: float, price: float, currency: str = "CAD") -> str:
    if currency == "USD":
        from strategy import get_usd_cad_rate
        fx_rate      = get_usd_cad_rate()
        fx_with_fee  = round(fx_rate * 1.015, 6)   # 1.5% Wealthsimple FX markup
        price_cad    = round(price * fx_with_fee, 4)
        notes        = f"USD {price:.4f} × {fx_with_fee:.4f} (rate {fx_rate:.4f} + 1.5% fee) = CAD {price_cad:.4f}"
    else:
        price_cad = price
        notes     = None

    result = portfolio.add_or_update_holding(ticker, shares, price_cad, notes=notes)

    reply = f"✅ Added {shares} {ticker} @ ${price:.4f} {currency}\n"
    if currency == "USD":
        reply += f"CAD: ${price_cad:.4f} (rate {fx_rate:.4f} + 1.5% fee)\n"
    reply += (
        f"Avg cost: ${result['avg_cost']:.4f} CAD "
        f"({result['shares']} shares total)"
    )
    return reply


def _cmd_dividend(ticker: str, shares: float, price_cad: float) -> str:
    result  = portfolio.add_dividend(ticker, shares, price_cad)
    total   = round(shares * price_cad, 4)
    return (
        f"✅ Dividend reinvestment recorded\n"
        f"{shares} {ticker} @ ${price_cad:.4f} CAD = ${total:.4f} total\n"
        f"Avg cost: ${result['avg_cost']:.4f} ({result['shares']} shares total)"
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


def _cmd_analyze(ticker: str) -> str:
    return analyze_stock(ticker)


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
        "BOUGHT 1.5175 AAPL at 270.1585 USD\n"
        "BOUGHT 3 VFV.TO at 162.50\n"
        "SOLD 2 SHOP.TO at 125.00\n"
        "DIVIDEND 0.0047 VFV.TO at 130.97\n"
        "PORTFOLIO  — view all holdings\n"
        "500  — get buy recs for $500 budget\n"
        "ROOM 7000  — update TFSA room\n"
        "IGNORE VFV.TO  — snooze 24h alerts\n"
        "ANALYZE NVDA  — full AI stock analysis\n"
        "HELP  — this message"
    )
