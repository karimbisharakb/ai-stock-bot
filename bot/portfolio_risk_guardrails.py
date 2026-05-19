"""
Phase A15 — Portfolio risk and position-sizing guardrails.

Deterministic position-sizing guidance, concentration checks, and configurable
risk policy for manual portfolio decisions.  Advisory only — no trades,
no broker calls, no order placement.

Public API:
  get_risk_policy()                              -> dict
  update_risk_policy(**kwargs)                   -> dict
  get_theme(ticker)                              -> str       pure
  is_speculative(ticker)                         -> bool      pure
  is_cad(ticker)                                 -> bool      pure
  compute_position_sizing(ticker, ..., policy)   -> dict      pure
  compute_concentration(positions, ..., policy)  -> dict      pure
  get_portfolio_risk_report()                    -> dict
  get_size_check(ticker, decision_type)          -> dict
"""
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


# ── Default risk policy ────────────────────────────────────────────────────────

DEFAULT_POLICY: dict = {
    "max_single_position_pct":  10.0,   # No single position > 10 % of portfolio
    "max_speculative_pct":      20.0,   # Individual stocks ≤ 20 %
    "max_same_theme_pct":       25.0,   # Same theme/sector ≤ 25 %
    "max_expected_loss_pct":     2.0,   # Max loss on any new position ≤ 2 % of portfolio
    "min_cash_reserve_pct":      5.0,   # Keep at least 5 % in cash
    "high_volatility_haircut":   0.5,   # Reduce suggested size 50 % for speculative stocks
    "risk_off_haircut":          0.5,   # Reduce 50 % when risk_off_mode = 1
    "risk_off_mode":               0,   # 0 = normal, 1 = risk-off
}

_POLICY_NUMERIC_FIELDS = frozenset({
    "max_single_position_pct",
    "max_speculative_pct",
    "max_same_theme_pct",
    "max_expected_loss_pct",
    "min_cash_reserve_pct",
    "high_volatility_haircut",
    "risk_off_haircut",
})

_POLICY_INT_FIELDS = frozenset({"risk_off_mode"})


# ── Classification maps ────────────────────────────────────────────────────────

_ETF_TICKERS = frozenset({
    "QQQ", "SPY", "VFV.TO", "XIU.TO", "XQQ.TO",
    "XEQT.TO", "VEQT.TO", "ZQQ.TO", "HXS.TO",
})

_THEME_MAP: dict = {
    # AI / Technology
    "NVDA":    "AI_TECH", "AMD":     "AI_TECH", "MSFT": "AI_TECH",
    "AAPL":    "AI_TECH", "META":    "AI_TECH", "GOOG": "AI_TECH",
    "PLTR":    "AI_TECH", "TSM":     "AI_TECH", "AMZN": "AI_TECH",
    "SHOP.TO": "AI_TECH", "QQQ":     "AI_TECH", "XQQ.TO": "AI_TECH",
    "ZQQ.TO":  "AI_TECH", "HXS.TO":  "AI_TECH",
    # Financials
    "RY.TO": "FINANCIALS", "TD.TO": "FINANCIALS",
    # Energy
    "ENB.TO": "ENERGY", "CNQ.TO": "ENERGY",
    # Broad market / index
    "SPY":     "BROAD_MARKET", "VFV.TO":  "BROAD_MARKET",
    "XIU.TO":  "BROAD_MARKET", "XEQT.TO": "BROAD_MARKET", "VEQT.TO": "BROAD_MARKET",
}


def get_theme(ticker: str) -> str:
    """Return theme/sector for a ticker.  Unknown tickers return 'OTHER'."""
    return _THEME_MAP.get(str(ticker).upper(), "OTHER")


def is_speculative(ticker: str) -> bool:
    """True if ticker is an individual stock (not an ETF)."""
    return str(ticker).upper() not in _ETF_TICKERS


def is_cad(ticker: str) -> bool:
    """True if ticker trades in CAD (has .TO suffix)."""
    return str(ticker).upper().endswith(".TO")


