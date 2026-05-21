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
from typing import Any, Optional

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

api_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


# ── Auth helper (write endpoints only) ───────────────────────────────────────

def _check_alpha_auth() -> bool:
    """Return True if the request is authorized (or no secret is configured).

    Checks Authorization: Bearer <token> against API_SECRET env var.
    Fails-open when API_SECRET is unset (local dev / Railway before secret is set).
    """
    import hmac
    secret = os.environ.get("API_SECRET", "")
    if not secret:
        return True
    auth_header = request.headers.get("Authorization", "")
    # Strip "Bearer " prefix safely (compatible with Python 3.8+)
    prefix = "Bearer "
    token = auth_header[len(prefix):].strip() if auth_header.startswith(prefix) else auth_header.strip()
    return hmac.compare_digest(token.encode(), secret.encode())


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
def alpha_run_universe():
    """
    Manually trigger a full alpha universe scan in a background thread.
    Auth-protected (Bearer token matching API_SECRET env var).
    Observation-only — no WhatsApp alerts sent.
    Returns immediately; poll /alpha/debug for last_universe_scan_time.
    """
    global _universe_scan_running

    if not _check_alpha_auth():
        return jsonify({"ok": False, "error": {"code": 401, "message": "unauthorized"}}), 401

    try:
        from alpha_universe import get_alpha_universe
        universe = get_alpha_universe()
        universe_size = len(universe)

        from feature_flags import alpha_shadow_enabled
        if not alpha_shadow_enabled():
            return _ok({
                "status": "skipped",
                "reason": "ALPHA_SHADOW_ENABLED is off",
                "universe_size": universe_size,
            })

        with _universe_scan_lock:
            if _universe_scan_running:
                return _ok({
                    "status": "already running",
                    "reason": "scan already in progress",
                    "universe_size": universe_size,
                })
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
        return _ok({
            "status": "scan started",
            "universe_size": universe_size,
        })

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


# ── Alpha A5: monitoring report, outcomes, learning ───────────────────────────

