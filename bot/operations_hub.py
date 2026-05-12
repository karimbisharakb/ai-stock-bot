"""
Operational intelligence hub for the Predator scanner.
Phase 4A — read-only aggregation; does NOT modify any live scoring logic.

Aggregates outputs from all analytics subsystems into a single unified
health snapshot.  Designed to be called periodically (or on demand) so
operators have a single entry point for system observability.

Subsystems consumed
-------------------
  confidence_validation   — calibration quality and overconfidence flags
  regime_validation       — regime inversion and suppression effectiveness
  meta_performance        — rolling-window degradation and safeguard ladder
  combo_validation        — deceptive / unstable signal combinations
  adaptive_weights        — current weight adjustments (boost / penalise)
  recommendation_observer — historical weight stability and drift events
  paper_portfolio         — simulated portfolio health and concentration
  decision_audit          — anomaly counts across scored alerts (optional)

Health levels (ascending severity)
-----------------------------------
  HEALTHY   all subsystems nominal
  WATCH     one or more soft warnings; no structural failures
  DEGRADED  multiple high-severity events or structural instabilities
  CRITICAL  safeguard OBSERVATION_ONLY active or multiple DEGRADED systems

Usage in tests
--------------
Pass a pre-built ``subsystem_reports`` dict so no DB or network calls occur:

    hub = generate_report(subsystem_reports={
        "confidence": confidence_report,
        "regime":     regime_report,
        ...
    })
"""
import logging
from typing import Optional

from outcome_analytics import MIN_ROWS_FOR_STATS, _fetch_completed_outcomes

log = logging.getLogger(__name__)

# ── Health level constants ────────────────────────────────────────────────────

HEALTH_HEALTHY  = "HEALTHY"
HEALTH_WATCH    = "WATCH"
HEALTH_DEGRADED = "DEGRADED"
HEALTH_CRITICAL = "CRITICAL"

HEALTH_ORDER: dict = {
    HEALTH_HEALTHY:  0,
    HEALTH_WATCH:    1,
    HEALTH_DEGRADED: 2,
    HEALTH_CRITICAL: 3,
}

# ── Safeguard ladder mapping ──────────────────────────────────────────────────

SAFEGUARD_NONE                   = "NONE"
SAFEGUARD_REDUCE                 = "REDUCE_AGGRESSIVENESS"
SAFEGUARD_INCREASE_CONF          = "INCREASE_CONFIDENCE_THRESHOLD"
SAFEGUARD_PAUSE                  = "PAUSE_ADAPTIVE_ROLLOUT"
SAFEGUARD_OBSERVATION_ONLY       = "OBSERVATION_ONLY"

SAFEGUARD_LEVEL_ORDER: dict = {
    SAFEGUARD_NONE:            0,
    SAFEGUARD_REDUCE:          1,
    SAFEGUARD_INCREASE_CONF:   2,
    SAFEGUARD_PAUSE:           3,
    SAFEGUARD_OBSERVATION_ONLY: 4,
}

# ── Thresholds ────────────────────────────────────────────────────────────────

ALERT_HIGH_WARNING_THRESHOLD:    int = 5    # total HIGH warnings → operational alert
CRITICAL_SUBSYSTEM_THRESHOLD:    int = 1    # n CRITICAL subsystems → overall CRITICAL
DEGRADED_SUBSYSTEM_THRESHOLD:    int = 2    # n DEGRADED subsystems → overall DEGRADED

CALIBRATION_POOR_DEGRAD_WARNS:   int = 3    # POOR quality + ≥ N warnings → DEGRADED
CALIBRATION_POOR_OVERCONF:       int = 2    # POOR quality + ≥ N overconf flags → DEGRADED
REGIME_INVERSION_DEGRAD_WARNS:   int = 3    # inversions + ≥ N warnings → DEGRADED
COMBO_DECEPTIVE_DEGRADED:        int = 3    # ≥ N deceptive combos → DEGRADED
COMBO_UNSTABLE_DEGRADED:         int = 5    # ≥ N unstable combos → DEGRADED
OBSERVER_UNSTABLE_DEGRADED:      int = 3    # ≥ N unstable signals → DEGRADED
OBSERVER_DRIFT_DEGRADED:         int = 3    # ≥ N drift events    → DEGRADED
TOP_CONCERNS_LIMIT:              int = 8    # max top-concern strings in report


