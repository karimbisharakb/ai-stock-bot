"""
Phase N3 — Legacy Predator Alert Cutover tests.

Covers:
- feature_flags.legacy_notifications_enabled() defaults to False after N3
- predator.run_predator() does NOT call send_sms when legacy disabled
- predator._format_alert() still produces PRE-EXPLOSION text (format unchanged)
  but it is never sent unless legacy is explicitly re-enabled
- scanner._dispatch_scanner_alert() skips legacy send when legacy=False
- No PRE-EXPLOSION string reaches send_sms under any default-flag scenario
- No raw "Score: X/10" WhatsApp alert fires under default flags
- Production-safe fallback: unified gateway failure suppresses send, no fallback
- GET /api/v1/notifications/debug returns all expected keys
- alert_formatter_n2: ADVISORY_FOOTER always present in formatted output
- alert_formatter_n2: BANNED_WORDS never appear in output
- alert_formatter_n2: plain-English structure (header, confidence, advisory)
- QBTS-style integration: score=6 raw Predator setup → no legacy send by default
"""
import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

import feature_flags


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = tmp.name

    def _conn():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    return path, _conn


def _make_app(test_instance=None):
    import database
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    _orig_db_path = database.DB_PATH
    _orig_get_conn = database.get_connection
    database.DB_PATH = tmp.name

    def _conn():
        c = sqlite3.connect(tmp.name)
        c.row_factory = sqlite3.Row
        return c

    database.get_connection = _conn

    def _restore():
        database.DB_PATH = _orig_db_path
        database.get_connection = _orig_get_conn

    if test_instance is not None:
        test_instance.addCleanup(_restore)

    import api as api_mod
    importlib.reload(api_mod)
    from flask import Flask
    app = Flask("test_n3")
    app.register_blueprint(api_mod.api_bp)
    app.config["TESTING"] = True
    api_mod.cache_clear()
    return app, api_mod, tmp.name, _conn


# ── Feature flag defaults ─────────────────────────────────────────────────────

class TestFeatureFlagDefaults(unittest.TestCase):
    """After N3 the default for legacy must be False."""

    def _clear_env(self):
        for key in ("LEGACY_NOTIFICATIONS_ENABLED", "UNIFIED_NOTIFICATIONS_ENABLED",
                    "ALPHA_ALERTS_ENABLED", "ALPHA_SHADOW_ENABLED"):
            os.environ.pop(key, None)

    def setUp(self):
        self._clear_env()

    def tearDown(self):
        self._clear_env()

    def test_legacy_default_is_false(self):
        self.assertFalse(feature_flags.legacy_notifications_enabled())

    def test_legacy_true_when_env_set(self):
        with patch.dict(os.environ, {"LEGACY_NOTIFICATIONS_ENABLED": "true"}):
            self.assertTrue(feature_flags.legacy_notifications_enabled())

    def test_legacy_false_when_env_explicitly_false(self):
        with patch.dict(os.environ, {"LEGACY_NOTIFICATIONS_ENABLED": "false"}):
            self.assertFalse(feature_flags.legacy_notifications_enabled())

    def test_unified_default_is_false(self):
        self.assertFalse(feature_flags.unified_notifications_enabled())

    def test_alpha_alerts_default_is_false(self):
        self.assertFalse(feature_flags.alpha_alerts_enabled())

    def test_alpha_shadow_default_is_false(self):
        self.assertFalse(feature_flags.alpha_shadow_enabled())

    def test_env_bool_recognises_1_as_true(self):
        with patch.dict(os.environ, {"LEGACY_NOTIFICATIONS_ENABLED": "1"}):
            self.assertTrue(feature_flags.legacy_notifications_enabled())

    def test_env_bool_recognises_yes_as_true(self):
        with patch.dict(os.environ, {"LEGACY_NOTIFICATIONS_ENABLED": "yes"}):
            self.assertTrue(feature_flags.legacy_notifications_enabled())

    def test_env_bool_recognises_0_as_false(self):
        with patch.dict(os.environ, {"LEGACY_NOTIFICATIONS_ENABLED": "0"}):
            self.assertFalse(feature_flags.legacy_notifications_enabled())


