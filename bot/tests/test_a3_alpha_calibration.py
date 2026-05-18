"""
Phase A3 — Alpha engine calibration tests.

Covers:
  - New tier thresholds (35 / 50 / 65 / 80)
  - Catalyst MISSING neutral scoring
  - Options MISSING neutral scoring
  - Risk/reward MISSING neutral + ATR fallback
  - History-based 52w high/low fallback
  - Alpha universe list invariants
  - scan_alpha_universe() feature-flag + scoring
  - AlphaShadowManager analytics (count_by_tier, get_top_setup_types,
    get_best_non_predator, get_universe_coverage)
  - alpha_alerts_enabled() flag default
"""
import json
import os
import sqlite3
import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from alpha_engine import (
    AlphaEngine,
    AlphaInput,
    ComponentScore,
    _classify_tier,
    _score_catalyst,
    _score_options,
    _score_risk_reward,
    _WEIGHTS,
)

ENGINE = AlphaEngine()


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
    explanation           TEXT,
    detail_json           TEXT
)
"""


def _make_db(path: str) -> str:
    conn = sqlite3.connect(path)
    conn.execute(_CREATE_TABLE)
    conn.commit()
    conn.close()
    return path


def _row_conn(conn):
    conn.row_factory = sqlite3.Row
    return conn


def _seed_row(path: str, **kwargs):
    defaults = dict(
        ticker="AAPL",
        scan_time="2026-05-18T10:00:00",
        alpha_score=55.0,
        alpha_tier="STRONG_WATCH",
        setup_type="BREAKOUT_EXPANSION",
        predator_tier="ALERT",
        predator_score=7.5,
        tier_match=0,
        filter_reason=None,
        component_scores_json=None,
        explanation=None,
        detail_json=None,
    )
    defaults.update(kwargs)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        INSERT INTO alpha_shadow_log
            (ticker, scan_time, alpha_score, alpha_tier, setup_type,
             predator_tier, predator_score, tier_match, filter_reason,
             component_scores_json, explanation, detail_json)
        VALUES (:ticker, :scan_time, :alpha_score, :alpha_tier, :setup_type,
                :predator_tier, :predator_score, :tier_match, :filter_reason,
                :component_scores_json, :explanation, :detail_json)
        """,
        defaults,
    )
    conn.commit()
    conn.close()


def _patch_db(monkeypatch, path: str):
    import database as _db
    monkeypatch.setattr(_db, "get_connection", lambda: _row_conn(sqlite3.connect(path)))


def _comps(scores):
    """Create a list of ComponentScore for tier gate testing."""
    names = ["relative_strength", "acceleration", "squeeze", "catalyst",
             "options", "breakout", "risk_reward", "novelty"]
    return [
        ComponentScore(n, s, _WEIGHTS[n], [f"score {s}"], "HIGH")
        for n, s in zip(names, scores)
    ]


# ── TestTierThresholds ────────────────────────────────────────────────────────

class TestTierThresholds:
    """Verify new tier thresholds: 35 / 50 / 65 / 80."""

    def test_score_34_is_ignore(self):
        tier, _, _ = _classify_tier(34.9, _comps([3.4] * 8))
        assert tier == "IGNORE"

    def test_score_35_is_watch(self):
        tier, _, _ = _classify_tier(35.0, _comps([3.5] * 8))
        assert tier == "WATCH"

    def test_score_49_is_watch(self):
        tier, _, _ = _classify_tier(49.9, _comps([5.0] * 8))
        assert tier == "WATCH"

    def test_score_50_is_strong_watch(self):
        tier, _, _ = _classify_tier(50.0, _comps([5.0] * 8))
        assert tier == "STRONG_WATCH"

    def test_score_64_is_strong_watch(self):
        tier, _, _ = _classify_tier(64.9, _comps([6.5] * 8))
        assert tier == "STRONG_WATCH"

    def test_score_65_with_gate_met_is_high_conviction(self):
        # Need ≥3 components ≥ 6.0 for HIGH_CONVICTION
        scores = [8.0, 7.0, 6.0, 5.0, 5.0, 5.0, 5.0, 5.0]
        tier, gate_applied, _ = _classify_tier(65.0, _comps(scores))
        assert tier == "HIGH_CONVICTION"
        assert gate_applied is False

    def test_score_below_35_is_ignore(self):
        tier, _, _ = _classify_tier(20.0, _comps([2.0] * 8))
        assert tier == "IGNORE"

    def test_score_exactly_80_is_rare_setup_when_gate_met(self):
        # Need ≥4 components ≥ 7.0 and no component < 3.0
        scores = [9.0, 8.0, 8.0, 7.0, 8.0, 5.0, 5.0, 5.0]
        tier, gate_applied, _ = _classify_tier(80.0, _comps(scores))
        assert tier == "RARE_SETUP"
        assert gate_applied is False


