"""
bot/weekly_review.py — Phase A26: Weekly Review and Accountability Report

Generates a weekly accountability report covering portfolio performance,
alpha activity, validation outcomes, notification pipeline, checklist
discipline, research workflow, thesis reviews, market context, and
retrospective analysis (mistakes, wins, missed opportunities).

Three modes: compact (WhatsApp ≤ COMPACT_MAX_CHARS), detailed (full dict),
debug (detailed + data-source metadata).

Feature flag: WEEKLY_REVIEW_ENABLED (default false) — controls scheduler send.
The API always responds; only the Friday WhatsApp send is suppressed.

Safety: no trade instructions, no broker calls, no order placement.
All output is advisory and retrospective only.
"""
import logging
import os
from datetime import datetime, timedelta
from typing import Any, List, Optional

import pytz

log = logging.getLogger(__name__)
EASTERN = pytz.timezone("America/Toronto")

# ── Configuration ──────────────────────────────────────────────────────────────

MODES  = ["compact", "detailed", "debug"]
GRADES = ["A", "B", "C", "D", "F"]

COMPACT_MAX_CHARS = 1600

BANNED_WORDS: List[str] = [
    "moon", "explosion", "explode", "rocket", "yolo", "hodl", "gem",
    "lambo", "\U0001f680", "\U0001f48e", "to the moon",
]

# Grade thresholds (penalty score from 100)
GRADE_THRESHOLDS = {"A": 90, "B": 75, "C": 60, "D": 45}

# Keys every detailed/debug report must contain
REQUIRED_SECTIONS: List[str] = [
    "portfolio_weekly_change",
    "alpha_generated",
    "alpha_improved",
    "alpha_failed",
    "validation_outcomes",
    "notification_activity",
    "qc_suppressions",
    "delivery_attempts",
    "checklist_discipline",
    "workflow_summary",
    "thesis_summary",
    "watchlist_changes",
    "scorecard_changes",
    "stress_test_changes",
    "planner_drift_changes",
    "regime_changes",
    "key_mistakes",
    "best_decisions",
    "missed_opportunities",
    "focus_next_week",
]

MAX_MISTAKES          = 5
MAX_DECISIONS         = 5
MAX_MISSED            = 5
MAX_FOCUS             = 5
MAX_HISTORY_ROWS      = 52   # one year of weekly reviews in history


# ── Feature flag ───────────────────────────────────────────────────────────────

def weekly_review_enabled() -> bool:
    """Return True when WEEKLY_REVIEW_ENABLED=true is set in the environment."""
    return os.getenv("WEEKLY_REVIEW_ENABLED", "false").lower() == "true"


# ── Utility helpers ────────────────────────────────────────────────────────────

def check_banned_words(text: str) -> List[str]:
    lower = text.lower()
    return [w for w in BANNED_WORDS if w.lower() in lower]


def _has_banned_word(text: str) -> bool:
    return bool(check_banned_words(text))


def _safe_truncate(text: str, max_len: int) -> str:
    if not text:
        return ""
    return text[:max_len] + ("…" if len(text) > max_len else "")


def _sign(val: float) -> str:
    return "+" if val >= 0 else ""


# ── Week boundary helpers ──────────────────────────────────────────────────────

def _parse_week_start(week_start_str: Optional[str]) -> str:
    """
    Return "YYYY-MM-DD" for the Monday of the given date's week (or current week).
    The input date can be any day of the target week.
    """
    try:
        if week_start_str:
            dt = datetime.strptime(week_start_str[:10], "%Y-%m-%d")
            monday = dt - timedelta(days=dt.weekday())   # weekday() 0 = Monday
            return monday.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        pass
    now_et = datetime.now(EASTERN)
    monday = now_et - timedelta(days=now_et.weekday())
    return monday.strftime("%Y-%m-%d")


def _week_end(week_start: str) -> str:
    """Return the exclusive end of the week (next Monday, "YYYY-MM-DD")."""
    dt = datetime.strptime(week_start, "%Y-%m-%d") + timedelta(days=7)
    return dt.strftime("%Y-%m-%d")


def _week_label(week_start: str, week_end_excl: str) -> str:
    """Human-readable label like "May 12–18, 2026"."""
    try:
        s = datetime.strptime(week_start, "%Y-%m-%d")
        # week_end_excl is exclusive (next Monday); display day before
        e = datetime.strptime(week_end_excl, "%Y-%m-%d") - timedelta(days=1)
        if s.month == e.month:
            return f"{s.strftime('%b %-d')}–{e.strftime('%-d, %Y')}"
        return f"{s.strftime('%b %-d')} – {e.strftime('%b %-d, %Y')}"
    except Exception:
        return week_start


# ── Table bootstrap ────────────────────────────────────────────────────────────

def _ensure_sends_table() -> None:
    """Create weekly_review_sends table if it doesn't exist (idempotent)."""
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weekly_review_sends (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start  TEXT    NOT NULL UNIQUE,
                sent_at     TEXT    NOT NULL,
                grade       TEXT,
                mode        TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wrs_week_start ON weekly_review_sends(week_start)"
        )
        conn.commit()
    finally:
        conn.close()


# ── Duplicate-send suppression ─────────────────────────────────────────────────

def _already_sent_this_week(week_start: str) -> bool:
    """Return True if a weekly review was already sent for this week."""
    try:
        _ensure_sends_table()
        from database import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT id FROM weekly_review_sends WHERE week_start = ?", (week_start,)
            ).fetchone()
        finally:
            conn.close()
        return row is not None
    except Exception as exc:
        log.debug("_already_sent_this_week: %s", exc)
        return False


