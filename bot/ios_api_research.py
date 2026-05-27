"""
Research API — Flask Blueprint at /api/research.

Provides AI-powered research tools for the iOS app:
  - Multi-persona AI chat
  - Multi-ticker comparison
  - Reddit/social trending
  - News impact analysis
  - Daily market brief
  - Sector heatmap
  - Saved research library
"""
import json
import logging
import traceback
from datetime import datetime

import pytz
from flask import Blueprint, jsonify, request

from database import get_connection
from ios_api import _require_auth
from research_engine import (
    PERSONAS,
    analyze_news_impact,
    compare_tickers,
    generate_market_brief,
    get_sector_heatmap,
    persona_chat,
    scan_social_trending,
)

log = logging.getLogger(__name__)
research_bp = Blueprint("research", __name__, url_prefix="/api/research")
EASTERN = pytz.timezone("America/Toronto")

_SESSION_TTL_HOURS = 24  # chat sessions expire after this


# ─────────────────────────────────────────────────────────────
# POST /api/research/chat
# body: {message, persona, ticker?, session_id?}
# ─────────────────────────────────────────────────────────────

@research_bp.route("/chat", methods=["POST"])
@_require_auth
def chat():
    try:
        body = request.get_json(force=True, silent=True) or {}
        message = str(body.get("message", "")).strip()
        persona_key = str(body.get("persona", "VALUE")).upper().strip()
        ticker = body.get("ticker")
        session_id = str(body.get("session_id", "default"))

        if not message:
            return jsonify({"error": "message required"}), 400

        # Load recent history for this session
        conn = get_connection()
        rows = conn.execute(
            "SELECT role, content FROM chat_history "
            "WHERE session_id = ? AND persona = ? "
            "ORDER BY timestamp DESC LIMIT 12",
            (session_id, persona_key),
        ).fetchall()
        conn.close()

        history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

        # Get AI response
        response_text = persona_chat(message, persona_key, ticker=ticker, history=history)

        # Store both turns in DB
        now = datetime.now(EASTERN).isoformat()
        conn = get_connection()
        conn.execute(
            "INSERT INTO chat_history (session_id, persona, role, content, ticker, timestamp) VALUES (?,?,?,?,?,?)",
            (session_id, persona_key, "user", message, ticker, now),
        )
        conn.execute(
            "INSERT INTO chat_history (session_id, persona, role, content, ticker, timestamp) VALUES (?,?,?,?,?,?)",
            (session_id, persona_key, "assistant", response_text, ticker, now),
        )
        conn.commit()
        conn.close()

        persona_info = PERSONAS.get(persona_key, PERSONAS["VALUE"])
        return jsonify({
            "response": response_text,
            "persona": persona_key,
            "persona_name": persona_info["name"],
            "persona_emoji": persona_info["emoji"],
            "session_id": session_id,
        })
    except Exception:
        log.error("POST /api/research/chat error:\n%s", traceback.format_exc())
        return jsonify({"error": "Chat failed"}), 500


# ─────────────────────────────────────────────────────────────
# GET /api/research/chat-history?session_id=X&persona=Y&limit=N
# ─────────────────────────────────────────────────────────────

