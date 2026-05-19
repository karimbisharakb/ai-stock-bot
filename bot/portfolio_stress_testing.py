"""
Phase A18 — Portfolio scenario stress testing.

Stress-tests the current portfolio against 11 scenario types plus CUSTOM.
Advisory only — no trades, no order execution, no broker calls.

Public API (pure — no I/O):
  SCENARIO_TYPES                                                list[str]
  RISK_LEVELS                                                   list[str]
  get_scenario_shock(ticker, scenario_type, custom_overrides)   -> float
  stress_position(position, shock_pct)                          -> dict
  apply_scenario(scenario_type, positions, cash,
                 total_portfolio_value, custom_overrides)        -> dict
  compute_risk_level(loss_pct)                                  -> str
  compute_recommended_actions(scenario_type, loss_pct,
                               risk_level)                      -> list
  compute_aggregate_report(scenario_results, cash,
                            total_portfolio_value)               -> dict

Public API (DB — lazy imports):
  save_stress_run(report, scenario_results)   -> dict
  get_stress_run(run_id)                      -> Optional[dict]
  get_stress_history(limit=20)                -> list

Orchestration:
  run_stress_test(portfolio, custom_scenarios, regime_context)  -> dict
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


# ── Scenario catalogue ─────────────────────────────────────────────────────────

SCENARIO_TYPES: list = [
    "MARKET_PULLBACK_5",
    "MARKET_CORRECTION_10",
    "MARKET_CRASH_20",
    "TECH_SELL_OFF",
    "AI_SEMI_REVERSAL",
    "CRYPTO_RISK_OFF",
    "CANADA_UNDERPERFORMANCE",
    "USD_CAD_MOVE",
    "VOLATILITY_SPIKE",
    "ALPHA_FALSE_POSITIVE_CLUSTER",
    "CUSTOM",
]

RISK_LEVELS: list = ["LOW", "MODERATE", "HIGH", "SEVERE"]

# Thresholds for risk level classification
_RISK_LOW_MAX      =  5.0   # |loss_pct| < 5 % → LOW
_RISK_MODERATE_MAX = 10.0   # |loss_pct| < 10 % → MODERATE
_RISK_HIGH_MAX     = 20.0   # |loss_pct| < 20 % → HIGH
                            # |loss_pct| ≥ 20 % → SEVERE

# Per-scenario shock tables.
# Keys: explicit tickers, "_ai_tech" (theme), "_speculative" (individual stocks),
#       "_cad" (.TO-suffix), "_usd" (non-.TO), "_default" (catch-all).
# Resolution priority: ticker > _ai_tech > _speculative > _cad/_usd > _default.
# Missing key → 0.0 (no shock for that classification).
_SCENARIO_SHOCKS: dict = {
    "MARKET_PULLBACK_5": {
        "_default": -5.0,
    },
    "MARKET_CORRECTION_10": {
        "_default": -10.0,
    },
    "MARKET_CRASH_20": {
        "_default": -20.0,
    },
    "TECH_SELL_OFF": {
        "_ai_tech":  -25.0,
        "_default":   -8.0,
    },
    "AI_SEMI_REVERSAL": {
        "NVDA":      -35.0,
        "AMD":       -35.0,
        "TSM":       -35.0,
        "_ai_tech":  -20.0,
        "_default":   -5.0,
    },
    "CRYPTO_RISK_OFF": {
        "_speculative": -15.0,
        "_default":      -5.0,
    },
    "CANADA_UNDERPERFORMANCE": {
        "_cad":     -12.0,
        "_default":   0.0,
    },
    "USD_CAD_MOVE": {
        # CAD strengthens → USD positions lose purchasing-power value
        "_cad":   0.0,
        "_usd":  -8.0,
    },
    "VOLATILITY_SPIKE": {
        "_speculative": -18.0,
        "_default":     -12.0,
    },
    "ALPHA_FALSE_POSITIVE_CLUSTER": {
        "_speculative": -10.0,
        "_default":      -3.0,
    },
    "CUSTOM": {},
}


# ── Pure helpers (lazy-imported classification from A15) ───────────────────────

def _get_theme(ticker: str) -> str:
    from portfolio_risk_guardrails import get_theme
    return get_theme(ticker)


def _is_speculative(ticker: str) -> bool:
    from portfolio_risk_guardrails import is_speculative
    return is_speculative(ticker)


def _is_cad(ticker: str) -> bool:
    from portfolio_risk_guardrails import is_cad
    return is_cad(ticker)


# ── Pure functions ─────────────────────────────────────────────────────────────

def get_scenario_shock(
    ticker: str,
    scenario_type: str,
    custom_overrides: Optional[dict] = None,
) -> float:
    """Return the shock % (negative = loss) for a ticker under a scenario.

    Resolution order for built-in scenarios:
      1. Ticker-specific key in the shock table
      2. _ai_tech  (theme == AI_TECH)
      3. _speculative (individual stock, not ETF)
      4. _cad / _usd (currency split)
      5. _default

    For CUSTOM, custom_overrides is used directly:
      custom_overrides[ticker] → ticker override
      custom_overrides["_default"] → fallback (default 0.0)
    """
    t = str(ticker).upper()

    if scenario_type == "CUSTOM":
        if not custom_overrides:
            return 0.0
        if t in custom_overrides:
            return float(custom_overrides[t])
        return float(custom_overrides.get("_default", 0.0))

    table = _SCENARIO_SHOCKS.get(scenario_type, {})

    if t in table:
        return float(table[t])

    if "_ai_tech" in table and _get_theme(t) == "AI_TECH":
        return float(table["_ai_tech"])

    if "_speculative" in table and _is_speculative(t):
        return float(table["_speculative"])

    if "_cad" in table or "_usd" in table:
        if _is_cad(t):
            return float(table.get("_cad", 0.0))
        return float(table.get("_usd", table.get("_default", 0.0)))

    return float(table.get("_default", 0.0))


def stress_position(position: dict, shock_pct: float) -> dict:
    """Apply a shock % to a single position and return the stressed view."""
    ticker       = str(position.get("ticker", ""))
    market_value = float(position.get("market_value", 0.0))
    estimated_loss  = market_value * shock_pct / 100.0
    stressed_value  = market_value + estimated_loss
    return {
        "ticker":          ticker,
        "market_value":    round(market_value,   2),
        "shock_pct":       round(shock_pct,       2),
        "estimated_loss":  round(estimated_loss,  2),
        "stressed_value":  round(stressed_value,  2),
    }


def compute_risk_level(loss_pct: float) -> str:
    """Map portfolio loss % to a risk level string."""
    abs_loss = abs(float(loss_pct))
    if abs_loss < _RISK_LOW_MAX:
        return "LOW"
    if abs_loss < _RISK_MODERATE_MAX:
        return "MODERATE"
    if abs_loss < _RISK_HIGH_MAX:
        return "HIGH"
    return "SEVERE"


def compute_recommended_actions(
    scenario_type: str,
    loss_pct: float,
    risk_level: str,
) -> list:
    """Return advisory action strings for a scenario result.  Pure, no I/O."""
    actions: list = []

    if risk_level == "SEVERE":
        actions.append("Consider reducing overall equity exposure immediately")
        actions.append("Review stop-loss levels for all positions")
    elif risk_level == "HIGH":
        actions.append("Review concentration in highest-impact positions")
        actions.append("Consider partial hedges or reduced exposure")
    elif risk_level == "MODERATE":
        actions.append("Monitor positions closely under this scenario")

    if scenario_type in ("TECH_SELL_OFF", "AI_SEMI_REVERSAL"):
        actions.append("Consider diversifying away from technology concentration")
    if scenario_type == "AI_SEMI_REVERSAL":
        actions.append("Evaluate AI/semiconductor exposure relative to portfolio weight")
    if scenario_type == "VOLATILITY_SPIKE":
        actions.append("Avoid new speculative entries during elevated volatility")
    if scenario_type == "CANADA_UNDERPERFORMANCE":
        actions.append("Review CAD-denominated holdings relative to USD allocation")
    if scenario_type == "USD_CAD_MOVE":
        actions.append("Assess currency risk across CAD and USD positions")
    if scenario_type == "CRYPTO_RISK_OFF":
        actions.append("Evaluate speculative position sizing under risk-off conditions")

    return actions


def apply_scenario(
    scenario_type: str,
    positions: list,
    cash: float,
    total_portfolio_value: float,
    custom_overrides: Optional[dict] = None,
) -> dict:
    """Apply a single scenario to all positions and return the scenario result."""
    position_results: list = []
    total_loss = 0.0

    for pos in positions:
        shock  = get_scenario_shock(pos.get("ticker", ""), scenario_type, custom_overrides)
        result = stress_position(pos, shock)
        position_results.append(result)
        total_loss += result["estimated_loss"]

    tpv = float(total_portfolio_value)
    estimated_loss_pct = (total_loss / tpv * 100.0) if tpv > 0 else 0.0
    risk_level = compute_risk_level(estimated_loss_pct)
    actions    = compute_recommended_actions(scenario_type, estimated_loss_pct, risk_level)

    return {
        "scenario_type":         scenario_type,
        "estimated_loss_amount": round(total_loss,          2),
        "estimated_loss_pct":    round(estimated_loss_pct,  2),
        "risk_level":            risk_level,
        "position_results":      position_results,
        "recommended_actions":   actions,
    }


def compute_aggregate_report(
    scenario_results: list,
    cash: float,
    total_portfolio_value: float,
) -> dict:
    """Summarise results across all scenarios.  Pure, no I/O."""
    if not scenario_results:
        return {
            "scenario_count":   0,
            "worst_scenario":   None,
            "worst_loss_pct":   0.0,
            "avg_loss_pct":     0.0,
            "scenarios":        [],
            "warnings":         [],
        }

    worst     = min(scenario_results, key=lambda r: r["estimated_loss_pct"])
    avg_loss  = sum(r["estimated_loss_pct"] for r in scenario_results) / len(scenario_results)

    warnings: list = []
    if worst["risk_level"] == "SEVERE":
        warnings.append(
            f"Worst-case scenario ({worst['scenario_type']}) is SEVERE — "
            f"portfolio loses {abs(worst['estimated_loss_pct']):.1f}%"
        )
    severe_count = sum(1 for r in scenario_results if r["risk_level"] == "SEVERE")
    if severe_count >= 2:
        warnings.append(
            f"{severe_count} scenarios produce SEVERE losses — "
            "portfolio may be concentrated in correlated risk factors"
        )
    high_or_worse = sum(1 for r in scenario_results if r["risk_level"] in ("HIGH", "SEVERE"))
    if high_or_worse >= 3:
        warnings.append(
            f"{high_or_worse} scenarios produce HIGH or worse losses — "
            "consider reviewing overall risk exposure"
        )

    return {
        "scenario_count":   len(scenario_results),
        "worst_scenario":   worst["scenario_type"],
        "worst_loss_pct":   round(worst["estimated_loss_pct"], 2),
        "avg_loss_pct":     round(avg_loss, 2),
        "scenarios":        scenario_results,
        "warnings":         warnings,
    }


# ── Run ID ────────────────────────────────────────────────────────────────────

def _run_id_from_params(portfolio_value: float, created_at: str) -> str:
    raw    = f"{portfolio_value:.4f}:{created_at}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16].upper()
    return f"STR-{digest}"


# ── DDL (safety fallback — migration v21 is primary) ──────────────────────────

_STRESS_DDL = [
    """
    CREATE TABLE IF NOT EXISTS portfolio_stress_runs (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id            TEXT    UNIQUE NOT NULL,
        created_at        TEXT    NOT NULL,
        portfolio_value   REAL    NOT NULL DEFAULT 0.0,
        cash              REAL    NOT NULL DEFAULT 0.0,
        position_count    INTEGER NOT NULL DEFAULT 0,
        scenario_count    INTEGER NOT NULL DEFAULT 0,
        worst_scenario    TEXT,
        worst_loss_pct    REAL,
        avg_loss_pct      REAL,
        warnings_json     TEXT    NOT NULL DEFAULT '[]',
        summary_json      TEXT    NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_stress_events (
        id                        INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id                    TEXT    NOT NULL,
        scenario_type             TEXT    NOT NULL,
        estimated_loss_pct        REAL    NOT NULL,
        estimated_loss_amount     REAL    NOT NULL,
        risk_level                TEXT    NOT NULL,
        position_results_json     TEXT    NOT NULL DEFAULT '[]',
        recommended_actions_json  TEXT    NOT NULL DEFAULT '[]',
        created_at                TEXT    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_psr_run_id     ON portfolio_stress_runs(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_psr_created_at ON portfolio_stress_runs(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_pse_run_id     ON portfolio_stress_events(run_id)",
]


def _ensure_tables() -> None:
    from database import get_connection
    conn = get_connection()
    try:
        for ddl in _STRESS_DDL:
            conn.execute(ddl)
        conn.commit()
    finally:
        conn.close()


# ── DB functions (lazy import pattern) ────────────────────────────────────────

def save_stress_run(report: dict, scenario_results: list) -> dict:
    """Persist a stress-test run + per-scenario events.  Returns the saved run dict."""
    from database import get_connection

    _ensure_tables()

    now        = datetime.now(timezone.utc).isoformat()
    port_value = float(report.get("portfolio_value", 0.0))
    run_id     = _run_id_from_params(port_value, now)

    run_row = {
        "run_id":          run_id,
        "created_at":      now,
        "portfolio_value": port_value,
        "cash":            float(report.get("cash", 0.0)),
        "position_count":  int(report.get("position_count", 0)),
        "scenario_count":  int(report.get("scenario_count", len(scenario_results))),
        "worst_scenario":  report.get("worst_scenario"),
        "worst_loss_pct":  report.get("worst_loss_pct"),
        "avg_loss_pct":    report.get("avg_loss_pct"),
        "warnings_json":   json.dumps(report.get("warnings", [])),
        "summary_json":    json.dumps({
            "scenario_count": report.get("scenario_count", len(scenario_results)),
            "worst_scenario": report.get("worst_scenario"),
            "worst_loss_pct": report.get("worst_loss_pct"),
            "avg_loss_pct":   report.get("avg_loss_pct"),
        }),
    }

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO portfolio_stress_runs
              (run_id, created_at, portfolio_value, cash, position_count, scenario_count,
               worst_scenario, worst_loss_pct, avg_loss_pct, warnings_json, summary_json)
            VALUES
              (:run_id, :created_at, :portfolio_value, :cash, :position_count, :scenario_count,
               :worst_scenario, :worst_loss_pct, :avg_loss_pct, :warnings_json, :summary_json)
            """,
            run_row,
        )

        for sr in scenario_results:
            conn.execute(
                """
                INSERT INTO portfolio_stress_events
                  (run_id, scenario_type, estimated_loss_pct, estimated_loss_amount,
                   risk_level, position_results_json, recommended_actions_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    sr.get("scenario_type", "UNKNOWN"),
                    float(sr.get("estimated_loss_pct",    0.0)),
                    float(sr.get("estimated_loss_amount", 0.0)),
                    sr.get("risk_level", "LOW"),
                    json.dumps(sr.get("position_results",    [])),
                    json.dumps(sr.get("recommended_actions", [])),
                    now,
                ),
            )

        conn.commit()
    finally:
        conn.close()

    return {**run_row, "warnings": report.get("warnings", [])}


def get_stress_run(run_id: str) -> Optional[dict]:
    """Return a stress run dict with embedded scenario events, or None."""
    from database import get_connection

    _ensure_tables()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM portfolio_stress_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["warnings"]     = json.loads(result.pop("warnings_json", "[]"))
        result["summary"]      = json.loads(result.pop("summary_json",  "{}"))

        events = conn.execute(
            "SELECT * FROM portfolio_stress_events WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        result["scenario_events"] = [
            {
                **dict(e),
                "position_results":    json.loads(dict(e)["position_results_json"]),
                "recommended_actions": json.loads(dict(e)["recommended_actions_json"]),
            }
            for e in events
        ]
        return result
    finally:
        conn.close()


def get_stress_history(limit: int = 20) -> list:
    """Return the most recent stress runs (without embedded events), newest first."""
    from database import get_connection

    _ensure_tables()
    safe_limit = min(max(int(limit), 1), 200)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM portfolio_stress_runs ORDER BY created_at DESC LIMIT ?",
            (safe_limit,),
        ).fetchall()
        results = []
        for row in rows:
            r = dict(row)
            r["warnings"] = json.loads(r.pop("warnings_json", "[]"))
            r["summary"]  = json.loads(r.pop("summary_json",  "{}"))
            results.append(r)
        return results
    finally:
        conn.close()


# ── Orchestration ──────────────────────────────────────────────────────────────

def run_stress_test(
    portfolio: Optional[dict]  = None,
    custom_scenarios: Optional[list] = None,
    regime_context: Optional[dict]   = None,
) -> dict:
    """
    Run the full stress test against the current canonical portfolio.

    custom_scenarios: list of custom_overrides dicts, each with optional
      "_label" key and per-ticker shock values.  Each is run as a CUSTOM scenario.

    Returns the aggregate report dict with run_id and created_at populated.
    """
    if portfolio is None:
        from portfolio_reconciliation import get_canonical_portfolio
        portfolio = get_canonical_portfolio()

    positions   = portfolio.get("positions", [])
    aggregates  = portfolio.get("aggregates", {})
    cash        = float(aggregates.get("cash",                    0.0))
    total_value = float(aggregates.get("total_portfolio_value",   0.0))

    scenario_results: list = []

    # Standard scenarios (skip CUSTOM — it needs explicit overrides)
    for st in SCENARIO_TYPES:
        if st == "CUSTOM":
            continue
        result = apply_scenario(st, positions, cash, total_value)
        scenario_results.append(result)

    # User-supplied custom scenarios
    if custom_scenarios:
        for overrides in custom_scenarios:
            label  = overrides.get("_label", "CUSTOM")
            result = apply_scenario("CUSTOM", positions, cash, total_value, custom_overrides=overrides)
            result["scenario_label"] = label
            scenario_results.append(result)

    report = compute_aggregate_report(scenario_results, cash, total_value)
    report["portfolio_value"] = round(total_value, 2)
    report["cash"]            = round(cash, 2)
    report["position_count"]  = len(positions)

    if regime_context:
        report["regime_context"] = regime_context

    saved = save_stress_run(report, scenario_results)
    report["run_id"]     = saved["run_id"]
    report["created_at"] = saved["created_at"]

    return report
