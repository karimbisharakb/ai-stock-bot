"""
Migration v2 tests — predator schema expansion.

Verifies that migration v2 is:
  - Additive: no existing columns removed, existing data preserved
  - Correct: all expected columns, table, and indexes exist after migration
  - Idempotent: running run_migrations() twice does not error or corrupt data
  - Compatible: inserts that omit new columns (NULL defaults) still succeed
  - Seeded: predator_signal_weights contains exactly 6 rows with correct values

All tests operate on a throw-away SQLite file in a pytest tmp_path directory.
The real portfolio.db is never touched.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
import pytest
from unittest.mock import patch

import database
from database import init_db, run_migrations, MIGRATIONS


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "test_predator.db")


@pytest.fixture()
def fresh_db(db_path):
    """Fully initialised and migrated database in a temp directory."""
    with patch("database.DB_PATH", db_path):
        init_db()
        run_migrations()
    yield db_path


@pytest.fixture()
def pre_v2_db(db_path):
    """Database at schema v1 — init + migration v1 only, v2 not yet applied."""
    v1_only = [m for m in MIGRATIONS if m.version <= 1]
    with patch("database.DB_PATH", db_path), \
         patch("database.MIGRATIONS", v1_only):
        init_db()
        run_migrations()
    yield db_path


# ─────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────

def _columns(db_path: str, table: str) -> list:
    conn = sqlite3.connect(db_path)
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    conn.close()
    return cols


def _indexes(db_path: str, table: str) -> list:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(f"PRAGMA index_list({table})").fetchall()
    conn.close()
    return [row[1] for row in rows]  # index name is column 1


def _schema_version(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    conn.close()
    return row[0] if row else 0


# ─────────────────────────────────────────────
# Sanity: migration list
# ─────────────────────────────────────────────

class TestMigrationList:
    def test_v2_exists_in_migrations(self):
        versions = [m.version for m in MIGRATIONS]
        assert 2 in versions

    def test_migrations_are_ordered(self):
        versions = [m.version for m in MIGRATIONS]
        assert versions == sorted(versions)

    def test_v2_has_non_empty_sql(self):
        v2 = next(m for m in MIGRATIONS if m.version == 2)
        assert len(v2.sql) > 0

    def test_v2_description_is_non_empty(self):
        v2 = next(m for m in MIGRATIONS if m.version == 2)
        assert v2.description.strip()


# ─────────────────────────────────────────────
# Schema version
# ─────────────────────────────────────────────

class TestSchemaVersion:
    def test_schema_version_is_latest_after_full_migration(self, fresh_db):
        assert _schema_version(fresh_db) == max(m.version for m in MIGRATIONS)

    def test_schema_version_is_1_after_v1_only(self, pre_v2_db):
        assert _schema_version(pre_v2_db) == 1


# ─────────────────────────────────────────────
# predator_alerts columns
# ─────────────────────────────────────────────

_PREDATOR_ALERTS_NEW_COLS = [
    "confidence_pct",
    "adjusted_score",
    "raw_score",
    "tier",
    "score_options",
    "score_insider",
    "score_short_squeeze",
    "score_catalyst",
    "score_institutional",
    "score_breakout",
]

_PREDATOR_ALERTS_ORIGINAL_COLS = [
    "id", "ticker", "score", "signals_json", "entry_price",
    "stop_price", "position_size_cad", "alert_time",
    "price_7d_later", "price_14d_later", "price_30d_later", "outcome",
]


class TestPredatorAlertsColumns:
    def test_all_new_columns_added(self, fresh_db):
        cols = _columns(fresh_db, "predator_alerts")
        for col in _PREDATOR_ALERTS_NEW_COLS:
            assert col in cols, f"Missing column: {col}"

    def test_original_columns_preserved(self, fresh_db):
        cols = _columns(fresh_db, "predator_alerts")
        for col in _PREDATOR_ALERTS_ORIGINAL_COLS:
            assert col in cols, f"Original column missing after migration: {col}"

    def test_new_columns_absent_before_v2(self, pre_v2_db):
        cols = _columns(pre_v2_db, "predator_alerts")
        for col in _PREDATOR_ALERTS_NEW_COLS:
            assert col not in cols, f"Column {col} should not exist before v2"

    def test_new_columns_are_nullable(self, fresh_db):
        # SQLite PRAGMA table_info: col 3 is "notnull" (1 = NOT NULL, 0 = nullable)
        conn = sqlite3.connect(fresh_db)
        info = {
            row[1]: row[3]
            for row in conn.execute("PRAGMA table_info(predator_alerts)").fetchall()
        }
        conn.close()
        for col in _PREDATOR_ALERTS_NEW_COLS:
            assert info.get(col) == 0, f"{col} should be nullable"


# ─────────────────────────────────────────────
# predator_latest columns
# ─────────────────────────────────────────────

_PREDATOR_LATEST_ORIGINAL_COLS = [
    "ticker", "score", "signals_json", "entry_price", "stop_price", "scan_time",
]

_PREDATOR_LATEST_NEW_COLS = [
    "confidence_pct",
    "adjusted_score",
    "raw_score",
    "tier",
    "score_options",
    "score_insider",
    "score_short_squeeze",
    "score_catalyst",
    "score_institutional",
    "score_breakout",
]


class TestPredatorLatestColumns:
    def test_all_new_columns_added(self, fresh_db):
        cols = _columns(fresh_db, "predator_latest")
        for col in _PREDATOR_LATEST_NEW_COLS:
            assert col in cols, f"Missing column: {col}"

    def test_original_columns_preserved(self, fresh_db):
        cols = _columns(fresh_db, "predator_latest")
        for col in _PREDATOR_LATEST_ORIGINAL_COLS:
            assert col in cols, f"Original column missing: {col}"

    def test_new_columns_are_nullable(self, fresh_db):
        conn = sqlite3.connect(fresh_db)
        info = {
            row[1]: row[3]
            for row in conn.execute("PRAGMA table_info(predator_latest)").fetchall()
        }
        conn.close()
        for col in _PREDATOR_LATEST_NEW_COLS:
            assert info.get(col) == 0, f"{col} should be nullable"


# ─────────────────────────────────────────────
# predator_signal_weights table
# ─────────────────────────────────────────────

_EXPECTED_SIGNAL_WEIGHTS = {
    "options":       3,
    "insider":       2,
    "short_squeeze": 2,
    "catalyst":      2,
    "institutional": 1,
    "breakout":      2,
}


class TestSignalWeightsTable:
    def test_table_exists(self, fresh_db):
        conn = sqlite3.connect(fresh_db)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='predator_signal_weights'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_table_absent_before_v2(self, pre_v2_db):
        conn = sqlite3.connect(pre_v2_db)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='predator_signal_weights'"
        ).fetchone()
        conn.close()
        assert row is None

    def test_has_required_columns(self, fresh_db):
        cols = _columns(fresh_db, "predator_signal_weights")
        for col in ("signal_name", "max_score", "weight", "last_updated"):
            assert col in cols

    def test_exactly_six_seed_rows(self, fresh_db):
        conn = sqlite3.connect(fresh_db)
        count = conn.execute("SELECT COUNT(*) FROM predator_signal_weights").fetchone()[0]
        conn.close()
        assert count == 6

    def test_all_expected_signals_present(self, fresh_db):
        conn = sqlite3.connect(fresh_db)
        rows = conn.execute("SELECT signal_name FROM predator_signal_weights").fetchall()
        conn.close()
        names = {row[0] for row in rows}
        assert names == set(_EXPECTED_SIGNAL_WEIGHTS.keys())

    def test_max_scores_match_predator_config(self, fresh_db):
        conn = sqlite3.connect(fresh_db)
        rows = conn.execute(
            "SELECT signal_name, max_score FROM predator_signal_weights"
        ).fetchall()
        conn.close()
        for name, max_score in rows:
            expected = _EXPECTED_SIGNAL_WEIGHTS[name]
            assert max_score == expected, \
                f"{name}: expected max_score={expected}, got {max_score}"

    def test_default_weight_is_1(self, fresh_db):
        conn = sqlite3.connect(fresh_db)
        rows = conn.execute("SELECT signal_name, weight FROM predator_signal_weights").fetchall()
        conn.close()
        for name, weight in rows:
            assert weight == 1.0, f"{name}: expected weight=1.0, got {weight}"

    def test_last_updated_is_not_null(self, fresh_db):
        conn = sqlite3.connect(fresh_db)
        rows = conn.execute(
            "SELECT signal_name, last_updated FROM predator_signal_weights"
        ).fetchall()
        conn.close()
        for name, ts in rows:
            assert ts is not None and ts.strip(), \
                f"{name}: last_updated should not be null or empty"

    def test_signal_name_is_primary_key_unique(self, fresh_db):
        conn = sqlite3.connect(fresh_db)
        try:
            conn.execute(
                "INSERT INTO predator_signal_weights (signal_name, max_score, weight, last_updated)"
                " VALUES ('options', 3, 1.0, CURRENT_TIMESTAMP)"
            )
            conn.commit()
            assert False, "Should have raised on duplicate primary key"
        except sqlite3.IntegrityError:
            pass
        finally:
            conn.close()


# ─────────────────────────────────────────────
# Indexes
# ─────────────────────────────────────────────

_EXPECTED_ALERTS_INDEXES = [
    "idx_predator_alerts_ticker",
    "idx_predator_alerts_alert_time",
    "idx_predator_alerts_tier",
    "idx_predator_alerts_score",
]

_EXPECTED_LATEST_INDEXES = [
    "idx_predator_latest_score",
    "idx_predator_latest_scan_time",
]


class TestIndexes:
    def test_predator_alerts_indexes_created(self, fresh_db):
        idx = _indexes(fresh_db, "predator_alerts")
        for name in _EXPECTED_ALERTS_INDEXES:
            assert name in idx, f"Missing index: {name}"

    def test_predator_latest_indexes_created(self, fresh_db):
        idx = _indexes(fresh_db, "predator_latest")
        for name in _EXPECTED_LATEST_INDEXES:
            assert name in idx, f"Missing index: {name}"

    def test_indexes_absent_before_v2(self, pre_v2_db):
        alerts_idx = _indexes(pre_v2_db, "predator_alerts")
        for name in _EXPECTED_ALERTS_INDEXES:
            assert name not in alerts_idx

    def test_all_six_indexes_exist(self, fresh_db):
        all_idx = (
            _indexes(fresh_db, "predator_alerts") +
            _indexes(fresh_db, "predator_latest")
        )
        expected = _EXPECTED_ALERTS_INDEXES + _EXPECTED_LATEST_INDEXES
        for name in expected:
            assert name in all_idx


# ─────────────────────────────────────────────
# Idempotency
# ─────────────────────────────────────────────

class TestIdempotency:
    def test_run_migrations_twice_does_not_error(self, db_path):
        with patch("database.DB_PATH", db_path):
            init_db()
            run_migrations()
            run_migrations()  # second call — must be a no-op
        assert _schema_version(db_path) == max(m.version for m in MIGRATIONS)

    def test_insert_or_ignore_on_signal_weights_is_idempotent(self, fresh_db):
        # Manually re-running the INSERT OR IGNORE should not change row count
        conn = sqlite3.connect(fresh_db)
        conn.execute(
            "INSERT OR IGNORE INTO predator_signal_weights (signal_name, max_score, weight, last_updated)"
            " VALUES ('options', 3, 1.0, CURRENT_TIMESTAMP)"
        )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM predator_signal_weights").fetchone()[0]
        conn.close()
        assert count == 6  # unchanged


# ─────────────────────────────────────────────
# Additive safety / existing behaviour preserved
# ─────────────────────────────────────────────

class TestExistingBehaviourPreserved:
    def test_pre_migration_rows_in_predator_alerts_survive(self, db_path):
        # Insert a row using only the original columns, then apply v2, verify row intact
        with patch("database.DB_PATH", db_path):
            init_db()

        # Insert before any migrations
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO predator_alerts"
            " (ticker, score, signals_json, entry_price, stop_price, position_size_cad, alert_time)"
            " VALUES ('NVDA', 8.0, '{}', 500.0, 455.0, 1000.0, '2026-01-01T09:00:00')"
        )
        conn.commit()
        conn.close()

        with patch("database.DB_PATH", db_path):
            run_migrations()

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT ticker, score, confidence_pct, tier FROM predator_alerts WHERE ticker='NVDA'"
        ).fetchone()
        conn.close()

        assert row is not None, "Pre-migration row should still exist"
        assert row[0] == "NVDA"
        assert row[1] == 8.0
        assert row[2] is None, "confidence_pct should be NULL for pre-migration row"
        assert row[3] is None, "tier should be NULL for pre-migration row"

    def test_pre_migration_rows_in_predator_latest_survive(self, db_path):
        # Apply v1, insert into predator_latest, then apply v2
        v1_only = [m for m in MIGRATIONS if m.version <= 1]
        with patch("database.DB_PATH", db_path), \
             patch("database.MIGRATIONS", v1_only):
            init_db()
            run_migrations()

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO predator_latest"
            " (ticker, score, signals_json, entry_price, stop_price, scan_time)"
            " VALUES ('AAPL', 7.0, '{}', 200.0, 182.0, '2026-01-01T09:00:00')"
        )
        conn.commit()
        conn.close()

        with patch("database.DB_PATH", db_path):
            run_migrations()

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT ticker, score, confidence_pct, tier FROM predator_latest WHERE ticker='AAPL'"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "AAPL"
        assert row[1] == 7.0
        assert row[2] is None
        assert row[3] is None

    def test_insert_without_new_columns_still_works(self, fresh_db):
        # After migration, old-style INSERT (no new cols) must succeed with NULL defaults
        conn = sqlite3.connect(fresh_db)
        conn.execute(
            "INSERT INTO predator_alerts"
            " (ticker, score, signals_json, entry_price, stop_price, position_size_cad, alert_time)"
            " VALUES ('AMD', 6.0, '{}', 120.0, 109.2, 500.0, '2026-01-02T10:00:00')"
        )
        conn.commit()

        row = conn.execute(
            "SELECT confidence_pct, adjusted_score, raw_score, tier"
            " FROM predator_alerts WHERE ticker='AMD'"
        ).fetchone()
        conn.close()

        assert row is not None
        for val in row:
            assert val is None, f"Expected NULL for omitted column, got {val}"

    def test_insert_with_new_columns_also_works(self, fresh_db):
        # Verify the full new-style INSERT (all new cols provided) succeeds
        conn = sqlite3.connect(fresh_db)
        conn.execute(
            "INSERT INTO predator_alerts"
            " (ticker, score, signals_json, alert_time,"
            "  confidence_pct, adjusted_score, raw_score, tier,"
            "  score_options, score_insider, score_short_squeeze,"
            "  score_catalyst, score_institutional, score_breakout)"
            " VALUES ('PLTR', 7.0, '{}', '2026-01-03T11:00:00',"
            "         72.5, 5.08, 7.0, 'CONVICTION',"
            "         3.0, 2.0, 0.0, 2.0, 0.0, 0.0)"
        )
        conn.commit()

        row = conn.execute(
            "SELECT confidence_pct, adjusted_score, tier, score_options"
            " FROM predator_alerts WHERE ticker='PLTR'"
        ).fetchone()
        conn.close()

        assert row[0] == 72.5
        assert row[1] == 5.08
        assert row[2] == "CONVICTION"
        assert row[3] == 3.0

    def test_all_other_tables_unaffected(self, fresh_db):
        # Tables outside predator scope must still exist and have their original columns
        conn = sqlite3.connect(fresh_db)
        for table in ("holdings", "transactions", "cash", "tfsa_info",
                      "alert_log", "portfolio_history"):
            row = conn.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"
            ).fetchone()
            assert row is not None, f"Table {table} missing after v2 migration"
        conn.close()