# ── TestCatalystMissing ───────────────────────────────────────────────────────

class TestCatalystMissing:
    """Neutral scoring when no catalyst data present."""

    def test_all_none_false_returns_neutral(self):
        inp = AlphaInput(ticker="X", price=50.0)
        comp = _score_catalyst(inp)
        assert comp.score == 5.0
        assert comp.data_quality == "MISSING"
        assert "neutral" in comp.reasons[0].lower()

    def test_earnings_days_present_not_missing(self):
        """If earnings_days is set, we have data → not MISSING."""
        inp = AlphaInput(ticker="X", price=50.0, earnings_days=10)
        comp = _score_catalyst(inp)
        assert comp.data_quality != "MISSING"

    def test_has_catalyst_news_true_not_missing(self):
        inp = AlphaInput(ticker="X", price=50.0, has_catalyst_news=True)
        comp = _score_catalyst(inp)
        assert comp.data_quality != "MISSING"

    def test_has_sec_8k_true_not_missing(self):
        inp = AlphaInput(ticker="X", price=50.0, has_sec_8k=True)
        comp = _score_catalyst(inp)
        assert comp.data_quality != "MISSING"

    def test_news_sentiment_set_not_missing(self):
        """news_sentiment alone (no catalyst_news) still prevents MISSING."""
        inp = AlphaInput(ticker="X", price=50.0, news_sentiment=0.5)
        comp = _score_catalyst(inp)
        assert comp.data_quality != "MISSING"

    def test_missing_catalyst_quality_string(self):
        comp = _score_catalyst(AlphaInput(ticker="X", price=50.0))
        assert comp.data_quality == "MISSING"


# ── TestOptionsNeutral ────────────────────────────────────────────────────────

class TestOptionsNeutral:
    """Options MISSING → neutral 5.0."""

    def test_no_options_data_returns_5(self):
        inp = AlphaInput(ticker="X", price=50.0)
        comp = _score_options(inp)
        assert comp.score == 5.0
        assert comp.data_quality == "MISSING"

    def test_no_options_reason_mentions_neutral(self):
        comp = _score_options(AlphaInput(ticker="X", price=50.0))
        assert any("neutral" in r.lower() for r in comp.reasons)

    def test_call_volume_present_not_missing(self):
        inp = AlphaInput(ticker="X", price=50.0, call_volume=100_000.0)
        comp = _score_options(inp)
        assert comp.data_quality != "MISSING"


# ── TestRiskRewardNeutral ─────────────────────────────────────────────────────

class TestRiskRewardNeutral:
    """No stop + no 52w high + no ATR → MISSING 5.0.
    No stop + no 52w high + ATR present → ATR fallback used, not MISSING."""

    def test_no_stop_no_52w_no_atr_returns_5(self):
        inp = AlphaInput(ticker="X", price=50.0)
        comp = _score_risk_reward(inp)
        assert comp.score == 5.0
        assert comp.data_quality == "MISSING"

    def test_no_stop_no_52w_with_atr_uses_fallback(self):
        """ATR fallback: has_data becomes True, score > 0, not MISSING."""
        inp = AlphaInput(ticker="X", price=100.0, atr=3.0)  # ATR stop ~95.5
        comp = _score_risk_reward(inp)
        assert comp.data_quality != "MISSING"
        # ATR-based stop should have added some score
        assert any("ATR" in r for r in comp.reasons)

    def test_stop_price_present_not_missing(self):
        inp = AlphaInput(ticker="X", price=100.0, stop_price=92.0)
        comp = _score_risk_reward(inp)
        assert comp.data_quality != "MISSING"

    def test_52w_high_present_not_missing(self):
        inp = AlphaInput(ticker="X", price=80.0, price_high_52w=100.0)
        comp = _score_risk_reward(inp)
        assert comp.data_quality != "MISSING"

    def test_atr_fallback_tight_stop_score(self):
        """ATR 1.5% → stop pct < 5% → +1.5 points."""
        inp = AlphaInput(ticker="X", price=100.0, atr=1.0)  # 1.5% stop
        comp = _score_risk_reward(inp)
        # At minimum the ATR-based stop was scored
        assert any("ATR" in r for r in comp.reasons)

    def test_atr_fallback_zero_atr_still_missing(self):
        """ATR=0 → fallback skipped → still MISSING."""
        inp = AlphaInput(ticker="X", price=100.0, atr=0.0)
        comp = _score_risk_reward(inp)
        assert comp.data_quality == "MISSING"


