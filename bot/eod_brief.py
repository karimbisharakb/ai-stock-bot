"""
bot/eod_brief.py — Phase A22: End-of-Day Review Brief

Summarises what happened during the trading day across all Alpha systems.
Three modes: compact (WhatsApp, ≤ COMPACT_MAX_CHARS), detailed (full dict),
debug (detailed + data-source metadata).

Feature flag: EOD_BRIEF_ENABLED env var (default false).
When false the API still responds; only the scheduler send is suppressed.

Safety: no trade instructions, no order placement, no broker calls.
All output is advisory information only.
"""
import logging
import os
from datetime import datetime
from typing import Any, List, Optional

import pytz

log = logging.getLogger(__name__)
EASTERN = pytz.timezone("America/Toronto")

# ── Configuration ──────────────────────────────────────────────────────────────

MODES         = ["compact", "detailed", "debug"]
_DEFAULT_MODE = "detailed"

MAX_ALPHA_CHANGES    = 3
MAX_RISK_CHANGES     = 3
MAX_UNRESOLVED       = 3
MAX_WATCHLIST        = 3
MAX_LEARNING_NOTES   = 3
MAX_OVERNIGHT_DRS    = 3

COMPACT_MAX_CHARS = 1600

BANNED_WORDS: List[str] = [
    "moon", "explosion", "explode", "rocket", "yolo", "hodl", "gem",
    "lambo", "\U0001f680", "\U0001f48e", "to the moon",
]

REQUIRED_SECTIONS: List[str] = [
    "portfolio_change_summary",
    "alpha_candidates_today",
    "alert_readiness_changes",
    "dryrun_activity_today",
    "qc_suppressions_today",
    "regime_changes_today",
    "stress_risk_changes_today",
    "checklists_updated_today",
    "thesis_updates_today",
    "learning_changes_today",
    "outcomes_completed_today",
    "planner_updates_today",
    "tomorrow_watchlist",
    "unresolved_actions",
    "workflow_summary",
]


# ── Feature flag ───────────────────────────────────────────────────────────────

def eod_enabled() -> bool:
    """Return True when EOD_BRIEF_ENABLED=true is set in the environment."""
    return os.getenv("EOD_BRIEF_ENABLED", "false").lower() == "true"


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


def _today_start() -> str:
    return (
        datetime.now(EASTERN)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat()
    )


# ── Pure section builders ──────────────────────────────────────────────────────

def _portfolio_change_section(portfolio: dict, transactions_today: list) -> dict:
    positions  = portfolio.get("positions", [])
    aggregates = portfolio.get("aggregates", {})
    total_val  = float(aggregates.get("total_portfolio_value", 0.0))
    cash       = float(aggregates.get("cash", 0.0))
    total_pnl  = sum(float(p.get("unrealized_pnl", 0.0) or 0.0) for p in positions)
    total_cost = sum(
        float(p.get("quantity", 0.0) or 0.0) * float(p.get("avg_cost", 0.0) or 0.0)
        for p in positions
    )
    pnl_pct = round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0.0
    return {
        "position_count":     len(positions),
        "total_value":        round(total_val, 2),
        "cash":               round(cash, 2),
        "unrealized_pnl":     round(total_pnl, 2),
        "unrealized_pnl_pct": pnl_pct,
        "transactions_today": len(transactions_today),
        "transactions": [
            {
                "ticker":    t.get("ticker", ""),
                "type":      t.get("type", ""),
                "shares":    t.get("shares", 0.0),
                "price_cad": t.get("price_cad", 0.0),
            }
            for t in transactions_today
        ],
    }


def _alpha_candidates_section(alpha_today: list) -> dict:
    if not alpha_today:
        return {"scored_today": 0, "top_candidates": [], "tier_distribution": {}}
    by_tier: dict = {}
    for c in alpha_today:
        t = c.get("alpha_tier", "UNKNOWN")
        by_tier[t] = by_tier.get(t, 0) + 1
    top = sorted(alpha_today, key=lambda c: float(c.get("alpha_score") or 0.0), reverse=True)
    return {
        "scored_today":    len(alpha_today),
        "top_candidates":  [
            {
                "ticker":     c.get("ticker", ""),
                "alpha_score": c.get("alpha_score"),
                "alpha_tier": c.get("alpha_tier", ""),
                "setup_type": c.get("setup_type", ""),
            }
            for c in top[:MAX_ALPHA_CHANGES]
        ],
        "tier_distribution": by_tier,
    }