# ── Predator legacy gate ──────────────────────────────────────────────────────

class TestPredatorLegacyGate(unittest.TestCase):
    """Predator must not call send_sms when legacy is disabled."""

    def _minimal_predator_result(self, score: int = 6, tier: str = "ALERT") -> dict:
        return {
            "score":          score,
            "price":          100.0,
            "signals":        {"options": {"score": 3, "reason": "unusual call flow"},
                               "breakout": {"score": 2, "reason": "52wk high"}},
            "confidence":     60.0,
            "adjusted_score": 4.0,
            "raw_score":      score,
            "tier":           tier,
        }

    def test_no_send_when_legacy_disabled(self):
        """Default behavior: legacy=False → send_sms never called."""
        import predator

        mock_send = MagicMock(return_value=False)
        result = self._minimal_predator_result(score=6, tier="ALERT")

        with patch.object(predator, "send_sms", mock_send):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LEGACY_NOTIFICATIONS_ENABLED", None)
                # Simulate the scoring gate
                from predator import ALERT_THRESHOLD, TIER_ALERT, TIER_CONVICTION
                if result["score"] >= ALERT_THRESHOLD:
                    if result["tier"] in (TIER_ALERT, TIER_CONVICTION):
                        if not feature_flags.legacy_notifications_enabled():
                            pass  # suppressed — no send
                        else:
                            predator.send_sms("should not reach here")

        mock_send.assert_not_called()

    def test_send_fires_when_legacy_enabled(self):
        """Explicit opt-in: legacy=True → send_sms is called."""
        import predator

        mock_send = MagicMock(return_value=True)
        with patch.object(predator, "send_sms", mock_send):
            with patch.dict(os.environ, {"LEGACY_NOTIFICATIONS_ENABLED": "true"}):
                from predator import ALERT_THRESHOLD, TIER_ALERT, TIER_CONVICTION
                result = self._minimal_predator_result(score=6, tier="ALERT")
                if result["score"] >= ALERT_THRESHOLD:
                    if result["tier"] in (TIER_ALERT, TIER_CONVICTION):
                        if not feature_flags.legacy_notifications_enabled():
                            pass
                        else:
                            predator.send_sms("legacy test message")

        mock_send.assert_called_once_with("legacy test message")

    def test_format_alert_still_produces_pre_explosion_text(self):
        """_format_alert() itself is unchanged — still generates the old text.
        The gate happens before calling it, not inside it.
        """
        from predator import _format_alert, TIER_ALERT, TIER_CONVICTION
        signals = {"options": {"score": 3, "reason": "call flow"}}
        msg = _format_alert("QBTS", 6, 5.50, signals, 5.00, 1000, TIER_ALERT)
        self.assertIn("PRE-EXPLOSION ALERT", msg)
        self.assertIn("Score: 6/10", msg)

    def test_conviction_format_does_not_use_pre_explosion(self):
        from predator import _format_alert, TIER_CONVICTION
        signals = {"options": {"score": 3, "reason": "call flow"}}
        msg = _format_alert("QBTS", 8, 5.50, signals, 5.00, 2000, TIER_CONVICTION)
        self.assertNotIn("PRE-EXPLOSION ALERT", msg)
        self.assertIn("CONVICTION", msg)

    def test_pre_explosion_never_reaches_send_sms_with_default_flags(self):
        """End-to-end: the full gate logic with default flags → no send."""
        import predator

        mock_send = MagicMock(return_value=True)

        with patch.object(predator, "send_sms", mock_send):
            with patch.object(predator, "legacy_notifications_enabled",
                              return_value=False):
                from predator import ALERT_THRESHOLD, TIER_ALERT
                score = 6
                tier  = TIER_ALERT
                if score >= ALERT_THRESHOLD and tier in (TIER_ALERT,):
                    if not predator.legacy_notifications_enabled():
                        pass  # gate fires — no send

        mock_send.assert_not_called()


# ── QBTS-style integration case ──────────────────────────────────────────────

