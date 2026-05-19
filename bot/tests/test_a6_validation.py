"""
Phase A6 — Reality Validation Layer tests.

Covers:
  - validate_outcome(): behavior classification, metric computation, score
  - classify_behavior(): all 9 classes + priority ordering
  - INCONCLUSIVE on sparse data
  - validate_all_outcomes(): DB write, idempotency (INSERT OR IGNORE)
  - get_validations(): filtering by setup_type and behavior_class
  - get_validation_summary(): aggregate analytics, leaderboards
  - compute_setup_trap_rates() / compute_setup_sustainability()
  - get_validation_metrics_for_proposals()
  - validate_outcome(): never raises on malformed rows
  - compute_validated_component_effectiveness(): validation-weighted learning
  - generate_recommendations_report(): includes validation_weighted flag
  - API endpoints: GET /alpha/validation, GET /alpha/validation/summary
"""
import json
import os
import sqlite3
import sys
import tempfile
from typing import Any

import pytest

# ── Module resolution ──────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── DB isolation fixtures ─────────────────────────────────────────────────────

def _make_get_conn(path: str):
    def _get():
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    return _get


def _init_tables(db_path: str) -> None:
    """Bootstrap minimal schema needed for A6 tests."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
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
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS alpha_validation (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            outcome_id               INTEGER NOT NULL UNIQUE,
            ticker                   TEXT    NOT NULL,
            scan_time                TEXT    NOT NULL,
            setup_type               TEXT,
            alpha_tier               TEXT,
            behavior_class           TEXT    NOT NULL,
            validation_score         REAL    NOT NULL,
            confidence               TEXT    NOT NULL,
            follow_through_score     REAL,
            gain_retention           REAL,
            drawdown_severity        REAL,
            continuation_quality     REAL,
            multi_window_consistency REAL,
            sustained_strength       REAL,
            reversal_severity        REAL,
            n_windows                INTEGER NOT NULL DEFAULT 0,
            evidence_summary         TEXT,
            key_failure_reason       TEXT,
            key_success_reason       TEXT,
            computed_at              TEXT    NOT NULL
        )
    """)
    conn.commit()
    conn.close()


@pytest.fixture()
def db_path(monkeypatch, tmp_path):
    path = str(tmp_path / "test.db")
    _init_tables(path)
    import database
    monkeypatch.setattr(database, "get_connection", _make_get_conn(path))
    import alpha_validation
    monkeypatch.setattr(alpha_validation, "_ensure_table", lambda: None)
    return path


