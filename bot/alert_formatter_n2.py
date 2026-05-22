"""
Phase N2: Plain-English WhatsApp alert formatter.

Design principles:
  - Readable in under 10 seconds on a mobile screen
  - Simple words, short lines
  - No hype, no marketing language
  - Always ends with advisory footer
  - No unexplained abbreviations
  - Banned words never appear in output

Message structure (buy signal):
  TICKER — TIER

  Confidence: 82%
  Adjusted score: 4.4/10
  Market: Supportive

  Why this alert:
  • Strong options activity
  • Insider buying
  • Breakout confirmed

  Watch:
  • Upcoming catalyst — Earnings in 4 days

  Plan:
  Entry: $225.00
  Stop: $205.00 (-9%)
  Suggested size: $5,000 CAD

  Advisory only. No trade was placed.
"""
from __future__ import annotations

import re
from typing import Optional
from alert_schema import AlertCandidate, EligibilityResult

# ── Constants ──────────────────────────────────────────────────────────────────

ADVISORY_FOOTER     = "Advisory only. No trade was placed."
MAX_SIGNAL_BULLETS  = 4
MAX_WATCH_ITEMS     = 2

# Words that must never appear in any formatted output
BANNED_WORDS: frozenset[str] = frozenset({
    "guaranteed", "guarantee",
    "explosion", "explode",
    "moon", "mooning",
    "sure win",
    "must buy",
    "pre-explosion",
    "to the moon",
})

# ── Translation tables ─────────────────────────────────────────────────────────

_PREDATOR_SIGNAL_LABELS: dict[str, str] = {
    "options":       "Strong options activity",
    "insider":       "Insider buying",
    "short_squeeze": "Short squeeze setup",
    "catalyst":      "Upcoming catalyst",
    "institutional": "Big fund ownership",
    "breakout":      "Breakout confirmed",
    "momentum":      "Strong momentum",
}

_REGIME_LABELS: dict[str, str] = {
    "BULL":     "Supportive",
    "NEUTRAL":  "Mixed",
    "BEAR":     "Weak",
    "RISK_OFF": "Risk-off",
}

_RISK_MODE_LABELS: dict[str, str] = {
    "NORMAL":    "Normal",
    "DEFENSIVE": "Defensive — reduce exposure",
    "REDUCED":   "Reduced activity",
    "CRITICAL":  "Critical — high caution",
    "LOCKDOWN":  "Lockdown — avoid new positions",
    "RISK_OFF":  "Risk-off",
}


# ── Internal helpers ───────────────────────────────────────────────────────────

def _regime_label(regime: Optional[str]) -> str:
    if not regime:
        return "Mixed"
    return _REGIME_LABELS.get(regime.upper(), regime.title())


def _risk_mode_label(mode: str) -> str:
    return _RISK_MODE_LABELS.get(mode.upper(), mode.title())


def _clean_scanner_signal(reason: str) -> str:
    """Strip parenthetical score values from scanner reason strings.

    "strong bullish chatter (+0.65)"  → "Strong bullish chatter"
    "RSI 58 bullish momentum"         → "RSI 58 bullish momentum"
    "+1.5% today"                     → "+1.5% today"
    """
    clean = re.sub(r"\s*\([^)]+\)", "", reason).strip()
    return clean[:1].upper() + clean[1:] if clean else reason


def _translate_signals(
    active_signals: list[str],
    signals_data: dict,
    source: str,
) -> list[str]:
    """Convert signal keys (predator) or reason strings (scanner) to plain English."""
    result: list[str] = []
    for sig in active_signals:
        if source == "scanner":
            result.append(_clean_scanner_signal(sig))
        else:
            result.append(_PREDATOR_SIGNAL_LABELS.get(sig, sig.replace("_", " ").title()))
    return result


def _build_watch_items(candidate: AlertCandidate, signals_data: dict) -> list[str]:
    """Build the Watch section: suppressed signals + notable signal details."""
    items: list[str] = []

    # Suppressed signals (regime penalty)
    for sig in candidate.suppressed_signals[:MAX_WATCH_ITEMS]:
        label = _PREDATOR_SIGNAL_LABELS.get(sig, sig.replace("_", " ").title())
        items.append(f"{label} reduced by current market")

    # Catalyst timing detail (extracted from signal reason if catalyst is active)
    if "catalyst" not in candidate.suppressed_signals:
        cat_data = signals_data.get("catalyst", {})
        cat_reason = (cat_data.get("reason") or "").strip()
        if cat_reason and cat_reason != "—" and "catalyst" in candidate.active_signals:
            items.append(cat_reason)

    return items[:MAX_WATCH_ITEMS]


