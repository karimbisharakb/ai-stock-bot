"""
Recommendation observer for the Predator adaptive weighting system.
Phase 1G — read-only observation; does NOT apply weights to live scoring.

Persists recommendation snapshots over time, measures signal stability,
detects drift, and prepares the data foundation for a future controlled
weight rollout.

Snapshot lifecycle
------------------
  1. Phase 1F  compute_weight_adjustments() → adjustments dict
  2. save_snapshot()          persists weights + metrics to DB
  3. get_snapshot_history()   retrieves recent snapshots (bounded)
  4. classify_stability()     STABLE / SLOWLY_ADAPTING / UNSTABLE per signal
  5. detect_drift()           flags sudden inter-snapshot jumps
  6. generate_observation_report()  aggregates everything for review

Pure functions (compute_deltas, classify_stability, detect_drift,
generate_observation_report) accept pre-fetched snapshot dicts and have
no I/O, so they can be unit-tested without a live database.

DB-touching functions (save_snapshot, get_latest_snapshot,
get_snapshot_history) are small and isolated at the bottom of the file.
"""
import json
import logging
from datetime import datetime
from typing import Optional

from adaptive_weights import DEFAULT_WEIGHTS
from database import get_connection

log = logging.getLogger(__name__)

# ── Stability labels ──────────────────────────────────────────────────────────
STABILITY_STABLE           = "STABLE"
STABILITY_SLOWLY_ADAPTING  = "SLOWLY_ADAPTING"
STABILITY_UNSTABLE         = "UNSTABLE"

# ── Thresholds ────────────────────────────────────────────────────────────────
# Maximum per-signal |delta| across the full history window before each label.
STABLE_THRESHOLD:    float = 0.05   # ≤ this → STABLE
UNSTABLE_THRESHOLD:  float = 0.20   # > this → UNSTABLE  (else SLOWLY_ADAPTING)

# Single-step delta that triggers a drift warning log.
DRIFT_WARNING_THRESHOLD: float = 0.15