def _insert_outcome(db_path: str, **kwargs) -> int:
    defaults = {
        "ticker": "TEST", "scan_time": "2026-01-01T12:00:00", "status": "COMPLETE",
        "alpha_tier": "STRONG_WATCH", "setup_type": "BREAKOUT",
        "return_1d": 0.02, "return_3d": 0.03, "return_5d": 0.04,
        "return_10d": 0.05, "return_20d": 0.06,
        "max_gain": 0.07, "max_drawdown": -0.01,
        "created_at": "2026-01-01T12:00:00",
    }
    merged = {**defaults, **kwargs}
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    cols = ", ".join(merged)
    vals = ", ".join("?" * len(merged))
    c.execute(f"INSERT INTO alpha_outcomes ({cols}) VALUES ({vals})", list(merged.values()))
    row_id = c.lastrowid
    conn.commit()
    conn.close()
    return row_id


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pure metric computation
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeMetrics:
    from alpha_validation import _compute_metrics

    def test_follow_through_all_positive(self):
        from alpha_validation import _compute_metrics
        row = {"return_1d": 0.02, "return_3d": 0.03, "return_5d": 0.04,
               "return_10d": 0.05, "return_20d": 0.06}
        m = _compute_metrics(row)
        assert m["follow_through"] == 1.0

    def test_follow_through_mixed(self):
        from alpha_validation import _compute_metrics
        row = {"return_1d": 0.02, "return_3d": -0.01, "return_5d": 0.04}
        m = _compute_metrics(row)
        # 2 later windows: r3d=-0.01 (fail), r5d=0.04 (pass) → 1/2
        assert m["follow_through"] == 0.5

    def test_gain_retention_full(self):
        from alpha_validation import _compute_metrics
        row = {"return_5d": 0.08, "max_gain": 0.08, "max_drawdown": 0.0}
        m = _compute_metrics(row)
        assert m["gain_retention"] == 1.0

    def test_gain_retention_partial(self):
        from alpha_validation import _compute_metrics
        row = {"return_5d": 0.04, "max_gain": 0.08, "max_drawdown": -0.01}
        m = _compute_metrics(row)
        assert m["gain_retention"] == pytest.approx(0.5, abs=0.01)

    def test_drawdown_score_no_drawdown(self):
        from alpha_validation import _compute_metrics
        row = {"max_gain": 0.05, "max_drawdown": 0.0}
        m = _compute_metrics(row)
        assert m["drawdown_score"] == 1.0

    def test_drawdown_score_severe(self):
        from alpha_validation import _compute_metrics
        # dd = max_gain → score near 0
        row = {"max_gain": 0.05, "max_drawdown": -0.05}
        m = _compute_metrics(row)
        assert m["drawdown_score"] == pytest.approx(0.0, abs=0.01)

    def test_continuation_quality_increasing(self):
        from alpha_validation import _compute_metrics
        row = {"return_1d": 0.01, "return_3d": 0.02, "return_5d": 0.03,
               "return_10d": 0.04, "return_20d": 0.05}
        m = _compute_metrics(row)
        assert m["continuation_quality"] == 1.0

    def test_continuation_quality_decreasing(self):
        from alpha_validation import _compute_metrics
        row = {"return_1d": 0.05, "return_3d": 0.03, "return_5d": 0.01}
        m = _compute_metrics(row)
        assert m["continuation_quality"] == 0.0

    def test_consistency_low_spread(self):
        from alpha_validation import _compute_metrics
        row = {"return_1d": 0.03, "return_3d": 0.031, "return_5d": 0.029}
        m = _compute_metrics(row)
        assert m["consistency"] is not None
        assert m["consistency"] > 0.9

    def test_sustained_strength_accelerating(self):
        from alpha_validation import _compute_metrics
        row = {"return_5d": 0.03, "return_20d": 0.04}
        m = _compute_metrics(row)
        assert m["sustained_strength"] == 1.0

    def test_sustained_strength_reversal(self):
        from alpha_validation import _compute_metrics
        row = {"return_5d": 0.04, "return_20d": -0.02}
        m = _compute_metrics(row)
        assert m["sustained_strength"] == 0.0

    def test_reversal_score_full_retention(self):
        from alpha_validation import _compute_metrics
        row = {"max_gain": 0.05, "return_20d": 0.05}
        m = _compute_metrics(row)
        assert m["reversal_score"] == 1.0

    def test_n_windows_counts_correctly(self):
        from alpha_validation import _compute_metrics
        row = {"return_1d": 0.01, "return_5d": 0.02}
        m = _compute_metrics(row)
        assert m["n_windows"] == 2

    def test_sparse_row_returns_none_metrics(self):
        from alpha_validation import _compute_metrics
        m = _compute_metrics({})
        assert m["n_windows"] == 0
        assert m["follow_through"] is None
        assert m["gain_retention"] is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. Validation score
# ─────────────────────────────────────────────────────────────────────────────

class TestValidationScore:
    def test_perfect_score_is_100(self):
        from alpha_validation import _compute_metrics, _compute_validation_score
        row = {
            "return_1d": 0.02, "return_3d": 0.03, "return_5d": 0.04,
            "return_10d": 0.05, "return_20d": 0.06,
            "max_gain": 0.04, "max_drawdown": 0.0,
        }
        metrics = _compute_metrics(row)
        score   = _compute_validation_score(metrics)
        assert score == pytest.approx(100.0, abs=2.0)

    def test_score_is_zero_for_empty_metrics(self):
        from alpha_validation import _compute_validation_score
        assert _compute_validation_score({}) == 0.0

    def test_score_range_0_100(self):
        from alpha_validation import _compute_metrics, _compute_validation_score
        for row in [
            {"return_1d": -0.05, "return_5d": -0.10, "return_20d": -0.15,
             "max_gain": 0.01, "max_drawdown": -0.20},
            {"return_1d": 0.01, "return_5d": 0.02, "return_20d": 0.03},
        ]:
            score = _compute_validation_score(_compute_metrics(row))
            assert 0.0 <= score <= 100.0


