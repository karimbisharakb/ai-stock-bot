"""
bot/catalyst_calendar.py — Phase A27: Catalyst Calendar and Event Tracker

Tracks upcoming events, earnings, macro dates, thesis catalysts, Alpha
catalysts, and watchlist/thesis review events in a single calendar.

Sources: alpha alert gate, position theses, research watchlist, research
workflow, macro event placeholders, and manual entries.

Safety: no trade instructions, no broker calls, no order placement.
All output is advisory and informational only.
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, List, Optional

import pytz

log = logging.getLogger(__name__)
EASTERN = pytz.timezone("America/Toronto")

# ── Constants ──────────────────────────────────────────────────────────────────

CATALYST_TYPES = frozenset({
    "EARNINGS", "FDA_REGULATORY", "MACRO", "PRODUCT", "CONTRACT",
    "INVESTOR_DAY", "THESIS_REVIEW", "WATCHLIST_REVIEW",
    "ALPHA_CONFIRMATION", "PORTFOLIO_RISK", "OTHER",
})

CONFIDENCE_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})
IMPORTANCE_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})
SOURCES           = frozenset({"alpha", "thesis", "watchlist", "macro", "manual", "research"})
STATUSES          = frozenset({"UPCOMING", "COMPLETED", "MISSED", "ARCHIVED"})

MAX_UPCOMING_LIST = 50
MAX_BRIEF_ITEMS   = 3
MAX_SUMMARY_LIST  = 10

# Macro event schedule (date, title, description, importance)
_MACRO_EVENTS: List[tuple] = [
    # FOMC 2026
    ("2026-01-29", "FOMC Decision",  "Federal Reserve interest rate decision", "HIGH"),
    ("2026-03-19", "FOMC Decision",  "Federal Reserve interest rate decision", "HIGH"),
    ("2026-05-07", "FOMC Decision",  "Federal Reserve interest rate decision", "HIGH"),
    ("2026-06-18", "FOMC Decision",  "Federal Reserve interest rate decision", "HIGH"),
    ("2026-07-30", "FOMC Decision",  "Federal Reserve interest rate decision", "HIGH"),
    ("2026-09-17", "FOMC Decision",  "Federal Reserve interest rate decision", "HIGH"),
    ("2026-10-29", "FOMC Decision",  "Federal Reserve interest rate decision", "HIGH"),
    ("2026-12-17", "FOMC Decision",  "Federal Reserve interest rate decision", "HIGH"),
    # CPI releases 2026 (approximate: 2nd or 3rd Wednesday)
    ("2026-01-14", "US CPI Release", "Consumer Price Index report",            "MEDIUM"),
    ("2026-02-11", "US CPI Release", "Consumer Price Index report",            "MEDIUM"),
    ("2026-03-11", "US CPI Release", "Consumer Price Index report",            "MEDIUM"),
    ("2026-04-08", "US CPI Release", "Consumer Price Index report",            "MEDIUM"),
    ("2026-05-13", "US CPI Release", "Consumer Price Index report",            "MEDIUM"),
    ("2026-06-10", "US CPI Release", "Consumer Price Index report",            "MEDIUM"),
    ("2026-07-15", "US CPI Release", "Consumer Price Index report",            "MEDIUM"),
    ("2026-08-12", "US CPI Release", "Consumer Price Index report",            "MEDIUM"),
    ("2026-09-09", "US CPI Release", "Consumer Price Index report",            "MEDIUM"),
    ("2026-10-14", "US CPI Release", "Consumer Price Index report",            "MEDIUM"),
    ("2026-11-12", "US CPI Release", "Consumer Price Index report",            "MEDIUM"),
    ("2026-12-10", "US CPI Release", "Consumer Price Index report",            "MEDIUM"),
    # Non-Farm Payroll 2026 (approximate: 1st Friday)
    ("2026-01-09", "US Jobs Report", "Non-Farm Payroll report",                "HIGH"),
    ("2026-02-06", "US Jobs Report", "Non-Farm Payroll report",                "HIGH"),
    ("2026-03-06", "US Jobs Report", "Non-Farm Payroll report",                "HIGH"),
    ("2026-04-03", "US Jobs Report", "Non-Farm Payroll report",                "HIGH"),
    ("2026-05-01", "US Jobs Report", "Non-Farm Payroll report",                "HIGH"),
    ("2026-06-05", "US Jobs Report", "Non-Farm Payroll report",                "HIGH"),
    ("2026-07-02", "US Jobs Report", "Non-Farm Payroll report",                "HIGH"),
    ("2026-08-07", "US Jobs Report", "Non-Farm Payroll report",                "HIGH"),
    ("2026-09-04", "US Jobs Report", "Non-Farm Payroll report",                "HIGH"),
    ("2026-10-02", "US Jobs Report", "Non-Farm Payroll report",                "HIGH"),
    ("2026-11-06", "US Jobs Report", "Non-Farm Payroll report",                "HIGH"),
    ("2026-12-04", "US Jobs Report", "Non-Farm Payroll report",                "HIGH"),
]


# ── Table bootstrap ────────────────────────────────────────────────────────────

def _ensure_table() -> None:
    """Create catalyst_calendar table if it doesn't exist (idempotent)."""
    from database import get_connection
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS catalyst_calendar (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                catalyst_id         TEXT    NOT NULL UNIQUE,
                ticker              TEXT,
                title               TEXT    NOT NULL,
                description         TEXT    NOT NULL DEFAULT '',
                catalyst_type       TEXT    NOT NULL DEFAULT 'OTHER',
                date                TEXT    NOT NULL,
                confidence          TEXT    NOT NULL DEFAULT 'MEDIUM',
                importance          TEXT    NOT NULL DEFAULT 'MEDIUM',
                source              TEXT    NOT NULL DEFAULT 'manual',
                status              TEXT    NOT NULL DEFAULT 'UPCOMING',
                linked_entity_type  TEXT,
                linked_entity_id    TEXT,
                created_at          TEXT    NOT NULL,
                updated_at          TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cat_status     ON catalyst_calendar(status);
            CREATE INDEX IF NOT EXISTS idx_cat_date       ON catalyst_calendar(date);
            CREATE INDEX IF NOT EXISTS idx_cat_ticker     ON catalyst_calendar(ticker);
            CREATE INDEX IF NOT EXISTS idx_cat_type       ON catalyst_calendar(catalyst_type);
            CREATE INDEX IF NOT EXISTS idx_cat_importance ON catalyst_calendar(importance);
        """)
        conn.commit()
    finally:
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(EASTERN).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(EASTERN).strftime("%Y-%m-%d")


def _date_n_days(n: int) -> str:
    return (datetime.now(EASTERN) + timedelta(days=n)).strftime("%Y-%m-%d")


def _make_catalyst_id(
    ticker: Optional[str],
    catalyst_type: str,
    source: str,
    extra: str = "",
) -> str:
    """
    Deterministic catalyst_id: SHA256 of (ticker|type|source[|extra])[:16].

    For review-type catalysts (one per ticker): no extra.
    For events tied to a specific date/title (macro, manual, earnings): pass extra.
    """
    t   = (ticker or "NONE").upper()
    key = f"{t}|{catalyst_type}|{source}"
    if extra:
        key += f"|{extra}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _row_to_catalyst(row) -> dict:
    return dict(row)


def _validate_enum(value: str, valid: frozenset, default: str, name: str) -> str:
    if value in valid:
        return value
    log.warning("catalyst_calendar: invalid %s %r — using %r", name, value, default)
    return default


def _safe_truncate(text: str, n: int) -> str:
    return text[:n] + ("…" if len(text) > n else "") if text else ""


# ── Core CRUD ─────────────────────────────────────────────────────────────────

def _build_catalyst(
    ticker:             Optional[str],
    title:              str,
    catalyst_type:      str,
    date:               str,
    description:        str = "",
    confidence:         str = "MEDIUM",
    importance:         str = "MEDIUM",
    source:             str = "manual",
    linked_entity_type: Optional[str] = None,
    linked_entity_id:   Optional[str] = None,
    extra:              str = "",
) -> dict:
    now = _now_iso()
    return {
        "catalyst_id":       _make_catalyst_id(ticker, catalyst_type, source, extra),
        "ticker":            (ticker or "").upper() or None,
        "title":             (title or "").strip(),
        "description":       (description or "").strip(),
        "catalyst_type":     _validate_enum(catalyst_type, CATALYST_TYPES, "OTHER", "catalyst_type"),
        "date":              (date or "")[:10],
        "confidence":        _validate_enum(confidence, CONFIDENCE_LEVELS, "MEDIUM", "confidence"),
        "importance":        _validate_enum(importance, IMPORTANCE_LEVELS, "MEDIUM", "importance"),
        "source":            _validate_enum(source, SOURCES, "manual", "source"),
        "status":            "UPCOMING",
        "linked_entity_type": linked_entity_type,
        "linked_entity_id":  linked_entity_id,
        "created_at":        now,
        "updated_at":        now,
    }


def _upsert_auto_catalyst(catalyst: dict) -> None:
    """
    Insert or update an auto-generated catalyst.
    Never reopens COMPLETED or ARCHIVED items.
    """
    from database import get_connection
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT status FROM catalyst_calendar WHERE catalyst_id = ?",
            (catalyst["catalyst_id"],),
        ).fetchone()
        if existing:
            if existing["status"] in ("COMPLETED", "ARCHIVED"):
                return
            conn.execute(
                """
                UPDATE catalyst_calendar
                SET ticker=?, title=?, description=?, catalyst_type=?,
                    date=?, confidence=?, importance=?, source=?,
                    linked_entity_type=?, linked_entity_id=?, updated_at=?
                WHERE catalyst_id=?
                """,
                (
                    catalyst["ticker"], catalyst["title"], catalyst["description"],
                    catalyst["catalyst_type"], catalyst["date"], catalyst["confidence"],
                    catalyst["importance"], catalyst["source"],
                    catalyst["linked_entity_type"], catalyst["linked_entity_id"],
                    catalyst["updated_at"], catalyst["catalyst_id"],
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO catalyst_calendar
                    (catalyst_id, ticker, title, description, catalyst_type,
                     date, confidence, importance, source, status,
                     linked_entity_type, linked_entity_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    catalyst["catalyst_id"], catalyst["ticker"], catalyst["title"],
                    catalyst["description"], catalyst["catalyst_type"],
                    catalyst["date"], catalyst["confidence"], catalyst["importance"],
                    catalyst["source"], "UPCOMING",
                    catalyst["linked_entity_type"], catalyst["linked_entity_id"],
                    catalyst["created_at"], catalyst["updated_at"],
                ),
            )
        conn.commit()
    finally:
        conn.close()


def upsert_catalyst(
    ticker:             Optional[str] = None,
    title:              str = "",
    catalyst_type:      str = "OTHER",
    date:               str = "",
    description:        str = "",
    confidence:         str = "MEDIUM",
    importance:         str = "MEDIUM",
    source:             str = "manual",
    linked_entity_type: Optional[str] = None,
    linked_entity_id:   Optional[str] = None,
    catalyst_id:        Optional[str] = None,
) -> dict:
    """
    Public upsert (create or update a catalyst).

    If catalyst_id is provided, update that specific record.
    Otherwise generate a deterministic ID from ticker+type+source+title+date.
    Returns the saved catalyst dict.
    """
    _ensure_table()

    # For manual catalysts include title+date in extra to allow multiple entries
    extra = f"{(title or '')[:50]}|{(date or '')[:10]}" if source == "manual" else ""
    if catalyst_id is None:
        catalyst_id = _make_catalyst_id(ticker, catalyst_type, source, extra)

    now = _now_iso()
    cat_type   = _validate_enum(catalyst_type, CATALYST_TYPES,    "OTHER",  "catalyst_type")
    conf       = _validate_enum(confidence,    CONFIDENCE_LEVELS,  "MEDIUM", "confidence")
    importance_ = _validate_enum(importance,   IMPORTANCE_LEVELS,  "MEDIUM", "importance")
    src        = _validate_enum(source,        SOURCES,            "manual", "source")
    ticker_    = (ticker or "").upper() or None

    from database import get_connection
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT * FROM catalyst_calendar WHERE catalyst_id = ?",
            (catalyst_id,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE catalyst_calendar
                SET ticker=?, title=?, description=?, catalyst_type=?,
                    date=?, confidence=?, importance=?, source=?,
                    linked_entity_type=?, linked_entity_id=?, updated_at=?
                WHERE catalyst_id=?
                """,
                (
                    ticker_, title.strip(), description.strip(), cat_type,
                    (date or "")[:10], conf, importance_, src,
                    linked_entity_type, linked_entity_id, now,
                    catalyst_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO catalyst_calendar
                    (catalyst_id, ticker, title, description, catalyst_type,
                     date, confidence, importance, source, status,
                     linked_entity_type, linked_entity_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'UPCOMING', ?, ?, ?, ?)
                """,
                (
                    catalyst_id, ticker_, title.strip(), description.strip(), cat_type,
                    (date or "")[:10], conf, importance_, src,
                    linked_entity_type, linked_entity_id, now, now,
                ),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM catalyst_calendar WHERE catalyst_id = ?", (catalyst_id,)
        ).fetchone()
        return _row_to_catalyst(row)
    finally:
        conn.close()


def get_catalyst(catalyst_id: str) -> Optional[dict]:
    """Return a single catalyst by catalyst_id, or None."""
    _ensure_table()
    from database import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM catalyst_calendar WHERE catalyst_id = ?", (catalyst_id,)
        ).fetchone()
        return _row_to_catalyst(row) if row else None
    finally:
        conn.close()


def _transition(catalyst_id: str, new_status: str) -> dict:
    """Apply a status transition. Returns updated catalyst."""
    _ensure_table()
    if new_status not in STATUSES:
        raise ValueError(f"Invalid status: {new_status!r}")
    from database import get_connection
    conn = get_connection()
    now = _now_iso()
    try:
        existing = conn.execute(
            "SELECT catalyst_id FROM catalyst_calendar WHERE catalyst_id = ?",
            (catalyst_id,),
        ).fetchone()
        if not existing:
            raise ValueError(f"catalyst_id {catalyst_id!r} not found")
        conn.execute(
            "UPDATE catalyst_calendar SET status=?, updated_at=? WHERE catalyst_id=?",
            (new_status, now, catalyst_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM catalyst_calendar WHERE catalyst_id = ?", (catalyst_id,)
        ).fetchone()
        return _row_to_catalyst(row)
    finally:
        conn.close()


def mark_completed(catalyst_id: str) -> dict:
    """Mark a catalyst as COMPLETED."""
    return _transition(catalyst_id, "COMPLETED")


def archive_catalyst(catalyst_id: str) -> dict:
    """Archive a catalyst (no deletes)."""
    return _transition(catalyst_id, "ARCHIVED")


# ── Calendar queries ───────────────────────────────────────────────────────────

def get_upcoming(
    days: int = 30,
    ticker: Optional[str] = None,
    importance: Optional[str] = None,
    include_overdue: bool = True,
) -> List[dict]:
    """
    Return UPCOMING catalysts.
    Default: today through the next `days` days, plus overdue if include_overdue=True.
    Sorted: date ASC, importance DESC (HIGH first), catalyst_id ASC.
    """
    _ensure_table()
    from database import get_connection
    conn = get_connection()
    today    = _today()
    end_date = _date_n_days(days)
    try:
        clauses = ["status = 'UPCOMING'"]
        params:  list = []

        if include_overdue:
            clauses.append("date <= ?")
            params.append(end_date)
        else:
            clauses.append("date >= ?")
            params.append(today)
            clauses.append("date <= ?")
            params.append(end_date)

        if ticker:
            clauses.append("ticker = ?")
            params.append(ticker.upper())

        if importance and importance in IMPORTANCE_LEVELS:
            clauses.append("importance = ?")
            params.append(importance)

        where = " AND ".join(clauses)
        rows = conn.execute(
            f"""
            SELECT * FROM catalyst_calendar
            WHERE {where}
            ORDER BY
                date ASC,
                CASE importance WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END ASC,
                catalyst_id ASC
            LIMIT ?
            """,
            (*params, MAX_UPCOMING_LIST),
        ).fetchall()
        return [_row_to_catalyst(r) for r in rows]
    finally:
        conn.close()


def get_overdue() -> List[dict]:
    """Return UPCOMING catalysts whose date is in the past."""
    _ensure_table()
    from database import get_connection
    conn = get_connection()
    today = _today()
    try:
        rows = conn.execute(
            """
            SELECT * FROM catalyst_calendar
            WHERE status = 'UPCOMING' AND date < ?
            ORDER BY date ASC, catalyst_id ASC
            LIMIT ?
            """,
            (today, MAX_UPCOMING_LIST),
        ).fetchall()
        return [_row_to_catalyst(r) for r in rows]
    finally:
        conn.close()


def get_by_ticker(ticker: str) -> List[dict]:
    """Return all non-archived catalysts for a ticker, newest first."""
    _ensure_table()
    from database import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM catalyst_calendar
            WHERE ticker = ? AND status != 'ARCHIVED'
            ORDER BY date ASC, catalyst_id ASC
            """,
            (ticker.upper(),),
        ).fetchall()
        return [_row_to_catalyst(r) for r in rows]
    finally:
        conn.close()


# ── Source collectors ──────────────────────────────────────────────────────────

def _collect_from_thesis() -> List[dict]:
    """THESIS_REVIEW catalysts from position_theses.next_review_at."""
    catalysts: List[dict] = []
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT ticker, next_review_at, conviction_level, thesis_title "
                "FROM position_theses WHERE status = 'ACTIVE' "
                "AND next_review_at IS NOT NULL AND next_review_at != '' "
                "ORDER BY next_review_at"
            ).fetchall()
        finally:
            conn.close()
        for r in rows:
            ticker  = r["ticker"]
            due     = r["next_review_at"][:10] if r["next_review_at"] else ""
            if not due:
                continue
            title   = f"Thesis review: {ticker}"
            conv    = r["conviction_level"] or "MEDIUM"
            imp     = "HIGH" if conv == "HIGH" else "MEDIUM"
            cat = _build_catalyst(
                ticker=ticker, title=title,
                catalyst_type="THESIS_REVIEW", date=due,
                description=f"Scheduled thesis review for {ticker}",
                confidence="HIGH", importance=imp, source="thesis",
                linked_entity_type="THESIS", linked_entity_id=ticker,
            )
            catalysts.append(cat)
    except Exception as exc:
        log.debug("_collect_from_thesis: %s", exc)
    return catalysts


