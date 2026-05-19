"""
Phase A8 — Alpha notification dry-run review.

Generates proposed WhatsApp messages for Alpha candidates at PRE_ALERT,
ALERT_READY, or RARE_ALERT readiness tiers and stores them as DRY_RUN rows
for human review before any real notification is ever sent.

Observation-only: no Twilio calls, no send_sms, no live notifications.
No real messages are sent at any point.

Public API:
  generate_notification_text(candidate, gate_result) -> str  (pure, never raises)
  check_banned_words(text)                           -> list[str]
  generate_dry_runs()                                -> list[dict]
  get_dry_runs(status_filter, limit)                 -> list[dict]
  mark_reviewed(dry_run_id, actor, note)             -> dict
  dismiss_dry_run(dry_run_id, reason, actor)         -> dict
  expire_stale_dry_runs()                            -> int
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# ── Banned words (case-insensitive) ──────────────────────────────────────────

BANNED_WORDS: frozenset = frozenset({
    "moon", "mooning", "mooned",
    "explosion", "explode", "exploding", "exploded",
    "rocket", "rocketship",
    "pump", "pumping", "pumped",
    "must buy", "must-buy",
    "guaranteed", "guarantee",
    "certain", "certainly",
    "definitely",
    "skyrocket", "skyrocketing",
    "surge", "surging",
    "blast off", "blastoff",
    "soar", "soaring",
    "lambo", "ape", "yolo",
})

# ── Message content constants ─────────────────────────────────────────────────

_ELIGIBLE_READINESS_TIERS = frozenset({"PRE_ALERT", "ALERT_READY", "RARE_ALERT"})

_DRY_RUN_EXPIRE_HOURS = 48

# Readiness tier → header prefix and status label
_TIER_HEADER: dict = {
    "PRE_ALERT":   "ALPHA WATCH",
    "ALERT_READY": "ALPHA ALERT",
    "RARE_ALERT":  "ALPHA RARE SETUP",
}
_TIER_STATUS_LABEL: dict = {
    "PRE_ALERT":   "Almost ready",
    "ALERT_READY": "Alert ready",
    "RARE_ALERT":  "Rare setup — alert ready",
}

# Setup type → human-readable label
_SETUP_LABELS: dict = {
    "BREAKOUT_EXPANSION": "Breakout expansion",
    "SQUEEZE_CANDIDATE":  "Squeeze candidate",
    "CATALYST_RUNUP":     "Catalyst runup",
    "OPTIONS_PRESSURE":   "Options pressure",
    "EARLY_ACCUMULATION": "Early accumulation",
    "UNKNOWN":            "Mixed signals",
}

# Component name → brief "Why" bullet text
_COMPONENT_REASONS: dict = {
    "relative_strength": "Strong relative strength",
    "acceleration":      "Price momentum accelerating",
    "squeeze":           "Squeeze pattern forming",
    "catalyst":          "Catalyst identified",
    "options":           "Unusual options activity",
    "breakout":          "Breakout pattern present",
    "risk_reward":       "Favorable risk / reward ratio",
    "novelty":           "Fresh signal — not repeat",
}

# Confirmation need key → human-readable label
_CONFIRMATION_LABELS: dict = {
    "volume_confirmation":        "Volume confirmation needed",
    "price_holds_breakout_level": "Price must hold breakout level",
    "relative_strength_continues":"Relative strength must continue",
    "catalyst_confirmation":      "Catalyst confirmation needed",
    "risk_reward_improves":       "Risk / reward must improve",
    "options_activity_persists":  "Options activity must persist",
    "volatility_cools_down":      "Volatility must cool first",
}

# Blocking factor prefix → brief risk label
_BLOCKER_LABELS: dict = {
    "missing component":          "Limited data quality",
    "no component":               "Limited data quality",
    "high trap rate":             "High setup trap rate",
    "recent validation: volatility_trap": "Previous volatility trap",
    "recent validation: failed_squeeze":  "Previous failed squeeze",
    "recent validation: short_lived_spike": "Previous short-lived spike",
    "recent validation: failed_breakout":   "Previous failed breakout",
    "recent duplicate":           "Recently alerted",
    "alpha tier":                 None,  # internal cap — suppress from output
}

# Status transitions
_VALID_TRANSITIONS: dict = {
    "DRY_RUN":   {"REVIEWED", "DISMISSED", "EXPIRED"},
    "REVIEWED":  {"EXPIRED"},
    "DISMISSED": set(),
    "EXPIRED":   set(),
}

_ACTIVE_STATUSES = ("DRY_RUN", "REVIEWED")


# ── Pure helpers ───────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now().isoformat()


def _dry_run_id(ticker: str, readiness_tier: str, alpha_tier: str, setup_type: str) -> str:
    """Deterministic 16-char hex ID from candidate identity fields."""
    payload = json.dumps(
        {"ticker": ticker, "readiness_tier": readiness_tier,
         "alpha_tier": alpha_tier, "setup_type": setup_type},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _format_setup_label(setup_type: Optional[str]) -> str:
    if not setup_type:
        return "Mixed signals"
    return _SETUP_LABELS.get(setup_type, setup_type.replace("_", " ").title())


def _extract_why_bullets(candidate: dict, gate_result: dict, max_bullets: int = 4) -> list:
    """Derive 'Why' bullets from component scores, falling back to reason text."""
    bullets: list = []

    # Try component scores first
    raw = candidate.get("component_scores_json")
    if raw:
        try:
            components = json.loads(raw)
            if isinstance(components, dict):
                scored = [
                    (comp, float(info.get("score") or 0.0))
                    for comp, info in components.items()
                    if isinstance(info, dict)
                    and info.get("data_quality") != "MISSING"
                    and info.get("score") is not None
                ]
                scored.sort(key=lambda x: -x[1])
                for comp, _ in scored[:max_bullets]:
                    label = _COMPONENT_REASONS.get(comp)
                    if label and label not in bullets:
                        bullets.append(label)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # Fallback: extract from reason string
    if not bullets:
        reason = gate_result.get("reason", "")
        if reason:
            bullets.append("Score and tier support this setup")
        else:
            bullets.append("Alpha scan flagged this ticker")

    return bullets[:max_bullets]


def _format_risk_bullets(blocking_factors: list, max_bullets: int = 3) -> list:
    """Convert blocking factors to short user-facing risk bullets."""
    seen: set = set()
    result: list = []
    for factor in blocking_factors:
        factor_lower = factor.lower()
        label: Optional[str] = None
        for key, text in _BLOCKER_LABELS.items():
            if key in factor_lower:
                label = text
                break
        if label is None:
            # Generic fallback — truncate to 50 chars
            label = factor[:50].rstrip(":").strip()
        if label and label not in seen:
            seen.add(label)
            result.append(label)
        if len(result) >= max_bullets:
            break
    return result


def generate_notification_text(candidate: dict, gate_result: dict) -> str:
    """
    Generate a proposed WhatsApp message for an alert-candidate.

    Pure function — no DB access.  Never raises.
    Returns a plain-text message ready for human review.
    """
    try:
        return _generate_notification_text_inner(candidate, gate_result)
    except Exception as exc:
        log.warning("alpha_notification_dryrun: generate_notification_text failed: %s", exc,
                    exc_info=True)
        ticker = candidate.get("ticker", "UNKNOWN")
        return (
            f"ALPHA WATCH — {ticker}\n\n"
            "Status: Review pending\n\n"
            "No trade placed.\nAdvisory only."
        )


def _generate_notification_text_inner(candidate: dict, gate_result: dict) -> str:
    ticker         = (candidate.get("ticker") or "UNKNOWN").upper()
    readiness_tier = gate_result.get("readiness_tier") or "PRE_ALERT"
    alpha_score    = float(candidate.get("alpha_score") or 0.0)
    setup_type     = candidate.get("setup_type") or "UNKNOWN"

    header       = _TIER_HEADER.get(readiness_tier, "ALPHA WATCH")
    status_label = _TIER_STATUS_LABEL.get(readiness_tier, "Under review")
    setup_label  = _format_setup_label(setup_type)

    why_bullets  = _extract_why_bullets(candidate, gate_result)
    conf_needed  = gate_result.get("confirmation_needed") or []
    blockers     = gate_result.get("blocking_factors") or []

    needs_bullets = [_CONFIRMATION_LABELS.get(c, c.replace("_", " ").capitalize())
                     for c in conf_needed[:4]]
    risk_bullets  = _format_risk_bullets(blockers)

    lines = [
        f"{header} — {ticker}",
        "",
        f"Status: {status_label}",
        f"Score: {alpha_score:.1f}",
        f"Setup: {setup_label}",
    ]

    if why_bullets:
        lines += ["", "Why:"]
        lines += [f"• {b}" for b in why_bullets]

    if needs_bullets:
        lines += ["", "Needs:"]
        lines += [f"• {b}" for b in needs_bullets]

    if risk_bullets:
        lines += ["", "Risk:"]
        lines += [f"• {b}" for b in risk_bullets]

    lines += ["", "No trade placed.", "Advisory only."]

    return "\n".join(lines)


def check_banned_words(text: str) -> list:
    """
    Return list of banned words/phrases found in text (case-insensitive).

    Returns empty list when text is clean.
    """
    text_lower = text.lower()
    return [w for w in BANNED_WORDS if w in text_lower]


# ── DB table management ───────────────────────────────────────────────────────

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS alpha_notification_dryruns (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    dry_run_id               TEXT    NOT NULL UNIQUE,
    ticker                   TEXT    NOT NULL,
    readiness_tier           TEXT    NOT NULL,
    alpha_score              REAL,
    alpha_tier               TEXT,
    setup_type               TEXT,
    message_text             TEXT    NOT NULL,
    reason                   TEXT,
    blocking_factors_json    TEXT,
    confirmation_needed_json TEXT,
    status                   TEXT    NOT NULL DEFAULT 'DRY_RUN',
    created_at               TEXT    NOT NULL,
    expires_at               TEXT    NOT NULL,
    reviewed_at              TEXT,
    reviewed_by              TEXT,
    review_note              TEXT,
    dismissed_at             TEXT,
    dismissed_by             TEXT,
    dismiss_reason           TEXT
)
"""


