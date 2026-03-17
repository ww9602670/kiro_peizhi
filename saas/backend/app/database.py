"""SQLite 

- aiosqlite 
- WAL PRAGMA journal_mode=WAL
- 8  DDL +  + 
- 
"""
import os
from pathlib import Path

import aiosqlite

#  :memory:
DB_PATH = os.environ.get("BOCAI_DB_PATH", str(Path(__file__).parent.parent / "data" / "bocai.db"))

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
        platform_type   TEXT NOT NULL,
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
        worker_lock_token TEXT DEFAULT NULL,
        worker_lock_ts  TEXT DEFAULT NULL,
        created_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
        UNIQUE(operator_id, account_name, platform_type)
    );
    """,

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
        key_code        TEXT NOT NULL,
        odds_value      INTEGER NOT NULL,
        confirmed       INTEGER NOT NULL DEFAULT 0,
        fetched_at      TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
        confirmed_at    TEXT,
        UNIQUE(account_id, key_code)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_account_odds_account ON account_odds(account_id, confirmed);",
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
        for stmt in DDL_STATEMENTS:
            await db.execute(stmt)
        # 自动迁移：检测并添加缺失列
        await _auto_migrate(db)
        # 
        await db.execute(INSERT_DEFAULT_ADMIN)
        await db.commit()
    except Exception:
        await db.close()
        raise

    #  :memory: 
    if _shared_db is not None:
        await _shared_db.close()
    _shared_db = db
