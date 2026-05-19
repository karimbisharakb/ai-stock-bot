"""
Phase A9 — Alpha notification quality-control layer.

Deterministic QC engine that prevents noisy, repetitive, and low-quality
Alpha dry-run notifications from advancing toward live delivery.

Checks for:
  - duplicate suppression / cooldown enforcement
  - readiness instability (rapid flips, score oscillation)
  - insufficient information gain vs prior notifications
  - novelty (first-ever alerts, new setup types, rare validation)
  - priority classification for highest-quality candidates

Observation-only: no Twilio calls, no real notifications sent.

Public API:
  evaluate_notification_quality(candidate, prior_notifications, context)
                                        -> dict  (pure, never raises)
  run_qc_for_dry_runs()                 -> list[dict]
  get_qc_records(ticker, qc_tier, limit)-> list[dict]
  get_qc_summary()                      -> dict
"""
import json
import logging
import statistics
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# ── QC tiers ─────────────────────────────────────────────────────────────────

QC_TIERS = ("BLOCK", "SUPPRESS", "ALLOW", "PRIORITY")

# ── Cooldown windows (hours) per readiness tier ───────────────────────────────

_COOLDOWN_HOURS: dict = {
    "NOT_READY":   0.0,
    "MONITOR":     1.0,
    "PRE_ALERT":   6.0,
    "ALERT_READY": 12.0,
    "RARE_ALERT":  24.0,
}

# ── Stability sub-score constants ─────────────────────────────────────────────

_STAB_BASE               = 60.0
_STAB_CONSISTENT_1       = 20.0   # 1 prior, same tier
_STAB_CONSISTENT_MULTI   = 30.0   # 2+ priors, all same tier
_STAB_FLIP_1_PEN         = -20.0  # 1 tier change in last 24 h
_STAB_FLIP_MULTI_PEN     = -40.0  # 2+ tier changes in last 24 h (rapid flip)
_STAB_OSCILLATION_PEN    = -25.0  # readiness_score std dev > threshold
_STAB_PREALT_LOOP_PEN    = -20.0  # repeated PRE_ALERT ↔ MONITOR pattern
_STAB_OSCILLATION_THRESH = 15.0   # std dev threshold
_STAB_WINDOW_HOURS       = 24.0   # look-back window for flip/oscillation

# ── Information gain sub-score constants ─────────────────────────────────────

_GAIN_FIRST_EVER         = 100.0  # no prior QC records for ticker
_GAIN_BASE               = 40.0
_GAIN_DELTA_LARGE        = 30.0   # readiness_score Δ ≥ 15
_GAIN_DELTA_MED          = 20.0   # 10 ≤ Δ < 15
_GAIN_DELTA_SMALL        = 10.0   # 5 ≤ Δ < 10
_GAIN_NEW_SETUP          = 25.0   # new setup type not seen in prior history
_GAIN_NEW_VALIDATION     = 20.0   # behavior_class changed vs prior
_GAIN_NO_CHANGE_PEN      = -30.0  # Δ < 5 AND same setup AND same tier
_GAIN_DELTA_LARGE_THRESH = 15.0
_GAIN_DELTA_MED_THRESH   = 10.0
_GAIN_DELTA_SMALL_THRESH = 5.0

# ── Novelty sub-score constants ───────────────────────────────────────────────

_NOVELTY_BASE            = 20.0
_NOVELTY_FIRST_EVER      = 50.0   # first QC record for ticker
_NOVELTY_NEW_SETUP       = 30.0   # setup type not in prior QC history
_NOVELTY_RARE_VALIDATION = 20.0   # INSTITUTIONAL_ACCUMULATION or VALID_BREAKOUT
_NOVELTY_SUSTAINED_TREND = 15.0   # SUSTAINED_TREND validation
_NOVELTY_RARE_ALERT      = 20.0   # readiness_tier = RARE_ALERT
_NOVELTY_ALERT_READY     = 10.0   # readiness_tier = ALERT_READY
_NOVELTY_REPEAT_PEN      = -20.0  # same tier + setup as most recent prior

