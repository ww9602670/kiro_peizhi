"""CRUD

/ db (aiosqlite.Connection) 
 lottery_results operator_id 
 aiosqlite db WriteQueue
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import aiosqlite


# 
# helpers
# 

def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _rows_to_list(rows: list[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def _now() -> str:
    """返回北京时间（UTC+8）字符串"""
    from datetime import timezone, timedelta
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


# 
# 1. operators CRUD
# 

async def operator_create(
    db: aiosqlite.Connection,
    *,
    username: str,
    password: str,
    role: str = "operator",
    status: str = "active",
    max_accounts: int = 1,
    expire_date: str | None = None,
    created_by: int | None = None,
) -> dict[str, Any]:
    now = _now()
    cursor = await db.execute(
        """INSERT INTO operators
           (username, password, role, status, max_accounts, expire_date, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (username, password, role, status, max_accounts, expire_date, created_by, now, now),
    )
    await db.commit()
    return _row_to_dict(await (await db.execute("SELECT * FROM operators WHERE id=?", (cursor.lastrowid,))).fetchone())  # type: ignore


async def operator_get_by_id(db: aiosqlite.Connection, *, operator_id: int) -> dict[str, Any] | None:
    row = await (await db.execute("SELECT * FROM operators WHERE id=?", (operator_id,))).fetchone()
    return _row_to_dict(row)


async def operator_get_by_username(db: aiosqlite.Connection, *, username: str) -> dict[str, Any] | None:
    row = await (await db.execute("SELECT * FROM operators WHERE username=?", (username,))).fetchone()
    return _row_to_dict(row)


