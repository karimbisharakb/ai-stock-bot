"""
Controlled adaptive rollout for the Predator scanner.
Phase 5A — tightly bounded live adaptation with safeguard protections.

Provides a gated, reversible, bounded mechanism for moving adaptive weight
recommendations into the live engine.  Every adaptation step requires
multi-condition clearance, is capped per-signal and portfolio-wide, and can
be rolled back automatically when trigger conditions are met.

No function in this module writes to the database, calls live engine code,
or applies weights without explicit external invocation.  All functions are
pure: they accept pre-built dicts and return new dicts without mutation.

Rollout policies
----------------
  DISABLED            no adaptive influence whatsoever (default safe state)
  OBSERVATION_ONLY    shadow runs; nothing touches live weights
  LIMITED_TRIAL       bounded live adaptation (LIMITED_TRIAL_READY readiness)
  CONTROLLED_ACTIVE   same bounds, extended monitoring (STABLE readiness)

Adaptation gating (ALL must pass for allowed=True)
---------------------------------------------------
  1. Shadow readiness ≥ LIMITED_TRIAL_READY
  2. No CRITICAL operational status
  3. Weight recommendations not UNSTABLE
  4. Minimum historical sample met
  5. Cooldown period elapsed since last adaptation
  6. Rollback lockout period elapsed since last rollback

Automatic rollback triggers
----------------------------
  CALIBRATION_WORSENING   Brier score increases > threshold
  WIN_RATE_REGRESSION     live win-rate drops > threshold pp
  DRAWDOWN_SPIKE          drawdown increases > threshold pp
  UNSTABLE_CHURN          alert churn rate > threshold
  OPERATIONAL_CRITICAL    hub overall_health == "CRITICAL"
  WEIGHT_INSTABILITY      stability_analysis overall == UNSTABLE

Adaptation bounds
-----------------
  LIVE_MAX_ADJUSTMENT_PER_SIGNAL  0.10 — hard cap on |Δ| per signal
  TOTAL_PORTFOLIO_ADJUSTMENT_CAP  0.30 — cap on sum(|Δ|) across all signals
  MIN_LIVE_WEIGHT                 0.50 — absolute weight floor
"""
import logging
from typing import Optional

from adaptive_weights import DEFAULT_WEIGHTS, MIN_WEIGHT as _MIN_WEIGHT_UPSTREAM
from shadow_adaptive import (
    READINESS_NOT_READY, READINESS_OBSERVE, READINESS_LIMITED, READINESS_STABLE,
    READINESS_ORDER,
    STABILITY_STABLE, STABILITY_WATCH, STABILITY_UNSTABLE,
    SIGNAL_NAMES,
)

log = logging.getLogger(__name__)

# ── Rollout policies ──────────────────────────────────────────────────────────

POLICY_DISABLED          = "DISABLED"
POLICY_OBSERVATION_ONLY  = "OBSERVATION_ONLY"
POLICY_LIMITED_TRIAL     = "LIMITED_TRIAL"
POLICY_CONTROLLED_ACTIVE = "CONTROLLED_ACTIVE"

POLICY_ORDER: dict = {
    POLICY_DISABLED:          0,
    POLICY_OBSERVATION_ONLY:  1,
    POLICY_LIMITED_TRIAL:     2,
    POLICY_CONTROLLED_ACTIVE: 3,
}

# ── Adaptation bounds ─────────────────────────────────────────────────────────

LIVE_MAX_ADJUSTMENT_PER_SIGNAL: float = 0.10  # |new − default| hard cap
TOTAL_PORTFOLIO_ADJUSTMENT_CAP: float = 0.30  # sum(|Δ|) across all signals
MIN_LIVE_WEIGHT:                float = _MIN_WEIGHT_UPSTREAM  # = 0.50

# ── Gating constants ──────────────────────────────────────────────────────────

MIN_ROWS_FOR_ADAPTATION: int   = 30
REQUIRED_READINESS_LEVEL: int  = READINESS_ORDER[READINESS_LIMITED]  # = 2

# ── Scheduler constants ───────────────────────────────────────────────────────

DEFAULT_COOLDOWN_ROWS:         int = 20   # min rows between adaptations
DEFAULT_MIN_OBSERVATION_ROWS:  int = 30   # min rows before any first adaptation
DEFAULT_ROLLBACK_LOCKOUT_ROWS: int = 40   # rows locked out after a rollback