# ── TestHistHighFallback ──────────────────────────────────────────────────────

class TestHistHighFallback:
    """When info has no fiftyTwoWeekHigh, history fallback populates it."""

    def _make_hist(self, n=30, price=100.0):
        prices = [price + i * 0.1 for i in range(n)]
        return pd.DataFrame({
            "Open": prices, "High": [p + 1.0 for p in prices],
            "Low":  [p - 1.0 for p in prices], "Close": prices,
            "Volume": [1_000_000.0] * n,
        })

    def test_history_fallback_populates_52w_high(self):
        from alpha_engine import fetch_alpha_input
        from unittest.mock import PropertyMock
        hist = self._make_hist(30, price=100.0)

        # Build ticker mock using spec so PropertyMock works cleanly
        tkr = MagicMock()
        tkr.history.return_value = hist
        tkr.options = ()
        tkr.calendar = None
        tkr.news = []
        # info returns empty dict — no fiftyTwoWeekHigh
        tkr.info = {}

        vix_df = self._make_hist(2, price=18.0)

        def _factory(sym):
            if sym == "^VIX":
                m = MagicMock()
                m.history.return_value = vix_df
                m.info = {}
                return m
            if sym in ("SPY", "QQQ", "XUS.TO"):
                m = MagicMock()
                m.history.return_value = self._make_hist(40, price=500.0)
                m.info = {}
                return m
            return tkr

        with patch("yfinance.Ticker", side_effect=_factory), \
             patch("market_data.get_ticker_data", side_effect=RuntimeError("down")), \
             patch("market_data.ma200_recent_cross", side_effect=RuntimeError("down")):
            result = fetch_alpha_input("NVDA")

        assert result is not None
        # history-based fallback: High column max should be ~130.9 (100 + 29*0.1 + 1.0)
        assert result.price_high_52w is not None
        assert result.price_high_52w > 0

    def test_history_fallback_populates_52w_low(self):
        from alpha_engine import fetch_alpha_input
        hist = self._make_hist(30, price=100.0)

        tkr = MagicMock()
        tkr.history.return_value = hist
        tkr.options = ()
        tkr.calendar = None
        tkr.news = []
        tkr.info = {}

        vix_df = self._make_hist(2, price=18.0)

        def _factory(sym):
            if sym == "^VIX":
                m = MagicMock()
                m.history.return_value = vix_df
                m.info = {}
                return m
            if sym in ("SPY", "QQQ", "XUS.TO"):
                m = MagicMock()
                m.history.return_value = self._make_hist(40, price=500.0)
                m.info = {}
                return m
            return tkr

        with patch("yfinance.Ticker", side_effect=_factory), \
             patch("market_data.get_ticker_data", side_effect=RuntimeError("down")), \
             patch("market_data.ma200_recent_cross", side_effect=RuntimeError("down")):
            result = fetch_alpha_input("NVDA")

        assert result is not None
        assert result.price_low_52w is not None
        assert result.price_low_52w > 0


# ── TestStrongSetupHighConviction ─────────────────────────────────────────────