def _mark_sent(week_start: str, grade: str = "") -> None:
    """Record that the weekly review was sent for this week."""
    try:
        _ensure_sends_table()
        from database import get_connection
        conn = get_connection()
        now = datetime.now(EASTERN).isoformat()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO weekly_review_sends (week_start, sent_at, grade, mode)
                VALUES (?, ?, ?, 'compact')
                """,
                (week_start, now, grade),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log.warning("_mark_sent failed: %s", exc)


def get_review_history(limit: int = MAX_HISTORY_ROWS) -> List[dict]:
    """Return past weekly review send records, newest first."""
    try:
        _ensure_sends_table()
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT week_start, sent_at, grade, mode "
                "FROM weekly_review_sends ORDER BY week_start DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.debug("get_review_history: %s", exc)
        return []


# ── Data collectors ────────────────────────────────────────────────────────────

def _collect_portfolio_change(week_start: str, week_end: str) -> dict:
    result: dict = {
        "available": False,
        "start_value": None, "end_value": None,
        "change_cad": None, "change_pct": None,
    }
    try:
        from database import get_connection
        conn = get_connection()
        try:
            s_row = conn.execute(
                "SELECT value_cad FROM portfolio_history "
                "WHERE date >= ? ORDER BY date ASC LIMIT 1",
                (week_start,),
            ).fetchone()
            e_row = conn.execute(
                "SELECT value_cad FROM portfolio_history "
                "WHERE date < ? ORDER BY date DESC LIMIT 1",
                (week_end,),
            ).fetchone()
        finally:
            conn.close()
        if s_row and e_row:
            sv = float(s_row["value_cad"])
            ev = float(e_row["value_cad"])
            result.update({
                "available":   True,
                "start_value": round(sv, 2),
                "end_value":   round(ev, 2),
                "change_cad":  round(ev - sv, 2),
                "change_pct":  round((ev - sv) / sv * 100, 2) if sv > 0 else 0.0,
            })
    except Exception as exc:
        log.debug("_collect_portfolio_change: %s", exc)
    return result


def _collect_alpha_activity(week_start: str, week_end: str) -> dict:
    """Alpha shadow log activity for the week."""
    result: dict = {
        "generated_count":  0,
        "generated_tickers": [],
        "tier_distribution": {},
        "improved":         [],   # [{ticker, score_start, score_end, change}]
        "tier_snapshots":   {},   # ticker → list of (scan_time, alpha_score)
    }
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT ticker, alpha_score, alpha_tier, scan_time "
                "FROM alpha_shadow_log "
                "WHERE scan_time >= ? AND scan_time < ? "
                "ORDER BY ticker, scan_time",
                (week_start, week_end),
            ).fetchall()
        finally:
            conn.close()

        by_ticker: dict = {}
        for r in rows:
            t = (r["ticker"] or "").upper()
            if t not in by_ticker:
                by_ticker[t] = []
            by_ticker[t].append((r["scan_time"], float(r["alpha_score"] or 0.0), r["alpha_tier"]))

        tiers: dict = {}
        improved: list = []
        for ticker, scans in by_ticker.items():
            if scans:
                # tier distribution (last scan)
                tier = scans[-1][2]
                tiers[tier] = tiers.get(tier, 0) + 1
                # improved: last score > first score
                if len(scans) >= 2:
                    first_score = scans[0][1]
                    last_score  = scans[-1][1]
                    if last_score > first_score:
                        improved.append({
                            "ticker":      ticker,
                            "score_start": round(first_score, 2),
                            "score_end":   round(last_score, 2),
                            "change":      round(last_score - first_score, 2),
                        })

        result.update({
            "generated_count":   len(by_ticker),
            "generated_tickers": sorted(by_ticker.keys()),
            "tier_distribution": tiers,
            "improved":          sorted(improved, key=lambda x: -x["change"]),
        })
    except Exception as exc:
        log.debug("_collect_alpha_activity: %s", exc)
    return result


def _collect_outcomes(week_start: str, week_end: str) -> dict:
    """Alpha outcomes and replay missed winners for the week."""
    result: dict = {
        "completed":           [],
        "completed_count":     0,
        "false_positive_count": 0,
        "positive_count":      0,
        "missed_winners":      [],
        "missed_winner_count": 0,
    }
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT ticker, alpha_tier, return_5d, return_10d, status "
                "FROM alpha_outcomes "
                "WHERE status = 'COMPLETE' AND updated_at >= ? AND updated_at < ?",
                (week_start, week_end),
            ).fetchall()
        finally:
            conn.close()
        completed = [dict(r) for r in rows]
        false_pos = sum(
            1 for o in completed
            if float(o.get("return_5d") or o.get("return_10d") or 0.0) < 0
        )
        positive = sum(
            1 for o in completed
            if float(o.get("return_5d") or o.get("return_10d") or 0.0) > 0
        )
        result.update({
            "completed":           completed,
            "completed_count":     len(completed),
            "false_positive_count": false_pos,
            "positive_count":      positive,
        })
    except Exception as exc:
        log.debug("_collect_outcomes (alpha_outcomes): %s", exc)

    # Replay missed winners
    try:
        from historical_replay import get_replay_runs, get_replay_events
        runs = get_replay_runs(limit=5)
        missed_seen: set = set()
        missed: list = []
        for run in runs:
            run_id = run.get("run_id") or str(run.get("id", ""))
            events = get_replay_events(run_id, limit=200)
            for ev in events:
                if ev.get("outcome_status") != "missed_winner":
                    continue
                ticker = (ev.get("ticker") or "").upper()
                if not ticker or ticker in missed_seen:
                    continue
                # Check if within the week (use run's computed_at or ev's computed_at)
                ev_time = ev.get("computed_at") or run.get("created_at", "")
                if ev_time and not (week_start <= ev_time[:10] < week_end):
                    continue
                missed_seen.add(ticker)
                missed.append({
                    "ticker":    ticker,
                    "return_5d": ev.get("return_5d"),
                    "run_id":    run_id,
                })
        result.update({
            "missed_winners":      missed[:MAX_MISSED],
            "missed_winner_count": len(missed),
        })
    except Exception as exc:
        log.debug("_collect_outcomes (replay): %s", exc)

    return result


def _collect_dryruns(week_start: str, week_end: str) -> dict:
    result: dict = {
        "created_this_week":   0,
        "reviewed_this_week":  0,
        "dismissed_this_week": 0,
        "still_active":        0,
    }
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT status FROM alpha_notification_dryruns "
                "WHERE created_at >= ? AND created_at < ?",
                (week_start, week_end),
            ).fetchall()
            # Also count currently active (not time-bounded)
            active_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM alpha_notification_dryruns "
                "WHERE status = 'DRY_RUN'"
            ).fetchone()
        finally:
            conn.close()
        statuses = [r["status"] for r in rows]
        result.update({
            "created_this_week":   len(statuses),
            "reviewed_this_week":  sum(1 for s in statuses if s == "REVIEWED"),
            "dismissed_this_week": sum(1 for s in statuses if s == "DISMISSED"),
            "still_active":        int((active_row["cnt"] if active_row else 0)),
        })
    except Exception as exc:
        log.debug("_collect_dryruns: %s", exc)
    return result


def _collect_qc(week_start: str, week_end: str) -> dict:
    result: dict = {
        "evaluated_this_week": 0,
        "suppressed_this_week": 0,
        "allowed_this_week":   0,
        "suppression_rate":    0.0,
    }
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT allow_notification FROM notification_qc_history "
                "WHERE evaluated_at >= ? AND evaluated_at < ?",
                (week_start, week_end),
            ).fetchall()
        finally:
            conn.close()
        total    = len(rows)
        allowed  = sum(1 for r in rows if r["allow_notification"])
        suppressed = total - allowed
        result.update({
            "evaluated_this_week":  total,
            "allowed_this_week":    allowed,
            "suppressed_this_week": suppressed,
            "suppression_rate":     round(suppressed / total * 100, 1) if total > 0 else 0.0,
        })
    except Exception as exc:
        log.debug("_collect_qc: %s", exc)
    return result


def _collect_delivery(week_start: str, week_end: str) -> dict:
    result: dict = {"sent_this_week": 0, "by_urgency": {}}
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT urgency FROM alert_log "
                "WHERE sent_at >= ? AND sent_at < ?",
                (week_start, week_end),
            ).fetchall()
        finally:
            conn.close()
        by_urgency: dict = {}
        for r in rows:
            u = r["urgency"] or "UNKNOWN"
            by_urgency[u] = by_urgency.get(u, 0) + 1
        result.update({"sent_this_week": len(rows), "by_urgency": by_urgency})
    except Exception as exc:
        log.debug("_collect_delivery: %s", exc)
    return result


def _collect_checklists(week_start: str, week_end: str) -> dict:
    result: dict = {
        "created_this_week":   0,
        "approved_this_week":  0,
        "rejected_this_week":  0,
        "pending_count":       0,
    }
    try:
        from database import get_connection
        conn = get_connection()
        try:
            created_rows = conn.execute(
                "SELECT checklist_status FROM decision_checklists "
                "WHERE created_at >= ? AND created_at < ?",
                (week_start, week_end),
            ).fetchall()
            # Updated (status changed) within the week
            updated_rows = conn.execute(
                "SELECT checklist_status FROM decision_checklists "
                "WHERE updated_at >= ? AND updated_at < ?",
                (week_start, week_end),
            ).fetchall()
            # Currently pending
            pending_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM decision_checklists "
                "WHERE checklist_status IN ('DRAFT', 'READY')"
            ).fetchone()
        finally:
            conn.close()
        created_statuses = [r["checklist_status"] for r in created_rows]
        updated_statuses = [r["checklist_status"] for r in updated_rows]
        all_statuses = created_statuses + updated_statuses
        result.update({
            "created_this_week":   len(created_rows),
            "approved_this_week":  sum(1 for s in all_statuses if s == "APPROVED"),
            "rejected_this_week":  sum(1 for s in all_statuses if s == "REJECTED"),
            "pending_count":       int(pending_row["cnt"] if pending_row else 0),
        })
    except Exception as exc:
        log.debug("_collect_checklists: %s", exc)
    return result


def _collect_workflow(week_start: str, week_end: str) -> dict:
    result: dict = {
        "completed_this_week": 0,
        "overdue_count":       0,
        "open_count":          0,
        "high_open_count":     0,
        "overdue_items":       [],
        "ignored_high":        [],  # HIGH priority OPEN, created before this week
    }
    try:
        from database import get_connection
        conn = get_connection()
        now_iso = datetime.now(EASTERN).isoformat(timespec="seconds")
        try:
            done_rows = conn.execute(
                "SELECT item_id, ticker, reason FROM research_workflow_items "
                "WHERE status = 'DONE' AND updated_at >= ? AND updated_at < ?",
                (week_start, week_end),
            ).fetchall()
            overdue_rows = conn.execute(
                "SELECT item_id, ticker, reason, due_at FROM research_workflow_items "
                "WHERE status IN ('OPEN','IN_PROGRESS') "
                "AND due_at IS NOT NULL AND due_at < ? "
                "ORDER BY due_at ASC LIMIT 10",
                (now_iso,),
            ).fetchall()
            open_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM research_workflow_items "
                "WHERE status IN ('OPEN','IN_PROGRESS')"
            ).fetchone()
            high_open_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM research_workflow_items "
                "WHERE status IN ('OPEN','IN_PROGRESS') AND priority = 'HIGH'"
            ).fetchone()
            # Ignored: HIGH priority OPEN/IN_PROGRESS, created BEFORE this week
            ignored_rows = conn.execute(
                "SELECT item_id, ticker, reason, created_at FROM research_workflow_items "
                "WHERE status IN ('OPEN','IN_PROGRESS') "
                "AND priority = 'HIGH' AND created_at < ? "
                "ORDER BY created_at ASC LIMIT 10",
                (week_start,),
            ).fetchall()
        finally:
            conn.close()
        result.update({
            "completed_this_week": len(done_rows),
            "overdue_count":       len(overdue_rows),
            "open_count":          int(open_row["cnt"] if open_row else 0),
            "high_open_count":     int(high_open_row["cnt"] if high_open_row else 0),
            "overdue_items":       [dict(r) for r in overdue_rows[:5]],
            "ignored_high":        [dict(r) for r in ignored_rows[:5]],
        })
    except Exception as exc:
        log.debug("_collect_workflow: %s", exc)
    return result


def _collect_thesis(week_start: str, week_end: str) -> dict:
    result: dict = {
        "reviews_completed_this_week": 0,
        "overdue_count":               0,
        "stale_count":                 0,
    }
    try:
        from database import get_connection
        conn = get_connection()
        now_iso = datetime.now(EASTERN).isoformat(timespec="seconds")
        try:
            review_rows = conn.execute(
                "SELECT COUNT(*) as cnt FROM position_journal "
                "WHERE entry_type = 'REVIEW' AND created_at >= ? AND created_at < ?",
                (week_start, week_end),
            ).fetchone()
            overdue_rows = conn.execute(
                "SELECT COUNT(*) as cnt FROM position_theses "
                "WHERE next_review_at IS NOT NULL AND next_review_at < ? "
                "AND status != 'ARCHIVED'",
                (now_iso,),
            ).fetchone()
            stale_cutoff = (
                datetime.now(EASTERN) - timedelta(days=90)
            ).strftime("%Y-%m-%d")
            stale_rows = conn.execute(
                "SELECT COUNT(*) as cnt FROM position_theses "
                "WHERE (updated_at < ? OR updated_at IS NULL) "
                "AND status != 'ARCHIVED'",
                (stale_cutoff,),
            ).fetchone()
        finally:
            conn.close()
        result.update({
            "reviews_completed_this_week": int(review_rows["cnt"] if review_rows else 0),
            "overdue_count":               int(overdue_rows["cnt"] if overdue_rows else 0),
            "stale_count":                 int(stale_rows["cnt"] if stale_rows else 0),
        })
    except Exception as exc:
        log.debug("_collect_thesis: %s", exc)
    return result


def _collect_watchlist(week_start: str, week_end: str) -> dict:
    result: dict = {
        "updated_this_week":  0,
        "archived_this_week": 0,
        "total_active":       0,
    }
    try:
        from database import get_connection
        conn = get_connection()
        try:
            updated_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM research_watchlist "
                "WHERE updated_at >= ? AND updated_at < ?",
                (week_start, week_end),
            ).fetchone()
            archived_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM research_watchlist "
                "WHERE status = 'ARCHIVED' "
                "AND updated_at >= ? AND updated_at < ?",
                (week_start, week_end),
            ).fetchone()
            active_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM research_watchlist "
                "WHERE status != 'ARCHIVED'"
            ).fetchone()
        finally:
            conn.close()
        result.update({
            "updated_this_week":  int(updated_row["cnt"] if updated_row else 0),
            "archived_this_week": int(archived_row["cnt"] if archived_row else 0),
            "total_active":       int(active_row["cnt"] if active_row else 0),
        })
    except Exception as exc:
        log.debug("_collect_watchlist: %s", exc)
    return result


def _collect_scorecards(week_start: str, week_end: str) -> dict:
    result: dict = {"computed_this_week": 0, "top_strategy": None}
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT strategy, computed_at FROM strategy_scorecard_snapshots "
                "WHERE computed_at >= ? AND computed_at < ? "
                "ORDER BY computed_at DESC",
                (week_start, week_end),
            ).fetchall()
        finally:
            conn.close()
        result.update({
            "computed_this_week": len(rows),
            "top_strategy": rows[0]["strategy"] if rows else None,
        })
    except Exception as exc:
        log.debug("_collect_scorecards: %s", exc)
    return result


def _collect_stress(week_start: str, week_end: str) -> dict:
    result: dict = {"runs_this_week": 0, "worst_loss_pct": None}
    try:
        from portfolio_stress_testing import get_stress_history
        runs = get_stress_history(limit=50)
        week_runs = [r for r in runs if week_start <= (r.get("created_at") or "")[:10] < week_end]
        worst = min(
            (float(r.get("worst_loss_pct") or 0.0) for r in week_runs), default=None
        )
        result.update({
            "runs_this_week": len(week_runs),
            "worst_loss_pct": round(worst, 2) if worst is not None else None,
        })
    except Exception as exc:
        log.debug("_collect_stress: %s", exc)
    return result


def _collect_planner(week_start: str, week_end: str) -> dict:
    result: dict = {"runs_this_week": 0, "last_urgency": "NONE", "drift_changed": False}
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT rebalance_urgency FROM planner_snapshots "
                "WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
                (week_start, week_end),
            ).fetchall()
        finally:
            conn.close()
        if rows:
            urgencies = [r["rebalance_urgency"] for r in rows]
            result.update({
                "runs_this_week": len(rows),
                "last_urgency":   urgencies[-1] or "NONE",
                "drift_changed":  len(set(urgencies)) > 1,
            })
    except Exception as exc:
        log.debug("_collect_planner: %s", exc)
    return result


def _collect_regime(week_start: str, week_end: str) -> dict:
    result: dict = {
        "snapshots_this_week": 0,
        "opening_regime":      "NEUTRAL",
        "closing_regime":      "NEUTRAL",
        "regime_changed":      False,
    }
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT overall_regime, regime_score, captured_at "
                "FROM market_regime_snapshots "
                "WHERE captured_at >= ? AND captured_at < ? "
                "ORDER BY captured_at",
                (week_start, week_end),
            ).fetchall()
        finally:
            conn.close()
        if rows:
            first = dict(rows[0])
            last  = dict(rows[-1])
            result.update({
                "snapshots_this_week": len(rows),
                "opening_regime":      first.get("overall_regime", "NEUTRAL"),
                "closing_regime":      last.get("overall_regime", "NEUTRAL"),
                "closing_score":       last.get("regime_score", 50.0),
                "regime_changed":      first.get("overall_regime") != last.get("overall_regime"),
            })
    except Exception as exc:
        log.debug("_collect_regime: %s", exc)
    return result


def _collect_risk_warnings() -> int:
    """Return the count of current unresolved risk warnings (current state)."""
    try:
        from portfolio_risk_guardrails import get_portfolio_risk_report
        report = get_portfolio_risk_report()
        count = 0
        if report.get("cash_warning"):
            count += 1
        if report.get("drawdown_warning"):
            count += 1
        count += len(report.get("theme_warnings", []))
        count += len(report.get("concentration_warnings", []))
        return count
    except Exception as exc:
        log.debug("_collect_risk_warnings: %s", exc)
        return 0


# ── Data collection entry point ────────────────────────────────────────────────

def collect_weekly_data(week_start: str, week_end: str) -> dict:
    """
    Fetch weekly data from all systems.
    Each source is individually wrapped in try/except. Never raises.
    """
    data: dict = {
        "week_start": week_start,
        "week_end":   week_end,
    }
    data["portfolio"]    = _collect_portfolio_change(week_start, week_end)
    data["alpha"]        = _collect_alpha_activity(week_start, week_end)
    data["outcomes"]     = _collect_outcomes(week_start, week_end)
    data["dryruns"]      = _collect_dryruns(week_start, week_end)
    data["qc"]           = _collect_qc(week_start, week_end)
    data["delivery"]     = _collect_delivery(week_start, week_end)
    data["checklists"]   = _collect_checklists(week_start, week_end)
    data["workflow"]     = _collect_workflow(week_start, week_end)
    data["thesis"]       = _collect_thesis(week_start, week_end)
    data["watchlist"]    = _collect_watchlist(week_start, week_end)
    data["scorecards"]   = _collect_scorecards(week_start, week_end)
    data["stress"]       = _collect_stress(week_start, week_end)
    data["planner"]      = _collect_planner(week_start, week_end)
    data["regime"]       = _collect_regime(week_start, week_end)
    data["risk_warnings_unresolved"] = _collect_risk_warnings()
    return data


# ── Pure section builders ──────────────────────────────────────────────────────

def _section_portfolio_weekly_change(data: dict) -> dict:
    p = data.get("portfolio", {})
    return {
        "available":    p.get("available", False),
        "start_value":  p.get("start_value"),
        "end_value":    p.get("end_value"),
        "change_cad":   p.get("change_cad"),
        "change_pct":   p.get("change_pct"),
    }


def _section_alpha_generated(data: dict) -> dict:
    a = data.get("alpha", {})
    return {
        "count":            a.get("generated_count", 0),
        "tickers":          a.get("generated_tickers", [])[:10],
        "tier_distribution": a.get("tier_distribution", {}),
    }


def _section_alpha_improved(data: dict) -> dict:
    a = data.get("alpha", {})
    improved = a.get("improved", [])
    return {
        "count":    len(improved),
        "improved": improved[:5],
    }


def _section_alpha_failed(data: dict) -> dict:
    o = data.get("outcomes", {})
    failed = [
        x for x in o.get("completed", [])
        if float(x.get("return_5d") or x.get("return_10d") or 0.0) < 0
    ]
    return {
        "count":  len(failed),
        "failed": [
            {
                "ticker":    f.get("ticker", ""),
                "return_5d": f.get("return_5d"),
                "return_10d": f.get("return_10d"),
            }
            for f in failed[:5]
        ],
    }


def _section_validation_outcomes(data: dict) -> dict:
    o = data.get("outcomes", {})
    return {
        "completed_count":     o.get("completed_count", 0),
        "false_positive_count": o.get("false_positive_count", 0),
        "positive_count":      o.get("positive_count", 0),
        "outcomes": [
            {
                "ticker":    x.get("ticker", ""),
                "return_5d": x.get("return_5d"),
                "status":    x.get("status", ""),
            }
            for x in o.get("completed", [])[:5]
        ],
    }


def _section_notification_activity(data: dict) -> dict:
    d = data.get("dryruns", {})
    return {
        "created_this_week":   d.get("created_this_week", 0),
        "reviewed_this_week":  d.get("reviewed_this_week", 0),
        "dismissed_this_week": d.get("dismissed_this_week", 0),
        "still_active":        d.get("still_active", 0),
    }


def _section_qc_suppressions(data: dict) -> dict:
    q = data.get("qc", {})
    return {
        "evaluated_this_week":  q.get("evaluated_this_week", 0),
        "suppressed_this_week": q.get("suppressed_this_week", 0),
        "allowed_this_week":    q.get("allowed_this_week", 0),
        "suppression_rate":     q.get("suppression_rate", 0.0),
    }


def _section_delivery_attempts(data: dict) -> dict:
    d = data.get("delivery", {})
    return {
        "sent_this_week": d.get("sent_this_week", 0),
        "by_urgency":     d.get("by_urgency", {}),
    }


def _section_checklist_discipline(data: dict) -> dict:
    c = data.get("checklists", {})
    return {
        "created_this_week":   c.get("created_this_week", 0),
        "approved_this_week":  c.get("approved_this_week", 0),
        "rejected_this_week":  c.get("rejected_this_week", 0),
        "pending_count":       c.get("pending_count", 0),
    }


def _section_workflow_summary(data: dict) -> dict:
    w = data.get("workflow", {})
    return {
        "completed_this_week": w.get("completed_this_week", 0),
        "overdue_count":       w.get("overdue_count", 0),
        "open_count":          w.get("open_count", 0),
        "high_open_count":     w.get("high_open_count", 0),
        "overdue_items": [
            {"ticker": i.get("ticker"), "reason": _safe_truncate(i.get("reason", ""), 60)}
            for i in w.get("overdue_items", [])[:3]
        ],
    }


def _section_thesis_summary(data: dict) -> dict:
    t = data.get("thesis", {})
    return {
        "reviews_completed_this_week": t.get("reviews_completed_this_week", 0),
        "overdue_count":               t.get("overdue_count", 0),
        "stale_count":                 t.get("stale_count", 0),
    }


def _section_watchlist_changes(data: dict) -> dict:
    w = data.get("watchlist", {})
    return {
        "updated_this_week":  w.get("updated_this_week", 0),
        "archived_this_week": w.get("archived_this_week", 0),
        "total_active":       w.get("total_active", 0),
    }


def _section_scorecard_changes(data: dict) -> dict:
    s = data.get("scorecards", {})
    return {
        "computed_this_week": s.get("computed_this_week", 0),
        "top_strategy":       s.get("top_strategy"),
    }


def _section_stress_test_changes(data: dict) -> dict:
    s = data.get("stress", {})
    return {
        "runs_this_week": s.get("runs_this_week", 0),
        "worst_loss_pct": s.get("worst_loss_pct"),
    }


def _section_planner_drift_changes(data: dict) -> dict:
    p = data.get("planner", {})
    return {
        "runs_this_week": p.get("runs_this_week", 0),
        "last_urgency":   p.get("last_urgency", "NONE"),
        "drift_changed":  p.get("drift_changed", False),
    }


def _section_regime_changes(data: dict) -> dict:
    r = data.get("regime", {})
    return {
        "snapshots_this_week": r.get("snapshots_this_week", 0),
        "opening_regime":      r.get("opening_regime", "NEUTRAL"),
        "closing_regime":      r.get("closing_regime", "NEUTRAL"),
        "regime_changed":      r.get("regime_changed", False),
    }


def _section_key_mistakes(data: dict) -> List[dict]:
    """Factual observations about things that didn't go well."""
    mistakes: List[dict] = []
    # Negative alpha outcomes
    for o in data.get("outcomes", {}).get("completed", []):
        ret = float(o.get("return_5d") or o.get("return_10d") or 0.0)
        if ret < 0:
            mistakes.append({
                "type":        "negative_outcome",
                "ticker":      o.get("ticker", ""),
                "description": f"Outcome: {ret:.1f}% return for {o.get('ticker', '')}",
            })
    # Ignored HIGH priority workflow items
    for w in data.get("workflow", {}).get("ignored_high", []):
        mistakes.append({
            "type":        "ignored_high_priority",
            "ticker":      w.get("ticker"),
            "description": _safe_truncate(
                f"High-priority item unresolved: {w.get('reason', '')}", 80
            ),
        })
    return mistakes[:MAX_MISTAKES]


