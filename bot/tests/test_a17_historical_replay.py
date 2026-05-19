"""
Phase A17 — Tests for historical_replay.py and related API endpoints.

Covers:
  - _run_id_from_params: deterministic, prefixed, different params → different ID
  - classify_simulated_decision: all decision paths
  - classify_outcome: all outcome paths
  - _compute_summary: aggregation math
  - create_replay_run: validation, persistence, max_rows cap
  - get_replay_run / get_replay_runs / get_replay_events: DB reads
  - _find_nearest_regime: nearest snapshot logic
  - _find_nearest_outcome: nearest outcome logic
  - execute_replay: full reconstruction (mocked gate/QC/data)
  - run_replay: create + execute
  - Bounded caps (max_rows)
  - Filters (ticker, source, setup_type, date)
  - Safety: no trading, no send_sms, no broker, no DELETE on events
  - API: GET /replay/runs, POST /replay/run, GET /replay/runs/<id>,
         GET /replay/runs/<id>/events
"""
import inspect
import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import database
import historical_replay as hr

# ── DB helpers ────────────────────────────────────────────────────────────────

def _make_get_conn(db_path: str):
    def _get_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    return _get_conn


def _db(tmp_path):
    db_path = str(tmp_path / "test_a17.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS replay_runs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id            TEXT    NOT NULL UNIQUE,
            created_at        TEXT    NOT NULL,
            start_date        TEXT    NOT NULL,
            end_date          TEXT    NOT NULL,
            ticker_filter     TEXT,
            source_filter     TEXT,
            setup_type_filter TEXT,
            max_rows          INTEGER NOT NULL DEFAULT 500,
            status            TEXT    NOT NULL DEFAULT 'PENDING',
            event_count       INTEGER NOT NULL DEFAULT 0,
            completed_at      TEXT,
            summary_json      TEXT    NOT NULL DEFAULT '{}'
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
        CREATE TABLE IF NOT EXISTS alpha_shadow_log (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker                TEXT    NOT NULL,
            scan_time             TEXT    NOT NULL,
            alpha_score           REAL,
            alpha_tier            TEXT,
            setup_type            TEXT,
            predator_tier         TEXT,
            predator_score        REAL,
            tier_match            INTEGER NOT NULL DEFAULT 0,
            filter_reason         TEXT,
            component_scores_json TEXT,
            explanation           TEXT,
            detail_json           TEXT
        );
        CREATE TABLE IF NOT EXISTS market_regime_snapshots (
            id                         INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at                TEXT    NOT NULL,
            overall_regime             TEXT    NOT NULL,
            volatility_regime          TEXT    NOT NULL,
            breadth_regime             TEXT    NOT NULL,
            speculative_regime         TEXT    NOT NULL,
            regime_score               REAL    NOT NULL,
            risk_multiplier            REAL    NOT NULL,
            sizing_multiplier          REAL    NOT NULL,
            alpha_threshold_adjustment REAL    NOT NULL,
            confidence_adjustment      REAL    NOT NULL,
            explanation                TEXT    NOT NULL,
            warnings_json              TEXT    NOT NULL DEFAULT '[]',
            data_quality               TEXT    NOT NULL,
            raw_signals_json           TEXT    NOT NULL DEFAULT '{}'
        );
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
            created_at            TEXT,
            updated_at            TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
    monkeypatch.setattr(hr, "_ensure_tables", lambda: None)
    return db_path


# ── Data helpers ──────────────────────────────────────────────────────────────

def _insert_shadow(db_path, ticker="AAPL", scan_time="2026-01-15T10:00:00",
                   alpha_score=72.0, alpha_tier="HIGH_CONVICTION",
                   setup_type="MOMENTUM", predator_tier=None, filter_reason=None):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO alpha_shadow_log
           (ticker, scan_time, alpha_score, alpha_tier, setup_type,
            predator_tier, filter_reason)
           VALUES (?,?,?,?,?,?,?)""",
        (ticker, scan_time, alpha_score, alpha_tier, setup_type,
         predator_tier, filter_reason),
    )
    conn.commit()
    conn.close()


def _insert_regime(db_path, captured_at="2026-01-15T09:00:00", overall="RISK_ON",
                   score=75.0):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO market_regime_snapshots
           (captured_at, overall_regime, volatility_regime, breadth_regime,
            speculative_regime, regime_score, risk_multiplier, sizing_multiplier,
            alpha_threshold_adjustment, confidence_adjustment, explanation,
            data_quality)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (captured_at, overall, "CALM", "BROAD_STRENGTH", "SPECULATION_ACTIVE",
         score, 0.8, 1.2, -5.0, 5.0, "test", "GOOD"),
    )
    conn.commit()
    conn.close()


def _insert_outcome(db_path, ticker="AAPL", scan_time="2026-01-15T10:00:00",
                    status="COMPLETE", return_5d=6.0, return_10d=8.0,
                    max_gain=9.0, max_drawdown=-1.0):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO alpha_outcomes
           (ticker, scan_time, status, return_5d, return_10d, max_gain, max_drawdown)
           VALUES (?,?,?,?,?,?,?)""",
        (ticker, scan_time, status, return_5d, return_10d, max_gain, max_drawdown),
    )
    conn.commit()
    conn.close()


