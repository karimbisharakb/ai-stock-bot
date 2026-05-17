"""
Phase A2 — Alpha shadow integration tests.

Covers AlphaShadowManager analytics queries, run_shadow_score behaviour,
API endpoints, and feature-flag gating.  No network, no real DB path —
all tests use tmp_path SQLite databases.
"""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── DB helpers ────────────────────────────────────────────────────────────────

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
    explanation           TEXT
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
        predator_tier="ALERT",
        predator_score=7.5,
        tier_match=0,
        filter_reason=None,
        component_scores_json=json.dumps({"breakout": {"score": 8.0}}),
        explanation="Fresh MA200 cross with volume surge",
    )
    defaults.update(kwargs)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        INSERT INTO alpha_shadow_log
            (ticker, scan_time, alpha_score, alpha_tier, setup_type,
             predator_tier, predator_score, tier_match, filter_reason,
             component_scores_json, explanation)
        VALUES (:ticker, :scan_time, :alpha_score, :alpha_tier, :setup_type,
                :predator_tier, :predator_score, :tier_match, :filter_reason,
                :component_scores_json, :explanation)
        """,
        defaults,
    )
    conn.commit()
    conn.close()


def _patch_db(monkeypatch, manager, path: str):
    """Redirect manager's DB calls to tmp_path database."""
    import sqlite3 as sq

    def _get_conn():
        c = sq.connect(path, timeout=10)
        c.row_factory = sq.Row
        return c

    import alpha_shadow as _mod
    monkeypatch.setattr(_mod, "__builtins__", __builtins__)

    import database as _db
    monkeypatch.setattr(_db, "get_connection", _get_conn)


# ── AlphaShadowManager — analytics ────────────────────────────────────────────

class TestGetLatestResults:
    def test_empty_db_returns_empty_list(self, tmp_path, monkeypatch):
        path = _make_db(str(tmp_path / "t.db"))
        from alpha_shadow import AlphaShadowManager
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: sqlite3.connect(path))
        mgr = AlphaShadowManager()
        result = mgr.get_latest_results()
        assert result == []

    def test_returns_most_recent_per_ticker(self, tmp_path, monkeypatch):
        path = _make_db(str(tmp_path / "t.db"))
        import sqlite3 as sq
        monkeypatch.setattr(
            __import__("database"), "get_connection",
            lambda: _row_conn(sq.connect(path)),
        )
        _seed_row(path, ticker="NVDA", scan_time="2026-05-17T09:00:00", alpha_score=40.0)
        _seed_row(path, ticker="NVDA", scan_time="2026-05-17T10:00:00", alpha_score=65.0)
        _seed_row(path, ticker="AAPL", scan_time="2026-05-17T10:00:00", alpha_score=50.0)

        from alpha_shadow import AlphaShadowManager
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))

        mgr   = AlphaShadowManager()
        rows  = mgr.get_latest_results()
        tickers = [r["ticker"] for r in rows]
        # Only one row per ticker
        assert len(set(tickers)) == len(tickers)
        # NVDA most-recent score is 65
        nvda = next(r for r in rows if r["ticker"] == "NVDA")
        assert nvda["alpha_score"] == 65.0

    def test_limit_respected(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        for t in ["A", "B", "C", "D", "E"]:
            _seed_row(path, ticker=t)
        from alpha_shadow import AlphaShadowManager
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))
        rows = AlphaShadowManager().get_latest_results(limit=3)
        assert len(rows) <= 3


