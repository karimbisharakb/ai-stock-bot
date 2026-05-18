"""
Phase A5 — Alpha outcome tracking and learning dataset.

Observation-only: records price outcomes for alpha candidates above WATCH,
computes returns over 1/3/5/10/20 trading-day windows, and derives learning
analytics from completed rows.  Never sends alerts.  Never modifies scoring.
"""
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

_OUTCOME_WINDOWS = (1, 3, 5, 10, 20)  # trading days
_STALE_AFTER_DAYS = 35                 # mark PENDING rows older than this as STALE
_ELIGIBLE_TIERS = frozenset({"STRONG_WATCH", "HIGH_CONVICTION", "RARE_SETUP"})


# ── Table management ──────────────────────────────────────────────────────────

def ensure_outcomes_table():
    """Create alpha_outcomes table if it does not exist (idempotent, safe to call repeatedly)."""
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alpha_outcomes (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker                TEXT    NOT NULL,
                scan_time             TEXT    NOT NULL,
                alpha_score           REAL,
                alpha_tier            TEXT,
                setup_type            TEXT,
                source                TEXT,
                component_scores_json TEXT,
                price_at_scan         REAL,
                price_1d              REAL,
                price_3d              REAL,
                price_5d              REAL,
                price_10d             REAL,
                price_20d             REAL,
                return_1d             REAL,
                return_3d             REAL,
                return_5d             REAL,
                return_10d            REAL,
                return_20d            REAL,
                max_gain              REAL,
                max_drawdown          REAL,
                status                TEXT NOT NULL DEFAULT 'PENDING',
                created_at            TEXT NOT NULL,
                updated_at            TEXT,
                UNIQUE(ticker, scan_time)
            )
            """
        )
        conn.commit()
    except Exception:
        log.warning("alpha_outcomes: table creation failed", exc_info=True)
    finally:
        conn.close()


# ── Write operations ──────────────────────────────────────────────────────────

def insert_pending_outcomes(candidates: list) -> int:
    """Idempotent insert of PENDING rows for candidates above WATCH.

    Args:
        candidates: list of dicts from alpha_shadow_log (needs ticker, scan_time,
                    alpha_score, alpha_tier).
    Returns:
        Count of newly inserted rows (duplicates silently skipped).
    """
    ensure_outcomes_table()

    eligible = [
        c for c in candidates
        if c.get("alpha_tier") in _ELIGIBLE_TIERS and c.get("alpha_score") is not None
    ]
    if not eligible:
        log.info("alpha_outcomes: no eligible candidates for insert (need STRONG_WATCH or better)")
        return 0

    from database import get_connection
    now = datetime.now().isoformat()
    inserted = 0
    conn = get_connection()
    try:
        for c in eligible:
            source = "predator_shadow" if c.get("predator_tier") else "alpha_universe"
            price  = _fetch_current_price(c["ticker"])
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO alpha_outcomes
                        (ticker, scan_time, alpha_score, alpha_tier, setup_type, source,
                         component_scores_json, price_at_scan, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
                    """,
                    (
                        c["ticker"], c["scan_time"], c.get("alpha_score"),
                        c.get("alpha_tier"), c.get("setup_type"), source,
                        c.get("component_scores_json"), price, now,
                    ),
                )
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    inserted += 1
            except Exception:
                log.warning("alpha_outcomes: insert failed for %s", c["ticker"], exc_info=True)
        conn.commit()
        log.info("alpha_outcomes: inserted %d / %d eligible rows", inserted, len(eligible))
    except Exception:
        log.warning("alpha_outcomes: batch insert failed", exc_info=True)
    finally:
        conn.close()

    return inserted