@api_bp.route("/alpha/report", methods=["GET"])
def alpha_report():
    """
    Full alpha monitoring report — tier distribution, top candidates,
    data quality analysis, quality diagnosis, and actionable recommendations.
    Cached for 5 minutes.
    """
    try:
        def _build():
            from alpha_monitor import generate_alpha_report
            return generate_alpha_report()

        payload, cached = _cached("alpha:report", 300, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /alpha/report error:\n%s", traceback.format_exc())
        return _err("alpha report generation failed")


@api_bp.route("/alpha/outcomes", methods=["GET"])
def alpha_outcomes_endpoint():
    """
    Alpha outcome tracking rows ordered scan_time DESC.
    Query params:
      status  — filter by PENDING / COMPLETE / STALE (optional)
      limit   — max rows (default 50, max 200)
    """
    try:
        status = request.args.get("status") or None
        limit  = min(int(request.args.get("limit", 50)), 200)

        if status and status not in ("PENDING", "COMPLETE", "STALE"):
            return _err("status must be PENDING, COMPLETE, or STALE", code=400)

        from alpha_outcomes import get_outcomes
        rows = get_outcomes(limit=limit, status=status)
        return _ok({"results": rows, "total": len(rows), "status_filter": status})
    except Exception:
        log.error("GET /alpha/outcomes error:\n%s", traceback.format_exc())
        return _err("failed to fetch alpha outcomes")


@api_bp.route("/alpha/learning", methods=["GET"])
def alpha_learning():
    """
    Learning analytics from completed alpha outcomes:
    per-setup, per-tier, per-source effectiveness with avg 5-day return and win rate.
    Cached for 10 minutes.
    """
    try:
        def _build():
            from alpha_outcomes import compute_learning_analytics
            return compute_learning_analytics()

        payload, cached = _cached("alpha:learning", 600, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /alpha/learning error:\n%s", traceback.format_exc())
        return _err("alpha learning analytics failed")


# ── Alpha L2: shadow weight recommendations and policy simulation ──────────────

@api_bp.route("/alpha/learning/recommendations", methods=["GET"])
def alpha_learning_recommendations():
    """
    Shadow weight recommendations from COMPLETE alpha outcomes.
    Includes per-component lift, setup effectiveness, tier calibration,
    weight change recommendations, and threshold recommendations.
    Cached 10 min.
    """
    try:
        def _build():
            from alpha_learning_engine import generate_recommendations_report
            return generate_recommendations_report()

        payload, cached = _cached("alpha:learning:recommendations", 600, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /alpha/learning/recommendations error:\n%s", traceback.format_exc())
        return _err("recommendations report failed")


@api_bp.route("/alpha/learning/shadow-policy", methods=["GET"])
def alpha_learning_shadow_policy():
    """
    Shadow policy simulation: applies recommended weights and replays past
    COMPLETE outcomes to estimate false-positive reduction and missed-winner risk.
    Cached 10 min.
    """
    try:
        def _build():
            from alpha_learning_engine import generate_shadow_policy_report
            return generate_shadow_policy_report()

        payload, cached = _cached("alpha:learning:shadow-policy", 600, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /alpha/learning/shadow-policy error:\n%s", traceback.format_exc())
        return _err("shadow policy report failed")


# ── Alpha L3: controlled promotion workflow ────────────────────────────────────

@api_bp.route("/alpha/learning/proposals", methods=["GET"])
def alpha_proposals_list():
    """
    List Alpha learning proposals.
    Query params:
      status            — filter by status (optional)
      include_historical — if "true", include REJECTED/EXPIRED/ROLLBACK_READY
    """
    try:
        status_filter      = request.args.get("status") or None
        include_historical = request.args.get("include_historical", "").lower() == "true"

        from alpha_proposals import get_proposals
        proposals = get_proposals(
            status_filter=status_filter,
            include_historical=include_historical,
        )
        active = sum(1 for p in proposals if p["status"] in ("PROPOSED", "APPROVED_FOR_SHADOW"))
        return _ok({
            "proposals":   proposals,
            "total":       len(proposals),
            "active":      active,
            "status_filter": status_filter,
        })
    except Exception:
        log.error("GET /alpha/learning/proposals error:\n%s", traceback.format_exc())
        return _err("failed to fetch proposals")


@api_bp.route("/alpha/learning/proposals/generate", methods=["POST"])
def alpha_proposals_generate():
    """
    Generate promotion proposals from current L2 recommendations.
    Auth required.  Idempotent — re-running produces no duplicates.
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)
    try:
        from alpha_proposals import generate_proposals
        proposals = generate_proposals()
        return _ok({
            "generated": len(proposals),
            "proposals": proposals,
            "note":      "Proposals are idempotent — identical recommendations produce no duplicates",
        })
    except Exception:
        log.error("POST /alpha/learning/proposals/generate error:\n%s", traceback.format_exc())
        return _err("proposal generation failed")


@api_bp.route("/alpha/learning/proposals/<proposal_id>/approve-shadow", methods=["POST"])
def alpha_proposals_approve(proposal_id: str):
    """
    Approve a PROPOSED proposal for shadow testing.
    Auth required.  Body (JSON, optional): {"note": "..."}
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)
    try:
        body  = request.get_json(silent=True) or {}
        note  = body.get("note")
        actor = request.headers.get("X-Actor", "api")

        from alpha_proposals import approve_for_shadow
        proposal = approve_for_shadow(proposal_id, actor=actor, note=note)
        return _ok({
            "proposal_id": proposal["proposal_id"],
            "status":      proposal["status"],
            "reviewed_at": proposal["reviewed_at"],
            "reviewed_by": proposal["reviewed_by"],
        })
    except ValueError as e:
        return _err(str(e), code=400)
    except Exception:
        log.error("POST /alpha/learning/proposals/%s/approve-shadow error:\n%s",
                  proposal_id, traceback.format_exc())
        return _err("approve-shadow failed")


@api_bp.route("/alpha/learning/proposals/<proposal_id>/reject", methods=["POST"])
def alpha_proposals_reject(proposal_id: str):
    """
    Reject a PROPOSED proposal.
    Auth required.  Body (JSON, optional): {"reason": "..."}
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)
    try:
        body   = request.get_json(silent=True) or {}
        reason = body.get("reason")
        actor  = request.headers.get("X-Actor", "api")

        from alpha_proposals import reject_proposal
        proposal = reject_proposal(proposal_id, reason=reason, actor=actor)
        return _ok({
            "proposal_id": proposal["proposal_id"],
            "status":      proposal["status"],
            "reviewed_at": proposal["reviewed_at"],
            "reviewed_by": proposal["reviewed_by"],
        })
    except ValueError as e:
        return _err(str(e), code=400)
    except Exception:
        log.error("POST /alpha/learning/proposals/%s/reject error:\n%s",
                  proposal_id, traceback.format_exc())
        return _err("reject failed")


@api_bp.route("/alpha/learning/proposals/<proposal_id>/shadow-results", methods=["GET"])
def alpha_proposals_shadow_results(proposal_id: str):
    """
    On-demand shadow replay for a specific proposal using its stored shadow weights.
    No auth required (read-only).
    """
    try:
        from alpha_proposals import get_shadow_results
        results = get_shadow_results(proposal_id)
        if "error" in results and "not found" in results.get("error", ""):
            return _err(results["error"], code=404)
        return _ok(results)
    except Exception:
        log.error("GET /alpha/learning/proposals/%s/shadow-results error:\n%s",
                  proposal_id, traceback.format_exc())
        return _err("shadow results failed")


# ── Alpha A6: reality validation layer ────────────────────────────────────────

TTL_VALIDATION = 300  # 5 minutes


@api_bp.route("/alpha/validation", methods=["GET"])
def alpha_validation():
    """
    Alpha reality validation rows.
    Query params:
      setup_type     — filter by setup_type (optional)
      behavior_class — filter by behavior_class (optional)
      limit          — max rows (default 100, max 500)
    Cached 5 min.
    """
    try:
        setup_type     = request.args.get("setup_type") or None
        behavior_class = request.args.get("behavior_class") or None
        limit          = min(int(request.args.get("limit", 100)), 500)

        cache_key = f"alpha:validation:{setup_type}:{behavior_class}:{limit}"

        def _build():
            from alpha_validation import get_validations
            rows = get_validations(limit=limit, setup_type=setup_type, behavior_class=behavior_class)
            return {"results": rows, "total": len(rows),
                    "setup_type_filter": setup_type, "behavior_class_filter": behavior_class}

        payload, cached = _cached(cache_key, TTL_VALIDATION, _build)
        return _ok(payload, cached)

    except Exception:
        log.error("GET /alpha/validation error:\n%s", traceback.format_exc())
        return _err("failed to fetch validation rows")


@api_bp.route("/alpha/validation/summary", methods=["GET"])
def alpha_validation_summary():
    """
    Aggregate validation analytics: behavior distribution, trap rates,
    sustainability rates, leaderboards, per-tier averages.
    Cached 5 min.
    """
    try:
        def _build():
            from alpha_validation import get_validation_summary
            return get_validation_summary()

        payload, cached = _cached("alpha:validation:summary", TTL_VALIDATION, _build)
        return _ok(payload, cached)

    except Exception:
        log.error("GET /alpha/validation/summary error:\n%s", traceback.format_exc())
        return _err("validation summary failed")


# ── Alpha A7: alert candidate gate ────────────────────────────────────────────

TTL_GATE = 60  # 1 minute — same as alpha scan results


@api_bp.route("/alpha/alert-candidates", methods=["GET"])
def alpha_alert_candidates():
    """
    All current Alpha candidates scored for alert readiness.
    Sorted by readiness_score DESC.
    Query params:
      limit — max rows (default 50, max 200)
    Cached 1 min.
    """
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        cache_key = f"alpha:alert-candidates:{limit}"

        def _build():
            from alpha_alert_gate import get_alert_candidates
            results = get_alert_candidates(limit=limit)
            alert_ready = sum(1 for r in results if r["alert_ready"])
            return {
                "results":     results,
                "total":       len(results),
                "alert_ready": alert_ready,
                "note":        "Simulation only — no WhatsApp alerts sent",
            }

        payload, cached = _cached(cache_key, TTL_GATE, _build)
        return _ok(payload, cached)

    except Exception:
        log.error("GET /alpha/alert-candidates error:\n%s", traceback.format_exc())
        return _err("failed to score alert candidates")


@api_bp.route("/alpha/alert-gate/summary", methods=["GET"])
def alpha_alert_gate_summary():
    """
    Aggregate alert gate analytics: tier distribution, top blockers,
    confirmation needs, candidates close to alert-ready.
    Cached 1 min.
    """
    try:
        def _build():
            from alpha_alert_gate import get_alert_gate_summary
            return get_alert_gate_summary()

        payload, cached = _cached("alpha:alert-gate:summary", TTL_GATE, _build)
        return _ok(payload, cached)

    except Exception:
        log.error("GET /alpha/alert-gate/summary error:\n%s", traceback.format_exc())
        return _err("alert gate summary failed")


# ── Alpha A8: notification dry-run review ─────────────────────────────────────

TTL_DRYRUN = 30   # 30 s — short TTL; operator expects fresh state after actions


@api_bp.route("/alpha/notifications/dry-run", methods=["GET"])
def alpha_dry_run_list():
    """
    List Alpha notification dry-runs.
    Query params:
      status — filter by DRY_RUN, REVIEWED, DISMISSED, EXPIRED (default: active only)
      limit  — max rows (default 50, max 200)
    Cached 30 s.
    """
    try:
        status_filter = request.args.get("status") or None
        limit         = min(int(request.args.get("limit", 50)), 200)

        valid_statuses = {"DRY_RUN", "REVIEWED", "DISMISSED", "EXPIRED"}
        if status_filter and status_filter not in valid_statuses:
            return _err(
                f"invalid status {status_filter!r}; valid: {sorted(valid_statuses)}",
                code=400,
            )

        cache_key = f"alpha:dryrun:list:{status_filter}:{limit}"

        def _build():
            from alpha_notification_dryrun import get_dry_runs
            rows = get_dry_runs(status_filter=status_filter, limit=limit)
            active = sum(1 for r in rows if r["status"] in ("DRY_RUN", "REVIEWED"))
            return {
                "results":       rows,
                "total":         len(rows),
                "active":        active,
                "status_filter": status_filter,
                "note":          "Dry-run only — no real notifications sent",
            }

        payload, cached = _cached(cache_key, TTL_DRYRUN, _build)
        return _ok(payload, cached)

    except Exception:
        log.error("GET /alpha/notifications/dry-run error:\n%s", traceback.format_exc())
        return _err("failed to fetch dry-run notifications")


@api_bp.route("/alpha/notifications/dry-run/generate", methods=["POST"])
def alpha_dry_run_generate():
    """
    Generate dry-run notification rows for all eligible Alpha candidates.
    Auth required.  Idempotent — same candidate identity produces no duplicates.
    No real messages are sent.
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)
    try:
        from alpha_notification_dryrun import generate_dry_runs
        rows     = generate_dry_runs()
        new_rows = [r for r in rows if r["status"] == "DRY_RUN"]
        cache_clear()  # invalidate list cache after generation
        return _ok({
            "generated": len(new_rows),
            "total":     len(rows),
            "dry_runs":  rows,
            "note":      "Dry-run only — no real notifications sent",
        })
    except Exception:
        log.error("POST /alpha/notifications/dry-run/generate error:\n%s", traceback.format_exc())
        return _err("dry-run generation failed")


@api_bp.route("/alpha/notifications/dry-run/<dry_run_id>/review", methods=["POST"])
def alpha_dry_run_review(dry_run_id: str):
    """
    Mark a DRY_RUN notification as REVIEWED.
    Auth required.  Body (JSON, optional): {"note": "..."}
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)
    try:
        body  = request.get_json(silent=True) or {}
        note  = body.get("note")
        actor = request.headers.get("X-Actor", "api")

        from alpha_notification_dryrun import mark_reviewed
        row = mark_reviewed(dry_run_id, actor=actor, note=note)
        cache_clear()
        return _ok({
            "dry_run_id":  row["dry_run_id"],
            "status":      row["status"],
            "reviewed_at": row["reviewed_at"],
            "reviewed_by": row["reviewed_by"],
        })
    except ValueError as e:
        return _err(str(e), code=400)
    except Exception:
        log.error("POST /alpha/notifications/dry-run/%s/review error:\n%s",
                  dry_run_id, traceback.format_exc())
        return _err("review failed")


@api_bp.route("/alpha/notifications/dry-run/<dry_run_id>/dismiss", methods=["POST"])
def alpha_dry_run_dismiss(dry_run_id: str):
    """
    Dismiss a DRY_RUN notification.
    Auth required.  Body (JSON, optional): {"reason": "..."}
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)
    try:
        body   = request.get_json(silent=True) or {}
        reason = body.get("reason")
        actor  = request.headers.get("X-Actor", "api")

        from alpha_notification_dryrun import dismiss_dry_run
        row = dismiss_dry_run(dry_run_id, reason=reason, actor=actor)
        cache_clear()
        return _ok({
            "dry_run_id":   row["dry_run_id"],
            "status":       row["status"],
            "dismissed_at": row["dismissed_at"],
            "dismissed_by": row["dismissed_by"],
        })
    except ValueError as e:
        return _err(str(e), code=400)
    except Exception:
        log.error("POST /alpha/notifications/dry-run/%s/dismiss error:\n%s",
                  dry_run_id, traceback.format_exc())
        return _err("dismiss failed")


# ── A9: Notification QC ───────────────────────────────────────────────────────

@api_bp.route("/alpha/notifications/qc", methods=["GET"])
def alpha_notifications_qc():
    """
    List QC history records.
    No auth required.
    Optional query params: ?ticker=AAPL  ?qc_tier=PRIORITY  ?limit=50
    No real notifications are sent at any point.
    """
    ticker  = request.args.get("ticker")
    qc_tier = request.args.get("qc_tier")
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except (ValueError, TypeError):
        limit = 50

    if qc_tier and qc_tier not in ("BLOCK", "SUPPRESS", "ALLOW", "PRIORITY"):
        return _err(
            f"invalid qc_tier {qc_tier!r}; must be one of BLOCK, SUPPRESS, ALLOW, PRIORITY",
            code=400,
        )

    cache_key = f"alpha:qc:{ticker}:{qc_tier}:{limit}"

    def _build():
        from alpha_notification_qc import get_qc_records
        records = get_qc_records(ticker=ticker, qc_tier=qc_tier, limit=limit)
        return {
            "count":   len(records),
            "records": records,
            "note":    "Simulation only — no real notifications sent",
        }

    try:
        payload, cached = _cached(cache_key, 60, _build)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /alpha/notifications/qc error:\n%s", traceback.format_exc())
        return _err("failed to fetch QC records")


@api_bp.route("/alpha/notifications/qc/summary", methods=["GET"])
def alpha_notifications_qc_summary():
    """
    Aggregate QC statistics: suppressed count, duplicates, unstable suppressions,
    avg qc_score, priority candidates, cooldown-active count.
    No auth required.  No real notifications are sent.
    """
    def _build():
        from alpha_notification_qc import get_qc_summary
        return get_qc_summary()

    try:
        payload, cached = _cached("alpha:qc:summary", 60, _build)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /alpha/notifications/qc/summary error:\n%s", traceback.format_exc())
        return _err("failed to fetch QC summary")


# ── A10: Notification delivery bridge ────────────────────────────────────────

@api_bp.route("/alpha/notifications/delivery-log", methods=["GET"])
def alpha_notifications_delivery_log():
    """
    List delivery audit log entries.
    No auth required (read-only).
    Optional query params: ?ticker=AAPL  ?limit=50
    """
    ticker = request.args.get("ticker")
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except (ValueError, TypeError):
        limit = 50

    cache_key = f"alpha:delivery-log:{ticker}:{limit}"

    def _build():
        from alpha_notification_delivery import get_delivery_log, get_delivery_flags
        rows  = get_delivery_log(ticker=ticker, limit=limit)
        flags = get_delivery_flags()
        return {
            "count":         len(rows),
            "entries":       rows,
            "feature_flags": {
                "enabled":          flags["enabled"],
                "dry_run_only":     flags["dry_run_only"],
                "min_qc_tier":      flags["min_qc_tier"],
                "require_reviewed": flags["require_reviewed"],
            },
            "note": "Delivery bridge is disabled by default — see feature_flags",
        }

    try:
        payload, cached = _cached(cache_key, 30, _build)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /alpha/notifications/delivery-log error:\n%s", traceback.format_exc())
        return _err("failed to fetch delivery log")


@api_bp.route("/alpha/notifications/<dry_run_id>/send", methods=["POST"])
def alpha_notifications_send(dry_run_id: str):
    """
    Attempt to deliver one Alpha WhatsApp notification.

    Auth required.  Runs all eligibility gates before any send.
    Sending is blocked by default unless feature flags explicitly enable it.
    No batch sending — one dry-run per POST.
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)
    try:
        from alpha_notification_delivery import deliver_notification
        result = deliver_notification(dry_run_id)
        cache_clear()
        return _ok(result)
    except Exception:
        log.error("POST /alpha/notifications/%s/send error:\n%s",
                  dry_run_id, traceback.format_exc())
        return _err("delivery attempt failed")


# ── A11: Canonical portfolio truth layer ─────────────────────────────────────

@api_bp.route("/portfolio", methods=["GET"])
def portfolio_canonical():
    """
    Return the latest canonical portfolio state from portfolio_positions.
    No auth required (read-only).  TTL 30 s.
    Reads from the positions table — does NOT trigger a fresh yfinance fetch.
    POST /portfolio/reconcile to refresh prices.
    Each position includes a thesis_summary field (or null if no thesis exists).
    """
    def _build():
        from portfolio_reconciliation import get_canonical_portfolio
        from position_journal import get_thesis_summaries
        result = get_canonical_portfolio()
        positions = result.get("positions", [])
        if positions:
            tickers   = [p["ticker"] for p in positions]
            summaries = get_thesis_summaries(tickers)
            for p in positions:
                p["thesis_summary"] = summaries.get(p["ticker"])
        return result

    try:
        payload, cached = _cached("portfolio:canonical", 30, _build)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /portfolio error:\n%s", traceback.format_exc())
        return _err("failed to fetch canonical portfolio")


@api_bp.route("/portfolio/snapshots", methods=["GET"])
def portfolio_snapshots():
    """
    Return recent portfolio snapshots ordered by taken_at DESC.
    No auth required.  TTL 60 s.
    Optional query param: ?limit=20
    """
    try:
        limit = min(int(request.args.get("limit", 20)), 100)
    except (ValueError, TypeError):
        limit = 20

    cache_key = f"portfolio:snapshots:{limit}"

    def _build():
        from portfolio_reconciliation import get_snapshots
        snaps = get_snapshots(limit=limit)
        return {"count": len(snaps), "snapshots": snaps}

    try:
        payload, cached = _cached(cache_key, 60, _build)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /portfolio/snapshots error:\n%s", traceback.format_exc())
        return _err("failed to fetch portfolio snapshots")


@api_bp.route("/portfolio/reconciliation", methods=["GET"])
def portfolio_reconciliation_log():
    """
    Return reconciliation run history ordered by reconciled_at DESC.
    No auth required.  TTL 30 s.
    Optional query param: ?limit=50
    """
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except (ValueError, TypeError):
        limit = 50

    cache_key = f"portfolio:reconciliation-log:{limit}"

    def _build():
        from portfolio_reconciliation import get_reconciliation_log
        runs = get_reconciliation_log(limit=limit)
        return {"count": len(runs), "runs": runs}

    try:
        payload, cached = _cached(cache_key, 30, _build)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /portfolio/reconciliation error:\n%s", traceback.format_exc())
        return _err("failed to fetch reconciliation log")


@api_bp.route("/portfolio/reconcile", methods=["POST"])
def portfolio_reconcile():
    """
    Trigger a fresh portfolio reconciliation.

    Auth required.  Fetches live prices via market_data, rebuilds portfolio_positions,
    logs the run to portfolio_reconciliation_log.
    Never writes to holdings, transactions, or cash.
    Returns the full reconciliation result including canonical positions and issues.
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)
    try:
        from portfolio_reconciliation import reconcile_portfolio
        result = reconcile_portfolio(trigger="api")
        cache_clear()
        return _ok(result)
    except Exception:
        log.error("POST /portfolio/reconcile error:\n%s", traceback.format_exc())
        return _err("reconciliation failed")


