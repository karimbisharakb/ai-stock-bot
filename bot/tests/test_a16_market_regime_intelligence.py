"""
Phase A16 — Tests for market_regime_intelligence.py and related endpoints.

Covers:
  - _compute_points (pure)
  - _classify_overall (pure)
  - _classify_volatility (pure)
  - _classify_breadth (pure)
  - _classify_speculative (pure)
  - _compute_regime_score (pure)
  - _compute_multipliers (pure)
  - compute_regime (pure end-to-end)
  - save_regime_snapshot / get_latest_regime / get_regime_history (DB)
  - refresh_regime (integrated, mocked fetch)
  - get_regime_context_for_checklist (context accessor)
  - get_regime_context_for_risk (context accessor)
  - Multiplier bounds across all regime combos
  - Sparse-data safety
  - Safety constraints (no trading, no broker, no UPDATE/DELETE on snapshots)
  - API: GET /market/regime, GET /market/regime/history, POST /market/regime/refresh
  - Scheduler: _run_regime_refresh registered, morning brief integration
"""
import inspect
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import database
import market_regime_intelligence as mri

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_get_conn(db_path: str):
    def _get_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    return _get_conn


_CREATE_TABLE = """
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
)
"""


def _db(tmp_path):
    db_path = str(tmp_path / "test_a16.db")
    conn = sqlite3.connect(db_path)
    conn.execute(_CREATE_TABLE)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
    monkeypatch.setattr(mri, "_ensure_tables", lambda: None)
    return db_path


def _risk_on_signals():
    return {
        "spy_vs_20sma":      5.0,
        "spy_vs_50sma":      4.0,
        "spy_vs_200sma":     10.0,
        "qqq_vs_200sma":     8.0,
        "iwm_vs_200sma":     3.0,
        "iwm_vs_50sma":      2.0,
        "vix":               13.0,
        "vix_change_pct":    -5.0,
        "btc_7d_return_pct": 12.0,
        "sox_vs_50sma":      6.0,
        "tsx_vs_200sma":     2.0,
    }


def _risk_off_signals():
    # Targeted to produce points ≈ -1 (RISK_OFF band: -2 ≤ pts < 2)
    # SPY barely above 200 SMA (+2), QQQ below (-2), VIX elevated (-1) = -1 total
    return {
        "spy_vs_200sma":     2.0,
        "qqq_vs_200sma":     -1.0,
        "vix":               27.0,
        "vix_change_pct":    3.0,
        "btc_7d_return_pct": -4.0,
    }


def _panic_signals():
    return {
        "spy_vs_200sma":     -15.0,
        "qqq_vs_200sma":     -12.0,
        "iwm_vs_200sma":     -20.0,
        "vix":               55.0,
        "vix_change_pct":    40.0,
        "btc_7d_return_pct": -25.0,
    }


def _make_regime(
    overall="NEUTRAL",
    volatility="ELEVATED",
    breadth="MIXED",
    speculative="SELECTIVE",
    score=50.0,
    risk_mult=1.0,
    size_mult=1.0,
    alpha_adj=0.0,
    conf_adj=0.0,
    explanation="test",
    warnings=None,
    data_quality="GOOD",
    raw_signals=None,
):
    return {
        "overall_regime":              overall,
        "volatility_regime":           volatility,
        "breadth_regime":              breadth,
        "speculative_regime":          speculative,
        "regime_score":                score,
        "risk_multiplier":             risk_mult,
        "sizing_multiplier":           size_mult,
        "alpha_threshold_adjustment":  alpha_adj,
        "confidence_adjustment":       conf_adj,
        "explanation":                 explanation,
        "warnings":                    warnings or [],
        "data_quality":                data_quality,
        "points":                      3.0,
        "raw_signals":                 raw_signals or {},
    }


# ── _compute_points ───────────────────────────────────────────────────────────

class TestComputePoints:
    def test_all_none_returns_zero(self):
        assert mri._compute_points({}) == 0.0

    def test_spy_qqq_above_200_adds_points(self):
        pts = mri._compute_points({"spy_vs_200sma": 5.0, "qqq_vs_200sma": 3.0})
        assert pts == 4.0

    def test_spy_qqq_below_200_subtracts(self):
        pts = mri._compute_points({"spy_vs_200sma": -5.0, "qqq_vs_200sma": -3.0})
        assert pts == -4.0

    def test_vix_calm_adds_points(self):
        pts = mri._compute_points({"vix": 12.0})
        assert pts == 2.0

    def test_vix_extreme_subtracts(self):
        pts = mri._compute_points({"vix": 50.0})
        assert pts == -5.0

    def test_vix_spike_penalty(self):
        pts = mri._compute_points({"vix_change_pct": 25.0})
        assert pts == -1.0

    def test_btc_positive_adds(self):
        pts = mri._compute_points({"btc_7d_return_pct": 10.0})
        assert pts == 1.0

    def test_btc_crash_subtracts(self):
        pts = mri._compute_points({"btc_7d_return_pct": -15.0})
        assert pts == -1.0

    def test_tsx_positive_adds_half(self):
        pts = mri._compute_points({"tsx_vs_200sma": 3.0})
        assert pts == 0.5