def _section_best_decisions(data: dict) -> List[dict]:
    """Factual observations about things that went well."""
    decisions: List[dict] = []
    # Positive alpha outcomes
    for o in data.get("outcomes", {}).get("completed", []):
        ret = float(o.get("return_5d") or o.get("return_10d") or 0.0)
        if ret > 0:
            decisions.append({
                "type":        "positive_outcome",
                "ticker":      o.get("ticker", ""),
                "description": f"Outcome: +{ret:.1f}% return for {o.get('ticker', '')}",
            })
    # Research discipline
    done = data.get("workflow", {}).get("completed_this_week", 0)
    if done > 0:
        decisions.append({
            "type":        "research_discipline",
            "ticker":      None,
            "description": f"Completed {done} research workflow item(s) this week",
        })
    # Checklist discipline
    approved = data.get("checklists", {}).get("approved_this_week", 0)
    if approved > 0:
        decisions.append({
            "type":        "checklist_discipline",
            "ticker":      None,
            "description": f"Completed {approved} decision checklist(s)",
        })
    return decisions[:MAX_DECISIONS]


def _section_missed_opportunities(data: dict) -> List[dict]:
    missed: List[dict] = []
    for m in data.get("outcomes", {}).get("missed_winners", []):
        r5d = m.get("return_5d")
        ticker = m.get("ticker", "")
        missed.append({
            "type":   "missed_winner",
            "ticker": ticker,
            "description": (
                f"Missed winner: {ticker} ({r5d:.1f}% return)" if r5d is not None
                else f"Missed winner: {ticker}"
            ),
        })
    return missed[:MAX_MISSED]


