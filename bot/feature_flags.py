"""
Feature flags for the unified notification gateway rollout (Phase N1).

All flags read from environment variables at call time (not import time),
so tests can override them via monkeypatching or os.environ.

Safe-rollout defaults:
  LEGACY_NOTIFICATIONS_ENABLED   = true   (preserve current behavior)
  UNIFIED_NOTIFICATIONS_ENABLED  = false  (gateway inactive)
  SHADOW_COMPARE_NOTIFICATIONS   = false  (no comparison logging)
"""
import os


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name, "").strip().lower()
    if val in ("1", "true", "yes"):
        return True
    if val in ("0", "false", "no"):
        return False
    return default


def legacy_notifications_enabled() -> bool:
    """Legacy score-threshold routing is active (scanner/predator send directly)."""
    return _env_bool("LEGACY_NOTIFICATIONS_ENABLED", True)


def unified_notifications_enabled() -> bool:
    """Unified alert gateway is the active dispatch path."""
    return _env_bool("UNIFIED_NOTIFICATIONS_ENABLED", False)


def shadow_compare_enabled() -> bool:
    """Run unified gateway alongside legacy; log mismatches, never send from gateway."""
    return _env_bool("SHADOW_COMPARE_NOTIFICATIONS", False)


def alpha_shadow_enabled() -> bool:
    """Alpha engine runs alongside Predator in observation-only mode."""
    return _env_bool("ALPHA_SHADOW_ENABLED", False)


def alpha_alerts_enabled() -> bool:
    """Alpha engine can trigger its own WhatsApp alerts (requires alpha_shadow_enabled)."""
    return _env_bool("ALPHA_ALERTS_ENABLED", False)