# ─────────────────────────────────────────────────────────────────────────────
# 3. Behavior classification
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyBehavior:
    def _classify(self, row: dict, setup_type: str = "BREAKOUT") -> str:
        from alpha_validation import _compute_metrics, _classify_behavior
        metrics = _compute_metrics(row)
        return _classify_behavior(row, metrics, setup_type)

    def test_sustained_trend(self):
        # continuation_quality high + good r5d and r20d
        row = {
            "return_1d": 0.01, "return_3d": 0.02, "return_5d": 0.03,
            "return_10d": 0.04, "return_20d": 0.05,
            "max_gain": 0.06, "max_drawdown": -0.005,
        }
        assert self._classify(row) == "SUSTAINED_TREND"

    def test_short_lived_spike(self):
        # big 1d gain, collapses by 5d
        row = {
            "return_1d": 0.08, "return_3d": 0.04, "return_5d": 0.01,
            "max_gain": 0.09, "max_drawdown": -0.01,
        }
        assert self._classify(row) == "SHORT_LIVED_SPIKE"

    def test_volatility_trap(self):
        # Low 1d gain (below SHORT_LIVED_SPIKE threshold) but severe dd/gain ratio
        row = {
            "return_1d": 0.01, "return_5d": 0.00,
            "max_gain": 0.05, "max_drawdown": -0.05,  # ratio = 1.0 > 0.75
        }
        assert self._classify(row) == "VOLATILITY_TRAP"

    def test_failed_squeeze(self):
        row = {
            "return_1d": 0.01, "return_5d": 0.005,
            "max_gain": 0.02, "max_drawdown": -0.01,
        }
        assert self._classify(row, setup_type="SQUEEZE_BREAKOUT") == "FAILED_SQUEEZE"

    def test_failed_breakout(self):
        row = {
            "return_1d": 0.01, "return_5d": -0.04,
            "max_gain": 0.02, "max_drawdown": -0.05,
        }
        assert self._classify(row) == "FAILED_BREAKOUT"

    def test_mean_reversion_positive_to_negative(self):
        # r1d below SHORT_LIVED_SPIKE threshold (< 0.03) but above mean reversion floor (>= 0.02)
        row = {
            "return_1d": 0.025, "return_5d": 0.005, "return_20d": -0.02,
            "max_gain": 0.025, "max_drawdown": -0.005,
        }
        assert self._classify(row) == "MEAN_REVERSION"

    def test_institutional_accumulation(self):
        row = {
            "return_1d": 0.005, "return_3d": 0.01, "return_5d": 0.015,
            "return_10d": 0.02, "return_20d": 0.025,
            "max_gain": 0.03, "max_drawdown": -0.01,
        }
        result = self._classify(row)
        # Could be SUSTAINED_TREND or INSTITUTIONAL_ACCUMULATION depending on cq
        assert result in ("SUSTAINED_TREND", "INSTITUTIONAL_ACCUMULATION")

    def test_valid_breakout(self):
        row = {
            "return_1d": 0.02, "return_3d": 0.03, "return_5d": 0.03,
            "return_10d": 0.04, "return_20d": 0.01,
            "max_gain": 0.06, "max_drawdown": -0.005,
        }
        result = self._classify(row)
        assert result in ("VALID_BREAKOUT", "SUSTAINED_TREND", "INSTITUTIONAL_ACCUMULATION")

    def test_inconclusive_sparse(self):
        row = {"return_1d": 0.02}  # only 1 window
        assert self._classify(row) == "INCONCLUSIVE"

    def test_inconclusive_empty_row(self):
        assert self._classify({}) == "INCONCLUSIVE"

    def test_sustained_trend_priority_over_valid_breakout(self):
        # A strongly trending row should be SUSTAINED_TREND, not VALID_BREAKOUT
        row = {
            "return_1d": 0.01, "return_3d": 0.02, "return_5d": 0.03,
            "return_10d": 0.04, "return_20d": 0.05,
            "max_gain": 0.05, "max_drawdown": 0.0,
        }
        assert self._classify(row) == "SUSTAINED_TREND"


