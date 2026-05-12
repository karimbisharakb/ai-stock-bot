"""
Decision audit and explainability layer for the Predator scanner.
Phase 3A — read-only; does NOT modify live scoring logic.

Every scored ticker can be wrapped in an audit snapshot that makes the
decision fully explainable: raw inputs → lineage chain → anomaly flags →
plain-English summary.

Scoring pipeline (for reference — this module observes, never drives it)
------------------------------------------------------------------------
  raw_score          = Σ signal contributions  (max 12, capped at 10)
  confidence_raw     = quality_component × 0.70 + convergence × 0.30
  confidence         = calibrate_confidence(raw, signals)
                       — correlation_penalty, conflict_penalty, agreement_boost
  confidence         = apply_regime_penalty(confidence, signals, regime)
                       — NEUTRAL ×0.90 | RISK_OFF ×0.75 + suppress breakout
  adjusted_score     = raw_score × confidence / 100
  tier               = classify_tier(raw_score, adjusted_score, confidence, active_sigs)

Tiers
-----
  CONVICTION  — active_sigs ≥ 3 AND confidence ≥ 55 AND raw_score ≥ 6
  ALERT       — adjusted_score ≥ 2.5 AND raw_score ≥ 6
  WATCH       — everything else
"""
import json
import logging
from typing import Optional

log = logging.getLogger(__name__)

# ── Tier labels and thresholds (mirrors predator.py — kept in sync manually) ──
TIER_WATCH      = "WATCH"
TIER_ALERT      = "ALERT"
TIER_CONVICTION = "CONVICTION"

ALERT_THRESHOLD:           int   = 6
ALERT_MIN_ADJUSTED:        float = 2.5
CONVICTION_MIN_SIGNALS:    int   = 3
CONVICTION_MIN_CONFIDENCE: float = 55.0

# ── Signal universe (mirrors predator.py) ─────────────────────────────────────
SIGNAL_NAMES: tuple = (
    "options", "insider", "short_squeeze", "catalyst", "institutional", "breakout"
)
SIGNAL_MAX_SCORES: dict = {
    "options": 3, "insider": 2, "short_squeeze": 2,
    "catalyst": 2, "institutional": 1, "breakout": 2,
}

# ── Anomaly detection thresholds ──────────────────────────────────────────────
ANOMALY_HIGH_CONF_THRESHOLD:    float = 65.0  # ≥ this = "high confidence"
ANOMALY_LOW_SCORE_FOR_HIGH_CONF: int  = 6     # raw_score < this = suspicious with high conf
ANOMALY_HIGH_SCORE_THRESHOLD:   int   = 8     # ≥ this = "high score"
ANOMALY_LOW_CONF_FOR_HIGH_SCORE: float = 35.0 # < this = suspicious with high score
ANOMALY_SUPPRESSED_THRESHOLD:   int   = 1     # ≥ this many suppressed signals = flag

# ── Lineage step labels ───────────────────────────────────────────────────────
STEP_SIGNAL_SUM        = "SIGNAL_SUM"
STEP_SCORE_CAP         = "SCORE_CAP"
STEP_CALIBRATION       = "CALIBRATION"
STEP_REGIME_PENALTY    = "REGIME_PENALTY"
STEP_ADJUSTED_SCORE    = "ADJUSTED_SCORE"
STEP_TIER              = "TIER_CLASSIFICATION"

# ── Regime penalty factors (mirrors market_regime.py) ────────────────────────
_REGIME_FACTORS: dict = {
    "BULL":     1.00,
    "NEUTRAL":  0.90,
    "RISK_OFF": 0.75,
}


# ── Snapshot builder ──────────────────────────────────────────────────────────

