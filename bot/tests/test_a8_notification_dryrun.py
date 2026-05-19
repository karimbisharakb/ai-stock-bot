"""
Phase A8 — Alpha notification dry-run review tests.

Covers:
  - generate_notification_text(): format, banned words absent
  - check_banned_words(): detects banned words
  - PRE_ALERT / ALERT_READY / RARE_ALERT generate dry-run rows
  - NOT_READY / MONITOR do not generate dry-run rows
  - Idempotent generation (same candidate → same dry_run_id → INSERT OR IGNORE)
  - mark_reviewed(): DRY_RUN → REVIEWED, invalid transition raises
  - dismiss_dry_run(): DRY_RUN → DISMISSED, invalid from REVIEWED raises
  - expire_stale_dry_runs(): past-expiry rows become EXPIRED
  - get_dry_runs(): status filter, limit
  - API GET /alpha/notifications/dry-run — read-only, no auth needed
  - API POST generate requires auth
  - API POST review / dismiss require auth
  - No Twilio / send_sms called anywhere
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── DB isolation fixtures ──────────────────────────────────────────────────────

def _make_get_conn(path: str):
    def _get():
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    return _get


def _init_tables(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    c    = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS alpha_notification_dryruns (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            dry_run_id               TEXT    NOT NULL UNIQUE,
            ticker                   TEXT    NOT NULL,
            readiness_tier           TEXT    NOT NULL,
            alpha_score              REAL,
            alpha_tier               TEXT,
            setup_type               TEXT,
            message_text             TEXT    NOT NULL,
            reason                   TEXT,
            blocking_factors_json    TEXT,
            confirmation_needed_json TEXT,
            status                   TEXT    NOT NULL DEFAULT 'DRY_RUN',
            created_at               TEXT    NOT NULL,
            expires_at               TEXT    NOT NULL,
            reviewed_at              TEXT,
            reviewed_by              TEXT,
            review_note              TEXT,
            dismissed_at             TEXT,
            dismissed_by             TEXT,
            dismiss_reason           TEXT
        )
    """)
    conn.commit()
    conn.close()


@pytest.fixture()
def db_path(monkeypatch, tmp_path):
    path = str(tmp_path / "test.db")
    _init_tables(path)
    import database
    monkeypatch.setattr(database, "get_connection", _make_get_conn(path))
    import alpha_notification_dryrun
    monkeypatch.setattr(alpha_notification_dryrun, "_ensure_table", lambda: None)
    return path