def _make_gate_result(
    alpha_tier="HIGH_CONVICTION",
    readiness_tier="ALERT_READY",
    readiness_score=75.0,
    alert_ready=True,
):
    return {
        "ticker":          "AAPL",
        "alpha_tier":      alpha_tier,
        "readiness_tier":  readiness_tier,
        "readiness_score": readiness_score,
        "alert_ready":     alert_ready,
        "reason":          "test",
        "blocking_factors": [],
    }


def _make_qc_result(
    qc_tier="ALLOW",
    qc_score=0.75,
    allow_notification=True,
):
    return {
        "qc_tier":            qc_tier,
        "qc_score":           qc_score,
        "allow_notification": allow_notification,
        "suppression_reason": None,
        "quality_flags":      [],
    }


def _make_params(
    start_date="2026-01-01",
    end_date="2026-06-01",
    ticker_filter=None,
    source_filter=None,
    setup_type_filter=None,
    max_rows=50,
):
    return {
        "start_date":         start_date,
        "end_date":           end_date,
        "ticker_filter":      ticker_filter,
        "source_filter":      source_filter,
        "setup_type_filter":  setup_type_filter,
        "max_rows":           max_rows,
    }


# ── _run_id_from_params ───────────────────────────────────────────────────────

class TestRunIdGeneration:
    def test_deterministic_same_params(self):
        id1 = hr._run_id_from_params("2026-01-01", "2026-06-01", None, None, None, "T1")
        id2 = hr._run_id_from_params("2026-01-01", "2026-06-01", None, None, None, "T1")
        assert id1 == id2

    def test_different_dates_give_different_id(self):
        id1 = hr._run_id_from_params("2026-01-01", "2026-06-01", None, None, None, "T1")
        id2 = hr._run_id_from_params("2026-02-01", "2026-06-01", None, None, None, "T1")
        assert id1 != id2

    def test_prefixed_with_rpl(self):
        run_id = hr._run_id_from_params("2026-01-01", "2026-06-01", None, None, None, "T1")
        assert run_id.startswith("RPL-")

    def test_different_ticker_filter_gives_different_id(self):
        id1 = hr._run_id_from_params("2026-01-01", "2026-06-01", ["AAPL"], None, None, "T1")
        id2 = hr._run_id_from_params("2026-01-01", "2026-06-01", ["MSFT"], None, None, "T1")
        assert id1 != id2

    def test_ticker_filter_order_independent(self):
        id1 = hr._run_id_from_params("2026-01-01", "2026-06-01", ["AAPL", "MSFT"], None, None, "T1")
        id2 = hr._run_id_from_params("2026-01-01", "2026-06-01", ["MSFT", "AAPL"], None, None, "T1")
        assert id1 == id2

    def test_different_created_at_gives_different_id(self):
        id1 = hr._run_id_from_params("2026-01-01", "2026-06-01", None, None, None, "T1")
        id2 = hr._run_id_from_params("2026-01-01", "2026-06-01", None, None, None, "T2")
        assert id1 != id2


# ── classify_simulated_decision ───────────────────────────────────────────────

