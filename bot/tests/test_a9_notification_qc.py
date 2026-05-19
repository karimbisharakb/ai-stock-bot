"""
Phase A9 — Alpha notification quality-control layer tests.

Covers:
  - duplicate suppression and cooldown enforcement
  - stability penalties for rapid flips, score oscillation, PRE_ALERT loops
  - novelty boosts for first-ever, new setup types, rare validation
  - information gain requirements — no meaningful change is penalized
  - priority classification — all conditions met → PRIORITY
  - deterministic outputs — same inputs → same outputs
  - no send functions called anywhere
  - sparse / empty history is safe (never raises)
  - API GET endpoints return valid envelopes
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "test_a9.db"
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_qc_history (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker                 TEXT    NOT NULL,
            readiness_tier         TEXT    NOT NULL,
            alpha_tier             TEXT    NOT NULL,
            setup_type             TEXT    NOT NULL,
            alpha_score            REAL,
            readiness_score        REAL,
            qc_score               REAL    NOT NULL,
            qc_tier                TEXT    NOT NULL,
            allow_notification     INTEGER NOT NULL DEFAULT 0,
            suppression_reason     TEXT,
            cooldown_remaining     REAL    NOT NULL DEFAULT 0.0,
            novelty_score          REAL    NOT NULL DEFAULT 0.0,
            stability_score        REAL    NOT NULL DEFAULT 0.0,
            information_gain_score REAL    NOT NULL DEFAULT 0.0,
            quality_flags_json     TEXT    NOT NULL DEFAULT '[]',
            behavior_class         TEXT,
            dry_run_id             TEXT,
            evaluated_at           TEXT    NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    return str(p)


def _make_get_conn(db_path):
    def _get_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    return _get_conn


def _hours_from_now_iso(delta_hours: float) -> str:
    return (datetime.now() + timedelta(hours=delta_hours)).isoformat()


def _hours_ago_iso(hours: float) -> str:
    return (datetime.now() - timedelta(hours=hours)).isoformat()


# ── Canonical candidate and context helpers ───────────────────────────────────

def _candidate(
    ticker="AAPL",
    readiness_tier="PRE_ALERT",
    readiness_score=58.0,
    alpha_score=60.0,
    alpha_tier="STRONG_WATCH",
    setup_type="BREAKOUT_EXPANSION",
):
    return {
        "ticker": ticker,
        "readiness_tier": readiness_tier,
        "readiness_score": readiness_score,
        "alpha_score": alpha_score,
        "alpha_tier": alpha_tier,
        "setup_type": setup_type,
    }


def _context(trap_rates=None, validation_summary=None):
    return {
        "trap_rates": trap_rates or {},
        "validation_summary": validation_summary or {},
    }


def _prior(
    ticker="AAPL",
    readiness_tier="PRE_ALERT",
    setup_type="BREAKOUT_EXPANSION",
    readiness_score=55.0,
    allow_notification=1,
    evaluated_at=None,
    behavior_class=None,
):
    return {
        "ticker": ticker,
        "readiness_tier": readiness_tier,
        "setup_type": setup_type,
        "readiness_score": readiness_score,
        "allow_notification": allow_notification,
        "evaluated_at": evaluated_at or _hours_ago_iso(2.0),
        "qc_tier": "ALLOW",
        "behavior_class": behavior_class,
    }


# ── 1. Pure evaluate_notification_quality ────────────────────────────────────

class TestEvaluateNotificationQuality:

    def test_empty_prior_allows_notification(self):
        from alpha_notification_qc import evaluate_notification_quality
        result = evaluate_notification_quality(_candidate(), [], _context())
        assert result["allow_notification"] is True

    def test_empty_prior_returns_first_alert_flag(self):
        from alpha_notification_qc import evaluate_notification_quality
        result = evaluate_notification_quality(_candidate(), [], _context())
        assert "FIRST_ALERT" in result["quality_flags"]

    def test_empty_prior_high_novelty(self):
        from alpha_notification_qc import evaluate_notification_quality
        result = evaluate_notification_quality(_candidate(), [], _context())
        assert result["novelty_score"] >= 60.0

    def test_returns_required_keys(self):
        from alpha_notification_qc import evaluate_notification_quality
        result = evaluate_notification_quality(_candidate(), [], _context())
        for key in (
            "allow_notification", "qc_score", "qc_tier", "suppression_reason",
            "cooldown_remaining", "quality_flags", "novelty_score",
            "stability_score", "information_gain_score",
        ):
            assert key in result, f"Missing key: {key}"

    def test_qc_tier_is_valid(self):
        from alpha_notification_qc import evaluate_notification_quality, QC_TIERS
        result = evaluate_notification_quality(_candidate(), [], _context())
        assert result["qc_tier"] in QC_TIERS

    def test_never_raises_on_empty_candidate(self):
        from alpha_notification_qc import evaluate_notification_quality
        result = evaluate_notification_quality({}, None, None)
        assert isinstance(result, dict)
        assert "qc_tier" in result

    def test_never_raises_on_missing_fields(self):
        from alpha_notification_qc import evaluate_notification_quality
        result = evaluate_notification_quality(
            {"ticker": "X"}, [{"evaluated_at": "bad-date"}], {}
        )
        assert isinstance(result, dict)

    def test_scores_are_floats_in_range(self):
        from alpha_notification_qc import evaluate_notification_quality
        result = evaluate_notification_quality(_candidate(), [], _context())
        for key in ("qc_score", "novelty_score", "stability_score", "information_gain_score"):
            val = result[key]
            assert isinstance(val, float), f"{key} is not float"
            assert 0.0 <= val <= 100.0, f"{key}={val} out of range"


# ── 2. Cooldown enforcement ───────────────────────────────────────────────────

class TestCooldownEnforcement:

    def test_recent_allowed_same_tier_setup_blocks(self):
        """Same tier + setup + allowed within PRE_ALERT 6h cooldown → BLOCK."""
        from alpha_notification_qc import evaluate_notification_quality
        prior_list = [_prior(evaluated_at=_hours_ago_iso(2.0))]  # 2 h ago
        result = evaluate_notification_quality(_candidate(), prior_list, _context())
        assert result["allow_notification"] is False
        assert result["qc_tier"] == "BLOCK"
        assert "IN_COOLDOWN" in (result["suppression_reason"] or "")
        assert result["cooldown_remaining"] > 0.0

    def test_pre_alert_cooldown_6h(self):
        """A prior 7h-old ALLOWED PRE_ALERT should NOT trigger cooldown."""
        from alpha_notification_qc import evaluate_notification_quality
        prior_list = [_prior(evaluated_at=_hours_ago_iso(7.0))]  # 7 h > 6 h cooldown
        result = evaluate_notification_quality(_candidate(), prior_list, _context())
        # Should not be in cooldown
        assert "IN_COOLDOWN" not in (result["suppression_reason"] or "")

    def test_alert_ready_cooldown_12h(self):
        """An 11h-old ALERT_READY should still be in cooldown."""
        from alpha_notification_qc import evaluate_notification_quality
        prior_list = [_prior(
            readiness_tier="ALERT_READY",
            evaluated_at=_hours_ago_iso(11.0),
        )]
        result = evaluate_notification_quality(
            _candidate(readiness_tier="ALERT_READY", alpha_tier="HIGH_CONVICTION"),
            prior_list, _context(),
        )
        assert result["allow_notification"] is False
        assert result["cooldown_remaining"] > 0.0

    def test_rare_alert_cooldown_24h(self):
        """A 20h-old RARE_ALERT should still be in cooldown."""
        from alpha_notification_qc import evaluate_notification_quality
        prior_list = [_prior(
            readiness_tier="RARE_ALERT",
            setup_type="CATALYST_RUNUP",
            evaluated_at=_hours_ago_iso(20.0),
        )]
        result = evaluate_notification_quality(
            _candidate(readiness_tier="RARE_ALERT", setup_type="CATALYST_RUNUP",
                       alpha_tier="RARE_SETUP"),
            prior_list, _context(),
        )
        assert result["allow_notification"] is False
        assert result["cooldown_remaining"] > 0.0

    def test_different_setup_type_bypasses_cooldown(self):
        """Same tier but different setup type → cooldown does NOT apply."""
        from alpha_notification_qc import evaluate_notification_quality
        prior_list = [_prior(setup_type="SQUEEZE_CANDIDATE", evaluated_at=_hours_ago_iso(2.0))]
        result = evaluate_notification_quality(
            _candidate(setup_type="BREAKOUT_EXPANSION"),  # different setup
            prior_list, _context(),
        )
        # Cooldown should not apply
        assert "IN_COOLDOWN" not in (result["suppression_reason"] or "")

    def test_different_tier_bypasses_cooldown(self):
        """Same setup but different readiness tier → cooldown does NOT apply."""
        from alpha_notification_qc import evaluate_notification_quality
        prior_list = [_prior(readiness_tier="MONITOR", evaluated_at=_hours_ago_iso(1.0))]
        result = evaluate_notification_quality(
            _candidate(readiness_tier="PRE_ALERT"),  # different tier
            prior_list, _context(),
        )
        assert "IN_COOLDOWN" not in (result["suppression_reason"] or "")

    def test_prior_suppressed_does_not_trigger_cooldown(self):
        """A prior SUPPRESSED (allow_notification=0) should not trigger cooldown."""
        from alpha_notification_qc import evaluate_notification_quality
        prior_list = [_prior(allow_notification=0, evaluated_at=_hours_ago_iso(1.0))]
        result = evaluate_notification_quality(_candidate(), prior_list, _context())
        assert "IN_COOLDOWN" not in (result["suppression_reason"] or "")

    def test_cooldown_remaining_is_positive(self):
        from alpha_notification_qc import evaluate_notification_quality
        prior_list = [_prior(evaluated_at=_hours_ago_iso(1.0))]  # 1 h ago, 5 h remaining
        result = evaluate_notification_quality(_candidate(), prior_list, _context())
        assert result["cooldown_remaining"] > 4.0  # should be ~5h remaining


# ── 3. Stability sub-score ────────────────────────────────────────────────────

class TestStabilityScore:

    def test_no_history_returns_neutral(self):
        """No prior notifications → neutral stability (60)."""
        from alpha_notification_qc import _compute_stability_score
        score, flags = _compute_stability_score("PRE_ALERT", 58.0, [])
        assert score == 60.0
        assert flags == []

    def test_consistent_tier_bonus(self):
        """Same tier 3 times in history → CONSISTENT_SIGNAL bonus."""
        from alpha_notification_qc import _compute_stability_score
        priors = [
            {"readiness_tier": "PRE_ALERT", "readiness_score": 57.0,
             "evaluated_at": _hours_ago_iso(i * 2 + 2)}
            for i in range(3)
        ]
        score, flags = _compute_stability_score("PRE_ALERT", 58.0, priors)
        assert "CONSISTENT_SIGNAL" in flags
        assert score > 60.0

    def test_rapid_flip_penalty(self):
        """3+ tier changes in 24 h → RAPID_FLIP flag and large score penalty."""
        from alpha_notification_qc import _compute_stability_score
        tiers = ["PRE_ALERT", "MONITOR", "PRE_ALERT", "MONITOR"]
        priors = [
            {"readiness_tier": t, "readiness_score": 50.0,
             "evaluated_at": _hours_ago_iso(i + 1)}
            for i, t in enumerate(tiers)
        ]
        score, flags = _compute_stability_score("PRE_ALERT", 55.0, priors)
        assert "RAPID_FLIP" in flags
        assert score < 40.0

    def test_single_flip_gets_tier_flip_flag(self):
        """One tier change in 24 h → TIER_FLIP flag."""
        from alpha_notification_qc import _compute_stability_score
        priors = [
            {"readiness_tier": "MONITOR", "readiness_score": 40.0,
             "evaluated_at": _hours_ago_iso(1.0)},
        ]
        score, flags = _compute_stability_score("PRE_ALERT", 55.0, priors)
        assert "TIER_FLIP" in flags

    def test_score_oscillation_penalty(self):
        """High std dev of readiness_score in 24 h → SCORE_OSCILLATION flag."""
        from alpha_notification_qc import _compute_stability_score
        priors = [
            {"readiness_tier": "PRE_ALERT", "readiness_score": 30.0,
             "evaluated_at": _hours_ago_iso(1.0)},
            {"readiness_tier": "PRE_ALERT", "readiness_score": 75.0,
             "evaluated_at": _hours_ago_iso(2.0)},
            {"readiness_tier": "PRE_ALERT", "readiness_score": 35.0,
             "evaluated_at": _hours_ago_iso(3.0)},
        ]
        score, flags = _compute_stability_score("PRE_ALERT", 72.0, priors)
        assert "SCORE_OSCILLATION" in flags

    def test_pre_alert_monitor_loop_penalty(self):
        """3+ entries alternating PRE_ALERT/MONITOR in all history → loop penalty."""
        from alpha_notification_qc import _compute_stability_score
        tiers = ["PRE_ALERT", "MONITOR", "PRE_ALERT", "MONITOR"]
        priors = [
            {"readiness_tier": t, "readiness_score": 45.0,
             "evaluated_at": _hours_ago_iso(i * 12 + 24)}  # outside 24h window
            for i, t in enumerate(tiers)
        ]
        score, flags = _compute_stability_score("PRE_ALERT", 55.0, priors)
        assert "PRE_ALERT_MONITOR_LOOP" in flags

    def test_stable_alert_ready_has_high_stability(self):
        """Consistent ALERT_READY 3 times → stability above 80."""
        from alpha_notification_qc import _compute_stability_score
        priors = [
            {"readiness_tier": "ALERT_READY", "readiness_score": 72.0,
             "evaluated_at": _hours_ago_iso(i * 2 + 2)}
            for i in range(3)
        ]
        score, flags = _compute_stability_score("ALERT_READY", 74.0, priors)
        assert score >= 80.0


# ── 4. Information gain sub-score ─────────────────────────────────────────────

class TestInformationGainScore:

    def test_first_ever_max_gain(self):
        """No prior → GAIN_FIRST_EVER (100) and FIRST_ALERT flag."""
        from alpha_notification_qc import _compute_information_gain_score
        score, flags = _compute_information_gain_score(
            "PRE_ALERT", 58.0, "BREAKOUT_EXPANSION", [], {}
        )
        assert score == 100.0
        assert "FIRST_ALERT" in flags

    def test_large_score_delta(self):
        """readiness_score Δ ≥ 15 → LARGE_SCORE_DELTA flag and bonus."""
        from alpha_notification_qc import _compute_information_gain_score
        priors = [_prior(readiness_score=40.0)]
        score, flags = _compute_information_gain_score(
            "PRE_ALERT", 60.0, "BREAKOUT_EXPANSION", priors, {}
        )
        assert "LARGE_SCORE_DELTA" in flags
        assert score > 40.0

    def test_medium_score_delta(self):
        """10 ≤ Δ < 15 → MEDIUM_SCORE_DELTA."""
        from alpha_notification_qc import _compute_information_gain_score
        priors = [_prior(readiness_score=48.0)]
        score, flags = _compute_information_gain_score(
            "PRE_ALERT", 60.0, "BREAKOUT_EXPANSION", priors, {}
        )
        assert "MEDIUM_SCORE_DELTA" in flags

    def test_no_meaningful_change_penalized(self):
        """Same tier + setup + score Δ < 5 → NO_MEANINGFUL_CHANGE flag."""
        from alpha_notification_qc import _compute_information_gain_score
        priors = [_prior(readiness_score=58.0, readiness_tier="PRE_ALERT",
                         setup_type="BREAKOUT_EXPANSION")]
        score, flags = _compute_information_gain_score(
            "PRE_ALERT", 59.0, "BREAKOUT_EXPANSION", priors, {}
        )
        assert "NO_MEANINGFUL_CHANGE" in flags
        assert score < 40.0

    def test_new_setup_type_bonus(self):
        """New setup type not seen in prior history → NEW_SETUP_TYPE bonus."""
        from alpha_notification_qc import _compute_information_gain_score
        priors = [_prior(setup_type="SQUEEZE_CANDIDATE", readiness_score=50.0)]
        score, flags = _compute_information_gain_score(
            "PRE_ALERT", 52.0, "BREAKOUT_EXPANSION", priors, {}
        )
        assert "NEW_SETUP_TYPE" in flags

    def test_new_validation_evidence_bonus(self):
        """New behavior_class not in prior records → NEW_VALIDATION_EVIDENCE."""
        from alpha_notification_qc import _compute_information_gain_score
        priors = [_prior(readiness_score=50.0, behavior_class="INCONCLUSIVE")]
        ctx = {"ticker": "AAPL", "validation_summary": {"AAPL": "SUSTAINED_TREND"}}
        score, flags = _compute_information_gain_score(
            "PRE_ALERT", 52.0, "BREAKOUT_EXPANSION", priors, ctx
        )
        assert "NEW_VALIDATION_EVIDENCE" in flags

    def test_same_validation_no_bonus(self):
        """Same behavior_class as prior → no NEW_VALIDATION_EVIDENCE."""
        from alpha_notification_qc import _compute_information_gain_score
        priors = [_prior(readiness_score=50.0, behavior_class="SUSTAINED_TREND")]
        ctx = {"ticker": "AAPL", "validation_summary": {"AAPL": "SUSTAINED_TREND"}}
        score, flags = _compute_information_gain_score(
            "PRE_ALERT", 52.0, "BREAKOUT_EXPANSION", priors, ctx
        )
        assert "NEW_VALIDATION_EVIDENCE" not in flags


# ── 5. Novelty sub-score ──────────────────────────────────────────────────────

class TestNoveltyScore:

    def test_first_ever_high_novelty(self):
        """No prior QC records → FIRST_QC_RECORD flag and high score."""
        from alpha_notification_qc import _compute_novelty_score
        score, flags = _compute_novelty_score(
            "AAPL", "PRE_ALERT", "BREAKOUT_EXPANSION", [], {}
        )
        assert "FIRST_QC_RECORD" in flags
        assert score >= 60.0

    def test_rare_alert_tier_bonus(self):
        from alpha_notification_qc import _compute_novelty_score
        score_rare, _ = _compute_novelty_score(
            "AAPL", "RARE_ALERT", "CATALYST_RUNUP", [], {}
        )
        score_pre, _ = _compute_novelty_score(
            "AAPL", "PRE_ALERT", "CATALYST_RUNUP", [], {}
        )
        assert score_rare > score_pre

    def test_institutional_accumulation_rare_validation_bonus(self):
        from alpha_notification_qc import _compute_novelty_score
        ctx = {"validation_summary": {"AAPL": "INSTITUTIONAL_ACCUMULATION"}}
        score, flags = _compute_novelty_score(
            "AAPL", "PRE_ALERT", "BREAKOUT_EXPANSION", [_prior()], ctx
        )
        assert "RARE_VALIDATION" in flags

    def test_valid_breakout_rare_validation_bonus(self):
        from alpha_notification_qc import _compute_novelty_score
        ctx = {"validation_summary": {"AAPL": "VALID_BREAKOUT"}}
        score, flags = _compute_novelty_score(
            "AAPL", "PRE_ALERT", "BREAKOUT_EXPANSION", [_prior()], ctx
        )
        assert "RARE_VALIDATION" in flags

    def test_sustained_trend_bonus(self):
        from alpha_notification_qc import _compute_novelty_score
        ctx = {"validation_summary": {"AAPL": "SUSTAINED_TREND"}}
        score, flags = _compute_novelty_score(
            "AAPL", "PRE_ALERT", "BREAKOUT_EXPANSION", [_prior()], ctx
        )
        assert "SUSTAINED_TREND" in flags

    def test_repeat_signal_penalty(self):
        """Same tier + setup as most recent prior → REPEAT_SIGNAL penalty."""
        from alpha_notification_qc import _compute_novelty_score
        prior_list = [_prior(readiness_tier="PRE_ALERT", setup_type="BREAKOUT_EXPANSION")]
        score_repeat, flags = _compute_novelty_score(
            "AAPL", "PRE_ALERT", "BREAKOUT_EXPANSION", prior_list, {}
        )
        assert "REPEAT_SIGNAL" in flags

    def test_novel_setup_bonus(self):
        """New setup type vs prior → NOVEL_SETUP flag."""
        from alpha_notification_qc import _compute_novelty_score
        prior_list = [_prior(setup_type="SQUEEZE_CANDIDATE")]
        score, flags = _compute_novelty_score(
            "AAPL", "PRE_ALERT", "BREAKOUT_EXPANSION", prior_list, {}
        )
        assert "NOVEL_SETUP" in flags


# ── 6. Priority classification ────────────────────────────────────────────────

class TestPriorityClassification:

    def _priority_candidate(self):
        return _candidate(
            readiness_tier="RARE_ALERT",
            readiness_score=90.0,
            alpha_score=88.0,
            alpha_tier="RARE_SETUP",
            setup_type="CATALYST_RUNUP",
        )

    def test_priority_first_ever_rare_alert(self):
        """First-ever RARE_ALERT with no prior history → PRIORITY."""
        from alpha_notification_qc import evaluate_notification_quality
        result = evaluate_notification_quality(
            self._priority_candidate(), [], _context()
        )
        assert result["qc_tier"] == "PRIORITY"
        assert result["allow_notification"] is True

    def test_priority_requires_alert_ready_or_rare(self):
        """PRE_ALERT can never be PRIORITY."""
        from alpha_notification_qc import evaluate_notification_quality
        c = _candidate(readiness_tier="PRE_ALERT", readiness_score=90.0)
        result = evaluate_notification_quality(c, [], _context())
        assert result["qc_tier"] != "PRIORITY"

    def test_priority_blocked_by_high_trap_rate(self):
        """High trap rate > 0.30 prevents PRIORITY even with all other conditions."""
        from alpha_notification_qc import evaluate_notification_quality
        ctx = _context(trap_rates={"CATALYST_RUNUP": 0.50})
        result = evaluate_notification_quality(
            self._priority_candidate(), [], ctx
        )
        assert result["qc_tier"] != "PRIORITY"

    def test_priority_when_all_conditions_met_no_prior(self):
        """First-ever RARE_ALERT, low trap rate → PRIORITY."""
        from alpha_notification_qc import evaluate_notification_quality
        ctx = _context(trap_rates={"CATALYST_RUNUP": 0.10})
        result = evaluate_notification_quality(
            self._priority_candidate(), [], ctx
        )
        assert result["qc_tier"] == "PRIORITY"
        assert result["allow_notification"] is True

    def test_priority_not_assigned_when_low_qc_score(self):
        """Low composite score → no PRIORITY even for RARE_ALERT."""
        from alpha_notification_qc import evaluate_notification_quality
        # Create scenario where score is low: rapid flips reduce stability heavily
        tiers = ["RARE_ALERT", "MONITOR", "RARE_ALERT", "MONITOR"]
        prior_list = [
            {
                "ticker": "AAPL", "readiness_tier": t, "setup_type": "CATALYST_RUNUP",
                "readiness_score": 50.0, "allow_notification": 0,
                "evaluated_at": _hours_ago_iso(i + 1), "qc_tier": "SUPPRESS",
                "behavior_class": None,
            }
            for i, t in enumerate(tiers)
        ]
        result = evaluate_notification_quality(
            _candidate(readiness_tier="RARE_ALERT", alpha_tier="RARE_SETUP"),
            prior_list, _context(),
        )
        assert result["qc_tier"] != "PRIORITY"


# ── 7. Determinism ────────────────────────────────────────────────────────────

class TestDeterminism:

    def test_same_inputs_same_output(self):
        """evaluate_notification_quality is deterministic."""
        from alpha_notification_qc import evaluate_notification_quality
        prior_list = [_prior()]
        ctx = _context(validation_summary={"AAPL": "SUSTAINED_TREND"})
        c = _candidate()
        r1 = evaluate_notification_quality(c, prior_list, ctx)
        r2 = evaluate_notification_quality(c, prior_list, ctx)
        assert r1["qc_score"] == r2["qc_score"]
        assert r1["qc_tier"] == r2["qc_tier"]
        assert r1["quality_flags"] == r2["quality_flags"]

    def test_sparse_history_safe(self):
        """Single partial prior record does not raise."""
        from alpha_notification_qc import evaluate_notification_quality
        partial_prior = {"ticker": "AAPL"}  # missing most fields
        result = evaluate_notification_quality(_candidate(), [partial_prior], {})
        assert isinstance(result, dict)
        assert result["qc_tier"] in ("BLOCK", "SUPPRESS", "ALLOW", "PRIORITY")

    def test_none_inputs_safe(self):
        from alpha_notification_qc import evaluate_notification_quality
        result = evaluate_notification_quality(None, None, None)
        assert isinstance(result, dict)

    def test_empty_context_safe(self):
        from alpha_notification_qc import evaluate_notification_quality
        result = evaluate_notification_quality(_candidate(), [], {})
        assert isinstance(result, dict)
        assert "qc_score" in result

    def test_qc_score_is_float(self):
        from alpha_notification_qc import evaluate_notification_quality
        result = evaluate_notification_quality(_candidate(), [], _context())
        assert isinstance(result["qc_score"], float)


# ── 8. DB integration — run_qc_for_dry_runs ──────────────────────────────────

class TestRunQcForDryRuns:

    def test_empty_db_returns_empty_when_no_candidates(self, db_path, monkeypatch):
        import alpha_notification_qc
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)

        # Patch get_alert_candidates to return nothing
        monkeypatch.setattr(
            alpha_notification_qc,
            "_build_qc_context",
            lambda: {"trap_rates": {}, "validation_summary": {}},
        )
        with patch.dict("sys.modules", {
            "alpha_alert_gate": MagicMock(get_alert_candidates=lambda limit=50: []),
        }):
            result = alpha_notification_qc.run_qc_for_dry_runs()
        assert result == []

    def test_records_stored_in_history(self, db_path, monkeypatch):
        import alpha_notification_qc
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)
        monkeypatch.setattr(
            alpha_notification_qc,
            "_build_qc_context",
            lambda: {"trap_rates": {}, "validation_summary": {}},
        )

        fake_candidates = [
            {
                "ticker": "NVDA", "readiness_tier": "ALERT_READY",
                "readiness_score": 72.0, "alpha_score": 75.0,
                "alpha_tier": "HIGH_CONVICTION", "setup_type": "BREAKOUT_EXPANSION",
            }
        ]
        mock_gate = MagicMock(get_alert_candidates=lambda limit=50: fake_candidates)
        mock_dryrun = MagicMock(get_dry_runs=lambda status_filter=None, limit=100: [])

        with patch.dict("sys.modules", {
            "alpha_alert_gate": mock_gate,
            "alpha_notification_dryrun": mock_dryrun,
        }):
            results = alpha_notification_qc.run_qc_for_dry_runs()

        assert len(results) == 1
        assert results[0]["ticker"] == "NVDA"

        # Verify DB record was written
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT * FROM notification_qc_history").fetchall()
        conn.close()
        assert len(rows) == 1

    def test_result_has_required_qc_keys(self, db_path, monkeypatch):
        import alpha_notification_qc
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)
        monkeypatch.setattr(
            alpha_notification_qc,
            "_build_qc_context",
            lambda: {"trap_rates": {}, "validation_summary": {}},
        )

        fake_candidates = [{
            "ticker": "TSLA", "readiness_tier": "PRE_ALERT",
            "readiness_score": 58.0, "alpha_score": 60.0,
            "alpha_tier": "STRONG_WATCH", "setup_type": "SQUEEZE_CANDIDATE",
        }]
        mock_gate = MagicMock(get_alert_candidates=lambda limit=50: fake_candidates)
        mock_dryrun = MagicMock(get_dry_runs=lambda status_filter=None, limit=100: [])

        with patch.dict("sys.modules", {
            "alpha_alert_gate": mock_gate,
            "alpha_notification_dryrun": mock_dryrun,
        }):
            results = alpha_notification_qc.run_qc_for_dry_runs()

        row = results[0]
        for key in ("allow_notification", "qc_score", "qc_tier", "ticker"):
            assert key in row


# ── 9. get_qc_records ────────────────────────────────────────────────────────

class TestGetQcRecords:

    def _insert_qc_row(self, conn, ticker, qc_tier, allow_notification=1,
                       readiness_tier="PRE_ALERT", setup_type="BREAKOUT_EXPANSION",
                       evaluated_at=None):
        conn.execute(
            """INSERT INTO notification_qc_history
               (ticker, readiness_tier, alpha_tier, setup_type, qc_score, qc_tier,
                allow_notification, quality_flags_json, evaluated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (ticker, readiness_tier, "STRONG_WATCH", setup_type,
             75.0, qc_tier, allow_notification, "[]",
             evaluated_at or _hours_ago_iso(1.0)),
        )
        conn.commit()

    def test_empty_db_returns_empty(self, db_path, monkeypatch):
        import alpha_notification_qc
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)
        result = alpha_notification_qc.get_qc_records()
        assert result == []

    def test_returns_all_without_filter(self, db_path, monkeypatch):
        import alpha_notification_qc
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)

        conn = sqlite3.connect(db_path)
        self._insert_qc_row(conn, "AAPL", "ALLOW")
        self._insert_qc_row(conn, "NVDA", "PRIORITY")
        conn.close()

        result = alpha_notification_qc.get_qc_records()
        assert len(result) == 2

    def test_ticker_filter(self, db_path, monkeypatch):
        import alpha_notification_qc
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)

        conn = sqlite3.connect(db_path)
        self._insert_qc_row(conn, "AAPL", "ALLOW")
        self._insert_qc_row(conn, "NVDA", "PRIORITY")
        conn.close()

        result = alpha_notification_qc.get_qc_records(ticker="AAPL")
        assert all(r["ticker"] == "AAPL" for r in result)

    def test_qc_tier_filter(self, db_path, monkeypatch):
        import alpha_notification_qc
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)

        conn = sqlite3.connect(db_path)
        self._insert_qc_row(conn, "AAPL", "ALLOW")
        self._insert_qc_row(conn, "NVDA", "PRIORITY")
        self._insert_qc_row(conn, "MSFT", "SUPPRESS")
        conn.close()

        result = alpha_notification_qc.get_qc_records(qc_tier="PRIORITY")
        assert all(r["qc_tier"] == "PRIORITY" for r in result)
        assert len(result) == 1

    def test_limit_respected(self, db_path, monkeypatch):
        import alpha_notification_qc
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)

        conn = sqlite3.connect(db_path)
        for i in range(5):
            self._insert_qc_row(conn, f"T{i}", "ALLOW")
        conn.close()

        result = alpha_notification_qc.get_qc_records(limit=2)
        assert len(result) == 2


