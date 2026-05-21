"""
Feature flags for the unified notification gateway rollout (Phase N1/N3).

All flags read from environment variables at call time (not import time),
so tests can override them via monkeypatching or os.environ.

Safe-rollout defaults (N3 cutover):
  LEGACY_NOTIFICATIONS_ENABLED   = false  (legacy PRE-EXPLOSION path disabled)
  UNIFIED_NOTIFICATIONS_ENABLED  = false  (gateway inactive — no automatic sends)
  SHADOW_COMPARE_NOTIFICATIONS   = false  (no comparison logging)

Set LEGACY_NOTIFICATIONS_ENABLED=true in Railway only to restore the old
score-threshold Predator / scanner send path (emergency rollback only).
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
    """Legacy score-threshold routing (scanner/predator send PRE-EXPLOSION style).

    Default is False after N3 cutover — legacy path disabled in production.
    Set LEGACY_NOTIFICATIONS_ENABLED=true only for emergency rollback.
    """
    return _env_bool("LEGACY_NOTIFICATIONS_ENABLED", False)


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
