"""
Phase A17 — Historical replay and decision simulator.

Reconstructs past Alpha candidates from alpha_shadow_log and simulates
what the system would have decided given the gate, QC, regime context,
and actual outcomes.

Never executes trades, sends alerts, or mutates live system state.
All replay data is appended to replay_runs / replay_events (immutable).
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
_MAX_ROWS_CAP     = 2000
_DEFAULT_MAX_ROWS = 500

# Outcome classification thresholds
_WINNER_THRESHOLD    = 5.0    # return_5d ≥ 5% → winner territory
_LOSER_THRESHOLD     = -5.0   # return_5d ≤ -5% → loser territory
_ALERT_WIN_THRESHOLD = 3.0    # WOULD_ALERT + return_5d ≥ 3% → early_but_valid
_ALERT_LOSS_THRESH   = -3.0   # WOULD_ALERT + return_5d < -3% → false_positive
_CORRECT_IGNORE_BAND = 2.0    # |return_5d| < 2% with non-alert → correct_ignore
_TOO_LATE_MAX_GAIN   = 5.0    # max_gain ≥ 5% but return_5d < 1% → peaked and gone

SIMULATED_DECISIONS = frozenset({
    "WOULD_IGNORE", "WOULD_MONITOR", "WOULD_PREPARE",
    "WOULD_ALERT", "WOULD_BLOCK", "WOULD_REJECT",
})

OUTCOME_CLASSIFICATIONS = frozenset({
    "correct_ignore", "missed_winner", "avoided_loser",
    "false_positive", "early_but_valid", "too_late", "inconclusive",
})


# ── Table setup (safety fallback — migration v20 is primary) ──────────────────

def _ensure_tables() -> None:
    from database import get_connection
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS replay_runs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id       TEXT    NOT NULL UNIQUE,
                created_at   TEXT    NOT NULL,
                start_date   TEXT    NOT NULL,
                end_date     TEXT    NOT NULL,
                ticker_filter     TEXT,
                source_filter     TEXT,
                setup_type_filter TEXT,
                max_rows     INTEGER NOT NULL DEFAULT 500,
                status       TEXT    NOT NULL DEFAULT 'PENDING',
                event_count  INTEGER NOT NULL DEFAULT 0,
                completed_at TEXT,
                summary_json TEXT    NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS replay_events (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id                 TEXT    NOT NULL,
                shadow_log_id          INTEGER,
                ticker                 TEXT    NOT NULL,
                scan_time              TEXT    NOT NULL,
                alpha_score            REAL,
                alpha_tier             TEXT,
                setup_type             TEXT,
                source                 TEXT,
                filter_reason          TEXT,
                readiness_tier         TEXT,
                readiness_score        REAL,
                alert_ready            INTEGER,
                qc_tier                TEXT,
                qc_score               REAL,
                allow_notification     INTEGER,
                regime_overall         TEXT,
                regime_score           REAL,
                regime_captured_at     TEXT,
                simulated_decision     TEXT    NOT NULL,
                outcome_status         TEXT,
                return_5d              REAL,
                return_10d             REAL,
                max_gain               REAL,
                max_drawdown           REAL,
                outcome_classification TEXT,
                created_at             TEXT    NOT NULL
            );
        """)
        conn.commit()
    finally:
        conn.close()


# ── Pure helper functions ─────────────────────────────────────────────────────

def _run_id_from_params(
    start_date:        str,
    end_date:          str,
    ticker_filter:     Optional[list],
    source_filter:     Optional[str],
    setup_type_filter: Optional[str],
    created_at:        str,
) -> str:
    """Deterministic run ID: SHA-256 of all params, prefixed with RPL-."""
    tickers_str = json.dumps(sorted(ticker_filter)) if ticker_filter else "[]"
    raw = (
        f"{start_date}:{end_date}:{tickers_str}"
        f":{source_filter or ''}:{setup_type_filter or ''}:{created_at}"
    )
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16].upper()
    return f"RPL-{digest}"