def _section_focus_next_week(data: dict) -> List[str]:
    focus: List[str] = []
    wf = data.get("workflow", {})
    overdue_items = wf.get("overdue_items", [])
    if overdue_items:
        tickers = ", ".join(
            (i.get("ticker") or "—") for i in overdue_items[:3]
        )
        focus.append(f"Complete overdue workflow items: {tickers}")

    thesis_overdue = data.get("thesis", {}).get("overdue_count", 0)
    if thesis_overdue > 0:
        focus.append(f"Review {thesis_overdue} overdue thesis(es)")

    pending_cl = data.get("checklists", {}).get("pending_count", 0)
    if pending_cl > 0:
        focus.append(f"Complete {pending_cl} pending decision checklist(s)")

    high_open = wf.get("high_open_count", 0)
    if high_open > 0:
        focus.append(f"Address {high_open} high-priority open research item(s)")

    still_active = data.get("dryruns", {}).get("still_active", 0)
    if still_active > 0:
        focus.append(f"Review {still_active} pending dry-run notification(s)")

    stale = data.get("thesis", {}).get("stale_count", 0)
    if stale > 0:
        focus.append(f"Update {stale} stale thesis(es) (90+ days since review)")

    if not focus:
        focus.append("No outstanding items — maintain current review cadence")
    return focus[:MAX_FOCUS]