# ── Extraction helpers ────────────────────────────────────────────────────────
# Each helper normalises a subsystem report into a flat dict of key metrics.

def _extract_calibration(report: dict) -> dict:
    cal   = report.get("calibration") or {}
    return {
        "quality":                  cal.get("quality") or "INSUFFICIENT_DATA",
        "ece":                      cal.get("ece"),
        "correlation":              cal.get("correlation"),
        "overconfidence_flag_count": len(report.get("overconfidence_flags") or []),
        "warning_count":            len(report.get("warnings") or []),
        "row_count":                report.get("row_count") or 0,
    }


def _extract_regime(report: dict) -> dict:
    inv = report.get("inversion") or {}
    strongest = report.get("strongest_regime") or {}
    weakest   = report.get("weakest_regime")   or {}
    return {
        "inversion_count":  inv.get("inversion_count") or 0,
        "strongest_regime": strongest.get("regime"),
        "weakest_regime":   weakest.get("regime"),
        "warning_count":    len(report.get("warnings") or []),
        "row_count":        report.get("row_count") or 0,
    }


def _extract_meta(report: dict) -> dict:
    safeguards     = report.get("safeguard_recommendations") or []
    rec_names      = [r.get("recommendation") or "" for r in safeguards]
    degrad         = report.get("degradation_events")  or []
    inflate        = report.get("inflation_events")    or []
    regime_ev      = report.get("regime_events")       or []
    high_degrad    = [e for e in degrad    if e.get("severity") == "HIGH"]

    # Highest safeguard level in effect
    if SAFEGUARD_OBSERVATION_ONLY in rec_names:
        safeguard_level = SAFEGUARD_OBSERVATION_ONLY
    elif SAFEGUARD_PAUSE in rec_names:
        safeguard_level = SAFEGUARD_PAUSE
    elif SAFEGUARD_INCREASE_CONF in rec_names:
        safeguard_level = SAFEGUARD_INCREASE_CONF
    elif SAFEGUARD_REDUCE in rec_names:
        safeguard_level = SAFEGUARD_REDUCE
    else:
        safeguard_level = SAFEGUARD_NONE

    return {
        "safeguard_level":         safeguard_level,
        "degradation_event_count": len(degrad),
        "high_degradation_count":  len(high_degrad),
        "inflation_event_count":   len(inflate),
        "regime_event_count":      len(regime_ev),
        "strongest_window":        report.get("strongest_window"),
        "weakest_window":          report.get("weakest_window"),
        "warning_count":           len(report.get("warnings") or []),
        "row_count":               report.get("row_count") or 0,
    }


def _extract_combo(report: dict) -> dict:
    return {
        "deceptive_count": len(report.get("deceptive_combos")  or []),
        "unstable_count":  len(report.get("unstable_combos")   or []),
        "pair_count":      report.get("pair_count")  or 0,
        "triple_count":    report.get("triple_count") or 0,
        "warning_count":   len(report.get("warnings") or []),
        "row_count":       report.get("row_count") or 0,
    }


def _extract_weights(report: dict) -> dict:
    summary = report.get("summary") or {}
    return {
        "n_boosted":              len(summary.get("signals_boosted")   or []),
        "n_penalized":            len(summary.get("signals_penalized") or []),
        "n_held":                 len(summary.get("signals_held")      or []),
        "total_default_weight":   summary.get("total_default_weight"),
        "total_suggested_weight": summary.get("total_suggested_weight"),
        "row_count":              report.get("row_count") or 0,
    }


def _extract_observer(report: dict) -> dict:
    summary = report.get("summary") or {}
    return {
        "n_stable":          summary.get("n_stable")          or 0,
        "n_slowly_adapting": summary.get("n_slowly_adapting") or 0,
        "n_unstable":        summary.get("n_unstable")        or 0,
        "has_drift":         summary.get("has_drift")         or False,
        "drift_event_count": summary.get("drift_event_count") or 0,
        "snapshot_count":    report.get("snapshot_count")     or 0,
    }