def classify_simulated_decision(
    gate_result:    dict,
    qc_result:      dict,
    filter_reason:  Optional[str],
) -> str:
    """
    Pure function.  Classify what decision the system would have made.
    Precedence: REJECT > IGNORE > BLOCK > MONITOR > PREPARE > ALERT
    """
    if not gate_result:
        return "WOULD_IGNORE"

    # 1. Reject: filtered or IGNORE alpha tier
    if filter_reason:
        return "WOULD_REJECT"
    alpha_tier = gate_result.get("alpha_tier") or "IGNORE"
    if alpha_tier == "IGNORE":
        return "WOULD_REJECT"

    # 2. Ignore: gate says not ready
    readiness_tier = gate_result.get("readiness_tier") or "NOT_READY"
    if readiness_tier == "NOT_READY":
        return "WOULD_IGNORE"

    # 3. Block: QC blocks or suppresses
    if qc_result:
        qc_tier            = qc_result.get("qc_tier") or ""
        allow_notification = bool(qc_result.get("allow_notification", True))
        if qc_tier == "BLOCK" or not allow_notification:
            return "WOULD_BLOCK"

    # 4. Decision based on readiness tier
    alert_ready = bool(gate_result.get("alert_ready", False))
    if alert_ready:
        return "WOULD_ALERT"
    if readiness_tier == "PRE_ALERT":
        return "WOULD_PREPARE"
    if readiness_tier == "MONITOR":
        return "WOULD_MONITOR"

    return "WOULD_IGNORE"


def classify_outcome(
    simulated_decision: str,
    outcome_row:        Optional[dict],
) -> str:
    """
    Pure function.  Compare simulated decision to actual outcome.
    Returns one of OUTCOME_CLASSIFICATIONS.
    """
    if outcome_row is None:
        return "inconclusive"

    status = outcome_row.get("status") or "PENDING"
    if status != "COMPLETE":
        return "inconclusive"

    return_5d    = outcome_row.get("return_5d")
    max_gain     = outcome_row.get("max_gain")

    if return_5d is None:
        return "inconclusive"

    was_alerted  = simulated_decision == "WOULD_ALERT"
    was_prepared = simulated_decision == "WOULD_PREPARE"
    was_passive  = simulated_decision in (
        "WOULD_IGNORE", "WOULD_REJECT", "WOULD_MONITOR", "WOULD_BLOCK"
    )

    if was_alerted:
        if max_gain is not None and max_gain >= _TOO_LATE_MAX_GAIN and return_5d < 1.0:
            return "too_late"
        if return_5d >= _ALERT_WIN_THRESHOLD:
            return "early_but_valid"
        if return_5d < _ALERT_LOSS_THRESH:
            return "false_positive"
        return "inconclusive"

    if was_prepared:
        if return_5d >= _ALERT_WIN_THRESHOLD:
            return "early_but_valid"
        if return_5d <= _LOSER_THRESHOLD:
            return "avoided_loser"
        return "inconclusive"

    if was_passive:
        if return_5d >= _WINNER_THRESHOLD:
            return "missed_winner"
        if return_5d <= _LOSER_THRESHOLD:
            return "avoided_loser"
        if abs(return_5d) < _CORRECT_IGNORE_BAND:
            return "correct_ignore"
        return "inconclusive"

    return "inconclusive"