class TestClassifySimulatedDecision:
    def test_filter_reason_gives_reject(self):
        gate = _make_gate_result(alpha_tier="HIGH_CONVICTION")
        assert hr.classify_simulated_decision(gate, {}, "bad signal") == "WOULD_REJECT"

    def test_ignore_alpha_tier_gives_reject(self):
        gate = _make_gate_result(alpha_tier="IGNORE")
        assert hr.classify_simulated_decision(gate, {}, None) == "WOULD_REJECT"

    def test_not_ready_gives_ignore(self):
        gate = _make_gate_result(
            alpha_tier="WATCH", readiness_tier="NOT_READY",
            alert_ready=False,
        )
        assert hr.classify_simulated_decision(gate, {}, None) == "WOULD_IGNORE"

    def test_qc_block_gives_block(self):
        gate = _make_gate_result(readiness_tier="MONITOR", alert_ready=False)
        qc   = _make_qc_result(qc_tier="BLOCK", allow_notification=False)
        assert hr.classify_simulated_decision(gate, qc, None) == "WOULD_BLOCK"

    def test_qc_suppress_gives_block(self):
        gate = _make_gate_result(readiness_tier="PRE_ALERT", alert_ready=False)
        qc   = _make_qc_result(qc_tier="SUPPRESS", allow_notification=False)
        assert hr.classify_simulated_decision(gate, qc, None) == "WOULD_BLOCK"

    def test_monitor_tier_gives_monitor(self):
        gate = _make_gate_result(readiness_tier="MONITOR", alert_ready=False)
        qc   = _make_qc_result(qc_tier="ALLOW", allow_notification=True)
        assert hr.classify_simulated_decision(gate, qc, None) == "WOULD_MONITOR"

    def test_pre_alert_tier_gives_prepare(self):
        gate = _make_gate_result(readiness_tier="PRE_ALERT", alert_ready=False)
        qc   = _make_qc_result(qc_tier="ALLOW", allow_notification=True)
        assert hr.classify_simulated_decision(gate, qc, None) == "WOULD_PREPARE"

    def test_alert_ready_with_allow_gives_alert(self):
        gate = _make_gate_result(readiness_tier="ALERT_READY", alert_ready=True)
        qc   = _make_qc_result(qc_tier="ALLOW", allow_notification=True)
        assert hr.classify_simulated_decision(gate, qc, None) == "WOULD_ALERT"

    def test_alert_ready_with_priority_gives_alert(self):
        gate = _make_gate_result(readiness_tier="RARE_ALERT", alert_ready=True)
        qc   = _make_qc_result(qc_tier="PRIORITY", allow_notification=True)
        assert hr.classify_simulated_decision(gate, qc, None) == "WOULD_ALERT"

    def test_alert_ready_qc_block_gives_block(self):
        gate = _make_gate_result(readiness_tier="ALERT_READY", alert_ready=True)
        qc   = _make_qc_result(qc_tier="BLOCK", allow_notification=False)
        assert hr.classify_simulated_decision(gate, qc, None) == "WOULD_BLOCK"

    def test_empty_gate_result_gives_ignore(self):
        assert hr.classify_simulated_decision({}, {}, None) == "WOULD_IGNORE"

    def test_none_gate_gives_ignore(self):
        assert hr.classify_simulated_decision(None, {}, None) == "WOULD_IGNORE"

    def test_all_in_valid_set(self):
        cases = [
            (_make_gate_result(), {}, "filter"),
            (_make_gate_result(alpha_tier="IGNORE"), {}, None),
            (_make_gate_result(readiness_tier="NOT_READY", alert_ready=False), {}, None),
            (_make_gate_result(), _make_qc_result(qc_tier="BLOCK", allow_notification=False), None),
            (_make_gate_result(readiness_tier="MONITOR", alert_ready=False), _make_qc_result(), None),
            (_make_gate_result(readiness_tier="PRE_ALERT", alert_ready=False), _make_qc_result(), None),
            (_make_gate_result(readiness_tier="ALERT_READY", alert_ready=True), _make_qc_result(), None),
        ]
        for gate, qc, fr in cases:
            d = hr.classify_simulated_decision(gate, qc, fr)
            assert d in hr.SIMULATED_DECISIONS, f"Invalid decision: {d}"


# ── classify_outcome ──────────────────────────────────────────────────────────

class TestClassifyOutcome:
    def test_no_outcome_is_inconclusive(self):
        assert hr.classify_outcome("WOULD_ALERT", None) == "inconclusive"

    def test_pending_outcome_is_inconclusive(self):
        outcome = {"status": "PENDING", "return_5d": 8.0}
        assert hr.classify_outcome("WOULD_ALERT", outcome) == "inconclusive"

    def test_would_alert_positive_return_is_early_but_valid(self):
        outcome = {"status": "COMPLETE", "return_5d": 5.0, "max_gain": 6.0}
        assert hr.classify_outcome("WOULD_ALERT", outcome) == "early_but_valid"

    def test_would_alert_negative_return_is_false_positive(self):
        outcome = {"status": "COMPLETE", "return_5d": -4.0, "max_gain": 0.5}
        assert hr.classify_outcome("WOULD_ALERT", outcome) == "false_positive"

    def test_would_ignore_high_return_is_missed_winner(self):
        outcome = {"status": "COMPLETE", "return_5d": 6.0, "max_gain": 7.0}
        assert hr.classify_outcome("WOULD_IGNORE", outcome) == "missed_winner"

    def test_would_ignore_large_loss_is_avoided_loser(self):
        outcome = {"status": "COMPLETE", "return_5d": -6.0, "max_gain": 0.0}
        assert hr.classify_outcome("WOULD_IGNORE", outcome) == "avoided_loser"

    def test_would_ignore_flat_return_is_correct_ignore(self):
        outcome = {"status": "COMPLETE", "return_5d": 0.5, "max_gain": 1.0}
        assert hr.classify_outcome("WOULD_IGNORE", outcome) == "correct_ignore"

    def test_would_block_high_return_is_missed_winner(self):
        outcome = {"status": "COMPLETE", "return_5d": 8.0, "max_gain": 9.0}
        assert hr.classify_outcome("WOULD_BLOCK", outcome) == "missed_winner"

    def test_would_reject_negative_return_is_avoided_loser(self):
        outcome = {"status": "COMPLETE", "return_5d": -7.0, "max_gain": 0.5}
        assert hr.classify_outcome("WOULD_REJECT", outcome) == "avoided_loser"

    def test_would_prepare_positive_return_is_early_but_valid(self):
        outcome = {"status": "COMPLETE", "return_5d": 4.0, "max_gain": 5.0}
        assert hr.classify_outcome("WOULD_PREPARE", outcome) == "early_but_valid"

    def test_too_late_when_max_gain_high_but_return_flat(self):
        outcome = {"status": "COMPLETE", "return_5d": 0.5, "max_gain": 6.0}
        assert hr.classify_outcome("WOULD_ALERT", outcome) == "too_late"

    def test_all_return_valid_classification(self):
        outcomes = [
            {"status": "COMPLETE", "return_5d": 7.0, "max_gain": 8.0},
            {"status": "COMPLETE", "return_5d": -6.0, "max_gain": 1.0},
            {"status": "COMPLETE", "return_5d": 0.5, "max_gain": 0.8},
        ]
        for dec in hr.SIMULATED_DECISIONS:
            for out in outcomes:
                oc = hr.classify_outcome(dec, out)
                assert oc in hr.OUTCOME_CLASSIFICATIONS, f"Invalid: {oc} for {dec}"