# ── Rollback trigger thresholds ───────────────────────────────────────────────

ROLLBACK_CALIBRATION_WORSENING: float = 0.02  # Brier increase → rollback
ROLLBACK_WIN_RATE_REGRESSION:   float = 5.0   # pp win-rate drop → rollback
ROLLBACK_DRAWDOWN_SPIKE:        float = 5.0   # % additional drawdown → rollback
ROLLBACK_CHURN_THRESHOLD:       float = 0.30  # churn rate → rollback
ROLLBACK_DIVERGENCE_THRESHOLD:  float = 20.0  # pp confidence divergence → rollback

# ── Output bounds ─────────────────────────────────────────────────────────────

MAX_HISTORY_ENTRIES: int = 20
MAX_RECOMMENDATIONS: int = 10
MAX_TRIGGER_ENTRIES: int = 10

# ── Confidence score change threshold ─────────────────────────────────────────

CONFIDENCE_CHANGE_MIN: int = 10   # point shift before recording as a change


# ── Bounded weight application ────────────────────────────────────────────────

def apply_controlled_weights(
    proposed_weights: Optional[dict] = None,
    current_weights:  Optional[dict] = None,
    allowlist:        Optional[list]  = None,
) -> dict:
    """
    Apply proposed adaptive weights with hard per-signal and portfolio bounds.

    For each signal:
      1. If not in allowlist (when allowlist is set): hold at DEFAULT_WEIGHTS value.
      2. proposed_delta = proposed_weight − default_weight
      3. clamped_delta  = clamp(proposed_delta, ±LIVE_MAX_ADJUSTMENT_PER_SIGNAL)
      4. applied_weight = clamp(default + clamped_delta,
                                MIN_LIVE_WEIGHT,
                                default + LIVE_MAX_ADJUSTMENT_PER_SIGNAL)
      5. If sum(|Δ from default|) > TOTAL_PORTFOLIO_ADJUSTMENT_CAP:
         scale all deltas proportionally so the total hits the cap exactly.

    Parameters
    ----------
    proposed_weights  {signal: proposed weight} — from adaptive_weights.compute_weight_adjustments
    current_weights   unused for bounds computation but accepted for caller convenience
    allowlist         if set, only these signals may be adapted; others hold default

    Returns
    -------
    {
        applied_weights:       {signal: float},
        per_signal_delta:      {signal: float},      — Δ from DEFAULT_WEIGHTS
        clamped_signals:       list,                  — signals per-signal capped
        total_portfolio_delta: float,                 — sum(|Δ|)
        clamped_by_portfolio:  bool,
        allowlist_blocked:     list,                  — signals blocked by allowlist
    }
    """
    _proposed = proposed_weights or {}
    intermediate: dict = {}
    allowlist_blocked: list = []

    for sig in SIGNAL_NAMES:
        default = DEFAULT_WEIGHTS.get(sig, 1.0)
        max_w   = default + LIVE_MAX_ADJUSTMENT_PER_SIGNAL

        # Allowlist gate
        if allowlist is not None and sig not in allowlist:
            allowlist_blocked.append(sig)
            intermediate[sig] = {
                "delta":   0.0,
                "applied": default,
                "clamped": False,
            }
            continue

        proposed = float(_proposed.get(sig) or default)
        prop_delta    = proposed - default
        clamped_delta = max(-LIVE_MAX_ADJUSTMENT_PER_SIGNAL,
                            min(LIVE_MAX_ADJUSTMENT_PER_SIGNAL, prop_delta))
        per_sig_clamped = abs(clamped_delta - prop_delta) > 1e-9
        applied = max(MIN_LIVE_WEIGHT, min(max_w, default + clamped_delta))
        actual_delta = round(applied - default, 6)

        intermediate[sig] = {
            "delta":   actual_delta,
            "applied": applied,
            "clamped": per_sig_clamped,
        }

    # ── Portfolio cap ─────────────────────────────────────────────────────────
    total_delta = sum(abs(v["delta"]) for v in intermediate.values())
    clamped_by_portfolio = total_delta > TOTAL_PORTFOLIO_ADJUSTMENT_CAP + 1e-9

    if clamped_by_portfolio:
        scale = TOTAL_PORTFOLIO_ADJUSTMENT_CAP / total_delta
        for sig in SIGNAL_NAMES:
            v       = intermediate[sig]
            default = DEFAULT_WEIGHTS.get(sig, 1.0)
            max_w   = default + LIVE_MAX_ADJUSTMENT_PER_SIGNAL
            scaled_delta = v["delta"] * scale
            applied      = max(MIN_LIVE_WEIGHT, min(max_w, default + scaled_delta))
            intermediate[sig] = {
                "delta":   round(applied - default, 6),
                "applied": round(applied, 6),
                "clamped": v["clamped"],
            }
        total_delta = sum(abs(v["delta"]) for v in intermediate.values())

    return {
        "applied_weights":      {sig: round(intermediate[sig]["applied"], 6)
                                 for sig in SIGNAL_NAMES},
        "per_signal_delta":     {sig: intermediate[sig]["delta"]
                                 for sig in SIGNAL_NAMES},
        "clamped_signals":      [sig for sig in SIGNAL_NAMES
                                 if intermediate[sig]["clamped"]],
        "total_portfolio_delta": round(total_delta, 6),
        "clamped_by_portfolio":  clamped_by_portfolio,
        "allowlist_blocked":     allowlist_blocked,
    }