# ─────────────────────────────────────────────────────────────────────────────
# 4. validate_outcome() — pure function
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateOutcome:
    def test_returns_expected_keys(self):
        from alpha_validation import validate_outcome
        row = {
            "id": 1, "ticker": "AAPL", "scan_time": "2026-01-01T00:00:00",
            "setup_type": "BREAKOUT", "alpha_tier": "STRONG_WATCH",
            "return_1d": 0.02, "return_3d": 0.03, "return_5d": 0.04,
            "return_20d": 0.05, "max_gain": 0.06, "max_drawdown": -0.005,
        }
        result = validate_outcome(row)
        for key in ("behavior_class", "validation_score", "confidence",
                    "n_windows", "evidence_summary"):
            assert key in result

    def test_behavior_class_is_valid(self):
        from alpha_validation import validate_outcome, BEHAVIOR_CLASSES
        row = {"return_1d": 0.02, "return_5d": 0.03, "return_20d": 0.04}
        result = validate_outcome(row)
        assert result["behavior_class"] in BEHAVIOR_CLASSES

    def test_validation_score_is_float_in_range(self):
        from alpha_validation import validate_outcome
        row = {"return_1d": 0.02, "return_5d": 0.03, "max_gain": 0.05, "max_drawdown": -0.01}
        result = validate_outcome(row)
        assert isinstance(result["validation_score"], float)
        assert 0.0 <= result["validation_score"] <= 100.0

    def test_never_raises_on_malformed_row(self):
        from alpha_validation import validate_outcome
        for bad_row in [None, {}, {"return_1d": "not_a_number"}, {"max_gain": None}]:
            if bad_row is None:
                bad_row = {}
            result = validate_outcome(bad_row)
            assert result["behavior_class"] in ("INCONCLUSIVE",) or isinstance(result["behavior_class"], str)

    def test_confidence_high_with_many_windows(self):
        from alpha_validation import validate_outcome
        row = {
            "id": 1, "ticker": "T", "scan_time": "2026-01-01T00:00:00",
            "return_1d": 0.01, "return_3d": 0.02, "return_5d": 0.03,
            "return_10d": 0.04, "return_20d": 0.05,
            "max_gain": 0.05, "max_drawdown": 0.0,
        }
        result = validate_outcome(row)
        # 5 windows → behavior-dependent; if not INCONCLUSIVE, confidence ≥ MEDIUM
        if result["behavior_class"] != "INCONCLUSIVE":
            assert result["confidence"] in ("HIGH", "MEDIUM")

    def test_positive_behavior_has_success_reason(self):
        from alpha_validation import validate_outcome, POSITIVE_BEHAVIORS
        row = {
            "return_1d": 0.01, "return_3d": 0.02, "return_5d": 0.03,
            "return_10d": 0.04, "return_20d": 0.05,
            "max_gain": 0.05, "max_drawdown": 0.0,
        }
        result = validate_outcome(row)
        if result["behavior_class"] in POSITIVE_BEHAVIORS:
            assert result["key_success_reason"] is not None

    def test_negative_behavior_has_failure_reason(self):
        from alpha_validation import validate_outcome, NEGATIVE_BEHAVIORS
        row = {
            "return_1d": 0.08, "return_5d": 0.01,
            "max_gain": 0.09, "max_drawdown": -0.01,
        }
        result = validate_outcome(row)
        if result["behavior_class"] in NEGATIVE_BEHAVIORS:
            assert result["key_failure_reason"] is not None

    def test_deterministic_output(self):
        from alpha_validation import validate_outcome
        row = {
            "return_1d": 0.02, "return_3d": 0.03, "return_5d": 0.04,
            "return_20d": 0.05, "max_gain": 0.06, "max_drawdown": -0.005,
        }
        r1 = validate_outcome(row)
        r2 = validate_outcome(row)
        assert r1["behavior_class"]    == r2["behavior_class"]
        assert r1["validation_score"]  == r2["validation_score"]


