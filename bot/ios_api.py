"""
iOS app REST API — Blueprint registered on the main Flask app.
All heavy lifting delegates to existing portfolio, market_data, analyst,
scanner, and database modules. No auth (personal sideloaded app).
"""
import json
import logging
import os
import traceback
from datetime import datetime, timedelta

import anthropic
import pytz
import yfinance as yf
from flask import Blueprint, jsonify, request

import market_data as md
import portfolio as port
from database import get_connection

log = logging.getLogger(__name__)
ios = Blueprint("ios", __name__, url_prefix="/api")

EASTERN = pytz.timezone("America/New_York")


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _usdcad() -> float:
    try:
        from strategy import get_usd_cad_rate
        return get_usd_cad_rate()
    except Exception:
        return 1.38


def _market_status() -> str:
    now = datetime.now(EASTERN)
    if now.weekday() >= 5:
        return "closed"
    t = now.time()
    from datetime import time
    if time(9, 30) <= t <= time(16, 0):
        return "open"
    if time(4, 0) <= t < time(9, 30):
        return "pre-market"
    return "after-hours"


def _history_points() -> list:
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT date, value_cad FROM portfolio_history ORDER BY date DESC LIMIT 30"
        ).fetchall()
        conn.close()
        return [{"date": r["date"], "value_cad": r["value_cad"]} for r in reversed(rows)]
    except Exception:
        return []


def _parse_scores_from_text(text: str) -> tuple[int, int, int]:
    """Pull Overall/Growth/Risk scores from the WhatsApp-style Claude analysis."""
    import re
    overall = growth = risk = 50
    for line in text.splitlines():
        m = re.search(r"Overall.*?(\d+)/100", line)
        if m:
            overall = int(m.group(1))
        m = re.search(r"Growth.*?(\d+)/100", line)
        if m:
            growth = int(m.group(1))
        m = re.search(r"Risk.*?(\d+)/100", line)
        if m:
            risk = int(m.group(1))
    return overall, growth, risk


def _indicators_from_ticker_data(data: dict) -> list:
    if not data:
        return []
    rsi = data.get("rsi", 50)
    macd_hist = data.get("macd_hist", 0)
    vol_ratio = data.get("vol_ratio", 1.0)
    pct_1d = data.get("pct_1d", 0)
    ma50 = data.get("ma50")
    price = data.get("price", 0)

    return [
        {
            "name": "RSI (14)",
            "value": str(rsi),
            "passed": 40 <= rsi <= 65,
            "detail": "Overbought" if rsi > 70 else ("Oversold" if rsi < 30 else "Neutral"),
        },
        {
            "name": "MACD",
            "value": f"{macd_hist:+.3f}",
            "passed": macd_hist > 0,
            "detail": "Bullish histogram" if macd_hist > 0 else "Bearish histogram",
        },
        {
            "name": "Volume",
            "value": f"{vol_ratio:.2f}x avg",
            "passed": vol_ratio >= 1.0,
            "detail": "Above average volume" if vol_ratio >= 1.0 else "Below average",
        },
        {
            "name": "Day change",
            "value": f"{pct_1d:+.2f}%",
            "passed": pct_1d >= 0,
            "detail": None,
        },
        {
            "name": "50-day MA",
            "value": f"${ma50:.2f}" if ma50 else "N/A",
            "passed": bool(ma50 and price >= ma50),
            "detail": "Above MA50" if (ma50 and price >= ma50) else "Below MA50",
        },
    ]


# ─────────────────────────────────────────────
# GET /api/portfolio
# ─────────────────────────────────────────────