# ── _compute_summary ──────────────────────────────────────────────────────────

class TestComputeSummary:
    def _events(self, n, decision="WOULD_IGNORE", oc="inconclusive",
                regime="NEUTRAL", setup="MOMENTUM", source="alpha_universe"):
        return [
            {
                "simulated_decision":     decision,
                "outcome_classification": oc,
                "regime_overall":         regime,
                "setup_type":             setup,
                "source":                 source,
                "return_5d":              None,
                "alpha_tier":             "WATCH",
                "ticker":                 "AAPL",
                "scan_time":              "2026-01-01",
            }
            for _ in range(n)
        ]

    def test_event_count_correct(self):
        events = self._events(7)
        s = hr._compute_summary(events, "2026-01-01", "2026-06-01")
        assert s["event_count"] == 7

    def test_simulated_alert_count(self):
        events = self._events(3, "WOULD_ALERT") + self._events(2, "WOULD_IGNORE")
        s = hr._compute_summary(events, "2026-01-01", "2026-06-01")
        assert s["simulated_alert_count"] == 3

    def test_missed_winners_count(self):
        events = self._events(4, "WOULD_IGNORE", "missed_winner") + self._events(2)
        s = hr._compute_summary(events, "2026-01-01", "2026-06-01")
        assert s["missed_winners"] == 4

    def test_avoided_losers_count(self):
        events = self._events(3, "WOULD_BLOCK", "avoided_loser")
        s = hr._compute_summary(events, "2026-01-01", "2026-06-01")
        assert s["avoided_losers"] == 3

    def test_false_positives_count(self):
        events = self._events(2, "WOULD_ALERT", "false_positive")
        s = hr._compute_summary(events, "2026-01-01", "2026-06-01")
        assert s["false_positives"] == 2

    def test_regime_breakdown_populated(self):
        events = self._events(3, regime="RISK_ON") + self._events(2, regime="NEUTRAL")
        s = hr._compute_summary(events, "2026-01-01", "2026-06-01")
        assert s["regime_breakdown"]["RISK_ON"] == 3
        assert s["regime_breakdown"]["NEUTRAL"] == 2

    def test_setup_breakdown_populated(self):
        events = self._events(4, setup="MOMENTUM") + self._events(1, setup="BREAKOUT")
        s = hr._compute_summary(events, "2026-01-01", "2026-06-01")
        assert s["setup_breakdown"]["MOMENTUM"] == 4

    def test_best_opportunities_for_alerts(self):
        alerted = [
            {**self._events(1, "WOULD_ALERT")[0], "return_5d": r}
            for r in [10.0, 5.0, 2.0]
        ]
        events = alerted + self._events(2)
        s = hr._compute_summary(events, "2026-01-01", "2026-06-01")
        returns = [x["return_5d"] for x in s["best_simulated_opportunities"]]
        assert returns == sorted(returns, reverse=True)

    def test_worst_alerts(self):
        alerted = [
            {**self._events(1, "WOULD_ALERT")[0], "return_5d": r}
            for r in [-5.0, -2.0, 0.5]
        ]
        events = alerted
        s = hr._compute_summary(events, "2026-01-01", "2026-06-01")
        returns = [x["return_5d"] for x in s["worst_simulated_alerts"]]
        assert returns == sorted(returns)

    def test_replay_period_in_summary(self):
        s = hr._compute_summary([], "2026-01-01", "2026-06-01")
        assert s["replay_period"]["start_date"] == "2026-01-01"
        assert s["replay_period"]["end_date"] == "2026-06-01"


# ── create_replay_run ─────────────────────────────────────────────────────────

class TestCreateReplayRun:
    def test_creates_pending_row(self, db):
        run = hr.create_replay_run(_make_params())
        assert run["status"] == "PENDING"

    def test_run_id_has_rpl_prefix(self, db):
        run = hr.create_replay_run(_make_params())
        assert run["run_id"].startswith("RPL-")

    def test_max_rows_capped_at_2000(self, db):
        run = hr.create_replay_run(_make_params(max_rows=9999))
        assert run["max_rows"] == 2000

    def test_max_rows_defaults_to_500(self, db):
        params = _make_params()
        params.pop("max_rows")
        run = hr.create_replay_run(params)
        assert run["max_rows"] == 500

    def test_missing_start_date_raises(self, db):
        with pytest.raises(ValueError, match="start_date"):
            hr.create_replay_run({"end_date": "2026-06-01"})

    def test_start_date_after_end_raises(self, db):
        with pytest.raises(ValueError):
            hr.create_replay_run({"start_date": "2026-06-01", "end_date": "2026-01-01"})

    def test_returns_run_id(self, db):
        run = hr.create_replay_run(_make_params())
        assert run["run_id"] is not None and len(run["run_id"]) > 5

    def test_ticker_filter_stored(self, db):
        run = hr.create_replay_run(_make_params(ticker_filter=["AAPL", "MSFT"]))
        fetched = hr.get_replay_run(run["run_id"])
        assert set(fetched["ticker_filter"]) == {"AAPL", "MSFT"}


