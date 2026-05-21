"""
bot/operator_brief.py — Phase A21: Daily Operator Brief 2.0

Pulls data from all major Alpha systems and renders a daily briefing.

Three modes:
  compact  — WhatsApp-safe text block, ≤ COMPACT_MAX_CHARS characters
  detailed — structured dict with every section
  debug    — detailed + data-source freshness metadata

Safety: no trade instructions, no order placement, no broker calls.
All output is advisory information only.
"""
import logging
from datetime import datetime
from typing import Any, List, Optional

import pytz

log = logging.getLogger(__name__)
EASTERN = pytz.timezone("America/Toronto")

# ── Configuration ──────────────────────────────────────────────────────────────

MODES         = ["compact", "detailed", "debug"]
_DEFAULT_MODE = "detailed"

MAX_ALPHA_HIGHLIGHTS   = 3
MAX_RISK_WARNINGS      = 3
MAX_CHECKLIST_ITEMS    = 3
MAX_PLANNER_NOTES      = 3
MAX_OVERNIGHT_SIGNALS  = 5
MAX_DRYRUN_HIGHLIGHTS  = 3
MAX_THESIS_REVIEWS     = 3
MAX_SCORECARD_WARNINGS = 3
MAX_KEY_ACTIONS        = 7

COMPACT_MAX_CHARS = 1600

BANNED_WORDS: List[str] = [
    "moon", "explosion", "explode", "rocket", "yolo", "hodl", "gem",
    "lambo", "\U0001f680", "\U0001f48e", "to the moon",
]

# Keys every detailed/debug brief must contain
REQUIRED_SECTIONS: List[str] = [
    "portfolio_truth",
    "overnight_changes",
    "alpha_highlights",
    "dryrun_highlights",
    "qc_suppression_summary",
    "market_regime",
    "risk_warnings",
    "stress_worst_case",
    "checklists_due",
    "thesis_reviews_due",
    "scorecard_warnings",
    "planner_summary",
    "cash_tfsa_notes",
    "key_actions",
    "workflow_items",
    "upcoming_catalysts",
]


# ── Utility helpers ────────────────────────────────────────────────────────────

def check_banned_words(text: str) -> List[str]:
    """Return list of banned words found in text (case-insensitive)."""
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


# ── Pure section builders ──────────────────────────────────────────────────────

def _portfolio_truth_section(portfolio: dict) -> dict:
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
        "positions": [
            {
                "ticker":            p.get("ticker", ""),
                "quantity":          p.get("quantity", 0.0),
                "market_value":      round(float(p.get("market_value", 0.0) or 0.0), 2),
                "unrealized_pnl":    round(float(p.get("unrealized_pnl", 0.0) or 0.0), 2),
                "unrealized_pnl_pct": round(float(p.get("unrealized_pnl_pct", 0.0) or 0.0), 2),
            }
            for p in positions
        ],
    }


def _overnight_section(signals: List[str]) -> List[str]:
    """Cap at MAX_OVERNIGHT_SIGNALS; drop any entry containing a banned word."""
    return [s for s in signals[:MAX_OVERNIGHT_SIGNALS] if not _has_banned_word(s)]


def _alpha_highlights_section(candidates: list) -> List[dict]:
    """Return top MAX_ALPHA_HIGHLIGHTS candidates from alert-eligible tiers."""
    eligible_tiers = {"ALERT_READY", "PRE_ALERT", "RARE_ALERT"}
    eligible = [c for c in candidates if c.get("readiness_tier") in eligible_tiers]
    results = []
    for c in eligible[:MAX_ALPHA_HIGHLIGHTS]:
        score = c.get("alpha_score")
        results.append({
            "ticker":         c.get("ticker", ""),
            "alpha_score":    round(float(score), 2) if score is not None else None,
            "alpha_tier":     c.get("alpha_tier", ""),
            "setup_type":     c.get("setup_type", ""),
            "readiness_tier": c.get("readiness_tier", ""),
            "reason":         _safe_truncate(c.get("reason", ""), 120),
        })
    return results


def _dryrun_highlights_section(dry_runs: list) -> List[dict]:
    results = []
    for dr in dry_runs[:MAX_DRYRUN_HIGHLIGHTS]:
        msg   = dr.get("message_text", "")
        score = dr.get("alpha_score")
        results.append({
            "ticker":          dr.get("ticker", ""),
            "readiness_tier":  dr.get("readiness_tier", ""),
            "alpha_score":     round(float(score), 2) if score is not None else None,
            "status":          dr.get("status", ""),
            "message_preview": _safe_truncate(msg, 120),
        })
    return results


