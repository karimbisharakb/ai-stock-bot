"""
Phase A14 — Decision checklist and entry/exit discipline.

Per-ticker disciplined checklist before any position action
(ENTER / ADD / REDUCE / EXIT / HOLD).  10 default checks with completion
scoring and three-tier readiness.  Append-only audit trail.

Approving a checklist does NOT place any trade or order.
No broker integration.  No autonomous actions.

Public API:
  validate_checklist(ticker, decision_type)          -> list[str]   pure, no DB
  create_checklist(ticker, decision_type, ...)       -> dict
  get_checklist(checklist_id)                        -> dict | None
  get_all_checklists(ticker, decision_type, status)  -> list[dict]
  update_item(checklist_id, item_key, passed, note)  -> dict
  compute_scoring(checklist_id)                      -> dict
  approve_checklist(checklist_id, actor)             -> dict
  reject_checklist(checklist_id, reason, actor)      -> dict
  archive_checklist(checklist_id)                    -> dict
  get_summary()                                      -> dict
  get_pending_checklists()                           -> list[dict]
"""
import hashlib
import json
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

VALID_DECISION_TYPES = frozenset({"ENTER", "ADD", "REDUCE", "EXIT", "HOLD"})
VALID_STATUSES       = frozenset({"DRAFT", "READY", "APPROVED", "REJECTED", "ARCHIVED"})

_VALID_TRANSITIONS: dict = {
    "DRAFT":    frozenset({"READY", "APPROVED", "REJECTED", "ARCHIVED"}),
    "READY":    frozenset({"APPROVED", "REJECTED", "ARCHIVED"}),
    "APPROVED": frozenset({"ARCHIVED"}),
    "REJECTED": frozenset({"ARCHIVED"}),
    "ARCHIVED": frozenset(),
}

# Default checklist items seeded on creation: (item_key, label, required)
DEFAULT_ITEMS: list = [
    ("thesis_exists",             "Thesis exists for this ticker",                              True),
    ("exit_plan_exists",          "Exit plan defined in thesis",                                True),
    ("position_size_reasonable",  "Position size is reasonable relative to portfolio",          True),
    ("stop_invalidation_defined", "Stop/invalidation level defined",                            True),
    ("risk_reward_acceptable",    "Risk/reward ratio is acceptable",                            True),
    ("alpha_readiness_reviewed",  "Alpha readiness has been reviewed",                          True),
    ("qc_reviewed",               "QC reviewed (required for notification-driven decisions)",   False),
    ("concentration_checked",     "Portfolio concentration checked",                            True),
    ("market_regime_checked",     "Market regime checked",                                      True),
    ("catalyst_risk_checked",     "Catalyst risk reviewed",                                     True),
]


# ── DDL ───────────────────────────────────────────────────────────────────────

_CHECKLISTS_DDL = """
CREATE TABLE IF NOT EXISTS decision_checklists (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    checklist_id              TEXT    NOT NULL UNIQUE,
    ticker                    TEXT    NOT NULL,
    decision_type             TEXT    NOT NULL,
    linked_alpha_candidate_id TEXT,
    linked_thesis_id          INTEGER,
    checklist_status          TEXT    NOT NULL DEFAULT 'DRAFT',
    checklist_completion      REAL    NOT NULL DEFAULT 0.0,
    blocking_items            INTEGER NOT NULL DEFAULT 0,
    readiness                 TEXT    NOT NULL DEFAULT 'NOT_READY',
    notes                     TEXT    NOT NULL DEFAULT '',
    created_at                TEXT    NOT NULL,
    reviewed_at               TEXT,
    updated_at                TEXT    NOT NULL
)
"""

_ITEMS_DDL = """
CREATE TABLE IF NOT EXISTS decision_checklist_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    checklist_id TEXT    NOT NULL,
    item_key     TEXT    NOT NULL,
    label        TEXT    NOT NULL DEFAULT '',
    passed       INTEGER,
    note         TEXT    NOT NULL DEFAULT '',
    required     INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    UNIQUE(checklist_id, item_key)
)
"""