def _collect_from_watchlist() -> List[dict]:
    """WATCHLIST_REVIEW catalysts from research_watchlist.next_review_at."""
    catalysts: List[dict] = []
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT ticker, next_review_at, priority "
                "FROM research_watchlist "
                "WHERE status NOT IN ('ARCHIVED','PAUSED') "
                "AND next_review_at IS NOT NULL AND next_review_at != '' "
                "ORDER BY next_review_at"
            ).fetchall()
        finally:
            conn.close()
        for r in rows:
            ticker = r["ticker"]
            due    = r["next_review_at"][:10] if r["next_review_at"] else ""
            if not due:
                continue
            priority = r["priority"] or "MEDIUM"
            imp      = "HIGH" if priority == "HIGH" else "MEDIUM"
            cat = _build_catalyst(
                ticker=ticker, title=f"Watchlist review: {ticker}",
                catalyst_type="WATCHLIST_REVIEW", date=due,
                description=f"Scheduled watchlist review for {ticker}",
                confidence="HIGH", importance=imp, source="watchlist",
                linked_entity_type="WATCHLIST", linked_entity_id=ticker,
            )
            catalysts.append(cat)
    except Exception as exc:
        log.debug("_collect_from_watchlist: %s", exc)
    return catalysts


def _collect_from_workflow() -> List[dict]:
    """PORTFOLIO_RISK catalysts from research_workflow_items with due_at."""
    catalysts: List[dict] = []
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT item_id, ticker, reason, due_at, priority "
                "FROM research_workflow_items "
                "WHERE status IN ('OPEN','IN_PROGRESS') "
                "AND due_at IS NOT NULL AND due_at != '' "
                "ORDER BY due_at"
            ).fetchall()
        finally:
            conn.close()
        for r in rows:
            due    = r["due_at"][:10] if r["due_at"] else ""
            if not due:
                continue
            ticker  = r["ticker"]
            reason  = r["reason"] or ""
            item_id = r["item_id"]
            priority = r["priority"] or "MEDIUM"
            imp      = "HIGH" if priority == "HIGH" else "MEDIUM"
            title   = _safe_truncate(f"Research due: {reason}", 80) or f"Research item due"
            cat = _build_catalyst(
                ticker=ticker, title=title,
                catalyst_type="PORTFOLIO_RISK", date=due,
                description=f"Research workflow item due: {reason}",
                confidence="HIGH", importance=imp, source="research",
                linked_entity_type="WORKFLOW_ITEM", linked_entity_id=item_id,
                extra=item_id,
            )
            catalysts.append(cat)
    except Exception as exc:
        log.debug("_collect_from_workflow: %s", exc)
    return catalysts