# ── Section assembly ───────────────────────────────────────────────────────────

def build_weekly_sections(data: dict) -> dict:
    """Assemble all sections from collected data. Pure function — no I/O."""
    return {
        "portfolio_weekly_change": _section_portfolio_weekly_change(data),
        "alpha_generated":         _section_alpha_generated(data),
        "alpha_improved":          _section_alpha_improved(data),
        "alpha_failed":            _section_alpha_failed(data),
        "validation_outcomes":     _section_validation_outcomes(data),
        "notification_activity":   _section_notification_activity(data),
        "qc_suppressions":         _section_qc_suppressions(data),
        "delivery_attempts":       _section_delivery_attempts(data),
        "checklist_discipline":    _section_checklist_discipline(data),
        "workflow_summary":        _section_workflow_summary(data),
        "thesis_summary":          _section_thesis_summary(data),
        "watchlist_changes":       _section_watchlist_changes(data),
        "scorecard_changes":       _section_scorecard_changes(data),
        "stress_test_changes":     _section_stress_test_changes(data),
        "planner_drift_changes":   _section_planner_drift_changes(data),
        "regime_changes":          _section_regime_changes(data),
        "key_mistakes":            _section_key_mistakes(data),
        "best_decisions":          _section_best_decisions(data),
        "missed_opportunities":    _section_missed_opportunities(data),
        "focus_next_week":         _section_focus_next_week(data),
    }


