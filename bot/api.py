"""
App-facing API layer — Phase 6A.

Flask Blueprint at /api/v1.  JSON-only responses with a stable envelope:

  Success:  {"ok": true,  "data": {...},               "meta": {"ts": str, "cached": bool}}
  Error:    {"ok": false, "error": {"code": int, "message": str}, "meta": {"ts": str}}

Design rules
------------
• Never expose raw DB rows — all data passes through a formatter function.
• Enforce response size caps (MAX_ALERTS, MAX_TOP, MAX_ENTRIES, MAX_SECTIONS).
• Deterministic ordering: score DESC, then ticker ASC as tie-breaker.
• Graceful sparse-data: endpoints return {"ok": true, "data": <empty payload>}
  rather than 500 when the DB is empty or a subsystem returns None.
• Lightweight per-endpoint TTL cache (thread-safe for CPython/single-worker).
• No auth on read endpoints (Phase 6A scope).
• No hidden mutations — endpoints are read-only.
"""
import json
import logging
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Optional

from flask import Blueprint, jsonify

log = logging.getLogger(__name__)

api_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

# ── Size caps ─────────────────────────────────────────────────────────────────

MAX_ALERTS   = 50    # predator/latest row cap
MAX_TOP      = 20    # predator/top result cap
MAX_SECTIONS = 10    # research report section cap
MAX_ENTRIES  =  8    # entries per section cap
MAX_FINDINGS =  8    # top_findings / recommendations cap

# ── Cache TTLs (seconds) ──────────────────────────────────────────────────────

TTL_HEALTH     =  30
TTL_PREDATOR   =  60
TTL_RISK       =  30
TTL_OPERATIONS = 120
TTL_PAPER      =  60
TTL_RESEARCH   = 300

# ── Valid research report type identifiers ────────────────────────────────────

VALID_REPORT_TYPES = frozenset({
    "daily", "weekly", "calibration", "regime",
    "portfolio", "adaptive", "anomaly", "degradation",
})

# ── In-process cache ──────────────────────────────────────────────────────────
# {key: (monotonic_timestamp, payload_dict)}
# CPython dict writes are GIL-protected; safe for 1-worker gunicorn.

_CACHE: dict = {}


def _ts_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _ok(data: Any, cached: bool = False) -> Any:
    return jsonify({"ok": True, "data": data, "meta": {"ts": _ts_now(), "cached": cached}})


def _err(message: str, code: int = 500) -> Any:
    return jsonify({"ok": False, "error": {"code": code, "message": message},
                    "meta": {"ts": _ts_now()}}), code


def _cached(key: str, ttl: float, factory):
    """Return (payload, was_cached).  factory() must return a JSON-serialisable dict."""
    now   = time.monotonic()
    entry = _CACHE.get(key)
    if entry and (now - entry[0]) < ttl:
        return entry[1], True
    result       = factory()
    _CACHE[key]  = (now, result)
    return result, False


def cache_clear() -> None:
    """Flush the in-process cache (used by tests)."""
    _CACHE.clear()


# ── Formatters ────────────────────────────────────────────────────────────────

def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _cap_list(items: list, limit: int) -> list:
    return (items or [])[:limit]


def _cap_sections(sections: dict) -> dict:
    """Trim sections dict to MAX_SECTIONS; cap entries within each section."""
    result = {}
    for name, sec in list((sections or {}).items())[:MAX_SECTIONS]:
        result[name] = {
            "severity":  sec.get("severity"),
            "summary":   (sec.get("summary") or "")[:400],
            "n_entries": len(sec.get("entries") or []),
        }
    return result


def fmt_predator_row(row: dict) -> dict:
    """Serialise one predator_latest / predator_alerts DB row for API responses."""
    signals_raw = {}
    try:
        raw = row.get("signals_json")
        signals_raw = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        pass

    per_signal = {}
    for sig in ("options", "insider", "short_squeeze", "catalyst", "institutional", "breakout"):
        val = signals_raw.get(sig)
        if val is None:
            continue
        score = val.get("score", 0) if isinstance(val, dict) else float(val or 0)
        per_signal[sig] = {"score": _safe_float(score)}

    return {
        "ticker":         row.get("ticker", ""),
        "score":          _safe_float(row.get("adjusted_score") or row.get("score")),
        "raw_score":      _safe_float(row.get("raw_score")),
        "confidence_pct": _safe_float(row.get("confidence_pct")),
        "tier":           row.get("tier") or "ALERT",
        "entry_price":    _safe_float(row.get("entry_price")),
        "stop_price":     _safe_float(row.get("stop_price")),
        "signals":        per_signal,
        "alert_time":     row.get("alert_time") or row.get("scan_time"),
    }


