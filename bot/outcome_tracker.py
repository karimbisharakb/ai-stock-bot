"""
Outcome tracker for Predator CONVICTION alerts.

Records what alerts actually do after firing, enabling future win-rate analysis
and confidence calibration from real outcomes.

Lifecycle of a tracked outcome:
  PENDING  → inserted when a CONVICTION alert fires
  COMPLETE → 20 trading-day price history is available; all returns computed
  STALE    → alert is old but price data is unavailable or insufficient

Design constraints:
  - UNIQUE(ticker, alert_time) makes insert_pending_outcome() idempotent
  - evaluate_pending_outcomes() processes only PENDING rows and is safe to
    call repeatedly under duplicate scheduler runs
  - Missing price data results in partial updates, not crashes
  - Batch size is capped to avoid network storms
"""
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import yfinance as yf

from database import get_connection

log = logging.getLogger(__name__)

# ── Status constants ──────────────────────────────────────────────────────────
PENDING  = "PENDING"
COMPLETE = "COMPLETE"
STALE    = "STALE"

# ── Configuration ─────────────────────────────────────────────────────────────
STALE_THRESHOLD_DAYS = 35  # mark STALE when pending older than this
MAX_EVAL_PER_RUN     = 20  # cap rows evaluated per job run


# ── Internal helpers ──────────────────────────────────────────────────────────

def _signal_summary(signals: dict) -> str:
    """Return compact JSON of active signals (score > 0) and their scores."""
    active = {
        name: int(sig.get("score") or 0)
        for name, sig in signals.items()
        if (sig.get("score") or 0) > 0
    }
    return json.dumps(active, separators=(",", ":"))