def _compute_summary(
    events: list,
    start_date: str,
    end_date: str,
) -> dict:
    """Pure function.  Aggregate events into a summary report."""
    n        = len(events)
    alerted  = [e for e in events if e.get("simulated_decision") == "WOULD_ALERT"]

    # Decision breakdown
    decision_counts: dict = {}
    for dec in SIMULATED_DECISIONS:
        decision_counts[dec] = sum(1 for e in events if e.get("simulated_decision") == dec)

    # Outcome breakdown
    outcome_counts: dict = {}
    for oc in OUTCOME_CLASSIFICATIONS:
        outcome_counts[oc] = sum(1 for e in events if e.get("outcome_classification") == oc)

    # Dimensional breakdowns
    regime_counts: dict = {}
    setup_counts:  dict = {}
    source_counts: dict = {}
    for e in events:
        r = e.get("regime_overall") or "UNKNOWN"
        regime_counts[r] = regime_counts.get(r, 0) + 1
        s = e.get("setup_type") or "UNKNOWN"
        setup_counts[s] = setup_counts.get(s, 0) + 1
        src = e.get("source") or "UNKNOWN"
        source_counts[src] = source_counts.get(src, 0) + 1

    # Best / worst simulated alerts
    alerted_with_returns = [e for e in alerted if e.get("return_5d") is not None]
    best_opps = sorted(alerted_with_returns, key=lambda e: e.get("return_5d", 0), reverse=True)[:5]
    worst_alerts = sorted(alerted_with_returns, key=lambda e: e.get("return_5d", 0))[:5]

    return {
        "replay_period":           {"start_date": start_date, "end_date": end_date},
        "event_count":             n,
        "simulated_alert_count":   decision_counts.get("WOULD_ALERT", 0),
        "simulated_monitor_count": decision_counts.get("WOULD_MONITOR", 0),
        "simulated_prepare_count": decision_counts.get("WOULD_PREPARE", 0),
        "simulated_block_count":   decision_counts.get("WOULD_BLOCK", 0),
        "simulated_ignore_count":  decision_counts.get("WOULD_IGNORE", 0),
        "simulated_reject_count":  decision_counts.get("WOULD_REJECT", 0),
        "missed_winners":          outcome_counts.get("missed_winner", 0),
        "avoided_losers":          outcome_counts.get("avoided_loser", 0),
        "false_positives":         outcome_counts.get("false_positive", 0),
        "correct_ignores":         outcome_counts.get("correct_ignore", 0),
        "outcome_breakdown":       outcome_counts,
        "decision_breakdown":      decision_counts,
        "regime_breakdown":        regime_counts,
        "setup_breakdown":         setup_counts,
        "source_breakdown":        source_counts,
        "best_simulated_opportunities": [
            {
                "ticker":     e.get("ticker"),
                "return_5d":  e.get("return_5d"),
                "alpha_tier": e.get("alpha_tier"),
                "scan_time":  e.get("scan_time"),
            }
            for e in best_opps
        ],
        "worst_simulated_alerts": [
            {
                "ticker":     e.get("ticker"),
                "return_5d":  e.get("return_5d"),
                "alpha_tier": e.get("alpha_tier"),
                "scan_time":  e.get("scan_time"),
            }
            for e in worst_alerts
        ],
    }


# ── DB reads ──────────────────────────────────────────────────────────────────

def _fetch_shadow_rows(
    start_date:        str,
    end_date:          str,
    ticker_filter:     Optional[list],
    source_filter:     Optional[str],
    setup_type_filter: Optional[str],
    max_rows:          int,
) -> list:
    """Read alpha_shadow_log rows matching the replay params. Returns list of dicts."""
    from database import get_connection

    where_parts = ["scan_time >= ?", "scan_time <= ?"]
    params: list = [start_date, end_date]

    if ticker_filter:
        placeholders = ",".join("?" * len(ticker_filter))
        where_parts.append(f"ticker IN ({placeholders})")
        params.extend(ticker_filter)

    if setup_type_filter:
        where_parts.append("setup_type = ?")
        params.append(setup_type_filter)

    if source_filter == "predator_shadow":
        where_parts.append("predator_tier IS NOT NULL")
    elif source_filter == "alpha_universe":
        where_parts.append("predator_tier IS NULL")

    where_clause = " AND ".join(where_parts)
    params.append(max_rows)

    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT * FROM alpha_shadow_log WHERE {where_clause} "
            f"ORDER BY scan_time DESC LIMIT ?",
            params,
        ).fetchall()
    finally:
        conn.close()

    return [dict(r) for r in rows]