_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS decision_checklist_audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    checklist_id TEXT    NOT NULL,
    action       TEXT    NOT NULL,
    from_status  TEXT,
    to_status    TEXT,
    actor        TEXT,
    detail_json  TEXT,
    performed_at TEXT    NOT NULL
)
"""

_CHECKLISTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_dcl_ticker        ON decision_checklists(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_dcl_status        ON decision_checklists(checklist_status)",
    "CREATE INDEX IF NOT EXISTS idx_dcl_decision_type ON decision_checklists(decision_type)",
    "CREATE INDEX IF NOT EXISTS idx_dcl_created_at    ON decision_checklists(created_at)",
]

_ITEMS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_dcl_items_checklist_id ON decision_checklist_items(checklist_id)",
]

_AUDIT_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_dcl_audit_checklist_id ON decision_checklist_audit(checklist_id)",
    "CREATE INDEX IF NOT EXISTS idx_dcl_audit_performed_at ON decision_checklist_audit(performed_at)",
]


def _ensure_tables() -> None:
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(_CHECKLISTS_DDL)
        conn.execute(_ITEMS_DDL)
        conn.execute(_AUDIT_DDL)
        for idx in _CHECKLISTS_INDEXES + _ITEMS_INDEXES + _AUDIT_INDEXES:
            conn.execute(idx)
        conn.commit()
    except Exception:
        log.warning("decision_checklist: table creation failed", exc_info=True)
    finally:
        conn.close()


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _generate_checklist_id(ticker: str, decision_type: str, created_at: str) -> str:
    payload = f"{ticker}:{decision_type}:{created_at}"
    digest  = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"DCL-{digest}"


def _compute_scoring_from_items(items: list) -> dict:
    """
    Pure scoring function from a list of item dicts.
    Each item must have keys: passed (int | None), required (int | bool).

    Returns:
      checklist_completion  — % of items where passed IS NOT NULL
      blocking_items        — count of required items where passed = 0
      readiness             — NOT_READY | NEEDS_REVIEW | READY_FOR_MANUAL_DECISION
    """
    if not items:
        return {"checklist_completion": 0.0, "blocking_items": 0, "readiness": "NOT_READY"}

    total            = len(items)
    answered         = sum(1 for i in items if i["passed"] is not None)
    blocking         = sum(1 for i in items if i["required"] and i["passed"] == 0)
    req_unanswered   = sum(1 for i in items if i["required"] and i["passed"] is None)

    completion = round(answered / total * 100, 1)

    if req_unanswered > 0:
        readiness = "NOT_READY"
    elif blocking > 0:
        readiness = "NEEDS_REVIEW"
    else:
        readiness = "READY_FOR_MANUAL_DECISION"

    return {
        "checklist_completion": completion,
        "blocking_items":       blocking,
        "readiness":            readiness,
    }


# ── Validation (pure) ─────────────────────────────────────────────────────────

def validate_checklist(ticker: str, decision_type: str) -> list:
    """Validate checklist inputs.  Pure function.  Returns list of error strings."""
    errors = []
    if not ticker or not str(ticker).strip():
        errors.append("MISSING_TICKER")
    if decision_type not in VALID_DECISION_TYPES:
        errors.append(f"INVALID_DECISION_TYPE:{decision_type}")
    return errors


# ── Internal DB helpers ───────────────────────────────────────────────────────

def _get_checklist_row(checklist_id: str) -> Optional[dict]:
    try:
        from database import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM decision_checklists WHERE checklist_id=?", (checklist_id,)
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None
    except Exception:
        log.warning("decision_checklist: _get_checklist_row failed for %s", checklist_id, exc_info=True)
        return None


def _get_items(checklist_id: str) -> list:
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM decision_checklist_items WHERE checklist_id=? ORDER BY id",
                (checklist_id,)
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("decision_checklist: _get_items failed for %s", checklist_id, exc_info=True)
        return []


def _append_audit(
    checklist_id: str,
    action: str,
    from_status: Optional[str] = None,
    to_status: Optional[str]   = None,
    actor: Optional[str]       = None,
    detail: Optional[dict]     = None,
    now: Optional[str]         = None,
) -> None:
    """Append-only write to decision_checklist_audit.  Never raises."""
    if now is None:
        now = datetime.now().isoformat()
    try:
        from database import get_connection
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO decision_checklist_audit
                   (checklist_id, action, from_status, to_status, actor, detail_json, performed_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (checklist_id, action, from_status, to_status, actor,
                 json.dumps(detail) if detail else None, now),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.warning("decision_checklist: audit write failed for %s", checklist_id, exc_info=True)


def _transition_status(
    checklist_id: str,
    from_status: str,
    to_status: str,
    action: str             = "STATUS_CHANGE",
    actor: Optional[str]    = None,
    detail: Optional[dict]  = None,
) -> None:
    """Update checklist status and log to audit.  Raises on DB error."""
    now = datetime.now().isoformat()
    reviewed_at = now if to_status in ("APPROVED", "REJECTED") else None

    from database import get_connection
    conn = get_connection()
    try:
        if reviewed_at:
            conn.execute(
                "UPDATE decision_checklists SET checklist_status=?, reviewed_at=?, updated_at=? "
                "WHERE checklist_id=?",
                (to_status, reviewed_at, now, checklist_id),
            )
        else:
            conn.execute(
                "UPDATE decision_checklists SET checklist_status=?, updated_at=? "
                "WHERE checklist_id=?",
                (to_status, now, checklist_id),
            )
        conn.execute(
            """INSERT INTO decision_checklist_audit
               (checklist_id, action, from_status, to_status, actor, detail_json, performed_at)
               VALUES (?,?,?,?,?,?,?)""",
            (checklist_id, action, from_status, to_status, actor,
             json.dumps(detail) if detail else None, now),
        )
        conn.commit()
    finally:
        conn.close()


# ── Checklist CRUD ────────────────────────────────────────────────────────────

def create_checklist(
    ticker:                   str,
    decision_type:            str,
    linked_alpha_candidate_id: Optional[str] = None,
    linked_thesis_id:          Optional[int] = None,
    notes:                    str            = "",
) -> dict:
    """
    Create a new decision checklist with the 10 default items seeded as NULL.
    Returns {'ok': True, 'checklist_id': ..., 'checklist': {...}} or
            {'ok': False, 'errors': [...]}.
    Never raises.
    """
    _ensure_tables()
    ticker = str(ticker).strip().upper() if ticker else ""
    errors = validate_checklist(ticker, decision_type)
    if errors:
        return {"ok": False, "errors": errors, "ticker": ticker}

    now          = datetime.now().isoformat()
    checklist_id = _generate_checklist_id(ticker, decision_type, now)

    try:
        from database import get_connection
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO decision_checklists
                   (checklist_id, ticker, decision_type, linked_alpha_candidate_id,
                    linked_thesis_id, checklist_status, checklist_completion,
                    blocking_items, readiness, notes, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (checklist_id, ticker, decision_type, linked_alpha_candidate_id,
                 linked_thesis_id, "DRAFT", 0.0, 0, "NOT_READY", notes, now, now),
            )
            for key, label, required in DEFAULT_ITEMS:
                conn.execute(
                    """INSERT INTO decision_checklist_items
                       (checklist_id, item_key, label, passed, note, required, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (checklist_id, key, label, None, "", int(required), now, now),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log.error("decision_checklist: create_checklist failed for %s %s: %s",
                  ticker, decision_type, exc, exc_info=True)
        return {"ok": False, "errors": [f"DB_ERROR:{str(exc)[:100]}"], "ticker": ticker}

    _append_audit(
        checklist_id = checklist_id,
        action       = "CREATE",
        to_status    = "DRAFT",
        detail       = {"ticker": ticker, "decision_type": decision_type},
        now          = now,
    )

    checklist = get_checklist(checklist_id)
    return {"ok": True, "checklist_id": checklist_id, "checklist": checklist}


def get_checklist(checklist_id: str) -> Optional[dict]:
    """
    Return checklist row merged with its items list, or None if not found.
    Never raises.
    """
    _ensure_tables()
    cl = _get_checklist_row(checklist_id)
    if cl is None:
        return None
    items = _get_items(checklist_id)
    return {**cl, "items": items}


def get_all_checklists(
    ticker:        Optional[str] = None,
    decision_type: Optional[str] = None,
    status:        Optional[str] = None,
) -> list:
    """Return checklists ordered by created_at DESC.  Optional filters.  Never raises."""
    _ensure_tables()
    try:
        where  = []
        params = []
        if ticker:
            where.append("ticker=?")
            params.append(ticker.strip().upper())
        if decision_type:
            where.append("decision_type=?")
            params.append(decision_type.strip().upper())
        if status:
            where.append("checklist_status=?")
            params.append(status.strip().upper())

        sql = "SELECT * FROM decision_checklists"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC"

        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("decision_checklist: get_all_checklists failed", exc_info=True)
        return []


# ── Item updates and scoring ──────────────────────────────────────────────────

def update_item(
    checklist_id: str,
    item_key:     str,
    passed:       Optional[bool],
    note:         str = "",
) -> dict:
    """
    Update a single checklist item.  Recomputes scoring.
    Auto-advances DRAFT → READY when readiness = READY_FOR_MANUAL_DECISION.
    Returns {'ok': True, 'checklist_id': ..., 'item': {...}, 'scoring': {...}}
    or {'ok': False, 'errors': [...]}.
    Never raises.
    """
    _ensure_tables()
    try:
        cl = _get_checklist_row(checklist_id)
        if not cl:
            return {"ok": False, "errors": ["CHECKLIST_NOT_FOUND"], "checklist_id": checklist_id}

        if cl["checklist_status"] in ("APPROVED", "REJECTED", "ARCHIVED"):
            return {
                "ok":          False,
                "errors":      [f"INVALID_STATE:{cl['checklist_status']}"],
                "checklist_id": checklist_id,
            }

        from database import get_connection
        conn = get_connection()
        try:
            existing = conn.execute(
                "SELECT id FROM decision_checklist_items WHERE checklist_id=? AND item_key=?",
                (checklist_id, item_key),
            ).fetchone()
        finally:
            conn.close()

        if not existing:
            return {
                "ok":          False,
                "errors":      [f"ITEM_NOT_FOUND:{item_key}"],
                "checklist_id": checklist_id,
            }

        now        = datetime.now().isoformat()
        passed_int = None if passed is None else (1 if passed else 0)

        conn = get_connection()
        try:
            conn.execute(
                "UPDATE decision_checklist_items SET passed=?, note=?, updated_at=? "
                "WHERE checklist_id=? AND item_key=?",
                (passed_int, note, now, checklist_id, item_key),
            )
            conn.commit()
        finally:
            conn.close()

        scoring = compute_scoring(checklist_id)

        # Auto-advance DRAFT → READY when all required items pass
        if cl["checklist_status"] == "DRAFT" and scoring["readiness"] == "READY_FOR_MANUAL_DECISION":
            try:
                _transition_status(
                    checklist_id,
                    from_status = "DRAFT",
                    to_status   = "READY",
                    action      = "AUTO_ADVANCE",
                    detail      = {"reason": "all required items answered and passed"},
                )
            except Exception:
                log.warning("decision_checklist: auto-advance failed for %s", checklist_id, exc_info=True)

        conn = get_connection()
        try:
            updated_item = conn.execute(
                "SELECT * FROM decision_checklist_items WHERE checklist_id=? AND item_key=?",
                (checklist_id, item_key),
            ).fetchone()
        finally:
            conn.close()

        return {
            "ok":          True,
            "checklist_id": checklist_id,
            "item":        dict(updated_item) if updated_item else None,
            "scoring":     scoring,
        }
    except Exception as exc:
        log.error("decision_checklist: update_item failed for %s/%s: %s",
                  checklist_id, item_key, exc, exc_info=True)
        return {"ok": False, "errors": [f"DB_ERROR:{str(exc)[:100]}"], "checklist_id": checklist_id}


def compute_scoring(checklist_id: str) -> dict:
    """
    Recompute completion %, blocking_items, and readiness for a checklist.
    Persists the updated values to decision_checklists.
    Returns {checklist_completion, blocking_items, readiness}.  Never raises.
    """
    _ensure_tables()
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT passed, required FROM decision_checklist_items WHERE checklist_id=?",
                (checklist_id,),
            ).fetchall()
        finally:
            conn.close()

        items   = [{"passed": r["passed"], "required": r["required"]} for r in rows]
        scoring = _compute_scoring_from_items(items)

        now = datetime.now().isoformat()
        conn = get_connection()
        try:
            conn.execute(
                """UPDATE decision_checklists
                   SET checklist_completion=?, blocking_items=?, readiness=?, updated_at=?
                   WHERE checklist_id=?""",
                (scoring["checklist_completion"], scoring["blocking_items"],
                 scoring["readiness"], now, checklist_id),
            )
            conn.commit()
        finally:
            conn.close()

        return scoring
    except Exception:
        log.warning("decision_checklist: compute_scoring failed for %s", checklist_id, exc_info=True)
        return {"checklist_completion": 0.0, "blocking_items": 0, "readiness": "NOT_READY"}