def _extract_portfolio(report: dict) -> dict:
    metrics = report.get("metrics")       or {}
    conc    = report.get("concentration") or {}
    rob     = report.get("robustness")    or {}
    return {
        "portfolio_health":             report.get("portfolio_health") or "INSUFFICIENT_DATA",
        "win_rate":                     metrics.get("win_rate"),
        "cumulative_return_pct":        metrics.get("cumulative_return_pct"),
        "max_drawdown_pct":             metrics.get("max_drawdown_pct"),
        "concentration_warning_count":  len(conc.get("warnings") or []),
        "robustness_warning_count":     len(rob.get("warnings")  or []),
        "n_trades":                     metrics.get("n_trades")   or 0,
        "row_count":                    report.get("row_count")   or 0,
    }


def _extract_audit(batch: dict) -> dict:
    return {
        "count":                batch.get("count")              or 0,
        "total_anomaly_count":  sum((batch.get("anomaly_summary") or {}).values()),
        "high_severity_count":  batch.get("high_severity_count") or 0,
        "anomaly_summary":      batch.get("anomaly_summary")     or {},
        "tier_breakdown":       batch.get("tier_breakdown")      or {},
    }


# ── Subsystem health classifiers ──────────────────────────────────────────────

def _classify_calibration(ex: dict) -> str:
    quality   = ex.get("quality") or "INSUFFICIENT_DATA"
    n_warn    = ex.get("warning_count") or 0
    n_overconf = ex.get("overconfidence_flag_count") or 0

    if quality == "POOR" and (n_warn >= CALIBRATION_POOR_DEGRAD_WARNS
                              or n_overconf >= CALIBRATION_POOR_OVERCONF):
        return HEALTH_DEGRADED
    if quality == "POOR" or n_warn >= 2:
        return HEALTH_WATCH
    if quality == "FAIR" or n_warn >= 1:
        return HEALTH_WATCH
    return HEALTH_HEALTHY


def _classify_regime(ex: dict) -> str:
    inv    = ex.get("inversion_count") or 0
    n_warn = ex.get("warning_count")   or 0

    if inv >= 2 and n_warn >= REGIME_INVERSION_DEGRAD_WARNS:
        return HEALTH_DEGRADED
    if inv >= 1 or n_warn >= 2:
        return HEALTH_WATCH
    return HEALTH_HEALTHY


def _classify_meta(ex: dict) -> str:
    level_order = SAFEGUARD_LEVEL_ORDER.get(
        ex.get("safeguard_level") or SAFEGUARD_NONE, 0
    )

    if level_order >= SAFEGUARD_LEVEL_ORDER[SAFEGUARD_OBSERVATION_ONLY]:
        return HEALTH_CRITICAL
    if level_order >= SAFEGUARD_LEVEL_ORDER[SAFEGUARD_PAUSE]:
        return HEALTH_DEGRADED
    if level_order >= SAFEGUARD_LEVEL_ORDER[SAFEGUARD_REDUCE]:
        return HEALTH_WATCH
    return HEALTH_HEALTHY


def _classify_combo(ex: dict) -> str:
    deceptive = ex.get("deceptive_count") or 0
    unstable  = ex.get("unstable_count")  or 0

    if deceptive >= COMBO_DECEPTIVE_DEGRADED or unstable >= COMBO_UNSTABLE_DEGRADED:
        return HEALTH_DEGRADED
    if deceptive >= 1 or unstable >= 2:
        return HEALTH_WATCH
    return HEALTH_HEALTHY


def _classify_observer(ex: dict) -> str:
    n_unstable   = ex.get("n_unstable")        or 0
    drift_count  = ex.get("drift_event_count") or 0

    if (n_unstable  >= OBSERVER_UNSTABLE_DEGRADED
            or drift_count >= OBSERVER_DRIFT_DEGRADED):
        return HEALTH_DEGRADED
    if n_unstable >= 1 or drift_count >= 1:
        return HEALTH_WATCH
    return HEALTH_HEALTHY