# ── 10. get_qc_summary ────────────────────────────────────────────────────────

class TestGetQcSummary:

    def _insert_qc_row(self, conn, qc_tier, allow_notification,
                       suppression_reason=None, cooldown_remaining=0.0,
                       ticker="AAPL", qc_score=60.0):
        conn.execute(
            """INSERT INTO notification_qc_history
               (ticker, readiness_tier, alpha_tier, setup_type, qc_score, qc_tier,
                allow_notification, suppression_reason, cooldown_remaining,
                quality_flags_json, evaluated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (ticker, "PRE_ALERT", "STRONG_WATCH", "BREAKOUT_EXPANSION",
             qc_score, qc_tier, allow_notification, suppression_reason,
             cooldown_remaining, "[]", _hours_ago_iso(1.0)),
        )
        conn.commit()

    def test_empty_returns_safe_structure(self, db_path, monkeypatch):
        import alpha_notification_qc
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)
        summary = alpha_notification_qc.get_qc_summary()
        assert summary["total_evaluated"] == 0
        assert summary["avg_qc_score"] == 0.0
        assert "generated_at" in summary

    def test_counts_suppressed(self, db_path, monkeypatch):
        import alpha_notification_qc
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)

        conn = sqlite3.connect(db_path)
        self._insert_qc_row(conn, "ALLOW", 1, ticker="AAPL")
        self._insert_qc_row(conn, "BLOCK", 0, "IN_COOLDOWN:5.0h", 5.0, ticker="NVDA")
        self._insert_qc_row(conn, "SUPPRESS", 0, "LOW_QC_SCORE", ticker="MSFT")
        conn.close()

        summary = alpha_notification_qc.get_qc_summary()
        assert summary["total_evaluated"] == 3
        assert summary["suppressed_count"] == 2
        assert summary["allowed_count"] == 1

    def test_counts_duplicate_suppressions(self, db_path, monkeypatch):
        import alpha_notification_qc
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)

        conn = sqlite3.connect(db_path)
        self._insert_qc_row(conn, "BLOCK", 0, "IN_COOLDOWN:3.0h", 3.0, ticker="A1")
        self._insert_qc_row(conn, "BLOCK", 0, "IN_COOLDOWN:1.0h", 1.0, ticker="A2")
        self._insert_qc_row(conn, "BLOCK", 0, "UNSTABLE_READINESS", ticker="A3")
        conn.close()

        summary = alpha_notification_qc.get_qc_summary()
        assert summary["duplicate_suppressions"] == 2
        assert summary["unstable_suppressions"] == 1

    def test_counts_priority(self, db_path, monkeypatch):
        import alpha_notification_qc
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)

        conn = sqlite3.connect(db_path)
        self._insert_qc_row(conn, "PRIORITY", 1, ticker="P1", qc_score=88.0)
        self._insert_qc_row(conn, "PRIORITY", 1, ticker="P2", qc_score=90.0)
        self._insert_qc_row(conn, "ALLOW",    1, ticker="A1", qc_score=55.0)
        conn.close()

        summary = alpha_notification_qc.get_qc_summary()
        assert summary["priority_candidates"] == 2

    def test_avg_qc_score(self, db_path, monkeypatch):
        import alpha_notification_qc
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)

        conn = sqlite3.connect(db_path)
        self._insert_qc_row(conn, "ALLOW", 1, ticker="A", qc_score=60.0)
        self._insert_qc_row(conn, "ALLOW", 1, ticker="B", qc_score=80.0)
        conn.close()

        summary = alpha_notification_qc.get_qc_summary()
        assert summary["avg_qc_score"] == 70.0

    def test_cooldown_active_count(self, db_path, monkeypatch):
        import alpha_notification_qc
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)

        conn = sqlite3.connect(db_path)
        self._insert_qc_row(conn, "BLOCK", 0, "IN_COOLDOWN:5.0h", 5.0, ticker="X1")
        self._insert_qc_row(conn, "BLOCK", 0, "IN_COOLDOWN:2.0h", 2.0, ticker="X2")
        self._insert_qc_row(conn, "ALLOW", 1, cooldown_remaining=0.0, ticker="X3")
        conn.close()

        summary = alpha_notification_qc.get_qc_summary()
        assert summary["cooldown_active_count"] == 2

    def test_summary_note_confirms_no_alerts(self, db_path, monkeypatch):
        import alpha_notification_qc
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)

        conn = sqlite3.connect(db_path)
        self._insert_qc_row(conn, "ALLOW", 1, ticker="X")
        conn.close()

        summary = alpha_notification_qc.get_qc_summary()
        assert "no WhatsApp alerts sent" in summary.get("note", "").lower() or \
               "no real" in summary.get("note", "").lower() or \
               "simulation" in summary.get("note", "").lower()


# ── 11. API endpoints ─────────────────────────────────────────────────────────

@pytest.fixture
def app_client(db_path, monkeypatch):
    import alpha_notification_qc
    monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
    monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)

    from flask import Flask
    from api import api_bp, cache_clear
    flask_app = Flask("test_a9")
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(api_bp)
    cache_clear()
    with flask_app.test_client() as client:
        yield client, db_path


class TestApiQcList:

    def test_get_list_empty(self, app_client):
        client, _ = app_client
        resp = client.get("/api/v1/alpha/notifications/qc")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["count"] == 0
        assert body["data"]["records"] == []

    def test_get_list_with_rows(self, app_client):
        client, db_path = app_client
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO notification_qc_history
               (ticker, readiness_tier, alpha_tier, setup_type, qc_score, qc_tier,
                allow_notification, quality_flags_json, evaluated_at)
               VALUES ('AAPL','PRE_ALERT','STRONG_WATCH','BREAKOUT_EXPANSION',
                       72.0,'ALLOW',1,'[]',?)""",
            (_hours_ago_iso(1.0),),
        )
        conn.commit()
        conn.close()

        from api import _CACHE
        _CACHE.clear()
        resp = client.get("/api/v1/alpha/notifications/qc")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["count"] == 1

    def test_invalid_qc_tier_returns_400(self, app_client):
        client, _ = app_client
        resp = client.get("/api/v1/alpha/notifications/qc?qc_tier=INVALID")
        assert resp.status_code == 400

    def test_envelope_structure(self, app_client):
        client, _ = app_client
        resp = client.get("/api/v1/alpha/notifications/qc")
        body = resp.get_json()
        assert "ok" in body
        assert "data" in body
        assert "meta" in body

    def test_no_auth_required(self, app_client):
        """GET list should not require auth."""
        client, _ = app_client
        resp = client.get("/api/v1/alpha/notifications/qc")
        assert resp.status_code == 200

    def test_note_confirms_no_alerts(self, app_client):
        client, _ = app_client
        resp = client.get("/api/v1/alpha/notifications/qc")
        body = resp.get_json()
        note = body["data"].get("note", "")
        assert "no real" in note.lower() or "simulation" in note.lower()