def fmt_risk_report(risk_report: dict) -> dict:
    """Serialise a dynamic_risk.generate_report() result for the API."""
    return {
        "mode":             risk_report.get("current_mode"),
        "policy":           risk_report.get("policy") or {},
        "safeguards":       risk_report.get("active_safeguards") or [],
        "threats":          risk_report.get("operational_threats") or [],
        "recovery":         risk_report.get("recovery_readiness") or {},
        "recommendations":  _cap_list(risk_report.get("recommendations"), 5),
        "rows_in_mode":     _safe_int(risk_report.get("rows_in_mode")),
        "escalation_count": len(risk_report.get("escalation_history") or []),
        "stabilization":    risk_report.get("stabilization_progress") or {},
    }


def fmt_operations_summary(hub_report: dict) -> dict:
    """Serialise an operations_hub.generate_report() result for the API."""
    statuses = hub_report.get("subsystem_statuses") or {}
    return {
        "overall_health":  hub_report.get("overall_health"),
        "row_count":       _safe_int(hub_report.get("row_count")),
        "top_concerns":    _cap_list(hub_report.get("top_concerns"), 5),
        "recommendations": _cap_list(hub_report.get("recommendations"), 5),
        "alerts":          _cap_list(hub_report.get("operational_alerts"), 5),
        "subsystems": {
            name: {
                "health":  s.get("health"),
            }
            for name, s in statuses.items()
        },
        "executive_summary": (hub_report.get("executive_summary") or "")[:500],
    }


def fmt_paper_portfolio(paper_report: dict) -> dict:
    """Serialise a paper_portfolio.generate_report() result for the API."""
    metrics = paper_report.get("metrics") or {}
    return {
        "health":              paper_report.get("portfolio_health"),
        "initial_capital":     _safe_float(metrics.get("initial_capital")),
        "final_value":         _safe_float(metrics.get("final_value")),
        "cumulative_return":   _safe_float(metrics.get("cumulative_return_pct")),
        "win_rate":            _safe_float(metrics.get("win_rate")),
        "max_drawdown_pct":    _safe_float(metrics.get("max_drawdown_pct")),
        "sharpe_like":         _safe_float(metrics.get("sharpe_like")),
        "n_trades":            _safe_int(metrics.get("n_trades")),
        "row_count":           _safe_int(paper_report.get("row_count")),
        "warnings":            _cap_list(paper_report.get("warnings"), 5),
        "recommendations":     _cap_list(paper_report.get("recommendations"), 5),
    }


def fmt_research_report(report: dict, report_type: str) -> dict:
    """Serialise a research_reports.*_report() result for the API."""
    return {
        "report_type":     report_type,
        "severity":        report.get("severity"),
        "health_score":    _safe_float(report.get("health_score")),
        "quality_score":   _safe_float((report.get("quality") or {}).get("score")),
        "row_count":       _safe_int(report.get("row_count")),
        "top_findings":    _cap_list(report.get("top_findings"), MAX_FINDINGS),
        "recommendations": _cap_list(report.get("recommendations"), MAX_FINDINGS),
        "sections":        _cap_sections(report.get("sections") or {}),
        "generated_at":    report.get("generated_at"),
        "executive_commentary": (report.get("executive_commentary") or "")[:600],
    }


# ── Endpoint helpers ──────────────────────────────────────────────────────────

def _fetch_predator_rows(limit: int = MAX_ALERTS) -> list:
    from database import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT ticker, score, adjusted_score, raw_score, confidence_pct, tier, "
            "signals_json, entry_price, stop_price, scan_time "
            "FROM predator_latest ORDER BY COALESCE(adjusted_score, score) DESC, ticker ASC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _fetch_operations_report() -> dict:
    from operations_hub import generate_report as hub_report
    return hub_report()


def _fetch_paper_portfolio_report() -> dict:
    from paper_portfolio import generate_report as pp_report
    return pp_report()


def _fetch_risk_report() -> dict:
    from dynamic_risk import create_risk_state, evaluate_risk_inputs, generate_report as dr_report
    from operations_hub import generate_report as hub_report
    try:
        hub = hub_report()
    except Exception:
        hub = {}
    ri    = evaluate_risk_inputs(hub_report=hub)
    state = create_risk_state()
    return dr_report(state, ri)


def _build_research_report(report_type: str) -> dict:
    import research_reports as rr
    _map = {
        "daily":       rr.daily_operational_report,
        "weekly":      rr.weekly_performance_report,
        "calibration": rr.calibration_research_report,
        "regime":      rr.regime_research_report,
        "portfolio":   rr.portfolio_research_report,
        "adaptive":    rr.adaptive_recommendation_report,
        "anomaly":     rr.anomaly_research_report,
        "degradation": rr.degradation_research_report,
    }
    fn = _map.get(report_type)
    if fn is None:
        raise KeyError(report_type)
    return fn()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@api_bp.route("/health", methods=["GET"])