def _qc_section(qc_summary: dict) -> dict:
    return {
        "total_evaluated":   qc_summary.get("total_evaluated", 0),
        "allowed_count":     qc_summary.get("allowed_count", 0),
        "suppressed_count":  qc_summary.get("suppressed_count", 0),
        "priority_candidates": qc_summary.get("priority_candidates", 0),
        "avg_qc_score":      qc_summary.get("avg_qc_score", 0.0),
    }


def _regime_section(regime_ctx: dict) -> dict:
    if not regime_ctx.get("available"):
        return {
            "available":      False,
            "overall_regime": "NEUTRAL",
            "regime_score":   50.0,
            "warnings":       [],
        }
    return {
        "available":          True,
        "overall_regime":     regime_ctx.get("overall_regime", "NEUTRAL"),
        "volatility_regime":  regime_ctx.get("volatility_regime"),
        "breadth_regime":     regime_ctx.get("breadth_regime"),
        "speculative_regime": regime_ctx.get("speculative_regime"),
        "regime_score":       regime_ctx.get("regime_score", 50.0),
        "warnings":           (regime_ctx.get("warnings") or [])[:3],
        "captured_at":        regime_ctx.get("captured_at"),
    }


def _risk_warnings_section(risk_report: dict) -> List[str]:
    """Collect portfolio risk warnings, priority ordered, capped at MAX_RISK_WARNINGS."""
    warnings: List[str] = []
    if risk_report.get("cash_warning"):
        warnings.append(str(risk_report["cash_warning"]))
    if risk_report.get("drawdown_warning"):
        warnings.append(str(risk_report["drawdown_warning"]))
    for w in risk_report.get("theme_warnings", []):
        warnings.append(str(w))
    for w in risk_report.get("concentration_warnings", []):
        warnings.append(str(w))
    return warnings[:MAX_RISK_WARNINGS]


def _stress_section(stress_run: Optional[dict]) -> dict:
    if not stress_run:
        return {"available": False}
    return {
        "available":      True,
        "run_id":         stress_run.get("run_id"),
        "worst_scenario": stress_run.get("worst_scenario"),
        "worst_loss_pct": stress_run.get("worst_loss_pct"),
        "avg_loss_pct":   stress_run.get("avg_loss_pct"),
        "warnings":       (stress_run.get("warnings") or [])[:2],
        "created_at":     stress_run.get("created_at"),
    }


def _checklists_section(pending: list) -> List[dict]:
    results = []
    for cl in pending[:MAX_CHECKLIST_ITEMS]:
        results.append({
            "ticker":           cl.get("ticker", ""),
            "decision_type":    cl.get("decision_type", ""),
            "checklist_status": cl.get("checklist_status", ""),
            "readiness":        cl.get("readiness", ""),
            "blocking_items":   cl.get("blocking_items", 0),
        })
    return results


def _thesis_reviews_section(due_reviews: dict, thesis_warnings: dict) -> dict:
    overdue  = due_reviews.get("overdue", [])
    upcoming = due_reviews.get("upcoming", [])
    return {
        "overdue_count":     len(overdue),
        "overdue":           [
            {"ticker": r.get("ticker", ""), "next_review_at": r.get("next_review_at")}
            for r in overdue[:MAX_THESIS_REVIEWS]
        ],
        "upcoming_count":    len(upcoming),
        "missing_thesis":    thesis_warnings.get("missing_thesis", [])[:MAX_THESIS_REVIEWS],
        "stale_thesis":      thesis_warnings.get("stale_thesis", [])[:MAX_THESIS_REVIEWS],
        "missing_exit_plan": thesis_warnings.get("missing_exit_plan", [])[:MAX_THESIS_REVIEWS],
    }


def _scorecard_warnings_section(scorecard_summary: dict) -> List[str]:
    """Collect scorecard warnings from bottom strategies and priority recs."""
    warnings: List[str] = []
    for s in scorecard_summary.get("bottom_strategies", []):
        score = s.get("risk_adjusted_score")
        if score is not None and score < 40:
            warnings.append(f"{s['strategy']}: low score ({score:.0f})")
    for rec in scorecard_summary.get("priority_recommendations", []):
        r = rec.get("recommendation", "")
        if r in ("reduce_exposure", "use_smaller_sizing", "avoid_during_risk_off"):
            warnings.append(
                f"{rec.get('strategy')}: {_safe_truncate(rec.get('description', r), 80)}"
            )
    # Deduplicate while preserving order
    seen: set = set()
    unique: List[str] = []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return unique[:MAX_SCORECARD_WARNINGS]


