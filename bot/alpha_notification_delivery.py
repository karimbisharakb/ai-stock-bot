"""
Phase A10 — Alpha notification delivery bridge.

Controlled bridge between the A8 dry-run review layer and live WhatsApp
delivery.  Disabled by default via feature flags — does NOT send anything
unless ALL flags are explicitly configured.

Default state (safe):
  ALPHA_NOTIFICATIONS_ENABLED   = false  → all sends blocked
  ALPHA_NOTIFICATIONS_DRY_RUN_ONLY = true  → dry-run-only mode

Sending may only happen after passing:
  1. Feature flag checks
  2. Dry-run status (REVIEWED if required)
  3. QC allow_notification=true AND qc_tier >= min configured tier
  4. Duplicate check (same dry_run_id not already SENT)

Uses existing alerts.send_sms() — no new Twilio code.
No batch sending.  No auto-send.  No scheduler trigger.

Public API:
  get_delivery_flags()                      -> dict  (reads env vars)
  check_delivery_eligibility(dry_run,       -> (status, reason)  pure
                              qc_record, flags)
  deliver_notification(dry_run_id)          -> dict
  get_delivery_log(ticker, limit)           -> list[dict]
"""
import hashlib
import logging
import os
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

# ── QC tier rank (for min-tier comparison) ────────────────────────────────────

_QC_TIER_RANK: dict = {
    "BLOCK":    0,
    "SUPPRESS": 1,
    "ALLOW":    2,
    "PRIORITY": 3,
}

# ── Delivery result statuses ─────────────────────────────────────────────────

DELIVERY_STATUSES = (
    "SENT",
    "BLOCKED",
    "DRY_RUN_ONLY",
    "NOT_REVIEWED",
    "QC_BLOCKED",
    "DUPLICATE",
    "ERROR",
)


# ── Feature flags ─────────────────────────────────────────────────────────────

def get_delivery_flags() -> dict:
    """
    Read current feature flag state from environment variables.

    Reads at call time so tests can patch os.environ or this function directly.
    """
    def _bool(name: str, default: str) -> bool:
        return os.getenv(name, default).lower() in ("1", "true", "yes")

    return {
        "enabled":          _bool("ALPHA_NOTIFICATIONS_ENABLED", "false"),
        "dry_run_only":     _bool("ALPHA_NOTIFICATIONS_DRY_RUN_ONLY", "true"),
        "min_qc_tier":      os.getenv("ALPHA_NOTIFICATION_MIN_QC_TIER", "PRIORITY"),
        "require_reviewed": _bool("ALPHA_NOTIFICATION_REQUIRE_REVIEWED", "true"),
    }


# Alias — internal calls use this so tests can patch a single name
_read_flags = get_delivery_flags


# ── Pure eligibility check ────────────────────────────────────────────────────

def check_delivery_eligibility(
    dry_run: dict,
    qc_record: Optional[dict],
    flags: dict,
) -> tuple:
    """
    Deterministic eligibility gate.

    Pure function — no DB access, never raises.

    Returns (status, reason).
    status ∈ {ELIGIBLE, BLOCKED, DRY_RUN_ONLY, NOT_REVIEWED, QC_BLOCKED}.
    """
    # 1. Feature flags
    if not flags.get("enabled"):
        return "BLOCKED", "ALPHA_NOTIFICATIONS_DISABLED"

    if flags.get("dry_run_only"):
        return "DRY_RUN_ONLY", "ALPHA_NOTIFICATIONS_DRY_RUN_ONLY=true"

    # 2. Dry-run status
    dr_status = (dry_run or {}).get("status", "")
    if dr_status == "DISMISSED":
        return "BLOCKED", "DRY_RUN_DISMISSED"
    if dr_status == "EXPIRED":
        return "BLOCKED", "DRY_RUN_EXPIRED"
    if flags.get("require_reviewed") and dr_status != "REVIEWED":
        return "NOT_REVIEWED", f"DRY_RUN_STATUS={dr_status}"

    # 3. QC record
    if qc_record is None:
        return "QC_BLOCKED", "NO_QC_RECORD"

    if not qc_record.get("allow_notification"):
        sup_reason = qc_record.get("suppression_reason") or "BLOCKED"
        return "QC_BLOCKED", f"QC:{sup_reason}"

    qc_tier  = qc_record.get("qc_tier", "BLOCK")
    min_tier = flags.get("min_qc_tier", "PRIORITY")
    if _QC_TIER_RANK.get(qc_tier, 0) < _QC_TIER_RANK.get(min_tier, 3):
        return "QC_BLOCKED", f"QC_TIER_{qc_tier}_BELOW_MIN_{min_tier}"

    return "ELIGIBLE", "all_checks_passed"


