"""
Phase A6 — Reality validation layer.

Classifies completed alpha_outcomes into behavior classes and computes a
validation score from deterministic price-series metrics.  Prevents the
learning engine from treating noisy speculative moves as genuine signals.

Observation-only: reads COMPLETE alpha_outcomes rows, writes to alpha_validation.
Never mutates live Alpha scoring.  Never sends alerts.  Never affects Predator.

Public API:
  validate_outcome(row)              -> dict  (pure — no DB access)
  validate_all_outcomes()            -> dict
  get_validations(limit, setup_type, behavior_class) -> list
  get_validation_summary()           -> dict
  compute_setup_trap_rates(vs)       -> dict
  compute_setup_sustainability(vs)   -> dict
  get_validation_metrics_for_proposals() -> dict
"""
import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

# ── Behavior classes ───────────────────────────────────────────────────────────

BEHAVIOR_CLASSES = (
    "VALID_BREAKOUT",
    "FAILED_BREAKOUT",
    "SHORT_LIVED_SPIKE",
    "SUSTAINED_TREND",
    "VOLATILITY_TRAP",
    "FAILED_SQUEEZE",
    "INSTITUTIONAL_ACCUMULATION",
    "MEAN_REVERSION",
    "INCONCLUSIVE",
)

# Behavior quality buckets (used by learning integration for quality weighting)
POSITIVE_BEHAVIORS = frozenset({"VALID_BREAKOUT", "SUSTAINED_TREND", "INSTITUTIONAL_ACCUMULATION"})
NEGATIVE_BEHAVIORS = frozenset({"VOLATILITY_TRAP", "FAILED_SQUEEZE", "SHORT_LIVED_SPIKE"})
MIXED_BEHAVIORS    = frozenset({"FAILED_BREAKOUT", "MEAN_REVERSION"})

# Learning quality weights per behavior class
VALIDATION_QUALITY_WEIGHTS: dict = {
    "VALID_BREAKOUT":             1.5,
    "SUSTAINED_TREND":            1.5,
    "INSTITUTIONAL_ACCUMULATION": 1.3,
    "FAILED_BREAKOUT":            0.75,
    "MEAN_REVERSION":             0.75,
    "INCONCLUSIVE":               1.0,
    "VOLATILITY_TRAP":            0.4,
    "FAILED_SQUEEZE":             0.4,
    "SHORT_LIVED_SPIKE":          0.4,
}

# ── Metric weights for 0-100 validation score ─────────────────────────────────

_METRIC_WEIGHTS = {
    "follow_through":       0.22,
    "gain_retention":       0.20,
    "drawdown_score":       0.15,
    "continuation_quality": 0.18,
    "consistency":          0.10,
    "sustained_strength":   0.10,
    "reversal_score":       0.05,
}
assert abs(sum(_METRIC_WEIGHTS.values()) - 1.0) < 1e-9

# ── Classification thresholds ─────────────────────────────────────────────────

_MIN_WINDOWS          = 2      # need ≥2 price windows to classify
_SPIKE_MIN_GAIN_1D    = 0.030  # r1d > 3% for spike candidate
_SPIKE_MIN_PEAK       = 0.040  # max_gain > 4%
_SPIKE_COLLAPSE_RATIO = 0.45   # r5d / r1d < this → spike collapsed
_TRAP_MIN_PEAK        = 0.030  # max_gain > 3% for trap
_TRAP_MIN_DD          = -0.04  # max_drawdown < -4% for trap
_TRAP_DD_TO_GAIN      = 0.75   # |max_drawdown| / max_gain > this → trap
_TREND_CQ_THRESHOLD   = 0.60   # continuation_quality ≥ 60%
_TREND_MIN_5D         = 0.020  # return_5d > 2%
_TREND_MIN_20D        = 0.020  # return_20d > 2%
_BREAKOUT_MIN_FT      = 0.50   # follow_through ≥ 50%
_BREAKOUT_MIN_5D      = 0.010  # return_5d > 1%
_BREAKOUT_MIN_GR      = 0.25   # gain_retention > 25%
_ACCUM_MAX_GAIN       = 0.060  # max_gain < 6%
_ACCUM_MAX_DD         = 0.040  # |max_drawdown| < 4%
_ACCUM_MIN_5D         = 0.0    # return_5d ≥ 0
_ACCUM_MIN_20D        = 0.010  # return_20d > 1%


