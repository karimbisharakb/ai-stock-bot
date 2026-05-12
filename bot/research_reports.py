"""
Automated research report generation for the Predator scanner.
Phase 4B — read-only; does NOT modify live scoring logic.

Generates deterministic, hedge-fund-style analytics reports by aggregating
outputs from all analytics subsystems.  All report builders accept pre-built
subsystem report dicts so tests never touch the database.

Report types
------------
  daily_operational_report      current state across all subsystems
  weekly_performance_report     compounded performance lens
  calibration_research_report   deep-dive on confidence calibration
  regime_research_report        regime effectiveness and inversions
  portfolio_research_report     paper portfolio health and stress
  adaptive_recommendation_report weight stability and drift
  anomaly_research_report        decision audit anomaly counts
  degradation_research_report    rolling-window degradation and safeguards

Report anatomy
--------------
Each report is a plain dict (JSON-serializable) with the following keys:

  report_type          str    identifier
  overall_severity     str    max severity across all entries
  health_score         int    0-100 (higher = healthier)
  health_label         str    EXCELLENT / GOOD / FAIR / POOR
  sections             dict   {name: section_dict}
  executive_commentary str    deterministic narrative overview
  trend_summary        list   trend strings vs previous report
  recommendations      list   deduplicated action items
  top_findings         list   highest-severity entries (capped)
  changes              list   severity / score changes vs previous
  row_count            int    number of outcome rows analysed
  quality              dict   {score, label} confidence in the report itself

Severity levels
---------------
  INFO     nominal — no action required
  WATCH    soft concern — monitor closely
  WARNING  action may be required
  CRITICAL immediate attention — escalate
"""
import logging
from typing import Optional

log = logging.getLogger(__name__)

# ── Severity ──────────────────────────────────────────────────────────────────

SEV_INFO     = "INFO"
SEV_WATCH    = "WATCH"
SEV_WARNING  = "WARNING"
SEV_CRITICAL = "CRITICAL"

SEV_ORDER: dict = {SEV_INFO: 0, SEV_WATCH: 1, SEV_WARNING: 2, SEV_CRITICAL: 3}

# Penalty per entry when computing health_score (deducted from 100)
SEV_PENALTY: dict = {SEV_INFO: 0, SEV_WATCH: 5, SEV_WARNING: 15, SEV_CRITICAL: 30}

# ── Health labels ─────────────────────────────────────────────────────────────

HEALTH_EXCELLENT = "EXCELLENT"
HEALTH_GOOD      = "GOOD"
HEALTH_FAIR      = "FAIR"
HEALTH_POOR      = "POOR"

HEALTH_EXCELLENT_THRESHOLD: int = 90
HEALTH_GOOD_THRESHOLD:      int = 70
HEALTH_FAIR_THRESHOLD:      int = 50

# ── Report size limits ────────────────────────────────────────────────────────

MAX_ENTRIES_PER_SECTION: int = 10
MAX_TOP_FINDINGS:        int = 8
MAX_RECOMMENDATIONS:     int = 10
MAX_TREND_ITEMS:         int = 6

# ── Metric thresholds ─────────────────────────────────────────────────────────

CAL_ECE_WARNING:          float = 0.20
CAL_ECE_WATCH:            float = 0.10
CAL_CORR_WARNING:         float = 0.0    # negative → WARNING
CAL_CORR_WATCH:           float = 0.30   # < 0.30 → WATCH

DRAWDOWN_WARNING:         float = 25.0
DRAWDOWN_WATCH:           float = 15.0
WIN_RATE_WARNING:         float = 40.0
WIN_RATE_WATCH:           float = 50.0
CUMRET_WARNING:           float = -5.0   # cumulative return < -5 % → WARNING
ANOMALY_HIGH_WARNING:     int   = 3      # > N high-severity anomalies → WARNING
ANOMALY_HIGH_WATCH:       int   = 1
UNSTABLE_SIGNALS_WARNING: int   = 3
UNSTABLE_SIGNALS_WATCH:   int   = 1
DRIFT_WARNING:            int   = 3
DRIFT_WATCH:              int   = 1
DECEPTIVE_WARNING:        int   = 3
DECEPTIVE_WATCH:          int   = 1
TREND_STABLE_DELTA:       int   = 5      # |Δscore| < 5 → stable


# ── Primitive builders ────────────────────────────────────────────────────────

def _entry(
    severity: str,
    category: str,
    title:    str,
    detail:   str,
    metric    = None,
) -> dict:
    return {
        "severity": severity,
        "category": category,
        "title":    title,
        "detail":   detail,
        "metric":   metric,
    }


def _section(name: str, entries: list, summary: str = "") -> dict:
    sev = (
        max(entries, key=lambda e: SEV_ORDER.get(e["severity"], 0))["severity"]
        if entries else SEV_INFO
    )
    return {
        "name":    name,
        "severity": sev,
        "entries": entries[:MAX_ENTRIES_PER_SECTION],
        "summary": summary,
    }


def _max_sev(entries: list) -> str:
    return (
        max(entries, key=lambda e: SEV_ORDER.get(e["severity"], 0))["severity"]
        if entries else SEV_INFO
    )


