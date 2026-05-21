"""
Research Workflow Automation and Review Cadence — Phase A25.

Turns the research watchlist into an active workflow system: deterministic
review queue, priority scoring, status transitions, append-only notes,
daily summary, and integration hooks for the morning/EOD briefs.

No broker integration, no order placement, no autonomous trading.
No alerts sent. Archive-only (no deletes). Append-only notes.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

ITEM_STATUSES   = frozenset({"OPEN", "IN_PROGRESS", "DONE", "SNOOZED", "ARCHIVED"})
ENTITY_TYPES    = frozenset({"WATCHLIST", "ALPHA_CANDIDATE", "THESIS", "CHECKLIST",
                              "VALIDATION", "SCORECARD", "REPLAY", "REGIME", "NONE"})
SOURCES         = frozenset({"watchlist_due", "watchlist_stale", "alpha_gate",
                              "validation_trends", "replay_missed", "thesis_due",
                              "scorecard_warning", "regime_change"})

# Score weights (all positive; higher = more urgent)
_W_URGENCY     = 40   # deadline-based urgency
_W_OPPORTUNITY = 30   # alpha/readiness opportunity score
_W_RISK        = 20   # risk/warning signals
_W_STALE       = 10   # staleness penalty

MAX_QUEUE_ITEMS       = 50
MAX_SUMMARY_TOP       = 3
MAX_SUMMARY_OVERDUE   = 5
MAX_SUMMARY_SNOOZED   = 5
MAX_SUMMARY_NEW       = 5
MAX_BRIEF_ITEMS       = 3   # morning/EOD brief integration cap

# Snooze defaults
_DEFAULT_SNOOZE_HOURS = 24

# Stale thresholds (days)
_WATCHLIST_STALE_DAYS = 30
_ALPHA_STALE_DAYS     = 3


# ── Table bootstrap ───────────────────────────────────────────────────────────

def _ensure_tables() -> None:
    from database import get_connection
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS research_workflow_items (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id             TEXT    NOT NULL UNIQUE,
                ticker              TEXT,
                source              TEXT    NOT NULL,
                priority            TEXT    NOT NULL DEFAULT 'MEDIUM',
                reason              TEXT    NOT NULL DEFAULT '',
                due_at              TEXT,
                status              TEXT    NOT NULL DEFAULT 'OPEN',
                linked_entity_type  TEXT    NOT NULL DEFAULT 'NONE',
                linked_entity_id    TEXT,
                urgency_score       REAL    NOT NULL DEFAULT 0.0,
                opportunity_score   REAL    NOT NULL DEFAULT 0.0,
                risk_score          REAL    NOT NULL DEFAULT 0.0,
                stale_score         REAL    NOT NULL DEFAULT 0.0,
                priority_score      REAL    NOT NULL DEFAULT 0.0,
                snoozed_until       TEXT,
                created_at          TEXT    NOT NULL,
                updated_at          TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rwf_status    ON research_workflow_items(status);
            CREATE INDEX IF NOT EXISTS idx_rwf_ticker    ON research_workflow_items(ticker);
            CREATE INDEX IF NOT EXISTS idx_rwf_due_at    ON research_workflow_items(due_at);
            CREATE INDEX IF NOT EXISTS idx_rwf_priority  ON research_workflow_items(priority_score DESC);
            CREATE INDEX IF NOT EXISTS idx_rwf_source    ON research_workflow_items(source);

            CREATE TABLE IF NOT EXISTS research_workflow_notes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id     TEXT    NOT NULL,
                text        TEXT    NOT NULL,
                created_at  TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rwf_notes_item ON research_workflow_notes(item_id);
        """)
        conn.commit()
    finally:
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _make_item_id(ticker: Optional[str], source: str) -> str:
    """Deterministic item_id: hash of (ticker|source)."""
    key = f"{(ticker or 'NONE').upper()}|{source}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _row_to_item(row) -> dict:
    return dict(row)


def _row_to_note(row) -> dict:
    return dict(row)


# ── Scoring ───────────────────────────────────────────────────────────────────