async def operator_list_all(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    rows = await (await db.execute("SELECT * FROM operators ORDER BY id")).fetchall()
    return _rows_to_list(rows)


async def operator_list_paged(
    db: aiosqlite.Connection,
    *,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """ (items, total)"""
    count_row = await (await db.execute(
        "SELECT COUNT(*) as cnt FROM operators"
    )).fetchone()
    total = count_row["cnt"] if count_row else 0

    offset = (page - 1) * page_size
    rows = await (await db.execute(
        "SELECT * FROM operators ORDER BY id LIMIT ? OFFSET ?",
        (page_size, offset),
    )).fetchall()
    return _rows_to_list(rows), total


async def operator_update(
    db: aiosqlite.Connection,
    *,
    operator_id: int,
    **fields: Any,
) -> dict[str, Any] | None:
    if not fields:
        return await operator_get_by_id(db, operator_id=operator_id)
    allowed = {"password", "max_accounts", "expire_date", "current_jti", "status"}
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if not filtered:
        return await operator_get_by_id(db, operator_id=operator_id)
    filtered["updated_at"] = _now()
    set_clause = ", ".join(f"{k}=?" for k in filtered)
    values = list(filtered.values()) + [operator_id]
    await db.execute(f"UPDATE operators SET {set_clause} WHERE id=?", tuple(values))
    await db.commit()
    return await operator_get_by_id(db, operator_id=operator_id)


async def operator_update_status(
    db: aiosqlite.Connection,
    *,
    operator_id: int,
    status: str,
) -> dict[str, Any] | None:
    return await operator_update(db, operator_id=operator_id, status=status)


# 
# 2. gambling_accounts CRUD
# 

async def account_create(
    db: aiosqlite.Connection,
    *,
    operator_id: int,
    account_name: str,
    password: str,
    platform_type: str,
    platform_url: str | None = None,
) -> dict[str, Any]:
    now = _now()
    cursor = await db.execute(
        """INSERT INTO gambling_accounts
           (operator_id, account_name, password, platform_type, platform_url, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (operator_id, account_name, password, platform_type, platform_url, now, now),
    )
    await db.commit()
    row = await (await db.execute("SELECT * FROM gambling_accounts WHERE id=?", (cursor.lastrowid,))).fetchone()
    return _row_to_dict(row)  # type: ignore


async def account_get_by_id(
    db: aiosqlite.Connection, *, account_id: int, operator_id: int
) -> dict[str, Any] | None:
    row = await (await db.execute(
        "SELECT * FROM gambling_accounts WHERE id=? AND operator_id=?",
        (account_id, operator_id),
    )).fetchone()
    return _row_to_dict(row)


async def account_list_by_operator(
    db: aiosqlite.Connection, *, operator_id: int
) -> list[dict[str, Any]]:
    rows = await (await db.execute(
        "SELECT * FROM gambling_accounts WHERE operator_id=? ORDER BY id",
        (operator_id,),
    )).fetchall()
    return _rows_to_list(rows)


async def account_update(
    db: aiosqlite.Connection,
    *,
    account_id: int,
    operator_id: int,
    **fields: Any,
) -> dict[str, Any] | None:
    if not fields:
        return await account_get_by_id(db, account_id=account_id, operator_id=operator_id)
    allowed = {
        "password", "status", "session_token", "balance",
        "login_fail_count", "last_login_at", "kill_switch",
        "single_bet_limit", "daily_limit", "period_limit",
    }
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if not filtered:
        return await account_get_by_id(db, account_id=account_id, operator_id=operator_id)
    filtered["updated_at"] = _now()
    set_clause = ", ".join(f"{k}=?" for k in filtered)
    values = list(filtered.values()) + [account_id, operator_id]
    await db.execute(
        f"UPDATE gambling_accounts SET {set_clause} WHERE id=? AND operator_id=?",
        tuple(values),
    )
    await db.commit()
    return await account_get_by_id(db, account_id=account_id, operator_id=operator_id)


async def account_delete(
    db: aiosqlite.Connection, *, account_id: int, operator_id: int
) -> bool:
    cursor = await db.execute(
        "DELETE FROM gambling_accounts WHERE id=? AND operator_id=?",
        (account_id, operator_id),
    )
    await db.commit()
    return cursor.rowcount > 0


# 
# 3. strategies CRUD
# 

async def strategy_create(
    db: aiosqlite.Connection,
    *,
    operator_id: int,
    account_id: int,
    name: str,
    type: str,
    play_code: str,
    base_amount: int,
    martin_sequence: str | None = None,
    bet_timing: int = 30,
    simulation: int = 0,
    stop_loss: int | None = None,
    take_profit: int | None = None,
) -> dict[str, Any]:
    now = _now()
    cursor = await db.execute(
        """INSERT INTO strategies
           (operator_id, account_id, name, type, play_code, base_amount,
            martin_sequence, bet_timing, simulation, stop_loss, take_profit,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (operator_id, account_id, name, type, play_code, base_amount,
         martin_sequence, bet_timing, simulation, stop_loss, take_profit, now, now),
    )
    await db.commit()
    row = await (await db.execute("SELECT * FROM strategies WHERE id=?", (cursor.lastrowid,))).fetchone()
    return _row_to_dict(row)  # type: ignore


async def strategy_get_by_id(
    db: aiosqlite.Connection, *, strategy_id: int, operator_id: int
) -> dict[str, Any] | None:
    row = await (await db.execute(
        "SELECT * FROM strategies WHERE id=? AND operator_id=?",
        (strategy_id, operator_id),
    )).fetchone()
    return _row_to_dict(row)


async def strategy_list_by_operator(
    db: aiosqlite.Connection, *, operator_id: int
) -> list[dict[str, Any]]:
    rows = await (await db.execute(
        "SELECT * FROM strategies WHERE operator_id=? ORDER BY id",
        (operator_id,),
    )).fetchall()
    return _rows_to_list(rows)


async def strategy_update(
    db: aiosqlite.Connection,
    *,
    strategy_id: int,
    operator_id: int,
    **fields: Any,
) -> dict[str, Any] | None:
    if not fields:
        return await strategy_get_by_id(db, strategy_id=strategy_id, operator_id=operator_id)
    allowed = {
        "name", "play_code", "base_amount", "martin_sequence",
        "bet_timing", "simulation", "status", "martin_level",
        "stop_loss", "take_profit", "daily_pnl", "total_pnl", "daily_pnl_date",
    }
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if not filtered:
        return await strategy_get_by_id(db, strategy_id=strategy_id, operator_id=operator_id)
    filtered["updated_at"] = _now()
    set_clause = ", ".join(f"{k}=?" for k in filtered)
    values = list(filtered.values()) + [strategy_id, operator_id]
    await db.execute(
        f"UPDATE strategies SET {set_clause} WHERE id=? AND operator_id=?",
        tuple(values),
    )
    await db.commit()
    return await strategy_get_by_id(db, strategy_id=strategy_id, operator_id=operator_id)


async def strategy_delete(
    db: aiosqlite.Connection, *, strategy_id: int, operator_id: int, force: bool = False
) -> bool:
    """删除策略。

    force=False（默认）：如果存在关联 bet_orders，抛出 ValueError 提示用户。
    force=True：级联删除关联的 bet_orders 后再删除策略。
    """
    # 检查是否有关联的 bet_orders
    count_row = await (await db.execute(
        "SELECT COUNT(*) as cnt FROM bet_orders WHERE strategy_id=? AND operator_id=?",
        (strategy_id, operator_id),
    )).fetchone()
    bet_count = count_row["cnt"] if count_row else 0

    if bet_count > 0 and not force:
        raise ValueError(f"该策略关联了 {bet_count} 条投注记录，请使用强制删除或先清理投注记录")

    if bet_count > 0 and force:
        await db.execute(
            "DELETE FROM bet_orders WHERE strategy_id=? AND operator_id=?",
            (strategy_id, operator_id),
        )

    cursor = await db.execute(
        "DELETE FROM strategies WHERE id=? AND operator_id=?",
        (strategy_id, operator_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def strategy_update_status(
    db: aiosqlite.Connection,
    *,
    strategy_id: int,
    operator_id: int,
    status: str,
) -> dict[str, Any] | None:
    return await strategy_update(db, strategy_id=strategy_id, operator_id=operator_id, status=status)


async def strategy_update_pnl(
    db: aiosqlite.Connection,
    *,
    strategy_id: int,
    operator_id: int,
    daily_pnl: int,
    total_pnl: int,
    daily_pnl_date: str | None = None,
) -> dict[str, Any] | None:
    return await strategy_update(
        db, strategy_id=strategy_id, operator_id=operator_id,
        daily_pnl=daily_pnl, total_pnl=total_pnl, daily_pnl_date=daily_pnl_date,
    )


# 
# 4. bet_orders CRUD
# 

async def bet_order_create(
    db: aiosqlite.Connection,
    *,
    idempotent_id: str,
    operator_id: int,
    account_id: int,
    strategy_id: int,
    issue: str,
    key_code: str,
    amount: int,
    odds: int | None = None,
    status: str = "pending",
    simulation: int = 0,
    martin_level: int | None = None,
) -> dict[str, Any]:
    """idempotent_id  UNIQUE  IntegrityError"""
    now = _now()
    cursor = await db.execute(
        """INSERT INTO bet_orders
           (idempotent_id, operator_id, account_id, strategy_id, issue,
            key_code, amount, odds, status, simulation, martin_level, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (idempotent_id, operator_id, account_id, strategy_id, issue,
         key_code, amount, odds, status, simulation, martin_level, now),
    )
    await db.commit()
    row = await (await db.execute("SELECT * FROM bet_orders WHERE id=?", (cursor.lastrowid,))).fetchone()
    return _row_to_dict(row)  # type: ignore


async def bet_order_get_by_id(
    db: aiosqlite.Connection, *, order_id: int, operator_id: int
) -> dict[str, Any] | None:
    row = await (await db.execute(
        "SELECT * FROM bet_orders WHERE id=? AND operator_id=?",
        (order_id, operator_id),
    )).fetchone()
    return _row_to_dict(row)


async def bet_order_list_by_operator(
    db: aiosqlite.Connection,
    *,
    operator_id: int,
    page: int = 1,
    page_size: int = 20,
    date_from: str | None = None,
    date_to: str | None = None,
    strategy_id: int | None = None,
    status: str | None = None,
    account_id: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """ (items, total)"""
    conditions = ["operator_id=?"]
    params: list[Any] = [operator_id]

    if date_from:
        conditions.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("created_at <= ?")
        params.append(date_to + " 23:59:59")
    if strategy_id is not None:
        conditions.append("strategy_id=?")
        params.append(strategy_id)
    if status == "settled":
        conditions.append("status='settled'")
    elif status == "pending":
        conditions.append("status IN ('bet_success','settling','pending_match')")
    if account_id is not None:
        conditions.append("account_id=?")
        params.append(account_id)

    where = " AND ".join(conditions)

    # total count
    count_row = await (await db.execute(
        f"SELECT COUNT(*) as cnt FROM bet_orders WHERE {where}", tuple(params)
    )).fetchone()
    total = count_row["cnt"] if count_row else 0

    # paginated results
    offset = (page - 1) * page_size
    rows = await (await db.execute(
        f"SELECT * FROM bet_orders WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        tuple(params) + (page_size, offset),
    )).fetchall()

    return _rows_to_list(rows), total


async def bet_order_summary_by_operator(
    db: aiosqlite.Connection,
    *,
    operator_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
    strategy_id: int | None = None,
    status: str | None = None,
    account_id: int | None = None,
) -> dict[str, Any]:
    """返回筛选条件下的汇总统计：total_amount, total_payout"""
    conditions = ["operator_id=?"]
    params: list[Any] = [operator_id]

    if date_from:
        conditions.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("created_at <= ?")
        params.append(date_to + " 23:59:59")
    if strategy_id is not None:
        conditions.append("strategy_id=?")
        params.append(strategy_id)
    if status == "settled":
        conditions.append("status='settled'")
    elif status == "pending":
        conditions.append("status IN ('bet_success','settling','pending_match')")
    if account_id is not None:
        conditions.append("account_id=?")
        params.append(account_id)

    where = " AND ".join(conditions)

    row = await (await db.execute(
        f"""SELECT COALESCE(SUM(amount), 0) as total_amount,
                   COALESCE(SUM(CASE WHEN status='settled' AND pnl IS NOT NULL
                                     THEN amount + pnl ELSE 0 END), 0) as total_payout
            FROM bet_orders WHERE {where}""",
        tuple(params),
    )).fetchone()

    return {
        "total_amount": (row["total_amount"] if row else 0) / 100,
        "total_payout": (row["total_payout"] if row else 0) / 100,
    }


async def bet_order_list_pending_by_operator(
    db: aiosqlite.Connection,
    *,
    operator_id: int,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """查询待结算投注（JOIN 策略名+账户名），最多 limit 条"""
    rows = await (await db.execute(
        """SELECT b.*, s.name AS strategy_name, a.account_name AS account_name
           FROM bet_orders b
           JOIN strategies s ON b.strategy_id = s.id
           JOIN gambling_accounts a ON b.account_id = a.id
           WHERE b.operator_id=?
             AND b.status IN ('bet_success','settling','pending_match')
           ORDER BY b.created_at DESC
           LIMIT ?""",
        (operator_id, limit),
    )).fetchall()
    return _rows_to_list(rows)


async def bet_order_update_status(
    db: aiosqlite.Connection,
    *,
    order_id: int,
    operator_id: int,
    status: str,
    **extra_fields: Any,
) -> dict[str, Any] | None:
    """ DB """
    allowed_extra = {
        "bet_response", "open_result", "sum_value", "is_win",
        "pnl", "bet_at", "settled_at", "fail_reason", "odds",
        "match_source", "pending_match_count",
    }
    updates = {"status": status}
    for k, v in extra_fields.items():
        if k in allowed_extra:
            updates[k] = v

    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [order_id, operator_id]
    await db.execute(
        f"UPDATE bet_orders SET {set_clause} WHERE id=? AND operator_id=?",
        tuple(values),
    )
    await db.commit()
    return await bet_order_get_by_id(db, order_id=order_id, operator_id=operator_id)


# 
# 5. alerts CRUD
# 

async def alert_create(
    db: aiosqlite.Connection,
    *,
    operator_id: int,
    type: str,
    level: str = "warning",
    title: str,
    detail: str | None = None,
) -> dict[str, Any]:
    now = _now()
    cursor = await db.execute(
        """INSERT INTO alerts (operator_id, type, level, title, detail, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (operator_id, type, level, title, detail, now),
    )
    await db.commit()
    row = await (await db.execute("SELECT * FROM alerts WHERE id=?", (cursor.lastrowid,))).fetchone()
    return _row_to_dict(row)  # type: ignore


async def alert_list_by_operator(
    db: aiosqlite.Connection,
    *,
    operator_id: int,
    is_read: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    conditions = ["operator_id=?"]
    params: list[Any] = [operator_id]
    if is_read is not None:
        conditions.append("is_read=?")
        params.append(is_read)

    where = " AND ".join(conditions)

    count_row = await (await db.execute(
        f"SELECT COUNT(*) as cnt FROM alerts WHERE {where}", tuple(params)
    )).fetchone()
    total = count_row["cnt"] if count_row else 0

    offset = (page - 1) * page_size
    rows = await (await db.execute(
        f"SELECT * FROM alerts WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        tuple(params) + (page_size, offset),
    )).fetchall()
    return _rows_to_list(rows), total


async def alert_mark_read(
    db: aiosqlite.Connection, *, alert_id: int, operator_id: int
) -> bool:
    cursor = await db.execute(
        "UPDATE alerts SET is_read=1 WHERE id=? AND operator_id=?",
        (alert_id, operator_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def alert_mark_all_read(
    db: aiosqlite.Connection, *, operator_id: int
) -> int:
    cursor = await db.execute(
        "UPDATE alerts SET is_read=1 WHERE operator_id=? AND is_read=0",
        (operator_id,),
    )
    await db.commit()
    return cursor.rowcount


async def alert_get_unread_count(
    db: aiosqlite.Connection, *, operator_id: int
) -> int:
    row = await (await db.execute(
        "SELECT COUNT(*) as cnt FROM alerts WHERE operator_id=? AND is_read=0",
        (operator_id,),
    )).fetchone()
    return row["cnt"] if row else 0


# 
# 6. audit_logs CRUD
# 

async def audit_log_create(
    db: aiosqlite.Connection,
    *,
    operator_id: int,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    detail: str | None = None,
    ip_address: str | None = None,
) -> dict[str, Any]:
    now = _now()
    cursor = await db.execute(
        """INSERT INTO audit_logs
           (operator_id, action, target_type, target_id, detail, ip_address, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (operator_id, action, target_type, target_id, detail, ip_address, now),
    )
    await db.commit()
    row = await (await db.execute("SELECT * FROM audit_logs WHERE id=?", (cursor.lastrowid,))).fetchone()
    return _row_to_dict(row)  # type: ignore


async def audit_log_list_by_operator(
    db: aiosqlite.Connection,
    *,
    operator_id: int,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    count_row = await (await db.execute(
        "SELECT COUNT(*) as cnt FROM audit_logs WHERE operator_id=?", (operator_id,)
    )).fetchone()
    total = count_row["cnt"] if count_row else 0

    offset = (page - 1) * page_size
    rows = await (await db.execute(
        "SELECT * FROM audit_logs WHERE operator_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (operator_id, page_size, offset),
    )).fetchall()
    return _rows_to_list(rows), total


# 
# 7. lottery_results CRUD operator_id 
# 

async def lottery_result_get_by_issue(
    db: aiosqlite.Connection, *, issue: str
) -> dict[str, Any] | None:
    row = await (await db.execute(
        "SELECT * FROM lottery_results WHERE issue=?", (issue,)
    )).fetchone()
    return _row_to_dict(row)


async def lottery_result_list_recent(
    db: aiosqlite.Connection, *, limit: int = 20
) -> list[dict[str, Any]]:
    rows = await (await db.execute(
        "SELECT * FROM lottery_results ORDER BY id DESC LIMIT ?", (limit,)
    )).fetchall()
    return _rows_to_list(rows)


async def lottery_result_save(
    db: aiosqlite.Connection,
    *,
    issue: str,
    open_result: str,
    sum_value: int,
    open_time: str | None = None,
) -> dict[str, Any]:
    """ API"""
    now = _now()
    await db.execute(
        """INSERT OR IGNORE INTO lottery_results
           (issue, open_result, sum_value, open_time, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (issue, open_result, sum_value, open_time, now),
    )
    await db.commit()
    row = await (await db.execute("SELECT * FROM lottery_results WHERE issue=?", (issue,))).fetchone()
    return _row_to_dict(row)  # type: ignore


# 
# 8. reconcile_records CRUD account_idoperator_id 
# 

async def reconcile_record_create(
    db: aiosqlite.Connection,
    *,
    operator_id: int,
    account_id: int,
    issue: str,
    local_bet_count: int,
    platform_bet_count: int | None = None,
    local_balance: int | None = None,
    platform_balance: int | None = None,
    diff_amount: int | None = None,
    status: str = "pending",
    detail: str | None = None,
) -> dict[str, Any]:
    """ JOIN  account_id  operator_id"""
    #  account 
    owner = await (await db.execute(
        "SELECT operator_id FROM gambling_accounts WHERE id=? AND operator_id=?",
        (account_id, operator_id),
    )).fetchone()
    if owner is None:
        raise ValueError(f"account_id={account_id}  operator_id={operator_id}")

    now = _now()
    cursor = await db.execute(
        """INSERT INTO reconcile_records
           (account_id, issue, local_bet_count, platform_bet_count,
            local_balance, platform_balance, diff_amount, status, detail, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (account_id, issue, local_bet_count, platform_bet_count,
         local_balance, platform_balance, diff_amount, status, detail, now),
    )
    await db.commit()
    row = await (await db.execute("SELECT * FROM reconcile_records WHERE id=?", (cursor.lastrowid,))).fetchone()
    return _row_to_dict(row)  # type: ignore


async def reconcile_record_list_by_account(
    db: aiosqlite.Connection,
    *,
    account_id: int,
    operator_id: int,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """ JOIN gambling_accounts  operator_id """
    count_row = await (await db.execute(
        """SELECT COUNT(*) as cnt FROM reconcile_records r
           JOIN gambling_accounts a ON r.account_id = a.id
           WHERE r.account_id=? AND a.operator_id=?""",
        (account_id, operator_id),
    )).fetchone()
    total = count_row["cnt"] if count_row else 0

    offset = (page - 1) * page_size
    rows = await (await db.execute(
        """SELECT r.* FROM reconcile_records r
           JOIN gambling_accounts a ON r.account_id = a.id
           WHERE r.account_id=? AND a.operator_id=?
           ORDER BY r.created_at DESC LIMIT ? OFFSET ?""",
        (account_id, operator_id, page_size, offset),
    )).fetchall()
    return _rows_to_list(rows), total


# ──────────────────────────────────────────────
# 9. account_odds CRUD
# ──────────────────────────────────────────────

async def odds_batch_upsert(
    db: aiosqlite.Connection,
    *,
    account_id: int,
    odds_map: dict[str, int],
    confirmed: bool = False,
) -> None:
    """批量写入赔率，INSERT OR REPLACE 语义，事务内执行。

    空 odds_map 时为 no-op。
    """
    if not odds_map:
        return

    confirmed_int = 1 if confirmed else 0
    confirmed_at_expr = "datetime('now', '+8 hours')" if confirmed else "NULL"

    for key_code, odds_value in odds_map.items():
        await db.execute(
            f"""INSERT OR REPLACE INTO account_odds
               (account_id, key_code, odds_value, confirmed, fetched_at, confirmed_at)
               VALUES (?, ?, ?, ?, datetime('now', '+8 hours'), {confirmed_at_expr})""",
            (account_id, key_code, odds_value, confirmed_int),
        )
    await db.commit()


async def odds_list_by_account(
    db: aiosqlite.Connection,
    *,
    account_id: int,
) -> list[dict[str, Any]]:
    """获取账号所有赔率记录，按 key_code 字母序排列。"""
    rows = await (await db.execute(
        "SELECT * FROM account_odds WHERE account_id=? ORDER BY key_code ASC",
        (account_id,),
    )).fetchall()
    return _rows_to_list(rows)


async def odds_get_confirmed_map(
    db: aiosqlite.Connection,
    *,
    account_id: int,
) -> dict[str, int] | None:
    """获取已确认赔率 dict。无记录→None；有未确认→None；全确认→{key_code: odds_value}。"""
    rows = await (await db.execute(
        "SELECT key_code, odds_value, confirmed FROM account_odds WHERE account_id=?",
        (account_id,),
    )).fetchall()

    if not rows:
        return None

    result: dict[str, int] = {}
    for r in rows:
        if r["confirmed"] == 0:
            return None
        result[r["key_code"]] = r["odds_value"]
    return result


async def odds_confirm_all(
    db: aiosqlite.Connection,
    *,
    account_id: int,
) -> int:
    """确认该账号所有未确认赔率，返回更新行数。幂等。"""
    cursor = await db.execute(
        """UPDATE account_odds SET confirmed=1, confirmed_at=datetime('now', '+8 hours')
           WHERE account_id=? AND confirmed=0""",
        (account_id,),
    )
    await db.commit()
    return cursor.rowcount


async def odds_has_records(
    db: aiosqlite.Connection,
    *,
    account_id: int,
) -> bool:
    """检查账号是否有赔率记录。"""
    row = await (await db.execute(
        "SELECT COUNT(*) as cnt FROM account_odds WHERE account_id=?",
        (account_id,),
    )).fetchone()
    return (row["cnt"] if row else 0) > 0
