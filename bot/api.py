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
import os
import threading
import time
import traceback
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Optional

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

api_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


# ── Auth helper (write endpoints only) ───────────────────────────────────────

def _alpha_require_auth(f):
    """Bearer-token guard for mutating alpha endpoints.

    Checks Authorization: Bearer <token> against API_SECRET env var.
    Fails-open if API_SECRET is unset (local dev). Rejects if set and mismatched.
    """
    import hmac

    @wraps(f)
    def _wrapper(*args, **kwargs):
        secret = os.environ.get("API_SECRET", "")
        if secret:
            auth_header = request.headers.get("Authorization", "")
            token = auth_header.removeprefix("Bearer ").strip()
            if not hmac.compare_digest(token.encode(), secret.encode()):
                return jsonify({"ok": False, "error": {"code": 401, "message": "unauthorized"}}), 401
        return f(*args, **kwargs)

    return _wrapper


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


MAX_ALPHA    = 50   # alpha shadow result row cap
MAX_HISTORY  = 90   # portfolio NAV history row cap
TTL_ALPHA    = 60   # alpha endpoint cache TTL (seconds)


# ── Alpha shadow formatters ───────────────────────────────────────────────────

def fmt_alpha_row(row: dict) -> dict:
    """Serialise one alpha_shadow_log row for API responses."""
    components = {}
    try:
        raw = row.get("component_scores_json")
        components = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        pass

    detail = {}
    try:
        raw_detail = row.get("detail_json")
        detail = json.loads(raw_detail) if raw_detail else {}
    except (json.JSONDecodeError, TypeError):
        pass

    source = "predator_shadow" if row.get("predator_tier") else "alpha_universe"

    return {
        "ticker":                  row.get("ticker", ""),
        "alpha_score":             _safe_float(row.get("alpha_score")),
        "alpha_tier":              row.get("alpha_tier"),
        "setup_type":              row.get("setup_type"),
        "predator_tier":           row.get("predator_tier"),
        "predator_score":          _safe_float(row.get("predator_score")),
        "tier_match":              bool(row.get("tier_match")),
        "filter_reason":           row.get("filter_reason"),
        "explanation":             (row.get("explanation") or "")[:300],
        "scan_time":               row.get("scan_time"),
        "source":                  source,
        "components":              components,
        "why_scored_high":         detail.get("why_scored_high", []),
        "what_must_happen_next":   detail.get("what_must_happen_next", []),
        "what_could_invalidate":   detail.get("what_could_invalidate", []),
        "risk_factors":            detail.get("risk_factors", []),
        "expected_holding_window": detail.get("expected_holding_window"),
        "tier_gate_note":          detail.get("tier_gate_note"),
    }


# ── Alpha endpoints ───────────────────────────────────────────────────────────

