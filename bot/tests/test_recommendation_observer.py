"""
Unit tests for recommendation_observer.py.

Pure-function tests (deltas, stability, drift, report) need no DB.
DB-touching tests use a temp SQLite file with the snapshot table created
directly — no need to run the full migration stack.
"""
import json
import sqlite3
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch

import database
from recommendation_observer import (
    DRIFT_WARNING_THRESHOLD,
    MAX_HISTORY_LIMIT,
    STABLE_THRESHOLD,
    STABILITY_SLOWLY_ADAPTING,
    STABILITY_STABLE,
    STABILITY_UNSTABLE,
    UNSTABLE_THRESHOLD,
    _parse_row,
    classify_stability,
    compute_deltas,
    detect_drift,
    generate_observation_report,
    get_latest_snapshot,
    get_snapshot_history,
    save_snapshot,
)
from adaptive_weights import DEFAULT_WEIGHTS


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _snap(weights, time="2026-01-01T00:00:00", row_count=10, snap_id=1, metrics=None):
    """Build a parsed snapshot dict."""
    return {
        "id":            snap_id,
        "snapshot_time": time,
        "row_count":     row_count,
        "weights":       dict(weights),
        "metrics":       metrics or {},
    }


def _default_snap(time="2026-01-01T00:00:00", snap_id=1):
    """Snapshot whose weights match DEFAULT_WEIGHTS exactly."""
    return _snap(DEFAULT_WEIGHTS.copy(), time=time, snap_id=snap_id)


def _adjustments(overrides: dict = None) -> dict:
    """
    Minimal adjustments dict compatible with save_snapshot().
    All fields present; override per-signal suggested_weight via overrides.
    """
    overrides = overrides or {}
    result = {}
    for sig, default in DEFAULT_WEIGHTS.items():
        suggested = overrides.get(sig, default)
        result[sig] = {
            "default_weight":   default,
            "adjustment":       round(suggested - default, 3),
            "suggested_weight": suggested,
            "n_active":         10,
            "win_rate_active":  60.0,
            "lift":             10.0,
            "avg_return_5d":    2.0,
            "reason":           "test",
            "clamped":          False,
        }
    return result


# ── DB fixture ─────────────────────────────────────────────────────────────────

