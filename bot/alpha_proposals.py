"""
Phase L3 — Controlled promotion workflow for Alpha learning recommendations.

Manages proposal lifecycle:
  PROPOSED → APPROVED_FOR_SHADOW → ROLLBACK_READY
  PROPOSED → REJECTED
  PROPOSED | APPROVED_FOR_SHADOW → EXPIRED

Never applies live weight changes.  Never sends alerts.  Never affects Predator.

Public API:
  generate_proposals()                          -> list[dict]
  approve_for_shadow(proposal_id, actor, note)  -> dict
  reject_proposal(proposal_id, reason, actor)   -> dict
  expire_stale_proposals()                      -> int
  get_proposals(status_filter, include_historical) -> list[dict]
  get_shadow_results(proposal_id)               -> dict
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_PROPOSAL_EXPIRE_DAYS        = 30
_MIN_CONFIDENCE_FOR_PROPOSAL = frozenset({"HIGH", "MEDIUM"})
_MIN_OUTCOMES_FOR_PROPOSAL   = 10    # same as alpha_learning_engine._MIN_SAMPLES

# Valid status transitions — terminal states have empty sets
_VALID_TRANSITIONS: dict = {
    "PROPOSED":           {"APPROVED_FOR_SHADOW", "REJECTED", "EXPIRED"},
    "APPROVED_FOR_SHADOW": {"ROLLBACK_READY", "EXPIRED"},
    "REJECTED":           set(),
    "EXPIRED":            set(),
    "ROLLBACK_READY":     set(),
}

_ACTIVE_STATUSES    = ("PROPOSED", "APPROVED_FOR_SHADOW")
_TERMINAL_STATUSES  = ("REJECTED", "EXPIRED", "ROLLBACK_READY")


# ── Table bootstrap ───────────────────────────────────────────────────────────

_PROPOSALS_DDL = """
CREATE TABLE IF NOT EXISTS learning_proposals (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id            TEXT    NOT NULL UNIQUE,
    status                 TEXT    NOT NULL DEFAULT 'PROPOSED',
    kind                   TEXT    NOT NULL,
    weight_changes_json    TEXT,
    threshold_changes_json TEXT,
    shadow_weights_json    TEXT,
    evidence_summary       TEXT,
    sample_size            INTEGER NOT NULL DEFAULT 0,
    confidence             TEXT    NOT NULL,
    risk_warning           TEXT,
    expected_benefit       TEXT,
    expected_downside      TEXT,
    created_at             TEXT    NOT NULL,
    expires_at             TEXT    NOT NULL,
    reviewed_at            TEXT,
    reviewed_by            TEXT,
    review_note            TEXT,
    shadow_results_json    TEXT
)
"""

_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS learning_proposal_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT    NOT NULL,
    from_status TEXT,
    to_status   TEXT    NOT NULL,
    reason      TEXT,
    actor       TEXT,
    ts          TEXT    NOT NULL
)
"""