def build_audit_snapshot(
    ticker:                  str,
    timestamp:               str,
    raw_score:               float,
    adjusted_score:          float,
    confidence_pct:          float,
    regime:                  Optional[str],
    tier:                    Optional[str],
    signal_contributions:    Optional[dict] = None,
    *,
    suppressed_signals:      Optional[list] = None,
    original_contributions:  Optional[dict] = None,
    penalties:               Optional[list] = None,
    boosts:                  Optional[list] = None,
    calibration_adjustments: Optional[list] = None,
    adaptive_recommendations: Optional[dict] = None,
    combo_effects:           Optional[dict] = None,
) -> dict:
    """
    Build a complete, serialisable audit snapshot for one scoring decision.

    Parameters
    ----------
    ticker                  : ticker symbol
    timestamp               : ISO-8601 alert timestamp
    raw_score               : sum of all signal contributions (may exceed 10)
    adjusted_score          : raw_score × confidence / 100 (post-cap)
    confidence_pct          : final calibrated confidence in [0, 100]
    regime                  : BULL / NEUTRAL / RISK_OFF / None
    tier                    : WATCH / ALERT / CONVICTION / None
    signal_contributions    : {signal_name: score_after_suppression}
    suppressed_signals      : signal names zeroed by regime (optional)
    original_contributions  : {signal_name: score_before_suppression} (optional)
    penalties               : [{"type", "factor", "reason"}] (optional)
    boosts                  : [{"type", "delta", "reason"}] (optional)
    calibration_adjustments : [{"type", "delta", "reason"}] (optional)
    adaptive_recommendations: {"active": bool, "adjustments": {}} (optional)
    combo_effects           : {"active_combo", "historical_win_rate", ...} (optional)

    Returns
    -------
    Flat dict suitable for JSON serialisation.  All list/dict fields default
    to empty rather than None so downstream code can always iterate safely.
    """
    contributions = dict(signal_contributions) if signal_contributions else {}
    active_count  = sum(1 for v in contributions.values() if (v or 0) > 0)

    snapshot = {
        # Core identity
        "ticker":                  ticker,
        "timestamp":               timestamp,
        # Scores
        "raw_score":               raw_score,
        "adjusted_score":          adjusted_score,
        "confidence_pct":          confidence_pct,
        # Classification
        "regime":                  regime,
        "tier":                    tier,
        # Signal detail
        "signal_contributions":    contributions,
        "active_signal_count":     active_count,
        "suppressed_signals":      list(suppressed_signals) if suppressed_signals else [],
        "original_contributions":  dict(original_contributions) if original_contributions else {},
        # Adjustments
        "penalties":               list(penalties)               if penalties               else [],
        "boosts":                  list(boosts)                  if boosts                  else [],
        "calibration_adjustments": list(calibration_adjustments) if calibration_adjustments else [],
        # Optional enrichment
        "adaptive_recommendations": adaptive_recommendations or {"active": False, "adjustments": {}},
        "combo_effects":            combo_effects,
    }
    log.debug(
        "decision_audit: snapshot built for %s tier=%s raw=%.1f adj=%.2f conf=%.1f%%",
        ticker, tier, raw_score, adjusted_score, confidence_pct,
    )
    return snapshot


def snapshot_from_db_row(row: dict) -> dict:
    """
    Convenience builder: construct an audit snapshot from a predator_alerts DB row.

    Expected row keys (all optional — missing keys default gracefully):
        ticker, alert_time, raw_score, adjusted_score, confidence_pct,
        tier, score_options, score_insider, score_short_squeeze,
        score_catalyst, score_institutional, score_breakout
    """
    contributions = {
        sig: float(row.get(f"score_{sig}") or 0)
        for sig in SIGNAL_NAMES
    }
    return build_audit_snapshot(
        ticker           = str(row.get("ticker") or ""),
        timestamp        = str(row.get("alert_time") or ""),
        raw_score        = float(row.get("raw_score") or 0),
        adjusted_score   = float(row.get("adjusted_score") or 0),
        confidence_pct   = float(row.get("confidence_pct") or 0),
        regime           = row.get("regime"),
        tier             = row.get("tier"),
        signal_contributions = contributions,
    )


# ── Score-lineage reconstruction ──────────────────────────────────────────────

def _lineage_step(step: str, value_in, value_out, detail: str) -> dict:
    """Build one lineage step dict with a computed delta."""
    delta = None
    if isinstance(value_in, (int, float)) and isinstance(value_out, (int, float)):
        delta = round(value_out - value_in, 4)
    return {
        "step":       step,
        "value_in":   value_in,
        "value_out":  value_out,
        "delta":      delta,
        "detail":     detail,
    }


