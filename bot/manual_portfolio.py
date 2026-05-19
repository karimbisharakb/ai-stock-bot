"""
Phase A12 — Manual portfolio control and account setup.

Safe manual controls for personal holdings and account settings.
Allows correction of canonical portfolio state before broker sync exists.
Never calls record_buy_trade, reduce_or_remove_holding, set_cash (trading ops),
or any broker API.

Public API:
  validate_position(ticker, quantity, avg_cost, account_type, currency) -> list[str]
  validate_account_settings(account_type, currency, available_cash)     -> list[str]
  upsert_position(ticker, quantity, avg_cost, ...)                      -> dict
  deactivate_position(ticker)                                            -> dict
  get_manual_positions(include_inactive)                                 -> list[dict]
  get_account_settings()                                                 -> dict
  update_account_settings(...)                                           -> dict
  get_manual_portfolio()                                                 -> dict
  reconcile_manual()                                                     -> dict
  get_audit_log(limit)                                                   -> list[dict]
"""
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

SUPPORTED_CURRENCIES    = frozenset({"CAD", "USD"})
SUPPORTED_ACCOUNT_TYPES = frozenset({"TFSA", "CASH", "RRSP", "OTHER"})


# ── DB DDL ────────────────────────────────────────────────────────────────────

_MANUAL_POSITIONS_DDL = """
CREATE TABLE IF NOT EXISTS manual_portfolio_positions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT    NOT NULL UNIQUE,
    quantity     REAL    NOT NULL,
    avg_cost     REAL    NOT NULL,
    realized_pnl REAL    NOT NULL DEFAULT 0.0,
    account_type TEXT    NOT NULL DEFAULT 'TFSA',
    currency     TEXT    NOT NULL DEFAULT 'CAD',
    note         TEXT    NOT NULL DEFAULT '',
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
)
"""

_ACCOUNT_SETTINGS_DDL = """
CREATE TABLE IF NOT EXISTS manual_account_settings (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    account_name      TEXT    NOT NULL DEFAULT '',
    account_type      TEXT    NOT NULL DEFAULT 'TFSA',
    base_currency     TEXT    NOT NULL DEFAULT 'CAD',
    available_cash    REAL    NOT NULL DEFAULT 0.0,
    contribution_room REAL,
    notes             TEXT    NOT NULL DEFAULT '',
    updated_at        TEXT    NOT NULL
)
"""

_AUDIT_LOG_DDL = """
CREATE TABLE IF NOT EXISTS manual_portfolio_audit_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    action         TEXT    NOT NULL,
    subject        TEXT    NOT NULL,
    old_value_json TEXT,
    new_value_json TEXT,
    performed_at   TEXT    NOT NULL
)
"""

_AUDIT_LOG_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_manual_audit_action      ON manual_portfolio_audit_log(action)",
    "CREATE INDEX IF NOT EXISTS idx_manual_audit_subject     ON manual_portfolio_audit_log(subject)",
    "CREATE INDEX IF NOT EXISTS idx_manual_audit_performed_at ON manual_portfolio_audit_log(performed_at)",
]


def _ensure_tables() -> None:
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(_MANUAL_POSITIONS_DDL)
        conn.execute(_ACCOUNT_SETTINGS_DDL)
        conn.execute(_AUDIT_LOG_DDL)
        for idx in _AUDIT_LOG_INDEXES:
            conn.execute(idx)
        # Seed single-row account settings if absent
        conn.execute(
            "INSERT OR IGNORE INTO manual_account_settings "
            "(id, account_name, account_type, base_currency, available_cash, "
            " contribution_room, notes, updated_at) "
            "VALUES (1,'','TFSA','CAD',0.0,NULL,'',?)",
            (datetime.now().isoformat(),),
        )
        conn.commit()
    except Exception:
        log.warning("manual_portfolio: table creation failed", exc_info=True)
    finally:
        conn.close()


# ── Validation (pure functions) ───────────────────────────────────────────────

