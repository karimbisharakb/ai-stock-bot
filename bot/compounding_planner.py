"""
Phase A20 — Long-horizon compounding and allocation planner.

Planning and analytics only.  No trades, no broker calls, no order placement,
no autonomous actions.  No tax or legal advice — educational wording only.
Deterministic.  Sparse-data safe.

Public API (pure — no I/O):
  ALLOCATION_BUCKETS                                          list[str]   (7)
  PRIMARY_BUCKETS                                             list[str]   (5)
  OVERLAY_BUCKETS                                             list[str]   (2)
  REBALANCE_URGENCY_LEVELS                                    list[str]
  PROJECTION_SCENARIOS                                        list[str]
  PROJECTION_HORIZONS                                         list[int]
  classify_position_to_bucket(ticker)                         -> str
  compute_current_allocation(positions, cash, total_value)    -> dict
  compute_target_allocation(regime, risk_score,
                             worst_stress, scorecard_summary) -> dict
  compute_drift(current, target)                              -> dict
  compute_rebalance_urgency(drift)                            -> str
  compute_priority_areas(drift)                               -> list
  compute_cash_deployment_guidance(cash, total_value,
                                    current, target, drift)   -> str
  compute_contribution_guidance(contribution_room,
                                 cash, total_value)           -> str
  compute_risk_reduction_guidance(risk_report,
                                   risk_score, regime)        -> str
  compute_strategy_alignment_notes(scorecard_summary,
                                    current, target)          -> list
  compute_bucket_dollar_values(positions, cash)               -> dict
  project_single_scenario(bucket_values, monthly_contrib,
                            scenario, years)                  -> dict
  compute_all_projections(bucket_values, monthly_contrib)     -> dict
  generate_planner_output(portfolio, account_settings,
                           regime_ctx, risk_report,
                           scorecard_summary, stress_summary) -> dict

Public API (DB — lazy imports):
  save_planner_snapshot(output, projections)   -> dict
  get_latest_planner_snapshot()                -> Optional[dict]
  get_planner_history(limit)                   -> list

Orchestration:
  run_planner(monthly_contribution)            -> dict
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


# ── Allocation buckets ────────────────────────────────────────────────────────

PRIMARY_BUCKETS: list = [
    "CORE_INDEX",
    "QUALITY_GROWTH",
    "ALPHA_OPPORTUNITY",
    "SPECULATIVE",
    "CASH_RESERVE",
]

OVERLAY_BUCKETS: list = [
    "CANADIAN_EXPOSURE",
    "USD_EXPOSURE",
]

ALLOCATION_BUCKETS: list = PRIMARY_BUCKETS + OVERLAY_BUCKETS

REBALANCE_URGENCY_LEVELS: list = ["NONE", "LOW", "MEDIUM", "HIGH"]

# Drift thresholds (percentage points)
_URGENCY_LOW    =  5.0
_URGENCY_MEDIUM = 10.0
_URGENCY_HIGH   = 20.0

# Minimum drift to appear in priority areas
_PRIORITY_THRESHOLD = 3.0


# ── Return assumptions for projection scenarios ───────────────────────────────

PROJECTION_SCENARIOS: list = ["conservative", "base", "aggressive", "downside"]
PROJECTION_HORIZONS:  list = [1, 3, 5, 10]

# Annualised real-return assumptions per bucket per scenario
# These are educational estimates only — not financial advice
_RETURN_ASSUMPTIONS: dict = {
    "conservative": {
        "CORE_INDEX":        0.055,
        "QUALITY_GROWTH":    0.070,
        "ALPHA_OPPORTUNITY": 0.050,
        "SPECULATIVE":       0.020,
        "CASH_RESERVE":      0.015,
    },
    "base": {
        "CORE_INDEX":        0.085,
        "QUALITY_GROWTH":    0.100,
        "ALPHA_OPPORTUNITY": 0.120,
        "SPECULATIVE":       0.080,
        "CASH_RESERVE":      0.025,
    },
    "aggressive": {
        "CORE_INDEX":        0.110,
        "QUALITY_GROWTH":    0.150,
        "ALPHA_OPPORTUNITY": 0.200,
        "SPECULATIVE":       0.150,
        "CASH_RESERVE":      0.030,
    },
    "downside": {
        "CORE_INDEX":       -0.050,
        "QUALITY_GROWTH":   -0.080,
        "ALPHA_OPPORTUNITY":-0.150,
        "SPECULATIVE":      -0.250,
        "CASH_RESERVE":      0.010,
    },
}

# Default primary targets (must sum to 100)
_DEFAULT_PRIMARY_TARGETS: dict = {
    "CORE_INDEX":        30.0,
    "QUALITY_GROWTH":    25.0,
    "ALPHA_OPPORTUNITY": 20.0,
    "SPECULATIVE":       10.0,
    "CASH_RESERVE":      15.0,
}

# Default overlay targets (informational, independent of primary)
_DEFAULT_OVERLAY_TARGETS: dict = {
    "CANADIAN_EXPOSURE": 30.0,
    "USD_EXPOSURE":       65.0,
}

# Max per-bucket cap before normalization
_BUCKET_MAX_PCT = 80.0
_BUCKET_MIN_PCT =  0.0


# ── Position → bucket map ─────────────────────────────────────────────────────

_TICKER_BUCKET: dict = {
    # Core index ETFs
    "QQQ":     "CORE_INDEX", "SPY":     "CORE_INDEX",
    "VFV.TO":  "CORE_INDEX", "XIU.TO":  "CORE_INDEX",
    "XQQ.TO":  "CORE_INDEX", "XEQT.TO": "CORE_INDEX",
    "VEQT.TO": "CORE_INDEX", "ZQQ.TO":  "CORE_INDEX",
    "HXS.TO":  "CORE_INDEX",
    # Quality growth
    "MSFT":    "QUALITY_GROWTH", "AAPL":    "QUALITY_GROWTH",
    "AMZN":    "QUALITY_GROWTH", "GOOG":    "QUALITY_GROWTH",
    "META":    "QUALITY_GROWTH", "SHOP.TO": "QUALITY_GROWTH",
    "RY.TO":   "QUALITY_GROWTH", "TD.TO":   "QUALITY_GROWTH",
    "ENB.TO":  "QUALITY_GROWTH", "CNQ.TO":  "QUALITY_GROWTH",
    # Alpha opportunity (AI / semiconductors)
    "NVDA":    "ALPHA_OPPORTUNITY",
    "AMD":     "ALPHA_OPPORTUNITY",
    "TSM":     "ALPHA_OPPORTUNITY",
    "PLTR":    "ALPHA_OPPORTUNITY",
}


# ── Pure classification ───────────────────────────────────────────────────────

def classify_position_to_bucket(ticker: str) -> str:
    """Map a ticker to a primary allocation bucket.  Unknown → SPECULATIVE."""
    return _TICKER_BUCKET.get(str(ticker).upper(), "SPECULATIVE")


# ── Pure allocation functions ─────────────────────────────────────────────────

def compute_current_allocation(
    positions: list,
    cash: float,
    total_value: float,
) -> dict:
    """
    Map portfolio positions + cash to allocation percentages.

    Primary buckets (CORE_INDEX, QUALITY_GROWTH, ALPHA_OPPORTUNITY,
    SPECULATIVE, CASH_RESERVE) are mutually exclusive and sum to 100 %.
    Overlay buckets (CANADIAN_EXPOSURE, USD_EXPOSURE) are informational
    currency splits of the equity portion (excluding cash).
    """
    bucket_values: dict = {b: 0.0 for b in PRIMARY_BUCKETS}
    bucket_values["CASH_RESERVE"] = float(cash)

    cad_equity = 0.0
    usd_equity = 0.0

    for pos in positions:
        ticker = str(pos.get("ticker", "")).upper()
        mv     = float(pos.get("market_value", 0.0))
        bucket = classify_position_to_bucket(ticker)
        bucket_values[bucket] = bucket_values.get(bucket, 0.0) + mv
        if ticker.endswith(".TO"):
            cad_equity += mv
        else:
            usd_equity += mv

    total = float(total_value) if float(total_value) > 0 else 1.0
    equity_total = cad_equity + usd_equity
    equity_denom = equity_total if equity_total > 0 else 1.0

    result: dict = {}
    for bucket in PRIMARY_BUCKETS:
        result[bucket] = round(bucket_values[bucket] / total * 100.0, 1)

    result["CANADIAN_EXPOSURE"] = round(cad_equity / equity_denom * 100.0, 1)
    result["USD_EXPOSURE"]      = round(usd_equity / equity_denom * 100.0, 1)

    return result


def compute_target_allocation(
    regime: str = "NEUTRAL",
    risk_score: float = 50.0,
    worst_stress_pct: float = 0.0,
    scorecard_summary: Optional[dict] = None,
) -> dict:
    """
    Compute a suggested target allocation based on market and risk conditions.
    Pure, deterministic.  Returns all 7 allocation bucket percentages.
    Primary 5 are normalised to sum to 100 %.  Overlay 2 are fixed defaults.
    """
    targets: dict = dict(_DEFAULT_PRIMARY_TARGETS)

    # Regime adjustments (net-zero so primary buckets stay near 100 %)
    reg = str(regime).upper()
    if reg == "PANIC":
        targets["CASH_RESERVE"]      += 10.0
        targets["SPECULATIVE"]       -=  5.0
        targets["ALPHA_OPPORTUNITY"] -=  5.0
    elif reg == "RISK_OFF":
        targets["CASH_RESERVE"]      +=  5.0
        targets["SPECULATIVE"]       -=  3.0
        targets["ALPHA_OPPORTUNITY"] -=  2.0
    elif reg == "RISK_ON":
        targets["CASH_RESERVE"]      -=  5.0
        targets["ALPHA_OPPORTUNITY"] +=  3.0
        targets["SPECULATIVE"]       +=  2.0

    # Risk score adjustments (net-zero)
    rs = float(risk_score)
    if rs > 70.0:
        targets["SPECULATIVE"]       -= 3.0
        targets["CORE_INDEX"]        += 3.0
    elif rs < 30.0:
        targets["ALPHA_OPPORTUNITY"] += 2.0
        targets["SPECULATIVE"]       += 1.0
        targets["CORE_INDEX"]        -= 3.0

    # Stress sensitivity adjustment (net-zero)
    if abs(float(worst_stress_pct)) > 20.0:
        targets["SPECULATIVE"]       -= 2.0
        targets["CORE_INDEX"]        += 2.0

    # Clamp each bucket
    for k in PRIMARY_BUCKETS:
        targets[k] = max(_BUCKET_MIN_PCT, min(_BUCKET_MAX_PCT, targets.get(k, 0.0)))

    # Normalise primary buckets to 100 %
    primary_total = sum(targets[b] for b in PRIMARY_BUCKETS)
    if primary_total > 0:
        for k in PRIMARY_BUCKETS:
            targets[k] = round(targets[k] / primary_total * 100.0, 1)

    # Append fixed overlay targets
    targets.update(_DEFAULT_OVERLAY_TARGETS)
    return targets


def compute_drift(current: dict, target: dict) -> dict:
    """
    Drift = current_pct − target_pct for every bucket.
    Positive drift → over-allocated; negative → under-allocated.
    """
    return {
        bucket: round(float(current.get(bucket, 0.0)) - float(target.get(bucket, 0.0)), 1)
        for bucket in ALLOCATION_BUCKETS
    }


def compute_rebalance_urgency(drift: dict) -> str:
    """
    Return NONE / LOW / MEDIUM / HIGH based on maximum absolute drift across
    primary buckets.
    """
    max_abs = max((abs(float(drift.get(b, 0.0))) for b in PRIMARY_BUCKETS), default=0.0)
    if max_abs >= _URGENCY_HIGH:
        return "HIGH"
    if max_abs >= _URGENCY_MEDIUM:
        return "MEDIUM"
    if max_abs >= _URGENCY_LOW:
        return "LOW"
    return "NONE"


def compute_priority_areas(drift: dict) -> list:
    """
    Return list of significant drift entries sorted by absolute drift desc.
    Each entry: {bucket, drift_pct, action: "REDUCE"|"INCREASE"}.
    Only primary buckets with |drift| >= _PRIORITY_THRESHOLD are included.
    """
    items = []
    for bucket in PRIMARY_BUCKETS:
        d = float(drift.get(bucket, 0.0))
        if abs(d) >= _PRIORITY_THRESHOLD:
            items.append({
                "bucket":    bucket,
                "drift_pct": d,
                "action":    "REDUCE" if d > 0 else "INCREASE",
            })
    return sorted(items, key=lambda x: abs(x["drift_pct"]), reverse=True)


# ── Guidance generators (pure) ────────────────────────────────────────────────

def compute_cash_deployment_guidance(
    cash: float,
    total_value: float,
    current_alloc: dict,
    target_alloc: dict,
    drift: dict,
) -> str:
    """
    Plain-language cash deployment guidance.  Educational only — no buy orders.
    """
    if float(total_value) <= 0:
        return "No portfolio data available for cash deployment guidance."

    cash_pct    = float(current_alloc.get("CASH_RESERVE", 0.0))
    target_cash = float(target_alloc.get("CASH_RESERVE", 15.0))
    above_target = cash_pct - target_cash

    if above_target > 5.0:
        underweight = [
            b for b in PRIMARY_BUCKETS
            if b != "CASH_RESERVE" and float(drift.get(b, 0.0)) < -_PRIORITY_THRESHOLD
        ]
        if underweight:
            buckets_str = " and ".join(underweight[:2])
            return (
                f"Cash is {cash_pct:.0f}% of portfolio — above the {target_cash:.0f}% target. "
                f"Consider deploying into underweight areas: {buckets_str}. "
                "No specific action is required — review when your next target opportunity arises."
            )
        return (
            f"Cash is {cash_pct:.0f}% of portfolio — above the {target_cash:.0f}% target. "
            "Monitor for opportunities in underweight buckets as they arise."
        )
    elif above_target < -5.0:
        return (
            f"Cash is {cash_pct:.0f}% of portfolio — below the {target_cash:.0f}% target. "
            "Consider building cash reserves before adding new positions. "
            "Aim to maintain a buffer for unexpected opportunities or expenses."
        )
    return (
        f"Cash is {cash_pct:.0f}% of portfolio — near the {target_cash:.0f}% target. "
        "Current reserve looks adequate. Continue monitoring."
    )


def compute_contribution_guidance(
    contribution_room: Optional[float],
    cash: float,
    total_value: float,
) -> str:
    """
    TFSA-aware contribution guidance.  Educational only — not tax or legal advice.
    """
    if contribution_room is None:
        return (
            "Contribution room is not set in account settings. "
            "Update it to receive contribution guidance. "
            "Note: this is general educational information — consult a financial advisor for personal advice."
        )

    room = float(contribution_room)
    if room <= 0:
        return (
            "No TFSA contribution room is available. "
            "Review your contribution history before making new deposits. "
            "This is educational information only — consult a financial advisor for personal guidance."
        )
    elif room < 5_000:
        return (
            f"Limited contribution room remaining ({room:,.0f} CAD). "
            "Prioritise the highest-conviction positions when room permits. "
            "This is educational information only."
        )
    elif room < 25_000:
        return (
            f"Moderate contribution room available ({room:,.0f} CAD). "
            "Consider deploying into underweight allocation buckets as target conditions align. "
            "This is educational information only — not financial advice."
        )
    return (
        f"Substantial contribution room available ({room:,.0f} CAD). "
        "Long-term compounding opportunities exist across your target allocation buckets. "
        "Regular, consistent contributions tend to smooth out market timing risk. "
        "This is educational information only — not financial advice."
    )


def compute_risk_reduction_guidance(
    risk_report: Optional[dict],
    risk_score: float,
    regime: str,
) -> str:
    """
    Risk reduction guidance based on current portfolio risk score and regime.
    """
    rs  = float(risk_score)
    reg = str(regime).upper()

    if not risk_report:
        return f"No risk report data available. Estimated risk score: {rs:.0f}/100."

    actions = risk_report.get("recommended_actions", [])

    if rs >= 70:
        action_str = f" Priority: {actions[0]}" if actions else ""
        regime_note = " In the current market regime, risk is elevated — extra caution is warranted." if reg in ("RISK_OFF", "PANIC") else ""
        return (
            f"Portfolio risk score is elevated ({rs:.0f}/100).{action_str}"
            f"{regime_note} "
            "Review position sizing and concentration before adding new risk."
        )
    elif rs >= 50:
        return (
            f"Portfolio risk score is moderate ({rs:.0f}/100). "
            "No urgent action needed, but continue to monitor concentration and thesis coverage."
        )
    return (
        f"Portfolio risk score is within an acceptable range ({rs:.0f}/100). "
        "Continue monitoring positions and keep checklists up to date."
    )


def compute_strategy_alignment_notes(
    scorecard_summary: Optional[dict],
    current_alloc: dict,
    target_alloc: dict,
) -> list:
    """
    Plain-language notes linking strategy performance to allocation gaps.
    Returns a list of strings; always returns at least one entry.
    """
    notes: list = []

    if not scorecard_summary:
        return ["No strategy scorecard data available. Run a scorecard analysis for alignment insights."]

    behavior = scorecard_summary.get("behavior_metrics", {})

    top_list = scorecard_summary.get("top_strategies", [])
    if top_list:
        top_name = top_list[0].get("strategy", "")
        if top_name:
            notes.append(
                f"Strongest historical strategy: {top_name}. "
                "Ensure allocation reflects this if conviction remains high."
            )

    overused = behavior.get("overused", [])
    if overused:
        notes.append(
            f"Overused strategy signal in: {', '.join(overused[:2])}. "
            "Review whether current allocation is intentional."
        )

    weak_thesis = behavior.get("weak_thesis", [])
    if weak_thesis:
        notes.append(
            f"Thin thesis coverage for: {', '.join(weak_thesis[:2])}. "
            "Strengthen documentation before increasing exposure in these areas."
        )

    checklist_neglect = behavior.get("checklist_neglect", [])
    if checklist_neglect:
        notes.append(
            f"Checklist discipline is low for: {', '.join(checklist_neglect[:2])}. "
            "Enforce completion before any new position entries."
        )

    # Structural alignment note
    core_current = float(current_alloc.get("CORE_INDEX", 0.0))
    core_target  = float(target_alloc.get("CORE_INDEX", 30.0))
    if core_current < core_target - 10.0:
        notes.append(
            f"CORE_INDEX is {core_target - core_current:.0f} pp below its target. "
            "Consider gradually rebalancing toward index exposure for stability."
        )

    if not notes:
        notes.append(
            "Allocation appears broadly aligned with strategy performance data."
        )

    return notes


# ── Projection engine (pure) ──────────────────────────────────────────────────

def compute_bucket_dollar_values(positions: list, cash: float) -> dict:
    """Return {bucket: dollar_value} for each primary bucket."""
    values: dict = {b: 0.0 for b in PRIMARY_BUCKETS}
    values["CASH_RESERVE"] = float(cash)
    for pos in positions:
        ticker = str(pos.get("ticker", "")).upper()
        mv     = float(pos.get("market_value", 0.0))
        bucket = classify_position_to_bucket(ticker)
        values[bucket] = values.get(bucket, 0.0) + mv
    return values


def project_single_scenario(
    bucket_values: dict,
    monthly_contribution: float,
    scenario: str,
    years: int,
) -> dict:
    """
    Project a single (scenario, horizon) cell.

    Uses compound growth for starting balance and a geometric series for
    monthly contributions.  Contributions are allocated proportionally to
    existing bucket weights.  Returns all values in the same currency as the
    input (typically CAD for a TFSA).

    These are illustrative estimates only — not financial projections.
    """
    returns    = _RETURN_ASSUMPTIONS.get(scenario, _RETURN_ASSUMPTIONS["base"])
    n_months   = int(years) * 12
    mc         = max(0.0, float(monthly_contribution))

    starting_values = {b: max(0.0, float(bucket_values.get(b, 0.0))) for b in PRIMARY_BUCKETS}
    starting_total  = sum(starting_values.values())

    bucket_fv:         dict  = {}
    contrib_fv_total:  float = 0.0
    total_fv:          float = 0.0

    for bucket in PRIMARY_BUCKETS:
        start   = starting_values[bucket]
        r_ann   = float(returns.get(bucket, 0.025))
        monthly_r = (1.0 + r_ann) ** (1.0 / 12.0) - 1.0

        # Contribution split proportional to starting balance
        proportion    = start / starting_total if starting_total > 0 else (1.0 / len(PRIMARY_BUCKETS))
        monthly_cb    = mc * proportion

        # FV of lump sum
        fv_lump = start * (1.0 + r_ann) ** int(years)

        # FV of monthly contributions (ordinary annuity)
        if abs(monthly_r) > 1e-10:
            fv_contrib = monthly_cb * ((1.0 + monthly_r) ** n_months - 1.0) / monthly_r
        else:
            fv_contrib = monthly_cb * n_months

        fv_lump    = max(0.0, fv_lump)
        fv_contrib = max(0.0, fv_contrib)

        bucket_fv[bucket] = round(fv_lump + fv_contrib, 2)
        contrib_fv_total += fv_contrib
        total_fv         += bucket_fv[bucket]

    total_contributed  = mc * n_months
    compounding_impact = total_fv - starting_total - total_contributed

    return {
        "scenario":           scenario,
        "years":              int(years),
        "starting_value":     round(starting_total,   2),
        "projected_value":    round(total_fv,          2),
        "total_contributed":  round(total_contributed, 2),
        "contribution_impact":round(contrib_fv_total,  2),
        "compounding_impact": round(compounding_impact,2),
        "bucket_values":      bucket_fv,
    }


def compute_all_projections(
    bucket_values: dict,
    monthly_contribution: float,
) -> dict:
    """
    Compute all (scenario × horizon) projection cells.

    Returns {scenario: [projection_dict, ...]} where each list is ordered
    by PROJECTION_HORIZONS.  Also includes 'monthly_contribution' and
    'starting_value' at the top level for context.
    """
    starting_total = sum(max(0.0, float(bucket_values.get(b, 0.0))) for b in PRIMARY_BUCKETS)
    result: dict = {
        "monthly_contribution": round(float(monthly_contribution), 2),
        "starting_value":       round(starting_total, 2),
    }
    for scenario in PROJECTION_SCENARIOS:
        result[scenario] = [
            project_single_scenario(bucket_values, monthly_contribution, scenario, y)
            for y in PROJECTION_HORIZONS
        ]
    return result


# ── Planner orchestration (pure) ──────────────────────────────────────────────

def generate_planner_output(
    portfolio: dict,
    account_settings: dict,
    regime_ctx: Optional[dict],
    risk_report: Optional[dict],
    scorecard_summary: Optional[dict],
    stress_summary: Optional[dict],
) -> dict:
    """
    Compute the full planner output from all input sources.  Pure, no I/O.
    """
    positions   = portfolio.get("positions", [])
    aggregates  = portfolio.get("aggregates", {})
    cash        = float(aggregates.get("cash",                  0.0))
    total_value = float(aggregates.get("total_portfolio_value", 0.0))

    regime      = str((regime_ctx or {}).get("overall_regime",  "NEUTRAL")).upper()
    risk_score  = float((risk_report or {}).get("portfolio_risk_score", 50.0))
    worst_stress= float((stress_summary or {}).get("worst_loss_pct",     0.0))

    current_alloc = compute_current_allocation(positions, cash, total_value)
    target_alloc  = compute_target_allocation(regime, risk_score, worst_stress, scorecard_summary)
    drift         = compute_drift(current_alloc, target_alloc)
    urgency       = compute_rebalance_urgency(drift)
    priority      = compute_priority_areas(drift)

    contribution_room = account_settings.get("contribution_room")

    cash_guidance         = compute_cash_deployment_guidance(cash, total_value, current_alloc, target_alloc, drift)
    contribution_guidance = compute_contribution_guidance(contribution_room, cash, total_value)
    risk_guidance         = compute_risk_reduction_guidance(risk_report, risk_score, regime)
    alignment_notes       = compute_strategy_alignment_notes(scorecard_summary, current_alloc, target_alloc)

    return {
        "portfolio_value":           round(total_value, 2),
        "cash":                      round(cash, 2),
        "regime":                    regime,
        "risk_score":                round(risk_score, 1),
        "current_allocation":        current_alloc,
        "target_allocation":         target_alloc,
        "drift":                     drift,
        "rebalance_urgency":         urgency,
        "priority_areas":            priority,
        "cash_deployment_guidance":  cash_guidance,
        "contribution_guidance":     contribution_guidance,
        "risk_reduction_guidance":   risk_guidance,
        "strategy_alignment_notes":  alignment_notes,
    }


# ── Snapshot ID ────────────────────────────────────────────────────────────────

def _snapshot_id(portfolio_value: float, created_at: str) -> str:
    raw    = f"{portfolio_value:.4f}:{created_at}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16].upper()
    return f"PLN-{digest}"


# ── DDL (safety fallback — migration v22 is primary) ──────────────────────────

_PLANNER_DDL = [
    """
    CREATE TABLE IF NOT EXISTS planner_snapshots (
        id                            INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id                   TEXT    UNIQUE NOT NULL,
        created_at                    TEXT    NOT NULL,
        portfolio_value               REAL    NOT NULL DEFAULT 0.0,
        cash                          REAL    NOT NULL DEFAULT 0.0,
        regime                        TEXT    NOT NULL DEFAULT 'NEUTRAL',
        risk_score                    REAL    NOT NULL DEFAULT 50.0,
        rebalance_urgency             TEXT    NOT NULL DEFAULT 'NONE',
        current_allocation_json       TEXT    NOT NULL DEFAULT '{}',
        target_allocation_json        TEXT    NOT NULL DEFAULT '{}',
        drift_json                    TEXT    NOT NULL DEFAULT '{}',
        priority_areas_json           TEXT    NOT NULL DEFAULT '[]',
        cash_deployment_guidance      TEXT    NOT NULL DEFAULT '',
        contribution_guidance         TEXT    NOT NULL DEFAULT '',
        risk_reduction_guidance       TEXT    NOT NULL DEFAULT '',
        strategy_alignment_notes_json TEXT    NOT NULL DEFAULT '[]',
        projections_json              TEXT    NOT NULL DEFAULT '{}',
        monthly_contribution          REAL    NOT NULL DEFAULT 0.0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_planner_created_at ON planner_snapshots(created_at)",
]