# ── DB table management ───────────────────────────────────────────────────────

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS alpha_notification_delivery_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    dry_run_id        TEXT    NOT NULL,
    ticker            TEXT    NOT NULL DEFAULT '',
    readiness_tier    TEXT    NOT NULL DEFAULT '',
    message_hash      TEXT    NOT NULL DEFAULT '',
    status            TEXT    NOT NULL,
    reason            TEXT,
    provider_response TEXT,
    sent_at           TEXT    NOT NULL
)
"""


def _ensure_table() -> None:
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(_TABLE_DDL)
        conn.commit()
    except Exception:
        log.warning("alpha_notification_delivery: table creation failed", exc_info=True)
    finally:
        conn.close()


# ── Audit log (append-only) ───────────────────────────────────────────────────

def _insert_log(
    dry_run_id:        str,
    ticker:            str,
    readiness_tier:    str,
    message_hash:      str,
    status:            str,
    reason:            str,
    provider_response: Optional[str],
) -> None:
    """
    Append one entry to the delivery audit log.

    Caller is responsible for calling this.  Never raises — errors are logged.
    """
    try:
        from database import get_connection
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO alpha_notification_delivery_log
                   (dry_run_id, ticker, readiness_tier, message_hash,
                    status, reason, provider_response, sent_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (dry_run_id, ticker, readiness_tier, message_hash,
                 status, reason, provider_response, datetime.now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.warning(
            "alpha_notification_delivery: failed to write delivery log for %s", dry_run_id,
            exc_info=True,
        )


def _result(
    status: str,
    reason: str,
    dry_run_id: str = "",
    ticker: str = "",
    readiness_tier: str = "",
) -> dict:
    return {
        "dry_run_id":     dry_run_id,
        "ticker":         ticker,
        "readiness_tier": readiness_tier,
        "status":         status,
        "reason":         reason,
        "sent_at":        datetime.now().isoformat(),
    }


# ── Core delivery ─────────────────────────────────────────────────────────────

def deliver_notification(dry_run_id: str) -> dict:
    """
    Attempt to deliver one Alpha WhatsApp notification.

    Runs all eligibility checks before calling alerts.send_sms().
    Logs every attempt (allowed or blocked) to alpha_notification_delivery_log.
    Never raises — errors are captured and returned as status=ERROR.

    Returns dict: {dry_run_id, ticker, readiness_tier, status, reason, sent_at}.
    status ∈ DELIVERY_STATUSES.
    """
    _ensure_table()
    try:
        return _deliver_inner(dry_run_id)
    except Exception as exc:
        log.error("alpha_notification_delivery: unhandled error for %s: %s",
                  dry_run_id, exc, exc_info=True)
        reason = f"UNHANDLED_ERROR:{str(exc)[:150]}"
        _insert_log(dry_run_id, "", "", "", "ERROR", reason, None)
        return _result("ERROR", reason, dry_run_id=dry_run_id)


def _deliver_inner(dry_run_id: str) -> dict:
    flags = _read_flags()

    # ── 1. Feature flags (no DB needed) ──────────────────────────────────────
    if not flags["enabled"]:
        reason = "ALPHA_NOTIFICATIONS_DISABLED"
        _insert_log(dry_run_id, "", "", "", "BLOCKED", reason, None)
        return _result("BLOCKED", reason, dry_run_id=dry_run_id)

    if flags["dry_run_only"]:
        reason = "ALPHA_NOTIFICATIONS_DRY_RUN_ONLY=true"
        _insert_log(dry_run_id, "", "", "", "DRY_RUN_ONLY", reason, None)
        return _result("DRY_RUN_ONLY", reason, dry_run_id=dry_run_id)

    # ── 2. Fetch dry-run row ──────────────────────────────────────────────────
    from database import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM alpha_notification_dryruns WHERE dry_run_id = ?",
            (dry_run_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        reason = "DRY_RUN_NOT_FOUND"
        _insert_log(dry_run_id, "", "", "", "BLOCKED", reason, None)
        return _result("BLOCKED", reason, dry_run_id=dry_run_id)

    dry_run        = dict(row)
    ticker         = dry_run.get("ticker", "")
    readiness_tier = dry_run.get("readiness_tier", "")
    message_text   = dry_run.get("message_text", "")

    # ── 3. Dry-run status checks ──────────────────────────────────────────────
    dr_status = dry_run.get("status", "")

    if dr_status == "DISMISSED":
        reason = "DRY_RUN_DISMISSED"
        _insert_log(dry_run_id, ticker, readiness_tier, "", "BLOCKED", reason, None)
        return _result("BLOCKED", reason, dry_run_id=dry_run_id, ticker=ticker,
                       readiness_tier=readiness_tier)

    if dr_status == "EXPIRED":
        reason = "DRY_RUN_EXPIRED"
        _insert_log(dry_run_id, ticker, readiness_tier, "", "BLOCKED", reason, None)
        return _result("BLOCKED", reason, dry_run_id=dry_run_id, ticker=ticker,
                       readiness_tier=readiness_tier)

    if flags["require_reviewed"] and dr_status != "REVIEWED":
        reason = f"DRY_RUN_STATUS={dr_status}"
        _insert_log(dry_run_id, ticker, readiness_tier, "", "NOT_REVIEWED", reason, None)
        return _result("NOT_REVIEWED", reason, dry_run_id=dry_run_id, ticker=ticker,
                       readiness_tier=readiness_tier)

    # ── 4. QC check ───────────────────────────────────────────────────────────
    qc_record: Optional[dict] = None
    try:
        from alpha_notification_qc import get_qc_records
        records = get_qc_records(ticker=ticker, limit=1)
        qc_record = records[0] if records else None
    except Exception as exc:
        log.warning("alpha_notification_delivery: QC lookup failed for %s: %s", ticker, exc)

    el_status, el_reason = check_delivery_eligibility(dry_run, qc_record, flags)
    if el_status != "ELIGIBLE":
        _insert_log(dry_run_id, ticker, readiness_tier, "", el_status, el_reason, None)
        return _result(el_status, el_reason, dry_run_id=dry_run_id, ticker=ticker,
                       readiness_tier=readiness_tier)

    # ── 5. Duplicate send check ───────────────────────────────────────────────
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM alpha_notification_delivery_log "
            "WHERE dry_run_id = ? AND status = 'SENT'",
            (dry_run_id,),
        ).fetchone()
    finally:
        conn.close()

    if existing:
        reason = "DRY_RUN_ALREADY_SENT"
        _insert_log(dry_run_id, ticker, readiness_tier, "", "DUPLICATE", reason, None)
        return _result("DUPLICATE", reason, dry_run_id=dry_run_id, ticker=ticker,
                       readiness_tier=readiness_tier)

    # ── 6. Send via existing WhatsApp infrastructure ──────────────────────────
    msg_hash = hashlib.sha256(message_text.encode()).hexdigest()[:32]
    try:
        from alerts import send_sms
        sent = send_sms(message_text, bypass_quiet=False)
    except Exception as exc:
        reason = f"SEND_ERROR:{str(exc)[:100]}"
        _insert_log(dry_run_id, ticker, readiness_tier, msg_hash, "ERROR", reason, str(exc)[:200])
        log.error("alpha_notification_delivery: send_sms raised for %s: %s", ticker, exc)
        return _result("ERROR", reason, dry_run_id=dry_run_id, ticker=ticker,
                       readiness_tier=readiness_tier)

    if sent:
        _insert_log(dry_run_id, ticker, readiness_tier, msg_hash, "SENT", "OK", "sent=True")
        log.info("alpha_notification_delivery: SENT %s (%s)", dry_run_id, ticker)
        return _result("SENT", "OK", dry_run_id=dry_run_id, ticker=ticker,
                       readiness_tier=readiness_tier)
    else:
        reason = "SEND_FAILED"
        _insert_log(dry_run_id, ticker, readiness_tier, msg_hash, "ERROR", reason, "send_sms=False")
        return _result("ERROR", reason, dry_run_id=dry_run_id, ticker=ticker,
                       readiness_tier=readiness_tier)


# ── Read operations ───────────────────────────────────────────────────────────

def get_delivery_log(
    ticker: Optional[str] = None,
    limit:  int = 50,
) -> list:
    """
    Return delivery log rows ordered by sent_at DESC.
    Optional ticker filter.  Never raises.
    """
    _ensure_table()
    from database import get_connection
    conn = get_connection()
    try:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM alpha_notification_delivery_log "
                "WHERE ticker = ? ORDER BY sent_at DESC LIMIT ?",
                (ticker, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM alpha_notification_delivery_log "
                "ORDER BY sent_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("alpha_notification_delivery: get_delivery_log failed", exc_info=True)
        return []
    finally:
        conn.close()