# ── Adaptation gating ─────────────────────────────────────────────────────────

def check_adaptation_gate(
    shadow_report:         Optional[dict] = None,
    hub_report:            Optional[dict] = None,
    rows_since_last:       Optional[int]  = None,
    rows_since_rollback:   Optional[int]  = None,
    cooldown_rows:         int            = DEFAULT_COOLDOWN_ROWS,
    min_observation_rows:  int            = DEFAULT_MIN_OBSERVATION_ROWS,
    rollback_lockout_rows: int            = DEFAULT_ROLLBACK_LOCKOUT_ROWS,
) -> dict:
    """
    Check all gating conditions required for live adaptation.

    Returns {allowed, policy, blockers, reasons}.
    ``allowed`` is True only when no blockers are present.
    """
    _shadow = shadow_report or {}
    _hub    = hub_report    or {}
    blockers: list = []
    reasons:  list = []

    # 1. Shadow readiness
    readiness_status = (_shadow.get("readiness") or {}).get("status") or READINESS_NOT_READY
    readiness_level  = READINESS_ORDER.get(readiness_status, 0)
    if readiness_level < REQUIRED_READINESS_LEVEL:
        blockers.append(
            f"Shadow readiness {readiness_status} below minimum "
            f"(need ≥ {READINESS_LIMITED})"
        )
    else:
        reasons.append(f"Shadow readiness {readiness_status} meets gate requirement")

    # 2. Operational status
    hub_health = _hub.get("overall_health") or "HEALTHY"
    if hub_health == "CRITICAL":
        blockers.append("Operational hub status is CRITICAL — adaptation blocked")
    elif hub_health == "DEGRADED":
        reasons.append("Hub status DEGRADED — proceed with reduced aggressiveness")

    # 3. Weight stability
    stability_overall = (_shadow.get("stability") or {}).get("overall") or STABILITY_STABLE
    if stability_overall == STABILITY_UNSTABLE:
        blockers.append(
            "Weight recommendations are UNSTABLE — oscillating signal(s) detected"
        )
    elif stability_overall == STABILITY_WATCH:
        reasons.append("Weight stability WATCH — reduced aggressiveness recommended")

    # 4. Minimum sample
    n_rows = _shadow.get("n_rows") or 0
    if n_rows < MIN_ROWS_FOR_ADAPTATION:
        blockers.append(
            f"Insufficient history: {n_rows} rows "
            f"(need ≥ {MIN_ROWS_FOR_ADAPTATION})"
        )
    else:
        reasons.append(f"Sample size {n_rows} meets minimum ({MIN_ROWS_FOR_ADAPTATION})")

    # 5. Cooldown window
    if rows_since_last is not None and rows_since_last < cooldown_rows:
        blockers.append(
            f"In cooldown: {rows_since_last} rows since last adaptation "
            f"(need ≥ {cooldown_rows})"
        )
    elif rows_since_last is not None:
        reasons.append(
            f"Cooldown elapsed: {rows_since_last} rows since last adaptation"
        )

    # 6. Rollback lockout
    if rows_since_rollback is not None and rows_since_rollback < rollback_lockout_rows:
        blockers.append(
            f"Rollback lockout: {rows_since_rollback} rows since last rollback "
            f"(need ≥ {rollback_lockout_rows})"
        )
    elif rows_since_rollback is not None:
        reasons.append(
            f"Rollback lockout elapsed: {rows_since_rollback} rows since rollback"
        )

    allowed = len(blockers) == 0

    # Determine policy
    if not allowed:
        policy = (POLICY_OBSERVATION_ONLY
                  if readiness_level >= 1 else POLICY_DISABLED)
    elif readiness_level >= READINESS_ORDER[READINESS_STABLE]:
        policy = POLICY_CONTROLLED_ACTIVE
    else:
        policy = POLICY_LIMITED_TRIAL

    if not allowed:
        log.info(
            "controlled_adaptation: gate BLOCKED — %d blocker(s): %s",
            len(blockers), [b[:60] for b in blockers[:3]],
        )
    else:
        log.info(
            "controlled_adaptation: gate OPEN — policy=%s readiness=%s",
            policy, readiness_status,
        )

    return {
        "allowed":  allowed,
        "policy":   policy,
        "blockers": blockers,
        "reasons":  reasons,
    }