def _classify_portfolio(ex: dict) -> str:
    ph        = ex.get("portfolio_health") or "INSUFFICIENT_DATA"
    conc_warn = ex.get("concentration_warning_count") or 0
    rob_warn  = ex.get("robustness_warning_count")    or 0

    if ph == "WEAK" and (conc_warn + rob_warn) >= 2:
        return HEALTH_DEGRADED
    if ph in ("WEAK", "CAUTION"):
        return HEALTH_WATCH
    return HEALTH_HEALTHY


def _classify_audit(ex: dict) -> str:
    n_high = ex.get("high_severity_count") or 0
    n_total = ex.get("total_anomaly_count") or 0

    if n_high >= 5:
        return HEALTH_DEGRADED
    if n_high >= 2 or n_total >= 5:
        return HEALTH_WATCH
    return HEALTH_HEALTHY


_CLASSIFIER_MAP = {
    "calibration": _classify_calibration,
    "regime":      _classify_regime,
    "meta":        _classify_meta,
    "combo":       _classify_combo,
    "observer":    _classify_observer,
    "portfolio":   _classify_portfolio,
    "audit":       _classify_audit,
}

_EXTRACTOR_MAP = {
    "calibration": _extract_calibration,
    "regime":      _extract_regime,
    "meta":        _extract_meta,
    "combo":       _extract_combo,
    "weights":     _extract_weights,
    "observer":    _extract_observer,
    "portfolio":   _extract_portfolio,
    "audit":       _extract_audit,
}


def classify_subsystem_health(name: str, extracted: dict) -> str:
    """Return health label for one subsystem given its extracted metrics."""
    fn = _CLASSIFIER_MAP.get(name)
    return fn(extracted) if fn else HEALTH_HEALTHY


# ── Overall health ────────────────────────────────────────────────────────────

def classify_overall_health(subsystem_statuses: dict) -> str:
    """
    Determine overall system health from the subsystem status map.

    Rule
    ----
    Any CRITICAL → CRITICAL.
    ≥ DEGRADED_SUBSYSTEM_THRESHOLD DEGRADED → DEGRADED.
    ≥ 1 DEGRADED or ≥ 2 WATCH → WATCH.
    Otherwise → HEALTHY.
    """
    levels = [HEALTH_ORDER.get(s.get("health", HEALTH_HEALTHY), 0)
              for s in subsystem_statuses.values()]

    n_critical = sum(1 for lvl in levels if lvl == HEALTH_ORDER[HEALTH_CRITICAL])
    n_degraded = sum(1 for lvl in levels if lvl == HEALTH_ORDER[HEALTH_DEGRADED])
    n_watch    = sum(1 for lvl in levels if lvl == HEALTH_ORDER[HEALTH_WATCH])

    if n_critical >= CRITICAL_SUBSYSTEM_THRESHOLD:
        return HEALTH_CRITICAL
    if n_degraded >= DEGRADED_SUBSYSTEM_THRESHOLD:
        return HEALTH_DEGRADED
    if n_degraded >= 1 or n_watch >= 2:
        return HEALTH_WATCH
    if n_watch >= 1:
        return HEALTH_WATCH
    return HEALTH_HEALTHY


# ── Operational alerts ────────────────────────────────────────────────────────