@ios.route("/portfolio", methods=["GET"])
def get_portfolio():
    try:
        fx = _usdcad()
        cash = port.get_cash()
        holdings_raw = port.get_holdings()

        holdings_out = []
        total_cad = cash
        total_cost_cad = 0.0
        daily_value_now = 0.0
        daily_value_prev = 0.0

        for h in holdings_raw:
            ticker = h["ticker"]
            shares = h["shares"]
            avg_cost_cad = h["avg_cost"]
            is_canadian = ticker.upper().endswith(".TO")

            data = md.get_ticker_data(ticker)
            if data:
                price_native = data["price"]
                closes = data["closes"]
                prev_native = float(closes.iloc[-2]) if len(closes) >= 2 else price_native
            else:
                price_native = avg_cost_cad if is_canadian else avg_cost_cad / fx
                prev_native = price_native

            if is_canadian:
                current_price_cad = price_native
                current_price_usd = None
                prev_price_cad = prev_native
            else:
                current_price_usd = price_native
                current_price_cad = round(price_native * fx, 4)
                prev_price_cad = round(prev_native * fx, 4)

            total_value_cad = round(shares * current_price_cad, 2)
            cost_basis_cad = round(shares * avg_cost_cad, 2)
            gain_loss_cad = round(total_value_cad - cost_basis_cad, 2)
            gain_loss_pct = round((gain_loss_cad / cost_basis_cad) * 100, 2) if cost_basis_cad else 0.0

            daily_value_now += shares * current_price_cad
            daily_value_prev += shares * prev_price_cad
            total_cad += total_value_cad
            total_cost_cad += cost_basis_cad

            holdings_out.append({
                "ticker": ticker,
                "shares": shares,
                "avg_cost_cad": avg_cost_cad,
                "current_price_cad": current_price_cad,
                "current_price_usd": current_price_usd,
                "currency": "CAD" if is_canadian else "USD",
                "total_value_cad": total_value_cad,
                "gain_loss_cad": gain_loss_cad,
                "gain_loss_percent": gain_loss_pct,
                "is_canadian": is_canadian,
            })

        daily_pnl = round(daily_value_now - daily_value_prev, 2)
        daily_pnl_pct = round((daily_pnl / daily_value_prev) * 100, 2) if daily_value_prev else 0.0
        all_time_gain = round(total_cad - cash - total_cost_cad, 2)
        all_time_pct = round((all_time_gain / total_cost_cad) * 100, 2) if total_cost_cad else 0.0

        return jsonify({
            "total_value_cad": round(total_cad, 2),
            "daily_pnl": daily_pnl,
            "daily_pnl_percent": daily_pnl_pct,
            "all_time_gain": all_time_gain,
            "all_time_gain_percent": all_time_pct,
            "available_cash": round(cash, 2),
            "holdings": holdings_out,
            "history_points": _history_points(),
        })
    except Exception:
        log.error("GET /api/portfolio error:\n%s", traceback.format_exc())
        return jsonify({"error": "Failed to load portfolio"}), 500


# ─────────────────────────────────────────────
# GET /api/market
# ─────────────────────────────────────────────

@ios.route("/market", methods=["GET"])
def get_market():
    try:
        fx = _usdcad()

        def _safe_price(sym):
            try:
                return round(float(yf.Ticker(sym).fast_info.last_price or 0), 2)
            except Exception:
                return 0.0

        def _safe_change(sym):
            try:
                hist = yf.Ticker(sym).history(period="2d")["Close"]
                if len(hist) >= 2:
                    return round((hist.iloc[-1] / hist.iloc[-2] - 1) * 100, 2)
            except Exception:
                pass
            return 0.0

        return jsonify({
            "sp500_price":    _safe_price("^GSPC"),
            "sp500_change":   _safe_change("^GSPC"),
            "tsx_price":      _safe_price("^GSPTSE"),
            "tsx_change":     _safe_change("^GSPTSE"),
            "nasdaq_price":   _safe_price("^IXIC"),
            "nasdaq_change":  _safe_change("^IXIC"),
            "vix":            _safe_price("^VIX"),
            "usd_cad_rate":   round(fx, 4),
            "market_status":  _market_status(),
        })
    except Exception:
        log.error("GET /api/market error:\n%s", traceback.format_exc())
        return jsonify({
            "sp500_price": 0, "sp500_change": 0,
            "tsx_price": 0, "tsx_change": 0,
            "nasdaq_price": 0, "nasdaq_change": 0,
            "vix": 20.0, "usd_cad_rate": 1.38,
            "market_status": "unknown",
        })


# ─────────────────────────────────────────────
# GET /api/signals
# ─────────────────────────────────────────────