# ── Composite weights ─────────────────────────────────────────────────────────

_W_STABILITY   = 0.35
_W_GAIN        = 0.35
_W_NOVELTY     = 0.30

# ── QC tier thresholds ────────────────────────────────────────────────────────

_PRIORITY_QC_THRESH      = 75.0
_ALLOW_QC_THRESH         = 40.0
_RAPID_FLIP_STAB_THRESH  = 20.0   # hard BLOCK when stability < this AND RAPID_FLIP
_PRIORITY_STAB_THRESH    = 55.0
_PRIORITY_GAIN_THRESH    = 55.0
_PRIORITY_NOVELTY_THRESH = 30.0
_PRIORITY_TRAP_MAX       = 0.30
_PRIORITY_TIERS          = frozenset({"ALERT_READY", "RARE_ALERT"})


# ── Utilities ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now().isoformat()


def _hours_ago_iso(hours: float) -> str:
    return (datetime.now() - timedelta(hours=hours)).isoformat()


# ── Pure sub-score helpers ────────────────────────────────────────────────────

def _check_cooldown(
    ticker: str,
    readiness_tier: str,
    setup_type: str,
    prior_notifications: list,
    cooldown_hours: float,
) -> tuple:
    """
    Returns (cooldown_remaining_hours, is_in_cooldown).

    Suppresses if a prior ALLOWED notification exists for the same
    ticker + readiness_tier + setup_type within the cooldown window.
    """
    if cooldown_hours <= 0:
        return 0.0, False

    cutoff = _hours_ago_iso(cooldown_hours)
    now = datetime.now()

    for prior in prior_notifications:
        if (
            prior.get("ticker") == ticker
            and prior.get("readiness_tier") == readiness_tier
            and prior.get("setup_type") == setup_type
            and prior.get("allow_notification") in (1, True)
        ):
            evaluated_at = prior.get("evaluated_at", "")
            if not evaluated_at or evaluated_at < cutoff:
                continue
            try:
                prior_time = datetime.fromisoformat(evaluated_at)
                elapsed_h  = (now - prior_time).total_seconds() / 3600.0
                remaining  = max(0.0, cooldown_hours - elapsed_h)
                return round(remaining, 2), True
            except (ValueError, TypeError):
                continue

    return 0.0, False


def _compute_stability_score(
    readiness_tier: str,
    readiness_score: float,
    prior_notifications: list,
) -> tuple:
    """Returns (stability_score 0–100, quality_flags list)."""
    flags: list = []

    if not prior_notifications:
        return _STAB_BASE, []

    all_tiers = [p.get("readiness_tier", "") for p in prior_notifications]

    # Recent 24 h window
    cutoff = _hours_ago_iso(_STAB_WINDOW_HOURS)
    recent = [p for p in prior_notifications if p.get("evaluated_at", "") >= cutoff]

    score = _STAB_BASE

    # ── Tier flip detection (recent window) ───────────────────────────────────
    recent_tiers = [p.get("readiness_tier", "") for p in recent]
    if recent_tiers:
        # Include the candidate's current tier as the latest entry
        extended = recent_tiers + [readiness_tier]
        tier_changes = sum(
            1 for i in range(len(extended) - 1)
            if extended[i] != extended[i + 1]
        )
        if tier_changes >= 3:
            score += _STAB_FLIP_MULTI_PEN
            flags.append("RAPID_FLIP")
        elif tier_changes >= 1:
            score += _STAB_FLIP_1_PEN
            flags.append("TIER_FLIP")

    # ── Score oscillation (recent window) ─────────────────────────────────────
    recent_scores = [
        float(p.get("readiness_score") or 0.0)
        for p in recent
        if p.get("readiness_score") is not None
    ]
    if len(recent_scores) >= 2:
        try:
            stdev = statistics.stdev(recent_scores)
            if stdev > _STAB_OSCILLATION_THRESH:
                score += _STAB_OSCILLATION_PEN
                flags.append("SCORE_OSCILLATION")
        except statistics.StatisticsError:
            pass

    # ── PRE_ALERT ↔ MONITOR loop (all history) ────────────────────────────────
    pre_monitor = sum(1 for t in all_tiers if t in ("PRE_ALERT", "MONITOR"))
    if pre_monitor >= 3:
        score += _STAB_PREALT_LOOP_PEN
        flags.append("PRE_ALERT_MONITOR_LOOP")

    # ── Consistency bonus ─────────────────────────────────────────────────────
    last_three = (all_tiers[:3])  # list is DESC, so first 3 are most recent
    same_as_current = sum(1 for t in last_three if t == readiness_tier)
    if same_as_current >= 3:
        score += _STAB_CONSISTENT_MULTI
        flags.append("CONSISTENT_SIGNAL")
    elif same_as_current >= 1:
        score += _STAB_CONSISTENT_1

    return round(max(0.0, min(100.0, score)), 2), flags


