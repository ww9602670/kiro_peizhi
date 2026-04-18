"""SQLite 

- aiosqlite 
- WAL PRAGMA journal_mode=WAL
- 8  DDL +  + 
- 
"""
import logging
from pathlib import Path

import aiosqlite

from app.config import BOCAI_DB_PATH, BOCAI_DEFAULT_ADMIN_ENABLED

logger = logging.getLogger(__name__)

#  :memory:
DB_PATH = BOCAI_DB_PATH

# 
# DDL8  +  + 
#  INTEGER INTEGER1000
# 

DDL_STATEMENTS = [
    # 1. operators
    """
    CREATE TABLE IF NOT EXISTS operators (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        username        TEXT NOT NULL UNIQUE,
        password        TEXT NOT NULL,
        role            TEXT NOT NULL DEFAULT 'operator',
        status          TEXT NOT NULL DEFAULT 'active',
        max_accounts    INTEGER NOT NULL DEFAULT 1,
        expire_date     TEXT,
        current_jti     TEXT,
        created_by      INTEGER REFERENCES operators(id),
        created_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
    );
    """,

    # 2. gambling_accounts
    """
    CREATE TABLE IF NOT EXISTS gambling_accounts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        operator_id     INTEGER NOT NULL REFERENCES operators(id),
        account_name    TEXT NOT NULL,
        password        TEXT NOT NULL,
        game_type       TEXT NOT NULL,
        platform_url    TEXT,
        status          TEXT NOT NULL DEFAULT 'inactive',
        session_token   TEXT,
        balance         INTEGER DEFAULT 0,
        login_fail_count INTEGER DEFAULT 0,
        last_login_at   TEXT,
        kill_switch     INTEGER NOT NULL DEFAULT 0,
        single_bet_limit INTEGER,
        daily_limit     INTEGER,
        period_limit    INTEGER,
        created_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
        UNIQUE(operator_id, account_name, game_type)
    );
    """,

    # 2.1 account_platform_sessions
    """
    CREATE TABLE IF NOT EXISTS account_platform_sessions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id      INTEGER NOT NULL REFERENCES gambling_accounts(id) ON DELETE CASCADE,
        platform_type   TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'inactive',
        session_token   TEXT,
        login_fail_count INTEGER DEFAULT 0,
        last_login_at   TEXT,
        worker_lock_token TEXT DEFAULT NULL,
        worker_lock_ts  TEXT DEFAULT NULL,
        created_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
        UNIQUE(account_id, platform_type)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_account_platform_sessions_account ON account_platform_sessions(account_id, platform_type);",

    # 2.2 account_verification_runs
    """
    CREATE TABLE IF NOT EXISTS account_verification_runs (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id              INTEGER NOT NULL REFERENCES gambling_accounts(id) ON DELETE CASCADE,
        run_status              TEXT NOT NULL DEFAULT 'running',
        snapshot_game_type      TEXT NOT NULL,
        snapshot_platform_url   TEXT,
        snapshot_password_hash  TEXT NOT NULL,
        stale                   INTEGER NOT NULL DEFAULT 0,
        stale_reason            TEXT,
        started_at              TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
        finished_at             TEXT,
        created_at              TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
        updated_at              TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_verification_runs_account_started ON account_verification_runs(account_id, started_at DESC);",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_verification_runs_running ON account_verification_runs(account_id) WHERE run_status='running';",
    "CREATE INDEX IF NOT EXISTS idx_verification_runs_account_status_stale ON account_verification_runs(account_id, run_status, stale);",

    # 2.3 account_platform_capabilities
    """
    CREATE TABLE IF NOT EXISTS account_platform_capabilities (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        verification_run_id     INTEGER NOT NULL REFERENCES account_verification_runs(id) ON DELETE CASCADE,
        platform_type           TEXT NOT NULL,
        verify_status           TEXT NOT NULL DEFAULT 'unknown',
        market_state            TEXT NOT NULL DEFAULT 'unknown',
        detected_issue          TEXT,
        last_verified_at        TEXT,
        odds_synced             INTEGER NOT NULL DEFAULT 0,
        odds_count              INTEGER NOT NULL DEFAULT 0,
        odds_message            TEXT,
        last_error              TEXT,
        created_at              TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
        updated_at              TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
        UNIQUE(verification_run_id, platform_type)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_capabilities_run ON account_platform_capabilities(verification_run_id, platform_type);",

    # 3. strategies
    """
    CREATE TABLE IF NOT EXISTS strategies (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        operator_id     INTEGER NOT NULL REFERENCES operators(id),
        account_id      INTEGER NOT NULL REFERENCES gambling_accounts(id),
        name            TEXT NOT NULL,
        type            TEXT NOT NULL,
        play_code       TEXT NOT NULL,
        base_amount     INTEGER NOT NULL,
        martin_sequence TEXT,
        bet_timing      INTEGER NOT NULL DEFAULT 30,
        simulation      INTEGER NOT NULL DEFAULT 0,
        status          TEXT NOT NULL DEFAULT 'stopped',
        martin_level    INTEGER NOT NULL DEFAULT 0,
        stop_loss       INTEGER,
        take_profit     INTEGER,
        daily_pnl       INTEGER NOT NULL DEFAULT 0,
        total_pnl       INTEGER NOT NULL DEFAULT 0,
        daily_pnl_date  TEXT,
        gate_window_issues INTEGER,
        platform_type   TEXT NOT NULL DEFAULT 'JND28WEB',
        created_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
    );
    """,

    # 4. bet_orders
    """
    CREATE TABLE IF NOT EXISTS bet_orders (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        idempotent_id   TEXT NOT NULL UNIQUE,
        operator_id     INTEGER NOT NULL REFERENCES operators(id),
        account_id      INTEGER NOT NULL REFERENCES gambling_accounts(id),
        strategy_id     INTEGER NOT NULL REFERENCES strategies(id),
        actual_platform_type TEXT,
        issue           TEXT NOT NULL,
        key_code        TEXT NOT NULL,
        amount          INTEGER NOT NULL,
        odds            INTEGER,
        status          TEXT NOT NULL DEFAULT 'pending',
        bet_response    TEXT,
        open_result     TEXT,
        sum_value       INTEGER,
        is_win          INTEGER,
        pnl             INTEGER,
        simulation      INTEGER NOT NULL DEFAULT 0,
        martin_level    INTEGER,
        bet_at          TEXT,
        settled_at      TEXT,
        fail_reason     TEXT,
        match_source    TEXT DEFAULT NULL,
        pending_match_count INTEGER DEFAULT 0,
        created_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_bet_orders_issue ON bet_orders(issue);",
    "CREATE INDEX IF NOT EXISTS idx_bet_orders_account ON bet_orders(account_id, issue);",
    "CREATE INDEX IF NOT EXISTS idx_bet_orders_strategy ON bet_orders(strategy_id, created_at);",

    # 
    """
    CREATE TRIGGER IF NOT EXISTS trg_bet_orders_terminal_state
    BEFORE UPDATE ON bet_orders
    WHEN OLD.status IN ('bet_failed', 'settled', 'reconcile_error')
    BEGIN
        SELECT RAISE(ABORT, '');
    END;
    """,

    # 5. alerts
    """
    CREATE TABLE IF NOT EXISTS alerts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        operator_id     INTEGER NOT NULL REFERENCES operators(id),
        type            TEXT NOT NULL,
        level           TEXT NOT NULL DEFAULT 'warning',
        title           TEXT NOT NULL,
        detail          TEXT,
        is_read         INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_alerts_operator ON alerts(operator_id, is_read, created_at);",

    # 6. audit_logs
    """
    CREATE TABLE IF NOT EXISTS audit_logs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        operator_id     INTEGER REFERENCES operators(id),
        action          TEXT NOT NULL,
        target_type     TEXT,
        target_id       INTEGER,
        detail          TEXT,
        ip_address      TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_operator ON audit_logs(operator_id, created_at);",

    # 7. lottery_results
    """
    CREATE TABLE IF NOT EXISTS lottery_results (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        issue           TEXT NOT NULL UNIQUE,
        open_result     TEXT NOT NULL,
        sum_value       INTEGER NOT NULL,
        open_time       TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
    );
    """,

    # 8. reconcile_records
    """
    CREATE TABLE IF NOT EXISTS reconcile_records (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id      INTEGER NOT NULL REFERENCES gambling_accounts(id),
        issue           TEXT NOT NULL,
        local_bet_count INTEGER NOT NULL,
        platform_bet_count INTEGER,
        local_balance   INTEGER,
        platform_balance INTEGER,
        diff_amount     INTEGER,
        status          TEXT NOT NULL DEFAULT 'pending',
        detail          TEXT,
        resolved_by     TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_reconcile_account ON reconcile_records(account_id, issue);",

    # 9. bet_order_platform_records (审计平台 Topbetlist 原始记录)
    """
    CREATE TABLE IF NOT EXISTS bet_order_platform_records (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        issue           TEXT NOT NULL,
        key_code        TEXT NOT NULL,
        amount          INTEGER NOT NULL,
        win_amount      INTEGER NOT NULL,
        raw_json        TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_platform_records_issue ON bet_order_platform_records(issue);",

    # 10. account_odds
    """
    CREATE TABLE IF NOT EXISTS account_odds (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id      INTEGER NOT NULL REFERENCES gambling_accounts(id) ON DELETE CASCADE,
        platform_type   TEXT NOT NULL DEFAULT 'JND28WEB',
        key_code        TEXT NOT NULL,
        odds_value      INTEGER NOT NULL,
        confirmed       INTEGER NOT NULL DEFAULT 0,
        fetched_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
        confirmed_at    TEXT,
        UNIQUE(account_id, platform_type, key_code)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_account_odds_account ON account_odds(account_id, platform_type, confirmed);",

    # 11. backtest_tasks (回测任务)
    """
    CREATE TABLE IF NOT EXISTS backtest_tasks (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        operator_id     INTEGER NOT NULL REFERENCES operators(id),
        strategy_type   TEXT NOT NULL,
        key_codes       TEXT NOT NULL,
        base_amount     INTEGER NOT NULL,
        martin_sequence TEXT,
        odds_map        TEXT NOT NULL,
        start_issue     TEXT NOT NULL,
        end_issue       TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending',
        error_message   TEXT,
        result_json     TEXT,
        total_issues    INTEGER DEFAULT 0,
        processed_issues INTEGER DEFAULT 0,
        created_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
        completed_at    TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_backtest_tasks_operator ON backtest_tasks(operator_id, created_at);",
]


#  SQL
INSERT_DEFAULT_ADMIN = """
    INSERT OR IGNORE INTO operators (username, password, role, status)
    VALUES ('admin', 'admin123', 'admin', 'active');
"""


async def get_db(db_path: str | None = None) -> aiosqlite.Connection:
    """ WAL """
    path = db_path or DB_PATH
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


#   
_shared_db: aiosqlite.Connection | None = None


async def get_shared_db() -> aiosqlite.Connection:
    """

     FastAPI  :memory: 
    
    """
    global _shared_db
    if _shared_db is None:
        _shared_db = await get_db()
    return _shared_db


async def close_shared_db() -> None:
    """"""
    global _shared_db
    if _shared_db is not None:
        await _shared_db.close()
        _shared_db = None


async def _reset_legacy_platform_binding_schema(db: aiosqlite.Connection) -> None:
    """Drop legacy tables when the account schema still binds platform_type.

    The current workspace only contains disposable test data, so a one-time
    destructive reset is acceptable and avoids carrying forward the broken
    account-platform coupling.
    """
    rows = await (await db.execute("PRAGMA table_info(gambling_accounts)")).fetchall()
    if not rows:
        return

    existing_cols = {row[1] for row in rows}
    if "game_type" in existing_cols and "platform_type" not in existing_cols:
        return

    logger.warning("Detected legacy account schema; resetting platform-coupled tables")
    for table_name in (
        "account_platform_sessions",
        "bet_orders",
        "strategies",
        "reconcile_records",
        "account_platform_capabilities",
        "account_verification_runs",
        "account_odds",
        "gambling_accounts",
    ):
        await db.execute(f"DROP TABLE IF EXISTS {table_name}")
    await db.commit()


async def _auto_migrate(db: aiosqlite.Connection) -> None:
    """检测已有表的缺失列，自动执行 ALTER TABLE ADD COLUMN。

    原理：解析 DDL_STATEMENTS 中的 CREATE TABLE 语句，提取列名，
    与 PRAGMA table_info 返回的实际列对比，缺失的列自动添加。
    """
    import re
    import logging
    logger = logging.getLogger(__name__)

    # 从 DDL 中提取每张表的列定义
    create_re = re.compile(
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\((.*)\)",
        re.IGNORECASE | re.DOTALL,
    )
    for stmt in DDL_STATEMENTS:
        m = create_re.search(stmt)
        if not m:
            continue
        table_name = m.group(1)
        body = m.group(2)

        # 解析列定义（跳过约束行如 UNIQUE/FOREIGN KEY 等）
        ddl_columns: dict[str, str] = {}
        for line in body.split("\n"):
            line = line.strip().rstrip(",")
            if not line:
                continue
            # 跳过表级约束
            upper = line.upper()
            if any(upper.startswith(kw) for kw in (
                "UNIQUE", "FOREIGN", "PRIMARY", "CHECK", "CONSTRAINT",
            )):
                continue
            parts = line.split()
            if len(parts) >= 2:
                col_name = parts[0]
                # 跳过不是合法列名的行
                if col_name.upper() in ("CREATE", "TABLE", "IF", "NOT", "EXISTS"):
                    continue
                ddl_columns[col_name] = line

        # 获取实际列
        rows = await (await db.execute(f"PRAGMA table_info({table_name})")).fetchall()
        existing_cols = {row[1] for row in rows}  # row[1] = column name

        # 添加缺失列
        for col_name, col_def in ddl_columns.items():
            if col_name not in existing_cols:
                # 构造 ALTER TABLE 语句：去掉列名前面的部分，保留类型和约束
                alter_sql = f"ALTER TABLE {table_name} ADD COLUMN {col_def}"
                logger.info("自动迁移: %s", alter_sql)
                try:
                    await db.execute(alter_sql)
                except Exception as e:
                    logger.warning("自动迁移失败 (%s.%s): %s", table_name, col_name, e)

    await db.commit()


async def init_db(db_path: str | None = None) -> None:
    """ +  +  + 自动迁移"""
    global _shared_db
    path = db_path or DB_PATH

    # :memory: 
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    db = await get_db(path)
    try:
        await _reset_legacy_platform_binding_schema(db)
        for stmt in DDL_STATEMENTS:
            await db.execute(stmt)
        # 自动迁移：检测并添加缺失列
        await _auto_migrate(db)
        # 
        if BOCAI_DEFAULT_ADMIN_ENABLED:
            await db.execute(INSERT_DEFAULT_ADMIN)
        else:
            logger.info("Skip default admin bootstrap in current environment")
        await db.commit()
    except Exception:
        await db.close()
        raise

    #  :memory: 
    if _shared_db is not None:
        await _shared_db.close()
    _shared_db = db
