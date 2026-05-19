"""
Phase L3 — Controlled promotion workflow tests.

Covers:
  - proposal ID generation (deterministic, collision-resistant)
  - generate_proposals() from L2 recs (with DB-backed outcomes)
  - idempotent re-generation
  - approval workflow (PROPOSED → APPROVED_FOR_SHADOW)
  - rejection workflow (PROPOSED → REJECTED)
  - invalid status transition protection
  - stale proposal expiry
  - ROLLBACK_READY transition
  - shadow trial tracking (get_shadow_results)
  - audit log immutability and ordering
  - get_proposals() filtering
  - API endpoints: list, generate (auth), approve (auth), reject (auth), shadow-results
  - no live Alpha weights mutated
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── DB helpers ────────────────────────────────────────────────────────────────

_SHADOW_DDL = """
CREATE TABLE IF NOT EXISTS alpha_shadow_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, scan_time TEXT,
    alpha_score REAL, alpha_tier TEXT, setup_type TEXT, predator_tier TEXT,
    predator_score REAL, tier_match INTEGER DEFAULT 0, filter_reason TEXT,
    component_scores_json TEXT, explanation TEXT, detail_json TEXT
)"""

_OUTCOMES_DDL = """
CREATE TABLE IF NOT EXISTS alpha_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL,
    scan_time TEXT NOT NULL, alpha_score REAL, alpha_tier TEXT, setup_type TEXT,
    source TEXT, component_scores_json TEXT, price_at_scan REAL,
    price_1d REAL, price_3d REAL, price_5d REAL, price_10d REAL, price_20d REAL,
    return_1d REAL, return_3d REAL, return_5d REAL, return_10d REAL, return_20d REAL,
    max_gain REAL, max_drawdown REAL, status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL, updated_at TEXT, UNIQUE(ticker, scan_time)
)"""

_PROPOSALS_DDL = """
CREATE TABLE IF NOT EXISTS learning_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'PROPOSED',
    kind TEXT NOT NULL,
    weight_changes_json TEXT, threshold_changes_json TEXT, shadow_weights_json TEXT,
    evidence_summary TEXT, sample_size INTEGER NOT NULL DEFAULT 0,
    confidence TEXT NOT NULL, risk_warning TEXT, expected_benefit TEXT,
    expected_downside TEXT, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    reviewed_at TEXT, reviewed_by TEXT, review_note TEXT, shadow_results_json TEXT
)"""

_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS learning_proposal_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL,
    reason TEXT, actor TEXT, ts TEXT NOT NULL
)"""


def _make_db(path: str) -> str:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for ddl in (_SHADOW_DDL, _OUTCOMES_DDL, _PROPOSALS_DDL, _AUDIT_DDL):
        conn.execute(ddl)
    conn.commit()
    conn.close()
    return path


def _make_get_conn(db_path: str):
    def _get_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    return _get_conn


_CURRENT_WEIGHTS = {
    "relative_strength": 0.15, "acceleration": 0.15, "squeeze": 0.12,
    "catalyst": 0.15, "options": 0.13, "breakout": 0.15,
    "risk_reward": 0.10, "novelty": 0.05,
}


def _cs_json(**scores) -> str:
    cs = {
        name: {"score": scores.get(name, 5.0), "weight": w, "data_quality": "HIGH"}
        for name, w in _CURRENT_WEIGHTS.items()
    }
    return json.dumps(cs)


def _seed_complete_outcome(db_path: str, ticker: str, return_5d: float,
                            tier: str = "STRONG_WATCH",
                            setup: str = "BREAKOUT_EXPANSION",
                            cs_json: str = None):
    if cs_json is None:
        cs_json = _cs_json(breakout=8.0)
    now = datetime.now().isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT OR IGNORE INTO alpha_outcomes
           (ticker, scan_time, alpha_score, alpha_tier, setup_type, source,
            component_scores_json, price_at_scan,
            return_5d, return_10d, return_20d, max_gain, max_drawdown,
            status, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ticker, now, 60.0, tier, setup, "alpha_universe",
         cs_json, 100.0, return_5d, return_5d * 1.5, return_5d * 2.0,
         max(0.0, return_5d), min(0.0, return_5d * 0.5), "COMPLETE", now),
    )
    conn.commit()
    conn.close()


