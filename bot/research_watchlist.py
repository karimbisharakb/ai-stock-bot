"""
Research Watchlist and Alert Notes — Phase A24.

Personal research memory layer: watchlist items, append-only notes,
auto-suggestions from Alpha/replay/thesis systems, and review engine.

No broker integration, no order placement, no autonomous trading.
No alerts sent.  Archive-only (no deletes).
"""
import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

ASSET_TYPES   = frozenset({"STOCK", "ETF", "CRYPTO", "INDEX", "OTHER"})
CATEGORIES    = frozenset({"CORE", "ALPHA", "SPECULATIVE", "MACRO", "HEDGE", "LEARNING"})
STATUSES      = frozenset({"WATCHING", "REVIEW_SOON", "ACTIVE_RESEARCH", "PAUSED", "ARCHIVED"})
PRIORITIES    = frozenset({"LOW", "MEDIUM", "HIGH"})
NOTE_TYPES    = frozenset({"RESEARCH", "NEWS", "CATALYST", "RISK", "VALUATION",
                            "TECHNICAL", "MACRO", "OTHER"})

# Defaults
_DEFAULT_STATUS   = "WATCHING"
_DEFAULT_PRIORITY = "MEDIUM"
_DEFAULT_CATEGORY = "LEARNING"
_DEFAULT_ASSET    = "STOCK"

# Review engine thresholds
_REVIEW_DUE_DAYS    = 0    # next_review_at <= now + 0d → due
_OVERDUE_DAYS       = -1   # next_review_at < now → overdue
_STALE_DAYS         = 30   # updated_at older than 30d → high-priority stale

# Suggestion caps
MAX_ALPHA_SUGGESTIONS    = 5
MAX_GATE_SUGGESTIONS     = 5
MAX_REPLAY_SUGGESTIONS   = 3
MAX_VALIDATION_SUGG      = 3
MAX_THESIS_WARNINGS_SUGG = 3
MAX_SCORECARD_SUGG       = 3


# ── Table bootstrap ───────────────────────────────────────────────────────────

