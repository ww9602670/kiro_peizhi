from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
BOCAI_DB = BACKEND_DIR / "data" / "bocai.db"
JND28_DB = ROOT_DIR / "jnd28.sqlite3"

EXPECTED_TABLES: dict[str, list[str]] = {
    "operators": [
        "id", "username", "password", "role", "status", "max_accounts",
        "expire_date", "current_jti", "created_by", "created_at", "updated_at",
    ],
    "gambling_accounts": [
        "id", "operator_id", "account_name", "password", "platform_type",
        "platform_url", "status", "session_token", "balance", "login_fail_count",
        "last_login_at", "kill_switch", "single_bet_limit", "daily_limit",
        "period_limit", "worker_lock_token", "worker_lock_ts", "created_at",
        "updated_at",
    ],
    "strategies": [
        "id", "operator_id", "account_id", "name", "type", "play_code",
        "base_amount", "martin_sequence", "bet_timing", "simulation", "status",
        "martin_level", "stop_loss", "take_profit", "daily_pnl", "total_pnl",
        "daily_pnl_date", "platform_type", "created_at", "updated_at",
    ],
    "bet_orders": [
        "id", "idempotent_id", "operator_id", "account_id", "strategy_id",
        "issue", "key_code", "amount", "odds", "status", "bet_response",
        "open_result", "sum_value", "is_win", "pnl", "simulation",
        "martin_level", "bet_at", "settled_at", "fail_reason", "match_source",
        "pending_match_count", "created_at",
    ],
    "alerts": [
        "id", "operator_id", "type", "level", "title", "detail", "is_read",
        "created_at",
    ],
    "audit_logs": [
        "id", "operator_id", "action", "target_type", "target_id", "detail",
        "ip_address", "created_at",
    ],
    "lottery_results": [
        "id", "issue", "open_result", "sum_value", "open_time", "created_at",
    ],
    "reconcile_records": [
        "id", "account_id", "issue", "local_bet_count", "platform_bet_count",
        "local_balance", "platform_balance", "diff_amount", "status", "detail",
        "resolved_by", "created_at",
    ],
    "bet_order_platform_records": [
        "id", "issue", "key_code", "amount", "win_amount", "raw_json",
        "created_at",
    ],
    "account_odds": [
        "id", "account_id", "key_code", "odds_value", "confirmed", "fetched_at",
        "confirmed_at",
    ],
    "backtest_tasks": [
        "id", "operator_id", "strategy_type", "key_codes", "base_amount",
        "martin_sequence", "odds_map", "start_issue", "end_issue", "status",
        "error_message", "result_json", "total_issues", "processed_issues",
        "created_at", "completed_at",
    ],
}

EXPECTED_INDEXES = [
    "idx_account_odds_account",
    "idx_alerts_operator",
    "idx_audit_logs_operator",
    "idx_backtest_tasks_operator",
    "idx_bet_orders_account",
    "idx_bet_orders_issue",
    "idx_bet_orders_strategy",
    "idx_platform_records_issue",
    "idx_reconcile_account",
]