# ── Status transitions ────────────────────────────────────────────────────────

def approve_checklist(
    checklist_id: str,
    actor:        Optional[str] = None,
) -> dict:
    """
    Transition a checklist to APPROVED.  Requires readiness = READY_FOR_MANUAL_DECISION.
    Valid from DRAFT or READY only.
    Approving does NOT place any trade.  Never raises.
    """
    _ensure_tables()
    cl = _get_checklist_row(checklist_id)
    if not cl:
        return {"ok": False, "errors": ["CHECKLIST_NOT_FOUND"], "checklist_id": checklist_id}

    current = cl["checklist_status"]
    if "APPROVED" not in _VALID_TRANSITIONS.get(current, set()):
        return {
            "ok":          False,
            "errors":      [f"INVALID_TRANSITION:{current}->APPROVED"],
            "checklist_id": checklist_id,
        }

    if cl["readiness"] != "READY_FOR_MANUAL_DECISION":
        return {
            "ok":          False,
            "errors":      [f"NOT_READY:{cl['readiness']}"],
            "checklist_id": checklist_id,
        }

    try:
        _transition_status(checklist_id, current, "APPROVED", action="APPROVE", actor=actor)
        return {"ok": True, "checklist_id": checklist_id, "checklist": get_checklist(checklist_id)}
    except Exception as exc:
        log.error("decision_checklist: approve_checklist failed for %s: %s",
                  checklist_id, exc, exc_info=True)
        return {"ok": False, "errors": [f"DB_ERROR:{str(exc)[:100]}"], "checklist_id": checklist_id}