class TestGetTopCandidates:
    def test_excludes_filtered_rows(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="AAPL", alpha_score=80.0, filter_reason="price < $0.50")
        _seed_row(path, ticker="NVDA", alpha_score=75.0, filter_reason=None)
        from alpha_shadow import AlphaShadowManager
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))
        rows = AlphaShadowManager().get_top_candidates()
        tickers = [r["ticker"] for r in rows]
        assert "NVDA" in tickers
        assert "AAPL" not in tickers

    def test_excludes_null_score_rows(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="TSLA", alpha_score=None, filter_reason="price < $0.50")
        from alpha_shadow import AlphaShadowManager
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))
        rows = AlphaShadowManager().get_top_candidates()
        assert rows == []

    def test_ordered_by_score_desc(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="A", alpha_score=40.0)
        _seed_row(path, ticker="B", alpha_score=80.0)
        _seed_row(path, ticker="C", alpha_score=60.0)
        from alpha_shadow import AlphaShadowManager
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))
        rows   = AlphaShadowManager().get_top_candidates()
        scores = [r["alpha_score"] for r in rows]
        assert scores == sorted(scores, reverse=True)


class TestGetMismatches:
    def test_returns_only_mismatched_rows(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="AAPL", tier_match=1, alpha_tier="ALERT", predator_tier="ALERT")
        _seed_row(path, ticker="NVDA", tier_match=0, alpha_tier="HIGH_CONVICTION",
                  predator_tier="WATCH")
        from alpha_shadow import AlphaShadowManager
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))
        rows = AlphaShadowManager().get_mismatches()
        assert all(r["tier_match"] == 0 for r in rows)
        tickers = [r["ticker"] for r in rows]
        assert "NVDA" in tickers
        assert "AAPL" not in tickers

    def test_excludes_rows_with_null_tiers(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="BBAI", tier_match=0, alpha_tier=None, predator_tier="WATCH")
        from alpha_shadow import AlphaShadowManager
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))
        rows = AlphaShadowManager().get_mismatches()
        assert rows == []


class TestGetRareSetups:
    @pytest.mark.parametrize("tier", ["RARE_SETUP", "HIGH_CONVICTION"])
    def test_includes_high_tiers(self, tier, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="X", alpha_tier=tier, alpha_score=82.0)
        from alpha_shadow import AlphaShadowManager
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))
        rows = AlphaShadowManager().get_rare_setups()
        assert any(r["ticker"] == "X" for r in rows)

    def test_excludes_lower_tiers(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="Y", alpha_tier="STRONG_WATCH", alpha_score=58.0)
        from alpha_shadow import AlphaShadowManager
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))
        rows = AlphaShadowManager().get_rare_setups()
        assert rows == []


class TestGetRejectedAlerts:
    def test_returns_watch_predator_with_high_alpha(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="PLTR", predator_tier="WATCH",
                  alpha_tier="HIGH_CONVICTION", alpha_score=71.0, tier_match=0)
        from alpha_shadow import AlphaShadowManager
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))
        rows = AlphaShadowManager().get_rejected_alerts()
        assert any(r["ticker"] == "PLTR" for r in rows)

    def test_excludes_alert_predator_rows(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="MSFT", predator_tier="ALERT",
                  alpha_tier="HIGH_CONVICTION", alpha_score=71.0, tier_match=0)
        from alpha_shadow import AlphaShadowManager
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))
        rows = AlphaShadowManager().get_rejected_alerts()
        assert rows == []


# ── run_shadow_score ──────────────────────────────────────────────────────────