# ── Health scoring ────────────────────────────────────────────────────────────

def health_score(sections: dict) -> tuple:
    """Return (score: int, label: str) from a sections dict."""
    all_entries = [e for s in sections.values() for e in (s.get("entries") or [])]
    penalty     = sum(SEV_PENALTY.get(e.get("severity", SEV_INFO), 0)
                      for e in all_entries)
    score       = max(0, min(100, 100 - penalty))

    if score >= HEALTH_EXCELLENT_THRESHOLD:
        label = HEALTH_EXCELLENT
    elif score >= HEALTH_GOOD_THRESHOLD:
        label = HEALTH_GOOD
    elif score >= HEALTH_FAIR_THRESHOLD:
        label = HEALTH_FAIR
    else:
        label = HEALTH_POOR

    return score, label


def report_quality_score(report: dict) -> dict:
    """
    Estimate confidence in the report's findings (data completeness).

    Returns {"score": int, "label": str}.
    """
    sections = report.get("sections") or {}
    n_sec    = len(sections)
    if n_sec == 0:
        return {"score": 0, "label": HEALTH_POOR}

    all_entries = [e for s in sections.values() for e in (s.get("entries") or [])]
    n_total     = len(all_entries)
    if n_total == 0:
        return {"score": 0, "label": HEALTH_POOR}

    n_insufficient = sum(
        1 for e in all_entries
        if "insufficient" in (e.get("title") or "").lower()
        or "no data" in (e.get("detail") or "").lower()
    )

    data_quality     = max(0.0, 1.0 - n_insufficient / n_total) * 100.0
    section_coverage = min(100.0, n_sec / 5.0 * 100.0)

    score = int(data_quality * 0.7 + section_coverage * 0.3)
    if score >= HEALTH_EXCELLENT_THRESHOLD:
        label = HEALTH_EXCELLENT
    elif score >= HEALTH_GOOD_THRESHOLD:
        label = HEALTH_GOOD
    elif score >= HEALTH_FAIR_THRESHOLD:
        label = HEALTH_FAIR
    else:
        label = HEALTH_POOR

    return {"score": score, "label": label}


# ── Report severity helper ────────────────────────────────────────────────────

def _report_severity(sections: dict) -> str:
    if not sections:
        return SEV_INFO
    return max(
        (s.get("severity", SEV_INFO) for s in sections.values()),
        key=lambda s: SEV_ORDER.get(s, 0),
    )


# ── Top findings collector ────────────────────────────────────────────────────

def _top_findings(sections: dict) -> list:
    all_entries = [e for s in sections.values() for e in (s.get("entries") or [])]
    return sorted(
        all_entries,
        key=lambda e: (-SEV_ORDER.get(e.get("severity", SEV_INFO), 0),
                       e.get("title", "")),
    )[:MAX_TOP_FINDINGS]


# ── Recommendations collector ─────────────────────────────────────────────────

def _collect_all_recommendations(sections: dict) -> list:
    recs = []
    seen = set()
    for s in sections.values():
        for e in (s.get("entries") or []):
            r = e.get("recommendation")
            if r and r not in seen:
                seen.add(r)
                recs.append(r)
    return recs[:MAX_RECOMMENDATIONS]


def _severity_to_rec(severity: str, section: str) -> Optional[str]:
    """Generate a default recommendation string from severity + section."""
    if severity == SEV_CRITICAL:
        return f"ESCALATE {section.upper()}: immediate review required"
    if severity == SEV_WARNING:
        return f"REVIEW {section.upper()}: action may be needed"
    if severity == SEV_WATCH:
        return f"MONITOR {section.upper()}: watch for further deterioration"
    return None


# ── Executive commentary ──────────────────────────────────────────────────────

def _executive_commentary(
    report_type: str,
    sections:    dict,
    score:       int,
    label:       str,
    overall_sev: str,
) -> str:
    """Build a deterministic human-readable narrative overview."""
    title = report_type.replace("_", " ").title()
    lines = [
        f"{title} — System {label}",
        f"Health score {score}/100  |  Severity: {overall_sev}",
    ]

    if not sections:
        lines.append("No subsystem data available.")
        return "\n".join(lines)

    # Worst section (highest severity)
    worst = max(sections.values(),
                key=lambda s: SEV_ORDER.get(s.get("severity", SEV_INFO), 0))
    # Best sections (all INFO)
    good  = [s for s in sections.values() if s.get("severity") == SEV_INFO]

    if worst.get("severity") != SEV_INFO:
        lines.append(
            f"Primary concern: {worst['name']} ({worst['severity']}) — "
            f"{worst.get('summary', '—')}"
        )

    if good:
        names = ", ".join(s["name"] for s in sorted(good, key=lambda s: s["name"])[:3])
        lines.append(f"Subsystems operating normally: {names}")

    # Summarise top finding
    top = _top_findings(sections)
    if top:
        t = top[0]
        if SEV_ORDER.get(t.get("severity"), 0) >= SEV_ORDER[SEV_WARNING]:
            lines.append(f"Top finding [{t['severity']}]: {t['title']} — {t['detail']}")

    return "\n".join(lines)