def _collect_from_alpha() -> List[dict]:
    """ALPHA_CONFIRMATION catalysts for near-alert Alpha candidates."""
    catalysts: List[dict] = []
    try:
        from alpha_alert_gate import get_alert_candidates
        candidates = get_alert_candidates(limit=30)
        alert_tiers = {"RARE_ALERT", "NEAR_ALERT"}
        # Near-alert candidates: review within 7 days
        review_date = _date_n_days(7)
        for c in candidates:
            tier = c.get("readiness_tier", "NOT_READY")
            if tier not in alert_tiers:
                continue
            ticker = c.get("ticker", "")
            if not ticker:
                continue
            score = c.get("readiness_score", 0) or 0
            cat = _build_catalyst(
                ticker=ticker,
                title=f"Alpha confirmation: {ticker}",
                catalyst_type="ALPHA_CONFIRMATION",
                date=review_date,
                description=f"Alpha readiness {tier} (score {score:.0f}) — confirm or pass",
                confidence="MEDIUM",
                importance="HIGH" if tier == "RARE_ALERT" else "MEDIUM",
                source="alpha",
                linked_entity_type="ALPHA_CANDIDATE",
                linked_entity_id=ticker,
            )
            catalysts.append(cat)
    except Exception as exc:
        log.debug("_collect_from_alpha: %s", exc)
    return catalysts


