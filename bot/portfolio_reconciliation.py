"""
Phase A11 — Portfolio truth and reconciliation layer.

Canonical source of portfolio truth for the app, morning brief, Alpha engine,
and analytics.  Reads only from holdings/transactions/cash tables — no broker
API, no writes to those tables.  Never calls record_buy_trade, reduce_or_remove,
add_or_update_holding, set_cash, or add_cash.

Public API:
  build_position(...)               -> dict   pure, no DB
  check_impossible_states(...)      -> list   pure, no DB
  compute_aggregates(positions, cash) -> dict  pure, no DB
  reconcile_portfolio(trigger)      -> dict   fetches prices, upserts positions
  get_canonical_portfolio()         -> dict   reads positions table (no yfinance)
  get_canonical_positions()         -> list[dict]
  take_snapshot(trigger)            -> dict   immutable snapshot
  get_snapshots(limit)              -> list[dict]
  get_reconciliation_log(limit)     -> list[dict]
  detect_drift()                    -> dict
"""
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

_MIN_QUANTITY = 1e-6


# ── DB DDL ────────────────────────────────────────────────────────────────────

_POSITIONS_DDL = """
CREATE TABLE IF NOT EXISTS portfolio_positions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker             TEXT    NOT NULL UNIQUE,
    quantity           REAL    NOT NULL,
    avg_cost           REAL    NOT NULL,
    market_price       REAL,
    market_value       REAL,
    cost_basis         REAL,
    unrealized_pnl     REAL,
    unrealized_pnl_pct REAL,
    realized_pnl       REAL    NOT NULL DEFAULT 0.0,
    source             TEXT    NOT NULL DEFAULT 'manual',
    is_stale           INTEGER NOT NULL DEFAULT 0,
    concentration_pct  REAL    NOT NULL DEFAULT 0.0,
    price_fetched_at   TEXT,
    reconciled_at      TEXT    NOT NULL
)
"""