# ── Metric computation ────────────────────────────────────────────────────────

def _compute_metrics(row: dict) -> dict:
    """
    Compute all validation metrics from an outcome row.

    All computations are deterministic arithmetic.
    Returns dict with metric values; each may be None when data is absent.
    """
    r1d  = row.get("return_1d")
    r3d  = row.get("return_3d")
    r5d  = row.get("return_5d")
    r10d = row.get("return_10d")
    r20d = row.get("return_20d")
    mg   = row.get("max_gain")
    md   = row.get("max_drawdown")

    windows: dict = {}
    for w, v in ((1, r1d), (3, r3d), (5, r5d), (10, r10d), (20, r20d)):
        if v is not None:
            windows[w] = v
    n = len(windows)

    # 1. Follow-through persistence: fraction of windows after the 1st that are positive
    later = [v for k, v in windows.items() if k > 1]
    follow_through: Optional[float] = (
        sum(1 for v in later if v > 0) / len(later) if later else None
    )

    # 2. Gain retention: how much of max_gain is retained at 5d
    if mg is not None and mg > 0.005 and r5d is not None:
        gain_retention: Optional[float] = max(0.0, min(1.0, r5d / mg))
    else:
        gain_retention = None

    # 3. Drawdown score (1 = no drawdown, 0 = drawdown = 100% of peak)
    if mg is not None and mg > 0.005 and md is not None and md < 0:
        dd_ratio = (-md) / mg
        drawdown_score: Optional[float] = max(0.0, 1.0 - dd_ratio)
    elif md is not None and md >= 0:
        drawdown_score = 1.0
    else:
        drawdown_score = None

    # 4. Continuation quality: fraction of consecutive window steps that are non-decreasing
    ordered = sorted(windows.items())
    if len(ordered) >= 2:
        pairs = [(ordered[i][1], ordered[i + 1][1]) for i in range(len(ordered) - 1)]
        continuation_quality: Optional[float] = sum(1 for a, b in pairs if b >= a) / len(pairs)
    else:
        continuation_quality = None

    # 5. Multi-window consistency: 1 - normalised std-dev (low spread = high consistency)
    if n >= 3:
        vals = list(windows.values())
        mean = sum(vals) / n
        variance = sum((v - mean) ** 2 for v in vals) / n
        stddev = variance ** 0.5
        consistency: Optional[float] = max(0.0, 1.0 - stddev / 0.20)
    else:
        consistency = None

    # 6. Sustained strength: how well 5d gains persist to 20d
    if r5d is not None and r20d is not None:
        if r5d > 0 and r20d >= r5d:
            sustained_strength: Optional[float] = 1.0       # accelerating
        elif r5d > 0 and r20d > 0:
            sustained_strength = min(1.0, r20d / r5d)
        elif r5d > 0:
            sustained_strength = 0.0                        # reversed
        elif r20d > 0:
            sustained_strength = 0.5                        # recovered
        else:
            denom = abs(r5d) + 0.001
            sustained_strength = max(0.0, 1.0 + r20d / denom)
    else:
        sustained_strength = None

    # 7. Reversal score: how much of peak gain is retained at 20d (1 = full retention)
    if mg is not None and mg > 0.005 and r20d is not None:
        if r20d >= 0:
            reversal_score: Optional[float] = min(1.0, r20d / mg)
        else:
            reversal_score = max(0.0, 1.0 - (-r20d) / (mg + 0.001))
    elif r20d is not None and r20d > 0:
        reversal_score = 0.7
    else:
        reversal_score = None

    return {
        "n_windows":            n,
        "follow_through":       follow_through,
        "gain_retention":       gain_retention,
        "drawdown_score":       drawdown_score,
        "continuation_quality": continuation_quality,
        "consistency":          consistency,
        "sustained_strength":   sustained_strength,
        "reversal_score":       reversal_score,
    }