class TestRunShadowScore:
    def _predator_result(self, tier="ALERT", score=7.5):
        return {"ticker": "NVDA", "tier": tier, "score": score, "price": 900.0}

    def test_returns_none_when_fetch_fails(self, monkeypatch):
        from alpha_shadow import AlphaShadowManager

        def _bad_fetch(ticker, **kw):
            raise RuntimeError("network error")

        monkeypatch.setattr(
            __import__("alpha_engine"), "fetch_alpha_input", _bad_fetch
        )
        result = AlphaShadowManager().run_shadow_score("NVDA", self._predator_result())
        assert result is None

    def test_returns_none_when_fetch_returns_none(self, monkeypatch):
        from alpha_shadow import AlphaShadowManager
        monkeypatch.setattr(__import__("alpha_engine"), "fetch_alpha_input", lambda *a, **kw: None)
        result = AlphaShadowManager().run_shadow_score("NVDA", self._predator_result())
        assert result is None

    def test_returns_alpha_result_on_success(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))

        from alpha_engine import AlphaInput, AlphaEngine
        inp = AlphaInput(ticker="NVDA", price=900.0)

        monkeypatch.setattr(__import__("alpha_engine"), "fetch_alpha_input", lambda *a, **kw: inp)

        from alpha_shadow import AlphaShadowManager
        result = AlphaShadowManager().run_shadow_score("NVDA", self._predator_result())
        assert result is not None
        assert result.ticker == "NVDA"

    def test_persists_row_to_db(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))

        from alpha_engine import AlphaInput
        inp = AlphaInput(ticker="NVDA", price=900.0)
        monkeypatch.setattr(__import__("alpha_engine"), "fetch_alpha_input", lambda *a, **kw: inp)

        from alpha_shadow import AlphaShadowManager
        AlphaShadowManager().run_shadow_score("NVDA", self._predator_result())

        conn = sq.connect(path)
        count = conn.execute("SELECT COUNT(*) FROM alpha_shadow_log WHERE ticker='NVDA'").fetchone()[0]
        conn.close()
        assert count == 1

    def test_does_not_raise_when_engine_fails(self, monkeypatch):
        from alpha_engine import AlphaInput

        def _bad_score(self, inp):
            raise ValueError("scoring blew up")

        monkeypatch.setattr(__import__("alpha_engine"), "fetch_alpha_input",
                            lambda *a, **kw: AlphaInput(ticker="X", price=1.0))
        monkeypatch.setattr(__import__("alpha_engine").AlphaEngine, "score", _bad_score)

        from alpha_shadow import AlphaShadowManager
        result = AlphaShadowManager().run_shadow_score("X", {"tier": "WATCH", "score": 5.0})
        assert result is None

    def test_records_tier_match_correctly(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        import database as _db
        monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))

        from alpha_engine import AlphaInput, AlphaResult, ComponentScore

        _cs = [
            ComponentScore("relative_strength", 5.0, 0.15, [], "MEDIUM"),
            ComponentScore("acceleration",      5.0, 0.15, [], "MEDIUM"),
            ComponentScore("squeeze",           5.0, 0.12, [], "MEDIUM"),
            ComponentScore("catalyst",          5.0, 0.15, [], "MEDIUM"),
            ComponentScore("options",           5.0, 0.13, [], "MEDIUM"),
            ComponentScore("breakout",          5.0, 0.15, [], "MEDIUM"),
            ComponentScore("risk_reward",       5.0, 0.10, [], "MEDIUM"),
            ComponentScore("novelty",           5.0, 0.05, [], "MEDIUM"),
        ]
        fake_result = AlphaResult(
            ticker="NVDA", alpha_score=50.0, tier="STRONG_WATCH",
            setup_type="BREAKOUT_EXPANSION", components=_cs,
            relative_strength_score=5.0, acceleration_score=5.0,
            squeeze_score=5.0, catalyst_score=5.0, options_score=5.0,
            breakout_score=5.0, risk_reward_score=5.0, novelty_score=5.0,
            why_scored_high=["momentum"], what_could_invalidate=[],
            what_must_happen_next=[], risk_factors=[],
            expected_holding_window="1–2 weeks",
            filtered=False, filter_reasons=[],
        )
        monkeypatch.setattr(
            __import__("alpha_engine"), "fetch_alpha_input",
            lambda *a, **kw: AlphaInput(ticker="NVDA", price=900.0),
        )
        monkeypatch.setattr(
            __import__("alpha_engine").AlphaEngine, "score",
            lambda self, inp: fake_result,
        )

        from alpha_shadow import AlphaShadowManager
        # predator tier = "STRONG_WATCH" → matches alpha tier → tier_match should be 1
        AlphaShadowManager().run_shadow_score(
            "NVDA", {"tier": "STRONG_WATCH", "score": 6.0}
        )

        conn  = sq.connect(path)
        conn.row_factory = sq.Row
        row   = conn.execute("SELECT tier_match FROM alpha_shadow_log WHERE ticker='NVDA'").fetchone()
        conn.close()
        assert row["tier_match"] == 1


