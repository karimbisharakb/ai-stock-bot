"""
Phase A16 — Market regime intelligence engine.

Classifies market conditions across four dimensions:
  - overall_regime:      RISK_ON / NEUTRAL / RISK_OFF / PANIC
  - volatility_regime:   CALM / ELEVATED / HIGH / EXTREME
  - breadth_regime:      BROAD_STRENGTH / NARROW_STRENGTH / MIXED / WEAK
  - speculative_regime:  SPECULATION_ACTIVE / SELECTIVE / DEFENSIVE / SPECULATION_DEAD

Outputs regime_score (0-100), risk/sizing/threshold/confidence multipliers,
explanation, warnings, and data_quality.

All classification functions are pure (no I/O). Fetching and persistence are
separate and patchable for testing. Never executes trades or sends alerts.
"""
import json
import logging
from typing import Optional

import yfinance as yf

log = logging.getLogger(__name__)

# ── Regime constants ──────────────────────────────────────────────────────────
RISK_ON  = "RISK_ON"
NEUTRAL  = "NEUTRAL"
RISK_OFF = "RISK_OFF"
PANIC    = "PANIC"

CALM     = "CALM"
ELEVATED = "ELEVATED"
HIGH     = "HIGH"
EXTREME  = "EXTREME"

BROAD_STRENGTH  = "BROAD_STRENGTH"
NARROW_STRENGTH = "NARROW_STRENGTH"
MIXED           = "MIXED"
WEAK            = "WEAK"

SPECULATION_ACTIVE = "SPECULATION_ACTIVE"
SELECTIVE          = "SELECTIVE"
DEFENSIVE          = "DEFENSIVE"
SPECULATION_DEAD   = "SPECULATION_DEAD"

OVERALL_REGIMES     = frozenset({RISK_ON, NEUTRAL, RISK_OFF, PANIC})
VOLATILITY_REGIMES  = frozenset({CALM, ELEVATED, HIGH, EXTREME})
BREADTH_REGIMES     = frozenset({BROAD_STRENGTH, NARROW_STRENGTH, MIXED, WEAK})
SPECULATIVE_REGIMES = frozenset({SPECULATION_ACTIVE, SELECTIVE, DEFENSIVE, SPECULATION_DEAD})

# ── VIX thresholds ────────────────────────────────────────────────────────────
_VIX_CALM     = 17.0
_VIX_ELEVATED = 25.0
_VIX_HIGH     = 35.0
_VIX_PANIC    = 45.0

# ── Points band boundaries for overall regime ────────────────────────────────
_POINTS_RISK_ON  =  6.0
_POINTS_NEUTRAL  =  2.0
_POINTS_RISK_OFF = -2.0
# below _POINTS_RISK_OFF → PANIC

# ── Multiplier tables keyed by overall_regime ────────────────────────────────
_RISK_MULT = {RISK_ON: 0.8,  NEUTRAL: 1.0, RISK_OFF: 1.5, PANIC: 2.0}
_SIZE_MULT = {RISK_ON: 1.2,  NEUTRAL: 1.0, RISK_OFF: 0.6, PANIC: 0.3}
_ALPHA_ADJ = {RISK_ON: -5.0, NEUTRAL: 0.0, RISK_OFF: 10.0, PANIC: 15.0}
_CONF_ADJ  = {RISK_ON:  5.0, NEUTRAL: 0.0, RISK_OFF: -10.0, PANIC: -20.0}

# ── Regime score midpoints (0-100) ────────────────────────────────────────────
_SCORE_MID = {PANIC: 8.0, RISK_OFF: 26.0, NEUTRAL: 50.0, RISK_ON: 80.0}


# ── Table setup (idempotent safety fallback — migration v19 is primary) ───────