def reject_checklist(
    checklist_id: str,
    reason:       str           = "",
    actor:        Optional[str] = None,
) -> dict:
    """
    Transition a checklist to REJECTED.  Valid from DRAFT or READY.
    Never raises.
    """
    _ensure_tables()
    cl = _get_checklist_row(checklist_id)
    if not cl:
        return {"ok": False, "errors": ["CHECKLIST_NOT_FOUND"], "checklist_id": checklist_id}

    current = cl["checklist_status"]
    if "REJECTED" not in _VALID_TRANSITIONS.get(current, set()):
        return {
            "ok":          False,
            "errors":      [f"INVALID_TRANSITION:{current}->REJECTED"],
            "checklist_id": checklist_id,
        }

    try:
        _transition_status(
            checklist_id, current, "REJECTED",
            action = "REJECT",
            actor  = actor,
            detail = {"reason": reason} if reason else None,
        )
        return {"ok": True, "checklist_id": checklist_id, "checklist": get_checklist(checklist_id)}
    except Exception as exc:
        log.error("decision_checklist: reject_checklist failed for %s: %s",
                  checklist_id, exc, exc_info=True)
        return {"ok": False, "errors": [f"DB_ERROR:{str(exc)[:100]}"], "checklist_id": checklist_id}


def archive_checklist(checklist_id: str) -> dict:
    """
    Transition a checklist to ARCHIVED.  Valid from APPROVED or REJECTED.
    Never raises.
    """
    _ensure_tables()
    cl = _get_checklist_row(checklist_id)
    if not cl:
        return {"ok": False, "errors": ["CHECKLIST_NOT_FOUND"], "checklist_id": checklist_id}

    current = cl["checklist_status"]
    if "ARCHIVED" not in _VALID_TRANSITIONS.get(current, set()):
        return {
            "ok":          False,
            "errors":      [f"INVALID_TRANSITION:{current}->ARCHIVED"],
            "checklist_id": checklist_id,
        }

    try:
        _transition_status(checklist_id, current, "ARCHIVED", action="ARCHIVE")
        return {"ok": True, "checklist_id": checklist_id, "checklist": get_checklist(checklist_id)}
    except Exception as exc:
        log.error("decision_checklist: archive_checklist failed for %s: %s",
                  checklist_id, exc, exc_info=True)
        return {"ok": False, "errors": [f"DB_ERROR:{str(exc)[:100]}"], "checklist_id": checklist_id}


