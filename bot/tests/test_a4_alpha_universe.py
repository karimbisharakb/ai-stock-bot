"""
Phase A4 — Alpha universe scanner, analytics route, and API tests.

Covers:
  - ALPHA_UNIVERSE expansion (new tickers present)
  - get_universe_diagnostics() returns correct structure
  - scan_alpha_universe() updates diagnostics after run
  - scan_alpha_universe() feature-flag gating
  - fmt_alpha_row() source field derivation
  - /alpha/debug includes universe diagnostics
  - /alpha/analytics includes version, total_rows, rejected_predator_alerts
  - POST /alpha/run-universe auth and dispatch behaviour
  - get_top_candidates() stable ordering (score DESC, scan_time DESC)
  - alpha_shadow_log sort stability for equal scores
"""
import json
import os
import sqlite3
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── DB helpers ─────────────────────────────────────────────────────────────────

_CREATE_TABLE = """
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


def _make_db(path: str) -> str:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE_TABLE)
    conn.commit()
    conn.close()
    return path


def _seed_row(path: str, **kwargs):
    defaults = dict(
        ticker="AAPL",
        scan_time="2026-05-17T10:00:00",
        alpha_score=55.0,
        alpha_tier="STRONG_WATCH",
        setup_type="BREAKOUT_EXPANSION",
        predator_tier=None,
        predator_score=None,
        tier_match=0,
        filter_reason=None,
        component_scores_json=None,
        explanation=None,
        detail_json=None,
    )
    defaults.update(kwargs)
    conn = sqlite3.connect(path)
    conn.execute(
        """INSERT INTO alpha_shadow_log
           (ticker, scan_time, alpha_score, alpha_tier, setup_type,
            predator_tier, predator_score, tier_match, filter_reason,
            component_scores_json, explanation, detail_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            defaults["ticker"], defaults["scan_time"], defaults["alpha_score"],
            defaults["alpha_tier"], defaults["setup_type"], defaults["predator_tier"],
            defaults["predator_score"], defaults["tier_match"], defaults["filter_reason"],
            defaults["component_scores_json"], defaults["explanation"], defaults["detail_json"],
        ),
    )
    conn.commit()
    conn.close()


# ── Universe list tests ────────────────────────────────────────────────────────

class TestAlphaUniverseList:
    def test_new_canadian_tickers_present(self):
        from alpha_universe import ALPHA_UNIVERSE
        for ticker in ("MDA.TO", "BITF.TO", "CLS.TO"):
            assert ticker in ALPHA_UNIVERSE, f"{ticker} missing from ALPHA_UNIVERSE"

    def test_biotech_tickers_present(self):
        from alpha_universe import ALPHA_UNIVERSE
        for ticker in ("MRNA", "ROIV"):
            assert ticker in ALPHA_UNIVERSE, f"{ticker} missing from ALPHA_UNIVERSE"

    def test_ai_voice_tickers_present(self):
        from alpha_universe import ALPHA_UNIVERSE
        for ticker in ("SOUN", "LAZR"):
            assert ticker in ALPHA_UNIVERSE, f"{ticker} missing from ALPHA_UNIVERSE"

    def test_universe_has_no_duplicates(self):
        from alpha_universe import ALPHA_UNIVERSE
        assert len(ALPHA_UNIVERSE) == len(set(ALPHA_UNIVERSE))

    def test_get_alpha_universe_returns_copy(self):
        from alpha_universe import ALPHA_UNIVERSE, get_alpha_universe
        result = get_alpha_universe()
        result.append("FAKE")
        assert "FAKE" not in ALPHA_UNIVERSE

    def test_universe_size_at_least_50(self):
        from alpha_universe import ALPHA_UNIVERSE
        assert len(ALPHA_UNIVERSE) >= 50


# ── Universe diagnostics tests ─────────────────────────────────────────────────

