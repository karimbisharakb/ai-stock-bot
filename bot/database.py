import sqlite3
import os
from datetime import datetime

# Railway injects RAILWAY_VOLUME_MOUNT_PATH when a persistent volume is attached.
# Fall back to DB_PATH env var, then to a local file beside this module.
_volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
if _volume:
    DB_PATH = os.path.join(_volume, "portfolio.db")
else:
    DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "portfolio.db"))

print(f"[database] DB_PATH = {DB_PATH}")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()

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

    conn.commit()
    conn.close()
    print("✅ Database initialized")