def update_outcome_prices() -> dict:
    """Fill historical prices and compute returns for all PENDING outcomes.

    Runs daily.  Marks rows COMPLETE when all 5 windows are filled.
    Marks rows STALE when scan_time is older than _STALE_AFTER_DAYS.

    Returns:
        {"updated": int, "completed": int, "staled": int}
    """
    ensure_outcomes_table()

    from database import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM alpha_outcomes WHERE status = 'PENDING'"
        ).fetchall()
        rows = [dict(r) for r in rows]
    except Exception:
        log.warning("alpha_outcomes: query failed in update_outcome_prices", exc_info=True)
        return {"updated": 0, "completed": 0, "staled": 0}
    finally:
        conn.close()

    if not rows:
        log.info("alpha_outcomes: no PENDING rows to update")
        return {"updated": 0, "completed": 0, "staled": 0}

    stale_cutoff = datetime.now() - timedelta(days=_STALE_AFTER_DAYS)
    updated = completed = staled = 0

    for row in rows:
        scan_dt = _parse_dt(row.get("scan_time", ""))
        if scan_dt is None:
            continue

        if scan_dt < stale_cutoff:
            _set_status(row["id"], "STALE")
            staled += 1
            log.info("alpha_outcomes: staled id=%d %s scan=%s", row["id"], row["ticker"], row["scan_time"])
            continue

        prices = _fetch_historical_prices(row["ticker"], scan_dt)
        if not prices:
            continue

        p0 = row.get("price_at_scan")
        field_updates: dict = {}

        for window in _OUTCOME_WINDOWS:
            price_col  = f"price_{window}d"
            return_col = f"return_{window}d"
            if row.get(price_col) is None and window in prices:
                p = prices[window]
                field_updates[price_col] = p
                if p0 and p0 > 0:
                    field_updates[return_col] = round((p - p0) / p0, 6)

        # Recompute max_gain / max_drawdown across all available windows
        all_window_prices: dict = {}
        for window in _OUTCOME_WINDOWS:
            p = field_updates.get(f"price_{window}d") or row.get(f"price_{window}d")
            if p is not None:
                all_window_prices[window] = p

        if p0 and p0 > 0 and all_window_prices:
            gains = [(p - p0) / p0 for p in all_window_prices.values()]
            field_updates["max_gain"]     = round(max(gains), 6)
            field_updates["max_drawdown"] = round(min(gains), 6)

        if field_updates:
            _apply_updates(row["id"], field_updates)
            updated += 1

        merged = {**row, **field_updates}
        if _all_windows_filled(merged):
            _set_status(row["id"], "COMPLETE")
            completed += 1

    log.info("alpha_outcomes: price update — updated=%d completed=%d staled=%d",
             updated, completed, staled)
    return {"updated": updated, "completed": completed, "staled": staled}


# ── Read operations ───────────────────────────────────────────────────────────

def get_outcomes(limit: int = 50, status: Optional[str] = None) -> list:
    """Return outcome rows ordered scan_time DESC; optionally filter by status."""
    ensure_outcomes_table()
    from database import get_connection
    conn = get_connection()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM alpha_outcomes WHERE status = ? ORDER BY scan_time DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM alpha_outcomes ORDER BY scan_time DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("alpha_outcomes: get_outcomes query failed", exc_info=True)
        return []
    finally:
        conn.close()


def get_learning_dataset(limit: int = 200) -> list:
    """Return COMPLETE outcome rows left-joined with alpha_shadow_log for analysis."""
    ensure_outcomes_table()
    from database import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT o.*,
                   s.explanation,
                   s.predator_tier  AS shadow_predator_tier,
                   s.predator_score AS shadow_predator_score,
                   s.filter_reason
            FROM alpha_outcomes o
            LEFT JOIN alpha_shadow_log s
                   ON o.ticker = s.ticker AND o.scan_time = s.scan_time
            WHERE o.status = 'COMPLETE'
            ORDER BY o.scan_time DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("alpha_outcomes: get_learning_dataset failed", exc_info=True)
        return []
    finally:
        conn.close()