def _ensure_table() -> None:
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(_TABLE_DDL)
        conn.commit()
    except Exception:
        log.warning("alpha_notification_dryrun: table creation failed", exc_info=True)
    finally:
        conn.close()


# ── Status transitions ────────────────────────────────────────────────────────

def _fetch_dry_run(conn, dry_run_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM alpha_notification_dryruns WHERE dry_run_id = ?", (dry_run_id,)
    ).fetchone()
    return dict(row) if row else None


def _transition_status(
    dry_run_id: str,
    to_status: str,
    extra_fields: dict,
) -> dict:
    """Apply a status transition, returning the updated row. Raises ValueError on bad transition."""
    _ensure_table()
    from database import get_connection
    conn = get_connection()
    try:
        row = _fetch_dry_run(conn, dry_run_id)
        if row is None:
            raise ValueError(f"Dry run {dry_run_id!r} not found")

        current = row["status"]
        allowed = _VALID_TRANSITIONS.get(current, set())
        if to_status not in allowed:
            raise ValueError(
                f"Invalid transition {current!r} → {to_status!r}; "
                f"allowed from {current!r}: {sorted(allowed) or 'none (terminal)'}"
            )

        # Build SET clause from extra_fields + status
        fields = {"status": to_status, **extra_fields}
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE alpha_notification_dryruns SET {set_clause} WHERE dry_run_id = ?",
            list(fields.values()) + [dry_run_id],
        )
        conn.commit()
        row.update(fields)
        return row
    finally:
        conn.close()