def reconstruct_lineage(snapshot: dict) -> dict:
    """
    Reconstruct the score transformation chain from an audit snapshot.

    Works best-effort: if intermediate values are not in the snapshot the
    steps still appear but with value_in=None.

    Returns
    -------
    {
        "steps":          [ {step, value_in, value_out, delta, detail}, ... ],
        "raw_score":      float,
        "capped_score":   float,   # min(raw_score, 10)
        "final_confidence": float,
        "adjusted_score": float,
        "tier":           str,
        "tier_threshold_crossed": str | None,  # which threshold the adjusted score cleared
    }
    """
    raw       = snapshot.get("raw_score") or 0.0
    conf      = snapshot.get("confidence_pct") or 0.0
    adj       = snapshot.get("adjusted_score") or 0.0
    tier      = snapshot.get("tier") or TIER_WATCH
    regime    = snapshot.get("regime") or "BULL"
    contrib   = snapshot.get("signal_contributions") or {}
    cal_adjs  = snapshot.get("calibration_adjustments") or []
    penalties = snapshot.get("penalties") or []
    boosts    = snapshot.get("boosts") or []

    capped = min(raw, 10.0)
    steps  = []

    # ── 1. Signal aggregation ─────────────────────────────────────────────────
    total_contrib = sum((v or 0) for v in contrib.values())
    active_parts  = [
        f"{sig}+{v:.0f}"
        for sig, v in sorted(contrib.items(), key=lambda kv: -(kv[1] or 0))
        if (v or 0) > 0
    ] or ["no active signals"]
    steps.append(_lineage_step(
        STEP_SIGNAL_SUM,
        value_in  = None,
        value_out = raw,
        detail    = "Σ signal contributions: " + ", ".join(active_parts),
    ))

    # ── 2. Score cap (only if raw_score was uncapped > 10) ────────────────────
    if raw > 10.0:
        steps.append(_lineage_step(
            STEP_SCORE_CAP,
            value_in  = raw,
            value_out = 10.0,
            detail    = f"Capped at 10 (raw={raw:.1f})",
        ))

    # ── 3. Calibration adjustments (in order recorded) ────────────────────────
    cal_delta_total = sum(item.get("delta") or 0 for item in cal_adjs)
    boost_delta     = sum(item.get("delta") or 0 for item in boosts)
    total_cal       = cal_delta_total + boost_delta

    if cal_adjs or boosts:
        all_cal = cal_adjs + boosts
        reasons = "; ".join(
            f"{a.get('type', '?')} {a.get('delta', 0):+.1f}"
            for a in all_cal if a.get("delta") is not None
        ) or "adjustments applied"
        # Estimate pre-calibration confidence as conf - total_cal
        pre_cal = round(conf - total_cal, 2)
        steps.append(_lineage_step(
            STEP_CALIBRATION,
            value_in  = pre_cal,
            value_out = conf,
            detail    = f"Calibration: {reasons}",
        ))

    # ── 4. Regime penalty (reconstruct from known penalty factor) ─────────────
    regime_factor = _REGIME_FACTORS.get(regime, 1.0)
    if regime_factor < 1.0 and not penalties:
        # No explicit penalty recorded — estimate from factor
        pre_regime = round(conf / regime_factor, 2) if regime_factor else conf
        penalties = [{
            "type":   "REGIME_PENALTY",
            "factor": regime_factor,
            "reason": f"{regime} ×{regime_factor:.2f} (inferred)",
        }]
        steps.append(_lineage_step(
            STEP_REGIME_PENALTY,
            value_in  = pre_regime,
            value_out = conf,
            detail    = f"{regime} regime: confidence ×{regime_factor:.2f}",
        ))
    elif penalties:
        for p in penalties:
            factor = p.get("factor") or 1.0
            pre    = round(conf / factor, 2) if factor else conf
            steps.append(_lineage_step(
                STEP_REGIME_PENALTY,
                value_in  = pre,
                value_out = round(pre * factor, 2),
                detail    = p.get("reason") or f"{regime} regime penalty",
            ))

    # ── 5. Adjusted score computation ─────────────────────────────────────────
    steps.append(_lineage_step(
        STEP_ADJUSTED_SCORE,
        value_in  = (capped, conf),
        value_out = adj,
        detail    = f"{capped:.1f} × {conf:.1f}/100 = {adj:.2f}",
    ))

    # ── 6. Tier classification ─────────────────────────────────────────────────
    active_sigs = snapshot.get("active_signal_count") or 0
    recomputed  = _compute_tier(raw, adj, conf, active_sigs)
    threshold_crossed = _tier_threshold_crossed(adj, raw)
    steps.append(_lineage_step(
        STEP_TIER,
        value_in  = adj,
        value_out = tier,
        detail    = _tier_classification_detail(raw, adj, conf, active_sigs, recomputed),
    ))

    return {
        "steps":               steps,
        "raw_score":           raw,
        "capped_score":        capped,
        "final_confidence":    conf,
        "adjusted_score":      adj,
        "tier":                tier,
        "recomputed_tier":     recomputed,
        "tier_threshold_crossed": threshold_crossed,
    }