# ── DDL ───────────────────────────────────────────────────────────────────────

_POLICY_DDL = """
CREATE TABLE IF NOT EXISTS risk_policy (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    max_single_position_pct REAL    NOT NULL DEFAULT 10.0,
    max_speculative_pct     REAL    NOT NULL DEFAULT 20.0,
    max_same_theme_pct      REAL    NOT NULL DEFAULT 25.0,
    max_expected_loss_pct   REAL    NOT NULL DEFAULT 2.0,
    min_cash_reserve_pct    REAL    NOT NULL DEFAULT 5.0,
    high_volatility_haircut REAL    NOT NULL DEFAULT 0.5,
    risk_off_haircut        REAL    NOT NULL DEFAULT 0.5,
    risk_off_mode           INTEGER NOT NULL DEFAULT 0,
    updated_at              TEXT    NOT NULL
)
"""


def _ensure_tables() -> None:
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(_POLICY_DDL)
        conn.commit()
    except Exception:
        log.warning("portfolio_risk_guardrails: table creation failed", exc_info=True)
    finally:
        conn.close()


# ── Risk policy CRUD ──────────────────────────────────────────────────────────

def get_risk_policy() -> dict:
    """
    Return the current risk policy from DB, seeding defaults on first call.
    Never raises.
    """
    _ensure_tables()
    try:
        from database import get_connection
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM risk_policy WHERE id=1").fetchone()
            if row is None:
                now = datetime.now().isoformat()
                conn.execute(
                    """INSERT OR IGNORE INTO risk_policy
                       (id, max_single_position_pct, max_speculative_pct,
                        max_same_theme_pct, max_expected_loss_pct, min_cash_reserve_pct,
                        high_volatility_haircut, risk_off_haircut, risk_off_mode, updated_at)
                       VALUES (1,?,?,?,?,?,?,?,?,?)""",
                    (
                        DEFAULT_POLICY["max_single_position_pct"],
                        DEFAULT_POLICY["max_speculative_pct"],
                        DEFAULT_POLICY["max_same_theme_pct"],
                        DEFAULT_POLICY["max_expected_loss_pct"],
                        DEFAULT_POLICY["min_cash_reserve_pct"],
                        DEFAULT_POLICY["high_volatility_haircut"],
                        DEFAULT_POLICY["risk_off_haircut"],
                        DEFAULT_POLICY["risk_off_mode"],
                        now,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM risk_policy WHERE id=1").fetchone()
        finally:
            conn.close()

        policy = dict(DEFAULT_POLICY)
        if row:
            row_dict = dict(row)
            for k in DEFAULT_POLICY:
                if k in row_dict:
                    policy[k] = bool(row_dict[k]) if k in _POLICY_INT_FIELDS else row_dict[k]
            if "updated_at" in row_dict:
                policy["updated_at"] = row_dict["updated_at"]
        return policy
    except Exception:
        log.warning("portfolio_risk_guardrails: get_risk_policy failed", exc_info=True)
        return dict(DEFAULT_POLICY)


def update_risk_policy(**kwargs) -> dict:
    """
    Partial update of risk policy.  Only recognised fields are applied.
    Returns {'ok': True, 'policy': {...}} or {'ok': False, 'errors': [...]}.
    Never raises.
    """
    _ensure_tables()
    get_risk_policy()  # ensure row exists

    valid_fields = _POLICY_NUMERIC_FIELDS | _POLICY_INT_FIELDS
    errors: list  = []
    updates: dict = {}

    for key, val in kwargs.items():
        if key not in valid_fields:
            errors.append(f"UNKNOWN_FIELD:{key}")
            continue
        try:
            updates[key] = int(val) if key in _POLICY_INT_FIELDS else float(val)
        except (TypeError, ValueError):
            errors.append(f"INVALID_VALUE:{key}={val}")

    if errors:
        return {"ok": False, "errors": errors}
    if not updates:
        return {"ok": True, "policy": get_risk_policy()}

    set_clause = ", ".join(f"{k}=?" for k in updates)
    values     = list(updates.values())

    try:
        from database import get_connection
        now  = datetime.now().isoformat()
        conn = get_connection()
        try:
            conn.execute(
                f"UPDATE risk_policy SET {set_clause}, updated_at=? WHERE id=1",
                (*values, now),
            )
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "policy": get_risk_policy()}
    except Exception as exc:
        log.error("portfolio_risk_guardrails: update_risk_policy failed: %s", exc, exc_info=True)
        return {"ok": False, "errors": [f"DB_ERROR:{str(exc)[:100]}"]}


# ── Pure computation functions ────────────────────────────────────────────────

def compute_position_sizing(
    ticker:                 str,
    decision_type:          str,
    current_position_value: float,
    total_portfolio_value:  float,
    available_cash:         float,
    policy:                 dict,
    is_high_volatility:     bool          = False,
    risk_off:               bool          = False,
    stop_price:             Optional[float] = None,
    current_price:          Optional[float] = None,
) -> dict:
    """
    Compute position-sizing guidance.  Pure function — no DB access.

    decision_type: ENTER | ADD | REDUCE | EXIT | HOLD

    Returns dict with:
      current_concentration_pct, max_position_size_cad, remaining_budget_cad,
      suggested_size_cad, max_loss_amount_cad, sizing_tier,
      haircut_applied, haircut_reason, stop_distance_pct, risk_reward_note.

    Sparse-data safe: returns NOT_READY tier when total_portfolio_value ≤ 0.
    """
    # Sparse-data path
    if total_portfolio_value <= 0:
        return {
            "ticker":                    ticker,
            "decision_type":             decision_type,
            "current_position_value":    0.0,
            "current_concentration_pct": 0.0,
            "max_position_size_cad":     0.0,
            "remaining_budget_cad":      0.0,
            "suggested_size_cad":        0.0,
            "max_loss_amount_cad":       0.0,
            "max_portfolio_risk_pct":    policy.get("max_expected_loss_pct", 2.0),
            "sizing_tier":               "NOT_READY",
            "haircut_applied":           False,
            "haircut_reason":            None,
            "stop_distance_pct":         None,
            "risk_reward_note":          "No portfolio data available — size guidance unavailable",
        }

    # For EXIT/REDUCE/HOLD, sizing has a different meaning
    if decision_type in ("EXIT", "REDUCE", "HOLD"):
        note_map = {
            "EXIT":   f"EXIT: sell all of {ticker} ({current_position_value:,.0f} CAD)",
            "REDUCE": f"REDUCE: consider selling 25–50 % of {ticker} position",
            "HOLD":   f"HOLD: no new capital required for {ticker}",
        }
        return {
            "ticker":                    ticker,
            "decision_type":             decision_type,
            "current_position_value":    round(current_position_value, 2),
            "current_concentration_pct": round(current_position_value / total_portfolio_value * 100, 2),
            "max_position_size_cad":     round(total_portfolio_value * policy["max_single_position_pct"] / 100, 2),
            "remaining_budget_cad":      0.0,
            "suggested_size_cad":        round(current_position_value, 2) if decision_type == "EXIT" else 0.0,
            "max_loss_amount_cad":       round(total_portfolio_value * policy["max_expected_loss_pct"] / 100, 2),
            "max_portfolio_risk_pct":    policy["max_expected_loss_pct"],
            "sizing_tier":               "NORMAL",
            "haircut_applied":           False,
            "haircut_reason":            None,
            "stop_distance_pct":         None,
            "risk_reward_note":          note_map[decision_type],
        }

    # ENTER / ADD
    max_single_pct = policy["max_single_position_pct"]
    max_size       = total_portfolio_value * max_single_pct / 100.0
    current_pct    = current_position_value / total_portfolio_value * 100.0
    remaining      = max(0.0, max_size - current_position_value)
    max_loss       = total_portfolio_value * policy["max_expected_loss_pct"] / 100.0

    # Haircut
    haircut        = 0.0
    haircut_reason: Optional[str] = None
    if is_high_volatility and risk_off:
        haircut        = max(policy["high_volatility_haircut"], policy["risk_off_haircut"])
        haircut_reason = "high-volatility stock in RISK_OFF mode"
    elif is_high_volatility:
        haircut        = policy["high_volatility_haircut"]
        haircut_reason = "high-volatility stock"
    elif risk_off:
        haircut        = policy["risk_off_haircut"]
        haircut_reason = "RISK_OFF mode active"

    raw_size  = min(remaining, available_cash)
    suggested = max(0.0, raw_size * (1.0 - haircut))

    # Sizing tier
    if remaining <= 0 or current_pct >= max_single_pct:
        tier = "TOO_RISKY"
    elif current_pct >= 0.75 * max_single_pct:
        tier = "HIGH_CONVICTION_ONLY"
    elif is_high_volatility or risk_off:
        tier = "SMALL_ONLY"
    elif available_cash > 0 and raw_size < 0.25 * max_size:
        tier = "SMALL_ONLY"
    else:
        tier = "NORMAL"

    # Stop distance
    stop_dist: Optional[float] = None
    if stop_price and current_price and current_price > 0 and stop_price < current_price:
        stop_dist = round((current_price - stop_price) / current_price * 100.0, 2)

    # Human-readable note
    if tier == "TOO_RISKY":
        note = (
            f"{ticker} is at or over the {max_single_pct:.0f}% single-position limit "
            f"({current_pct:.1f}% currently)"
        )
    elif tier == "HIGH_CONVICTION_ONLY":
        note = f"{ticker} near position limit ({current_pct:.1f}%) — only add with high conviction"
    elif tier == "SMALL_ONLY":
        reasons = []
        if is_high_volatility:
            reasons.append("high volatility")
        if risk_off:
            reasons.append("RISK_OFF mode")
        if not reasons:
            reasons.append("limited remaining budget")
        note = f"Reduced sizing recommended for {ticker} ({', '.join(reasons)})"
    else:
        note = f"Normal sizing for {ticker} — up to {suggested:,.0f} CAD suggested"

    return {
        "ticker":                    ticker,
        "decision_type":             decision_type,
        "current_position_value":    round(current_position_value, 2),
        "current_concentration_pct": round(current_pct, 2),
        "max_position_size_cad":     round(max_size, 2),
        "remaining_budget_cad":      round(remaining, 2),
        "suggested_size_cad":        round(suggested, 2),
        "max_loss_amount_cad":       round(max_loss, 2),
        "max_portfolio_risk_pct":    policy["max_expected_loss_pct"],
        "sizing_tier":               tier,
        "haircut_applied":           haircut > 0,
        "haircut_reason":            haircut_reason,
        "stop_distance_pct":         stop_dist,
        "risk_reward_note":          note,
    }


def compute_concentration(
    positions:              list,
    total_portfolio_value:  float,
    cash:                   float,
    policy:                 dict,
) -> dict:
    """
    Compute portfolio concentration metrics.  Pure function.

    positions: list of dicts with keys ticker, market_value, concentration_pct.
    Returns cash_pct, speculative_pct, cad_pct, usd_pct, theme_exposure,
    and warning lists.
    """
    if total_portfolio_value <= 0:
        return {
            "cash_pct":               100.0,
            "cash_warning":           None,
            "speculative_pct":        0.0,
            "speculative_warning":    None,
            "cad_pct":                0.0,
            "usd_pct":                0.0,
            "theme_exposure":         {},
            "theme_warnings":         [],
            "concentration_warnings": [],
            "all_warnings":           [],
        }

    cash_pct     = cash / total_portfolio_value * 100.0
    cash_warning: Optional[str] = None
    if cash_pct < policy["min_cash_reserve_pct"]:
        cash_warning = (
            f"Cash {cash_pct:.1f}% is below the "
            f"{policy['min_cash_reserve_pct']:.0f}% minimum reserve"
        )

    # Speculative exposure (individual stocks only)
    spec_value = sum(
        p.get("market_value", 0.0) for p in positions if is_speculative(p["ticker"])
    )
    spec_pct           = spec_value / total_portfolio_value * 100.0
    speculative_warning: Optional[str] = None
    if spec_pct > policy["max_speculative_pct"]:
        speculative_warning = (
            f"Speculative exposure {spec_pct:.1f}% exceeds limit of "
            f"{policy['max_speculative_pct']:.0f}%"
        )

    # CAD / USD split
    cad_value = sum(p.get("market_value", 0.0) for p in positions if is_cad(p["ticker"]))
    usd_value = sum(p.get("market_value", 0.0) for p in positions if not is_cad(p["ticker"]))
    cad_pct   = cad_value / total_portfolio_value * 100.0
    usd_pct   = usd_value / total_portfolio_value * 100.0

    # Theme exposure
    theme_values: dict = {}
    for p in positions:
        theme = get_theme(p["ticker"])
        theme_values[theme] = theme_values.get(theme, 0.0) + p.get("market_value", 0.0)
    theme_pct = {
        t: round(v / total_portfolio_value * 100.0, 1)
        for t, v in theme_values.items()
    }
    theme_warnings = [
        f"Theme {t} exposure {pct:.1f}% exceeds limit of {policy['max_same_theme_pct']:.0f}%"
        for t, pct in theme_pct.items()
        if pct > policy["max_same_theme_pct"]
    ]

    # Single-position concentration warnings
    concentration_warnings = [
        (
            f"{p['ticker']} concentration {p.get('concentration_pct', 0):.1f}% "
            f"exceeds limit of {policy['max_single_position_pct']:.0f}%"
        )
        for p in positions
        if p.get("concentration_pct", 0) > policy["max_single_position_pct"]
    ]

    all_warnings = (
        ([cash_warning]       if cash_warning       else []) +
        ([speculative_warning] if speculative_warning else []) +
        theme_warnings +
        concentration_warnings
    )

    return {
        "cash_pct":               round(cash_pct, 1),
        "cash_warning":           cash_warning,
        "speculative_pct":        round(spec_pct, 1),
        "speculative_warning":    speculative_warning,
        "cad_pct":                round(cad_pct, 1),
        "usd_pct":                round(usd_pct, 1),
        "theme_exposure":         theme_pct,
        "theme_warnings":         theme_warnings,
        "concentration_warnings": concentration_warnings,
        "all_warnings":           all_warnings,
    }


def _compute_risk_score(
    concentration_pct_max: float,
    speculative_pct:       float,
    cash_pct:              float,
    n_warnings:            int,
    policy:                dict,
) -> float:
    """
    Deterministic portfolio risk score 0–100.  Pure function.
    Higher = riskier.  Component breakdown:
      concentration (0–30), speculative exposure (0–25),
      cash reserve (0–20), additional warnings (0–25).
    """
    score = 0.0

    max_pos   = policy["max_single_position_pct"]
    if concentration_pct_max >= max_pos:
        score += 30.0
    elif concentration_pct_max >= 0.75 * max_pos:
        score += 15.0

    max_spec  = policy["max_speculative_pct"]
    if speculative_pct >= max_spec:
        score += 25.0
    elif speculative_pct >= 0.75 * max_spec:
        score += 12.0

    min_cash  = policy["min_cash_reserve_pct"]
    if cash_pct < min_cash:
        score += 20.0
    elif cash_pct < 2 * min_cash:
        score += 10.0

    score += min(n_warnings * 5.0, 25.0)

    return min(100.0, round(score, 1))


def _compute_ticker_risk_table(
    positions:             list,
    total_portfolio_value: float,
    policy:                dict,
) -> list:
    """
    Build per-ticker risk summary sorted by concentration DESC.
    Pure function (no DB calls).
    """
    result = []
    for p in positions:
        ticker   = p["ticker"]
        mv       = p.get("market_value", 0.0)
        conc_pct = p.get("concentration_pct", 0.0)
        flags    = []
        if conc_pct > policy["max_single_position_pct"]:
            flags.append("OVER_CONCENTRATION_LIMIT")
        result.append({
            "ticker":            ticker,
            "market_value":      round(mv, 2),
            "concentration_pct": round(conc_pct, 2),
            "theme":             get_theme(ticker),
            "is_speculative":    is_speculative(ticker),
            "is_cad":            is_cad(ticker),
            "risk_flags":        flags,
        })
    result.sort(key=lambda x: x["concentration_pct"], reverse=True)
    return result


# ── Integrated report and check functions ─────────────────────────────────────

def get_portfolio_risk_report() -> dict:
    """
    Full portfolio risk report.  Reads canonical portfolio and risk policy.
    Never raises.
    """
    _ensure_tables()
    try:
        from portfolio_reconciliation import get_canonical_portfolio
        portfolio  = get_canonical_portfolio()
        positions  = portfolio.get("positions", [])
        aggregates = portfolio.get("aggregates", {})
        policy     = get_risk_policy()

        cash        = aggregates.get("cash", 0.0)
        total_value = aggregates.get("total_portfolio_value", 0.0)

        conc         = compute_concentration(positions, total_value, cash, policy)
        ticker_table = _compute_ticker_risk_table(positions, total_value, policy)

        max_conc_pct = max((p.get("concentration_pct", 0.0) for p in positions), default=0.0)
        risk_score   = _compute_risk_score(
            concentration_pct_max = max_conc_pct,
            speculative_pct       = conc["speculative_pct"],
            cash_pct              = conc["cash_pct"],
            n_warnings            = len(conc["all_warnings"]),
            policy                = policy,
        )

        # Drawdown warning — flag first position > 15 % underwater
        drawdown_warning: Optional[str] = None
        for p in positions:
            pnl_pct = p.get("unrealized_pnl_pct") or 0.0
            if pnl_pct < -15.0:
                drawdown_warning = (
                    f"{p['ticker']} is {pnl_pct:.1f}% below cost — review exit plan"
                )
                break

        # Recommended actions
        actions: list = []
        if conc.get("cash_warning"):
            actions.append("Raise cash to meet minimum reserve requirement")
        if conc.get("speculative_warning"):
            actions.append("Reduce speculative exposure to within policy limit")
        for tw in conc.get("theme_warnings", []):
            theme = tw.split()[1]
            actions.append(f"Review {theme} theme concentration")
        for cw in conc.get("concentration_warnings", []):
            ticker = cw.split()[0]
            actions.append(f"Consider reducing {ticker} position to meet limit")
        if drawdown_warning:
            actions.append("Review exit plans for positions with >15% drawdown")

        return {
            "portfolio_risk_score":   risk_score,
            "concentration_warnings": conc["concentration_warnings"],
            "sizing_warnings":        conc["concentration_warnings"],
            "cash_warning":           conc.get("cash_warning"),
            "drawdown_warning":       drawdown_warning,
            "speculative_pct":        conc["speculative_pct"],
            "cash_pct":               conc["cash_pct"],
            "cad_pct":                conc["cad_pct"],
            "usd_pct":                conc["usd_pct"],
            "theme_exposure":         conc["theme_exposure"],
            "theme_warnings":         conc["theme_warnings"],
            "ticker_risk_table":      ticker_table,
            "recommended_actions":    actions,
            "policy":                 policy,
            "checked_at":             datetime.now().isoformat(),
        }
    except Exception:
        log.error("portfolio_risk_guardrails: get_portfolio_risk_report failed", exc_info=True)
        return {
            "portfolio_risk_score":   0.0,
            "concentration_warnings": [],
            "sizing_warnings":        [],
            "cash_warning":           None,
            "drawdown_warning":       None,
            "speculative_pct":        0.0,
            "cash_pct":               0.0,
            "cad_pct":                0.0,
            "usd_pct":                0.0,
            "theme_exposure":         {},
            "theme_warnings":         [],
            "ticker_risk_table":      [],
            "recommended_actions":    ["Unable to load portfolio data"],
            "policy":                 get_risk_policy(),
            "checked_at":             datetime.now().isoformat(),
        }


def get_size_check(ticker: str, decision_type: str) -> dict:
    """
    Sizing guidance for a specific decision.  Reads canonical portfolio,
    risk policy, and thesis (for stop/invalidation level).
    Never raises.
    """
    _ensure_tables()
    ticker        = str(ticker).strip().upper() if ticker else ""
    decision_type = str(decision_type).strip().upper() if decision_type else "ENTER"

    try:
        from portfolio_reconciliation import get_canonical_portfolio
        portfolio  = get_canonical_portfolio()
        positions  = portfolio.get("positions", [])
        aggregates = portfolio.get("aggregates", {})
        policy     = get_risk_policy()

        cash        = aggregates.get("cash", 0.0)
        total_value = aggregates.get("total_portfolio_value", 0.0)

        current_pos       = next((p for p in positions if p["ticker"] == ticker), None)
        current_pos_value = current_pos["market_value"]  if current_pos else 0.0
        current_price     = current_pos["market_price"]  if current_pos else None

        # Get thesis for stop/invalidation level
        stop_price: Optional[float] = None
        try:
            from position_journal import get_thesis
            thesis = get_thesis(ticker)
            if thesis and thesis.get("invalidation_level"):
                stop_price = float(thesis["invalidation_level"])
        except Exception:
            pass

        hv = is_speculative(ticker)
        ro = bool(policy.get("risk_off_mode", 0))

        sizing = compute_position_sizing(
            ticker                 = ticker,
            decision_type          = decision_type,
            current_position_value = current_pos_value,
            total_portfolio_value  = total_value,
            available_cash         = cash,
            policy                 = policy,
            is_high_volatility     = hv,
            risk_off               = ro,
            stop_price             = stop_price,
            current_price          = current_price,
        )

        conc = compute_concentration(positions, total_value, cash, policy)

        # Blockers
        blockers: list = []
        if sizing["sizing_tier"] == "TOO_RISKY":
            blockers.append(
                f"At or over single-position limit ({policy['max_single_position_pct']:.0f}%)"
            )
        if conc.get("speculative_warning") and is_speculative(ticker):
            blockers.append("Speculative exposure already at policy limit")

        # Warnings
        warnings = list(conc["all_warnings"])
        if sizing.get("haircut_applied"):
            warnings.append(f"Size haircut applied: {sizing['haircut_reason']}")

        # Checklist item suggestions
        position_size_ok  = sizing["sizing_tier"] in ("NORMAL", "HIGH_CONVICTION_ONLY")
        concentration_ok  = len(conc["concentration_warnings"]) == 0
        stop_defined      = bool(stop_price) if stop_price is not None else None

        suggestions = {
            "position_size_reasonable":  position_size_ok,
            "concentration_checked":     concentration_ok,
            "stop_invalidation_defined": stop_defined,
            "risk_reward_acceptable":    None,
        }

        return {
            "ticker":                     ticker,
            "decision_type":              decision_type,
            "sizing_guidance":            sizing,
            "blockers":                   blockers,
            "warnings":                   warnings,
            "checklist_item_suggestions": suggestions,
            "checked_at":                 datetime.now().isoformat(),
        }
    except Exception:
        log.error("portfolio_risk_guardrails: get_size_check failed for %s", ticker, exc_info=True)
        return {
            "ticker":                     ticker,
            "decision_type":              decision_type,
            "sizing_guidance":            {
                "sizing_tier":    "NOT_READY",
                "risk_reward_note": "Error computing sizing guidance",
            },
            "blockers":                   ["Error loading portfolio data"],
            "warnings":                   [],
            "checklist_item_suggestions": {},
            "checked_at":                 datetime.now().isoformat(),
        }
