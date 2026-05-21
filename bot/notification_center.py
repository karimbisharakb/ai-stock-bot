"""
Phase N4 — Unified In-App Notification Center.

Backend inbox that the iOS app polls. All notifications are read-only advisory
items — no WhatsApp sends, no trade execution, no push notifications.

Feature flag: NOTIFICATION_CENTER_ENABLED (default True).
"""
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import database

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

CATEGORIES = (
    "PORTFOLIO",
    "ALPHA_SIGNAL",
    "RISK",
    "MARKET",
    "RESEARCH",
    "REGIME",
    "CATALYST",
    "SYSTEM",
    "PERFORMANCE",
    "COMPLIANCE",
)

SEVERITIES = ("CRITICAL", "WARNING", "INFO", "DEBUG")

STATUSES = ("UNREAD", "READ", "ARCHIVED", "DISMISSED")

BANNED_WORDS = frozenset({
    "explosion", "pre-explosion", "moon", "guaranteed", "must buy",
    "moonshot", "to the moon", "100x", "sure thing",
})

TOP_N_SUMMARY = 5
STALE_HOURS = 72


# ── Feature flag ──────────────────────────────────────────────────────────────

def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name, "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def notification_center_enabled() -> bool:
    """NOTIFICATION_CENTER_ENABLED env var (default True)."""
    return _env_bool("NOTIFICATION_CENTER_ENABLED", True)


# ── Deterministic ID ──────────────────────────────────────────────────────────

def _make_notification_id(source: str, category: str, entity_type: str, entity_id: str) -> str:
    raw = f"{source}:{category}:{entity_type}:{entity_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── DB helpers ────────────────────────────────────────────────────────────────