def _insert_dry_run(db_path: str, dry_run_id: str = "abc123", ticker: str = "TEST",
                    readiness_tier: str = "PRE_ALERT", status: str = "DRY_RUN",
                    expires_at: str = None) -> None:
    if expires_at is None:
        expires_at = (datetime.now() + timedelta(hours=48)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO alpha_notification_dryruns
            (dry_run_id, ticker, readiness_tier, alpha_score, alpha_tier, setup_type,
             message_text, status, created_at, expires_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (dry_run_id, ticker, readiness_tier, 72.0, "HIGH_CONVICTION",
         "BREAKOUT_EXPANSION", "ALPHA ALERT — TEST\n\nNo trade placed.\nAdvisory only.",
         status, datetime.now().isoformat(), expires_at),
    )
    conn.commit()
    conn.close()


# ── Candidate/gate helpers ─────────────────────────────────────────────────────

def _make_candidate(ticker: str = "AAPL", alpha_score: float = 75.0,
                    alpha_tier: str = "HIGH_CONVICTION",
                    setup_type: str = "BREAKOUT_EXPANSION",
                    components: dict = None) -> dict:
    cs = components or {
        "relative_strength": {"score": 8.0, "data_quality": "FRESH"},
        "acceleration":      {"score": 7.0, "data_quality": "FRESH"},
        "breakout":          {"score": 7.5, "data_quality": "FRESH"},
        "catalyst":          {"score": 6.0, "data_quality": "FRESH"},
    }
    return {
        "ticker":                ticker,
        "alpha_score":           alpha_score,
        "alpha_tier":            alpha_tier,
        "setup_type":            setup_type,
        "component_scores_json": json.dumps(cs),
    }


def _make_gate_result(ticker: str = "AAPL", readiness_tier: str = "PRE_ALERT",
                      alpha_tier: str = "HIGH_CONVICTION",
                      blocking_factors: list = None,
                      confirmation_needed: list = None) -> dict:
    return {
        "ticker":               ticker,
        "readiness_tier":       readiness_tier,
        "alpha_tier":           alpha_tier,
        "alpha_score":          75.0,
        "setup_type":           "BREAKOUT_EXPANSION",
        "readiness_score":      62.0,
        "alert_ready":          readiness_tier in ("ALERT_READY", "RARE_ALERT"),
        "reason":               f"Alpha tier {alpha_tier!r} → readiness {readiness_tier!r}",
        "blocking_factors":     blocking_factors or [],
        "confirmation_needed":  confirmation_needed or ["volume_confirmation",
                                                        "price_holds_breakout_level"],
        "suggested_wait_window": "Watch next 1-2 days",
        "scan_time":            "2026-01-01T12:00:00",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. generate_notification_text() — pure function
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateNotificationText:
    def test_contains_ticker(self):
        from alpha_notification_dryrun import generate_notification_text
        text = generate_notification_text(_make_candidate("SHOP.TO"), _make_gate_result("SHOP.TO"))
        assert "SHOP.TO" in text

    def test_contains_alpha_score(self):
        from alpha_notification_dryrun import generate_notification_text
        text = generate_notification_text(_make_candidate(alpha_score=72.5),
                                          _make_gate_result())
        assert "72.5" in text

    def test_contains_no_trade_advisory(self):
        from alpha_notification_dryrun import generate_notification_text
        text = generate_notification_text(_make_candidate(), _make_gate_result())
        assert "No trade placed" in text
        assert "Advisory only" in text

    def test_pre_alert_header(self):
        from alpha_notification_dryrun import generate_notification_text
        text = generate_notification_text(_make_candidate(),
                                          _make_gate_result(readiness_tier="PRE_ALERT"))
        assert "ALPHA WATCH" in text

    def test_alert_ready_header(self):
        from alpha_notification_dryrun import generate_notification_text
        text = generate_notification_text(_make_candidate(alpha_score=75.0,
                                                          alpha_tier="HIGH_CONVICTION"),
                                          _make_gate_result(readiness_tier="ALERT_READY",
                                                            alpha_tier="HIGH_CONVICTION"))
        assert "ALPHA ALERT" in text

    def test_rare_alert_header(self):
        from alpha_notification_dryrun import generate_notification_text
        text = generate_notification_text(_make_candidate(alpha_score=90.0,
                                                          alpha_tier="RARE_SETUP"),
                                          _make_gate_result(readiness_tier="RARE_ALERT",
                                                            alpha_tier="RARE_SETUP"))
        assert "RARE SETUP" in text

    def test_contains_status_label(self):
        from alpha_notification_dryrun import generate_notification_text
        text = generate_notification_text(_make_candidate(), _make_gate_result())
        assert "Status:" in text
        assert "Almost ready" in text  # PRE_ALERT label

    def test_contains_setup_label(self):
        from alpha_notification_dryrun import generate_notification_text
        text = generate_notification_text(_make_candidate(setup_type="BREAKOUT_EXPANSION"),
                                          _make_gate_result())
        assert "Breakout expansion" in text

    def test_contains_why_section(self):
        from alpha_notification_dryrun import generate_notification_text
        text = generate_notification_text(_make_candidate(), _make_gate_result())
        assert "Why:" in text

    def test_why_from_components(self):
        from alpha_notification_dryrun import generate_notification_text
        cs = {"relative_strength": {"score": 9.0, "data_quality": "FRESH"}}
        text = generate_notification_text(_make_candidate(components=cs), _make_gate_result())
        assert "relative strength" in text.lower()

    def test_needs_section_from_confirmation(self):
        from alpha_notification_dryrun import generate_notification_text
        gate = _make_gate_result(confirmation_needed=["volume_confirmation"])
        text = generate_notification_text(_make_candidate(), gate)
        assert "Needs:" in text
        assert "Volume" in text

    def test_risk_section_from_blockers(self):
        from alpha_notification_dryrun import generate_notification_text
        gate = _make_gate_result(blocking_factors=["High trap rate for BREAKOUT_EXPANSION: 60%"])
        text = generate_notification_text(_make_candidate(), gate)
        assert "Risk:" in text

    def test_never_raises_on_empty_candidate(self):
        from alpha_notification_dryrun import generate_notification_text
        try:
            text = generate_notification_text({}, {})
            assert "Advisory only" in text
        except Exception as exc:
            pytest.fail(f"raised: {exc}")

    def test_deterministic(self):
        from alpha_notification_dryrun import generate_notification_text
        c = _make_candidate()
        g = _make_gate_result()
        assert generate_notification_text(c, g) == generate_notification_text(c, g)

    def test_short_enough_for_whatsapp(self):
        from alpha_notification_dryrun import generate_notification_text
        text = generate_notification_text(_make_candidate(), _make_gate_result())
        # WhatsApp limit is 4096 chars; a dry-run message should be much shorter
        assert len(text) < 1000


# ─────────────────────────────────────────────────────────────────────────────
# 2. check_banned_words()
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckBannedWords:
    def test_clean_text_returns_empty(self):
        from alpha_notification_dryrun import check_banned_words
        assert check_banned_words("Stock looks interesting. Advisory only.") == []

    def test_detects_moon(self):
        from alpha_notification_dryrun import check_banned_words
        assert "moon" in check_banned_words("This is going to moon!")

    def test_detects_explosion(self):
        from alpha_notification_dryrun import check_banned_words
        assert "explosion" in check_banned_words("Expecting an explosion in price")

    def test_detects_rocket(self):
        from alpha_notification_dryrun import check_banned_words
        assert "rocket" in check_banned_words("Rocket setup detected")

    def test_detects_must_buy(self):
        from alpha_notification_dryrun import check_banned_words
        assert "must buy" in check_banned_words("You must buy this now")

    def test_detects_guaranteed(self):
        from alpha_notification_dryrun import check_banned_words
        assert "guaranteed" in check_banned_words("Guaranteed profit")

    def test_case_insensitive(self):
        from alpha_notification_dryrun import check_banned_words
        assert check_banned_words("MOON TO THE MOON") != []

    def test_generated_messages_are_clean(self):
        from alpha_notification_dryrun import generate_notification_text, check_banned_words
        for tier in ("PRE_ALERT", "ALERT_READY", "RARE_ALERT"):
            text = generate_notification_text(
                _make_candidate(),
                _make_gate_result(readiness_tier=tier),
            )
            found = check_banned_words(text)
            assert found == [], f"Banned words found in {tier} message: {found}"

    def test_returns_list_of_found_words(self):
        from alpha_notification_dryrun import check_banned_words
        result = check_banned_words("moon rocket pump")
        assert len(result) >= 2


# ─────────────────────────────────────────────────────────────────────────────
# 3. generate_dry_runs() — gate integration + DB
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateDryRuns:
    def _mock_candidates(self, candidates: list, monkeypatch):
        import alpha_notification_dryrun as m
        monkeypatch.setattr(m, "_ensure_table", lambda: None)
        import alpha_alert_gate
        monkeypatch.setattr(alpha_alert_gate, "get_alert_candidates",
                            lambda limit=50: candidates)

    def test_pre_alert_generates_dry_run(self, db_path, monkeypatch):
        candidates = [
            {**_make_gate_result("AAPL", "PRE_ALERT"),
             "component_scores_json": json.dumps({})}
        ]
        # Patch get_alert_candidates inside the module under test
        import alpha_notification_dryrun as m
        monkeypatch.setattr(m, "_ensure_table", lambda: None)
        monkeypatch.setattr("alpha_alert_gate.get_alert_candidates", lambda limit=50: candidates)

        from alpha_notification_dryrun import generate_dry_runs
        rows = generate_dry_runs()
        assert len(rows) >= 1
        assert rows[0]["ticker"] == "AAPL"
        assert rows[0]["readiness_tier"] == "PRE_ALERT"
        assert rows[0]["status"] == "DRY_RUN"

    def test_alert_ready_generates_dry_run(self, db_path, monkeypatch):
        candidates = [
            {**_make_gate_result("NVDA", "ALERT_READY", "HIGH_CONVICTION"),
             "component_scores_json": json.dumps({})}
        ]
        import alpha_notification_dryrun as m
        monkeypatch.setattr(m, "_ensure_table", lambda: None)
        monkeypatch.setattr("alpha_alert_gate.get_alert_candidates", lambda limit=50: candidates)

        from alpha_notification_dryrun import generate_dry_runs
        rows = generate_dry_runs()
        assert any(r["ticker"] == "NVDA" for r in rows)

    def test_rare_alert_generates_dry_run(self, db_path, monkeypatch):
        candidates = [
            {**_make_gate_result("MSFT", "RARE_ALERT", "RARE_SETUP"),
             "component_scores_json": json.dumps({})}
        ]
        import alpha_notification_dryrun as m
        monkeypatch.setattr(m, "_ensure_table", lambda: None)
        monkeypatch.setattr("alpha_alert_gate.get_alert_candidates", lambda limit=50: candidates)

        from alpha_notification_dryrun import generate_dry_runs
        rows = generate_dry_runs()
        assert any(r["ticker"] == "MSFT" for r in rows)

    def test_not_ready_does_not_generate(self, db_path, monkeypatch):
        candidates = [
            {**_make_gate_result("IGNORE_ME", "NOT_READY"),
             "component_scores_json": json.dumps({})}
        ]
        import alpha_notification_dryrun as m
        monkeypatch.setattr(m, "_ensure_table", lambda: None)
        monkeypatch.setattr("alpha_alert_gate.get_alert_candidates", lambda limit=50: candidates)

        from alpha_notification_dryrun import generate_dry_runs
        rows = generate_dry_runs()
        assert not any(r["ticker"] == "IGNORE_ME" for r in rows)

    def test_monitor_does_not_generate(self, db_path, monkeypatch):
        candidates = [
            {**_make_gate_result("MONITOR_ME", "MONITOR"),
             "component_scores_json": json.dumps({})}
        ]
        import alpha_notification_dryrun as m
        monkeypatch.setattr(m, "_ensure_table", lambda: None)
        monkeypatch.setattr("alpha_alert_gate.get_alert_candidates", lambda limit=50: candidates)

        from alpha_notification_dryrun import generate_dry_runs
        rows = generate_dry_runs()
        assert not any(r["ticker"] == "MONITOR_ME" for r in rows)

    def test_idempotent_same_candidate(self, db_path, monkeypatch):
        candidates = [
            {**_make_gate_result("IDEM", "ALERT_READY"),
             "component_scores_json": json.dumps({})}
        ]
        import alpha_notification_dryrun as m
        monkeypatch.setattr(m, "_ensure_table", lambda: None)
        monkeypatch.setattr("alpha_alert_gate.get_alert_candidates", lambda limit=50: candidates)

        from alpha_notification_dryrun import generate_dry_runs
        rows1 = generate_dry_runs()
        rows2 = generate_dry_runs()
        assert len(rows1) == len(rows2)
        assert rows1[0]["dry_run_id"] == rows2[0]["dry_run_id"]

    def test_no_candidates_returns_empty(self, db_path, monkeypatch):
        import alpha_notification_dryrun as m
        monkeypatch.setattr(m, "_ensure_table", lambda: None)
        monkeypatch.setattr("alpha_alert_gate.get_alert_candidates", lambda limit=50: [])

        from alpha_notification_dryrun import generate_dry_runs
        assert generate_dry_runs() == []

    def test_message_text_stored(self, db_path, monkeypatch):
        candidates = [
            {**_make_gate_result("AAPL", "PRE_ALERT"),
             "component_scores_json": json.dumps({})}
        ]
        import alpha_notification_dryrun as m
        monkeypatch.setattr(m, "_ensure_table", lambda: None)
        monkeypatch.setattr("alpha_alert_gate.get_alert_candidates", lambda limit=50: candidates)

        from alpha_notification_dryrun import generate_dry_runs
        rows = generate_dry_runs()
        msg = rows[0]["message_text"]
        assert "AAPL" in msg
        assert "Advisory only" in msg

    def test_never_raises_on_empty_db(self, db_path, monkeypatch):
        import alpha_notification_dryrun as m
        monkeypatch.setattr(m, "_ensure_table", lambda: None)
        monkeypatch.setattr("alpha_alert_gate.get_alert_candidates", lambda limit=50: [])
        try:
            from alpha_notification_dryrun import generate_dry_runs
            generate_dry_runs()
        except Exception as exc:
            pytest.fail(f"raised: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# 4. mark_reviewed() and dismiss_dry_run() workflow
# ─────────────────────────────────────────────────────────────────────────────

class TestReviewWorkflow:
    def test_mark_reviewed_transitions_status(self, db_path):
        _insert_dry_run(db_path, dry_run_id="rev001")
        from alpha_notification_dryrun import mark_reviewed
        result = mark_reviewed("rev001", actor="operator", note="Looks good")
        assert result["status"] == "REVIEWED"
        assert result["reviewed_by"] == "operator"
        assert result["review_note"] == "Looks good"

    def test_dismiss_transitions_status(self, db_path):
        _insert_dry_run(db_path, dry_run_id="dis001")
        from alpha_notification_dryrun import dismiss_dry_run
        result = dismiss_dry_run("dis001", reason="Not relevant", actor="operator")
        assert result["status"] == "DISMISSED"
        assert result["dismissed_by"] == "operator"
        assert result["dismiss_reason"] == "Not relevant"

    def test_review_already_reviewed_raises(self, db_path):
        _insert_dry_run(db_path, dry_run_id="rev002", status="REVIEWED")
        from alpha_notification_dryrun import mark_reviewed
        with pytest.raises(ValueError, match="Invalid transition"):
            mark_reviewed("rev002")

    def test_dismiss_already_dismissed_raises(self, db_path):
        _insert_dry_run(db_path, dry_run_id="dis002", status="DISMISSED")
        from alpha_notification_dryrun import dismiss_dry_run
        with pytest.raises(ValueError, match="Invalid transition"):
            dismiss_dry_run("dis002")

    def test_dismiss_reviewed_raises(self, db_path):
        _insert_dry_run(db_path, dry_run_id="rev003", status="REVIEWED")
        from alpha_notification_dryrun import dismiss_dry_run
        with pytest.raises(ValueError):
            dismiss_dry_run("rev003")

    def test_not_found_raises(self, db_path):
        from alpha_notification_dryrun import mark_reviewed, dismiss_dry_run
        with pytest.raises(ValueError, match="not found"):
            mark_reviewed("nonexistent_id")
        with pytest.raises(ValueError, match="not found"):
            dismiss_dry_run("nonexistent_id")

    def test_reviewed_at_is_set(self, db_path):
        _insert_dry_run(db_path, dry_run_id="rev004")
        from alpha_notification_dryrun import mark_reviewed
        result = mark_reviewed("rev004")
        assert result["reviewed_at"] is not None

    def test_dismissed_at_is_set(self, db_path):
        _insert_dry_run(db_path, dry_run_id="dis003")
        from alpha_notification_dryrun import dismiss_dry_run
        result = dismiss_dry_run("dis003")
        assert result["dismissed_at"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# 5. expire_stale_dry_runs()
# ─────────────────────────────────────────────────────────────────────────────

class TestExpireStale:
    def test_past_expiry_dry_run_is_expired(self, db_path):
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        _insert_dry_run(db_path, dry_run_id="exp001", status="DRY_RUN", expires_at=past)
        from alpha_notification_dryrun import expire_stale_dry_runs
        count = expire_stale_dry_runs()
        assert count >= 1

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status FROM alpha_notification_dryruns WHERE dry_run_id = ?", ("exp001",)
        ).fetchone()
        conn.close()
        assert row[0] == "EXPIRED"

    def test_past_expiry_reviewed_is_expired(self, db_path):
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        _insert_dry_run(db_path, dry_run_id="exp002", status="REVIEWED", expires_at=past)
        from alpha_notification_dryrun import expire_stale_dry_runs
        expire_stale_dry_runs()

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status FROM alpha_notification_dryruns WHERE dry_run_id = ?", ("exp002",)
        ).fetchone()
        conn.close()
        assert row[0] == "EXPIRED"

    def test_future_expiry_not_touched(self, db_path):
        _insert_dry_run(db_path, dry_run_id="keep001", status="DRY_RUN")  # future expires_at
        from alpha_notification_dryrun import expire_stale_dry_runs
        expire_stale_dry_runs()

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status FROM alpha_notification_dryruns WHERE dry_run_id = ?", ("keep001",)
        ).fetchone()
        conn.close()
        assert row[0] == "DRY_RUN"

    def test_dismissed_not_expired(self, db_path):
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        _insert_dry_run(db_path, dry_run_id="dis_old", status="DISMISSED", expires_at=past)
        from alpha_notification_dryrun import expire_stale_dry_runs
        expire_stale_dry_runs()

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status FROM alpha_notification_dryruns WHERE dry_run_id = ?", ("dis_old",)
        ).fetchone()
        conn.close()
        assert row[0] == "DISMISSED"  # terminal — unchanged

    def test_returns_count(self, db_path):
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        for i in range(3):
            _insert_dry_run(db_path, dry_run_id=f"ex{i:03d}", status="DRY_RUN", expires_at=past)
        from alpha_notification_dryrun import expire_stale_dry_runs
        assert expire_stale_dry_runs() == 3


