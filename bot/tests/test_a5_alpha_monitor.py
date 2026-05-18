"""
Phase A5 — Alpha monitoring report, outcome tracking, and learning tests.

Covers:
  - generate_alpha_report() structure and resilience
  - diagnose_alpha_quality() deterministic rules
  - get_recommendations() for each issue code
  - insert_pending_outcomes() idempotency and eligibility filtering
  - update_outcome_prices() return calculations and status transitions
  - _all_windows_filled() completeness check
  - compute_learning_analytics() with sparse/full datasets
  - get_learning_dataset() join correctness
  - API: GET /alpha/report, /alpha/outcomes, /alpha/learning
  - No alpha alerts fired anywhere
  - Scheduled tasks registered without errors
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── DB helpers ─────────────────────────────────────────────────────────────────

_SHADOW_DDL = """
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
)
"""

_OUTCOMES_DDL = """
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


def _make_db(path: str) -> str:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(_SHADOW_DDL)
    conn.execute(_OUTCOMES_DDL)
    conn.commit()
    conn.close()
    return path


def _make_get_conn(db_path: str):
    """Return a get_connection factory that connects to the test db."""
    def _get_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    return _get_conn


def _seed_shadow(path: str, **kwargs):
    defaults = dict(
        ticker="AAPL", scan_time="2026-05-17T10:00:00",
        alpha_score=55.0, alpha_tier="STRONG_WATCH", setup_type="BREAKOUT_EXPANSION",
        predator_tier=None, predator_score=None, tier_match=0,
        filter_reason=None, component_scores_json=None, explanation=None, detail_json=None,
    )
    defaults.update(kwargs)
    conn = sqlite3.connect(path)
    conn.execute(
        """INSERT INTO alpha_shadow_log
           (ticker, scan_time, alpha_score, alpha_tier, setup_type, predator_tier,
            predator_score, tier_match, filter_reason, component_scores_json, explanation, detail_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (defaults["ticker"], defaults["scan_time"], defaults["alpha_score"],
         defaults["alpha_tier"], defaults["setup_type"], defaults["predator_tier"],
         defaults["predator_score"], defaults["tier_match"], defaults["filter_reason"],
         defaults["component_scores_json"], defaults["explanation"], defaults["detail_json"]),
    )
    conn.commit()
    conn.close()


def _seed_outcome(path: str, **kwargs):
    defaults = dict(
        ticker="AAPL", scan_time="2026-05-17T10:00:00",
        alpha_score=55.0, alpha_tier="STRONG_WATCH", setup_type="BREAKOUT_EXPANSION",
        source="alpha_universe", component_scores_json=None,
        price_at_scan=100.0, price_1d=None, price_3d=None, price_5d=None,
        price_10d=None, price_20d=None,
        return_1d=None, return_3d=None, return_5d=None, return_10d=None, return_20d=None,
        max_gain=None, max_drawdown=None, status="PENDING", created_at="2026-05-17T10:00:00",
        updated_at=None,
    )
    defaults.update(kwargs)
    conn = sqlite3.connect(path)
    conn.execute(
        """INSERT OR IGNORE INTO alpha_outcomes
           (ticker, scan_time, alpha_score, alpha_tier, setup_type, source,
            component_scores_json, price_at_scan, price_1d, price_3d, price_5d,
            price_10d, price_20d, return_1d, return_3d, return_5d, return_10d, return_20d,
            max_gain, max_drawdown, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (defaults["ticker"], defaults["scan_time"], defaults["alpha_score"],
         defaults["alpha_tier"], defaults["setup_type"], defaults["source"],
         defaults["component_scores_json"], defaults["price_at_scan"],
         defaults["price_1d"], defaults["price_3d"], defaults["price_5d"],
         defaults["price_10d"], defaults["price_20d"],
         defaults["return_1d"], defaults["return_3d"], defaults["return_5d"],
         defaults["return_10d"], defaults["return_20d"],
         defaults["max_gain"], defaults["max_drawdown"],
         defaults["status"], defaults["created_at"], defaults["updated_at"]),
    )
    conn.commit()
    conn.close()


def _read_outcome(db_path: str, ticker: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM alpha_outcomes WHERE ticker = ?", (ticker,)).fetchone()
    conn.close()
    return dict(row) if row else {}


# ── diagnose_alpha_quality ────────────────────────────────────────────────────

class TestDiagnoseAlphaQuality:
    def _run(self, tier_counts, data_quality=None, coverage=None):
        from alpha_monitor import diagnose_alpha_quality
        dq = data_quality or {
            "missing_catalyst_rate": 0.1, "missing_options_rate": 0.2,
            "missing_risk_reward_rate": 0.1, "stale_count": 0, "stale_tickers": [],
        }
        cov = coverage or {"universe_size": 50, "covered_in_db": 40, "missing": []}
        return diagnose_alpha_quality(tier_counts, dq, cov)

    def test_no_data_returns_no_data_issue(self):
        issues = self._run({})
        assert any(i["code"] == "NO_DATA" for i in issues)
        assert issues[0]["severity"] == "HIGH"

    def test_high_ignore_rate_flags_too_strict(self):
        issues = self._run({"IGNORE": 70, "WATCH": 20, "STRONG_WATCH": 5, "HIGH_CONVICTION": 5})
        assert any(i["code"] == "TOO_STRICT" for i in issues)

    def test_ignore_rate_below_threshold_no_too_strict(self):
        issues = self._run({"IGNORE": 30, "WATCH": 30, "STRONG_WATCH": 20, "HIGH_CONVICTION": 20})
        assert not any(i["code"] == "TOO_STRICT" for i in issues)

    def test_no_high_quality_setups_flags_no_high_conviction(self):
        issues = self._run({"IGNORE": 30, "WATCH": 70})
        assert any(i["code"] == "NO_HIGH_CONVICTION" for i in issues)

    def test_healthy_distribution_no_issues(self):
        issues = self._run({
            "IGNORE": 10, "WATCH": 20, "STRONG_WATCH": 40,
            "HIGH_CONVICTION": 20, "RARE_SETUP": 10,
        })
        assert issues == []

    def test_high_missing_catalyst_rate(self):
        dq = {"missing_catalyst_rate": 0.75, "missing_options_rate": 0.5,
              "missing_risk_reward_rate": 0.2, "stale_count": 0, "stale_tickers": []}
        issues = self._run({"IGNORE": 10, "WATCH": 40, "STRONG_WATCH": 50}, data_quality=dq)
        assert any(i["code"] == "MISSING_CATALYST" for i in issues)

    def test_high_missing_options_rate(self):
        dq = {"missing_catalyst_rate": 0.1, "missing_options_rate": 0.9,
              "missing_risk_reward_rate": 0.1, "stale_count": 0, "stale_tickers": []}
        issues = self._run({"IGNORE": 10, "WATCH": 40, "STRONG_WATCH": 50}, data_quality=dq)
        assert any(i["code"] == "MISSING_OPTIONS" for i in issues)

    def test_stale_data_issue(self):
        dq = {"missing_catalyst_rate": 0.1, "missing_options_rate": 0.1,
              "missing_risk_reward_rate": 0.1, "stale_count": 15, "stale_tickers": []}
        cov = {"universe_size": 50, "covered_in_db": 40, "missing": []}
        issues = self._run({"IGNORE": 5, "WATCH": 20, "STRONG_WATCH": 75},
                           data_quality=dq, coverage=cov)
        assert any(i["code"] == "STALE_DATA" for i in issues)

    def test_coverage_gap_issue(self):
        cov = {"universe_size": 50, "covered_in_db": 20, "missing": list(range(30))}
        issues = self._run({"IGNORE": 5, "WATCH": 15}, coverage=cov)
        assert any(i["code"] == "COVERAGE_GAP" for i in issues)

    def test_too_loose_not_flagged_when_too_strict(self):
        issues = self._run({"IGNORE": 80, "WATCH": 20})
        codes = {i["code"] for i in issues}
        assert "TOO_STRICT" in codes
        assert "TOO_LOOSE" not in codes

    def test_missing_risk_reward_rate_issue(self):
        dq = {"missing_catalyst_rate": 0.1, "missing_options_rate": 0.1,
              "missing_risk_reward_rate": 0.65, "stale_count": 0, "stale_tickers": []}
        issues = self._run({"IGNORE": 5, "WATCH": 20, "STRONG_WATCH": 75}, data_quality=dq)
        assert any(i["code"] == "MISSING_RISK_REWARD" for i in issues)


# ── get_recommendations ───────────────────────────────────────────────────────

class TestGetRecommendations:
    def _issue(self, code):
        return {"code": code, "severity": "MEDIUM", "message": "test"}

    def test_no_data_recommendation(self):
        from alpha_monitor import get_recommendations
        recs = get_recommendations([self._issue("NO_DATA")])
        assert any("ALPHA_SHADOW_ENABLED" in r for r in recs)

    def test_too_strict_recommendation(self):
        from alpha_monitor import get_recommendations
        recs = get_recommendations([self._issue("TOO_STRICT")])
        assert any("35.0" in r or "threshold" in r.lower() for r in recs)

    def test_missing_catalyst_recommendation(self):
        from alpha_monitor import get_recommendations
        recs = get_recommendations([self._issue("MISSING_CATALYST")])
        assert any("catalyst" in r.lower() or "earnings" in r.lower() for r in recs)

    def test_coverage_gap_recommendation(self):
        from alpha_monitor import get_recommendations
        recs = get_recommendations([self._issue("COVERAGE_GAP")])
        assert any("run-universe" in r or "backfill" in r.lower() for r in recs)

    def test_no_issues_no_recommendations(self):
        from alpha_monitor import get_recommendations
        assert get_recommendations([]) == []

    def test_multiple_issues_multiple_recs(self):
        from alpha_monitor import get_recommendations
        recs = get_recommendations([
            self._issue("TOO_STRICT"), self._issue("MISSING_CATALYST"), self._issue("STALE_DATA"),
        ])
        assert len(recs) >= 3

    def test_stale_data_recommendation(self):
        from alpha_monitor import get_recommendations
        recs = get_recommendations([self._issue("STALE_DATA")])
        assert any("yfinance" in r.lower() or "universe" in r.lower() for r in recs)


# ── generate_alpha_report ─────────────────────────────────────────────────────

class TestGenerateAlphaReport:
    def test_report_has_required_keys(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        _seed_shadow(db_path, ticker="NVDA", alpha_score=70.0, alpha_tier="HIGH_CONVICTION")
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_monitor import generate_alpha_report
        report = generate_alpha_report()
        for key in ("generated_at", "errors", "summary", "top_candidates",
                    "best_non_predator", "rejected_predator_alerts",
                    "data_quality", "diagnosis", "recommendations"):
            assert key in report, f"Missing key: {key}"

    def test_report_never_raises_on_empty_db(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_monitor import generate_alpha_report
        report = generate_alpha_report()
        assert isinstance(report, dict)

    def test_report_generated_at_is_iso_string(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_monitor import generate_alpha_report
        datetime.fromisoformat(generate_alpha_report()["generated_at"])  # must not raise

    def test_report_has_no_data_diagnosis_when_empty(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_monitor import generate_alpha_report
        report = generate_alpha_report()
        assert any(i["code"] == "NO_DATA" for i in report["diagnosis"])

    def test_report_summary_includes_tier_distribution(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        _seed_shadow(db_path, ticker="NVDA", alpha_score=70.0, alpha_tier="HIGH_CONVICTION")
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_monitor import generate_alpha_report
        report = generate_alpha_report()
        assert isinstance(report["summary"]["tier_distribution"], dict)

    def test_report_resilient_to_malformed_component_scores_json(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        _seed_shadow(db_path, ticker="X", alpha_score=50.0, alpha_tier="WATCH",
                     component_scores_json="{{invalid")
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_monitor import generate_alpha_report
        report = generate_alpha_report()  # must not raise
        assert isinstance(report, dict)


# ── insert_pending_outcomes ───────────────────────────────────────────────────

class TestInsertPendingOutcomes:
    def _c(self, ticker="NVDA", scan_time="2026-05-17T10:00:00",
           alpha_score=70.0, alpha_tier="HIGH_CONVICTION", predator_tier=None, **kw):
        d = dict(ticker=ticker, scan_time=scan_time, alpha_score=alpha_score,
                 alpha_tier=alpha_tier, predator_tier=predator_tier,
                 setup_type="BREAKOUT_EXPANSION", component_scores_json=None)
        d.update(kw)
        return d

    def test_inserts_eligible_candidates(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_outcomes import insert_pending_outcomes
        with patch("alpha_outcomes._fetch_current_price", return_value=150.0):
            n = insert_pending_outcomes([self._c()])
        assert n == 1

    def test_idempotent_on_duplicate(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_outcomes import insert_pending_outcomes
        c = self._c()
        with patch("alpha_outcomes._fetch_current_price", return_value=150.0):
            first  = insert_pending_outcomes([c])
            second = insert_pending_outcomes([c])
        assert first  == 1
        assert second == 0

    def test_ignores_watch_tier(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_outcomes import insert_pending_outcomes
        with patch("alpha_outcomes._fetch_current_price", return_value=100.0):
            n = insert_pending_outcomes([self._c(alpha_tier="WATCH")])
        assert n == 0

    def test_ignores_ignore_tier(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_outcomes import insert_pending_outcomes
        with patch("alpha_outcomes._fetch_current_price", return_value=100.0):
            n = insert_pending_outcomes([self._c(alpha_tier="IGNORE", alpha_score=20.0)])
        assert n == 0

    def test_inserts_all_eligible_tiers(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_outcomes import insert_pending_outcomes
        candidates = [
            self._c("A", alpha_tier="STRONG_WATCH",   scan_time="2026-05-17T10:00:00"),
            self._c("B", alpha_tier="HIGH_CONVICTION", scan_time="2026-05-17T10:01:00"),
            self._c("C", alpha_tier="RARE_SETUP",      scan_time="2026-05-17T10:02:00"),
            self._c("D", alpha_tier="WATCH",           scan_time="2026-05-17T10:03:00"),
        ]
        with patch("alpha_outcomes._fetch_current_price", return_value=100.0):
            n = insert_pending_outcomes(candidates)
        assert n == 3  # only STRONG_WATCH, HIGH_CONVICTION, RARE_SETUP

    def test_source_predator_shadow(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_outcomes import insert_pending_outcomes
        with patch("alpha_outcomes._fetch_current_price", return_value=100.0):
            insert_pending_outcomes([self._c(predator_tier="ALERT")])

        row = _read_outcome(db_path, "NVDA")
        assert row["source"] == "predator_shadow"

    def test_source_alpha_universe(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_outcomes import insert_pending_outcomes
        with patch("alpha_outcomes._fetch_current_price", return_value=100.0):
            insert_pending_outcomes([self._c(predator_tier=None)])

        row = _read_outcome(db_path, "NVDA")
        assert row["source"] == "alpha_universe"

    def test_insert_succeeds_when_price_fetch_fails(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_outcomes import insert_pending_outcomes
        with patch("alpha_outcomes._fetch_current_price", return_value=None):
            n = insert_pending_outcomes([self._c()])
        assert n == 1

    def test_empty_candidates_returns_zero(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_outcomes import insert_pending_outcomes
        assert insert_pending_outcomes([]) == 0


# ── update_outcome_prices / return calculations ───────────────────────────────

class TestUpdateOutcomePrices:
    def test_return_calculation_correct(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        recent = (datetime.now() - timedelta(days=5)).isoformat()
        _seed_outcome(db_path, ticker="TSLA", scan_time=recent,
                      price_at_scan=100.0, status="PENDING")

        fake = {1: 105.0, 3: 108.0, 5: 110.0, 10: 115.0, 20: 120.0}
        with patch("alpha_outcomes._fetch_historical_prices", return_value=fake):
            stats = __import__("alpha_outcomes").update_outcome_prices()

        assert stats["updated"] >= 1
        row = _read_outcome(db_path, "TSLA")
        assert abs(row["return_1d"]  - 0.05) < 1e-5
        assert abs(row["return_5d"]  - 0.10) < 1e-5
        assert abs(row["return_20d"] - 0.20) < 1e-5

    def test_max_gain_and_drawdown_computed(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        recent = (datetime.now() - timedelta(days=5)).isoformat()
        _seed_outcome(db_path, ticker="AMD", scan_time=recent,
                      price_at_scan=200.0, status="PENDING")

        fake = {1: 210.0, 3: 180.0, 5: 220.0, 10: 230.0, 20: 240.0}
        with patch("alpha_outcomes._fetch_historical_prices", return_value=fake):
            __import__("alpha_outcomes").update_outcome_prices()

        row = _read_outcome(db_path, "AMD")
        assert row["max_gain"]     > 0
        assert row["max_drawdown"] < 0

    def test_row_marked_complete_when_all_windows_filled(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        recent = (datetime.now() - timedelta(days=5)).isoformat()
        _seed_outcome(db_path, ticker="NVDA", scan_time=recent,
                      price_at_scan=100.0, status="PENDING")

        with patch("alpha_outcomes._fetch_historical_prices",
                   return_value={1: 101.0, 3: 102.0, 5: 103.0, 10: 104.0, 20: 105.0}):
            stats = __import__("alpha_outcomes").update_outcome_prices()

        assert stats["completed"] >= 1
        row = _read_outcome(db_path, "NVDA")
        assert row["status"] == "COMPLETE"

    def test_row_marked_stale_when_old(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        old_time = (datetime.now() - timedelta(days=40)).isoformat()
        _seed_outcome(db_path, ticker="MSTR", scan_time=old_time,
                      price_at_scan=50.0, status="PENDING")

        with patch("alpha_outcomes._fetch_historical_prices", return_value={}):
            stats = __import__("alpha_outcomes").update_outcome_prices()

        assert stats["staled"] >= 1
        row = _read_outcome(db_path, "MSTR")
        assert row["status"] == "STALE"

    def test_partial_windows_not_marked_complete(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        recent = (datetime.now() - timedelta(days=5)).isoformat()
        _seed_outcome(db_path, ticker="COIN", scan_time=recent,
                      price_at_scan=100.0, status="PENDING")

        with patch("alpha_outcomes._fetch_historical_prices",
                   return_value={1: 101.0, 3: 102.0, 5: 103.0}):  # only 3 of 5
            __import__("alpha_outcomes").update_outcome_prices()

        row = _read_outcome(db_path, "COIN")
        assert row["status"] == "PENDING"

    def test_no_pending_rows_returns_zeros(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        stats = __import__("alpha_outcomes").update_outcome_prices()
        assert stats == {"updated": 0, "completed": 0, "staled": 0}


# ── _all_windows_filled ───────────────────────────────────────────────────────

class TestAllWindowsFilled:
    def test_all_filled(self):
        from alpha_outcomes import _all_windows_filled
        assert _all_windows_filled({"price_1d": 1.0, "price_3d": 1.0, "price_5d": 1.0,
                                    "price_10d": 1.0, "price_20d": 1.0}) is True

    def test_one_missing(self):
        from alpha_outcomes import _all_windows_filled
        assert _all_windows_filled({"price_1d": 1.0, "price_3d": 1.0, "price_5d": None,
                                    "price_10d": 1.0, "price_20d": 1.0}) is False

    def test_all_missing(self):
        from alpha_outcomes import _all_windows_filled
        assert _all_windows_filled({}) is False


# ── compute_learning_analytics ────────────────────────────────────────────────

class TestComputeLearningAnalytics:
    def test_empty_dataset_returns_note(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_outcomes import compute_learning_analytics
        result = compute_learning_analytics()
        assert result["total_complete"] == 0
        assert "note" in result

    def test_complete_dataset_has_effectiveness_keys(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        for i, ticker in enumerate(["NVDA", "TSLA", "AMD"]):
            _seed_outcome(db_path, ticker=ticker, scan_time=f"2026-04-0{i+1}T10:00:00",
                          alpha_tier="STRONG_WATCH", return_5d=0.05 * (i + 1),
                          price_1d=101.0, price_3d=102.0, price_5d=103.0,
                          price_10d=104.0, price_20d=105.0, status="COMPLETE")

        from alpha_outcomes import compute_learning_analytics
        result = compute_learning_analytics()
        assert result["total_complete"] == 3
        for key in ("setup_effectiveness", "tier_effectiveness",
                    "source_effectiveness", "false_positive_rate"):
            assert key in result

    def test_false_positive_rate_calculated(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        # 1 false positive (return_5d < -5%), 1 true positive
        _seed_outcome(db_path, ticker="RIOT", scan_time="2026-04-01T10:00:00",
                      return_5d=-0.10, price_1d=95.0, price_3d=92.0, price_5d=90.0,
                      price_10d=88.0, price_20d=85.0, status="COMPLETE")
        _seed_outcome(db_path, ticker="COIN", scan_time="2026-04-01T10:00:00",
                      return_5d=0.10, price_1d=105.0, price_3d=107.0, price_5d=110.0,
                      price_10d=112.0, price_20d=115.0, status="COMPLETE")

        from alpha_outcomes import compute_learning_analytics
        result = compute_learning_analytics()
        assert result["false_positive_rate"] == 0.5

    def test_win_rate_reflects_positive_returns(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        for i in range(4):
            _seed_outcome(db_path, ticker=f"T{i}", scan_time=f"2026-04-0{i+1}T10:00:00",
                          alpha_tier="HIGH_CONVICTION",
                          return_5d=0.05 if i < 3 else -0.05,
                          price_1d=101.0, price_3d=102.0, price_5d=103.0,
                          price_10d=104.0, price_20d=105.0, status="COMPLETE")

        from alpha_outcomes import compute_learning_analytics
        result = compute_learning_analytics()
        hc = result["tier_effectiveness"].get("HIGH_CONVICTION", {})
        assert hc["win_rate"] == 0.75


# ── get_learning_dataset ──────────────────────────────────────────────────────

class TestGetLearningDataset:
    def test_returns_only_complete_rows(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        _seed_outcome(db_path, ticker="A", scan_time="2026-04-01T10:00:00",
                      status="COMPLETE", price_1d=1.0, price_3d=1.0, price_5d=1.0,
                      price_10d=1.0, price_20d=1.0)
        _seed_outcome(db_path, ticker="B", scan_time="2026-04-01T10:00:00", status="PENDING")
        _seed_outcome(db_path, ticker="C", scan_time="2026-04-01T10:00:00", status="STALE")

        from alpha_outcomes import get_learning_dataset
        rows = get_learning_dataset()
        tickers = [r["ticker"] for r in rows]
        assert "A" in tickers
        assert "B" not in tickers
        assert "C" not in tickers


# ── get_outcomes ──────────────────────────────────────────────────────────────

class TestGetOutcomes:
    def test_returns_all_rows(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        for i, status in enumerate(["PENDING", "COMPLETE", "STALE"]):
            _seed_outcome(db_path, ticker=f"T{i}", scan_time=f"2026-04-0{i+1}T10:00:00",
                          status=status)

        from alpha_outcomes import get_outcomes
        rows = get_outcomes(limit=10)
        assert len(rows) == 3

    def test_status_filter_pending(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        for i, status in enumerate(["PENDING", "COMPLETE", "PENDING"]):
            _seed_outcome(db_path, ticker=f"T{i}", scan_time=f"2026-04-0{i+1}T10:00:00",
                          status=status)

        from alpha_outcomes import get_outcomes
        rows = get_outcomes(limit=10, status="PENDING")
        assert all(r["status"] == "PENDING" for r in rows)
        assert len(rows) == 2

    def test_empty_db_returns_empty_list(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_outcomes import get_outcomes
        assert get_outcomes() == []


# ── API endpoints ─────────────────────────────────────────────────────────────

class TestAlphaA5ApiEndpoints:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        _seed_shadow(db_path, ticker="NVDA", alpha_score=75.0, alpha_tier="HIGH_CONVICTION")
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        monkeypatch.setenv("ALPHA_SHADOW_ENABLED", "true")

        import importlib, sms_handler, api
        importlib.reload(api)

        from sms_handler import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_alpha_report_returns_200(self, client):
        resp = client.get("/api/v1/alpha/report")
        assert resp.status_code == 200
        assert resp.get_json()["ok"]

    def test_alpha_report_has_diagnosis(self, client):
        data = client.get("/api/v1/alpha/report").get_json()
        assert "diagnosis" in data["data"]
        assert isinstance(data["data"]["diagnosis"], list)

    def test_alpha_report_has_recommendations(self, client):
        data = client.get("/api/v1/alpha/report").get_json()
        assert "recommendations" in data["data"]
        assert isinstance(data["data"]["recommendations"], list)

    def test_alpha_outcomes_returns_200(self, client):
        resp = client.get("/api/v1/alpha/outcomes")
        assert resp.status_code == 200
        assert resp.get_json()["ok"]

    def test_alpha_outcomes_has_results(self, client):
        data = client.get("/api/v1/alpha/outcomes").get_json()
        assert "results" in data["data"]

    def test_alpha_outcomes_status_filter_valid(self, client):
        resp = client.get("/api/v1/alpha/outcomes?status=PENDING")
        assert resp.status_code == 200

    def test_alpha_outcomes_status_filter_invalid_returns_400(self, client):
        assert client.get("/api/v1/alpha/outcomes?status=BADSTATUS").status_code == 400

    def test_alpha_learning_returns_200(self, client):
        resp = client.get("/api/v1/alpha/learning")
        assert resp.status_code == 200
        assert resp.get_json()["ok"]

    def test_alpha_learning_has_total_complete(self, client):
        data = client.get("/api/v1/alpha/learning").get_json()
        assert "total_complete" in data["data"]

    def test_no_alpha_alerts_fired(self, client, monkeypatch):
        alerts_sent = []
        monkeypatch.setattr("alerts.send_whatsapp_message",
                            lambda *a, **kw: alerts_sent.append(1), raising=False)
        client.get("/api/v1/alpha/report")
        client.get("/api/v1/alpha/outcomes")
        client.get("/api/v1/alpha/learning")
        assert alerts_sent == []


# ── Scheduled tasks ───────────────────────────────────────────────────────────

class TestScheduledTasksRegistration:
    def _get_job_ids(self, monkeypatch):
        import importlib
        import scheduler as sched_mod
        importlib.reload(sched_mod)
        for attr in ("morning_summary_job", "run_sell_monitor", "run_scanner",
                     "run_predator", "update_outcomes"):
            monkeypatch.setattr(sched_mod, attr, lambda: None)
        for attr in ("watchlist_check_job", "weekly_summary_job"):
            monkeypatch.setattr(sched_mod, attr, lambda: None, raising=False)

        scheduler = sched_mod.start_scheduler()
        if scheduler is None:
            return None
        job_ids = [j.id for j in scheduler.get_jobs()]
        scheduler.shutdown(wait=False)
        return job_ids

    def test_alpha_daily_tasks_registered(self, monkeypatch):
        ids = self._get_job_ids(monkeypatch)
        if ids is None:
            pytest.skip("Scheduler lease not available")
        assert "alpha_daily_tasks" in ids

    def test_alpha_universe_scan_registered(self, monkeypatch):
        ids = self._get_job_ids(monkeypatch)
        if ids is None:
            pytest.skip("Scheduler lease not available")
        assert "alpha_universe_scan" in ids


# ── Sparse data resilience ────────────────────────────────────────────────────

class TestSparseDataResilience:
    def test_insert_outcomes_with_no_component_scores(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        from alpha_outcomes import insert_pending_outcomes
        c = dict(ticker="SOUN", scan_time="2026-05-17T10:00:00",
                 alpha_score=60.0, alpha_tier="HIGH_CONVICTION",
                 predator_tier=None, setup_type=None, component_scores_json=None)
        with patch("alpha_outcomes._fetch_current_price", return_value=None):
            assert insert_pending_outcomes([c]) == 1

    def test_update_prices_with_no_price_at_scan(self, tmp_path, monkeypatch):
        """Return calculations skip gracefully when price_at_scan is NULL."""
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        recent = (datetime.now() - timedelta(days=5)).isoformat()
        _seed_outcome(db_path, ticker="LAZR", scan_time=recent,
                      price_at_scan=None, status="PENDING")

        with patch("alpha_outcomes._fetch_historical_prices",
                   return_value={1: 10.0, 3: 10.5, 5: 11.0, 10: 11.5, 20: 12.0}):
            __import__("alpha_outcomes").update_outcome_prices()

        row = _read_outcome(db_path, "LAZR")
        assert row["price_5d"]   == 11.0  # price stored
        assert row["return_5d"]  is None   # return not computable without denominator

    def test_compute_analytics_handles_none_return_5d(self, tmp_path, monkeypatch):
        """Rows with NULL return_5d should not cause analytics to crash."""
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))

        _seed_outcome(db_path, ticker="X", scan_time="2026-04-01T10:00:00",
                      return_5d=None, price_1d=1.0, price_3d=1.0, price_5d=1.0,
                      price_10d=1.0, price_20d=1.0, status="COMPLETE")

        from alpha_outcomes import compute_learning_analytics
        result = compute_learning_analytics()
        assert isinstance(result, dict)
        assert result["total_complete"] == 1