@api_bp.route("/alpha/debug", methods=["GET"])
def alpha_debug():
    """
    Alpha shadow diagnostic — never cached.

    Returns:
      alpha_shadow_enabled  bool   — resolved flag value
      env_var_raw           str    — raw ALPHA_SHADOW_ENABLED env var (or "(not set)")
      table_exists          bool   — whether alpha_shadow_log table is in the DB
      row_count             int    — total rows in alpha_shadow_log
      latest_scan_time      str|null — MAX(scan_time) across all rows
    """
    import os

    try:
        from feature_flags import alpha_shadow_enabled
        flag_on  = alpha_shadow_enabled()
        flag_raw = os.environ.get("ALPHA_SHADOW_ENABLED", "")

        table_exists = False
        row_count    = 0
        latest_scan  = None

        try:
            from database import get_connection
            conn = get_connection()
            try:
                row          = conn.execute(
                    "SELECT COUNT(*), MAX(scan_time) FROM alpha_shadow_log"
                ).fetchone()
                table_exists = True
                row_count    = int(row[0]) if row else 0
                latest_scan  = row[1] if row else None
            except Exception as db_exc:
                log.warning("alpha_debug: DB query failed — %s", db_exc)
            finally:
                conn.close()
        except Exception as conn_exc:
            log.warning("alpha_debug: could not open DB — %s", conn_exc)

        hook_diag: dict = {}
        try:
            from alpha_shadow import get_hook_diagnostics
            hook_diag = get_hook_diagnostics()
        except Exception:
            pass

        engine_version = "unknown"
        try:
            from alpha_engine import ALPHA_ENGINE_VERSION
            engine_version = ALPHA_ENGINE_VERSION
        except Exception:
            pass

        universe_diag: dict = {}
        try:
            from alpha_universe import get_universe_diagnostics
            universe_diag = get_universe_diagnostics()
        except Exception:
            pass

        return _ok({
            "alpha_shadow_enabled":     flag_on,
            "env_var_raw":              flag_raw or "(not set)",
            "table_exists":             table_exists,
            "row_count":                row_count,
            "latest_scan_time":         latest_scan,
            "hook_last_seen_at":        hook_diag.get("hook_last_seen_at"),
            "last_error":               hook_diag.get("last_error"),
            "alpha_engine_version":     engine_version,
            "alpha_universe_enabled":   flag_on,
            "universe_size":            universe_diag.get("universe_size"),
            "last_universe_scan_time":  universe_diag.get("last_universe_scan_time"),
            "last_universe_scan_count": universe_diag.get("last_universe_scan_count"),
        })

    except Exception:
        log.error("GET /alpha/debug error:\n%s", traceback.format_exc())
        return _err("alpha debug check failed")


@api_bp.route("/alpha/latest", methods=["GET"])
def alpha_latest():
    """
    Most-recent alpha shadow score per ticker.
    Ordered by alpha_score DESC; capped at MAX_ALPHA rows.
    Returns empty results when alpha shadow is disabled or the table is empty.
    Cached for TTL_ALPHA seconds.
    """
    try:
        def _build():
            from alpha_shadow import get_shadow_manager
            rows    = get_shadow_manager().get_latest_results(MAX_ALPHA)
            results = [fmt_alpha_row(r) for r in rows]
            return {"results": results, "total": len(results)}

        payload, cached = _cached("alpha:latest", TTL_ALPHA, _build)
        return _ok(payload, cached)

    except Exception:
        log.error("GET /alpha/latest error:\n%s", traceback.format_exc())
        return _err("failed to fetch alpha shadow results")


@api_bp.route("/alpha/top", methods=["GET"])
def alpha_top():
    """
    Top-N non-filtered alpha candidates from the most recent scan (max MAX_TOP).
    Only returns tickers with a non-null alpha_score and no filter_reason.
    Cached for TTL_ALPHA seconds.
    """
    try:
        def _build():
            from alpha_shadow import get_shadow_manager
            rows    = get_shadow_manager().get_top_candidates(MAX_TOP)
            results = [fmt_alpha_row(r) for r in rows]
            return {"results": results[:MAX_TOP], "total": len(results)}

        payload, cached = _cached("alpha:top", TTL_ALPHA, _build)
        return _ok(payload, cached)

    except Exception:
        log.error("GET /alpha/top error:\n%s", traceback.format_exc())
        return _err("failed to fetch top alpha candidates")