# ── Trend detection ───────────────────────────────────────────────────────────

def detect_trends(sections: dict, previous_report: Optional[dict]) -> list:
    """
    Compare current sections against a previous report and return trend strings.

    Each string describes whether a section is improving, worsening, or stable.
    Capped at MAX_TREND_ITEMS.
    """
    if not previous_report:
        return []

    prev_sections = previous_report.get("sections") or {}
    trends        = []

    for name, curr_s in sorted(sections.items()):
        prev_s = prev_sections.get(name)
        if not prev_s:
            continue
        curr_o = SEV_ORDER.get(curr_s.get("severity", SEV_INFO), 0)
        prev_o = SEV_ORDER.get(prev_s.get("severity", SEV_INFO), 0)

        if curr_o > prev_o:
            trends.append(
                f"{name}: worsening "
                f"({prev_s.get('severity', '?')} → {curr_s.get('severity', '?')})"
            )
        elif curr_o < prev_o:
            trends.append(
                f"{name}: improving "
                f"({prev_s.get('severity', '?')} → {curr_s.get('severity', '?')})"
            )
        # stable: no entry to keep output bounded

    return trends[:MAX_TREND_ITEMS]


def compare_report_changes(
    score:           int,
    overall_sev:     str,
    previous_report: dict,
) -> list:
    """
    Produce a list of change-event dicts comparing current metrics to a previous
    report snapshot.
    """
    changes = []
    prev_score = previous_report.get("health_score") or 100
    prev_sev   = previous_report.get("overall_severity") or SEV_INFO

    delta = score - prev_score
    if abs(delta) >= TREND_STABLE_DELTA:
        direction = "IMPROVED" if delta > 0 else "WORSENED"
        changes.append({
            "type":      "HEALTH_SCORE_CHANGE",
            "from":      prev_score,
            "to":        score,
            "delta":     delta,
            "direction": direction,
        })

    curr_o = SEV_ORDER.get(overall_sev, 0)
    prev_o = SEV_ORDER.get(prev_sev,    0)
    if curr_o != prev_o:
        direction = "ESCALATED" if curr_o > prev_o else "DE_ESCALATED"
        changes.append({
            "type":      "SEVERITY_CHANGE",
            "from":      prev_sev,
            "to":        overall_sev,
            "direction": direction,
        })

    return changes


# ── Report assembler ──────────────────────────────────────────────────────────

def _assemble(
    report_type:     str,
    sections:        dict,
    row_count:       int             = 0,
    previous_report: Optional[dict] = None,
    extra_recs:      Optional[list] = None,
) -> dict:
    """Assemble a full report dict from sections and optional previous snapshot."""
    score, label  = health_score(sections)
    overall_sev   = _report_severity(sections)
    top           = _top_findings(sections)
    internal_recs = _collect_all_recommendations(sections)

    # Add section-level default recommendations for non-INFO sections
    section_recs = []
    for name, s in sorted(sections.items()):
        rec = _severity_to_rec(s.get("severity", SEV_INFO), name)
        if rec:
            section_recs.append(rec)

    all_recs = list(dict.fromkeys(internal_recs + (extra_recs or []) + section_recs))

    commentary = _executive_commentary(report_type, sections, score, label, overall_sev)
    trends     = detect_trends(sections, previous_report)
    changes    = compare_report_changes(score, overall_sev, previous_report) \
                 if previous_report else []
    quality    = report_quality_score({"sections": sections})

    log.info(
        "research_reports: assembled '%s' — score=%d (%s) severity=%s sections=%d",
        report_type, score, label, overall_sev, len(sections),
    )
    if overall_sev in (SEV_CRITICAL, SEV_WARNING):
        log.warning(
            "research_reports: '%s' at %s — top finding: %s",
            report_type,
            overall_sev,
            top[0].get("title", "?") if top else "none",
        )

    return {
        "report_type":          report_type,
        "overall_severity":     overall_sev,
        "health_score":         score,
        "health_label":         label,
        "sections":             sections,
        "executive_commentary": commentary,
        "trend_summary":        trends[:MAX_TREND_ITEMS],
        "recommendations":      all_recs[:MAX_RECOMMENDATIONS],
        "top_findings":         top[:MAX_TOP_FINDINGS],
        "changes":              changes,
        "row_count":            row_count,
        "quality":              quality,
    }


# ── Section builders ──────────────────────────────────────────────────────────