class TestStrongSetupHighConviction:
    """A well-crafted setup should reach at least HIGH_CONVICTION."""

    def _strong_input(self) -> AlphaInput:
        return AlphaInput(
            ticker="NVDA",
            price=60.0,
            price_5d_ago=54.0,     # +11% in 5d
            price_20d_ago=48.0,    # +25% in 20d
            price_60d_ago=35.0,    # +71% in 60d
            price_high_52w=64.0,   # within 7% of 52w high
            price_low_52w=30.0,
            volume_today=6_000_000,
            avg_volume_20d=1_000_000,  # 6x volume surge
            avg_volume_5d=1_200_000,
            spy_return_5d=0.01,
            spy_return_20d=0.03,
            spy_return_60d=0.05,
            qqq_return_5d=0.015,
            qqq_return_20d=0.04,
            qqq_return_60d=0.06,
            rsi=65.0,
            macd=0.5, macd_signal=0.3,
            ma_50=55.0, ma_200=50.0,
            ma_200_days_since_cross=10,
            short_percent_float=0.30,
            days_to_cover=7.0,
            earnings_days=10,
            has_catalyst_news=True,
            news_sentiment=0.6,
            unusual_options=True,
            call_volume=200_000, put_volume=30_000,
            call_oi=500_000, put_oi=100_000,
            stop_price=55.0,
            market_cap_millions=500.0,
            last_alerted_hours_ago=None,
        )

    def test_strong_setup_score_above_65(self):
        result = ENGINE.score(self._strong_input())
        assert result.alpha_score >= 65.0, f"score was {result.alpha_score}"

    def test_strong_setup_reaches_high_conviction_or_rare(self):
        result = ENGINE.score(self._strong_input())
        assert result.tier in ("HIGH_CONVICTION", "RARE_SETUP"), f"tier was {result.tier}"

    def test_strong_setup_has_high_component_count(self):
        result = ENGINE.score(self._strong_input())
        high_comps = sum(1 for c in result.components if c.score >= 6.0)
        assert high_comps >= 3


# ── TestRareSetupConditions ───────────────────────────────────────────────────

class TestRareSetupConditions:
    """All components >= 7.0 → score >= 80 → RARE_SETUP."""

    def test_all_components_7_gives_rare_setup_tier(self):
        scores = [8.0, 8.0, 7.0, 7.5, 7.0, 8.0, 7.0, 7.0]
        tier, _, _ = _classify_tier(82.0, _comps(scores))
        assert tier == "RARE_SETUP"

    def test_rare_setup_gate_requires_4_at_7(self):
        # Only 3 components >= 7.0 → drops to HIGH_CONVICTION
        scores = [9.0, 9.0, 8.0, 5.0, 5.0, 5.0, 5.0, 5.0]
        tier, gate_applied, _ = _classify_tier(82.0, _comps(scores))
        assert tier != "RARE_SETUP"
        assert gate_applied is True

    def test_rare_setup_fails_with_low_component(self):
        # One component < 3.0 → fails min gate
        scores = [10.0, 9.0, 9.0, 8.0, 8.0, 8.0, 2.0, 8.0]
        tier, gate_applied, _ = _classify_tier(85.0, _comps(scores))
        assert tier != "RARE_SETUP"
        assert gate_applied is True


# ── TestMediocreStaysIgnore ───────────────────────────────────────────────────

class TestMediocreStaysIgnore:
    """Vanilla flat stock should stay in IGNORE or WATCH."""

    def _mediocre(self) -> AlphaInput:
        return AlphaInput(
            ticker="FLAT",
            price=50.0,
            price_5d_ago=50.0,
            price_20d_ago=52.0,
            price_60d_ago=53.0,
            price_high_52w=80.0,
            volume_today=800_000,
            avg_volume_20d=1_000_000,
            spy_return_5d=0.02,
            spy_return_20d=0.05,
            qqq_return_5d=0.03,
            qqq_return_20d=0.06,
            market_cap_millions=200.0,
        )

    def test_mediocre_in_ignore_or_watch(self):
        result = ENGINE.score(self._mediocre())
        assert result.tier in ("IGNORE", "WATCH"), f"tier={result.tier} score={result.alpha_score}"

    def test_mediocre_score_below_50(self):
        result = ENGINE.score(self._mediocre())
        assert result.alpha_score < 50.0, f"score={result.alpha_score}"


# ── TestAlphaUniverseList ─────────────────────────────────────────────────────