def _compute_tier(raw_score: float, adjusted_score: float,
                  confidence: float, active_signals: int) -> str:
    """Recompute tier from thresholds — mirrors predator.classify_tier."""
    if raw_score < ALERT_THRESHOLD:
        return TIER_WATCH
    if active_signals >= CONVICTION_MIN_SIGNALS and confidence >= CONVICTION_MIN_CONFIDENCE:
        return TIER_CONVICTION
    if adjusted_score >= ALERT_MIN_ADJUSTED:
        return TIER_ALERT
    return TIER_WATCH


def _tier_threshold_crossed(adjusted_score: float, raw_score: float) -> Optional[str]:
    """Return the highest tier threshold this adjusted score clears."""
    if raw_score < ALERT_THRESHOLD:
        return None
    if adjusted_score >= ALERT_MIN_ADJUSTED:
        return f"ALERT (adjusted ≥ {ALERT_MIN_ADJUSTED})"
    return None


def _tier_classification_detail(
    raw: float, adj: float, conf: float, active: int, tier: str
) -> str:
    if tier == TIER_CONVICTION:
        return (
            f"CONVICTION: active_sigs={active} ≥ {CONVICTION_MIN_SIGNALS}, "
            f"confidence={conf:.1f}% ≥ {CONVICTION_MIN_CONFIDENCE}%"
        )
    if tier == TIER_ALERT:
        return f"ALERT: adjusted={adj:.2f} ≥ {ALERT_MIN_ADJUSTED}, raw={raw:.0f} ≥ {ALERT_THRESHOLD}"
    return f"WATCH: thresholds not met (raw={raw:.0f}, adj={adj:.2f}, conf={conf:.1f}%)"


# ── Contribution ranking ──────────────────────────────────────────────────────

def rank_contributions(snapshot: dict) -> dict:
    """
    Rank all signals by their contribution score.

    Returns
    -------
    {
        "ranked":              [contribution_entry, ...],
        "strongest_factor":    str | None,  # signal with highest score
        "weakest_active_factor": str | None,  # active signal with lowest score > 0
        "suppressed_factors":  [str, ...],
        "total_contribution":  float,
    }

    Each contribution entry:
    {
        "rank":        int,           # 1 = highest (ties broken alphabetically)
        "signal":      str,
        "score":       float,
        "max_score":   int,
        "pct_of_max":  float | None,  # score/max_score × 100
        "active":      bool,
        "suppressed":  bool,
    }
    """
    contributions = snapshot.get("signal_contributions") or {}
    suppressed    = set(snapshot.get("suppressed_signals") or [])

    entries = []
    for sig in SIGNAL_NAMES:
        score   = float(contributions.get(sig) or 0)
        max_s   = SIGNAL_MAX_SCORES.get(sig, 1)
        pct     = round(score / max_s * 100, 1) if max_s > 0 and score > 0 else 0.0
        entries.append({
            "signal":     sig,
            "score":      score,
            "max_score":  max_s,
            "pct_of_max": pct,
            "active":     score > 0,
            "suppressed": sig in suppressed,
        })

    # Sort: descending score, tie-break alphabetically
    entries.sort(key=lambda e: (-e["score"], e["signal"]))
    for i, e in enumerate(entries):
        e["rank"] = i + 1

    active_entries     = [e for e in entries if e["active"]]
    strongest          = active_entries[0]["signal"]  if active_entries else None
    weakest_active     = active_entries[-1]["signal"] if active_entries else None
    total_contribution = round(sum(e["score"] for e in entries), 2)

    return {
        "ranked":                entries,
        "strongest_factor":      strongest,
        "weakest_active_factor": weakest_active,
        "suppressed_factors":    sorted(suppressed),
        "total_contribution":    total_contribution,
    }