def _urgency_score(due_at: Optional[str]) -> float:
    """0–40: items due in past get 40, within 24h get 30, within 3d get 20, no due gets 0."""
    if not due_at:
        return 0.0
    try:
        now = datetime.now(timezone.utc)
        due = datetime.fromisoformat(due_at)
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        delta_hours = (now - due).total_seconds() / 3600
        if delta_hours > 0:
            return float(_W_URGENCY)   # overdue
        if delta_hours > -24:
            return 30.0
        if delta_hours > -72:
            return 20.0
        return 5.0
    except Exception:
        return 0.0


def _opportunity_score(source: str, metadata: Optional[dict] = None) -> float:
    """0–30: alpha/readiness signals elevate score."""
    meta = metadata or {}
    if source == "alpha_gate":
        tier = meta.get("readiness_tier", "NOT_READY")
        tier_scores = {"RARE_ALERT": 30, "NEAR_ALERT": 25, "WATCHING": 15,
                       "MONITORING": 10, "NOT_READY": 5}
        return float(tier_scores.get(tier, 5))
    if source in ("validation_trends", "replay_missed"):
        return 20.0
    if source == "watchlist_due":
        return 10.0
    if source == "watchlist_stale":
        return 8.0
    if source == "thesis_due":
        return 15.0
    if source == "scorecard_warning":
        return 12.0
    if source == "regime_change":
        return 18.0
    return 5.0


def _risk_score(source: str, metadata: Optional[dict] = None) -> float:
    """0–20: risk/warning signals."""
    meta = metadata or {}
    if source == "regime_change":
        regime = meta.get("regime", "NEUTRAL")
        return 20.0 if regime in ("RISK_OFF", "PANIC") else 12.0
    if source == "watchlist_stale":
        priority = meta.get("priority", "MEDIUM")
        return 15.0 if priority == "HIGH" else 8.0
    if source == "thesis_due":
        return 12.0
    if source == "scorecard_warning":
        return 10.0
    return 5.0


def _stale_score(updated_at: Optional[str], source: str) -> float:
    """0–10: items not touched for a while get higher stale score."""
    if not updated_at:
        return 10.0
    try:
        now     = datetime.now(timezone.utc)
        updated = datetime.fromisoformat(updated_at)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        days = (now - updated).days
        if days >= _WATCHLIST_STALE_DAYS:
            return 10.0
        if days >= 14:
            return 7.0
        if days >= 7:
            return 4.0
        return 1.0
    except Exception:
        return 0.0


def compute_priority_score(
    urgency: float,
    opportunity: float,
    risk: float,
    stale: float,
) -> float:
    """Weighted total priority score (0–100)."""
    total = float(urgency) + float(opportunity) + float(risk) + float(stale)
    return round(min(100.0, total), 2)


# ── Queue generation ──────────────────────────────────────────────────────────

def _build_item(
    ticker: Optional[str],
    source: str,
    reason: str,
    due_at: Optional[str] = None,
    linked_entity_type: str = "NONE",
    linked_entity_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    priority: str = "MEDIUM",
) -> dict:
    """Build a queue item dict (not yet persisted)."""
    urg  = _urgency_score(due_at)
    opp  = _opportunity_score(source, metadata)
    rsk  = _risk_score(source, metadata)
    now  = _now_iso()
    st   = _stale_score(None, source)
    prio = compute_priority_score(urg, opp, rsk, st)

    return {
        "item_id":           _make_item_id(ticker, source),
        "ticker":            (ticker or "").upper() or None,
        "source":            source,
        "priority":          priority,
        "reason":            reason,
        "due_at":            due_at,
        "status":            "OPEN",
        "linked_entity_type": linked_entity_type,
        "linked_entity_id":  linked_entity_id,
        "urgency_score":     urg,
        "opportunity_score": opp,
        "risk_score":        rsk,
        "stale_score":       st,
        "priority_score":    prio,
        "snoozed_until":     None,
        "created_at":        now,
        "updated_at":        now,
    }