@ios.route("/signals", methods=["GET"])
def get_signals():
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, ticker, score, sent_at, reason "
            "FROM scanner_alerts ORDER BY sent_at DESC LIMIT 60"
        ).fetchall()
        conn.close()

        signals = []
        for r in rows:
            score = r["score"] or 0
            confidence = min(int(score * 10), 99)
            signals.append({
                "id": str(r["id"]),
                "ticker": r["ticker"],
                "direction": "Buy",
                "confidence": confidence,
                "verdict": "CONFIRMED" if score >= 7 else "PENDING",
                "timestamp": r["sent_at"],
                "indicators": [],
                "reasoning": r["reason"] or "",
                "price_at_signal": None,
                "price_after_3d": None,
                "price_after_7d": None,
            })

        return jsonify({"signals": signals})
    except Exception:
        log.error("GET /api/signals error:\n%s", traceback.format_exc())
        return jsonify({"signals": []})


# ─────────────────────────────────────────────
# GET /api/opportunities
# ─────────────────────────────────────────────

@ios.route("/opportunities", methods=["GET"])
def get_opportunities():
    try:
        conn = get_connection()
        # 24-hour window so stale alerts don't show forever
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        rows = conn.execute(
            "SELECT id, ticker, score, sent_at, reason "
            "FROM scanner_alerts "
            "WHERE score >= 7 AND sent_at >= ? "
            "ORDER BY sent_at DESC LIMIT 20",
            (cutoff,),
        ).fetchall()
        conn.close()

        fx = _usdcad()
        opportunities = []

        for r in rows:
            ticker = r["ticker"]
            is_canadian = ticker.upper().endswith(".TO")
            score = r["score"] or 7
            confidence = min(int(score * 10), 99)

            data = md.get_ticker_data(ticker)
            entry_price = data["price"] if data else 0.0
            indicators = _indicators_from_ticker_data(data)

            # Suggest ~$1 000 CAD position
            suggested_cad = 1000.0

            opportunities.append({
                "id": str(r["id"]),
                "ticker": ticker,
                "catalyst": (r["reason"] or "").split("|")[0].strip() or f"AI score {score}/10",
                "confidence": confidence,
                "entry_price": round(entry_price, 2),
                "currency": "CAD" if is_canadian else "USD",
                "suggested_position_cad": suggested_cad,
                "catalyst_detail": r["reason"] or "",
                "risk_factors": ["Market volatility", "Sector rotation risk"],
                "claude_reasoning": "",
                "indicators": indicators,
                "outcome_3d": None,
                "outcome_7d": None,
                "timestamp": r["sent_at"],
            })

        return jsonify({"opportunities": opportunities})
    except Exception:
        log.error("GET /api/opportunities error:\n%s", traceback.format_exc())
        return jsonify({"opportunities": []})


# ─────────────────────────────────────────────
# POST /api/analyze
# ─────────────────────────────────────────────