# ── Summary and reporting ─────────────────────────────────────────────────────

def get_summary() -> dict:
    """
    Return aggregate counts by status and decision_type, plus pending checklists.
    Never raises.
    """
    _ensure_tables()
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT checklist_status, decision_type, COUNT(*) as cnt "
                "FROM decision_checklists GROUP BY checklist_status, decision_type"
            ).fetchall()
            pending_rows = conn.execute(
                "SELECT checklist_id, ticker, decision_type, checklist_status, "
                "checklist_completion, blocking_items, readiness, created_at "
                "FROM decision_checklists "
                "WHERE checklist_status IN ('DRAFT','READY') "
                "ORDER BY created_at DESC"
            ).fetchall()
        finally:
            conn.close()

        by_status:        dict = {}
        by_decision_type: dict = {}
        for r in rows:
            s = r["checklist_status"]
            d = r["decision_type"]
            by_status[s]        = by_status.get(s, 0) + r["cnt"]
            by_decision_type[d] = by_decision_type.get(d, 0) + r["cnt"]

        return {
            "pending_count":     by_status.get("DRAFT", 0) + by_status.get("READY", 0),
            "approved_count":    by_status.get("APPROVED", 0),
            "rejected_count":    by_status.get("REJECTED", 0),
            "archived_count":    by_status.get("ARCHIVED", 0),
            "by_decision_type":  by_decision_type,
            "pending_checklists": [dict(r) for r in pending_rows],
        }
    except Exception:
        log.warning("decision_checklist: get_summary failed", exc_info=True)
        return {
            "pending_count": 0, "approved_count": 0,
            "rejected_count": 0, "archived_count": 0,
            "by_decision_type": {}, "pending_checklists": [],
        }


def get_pending_checklists() -> list:
    """
    Return DRAFT and READY checklists ordered by created_at DESC.
    Used by the morning brief.  Never raises.
    """
    _ensure_tables()
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT checklist_id, ticker, decision_type, checklist_status, "
                "checklist_completion, blocking_items, readiness, created_at "
                "FROM decision_checklists "
                "WHERE checklist_status IN ('DRAFT','READY') "
                "ORDER BY created_at DESC"
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("decision_checklist: get_pending_checklists failed", exc_info=True)
        return []