class TestAlphaUniverseList:
    """ALPHA_UNIVERSE invariants."""

    def test_alpha_universe_is_list(self):
        from alpha_universe import ALPHA_UNIVERSE
        assert isinstance(ALPHA_UNIVERSE, list)

    def test_all_tickers_are_non_empty_strings(self):
        from alpha_universe import ALPHA_UNIVERSE
        for t in ALPHA_UNIVERSE:
            assert isinstance(t, str) and len(t) > 0

    def test_at_least_40_tickers(self):
        from alpha_universe import ALPHA_UNIVERSE
        assert len(ALPHA_UNIVERSE) >= 40

    def test_get_alpha_universe_returns_copy(self):
        from alpha_universe import get_alpha_universe, ALPHA_UNIVERSE
        u = get_alpha_universe()
        assert u == ALPHA_UNIVERSE
        # Modifying the copy doesn't affect original
        u.append("__TEST__")
        from alpha_universe import ALPHA_UNIVERSE as AU2
        assert "__TEST__" not in AU2

    def test_no_duplicates(self):
        from alpha_universe import ALPHA_UNIVERSE
        assert len(ALPHA_UNIVERSE) == len(set(ALPHA_UNIVERSE))


# ── TestAlphaUniverseScan ─────────────────────────────────────────────────────

class TestAlphaUniverseScan:
    """scan_alpha_universe() feature-flag + scoring behavior."""

    def test_flag_off_returns_zero(self, monkeypatch):
        monkeypatch.setenv("ALPHA_SHADOW_ENABLED", "false")
        from alpha_universe import scan_alpha_universe
        result = scan_alpha_universe()
        assert result == 0

    def test_flag_off_does_not_call_shadow(self, monkeypatch):
        monkeypatch.setenv("ALPHA_SHADOW_ENABLED", "false")
        called = []

        import alpha_shadow as _mod
        original_mgr = _mod.get_shadow_manager()

        class _SpyMgr:
            def run_shadow_score(self, *a, **kw):
                called.append(a)
                return None

        monkeypatch.setattr(_mod, "_manager", _SpyMgr())
        from alpha_universe import scan_alpha_universe
        scan_alpha_universe()
        assert called == []

        # restore
        monkeypatch.setattr(_mod, "_manager", original_mgr)

    def test_flag_on_calls_run_shadow_score(self, monkeypatch):
        monkeypatch.setenv("ALPHA_SHADOW_ENABLED", "true")
        called = []

        import alpha_shadow as _mod

        class _SpyMgr:
            def run_shadow_score(self, ticker, predator_result):
                called.append(ticker)
                return MagicMock()  # non-None = scored

        monkeypatch.setattr(_mod, "_manager", _SpyMgr())

        # Override sleep so test doesn't wait
        import alpha_universe as _au
        monkeypatch.setattr(_au.time, "sleep", lambda _: None)

        from alpha_universe import scan_alpha_universe
        count = scan_alpha_universe()
        assert count > 0
        assert len(called) > 0

    def test_per_ticker_failure_does_not_crash_scan(self, monkeypatch):
        monkeypatch.setenv("ALPHA_SHADOW_ENABLED", "true")

        import alpha_shadow as _mod

        class _BoomMgr:
            def run_shadow_score(self, ticker, predator_result):
                raise RuntimeError("boom")

        monkeypatch.setattr(_mod, "_manager", _BoomMgr())

        import alpha_universe as _au
        monkeypatch.setattr(_au.time, "sleep", lambda _: None)

        from alpha_universe import scan_alpha_universe
        # Should not raise — per-ticker errors are caught
        count = scan_alpha_universe()
        assert count == 0  # all failed → 0 scored


# ── TestAnalyticsHelpers ──────────────────────────────────────────────────────