def _upsert_queue_item(item: dict) -> None:
    """Insert or update a queue item. On conflict (item_id), update scores/reason."""
    from database import get_connection
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT status FROM research_workflow_items WHERE item_id = ?",
            (item["item_id"],),
        ).fetchone()
        if existing:
            status = existing["status"]
            # Don't touch DONE or ARCHIVED items — let them stay resolved
            if status in ("DONE", "ARCHIVED"):
                return
            conn.execute(
                """
                UPDATE research_workflow_items
                SET ticker=?, source=?, priority=?, reason=?, due_at=?,
                    linked_entity_type=?, linked_entity_id=?,
                    urgency_score=?, opportunity_score=?, risk_score=?,
                    stale_score=?, priority_score=?, updated_at=?
                WHERE item_id=?
                """,
                (item["ticker"], item["source"], item["priority"], item["reason"],
                 item["due_at"], item["linked_entity_type"], item["linked_entity_id"],
                 item["urgency_score"], item["opportunity_score"], item["risk_score"],
                 item["stale_score"], item["priority_score"], item["updated_at"],
                 item["item_id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO research_workflow_items
                    (item_id, ticker, source, priority, reason, due_at, status,
                     linked_entity_type, linked_entity_id,
                     urgency_score, opportunity_score, risk_score, stale_score,
                     priority_score, snoozed_until, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (item["item_id"], item["ticker"], item["source"], item["priority"],
                 item["reason"], item["due_at"], item["status"],
                 item["linked_entity_type"], item["linked_entity_id"],
                 item["urgency_score"], item["opportunity_score"], item["risk_score"],
                 item["stale_score"], item["priority_score"], item["snoozed_until"],
                 item["created_at"], item["updated_at"]),
            )
        conn.commit()
    finally:
        conn.close()


def _collect_from_watchlist() -> List[dict]:
    """Watchlist due/overdue items and high-priority stale items."""
    items = []
    try:
        from research_watchlist import get_due_reviews
        result = get_due_reviews()
        for wl_item in result.get("due_soon", []):
            ticker = wl_item.get("ticker")
            items.append(_build_item(
                ticker=ticker,
                source="watchlist_due",
                reason=f"Watchlist review due: {ticker}",
                due_at=wl_item.get("next_review_at"),
                linked_entity_type="WATCHLIST",
                linked_entity_id=str(wl_item.get("id", "")),
                priority=wl_item.get("priority", "MEDIUM"),
            ))
        for wl_item in result.get("high_priority_stale", []):
            ticker = wl_item.get("ticker")
            items.append(_build_item(
                ticker=ticker,
                source="watchlist_stale",
                reason=f"High-priority watchlist item not updated in ≥30d: {ticker}",
                due_at=None,
                linked_entity_type="WATCHLIST",
                linked_entity_id=str(wl_item.get("id", "")),
                priority="HIGH",
                metadata={"priority": "HIGH"},
            ))
    except Exception as e:
        log.debug("_collect_from_watchlist: %s", e)
    return items


def _collect_from_alpha_gate() -> List[dict]:
    """Alpha candidates near alert-ready."""
    items = []
    try:
        from alpha_alert_gate import get_alert_candidates
        candidates = get_alert_candidates(limit=20)
        alert_tiers = {"NEAR_ALERT", "RARE_ALERT", "WATCHING"}
        for c in candidates:
            tier = c.get("readiness_tier", "NOT_READY")
            if tier not in alert_tiers:
                continue
            ticker = c.get("ticker")
            score  = c.get("readiness_score", 0)
            items.append(_build_item(
                ticker=ticker,
                source="alpha_gate",
                reason=f"Alpha readiness {tier} (score {score:.0f}): {ticker}",
                due_at=None,
                linked_entity_type="ALPHA_CANDIDATE",
                linked_entity_id=ticker,
                metadata={"readiness_tier": tier, "readiness_score": score},
                priority="HIGH" if tier in ("NEAR_ALERT", "RARE_ALERT") else "MEDIUM",
            ))
    except Exception as e:
        log.debug("_collect_from_alpha_gate: %s", e)
    return items


def _collect_from_validation_trends() -> List[dict]:
    """Tickers with sustained-trend validation behavior."""
    items = []
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT ticker, behavior_class, validation_score, computed_at
                FROM alpha_validation
                WHERE behavior_class = 'SUSTAINED_TREND'
                ORDER BY computed_at DESC
                LIMIT 10
                """
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            ticker = row["ticker"]
            items.append(_build_item(
                ticker=ticker,
                source="validation_trends",
                reason=f"Sustained trend validation (score {row['validation_score']:.0f}): {ticker}",
                due_at=None,
                linked_entity_type="VALIDATION",
                linked_entity_id=ticker,
                priority="MEDIUM",
            ))
    except Exception as e:
        log.debug("_collect_from_validation_trends: %s", e)
    return items


def _collect_from_replay_missed() -> List[dict]:
    """Tickers classified as missed_winner in the most recent replay run."""
    items = []
    try:
        from historical_replay import get_replay_runs, get_replay_events
        runs = get_replay_runs(limit=2)
        if not runs:
            return []
        run    = runs[0]
        run_id = run.get("run_id") or str(run.get("id", ""))
        events = get_replay_events(run_id, limit=200)
        seen: set = set()
        for ev in events:
            if ev.get("outcome_status") != "missed_winner":
                continue
            ticker = (ev.get("ticker") or "").upper()
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            r5d = ev.get("return_5d")
            items.append(_build_item(
                ticker=ticker,
                source="replay_missed",
                reason=(
                    f"Missed winner in replay (5d return {r5d:.1f}%): {ticker}"
                    if r5d is not None else f"Missed winner in replay: {ticker}"
                ),
                due_at=None,
                linked_entity_type="REPLAY",
                linked_entity_id=run_id,
                priority="MEDIUM",
                metadata={"return_5d": r5d, "run_id": run_id},
            ))
    except Exception as e:
        log.debug("_collect_from_replay_missed: %s", e)
    return items


def _collect_from_thesis_due() -> List[dict]:
    """Thesis reviews that are due or overdue."""
    items = []
    try:
        from position_journal import get_due_reviews as _get_due
        result = _get_due()
        for row in result.get("due", []) + result.get("overdue", []):
            ticker   = row.get("ticker")
            due_date = row.get("next_review_at")
            items.append(_build_item(
                ticker=ticker,
                source="thesis_due",
                reason=f"Thesis review due: {ticker}",
                due_at=due_date,
                linked_entity_type="THESIS",
                linked_entity_id=ticker,
                priority="HIGH" if row in result.get("overdue", []) else "MEDIUM",
            ))
    except Exception as e:
        log.debug("_collect_from_thesis_due: %s", e)
    return items


def _collect_from_scorecard_warnings() -> List[dict]:
    """Strategy scorecard high-priority recommendations."""
    items = []
    try:
        from strategy_scorecards import get_scorecards_summary
        summary = get_scorecards_summary()
        recs    = summary.get("priority_recommendations", [])
        for rec in recs[:5]:
            strategy = rec.get("strategy", "")
            desc     = rec.get("description", rec.get("recommendation", ""))
            items.append(_build_item(
                ticker=None,
                source="scorecard_warning",
                reason=f"Strategy {strategy}: {desc}",
                due_at=None,
                linked_entity_type="SCORECARD",
                linked_entity_id=strategy,
                priority="MEDIUM",
            ))
    except Exception as e:
        log.debug("_collect_from_scorecard_warnings: %s", e)
    return items


def _collect_from_regime_changes() -> List[dict]:
    """Recent regime snapshots indicating notable market changes."""
    items = []
    try:
        from database import get_connection
        from datetime import datetime, timezone, timedelta
        conn = get_connection()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
        try:
            rows = conn.execute(
                """
                SELECT overall_regime, regime_score, captured_at
                FROM market_regime_snapshots
                WHERE captured_at >= ?
                ORDER BY captured_at DESC
                LIMIT 5
                """,
                (cutoff,),
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            regime = row["overall_regime"]
            if regime in ("RISK_OFF", "PANIC", "RISK_ON"):
                items.append(_build_item(
                    ticker=None,
                    source="regime_change",
                    reason=f"Market regime is {regime} (score {row['regime_score']:.0f})",
                    due_at=None,
                    linked_entity_type="REGIME",
                    linked_entity_id=regime,
                    priority="HIGH" if regime in ("RISK_OFF", "PANIC") else "MEDIUM",
                    metadata={"regime": regime, "regime_score": row["regime_score"]},
                ))
                break  # one regime entry is enough
    except Exception as e:
        log.debug("_collect_from_regime_changes: %s", e)
    return items


def generate_queue(force_refresh: bool = False) -> List[dict]:
    """
    Build and persist the deterministic review queue from all sources.

    Items are upserted (DONE/ARCHIVED items are never reopened).
    Returns current OPEN+IN_PROGRESS queue sorted by priority_score DESC.
    """
    _ensure_tables()

    # Collect from all sources
    raw: List[dict] = []
    raw.extend(_collect_from_watchlist())
    raw.extend(_collect_from_alpha_gate())
    raw.extend(_collect_from_validation_trends())
    raw.extend(_collect_from_replay_missed())
    raw.extend(_collect_from_thesis_due())
    raw.extend(_collect_from_scorecard_warnings())
    raw.extend(_collect_from_regime_changes())

    # Deduplicate by item_id — keep first occurrence (highest-scoring source wins)
    seen: set = set()
    unique = []
    for item in raw:
        iid = item["item_id"]
        if iid not in seen:
            seen.add(iid)
            unique.append(item)

    # Upsert all collected items
    for item in unique:
        try:
            _upsert_queue_item(item)
        except Exception as e:
            log.warning("generate_queue: upsert failed for %s: %s", item.get("item_id"), e)

    return get_queue()


def get_queue(include_snoozed: bool = False, include_done: bool = False) -> List[dict]:
    """
    Return persisted queue items.

    Default: OPEN + IN_PROGRESS, not yet snoozed or past snooze time.
    Sorted: priority_score DESC, then due_at ASC (None last), then item_id ASC.
    """
    _ensure_tables()
    from database import get_connection
    conn = get_connection()
    now  = _now_iso()
    try:
        # Build WHERE: OPEN/IN_PROGRESS always shown; expired SNOOZED always
        # reappear; active SNOOZED only if include_snoozed=True; DONE only if
        # include_done=True.
        clauses: list[str] = ["status IN ('OPEN', 'IN_PROGRESS')"]
        params: list = []

        # Expired snoozed items always reappear in the default view.
        clauses.append("(status = 'SNOOZED' AND snoozed_until IS NOT NULL AND snoozed_until <= ?)")
        params.append(now)

        if include_snoozed:
            # Also show actively-snoozed items (future expiry).
            clauses.append("(status = 'SNOOZED' AND (snoozed_until IS NULL OR snoozed_until > ?))")
            params.append(now)

        if include_done:
            clauses.append("status = 'DONE'")

        where = " OR ".join(clauses)

        rows = conn.execute(
            f"""
            SELECT * FROM research_workflow_items
            WHERE ({where})
            ORDER BY
                priority_score DESC,
                CASE WHEN due_at IS NULL THEN 1 ELSE 0 END ASC,
                due_at ASC,
                item_id ASC
            LIMIT ?
            """,
            (*params, MAX_QUEUE_ITEMS),
        ).fetchall()
        return [_row_to_item(r) for r in rows]
    finally:
        conn.close()


def get_item(item_id: str) -> Optional[dict]:
    """Return a single workflow item by item_id, or None."""
    _ensure_tables()
    from database import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM research_workflow_items WHERE item_id = ?", (item_id,)
        ).fetchone()
        return _row_to_item(row) if row else None
    finally:
        conn.close()


# ── Status transitions ────────────────────────────────────────────────────────

def _transition(item_id: str, new_status: str, extra_fields: Optional[dict] = None) -> dict:
    """Apply a status transition. Returns updated item."""
    _ensure_tables()
    if new_status not in ITEM_STATUSES:
        raise ValueError(f"Invalid status: {new_status!r}")

    from database import get_connection
    conn = get_connection()
    now  = _now_iso()
    try:
        existing = conn.execute(
            "SELECT status FROM research_workflow_items WHERE item_id = ?", (item_id,)
        ).fetchone()
        if not existing:
            raise ValueError(f"item_id {item_id!r} not found")

        sets = ["status = ?", "updated_at = ?"]
        params: list = [new_status, now]

        if extra_fields:
            for col, val in extra_fields.items():
                sets.append(f"{col} = ?")
                params.append(val)

        params.append(item_id)
        conn.execute(
            f"UPDATE research_workflow_items SET {', '.join(sets)} WHERE item_id = ?",
            params,
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM research_workflow_items WHERE item_id = ?", (item_id,)
        ).fetchone()
        return _row_to_item(row)
    finally:
        conn.close()


def mark_in_progress(item_id: str) -> dict:
    """Mark a queue item as IN_PROGRESS."""
    return _transition(item_id, "IN_PROGRESS")


def mark_done(item_id: str) -> dict:
    """Mark a queue item as DONE."""
    return _transition(item_id, "DONE")


def snooze_item(item_id: str, hours: int = _DEFAULT_SNOOZE_HOURS) -> dict:
    """
    Snooze a queue item for `hours` hours.
    Item stays SNOOZED until snoozed_until passes, then reappears in queue.
    """
    if hours <= 0:
        hours = _DEFAULT_SNOOZE_HOURS
    snoozed_until = (
        datetime.now(timezone.utc) + timedelta(hours=hours)
    ).isoformat(timespec="seconds")
    return _transition(item_id, "SNOOZED", {"snoozed_until": snoozed_until})


def archive_item(item_id: str) -> dict:
    """Archive a queue item (no deletes)."""
    return _transition(item_id, "ARCHIVED")


def append_note(item_id: str, text: str) -> dict:
    """Append an immutable note to a workflow item. Returns saved note dict."""
    _ensure_tables()
    text = (text or "").strip()
    if not text:
        raise ValueError("note text cannot be empty")

    from database import get_connection
    conn = get_connection()
    now  = _now_iso()
    try:
        # Verify item exists
        exists = conn.execute(
            "SELECT 1 FROM research_workflow_items WHERE item_id = ?", (item_id,)
        ).fetchone()
        if not exists:
            raise ValueError(f"item_id {item_id!r} not found")

        conn.execute(
            "INSERT INTO research_workflow_notes (item_id, text, created_at) VALUES (?, ?, ?)",
            (item_id, text, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM research_workflow_notes WHERE id = last_insert_rowid()"
        ).fetchone()
        return _row_to_note(row)
    finally:
        conn.close()


def get_notes(item_id: str, limit: int = 20) -> List[dict]:
    """Return notes for an item, newest first."""
    _ensure_tables()
    from database import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM research_workflow_notes WHERE item_id = ? ORDER BY id DESC LIMIT ?",
            (item_id, limit),
        ).fetchall()
        return [_row_to_note(r) for r in rows]
    finally:
        conn.close()


# ── Daily workflow summary ────────────────────────────────────────────────────

def get_summary() -> dict:
    """
    Daily workflow summary:
    - top review items (OPEN/IN_PROGRESS, top MAX_SUMMARY_TOP by score)
    - overdue items (due_at < now, OPEN/IN_PROGRESS)
    - snoozed items returning today (snoozed_until <= now+24h)
    - high-priority new items (created today, HIGH priority)
    - completed today (DONE, updated today)
    - bottlenecks (IN_PROGRESS items older than 48h)
    """
    _ensure_tables()
    from database import get_connection
    conn = get_connection()
    now     = _now_iso()
    today   = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    soon    = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec="seconds")
    stale48 = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(timespec="seconds")

    try:
        # Top items
        top_rows = conn.execute(
            """
            SELECT * FROM research_workflow_items
            WHERE status IN ('OPEN','IN_PROGRESS')
              AND (snoozed_until IS NULL OR snoozed_until <= ?)
            ORDER BY priority_score DESC, item_id ASC
            LIMIT ?
            """,
            (now, MAX_SUMMARY_TOP),
        ).fetchall()

        # Overdue
        overdue_rows = conn.execute(
            """
            SELECT * FROM research_workflow_items
            WHERE status IN ('OPEN','IN_PROGRESS')
              AND due_at IS NOT NULL AND due_at < ?
            ORDER BY due_at ASC
            LIMIT ?
            """,
            (now, MAX_SUMMARY_OVERDUE),
        ).fetchall()

        # Snoozed returning today
        snoozed_rows = conn.execute(
            """
            SELECT * FROM research_workflow_items
            WHERE status = 'SNOOZED' AND snoozed_until <= ?
            ORDER BY snoozed_until ASC
            LIMIT ?
            """,
            (soon, MAX_SUMMARY_SNOOZED),
        ).fetchall()

        # High-priority new (created today)
        new_rows = conn.execute(
            """
            SELECT * FROM research_workflow_items
            WHERE priority = 'HIGH' AND created_at >= ? AND status = 'OPEN'
            ORDER BY priority_score DESC
            LIMIT ?
            """,
            (today, MAX_SUMMARY_NEW),
        ).fetchall()

        # Completed today
        done_rows = conn.execute(
            """
            SELECT * FROM research_workflow_items
            WHERE status = 'DONE' AND updated_at >= ?
            ORDER BY updated_at DESC
            """,
            (today,),
        ).fetchall()

        # Bottlenecks: IN_PROGRESS but stale
        bottleneck_rows = conn.execute(
            """
            SELECT * FROM research_workflow_items
            WHERE status = 'IN_PROGRESS' AND updated_at < ?
            ORDER BY updated_at ASC
            """,
            (stale48,),
        ).fetchall()

    finally:
        conn.close()

    return {
        "top_items":         [_row_to_item(r) for r in top_rows],
        "overdue":           [_row_to_item(r) for r in overdue_rows],
        "snoozed_returning": [_row_to_item(r) for r in snoozed_rows],
        "new_high_priority": [_row_to_item(r) for r in new_rows],
        "completed_today":   [_row_to_item(r) for r in done_rows],
        "bottlenecks":       [_row_to_item(r) for r in bottleneck_rows],
        "generated_at":      now,
    }


# ── Brief integration hooks ───────────────────────────────────────────────────

def get_brief_items(limit: int = MAX_BRIEF_ITEMS) -> List[dict]:
    """
    Return top workflow items for brief integration.
    Used by morning brief (operator_brief.py) and EOD brief (eod_brief.py).
    Returns list of {ticker, source, reason, priority_score, status}.
    Never raises.
    """
    try:
        _ensure_tables()
        queue = get_queue()
        return [
            {
                "ticker":         item.get("ticker"),
                "source":         item.get("source"),
                "reason":         item.get("reason"),
                "priority_score": item.get("priority_score"),
                "status":         item.get("status"),
                "item_id":        item.get("item_id"),
            }
            for item in queue[:limit]
        ]
    except Exception as e:
        log.warning("get_brief_items: %s", e)
        return []


def get_completed_today() -> List[dict]:
    """
    Return workflow items completed today — for EOD brief integration.
    Never raises.
    """
    try:
        _ensure_tables()
        from database import get_connection
        conn = get_connection()
        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat(timespec="seconds")
        try:
            rows = conn.execute(
                "SELECT * FROM research_workflow_items "
                "WHERE status='DONE' AND updated_at >= ? "
                "ORDER BY updated_at DESC",
                (today,),
            ).fetchall()
        finally:
            conn.close()
        return [_row_to_item(r) for r in rows]
    except Exception as e:
        log.warning("get_completed_today: %s", e)
        return []