def operational_alerts(subsystem_statuses: dict, total_high_warnings: int) -> list:
    """
    Produce a list of actionable alert strings for operators.

    Each string is prefixed with its urgency level: [CRITICAL], [HIGH], [MEDIUM].
    """
    alerts = []

    for name, status in subsystem_statuses.items():
        health  = status.get("health") or HEALTH_HEALTHY
        ex      = status.get("extracted") or {}

        if health == HEALTH_CRITICAL:
            if name == "meta":
                level = ex.get("safeguard_level") or "?"
                alerts.append(
                    f"[CRITICAL] meta_performance safeguard={level} — "
                    "suspend adaptive scoring changes immediately"
                )
            else:
                alerts.append(f"[CRITICAL] {name} subsystem is CRITICAL")

        elif health == HEALTH_DEGRADED:
            if name == "calibration":
                quality = ex.get("quality") or "?"
                alerts.append(
                    f"[HIGH] calibration={quality} with "
                    f"{ex.get('warning_count', 0)} warning(s) — "
                    "confidence scores may be unreliable"
                )
            elif name == "meta":
                level = ex.get("safeguard_level") or "?"
                alerts.append(
                    f"[HIGH] meta_performance safeguard={level} — "
                    f"{ex.get('high_degradation_count', 0)} HIGH degradation event(s)"
                )
            elif name == "combo":
                alerts.append(
                    f"[HIGH] combo_validation: "
                    f"{ex.get('deceptive_count', 0)} deceptive combos, "
                    f"{ex.get('unstable_count', 0)} unstable combos"
                )
            elif name == "observer":
                alerts.append(
                    f"[HIGH] recommendation_observer: "
                    f"{ex.get('n_unstable', 0)} unstable signal(s), "
                    f"{ex.get('drift_event_count', 0)} drift event(s)"
                )
            elif name == "portfolio":
                ph = ex.get("portfolio_health") or "?"
                alerts.append(
                    f"[HIGH] portfolio={ph} with "
                    f"{ex.get('concentration_warning_count', 0)} concentration "
                    f"and {ex.get('robustness_warning_count', 0)} robustness warning(s)"
                )
            elif name == "audit":
                alerts.append(
                    f"[HIGH] decision_audit: "
                    f"{ex.get('high_severity_count', 0)} high-severity anomaly(ies)"
                )
            else:
                alerts.append(f"[HIGH] {name} is DEGRADED")

    if total_high_warnings >= ALERT_HIGH_WARNING_THRESHOLD:
        alerts.append(
            f"[HIGH] {total_high_warnings} total HIGH-severity warnings across "
            "all subsystems — review analytics immediately"
        )

    log.info("operations_hub: %d operational alert(s) generated", len(alerts))
    for a in alerts:
        if a.startswith("[CRITICAL]"):
            log.warning("operations_hub: %s", a)

    return alerts


# ── Change detection ──────────────────────────────────────────────────────────

def detect_changes(current: dict, previous: Optional[dict]) -> list:
    """
    Compare the current hub snapshot against a previous one.

    Returns a list of change-event dicts, each with:
      { "type", "subsystem", "from", "to", "direction" }

    Direction: "IMPROVED" (lower severity) or "WORSENED" (higher severity).
    """
    if not previous:
        return []

    changes = []

    def _direction(frm, to):
        fo = HEALTH_ORDER.get(frm, 0)
        to_ = HEALTH_ORDER.get(to,  0)
        if to_ < fo:
            return "IMPROVED"
        if to_ > fo:
            return "WORSENED"
        return "UNCHANGED"

    # Overall health change
    curr_health = current.get("overall_health")
    prev_health = previous.get("overall_health")
    if curr_health and prev_health and curr_health != prev_health:
        direction = _direction(prev_health, curr_health)
        changes.append({
            "type":      "OVERALL_HEALTH_CHANGE",
            "subsystem": "overall",
            "from":      prev_health,
            "to":        curr_health,
            "direction": direction,
        })
        log.info(
            "operations_hub: overall health %s → %s (%s)",
            prev_health, curr_health, direction,
        )

    # Subsystem health changes
    curr_statuses = current.get("subsystem_statuses")  or {}
    prev_statuses = previous.get("subsystem_statuses") or {}

    for name in curr_statuses:
        curr_h = curr_statuses[name].get("health")
        prev_h = (prev_statuses.get(name) or {}).get("health")
        if curr_h and prev_h and curr_h != prev_h:
            direction = _direction(prev_h, curr_h)
            changes.append({
                "type":      "SUBSYSTEM_HEALTH_CHANGE",
                "subsystem": name,
                "from":      prev_h,
                "to":        curr_h,
                "direction": direction,
            })

    # Safeguard level change
    curr_safeguard = (
        (curr_statuses.get("meta") or {})
        .get("extracted", {})
        .get("safeguard_level")
    )
    prev_safeguard = (
        (prev_statuses.get("meta") or {})
        .get("extracted", {})
        .get("safeguard_level")
    )
    if (curr_safeguard and prev_safeguard
            and curr_safeguard != prev_safeguard):
        co = SAFEGUARD_LEVEL_ORDER.get(curr_safeguard, 0)
        po = SAFEGUARD_LEVEL_ORDER.get(prev_safeguard, 0)
        direction = "ESCALATED" if co > po else "DE_ESCALATED"
        changes.append({
            "type":      "SAFEGUARD_CHANGE",
            "subsystem": "meta",
            "from":      prev_safeguard,
            "to":        curr_safeguard,
            "direction": direction,
        })

    # Calibration quality change
    curr_quality = (
        (curr_statuses.get("calibration") or {})
        .get("extracted", {})
        .get("quality")
    )
    prev_quality = (
        (prev_statuses.get("calibration") or {})
        .get("extracted", {})
        .get("quality")
    )
    _QUALITY_ORDER = {
        "GOOD":             0,
        "FAIR":             1,
        "POOR":             2,
        "INSUFFICIENT_DATA": 3,
    }
    if curr_quality and prev_quality and curr_quality != prev_quality:
        co = _QUALITY_ORDER.get(curr_quality, 99)
        po = _QUALITY_ORDER.get(prev_quality, 99)
        direction = "IMPROVED" if co < po else "WORSENED"
        changes.append({
            "type":      "CALIBRATION_QUALITY_CHANGE",
            "subsystem": "calibration",
            "from":      prev_quality,
            "to":        curr_quality,
            "direction": direction,
        })

    return changes