# ── Rollback trigger detection ────────────────────────────────────────────────

def check_rollback_triggers(
    comparison:       Optional[dict] = None,
    stability:        Optional[dict] = None,
    hub_report:       Optional[dict] = None,
    prior_comparison: Optional[dict] = None,
) -> dict:
    """
    Check all automatic rollback conditions.

    ``comparison``  — from shadow_adaptive.compare_live_vs_shadow (current)
    ``prior_comparison`` — previous comparison snapshot (for worsening detection)

    Returns {should_rollback, triggers, severity}.
    ``triggers`` list is capped at MAX_TRIGGER_ENTRIES.
    """
    comp  = comparison       or {}
    prior = prior_comparison or {}
    stab  = stability        or {}
    hub   = hub_report       or {}
    triggers: list = []

    # 1. Calibration worsening (Brier increases)
    curr_brier = comp.get("live_brier")
    prev_brier = prior.get("live_brier")
    if curr_brier is not None and prev_brier is not None:
        brier_delta = curr_brier - prev_brier
        if brier_delta > ROLLBACK_CALIBRATION_WORSENING:
            triggers.append({
                "type":      "CALIBRATION_WORSENING",
                "value":     round(brier_delta, 6),
                "threshold": ROLLBACK_CALIBRATION_WORSENING,
                "severity":  "HIGH",
            })

    # 2. Win-rate regression
    curr_wr = comp.get("live_win_rate")
    prev_wr = prior.get("live_win_rate")
    if curr_wr is not None and prev_wr is not None:
        wr_drop = prev_wr - curr_wr  # positive → dropped
        if wr_drop > ROLLBACK_WIN_RATE_REGRESSION:
            triggers.append({
                "type":      "WIN_RATE_REGRESSION",
                "value":     round(wr_drop, 2),
                "threshold": ROLLBACK_WIN_RATE_REGRESSION,
                "severity":  "HIGH",
            })

    # 3. Drawdown spike
    curr_dd = comp.get("live_drawdown")
    prev_dd = prior.get("live_drawdown")
    if curr_dd is not None and prev_dd is not None:
        dd_increase = curr_dd - prev_dd  # positive → drawdown worsened
        if dd_increase > ROLLBACK_DRAWDOWN_SPIKE:
            triggers.append({
                "type":      "DRAWDOWN_SPIKE",
                "value":     round(dd_increase, 2),
                "threshold": ROLLBACK_DRAWDOWN_SPIKE,
                "severity":  "HIGH",
            })

    # 4. Unstable churn
    churn = comp.get("churn_rate") or 0.0
    if churn > ROLLBACK_CHURN_THRESHOLD:
        triggers.append({
            "type":      "UNSTABLE_CHURN",
            "value":     round(churn, 4),
            "threshold": ROLLBACK_CHURN_THRESHOLD,
            "severity":  "MEDIUM",
        })

    # 5. Operational CRITICAL
    hub_health = hub.get("overall_health") or "HEALTHY"
    if hub_health == "CRITICAL":
        triggers.append({
            "type":      "OPERATIONAL_CRITICAL",
            "value":     hub_health,
            "threshold": "CRITICAL",
            "severity":  "CRITICAL",
        })

    # 6. Weight instability
    if stab.get("overall") == STABILITY_UNSTABLE:
        triggers.append({
            "type":      "WEIGHT_INSTABILITY",
            "value":     STABILITY_UNSTABLE,
            "threshold": STABILITY_STABLE,
            "severity":  "HIGH",
        })

    should_rollback = len(triggers) > 0

    # Severity: CRITICAL > HIGH > MEDIUM > NONE
    severity = "NONE"
    if triggers:
        sevs = [t["severity"] for t in triggers]
        if "CRITICAL" in sevs:
            severity = "CRITICAL"
        elif "HIGH" in sevs:
            severity = "HIGH"
        else:
            severity = "MEDIUM"

    if should_rollback:
        log.warning(
            "controlled_adaptation: rollback triggered — %d trigger(s) severity=%s: %s",
            len(triggers), severity,
            [t["type"] for t in triggers],
        )

    return {
        "should_rollback": should_rollback,
        "triggers":        triggers[:MAX_TRIGGER_ENTRIES],
        "severity":        severity,
    }