_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id           TEXT    NOT NULL UNIQUE,
    trigger               TEXT    NOT NULL DEFAULT 'manual',
    total_market_value    REAL    NOT NULL,
    total_cost_basis      REAL    NOT NULL,
    total_unrealized_pnl  REAL    NOT NULL,
    total_realized_pnl    REAL    NOT NULL,
    cash                  REAL    NOT NULL,
    total_portfolio_value REAL    NOT NULL,
    position_count        INTEGER NOT NULL,
    stale_count           INTEGER NOT NULL DEFAULT 0,
    positions_json        TEXT    NOT NULL,
    taken_at              TEXT    NOT NULL
)
"""

_RECON_LOG_DDL = """
CREATE TABLE IF NOT EXISTS portfolio_reconciliation_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT    NOT NULL UNIQUE,
    trigger         TEXT    NOT NULL DEFAULT 'manual',
    positions_found INTEGER NOT NULL DEFAULT 0,
    issues_found    INTEGER NOT NULL DEFAULT 0,
    issues_json     TEXT    NOT NULL DEFAULT '[]',
    duration_ms     REAL,
    status          TEXT    NOT NULL DEFAULT 'OK',
    reconciled_at   TEXT    NOT NULL
)
"""


def _ensure_tables() -> None:
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(_POSITIONS_DDL)
        conn.execute(_SNAPSHOTS_DDL)
        conn.execute(_RECON_LOG_DDL)
        conn.commit()
    except Exception:
        log.warning("portfolio_reconciliation: table creation failed", exc_info=True)
    finally:
        conn.close()


# ── Pure functions ────────────────────────────────────────────────────────────

def build_position(
    ticker:               str,
    quantity:             float,
    avg_cost:             float,
    market_price:         Optional[float],
    realized_pnl:         float = 0.0,
    total_portfolio_value: float = 0.0,
    is_stale:             bool = False,
    price_fetched_at:     Optional[str] = None,
    reconciled_at:        Optional[str] = None,
    source:               str = "manual",
) -> dict:
    """
    Build a canonical position dict.  Pure function — no DB access, never raises.
    Falls back to avg_cost when market_price is None or non-positive.
    """
    if market_price is None or market_price <= 0:
        effective_price = avg_cost
        is_stale = True
    else:
        effective_price = market_price

    market_value       = round(quantity * effective_price, 2)
    cost_basis         = round(quantity * avg_cost, 2)
    unrealized_pnl     = round(market_value - cost_basis, 2)
    unrealized_pnl_pct = (
        round(unrealized_pnl / cost_basis * 100, 2) if cost_basis != 0 else 0.0
    )
    concentration_pct  = (
        round(market_value / total_portfolio_value * 100, 2)
        if total_portfolio_value > 0
        else 0.0
    )

    return {
        "ticker":             ticker,
        "quantity":           round(quantity, 6),
        "avg_cost":           round(avg_cost, 4),
        "market_price":       round(effective_price, 4),
        "market_value":       market_value,
        "cost_basis":         cost_basis,
        "unrealized_pnl":     unrealized_pnl,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "realized_pnl":       round(realized_pnl, 2),
        "source":             source,
        "is_stale":           is_stale,
        "concentration_pct":  concentration_pct,
        "price_fetched_at":   price_fetched_at,
        "reconciled_at":      reconciled_at or datetime.now().isoformat(),
    }


def check_impossible_states(ticker: str, quantity: float, avg_cost: float) -> list:
    """Return a list of issue strings for impossible states. Pure function."""
    issues = []
    if quantity < -_MIN_QUANTITY:
        issues.append(f"NEGATIVE_QUANTITY:{ticker}:{quantity:.6f}")
    if avg_cost < 0:
        issues.append(f"NEGATIVE_AVG_COST:{ticker}:{avg_cost:.4f}")
    return issues


def compute_aggregates(positions: list, cash: float) -> dict:
    """
    Compute portfolio-level aggregates from a list of canonical positions.
    Pure function — no DB access, never raises.
    """
    total_market_value    = round(sum(p["market_value"]   for p in positions), 2)
    total_cost_basis      = round(sum(p["cost_basis"]     for p in positions), 2)
    total_unrealized_pnl  = round(sum(p["unrealized_pnl"] for p in positions), 2)
    total_realized_pnl    = round(sum(p["realized_pnl"]   for p in positions), 2)
    total_portfolio_value = round(total_market_value + cash, 2)
    stale_count           = sum(1 for p in positions if p["is_stale"])

    return {
        "total_market_value":    total_market_value,
        "total_cost_basis":      total_cost_basis,
        "total_unrealized_pnl":  total_unrealized_pnl,
        "total_realized_pnl":    total_realized_pnl,
        "cash":                  round(cash, 2),
        "total_portfolio_value": total_portfolio_value,
        "position_count":        len(positions),
        "stale_count":           stale_count,
    }


# ── Transaction analysis ──────────────────────────────────────────────────────

def _recompute_from_transactions(ticker: str, conn) -> tuple:
    """
    Single pass over transactions to compute (shares, avg_cost, realized_pnl).
    Uses running weighted-average cost for realized P&L accuracy.
    Never raises — returns (0.0, 0.0, 0.0) on any error.
    """
    try:
        rows = conn.execute(
            "SELECT type, shares, price_cad FROM transactions "
            "WHERE ticker = ? ORDER BY date",
            (ticker,),
        ).fetchall()

        running_shares = 0.0
        running_cost   = 0.0
        realized_pnl   = 0.0

        for r in rows:
            t = r["type"]
            s = float(r["shares"])
            p = float(r["price_cad"])

            if t in ("BUY", "DIVIDEND"):
                running_shares += s
                running_cost   += s * p
            elif t == "SELL" and running_shares > 1e-9:
                avg          = running_cost / running_shares
                sold         = min(s, running_shares)
                realized_pnl += sold * p - sold * avg
                running_cost -= sold * avg
                running_shares -= sold

        total_shares = max(0.0, round(running_shares, 6))
        avg_cost     = round(running_cost / total_shares, 4) if total_shares > 0 else 0.0
        return total_shares, avg_cost, round(realized_pnl, 2)
    except Exception:
        log.warning(
            "portfolio_reconciliation: transaction recompute failed for %s", ticker,
            exc_info=True,
        )
        return 0.0, 0.0, 0.0


# ── Price fetching ────────────────────────────────────────────────────────────

def _fetch_market_price(ticker: str, usdcad: float) -> tuple:
    """
    Fetch current market price in CAD via market_data.get_ticker_data().
    Returns (price_cad, is_stale).  Never raises.
    """
    try:
        from market_data import get_ticker_data
        data = get_ticker_data(ticker)
        if data is None:
            return None, True
        price_native = data["price"]
        is_usd       = not ticker.endswith(".TO")
        price_cad    = round(price_native * usdcad, 2) if is_usd else float(price_native)
        return price_cad, False
    except Exception:
        log.warning(
            "portfolio_reconciliation: price fetch failed for %s", ticker, exc_info=True
        )
        return None, True


# ── DB write helpers ──────────────────────────────────────────────────────────

def _upsert_positions(positions: list) -> None:
    """Write canonical positions to portfolio_positions (INSERT OR REPLACE). Never raises."""
    try:
        from database import get_connection
        conn = get_connection()
        try:
            for p in positions:
                conn.execute(
                    """INSERT OR REPLACE INTO portfolio_positions
                       (ticker, quantity, avg_cost, market_price, market_value,
                        cost_basis, unrealized_pnl, unrealized_pnl_pct, realized_pnl,
                        source, is_stale, concentration_pct, price_fetched_at, reconciled_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        p["ticker"],       p["quantity"],     p["avg_cost"],
                        p["market_price"], p["market_value"], p["cost_basis"],
                        p["unrealized_pnl"], p["unrealized_pnl_pct"], p["realized_pnl"],
                        p["source"],       1 if p["is_stale"] else 0,
                        p["concentration_pct"], p["price_fetched_at"], p["reconciled_at"],
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.warning("portfolio_reconciliation: upsert_positions failed", exc_info=True)


def _log_run(
    run_id: str,
    trigger: str,
    positions_found: int,
    issues: list,
    duration_ms: float,
    status: str,
    now: str,
) -> None:
    """Append reconciliation run to audit log. Never raises."""
    try:
        from database import get_connection
        conn = get_connection()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO portfolio_reconciliation_log
                   (run_id, trigger, positions_found, issues_found,
                    issues_json, duration_ms, status, reconciled_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    run_id, trigger, positions_found, len(issues),
                    json.dumps(issues), duration_ms, status, now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.warning("portfolio_reconciliation: log_run failed", exc_info=True)


# ── Core reconciliation ───────────────────────────────────────────────────────

def reconcile_portfolio(trigger: str = "manual") -> dict:
    """
    Rebuild canonical portfolio positions from holdings + transactions + live prices.

    Steps:
      1. Read holdings table
      2. Recompute shares/avg_cost/realized_pnl from transaction history
      3. Check impossible states (skips offending positions)
      4. Fetch live prices via market_data (marks stale on failure)
      5. Build canonical position dicts
      6. Upsert portfolio_positions table
      7. Log run to portfolio_reconciliation_log

    Never raises — returns status='ERROR' on unhandled failure.
    Never writes to holdings, transactions, or cash.
    """
    _ensure_tables()
    t0     = time.monotonic()
    run_id = str(uuid.uuid4())
    now    = datetime.now().isoformat()
    issues: list = []

    try:
        result      = _reconcile_inner(run_id, now, trigger, issues)
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        result["duration_ms"] = duration_ms
        _log_run(run_id, trigger, result.get("position_count", 0), issues, duration_ms, "OK", now)
        return result
    except Exception as exc:
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        log.error("portfolio_reconciliation: unhandled error: %s", exc, exc_info=True)
        issues.append(f"UNHANDLED_ERROR:{str(exc)[:150]}")
        _log_run(run_id, trigger, 0, issues, duration_ms, "ERROR", now)
        return {
            "status":         "ERROR",
            "run_id":         run_id,
            "positions":      [],
            "aggregates":     {},
            "issues":         issues,
            "position_count": 0,
            "reconciled_at":  now,
        }


def _reconcile_inner(run_id: str, now: str, trigger: str, issues: list) -> dict:
    from database import get_connection
    from strategy import get_usd_cad_rate

    usdcad = get_usd_cad_rate()

    conn = get_connection()
    try:
        holdings_rows = conn.execute(
            "SELECT ticker, shares, avg_cost FROM holdings ORDER BY ticker"
        ).fetchall()
        cash_row = conn.execute(
            "SELECT available_cash FROM cash WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()

    cash = float(cash_row["available_cash"]) if cash_row else 0.0

    # Pass 1 — recompute from transactions, validate
    raw_positions = []
    seen_tickers  = set()

    conn = get_connection()
    try:
        for row in holdings_rows:
            ticker = row["ticker"]

            if ticker in seen_tickers:
                issues.append(f"DUPLICATE_TICKER:{ticker}")
                continue
            seen_tickers.add(ticker)

            txn_shares, txn_avg_cost, realized_pnl = _recompute_from_transactions(
                ticker, conn
            )

            # Prefer transaction-derived values; fall back to holdings row
            quantity = txn_shares  if txn_shares   > _MIN_QUANTITY else float(row["shares"])
            avg_cost = txn_avg_cost if txn_shares   > _MIN_QUANTITY else float(row["avg_cost"])

            state_issues = check_impossible_states(ticker, quantity, avg_cost)
            if state_issues:
                issues.extend(state_issues)
                continue

            raw_positions.append({
                "ticker":       ticker,
                "quantity":     quantity,
                "avg_cost":     avg_cost,
                "realized_pnl": realized_pnl,
            })
    finally:
        conn.close()

    # Pass 2 — fetch live prices
    price_fetched_at   = datetime.now().isoformat()
    positions_with_prices = []
    for raw in raw_positions:
        ticker = raw["ticker"]
        market_price, is_stale = _fetch_market_price(ticker, usdcad)
        if is_stale:
            issues.append(f"STALE_PRICE:{ticker}")
        positions_with_prices.append({
            **raw,
            "market_price":     market_price,
            "is_stale":         is_stale,
            "price_fetched_at": price_fetched_at,
        })

    # Pass 3 — build canonical positions (need total value for concentration)
    total_with_cash = cash + sum(
        (p["market_price"] or p["avg_cost"]) * p["quantity"]
        for p in positions_with_prices
    )

    canonical = [
        build_position(
            ticker               = p["ticker"],
            quantity             = p["quantity"],
            avg_cost             = p["avg_cost"],
            market_price         = p["market_price"],
            realized_pnl         = p["realized_pnl"],
            total_portfolio_value = total_with_cash,
            is_stale             = p["is_stale"],
            price_fetched_at     = p["price_fetched_at"],
            reconciled_at        = now,
            source               = "manual",
        )
        for p in positions_with_prices
    ]

    _upsert_positions(canonical)

    aggregates              = compute_aggregates(canonical, cash)
    aggregates["reconciled_at"] = now

    return {
        "status":         "OK",
        "run_id":         run_id,
        "positions":      canonical,
        "aggregates":     aggregates,
        "issues":         issues,
        "position_count": len(canonical),
        "reconciled_at":  now,
    }


# ── Read operations ───────────────────────────────────────────────────────────

def get_canonical_portfolio() -> dict:
    """
    Return the latest reconciled portfolio state from portfolio_positions.
    Does NOT fetch live prices — call reconcile_portfolio() for fresh prices.
    Never raises.
    """
    _ensure_tables()
    try:
        from database import get_connection
        import portfolio as _p
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM portfolio_positions ORDER BY ticker"
            ).fetchall()
        finally:
            conn.close()

        positions = [dict(r) for r in rows]
        for p in positions:
            p["is_stale"] = bool(p.get("is_stale"))

        cash       = _p.get_cash()
        aggregates = compute_aggregates(positions, cash)
        aggregates["reconciled_at"] = (
            positions[0]["reconciled_at"] if positions else datetime.now().isoformat()
        )

        return {"positions": positions, "aggregates": aggregates}
    except Exception:
        log.warning("portfolio_reconciliation: get_canonical_portfolio failed", exc_info=True)
        return {"positions": [], "aggregates": {}}


def get_canonical_positions() -> list:
    """Return just the canonical positions list. Never raises."""
    return get_canonical_portfolio().get("positions", [])


# ── Snapshot engine (append-only) ────────────────────────────────────────────

def take_snapshot(trigger: str = "manual") -> dict:
    """
    Capture an immutable snapshot of the current canonical portfolio state.
    Reads from portfolio_positions (does NOT trigger a fresh reconcile).
    Snapshot rows are never updated or deleted within this module.
    Never raises.
    """
    _ensure_tables()
    try:
        state     = get_canonical_portfolio()
        positions = state["positions"]
        agg       = state["aggregates"]

        import portfolio as _p
        cash = _p.get_cash()

        snapshot_id = str(uuid.uuid4())
        now         = datetime.now().isoformat()

        from database import get_connection
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO portfolio_snapshots
                   (snapshot_id, trigger, total_market_value, total_cost_basis,
                    total_unrealized_pnl, total_realized_pnl, cash,
                    total_portfolio_value, position_count, stale_count,
                    positions_json, taken_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    snapshot_id,
                    trigger,
                    agg.get("total_market_value",    0.0),
                    agg.get("total_cost_basis",       0.0),
                    agg.get("total_unrealized_pnl",   0.0),
                    agg.get("total_realized_pnl",     0.0),
                    cash,
                    agg.get("total_portfolio_value",  cash),
                    len(positions),
                    sum(1 for p in positions if p.get("is_stale")),
                    json.dumps(positions),
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "snapshot_id":          snapshot_id,
            "trigger":              trigger,
            "total_market_value":   agg.get("total_market_value",   0.0),
            "total_cost_basis":     agg.get("total_cost_basis",      0.0),
            "total_unrealized_pnl": agg.get("total_unrealized_pnl",  0.0),
            "total_realized_pnl":   agg.get("total_realized_pnl",    0.0),
            "cash":                 cash,
            "total_portfolio_value": agg.get("total_portfolio_value", cash),
            "position_count":       len(positions),
            "stale_count":          sum(1 for p in positions if p.get("is_stale")),
            "taken_at":             now,
        }
    except Exception:
        log.warning("portfolio_reconciliation: take_snapshot failed", exc_info=True)
        return {}


def get_snapshots(limit: int = 20) -> list:
    """Return recent snapshots ordered by taken_at DESC. Never raises."""
    _ensure_tables()
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT snapshot_id, trigger, total_market_value, total_cost_basis, "
                "total_unrealized_pnl, total_realized_pnl, cash, total_portfolio_value, "
                "position_count, stale_count, taken_at "
                "FROM portfolio_snapshots ORDER BY taken_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("portfolio_reconciliation: get_snapshots failed", exc_info=True)
        return []


def get_reconciliation_log(limit: int = 50) -> list:
    """Return reconciliation run history ordered by reconciled_at DESC. Never raises."""
    _ensure_tables()
    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM portfolio_reconciliation_log "
                "ORDER BY reconciled_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("portfolio_reconciliation: get_reconciliation_log failed", exc_info=True)
        return []


# ── Drift detection ───────────────────────────────────────────────────────────

def detect_drift() -> dict:
    """
    Compare current holdings against portfolio_positions table.

    Returns:
      has_drift                — True if any check found a discrepancy
      missing_from_canonical   — tickers in holdings but not in portfolio_positions
      extra_in_canonical       — tickers in portfolio_positions but not in holdings
      impossible_states        — negative quantity/avg_cost found in positions table
      value_anomalies          — market_value / cost_basis > 3× or < 0.1×
      stale_positions          — tickers with is_stale=True in positions table
      checked_at               — ISO timestamp

    Never raises.
    """
    _ensure_tables()
    try:
        from database import get_connection
        conn = get_connection()
        try:
            holdings_rows  = conn.execute("SELECT ticker FROM holdings").fetchall()
            position_rows  = conn.execute(
                "SELECT ticker, quantity, avg_cost, market_value, cost_basis, is_stale "
                "FROM portfolio_positions"
            ).fetchall()
        finally:
            conn.close()

        holdings_tickers  = {r["ticker"] for r in holdings_rows}
        canonical_tickers = {r["ticker"] for r in position_rows}

        missing_from_canonical = sorted(holdings_tickers - canonical_tickers)
        extra_in_canonical     = sorted(canonical_tickers - holdings_tickers)
        impossible_states      = []
        value_anomalies        = []
        stale_positions        = []

        for r in position_rows:
            ticker = r["ticker"]
            qty    = r["quantity"]   or 0.0
            cost   = r["avg_cost"]   or 0.0
            mv     = r["market_value"] or 0.0
            cb     = r["cost_basis"]  or 0.0

            if qty < -_MIN_QUANTITY:
                impossible_states.append(f"NEGATIVE_QUANTITY:{ticker}")
            if cost < 0:
                impossible_states.append(f"NEGATIVE_AVG_COST:{ticker}")

            if cb > 0:
                ratio = mv / cb
                if ratio > 3.0:
                    value_anomalies.append(f"VALUE_3X_COST:{ticker}:{ratio:.1f}x")
                elif ratio < 0.1:
                    value_anomalies.append(f"VALUE_BELOW_10PCT:{ticker}:{ratio:.2f}x")

            if r["is_stale"]:
                stale_positions.append(ticker)

        has_drift = bool(
            missing_from_canonical or extra_in_canonical
            or impossible_states or value_anomalies
        )

        return {
            "has_drift":              has_drift,
            "missing_from_canonical": missing_from_canonical,
            "extra_in_canonical":     extra_in_canonical,
            "impossible_states":      impossible_states,
            "value_anomalies":        value_anomalies,
            "stale_positions":        stale_positions,
            "checked_at":             datetime.now().isoformat(),
        }
    except Exception:
        log.warning("portfolio_reconciliation: detect_drift failed", exc_info=True)
        return {
            "has_drift":  False,
            "error":      "drift_check_failed",
            "checked_at": datetime.now().isoformat(),
        }
