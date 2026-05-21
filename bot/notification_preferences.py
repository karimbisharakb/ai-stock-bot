"""
Phase N5 — Notification Preferences and Digest Rules.

Single-user preference store controlling what the notification center surfaces
and how digests are assembled. No WhatsApp sends, no push notifications, no
trades. Read/filter only.
"""
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import database

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

PREF_CATEGORIES = (
    "BRIEF",
    "ALPHA",
    "PORTFOLIO",
    "RISK",
    "REGIME",
    "RESEARCH",
    "CATALYST",
    "CHECKLIST",
    "WEEKLY_REVIEW",
    "SYSTEM",
)

PREF_SEVERITIES = ("INFO", "WATCH", "WARNING", "CRITICAL")

# Mapping from notification_center severity → numeric weight (higher = more severe)
_SEVERITY_RANK = {"DEBUG": 0, "INFO": 1, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}

DIGEST_MODES = ("OFF", "DAILY", "MORNING_AND_EOD", "WEEKLY")

# N5 categories map to notification_center categories (n4 uses different names for some)
_PREF_CAT_TO_NC_CATS = {
    "BRIEF":        ("SYSTEM",),
    "ALPHA":        ("ALPHA_SIGNAL",),
    "PORTFOLIO":    ("PORTFOLIO",),
    "RISK":         ("RISK",),
    "REGIME":       ("REGIME",),
    "RESEARCH":     ("RESEARCH",),
    "CATALYST":     ("CATALYST",),
    "CHECKLIST":    ("COMPLIANCE",),
    "WEEKLY_REVIEW": ("PERFORMANCE",),
    "SYSTEM":       ("SYSTEM",),
}

# Reverse: nc category → pref category (first match wins if multiple map to same)
_NC_CAT_TO_PREF_CAT: dict = {}
for _pcat, _ncats in _PREF_CAT_TO_NC_CATS.items():
    for _nc in _ncats:
        if _nc not in _NC_CAT_TO_PREF_CAT:
            _NC_CAT_TO_PREF_CAT[_nc] = _pcat

DEFAULT_PREFS = {
    "enabled_categories":       list(PREF_CATEGORIES),
    "minimum_severity":         "INFO",
    "quiet_hours_enabled":      False,
    "quiet_hours_start":        "22:00",
    "quiet_hours_end":          "07:00",
    "timezone":                 "America/Toronto",
    "digest_mode":              "OFF",
    "max_notifications_per_digest": 20,
    "include_read_items":       False,
    "auto_archive_after_days":  7,
}

DIGEST_MAX_PER_SECTION = 5


# ── DB bootstrap ──────────────────────────────────────────────────────────────