# ── Top concerns collector ────────────────────────────────────────────────────

def _collect_top_concerns(
    subsystem_reports: dict,
    subsystem_statuses: dict,
) -> list:
    """
    Gather warning strings from all subsystem reports, de-duplicate, and return
    the top TOP_CONCERNS_LIMIT entries sorted: CRITICAL > DEGRADED > WATCH.
    """
    HIGH_PREFIX = ("[HIGH]", "[CRITICAL]", "SAFEGUARD")
    scored = []

    for name in ("confidence", "regime", "meta", "combo", "portfolio"):
        rep = subsystem_reports.get(name) or {}
        health = (subsystem_statuses.get(
            "calibration" if name == "confidence" else name
        ) or {}).get("health", HEALTH_HEALTHY)
        level = HEALTH_ORDER.get(health, 0)

        for w in (rep.get("warnings") or []):
            is_high = any(w.startswith(p) for p in HIGH_PREFIX)
            scored.append((level + (1 if is_high else 0), w))

    # De-duplicate while preserving order
    seen     = set()
    concerns = []
    for score, w in sorted(scored, key=lambda x: -x[0]):
        if w not in seen:
            seen.add(w)
            concerns.append(w)
        if len(concerns) >= TOP_CONCERNS_LIMIT:
            break

    return concerns


# ── Recommendations collector ─────────────────────────────────────────────────

def _collect_recommendations(
    subsystem_reports: dict,
    meta_extracted: dict,
) -> list:
    """Gather recommendations from meta safeguards and portfolio module."""
    recs = []

    # Meta safeguard ladder
    for r in (subsystem_reports.get("meta") or {}).get("safeguard_recommendations") or []:
        rec_text = r.get("recommendation") or ""
        reason   = r.get("reason")         or ""
        if rec_text:
            recs.append(f"{rec_text}: {reason}" if reason else rec_text)

    # Portfolio recommendations
    for r in (subsystem_reports.get("portfolio") or {}).get("recommendations") or []:
        if r not in recs:
            recs.append(r)

    return recs


# ── Executive summary ─────────────────────────────────────────────────────────

def executive_summary(
    overall_health:     str,
    subsystem_statuses: dict,
    top_concerns:       list,
    recommendations:    list,
    row_count:          int,
    n_alerts:           int,
) -> str:
    """
    Build a compact human-readable operational overview.

    Deterministic: same inputs always produce the same string.
    """
    lines = [
        f"OPERATIONAL STATUS: {overall_health}",
        f"{row_count} completed alert(s) analysed",
        "",
        "SUBSYSTEMS",
    ]

    label_width = max((len(n) for n in subsystem_statuses), default=10)
    for name, status in sorted(subsystem_statuses.items()):
        health  = status.get("health") or "?"
        ex      = status.get("extracted") or {}
        detail  = _subsystem_detail_line(name, ex)
        lines.append(f"  {name:<{label_width}}  {health:<9}  {detail}")

    if top_concerns:
        lines.append("")
        lines.append(f"TOP CONCERNS ({len(top_concerns)})")
        for w in top_concerns:
            lines.append(f"  • {w}")

    if recommendations:
        lines.append("")
        lines.append("RECOMMENDATIONS")
        for r in recommendations:
            lines.append(f"  • {r}")

    if n_alerts > 0:
        lines.append("")
        lines.append(f"OPERATIONAL ALERTS: {n_alerts}")

    return "\n".join(lines)