# ── Anomaly detection ─────────────────────────────────────────────────────────

def flag_anomalies(snapshot: dict) -> list:
    """
    Detect suspicious patterns in an audit snapshot.

    Anomaly types
    -------------
    HIGH_CONF_LOW_SCORE         confidence ≥ threshold but raw_score < ALERT_THRESHOLD
    HIGH_SCORE_LOW_CONF         raw_score ≥ high threshold but confidence is very low
    SUPPRESSED_HIGH_CONVICTION  suppressed signals are enough to change the outcome
    TIER_MISMATCH               recomputed tier differs from stored tier
    ZERO_ACTIVE_WITH_SCORE      raw_score > 0 but active_signal_count == 0
    CONTRADICTORY_SIGNALS       calibration_adjustments include a conflict_penalty

    Sorted: HIGH severity first, then type alphabetically.
    """
    flags   = []
    raw     = snapshot.get("raw_score")  or 0.0
    adj     = snapshot.get("adjusted_score") or 0.0
    conf    = snapshot.get("confidence_pct") or 0.0
    tier    = snapshot.get("tier")
    active  = snapshot.get("active_signal_count") or 0
    suppressed = snapshot.get("suppressed_signals") or []
    orig_contrib = snapshot.get("original_contributions") or {}
    cal_adjs = snapshot.get("calibration_adjustments") or []

    # ── HIGH_CONF_LOW_SCORE ───────────────────────────────────────────────────
    if conf >= ANOMALY_HIGH_CONF_THRESHOLD and raw < ANOMALY_LOW_SCORE_FOR_HIGH_CONF:
        severity = "HIGH" if conf >= ANOMALY_HIGH_CONF_THRESHOLD + 10 else "MEDIUM"
        flags.append({
            "type":          "HIGH_CONF_LOW_SCORE",
            "severity":      severity,
            "confidence":    conf,
            "raw_score":     raw,
            "detail": (
                f"Confidence {conf:.1f}% ≥ {ANOMALY_HIGH_CONF_THRESHOLD}% "
                f"but raw_score {raw:.0f} < {ANOMALY_LOW_SCORE_FOR_HIGH_CONF} "
                f"— signal quality may outpace signal breadth"
            ),
        })
        log.warning(
            "decision_audit: HIGH_CONF_LOW_SCORE %s — conf=%.1f%% raw=%.0f",
            snapshot.get("ticker", "?"), conf, raw,
        )

    # ── HIGH_SCORE_LOW_CONF ───────────────────────────────────────────────────
    if raw >= ANOMALY_HIGH_SCORE_THRESHOLD and conf < ANOMALY_LOW_CONF_FOR_HIGH_SCORE:
        flags.append({
            "type":      "HIGH_SCORE_LOW_CONF",
            "severity":  "HIGH",
            "raw_score": raw,
            "confidence": conf,
            "detail": (
                f"Raw score {raw:.0f} ≥ {ANOMALY_HIGH_SCORE_THRESHOLD} "
                f"but confidence {conf:.1f}% < {ANOMALY_LOW_CONF_FOR_HIGH_SCORE}% "
                f"— signals fired on low-quality data"
            ),
        })
        log.warning(
            "decision_audit: HIGH_SCORE_LOW_CONF %s — raw=%.0f conf=%.1f%%",
            snapshot.get("ticker", "?"), raw, conf,
        )

    # ── SUPPRESSED_HIGH_CONVICTION ────────────────────────────────────────────
    if len(suppressed) >= ANOMALY_SUPPRESSED_THRESHOLD and orig_contrib:
        contrib = snapshot.get("signal_contributions") or {}
        # Restore suppressed signals to their original scores
        restored = dict(contrib)
        for sig in suppressed:
            if sig in orig_contrib:
                restored[sig] = orig_contrib[sig]
        restored_raw   = sum(restored.values())
        restored_active = sum(1 for v in restored.values() if v > 0)
        restored_tier  = _compute_tier(
            restored_raw, adj,  # adj unchanged (it's post-cap)
            conf, restored_active
        )
        if restored_tier == TIER_CONVICTION and tier != TIER_CONVICTION:
            flags.append({
                "type":          "SUPPRESSED_HIGH_CONVICTION",
                "severity":      "HIGH",
                "suppressed":    list(suppressed),
                "original_raw":  round(restored_raw, 2),
                "stored_tier":   tier,
                "would_be_tier": restored_tier,
                "detail": (
                    f"Without regime suppression of {suppressed}, raw score would be "
                    f"{restored_raw:.1f} → {restored_tier} (currently {tier})"
                ),
            })
            log.warning(
                "decision_audit: SUPPRESSED_HIGH_CONVICTION %s — would be CONVICTION",
                snapshot.get("ticker", "?"),
            )

    # ── TIER_MISMATCH ─────────────────────────────────────────────────────────
    recomputed = _compute_tier(raw, adj, conf, active)
    if tier is not None and recomputed != tier:
        flags.append({
            "type":           "TIER_MISMATCH",
            "severity":       "MEDIUM",
            "stored_tier":    tier,
            "recomputed_tier": recomputed,
            "detail": (
                f"Stored tier={tier} but re-classification from thresholds "
                f"gives {recomputed} (raw={raw:.0f}, adj={adj:.2f}, "
                f"conf={conf:.1f}%, active={active})"
            ),
        })
        log.warning(
            "decision_audit: TIER_MISMATCH %s — stored=%s recomputed=%s",
            snapshot.get("ticker", "?"), tier, recomputed,
        )

    # ── ZERO_ACTIVE_WITH_SCORE ────────────────────────────────────────────────
    if raw > 0 and active == 0:
        flags.append({
            "type":      "ZERO_ACTIVE_WITH_SCORE",
            "severity":  "HIGH",
            "raw_score": raw,
            "detail": (
                f"raw_score={raw:.0f} > 0 but active_signal_count=0 "
                "— snapshot data may be inconsistent"
            ),
        })
        log.warning(
            "decision_audit: ZERO_ACTIVE_WITH_SCORE %s — raw=%.0f active=0",
            snapshot.get("ticker", "?"), raw,
        )

    # ── CONTRADICTORY_SIGNALS ─────────────────────────────────────────────────
    conflict_adjs = [
        a for a in cal_adjs
        if "conflict" in (a.get("type") or "").lower()
        or "CONFLICT" in (a.get("type") or "")
    ]
    if conflict_adjs:
        total_pen = sum(a.get("delta") or 0 for a in conflict_adjs)
        flags.append({
            "type":      "CONTRADICTORY_SIGNALS",
            "severity":  "MEDIUM",
            "conflicts": [a.get("type") for a in conflict_adjs],
            "total_penalty": total_pen,
            "detail": (
                f"{len(conflict_adjs)} conflicting signal combination(s) detected; "
                f"total confidence penalty={total_pen:.1f}pp"
            ),
        })
        log.warning(
            "decision_audit: CONTRADICTORY_SIGNALS %s — %d conflict(s) penalty=%.1f",
            snapshot.get("ticker", "?"), len(conflict_adjs), total_pen,
        )

    flags.sort(key=lambda f: (0 if f["severity"] == "HIGH" else 1, f["type"]))
    return flags