def _alert_readiness_section(alpha_today: list) -> dict:
    eligible_tiers = {"ALERT_READY", "PRE_ALERT", "RARE_ALERT"}
    ready = [c for c in alpha_today if c.get("readiness_tier") in eligible_tiers]
    alert_ready = [c for c in alpha_today if c.get("readiness_tier") == "ALERT_READY"]
    return {
        "alert_eligible_count": len(ready),
        "alert_ready_count":    len(alert_ready),
        "alert_ready_tickers":  [c.get("ticker", "") for c in alert_ready[:MAX_ALPHA_CHANGES]],
    }


def _dryrun_activity_section(dryruns_today: list) -> dict:
    created   = len(dryruns_today)
    reviewed  = sum(1 for d in dryruns_today if d.get("status") == "REVIEWED")
    dismissed = sum(1 for d in dryruns_today if d.get("status") == "DISMISSED")
    active    = sum(1 for d in dryruns_today if d.get("status") == "DRY_RUN")
    return {
        "created_today":  created,
        "reviewed_today": reviewed,
        "dismissed_today": dismissed,
        "still_active":   active,
        "top_tickers":    [d.get("ticker", "") for d in dryruns_today[:MAX_OVERNIGHT_DRS]],
    }


def _qc_suppressions_section(qc_today: list) -> dict:
    total     = len(qc_today)
    allowed   = sum(1 for q in qc_today if q.get("allow_notification"))
    suppressed = total - allowed
    return {
        "evaluated_today":  total,
        "allowed_today":    allowed,
        "suppressed_today": suppressed,
        "suppression_rate": round(suppressed / total * 100, 1) if total > 0 else 0.0,
    }


def _regime_changes_section(regime_snapshots_today: list) -> dict:
    if not regime_snapshots_today:
        return {"snapshots_today": 0, "regime_changed": False}
    first = regime_snapshots_today[0]
    last  = regime_snapshots_today[-1]
    changed = (
        len(regime_snapshots_today) > 1
        and first.get("overall_regime") != last.get("overall_regime")
    )
    return {
        "snapshots_today":     len(regime_snapshots_today),
        "opening_regime":      first.get("overall_regime", "NEUTRAL"),
        "closing_regime":      last.get("overall_regime", "NEUTRAL"),
        "closing_regime_score": last.get("regime_score", 50.0),
        "regime_changed":      changed,
    }


def _stress_risk_section(stress_runs_today: list) -> dict:
    if not stress_runs_today:
        return {"runs_today": 0}
    worst_pct = min(
        (r.get("worst_loss_pct") or 0.0 for r in stress_runs_today), default=0.0
    )
    return {
        "runs_today":         len(stress_runs_today),
        "worst_loss_pct":     round(worst_pct, 2),
        "worst_scenario":     stress_runs_today[-1].get("worst_scenario"),
    }


def _checklists_section(checklists_today: list) -> dict:
    by_status: dict = {}
    for c in checklists_today:
        s = c.get("checklist_status", "UNKNOWN")
        by_status[s] = by_status.get(s, 0) + 1
    return {
        "created_today": len(checklists_today),
        "by_status":     by_status,
        "tickers":       [c.get("ticker", "") for c in checklists_today[:MAX_ALPHA_CHANGES]],
    }


def _thesis_updates_section(journal_today: list) -> dict:
    by_type: dict = {}
    tickers: set  = set()
    for e in journal_today:
        t = e.get("entry_type", "NOTE")
        by_type[t] = by_type.get(t, 0) + 1
        tickers.add(e.get("ticker", ""))
    return {
        "entries_today": len(journal_today),
        "by_type":       by_type,
        "tickers":       sorted(tickers)[:MAX_ALPHA_CHANGES],
    }