# ── Feature flag gating ───────────────────────────────────────────────────────

class TestAlphaShadowFeatureFlag:
    def test_flag_off_by_default(self, monkeypatch):
        monkeypatch.delenv("ALPHA_SHADOW_ENABLED", raising=False)
        from feature_flags import alpha_shadow_enabled
        assert alpha_shadow_enabled() is False

    def test_flag_enabled_via_env(self, monkeypatch):
        monkeypatch.setenv("ALPHA_SHADOW_ENABLED", "true")
        from feature_flags import alpha_shadow_enabled
        assert alpha_shadow_enabled() is True

    def test_alpha_alerts_flag_off_by_default(self, monkeypatch):
        monkeypatch.delenv("ALPHA_ALERTS_ENABLED", raising=False)
        from feature_flags import alpha_alerts_enabled
        assert alpha_alerts_enabled() is False

    def test_alpha_alerts_flag_enabled_via_env(self, monkeypatch):
        monkeypatch.setenv("ALPHA_ALERTS_ENABLED", "true")
        from feature_flags import alpha_alerts_enabled
        assert alpha_alerts_enabled() is True


# ── API endpoints ─────────────────────────────────────────────────────────────

def _make_flask_app(tmp_path, monkeypatch):
    import sqlite3 as sq

    path = _make_db(str(tmp_path / "t.db"))

    import database as _db
    monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sq.connect(path)))

    from flask import Flask
    from api import api_bp, cache_clear
    app = Flask(__name__)
    app.register_blueprint(api_bp)
    cache_clear()
    return app, path


class TestAlphaLatestEndpoint:
    def test_returns_ok_when_empty(self, tmp_path, monkeypatch):
        app, _ = _make_flask_app(tmp_path, monkeypatch)
        with app.test_client() as c:
            resp = c.get("/api/v1/alpha/latest")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["results"] == []
        assert body["data"]["total"] == 0

    def test_returns_seeded_rows(self, tmp_path, monkeypatch):
        app, path = _make_flask_app(tmp_path, monkeypatch)
        _seed_row(path, ticker="NVDA", alpha_score=70.0, alpha_tier="HIGH_CONVICTION")
        with app.test_client() as c:
            resp = c.get("/api/v1/alpha/latest")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["total"] == 1
        row = body["data"]["results"][0]
        assert row["ticker"] == "NVDA"
        assert row["alpha_tier"] == "HIGH_CONVICTION"

    def test_response_envelope_shape(self, tmp_path, monkeypatch):
        app, _ = _make_flask_app(tmp_path, monkeypatch)
        with app.test_client() as c:
            resp = c.get("/api/v1/alpha/latest")
        body = resp.get_json()
        assert "ok" in body
        assert "data" in body
        assert "meta" in body
        assert "ts" in body["meta"]

    def test_fmt_alpha_row_components_parsed(self, tmp_path, monkeypatch):
        app, path = _make_flask_app(tmp_path, monkeypatch)
        _seed_row(path, component_scores_json=json.dumps({"breakout": {"score": 9.0}}))
        with app.test_client() as c:
            resp = c.get("/api/v1/alpha/latest")
        body = resp.get_json()
        row  = body["data"]["results"][0]
        assert "breakout" in row["components"]
        assert row["components"]["breakout"]["score"] == 9.0


class TestAlphaTopEndpoint:
    def test_excludes_filtered_rows(self, tmp_path, monkeypatch):
        app, path = _make_flask_app(tmp_path, monkeypatch)
        _seed_row(path, ticker="AAPL", alpha_score=90.0, filter_reason="low volume")
        _seed_row(path, ticker="NVDA", alpha_score=75.0, filter_reason=None)
        with app.test_client() as c:
            resp = c.get("/api/v1/alpha/top")
        body    = resp.get_json()
        tickers = [r["ticker"] for r in body["data"]["results"]]
        assert "NVDA" in tickers
        assert "AAPL" not in tickers

    def test_returns_ok_when_empty(self, tmp_path, monkeypatch):
        app, _ = _make_flask_app(tmp_path, monkeypatch)
        with app.test_client() as c:
            resp = c.get("/api/v1/alpha/top")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True