def _planner_section(snapshot: Optional[dict]) -> dict:
    if not snapshot:
        return {"available": False}
    priority_areas = (snapshot.get("priority_areas") or [])[:MAX_PLANNER_NOTES]
    return {
        "available":               True,
        "rebalance_urgency":       snapshot.get("rebalance_urgency", "NONE"),
        "priority_areas":          priority_areas,
        "cash_deployment_guidance": snapshot.get("cash_deployment_guidance", ""),
        "contribution_guidance":   snapshot.get("contribution_guidance", ""),
        "risk_reduction_guidance": snapshot.get("risk_reduction_guidance", ""),
        "regime":                  snapshot.get("regime", "NEUTRAL"),
        "created_at":              snapshot.get("created_at"),
    }


def _cash_tfsa_section(cash: float, room: float) -> dict:
    return {
        "cash":      round(cash, 2),
        "tfsa_room": round(room, 2),
    }


def _key_actions_section(
    regime_section: dict,
    risk_warnings: List[str],
    alpha_highlights: List[dict],
    checklists_due: List[dict],
    thesis_reviews: dict,
    planner_section: dict,
    scorecard_warnings: List[str],
) -> List[str]:
    """Derive the top actions from all sections. Max MAX_KEY_ACTIONS items."""
    actions: List[str] = []

    overall = regime_section.get("overall_regime", "NEUTRAL")
    if overall in ("RISK_OFF", "PANIC"):
        actions.append(f"Regime is {overall} — review position sizes and reduce risk.")
    elif overall == "RISK_ON":
        actions.append("Regime is RISK_ON — check Alpha highlights for opportunities.")

    for w in risk_warnings[:2]:
        actions.append(f"Review: {_safe_truncate(w, 80)}")

    for a in alpha_highlights[:2]:
        score = a.get("alpha_score")
        ticker = a.get("ticker", "")
        tier   = a.get("readiness_tier", "")
        if score is not None:
            actions.append(f"Watch {ticker} — {tier} (score {score:.0f})")
        else:
            actions.append(f"Watch {ticker} — {tier}")

    if checklists_due:
        items = ", ".join(c["ticker"] for c in checklists_due[:2])
        actions.append(f"Complete checklist for: {items}")

    if thesis_reviews.get("overdue_count", 0) > 0:
        tickers = ", ".join(
            r["ticker"] for r in thesis_reviews.get("overdue", [])[:2]
        )
        actions.append(f"Review overdue thesis for: {tickers}")

    urgency = planner_section.get("rebalance_urgency", "NONE")
    if urgency in ("HIGH", "MEDIUM"):
        actions.append(f"Consider rebalancing — allocation drift is {urgency}.")

    if scorecard_warnings:
        actions.append(f"Strategy flag: {_safe_truncate(scorecard_warnings[0], 80)}")

    return actions[:MAX_KEY_ACTIONS]


# ── Section assembly ───────────────────────────────────────────────────────────

def build_sections(data: dict) -> dict:
    """Assemble all sections from raw data. Pure function — no I/O."""
    pt    = _portfolio_truth_section(data.get("portfolio", {}))
    ov    = _overnight_section(data.get("overnight_signals", []))
    al    = _alpha_highlights_section(data.get("alpha_candidates", []))
    dr    = _dryrun_highlights_section(data.get("dry_runs", []))
    qc    = _qc_section(data.get("qc_summary", {}))
    reg   = _regime_section(data.get("regime_ctx", {}))
    rsk   = _risk_warnings_section(data.get("risk_report", {}))
    str_  = _stress_section(data.get("stress_run"))
    cl    = _checklists_section(data.get("pending_checklists", []))
    th    = _thesis_reviews_section(
                data.get("due_reviews", {}),
                data.get("thesis_warnings", {}),
            )
    sc    = _scorecard_warnings_section(data.get("scorecard_summary", {}))
    pl    = _planner_section(data.get("planner_snapshot"))
    ca    = _cash_tfsa_section(data.get("cash", 0.0), data.get("tfsa_room", 0.0))
    acts  = _key_actions_section(reg, rsk, al, cl, th, pl, sc)

    workflow = data.get("workflow_items", [])

    return {
        "portfolio_truth":        pt,
        "overnight_changes":      ov,
        "alpha_highlights":       al,
        "dryrun_highlights":      dr,
        "qc_suppression_summary": qc,
        "market_regime":          reg,
        "risk_warnings":          rsk,
        "stress_worst_case":      str_,
        "checklists_due":         cl,
        "thesis_reviews_due":     th,
        "scorecard_warnings":     sc,
        "planner_summary":        pl,
        "cash_tfsa_notes":        ca,
        "key_actions":            acts,
        "workflow_items":         workflow,
        "upcoming_catalysts":     data.get("upcoming_catalysts", []),
    }


