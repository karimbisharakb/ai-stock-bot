"""
Predator API endpoints — pre-explosion alert system.

Registered under /api/predator prefix.
"""
import json
from datetime import datetime, timedelta

from flask import Blueprint, jsonify

from database import get_connection

predator_bp = Blueprint("predator", __name__, url_prefix="/api/predator")


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
    """Immediately run a full predator scan and return every ticker result."""
    from predator import run_predator
    started_at = datetime.now().isoformat()
    run_predator()
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM predator_alerts WHERE alert_time >= ? ORDER BY score DESC",
        (started_at,),
    ).fetchall()
    conn.close()
    return jsonify({"results": [_parse_row(dict(r)) for r in rows], "scanned_at": started_at})


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