# ── A12: Manual portfolio control ─────────────────────────────────────────────

@api_bp.route("/portfolio/manual", methods=["GET"])
def portfolio_manual_get():
    """
    Return the manual portfolio: active positions + account settings.
    No auth required (read-only).  TTL 30 s.
    """
    def _build():
        from manual_portfolio import get_manual_portfolio
        return get_manual_portfolio()

    try:
        payload, cached = _cached("portfolio:manual", 30, _build)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /portfolio/manual error:\n%s", traceback.format_exc())
        return _err("failed to fetch manual portfolio")


@api_bp.route("/portfolio/manual/positions/upsert", methods=["POST"])
def portfolio_manual_upsert():
    """
    Insert or update a manual position.

    Auth required.  Validates inputs.  Logs to audit trail.
    Body (JSON): ticker, quantity, avg_cost, [realized_pnl], [account_type],
                 [currency], [note]
    Re-activates a previously deactivated position on upsert.
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)
    body = request.get_json(silent=True) or {}
    ticker = body.get("ticker")
    if not ticker:
        return _err("ticker is required", code=400)
    try:
        quantity = float(body.get("quantity", 0))
        avg_cost = float(body.get("avg_cost", 0))
    except (TypeError, ValueError):
        return _err("quantity and avg_cost must be numbers", code=400)

    try:
        from manual_portfolio import upsert_position
        result = upsert_position(
            ticker       = ticker,
            quantity     = quantity,
            avg_cost     = avg_cost,
            realized_pnl = float(body.get("realized_pnl", 0)),
            account_type = str(body.get("account_type", "TFSA")),
            currency     = str(body.get("currency", "CAD")),
            note         = str(body.get("note", "")),
        )
        if not result.get("ok"):
            return _err(f"validation failed: {result.get('errors')}", code=422)
        cache_clear()
        return _ok(result)
    except Exception:
        log.error("POST /portfolio/manual/positions/upsert error:\n%s", traceback.format_exc())
        return _err("upsert failed")


@api_bp.route("/portfolio/manual/positions/<ticker>/deactivate", methods=["POST"])
def portfolio_manual_deactivate(ticker: str):
    """
    Deactivate a manual position.  Does NOT delete the row (audit safety).
    Auth required.  Returns error if the position is not found.
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)
    try:
        from manual_portfolio import deactivate_position
        result = deactivate_position(ticker)
        if not result.get("ok"):
            code = 404 if result.get("error") == "POSITION_NOT_FOUND" else 400
            return _err(result.get("error", "deactivate failed"), code=code)
        cache_clear()
        return _ok(result)
    except Exception:
        log.error("POST /portfolio/manual/positions/%s/deactivate error:\n%s",
                  ticker, traceback.format_exc())
        return _err("deactivate failed")


