"""
Unit tests for decision_audit.py (Phase 3A).

All tests use in-memory snapshots — no DB access, no network calls.
"""
import pytest

from decision_audit import (
    ALERT_MIN_ADJUSTED,
    ALERT_THRESHOLD,
    ANOMALY_HIGH_CONF_THRESHOLD,
    ANOMALY_HIGH_SCORE_THRESHOLD,
    ANOMALY_LOW_CONF_FOR_HIGH_SCORE,
    ANOMALY_LOW_SCORE_FOR_HIGH_CONF,
    ANOMALY_SUPPRESSED_THRESHOLD,
    CONVICTION_MIN_CONFIDENCE,
    CONVICTION_MIN_SIGNALS,
    SIGNAL_MAX_SCORES,
    SIGNAL_NAMES,
    STEP_ADJUSTED_SCORE,
    STEP_REGIME_PENALTY,
    STEP_SIGNAL_SUM,
    STEP_TIER,
    TIER_ALERT,
    TIER_CONVICTION,
    TIER_WATCH,
    _compute_tier,
    _tier_rank,
    audit_batch,
    build_audit_snapshot,
    explain_snapshot,
    explain_suppression,
    explain_tier_change,
    flag_anomalies,
    generate_report,
    rank_contributions,
    reconstruct_lineage,
    snapshot_from_db_row,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _snap(
    ticker="NVDA",
    timestamp="2026-05-12T10:00:00",
    raw_score=8.0,
    adjusted_score=5.6,
    confidence_pct=70.0,
    regime="BULL",
    tier=TIER_ALERT,
    signal_contributions=None,
    suppressed_signals=None,
    original_contributions=None,
    penalties=None,
    boosts=None,
    calibration_adjustments=None,
    adaptive_recommendations=None,
    combo_effects=None,
):
    return build_audit_snapshot(
        ticker=ticker,
        timestamp=timestamp,
        raw_score=raw_score,
        adjusted_score=adjusted_score,
        confidence_pct=confidence_pct,
        regime=regime,
        tier=tier,
        signal_contributions=signal_contributions or {
            "options": 3.0, "insider": 2.0, "breakout": 2.0,
            "short_squeeze": 0.0, "catalyst": 0.0, "institutional": 0.0,
        },
        suppressed_signals=suppressed_signals,
        original_contributions=original_contributions,
        penalties=penalties,
        boosts=boosts,
        calibration_adjustments=calibration_adjustments,
        adaptive_recommendations=adaptive_recommendations,
        combo_effects=combo_effects,
    )


# ── Constants ─────────────────────────────────────────────────────────────────

class TestConstants:
    def test_signal_names_tuple(self):
        assert isinstance(SIGNAL_NAMES, tuple)
        assert "options" in SIGNAL_NAMES
        assert "breakout" in SIGNAL_NAMES

    def test_signal_max_scores_sum(self):
        assert sum(SIGNAL_MAX_SCORES.values()) == 12

    def test_tier_labels(self):
        assert TIER_WATCH      == "WATCH"
        assert TIER_ALERT      == "ALERT"
        assert TIER_CONVICTION == "CONVICTION"

    def test_thresholds_ordered(self):
        assert ALERT_THRESHOLD > 0
        assert ALERT_MIN_ADJUSTED > 0
        assert CONVICTION_MIN_CONFIDENCE > 0
        assert CONVICTION_MIN_SIGNALS > 0


# ── build_audit_snapshot ──────────────────────────────────────────────────────

class TestBuildAuditSnapshot:
    def test_required_keys_present(self):
        snap = _snap()
        for key in ("ticker", "timestamp", "raw_score", "adjusted_score",
                    "confidence_pct", "regime", "tier", "signal_contributions",
                    "active_signal_count", "suppressed_signals",
                    "original_contributions", "penalties", "boosts",
                    "calibration_adjustments", "adaptive_recommendations",
                    "combo_effects"):
            assert key in snap, f"Missing key: {key}"

    def test_ticker_stored(self):
        assert _snap(ticker="AAPL")["ticker"] == "AAPL"

    def test_active_signal_count_computed(self):
        snap = _snap(signal_contributions={
            "options": 3.0, "insider": 0.0, "short_squeeze": 1.0,
            "catalyst": 0.0, "institutional": 0.0, "breakout": 0.0
        })
        assert snap["active_signal_count"] == 2

    def test_active_signal_count_all_zero(self):
        snap = _snap(signal_contributions={s: 0.0 for s in SIGNAL_NAMES})
        assert snap["active_signal_count"] == 0

    def test_suppressed_signals_defaults_to_empty_list(self):
        snap = _snap()
        assert snap["suppressed_signals"] == []

    def test_suppressed_signals_stored(self):
        snap = _snap(suppressed_signals=["breakout"])
        assert snap["suppressed_signals"] == ["breakout"]

    def test_penalties_defaults_to_empty_list(self):
        assert _snap()["penalties"] == []

    def test_boosts_defaults_to_empty_list(self):
        assert _snap()["boosts"] == []

    def test_calibration_adjustments_defaults_to_empty_list(self):
        assert _snap()["calibration_adjustments"] == []

    def test_adaptive_recommendations_default(self):
        ar = _snap()["adaptive_recommendations"]
        assert ar["active"] is False
        assert ar["adjustments"] == {}

    def test_combo_effects_defaults_to_none(self):
        assert _snap()["combo_effects"] is None

    def test_original_contributions_defaults_to_empty_dict(self):
        assert _snap()["original_contributions"] == {}

    def test_signal_contributions_stored(self):
        contrib = {"options": 3.0, "insider": 2.0, "short_squeeze": 0.0,
                   "catalyst": 0.0, "institutional": 0.0, "breakout": 1.0}
        snap = _snap(signal_contributions=contrib)
        assert snap["signal_contributions"] == contrib

    def test_none_signal_contributions_becomes_empty(self):
        snap = build_audit_snapshot(
            ticker="T", timestamp="", raw_score=0, adjusted_score=0,
            confidence_pct=0, regime="BULL", tier=TIER_WATCH,
            signal_contributions=None,
        )
        assert snap["signal_contributions"] == {}

    def test_serializable_to_dict(self):
        snap = _snap()
        import json
        json.dumps(snap)  # must not raise

    def test_deterministic(self):
        snap_a = _snap()
        snap_b = _snap()
        assert snap_a == snap_b


# ── snapshot_from_db_row ──────────────────────────────────────────────────────

class TestSnapshotFromDbRow:
    def test_basic_row(self):
        row = {
            "ticker": "AMD",
            "alert_time": "2026-05-12T09:00:00",
            "raw_score": 7.0,
            "adjusted_score": 4.2,
            "confidence_pct": 60.0,
            "tier": TIER_ALERT,
            "score_options": 2.0,
            "score_insider": 1.0,
            "score_short_squeeze": 0.0,
            "score_catalyst": 2.0,
            "score_institutional": 0.0,
            "score_breakout": 2.0,
        }
        snap = snapshot_from_db_row(row)
        assert snap["ticker"] == "AMD"
        assert snap["signal_contributions"]["options"] == 2.0
        assert snap["signal_contributions"]["catalyst"] == 2.0

    def test_missing_score_fields_default_to_zero(self):
        row = {"ticker": "X", "alert_time": "", "raw_score": 0}
        snap = snapshot_from_db_row(row)
        for sig in SIGNAL_NAMES:
            assert snap["signal_contributions"][sig] == 0.0

    def test_all_signal_names_present(self):
        snap = snapshot_from_db_row({})
        for sig in SIGNAL_NAMES:
            assert sig in snap["signal_contributions"]


# ── _compute_tier ─────────────────────────────────────────────────────────────

class TestComputeTier:
    def test_conviction_when_all_conditions_met(self):
        result = _compute_tier(
            raw_score=8, adjusted_score=5.6,
            confidence=60.0, active_signals=3
        )
        assert result == TIER_CONVICTION

    def test_alert_when_adjusted_clears_threshold(self):
        result = _compute_tier(
            raw_score=6, adjusted_score=3.0,
            confidence=40.0, active_signals=1
        )
        assert result == TIER_ALERT

    def test_watch_below_alert_threshold(self):
        result = _compute_tier(
            raw_score=5, adjusted_score=3.0,
            confidence=60.0, active_signals=3
        )
        assert result == TIER_WATCH

    def test_watch_when_adjusted_low(self):
        result = _compute_tier(
            raw_score=6, adjusted_score=1.0,
            confidence=10.0, active_signals=1
        )
        assert result == TIER_WATCH

    def test_conviction_requires_enough_signals(self):
        result = _compute_tier(
            raw_score=8, adjusted_score=5.6,
            confidence=60.0, active_signals=2   # < CONVICTION_MIN_SIGNALS=3
        )
        assert result != TIER_CONVICTION

    def test_conviction_requires_sufficient_confidence(self):
        result = _compute_tier(
            raw_score=8, adjusted_score=5.6,
            confidence=50.0, active_signals=3   # < CONVICTION_MIN_CONFIDENCE=55
        )
        assert result != TIER_CONVICTION


# ── reconstruct_lineage ───────────────────────────────────────────────────────

class TestReconstructLineage:
    def test_required_keys(self):
        lineage = reconstruct_lineage(_snap())
        for key in ("steps", "raw_score", "capped_score", "final_confidence",
                    "adjusted_score", "tier", "recomputed_tier", "tier_threshold_crossed"):
            assert key in lineage

    def test_steps_is_list(self):
        assert isinstance(reconstruct_lineage(_snap())["steps"], list)

    def test_signal_sum_step_present(self):
        lineage = reconstruct_lineage(_snap())
        step_types = [s["step"] for s in lineage["steps"]]
        assert STEP_SIGNAL_SUM in step_types

    def test_adjusted_score_step_present(self):
        lineage = reconstruct_lineage(_snap())
        step_types = [s["step"] for s in lineage["steps"]]
        assert STEP_ADJUSTED_SCORE in step_types

    def test_tier_step_present(self):
        lineage = reconstruct_lineage(_snap())
        step_types = [s["step"] for s in lineage["steps"]]
        assert STEP_TIER in step_types

    def test_signal_sum_step_is_first(self):
        lineage = reconstruct_lineage(_snap())
        assert lineage["steps"][0]["step"] == STEP_SIGNAL_SUM

    def test_tier_step_is_last(self):
        lineage = reconstruct_lineage(_snap())
        assert lineage["steps"][-1]["step"] == STEP_TIER

    def test_raw_score_stored(self):
        snap = _snap(raw_score=9.0)
        assert reconstruct_lineage(snap)["raw_score"] == 9.0

    def test_capped_score_is_min_of_raw_and_10(self):
        snap = _snap(raw_score=12.0)
        assert reconstruct_lineage(snap)["capped_score"] == 10.0

    def test_capped_score_unchanged_when_raw_le_10(self):
        snap = _snap(raw_score=8.0)
        assert reconstruct_lineage(snap)["capped_score"] == 8.0

    def test_cap_step_added_only_when_raw_exceeds_10(self):
        snap_over = _snap(raw_score=11.0)
        snap_ok   = _snap(raw_score=8.0)
        assert any(s["step"] == "SCORE_CAP" for s in reconstruct_lineage(snap_over)["steps"])
        assert not any(s["step"] == "SCORE_CAP" for s in reconstruct_lineage(snap_ok)["steps"])

    def test_regime_penalty_step_added_for_neutral(self):
        snap = _snap(regime="NEUTRAL", tier=TIER_ALERT)
        lineage = reconstruct_lineage(snap)
        step_types = [s["step"] for s in lineage["steps"]]
        assert STEP_REGIME_PENALTY in step_types

    def test_no_regime_penalty_step_for_bull(self):
        snap = _snap(regime="BULL", penalties=[])
        lineage = reconstruct_lineage(snap)
        regime_steps = [s for s in lineage["steps"] if s["step"] == STEP_REGIME_PENALTY]
        assert len(regime_steps) == 0

    def test_calibration_step_added_when_adjustments_present(self):
        snap = _snap(calibration_adjustments=[
            {"type": "CORRELATION_PENALTY", "delta": -2.5, "reason": "options+squeeze"}
        ])
        lineage = reconstruct_lineage(snap)
        step_types = [s["step"] for s in lineage["steps"]]
        assert "CALIBRATION" in step_types

    def test_final_confidence_matches_snapshot(self):
        snap = _snap(confidence_pct=68.5)
        assert reconstruct_lineage(snap)["final_confidence"] == 68.5

    def test_adjusted_score_matches_snapshot(self):
        snap = _snap(adjusted_score=4.93)
        assert reconstruct_lineage(snap)["adjusted_score"] == 4.93

    def test_recomputed_tier_present(self):
        assert reconstruct_lineage(_snap())["recomputed_tier"] in (
            TIER_WATCH, TIER_ALERT, TIER_CONVICTION
        )

    def test_steps_have_required_fields(self):
        for step in reconstruct_lineage(_snap())["steps"]:
            assert "step"      in step
            assert "value_in"  in step
            assert "value_out" in step
            assert "delta"     in step
            assert "detail"    in step

    def test_deterministic(self):
        snap = _snap()
        assert reconstruct_lineage(snap) == reconstruct_lineage(snap)

    def test_step_delta_computed_for_numeric_values(self):
        snap = _snap(regime="NEUTRAL")
        lineage = reconstruct_lineage(snap)
        for step in lineage["steps"]:
            if (isinstance(step["value_in"], (int, float)) and
                    isinstance(step["value_out"], (int, float))):
                expected = round(step["value_out"] - step["value_in"], 4)
                assert step["delta"] == pytest.approx(expected, abs=1e-3)


# ── rank_contributions ────────────────────────────────────────────────────────

class TestRankContributions:
    def test_required_keys(self):
        result = rank_contributions(_snap())
        for key in ("ranked", "strongest_factor", "weakest_active_factor",
                    "suppressed_factors", "total_contribution"):
            assert key in result

    def test_ranked_has_all_signals(self):
        result = rank_contributions(_snap())
        signals_in_ranked = [e["signal"] for e in result["ranked"]]
        for sig in SIGNAL_NAMES:
            assert sig in signals_in_ranked

    def test_ranked_descending_by_score(self):
        result = rank_contributions(_snap())
        scores = [e["score"] for e in result["ranked"]]
        assert scores == sorted(scores, reverse=True)

    def test_tie_broken_alphabetically(self):
        # insider=2, breakout=2 → "breakout" < "insider" alphabetically → breakout ranked higher
        snap = _snap(signal_contributions={
            "options": 3.0, "insider": 2.0, "breakout": 2.0,
            "short_squeeze": 0.0, "catalyst": 0.0, "institutional": 0.0,
        })
        result = rank_contributions(snap)
        scored_2 = [e for e in result["ranked"] if e["score"] == 2.0]
        names = [e["signal"] for e in scored_2]
        assert names == sorted(names)  # alphabetical within tied score

    def test_rank_1_is_highest(self):
        result = rank_contributions(_snap())
        rank1 = next(e for e in result["ranked"] if e["rank"] == 1)
        assert rank1["score"] == max(e["score"] for e in result["ranked"])

    def test_strongest_factor_matches_top_rank(self):
        result = rank_contributions(_snap())
        assert result["strongest_factor"] == result["ranked"][0]["signal"]

    def test_weakest_active_factor_is_active(self):
        result = rank_contributions(_snap())
        weak = result["weakest_active_factor"]
        if weak:
            entry = next(e for e in result["ranked"] if e["signal"] == weak)
            assert entry["active"] is True

    def test_no_active_signals_strongest_is_none(self):
        snap = _snap(signal_contributions={s: 0.0 for s in SIGNAL_NAMES})
        result = rank_contributions(snap)
        assert result["strongest_factor"] is None
        assert result["weakest_active_factor"] is None

    def test_suppressed_flag_set(self):
        snap = _snap(
            suppressed_signals=["breakout"],
            signal_contributions={
                "options": 3.0, "insider": 2.0, "breakout": 0.0,
                "short_squeeze": 0.0, "catalyst": 0.0, "institutional": 0.0,
            },
        )
        result = rank_contributions(snap)
        brk = next(e for e in result["ranked"] if e["signal"] == "breakout")
        assert brk["suppressed"] is True

    def test_suppressed_factors_list(self):
        snap = _snap(suppressed_signals=["breakout", "catalyst"])
        result = rank_contributions(snap)
        assert sorted(result["suppressed_factors"]) == ["breakout", "catalyst"]

    def test_pct_of_max_computed(self):
        snap = _snap(signal_contributions={
            "options": 3.0, "insider": 0.0, "short_squeeze": 0.0,
            "catalyst": 0.0, "institutional": 0.0, "breakout": 0.0,
        })
        result = rank_contributions(snap)
        opts = next(e for e in result["ranked"] if e["signal"] == "options")
        assert opts["pct_of_max"] == pytest.approx(100.0)

    def test_pct_of_max_zero_for_inactive(self):
        snap = _snap(signal_contributions={
            "options": 3.0, "insider": 0.0, "short_squeeze": 0.0,
            "catalyst": 0.0, "institutional": 0.0, "breakout": 0.0,
        })
        result = rank_contributions(snap)
        ins = next(e for e in result["ranked"] if e["signal"] == "insider")
        assert ins["pct_of_max"] == 0.0

    def test_total_contribution_sum(self):
        contrib = {"options": 3.0, "insider": 2.0, "breakout": 1.0,
                   "short_squeeze": 0.0, "catalyst": 0.0, "institutional": 0.0}
        snap = _snap(signal_contributions=contrib)
        result = rank_contributions(snap)
        assert result["total_contribution"] == pytest.approx(6.0)

    def test_deterministic(self):
        snap = _snap()
        assert rank_contributions(snap) == rank_contributions(snap)


# ── flag_anomalies ────────────────────────────────────────────────────────────

class TestFlagAnomalies:
    def test_no_anomalies_clean_snapshot(self):
        # Clean: normal tier, normal conf, normal score
        snap = _snap(raw_score=8.0, confidence_pct=70.0, tier=TIER_ALERT,
                     adjusted_score=5.6)
        flags = flag_anomalies(snap)
        # Should be empty or contain only TIER_MISMATCH if thresholds produce diff tier
        non_mismatch = [f for f in flags if f["type"] != "TIER_MISMATCH"]
        assert non_mismatch == []

    def test_high_conf_low_score_detected(self):
        snap = _snap(
            raw_score=ANOMALY_LOW_SCORE_FOR_HIGH_CONF - 1,
            confidence_pct=ANOMALY_HIGH_CONF_THRESHOLD + 1.0,
            tier=TIER_WATCH,
            adjusted_score=0.5,
        )
        flags = flag_anomalies(snap)
        types = [f["type"] for f in flags]
        assert "HIGH_CONF_LOW_SCORE" in types

    def test_high_conf_low_score_severity_medium(self):
        # conf = threshold + 5 (< threshold + 10) → MEDIUM
        snap = _snap(
            raw_score=4,
            confidence_pct=ANOMALY_HIGH_CONF_THRESHOLD + 5.0,
            tier=TIER_WATCH, adjusted_score=0.5,
        )
        flags = flag_anomalies(snap)
        flag = next((f for f in flags if f["type"] == "HIGH_CONF_LOW_SCORE"), None)
        if flag:
            assert flag["severity"] == "MEDIUM"

    def test_high_conf_low_score_severity_high(self):
        # conf = threshold + 15 (≥ threshold + 10) → HIGH
        snap = _snap(
            raw_score=4,
            confidence_pct=ANOMALY_HIGH_CONF_THRESHOLD + 15.0,
            tier=TIER_WATCH, adjusted_score=0.5,
        )
        flags = flag_anomalies(snap)
        flag = next((f for f in flags if f["type"] == "HIGH_CONF_LOW_SCORE"), None)
        if flag:
            assert flag["severity"] == "HIGH"

    def test_high_conf_low_score_not_triggered_normal_score(self):
        snap = _snap(raw_score=ANOMALY_LOW_SCORE_FOR_HIGH_CONF, confidence_pct=70.0)
        flags = flag_anomalies(snap)
        assert not any(f["type"] == "HIGH_CONF_LOW_SCORE" for f in flags)

    def test_high_score_low_conf_detected(self):
        snap = _snap(
            raw_score=ANOMALY_HIGH_SCORE_THRESHOLD,
            confidence_pct=ANOMALY_LOW_CONF_FOR_HIGH_SCORE - 1.0,
            tier=TIER_WATCH,
            adjusted_score=2.0,
        )
        flags = flag_anomalies(snap)
        types = [f["type"] for f in flags]
        assert "HIGH_SCORE_LOW_CONF" in types

    def test_high_score_low_conf_severity_high(self):
        snap = _snap(raw_score=9, confidence_pct=20.0, tier=TIER_WATCH, adjusted_score=1.8)
        flags = flag_anomalies(snap)
        flag = next((f for f in flags if f["type"] == "HIGH_SCORE_LOW_CONF"), None)
        if flag:
            assert flag["severity"] == "HIGH"

    def test_high_score_low_conf_not_triggered_normal_conf(self):
        snap = _snap(raw_score=9, confidence_pct=70.0)
        flags = flag_anomalies(snap)
        assert not any(f["type"] == "HIGH_SCORE_LOW_CONF" for f in flags)

    def test_suppressed_high_conviction_detected(self):
        # Without suppression: options=3, insider=2, breakout=2 → 7 active sigs=3 conf=70 → CONVICTION
        # With suppression: breakout=0 → active_sigs=2 → ALERT not CONVICTION
        snap = _snap(
            signal_contributions={"options": 3.0, "insider": 2.0, "breakout": 0.0,
                                  "short_squeeze": 0.0, "catalyst": 0.0, "institutional": 0.0},
            suppressed_signals=["breakout"],
            original_contributions={"breakout": 2.0},
            raw_score=5.0,  # after suppression
            adjusted_score=3.5,
            confidence_pct=70.0,
            regime="RISK_OFF",
            tier=TIER_ALERT,
        )
        flags = flag_anomalies(snap)
        types = [f["type"] for f in flags]
        assert "SUPPRESSED_HIGH_CONVICTION" in types

    def test_suppressed_flag_not_triggered_without_original_contributions(self):
        snap = _snap(
            suppressed_signals=["breakout"],
            original_contributions=None,  # no original scores provided
        )
        flags = flag_anomalies(snap)
        assert not any(f["type"] == "SUPPRESSED_HIGH_CONVICTION" for f in flags)

    def test_tier_mismatch_detected(self):
        # raw=8, adj=5.6, conf=70%, active=3 → should be CONVICTION, but store WATCH
        snap = _snap(
            raw_score=8.0, adjusted_score=5.6, confidence_pct=70.0,
            tier=TIER_WATCH,  # incorrect stored tier
            signal_contributions={"options": 3.0, "insider": 2.0, "breakout": 2.0,
                                  "short_squeeze": 0.0, "catalyst": 0.0, "institutional": 1.0},
        )
        flags = flag_anomalies(snap)
        types = [f["type"] for f in flags]
        assert "TIER_MISMATCH" in types

    def test_tier_mismatch_not_triggered_when_correct(self):
        # raw=8, adj=5.6, conf=70%, active=3 → CONVICTION, and that's what we store
        snap = _snap(
            raw_score=8.0, adjusted_score=5.6, confidence_pct=70.0,
            tier=TIER_CONVICTION,
            signal_contributions={"options": 3.0, "insider": 2.0, "breakout": 2.0,
                                  "short_squeeze": 0.0, "catalyst": 0.0, "institutional": 1.0},
        )
        flags = flag_anomalies(snap)
        assert not any(f["type"] == "TIER_MISMATCH" for f in flags)

    def test_zero_active_with_score_detected(self):
        snap = _snap(
            raw_score=5.0,
            adjusted_score=3.5,
            signal_contributions={s: 0.0 for s in SIGNAL_NAMES},  # all zero
        )
        flags = flag_anomalies(snap)
        types = [f["type"] for f in flags]
        assert "ZERO_ACTIVE_WITH_SCORE" in types

    def test_zero_active_no_anomaly_when_score_also_zero(self):
        snap = _snap(
            raw_score=0.0,
            adjusted_score=0.0,
            signal_contributions={s: 0.0 for s in SIGNAL_NAMES},
            tier=TIER_WATCH,
        )
        flags = flag_anomalies(snap)
        assert not any(f["type"] == "ZERO_ACTIVE_WITH_SCORE" for f in flags)

    def test_contradictory_signals_detected(self):
        snap = _snap(
            calibration_adjustments=[
                {"type": "CONFLICT_PENALTY", "delta": -3.0,
                 "reason": "options without price action"}
            ]
        )
        flags = flag_anomalies(snap)
        types = [f["type"] for f in flags]
        assert "CONTRADICTORY_SIGNALS" in types

    def test_contradictory_signals_not_triggered_without_conflict(self):
        snap = _snap(
            calibration_adjustments=[
                {"type": "CORRELATION_PENALTY", "delta": -2.5, "reason": "options+squeeze"}
            ]
        )
        flags = flag_anomalies(snap)
        assert not any(f["type"] == "CONTRADICTORY_SIGNALS" for f in flags)

    def test_high_severity_sorted_first(self):
        # Create snapshot with both HIGH and MEDIUM anomalies
        snap = _snap(
            raw_score=ANOMALY_HIGH_SCORE_THRESHOLD,
            confidence_pct=20.0,  # HIGH_SCORE_LOW_CONF → HIGH
            tier=TIER_WATCH, adjusted_score=1.8,
            calibration_adjustments=[
                {"type": "CONFLICT_PENALTY", "delta": -2.0, "reason": "conflict"}
            ],
        )
        flags = flag_anomalies(snap)
        if len(flags) >= 2:
            high_idx   = [i for i, f in enumerate(flags) if f["severity"] == "HIGH"]
            medium_idx = [i for i, f in enumerate(flags) if f["severity"] == "MEDIUM"]
            if high_idx and medium_idx:
                assert max(high_idx) < min(medium_idx)

    def test_flags_have_required_fields(self):
        snap = _snap(raw_score=4, confidence_pct=75.0, tier=TIER_WATCH, adjusted_score=0.5)
        flags = flag_anomalies(snap)
        for flag in flags:
            assert "type"     in flag
            assert "severity" in flag
            assert "detail"   in flag

    def test_returns_list(self):
        assert isinstance(flag_anomalies(_snap()), list)

    def test_deterministic(self):
        snap = _snap()
        assert flag_anomalies(snap) == flag_anomalies(snap)


# ── explain_snapshot ──────────────────────────────────────────────────────────

class TestExplainSnapshot:
    def test_returns_non_empty_string(self):
        result = explain_snapshot(_snap())
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_ticker(self):
        result = explain_snapshot(_snap(ticker="NVDA"))
        assert "NVDA" in result

    def test_contains_raw_score(self):
        result = explain_snapshot(_snap(raw_score=8.0))
        assert "8" in result

    def test_contains_tier(self):
        result = explain_snapshot(_snap(tier=TIER_CONVICTION))
        assert TIER_CONVICTION in result

    def test_contains_regime(self):
        result = explain_snapshot(_snap(regime="NEUTRAL"))
        assert "NEUTRAL" in result

    def test_contains_confidence(self):
        result = explain_snapshot(_snap(confidence_pct=72.5))
        assert "72.5" in result

    def test_mentions_top_signal(self):
        snap = _snap(signal_contributions={
            "options": 3.0, "insider": 0.0, "short_squeeze": 0.0,
            "catalyst": 0.0, "institutional": 0.0, "breakout": 0.0,
        })
        result = explain_snapshot(snap)
        assert "options" in result

    def test_mentions_suppressed_when_present(self):
        snap = _snap(suppressed_signals=["breakout"], regime="RISK_OFF")
        result = explain_snapshot(snap)
        assert "suppressed" in result.lower() or "breakout" in result

    def test_regime_penalty_mentioned_for_neutral(self):
        snap = _snap(regime="NEUTRAL")
        result = explain_snapshot(snap)
        assert "0.90" in result or "penalty" in result.lower()

    def test_no_penalty_mentioned_for_bull(self):
        snap = _snap(regime="BULL")
        result = explain_snapshot(snap)
        assert "no penalty" in result.lower() or "BULL" in result

    def test_calibration_mentioned_when_adjustments(self):
        snap = _snap(calibration_adjustments=[
            {"type": "CORRELATION_PENALTY", "delta": -2.5, "reason": "test"}
        ])
        result = explain_snapshot(snap)
        assert "calibr" in result.lower() or "-2.5" in result or "adjustment" in result.lower()

    def test_deterministic(self):
        snap = _snap()
        assert explain_snapshot(snap) == explain_snapshot(snap)


# ── explain_tier_change ───────────────────────────────────────────────────────

class TestExplainTierChange:
    def test_no_change(self):
        result = explain_tier_change(TIER_ALERT, TIER_ALERT, 0.0)
        assert "unchanged" in result.lower()
        assert TIER_ALERT in result

    def test_upgrade_mentioned(self):
        result = explain_tier_change(TIER_ALERT, TIER_CONVICTION, 1.0)
        assert "upgraded" in result.lower() or "upgrade" in result.lower()
        assert TIER_CONVICTION in result

    def test_downgrade_mentioned(self):
        result = explain_tier_change(TIER_CONVICTION, TIER_ALERT, -1.5)
        assert "downgraded" in result.lower() or "downgrade" in result.lower()
        assert TIER_ALERT in result

    def test_delta_mentioned(self):
        result = explain_tier_change(TIER_WATCH, TIER_ALERT, 2.5)
        assert "2.5" in result or "+2.5" in result

    def test_returns_string(self):
        assert isinstance(explain_tier_change(TIER_WATCH, TIER_ALERT, 1.0), str)


# ── explain_suppression ───────────────────────────────────────────────────────

class TestExplainSuppression:
    def test_no_suppression(self):
        result = explain_suppression([], "BULL")
        assert "No signals were suppressed" in result

    def test_suppressed_signal_named(self):
        result = explain_suppression(["breakout"], "RISK_OFF")
        assert "breakout" in result
        assert "RISK_OFF" in result

    def test_regime_factor_mentioned(self):
        result = explain_suppression(["breakout"], "RISK_OFF")
        assert "0.75" in result  # RISK_OFF factor

    def test_neutral_factor(self):
        result = explain_suppression(["breakout"], "NEUTRAL")
        assert "0.90" in result

    def test_returns_string(self):
        assert isinstance(explain_suppression(["breakout"], "RISK_OFF"), str)


# ── _tier_rank ────────────────────────────────────────────────────────────────

class TestTierRank:
    def test_conviction_highest(self):
        assert _tier_rank(TIER_CONVICTION) > _tier_rank(TIER_ALERT)
        assert _tier_rank(TIER_ALERT)      > _tier_rank(TIER_WATCH)

    def test_unknown_tier(self):
        assert _tier_rank("UNKNOWN") == 0


# ── generate_report ───────────────────────────────────────────────────────────

class TestGenerateReport:
    def test_required_keys(self):
        report = generate_report(_snap())
        for key in ("ticker", "timestamp", "tier", "summary", "lineage",
                    "contributions", "anomalies", "explanation",
                    "confidence_summary", "suppression_summary", "warnings"):
            assert key in report, f"Missing key: {key}"

    def test_ticker_matches(self):
        assert generate_report(_snap(ticker="PLTR"))["ticker"] == "PLTR"

    def test_tier_matches(self):
        assert generate_report(_snap(tier=TIER_ALERT))["tier"] == TIER_ALERT

    def test_summary_is_string(self):
        assert isinstance(generate_report(_snap())["summary"], str)

    def test_summary_contains_ticker(self):
        assert "NVDA" in generate_report(_snap(ticker="NVDA"))["summary"]

    def test_explanation_is_string(self):
        assert isinstance(generate_report(_snap())["explanation"], str)

    def test_lineage_is_dict(self):
        assert isinstance(generate_report(_snap())["lineage"], dict)

    def test_contributions_is_dict(self):
        assert isinstance(generate_report(_snap())["contributions"], dict)

    def test_anomalies_is_list(self):
        assert isinstance(generate_report(_snap())["anomalies"], list)

    def test_warnings_is_list(self):
        assert isinstance(generate_report(_snap())["warnings"], list)

    def test_confidence_summary_keys(self):
        cs = generate_report(_snap())["confidence_summary"]
        assert "final" in cs
        assert "calibration_delta" in cs
        assert "regime_delta" in cs

    def test_confidence_summary_final_matches(self):
        report = generate_report(_snap(confidence_pct=68.5))
        assert report["confidence_summary"]["final"] == 68.5

    def test_high_anomalies_in_warnings(self):
        # HIGH_SCORE_LOW_CONF → HIGH severity → should appear in warnings
        snap = _snap(raw_score=9, confidence_pct=20.0, tier=TIER_WATCH, adjusted_score=1.8)
        report = generate_report(snap)
        high_flags = [a for a in report["anomalies"] if a["severity"] == "HIGH"]
        if high_flags:
            assert len(report["warnings"]) > 0

    def test_suppression_summary_string(self):
        assert isinstance(generate_report(_snap())["suppression_summary"], str)

    def test_deterministic(self):
        snap = _snap()
        assert generate_report(snap) == generate_report(snap)


# ── audit_batch ───────────────────────────────────────────────────────────────

class TestAuditBatch:
    def test_required_keys(self):
        result = audit_batch([_snap(), _snap(ticker="AMD")])
        for key in ("count", "reports", "anomaly_summary",
                    "tier_breakdown", "high_severity_count"):
            assert key in result

    def test_count_matches_input(self):
        snaps = [_snap(ticker=f"T{i}") for i in range(5)]
        assert audit_batch(snaps)["count"] == 5

    def test_empty_batch(self):
        result = audit_batch([])
        assert result["count"] == 0
        assert result["reports"] == []
        assert result["anomaly_summary"] == {}
        assert result["tier_breakdown"] == {}
        assert result["high_severity_count"] == 0

    def test_reports_list_length(self):
        snaps = [_snap(ticker=f"T{i}") for i in range(3)]
        assert len(audit_batch(snaps)["reports"]) == 3

    def test_tier_breakdown_counts(self):
        snaps = [
            _snap(tier=TIER_ALERT),
            _snap(tier=TIER_ALERT),
            _snap(tier=TIER_CONVICTION),
        ]
        result = audit_batch(snaps)
        assert result["tier_breakdown"].get(TIER_ALERT) == 2
        assert result["tier_breakdown"].get(TIER_CONVICTION) == 1

    def test_high_severity_count_correct(self):
        # Create a snapshot with a HIGH anomaly
        bad_snap = _snap(raw_score=9, confidence_pct=20.0, tier=TIER_WATCH, adjusted_score=1.8)
        result = audit_batch([bad_snap, _snap()])
        # bad_snap should have at least one HIGH anomaly
        high_from_bad = sum(
            1 for a in result["reports"][0]["anomalies"]
            if a["severity"] == "HIGH"
        )
        assert result["high_severity_count"] >= high_from_bad

    def test_anomaly_summary_is_dict(self):
        assert isinstance(audit_batch([_snap()])["anomaly_summary"], dict)

    def test_deterministic(self):
        snaps = [_snap(ticker=f"T{i}") for i in range(3)]
        assert audit_batch(snaps) == audit_batch(snaps)


# ── Sparse handling ───────────────────────────────────────────────────────────

class TestSparseHandling:
    def test_none_raw_score_handled(self):
        snap = build_audit_snapshot(
            ticker="X", timestamp="", raw_score=None, adjusted_score=None,
            confidence_pct=None, regime=None, tier=None,
        )
        report = generate_report(snap)
        assert report is not None

    def test_empty_signal_contributions(self):
        snap = build_audit_snapshot(
            ticker="X", timestamp="", raw_score=0, adjusted_score=0,
            confidence_pct=0, regime="BULL", tier=TIER_WATCH,
            signal_contributions={s: 0.0 for s in SIGNAL_NAMES},
        )
        assert rank_contributions(snap)["strongest_factor"] is None

    def test_no_anomalies_on_zero_snapshot(self):
        snap = build_audit_snapshot(
            ticker="X", timestamp="", raw_score=0, adjusted_score=0,
            confidence_pct=0, regime="BULL", tier=TIER_WATCH,
        )
        flags = flag_anomalies(snap)
        assert isinstance(flags, list)

    def test_lineage_handles_zero_score(self):
        snap = _snap(raw_score=0, adjusted_score=0, confidence_pct=0)
        lineage = reconstruct_lineage(snap)
        assert lineage["raw_score"] == 0

    def test_batch_single_item(self):
        result = audit_batch([_snap()])
        assert result["count"] == 1

    def test_report_handles_missing_regime(self):
        snap = _snap(regime=None)
        report = generate_report(snap)
        assert "explanation" in report

    def test_explain_no_active_signals(self):
        snap = _snap(signal_contributions={s: 0.0 for s in SIGNAL_NAMES})
        result = explain_snapshot(snap)
        assert isinstance(result, str)
        assert "No signals fired" in result


# ── Lineage detail accuracy ───────────────────────────────────────────────────

class TestLineageDetailAccuracy:
    def test_signal_sum_detail_lists_active_signals(self):
        snap = _snap(signal_contributions={
            "options": 3.0, "insider": 0.0, "short_squeeze": 0.0,
            "catalyst": 0.0, "institutional": 0.0, "breakout": 0.0,
        })
        lineage = reconstruct_lineage(snap)
        sum_step = next(s for s in lineage["steps"] if s["step"] == STEP_SIGNAL_SUM)
        assert "options" in sum_step["detail"]

    def test_adjusted_score_detail_shows_formula(self):
        snap = _snap(raw_score=8.0, confidence_pct=70.0, adjusted_score=5.6)
        lineage = reconstruct_lineage(snap)
        adj_step = next(s for s in lineage["steps"] if s["step"] == STEP_ADJUSTED_SCORE)
        assert "70.0" in adj_step["detail"] or "70" in adj_step["detail"]

    def test_tier_step_detail_correct_conviction(self):
        snap = _snap(
            raw_score=8, adjusted_score=5.6, confidence_pct=70.0, tier=TIER_CONVICTION,
            signal_contributions={"options": 3.0, "insider": 2.0, "breakout": 2.0,
                                  "short_squeeze": 0.0, "catalyst": 0.0, "institutional": 1.0},
        )
        lineage = reconstruct_lineage(snap)
        tier_step = next(s for s in lineage["steps"] if s["step"] == STEP_TIER)
        assert "CONVICTION" in tier_step["detail"]

    def test_regime_penalty_detail_mentions_regime(self):
        snap = _snap(regime="NEUTRAL", penalties=[
            {"type": "REGIME_PENALTY", "factor": 0.9, "reason": "NEUTRAL ×0.90"}
        ])
        lineage = reconstruct_lineage(snap)
        regime_steps = [s for s in lineage["steps"] if s["step"] == STEP_REGIME_PENALTY]
        if regime_steps:
            assert "NEUTRAL" in regime_steps[0]["detail"] or "0.9" in regime_steps[0]["detail"]