def _ensure_table() -> None:
    conn = database.get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notification_center (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_id  TEXT NOT NULL UNIQUE,
                category         TEXT NOT NULL DEFAULT 'SYSTEM',
                severity         TEXT NOT NULL DEFAULT 'INFO',
                title            TEXT NOT NULL,
                body             TEXT NOT NULL DEFAULT '',
                entity_type      TEXT,
                entity_id        TEXT,
                source           TEXT NOT NULL DEFAULT 'system',
                status           TEXT NOT NULL DEFAULT 'UNREAD',
                action_url       TEXT,
                metadata_json    TEXT NOT NULL DEFAULT '{}',
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nc_status   ON notification_center(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nc_category ON notification_center(category)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nc_severity ON notification_center(severity)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nc_created  ON notification_center(created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nc_entity   ON notification_center(entity_type, entity_id)"
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row) -> dict:
    import json
    d = dict(row)
    try:
        d["metadata"] = json.loads(d.pop("metadata_json", "{}") or "{}")
    except Exception:
        d["metadata"] = {}
    return d


def _upsert_notification(
    *,
    notification_id: str,
    category: str,
    severity: str,
    title: str,
    body: str,
    entity_type: Optional[str],
    entity_id: Optional[str],
    source: str,
    action_url: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    import json

    _ensure_table()
    now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    meta_json = json.dumps(metadata or {})

    conn = database.get_connection()
    try:
        existing = conn.execute(
            "SELECT status FROM notification_center WHERE notification_id = ?",
            (notification_id,),
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE notification_center
                   SET title=?, body=?, severity=?, action_url=?, metadata_json=?, updated_at=?
                   WHERE notification_id=?""",
                (title, body, severity, action_url, meta_json, now, notification_id),
            )
        else:
            conn.execute(
                """INSERT INTO notification_center
                   (notification_id, category, severity, title, body,
                    entity_type, entity_id, source, status,
                    action_url, metadata_json, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    notification_id, category, severity, title, body,
                    entity_type, entity_id, source, "UNREAD",
                    action_url, meta_json, now, now,
                ),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM notification_center WHERE notification_id=?",
            (notification_id,),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


# ── Public read API ───────────────────────────────────────────────────────────

def get_notification(notification_id: str) -> Optional[dict]:
    _ensure_table()
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM notification_center WHERE notification_id=?",
            (notification_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_notifications(
    *,
    status: Optional[str] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list:
    _ensure_table()
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status.upper())
    if category:
        clauses.append("category = ?")
        params.append(category.upper())
    if severity:
        clauses.append("severity = ?")
        params.append(severity.upper())
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params += [max(1, min(limit, 200)), max(0, offset)]
    conn = database.get_connection()
    try:
        rows = conn.execute(
            f"SELECT * FROM notification_center {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


# ── User actions ──────────────────────────────────────────────────────────────

def _set_status(notification_id: str, new_status: str) -> dict:
    _ensure_table()
    now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM notification_center WHERE notification_id=?",
            (notification_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"notification {notification_id!r} not found")
        conn.execute(
            "UPDATE notification_center SET status=?, updated_at=? WHERE notification_id=?",
            (new_status, now, notification_id),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM notification_center WHERE notification_id=?",
            (notification_id,),
        ).fetchone()
        return _row_to_dict(updated)
    finally:
        conn.close()


def mark_read(notification_id: str) -> dict:
    return _set_status(notification_id, "READ")


def mark_unread(notification_id: str) -> dict:
    return _set_status(notification_id, "UNREAD")


def dismiss(notification_id: str) -> dict:
    return _set_status(notification_id, "DISMISSED")


def archive(notification_id: str) -> dict:
    return _set_status(notification_id, "ARCHIVED")


def mark_all_read() -> int:
    _ensure_table()
    now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    conn = database.get_connection()
    try:
        cur = conn.execute(
            "UPDATE notification_center SET status='READ', updated_at=? WHERE status='UNREAD'",
            (now,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def archive_read() -> int:
    _ensure_table()
    now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    conn = database.get_connection()
    try:
        cur = conn.execute(
            "UPDATE notification_center SET status='ARCHIVED', updated_at=? WHERE status='READ'",
            (now,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ── Source generators ─────────────────────────────────────────────────────────

def _gen_portfolio_value_change() -> list:
    try:
        from portfolio import get_holdings, get_cash
        import market_data as md

        holdings = get_holdings()
        if not holdings:
            return []

        results = []
        for h in holdings:
            ticker = h.get("ticker", "")
            shares = float(h.get("shares", 0) or 0)
            if not ticker or shares <= 0:
                continue
            try:
                price_data = md.get_price_data(ticker)
                change_pct = float(price_data.get("change_pct", 0) or 0)
            except Exception:
                continue

            if abs(change_pct) >= 3.0:
                severity = "WARNING" if change_pct <= -3.0 else "INFO"
                direction = "down" if change_pct < 0 else "up"
                nid = _make_notification_id("portfolio_value_change", "PORTFOLIO", "holding", ticker)
                results.append({
                    "notification_id": nid,
                    "category": "PORTFOLIO",
                    "severity": severity,
                    "title": f"{ticker} {direction} {abs(change_pct):.1f}% today",
                    "body": f"{ticker} moved {change_pct:+.1f}% today. Review your position.",
                    "entity_type": "holding",
                    "entity_id": ticker,
                    "source": "portfolio_value_change",
                })
        return results
    except Exception as exc:
        log.debug("_gen_portfolio_value_change: %s", exc)
        return []


def _gen_sell_signal_active() -> list:
    try:
        from sell_monitor import get_sell_signals_for_holdings
        signals = get_sell_signals_for_holdings()
        results = []
        for sig in (signals or []):
            ticker = sig.get("ticker", "")
            urgency = sig.get("urgency", "FYI")
            reason = sig.get("reason", "")
            nid = _make_notification_id("sell_signal", "PORTFOLIO", "holding", ticker)
            severity = "CRITICAL" if urgency == "URGENT" else "WARNING"
            results.append({
                "notification_id": nid,
                "category": "PORTFOLIO",
                "severity": severity,
                "title": f"Sell signal: {ticker} ({urgency})",
                "body": reason or f"Technical sell signal detected for {ticker}.",
                "entity_type": "holding",
                "entity_id": ticker,
                "source": "sell_signal",
            })
        return results
    except Exception as exc:
        log.debug("_gen_sell_signal_active: %s", exc)
        return []


def _gen_alpha_top_opportunity() -> list:
    try:
        from alpha_shadow import get_shadow_manager
        manager = get_shadow_manager()
        top = manager.get_top_opportunities(limit=3) if hasattr(manager, "get_top_opportunities") else []
        results = []
        for opp in (top or []):
            ticker = opp.get("ticker", "")
            score = opp.get("alpha_score", 0)
            tier = opp.get("tier", "WATCHLIST")
            nid = _make_notification_id("alpha_top", "ALPHA_SIGNAL", "alpha", ticker)
            results.append({
                "notification_id": nid,
                "category": "ALPHA_SIGNAL",
                "severity": "INFO",
                "title": f"Alpha opportunity: {ticker} (score {score})",
                "body": f"{ticker} scores {score}/100 in alpha engine — tier {tier}. Advisory only.",
                "entity_type": "alpha",
                "entity_id": ticker,
                "source": "alpha_top",
            })
        return results
    except Exception as exc:
        log.debug("_gen_alpha_top_opportunity: %s", exc)
        return []


def _gen_regime_change() -> list:
    try:
        from market_regime_intelligence import get_current_regime
        regime = get_current_regime()
        if regime is None:
            return []
        overall = regime.get("overall", "NEUTRAL")
        score = regime.get("regime_score", 50)
        nid = _make_notification_id("regime_snapshot", "REGIME", "regime", overall)
        severity = "WARNING" if overall in ("BEAR", "HIGH_VOLATILITY") else "INFO"
        return [{
            "notification_id": nid,
            "category": "REGIME",
            "severity": severity,
            "title": f"Market regime: {overall} (score {score})",
            "body": f"Current regime reading: {overall}, regime score {score}/100. Advisory only.",
            "entity_type": "regime",
            "entity_id": overall,
            "source": "regime_change",
        }]
    except Exception as exc:
        log.debug("_gen_regime_change: %s", exc)
        return []


def _gen_risk_guardrail_breach() -> list:
    try:
        from portfolio_risk_guardrails import check_portfolio_risk
        result = check_portfolio_risk()
        if not result:
            return []
        risk_score = result.get("risk_score", 0)
        tier = result.get("tier", "NORMAL")
        if tier in ("TOO_RISKY", "SMALL_ONLY"):
            nid = _make_notification_id("risk_guardrail", "RISK", "risk", tier)
            return [{
                "notification_id": nid,
                "category": "RISK",
                "severity": "WARNING",
                "title": f"Risk guardrail: {tier} (score {risk_score})",
                "body": f"Portfolio risk tier is {tier} with score {risk_score}/100. Review sizing.",
                "entity_type": "risk",
                "entity_id": tier,
                "source": "risk_guardrail",
            }]
        return []
    except Exception as exc:
        log.debug("_gen_risk_guardrail_breach: %s", exc)
        return []


def _gen_upcoming_catalyst() -> list:
    try:
        from catalyst_calendar import get_brief_catalysts
        cats = get_brief_catalysts()
        results = []
        for c in (cats or [])[:5]:
            cat_id = c.get("catalyst_id", c.get("title", ""))
            ticker = c.get("ticker") or ""
            title = c.get("title", "")
            date_str = c.get("date", "")
            nid = _make_notification_id("catalyst", "CATALYST", "catalyst", cat_id)
            label = f"{ticker} — " if ticker else ""
            results.append({
                "notification_id": nid,
                "category": "CATALYST",
                "severity": "INFO",
                "title": f"Upcoming catalyst: {label}{title}",
                "body": f"{label}Catalyst '{title}' on {date_str}. Monitor for opportunity.",
                "entity_type": "catalyst",
                "entity_id": cat_id,
                "source": "upcoming_catalyst",
            })
        return results
    except Exception as exc:
        log.debug("_gen_upcoming_catalyst: %s", exc)
        return []


def _gen_research_item_due() -> list:
    try:
        from research_workflow import get_due_reviews
        items = get_due_reviews(limit=5) if callable(
            getattr(__import__("research_workflow", fromlist=[None]), "get_due_reviews", None)
        ) else []
        results = []
        for item in (items or []):
            item_id = str(item.get("id", item.get("title", "")))
            ticker = item.get("ticker", "")
            title = item.get("title", item.get("ticker", "research item"))
            nid = _make_notification_id("research_due", "RESEARCH", "research", item_id)
            results.append({
                "notification_id": nid,
                "category": "RESEARCH",
                "severity": "INFO",
                "title": f"Research due: {ticker or title}",
                "body": f"Research item '{title}' is due for review.",
                "entity_type": "research",
                "entity_id": item_id,
                "source": "research_due",
            })
        return results
    except Exception as exc:
        log.debug("_gen_research_item_due: %s", exc)
        return []


def _gen_checklist_overdue() -> list:
    try:
        from decision_checklist import get_overdue_checklists
        items = get_overdue_checklists() if callable(
            getattr(__import__("decision_checklist", fromlist=[None]), "get_overdue_checklists", None)
        ) else []
        results = []
        for item in (items or [])[:3]:
            cl_id = str(item.get("checklist_id", item.get("ticker", "")))
            ticker = item.get("ticker", "")
            nid = _make_notification_id("checklist_overdue", "COMPLIANCE", "checklist", cl_id)
            results.append({
                "notification_id": nid,
                "category": "COMPLIANCE",
                "severity": "WARNING",
                "title": f"Checklist overdue: {ticker or cl_id}",
                "body": f"Decision checklist for {ticker or cl_id} is overdue for review.",
                "entity_type": "checklist",
                "entity_id": cl_id,
                "source": "checklist_overdue",
            })
        return results
    except Exception as exc:
        log.debug("_gen_checklist_overdue: %s", exc)
        return []


def _gen_stress_test_severe() -> list:
    try:
        from portfolio_stress_testing import get_latest_stress_report
        report = get_latest_stress_report()
        if not report:
            return []
        risk_level = report.get("aggregate_risk_level", "LOW")
        if risk_level in ("HIGH", "SEVERE"):
            nid = _make_notification_id("stress_test", "RISK", "stress", risk_level)
            return [{
                "notification_id": nid,
                "category": "RISK",
                "severity": "WARNING",
                "title": f"Stress test result: {risk_level}",
                "body": f"Latest portfolio stress test shows {risk_level} aggregate risk. Review positions.",
                "entity_type": "stress",
                "entity_id": risk_level,
                "source": "stress_test",
            }]
        return []
    except Exception as exc:
        log.debug("_gen_stress_test_severe: %s", exc)
        return []


def _gen_alpha_gate_not_ready() -> list:
    try:
        from alpha_alert_gate import get_current_readiness
        readiness = get_current_readiness()
        if not readiness:
            return []
        tier = readiness.get("readiness_tier", "")
        if tier == "NOT_READY":
            nid = _make_notification_id("alpha_gate", "ALPHA_SIGNAL", "gate", tier)
            return [{
                "notification_id": nid,
                "category": "ALPHA_SIGNAL",
                "severity": "WARNING",
                "title": "Alpha gate: NOT_READY",
                "body": "Alpha alert gate is in NOT_READY state — no new alpha alerts will fire.",
                "entity_type": "gate",
                "entity_id": tier,
                "source": "alpha_gate",
            }]
        return []
    except Exception as exc:
        log.debug("_gen_alpha_gate_not_ready: %s", exc)
        return []


def _gen_proposal_pending_review() -> list:
    try:
        from weight_proposals import get_pending_proposals
        proposals = get_pending_proposals() if callable(
            getattr(__import__("weight_proposals", fromlist=[None]), "get_pending_proposals", None)
        ) else []
        results = []
        for prop in (proposals or [])[:3]:
            prop_id = prop.get("proposal_id", "")
            nid = _make_notification_id("proposal_pending", "SYSTEM", "proposal", prop_id)
            results.append({
                "notification_id": nid,
                "category": "SYSTEM",
                "severity": "INFO",
                "title": f"Weight proposal pending review: {prop_id[:8]}",
                "body": "A weight adjustment proposal is pending your review in the operator panel.",
                "entity_type": "proposal",
                "entity_id": prop_id,
                "source": "proposal_pending",
            })
        return results
    except Exception as exc:
        log.debug("_gen_proposal_pending_review: %s", exc)
        return []


def _gen_journal_review_needed() -> list:
    try:
        from position_journal import get_positions_needing_review
        items = get_positions_needing_review() if callable(
            getattr(__import__("position_journal", fromlist=[None]), "get_positions_needing_review", None)
        ) else []
        results = []
        for item in (items or [])[:5]:
            ticker = item.get("ticker", "")
            nid = _make_notification_id("journal_review", "PORTFOLIO", "journal", ticker)
            results.append({
                "notification_id": nid,
                "category": "PORTFOLIO",
                "severity": "INFO",
                "title": f"Journal review needed: {ticker}",
                "body": f"Position journal for {ticker} is due for review.",
                "entity_type": "journal",
                "entity_id": ticker,
                "source": "journal_review",
            })
        return results
    except Exception as exc:
        log.debug("_gen_journal_review_needed: %s", exc)
        return []


def _gen_strategy_scorecard_alert() -> list:
    try:
        from strategy_scorecards import get_underperforming_strategies
        strats = get_underperforming_strategies() if callable(
            getattr(__import__("strategy_scorecards", fromlist=[None]), "get_underperforming_strategies", None)
        ) else []
        results = []
        for strat in (strats or [])[:3]:
            name = strat.get("strategy_name", "unknown")
            nid = _make_notification_id("strategy_scorecard", "PERFORMANCE", "strategy", name)
            results.append({
                "notification_id": nid,
                "category": "PERFORMANCE",
                "severity": "WARNING",
                "title": f"Strategy underperforming: {name}",
                "body": f"Strategy '{name}' is flagged as underperforming. Review scorecard.",
                "entity_type": "strategy",
                "entity_id": name,
                "source": "strategy_scorecard",
            })
        return results
    except Exception as exc:
        log.debug("_gen_strategy_scorecard_alert: %s", exc)
        return []


def _gen_market_index_drop() -> list:
    try:
        import market_data as md
        results = []
        for index_ticker, label in (("^GSPC", "S&P 500"), ("^OSPTX", "TSX")):
            try:
                change = md.get_index_day_change(index_ticker)
                if change is not None and change <= -1.5:
                    nid = _make_notification_id("index_drop", "MARKET", "index", index_ticker)
                    results.append({
                        "notification_id": nid,
                        "category": "MARKET",
                        "severity": "WARNING",
                        "title": f"{label} down {abs(change):.1f}% today",
                        "body": f"{label} dropped {abs(change):.1f}% today. Monitor for broad market risk.",
                        "entity_type": "index",
                        "entity_id": index_ticker,
                        "source": "market_index_drop",
                    })
            except Exception:
                pass
        return results
    except Exception as exc:
        log.debug("_gen_market_index_drop: %s", exc)
        return []


def _gen_weekly_grade_degraded() -> list:
    try:
        conn = database.get_connection()
        try:
            row = conn.execute(
                "SELECT grade FROM weekly_review_log ORDER BY sent_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return []
        grade = (row["grade"] if hasattr(row, "__getitem__") else row[0]) or ""
        if grade in ("D", "F"):
            nid = _make_notification_id("weekly_grade", "PERFORMANCE", "weekly", grade)
            return [{
                "notification_id": nid,
                "category": "PERFORMANCE",
                "severity": "WARNING",
                "title": f"Weekly review grade: {grade}",
                "body": f"Last weekly accountability review scored a {grade}. Review discipline metrics.",
                "entity_type": "weekly",
                "entity_id": grade,
                "source": "weekly_grade",
            }]
        return []
    except Exception as exc:
        log.debug("_gen_weekly_grade_degraded: %s", exc)
        return []


# ── Generator registry ────────────────────────────────────────────────────────

_GENERATORS = [
    _gen_portfolio_value_change,
    _gen_sell_signal_active,
    _gen_alpha_top_opportunity,
    _gen_regime_change,
    _gen_risk_guardrail_breach,
    _gen_upcoming_catalyst,
    _gen_research_item_due,
    _gen_checklist_overdue,
    _gen_stress_test_severe,
    _gen_alpha_gate_not_ready,
    _gen_proposal_pending_review,
    _gen_journal_review_needed,
    _gen_strategy_scorecard_alert,
    _gen_market_index_drop,
    _gen_weekly_grade_degraded,
]


def _sanitize_body(body: str) -> str:
    import re
    for word in BANNED_WORDS:
        body = re.sub(re.escape(word), "[redacted]", body, flags=re.IGNORECASE)
    return body


def generate_notifications() -> dict:
    """
    Run all generators, upsert results, return summary.

    Never raises — each generator is individually guarded.
    """
    if not notification_center_enabled():
        log.info("notification_center: NOTIFICATION_CENTER_ENABLED=false — skipping generation")
        return {"generated": 0, "skipped": True}

    _ensure_table()

    total = 0
    errors = 0

    for gen_fn in _GENERATORS:
        try:
            items = gen_fn() or []
        except Exception as exc:
            log.warning("generate_notifications: generator %s failed: %s", gen_fn.__name__, exc)
            errors += 1
            items = []

        for item in items:
            try:
                item["body"] = _sanitize_body(item.get("body", ""))
                _upsert_notification(
                    notification_id = item["notification_id"],
                    category        = item.get("category", "SYSTEM"),
                    severity        = item.get("severity", "INFO"),
                    title           = item.get("title", ""),
                    body            = item.get("body", ""),
                    entity_type     = item.get("entity_type"),
                    entity_id       = item.get("entity_id"),
                    source          = item.get("source", "system"),
                    action_url      = item.get("action_url"),
                    metadata        = item.get("metadata"),
                )
                total += 1
            except Exception as exc:
                log.warning("generate_notifications: upsert failed for %s: %s",
                            item.get("notification_id"), exc)
                errors += 1

    generated_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    log.info(
        "notification_center: generation complete — %d upserted, %d errors",
        total, errors,
    )
    return {
        "generated": total,
        "errors": errors,
        "generated_at": generated_at,
        "skipped": False,
    }


# ── Summary ───────────────────────────────────────────────────────────────────

def get_summary() -> dict:
    """
    Aggregated inbox state for the iOS status badge.

    Returns: unread_count, critical_count, warning_count, by_category, by_severity,
             top_notifications, stale_notification_count, generated_at
    """
    _ensure_table()
    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM notification_center WHERE status NOT IN ('ARCHIVED','DISMISSED') "
            "ORDER BY created_at DESC"
        ).fetchall()
    finally:
        conn.close()

    all_items = [_row_to_dict(r) for r in rows]

    now_iso = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    unread = [n for n in all_items if n["status"] == "UNREAD"]
    critical = [n for n in unread if n["severity"] == "CRITICAL"]
    warning = [n for n in unread if n["severity"] == "WARNING"]

    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for n in unread:
        by_category[n["category"]] = by_category.get(n["category"], 0) + 1
        by_severity[n["severity"]] = by_severity.get(n["severity"], 0) + 1

    # Stale = UNREAD, older than STALE_HOURS
    stale_count = 0
    try:
        from datetime import timedelta
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=STALE_HOURS)
        cutoff_iso = cutoff.isoformat(timespec="seconds")
        stale_count = sum(
            1 for n in unread
            if n.get("created_at", "") < cutoff_iso
        )
    except Exception:
        pass

    top = unread[:TOP_N_SUMMARY]

    return {
        "unread_count": len(unread),
        "critical_count": len(critical),
        "warning_count": len(warning),
        "by_category": by_category,
        "by_severity": by_severity,
        "top_notifications": top,
        "stale_notification_count": stale_count,
        "generated_at": now_iso,
    }
