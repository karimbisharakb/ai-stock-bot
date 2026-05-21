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
    Migration(
        version=2,
        description=(
            "predator schema expansion: add confidence_pct, adjusted_score, raw_score, tier, "
            "per-signal score columns to predator_alerts and predator_latest; "
            "add predator_signal_weights table with seed data; add query indexes"
        ),
        sql=[
            # ── predator_alerts: new columns ─────────────────────────────────
            "ALTER TABLE predator_alerts ADD COLUMN confidence_pct      REAL",
            "ALTER TABLE predator_alerts ADD COLUMN adjusted_score      REAL",
            "ALTER TABLE predator_alerts ADD COLUMN raw_score           REAL",
            "ALTER TABLE predator_alerts ADD COLUMN tier                TEXT",
            "ALTER TABLE predator_alerts ADD COLUMN score_options       REAL",
            "ALTER TABLE predator_alerts ADD COLUMN score_insider       REAL",
            "ALTER TABLE predator_alerts ADD COLUMN score_short_squeeze REAL",
            "ALTER TABLE predator_alerts ADD COLUMN score_catalyst      REAL",
            "ALTER TABLE predator_alerts ADD COLUMN score_institutional REAL",
            "ALTER TABLE predator_alerts ADD COLUMN score_breakout      REAL",
            # ── predator_latest: same new columns ────────────────────────────
            "ALTER TABLE predator_latest ADD COLUMN confidence_pct      REAL",
            "ALTER TABLE predator_latest ADD COLUMN adjusted_score      REAL",
            "ALTER TABLE predator_latest ADD COLUMN raw_score           REAL",
            "ALTER TABLE predator_latest ADD COLUMN tier                TEXT",
            "ALTER TABLE predator_latest ADD COLUMN score_options       REAL",
            "ALTER TABLE predator_latest ADD COLUMN score_insider       REAL",
            "ALTER TABLE predator_latest ADD COLUMN score_short_squeeze REAL",
            "ALTER TABLE predator_latest ADD COLUMN score_catalyst      REAL",
            "ALTER TABLE predator_latest ADD COLUMN score_institutional REAL",
            "ALTER TABLE predator_latest ADD COLUMN score_breakout      REAL",
            # ── predator_signal_weights: static config for adaptive scoring ──
            # weight column is reserved for future adaptive learning — seeds at 1.0
            """
            CREATE TABLE IF NOT EXISTS predator_signal_weights (
                signal_name  TEXT    PRIMARY KEY,
                max_score    REAL    NOT NULL,
                weight       REAL    NOT NULL DEFAULT 1.0,
                last_updated TEXT    NOT NULL
            )
            """,
            # Seed one row per signal; INSERT OR IGNORE makes this re-runnable safely
            "INSERT OR IGNORE INTO predator_signal_weights (signal_name, max_score, weight, last_updated) VALUES ('options',       3, 1.0, CURRENT_TIMESTAMP)",
            "INSERT OR IGNORE INTO predator_signal_weights (signal_name, max_score, weight, last_updated) VALUES ('insider',       2, 1.0, CURRENT_TIMESTAMP)",
            "INSERT OR IGNORE INTO predator_signal_weights (signal_name, max_score, weight, last_updated) VALUES ('short_squeeze', 2, 1.0, CURRENT_TIMESTAMP)",
            "INSERT OR IGNORE INTO predator_signal_weights (signal_name, max_score, weight, last_updated) VALUES ('catalyst',      2, 1.0, CURRENT_TIMESTAMP)",
            "INSERT OR IGNORE INTO predator_signal_weights (signal_name, max_score, weight, last_updated) VALUES ('institutional', 1, 1.0, CURRENT_TIMESTAMP)",
            "INSERT OR IGNORE INTO predator_signal_weights (signal_name, max_score, weight, last_updated) VALUES ('breakout',      2, 1.0, CURRENT_TIMESTAMP)",
            # ── Indexes for predator query patterns ──────────────────────────
            "CREATE INDEX IF NOT EXISTS idx_predator_alerts_ticker     ON predator_alerts(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_predator_alerts_alert_time ON predator_alerts(alert_time)",
            "CREATE INDEX IF NOT EXISTS idx_predator_alerts_tier       ON predator_alerts(tier)",
            "CREATE INDEX IF NOT EXISTS idx_predator_alerts_score      ON predator_alerts(score)",
            "CREATE INDEX IF NOT EXISTS idx_predator_latest_score      ON predator_latest(score)",
            "CREATE INDEX IF NOT EXISTS idx_predator_latest_scan_time  ON predator_latest(scan_time)",
        ],
    ),
    Migration(
        version=3,
        description="add predator_outcomes table for forward-return tracking of CONVICTION alerts",
        sql=[
            """
            CREATE TABLE IF NOT EXISTS predator_outcomes (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker            TEXT    NOT NULL,
                alert_time        TEXT    NOT NULL,
                entry_price       REAL,
                confidence_pct    REAL,
                regime            TEXT,
                tier              TEXT,
                signal_summary    TEXT,
                outcome_status    TEXT    NOT NULL DEFAULT 'PENDING',
                return_1d         REAL,
                return_5d         REAL,
                return_20d        REAL,
                max_gain_pct      REAL,
                max_drawdown_pct  REAL,
                evaluated_at      TEXT
            )
            """,
            # UNIQUE prevents duplicate rows on retry / scheduler overlap
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_predator_outcomes_dedup ON predator_outcomes(ticker, alert_time)",
            # Fast lookup of pending rows (the evaluation job's primary query)
            "CREATE INDEX IF NOT EXISTS idx_predator_outcomes_status ON predator_outcomes(outcome_status)",
        ],
    ),
    Migration(
        version=4,
        description="add recommendation_snapshots table for adaptive weight observation (Phase 1G)",
        sql=[
            """
            CREATE TABLE IF NOT EXISTS recommendation_snapshots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_time TEXT    NOT NULL,
                row_count     INTEGER NOT NULL,
                weights_json  TEXT    NOT NULL,
                metrics_json  TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_rec_snapshots_time ON recommendation_snapshots(snapshot_time)",
        ],
    ),
    Migration(
        version=5,
        description=(
            "Phase N1: add notification_audit_log and notification_suppressed_log tables "
            "for the unified alert gateway"
        ),
        sql=[
            """
            CREATE TABLE IF NOT EXISTS notification_audit_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id          TEXT    NOT NULL,
                ticker            TEXT    NOT NULL,
                source            TEXT    NOT NULL,
                tier              TEXT    NOT NULL,
                adjusted_score    REAL,
                confidence_pct    REAL,
                raw_score         REAL,
                active_signals    TEXT,
                suppressed_signals TEXT,
                regime_context    TEXT,
                risk_posture      TEXT,
                trigger_reason    TEXT,
                formatted_message TEXT,
                dry_run           INTEGER NOT NULL DEFAULT 0,
                shadow            INTEGER NOT NULL DEFAULT 0,
                delivered         INTEGER NOT NULL DEFAULT 0,
                sent_at           TEXT,
                evaluated_at      TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_notif_audit_ticker  ON notification_audit_log(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_notif_audit_sent_at ON notification_audit_log(sent_at)",
            "CREATE INDEX IF NOT EXISTS idx_notif_audit_source  ON notification_audit_log(source)",
            "CREATE INDEX IF NOT EXISTS idx_notif_audit_shadow  ON notification_audit_log(shadow)",
            """
            CREATE TABLE IF NOT EXISTS notification_suppressed_log (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker              TEXT    NOT NULL,
                source              TEXT    NOT NULL,
                suppression_reasons TEXT,
                resolved_tier       TEXT,
                adjusted_score      REAL,
                confidence_pct      REAL,
                evaluated_at        TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_notif_sup_ticker ON notification_suppressed_log(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_notif_sup_eval   ON notification_suppressed_log(evaluated_at)",
        ],
    ),
    Migration(
        version=6,
        description="Phase A2: add alpha_shadow_log table for observation-only alpha engine results",
        sql=[
            """
            CREATE TABLE IF NOT EXISTS alpha_shadow_log (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker                TEXT    NOT NULL,
                scan_time             TEXT    NOT NULL,
                alpha_score           REAL,
                alpha_tier            TEXT,
                setup_type            TEXT,
                predator_tier         TEXT,
                predator_score        REAL,
                tier_match            INTEGER NOT NULL DEFAULT 0,
                filter_reason         TEXT,
                component_scores_json TEXT,
                explanation           TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_alpha_shadow_ticker    ON alpha_shadow_log(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_alpha_shadow_scan_time ON alpha_shadow_log(scan_time)",
            "CREATE INDEX IF NOT EXISTS idx_alpha_shadow_tier      ON alpha_shadow_log(alpha_tier)",
            "CREATE INDEX IF NOT EXISTS idx_alpha_shadow_score     ON alpha_shadow_log(alpha_score)",
        ],
    ),
    Migration(
        version=7,
        description="Phase A3: add detail_json to alpha_shadow_log for full explanation storage",
        sql=[
            "ALTER TABLE alpha_shadow_log ADD COLUMN detail_json TEXT",
        ],
    ),
    Migration(
        version=8,
        description="Phase A5: add alpha_outcomes table for outcome tracking and learning dataset",
        sql=[
            """
            CREATE TABLE IF NOT EXISTS alpha_outcomes (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker                TEXT    NOT NULL,
                scan_time             TEXT    NOT NULL,
                alpha_score           REAL,
                alpha_tier            TEXT,
                setup_type            TEXT,
                source                TEXT,
                component_scores_json TEXT,
                price_at_scan         REAL,
                price_1d              REAL,
                price_3d              REAL,
                price_5d              REAL,
                price_10d             REAL,
                price_20d             REAL,
                return_1d             REAL,
                return_3d             REAL,
                return_5d             REAL,
                return_10d            REAL,
                return_20d            REAL,
                max_gain              REAL,
                max_drawdown          REAL,
                status                TEXT NOT NULL DEFAULT 'PENDING',
                created_at            TEXT NOT NULL,
                updated_at            TEXT,
                UNIQUE(ticker, scan_time)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_alpha_outcomes_ticker    ON alpha_outcomes(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_alpha_outcomes_scan_time ON alpha_outcomes(scan_time)",
            "CREATE INDEX IF NOT EXISTS idx_alpha_outcomes_status    ON alpha_outcomes(status)",
            "CREATE INDEX IF NOT EXISTS idx_alpha_outcomes_tier      ON alpha_outcomes(alpha_tier)",
        ],
    ),
    Migration(
        version=9,
        description="Phase L3: add learning_proposals and learning_proposal_audit tables",
        sql=[
            """
            CREATE TABLE IF NOT EXISTS learning_proposals (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id            TEXT    NOT NULL UNIQUE,
                status                 TEXT    NOT NULL DEFAULT 'PROPOSED',
                kind                   TEXT    NOT NULL,
                weight_changes_json    TEXT,
                threshold_changes_json TEXT,
                shadow_weights_json    TEXT,
                evidence_summary       TEXT,
                sample_size            INTEGER NOT NULL DEFAULT 0,
                confidence             TEXT    NOT NULL,
                risk_warning           TEXT,
                expected_benefit       TEXT,
                expected_downside      TEXT,
                created_at             TEXT    NOT NULL,
                expires_at             TEXT    NOT NULL,
                reviewed_at            TEXT,
                reviewed_by            TEXT,
                review_note            TEXT,
                shadow_results_json    TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_proposals_status     ON learning_proposals(status)",
            "CREATE INDEX IF NOT EXISTS idx_proposals_created_at ON learning_proposals(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_proposals_kind       ON learning_proposals(kind)",
            """
            CREATE TABLE IF NOT EXISTS learning_proposal_audit (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id TEXT    NOT NULL,
                from_status TEXT,
                to_status   TEXT    NOT NULL,
                reason      TEXT,
                actor       TEXT,
                ts          TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_proposal_audit_proposal_id ON learning_proposal_audit(proposal_id)",
            "CREATE INDEX IF NOT EXISTS idx_proposal_audit_ts          ON learning_proposal_audit(ts)",
        ],
    ),
    Migration(
        version=10,
        description="Phase A6: add alpha_validation table for reality validation layer",
        sql=[
            """
            CREATE TABLE IF NOT EXISTS alpha_validation (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                outcome_id               INTEGER NOT NULL UNIQUE,
                ticker                   TEXT    NOT NULL,
                scan_time                TEXT    NOT NULL,
                setup_type               TEXT,
                alpha_tier               TEXT,
                behavior_class           TEXT    NOT NULL,
                validation_score         REAL    NOT NULL,
                confidence               TEXT    NOT NULL,
                follow_through_score     REAL,
                gain_retention           REAL,
                drawdown_severity        REAL,
                continuation_quality     REAL,
                multi_window_consistency REAL,
                sustained_strength       REAL,
                reversal_severity        REAL,
                n_windows                INTEGER NOT NULL DEFAULT 0,
                evidence_summary         TEXT,
                key_failure_reason       TEXT,
                key_success_reason       TEXT,
                computed_at              TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_alpha_validation_outcome_id    ON alpha_validation(outcome_id)",
            "CREATE INDEX IF NOT EXISTS idx_alpha_validation_ticker         ON alpha_validation(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_alpha_validation_behavior_class ON alpha_validation(behavior_class)",
            "CREATE INDEX IF NOT EXISTS idx_alpha_validation_setup_type     ON alpha_validation(setup_type)",
            "CREATE INDEX IF NOT EXISTS idx_alpha_validation_alpha_tier     ON alpha_validation(alpha_tier)",
        ],
    ),
    Migration(
        version=11,
        description="Phase A8: add alpha_notification_dryruns table for dry-run review workflow",
        sql=[
            """
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
            """,
            "CREATE INDEX IF NOT EXISTS idx_dryrun_ticker         ON alpha_notification_dryruns(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_dryrun_status         ON alpha_notification_dryruns(status)",
            "CREATE INDEX IF NOT EXISTS idx_dryrun_readiness_tier ON alpha_notification_dryruns(readiness_tier)",
            "CREATE INDEX IF NOT EXISTS idx_dryrun_created_at     ON alpha_notification_dryruns(created_at)",
        ],
    ),
    Migration(
        version=12,
        description="Phase A9: add notification_qc_history table for notification quality-control layer",
        sql=[
            """
            CREATE TABLE IF NOT EXISTS notification_qc_history (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker                 TEXT    NOT NULL,
                readiness_tier         TEXT    NOT NULL,
                alpha_tier             TEXT    NOT NULL,
                setup_type             TEXT    NOT NULL,
                alpha_score            REAL,
                readiness_score        REAL,
                qc_score               REAL    NOT NULL,
                qc_tier                TEXT    NOT NULL,
                allow_notification     INTEGER NOT NULL DEFAULT 0,
                suppression_reason     TEXT,
                cooldown_remaining     REAL    NOT NULL DEFAULT 0.0,
                novelty_score          REAL    NOT NULL DEFAULT 0.0,
                stability_score        REAL    NOT NULL DEFAULT 0.0,
                information_gain_score REAL    NOT NULL DEFAULT 0.0,
                quality_flags_json     TEXT    NOT NULL DEFAULT '[]',
                behavior_class         TEXT,
                dry_run_id             TEXT,
                evaluated_at           TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_qc_ticker       ON notification_qc_history(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_qc_qc_tier      ON notification_qc_history(qc_tier)",
            "CREATE INDEX IF NOT EXISTS idx_qc_evaluated_at ON notification_qc_history(evaluated_at)",
            "CREATE INDEX IF NOT EXISTS idx_qc_allow        ON notification_qc_history(allow_notification)",
        ],
    ),
    Migration(
        version=13,
        description="Phase A10: add alpha_notification_delivery_log table for delivery audit trail",
        sql=[
            """
            CREATE TABLE IF NOT EXISTS alpha_notification_delivery_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                dry_run_id        TEXT    NOT NULL,
                ticker            TEXT    NOT NULL DEFAULT '',
                readiness_tier    TEXT    NOT NULL DEFAULT '',
                message_hash      TEXT    NOT NULL DEFAULT '',
                status            TEXT    NOT NULL,
                reason            TEXT,
                provider_response TEXT,
                sent_at           TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_delivery_dry_run_id ON alpha_notification_delivery_log(dry_run_id)",
            "CREATE INDEX IF NOT EXISTS idx_delivery_ticker      ON alpha_notification_delivery_log(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_delivery_status      ON alpha_notification_delivery_log(status)",
            "CREATE INDEX IF NOT EXISTS idx_delivery_sent_at     ON alpha_notification_delivery_log(sent_at)",
        ],
    ),
    Migration(
        version=14,
        description=(
            "Phase A11: add portfolio_positions, portfolio_snapshots, "
            "and portfolio_reconciliation_log tables for canonical portfolio truth layer"
        ),
        sql=[
            """
            CREATE TABLE IF NOT EXISTS portfolio_positions (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker             TEXT    NOT NULL UNIQUE,
                quantity           REAL    NOT NULL,
                avg_cost           REAL    NOT NULL,
                market_price       REAL,
                market_value       REAL,
                cost_basis         REAL,
                unrealized_pnl     REAL,
                unrealized_pnl_pct REAL,
                realized_pnl       REAL    NOT NULL DEFAULT 0.0,
                source             TEXT    NOT NULL DEFAULT 'manual',
                is_stale           INTEGER NOT NULL DEFAULT 0,
                concentration_pct  REAL    NOT NULL DEFAULT 0.0,
                price_fetched_at   TEXT,
                reconciled_at      TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_positions_ticker        ON portfolio_positions(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_positions_reconciled_at ON portfolio_positions(reconciled_at)",
            "CREATE INDEX IF NOT EXISTS idx_positions_is_stale      ON portfolio_positions(is_stale)",
            """
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id           TEXT    NOT NULL UNIQUE,
                trigger               TEXT    NOT NULL DEFAULT 'manual',
                total_market_value    REAL    NOT NULL,
                total_cost_basis      REAL    NOT NULL,
                total_unrealized_pnl  REAL    NOT NULL,
                total_realized_pnl    REAL    NOT NULL,
                cash                  REAL    NOT NULL,
                total_portfolio_value REAL    NOT NULL,
                position_count        INTEGER NOT NULL,
                stale_count           INTEGER NOT NULL DEFAULT 0,
                positions_json        TEXT    NOT NULL,
                taken_at              TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_snapshots_snapshot_id ON portfolio_snapshots(snapshot_id)",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_taken_at    ON portfolio_snapshots(taken_at)",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_trigger     ON portfolio_snapshots(trigger)",
            """
            CREATE TABLE IF NOT EXISTS portfolio_reconciliation_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id          TEXT    NOT NULL UNIQUE,
                trigger         TEXT    NOT NULL DEFAULT 'manual',
                positions_found INTEGER NOT NULL DEFAULT 0,
                issues_found    INTEGER NOT NULL DEFAULT 0,
                issues_json     TEXT    NOT NULL DEFAULT '[]',
                duration_ms     REAL,
                status          TEXT    NOT NULL DEFAULT 'OK',
                reconciled_at   TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_recon_log_run_id        ON portfolio_reconciliation_log(run_id)",
            "CREATE INDEX IF NOT EXISTS idx_recon_log_reconciled_at ON portfolio_reconciliation_log(reconciled_at)",
            "CREATE INDEX IF NOT EXISTS idx_recon_log_status        ON portfolio_reconciliation_log(status)",
        ],
    ),
    Migration(
        version=15,
        description=(
            "Phase A12: add manual_portfolio_positions, manual_account_settings, "
            "and manual_portfolio_audit_log tables for manual portfolio control"
        ),
        sql=[
            """
            CREATE TABLE IF NOT EXISTS manual_portfolio_positions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker       TEXT    NOT NULL UNIQUE,
                quantity     REAL    NOT NULL,
                avg_cost     REAL    NOT NULL,
                realized_pnl REAL    NOT NULL DEFAULT 0.0,
                account_type TEXT    NOT NULL DEFAULT 'TFSA',
                currency     TEXT    NOT NULL DEFAULT 'CAD',
                note         TEXT    NOT NULL DEFAULT '',
                active       INTEGER NOT NULL DEFAULT 1,
                created_at   TEXT    NOT NULL,
                updated_at   TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_manual_pos_ticker  ON manual_portfolio_positions(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_manual_pos_active  ON manual_portfolio_positions(active)",
            """
            CREATE TABLE IF NOT EXISTS manual_account_settings (
                id                INTEGER PRIMARY KEY CHECK (id = 1),
                account_name      TEXT    NOT NULL DEFAULT '',
                account_type      TEXT    NOT NULL DEFAULT 'TFSA',
                base_currency     TEXT    NOT NULL DEFAULT 'CAD',
                available_cash    REAL    NOT NULL DEFAULT 0.0,
                contribution_room REAL,
                notes             TEXT    NOT NULL DEFAULT '',
                updated_at        TEXT    NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS manual_portfolio_audit_log (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                action         TEXT    NOT NULL,
                subject        TEXT    NOT NULL,
                old_value_json TEXT,
                new_value_json TEXT,
                performed_at   TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_manual_audit_action       ON manual_portfolio_audit_log(action)",
            "CREATE INDEX IF NOT EXISTS idx_manual_audit_subject      ON manual_portfolio_audit_log(subject)",
            "CREATE INDEX IF NOT EXISTS idx_manual_audit_performed_at ON manual_portfolio_audit_log(performed_at)",
        ],
    ),
    Migration(
        version=16,
        description=(
            "Phase A13: add position_theses and position_journal tables "
            "for position journal and trade thesis tracking"
        ),
        sql=[
            """
            CREATE TABLE IF NOT EXISTS position_theses (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker                TEXT    NOT NULL UNIQUE,
                thesis_title          TEXT    NOT NULL DEFAULT '',
                thesis_text           TEXT    NOT NULL DEFAULT '',
                setup_type            TEXT    NOT NULL DEFAULT '',
                conviction_level      TEXT    NOT NULL DEFAULT 'MEDIUM',
                time_horizon          TEXT    NOT NULL DEFAULT 'MEDIUM',
                entry_reason          TEXT    NOT NULL DEFAULT '',
                expected_catalysts    TEXT    NOT NULL DEFAULT '',
                risk_factors          TEXT    NOT NULL DEFAULT '',
                invalidation_level    REAL,
                target_level          REAL,
                exit_plan             TEXT    NOT NULL DEFAULT '',
                review_frequency_days INTEGER NOT NULL DEFAULT 30,
                next_review_at        TEXT    NOT NULL,
                status                TEXT    NOT NULL DEFAULT 'ACTIVE',
                created_at            TEXT    NOT NULL,
                updated_at            TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_theses_ticker      ON position_theses(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_theses_status      ON position_theses(status)",
            "CREATE INDEX IF NOT EXISTS idx_theses_next_review ON position_theses(next_review_at)",
            """
            CREATE TABLE IF NOT EXISTS position_journal (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker            TEXT    NOT NULL,
                entry_type        TEXT    NOT NULL,
                text              TEXT    NOT NULL DEFAULT '',
                tags_json         TEXT    NOT NULL DEFAULT '[]',
                confidence_change TEXT,
                created_at        TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_journal_ticker     ON position_journal(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_journal_entry_type ON position_journal(entry_type)",
            "CREATE INDEX IF NOT EXISTS idx_journal_created_at ON position_journal(created_at)",
        ],
    ),
    Migration(
        version=17,
        description=(
            "Phase A14: add decision_checklists, decision_checklist_items, "
            "and decision_checklist_audit tables for entry/exit discipline"
        ),
        sql=[
            """
            CREATE TABLE IF NOT EXISTS decision_checklists (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                checklist_id              TEXT    NOT NULL UNIQUE,
                ticker                    TEXT    NOT NULL,
                decision_type             TEXT    NOT NULL,
                linked_alpha_candidate_id TEXT,
                linked_thesis_id          INTEGER,
                checklist_status          TEXT    NOT NULL DEFAULT 'DRAFT',
                checklist_completion      REAL    NOT NULL DEFAULT 0.0,
                blocking_items            INTEGER NOT NULL DEFAULT 0,
                readiness                 TEXT    NOT NULL DEFAULT 'NOT_READY',
                notes                     TEXT    NOT NULL DEFAULT '',
                created_at                TEXT    NOT NULL,
                reviewed_at               TEXT,
                updated_at                TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_dcl_ticker        ON decision_checklists(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_dcl_status        ON decision_checklists(checklist_status)",
            "CREATE INDEX IF NOT EXISTS idx_dcl_decision_type ON decision_checklists(decision_type)",
            "CREATE INDEX IF NOT EXISTS idx_dcl_created_at    ON decision_checklists(created_at)",
            """
            CREATE TABLE IF NOT EXISTS decision_checklist_items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                checklist_id TEXT    NOT NULL,
                item_key     TEXT    NOT NULL,
                label        TEXT    NOT NULL DEFAULT '',
                passed       INTEGER,
                note         TEXT    NOT NULL DEFAULT '',
                required     INTEGER NOT NULL DEFAULT 1,
                created_at   TEXT    NOT NULL,
                updated_at   TEXT    NOT NULL,
                UNIQUE(checklist_id, item_key)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_dcl_items_checklist_id ON decision_checklist_items(checklist_id)",
            """
            CREATE TABLE IF NOT EXISTS decision_checklist_audit (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                checklist_id TEXT    NOT NULL,
                action       TEXT    NOT NULL,
                from_status  TEXT,
                to_status    TEXT,
                actor        TEXT,
                detail_json  TEXT,
                performed_at TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_dcl_audit_checklist_id ON decision_checklist_audit(checklist_id)",
            "CREATE INDEX IF NOT EXISTS idx_dcl_audit_performed_at ON decision_checklist_audit(performed_at)",
        ],
    ),
    Migration(
        version=18,
        description=(
            "Phase A15: add risk_policy table for configurable portfolio "
            "risk and position-sizing guardrails"
        ),
        sql=[
            """
            CREATE TABLE IF NOT EXISTS risk_policy (
                id                      INTEGER PRIMARY KEY CHECK (id = 1),
                max_single_position_pct REAL    NOT NULL DEFAULT 10.0,
                max_speculative_pct     REAL    NOT NULL DEFAULT 20.0,
                max_same_theme_pct      REAL    NOT NULL DEFAULT 25.0,
                max_expected_loss_pct   REAL    NOT NULL DEFAULT 2.0,
                min_cash_reserve_pct    REAL    NOT NULL DEFAULT 5.0,
                high_volatility_haircut REAL    NOT NULL DEFAULT 0.5,
                risk_off_haircut        REAL    NOT NULL DEFAULT 0.5,
                risk_off_mode           INTEGER NOT NULL DEFAULT 0,
                updated_at              TEXT    NOT NULL
            )
            """,
        ],
    ),
    Migration(
        version=19,
        description=(
            "Phase A16: add market_regime_snapshots table for immutable "
            "regime intelligence history"
        ),
        sql=[
            """
            CREATE TABLE IF NOT EXISTS market_regime_snapshots (
                id                         INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at                TEXT    NOT NULL,
                overall_regime             TEXT    NOT NULL,
                volatility_regime          TEXT    NOT NULL,
                breadth_regime             TEXT    NOT NULL,
                speculative_regime         TEXT    NOT NULL,
                regime_score               REAL    NOT NULL,
                risk_multiplier            REAL    NOT NULL,
                sizing_multiplier          REAL    NOT NULL,
                alpha_threshold_adjustment REAL    NOT NULL,
                confidence_adjustment      REAL    NOT NULL,
                explanation                TEXT    NOT NULL,
                warnings_json              TEXT    NOT NULL DEFAULT '[]',
                data_quality               TEXT    NOT NULL,
                raw_signals_json           TEXT    NOT NULL DEFAULT '{}'
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_mri_captured_at ON market_regime_snapshots(captured_at)",
        ],
    ),
    Migration(
        version=20,
        description=(
            "Phase A17: add replay_runs and replay_events tables for historical "
            "replay and decision simulation"
        ),
        sql=[
            """
            CREATE TABLE IF NOT EXISTS replay_runs (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id            TEXT    NOT NULL UNIQUE,
                created_at        TEXT    NOT NULL,
                start_date        TEXT    NOT NULL,
                end_date          TEXT    NOT NULL,
                ticker_filter     TEXT,
                source_filter     TEXT,
                setup_type_filter TEXT,
                max_rows          INTEGER NOT NULL DEFAULT 500,
                status            TEXT    NOT NULL DEFAULT 'PENDING',
                event_count       INTEGER NOT NULL DEFAULT 0,
                completed_at      TEXT,
                summary_json      TEXT    NOT NULL DEFAULT '{}'
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS replay_events (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id                 TEXT    NOT NULL,
                shadow_log_id          INTEGER,
                ticker                 TEXT    NOT NULL,
                scan_time              TEXT    NOT NULL,
                alpha_score            REAL,
                alpha_tier             TEXT,
                setup_type             TEXT,
                source                 TEXT,
                filter_reason          TEXT,
                readiness_tier         TEXT,
                readiness_score        REAL,
                alert_ready            INTEGER,
                qc_tier                TEXT,
                qc_score               REAL,
                allow_notification     INTEGER,
                regime_overall         TEXT,
                regime_score           REAL,
                regime_captured_at     TEXT,
                simulated_decision     TEXT    NOT NULL,
                outcome_status         TEXT,
                return_5d              REAL,
                return_10d             REAL,
                max_gain               REAL,
                max_drawdown           REAL,
                outcome_classification TEXT,
                created_at             TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_replay_events_run_id ON replay_events(run_id)",
            "CREATE INDEX IF NOT EXISTS idx_replay_events_ticker  ON replay_events(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_replay_runs_created   ON replay_runs(created_at)",
        ],
    ),
    Migration(
        version=21,
        description="Phase A18: add portfolio_stress_runs and portfolio_stress_events tables",
        sql=[
            """
            CREATE TABLE IF NOT EXISTS portfolio_stress_runs (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id            TEXT    UNIQUE NOT NULL,
                created_at        TEXT    NOT NULL,
                portfolio_value   REAL    NOT NULL DEFAULT 0.0,
                cash              REAL    NOT NULL DEFAULT 0.0,
                position_count    INTEGER NOT NULL DEFAULT 0,
                scenario_count    INTEGER NOT NULL DEFAULT 0,
                worst_scenario    TEXT,
                worst_loss_pct    REAL,
                avg_loss_pct      REAL,
                warnings_json     TEXT    NOT NULL DEFAULT '[]',
                summary_json      TEXT    NOT NULL DEFAULT '{}'
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS portfolio_stress_events (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id                    TEXT    NOT NULL,
                scenario_type             TEXT    NOT NULL,
                estimated_loss_pct        REAL    NOT NULL,
                estimated_loss_amount     REAL    NOT NULL,
                risk_level                TEXT    NOT NULL,
                position_results_json     TEXT    NOT NULL DEFAULT '[]',
                recommended_actions_json  TEXT    NOT NULL DEFAULT '[]',
                created_at                TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_psr_run_id     ON portfolio_stress_runs(run_id)",
            "CREATE INDEX IF NOT EXISTS idx_psr_created_at ON portfolio_stress_runs(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_pse_run_id     ON portfolio_stress_events(run_id)",
        ],
    ),
    Migration(
        version=22,
        description="Phase A20: add planner_snapshots table for long-horizon compounding planner",
        sql=[
            """
            CREATE TABLE IF NOT EXISTS planner_snapshots (
                id                            INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id                   TEXT    UNIQUE NOT NULL,
                created_at                    TEXT    NOT NULL,
                portfolio_value               REAL    NOT NULL DEFAULT 0.0,
                cash                          REAL    NOT NULL DEFAULT 0.0,
                regime                        TEXT    NOT NULL DEFAULT 'NEUTRAL',
                risk_score                    REAL    NOT NULL DEFAULT 50.0,
                rebalance_urgency             TEXT    NOT NULL DEFAULT 'NONE',
                current_allocation_json       TEXT    NOT NULL DEFAULT '{}',
                target_allocation_json        TEXT    NOT NULL DEFAULT '{}',
                drift_json                    TEXT    NOT NULL DEFAULT '{}',
                priority_areas_json           TEXT    NOT NULL DEFAULT '[]',
                cash_deployment_guidance      TEXT    NOT NULL DEFAULT '',
                contribution_guidance         TEXT    NOT NULL DEFAULT '',
                risk_reduction_guidance       TEXT    NOT NULL DEFAULT '',
                strategy_alignment_notes_json TEXT    NOT NULL DEFAULT '[]',
                projections_json              TEXT    NOT NULL DEFAULT '{}',
                monthly_contribution          REAL    NOT NULL DEFAULT 0.0
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_planner_created_at ON planner_snapshots(created_at)",
        ],
    ),
    Migration(
        version=23,
        description="Phase A24: add research_watchlist and research_watchlist_notes tables",
        sql=[
            """
            CREATE TABLE IF NOT EXISTS research_watchlist (
                id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker                      TEXT    NOT NULL UNIQUE,
                name                        TEXT,
                asset_type                  TEXT    NOT NULL DEFAULT 'STOCK',
                category                    TEXT    NOT NULL DEFAULT 'LEARNING',
                status                      TEXT    NOT NULL DEFAULT 'WATCHING',
                priority                    TEXT    NOT NULL DEFAULT 'MEDIUM',
                reason                      TEXT    NOT NULL DEFAULT '',
                linked_alpha_candidate_id   INTEGER,
                linked_thesis_id            INTEGER,
                next_review_at              TEXT,
                created_at                  TEXT    NOT NULL,
                updated_at                  TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_rwl_status   ON research_watchlist(status)",
            "CREATE INDEX IF NOT EXISTS idx_rwl_priority ON research_watchlist(priority)",
            "CREATE INDEX IF NOT EXISTS idx_rwl_review   ON research_watchlist(next_review_at)",
            """
            CREATE TABLE IF NOT EXISTS research_watchlist_notes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker      TEXT    NOT NULL,
                note_type   TEXT    NOT NULL DEFAULT 'OTHER',
                text        TEXT    NOT NULL,
                tags        TEXT    NOT NULL DEFAULT '[]',
                created_at  TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_rwl_notes_ticker ON research_watchlist_notes(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_rwl_notes_type   ON research_watchlist_notes(note_type)",
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