def _learning_changes_section(proposals_today: list) -> dict:
    if not proposals_today:
        return {"proposals_today": 0}
    by_status: dict = {}
    for p in proposals_today:
        s = p.get("status", "UNKNOWN")
        by_status[s] = by_status.get(s, 0) + 1
    return {
        "proposals_today": len(proposals_today),
        "by_status":       by_status,
        "kinds":           list({p.get("kind", "") for p in proposals_today})[:MAX_LEARNING_NOTES],
    }


def _outcomes_section(outcomes_today: list, validations_today: list) -> dict:
    total   = len(outcomes_today)
    win_cnt = sum(
        1 for o in outcomes_today
        if float(o.get("return_10d") or o.get("return_5d") or 0.0) > 0
    )
    behavior_counts: dict = {}
    for v in validations_today:
        bc = v.get("behavior_class", "UNKNOWN")
        behavior_counts[bc] = behavior_counts.get(bc, 0) + 1
    return {
        "completed_today":    total,
        "validations_today":  len(validations_today),
        "behavior_summary":   behavior_counts,
        "tickers":            [o.get("ticker", "") for o in outcomes_today[:MAX_LEARNING_NOTES]],
    }


def _planner_updates_section(planner_today: list) -> dict:
    if not planner_today:
        return {"runs_today": 0}
    last = planner_today[-1]
    return {
        "runs_today":       len(planner_today),
        "last_urgency":     last.get("rebalance_urgency", "NONE"),
        "last_snapshot_id": last.get("snapshot_id"),
    }


def _tomorrow_watchlist_section(top_candidates: list) -> List[dict]:
    """Top MAX_WATCHLIST candidates for tomorrow's attention."""
    eligible_tiers = {"ALERT_READY", "PRE_ALERT", "RARE_ALERT"}
    # Prioritise high-readiness tiers, then fall back to score
    hi = [c for c in top_candidates if c.get("readiness_tier") in eligible_tiers]
    rest = [c for c in top_candidates if c.get("readiness_tier") not in eligible_tiers]
    ordered = (hi + rest)[:MAX_WATCHLIST]
    return [
        {
            "ticker":        c.get("ticker", ""),
            "alpha_score":   round(float(c.get("alpha_score") or 0.0), 2),
            "readiness_tier": c.get("readiness_tier", ""),
            "setup_type":    c.get("setup_type", ""),
        }
        for c in ordered
    ]


def _unresolved_actions_section(
    pending_checklists: list,
    due_reviews: dict,
    unreviewed_dryruns: list,
) -> List[str]:
    actions: List[str] = []
    for cl in pending_checklists[:MAX_UNRESOLVED]:
        actions.append(f"Checklist pending: {cl.get('ticker', '')} ({cl.get('checklist_status', '')})")
    for r in due_reviews.get("overdue", [])[:MAX_UNRESOLVED]:
        actions.append(f"Thesis review overdue: {r.get('ticker', '')}")
    for dr in unreviewed_dryruns[:MAX_UNRESOLVED]:
        actions.append(f"Dry-run needs review: {dr.get('ticker', '')} ({dr.get('readiness_tier', '')})")
    # Deduplicate
    seen: set = set()
    unique: List[str] = []
    for a in actions:
        if a not in seen:
            seen.add(a)
            unique.append(a)
    return unique[:MAX_UNRESOLVED]


def _workflow_summary_section(
    completed_today: list,
    open_items: list,
) -> dict:
    """Workflow summary for EOD brief: completed today + still-open items."""
    completed = [
        {"ticker": w.get("ticker"), "reason": w.get("reason"), "source": w.get("source")}
        for w in completed_today[:3]
    ]
    unresolved = [
        {"ticker": w.get("ticker"), "reason": w.get("reason"), "priority_score": w.get("priority_score")}
        for w in open_items[:3]
    ]
    return {
        "completed_count": len(completed_today),
        "completed":       completed,
        "unresolved_count": len(open_items),
        "unresolved":      unresolved,
    }


# ── Section assembly ───────────────────────────────────────────────────────────

