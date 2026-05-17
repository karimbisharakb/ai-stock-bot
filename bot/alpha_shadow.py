"""
Phase A2 — Alpha engine shadow integration.

AlphaShadowManager wraps fetch_alpha_input() + AlphaEngine.score() and
persists every result to alpha_shadow_log.  Observation-only:

  - No WhatsApp alerts are sent here.
  - No live alert eligibility is changed.
  - Feature-flagged: ALPHA_SHADOW_ENABLED env var (default False).

Called from run_predator() for every ticker that completes scoring.
Never raises — shadow errors must not crash Predator.
"""
import json
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now().isoformat()


class AlphaShadowManager:

    def run_shadow_score(self, ticker: str, predator_result: dict) -> Optional[object]:
        """Score ticker via alpha engine; persist result to alpha_shadow_log.

        Returns AlphaResult on success, None if input fetch fails or is unavailable.
        All exceptions are caught — shadow must not disrupt Predator.
        """
        from alpha_engine import fetch_alpha_input, AlphaEngine

        try:
            alpha_input = fetch_alpha_input(ticker)
        except Exception:
            log.warning("alpha_shadow: fetch_alpha_input failed for %s", ticker, exc_info=True)
            return None

        if alpha_input is None:
            log.debug("alpha_shadow: no alpha input for %s — skipping", ticker)
            return None

        try:
            result = AlphaEngine().score(alpha_input)
        except Exception:
            log.warning("alpha_shadow: AlphaEngine.score failed for %s", ticker, exc_info=True)
            return None

        predator_tier  = predator_result.get("tier")
        predator_score = predator_result.get("score")
        alpha_tier     = result.tier if not result.filtered else None
        alpha_score    = result.alpha_score if not result.filtered else None
        tier_match     = int(alpha_tier == predator_tier) if (alpha_tier and predator_tier) else 0
        filter_reason  = "; ".join(result.filter_reasons) if result.filter_reasons else None

        component_scores = {
            cs.name: {
                "score":        round(cs.score, 3),
                "weight":       cs.weight,
                "data_quality": cs.data_quality,
            }
            for cs in result.components
        }

        self._persist(
            ticker=ticker,
            alpha_score=alpha_score,
            alpha_tier=alpha_tier,
            setup_type=result.setup_type if not result.filtered else None,
            predator_tier=predator_tier,
            predator_score=predator_score,
            tier_match=tier_match,
            filter_reason=filter_reason,
            component_scores=component_scores,
            explanation="; ".join(result.why_scored_high) if result.why_scored_high else None,
        )

        if alpha_tier and predator_tier and alpha_tier != predator_tier:
            log.info(
                "alpha_shadow: MISMATCH %s — predator=%s alpha=%s (score=%.1f)",
                ticker, predator_tier, alpha_tier, alpha_score or 0,
            )
        elif alpha_tier in ("RARE_SETUP", "HIGH_CONVICTION"):
            log.info(
                "alpha_shadow: HIGH SIGNAL %s — %s (score=%.1f setup=%s)",
                ticker, alpha_tier, alpha_score or 0, result.setup_type,
            )

        return result

    def _persist(self, *, ticker, alpha_score, alpha_tier, setup_type,
                 predator_tier, predator_score, tier_match,
                 filter_reason, component_scores, explanation):
        from database import get_connection
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO alpha_shadow_log
                    (ticker, scan_time, alpha_score, alpha_tier, setup_type,
                     predator_tier, predator_score, tier_match, filter_reason,
                     component_scores_json, explanation)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker, _now_iso(), alpha_score, alpha_tier, setup_type,
                    predator_tier, predator_score, tier_match, filter_reason,
                    json.dumps(component_scores) if component_scores else None,
                    explanation,
                ),
            )
            conn.commit()
        except Exception:
            log.warning("alpha_shadow: DB persist failed for %s", ticker, exc_info=True)
        finally:
            conn.close()

    # ── Analytics queries ─────────────────────────────────────────────────────

    def get_latest_results(self, limit: int = 50) -> list:
        """Most-recent alpha shadow row per ticker, ordered by alpha_score DESC."""
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT a.*
                FROM alpha_shadow_log a
                INNER JOIN (
                    SELECT ticker, MAX(scan_time) AS latest
                    FROM alpha_shadow_log
                    GROUP BY ticker
                ) g ON a.ticker = g.ticker AND a.scan_time = g.latest
                ORDER BY a.alpha_score DESC, a.ticker ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_top_candidates(self, limit: int = 10) -> list:
        """Highest-scoring non-filtered rows from the most recent scan per ticker."""
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT a.*
                FROM alpha_shadow_log a
                INNER JOIN (
                    SELECT ticker, MAX(scan_time) AS latest
                    FROM alpha_shadow_log
                    GROUP BY ticker
                ) g ON a.ticker = g.ticker AND a.scan_time = g.latest
                WHERE a.filter_reason IS NULL AND a.alpha_score IS NOT NULL
                ORDER BY a.alpha_score DESC, a.ticker ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_mismatches(self, limit: int = 20) -> list:
        """Most-recent rows where alpha tier differs from predator tier."""
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT a.*
                FROM alpha_shadow_log a
                INNER JOIN (
                    SELECT ticker, MAX(scan_time) AS latest
                    FROM alpha_shadow_log
                    GROUP BY ticker
                ) g ON a.ticker = g.ticker AND a.scan_time = g.latest
                WHERE a.tier_match = 0
                  AND a.alpha_tier  IS NOT NULL
                  AND a.predator_tier IS NOT NULL
                ORDER BY a.alpha_score DESC, a.ticker ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_rare_setups(self, limit: int = 10) -> list:
        """Most-recent rows with RARE_SETUP or HIGH_CONVICTION alpha tier."""
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT a.*
                FROM alpha_shadow_log a
                INNER JOIN (
                    SELECT ticker, MAX(scan_time) AS latest
                    FROM alpha_shadow_log
                    GROUP BY ticker
                ) g ON a.ticker = g.ticker AND a.scan_time = g.latest
                WHERE a.alpha_tier IN ('RARE_SETUP', 'HIGH_CONVICTION')
                ORDER BY a.alpha_score DESC, a.ticker ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_rejected_alerts(self, limit: int = 20) -> list:
        """Tickers Predator rated WATCH but alpha sees HIGH_CONVICTION or RARE_SETUP."""
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT a.*
                FROM alpha_shadow_log a
                INNER JOIN (
                    SELECT ticker, MAX(scan_time) AS latest
                    FROM alpha_shadow_log
                    GROUP BY ticker
                ) g ON a.ticker = g.ticker AND a.scan_time = g.latest
                WHERE a.predator_tier = 'WATCH'
                  AND a.alpha_tier IN ('HIGH_CONVICTION', 'RARE_SETUP')
                ORDER BY a.alpha_score DESC, a.ticker ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


_manager = AlphaShadowManager()


def get_shadow_manager() -> AlphaShadowManager:
    return _manager