# ── get_replay_run / get_replay_runs / get_replay_events ─────────────────────

class TestGetReplayRun:
    def test_returns_none_when_not_found(self, db):
        assert hr.get_replay_run("RPL-NOTEXIST") is None

    def test_returns_dict_when_found(self, db):
        run = hr.create_replay_run(_make_params())
        fetched = hr.get_replay_run(run["run_id"])
        assert isinstance(fetched, dict)
        assert fetched["run_id"] == run["run_id"]

    def test_summary_deserialized(self, db):
        run = hr.create_replay_run(_make_params())
        fetched = hr.get_replay_run(run["run_id"])
        assert isinstance(fetched["summary"], dict)


class TestGetReplayRuns:
    def test_empty_returns_list(self, db):
        assert hr.get_replay_runs() == []

    def test_multiple_newest_first(self, db):
        hr.create_replay_run(_make_params(start_date="2026-01-01"))
        hr.create_replay_run(_make_params(start_date="2026-02-01"))
        runs = hr.get_replay_runs()
        assert runs[0]["start_date"] == "2026-02-01"

    def test_limit_respected(self, db):
        for i in range(5):
            hr.create_replay_run(_make_params(start_date=f"2026-0{i+1}-01"))
        assert len(hr.get_replay_runs(limit=3)) == 3


class TestGetReplayEvents:
    def _insert_events(self, db_path, run_id, n=3):
        conn = sqlite3.connect(db_path)
        for i in range(n):
            conn.execute(
                """INSERT INTO replay_events
                   (run_id, ticker, scan_time, simulated_decision, created_at)
                   VALUES (?,?,?,?,?)""",
                (run_id, f"T{i}", "2026-01-01", "WOULD_IGNORE", "2026-01-01"),
            )
        conn.commit()
        conn.close()

    def test_empty_returns_list(self, db):
        assert hr.get_replay_events("RPL-X") == []

    def test_returns_correct_run_events(self, db):
        run = hr.create_replay_run(_make_params())
        self._insert_events(db, run["run_id"], n=3)
        events = hr.get_replay_events(run["run_id"])
        assert len(events) == 3

    def test_limit_respected(self, db):
        run = hr.create_replay_run(_make_params())
        self._insert_events(db, run["run_id"], n=5)
        events = hr.get_replay_events(run["run_id"], limit=2)
        assert len(events) == 2

    def test_events_have_required_fields(self, db):
        run = hr.create_replay_run(_make_params())
        self._insert_events(db, run["run_id"], n=1)
        event = hr.get_replay_events(run["run_id"])[0]
        assert "ticker" in event
        assert "scan_time" in event
        assert "simulated_decision" in event


# ── _find_nearest_regime ──────────────────────────────────────────────────────

class TestFindNearestRegime:
    def test_returns_none_when_no_snapshots(self, db):
        assert hr._find_nearest_regime("2026-01-15T10:00:00") is None

    def test_returns_nearest_before_scan_time(self, db):
        _insert_regime(db, captured_at="2026-01-15T08:00:00", overall="NEUTRAL")
        _insert_regime(db, captured_at="2026-01-15T12:00:00", overall="RISK_ON")
        r = hr._find_nearest_regime("2026-01-15T10:00:00")
        assert r["overall_regime"] == "NEUTRAL"

    def test_returns_most_recent_before_scan_time(self, db):
        _insert_regime(db, captured_at="2026-01-15T07:00:00", overall="RISK_OFF")
        _insert_regime(db, captured_at="2026-01-15T09:00:00", overall="NEUTRAL")
        r = hr._find_nearest_regime("2026-01-15T10:00:00")
        assert r["overall_regime"] == "NEUTRAL"

    def test_falls_back_to_earliest_when_none_before(self, db):
        _insert_regime(db, captured_at="2026-01-15T14:00:00", overall="RISK_ON")
        r = hr._find_nearest_regime("2026-01-15T08:00:00")
        assert r["overall_regime"] == "RISK_ON"

    def test_returns_dict_with_overall_regime(self, db):
        _insert_regime(db, captured_at="2026-01-15T09:00:00", overall="PANIC")
        r = hr._find_nearest_regime("2026-01-15T10:00:00")
        assert "overall_regime" in r
        assert r["overall_regime"] == "PANIC"


# ── _find_nearest_outcome ─────────────────────────────────────────────────────