def _compute_information_gain_score(
    readiness_tier: str,
    readiness_score: float,
    setup_type: str,
    prior_notifications: list,
    context: dict,
) -> tuple:
    """Returns (information_gain_score 0–100, quality_flags list)."""
    flags: list = []

    if not prior_notifications:
        return _GAIN_FIRST_EVER, ["FIRST_ALERT"]

    score = _GAIN_BASE
    ticker = context.get("ticker", "")

    # Most recent prior record (list is DESC)
    most_recent = prior_notifications[0]
    prior_score = float(most_recent.get("readiness_score") or 0.0)
    delta = abs(readiness_score - prior_score)

    if delta >= _GAIN_DELTA_LARGE_THRESH:
        score += _GAIN_DELTA_LARGE
        flags.append("LARGE_SCORE_DELTA")
    elif delta >= _GAIN_DELTA_MED_THRESH:
        score += _GAIN_DELTA_MED
        flags.append("MEDIUM_SCORE_DELTA")
    elif delta >= _GAIN_DELTA_SMALL_THRESH:
        score += _GAIN_DELTA_SMALL
        flags.append("SMALL_SCORE_DELTA")
    else:
        # No meaningful score change — also check tier and setup
        same_tier  = most_recent.get("readiness_tier") == readiness_tier
        same_setup = most_recent.get("setup_type") == setup_type
        if same_tier and same_setup:
            score += _GAIN_NO_CHANGE_PEN
            flags.append("NO_MEANINGFUL_CHANGE")

    # New setup type vs all prior history
    prior_setups = {p.get("setup_type") for p in prior_notifications}
    if setup_type not in prior_setups:
        score += _GAIN_NEW_SETUP
        flags.append("NEW_SETUP_TYPE")

    # New validation evidence
    validation_summary = context.get("validation_summary", {})
    current_behavior   = validation_summary.get(ticker)
    prior_behaviors    = {p.get("behavior_class") for p in prior_notifications
                          if p.get("behavior_class")}
    if current_behavior and current_behavior not in prior_behaviors:
        score += _GAIN_NEW_VALIDATION
        flags.append("NEW_VALIDATION_EVIDENCE")

    return round(max(0.0, min(100.0, score)), 2), flags