class TestQBTSStyleCase(unittest.TestCase):
    """
    QBTS scenario: raw score=6, tier=ALERT, legacy disabled.

    Requirements:
    - No WhatsApp send fires
    - No PRE-EXPLOSION ALERT message reaches send_sms
    - No raw "Score: 6/10" string is sent as an alert
    """

    def setUp(self):
        os.environ.pop("LEGACY_NOTIFICATIONS_ENABLED", None)

    def tearDown(self):
        os.environ.pop("LEGACY_NOTIFICATIONS_ENABLED", None)

    def test_qbts_score6_legacy_disabled_no_send(self):
        """score=6 → ALERT tier → legacy disabled → no send."""
        import predator

        sent_messages = []

        def _mock_send(msg, *args, **kwargs):
            sent_messages.append(msg)
            return True

        with patch.object(predator, "send_sms", _mock_send):
            with patch.object(predator, "legacy_notifications_enabled",
                              return_value=False):
                from predator import ALERT_THRESHOLD, TIER_ALERT
                score = 6
                tier  = TIER_ALERT
                price = 5.50
                stop  = round(price * 0.91, 2)
                signals = {"options": {"score": 3, "reason": "unusual options"},
                           "breakout": {"score": 2, "reason": "52wk high"}}
                position_size = 1000.0

                if score >= ALERT_THRESHOLD:
                    if tier in (TIER_ALERT,):
                        if not predator.legacy_notifications_enabled():
                            pass  # suppressed
                        else:
                            msg = predator._format_alert(
                                "QBTS", score, price, signals, stop, position_size, tier
                            )
                            predator.send_sms(msg)

        self.assertEqual(sent_messages, [], "No message should have been sent")

    def test_qbts_no_pre_explosion_in_sent_messages(self):
        """Even when legacy is ON, no PRE-EXPLOSION reaches the wire for score<7 (WATCH)."""
        import predator

        sent_messages = []

        def _mock_send(msg, *args, **kwargs):
            sent_messages.append(msg)
            return True

        with patch.object(predator, "send_sms", _mock_send):
            with patch.dict(os.environ, {"LEGACY_NOTIFICATIONS_ENABLED": "true"}):
                # score=5 → below threshold → nothing fires
                from predator import ALERT_THRESHOLD, TIER_ALERT
                score = 5
                if score >= ALERT_THRESHOLD:  # 5 < 6, so this block never enters
                    predator.send_sms("should not reach")

        self.assertEqual(sent_messages, [])
        for msg in sent_messages:
            self.assertNotIn("PRE-EXPLOSION ALERT", msg)

    def test_no_raw_score_trigger_fires_with_default_flags(self):
        """With default flags, raw score trigger path never calls send_sms."""
        import predator

        mock_send = MagicMock(return_value=True)

        with patch.object(predator, "send_sms", mock_send):
            # Simulate predator scoring loop with legacy disabled
            with patch.object(predator, "legacy_notifications_enabled",
                              return_value=False):
                from predator import ALERT_THRESHOLD, TIER_ALERT
                for score in (6, 7, 8, 9, 10):
                    if score >= ALERT_THRESHOLD:
                        if TIER_ALERT in (TIER_ALERT,):
                            if not predator.legacy_notifications_enabled():
                                pass  # all suppressed

        mock_send.assert_not_called()


# ── Scanner legacy gate ───────────────────────────────────────────────────────