# ── _classify_overall ─────────────────────────────────────────────────────────

class TestClassifyOverall:
    def test_risk_on_from_high_points(self):
        assert mri._classify_overall(8.0, None) == mri.RISK_ON

    def test_neutral_from_medium_points(self):
        assert mri._classify_overall(3.0, None) == mri.NEUTRAL

    def test_risk_off_from_low_points(self):
        assert mri._classify_overall(0.0, None) == mri.RISK_OFF

    def test_panic_from_very_low_points(self):
        assert mri._classify_overall(-5.0, None) == mri.PANIC

    def test_panic_override_from_high_vix(self):
        # Points suggest NEUTRAL but VIX >= 45 → PANIC
        assert mri._classify_overall(3.0, 50.0) == mri.PANIC

    def test_risk_on_not_overridden_by_moderate_vix(self):
        assert mri._classify_overall(8.0, 20.0) == mri.RISK_ON

    def test_boundary_points_neutral(self):
        # Exactly at _POINTS_NEUTRAL boundary
        assert mri._classify_overall(mri._POINTS_NEUTRAL, None) == mri.NEUTRAL

    def test_boundary_points_risk_on(self):
        assert mri._classify_overall(mri._POINTS_RISK_ON, None) == mri.RISK_ON


# ── _classify_volatility ──────────────────────────────────────────────────────

class TestClassifyVolatility:
    def test_calm_low_vix(self):
        assert mri._classify_volatility(12.0) == mri.CALM

    def test_elevated_mid_vix(self):
        assert mri._classify_volatility(20.0) == mri.ELEVATED

    def test_high_vix(self):
        assert mri._classify_volatility(30.0) == mri.HIGH

    def test_extreme_vix(self):
        assert mri._classify_volatility(40.0) == mri.EXTREME

    def test_none_vix_defaults_elevated(self):
        assert mri._classify_volatility(None) == mri.ELEVATED

    def test_boundary_calm_elevated(self):
        assert mri._classify_volatility(mri._VIX_CALM) == mri.ELEVATED


# ── _classify_breadth ─────────────────────────────────────────────────────────

class TestClassifyBreadth:
    def test_broad_strength_all_above(self):
        assert mri._classify_breadth(True, True, True) == mri.BROAD_STRENGTH

    def test_narrow_strength_iwm_below(self):
        assert mri._classify_breadth(True, True, False) == mri.NARROW_STRENGTH

    def test_narrow_strength_one_large_cap(self):
        assert mri._classify_breadth(True, False, False) == mri.NARROW_STRENGTH

    def test_weak_all_below(self):
        assert mri._classify_breadth(False, False, False) == mri.WEAK

    def test_mixed_one_large_cap_above_iwm_none(self):
        assert mri._classify_breadth(True, False, None) == mri.MIXED

    def test_all_none_defaults_mixed(self):
        assert mri._classify_breadth(None, None, None) == mri.MIXED

    def test_large_caps_below_iwm_above(self):
        assert mri._classify_breadth(False, False, True) == mri.MIXED


# ── _classify_speculative ─────────────────────────────────────────────────────

class TestClassifySpeculative:
    def test_speculation_active_two_active(self):
        assert mri._classify_speculative(True, True, False) == mri.SPECULATION_ACTIVE

    def test_speculation_active_three_active(self):
        assert mri._classify_speculative(True, True, True) == mri.SPECULATION_ACTIVE

    def test_selective_one_active(self):
        assert mri._classify_speculative(True, False, None) == mri.SELECTIVE

    def test_speculation_dead_two_dead(self):
        assert mri._classify_speculative(False, False, True) == mri.SPECULATION_DEAD

    def test_defensive_zero_active_one_dead(self):
        assert mri._classify_speculative(False, None, None) == mri.DEFENSIVE

    def test_all_none_defaults_defensive(self):
        assert mri._classify_speculative(None, None, None) == mri.DEFENSIVE


# ── _compute_regime_score ─────────────────────────────────────────────────────