def _find_nearest_regime(scan_time: str) -> Optional[dict]:
    """
    Return the most recent market_regime_snapshots row captured at or before
    scan_time.  Falls back to earliest row if none captured before scan_time.
    Returns None if no snapshots exist.
    """
    from database import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT * FROM market_regime_snapshots
            WHERE captured_at <= ?
            ORDER BY captured_at DESC LIMIT 1
            """,
            (scan_time,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM market_regime_snapshots ORDER BY captured_at ASC LIMIT 1"
            ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    d = dict(row)
    d["warnings"]    = json.loads(d.pop("warnings_json", "[]"))
    d["raw_signals"] = json.loads(d.pop("raw_signals_json", "{}"))
    return d


def _find_nearest_outcome(ticker: str, scan_time: str) -> Optional[dict]:
    """
    Find the alpha_outcomes row for this ticker nearest to scan_time
    (within a 10-minute window).  Returns None if not found.
    """
    from database import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT * FROM alpha_outcomes
            WHERE ticker = ?
              AND ABS(julianday(scan_time) - julianday(?)) * 86400 < 600
            ORDER BY ABS(julianday(scan_time) - julianday(?)) ASC
            LIMIT 1
            """,
            (ticker, scan_time, scan_time),
        ).fetchone()
    finally:
        conn.close()

    return None if row is None else dict(row)


# ── DB writes ─────────────────────────────────────────────────────────────────