def health():
    """
    System health summary.
    Checks DB connectivity and reports predator scan staleness.
    Cached for TTL_HEALTH seconds.
    """
    try:
        def _build():
            from database import get_connection
            db_ok          = False
            predator_count = 0
            latest_scan    = None
            try:
                conn = get_connection()
                row  = conn.execute(
                    "SELECT COUNT(*), MAX(scan_time) FROM predator_latest"
                ).fetchone()
                conn.close()
                db_ok          = True
                predator_count = _safe_int(row[0]) if row else 0
                latest_scan    = row[1] if row else None
            except Exception as exc:
                log.warning("health: DB check failed — %s", exc)

            return {
                "status":                  "ok" if db_ok else "degraded",
                "db_connected":            db_ok,
                "predator_tickers_scanned": predator_count,
                "latest_scan_time":        latest_scan,
            }

        payload, cached = _cached("health", TTL_HEALTH, _build)
        return _ok(payload, cached)

    except Exception:
        log.error("GET /health error:\n%s", traceback.format_exc())
        return _err("health check failed")


@api_bp.route("/predator/latest", methods=["GET"])
def predator_latest():
    """
    Most-recent scan result per ticker from predator_latest.
    Ordered by score DESC; capped at MAX_ALERTS rows.
    Cached for TTL_PREDATOR seconds.
    """
    try:
        def _build():
            rows    = _fetch_predator_rows(MAX_ALERTS)
            results = [fmt_predator_row(r) for r in rows]
            return {"results": results, "total": len(results)}

        payload, cached = _cached("predator:latest", TTL_PREDATOR, _build)
        return _ok(payload, cached)

    except Exception:
        log.error("GET /predator/latest error:\n%s", traceback.format_exc())
        return _err("failed to fetch predator scan results")


@api_bp.route("/predator/top", methods=["GET"])
def predator_top():
    """
    Top-N tickers from the latest scan (default 10, max MAX_TOP).
    Only returns rows with a non-null score.
    Cached for TTL_PREDATOR seconds.
    """
    try:
        def _build():
            rows    = _fetch_predator_rows(MAX_TOP)
            results = [fmt_predator_row(r) for r in rows if r.get("score") is not None
                       or r.get("adjusted_score") is not None]
            return {"results": results[:MAX_TOP], "total": len(results)}

        payload, cached = _cached("predator:top", TTL_PREDATOR, _build)
        return _ok(payload, cached)

    except Exception:
        log.error("GET /predator/top error:\n%s", traceback.format_exc())
        return _err("failed to fetch top predator results")


@api_bp.route("/risk/status", methods=["GET"])
def risk_status():
    """
    Current dynamic risk management mode and active safeguards.
    Evaluates live inputs from operations hub; cached for TTL_RISK seconds.
    """
    try:
        def _build():
            report = _fetch_risk_report()
            return fmt_risk_report(report)

        payload, cached = _cached("risk:status", TTL_RISK, _build)
        return _ok(payload, cached)

    except Exception:
        log.error("GET /risk/status error:\n%s", traceback.format_exc())
        return _err("failed to compute risk status")


@api_bp.route("/operations/summary", methods=["GET"])
def operations_summary():
    """
    Operations hub health summary across all subsystems.
    Cached for TTL_OPERATIONS seconds.
    """
    try:
        def _build():
            report = _fetch_operations_report()
            return fmt_operations_summary(report)

        payload, cached = _cached("operations:summary", TTL_OPERATIONS, _build)
        return _ok(payload, cached)

    except Exception:
        log.error("GET /operations/summary error:\n%s", traceback.format_exc())
        return _err("failed to fetch operations summary")


@api_bp.route("/paper-portfolio/status", methods=["GET"])
def paper_portfolio_status():
    """
    Paper portfolio simulation metrics from completed predator outcomes.
    Cached for TTL_PAPER seconds.
    """
    try:
        def _build():
            report = _fetch_paper_portfolio_report()
            return fmt_paper_portfolio(report)

        payload, cached = _cached("paper:portfolio", TTL_PAPER, _build)
        return _ok(payload, cached)

    except Exception:
        log.error("GET /paper-portfolio/status error:\n%s", traceback.format_exc())
        return _err("failed to fetch paper portfolio status")


@api_bp.route("/research/report/<report_type>", methods=["GET"])
def research_report(report_type: str):
    """
    Research report by type.

    Valid types: daily, weekly, calibration, regime, portfolio,
                 adaptive, anomaly, degradation.

    Returns 400 for unknown types.  Cached per type for TTL_RESEARCH seconds.
    """
    if report_type not in VALID_REPORT_TYPES:
        return _err(
            f"unknown report type {report_type!r}. "
            f"valid: {sorted(VALID_REPORT_TYPES)}",
            code=400,
        )

    try:
        cache_key = f"research:{report_type}"

        def _build():
            report = _build_research_report(report_type)
            return fmt_research_report(report, report_type)

        payload, cached = _cached(cache_key, TTL_RESEARCH, _build)
        return _ok(payload, cached)

    except Exception:
        log.error("GET /research/report/%s error:\n%s", report_type, traceback.format_exc())
        return _err(f"failed to generate {report_type!r} report")
