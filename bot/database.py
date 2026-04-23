import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "portfolio.db")


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
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT    NOT NULL,
            action      TEXT    NOT NULL,
            shares      REAL    NOT NULL,
            price       REAL    NOT NULL,
            date        TEXT    NOT NULL,
            gain_loss   REAL
        )
    """)

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
