import glob
import logging
import os
import shutil
import sqlite3
from datetime import datetime
from typing import NamedTuple

log = logging.getLogger(__name__)

# Railway injects RAILWAY_VOLUME_MOUNT_PATH when a persistent volume is attached.
# Fall back to DB_PATH env var, then to a local file beside this module.
_volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
if _volume:
    DB_PATH = os.path.join(_volume, "portfolio.db")
else:
    DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "portfolio.db"))

print(f"[database] DB_PATH = {DB_PATH}")


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()

    # WAL mode must be set before any DDL and outside a transaction.
    # It persists in the DB file header, so subsequent init_db() calls
    # are a fast no-op. We verify the return value so a silent failure
    # (e.g. read-only volume) surfaces immediately in Railway logs.
    row = c.execute("PRAGMA journal_mode=WAL").fetchone()
    mode = row[0] if row else "unknown"
    if mode == "wal":
        print("[database] WAL mode confirmed")
    else:
        print(f"[database] WARNING: WAL mode not active — journal_mode={mode!r}")

    c.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT    NOT NULL UNIQUE,
            shares      REAL    NOT NULL,
            avg_cost    REAL    NOT NULL,
            date_added  TEXT    NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker    TEXT    NOT NULL,
            type      TEXT    NOT NULL,
            shares    REAL    NOT NULL,
            price_cad REAL    NOT NULL,
            total_cad REAL    NOT NULL,
            date      TEXT    NOT NULL,
            notes     TEXT
        )
    """)

    # Migrate old schema (action/price/gain_loss → type/price_cad/total_cad/notes)
    cols = [row[1] for row in c.execute("PRAGMA table_info(transactions)").fetchall()]
    if "action" in cols:
        c.execute("ALTER TABLE transactions RENAME TO _transactions_old")
        c.execute("""
            CREATE TABLE transactions (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker    TEXT    NOT NULL,
                type      TEXT    NOT NULL,
                shares    REAL    NOT NULL,
                price_cad REAL    NOT NULL,
                total_cad REAL    NOT NULL,
                date      TEXT    NOT NULL,
                notes     TEXT
            )
        """)
        c.execute("""
            INSERT INTO transactions (id, ticker, type, shares, price_cad, total_cad, date)
            SELECT id, ticker, action, shares, price, ROUND(price * shares, 4), date
            FROM _transactions_old
        """)
        c.execute("DROP TABLE _transactions_old")
        print("[database] Migrated transactions table to new schema")
    else:
        if "total_cad" not in cols:
            c.execute("ALTER TABLE transactions ADD COLUMN total_cad REAL NOT NULL DEFAULT 0")
        if "notes" not in cols:
            c.execute("ALTER TABLE transactions ADD COLUMN notes TEXT")

    c.execute("""
        CREATE TABLE IF NOT EXISTS tfsa_info (
            id                  INTEGER PRIMARY KEY,
            contribution_room   REAL    NOT NULL DEFAULT 0,
            total_deposited     REAL    NOT NULL DEFAULT 0,
            last_updated        TEXT    NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS cash (
            id              INTEGER PRIMARY KEY,
            available_cash  REAL    NOT NULL DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS alert_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT,
            urgency     TEXT,
            sent_at     TEXT    NOT NULL,
            message     TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS snoozed_tickers (
            ticker          TEXT    PRIMARY KEY,
            snoozed_until   TEXT    NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS scanner_alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT    NOT NULL,
            score       REAL    NOT NULL,
            sent_at     TEXT    NOT NULL,
            reason      TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            date      TEXT    NOT NULL,
            value_cad REAL    NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS predator_alerts (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker            TEXT    NOT NULL,
            score             REAL    NOT NULL,
            signals_json      TEXT    NOT NULL,
            entry_price       REAL,
            stop_price        REAL,
            position_size_cad REAL,
            alert_time        TEXT    NOT NULL,
            price_7d_later    REAL,
            price_14d_later   REAL,
            price_30d_later   REAL,
            outcome           TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT    NOT NULL,
            alert_price REAL    NOT NULL,
            direction   TEXT    NOT NULL DEFAULT 'above',
            note        TEXT,
            created_at  TEXT    NOT NULL,
            triggered   INTEGER NOT NULL DEFAULT 0,
            triggered_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS planner_config (
            id                  INTEGER PRIMARY KEY,
            paycheck_amount     REAL    NOT NULL DEFAULT 0,
            paycheck_day        INTEGER NOT NULL DEFAULT 15,
            allocation_percent  REAL    NOT NULL DEFAULT 100,
            last_updated        TEXT    NOT NULL
        )
    """)

    # Single-row lease table. CHECK (id = 1) makes it physically impossible
    # to insert a second row, so exactly one scheduler owner is enforced at
    # the DB level regardless of how many workers or processes are running.
    c.execute("""
        CREATE TABLE IF NOT EXISTS scheduler_lease (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            pid         INTEGER NOT NULL,
            hostname    TEXT    NOT NULL,
            acquired_at TEXT    NOT NULL
        )
    """)

    # Seed single-row tables
    c.execute("SELECT COUNT(*) FROM tfsa_info")
    if c.fetchone()[0] == 0:
        c.execute(
            "INSERT INTO tfsa_info (id, contribution_room, total_deposited, last_updated) VALUES (1, 0, 0, ?)",
            (datetime.now().isoformat(),),
        )

    c.execute("SELECT COUNT(*) FROM cash")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO cash (id, available_cash) VALUES (1, 0)")

    c.execute("SELECT COUNT(*) FROM planner_config")
    if c.fetchone()[0] == 0:
        c.execute(
            "INSERT INTO planner_config (id, paycheck_amount, paycheck_day, allocation_percent, last_updated) VALUES (1, 0, 15, 100, ?)",
            (datetime.now().isoformat(),),
        )

    c.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            id      INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL DEFAULT 0
        )
    """)

    c.execute("SELECT COUNT(*) FROM schema_version")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO schema_version (id, version) VALUES (1, 0)")

    conn.commit()
    conn.close()
    print("✅ Database initialized")


# ---------------------------------------------------------------------------
# Migration framework
# ---------------------------------------------------------------------------

class Migration(NamedTuple):
    version: int
    description: str
    sql: list  # list[str] — each entry is one SQL statement


# All migrations after the baseline schema established by init_db().
# Version 0 = the schema created by init_db() on first deploy.
# Add new migrations here as (version, description, [sql...]) tuples.
MIGRATIONS: list = [
    Migration(
        version=1,
        description="add predator_latest table (upserted scan results, replaces passive inserts)",
        sql=[
            """
            CREATE TABLE IF NOT EXISTS predator_latest (
                ticker       TEXT    PRIMARY KEY,
                score        REAL    NOT NULL,
                signals_json TEXT    NOT NULL DEFAULT '{}',
                entry_price  REAL,
                stop_price   REAL,
                scan_time    TEXT    NOT NULL
            )
            """
        ],
    ),
]


def _backup_db() -> str:
    """Checkpoint WAL, copy DB file, prune old backups. Returns backup path."""
    if not os.path.exists(DB_PATH):
        return ""

    # Checkpoint WAL so the backup is a single self-contained file.
    # TRUNCATE returns (busy, log, checkpointed); abort if any frames are busy
    # (active readers prevented a full flush — the backup would be inconsistent).
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        conn.close()
        if row and row[0] > 0:
            log.warning(
                "migration backup: WAL checkpoint busy (%d frames) — skipping backup",
                row[0],
            )
            return ""
    except Exception as exc:
        log.warning("migration backup: WAL checkpoint failed: %s — skipping backup", exc)
        return ""

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = f"{DB_PATH}.backup.{ts}"
    shutil.copy2(DB_PATH, backup_path)
    log.info("migration backup: created %s", backup_path)

    _prune_old_backups()
    return backup_path


def _prune_old_backups(keep: int = 5) -> None:
    pattern = f"{DB_PATH}.backup.*"
    backups = sorted(glob.glob(pattern))
    for old in backups[:-keep]:
        try:
            os.remove(old)
            log.info("migration backup: pruned %s", old)
        except OSError as exc:
            log.warning("migration backup: could not prune %s: %s", old, exc)


def _apply_migration(m: Migration) -> None:
    """
    Apply a single migration inside an EXCLUSIVE transaction.

    BEGIN EXCLUSIVE blocks all other writers for the duration, acting as a
    process-level migration lock. The version check inside the transaction
    makes this idempotent — a second process that gets in after us will see
    the updated version and skip.

    Raises on any error so the caller can abort startup.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN EXCLUSIVE")

        current = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
        if current is None or current["version"] >= m.version:
            conn.rollback()
            log.info("migration v%d already applied — skipping", m.version)
            return

        log.info("migration: applying v%d — %s", m.version, m.description)
        for statement in m.sql:
            conn.execute(statement)

        conn.execute("UPDATE schema_version SET version = ? WHERE id = 1", (m.version,))
        conn.commit()
        log.info("migration: v%d committed", m.version)
    except Exception:
        conn.rollback()
        log.exception("migration: v%d FAILED — rolled back", m.version)
        raise
    finally:
        conn.close()


def run_migrations() -> None:
    """
    Run all pending migrations in version order.

    Called once at startup (from wsgi.py, after init_db()).
    If any migration fails, raises — Railway will restart the dyno and retry.
    Only backs up the DB when there are pending migrations to apply.
    """
    if not MIGRATIONS:
        return

    conn = get_connection()
    try:
        row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
        current_version = row["version"] if row else 0
    finally:
        conn.close()

    pending = [m for m in MIGRATIONS if m.version > current_version]
    if not pending:
        log.info("migrations: schema at v%d — nothing to apply", current_version)
        return

    log.info(
        "migrations: schema at v%d — applying %d migration(s): %s",
        current_version,
        len(pending),
        [m.version for m in pending],
    )

    backup_path = _backup_db()
    if backup_path:
        log.info("migrations: DB backed up to %s before applying", backup_path)

    for m in sorted(pending, key=lambda x: x.version):
        _apply_migration(m)

    log.info("migrations: all migrations applied — schema now at v%d", pending[-1].version)
