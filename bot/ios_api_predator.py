"""
Predator API endpoints — pre-explosion alert system.

Registered under /api/predator prefix.
"""
import json
import logging
import threading
from datetime import datetime, timedelta

from flask import Blueprint, jsonify

from database import get_connection

log = logging.getLogger(__name__)

DEBUG_TICKERS = ["NVDA", "SOXL", "PLTR", "AMD", "IONQ"]

predator_bp = Blueprint("predator", __name__, url_prefix="/api/predator")


def _format_scored(result: dict) -> dict:
    """Shape a raw _score_ticker result for the run-now / debug endpoints."""
    signals = result.get("signals", {})
    return {
        "ticker":      result["ticker"],
        "total_score": result["score"],
        "price":       result.get("price"),
        "signals": {
            key: {"score": sig.get("score", 0), "reason": sig.get("reason", "")}
            for key, sig in signals.items()
        },
    }


def _parse_row(row: dict) -> dict:
    signals = {}
    try:
        signals = json.loads(row["signals_json"]) if row["signals_json"] else {}
    except (json.JSONDecodeError, TypeError):
        pass
    return {
        "id":               row["id"],
        "ticker":           row["ticker"],
        "score":            row["score"],
        "signals":          signals,
        "entry_price":      row["entry_price"],
        "stop_price":       row["stop_price"],
        "position_size_cad": row["position_size_cad"],
        "alert_time":       row["alert_time"],
        "price_7d_later":   row["price_7d_later"],
        "price_14d_later":  row["price_14d_later"],
        "price_30d_later":  row["price_30d_later"],
        "outcome":          row["outcome"],
    }


@predator_bp.route("/alerts", methods=["GET"])
def get_alerts():
    """Last 48 hours of pre-explosion alerts (score >= 8 only)."""
    cutoff = (datetime.now() - timedelta(hours=48)).isoformat()
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM predator_alerts WHERE alert_time >= ? AND score >= 8 ORDER BY alert_time DESC",
        (cutoff,),
    ).fetchall()
    conn.close()
    return jsonify({"alerts": [_parse_row(dict(r)) for r in rows]})


@predator_bp.route("/watchlist", methods=["GET"])
def get_watchlist():
    """Latest score for each watched ticker (last 7 days, one record per ticker)."""
    from predator import PREDATOR_WATCHLIST
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    conn = get_connection()
    # Get the most recent record per ticker
    rows = conn.execute(
        """
        SELECT p.*
        FROM predator_alerts p
        INNER JOIN (
            SELECT ticker, MAX(alert_time) AS latest
            FROM predator_alerts
            WHERE alert_time >= ?
            GROUP BY ticker
        ) m ON p.ticker = m.ticker AND p.alert_time = m.latest
        ORDER BY p.score DESC
        """,
        (cutoff,),
    ).fetchall()
    conn.close()

    # Ensure all watchlist tickers are present
    scored = {r["ticker"]: _parse_row(dict(r)) for r in rows}
    result = []
    for ticker in PREDATOR_WATCHLIST:
        if ticker in scored:
            result.append(scored[ticker])
        else:
            result.append({
                "ticker": ticker, "score": None, "signals": {},
                "entry_price": None, "stop_price": None,
                "position_size_cad": None, "alert_time": None,
                "price_7d_later": None, "price_14d_later": None,
                "price_30d_later": None, "outcome": None, "id": None,
            })

    return jsonify({"watchlist": result})


@predator_bp.route("/run-now", methods=["GET"])
def run_now():
    """Start a full background scan; returns immediately.

    Results are saved to the DB and readable via /api/predator/latest.
    """
    from predator import PREDATOR_WATCHLIST, save_scan_results, score_all_tickers

    started_at = datetime.now().isoformat()

    def _bg():
        log.info("Background predator scan started")
        results = score_all_tickers()
        save_scan_results(results)
        log.info("Background predator scan complete: %d tickers scored", len(results))

    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({
        "status":     "scan started",
        "tickers":    len(PREDATOR_WATCHLIST),
        "started_at": started_at,
        "results_at": "/api/predator/latest",
    })


@predator_bp.route("/debug", methods=["GET"])
def debug_scan():
    """Score 5 fast-path tickers synchronously; full signal breakdown returned immediately."""
    from predator import score_tickers
    started_at = datetime.now().isoformat()
    raw = score_tickers(DEBUG_TICKERS)
    return jsonify({"results": [_format_scored(r) for r in raw], "scanned_at": started_at})


@predator_bp.route("/latest", methods=["GET"])
def get_latest():
    """Most recent scan result per ticker from the DB — no computation."""
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT p.*
        FROM predator_alerts p
        INNER JOIN (
            SELECT ticker, MAX(alert_time) AS latest
            FROM predator_alerts
            WHERE alert_time >= ?
            GROUP BY ticker
        ) m ON p.ticker = m.ticker AND p.alert_time = m.latest
        ORDER BY p.score DESC
        """,
        (cutoff,),
    ).fetchall()
    conn.close()

    results = []
    for row in rows:
        d = dict(row)
        signals = {}
        try:
            signals = json.loads(d["signals_json"]) if d["signals_json"] else {}
        except (json.JSONDecodeError, TypeError):
            pass
        results.append({
            "ticker":      d["ticker"],
            "total_score": d["score"],
            "price":       d["entry_price"],
            "signals": {
                k: {"score": s.get("score", 0), "reason": s.get("reason", "")}
                for k, s in signals.items()
            },
            "alert_time":  d["alert_time"],
        })

    return jsonify({"results": results, "total": len(results)})


@predator_bp.route("/history", methods=["GET"])
def get_history():
    """All-time alert history with outcome tracking and accuracy stats."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM predator_alerts WHERE score >= 8 ORDER BY alert_time DESC LIMIT 100",
    ).fetchall()
    conn.close()

    alerts = [_parse_row(dict(r)) for r in rows]

    # Accuracy stats
    resolved_7d  = [a for a in alerts if a["price_7d_later"] and a["entry_price"]]
    resolved_14d = [a for a in alerts if a["price_14d_later"] and a["entry_price"]]

    def accuracy(items, price_key):
        if not items:
            return None
        wins = sum(1 for a in items if a[price_key] and a["entry_price"]
                   and a[price_key] > a["entry_price"])
        return round(wins / len(items), 2)

    return jsonify({
        "history":      alerts,
        "accuracy_7d":  accuracy(resolved_7d, "price_7d_later"),
        "accuracy_14d": accuracy(resolved_14d, "price_14d_later"),
        "total_alerts": len(alerts),
    })