def _compute_novelty_score(
    ticker: str,
    readiness_tier: str,
    setup_type: str,
    prior_notifications: list,
    context: dict,
) -> tuple:
    """Returns (novelty_score 0–100, quality_flags list)."""
    flags: list = []
    score = _NOVELTY_BASE

    # First-ever QC record for this ticker
    if not prior_notifications:
        score += _NOVELTY_FIRST_EVER
        flags.append("FIRST_QC_RECORD")

    # New setup type vs all prior QC history
    if prior_notifications:
        prior_setups = {p.get("setup_type") for p in prior_notifications}
        if setup_type not in prior_setups:
            score += _NOVELTY_NEW_SETUP
            flags.append("NOVEL_SETUP")

    # Readiness tier rarity bonus
    if readiness_tier == "RARE_ALERT":
        score += _NOVELTY_RARE_ALERT
        flags.append("RARE_TIER")
    elif readiness_tier == "ALERT_READY":
        score += _NOVELTY_ALERT_READY

    # Validation behavior rarity
    validation_summary = context.get("validation_summary", {})
    behavior = validation_summary.get(ticker)
    if behavior in ("INSTITUTIONAL_ACCUMULATION", "VALID_BREAKOUT"):
        score += _NOVELTY_RARE_VALIDATION
        flags.append("RARE_VALIDATION")
    elif behavior == "SUSTAINED_TREND":
        score += _NOVELTY_SUSTAINED_TREND
        flags.append("SUSTAINED_TREND")

    # Repeat penalty: same tier + setup as most recent allowed prior
    if prior_notifications:
        most_recent = prior_notifications[0]  # DESC order
        if (
            most_recent.get("readiness_tier") == readiness_tier
            and most_recent.get("setup_type") == setup_type
        ):
            score += _NOVELTY_REPEAT_PEN
            flags.append("REPEAT_SIGNAL")

    return round(max(0.0, min(100.0, score)), 2), flags


def _empty_qc_result() -> dict:
    return {
        "allow_notification":      False,
        "qc_score":                0.0,
        "qc_tier":                 "BLOCK",
        "suppression_reason":      "QC_EVALUATION_ERROR",
        "cooldown_remaining":      0.0,
        "quality_flags":           ["QC_ERROR"],
        "novelty_score":           0.0,
        "stability_score":         0.0,
        "information_gain_score":  0.0,
    }


# ── Public pure function ──────────────────────────────────────────────────────

def evaluate_notification_quality(
    candidate: dict,
    prior_notifications: list,
    context: Optional[dict] = None,
) -> dict:
    """
    Evaluate the quality of a proposed notification.

    Pure function — no DB access.  Never raises.

    Args:
        candidate:          dict with ticker, readiness_tier, readiness_score,
                            alpha_score, alpha_tier, setup_type.
        prior_notifications: list of prior notification_qc_history rows for
                            this ticker (sorted DESC by evaluated_at).
        context:            dict with:
                              trap_rates        : dict(setup_type → float)
                              validation_summary: dict(ticker → behavior_class)

    Returns dict with allow_notification, qc_score, qc_tier,
    suppression_reason, cooldown_remaining, quality_flags,
    novelty_score, stability_score, information_gain_score.
    """
    try:
        return _evaluate_inner(candidate, prior_notifications or [], context or {})
    except Exception as exc:
        log.warning("alpha_notification_qc: evaluate_notification_quality failed: %s",
                    exc, exc_info=True)
        return _empty_qc_result()