def _compute_validation_score(metrics: dict) -> float:
    """Weighted average of available metrics → 0-100."""
    total_weight = 0.0
    total = 0.0
    for metric, weight in _METRIC_WEIGHTS.items():
        val = metrics.get(metric)
        if val is not None:
            total += val * weight
            total_weight += weight
    if total_weight < 1e-9:
        return 0.0
    return round((total / total_weight) * 100.0, 1)


# ── Behavior classification ────────────────────────────────────────────────────

def _classify_behavior(row: dict, metrics: dict, setup_type: str) -> str:
    """
    Deterministic, priority-ordered classification of post-signal behavior.

    Priority ensures the most specific and unambiguous class wins when
    multiple conditions could match.
    """
    n    = metrics["n_windows"]
    if n < _MIN_WINDOWS:
        return "INCONCLUSIVE"

    r1d  = row.get("return_1d")  or 0.0
    r5d  = row.get("return_5d")  or 0.0
    r20d = row.get("return_20d") or 0.0
    mg   = row.get("max_gain")   or 0.0
    md   = row.get("max_drawdown") or 0.0
    ft   = metrics.get("follow_through")   or 0.0
    gr   = metrics.get("gain_retention")   or 0.0
    cq   = metrics.get("continuation_quality") or 0.0

    setup_upper = (setup_type or "").upper()

    # 1. SUSTAINED_TREND — clean positive progression across all major windows
    if cq >= _TREND_CQ_THRESHOLD and r5d >= _TREND_MIN_5D and r20d >= _TREND_MIN_20D:
        return "SUSTAINED_TREND"

    # 2. SHORT_LIVED_SPIKE — large 1d gain that collapsed by 5d
    if (
        r1d >= _SPIKE_MIN_GAIN_1D
        and mg >= _SPIKE_MIN_PEAK
        and r5d < r1d * _SPIKE_COLLAPSE_RATIO
    ):
        return "SHORT_LIVED_SPIKE"

    # 3. VOLATILITY_TRAP — significant peak but matched by severe drawdown (churn)
    if (
        mg >= _TRAP_MIN_PEAK
        and md <= _TRAP_MIN_DD
        and (-md) / (mg + 1e-9) > _TRAP_DD_TO_GAIN
    ):
        return "VOLATILITY_TRAP"

    # 4. FAILED_SQUEEZE — squeeze-type setup that did not follow through
    if "SQUEEZE" in setup_upper and r5d < 0.01:
        return "FAILED_SQUEEZE"

    # 5. FAILED_BREAKOUT — initially non-negative but turned clearly negative
    if r1d >= 0 and r5d < -0.02 and md < -0.03:
        return "FAILED_BREAKOUT"

    # 6. MEAN_REVERSION — clear directional reversal
    if r1d >= 0.02 and r20d < -0.01:
        return "MEAN_REVERSION"
    if r1d <= -0.01 and r5d <= r1d:
        return "MEAN_REVERSION"

    # 7. INSTITUTIONAL_ACCUMULATION — slow positive drift, low volatility
    if (
        r5d >= _ACCUM_MIN_5D
        and r20d >= _ACCUM_MIN_20D
        and mg < _ACCUM_MAX_GAIN
        and (-md) < _ACCUM_MAX_DD
    ):
        return "INSTITUTIONAL_ACCUMULATION"

    # 8. VALID_BREAKOUT — positive follow-through with reasonable gain retention
    if ft >= _BREAKOUT_MIN_FT and r5d >= _BREAKOUT_MIN_5D and gr >= _BREAKOUT_MIN_GR:
        return "VALID_BREAKOUT"

    return "INCONCLUSIVE"


def _confidence(n_windows: int, behavior: str) -> str:
    if behavior == "INCONCLUSIVE":
        return "LOW"
    if n_windows >= 4:
        return "HIGH"
    if n_windows >= _MIN_WINDOWS:
        return "MEDIUM"
    return "LOW"


