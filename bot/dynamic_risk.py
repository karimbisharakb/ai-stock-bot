"""
Dynamic risk management system for the Predator scanner.
Phase 5C — coordination layer; no live execution, no real money.

Aggregates risk signals from all subsystems into a single risk mode that
drives system-wide safeguards and policy adjustments.  All public functions
are pure — they accept state + inputs and return new dicts without mutation.
Replay-safe, deterministic, sparse-data-safe.

Risk modes (escalation ladder)
-------------------------------
  NORMAL    baseline operation — full allocation, adaptation allowed
  DEFENSIVE tighter thresholds, reduced allocation (75 %)
  REDUCED   adaptation frozen, 50 % exposure cap, regime suppression
  CRITICAL  near-minimal exposure (25 %), all blocking safeguards active
  LOCKDOWN  near-zero new exposure (5 %), longest de-escalation cooldown

Escalation rules
----------------
  Immediate: worst trigger wins; can jump multiple levels upward.
  Inputs: drawdown_pct, hub_health, ECE (calibration), churn_rate,
          rolling_vol, stability, risk_off flag, repeated CRITICAL hub.

De-escalation rules
-------------------
  Single-step only (one level down per tick).
  Requires: cooldown elapsed + all triggers for current mode cleared.
  LOCKDOWN has 4× longer cooldown than other modes.
  Repeated CRITICAL hub (≥ 3 consecutive) triggers LOCKDOWN and resets.

Safeguards
----------
  FREEZE_ADAPTATION    block adaptive weight changes
  BLOCK_NEW_ENTRIES    prevent all new positions (CRITICAL / LOCKDOWN)
  REDUCE_EXPOSURE      apply exposure multiplier (all non-NORMAL modes)
  LIQUIDATE_WEAKEST    flag weakest open positions (CRITICAL / LOCKDOWN)
  FORCE_OBSERVATION    demote adaptation to OBSERVATION_ONLY (REDUCED+)
  TIGHTEN_THRESHOLDS   raise score + confidence thresholds (all non-NORMAL)
"""
import logging
from typing import Optional

from operations_hub import HEALTH_HEALTHY, HEALTH_WATCH, HEALTH_DEGRADED, HEALTH_CRITICAL
from shadow_adaptive import STABILITY_STABLE, STABILITY_WATCH, STABILITY_UNSTABLE

log = logging.getLogger(__name__)

# ── Risk modes ────────────────────────────────────────────────────────────────

MODE_NORMAL    = "NORMAL"
MODE_DEFENSIVE = "DEFENSIVE"
MODE_REDUCED   = "REDUCED"
MODE_CRITICAL  = "CRITICAL"
MODE_LOCKDOWN  = "LOCKDOWN"

MODE_ORDER: dict = {
    MODE_NORMAL:    0,
    MODE_DEFENSIVE: 1,
    MODE_REDUCED:   2,
    MODE_CRITICAL:  3,
    MODE_LOCKDOWN:  4,
}

_LEVELS_TO_MODE: dict = {v: k for k, v in MODE_ORDER.items()}

# ── Safeguard identifiers ─────────────────────────────────────────────────────

SAFEGUARD_FREEZE_ADAPTATION  = "FREEZE_ADAPTATION"
SAFEGUARD_BLOCK_NEW_ENTRIES  = "BLOCK_NEW_ENTRIES"
SAFEGUARD_REDUCE_EXPOSURE    = "REDUCE_EXPOSURE"
SAFEGUARD_LIQUIDATE_WEAKEST  = "LIQUIDATE_WEAKEST"
SAFEGUARD_FORCE_OBSERVATION  = "FORCE_OBSERVATION"
SAFEGUARD_TIGHTEN_THRESHOLDS = "TIGHTEN_THRESHOLDS"

# ── Escalation thresholds ─────────────────────────────────────────────────────

DRAWDOWN_DEFENSIVE_PCT:  float = 12.0   # → DEFENSIVE
DRAWDOWN_REDUCED_PCT:    float = 20.0   # → REDUCED
DRAWDOWN_CRITICAL_PCT:   float = 30.0   # → CRITICAL
DRAWDOWN_LOCKDOWN_PCT:   float = 40.0   # → LOCKDOWN

ECE_DEFENSIVE:           float =  0.12  # calibration ECE → DEFENSIVE
ECE_REDUCED:             float =  0.20  # → REDUCED
ECE_CRITICAL:            float =  0.30  # → CRITICAL