class TestFindNearestOutcome:
    def test_returns_none_when_no_outcomes(self, db):
        assert hr._find_nearest_outcome("AAPL", "2026-01-15T10:00:00") is None

    def test_returns_matching_outcome(self, db):
        _insert_outcome(db, ticker="AAPL", scan_time="2026-01-15T10:00:00",
                        return_5d=6.0)
        o = hr._find_nearest_outcome("AAPL", "2026-01-15T10:00:00")
        assert o["return_5d"] == 6.0

    def test_ignores_different_ticker(self, db):
        _insert_outcome(db, ticker="MSFT", scan_time="2026-01-15T10:00:00")
        assert hr._find_nearest_outcome("AAPL", "2026-01-15T10:00:00") is None

    def test_ignores_time_outside_window(self, db):
        _insert_outcome(db, ticker="AAPL", scan_time="2026-01-15T12:00:00")
        assert hr._find_nearest_outcome("AAPL", "2026-01-15T10:00:00") is None

    def test_returns_closest_within_window(self, db):
        _insert_outcome(db, ticker="AAPL", scan_time="2026-01-15T10:01:00",
                        return_5d=3.0)
        _insert_outcome(db, ticker="AAPL", scan_time="2026-01-15T10:05:00",
                        return_5d=7.0)
        o = hr._find_nearest_outcome("AAPL", "2026-01-15T10:00:00")
        assert o["return_5d"] == 3.0


# ── execute_replay ────────────────────────────────────────────────────────────