def _subsystem_detail_line(name: str, ex: dict) -> str:
    """One-line detail string for a subsystem's extracted metrics."""
    if name == "calibration":
        q   = ex.get("quality") or "?"
        ece = ex.get("ece")
        return f"quality={q}" + (f", ece={ece:.3f}" if ece is not None else "")
    if name == "regime":
        inv = ex.get("inversion_count") or 0
        s   = ex.get("strongest_regime") or "?"
        return f"inversions={inv}, strongest={s}"
    if name == "meta":
        lvl = ex.get("safeguard_level") or "NONE"
        hd  = ex.get("high_degradation_count") or 0
        return f"safeguard={lvl}, high_degrad={hd}"
    if name == "combo":
        d = ex.get("deceptive_count") or 0
        u = ex.get("unstable_count")  or 0
        return f"deceptive={d}, unstable={u}"
    if name == "observer":
        un  = ex.get("n_unstable")        or 0
        drft = ex.get("drift_event_count") or 0
        return f"unstable_signals={un}, drift={drft}"
    if name == "portfolio":
        ph = ex.get("portfolio_health") or "?"
        wr = ex.get("win_rate")
        return f"health={ph}" + (f", win_rate={wr:.1f}%" if wr is not None else "")
    if name == "audit":
        hs = ex.get("high_severity_count") or 0
        return f"high_severity_anomalies={hs}"
    return ""


# ── Report builder ────────────────────────────────────────────────────────────