def _build_evidence(row: dict, metrics: dict, behavior: str) -> str:
    """Build a compact human-readable evidence string."""
    parts = []
    for label, key in (("5d", "return_5d"), ("20d", "return_20d")):
        v = row.get(key)
        if v is not None:
            parts.append(f"{label}: {v * 100:+.1f}%")
    if row.get("max_gain") is not None:
        parts.append(f"peak: {row['max_gain'] * 100:.1f}%")
    if row.get("max_drawdown") is not None:
        parts.append(f"drawdown: {row['max_drawdown'] * 100:.1f}%")
    ft = metrics.get("follow_through")
    if ft is not None:
        parts.append(f"follow-through: {ft:.0%}")
    cq = metrics.get("continuation_quality")
    if cq is not None:
        parts.append(f"continuation: {cq:.0%}")
    return "; ".join(parts) if parts else "Insufficient data"


def _key_failure_reason(behavior: str, metrics: dict, row: dict) -> Optional[str]:
    if behavior in POSITIVE_BEHAVIORS:
        return None
    r5d = row.get("return_5d") or 0.0
    r1d = row.get("return_1d") or 0.0
    mg  = row.get("max_gain")  or 0.0
    md  = row.get("max_drawdown") or 0.0
    if behavior == "FAILED_BREAKOUT":
        return f"Setup reversed: 5d return {r5d * 100:+.1f}%"
    if behavior == "SHORT_LIVED_SPIKE":
        return f"Spike {r1d * 100:+.1f}% (1d) collapsed to {r5d * 100:+.1f}% (5d)"
    if behavior == "VOLATILITY_TRAP":
        return f"Churn: peak {mg * 100:.1f}%, drawdown {md * 100:.1f}%"
    if behavior == "FAILED_SQUEEZE":
        return f"Squeeze did not follow through: 5d return {r5d * 100:+.1f}%"
    if behavior == "MEAN_REVERSION":
        return "Price reverted toward pre-signal level"
    return None


def _key_success_reason(behavior: str, metrics: dict, row: dict) -> Optional[str]:
    if behavior not in POSITIVE_BEHAVIORS:
        return None
    r20d = row.get("return_20d") or 0.0
    r5d  = row.get("return_5d")  or 0.0
    ft   = metrics.get("follow_through") or 0.0
    gr   = metrics.get("gain_retention") or 0.0
    if behavior == "SUSTAINED_TREND":
        return f"Consistent trend: 20d {r20d * 100:+.1f}%"
    if behavior == "VALID_BREAKOUT":
        return f"Follow-through {ft:.0%}, gain retention {gr:.0%}"
    if behavior == "INSTITUTIONAL_ACCUMULATION":
        return f"Steady accumulation: 5d {r5d * 100:+.1f}%, 20d {r20d * 100:+.1f}%"
    return None


# ── Public pure function ──────────────────────────────────────────────────────

def validate_outcome(row: dict) -> dict:
    """
    Validate a single COMPLETE outcome row.

    Pure function — no DB access.  Never raises (returns INCONCLUSIVE on error).
    Input: outcome dict with return_Nd / max_gain / max_drawdown columns.
    """
    try:
        metrics    = _compute_metrics(row)
        setup_type = row.get("setup_type") or ""
        behavior   = _classify_behavior(row, metrics, setup_type)
        vs         = _compute_validation_score(metrics)
        conf       = _confidence(metrics["n_windows"], behavior)

        def _r(v: Optional[float], precision: int = 4) -> Optional[float]:
            return round(v, precision) if v is not None else None

        return {
            "outcome_id":               row.get("id"),
            "ticker":                   row.get("ticker"),
            "scan_time":                row.get("scan_time"),
            "setup_type":               setup_type or None,
            "alpha_tier":               row.get("alpha_tier"),
            "behavior_class":           behavior,
            "validation_score":         vs,
            "confidence":               conf,
            "follow_through_score":     _r(metrics["follow_through"]),
            "gain_retention":           _r(metrics["gain_retention"]),
            "drawdown_severity":        _r(1.0 - (metrics["drawdown_score"] or 1.0)),
            "continuation_quality":     _r(metrics["continuation_quality"]),
            "multi_window_consistency": _r(metrics["consistency"]),
            "sustained_strength":       _r(metrics["sustained_strength"]),
            "reversal_severity":        _r(1.0 - (metrics["reversal_score"] or 1.0)),
            "n_windows":                metrics["n_windows"],
            "evidence_summary":         _build_evidence(row, metrics, behavior),
            "key_failure_reason":       _key_failure_reason(behavior, metrics, row),
            "key_success_reason":       _key_success_reason(behavior, metrics, row),
        }
    except Exception as exc:
        log.warning("alpha_validation: validate_outcome failed for %s: %s",
                    row.get("ticker"), exc, exc_info=True)
        return {
            "outcome_id":               row.get("id"),
            "ticker":                   row.get("ticker"),
            "scan_time":                row.get("scan_time"),
            "setup_type":               row.get("setup_type"),
            "alpha_tier":               row.get("alpha_tier"),
            "behavior_class":           "INCONCLUSIVE",
            "validation_score":         0.0,
            "confidence":               "LOW",
            "follow_through_score":     None,
            "gain_retention":           None,
            "drawdown_severity":        None,
            "continuation_quality":     None,
            "multi_window_consistency": None,
            "sustained_strength":       None,
            "reversal_severity":        None,
            "n_windows":                0,
            "evidence_summary":         "Validation error",
            "key_failure_reason":       str(exc),
            "key_success_reason":       None,
        }