def _ensure_tables() -> None:
    conn = database.get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notification_preferences (
                id                          INTEGER PRIMARY KEY CHECK(id = 1),
                enabled_categories          TEXT NOT NULL DEFAULT '[]',
                minimum_severity            TEXT NOT NULL DEFAULT 'INFO',
                quiet_hours_enabled         INTEGER NOT NULL DEFAULT 0,
                quiet_hours_start           TEXT NOT NULL DEFAULT '22:00',
                quiet_hours_end             TEXT NOT NULL DEFAULT '07:00',
                timezone                    TEXT NOT NULL DEFAULT 'America/Toronto',
                digest_mode                 TEXT NOT NULL DEFAULT 'OFF',
                max_notifications_per_digest INTEGER NOT NULL DEFAULT 20,
                include_read_items          INTEGER NOT NULL DEFAULT 0,
                auto_archive_after_days     INTEGER NOT NULL DEFAULT 7,
                updated_at                  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notification_preferences_categories (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                category            TEXT NOT NULL UNIQUE,
                enabled             INTEGER NOT NULL DEFAULT 1,
                minimum_severity    TEXT,
                digest_only         INTEGER NOT NULL DEFAULT 0,
                quiet_hours_override INTEGER,
                updated_at          TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_npc_cat ON notification_preferences_categories(category)"
        )
        conn.commit()
    finally:
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _severity_rank(sev: str) -> int:
    return _SEVERITY_RANK.get((sev or "INFO").upper(), 1)


def _parse_time(t: str) -> tuple:
    """Parse 'HH:MM' → (hour, minute). Returns (0, 0) on failure."""
    try:
        h, m = t.split(":")
        return int(h), int(m)
    except Exception:
        return 0, 0


def _quiet_hours_active(prefs: dict) -> bool:
    if not prefs.get("quiet_hours_enabled"):
        return False
    try:
        import pytz
        tz = pytz.timezone(prefs.get("timezone", "America/Toronto"))
        now_local = datetime.now(tz=timezone.utc).astimezone(tz)
        cur_min = now_local.hour * 60 + now_local.minute
        sh, sm = _parse_time(prefs.get("quiet_hours_start", "22:00"))
        eh, em = _parse_time(prefs.get("quiet_hours_end", "07:00"))
        start_min = sh * 60 + sm
        end_min = eh * 60 + em
        # crosses midnight
        if start_min > end_min:
            return cur_min >= start_min or cur_min < end_min
        return start_min <= cur_min < end_min
    except Exception:
        return False


# ── Preferences CRUD ──────────────────────────────────────────────────────────

def get_preferences() -> dict:
    """Return the single-user preferences row, seeding defaults on first call."""
    import json
    _ensure_tables()
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM notification_preferences WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return dict(DEFAULT_PREFS)

    d = dict(row)
    try:
        d["enabled_categories"] = json.loads(d.get("enabled_categories", "[]") or "[]")
    except Exception:
        d["enabled_categories"] = list(PREF_CATEGORIES)
    d["quiet_hours_enabled"] = bool(d.get("quiet_hours_enabled", 0))
    d["include_read_items"] = bool(d.get("include_read_items", 0))
    return d


def update_preferences(updates: dict) -> dict:
    """
    Merge updates into the single preferences row and persist.
    Returns the new preferences state.
    """
    import json

    _ensure_tables()
    current = get_preferences()
    current.update(
        {k: v for k, v in updates.items() if k in DEFAULT_PREFS or k == "updated_at"}
    )

    # Validate
    if current.get("minimum_severity") not in PREF_SEVERITIES:
        current["minimum_severity"] = "INFO"
    if current.get("digest_mode") not in DIGEST_MODES:
        current["digest_mode"] = "OFF"
    cats = [c for c in (current.get("enabled_categories") or []) if c in PREF_CATEGORIES]
    current["enabled_categories"] = cats or list(PREF_CATEGORIES)

    now = _now_iso()
    conn = database.get_connection()
    try:
        conn.execute(
            """INSERT INTO notification_preferences
               (id, enabled_categories, minimum_severity,
                quiet_hours_enabled, quiet_hours_start, quiet_hours_end,
                timezone, digest_mode, max_notifications_per_digest,
                include_read_items, auto_archive_after_days, updated_at)
               VALUES (1,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 enabled_categories=excluded.enabled_categories,
                 minimum_severity=excluded.minimum_severity,
                 quiet_hours_enabled=excluded.quiet_hours_enabled,
                 quiet_hours_start=excluded.quiet_hours_start,
                 quiet_hours_end=excluded.quiet_hours_end,
                 timezone=excluded.timezone,
                 digest_mode=excluded.digest_mode,
                 max_notifications_per_digest=excluded.max_notifications_per_digest,
                 include_read_items=excluded.include_read_items,
                 auto_archive_after_days=excluded.auto_archive_after_days,
                 updated_at=excluded.updated_at""",
            (
                json.dumps(current["enabled_categories"]),
                current.get("minimum_severity", "INFO"),
                1 if current.get("quiet_hours_enabled") else 0,
                current.get("quiet_hours_start", "22:00"),
                current.get("quiet_hours_end", "07:00"),
                current.get("timezone", "America/Toronto"),
                current.get("digest_mode", "OFF"),
                int(current.get("max_notifications_per_digest", 20)),
                1 if current.get("include_read_items") else 0,
                int(current.get("auto_archive_after_days", 7)),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return get_preferences()


# ── Per-category overrides ────────────────────────────────────────────────────

def get_category_overrides() -> list:
    """Return all per-category override rows."""
    _ensure_tables()
    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM notification_preferences_categories ORDER BY category"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_category_override(category: str) -> Optional[dict]:
    _ensure_tables()
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM notification_preferences_categories WHERE category=?",
            (category.upper(),),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def upsert_category_override(category: str, updates: dict) -> dict:
    """Create or update a per-category override. Returns the saved row."""
    if category.upper() not in PREF_CATEGORIES:
        raise ValueError(f"unknown category {category!r}")

    _ensure_tables()
    now = _now_iso()
    cat = category.upper()

    allowed = {"enabled", "minimum_severity", "digest_only", "quiet_hours_override"}
    safe = {k: v for k, v in updates.items() if k in allowed}

    severity = safe.get("minimum_severity")
    if severity is not None and severity not in PREF_SEVERITIES:
        raise ValueError(f"invalid minimum_severity {severity!r}")

    conn = database.get_connection()
    try:
        existing = conn.execute(
            "SELECT * FROM notification_preferences_categories WHERE category=?",
            (cat,),
        ).fetchone()

        if existing:
            sets, params = [], []
            for col in ("enabled", "minimum_severity", "digest_only", "quiet_hours_override"):
                if col in safe:
                    sets.append(f"{col}=?")
                    val = safe[col]
                    if col in ("enabled", "digest_only", "quiet_hours_override"):
                        val = int(val) if val is not None else None
                    params.append(val)
            sets.append("updated_at=?")
            params.append(now)
            params.append(cat)
            conn.execute(
                f"UPDATE notification_preferences_categories SET {', '.join(sets)} WHERE category=?",
                params,
            )
        else:
            enabled = int(safe.get("enabled", 1))
            min_sev = safe.get("minimum_severity")
            digest_only = int(safe.get("digest_only", 0))
            qh_override = safe.get("quiet_hours_override")
            if qh_override is not None:
                qh_override = int(qh_override)
            conn.execute(
                """INSERT INTO notification_preferences_categories
                   (category, enabled, minimum_severity, digest_only, quiet_hours_override, updated_at)
                   VALUES (?,?,?,?,?,?)""",
                (cat, enabled, min_sev, digest_only, qh_override, now),
            )
        conn.commit()
    finally:
        conn.close()

    return get_category_override(cat)


# ── Filtering engine ──────────────────────────────────────────────────────────

def _nc_cat_to_pref_cat(nc_category: str) -> str:
    return _NC_CAT_TO_PREF_CAT.get(nc_category, "SYSTEM")


def _get_override_for_nc_cat(nc_category: str, overrides: list) -> Optional[dict]:
    pcat = _nc_cat_to_pref_cat(nc_category)
    for ov in overrides:
        if ov.get("category", "").upper() == pcat:
            return ov
    return None


def should_surface_notification(
    notification: dict,
    preferences: dict,
    overrides: Optional[list] = None,
) -> bool:
    """
    Return True if notification should appear in the normal (non-digest) list.

    Rules applied in order:
    1. Dismissed/archived are always hidden.
    2. Category disabled (globally or per-override) → hidden.
    3. Severity below threshold → hidden.
    4. digest_only override → hidden from normal list.
    5. include_read_items=False and status=READ → hidden.
    """
    status = (notification.get("status") or "UNREAD").upper()
    if status in ("DISMISSED", "ARCHIVED"):
        return False

    nc_cat = (notification.get("category") or "SYSTEM").upper()
    pref_cat = _nc_cat_to_pref_cat(nc_cat)

    enabled_cats = [c.upper() for c in (preferences.get("enabled_categories") or [])]
    if pref_cat not in enabled_cats:
        return False

    if overrides is None:
        overrides = []
    override = _get_override_for_nc_cat(nc_cat, overrides)

    if override:
        if not override.get("enabled", 1):
            return False
        if override.get("digest_only", 0):
            return False
        ov_sev = override.get("minimum_severity")
        if ov_sev and _severity_rank(notification.get("severity", "INFO")) < _severity_rank(ov_sev):
            return False

    global_min = preferences.get("minimum_severity", "INFO")
    if _severity_rank(notification.get("severity", "INFO")) < _severity_rank(global_min):
        return False

    if not preferences.get("include_read_items", False) and status == "READ":
        return False

    return True


def should_include_in_digest(
    notification: dict,
    preferences: dict,
    overrides: Optional[list] = None,
) -> bool:
    """
    Return True if notification should appear in a digest.

    Rules:
    1. Dismissed/archived are excluded.
    2. Category disabled globally → excluded (digest_only items still included).
    3. Severity below global threshold → excluded.
    4. include_read_items=False and status=READ → excluded.
    """
    status = (notification.get("status") or "UNREAD").upper()
    if status in ("DISMISSED", "ARCHIVED"):
        return False

    nc_cat = (notification.get("category") or "SYSTEM").upper()
    pref_cat = _nc_cat_to_pref_cat(nc_cat)

    enabled_cats = [c.upper() for c in (preferences.get("enabled_categories") or [])]
    if pref_cat not in enabled_cats:
        return False

    if overrides is None:
        overrides = []
    override = _get_override_for_nc_cat(nc_cat, overrides)

    if override:
        if not override.get("enabled", 1):
            return False
        ov_sev = override.get("minimum_severity")
        if ov_sev and _severity_rank(notification.get("severity", "INFO")) < _severity_rank(ov_sev):
            return False

    global_min = preferences.get("minimum_severity", "INFO")
    if _severity_rank(notification.get("severity", "INFO")) < _severity_rank(global_min):
        return False

    if not preferences.get("include_read_items", False) and status == "READ":
        return False

    return True


def apply_notification_preferences(
    notifications: list,
    preferences: dict,
    overrides: Optional[list] = None,
    include_filtered: bool = False,
) -> dict:
    """
    Filter a list of notifications through preferences.

    Returns:
        visible: filtered list for display
        filtered: items hidden by prefs (only populated if include_filtered=True)
        suppressed_count: total items suppressed
        quiet_hours_active: bool
    """
    if overrides is None:
        overrides = []

    visible, suppressed = [], []
    for n in notifications:
        if should_surface_notification(n, preferences, overrides):
            visible.append(n)
        else:
            suppressed.append(n)

    return {
        "visible": visible,
        "filtered": suppressed if include_filtered else [],
        "suppressed_count": len(suppressed),
        "quiet_hours_active": _quiet_hours_active(preferences),
    }


# ── Digest builder ────────────────────────────────────────────────────────────

def _build_digest(
    notifications: list,
    preferences: dict,
    overrides: Optional[list],
    title: str,
    mode: str,
) -> dict:
    """
    Deterministic digest from a pre-filtered notification list.
    """
    if overrides is None:
        overrides = []

    max_total = int(preferences.get("max_notifications_per_digest", 20))

    eligible = [
        n for n in notifications
        if should_include_in_digest(n, preferences, overrides)
    ]

    by_category: dict = {}
    by_severity: dict = {}
    for n in eligible:
        nc_cat = (n.get("category") or "SYSTEM").upper()
        pcat = _nc_cat_to_pref_cat(nc_cat)
        sev = (n.get("severity") or "INFO").upper()
        by_category[pcat] = by_category.get(pcat, 0) + 1
        by_severity[sev] = by_severity.get(sev, 0) + 1

    # Sort: CRITICAL first, then WARNING, then INFO/DEBUG; within tier by created_at desc
    def _sort_key(n):
        return (-_severity_rank(n.get("severity", "INFO")), n.get("created_at", ""))

    eligible_sorted = sorted(eligible, key=_sort_key)

    # Section carve-outs
    critical_warning = [
        n for n in eligible_sorted
        if (n.get("severity") or "INFO").upper() in ("CRITICAL", "WARNING")
    ][:DIGEST_MAX_PER_SECTION]

    alpha_items = [
        n for n in eligible_sorted
        if (n.get("category") or "").upper() == "ALPHA_SIGNAL"
    ][:DIGEST_MAX_PER_SECTION]

    risk_items = [
        n for n in eligible_sorted
        if (n.get("category") or "").upper() == "RISK"
    ][:DIGEST_MAX_PER_SECTION]

    other_cats = {"RESEARCH", "CATALYST", "COMPLIANCE"}
    research_catalyst_checklist = [
        n for n in eligible_sorted
        if (n.get("category") or "").upper() in other_cats
    ][:DIGEST_MAX_PER_SECTION]

    included: list = []
    seen_ids: set = set()

    def _add(items):
        for n in items:
            nid = n.get("notification_id", id(n))
            if nid not in seen_ids and len(included) < max_total:
                seen_ids.add(nid)
                included.append(n)

    _add(critical_warning)
    _add(alpha_items)
    _add(risk_items)
    _add(research_catalyst_checklist)
    # fill remaining slots with remaining eligible items
    _add(eligible_sorted)

    omitted = len(eligible) - len(included)

    return {
        "title":          title,
        "mode":           mode,
        "generated_at":   _now_iso(),
        "included_count": len(included),
        "omitted_count":  max(0, omitted),
        "by_category":    by_category,
        "by_severity":    by_severity,
        "top_critical_warning":           critical_warning,
        "top_alpha":                      alpha_items,
        "top_risk":                       risk_items,
        "top_research_catalyst_checklist": research_catalyst_checklist,
        "notifications":  included,
    }


def build_daily_digest(
    notifications: list,
    preferences: dict,
    overrides: Optional[list] = None,
) -> dict:
    return _build_digest(notifications, preferences, overrides, "Daily Digest", "daily")


def build_eod_digest(
    notifications: list,
    preferences: dict,
    overrides: Optional[list] = None,
) -> dict:
    return _build_digest(notifications, preferences, overrides, "End-of-Day Digest", "eod")


def build_weekly_digest(
    notifications: list,
    preferences: dict,
    overrides: Optional[list] = None,
) -> dict:
    return _build_digest(notifications, preferences, overrides, "Weekly Digest", "weekly")


# ── Public convenience: filtered summary extras ───────────────────────────────

def get_preference_summary_extras(
    all_notifications: list,
    preferences: dict,
    overrides: Optional[list] = None,
) -> dict:
    """
    Compute the extra preference-aware counts for the summary endpoint.

    Returns: visible_unread_count, filtered_count, suppressed_by_preferences_count,
             quiet_hours_active
    """
    if overrides is None:
        overrides = []

    unread = [n for n in all_notifications if (n.get("status") or "UNREAD").upper() == "UNREAD"]
    result = apply_notification_preferences(unread, preferences, overrides)

    return {
        "visible_unread_count":          len(result["visible"]),
        "filtered_count":                result["suppressed_count"],
        "suppressed_by_preferences_count": result["suppressed_count"],
        "quiet_hours_active":            result["quiet_hours_active"],
    }
