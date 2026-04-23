"""
Proactive sell signal monitor.
Runs every 15 minutes during market hours (9:30–16:00 ET, weekdays).
"""
from datetime import datetime
import pytz
import portfolio
import alerts
from market_data import get_index_day_change
from strategy import get_sell_signals

EASTERN = pytz.timezone("America/Toronto")

# Broad-market crash threshold
CRASH_THRESHOLD = -1.5

# Risk classification for crash alert
RISK_MAP = {
    "VFV.TO":  ("High",   "tracks S&P 500"),
    "XIU.TO":  ("High",   "tracks TSX"),
    "XQQ.TO":  ("High",   "tracks NASDAQ"),
    "XEQT.TO": ("High",   "all-equity global"),
    "VEQT.TO": ("High",   "all-equity global"),
    "ZQQ.TO":  ("High",   "tracks NASDAQ"),
    "HXS.TO":  ("High",   "tracks S&P 500"),
    "QQQ":     ("High",   "tracks NASDAQ"),
    "SPY":     ("High",   "tracks S&P 500"),
    "SHOP.TO": ("Medium", "tech volatile in downturns"),
    "NVDA":    ("Medium", "semiconductor cyclical"),
    "AMD":     ("Medium", "semiconductor cyclical"),
    "PLTR":    ("Medium", "high-beta growth"),
}


def is_market_hours() -> bool:
    now = datetime.now(EASTERN)
    if now.weekday() >= 5:
        return False
    open_time  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_time = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_time <= now <= close_time


def run_sell_monitor():
    if not is_market_hours():
        return

    print(f"👁️  Sell monitor running @ {datetime.now(EASTERN).strftime('%H:%M')}")

    holdings = portfolio.get_holdings()
    if not holdings:
        return

    # ── Check broad market crash ──────────────────────────
    changes = get_index_day_change()
    crashed = {k: v for k, v in changes.items() if v <= CRASH_THRESHOLD}

    if crashed and alerts.can_send_alert("MARKET", "URGENT"):
        at_risk = []
        for h in holdings:
            t = h["ticker"]
            risk, reason = RISK_MAP.get(t, ("Low", "individual stock"))
            if risk in ("High", "Medium"):
                at_risk.append({"ticker": t, "risk": risk, "reason": reason})
        if at_risk:
            msg = alerts.format_market_crash_alert(changes, at_risk)
            if alerts.send_sms(msg):
                alerts.log_alert("MARKET", "URGENT", msg)

    # ── Check individual holdings ─────────────────────────
    for h in holdings:
        ticker   = h["ticker"]
        avg_cost = h["avg_cost"]
        shares   = h["shares"]

        signals, urgency, data = get_sell_signals(ticker, avg_cost)

        if urgency is None:
            continue

        if not alerts.can_send_alert(ticker, urgency):
            continue

        if urgency == "URGENT":
            msg = alerts.format_sell_alert(ticker, shares, avg_cost, data, signals)
            if alerts.send_sms(msg):
                alerts.log_alert(ticker, urgency, msg)

        elif urgency == "WARNING":
            # Bundle into morning summary — just log for now; scheduler picks them up
            alerts.log_alert(ticker, urgency, f"{ticker}: {signals[0]}")
            print(f"  🟡 {ticker}: {signals[0]} — queued for morning summary")