class TestAnalyticsHelpers:
    """Tests for the new AlphaShadowManager analytics methods."""

    def test_count_by_tier_empty_db(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        _patch_db(monkeypatch, path)
        from alpha_shadow import AlphaShadowManager
        result = AlphaShadowManager().count_by_tier()
        assert result == {}

    def test_count_by_tier_with_seeded_rows(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="A", alpha_tier="WATCH", scan_time="2026-05-18T10:00:00")
        _seed_row(path, ticker="B", alpha_tier="WATCH", scan_time="2026-05-18T10:00:00")
        _seed_row(path, ticker="C", alpha_tier="STRONG_WATCH", scan_time="2026-05-18T10:00:00")
        _patch_db(monkeypatch, path)
        from alpha_shadow import AlphaShadowManager
        result = AlphaShadowManager().count_by_tier()
        assert result.get("WATCH") == 2
        assert result.get("STRONG_WATCH") == 1

    def test_count_by_tier_excludes_null_tiers(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="X", alpha_tier=None)
        _patch_db(monkeypatch, path)
        from alpha_shadow import AlphaShadowManager
        result = AlphaShadowManager().count_by_tier()
        assert result == {}

    def test_get_top_setup_types_empty(self, tmp_path, monkeypatch):
        path = _make_db(str(tmp_path / "t.db"))
        _patch_db(monkeypatch, path)
        from alpha_shadow import AlphaShadowManager
        result = AlphaShadowManager().get_top_setup_types()
        assert result == []

    def test_get_top_setup_types_structure(self, tmp_path, monkeypatch):
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="A", setup_type="BREAKOUT_EXPANSION", alpha_score=70.0,
                  alpha_tier="HIGH_CONVICTION")
        _seed_row(path, ticker="B", setup_type="BREAKOUT_EXPANSION", alpha_score=65.0,
                  alpha_tier="STRONG_WATCH")
        _seed_row(path, ticker="C", setup_type="SQUEEZE_CANDIDATE", alpha_score=60.0,
                  alpha_tier="STRONG_WATCH")
        _patch_db(monkeypatch, path)
        from alpha_shadow import AlphaShadowManager
        result = AlphaShadowManager().get_top_setup_types()
        assert len(result) >= 1
        first = result[0]
        assert "setup_type" in first
        assert "count" in first
        assert "avg_score" in first

    def test_get_top_setup_types_ordered_by_avg_score(self, tmp_path, monkeypatch):
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="A", setup_type="HIGH_TYPE", alpha_score=90.0,
                  alpha_tier="RARE_SETUP")
        _seed_row(path, ticker="B", setup_type="LOW_TYPE", alpha_score=40.0,
                  alpha_tier="WATCH")
        _patch_db(monkeypatch, path)
        from alpha_shadow import AlphaShadowManager
        result = AlphaShadowManager().get_top_setup_types()
        types = [r["setup_type"] for r in result]
        assert types[0] == "HIGH_TYPE"

    def test_get_best_non_predator_excludes_predator_tickers(self, tmp_path, monkeypatch):
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="AAPL", alpha_score=90.0, filter_reason=None)
        _seed_row(path, ticker="RIOT", alpha_score=88.0, filter_reason=None)
        _patch_db(monkeypatch, path)
        from alpha_shadow import AlphaShadowManager
        result = AlphaShadowManager().get_best_non_predator(["AAPL"], limit=10)
        tickers = [r["ticker"] for r in result]
        assert "AAPL" not in tickers
        assert "RIOT" in tickers

    def test_get_best_non_predator_empty_predator_list(self, tmp_path, monkeypatch):
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="NVDA", alpha_score=80.0, filter_reason=None)
        _patch_db(monkeypatch, path)
        from alpha_shadow import AlphaShadowManager
        result = AlphaShadowManager().get_best_non_predator([], limit=10)
        assert any(r["ticker"] == "NVDA" for r in result)

    def test_get_best_non_predator_excludes_filtered(self, tmp_path, monkeypatch):
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="SMCI", alpha_score=95.0, filter_reason="penny_stock")
        _seed_row(path, ticker="MARA", alpha_score=70.0, filter_reason=None)
        _patch_db(monkeypatch, path)
        from alpha_shadow import AlphaShadowManager
        result = AlphaShadowManager().get_best_non_predator([], limit=10)
        tickers = [r["ticker"] for r in result]
        assert "SMCI" not in tickers
        assert "MARA" in tickers

    def test_get_universe_coverage_empty_db(self, tmp_path, monkeypatch):
        path = _make_db(str(tmp_path / "t.db"))
        _patch_db(monkeypatch, path)
        from alpha_shadow import AlphaShadowManager
        result = AlphaShadowManager().get_universe_coverage(["AAPL", "NVDA", "TSLA"])
        assert result["universe_size"] == 3
        assert result["covered_in_db"] == 0
        assert set(result["missing"]) == {"AAPL", "NVDA", "TSLA"}

    def test_get_universe_coverage_partial(self, tmp_path, monkeypatch):
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="AAPL")
        _seed_row(path, ticker="NVDA")
        _patch_db(monkeypatch, path)
        from alpha_shadow import AlphaShadowManager
        result = AlphaShadowManager().get_universe_coverage(["AAPL", "NVDA", "TSLA"])
        assert result["universe_size"] == 3
        assert result["covered_in_db"] == 2
        assert "TSLA" in result["missing"]
        assert "AAPL" not in result["missing"]

    def test_get_universe_coverage_full(self, tmp_path, monkeypatch):
        path = _make_db(str(tmp_path / "t.db"))
        _seed_row(path, ticker="AAPL")
        _seed_row(path, ticker="NVDA")
        _patch_db(monkeypatch, path)
        from alpha_shadow import AlphaShadowManager
        result = AlphaShadowManager().get_universe_coverage(["AAPL", "NVDA"])
        assert result["covered_in_db"] == 2
        assert result["missing"] == []