def generate_report(
    subsystem_reports:  Optional[dict] = None,
    rows:               Optional[list] = None,
    snapshots:          Optional[list] = None,
    audit_snapshots:    Optional[list] = None,
    previous_snapshot:  Optional[dict] = None,
) -> dict:
    """
    Full operational intelligence report.

    Parameters
    ----------
    subsystem_reports  : pre-built dict keyed by subsystem name (for tests / caching).
                         Supported keys: "confidence", "regime", "meta", "combo",
                         "weights", "observer", "portfolio", "audit".
                         Any missing key is fetched live via the subsystem's own
                         generate_report() / generate_weight_report().
    rows               : pre-fetched outcome rows shared across subsystems that
                         accept a rows argument.
    snapshots          : pre-fetched weight snapshots for recommendation_observer.
    audit_snapshots    : pre-fetched decision snapshots for decision_audit.
    previous_snapshot  : a prior generate_report() output for change detection.

    Returns
    -------
    {
        "row_count":           int,
        "snapshot_count":      int,
        "overall_health":      str,
        "subsystem_statuses":  { name: {"health": str, "extracted": dict} },
        "operational_alerts":  [ str ],
        "changes":             [ change_dict ],
        "top_concerns":        [ str ],
        "recommendations":     [ str ],
        "executive_summary":   str,
        "warnings":            [ str ],     # flattened across all subsystems
    }
    """
    sr = subsystem_reports or {}

    # ── Fetch rows once if needed ─────────────────────────────────────────────
    if rows is None and not all(k in sr for k in ("confidence", "regime", "meta",
                                                   "combo", "weights", "portfolio")):
        rows = _fetch_completed_outcomes()

    n_rows = len(rows) if rows is not None else 0

    # ── Build missing subsystem reports ───────────────────────────────────────
    # Lazy import inside the function to keep module-level imports minimal
    # and to ensure tests can mock subsystems easily.
    if "confidence" not in sr:
        from confidence_validation import generate_report as _cv_report
        sr = dict(sr, confidence=_cv_report(rows))

    if "regime" not in sr:
        from regime_validation import generate_report as _rv_report
        sr = dict(sr, regime=_rv_report(rows))

    if "meta" not in sr:
        from meta_performance import generate_report as _mp_report
        sr = dict(sr, meta=_mp_report(rows))

    if "combo" not in sr:
        from combo_validation import generate_report as _co_report
        sr = dict(sr, combo=_co_report(rows))

    if "weights" not in sr:
        from adaptive_weights import generate_weight_report as _aw_report
        sr = dict(sr, weights=_aw_report(rows))

    if "observer" not in sr:
        from recommendation_observer import generate_observation_report as _ro_report
        sr = dict(sr, observer=_ro_report(snapshots))

    if "portfolio" not in sr:
        from paper_portfolio import generate_report as _pp_report
        sr = dict(sr, portfolio=_pp_report(rows))

    if "audit" not in sr and audit_snapshots is not None:
        from decision_audit import audit_batch as _audit_batch
        sr = dict(sr, audit=_audit_batch(audit_snapshots))

    # ── Extract and classify each subsystem ───────────────────────────────────
    subsystem_statuses: dict = {}

    for name, extractor in _EXTRACTOR_MAP.items():
        report_key = "confidence" if name == "calibration" else name
        rep = sr.get(report_key)
        if rep is None:
            continue
        extracted = extractor(rep)
        health    = classify_subsystem_health(name, extracted)
        subsystem_statuses[name] = {
            "health":   health,
            "extracted": extracted,
        }

    # weights doesn't have its own classifier; fold into observer context
    if "weights" in sr and "observer" not in subsystem_statuses:
        subsystem_statuses["observer"] = {
            "health":    HEALTH_HEALTHY,
            "extracted": _extract_weights(sr["weights"]),
        }

    # ── Overall health ────────────────────────────────────────────────────────
    overall_health = classify_overall_health(subsystem_statuses)

    # ── Count HIGH-severity warnings ──────────────────────────────────────────
    all_warnings: list = []
    for name in ("confidence", "regime", "meta", "combo", "portfolio"):
        all_warnings.extend((sr.get(name) or {}).get("warnings") or [])

    high_warn_count = sum(
        1 for w in all_warnings
        if w.startswith("[HIGH]") or w.startswith("[CRITICAL]")
    )

    # ── Operational alerts ────────────────────────────────────────────────────
    op_alerts = operational_alerts(subsystem_statuses, high_warn_count)

    # ── Change detection ──────────────────────────────────────────────────────
    current_partial = {
        "overall_health":    overall_health,
        "subsystem_statuses": subsystem_statuses,
    }
    changes = detect_changes(current_partial, previous_snapshot)

    # ── Top concerns and recommendations ─────────────────────────────────────
    top_concerns = _collect_top_concerns(sr, subsystem_statuses)
    recs         = _collect_recommendations(sr, subsystem_statuses.get("meta", {}).get("extracted") or {})

    # snapshot count from observer
    n_snapshots = (subsystem_statuses.get("observer") or {}).get("extracted", {}).get("snapshot_count") or 0

    # ── Executive summary ─────────────────────────────────────────────────────
    summary = executive_summary(
        overall_health     = overall_health,
        subsystem_statuses = subsystem_statuses,
        top_concerns       = top_concerns,
        recommendations    = recs,
        row_count          = n_rows,
        n_alerts           = len(op_alerts),
    )

    log.info(
        "operations_hub: report done — health=%s subsystems=%d alerts=%d "
        "changes=%d concerns=%d",
        overall_health, len(subsystem_statuses), len(op_alerts),
        len(changes), len(top_concerns),
    )

    if overall_health in (HEALTH_CRITICAL, HEALTH_DEGRADED):
        log.warning(
            "operations_hub: system is %s — %d operational alert(s)",
            overall_health, len(op_alerts),
        )

    return {
        "row_count":          n_rows,
        "snapshot_count":     n_snapshots,
        "overall_health":     overall_health,
        "subsystem_statuses": subsystem_statuses,
        "operational_alerts": op_alerts,
        "changes":            changes,
        "top_concerns":       top_concerns,
        "recommendations":    recs,
        "executive_summary":  summary,
        "warnings":           all_warnings,
    }
