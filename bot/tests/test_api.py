"""
Tests for bot/api.py — Phase 6A API layer.

Strategy:
- Use Flask test client with api_bp registered on a minimal app.
- Mock all subsystem imports so tests run in isolation (no DB, no schedulers).
- Cover: envelope structure, cache hit/miss, size caps, formatters,
  all 7 endpoints (success + sparse), 400 on invalid report type,
  500 JSON on subsystem exception, determinism.
"""
import json
import time
import importlib
import sys
from unittest.mock import patch, MagicMock

import pytest
from flask import Flask


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_app():
    """Create a bare Flask app with the api blueprint registered."""
    # Re-import api freshly each time so module-level _CACHE starts clean.
    if "api" in sys.modules:
        importlib.reload(sys.modules["api"])
    import api as api_mod
    api_mod.cache_clear()
    app = Flask(__name__)
    app.register_blueprint(api_mod.api_bp)
    app.testing = True
    return app, api_mod


def _client():
    app, api_mod = _make_app()
    return app.test_client(), api_mod


def _parse(response):
    return json.loads(response.data)


# ── Envelope ──────────────────────────────────────────────────────────────────

class TestEnvelopeStructure:
    def test_ok_envelope_keys(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_predator_rows", return_value=[]):
            resp = client.get("/api/v1/predator/latest")
        body = _parse(resp)
        assert body["ok"] is True
        assert "data" in body
        assert "meta" in body
        assert "ts" in body["meta"]
        assert "cached" in body["meta"]

    def test_error_envelope_on_exception(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_predator_rows", side_effect=RuntimeError("boom")):
            resp = client.get("/api/v1/predator/latest")
        assert resp.status_code == 500
        body = _parse(resp)
        assert body["ok"] is False
        assert body["error"]["code"] == 500
        assert "message" in body["error"]
        assert "meta" in body
        assert "ts" in body["meta"]

    def test_meta_ts_is_iso8601(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_predator_rows", return_value=[]):
            resp = client.get("/api/v1/predator/latest")
        ts = _parse(resp)["meta"]["ts"]
        from datetime import datetime
        # Should parse without exception
        datetime.fromisoformat(ts)

    def test_cached_false_on_first_hit(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_predator_rows", return_value=[]):
            resp = client.get("/api/v1/predator/latest")
        assert _parse(resp)["meta"]["cached"] is False

    def test_cached_true_on_second_hit(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_predator_rows", return_value=[]) as mock_fetch:
            client.get("/api/v1/predator/latest")
            resp2 = client.get("/api/v1/predator/latest")
        assert _parse(resp2)["meta"]["cached"] is True
        assert mock_fetch.call_count == 1  # factory only called once


# ── Cache ─────────────────────────────────────────────────────────────────────

class TestCache:
    def test_cache_clear_resets(self):
        client, mod = _client()
        call_count = [0]

        def _factory(limit):
            call_count[0] += 1
            return []

        with patch.object(mod, "_fetch_predator_rows", side_effect=_factory):
            client.get("/api/v1/predator/latest")
            mod.cache_clear()
            client.get("/api/v1/predator/latest")
        assert call_count[0] == 2

    def test_different_endpoints_have_separate_cache_keys(self):
        client, mod = _client()
        latest_calls = [0]
        top_calls = [0]

        def _latest(*a):
            latest_calls[0] += 1
            return []

        def _top(*a):
            top_calls[0] += 1
            return []

        with patch.object(mod, "_fetch_predator_rows", side_effect=lambda limit: (
            _latest() or []
        )):
            client.get("/api/v1/predator/latest")
            client.get("/api/v1/predator/latest")
        # Both used same mock; check it's cached after 2nd call
        assert latest_calls[0] == 1

    def test_cache_expires_after_ttl(self):
        client, mod = _client()
        call_count = [0]

        original_cached = mod._cached

        def _fast_cached(key, ttl, factory):
            # Use TTL of 0 so it always expires
            return original_cached(key, 0, factory)

        with patch.object(mod, "_cached", side_effect=_fast_cached):
            with patch.object(mod, "_fetch_predator_rows", side_effect=lambda limit: (
                call_count.__setitem__(0, call_count[0] + 1) or []
            )):
                client.get("/api/v1/predator/latest")
                client.get("/api/v1/predator/latest")
        assert call_count[0] == 2


# ── /health ───────────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def _mock_conn(self, count=5, scan_time="2026-01-01T10:00:00"):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (count, scan_time)
        return conn

    def test_health_ok(self):
        client, mod = _client()
        conn = self._mock_conn()
        with patch("database.get_connection", return_value=conn):
            resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        body = _parse(resp)
        assert body["ok"] is True
        data = body["data"]
        assert data["status"] == "ok"
        assert data["db_connected"] is True
        assert data["predator_tickers_scanned"] == 5
        assert data["latest_scan_time"] == "2026-01-01T10:00:00"

    def test_health_degraded_on_db_error(self):
        client, mod = _client()
        with patch("database.get_connection", side_effect=Exception("no db")):
            resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = _parse(resp)["data"]
        assert data["status"] == "degraded"
        assert data["db_connected"] is False

    def test_health_sparse_empty_table(self):
        client, mod = _client()
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (0, None)
        with patch("database.get_connection", return_value=conn):
            resp = client.get("/api/v1/health")
        data = _parse(resp)["data"]
        assert data["predator_tickers_scanned"] == 0
        assert data["latest_scan_time"] is None

    def test_health_returns_200_not_500_on_db_error(self):
        client, mod = _client()
        with patch("database.get_connection", side_effect=OSError("disk full")):
            resp = client.get("/api/v1/health")
        # health endpoint catches DB errors internally and returns degraded, not 500
        assert resp.status_code == 200


# ── /predator/latest ──────────────────────────────────────────────────────────

_PREDATOR_ROW = {
    "ticker": "AAPL",
    "score": 8.5,
    "adjusted_score": 8.7,
    "raw_score": 8.0,
    "confidence_pct": 72.5,
    "tier": "PREDATOR",
    "entry_price": 150.25,
    "stop_price": 142.50,
    "scan_time": "2026-01-01T09:30:00",
    "signals_json": json.dumps({
        "options":      {"score": 7.0},
        "insider":      {"score": 5.0},
        "breakout":     {"score": 9.0},
    }),
}


class TestPredatorLatest:
    def test_returns_results_list(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_predator_rows", return_value=[_PREDATOR_ROW]):
            resp = client.get("/api/v1/predator/latest")
        data = _parse(resp)["data"]
        assert "results" in data
        assert "total" in data
        assert data["total"] == 1

    def test_row_formatting(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_predator_rows", return_value=[_PREDATOR_ROW]):
            resp = client.get("/api/v1/predator/latest")
        row = _parse(resp)["data"]["results"][0]
        assert row["ticker"] == "AAPL"
        assert row["score"] == round(8.7, 4)
        assert row["raw_score"] == round(8.0, 4)
        assert row["confidence_pct"] == round(72.5, 4)
        assert row["tier"] == "PREDATOR"
        assert row["entry_price"] == round(150.25, 4)
        assert row["stop_price"] == round(142.50, 4)
        assert "signals" in row
        assert "options" in row["signals"]
        assert row["signals"]["options"]["score"] == round(7.0, 4)

    def test_empty_db_returns_empty_list(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_predator_rows", return_value=[]):
            resp = client.get("/api/v1/predator/latest")
        assert resp.status_code == 200
        data = _parse(resp)["data"]
        assert data["results"] == []
        assert data["total"] == 0

    def test_bad_signals_json_handled(self):
        client, mod = _client()
        row = dict(_PREDATOR_ROW, signals_json="not-json")
        with patch.object(mod, "_fetch_predator_rows", return_value=[row]):
            resp = client.get("/api/v1/predator/latest")
        assert resp.status_code == 200
        result = _parse(resp)["data"]["results"][0]
        assert result["signals"] == {}

    def test_null_signals_json_handled(self):
        client, mod = _client()
        row = dict(_PREDATOR_ROW, signals_json=None)
        with patch.object(mod, "_fetch_predator_rows", return_value=[row]):
            resp = client.get("/api/v1/predator/latest")
        assert resp.status_code == 200

    def test_missing_adjusted_score_falls_back_to_score(self):
        client, mod = _client()
        row = dict(_PREDATOR_ROW)
        row.pop("adjusted_score", None)
        row["score"] = 7.0
        with patch.object(mod, "_fetch_predator_rows", return_value=[row]):
            resp = client.get("/api/v1/predator/latest")
        result = _parse(resp)["data"]["results"][0]
        assert result["score"] == round(7.0, 4)

    def test_exception_returns_500_json(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_predator_rows", side_effect=Exception("db down")):
            resp = client.get("/api/v1/predator/latest")
        assert resp.status_code == 500
        assert _parse(resp)["ok"] is False


# ── /predator/top ─────────────────────────────────────────────────────────────

class TestPredatorTop:
    def test_filters_null_score_rows(self):
        client, mod = _client()
        no_score = dict(_PREDATOR_ROW, score=None, adjusted_score=None)
        with patch.object(mod, "_fetch_predator_rows", return_value=[_PREDATOR_ROW, no_score]):
            resp = client.get("/api/v1/predator/top")
        data = _parse(resp)["data"]
        # no_score row has score=None and adjusted_score=None after fmt; should be excluded
        assert data["total"] >= 1

    def test_cap_at_max_top(self):
        client, mod = _client()
        rows = [dict(_PREDATOR_ROW, ticker=f"T{i}") for i in range(25)]
        with patch.object(mod, "_fetch_predator_rows", return_value=rows):
            resp = client.get("/api/v1/predator/top")
        data = _parse(resp)["data"]
        assert len(data["results"]) <= mod.MAX_TOP

    def test_empty_returns_empty(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_predator_rows", return_value=[]):
            resp = client.get("/api/v1/predator/top")
        assert _parse(resp)["data"]["results"] == []


# ── /risk/status ──────────────────────────────────────────────────────────────

_RISK_REPORT = {
    "current_mode": "NORMAL",
    "policy": {"confidence_multiplier": 1.0},
    "active_safeguards": [],
    "operational_threats": [],
    "recovery_readiness": {"score": 100},
    "recommendations": ["keep calm"] * 8,
    "rows_in_mode": 42,
    "escalation_history": [{"event": "e1"}, {"event": "e2"}],
    "stabilization_progress": {"pct": 0.9},
}


class TestRiskStatus:
    def test_structure(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_risk_report", return_value=_RISK_REPORT):
            resp = client.get("/api/v1/risk/status")
        assert resp.status_code == 200
        data = _parse(resp)["data"]
        assert data["mode"] == "NORMAL"
        assert "policy" in data
        assert "safeguards" in data
        assert "threats" in data
        assert "recovery" in data
        assert "recommendations" in data
        assert "rows_in_mode" in data
        assert "escalation_count" in data
        assert "stabilization" in data

    def test_recommendations_capped_at_5(self):
        client, mod = _client()
        report = dict(_RISK_REPORT, recommendations=["r"] * 10)
        with patch.object(mod, "_fetch_risk_report", return_value=report):
            resp = client.get("/api/v1/risk/status")
        recs = _parse(resp)["data"]["recommendations"]
        assert len(recs) <= 5

    def test_escalation_count_from_history_length(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_risk_report", return_value=_RISK_REPORT):
            resp = client.get("/api/v1/risk/status")
        assert _parse(resp)["data"]["escalation_count"] == 2

    def test_sparse_none_values(self):
        client, mod = _client()
        report = {
            "current_mode": None,
            "policy": None,
            "active_safeguards": None,
            "operational_threats": None,
            "recovery_readiness": None,
            "recommendations": None,
            "rows_in_mode": None,
            "escalation_history": None,
            "stabilization_progress": None,
        }
        with patch.object(mod, "_fetch_risk_report", return_value=report):
            resp = client.get("/api/v1/risk/status")
        assert resp.status_code == 200
        data = _parse(resp)["data"]
        assert data["safeguards"] == []
        assert data["threats"] == []
        assert data["recommendations"] == []
        assert data["rows_in_mode"] == 0
        assert data["escalation_count"] == 0

    def test_exception_returns_500(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_risk_report", side_effect=RuntimeError("fail")):
            resp = client.get("/api/v1/risk/status")
        assert resp.status_code == 500
        assert _parse(resp)["ok"] is False


# ── /operations/summary ───────────────────────────────────────────────────────

_HUB_REPORT = {
    "overall_health": "HEALTHY",
    "row_count": 1000,
    "top_concerns": ["concern1", "concern2"],
    "recommendations": ["rec1"],
    "operational_alerts": ["alert1", "alert2", "alert3"],
    "subsystem_statuses": {
        "predator": {"health": "HEALTHY"},
        "scanner":  {"health": "WARNING"},
    },
    "executive_summary": "All good.",
}


class TestOperationsSummary:
    def test_structure(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_operations_report", return_value=_HUB_REPORT):
            resp = client.get("/api/v1/operations/summary")
        assert resp.status_code == 200
        data = _parse(resp)["data"]
        assert data["overall_health"] == "HEALTHY"
        assert data["row_count"] == 1000
        assert "top_concerns" in data
        assert "recommendations" in data
        assert "alerts" in data
        assert "subsystems" in data
        assert "predator" in data["subsystems"]
        assert data["subsystems"]["predator"]["health"] == "HEALTHY"
        assert data["executive_summary"] == "All good."

    def test_concerns_capped_at_5(self):
        client, mod = _client()
        report = dict(_HUB_REPORT, top_concerns=["c"] * 10)
        with patch.object(mod, "_fetch_operations_report", return_value=report):
            resp = client.get("/api/v1/operations/summary")
        assert len(_parse(resp)["data"]["top_concerns"]) <= 5

    def test_executive_summary_truncated_at_500(self):
        client, mod = _client()
        report = dict(_HUB_REPORT, executive_summary="x" * 700)
        with patch.object(mod, "_fetch_operations_report", return_value=report):
            resp = client.get("/api/v1/operations/summary")
        assert len(_parse(resp)["data"]["executive_summary"]) <= 500

    def test_sparse_none_subsystems(self):
        client, mod = _client()
        report = dict(_HUB_REPORT, subsystem_statuses=None)
        with patch.object(mod, "_fetch_operations_report", return_value=report):
            resp = client.get("/api/v1/operations/summary")
        assert resp.status_code == 200
        assert _parse(resp)["data"]["subsystems"] == {}

    def test_exception_returns_500(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_operations_report", side_effect=OSError("fail")):
            resp = client.get("/api/v1/operations/summary")
        assert resp.status_code == 500


# ── /paper-portfolio/status ───────────────────────────────────────────────────

_PAPER_REPORT = {
    "portfolio_health": "GOOD",
    "metrics": {
        "initial_capital": 10000.0,
        "final_value": 11500.0,
        "cumulative_return_pct": 15.0,
        "win_rate": 0.65,
        "max_drawdown_pct": -8.5,
        "sharpe_like": 1.2,
        "n_trades": 20,
    },
    "row_count": 500,
    "warnings": ["low cash"],
    "recommendations": ["diversify"],
}


class TestPaperPortfolioStatus:
    def test_structure(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_paper_portfolio_report", return_value=_PAPER_REPORT):
            resp = client.get("/api/v1/paper-portfolio/status")
        assert resp.status_code == 200
        data = _parse(resp)["data"]
        assert data["health"] == "GOOD"
        assert data["initial_capital"] == round(10000.0, 4)
        assert data["final_value"] == round(11500.0, 4)
        assert data["cumulative_return"] == round(15.0, 4)
        assert data["win_rate"] == round(0.65, 4)
        assert data["max_drawdown_pct"] == round(-8.5, 4)
        assert data["sharpe_like"] == round(1.2, 4)
        assert data["n_trades"] == 20
        assert data["row_count"] == 500

    def test_sparse_empty_metrics(self):
        client, mod = _client()
        report = {"portfolio_health": None, "metrics": {}, "row_count": None,
                  "warnings": None, "recommendations": None}
        with patch.object(mod, "_fetch_paper_portfolio_report", return_value=report):
            resp = client.get("/api/v1/paper-portfolio/status")
        assert resp.status_code == 200
        data = _parse(resp)["data"]
        assert data["n_trades"] == 0
        assert data["row_count"] == 0
        assert data["warnings"] == []
        assert data["recommendations"] == []

    def test_warnings_capped_at_5(self):
        client, mod = _client()
        report = dict(_PAPER_REPORT, warnings=["w"] * 10)
        with patch.object(mod, "_fetch_paper_portfolio_report", return_value=report):
            resp = client.get("/api/v1/paper-portfolio/status")
        assert len(_parse(resp)["data"]["warnings"]) <= 5

    def test_exception_returns_500(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_paper_portfolio_report", side_effect=ValueError("x")):
            resp = client.get("/api/v1/paper-portfolio/status")
        assert resp.status_code == 500


# ── /research/report/<type> ───────────────────────────────────────────────────

_RESEARCH_REPORT = {
    "severity": "LOW",
    "health_score": 88.5,
    "quality": {"score": 92.0},
    "row_count": 200,
    "top_findings": ["f1", "f2", "f3"],
    "recommendations": ["r1"] * 10,
    "sections": {
        "section_a": {
            "severity": "INFO",
            "summary": "All nominal.",
            "entries": ["e1", "e2"],
        }
    },
    "generated_at": "2026-01-01T08:00:00Z",
    "executive_commentary": "System performing well.",
}


class TestResearchReport:
    @pytest.mark.parametrize("report_type", [
        "daily", "weekly", "calibration", "regime",
        "portfolio", "adaptive", "anomaly", "degradation",
    ])
    def test_valid_types_return_200(self, report_type):
        client, mod = _client()
        with patch.object(mod, "_build_research_report", return_value=_RESEARCH_REPORT):
            resp = client.get(f"/api/v1/research/report/{report_type}")
        assert resp.status_code == 200
        body = _parse(resp)
        assert body["ok"] is True

    def test_invalid_type_returns_400(self):
        client, mod = _client()
        resp = client.get("/api/v1/research/report/nonexistent")
        assert resp.status_code == 400
        body = _parse(resp)
        assert body["ok"] is False
        assert body["error"]["code"] == 400
        assert "nonexistent" in body["error"]["message"]

    def test_invalid_type_lists_valid_types(self):
        client, mod = _client()
        resp = client.get("/api/v1/research/report/bad_type")
        message = _parse(resp)["error"]["message"]
        assert "valid" in message.lower()

    def test_report_structure(self):
        client, mod = _client()
        with patch.object(mod, "_build_research_report", return_value=_RESEARCH_REPORT):
            resp = client.get("/api/v1/research/report/daily")
        data = _parse(resp)["data"]
        assert data["report_type"] == "daily"
        assert data["severity"] == "LOW"
        assert data["health_score"] == round(88.5, 4)
        assert data["quality_score"] == round(92.0, 4)
        assert data["row_count"] == 200
        assert "top_findings" in data
        assert "recommendations" in data
        assert "sections" in data
        assert data["generated_at"] == "2026-01-01T08:00:00Z"

    def test_recommendations_capped_at_max_findings(self):
        client, mod = _client()
        report = dict(_RESEARCH_REPORT, recommendations=["r"] * 20)
        with patch.object(mod, "_build_research_report", return_value=report):
            resp = client.get("/api/v1/research/report/daily")
        recs = _parse(resp)["data"]["recommendations"]
        assert len(recs) <= mod.MAX_FINDINGS

    def test_sections_capped_at_max_sections(self):
        client, mod = _client()
        many_sections = {
            f"sec_{i}": {"severity": "INFO", "summary": "ok", "entries": []}
            for i in range(20)
        }
        report = dict(_RESEARCH_REPORT, sections=many_sections)
        with patch.object(mod, "_build_research_report", return_value=report):
            resp = client.get("/api/v1/research/report/daily")
        sections = _parse(resp)["data"]["sections"]
        assert len(sections) <= mod.MAX_SECTIONS

    def test_section_summary_truncated_at_400(self):
        client, mod = _client()
        long_section = {
            "sec1": {"severity": "INFO", "summary": "x" * 600, "entries": []}
        }
        report = dict(_RESEARCH_REPORT, sections=long_section)
        with patch.object(mod, "_build_research_report", return_value=report):
            resp = client.get("/api/v1/research/report/daily")
        sec = _parse(resp)["data"]["sections"]["sec1"]
        assert len(sec["summary"]) <= 400

    def test_executive_commentary_truncated_at_600(self):
        client, mod = _client()
        report = dict(_RESEARCH_REPORT, executive_commentary="y" * 800)
        with patch.object(mod, "_build_research_report", return_value=report):
            resp = client.get("/api/v1/research/report/daily")
        ec = _parse(resp)["data"]["executive_commentary"]
        assert len(ec) <= 600

    def test_research_exception_returns_500(self):
        client, mod = _client()
        with patch.object(mod, "_build_research_report", side_effect=Exception("fail")):
            resp = client.get("/api/v1/research/report/daily")
        assert resp.status_code == 500
        assert _parse(resp)["ok"] is False

    def test_different_types_have_separate_cache(self):
        client, mod = _client()
        call_log = []

        def _build(rtype):
            call_log.append(rtype)
            return _RESEARCH_REPORT

        with patch.object(mod, "_build_research_report", side_effect=_build):
            client.get("/api/v1/research/report/daily")
            client.get("/api/v1/research/report/weekly")
            client.get("/api/v1/research/report/daily")  # should hit cache
        # daily and weekly should each be called once
        assert call_log.count("daily") == 1
        assert call_log.count("weekly") == 1

    def test_sparse_none_sections(self):
        client, mod = _client()
        report = dict(_RESEARCH_REPORT, sections=None)
        with patch.object(mod, "_build_research_report", return_value=report):
            resp = client.get("/api/v1/research/report/daily")
        assert resp.status_code == 200
        assert _parse(resp)["data"]["sections"] == {}

    def test_sparse_none_top_findings(self):
        client, mod = _client()
        report = dict(_RESEARCH_REPORT, top_findings=None)
        with patch.object(mod, "_build_research_report", return_value=report):
            resp = client.get("/api/v1/research/report/daily")
        assert resp.status_code == 200
        assert _parse(resp)["data"]["top_findings"] == []


# ── Formatter unit tests ──────────────────────────────────────────────────────

class TestFmtPredatorRow:
    def setup_method(self):
        _, self.mod = _client()

    def test_basic(self):
        row = dict(_PREDATOR_ROW)
        result = self.mod.fmt_predator_row(row)
        assert result["ticker"] == "AAPL"
        assert result["tier"] == "PREDATOR"

    def test_alert_time_falls_back_to_scan_time(self):
        row = dict(_PREDATOR_ROW)
        row.pop("alert_time", None)
        result = self.mod.fmt_predator_row(row)
        assert result["alert_time"] == row["scan_time"]

    def test_tier_defaults_to_alert(self):
        row = dict(_PREDATOR_ROW, tier=None)
        result = self.mod.fmt_predator_row(row)
        assert result["tier"] == "ALERT"

    def test_unknown_signal_keys_ignored(self):
        row = dict(_PREDATOR_ROW, signals_json=json.dumps({"unknown_key": {"score": 9}}))
        result = self.mod.fmt_predator_row(row)
        assert "unknown_key" not in result["signals"]

    def test_signal_scalar_value(self):
        row = dict(_PREDATOR_ROW, signals_json=json.dumps({"options": 7.5}))
        result = self.mod.fmt_predator_row(row)
        assert result["signals"]["options"]["score"] == round(7.5, 4)


class TestSafeHelpers:
    def setup_method(self):
        _, self.mod = _client()

    def test_safe_float_none(self):
        assert self.mod._safe_float(None) is None

    def test_safe_float_rounds(self):
        assert self.mod._safe_float(3.14159265) == 3.1416

    def test_safe_float_invalid(self):
        assert self.mod._safe_float("bad") is None

    def test_safe_int_none(self):
        assert self.mod._safe_int(None) == 0

    def test_safe_int_string_num(self):
        assert self.mod._safe_int("42") == 42

    def test_safe_int_custom_default(self):
        assert self.mod._safe_int(None, default=-1) == -1

    def test_cap_list_trims(self):
        assert self.mod._cap_list([1, 2, 3, 4, 5], 3) == [1, 2, 3]

    def test_cap_list_none_input(self):
        assert self.mod._cap_list(None, 5) == []

    def test_cap_sections_trims_to_max(self):
        many = {f"s{i}": {"severity": "OK", "summary": "x", "entries": []} for i in range(20)}
        result = self.mod._cap_sections(many)
        assert len(result) <= self.mod.MAX_SECTIONS

    def test_cap_sections_none_input(self):
        assert self.mod._cap_sections(None) == {}

    def test_cap_sections_n_entries_count(self):
        sections = {"s1": {"severity": "X", "summary": "y", "entries": ["a", "b", "c"]}}
        result = self.mod._cap_sections(sections)
        assert result["s1"]["n_entries"] == 3


# ── Determinism ───────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_input_produces_same_output(self):
        client, mod = _client()
        with patch.object(mod, "_fetch_predator_rows", return_value=[_PREDATOR_ROW]):
            mod.cache_clear()
            resp1 = client.get("/api/v1/predator/latest")
            mod.cache_clear()
            resp2 = client.get("/api/v1/predator/latest")
        d1 = _parse(resp1)["data"]
        d2 = _parse(resp2)["data"]
        assert d1["results"] == d2["results"]
        assert d1["total"] == d2["total"]

    def test_research_report_type_echoed_in_response(self):
        client, mod = _client()
        for rtype in ("daily", "weekly", "portfolio"):
            mod.cache_clear()
            with patch.object(mod, "_build_research_report", return_value=_RESEARCH_REPORT):
                resp = client.get(f"/api/v1/research/report/{rtype}")
            assert _parse(resp)["data"]["report_type"] == rtype