# ── Explanation helpers ───────────────────────────────────────────────────────

def explain_snapshot(snapshot: dict) -> str:
    """
    Generate a one-paragraph human-readable explanation of a scoring decision.

    Deterministic: same snapshot always produces the same string.
    """
    ticker  = snapshot.get("ticker")  or "?"
    raw     = snapshot.get("raw_score") or 0.0
    adj     = snapshot.get("adjusted_score") or 0.0
    conf    = snapshot.get("confidence_pct") or 0.0
    tier    = snapshot.get("tier") or TIER_WATCH
    regime  = snapshot.get("regime") or "unknown"
    contrib = snapshot.get("signal_contributions") or {}
    suppressed = snapshot.get("suppressed_signals") or []
    penalties  = snapshot.get("penalties") or []
    cal_adjs   = snapshot.get("calibration_adjustments") or []
    boosts     = snapshot.get("boosts") or []

    ranked = rank_contributions(snapshot)["ranked"]
    active = [e for e in ranked if e["active"]]

    # Signal summary
    if active:
        top_signals = ", ".join(
            f"{e['signal']} (+{e['score']:.0f})"
            for e in active[:3]
        )
        signal_txt = f"Top signals: {top_signals}."
    else:
        signal_txt = "No signals fired."

    # Confidence adjustment summary
    cal_total = sum(a.get("delta") or 0 for a in cal_adjs)
    boost_total = sum(b.get("delta") or 0 for b in boosts)
    net_cal = cal_total + boost_total
    if abs(net_cal) >= 0.1:
        cal_txt = f" Calibration adjustments netted {net_cal:+.1f}pp on confidence."
    else:
        cal_txt = ""

    # Regime summary
    factor = _REGIME_FACTORS.get(regime, 1.0)
    if factor < 1.0:
        regime_txt = (
            f" {regime} regime applied a ×{factor:.2f} confidence penalty."
        )
    else:
        regime_txt = f" Regime: {regime} (no penalty)."

    # Suppression summary
    supp_txt = ""
    if suppressed:
        supp_txt = f" {len(suppressed)} signal(s) suppressed by {regime}: {', '.join(suppressed)}."

    # Tier explanation
    if tier == TIER_CONVICTION:
        tier_txt = f"Tier: {tier} — multiple independent HIGH-quality signals agree."
    elif tier == TIER_ALERT:
        tier_txt = f"Tier: {tier} — adjusted score {adj:.2f} cleared the {ALERT_MIN_ADJUSTED} threshold."
    else:
        tier_txt = f"Tier: {tier} — score thresholds not met."

    summary = (
        f"{ticker} scored {raw:.0f} raw (adjusted {adj:.2f}, {tier}) "
        f"in {regime} regime. "
        f"{signal_txt}"
        f"{cal_txt}"
        f"{regime_txt}"
        f"{supp_txt}"
        f" Confidence: {conf:.1f}%."
        f" {tier_txt}"
    )
    return summary.strip()