def _schema_report(db_path: Path) -> dict[str, object]:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    actual_tables = [
        row[0]
        for row in cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    actual_indexes = [
        row[0]
        for row in cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]

    column_gaps: dict[str, list[str]] = {}
    extra_columns: dict[str, list[str]] = {}
    for table_name, expected_columns in EXPECTED_TABLES.items():
        rows = list(cur.execute(f"PRAGMA table_info({table_name})"))
        actual_columns = [row[1] for row in rows]
        missing = [col for col in expected_columns if col not in actual_columns]
        extra = [col for col in actual_columns if col not in expected_columns]
        if missing:
            column_gaps[table_name] = missing
        if extra:
            extra_columns[table_name] = extra

    admin_rows = list(
        cur.execute(
            "SELECT id, username, role, status "
            "FROM operators WHERE username='admin'"
        )
    )
    conn.close()

    return {
        "missing_tables": sorted(set(EXPECTED_TABLES) - set(actual_tables)),
        "extra_tables": sorted(set(actual_tables) - set(EXPECTED_TABLES)),
        "missing_indexes": sorted(set(EXPECTED_INDEXES) - set(actual_indexes)),
        "extra_indexes": sorted(set(actual_indexes) - set(EXPECTED_INDEXES)),
        "missing_columns": column_gaps,
        "extra_columns": extra_columns,
        "admin_rows": admin_rows,
    }


def _ensure_bocai_schema(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS operators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'operator',
            status TEXT NOT NULL DEFAULT 'active',
            max_accounts INTEGER NOT NULL DEFAULT 1,
            expire_date TEXT,
            current_jti TEXT,
            created_by INTEGER REFERENCES operators(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE TABLE IF NOT EXISTS gambling_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operator_id INTEGER NOT NULL REFERENCES operators(id),
            account_name TEXT NOT NULL,
            password TEXT NOT NULL,
            platform_type TEXT NOT NULL,
            platform_url TEXT,
            status TEXT NOT NULL DEFAULT 'inactive',
            session_token TEXT,
            balance INTEGER DEFAULT 0,
            login_fail_count INTEGER DEFAULT 0,
            last_login_at TEXT,
            kill_switch INTEGER NOT NULL DEFAULT 0,
            single_bet_limit INTEGER,
            daily_limit INTEGER,
            period_limit INTEGER,
            worker_lock_token TEXT DEFAULT NULL,
            worker_lock_ts TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
            UNIQUE(operator_id, account_name, platform_type)
        );
        CREATE TABLE IF NOT EXISTS strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operator_id INTEGER NOT NULL REFERENCES operators(id),
            account_id INTEGER NOT NULL REFERENCES gambling_accounts(id),
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            play_code TEXT NOT NULL,
            base_amount INTEGER NOT NULL,
            martin_sequence TEXT,
            bet_timing INTEGER NOT NULL DEFAULT 30,
            simulation INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'stopped',
            martin_level INTEGER NOT NULL DEFAULT 0,
            stop_loss INTEGER,
            take_profit INTEGER,
            daily_pnl INTEGER NOT NULL DEFAULT 0,
            total_pnl INTEGER NOT NULL DEFAULT 0,
            daily_pnl_date TEXT,
            platform_type TEXT NOT NULL DEFAULT 'JND28WEB',
            created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE TABLE IF NOT EXISTS bet_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idempotent_id TEXT NOT NULL UNIQUE,
            operator_id INTEGER NOT NULL REFERENCES operators(id),
            account_id INTEGER NOT NULL REFERENCES gambling_accounts(id),
            strategy_id INTEGER NOT NULL REFERENCES strategies(id),
            issue TEXT NOT NULL,
            key_code TEXT NOT NULL,
            amount INTEGER NOT NULL,
            odds INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            bet_response TEXT,
            open_result TEXT,
            sum_value INTEGER,
            is_win INTEGER,
            pnl INTEGER,
            simulation INTEGER NOT NULL DEFAULT 0,
            martin_level INTEGER,
            bet_at TEXT,
            settled_at TEXT,
            fail_reason TEXT,
            match_source TEXT DEFAULT NULL,
            pending_match_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE INDEX IF NOT EXISTS idx_bet_orders_issue ON bet_orders(issue);
        CREATE INDEX IF NOT EXISTS idx_bet_orders_account ON bet_orders(account_id, issue);
        CREATE INDEX IF NOT EXISTS idx_bet_orders_strategy ON bet_orders(strategy_id, created_at);
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operator_id INTEGER NOT NULL REFERENCES operators(id),
            type TEXT NOT NULL,
            level TEXT NOT NULL DEFAULT 'warning',
            title TEXT NOT NULL,
            detail TEXT,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE INDEX IF NOT EXISTS idx_alerts_operator ON alerts(operator_id, is_read, created_at);
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operator_id INTEGER REFERENCES operators(id),
            action TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            detail TEXT,
            ip_address TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE INDEX IF NOT EXISTS idx_audit_logs_operator ON audit_logs(operator_id, created_at);
        CREATE TABLE IF NOT EXISTS lottery_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue TEXT NOT NULL UNIQUE,
            open_result TEXT NOT NULL,
            sum_value INTEGER NOT NULL,
            open_time TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE TABLE IF NOT EXISTS reconcile_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES gambling_accounts(id),
            issue TEXT NOT NULL,
            local_bet_count INTEGER NOT NULL,
            platform_bet_count INTEGER,
            local_balance INTEGER,
            platform_balance INTEGER,
            diff_amount INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            detail TEXT,
            resolved_by TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE INDEX IF NOT EXISTS idx_reconcile_account ON reconcile_records(account_id, issue);
        CREATE TABLE IF NOT EXISTS bet_order_platform_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue TEXT NOT NULL,
            key_code TEXT NOT NULL,
            amount INTEGER NOT NULL,
            win_amount INTEGER NOT NULL,
            raw_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE INDEX IF NOT EXISTS idx_platform_records_issue ON bet_order_platform_records(issue);
        CREATE TABLE IF NOT EXISTS account_odds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES gambling_accounts(id) ON DELETE CASCADE,
            key_code TEXT NOT NULL,
            odds_value INTEGER NOT NULL,
            confirmed INTEGER NOT NULL DEFAULT 0,
            fetched_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
            confirmed_at TEXT,
            UNIQUE(account_id, key_code)
        );
        CREATE INDEX IF NOT EXISTS idx_account_odds_account ON account_odds(account_id, confirmed);
        CREATE TABLE IF NOT EXISTS backtest_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operator_id INTEGER NOT NULL REFERENCES operators(id),
            strategy_type TEXT NOT NULL,
            key_codes TEXT NOT NULL,
            base_amount INTEGER NOT NULL,
            martin_sequence TEXT,
            odds_map TEXT NOT NULL,
            start_issue TEXT NOT NULL,
            end_issue TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error_message TEXT,
            result_json TEXT,
            total_issues INTEGER DEFAULT 0,
            processed_issues INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
            completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_backtest_tasks_operator ON backtest_tasks(operator_id, created_at);
        """
    )
    cur.execute(
        "INSERT OR IGNORE INTO operators (username, password, role, status) "
        "VALUES ('admin', 'admin123', 'admin', 'active')"
    )
    conn.commit()
    conn.close()


def _ensure_jnd28_schema(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS jnd28_history ("
        "issue TEXT PRIMARY KEY, "
        "open_time TEXT, "
        "d1 INTEGER, "
        "d2 INTEGER, "
        "d3 INTEGER, "
        "sum INTEGER, "
        "created_at TEXT)"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS sync_state ("
        "k TEXT PRIMARY KEY, "
        "v TEXT)"
    )
    conn.commit()
    conn.close()


def _backfill_jnd28_from_lottery_results(bocai_db: Path, jnd28_db: Path) -> list[str]:
    b_conn = sqlite3.connect(str(bocai_db))
    b_conn.row_factory = sqlite3.Row
    j_conn = sqlite3.connect(str(jnd28_db))
    j_cur = j_conn.cursor()
    inserted_issues: list[str] = []

    rows = b_conn.execute(
        "SELECT issue, open_result, sum_value, open_time, created_at "
        "FROM lottery_results ORDER BY issue"
    ).fetchall()

    for row in rows:
        issue = str(row["issue"])
        exists = j_cur.execute(
            "SELECT 1 FROM jnd28_history WHERE issue=?",
            (issue,),
        ).fetchone()
        if exists:
            continue

        parts = [part.strip() for part in str(row["open_result"]).split(",")]
        if len(parts) != 3:
            continue
        try:
            d1, d2, d3 = (int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            continue

        j_cur.execute(
            "INSERT INTO jnd28_history(issue, open_time, d1, d2, d3, sum, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                issue,
                row["open_time"],
                d1,
                d2,
                d3,
                int(row["sum_value"]),
                row["created_at"],
            ),
        )
        inserted_issues.append(issue)

    j_conn.commit()
    b_conn.close()
    j_conn.close()
    return inserted_issues


def _find_sql_files(root_dir: Path) -> list[Path]:
    return sorted(root_dir.rglob("*.sql"))


def _connect_jnd_read_only() -> sqlite3.Connection:
    uri = f"file:{JND28_DB.as_posix()}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check and repair local SQLite data completeness."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply additive repairs to bocai.db and jnd28.sqlite3.",
    )
    args = parser.parse_args()

    sql_files = _find_sql_files(ROOT_DIR)
    print(f"sql_files_found={len(sql_files)}")
    for path in sql_files:
        print(f"sql_file={path}")

    report = _schema_report(BOCAI_DB)
    print(f"missing_tables={report['missing_tables']}")
    print(f"extra_tables={report['extra_tables']}")
    print(f"missing_indexes={report['missing_indexes']}")
    print(f"extra_indexes={report['extra_indexes']}")
    print(f"missing_columns={report['missing_columns']}")
    print(f"extra_columns={report['extra_columns']}")
    print(f"admin_rows={report['admin_rows']}")

    if not args.apply:
        j_conn = _connect_jnd_read_only()
        b_conn = sqlite3.connect(str(BOCAI_DB))
        j_issues = {row[0] for row in j_conn.execute("SELECT issue FROM jnd28_history")}
        b_issues = {row[0] for row in b_conn.execute("SELECT issue FROM lottery_results")}
        missing_in_jnd = sorted(b_issues - j_issues)
        print(f"missing_lottery_results_in_jnd_count={len(missing_in_jnd)}")
        print(f"missing_lottery_results_in_jnd_sample={missing_in_jnd[:20]}")
        j_conn.close()
        b_conn.close()
        return 0

    _ensure_bocai_schema(BOCAI_DB)
    _ensure_jnd28_schema(JND28_DB)
    inserted_issues = _backfill_jnd28_from_lottery_results(BOCAI_DB, JND28_DB)

    print(f"repaired_missing_issues_count={len(inserted_issues)}")
    print(f"repaired_missing_issues={inserted_issues}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