# ── Adaptation step computation ───────────────────────────────────────────────

def compute_adaptation_step(
    weight_adjustments: Optional[dict] = None,
    shadow_report:      Optional[dict] = None,
    current_weights:    Optional[dict] = None,
    allowlist:          Optional[list]  = None,
) -> dict:
    """
    Compute the next bounded adaptation step without applying it.

    ``weight_adjustments`` — output of adaptive_weights.compute_weight_adjustments()
    shape: {signal: {suggested_weight, adjustment, default_weight, ...}}

    Returns
    -------
    {
        proposed_weights,        — raw suggested weights (pre-clamp)
        clamped_weights,         — weights after all bounds applied
        per_signal_delta,        — Δ from DEFAULT_WEIGHTS for each signal
        clamped_signals,         — signals whose per-signal delta was clamped
        total_portfolio_delta,   — sum(|Δ|)
        clamped_by_portfolio,    — bool
        allowlist_blocked,       — signals blocked by allowlist
        ready_to_apply,          — bool: all gate conditions implied by shadow readiness
    }
    """
    _wa     = weight_adjustments or {}
    _shadow = shadow_report      or {}

    # Extract proposed weights from weight_adjustments
    proposed: dict = {
        sig: float((_wa.get(sig) or {}).get("suggested_weight")
                   or DEFAULT_WEIGHTS.get(sig, 1.0))
        for sig in SIGNAL_NAMES
    }

    bounded = apply_controlled_weights(proposed, current_weights, allowlist)

    readiness_status = (_shadow.get("readiness") or {}).get("status") or READINESS_NOT_READY
    ready_to_apply   = READINESS_ORDER.get(readiness_status, 0) >= REQUIRED_READINESS_LEVEL

    return {
        "proposed_weights":      proposed,
        "clamped_weights":       bounded["applied_weights"],
        "per_signal_delta":      bounded["per_signal_delta"],
        "clamped_signals":       bounded["clamped_signals"],
        "total_portfolio_delta": bounded["total_portfolio_delta"],
        "clamped_by_portfolio":  bounded["clamped_by_portfolio"],
        "allowlist_blocked":     bounded["allowlist_blocked"],
        "ready_to_apply":        ready_to_apply,
    }


# ── Adaptation history entry ──────────────────────────────────────────────────

def record_adaptation_entry(
    prior_weights:   dict,
    applied_weights: dict,
    policy:          str,
    reason:          str,
    timestamp:       str            = "",
    rollback:        bool           = False,
    rollback_cause:  Optional[str] = None,
) -> dict:
    """
    Create an adaptation history entry dict (pure — no I/O).

    Callers are responsible for appending this to persistent storage.
    ``timestamp`` should be an ISO datetime string; pass "" for deterministic tests.
    """
    all_sigs = set(list(prior_weights.keys()) + list(applied_weights.keys()))
    delta_weights = {
        sig: round(
            float(applied_weights.get(sig) or DEFAULT_WEIGHTS.get(sig, 1.0))
            - float(prior_weights.get(sig)  or DEFAULT_WEIGHTS.get(sig, 1.0)),
            6,
        )
        for sig in all_sigs
    }

    return {
        "timestamp":       timestamp,
        "policy":          policy,
        "prior_weights":   prior_weights,
        "applied_weights": applied_weights,
        "delta_weights":   delta_weights,
        "reason":          reason,
        "rollback":        rollback,
        "rollback_cause":  rollback_cause,
    }


