"""
Phase N2 — Plain-English WhatsApp alert formatter tests.

Covers:
  - Required content in buy and sell alerts
  - Deterministic output (same inputs → same output)
  - Max line count (≤ 20 content lines)
  - Banned words never appear
  - Sparse / None fields handled gracefully
  - Long signal list truncated to 4 bullets
  - Signal translation (predator keys → plain English)
  - Scanner signal cleanup (parentheticals stripped)
  - Watch section (suppressed signals, catalyst timing)
  - Sell alert URGENT vs WARNING wording
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from alert_schema import AlertCandidate, EligibilityResult
from alert_formatter_n2 import (
    format_buy_alert,
    format_sell_alert,
    ADVISORY_FOOTER,
    BANNED_WORDS,
    MAX_SIGNAL_BULLETS,
    MAX_WATCH_ITEMS,
    _clean_scanner_signal,
    _translate_signals,
    _build_watch_items,
)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _elig(tier="CONVICTION", adj=4.4, conf=82.0, trigger="tier:CONVICTION"):
    return EligibilityResult(
        eligible=True, resolved_tier=tier,
        adjusted_score=adj, confidence_pct=conf,
        reasons=[f"tier:{tier}"], suppression_reasons=[],
        trigger_reason=trigger,
    )


def _buy(
    ticker="NVDA", tier="CONVICTION", adj=4.4, conf=82.0,
    regime="BULL", active=None, suppressed=None,
    entry=225.0, stop=205.0, position=5000.0,
    source="predator", risk_mode=None,
    signals_meta=None,
):
    meta: dict = {}
    if signals_meta is not None:
        meta["signals"] = signals_meta
    else:
        meta["signals"] = {
            "options":  {"score": 3, "reason": "unusual calls", "data_quality": "HIGH"},
            "insider":  {"score": 2, "reason": "3 insiders bought",  "data_quality": "MEDIUM"},
            "breakout": {"score": 2, "reason": "near 52wk high",     "data_quality": "MEDIUM"},
        }
    if risk_mode:
        meta["risk_mode"] = risk_mode
    return AlertCandidate(
        source=source, ticker=ticker,
        raw_score=8.0, adjusted_score=adj, confidence_pct=conf,
        tier=tier, regime=regime,
        active_signals=active if active is not None else ["options", "insider", "breakout"],
        suppressed_signals=suppressed or [],
        urgency=None, entry_price=entry, stop_price=stop,
        position_size_cad=position, risk_posture=None,
        metadata=meta,
    )


def _sell_cand(urgency="URGENT", active=None, pct_1d=-2.1, price=158.0):
    return AlertCandidate(
        source="sell_monitor", ticker="VFV.TO",
        raw_score=None, adjusted_score=None, confidence_pct=None,
        tier=None, regime=None,
        active_signals=active if active is not None else ["Price below 50-day MA", "MACD cross"],
        suppressed_signals=[],
        urgency=urgency, entry_price=price, stop_price=None,
        position_size_cad=None, risk_posture=None,
        metadata={"pct_1d": pct_1d},
    )


# ─────────────────────────────────────────────
# Buy alert — required content
# ─────────────────────────────────────────────

class TestBuyAlertContent:
    def test_ticker_in_header(self):
        msg = format_buy_alert(_buy(), _elig())
        assert msg.startswith("NVDA —")

    def test_tier_in_header(self):
        msg = format_buy_alert(_buy(), _elig())
        assert "CONVICTION" in msg.split("\n")[0]

    def test_confidence_shown(self):
        msg = format_buy_alert(_buy(conf=82.0), _elig(conf=82.0))
        assert "Confidence: 82%" in msg

    def test_confidence_omitted_when_zero(self):
        msg = format_buy_alert(_buy(conf=0.0), _elig(conf=0.0))
        assert "Confidence:" not in msg

    def test_adjusted_score_shown(self):
        msg = format_buy_alert(_buy(adj=4.4), _elig(adj=4.4))
        assert "Adjusted score: 4.4/10" in msg

    def test_market_regime_shown(self):
        msg = format_buy_alert(_buy(regime="BULL"), _elig())
        assert "Market: Supportive" in msg

    def test_market_regime_omitted_when_none(self):
        msg = format_buy_alert(_buy(regime=None), _elig())
        assert "Market:" not in msg

    def test_risk_mode_shown_when_present(self):
        msg = format_buy_alert(_buy(risk_mode="DEFENSIVE"), _elig())
        assert "Risk mode: Defensive" in msg

    def test_risk_mode_omitted_when_absent(self):
        msg = format_buy_alert(_buy(), _elig())
        assert "Risk mode:" not in msg

    def test_why_this_alert_section_present(self):
        msg = format_buy_alert(_buy(), _elig())
        assert "Why this alert:" in msg

    def test_entry_price_shown(self):
        msg = format_buy_alert(_buy(entry=225.0), _elig())
        assert "Entry: $225.00" in msg

    def test_stop_price_shown_with_pct(self):
        msg = format_buy_alert(_buy(entry=225.0, stop=205.0), _elig())
        assert "Stop: $205.00" in msg
        assert "(-9%)" in msg or "(-8%)" in msg  # rounding

    def test_position_size_shown(self):
        msg = format_buy_alert(_buy(position=5000.0), _elig())
        assert "5,000" in msg
        assert "CAD" in msg

    def test_advisory_footer_always_present(self):
        msg = format_buy_alert(_buy(), _elig())
        assert msg.endswith(ADVISORY_FOOTER)

    def test_plan_section_absent_when_no_prices(self):
        msg = format_buy_alert(_buy(entry=None, stop=None, position=None), _elig())
        assert "Plan:" not in msg

    @pytest.mark.parametrize("tier", ["CONVICTION", "ALERT", "WATCH"])
    def test_all_tiers_render(self, tier):
        msg = format_buy_alert(_buy(tier=tier), _elig(tier=tier))
        assert tier in msg


# ─────────────────────────────────────────────
# Buy alert — determinism
# ─────────────────────────────────────────────

class TestBuyAlertDeterminism:
    def test_same_inputs_same_output(self):
        c, e = _buy(), _elig()
        assert format_buy_alert(c, e) == format_buy_alert(c, e)

    def test_stable_across_five_calls(self):
        c, e = _buy(), _elig()
        outputs = [format_buy_alert(c, e) for _ in range(5)]
        assert len(set(outputs)) == 1

    def test_different_tiers_differ(self):
        conviction = format_buy_alert(_buy(tier="CONVICTION"), _elig(tier="CONVICTION"))
        alert      = format_buy_alert(_buy(tier="ALERT"),      _elig(tier="ALERT"))
        assert conviction != alert


# ─────────────────────────────────────────────
# Buy alert — max line count
# ─────────────────────────────────────────────

class TestBuyAlertLineCount:
    def _content_lines(self, msg: str) -> int:
        return len([l for l in msg.splitlines() if l.strip()])

    def test_full_alert_under_20_content_lines(self):
        msg = format_buy_alert(_buy(risk_mode="NORMAL"), _elig())
        assert self._content_lines(msg) <= 20

    def test_minimal_alert_under_10_content_lines(self):
        msg = format_buy_alert(
            _buy(entry=None, stop=None, position=None, regime=None, active=["options"]),
            _elig(conf=0.0),
        )
        assert self._content_lines(msg) <= 10

    def test_max_signals_respected(self):
        sigs = ["options", "insider", "breakout", "short_squeeze", "catalyst", "institutional"]
        msg  = format_buy_alert(_buy(active=sigs), _elig())
        bullet_lines = [l for l in msg.splitlines() if l.startswith("•")]
        assert len(bullet_lines) <= MAX_SIGNAL_BULLETS + MAX_WATCH_ITEMS


# ─────────────────────────────────────────────
# Banned words
# ─────────────────────────────────────────────

class TestBannedWords:
    @pytest.mark.parametrize("word", list(BANNED_WORDS))
    def test_banned_word_absent_from_buy_alert(self, word):
        msg = format_buy_alert(_buy(), _elig())
        assert word.lower() not in msg.lower()

    @pytest.mark.parametrize("word", list(BANNED_WORDS))
    def test_banned_word_absent_from_sell_alert(self, word):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg = format_sell_alert(_sell_cand(), e, shares=15, avg_cost=162.5)
        assert word.lower() not in msg.lower()


# ─────────────────────────────────────────────
# Sparse / None field handling
# ─────────────────────────────────────────────

class TestSparseFieldHandling:
    def test_none_entry_does_not_crash(self):
        msg = format_buy_alert(_buy(entry=None, stop=None, position=None), _elig())
        assert "NVDA" in msg

    def test_none_regime_does_not_crash(self):
        msg = format_buy_alert(_buy(regime=None), _elig())
        assert len(msg) > 0

    def test_empty_active_signals_does_not_crash(self):
        msg = format_buy_alert(_buy(active=[]), _elig())
        assert "NVDA" in msg

    def test_zero_adj_score_renders(self):
        msg = format_buy_alert(_buy(adj=0.0), _elig(adj=0.0))
        assert "Adjusted score: 0.0/10" in msg

    def test_zero_confidence_omits_confidence_line(self):
        msg = format_buy_alert(_buy(conf=0.0), _elig(conf=0.0))
        assert "Confidence:" not in msg

    def test_none_stop_omits_stop_line(self):
        c   = _buy(entry=225.0, stop=None, position=5000.0)
        msg = format_buy_alert(c, _elig())
        assert "Stop:" not in msg

    def test_none_position_omits_size_line(self):
        msg = format_buy_alert(_buy(position=None), _elig())
        assert "Suggested size:" not in msg

    def test_missing_signals_meta_does_not_crash(self):
        c = _buy(active=["options"], signals_meta={})
        msg = format_buy_alert(c, _elig())
        assert len(msg) > 0

    def test_output_is_string(self):
        msg = format_buy_alert(_buy(), _elig())
        assert isinstance(msg, str)

    def test_always_ends_with_advisory_footer(self):
        for entry in [None, 225.0]:
            for regime in [None, "BULL", "BEAR"]:
                msg = format_buy_alert(_buy(entry=entry, regime=regime), _elig())
                assert msg.endswith(ADVISORY_FOOTER)


# ─────────────────────────────────────────────
# Signal truncation (max 4 bullets in Why)
# ─────────────────────────────────────────────

class TestSignalTruncation:
    def test_exactly_4_bullets_when_6_signals(self):
        sigs = ["options", "insider", "breakout", "short_squeeze", "catalyst", "institutional"]
        msg  = format_buy_alert(_buy(active=sigs), _elig())
        why_start = msg.find("Why this alert:")
        plan_start = msg.find("Plan:")
        why_section = msg[why_start:plan_start if plan_start > -1 else None]
        bullets = [l for l in why_section.splitlines() if l.startswith("•")]
        assert len(bullets) == MAX_SIGNAL_BULLETS

    def test_one_signal_shows_one_bullet(self):
        msg = format_buy_alert(_buy(active=["options"]), _elig())
        why_start = msg.find("Why this alert:")
        why_end   = msg.find("\n\n", why_start)
        why_section = msg[why_start:why_end]
        bullets = [l for l in why_section.splitlines() if l.startswith("•")]
        assert len(bullets) == 1

    def test_no_signals_omits_why_section(self):
        msg = format_buy_alert(_buy(active=[]), _elig())
        assert "Why this alert:" not in msg


# ─────────────────────────────────────────────
# Signal translation — predator keys
# ─────────────────────────────────────────────

class TestSignalTranslation:
    @pytest.mark.parametrize("key,expected", [
        ("options",       "Strong options activity"),
        ("insider",       "Insider buying"),
        ("short_squeeze", "Short squeeze setup"),
        ("catalyst",      "Upcoming catalyst"),
        ("institutional", "Big fund ownership"),
        ("breakout",      "Breakout confirmed"),
        ("momentum",      "Strong momentum"),
    ])
    def test_predator_key_translated(self, key, expected):
        translated = _translate_signals([key], {}, "predator")
        assert translated == [expected]

    def test_unknown_key_title_cased(self):
        translated = _translate_signals(["new_signal"], {}, "predator")
        assert translated == ["New Signal"]

    def test_scanner_signal_passed_through_cleaned(self):
        raw = "strong bullish chatter (+0.65)"
        translated = _translate_signals([raw], {}, "scanner")
        assert translated == ["Strong bullish chatter"]

    def test_multiple_signals_translated_in_order(self):
        keys = ["options", "insider"]
        translated = _translate_signals(keys, {}, "predator")
        assert translated == ["Strong options activity", "Insider buying"]


# ─────────────────────────────────────────────
# Scanner signal cleanup
# ─────────────────────────────────────────────

class TestScannerSignalCleanup:
    @pytest.mark.parametrize("raw,expected", [
        ("strong bullish chatter (+0.65)",  "Strong bullish chatter"),
        ("RSI 58 bullish momentum",          "RSI 58 bullish momentum"),
        ("+1.5% today",                      "+1.5% today"),
        ("MACD positive (score: 2.0)",       "MACD positive"),
        ("breakout confirmed (HIGH)",        "Breakout confirmed"),
    ])
    def test_cleanup(self, raw, expected):
        assert _clean_scanner_signal(raw) == expected

    def test_capitalises_first_letter(self):
        assert _clean_scanner_signal("bullish sentiment") == "Bullish sentiment"

    def test_empty_string_returns_empty(self):
        assert _clean_scanner_signal("") == ""


# ─────────────────────────────────────────────
# Watch section
# ─────────────────────────────────────────────

class TestWatchSection:
    def test_watch_absent_when_no_suppressed_and_no_catalyst(self):
        msg = format_buy_alert(_buy(suppressed=[], active=["options"]), _elig())
        assert "Watch:" not in msg

    def test_watch_present_for_suppressed_signal(self):
        c   = _buy(regime="BEAR", suppressed=["short_squeeze"])
        msg = format_buy_alert(c, _elig())
        assert "Watch:" in msg
        assert "Short squeeze setup reduced by current market" in msg

    def test_suppressed_max_2_items(self):
        c   = _buy(suppressed=["options", "insider", "breakout"])
        msg = format_buy_alert(c, _elig())
        watch_start = msg.find("Watch:")
        plan_start  = msg.find("Plan:")
        watch_section = msg[watch_start:plan_start if plan_start > -1 else None]
        bullets = [l for l in watch_section.splitlines() if l.startswith("•")]
        assert len(bullets) <= MAX_WATCH_ITEMS

    def test_catalyst_reason_shown_in_watch(self):
        signals_meta = {
            "catalyst": {"score": 2, "reason": "Earnings in 4 days", "data_quality": "HIGH"},
        }
        c   = _buy(active=["catalyst"], signals_meta=signals_meta)
        msg = format_buy_alert(c, _elig())
        assert "Earnings in 4 days" in msg

    def test_catalyst_reason_not_shown_when_suppressed(self):
        signals_meta = {
            "catalyst": {"score": 2, "reason": "Earnings in 4 days", "data_quality": "HIGH"},
        }
        c   = _buy(active=["options"], suppressed=["catalyst"], signals_meta=signals_meta)
        msg = format_buy_alert(c, _elig())
        assert "Earnings in 4 days" not in msg


# ─────────────────────────────────────────────
# Regime / risk-mode labels
# ─────────────────────────────────────────────

class TestRegimeLabels:
    @pytest.mark.parametrize("regime,label", [
        ("BULL",     "Supportive"),
        ("NEUTRAL",  "Mixed"),
        ("BEAR",     "Weak"),
        ("RISK_OFF", "Risk-off"),
    ])
    def test_regime_label(self, regime, label):
        msg = format_buy_alert(_buy(regime=regime), _elig())
        assert label in msg

    @pytest.mark.parametrize("mode,expected_substr", [
        ("NORMAL",    "Normal"),
        ("DEFENSIVE", "Defensive"),
        ("REDUCED",   "Reduced"),
        ("CRITICAL",  "Critical"),
        ("LOCKDOWN",  "Lockdown"),
    ])
    def test_risk_mode_label(self, mode, expected_substr):
        msg = format_buy_alert(_buy(risk_mode=mode), _elig())
        assert expected_substr in msg


# ─────────────────────────────────────────────
# Sell alert — URGENT vs WARNING
# ─────────────────────────────────────────────

class TestSellAlertFormat:
    def test_ticker_in_header(self):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg = format_sell_alert(_sell_cand(), e, shares=15, avg_cost=162.5)
        assert msg.startswith("VFV.TO —")

    def test_urgency_in_header(self):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg = format_sell_alert(_sell_cand("URGENT"), e, shares=15, avg_cost=162.5)
        assert "URGENT" in msg.split("\n")[0]

    def test_warning_in_header(self):
        e   = _elig("ALERT", 0.0, 0.0, "urgency:WARNING")
        msg = format_sell_alert(_sell_cand("WARNING"), e, shares=15, avg_cost=162.5)
        assert "WARNING" in msg.split("\n")[0]

    def test_position_summary_shown(self):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg = format_sell_alert(_sell_cand(), e, shares=15, avg_cost=162.5)
        assert "15 shares" in msg
        assert "162.50" in msg

    def test_current_price_shown(self):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg = format_sell_alert(_sell_cand(price=158.0), e, shares=15, avg_cost=162.5)
        assert "158.00" in msg

    def test_pnl_shown(self):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg = format_sell_alert(_sell_cand(price=158.0), e, shares=15, avg_cost=162.5)
        assert "P&L:" in msg

    def test_signals_shown_as_bullets(self):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg = format_sell_alert(_sell_cand(), e, shares=15, avg_cost=162.5)
        assert "50-day" in msg or "MACD" in msg

    def test_urgent_action_prompt(self):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg = format_sell_alert(_sell_cand("URGENT"), e, shares=15, avg_cost=162.5)
        assert "Wealthsimple" in msg
        assert "Sell in Wealthsimple when ready." in msg

    def test_warning_monitor_prompt(self):
        e   = _elig("ALERT", 0.0, 0.0, "urgency:WARNING")
        msg = format_sell_alert(_sell_cand("WARNING"), e, shares=15, avg_cost=162.5)
        assert "Monitor today" in msg
        assert "Sell in Wealthsimple" not in msg

    def test_advisory_footer_always_present(self):
        for urgency in ["URGENT", "WARNING"]:
            e   = _elig("CONVICTION", 0.0, 0.0, f"urgency:{urgency}")
            msg = format_sell_alert(_sell_cand(urgency), e, shares=15, avg_cost=162.5)
            assert msg.endswith(ADVISORY_FOOTER)

    def test_why_label_for_urgent(self):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg = format_sell_alert(_sell_cand("URGENT"), e, shares=15, avg_cost=162.5)
        assert "Why:" in msg

    def test_developing_signal_label_for_warning(self):
        e   = _elig("ALERT", 0.0, 0.0, "urgency:WARNING")
        msg = format_sell_alert(_sell_cand("WARNING"), e, shares=15, avg_cost=162.5)
        assert "Developing signal:" in msg

    def test_zero_avg_cost_no_crash(self):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg = format_sell_alert(_sell_cand(), e, shares=15, avg_cost=0.0)
        assert "VFV.TO" in msg

    def test_positive_pnl_shown_with_plus(self):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        msg = format_sell_alert(_sell_cand(price=170.0), e, shares=15, avg_cost=162.5)
        assert "+4" in msg or "+5" in msg  # ~+4.6%

    def test_sell_deterministic(self):
        e   = _elig("CONVICTION", 0.0, 0.0, "urgency:URGENT")
        c   = _sell_cand()
        assert (
            format_sell_alert(c, e, shares=15, avg_cost=162.5)
            == format_sell_alert(c, e, shares=15, avg_cost=162.5)
        )