# ── DB table management ───────────────────────────────────────────────────────

def _ensure_table() -> None:
    """Create alpha_validation table if absent (idempotent)."""
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alpha_validation (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                outcome_id               INTEGER NOT NULL UNIQUE,
                ticker                   TEXT    NOT NULL,
                scan_time                TEXT    NOT NULL,
                setup_type               TEXT,
                alpha_tier               TEXT,
                behavior_class           TEXT    NOT NULL,
                validation_score         REAL    NOT NULL,
                confidence               TEXT    NOT NULL,
                follow_through_score     REAL,
                gain_retention           REAL,
                drawdown_severity        REAL,
                continuation_quality     REAL,
                multi_window_consistency REAL,
                sustained_strength       REAL,
                reversal_severity        REAL,
                n_windows                INTEGER NOT NULL DEFAULT 0,
                evidence_summary         TEXT,
                key_failure_reason       TEXT,
                key_success_reason       TEXT,
                computed_at              TEXT    NOT NULL
            )
            """
        )
        conn.commit()
    except Exception:
        log.warning("alpha_validation: table creation failed", exc_info=True)
    finally:
        conn.close()


# ── DB write operations ───────────────────────────────────────────────────────

def validate_all_outcomes() -> dict:
    """
    Validate all COMPLETE alpha_outcomes that have no validation row yet.

    Idempotent — rows with an existing validation entry are skipped
    (INSERT OR IGNORE on UNIQUE outcome_id).

    Returns: {"processed": int, "inserted": int, "skipped": int, "errors": int}
    """
    _ensure_table()
    from database import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT o.*
            FROM alpha_outcomes o
            LEFT JOIN alpha_validation v ON v.outcome_id = o.id
            WHERE o.status = 'COMPLETE'
              AND v.outcome_id IS NULL
            ORDER BY o.scan_time DESC
            """
        ).fetchall()
        rows = [dict(r) for r in rows]
    except Exception:
        log.warning("alpha_validation: query failed in validate_all_outcomes", exc_info=True)
        return {"processed": 0, "inserted": 0, "skipped": 0, "errors": 1}
    finally:
        conn.close()

    if not rows:
        log.info("alpha_validation: no unvalidated COMPLETE outcomes")
        return {"processed": 0, "inserted": 0, "skipped": 0, "errors": 0}

    now      = datetime.now().isoformat()
    inserted = errors = 0

    conn = get_connection()
    try:
        for row in rows:
            try:
                result = validate_outcome(row)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO alpha_validation
                        (outcome_id, ticker, scan_time, setup_type, alpha_tier,
                         behavior_class, validation_score, confidence,
                         follow_through_score, gain_retention, drawdown_severity,
                         continuation_quality, multi_window_consistency,
                         sustained_strength, reversal_severity,
                         n_windows, evidence_summary, key_failure_reason,
                         key_success_reason, computed_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        result["outcome_id"],
                        result["ticker"],
                        result["scan_time"],
                        result["setup_type"],
                        result["alpha_tier"],
                        result["behavior_class"],
                        result["validation_score"],
                        result["confidence"],
                        result["follow_through_score"],
                        result["gain_retention"],
                        result["drawdown_severity"],
                        result["continuation_quality"],
                        result["multi_window_consistency"],
                        result["sustained_strength"],
                        result["reversal_severity"],
                        result["n_windows"],
                        result["evidence_summary"],
                        result["key_failure_reason"],
                        result["key_success_reason"],
                        now,
                    ),
                )
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    inserted += 1
            except Exception:
                log.warning(
                    "alpha_validation: insert failed for outcome_id=%s ticker=%s",
                    row.get("id"), row.get("ticker"), exc_info=True,
                )
                errors += 1
        conn.commit()
    except Exception:
        log.warning("alpha_validation: batch validation failed", exc_info=True)
        errors += 1
    finally:
        conn.close()

    skipped = len(rows) - inserted - errors
    log.info(
        "alpha_validation: validate_all_outcomes — processed=%d inserted=%d skipped=%d errors=%d",
        len(rows), inserted, skipped, errors,
    )
    return {"processed": len(rows), "inserted": inserted, "skipped": max(0, skipped), "errors": errors}


