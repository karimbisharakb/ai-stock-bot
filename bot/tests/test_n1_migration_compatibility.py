"""
Phase N1 — Migration compatibility tests.

Verifies backward-compatible rollout: feature flags correctly route
between legacy and unified paths, and the new modules import cleanly
without disturbing existing code.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch


# ─────────────────────────────────────────────
# Feature flag defaults
# ─────────────────────────────────────────────

class TestFeatureFlagDefaults:
    def test_legacy_disabled_by_default(self):
        # N3 cutover: default flipped to False to stop legacy PRE-EXPLOSION alerts
        import feature_flags
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LEGACY_NOTIFICATIONS_ENABLED", None)
            assert feature_flags.legacy_notifications_enabled() is False

    def test_unified_disabled_by_default(self):
        import feature_flags
        os.environ.pop("UNIFIED_NOTIFICATIONS_ENABLED", None)
        assert feature_flags.unified_notifications_enabled() is False

    def test_shadow_disabled_by_default(self):
        import feature_flags
        os.environ.pop("SHADOW_COMPARE_NOTIFICATIONS", None)
        assert feature_flags.shadow_compare_enabled() is False


class TestFeatureFlagEnvOverride:
    def test_legacy_disabled_via_env(self):
        import feature_flags
        with patch.dict(os.environ, {"LEGACY_NOTIFICATIONS_ENABLED": "false"}):
            assert feature_flags.legacy_notifications_enabled() is False

    def test_unified_enabled_via_env(self):
        import feature_flags
        with patch.dict(os.environ, {"UNIFIED_NOTIFICATIONS_ENABLED": "true"}):
            assert feature_flags.unified_notifications_enabled() is True

    def test_shadow_enabled_via_env(self):
        import feature_flags
        with patch.dict(os.environ, {"SHADOW_COMPARE_NOTIFICATIONS": "1"}):
            assert feature_flags.shadow_compare_enabled() is True

    def test_yes_string_is_truthy(self):
        import feature_flags
        with patch.dict(os.environ, {"UNIFIED_NOTIFICATIONS_ENABLED": "yes"}):
            assert feature_flags.unified_notifications_enabled() is True

    def test_zero_string_is_falsy(self):
        import feature_flags
        with patch.dict(os.environ, {"LEGACY_NOTIFICATIONS_ENABLED": "0"}):
            assert feature_flags.legacy_notifications_enabled() is False

    def test_unknown_string_falls_back_to_default(self):
        import feature_flags
        with patch.dict(os.environ, {"UNIFIED_NOTIFICATIONS_ENABLED": "maybe"}):
            # unknown → default (False)
            assert feature_flags.unified_notifications_enabled() is False


# ─────────────────────────────────────────────
# Legacy path still executes when flag is on
# ─────────────────────────────────────────────

class TestLegacyPathExecution:
    def test_dispatch_calls_legacy_send_alert_when_legacy_enabled(self):
        """scanner._dispatch_scanner_alert() must call _send_alert when legacy=True."""
        import scanner
        calls = []
        original = scanner._send_alert

        def mock_send(ticker, score, price, discovery, reason):
            calls.append(ticker)

        scanner._send_alert = mock_send
        try:
            with patch.dict(os.environ, {
                "LEGACY_NOTIFICATIONS_ENABLED":  "true",
                "UNIFIED_NOTIFICATIONS_ENABLED": "false",
                "SHADOW_COMPARE_NOTIFICATIONS":  "false",
            }):
                scanner._dispatch_scanner_alert("NVDA", 8, 150.0, "StockTwits trending", "RSI bullish")
        finally:
            scanner._send_alert = original

        assert "NVDA" in calls

    def test_dispatch_skips_legacy_send_alert_when_legacy_disabled(self):
        """When legacy=False and unified=True, legacy send must NOT be called."""
        import scanner
        calls = []
        original = scanner._send_alert

        def mock_send(*args):
            calls.append(args[0])  # ticker

        scanner._send_alert = mock_send
        try:
            with patch.dict(os.environ, {
                "LEGACY_NOTIFICATIONS_ENABLED":  "false",
                "UNIFIED_NOTIFICATIONS_ENABLED": "true",
                "SHADOW_COMPARE_NOTIFICATIONS":  "false",
            }):
                # gateway.submit will fail without real DB — that's OK for this test
                try:
                    scanner._dispatch_scanner_alert("BBAI", 7, 12.0, "StockTwits trending", "MACD bullish")
                except Exception:
                    pass
        finally:
            scanner._send_alert = original

        assert calls == []


# ─────────────────────────────────────────────
# Module imports (no circular deps, no crashes)
# ─────────────────────────────────────────────

class TestModuleImports:
    def test_feature_flags_imports_cleanly(self):
        import feature_flags
        assert hasattr(feature_flags, "legacy_notifications_enabled")
        assert hasattr(feature_flags, "unified_notifications_enabled")
        assert hasattr(feature_flags, "shadow_compare_enabled")

    def test_alert_schema_imports_cleanly(self):
        from alert_schema import AlertCandidate, EligibilityResult, OutboundAlert, SuppressedAlert
        assert AlertCandidate is not None

    def test_notification_policy_imports_cleanly(self):
        from notification_policy import NotificationPolicy
        assert NotificationPolicy is not None

    def test_alert_eligibility_imports_cleanly(self):
        from alert_eligibility import check_eligibility
        assert callable(check_eligibility)

    def test_alert_formatter_imports_cleanly(self):
        from alert_formatter import format_alert, resolve_tier, resolve_confidence
        assert callable(format_alert)

    def test_alert_gateway_imports_cleanly(self):
        from alert_gateway import AlertGateway
        assert AlertGateway is not None


# ─────────────────────────────────────────────
# NotificationPolicy named constructors
# ─────────────────────────────────────────────

class TestPolicyNamedConstructors:
    def test_conviction_only_default(self):
        from notification_policy import NotificationPolicy
        p = NotificationPolicy.conviction_only()
        assert p.min_tier == "CONVICTION"
        assert p.dry_run is False
        assert p.shadow_mode is False

    def test_alert_and_above(self):
        from notification_policy import NotificationPolicy
        p = NotificationPolicy.alert_and_above()
        assert p.min_tier == "ALERT"

    def test_dry_run_mode(self):
        from notification_policy import NotificationPolicy
        p = NotificationPolicy.dry_run_mode()
        assert p.dry_run is True
        assert p.shadow_mode is False

    def test_shadow_mode_policy(self):
        from notification_policy import NotificationPolicy
        p = NotificationPolicy.shadow_mode_policy()
        assert p.shadow_mode is True
        assert p.dry_run is False

    def test_dedup_window_default_is_24h(self):
        from notification_policy import NotificationPolicy
        p = NotificationPolicy()
        assert p.dedup_window_hours == 24

    def test_rate_limit_default_is_10(self):
        from notification_policy import NotificationPolicy
        p = NotificationPolicy()
        assert p.rate_limit_per_hour == 10