# ─────────────────────────────────────────────────────────────────────────────
# 6. get_dry_runs() — filtering
# ─────────────────────────────────────────────────────────────────────────────

class TestGetDryRuns:
    def test_empty_db_returns_empty(self, db_path):
        from alpha_notification_dryrun import get_dry_runs
        assert get_dry_runs() == []

    def test_returns_active_by_default(self, db_path):
        _insert_dry_run(db_path, dry_run_id="a1", status="DRY_RUN")
        _insert_dry_run(db_path, dry_run_id="a2", status="REVIEWED")
        _insert_dry_run(db_path, dry_run_id="a3", status="DISMISSED")
        from alpha_notification_dryrun import get_dry_runs
        rows = get_dry_runs()
        statuses = {r["status"] for r in rows}
        assert "DISMISSED" not in statuses

    def test_filter_by_status(self, db_path):
        _insert_dry_run(db_path, dry_run_id="f1", status="DRY_RUN")
        _insert_dry_run(db_path, dry_run_id="f2", status="DISMISSED")
        from alpha_notification_dryrun import get_dry_runs
        rows = get_dry_runs(status_filter="DISMISSED")
        assert all(r["status"] == "DISMISSED" for r in rows)

    def test_limit_respected(self, db_path):
        for i in range(5):
            _insert_dry_run(db_path, dry_run_id=f"lim{i:03d}")
        from alpha_notification_dryrun import get_dry_runs
        rows = get_dry_runs(limit=2)
        assert len(rows) <= 2