# ── DB read operations ────────────────────────────────────────────────────────

def get_validations(
    limit: int = 100,
    setup_type: Optional[str] = None,
    behavior_class: Optional[str] = None,
) -> list:
    """Return validation rows; optionally filter by setup_type or behavior_class."""
    _ensure_table()
    from database import get_connection
    conn = get_connection()
    try:
        base  = "SELECT * FROM alpha_validation"
        where = []
        args  = []
        if setup_type:
            where.append("setup_type = ?")
            args.append(setup_type)
        if behavior_class:
            where.append("behavior_class = ?")
            args.append(behavior_class)
        q = (base + " WHERE " + " AND ".join(where)) if where else base
        q += " ORDER BY computed_at DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in conn.execute(q, args).fetchall()]
    except Exception:
        log.warning("alpha_validation: get_validations query failed", exc_info=True)
        return []
    finally:
        conn.close()


def get_validation_summary() -> dict:
    """
    Aggregate validation analytics.

    Never raises.  Returns empty structure when no validation data exists.
    """
    _ensure_table()
    empty = {
        "total_validated":           0,
        "behavior_distribution":     {},
        "overall_trap_rate":         None,
        "overall_sustainability_rate": None,
        "avg_validation_score":      None,
        "best_validated_setups":     [],
        "worst_trap_prone_setups":   [],
        "validation_by_tier":        {},
        "note":                      "No validation data yet — run validate_all_outcomes() first",
    }

    validations: list = []
    try:
        validations = get_validations(limit=1000)
    except Exception as exc:
        return {**empty, "note": f"Query failed: {exc}"}

    if not validations:
        return empty

    n = len(validations)

    # Behavior distribution
    behavior_dist: dict = defaultdict(int)
    for v in validations:
        behavior_dist[v["behavior_class"]] += 1

    # Trap and sustainability rates
    trap_n  = sum(1 for v in validations if v["behavior_class"] in NEGATIVE_BEHAVIORS)
    sust_n  = sum(1 for v in validations if v["behavior_class"] in POSITIVE_BEHAVIORS)
    overall_trap_rate  = round(trap_n  / n, 4) if n else None
    overall_sust_rate  = round(sust_n  / n, 4) if n else None

    # Avg validation score
    scores = [v["validation_score"] for v in validations if v.get("validation_score") is not None]
    avg_vs = round(sum(scores) / len(scores), 1) if scores else None

    # Per-setup aggregates
    setup_trap_rates   = compute_setup_trap_rates(validations)
    setup_sust_rates   = compute_setup_sustainability(validations)

    # Per-setup avg validation score
    setup_vs: dict = defaultdict(list)
    for v in validations:
        st = v.get("setup_type") or "UNKNOWN"
        if v.get("validation_score") is not None:
            setup_vs[st].append(v["validation_score"])
    setup_avg_vs = {
        st: round(sum(vals) / len(vals), 1)
        for st, vals in setup_vs.items() if vals
    }

    # Leaderboards
    best_setups = sorted(
        ((st, avg) for st, avg in setup_avg_vs.items()),
        key=lambda x: -x[1],
    )[:5]

    worst_traps = sorted(
        ((st, rate) for st, rate in setup_trap_rates.items()),
        key=lambda x: -x[1],
    )[:5]

    # Validation by tier
    tier_vs: dict = defaultdict(list)
    for v in validations:
        tier = v.get("alpha_tier") or "UNKNOWN"
        if v.get("validation_score") is not None:
            tier_vs[tier].append(v["validation_score"])
    validation_by_tier = {
        tier: {"count": len(vals), "avg_validation_score": round(sum(vals) / len(vals), 1)}
        for tier, vals in tier_vs.items() if vals
    }

    return {
        "total_validated":           n,
        "behavior_distribution":     dict(behavior_dist),
        "overall_trap_rate":         overall_trap_rate,
        "overall_sustainability_rate": overall_sust_rate,
        "avg_validation_score":      avg_vs,
        "best_validated_setups":     [{"setup_type": st, "avg_validation_score": avg} for st, avg in best_setups],
        "worst_trap_prone_setups":   [{"setup_type": st, "trap_rate": rate} for st, rate in worst_traps],
        "validation_by_tier":        validation_by_tier,
        "note":                      "Simulation only — live scoring unchanged",
    }