# ── Adaptation impact analysis ────────────────────────────────────────────────

def analyze_adaptation_impact(
    history:    Optional[list] = None,
    comparison: Optional[dict] = None,
) -> dict:
    """
    Measure cumulative effects across adaptation history entries.

    ``history``    — list of record_adaptation_entry() dicts
    ``comparison`` — from shadow_adaptive.compare_live_vs_shadow

    Returns
    -------
    {
        n_adaptations, n_rollbacks, n_history_entries,
        total_weight_drift, avg_delta_magnitude,
        signals_most_adapted,   — top-3 by cumulative |Δ|
        sig_drift,              — {signal: cumulative |Δ|}
        performance_impact,     — win_rate_delta from comparison
        calibration_delta,      — calibration_delta from comparison
        churn_rate,             — churn_rate from comparison
    }
    """
    hist = history    or []
    comp = comparison or {}

    n_total      = len(hist)
    n_rollbacks  = sum(1 for e in hist if e.get("rollback"))
    n_adaptations = n_total - n_rollbacks

    sig_drift: dict = {sig: 0.0 for sig in SIGNAL_NAMES}
    total_drift = 0.0

    for entry in hist:
        for sig, delta in (entry.get("delta_weights") or {}).items():
            abs_d = abs(float(delta))
            total_drift += abs_d
            if sig in sig_drift:
                sig_drift[sig] = round(sig_drift[sig] + abs_d, 6)

    signals_most_adapted = sorted(
        sig_drift.items(), key=lambda x: -x[1]
    )[:3]

    avg_delta = round(total_drift / max(1, n_total), 6)

    return {
        "n_adaptations":       n_adaptations,
        "n_rollbacks":         n_rollbacks,
        "n_history_entries":   n_total,
        "total_weight_drift":  round(total_drift, 6),
        "avg_delta_magnitude": avg_delta,
        "signals_most_adapted": signals_most_adapted,
        "sig_drift":           sig_drift,
        "performance_impact":  comp.get("win_rate_delta"),
        "calibration_delta":   comp.get("calibration_delta"),
        "churn_rate":          comp.get("churn_rate"),
    }


# ── Rollout confidence evaluation ─────────────────────────────────────────────