@api_bp.route("/alpha/analytics", methods=["GET"])
def alpha_analytics():
    """
    Alpha shadow analytics summary — tier distribution, top setups,
    best non-Predator candidates, universe coverage.
    Never cached.
    """
    try:
        from alpha_shadow import get_shadow_manager
        from alpha_universe import get_alpha_universe
        from predator import PREDATOR_WATCHLIST
        mgr = get_shadow_manager()

        tier_counts   = {}
        setup_types   = []
        non_predator  = []
        coverage      = {}
        try:
            tier_counts  = mgr.count_by_tier()
            setup_types  = mgr.get_top_setup_types(limit=10)
            non_predator = [fmt_alpha_row(r) for r in mgr.get_best_non_predator(PREDATOR_WATCHLIST, limit=10)]
            coverage     = mgr.get_universe_coverage(get_alpha_universe())
        except Exception:
            log.warning("alpha_analytics: partial failure", exc_info=True)

        engine_version = "unknown"
        total_rows     = 0
        rejected_alerts: list = []
        try:
            from alpha_engine import ALPHA_ENGINE_VERSION
            engine_version = ALPHA_ENGINE_VERSION
        except Exception:
            pass
        try:
            from database import get_connection
            conn = get_connection()
            try:
                row = conn.execute("SELECT COUNT(*) FROM alpha_shadow_log").fetchone()
                total_rows = int(row[0]) if row else 0
            finally:
                conn.close()
        except Exception:
            pass
        try:
            rejected_alerts = [fmt_alpha_row(r) for r in mgr.get_rejected_alerts(limit=10)]
        except Exception:
            log.warning("alpha_analytics: rejected_alerts partial failure", exc_info=True)

        return _ok({
            "alpha_engine_version":    engine_version,
            "total_rows":              total_rows,
            "tier_counts":             tier_counts,
            "top_setup_types":         setup_types,
            "best_non_predator":       non_predator,
            "universe_coverage":       coverage,
            "rejected_predator_alerts": rejected_alerts,
        })
    except Exception:
        log.error("GET /alpha/analytics error:\n%s", traceback.format_exc())
        return _err("alpha analytics failed")


# ── Alpha universe manual trigger ─────────────────────────────────────────────

_universe_scan_lock = threading.Lock()
_universe_scan_running = False


@api_bp.route("/alpha/run-universe", methods=["POST"])
@_alpha_require_auth
def alpha_run_universe():
    """
    Manually trigger a full alpha universe scan in a background thread.
    Auth-protected (Bearer token matching API_SECRET env var).
    Observation-only — no WhatsApp alerts sent.
    Returns immediately; use /alpha/debug to check last_universe_scan_time.
    """
    global _universe_scan_running

    try:
        from feature_flags import alpha_shadow_enabled
        if not alpha_shadow_enabled():
            return _ok({"queued": False, "reason": "ALPHA_SHADOW_ENABLED is off"})

        with _universe_scan_lock:
            if _universe_scan_running:
                return _ok({"queued": False, "reason": "scan already in progress"})
            _universe_scan_running = True

        def _run():
            global _universe_scan_running
            try:
                from alpha_universe import scan_alpha_universe
                count = scan_alpha_universe()
                log.info("Manual alpha universe scan complete: %d scored", count)
            except Exception:
                log.warning("Manual alpha universe scan failed", exc_info=True)
            finally:
                with _universe_scan_lock:
                    _universe_scan_running = False

        threading.Thread(target=_run, daemon=True, name="alpha-universe-manual").start()
        return _ok({"queued": True, "reason": "scan started in background"})

    except Exception:
        log.error("POST /alpha/run-universe error:\n%s", traceback.format_exc())
        return _err("failed to start universe scan")


@api_bp.route("/paper-portfolio/history", methods=["GET"])
def paper_portfolio_history():
    """
    NAV time-series from portfolio_history table.
    Ordered date ASC, capped at MAX_HISTORY rows.
    Cached for TTL_PAPER seconds.
    """
    try:
        def _build():
            from database import get_connection
            conn = get_connection()
            try:
                rows = conn.execute(
                    "SELECT date, value_cad FROM portfolio_history "
                    "ORDER BY date ASC LIMIT ?",
                    (MAX_HISTORY,),
                ).fetchall()
            finally:
                conn.close()
            points = [
                {"date": r["date"], "value": _safe_float(r["value_cad"])}
                for r in rows
                if r["value_cad"] is not None
            ]
            return {"points": points, "total": len(points)}

        payload, cached = _cached("paper:history", TTL_PAPER, _build)
        return _ok(payload, cached)

    except Exception:
        log.error("GET /paper-portfolio/history error:\n%s", traceback.format_exc())
        return _err("failed to fetch portfolio history")


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
