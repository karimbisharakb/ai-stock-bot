"""
Phase A15 — Tests for portfolio_risk_guardrails.py and related API endpoints.

Covers:
  - get_risk_policy / update_risk_policy (DB)
  - get_theme / is_speculative / is_cad (pure classification)
  - compute_position_sizing (pure): ENTER/ADD, tiers, haircuts, sparse data
  - compute_concentration (pure): cash, speculative, single-position, theme warnings
  - _compute_risk_score (pure)
  - _compute_ticker_risk_table (pure)
  - get_portfolio_risk_report (mocked portfolio)
  - get_size_check (mocked portfolio + thesis)
  - Checklist item suggestions
  - Sparse data safety
  - Safety: no trading, no broker calls
  - API: GET /portfolio/risk, GET /decisions/size-check
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
import portfolio_risk_guardrails as prg

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_get_conn(db_path: str):
    def _get_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    return _get_conn


def _db(tmp_path):
    db_path = str(tmp_path / "test_a15.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS risk_policy (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            max_single_position_pct REAL NOT NULL DEFAULT 10.0,
            max_speculative_pct REAL NOT NULL DEFAULT 20.0,
            max_same_theme_pct REAL NOT NULL DEFAULT 25.0,
            max_expected_loss_pct REAL NOT NULL DEFAULT 2.0,
            min_cash_reserve_pct REAL NOT NULL DEFAULT 5.0,
            high_volatility_haircut REAL NOT NULL DEFAULT 0.5,
            risk_off_haircut REAL NOT NULL DEFAULT 0.5,
            risk_off_mode INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
    monkeypatch.setattr(prg, "_ensure_tables", lambda: None)
    return db_path


def _make_position(ticker, market_value, concentration_pct, unrealized_pnl_pct=0.0, market_price=None):
    return {
        "ticker": ticker,
        "market_value": market_value,
        "concentration_pct": concentration_pct,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "market_price": market_price or (market_value / 10),
        "quantity": 10,
        "avg_cost": market_value / 10,
    }


# ── get_risk_policy ───────────────────────────────────────────────────────────

class TestGetRiskPolicy:
    def test_returns_dict(self, db):
        policy = prg.get_risk_policy()
        assert isinstance(policy, dict)

    def test_default_max_single(self, db):
        assert prg.get_risk_policy()["max_single_position_pct"] == 10.0

    def test_default_max_speculative(self, db):
        assert prg.get_risk_policy()["max_speculative_pct"] == 20.0

    def test_default_min_cash(self, db):
        assert prg.get_risk_policy()["min_cash_reserve_pct"] == 5.0

    def test_default_risk_off_mode_false(self, db):
        assert prg.get_risk_policy()["risk_off_mode"] is False

    def test_seeds_on_first_call(self, db):
        conn = sqlite3.connect(db)
        cnt = conn.execute("SELECT COUNT(*) FROM risk_policy").fetchone()[0]
        conn.close()
        assert cnt == 0
        prg.get_risk_policy()
        conn = sqlite3.connect(db)
        cnt = conn.execute("SELECT COUNT(*) FROM risk_policy").fetchone()[0]
        conn.close()
        assert cnt == 1

    def test_second_call_reads_existing(self, db):
        p1 = prg.get_risk_policy()
        p2 = prg.get_risk_policy()
        assert p1["max_single_position_pct"] == p2["max_single_position_pct"]


# ── update_risk_policy ────────────────────────────────────────────────────────

class TestUpdateRiskPolicy:
    def test_partial_update(self, db):
        prg.get_risk_policy()
        result = prg.update_risk_policy(max_single_position_pct=15.0)
        assert result["ok"] is True
        assert result["policy"]["max_single_position_pct"] == 15.0

    def test_other_fields_unchanged(self, db):
        prg.get_risk_policy()
        prg.update_risk_policy(max_single_position_pct=15.0)
        assert prg.get_risk_policy()["min_cash_reserve_pct"] == 5.0

    def test_risk_off_mode_update(self, db):
        prg.get_risk_policy()
        result = prg.update_risk_policy(risk_off_mode=1)
        assert result["ok"] is True
        assert result["policy"]["risk_off_mode"] is True

    def test_unknown_field_returns_error(self, db):
        result = prg.update_risk_policy(bogus_field=99)
        assert result["ok"] is False
        assert any("UNKNOWN_FIELD" in e for e in result["errors"])

    def test_invalid_value_returns_error(self, db):
        result = prg.update_risk_policy(max_single_position_pct="notanumber")
        assert result["ok"] is False

    def test_empty_kwargs_returns_ok(self, db):
        prg.get_risk_policy()
        result = prg.update_risk_policy()
        assert result["ok"] is True


# ── Classification helpers (pure) ─────────────────────────────────────────────

class TestGetTheme:
    def test_nvda_is_ai_tech(self):
        assert prg.get_theme("NVDA") == "AI_TECH"

    def test_spy_is_broad_market(self):
        assert prg.get_theme("SPY") == "BROAD_MARKET"

    def test_ry_to_is_financials(self):
        assert prg.get_theme("RY.TO") == "FINANCIALS"

    def test_enb_to_is_energy(self):
        assert prg.get_theme("ENB.TO") == "ENERGY"

    def test_unknown_is_other(self):
        assert prg.get_theme("XYZ123") == "OTHER"

    def test_lowercase_ticker(self):
        assert prg.get_theme("nvda") == "AI_TECH"

    def test_shop_to_is_ai_tech(self):
        assert prg.get_theme("SHOP.TO") == "AI_TECH"


class TestIsSpeculative:
    def test_etf_not_speculative(self):
        for ticker in ("QQQ", "SPY", "VFV.TO", "XIU.TO"):
            assert prg.is_speculative(ticker) is False

    def test_individual_stock_speculative(self):
        for ticker in ("NVDA", "AAPL", "SHOP.TO", "RY.TO"):
            assert prg.is_speculative(ticker) is True

    def test_lowercase_handled(self):
        assert prg.is_speculative("qqq") is False
        assert prg.is_speculative("nvda") is True


class TestIsCad:
    def test_to_suffix_is_cad(self):
        for ticker in ("RY.TO", "SHOP.TO", "VFV.TO"):
            assert prg.is_cad(ticker) is True

    def test_us_ticker_not_cad(self):
        for ticker in ("NVDA", "AAPL", "QQQ", "SPY"):
            assert prg.is_cad(ticker) is False

    def test_lowercase_handled(self):
        assert prg.is_cad("ry.to") is True
        assert prg.is_cad("nvda") is False


# ── compute_position_sizing (pure) ────────────────────────────────────────────

class TestComputePositionSizing:
    def _policy(self):
        return dict(prg.DEFAULT_POLICY)

    def test_enter_normal_tier(self):
        p = self._policy()
        result = prg.compute_position_sizing("NVDA", "ENTER", 0, 100_000, 20_000, p)
        assert result["sizing_tier"] == "NORMAL"
        assert result["suggested_size_cad"] > 0

    def test_enter_max_size_is_10pct(self):
        p = self._policy()
        result = prg.compute_position_sizing("NVDA", "ENTER", 0, 100_000, 20_000, p)
        assert result["max_position_size_cad"] == pytest.approx(10_000.0)

    def test_add_remaining_budget_reduced(self):
        p = self._policy()
        # Already hold 8% of 100k = 8000
        result = prg.compute_position_sizing("NVDA", "ADD", 8_000, 100_000, 10_000, p)
        assert result["remaining_budget_cad"] == pytest.approx(2_000.0)

    def test_too_risky_when_at_limit(self):
        p = self._policy()
        result = prg.compute_position_sizing("NVDA", "ENTER", 10_000, 100_000, 5_000, p)
        assert result["sizing_tier"] == "TOO_RISKY"
        assert result["suggested_size_cad"] == 0.0

    def test_high_conviction_near_limit(self):
        p = self._policy()
        # 8% of 10% max = 80% full
        result = prg.compute_position_sizing("NVDA", "ENTER", 8_000, 100_000, 5_000, p)
        assert result["sizing_tier"] == "HIGH_CONVICTION_ONLY"

    def test_small_only_for_high_volatility(self):
        p = self._policy()
        result = prg.compute_position_sizing("NVDA", "ENTER", 0, 100_000, 20_000, p,
                                              is_high_volatility=True)
        assert result["sizing_tier"] == "SMALL_ONLY"
        assert result["haircut_applied"] is True
        assert "high-volatility" in result["haircut_reason"]

    def test_small_only_for_risk_off(self):
        p = self._policy()
        result = prg.compute_position_sizing("NVDA", "ENTER", 0, 100_000, 20_000, p,
                                              risk_off=True)
        assert result["sizing_tier"] == "SMALL_ONLY"
        assert result["haircut_applied"] is True
        assert "RISK_OFF" in result["haircut_reason"]

    def test_haircut_reduces_suggested_size(self):
        p = self._policy()
        no_haircut = prg.compute_position_sizing("NVDA", "ENTER", 0, 100_000, 20_000, p)
        with_haircut = prg.compute_position_sizing("NVDA", "ENTER", 0, 100_000, 20_000, p,
                                                    is_high_volatility=True)
        assert with_haircut["suggested_size_cad"] < no_haircut["suggested_size_cad"]

    def test_both_haircuts_applied(self):
        p = self._policy()
        result = prg.compute_position_sizing("NVDA", "ENTER", 0, 100_000, 20_000, p,
                                              is_high_volatility=True, risk_off=True)
        assert result["haircut_applied"] is True
        assert "RISK_OFF" in result["haircut_reason"]

    def test_stop_distance_calculated(self):
        p = self._policy()
        result = prg.compute_position_sizing(
            "NVDA", "ENTER", 0, 100_000, 20_000, p,
            stop_price=90.0, current_price=100.0,
        )
        assert result["stop_distance_pct"] == pytest.approx(10.0)

    def test_no_stop_distance_when_no_prices(self):
        p = self._policy()
        result = prg.compute_position_sizing("NVDA", "ENTER", 0, 100_000, 20_000, p)
        assert result["stop_distance_pct"] is None

    def test_max_loss_is_2pct_of_portfolio(self):
        p = self._policy()
        result = prg.compute_position_sizing("NVDA", "ENTER", 0, 100_000, 20_000, p)
        assert result["max_loss_amount_cad"] == pytest.approx(2_000.0)

    def test_exit_decision_returns_normal_tier(self):
        p = self._policy()
        result = prg.compute_position_sizing("NVDA", "EXIT", 5_000, 100_000, 0, p)
        assert result["sizing_tier"] == "NORMAL"
        assert result["suggested_size_cad"] == pytest.approx(5_000.0)

    def test_hold_decision_returns_normal_tier(self):
        p = self._policy()
        result = prg.compute_position_sizing("NVDA", "HOLD", 5_000, 100_000, 5_000, p)
        assert result["sizing_tier"] == "NORMAL"

    def test_reduce_decision_returns_normal_tier(self):
        p = self._policy()
        result = prg.compute_position_sizing("NVDA", "REDUCE", 5_000, 100_000, 0, p)
        assert result["sizing_tier"] == "NORMAL"

    def test_sparse_data_returns_not_ready(self):
        p = self._policy()
        result = prg.compute_position_sizing("NVDA", "ENTER", 0, 0, 0, p)
        assert result["sizing_tier"] == "NOT_READY"
        assert "unavailable" in result["risk_reward_note"].lower()

    def test_concentration_pct_calculated(self):
        p = self._policy()
        result = prg.compute_position_sizing("NVDA", "ENTER", 5_000, 100_000, 10_000, p)
        assert result["current_concentration_pct"] == pytest.approx(5.0)


# ── compute_concentration (pure) ──────────────────────────────────────────────

class TestComputeConcentration:
    def _policy(self):
        return dict(prg.DEFAULT_POLICY)

    def test_cash_warning_when_below_minimum(self):
        p = self._policy()
        positions = [_make_position("NVDA", 97_000, 97.0)]
        c = prg.compute_concentration(positions, 100_000, 3_000, p)
        assert c["cash_warning"] is not None
        assert "below" in c["cash_warning"].lower()

    def test_no_cash_warning_when_adequate(self):
        p = self._policy()
        positions = [_make_position("NVDA", 90_000, 90.0)]
        c = prg.compute_concentration(positions, 100_000, 10_000, p)
        assert c["cash_warning"] is None

    def test_speculative_warning_when_over_limit(self):
        p = self._policy()
        # 25k in individual stocks out of 100k = 25% > 20% limit
        positions = [_make_position("NVDA", 25_000, 25.0)]
        c = prg.compute_concentration(positions, 100_000, 75_000, p)
        assert c["speculative_warning"] is not None

    def test_no_speculative_warning_for_etfs(self):
        p = self._policy()
        positions = [_make_position("QQQ", 50_000, 50.0)]
        c = prg.compute_concentration(positions, 100_000, 50_000, p)
        assert c["speculative_warning"] is None

    def test_concentration_warning_when_over_limit(self):
        p = self._policy()
        positions = [_make_position("NVDA", 15_000, 15.0)]
        c = prg.compute_concentration(positions, 100_000, 85_000, p)
        assert len(c["concentration_warnings"]) == 1
        assert "NVDA" in c["concentration_warnings"][0]

    def test_no_concentration_warning_when_under_limit(self):
        p = self._policy()
        positions = [_make_position("NVDA", 9_000, 9.0)]
        c = prg.compute_concentration(positions, 100_000, 91_000, p)
        assert len(c["concentration_warnings"]) == 0

    def test_theme_warning_when_over_limit(self):
        p = self._policy()
        # NVDA (AI_TECH) 15% + MSFT (AI_TECH) 15% = 30% > 25% limit
        positions = [
            _make_position("NVDA", 15_000, 15.0),
            _make_position("MSFT", 15_000, 15.0),
        ]
        c = prg.compute_concentration(positions, 100_000, 70_000, p)
        assert any("AI_TECH" in w for w in c["theme_warnings"])

    def test_cad_usd_split(self):
        p = self._policy()
        positions = [
            _make_position("RY.TO", 40_000, 40.0),
            _make_position("NVDA", 40_000, 40.0),
        ]
        c = prg.compute_concentration(positions, 100_000, 20_000, p)
        assert c["cad_pct"] == pytest.approx(40.0)
        assert c["usd_pct"] == pytest.approx(40.0)

    def test_cash_pct_computed(self):
        p = self._policy()
        positions = [_make_position("NVDA", 80_000, 80.0)]
        c = prg.compute_concentration(positions, 100_000, 20_000, p)
        assert c["cash_pct"] == pytest.approx(20.0)

    def test_empty_positions_no_warnings(self):
        p = self._policy()
        c = prg.compute_concentration([], 10_000, 10_000, p)
        assert c["all_warnings"] == []
        assert c["cash_pct"] == pytest.approx(100.0)

    def test_sparse_data_safe(self):
        p = self._policy()
        c = prg.compute_concentration([], 0, 0, p)
        assert c["cash_pct"] == 100.0
        assert c["all_warnings"] == []

    def test_all_warnings_aggregated(self):
        p = self._policy()
        positions = [_make_position("NVDA", 15_000, 15.0)]
        c = prg.compute_concentration(positions, 100_000, 3_000, p)
        # cash warning (3% < 5%) + speculative warning (15% < 20%... no) + concentration (15% > 10%)
        assert len(c["all_warnings"]) >= 2


# ── _compute_risk_score (pure) ────────────────────────────────────────────────

class TestComputeRiskScore:
    def _policy(self):
        return dict(prg.DEFAULT_POLICY)

    def test_zero_when_all_fine(self):
        p = self._policy()
        score = prg._compute_risk_score(5.0, 10.0, 15.0, 0, p)
        assert score == 0.0

    def test_max_concentration_adds_30(self):
        p = self._policy()
        score = prg._compute_risk_score(10.0, 0.0, 20.0, 0, p)
        assert score >= 30.0

    def test_near_concentration_adds_15(self):
        p = self._policy()
        score = prg._compute_risk_score(8.0, 0.0, 20.0, 0, p)  # 8% = 80% of 10%
        assert score >= 15.0

    def test_speculative_over_limit_adds_25(self):
        p = self._policy()
        score = prg._compute_risk_score(0.0, 25.0, 20.0, 0, p)
        assert score >= 25.0

    def test_low_cash_adds_20(self):
        p = self._policy()
        score = prg._compute_risk_score(0.0, 0.0, 2.0, 0, p)  # 2% < 5% min
        assert score >= 20.0

    def test_warnings_add_to_score(self):
        p = self._policy()
        s1 = prg._compute_risk_score(0.0, 0.0, 20.0, 0, p)
        s2 = prg._compute_risk_score(0.0, 0.0, 20.0, 3, p)
        assert s2 > s1

    def test_capped_at_100(self):
        p = self._policy()
        score = prg._compute_risk_score(15.0, 30.0, 1.0, 20, p)
        assert score <= 100.0

    def test_returns_float(self):
        p = self._policy()
        assert isinstance(prg._compute_risk_score(5.0, 10.0, 10.0, 1, p), float)


# ── _compute_ticker_risk_table (pure) ─────────────────────────────────────────

class TestComputeTickerRiskTable:
    def _policy(self):
        return dict(prg.DEFAULT_POLICY)

    def test_sorted_desc_by_concentration(self):
        p = self._policy()
        positions = [
            _make_position("NVDA", 3_000, 3.0),
            _make_position("AAPL", 8_000, 8.0),
        ]
        table = prg._compute_ticker_risk_table(positions, 100_000, p)
        assert table[0]["ticker"] == "AAPL"

    def test_flags_over_concentration(self):
        p = self._policy()
        positions = [_make_position("NVDA", 15_000, 15.0)]
        table = prg._compute_ticker_risk_table(positions, 100_000, p)
        assert "OVER_CONCENTRATION_LIMIT" in table[0]["risk_flags"]

    def test_theme_classified(self):
        p = self._policy()
        positions = [_make_position("NVDA", 5_000, 5.0)]
        table = prg._compute_ticker_risk_table(positions, 100_000, p)
        assert table[0]["theme"] == "AI_TECH"

    def test_is_speculative_flag(self):
        p = self._policy()
        positions = [_make_position("QQQ", 5_000, 5.0), _make_position("NVDA", 5_000, 5.0)]
        table = prg._compute_ticker_risk_table(positions, 100_000, p)
        by_ticker = {t["ticker"]: t for t in table}
        assert by_ticker["QQQ"]["is_speculative"] is False
        assert by_ticker["NVDA"]["is_speculative"] is True

    def test_empty_positions_returns_empty(self):
        p = self._policy()
        assert prg._compute_ticker_risk_table([], 100_000, p) == []


# ── get_portfolio_risk_report (mocked) ────────────────────────────────────────

class TestGetPortfolioRiskReport:
    def _mock_portfolio(self, positions, cash=10_000, total=100_000):
        return {
            "positions": positions,
            "aggregates": {"cash": cash, "total_portfolio_value": total},
        }

    def test_structure_keys(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio", lambda: self._mock_portfolio([]))
        result = prg.get_portfolio_risk_report()
        for key in ("portfolio_risk_score", "concentration_warnings", "cash_warning",
                    "ticker_risk_table", "recommended_actions", "policy", "checked_at"):
            assert key in result

    def test_empty_portfolio_safe(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio", lambda: self._mock_portfolio([]))
        result = prg.get_portfolio_risk_report()
        assert result["portfolio_risk_score"] == 0.0
        assert result["ticker_risk_table"] == []

    def test_concentration_warning_surfaced(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        positions = [_make_position("NVDA", 15_000, 15.0)]
        monkeypatch.setattr(pr, "get_canonical_portfolio",
                            lambda: self._mock_portfolio(positions, cash=85_000, total=100_000))
        result = prg.get_portfolio_risk_report()
        assert len(result["concentration_warnings"]) >= 1

    def test_drawdown_warning_for_deep_loss(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        positions = [_make_position("NVDA", 8_000, 8.0, unrealized_pnl_pct=-20.0)]
        monkeypatch.setattr(pr, "get_canonical_portfolio",
                            lambda: self._mock_portfolio(positions))
        result = prg.get_portfolio_risk_report()
        assert result["drawdown_warning"] is not None
        assert "NVDA" in result["drawdown_warning"]

    def test_no_drawdown_warning_for_small_loss(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        positions = [_make_position("NVDA", 8_000, 8.0, unrealized_pnl_pct=-5.0)]
        monkeypatch.setattr(pr, "get_canonical_portfolio",
                            lambda: self._mock_portfolio(positions))
        result = prg.get_portfolio_risk_report()
        assert result["drawdown_warning"] is None

    def test_risk_score_increases_with_concentration(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        p_low = [_make_position("NVDA", 5_000, 5.0)]
        p_high = [_make_position("NVDA", 15_000, 15.0)]
        monkeypatch.setattr(pr, "get_canonical_portfolio",
                            lambda: self._mock_portfolio(p_low))
        s_low = prg.get_portfolio_risk_report()["portfolio_risk_score"]
        monkeypatch.setattr(pr, "get_canonical_portfolio",
                            lambda: self._mock_portfolio(p_high))
        s_high = prg.get_portfolio_risk_report()["portfolio_risk_score"]
        assert s_high > s_low

    def test_recommended_actions_non_empty_when_warnings(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        positions = [_make_position("NVDA", 15_000, 15.0)]
        monkeypatch.setattr(pr, "get_canonical_portfolio",
                            lambda: self._mock_portfolio(positions, cash=85_000, total=100_000))
        result = prg.get_portfolio_risk_report()
        assert len(result["recommended_actions"]) > 0


# ── get_size_check (mocked) ───────────────────────────────────────────────────

class TestGetSizeCheck:
    def _mock_portfolio(self, positions=None, cash=20_000, total=100_000):
        return {
            "positions": positions or [],
            "aggregates": {"cash": cash, "total_portfolio_value": total},
        }

    def test_structure_keys(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio", lambda: self._mock_portfolio())
        result = prg.get_size_check("NVDA", "ENTER")
        for key in ("ticker", "decision_type", "sizing_guidance", "blockers",
                    "warnings", "checklist_item_suggestions", "checked_at"):
            assert key in result

    def test_normal_entry(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio", lambda: self._mock_portfolio())
        result = prg.get_size_check("NVDA", "ENTER")
        assert result["sizing_guidance"]["sizing_tier"] in ("NORMAL", "SMALL_ONLY")

    def test_too_risky_when_at_limit(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        positions = [_make_position("NVDA", 10_000, 10.0)]
        monkeypatch.setattr(pr, "get_canonical_portfolio",
                            lambda: self._mock_portfolio(positions))
        result = prg.get_size_check("NVDA", "ADD")
        assert result["sizing_guidance"]["sizing_tier"] == "TOO_RISKY"
        assert len(result["blockers"]) >= 1

    def test_checklist_position_size_suggestion(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio", lambda: self._mock_portfolio())
        result = prg.get_size_check("QQQ", "ENTER")  # ETF, no volatility haircut
        sugg = result["checklist_item_suggestions"]
        assert "position_size_reasonable" in sugg

    def test_stop_defined_when_thesis_has_invalidation(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        import position_journal
        monkeypatch.setattr(pr, "get_canonical_portfolio", lambda: self._mock_portfolio())
        monkeypatch.setattr(
            position_journal, "get_thesis",
            lambda t: {"invalidation_level": 85.0},
        )
        result = prg.get_size_check("NVDA", "ENTER")
        assert result["checklist_item_suggestions"]["stop_invalidation_defined"] is True

    def test_stop_none_when_no_thesis(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        import position_journal
        monkeypatch.setattr(pr, "get_canonical_portfolio", lambda: self._mock_portfolio())
        monkeypatch.setattr(position_journal, "get_thesis", lambda t: None)
        result = prg.get_size_check("NVDA", "ENTER")
        assert result["checklist_item_suggestions"]["stop_invalidation_defined"] is None

    def test_risk_reward_acceptable_always_none(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio", lambda: self._mock_portfolio())
        result = prg.get_size_check("NVDA", "ENTER")
        assert result["checklist_item_suggestions"]["risk_reward_acceptable"] is None

    def test_etf_has_no_volatility_haircut(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio", lambda: self._mock_portfolio())
        result = prg.get_size_check("QQQ", "ENTER")
        assert result["sizing_guidance"]["haircut_applied"] is False

    def test_individual_stock_has_haircut(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio", lambda: self._mock_portfolio())
        result = prg.get_size_check("NVDA", "ENTER")
        assert result["sizing_guidance"]["haircut_applied"] is True

    def test_risk_off_haircut_applied(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio", lambda: self._mock_portfolio())
        prg.get_risk_policy()
        prg.update_risk_policy(risk_off_mode=1)
        result = prg.get_size_check("QQQ", "ENTER")  # ETF but RISK_OFF
        assert result["sizing_guidance"]["haircut_applied"] is True

    def test_ticker_normalized_uppercase(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio", lambda: self._mock_portfolio())
        result = prg.get_size_check("nvda", "enter")
        assert result["ticker"] == "NVDA"
        assert result["decision_type"] == "ENTER"


# ── Sparse data safety ────────────────────────────────────────────────────────

class TestSparseData:
    def test_empty_portfolio_risk_report(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio",
                            lambda: {"positions": [], "aggregates": {}})
        result = prg.get_portfolio_risk_report()
        assert result["portfolio_risk_score"] == 0.0

    def test_size_check_empty_portfolio(self, db, monkeypatch):
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio",
                            lambda: {"positions": [], "aggregates": {}})
        result = prg.get_size_check("NVDA", "ENTER")
        assert result["sizing_guidance"]["sizing_tier"] == "NOT_READY"

    def test_concentration_empty_positions_safe(self):
        p = dict(prg.DEFAULT_POLICY)
        c = prg.compute_concentration([], 0, 0, p)
        assert c["cash_pct"] == 100.0

    def test_sizing_zero_portfolio_safe(self):
        p = dict(prg.DEFAULT_POLICY)
        result = prg.compute_position_sizing("NVDA", "ENTER", 0, 0, 0, p)
        assert result["sizing_tier"] == "NOT_READY"


# ── Safety constraints ────────────────────────────────────────────────────────

class TestSafetyConstraints:
    def test_no_trading_operations(self):
        source = inspect.getsource(prg)
        for pattern in ("record_buy_trade", "reduce_or_remove_holding",
                        "add_or_update_holding", "place_order", "submit_order"):
            assert pattern not in source

    def test_no_broker_calls(self):
        source = inspect.getsource(prg)
        for pattern in ("broker_client", "wealthsimple_api"):
            assert pattern not in source

    def test_no_autonomous_actions(self):
        source = inspect.getsource(prg)
        assert "execute_trade" not in source
        assert "send_order" not in source

    def test_output_is_guidance_only(self):
        source = inspect.getsource(prg)
        assert "guidance" in source.lower() or "advisory" in source.lower()


# ── API fixture ───────────────────────────────────────────────────────────────

@pytest.fixture
def flask_app(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
    monkeypatch.setattr(prg, "_ensure_tables", lambda: None)

    from flask import Flask
    import api as api_mod
    api_mod.cache_clear()

    flask_test_app = Flask("test_a15")
    flask_test_app.register_blueprint(api_mod.api_bp)
    flask_test_app.config["TESTING"] = True

    return flask_test_app, db_path


# ── API: GET /portfolio/risk ───────────────────────────────────────────────────

class TestApiPortfolioRisk:
    def test_structure(self, flask_app, monkeypatch):
        app, _ = flask_app
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio",
                            lambda: {"positions": [], "aggregates": {}})
        with app.test_client() as c:
            r = c.get("/api/v1/portfolio/risk")
            assert r.status_code == 200
            data = r.get_json()["data"]
            assert "portfolio_risk_score" in data
            assert "ticker_risk_table" in data
            assert "policy" in data

    def test_no_auth_required(self, flask_app, monkeypatch):
        app, _ = flask_app
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio",
                            lambda: {"positions": [], "aggregates": {}})
        with app.test_client() as c:
            r = c.get("/api/v1/portfolio/risk")
            assert r.status_code == 200

    def test_concentration_warning_in_response(self, flask_app, monkeypatch):
        app, db_path = flask_app
        import database as db_mod
        import api as api_mod
        import portfolio_reconciliation as pr
        db_mod.get_connection = _make_get_conn(db_path)
        positions = [_make_position("NVDA", 15_000, 15.0)]
        monkeypatch.setattr(pr, "get_canonical_portfolio",
                            lambda: {"positions": positions,
                                     "aggregates": {"cash": 85_000,
                                                     "total_portfolio_value": 100_000}})
        api_mod.cache_clear()
        with app.test_client() as c:
            r = c.get("/api/v1/portfolio/risk")
            data = r.get_json()["data"]
            assert len(data["concentration_warnings"]) >= 1

    def test_ok_flag(self, flask_app, monkeypatch):
        app, _ = flask_app
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio",
                            lambda: {"positions": [], "aggregates": {}})
        with app.test_client() as c:
            r = c.get("/api/v1/portfolio/risk")
            assert r.get_json()["ok"] is True


# ── API: GET /decisions/size-check ────────────────────────────────────────────

class TestApiSizeCheck:
    def test_missing_ticker_returns_400(self, flask_app):
        app, _ = flask_app
        with app.test_client() as c:
            r = c.get("/api/v1/decisions/size-check")
            assert r.status_code == 400

    def test_valid_ticker_returns_200(self, flask_app, monkeypatch):
        app, _ = flask_app
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio",
                            lambda: {"positions": [], "aggregates": {}})
        with app.test_client() as c:
            r = c.get("/api/v1/decisions/size-check?ticker=NVDA")
            assert r.status_code == 200

    def test_response_has_sizing_guidance(self, flask_app, monkeypatch):
        app, _ = flask_app
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio",
                            lambda: {"positions": [],
                                     "aggregates": {"cash": 20_000,
                                                     "total_portfolio_value": 100_000}})
        with app.test_client() as c:
            r = c.get("/api/v1/decisions/size-check?ticker=NVDA&decision_type=ENTER")
            data = r.get_json()["data"]
            assert "sizing_guidance" in data
            assert "checklist_item_suggestions" in data

    def test_no_auth_required(self, flask_app, monkeypatch):
        app, _ = flask_app
        import portfolio_reconciliation as pr
        monkeypatch.setattr(pr, "get_canonical_portfolio",
                            lambda: {"positions": [], "aggregates": {}})
        with app.test_client() as c:
            r = c.get("/api/v1/decisions/size-check?ticker=QQQ")
            assert r.status_code == 200