def _get_pending_outcomes() -> list:
    """Return all PENDING rows ordered by alert_time ascending (oldest first)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM predator_outcomes WHERE outcome_status = ? ORDER BY alert_time",
            (PENDING,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _compute_returns(
    ticker:         str,
    entry_price:    float,
    alert_time_iso: str,
) -> dict:
    """
    Fetch up to 35 calendar days of closes after alert_time and compute:
      return_1d, return_5d, return_20d  — indexed trading-day closes
      max_gain_pct, max_drawdown_pct    — extremes over the whole window

    Returns a partial dict for however many trading days are available.
    Returns {} on any data failure.

    Percentage formula: (close_n / entry_price - 1) × 100, rounded to 2 dp.
    """
    if not entry_price or entry_price <= 0:
        log.warning("outcome_tracker: entry_price ≤ 0 for %s — skipping", ticker)
        return {}
    try:
        alert_dt = datetime.fromisoformat(alert_time_iso)
        start    = alert_dt.date()
        end      = start + timedelta(days=35)

        hist = yf.Ticker(ticker).history(start=str(start), end=str(end))
        if hist.empty:
            log.debug("outcome_tracker: no history for %s from %s", ticker, start)
            return {}

        closes = hist["Close"].dropna()
        n      = len(closes)
        if n == 0:
            return {}

        def _pct(px: float) -> float:
            return round((float(px) / entry_price - 1) * 100, 2)

        result: dict = {}
        if n >= 1:
            result["return_1d"]  = _pct(closes.iloc[0])
        if n >= 5:
            result["return_5d"]  = _pct(closes.iloc[4])
        if n >= 20:
            result["return_20d"] = _pct(closes.iloc[19])

        result["max_gain_pct"]     = _pct(closes.max())
        result["max_drawdown_pct"] = _pct(closes.min())

        return result

    except Exception as exc:
        log.warning("outcome_tracker: _compute_returns failed for %s: %s", ticker, exc)
        return {}


def _update_outcome(row_id: int, status: str, fields: dict) -> None:
    """
    Update a predator_outcomes row.

    COALESCE(new, existing) means: use the newly-computed value if present,
    otherwise keep whatever is already in the column.  This lets partial
    evaluation runs accumulate results without overwriting earlier values.
    """
    now = datetime.now().isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE predator_outcomes
            SET outcome_status   = ?,
                return_1d        = COALESCE(?, return_1d),
                return_5d        = COALESCE(?, return_5d),
                return_20d       = COALESCE(?, return_20d),
                max_gain_pct     = COALESCE(?, max_gain_pct),
                max_drawdown_pct = COALESCE(?, max_drawdown_pct),
                evaluated_at     = ?
            WHERE id = ?
            """,
            (
                status,
                fields.get("return_1d"),
                fields.get("return_5d"),
                fields.get("return_20d"),
                fields.get("max_gain_pct"),
                fields.get("max_drawdown_pct"),
                now,
                row_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _evaluate_row(row: dict) -> None:
    """
    Evaluate one PENDING outcome row.

    Decision:
      - return_20d available                  → COMPLETE
      - no 20d data AND alert > STALE days    → STALE
      - otherwise                             → stay PENDING, persist partial values
    """
    ticker      = row["ticker"]
    entry_price = row["entry_price"]
    alert_time  = row["alert_time"]
    row_id      = row["id"]

    try:
        alert_dt = datetime.fromisoformat(alert_time)
    except ValueError:
        log.warning(
            "outcome_tracker: unparseable alert_time=%r for id=%d — marking STALE",
            alert_time, row_id,
        )
        _update_outcome(row_id, STALE, {})
        return

    days_old = (datetime.now() - alert_dt).days
    returns  = _compute_returns(ticker, entry_price, alert_time)

    if returns.get("return_20d") is not None:
        _update_outcome(row_id, COMPLETE, returns)
        log.info(
            "outcome_tracker: id=%d %s COMPLETE | 1d=%s 5d=%s 20d=%.1f%%",
            row_id, ticker,
            f"{returns['return_1d']:.1f}%" if "return_1d" in returns else "n/a",
            f"{returns['return_5d']:.1f}%" if "return_5d" in returns else "n/a",
            returns["return_20d"],
        )
    elif days_old > STALE_THRESHOLD_DAYS:
        _update_outcome(row_id, STALE, returns)
        log.info(
            "outcome_tracker: id=%d %s STALE (%d days old — no 20d data)",
            row_id, ticker, days_old,
        )
    else:
        _update_outcome(row_id, PENDING, returns)
        log.info(
            "outcome_tracker: id=%d %s PENDING (%dd old) | 1d=%s 5d=%s",
            row_id, ticker, days_old,
            f"{returns['return_1d']:.1f}%" if "return_1d" in returns else "n/a",
            f"{returns['return_5d']:.1f}%" if "return_5d" in returns else "n/a",
        )


# ── Public API ────────────────────────────────────────────────────────────────

def insert_pending_outcome(
    ticker:         str,
    alert_time:     str,
    entry_price:    Optional[float],
    confidence_pct: Optional[float],
    regime:         Optional[str],
    tier:           str,
    signals:        dict,
) -> None:
    """
    Insert a PENDING outcome row when a CONVICTION alert fires.

    INSERT OR IGNORE on the UNIQUE(ticker, alert_time) index makes this
    idempotent — a duplicate call for the same alert is silently skipped.
    """
    summary = _signal_summary(signals)
    conn    = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO predator_outcomes
                (ticker, alert_time, entry_price, confidence_pct,
                 regime, tier, signal_summary, outcome_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticker, alert_time, entry_price, confidence_pct,
             regime, tier, summary, PENDING),
        )
        conn.commit()
        log.info(
            "outcome_tracker: PENDING outcome inserted for %s "
            "(conf=%.1f%% regime=%s tier=%s)",
            ticker, confidence_pct or 0.0, regime, tier,
        )
    finally:
        conn.close()


def evaluate_pending_outcomes() -> None:
    """
    Evaluate all PENDING outcome rows by fetching forward price data.

    Safe to call repeatedly:
      - Only processes PENDING rows; COMPLETE and STALE rows are skipped.
      - Each row is updated atomically (status change + values in one UPDATE).
      - Capped at MAX_EVAL_PER_RUN rows per call to avoid network storms.
      - 0.5 s sleep between tickers to stay within yfinance rate limits.
      - Per-row exceptions are logged and swallowed; other rows still proceed.
    """
    pending = _get_pending_outcomes()
    if not pending:
        log.debug("outcome_tracker: no pending outcomes to evaluate")
        return

    batch = pending[:MAX_EVAL_PER_RUN]
    log.info(
        "outcome_tracker: evaluating %d/%d pending rows",
        len(batch), len(pending),
    )

    ok = 0
    for row in batch:
        try:
            _evaluate_row(row)
            ok += 1
        except Exception:
            log.exception(
                "outcome_tracker: unhandled error for id=%d %s",
                row["id"], row["ticker"],
            )
        time.sleep(0.5)

    log.info(
        "outcome_tracker: evaluation complete — %d/%d rows processed",
        ok, len(batch),
    )