# ── Formatters ─────────────────────────────────────────────────────────────────

def format_compact_brief(sections: dict, generated_at: Optional[str] = None) -> str:
    """
    Format sections into a WhatsApp-safe string ≤ COMPACT_MAX_CHARS chars.
    No hype language. Clear watch/review/no-action language.
    """
    now_str = generated_at or datetime.now(EASTERN).strftime("%b %-d, %Y %H:%M ET")
    lines = [
        "─" * 25,
        f"☀️  DAILY BRIEF — {now_str}",
        "",
    ]

    pt   = sections["portfolio_truth"]
    sign = _sign(pt["unrealized_pnl"])
    lines += [
        "PORTFOLIO",
        f"  {pt['position_count']} positions  |  Total: ${pt['total_value']:,.0f}",
        f"  P&L: {sign}${pt['unrealized_pnl']:,.0f} ({sign}{pt['unrealized_pnl_pct']:.1f}%)",
        "",
    ]

    reg     = sections["market_regime"]
    overall = reg.get("overall_regime", "NEUTRAL")
    score   = reg.get("regime_score", 50.0)
    icon    = "\U0001f534" if overall in ("RISK_OFF", "PANIC") else (
              "\U0001f7e2" if overall == "RISK_ON" else "\U0001f7e1"
    )
    lines.append(f"REGIME: {icon} {overall} (score {score:.0f})")
    lines.append("")

    alpha = sections["alpha_highlights"]
    if alpha:
        lines.append("ALPHA WATCH")
        for a in alpha:
            s = f"{a['alpha_score']:.0f}" if a.get("alpha_score") is not None else "?"
            lines.append(f"  • {a['ticker']}  {a.get('readiness_tier','')}  score={s}")
        lines.append("")

    risks = sections["risk_warnings"]
    if risks:
        lines.append("RISK FLAGS")
        for r in risks:
            lines.append(f"  ⚠ {_safe_truncate(r, 70)}")
        lines.append("")

    stress = sections["stress_worst_case"]
    if stress.get("available"):
        worst = stress.get("worst_scenario", "N/A")
        pct   = stress.get("worst_loss_pct", 0.0) or 0.0
        lines.append(f"STRESS: worst case {worst} = {pct:.1f}%")
        lines.append("")

    review_items: List[str] = []
    for c in sections["checklists_due"]:
        review_items.append(
            f"\U0001f4cb {c['ticker']} decision ({c['checklist_status']})"
        )
    th = sections["thesis_reviews_due"]
    for r in th.get("overdue", []):
        review_items.append(f"\U0001f4d6 Review overdue: {r['ticker']}")
    for t in th.get("missing_thesis", []):
        review_items.append(f"⚠ No thesis: {t}")
    if review_items:
        lines.append("TO REVIEW")
        for item in review_items[:MAX_CHECKLIST_ITEMS]:
            lines.append(f"  {item}")
        lines.append("")

    planner = sections["planner_summary"]
    if planner.get("available") and planner.get("rebalance_urgency") not in ("NONE", None):
        lines.append(f"ALLOCATION: drift is {planner['rebalance_urgency']}")
        lines.append("")

    ca = sections["cash_tfsa_notes"]
    lines.append(f"Cash: ${ca['cash']:,.0f}  |  TFSA room: ${ca['tfsa_room']:,.0f}")
    lines.append("")

    actions = sections["key_actions"]
    if actions:
        lines.append("TODAY")
        for act in actions[:5]:
            lines.append(f"  → {_safe_truncate(act, 80)}")
        lines.append("")

    workflow = sections.get("workflow_items", [])
    if workflow:
        lines.append("RESEARCH QUEUE")
        for w in workflow[:3]:
            ticker  = w.get("ticker") or "—"
            reason  = _safe_truncate(w.get("reason", ""), 60)
            lines.append(f"  • {ticker}: {reason}")
        lines.append("")

    cats = sections.get("upcoming_catalysts", [])
    if cats:
        lines.append("UPCOMING CATALYSTS")
        for c in cats[:3]:
            ticker_str = f"{c.get('ticker')} " if c.get("ticker") else ""
            lines.append(f"  • {ticker_str}{_safe_truncate(c.get('title', ''), 50)} [{c.get('date', '')}]")
        lines.append("")

    lines.append("─" * 25)

    result = "\n".join(lines)
    if len(result) > COMPACT_MAX_CHARS:
        result = result[: COMPACT_MAX_CHARS - 3] + "..."
    return result


