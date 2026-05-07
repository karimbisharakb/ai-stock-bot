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
from predator import run_predator, update_outcomes
from strategy import WATCHLIST
from database import get_connection

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


def watchlist_check_job():
    """Check price alerts every 15 min during market hours."""
    try:
        import market_data as md
        conn = get_connection()
        items = conn.execute(
            "SELECT id, ticker, alert_price, direction, note FROM watchlist WHERE triggered = 0"
        ).fetchall()
        conn.close()

        if not items:
            return

        for item in items:
            try:
                data = md.get_ticker_data(item["ticker"])
                if not data:
                    continue
                price = data["price"]
                triggered = (
                    (item["direction"] == "above" and price >= item["alert_price"]) or
                    (item["direction"] == "below" and price <= item["alert_price"])
                )
                if not triggered:
                    continue

                now = datetime.now(EASTERN).isoformat()
                conn = get_connection()
                conn.execute(
                    "UPDATE watchlist SET triggered=1, triggered_at=? WHERE id=?",
                    (now, item["id"]),
                )
                conn.commit()
                conn.close()

                direction_word = "above" if item["direction"] == "above" else "below"
                note_part = f"\n📝 {item['note']}" if item["note"] else ""
                msg = (
                    f"🔔 Price Alert: {item['ticker']}\n"
                    f"${price:.2f} is {direction_word} your target of ${item['alert_price']:.2f}{note_part}"
                )
                alerts.send_sms(msg)
                alerts.log_alert(item["ticker"], "FYI", msg)
                print(f"📣 Watchlist alert triggered: {item['ticker']} @ ${price:.2f}")
            except Exception as e:
                print(f"watchlist_check_job error for {item['ticker']}: {e}")
    except Exception as e:
        print(f"watchlist_check_job error: {e}")


def weekly_summary_job():
    """Every Sunday 9 AM ET — send a Claude-generated weekly portfolio review via WhatsApp."""
    import os
    import anthropic

    print(f"📅 Weekly summary job @ {datetime.now(EASTERN).strftime('%H:%M')}")
    try:
        holdings = portfolio.get_portfolio_with_prices()
        cash = portfolio.get_cash()

        if not holdings:
            msg = "📅 Weekly Summary\n\nNo holdings to review. Add some investments to get started!"
            alerts.send_sms(msg, bypass_quiet=True)
            alerts.log_alert(None, "FYI", msg)
            return

        # Build data for Claude
        total_value = cash
        best_ticker, best_pct = "", -999
        worst_ticker, worst_pct = "", 999

        lines = []
        for h in holdings:
            ticker = h.get("ticker", "")
            shares = h.get("shares", 0)
            avg_cost = h.get("avg_cost", 0)
            current = h.get("current_price", avg_cost)
            value = round(shares * current, 2)
            gain_pct = round((current - avg_cost) / avg_cost * 100, 1) if avg_cost else 0
            total_value += value
            lines.append(f"  {ticker}: ${value:.0f} ({gain_pct:+.1f}%)")
            if gain_pct > best_pct:
                best_pct, best_ticker = gain_pct, ticker
            if gain_pct < worst_pct:
                worst_pct, worst_ticker = gain_pct, ticker

        holdings_text = "\n".join(lines)

        # Predator accuracy this week
        try:
            conn = get_connection()
            week_ago = (datetime.now(EASTERN) - timedelta(days=7)).isoformat()
            total_alerts = conn.execute(
                "SELECT COUNT(*) FROM predator_alerts WHERE alert_time >= ?", (week_ago,)
            ).fetchone()[0]
            won = conn.execute(
                "SELECT COUNT(*) FROM predator_alerts WHERE alert_time >= ? AND outcome = 'WIN'",
                (week_ago,),
            ).fetchone()[0]
            conn.close()
            accuracy_str = f"{won}/{total_alerts} alerts won" if total_alerts else "no alerts this week"
        except Exception:
            accuracy_str = "N/A"

        api_key = os.getenv("ANTHROPIC_API_KEY")
        claude_comment = ""
        if api_key:
            client = anthropic.Anthropic(api_key=api_key)
            prompt = (
                f"Weekly TFSA portfolio review for a Canadian investor.\n\n"
                f"Portfolio value: ${total_value:.2f} CAD\n"
                f"Cash: ${cash:.2f} CAD\n"
                f"Holdings:\n{holdings_text}\n"
                f"Best performer: {best_ticker} ({best_pct:+.1f}%)\n"
                f"Worst performer: {worst_ticker} ({worst_pct:+.1f}%)\n"
                f"Predator AI accuracy this week: {accuracy_str}\n\n"
                "Write a brief (4-5 sentence) weekly review. Include:\n"
                "1. Overall portfolio health\n"
                "2. One insight about the best/worst performer\n"
                "3. One actionable recommendation for the coming week\n"
                "Keep it concise and practical. No emojis in body text."
            )
            try:
                resp = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=300,
                    messages=[{"role": "user", "content": prompt}],
                )
                claude_comment = resp.content[0].text.strip()
            except Exception as e:
                print(f"weekly_summary Claude error: {e}")

        msg = (
            f"📅 Weekly Portfolio Review\n\n"
            f"💰 Total: ${total_value:,.2f} CAD\n"
            f"💵 Cash: ${cash:,.2f} CAD\n\n"
            f"📈 Best: {best_ticker} ({best_pct:+.1f}%)\n"
            f"📉 Worst: {worst_ticker} ({worst_pct:+.1f}%)\n"
            f"🤖 AI accuracy: {accuracy_str}\n\n"
        )
        if claude_comment:
            msg += claude_comment

        alerts.send_sms(msg, bypass_quiet=True)
        alerts.log_alert(None, "FYI", msg)
        print(f"✅ Weekly summary sent ({len(msg)} chars)")

    except Exception as e:
        print(f"weekly_summary_job error: {e}")


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=EASTERN)

    # Morning summary — 8:45 AM ET, Mon–Fri
    scheduler.add_job(
        morning_summary_job,
        CronTrigger(hour=8, minute=45, day_of_week="mon-fri", timezone=EASTERN),
        id="morning_summary",
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

    # Pre-explosion scanner — every 60 min, Mon–Fri 8:00–20:00
    scheduler.add_job(
        run_predator,
        CronTrigger(
            minute="0",
            hour="8-20",
            day_of_week="mon-fri",
            timezone=EASTERN,
        ),
        id="predator",
        replace_existing=True,
    )

    # Daily 7 AM: update outcomes for alerts from 7/14/30 days ago + early predator run
    scheduler.add_job(
        update_outcomes,
        CronTrigger(hour=7, minute=0, day_of_week="mon-fri", timezone=EASTERN),
        id="predator_outcomes",
        replace_existing=True,
    )

    # Watchlist price alerts — every 15 min, Mon–Fri 9:30–16:00
    scheduler.add_job(
        watchlist_check_job,
        CronTrigger(
            minute="*/15",
            hour="9-16",
            day_of_week="mon-fri",
            timezone=EASTERN,
        ),
        id="watchlist_check",
        replace_existing=True,
    )

    # Weekly summary — every Sunday 9:00 AM ET
    scheduler.add_job(
        weekly_summary_job,
        CronTrigger(hour=9, minute=0, day_of_week="sun", timezone=EASTERN),
        id="weekly_summary",
        replace_existing=True,
    )

    scheduler.start()
    print(
        "✅ Scheduler started (morning summary 8:45 ET | sell monitor every 15 min | "
        "scanner every 30 min | predator every 60 min | "
        "watchlist check every 15 min | weekly summary Sundays 9 AM)"
    )
    return scheduler