def explain_tier_change(old_tier: str, new_tier: str, delta_adj: float) -> str:
    """
    Explain why the tier changed between two scoring runs.

    delta_adj: new_adjusted_score − old_adjusted_score
    """
    if old_tier == new_tier:
        return f"Tier unchanged: {old_tier} (adjusted score shifted by {delta_adj:+.2f})."

    direction = "upgraded" if _tier_rank(new_tier) > _tier_rank(old_tier) else "downgraded"
    return (
        f"Tier {direction} from {old_tier} → {new_tier}. "
        f"Adjusted score changed by {delta_adj:+.2f}."
    )


def explain_suppression(suppressed_signals: list, regime: str) -> str:
    """Human-readable reason why signals were suppressed."""
    if not suppressed_signals:
        return "No signals were suppressed."
    sigs = ", ".join(suppressed_signals)
    factor = _REGIME_FACTORS.get(regime, 1.0)
    return (
        f"{regime} regime suppressed {len(suppressed_signals)} signal(s): {sigs}. "
        f"Confidence was additionally reduced by ×{factor:.2f}."
    )


def _tier_rank(tier: str) -> int:
    """Numeric rank for tier comparison (higher = better)."""
    return {TIER_CONVICTION: 3, TIER_ALERT: 2, TIER_WATCH: 1}.get(tier, 0)


# ── Report builder ────────────────────────────────────────────────────────────