def format_detailed_brief(sections: dict, generated_at: Optional[str] = None) -> dict:
    """Return the full structured brief dict with all sections."""
    return {
        "mode":         "detailed",
        "generated_at": generated_at or datetime.now(EASTERN).isoformat(),
        **sections,
    }


def format_debug_brief(
    sections: dict,
    data: dict,
    generated_at: Optional[str] = None,
) -> dict:
    """Return detailed brief with data-source freshness metadata appended."""
    brief = format_detailed_brief(sections, generated_at)
    brief["mode"] = "debug"
    brief["data_sources"] = {
        "portfolio_available":        bool(data.get("portfolio")),
        "alpha_candidates_count":     len(data.get("alpha_candidates", [])),
        "dry_runs_count":             len(data.get("dry_runs", [])),
        "regime_available":           bool(data.get("regime_ctx", {}).get("available")),
        "stress_run_available":       data.get("stress_run") is not None,
        "planner_snapshot_available": data.get("planner_snapshot") is not None,
        "pending_checklists_count":   len(data.get("pending_checklists", [])),
        "scorecard_computed_at":      data.get("scorecard_summary", {}).get("computed_at"),
    }
    return brief


# ── Data collection ────────────────────────────────────────────────────────────

def collect_brief_data() -> dict:
    """
    Fetch data from all systems.  Each source is individually wrapped in
    try/except.  Never raises.  Returns a dict keyed by source name.
    """
    data: dict = {}

    # Portfolio truth via reconciliation layer
    try:
        from portfolio_reconciliation import reconcile_portfolio
        data["portfolio"] = reconcile_portfolio(trigger="operator_brief")
    except Exception:
        log.warning("operator_brief: portfolio reconcile failed", exc_info=True)
        data["portfolio"] = {}

    # Cash and TFSA contribution room
    try:
        import portfolio as _pm
        data["cash"]      = float(_pm.get_cash())
        data["tfsa_room"] = float(_pm.get_tfsa_room())
    except Exception:
        log.warning("operator_brief: cash/room fetch failed", exc_info=True)
        data["cash"]      = 0.0
        data["tfsa_room"] = 0.0

    # Overnight WARNING signals from alert_log (today only)
    try:
        from database import get_connection
        now_et      = datetime.now(EASTERN)
        today_start = now_et.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT ticker, message FROM alert_log "
                "WHERE urgency = 'WARNING' AND sent_at >= ? "
                "ORDER BY sent_at DESC LIMIT 10",
                (today_start,),
            ).fetchall()
        finally:
            conn.close()
        data["overnight_signals"] = [
            f"{row['ticker']}: {_safe_truncate(str(row['message'] or ''), 80)}"
            for row in rows
            if row["ticker"] and row["message"]
        ]
    except Exception:
        log.warning("operator_brief: overnight signals fetch failed", exc_info=True)
        data["overnight_signals"] = []

    # Alpha alert candidates
    try:
        from alpha_alert_gate import get_alert_candidates
        data["alpha_candidates"] = get_alert_candidates(limit=50)
    except Exception:
        log.warning("operator_brief: alpha candidates fetch failed", exc_info=True)
        data["alpha_candidates"] = []

    # Dry-run notification queue
    try:
        from alpha_notification_dryrun import get_dry_runs
        data["dry_runs"] = get_dry_runs(status_filter="DRY_RUN", limit=20)
    except Exception:
        log.warning("operator_brief: dry-run fetch failed", exc_info=True)
        data["dry_runs"] = []

    # QC suppression summary
    try:
        from alpha_notification_qc import get_qc_summary
        data["qc_summary"] = get_qc_summary()
    except Exception:
        log.warning("operator_brief: qc summary fetch failed", exc_info=True)
        data["qc_summary"] = {}

    # Market regime context
    try:
        from market_regime_intelligence import get_regime_context_for_checklist
        data["regime_ctx"] = get_regime_context_for_checklist()
    except Exception:
        log.warning("operator_brief: regime fetch failed", exc_info=True)
        data["regime_ctx"] = {
            "available": False, "overall_regime": "NEUTRAL",
            "regime_score": 50.0, "warnings": [],
        }

    # Portfolio risk report
    try:
        from portfolio_risk_guardrails import get_portfolio_risk_report
        data["risk_report"] = get_portfolio_risk_report()
    except Exception:
        log.warning("operator_brief: risk report fetch failed", exc_info=True)
        data["risk_report"] = {}

    # Latest stress test run
    try:
        from portfolio_stress_testing import get_stress_history, get_stress_run
        runs = get_stress_history(limit=1)
        data["stress_run"] = get_stress_run(runs[0]["run_id"]) if runs else None
    except Exception:
        log.warning("operator_brief: stress test fetch failed", exc_info=True)
        data["stress_run"] = None

    # Pending decision checklists
    try:
        from decision_checklist import get_pending_checklists
        data["pending_checklists"] = get_pending_checklists()
    except Exception:
        log.warning("operator_brief: checklists fetch failed", exc_info=True)
        data["pending_checklists"] = []

    # Thesis reviews and warnings
    try:
        from position_journal import get_due_reviews, get_thesis_warnings
        data["due_reviews"]     = get_due_reviews()
        data["thesis_warnings"] = get_thesis_warnings()
    except Exception:
        log.warning("operator_brief: thesis reviews fetch failed", exc_info=True)
        data["due_reviews"] = {
            "due": [], "overdue": [], "upcoming": [],
            "due_count": 0, "overdue_count": 0, "upcoming_count": 0,
        }
        data["thesis_warnings"] = {
            "missing_thesis": [], "stale_thesis": [],
            "missing_exit_plan": [], "has_warnings": False,
        }

    # Strategy scorecard summary
    try:
        from strategy_scorecards import get_scorecards_summary
        data["scorecard_summary"] = get_scorecards_summary()
    except Exception:
        log.warning("operator_brief: scorecard summary fetch failed", exc_info=True)
        data["scorecard_summary"] = {
            "top_strategies": [], "bottom_strategies": [],
            "priority_recommendations": [], "behavior_metrics": {},
        }

    # Latest planner snapshot
    try:
        from compounding_planner import get_latest_planner_snapshot
        data["planner_snapshot"] = get_latest_planner_snapshot()
    except Exception:
        log.warning("operator_brief: planner snapshot fetch failed", exc_info=True)
        data["planner_snapshot"] = None

    # Top research workflow items (A25)
    try:
        from research_workflow import get_brief_items
        data["workflow_items"] = get_brief_items(limit=3)
    except Exception:
        log.warning("operator_brief: workflow items fetch failed", exc_info=True)
        data["workflow_items"] = []

    # Upcoming catalysts (A27)
    try:
        from catalyst_calendar import get_brief_catalysts
        data["upcoming_catalysts"] = get_brief_catalysts()
    except Exception:
        log.warning("operator_brief: catalyst calendar fetch failed", exc_info=True)
        data["upcoming_catalysts"] = []

    return data