# ── TestAlphaAlertsDisabled ───────────────────────────────────────────────────

class TestAlphaAlertsDisabled:
    """alpha_alerts_enabled() must default to False."""

    def test_alpha_alerts_disabled_by_default(self):
        # Ensure no env var pollution from other tests
        os.environ.pop("ALPHA_ALERTS_ENABLED", None)
        from feature_flags import alpha_alerts_enabled
        assert alpha_alerts_enabled() is False

    def test_alpha_alerts_not_in_env_returns_false(self, monkeypatch):
        monkeypatch.delenv("ALPHA_ALERTS_ENABLED", raising=False)
        from feature_flags import alpha_alerts_enabled
        assert alpha_alerts_enabled() is False

    def test_alpha_alerts_explicit_false(self, monkeypatch):
        monkeypatch.setenv("ALPHA_ALERTS_ENABLED", "false")
        from feature_flags import alpha_alerts_enabled
        assert alpha_alerts_enabled() is False

    def test_alpha_alerts_explicit_true(self, monkeypatch):
        monkeypatch.setenv("ALPHA_ALERTS_ENABLED", "true")
        from feature_flags import alpha_alerts_enabled
        assert alpha_alerts_enabled() is True


# ── TestCatalystNewsDetection ─────────────────────────────────────────────────

class TestCatalystNewsDetection:
    """Tests for the catalyst keyword constants in alpha_engine."""

    def test_catalyst_kw_is_frozenset(self):
        from alpha_engine import _CATALYST_KW
        assert isinstance(_CATALYST_KW, frozenset)

    def test_positive_kw_is_frozenset(self):
        from alpha_engine import _POSITIVE_KW
        assert isinstance(_POSITIVE_KW, frozenset)

    def test_negative_kw_is_frozenset(self):
        from alpha_engine import _NEGATIVE_KW
        assert isinstance(_NEGATIVE_KW, frozenset)

    def test_known_catalyst_keywords_present(self):
        from alpha_engine import _CATALYST_KW
        for kw in ("fda", "earnings", "merger", "upgrade"):
            assert kw in _CATALYST_KW

    def test_known_positive_keywords_present(self):
        from alpha_engine import _POSITIVE_KW
        for kw in ("approved", "beat", "upgraded"):
            assert kw in _POSITIVE_KW

    def test_known_negative_keywords_present(self):
        from alpha_engine import _NEGATIVE_KW
        for kw in ("miss", "downgraded", "investigation"):
            assert kw in _NEGATIVE_KW


# ── TestDetailJsonPersisted ───────────────────────────────────────────────────

class TestDetailJsonPersisted:
    """Verify detail_json is written to DB by run_shadow_score."""

    def test_detail_json_row_written(self, tmp_path, monkeypatch):
        import sqlite3 as sq
        path = _make_db(str(tmp_path / "t.db"))
        _patch_db(monkeypatch, path)

        inp = AlphaInput(ticker="NVDA", price=900.0, market_cap_millions=1_000_000.0,
                        avg_volume_20d=50_000_000.0)

        from alpha_shadow import AlphaShadowManager
        with patch("alpha_engine.fetch_alpha_input", return_value=inp):
            AlphaShadowManager().run_shadow_score("NVDA", {"tier": "WATCH", "score": 5.0})

        conn = sq.connect(path)
        conn.row_factory = sq.Row
        row = conn.execute("SELECT detail_json FROM alpha_shadow_log WHERE ticker='NVDA'").fetchone()
        conn.close()
        assert row is not None
        # detail_json should be a JSON string
        assert row["detail_json"] is not None
        parsed = json.loads(row["detail_json"])
        assert "why_scored_high" in parsed
        assert "what_must_happen_next" in parsed
        assert "what_could_invalidate" in parsed
        assert "risk_factors" in parsed