def _evaluate_inner(candidate: dict, prior_notifications: list, context: dict) -> dict:
    ticker          = candidate.get("ticker", "")
    readiness_tier  = candidate.get("readiness_tier") or "NOT_READY"
    readiness_score = float(candidate.get("readiness_score") or 0.0)
    alpha_tier      = candidate.get("alpha_tier") or "WATCH"
    setup_type      = candidate.get("setup_type") or "UNKNOWN"

    quality_flags: list = []

    # ── A. Cooldown / duplicate suppression ───────────────────────────────────
    cooldown_hours     = _COOLDOWN_HOURS.get(readiness_tier, 6.0)
    cooldown_remaining, is_in_cooldown = _check_cooldown(
        ticker, readiness_tier, setup_type, prior_notifications, cooldown_hours,
    )
    if is_in_cooldown:
        quality_flags.append("IN_COOLDOWN")

    # ── B. Stability ──────────────────────────────────────────────────────────
    stability_score, stab_flags = _compute_stability_score(
        readiness_tier, readiness_score, prior_notifications,
    )
    quality_flags.extend(stab_flags)

    # ── C. Information gain ───────────────────────────────────────────────────
    information_gain_score, gain_flags = _compute_information_gain_score(
        readiness_tier, readiness_score, setup_type,
        prior_notifications, {**context, "ticker": ticker},
    )
    quality_flags.extend(gain_flags)

    # ── D. Novelty ────────────────────────────────────────────────────────────
    novelty_score, novelty_flags = _compute_novelty_score(
        ticker, readiness_tier, setup_type, prior_notifications, context,
    )
    quality_flags.extend(novelty_flags)

    # ── Composite QC score ────────────────────────────────────────────────────
    qc_score = round(min(100.0, max(0.0,
        _W_STABILITY * stability_score
        + _W_GAIN     * information_gain_score
        + _W_NOVELTY  * novelty_score
    )), 2)

    # ── QC tier and suppression decision ──────────────────────────────────────
    suppression_reason = None
    allow_notification = True

    if is_in_cooldown:
        allow_notification = False
        suppression_reason = f"IN_COOLDOWN:{cooldown_remaining:.1f}h"
        qc_tier = "BLOCK"

    elif "RAPID_FLIP" in quality_flags and stability_score < _RAPID_FLIP_STAB_THRESH:
        allow_notification = False
        suppression_reason = "UNSTABLE_READINESS"
        qc_tier = "BLOCK"

    elif qc_score < _ALLOW_QC_THRESH:
        allow_notification = False
        suppression_reason = "LOW_QC_SCORE"
        qc_tier = "SUPPRESS"

    else:
        # Check for PRIORITY
        trap_rates = context.get("trap_rates", {})
        trap_rate  = float(trap_rates.get(setup_type) or 0.0)
        if (
            readiness_tier in _PRIORITY_TIERS
            and stability_score          >= _PRIORITY_STAB_THRESH
            and information_gain_score   >= _PRIORITY_GAIN_THRESH
            and novelty_score            >= _PRIORITY_NOVELTY_THRESH
            and trap_rate                <  _PRIORITY_TRAP_MAX
            and qc_score                 >= _PRIORITY_QC_THRESH
        ):
            qc_tier = "PRIORITY"
        else:
            qc_tier = "ALLOW"

    return {
        "allow_notification":      allow_notification,
        "qc_score":                qc_score,
        "qc_tier":                 qc_tier,
        "suppression_reason":      suppression_reason,
        "cooldown_remaining":      round(cooldown_remaining, 2),
        "quality_flags":           list(dict.fromkeys(quality_flags)),
        "novelty_score":           round(novelty_score, 2),
        "stability_score":         round(stability_score, 2),
        "information_gain_score":  round(information_gain_score, 2),
    }