# ── Accountability metrics ─────────────────────────────────────────────────────

def compute_accountability_metrics(data: dict) -> dict:
    """
    Derive the nine accountability metrics from collected weekly data.
    All values are deterministic given the same data dict.
    """
    wf  = data.get("workflow", {})
    cl  = data.get("checklists", {})
    th  = data.get("thesis", {})
    dr  = data.get("dryruns", {})
    out = data.get("outcomes", {})

    done      = wf.get("completed_this_week", 0)
    overdue   = wf.get("overdue_count", 0)
    open_cnt  = wf.get("open_count", 0)
    total_wf  = done + overdue + open_cnt
    review_completion_rate = round(done / total_wf, 3) if total_wf > 0 else 0.0

    total_cl  = cl.get("created_this_week", 0)
    approved  = cl.get("approved_this_week", 0)
    # 1.0 when no checklists were created (not penalized)
    checklist_discipline = round(approved / total_cl, 3) if total_cl > 0 else 1.0

    ignored_high = len(wf.get("ignored_high", []))

    return {
        "review_completion_rate":         review_completion_rate,
        "overdue_review_count":           overdue,
        "checklist_discipline_score":     checklist_discipline,
        "ignored_high_priority_workflow": ignored_high,
        "unreviewed_dry_runs":            dr.get("still_active", 0),
        "stale_theses":                   th.get("stale_count", 0),
        "alpha_false_positive_count":     out.get("false_positive_count", 0),
        "missed_winner_count":            out.get("missed_winner_count", 0),
        "risk_warnings_unresolved":       data.get("risk_warnings_unresolved", 0),
    }