def build_eod_sections(data: dict) -> dict:
    """Assemble all sections from raw data. Pure — no I/O."""
    return {
        "portfolio_change_summary":  _portfolio_change_section(
            data.get("portfolio", {}),
            data.get("transactions_today", []),
        ),
        "alpha_candidates_today":    _alpha_candidates_section(data.get("alpha_today", [])),
        "alert_readiness_changes":   _alert_readiness_section(data.get("alpha_today", [])),
        "dryrun_activity_today":     _dryrun_activity_section(data.get("dryruns_today", [])),
        "qc_suppressions_today":     _qc_suppressions_section(data.get("qc_today", [])),
        "regime_changes_today":      _regime_changes_section(data.get("regime_snapshots_today", [])),
        "stress_risk_changes_today": _stress_risk_section(data.get("stress_runs_today", [])),
        "checklists_updated_today":  _checklists_section(data.get("checklists_today", [])),
        "thesis_updates_today":      _thesis_updates_section(data.get("journal_today", [])),
        "learning_changes_today":    _learning_changes_section(data.get("proposals_today", [])),
        "outcomes_completed_today":  _outcomes_section(
            data.get("outcomes_today", []),
            data.get("validations_today", []),
        ),
        "planner_updates_today":     _planner_updates_section(data.get("planner_today", [])),
        "tomorrow_watchlist":        _tomorrow_watchlist_section(data.get("top_candidates", [])),
        "unresolved_actions":        _unresolved_actions_section(
            data.get("pending_checklists", []),
            data.get("due_reviews", {}),
            data.get("unreviewed_dryruns", []),
        ),
        "workflow_summary":          _workflow_summary_section(
            data.get("workflow_completed_today", []),
            data.get("workflow_open_items", []),
        ),
    }


# ── Formatters ─────────────────────────────────────────────────────────────────

def format_compact_eod(sections: dict, generated_at: Optional[str] = None) -> str:
    """WhatsApp-safe EOD review string ≤ COMPACT_MAX_CHARS chars."""
    now_str = generated_at or datetime.now(EASTERN).strftime("%b %-d, %Y %H:%M ET")
    lines = [
        "─" * 25,
        f"🌙 EOD REVIEW — {now_str}",
        "",
    ]

    pt = sections["portfolio_change_summary"]
    sign = _sign(pt["unrealized_pnl"])
    lines += [
        "PORTFOLIO",
        f"  {pt['position_count']} positions  |  Total: ${pt['total_value']:,.0f}",
        f"  P&L: {sign}${pt['unrealized_pnl']:,.0f} ({sign}{pt['unrealized_pnl_pct']:.1f}%)",
    ]
    if pt["transactions_today"] > 0:
        lines.append(f"  Transactions today: {pt['transactions_today']}")
    lines.append("")

    # Alpha activity
    al = sections["alpha_candidates_today"]
    rd = sections["alert_readiness_changes"]
    if al["scored_today"] > 0:
        lines.append(
            f"ALPHA: {al['scored_today']} scored  |  "
            f"{rd['alert_ready_count']} alert-ready"
        )
        for c in al["top_candidates"][:MAX_ALPHA_CHANGES]:
            s = f"{c['alpha_score']:.0f}" if c.get("alpha_score") is not None else "?"
            lines.append(f"  • {c['ticker']}  {c.get('alpha_tier','')}  score={s}")
        lines.append("")

    # Dry-runs + QC
    dr = sections["dryrun_activity_today"]
    qc = sections["qc_suppressions_today"]
    if dr["created_today"] > 0 or qc["evaluated_today"] > 0:
        lines.append(
            f"NOTIFICATIONS: {dr['created_today']} dry-runs  |  "
            f"{qc['suppressed_today']} suppressed"
        )
        lines.append("")

    # Regime
    reg = sections["regime_changes_today"]
    if reg["snapshots_today"] > 0:
        closing = reg.get("closing_regime", "NEUTRAL")
        score   = reg.get("closing_regime_score", 50.0)
        icon    = "\U0001f534" if closing in ("RISK_OFF", "PANIC") else (
                  "\U0001f7e2" if closing == "RISK_ON" else "\U0001f7e1"
        )
        changed_note = "  CHANGED today" if reg.get("regime_changed") else ""
        lines.append(f"REGIME: {icon} {closing} (score {score:.0f}){changed_note}")
        lines.append("")

    # Stress
    stress = sections["stress_risk_changes_today"]
    if stress.get("runs_today", 0) > 0:
        worst = stress.get("worst_scenario", "N/A")
        pct   = stress.get("worst_loss_pct", 0.0) or 0.0
        lines.append(f"STRESS: {stress['runs_today']} runs  |  worst {worst} = {pct:.1f}%")
        lines.append("")

    # Learning / outcomes
    oc = sections["outcomes_completed_today"]
    lc = sections["learning_changes_today"]
    if oc["completed_today"] > 0 or lc.get("proposals_today", 0) > 0:
        lines.append(
            f"LEARNING: {oc['completed_today']} outcomes  |  "
            f"{lc.get('proposals_today', 0)} proposals"
        )
        lines.append("")

    # Tomorrow watchlist
    wl = sections["tomorrow_watchlist"]
    if wl:
        lines.append("WATCH TOMORROW")
        for w in wl:
            s = f"{w['alpha_score']:.0f}" if w.get("alpha_score") is not None else "?"
            lines.append(f"  • {w['ticker']}  {w.get('readiness_tier','')}  score={s}")
        lines.append("")

    # Unresolved
    ur = sections["unresolved_actions"]
    if ur:
        lines.append("UNRESOLVED")
        for act in ur[:MAX_UNRESOLVED]:
            lines.append(f"  → {_safe_truncate(act, 70)}")
        lines.append("")

    # Workflow summary (A25)
    wf = sections.get("workflow_summary", {})
    if wf.get("completed_count", 0) > 0 or wf.get("unresolved_count", 0) > 0:
        lines.append(
            f"RESEARCH WORKFLOW: {wf.get('completed_count', 0)} done  |  "
            f"{wf.get('unresolved_count', 0)} open"
        )
        lines.append("")

    lines.append("─" * 25)

    result = "\n".join(lines)
    if len(result) > COMPACT_MAX_CHARS:
        result = result[: COMPACT_MAX_CHARS - 3] + "..."
    return result