class TestExecuteReplay:
    def _mock_gate(self, candidate, context=None):
        return _make_gate_result()

    def _mock_qc(self, candidate, prior, context=None):
        return _make_qc_result()

    def test_empty_shadow_log_gives_zero_events(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        run = hr.create_replay_run(_make_params())
        result = hr.execute_replay(run["run_id"])
        assert result["event_count"] == 0
        assert result["status"] == "COMPLETE"

    def test_creates_one_event_per_shadow_row(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        _insert_shadow(db, ticker="AAPL", scan_time="2026-01-15T10:00:00")
        _insert_shadow(db, ticker="MSFT", scan_time="2026-01-16T10:00:00")
        run = hr.create_replay_run(_make_params())
        result = hr.execute_replay(run["run_id"])
        assert result["event_count"] == 2

    def test_status_is_complete_after_run(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        run = hr.create_replay_run(_make_params())
        result = hr.execute_replay(run["run_id"])
        assert result["status"] == "COMPLETE"

    def test_summary_populated(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        _insert_shadow(db)
        run = hr.create_replay_run(_make_params())
        result = hr.execute_replay(run["run_id"])
        assert "event_count" in result["summary"]

    def test_events_persisted_to_db(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        _insert_shadow(db)
        run = hr.create_replay_run(_make_params())
        hr.execute_replay(run["run_id"])
        events = hr.get_replay_events(run["run_id"])
        assert len(events) >= 1

    def test_gate_failure_skips_row_not_crash(self, db, monkeypatch):
        def _bad_gate(c, ctx=None):
            raise RuntimeError("gate down")
        monkeypatch.setattr("alpha_alert_gate.score_readiness", _bad_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        _insert_shadow(db)
        run = hr.create_replay_run(_make_params())
        result = hr.execute_replay(run["run_id"])
        assert result["status"] == "COMPLETE"

    def test_max_rows_honored(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        for i in range(10):
            _insert_shadow(db, ticker="AAPL", scan_time=f"2026-01-{i+1:02d}T10:00:00")
        run = hr.create_replay_run(_make_params(max_rows=3))
        result = hr.execute_replay(run["run_id"])
        assert result["event_count"] <= 3

    def test_not_found_run_raises(self, db):
        with pytest.raises(ValueError, match="not found"):
            hr.execute_replay("RPL-NOTEXIST")

    def test_regime_context_attached_when_available(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        _insert_shadow(db, scan_time="2026-01-15T10:00:00")
        _insert_regime(db, captured_at="2026-01-15T09:00:00", overall="RISK_ON")
        run = hr.create_replay_run(_make_params())
        result = hr.execute_replay(run["run_id"])
        events = hr.get_replay_events(result["run_id"])
        assert events[0]["regime_overall"] == "RISK_ON"


# ── run_replay ────────────────────────────────────────────────────────────────

class TestRunReplay:
    def _mock_gate(self, c, ctx=None):
        return _make_gate_result()

    def _mock_qc(self, c, prior, ctx=None):
        return _make_qc_result()

    def test_returns_dict_with_run_id(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        result = hr.run_replay(_make_params())
        assert "run_id" in result
        assert result["run_id"].startswith("RPL-")

    def test_returns_complete_status(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        result = hr.run_replay(_make_params())
        assert result["status"] == "COMPLETE"

    def test_invalid_params_raises_value_error(self, db):
        with pytest.raises(ValueError):
            hr.run_replay({"start_date": "2026-06-01", "end_date": "2026-01-01"})

    def test_run_persisted(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        result = hr.run_replay(_make_params())
        fetched = hr.get_replay_run(result["run_id"])
        assert fetched is not None

    def test_run_id_deterministic_from_params_and_time(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        r1 = hr.run_replay(_make_params(start_date="2026-01-01"))
        r2 = hr.run_replay(_make_params(start_date="2026-02-01"))
        assert r1["run_id"] != r2["run_id"]


# ── Bounded caps ──────────────────────────────────────────────────────────────

class TestBoundedCaps:
    def _mock_gate(self, c, ctx=None): return _make_gate_result()
    def _mock_qc(self, c, p, ctx=None): return _make_qc_result()

    def test_max_rows_above_cap_is_clamped(self, db):
        run = hr.create_replay_run(_make_params(max_rows=5000))
        assert run["max_rows"] == 2000

    def test_max_rows_zero_gives_zero_events(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        _insert_shadow(db)
        result = hr.run_replay(_make_params(max_rows=0))
        assert result["event_count"] == 0

    def test_max_rows_five_caps_at_five(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        for i in range(10):
            _insert_shadow(db, ticker=f"T{i}", scan_time=f"2026-01-{i+1:02d}T10:00:00")
        result = hr.run_replay(_make_params(max_rows=5))
        assert result["event_count"] == 5


# ── Filters ───────────────────────────────────────────────────────────────────

class TestFilters:
    def _mock_gate(self, c, ctx=None): return _make_gate_result()
    def _mock_qc(self, c, p, ctx=None): return _make_qc_result()

    def test_ticker_filter_applied(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        _insert_shadow(db, ticker="AAPL")
        _insert_shadow(db, ticker="MSFT")
        result = hr.run_replay(_make_params(ticker_filter=["AAPL"]))
        tickers = [e["ticker"] for e in result["events"]]
        assert all(t == "AAPL" for t in tickers)
        assert "MSFT" not in tickers

    def test_source_filter_predator(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        _insert_shadow(db, ticker="AAPL", predator_tier="BREAKOUT")
        _insert_shadow(db, ticker="MSFT", predator_tier=None)
        result = hr.run_replay(_make_params(source_filter="predator_shadow"))
        tickers = [e["ticker"] for e in result["events"]]
        assert "AAPL" in tickers
        assert "MSFT" not in tickers

    def test_source_filter_alpha_universe(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        _insert_shadow(db, ticker="AAPL", predator_tier=None)
        _insert_shadow(db, ticker="MSFT", predator_tier="BREAKOUT")
        result = hr.run_replay(_make_params(source_filter="alpha_universe"))
        tickers = [e["ticker"] for e in result["events"]]
        assert "AAPL" in tickers
        assert "MSFT" not in tickers

    def test_setup_type_filter(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        _insert_shadow(db, ticker="AAPL", setup_type="MOMENTUM")
        _insert_shadow(db, ticker="MSFT", setup_type="BREAKOUT")
        result = hr.run_replay(_make_params(setup_type_filter="MOMENTUM"))
        setups = [e["setup_type"] for e in result["events"]]
        assert all(s == "MOMENTUM" for s in setups)

    def test_date_range_filter(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        _insert_shadow(db, ticker="AAPL", scan_time="2026-01-15T10:00:00")
        _insert_shadow(db, ticker="MSFT", scan_time="2026-03-15T10:00:00")
        result = hr.run_replay(_make_params(start_date="2026-01-01", end_date="2026-02-01"))
        tickers = [e["ticker"] for e in result["events"]]
        assert "AAPL" in tickers
        assert "MSFT" not in tickers

    def test_no_match_returns_zero_events(self, db, monkeypatch):
        monkeypatch.setattr("alpha_alert_gate.score_readiness", self._mock_gate)
        monkeypatch.setattr("alpha_notification_qc.evaluate_notification_quality", self._mock_qc)
        _insert_shadow(db, ticker="AAPL")
        result = hr.run_replay(_make_params(ticker_filter=["NOTEXIST"]))
        assert result["event_count"] == 0


# ── Safety constraints ────────────────────────────────────────────────────────

class TestSafetyConstraints:
    @staticmethod
    def _src():
        return inspect.getsource(hr)

    def test_no_trading_functions(self):
        src = self._src()
        for fn in ["place_order", "submit_order", "execute_trade", "buy_stock"]:
            assert fn not in src, f"Found forbidden: {fn}"

    def test_no_send_sms(self):
        assert "send_sms" not in self._src()

    def test_no_broker_calls(self):
        src = self._src().lower()
        for b in ["wealthsimple", "alpaca", "ibkr"]:
            assert b not in src, f"Found broker ref: {b}"

    def test_no_delete_on_replay_events(self):
        lines = [ln for ln in self._src().splitlines()
                 if "DELETE" in ln.upper() and "replay_events" in ln]
        assert not lines, f"Found DELETE on replay_events: {lines}"

    def test_no_alert_sends(self):
        src = self._src()
        assert "send_alert" not in src


# ── API tests ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
    monkeypatch.setattr(hr, "_ensure_tables", lambda: None)
    from api import api_bp, _CACHE
    _CACHE.clear()
    from flask import Flask
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(api_bp)
    return app.test_client()


class TestApiReplayRuns:
    def test_get_runs_returns_200(self, client, monkeypatch):
        monkeypatch.setattr(hr, "get_replay_runs", lambda limit=20: [])
        resp = client.get("/api/v1/replay/runs")
        assert resp.status_code == 200

    def test_get_runs_empty_list(self, client, monkeypatch):
        monkeypatch.setattr(hr, "get_replay_runs", lambda limit=20: [])
        body = client.get("/api/v1/replay/runs").get_json()
        assert body["data"]["runs"] == []

    def test_get_run_not_found_returns_404(self, client, monkeypatch):
        monkeypatch.setattr(hr, "get_replay_run", lambda run_id: None)
        resp = client.get("/api/v1/replay/runs/RPL-FAKE")
        assert resp.status_code == 404

    def test_get_run_found_returns_200(self, client, monkeypatch):
        fake_run = {"run_id": "RPL-TEST", "status": "COMPLETE",
                    "event_count": 5, "summary": {}, "start_date": "2026-01-01",
                    "end_date": "2026-06-01", "ticker_filter": None,
                    "source_filter": None, "setup_type_filter": None,
                    "max_rows": 500, "created_at": "2026-01-01"}
        monkeypatch.setattr(hr, "get_replay_run", lambda run_id: fake_run)
        resp = client.get("/api/v1/replay/runs/RPL-TEST")
        assert resp.status_code == 200


class TestApiReplayRunCreate:
    def test_post_requires_auth(self, client, monkeypatch):
        monkeypatch.setenv("API_SECRET", "secret")
        resp = client.post(
            "/api/v1/replay/run",
            json={"start_date": "2026-01-01", "end_date": "2026-06-01"},
        )
        assert resp.status_code == 401

    def test_post_with_auth_returns_200(self, client, monkeypatch):
        monkeypatch.setenv("API_SECRET", "secret")
        fake_result = {
            "run_id": "RPL-TEST", "status": "COMPLETE",
            "event_count": 0, "summary": {}, "events": [],
        }
        monkeypatch.setattr(hr, "run_replay", lambda params: fake_result)
        resp = client.post(
            "/api/v1/replay/run",
            json={"start_date": "2026-01-01", "end_date": "2026-06-01"},
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200

    def test_post_with_auth_returns_run_id(self, client, monkeypatch):
        monkeypatch.setenv("API_SECRET", "secret")
        fake_result = {
            "run_id": "RPL-ABC", "status": "COMPLETE",
            "event_count": 0, "summary": {}, "events": [],
        }
        monkeypatch.setattr(hr, "run_replay", lambda params: fake_result)
        body = client.post(
            "/api/v1/replay/run",
            json={"start_date": "2026-01-01", "end_date": "2026-06-01"},
            headers={"Authorization": "Bearer secret"},
        ).get_json()
        assert body["data"]["run_id"] == "RPL-ABC"

    def test_invalid_params_returns_400(self, client, monkeypatch):
        monkeypatch.setenv("API_SECRET", "secret")
        monkeypatch.setattr(
            hr, "run_replay",
            lambda p: (_ for _ in ()).throw(ValueError("bad dates"))
        )
        resp = client.post(
            "/api/v1/replay/run",
            json={"start_date": "2026-06-01", "end_date": "2026-01-01"},
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 400


class TestApiReplayRunEvents:
    def test_events_not_found_returns_404(self, client, monkeypatch):
        monkeypatch.setattr(hr, "get_replay_run", lambda run_id: None)
        resp = client.get("/api/v1/replay/runs/RPL-FAKE/events")
        assert resp.status_code == 404

    def test_events_found_returns_200(self, client, monkeypatch):
        fake_run = {"run_id": "RPL-TEST", "status": "COMPLETE",
                    "event_count": 1, "summary": {}}
        monkeypatch.setattr(hr, "get_replay_run", lambda run_id: fake_run)
        monkeypatch.setattr(hr, "get_replay_events", lambda run_id, limit=200: [])
        resp = client.get("/api/v1/replay/runs/RPL-TEST/events")
        assert resp.status_code == 200

    def test_events_limit_param(self, client, monkeypatch):
        received = {}
        fake_run = {"run_id": "RPL-TEST", "status": "COMPLETE", "summary": {}}
        monkeypatch.setattr(hr, "get_replay_run", lambda run_id: fake_run)
        def _fake_events(run_id, limit=200):
            received["limit"] = limit
            return []
        monkeypatch.setattr(hr, "get_replay_events", _fake_events)
        client.get("/api/v1/replay/runs/RPL-TEST/events?limit=10")
        assert received.get("limit") == 10

    def test_events_limit_capped(self, client, monkeypatch):
        received = {}
        fake_run = {"run_id": "RPL-TEST", "status": "COMPLETE", "summary": {}}
        monkeypatch.setattr(hr, "get_replay_run", lambda run_id: fake_run)
        def _fake_events(run_id, limit=200):
            received["limit"] = limit
            return []
        monkeypatch.setattr(hr, "get_replay_events", _fake_events)
        client.get("/api/v1/replay/runs/RPL-TEST/events?limit=9999")
        assert received.get("limit") == 2000