def evaluate_rollout_confidence(
    gate:          Optional[dict] = None,
    shadow_report: Optional[dict] = None,
    history:       Optional[list] = None,
) -> dict:
    """
    Compute a 0-100 rollout confidence score and active policy recommendation.

    Score composition:
      +50   gate is open (all conditions met)
      +0/10/20/30  readiness level (NOT_READY / OBSERVE / LIMITED / STABLE)
      +15   STABLE weights, +5 WATCH weights
      −10 × n_recent_rollbacks  (last 5 history entries)

    Returns {rollout_confidence, active_policy, reasons}.
    """
    _gate   = gate          or {}
    _shadow = shadow_report or {}
    _hist   = history       or []

    score:   int  = 0
    reasons: list = []

    # Gate
    gate_allowed = _gate.get("allowed") or False
    if gate_allowed:
        score += 50
        reasons.append("Gate open — all conditions met (+50)")
    else:
        n_blockers = len(_gate.get("blockers") or [])
        reasons.append(f"Gate blocked — {n_blockers} blocker(s)")

    # Shadow readiness
    readiness_status = (_shadow.get("readiness") or {}).get("status") or READINESS_NOT_READY
    readiness_pts    = READINESS_ORDER.get(readiness_status, 0) * 10
    score += readiness_pts
    reasons.append(f"Shadow readiness {readiness_status} (+{readiness_pts})")

    # Weight stability
    stability = (_shadow.get("stability") or {}).get("overall") or STABILITY_STABLE
    if stability == STABILITY_STABLE:
        score += 15
        reasons.append("Weight stability STABLE (+15)")
    elif stability == STABILITY_WATCH:
        score += 5
        reasons.append("Weight stability WATCH (+5)")
    else:
        reasons.append("Weight stability UNSTABLE (+0)")

    # Recent rollbacks (look at last 5 entries)
    recent = _hist[-5:] if _hist else []
    n_recent_rollbacks = sum(1 for e in recent if e.get("rollback"))
    if n_recent_rollbacks > 0:
        penalty = 10 * n_recent_rollbacks
        score  -= penalty
        reasons.append(f"{n_recent_rollbacks} recent rollback(s) (−{penalty})")
    elif _hist:
        score += 5
        reasons.append("No recent rollbacks (+5)")

    score = max(0, min(100, score))

    active_policy = _gate.get("policy") or POLICY_DISABLED

    return {
        "rollout_confidence": score,
        "active_policy":      active_policy,
        "reasons":            reasons,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _current_adjustments(current_weights: Optional[dict]) -> dict:
    """Show per-signal deviation of current_weights from DEFAULT_WEIGHTS."""
    return {
        sig: {
            "default": DEFAULT_WEIGHTS.get(sig, 1.0),
            "current": float((current_weights or {}).get(sig)
                             or DEFAULT_WEIGHTS.get(sig, 1.0)),
            "delta":   round(
                float((current_weights or {}).get(sig)
                      or DEFAULT_WEIGHTS.get(sig, 1.0))
                - DEFAULT_WEIGHTS.get(sig, 1.0),
                6,
            ),
        }
        for sig in SIGNAL_NAMES
    }


def _safeguard_status(
    hub_report:     dict,
    rollback_check: dict,
    gate:           dict,
) -> dict:
    """Aggregate active safeguards from hub, rollback triggers, and gate."""
    hub_health      = (hub_report      or {}).get("overall_health") or "HEALTHY"
    rollback_active = (rollback_check  or {}).get("should_rollback") or False
    gate_blocked    = not ((gate or {}).get("allowed") or False)

    active: list = []
    if rollback_active:
        for t in ((rollback_check or {}).get("triggers") or []):
            active.append(f"ROLLBACK/{t['type']}")
    if gate_blocked:
        for b in ((gate or {}).get("blockers") or [])[:3]:
            active.append(f"GATE/{b[:60]}")
    if hub_health in ("CRITICAL", "DEGRADED"):
        active.append(f"HUB/{hub_health}")

    return {
        "active_safeguards": active,
        "rollback_active":   rollback_active,
        "gate_blocked":      gate_blocked,
        "hub_health":        hub_health,
        "safeguard_count":   len(active),
    }


def _generate_adaptation_recommendations(
    gate:     dict,
    rollback: dict,
    impact:   dict,
    shadow:   dict,
) -> list:
    """Build deduplicated actionable recommendations."""
    recs: list = []

    if (rollback or {}).get("should_rollback"):
        recs.append("ROLLBACK: revert to default weights immediately")
        for t in ((rollback or {}).get("triggers") or [])[:2]:
            recs.append(f"Rollback cause: {t['type']} (value={t['value']})")

    if not (gate or {}).get("allowed"):
        recs.append("Remain in observation-only mode — gate is blocked")
        for b in ((gate or {}).get("blockers") or [])[:2]:
            recs.append(f"Clear blocker: {b}")

    n_rollbacks = (impact or {}).get("n_rollbacks") or 0
    if n_rollbacks > 1:
        recs.append(
            "Reduce adaptive magnitude — multiple rollbacks in history"
        )

    stability = ((shadow or {}).get("stability") or {}).get("overall") or STABILITY_STABLE
    if stability == STABILITY_UNSTABLE:
        recs.append("Freeze unstable signals until oscillation resolves")

    for sig, info in (((shadow or {}).get("stability") or {}).get("per_signal") or {}).items():
        if info.get("label") == STABILITY_UNSTABLE:
            recs.append(f"Freeze signal '{sig}' from adaptive influence")

    n = ((shadow or {}).get("comparison") or (shadow or {})).get("n_rows") or 0
    if 0 < n < 30:
        recs.append(
            f"Increase sample size before adaptation decision "
            f"(current={n}, target=30)"
        )

    policy = (gate or {}).get("policy") or POLICY_DISABLED
    if policy in (POLICY_LIMITED_TRIAL, POLICY_CONTROLLED_ACTIVE):
        recs.append(
            f"Policy {policy} active — expand trial slowly with ≤10% alert "
            "coverage under close monitoring"
        )

    return list(dict.fromkeys(recs))[:MAX_RECOMMENDATIONS]


def _compare_adaptation_vs_previous(
    gate:            dict,
    confidence:      dict,
    previous_report: Optional[dict],
) -> list:
    """Detect significant policy or confidence changes vs a previous adaptation report."""
    if not previous_report:
        return []

    changes: list = []

    # Policy change
    curr_policy = (gate or {}).get("policy") or POLICY_DISABLED
    prev_policy = previous_report.get("rollout_status") or POLICY_DISABLED
    if curr_policy != prev_policy:
        curr_order = POLICY_ORDER.get(curr_policy, 0)
        prev_order = POLICY_ORDER.get(prev_policy, 0)
        direction  = "PROMOTED" if curr_order > prev_order else "DEMOTED"
        changes.append({
            "type":      "POLICY_CHANGE",
            "from":      prev_policy,
            "to":        curr_policy,
            "direction": direction,
        })
        log.info(
            "controlled_adaptation: policy %s %s → %s",
            direction, prev_policy, curr_policy,
        )

    # Confidence change
    curr_conf = (confidence or {}).get("rollout_confidence") or 0
    prev_conf = (previous_report.get("rollout_confidence") or {}).get("rollout_confidence") or 0
    conf_delta = curr_conf - prev_conf
    if abs(conf_delta) >= CONFIDENCE_CHANGE_MIN:
        changes.append({
            "type":      "CONFIDENCE_CHANGE",
            "from":      prev_conf,
            "to":        curr_conf,
            "delta":     conf_delta,
            "direction": "IMPROVED" if conf_delta > 0 else "WORSENED",
        })

    return changes


# ── Full report ───────────────────────────────────────────────────────────────

def generate_adaptation_report(
    shadow_report:       Optional[dict] = None,
    weight_adjustments:  Optional[dict] = None,
    current_weights:     Optional[dict] = None,
    history:             Optional[list] = None,
    hub_report:          Optional[dict] = None,
    comparison:          Optional[dict] = None,
    prior_comparison:    Optional[dict] = None,
    previous_report:     Optional[dict] = None,
    rows_since_last:     Optional[int]  = None,
    rows_since_rollback: Optional[int]  = None,
    allowlist:           Optional[list]  = None,
) -> dict:
    """
    Generate a full controlled adaptation report.

    Orchestrates gating, rollback checks, impact analysis, confidence
    scoring, and recommendation generation into a single structured dict.

    ``comparison`` may be passed explicitly or falls back to
    ``shadow_report["comparison"]`` when available.

    Returns
    -------
    {
        report_type, rollout_status, gate, rollback_check,
        adaptation_step, impact, rollout_confidence,
        history (capped), active_adjustments, safeguard_status,
        recommendations, changes_vs_previous,
    }
    """
    _shadow  = shadow_report   or {}
    _hist    = history         or []
    _hub     = hub_report      or {}
    _comp    = comparison or _shadow.get("comparison") or {}
    _prior   = prior_comparison or {}
    _stab    = _shadow.get("stability") or {}

    gate       = check_adaptation_gate(
        _shadow, _hub, rows_since_last, rows_since_rollback
    )
    rollback   = check_rollback_triggers(_comp, _stab, _hub, _prior)
    impact     = analyze_adaptation_impact(_hist, _comp)
    confidence = evaluate_rollout_confidence(gate, _shadow, _hist)
    recs       = _generate_adaptation_recommendations(gate, rollback, impact, _shadow)
    changes    = _compare_adaptation_vs_previous(gate, confidence, previous_report)

    step = (compute_adaptation_step(weight_adjustments, _shadow, current_weights, allowlist)
            if weight_adjustments else None)

    log.info(
        "controlled_adaptation: report — policy=%s confidence=%d "
        "rollback=%s n_hist=%d",
        gate["policy"], confidence["rollout_confidence"],
        rollback["should_rollback"], len(_hist),
    )

    return {
        "report_type":         "controlled_adaptation_report",
        "rollout_status":      gate["policy"],
        "gate":                gate,
        "rollback_check":      rollback,
        "adaptation_step":     step,
        "impact":              impact,
        "rollout_confidence":  confidence,
        "history":             _hist[-MAX_HISTORY_ENTRIES:],
        "active_adjustments":  _current_adjustments(current_weights),
        "safeguard_status":    _safeguard_status(_hub, rollback, gate),
        "recommendations":     recs,
        "changes_vs_previous": changes,
    }