def _ensure_tables() -> None:
    """Create proposal tables if missing (idempotent — safe to call every request)."""
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(_PROPOSALS_DDL)
        conn.execute(_AUDIT_DDL)
        conn.commit()
    except Exception:
        log.warning("alpha_proposals: table bootstrap failed", exc_info=True)
    finally:
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _proposal_id_hash(
    weight_changes: dict,
    threshold_changes: dict,
    sample_size: int,
) -> str:
    """Deterministic 16-char hex ID from proposal content."""
    payload = json.dumps(
        {"w": weight_changes, "t": threshold_changes, "n": sample_size},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now().isoformat()


def _write_audit(
    conn,
    proposal_id: str,
    from_status: Optional[str],
    to_status: str,
    reason: Optional[str],
    actor: Optional[str],
) -> None:
    """Append one immutable audit entry. Never updates existing rows."""
    conn.execute(
        """
        INSERT INTO learning_proposal_audit
            (proposal_id, from_status, to_status, reason, actor, ts)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (proposal_id, from_status, to_status, reason, actor, _now_iso()),
    )


def _fetch_proposal(conn, proposal_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM learning_proposals WHERE proposal_id = ?", (proposal_id,)
    ).fetchone()
    return dict(row) if row else None


def _load_proposals(conn, *, status_filter: Optional[str] = None, include_historical: bool = False) -> list:
    if status_filter:
        rows = conn.execute(
            "SELECT * FROM learning_proposals WHERE status = ? ORDER BY created_at DESC",
            (status_filter,),
        ).fetchall()
    elif include_historical:
        rows = conn.execute(
            "SELECT * FROM learning_proposals ORDER BY created_at DESC"
        ).fetchall()
    else:
        placeholders = ",".join("?" * len(_ACTIVE_STATUSES))
        rows = conn.execute(
            f"SELECT * FROM learning_proposals WHERE status IN ({placeholders}) ORDER BY created_at DESC",
            _ACTIVE_STATUSES,
        ).fetchall()
    return [dict(r) for r in rows]


def _transition_status(
    proposal_id: str,
    to_status: str,
    reason: Optional[str],
    actor: Optional[str],
) -> dict:
    """
    Transition a proposal to a new status.

    Validates the transition is allowed.  Writes audit entry.
    Returns updated proposal dict or raises ValueError on invalid transition.
    """
    _ensure_tables()
    from database import get_connection
    conn = get_connection()
    try:
        proposal = _fetch_proposal(conn, proposal_id)
        if proposal is None:
            raise ValueError(f"Proposal {proposal_id!r} not found")

        current = proposal["status"]
        allowed = _VALID_TRANSITIONS.get(current, set())

        if to_status not in allowed:
            raise ValueError(
                f"Invalid transition {current!r} → {to_status!r}; "
                f"allowed from {current!r}: {sorted(allowed) or 'none (terminal)'}"
            )

        now = _now_iso()
        conn.execute(
            """
            UPDATE learning_proposals
               SET status = ?, reviewed_at = ?, reviewed_by = ?, review_note = ?
             WHERE proposal_id = ?
            """,
            (to_status, now, actor, reason, proposal_id),
        )
        _write_audit(conn, proposal_id, current, to_status, reason, actor)
        conn.commit()

        proposal["status"]      = to_status
        proposal["reviewed_at"] = now
        proposal["reviewed_by"] = actor
        proposal["review_note"] = reason
        return proposal
    finally:
        conn.close()


# ── Proposal generation ───────────────────────────────────────────────────────

def _build_weight_proposal(weight_recs: list, n_outcomes: int) -> Optional[dict]:
    """Build a WEIGHT proposal dict from qualifying weight recommendations."""
    actionable = [
        r for r in weight_recs
        if r["action"] in ("INCREASE", "DECREASE")
        and r["confidence"] in _MIN_CONFIDENCE_FOR_PROPOSAL
    ]
    if not actionable:
        return None

    weight_changes: dict = {}
    risk_parts: list     = []
    evidence_parts: list = []

    for r in actionable:
        comp = r["component"]
        weight_changes[comp] = {
            "current_weight":  r["current_weight"],
            "shrunk_delta":    r["shrunk_delta"],
            "action":          r["action"],
            "confidence":      r["confidence"],
            "reason":          r["reason"],
            "sample_size":     r["sample_size"],
        }
        lift_str = (
            f"lift in reason: see reason"
            if "lift" not in r.get("reason", "").lower()
            else r["reason"].split("—")[0].strip()
        )
        evidence_parts.append(
            f"{comp}: {r['action']} (confidence={r['confidence']}, n={r['sample_size']})"
        )
        if r.get("risk"):
            risk_parts.append(r["risk"])

    overall_confidence = (
        "HIGH" if all(r["confidence"] == "HIGH" for r in actionable)
        else "MEDIUM"
    )
    proposal_id = _proposal_id_hash(weight_changes, {}, n_outcomes)

    # Compute shadow weights for replay
    from alpha_learning_engine import _compute_shadow_weights
    shadow_w = _compute_shadow_weights(actionable)

    # Append validation metrics when available
    try:
        from alpha_validation import get_validation_metrics_for_proposals
        vm = get_validation_metrics_for_proposals()
        if vm and vm.get("validation_count", 0) > 0:
            evidence_parts.append(
                f"validation: {vm['validation_count']} validated outcomes, "
                f"trap_rate={vm.get('trap_rate', 'N/A')}, "
                f"sustainability={vm.get('sustainability_rate', 'N/A')}, "
                f"avg_score={vm.get('avg_validation_score', 'N/A')}"
            )
    except Exception:
        pass

    return {
        "proposal_id":            proposal_id,
        "kind":                   "WEIGHT",
        "weight_changes_json":    json.dumps(weight_changes, sort_keys=True),
        "threshold_changes_json": None,
        "shadow_weights_json":    json.dumps(shadow_w, sort_keys=True),
        "evidence_summary":       (
            f"Based on {n_outcomes} COMPLETE outcomes: "
            + "; ".join(evidence_parts)
        ),
        "sample_size":            n_outcomes,
        "confidence":             overall_confidence,
        "risk_warning":           "; ".join(dict.fromkeys(risk_parts)) or "Standard estimation risk",
        "expected_benefit":       (
            f"{len(actionable)} component weight adjustments expected to improve "
            "signal-to-noise ratio based on outcome lift analysis"
        ),
        "expected_downside":      (
            "Risk of over-fitting to limited outcome history; monitor for 20+ COMPLETE "
            "outcomes before acting on HIGH-confidence recommendations"
        ),
    }


def _build_threshold_proposal(threshold_recs: list, n_outcomes: int) -> Optional[dict]:
    """Build a THRESHOLD proposal dict from qualifying threshold recommendations."""
    actionable = [
        r for r in threshold_recs
        if r["action"] in ("TIGHTEN", "LOOSEN")
        and r["confidence"] in _MIN_CONFIDENCE_FOR_PROPOSAL
    ]
    if not actionable:
        return None

    threshold_changes: dict = {}
    risk_parts: list        = []
    evidence_parts: list    = []

    for r in actionable:
        tier = r["tier"]
        threshold_changes[tier] = {
            "current_threshold": r["current_threshold"],
            "suggested_delta":   r["suggested_delta"],
            "action":            r["action"],
            "confidence":        r["confidence"],
            "reason":            r["reason"],
        }
        evidence_parts.append(
            f"{tier}: {r['action']} by {r['suggested_delta']:+.1f} (confidence={r['confidence']})"
        )
        if r.get("risk"):
            risk_parts.append(r["risk"])

    overall_confidence = (
        "HIGH" if all(r["confidence"] == "HIGH" for r in actionable)
        else "MEDIUM"
    )
    proposal_id = _proposal_id_hash({}, threshold_changes, n_outcomes)

    from alpha_learning_engine import _CURRENT_WEIGHTS
    shadow_w = dict(_CURRENT_WEIGHTS)

    # Append validation metrics when available
    try:
        from alpha_validation import get_validation_metrics_for_proposals
        vm = get_validation_metrics_for_proposals()
        if vm and vm.get("validation_count", 0) > 0:
            evidence_parts.append(
                f"validation: {vm['validation_count']} validated outcomes, "
                f"trap_rate={vm.get('trap_rate', 'N/A')}, "
                f"sustainability={vm.get('sustainability_rate', 'N/A')}"
            )
    except Exception:
        pass

    return {
        "proposal_id":            proposal_id,
        "kind":                   "THRESHOLD",
        "weight_changes_json":    None,
        "threshold_changes_json": json.dumps(threshold_changes, sort_keys=True),
        "shadow_weights_json":    json.dumps(shadow_w, sort_keys=True),
        "evidence_summary":       (
            f"Based on {n_outcomes} COMPLETE outcomes: "
            + "; ".join(evidence_parts)
        ),
        "sample_size":            n_outcomes,
        "confidence":             overall_confidence,
        "risk_warning":           "; ".join(dict.fromkeys(risk_parts)) or "Standard estimation risk",
        "expected_benefit":       (
            f"{len(actionable)} tier threshold adjustment(s) expected to improve "
            "precision / recall balance based on false-positive rate analysis"
        ),
        "expected_downside":      (
            "Threshold changes affect tier boundaries; tightening may exclude borderline winners"
        ),
    }


def generate_proposals() -> list:
    """
    Generate promotion proposals from current L2 recommendations.

    Idempotent: re-running with the same recommendations produces no new rows
    (INSERT OR IGNORE on unique proposal_id).

    Returns list of proposal dicts (newly inserted + any existing with same ID).
    Never raises; errors are logged.
    """
    _ensure_tables()

    try:
        from alpha_learning_engine import generate_recommendations_report
        report = generate_recommendations_report()
    except Exception as exc:
        log.warning("alpha_proposals: generate_proposals failed — could not build report: %s", exc)
        return []

    n = report.get("total_complete_outcomes", 0)
    if n < _MIN_OUTCOMES_FOR_PROPOSAL:
        log.info(
            "alpha_proposals: only %d COMPLETE outcomes — need %d to generate proposals",
            n, _MIN_OUTCOMES_FOR_PROPOSAL,
        )
        return []

    proposals_to_insert: list = []

    w_proposal = _build_weight_proposal(report.get("weight_recommendations", []), n)
    if w_proposal:
        proposals_to_insert.append(w_proposal)

    t_proposal = _build_threshold_proposal(report.get("threshold_recommendations", []), n)
    if t_proposal:
        proposals_to_insert.append(t_proposal)

    if not proposals_to_insert:
        log.info("alpha_proposals: no actionable recommendations to propose")
        return []

    now        = _now_iso()
    expires_at = (datetime.now() + timedelta(days=_PROPOSAL_EXPIRE_DAYS)).isoformat()
    inserted_ids: list = []

    from database import get_connection
    conn = get_connection()
    try:
        for p in proposals_to_insert:
            result = conn.execute(
                """
                INSERT OR IGNORE INTO learning_proposals
                    (proposal_id, status, kind,
                     weight_changes_json, threshold_changes_json, shadow_weights_json,
                     evidence_summary, sample_size, confidence,
                     risk_warning, expected_benefit, expected_downside,
                     created_at, expires_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    p["proposal_id"], "PROPOSED", p["kind"],
                    p.get("weight_changes_json"), p.get("threshold_changes_json"),
                    p.get("shadow_weights_json"),
                    p["evidence_summary"], p["sample_size"], p["confidence"],
                    p["risk_warning"], p["expected_benefit"], p["expected_downside"],
                    now, expires_at,
                ),
            )
            if result.rowcount > 0:
                _write_audit(conn, p["proposal_id"], None, "PROPOSED", "auto-generated", "system")
                inserted_ids.append(p["proposal_id"])
                log.info("alpha_proposals: inserted proposal %s (%s)", p["proposal_id"], p["kind"])
            else:
                log.info("alpha_proposals: proposal %s already exists", p["proposal_id"])
                inserted_ids.append(p["proposal_id"])

        conn.commit()
        # Return all proposals that match the IDs we processed
        placeholders = ",".join("?" * len(inserted_ids))
        rows = conn.execute(
            f"SELECT * FROM learning_proposals WHERE proposal_id IN ({placeholders})",
            inserted_ids,
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("alpha_proposals: DB error during generate_proposals", exc_info=True)
        return []
    finally:
        conn.close()


# ── Review workflow ────────────────────────────────────────────────────────────

def approve_for_shadow(
    proposal_id: str,
    actor: Optional[str] = "api",
    note: Optional[str] = None,
) -> dict:
    """Transition PROPOSED → APPROVED_FOR_SHADOW. Raises ValueError on bad transition."""
    return _transition_status(proposal_id, "APPROVED_FOR_SHADOW", note, actor)


def reject_proposal(
    proposal_id: str,
    reason: Optional[str] = None,
    actor: Optional[str] = "api",
) -> dict:
    """Transition PROPOSED → REJECTED. Raises ValueError on bad transition."""
    return _transition_status(proposal_id, "REJECTED", reason, actor)


def mark_rollback_ready(
    proposal_id: str,
    reason: Optional[str] = None,
    actor: Optional[str] = "system",
) -> dict:
    """Transition APPROVED_FOR_SHADOW → ROLLBACK_READY. Raises ValueError on bad transition."""
    return _transition_status(proposal_id, "ROLLBACK_READY", reason, actor)


def expire_stale_proposals() -> int:
    """
    Mark PROPOSED and APPROVED_FOR_SHADOW proposals as EXPIRED when past their expires_at.

    Returns count of proposals expired.
    """
    _ensure_tables()
    from database import get_connection
    conn = get_connection()
    expired_count = 0
    now = _now_iso()
    try:
        rows = conn.execute(
            """
            SELECT proposal_id, status FROM learning_proposals
             WHERE status IN ('PROPOSED', 'APPROVED_FOR_SHADOW')
               AND expires_at < ?
            """,
            (now,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE learning_proposals SET status = 'EXPIRED', reviewed_at = ? WHERE proposal_id = ?",
                (now, row["proposal_id"]),
            )
            _write_audit(conn, row["proposal_id"], row["status"], "EXPIRED",
                         "auto-expired past expires_at", "system")
            expired_count += 1
        conn.commit()
        if expired_count:
            log.info("alpha_proposals: expired %d stale proposals", expired_count)
    except Exception:
        log.warning("alpha_proposals: expire_stale_proposals failed", exc_info=True)
    finally:
        conn.close()
    return expired_count


# ── Read operations ───────────────────────────────────────────────────────────

def get_proposals(
    status_filter: Optional[str] = None,
    include_historical: bool = False,
) -> list:
    """
    Return proposals ordered by created_at DESC.
    Default: active only (PROPOSED + APPROVED_FOR_SHADOW).
    include_historical=True: all statuses.
    status_filter: one specific status.
    """
    _ensure_tables()
    expire_stale_proposals()  # auto-expire on read
    from database import get_connection
    conn = get_connection()
    try:
        return _load_proposals(conn, status_filter=status_filter, include_historical=include_historical)
    except Exception:
        log.warning("alpha_proposals: get_proposals failed", exc_info=True)
        return []
    finally:
        conn.close()


def get_shadow_results(proposal_id: str) -> dict:
    """
    On-demand shadow replay for a specific proposal using its stored shadow weights.

    Loads COMPLETE outcomes and replays against proposal's shadow weights.
    Returns replay stats regardless of proposal status.
    """
    _ensure_tables()
    from database import get_connection
    conn = get_connection()
    try:
        proposal = _fetch_proposal(conn, proposal_id)
    finally:
        conn.close()

    if proposal is None:
        return {"error": f"Proposal {proposal_id!r} not found", "proposal_id": proposal_id}

    # Resolve shadow weights
    shadow_weights: Optional[dict] = None
    raw = proposal.get("shadow_weights_json")
    if raw:
        try:
            shadow_weights = json.loads(raw)
        except Exception:
            pass

    if not shadow_weights:
        from alpha_learning_engine import _CURRENT_WEIGHTS
        shadow_weights = dict(_CURRENT_WEIGHTS)

    # Load COMPLETE outcomes and replay
    try:
        from alpha_learning_engine import _load_complete_outcomes, generate_shadow_policy_replay
        outcomes = _load_complete_outcomes()
        replay   = generate_shadow_policy_replay(shadow_weights, outcomes)
    except Exception as exc:
        log.warning("alpha_proposals: get_shadow_results replay failed: %s", exc)
        replay = {"error": str(exc)}

    return {
        "proposal_id":   proposal_id,
        "status":        proposal["status"],
        "kind":          proposal["kind"],
        "confidence":    proposal["confidence"],
        "sample_size":   proposal["sample_size"],
        "shadow_weights": shadow_weights,
        "replay_stats":  replay,
    }


def get_audit_trail(proposal_id: str) -> list:
    """Return all audit entries for a proposal in chronological order."""
    _ensure_tables()
    from database import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM learning_proposal_audit WHERE proposal_id = ? ORDER BY ts ASC",
            (proposal_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("alpha_proposals: get_audit_trail failed", exc_info=True)
        return []
    finally:
        conn.close()