CHURN_DEFENSIVE:         float =  0.25  # shadow churn → DEFENSIVE
CHURN_REDUCED:           float =  0.40  # → REDUCED

VOL_DEFENSIVE:           float =  3.0   # rolling_vol % → DEFENSIVE
VOL_REDUCED:             float =  5.0   # → REDUCED

REPEATED_CRITICAL_THRESHOLD: int = 3    # consecutive CRITICAL hub ticks → LOCKDOWN

# ── Cooldown lengths (rows) ───────────────────────────────────────────────────

MIN_COOLDOWN_ROWS:      int = 10
LOCKDOWN_COOLDOWN_ROWS: int = 40

# ── Collection bounds ─────────────────────────────────────────────────────────

MAX_RISK_EVENTS:     int = 50
MAX_RECOMMENDATIONS: int = 10

# ── Mode policy table ─────────────────────────────────────────────────────────

_MODE_POLICY: dict = {
    MODE_NORMAL: {
        "confidence_multiplier":    1.00,
        "score_threshold_delta":    0.0,
        "max_exposure_multiplier":  1.00,
        "max_positions_multiplier": 1.00,
        "position_size_multiplier": 1.00,
        "adaptation_allowed":       True,
        "regime_suppression":       False,
    },
    MODE_DEFENSIVE: {
        "confidence_multiplier":    1.15,
        "score_threshold_delta":    0.5,
        "max_exposure_multiplier":  0.75,
        "max_positions_multiplier": 0.80,
        "position_size_multiplier": 0.80,
        "adaptation_allowed":       True,
        "regime_suppression":       False,
    },
    MODE_REDUCED: {
        "confidence_multiplier":    1.30,
        "score_threshold_delta":    1.0,
        "max_exposure_multiplier":  0.50,
        "max_positions_multiplier": 0.60,
        "position_size_multiplier": 0.60,
        "adaptation_allowed":       False,
        "regime_suppression":       True,
    },
    MODE_CRITICAL: {
        "confidence_multiplier":    1.50,
        "score_threshold_delta":    1.5,
        "max_exposure_multiplier":  0.25,
        "max_positions_multiplier": 0.30,
        "position_size_multiplier": 0.30,
        "adaptation_allowed":       False,
        "regime_suppression":       True,
    },
    MODE_LOCKDOWN: {
        "confidence_multiplier":    2.00,
        "score_threshold_delta":    3.0,
        "max_exposure_multiplier":  0.05,
        "max_positions_multiplier": 0.10,
        "position_size_multiplier": 0.10,
        "adaptation_allowed":       False,
        "regime_suppression":       True,
    },
}


# ── State creation ────────────────────────────────────────────────────────────

def create_risk_state() -> dict:
    """Return a fresh risk state at NORMAL mode."""
    return {
        "mode":                     MODE_NORMAL,
        "mode_since_row":           0,
        "row_idx":                  0,
        "risk_events":              [],
        "consecutive_critical_hub": 0,
    }


# ── Input normalisation ───────────────────────────────────────────────────────