def _build_warnings(anomalies: list, lineage: dict) -> list:
    warnings = []
    for a in anomalies:
        if a.get("severity") == "HIGH":
            warnings.append(f"[HIGH] {a['type']}: {a['detail']}")
    if lineage.get("recomputed_tier") != lineage.get("tier"):
        rt = lineage.get("recomputed_tier")
        st = lineage.get("tier")
        if rt and st:
            warnings.append(f"Tier mismatch: stored={st} recomputed={rt}")
    return warnings


def generate_report(snapshot: dict) -> dict:
    """
    Full audit report for one scoring decision.

    Returns
    -------
    {
        "ticker":             str,
        "timestamp":          str,
        "tier":               str,
        "summary":            str,          # one-line human label
        "lineage":            dict,         # reconstruct_lineage() output
        "contributions":      dict,         # rank_contributions() output
        "anomalies":          list,         # flag_anomalies() output
        "explanation":        str,          # explain_snapshot() output
        "confidence_summary": dict,
        "suppression_summary": str,
        "warnings":           list,
    }
    """
    ticker    = snapshot.get("ticker")  or "?"
    timestamp = snapshot.get("timestamp") or ""
    tier      = snapshot.get("tier")    or TIER_WATCH
    regime    = snapshot.get("regime")  or "unknown"
    raw       = snapshot.get("raw_score") or 0.0
    adj       = snapshot.get("adjusted_score") or 0.0
    conf      = snapshot.get("confidence_pct") or 0.0
    suppressed = snapshot.get("suppressed_signals") or []
    cal_adjs   = snapshot.get("calibration_adjustments") or []
    boosts     = snapshot.get("boosts") or []
    penalties  = snapshot.get("penalties") or []

    lineage      = reconstruct_lineage(snapshot)
    contributions = rank_contributions(snapshot)
    anomalies    = flag_anomalies(snapshot)
    explanation  = explain_snapshot(snapshot)

    cal_delta   = sum(a.get("delta") or 0 for a in cal_adjs)
    boost_delta = sum(b.get("delta") or 0 for b in boosts)
    regime_factor = _REGIME_FACTORS.get(regime, 1.0)
    regime_delta  = round(conf * (regime_factor - 1), 2) if regime_factor < 1 else 0.0

    conf_summary = {
        "final":               conf,
        "calibration_delta":   round(cal_delta + boost_delta, 2),
        "regime_delta":        regime_delta,
    }

    summary = f"{ticker} | {tier} | raw={raw:.0f} adj={adj:.2f} conf={conf:.1f}%"

    warnings = _build_warnings(anomalies, lineage)

    log.info(
        "decision_audit: report generated for %s — tier=%s anomalies=%d warnings=%d",
        ticker, tier, len(anomalies), len(warnings),
    )

    return {
        "ticker":             ticker,
        "timestamp":          timestamp,
        "tier":               tier,
        "summary":            summary,
        "lineage":            lineage,
        "contributions":      contributions,
        "anomalies":          anomalies,
        "explanation":        explanation,
        "confidence_summary": conf_summary,
        "suppression_summary": explain_suppression(suppressed, regime),
        "warnings":           warnings,
    }


# ── Batch audit ───────────────────────────────────────────────────────────────

def audit_batch(snapshots: list) -> dict:
    """
    Generate audit reports for a list of snapshots.

    Returns
    -------
    {
        "count":          int,
        "reports":        [ generate_report(s) for s in snapshots ],
        "anomaly_summary": { anomaly_type: count },
        "tier_breakdown": { tier: count },
        "high_severity_count": int,
    }
    """
    reports = [generate_report(s) for s in snapshots]

    anomaly_counts: dict = {}
    tier_counts: dict    = {}
    high_severity        = 0

    for r in reports:
        for a in r.get("anomalies", []):
            t = a.get("type", "UNKNOWN")
            anomaly_counts[t] = anomaly_counts.get(t, 0) + 1
            if a.get("severity") == "HIGH":
                high_severity += 1
        t = r.get("tier") or "UNKNOWN"
        tier_counts[t] = tier_counts.get(t, 0) + 1

    return {
        "count":               len(reports),
        "reports":             reports,
        "anomaly_summary":     dict(sorted(anomaly_counts.items())),
        "tier_breakdown":      dict(sorted(tier_counts.items())),
        "high_severity_count": high_severity,
    }