@ios.route("/analyze", methods=["POST"])
def api_analyze():
    try:
        body = request.get_json(force=True, silent=True) or {}
        ticker = body.get("ticker", "").upper().strip()
        if not ticker:
            return jsonify({"error": "ticker required"}), 400

        # Reuse the existing WhatsApp analyst (returns formatted text)
        from analyst import analyze_stock, _fetch_metrics
        raw_text = analyze_stock(ticker)
        overall, growth, risk = _parse_scores_from_text(raw_text)

        # Also pull live technicals for the metric grid
        m = _fetch_metrics(ticker)
        data = md.get_ticker_data(ticker)

        metrics = [
            {"label": "P/E",          "value": m.get("pe", "N/A"),          "rating": _pe_rating(m.get("pe"))},
            {"label": "Fwd P/E",      "value": m.get("fwd_pe", "N/A"),      "rating": _pe_rating(m.get("fwd_pe"))},
            {"label": "Rev Growth",   "value": m.get("rev_growth", "N/A"),   "rating": _pct_rating(m.get("rev_growth"))},
            {"label": "Net Margin",   "value": m.get("net_margin", "N/A"),   "rating": _pct_rating(m.get("net_margin"))},
            {"label": "ROE",          "value": m.get("roe", "N/A"),          "rating": _pct_rating(m.get("roe"))},
            {"label": "FCF Margin",   "value": m.get("fcf_margin", "N/A"),   "rating": _pct_rating(m.get("fcf_margin"))},
            {"label": "Gross Margin", "value": m.get("gross_margin", "N/A"), "rating": _pct_rating(m.get("gross_margin"))},
            {"label": "EPS Growth",   "value": m.get("eps_growth", "N/A"),   "rating": _pct_rating(m.get("eps_growth"))},
            {"label": "RSI",          "value": str(data["rsi"]) if data else "N/A",       "rating": _rsi_rating(data)},
            {"label": "MACD",         "value": f"{data['macd_hist']:+.3f}" if data else "N/A", "rating": "good" if data and data["macd_hist"] > 0 else "poor"},
            {"label": "Vol Ratio",    "value": f"{data['vol_ratio']:.2f}x" if data else "N/A", "rating": "good" if data and data["vol_ratio"] >= 1.0 else "neutral"},
            {"label": "Day Chg",      "value": f"{data['pct_1d']:+.2f}%" if data else "N/A", "rating": "good" if data and data["pct_1d"] >= 0 else "poor"},
        ]

        # Parse sections out of the formatted text Claude returned
        lines = raw_text.splitlines()
        moat = _extract_line(lines, "MOAT")
        catalyst = _extract_line(lines, "CATALYST")
        bull = _extract_line(lines, "BULL")
        bear = _extract_line(lines, "BEAR")
        verdict_text = _extract_section(lines, "VERDICT")

        return jsonify({
            "ticker": ticker,
            "overall_score": overall,
            "risk_score": risk,
            "growth_score": growth,
            "revenue": m.get("pe", "N/A"),
            "revenue_growth": m.get("rev_growth", "N/A"),
            "eps": m.get("eps_growth", "N/A"),
            "pe_ratio": f"{m.get('pe', 'N/A')}x",
            "business_model": f"{ticker} — {m.get('sector', '')} / {m.get('industry', '')}",
            "moat": moat,
            "catalysts": [catalyst] if catalyst else [],
            "bull_case": bull,
            "bear_case": bear,
            "verdict": verdict_text,
            "metrics": metrics,
            "claude_reasoning": raw_text,
        })
    except Exception:
        log.error("POST /api/analyze error:\n%s", traceback.format_exc())
        return jsonify({"error": "Analysis failed"}), 500


def _extract_line(lines: list, keyword: str) -> str:
    for line in lines:
        if keyword in line.upper():
            # strip leading emoji / label up to first colon
            idx = line.find(":")
            return line[idx + 1:].strip() if idx != -1 else line.strip()
    return ""


def _extract_section(lines: list, keyword: str) -> str:
    collecting = False
    parts = []
    for line in lines:
        if keyword in line.upper():
            idx = line.find(":")
            if idx != -1:
                parts.append(line[idx + 1:].strip())
            collecting = True
            continue
        if collecting:
            if line.strip() == "" or any(k in line.upper() for k in ("🏆", "⚡", "💰", "🏰", "🚀", "🐻", "🧠")):
                break
            parts.append(line.strip())
    return " ".join(p for p in parts if p)


def _pe_rating(val) -> str:
    try:
        v = float(str(val).replace("N/A", ""))
        if v <= 0:
            return "neutral"
        return "good" if v < 25 else ("neutral" if v < 40 else "poor")
    except Exception:
        return "neutral"


def _pct_rating(val) -> str:
    try:
        v = float(str(val).replace("%", "").replace("N/A", ""))
        return "good" if v > 0 else "poor"
    except Exception:
        return "neutral"


def _rsi_rating(data) -> str:
    if not data:
        return "neutral"
    rsi = data.get("rsi", 50)
    return "good" if 40 <= rsi <= 65 else ("neutral" if 30 <= rsi < 40 or 65 < rsi <= 75 else "poor")


# ─────────────────────────────────────────────
# POST /api/parse-screenshot
# ─────────────────────────────────────────────