# ── DB table management ───────────────────────────────────────────────────────

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS notification_qc_history (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker                 TEXT    NOT NULL,
    readiness_tier         TEXT    NOT NULL,
    alpha_tier             TEXT    NOT NULL,
    setup_type             TEXT    NOT NULL,
    alpha_score            REAL,
    readiness_score        REAL,
    qc_score               REAL    NOT NULL,
    qc_tier                TEXT    NOT NULL,
    allow_notification     INTEGER NOT NULL DEFAULT 0,
    suppression_reason     TEXT,
    cooldown_remaining     REAL    NOT NULL DEFAULT 0.0,
    novelty_score          REAL    NOT NULL DEFAULT 0.0,
    stability_score        REAL    NOT NULL DEFAULT 0.0,
    information_gain_score REAL    NOT NULL DEFAULT 0.0,
    quality_flags_json     TEXT    NOT NULL DEFAULT '[]',
    behavior_class         TEXT,
    dry_run_id             TEXT,
    evaluated_at           TEXT    NOT NULL
)
"""


def _ensure_table() -> None:
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(_TABLE_DDL)
        conn.commit()
    except Exception:
        log.warning("alpha_notification_qc: table creation failed", exc_info=True)
    finally:
        conn.close()


# ── Context builder ───────────────────────────────────────────────────────────

def _build_qc_context() -> dict:
    """Load trap_rates and validation_summary. Never raises."""
    trap_rates:         dict = {}
    validation_summary: dict = {}

    try:
        from alpha_validation import get_validations, compute_setup_trap_rates
        validations = get_validations(limit=500)
        if validations:
            for v in reversed(validations):
                validation_summary[v["ticker"]] = v["behavior_class"]
            trap_rates = compute_setup_trap_rates(validations)
    except Exception:
        log.warning("alpha_notification_qc: validation context load failed", exc_info=True)

    return {"trap_rates": trap_rates, "validation_summary": validation_summary}


# ── Write operations ──────────────────────────────────────────────────────────

def _record_qc_result(
    conn,
    candidate: dict,
    qc_result: dict,
    dry_run_id: Optional[str] = None,
    behavior_class: Optional[str] = None,
) -> None:
    """Insert one QC result row (caller owns conn and commit)."""
    conn.execute(
        """
        INSERT INTO notification_qc_history
            (ticker, readiness_tier, alpha_tier, setup_type,
             alpha_score, readiness_score, qc_score, qc_tier,
             allow_notification, suppression_reason, cooldown_remaining,
             novelty_score, stability_score, information_gain_score,
             quality_flags_json, behavior_class, dry_run_id, evaluated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            candidate.get("ticker", ""),
            candidate.get("readiness_tier", "NOT_READY"),
            candidate.get("alpha_tier", "WATCH"),
            candidate.get("setup_type", "UNKNOWN"),
            candidate.get("alpha_score"),
            candidate.get("readiness_score"),
            qc_result["qc_score"],
            qc_result["qc_tier"],
            1 if qc_result["allow_notification"] else 0,
            qc_result["suppression_reason"],
            qc_result["cooldown_remaining"],
            qc_result["novelty_score"],
            qc_result["stability_score"],
            qc_result["information_gain_score"],
            json.dumps(qc_result["quality_flags"]),
            behavior_class,
            dry_run_id,
            _now_iso(),
        ),
    )


def run_qc_for_dry_runs() -> list:
    """
    Run QC evaluation for all eligible Alpha candidates.

    1. Fetches gate-scored candidates from A7 (includes readiness_score).
    2. Matches against active A8 dry-runs for dry_run_id cross-reference.
    3. Loads prior QC history per ticker.
    4. Evaluates quality and writes to notification_qc_history.

    Returns list of QC result dicts.  Never raises.
    """
    _ensure_table()

    try:
        from alpha_alert_gate import get_alert_candidates
        candidates = get_alert_candidates(limit=50)
    except Exception as exc:
        log.warning("alpha_notification_qc: could not fetch candidates: %s", exc)
        return []

    eligible = [c for c in candidates
                if c.get("readiness_tier") in ("PRE_ALERT", "ALERT_READY", "RARE_ALERT")]
    if not eligible:
        log.info("alpha_notification_qc: no eligible candidates for QC")
        return []

    # Build ticker → dry_run_id map from active A8 dry-runs
    dry_run_id_map: dict = {}
    try:
        from alpha_notification_dryrun import get_dry_runs
        for dr in get_dry_runs(status_filter="DRY_RUN", limit=100):
            dry_run_id_map[dr["ticker"]] = dr["dry_run_id"]
    except Exception:
        pass

    context = _build_qc_context()
    validation_summary = context.get("validation_summary", {})
    results: list = []

    from database import get_connection
    conn = get_connection()
    try:
        for gate_result in eligible:
            ticker = gate_result.get("ticker", "")

            prior_rows = conn.execute(
                """SELECT * FROM notification_qc_history
                   WHERE ticker = ?
                   ORDER BY evaluated_at DESC LIMIT 50""",
                (ticker,),
            ).fetchall()
            prior_list = [dict(r) for r in prior_rows]

            candidate = {
                "ticker":          ticker,
                "readiness_tier":  gate_result.get("readiness_tier", "NOT_READY"),
                "readiness_score": float(gate_result.get("readiness_score") or 0.0),
                "alpha_score":     gate_result.get("alpha_score"),
                "alpha_tier":      gate_result.get("alpha_tier", "WATCH"),
                "setup_type":      gate_result.get("setup_type", "UNKNOWN"),
            }

            qc_result    = evaluate_notification_quality(candidate, prior_list, context)
            dry_run_id   = dry_run_id_map.get(ticker)
            behavior     = validation_summary.get(ticker)

            _record_qc_result(conn, candidate, qc_result,
                              dry_run_id=dry_run_id, behavior_class=behavior)
            results.append({"ticker": ticker, "dry_run_id": dry_run_id, **qc_result})

        conn.commit()
        log.info("alpha_notification_qc: QC run complete — %d candidates evaluated",
                 len(results))
        return results

    except Exception:
        log.warning("alpha_notification_qc: run_qc_for_dry_runs DB error", exc_info=True)
        return []
    finally:
        conn.close()