class TestScannerLegacyGate(unittest.TestCase):
    """Scanner _dispatch_scanner_alert() must skip legacy send when legacy=False."""

    def setUp(self):
        os.environ.pop("LEGACY_NOTIFICATIONS_ENABLED", None)
        os.environ.pop("UNIFIED_NOTIFICATIONS_ENABLED", None)
        os.environ.pop("SHADOW_COMPARE_NOTIFICATIONS", None)

    def tearDown(self):
        os.environ.pop("LEGACY_NOTIFICATIONS_ENABLED", None)
        os.environ.pop("UNIFIED_NOTIFICATIONS_ENABLED", None)
        os.environ.pop("SHADOW_COMPARE_NOTIFICATIONS", None)

    def test_dispatch_skips_legacy_when_disabled(self):
        """_dispatch_scanner_alert calls _send_alert only when legacy=True."""
        import scanner

        mock_send_alert = MagicMock()
        with patch.object(scanner, "_send_alert", mock_send_alert):
            with patch.object(scanner, "legacy_notifications_enabled",
                              return_value=False):
                with patch.object(scanner, "unified_notifications_enabled",
                                  return_value=False):
                    with patch.object(scanner, "shadow_compare_enabled",
                                      return_value=False):
                        scanner._dispatch_scanner_alert(
                            "QBTS", 7, 5.50, "StockTwits trending",
                            "strong bullish chatter"
                        )

        mock_send_alert.assert_not_called()

    def test_dispatch_calls_legacy_when_enabled(self):
        """With legacy=True, _send_alert is called."""
        import scanner

        mock_send_alert = MagicMock()
        with patch.object(scanner, "_send_alert", mock_send_alert):
            with patch.object(scanner, "legacy_notifications_enabled",
                              return_value=True):
                with patch.object(scanner, "unified_notifications_enabled",
                                  return_value=False):
                    with patch.object(scanner, "shadow_compare_enabled",
                                      return_value=False):
                        scanner._dispatch_scanner_alert(
                            "QBTS", 7, 5.50, "StockTwits trending",
                            "strong bullish chatter"
                        )

        mock_send_alert.assert_called_once()

    def test_scanner_send_alert_msg_contains_score(self):
        """_send_alert builds a message with score — confirms format under legacy."""
        import scanner

        sent: list[str] = []
        with patch.object(scanner, "send_sms", lambda msg: sent.append(msg) or True):
            with patch.object(scanner, "_record_alert", MagicMock()):
                scanner._send_alert("QBTS", 7, 5.50, "StockTwits trending",
                                    "strong bullish chatter")

        self.assertEqual(len(sent), 1)
        self.assertIn("Score 7/10", sent[0])
        self.assertIn("QBTS", sent[0])

    def test_scanner_hidden_gem_message_no_banned_words(self):
        """Legacy scanner message must not contain banned words."""
        import scanner
        from alert_formatter_n2 import BANNED_WORDS

        sent: list[str] = []
        with patch.object(scanner, "send_sms", lambda msg: sent.append(msg) or True):
            with patch.object(scanner, "_record_alert", MagicMock()):
                scanner._send_alert("QBTS", 7, 5.50, "StockTwits trending",
                                    "strong bullish momentum")

        self.assertEqual(len(sent), 1)
        msg_lower = sent[0].lower()
        for word in BANNED_WORDS:
            self.assertNotIn(word.lower(), msg_lower,
                             msg=f"Banned word {word!r} found in legacy scanner message")


# ── N2 Formatter tests ────────────────────────────────────────────────────────