@api_bp.route("/portfolio/manual/account", methods=["POST"])
def portfolio_manual_account():
    """
    Update manual account settings (partial update — only supplied fields change).

    Auth required.  Validates account_type, base_currency, available_cash.
    Body (JSON): [account_name], [account_type], [base_currency], [available_cash],
                 [contribution_room], [notes]
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)
    body = request.get_json(silent=True) or {}

    kwargs = {}
    for key in ("account_name", "account_type", "base_currency", "notes"):
        if key in body:
            kwargs[key] = str(body[key])
    for key in ("available_cash", "contribution_room"):
        if key in body:
            try:
                kwargs[key] = float(body[key])
            except (TypeError, ValueError):
                return _err(f"{key} must be a number", code=400)

    try:
        from manual_portfolio import update_account_settings
        result = update_account_settings(**kwargs)
        if not result.get("ok"):
            return _err(f"validation failed: {result.get('errors')}", code=422)
        cache_clear()
        return _ok(result)
    except Exception:
        log.error("POST /portfolio/manual/account error:\n%s", traceback.format_exc())
        return _err("account settings update failed")


@api_bp.route("/portfolio/reconcile/manual", methods=["POST"])
def portfolio_reconcile_manual():
    """
    Trigger a manual reconciliation from manual_portfolio_positions + manual_account_settings.

    Auth required.  Fetches live prices, rebuilds portfolio_positions using manual
    positions as truth source, takes an immutable snapshot, logs run.
    No trading operations performed.
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)
    try:
        from manual_portfolio import reconcile_manual
        result = reconcile_manual()
        cache_clear()
        return _ok(result)
    except Exception:
        log.error("POST /portfolio/reconcile/manual error:\n%s", traceback.format_exc())
        return _err("manual reconciliation failed")


# ── Phase A13: position journal and thesis endpoints ─────────────────────────

@api_bp.route("/portfolio/thesis", methods=["GET"])
def portfolio_thesis_list():
    """
    Return all position theses.  Optional ?status= filter.
    No auth required (read-only).  TTL 60 s.
    """
    status_filter = request.args.get("status", "").strip().upper() or None

    def _build():
        from position_journal import get_all_theses
        return {"theses": get_all_theses(status=status_filter)}

    cache_key = f"portfolio:thesis:all:{status_filter or 'ALL'}"
    try:
        payload, cached = _cached(cache_key, 60, _build)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /portfolio/thesis error:\n%s", traceback.format_exc())
        return _err("failed to fetch theses")


@api_bp.route("/portfolio/thesis/<ticker>", methods=["GET"])
def portfolio_thesis_get(ticker: str):
    """
    Return the thesis and recent journal entries for a ticker.
    No auth required (read-only).  TTL 30 s.  404 if no thesis found.
    """
    ticker = ticker.strip().upper()

    def _build():
        from position_journal import get_thesis, get_journal_entries
        thesis = get_thesis(ticker)
        if thesis is None:
            return None
        entries = get_journal_entries(ticker, limit=50)
        return {"thesis": thesis, "journal": entries}

    cache_key = f"portfolio:thesis:{ticker}"
    try:
        payload, cached = _cached(cache_key, 30, _build)
        if payload is None:
            return _err(f"no thesis found for {ticker}", code=404)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /portfolio/thesis/%s error:\n%s", ticker, traceback.format_exc())
        return _err("failed to fetch thesis")


@api_bp.route("/portfolio/thesis/<ticker>/upsert", methods=["POST"])
def portfolio_thesis_upsert(ticker: str):
    """
    Insert or update a position thesis for a ticker.
    Auth required.  Body (JSON): thesis_title, thesis_text, setup_type,
    conviction_level, time_horizon, entry_reason, expected_catalysts, risk_factors,
    invalidation_level, target_level, exit_plan, review_frequency_days,
    next_review_at, status.
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)

    ticker = ticker.strip().upper()
    body   = request.get_json(silent=True) or {}

    numeric_fields = {}
    for key in ("invalidation_level", "target_level"):
        if key in body and body[key] is not None:
            try:
                numeric_fields[key] = float(body[key])
            except (TypeError, ValueError):
                return _err(f"{key} must be a number", code=400)

    freq = body.get("review_frequency_days", 30)
    try:
        freq = int(freq)
    except (TypeError, ValueError):
        return _err("review_frequency_days must be an integer", code=400)

    try:
        from position_journal import upsert_thesis
        result = upsert_thesis(
            ticker               = ticker,
            thesis_title         = str(body.get("thesis_title", "")),
            thesis_text          = str(body.get("thesis_text", "")),
            setup_type           = str(body.get("setup_type", "")),
            conviction_level     = str(body.get("conviction_level", "MEDIUM")),
            time_horizon         = str(body.get("time_horizon", "MEDIUM")),
            entry_reason         = str(body.get("entry_reason", "")),
            expected_catalysts   = str(body.get("expected_catalysts", "")),
            risk_factors         = str(body.get("risk_factors", "")),
            invalidation_level   = numeric_fields.get("invalidation_level"),
            target_level         = numeric_fields.get("target_level"),
            exit_plan            = str(body.get("exit_plan", "")),
            review_frequency_days = freq,
            next_review_at       = body.get("next_review_at") or None,
            status               = str(body.get("status", "ACTIVE")),
        )
        if not result.get("ok"):
            return _err(f"validation failed: {result.get('errors')}", code=422)
        cache_clear()
        return _ok(result)
    except Exception:
        log.error("POST /portfolio/thesis/%s/upsert error:\n%s", ticker, traceback.format_exc())
        return _err("thesis upsert failed")


@api_bp.route("/portfolio/thesis/<ticker>/journal", methods=["POST"])
def portfolio_thesis_journal(ticker: str):
    """
    Append a journal entry for a ticker's thesis.
    Auth required.  Body (JSON): entry_type, text, [tags], [confidence_change].
    entry_type must be one of: NOTE, REVIEW, THESIS_UPDATE, RISK_UPDATE,
    CATALYST_UPDATE, EXIT_PLAN_UPDATE.
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)

    ticker = ticker.strip().upper()
    body   = request.get_json(silent=True) or {}

    entry_type = str(body.get("entry_type", "")).strip().upper()
    text       = str(body.get("text", "")).strip()
    tags       = body.get("tags") or []
    confidence_change = body.get("confidence_change")
    if confidence_change is not None:
        confidence_change = str(confidence_change)

    try:
        from position_journal import add_journal_entry
        result = add_journal_entry(
            ticker            = ticker,
            entry_type        = entry_type,
            text              = text,
            tags              = tags,
            confidence_change = confidence_change,
        )
        if not result.get("ok"):
            return _err(f"validation failed: {result.get('errors')}", code=422)
        cache_clear()
        return _ok(result)
    except Exception:
        log.error("POST /portfolio/thesis/%s/journal error:\n%s", ticker, traceback.format_exc())
        return _err("journal entry failed")


@api_bp.route("/portfolio/reviews", methods=["GET"])
def portfolio_reviews():
    """
    Return due/overdue/upcoming thesis reviews and quality warnings.
    No auth required (read-only).  TTL 30 s.
    """
    def _build():
        from position_journal import get_review_summary
        return get_review_summary()

    try:
        payload, cached = _cached("portfolio:reviews", 30, _build)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /portfolio/reviews error:\n%s", traceback.format_exc())
        return _err("failed to fetch review summary")


# ── Phase A14: decision checklist endpoints ───────────────────────────────────