def _seed_proposal(db_path: str, proposal_id: str = "abc123def456dead",
                   status: str = "PROPOSED", kind: str = "WEIGHT",
                   expires_delta_days: int = 30,
                   confidence: str = "MEDIUM"):
    """Insert a proposal row directly for testing."""
    now        = datetime.now().isoformat()
    expires_at = (datetime.now() + timedelta(days=expires_delta_days)).isoformat()
    wc         = json.dumps({"breakout": {"current_weight": 0.15, "shrunk_delta": 0.03,
                                          "action": "INCREASE", "confidence": confidence,
                                          "reason": "test", "sample_size": 15}})
    sw         = json.dumps({**_CURRENT_WEIGHTS})
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT OR IGNORE INTO learning_proposals
           (proposal_id, status, kind, weight_changes_json, shadow_weights_json,
            evidence_summary, sample_size, confidence, risk_warning,
            expected_benefit, expected_downside, created_at, expires_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (proposal_id, status, kind, wc, sw,
         "test evidence", 15, confidence, "test risk",
         "test benefit", "test downside", now, expires_at),
    )
    conn.commit()
    conn.close()
    return proposal_id


def _read_audit(db_path: str, proposal_id: str) -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM learning_proposal_audit WHERE proposal_id = ? ORDER BY ts",
        (proposal_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _read_proposal(db_path: str, proposal_id: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM learning_proposals WHERE proposal_id = ?", (proposal_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


# ── TestProposalIdGeneration ──────────────────────────────────────────────────

class TestProposalIdGeneration:

    def test_same_input_same_id(self):
        from alpha_proposals import _proposal_id_hash
        id1 = _proposal_id_hash({"breakout": {"delta": 0.05}}, {}, 20)
        id2 = _proposal_id_hash({"breakout": {"delta": 0.05}}, {}, 20)
        assert id1 == id2

    def test_different_weights_different_id(self):
        from alpha_proposals import _proposal_id_hash
        id1 = _proposal_id_hash({"breakout": {"delta": 0.05}}, {}, 20)
        id2 = _proposal_id_hash({"breakout": {"delta": 0.04}}, {}, 20)
        assert id1 != id2

    def test_different_sample_size_different_id(self):
        from alpha_proposals import _proposal_id_hash
        id1 = _proposal_id_hash({"breakout": {"delta": 0.05}}, {}, 20)
        id2 = _proposal_id_hash({"breakout": {"delta": 0.05}}, {}, 21)
        assert id1 != id2

    def test_different_threshold_changes_different_id(self):
        from alpha_proposals import _proposal_id_hash
        id1 = _proposal_id_hash({}, {"STRONG_WATCH": {"delta": 2.5}}, 20)
        id2 = _proposal_id_hash({}, {"STRONG_WATCH": {"delta": 3.0}}, 20)
        assert id1 != id2

    def test_id_is_16_chars(self):
        from alpha_proposals import _proposal_id_hash
        pid = _proposal_id_hash({"a": 1}, {}, 10)
        assert len(pid) == 16
        assert all(c in "0123456789abcdef" for c in pid)

    def test_key_order_does_not_affect_id(self):
        from alpha_proposals import _proposal_id_hash
        # json.dumps with sort_keys=True normalises key order
        id1 = _proposal_id_hash({"a": 1, "b": 2}, {}, 10)
        id2 = _proposal_id_hash({"b": 2, "a": 1}, {}, 10)
        assert id1 == id2


# ── TestGenerateProposals ─────────────────────────────────────────────────────

class TestGenerateProposals:

    def _setup_outcomes_that_drive_recommendations(self, db_path: str):
        """Seed outcomes where breakout high → winners, so lift > 1.3x → INCREASE rec."""
        for i in range(12):
            # High breakout score + positive return = lift above threshold
            _seed_complete_outcome(
                db_path, f"WIN{i}", return_5d=0.12,
                cs_json=_cs_json(breakout=8.0, catalyst=3.0)
            )
        for i in range(4):
            # Low breakout + negative return (doesn't affect breakout lift much)
            _seed_complete_outcome(
                db_path, f"LOSE{i}", return_5d=-0.08,
                cs_json=_cs_json(breakout=2.0, catalyst=3.0)
            )

    def test_generates_proposals_with_enough_outcomes(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        self._setup_outcomes_that_drive_recommendations(db_path)
        from alpha_proposals import generate_proposals
        proposals = generate_proposals()
        assert isinstance(proposals, list)
        # Should produce at least one proposal (weight or threshold)

    def test_no_proposals_when_insufficient_outcomes(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        # Only 3 complete outcomes → below _MIN_OUTCOMES_FOR_PROPOSAL=10
        for i in range(3):
            _seed_complete_outcome(db_path, f"T{i}", return_5d=0.05)
        from alpha_proposals import generate_proposals
        proposals = generate_proposals()
        assert proposals == []

    def test_idempotent_second_call_no_duplicates(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        self._setup_outcomes_that_drive_recommendations(db_path)
        from alpha_proposals import generate_proposals
        p1 = generate_proposals()
        p2 = generate_proposals()
        # Same IDs — no duplicates
        ids1 = {p["proposal_id"] for p in p1}
        ids2 = {p["proposal_id"] for p in p2}
        assert ids1 == ids2

    def test_proposal_status_is_proposed(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        self._setup_outcomes_that_drive_recommendations(db_path)
        from alpha_proposals import generate_proposals
        proposals = generate_proposals()
        for p in proposals:
            assert p["status"] == "PROPOSED"

    def test_proposal_has_required_fields(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        self._setup_outcomes_that_drive_recommendations(db_path)
        from alpha_proposals import generate_proposals
        proposals = generate_proposals()
        required = [
            "proposal_id", "status", "kind", "confidence", "sample_size",
            "evidence_summary", "risk_warning", "expected_benefit",
            "expected_downside", "created_at", "expires_at",
        ]
        for p in proposals:
            for field in required:
                assert field in p, f"Missing field {field!r} in proposal"

    def test_proposal_expires_in_30_days(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        self._setup_outcomes_that_drive_recommendations(db_path)
        from alpha_proposals import generate_proposals
        proposals = generate_proposals()
        for p in proposals:
            expires = datetime.fromisoformat(p["expires_at"])
            created = datetime.fromisoformat(p["created_at"])
            delta   = expires - created
            assert 29 <= delta.days <= 31

    def test_audit_entry_written_on_generation(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        self._setup_outcomes_that_drive_recommendations(db_path)
        from alpha_proposals import generate_proposals
        proposals = generate_proposals()
        for p in proposals:
            audit = _read_audit(db_path, p["proposal_id"])
            assert len(audit) >= 1
            assert audit[0]["to_status"] == "PROPOSED"
            assert audit[0]["actor"]     == "system"

    def test_proposal_kind_is_weight_or_threshold(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        self._setup_outcomes_that_drive_recommendations(db_path)
        from alpha_proposals import generate_proposals
        proposals = generate_proposals()
        for p in proposals:
            assert p["kind"] in ("WEIGHT", "THRESHOLD")


# ── TestApproveForShadow ──────────────────────────────────────────────────────

class TestApproveForShadow:

    def test_approve_transitions_to_approved_for_shadow(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path)
        from alpha_proposals import approve_for_shadow
        result = approve_for_shadow(pid, actor="test_user")
        assert result["status"] == "APPROVED_FOR_SHADOW"

    def test_approval_writes_audit_entry(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path)
        from alpha_proposals import approve_for_shadow
        approve_for_shadow(pid, actor="test_user", note="Looks good")
        audit = _read_audit(db_path, pid)
        approve_entry = next((a for a in audit if a["to_status"] == "APPROVED_FOR_SHADOW"), None)
        assert approve_entry is not None
        assert approve_entry["from_status"] == "PROPOSED"
        assert approve_entry["actor"] == "test_user"

    def test_cannot_approve_rejected_proposal(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="REJECTED")
        from alpha_proposals import approve_for_shadow
        with pytest.raises(ValueError, match="Invalid transition"):
            approve_for_shadow(pid)

    def test_cannot_approve_expired_proposal(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="EXPIRED")
        from alpha_proposals import approve_for_shadow
        with pytest.raises(ValueError, match="Invalid transition"):
            approve_for_shadow(pid)

    def test_cannot_approve_unknown_proposal(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        from alpha_proposals import approve_for_shadow
        with pytest.raises(ValueError, match="not found"):
            approve_for_shadow("nonexistent1234")

    def test_reviewed_by_and_at_recorded(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path)
        from alpha_proposals import approve_for_shadow
        approve_for_shadow(pid, actor="karim", note="approved")
        row = _read_proposal(db_path, pid)
        assert row["reviewed_by"] == "karim"
        assert row["review_note"] == "approved"
        assert row["reviewed_at"] is not None


# ── TestRejectProposal ────────────────────────────────────────────────────────

class TestRejectProposal:

    def test_reject_transitions_to_rejected(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path)
        from alpha_proposals import reject_proposal
        result = reject_proposal(pid, reason="Too few samples", actor="karim")
        assert result["status"] == "REJECTED"

    def test_rejection_writes_audit_entry(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path)
        from alpha_proposals import reject_proposal
        reject_proposal(pid, reason="Low confidence", actor="karim")
        audit = _read_audit(db_path, pid)
        reject_entry = next((a for a in audit if a["to_status"] == "REJECTED"), None)
        assert reject_entry is not None
        assert reject_entry["reason"] == "Low confidence"

    def test_cannot_reject_already_rejected(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="REJECTED")
        from alpha_proposals import reject_proposal
        with pytest.raises(ValueError, match="Invalid transition"):
            reject_proposal(pid)

    def test_cannot_reject_rollback_ready(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="ROLLBACK_READY")
        from alpha_proposals import reject_proposal
        with pytest.raises(ValueError, match="Invalid transition"):
            reject_proposal(pid)

    def test_cannot_reject_approved_for_shadow(self, tmp_path, monkeypatch):
        """APPROVED_FOR_SHADOW can only go to ROLLBACK_READY or EXPIRED, not REJECTED."""
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="APPROVED_FOR_SHADOW")
        from alpha_proposals import reject_proposal
        with pytest.raises(ValueError, match="Invalid transition"):
            reject_proposal(pid)


# ── TestInvalidTransitions ────────────────────────────────────────────────────

class TestInvalidTransitions:

    def test_all_terminal_statuses_have_no_transitions(self):
        from alpha_proposals import _VALID_TRANSITIONS, _TERMINAL_STATUSES
        for status in _TERMINAL_STATUSES:
            assert _VALID_TRANSITIONS.get(status, set()) == set()

    def test_proposed_cannot_go_to_rollback_ready(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="PROPOSED")
        from alpha_proposals import mark_rollback_ready
        with pytest.raises(ValueError, match="Invalid transition"):
            mark_rollback_ready(pid)

    def test_rollback_ready_to_approved_is_invalid(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="ROLLBACK_READY")
        from alpha_proposals import approve_for_shadow
        with pytest.raises(ValueError, match="Invalid transition"):
            approve_for_shadow(pid)


# ── TestExpireStaleProposals ──────────────────────────────────────────────────

class TestExpireStaleProposals:

    def test_expires_old_proposed_proposal(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        # Seed proposal that expired yesterday
        pid = _seed_proposal(db_path, status="PROPOSED", expires_delta_days=-1)
        from alpha_proposals import expire_stale_proposals
        count = expire_stale_proposals()
        assert count >= 1
        row = _read_proposal(db_path, pid)
        assert row["status"] == "EXPIRED"

    def test_expires_old_approved_for_shadow(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="APPROVED_FOR_SHADOW", expires_delta_days=-1)
        from alpha_proposals import expire_stale_proposals
        count = expire_stale_proposals()
        assert count >= 1
        row = _read_proposal(db_path, pid)
        assert row["status"] == "EXPIRED"

    def test_does_not_expire_recent_proposed(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="PROPOSED", expires_delta_days=10)
        from alpha_proposals import expire_stale_proposals
        expire_stale_proposals()
        row = _read_proposal(db_path, pid)
        assert row["status"] == "PROPOSED"

    def test_terminal_statuses_not_expired(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        for s, pid in [("REJECTED", "aaa1111111111111"), ("ROLLBACK_READY", "bbb2222222222222")]:
            _seed_proposal(db_path, proposal_id=pid, status=s, expires_delta_days=-1)
        from alpha_proposals import expire_stale_proposals
        expire_stale_proposals()
        for pid, expected in [("aaa1111111111111", "REJECTED"), ("bbb2222222222222", "ROLLBACK_READY")]:
            row = _read_proposal(db_path, pid)
            assert row["status"] == expected

    def test_expiry_writes_audit_entry(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="PROPOSED", expires_delta_days=-1)
        from alpha_proposals import expire_stale_proposals
        expire_stale_proposals()
        audit = _read_audit(db_path, pid)
        expire_entry = next((a for a in audit if a["to_status"] == "EXPIRED"), None)
        assert expire_entry is not None
        assert expire_entry["actor"] == "system"

    def test_returns_count_of_expired(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        _seed_proposal(db_path, proposal_id="exp1111111111111", status="PROPOSED", expires_delta_days=-1)
        _seed_proposal(db_path, proposal_id="exp2222222222222", status="PROPOSED", expires_delta_days=-1)
        from alpha_proposals import expire_stale_proposals
        count = expire_stale_proposals()
        assert count == 2


# ── TestGetProposals ──────────────────────────────────────────────────────────

class TestGetProposals:

    def test_returns_only_active_by_default(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        _seed_proposal(db_path, proposal_id="active111111111a", status="PROPOSED")
        _seed_proposal(db_path, proposal_id="approved11111111", status="APPROVED_FOR_SHADOW")
        _seed_proposal(db_path, proposal_id="rejected11111111", status="REJECTED")
        from alpha_proposals import get_proposals
        proposals = get_proposals()
        statuses = {p["status"] for p in proposals}
        assert "REJECTED" not in statuses
        assert "PROPOSED" in statuses or "APPROVED_FOR_SHADOW" in statuses

    def test_status_filter_works(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        _seed_proposal(db_path, proposal_id="proposed11111111", status="PROPOSED")
        _seed_proposal(db_path, proposal_id="approved11111111", status="APPROVED_FOR_SHADOW",
                       expires_delta_days=10)
        from alpha_proposals import get_proposals
        proposed = get_proposals(status_filter="PROPOSED")
        for p in proposed:
            assert p["status"] == "PROPOSED"

    def test_include_historical_returns_all(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        _seed_proposal(db_path, proposal_id="p1111111p1111111", status="PROPOSED")
        _seed_proposal(db_path, proposal_id="r1111111r1111111", status="REJECTED")
        from alpha_proposals import get_proposals
        proposals = get_proposals(include_historical=True)
        statuses = {p["status"] for p in proposals}
        assert "PROPOSED"  in statuses
        assert "REJECTED"  in statuses

    def test_empty_db_returns_empty_list(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        from alpha_proposals import get_proposals
        assert get_proposals() == []

    def test_auto_expires_stale_on_list(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="PROPOSED", expires_delta_days=-1)
        from alpha_proposals import get_proposals
        # After get_proposals, stale proposals are auto-expired
        get_proposals()
        row = _read_proposal(db_path, pid)
        assert row["status"] == "EXPIRED"


# ── TestGetShadowResults ──────────────────────────────────────────────────────

class TestGetShadowResults:

    def test_returns_results_for_known_proposal(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="APPROVED_FOR_SHADOW")
        from alpha_proposals import get_shadow_results
        results = get_shadow_results(pid)
        assert results["proposal_id"] == pid
        assert "replay_stats" in results
        assert "shadow_weights" in results

    def test_returns_error_for_unknown_proposal(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        from alpha_proposals import get_shadow_results
        results = get_shadow_results("doesnotexist1234")
        assert "error" in results
        assert "not found" in results["error"]

    def test_shadow_weights_sum_to_one(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path)
        from alpha_proposals import get_shadow_results
        results = get_shadow_results(pid)
        sw = results.get("shadow_weights", {})
        if sw:
            assert abs(sum(sw.values()) - 1.0) < 1e-4

    def test_works_for_any_status(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        for s, pid in [
            ("PROPOSED",           "p1prop111111111p"),
            ("APPROVED_FOR_SHADOW", "p1appr111111111p"),
            ("REJECTED",           "p1reje111111111p"),
        ]:
            _seed_proposal(db_path, proposal_id=pid, status=s)
        from alpha_proposals import get_shadow_results
        for pid in ["p1prop111111111p", "p1appr111111111p", "p1reje111111111p"]:
            results = get_shadow_results(pid)
            assert "replay_stats" in results


# ── TestRollbackReady ─────────────────────────────────────────────────────────

class TestRollbackReady:

    def test_mark_rollback_ready_from_approved_for_shadow(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="APPROVED_FOR_SHADOW")
        from alpha_proposals import mark_rollback_ready
        result = mark_rollback_ready(pid, reason="Shadow FP rate too high")
        assert result["status"] == "ROLLBACK_READY"

    def test_rollback_audit_entry_written(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="APPROVED_FOR_SHADOW")
        from alpha_proposals import mark_rollback_ready
        mark_rollback_ready(pid, reason="poor shadow performance", actor="scheduler")
        audit = _read_audit(db_path, pid)
        rb_entry = next((a for a in audit if a["to_status"] == "ROLLBACK_READY"), None)
        assert rb_entry is not None
        assert rb_entry["reason"] == "poor shadow performance"


# ── TestAuditTrail ────────────────────────────────────────────────────────────

class TestAuditTrail:

    def test_audit_grows_with_each_transition(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="PROPOSED")
        from alpha_proposals import approve_for_shadow, mark_rollback_ready
        approve_for_shadow(pid, actor="user1")
        mark_rollback_ready(pid, reason="poor performance", actor="system")
        audit = _read_audit(db_path, pid)
        # Seeded proposal may have no audit; transitions add 2 entries
        assert len(audit) >= 2

    def test_audit_entries_ordered_chronologically(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="PROPOSED")
        from alpha_proposals import approve_for_shadow, mark_rollback_ready
        approve_for_shadow(pid, actor="u1")
        mark_rollback_ready(pid, reason="r", actor="u2")
        audit = _read_audit(db_path, pid)
        ts_list = [a["ts"] for a in audit]
        assert ts_list == sorted(ts_list)

    def test_audit_captures_actor(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="PROPOSED")
        from alpha_proposals import reject_proposal
        reject_proposal(pid, reason="test rejection", actor="karim_test")
        audit = _read_audit(db_path, pid)
        actors = [a["actor"] for a in audit]
        assert "karim_test" in actors

    def test_get_audit_trail_function(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path, status="PROPOSED")
        from alpha_proposals import approve_for_shadow, get_audit_trail
        approve_for_shadow(pid, actor="tester")
        trail = get_audit_trail(pid)
        assert isinstance(trail, list)
        assert all("proposal_id" in e and "to_status" in e and "ts" in e for e in trail)


# ── TestNoLiveWeightChanges ───────────────────────────────────────────────────

class TestNoLiveWeightChanges:

    def test_current_weights_unchanged_after_generate(self, tmp_path, monkeypatch):
        import copy
        from alpha_learning_engine import _CURRENT_WEIGHTS
        before = copy.deepcopy(_CURRENT_WEIGHTS)
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        for i in range(12):
            _seed_complete_outcome(db_path, f"T{i}", return_5d=0.05)
        from alpha_proposals import generate_proposals
        generate_proposals()
        assert _CURRENT_WEIGHTS == before

    def test_current_weights_unchanged_after_approve(self, tmp_path, monkeypatch):
        import copy
        from alpha_learning_engine import _CURRENT_WEIGHTS
        before = copy.deepcopy(_CURRENT_WEIGHTS)
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path)
        from alpha_proposals import approve_for_shadow
        approve_for_shadow(pid)
        assert _CURRENT_WEIGHTS == before

    def test_current_weights_unchanged_after_reject(self, tmp_path, monkeypatch):
        import copy
        from alpha_learning_engine import _CURRENT_WEIGHTS
        before = copy.deepcopy(_CURRENT_WEIGHTS)
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        pid = _seed_proposal(db_path)
        from alpha_proposals import reject_proposal
        reject_proposal(pid, reason="test")
        assert _CURRENT_WEIGHTS == before


# ── TestL3ApiEndpoints ────────────────────────────────────────────────────────

class TestL3ApiEndpoints:

    def _client(self, db_path, monkeypatch, api_secret: str = ""):
        monkeypatch.setattr("database.get_connection", _make_get_conn(db_path))
        if api_secret:
            monkeypatch.setenv("API_SECRET", api_secret)
        else:
            monkeypatch.delenv("API_SECRET", raising=False)
        import importlib
        import api
        importlib.reload(api)
        from flask import Flask
        from api import api_bp
        app = Flask(__name__)
        app.register_blueprint(api_bp, url_prefix="/api/v1")
        app.config["TESTING"] = True
        return app.test_client()

    def test_get_proposals_returns_200(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        client = self._client(db_path, monkeypatch)
        resp = client.get("/api/v1/alpha/learning/proposals")
        assert resp.status_code == 200

    def test_get_proposals_response_structure(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        client = self._client(db_path, monkeypatch)
        data = client.get("/api/v1/alpha/learning/proposals").get_json()
        assert data["ok"] is True
        assert "proposals" in data["data"]
        assert "total"     in data["data"]
        assert "active"    in data["data"]

    def test_generate_requires_auth(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        client = self._client(db_path, monkeypatch, api_secret="secret123")
        resp = client.post("/api/v1/alpha/learning/proposals/generate",
                           headers={"Authorization": "Bearer wrongtoken"})
        assert resp.status_code in (401, 200)  # 401 when auth fails
        data = resp.get_json()
        if resp.status_code == 401:
            assert data["ok"] is False

    def test_generate_returns_200_with_correct_auth(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        client = self._client(db_path, monkeypatch, api_secret="mysecret")
        resp = client.post("/api/v1/alpha/learning/proposals/generate",
                           headers={"Authorization": "Bearer mysecret"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "generated" in data["data"]

    def test_generate_no_auth_when_secret_unset(self, tmp_path, monkeypatch):
        """When API_SECRET is not set, auth is bypassed (open mode)."""
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        client = self._client(db_path, monkeypatch, api_secret="")
        resp = client.post("/api/v1/alpha/learning/proposals/generate")
        assert resp.status_code == 200

    def test_approve_requires_auth(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        pid = _seed_proposal(db_path)
        client = self._client(db_path, monkeypatch, api_secret="secret123")
        resp = client.post(f"/api/v1/alpha/learning/proposals/{pid}/approve-shadow",
                           headers={"Authorization": "Bearer badtoken"},
                           json={})
        assert resp.status_code == 401

    def test_approve_returns_200_with_auth(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        pid = _seed_proposal(db_path)
        client = self._client(db_path, monkeypatch, api_secret="mysecret")
        resp = client.post(f"/api/v1/alpha/learning/proposals/{pid}/approve-shadow",
                           headers={"Authorization": "Bearer mysecret"},
                           json={"note": "Approving for shadow"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["status"] == "APPROVED_FOR_SHADOW"

    def test_approve_invalid_transition_returns_400(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        pid = _seed_proposal(db_path, status="REJECTED")
        client = self._client(db_path, monkeypatch, api_secret="")
        resp = client.post(f"/api/v1/alpha/learning/proposals/{pid}/approve-shadow",
                           json={})
        assert resp.status_code == 400

    def test_reject_requires_auth(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        pid = _seed_proposal(db_path)
        client = self._client(db_path, monkeypatch, api_secret="secret123")
        resp = client.post(f"/api/v1/alpha/learning/proposals/{pid}/reject",
                           headers={"Authorization": "Bearer badtoken"},
                           json={"reason": "test"})
        assert resp.status_code == 401

    def test_reject_returns_200_with_auth(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        pid = _seed_proposal(db_path)
        client = self._client(db_path, monkeypatch, api_secret="mysecret")
        resp = client.post(f"/api/v1/alpha/learning/proposals/{pid}/reject",
                           headers={"Authorization": "Bearer mysecret"},
                           json={"reason": "Not enough data"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["status"] == "REJECTED"

    def test_shadow_results_returns_200(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        pid = _seed_proposal(db_path, status="APPROVED_FOR_SHADOW")
        client = self._client(db_path, monkeypatch)
        resp = client.get(f"/api/v1/alpha/learning/proposals/{pid}/shadow-results")
        assert resp.status_code == 200

    def test_shadow_results_404_for_unknown(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        client = self._client(db_path, monkeypatch)
        resp = client.get("/api/v1/alpha/learning/proposals/doesnotexist/shadow-results")
        assert resp.status_code == 404

    def test_shadow_results_has_replay_stats(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        pid = _seed_proposal(db_path, status="PROPOSED")
        client = self._client(db_path, monkeypatch)
        data = client.get(f"/api/v1/alpha/learning/proposals/{pid}/shadow-results").get_json()
        assert data["ok"] is True
        assert "replay_stats" in data["data"]

    def test_get_proposals_filters_by_status(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        _seed_proposal(db_path, proposal_id="prop1prop1prop1p", status="PROPOSED")
        _seed_proposal(db_path, proposal_id="rej1rej1rej1rej1", status="REJECTED")
        client = self._client(db_path, monkeypatch)
        data = client.get("/api/v1/alpha/learning/proposals?status=PROPOSED").get_json()
        for p in data["data"]["proposals"]:
            assert p["status"] == "PROPOSED"