# ── Entry points ───────────────────────────────────────────────────────────────

def generate_brief(mode: str = "detailed") -> Any:
    """
    Collect data from all systems and return the formatted brief.

    mode:
      compact  — str, WhatsApp-safe, ≤ COMPACT_MAX_CHARS
      detailed — dict with all sections
      debug    — dict with all sections + data-source metadata
    """
    if mode not in MODES:
        mode = _DEFAULT_MODE

    generated_at = datetime.now(EASTERN).isoformat()
    data         = collect_brief_data()
    sections     = build_sections(data)

    if mode == "compact":
        return format_compact_brief(sections, generated_at)
    if mode == "debug":
        return format_debug_brief(sections, data, generated_at)
    return format_detailed_brief(sections, generated_at)


def generate_compact_brief() -> str:
    """
    Convenience entry point for the morning scheduler.
    Returns a WhatsApp-safe compact brief string.
    Never raises — returns a safe fallback on error.
    """
    try:
        return generate_brief(mode="compact")
    except Exception:
        log.error("operator_brief: generate_compact_brief failed", exc_info=True)
        now_str = datetime.now(EASTERN).strftime("%b %-d, %Y %H:%M ET")
        return f"☀️  DAILY BRIEF — {now_str}\n\nBrief unavailable. Check system logs."