@api_bp.route("/decisions/checklists", methods=["GET"])
def decisions_checklists_list():
    """
    Return decision checklists.  No auth required (read-only).  TTL 30 s.
    Optional query params: ?ticker=, ?status=, ?decision_type=
    """
    ticker        = request.args.get("ticker", "").strip().upper() or None
    status_filter = request.args.get("status", "").strip().upper() or None
    dt_filter     = request.args.get("decision_type", "").strip().upper() or None

    cache_key = f"decisions:checklists:{ticker}:{status_filter}:{dt_filter}"

    def _build():
        from decision_checklist import get_all_checklists
        return {"checklists": get_all_checklists(ticker=ticker, decision_type=dt_filter,
                                                  status=status_filter)}

    try:
        payload, cached = _cached(cache_key, 30, _build)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /decisions/checklists error:\n%s", traceback.format_exc())
        return _err("failed to fetch checklists")


@api_bp.route("/decisions/checklists/<checklist_id>", methods=["GET"])
def decisions_checklist_get(checklist_id: str):
    """
    Return a single checklist with its items.  No auth required.  TTL 30 s.
    Returns 404 if not found.
    """
    def _build():
        from decision_checklist import get_checklist
        return get_checklist(checklist_id)

    cache_key = f"decisions:checklist:{checklist_id}"
    try:
        payload, cached = _cached(cache_key, 30, _build)
        if payload is None:
            return _err(f"checklist {checklist_id} not found", code=404)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /decisions/checklists/%s error:\n%s", checklist_id, traceback.format_exc())
        return _err("failed to fetch checklist")


@api_bp.route("/decisions/checklists/create", methods=["POST"])
def decisions_checklist_create():
    """
    Create a new decision checklist with 10 default items seeded as NULL.
    Auth required.
    Body (JSON): ticker, decision_type, [linked_alpha_candidate_id],
                 [linked_thesis_id], [notes]
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)

    body = request.get_json(silent=True) or {}
    ticker        = str(body.get("ticker", "")).strip().upper()
    decision_type = str(body.get("decision_type", "")).strip().upper()
    notes         = str(body.get("notes", ""))

    linked_thesis_id = body.get("linked_thesis_id")
    if linked_thesis_id is not None:
        try:
            linked_thesis_id = int(linked_thesis_id)
        except (TypeError, ValueError):
            return _err("linked_thesis_id must be an integer", code=400)

    try:
        from decision_checklist import create_checklist
        result = create_checklist(
            ticker                   = ticker,
            decision_type            = decision_type,
            linked_alpha_candidate_id = body.get("linked_alpha_candidate_id") or None,
            linked_thesis_id         = linked_thesis_id,
            notes                    = notes,
        )
        if not result.get("ok"):
            return _err(f"validation failed: {result.get('errors')}", code=422)
        cache_clear()
        return _ok(result)
    except Exception:
        log.error("POST /decisions/checklists/create error:\n%s", traceback.format_exc())
        return _err("checklist creation failed")


@api_bp.route("/decisions/checklists/<checklist_id>/item", methods=["POST"])
def decisions_checklist_item(checklist_id: str):
    """
    Update a checklist item (pass / fail / reset to null).
    Auth required.
    Body (JSON): item_key, passed (true/false/null), [note]
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)

    body     = request.get_json(silent=True) or {}
    item_key = str(body.get("item_key", "")).strip()
    note     = str(body.get("note", ""))
    passed_raw = body.get("passed")

    passed: object
    if passed_raw is None:
        passed = None
    elif isinstance(passed_raw, bool):
        passed = passed_raw
    else:
        passed = bool(passed_raw)

    try:
        from decision_checklist import update_item
        result = update_item(checklist_id, item_key, passed, note)
        if not result.get("ok"):
            code = 404 if any("NOT_FOUND" in e for e in result.get("errors", [])) else 400
            return _err(result.get("errors"), code=code)
        cache_clear()
        return _ok(result)
    except Exception:
        log.error("POST /decisions/checklists/%s/item error:\n%s",
                  checklist_id, traceback.format_exc())
        return _err("item update failed")


@api_bp.route("/decisions/checklists/<checklist_id>/approve", methods=["POST"])
def decisions_checklist_approve(checklist_id: str):
    """
    Approve a checklist.  Requires readiness = READY_FOR_MANUAL_DECISION.
    Auth required.  Does NOT place any trade.
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)

    body  = request.get_json(silent=True) or {}
    actor = body.get("actor") or None

    try:
        from decision_checklist import approve_checklist
        result = approve_checklist(checklist_id, actor=actor)
        if not result.get("ok"):
            errors = result.get("errors", [])
            code   = 404 if "CHECKLIST_NOT_FOUND" in errors else 422
            return _err(errors, code=code)
        cache_clear()
        return _ok(result)
    except Exception:
        log.error("POST /decisions/checklists/%s/approve error:\n%s",
                  checklist_id, traceback.format_exc())
        return _err("approve failed")


@api_bp.route("/decisions/checklists/<checklist_id>/reject", methods=["POST"])
def decisions_checklist_reject(checklist_id: str):
    """
    Reject a checklist.  Auth required.
    Body (JSON): [reason], [actor]
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)

    body   = request.get_json(silent=True) or {}
    reason = str(body.get("reason", ""))
    actor  = body.get("actor") or None

    try:
        from decision_checklist import reject_checklist
        result = reject_checklist(checklist_id, reason=reason, actor=actor)
        if not result.get("ok"):
            errors = result.get("errors", [])
            code   = 404 if "CHECKLIST_NOT_FOUND" in errors else 422
            return _err(errors, code=code)
        cache_clear()
        return _ok(result)
    except Exception:
        log.error("POST /decisions/checklists/%s/reject error:\n%s",
                  checklist_id, traceback.format_exc())
        return _err("reject failed")


@api_bp.route("/decisions/summary", methods=["GET"])
def decisions_summary():
    """
    Return aggregate counts and pending checklists.
    No auth required (read-only).  TTL 30 s.
    """
    def _build():
        from decision_checklist import get_summary
        return get_summary()

    try:
        payload, cached = _cached("decisions:summary", 30, _build)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /decisions/summary error:\n%s", traceback.format_exc())
        return _err("failed to fetch decisions summary")


# ── Phase A15: portfolio risk and position-sizing endpoints ───────────────────

@api_bp.route("/portfolio/risk", methods=["GET"])
def portfolio_risk():
    """
    Full portfolio risk report: concentration warnings, sizing warnings,
    cash reserve, drawdown, theme exposure, and ticker-level risk table.
    No auth required (read-only).  TTL 60 s.
    """
    def _build():
        from portfolio_risk_guardrails import get_portfolio_risk_report
        return get_portfolio_risk_report()

    try:
        payload, cached = _cached("portfolio:risk", 60, _build)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /portfolio/risk error:\n%s", traceback.format_exc())
        return _err("failed to fetch portfolio risk report")


@api_bp.route("/decisions/size-check", methods=["GET"])
def decisions_size_check():
    """
    Sizing guidance for a specific ticker and decision type.
    No auth required (read-only).  TTL 30 s.
    Required query param: ?ticker=XYZ
    Optional query param: ?decision_type=ENTER (default ENTER)
    Returns sizing guidance, blockers, warnings, and checklist item suggestions.
    """
    ticker        = request.args.get("ticker", "").strip().upper()
    decision_type = request.args.get("decision_type", "ENTER").strip().upper()

    if not ticker:
        return _err("ticker parameter is required", code=400)

    cache_key = f"decisions:size-check:{ticker}:{decision_type}"

    def _build():
        from portfolio_risk_guardrails import get_size_check
        return get_size_check(ticker, decision_type)

    try:
        payload, cached = _cached(cache_key, 30, _build)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /decisions/size-check error:\n%s", traceback.format_exc())
        return _err("failed to compute size check")


# ── Phase A16: market regime intelligence endpoints ───────────────────────────

@api_bp.route("/market/regime", methods=["GET"])
def market_regime():
    """
    Latest market regime snapshot.
    Returns the most recent snapshot, or {data: null} if none have been captured yet.
    No auth required (read-only).  TTL 60 s.
    """
    def _build():
        from market_regime_intelligence import get_latest_regime
        return {"regime": get_latest_regime()}

    try:
        payload, cached = _cached("market:regime:latest", 60, _build)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /market/regime error:\n%s", traceback.format_exc())
        return _err("failed to fetch market regime")


@api_bp.route("/market/regime/history", methods=["GET"])
def market_regime_history():
    """
    Recent market regime snapshots.
    Optional query param: ?limit=N (default 20, max 100).
    No auth required (read-only).  TTL 60 s.
    """
    try:
        limit = min(int(request.args.get("limit", 20)), 100)
    except (TypeError, ValueError):
        limit = 20

    cache_key = f"market:regime:history:{limit}"

    def _build():
        from market_regime_intelligence import get_regime_history
        return {"history": get_regime_history(limit=limit)}

    try:
        payload, cached = _cached(cache_key, 60, _build)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /market/regime/history error:\n%s", traceback.format_exc())
        return _err("failed to fetch regime history")