def _collect_macro_placeholders() -> List[dict]:
    """MACRO catalysts from the hardcoded schedule, filtered to upcoming 90 days."""
    catalysts: List[dict] = []
    today    = _today()
    end_date = _date_n_days(90)
    for date, title, description, importance in _MACRO_EVENTS:
        if not (today <= date <= end_date):
            continue
        cat = _build_catalyst(
            ticker=None,
            title=title,
            catalyst_type="MACRO",
            date=date,
            description=description,
            confidence="HIGH",
            importance=importance,
            source="macro",
            linked_entity_type=None,
            linked_entity_id=None,
            extra=f"{title}|{date}",
        )
        catalysts.append(cat)
    return catalysts


# ── Queue generation ───────────────────────────────────────────────────────────

def generate_catalysts() -> List[dict]:
    """
    Collect catalysts from all sources, upsert into DB (respecting
    COMPLETED/ARCHIVED items), and return get_upcoming(days=30).
    """
    _ensure_table()
    raw: List[dict] = []
    raw.extend(_collect_from_thesis())
    raw.extend(_collect_from_watchlist())
    raw.extend(_collect_from_workflow())
    raw.extend(_collect_from_alpha())
    raw.extend(_collect_macro_placeholders())

    # Deduplicate by catalyst_id (first occurrence wins)
    seen: set = set()
    unique: List[dict] = []
    for cat in raw:
        cid = cat["catalyst_id"]
        if cid not in seen:
            seen.add(cid)
            unique.append(cat)

    for cat in unique:
        try:
            _upsert_auto_catalyst(cat)
        except Exception as exc:
            log.warning("generate_catalysts: upsert failed for %s: %s", cat.get("catalyst_id"), exc)

    return get_upcoming(days=30)


