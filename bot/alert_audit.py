"""
Notification audit log (Phase N1).

Records every gateway evaluation — eligible or suppressed — to SQLite.
Provides eligibility reasoning trace, suppression tracking, mismatch detection,
and delivery outcome tracking.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Optional

from database import get_connection
from alert_schema import AlertCandidate, EligibilityResult, OutboundAlert

log = logging.getLogger(__name__)


class AlertAuditLog:
    """SQLite-backed audit log for the unified notification gateway.

    All methods are safe to call in a single-worker Gunicorn deployment.
    Exceptions are caught and logged — audit failures never block notification dispatch.
    """

    # ── Eligible alerts ────────────────────────────────────────────────────────

    def log_eligible(self, alert: OutboundAlert) -> None:
        """Record an alert that passed eligibility (sent, dry-run, or shadow)."""
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO notification_audit_log
                  (alert_id, ticker, source, tier, adjusted_score, confidence_pct,
                   raw_score, active_signals, suppressed_signals, regime_context,
                   risk_posture, trigger_reason, formatted_message,
                   dry_run, shadow, delivered, sent_at, evaluated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    alert.alert_id,
                    alert.ticker,
                    alert.source,
                    alert.tier,
                    alert.adjusted_score,
                    alert.confidence_pct,
                    alert.raw_score,
                    json.dumps(alert.active_signals),
                    json.dumps(alert.suppressed_signals),
                    alert.regime_context,
                    alert.risk_posture,
                    alert.trigger_reason,
                    alert.formatted_message[:1000],
                    int(alert.dry_run),
                    int(alert.shadow),
                    int(alert.delivered),
                    alert.sent_at,
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
        except Exception as exc:
            log.warning("audit log_eligible failed for %s: %s", alert.ticker, exc)
        finally:
            conn.close()

    # ── Suppressed alerts ──────────────────────────────────────────────────────

    def log_suppressed(
        self,
        candidate: AlertCandidate,
        eligibility: EligibilityResult,
    ) -> None:
        """Record a candidate that was suppressed (not sent)."""
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO notification_suppressed_log
                  (ticker, source, suppression_reasons, resolved_tier,
                   adjusted_score, confidence_pct, evaluated_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    candidate.ticker,
                    candidate.source,
                    json.dumps(eligibility.suppression_reasons),
                    eligibility.resolved_tier,
                    eligibility.adjusted_score,
                    eligibility.confidence_pct,
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
        except Exception as exc:
            log.warning("audit log_suppressed failed for %s: %s", candidate.ticker, exc)
        finally:
            conn.close()

    # ── Mismatch detection ─────────────────────────────────────────────────────

    def log_mismatch(
        self,
        candidate: AlertCandidate,
        legacy_would_send: bool,
        unified_would_send: bool,
        eligibility: EligibilityResult,
    ) -> None:
        """Record a legacy vs unified decision mismatch (shadow-compare mode).

        Only writes a row when the two systems disagree.
        """
        if legacy_would_send == unified_would_send:
            return
        conn = get_connection()
        ts = datetime.now()
        try:
            conn.execute(
                """
                INSERT INTO notification_audit_log
                  (alert_id, ticker, source, tier, adjusted_score, confidence_pct,
                   raw_score, active_signals, suppressed_signals, regime_context,
                   risk_posture, trigger_reason, formatted_message,
                   dry_run, shadow, delivered, sent_at, evaluated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"mismatch-{candidate.source}-{candidate.ticker}-{ts.strftime('%Y%m%dT%H%M%S')}",
                    candidate.ticker,
                    candidate.source,
                    eligibility.resolved_tier,
                    eligibility.adjusted_score,
                    eligibility.confidence_pct,
                    candidate.raw_score,
                    json.dumps(candidate.active_signals),
                    json.dumps(candidate.suppressed_signals),
                    candidate.regime,
                    candidate.risk_posture,
                    (
                        f"MISMATCH legacy={'SEND' if legacy_would_send else 'SUPPRESS'} "
                        f"unified={'SEND' if unified_would_send else 'SUPPRESS'} | "
                        + (eligibility.trigger_reason or
                           ", ".join(eligibility.suppression_reasons))
                    ),
                    "",
                    0,    # dry_run=False
                    1,    # shadow=True
                    0,    # delivered=False
                    None,
                    ts.isoformat(),
                ),
            )
            conn.commit()
            log.warning(
                "SHADOW MISMATCH %s: legacy=%s unified=%s tier=%s adj=%.2f",
                candidate.ticker,
                "SEND"     if legacy_would_send  else "SUPPRESS",
                "SEND"     if unified_would_send else "SUPPRESS",
                eligibility.resolved_tier,
                eligibility.adjusted_score,
            )
        except Exception as exc:
            log.warning("audit log_mismatch failed for %s: %s", candidate.ticker, exc)
        finally:
            conn.close()

    # ── Delivery outcome tracking ──────────────────────────────────────────────

    def log_delivery_outcome(self, alert_id: str, outcome: str) -> None:
        """Mark an alert as delivered (or failed)."""
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE notification_audit_log SET delivered = ? WHERE alert_id = ?",
                (1 if outcome == "delivered" else 0, alert_id),
            )
            conn.commit()
        except Exception as exc:
            log.warning("audit log_delivery_outcome failed for %s: %s", alert_id, exc)
        finally:
            conn.close()

    # ── Observability queries ──────────────────────────────────────────────────

    def recent_suppressed(self, limit: int = 50) -> list[dict]:
        """Return recent suppressed candidates for observability."""
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT ticker, source, suppression_reasons, resolved_tier,
                       adjusted_score, confidence_pct, evaluated_at
                FROM notification_suppressed_log
                ORDER BY evaluated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()

    def recent_mismatches(self, limit: int = 50) -> list[dict]:
        """Return recent shadow-mode mismatches for review."""
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT ticker, source, tier, adjusted_score, trigger_reason, evaluated_at
                FROM notification_audit_log
                WHERE shadow = 1 AND trigger_reason LIKE 'MISMATCH%'
                ORDER BY evaluated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()