def compute_learning_analytics() -> dict:
    """Compute effectiveness analytics from COMPLETE outcome rows.

    Returns per-setup, per-tier, and per-source effectiveness with avg 5-day
    return and win rate.  Returns a note instead of crashing when no data exists.
    """
    dataset = get_learning_dataset(limit=500)
    total   = len(dataset)

    if total == 0:
        return {
            "total_complete":      0,
            "setup_effectiveness":  {},
            "tier_effectiveness":   {},
            "source_effectiveness": {},
            "false_positive_rate":  None,
            "note": "No complete outcomes yet — outcomes mature over 20 trading days",
        }

    _empty = lambda: {"count": 0, "sum_5d": 0.0, "positive": 0, "false_pos": 0}
    setup_stats  = defaultdict(_empty)
    tier_stats   = defaultdict(_empty)

    _empty_src = lambda: {"count": 0, "sum_5d": 0.0, "positive": 0}
    source_stats = defaultdict(_empty_src)

    false_positives = 0

    for row in dataset:
        r5d    = row.get("return_5d")
        setup  = row.get("setup_type")  or "UNKNOWN"
        tier   = row.get("alpha_tier")  or "UNKNOWN"
        source = row.get("source")      or "unknown"

        for bucket, key in ((setup_stats, setup), (tier_stats, tier)):
            bucket[key]["count"] += 1
            if r5d is not None:
                bucket[key]["sum_5d"] += r5d
                if r5d > 0:
                    bucket[key]["positive"] += 1
                if r5d < -0.05:
                    bucket[key]["false_pos"] += 1

        source_stats[source]["count"] += 1
        if r5d is not None:
            source_stats[source]["sum_5d"] += r5d
            if r5d > 0:
                source_stats[source]["positive"] += 1

        if r5d is not None and r5d < -0.05:
            false_positives += 1

    def _fmt(d: dict, include_fp: bool = False) -> dict:
        out = {}
        for k, v in d.items():
            n = v["count"]
            entry = {
                "count":         n,
                "avg_return_5d": round(v["sum_5d"] / n, 4) if n else None,
                "win_rate":      round(v["positive"] / n, 3) if n else None,
            }
            if include_fp:
                entry["false_positive_rate"] = round(v["false_pos"] / n, 3) if n else None
            out[k] = entry
        return out

    return {
        "total_complete":      total,
        "setup_effectiveness":  _fmt(setup_stats,  include_fp=True),
        "tier_effectiveness":   _fmt(tier_stats,   include_fp=True),
        "source_effectiveness": _fmt(source_stats),
        "false_positive_rate":  round(false_positives / total, 3) if total else None,
    }


# ── Private helpers ───────────────────────────────────────────────────────────

def _fetch_current_price(ticker: str) -> Optional[float]:
    """Fetch current market price via yfinance; returns None on any failure."""
    try:
        import yfinance as yf
        info  = yf.Ticker(ticker).fast_info
        price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
        return float(price) if price else None
    except Exception:
        return None


def _fetch_historical_prices(ticker: str, scan_dt: datetime) -> dict:
    """Return {window: closing_price} for each trading-day window after scan_dt."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="2mo", auto_adjust=True)
        if hist.empty:
            return {}

        closes = hist["Close"].dropna()
        scan_date = scan_dt.date() if hasattr(scan_dt, "date") else scan_dt

        # Collect (date, price) pairs strictly after scan date, in chronological order
        future = sorted(
            (
                (idx.date() if hasattr(idx, "date") else idx, float(price))
                for idx, price in closes.items()
                if (idx.date() if hasattr(idx, "date") else idx) > scan_date
            ),
            key=lambda x: x[0],
        )

        result: dict = {}
        for window in _OUTCOME_WINDOWS:
            if len(future) >= window:
                result[window] = future[window - 1][1]
        return result
    except Exception:
        log.warning("alpha_outcomes: price history fetch failed for %s", ticker, exc_info=True)
        return {}


def _parse_dt(s: str) -> Optional[datetime]:
    """Parse ISO datetime string; return None on failure."""
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _all_windows_filled(row: dict) -> bool:
    """Return True if all 5 price_Nd columns are non-null."""
    return all(row.get(f"price_{w}d") is not None for w in _OUTCOME_WINDOWS)


def _set_status(outcome_id: int, status: str) -> None:
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE alpha_outcomes SET status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.now().isoformat(), outcome_id),
        )
        conn.commit()
    except Exception:
        log.warning("alpha_outcomes: status update failed for id=%d", outcome_id, exc_info=True)
    finally:
        conn.close()


def _apply_updates(outcome_id: int, updates: dict) -> None:
    """Apply column updates to a single outcome row."""
    if not updates:
        return
    from database import get_connection
    conn = get_connection()
    try:
        set_clause = ", ".join(f"{col} = ?" for col in updates)
        vals = list(updates.values()) + [datetime.now().isoformat(), outcome_id]
        conn.execute(
            f"UPDATE alpha_outcomes SET {set_clause}, updated_at = ? WHERE id = ?",  # noqa: S608
            vals,
        )
        conn.commit()
    except Exception:
        log.warning("alpha_outcomes: column update failed for id=%d", outcome_id, exc_info=True)
    finally:
        conn.close()