# ── Core write operations ─────────────────────────────────────────────────────

def generate_dry_runs() -> list:
    """
    Generate DRY_RUN notification rows for all eligible Alpha candidates.

    Eligible: readiness_tier in (PRE_ALERT, ALERT_READY, RARE_ALERT).
    Idempotent: same candidate identity → same dry_run_id → INSERT OR IGNORE.

    Returns list of dry-run dicts (newly inserted + any already-existing).
    Never raises — errors are logged.
    """
    _ensure_table()

    try:
        from alpha_alert_gate import get_alert_candidates
        candidates = get_alert_candidates(limit=50)
    except Exception as exc:
        log.warning("alpha_notification_dryrun: could not fetch candidates: %s", exc)
        return []

    eligible = [c for c in candidates if c.get("readiness_tier") in _ELIGIBLE_READINESS_TIERS]
    if not eligible:
        log.info("alpha_notification_dryrun: no eligible candidates for dry run")
        return []

    now        = _now_iso()
    expires_at = (datetime.now() + timedelta(hours=_DRY_RUN_EXPIRE_HOURS)).isoformat()
    processed_ids: list = []

    from database import get_connection
    conn = get_connection()
    try:
        for gate_result in eligible:
            ticker         = gate_result.get("ticker", "")
            readiness_tier = gate_result.get("readiness_tier", "")
            alpha_tier     = gate_result.get("alpha_tier", "")
            setup_type     = gate_result.get("setup_type", "")
            alpha_score    = gate_result.get("alpha_score")

            dry_id = _dry_run_id(ticker, readiness_tier, alpha_tier, setup_type)

            # We need the raw candidate to generate the message
            candidate = {
                "ticker":                 ticker,
                "alpha_score":            alpha_score,
                "alpha_tier":             alpha_tier,
                "setup_type":             setup_type,
                "component_scores_json":  None,  # not available in gate_result
            }
            message_text = generate_notification_text(candidate, gate_result)

            # Safety: check banned words — skip if found
            found = check_banned_words(message_text)
            if found:
                log.warning(
                    "alpha_notification_dryrun: banned words %s in message for %s — skipping",
                    found, ticker,
                )
                continue

            result = conn.execute(
                """
                INSERT OR IGNORE INTO alpha_notification_dryruns
                    (dry_run_id, ticker, readiness_tier, alpha_score, alpha_tier, setup_type,
                     message_text, reason, blocking_factors_json, confirmation_needed_json,
                     status, created_at, expires_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    dry_id, ticker, readiness_tier, alpha_score, alpha_tier, setup_type,
                    message_text,
                    gate_result.get("reason"),
                    json.dumps(gate_result.get("blocking_factors") or []),
                    json.dumps(gate_result.get("confirmation_needed") or []),
                    "DRY_RUN", now, expires_at,
                ),
            )
            if result.rowcount > 0:
                log.info("alpha_notification_dryrun: inserted %s (%s)", dry_id, ticker)
            processed_ids.append(dry_id)

        conn.commit()

        if not processed_ids:
            return []

        placeholders = ",".join("?" * len(processed_ids))
        rows = conn.execute(
            f"SELECT * FROM alpha_notification_dryruns WHERE dry_run_id IN ({placeholders})",
            processed_ids,
        ).fetchall()
        return [dict(r) for r in rows]

    except Exception:
        log.warning("alpha_notification_dryrun: generate_dry_runs DB error", exc_info=True)
        return []
    finally:
        conn.close()


# ── Read operations ───────────────────────────────────────────────────────────

def get_dry_runs(
    status_filter: Optional[str] = None,
    limit: int = 50,
) -> list:
    """
    Return dry-run rows ordered by created_at DESC.
    status_filter: one of DRY_RUN, REVIEWED, DISMISSED, EXPIRED (or None for all active).
    """
    _ensure_table()
    expire_stale_dry_runs()

    from database import get_connection
    conn = get_connection()
    try:
        if status_filter:
            rows = conn.execute(
                "SELECT * FROM alpha_notification_dryruns WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status_filter, limit),
            ).fetchall()
        else:
            placeholders = ",".join("?" * len(_ACTIVE_STATUSES))
            rows = conn.execute(
                f"SELECT * FROM alpha_notification_dryruns WHERE status IN ({placeholders}) "
                "ORDER BY created_at DESC LIMIT ?",
                list(_ACTIVE_STATUSES) + [limit],
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("alpha_notification_dryrun: get_dry_runs failed", exc_info=True)
        return []
    finally:
        conn.close()


# ── Review workflow ───────────────────────────────────────────────────────────

def mark_reviewed(
    dry_run_id: str,
    actor: Optional[str] = "api",
    note: Optional[str] = None,
) -> dict:
    """Transition DRY_RUN → REVIEWED. Raises ValueError on bad transition."""
    return _transition_status(dry_run_id, "REVIEWED", {
        "reviewed_at": _now_iso(),
        "reviewed_by": actor,
        "review_note": note,
    })


def dismiss_dry_run(
    dry_run_id: str,
    reason: Optional[str] = None,
    actor: Optional[str] = "api",
) -> dict:
    """Transition DRY_RUN → DISMISSED. Raises ValueError on bad transition."""
    return _transition_status(dry_run_id, "DISMISSED", {
        "dismissed_at":   _now_iso(),
        "dismissed_by":   actor,
        "dismiss_reason": reason,
    })


def expire_stale_dry_runs() -> int:
    """
    Mark DRY_RUN and REVIEWED rows as EXPIRED when past their expires_at.

    Returns count of rows expired.
    """
    _ensure_table()
    from database import get_connection
    conn   = get_connection()
    now    = _now_iso()
    count  = 0
    try:
        rows = conn.execute(
            """
            SELECT dry_run_id, status FROM alpha_notification_dryruns
             WHERE status IN ('DRY_RUN', 'REVIEWED')
               AND expires_at < ?
            """,
            (now,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE alpha_notification_dryruns SET status = 'EXPIRED' WHERE dry_run_id = ?",
                (row["dry_run_id"],),
            )
            count += 1
        conn.commit()
        if count:
            log.info("alpha_notification_dryrun: expired %d stale dry runs", count)
    except Exception:
        log.warning("alpha_notification_dryrun: expire_stale_dry_runs failed", exc_info=True)
    finally:
        conn.close()
    return count