def format_detailed_eod(sections: dict, generated_at: Optional[str] = None) -> dict:
    return {
        "mode":         "detailed",
        "generated_at": generated_at or datetime.now(EASTERN).isoformat(),
        **sections,
    }


def format_debug_eod(
    sections: dict,
    data: dict,
    generated_at: Optional[str] = None,
) -> dict:
    brief = format_detailed_eod(sections, generated_at)
    brief["mode"] = "debug"
    brief["data_sources"] = {
        "portfolio_available":      bool(data.get("portfolio")),
        "alpha_today_count":        len(data.get("alpha_today", [])),
        "dryruns_today_count":      len(data.get("dryruns_today", [])),
        "qc_today_count":           len(data.get("qc_today", [])),
        "regime_snapshots_count":   len(data.get("regime_snapshots_today", [])),
        "stress_runs_today":        len(data.get("stress_runs_today", [])),
        "outcomes_today_count":     len(data.get("outcomes_today", [])),
        "proposals_today_count":    len(data.get("proposals_today", [])),
        "top_candidates_count":     len(data.get("top_candidates", [])),
        "today_start":              data.get("today_start", ""),
    }
    return brief


# ── Data collection ────────────────────────────────────────────────────────────

def collect_eod_data() -> dict:
    """
    Fetch today's activity from all systems.  Each source is individually
    wrapped in try/except.  Never raises.
    """
    ts = _today_start()
    data: dict = {"today_start": ts}

    # Current portfolio
    try:
        from portfolio_reconciliation import get_canonical_portfolio
        data["portfolio"] = get_canonical_portfolio()
    except Exception:
        log.warning("eod_brief: portfolio fetch failed", exc_info=True)
        data["portfolio"] = {}

    # Transactions recorded today
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT ticker, type, shares, price_cad FROM transactions WHERE date >= ?",
                (ts,),
            ).fetchall()
        finally:
            conn.close()
        data["transactions_today"] = [dict(r) for r in rows]
    except Exception:
        log.warning("eod_brief: transactions fetch failed", exc_info=True)
        data["transactions_today"] = []

    # Alpha candidates scored today (alpha_shadow_log uses scan_time)
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT ticker, alpha_score, alpha_tier, setup_type "
                "FROM alpha_shadow_log WHERE scan_time >= ? "
                "ORDER BY alpha_score DESC",
                (ts,),
            ).fetchall()
        finally:
            conn.close()
        data["alpha_today"] = [dict(r) for r in rows]
    except Exception:
        log.warning("eod_brief: alpha_shadow_log fetch failed", exc_info=True)
        data["alpha_today"] = []

    # Current readiness tiers from alpha_alert_gate (for alert_readiness_changes
    # and tomorrow_watchlist — both built from same source)
    try:
        from alpha_alert_gate import get_alert_candidates
        candidates = get_alert_candidates(limit=50)
        # Merge readiness_tier into alpha_today for alert_readiness_section
        tier_map = {c["ticker"]: c.get("readiness_tier") for c in candidates}
        for c in data["alpha_today"]:
            c["readiness_tier"] = tier_map.get(c.get("ticker", ""), "NOT_READY")
        data["top_candidates"] = candidates
    except Exception:
        log.warning("eod_brief: alert candidates fetch failed", exc_info=True)
        for c in data.get("alpha_today", []):
            c.setdefault("readiness_tier", "NOT_READY")
        data["top_candidates"] = []

    # Dry-run notifications created today
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT ticker, status, readiness_tier, alpha_score "
                "FROM alpha_notification_dryruns WHERE created_at >= ? "
                "ORDER BY created_at DESC",
                (ts,),
            ).fetchall()
        finally:
            conn.close()
        data["dryruns_today"] = [dict(r) for r in rows]
    except Exception:
        log.warning("eod_brief: dryruns fetch failed", exc_info=True)
        data["dryruns_today"] = []

    # QC evaluations today
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT ticker, allow_notification, suppression_reason, qc_score "
                "FROM notification_qc_history WHERE evaluated_at >= ?",
                (ts,),
            ).fetchall()
        finally:
            conn.close()
        data["qc_today"] = [dict(r) for r in rows]
    except Exception:
        log.warning("eod_brief: qc_today fetch failed", exc_info=True)
        data["qc_today"] = []

    # Regime snapshots today
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT overall_regime, regime_score, captured_at "
                "FROM market_regime_snapshots WHERE captured_at >= ? "
                "ORDER BY captured_at",
                (ts,),
            ).fetchall()
        finally:
            conn.close()
        data["regime_snapshots_today"] = [dict(r) for r in rows]
    except Exception:
        log.warning("eod_brief: regime snapshots fetch failed", exc_info=True)
        data["regime_snapshots_today"] = []

    # Stress runs today
    try:
        from portfolio_stress_testing import get_stress_history
        all_runs = get_stress_history(limit=50)
        data["stress_runs_today"] = [r for r in all_runs if (r.get("created_at") or "") >= ts]
    except Exception:
        log.warning("eod_brief: stress runs fetch failed", exc_info=True)
        data["stress_runs_today"] = []

    # Decision checklists created today
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT ticker, decision_type, checklist_status, checklist_completion "
                "FROM decision_checklists WHERE created_at >= ? "
                "ORDER BY created_at DESC",
                (ts,),
            ).fetchall()
        finally:
            conn.close()
        data["checklists_today"] = [dict(r) for r in rows]
    except Exception:
        log.warning("eod_brief: checklists fetch failed", exc_info=True)
        data["checklists_today"] = []

    # Journal entries today (position_journal table)
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT ticker, entry_type, created_at "
                "FROM position_journal WHERE created_at >= ? "
                "ORDER BY created_at DESC",
                (ts,),
            ).fetchall()
        finally:
            conn.close()
        data["journal_today"] = [dict(r) for r in rows]
    except Exception:
        log.warning("eod_brief: journal fetch failed", exc_info=True)
        data["journal_today"] = []

    # Learning proposals created today
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT proposal_id, status, kind "
                "FROM learning_proposals WHERE created_at >= ? "
                "ORDER BY created_at DESC",
                (ts,),
            ).fetchall()
        finally:
            conn.close()
        data["proposals_today"] = [dict(r) for r in rows]
    except Exception:
        log.warning("eod_brief: proposals fetch failed", exc_info=True)
        data["proposals_today"] = []

    # Alpha outcomes completed today
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT ticker, alpha_tier, return_10d, return_5d "
                "FROM alpha_outcomes WHERE status = 'COMPLETE' AND updated_at >= ?",
                (ts,),
            ).fetchall()
        finally:
            conn.close()
        data["outcomes_today"] = [dict(r) for r in rows]
    except Exception:
        log.warning("eod_brief: outcomes fetch failed", exc_info=True)
        data["outcomes_today"] = []

    # Alpha validations computed today
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT ticker, behavior_class, validation_score "
                "FROM alpha_validation WHERE computed_at >= ?",
                (ts,),
            ).fetchall()
        finally:
            conn.close()
        data["validations_today"] = [dict(r) for r in rows]
    except Exception:
        log.warning("eod_brief: validations fetch failed", exc_info=True)
        data["validations_today"] = []

    # Planner snapshots today
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT snapshot_id, rebalance_urgency, created_at "
                "FROM planner_snapshots WHERE created_at >= ? "
                "ORDER BY created_at",
                (ts,),
            ).fetchall()
        finally:
            conn.close()
        data["planner_today"] = [dict(r) for r in rows]
    except Exception:
        log.warning("eod_brief: planner snapshots fetch failed", exc_info=True)
        data["planner_today"] = []

    # Unresolved: pending checklists
    try:
        from decision_checklist import get_pending_checklists
        data["pending_checklists"] = get_pending_checklists()
    except Exception:
        log.warning("eod_brief: pending checklists fetch failed", exc_info=True)
        data["pending_checklists"] = []

    # Unresolved: overdue thesis reviews
    try:
        from position_journal import get_due_reviews
        data["due_reviews"] = get_due_reviews()
    except Exception:
        log.warning("eod_brief: due reviews fetch failed", exc_info=True)
        data["due_reviews"] = {"due": [], "overdue": [], "upcoming": []}

    # Unresolved: dry-runs still awaiting review
    try:
        from alpha_notification_dryrun import get_dry_runs
        data["unreviewed_dryruns"] = get_dry_runs(status_filter="DRY_RUN", limit=10)
    except Exception:
        log.warning("eod_brief: unreviewed dryruns fetch failed", exc_info=True)
        data["unreviewed_dryruns"] = []

    # Workflow: completed today and still-open items (A25)
    try:
        from research_workflow import get_completed_today, get_brief_items
        data["workflow_completed_today"] = get_completed_today()
        data["workflow_open_items"]      = get_brief_items(limit=3)
    except Exception:
        log.warning("eod_brief: workflow fetch failed", exc_info=True)
        data["workflow_completed_today"] = []
        data["workflow_open_items"]      = []

    return data