# ── Catalyst risk summary ──────────────────────────────────────────────────────

def get_catalyst_summary() -> dict:
    """
    Return a risk-oriented summary of upcoming catalysts:
    - catalysts this week / next week
    - high-importance count (next 30 days)
    - portfolio positions with upcoming catalysts
    - alpha candidates with upcoming catalysts
    - overdue count + list
    - missing next_review_at in active theses
    """
    _ensure_table()
    from database import get_connection
    today      = _today()
    week_end   = _date_n_days(7)
    next_week  = _date_n_days(14)
    month_end  = _date_n_days(30)

    this_week_count   = 0
    next_week_count   = 0
    high_imp_count    = 0
    portfolio_cats:   List[dict] = []
    alpha_cats:       List[dict] = []
    overdue_cats:     List[dict] = []
    missing_thesis:   List[str]  = []

    try:
        conn = get_connection()
        try:
            # This week
            this_week_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM catalyst_calendar "
                "WHERE status='UPCOMING' AND date >= ? AND date < ?",
                (today, week_end),
            ).fetchone()["cnt"]

            # Next week
            next_week_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM catalyst_calendar "
                "WHERE status='UPCOMING' AND date >= ? AND date < ?",
                (week_end, next_week),
            ).fetchone()["cnt"]

            # High importance (next 30 days)
            high_imp_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM catalyst_calendar "
                "WHERE status='UPCOMING' AND importance='HIGH' AND date <= ?",
                (month_end,),
            ).fetchone()["cnt"]

            # Portfolio catalysts: tickers in holdings with upcoming catalysts
            try:
                holding_tickers = [
                    r["ticker"] for r in conn.execute(
                        "SELECT ticker FROM holdings"
                    ).fetchall()
                ]
                if holding_tickers:
                    placeholders = ",".join("?" * len(holding_tickers))
                    port_rows = conn.execute(
                        f"SELECT catalyst_id, ticker, title, date, importance "
                        f"FROM catalyst_calendar "
                        f"WHERE status='UPCOMING' AND ticker IN ({placeholders}) "
                        f"AND date <= ? "
                        f"ORDER BY date ASC LIMIT ?",
                        (*holding_tickers, month_end, MAX_SUMMARY_LIST),
                    ).fetchall()
                    portfolio_cats = [dict(r) for r in port_rows]
            except Exception:
                pass

            # Overdue
            overdue_rows = conn.execute(
                "SELECT catalyst_id, ticker, title, date, importance "
                "FROM catalyst_calendar "
                "WHERE status='UPCOMING' AND date < ? "
                "ORDER BY date ASC LIMIT ?",
                (today, MAX_SUMMARY_LIST),
            ).fetchall()
            overdue_cats = [dict(r) for r in overdue_rows]

            # Missing next_review_at in active theses
            try:
                thesis_rows = conn.execute(
                    "SELECT ticker FROM position_theses "
                    "WHERE status='ACTIVE' AND "
                    "(next_review_at IS NULL OR next_review_at = '')"
                ).fetchall()
                missing_thesis = [r["ticker"] for r in thesis_rows]
            except Exception:
                pass

        finally:
            conn.close()
    except Exception as exc:
        log.debug("get_catalyst_summary: %s", exc)

    # Alpha catalysts: tickers from Alpha near-alert in upcoming list
    try:
        from alpha_alert_gate import get_alert_candidates
        candidates = get_alert_candidates(limit=20)
        alert_tiers = {"RARE_ALERT", "NEAR_ALERT"}
        alpha_cats = [
            {"ticker": c.get("ticker", ""), "readiness_tier": c.get("readiness_tier", "")}
            for c in candidates
            if c.get("readiness_tier") in alert_tiers
        ][:MAX_SUMMARY_LIST]
    except Exception:
        pass

    return {
        "this_week_count":    int(this_week_count),
        "next_week_count":    int(next_week_count),
        "high_importance_count": int(high_imp_count),
        "portfolio_catalysts": portfolio_cats,
        "alpha_catalysts":    alpha_cats,
        "overdue_count":      len(overdue_cats),
        "overdue_catalysts":  overdue_cats,
        "missing_thesis_dates": missing_thesis,
    }