def _ensure_tables() -> None:
    from database import get_connection
    conn = get_connection()
    try:
        for ddl in _PLANNER_DDL:
            conn.execute(ddl)
        conn.commit()
    finally:
        conn.close()


# ── DB functions (lazy import pattern) ────────────────────────────────────────

def save_planner_snapshot(output: dict, projections: dict) -> dict:
    """Persist a planner output + projections as an immutable snapshot."""
    from database import get_connection
    _ensure_tables()

    now      = datetime.now(timezone.utc).isoformat()
    port_val = float(output.get("portfolio_value", 0.0))
    snap_id  = _snapshot_id(port_val, now)

    row = {
        "snapshot_id":                   snap_id,
        "created_at":                    now,
        "portfolio_value":               port_val,
        "cash":                          float(output.get("cash",         0.0)),
        "regime":                        str(output.get("regime",         "NEUTRAL")),
        "risk_score":                    float(output.get("risk_score",   50.0)),
        "rebalance_urgency":             str(output.get("rebalance_urgency", "NONE")),
        "current_allocation_json":       json.dumps(output.get("current_allocation",  {})),
        "target_allocation_json":        json.dumps(output.get("target_allocation",   {})),
        "drift_json":                    json.dumps(output.get("drift",               {})),
        "priority_areas_json":           json.dumps(output.get("priority_areas",      [])),
        "cash_deployment_guidance":      str(output.get("cash_deployment_guidance",  "")),
        "contribution_guidance":         str(output.get("contribution_guidance",      "")),
        "risk_reduction_guidance":       str(output.get("risk_reduction_guidance",   "")),
        "strategy_alignment_notes_json": json.dumps(output.get("strategy_alignment_notes", [])),
        "projections_json":              json.dumps(projections),
        "monthly_contribution":          float(projections.get("monthly_contribution", 0.0)),
    }

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO planner_snapshots
              (snapshot_id, created_at, portfolio_value, cash, regime, risk_score,
               rebalance_urgency, current_allocation_json, target_allocation_json,
               drift_json, priority_areas_json, cash_deployment_guidance,
               contribution_guidance, risk_reduction_guidance,
               strategy_alignment_notes_json, projections_json, monthly_contribution)
            VALUES
              (:snapshot_id, :created_at, :portfolio_value, :cash, :regime, :risk_score,
               :rebalance_urgency, :current_allocation_json, :target_allocation_json,
               :drift_json, :priority_areas_json, :cash_deployment_guidance,
               :contribution_guidance, :risk_reduction_guidance,
               :strategy_alignment_notes_json, :projections_json, :monthly_contribution)
            """,
            row,
        )
        conn.commit()
    finally:
        conn.close()

    return {**row}


def get_latest_planner_snapshot() -> Optional[dict]:
    """Return the most recent planner snapshot with all JSON fields deserialised."""
    from database import get_connection
    _ensure_tables()

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM planner_snapshots ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        r = dict(row)
        r["current_allocation"]       = json.loads(r.pop("current_allocation_json",       "{}"))
        r["target_allocation"]        = json.loads(r.pop("target_allocation_json",        "{}"))
        r["drift"]                    = json.loads(r.pop("drift_json",                    "{}"))
        r["priority_areas"]           = json.loads(r.pop("priority_areas_json",           "[]"))
        r["strategy_alignment_notes"] = json.loads(r.pop("strategy_alignment_notes_json", "[]"))
        r["projections"]              = json.loads(r.pop("projections_json",              "{}"))
        return r
    finally:
        conn.close()


def get_planner_history(limit: int = 20) -> list:
    """Return recent planner snapshots (without projections), newest first."""
    from database import get_connection
    _ensure_tables()

    safe_limit = min(max(int(limit), 1), 100)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT snapshot_id, created_at, portfolio_value, cash, regime, risk_score, "
            "rebalance_urgency, cash_deployment_guidance, contribution_guidance, "
            "risk_reduction_guidance, monthly_contribution "
            "FROM planner_snapshots ORDER BY created_at DESC LIMIT ?",
            (safe_limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Orchestration ─────────────────────────────────────────────────────────────

def run_planner(monthly_contribution: float = 500.0) -> dict:
    """
    Fetch all data sources, compute the planner output and projections,
    persist an immutable snapshot, and return the combined result.

    monthly_contribution: assumed CAD amount contributed per month to the TFSA.
    Educational only — not financial advice.
    """
    mc = max(0.0, float(monthly_contribution))

    # ── Fetch data (each wrapped to be sparse-safe) ──────────────────────────
    portfolio: dict = {}
    try:
        from portfolio_reconciliation import get_canonical_portfolio
        portfolio = get_canonical_portfolio()
    except Exception:
        log.warning("run_planner: portfolio fetch failed", exc_info=True)

    account_settings: dict = {}
    try:
        from manual_portfolio import get_account_settings
        account_settings = get_account_settings()
    except Exception:
        log.warning("run_planner: account settings fetch failed", exc_info=True)

    regime_ctx: Optional[dict] = None
    try:
        from market_regime_intelligence import get_regime_context_for_checklist
        regime_ctx = get_regime_context_for_checklist()
    except Exception:
        log.warning("run_planner: regime fetch failed", exc_info=True)

    risk_report: Optional[dict] = None
    try:
        from portfolio_risk_guardrails import get_portfolio_risk_report
        risk_report = get_portfolio_risk_report()
    except Exception:
        log.warning("run_planner: risk report fetch failed", exc_info=True)

    scorecard_summary: Optional[dict] = None
    try:
        from strategy_scorecards import get_scorecards_summary
        scorecard_summary = get_scorecards_summary()
    except Exception:
        log.warning("run_planner: scorecard summary fetch failed", exc_info=True)

    stress_summary: Optional[dict] = None
    try:
        from portfolio_stress_testing import get_stress_history, get_stress_run
        runs = get_stress_history(limit=1)
        if runs:
            stress_summary = get_stress_run(runs[0]["run_id"])
    except Exception:
        log.warning("run_planner: stress summary fetch failed", exc_info=True)

    # ── Compute output ────────────────────────────────────────────────────────
    output = generate_planner_output(
        portfolio, account_settings, regime_ctx,
        risk_report, scorecard_summary, stress_summary,
    )

    positions   = portfolio.get("positions", [])
    aggregates  = portfolio.get("aggregates", {})
    cash        = float(aggregates.get("cash", 0.0))
    bucket_vals = compute_bucket_dollar_values(positions, cash)
    projections = compute_all_projections(bucket_vals, mc)

    # ── Persist ───────────────────────────────────────────────────────────────
    saved = save_planner_snapshot(output, projections)
    output["snapshot_id"]         = saved["snapshot_id"]
    output["created_at"]          = saved["created_at"]
    output["monthly_contribution"] = mc
    output["projections"]         = projections

    return output