def evaluate_risk_inputs(
    hub_report:      Optional[dict] = None,
    paper_metrics:   Optional[dict] = None,
    shadow_report:   Optional[dict] = None,
    adaptation_gate: Optional[dict] = None,
) -> dict:
    """
    Normalise subsystem reports into a flat risk-signal dict.

    All arguments are optional; missing/None values produce safe defaults so
    the dynamic-risk engine can operate with partial subsystem data.

    Returns a dict with keys:
      hub_health, drawdown_pct, rolling_vol, exposure_pct, is_risk_off,
      ece, churn_rate, stability, readiness, adaptation_policy, n_shadow_rows.
    """
    _hub    = hub_report      or {}
    _paper  = paper_metrics   or {}
    _shadow = shadow_report   or {}
    _gate   = adaptation_gate or {}

    # ── hub ──
    hub_health = _hub.get("overall_health") or HEALTH_HEALTHY

    # ECE lives in hub's calibration subsystem extracted data
    hub_statuses  = _hub.get("subsystem_statuses") or {}
    cal_status    = hub_statuses.get("calibration") or {}
    cal_extracted = cal_status.get("extracted") or {}
    ece           = cal_extracted.get("ece")

    # ── paper trading ──
    drawdown_pct = float(_paper.get("drawdown_pct") or 0.0)
    rolling_vol  = _paper.get("rolling_vol")                       # None if sparse
    exposure_pct = float(_paper.get("exposure_pct") or 0.0)
    is_risk_off  = bool(_paper.get("risk_off") or False)

    # ── shadow adaptive ──
    comparison   = _shadow.get("comparison") or {}
    churn_rate   = float(comparison.get("churn_rate") or 0.0)
    stability    = (_shadow.get("stability") or {}).get("overall") or STABILITY_STABLE
    readiness    = (_shadow.get("readiness") or {}).get("status")  or "NOT_READY"
    n_shadow_rows = int(_shadow.get("n_rows") or 0)

    # ── adaptation gate ──
    adaptation_policy = _gate.get("policy") or "DISABLED"

    return {
        "hub_health":        hub_health,
        "drawdown_pct":      drawdown_pct,
        "rolling_vol":       rolling_vol,
        "exposure_pct":      exposure_pct,
        "is_risk_off":       is_risk_off,
        "ece":               ece,
        "churn_rate":        churn_rate,
        "stability":         stability,
        "readiness":         readiness,
        "adaptation_policy": adaptation_policy,
        "n_shadow_rows":     n_shadow_rows,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _max_mode(a: str, b: str) -> str:
    """Return whichever mode has the higher severity level."""
    return a if MODE_ORDER.get(a, 0) >= MODE_ORDER.get(b, 0) else b


def _mode_from_level(level: int) -> str:
    return _LEVELS_TO_MODE.get(max(0, min(level, 4)), MODE_NORMAL)


def _required_mode_from_triggers(risk_inputs: dict) -> str:
    """
    Compute the mode that current risk metrics alone require.
    Pure — no side effects.
    """
    ri   = risk_inputs or {}
    mode = MODE_NORMAL

    # Drawdown
    dd = float(ri.get("drawdown_pct") or 0.0)
    if dd >= DRAWDOWN_LOCKDOWN_PCT:
        mode = _max_mode(mode, MODE_LOCKDOWN)
    elif dd >= DRAWDOWN_CRITICAL_PCT:
        mode = _max_mode(mode, MODE_CRITICAL)
    elif dd >= DRAWDOWN_REDUCED_PCT:
        mode = _max_mode(mode, MODE_REDUCED)
    elif dd >= DRAWDOWN_DEFENSIVE_PCT:
        mode = _max_mode(mode, MODE_DEFENSIVE)

    # Hub health
    hub = ri.get("hub_health") or HEALTH_HEALTHY
    if hub == HEALTH_CRITICAL:
        mode = _max_mode(mode, MODE_CRITICAL)
    elif hub == HEALTH_DEGRADED:
        mode = _max_mode(mode, MODE_REDUCED)
    elif hub == HEALTH_WATCH:
        mode = _max_mode(mode, MODE_DEFENSIVE)

    # Calibration ECE
    ece = ri.get("ece")
    if ece is not None:
        if ece >= ECE_CRITICAL:
            mode = _max_mode(mode, MODE_CRITICAL)
        elif ece >= ECE_REDUCED:
            mode = _max_mode(mode, MODE_REDUCED)
        elif ece >= ECE_DEFENSIVE:
            mode = _max_mode(mode, MODE_DEFENSIVE)

    # Shadow churn
    churn = float(ri.get("churn_rate") or 0.0)
    if churn >= CHURN_REDUCED:
        mode = _max_mode(mode, MODE_REDUCED)
    elif churn >= CHURN_DEFENSIVE:
        mode = _max_mode(mode, MODE_DEFENSIVE)

    # Rolling volatility
    vol = ri.get("rolling_vol")
    if vol is not None:
        if vol >= VOL_REDUCED:
            mode = _max_mode(mode, MODE_REDUCED)
        elif vol >= VOL_DEFENSIVE:
            mode = _max_mode(mode, MODE_DEFENSIVE)

    # Adaptive stability
    if ri.get("stability") == STABILITY_UNSTABLE:
        mode = _max_mode(mode, MODE_DEFENSIVE)

    # Paper risk-off flag
    if ri.get("is_risk_off"):
        mode = _max_mode(mode, MODE_DEFENSIVE)

    return mode


def _collect_triggers(risk_inputs: dict, consecutive_critical: int = 0) -> list:
    """Build a human-readable list of active risk triggers."""
    ri  = risk_inputs or {}
    out = []

    dd = float(ri.get("drawdown_pct") or 0.0)
    if dd >= DRAWDOWN_DEFENSIVE_PCT:
        out.append(f"drawdown_pct={dd:.1f}%")

    hub = ri.get("hub_health") or HEALTH_HEALTHY
    if hub in (HEALTH_WATCH, HEALTH_DEGRADED, HEALTH_CRITICAL):
        out.append(f"hub_health={hub}")

    ece = ri.get("ece")
    if ece is not None and ece >= ECE_DEFENSIVE:
        out.append(f"ece={ece:.3f}")

    churn = float(ri.get("churn_rate") or 0.0)
    if churn >= CHURN_DEFENSIVE:
        out.append(f"churn_rate={churn:.2f}")

    vol = ri.get("rolling_vol")
    if vol is not None and vol >= VOL_DEFENSIVE:
        out.append(f"rolling_vol={vol:.2f}%")

    if ri.get("stability") == STABILITY_UNSTABLE:
        out.append("stability=UNSTABLE")

    if ri.get("is_risk_off"):
        out.append("is_risk_off=True")

    if consecutive_critical >= REPEATED_CRITICAL_THRESHOLD:
        out.append(f"consecutive_critical_hub={consecutive_critical}")

    return out


# ── Core mode computation ─────────────────────────────────────────────────────

def compute_risk_mode(
    risk_inputs:          dict,
    current_mode:         str,
    mode_since_row:       int,
    current_row:          int,
    risk_events:          Optional[list] = None,
    consecutive_critical: int            = 0,
) -> dict:
    """
    Determine the target risk mode for this tick.

    Escalation is immediate (jump to worst-required level).
    De-escalation is single-step with cooldown + trigger-clear gate.
    Repeated CRITICAL hub (≥ REPEATED_CRITICAL_THRESHOLD consecutive) forces
    LOCKDOWN regardless of other triggers.

    Returns {target_mode, escalating, deescalating, triggers, cooldown_remaining}.
    """
    ri       = risk_inputs or {}
    required = _required_mode_from_triggers(ri)

    # Repeated CRITICAL hub forces LOCKDOWN
    if consecutive_critical >= REPEATED_CRITICAL_THRESHOLD:
        required = _max_mode(required, MODE_LOCKDOWN)

    triggers        = _collect_triggers(ri, consecutive_critical)
    current_level   = MODE_ORDER.get(current_mode,  0)
    required_level  = MODE_ORDER.get(required,       0)

    # ── Escalation ──
    if required_level > current_level:
        return {
            "target_mode":        required,
            "escalating":         True,
            "deescalating":       False,
            "triggers":           triggers,
            "cooldown_remaining": 0,
        }

    # ── De-escalation attempt ──
    if required_level < current_level:
        cooldown      = LOCKDOWN_COOLDOWN_ROWS if current_mode == MODE_LOCKDOWN else MIN_COOLDOWN_ROWS
        rows_in_mode  = current_row - mode_since_row
        if rows_in_mode >= cooldown:
            deesc_level = current_level - 1
            deesc_mode  = _mode_from_level(deesc_level)
            return {
                "target_mode":        deesc_mode,
                "escalating":         False,
                "deescalating":       True,
                "triggers":           [],
                "cooldown_remaining": 0,
            }
        return {
            "target_mode":        current_mode,
            "escalating":         False,
            "deescalating":       False,
            "triggers":           triggers,
            "cooldown_remaining": cooldown - (current_row - mode_since_row),
        }

    # ── Same mode ──
    return {
        "target_mode":        current_mode,
        "escalating":         False,
        "deescalating":       False,
        "triggers":           triggers,
        "cooldown_remaining": 0,
    }


# ── Policy and safeguards ─────────────────────────────────────────────────────

def apply_mode_policy(mode: str) -> dict:
    """Return a copy of the policy adjustment dict for the given mode."""
    return dict(_MODE_POLICY.get(mode) or _MODE_POLICY[MODE_NORMAL])


def determine_safeguards(mode: str, risk_inputs: Optional[dict] = None) -> list:
    """
    Select active safeguards for the current mode and risk signals.
    Returns a deduplicated ordered list.
    """
    ri  = risk_inputs or {}
    out = []

    if mode != MODE_NORMAL:
        out.append(SAFEGUARD_TIGHTEN_THRESHOLDS)
        out.append(SAFEGUARD_REDUCE_EXPOSURE)

    if mode in (MODE_REDUCED, MODE_CRITICAL, MODE_LOCKDOWN):
        out.append(SAFEGUARD_FREEZE_ADAPTATION)
        out.append(SAFEGUARD_FORCE_OBSERVATION)
    elif mode == MODE_DEFENSIVE and ri.get("stability") == STABILITY_UNSTABLE:
        out.append(SAFEGUARD_FREEZE_ADAPTATION)

    if mode in (MODE_CRITICAL, MODE_LOCKDOWN):
        out.append(SAFEGUARD_BLOCK_NEW_ENTRIES)
        out.append(SAFEGUARD_LIQUIDATE_WEAKEST)

    # Deduplicate while preserving order
    seen = set()
    result = []
    for s in out:
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


# ── Recovery readiness ────────────────────────────────────────────────────────

def compute_recovery_readiness(
    risk_inputs:    dict,
    mode:           str,
    mode_since_row: int,
    current_row:    int,
) -> dict:
    """
    Assess whether the system is ready to de-escalate one step.

    Returns {ready, rows_in_mode, cooldown_rows, blockers}.
    """
    if mode == MODE_NORMAL:
        return {"ready": True, "rows_in_mode": 0, "cooldown_rows": 0, "blockers": []}

    ri           = risk_inputs or {}
    rows_in_mode = current_row - mode_since_row
    cooldown     = LOCKDOWN_COOLDOWN_ROWS if mode == MODE_LOCKDOWN else MIN_COOLDOWN_ROWS
    blockers     = []

    if rows_in_mode < cooldown:
        blockers.append(f"cooldown:{rows_in_mode}/{cooldown}")

    required = _required_mode_from_triggers(ri)
    if MODE_ORDER.get(required, 0) >= MODE_ORDER.get(mode, 0):
        blockers.append(f"triggers_active:{required}")

    return {
        "ready":         len(blockers) == 0,
        "rows_in_mode":  rows_in_mode,
        "cooldown_rows": cooldown,
        "blockers":      blockers,
    }


# ── Recommendations ───────────────────────────────────────────────────────────

def generate_recommendations(
    mode:       str,
    risk_inputs: Optional[dict] = None,
    safeguards:  Optional[list] = None,
) -> list:
    """
    Generate human-readable operational recommendations for the current mode.
    Returns up to MAX_RECOMMENDATIONS items.
    """
    ri  = risk_inputs or {}
    sg  = safeguards  or []
    out = []

    if mode == MODE_LOCKDOWN:
        out.append("Hold in lockdown — block all new entries and adaptive changes")
        out.append("Review root cause of sustained CRITICAL hub failure before resuming")

    elif mode == MODE_CRITICAL:
        out.append("Maintain critical safeguards; monitor hub health recovery")
        if SAFEGUARD_LIQUIDATE_WEAKEST in sg:
            out.append("Flag weakest open positions for review and potential exit")

    elif mode == MODE_REDUCED:
        out.append("Freeze adaptive weight changes until weight stability recovers")
        out.append("Reduce new position size to 60 % of normal allocation")

    elif mode == MODE_DEFENSIVE:
        out.append("Tighten entry thresholds — raise minimum score by 0.5")
        out.append("Monitor for continued improvement before restoring NORMAL mode")

    # Signal-specific additions
    dd = float(ri.get("drawdown_pct") or 0.0)
    if dd >= DRAWDOWN_CRITICAL_PCT:
        out.append(f"Drawdown {dd:.1f}% — preserve capital, avoid new long exposure")

    if ri.get("stability") == STABILITY_UNSTABLE:
        out.append("Freeze unstable adaptive signals — weight oscillation detected")

    ece = ri.get("ece")
    if ece is not None and ece >= ECE_DEFENSIVE:
        out.append(f"Calibration ECE {ece:.3f} — raise confidence thresholds immediately")

    churn = float(ri.get("churn_rate") or 0.0)
    if churn >= CHURN_DEFENSIVE:
        out.append(f"Shadow churn {churn:.0%} — adaptive weights oscillating; freeze recommended")

    if ri.get("is_risk_off"):
        out.append("Paper portfolio entered risk-off mode — drawdown threshold breached")

    return out[:MAX_RECOMMENDATIONS]


# ── Main tick processor ───────────────────────────────────────────────────────

def process_risk_tick(
    risk_state:  dict,
    risk_inputs: dict,
    row_idx:     int = 0,
) -> dict:
    """
    Advance the risk state by one tick.

    Returns {new_state, policy, active_safeguards, recommendations,
             mode_changed, new_events}.

    Input state is never mutated.
    """
    _state = risk_state  or create_risk_state()
    _ri    = risk_inputs or {}

    current_mode    = _state.get("mode")           or MODE_NORMAL
    mode_since_row  = _state.get("mode_since_row") or 0
    prev_events     = list(_state.get("risk_events") or [])
    consecutive     = _state.get("consecutive_critical_hub") or 0

    # Update consecutive CRITICAL hub counter
    hub_health = _ri.get("hub_health") or HEALTH_HEALTHY
    if hub_health == HEALTH_CRITICAL:
        consecutive += 1
    else:
        consecutive = 0

    mode_result   = compute_risk_mode(
        _ri, current_mode, mode_since_row, row_idx, prev_events, consecutive,
    )
    target_mode   = mode_result["target_mode"]
    mode_changed  = target_mode != current_mode

    # Build event if mode changed or active triggers present
    new_event_list = prev_events
    tick_events    = []
    if mode_changed or mode_result.get("triggers"):
        evt_type = (
            "ESCALATION"   if mode_result.get("escalating")
            else "DEESCALATION" if mode_result.get("deescalating")
            else "TRIGGER_ACTIVE"
        )
        evt = {
            "event_type": evt_type,
            "from_mode":  current_mode,
            "to_mode":    target_mode,
            "row_idx":    row_idx,
            "triggers":   mode_result.get("triggers") or [],
        }
        tick_events     = [evt]
        new_event_list  = (prev_events + [evt])[-MAX_RISK_EVENTS:]

        if evt_type == "ESCALATION":
            log.warning(
                "dynamic_risk: ESCALATE %s → %s row=%d triggers=%s",
                current_mode, target_mode, row_idx,
                ";".join(mode_result.get("triggers") or []),
            )
        elif evt_type == "DEESCALATION":
            log.info(
                "dynamic_risk: DEESCALATE %s → %s row=%d",
                current_mode, target_mode, row_idx,
            )

    new_state = {
        "mode":                     target_mode,
        "mode_since_row":           row_idx if mode_changed else mode_since_row,
        "row_idx":                  row_idx,
        "risk_events":              new_event_list,
        "consecutive_critical_hub": consecutive,
    }

    policy     = apply_mode_policy(target_mode)
    safeguards = determine_safeguards(target_mode, _ri)
    recs       = generate_recommendations(target_mode, _ri, safeguards)

    return {
        "new_state":         new_state,
        "policy":            policy,
        "active_safeguards": safeguards,
        "recommendations":   recs,
        "mode_changed":      mode_changed,
        "new_events":        tick_events,
    }


# ── Report generation ─────────────────────────────────────────────────────────

def generate_report(
    risk_state:      dict,
    risk_inputs:     dict,
    policy:          Optional[dict] = None,
    safeguards:      Optional[list] = None,
    recommendations: Optional[list] = None,
) -> dict:
    """
    Full risk management snapshot report.

    Returns {current_mode, policy, active_safeguards, escalation_history,
             stabilization_progress, exposure_policy, recovery_readiness,
             operational_threats, recommendations, row_idx, rows_in_mode}.
    """
    _state = risk_state  or create_risk_state()
    _ri    = risk_inputs or {}
    mode   = _state.get("mode") or MODE_NORMAL

    _policy     = policy         or apply_mode_policy(mode)
    _safeguards = safeguards     or determine_safeguards(mode, _ri)
    _recs       = recommendations or generate_recommendations(mode, _ri, _safeguards)

    all_events         = _state.get("risk_events") or []
    escalation_history = [
        e for e in all_events
        if e.get("event_type") in ("ESCALATION", "DEESCALATION")
    ][-10:]

    triggers = _collect_triggers(_ri, _state.get("consecutive_critical_hub") or 0)

    mode_since = _state.get("mode_since_row") or 0
    row_idx    = _state.get("row_idx")        or 0
    rows_in_mode = row_idx - mode_since

    readiness = compute_recovery_readiness(_ri, mode, mode_since, row_idx)

    return {
        "current_mode":      mode,
        "policy":            _policy,
        "active_safeguards": _safeguards,
        "escalation_history": escalation_history,
        "stabilization_progress": {
            "rows_in_mode":             rows_in_mode,
            "consecutive_critical_hub": _state.get("consecutive_critical_hub") or 0,
        },
        "exposure_policy": {
            "max_exposure_multiplier":  _policy["max_exposure_multiplier"],
            "max_positions_multiplier": _policy["max_positions_multiplier"],
            "position_size_multiplier": _policy["position_size_multiplier"],
        },
        "recovery_readiness":  readiness,
        "operational_threats": triggers,
        "recommendations":     _recs,
        "row_idx":             row_idx,
        "rows_in_mode":        rows_in_mode,
    }