def _build_calibration_section(conf_report: dict) -> dict:
    entries = []
    cal     = conf_report.get("calibration") or {}
    quality = cal.get("quality") or "INSUFFICIENT_DATA"
    ece     = cal.get("ece")
    corr    = cal.get("correlation")
    oconf   = conf_report.get("overconfidence_flags") or []
    warns   = conf_report.get("warnings") or []

    if quality == "POOR":
        entries.append(_entry(SEV_WARNING, "calibration", "Calibration quality POOR",
                              "Confidence scores have poor calibration — "
                              "signal confidence values are not trustworthy",
                              quality))
    elif quality == "FAIR":
        entries.append(_entry(SEV_WATCH, "calibration", "Calibration quality FAIR",
                              "Moderate calibration — confidence scores have limited accuracy",
                              quality))
    elif quality == "GOOD":
        entries.append(_entry(SEV_INFO, "calibration", "Calibration quality GOOD",
                              "Confidence scores are well-calibrated",
                              quality))
    else:
        entries.append(_entry(SEV_INFO, "calibration", "Insufficient calibration data",
                              "Not enough outcomes to assess calibration quality",
                              quality))

    if ece is not None:
        if ece > CAL_ECE_WARNING:
            entries.append(_entry(SEV_WARNING, "calibration",
                                  f"High ECE {ece:.3f}",
                                  f"Expected Calibration Error {ece:.3f} exceeds "
                                  f"WARNING threshold {CAL_ECE_WARNING}",
                                  ece))
        elif ece > CAL_ECE_WATCH:
            entries.append(_entry(SEV_WATCH, "calibration",
                                  f"Moderate ECE {ece:.3f}",
                                  f"ECE {ece:.3f} is above WATCH threshold {CAL_ECE_WATCH}",
                                  ece))
        else:
            entries.append(_entry(SEV_INFO, "calibration",
                                  f"ECE within range {ece:.3f}",
                                  f"ECE {ece:.3f} is acceptable",
                                  ece))

    if corr is not None:
        if corr < CAL_CORR_WARNING:
            entries.append(_entry(SEV_WARNING, "calibration",
                                  f"Negative confidence-return correlation {corr:.3f}",
                                  f"Higher confidence does NOT predict higher returns ({corr:.3f})",
                                  corr))
        elif corr < CAL_CORR_WATCH:
            entries.append(_entry(SEV_WATCH, "calibration",
                                  f"Weak confidence-return correlation {corr:.3f}",
                                  f"Confidence–return alignment is weak ({corr:.3f} < {CAL_CORR_WATCH})",
                                  corr))
        else:
            entries.append(_entry(SEV_INFO, "calibration",
                                  f"Positive confidence-return correlation {corr:.3f}",
                                  "Higher confidence reliably predicts higher returns",
                                  corr))

    if oconf:
        entries.append(_entry(SEV_WARNING, "calibration",
                              f"{len(oconf)} overconfidence flag(s)",
                              f"{len(oconf)} confidence bucket(s) overconfident — "
                              "win rate below expectation",
                              len(oconf)))

    for w in warns:
        if "[HIGH]" in w:
            entries.append(_entry(SEV_WARNING, "calibration", "Calibration high warning", w))

    summary = f"quality={quality}" + (f", ECE={ece:.3f}" if ece is not None else "")
    return _section("calibration", entries, summary)


def _build_regime_section(regime_report: dict) -> dict:
    entries = []
    inv      = regime_report.get("inversion") or {}
    inv_count = inv.get("inversion_count") or 0
    strongest = (regime_report.get("strongest_regime") or {}).get("regime")
    weakest   = (regime_report.get("weakest_regime")   or {}).get("regime")
    warns     = regime_report.get("warnings") or []

    if inv_count > 0:
        entries.append(_entry(SEV_WARNING, "regime",
                              f"{inv_count} regime inversion(s)",
                              f"Lower-risk regime is outperforming higher-risk "
                              f"({inv_count} inversion(s) detected)",
                              inv_count))
    else:
        entries.append(_entry(SEV_INFO, "regime", "No regime inversions",
                              "Regime ordering is consistent: BULL > NEUTRAL > RISK_OFF",
                              0))

    if strongest and weakest:
        entries.append(_entry(SEV_INFO, "regime",
                              f"Strongest regime: {strongest}",
                              f"{strongest} leads in win rate; {weakest} is weakest",
                              strongest))

    for w in warns:
        sev = SEV_WARNING if "[HIGH]" in w else SEV_WATCH
        entries.append(_entry(sev, "regime", "Regime warning", w))

    summary = (f"inversions={inv_count}"
               + (f", strongest={strongest}" if strongest else "")
               + (f", weakest={weakest}"   if weakest   else ""))
    return _section("regime", entries, summary)