# ─────────────────────────────────────────────────────────────────────────────
# 7. API endpoints
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def app_client(db_path, monkeypatch):
    import database
    monkeypatch.setattr(database, "get_connection", _make_get_conn(db_path))
    import alpha_notification_dryrun
    monkeypatch.setattr(alpha_notification_dryrun, "_ensure_table", lambda: None)

    from flask import Flask
    from api import api_bp, cache_clear
    flask_app = Flask("test")
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(api_bp)
    cache_clear()
    with flask_app.test_client() as client:
        yield client


class TestApiDryRunList:
    def test_get_list_empty(self, app_client):
        rv = app_client.get("/api/v1/alpha/notifications/dry-run")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert data["data"]["total"] == 0

    def test_get_list_with_rows(self, app_client, db_path):
        _insert_dry_run(db_path, dry_run_id="list001")
        from api import cache_clear
        cache_clear()
        rv = app_client.get("/api/v1/alpha/notifications/dry-run")
        data = rv.get_json()
        assert data["ok"] is True
        assert data["data"]["total"] == 1

    def test_get_list_status_filter(self, app_client, db_path):
        _insert_dry_run(db_path, dry_run_id="sf1", status="DRY_RUN")
        _insert_dry_run(db_path, dry_run_id="sf2", status="DISMISSED")
        from api import cache_clear
        cache_clear()
        rv = app_client.get("/api/v1/alpha/notifications/dry-run?status=DISMISSED")
        data = rv.get_json()
        assert data["ok"] is True
        for row in data["data"]["results"]:
            assert row["status"] == "DISMISSED"

    def test_get_list_invalid_status_returns_400(self, app_client):
        rv = app_client.get("/api/v1/alpha/notifications/dry-run?status=INVALID")
        assert rv.status_code == 400

    def test_get_list_note_confirms_no_alerts(self, app_client):
        rv = app_client.get("/api/v1/alpha/notifications/dry-run")
        data = rv.get_json()
        note = data["data"]["note"].lower()
        assert "no real" in note or "dry-run" in note or "not sent" in note or "simulation" in note

    def test_get_list_no_auth_required(self, app_client):
        # GET is read-only — no auth header needed
        rv = app_client.get("/api/v1/alpha/notifications/dry-run")
        assert rv.status_code == 200

    def test_envelope_structure(self, app_client):
        rv = app_client.get("/api/v1/alpha/notifications/dry-run")
        data = rv.get_json()
        assert "ok" in data and "data" in data and "meta" in data