@ios.route("/parse-screenshot", methods=["POST"])
def parse_screenshot():
    try:
        body = request.get_json(force=True, silent=True) or {}
        image_b64 = body.get("image", "")
        if not image_b64:
            return jsonify({"error": "image required"}), 400

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "This is a Wealthsimple trade confirmation screenshot. "
                            "Extract the trade details and reply ONLY with valid JSON "
                            "(no markdown, no code fences):\n"
                            '{"ticker":"<UPPER>","shares":<number>,"price_cad":<number>,'
                            '"currency":"<CAD|USD>","total_cad":<number>,"trade_type":"<BUY|SELL>"}\n'
                            "If the price is in USD, multiply by 1.38 to get price_cad. "
                            "Infer trade_type from context (buy/purchase → BUY, sell → SELL)."
                        ),
                    },
                ],
            }],
        )

        text = resp.content[0].text.strip()
        # Strip any accidental markdown fences
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.lower().startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        return jsonify(result)

    except json.JSONDecodeError as e:
        log.error("parse-screenshot: JSON decode failed: %s", e)
        return jsonify({"error": "Could not parse Claude response as JSON"}), 500
    except Exception:
        log.error("POST /api/parse-screenshot error:\n%s", traceback.format_exc())
        return jsonify({"error": "Screenshot parsing failed"}), 500


# ─────────────────────────────────────────────
# GET /api/test-add  (smoke-test: writes PLTR to DB)
# ─────────────────────────────────────────────

@ios.route("/test-add", methods=["GET"])
def test_add():
    try:
        result = port.add_or_update_holding("PLTR", 0.7619, 28.50)
        log.info("test-add: inserted PLTR — %s", result)
        return jsonify({"success": True, "result": result})
    except Exception:
        log.error("GET /api/test-add error:\n%s", traceback.format_exc())
        return jsonify({"success": False, "error": traceback.format_exc()}), 500


# ─────────────────────────────────────────────
# POST /api/confirm-trade
# ─────────────────────────────────────────────

@ios.route("/confirm-trade", methods=["POST"])
def confirm_trade():
    # Log raw body first so Railway logs always show what arrived
    raw = request.get_data(as_text=True)
    log.info("confirm-trade raw body: %r", raw[:500])

    try:
        # force=True ignores Content-Type; silent=False raises on bad JSON
        body = request.get_json(force=True, silent=False)
        if body is None:
            log.error("confirm-trade: get_json returned None (empty body?)")
            return jsonify({"success": False, "error": "Empty or non-JSON body"}), 400

        log.info("confirm-trade parsed body: %s", body)

        ticker     = str(body.get("ticker", "")).upper().strip()
        shares     = float(body.get("shares", 0))
        price_cad  = float(body.get("price_cad", 0))
        trade_type = str(body.get("type", "BUY")).upper().strip()

        log.info("confirm-trade: ticker=%s shares=%s price_cad=%s type=%s",
                 ticker, shares, price_cad, trade_type)

        if not ticker:
            return jsonify({"success": False, "error": "ticker is required"}), 400
        if shares <= 0:
            return jsonify({"success": False, "error": "shares must be > 0"}), 400
        if price_cad <= 0:
            return jsonify({"success": False, "error": "price_cad must be > 0"}), 400

        if trade_type == "BUY":
            result = port.add_or_update_holding(ticker, shares, price_cad)
            trade_cost = round(shares * price_cad, 2)
            port.add_cash(-trade_cost)
            log.info("confirm-trade: BUY saved — %s, deducted $%.2f from cash", result, trade_cost)
        elif trade_type == "SELL":
            result = port.reduce_or_remove_holding(ticker, shares, price_cad)
            log.info("confirm-trade: SELL result — %s", result)
            if "error" in result:
                return jsonify({"success": False, "error": result["error"]}), 400
        else:
            log.error("confirm-trade: unrecognised type %r", trade_type)
            return jsonify({"success": False, "error": f"type must be BUY or SELL, got {trade_type!r}"}), 400

        log.info("confirm-trade: success for %s %s @ %.4f CAD", trade_type, ticker, price_cad)
        return jsonify({"success": True})

    except Exception:
        log.error("POST /api/confirm-trade unhandled error:\n%s", traceback.format_exc())
        return jsonify({"success": False, "error": "Trade failed — see Railway logs"}), 500


# ─────────────────────────────────────────────
# POST /api/cash
# ─────────────────────────────────────────────

@ios.route("/cash", methods=["POST"])
def set_cash():
    try:
        body = request.get_json(force=True, silent=True) or {}
        amount = float(body.get("cash", 0))
        port.set_cash_exact(amount)
        return jsonify({"success": True})
    except Exception:
        log.error("POST /api/cash error:\n%s", traceback.format_exc())
        return jsonify({"success": False, "error": "Failed to update cash"}), 500