def _build_meta_section(meta_report: dict) -> dict:
    entries    = []
    safeguards = meta_report.get("safeguard_recommendations") or []
    degrad     = meta_report.get("degradation_events") or []
    inflate    = meta_report.get("inflation_events")   or []
    regime_ev  = meta_report.get("regime_events")      or []
    high_d     = [e for e in degrad if e.get("severity") == "HIGH"]
    strongest  = meta_report.get("strongest_window")
    weakest    = meta_report.get("weakest_window")
    warns      = meta_report.get("warnings") or []

    # Safeguard level
    rec_names = [r.get("recommendation") for r in safeguards if r.get("recommendation")]
    if "OBSERVATION_ONLY" in rec_names:
        entries.append(_entry(SEV_CRITICAL, "meta",
                              "OBSERVATION_ONLY safeguard active",
                              f"{len(degrad) + len(inflate) + len(regime_ev)} critical "
                              "performance events — adaptive scoring suspended",
                              "OBSERVATION_ONLY"))
    elif "PAUSE_ADAPTIVE_ROLLOUT" in rec_names:
        entries.append(_entry(SEV_WARNING, "meta",
                              "PAUSE_ADAPTIVE_ROLLOUT active",
                              "Multiple high-severity events — adaptive rollout paused",
                              "PAUSE_ADAPTIVE_ROLLOUT"))
    elif rec_names:
        entries.append(_entry(SEV_WATCH, "meta",
                              f"Safeguard: {rec_names[0]}",
                              f"Performance warnings triggered: {', '.join(rec_names)}",
                              rec_names[0]))
    else:
        entries.append(_entry(SEV_INFO, "meta", "No safeguards active",
                              "Rolling-window performance within normal bounds", None))

    if high_d:
        entries.append(_entry(SEV_WARNING, "meta",
                              f"{len(high_d)} HIGH degradation event(s)",
                              f"{len(high_d)} high-severity degradation event(s) detected "
                              "across rolling windows",
                              len(high_d)))
    elif degrad:
        entries.append(_entry(SEV_WATCH, "meta",
                              f"{len(degrad)} degradation event(s)",
                              "Rolling-window degradation detected — monitor closely",
                              len(degrad)))

    if inflate:
        entries.append(_entry(SEV_WATCH, "meta",
                              f"{len(inflate)} confidence inflation event(s)",
                              "Confidence rising while returns fall — potential overconfidence",
                              len(inflate)))

    if strongest and weakest and strongest != weakest:
        entries.append(_entry(SEV_INFO, "meta",
                              f"Strongest window: {strongest}-alert, weakest: {weakest}-alert",
                              "Recent performance diverges from longer-term baselines",
                              strongest))

    for w in warns:
        if "[HIGH]" in w or "SAFEGUARD" in w:
            entries.append(_entry(SEV_WARNING, "meta", "Meta-performance warning", w))

    summary = (f"safeguard={rec_names[0] if rec_names else 'NONE'}, "
               f"degrad_events={len(degrad)}, inflate={len(inflate)}")
    return _section("meta", entries, summary)


def _build_portfolio_section(portfolio_report: dict) -> dict:
    entries  = []
    metrics  = portfolio_report.get("metrics")       or {}
    ph       = portfolio_report.get("portfolio_health") or "INSUFFICIENT_DATA"
    wr       = metrics.get("win_rate")
    cum_ret  = metrics.get("cumulative_return_pct")
    max_dd   = metrics.get("max_drawdown_pct") or 0.0
    n_trades = metrics.get("n_trades")         or 0
    conc     = portfolio_report.get("concentration") or {}
    conc_w   = conc.get("warnings") or []
    rob      = portfolio_report.get("robustness") or {}
    rob_w    = rob.get("warnings") or []

    if ph == "WEAK":
        entries.append(_entry(SEV_WARNING, "portfolio", "Portfolio health WEAK",
                              f"Paper portfolio is in WEAK health state "
                              f"({n_trades} trades)", ph))
    elif ph == "CAUTION":
        entries.append(_entry(SEV_WATCH, "portfolio", "Portfolio health CAUTION",
                              "Portfolio shows CAUTION — review win rate and drawdown", ph))
    elif ph == "HEALTHY":
        entries.append(_entry(SEV_INFO, "portfolio", "Portfolio health HEALTHY",
                              f"Portfolio simulation healthy ({n_trades} trades)", ph))
    else:
        entries.append(_entry(SEV_INFO, "portfolio", "Insufficient portfolio data",
                              f"Not enough trades for reliable portfolio health ({n_trades})", ph))

    if max_dd > DRAWDOWN_WARNING:
        entries.append(_entry(SEV_WARNING, "portfolio",
                              f"Max drawdown {max_dd:.1f}%",
                              f"Portfolio drawdown {max_dd:.1f}% exceeds "
                              f"WARNING threshold {DRAWDOWN_WARNING}%",
                              max_dd))
    elif max_dd > DRAWDOWN_WATCH:
        entries.append(_entry(SEV_WATCH, "portfolio",
                              f"Elevated drawdown {max_dd:.1f}%",
                              f"Drawdown {max_dd:.1f}% above WATCH threshold {DRAWDOWN_WATCH}%",
                              max_dd))

    if wr is not None:
        if wr < WIN_RATE_WARNING:
            entries.append(_entry(SEV_WARNING, "portfolio",
                                  f"Low win rate {wr:.1f}%",
                                  f"Win rate {wr:.1f}% below WARNING threshold {WIN_RATE_WARNING}%",
                                  wr))
        elif wr < WIN_RATE_WATCH:
            entries.append(_entry(SEV_WATCH, "portfolio",
                                  f"Below-average win rate {wr:.1f}%",
                                  f"Win rate {wr:.1f}% below WATCH threshold {WIN_RATE_WATCH}%",
                                  wr))
        else:
            entries.append(_entry(SEV_INFO, "portfolio",
                                  f"Win rate {wr:.1f}%",
                                  "Win rate is above WATCH threshold", wr))

    if cum_ret is not None and cum_ret < CUMRET_WARNING:
        entries.append(_entry(SEV_WARNING, "portfolio",
                              f"Negative cumulative return {cum_ret:.1f}%",
                              f"Portfolio simulation has lost {abs(cum_ret):.1f}% "
                              "of initial capital",
                              cum_ret))

    for w in conc_w:
        entries.append(_entry(SEV_WATCH, "portfolio", "Concentration warning", w))

    for w in rob_w:
        entries.append(_entry(SEV_WATCH, "portfolio", "Robustness warning", w))

    summary = (f"health={ph}, n_trades={n_trades}"
               + (f", win_rate={wr:.1f}%" if wr is not None else "")
               + (f", drawdown={max_dd:.1f}%"))
    return _section("portfolio", entries, summary)