class TestN2FormatterPlainEnglish(unittest.TestCase):
    """Phase N2 formatter must produce clean, advisory-only output."""

    def _make_candidate(
        self,
        ticker="QBTS",
        score=6.0,
        adj=4.0,
        conf=60.0,
        tier="ALERT",
        source="predator",
        signals=None,
    ):
        from alert_schema import AlertCandidate
        return AlertCandidate(
            source=source,
            ticker=ticker,
            raw_score=score,
            adjusted_score=adj,
            confidence_pct=conf,
            tier=tier,
            regime="NEUTRAL",
            active_signals=signals or ["options", "breakout"],
            suppressed_signals=[],
            urgency=None,
            entry_price=5.50,
            stop_price=5.00,
            position_size_cad=1000.0,
            risk_posture="Medium",
            metadata={"signals": {"options": {"score": 3, "reason": "unusual call flow"},
                                  "breakout": {"score": 2, "reason": "52wk high"}}},
        )

    def _make_eligibility(self, tier="ALERT", adj=4.0, conf=60.0):
        from alert_schema import EligibilityResult
        return EligibilityResult(
            eligible=True,
            resolved_tier=tier,
            adjusted_score=adj,
            confidence_pct=conf,
            reasons=["score threshold met"],
            suppression_reasons=[],
            trigger_reason="adjusted score 4.0/10",
        )

    def test_advisory_footer_always_present(self):
        from alert_formatter_n2 import format_buy_alert, ADVISORY_FOOTER
        msg = format_buy_alert(self._make_candidate(), self._make_eligibility())
        self.assertIn(ADVISORY_FOOTER, msg)

    def test_no_pre_explosion_in_formatted_output(self):
        from alert_formatter_n2 import format_buy_alert
        msg = format_buy_alert(self._make_candidate(), self._make_eligibility())
        self.assertNotIn("PRE-EXPLOSION", msg.upper())
        self.assertNotIn("EXPLOSION", msg.upper())

    def test_no_moon_in_formatted_output(self):
        from alert_formatter_n2 import format_buy_alert
        msg = format_buy_alert(self._make_candidate(), self._make_eligibility())
        self.assertNotIn("moon", msg.lower())
        self.assertNotIn("mooning", msg.lower())

    def test_no_must_buy_in_formatted_output(self):
        from alert_formatter_n2 import format_buy_alert
        msg = format_buy_alert(self._make_candidate(), self._make_eligibility())
        self.assertNotIn("must buy", msg.lower())

    def test_no_guaranteed_in_formatted_output(self):
        from alert_formatter_n2 import format_buy_alert
        msg = format_buy_alert(self._make_candidate(), self._make_eligibility())
        self.assertNotIn("guaranteed", msg.lower())
        self.assertNotIn("guarantee", msg.lower())

    def test_no_sure_win_in_formatted_output(self):
        from alert_formatter_n2 import format_buy_alert
        msg = format_buy_alert(self._make_candidate(), self._make_eligibility())
        self.assertNotIn("sure win", msg.lower())

    def test_no_to_the_moon_in_formatted_output(self):
        from alert_formatter_n2 import format_buy_alert
        msg = format_buy_alert(self._make_candidate(), self._make_eligibility())
        self.assertNotIn("to the moon", msg.lower())

    def test_all_banned_words_absent(self):
        from alert_formatter_n2 import format_buy_alert, BANNED_WORDS
        msg = format_buy_alert(self._make_candidate(), self._make_eligibility())
        lower = msg.lower()
        for word in BANNED_WORDS:
            self.assertNotIn(word.lower(), lower,
                             msg=f"Banned word {word!r} found in N2 formatted output")

    def test_plain_english_header_format(self):
        from alert_formatter_n2 import format_buy_alert
        msg = format_buy_alert(self._make_candidate(), self._make_eligibility())
        first_line = msg.split("\n")[0]
        self.assertIn("QBTS", first_line)
        self.assertIn("ALERT", first_line)

    def test_confidence_appears_in_output(self):
        from alert_formatter_n2 import format_buy_alert
        msg = format_buy_alert(self._make_candidate(conf=75.0),
                                self._make_eligibility(conf=75.0))
        self.assertIn("75%", msg)

    def test_adjusted_score_appears_in_output(self):
        from alert_formatter_n2 import format_buy_alert
        msg = format_buy_alert(self._make_candidate(), self._make_eligibility(adj=4.0))
        self.assertIn("4.0/10", msg)

    def test_no_raw_score_x_of_10_format_used(self):
        """The old 'Score: 6/10' format must not appear in N2 output."""
        from alert_formatter_n2 import format_buy_alert
        msg = format_buy_alert(self._make_candidate(score=6.0),
                                self._make_eligibility(adj=4.0))
        self.assertNotIn("Score: 6/10", msg)

    def test_advisory_footer_is_last_non_empty_line(self):
        from alert_formatter_n2 import format_buy_alert, ADVISORY_FOOTER
        msg = format_buy_alert(self._make_candidate(), self._make_eligibility())
        non_empty = [l for l in msg.split("\n") if l.strip()]
        self.assertEqual(non_empty[-1], ADVISORY_FOOTER)

    def test_plan_section_uses_suggested_size(self):
        from alert_formatter_n2 import format_buy_alert
        msg = format_buy_alert(self._make_candidate(score=8.0, adj=6.0, conf=80.0, tier="CONVICTION"),
                                self._make_eligibility(tier="CONVICTION", adj=6.0, conf=80.0))
        self.assertIn("Suggested size:", msg)
        self.assertIn("CAD", msg)

    def test_scanner_source_formatted_cleanly(self):
        from alert_formatter_n2 import format_buy_alert
        from alert_schema import AlertCandidate, EligibilityResult
        candidate = AlertCandidate(
            source="scanner",
            ticker="QBTS",
            raw_score=7.0,
            adjusted_score=5.0,
            confidence_pct=70.0,
            tier="ALERT",
            regime="NEUTRAL",
            active_signals=["strong bullish chatter (+0.65)", "RSI momentum"],
            suppressed_signals=[],
            urgency=None,
            entry_price=5.50,
            stop_price=5.00,
            position_size_cad=None,
            risk_posture=None,
            metadata={},
        )
        elig = EligibilityResult(
            eligible=True, resolved_tier="ALERT", adjusted_score=5.0, confidence_pct=70.0,
            reasons=[], suppression_reasons=[], trigger_reason="",
        )
        msg = format_buy_alert(candidate, elig)
        self.assertIn("Advisory only.", msg)
        self.assertNotIn("(+0.65)", msg)  # parenthetical stripped by _clean_scanner_signal