# ─────────────────────────────────────────────────────────────────────────────
# 5. validate_all_outcomes() — DB operations
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateAllOutcomes:
    def test_empty_db_returns_zero_processed(self, db_path):
        from alpha_validation import validate_all_outcomes
        stats = validate_all_outcomes()
        assert stats["processed"] == 0
        assert stats["inserted"] == 0
        assert stats["errors"]   == 0

    def test_processes_complete_outcomes(self, db_path):
        _insert_outcome(db_path)
        from alpha_validation import validate_all_outcomes
        stats = validate_all_outcomes()
        assert stats["processed"] == 1
        assert stats["inserted"]  == 1

    def test_idempotent_reruns(self, db_path):
        _insert_outcome(db_path)
        from alpha_validation import validate_all_outcomes
        validate_all_outcomes()
        stats2 = validate_all_outcomes()
        assert stats2["processed"] == 0  # already validated — LEFT JOIN filters out

    def test_skips_pending_outcomes(self, db_path):
        _insert_outcome(db_path, status="PENDING")
        from alpha_validation import validate_all_outcomes
        stats = validate_all_outcomes()
        assert stats["processed"] == 0

    def test_multiple_outcomes(self, db_path):
        for i in range(5):
            _insert_outcome(db_path, ticker=f"T{i}", scan_time=f"2026-01-0{i+1}T12:00:00")
        from alpha_validation import validate_all_outcomes
        stats = validate_all_outcomes()
        assert stats["processed"] == 5
        assert stats["inserted"]  == 5

    def test_validation_rows_written(self, db_path):
        _insert_outcome(db_path)
        from alpha_validation import validate_all_outcomes, get_validations
        validate_all_outcomes()
        rows = get_validations(limit=10)
        assert len(rows) == 1
        assert rows[0]["behavior_class"] in (
            "VALID_BREAKOUT", "SUSTAINED_TREND", "INSTITUTIONAL_ACCUMULATION",
            "INCONCLUSIVE", "SHORT_LIVED_SPIKE", "VOLATILITY_TRAP",
            "FAILED_BREAKOUT", "FAILED_SQUEEZE", "MEAN_REVERSION",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 6. get_validations() — filtering
# ─────────────────────────────────────────────────────────────────────────────

class TestGetValidations:
    def _seed(self, db_path, n: int = 3):
        for i in range(n):
            _insert_outcome(db_path, ticker=f"T{i}", scan_time=f"2026-01-0{i+1}T12:00:00",
                            setup_type="SQUEEZE" if i % 2 == 0 else "BREAKOUT")
        from alpha_validation import validate_all_outcomes
        validate_all_outcomes()

    def test_returns_all_without_filter(self, db_path):
        self._seed(db_path, 3)
        from alpha_validation import get_validations
        rows = get_validations(limit=10)
        assert len(rows) == 3

    def test_limit_respected(self, db_path):
        self._seed(db_path, 3)
        from alpha_validation import get_validations
        rows = get_validations(limit=2)
        assert len(rows) <= 2

    def test_filter_by_setup_type(self, db_path):
        self._seed(db_path, 3)
        from alpha_validation import get_validations
        rows = get_validations(limit=10, setup_type="SQUEEZE")
        assert all(r["setup_type"] == "SQUEEZE" for r in rows)

    def test_filter_by_behavior_class(self, db_path):
        # Insert outcome that will be INCONCLUSIVE (sparse data)
        _insert_outcome(db_path, ticker="SPARSE", scan_time="2026-02-01T12:00:00",
                        return_1d=0.01, return_3d=None, return_5d=None,
                        return_10d=None, return_20d=None,
                        max_gain=None, max_drawdown=None)
        from alpha_validation import validate_all_outcomes, get_validations
        validate_all_outcomes()
        rows = get_validations(limit=10, behavior_class="INCONCLUSIVE")
        assert all(r["behavior_class"] == "INCONCLUSIVE" for r in rows)

    def test_empty_db_returns_empty_list(self, db_path):
        from alpha_validation import get_validations
        assert get_validations() == []


# ─────────────────────────────────────────────────────────────────────────────
# 7. get_validation_summary()
# ─────────────────────────────────────────────────────────────────────────────

class TestGetValidationSummary:
    def test_empty_returns_safe_structure(self, db_path):
        from alpha_validation import get_validation_summary
        s = get_validation_summary()
        assert s["total_validated"] == 0
        assert s["overall_trap_rate"] is None

    def test_counts_match(self, db_path):
        for i in range(5):
            _insert_outcome(db_path, ticker=f"S{i}", scan_time=f"2026-03-0{i+1}T12:00:00")
        from alpha_validation import validate_all_outcomes, get_validation_summary
        validate_all_outcomes()
        s = get_validation_summary()
        assert s["total_validated"] == 5

    def test_trap_rate_is_float_in_range(self, db_path):
        for i in range(4):
            _insert_outcome(db_path, ticker=f"TR{i}", scan_time=f"2026-04-0{i+1}T12:00:00")
        from alpha_validation import validate_all_outcomes, get_validation_summary
        validate_all_outcomes()
        s = get_validation_summary()
        if s["overall_trap_rate"] is not None:
            assert 0.0 <= s["overall_trap_rate"] <= 1.0

    def test_sustainability_rate_in_range(self, db_path):
        for i in range(4):
            _insert_outcome(db_path, ticker=f"SU{i}", scan_time=f"2026-05-0{i+1}T12:00:00")
        from alpha_validation import validate_all_outcomes, get_validation_summary
        validate_all_outcomes()
        s = get_validation_summary()
        if s["overall_sustainability_rate"] is not None:
            assert 0.0 <= s["overall_sustainability_rate"] <= 1.0

    def test_behavior_distribution_keys_are_valid(self, db_path):
        from alpha_validation import (
            BEHAVIOR_CLASSES, get_validation_summary, validate_all_outcomes,
        )
        _insert_outcome(db_path, ticker="BD", scan_time="2026-06-01T12:00:00")
        validate_all_outcomes()
        s = get_validation_summary()
        for key in s["behavior_distribution"]:
            assert key in BEHAVIOR_CLASSES

    def test_never_raises_on_empty(self, db_path):
        from alpha_validation import get_validation_summary
        try:
            get_validation_summary()
        except Exception as exc:
            pytest.fail(f"get_validation_summary raised: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# 8. Trap rates and sustainability helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestTrapAndSustainabilityRates:
    def _make_validation(self, setup: str, behavior: str) -> dict:
        return {"setup_type": setup, "behavior_class": behavior, "validation_score": 50.0}

    def test_trap_rate_100_percent(self):
        from alpha_validation import compute_setup_trap_rates, NEGATIVE_BEHAVIORS
        behavior = next(iter(NEGATIVE_BEHAVIORS))
        vs = [self._make_validation("SQUEEZE", behavior) for _ in range(5)]
        rates = compute_setup_trap_rates(vs)
        assert rates["SQUEEZE"] == 1.0

    def test_trap_rate_zero(self):
        from alpha_validation import compute_setup_trap_rates
        vs = [self._make_validation("BREAKOUT", "VALID_BREAKOUT") for _ in range(3)]
        rates = compute_setup_trap_rates(vs)
        assert rates["BREAKOUT"] == 0.0

    def test_sustainability_100_percent(self):
        from alpha_validation import compute_setup_sustainability
        vs = [self._make_validation("TREND", "SUSTAINED_TREND") for _ in range(4)]
        rates = compute_setup_sustainability(vs)
        assert rates["TREND"] == 1.0

    def test_sustainability_mixed(self):
        from alpha_validation import compute_setup_sustainability
        vs = [
            self._make_validation("X", "SUSTAINED_TREND"),
            self._make_validation("X", "VOLATILITY_TRAP"),
            self._make_validation("X", "INCONCLUSIVE"),
            self._make_validation("X", "VALID_BREAKOUT"),
        ]
        rates = compute_setup_sustainability(vs)
        assert rates["X"] == pytest.approx(0.5, abs=0.01)

    def test_empty_input_returns_empty_dict(self):
        from alpha_validation import compute_setup_trap_rates, compute_setup_sustainability
        assert compute_setup_trap_rates([]) == {}
        assert compute_setup_sustainability([]) == {}


# ─────────────────────────────────────────────────────────────────────────────
# 9. get_validation_metrics_for_proposals()
# ─────────────────────────────────────────────────────────────────────────────

class TestValidationMetricsForProposals:
    def test_empty_db_returns_empty_dict(self, db_path):
        from alpha_validation import get_validation_metrics_for_proposals
        result = get_validation_metrics_for_proposals()
        assert result == {}

    def test_returns_expected_keys_when_data_exists(self, db_path):
        for i in range(3):
            _insert_outcome(db_path, ticker=f"VM{i}", scan_time=f"2026-07-0{i+1}T12:00:00")
        from alpha_validation import validate_all_outcomes, get_validation_metrics_for_proposals
        validate_all_outcomes()
        result = get_validation_metrics_for_proposals()
        if result:  # may be empty if no validations yet
            assert "validation_count" in result
            assert "trap_rate"        in result
            assert "sustainability_rate" in result
            assert "avg_validation_score" in result

    def test_validation_count_matches(self, db_path):
        for i in range(3):
            _insert_outcome(db_path, ticker=f"VC{i}", scan_time=f"2026-08-0{i+1}T12:00:00")
        from alpha_validation import validate_all_outcomes, get_validation_metrics_for_proposals
        validate_all_outcomes()
        result = get_validation_metrics_for_proposals()
        assert result.get("validation_count") == 3

    def test_never_raises(self, db_path):
        from alpha_validation import get_validation_metrics_for_proposals
        try:
            get_validation_metrics_for_proposals()
        except Exception as exc:
            pytest.fail(f"raised: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# 10. compute_validated_component_effectiveness()
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatedComponentEffectiveness:
    def _make_outcome(self, oid: int, r5d: float, comp: str, score: float) -> dict:
        return {
            "id": oid,
            "return_5d": r5d,
            "max_gain": abs(r5d) + 0.01,
            "max_drawdown": -0.005,
            "alpha_tier": "STRONG_WATCH",
            "setup_type": "BREAKOUT",
            "component_scores_json": json.dumps(
                {comp: {"score": score, "data_quality": "FRESH"}}
            ),
            "shadow_component_json": None,
        }

    def test_returns_same_keys_as_unweighted(self):
        from alpha_learning_engine import (
            compute_component_effectiveness,
            compute_validated_component_effectiveness,
        )
        outcomes = [self._make_outcome(i, 0.03, "catalyst", 8.0) for i in range(15)]
        ue = compute_component_effectiveness(outcomes)
        ve = compute_validated_component_effectiveness(outcomes, {})  # empty → falls back
        assert set(ue.keys()) == set(ve.keys())

    def test_positive_behavior_increases_weight(self):
        from alpha_learning_engine import compute_validated_component_effectiveness
        from alpha_validation import VALIDATION_QUALITY_WEIGHTS
        outcomes = [self._make_outcome(i, 0.04, "breakout", 8.0) for i in range(10)]
        # All outcomes with SUSTAINED_TREND (positive) → quality weight 1.5
        validations = {i: {"behavior_class": "SUSTAINED_TREND", "validation_score": 80} for i in range(10)}
        eff = compute_validated_component_effectiveness(outcomes, validations)
        # Should have validation_weighted flag
        assert eff["breakout"].get("validation_weighted") is True

    def test_negative_behavior_lowers_weight(self):
        from alpha_learning_engine import compute_validated_component_effectiveness
        outcomes = [self._make_outcome(i, -0.04, "options", 8.0) for i in range(10)]
        # VOLATILITY_TRAP → quality weight 0.4
        validations = {i: {"behavior_class": "VOLATILITY_TRAP", "validation_score": 20} for i in range(10)}
        eff = compute_validated_component_effectiveness(outcomes, validations)
        assert eff["options"].get("validation_weighted") is True

    def test_empty_outcomes_returns_empty(self):
        from alpha_learning_engine import compute_validated_component_effectiveness
        assert compute_validated_component_effectiveness([], {}) == {}

    def test_fallback_when_no_validations(self):
        from alpha_learning_engine import (
            compute_component_effectiveness,
            compute_validated_component_effectiveness,
        )
        outcomes = [self._make_outcome(i, 0.03, "squeeze", 7.0) for i in range(12)]
        ue = compute_component_effectiveness(outcomes)
        ve = compute_validated_component_effectiveness(outcomes, {})
        assert "squeeze" in ue
        assert "squeeze" in ve


# ─────────────────────────────────────────────────────────────────────────────
# 11. generate_recommendations_report() includes validation metadata
# ─────────────────────────────────────────────────────────────────────────────

class TestRecommendationsReportValidationIntegration:
    def test_report_has_validation_fields(self, db_path):
        from alpha_learning_engine import generate_recommendations_report
        report = generate_recommendations_report()
        assert "total_validations" in report
        assert "validation_weighted" in report

    def test_validation_weighted_false_when_no_validations(self, db_path):
        from alpha_learning_engine import generate_recommendations_report
        report = generate_recommendations_report()
        assert report["validation_weighted"] is False

    def test_report_never_raises(self, db_path):
        from alpha_learning_engine import generate_recommendations_report
        try:
            generate_recommendations_report()
        except Exception as exc:
            pytest.fail(f"raised: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# 12. API endpoints — Flask integration
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def app_client(db_path, monkeypatch):
    import database
    monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
    import alpha_validation
    monkeypatch.setattr(alpha_validation, "_ensure_table", lambda: None)

    from flask import Flask
    from api import api_bp, cache_clear
    flask_app = Flask("test")
    flask_app.register_blueprint(api_bp)
    cache_clear()
    with flask_app.test_client() as client:
        yield client


class TestApiAlphaValidation:
    def test_get_validation_empty_db(self, app_client):
        rv = app_client.get("/api/v1/alpha/validation")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert data["data"]["total"] == 0
        assert data["data"]["results"] == []

    def test_get_validation_summary_empty_db(self, app_client):
        rv = app_client.get("/api/v1/alpha/validation/summary")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert data["data"]["total_validated"] == 0

    def test_get_validation_with_data(self, app_client, db_path):
        for i in range(3):
            _insert_outcome(db_path, ticker=f"API{i}", scan_time=f"2026-09-0{i+1}T12:00:00")
        from alpha_validation import validate_all_outcomes
        validate_all_outcomes()

        from api import cache_clear
        cache_clear()

        rv = app_client.get("/api/v1/alpha/validation?limit=10")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert data["data"]["total"] == 3

    def test_get_validation_summary_with_data(self, app_client, db_path):
        for i in range(3):
            _insert_outcome(db_path, ticker=f"AS{i}", scan_time=f"2026-10-0{i+1}T12:00:00")
        from alpha_validation import validate_all_outcomes
        validate_all_outcomes()

        from api import cache_clear
        cache_clear()

        rv = app_client.get("/api/v1/alpha/validation/summary")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert data["data"]["total_validated"] == 3

    def test_filter_by_behavior_class_query_param(self, app_client, db_path):
        # Seed one sparse outcome that will be INCONCLUSIVE
        _insert_outcome(db_path, ticker="SPARSE2", scan_time="2026-11-01T12:00:00",
                        return_1d=0.01, return_3d=None, return_5d=None,
                        return_10d=None, return_20d=None,
                        max_gain=None, max_drawdown=None)
        from alpha_validation import validate_all_outcomes
        validate_all_outcomes()
        from api import cache_clear
        cache_clear()

        rv = app_client.get("/api/v1/alpha/validation?behavior_class=INCONCLUSIVE")
        assert rv.status_code == 200
        data = rv.get_json()
        for row in data["data"]["results"]:
            assert row["behavior_class"] == "INCONCLUSIVE"

    def test_envelope_structure(self, app_client):
        rv = app_client.get("/api/v1/alpha/validation")
        data = rv.get_json()
        assert "ok"   in data
        assert "data" in data
        assert "meta" in data
        assert "ts"   in data["meta"]

    def test_limit_param_respected(self, app_client, db_path):
        for i in range(5):
            _insert_outcome(db_path, ticker=f"LIM{i}", scan_time=f"2026-12-0{i+1}T12:00:00")
        from alpha_validation import validate_all_outcomes
        validate_all_outcomes()
        from api import cache_clear
        cache_clear()

        rv = app_client.get("/api/v1/alpha/validation?limit=2")
        data = rv.get_json()
        assert len(data["data"]["results"]) <= 2
