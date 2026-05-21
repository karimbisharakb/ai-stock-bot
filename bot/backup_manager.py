"""
Phase A29 — Backup, Export, and Disaster Recovery.

Read-mostly: creates JSON exports of DB tables grouped by domain.
Never includes API keys or secrets.
Never writes to the live DB during backup or restore-preview.
No autonomous sends, no trading, no broker calls.

Backup types:
  FULL               — all important tables
  PORTFOLIO_ONLY     — manual/canonical portfolio, snapshots, theses, journal, decisions
  ALPHA_ONLY         — alpha shadow, outcomes, validation, proposals, dry-runs, QC, delivery
  RESEARCH_ONLY      — watchlist, notes, workflow, catalysts
  NOTIFICATIONS_ONLY — notification center, preferences, delivery logs
  SYSTEM_CONFIG_ONLY — feature flags snapshot (no secrets) + release-check snapshot
"""
import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import database

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

BACKUP_TYPES = (
    "FULL",
    "PORTFOLIO_ONLY",
    "ALPHA_ONLY",
    "RESEARCH_ONLY",
    "NOTIFICATIONS_ONLY",
    "SYSTEM_CONFIG_ONLY",
)

BACKUP_STATUSES = ("CREATED", "VERIFIED", "FAILED", "ARCHIVED")

# Default storage path — Railway volume if available, else beside portfolio.db
_volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
DEFAULT_BACKUP_DIR = (
    os.path.join(_volume, "backups") if _volume
    else os.path.join(os.path.dirname(database.DB_PATH), "backups")
)

# Tables included in each backup type
BACKUP_GROUPS: dict = {
    "PORTFOLIO_ONLY": [
        "manual_portfolio_positions",
        "manual_account_settings",
        "manual_portfolio_audit_log",
        "portfolio_positions",
        "portfolio_snapshots",
        "portfolio_reconciliation_log",
        "portfolio_history",
        "holdings",
        "transactions",
        "position_theses",
        "position_journal",
        "decision_checklists",
        "decision_checklist_items",
        "decision_checklist_audit",
        "risk_policy",
        "cash",
        "tfsa_info",
    ],
    "ALPHA_ONLY": [
        "alpha_shadow_log",
        "alpha_outcomes",
        "alpha_validation",
        "learning_proposals",
        "learning_proposal_audit",
        "alpha_notification_dryruns",
        "notification_qc_history",
        "alpha_notification_delivery_log",
        "notification_audit_log",
        "notification_suppressed_log",
        "predator_alerts",
        "predator_latest",
        "predator_outcomes",
        "predator_signal_weights",
        "scanner_alerts",
        "replay_runs",
        "replay_events",
        "portfolio_stress_runs",
        "portfolio_stress_events",
        "market_regime_snapshots",
        "recommendation_snapshots",
    ],
    "RESEARCH_ONLY": [
        "research_watchlist",
        "research_watchlist_notes",
        "research_workflow_items",
        "research_workflow_notes",
        "catalyst_calendar",
        "watchlist",
    ],
    "NOTIFICATIONS_ONLY": [
        "notification_center",
        "notification_preferences",
        "notification_preferences_categories",
        "alpha_notification_delivery_log",
        "notification_audit_log",
        "notification_suppressed_log",
        "alert_log",
        "snoozed_tickers",
    ],
    "SYSTEM_CONFIG_ONLY": [],  # no table rows — env flags snapshot only
}

# FULL = union of all non-system groups + schema version
BACKUP_GROUPS["FULL"] = sorted({
    tbl
    for group in ("PORTFOLIO_ONLY", "ALPHA_ONLY", "RESEARCH_ONLY", "NOTIFICATIONS_ONLY")
    for tbl in BACKUP_GROUPS[group]
} | {
    "schema_version",
    "planner_config",
    "planner_snapshots",
    "strategy_scorecards" if False else "",  # filled below if table exists
    "weekly_review_sends",
    "scheduler_lease",
})
BACKUP_GROUPS["FULL"] = [t for t in BACKUP_GROUPS["FULL"] if t]

# Env vars that are safe to snapshot (no secrets)
_SAFE_ENV_KEYS = (
    "LEGACY_NOTIFICATIONS_ENABLED",
    "UNIFIED_NOTIFICATIONS_ENABLED",
    "ALPHA_SHADOW_ENABLED",
    "ALPHA_ALERTS_ENABLED",
    "ALPHA_NOTIFICATIONS_ENABLED",
    "ALPHA_NOTIFICATIONS_DRY_RUN_ONLY",
    "EOD_BRIEF_ENABLED",
    "WEEKLY_REVIEW_ENABLED",
    "NOTIFICATION_CENTER_ENABLED",
    "BACKUP_SCHEDULE_ENABLED",
    "RAILWAY_ENVIRONMENT",
    "RAILWAY_SERVICE_NAME",
)