@api_bp.route("/market/regime/refresh", methods=["POST"])
def market_regime_refresh():
    """
    Trigger a live regime refresh: fetch signals, classify, persist snapshot.
    Auth required.  Clears related caches.  Never sends alerts or trades.
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)

    try:
        from market_regime_intelligence import refresh_regime
        regime = refresh_regime()
        # Bust stale cache entries for regime
        for key in list(_CACHE.keys()):
            if key.startswith("market:regime"):
                _CACHE.pop(key, None)
        return _ok({"regime": regime})
    except Exception:
        log.error("POST /market/regime/refresh error:\n%s", traceback.format_exc())
        return _err("failed to refresh market regime")


# ── Phase A17: historical replay and decision simulator endpoints ──────────────

@api_bp.route("/replay/runs", methods=["GET"])
def replay_runs_list():
    """
    List recent replay runs, newest first.
    Optional query param: ?limit=N (default 20, max 100).
    No auth required (read-only).  TTL 30 s.
    """
    try:
        limit = min(int(request.args.get("limit", 20)), 100)
    except (TypeError, ValueError):
        limit = 20

    cache_key = f"replay:runs:{limit}"

    def _build():
        from historical_replay import get_replay_runs
        return {"runs": get_replay_runs(limit=limit)}

    try:
        payload, cached = _cached(cache_key, 30, _build)
        return _ok(payload, cached=cached)
    except Exception:
        log.error("GET /replay/runs error:\n%s", traceback.format_exc())
        return _err("failed to fetch replay runs")


@api_bp.route("/replay/run", methods=["POST"])
def replay_run_create():
    """
    Create and execute a replay run.
    Auth required.  Clears replay list cache.
    Body (JSON): start_date, end_date, ticker_filter?, source_filter?,
                 setup_type_filter?, max_rows? (default 500, max 2000).
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)

    body = request.get_json(silent=True) or {}

    try:
        from historical_replay import run_replay
        result = run_replay(body)
        # Bust list cache
        for key in list(_CACHE.keys()):
            if key.startswith("replay:runs"):
                _CACHE.pop(key, None)
        return _ok({
            "run_id":      result["run_id"],
            "status":      result["status"],
            "event_count": result["event_count"],
            "summary":     result["summary"],
        })
    except ValueError as exc:
        return _err(str(exc), code=400)
    except Exception:
        log.error("POST /replay/run error:\n%s", traceback.format_exc())
        return _err("failed to execute replay run")


@api_bp.route("/replay/runs/<run_id>", methods=["GET"])
def replay_run_get(run_id: str):
    """
    Get a single replay run with its summary.
    No auth required (read-only).  TTL 30 s.
    """
    cache_key = f"replay:run:{run_id}"

    def _build():
        from historical_replay import get_replay_run
        run = get_replay_run(run_id)
        if run is None:
            raise LookupError(f"replay run not found: {run_id}")
        return {"run": run}

    try:
        payload, cached = _cached(cache_key, 30, _build)
        return _ok(payload, cached=cached)
    except LookupError as exc:
        return _err(str(exc), code=404)
    except Exception:
        log.error("GET /replay/runs/%s error:\n%s", run_id, traceback.format_exc())
        return _err("failed to fetch replay run")


@api_bp.route("/replay/runs/<run_id>/events", methods=["GET"])
def replay_run_events(run_id: str):
    """
    Get events for a replay run.
    Optional query param: ?limit=N (default 200, max 2000).
    No auth required (read-only).  TTL 30 s.
    """
    try:
        limit = min(int(request.args.get("limit", 200)), 2000)
    except (TypeError, ValueError):
        limit = 200

    def _build():
        from historical_replay import get_replay_run, get_replay_events
        run = get_replay_run(run_id)
        if run is None:
            raise LookupError(f"replay run not found: {run_id}")
        return {"events": get_replay_events(run_id, limit=limit)}

    try:
        payload, cached = _cached(f"replay:events:{run_id}:{limit}", 30, _build)
        return _ok(payload, cached=cached)
    except LookupError as exc:
        return _err(str(exc), code=404)
    except Exception:
        log.error("GET /replay/runs/%s/events error:\n%s", run_id, traceback.format_exc())
        return _err("failed to fetch replay events")


# ── Portfolio stress testing (Phase A18) ──────────────────────────────────────

TTL_STRESS = 60  # seconds


@api_bp.route("/portfolio/stress", methods=["GET"])
def portfolio_stress_latest():
    """
    Return the most recent portfolio stress run (with embedded scenario events).
    No auth required.  Cached for TTL_STRESS seconds.
    """
    def _build():
        from portfolio_stress_testing import get_stress_history, get_stress_run
        runs = get_stress_history(limit=1)
        if not runs:
            return {"run": None}
        run = get_stress_run(runs[0]["run_id"])
        return {"run": run}

    try:
        payload, cached = _cached("stress:latest", TTL_STRESS, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /portfolio/stress error:\n%s", traceback.format_exc())
        return _err("failed to fetch latest stress run")


@api_bp.route("/portfolio/stress/run", methods=["POST"])
def portfolio_stress_run():
    """
    Trigger a fresh portfolio stress test.
    Auth-protected (Bearer token matching API_SECRET env var).
    Accepts optional JSON body:
      {
        "custom_scenarios": [{"NVDA": -50.0, "_default": -10.0, "_label": "Bear"}],
        "include_regime":   true
      }
    Returns the full aggregate report.
    Busts the stress:latest cache on success.
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)

    body             = request.get_json(silent=True) or {}
    custom_scenarios = body.get("custom_scenarios")
    include_regime   = bool(body.get("include_regime", False))

    regime_context = None
    if include_regime:
        try:
            from market_regime_intelligence import get_regime_context_for_checklist
            regime_context = get_regime_context_for_checklist()
        except Exception:
            log.warning("portfolio_stress_run: regime context unavailable", exc_info=True)

    try:
        from portfolio_stress_testing import run_stress_test
        report = run_stress_test(
            custom_scenarios=custom_scenarios,
            regime_context=regime_context,
        )
        _CACHE.pop("stress:latest", None)
        return _ok({"report": report})
    except Exception:
        log.error("POST /portfolio/stress/run error:\n%s", traceback.format_exc())
        return _err("stress test failed")


@api_bp.route("/portfolio/stress/history", methods=["GET"])
def portfolio_stress_history():
    """
    Return recent portfolio stress runs (without embedded events), newest first.
    No auth required.  Cached for TTL_STRESS seconds.
    Accepts optional ?limit=N (max 100, default 20).
    """
    try:
        limit = min(int(request.args.get("limit", 20)), 100)
    except (TypeError, ValueError):
        limit = 20

    def _build():
        from portfolio_stress_testing import get_stress_history
        runs = get_stress_history(limit=limit)
        return {"runs": runs, "total": len(runs)}

    try:
        payload, cached = _cached(f"stress:history:{limit}", TTL_STRESS, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /portfolio/stress/history error:\n%s", traceback.format_exc())
        return _err("failed to fetch stress history")


# ── Strategy scorecards (Phase A19) ───────────────────────────────────────────

TTL_SCORECARDS = 120  # seconds — scorecards are relatively expensive to compute


@api_bp.route("/strategies/scorecards", methods=["GET"])
def strategies_scorecards():
    """
    All strategy scorecards with behaviour metrics and recommendations.
    No auth required.  Cached for TTL_SCORECARDS seconds.
    """
    def _build():
        from strategy_scorecards import compute_all_scorecards
        return compute_all_scorecards()

    try:
        payload, cached = _cached("strategies:scorecards", TTL_SCORECARDS, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /strategies/scorecards error:\n%s", traceback.format_exc())
        return _err("failed to compute strategy scorecards")


@api_bp.route("/strategies/summary", methods=["GET"])
def strategies_summary():
    """
    Compact summary: top/bottom 3 strategies, behaviour metrics,
    high-priority recommendations across all strategies.
    No auth required.  Cached for TTL_SCORECARDS seconds.
    """
    def _build():
        from strategy_scorecards import get_scorecards_summary
        return get_scorecards_summary()

    try:
        payload, cached = _cached("strategies:summary", TTL_SCORECARDS, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /strategies/summary error:\n%s", traceback.format_exc())
        return _err("failed to compute strategy summary")


@api_bp.route("/strategies/<strategy>", methods=["GET"])
def strategy_scorecard(strategy: str):
    """
    Single-strategy scorecard.  <strategy> must be one of STRATEGY_TYPES.
    No auth required.  Cached for TTL_SCORECARDS seconds.
    Returns 404 for unknown strategy names.
    """
    def _build():
        from strategy_scorecards import get_scorecard, STRATEGY_TYPES
        if strategy not in STRATEGY_TYPES:
            raise LookupError(f"unknown strategy: {strategy!r}")
        card = get_scorecard(strategy)
        if card is None:
            raise LookupError(f"unknown strategy: {strategy!r}")
        return {"scorecard": card}

    try:
        payload, cached = _cached(f"strategies:single:{strategy}", TTL_SCORECARDS, _build)
        return _ok(payload, cached)
    except LookupError as exc:
        return _err(str(exc), code=404)
    except Exception:
        log.error("GET /strategies/%s error:\n%s", strategy, traceback.format_exc())
        return _err("failed to compute strategy scorecard")


# ── Compounding planner (Phase A20) ───────────────────────────────────────────

TTL_PLANNER = 120  # seconds


@api_bp.route("/planner/summary", methods=["GET"])
def planner_summary():
    """
    Return the most recent planner snapshot (allocation, drift, guidance).
    No auth required.  Cached for TTL_PLANNER seconds.
    Returns {"snapshot": null} when no snapshot has been generated yet.
    """
    def _build():
        from compounding_planner import get_latest_planner_snapshot
        snap = get_latest_planner_snapshot()
        if snap:
            # Exclude heavy projections JSON from the summary endpoint
            snap.pop("projections", None)
        return {"snapshot": snap}

    try:
        payload, cached = _cached("planner:summary", TTL_PLANNER, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /planner/summary error:\n%s", traceback.format_exc())
        return _err("failed to fetch planner summary")


@api_bp.route("/planner/projections", methods=["GET"])
def planner_projections():
    """
    Return the projections section of the most recent planner snapshot.
    No auth required.  Cached for TTL_PLANNER seconds.
    Returns {"projections": null} when no snapshot exists.
    """
    def _build():
        from compounding_planner import get_latest_planner_snapshot
        snap = get_latest_planner_snapshot()
        if snap is None:
            return {"projections": None}
        return {
            "projections":        snap.get("projections"),
            "monthly_contribution": snap.get("monthly_contribution"),
            "portfolio_value":    snap.get("portfolio_value"),
            "created_at":         snap.get("created_at"),
        }

    try:
        payload, cached = _cached("planner:projections", TTL_PLANNER, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /planner/projections error:\n%s", traceback.format_exc())
        return _err("failed to fetch planner projections")


@api_bp.route("/planner/refresh", methods=["POST"])
def planner_refresh():
    """
    Compute a fresh planner run, persist the snapshot, and return the full output.
    Auth-protected (Bearer token matching API_SECRET env var).
    Accepts optional JSON body: {"monthly_contribution": 500.0}
    Busts planner:summary and planner:projections caches on success.
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)

    body = request.get_json(silent=True) or {}
    monthly_contribution = float(body.get("monthly_contribution", 500.0))

    try:
        from compounding_planner import run_planner
        result = run_planner(monthly_contribution=monthly_contribution)
        _CACHE.pop("planner:summary",     None)
        _CACHE.pop("planner:projections", None)
        return _ok({"planner": result})
    except Exception:
        log.error("POST /planner/refresh error:\n%s", traceback.format_exc())
        return _err("planner refresh failed")


