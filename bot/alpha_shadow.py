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

# ── Module-level state for diagnostics ───────────────────────────────────────
_hook_last_seen_at: Optional[str] = None
_last_error: Optional[str]        = None


def _now_iso() -> str:
    return datetime.now().isoformat()


def get_hook_diagnostics() -> dict:
    """Return hook_last_seen_at and last_error for the /alpha/debug endpoint."""
    return {
        "hook_last_seen_at": _hook_last_seen_at,
        "last_error":        _last_error,
    }


class AlphaShadowManager:

    def run_shadow_score(self, ticker: str, predator_result: dict) -> Optional[object]:
        """Score ticker via alpha engine; persist result to alpha_shadow_log.

        Returns AlphaResult on success, None if input fetch fails or is unavailable.
        All exceptions are caught — shadow must not disrupt Predator.
        """
        global _hook_last_seen_at, _last_error
        from alpha_engine import fetch_alpha_input, AlphaEngine

        log.info("alpha_shadow: scoring %s", ticker)
        _hook_last_seen_at = _now_iso()

        try:
            alpha_input = fetch_alpha_input(ticker)
        except Exception as exc:
            _last_error = f"fetch_alpha_input({ticker}): {exc}"
            log.warning("alpha_shadow: fetch_alpha_input failed for %s", ticker, exc_info=True)
            return None

        if alpha_input is None:
            log.info("alpha_shadow: fetch_alpha_input returned None for %s — skipping", ticker)
            return None

        try:
            result = AlphaEngine().score(alpha_input)
        except Exception as exc:
            _last_error = f"AlphaEngine.score({ticker}): {exc}"
            log.warning("alpha_shadow: AlphaEngine.score failed for %s", ticker, exc_info=True)
            return None

        log.info("alpha_shadow: %s scored %.1f tier=%s setup=%s filtered=%s",
                 ticker, result.alpha_score, result.tier, result.setup_type, result.filtered)

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

        detail_json = None
        try:
            detail_json = json.dumps({
                "why_scored_high":        result.why_scored_high,
                "what_must_happen_next":  result.what_must_happen_next,
                "what_could_invalidate":  result.what_could_invalidate,
                "risk_factors":           result.risk_factors,
                "expected_holding_window": result.expected_holding_window,
                "tier_gate_note":         result.tier_gate_note,
            })
        except Exception:
            pass

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
            detail_json=detail_json,
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
                 filter_reason, component_scores, explanation,
                 detail_json: Optional[str] = None):
        from database import get_connection
        conn = get_connection()
        base_args = (
            ticker, _now_iso(), alpha_score, alpha_tier, setup_type,
            predator_tier, predator_score, tier_match, filter_reason,
            json.dumps(component_scores) if component_scores else None,
            explanation,
        )
        try:
            try:
                # Preferred: full 12-column insert including detail_json (migration v7)
                conn.execute(
                    """
                    INSERT INTO alpha_shadow_log
                        (ticker, scan_time, alpha_score, alpha_tier, setup_type,
                         predator_tier, predator_score, tier_match, filter_reason,
                         component_scores_json, explanation, detail_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*base_args, detail_json),
                )
            except Exception:
                # Fallback: 11-column insert for pre-migration-v7 databases
                conn.execute(
                    """
                    INSERT INTO alpha_shadow_log
                        (ticker, scan_time, alpha_score, alpha_tier, setup_type,
                         predator_tier, predator_score, tier_match, filter_reason,
                         component_scores_json, explanation)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    base_args,
                )
            conn.commit()
            log.info("alpha_shadow: row persisted for %s (alpha_score=%s tier=%s)",
                     ticker, alpha_score, alpha_tier)
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


    def count_by_tier(self) -> dict:
        """Count of most-recent alpha results per tier."""
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT a.alpha_tier, COUNT(*) as cnt
                FROM alpha_shadow_log a
                INNER JOIN (
                    SELECT ticker, MAX(scan_time) AS latest
                    FROM alpha_shadow_log GROUP BY ticker
                ) g ON a.ticker = g.ticker AND a.scan_time = g.latest
                WHERE a.alpha_tier IS NOT NULL
                GROUP BY a.alpha_tier
                ORDER BY cnt DESC
                """
            ).fetchall()
            return {row[0]: row[1] for row in rows}
        finally:
            conn.close()

    def get_top_setup_types(self, limit: int = 10) -> list:
        """Top setup types by count and average alpha score (most-recent per ticker)."""
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT a.setup_type, COUNT(*) AS cnt, AVG(a.alpha_score) AS avg_score
                FROM alpha_shadow_log a
                INNER JOIN (
                    SELECT ticker, MAX(scan_time) AS latest
                    FROM alpha_shadow_log GROUP BY ticker
                ) g ON a.ticker = g.ticker AND a.scan_time = g.latest
                WHERE a.setup_type IS NOT NULL AND a.alpha_score IS NOT NULL
                GROUP BY a.setup_type
                ORDER BY avg_score DESC, cnt DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [{"setup_type": r[0], "count": r[1], "avg_score": round(r[2], 2)} for r in rows]
        finally:
            conn.close()

    def get_best_non_predator(self, predator_tickers: list, limit: int = 10) -> list:
        """Highest-scoring tickers NOT in predator_tickers (most-recent per ticker)."""
        from database import get_connection
        conn = get_connection()
        try:
            placeholders = ",".join("?" * len(predator_tickers)) if predator_tickers else "'__none__'"
            rows = conn.execute(
                f"""
                SELECT a.*
                FROM alpha_shadow_log a
                INNER JOIN (
                    SELECT ticker, MAX(scan_time) AS latest
                    FROM alpha_shadow_log GROUP BY ticker
                ) g ON a.ticker = g.ticker AND a.scan_time = g.latest
                WHERE a.alpha_score IS NOT NULL
                  AND a.filter_reason IS NULL
                  {"AND a.ticker NOT IN (" + placeholders + ")" if predator_tickers else ""}
                ORDER BY a.alpha_score DESC, a.ticker ASC
                LIMIT ?
                """,
                (*predator_tickers, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_universe_coverage(self, universe_tickers: list) -> dict:
        """How many universe tickers have been scored and are in the DB."""
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT ticker FROM alpha_shadow_log"
            ).fetchall()
            in_db = {r[0] for r in rows}
            covered = [t for t in universe_tickers if t in in_db]
            return {
                "universe_size": len(universe_tickers),
                "covered_in_db": len(covered),
                "missing":       [t for t in universe_tickers if t not in in_db],
            }
        finally:
            conn.close()


_manager = AlphaShadowManager()


def get_shadow_manager() -> AlphaShadowManager:
    return _manager