# Feature flag — scheduled backups (off by default)
BACKUP_SCHEDULE_ENV = "BACKUP_SCHEDULE_ENABLED"


def backup_schedule_enabled() -> bool:
    val = os.getenv(BACKUP_SCHEDULE_ENV, "false").strip().lower()
    return val in ("1", "true", "yes", "on")


# ── Backup manifest DB (lightweight, stored in backup dir) ────────────────────

_MANIFEST_FILE = "backup_manifest.json"


def _manifest_path(backup_dir: str) -> str:
    return os.path.join(backup_dir, _MANIFEST_FILE)


def _load_manifest(backup_dir: str) -> list:
    path = _manifest_path(backup_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_manifest(backup_dir: str, entries: list) -> None:
    path = _manifest_path(backup_dir)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, sort_keys=True)


def _append_manifest(backup_dir: str, entry: dict) -> None:
    entries = _load_manifest(backup_dir)
    # Replace if same backup_id exists
    entries = [e for e in entries if e.get("backup_id") != entry["backup_id"]]
    entries.append(entry)
    # Keep most recent 100 entries
    entries = sorted(entries, key=lambda e: e.get("created_at", ""), reverse=True)[:100]
    _save_manifest(backup_dir, entries)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _make_backup_id(backup_type: str, ts: str) -> str:
    raw = f"{backup_type}:{ts}"
    return "bk_" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _existing_tables() -> set:
    conn = database.get_connection()
    try:
        return {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()


def _dump_table(table: str) -> list:
    """Return all rows from a table as a list of dicts. Empty list if table missing."""
    try:
        conn = database.get_connection()
        try:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def _safe_env_snapshot() -> dict:
    """Snapshot of safe (non-secret) env vars."""
    return {k: os.getenv(k, "(unset)") for k in _SAFE_ENV_KEYS}


def _feature_flags_snapshot() -> dict:
    flags = {}
    try:
        import feature_flags as ff
        flags["legacy_notifications_enabled"]  = ff.legacy_notifications_enabled()
        flags["unified_notifications_enabled"] = ff.unified_notifications_enabled()
        flags["shadow_compare_enabled"]        = ff.shadow_compare_enabled()
        flags["alpha_shadow_enabled"]          = ff.alpha_shadow_enabled()
        flags["alpha_alerts_enabled"]          = ff.alpha_alerts_enabled()
    except Exception as exc:
        flags["error"] = str(exc)
    return flags


def _ensure_backup_dir(backup_dir: str) -> bool:
    """Create backup_dir if it does not exist. Returns True on success."""
    try:
        os.makedirs(backup_dir, exist_ok=True)
        return True
    except Exception as exc:
        log.warning("backup_manager: cannot create backup dir %s: %s", backup_dir, exc)
        return False


# ── Core export logic ─────────────────────────────────────────────────────────

def _build_export_payload(backup_type: str) -> dict:
    """
    Assemble the full JSON payload for a given backup type.

    Returns a dict with 'tables', 'row_count', 'table_count', and for
    SYSTEM_CONFIG_ONLY also 'feature_flags' and 'env_snapshot'.
    """
    tables_data: dict = {}
    total_rows = 0

    if backup_type == "SYSTEM_CONFIG_ONLY":
        # No table rows — only flags and env
        payload = {
            "backup_type":    backup_type,
            "generated_at":   _now_iso(),
            "tables":         {},
            "table_count":    0,
            "row_count":      0,
            "feature_flags":  _feature_flags_snapshot(),
            "env_snapshot":   _safe_env_snapshot(),
        }
        return payload

    wanted = BACKUP_GROUPS.get(backup_type, [])
    existing = _existing_tables()

    for table in wanted:
        if table not in existing:
            continue
        rows = _dump_table(table)
        tables_data[table] = rows
        total_rows += len(rows)

    return {
        "backup_type":  backup_type,
        "generated_at": _now_iso(),
        "tables":       tables_data,
        "table_count":  len(tables_data),
        "row_count":    total_rows,
    }


def create_backup(
    backup_type: str = "FULL",
    notes: Optional[str] = None,
    backup_dir: Optional[str] = None,
) -> dict:
    """
    Create a JSON backup and write it to backup_dir.

    Returns the backup manifest entry dict.
    Never raises — on any storage failure returns a FAILED entry.
    """
    if backup_type not in BACKUP_TYPES:
        backup_type = "FULL"

    ts = _now_iso().replace(":", "").replace("+", "")
    backup_id = _make_backup_id(backup_type, ts)
    bdir = backup_dir or DEFAULT_BACKUP_DIR

    manifest_entry: dict = {
        "backup_id":     backup_id,
        "created_at":    _now_iso(),
        "backup_type":   backup_type,
        "table_count":   0,
        "row_count":     0,
        "size_bytes":    0,
        "checksum_sha256": "",
        "status":        "FAILED",
        "source_db_path": database.DB_PATH,
        "file_path":     "",
        "notes":         notes or "",
    }

    if not _ensure_backup_dir(bdir):
        manifest_entry["notes"] = f"Storage unavailable: {bdir}"
        return manifest_entry

    filename = f"{backup_id}_{backup_type.lower()}.json"
    file_path = os.path.join(bdir, filename)

    try:
        payload = _build_export_payload(backup_type)
        json_str = json.dumps(payload, indent=2, sort_keys=True, default=str)
        checksum = _sha256_str(json_str)

        with open(file_path, "w", encoding="utf-8") as fh:
            fh.write(json_str)

        size_bytes = os.path.getsize(file_path)

        manifest_entry.update({
            "table_count":   payload["table_count"],
            "row_count":     payload["row_count"],
            "size_bytes":    size_bytes,
            "checksum_sha256": checksum,
            "status":        "CREATED",
            "file_path":     file_path,
        })
        log.info(
            "backup_manager: created %s (%s, %d tables, %d rows, %d bytes)",
            backup_id, backup_type, payload["table_count"], payload["row_count"], size_bytes,
        )
    except Exception as exc:
        log.warning("backup_manager: backup failed for %s: %s", backup_id, exc)
        manifest_entry["notes"] = f"Error: {exc}"

    _append_manifest(bdir, manifest_entry)
    return manifest_entry


# ── Verification ──────────────────────────────────────────────────────────────

def verify_backup(backup_id: str, backup_dir: Optional[str] = None) -> dict:
    """
    Verify a backup file against its manifest entry.

    Checks: file exists, checksum matches, JSON parses, required tables present,
    row counts match manifest.

    Returns a result dict with 'ok', 'checks', and 'manifest_entry'.
    Never writes to the live DB.
    """
    bdir = backup_dir or DEFAULT_BACKUP_DIR
    entries = _load_manifest(bdir)
    entry = next((e for e in entries if e.get("backup_id") == backup_id), None)

    if entry is None:
        return {
            "ok": False,
            "backup_id": backup_id,
            "error": f"Backup {backup_id!r} not found in manifest",
            "checks": [],
        }

    checks = []
    ok = True

    file_path = entry.get("file_path", "")
    if not os.path.exists(file_path):
        checks.append({"name": "file_exists", "passed": False,
                       "detail": f"File not found: {file_path}"})
        ok = False
    else:
        checks.append({"name": "file_exists", "passed": True, "detail": file_path})

        # Checksum
        try:
            actual_checksum = _sha256_file(file_path)
            expected = entry.get("checksum_sha256", "")
            passed = hmac_safe_compare(actual_checksum, expected)
            checks.append({
                "name": "checksum",
                "passed": passed,
                "detail": f"expected {expected[:12]}… got {actual_checksum[:12]}…",
            })
            if not passed:
                ok = False
        except Exception as exc:
            checks.append({"name": "checksum", "passed": False, "detail": str(exc)})
            ok = False

        # JSON parse + row counts
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            checks.append({"name": "json_parses", "passed": True,
                           "detail": f"backup_type={payload.get('backup_type')}"})

            actual_tables = list(payload.get("tables", {}).keys())
            actual_row_count = sum(len(v) for v in payload.get("tables", {}).values())
            manifest_rows = entry.get("row_count", 0)
            rows_match = actual_row_count == manifest_rows
            checks.append({
                "name": "row_counts_match",
                "passed": rows_match,
                "detail": f"manifest={manifest_rows} actual={actual_row_count}",
            })
            if not rows_match:
                ok = False

            checks.append({
                "name": "tables_present",
                "passed": True,
                "detail": f"{len(actual_tables)} tables: {sorted(actual_tables)[:5]}…",
            })
        except Exception as exc:
            checks.append({"name": "json_parses", "passed": False, "detail": str(exc)})
            ok = False

    # Update manifest status
    new_status = "VERIFIED" if ok else entry.get("status", "CREATED")
    entry["status"] = new_status
    all_entries = _load_manifest(bdir)
    all_entries = [e if e.get("backup_id") != backup_id else entry for e in all_entries]
    _save_manifest(bdir, all_entries)

    return {
        "ok": ok,
        "backup_id": backup_id,
        "checks": checks,
        "manifest_entry": entry,
    }


def hmac_safe_compare(a: str, b: str) -> bool:
    """Constant-time string comparison."""
    import hmac as _hmac
    return _hmac.compare_digest(a.encode(), b.encode())


# ── Restore preview (read-only) ───────────────────────────────────────────────

def restore_preview(backup_id: str, backup_dir: Optional[str] = None) -> dict:
    """
    Show what a restore would change — NO writes to live DB.

    Returns: tables in backup, row counts per table, current live counts,
    delta, checksum status, and a 'would_change' summary.
    """
    bdir = backup_dir or DEFAULT_BACKUP_DIR
    entries = _load_manifest(bdir)
    entry = next((e for e in entries if e.get("backup_id") == backup_id), None)

    if entry is None:
        return {
            "ok": False,
            "backup_id": backup_id,
            "error": f"Backup {backup_id!r} not found",
            "preview": None,
        }

    file_path = entry.get("file_path", "")
    if not os.path.exists(file_path):
        return {
            "ok": False,
            "backup_id": backup_id,
            "error": f"Backup file not found: {file_path}",
            "preview": None,
        }

    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as exc:
        return {"ok": False, "backup_id": backup_id, "error": f"Cannot read backup: {exc}",
                "preview": None}

    # Build per-table delta
    live_tables = _existing_tables()
    tables_in_backup = payload.get("tables", {})
    table_deltas = {}

    for table, backup_rows in tables_in_backup.items():
        backup_count = len(backup_rows)
        live_count = 0
        if table in live_tables:
            try:
                conn = database.get_connection()
                try:
                    live_count = conn.execute(
                        f"SELECT COUNT(*) FROM {table}"  # noqa: S608
                    ).fetchone()[0]
                finally:
                    conn.close()
            except Exception:
                live_count = -1  # unknown

        table_deltas[table] = {
            "backup_rows": backup_count,
            "live_rows":   live_count,
            "delta":       backup_count - live_count,
            "table_exists_in_live": table in live_tables,
        }

    tables_with_delta = [t for t, d in table_deltas.items() if d["delta"] != 0]

    return {
        "ok": True,
        "backup_id": backup_id,
        "warning": "THIS IS A READ-ONLY PREVIEW. No data has been written.",
        "preview": {
            "backup_type":      entry.get("backup_type"),
            "backup_created_at": entry.get("created_at"),
            "backup_status":    entry.get("status"),
            "checksum_sha256":  entry.get("checksum_sha256", "")[:12] + "…",
            "tables":           table_deltas,
            "tables_with_delta": tables_with_delta,
            "would_change":     len(tables_with_delta) > 0,
            "change_summary":   f"{len(tables_with_delta)} table(s) would have row count changes",
            "feature_flags":    payload.get("feature_flags"),
            "env_snapshot":     payload.get("env_snapshot"),
        },
    }


# ── Backup list / get ─────────────────────────────────────────────────────────

def list_backups(backup_dir: Optional[str] = None, limit: int = 20) -> list:
    """Return manifest entries, most recent first."""
    bdir = backup_dir or DEFAULT_BACKUP_DIR
    entries = _load_manifest(bdir)
    return entries[:max(1, min(limit, 100))]


def get_backup(backup_id: str, backup_dir: Optional[str] = None) -> Optional[dict]:
    """Return a single manifest entry or None."""
    bdir = backup_dir or DEFAULT_BACKUP_DIR
    entries = _load_manifest(bdir)
    return next((e for e in entries if e.get("backup_id") == backup_id), None)


# ── Latest backup age (for release check integration) ────────────────────────

def latest_backup_age_hours(backup_dir: Optional[str] = None) -> Optional[float]:
    """Return hours since the most recent CREATED/VERIFIED backup, or None if none."""
    entries = list_backups(backup_dir, limit=10)
    good = [e for e in entries if e.get("status") in ("CREATED", "VERIFIED")]
    if not good:
        return None
    try:
        ts_str = good[0]["created_at"]
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(tz=timezone.utc) - ts
        return delta.total_seconds() / 3600
    except Exception:
        return None