def validate_position(
    ticker:       str,
    quantity:     float,
    avg_cost:     float,
    account_type: str = "TFSA",
    currency:     str = "CAD",
) -> list:
    """
    Validate manual position inputs.  Pure function — no DB access, never raises.
    Returns a list of error strings (empty list = valid).
    """
    errors = []
    if not ticker or not str(ticker).strip():
        errors.append("MISSING_TICKER")
    if quantity < 0:
        errors.append(f"NEGATIVE_QUANTITY:{quantity}")
    if avg_cost < 0:
        errors.append(f"IMPOSSIBLE_AVG_COST:{avg_cost}")
    if currency not in SUPPORTED_CURRENCIES:
        errors.append(f"UNSUPPORTED_CURRENCY:{currency}")
    if account_type not in SUPPORTED_ACCOUNT_TYPES:
        errors.append(f"UNSUPPORTED_ACCOUNT_TYPE:{account_type}")
    return errors


def validate_account_settings(
    account_type:  str   = "TFSA",
    base_currency: str   = "CAD",
    available_cash: float = 0.0,
) -> list:
    """
    Validate account settings inputs.  Pure function — never raises.
    Returns a list of error strings (empty list = valid).
    """
    errors = []
    if account_type not in SUPPORTED_ACCOUNT_TYPES:
        errors.append(f"UNSUPPORTED_ACCOUNT_TYPE:{account_type}")
    if base_currency not in SUPPORTED_CURRENCIES:
        errors.append(f"UNSUPPORTED_CURRENCY:{base_currency}")
    if available_cash < 0:
        errors.append(f"NEGATIVE_CASH:{available_cash}")
    return errors


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_position_by_ticker(ticker: str) -> Optional[dict]:
    """Fetch one manual position row by ticker.  Returns None if not found."""
    try:
        from database import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM manual_portfolio_positions WHERE ticker = ?", (ticker,)
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def _log_audit(
    action:         str,
    subject:        str,
    old_value:      Optional[dict],
    new_value:      Optional[dict],
    performed_at:   str,
) -> None:
    """Append one entry to the audit log.  Never raises.  Append-only."""
    try:
        from database import get_connection
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO manual_portfolio_audit_log
                   (action, subject, old_value_json, new_value_json, performed_at)
                   VALUES (?,?,?,?,?)""",
                (
                    action,
                    subject,
                    json.dumps(old_value) if old_value is not None else None,
                    json.dumps(new_value) if new_value is not None else None,
                    performed_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.warning("manual_portfolio: audit log write failed for %s", action, exc_info=True)


# ── Position management ───────────────────────────────────────────────────────

def upsert_position(
    ticker:       str,
    quantity:     float,
    avg_cost:     float,
    realized_pnl: float = 0.0,
    account_type: str   = "TFSA",
    currency:     str   = "CAD",
    note:         str   = "",
) -> dict:
    """
    Insert or update a manual position.  Validates inputs.
    Logs to audit trail.  Never raises.
    Returns {'ok': True, 'ticker': ..., 'position': {...}} on success,
    or     {'ok': False, 'errors': [...], 'ticker': ...} on validation failure.
    Sets active=1 on upsert (re-activates a previously deactivated position).
    """
    _ensure_tables()
    ticker = str(ticker).strip().upper() if ticker else ""
    errors = validate_position(ticker, quantity, avg_cost, account_type, currency)
    if errors:
        return {"ok": False, "errors": errors, "ticker": ticker}

    now       = datetime.now().isoformat()
    old_value = _get_position_by_ticker(ticker)

    try:
        from database import get_connection
        conn = get_connection()
        try:
            existing = conn.execute(
                "SELECT created_at FROM manual_portfolio_positions WHERE ticker = ?",
                (ticker,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now

            conn.execute(
                """INSERT OR REPLACE INTO manual_portfolio_positions
                   (ticker, quantity, avg_cost, realized_pnl, account_type, currency,
                    note, active, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,1,?,?)""",
                (ticker, quantity, avg_cost, realized_pnl, account_type, currency,
                 note, created_at, now),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log.error("manual_portfolio: upsert_position failed for %s: %s", ticker, exc, exc_info=True)
        return {"ok": False, "errors": [f"DB_ERROR:{str(exc)[:100]}"], "ticker": ticker}

    new_value = _get_position_by_ticker(ticker)
    _log_audit("UPSERT_POSITION", ticker, old_value, new_value, now)
    return {"ok": True, "ticker": ticker, "position": new_value}


def deactivate_position(ticker: str) -> dict:
    """
    Mark a manual position as inactive.  Does NOT delete the row (audit safety).
    Returns {'ok': True, 'ticker': ...} or {'ok': False, 'error': '...'}.
    Never raises.
    """
    _ensure_tables()
    ticker = str(ticker).strip().upper() if ticker else ""
    if not ticker:
        return {"ok": False, "error": "MISSING_TICKER"}

    now       = datetime.now().isoformat()
    old_value = _get_position_by_ticker(ticker)
    if old_value is None:
        return {"ok": False, "error": "POSITION_NOT_FOUND", "ticker": ticker}

    try:
        from database import get_connection
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE manual_portfolio_positions SET active=0, updated_at=? WHERE ticker=?",
                (now, ticker),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log.error("manual_portfolio: deactivate_position failed for %s: %s", ticker, exc)
        return {"ok": False, "error": f"DB_ERROR:{str(exc)[:100]}", "ticker": ticker}

    new_value = _get_position_by_ticker(ticker)
    _log_audit("DEACTIVATE_POSITION", ticker, old_value, new_value, now)
    return {"ok": True, "ticker": ticker}


def get_manual_positions(include_inactive: bool = False) -> list:
    """
    Return manual position rows.  By default returns only active positions.
    Never raises.
    """
    _ensure_tables()
    try:
        from database import get_connection
        conn = get_connection()
        try:
            if include_inactive:
                rows = conn.execute(
                    "SELECT * FROM manual_portfolio_positions ORDER BY ticker"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM manual_portfolio_positions "
                    "WHERE active=1 ORDER BY ticker"
                ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("manual_portfolio: get_manual_positions failed", exc_info=True)
        return []


# ── Account settings ──────────────────────────────────────────────────────────

def get_account_settings() -> dict:
    """
    Return the single manual account settings row.
    Returns defaults if table has no row yet.  Never raises.
    """
    _ensure_tables()
    try:
        from database import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM manual_account_settings WHERE id = 1"
            ).fetchone()
        finally:
            conn.close()
        if row:
            return dict(row)
    except Exception:
        log.warning("manual_portfolio: get_account_settings failed", exc_info=True)

    return {
        "id":                1,
        "account_name":      "",
        "account_type":      "TFSA",
        "base_currency":     "CAD",
        "available_cash":    0.0,
        "contribution_room": None,
        "notes":             "",
        "updated_at":        datetime.now().isoformat(),
    }


def update_account_settings(
    account_name:      Optional[str]   = None,
    account_type:      Optional[str]   = None,
    base_currency:     Optional[str]   = None,
    available_cash:    Optional[float] = None,
    contribution_room: Optional[float] = None,
    notes:             Optional[str]   = None,
) -> dict:
    """
    Update manual account settings.  Only non-None fields are changed.
    Validates account_type, base_currency, and available_cash.
    Logs to audit trail.  Never raises.
    Returns {'ok': True, 'settings': {...}} or {'ok': False, 'errors': [...]}.
    """
    _ensure_tables()
    now      = datetime.now().isoformat()
    old_vals = get_account_settings()

    # Merge with current values so we can validate the final state
    merged_type     = account_type  if account_type  is not None else old_vals.get("account_type",  "TFSA")
    merged_currency = base_currency if base_currency is not None else old_vals.get("base_currency", "CAD")
    merged_cash     = available_cash if available_cash is not None else old_vals.get("available_cash", 0.0)

    errors = validate_account_settings(merged_type, merged_currency, merged_cash)
    if errors:
        return {"ok": False, "errors": errors}

    # Build the SET clause from supplied (non-None) fields
    updates = []
    params  = []

    if account_name is not None:
        updates.append("account_name=?");      params.append(account_name)
    if account_type is not None:
        updates.append("account_type=?");      params.append(account_type)
    if base_currency is not None:
        updates.append("base_currency=?");     params.append(base_currency)
    if available_cash is not None:
        updates.append("available_cash=?");    params.append(available_cash)
    if contribution_room is not None:
        updates.append("contribution_room=?"); params.append(contribution_room)
    if notes is not None:
        updates.append("notes=?");             params.append(notes)

    updates.append("updated_at=?")
    params.append(now)
    params.append(1)  # WHERE id=1

    try:
        from database import get_connection
        conn = get_connection()
        try:
            conn.execute(
                f"UPDATE manual_account_settings SET {', '.join(updates)} WHERE id=?",
                params,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log.error("manual_portfolio: update_account_settings failed: %s", exc, exc_info=True)
        return {"ok": False, "errors": [f"DB_ERROR:{str(exc)[:100]}"]}

    new_vals = get_account_settings()
    _log_audit("UPDATE_ACCOUNT_SETTINGS", "account", old_vals, new_vals, now)
    return {"ok": True, "settings": new_vals}


# ── Portfolio read ────────────────────────────────────────────────────────────

def get_manual_portfolio() -> dict:
    """
    Return combined view: active positions + account settings.  Never raises.
    """
    return {
        "positions":        get_manual_positions(include_inactive=False),
        "account_settings": get_account_settings(),
    }


# ── Manual reconciliation ─────────────────────────────────────────────────────

def reconcile_manual() -> dict:
    """
    Rebuild canonical portfolio from manual_portfolio_positions + manual_account_settings.

    Steps:
      1. Read active manual positions
      2. Read account settings (cash, contribution_room)
      3. Update cash table with manual available_cash → canonical state stays in sync
      4. Fetch live prices via market_data
      5. Build canonical position dicts (via portfolio_reconciliation.build_position)
      6. Upsert portfolio_positions (A11 canonical table)
      7. Take immutable snapshot via portfolio_reconciliation.take_snapshot
      8. Log reconciliation run

    Never raises — returns status='ERROR' on failure.
    Never calls trading operations (record_buy_trade, reduce_or_remove_holding, etc.).
    """
    _ensure_tables()
    t0     = time.monotonic()
    run_id = str(uuid.uuid4())
    now    = datetime.now().isoformat()
    issues: list = []

    try:
        result      = _reconcile_manual_inner(run_id, now, issues)
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        result["duration_ms"] = duration_ms
        _write_recon_log(run_id, "manual_api", result.get("position_count", 0),
                         issues, duration_ms, "OK", now)
        return result
    except Exception as exc:
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        log.error("manual_portfolio: reconcile_manual unhandled error: %s", exc, exc_info=True)
        issues.append(f"UNHANDLED_ERROR:{str(exc)[:150]}")
        _write_recon_log(run_id, "manual_api", 0, issues, duration_ms, "ERROR", now)
        return {
            "status":         "ERROR",
            "run_id":         run_id,
            "positions":      [],
            "aggregates":     {},
            "issues":         issues,
            "position_count": 0,
            "reconciled_at":  now,
        }


def _reconcile_manual_inner(run_id: str, now: str, issues: list) -> dict:
    from strategy import get_usd_cad_rate
    from portfolio_reconciliation import (
        build_position, compute_aggregates, _upsert_positions, take_snapshot,
    )

    usdcad = get_usd_cad_rate()

    # 1. Read active manual positions
    active_positions = get_manual_positions(include_inactive=False)

    # 2. Read account settings
    account = get_account_settings()
    manual_cash = float(account.get("available_cash") or 0.0)

    # 3. Sync cash table with manual available_cash
    _sync_cash(manual_cash)

    # 4 & 5. Fetch prices, build canonical positions
    price_fetched_at = datetime.now().isoformat()
    canonical        = []
    seen_tickers     = set()

    total_market_est = manual_cash + sum(
        p["avg_cost"] * p["quantity"] for p in active_positions
    )

    for mp in active_positions:
        ticker = mp["ticker"]
        if ticker in seen_tickers:
            issues.append(f"DUPLICATE_TICKER:{ticker}")
            continue
        seen_tickers.add(ticker)

        quantity = float(mp["quantity"])
        avg_cost = float(mp["avg_cost"])

        # Fetch price (currency-aware)
        market_price, is_stale = _fetch_price(ticker, mp.get("currency", "CAD"), usdcad)
        if is_stale:
            issues.append(f"STALE_PRICE:{ticker}")

        pos = build_position(
            ticker               = ticker,
            quantity             = quantity,
            avg_cost             = avg_cost,
            market_price         = market_price,
            realized_pnl         = float(mp.get("realized_pnl") or 0.0),
            total_portfolio_value = total_market_est,
            is_stale             = is_stale,
            price_fetched_at     = price_fetched_at,
            reconciled_at        = now,
            source               = "manual",
        )
        canonical.append(pos)

    # Recompute concentration with accurate market values
    total_with_cash = manual_cash + sum(p["market_value"] for p in canonical)
    for pos in canonical:
        pos["concentration_pct"] = (
            round(pos["market_value"] / total_with_cash * 100, 2)
            if total_with_cash > 0 else 0.0
        )

    # 6. Upsert canonical portfolio_positions
    _upsert_positions(canonical)

    # 7. Take immutable snapshot
    snap = take_snapshot(trigger="manual_reconcile")

    # 8. Aggregates
    aggregates              = compute_aggregates(canonical, manual_cash)
    aggregates["reconciled_at"] = now

    return {
        "status":         "OK",
        "run_id":         run_id,
        "positions":      canonical,
        "aggregates":     aggregates,
        "issues":         issues,
        "position_count": len(canonical),
        "reconciled_at":  now,
        "snapshot_id":    snap.get("snapshot_id"),
        "account_settings": account,
    }


def _fetch_price(ticker: str, currency: str, usdcad: float) -> tuple:
    """
    Fetch market price in CAD using the position's declared currency.
    Returns (price_cad, is_stale).  Never raises.
    """
    try:
        from market_data import get_ticker_data
        data = get_ticker_data(ticker)
        if data is None:
            return None, True
        price_native = float(data["price"])
        is_usd       = (currency == "USD")
        price_cad    = round(price_native * usdcad, 2) if is_usd else price_native
        return price_cad, False
    except Exception:
        log.warning("manual_portfolio: price fetch failed for %s", ticker, exc_info=True)
        return None, True


def _sync_cash(cash: float) -> None:
    """Update the canonical cash table from manual account settings.  Never raises."""
    try:
        from database import get_connection
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE cash SET available_cash = ? WHERE id = 1", (cash,)
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.warning("manual_portfolio: cash sync failed", exc_info=True)


def _write_recon_log(
    run_id:          str,
    trigger:         str,
    positions_found: int,
    issues:          list,
    duration_ms:     float,
    status:          str,
    now:             str,
) -> None:
    """Write to portfolio_reconciliation_log.  Never raises."""
    try:
        from portfolio_reconciliation import _log_run
        _log_run(run_id, trigger, positions_found, issues, duration_ms, status, now)
    except Exception:
        log.warning("manual_portfolio: recon log write failed", exc_info=True)


# ── Audit log read ────────────────────────────────────────────────────────────

def get_audit_log(limit: int = 50) -> list:
    """
    Return audit log entries ordered by performed_at DESC.  Never raises.
    """
    _ensure_tables()
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM manual_portfolio_audit_log "
                "ORDER BY performed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("manual_portfolio: get_audit_log failed", exc_info=True)
        return []
