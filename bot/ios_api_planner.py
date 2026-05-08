"""
Paycheck Investment Planner API.
Blueprint registered on the main Flask app at /api/planner.
"""
import logging
import os
import traceback
from datetime import datetime, date
from calendar import monthrange

import anthropic
import pytz
from flask import Blueprint, jsonify, request

import portfolio as port
from database import get_connection
from ios_api import _require_auth

log = logging.getLogger(__name__)
planner_bp = Blueprint("planner", __name__, url_prefix="/api/planner")
EASTERN = pytz.timezone("America/Toronto")


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _get_config() -> dict:
    conn = get_connection()
    row = conn.execute("SELECT * FROM planner_config WHERE id = 1").fetchone()
    conn.close()
    if row:
        return dict(row)
    return {
        "paycheck_amount": 0,
        "paycheck_day": 15,
        "allocation_percent": 100,
        "last_updated": datetime.now().isoformat(),
    }


def _next_paycheck_date(paycheck_day: int) -> date:
    """Return the next upcoming date for the given day-of-month."""
    today = date.today()
    # Clamp day to month length
    max_day = monthrange(today.year, today.month)[1]
    day = min(paycheck_day, max_day)
    candidate = today.replace(day=day)
    if candidate <= today:
        # Move to next month
        if today.month == 12:
            candidate = date(today.year + 1, 1, min(paycheck_day, 31))
        else:
            next_month = today.month + 1
            max_next = monthrange(today.year, next_month)[1]
            candidate = date(today.year, next_month, min(paycheck_day, max_next))
    return candidate


# ─────────────────────────────────────────────
# POST /api/planner/setup
# ─────────────────────────────────────────────

@planner_bp.route("/setup", methods=["POST"])
@_require_auth
def planner_setup():
    try:
        body = request.get_json(force=True, silent=True) or {}
        paycheck_amount = float(body.get("paycheck_amount", 0))
        paycheck_day = int(body.get("paycheck_day", 15))
        allocation_percent = float(body.get("allocation_percent", 100))

        if paycheck_amount <= 0:
            return jsonify({"success": False, "error": "paycheck_amount must be > 0"}), 400
        if not 1 <= paycheck_day <= 31:
            return jsonify({"success": False, "error": "paycheck_day must be 1–31"}), 400
        if not 1 <= allocation_percent <= 100:
            return jsonify({"success": False, "error": "allocation_percent must be 1–100"}), 400

        conn = get_connection()
        conn.execute(
            "UPDATE planner_config SET paycheck_amount=?, paycheck_day=?, allocation_percent=?, last_updated=? WHERE id=1",
            (paycheck_amount, paycheck_day, allocation_percent, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

        next_date = _next_paycheck_date(paycheck_day)
        deployable = round(paycheck_amount * allocation_percent / 100, 2)

        log.info("planner/setup: $%.2f on day %d, %.0f%% = $%.2f deployable",
                 paycheck_amount, paycheck_day, allocation_percent, deployable)
        return jsonify({
            "success": True,
            "paycheck_amount": paycheck_amount,
            "paycheck_day": paycheck_day,
            "allocation_percent": allocation_percent,
            "next_paycheck_date": next_date.isoformat(),
            "deployable_amount": deployable,
        })
    except Exception:
        log.error("POST /api/planner/setup error:\n%s", traceback.format_exc())
        return jsonify({"success": False, "error": "Failed to save planner config"}), 500


# ─────────────────────────────────────────────
# GET /api/planner/next-deployment
# ─────────────────────────────────────────────

@planner_bp.route("/next-deployment", methods=["GET"])
@_require_auth
def planner_next_deployment():
    try:
        config = _get_config()
        paycheck_amount = config["paycheck_amount"]
        paycheck_day = config["paycheck_day"]
        allocation_percent = config["allocation_percent"]
        deployable = round(paycheck_amount * allocation_percent / 100, 2)

        next_date = _next_paycheck_date(paycheck_day)
        days_until = (next_date - date.today()).days

        # Build portfolio context for Claude
        holdings = port.get_holdings()
        cash = port.get_cash()
        tickers_held = [h["ticker"] for h in holdings]

        if paycheck_amount <= 0:
            return jsonify({
                "configured": False,
                "message": "Set up your paycheck schedule in Settings first.",
                "next_paycheck_date": None,
                "days_until_paycheck": None,
                "deployable_amount": 0,
                "recommendations": [],
            })

        # Ask Claude for deployment recommendations
        api_key = os.getenv("ANTHROPIC_API_KEY")
        recommendations = []

        if api_key and deployable > 0:
            client = anthropic.Anthropic(api_key=api_key)
            holdings_summary = "\n".join(
                f"  • {h['ticker']}: {h['shares']:.4f} shares @ ${h['avg_cost']:.2f} CAD avg"
                for h in holdings
            ) or "  (none)"

            prompt = (
                f"You are an investment advisor for a Canadian TFSA (Tax-Free Savings Account).\n\n"
                f"Current portfolio:\n{holdings_summary}\n"
                f"Available cash: ${cash:.2f} CAD\n\n"
                f"Upcoming paycheck: ${paycheck_amount:.2f} CAD in {days_until} days\n"
                f"Amount to deploy: ${deployable:.2f} CAD ({allocation_percent:.0f}% of paycheck)\n\n"
                f"Suggest 2-3 specific buy recommendations for deploying ${deployable:.2f} CAD.\n"
                f"Focus on: existing holdings that are underweight, high-conviction additions, "
                f"or diversification opportunities.\n"
                f"Format each recommendation as:\n"
                f"TICKER | $AMOUNT | One-line rationale\n\n"
                f"Be concise. Max 3 recommendations."
            )

            try:
                resp = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=400,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = resp.content[0].text.strip()
                for line in raw.splitlines():
                    line = line.strip()
                    if "|" in line and len(line.split("|")) >= 3:
                        parts = [p.strip() for p in line.split("|")]
                        try:
                            amount_str = parts[1].replace("$", "").replace(",", "").strip()
                            recommendations.append({
                                "ticker": parts[0].upper(),
                                "amount": float(amount_str),
                                "rationale": parts[2],
                            })
                        except (ValueError, IndexError):
                            continue
            except Exception as e:
                log.warning("planner Claude call failed: %s", e)

        return jsonify({
            "configured": True,
            "paycheck_amount": paycheck_amount,
            "paycheck_day": paycheck_day,
            "allocation_percent": allocation_percent,
            "next_paycheck_date": next_date.isoformat(),
            "days_until_paycheck": days_until,
            "deployable_amount": deployable,
            "existing_tickers": tickers_held,
            "recommendations": recommendations,
        })
    except Exception:
        log.error("GET /api/planner/next-deployment error:\n%s", traceback.format_exc())
        return jsonify({"error": "Failed to get deployment plan"}), 500
