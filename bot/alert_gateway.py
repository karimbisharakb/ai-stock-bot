"""
Unified alert gateway (Phase N1).

AlertGateway is the single orchestrator for all outbound notifications.
Chain: eligibility check → dedup → rate limit → snooze → format → send → audit.

Thread safety: rate_window is in-memory and not synchronized — safe for
single-worker Gunicorn deployments. For multi-worker use, promote to DB.
"""
from __future__ import annotations
import logging
import time
import uuid
from collections import deque
from datetime import datetime, timedelta
from typing import Callable, Optional

import pytz

from alert_schema import AlertCandidate, EligibilityResult, OutboundAlert
from alert_eligibility import check_eligibility
from alert_formatter import format_alert
from alert_audit import AlertAuditLog
from notification_policy import NotificationPolicy
from database import get_connection

log = logging.getLogger(__name__)
EASTERN = pytz.timezone("America/Toronto")


class AlertGateway:
    """Single entry point for all outbound alert decisions.

    Usage:
        policy  = NotificationPolicy.conviction_only()
        gateway = AlertGateway(policy, send_fn=alerts.send_sms)
        result  = gateway.submit(candidate)

    submit() returns:
        OutboundAlert — if the candidate was sent (or dry_run/shadow eligible)
        None          — if the candidate was suppressed

    compare_shadow() is the shadow-mode companion: evaluates with unified policy,
    logs any legacy vs unified mismatch, and never sends.
    """

    def __init__(
        self,
        policy: NotificationPolicy,
        send_fn: Callable[[str], bool],
    ):
        self.policy  = policy
        self._send   = send_fn
        self.audit   = AlertAuditLog()
        self._rate_window: deque[float] = deque()

    # ── Public API ─────────────────────────────────────────────────────────────

    def submit(
        self,
        candidate: AlertCandidate,
        **format_kwargs,
    ) -> Optional[OutboundAlert]:
        """Evaluate candidate and dispatch if eligible.

        Returns OutboundAlert on success (including dry_run/shadow), None if suppressed.
        """
        eligibility = check_eligibility(candidate, self.policy)

        if not eligibility.eligible:
            self.audit.log_suppressed(candidate, eligibility)
            return None

        # Dedup check (gateway's own window, source-scoped)
        if self._is_duplicate(candidate):
            eligibility.suppression_reasons.append("duplicate_within_dedup_window")
            self.audit.log_suppressed(candidate, eligibility)
            return None

        # Rate limiting
        if self._is_rate_limited():
            eligibility.suppression_reasons.append("rate_limit_exceeded")
            self.audit.log_suppressed(candidate, eligibility)
            return None

        # Snooze check
        if self._is_snoozed(candidate.ticker):
            eligibility.suppression_reasons.append("ticker_snoozed")
            self.audit.log_suppressed(candidate, eligibility)
            return None

        # Format
        message = format_alert(candidate, eligibility, **format_kwargs)

        outbound = OutboundAlert(
            alert_id=self._new_id(candidate),
            ticker=candidate.ticker,
            source=candidate.source,
            tier=eligibility.resolved_tier,
            adjusted_score=eligibility.adjusted_score,
            confidence_pct=eligibility.confidence_pct,
            raw_score=candidate.raw_score,
            active_signals=list(candidate.active_signals),
            suppressed_signals=list(candidate.suppressed_signals),
            regime_context=candidate.regime,
            risk_posture=candidate.risk_posture,
            trigger_reason=eligibility.trigger_reason,
            formatted_message=message,
            dry_run=self.policy.dry_run,
            shadow=self.policy.shadow_mode,
            sent_at=None,
            delivered=False,
        )

        if self.policy.dry_run or self.policy.shadow_mode:
            outbound.sent_at = datetime.now().isoformat()
            self.audit.log_eligible(outbound)
            log.info(
                "Gateway %s: %s/%s tier=%s adj=%.2f conf=%.0f%%",
                "DRY-RUN" if self.policy.dry_run else "SHADOW",
                candidate.source, candidate.ticker,
                eligibility.resolved_tier,
                eligibility.adjusted_score,
                eligibility.confidence_pct,
            )
            return outbound

        # Live send
        delivered        = self._send(message)
        now_iso          = datetime.now().isoformat()
        outbound.sent_at  = now_iso
        outbound.delivered = delivered

        if delivered:
            self._record_rate_tick()

        self.audit.log_eligible(outbound)
        log.info(
            "Gateway SENT: %s/%s tier=%s adj=%.2f conf=%.0f%% delivered=%s",
            candidate.source, candidate.ticker,
            eligibility.resolved_tier,
            eligibility.adjusted_score,
            eligibility.confidence_pct,
            delivered,
        )
        return outbound

    def compare_shadow(
        self,
        candidate: AlertCandidate,
        legacy_would_send: bool,
        **format_kwargs,
    ) -> Optional[OutboundAlert]:
        """Shadow-comparison mode.

        Evaluates with unified policy, records any legacy vs unified mismatch,
        and NEVER sends. Returns the unified OutboundAlert (shadow=True) or None.
        """
        eligibility      = check_eligibility(candidate, self.policy)
        is_dup           = self._is_duplicate(candidate)
        unified_sends    = eligibility.eligible and not is_dup

        self.audit.log_mismatch(candidate, legacy_would_send, unified_sends, eligibility)

        if not unified_sends:
            if not eligibility.eligible:
                self.audit.log_suppressed(candidate, eligibility)
            return None

        message = format_alert(candidate, eligibility, **format_kwargs)
        return OutboundAlert(
            alert_id=self._new_id(candidate),
            ticker=candidate.ticker,
            source=candidate.source,
            tier=eligibility.resolved_tier,
            adjusted_score=eligibility.adjusted_score,
            confidence_pct=eligibility.confidence_pct,
            raw_score=candidate.raw_score,
            active_signals=list(candidate.active_signals),
            suppressed_signals=list(candidate.suppressed_signals),
            regime_context=candidate.regime,
            risk_posture=candidate.risk_posture,
            trigger_reason=eligibility.trigger_reason,
            formatted_message=message,
            dry_run=False,
            shadow=True,
            sent_at=datetime.now().isoformat(),
            delivered=False,
        )

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _new_id(self, candidate: AlertCandidate) -> str:
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        return f"{candidate.source}-{candidate.ticker}-{ts}-{uuid.uuid4().hex[:6]}"

    def _is_duplicate(self, candidate: AlertCandidate) -> bool:
        """Check notification_audit_log for a recent live send of the same ticker+source."""
        cutoff = (
            datetime.now() - timedelta(hours=self.policy.dedup_window_hours)
        ).isoformat()
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT alert_id FROM notification_audit_log
                WHERE ticker = ? AND source = ?
                  AND shadow = 0 AND dry_run = 0
                  AND delivered = 1
                  AND sent_at >= ?
                LIMIT 1
                """,
                (candidate.ticker, candidate.source, cutoff),
            ).fetchone()
            return row is not None
        except Exception:
            return False
        finally:
            conn.close()

    def _is_rate_limited(self) -> bool:
        now      = time.monotonic()
        hour_ago = now - 3600.0
        while self._rate_window and self._rate_window[0] < hour_ago:
            self._rate_window.popleft()
        return len(self._rate_window) >= self.policy.rate_limit_per_hour

    def _record_rate_tick(self) -> None:
        self._rate_window.append(time.monotonic())

    def _is_snoozed(self, ticker: str) -> bool:
        try:
            from alerts import _is_snoozed
            return _is_snoozed(ticker)
        except Exception:
            return False