# ── Brief integration hooks ────────────────────────────────────────────────────

def get_brief_catalysts(limit: int = MAX_BRIEF_ITEMS) -> List[dict]:
    """
    Return top upcoming catalysts for morning/EOD brief integration.
    Prioritises HIGH importance, then nearest date.
    Never raises.
    """
    try:
        _ensure_table()
        from database import get_connection
        conn = get_connection()
        today = _today()
        try:
            rows = conn.execute(
                """
                SELECT catalyst_id, ticker, title, date, catalyst_type, importance
                FROM catalyst_calendar
                WHERE status = 'UPCOMING'
                ORDER BY
                    CASE importance WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END ASC,
                    date ASC,
                    catalyst_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("get_brief_catalysts: %s", exc)
        return []


def get_completed_today() -> List[dict]:
    """Return catalysts marked COMPLETED today. For EOD brief integration. Never raises."""
    try:
        _ensure_table()
        from database import get_connection
        conn = get_connection()
        today = _today()
        try:
            rows = conn.execute(
                "SELECT catalyst_id, ticker, title, catalyst_type, importance "
                "FROM catalyst_calendar "
                "WHERE status = 'COMPLETED' AND updated_at >= ? "
                "ORDER BY updated_at DESC",
                (today,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("get_completed_today: %s", exc)
        return []


def get_weekly_catalyst_summary(week_start: str, week_end: str) -> dict:
    """
    Return a summary of catalyst activity for a given week.
    Used by the weekly review module.
    Never raises.
    """
    try:
        _ensure_table()
        from database import get_connection
        conn = get_connection()
        try:
            completed = conn.execute(
                "SELECT COUNT(*) as cnt FROM catalyst_calendar "
                "WHERE status='COMPLETED' AND updated_at >= ? AND updated_at < ?",
                (week_start, week_end),
            ).fetchone()["cnt"]
            missed = conn.execute(
                "SELECT COUNT(*) as cnt FROM catalyst_calendar "
                "WHERE status='UPCOMING' AND date >= ? AND date < ?",
                (week_start, week_start),  # always 0 but safe
            ).fetchone()["cnt"]
            # Actually: missed = UPCOMING items whose date was in the week (past due)
            overdue_in_week = conn.execute(
                "SELECT COUNT(*) as cnt FROM catalyst_calendar "
                "WHERE status='UPCOMING' AND date >= ? AND date < ?",
                (week_start, week_end),
            ).fetchone()["cnt"]
            high_imp = conn.execute(
                "SELECT COUNT(*) as cnt FROM catalyst_calendar "
                "WHERE importance='HIGH' AND date >= ? AND date < ?",
                (week_start, week_end),
            ).fetchone()["cnt"]
        finally:
            conn.close()
        return {
            "completed_this_week":    int(completed),
            "active_this_week":       int(overdue_in_week),
            "high_importance_count":  int(high_imp),
        }
    except Exception as exc:
        log.debug("get_weekly_catalyst_summary: %s", exc)
        return {
            "completed_this_week":   0,
            "active_this_week":      0,
            "high_importance_count": 0,
        }
