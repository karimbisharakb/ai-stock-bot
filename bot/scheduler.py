"""
APScheduler setup.
Jobs:
  - Morning summary at 8:45 AM ET (weekdays)
  - Sell monitor every 15 min during market hours (weekdays)
"""
from datetime import datetime, date, timedelta
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import portfolio
import alerts
from sell_monitor import run_sell_monitor
from scanner import run_scanner
from strategy import WATCHLIST

EASTERN = pytz.timezone("America/Toronto")

# Give alerts.py access to the watchlist size
alerts.WATCHLIST_REF = WATCHLIST


def morning_summary_job():
    print(f"☀️  Morning summary job @ {datetime.now(EASTERN).strftime('%H:%M')}")
    holdings = portfolio.get_portfolio_with_prices()
    cash     = portfolio.get_cash()
    room     = portfolio.get_tfsa_room()

    # Collect WARNING signals logged overnight / this morning
    from database import get_connection
    today_start = datetime.now(EASTERN).replace(hour=0, minute=0, second=0, microsecond=0)
    conn = get_connection()
    rows = conn.execute(
        "SELECT ticker, message FROM alert_log "
        "WHERE urgency = 'WARNING' AND sent_at >= ? "
        "ORDER BY sent_at DESC",
        (today_start.isoformat(),),
    ).fetchall()
    conn.close()

    overnight_signals = []
    for row in rows:
        ticker  = row["ticker"] or ""
        message = row["message"] or ""
        if ticker and message:
            short = message[:80]
            overnight_signals.append(f"🟡 {short}")

    msg = alerts.format_morning_summary(holdings, overnight_signals, cash, room)
    if alerts.send_sms(msg, bypass_quiet=True):
        alerts.log_alert(None, "FYI", msg)


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=EASTERN)

    # Morning summary — 9:00 AM ET, Mon–Fri
    scheduler.add_job(
        morning_summary_job,
        CronTrigger(hour=9, minute=0, day_of_week="mon-fri", timezone=EASTERN),
        id="morning_summary",
        replace_existing=True,
    )

    # Afternoon summary — 2:00 PM ET, Mon–Fri
    scheduler.add_job(
        morning_summary_job,
        CronTrigger(hour=14, minute=0, day_of_week="mon-fri", timezone=EASTERN),
        id="afternoon_summary",
        replace_existing=True,
    )

    # Evening summary — 7:00 PM ET, Mon–Fri
    scheduler.add_job(
        morning_summary_job,
        CronTrigger(hour=19, minute=0, day_of_week="mon-fri", timezone=EASTERN),
        id="evening_summary",
        replace_existing=True,
    )

    # Sell monitor — every 15 min, Mon–Fri 9:30–16:00
    scheduler.add_job(
        run_sell_monitor,
        CronTrigger(
            minute="*/15",
            hour="9-16",
            day_of_week="mon-fri",
            timezone=EASTERN,
        ),
        id="sell_monitor",
        replace_existing=True,
    )

    # Stock discovery scanner — every 30 min, Mon–Fri 7:00–20:00
    scheduler.add_job(
        run_scanner,
        CronTrigger(
            minute="*/30",
            hour="7-20",
            day_of_week="mon-fri",
            timezone=EASTERN,
        ),
        id="scanner",
        replace_existing=True,
    )

    scheduler.start()
    print("✅ Scheduler started (summaries at 9:00 AM, 2:00 PM, 7:00 PM ET | sell monitor every 15 min | scanner every 30 min)")
    return scheduler