# ── Read operations ───────────────────────────────────────────────────────────

def get_qc_records(
    ticker: Optional[str] = None,
    qc_tier: Optional[str] = None,
    limit: int = 50,
) -> list:
    """
    Return QC history rows ordered by evaluated_at DESC.
    Optional ticker and qc_tier filters.  Never raises.
    """
    _ensure_table()
    from database import get_connection
    conn = get_connection()
    try:
        where_parts: list = []
        params:      list = []
        if ticker:
            where_parts.append("ticker = ?")
            params.append(ticker)
        if qc_tier:
            where_parts.append("qc_tier = ?")
            params.append(qc_tier)

        where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        params.append(limit)

        rows = conn.execute(
            f"SELECT * FROM notification_qc_history {where_clause} "
            "ORDER BY evaluated_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("alpha_notification_qc: get_qc_records failed", exc_info=True)
        return []
    finally:
        conn.close()


def get_qc_summary() -> dict:
    """
    Aggregate QC statistics across recent records.

    Never raises.  Returns a safe empty structure when no data exists.
    """
    empty: dict = {
        "total_evaluated":          0,
        "allowed_count":            0,
        "suppressed_count":         0,
        "duplicate_suppressions":   0,
        "unstable_suppressions":    0,
        "low_quality_suppressions": 0,
        "priority_candidates":      0,
        "cooldown_active_count":    0,
        "avg_qc_score":             0.0,
        "qc_tier_distribution":     {},
        "note":                     "No QC records found",
        "generated_at":             _now_iso(),
    }

    try:
        _ensure_table()
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM notification_qc_history ORDER BY evaluated_at DESC LIMIT 200"
            ).fetchall()
            records = [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        log.warning("alpha_notification_qc: get_qc_summary failed", exc_info=True)
        return empty

    if not records:
        return empty

    total     = len(records)
    allowed   = sum(1 for r in records if r.get("allow_notification"))
    suppressed = total - allowed

    dup_suppress = sum(
        1 for r in records
        if (r.get("suppression_reason") or "").startswith("IN_COOLDOWN")
    )
    unstable_suppress = sum(
        1 for r in records
        if r.get("suppression_reason") == "UNSTABLE_READINESS"
    )
    low_quality_suppress = sum(
        1 for r in records
        if r.get("suppression_reason") == "LOW_QC_SCORE"
    )
    priority_count = sum(1 for r in records if r.get("qc_tier") == "PRIORITY")

    cooldown_active = {
        r["ticker"]
        for r in records
        if float(r.get("cooldown_remaining") or 0.0) > 0.0
    }

    scores    = [float(r["qc_score"]) for r in records if r.get("qc_score") is not None]
    avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0

    tier_dist: dict = {}
    for r in records:
        t = r.get("qc_tier", "UNKNOWN")
        tier_dist[t] = tier_dist.get(t, 0) + 1

    return {
        "total_evaluated":          total,
        "allowed_count":            allowed,
        "suppressed_count":         suppressed,
        "duplicate_suppressions":   dup_suppress,
        "unstable_suppressions":    unstable_suppress,
        "low_quality_suppressions": low_quality_suppress,
        "priority_candidates":      priority_count,
        "cooldown_active_count":    len(cooldown_active),
        "avg_qc_score":             avg_score,
        "qc_tier_distribution":     tier_dist,
        "note":                     "Simulation only — no WhatsApp alerts sent",
        "generated_at":             _now_iso(),
    }