def _ensure_tables() -> None:
    """Idempotent: create watchlist tables if they don't exist yet."""
    from database import get_connection
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS research_watchlist (
                id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker                      TEXT    NOT NULL UNIQUE,
                name                        TEXT,
                asset_type                  TEXT    NOT NULL DEFAULT 'STOCK',
                category                    TEXT    NOT NULL DEFAULT 'LEARNING',
                status                      TEXT    NOT NULL DEFAULT 'WATCHING',
                priority                    TEXT    NOT NULL DEFAULT 'MEDIUM',
                reason                      TEXT    NOT NULL DEFAULT '',
                linked_alpha_candidate_id   INTEGER,
                linked_thesis_id            INTEGER,
                next_review_at              TEXT,
                created_at                  TEXT    NOT NULL,
                updated_at                  TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_rwl_status   ON research_watchlist(status);
            CREATE INDEX IF NOT EXISTS idx_rwl_priority ON research_watchlist(priority);
            CREATE INDEX IF NOT EXISTS idx_rwl_review   ON research_watchlist(next_review_at);

            CREATE TABLE IF NOT EXISTS research_watchlist_notes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker      TEXT    NOT NULL,
                note_type   TEXT    NOT NULL DEFAULT 'OTHER',
                text        TEXT    NOT NULL,
                tags        TEXT    NOT NULL DEFAULT '[]',
                created_at  TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_rwl_notes_ticker ON research_watchlist_notes(ticker);
            CREATE INDEX IF NOT EXISTS idx_rwl_notes_type   ON research_watchlist_notes(note_type);
            """
        )
        conn.commit()
    finally:
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_enum(value: str, allowed: frozenset, field: str, default: str) -> str:
    v = (value or "").strip().upper()
    if v not in allowed:
        log.warning("research_watchlist: invalid %s=%r, using %s", field, value, default)
        return default
    return v


def _row_to_item(row) -> dict:
    d = dict(row)
    return d


def _row_to_note(row) -> dict:
    d = dict(row)
    try:
        d["tags"] = json.loads(d.get("tags") or "[]")
    except Exception:
        d["tags"] = []
    return d


# ── Watchlist CRUD ────────────────────────────────────────────────────────────

def upsert_item(
    ticker: str,
    *,
    name: Optional[str] = None,
    asset_type: str = _DEFAULT_ASSET,
    category: str = _DEFAULT_CATEGORY,
    status: str = _DEFAULT_STATUS,
    priority: str = _DEFAULT_PRIORITY,
    reason: str = "",
    linked_alpha_candidate_id: Optional[int] = None,
    linked_thesis_id: Optional[int] = None,
    next_review_at: Optional[str] = None,
) -> dict:
    """Insert or update a watchlist item. Returns the saved item dict."""
    _ensure_tables()
    ticker = ticker.upper().strip()
    if not ticker:
        raise ValueError("ticker cannot be empty")

    asset_type = _validate_enum(asset_type, ASSET_TYPES, "asset_type", _DEFAULT_ASSET)
    category   = _validate_enum(category, CATEGORIES, "category", _DEFAULT_CATEGORY)
    status     = _validate_enum(status, STATUSES, "status", _DEFAULT_STATUS)
    priority   = _validate_enum(priority, PRIORITIES, "priority", _DEFAULT_PRIORITY)
    reason     = (reason or "").strip()
    now        = _now_iso()

    from database import get_connection
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id, created_at FROM research_watchlist WHERE ticker = ?", (ticker,)
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE research_watchlist
                SET name=?, asset_type=?, category=?, status=?, priority=?, reason=?,
                    linked_alpha_candidate_id=?, linked_thesis_id=?,
                    next_review_at=?, updated_at=?
                WHERE ticker=?
                """,
                (name, asset_type, category, status, priority, reason,
                 linked_alpha_candidate_id, linked_thesis_id,
                 next_review_at, now, ticker),
            )
        else:
            conn.execute(
                """
                INSERT INTO research_watchlist
                    (ticker, name, asset_type, category, status, priority, reason,
                     linked_alpha_candidate_id, linked_thesis_id,
                     next_review_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, name, asset_type, category, status, priority, reason,
                 linked_alpha_candidate_id, linked_thesis_id,
                 next_review_at, now, now),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM research_watchlist WHERE ticker = ?", (ticker,)
        ).fetchone()
        return _row_to_item(row)
    finally:
        conn.close()


def archive_item(ticker: str) -> dict:
    """Set status=ARCHIVED for a watchlist item. Returns updated item."""
    _ensure_tables()
    ticker = ticker.upper().strip()
    now = _now_iso()
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE research_watchlist SET status='ARCHIVED', updated_at=? WHERE ticker=?",
            (now, ticker),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM research_watchlist WHERE ticker=?", (ticker,)
        ).fetchone()
        if not row:
            raise ValueError(f"ticker {ticker!r} not found in watchlist")
        return _row_to_item(row)
    finally:
        conn.close()


def get_item(ticker: str) -> Optional[dict]:
    """Return a single watchlist item by ticker, or None."""
    _ensure_tables()
    ticker = ticker.upper().strip()
    from database import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM research_watchlist WHERE ticker=?", (ticker,)
        ).fetchone()
        return _row_to_item(row) if row else None
    finally:
        conn.close()


def get_watchlist(
    include_archived: bool = False,
    include_paused: bool = False,
    status: Optional[str] = None,
    priority: Optional[str] = None,
) -> list:
    """
    Return watchlist items, sorted by:
    priority (HIGH first), then updated_at DESC, then ticker ASC.
    By default excludes ARCHIVED and PAUSED.
    """
    _ensure_tables()
    from database import get_connection
    conn = get_connection()
    try:
        excludes = []
        if not include_archived:
            excludes.append("ARCHIVED")
        if not include_paused:
            excludes.append("PAUSED")

        clauses = []
        params: list = []

        if excludes:
            placeholders = ",".join("?" * len(excludes))
            clauses.append(f"status NOT IN ({placeholders})")
            params.extend(excludes)

        if status:
            s = status.upper().strip()
            if s in STATUSES:
                clauses.append("status = ?")
                params.append(s)

        if priority:
            p = priority.upper().strip()
            if p in PRIORITIES:
                clauses.append("priority = ?")
                params.append(p)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        order = """
            ORDER BY
                CASE priority WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END ASC,
                updated_at DESC, ticker ASC
        """
        rows = conn.execute(
            f"SELECT * FROM research_watchlist {where} {order}", params
        ).fetchall()
        return [_row_to_item(r) for r in rows]
    finally:
        conn.close()


# ── Notes ─────────────────────────────────────────────────────────────────────

def append_note(
    ticker: str,
    text: str,
    note_type: str = "OTHER",
    tags: Optional[list] = None,
) -> dict:
    """Append an immutable note for a ticker. Returns saved note dict."""
    _ensure_tables()
    ticker = ticker.upper().strip()
    if not ticker:
        raise ValueError("ticker cannot be empty")
    text = (text or "").strip()
    if not text:
        raise ValueError("note text cannot be empty")

    note_type = _validate_enum(note_type, NOTE_TYPES, "note_type", "OTHER")
    tags_str  = json.dumps(sorted(set(tags or [])))
    now       = _now_iso()

    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO research_watchlist_notes (ticker, note_type, text, tags, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ticker, note_type, text, tags_str, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM research_watchlist_notes WHERE id = last_insert_rowid()"
        ).fetchone()
        return _row_to_note(row)
    finally:
        conn.close()


def get_notes(ticker: str, limit: int = 50) -> list:
    """Return notes for a ticker, newest first, up to limit."""
    _ensure_tables()
    ticker = ticker.upper().strip()
    from database import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM research_watchlist_notes WHERE ticker=? ORDER BY id DESC LIMIT ?",
            (ticker, limit),
        ).fetchall()
        return [_row_to_note(r) for r in rows]
    finally:
        conn.close()


# ── Review engine ─────────────────────────────────────────────────────────────

def get_due_reviews(include_overdue: bool = True) -> dict:
    """
    Return watchlist items that need review.

    - due_soon:   next_review_at is today or in the past (but not yet archived/paused)
    - overdue:    next_review_at < now (subset of due_soon when include_overdue=True)
    - high_stale: HIGH priority items not updated in _STALE_DAYS days

    Excludes ARCHIVED and PAUSED.
    """
    _ensure_tables()
    from database import get_connection
    conn = get_connection()
    now_iso  = _now_iso()
    stale_cutoff = (
        datetime.now(timezone.utc) - timedelta(days=_STALE_DAYS)
    ).isoformat(timespec="seconds")

    try:
        # Items with a review date that has arrived
        due_rows = conn.execute(
            """
            SELECT * FROM research_watchlist
            WHERE status NOT IN ('ARCHIVED','PAUSED')
              AND next_review_at IS NOT NULL
              AND next_review_at <= ?
            ORDER BY next_review_at ASC, ticker ASC
            """,
            (now_iso,),
        ).fetchall()

        # High-priority items that haven't been updated in a while
        stale_rows = conn.execute(
            """
            SELECT * FROM research_watchlist
            WHERE status NOT IN ('ARCHIVED','PAUSED')
              AND priority = 'HIGH'
              AND updated_at < ?
            ORDER BY updated_at ASC, ticker ASC
            """,
            (stale_cutoff,),
        ).fetchall()

        due   = [_row_to_item(r) for r in due_rows]
        stale = [_row_to_item(r) for r in stale_rows]

    finally:
        conn.close()

    return {
        "due_soon":        due,
        "high_priority_stale": stale,
        "due_count":       len(due),
        "stale_count":     len(stale),
        "checked_at":      now_iso,
    }


# ── Auto-suggestions ──────────────────────────────────────────────────────────

def _existing_tickers() -> set:
    """Return set of tickers already on the watchlist (any status)."""
    _ensure_tables()
    from database import get_connection
    conn = get_connection()
    try:
        rows = conn.execute("SELECT ticker FROM research_watchlist").fetchall()
        return {r["ticker"] for r in rows}
    finally:
        conn.close()


def _sugg(ticker: str, reason: str, source: str, category: str = "ALPHA",
          priority: str = "MEDIUM", metadata: Optional[dict] = None) -> dict:
    return {
        "ticker":   ticker,
        "reason":   reason,
        "source":   source,
        "category": category,
        "priority": priority,
        "metadata": metadata or {},
    }


def _suggestions_from_alpha_candidates(existing: set) -> list:
    """Top Alpha shadow candidates not already on watchlist."""
    try:
        from alpha_shadow import AlphaShadowManager
        mgr  = AlphaShadowManager()
        rows = mgr.get_top_candidates(limit=MAX_ALPHA_SUGGESTIONS + len(existing))
        sugg = []
        for row in rows:
            t = (row.get("ticker") or "").upper()
            if not t or t in existing:
                continue
            score = row.get("alpha_score") or 0
            tier  = row.get("alpha_tier") or "UNKNOWN"
            sugg.append(_sugg(
                t,
                f"Alpha score {score:.0f}, tier {tier}",
                "alpha_candidates",
                category="ALPHA",
                priority="HIGH" if score >= 75 else "MEDIUM",
                metadata={"alpha_score": score, "alpha_tier": tier},
            ))
            if len(sugg) >= MAX_ALPHA_SUGGESTIONS:
                break
        return sugg
    except Exception as e:
        log.debug("suggestions_from_alpha_candidates: %s", e)
        return []


def _suggestions_from_alert_gate(existing: set) -> list:
    """Alert-readiness candidates not already on watchlist."""
    try:
        from alpha_alert_gate import get_alert_candidates
        candidates = get_alert_candidates(limit=MAX_GATE_SUGGESTIONS + len(existing))
        sugg = []
        for c in candidates:
            t = (c.get("ticker") or "").upper()
            if not t or t in existing:
                continue
            tier = c.get("readiness_tier") or "UNKNOWN"
            score = c.get("readiness_score") or 0
            sugg.append(_sugg(
                t,
                f"Alert readiness tier {tier}, score {score:.0f}",
                "alert_gate",
                category="ALPHA",
                priority="HIGH" if tier in ("NEAR_ALERT", "RARE_ALERT") else "MEDIUM",
                metadata={"readiness_tier": tier, "readiness_score": score},
            ))
            if len(sugg) >= MAX_GATE_SUGGESTIONS:
                break
        return sugg
    except Exception as e:
        log.debug("suggestions_from_alert_gate: %s", e)
        return []


def _suggestions_from_missed_winners(existing: set) -> list:
    """Tickers classified as missed_winner in the most recent replay run."""
    try:
        from historical_replay import get_replay_runs, get_replay_events
        runs = get_replay_runs(limit=3)
        if not runs:
            return []
        # Use most recent run
        run = runs[0]
        run_id = run.get("run_id") or run.get("id")
        events = get_replay_events(str(run_id), limit=500)
        seen: set = set()
        sugg = []
        for ev in events:
            if ev.get("outcome_status") != "missed_winner":
                continue
            t = (ev.get("ticker") or "").upper()
            if not t or t in existing or t in seen:
                continue
            seen.add(t)
            r5d = ev.get("return_5d")
            sugg.append(_sugg(
                t,
                f"Missed winner in replay (5d return {r5d:.1f}%)" if r5d is not None else "Missed winner in replay",
                "replay_missed_winners",
                category="ALPHA",
                priority="MEDIUM",
                metadata={"return_5d": r5d, "run_id": str(run_id)},
            ))
            if len(sugg) >= MAX_REPLAY_SUGGESTIONS:
                break
        return sugg
    except Exception as e:
        log.debug("suggestions_from_missed_winners: %s", e)
        return []


def _suggestions_from_validation_trends(existing: set) -> list:
    """Tickers with sustained-trend validation behavior not on watchlist."""
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT ticker, behavior_class, validation_score
                FROM alpha_validation
                WHERE behavior_class = 'SUSTAINED_TREND'
                ORDER BY computed_at DESC
                LIMIT 50
                """,
            ).fetchall()
        finally:
            conn.close()
        seen: set = set()
        sugg = []
        for row in rows:
            t = (row["ticker"] or "").upper()
            if not t or t in existing or t in seen:
                continue
            seen.add(t)
            sugg.append(_sugg(
                t,
                f"Sustained trend validation (score {row['validation_score']:.0f})",
                "validation_trends",
                category="ALPHA",
                priority="MEDIUM",
                metadata={"validation_score": row["validation_score"]},
            ))
            if len(sugg) >= MAX_VALIDATION_SUGG:
                break
        return sugg
    except Exception as e:
        log.debug("suggestions_from_validation_trends: %s", e)
        return []