class TestApiDryRunGenerate:
    def _candidates(self, monkeypatch):
        candidates = [_make_gate_result("GEN1", "PRE_ALERT")]
        import alpha_notification_dryrun as m
        monkeypatch.setattr(m, "_ensure_table", lambda: None)
        monkeypatch.setattr("alpha_alert_gate.get_alert_candidates",
                            lambda limit=50: candidates)

    def test_generate_requires_auth(self, app_client, monkeypatch):
        self._candidates(monkeypatch)
        rv = app_client.post("/api/v1/alpha/notifications/dry-run/generate",
                             headers={})
        # With no API_SECRET set → fails-open (authorized). But let's force unauth:
        import os
        with patch.dict(os.environ, {"API_SECRET": "secret123"}):
            rv2 = app_client.post("/api/v1/alpha/notifications/dry-run/generate",
                                  headers={})
            assert rv2.status_code == 401

    def test_generate_with_auth(self, app_client, monkeypatch, db_path):
        self._candidates(monkeypatch)
        import os
        with patch.dict(os.environ, {"API_SECRET": "testtoken"}):
            rv = app_client.post(
                "/api/v1/alpha/notifications/dry-run/generate",
                headers={"Authorization": "Bearer testtoken"},
            )
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert "generated" in data["data"]
        assert "note" in data["data"]

    def test_generate_note_confirms_no_real_notifications(self, app_client, monkeypatch):
        self._candidates(monkeypatch)
        rv = app_client.post("/api/v1/alpha/notifications/dry-run/generate")
        data = rv.get_json()
        if data.get("ok"):
            assert "no real" in data["data"]["note"].lower() or \
                   "dry-run" in data["data"]["note"].lower()