def _build_adaptive_section(weights_report: dict, observer_report: dict) -> dict:
    entries    = []
    w_summary  = weights_report.get("summary") or {}
    n_boosted  = len(w_summary.get("signals_boosted")   or [])
    n_penalized = len(w_summary.get("signals_penalized") or [])
    n_held     = len(w_summary.get("signals_held")      or [])

    o_summary    = observer_report.get("summary") or {}
    n_unstable   = o_summary.get("n_unstable")        or 0
    drift_count  = o_summary.get("drift_event_count") or 0
    has_drift    = o_summary.get("has_drift")          or False

    if n_unstable >= UNSTABLE_SIGNALS_WARNING:
        entries.append(_entry(SEV_WARNING, "adaptive",
                              f"{n_unstable} unstable signal(s)",
                              f"{n_unstable} signals showing unstable weight recommendations",
                              n_unstable))
    elif n_unstable >= UNSTABLE_SIGNALS_WATCH:
        entries.append(_entry(SEV_WATCH, "adaptive",
                              f"{n_unstable} unstable signal(s)",
                              f"{n_unstable} signal(s) have inconsistent weight recommendations",
                              n_unstable))
    else:
        entries.append(_entry(SEV_INFO, "adaptive", "Signal weights stable",
                              "All adaptive weight recommendations are stable", n_unstable))

    if drift_count >= DRIFT_WARNING:
        entries.append(_entry(SEV_WARNING, "adaptive",
                              f"{drift_count} drift event(s) detected",
                              "Adaptive weights are drifting — recommendations may be unstable",
                              drift_count))
    elif drift_count >= DRIFT_WATCH:
        entries.append(_entry(SEV_WATCH, "adaptive",
                              f"{drift_count} drift event(s)",
                              "Minor drift in adaptive recommendations", drift_count))

    entries.append(_entry(SEV_INFO, "adaptive",
                          f"Boosted: {n_boosted}, penalised: {n_penalized}, held: {n_held}",
                          "Current weight adjustment distribution",
                          {"boosted": n_boosted, "penalized": n_penalized, "held": n_held}))

    summary = (f"unstable={n_unstable}, drift={drift_count}, "
               f"boosted={n_boosted}, penalized={n_penalized}")
    return _section("adaptive", entries, summary)


def _build_anomaly_section(audit_batch: dict) -> dict:
    entries     = []
    n_high      = audit_batch.get("high_severity_count") or 0
    anomaly_sum = audit_batch.get("anomaly_summary")     or {}
    n_total     = sum(anomaly_sum.values())
    n_snapshots = audit_batch.get("count")               or 0
    tier_bd     = audit_batch.get("tier_breakdown")      or {}

    if n_high > ANOMALY_HIGH_WARNING:
        entries.append(_entry(SEV_WARNING, "audit",
                              f"{n_high} high-severity anomaly(ies)",
                              f"{n_high} high-severity anomalies detected across "
                              f"{n_snapshots} decision snapshots",
                              n_high))
    elif n_high >= ANOMALY_HIGH_WATCH:
        entries.append(_entry(SEV_WATCH, "audit",
                              f"{n_high} high-severity anomaly(ies)",
                              f"Low count of high-severity anomalies — monitor",
                              n_high))
    else:
        entries.append(_entry(SEV_INFO, "audit", "No high-severity anomalies",
                              f"{n_snapshots} snapshot(s) reviewed — no critical issues",
                              n_high))

    if anomaly_sum:
        top_anomaly = max(anomaly_sum, key=anomaly_sum.get)
        entries.append(_entry(SEV_WATCH if n_total > 5 else SEV_INFO, "audit",
                              f"Most common anomaly: {top_anomaly} ({anomaly_sum[top_anomaly]}×)",
                              f"Total anomaly count: {n_total} across {len(anomaly_sum)} types",
                              n_total))

    if tier_bd:
        conviction_n = tier_bd.get("CONVICTION") or 0
        entries.append(_entry(SEV_INFO, "audit",
                              f"Tier breakdown: {dict(sorted(tier_bd.items()))}",
                              f"CONVICTION alerts: {conviction_n}",
                              tier_bd))

    summary = f"high_severity={n_high}, total_anomalies={n_total}, snapshots={n_snapshots}"
    return _section("audit", entries, summary)