def _suggestions_from_thesis_warnings(existing: set) -> list:
    """Active positions with thesis warnings not already on watchlist."""
    try:
        from position_journal import get_thesis_warnings
        warnings = get_thesis_warnings()
        tickers = (
            warnings.get("missing_thesis", [])
            + warnings.get("stale_thesis", [])
            + warnings.get("missing_exit_plan", [])
        )
        sugg = []
        seen: set = set()
        for t in tickers:
            t = t.upper()
            if t in existing or t in seen:
                continue
            seen.add(t)
            reasons = []
            if t in warnings.get("missing_thesis", []):
                reasons.append("no thesis")
            if t in warnings.get("stale_thesis", []):
                reasons.append("stale thesis")
            if t in warnings.get("missing_exit_plan", []):
                reasons.append("no exit plan")
            sugg.append(_sugg(
                t,
                "Thesis warning: " + ", ".join(reasons),
                "thesis_warnings",
                category="CORE",
                priority="HIGH",
                metadata={"warnings": reasons},
            ))
            if len(sugg) >= MAX_THESIS_WARNINGS_SUGG:
                break
        return sugg
    except Exception as e:
        log.debug("suggestions_from_thesis_warnings: %s", e)
        return []


def _suggestions_from_scorecard_gaps(existing: set) -> list:
    """Strategies with high-priority scorecard recommendations."""
    try:
        from strategy_scorecards import get_scorecards_summary
        summary = get_scorecards_summary()
        recs    = summary.get("priority_recommendations", [])
        # Each rec has strategy + recommendation; no ticker, but useful as context
        # We can only suggest if there are underperforming strategies, not specific tickers
        # Return metadata only — no ticker; filter those out downstream
        # Instead surface bottom strategies as research areas
        bottom = summary.get("bottom_strategies", [])
        sugg = []
        for s in bottom[:MAX_SCORECARD_SUGG]:
            strategy = s.get("strategy", "")
            score    = s.get("risk_adjusted_score")
            if not strategy:
                continue
            sugg.append({
                "ticker":   None,  # strategy-level suggestion, no specific ticker
                "reason":   f"Strategy '{strategy}' underperforming (score {score})",
                "source":   "scorecard_gaps",
                "category": "LEARNING",
                "priority": "LOW",
                "metadata": {"strategy": strategy, "risk_adjusted_score": score},
            })
        return sugg
    except Exception as e:
        log.debug("suggestions_from_scorecard_gaps: %s", e)
        return []