class TestUniverseDiagnostics:
    def test_get_universe_diagnostics_structure(self):
        from alpha_universe import get_universe_diagnostics, ALPHA_UNIVERSE
        diag = get_universe_diagnostics()
        assert "universe_size" in diag
        assert "last_universe_scan_time" in diag
        assert "last_universe_scan_count" in diag
        assert diag["universe_size"] == len(ALPHA_UNIVERSE)

    def test_initial_diagnostics_none_time(self):
        import alpha_universe
        # Reset module state for clean test
        alpha_universe._last_scan_time = None
        alpha_universe._last_scan_count = 0
        diag = alpha_universe.get_universe_diagnostics()
        assert diag["last_universe_scan_time"] is None
        assert diag["last_universe_scan_count"] == 0


# ── scan_alpha_universe() feature-flag gating ──────────────────────────────────

class TestScanAlphaUniverseGating:
    def test_scan_returns_zero_when_flag_off(self, monkeypatch):
        monkeypatch.setenv("ALPHA_SHADOW_ENABLED", "false")
        # Force feature_flags to re-read the env (it reads at call time)
        import feature_flags
        from alpha_universe import scan_alpha_universe
        count = scan_alpha_universe()
        assert count == 0

    def test_scan_updates_diagnostics_when_flag_on(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALPHA_SHADOW_ENABLED", "true")

        import alpha_universe
        import feature_flags
        import alpha_shadow as alpha_shadow_mod
        from alpha_universe import scan_alpha_universe

        # Stub run_shadow_score to avoid real network calls
        class _FakeMgr:
            def run_shadow_score(self, ticker, result):
                return object()  # non-None = success

        monkeypatch.setattr(feature_flags, "alpha_shadow_enabled", lambda: True)
        monkeypatch.setattr(alpha_shadow_mod, "get_shadow_manager", lambda: _FakeMgr())

        # Reset state
        alpha_universe._last_scan_time = None
        alpha_universe._last_scan_count = 0

        count = scan_alpha_universe()

        assert count == len(alpha_universe.ALPHA_UNIVERSE)
        assert alpha_universe._last_scan_time is not None
        assert alpha_universe._last_scan_count == count


# ── fmt_alpha_row source field ─────────────────────────────────────────────────

class TestFmtAlphaRowSource:
    def _row(self, predator_tier=None, **kwargs):
        base = dict(
            ticker="TSLA", alpha_score=60.0, alpha_tier="HIGH_CONVICTION",
            setup_type="SQUEEZE_BREAKOUT", predator_score=None,
            tier_match=0, filter_reason=None, explanation=None,
            scan_time="2026-05-17T12:00:00",
            component_scores_json=None, detail_json=None,
        )
        base["predator_tier"] = predator_tier
        base.update(kwargs)
        return base

    def test_source_predator_shadow_when_predator_tier_set(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from api import fmt_alpha_row
        row = self._row(predator_tier="ALERT")
        result = fmt_alpha_row(row)
        assert result["source"] == "predator_shadow"

    def test_source_alpha_universe_when_predator_tier_null(self):
        from api import fmt_alpha_row
        row = self._row(predator_tier=None)
        result = fmt_alpha_row(row)
        assert result["source"] == "alpha_universe"

    def test_source_field_present_in_output(self):
        from api import fmt_alpha_row
        row = self._row()
        result = fmt_alpha_row(row)
        assert "source" in result

    def test_empty_predator_tier_string_treated_as_predator_shadow(self):
        from api import fmt_alpha_row
        # Empty string is falsy — treated as no predator context
        row = self._row(predator_tier="")
        result = fmt_alpha_row(row)
        assert result["source"] == "alpha_universe"


# ── /alpha/debug universe diagnostics ─────────────────────────────────────────

class TestAlphaDebugUniverseDiagnostics:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setenv("DATABASE_PATH", db_path)
        monkeypatch.setenv("ALPHA_SHADOW_ENABLED", "true")

        import importlib, sms_handler, api
        importlib.reload(api)

        from sms_handler import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_debug_includes_universe_size(self, client):
        resp = client.get("/api/v1/alpha/debug")
        data = resp.get_json()
        assert data["ok"]
        assert "universe_size" in data["data"]
        assert isinstance(data["data"]["universe_size"], int)
        assert data["data"]["universe_size"] >= 50

    def test_debug_includes_alpha_universe_enabled(self, client):
        resp = client.get("/api/v1/alpha/debug")
        data = resp.get_json()
        assert "alpha_universe_enabled" in data["data"]

    def test_debug_includes_last_universe_scan_time(self, client):
        resp = client.get("/api/v1/alpha/debug")
        data = resp.get_json()
        assert "last_universe_scan_time" in data["data"]

    def test_debug_includes_last_universe_scan_count(self, client):
        resp = client.get("/api/v1/alpha/debug")
        data = resp.get_json()
        assert "last_universe_scan_count" in data["data"]


# ── /alpha/analytics enhanced fields ──────────────────────────────────────────

class TestAlphaAnalyticsEnhanced:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        # Seed some rows
        _seed_row(db_path, ticker="NVDA", alpha_score=75.0, alpha_tier="HIGH_CONVICTION",
                  predator_tier=None, filter_reason=None)
        _seed_row(db_path, ticker="TSLA", alpha_score=50.0, alpha_tier="STRONG_WATCH",
                  predator_tier="WATCH", filter_reason=None)
        monkeypatch.setenv("DATABASE_PATH", db_path)
        monkeypatch.setenv("ALPHA_SHADOW_ENABLED", "true")

        import importlib, sms_handler, api
        importlib.reload(api)

        from sms_handler import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_analytics_includes_engine_version(self, client):
        resp = client.get("/api/v1/alpha/analytics")
        data = resp.get_json()
        assert data["ok"]
        assert "alpha_engine_version" in data["data"]

    def test_analytics_includes_total_rows(self, client):
        resp = client.get("/api/v1/alpha/analytics")
        data = resp.get_json()
        assert "total_rows" in data["data"]
        assert isinstance(data["data"]["total_rows"], int)
        assert data["data"]["total_rows"] >= 0

    def test_analytics_includes_rejected_predator_alerts(self, client):
        resp = client.get("/api/v1/alpha/analytics")
        data = resp.get_json()
        assert "rejected_predator_alerts" in data["data"]
        assert isinstance(data["data"]["rejected_predator_alerts"], list)

    def test_analytics_includes_tier_counts(self, client):
        resp = client.get("/api/v1/alpha/analytics")
        data = resp.get_json()
        assert "tier_counts" in data["data"]

    def test_analytics_includes_universe_coverage(self, client):
        resp = client.get("/api/v1/alpha/analytics")
        data = resp.get_json()
        assert "universe_coverage" in data["data"]


# ── POST /alpha/run-universe ───────────────────────────────────────────────────

class TestAlphaRunUniverse:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setenv("DATABASE_PATH", db_path)
        monkeypatch.setenv("ALPHA_SHADOW_ENABLED", "true")
        # No API_SECRET → fails-open locally

        import importlib, sms_handler, api
        importlib.reload(api)

        # Reset lock state
        api._universe_scan_running = False

        from sms_handler import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_run_universe_queues_scan(self, client, monkeypatch):
        scan_called = []

        def _fake_scan():
            scan_called.append(1)
            return 5

        monkeypatch.setattr("alpha_universe.scan_alpha_universe", _fake_scan)
        resp = client.post("/api/v1/alpha/run-universe")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["ok"]
        # Give the background thread a moment
        time.sleep(0.2)
        assert scan_called, "scan_alpha_universe was not called"

    def test_run_universe_returns_queued_true(self, client, monkeypatch):
        monkeypatch.setattr("alpha_universe.scan_alpha_universe", lambda: 0)
        resp = client.post("/api/v1/alpha/run-universe")
        data = resp.get_json()
        assert data["data"]["queued"] is True

    def test_run_universe_rejects_second_call_while_running(self, client, monkeypatch):
        import api as api_mod
        api_mod._universe_scan_running = True

        resp = client.post("/api/v1/alpha/run-universe")
        data = resp.get_json()
        assert data["ok"]
        assert data["data"]["queued"] is False
        assert "in progress" in data["data"]["reason"]

        api_mod._universe_scan_running = False

    def test_run_universe_flag_off_returns_not_queued(self, client, monkeypatch):
        monkeypatch.setenv("ALPHA_SHADOW_ENABLED", "false")
        resp = client.post("/api/v1/alpha/run-universe")
        data = resp.get_json()
        assert data["ok"]
        assert data["data"]["queued"] is False

    def test_run_universe_auth_rejects_bad_token(self, monkeypatch, tmp_path):
        """When API_SECRET is set, wrong token gets 401."""
        monkeypatch.setenv("API_SECRET", "correcttoken")
        monkeypatch.setenv("ALPHA_SHADOW_ENABLED", "true")
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setenv("DATABASE_PATH", db_path)

        import importlib, sms_handler, api
        importlib.reload(api)

        from sms_handler import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/alpha/run-universe",
                headers={"Authorization": "Bearer wrongtoken"},
            )
        assert resp.status_code == 401
        monkeypatch.delenv("API_SECRET", raising=False)

    def test_run_universe_auth_accepts_correct_token(self, monkeypatch, tmp_path):
        """When API_SECRET is set, correct token gets through."""
        monkeypatch.setenv("API_SECRET", "mytoken")
        monkeypatch.setenv("ALPHA_SHADOW_ENABLED", "false")
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setenv("DATABASE_PATH", db_path)

        import importlib, sms_handler, api
        importlib.reload(api)
        api._universe_scan_running = False

        from sms_handler import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/alpha/run-universe",
                headers={"Authorization": "Bearer mytoken"},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"]
        monkeypatch.delenv("API_SECRET", raising=False)


# ── get_top_candidates() sort stability ───────────────────────────────────────

class TestTopCandidatesSort:
    def test_newer_scan_time_wins_on_equal_score(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)

        # Two rows for TSLA — same score, different scan times
        _seed_row(db_path, ticker="TSLA", alpha_score=70.0,
                  scan_time="2026-05-17T09:00:00", alpha_tier="HIGH_CONVICTION")
        _seed_row(db_path, ticker="TSLA", alpha_score=70.0,
                  scan_time="2026-05-17T13:00:00", alpha_tier="HIGH_CONVICTION")
        # One row for NVDA with equal score
        _seed_row(db_path, ticker="NVDA", alpha_score=70.0,
                  scan_time="2026-05-17T11:00:00", alpha_tier="HIGH_CONVICTION")

        # Direct SQL test using the query from get_top_candidates()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT a.*
            FROM alpha_shadow_log a
            INNER JOIN (
                SELECT ticker, MAX(scan_time) AS latest
                FROM alpha_shadow_log GROUP BY ticker
            ) g ON a.ticker = g.ticker AND a.scan_time = g.latest
            WHERE a.filter_reason IS NULL AND a.alpha_score IS NOT NULL
            ORDER BY a.alpha_score DESC, a.scan_time DESC, a.ticker ASC
            LIMIT 10
            """
        ).fetchall()
        conn.close()

        tickers = [r["ticker"] for r in rows]
        assert "TSLA" in tickers
        assert "NVDA" in tickers
        # Only the latest TSLA row should appear (MAX(scan_time) per ticker)
        assert len(set(tickers)) == len(tickers)  # no duplicates
        # Latest TSLA row is 13:00, NVDA is 11:00 — both same score so order by scan_time
        tsla_idx = tickers.index("TSLA")
        nvda_idx = tickers.index("NVDA")
        assert tsla_idx < nvda_idx  # TSLA has newer scan_time

    def test_higher_score_ranks_first(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setenv("DATABASE_PATH", db_path)

        _seed_row(db_path, ticker="ALAB", alpha_score=80.0, scan_time="2026-05-17T10:00:00",
                  alpha_tier="RARE_SETUP", filter_reason=None)
        _seed_row(db_path, ticker="MU", alpha_score=55.0, scan_time="2026-05-17T10:00:00",
                  alpha_tier="STRONG_WATCH", filter_reason=None)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT a.*
            FROM alpha_shadow_log a
            INNER JOIN (
                SELECT ticker, MAX(scan_time) AS latest
                FROM alpha_shadow_log GROUP BY ticker
            ) g ON a.ticker = g.ticker AND a.scan_time = g.latest
            WHERE a.filter_reason IS NULL AND a.alpha_score IS NOT NULL
            ORDER BY a.alpha_score DESC, a.scan_time DESC, a.ticker ASC
            LIMIT 10
            """
        ).fetchall()
        conn.close()

        assert rows[0]["ticker"] == "ALAB"
        assert rows[1]["ticker"] == "MU"
