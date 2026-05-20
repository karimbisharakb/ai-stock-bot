"""
APScheduler setup.
Jobs:
  - Morning summary at 8:45 AM ET (weekdays)
  - Sell monitor every 15 min during market hours (weekdays)
"""
import logging
import os
import socket
from datetime import datetime, date, timedelta
from typing import Optional

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

log = logging.getLogger(__name__)
EASTERN = pytz.timezone("America/Toronto")

# Give alerts.py access to the watchlist size
alerts.WATCHLIST_REF = WATCHLIST


def morning_summary_job():
    print(f"☀️  Morning summary job @ {datetime.now(EASTERN).strftime('%H:%M')}")
    from operator_brief import generate_compact_brief
    msg = generate_compact_brief()
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
                cursor = conn.execute(
                    "UPDATE watchlist SET triggered=1, triggered_at=? WHERE id=? AND triggered=0",
                    (now, item["id"]),
                )
                conn.commit()
                conn.close()

                if cursor.rowcount == 0:
                    # Another scheduler already claimed this row
                    continue

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


def _pid_alive(pid: int) -> bool:
    """Return True if a process with this PID exists on the current host.

    os.kill(pid, 0) raises:
      ProcessLookupError (ESRCH)  — process does not exist → dead
      PermissionError    (EPERM)  — process exists but we can't signal it → alive
      OSError            (other)  — treat as dead to be safe
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists; we just lack permission to signal it
    except OSError:
        return False


def _try_claim_lease() -> bool:
    """
    Claim the scheduler lease using PID liveness on same host, or by
    taking over a stale lease from a different host (redeploy scenario).

    Returns True if this process should start the scheduler.
    Fails open: if the lease table is missing or the query errors, returns
    True so the scheduler always starts rather than silently disappearing.
    """
    pid  = os.getpid()
    host = socket.gethostname()
    now  = datetime.now().isoformat()

    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT pid, hostname, acquired_at FROM scheduler_lease WHERE id = 1"
            ).fetchone()

            if row is not None:
                owner_pid  = row["pid"]
                owner_host = row["hostname"]

                if owner_pid == pid and owner_host == host:
                    # Same process calling start_scheduler() twice — already own the lease.
                    return True

                if owner_host == host:
                    if _pid_alive(owner_pid):
                        log.info(
                            "Scheduler: lease held by pid=%d on this host — "
                            "this worker will not start a scheduler",
                            owner_pid,
                        )
                        return False
                    log.warning(
                        "Scheduler: previous owner pid=%d is gone — reclaiming lease",
                        owner_pid,
                    )
                else:
                    log.info(
                        "Scheduler: lease from host=%r — claiming for this deployment",
                        owner_host,
                    )

            conn.execute(
                "INSERT OR REPLACE INTO scheduler_lease (id, pid, hostname, acquired_at) "
                "VALUES (1, ?, ?, ?)",
                (pid, host, now),
            )
            conn.commit()
            log.info("Scheduler: lease acquired (pid=%d host=%s)", pid, host)
            return True
        finally:
            conn.close()
    except Exception:
        log.error(
            "Scheduler: lease claim failed due to DB error — not starting scheduler. "
            "Railway will restart the process and retry.",
            exc_info=True,
        )
        return False  # fail closed: duplicate schedulers > no scheduler, but DB errors need investigation


def release_scheduler_lease() -> None:
    """
    Delete the lease row if this process owns it.
    Called from wsgi.py atexit so Railway SIGTERM triggers a clean handoff.
    """
    pid  = os.getpid()
    host = socket.gethostname()
    try:
        conn = get_connection()
        try:
            conn.execute(
                "DELETE FROM scheduler_lease WHERE id = 1 AND pid = ? AND hostname = ?",
                (pid, host),
            )
            conn.commit()
            log.info("Scheduler: lease released (pid=%d)", pid)
        finally:
            conn.close()
    except Exception as exc:
        log.warning("Scheduler: lease release failed: %s", exc)


def _run_regime_refresh():
    """Refresh market regime snapshot (non-fatal — never blocks Alpha or Predator)."""
    try:
        from market_regime_intelligence import refresh_regime
        result = refresh_regime()
        log.info(
            "Regime refresh complete: %s | score=%.1f | quality=%s",
            result.get("overall_regime"),
            result.get("regime_score", 0),
            result.get("data_quality"),
        )
    except Exception:
        log.warning("Regime refresh failed (non-fatal)", exc_info=True)


def _run_alpha_universe_scan():
    """Scan ALPHA_UNIVERSE in alpha shadow mode, then queue outcome tracking rows."""
    try:
        from alpha_universe import scan_alpha_universe
        count = scan_alpha_universe()
        log.info("Alpha universe scan complete: %d tickers scored", count)
    except Exception:
        log.warning("Alpha universe scan failed (non-fatal)", exc_info=True)
        return

    # After scan, record pending outcomes for top candidates (non-fatal)
    try:
        from alpha_shadow import get_shadow_manager
        from alpha_outcomes import insert_pending_outcomes
        candidates = get_shadow_manager().get_top_candidates(limit=20)
        n = insert_pending_outcomes(candidates)
        log.info("Alpha outcomes: inserted %d pending rows after universe scan", n)
    except Exception:
        log.warning("Alpha outcomes insert failed (non-fatal)", exc_info=True)


def _run_alpha_daily_tasks():
    """Daily alpha maintenance: update outcome prices and log monitoring report."""
    # Update return windows for PENDING outcomes
    try:
        from alpha_outcomes import update_outcome_prices
        stats = update_outcome_prices()
        log.info("Alpha outcome price update: %s", stats)
    except Exception:
        log.warning("Alpha outcome price update failed (non-fatal)", exc_info=True)

    # Generate and log the daily monitoring report
    try:
        from alpha_monitor import generate_alpha_report
        report = generate_alpha_report()
        diag = report.get("diagnosis", [])
        recs = report.get("recommendations", [])
        log.info(
            "Alpha daily report: %d tickers scored | %d issues | %d recommendations",
            report.get("summary", {}).get("total_unique_scored", 0),
            len(diag), len(recs),
        )
        for issue in diag:
            log.info("Alpha diagnosis [%s]: %s", issue["severity"], issue["message"])
        for rec in recs:
            log.info("Alpha recommendation: %s", rec)
    except Exception:
        log.warning("Alpha daily report failed (non-fatal)", exc_info=True)


def start_scheduler() -> Optional[BackgroundScheduler]:
    if not _try_claim_lease():
        print("⏭️  Scheduler not started — lease held by another worker on this host")
        return None

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

    # Alpha universe scan — every 4 hours, Mon–Fri 8:00–20:00
    scheduler.add_job(
        _run_alpha_universe_scan,
        CronTrigger(
            hour="8,12,16,20",
            minute="0",
            day_of_week="mon-fri",
            timezone=EASTERN,
        ),
        id="alpha_universe_scan",
        replace_existing=True,
    )

    # Alpha daily tasks — 9:30 AM ET weekdays (after market open, before predator peak)
    # Updates outcome return windows and logs quality report. Never blocks Predator.
    scheduler.add_job(
        _run_alpha_daily_tasks,
        CronTrigger(hour=9, minute=30, day_of_week="mon-fri", timezone=EASTERN),
        id="alpha_daily_tasks",
        replace_existing=True,
    )

    # Market regime refresh — pre-market, midday, near-close (Mon-Fri)
    # Non-fatal; never blocks Alpha or Predator.
    scheduler.add_job(
        _run_regime_refresh,
        CronTrigger(hour=8, minute=0, day_of_week="mon-fri", timezone=EASTERN),
        id="regime_refresh_premarket",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_regime_refresh,
        CronTrigger(hour=12, minute=0, day_of_week="mon-fri", timezone=EASTERN),
        id="regime_refresh_midday",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_regime_refresh,
        CronTrigger(hour=15, minute=30, day_of_week="mon-fri", timezone=EASTERN),
        id="regime_refresh_nearclose",
        replace_existing=True,
    )

    scheduler.start()
    print(
        "✅ Scheduler started (morning summary 8:45 ET | sell monitor every 15 min | "
        "scanner every 30 min | predator every 60 min | "
        "watchlist check every 15 min | weekly summary Sundays 9 AM | "
        "alpha universe scan every 4 h Mon-Fri | alpha daily tasks 9:30 AM Mon-Fri | "
        "regime refresh 8:00/12:00/15:30 ET Mon-Fri)"
    )
    return scheduler