def _build_degradation_section(meta_report: dict) -> dict:
    entries   = []
    degrad    = meta_report.get("degradation_events") or []
    inflate   = meta_report.get("inflation_events")   or []
    regime_ev = meta_report.get("regime_events")      or []
    safeguards = meta_report.get("safeguard_recommendations") or []
    rec_names  = [r.get("recommendation") for r in safeguards if r.get("recommendation")]

    high_d = [e for e in degrad if e.get("severity") == "HIGH"]

    if high_d:
        entries.append(_entry(SEV_WARNING, "degradation",
                              f"{len(high_d)} HIGH-severity degradation event(s)",
                              "; ".join(e.get("detail", e.get("type", "")) for e in high_d[:3]),
                              len(high_d)))
    elif degrad:
        entries.append(_entry(SEV_WATCH, "degradation",
                              f"{len(degrad)} degradation event(s)",
                              "Performance declining vs prior windows — continue monitoring",
                              len(degrad)))
    else:
        entries.append(_entry(SEV_INFO, "degradation", "No degradation detected",
                              "Rolling-window performance is stable", 0))

    if inflate:
        entries.append(_entry(SEV_WATCH, "degradation",
                              f"{len(inflate)} confidence inflation event(s)",
                              "Confidence rising while returns declining",
                              len(inflate)))

    if regime_ev:
        entries.append(_entry(SEV_WATCH, "degradation",
                              f"{len(regime_ev)} regime performance event(s)",
                              "Recent regime-specific performance is deteriorating",
                              len(regime_ev)))

    if "OBSERVATION_ONLY" in rec_names:
        entries.append(_entry(SEV_CRITICAL, "degradation",
                              "OBSERVATION_ONLY safeguard active",
                              "Degradation is severe — all adaptive changes suspended",
                              "OBSERVATION_ONLY"))
    elif "PAUSE_ADAPTIVE_ROLLOUT" in rec_names:
        entries.append(_entry(SEV_WARNING, "degradation",
                              "PAUSE_ADAPTIVE_ROLLOUT active",
                              "Degradation requires pausing adaptive weight rollout",
                              "PAUSE_ADAPTIVE_ROLLOUT"))

    summary = (f"degrad_events={len(degrad)}, "
               f"high={len(high_d)}, inflate={len(inflate)}, "
               f"safeguard={rec_names[0] if rec_names else 'NONE'}")
    return _section("degradation", entries, summary)


def _build_replay_section(replay_report: dict) -> dict:
    entries = []
    base    = replay_report.get("baseline") or {}
    b_wr    = base.get("win_rate")
    b_n     = base.get("n") or 0
    cf      = replay_report.get("counterfactuals") or {}
    rob     = replay_report.get("robustness_warnings") or []
    sens    = replay_report.get("sensitivity") or {}
    wr_range = sens.get("win_rate_range")

    entries.append(_entry(SEV_INFO, "replay",
                          f"Baseline: {b_n} alerts"
                          + (f", win_rate={b_wr:.1f}%" if b_wr is not None else ""),
                          "Strategy replay baseline statistics",
                          b_wr))

    if wr_range is not None and wr_range > 20.0:
        entries.append(_entry(SEV_WATCH, "replay",
                              f"High sensitivity: win rate range {wr_range:.1f}pp",
                              "Strategy performance changes significantly with parameter variation",
                              wr_range))
    elif wr_range is not None:
        entries.append(_entry(SEV_INFO, "replay",
                              f"Sensitivity: win rate range {wr_range:.1f}pp",
                              "Strategy is relatively robust to parameter changes",
                              wr_range))

    for w in rob:
        sev = SEV_WARNING if w.get("severity") == "HIGH" else SEV_WATCH
        entries.append(_entry(sev, "replay",
                              f"Robustness: {w.get('type', '?')}",
                              w.get("detail", ""),
                              w.get("n")))

    summary = (f"baseline_n={b_n}"
               + (f", win_rate={b_wr:.1f}%" if b_wr is not None else "")
               + (f", win_rate_range={wr_range:.1f}pp" if wr_range is not None else ""))
    return _section("replay", entries, summary)


# ── Public report builders ────────────────────────────────────────────────────