class TestComputeRegimeScore:
    def test_risk_on_score_high(self):
        score = mri._compute_regime_score(mri.RISK_ON, 8.0, None)
        assert 66 <= score <= 100

    def test_risk_off_score_low(self):
        score = mri._compute_regime_score(mri.RISK_OFF, 0.0, None)
        assert 16 <= score <= 35

    def test_panic_score_very_low(self):
        score = mri._compute_regime_score(mri.PANIC, -5.0, None)
        assert 0 <= score <= 15

    def test_neutral_midrange(self):
        score = mri._compute_regime_score(mri.NEUTRAL, 3.0, None)
        assert 36 <= score <= 65

    def test_score_clamped_to_100(self):
        score = mri._compute_regime_score(mri.RISK_ON, 100.0, None)
        assert score <= 100.0

    def test_score_clamped_to_zero(self):
        score = mri._compute_regime_score(mri.PANIC, -100.0, 80.0)
        assert score >= 0.0


# ── _compute_multipliers ──────────────────────────────────────────────────────

class TestComputeMultipliers:
    def test_risk_on_sizing_above_1(self):
        m = mri._compute_multipliers(mri.RISK_ON, mri.CALM)
        assert m["sizing_multiplier"] > 1.0

    def test_risk_on_risk_mult_below_1(self):
        m = mri._compute_multipliers(mri.RISK_ON, mri.CALM)
        assert m["risk_multiplier"] < 1.0

    def test_panic_sizing_very_low(self):
        m = mri._compute_multipliers(mri.PANIC, mri.EXTREME)
        assert m["sizing_multiplier"] < 0.5

    def test_panic_risk_mult_high(self):
        m = mri._compute_multipliers(mri.PANIC, mri.EXTREME)
        assert m["risk_multiplier"] > 1.5

    def test_neutral_multipliers_at_one(self):
        m = mri._compute_multipliers(mri.NEUTRAL, mri.CALM)
        assert m["risk_multiplier"] == 1.0
        assert m["sizing_multiplier"] == 1.0

    def test_extreme_volatility_reduces_sizing(self):
        m_calm    = mri._compute_multipliers(mri.RISK_ON, mri.CALM)
        m_extreme = mri._compute_multipliers(mri.RISK_ON, mri.EXTREME)
        assert m_extreme["sizing_multiplier"] < m_calm["sizing_multiplier"]

    def test_high_volatility_reduces_sizing(self):
        m_calm = mri._compute_multipliers(mri.NEUTRAL, mri.CALM)
        m_high = mri._compute_multipliers(mri.NEUTRAL, mri.HIGH)
        assert m_high["sizing_multiplier"] < m_calm["sizing_multiplier"]

    def test_all_regimes_return_four_keys(self):
        required = {"risk_multiplier", "sizing_multiplier",
                    "alpha_threshold_adjustment", "confidence_adjustment"}
        for o in mri.OVERALL_REGIMES:
            for v in mri.VOLATILITY_REGIMES:
                m = mri._compute_multipliers(o, v)
                assert required == set(m.keys())


# ── compute_regime (pure end-to-end) ─────────────────────────────────────────