def create_replay_run(params: dict) -> dict:
    """
    Validate params and create a PENDING replay_runs row.
    Returns the row as a dict (including run_id).
    Raises ValueError on invalid params.
    """
    _ensure_tables()
    from database import get_connection

    start_date        = str(params.get("start_date", "")).strip()
    end_date          = str(params.get("end_date", "")).strip()
    ticker_filter     = params.get("ticker_filter") or None
    source_filter     = params.get("source_filter") or None
    setup_type_filter = params.get("setup_type_filter") or None
    _raw_max = params.get("max_rows")
    max_rows = min(int(_raw_max) if _raw_max is not None else _DEFAULT_MAX_ROWS, _MAX_ROWS_CAP)

    if not start_date:
        raise ValueError("start_date is required")
    if not end_date:
        raise ValueError("end_date is required")
    if start_date >= end_date:
        raise ValueError("start_date must be before end_date")

    created_at = datetime.now(timezone.utc).isoformat()
    run_id     = _run_id_from_params(
        start_date, end_date, ticker_filter,
        source_filter, setup_type_filter, created_at,
    )

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO replay_runs
                (run_id, created_at, start_date, end_date, ticker_filter,
                 source_filter, setup_type_filter, max_rows, status,
                 event_count, summary_json)
            VALUES (?,?,?,?,?,?,?,?,'PENDING',0,'{}')
            """,
            (
                run_id, created_at, start_date, end_date,
                json.dumps(ticker_filter) if ticker_filter else None,
                source_filter, setup_type_filter, max_rows,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "run_id":             run_id,
        "created_at":         created_at,
        "start_date":         start_date,
        "end_date":           end_date,
        "ticker_filter":      ticker_filter,
        "source_filter":      source_filter,
        "setup_type_filter":  setup_type_filter,
        "max_rows":           max_rows,
        "status":             "PENDING",
        "event_count":        0,
        "summary":            {},
    }


def get_replay_run(run_id: str) -> Optional[dict]:
    """Return a single replay_runs row with summary deserialized, or None."""
    _ensure_tables()
    from database import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM replay_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    d = dict(row)
    d["summary"] = json.loads(d.pop("summary_json", "{}"))
    d["ticker_filter"] = json.loads(d["ticker_filter"]) if d.get("ticker_filter") else None
    return d


def get_replay_runs(limit: int = 20) -> list:
    """Return recent replay_runs rows, newest first."""
    _ensure_tables()
    from database import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM replay_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["summary"] = json.loads(d.pop("summary_json", "{}"))
        d["ticker_filter"] = json.loads(d["ticker_filter"]) if d.get("ticker_filter") else None
        result.append(d)
    return result


def get_replay_events(run_id: str, limit: int = 200) -> list:
    """Return events for a run, up to limit, insertion order."""
    _ensure_tables()
    from database import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM replay_events WHERE run_id = ? ORDER BY id ASC LIMIT ?",
            (run_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _save_events(events: list) -> None:
    """Bulk-insert replay_events rows."""
    from database import get_connection
    if not events:
        return
    conn = get_connection()
    try:
        conn.executemany(
            """
            INSERT INTO replay_events
                (run_id, shadow_log_id, ticker, scan_time, alpha_score, alpha_tier,
                 setup_type, source, filter_reason, readiness_tier, readiness_score,
                 alert_ready, qc_tier, qc_score, allow_notification,
                 regime_overall, regime_score, regime_captured_at,
                 simulated_decision, outcome_status, return_5d, return_10d,
                 max_gain, max_drawdown, outcome_classification, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    e["run_id"], e.get("shadow_log_id"), e["ticker"], e["scan_time"],
                    e.get("alpha_score"), e.get("alpha_tier"), e.get("setup_type"),
                    e.get("source"), e.get("filter_reason"),
                    e.get("readiness_tier"), e.get("readiness_score"),
                    int(bool(e.get("alert_ready", False))),
                    e.get("qc_tier"), e.get("qc_score"),
                    int(bool(e.get("allow_notification", False))),
                    e.get("regime_overall"), e.get("regime_score"),
                    e.get("regime_captured_at"),
                    e["simulated_decision"],
                    e.get("outcome_status"), e.get("return_5d"), e.get("return_10d"),
                    e.get("max_gain"), e.get("max_drawdown"),
                    e.get("outcome_classification"),
                    e["created_at"],
                )
                for e in events
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _update_run_complete(run_id: str, event_count: int, summary: dict) -> None:
    """Mark a replay run as COMPLETE and store its summary."""
    from database import get_connection
    completed_at = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE replay_runs
            SET status='COMPLETE', event_count=?, completed_at=?, summary_json=?
            WHERE run_id=?
            """,
            (event_count, completed_at, json.dumps(summary), run_id),
        )
        conn.commit()
    finally:
        conn.close()


def _update_run_failed(run_id: str, reason: str) -> None:
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE replay_runs SET status='FAILED', summary_json=? WHERE run_id=?",
            (json.dumps({"error": reason}), run_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── Core replay engine ────────────────────────────────────────────────────────

def _build_replay_event(shadow_row: dict, run_id: str) -> dict:
    """
    Reconstruct a single replay event from an alpha_shadow_log row.
    Calls gate + QC (pure functions), looks up regime + outcome.
    Returns a dict ready for _save_events.
    """
    from alpha_alert_gate import score_readiness
    from alpha_notification_qc import evaluate_notification_quality

    ticker        = shadow_row.get("ticker", "")
    scan_time     = shadow_row.get("scan_time", "")
    filter_reason = shadow_row.get("filter_reason")
    predator_tier = shadow_row.get("predator_tier")
    source        = "predator_shadow" if predator_tier else "alpha_universe"
    created_at    = datetime.now(timezone.utc).isoformat()

    # Candidate dict for gate
    candidate = {
        "ticker":                 ticker,
        "scan_time":              scan_time,
        "alpha_score":            shadow_row.get("alpha_score"),
        "alpha_tier":             shadow_row.get("alpha_tier"),
        "setup_type":             shadow_row.get("setup_type"),
        "component_scores_json":  shadow_row.get("component_scores_json"),
        "predator_tier":          predator_tier,
    }

    # Gate (pure — never raises)
    gate_result: dict = {}
    try:
        if not filter_reason:
            gate_result = score_readiness(candidate)
    except Exception as exc:
        log.debug("replay gate error for %s @ %s: %s", ticker, scan_time, exc)

    # QC (pure — never raises)
    qc_result: dict = {}
    try:
        if gate_result and not filter_reason:
            qc_candidate = {
                "ticker":          ticker,
                "readiness_tier":  gate_result.get("readiness_tier"),
                "readiness_score": gate_result.get("readiness_score"),
                "alpha_score":     candidate["alpha_score"],
                "alpha_tier":      candidate["alpha_tier"],
                "setup_type":      candidate["setup_type"],
            }
            qc_result = evaluate_notification_quality(qc_candidate, [], {})
    except Exception as exc:
        log.debug("replay QC error for %s @ %s: %s", ticker, scan_time, exc)

    # Nearest regime snapshot
    regime = _find_nearest_regime(scan_time)

    # Nearest outcome
    outcome = _find_nearest_outcome(ticker, scan_time)

    # Decision + outcome classification
    decision = classify_simulated_decision(gate_result, qc_result, filter_reason)
    oc       = classify_outcome(decision, outcome)

    return {
        "run_id":               run_id,
        "shadow_log_id":        shadow_row.get("id"),
        "ticker":               ticker,
        "scan_time":            scan_time,
        "alpha_score":          shadow_row.get("alpha_score"),
        "alpha_tier":           shadow_row.get("alpha_tier"),
        "setup_type":           shadow_row.get("setup_type"),
        "source":               source,
        "filter_reason":        filter_reason,
        "readiness_tier":       gate_result.get("readiness_tier"),
        "readiness_score":      gate_result.get("readiness_score"),
        "alert_ready":          gate_result.get("alert_ready", False),
        "qc_tier":              qc_result.get("qc_tier"),
        "qc_score":             qc_result.get("qc_score"),
        "allow_notification":   qc_result.get("allow_notification", False),
        "regime_overall":       regime.get("overall_regime") if regime else None,
        "regime_score":         regime.get("regime_score") if regime else None,
        "regime_captured_at":   regime.get("captured_at") if regime else None,
        "simulated_decision":   decision,
        "outcome_status":       outcome.get("status") if outcome else None,
        "return_5d":            outcome.get("return_5d") if outcome else None,
        "return_10d":           outcome.get("return_10d") if outcome else None,
        "max_gain":             outcome.get("max_gain") if outcome else None,
        "max_drawdown":         outcome.get("max_drawdown") if outcome else None,
        "outcome_classification": oc,
        "created_at":           created_at,
    }


def execute_replay(run_id: str) -> dict:
    """
    Execute a replay run by run_id.  Reads the run params, fetches shadow rows,
    builds events, and saves to DB.  Returns the completed run dict.
    """
    run = get_replay_run(run_id)
    if run is None:
        raise ValueError(f"replay run not found: {run_id}")

    try:
        shadow_rows = _fetch_shadow_rows(
            start_date        = run["start_date"],
            end_date          = run["end_date"],
            ticker_filter     = run["ticker_filter"],
            source_filter     = run["source_filter"],
            setup_type_filter = run["setup_type_filter"],
            max_rows          = run["max_rows"],
        )

        events = []
        for row in shadow_rows:
            try:
                event = _build_replay_event(row, run_id)
                events.append(event)
            except Exception as exc:
                log.warning("replay: skipping row id=%s (%s): %s",
                            row.get("id"), row.get("ticker"), exc)

        _save_events(events)

        summary = _compute_summary(events, run["start_date"], run["end_date"])
        _update_run_complete(run_id, len(events), summary)

        log.info(
            "Replay %s complete: %d events | %d alerts | %d missed winners",
            run_id, len(events),
            summary.get("simulated_alert_count", 0),
            summary.get("missed_winners", 0),
        )

        run["status"]      = "COMPLETE"
        run["event_count"] = len(events)
        run["summary"]     = summary
        run["events"]      = events
        return run

    except Exception as exc:
        log.error("Replay %s failed: %s", run_id, exc, exc_info=True)
        _update_run_failed(run_id, str(exc))
        raise


def run_replay(params: dict) -> dict:
    """
    Create a replay run from params, execute it, and return the result.
    Sparse-safe: empty shadow log → 0 events.
    """
    run = create_replay_run(params)
    return execute_replay(run["run_id"])