def generate_suggestions() -> dict:
    """
    Generate deterministic watchlist suggestions from all available sources.

    Returns dict with per-source lists and a combined deduplicated list.
    Suggestions are ordered deterministically: source priority, then ticker ASC.
    """
    existing = _existing_tickers()

    alpha     = _suggestions_from_alpha_candidates(existing)
    gate      = _suggestions_from_alert_gate(existing)
    missed    = _suggestions_from_missed_winners(existing)
    trends    = _suggestions_from_validation_trends(existing)
    thesis    = _suggestions_from_thesis_warnings(existing)
    scorecard = _suggestions_from_scorecard_gaps(existing)

    # Deduplicate by ticker across sources (ticker=None scorecard entries kept as-is)
    seen_tickers: set = set()
    combined = []
    for group in [alpha, gate, missed, trends, thesis, scorecard]:
        for s in group:
            t = s.get("ticker")
            if t is None:
                combined.append(s)
                continue
            if t not in seen_tickers:
                seen_tickers.add(t)
                combined.append(s)

    # Sort: HIGH first, then MEDIUM, then LOW; within priority alphabetically
    _prank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    combined.sort(key=lambda s: (_prank.get(s.get("priority", "LOW"), 2),
                                  s.get("ticker") or ""))

    return {
        "alpha_candidates":   alpha,
        "alert_gate":         gate,
        "missed_winners":     missed,
        "validation_trends":  trends,
        "thesis_warnings":    thesis,
        "scorecard_gaps":     scorecard,
        "combined":           combined,
        "total":              len(combined),
        "generated_at":       _now_iso(),
    }


# ── Full item view (item + notes) ──────────────────────────────────────────────

def get_item_with_notes(ticker: str, notes_limit: int = 20) -> Optional[dict]:
    """Return a watchlist item with its recent notes, or None if not found."""
    item = get_item(ticker)
    if item is None:
        return None
    item["notes"] = get_notes(ticker, limit=notes_limit)
    return item