# Hard cap on history queries to prevent runaway DB reads.
MAX_HISTORY_LIMIT: int = 100


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_row(row: dict) -> dict:
    """
    Inflate a raw recommendation_snapshots DB row into a structured dict.

    Output shape:
        {
            "id":            int,
            "snapshot_time": str (ISO),
            "row_count":     int,
            "weights":       {signal: suggested_weight},
            "metrics":       {signal: {n_active, lift, win_rate_active, avg_return_5d}},
        }
    """
    try:
        weights = json.loads(row.get("weights_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        weights = {}
    try:
        metrics = json.loads(row.get("metrics_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        metrics = {}
    return {
        "id":            row.get("id"),
        "snapshot_time": row.get("snapshot_time", ""),
        "row_count":     row.get("row_count", 0),
        "weights":       weights,
        "metrics":       metrics,
    }


# ── Pure analytics ────────────────────────────────────────────────────────────

def compute_deltas(snap_a: dict, snap_b: dict) -> dict:
    """
    Per-signal weight delta between two snapshots.

    snap_b is assumed to be more recent than snap_a.
    delta = snap_b.weight − snap_a.weight

    Returns
    -------
    {
        signal: {
            "delta":       float,          # snap_b − snap_a
            "direction":   "up"|"down"|"flat",
            "weight_from": float,
            "weight_to":   float,
        }
    }
    """
    weights_a = snap_a.get("weights", {})
    weights_b = snap_b.get("weights", {})
    all_sigs  = set(weights_a) | set(weights_b) | set(DEFAULT_WEIGHTS)

    result = {}
    for sig in sorted(all_sigs):
        wa    = weights_a.get(sig, DEFAULT_WEIGHTS.get(sig, 0.0))
        wb    = weights_b.get(sig, DEFAULT_WEIGHTS.get(sig, 0.0))
        delta = round(wb - wa, 4)
        result[sig] = {
            "delta":       delta,
            "direction":   "up" if delta > 0 else ("down" if delta < 0 else "flat"),
            "weight_from": wa,
            "weight_to":   wb,
        }
    return result


def classify_stability(history: list) -> dict:
    """
    Classify each signal as STABLE / SLOWLY_ADAPTING / UNSTABLE.

    Based on the maximum absolute per-signal weight delta across all
    consecutive snapshot pairs in the history window.

    Parameters
    ----------
    history : list of parsed snapshot dicts (any time ordering — sorted internally)

    Returns
    -------
    {
        signal: {
            "label":       STABILITY_* constant,
            "max_delta":   float,    # largest single-step |delta|
            "n_snapshots": int,
        }
    }
    """
    n = len(history)

    # Fewer than 2 snapshots → no movement possible → all STABLE
    if n < 2:
        return {
            sig: {
                "label":       STABILITY_STABLE,
                "max_delta":   0.0,
                "n_snapshots": n,
            }
            for sig in DEFAULT_WEIGHTS
        }

    sorted_h   = sorted(history, key=lambda s: s.get("snapshot_time", ""))
    max_deltas = {sig: 0.0 for sig in DEFAULT_WEIGHTS}

    for i in range(len(sorted_h) - 1):
        step_deltas = compute_deltas(sorted_h[i], sorted_h[i + 1])
        for sig in DEFAULT_WEIGHTS:
            if sig in step_deltas:
                max_deltas[sig] = max(max_deltas[sig], abs(step_deltas[sig]["delta"]))

    result = {}
    for sig, max_d in max_deltas.items():
        if max_d <= STABLE_THRESHOLD:
            label = STABILITY_STABLE
        elif max_d <= UNSTABLE_THRESHOLD:
            label = STABILITY_SLOWLY_ADAPTING
        else:
            label = STABILITY_UNSTABLE
        result[sig] = {
            "label":       label,
            "max_delta":   round(max_d, 4),
            "n_snapshots": n,
        }
    return result


def detect_drift(history: list) -> list:
    """
    Find signals with significant weight changes between consecutive snapshots.

    An event is emitted whenever |delta| > DRIFT_WARNING_THRESHOLD for any
    consecutive pair.  Logs a warning for each event found.

    Parameters
    ----------
    history : list of parsed snapshot dicts (any time ordering — sorted internally)

    Returns
    -------
    List of drift event dicts, sorted by |delta| descending:
        {
            "signal":      str,
            "delta":       float,
            "abs_delta":   float,
            "direction":   "up" | "down",
            "weight_from": float,
            "weight_to":   float,
            "from_time":   str (ISO),
            "to_time":     str (ISO),
            "severity":    "HIGH" | "MEDIUM",
        }
    """
    if len(history) < 2:
        return []

    sorted_h = sorted(history, key=lambda s: s.get("snapshot_time", ""))
    events   = []

    for i in range(len(sorted_h) - 1):
        snap_a = sorted_h[i]
        snap_b = sorted_h[i + 1]
        deltas = compute_deltas(snap_a, snap_b)

        for sig, d in deltas.items():
            abs_d = abs(d["delta"])
            if abs_d > DRIFT_WARNING_THRESHOLD:
                severity = "HIGH" if abs_d > UNSTABLE_THRESHOLD else "MEDIUM"
                event = {
                    "signal":      sig,
                    "delta":       d["delta"],
                    "abs_delta":   abs_d,
                    "direction":   d["direction"],
                    "weight_from": d["weight_from"],
                    "weight_to":   d["weight_to"],
                    "from_time":   snap_a.get("snapshot_time", ""),
                    "to_time":     snap_b.get("snapshot_time", ""),
                    "severity":    severity,
                }
                events.append(event)
                log.warning(
                    "recommendation_observer: DRIFT %s | %s changed %+.3f "
                    "(%.3f → %.3f) from %s → %s [%s]",
                    severity, sig, d["delta"],
                    d["weight_from"], d["weight_to"],
                    snap_a.get("snapshot_time", "?"),
                    snap_b.get("snapshot_time", "?"),
                    severity,
                )

    events.sort(key=lambda e: -e["abs_delta"])
    return events


def generate_observation_report(snapshots: Optional[list] = None, limit: int = 30) -> dict:
    """
    Full observation report: stability + drift + latest snapshot summary.

    If snapshots is None, fetches the most recent `limit` snapshots from DB.
    Pass a list of pre-parsed snapshot dicts to use in-memory data (tests).

    Returns
    -------
    {
        "snapshot_count": int,
        "latest":         dict | None,      # most recent snapshot
        "stability":      { signal: {...} },
        "drift_events":   [ {...} ],
        "summary": {
            "n_stable":            int,
            "n_slowly_adapting":   int,
            "n_unstable":          int,
            "has_drift":           bool,
            "drift_event_count":   int,
        },
    }
    """
    if snapshots is None:
        snapshots = get_snapshot_history(limit=limit)

    n = len(snapshots)
    log.info("recommendation_observer: generating report on %d snapshots", n)

    if n == 0:
        log.info("recommendation_observer: no snapshots found — fresh run")
        return {
            "snapshot_count": 0,
            "latest":         None,
            "stability":      {
                sig: {"label": STABILITY_STABLE, "max_delta": 0.0, "n_snapshots": 0}
                for sig in DEFAULT_WEIGHTS
            },
            "drift_events":   [],
            "summary": {
                "n_stable":          len(DEFAULT_WEIGHTS),
                "n_slowly_adapting": 0,
                "n_unstable":        0,
                "has_drift":         False,
                "drift_event_count": 0,
            },
        }

    sorted_snaps = sorted(snapshots, key=lambda s: s.get("snapshot_time", ""))
    latest       = sorted_snaps[-1]
    stability    = classify_stability(sorted_snaps)
    drift_events = detect_drift(sorted_snaps)

    counts = {
        STABILITY_STABLE:          0,
        STABILITY_SLOWLY_ADAPTING: 0,
        STABILITY_UNSTABLE:        0,
    }
    for entry in stability.values():
        counts[entry["label"]] = counts.get(entry["label"], 0) + 1

    if drift_events:
        log.warning(
            "recommendation_observer: %d drift event(s) detected across %d snapshots",
            len(drift_events), n,
        )
    else:
        log.info(
            "recommendation_observer: no drift detected across %d snapshot(s)", n
        )

    log.info(
        "recommendation_observer: stability — stable=%d slowly_adapting=%d unstable=%d",
        counts[STABILITY_STABLE],
        counts[STABILITY_SLOWLY_ADAPTING],
        counts[STABILITY_UNSTABLE],
    )

    return {
        "snapshot_count": n,
        "latest":         latest,
        "stability":      stability,
        "drift_events":   drift_events,
        "summary": {
            "n_stable":          counts[STABILITY_STABLE],
            "n_slowly_adapting": counts[STABILITY_SLOWLY_ADAPTING],
            "n_unstable":        counts[STABILITY_UNSTABLE],
            "has_drift":         len(drift_events) > 0,
            "drift_event_count": len(drift_events),
        },
    }


# ── DB functions ──────────────────────────────────────────────────────────────

def save_snapshot(adjustments: dict, row_count: int) -> None:
    """
    Persist a recommendation snapshot to the database.

    Parameters
    ----------
    adjustments : output of adaptive_weights.compute_weight_adjustments()
    row_count   : number of completed outcome rows used to generate the rec
    """
    weights = {sig: entry["suggested_weight"] for sig, entry in adjustments.items()}
    metrics = {
        sig: {
            "n_active":        entry.get("n_active"),
            "lift":            entry.get("lift"),
            "win_rate_active": entry.get("win_rate_active"),
            "avg_return_5d":   entry.get("avg_return_5d"),
        }
        for sig, entry in adjustments.items()
    }
    now  = datetime.now().isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO recommendation_snapshots
                (snapshot_time, row_count, weights_json, metrics_json)
            VALUES (?, ?, ?, ?)
            """,
            (now, row_count, json.dumps(weights), json.dumps(metrics)),
        )
        conn.commit()
        log.info(
            "recommendation_observer: snapshot saved (row_count=%d, "
            "signals=%d, time=%s)",
            row_count, len(weights), now,
        )
    finally:
        conn.close()


def get_latest_snapshot() -> Optional[dict]:
    """Return the most recent snapshot, or None if the table is empty."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM recommendation_snapshots ORDER BY snapshot_time DESC LIMIT 1"
        ).fetchone()
        return _parse_row(dict(row)) if row else None
    finally:
        conn.close()


def get_snapshot_history(limit: int = 30) -> list:
    """
    Return up to `limit` most-recent snapshots, oldest-first.

    Limit is clamped to [1, MAX_HISTORY_LIMIT] to prevent runaway queries.
    """
    limit = max(1, min(limit, MAX_HISTORY_LIMIT))
    conn  = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM recommendation_snapshots
            ORDER BY snapshot_time DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        # Reverse so returned list is oldest-first (convenient for callers)
        return [_parse_row(dict(r)) for r in reversed(rows)]
    finally:
        conn.close()