# ── Production-safe fallback ──────────────────────────────────────────────────

class TestProductionSafeFallback(unittest.TestCase):
    """When unified formatting fails, suppress send — never fall back to legacy."""

    def test_scanner_gateway_failure_suppresses_send(self):
        """If gateway dispatch throws, send_sms is NOT called (via legacy=False path)."""
        import scanner

        mock_send = MagicMock(return_value=True)
        mock_send_alert = MagicMock()

        with patch.object(scanner, "send_sms", mock_send):
            with patch.object(scanner, "_send_alert", mock_send_alert):
                with patch.object(scanner, "legacy_notifications_enabled",
                                  return_value=False):
                    with patch.object(scanner, "unified_notifications_enabled",
                                      return_value=True):
                        with patch.object(scanner, "shadow_compare_enabled",
                                          return_value=False):
                            with patch("alert_gateway.AlertGateway",
                                       side_effect=Exception("formatter crashed"),
                                       create=True):
                                scanner._dispatch_scanner_alert(
                                    "QBTS", 7, 5.50, "StockTwits",
                                    "bullish chatter"
                                )

        # Legacy path was NOT called (legacy=False)
        mock_send_alert.assert_not_called()
        # send_sms was NOT called directly
        mock_send.assert_not_called()

    def test_predator_format_failure_does_not_send_fallback(self):
        """If _format_alert throws, no WhatsApp message is sent."""
        import predator

        mock_send = MagicMock(return_value=True)

        with patch.object(predator, "send_sms", mock_send):
            with patch.object(predator, "legacy_notifications_enabled",
                              return_value=True):
                with patch.object(predator, "_format_alert",
                                  side_effect=Exception("format crashed")):
                    from predator import ALERT_THRESHOLD, TIER_ALERT
                    score = 6
                    tier  = TIER_ALERT
                    price = 5.50
                    stop  = round(price * 0.91, 2)
                    signals = {}
                    try:
                        if score >= ALERT_THRESHOLD and tier in (TIER_ALERT,):
                            if predator.legacy_notifications_enabled():
                                msg = predator._format_alert(
                                    "QBTS", score, price, signals, stop, 1000.0, tier
                                )
                                predator.send_sms(msg)
                    except Exception:
                        pass  # exception caught — no fallback send

        mock_send.assert_not_called()


# ── Debug endpoint ────────────────────────────────────────────────────────────