# ── Buy-signal formatter ───────────────────────────────────────────────────────

def format_buy_alert(
    candidate: AlertCandidate,
    eligibility: EligibilityResult,
) -> str:
    """Plain-English buy alert for predator and scanner candidates.

    Sections (each omitted gracefully when data is absent):
      Header   — TICKER — TIER
      Scores   — Confidence, Adjusted score, Market, Risk mode
      Signals  — Why this alert (max 4 bullets)
      Watch    — Suppressed signals / catalyst timing (max 2 bullets)
      Plan     — Entry, Stop, Suggested size
      Footer   — Advisory only. No trade was placed.
    """
    ticker    = candidate.ticker.upper()
    tier      = eligibility.resolved_tier
    adj       = eligibility.adjusted_score
    conf      = eligibility.confidence_pct
    signals_d = candidate.metadata.get("signals", {})

    lines: list[str] = [f"{ticker} — {tier}", ""]

    # Scores block
    if conf > 0:
        lines.append(f"Confidence: {conf:.0f}%")
    lines.append(f"Adjusted score: {adj:.1f}/10")

    if candidate.regime:
        lines.append(f"Market: {_regime_label(candidate.regime)}")

    risk_mode = candidate.metadata.get("risk_mode")
    if risk_mode:
        lines.append(f"Risk mode: {_risk_mode_label(risk_mode)}")

    lines.append("")

    # Why this alert
    translated = _translate_signals(candidate.active_signals, signals_d, candidate.source)
    if translated:
        lines.append("Why this alert:")
        for item in translated[:MAX_SIGNAL_BULLETS]:
            lines.append(f"• {item}")
        lines.append("")

    # Watch
    watch_items = _build_watch_items(candidate, signals_d)
    if watch_items:
        lines.append("Watch:")
        for item in watch_items:
            lines.append(f"• {item}")
        lines.append("")

    # Plan
    has_plan = any(
        x is not None
        for x in [candidate.entry_price, candidate.stop_price, candidate.position_size_cad]
    )
    if has_plan:
        lines.append("Plan:")
        if candidate.entry_price is not None:
            lines.append(f"Entry: ${candidate.entry_price:.2f}")
        if candidate.stop_price is not None:
            pct_str = ""
            if candidate.entry_price and candidate.entry_price > 0:
                drop    = (candidate.stop_price / candidate.entry_price - 1) * 100
                pct_str = f" ({drop:.0f}%)"
            lines.append(f"Stop: ${candidate.stop_price:.2f}{pct_str}")
        if candidate.position_size_cad is not None:
            lines.append(f"Suggested size: ${candidate.position_size_cad:,.0f} CAD")
        lines.append("")

    lines.append(ADVISORY_FOOTER)
    return "\n".join(lines)


# ── Sell-signal formatter ──────────────────────────────────────────────────────

def format_sell_alert(
    candidate: AlertCandidate,
    eligibility: EligibilityResult,
    shares: float,
    avg_cost: float,
) -> str:
    """Plain-English sell alert for sell_monitor candidates.

    URGENT → action-oriented header and prompt to sell.
    WARNING → informational header and "monitor today" close.
    """
    ticker  = candidate.ticker.upper()
    urgency = (candidate.urgency or "URGENT").upper()
    price   = candidate.entry_price or 0.0
    pct_1d  = candidate.metadata.get("pct_1d", 0.0)
    pct_pnl = (
        round((price / avg_cost - 1) * 100, 1)
        if avg_cost > 0 and price > 0 else 0.0
    )
    sign = "+" if pct_pnl >= 0 else ""

    lines: list[str] = [f"{ticker} — {urgency}", ""]

    # Position summary
    if shares and avg_cost:
        lines.append(f"Position: {int(shares)} shares at ${avg_cost:.2f} avg")
    if price:
        lines.append(f"Now: ${price:.2f} ({pct_1d:+.1f}% today)")
    if avg_cost > 0 and price:
        lines.append(f"P&L: {sign}{pct_pnl:.1f}% if sold now")

    lines.append("")

    # Signals
    if candidate.active_signals:
        header = "Why:" if urgency == "URGENT" else "Developing signal:"
        lines.append(header)
        for sig in candidate.active_signals[:MAX_SIGNAL_BULLETS]:
            lines.append(f"• {sig}")
        lines.append("")

    # Close
    if urgency == "URGENT":
        lines.append("Sell in Wealthsimple when ready.")
    else:
        lines.append("No action needed yet. Monitor today.")

    lines += ["", ADVISORY_FOOTER]
    return "\n".join(lines)