@research_bp.route("/chat-history", methods=["GET"])
def chat_history():
    try:
        session_id = request.args.get("session_id", "default")
        persona = request.args.get("persona", "").upper() or None
        limit = min(int(request.args.get("limit", 50)), 200)

        conn = get_connection()
        if persona:
            rows = conn.execute(
                "SELECT id, session_id, persona, role, content, ticker, timestamp "
                "FROM chat_history WHERE session_id = ? AND persona = ? "
                "ORDER BY timestamp ASC LIMIT ?",
                (session_id, persona, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, session_id, persona, role, content, ticker, timestamp "
                "FROM chat_history WHERE session_id = ? "
                "ORDER BY timestamp ASC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        conn.close()

        messages = []
        for r in rows:
            persona_info = PERSONAS.get(r["persona"], PERSONAS["VALUE"])
            messages.append({
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "persona": r["persona"],
                "persona_name": persona_info["name"],
                "persona_emoji": persona_info["emoji"],
                "ticker": r["ticker"],
                "timestamp": r["timestamp"],
            })

        return jsonify({"messages": messages, "session_id": session_id})
    except Exception:
        log.error("GET /api/research/chat-history error:\n%s", traceback.format_exc())
        return jsonify({"messages": []}), 500


# ─────────────────────────────────────────────────────────────
# POST /api/research/compare
# body: {tickers: [...], perspective}
# ─────────────────────────────────────────────────────────────

@research_bp.route("/compare", methods=["POST"])
def compare():
    try:
        body = request.get_json(force=True, silent=True) or {}
        tickers = body.get("tickers", [])
        perspective = str(body.get("perspective", "ALL")).upper()

        if not tickers or len(tickers) < 2:
            return jsonify({"error": "Need at least 2 tickers to compare"}), 400

        result = compare_tickers(tickers, perspective)
        return jsonify(result)
    except Exception:
        log.error("POST /api/research/compare error:\n%s", traceback.format_exc())
        return jsonify({"error": "Comparison failed"}), 500


# ─────────────────────────────────────────────────────────────
# GET /api/research/trending-social
# ─────────────────────────────────────────────────────────────

@research_bp.route("/trending-social", methods=["GET"])
def trending_social():
    try:
        # Check if we have a recent scan in DB (< 30 min old)
        conn = get_connection()
        cutoff = (datetime.utcnow().replace(tzinfo=pytz.utc).astimezone(EASTERN)
                  .replace(tzinfo=None) - __import__("datetime").timedelta(minutes=30)).isoformat()
        recent = conn.execute(
            "SELECT ticker, mention_count, sentiment_score, sentiment_label, "
            "sample_post_url, subreddit, scanned_at FROM social_trending_log "
            "WHERE scanned_at >= ? ORDER BY mention_count DESC LIMIT 20",
            (cutoff,),
        ).fetchall()
        conn.close()

        if recent:
            return jsonify({
                "trending": [dict(r) for r in recent],
                "from_cache": True,
                "scanned_at": recent[0]["scanned_at"] if recent else None,
            })

        # Do a fresh scan
        results = scan_social_trending(20)

        # Save to DB
        if results:
            now = datetime.now(EASTERN).isoformat()
            conn = get_connection()
            for item in results:
                conn.execute(
                    "INSERT INTO social_trending_log "
                    "(ticker, mention_count, sentiment_score, sentiment_label, "
                    "sample_post_url, subreddit, scanned_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        item["ticker"], item["mention_count"],
                        item["sentiment_score"], item["sentiment_label"],
                        item.get("sample_url", ""), item.get("subreddit", ""),
                        now,
                    ),
                )
            conn.commit()
            conn.close()

        return jsonify({
            "trending": results,
            "from_cache": False,
            "scanned_at": datetime.now(EASTERN).isoformat(),
        })
    except Exception:
        log.error("GET /api/research/trending-social error:\n%s", traceback.format_exc())
        return jsonify({"trending": [], "error": "Scan failed"}), 500


# ─────────────────────────────────────────────────────────────
# GET /api/research/news-impact
# Returns recent news impact analyses from DB
# POST /api/research/news-impact  — analyze a new headline
# body: {headline}
# ─────────────────────────────────────────────────────────────

@research_bp.route("/news-impact", methods=["GET"])
def news_impact_get():
    try:
        limit = min(int(request.args.get("limit", 20)), 50)
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, headline, source, affected_tickers_json, impact_analysis, timestamp "
            "FROM news_impact_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()

        items = []
        for r in rows:
            affected = []
            try:
                affected = json.loads(r["affected_tickers_json"] or "[]")
            except Exception:
                pass
            items.append({
                "id": r["id"],
                "headline": r["headline"],
                "source": r["source"],
                "affected": affected,
                "impact_analysis": r["impact_analysis"],
                "timestamp": r["timestamp"],
            })
        return jsonify({"items": items})
    except Exception:
        log.error("GET /api/research/news-impact error:\n%s", traceback.format_exc())
        return jsonify({"items": []}), 500


@research_bp.route("/news-impact", methods=["POST"])
@_require_auth
def news_impact_post():
    try:
        body = request.get_json(force=True, silent=True) or {}
        headline = str(body.get("headline", "")).strip()
        source = str(body.get("source", "manual"))

        if not headline:
            return jsonify({"error": "headline required"}), 400

        # Get user's holdings
        import portfolio as port
        holdings = port.get_holdings()
        tickers = [h["ticker"] for h in holdings]

        result = analyze_news_impact(headline, tickers)

        # Save to DB
        now = datetime.now(EASTERN).isoformat()
        conn = get_connection()
        cur = conn.execute(
            "INSERT INTO news_impact_log (headline, source, affected_tickers_json, impact_analysis, timestamp) "
            "VALUES (?,?,?,?,?)",
            (headline, source, json.dumps(result.get("affected", [])),
             result.get("summary", ""), now),
        )
        new_id = cur.lastrowid
        conn.commit()
        conn.close()

        return jsonify({"id": new_id, **result})
    except Exception:
        log.error("POST /api/research/news-impact error:\n%s", traceback.format_exc())
        return jsonify({"error": "Analysis failed"}), 500


# ─────────────────────────────────────────────────────────────
# GET /api/research/market-brief
# ─────────────────────────────────────────────────────────────

@research_bp.route("/market-brief", methods=["GET"])
def market_brief():
    try:
        # Check DB for a brief generated < 60 min ago
        conn = get_connection()
        cutoff = (datetime.utcnow().replace(tzinfo=pytz.utc).astimezone(EASTERN)
                  .replace(tzinfo=None) - __import__("datetime").timedelta(minutes=60)).isoformat()
        recent = conn.execute(
            "SELECT brief_text, key_metrics_json, generated_at FROM market_briefs "
            "WHERE generated_at >= ? ORDER BY generated_at DESC LIMIT 1",
            (cutoff,),
        ).fetchone()
        conn.close()

        if recent:
            metrics = {}
            try:
                metrics = json.loads(recent["key_metrics_json"] or "{}")
            except Exception:
                pass
            return jsonify({
                "brief_text": recent["brief_text"],
                "key_metrics": metrics,
                "generated_at": recent["generated_at"],
                "from_cache": True,
            })

        # Generate fresh
        result = generate_market_brief()

        # Save to DB
        conn = get_connection()
        conn.execute(
            "INSERT INTO market_briefs (brief_text, key_metrics_json, generated_at) VALUES (?,?,?)",
            (result["brief_text"], json.dumps(result.get("key_metrics", {})),
             result["generated_at"]),
        )
        conn.commit()
        conn.close()

        return jsonify({**result, "from_cache": False})
    except Exception:
        log.error("GET /api/research/market-brief error:\n%s", traceback.format_exc())
        return jsonify({"error": "Brief generation failed", "brief_text": ""}), 500


# ─────────────────────────────────────────────────────────────
# GET /api/research/sectors
# ─────────────────────────────────────────────────────────────

@research_bp.route("/sectors", methods=["GET"])
def sectors():
    try:
        data = get_sector_heatmap()
        return jsonify({"sectors": data, "generated_at": datetime.now(EASTERN).isoformat()})
    except Exception:
        log.error("GET /api/research/sectors error:\n%s", traceback.format_exc())
        return jsonify({"sectors": []}), 500


# ─────────────────────────────────────────────────────────────
# POST /api/research/save
# body: {title, content, tickers: [...], persona}
# ─────────────────────────────────────────────────────────────

@research_bp.route("/save", methods=["POST"])
@_require_auth
def save_research():
    try:
        body = request.get_json(force=True, silent=True) or {}
        title = str(body.get("title", "")).strip()
        content = str(body.get("content", "")).strip()
        tickers = body.get("tickers", [])
        persona = str(body.get("persona", "VALUE")).upper()

        if not title or not content:
            return jsonify({"success": False, "error": "title and content required"}), 400

        now = datetime.now(EASTERN).isoformat()
        conn = get_connection()
        cur = conn.execute(
            "INSERT INTO saved_research (title, content, tickers_json, persona, created_at) VALUES (?,?,?,?,?)",
            (title, content, json.dumps(tickers), persona, now),
        )
        new_id = cur.lastrowid
        conn.commit()
        conn.close()

        log.info("save_research: saved id=%d title=%r", new_id, title[:40])
        return jsonify({"success": True, "id": new_id})
    except Exception:
        log.error("POST /api/research/save error:\n%s", traceback.format_exc())
        return jsonify({"success": False, "error": "Failed to save"}), 500


# ─────────────────────────────────────────────────────────────
# GET /api/research/saved
# ─────────────────────────────────────────────────────────────

@research_bp.route("/saved", methods=["GET"])
def saved_research():
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, title, content, tickers_json, persona, created_at "
            "FROM saved_research ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()

        items = []
        for r in rows:
            tickers = []
            try:
                tickers = json.loads(r["tickers_json"] or "[]")
            except Exception:
                pass
            persona_info = PERSONAS.get(r["persona"], PERSONAS["VALUE"])
            snippet = r["content"][:150] + "…" if len(r["content"]) > 150 else r["content"]
            items.append({
                "id": r["id"],
                "title": r["title"],
                "snippet": snippet,
                "content": r["content"],
                "tickers": tickers,
                "persona": r["persona"],
                "persona_name": persona_info["name"],
                "persona_emoji": persona_info["emoji"],
                "created_at": r["created_at"],
            })
        return jsonify({"items": items})
    except Exception:
        log.error("GET /api/research/saved error:\n%s", traceback.format_exc())
        return jsonify({"items": []}), 500


# ─────────────────────────────────────────────────────────────
# DELETE /api/research/saved/<id>
# ─────────────────────────────────────────────────────────────

@research_bp.route("/saved/<int:item_id>", methods=["DELETE"])
@_require_auth
def delete_saved_research(item_id: int):
    try:
        conn = get_connection()
        conn.execute("DELETE FROM saved_research WHERE id = ?", (item_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception:
        log.error("DELETE /api/research/saved/%d error:\n%s", item_id, traceback.format_exc())
        return jsonify({"success": False, "error": "Delete failed"}), 500


# ─────────────────────────────────────────────────────────────
# GET /api/research/personas
# ─────────────────────────────────────────────────────────────

@research_bp.route("/personas", methods=["GET"])
def get_personas():
    """Return persona metadata for the iOS app."""
    return jsonify({
        "personas": [
            {
                "key": k,
                "name": v["name"],
                "emoji": v["emoji"],
                "color": v["color"],
                "intro": v["intro"],
            }
            for k, v in PERSONAS.items()
        ]
    })
