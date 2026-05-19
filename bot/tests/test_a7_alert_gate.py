"""
Phase A7 — Alpha alert candidate gate tests.

Covers:
  - WATCH alone is not ready (max MONITOR)
  - STRONG_WATCH capped at PRE_ALERT
  - HIGH_CONVICTION can reach ALERT_READY
  - RARE_SETUP can reach RARE_ALERT
  - Missing component data blocks readiness
  - High trap-rate setup blocks readiness
  - Validation success improves readiness score
  - Validation failure (trap/spike) reduces readiness
  - Duplicate/recent alert reduces readiness
  - score_readiness() never raises on bad input
  - Deterministic: same input → same output
  - get_alert_candidates() and get_alert_gate_summary() safe on empty DB
  - API endpoints: GET /alpha/alert-candidates, GET /alpha/alert-gate/summary
  - No Alpha alerts are sent
"""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_get_conn(path: str):
    def _get():
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    return _get


def _init_tables(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
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
    c.execute("""
        CREATE TABLE IF NOT EXISTS alert_log (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker  TEXT,
            urgency TEXT,
            sent_at TEXT NOT NULL,
            message TEXT
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


def _insert_shadow(db_path: str, ticker: str, alpha_score: float, alpha_tier: str,
                   setup_type: str = "BREAKOUT_EXPANSION",
                   component_scores: dict = None,
                   scan_time: str = "2026-01-01T12:00:00") -> None:
    cs_json = json.dumps(component_scores) if component_scores else None
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO alpha_shadow_log (ticker, scan_time, alpha_score, alpha_tier, setup_type, component_scores_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ticker, scan_time, alpha_score, alpha_tier, setup_type, cs_json),
    )
    conn.commit()
    conn.close()


def _insert_validation(db_path: str, ticker: str, behavior_class: str,
                       setup_type: str = "BREAKOUT_EXPANSION") -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO alpha_validation
            (outcome_id, ticker, scan_time, setup_type, alpha_tier, behavior_class,
             validation_score, confidence, n_windows, computed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ticker.__hash__() % 100000, ticker, "2026-01-01T00:00:00", setup_type,
         "STRONG_WATCH", behavior_class, 60.0, "MEDIUM", 3, "2026-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()


def _insert_alert_log(db_path: str, ticker: str, sent_at: str = "2026-01-01T12:00:00") -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO alert_log (ticker, urgency, sent_at) VALUES (?, ?, ?)",
                 (ticker, "FYI", sent_at))
    conn.commit()
    conn.close()


# Good component data
_GOOD_COMPONENTS = {
    "relative_strength": {"score": 7.5, "data_quality": "FRESH"},
    "acceleration":      {"score": 7.0, "data_quality": "FRESH"},
    "squeeze":           {"score": 6.5, "data_quality": "FRESH"},
    "catalyst":          {"score": 7.0, "data_quality": "FRESH"},
    "options":           {"score": 6.0, "data_quality": "FRESH"},
    "breakout":          {"score": 7.5, "data_quality": "FRESH"},
    "risk_reward":       {"score": 6.0, "data_quality": "FRESH"},
    "novelty":           {"score": 5.0, "data_quality": "FRESH"},
}

_MISSING_COMPONENTS = {
    "relative_strength": {"score": None, "data_quality": "MISSING"},
    "acceleration":      {"score": None, "data_quality": "MISSING"},
    "squeeze":           {"score": None, "data_quality": "MISSING"},
    "catalyst":          {"score": 5.0,  "data_quality": "FRESH"},
    "options":           {"score": 5.0,  "data_quality": "FRESH"},
    "breakout":          {"score": 5.0,  "data_quality": "FRESH"},
    "risk_reward":       {"score": 4.0,  "data_quality": "FRESH"},
    "novelty":           {"score": 4.0,  "data_quality": "FRESH"},
}


# ─────────────────────────────────────────────────────────────────────────────
# 1. score_readiness() — pure function
# ─────────────────────────────────────────────────────────────────────────────

class TestScoreReadinessPure:

    def _candidate(self, alpha_score: float, alpha_tier: str,
                   setup_type: str = "BREAKOUT_EXPANSION",
                   components: dict = None) -> dict:
        return {
            "ticker": "TEST",
            "scan_time": "2026-01-01T12:00:00",
            "alpha_score": alpha_score,
            "alpha_tier": alpha_tier,
            "setup_type": setup_type,
            "component_scores_json": json.dumps(components or _GOOD_COMPONENTS),
        }

    def test_returns_expected_keys(self):
        from alpha_alert_gate import score_readiness
        result = score_readiness(self._candidate(72.0, "HIGH_CONVICTION"))
        for key in ("ticker", "alpha_score", "alpha_tier", "readiness_score",
                    "readiness_tier", "alert_ready", "reason", "blocking_factors",
                    "confirmation_needed", "suggested_wait_window"):
            assert key in result

    # ── Tier caps ──────────────────────────────────────────────────────────────

    def test_watch_alone_is_at_most_monitor(self):
        from alpha_alert_gate import score_readiness
        c = self._candidate(48.0, "WATCH")
        result = score_readiness(c)
        assert result["readiness_tier"] in ("NOT_READY", "MONITOR")
        assert result["alert_ready"] is False

    def test_strong_watch_at_most_pre_alert(self):
        from alpha_alert_gate import score_readiness
        c = self._candidate(64.0, "STRONG_WATCH")
        result = score_readiness(c)
        assert result["readiness_tier"] in ("NOT_READY", "MONITOR", "PRE_ALERT")
        assert result["alert_ready"] is False

    def test_high_conviction_can_be_alert_ready(self):
        from alpha_alert_gate import score_readiness
        c = self._candidate(78.0, "HIGH_CONVICTION")
        result = score_readiness(c, {"trap_rates": {}, "validation_summary": {},
                                     "recent_alert_tickers": set()})
        assert result["readiness_tier"] in ("PRE_ALERT", "ALERT_READY")

    def test_high_conviction_alert_ready_with_good_data(self):
        from alpha_alert_gate import score_readiness
        c = self._candidate(78.0, "HIGH_CONVICTION")
        result = score_readiness(c, {"trap_rates": {}, "validation_summary": {"TEST": "SUSTAINED_TREND"},
                                     "recent_alert_tickers": set()})
        assert result["readiness_tier"] == "ALERT_READY"
        assert result["alert_ready"] is True

    def test_rare_setup_can_be_rare_alert(self):
        from alpha_alert_gate import score_readiness
        c = self._candidate(90.0, "RARE_SETUP")
        result = score_readiness(c, {"trap_rates": {}, "validation_summary": {"TEST": "VALID_BREAKOUT"},
                                     "recent_alert_tickers": set()})
        assert result["readiness_tier"] == "RARE_ALERT"
        assert result["alert_ready"] is True

    def test_ignore_tier_is_not_ready(self):
        from alpha_alert_gate import score_readiness
        c = self._candidate(20.0, "IGNORE")
        result = score_readiness(c)
        assert result["readiness_tier"] == "NOT_READY"
        assert result["alert_ready"] is False

    # ── Missing data ───────────────────────────────────────────────────────────

    def test_missing_components_reduce_readiness(self):
        from alpha_alert_gate import score_readiness
        good = score_readiness(self._candidate(75.0, "HIGH_CONVICTION", components=_GOOD_COMPONENTS))
        bad  = score_readiness(self._candidate(75.0, "HIGH_CONVICTION", components=_MISSING_COMPONENTS))
        assert bad["readiness_score"] < good["readiness_score"]

    def test_missing_components_add_blocking_factor(self):
        from alpha_alert_gate import score_readiness
        result = score_readiness(self._candidate(75.0, "HIGH_CONVICTION",
                                                 components=_MISSING_COMPONENTS))
        assert any("missing" in b.lower() for b in result["blocking_factors"])

    def test_no_component_data_adds_blocking_factor(self):
        from alpha_alert_gate import score_readiness
        c = {"ticker": "X", "alpha_score": 75.0, "alpha_tier": "HIGH_CONVICTION",
             "setup_type": "BREAKOUT_EXPANSION", "component_scores_json": None}
        result = score_readiness(c)
        assert any("component" in b.lower() for b in result["blocking_factors"])

    # ── Trap rate ──────────────────────────────────────────────────────────────

    def test_high_trap_rate_reduces_readiness(self):
        from alpha_alert_gate import score_readiness
        ctx_clean = {"trap_rates": {"BREAKOUT_EXPANSION": 0.10}, "validation_summary": {},
                     "recent_alert_tickers": set()}
        ctx_trap  = {"trap_rates": {"BREAKOUT_EXPANSION": 0.75}, "validation_summary": {},
                     "recent_alert_tickers": set()}
        c = self._candidate(75.0, "HIGH_CONVICTION")
        clean_score = score_readiness(c, ctx_clean)["readiness_score"]
        trap_score  = score_readiness(c, ctx_trap)["readiness_score"]
        assert trap_score < clean_score

    def test_high_trap_rate_adds_blocking_factor(self):
        from alpha_alert_gate import score_readiness
        ctx = {"trap_rates": {"BREAKOUT_EXPANSION": 0.70}, "validation_summary": {},
               "recent_alert_tickers": set()}
        result = score_readiness(self._candidate(75.0, "HIGH_CONVICTION"), ctx)
        assert any("trap rate" in b.lower() for b in result["blocking_factors"])

    def test_low_trap_rate_no_blocking(self):
        from alpha_alert_gate import score_readiness
        ctx = {"trap_rates": {"BREAKOUT_EXPANSION": 0.15}, "validation_summary": {},
               "recent_alert_tickers": set()}
        result = score_readiness(self._candidate(75.0, "HIGH_CONVICTION"), ctx)
        assert not any("trap rate" in b.lower() for b in result["blocking_factors"])

    # ── Validation effects ─────────────────────────────────────────────────────

    def test_sustained_trend_validation_boosts_readiness(self):
        from alpha_alert_gate import score_readiness
        ctx_none = {"trap_rates": {}, "validation_summary": {},
                    "recent_alert_tickers": set()}
        ctx_good = {"trap_rates": {}, "validation_summary": {"TEST": "SUSTAINED_TREND"},
                    "recent_alert_tickers": set()}
        c = self._candidate(72.0, "HIGH_CONVICTION")
        score_none = score_readiness(c, ctx_none)["readiness_score"]
        score_good = score_readiness(c, ctx_good)["readiness_score"]
        assert score_good > score_none

    def test_volatility_trap_validation_blocks(self):
        from alpha_alert_gate import score_readiness
        ctx = {"trap_rates": {}, "validation_summary": {"TEST": "VOLATILITY_TRAP"},
               "recent_alert_tickers": set()}
        c = self._candidate(72.0, "HIGH_CONVICTION")
        result = score_readiness(c, ctx)
        assert any("volatility_trap" in b.lower() or "validation" in b.lower()
                   for b in result["blocking_factors"])

    def test_short_lived_spike_reduces_readiness(self):
        from alpha_alert_gate import score_readiness
        ctx_none = {"trap_rates": {}, "validation_summary": {},
                    "recent_alert_tickers": set()}
        ctx_spike = {"trap_rates": {}, "validation_summary": {"TEST": "SHORT_LIVED_SPIKE"},
                     "recent_alert_tickers": set()}
        c = self._candidate(72.0, "HIGH_CONVICTION")
        score_none  = score_readiness(c, ctx_none)["readiness_score"]
        score_spike = score_readiness(c, ctx_spike)["readiness_score"]
        assert score_spike < score_none

    def test_valid_breakout_validation_boosts(self):
        from alpha_alert_gate import score_readiness
        ctx = {"trap_rates": {}, "validation_summary": {"TEST": "VALID_BREAKOUT"},
               "recent_alert_tickers": set()}
        ctx_none = {"trap_rates": {}, "validation_summary": {},
                    "recent_alert_tickers": set()}
        c = self._candidate(72.0, "HIGH_CONVICTION")
        assert (score_readiness(c, ctx)["readiness_score"] >
                score_readiness(c, ctx_none)["readiness_score"])

    # ── Duplicate alert ────────────────────────────────────────────────────────

    def test_recent_duplicate_reduces_readiness(self):
        from alpha_alert_gate import score_readiness
        ctx_dup   = {"trap_rates": {}, "validation_summary": {},
                     "recent_alert_tickers": {"TEST"}}
        ctx_clean = {"trap_rates": {}, "validation_summary": {},
                     "recent_alert_tickers": set()}
        c = self._candidate(75.0, "HIGH_CONVICTION")
        dup_score   = score_readiness(c, ctx_dup)["readiness_score"]
        clean_score = score_readiness(c, ctx_clean)["readiness_score"]
        assert dup_score < clean_score

    def test_recent_duplicate_adds_blocking_factor(self):
        from alpha_alert_gate import score_readiness
        ctx = {"trap_rates": {}, "validation_summary": {},
               "recent_alert_tickers": {"TEST"}}
        result = score_readiness(self._candidate(75.0, "HIGH_CONVICTION"), ctx)
        assert any("duplicate" in b.lower() for b in result["blocking_factors"])

    def test_no_duplicate_no_blocking_factor(self):
        from alpha_alert_gate import score_readiness
        ctx = {"trap_rates": {}, "validation_summary": {},
               "recent_alert_tickers": set()}
        result = score_readiness(self._candidate(75.0, "HIGH_CONVICTION"), ctx)
        assert not any("duplicate" in b.lower() for b in result["blocking_factors"])

    # ── Confirmation framework ─────────────────────────────────────────────────

    def test_breakout_setup_needs_price_confirmation(self):
        from alpha_alert_gate import score_readiness
        ctx = {"trap_rates": {"BREAKOUT_EXPANSION": 0.60}, "validation_summary": {},
               "recent_alert_tickers": set()}
        c = self._candidate(75.0, "HIGH_CONVICTION", setup_type="BREAKOUT_EXPANSION")
        result = score_readiness(c, ctx)
        assert "price_holds_breakout_level" in result["confirmation_needed"]

    def test_options_setup_needs_options_confirmation(self):
        from alpha_alert_gate import score_readiness
        c = self._candidate(75.0, "HIGH_CONVICTION", setup_type="OPTIONS_PRESSURE")
        result = score_readiness(c)
        assert "options_activity_persists" in result["confirmation_needed"]

    def test_squeeze_setup_needs_volatility_confirmation(self):
        from alpha_alert_gate import score_readiness
        c = self._candidate(58.0, "STRONG_WATCH", setup_type="SQUEEZE_CANDIDATE")
        result = score_readiness(c)
        assert "volatility_cools_down" in result["confirmation_needed"]

    # ── Safety / never-raises ──────────────────────────────────────────────────

    def test_never_raises_on_empty_candidate(self):
        from alpha_alert_gate import score_readiness
        try:
            result = score_readiness({})
            assert result["alert_ready"] is False
        except Exception as exc:
            pytest.fail(f"score_readiness raised: {exc}")

    def test_never_raises_on_none_tier(self):
        from alpha_alert_gate import score_readiness
        try:
            result = score_readiness({"ticker": "X", "alpha_score": None, "alpha_tier": None})
            assert result["readiness_tier"] == "NOT_READY"
        except Exception as exc:
            pytest.fail(f"score_readiness raised: {exc}")

    def test_never_raises_on_bad_json(self):
        from alpha_alert_gate import score_readiness
        c = {"ticker": "X", "alpha_score": 70.0, "alpha_tier": "HIGH_CONVICTION",
             "component_scores_json": "NOT_VALID_JSON"}
        try:
            score_readiness(c)
        except Exception as exc:
            pytest.fail(f"score_readiness raised: {exc}")

    # ── Determinism ────────────────────────────────────────────────────────────

    def test_deterministic_same_input_same_output(self):
        from alpha_alert_gate import score_readiness
        c   = self._candidate(72.0, "HIGH_CONVICTION")
        ctx = {"trap_rates": {"BREAKOUT_EXPANSION": 0.3},
               "validation_summary": {"TEST": "SUSTAINED_TREND"},
               "recent_alert_tickers": set()}
        r1 = score_readiness(c, ctx)
        r2 = score_readiness(c, ctx)
        assert r1["readiness_score"] == r2["readiness_score"]
        assert r1["readiness_tier"]  == r2["readiness_tier"]
        assert r1["alert_ready"]     == r2["alert_ready"]

    # ── Readiness score range ──────────────────────────────────────────────────

    def test_readiness_score_in_0_100(self):
        from alpha_alert_gate import score_readiness
        for tier, score in [("IGNORE", 10.0), ("WATCH", 42.0), ("STRONG_WATCH", 60.0),
                             ("HIGH_CONVICTION", 75.0), ("RARE_SETUP", 90.0)]:
            result = score_readiness(self._candidate(score, tier))
            assert 0.0 <= result["readiness_score"] <= 100.0, \
                f"{tier}: readiness_score={result['readiness_score']}"

    # ── alert_ready flag ───────────────────────────────────────────────────────

    def test_alert_ready_only_for_top_tiers(self):
        from alpha_alert_gate import score_readiness, READINESS_TIERS
        # Only ALERT_READY and RARE_ALERT should set alert_ready=True
        for tier, score in [("WATCH", 42.0), ("STRONG_WATCH", 60.0)]:
            result = score_readiness(self._candidate(score, tier))
            assert result["alert_ready"] is False, f"Expected False for {tier}"

    def test_alert_ready_true_for_rare_alert(self):
        from alpha_alert_gate import score_readiness
        c = self._candidate(92.0, "RARE_SETUP")
        ctx = {"trap_rates": {}, "validation_summary": {"TEST": "SUSTAINED_TREND"},
               "recent_alert_tickers": set()}
        result = score_readiness(c, ctx)
        if result["readiness_tier"] == "RARE_ALERT":
            assert result["alert_ready"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. _score_to_readiness_tier helper
# ─────────────────────────────────────────────────────────────────────────────

class TestScoreToReadinessTier:
    def test_below_35_is_not_ready(self):
        from alpha_alert_gate import _score_to_readiness_tier
        assert _score_to_readiness_tier(0.0)  == "NOT_READY"
        assert _score_to_readiness_tier(34.9) == "NOT_READY"

    def test_35_to_55_is_monitor(self):
        from alpha_alert_gate import _score_to_readiness_tier
        assert _score_to_readiness_tier(35.0) == "MONITOR"
        assert _score_to_readiness_tier(54.9) == "MONITOR"

    def test_55_to_70_is_pre_alert(self):
        from alpha_alert_gate import _score_to_readiness_tier
        assert _score_to_readiness_tier(55.0) == "PRE_ALERT"
        assert _score_to_readiness_tier(69.9) == "PRE_ALERT"

    def test_70_to_85_is_alert_ready(self):
        from alpha_alert_gate import _score_to_readiness_tier
        assert _score_to_readiness_tier(70.0) == "ALERT_READY"
        assert _score_to_readiness_tier(84.9) == "ALERT_READY"

    def test_85_plus_is_rare_alert(self):
        from alpha_alert_gate import _score_to_readiness_tier
        assert _score_to_readiness_tier(85.0) == "RARE_ALERT"
        assert _score_to_readiness_tier(100.0) == "RARE_ALERT"


# ─────────────────────────────────────────────────────────────────────────────
# 3. _cap_readiness_tier helper
# ─────────────────────────────────────────────────────────────────────────────

class TestCapReadinessTier:
    def test_within_cap_unchanged(self):
        from alpha_alert_gate import _cap_readiness_tier
        assert _cap_readiness_tier("PRE_ALERT", "PRE_ALERT")   == "PRE_ALERT"
        assert _cap_readiness_tier("MONITOR",   "PRE_ALERT")   == "MONITOR"
        assert _cap_readiness_tier("NOT_READY", "ALERT_READY") == "NOT_READY"

    def test_above_cap_is_capped(self):
        from alpha_alert_gate import _cap_readiness_tier
        assert _cap_readiness_tier("ALERT_READY", "PRE_ALERT") == "PRE_ALERT"
        assert _cap_readiness_tier("RARE_ALERT",  "ALERT_READY") == "ALERT_READY"
        assert _cap_readiness_tier("RARE_ALERT",  "PRE_ALERT")   == "PRE_ALERT"


# ─────────────────────────────────────────────────────────────────────────────
# 4. get_alert_candidates() — DB-integrated
# ─────────────────────────────────────────────────────────────────────────────

class TestGetAlertCandidates:
    def test_empty_db_returns_empty_list(self, db_path):
        from alpha_alert_gate import get_alert_candidates
        assert get_alert_candidates() == []

    def test_returns_scored_results(self, db_path):
        _insert_shadow(db_path, "AAPL", 75.0, "HIGH_CONVICTION",
                       component_scores=_GOOD_COMPONENTS)
        from alpha_alert_gate import get_alert_candidates
        results = get_alert_candidates(limit=10)
        assert len(results) == 1
        r = results[0]
        assert r["ticker"] == "AAPL"
        assert "readiness_tier" in r
        assert "alert_ready"    in r

    def test_sorted_by_readiness_score_desc(self, db_path):
        for ticker, score, tier in [("A", 90.0, "RARE_SETUP"),
                                    ("B", 75.0, "HIGH_CONVICTION"),
                                    ("C", 55.0, "STRONG_WATCH")]:
            _insert_shadow(db_path, ticker, score, tier, component_scores=_GOOD_COMPONENTS)
        from alpha_alert_gate import get_alert_candidates
        results = get_alert_candidates(limit=10)
        scores = [r["readiness_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_one_per_ticker_latest_scan(self, db_path):
        _insert_shadow(db_path, "DUP", 60.0, "STRONG_WATCH",
                       scan_time="2026-01-01T10:00:00", component_scores=_GOOD_COMPONENTS)
        _insert_shadow(db_path, "DUP", 70.0, "HIGH_CONVICTION",
                       scan_time="2026-01-01T12:00:00", component_scores=_GOOD_COMPONENTS)
        from alpha_alert_gate import get_alert_candidates
        results = get_alert_candidates(limit=10)
        dup_results = [r for r in results if r["ticker"] == "DUP"]
        assert len(dup_results) == 1
        assert dup_results[0]["alpha_score"] == 70.0

    def test_filters_null_alpha_score(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO alpha_shadow_log (ticker, scan_time, alpha_score, alpha_tier) "
            "VALUES ('NULL_SCORE', '2026-01-01T12:00:00', NULL, 'STRONG_WATCH')"
        )
        conn.commit()
        conn.close()
        from alpha_alert_gate import get_alert_candidates
        results = get_alert_candidates(limit=10)
        assert all(r["ticker"] != "NULL_SCORE" for r in results)

    def test_validation_context_applied(self, db_path):
        _insert_shadow(db_path, "V1", 72.0, "HIGH_CONVICTION",
                       component_scores=_GOOD_COMPONENTS)
        _insert_validation(db_path, "V1", "SUSTAINED_TREND")
        _insert_shadow(db_path, "V2", 72.0, "HIGH_CONVICTION",
                       component_scores=_GOOD_COMPONENTS,
                       scan_time="2026-01-01T13:00:00")
        _insert_validation(db_path, "V2", "VOLATILITY_TRAP")

        from alpha_alert_gate import get_alert_candidates
        results = get_alert_candidates(limit=10)
        v1 = next(r for r in results if r["ticker"] == "V1")
        v2 = next(r for r in results if r["ticker"] == "V2")
        assert v1["readiness_score"] > v2["readiness_score"]

    def test_recent_alert_context_applied(self, db_path):
        from datetime import datetime
        recent_ts = datetime.now().isoformat()
        _insert_shadow(db_path, "RECENT", 75.0, "HIGH_CONVICTION",
                       component_scores=_GOOD_COMPONENTS)
        _insert_shadow(db_path, "FRESH",  75.0, "HIGH_CONVICTION",
                       component_scores=_GOOD_COMPONENTS,
                       scan_time="2026-01-01T13:00:00")
        _insert_alert_log(db_path, "RECENT", sent_at=recent_ts)

        from alpha_alert_gate import get_alert_candidates
        results = get_alert_candidates(limit=10)
        recent_r = next((r for r in results if r["ticker"] == "RECENT"), None)
        fresh_r  = next((r for r in results if r["ticker"] == "FRESH"),  None)
        if recent_r and fresh_r:
            assert recent_r["readiness_score"] < fresh_r["readiness_score"]

    def test_never_raises(self, db_path):
        from alpha_alert_gate import get_alert_candidates
        try:
            get_alert_candidates()
        except Exception as exc:
            pytest.fail(f"raised: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# 5. get_alert_gate_summary()
# ─────────────────────────────────────────────────────────────────────────────

class TestGetAlertGateSummary:
    def test_empty_db_returns_safe_structure(self, db_path):
        from alpha_alert_gate import get_alert_gate_summary
        s = get_alert_gate_summary()
        assert s["total_evaluated"] == 0
        assert s["alert_ready_count"] == 0

    def test_total_evaluated_matches(self, db_path):
        for i, (score, tier) in enumerate([
            (42.0, "WATCH"), (60.0, "STRONG_WATCH"), (75.0, "HIGH_CONVICTION")
        ]):
            _insert_shadow(db_path, f"T{i}", score, tier,
                           component_scores=_GOOD_COMPONENTS,
                           scan_time=f"2026-01-0{i+1}T12:00:00")
        from alpha_alert_gate import get_alert_gate_summary
        s = get_alert_gate_summary()
        assert s["total_evaluated"] == 3

    def test_readiness_distribution_covers_all_evaluated(self, db_path):
        for i, (score, tier) in enumerate([
            (42.0, "WATCH"), (60.0, "STRONG_WATCH"), (75.0, "HIGH_CONVICTION")
        ]):
            _insert_shadow(db_path, f"D{i}", score, tier,
                           component_scores=_GOOD_COMPONENTS,
                           scan_time=f"2026-01-0{i+1}T12:00:00")
        from alpha_alert_gate import get_alert_gate_summary
        s = get_alert_gate_summary()
        dist_total = sum(s["readiness_distribution"].values())
        assert dist_total == s["total_evaluated"]

    def test_alert_ready_count_matches_list(self, db_path):
        _insert_shadow(db_path, "RA", 90.0, "RARE_SETUP",
                       component_scores=_GOOD_COMPONENTS)
        from alpha_alert_gate import get_alert_gate_summary, get_alert_candidates
        results = get_alert_candidates(limit=100)
        expected = sum(1 for r in results if r["alert_ready"])
        s = get_alert_gate_summary()
        assert s["alert_ready_count"] == expected

    def test_note_confirms_no_alerts(self, db_path):
        from alpha_alert_gate import get_alert_gate_summary
        s = get_alert_gate_summary()
        assert "no" in s.get("note", "").lower() or "simulation" in s.get("note", "").lower()

    def test_never_raises(self, db_path):
        from alpha_alert_gate import get_alert_gate_summary
        try:
            get_alert_gate_summary()
        except Exception as exc:
            pytest.fail(f"raised: {exc}")

    def test_top_blockers_structure(self, db_path):
        _insert_shadow(db_path, "BLK", 60.0, "STRONG_WATCH",
                       component_scores=_MISSING_COMPONENTS)
        from alpha_alert_gate import get_alert_gate_summary
        s = get_alert_gate_summary()
        for item in s["top_blockers"]:
            assert "factor" in item
            assert "count"  in item

    def test_top_confirmations_structure(self, db_path):
        _insert_shadow(db_path, "CONF", 60.0, "STRONG_WATCH",
                       setup_type="BREAKOUT_EXPANSION",
                       component_scores=_GOOD_COMPONENTS)
        from alpha_alert_gate import get_alert_gate_summary
        s = get_alert_gate_summary()
        for item in s["top_confirmations_needed"]:
            assert "confirmation" in item
            assert "count"        in item


# ─────────────────────────────────────────────────────────────────────────────
# 6. API endpoints
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


class TestApiAlertGate:
    def test_get_alert_candidates_empty(self, app_client):
        rv = app_client.get("/api/v1/alpha/alert-candidates")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert data["data"]["total"] == 0
        assert data["data"]["results"] == []

    def test_get_alert_gate_summary_empty(self, app_client):
        rv = app_client.get("/api/v1/alpha/alert-gate/summary")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert data["data"]["total_evaluated"] == 0

    def test_get_alert_candidates_with_data(self, app_client, db_path):
        _insert_shadow(db_path, "API1", 75.0, "HIGH_CONVICTION",
                       component_scores=_GOOD_COMPONENTS)
        from api import cache_clear
        cache_clear()
        rv = app_client.get("/api/v1/alpha/alert-candidates?limit=10")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert data["data"]["total"] == 1
        r = data["data"]["results"][0]
        assert r["ticker"] == "API1"
        assert "readiness_tier" in r
        assert "alert_ready"    in r

    def test_get_alert_gate_summary_with_data(self, app_client, db_path):
        for i, (score, tier) in enumerate([
            (90.0, "RARE_SETUP"), (75.0, "HIGH_CONVICTION"), (55.0, "STRONG_WATCH")
        ]):
            _insert_shadow(db_path, f"GS{i}", score, tier,
                           component_scores=_GOOD_COMPONENTS,
                           scan_time=f"2026-02-0{i+1}T12:00:00")
        from api import cache_clear
        cache_clear()
        rv = app_client.get("/api/v1/alpha/alert-gate/summary")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert data["data"]["total_evaluated"] == 3

    def test_no_alerts_sent_note_in_response(self, app_client, db_path):
        _insert_shadow(db_path, "SAFE", 90.0, "RARE_SETUP",
                       component_scores=_GOOD_COMPONENTS)
        from api import cache_clear
        cache_clear()
        rv = app_client.get("/api/v1/alpha/alert-candidates")
        data = rv.get_json()
        assert "no" in data["data"]["note"].lower() or "simulation" in data["data"]["note"].lower()

    def test_envelope_structure(self, app_client):
        for url in ["/api/v1/alpha/alert-candidates", "/api/v1/alpha/alert-gate/summary"]:
            rv = app_client.get(url)
            data = rv.get_json()
            assert "ok"   in data
            assert "data" in data
            assert "meta" in data

    def test_limit_param(self, app_client, db_path):
        for i in range(5):
            _insert_shadow(db_path, f"LM{i}", 70.0 + i, "HIGH_CONVICTION",
                           component_scores=_GOOD_COMPONENTS,
                           scan_time=f"2026-03-0{i+1}T12:00:00")
        from api import cache_clear
        cache_clear()
        rv = app_client.get("/api/v1/alpha/alert-candidates?limit=3")
        data = rv.get_json()
        assert len(data["data"]["results"]) <= 3

    def test_alert_ready_count_in_response(self, app_client, db_path):
        _insert_shadow(db_path, "AR1", 90.0, "RARE_SETUP",
                       component_scores=_GOOD_COMPONENTS)
        from api import cache_clear
        cache_clear()
        rv = app_client.get("/api/v1/alpha/alert-candidates")
        data = rv.get_json()
        assert "alert_ready" in data["data"]
        assert isinstance(data["data"]["alert_ready"], int)


# ─────────────────────────────────────────────────────────────────────────────
# 7. No alerts sent (safety)
# ─────────────────────────────────────────────────────────────────────────────

class TestNoAlertsSent:
    def test_get_alert_candidates_does_not_write_alert_log(self, db_path):
        _insert_shadow(db_path, "NOSEND", 90.0, "RARE_SETUP",
                       component_scores=_GOOD_COMPONENTS)
        from alpha_alert_gate import get_alert_candidates
        get_alert_candidates()

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM alert_log").fetchone()[0]
        conn.close()
        assert count == 0

    def test_score_readiness_does_not_write_to_db(self, db_path):
        from alpha_alert_gate import score_readiness
        c = {"ticker": "NOSEND", "alpha_score": 90.0, "alpha_tier": "RARE_SETUP",
             "setup_type": "BREAKOUT_EXPANSION",
             "component_scores_json": json.dumps(_GOOD_COMPONENTS)}
        score_readiness(c)

        conn = sqlite3.connect(db_path)
        alert_count = conn.execute("SELECT COUNT(*) FROM alert_log").fetchone()[0]
        shadow_count = conn.execute("SELECT COUNT(*) FROM alpha_shadow_log").fetchone()[0]
        conn.close()
        assert alert_count  == 0
        assert shadow_count == 0  # pure function — no shadow writes