class TestApiQcSummary:

    def test_summary_empty(self, app_client):
        client, _ = app_client
        resp = client.get("/api/v1/alpha/notifications/qc/summary")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["total_evaluated"] == 0

    def test_summary_structure(self, app_client):
        client, _ = app_client
        resp = client.get("/api/v1/alpha/notifications/qc/summary")
        body = resp.get_json()
        data = body["data"]
        for key in (
            "total_evaluated", "allowed_count", "suppressed_count",
            "duplicate_suppressions", "unstable_suppressions",
            "avg_qc_score", "priority_candidates", "cooldown_active_count",
            "qc_tier_distribution", "generated_at",
        ):
            assert key in data, f"Missing summary key: {key}"

    def test_summary_no_auth_required(self, app_client):
        client, _ = app_client
        resp = client.get("/api/v1/alpha/notifications/qc/summary")
        assert resp.status_code == 200


# ── 12. No alerts sent ────────────────────────────────────────────────────────

class TestNoAlertsSent:

    def test_module_has_no_twilio_import(self):
        import alpha_notification_qc
        src = open(alpha_notification_qc.__file__).read()
        assert "import twilio" not in src
        assert "from twilio" not in src
        assert "send_sms(" not in src
        assert "client.messages" not in src

    def test_evaluate_does_not_import_twilio(self):
        from alpha_notification_qc import evaluate_notification_quality
        with patch.dict("sys.modules", {"twilio": None}):
            result = evaluate_notification_quality(_candidate(), [], _context())
        assert isinstance(result, dict)

    def test_run_qc_does_not_call_send_sms(self, db_path, monkeypatch):
        import alpha_notification_qc
        monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
        monkeypatch.setattr(alpha_notification_qc, "_ensure_table", lambda: None)
        monkeypatch.setattr(
            alpha_notification_qc,
            "_build_qc_context",
            lambda: {"trap_rates": {}, "validation_summary": {}},
        )

        mock_send = MagicMock(side_effect=AssertionError("send_sms must not be called"))
        with patch.dict("sys.modules", {
            "alpha_alert_gate": MagicMock(get_alert_candidates=lambda limit=50: []),
            "alerts": MagicMock(send_sms=mock_send),
        }):
            alpha_notification_qc.run_qc_for_dry_runs()

        mock_send.assert_not_called()

    def test_evaluate_never_raises(self):
        from alpha_notification_qc import evaluate_notification_quality
        for bad_input in [
            ({}, [], {}),
            ({"ticker": None}, [{"evaluated_at": None}], {"trap_rates": None}),
            (None, None, None),
        ]:
            result = evaluate_notification_quality(*bad_input)
            assert isinstance(result, dict), f"Should return dict for input {bad_input}"