# ── Weekly grade ───────────────────────────────────────────────────────────────

def compute_weekly_grade(metrics: dict) -> str:
    """
    Deterministic letter grade from A to F based on accountability metrics.

    Starts at 100.0 and applies penalty deductions.
    A ≥ 90 | B ≥ 75 | C ≥ 60 | D ≥ 45 | F < 45
    """
    score = 100.0

    # Overdue reviews: -5 each, max -20
    overdue = int(metrics.get("overdue_review_count", 0))
    score -= min(20, overdue * 5)

    # Stale theses: -5 each, max -15
    stale = int(metrics.get("stale_theses", 0))
    score -= min(15, stale * 5)

    # Unreviewed dry-runs: -3 each, max -12
    unreviewed = int(metrics.get("unreviewed_dry_runs", 0))
    score -= min(12, unreviewed * 3)

    # Alpha false positives: -4 each, max -16
    fp = int(metrics.get("alpha_false_positive_count", 0))
    score -= min(16, fp * 4)

    # Missed winners > 2: -3 each beyond 2, max -9
    missed = int(metrics.get("missed_winner_count", 0))
    if missed > 2:
        score -= min(9, (missed - 2) * 3)

    # Zero completed reviews → -10
    completion_rate = float(metrics.get("review_completion_rate", 0.0))
    if completion_rate == 0.0:
        score -= 10

    # Low checklist discipline (< 50%, but only when checklists exist) → -8
    discipline = float(metrics.get("checklist_discipline_score", 1.0))
    if 0.0 < discipline < 0.5:
        score -= 8

    # Unresolved risk warnings > 2 → -6
    risk = int(metrics.get("risk_warnings_unresolved", 0))
    if risk > 2:
        score -= 6

    # Ignored HIGH priority items: -2 each, max -5
    ignored = int(metrics.get("ignored_high_priority_workflow", 0))
    score -= min(5, ignored * 2)

    score = max(0.0, score)

    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 45:
        return "D"
    return "F"


# ── Formatters ─────────────────────────────────────────────────────────────────

def format_compact_weekly(
    sections: dict,
    metrics: dict,
    grade: str,
    week_start: str,
    generated_at: Optional[str] = None,
) -> str:
    """WhatsApp-safe compact weekly review ≤ COMPACT_MAX_CHARS chars."""
    week_end = _week_end(week_start)
    label    = _week_label(week_start, week_end)
    now_str  = generated_at or datetime.now(EASTERN).strftime("%b %-d, %Y %H:%M ET")

    grade_icon = {"A": "🏆", "B": "✅", "C": "⚠️", "D": "❌", "F": "🚨"}.get(grade, "")

    lines = [
        "─" * 25,
        f"📅 WEEKLY REVIEW — {label}",
        f"Grade: {grade_icon} {grade}",
        "",
    ]

    # Portfolio
    pt = sections.get("portfolio_weekly_change", {})
    if pt.get("available"):
        sign = _sign(pt.get("change_cad") or 0)
        lines += [
            "PORTFOLIO",
            f"  {sign}${abs(pt['change_cad']):,.0f} ({sign}{pt['change_pct']:.1f}%)",
            f"  End: ${pt['end_value']:,.0f}",
            "",
        ]

    # Alpha & outcomes
    ag  = sections.get("alpha_generated", {})
    val = sections.get("validation_outcomes", {})
    af  = sections.get("alpha_failed", {})
    if ag.get("count", 0) > 0 or val.get("completed_count", 0) > 0:
        lines.append("ALPHA ACTIVITY")
        lines.append(f"  {ag.get('count', 0)} tickers scanned  |  "
                     f"{val.get('completed_count', 0)} outcomes completed")
        if val.get("completed_count", 0) > 0:
            lines.append(f"  Winners: {val.get('positive_count', 0)}  |  "
                         f"False positives: {val.get('false_positive_count', 0)}")
        lines.append("")

    # Regime
    reg = sections.get("regime_changes", {})
    if reg.get("snapshots_this_week", 0) > 0:
        opening = reg.get("opening_regime", "NEUTRAL")
        closing = reg.get("closing_regime", "NEUTRAL")
        icon = "\U0001f534" if closing in ("RISK_OFF", "PANIC") else (
               "\U0001f7e2" if closing == "RISK_ON" else "\U0001f7e1"
        )
        changed = "  CHANGED" if reg.get("regime_changed") else ""
        lines.append(f"REGIME: {icon} {opening} → {closing}{changed}")
        lines.append("")

    # Accountability
    lines.append("ACCOUNTABILITY")
    cr = round(metrics.get("review_completion_rate", 0.0) * 100)
    lines.append(f"  Review completion: {cr}%")
    wf = sections.get("workflow_summary", {})
    lines.append(f"  Workflow done: {wf.get('completed_this_week', 0)}  |  "
                 f"Overdue: {wf.get('overdue_count', 0)}")
    unrev = metrics.get("unreviewed_dry_runs", 0)
    if unrev > 0:
        lines.append(f"  Dry-runs unreviewed: {unrev}")
    disc = metrics.get("checklist_discipline_score", 1.0)
    if disc < 1.0:
        lines.append(f"  Checklist discipline: {round(disc * 100)}%")
    stale = metrics.get("stale_theses", 0)
    if stale > 0:
        lines.append(f"  Stale theses: {stale}")
    lines.append("")

    # Focus next week
    focus = sections.get("focus_next_week", [])
    if focus:
        lines.append("FOCUS NEXT WEEK")
        for f in focus[:MAX_FOCUS]:
            lines.append(f"  → {_safe_truncate(f, 70)}")
        lines.append("")

    lines.append(f"Generated: {now_str}")
    lines.append("─" * 25)

    result = "\n".join(lines)
    if _has_banned_word(result):
        result = "[Weekly review contains flagged content — check system logs]"
    if len(result) > COMPACT_MAX_CHARS:
        result = result[: COMPACT_MAX_CHARS - 3] + "..."
    return result


