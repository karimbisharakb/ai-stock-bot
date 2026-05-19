"""
Phase A13 — Position journal and trade thesis tracking.

Per-ticker thesis with conviction level, time horizon, catalysts, risk factors,
invalidation/target levels, exit plan, and review schedule.  Append-only journal
for notes, reviews, and thesis updates.  Review engine for due/overdue/stale
detection.

No trading operations.  No broker integration.  No autonomous actions.
Archive only (no deletes).

Public API:
  validate_thesis(...)              -> list[str]   pure, no DB
  validate_journal_entry(...)       -> list[str]   pure, no DB
  upsert_thesis(ticker, ...)        -> dict
  get_thesis(ticker)                -> dict | None
  get_all_theses(status)            -> list[dict]
  get_thesis_summaries(tickers)     -> dict[str, dict | None]
  add_journal_entry(ticker, ...)    -> dict
  get_journal_entries(ticker, limit) -> list[dict]
  get_due_reviews()                 -> dict
  get_thesis_warnings()             -> dict
  get_review_summary()              -> dict
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

VALID_CONVICTION_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})
VALID_TIME_HORIZONS     = frozenset({"SHORT", "MEDIUM", "LONG"})
VALID_THESIS_STATUSES   = frozenset({"ACTIVE", "WATCH", "CLOSED", "ARCHIVED"})
VALID_ENTRY_TYPES       = frozenset({
    "NOTE", "REVIEW", "THESIS_UPDATE", "RISK_UPDATE",
    "CATALYST_UPDATE", "EXIT_PLAN_UPDATE",
})

_STALE_DAYS    = 90   # ACTIVE thesis not updated in this many days → stale warning
_UPCOMING_DAYS =  7   # next_review_at within this window → upcoming


# ── DB DDL ────────────────────────────────────────────────────────────────────

_THESES_DDL = """
CREATE TABLE IF NOT EXISTS position_theses (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker                TEXT    NOT NULL UNIQUE,
    thesis_title          TEXT    NOT NULL DEFAULT '',
    thesis_text           TEXT    NOT NULL DEFAULT '',
    setup_type            TEXT    NOT NULL DEFAULT '',
    conviction_level      TEXT    NOT NULL DEFAULT 'MEDIUM',
    time_horizon          TEXT    NOT NULL DEFAULT 'MEDIUM',
    entry_reason          TEXT    NOT NULL DEFAULT '',
    expected_catalysts    TEXT    NOT NULL DEFAULT '',
    risk_factors          TEXT    NOT NULL DEFAULT '',
    invalidation_level    REAL,
    target_level          REAL,
    exit_plan             TEXT    NOT NULL DEFAULT '',
    review_frequency_days INTEGER NOT NULL DEFAULT 30,
    next_review_at        TEXT    NOT NULL,
    status                TEXT    NOT NULL DEFAULT 'ACTIVE',
    created_at            TEXT    NOT NULL,
    updated_at            TEXT    NOT NULL
)
"""

_JOURNAL_DDL = """
CREATE TABLE IF NOT EXISTS position_journal (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT    NOT NULL,
    entry_type        TEXT    NOT NULL,
    text              TEXT    NOT NULL DEFAULT '',
    tags_json         TEXT    NOT NULL DEFAULT '[]',
    confidence_change TEXT,
    created_at        TEXT    NOT NULL
)
"""

_THESES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_theses_ticker     ON position_theses(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_theses_status     ON position_theses(status)",
    "CREATE INDEX IF NOT EXISTS idx_theses_next_review ON position_theses(next_review_at)",
]

_JOURNAL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_journal_ticker     ON position_journal(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_journal_entry_type ON position_journal(entry_type)",
    "CREATE INDEX IF NOT EXISTS idx_journal_created_at ON position_journal(created_at)",
]


def _ensure_tables() -> None:
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(_THESES_DDL)
        conn.execute(_JOURNAL_DDL)
        for idx in _THESES_INDEXES + _JOURNAL_INDEXES:
            conn.execute(idx)
        conn.commit()
    except Exception:
        log.warning("position_journal: table creation failed", exc_info=True)
    finally:
        conn.close()


# ── Validation (pure functions) ───────────────────────────────────────────────

def validate_thesis(
    ticker:           str,
    conviction_level: str = "MEDIUM",
    time_horizon:     str = "MEDIUM",
    status:           str = "ACTIVE",
) -> list:
    """Validate thesis inputs.  Pure function.  Returns list of error strings."""
    errors = []
    if not ticker or not str(ticker).strip():
        errors.append("MISSING_TICKER")
    if conviction_level not in VALID_CONVICTION_LEVELS:
        errors.append(f"INVALID_CONVICTION_LEVEL:{conviction_level}")
    if time_horizon not in VALID_TIME_HORIZONS:
        errors.append(f"INVALID_TIME_HORIZON:{time_horizon}")
    if status not in VALID_THESIS_STATUSES:
        errors.append(f"INVALID_STATUS:{status}")
    return errors


def validate_journal_entry(
    ticker:     str,
    entry_type: str,
    text:       str,
) -> list:
    """Validate journal entry inputs.  Pure function.  Returns list of error strings."""
    errors = []
    if not ticker or not str(ticker).strip():
        errors.append("MISSING_TICKER")
    if entry_type not in VALID_ENTRY_TYPES:
        errors.append(f"INVALID_ENTRY_TYPE:{entry_type}")
    if not text or not str(text).strip():
        errors.append("EMPTY_TEXT")
    return errors


# ── Thesis CRUD ───────────────────────────────────────────────────────────────

def upsert_thesis(
    ticker:               str,
    thesis_title:         str   = "",
    thesis_text:          str   = "",
    setup_type:           str   = "",
    conviction_level:     str   = "MEDIUM",
    time_horizon:         str   = "MEDIUM",
    entry_reason:         str   = "",
    expected_catalysts:   str   = "",
    risk_factors:         str   = "",
    invalidation_level:   Optional[float] = None,
    target_level:         Optional[float] = None,
    exit_plan:            str   = "",
    review_frequency_days: int  = 30,
    next_review_at:       Optional[str]   = None,
    status:               str   = "ACTIVE",
) -> dict:
    """
    Insert or update a position thesis.  Archive with status='ARCHIVED' (no delete).
    Validates inputs.  Logs a THESIS_UPDATE journal entry on update.
    Returns {'ok': True, 'ticker': ..., 'thesis': {...}} or {'ok': False, 'errors': [...]}.
    Never raises.
    """
    _ensure_tables()
    ticker = str(ticker).strip().upper() if ticker else ""
    errors = validate_thesis(ticker, conviction_level, time_horizon, status)
    if errors:
        return {"ok": False, "errors": errors, "ticker": ticker}

    now = datetime.now().isoformat()

    # Compute next_review_at default if not supplied
    if not next_review_at:
        next_review_at = (
            datetime.now() + timedelta(days=max(1, review_frequency_days))
        ).isoformat()

    try:
        from database import get_connection
        conn = get_connection()
        try:
            existing = conn.execute(
                "SELECT created_at FROM position_theses WHERE ticker = ?", (ticker,)
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            is_update  = existing is not None

            conn.execute(
                """INSERT OR REPLACE INTO position_theses
                   (ticker, thesis_title, thesis_text, setup_type, conviction_level,
                    time_horizon, entry_reason, expected_catalysts, risk_factors,
                    invalidation_level, target_level, exit_plan, review_frequency_days,
                    next_review_at, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ticker, thesis_title, thesis_text, setup_type, conviction_level,
                    time_horizon, entry_reason, expected_catalysts, risk_factors,
                    invalidation_level, target_level, exit_plan, review_frequency_days,
                    next_review_at, status, created_at, now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log.error("position_journal: upsert_thesis failed for %s: %s", ticker, exc, exc_info=True)
        return {"ok": False, "errors": [f"DB_ERROR:{str(exc)[:100]}"], "ticker": ticker}

    # Auto-append THESIS_UPDATE journal entry on update (not first create)
    if is_update:
        _append_journal_entry(
            ticker    = ticker,
            entry_type = "THESIS_UPDATE",
            text      = "Thesis updated.",
            tags      = [],
            confidence_change = None,
            now       = now,
        )

    thesis = get_thesis(ticker)
    return {"ok": True, "ticker": ticker, "thesis": thesis}


def get_thesis(ticker: str) -> Optional[dict]:
    """Return the thesis row for a ticker, or None if not found.  Never raises."""
    _ensure_tables()
    try:
        ticker = str(ticker).strip().upper() if ticker else ""
        from database import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM position_theses WHERE ticker = ?", (ticker,)
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None
    except Exception:
        log.warning("position_journal: get_thesis failed for %s", ticker, exc_info=True)
        return None


def get_all_theses(status: Optional[str] = None) -> list:
    """
    Return all theses ordered by ticker ASC.
    Optional status filter (ACTIVE/WATCH/CLOSED/ARCHIVED).
    Never raises.
    """
    _ensure_tables()
    try:
        from database import get_connection
        conn = get_connection()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM position_theses WHERE status = ? ORDER BY ticker",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM position_theses ORDER BY ticker"
                ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("position_journal: get_all_theses failed", exc_info=True)
        return []


def get_thesis_summaries(tickers: list) -> dict:
    """
    Return {ticker: summary_dict | None} for a list of tickers.
    summary_dict keys: conviction_level, time_horizon, status, next_review_at,
                       has_exit_plan, is_stale, thesis_title.
    Never raises.
    """
    if not tickers:
        return {}
    _ensure_tables()
    try:
        from database import get_connection
        conn = get_connection()
        try:
            placeholders = ",".join("?" * len(tickers))
            rows = conn.execute(
                f"SELECT ticker, thesis_title, conviction_level, time_horizon, status, "
                f"exit_plan, next_review_at, updated_at "
                f"FROM position_theses WHERE ticker IN ({placeholders})",
                tickers,
            ).fetchall()
        finally:
            conn.close()

        stale_cutoff = (datetime.now() - timedelta(days=_STALE_DAYS)).isoformat()
        result = {t: None for t in tickers}
        for r in rows:
            result[r["ticker"]] = {
                "thesis_title":    r["thesis_title"],
                "conviction_level": r["conviction_level"],
                "time_horizon":    r["time_horizon"],
                "status":          r["status"],
                "next_review_at":  r["next_review_at"],
                "has_exit_plan":   bool(r["exit_plan"]),
                "is_stale":        r["updated_at"] < stale_cutoff,
            }
        return result
    except Exception:
        log.warning("position_journal: get_thesis_summaries failed", exc_info=True)
        return {t: None for t in tickers}


# ── Journal entries (append-only) ────────────────────────────────────────────

def add_journal_entry(
    ticker:            str,
    entry_type:        str,
    text:              str,
    tags:              Optional[list] = None,
    confidence_change: Optional[str]  = None,
) -> dict:
    """
    Append a journal entry for a ticker.  Validates inputs.
    If entry_type='REVIEW', advances next_review_at on the thesis by review_frequency_days.
    Returns {'ok': True, 'entry': {...}} or {'ok': False, 'errors': [...]}.
    Never raises.
    """
    _ensure_tables()
    ticker = str(ticker).strip().upper() if ticker else ""
    errors = validate_journal_entry(ticker, entry_type, text)
    if errors:
        return {"ok": False, "errors": errors, "ticker": ticker}

    now    = datetime.now().isoformat()
    result = _append_journal_entry(
        ticker            = ticker,
        entry_type        = entry_type,
        text              = text,
        tags              = tags or [],
        confidence_change = confidence_change,
        now               = now,
    )
    if not result:
        return {"ok": False, "errors": ["DB_WRITE_FAILED"], "ticker": ticker}

    # Advance next_review_at when a REVIEW entry is added
    if entry_type == "REVIEW":
        thesis = get_thesis(ticker)
        if thesis:
            freq           = int(thesis.get("review_frequency_days") or 30)
            new_next       = (datetime.now() + timedelta(days=max(1, freq))).isoformat()
            _advance_next_review(ticker, new_next)

    return {"ok": True, "ticker": ticker, "entry": result}


def _append_journal_entry(
    ticker:            str,
    entry_type:        str,
    text:              str,
    tags:              list,
    confidence_change: Optional[str],
    now:               str,
) -> Optional[dict]:
    """Internal: write one row to position_journal.  Never raises."""
    try:
        from database import get_connection
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO position_journal
                   (ticker, entry_type, text, tags_json, confidence_change, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (ticker, entry_type, text, json.dumps(tags or []), confidence_change, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM position_journal WHERE rowid = last_insert_rowid()"
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None
    except Exception:
        log.warning("position_journal: journal write failed for %s", ticker, exc_info=True)
        return None


def _advance_next_review(ticker: str, next_review_at: str) -> None:
    """Update next_review_at on a thesis.  Never raises."""
    try:
        from database import get_connection
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE position_theses SET next_review_at=?, updated_at=? WHERE ticker=?",
                (next_review_at, datetime.now().isoformat(), ticker),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.warning("position_journal: advance_next_review failed for %s", ticker, exc_info=True)


def get_journal_entries(ticker: str, limit: int = 50) -> list:
    """
    Return journal entries for a ticker ordered by created_at DESC.
    Never raises.
    """
    _ensure_tables()
    try:
        ticker = str(ticker).strip().upper() if ticker else ""
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM position_journal "
                "WHERE ticker = ? ORDER BY created_at DESC LIMIT ?",
                (ticker, limit),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("position_journal: get_journal_entries failed for %s", ticker, exc_info=True)
        return []


# ── Review engine ─────────────────────────────────────────────────────────────

def get_due_reviews() -> dict:
    """
    Identify ACTIVE/WATCH theses requiring attention based on next_review_at.

    Returns:
      due          — theses where next_review_at <= now (sorted ticker ASC)
      overdue      — subset of due where next_review_at <= now - UPCOMING_DAYS
      upcoming     — next_review_at within next UPCOMING_DAYS
      due_count    — len(due)
      overdue_count — len(overdue)
      upcoming_count — len(upcoming)

    Never raises.
    """
    _ensure_tables()
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT ticker, thesis_title, conviction_level, next_review_at, "
                "review_frequency_days, status "
                "FROM position_theses "
                "WHERE status IN ('ACTIVE','WATCH') "
                "ORDER BY next_review_at ASC, ticker ASC"
            ).fetchall()
        finally:
            conn.close()

        now         = datetime.now()
        now_iso     = now.isoformat()
        overdue_cut = (now - timedelta(days=_UPCOMING_DAYS)).isoformat()
        upcoming_cut = (now + timedelta(days=_UPCOMING_DAYS)).isoformat()

        due      = []
        overdue  = []
        upcoming = []

        for r in rows:
            row = dict(r)
            nr  = row["next_review_at"] or ""
            if nr <= now_iso:
                due.append(row)
                if nr <= overdue_cut:
                    overdue.append(row)
            elif nr <= upcoming_cut:
                upcoming.append(row)

        return {
            "due":           due,
            "overdue":       overdue,
            "upcoming":      upcoming,
            "due_count":     len(due),
            "overdue_count": len(overdue),
            "upcoming_count": len(upcoming),
        }
    except Exception:
        log.warning("position_journal: get_due_reviews failed", exc_info=True)
        return {"due": [], "overdue": [], "upcoming": [], "due_count": 0,
                "overdue_count": 0, "upcoming_count": 0}


def get_thesis_warnings() -> dict:
    """
    Identify ACTIVE positions with thesis quality issues.

    Checks:
      missing_thesis    — tickers in manual_portfolio_positions (active) with no thesis
      stale_thesis      — ACTIVE theses updated more than STALE_DAYS days ago
      missing_exit_plan — ACTIVE theses with empty exit_plan

    Returns dict with those three lists + has_warnings flag.  Never raises.
    """
    _ensure_tables()
    try:
        from database import get_connection
        conn = get_connection()
        try:
            # Active manual positions
            pos_rows = conn.execute(
                "SELECT ticker FROM manual_portfolio_positions WHERE active=1"
            ).fetchall()
            # Active theses
            thesis_rows = conn.execute(
                "SELECT ticker, exit_plan, updated_at "
                "FROM position_theses WHERE status='ACTIVE'"
            ).fetchall()
        finally:
            conn.close()

        position_tickers = {r["ticker"] for r in pos_rows}
        thesis_map       = {r["ticker"]: dict(r) for r in thesis_rows}

        stale_cutoff = (datetime.now() - timedelta(days=_STALE_DAYS)).isoformat()

        missing_thesis    = sorted(position_tickers - thesis_map.keys())
        stale_thesis      = sorted(
            t for t, v in thesis_map.items()
            if v["updated_at"] < stale_cutoff
        )
        missing_exit_plan = sorted(
            t for t, v in thesis_map.items()
            if not v["exit_plan"]
        )

        has_warnings = bool(missing_thesis or stale_thesis or missing_exit_plan)

        return {
            "missing_thesis":    missing_thesis,
            "stale_thesis":      stale_thesis,
            "missing_exit_plan": missing_exit_plan,
            "has_warnings":      has_warnings,
        }
    except Exception:
        log.warning("position_journal: get_thesis_warnings failed", exc_info=True)
        return {"missing_thesis": [], "stale_thesis": [], "missing_exit_plan": [],
                "has_warnings": False}


def get_review_summary() -> dict:
    """
    Combined review status: due reviews + thesis warnings.  Never raises.
    """
    return {
        "reviews":  get_due_reviews(),
        "warnings": get_thesis_warnings(),
    }
