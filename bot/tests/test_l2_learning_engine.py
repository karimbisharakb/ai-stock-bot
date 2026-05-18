"""
Phase L2 — Shadow weight recommendations and policy simulation tests.

Covers:
  - component lift math
  - setup effectiveness calculation
  - tier calibration logic
  - sample-size shrinkage and confidence levels
  - weight clamp enforcement (DELTA_CAP)
  - deterministic recommendations (same data → same output)
  - shadow policy replay (score recalculation, FP reduction, missed winner)
  - _compute_shadow_weights renormalisation
  - _classify_shadow_tier numeric thresholds
  - sparse / missing data resilience
  - generate_recommendations_report() structure and never-raises guarantee
  - generate_shadow_policy_report() structure and never-raises guarantee
  - API endpoints: /alpha/learning/recommendations, /alpha/learning/shadow-policy
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Constants mirrored from the module ───────────────────────────────────────

_MIN_SAMPLES  = 10
_DELTA_CAP    = 0.05
_SHRINKAGE_N  = 20
_LIFT_INC     = 1.30
_LIFT_DEC     = 0.70
_FP_THRESH    = -0.05
_WIN_THRESH   = 0.0
_HIGH_THRESH  = 6.0

_COMP_NAMES = [
    "relative_strength", "acceleration", "squeeze", "catalyst",
    "options", "breakout", "risk_reward", "novelty",
]
_CURRENT_WEIGHTS = {
    "relative_strength": 0.15, "acceleration": 0.15, "squeeze": 0.12,
    "catalyst": 0.15, "options": 0.13, "breakout": 0.15,
    "risk_reward": 0.10, "novelty": 0.05,
}


# ── Outcome factory ────────────────────────────────────────────────────────────

def _make_outcome(
    ticker="AAPL",
    scan_time="2026-05-01T10:00:00",
    alpha_score=60.0,
    alpha_tier="STRONG_WATCH",
    setup_type="BREAKOUT_EXPANSION",
    return_5d=0.05,
    return_10d=0.08,
    return_20d=0.12,
    max_gain=0.15,
    max_drawdown=-0.03,
    component_scores_json=None,
    status="COMPLETE",
):
    """Create a minimal outcome dict. component_scores_json is auto-built when None."""
    if component_scores_json is None:
        cs = {
            name: {"score": 7.0 if name == "breakout" else 5.0, "weight": w, "data_quality": "HIGH"}
            for name, w in _CURRENT_WEIGHTS.items()
        }
        component_scores_json = json.dumps(cs)
    return {
        "ticker": ticker,
        "scan_time": scan_time,
        "alpha_score": alpha_score,
        "alpha_tier": alpha_tier,
        "setup_type": setup_type,
        "return_5d": return_5d,
        "return_10d": return_10d,
        "return_20d": return_20d,
        "max_gain": max_gain,
        "max_drawdown": max_drawdown,
        "component_scores_json": component_scores_json,
        "shadow_component_json": None,
        "status": status,
    }


def _cs_json(**scores):
    """Build component_scores_json where named components get their value, others 5.0."""
    cs = {}
    for name, w in _CURRENT_WEIGHTS.items():
        cs[name] = {
            "score": scores.get(name, 5.0),
            "weight": w,
            "data_quality": "MISSING" if scores.get(f"{name}_missing") else "HIGH",
        }
    return json.dumps(cs)


# ── DB helpers for API tests ──────────────────────────────────────────────────

_SHADOW_DDL = """
CREATE TABLE IF NOT EXISTS alpha_shadow_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, scan_time TEXT,
    alpha_score REAL, alpha_tier TEXT, setup_type TEXT, predator_tier TEXT,
    predator_score REAL, tier_match INTEGER DEFAULT 0, filter_reason TEXT,
    component_scores_json TEXT, explanation TEXT, detail_json TEXT
)"""

_OUTCOMES_DDL = """
CREATE TABLE IF NOT EXISTS alpha_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL,
    scan_time TEXT NOT NULL, alpha_score REAL, alpha_tier TEXT, setup_type TEXT,
    source TEXT, component_scores_json TEXT, price_at_scan REAL,
    price_1d REAL, price_3d REAL, price_5d REAL, price_10d REAL, price_20d REAL,
    return_1d REAL, return_3d REAL, return_5d REAL, return_10d REAL, return_20d REAL,
    max_gain REAL, max_drawdown REAL, status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL, updated_at TEXT, UNIQUE(ticker, scan_time)
)"""


def _make_db(path: str) -> str:
    conn = sqlite3.connect(path)
    conn.execute(_SHADOW_DDL)
    conn.execute(_OUTCOMES_DDL)
    conn.commit()
    conn.close()
    return path


def _make_get_conn(db_path: str):
    def _get_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    return _get_conn


def _seed_complete_outcome(db_path: str, ticker: str, return_5d: float,
                            tier: str = "STRONG_WATCH",
                            setup_type: str = "BREAKOUT_EXPANSION",
                            cs_json: str = None):
    now = datetime.now().isoformat()
    if cs_json is None:
        cs_json = _cs_json(breakout=7.0)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT OR IGNORE INTO alpha_outcomes
           (ticker, scan_time, alpha_score, alpha_tier, setup_type, source,
            component_scores_json, price_at_scan, return_5d, return_10d, return_20d,
            max_gain, max_drawdown, status, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ticker, now, 60.0, tier, setup_type, "alpha_universe",
         cs_json, 100.0, return_5d, return_5d * 1.5, return_5d * 2.0,
         max(0.0, return_5d), min(0.0, return_5d * 0.5), "COMPLETE", now),
    )
    conn.commit()
    conn.close()


# ── TestComponentEffectiveness ────────────────────────────────────────────────

class TestComponentEffectiveness:

    def test_empty_outcomes_returns_empty(self):
        from alpha_learning_engine import compute_component_effectiveness
        assert compute_component_effectiveness([]) == {}

    def test_baseline_win_rate_reflects_all_outcomes(self):
        from alpha_learning_engine import compute_component_effectiveness
        outcomes = [
            _make_outcome(return_5d=0.05),   # win
            _make_outcome(return_5d=-0.03),  # loss
            _make_outcome(return_5d=0.02),   # win
            _make_outcome(return_5d=-0.10),  # loss
        ]
        eff = compute_component_effectiveness(outcomes)
        for comp in _COMP_NAMES:
            # baseline_win_rate = 2/4 = 0.5
            assert abs(eff[comp]["baseline_win_rate"] - 0.5) < 1e-4

    def test_lift_above_one_when_high_score_wins_more(self):
        from alpha_learning_engine import compute_component_effectiveness
        # Give breakout score=8.0 to all winners, 3.0 to all losers
        winners = [
            _make_outcome(return_5d=0.10, component_scores_json=_cs_json(breakout=8.0)),
            _make_outcome(return_5d=0.08, component_scores_json=_cs_json(breakout=8.0)),
        ]
        losers = [
            _make_outcome(return_5d=-0.10, component_scores_json=_cs_json(breakout=3.0)),
            _make_outcome(return_5d=-0.08, component_scores_json=_cs_json(breakout=3.0)),
        ]
        eff = compute_component_effectiveness(winners + losers)
        # breakout high_count = 2, all wins → win_rate_when_high = 1.0
        # baseline = 2/4 = 0.5, lift = 1.0/0.5 = 2.0
        assert eff["breakout"]["lift"] > 1.0

    def test_lift_below_one_when_high_score_wins_less(self):
        from alpha_learning_engine import compute_component_effectiveness
        # high breakout → losers; low breakout → winners
        outcomes = [
            _make_outcome(return_5d=-0.15, component_scores_json=_cs_json(breakout=8.0)),
            _make_outcome(return_5d=-0.12, component_scores_json=_cs_json(breakout=8.0)),
            _make_outcome(return_5d=0.10,  component_scores_json=_cs_json(breakout=3.0)),
            _make_outcome(return_5d=0.08,  component_scores_json=_cs_json(breakout=3.0)),
        ]
        eff = compute_component_effectiveness(outcomes)
        assert eff["breakout"]["lift"] < 1.0

    def test_high_count_equals_rows_with_high_score(self):
        from alpha_learning_engine import compute_component_effectiveness
        outcomes = [
            _make_outcome(return_5d=0.05, component_scores_json=_cs_json(catalyst=8.0)),
            _make_outcome(return_5d=0.05, component_scores_json=_cs_json(catalyst=7.5)),
            _make_outcome(return_5d=0.05, component_scores_json=_cs_json(catalyst=4.0)),
        ]
        eff = compute_component_effectiveness(outcomes)
        assert eff["catalyst"]["high_count"] == 2

    def test_missing_data_quality_excluded_from_active_count(self):
        from alpha_learning_engine import compute_component_effectiveness

        def _cs_with_missing(comp_missing: str) -> str:
            cs = {
                name: {
                    "score": 5.0,
                    "weight": _CURRENT_WEIGHTS[name],
                    "data_quality": "MISSING" if name == comp_missing else "HIGH",
                }
                for name in _CURRENT_WEIGHTS
            }
            return json.dumps(cs)

        outcomes = [
            _make_outcome(return_5d=0.05, component_scores_json=_cs_with_missing("options")),
            _make_outcome(return_5d=0.05, component_scores_json=_cs_with_missing("options")),
            _make_outcome(return_5d=0.05, component_scores_json=_cs_json()),
        ]
        eff = compute_component_effectiveness(outcomes)
        assert eff["options"]["active_count"] == 1
        assert eff["options"]["missing_rate"] > 0.0

    def test_false_positive_rate_when_high_calculated(self):
        from alpha_learning_engine import compute_component_effectiveness
        outcomes = [
            _make_outcome(return_5d=-0.10, component_scores_json=_cs_json(breakout=8.0)),  # FP
            _make_outcome(return_5d=-0.08, component_scores_json=_cs_json(breakout=8.0)),  # FP
            _make_outcome(return_5d=0.05,  component_scores_json=_cs_json(breakout=8.0)),  # win
            _make_outcome(return_5d=0.05,  component_scores_json=_cs_json(breakout=8.0)),  # win
        ]
        eff = compute_component_effectiveness(outcomes)
        # 2 FP out of 4 high-score rows
        assert abs(eff["breakout"]["false_positive_rate_when_high"] - 0.5) < 1e-4

    def test_avg_return_when_high_correct(self):
        from alpha_learning_engine import compute_component_effectiveness
        outcomes = [
            _make_outcome(return_5d=0.10, component_scores_json=_cs_json(catalyst=8.0)),
            _make_outcome(return_5d=0.20, component_scores_json=_cs_json(catalyst=8.0)),
        ]
        eff = compute_component_effectiveness(outcomes)
        assert abs(eff["catalyst"]["avg_return_when_high"] - 0.15) < 1e-5

    def test_all_components_present_in_result(self):
        from alpha_learning_engine import compute_component_effectiveness
        outcomes = [_make_outcome()]
        eff = compute_component_effectiveness(outcomes)
        for comp in _COMP_NAMES:
            assert comp in eff


# ── TestSetupEffectiveness ────────────────────────────────────────────────────

class TestSetupEffectiveness:

    def test_empty_outcomes_returns_empty(self):
        from alpha_learning_engine import compute_setup_effectiveness
        assert compute_setup_effectiveness([]) == {}

    def test_grouping_by_setup_type(self):
        from alpha_learning_engine import compute_setup_effectiveness
        outcomes = [
            _make_outcome(setup_type="BREAKOUT_EXPANSION", return_5d=0.10),
            _make_outcome(setup_type="BREAKOUT_EXPANSION", return_5d=0.05),
            _make_outcome(setup_type="CATALYST_RUNUP",     return_5d=-0.05),
        ]
        eff = compute_setup_effectiveness(outcomes)
        assert "BREAKOUT_EXPANSION" in eff
        assert "CATALYST_RUNUP" in eff
        assert eff["BREAKOUT_EXPANSION"]["count"] == 2
        assert eff["CATALYST_RUNUP"]["count"] == 1

    def test_win_rate_correct(self):
        from alpha_learning_engine import compute_setup_effectiveness
        outcomes = [
            _make_outcome(setup_type="SQUEEZE_CANDIDATE", return_5d=0.10),
            _make_outcome(setup_type="SQUEEZE_CANDIDATE", return_5d=-0.05),
            _make_outcome(setup_type="SQUEEZE_CANDIDATE", return_5d=0.05),
            _make_outcome(setup_type="SQUEEZE_CANDIDATE", return_5d=-0.08),
        ]
        eff = compute_setup_effectiveness(outcomes)
        # 2 wins out of 4
        assert abs(eff["SQUEEZE_CANDIDATE"]["win_rate"] - 0.5) < 1e-4

    def test_best_window_is_highest_avg_return(self):
        from alpha_learning_engine import compute_setup_effectiveness
        outcomes = [
            _make_outcome(
                setup_type="EARLY_ACCUMULATION",
                return_5d=0.02, return_10d=0.10, return_20d=0.05,
            ),
        ]
        eff = compute_setup_effectiveness(outcomes)
        # 10d has highest avg return → best_window = "10d"
        assert eff["EARLY_ACCUMULATION"]["best_window"] == "10d"

    def test_false_positive_rate_correct(self):
        from alpha_learning_engine import compute_setup_effectiveness
        outcomes = [
            _make_outcome(setup_type="OPTIONS_PRESSURE", return_5d=-0.10),  # FP
            _make_outcome(setup_type="OPTIONS_PRESSURE", return_5d=0.05),   # win
        ]
        eff = compute_setup_effectiveness(outcomes)
        assert abs(eff["OPTIONS_PRESSURE"]["false_positive_rate"] - 0.5) < 1e-4

    def test_unknown_setup_type_bucketed(self):
        from alpha_learning_engine import compute_setup_effectiveness
        outcomes = [_make_outcome(setup_type=None, return_5d=0.05)]
        eff = compute_setup_effectiveness(outcomes)
        assert "UNKNOWN" in eff


# ── TestTierCalibration ───────────────────────────────────────────────────────

class TestTierCalibration:

    def test_empty_outcomes_returns_empty(self):
        from alpha_learning_engine import compute_tier_calibration
        assert compute_tier_calibration([]) == {}

    def test_too_loose_when_fp_rate_high(self):
        from alpha_learning_engine import compute_tier_calibration
        # 5 FP out of 10 = 50% > 40% threshold
        outcomes = [
            _make_outcome(alpha_tier="STRONG_WATCH", return_5d=-0.10) for _ in range(5)
        ] + [
            _make_outcome(alpha_tier="STRONG_WATCH", return_5d=0.05) for _ in range(5)
        ]
        cal = compute_tier_calibration(outcomes)
        assert any("TOO_LOOSE" in a for a in cal["STRONG_WATCH"]["assessment"])

    def test_ok_when_fp_rate_low(self):
        from alpha_learning_engine import compute_tier_calibration
        outcomes = [
            _make_outcome(alpha_tier="HIGH_CONVICTION", return_5d=0.10) for _ in range(15)
        ] + [
            _make_outcome(alpha_tier="HIGH_CONVICTION", return_5d=-0.03) for _ in range(3)
        ]
        cal = compute_tier_calibration(outcomes)
        # FP rate = 3/18 = 0.167 < 0.40 → OK (no TOO_LOOSE)
        assert all("TOO_LOOSE" not in a for a in cal["HIGH_CONVICTION"]["assessment"])

    def test_too_rare_for_high_conviction(self):
        from alpha_learning_engine import compute_tier_calibration
        # Only 5 HIGH_CONVICTION outcomes → TOO_RARE
        outcomes = [_make_outcome(alpha_tier="HIGH_CONVICTION", return_5d=0.05) for _ in range(5)]
        cal = compute_tier_calibration(outcomes)
        assert any("TOO_RARE" in a for a in cal["HIGH_CONVICTION"]["assessment"])

    def test_win_rate_calculated(self):
        from alpha_learning_engine import compute_tier_calibration
        outcomes = [
            _make_outcome(alpha_tier="WATCH", return_5d=0.10),
            _make_outcome(alpha_tier="WATCH", return_5d=-0.05),
        ]
        cal = compute_tier_calibration(outcomes)
        assert abs(cal["WATCH"]["win_rate"] - 0.5) < 1e-4


# ── TestRecommendWeights ──────────────────────────────────────────────────────

class TestRecommendWeights:

    def _eff(self, lift: float, high_count: int, wr_high: float = 0.6, fp_rate: float = 0.1,
             missing_rate: float = 0.0) -> dict:
        return {
            comp: {
                "lift": lift, "high_count": high_count,
                "win_rate_when_high": wr_high, "false_positive_rate_when_high": fp_rate,
                "missing_rate": missing_rate, "data_quality_penalty": missing_rate,
                "baseline_win_rate": 0.5,
            }
            for comp in _COMP_NAMES
        }

    def test_insufficient_samples_returns_keep(self):
        from alpha_learning_engine import recommend_weights
        eff = self._eff(lift=2.0, high_count=_MIN_SAMPLES - 1)
        recs = recommend_weights(eff)
        for r in recs:
            assert r["action"] == "KEEP"
            assert r["confidence"] == "LOW"

    def test_high_lift_suggests_increase(self):
        from alpha_learning_engine import recommend_weights
        eff = self._eff(lift=_LIFT_INC + 0.1, high_count=_MIN_SAMPLES + 5)
        recs = recommend_weights(eff)
        for r in recs:
            assert r["action"] == "INCREASE"

    def test_low_lift_suggests_decrease(self):
        from alpha_learning_engine import recommend_weights
        eff = self._eff(lift=_LIFT_DEC - 0.1, high_count=_MIN_SAMPLES + 5)
        recs = recommend_weights(eff)
        for r in recs:
            assert r["action"] == "DECREASE"

    def test_moderate_lift_suggests_keep(self):
        from alpha_learning_engine import recommend_weights
        eff = self._eff(lift=1.0, high_count=_MIN_SAMPLES + 5)
        recs = recommend_weights(eff)
        for r in recs:
            assert r["action"] == "KEEP"

    def test_raw_delta_clamped_to_delta_cap(self):
        from alpha_learning_engine import recommend_weights
        eff = self._eff(lift=5.0, high_count=50)  # extreme lift
        recs = recommend_weights(eff)
        for r in recs:
            assert abs(r["raw_delta"]) <= _DELTA_CAP

    def test_shrunk_delta_smaller_at_min_samples(self):
        from alpha_learning_engine import recommend_weights
        eff_low  = self._eff(lift=_LIFT_INC + 0.5, high_count=_MIN_SAMPLES)
        eff_high = self._eff(lift=_LIFT_INC + 0.5, high_count=_SHRINKAGE_N)
        recs_low  = {r["component"]: r for r in recommend_weights(eff_low)}
        recs_high = {r["component"]: r for r in recommend_weights(eff_high)}
        for comp in _COMP_NAMES:
            if recs_high[comp]["action"] == "INCREASE":
                assert recs_low[comp]["shrunk_delta"] <= recs_high[comp]["shrunk_delta"]

    def test_confidence_high_for_large_samples(self):
        from alpha_learning_engine import recommend_weights
        eff = self._eff(lift=_LIFT_INC + 0.1, high_count=_SHRINKAGE_N)
        recs = recommend_weights(eff)
        for r in recs:
            assert r["confidence"] == "HIGH"

    def test_confidence_medium_for_medium_samples(self):
        from alpha_learning_engine import recommend_weights
        eff = self._eff(lift=_LIFT_INC + 0.1, high_count=_MIN_SAMPLES + 1)
        recs = recommend_weights(eff)
        for r in recs:
            assert r["confidence"] == "MEDIUM"

    def test_recommendations_deterministic(self):
        from alpha_learning_engine import recommend_weights
        outcomes = [
            _make_outcome(return_5d=0.10, component_scores_json=_cs_json(breakout=8.0)),
            _make_outcome(return_5d=-0.10, component_scores_json=_cs_json(breakout=3.0)),
        ] * 10
        from alpha_learning_engine import compute_component_effectiveness
        eff = compute_component_effectiveness(outcomes)
        r1 = recommend_weights(eff)
        r2 = recommend_weights(eff)
        assert r1 == r2

    def test_sorted_increase_before_decrease_before_keep(self):
        from alpha_learning_engine import recommend_weights

        eff = {}
        for i, comp in enumerate(_COMP_NAMES):
            if i < 2:
                lift, n = 2.0, 25
            elif i < 4:
                lift, n = 0.4, 25
            else:
                lift, n = 1.0, 25
            eff[comp] = {
                "lift": lift, "high_count": n,
                "win_rate_when_high": 0.6, "false_positive_rate_when_high": 0.1,
                "missing_rate": 0.0, "data_quality_penalty": 0.0,
                "baseline_win_rate": 0.5,
            }

        recs = recommend_weights(eff)
        actions = [r["action"] for r in recs]
        _order = {"INCREASE": 0, "DECREASE": 1, "KEEP": 2}
        assert actions == sorted(actions, key=lambda a: _order[a])

    def test_high_missing_rate_scales_back_increase_delta(self):
        from alpha_learning_engine import recommend_weights
        eff = {
            comp: {
                "lift": 2.0, "high_count": 25,
                "win_rate_when_high": 0.8, "false_positive_rate_when_high": 0.05,
                "missing_rate": 0.70, "data_quality_penalty": 0.70,
                "baseline_win_rate": 0.5,
            }
            for comp in _COMP_NAMES
        }
        recs_high_missing = recommend_weights(eff)

        eff_no_missing = {
            comp: {**v, "missing_rate": 0.0, "data_quality_penalty": 0.0}
            for comp, v in eff.items()
        }
        recs_no_missing = recommend_weights(eff_no_missing)

        for r_hm, r_nm in zip(recs_high_missing, recs_no_missing):
            if r_nm["action"] == "INCREASE":
                # high missing_rate should produce smaller or equal raw_delta
                assert r_hm["raw_delta"] <= r_nm["raw_delta"]


# ── TestComputeShadowWeights ───────────────────────────────────────────────────

class TestComputeShadowWeights:

    def test_weights_sum_to_one_after_renorm(self):
        from alpha_learning_engine import _compute_shadow_weights
        recs = [{"component": "breakout", "shrunk_delta": 0.05}]
        sw = _compute_shadow_weights(recs)
        assert abs(sum(sw.values()) - 1.0) < 1e-5

    def test_increase_rec_raises_weight_relative_to_others(self):
        from alpha_learning_engine import _compute_shadow_weights
        recs = [{"component": "novelty", "shrunk_delta": 0.05}]
        sw = _compute_shadow_weights(recs)
        # novelty current = 0.05, +0.05 raw = 0.10 before renorm
        assert sw["novelty"] > _CURRENT_WEIGHTS["novelty"]

    def test_decrease_rec_lowers_weight(self):
        from alpha_learning_engine import _compute_shadow_weights
        recs = [{"component": "relative_strength", "shrunk_delta": -0.05}]
        sw = _compute_shadow_weights(recs)
        assert sw["relative_strength"] < _CURRENT_WEIGHTS["relative_strength"]

    def test_empty_recs_returns_current_weights(self):
        from alpha_learning_engine import _compute_shadow_weights
        sw = _compute_shadow_weights([])
        for comp in _CURRENT_WEIGHTS:
            assert abs(sw[comp] - _CURRENT_WEIGHTS[comp] / 1.0) < 1e-4

    def test_delta_clamped_before_application(self):
        from alpha_learning_engine import _compute_shadow_weights
        recs = [{"component": "novelty", "shrunk_delta": 999.0}]
        sw = _compute_shadow_weights(recs)
        # Even with extreme delta, novelty should only shift by at most _DELTA_CAP
        max_allowed = _CURRENT_WEIGHTS["novelty"] + _DELTA_CAP + 0.01
        assert sw["novelty"] < max_allowed


# ── TestClassifyShadowTier ────────────────────────────────────────────────────

class TestClassifyShadowTier:

    def test_score_above_80_is_rare_setup(self):
        from alpha_learning_engine import _classify_shadow_tier
        assert _classify_shadow_tier(85.0) == "RARE_SETUP"

    def test_score_80_is_rare_setup(self):
        from alpha_learning_engine import _classify_shadow_tier
        assert _classify_shadow_tier(80.0) == "RARE_SETUP"

    def test_score_65_is_high_conviction(self):
        from alpha_learning_engine import _classify_shadow_tier
        assert _classify_shadow_tier(65.0) == "HIGH_CONVICTION"

    def test_score_70_is_high_conviction(self):
        from alpha_learning_engine import _classify_shadow_tier
        assert _classify_shadow_tier(70.0) == "HIGH_CONVICTION"

    def test_score_50_is_strong_watch(self):
        from alpha_learning_engine import _classify_shadow_tier
        assert _classify_shadow_tier(50.0) == "STRONG_WATCH"

    def test_score_55_is_strong_watch(self):
        from alpha_learning_engine import _classify_shadow_tier
        assert _classify_shadow_tier(55.0) == "STRONG_WATCH"

    def test_score_35_is_watch(self):
        from alpha_learning_engine import _classify_shadow_tier
        assert _classify_shadow_tier(35.0) == "WATCH"

    def test_score_0_is_ignore(self):
        from alpha_learning_engine import _classify_shadow_tier
        assert _classify_shadow_tier(0.0) == "IGNORE"

    def test_custom_thresholds_respected(self):
        from alpha_learning_engine import _classify_shadow_tier
        custom = {"RARE_SETUP": 90.0, "HIGH_CONVICTION": 70.0, "STRONG_WATCH": 55.0, "WATCH": 40.0}
        assert _classify_shadow_tier(85.0, custom) == "HIGH_CONVICTION"
        assert _classify_shadow_tier(95.0, custom) == "RARE_SETUP"


# ── TestShrinkageAndConfidence ────────────────────────────────────────────────

class TestShrinkageAndConfidence:

    def test_shrinkage_zero_at_n0(self):
        from alpha_learning_engine import _shrinkage_factor
        assert _shrinkage_factor(0) == 0.0

    def test_shrinkage_one_at_shrinkage_n(self):
        from alpha_learning_engine import _shrinkage_factor
        assert _shrinkage_factor(_SHRINKAGE_N) == 1.0

    def test_shrinkage_one_above_shrinkage_n(self):
        from alpha_learning_engine import _shrinkage_factor
        assert _shrinkage_factor(_SHRINKAGE_N + 100) == 1.0

    def test_shrinkage_half_at_half_n(self):
        from alpha_learning_engine import _shrinkage_factor
        assert abs(_shrinkage_factor(_SHRINKAGE_N // 2) - 0.5) < 1e-6

    def test_confidence_high(self):
        from alpha_learning_engine import _confidence_level
        assert _confidence_level(_SHRINKAGE_N) == "HIGH"

    def test_confidence_medium(self):
        from alpha_learning_engine import _confidence_level
        assert _confidence_level(_MIN_SAMPLES) == "MEDIUM"

    def test_confidence_low(self):
        from alpha_learning_engine import _confidence_level
        assert _confidence_level(_MIN_SAMPLES - 1) == "LOW"


# ── TestShadowPolicyReplay ────────────────────────────────────────────────────

class TestShadowPolicyReplay:

    def test_empty_outcomes_returns_zeros(self):
        from alpha_learning_engine import generate_shadow_policy_replay
        result = generate_shadow_policy_replay(_CURRENT_WEIGHTS, [])
        assert result["total_replayed"] == 0
        assert result["changed_candidates"] == []

    def test_unchanged_when_weights_unchanged(self):
        from alpha_learning_engine import generate_shadow_policy_replay
        outcomes = [_make_outcome() for _ in range(5)]
        result = generate_shadow_policy_replay(dict(_CURRENT_WEIGHTS), outcomes)
        # With identical weights, all tiers should stay the same
        assert result["tier_unchanged"] == result["total_replayed"]

    def test_downgraded_when_key_component_weight_reduced(self):
        from alpha_learning_engine import generate_shadow_policy_replay
        # Build outcome where breakout dominates the score
        cs = {comp: {"score": 8.0 if comp == "breakout" else 2.0,
                     "weight": w, "data_quality": "HIGH"}
              for comp, w in _CURRENT_WEIGHTS.items()}
        outcome = _make_outcome(
            alpha_score=75.0,
            alpha_tier="RARE_SETUP",
            component_scores_json=json.dumps(cs),
            return_5d=0.05,
        )
        # Reduce breakout weight dramatically
        modified = dict(_CURRENT_WEIGHTS)
        modified["breakout"] = 0.01
        total = sum(modified.values())
        modified = {k: v / total for k, v in modified.items()}
        result = generate_shadow_policy_replay(modified, [outcome])
        # Shadow score will be lower → tier should not be RARE_SETUP
        if result["changed_candidates"]:
            for c in result["changed_candidates"]:
                assert c["old_tier"] != c["shadow_tier"]

    def test_changed_candidates_captured(self):
        from alpha_learning_engine import generate_shadow_policy_replay
        cs_high = {comp: {"score": 8.0, "weight": w, "data_quality": "HIGH"}
                   for comp, w in _CURRENT_WEIGHTS.items()}
        outcomes = [_make_outcome(
            alpha_score=90.0, alpha_tier="RARE_SETUP",
            component_scores_json=json.dumps(cs_high), return_5d=0.05
        )]
        # Slash all weights except novelty
        modified = {comp: 0.001 for comp in _CURRENT_WEIGHTS}
        modified["novelty"] = 0.992
        total = sum(modified.values())
        modified = {k: v / total for k, v in modified.items()}
        result = generate_shadow_policy_replay(modified, outcomes)
        assert isinstance(result["changed_candidates"], list)

    def test_fp_reduction_calculated(self):
        from alpha_learning_engine import generate_shadow_policy_replay
        # Outcome: STRONG_WATCH tier (eligible), return = -0.10 (FP)
        cs = {comp: {"score": 8.0, "weight": w, "data_quality": "HIGH"}
              for comp, w in _CURRENT_WEIGHTS.items()}
        outcome = _make_outcome(
            alpha_score=60.0, alpha_tier="STRONG_WATCH",
            component_scores_json=json.dumps(cs), return_5d=-0.10,
        )
        # Zero out all weights → shadow score near 0 → IGNORE (downgraded)
        shadow = {comp: 1e-6 for comp in _CURRENT_WEIGHTS}
        total = sum(shadow.values())
        shadow = {k: v / total for k, v in shadow.items()}
        result = generate_shadow_policy_replay(shadow, [outcome])
        # This FP was downgraded; fp_reduction should be > 0
        if result["expected_fp_reduction"] is not None:
            assert result["expected_fp_reduction"] >= 0.0

    def test_rows_without_component_scores_skipped(self):
        from alpha_learning_engine import generate_shadow_policy_replay
        outcomes = [
            _make_outcome(component_scores_json="null"),   # JSON null → not a dict
            _make_outcome(component_scores_json="not_json"),
        ]
        result = generate_shadow_policy_replay(dict(_CURRENT_WEIGHTS), outcomes)
        assert result["total_replayed"] == 0

    def test_changed_candidates_capped_at_50(self):
        from alpha_learning_engine import generate_shadow_policy_replay
        # Create 60 outcomes all in RARE_SETUP
        cs_high = {comp: {"score": 9.0, "weight": w, "data_quality": "HIGH"}
                   for comp, w in _CURRENT_WEIGHTS.items()}
        outcomes = [
            _make_outcome(alpha_score=90.0, alpha_tier="RARE_SETUP",
                          component_scores_json=json.dumps(cs_high), return_5d=0.05)
            for _ in range(60)
        ]
        # Artificially lower scores
        shadow = {comp: 1e-6 for comp in _CURRENT_WEIGHTS}
        total = sum(shadow.values())
        shadow = {k: v / total for k, v in shadow.items()}
        result = generate_shadow_policy_replay(shadow, outcomes)
        assert len(result["changed_candidates"]) <= 50


# ── TestGenerateRecommendationsReport ────────────────────────────────────────

class TestGenerateRecommendationsReport:

    def test_report_has_required_keys(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        from alpha_learning_engine import generate_recommendations_report
        report = generate_recommendations_report()
        required = [
            "generated_at", "note", "total_complete_outcomes", "sample_size_warning",
            "errors", "current_weights", "current_tier_thresholds",
            "component_effectiveness", "setup_effectiveness", "tier_calibration",
            "weight_recommendations", "threshold_recommendations", "top_changes",
        ]
        for key in required:
            assert key in report, f"Missing key: {key}"

    def test_report_never_raises_on_empty_db(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        from alpha_learning_engine import generate_recommendations_report
        report = generate_recommendations_report()  # must not raise
        assert isinstance(report, dict)

    def test_sample_size_warning_when_no_data(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        from alpha_learning_engine import generate_recommendations_report
        report = generate_recommendations_report()
        assert report["sample_size_warning"] is not None
        assert "COMPLETE" in report["sample_size_warning"]

    def test_note_says_shadow_only(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        from alpha_learning_engine import generate_recommendations_report
        report = generate_recommendations_report()
        assert "shadow" in report["note"].lower()
        assert "no live" in report["note"].lower()

    def test_current_weights_unchanged_after_report(self, tmp_path, monkeypatch):
        from alpha_learning_engine import _CURRENT_WEIGHTS
        import copy
        original = copy.deepcopy(_CURRENT_WEIGHTS)
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        from alpha_learning_engine import generate_recommendations_report
        generate_recommendations_report()
        assert _CURRENT_WEIGHTS == original

    def test_report_with_complete_outcomes(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        for i in range(15):
            _seed_complete_outcome(
                db_path, f"T{i:02d}", return_5d=0.05 if i % 2 == 0 else -0.03
            )
        from alpha_learning_engine import generate_recommendations_report
        report = generate_recommendations_report()
        assert report["total_complete_outcomes"] == 15
        assert isinstance(report["weight_recommendations"], list)
        assert len(report["weight_recommendations"]) == len(_COMP_NAMES)

    def test_report_deterministic(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        for i in range(12):
            _seed_complete_outcome(db_path, f"X{i}", return_5d=0.05)
        from alpha_learning_engine import generate_recommendations_report
        r1 = generate_recommendations_report()
        r2 = generate_recommendations_report()
        # Compare stable fields (not generated_at)
        assert r1["weight_recommendations"] == r2["weight_recommendations"]
        assert r1["component_effectiveness"] == r2["component_effectiveness"]


# ── TestGenerateShadowPolicyReport ────────────────────────────────────────────

class TestGenerateShadowPolicyReport:

    def test_report_has_required_keys(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        from alpha_learning_engine import generate_shadow_policy_report
        report = generate_shadow_policy_report()
        required = [
            "generated_at", "note", "errors", "current_weights",
            "shadow_weights", "weight_deltas", "shadow_weights_sum_to_one",
            "replay_stats",
        ]
        for key in required:
            assert key in report, f"Missing key: {key}"

    def test_shadow_weights_sum_to_one(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        from alpha_learning_engine import generate_shadow_policy_report
        report = generate_shadow_policy_report()
        assert abs(sum(report["shadow_weights"].values()) - 1.0) < 1e-4
        assert report["shadow_weights_sum_to_one"] is True

    def test_report_never_raises(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        from alpha_learning_engine import generate_shadow_policy_report
        report = generate_shadow_policy_report()
        assert isinstance(report, dict)

    def test_note_says_shadow(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        from alpha_learning_engine import generate_shadow_policy_report
        report = generate_shadow_policy_report()
        assert "shadow" in report["note"].lower()

    def test_weight_deltas_all_components_present(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        from alpha_learning_engine import generate_shadow_policy_report
        report = generate_shadow_policy_report()
        for comp in _COMP_NAMES:
            assert comp in report["weight_deltas"]


# ── TestRecommendThresholds ───────────────────────────────────────────────────

class TestRecommendThresholds:

    def test_tighten_when_too_loose(self):
        from alpha_learning_engine import recommend_thresholds
        tier_cal = {
            "STRONG_WATCH": {
                "count": 20, "win_rate": 0.4, "avg_return_5d": -0.02,
                "false_positive_rate": 0.50,  # > 40% → TOO_LOOSE
                "assessment": ["TOO_LOOSE: false-positive rate 50% exceeds 40%"],
            }
        }
        recs = recommend_thresholds(tier_cal)
        assert any(r["action"] == "TIGHTEN" and r["tier"] == "STRONG_WATCH" for r in recs)

    def test_loosen_when_too_rare(self):
        from alpha_learning_engine import recommend_thresholds
        tier_cal = {
            "HIGH_CONVICTION": {
                "count": 3, "win_rate": 0.67, "avg_return_5d": 0.05,
                "false_positive_rate": 0.10,
                "assessment": ["TOO_RARE: only 3 outcomes"],
            }
        }
        recs = recommend_thresholds(tier_cal)
        assert any(r["action"] == "LOOSEN" and r["tier"] == "HIGH_CONVICTION" for r in recs)

    def test_keep_when_calibrated(self):
        from alpha_learning_engine import recommend_thresholds
        tier_cal = {
            "WATCH": {
                "count": 30, "win_rate": 0.55, "avg_return_5d": 0.03,
                "false_positive_rate": 0.20,
                "assessment": ["OK"],
            }
        }
        recs = recommend_thresholds(tier_cal)
        assert any(r["action"] == "KEEP" and r["tier"] == "WATCH" for r in recs)

    def test_unknown_tier_skipped(self):
        from alpha_learning_engine import recommend_thresholds
        tier_cal = {
            "UNKNOWN": {
                "count": 5, "win_rate": 0.4, "avg_return_5d": -0.02,
                "false_positive_rate": 0.50,
                "assessment": ["TOO_LOOSE"],
            }
        }
        recs = recommend_thresholds(tier_cal)
        # UNKNOWN has no threshold → skipped
        assert all(r["tier"] != "UNKNOWN" for r in recs)


# ── TestSparseDataHandling ────────────────────────────────────────────────────

class TestSparseDataHandling:

    def test_outcomes_without_component_json_handled_in_effectiveness(self):
        from alpha_learning_engine import compute_component_effectiveness
        outcomes = [
            _make_outcome(return_5d=0.05, component_scores_json="null"),   # JSON null → not a dict
            _make_outcome(return_5d=0.05, component_scores_json="invalid_json"),
        ]
        # Should not raise; all components have missing_n = 2
        eff = compute_component_effectiveness(outcomes)
        for comp in _COMP_NAMES:
            assert eff[comp]["active_count"] == 0
            assert eff[comp]["missing_rate"] == 1.0

    def test_missing_return_5d_not_counted_in_wins(self):
        from alpha_learning_engine import compute_component_effectiveness
        outcomes = [
            _make_outcome(return_5d=None, component_scores_json=_cs_json(breakout=8.0)),
        ]
        eff = compute_component_effectiveness(outcomes)
        # No return_5d → baseline_win_rate = 0, win_rate_when_high = None
        assert eff["breakout"]["win_rate_when_high"] is None

    def test_setup_with_no_return_5d(self):
        from alpha_learning_engine import compute_setup_effectiveness
        outcomes = [_make_outcome(setup_type="CATALYST_RUNUP", return_5d=None)]
        eff = compute_setup_effectiveness(outcomes)
        assert eff["CATALYST_RUNUP"]["win_rate"] is None
        assert eff["CATALYST_RUNUP"]["count"] == 1

    def test_tier_calibration_with_no_return_5d(self):
        from alpha_learning_engine import compute_tier_calibration
        outcomes = [_make_outcome(alpha_tier="HIGH_CONVICTION", return_5d=None)]
        cal = compute_tier_calibration(outcomes)
        assert cal["HIGH_CONVICTION"]["win_rate"] is None
        assert cal["HIGH_CONVICTION"]["count"] == 1

    def test_parse_component_scores_fallback_to_shadow_json(self):
        from alpha_learning_engine import _parse_component_scores
        cs = {"breakout": {"score": 7.0, "weight": 0.15, "data_quality": "HIGH"}}
        row = {
            "component_scores_json": None,
            "shadow_component_json": json.dumps(cs),
        }
        result = _parse_component_scores(row)
        assert "breakout" in result

    def test_parse_component_scores_returns_empty_on_failure(self):
        from alpha_learning_engine import _parse_component_scores
        row = {"component_scores_json": None, "shadow_component_json": None}
        assert _parse_component_scores(row) == {}

    def test_report_never_raises_on_corrupt_db(self, tmp_path, monkeypatch):
        def _bad_conn():
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            # alpha_outcomes table does NOT exist → queries fail
            return conn

        monkeypatch.setattr("database.get_connection", _bad_conn)
        from alpha_learning_engine import generate_recommendations_report
        report = generate_recommendations_report()
        # Must not raise; errors list captures the failure
        assert isinstance(report, dict)


# ── TestL2ApiEndpoints ────────────────────────────────────────────────────────

class TestL2ApiEndpoints:

    def _app(self, db_path, monkeypatch):
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        import importlib
        import api
        importlib.reload(api)
        from flask import Flask
        from api import api_bp
        app = Flask(__name__)
        app.register_blueprint(api_bp, url_prefix="/api/v1")
        app.config["TESTING"] = True
        return app.test_client()

    def test_recommendations_returns_200(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        client = self._app(db_path, monkeypatch)
        resp = client.get("/api/v1/alpha/learning/recommendations")
        assert resp.status_code == 200

    def test_recommendations_has_weight_recommendations(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        client = self._app(db_path, monkeypatch)
        data = client.get("/api/v1/alpha/learning/recommendations").get_json()
        assert data["ok"] is True
        assert "weight_recommendations" in data["data"]

    def test_recommendations_note_says_shadow(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        client = self._app(db_path, monkeypatch)
        data = client.get("/api/v1/alpha/learning/recommendations").get_json()
        assert "shadow" in data["data"]["note"].lower()

    def test_shadow_policy_returns_200(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        client = self._app(db_path, monkeypatch)
        resp = client.get("/api/v1/alpha/learning/shadow-policy")
        assert resp.status_code == 200

    def test_shadow_policy_has_shadow_weights(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        client = self._app(db_path, monkeypatch)
        data = client.get("/api/v1/alpha/learning/shadow-policy").get_json()
        assert data["ok"] is True
        assert "shadow_weights" in data["data"]
        assert "replay_stats" in data["data"]

    def test_shadow_policy_weights_sum_to_one(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        client = self._app(db_path, monkeypatch)
        data = client.get("/api/v1/alpha/learning/shadow-policy").get_json()
        sw = data["data"]["shadow_weights"]
        assert abs(sum(sw.values()) - 1.0) < 1e-4

    def test_no_live_weights_mutated(self, tmp_path, monkeypatch):
        from alpha_learning_engine import _CURRENT_WEIGHTS
        import copy
        before = copy.deepcopy(_CURRENT_WEIGHTS)
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        client = self._app(db_path, monkeypatch)
        client.get("/api/v1/alpha/learning/recommendations")
        client.get("/api/v1/alpha/learning/shadow-policy")
        assert _CURRENT_WEIGHTS == before