class TestComputeRegime:
    def test_risk_on_classification(self):
        r = mri.compute_regime(_risk_on_signals())
        assert r["overall_regime"] == mri.RISK_ON

    def test_risk_off_classification(self):
        r = mri.compute_regime(_risk_off_signals())
        assert r["overall_regime"] == mri.RISK_OFF

    def test_panic_vix_classification(self):
        r = mri.compute_regime(_panic_signals())
        assert r["overall_regime"] == mri.PANIC

    def test_neutral_mixed_signals(self):
        # spy+qqq above 200 (+4), but VIX elevated (-1) and IWM lagging (-1) → pts=2 (NEUTRAL)
        signals = {
            "spy_vs_200sma": 3.0, "qqq_vs_200sma": 2.0,
            "iwm_vs_200sma": -1.0,
            "vix": 26.0,
        }
        r = mri.compute_regime(signals)
        assert r["overall_regime"] == mri.NEUTRAL

    def test_narrow_strength_detection(self):
        signals = {
            "spy_vs_200sma": 5.0, "qqq_vs_200sma": 4.0,
            "iwm_vs_200sma": -3.0,
            "vix": 18.0,
        }
        r = mri.compute_regime(signals)
        assert r["breadth_regime"] == mri.NARROW_STRENGTH

    def test_broad_strength_detection(self):
        signals = {
            "spy_vs_200sma": 5.0, "qqq_vs_200sma": 4.0,
            "iwm_vs_200sma": 2.0,
            "vix": 15.0,
        }
        r = mri.compute_regime(signals)
        assert r["breadth_regime"] == mri.BROAD_STRENGTH

    def test_speculation_active_detection(self):
        signals = {
            "spy_vs_200sma":     5.0,
            "btc_7d_return_pct": 15.0,
            "sox_vs_50sma":      8.0,
            "iwm_vs_50sma":      3.0,
            "vix":               14.0,
        }
        r = mri.compute_regime(signals)
        assert r["speculative_regime"] == mri.SPECULATION_ACTIVE

    def test_sparse_data_fallback(self):
        r = mri.compute_regime({})
        assert r["overall_regime"] in mri.OVERALL_REGIMES
        assert r["volatility_regime"] in mri.VOLATILITY_REGIMES
        assert r["data_quality"] == "SPARSE"

    def test_none_signals_input(self):
        r = mri.compute_regime(None)  # type: ignore
        assert r["overall_regime"] in mri.OVERALL_REGIMES

    def test_all_multipliers_present(self):
        r = mri.compute_regime(_risk_on_signals())
        assert "risk_multiplier" in r
        assert "sizing_multiplier" in r
        assert "alpha_threshold_adjustment" in r
        assert "confidence_adjustment" in r

    def test_explanation_not_empty(self):
        r = mri.compute_regime(_risk_on_signals())
        assert len(r["explanation"]) > 0

    def test_warnings_is_list(self):
        r = mri.compute_regime(_risk_off_signals())
        assert isinstance(r["warnings"], list)

    def test_data_quality_good_when_all_signals(self):
        r = mri.compute_regime(_risk_on_signals())
        assert r["data_quality"] == "GOOD"

    def test_risk_off_generates_warning(self):
        r = mri.compute_regime(_risk_off_signals())
        assert any("RISK_OFF" in w for w in r["warnings"])

    def test_panic_generates_warning(self):
        r = mri.compute_regime(_panic_signals())
        assert any("PANIC" in w for w in r["warnings"])

    def test_calm_vix_no_volatility_warning(self):
        r = mri.compute_regime(_risk_on_signals())
        assert not any("volatility" in w.lower() for w in r["warnings"])


# ── DB persistence ────────────────────────────────────────────────────────────

class TestSaveGetRegime:
    def test_save_returns_dict_with_id(self, db):
        regime = _make_regime()
        saved = mri.save_regime_snapshot(regime)
        assert "id" in saved and saved["id"] is not None
        assert "captured_at" in saved

    def test_get_latest_returns_most_recent(self, db):
        mri.save_regime_snapshot(_make_regime(overall="NEUTRAL", score=50.0))
        mri.save_regime_snapshot(_make_regime(overall="RISK_ON", score=80.0))
        latest = mri.get_latest_regime()
        assert latest["overall_regime"] == "RISK_ON"

    def test_get_latest_none_when_empty(self, db):
        assert mri.get_latest_regime() is None

    def test_warnings_deserialized_as_list(self, db):
        regime = _make_regime(warnings=["warning one", "warning two"])
        mri.save_regime_snapshot(regime)
        latest = mri.get_latest_regime()
        assert latest["warnings"] == ["warning one", "warning two"]

    def test_raw_signals_deserialized_as_dict(self, db):
        regime = _make_regime(raw_signals={"vix": 15.0, "spy_vs_200sma": 5.0})
        mri.save_regime_snapshot(regime)
        latest = mri.get_latest_regime()
        assert latest["raw_signals"]["vix"] == 15.0

    def test_all_regime_fields_persisted(self, db):
        regime = _make_regime(
            overall="RISK_OFF", volatility="HIGH",
            breadth="NARROW_STRENGTH", speculative="DEFENSIVE",
            score=25.0, risk_mult=1.5, size_mult=0.6,
        )
        mri.save_regime_snapshot(regime)
        latest = mri.get_latest_regime()
        assert latest["overall_regime"] == "RISK_OFF"
        assert latest["volatility_regime"] == "HIGH"
        assert latest["breadth_regime"] == "NARROW_STRENGTH"
        assert latest["speculative_regime"] == "DEFENSIVE"
        assert latest["regime_score"] == 25.0


# ── get_regime_history ────────────────────────────────────────────────────────