def _ensure_tables() -> None:
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_regime_snapshots (
                id                         INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at                TEXT    NOT NULL,
                overall_regime             TEXT    NOT NULL,
                volatility_regime          TEXT    NOT NULL,
                breadth_regime             TEXT    NOT NULL,
                speculative_regime         TEXT    NOT NULL,
                regime_score               REAL    NOT NULL,
                risk_multiplier            REAL    NOT NULL,
                sizing_multiplier          REAL    NOT NULL,
                alpha_threshold_adjustment REAL    NOT NULL,
                confidence_adjustment      REAL    NOT NULL,
                explanation                TEXT    NOT NULL,
                warnings_json              TEXT    NOT NULL DEFAULT '[]',
                data_quality               TEXT    NOT NULL,
                raw_signals_json           TEXT    NOT NULL DEFAULT '{}'
            )
        """)
        conn.commit()
    finally:
        conn.close()


# ── Pure signal scoring ───────────────────────────────────────────────────────

def _compute_points(signals: dict) -> float:
    """
    Score market signals on a continuous scale.
    Higher = more risk-on.  All inputs are optional (None = skip).
    """
    pts = 0.0

    spy_vs_200 = signals.get("spy_vs_200sma")
    qqq_vs_200 = signals.get("qqq_vs_200sma")
    spy_vs_50  = signals.get("spy_vs_50sma")
    spy_vs_20  = signals.get("spy_vs_20sma")
    iwm_vs_200 = signals.get("iwm_vs_200sma")
    vix        = signals.get("vix")
    vix_change = signals.get("vix_change_pct")
    btc_7d     = signals.get("btc_7d_return_pct")
    sox_vs_50  = signals.get("sox_vs_50sma")
    tsx_vs_200 = signals.get("tsx_vs_200sma")

    if spy_vs_200 is not None:
        pts += 2.0 if spy_vs_200 > 0 else -2.0
    if qqq_vs_200 is not None:
        pts += 2.0 if qqq_vs_200 > 0 else -2.0
    if spy_vs_50 is not None:
        pts += 1.0 if spy_vs_50 > 0 else -1.0
    if spy_vs_20 is not None:
        pts += 1.0 if spy_vs_20 > 1.5 else (-1.0 if spy_vs_20 < -1.5 else 0.0)
    if iwm_vs_200 is not None:
        pts += 1.0 if iwm_vs_200 > 0 else -1.0
    if btc_7d is not None:
        pts += 1.0 if btc_7d > 5.0 else (-1.0 if btc_7d < -10.0 else 0.0)
    if sox_vs_50 is not None:
        pts += 1.0 if sox_vs_50 > 0 else -1.0
    if tsx_vs_200 is not None:
        pts += 0.5 if tsx_vs_200 > 0 else -0.5

    # VIX contribution
    if vix is not None:
        if vix < 15:
            pts += 2.0
        elif vix < 20:
            pts += 1.0
        elif vix < 25:
            pts += 0.0
        elif vix < 30:
            pts -= 1.0
        elif vix < 35:
            pts -= 2.0
        elif vix < 45:
            pts -= 3.0
        else:
            pts -= 5.0

    # Rapid VIX spike penalty
    if vix_change is not None and vix_change > 20:
        pts -= 1.0

    return round(pts, 2)


# ── Pure classifiers ──────────────────────────────────────────────────────────

def _classify_overall(points: float, vix: Optional[float]) -> str:
    """Classify overall market regime from points score and VIX."""
    if vix is not None and vix >= _VIX_PANIC:
        return PANIC
    if points < _POINTS_RISK_OFF:
        return PANIC
    if points < _POINTS_NEUTRAL:
        return RISK_OFF
    if points < _POINTS_RISK_ON:
        return NEUTRAL
    return RISK_ON


def _classify_volatility(vix: Optional[float]) -> str:
    """Classify volatility regime from VIX level. Defaults to ELEVATED when unavailable."""
    if vix is None:
        return ELEVATED
    if vix < _VIX_CALM:
        return CALM
    if vix < _VIX_ELEVATED:
        return ELEVATED
    if vix < _VIX_HIGH:
        return HIGH
    return EXTREME


def _classify_breadth(
    spy_above_200: Optional[bool],
    qqq_above_200: Optional[bool],
    iwm_above_200: Optional[bool],
) -> str:
    """
    Classify market breadth from large-cap and small-cap relative strength.
    IWM below 200 SMA while large-caps lead signals narrow (tech-only) strength.
    """
    if spy_above_200 is None and qqq_above_200 is None and iwm_above_200 is None:
        return MIXED

    large_above = sum(1 for v in [spy_above_200, qqq_above_200] if v is True)
    large_below = sum(1 for v in [spy_above_200, qqq_above_200] if v is False)

    if large_above == 2 and iwm_above_200 is True:
        return BROAD_STRENGTH
    if large_above >= 1 and iwm_above_200 is False:
        return NARROW_STRENGTH
    if large_below == 2 and iwm_above_200 is False:
        return WEAK
    return MIXED


def _classify_speculative(
    btc_positive: Optional[bool],
    sox_above_50: Optional[bool],
    iwm_trending_up: Optional[bool],
) -> str:
    """Classify speculative appetite from crypto, semis, and small-cap momentum."""
    active = sum(1 for v in [btc_positive, sox_above_50, iwm_trending_up] if v is True)
    dead   = sum(1 for v in [btc_positive, sox_above_50, iwm_trending_up] if v is False)

    if active >= 2:
        return SPECULATION_ACTIVE
    if active == 1 and dead <= 1:
        return SELECTIVE
    if dead >= 2:
        return SPECULATION_DEAD
    return DEFENSIVE


def _compute_regime_score(overall: str, points: float, vix: Optional[float]) -> float:
    """Map overall regime + points to a 0-100 regime score (higher = more risk-on)."""
    base = _SCORE_MID.get(overall, 50.0)

    if overall == RISK_ON:
        # 66-100 band
        offset = min((points - _POINTS_RISK_ON) / 6.0, 1.0) * 20.0
        score = base + offset
    elif overall == NEUTRAL:
        # 36-65 band
        span = _POINTS_RISK_ON - _POINTS_NEUTRAL
        offset = (points - _POINTS_NEUTRAL) / span * 15.0 if span else 0.0
        score = base + offset
    elif overall == RISK_OFF:
        # 16-35 band
        span = _POINTS_NEUTRAL - _POINTS_RISK_OFF
        offset = (points - _POINTS_RISK_OFF) / span * 10.0 if span else 0.0
        score = base + offset
    else:  # PANIC
        # 0-15 band
        offset = max((points + 5.0) / 3.0, 0.0) * 5.0
        score = base + offset

    if vix is not None and vix > 60:
        score -= 5.0

    return round(max(0.0, min(100.0, score)), 1)


def _compute_multipliers(overall: str, volatility: str) -> dict:
    """Return risk/sizing/threshold/confidence multipliers for the given regimes."""
    risk_mult = _RISK_MULT.get(overall, 1.0)
    size_mult = _SIZE_MULT.get(overall, 1.0)
    alpha_adj = _ALPHA_ADJ.get(overall, 0.0)
    conf_adj  = _CONF_ADJ.get(overall, 0.0)

    if volatility == EXTREME:
        size_mult = round(size_mult * 0.8, 3)
        risk_mult = round(risk_mult * 1.2, 3)
    elif volatility == HIGH and overall != PANIC:
        size_mult = round(size_mult * 0.9, 3)

    return {
        "risk_multiplier":            round(max(0.1, risk_mult), 3),
        "sizing_multiplier":          round(max(0.1, min(2.0, size_mult)), 3),
        "alpha_threshold_adjustment": round(alpha_adj, 1),
        "confidence_adjustment":      round(conf_adj, 1),
    }


def _assess_data_quality(signals: dict) -> str:
    """Return GOOD / PARTIAL / SPARSE based on availability of key signals."""
    key_signals = [
        "spy_vs_200sma", "qqq_vs_200sma", "spy_vs_50sma",
        "spy_vs_20sma", "iwm_vs_200sma", "vix",
    ]
    present = sum(1 for k in key_signals if signals.get(k) is not None)
    if present >= 5:
        return "GOOD"
    if present >= 3:
        return "PARTIAL"
    return "SPARSE"


def _build_explanation(
    overall: str,
    volatility: str,
    breadth: str,
    speculative: str,
    points: float,
    signals: dict,
) -> str:
    parts = [f"Overall: {overall} (score={points:.1f})"]
    vix = signals.get("vix")
    if vix is not None:
        parts.append(f"VIX={vix:.1f} -> {volatility}")
    else:
        parts.append(f"VIX=unavailable -> {volatility} (default)")
    parts.append(f"Breadth: {breadth}")
    parts.append(f"Speculative: {speculative}")
    return " | ".join(parts)


def _build_warnings(overall: str, volatility: str, breadth: str, signals: dict) -> list:
    w = []
    vix        = signals.get("vix")
    vix_change = signals.get("vix_change_pct")
    spy_vs_200 = signals.get("spy_vs_200sma")

    if overall == PANIC:
        w.append("PANIC: extreme risk-off conditions — minimize new positions")
    elif overall == RISK_OFF:
        w.append("RISK_OFF: risk-off conditions — exercise caution with new entries")

    if volatility == EXTREME:
        w.append("EXTREME volatility: VIX in extreme territory")
    elif volatility == HIGH:
        w.append("HIGH volatility: elevated VIX may cause oversized moves")

    if breadth == NARROW_STRENGTH:
        w.append("NARROW breadth: only large-cap leadership — small-caps lagging")
    elif breadth == WEAK:
        w.append("WEAK breadth: broad market deterioration")

    if vix_change is not None and vix_change > 30:
        w.append(f"VIX spiking fast (+{vix_change:.0f}%) — panic possible")

    if spy_vs_200 is not None and spy_vs_200 < -10:
        w.append(f"SPY far below 200 SMA ({spy_vs_200:.1f}%) — bear market territory")

    return w


# ── Core pure function ────────────────────────────────────────────────────────

def compute_regime(signals: dict) -> dict:
    """
    Pure function: classify regime from a signals dict.
    No I/O.  All signal values may be None (sparse-safe).
    Returns all regime dimensions, score, multipliers, explanation, warnings,
    data_quality, and raw points.
    """
    if not signals:
        signals = {}

    vix        = signals.get("vix")
    points     = _compute_points(signals)

    spy_vs_200 = signals.get("spy_vs_200sma")
    qqq_vs_200 = signals.get("qqq_vs_200sma")
    iwm_vs_200 = signals.get("iwm_vs_200sma")
    btc_7d     = signals.get("btc_7d_return_pct")
    sox_vs_50  = signals.get("sox_vs_50sma")
    iwm_vs_50  = signals.get("iwm_vs_50sma")

    spy_above_200   = None if spy_vs_200 is None else spy_vs_200 > 0
    qqq_above_200   = None if qqq_vs_200 is None else qqq_vs_200 > 0
    iwm_above_200   = None if iwm_vs_200 is None else iwm_vs_200 > 0
    btc_positive    = None if btc_7d    is None else btc_7d    > 0
    sox_above_50_b  = None if sox_vs_50 is None else sox_vs_50 > 0
    iwm_trend_ref   = iwm_vs_50 if iwm_vs_50 is not None else iwm_vs_200
    iwm_trending_up = None if iwm_trend_ref is None else iwm_trend_ref > 1.0

    overall     = _classify_overall(points, vix)
    volatility  = _classify_volatility(vix)
    breadth     = _classify_breadth(spy_above_200, qqq_above_200, iwm_above_200)
    speculative = _classify_speculative(btc_positive, sox_above_50_b, iwm_trending_up)

    score = _compute_regime_score(overall, points, vix)
    mults = _compute_multipliers(overall, volatility)

    return {
        "overall_regime":              overall,
        "volatility_regime":           volatility,
        "breadth_regime":              breadth,
        "speculative_regime":          speculative,
        "regime_score":                score,
        "risk_multiplier":             mults["risk_multiplier"],
        "sizing_multiplier":           mults["sizing_multiplier"],
        "alpha_threshold_adjustment":  mults["alpha_threshold_adjustment"],
        "confidence_adjustment":       mults["confidence_adjustment"],
        "explanation":                 _build_explanation(overall, volatility, breadth, speculative, points, signals),
        "warnings":                    _build_warnings(overall, volatility, breadth, signals),
        "data_quality":                _assess_data_quality(signals),
        "points":                      points,
    }


# ── Data fetchers (module-level — patchable in tests) ─────────────────────────

def _fetch_pct_vs_sma(symbol: str, sma_period: int, lookback: str = "1y") -> Optional[float]:
    """Return pct deviation of latest close vs its N-day SMA; None on any failure."""
    try:
        hist = yf.Ticker(symbol).history(period=lookback)["Close"]
        if len(hist) < sma_period:
            return None
        price = float(hist.iloc[-1])
        sma   = float(hist.tail(sma_period).mean())
        return round((price / sma - 1.0) * 100.0, 2)
    except Exception as exc:
        log.debug("_fetch_pct_vs_sma(%s, %d): %s", symbol, sma_period, exc)
        return None


def _fetch_vix() -> Optional[float]:
    try:
        hist = yf.Ticker("^VIX").history(period="5d")["Close"]
        if hist.empty:
            return None
        return round(float(hist.iloc[-1]), 1)
    except Exception as exc:
        log.debug("_fetch_vix: %s", exc)
        return None


def _fetch_vix_change() -> Optional[float]:
    """VIX % change over last 5 sessions; None on failure."""
    try:
        hist = yf.Ticker("^VIX").history(period="10d")["Close"]
        if len(hist) < 5:
            return None
        old = float(hist.iloc[-5])
        new = float(hist.iloc[-1])
        if old == 0:
            return None
        return round((new / old - 1.0) * 100.0, 1)
    except Exception as exc:
        log.debug("_fetch_vix_change: %s", exc)
        return None


def _fetch_btc_7d() -> Optional[float]:
    """BTC-USD 7-day return %; None on failure."""
    try:
        hist = yf.Ticker("BTC-USD").history(period="15d")["Close"]
        if len(hist) < 7:
            return None
        old = float(hist.iloc[-7])
        new = float(hist.iloc[-1])
        if old == 0:
            return None
        return round((new / old - 1.0) * 100.0, 1)
    except Exception as exc:
        log.debug("_fetch_btc_7d: %s", exc)
        return None


def _fetch_signals() -> dict:
    """
    Fetch all market signals. Each value may be None (sparse-safe).
    Module-level so tests can monkeypatch it.
    """
    return {
        "spy_vs_20sma":      _fetch_pct_vs_sma("SPY",    20,  "3mo"),
        "spy_vs_50sma":      _fetch_pct_vs_sma("SPY",    50,  "6mo"),
        "spy_vs_200sma":     _fetch_pct_vs_sma("SPY",   200,  "1y"),
        "qqq_vs_200sma":     _fetch_pct_vs_sma("QQQ",   200,  "1y"),
        "iwm_vs_200sma":     _fetch_pct_vs_sma("IWM",   200,  "1y"),
        "iwm_vs_50sma":      _fetch_pct_vs_sma("IWM",    50,  "6mo"),
        "sox_vs_50sma":      _fetch_pct_vs_sma("SOXX",   50,  "6mo"),
        "tsx_vs_200sma":     _fetch_pct_vs_sma("XIU.TO", 200, "1y"),
        "vix":               _fetch_vix(),
        "vix_change_pct":    _fetch_vix_change(),
        "btc_7d_return_pct": _fetch_btc_7d(),
    }


# ── Persistence (immutable append-only rows) ──────────────────────────────────

def save_regime_snapshot(regime: dict) -> dict:
    """
    Persist a regime dict as an immutable snapshot row.
    Returns the saved dict with id and captured_at added.
    """
    _ensure_tables()
    from datetime import datetime, timezone
    from database import get_connection
    captured_at = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO market_regime_snapshots
                (captured_at, overall_regime, volatility_regime, breadth_regime,
                 speculative_regime, regime_score, risk_multiplier, sizing_multiplier,
                 alpha_threshold_adjustment, confidence_adjustment,
                 explanation, warnings_json, data_quality, raw_signals_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                captured_at,
                regime["overall_regime"],
                regime["volatility_regime"],
                regime["breadth_regime"],
                regime["speculative_regime"],
                regime["regime_score"],
                regime["risk_multiplier"],
                regime["sizing_multiplier"],
                regime["alpha_threshold_adjustment"],
                regime["confidence_adjustment"],
                regime["explanation"],
                json.dumps(regime.get("warnings", [])),
                regime["data_quality"],
                json.dumps(regime.get("raw_signals", {})),
            ),
        )
        conn.commit()
        row_id = cur.lastrowid
    finally:
        conn.close()

    return {**regime, "id": row_id, "captured_at": captured_at}


def get_latest_regime() -> Optional[dict]:
    """Return the most recent snapshot row, or None if none exist."""
    _ensure_tables()
    from database import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM market_regime_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    return None if row is None else _row_to_dict(row)


def get_regime_history(limit: int = 20) -> list:
    """Return up to `limit` recent snapshots, newest first."""
    _ensure_tables()
    from database import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM market_regime_snapshots ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["warnings"]    = json.loads(d.pop("warnings_json", "[]"))
    d["raw_signals"] = json.loads(d.pop("raw_signals_json", "{}"))
    return d


# ── Integrated refresh ────────────────────────────────────────────────────────

def refresh_regime() -> dict:
    """
    Fetch live signals, classify regime, persist snapshot.
    Sparse-safe: if fetching fails entirely, classifies with empty signals.
    """
    try:
        signals = _fetch_signals()
    except Exception as exc:
        log.warning("refresh_regime: _fetch_signals failed: %s", exc)
        signals = {}

    regime = compute_regime(signals)
    regime["raw_signals"] = signals

    try:
        saved = save_regime_snapshot(regime)
        log.info(
            "Regime refreshed: %s | score=%.1f | vix=%s | quality=%s",
            saved["overall_regime"],
            saved["regime_score"],
            signals.get("vix"),
            saved["data_quality"],
        )
        return saved
    except Exception as exc:
        log.error("refresh_regime: save failed: %s", exc)
        return regime


# ── Context accessors for downstream consumers ────────────────────────────────

def get_regime_context_for_checklist() -> dict:
    """
    Compact regime summary for decision checklist context.
    Always returns a dict; never raises (sparse-safe).
    """
    try:
        latest = get_latest_regime()
        if latest is None:
            return {
                "available":      False,
                "overall_regime": NEUTRAL,
                "regime_score":   50.0,
                "warnings":       [],
                "note":           "No regime snapshot available — POST /api/v1/market/regime/refresh",
            }
        return {
            "available":          True,
            "overall_regime":     latest["overall_regime"],
            "volatility_regime":  latest["volatility_regime"],
            "breadth_regime":     latest["breadth_regime"],
            "speculative_regime": latest["speculative_regime"],
            "regime_score":       latest["regime_score"],
            "sizing_multiplier":  latest["sizing_multiplier"],
            "warnings":           latest.get("warnings", []),
            "captured_at":        latest.get("captured_at"),
            "data_quality":       latest.get("data_quality"),
        }
    except Exception as exc:
        log.warning("get_regime_context_for_checklist: %s", exc)
        return {"available": False, "overall_regime": NEUTRAL, "regime_score": 50.0, "warnings": []}


def get_regime_context_for_risk() -> dict:
    """
    Regime multipliers and flags for portfolio risk guardrails.
    Always returns a dict; never raises (sparse-safe).
    """
    try:
        latest = get_latest_regime()
        if latest is None:
            return {
                "available":                  False,
                "overall_regime":             NEUTRAL,
                "risk_multiplier":            1.0,
                "sizing_multiplier":          1.0,
                "alpha_threshold_adjustment": 0.0,
                "confidence_adjustment":      0.0,
                "is_risk_off":                False,
                "is_panic":                   False,
                "is_high_volatility":         False,
            }
        overall    = latest["overall_regime"]
        volatility = latest["volatility_regime"]
        return {
            "available":                  True,
            "overall_regime":             overall,
            "volatility_regime":          volatility,
            "risk_multiplier":            latest["risk_multiplier"],
            "sizing_multiplier":          latest["sizing_multiplier"],
            "alpha_threshold_adjustment": latest["alpha_threshold_adjustment"],
            "confidence_adjustment":      latest["confidence_adjustment"],
            "is_risk_off":                overall in (RISK_OFF, PANIC),
            "is_panic":                   overall == PANIC,
            "is_high_volatility":         volatility in (HIGH, EXTREME),
            "captured_at":                latest.get("captured_at"),
        }
    except Exception as exc:
        log.warning("get_regime_context_for_risk: %s", exc)
        return {
            "available":                  False,
            "overall_regime":             NEUTRAL,
            "risk_multiplier":            1.0,
            "sizing_multiplier":          1.0,
            "alpha_threshold_adjustment": 0.0,
            "confidence_adjustment":      0.0,
            "is_risk_off":                False,
            "is_panic":                   False,
            "is_high_volatility":         False,
        }