def format_detailed_weekly(
    sections: dict,
    metrics: dict,
    grade: str,
    week_start: str,
    generated_at: Optional[str] = None,
) -> dict:
    """Return the full structured weekly review dict."""
    return {
        "mode":          "detailed",
        "grade":         grade,
        "week_start":    week_start,
        "week_end":      _week_end(week_start),
        "week_label":    _week_label(week_start, _week_end(week_start)),
        "generated_at":  generated_at or datetime.now(EASTERN).isoformat(),
        "accountability_metrics": metrics,
        **sections,
    }


def format_debug_weekly(
    sections: dict,
    metrics: dict,
    grade: str,
    data: dict,
    week_start: str,
    generated_at: Optional[str] = None,
) -> dict:
    """Return detailed review with data-source freshness metadata."""
    result = format_detailed_weekly(sections, metrics, grade, week_start, generated_at)
    result["mode"] = "debug"
    result["data_sources"] = {
        "portfolio_available":         data.get("portfolio", {}).get("available", False),
        "alpha_generated_count":       data.get("alpha", {}).get("generated_count", 0),
        "outcomes_completed_count":    data.get("outcomes", {}).get("completed_count", 0),
        "dryruns_created_count":       data.get("dryruns", {}).get("created_this_week", 0),
        "qc_evaluated_count":          data.get("qc", {}).get("evaluated_this_week", 0),
        "delivery_count":              data.get("delivery", {}).get("sent_this_week", 0),
        "checklists_created_count":    data.get("checklists", {}).get("created_this_week", 0),
        "workflow_completed_count":    data.get("workflow", {}).get("completed_this_week", 0),
        "thesis_reviews_count":        data.get("thesis", {}).get("reviews_completed_this_week", 0),
        "regime_snapshots_count":      data.get("regime", {}).get("snapshots_this_week", 0),
        "missed_winners_count":        data.get("outcomes", {}).get("missed_winner_count", 0),
        "risk_warnings_unresolved":    data.get("risk_warnings_unresolved", 0),
    }
    return result


# ── Core computation ───────────────────────────────────────────────────────────

def _compute_review(week_start_str: Optional[str]) -> tuple:
    """
    Collect, build, score. Returns:
    (sections, metrics, grade, data, generated_at, week_start)
    """
    week_start   = _parse_week_start(week_start_str)
    week_end     = _week_end(week_start)
    generated_at = datetime.now(EASTERN).isoformat()
    data         = collect_weekly_data(week_start, week_end)
    sections     = build_weekly_sections(data)
    metrics      = compute_accountability_metrics(data)
    grade        = compute_weekly_grade(metrics)
    return sections, metrics, grade, data, generated_at, week_start


# ── Entry points ───────────────────────────────────────────────────────────────

def generate_weekly_review(
    mode: str = "detailed",
    week_start_str: Optional[str] = None,
) -> Any:
    """
    Generate the weekly review report.

    mode:
      compact  — str, WhatsApp-safe, ≤ COMPACT_MAX_CHARS
      detailed — dict with all sections + metrics + grade
      debug    — dict with all sections + metrics + grade + data-source metadata
    """
    if mode not in MODES:
        mode = "detailed"
    sections, metrics, grade, data, generated_at, week_start = _compute_review(week_start_str)
    if mode == "compact":
        return format_compact_weekly(sections, metrics, grade, week_start, generated_at)
    if mode == "debug":
        return format_debug_weekly(sections, metrics, grade, data, week_start, generated_at)
    return format_detailed_weekly(sections, metrics, grade, week_start, generated_at)


def generate_compact_weekly() -> str:
    """
    Convenience entry point for the Friday scheduler job.
    Returns a WhatsApp-safe compact weekly review string.
    Never raises — returns a safe fallback on error.
    """
    try:
        return generate_weekly_review(mode="compact")
    except Exception:
        log.error("weekly_review: generate_compact_weekly failed", exc_info=True)
        now_str = datetime.now(EASTERN).strftime("%b %-d, %Y %H:%M ET")
        return f"📅 WEEKLY REVIEW — {now_str}\n\nReview unavailable. Check system logs."