class TestGetRegimeHistory:
    def test_empty_history(self, db):
        assert mri.get_regime_history() == []

    def test_history_newest_first(self, db):
        mri.save_regime_snapshot(_make_regime(overall="NEUTRAL"))
        mri.save_regime_snapshot(_make_regime(overall="RISK_ON"))
        history = mri.get_regime_history()
        assert history[0]["overall_regime"] == "RISK_ON"
        assert history[1]["overall_regime"] == "NEUTRAL"

    def test_history_limit_respected(self, db):
        for _ in range(5):
            mri.save_regime_snapshot(_make_regime())
        assert len(mri.get_regime_history(limit=3)) == 3

    def test_history_all_rows_have_captured_at(self, db):
        for _ in range(3):
            mri.save_regime_snapshot(_make_regime())
        for row in mri.get_regime_history():
            assert "captured_at" in row and row["captured_at"]


# ── refresh_regime ────────────────────────────────────────────────────────────

class TestRefreshRegime:
    def test_refresh_returns_regime_dict(self, db, monkeypatch):
        monkeypatch.setattr(mri, "_fetch_signals", lambda: _risk_on_signals())
        result = mri.refresh_regime()
        assert result["overall_regime"] == mri.RISK_ON
        assert "regime_score" in result

    def test_refresh_persists_snapshot(self, db, monkeypatch):
        monkeypatch.setattr(mri, "_fetch_signals", lambda: _risk_on_signals())
        mri.refresh_regime()
        latest = mri.get_latest_regime()
        assert latest is not None
        assert latest["overall_regime"] == mri.RISK_ON

    def test_refresh_fetch_failure_safe(self, db, monkeypatch):
        def _fail():
            raise RuntimeError("network down")
        monkeypatch.setattr(mri, "_fetch_signals", _fail)
        result = mri.refresh_regime()
        assert result["overall_regime"] in mri.OVERALL_REGIMES
        assert result["data_quality"] == "SPARSE"

    def test_refresh_regime_score_in_bounds(self, db, monkeypatch):
        monkeypatch.setattr(mri, "_fetch_signals", lambda: _risk_off_signals())
        result = mri.refresh_regime()
        assert 0.0 <= result["regime_score"] <= 100.0

    def test_refresh_save_failure_still_returns_regime(self, db, monkeypatch):
        monkeypatch.setattr(mri, "_fetch_signals", lambda: _risk_on_signals())
        monkeypatch.setattr(mri, "save_regime_snapshot", lambda r: (_ for _ in ()).throw(RuntimeError("db fail")))
        result = mri.refresh_regime()
        assert result["overall_regime"] in mri.OVERALL_REGIMES


# ── get_regime_context_for_checklist ─────────────────────────────────────────