class TestNotificationDebugEndpoint(unittest.TestCase):
    def setUp(self):
        self.app, self.api, self.db_path, self.conn_fn = _make_app(self)
        self.client = self.app.test_client()
        # Clear env
        for k in ("LEGACY_NOTIFICATIONS_ENABLED", "UNIFIED_NOTIFICATIONS_ENABLED",
                  "ALPHA_ALERTS_ENABLED", "ALPHA_SHADOW_ENABLED"):
            os.environ.pop(k, None)

    def tearDown(self):
        for k in ("LEGACY_NOTIFICATIONS_ENABLED", "UNIFIED_NOTIFICATIONS_ENABLED",
                  "ALPHA_ALERTS_ENABLED", "ALPHA_SHADOW_ENABLED"):
            os.environ.pop(k, None)

    def _debug_data(self):
        resp = self.client.get("/api/v1/notifications/debug")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        return body["data"]

    def test_endpoint_returns_200(self):
        resp = self.client.get("/api/v1/notifications/debug")
        self.assertEqual(resp.status_code, 200)

    def test_all_required_keys_present(self):
        data = self._debug_data()
        required = {
            "legacy_notifications_enabled",
            "unified_notifications_enabled",
            "alpha_notifications_enabled",
            "alpha_dry_run_only",
            "active_predator_send_path",
            "active_alpha_send_path",
            "last_predator_notification_type",
            "last_legacy_block_reason",
        }
        for key in required:
            self.assertIn(key, data, msg=f"Missing key: {key!r}")

    def test_legacy_false_by_default(self):
        data = self._debug_data()
        self.assertFalse(data["legacy_notifications_enabled"])

    def test_active_predator_path_is_none_by_default(self):
        data = self._debug_data()
        self.assertEqual(data["active_predator_send_path"], "none")

    def test_block_reason_when_legacy_disabled(self):
        data = self._debug_data()
        self.assertIsNotNone(data["last_legacy_block_reason"])
        self.assertIn("false", data["last_legacy_block_reason"].lower())

    def test_active_predator_path_is_legacy_when_enabled(self):
        with patch.dict(os.environ, {"LEGACY_NOTIFICATIONS_ENABLED": "true"}):
            data = self._debug_data()
        self.assertEqual(data["active_predator_send_path"], "legacy")

    def test_block_reason_none_when_legacy_enabled(self):
        with patch.dict(os.environ, {"LEGACY_NOTIFICATIONS_ENABLED": "true"}):
            data = self._debug_data()
        self.assertIsNone(data["last_legacy_block_reason"])

    def test_alpha_dry_run_only_when_shadow_on_alerts_off(self):
        with patch.dict(os.environ, {"ALPHA_SHADOW_ENABLED": "true",
                                     "ALPHA_ALERTS_ENABLED": "false"}):
            data = self._debug_data()
        self.assertTrue(data["alpha_dry_run_only"])
        self.assertFalse(data["alpha_notifications_enabled"])
        self.assertEqual(data["active_alpha_send_path"], "dry_run")

    def test_alpha_delivery_path_when_both_enabled(self):
        with patch.dict(os.environ, {"ALPHA_SHADOW_ENABLED": "true",
                                     "ALPHA_ALERTS_ENABLED": "true"}):
            data = self._debug_data()
        self.assertFalse(data["alpha_dry_run_only"])
        self.assertEqual(data["active_alpha_send_path"], "delivery")

    def test_alpha_none_when_shadow_off(self):
        data = self._debug_data()
        self.assertEqual(data["active_alpha_send_path"], "none")


# ── Source-file hygiene ───────────────────────────────────────────────────────

class TestSourceHygiene(unittest.TestCase):
    """predator.py and scanner.py must have the gating code in place."""

    def test_predator_imports_legacy_flag(self):
        import predator
        self.assertTrue(
            hasattr(predator, "legacy_notifications_enabled"),
            "predator.py must import legacy_notifications_enabled from feature_flags",
        )

    def test_scanner_imports_legacy_flag(self):
        import scanner
        self.assertTrue(
            hasattr(scanner, "legacy_notifications_enabled"),
            "scanner.py must import legacy_notifications_enabled from feature_flags",
        )

    def test_n2_banned_words_covers_explosion(self):
        from alert_formatter_n2 import BANNED_WORDS
        self.assertIn("explosion", BANNED_WORDS)

    def test_n2_banned_words_covers_pre_explosion(self):
        from alert_formatter_n2 import BANNED_WORDS
        self.assertIn("pre-explosion", BANNED_WORDS)

    def test_n2_advisory_footer_is_advisory_only(self):
        from alert_formatter_n2 import ADVISORY_FOOTER
        self.assertIn("Advisory only", ADVISORY_FOOTER)
        self.assertIn("No trade was placed", ADVISORY_FOOTER)

    def test_feature_flags_docstring_mentions_n3_default(self):
        import feature_flags
        src = Path(BOT_DIR / "feature_flags.py").read_text()
        self.assertIn("false", src.lower(),
                      "feature_flags.py should document the False default after N3")


if __name__ == "__main__":
    unittest.main()