class TestApiDryRunReview:
    def test_review_requires_auth(self, app_client, db_path):
        _insert_dry_run(db_path, dry_run_id="rva001")
        import os
        with patch.dict(os.environ, {"API_SECRET": "secret"}):
            rv = app_client.post("/api/v1/alpha/notifications/dry-run/rva001/review",
                                 headers={},
                                 json={"note": "Looks good"})
            assert rv.status_code == 401

    def test_review_with_auth(self, app_client, db_path):
        _insert_dry_run(db_path, dry_run_id="rva002")
        import os
        with patch.dict(os.environ, {"API_SECRET": "testtoken"}):
            rv = app_client.post(
                "/api/v1/alpha/notifications/dry-run/rva002/review",
                headers={"Authorization": "Bearer testtoken"},
                json={"note": "Reviewed"},
            )
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert data["data"]["status"] == "REVIEWED"

    def test_review_not_found_returns_400(self, app_client, db_path):
        with patch.dict(os.environ, {"API_SECRET": ""}):  # fails-open
            rv = app_client.post("/api/v1/alpha/notifications/dry-run/nonexistent/review",
                                 json={})
            assert rv.status_code == 400

    def test_review_already_reviewed_returns_400(self, app_client, db_path):
        _insert_dry_run(db_path, dry_run_id="rva003", status="REVIEWED")
        with patch.dict(os.environ, {"API_SECRET": ""}):
            rv = app_client.post("/api/v1/alpha/notifications/dry-run/rva003/review",
                                 json={})
            assert rv.status_code == 400