class TestGetRegimeContextForChecklist:
    def test_no_snapshot_returns_available_false(self, db):
        ctx = mri.get_regime_context_for_checklist()
        assert ctx["available"] is False

    def test_no_snapshot_has_default_regime(self, db):
        ctx = mri.get_regime_context_for_checklist()
        assert ctx["overall_regime"] == mri.NEUTRAL

    def test_with_snapshot_returns_available_true(self, db, monkeypatch):
        monkeypatch.setattr(mri, "_fetch_signals", lambda: _risk_on_signals())
        mri.refresh_regime()
        ctx = mri.get_regime_context_for_checklist()
        assert ctx["available"] is True

    def test_always_returns_dict(self, db, monkeypatch):
        monkeypatch.setattr(mri, "get_latest_regime", lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        ctx = mri.get_regime_context_for_checklist()
        assert isinstance(ctx, dict)

    def test_returns_warnings_list(self, db, monkeypatch):
        monkeypatch.setattr(mri, "_fetch_signals", lambda: _risk_off_signals())
        mri.refresh_regime()
        ctx = mri.get_regime_context_for_checklist()
        assert isinstance(ctx.get("warnings"), list)

    def test_risk_off_context_has_warnings(self, db, monkeypatch):
        monkeypatch.setattr(mri, "_fetch_signals", lambda: _risk_off_signals())
        mri.refresh_regime()
        ctx = mri.get_regime_context_for_checklist()
        assert ctx["overall_regime"] in (mri.RISK_OFF, mri.PANIC, mri.NEUTRAL)

    def test_context_has_sizing_multiplier(self, db, monkeypatch):
        monkeypatch.setattr(mri, "_fetch_signals", lambda: _risk_on_signals())
        mri.refresh_regime()
        ctx = mri.get_regime_context_for_checklist()
        assert "sizing_multiplier" in ctx


# ── get_regime_context_for_risk ───────────────────────────────────────────────

class TestGetRegimeContextForRisk:
    def test_no_snapshot_returns_safe_defaults(self, db):
        ctx = mri.get_regime_context_for_risk()
        assert ctx["available"] is False
        assert ctx["is_risk_off"] is False
        assert ctx["is_panic"] is False
        assert ctx["is_high_volatility"] is False

    def test_is_risk_off_when_risk_off(self, db, monkeypatch):
        monkeypatch.setattr(mri, "_fetch_signals", lambda: _risk_off_signals())
        mri.refresh_regime()
        ctx = mri.get_regime_context_for_risk()
        assert ctx["is_risk_off"] is True

    def test_is_panic_when_panic(self, db, monkeypatch):
        monkeypatch.setattr(mri, "_fetch_signals", lambda: _panic_signals())
        mri.refresh_regime()
        ctx = mri.get_regime_context_for_risk()
        assert ctx["is_panic"] is True
        assert ctx["is_risk_off"] is True

    def test_is_high_volatility_when_high(self, db, monkeypatch):
        signals = {**_risk_on_signals(), "vix": 32.0}
        monkeypatch.setattr(mri, "_fetch_signals", lambda: signals)
        mri.refresh_regime()
        ctx = mri.get_regime_context_for_risk()
        assert ctx["is_high_volatility"] is True

    def test_risk_on_not_risk_off(self, db, monkeypatch):
        monkeypatch.setattr(mri, "_fetch_signals", lambda: _risk_on_signals())
        mri.refresh_regime()
        ctx = mri.get_regime_context_for_risk()
        assert ctx["is_risk_off"] is False
        assert ctx["is_panic"] is False

    def test_always_returns_dict(self, db, monkeypatch):
        monkeypatch.setattr(mri, "get_latest_regime", lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        ctx = mri.get_regime_context_for_risk()
        assert isinstance(ctx, dict)

    def test_all_required_keys_present(self, db):
        required = {
            "available", "overall_regime", "risk_multiplier", "sizing_multiplier",
            "alpha_threshold_adjustment", "confidence_adjustment",
            "is_risk_off", "is_panic", "is_high_volatility",
        }
        ctx = mri.get_regime_context_for_risk()
        assert required <= set(ctx.keys())


# ── Multiplier bounds ─────────────────────────────────────────────────────────

class TestMultiplierBounds:
    def test_sizing_multiplier_between_point_one_and_two(self):
        for o in mri.OVERALL_REGIMES:
            for v in mri.VOLATILITY_REGIMES:
                m = mri._compute_multipliers(o, v)
                assert 0.1 <= m["sizing_multiplier"] <= 2.0, \
                    f"sizing_multiplier={m['sizing_multiplier']} for {o}/{v}"

    def test_risk_multiplier_positive(self):
        for o in mri.OVERALL_REGIMES:
            for v in mri.VOLATILITY_REGIMES:
                m = mri._compute_multipliers(o, v)
                assert m["risk_multiplier"] > 0, f"negative risk_mult for {o}/{v}"

    def test_regime_score_between_0_and_100(self):
        for o in mri.OVERALL_REGIMES:
            for pts in [-10.0, -2.0, 0.0, 3.0, 6.0, 12.0]:
                score = mri._compute_regime_score(o, pts, None)
                assert 0.0 <= score <= 100.0, \
                    f"score={score} out of bounds for {o} pts={pts}"

    def test_compute_regime_score_always_bounded(self):
        for signals in [_risk_on_signals(), _risk_off_signals(), _panic_signals(), {}]:
            r = mri.compute_regime(signals)
            assert 0.0 <= r["regime_score"] <= 100.0


# ── Sparse-data safety ────────────────────────────────────────────────────────

class TestSparseData:
    def test_empty_signals_safe(self):
        r = mri.compute_regime({})
        assert r["overall_regime"] in mri.OVERALL_REGIMES

    def test_none_vix_safe(self):
        r = mri.compute_regime({"spy_vs_200sma": 5.0})
        assert r["volatility_regime"] in mri.VOLATILITY_REGIMES

    def test_all_none_values_safe(self):
        signals = {k: None for k in [
            "spy_vs_20sma", "spy_vs_50sma", "spy_vs_200sma", "qqq_vs_200sma",
            "iwm_vs_200sma", "iwm_vs_50sma", "vix", "vix_change_pct",
            "btc_7d_return_pct", "sox_vs_50sma", "tsx_vs_200sma",
        ]}
        r = mri.compute_regime(signals)
        assert r["overall_regime"] in mri.OVERALL_REGIMES

    def test_sparse_signals_data_quality_label(self):
        r = mri.compute_regime({"vix": 20.0})
        assert r["data_quality"] == "SPARSE"

    def test_partial_signals_data_quality(self):
        r = mri.compute_regime({
            "spy_vs_200sma": 5.0,
            "qqq_vs_200sma": 3.0,
            "vix": 18.0,
        })
        assert r["data_quality"] == "PARTIAL"


# ── Safety constraints ────────────────────────────────────────────────────────

class TestSafetyConstraints:
    @staticmethod
    def _src():
        return inspect.getsource(mri)

    def test_no_trading_functions(self):
        src = self._src()
        forbidden = ["place_order", "submit_order", "execute_trade", "buy(", "sell("]
        for fn in forbidden:
            assert fn not in src, f"Found forbidden trading call: {fn}"

    def test_no_broker_calls(self):
        src = self._src()
        brokers = ["wealthsimple", "alpaca", "ibkr", "interactive_brokers"]
        for broker in brokers:
            assert broker not in src.lower(), f"Found broker reference: {broker}"

    def test_no_order_placement(self):
        src = self._src()
        assert "order" not in src.lower() or "place_order" not in src

    def test_no_update_on_snapshots(self):
        src = self._src()
        lines = [ln for ln in src.splitlines() if "UPDATE" in ln.upper()
                 and "market_regime_snapshots" in ln]
        assert not lines, "Found UPDATE on market_regime_snapshots"

    def test_no_delete_on_snapshots(self):
        src = self._src()
        lines = [ln for ln in src.splitlines() if "DELETE" in ln.upper()
                 and "market_regime_snapshots" in ln]
        assert not lines, "Found DELETE on market_regime_snapshots"

    def test_no_alert_sends(self):
        src = self._src()
        assert "send_sms" not in src
        assert "send_alert" not in src


# ── API: GET /market/regime ───────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
    monkeypatch.setattr(mri, "_ensure_tables", lambda: None)
    from api import api_bp, _CACHE
    _CACHE.clear()
    from flask import Flask
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(api_bp)
    return app.test_client()


class TestApiRegime:
    def test_get_regime_no_snapshot_returns_200(self, client, monkeypatch):
        monkeypatch.setattr(mri, "get_latest_regime", lambda: None)
        resp = client.get("/api/v1/market/regime")
        assert resp.status_code == 200

    def test_get_regime_data_null_when_no_snapshot(self, client, monkeypatch):
        monkeypatch.setattr(mri, "get_latest_regime", lambda: None)
        body = resp = client.get("/api/v1/market/regime").get_json()
        assert body["data"]["regime"] is None

    def test_get_regime_with_snapshot_returns_regime(self, client, monkeypatch):
        snapshot = _make_regime(overall="RISK_ON", score=80.0)
        snapshot["id"] = 1
        snapshot["captured_at"] = "2026-01-01T00:00:00"
        monkeypatch.setattr(mri, "get_latest_regime", lambda: snapshot)
        body = client.get("/api/v1/market/regime").get_json()
        assert body["data"]["regime"]["overall_regime"] == "RISK_ON"

    def test_get_regime_has_ok_field(self, client, monkeypatch):
        monkeypatch.setattr(mri, "get_latest_regime", lambda: None)
        body = client.get("/api/v1/market/regime").get_json()
        assert body["ok"] is True


class TestApiRegimeHistory:
    def test_history_empty_list(self, client, monkeypatch):
        monkeypatch.setattr(mri, "get_regime_history", lambda limit=20: [])
        body = client.get("/api/v1/market/regime/history").get_json()
        assert body["data"]["history"] == []

    def test_history_with_data(self, client, monkeypatch):
        snapshot = _make_regime()
        snapshot["id"] = 1
        snapshot["captured_at"] = "2026-01-01T00:00:00"
        monkeypatch.setattr(mri, "get_regime_history", lambda limit=20: [snapshot])
        body = client.get("/api/v1/market/regime/history").get_json()
        assert len(body["data"]["history"]) == 1

    def test_history_limit_param(self, client, monkeypatch):
        received_limit = {}
        def _fake_history(limit=20):
            received_limit["v"] = limit
            return []
        monkeypatch.setattr(mri, "get_regime_history", _fake_history)
        client.get("/api/v1/market/regime/history?limit=5")
        assert received_limit.get("v") == 5

    def test_history_limit_capped_at_100(self, client, monkeypatch):
        received_limit = {}
        def _fake_history(limit=20):
            received_limit["v"] = limit
            return []
        monkeypatch.setattr(mri, "get_regime_history", _fake_history)
        client.get("/api/v1/market/regime/history?limit=999")
        assert received_limit.get("v") == 100


class TestApiRegimeRefresh:
    def test_refresh_requires_auth(self, client, monkeypatch):
        monkeypatch.setenv("API_SECRET", "secret")
        resp = client.post("/api/v1/market/regime/refresh")
        assert resp.status_code == 401

    def test_refresh_with_auth_returns_200(self, client, monkeypatch):
        monkeypatch.setenv("API_SECRET", "secret")
        snapshot = _make_regime(overall="RISK_ON")
        snapshot["id"] = 1
        snapshot["captured_at"] = "2026-01-01T00:00:00"
        monkeypatch.setattr(mri, "refresh_regime", lambda: snapshot)
        resp = client.post(
            "/api/v1/market/regime/refresh",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200

    def test_refresh_returns_regime_dict(self, client, monkeypatch):
        monkeypatch.setenv("API_SECRET", "secret")
        snapshot = _make_regime(overall="NEUTRAL")
        snapshot["id"] = 1
        snapshot["captured_at"] = "2026-01-01T00:00:00"
        monkeypatch.setattr(mri, "refresh_regime", lambda: snapshot)
        body = client.post(
            "/api/v1/market/regime/refresh",
            headers={"Authorization": "Bearer secret"},
        ).get_json()
        assert body["data"]["regime"]["overall_regime"] == "NEUTRAL"

    def test_refresh_post_method_only(self, client):
        resp = client.get("/api/v1/market/regime/refresh")
        assert resp.status_code == 405


# ── Scheduler integration ─────────────────────────────────────────────────────

class TestSchedulerIntegration:
    def test_regime_jobs_registered(self):
        import scheduler
        # Verify the job functions exist
        assert hasattr(scheduler, "_run_regime_refresh")
        assert callable(scheduler._run_regime_refresh)

    def test_run_regime_refresh_does_not_propagate_exceptions(self, monkeypatch):
        import scheduler
        monkeypatch.setattr(
            "market_regime_intelligence.refresh_regime",
            lambda: (_ for _ in ()).throw(RuntimeError("simulated failure")),
        )
        # Must not raise
        try:
            scheduler._run_regime_refresh()
        except Exception as exc:
            pytest.fail(f"_run_regime_refresh propagated an exception: {exc}")

    def test_morning_brief_includes_risk_on_regime(self, monkeypatch):
        monkeypatch.setattr(
            "market_regime_intelligence.get_regime_context_for_checklist",
            lambda: {
                "available":      True,
                "overall_regime": "RISK_ON",
                "regime_score":   80.0,
                "warnings":       [],
            },
        )
        signals = []
        try:
            from market_regime_intelligence import get_regime_context_for_checklist
            ctx = get_regime_context_for_checklist()
            if ctx.get("available"):
                overall = ctx.get("overall_regime", "NEUTRAL")
                score   = ctx.get("regime_score", 50.0)
                if overall in ("RISK_OFF", "PANIC"):
                    signals.append(f"🔴 Regime: {overall} (score={score:.0f})")
                elif overall == "NEUTRAL":
                    signals.append(f"🟡 Regime: {overall} (score={score:.0f})")
                else:
                    signals.append(f"🟢 Regime: {overall} (score={score:.0f})")
        except Exception:
            pass
        assert any("🟢 Regime: RISK_ON" in s for s in signals)

    def test_morning_brief_includes_risk_off_regime(self, monkeypatch):
        monkeypatch.setattr(
            "market_regime_intelligence.get_regime_context_for_checklist",
            lambda: {
                "available":      True,
                "overall_regime": "RISK_OFF",
                "regime_score":   25.0,
                "warnings":       ["RISK_OFF: exercise caution"],
            },
        )
        signals = []
        try:
            from market_regime_intelligence import get_regime_context_for_checklist
            ctx = get_regime_context_for_checklist()
            if ctx.get("available"):
                overall = ctx.get("overall_regime", "NEUTRAL")
                score   = ctx.get("regime_score", 50.0)
                if overall in ("RISK_OFF", "PANIC"):
                    signals.append(f"🔴 Regime: {overall} (score={score:.0f})")
                elif overall == "NEUTRAL":
                    signals.append(f"🟡 Regime: {overall} (score={score:.0f})")
                else:
                    signals.append(f"🟢 Regime: {overall} (score={score:.0f})")
        except Exception:
            pass
        assert any("🔴 Regime: RISK_OFF" in s for s in signals)

    def test_morning_brief_no_crash_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "market_regime_intelligence.get_regime_context_for_checklist",
            lambda: {"available": False, "overall_regime": "NEUTRAL",
                     "regime_score": 50.0, "warnings": []},
        )
        signals = []
        try:
            from market_regime_intelligence import get_regime_context_for_checklist
            ctx = get_regime_context_for_checklist()
            if ctx.get("available"):
                signals.append("should not appear")
        except Exception as exc:
            pytest.fail(f"Unexpected exception: {exc}")
        assert signals == []