# ── /alpha/debug endpoint ─────────────────────────────────────────────────────

class TestAlphaDebugEndpoint:
    def test_flag_off_shows_false(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ALPHA_SHADOW_ENABLED", raising=False)
        app, _ = _make_flask_app(tmp_path, monkeypatch)
        with app.test_client() as c:
            resp = c.get("/api/v1/alpha/debug")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["alpha_shadow_enabled"] is False
        assert body["data"]["env_var_raw"] == "(not set)"

    def test_flag_on_shows_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ALPHA_SHADOW_ENABLED", "true")
        app, _ = _make_flask_app(tmp_path, monkeypatch)
        with app.test_client() as c:
            resp = c.get("/api/v1/alpha/debug")
        body = resp.get_json()
        assert body["data"]["alpha_shadow_enabled"] is True
        assert body["data"]["env_var_raw"] == "true"

    def test_table_exists_true_when_seeded(self, tmp_path, monkeypatch):
        app, path = _make_flask_app(tmp_path, monkeypatch)
        _seed_row(path)
        with app.test_client() as c:
            resp = c.get("/api/v1/alpha/debug")
        body = resp.get_json()
        assert body["data"]["table_exists"] is True
        assert body["data"]["row_count"] == 1
        assert body["data"]["latest_scan_time"] is not None

    def test_table_empty_returns_zero(self, tmp_path, monkeypatch):
        app, _ = _make_flask_app(tmp_path, monkeypatch)
        with app.test_client() as c:
            resp = c.get("/api/v1/alpha/debug")
        body = resp.get_json()
        assert body["data"]["table_exists"] is True
        assert body["data"]["row_count"] == 0
        assert body["data"]["latest_scan_time"] is None

    def test_response_not_cached(self, tmp_path, monkeypatch):
        """Debug endpoint must not return stale cached data."""
        app, path = _make_flask_app(tmp_path, monkeypatch)
        with app.test_client() as c:
            r1 = c.get("/api/v1/alpha/debug")
            _seed_row(path, ticker="NVDA")
            r2 = c.get("/api/v1/alpha/debug")
        d1 = r1.get_json()["data"]
        d2 = r2.get_json()["data"]
        assert d1["row_count"] == 0
        assert d2["row_count"] == 1

    def test_envelope_shape(self, tmp_path, monkeypatch):
        app, _ = _make_flask_app(tmp_path, monkeypatch)
        with app.test_client() as c:
            resp = c.get("/api/v1/alpha/debug")
        body = resp.get_json()
        assert {"ok", "data", "meta"} <= set(body)
        assert {"alpha_shadow_enabled", "env_var_raw", "table_exists",
                "row_count", "latest_scan_time"} <= set(body["data"])

    @pytest.mark.parametrize("val,expected", [
        ("true",  True),
        ("1",     True),
        ("yes",   True),
        ("false", False),
        ("0",     False),
        ("no",    False),
        ("",      False),
    ])
    def test_flag_parsing_all_values(self, val, expected, tmp_path, monkeypatch):
        if val:
            monkeypatch.setenv("ALPHA_SHADOW_ENABLED", val)
        else:
            monkeypatch.delenv("ALPHA_SHADOW_ENABLED", raising=False)
        app, _ = _make_flask_app(tmp_path, monkeypatch)
        with app.test_client() as c:
            resp = c.get("/api/v1/alpha/debug")
        assert resp.get_json()["data"]["alpha_shadow_enabled"] is expected


# ── Helpers ────────────────────────────────────────────────────────────────────

def _row_conn(conn):
    conn.row_factory = sqlite3.Row
    return conn