class TestApiDryRunDismiss:
    def test_dismiss_requires_auth(self, app_client, db_path):
        _insert_dry_run(db_path, dry_run_id="dis_api001")
        import os
        with patch.dict(os.environ, {"API_SECRET": "secret"}):
            rv = app_client.post("/api/v1/alpha/notifications/dry-run/dis_api001/dismiss",
                                 headers={},
                                 json={"reason": "Not relevant"})
            assert rv.status_code == 401

    def test_dismiss_with_auth(self, app_client, db_path):
        _insert_dry_run(db_path, dry_run_id="dis_api002")
        import os
        with patch.dict(os.environ, {"API_SECRET": "testtoken"}):
            rv = app_client.post(
                "/api/v1/alpha/notifications/dry-run/dis_api002/dismiss",
                headers={"Authorization": "Bearer testtoken"},
                json={"reason": "Not relevant"},
            )
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert data["data"]["status"] == "DISMISSED"

    def test_dismiss_not_found_returns_400(self, app_client, db_path):
        with patch.dict(os.environ, {"API_SECRET": ""}):
            rv = app_client.post("/api/v1/alpha/notifications/dry-run/ghost/dismiss",
                                 json={})
            assert rv.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# 8. No Twilio / send_sms called
# ─────────────────────────────────────────────────────────────────────────────