def _create_snapshots_table(db_path: str) -> None:
    """Create just the recommendation_snapshots table in a temp DB."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recommendation_snapshots (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_time TEXT    NOT NULL,
            row_count     INTEGER NOT NULL,
            weights_json  TEXT    NOT NULL,
            metrics_json  TEXT    NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rec_snapshots_time "
        "ON recommendation_snapshots(snapshot_time)"
    )
    conn.commit()
    conn.close()


def _make_conn(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture()
def snap_db(tmp_path):
    """Temp DB with the recommendation_snapshots table wired in."""
    db_path = str(tmp_path / "test_observer.db")
    _create_snapshots_table(db_path)
    with patch.object(database, "DB_PATH", db_path), \
         patch("database.get_connection", lambda: _make_conn(db_path)):
        yield db_path


# ── _parse_row ─────────────────────────────────────────────────────────────────

class TestParseRow:
    def test_parses_weights(self):
        row = {
            "id": 1, "snapshot_time": "2026-01-01T00:00:00",
            "row_count": 5,
            "weights_json": '{"options": 3.2, "breakout": 1.9}',
            "metrics_json": "{}",
        }
        snap = _parse_row(row)
        assert snap["weights"]["options"] == 3.2
        assert snap["weights"]["breakout"] == 1.9

    def test_parses_metrics(self):
        row = {
            "id": 2, "snapshot_time": "2026-01-02T00:00:00",
            "row_count": 8,
            "weights_json": "{}",
            "metrics_json": '{"options": {"n_active": 5, "lift": 20.0}}',
        }
        snap = _parse_row(row)
        assert snap["metrics"]["options"]["n_active"] == 5

    def test_invalid_json_defaults_to_empty(self):
        row = {
            "id": 3, "snapshot_time": "2026-01-03T00:00:00",
            "row_count": 0,
            "weights_json": "not-json",
            "metrics_json": "also-not-json",
        }
        snap = _parse_row(row)
        assert snap["weights"] == {}
        assert snap["metrics"] == {}

    def test_none_json_defaults_to_empty(self):
        row = {
            "id": 4, "snapshot_time": "2026-01-04T00:00:00",
            "row_count": 0,
            "weights_json": None,
            "metrics_json": None,
        }
        snap = _parse_row(row)
        assert snap["weights"] == {}
        assert snap["metrics"] == {}


# ── compute_deltas ─────────────────────────────────────────────────────────────

class TestComputeDeltas:
    def test_positive_delta(self):
        a = _snap({"options": 3.0})
        b = _snap({"options": 3.3})
        result = compute_deltas(a, b)
        assert result["options"]["delta"]     == pytest.approx(0.3, abs=1e-6)
        assert result["options"]["direction"] == "up"

    def test_negative_delta(self):
        a = _snap({"breakout": 2.0})
        b = _snap({"breakout": 1.7})
        result = compute_deltas(a, b)
        assert result["breakout"]["delta"]     == pytest.approx(-0.3, abs=1e-6)
        assert result["breakout"]["direction"] == "down"

    def test_zero_delta(self):
        a = _snap({"catalyst": 2.0})
        b = _snap({"catalyst": 2.0})
        result = compute_deltas(a, b)
        assert result["catalyst"]["delta"]     == 0.0
        assert result["catalyst"]["direction"] == "flat"

    def test_all_signals_present_in_result(self):
        result = compute_deltas(_default_snap(), _default_snap())
        for sig in DEFAULT_WEIGHTS:
            assert sig in result

    def test_missing_signal_uses_default(self):
        # snap_a has no "options" key — should use DEFAULT_WEIGHTS["options"]
        a = _snap({})
        b = _snap({"options": 3.5})
        result = compute_deltas(a, b)
        assert result["options"]["weight_from"] == DEFAULT_WEIGHTS["options"]
        assert result["options"]["delta"]       == pytest.approx(0.5, abs=1e-6)

    def test_weight_from_weight_to_populated(self):
        a = _snap({"insider": 2.0})
        b = _snap({"insider": 2.4})
        result = compute_deltas(a, b)
        assert result["insider"]["weight_from"] == 2.0
        assert result["insider"]["weight_to"]   == 2.4

    def test_deterministic(self):
        a = _default_snap("2026-01-01T00:00:00")
        b = _snap({"options": 3.2, "breakout": 1.8, "insider": 2.1,
                   "short_squeeze": 2.0, "catalyst": 1.9, "institutional": 1.0})
        r1 = compute_deltas(a, b)
        r2 = compute_deltas(a, b)
        assert r1 == r2


# ── classify_stability ─────────────────────────────────────────────────────────

class TestClassifyStability:
    def test_single_snapshot_all_stable(self):
        result = classify_stability([_default_snap()])
        for sig in DEFAULT_WEIGHTS:
            assert result[sig]["label"]     == STABILITY_STABLE
            assert result[sig]["max_delta"] == 0.0

    def test_empty_history_all_stable(self):
        result = classify_stability([])
        for sig in DEFAULT_WEIGHTS:
            assert result[sig]["label"] == STABILITY_STABLE

    def test_stable_when_delta_at_threshold(self):
        a = _snap({"breakout": 2.0}, time="2026-01-01T00:00:00")
        b = _snap({"breakout": 2.0 + STABLE_THRESHOLD}, time="2026-01-02T00:00:00")
        result = classify_stability([a, b])
        assert result["breakout"]["label"] == STABILITY_STABLE

    def test_slowly_adapting_when_delta_above_stable(self):
        delta = STABLE_THRESHOLD + 0.01
        a = _snap({"breakout": 2.0}, time="2026-01-01T00:00:00")
        b = _snap({"breakout": 2.0 + delta}, time="2026-01-02T00:00:00")
        result = classify_stability([a, b])
        assert result["breakout"]["label"] == STABILITY_SLOWLY_ADAPTING

    def test_slowly_adapting_when_delta_at_unstable_threshold(self):
        a = _snap({"catalyst": 2.0}, time="2026-01-01T00:00:00")
        b = _snap({"catalyst": 2.0 + UNSTABLE_THRESHOLD}, time="2026-01-02T00:00:00")
        result = classify_stability([a, b])
        assert result["catalyst"]["label"] == STABILITY_SLOWLY_ADAPTING

    def test_unstable_when_delta_above_unstable_threshold(self):
        delta = UNSTABLE_THRESHOLD + 0.01
        a = _snap({"options": 3.0}, time="2026-01-01T00:00:00")
        b = _snap({"options": 3.0 + delta}, time="2026-01-02T00:00:00")
        result = classify_stability([a, b])
        assert result["options"]["label"] == STABILITY_UNSTABLE

    def test_uses_max_across_multiple_steps(self):
        # Small step then large step — max_delta should reflect the large one
        a = _snap({"insider": 2.0}, time="2026-01-01T00:00:00")
        b = _snap({"insider": 2.02}, time="2026-01-02T00:00:00")   # tiny
        c = _snap({"insider": 2.02 + UNSTABLE_THRESHOLD + 0.05},    # big
                  time="2026-01-03T00:00:00")
        result = classify_stability([a, b, c])
        assert result["insider"]["label"] == STABILITY_UNSTABLE

    def test_all_signals_present_in_result(self):
        result = classify_stability([_default_snap()])
        assert set(result.keys()) == set(DEFAULT_WEIGHTS.keys())

    def test_n_snapshots_reported(self):
        snaps = [_default_snap(f"2026-01-0{i}T00:00:00", i) for i in range(1, 4)]
        result = classify_stability(snaps)
        for entry in result.values():
            assert entry["n_snapshots"] == 3

    def test_order_independent(self):
        a = _snap({"options": 3.0}, time="2026-01-01T00:00:00")
        b = _snap({"options": 3.15}, time="2026-01-02T00:00:00")
        r_asc  = classify_stability([a, b])
        r_desc = classify_stability([b, a])
        assert r_asc["options"]["label"]     == r_desc["options"]["label"]
        assert r_asc["options"]["max_delta"] == r_desc["options"]["max_delta"]

    def test_deterministic(self):
        snaps = [
            _snap({"options": 3.0 + i * 0.03}, time=f"2026-01-0{i+1}T00:00:00")
            for i in range(3)
        ]
        assert classify_stability(snaps) == classify_stability(snaps)


# ── detect_drift ──────────────────────────────────────────────────────────────

class TestDetectDrift:
    def test_no_drift_empty_history(self):
        assert detect_drift([]) == []

    def test_no_drift_single_snapshot(self):
        assert detect_drift([_default_snap()]) == []

    def test_no_event_below_threshold(self):
        a = _snap({"breakout": 2.0}, time="2026-01-01T00:00:00")
        b = _snap({"breakout": 2.0 + DRIFT_WARNING_THRESHOLD * 0.9},
                  time="2026-01-02T00:00:00")
        assert detect_drift([a, b]) == []

    def test_event_at_threshold(self):
        delta = DRIFT_WARNING_THRESHOLD + 0.01
        a = _snap({"catalyst": 2.0}, time="2026-01-01T00:00:00")
        b = _snap({"catalyst": 2.0 + delta}, time="2026-01-02T00:00:00")
        events = detect_drift([a, b])
        assert len(events) == 1
        assert events[0]["signal"]    == "catalyst"
        assert events[0]["direction"] == "up"

    def test_negative_drift_detected(self):
        delta = DRIFT_WARNING_THRESHOLD + 0.01
        a = _snap({"options": 3.0}, time="2026-01-01T00:00:00")
        b = _snap({"options": 3.0 - delta}, time="2026-01-02T00:00:00")
        events = detect_drift([a, b])
        assert len(events) >= 1
        match = next(e for e in events if e["signal"] == "options")
        assert match["direction"] == "down"

    def test_high_severity_above_unstable_threshold(self):
        delta = UNSTABLE_THRESHOLD + 0.01
        a = _snap({"insider": 2.0}, time="2026-01-01T00:00:00")
        b = _snap({"insider": 2.0 + delta}, time="2026-01-02T00:00:00")
        events = detect_drift([a, b])
        assert any(e["severity"] == "HIGH" for e in events if e["signal"] == "insider")

    def test_medium_severity_between_thresholds(self):
        delta = (DRIFT_WARNING_THRESHOLD + UNSTABLE_THRESHOLD) / 2
        a = _snap({"breakout": 2.0}, time="2026-01-01T00:00:00")
        b = _snap({"breakout": 2.0 + delta}, time="2026-01-02T00:00:00")
        events = detect_drift([a, b])
        if events:
            match = next((e for e in events if e["signal"] == "breakout"), None)
            if match:
                assert match["severity"] == "MEDIUM"

    def test_sorted_by_abs_delta_descending(self):
        big   = UNSTABLE_THRESHOLD + 0.1
        small = DRIFT_WARNING_THRESHOLD + 0.01
        a = _snap({"options": 3.0, "catalyst": 2.0}, time="2026-01-01T00:00:00")
        b = _snap({"options": 3.0 + big, "catalyst": 2.0 + small},
                  time="2026-01-02T00:00:00")
        events = detect_drift([a, b])
        abs_deltas = [e["abs_delta"] for e in events]
        assert abs_deltas == sorted(abs_deltas, reverse=True)

    def test_event_fields(self):
        delta = DRIFT_WARNING_THRESHOLD + 0.05
        a = _snap({"options": 3.0}, time="2026-01-01T00:00:00")
        b = _snap({"options": 3.0 + delta}, time="2026-01-02T00:00:00")
        events = detect_drift([a, b])
        opt_event = next(e for e in events if e["signal"] == "options")
        for key in ("signal", "delta", "abs_delta", "direction",
                    "weight_from", "weight_to", "from_time", "to_time", "severity"):
            assert key in opt_event, f"missing field: {key}"

    def test_order_independent(self):
        delta = DRIFT_WARNING_THRESHOLD + 0.05
        a = _snap({"catalyst": 2.0}, time="2026-01-01T00:00:00")
        b = _snap({"catalyst": 2.0 + delta}, time="2026-01-02T00:00:00")
        assert detect_drift([a, b]) == detect_drift([b, a])

    def test_deterministic(self):
        delta = DRIFT_WARNING_THRESHOLD + 0.05
        a = _snap({"options": 3.0, "breakout": 2.0}, time="2026-01-01T00:00:00")
        b = _snap({"options": 3.0 + delta, "breakout": 2.0 + delta},
                  time="2026-01-02T00:00:00")
        assert detect_drift([a, b]) == detect_drift([a, b])


# ── generate_observation_report ───────────────────────────────────────────────

class TestGenerateObservationReport:
    def test_empty_returns_all_stable(self):
        report = generate_observation_report([])
        assert report["snapshot_count"]         == 0
        assert report["latest"]                 is None
        assert report["summary"]["has_drift"]   is False
        assert report["summary"]["n_stable"]    == len(DEFAULT_WEIGHTS)
        assert report["summary"]["n_unstable"]  == 0

    def test_structure_keys(self):
        report = generate_observation_report([_default_snap()])
        for key in ("snapshot_count", "latest", "stability",
                    "drift_events", "summary"):
            assert key in report
        for key in ("n_stable", "n_slowly_adapting", "n_unstable",
                    "has_drift", "drift_event_count"):
            assert key in report["summary"]

    def test_snapshot_count(self):
        snaps = [_default_snap(f"2026-01-0{i}T00:00:00", i) for i in range(1, 4)]
        report = generate_observation_report(snaps)
        assert report["snapshot_count"] == 3

    def test_latest_is_most_recent(self):
        snaps = [
            _snap(DEFAULT_WEIGHTS, time="2026-01-01T00:00:00", snap_id=1),
            _snap(DEFAULT_WEIGHTS, time="2026-01-03T00:00:00", snap_id=3),
            _snap(DEFAULT_WEIGHTS, time="2026-01-02T00:00:00", snap_id=2),
        ]
        report = generate_observation_report(snaps)
        assert report["latest"]["snapshot_time"] == "2026-01-03T00:00:00"

    def test_has_drift_when_large_step(self):
        delta = DRIFT_WARNING_THRESHOLD + 0.1
        a = _snap({"options": 3.0}, time="2026-01-01T00:00:00")
        b = _snap({"options": 3.0 + delta}, time="2026-01-02T00:00:00")
        report = generate_observation_report([a, b])
        assert report["summary"]["has_drift"]         is True
        assert report["summary"]["drift_event_count"] >= 1

    def test_unstable_counted(self):
        delta = UNSTABLE_THRESHOLD + 0.05
        a = _snap({"options": 3.0}, time="2026-01-01T00:00:00")
        b = _snap({"options": 3.0 + delta}, time="2026-01-02T00:00:00")
        report = generate_observation_report([a, b])
        assert report["summary"]["n_unstable"] >= 1

    def test_counts_sum_to_total_signals(self):
        snaps = [_default_snap("2026-01-01T00:00:00", 1),
                 _default_snap("2026-01-02T00:00:00", 2)]
        report = generate_observation_report(snaps)
        total = (report["summary"]["n_stable"] +
                 report["summary"]["n_slowly_adapting"] +
                 report["summary"]["n_unstable"])
        assert total == len(DEFAULT_WEIGHTS)

    def test_deterministic(self):
        snaps = [
            _snap({"options": 3.0 + i * 0.05}, time=f"2026-01-0{i+1}T00:00:00")
            for i in range(3)
        ]
        r1 = generate_observation_report(snaps)
        r2 = generate_observation_report(snaps)
        assert r1["snapshot_count"]           == r2["snapshot_count"]
        assert r1["summary"]                  == r2["summary"]
        assert r1["drift_events"]             == r2["drift_events"]


# ── DB: save_snapshot / get_latest_snapshot / get_snapshot_history ─────────────

class TestSnapshotDB:
    def test_save_then_get_latest(self, snap_db):
        adj = _adjustments()
        save_snapshot(adj, row_count=10)
        snap = get_latest_snapshot()
        assert snap is not None
        assert snap["row_count"] == 10
        for sig in DEFAULT_WEIGHTS:
            assert sig in snap["weights"]

    def test_latest_is_most_recent_save(self, snap_db):
        save_snapshot(_adjustments({"options": 3.1}), row_count=5)
        save_snapshot(_adjustments({"options": 3.4}), row_count=8)
        snap = get_latest_snapshot()
        assert snap["weights"]["options"] == pytest.approx(3.4, abs=1e-6)
        assert snap["row_count"] == 8

    def test_empty_db_returns_none(self, snap_db):
        assert get_latest_snapshot() is None

    def test_history_returns_saved_snapshots(self, snap_db):
        for i in range(3):
            save_snapshot(_adjustments({"options": 3.0 + i * 0.1}), row_count=i + 5)
        history = get_snapshot_history(limit=10)
        assert len(history) == 3

    def test_history_oldest_first(self, snap_db):
        # Insert with known times by saving twice; timestamps should be ascending
        save_snapshot(_adjustments(), row_count=1)
        save_snapshot(_adjustments(), row_count=2)
        history = get_snapshot_history()
        times = [s["snapshot_time"] for s in history]
        assert times == sorted(times)

    def test_history_limit_respected(self, snap_db):
        for i in range(5):
            save_snapshot(_adjustments(), row_count=i)
        history = get_snapshot_history(limit=3)
        assert len(history) == 3

    def test_history_limit_clamped_to_max(self, snap_db):
        # Requesting more than MAX_HISTORY_LIMIT should still work (just capped)
        save_snapshot(_adjustments(), row_count=1)
        history = get_snapshot_history(limit=MAX_HISTORY_LIMIT + 1000)
        assert len(history) <= MAX_HISTORY_LIMIT

    def test_history_limit_minimum_one(self, snap_db):
        save_snapshot(_adjustments(), row_count=1)
        history = get_snapshot_history(limit=0)  # clamped to 1
        assert len(history) >= 1

    def test_saved_weights_roundtrip(self, snap_db):
        overrides = {"options": 3.2, "breakout": 1.8, "catalyst": 2.1}
        save_snapshot(_adjustments(overrides), row_count=15)
        snap = get_latest_snapshot()
        for sig, expected in overrides.items():
            assert snap["weights"][sig] == pytest.approx(expected, abs=1e-6)

    def test_saved_metrics_roundtrip(self, snap_db):
        save_snapshot(_adjustments(), row_count=7)
        snap = get_latest_snapshot()
        for sig in DEFAULT_WEIGHTS:
            assert sig in snap["metrics"]
            assert "n_active" in snap["metrics"][sig]
            assert "lift"     in snap["metrics"][sig]

    def test_empty_history_returns_empty_list(self, snap_db):
        assert get_snapshot_history() == []


# ── Stability threshold boundary sanity ──────────────────────────────────────

class TestThresholds:
    def test_stable_threshold_below_unstable(self):
        assert STABLE_THRESHOLD < UNSTABLE_THRESHOLD

    def test_drift_threshold_positive(self):
        assert DRIFT_WARNING_THRESHOLD > 0.0

    def test_max_history_limit_reasonable(self):
        assert 10 <= MAX_HISTORY_LIMIT <= 10_000
