"""
Phase A19 — Personal strategy scorecards.

Classifies decisions, alpha candidates, journal theses, and outcomes into
strategy buckets and computes per-strategy metrics to show which investing
styles work best over time.

Analytics only.  No trades, no alerts, no order placement, no autonomous
actions, no live scoring mutation.  Sparse-data safe.

Public API (pure — no I/O):
  STRATEGY_TYPES                                              list[str]
  RECOMMENDATIONS                                             dict[str, str]
  classify_ticker(ticker)                                     -> str
  classify_setup_type(setup_type)                             -> str
  classify_candidate(ticker, setup_type, alpha_tier)          -> str
  compute_thesis_completeness(thesis)                         -> float   0-100
  compute_checklist_discipline_score(checklists)              -> float | None
  compute_validation_quality(validation_rows)                 -> float | None
  compute_risk_adjusted_score(win_rate, avg_return,
                               avg_max_drawdown, val_quality) -> float | None
  compute_confidence_score(n_outcomes)                        -> float
  compute_stress_sensitivity(strategy, stress_events)         -> float
  compute_scorecard(strategy, raw_data)                       -> dict
  compute_behavior_metrics(scorecards)                        -> dict
  generate_recommendations(strategy, scorecard, regime)       -> list[str]

Public API (DB — lazy imports):
  compute_all_scorecards()          -> dict
  get_scorecard(strategy)           -> dict | None
  get_scorecards_summary()          -> dict
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


# ── Strategy taxonomy ─────────────────────────────────────────────────────────

STRATEGY_TYPES: list = [
    "CORE_INDEX",
    "GROWTH_COMPOUNDER",
    "AI_SEMI_MOMENTUM",
    "SPACE_DEFENSE",
    "CRYPTO_BETA",
    "SHORT_SQUEEZE",
    "EVENT_CATALYST",
    "BREAKOUT_MOMENTUM",
    "EARLY_ACCUMULATION",
    "SPECULATIVE_HIGH_VOL",
    "CASH_DEFENSIVE",
]

# Human-readable labels for recommendations
RECOMMENDATIONS: dict = {
    "increase_focus":          "Strong historical outcomes — allocate more attention and capital to this strategy",
    "reduce_exposure":         "Poor risk-adjusted returns — reduce position sizing or avoid new entries",
    "require_stricter_checklist": "High false-positive rate — require full checklist completion before every entry",
    "improve_thesis_quality":  "Thesis quality is weak — write stronger written rationale before entering positions",
    "use_smaller_sizing":      "Large historical drawdowns — use smaller initial positions with defined stops",
    "avoid_during_risk_off":   "Strategy performs poorly in adverse regimes — pause during RISK_OFF / PANIC",
    "monitor_only":            "Insufficient track record — observe signals without committing capital",
    "promote_to_core":         "Consistent strong performance — promote to a core strategy with full sizing",
}

# Thresholds
_WIN_RATE_STRONG     = 60.0    # win_rate >= this → "strong"
_WIN_RATE_WEAK       = 30.0    # win_rate < this → "weak"
_FP_RATE_HIGH        = 40.0    # false_positive_rate > this → high
_FP_RATE_ELEVATED    = 30.0    # false_positive_rate > this → elevated
_THESIS_WEAK         = 40.0    # thesis_completeness < this → weak
_CHECKLIST_NEGLECT   = 50.0    # discipline_score < this → neglect
_DRAWDOWN_LARGE      = -15.0   # avg_max_drawdown < this → large
_DRAWDOWN_SEVERE     = -12.0   # used for regime-specific avoidance
_SCORE_PROMOTE       = 80.0    # risk_adjusted_score >= this → promote candidate
_SCORE_STRONG        = 70.0    # risk_adjusted_score >= this → increase_focus candidate
_SCORE_WEAK          = 30.0    # risk_adjusted_score < this → reduce candidate
_MIN_CANDIDATES_MONITOR = 5    # < this → monitor_only

# Default stress sensitivity (% loss estimate) per strategy when no live data
_DEFAULT_STRESS_SENSITIVITY: dict = {
    "CORE_INDEX":            5.0,
    "GROWTH_COMPOUNDER":    10.0,
    "AI_SEMI_MOMENTUM":     25.0,
    "SPACE_DEFENSE":         8.0,
    "CRYPTO_BETA":          20.0,
    "SHORT_SQUEEZE":        18.0,
    "EVENT_CATALYST":       15.0,
    "BREAKOUT_MOMENTUM":    12.0,
    "EARLY_ACCUMULATION":   12.0,
    "SPECULATIVE_HIGH_VOL": 22.0,
    "CASH_DEFENSIVE":        2.0,
}


# ── Classification maps ───────────────────────────────────────────────────────

_TICKER_STRATEGY: dict = {
    # Core index / broad-market ETFs
    "QQQ":     "CORE_INDEX", "SPY":     "CORE_INDEX",
    "VFV.TO":  "CORE_INDEX", "XIU.TO":  "CORE_INDEX",
    "XQQ.TO":  "CORE_INDEX", "XEQT.TO": "CORE_INDEX",
    "VEQT.TO": "CORE_INDEX", "ZQQ.TO":  "CORE_INDEX",
    "HXS.TO":  "CORE_INDEX",
    # Growth compounders
    "MSFT":    "GROWTH_COMPOUNDER", "AAPL":    "GROWTH_COMPOUNDER",
    "AMZN":    "GROWTH_COMPOUNDER", "GOOG":    "GROWTH_COMPOUNDER",
    "META":    "GROWTH_COMPOUNDER", "SHOP.TO": "GROWTH_COMPOUNDER",
    "RY.TO":   "GROWTH_COMPOUNDER", "TD.TO":   "GROWTH_COMPOUNDER",
    "ENB.TO":  "GROWTH_COMPOUNDER", "CNQ.TO":  "GROWTH_COMPOUNDER",
    # AI / Semiconductor momentum
    "NVDA":    "AI_SEMI_MOMENTUM",
    "AMD":     "AI_SEMI_MOMENTUM",
    "TSM":     "AI_SEMI_MOMENTUM",
    "PLTR":    "AI_SEMI_MOMENTUM",
}

_SETUP_TO_STRATEGY: dict = {
    "BREAKOUT_EXPANSION":    "BREAKOUT_MOMENTUM",
    "SQUEEZE_CANDIDATE":     "SHORT_SQUEEZE",
    "CATALYST_RUNUP":        "EVENT_CATALYST",
    "OPTIONS_PRESSURE":      "SPECULATIVE_HIGH_VOL",
    "EARLY_ACCUMULATION":    "EARLY_ACCUMULATION",
    "HIGH_RISK_SPECULATION": "SPECULATIVE_HIGH_VOL",
}

# Behavior classes from A6 alpha_validation
_NEGATIVE_BEHAVIORS = frozenset({"VOLATILITY_TRAP", "FAILED_SQUEEZE", "SHORT_LIVED_SPIKE"})
_POSITIVE_BEHAVIORS = frozenset({"VALID_BREAKOUT", "SUSTAINED_TREND", "INSTITUTIONAL_ACCUMULATION"})


# ── Pure classification functions ─────────────────────────────────────────────

def classify_ticker(ticker: str) -> str:
    """Return the strategy bucket for a ticker.  Unknown tickers → SPECULATIVE_HIGH_VOL."""
    return _TICKER_STRATEGY.get(str(ticker).upper(), "SPECULATIVE_HIGH_VOL")


def classify_setup_type(setup_type: str) -> str:
    """Return the strategy bucket for a setup_type string.  Unknown → SPECULATIVE_HIGH_VOL."""
    if not setup_type:
        return "SPECULATIVE_HIGH_VOL"
    return _SETUP_TO_STRATEGY.get(str(setup_type).strip().upper(), "SPECULATIVE_HIGH_VOL")


def classify_candidate(
    ticker: str,
    setup_type: Optional[str],
    alpha_tier: Optional[str],
) -> str:
    """
    Classify an alpha candidate into a strategy bucket.

    Priority:
      1. Ticker map (deterministic for known watchlist tickers)
      2. Setup-type map (for discovered-universe tickers)
      3. SPECULATIVE_HIGH_VOL (catch-all)
    """
    t = str(ticker).upper() if ticker else ""
    if t in _TICKER_STRATEGY:
        return _TICKER_STRATEGY[t]
    st = str(setup_type).strip().upper() if setup_type else ""
    return _SETUP_TO_STRATEGY.get(st, "SPECULATIVE_HIGH_VOL")


# ── Pure metric helpers ───────────────────────────────────────────────────────

def compute_thesis_completeness(thesis: dict) -> float:
    """Score 0-100 measuring how complete a single position thesis is."""
    text_fields = {
        "thesis_title":       1,
        "thesis_text":        3,
        "entry_reason":       1,
        "expected_catalysts": 1,
        "risk_factors":       1,
        "exit_plan":          1,
    }
    numeric_fields = ("invalidation_level", "target_level")
    max_score = sum(text_fields.values()) + len(numeric_fields)

    score = 0.0
    for field, weight in text_fields.items():
        val = str(thesis.get(field, "")).strip()
        if len(val) >= 10:
            score += weight
    for field in numeric_fields:
        if thesis.get(field) is not None:
            score += 1

    return round(score / max_score * 100.0, 1)


def compute_checklist_discipline_score(checklists: list) -> Optional[float]:
    """
    Return 0-100 discipline score across a list of checklist dicts.
    None if no checklists.  Penalizes high blocking_items counts.
    """
    if not checklists:
        return None
    completions = [float(c.get("checklist_completion", 0.0)) for c in checklists]
    avg = sum(completions) / len(completions)
    total_blocking = sum(int(c.get("blocking_items", 0)) for c in checklists)
    # Penalty: each blocking item reduces score by 5 pts, capped at 20
    penalty = min(total_blocking * 5.0, 20.0)
    return round(max(0.0, avg - penalty), 1)


def compute_validation_quality(validation_rows: list) -> Optional[float]:
    """
    Return average validation_score 0-100 from alpha_validation rows.
    None if no rows.
    """
    scores = [float(v["validation_score"]) for v in validation_rows
              if v.get("validation_score") is not None]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 1)


def compute_risk_adjusted_score(
    win_rate: Optional[float],
    avg_return: Optional[float],
    avg_max_drawdown: Optional[float],
    validation_quality: Optional[float],
) -> Optional[float]:
    """
    Combine win rate, average return, drawdown, and validation quality
    into a single 0-100 risk-adjusted score.  Returns None when there is
    no outcome data at all.
    """
    if win_rate is None and avg_return is None:
        return None

    score = 50.0

    if win_rate is not None:
        # 50 % win rate is neutral; ±25 pts range
        score += (float(win_rate) - 50.0) * 0.5

    if avg_return is not None:
        # Each 1 % avg return adds 2 pts; capped at ±10
        score += max(-10.0, min(10.0, float(avg_return) * 2.0))

    if avg_max_drawdown is not None:
        # Drawdown penalty starts at -5 %; each % beyond costs 1.5 pts
        dd = float(avg_max_drawdown)
        penalty = max(0.0, (abs(dd) - 5.0) * 1.5)
        score -= penalty

    if validation_quality is not None:
        # Quality adjustment: ±10 pts
        score += (float(validation_quality) - 50.0) * 0.2

    return round(max(0.0, min(100.0, score)), 1)


def compute_confidence_score(n_outcomes: int) -> float:
    """
    Map number of completed outcomes to a 0-100 confidence score.
    Represents how much data we have to trust the scorecard metrics.
    """
    n = int(n_outcomes)
    if n == 0:
        return 0.0
    if n < 5:
        return 20.0
    if n < 15:
        return 50.0
    if n < 30:
        return 75.0
    return 100.0


def compute_stress_sensitivity(strategy: str, stress_events: list) -> float:
    """
    Return estimated % loss exposure for this strategy under the worst scenario.

    If stress_events contains position_results, compute from actual portfolio
    holdings.  Falls back to the deterministic default table.
    """
    if not stress_events:
        return _DEFAULT_STRESS_SENSITIVITY.get(strategy, 15.0)

    # Find the worst-case scenario (lowest loss %)
    worst = min(stress_events, key=lambda e: float(e.get("estimated_loss_pct", 0)))
    try:
        pr_json = worst.get("position_results_json") or worst.get("position_results", "[]")
        if isinstance(pr_json, list):
            position_results = pr_json
        else:
            position_results = json.loads(pr_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        position_results = []

    shocks = []
    for pr in position_results:
        ticker = str(pr.get("ticker", "")).upper()
        ticker_strategy = classify_ticker(ticker)
        if ticker_strategy == strategy:
            shocks.append(abs(float(pr.get("shock_pct", 0))))

    if not shocks:
        return _DEFAULT_STRESS_SENSITIVITY.get(strategy, 15.0)

    return round(sum(shocks) / len(shocks), 1)


# ── Scorecard computation ─────────────────────────────────────────────────────

def compute_scorecard(strategy: str, raw_data: dict) -> dict:
    """
    Compute a full scorecard dict for a strategy from pre-grouped raw data.

    raw_data keys:
      "shadow_rows"      — alpha_shadow_log rows for this strategy
      "outcome_rows"     — alpha_outcomes rows (COMPLETE) for this strategy
      "validation_rows"  — alpha_validation rows for this strategy
      "thesis_rows"      — position_theses rows for tickers in this strategy
      "checklist_rows"   — decision_checklists rows for this strategy
      "position_rows"    — portfolio_positions rows for this strategy
      "stress_events"    — portfolio_stress_events rows (all scenarios)
    """
    shadow      = raw_data.get("shadow_rows",      [])
    outcomes    = raw_data.get("outcome_rows",      [])
    validations = raw_data.get("validation_rows",   [])
    theses      = raw_data.get("thesis_rows",       [])
    checklists  = raw_data.get("checklist_rows",    [])
    positions   = raw_data.get("position_rows",     [])
    stress_evts = raw_data.get("stress_events",     [])

    total_candidates = len(shadow)
    total_decisions  = len(checklists)
    active_positions = sum(1 for p in positions if not int(p.get("is_stale", 0)))
    closed_positions = sum(1 for p in positions if int(p.get("is_stale", 0)))

    # ── Outcome metrics ──────────────────────────────────────────────────────
    returns_5d   = [float(o["return_5d"])   for o in outcomes if o.get("return_5d")   is not None]
    max_gains    = [float(o["max_gain"])    for o in outcomes if o.get("max_gain")    is not None]
    max_drawdowns= [float(o["max_drawdown"])for o in outcomes if o.get("max_drawdown")is not None]

    n_with_return = len(returns_5d)
    win_rate  = (sum(1 for r in returns_5d if r > 0) / n_with_return * 100.0
                 ) if returns_5d else None
    avg_return    = (sum(returns_5d)    / n_with_return  ) if returns_5d   else None
    avg_max_gain  = (sum(max_gains)     / len(max_gains) ) if max_gains    else None
    avg_max_dd    = (sum(max_drawdowns) / len(max_drawdowns)) if max_drawdowns else None

    # ── Validation quality & false-positive rate ─────────────────────────────
    validation_quality = compute_validation_quality(validations)
    fp_count = sum(1 for v in validations
                   if v.get("behavior_class") in _NEGATIVE_BEHAVIORS)
    false_positive_rate = (fp_count / len(validations) * 100.0
                           ) if validations else None

    # ── Thesis completeness ──────────────────────────────────────────────────
    thesis_scores = [compute_thesis_completeness(t) for t in theses]
    thesis_completeness = (sum(thesis_scores) / len(thesis_scores)
                           ) if thesis_scores else None

    # ── Checklist discipline ─────────────────────────────────────────────────
    checklist_discipline = compute_checklist_discipline_score(checklists)

    # ── Stress sensitivity ────────────────────────────────────────────────────
    stress_sensitivity = compute_stress_sensitivity(strategy, stress_evts)

    # ── Composite scores ──────────────────────────────────────────────────────
    risk_adjusted = compute_risk_adjusted_score(win_rate, avg_return, avg_max_dd, validation_quality)
    confidence    = compute_confidence_score(n_with_return)

    def _r(v, digits=1):
        return round(v, digits) if v is not None else None

    return {
        "strategy":                  strategy,
        "total_candidates":          total_candidates,
        "total_decisions":           total_decisions,
        "active_positions":          active_positions,
        "closed_positions":          closed_positions,
        "win_rate":                  _r(win_rate),
        "avg_return":                _r(avg_return, 2),
        "avg_max_gain":              _r(avg_max_gain, 2),
        "avg_max_drawdown":          _r(avg_max_dd, 2),
        "validation_quality":        _r(validation_quality),
        "false_positive_rate":       _r(false_positive_rate),
        "stress_sensitivity":        _r(stress_sensitivity),
        "thesis_completeness":       _r(thesis_completeness),
        "checklist_discipline_score":_r(checklist_discipline),
        "risk_adjusted_score":       _r(risk_adjusted),
        "confidence_score":          _r(confidence),
        "data_available":            total_candidates > 0 or len(outcomes) > 0,
    }


# ── Behavior metrics (cross-strategy) ────────────────────────────────────────

def compute_behavior_metrics(scorecards: list) -> dict:
    """
    Derive personal-pattern insights across all strategy scorecards.

    Returns a dict with lists of strategy names for each behaviour pattern.
    All lists contain strategy names (strings), not scorecard dicts.
    """
    non_empty = [s for s in scorecards if s.get("total_candidates", 0) > 0]

    # Overused: strategies with the most candidates (top 3, only if > 10 candidates)
    by_candidates = sorted(non_empty, key=lambda s: s["total_candidates"], reverse=True)
    overused = [s["strategy"] for s in by_candidates[:3]
                if s["total_candidates"] > 10]

    # Underused: strategies with very few candidates
    underused = [s["strategy"] for s in sorted(non_empty, key=lambda s: s["total_candidates"])
                 if s["total_candidates"] <= 2][:3]

    # Best historical outcomes: by risk_adjusted_score descending
    with_score = [s for s in non_empty if s.get("risk_adjusted_score") is not None]
    best_historical = [s["strategy"] for s in
                       sorted(with_score, key=lambda x: x["risk_adjusted_score"], reverse=True)[:3]]

    # Worst drawdowns: by avg_max_drawdown ascending (most negative first)
    with_dd = [s for s in non_empty if s.get("avg_max_drawdown") is not None]
    worst_drawdowns = [s["strategy"] for s in
                       sorted(with_dd, key=lambda x: x["avg_max_drawdown"])[:3]]

    # Checklist neglect: discipline_score < threshold
    checklist_neglect = [s["strategy"] for s in non_empty
                         if s.get("checklist_discipline_score") is not None
                         and s["checklist_discipline_score"] < _CHECKLIST_NEGLECT]

    # Weak thesis: thesis_completeness < threshold
    weak_thesis = [s["strategy"] for s in non_empty
                   if s.get("thesis_completeness") is not None
                   and s["thesis_completeness"] < _THESIS_WEAK]

    # Repeated false positives: fp_rate > threshold
    repeated_false_positives = [s["strategy"] for s in non_empty
                                 if s.get("false_positive_rate") is not None
                                 and s["false_positive_rate"] > _FP_RATE_ELEVATED]

    return {
        "overused":                  overused,
        "underused":                 underused,
        "best_historical":           best_historical,
        "worst_drawdowns":           worst_drawdowns,
        "checklist_neglect":         checklist_neglect,
        "weak_thesis":               weak_thesis,
        "repeated_false_positives":  repeated_false_positives,
    }


# ── Recommendations ───────────────────────────────────────────────────────────

def generate_recommendations(
    strategy: str,
    scorecard: dict,
    regime: str = "NEUTRAL",
) -> list:
    """
    Return a list of recommendation key strings for a strategy.
    Deterministic — no randomness, no I/O.

    Possible values (keys of RECOMMENDATIONS):
      increase_focus, reduce_exposure, require_stricter_checklist,
      improve_thesis_quality, use_smaller_sizing, avoid_during_risk_off,
      monitor_only, promote_to_core
    """
    recs: list = []
    total_cands   = scorecard.get("total_candidates", 0)
    win_rate      = scorecard.get("win_rate")
    avg_return    = scorecard.get("avg_return")
    fp_rate       = scorecard.get("false_positive_rate")
    thesis_score  = scorecard.get("thesis_completeness")
    dd            = scorecard.get("avg_max_drawdown")
    ra_score      = scorecard.get("risk_adjusted_score")
    discipline    = scorecard.get("checklist_discipline_score")
    active_pos    = scorecard.get("active_positions", 0)
    conf_score    = scorecard.get("confidence_score", 0.0)

    # Insufficient data → monitor only
    if total_cands < _MIN_CANDIDATES_MONITOR:
        recs.append("monitor_only")
        return recs

    # Promote to core (all conditions must be met)
    if (ra_score is not None and ra_score >= _SCORE_PROMOTE
            and win_rate is not None and win_rate >= _WIN_RATE_STRONG
            and conf_score >= 50.0):
        recs.append("promote_to_core")

    # Increase focus (strong score, few active positions)
    elif (ra_score is not None and ra_score >= _SCORE_STRONG
          and active_pos <= 1 and conf_score >= 50.0):
        recs.append("increase_focus")

    # Reduce exposure (weak score with enough data)
    if (ra_score is not None and ra_score < _SCORE_WEAK
            and conf_score >= 50.0 and "promote_to_core" not in recs):
        recs.append("reduce_exposure")

    # Poor win rate (enough outcomes but losing)
    if (win_rate is not None and win_rate < _WIN_RATE_WEAK
            and conf_score >= 50.0 and "reduce_exposure" not in recs):
        recs.append("reduce_exposure")

    # High false-positive rate
    if fp_rate is not None and fp_rate > _FP_RATE_HIGH:
        recs.append("require_stricter_checklist")

    # Checklist neglect
    if discipline is not None and discipline < _CHECKLIST_NEGLECT and "require_stricter_checklist" not in recs:
        recs.append("require_stricter_checklist")

    # Weak thesis quality
    if thesis_score is not None and thesis_score < _THESIS_WEAK:
        recs.append("improve_thesis_quality")

    # Large drawdowns → size down
    if dd is not None and dd < _DRAWDOWN_LARGE:
        recs.append("use_smaller_sizing")

    # Avoid during risk-off regime
    if (regime in ("RISK_OFF", "PANIC")
            and dd is not None and dd < _DRAWDOWN_SEVERE):
        recs.append("avoid_during_risk_off")

    # Fallback if nothing actionable
    if not recs:
        recs.append("monitor_only")

    return recs


# ── Data fetchers (lazy imports) ──────────────────────────────────────────────

def _fetch_all_raw_data() -> dict:
    """
    Read all relevant tables and return raw rows keyed by table name.
    Tables that do not yet exist return empty lists (sparse-data safe).
    """
    from database import get_connection
    conn = get_connection()
    try:
        def _safe_query(sql, params=()):
            try:
                rows = conn.execute(sql, params).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []

        shadow_rows = _safe_query(
            "SELECT ticker, setup_type, alpha_tier, alpha_score, filter_reason "
            "FROM alpha_shadow_log"
        )
        outcome_rows = _safe_query(
            "SELECT ticker, setup_type, alpha_tier, return_5d, max_gain, "
            "max_drawdown, status FROM alpha_outcomes WHERE status = 'COMPLETE'"
        )
        validation_rows = _safe_query(
            "SELECT ticker, setup_type, alpha_tier, behavior_class, validation_score "
            "FROM alpha_validation"
        )
        thesis_rows = _safe_query(
            "SELECT ticker, setup_type, thesis_title, thesis_text, entry_reason, "
            "expected_catalysts, risk_factors, invalidation_level, target_level, "
            "exit_plan, status FROM position_theses"
        )
        checklist_rows = _safe_query(
            "SELECT ticker, decision_type, checklist_status, checklist_completion, "
            "blocking_items, readiness FROM decision_checklists"
        )
        position_rows = _safe_query(
            "SELECT ticker, is_stale FROM portfolio_positions"
        )

        # Latest stress run events (optional)
        stress_events: list = []
        try:
            run_row = conn.execute(
                "SELECT run_id FROM portfolio_stress_runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if run_row:
                stress_events = _safe_query(
                    "SELECT scenario_type, position_results_json, estimated_loss_pct "
                    "FROM portfolio_stress_events WHERE run_id = ?",
                    (run_row["run_id"],),
                )
        except Exception:
            pass

        # Latest regime snapshot (optional — for recommendation context)
        regime_overall = "NEUTRAL"
        try:
            reg_row = conn.execute(
                "SELECT overall_regime FROM market_regime_snapshots "
                "ORDER BY captured_at DESC LIMIT 1"
            ).fetchone()
            if reg_row:
                regime_overall = reg_row["overall_regime"]
        except Exception:
            pass

        return {
            "shadow_rows":      shadow_rows,
            "outcome_rows":     outcome_rows,
            "validation_rows":  validation_rows,
            "thesis_rows":      thesis_rows,
            "checklist_rows":   checklist_rows,
            "position_rows":    position_rows,
            "stress_events":    stress_events,
            "regime_overall":   regime_overall,
        }
    finally:
        conn.close()


def _group_by_strategy(raw: dict) -> dict:
    """
    Partition raw rows by strategy bucket.

    Returns dict[strategy_name → {"shadow": [], "outcome": [], ...}]
    """
    buckets: dict = {st: {
        "shadow_rows":      [],
        "outcome_rows":     [],
        "validation_rows":  [],
        "thesis_rows":      [],
        "checklist_rows":   [],
        "position_rows":    [],
        "stress_events":    raw.get("stress_events", []),
    } for st in STRATEGY_TYPES}

    def _add(rows, key, use_ticker=True, use_setup=True):
        for row in rows:
            ticker    = row.get("ticker", "") if use_ticker else ""
            setup_type= row.get("setup_type") if use_setup else None
            alpha_tier= row.get("alpha_tier")
            st = classify_candidate(ticker, setup_type, alpha_tier)
            buckets[st][key].append(row)

    _add(raw["shadow_rows"],      "shadow_rows")
    _add(raw["outcome_rows"],     "outcome_rows")
    _add(raw["validation_rows"],  "validation_rows")
    _add(raw["thesis_rows"],      "thesis_rows")
    _add(raw["checklist_rows"],   "checklist_rows",  use_setup=False)
    _add(raw["position_rows"],    "position_rows",   use_setup=False)

    return buckets


# ── Orchestration (lazy imports) ──────────────────────────────────────────────

def compute_all_scorecards() -> dict:
    """
    Read all relevant tables, group by strategy, compute scorecards,
    derive behaviour metrics, and generate recommendations.

    Returns:
    {
        "scorecards":       list[dict],
        "behavior_metrics": dict,
        "computed_at":      str (ISO 8601),
    }
    """
    raw     = _fetch_all_raw_data()
    buckets = _group_by_strategy(raw)
    regime  = raw.get("regime_overall", "NEUTRAL")

    scorecards: list = []
    for strategy in STRATEGY_TYPES:
        card = compute_scorecard(strategy, buckets[strategy])
        card["recommendations"] = generate_recommendations(strategy, card, regime)
        scorecards.append(card)

    behavior = compute_behavior_metrics(scorecards)

    return {
        "scorecards":       scorecards,
        "behavior_metrics": behavior,
        "computed_at":      datetime.now(timezone.utc).isoformat(),
    }


def get_scorecard(strategy: str) -> Optional[dict]:
    """
    Return the scorecard for a single strategy, or None if strategy is unknown.
    Always recomputes from current data.
    """
    if strategy not in STRATEGY_TYPES:
        return None
    result = compute_all_scorecards()
    for card in result["scorecards"]:
        if card["strategy"] == strategy:
            card["behavior_metrics"] = result["behavior_metrics"]
            card["computed_at"]      = result["computed_at"]
            return card
    return None


def get_scorecards_summary() -> dict:
    """
    Return a compact summary: top/bottom 3 strategies by risk_adjusted_score,
    overall behavior metrics, and high-priority recommendations.
    """
    result    = compute_all_scorecards()
    cards     = result["scorecards"]
    behavior  = result["behavior_metrics"]

    scored = [c for c in cards if c.get("risk_adjusted_score") is not None]
    top3   = sorted(scored, key=lambda c: c["risk_adjusted_score"], reverse=True)[:3]
    bot3   = sorted(scored, key=lambda c: c["risk_adjusted_score"])[:3]

    # Collect high-priority recommendations (exclude "monitor_only")
    priority_recs: list = []
    for card in cards:
        for rec in card.get("recommendations", []):
            if rec != "monitor_only":
                priority_recs.append({
                    "strategy":       card["strategy"],
                    "recommendation": rec,
                    "description":    RECOMMENDATIONS.get(rec, rec),
                })

    return {
        "total_strategies":     len(STRATEGY_TYPES),
        "strategies_with_data": sum(1 for c in cards if c.get("data_available")),
        "top_strategies":       [{"strategy": c["strategy"], "risk_adjusted_score": c["risk_adjusted_score"]} for c in top3],
        "bottom_strategies":    [{"strategy": c["strategy"], "risk_adjusted_score": c["risk_adjusted_score"]} for c in bot3],
        "behavior_metrics":     behavior,
        "priority_recommendations": priority_recs[:10],
        "computed_at":          result["computed_at"],
    }