# ── Daily operator brief (Phase A21) ──────────────────────────────────────────

TTL_BRIEF = 60  # seconds


@api_bp.route("/brief/daily", methods=["GET"])
def daily_brief():
    """
    Return the daily operator brief.
    Query param ?mode=compact|detailed|debug  (default: detailed).
    No auth required.  Cached for TTL_BRIEF seconds per mode.
    Compact mode returns {"brief": "<text>"}; other modes return the full
    structured dict.  Never crashes — sparse-safe.
    """
    mode = request.args.get("mode", "detailed")
    if mode not in ("compact", "detailed", "debug"):
        mode = "detailed"

    def _build():
        from operator_brief import generate_brief
        result = generate_brief(mode=mode)
        if isinstance(result, str):
            return {"brief": result, "mode": "compact"}
        return result

    try:
        payload, cached = _cached(f"brief:{mode}", TTL_BRIEF, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /brief/daily error:\n%s", traceback.format_exc())
        return _err("failed to generate daily brief")


TTL_EOD_BRIEF = 60  # seconds


@api_bp.route("/brief/eod", methods=["GET"])
def eod_brief():
    """
    Return the end-of-day review brief.
    Query param ?mode=compact|detailed|debug  (default: detailed).
    No auth required.  Cached for TTL_EOD_BRIEF seconds per mode.
    Always responds regardless of EOD_BRIEF_ENABLED flag.
    """
    mode = request.args.get("mode", "detailed")
    if mode not in ("compact", "detailed", "debug"):
        mode = "detailed"

    def _build():
        from eod_brief import generate_eod_brief
        result = generate_eod_brief(mode=mode)
        if isinstance(result, str):
            return {"brief": result, "mode": "compact"}
        return result

    try:
        payload, cached = _cached(f"eod_brief:{mode}", TTL_EOD_BRIEF, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /brief/eod error:\n%s", traceback.format_exc())
        return _err("failed to generate EOD brief")


# ── Phase A23: Market Research endpoints ─────────────────────────────────────

TTL_MARKET_PULSE  = 60   # seconds
TTL_STOCK_ANALYSIS = 300
TTL_ETF_ANALYSIS   = 300
TTL_MACRO          = 600
TTL_NEWS           = 120
TTL_TICKER_NEWS    = 120


@api_bp.route("/market/pulse", methods=["GET"])
def market_pulse():
    """
    Return market pulse: 10 market tickers + 11 sector ETFs.
    Query param ?period=1D|5D|1M|3M|6M|YTD|1Y|3Y|5Y|10Y|Max  (default: 1D).
    No auth required.  Cached per period.
    """
    from market_research import VALID_PERIODS
    period = request.args.get("period", "1D")
    if period not in VALID_PERIODS:
        period = "1D"

    def _build():
        from market_research import get_market_pulse
        return get_market_pulse(period)

    try:
        payload, cached = _cached(f"market_pulse:{period}", TTL_MARKET_PULSE, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /market/pulse error:\n%s", traceback.format_exc())
        return _err("failed to fetch market pulse")


@api_bp.route("/research/sector", methods=["GET"])
def sector_performance():
    """
    Return sector ETF performance sorted by return.
    Query param ?period=  (default: 1D).
    """
    from market_research import VALID_PERIODS
    period = request.args.get("period", "1D")
    if period not in VALID_PERIODS:
        period = "1D"

    def _build():
        from market_research import get_sector_performance
        return get_sector_performance(period)

    try:
        payload, cached = _cached(f"sector_perf:{period}", TTL_MARKET_PULSE, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /research/sector error:\n%s", traceback.format_exc())
        return _err("failed to fetch sector performance")


@api_bp.route("/research/stock/<ticker>", methods=["GET"])
def stock_analysis(ticker: str):
    """
    Return technical + fundamental analysis for a stock ticker.
    Query param ?period=  (default: 1Y).
    No auth required.  Cached per ticker+period.
    """
    from market_research import VALID_PERIODS
    ticker = ticker.upper().strip()
    period = request.args.get("period", "1Y")
    if period not in VALID_PERIODS:
        period = "1Y"

    def _build():
        from market_research import get_stock_analysis
        return get_stock_analysis(ticker, period)

    try:
        payload, cached = _cached(f"stock_analysis:{ticker}:{period}", TTL_STOCK_ANALYSIS, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /research/stock/%s error:\n%s", ticker, traceback.format_exc())
        return _err("failed to fetch stock analysis")


@api_bp.route("/research/etf/<ticker>", methods=["GET"])
def etf_analysis(ticker: str):
    """
    Return ETF analysis: returns table, risk score, holdings, peers.
    Query param ?period=  (default: 1Y).
    No auth required.  Cached per ticker+period.
    """
    from market_research import VALID_PERIODS
    ticker = ticker.upper().strip()
    period = request.args.get("period", "1Y")
    if period not in VALID_PERIODS:
        period = "1Y"

    def _build():
        from market_research import get_etf_analysis
        return get_etf_analysis(ticker, period)

    try:
        payload, cached = _cached(f"etf_analysis:{ticker}:{period}", TTL_ETF_ANALYSIS, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /research/etf/%s error:\n%s", ticker, traceback.format_exc())
        return _err("failed to fetch ETF analysis")


@api_bp.route("/research/macro", methods=["GET"])
def macro_data():
    """
    Return macro indicators from FRED.
    Returns available=false when FRED_API_KEY is not configured or fredapi not installed.
    No auth required.  Cached TTL_MACRO seconds.
    """
    def _build():
        from market_research import get_macro_data
        return get_macro_data()

    try:
        payload, cached = _cached("macro_data", TTL_MACRO, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /research/macro error:\n%s", traceback.format_exc())
        return _err("failed to fetch macro data")


@api_bp.route("/research/news", methods=["GET"])
def market_news():
    """
    Return recent market news (SPY proxy).
    No auth required.  Cached TTL_NEWS seconds.
    """
    def _build():
        from market_research import get_market_news
        return get_market_news()

    try:
        payload, cached = _cached("market_news", TTL_NEWS, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /research/news error:\n%s", traceback.format_exc())
        return _err("failed to fetch market news")


@api_bp.route("/research/news/<ticker>", methods=["GET"])
def ticker_news(ticker: str):
    """
    Return recent news for a specific ticker.
    No auth required.  Cached per ticker.
    """
    ticker = ticker.upper().strip()

    def _build():
        from market_research import get_ticker_news
        return get_ticker_news(ticker)

    try:
        payload, cached = _cached(f"ticker_news:{ticker}", TTL_TICKER_NEWS, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /research/news/%s error:\n%s", ticker, traceback.format_exc())
        return _err("failed to fetch ticker news")


@api_bp.route("/research/ai/stock/<ticker>", methods=["POST"])
def ai_stock_analysis(ticker: str):
    """
    Generate educational AI commentary for a stock.
    No auth required.  Not cached (POST).
    """
    ticker = ticker.upper().strip()
    try:
        body = request.get_json(silent=True) or {}
        from market_research import get_stock_analysis, generate_stock_analysis_ai
        analysis_data = body.get("analysis_data") or get_stock_analysis(ticker)
        result = generate_stock_analysis_ai(ticker, analysis_data)
        return _ok(result)
    except Exception:
        log.error("POST /research/ai/stock/%s error:\n%s", ticker, traceback.format_exc())
        return _err("failed to generate AI stock analysis")


@api_bp.route("/research/ai/etf/<ticker>", methods=["POST"])
def ai_etf_analysis(ticker: str):
    """
    Generate educational AI commentary for an ETF.
    No auth required.  Not cached (POST).
    """
    ticker = ticker.upper().strip()
    try:
        body = request.get_json(silent=True) or {}
        from market_research import get_etf_analysis, generate_etf_analysis_ai
        analysis_data = body.get("analysis_data") or get_etf_analysis(ticker)
        result = generate_etf_analysis_ai(ticker, analysis_data)
        return _ok(result)
    except Exception:
        log.error("POST /research/ai/etf/%s error:\n%s", ticker, traceback.format_exc())
        return _err("failed to generate AI ETF analysis")


@api_bp.route("/research/ai/macro", methods=["POST"])
def ai_macro_analysis():
    """
    Generate educational AI commentary for macro conditions.
    No auth required.  Not cached (POST).
    """
    try:
        body = request.get_json(silent=True) or {}
        from market_research import get_macro_data, generate_macro_analysis_ai
        macro = body.get("macro_data") or get_macro_data()
        result = generate_macro_analysis_ai(macro)
        return _ok(result)
    except Exception:
        log.error("POST /research/ai/macro error:\n%s", traceback.format_exc())
        return _err("failed to generate AI macro analysis")


# ── Phase A24: Research Watchlist endpoints ────────────────────────────────────

TTL_WATCHLIST     = 30   # seconds — short TTL since writes invalidate state
TTL_SUGGESTIONS   = 120  # suggestions are heavier to compute


@api_bp.route("/research/watchlist", methods=["GET"])
def research_watchlist():
    """
    Return all active watchlist items (excludes ARCHIVED/PAUSED by default).
    Query params:
      ?include_archived=1  — include ARCHIVED items
      ?include_paused=1    — include PAUSED items
      ?status=             — filter by status
      ?priority=           — filter by priority
    No auth required.  Short TTL cache.
    """
    include_archived = request.args.get("include_archived", "0") in ("1", "true", "yes")
    include_paused   = request.args.get("include_paused", "0") in ("1", "true", "yes")
    status_filter    = request.args.get("status", "")
    priority_filter  = request.args.get("priority", "")

    cache_key = f"watchlist:{include_archived}:{include_paused}:{status_filter}:{priority_filter}"

    def _build():
        from research_watchlist import get_watchlist
        items = get_watchlist(
            include_archived=include_archived,
            include_paused=include_paused,
            status=status_filter or None,
            priority=priority_filter or None,
        )
        return {"items": items, "count": len(items)}

    try:
        payload, cached = _cached(cache_key, TTL_WATCHLIST, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /research/watchlist error:\n%s", traceback.format_exc())
        return _err("failed to fetch watchlist")


@api_bp.route("/research/watchlist/suggestions", methods=["GET"])
def watchlist_suggestions():
    """
    Return auto-generated watchlist suggestions from all sources.
    No auth required.  Cached TTL_SUGGESTIONS seconds.
    """
    def _build():
        from research_watchlist import generate_suggestions
        return generate_suggestions()

    try:
        payload, cached = _cached("watchlist_suggestions", TTL_SUGGESTIONS, _build)
        return _ok(payload, cached)
    except Exception:
        log.error("GET /research/watchlist/suggestions error:\n%s", traceback.format_exc())
        return _err("failed to generate watchlist suggestions")


@api_bp.route("/research/watchlist/<ticker>", methods=["GET"])
def watchlist_item(ticker: str):
    """
    Return a single watchlist item with its recent notes.
    Returns 404 if ticker not on watchlist.
    No auth required.
    """
    ticker = ticker.upper().strip()
    try:
        from research_watchlist import get_item_with_notes
        item = get_item_with_notes(ticker)
        if item is None:
            return _err(f"ticker {ticker!r} not found in watchlist", code=404)
        return _ok(item)
    except Exception:
        log.error("GET /research/watchlist/%s error:\n%s", ticker, traceback.format_exc())
        return _err("failed to fetch watchlist item")


@api_bp.route("/research/watchlist/upsert", methods=["POST"])
def watchlist_upsert():
    """
    Insert or update a watchlist item.
    Auth-protected (Bearer token matching API_SECRET env var).
    Body JSON: {ticker, name?, asset_type?, category?, status?, priority?,
                reason?, linked_alpha_candidate_id?, linked_thesis_id?,
                next_review_at?}
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)

    body = request.get_json(silent=True) or {}
    ticker = (body.get("ticker") or "").strip().upper()
    if not ticker:
        return _err("ticker is required", code=400)

    try:
        from research_watchlist import upsert_item
        item = upsert_item(
            ticker,
            name=body.get("name"),
            asset_type=body.get("asset_type", "STOCK"),
            category=body.get("category", "LEARNING"),
            status=body.get("status", "WATCHING"),
            priority=body.get("priority", "MEDIUM"),
            reason=body.get("reason", ""),
            linked_alpha_candidate_id=body.get("linked_alpha_candidate_id"),
            linked_thesis_id=body.get("linked_thesis_id"),
            next_review_at=body.get("next_review_at"),
        )
        cache_clear()
        return _ok(item)
    except ValueError as exc:
        return _err(str(exc), code=400)
    except Exception:
        log.error("POST /research/watchlist/upsert error:\n%s", traceback.format_exc())
        return _err("failed to upsert watchlist item")


@api_bp.route("/research/watchlist/<ticker>/note", methods=["POST"])
def watchlist_add_note(ticker: str):
    """
    Append a note to a watchlist item (append-only).
    Auth-protected.
    Body JSON: {text, note_type?, tags?}
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)

    ticker = ticker.upper().strip()
    body   = request.get_json(silent=True) or {}
    text   = (body.get("text") or "").strip()
    if not text:
        return _err("note text is required", code=400)

    try:
        from research_watchlist import append_note
        note = append_note(
            ticker,
            text,
            note_type=body.get("note_type", "OTHER"),
            tags=body.get("tags"),
        )
        cache_clear()
        return _ok(note)
    except ValueError as exc:
        return _err(str(exc), code=400)
    except Exception:
        log.error("POST /research/watchlist/%s/note error:\n%s", ticker, traceback.format_exc())
        return _err("failed to append note")


@api_bp.route("/research/watchlist/<ticker>/archive", methods=["POST"])
def watchlist_archive(ticker: str):
    """
    Archive a watchlist item (no deletes — archive only).
    Auth-protected.
    """
    if not _check_alpha_auth():
        return _err("unauthorized", code=401)

    ticker = ticker.upper().strip()
    try:
        from research_watchlist import archive_item
        item = archive_item(ticker)
        cache_clear()
        return _ok(item)
    except ValueError as exc:
        return _err(str(exc), code=404)
    except Exception:
        log.error("POST /research/watchlist/%s/archive error:\n%s", ticker, traceback.format_exc())
        return _err("failed to archive watchlist item")