# ── Analytics helpers ─────────────────────────────────────────────────────────

def compute_setup_trap_rates(validations: list) -> dict:
    """
    Per-setup-type trap rate from a list of validation dicts.

    trap_rate = fraction of VOLATILITY_TRAP + FAILED_SQUEEZE + SHORT_LIVED_SPIKE
    Returns {} when validations is empty.
    """
    if not validations:
        return {}
    setup_buckets: dict = defaultdict(lambda: {"count": 0, "traps": 0})
    for v in validations:
        st = v.get("setup_type") or "UNKNOWN"
        setup_buckets[st]["count"] += 1
        if v.get("behavior_class") in NEGATIVE_BEHAVIORS:
            setup_buckets[st]["traps"] += 1
    return {
        st: round(b["traps"] / b["count"], 4)
        for st, b in setup_buckets.items()
        if b["count"] > 0
    }


def compute_setup_sustainability(validations: list) -> dict:
    """
    Per-setup-type sustainability rate.

    sustainability_rate = fraction of VALID_BREAKOUT + SUSTAINED_TREND + INSTITUTIONAL_ACCUMULATION
    Returns {} when validations is empty.
    """
    if not validations:
        return {}
    setup_buckets: dict = defaultdict(lambda: {"count": 0, "sust": 0})
    for v in validations:
        st = v.get("setup_type") or "UNKNOWN"
        setup_buckets[st]["count"] += 1
        if v.get("behavior_class") in POSITIVE_BEHAVIORS:
            setup_buckets[st]["sust"] += 1
    return {
        st: round(b["sust"] / b["count"], 4)
        for st, b in setup_buckets.items()
        if b["count"] > 0
    }


def get_validation_metrics_for_proposals() -> dict:
    """
    Summary validation metrics intended for inclusion in proposal evidence.

    Returns {} on any failure (caller should treat as optional enhancement).
    """
    try:
        validations = get_validations(limit=500)
        if not validations:
            return {}
        n = len(validations)
        trap_n  = sum(1 for v in validations if v["behavior_class"] in NEGATIVE_BEHAVIORS)
        sust_n  = sum(1 for v in validations if v["behavior_class"] in POSITIVE_BEHAVIORS)
        scores  = [v["validation_score"] for v in validations if v.get("validation_score") is not None]
        return {
            "validation_count":      n,
            "trap_rate":             round(trap_n  / n, 4) if n else None,
            "sustainability_rate":   round(sust_n  / n, 4) if n else None,
            "avg_validation_score":  round(sum(scores) / len(scores), 1) if scores else None,
        }
    except Exception as exc:
        log.warning("alpha_validation: get_validation_metrics_for_proposals failed: %s", exc)
        return {}