# ── Entry points ───────────────────────────────────────────────────────────────

def generate_eod_brief(mode: str = "detailed") -> Any:
    """
    Collect today's data from all systems and return the formatted EOD brief.

    mode:
      compact  — str, WhatsApp-safe, ≤ COMPACT_MAX_CHARS
      detailed — dict with all sections
      debug    — dict with all sections + data-source metadata
    """
    if mode not in MODES:
        mode = _DEFAULT_MODE

    generated_at = datetime.now(EASTERN).isoformat()
    data         = collect_eod_data()
    sections     = build_eod_sections(data)

    if mode == "compact":
        return format_compact_eod(sections, generated_at)
    if mode == "debug":
        return format_debug_eod(sections, data, generated_at)
    return format_detailed_eod(sections, generated_at)


def generate_compact_eod() -> str:
    """
    Convenience entry point for the evening scheduler.
    Returns a WhatsApp-safe EOD brief string.
    Never raises — returns a safe fallback on error.
    """
    try:
        return generate_eod_brief(mode="compact")
    except Exception:
        log.error("eod_brief: generate_compact_eod failed", exc_info=True)
        now_str = datetime.now(EASTERN).strftime("%b %-d, %Y %H:%M ET")
        return f"🌙 EOD REVIEW — {now_str}\n\nBrief unavailable. Check system logs."
