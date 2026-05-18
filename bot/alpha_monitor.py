"""
Phase A5 — Alpha monitoring report, quality diagnosis, and self-improvement recommendations.

Produces a deterministic daily report with:
  - tier distribution and top candidates
  - data quality analysis (missing data rates, stale tickers)
  - quality diagnosis (too strict / too loose / coverage gaps)
  - actionable recommendations (no weights changed automatically)

Never raises — all subsystem errors are caught and surfaced in report["errors"].
Never sends alerts.  Never modifies scoring or thresholds.
"""
import json
import logging
from datetime import datetime, timedelta

log = logging.getLogger(__name__)


# ── Public API ────────────────────────────────────────────────────────────────

def generate_alpha_report() -> dict:
    """Return a complete alpha monitoring report dict.

    Structure:
      generated_at, errors, summary, top_candidates, best_non_predator,
      rejected_predator_alerts, rare_setups, setup_distribution,
      data_quality, diagnosis, recommendations.
    """
    errors: list = []

    # ── Collect alpha shadow data ─────────────────────────────────────────────
    tier_counts        = {}
    top_candidates     = []
    rare_setups        = []
    setup_distribution = []
    best_non_predator  = []
    rejected_alerts    = []
    coverage           = {}
    universe           = []

    try:
        from alpha_shadow import get_shadow_manager
        mgr = get_shadow_manager()
        tier_counts        = mgr.count_by_tier()
        top_candidates     = mgr.get_top_candidates(limit=10)
        rare_setups        = mgr.get_rare_setups(limit=5)
        setup_distribution = mgr.get_top_setup_types(limit=10)
    except Exception as exc:
        errors.append(f"alpha_shadow error: {exc}")

    try:
        from predator import PREDATOR_WATCHLIST
        from alpha_shadow import get_shadow_manager
        mgr = get_shadow_manager()
        best_non_predator = mgr.get_best_non_predator(PREDATOR_WATCHLIST, limit=5)
        rejected_alerts   = mgr.get_rejected_alerts(limit=10)
    except Exception as exc:
        errors.append(f"predator comparison error: {exc}")

    try:
        from alpha_universe import get_alpha_universe
        from alpha_shadow import get_shadow_manager
        universe = get_alpha_universe()
        coverage = get_shadow_manager().get_universe_coverage(universe)
    except Exception as exc:
        errors.append(f"universe coverage error: {exc}")

    # ── Data quality ──────────────────────────────────────────────────────────
    data_quality: dict = {}
    try:
        data_quality = _compute_data_quality()
    except Exception as exc:
        errors.append(f"data quality error: {exc}")

    # ── Diagnosis and recommendations ─────────────────────────────────────────
    diagnosis       = diagnose_alpha_quality(tier_counts, data_quality, coverage)
    recommendations = get_recommendations(diagnosis)

    return {
        "generated_at":             datetime.now().isoformat(),
        "errors":                   errors,
        "summary": {
            "total_unique_scored":  coverage.get("covered_in_db", 0),
            "tier_distribution":    tier_counts,
            "universe_coverage":    coverage,
        },
        "top_candidates":           top_candidates,
        "best_non_predator":        best_non_predator,
        "rejected_predator_alerts": rejected_alerts,
        "rare_setups":              rare_setups,
        "setup_distribution":       setup_distribution,
        "data_quality":             data_quality,
        "diagnosis":                diagnosis,
        "recommendations":          recommendations,
    }


def diagnose_alpha_quality(
    tier_counts: dict,
    data_quality: dict,
    coverage: dict,
) -> list:
    """Return a list of quality-issue dicts: {code, severity, message}.

    Deterministic — same inputs always produce the same output.
    All thresholds are documented inline so they can be tuned.
    """
    issues: list = []
    total = sum(tier_counts.values()) if tier_counts else 0

    if total == 0:
        issues.append({
            "code":     "NO_DATA",
            "severity": "HIGH",
            "message":  "No alpha scores in database — verify ALPHA_SHADOW_ENABLED=true and scanner is running",
        })
        return issues

    ignore_count    = tier_counts.get("IGNORE", 0)
    strong_watch    = tier_counts.get("STRONG_WATCH", 0)
    high_conviction = tier_counts.get("HIGH_CONVICTION", 0)
    rare_setup      = tier_counts.get("RARE_SETUP", 0)
    high_quality    = strong_watch + high_conviction + rare_setup

    ignore_rate = ignore_count / total
    if ignore_rate > 0.60:  # threshold: >60% IGNORE → too strict
        issues.append({
            "code":     "TOO_STRICT",
            "severity": "MEDIUM",
            "message":  (
                f"IGNORE rate is {ignore_rate:.0%} of {total} scored tickers — "
                "scanner may be too strict; consider lowering tier thresholds"
            ),
        })

    # Only flag TOO_LOOSE if we're not already flagging TOO_STRICT
    if total > 5 and high_quality / total < 0.05 and ignore_rate <= 0.60:
        issues.append({
            "code":     "TOO_LOOSE",
            "severity": "MEDIUM",
            "message":  (
                f"Only {high_quality}/{total} tickers reach STRONG_WATCH or above — "
                "scoring may not differentiate opportunities well"
            ),
        })

    if high_quality == 0 and total > 10:
        issues.append({
            "code":     "NO_HIGH_CONVICTION",
            "severity": "HIGH",
            "message":  "No STRONG_WATCH or better found — check universe diversity and scoring calibration",
        })

    mc_rate = data_quality.get("missing_catalyst_rate")
    if mc_rate is not None and mc_rate > 0.50:
        issues.append({
            "code":     "MISSING_CATALYST",
            "severity": "MEDIUM",
            "message":  f"Catalyst data absent in {mc_rate:.0%} of rows — fetch_alpha_input() may need improvement",
        })

    mo_rate = data_quality.get("missing_options_rate")
    if mo_rate is not None and mo_rate > 0.80:
        issues.append({
            "code":     "MISSING_OPTIONS",
            "severity": "LOW",
            "message":  f"Options data absent in {mo_rate:.0%} of rows — expected for many non-optionable tickers",
        })

    mrr_rate = data_quality.get("missing_risk_reward_rate")
    if mrr_rate is not None and mrr_rate > 0.50:
        issues.append({
            "code":     "MISSING_RISK_REWARD",
            "severity": "MEDIUM",
            "message":  f"Risk/reward data absent in {mrr_rate:.0%} of rows — ATR-based fallback in use throughout",
        })

    stale_count   = data_quality.get("stale_count", 0)
    universe_size = max(coverage.get("universe_size", 1), 1)
    if stale_count > universe_size * 0.20:
        issues.append({
            "code":     "STALE_DATA",
            "severity": "MEDIUM",
            "message":  f"{stale_count}/{universe_size} tickers not rescored in 25 h — coverage degrading",
        })

    missing_count = len(coverage.get("missing", []))
    if missing_count > universe_size * 0.30:
        issues.append({
            "code":     "COVERAGE_GAP",
            "severity": "MEDIUM",
            "message":  (
                f"{missing_count}/{universe_size} universe tickers have never been scored — "
                "POST /api/v1/alpha/run-universe to backfill"
            ),
        })

    return issues