def daily_operational_report(
    hub_report:       Optional[dict] = None,
    conf_report:      Optional[dict] = None,
    regime_report:    Optional[dict] = None,
    meta_report:      Optional[dict] = None,
    portfolio_report: Optional[dict] = None,
    previous_report:  Optional[dict] = None,
    row_count:        int = 0,
) -> dict:
    """Current operational state across all subsystems."""
    sections: dict = {}

    if conf_report:
        sections["calibration"] = _build_calibration_section(conf_report)
    if regime_report:
        sections["regime"]      = _build_regime_section(regime_report)
    if meta_report:
        sections["meta"]        = _build_meta_section(meta_report)
    if portfolio_report:
        sections["portfolio"]   = _build_portfolio_section(portfolio_report)

    # Derive row_count from any available report
    if not row_count:
        for r in (conf_report, regime_report, meta_report, portfolio_report):
            if r and r.get("row_count"):
                row_count = r["row_count"]
                break

    # Add hub-level section if provided
    if hub_report:
        n_alerts     = len(hub_report.get("operational_alerts") or [])
        hub_health   = hub_report.get("overall_health") or "HEALTHY"
        hub_sev      = SEV_CRITICAL if hub_health == "CRITICAL" else \
                       SEV_WARNING  if hub_health == "DEGRADED"  else \
                       SEV_WATCH    if hub_health == "WATCH"      else SEV_INFO
        hub_entries  = [_entry(hub_sev, "hub", f"Hub status: {hub_health}",
                               f"{n_alerts} operational alert(s)", n_alerts)]
        for a in (hub_report.get("operational_alerts") or [])[:3]:
            sev = SEV_CRITICAL if "[CRITICAL]" in a else \
                  SEV_WARNING  if "[HIGH]"     in a else SEV_WATCH
            hub_entries.append(_entry(sev, "hub", a[:80], a))
        sections["hub"] = _section("hub", hub_entries,
                                   f"status={hub_health}, alerts={n_alerts}")

    recs = list(hub_report.get("recommendations") or []) if hub_report else []
    return _assemble("daily_operational_report", sections,
                     row_count=row_count,
                     previous_report=previous_report,
                     extra_recs=recs)


def weekly_performance_report(
    meta_report:      Optional[dict] = None,
    portfolio_report: Optional[dict] = None,
    conf_report:      Optional[dict] = None,
    replay_report:    Optional[dict] = None,
    previous_report:  Optional[dict] = None,
    row_count:        int = 0,
) -> dict:
    """Compounded performance lens for periodic (weekly) review."""
    sections: dict = {}

    if meta_report:
        sections["degradation"]  = _build_degradation_section(meta_report)
    if portfolio_report:
        sections["portfolio"]    = _build_portfolio_section(portfolio_report)
    if conf_report:
        sections["calibration"]  = _build_calibration_section(conf_report)
    if replay_report:
        sections["replay"]       = _build_replay_section(replay_report)

    if not row_count:
        for r in (meta_report, portfolio_report, conf_report):
            if r and r.get("row_count"):
                row_count = r["row_count"]
                break

    return _assemble("weekly_performance_report", sections,
                     row_count=row_count, previous_report=previous_report)


def calibration_research_report(
    conf_report:     Optional[dict] = None,
    previous_report: Optional[dict] = None,
) -> dict:
    """Deep-dive calibration quality report."""
    sections: dict = {}
    if conf_report:
        sections["calibration"] = _build_calibration_section(conf_report)

    rc = (conf_report or {}).get("row_count") or 0
    return _assemble("calibration_research_report", sections,
                     row_count=rc, previous_report=previous_report)


def regime_research_report(
    regime_report:   Optional[dict] = None,
    previous_report: Optional[dict] = None,
) -> dict:
    """Regime effectiveness report."""
    sections: dict = {}
    if regime_report:
        sections["regime"] = _build_regime_section(regime_report)

    rc = (regime_report or {}).get("row_count") or 0
    return _assemble("regime_research_report", sections,
                     row_count=rc, previous_report=previous_report)


def portfolio_research_report(
    portfolio_report: Optional[dict] = None,
    replay_report:    Optional[dict] = None,
    previous_report:  Optional[dict] = None,
) -> dict:
    """Paper portfolio health and replay counterfactual report."""
    sections: dict = {}
    if portfolio_report:
        sections["portfolio"] = _build_portfolio_section(portfolio_report)
    if replay_report:
        sections["replay"]    = _build_replay_section(replay_report)

    rc = (portfolio_report or {}).get("row_count") or 0
    return _assemble("portfolio_research_report", sections,
                     row_count=rc, previous_report=previous_report)


def adaptive_recommendation_report(
    weights_report:  Optional[dict] = None,
    observer_report: Optional[dict] = None,
    previous_report: Optional[dict] = None,
) -> dict:
    """Adaptive weight stability and drift report."""
    sections: dict = {}
    wr = weights_report  or {}
    ob = observer_report or {}
    if wr or ob:
        sections["adaptive"] = _build_adaptive_section(wr, ob)

    rc = wr.get("row_count") or 0
    return _assemble("adaptive_recommendation_report", sections,
                     row_count=rc, previous_report=previous_report)


def anomaly_research_report(
    audit_batch:     Optional[dict] = None,
    previous_report: Optional[dict] = None,
) -> dict:
    """Decision audit anomaly counts and patterns report."""
    sections: dict = {}
    if audit_batch:
        sections["audit"] = _build_anomaly_section(audit_batch)

    return _assemble("anomaly_research_report", sections,
                     row_count=0, previous_report=previous_report)


def degradation_research_report(
    meta_report:     Optional[dict] = None,
    previous_report: Optional[dict] = None,
) -> dict:
    """Rolling-window degradation and safeguard ladder report."""
    sections: dict = {}
    if meta_report:
        sections["degradation"] = _build_degradation_section(meta_report)

    rc = (meta_report or {}).get("row_count") or 0
    return _assemble("degradation_research_report", sections,
                     row_count=rc, previous_report=previous_report)