class TestNoAlertsSent:
    def test_generate_notification_text_does_not_import_twilio(self):
        from alpha_notification_dryrun import generate_notification_text
        # Ensure calling generate_notification_text does not trigger any Twilio import
        import sys
        before = set(sys.modules.keys())
        generate_notification_text(_make_candidate(), _make_gate_result())
        after = set(sys.modules.keys())
        new_modules = after - before
        twilio_modules = [m for m in new_modules if "twilio" in m.lower()]
        assert twilio_modules == [], f"Twilio imported: {twilio_modules}"

    def test_generate_dry_runs_does_not_call_send_sms(self, db_path, monkeypatch):
        import alpha_notification_dryrun as m
        monkeypatch.setattr(m, "_ensure_table", lambda: None)
        monkeypatch.setattr("alpha_alert_gate.get_alert_candidates",
                            lambda limit=50: [_make_gate_result("SAFE", "ALERT_READY")])

        # Patch alerts.send_sms to raise if called
        mock_send = MagicMock(side_effect=AssertionError("send_sms must not be called"))
        with patch.dict("sys.modules", {"alerts": MagicMock(send_sms=mock_send)}):
            from alpha_notification_dryrun import generate_dry_runs
            rows = generate_dry_runs()  # must not raise

    def test_mark_reviewed_does_not_send_alert(self, db_path):
        _insert_dry_run(db_path, dry_run_id="safe_rev")
        mock_send = MagicMock(side_effect=AssertionError("send_sms must not be called"))
        with patch.dict("sys.modules", {"alerts": MagicMock(send_sms=mock_send)}):
            from alpha_notification_dryrun import mark_reviewed
            mark_reviewed("safe_rev")  # must not raise

    def test_module_has_no_twilio_import(self):
        """alpha_notification_dryrun must not import twilio at module level."""
        import alpha_notification_dryrun
        module_source = open(alpha_notification_dryrun.__file__).read()
        assert "import twilio" not in module_source
        assert "from twilio" not in module_source
        assert "send_sms(" not in module_source
        assert "client.messages" not in module_source