def get_recommendations(issues: list) -> list:
    """Return actionable recommendation strings from diagnosis issues.

    Safe self-improvement: recommendations only — no automatic weight changes.
    """
    recs: list = []
    codes = {i["code"] for i in issues}

    if "NO_DATA" in codes:
        recs.append(
            "Set ALPHA_SHADOW_ENABLED=true in Railway env vars; confirm alpha_universe_scan "
            "job is registered in scheduler"
        )
    if "TOO_STRICT" in codes:
        recs.append(
            "Lower the IGNORE tier threshold from 35.0 → 30.0 in alpha_engine.py _TIER_ORDER "
            "to reduce false-negatives"
        )
    if "TOO_LOOSE" in codes or "NO_HIGH_CONVICTION" in codes:
        recs.append(
            "Review component weights — relative_strength and breakout may be underweighted "
            "relative to catalyst and options"
        )
        recs.append(
            "Consider tightening the WATCH → STRONG_WATCH boundary to improve signal-to-noise"
        )
    if "MISSING_CATALYST" in codes:
        recs.append(
            "Add an earnings calendar API or SEC EDGAR 8-K polling to fetch_alpha_input() "
            "to improve catalyst detection"
        )
    if "MISSING_RISK_REWARD" in codes:
        recs.append(
            "Risk/reward relies on ATR fallback — consider volatility-adjusted stop targets "
            "or explicit support-level detection"
        )
    if "STALE_DATA" in codes:
        recs.append(
            "Investigate which tickers consistently fail yfinance fetch — "
            "consider removing persistent failures from ALPHA_UNIVERSE"
        )
    if "COVERAGE_GAP" in codes:
        recs.append(
            "Run POST /api/v1/alpha/run-universe to backfill missing tickers; "
            "check for newly added universe tickers that have not been scored yet"
        )

    return recs


# ── Private helpers ───────────────────────────────────────────────────────────

def _compute_data_quality() -> dict:
    """Analyse most-recent alpha_shadow_log row per ticker for data quality issues."""
    from database import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT a.*
            FROM alpha_shadow_log a
            INNER JOIN (
                SELECT ticker, MAX(scan_time) AS latest
                FROM alpha_shadow_log GROUP BY ticker
            ) g ON a.ticker = g.ticker AND a.scan_time = g.latest
            """
        ).fetchall()
        rows = [dict(r) for r in rows]
    except Exception:
        return {
            "total_scored": 0,
            "missing_catalyst_rate": None,
            "missing_options_rate": None,
            "missing_risk_reward_rate": None,
            "stale_count": 0,
            "stale_tickers": [],
        }
    finally:
        conn.close()

    if not rows:
        return {
            "total_scored": 0,
            "missing_catalyst_rate": None,
            "missing_options_rate": None,
            "missing_risk_reward_rate": None,
            "stale_count": 0,
            "stale_tickers": [],
        }

    stale_cutoff     = (datetime.now() - timedelta(hours=25)).isoformat()
    missing_catalyst = missing_options = missing_rr = 0
    stale_tickers: list = []

    for row in rows:
        if (row.get("scan_time") or "") < stale_cutoff:
            stale_tickers.append(row["ticker"])

        cs_json = row.get("component_scores_json")
        if not cs_json:
            continue
        try:
            cs = json.loads(cs_json)
            if cs.get("catalyst",    {}).get("data_quality") == "MISSING":
                missing_catalyst += 1
            if cs.get("options",     {}).get("data_quality") == "MISSING":
                missing_options  += 1
            if cs.get("risk_reward", {}).get("data_quality") == "MISSING":
                missing_rr       += 1
        except (json.JSONDecodeError, TypeError):
            pass

    n = len(rows)
    return {
        "total_scored":             n,
        "missing_catalyst_rate":    round(missing_catalyst / n, 3) if n else None,
        "missing_options_rate":     round(missing_options  / n, 3) if n else None,
        "missing_risk_reward_rate": round(missing_rr       / n, 3) if n else None,
        "stale_count":              len(stale_tickers),
        "stale_tickers":            stale_tickers[:20],
    }
