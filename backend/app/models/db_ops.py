"""CRUD

/ db (aiosqlite.Connection) 
 lottery_results operator_id 
 aiosqlite db WriteQueue
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Optional

import aiosqlite

from app.utils.strategy_timing import DEFAULT_BET_TIMING


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


def _now_minus_minutes(minutes: int) -> str:
    from datetime import timezone, timedelta

    return (
        datetime.now(timezone(timedelta(hours=8))) - timedelta(minutes=minutes)
    ).strftime("%Y-%m-%d %H:%M:%S")


def _password_hash(password: str) -> str:
    return hashlib.sha256((password or "").encode("utf-8")).hexdigest()


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
    game_type: str | None = None,
    platform_type: str | None = None,
    platform_url: str | None = None,
) -> dict[str, Any]:
    from app.schemas.account import normalize_game_type

    resolved_game_type = normalize_game_type(game_type, platform_type)
    now = _now()
    cursor = await db.execute(
        """INSERT INTO gambling_accounts
           (operator_id, account_name, password, game_type, platform_url, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (operator_id, account_name, password, resolved_game_type, platform_url, now, now),
    )
    await db.commit()
    row = await (await db.execute("SELECT * FROM gambling_accounts WHERE id=?", (cursor.lastrowid,))).fetchone()
    return _row_to_dict(row)  # type: ignore


async def account_get_by_id(
    db: aiosqlite.Connection, *, account_id: int, operator_id: int
) -> dict[str, Any] | None:
    row = await (await db.execute(
        """SELECT * FROM gambling_accounts
           WHERE id=? AND operator_id=?
             AND deleted_at IS NULL
             AND status <> 'deleted'""",
        (account_id, operator_id),
    )).fetchone()
    record = _row_to_dict(row)
    if not record:
        return None
    return await _attach_account_verification_view(db, record)


async def account_list_by_operator(
    db: aiosqlite.Connection, *, operator_id: int
) -> list[dict[str, Any]]:
    rows = await (await db.execute(
        """SELECT * FROM gambling_accounts
           WHERE operator_id=?
             AND deleted_at IS NULL
             AND status <> 'deleted'
           ORDER BY id""",
        (operator_id,),
    )).fetchall()
    records = _rows_to_list(rows)
    enriched: list[dict[str, Any]] = []
    for record in records:
        enriched.append(await _attach_account_verification_view(db, record))
    return enriched


async def account_update(
    db: aiosqlite.Connection,
    *,
    account_id: int,
    operator_id: int,
    **fields: Any,
) -> dict[str, Any] | None:
    if not fields:
        return await account_get_by_id(db, account_id=account_id, operator_id=operator_id)
    existing = await account_get_by_id(db, account_id=account_id, operator_id=operator_id)
    if not existing:
        return None
    allowed = {
        "password", "status", "session_token", "balance",
        "login_fail_count", "last_login_at", "kill_switch",
        "single_bet_limit", "daily_limit", "period_limit",
        "platform_url", "game_type",
    }
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if not filtered:
        return await account_get_by_id(db, account_id=account_id, operator_id=operator_id)
    stale_watch_fields = {"password", "platform_url", "game_type"}
    should_mark_stale = any(
        key in filtered and filtered[key] != existing.get(key)
        for key in stale_watch_fields
    )
    filtered["updated_at"] = _now()
    set_clause = ", ".join(f"{k}=?" for k in filtered)
    values = list(filtered.values()) + [account_id, operator_id]
    await db.execute(
        f"UPDATE gambling_accounts SET {set_clause} WHERE id=? AND operator_id=?",
        tuple(values),
    )
    if should_mark_stale:
        await account_verification_runs_mark_stale_by_account(
            db,
            account_id=account_id,
            stale_reason="account_fields_changed",
        )
    await db.commit()
    return await account_get_by_id(db, account_id=account_id, operator_id=operator_id)


async def operator_strategy_permission_list(
    db: aiosqlite.Connection,
    *,
    operator_id: int,
) -> list[str]:
    rows = await (
        await db.execute(
            """SELECT strategy_type
               FROM operator_strategy_permissions
              WHERE operator_id=? AND enabled=1
              ORDER BY strategy_type""",
            (operator_id,),
        )
    ).fetchall()
    return [str(row["strategy_type"]) for row in rows]


async def operator_strategy_permission_set(
    db: aiosqlite.Connection,
    *,
    operator_id: int,
    strategy_types: list[str],
    created_by: int,
) -> list[str] | None:
    from app.schemas.strategy import normalize_strategy_permission_type

    operator = await (
        await db.execute(
            "SELECT id FROM operators WHERE id=? AND role='operator'",
            (operator_id,),
        )
    ).fetchone()
    if operator is None:
        return None

    normalized: list[str] = []
    for item in strategy_types:
        value = normalize_strategy_permission_type(item)
        if value not in normalized:
            normalized.append(value)

    now = _now()
    await db.execute(
        "DELETE FROM operator_strategy_permissions WHERE operator_id=?",
        (operator_id,),
    )
    for strategy_type in normalized:
        await db.execute(
            """INSERT INTO operator_strategy_permissions
               (operator_id, strategy_type, enabled, created_by, created_at, updated_at)
               VALUES (?, ?, 1, ?, ?, ?)""",
            (operator_id, strategy_type, created_by, now, now),
        )
    await db.commit()
    return normalized


async def account_delete(
    db: aiosqlite.Connection, *, account_id: int, operator_id: int
) -> bool:
    """Soft-delete an account while preserving strategy and order history."""
    row = await (
        await db.execute(
            """SELECT * FROM gambling_accounts
               WHERE id=? AND operator_id=?
                 AND deleted_at IS NULL
                 AND status <> 'deleted'""",
            (account_id, operator_id),
        )
    ).fetchone()
    if row is None:
        return False

    running = await (
        await db.execute(
            """SELECT COUNT(*) AS cnt
               FROM strategies
              WHERE account_id=? AND operator_id=?
                AND deleted_at IS NULL
                AND status='running'""",
            (account_id, operator_id),
        )
    ).fetchone()
    if running and int(running["cnt"]) > 0:
        raise ValueError("account has running strategies")

    now = _now()
    account_name = str(row["account_name"])
    archived_name = f"{account_name}#deleted#{account_id}"
    await db.execute(
        """UPDATE strategies
              SET status='deleted', deleted_at=?, updated_at=?
            WHERE account_id=? AND operator_id=?
              AND deleted_at IS NULL""",
        (now, now, account_id, operator_id),
    )
    await db.execute(
        """UPDATE account_platform_sessions
              SET status='inactive',
                  session_token=NULL,
                  worker_lock_token=NULL,
                  worker_lock_ts=NULL,
                  updated_at=?
            WHERE account_id=?""",
        (now, account_id),
    )
    cursor = await db.execute(
        """UPDATE gambling_accounts
              SET status='deleted',
                  deleted_at=?,
                  deleted_account_name=?,
                  account_name=?,
                  session_token=NULL,
                  kill_switch=1,
                  updated_at=?
            WHERE id=? AND operator_id=?
              AND deleted_at IS NULL
              AND status <> 'deleted'""",
        (now, account_name, archived_name, now, account_id, operator_id),
    )
    await db.commit()
    return cursor.rowcount > 0

    """删除账号，级联清理所有关联数据。

    删除顺序（按外键依赖）：
    bet_orders → strategies → reconcile_records → account_odds → gambling_accounts
    """
    # 1. 删除该账号下所有投注记录
    # 2. 删除该账号下所有策略
    # 3. 删除对账记录（reconcile_records 引用 gambling_accounts 但无 CASCADE）
    # 4. 删除赔率记录（account_odds 有 ON DELETE CASCADE，但显式删除更安全）
    # 5. 删除账号本身


# 
# 2.1 account_verification_runs CRUD
# 

async def account_verification_run_create(
    db: aiosqlite.Connection,
    *,
    account_id: int,
    snapshot_game_type: str,
    snapshot_platform_url: str | None,
    snapshot_password_hash: str,
    run_status: str = "running",
) -> dict[str, Any]:
    now = _now()
    cursor = await db.execute(
        """INSERT INTO account_verification_runs
           (account_id, run_status, snapshot_game_type, snapshot_platform_url,
            snapshot_password_hash, stale, started_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)""",
        (
            account_id,
            run_status,
            snapshot_game_type,
            snapshot_platform_url,
            snapshot_password_hash,
            now,
            now,
            now,
        ),
    )
    await db.commit()
    row = await (
        await db.execute(
            "SELECT * FROM account_verification_runs WHERE id=?",
            (cursor.lastrowid,),
        )
    ).fetchone()
    return _row_to_dict(row)  # type: ignore


async def account_verification_run_get_by_id(
    db: aiosqlite.Connection,
    *,
    verification_run_id: int,
) -> dict[str, Any] | None:
    row = await (
        await db.execute(
            "SELECT * FROM account_verification_runs WHERE id=?",
            (verification_run_id,),
        )
    ).fetchone()
    return _row_to_dict(row)


async def account_verification_run_get_latest(
    db: aiosqlite.Connection,
    *,
    account_id: int,
) -> dict[str, Any] | None:
    row = await (
        await db.execute(
            "SELECT * FROM account_verification_runs WHERE account_id=? ORDER BY id DESC LIMIT 1",
            (account_id,),
        )
    ).fetchone()
    return _row_to_dict(row)


async def account_verification_run_get_latest_completed(
    db: aiosqlite.Connection,
    *,
    account_id: int,
) -> dict[str, Any] | None:
    row = await (
        await db.execute(
            "SELECT * FROM account_verification_runs "
            "WHERE account_id=? AND run_status='completed' ORDER BY id DESC LIMIT 1",
            (account_id,),
        )
    ).fetchone()
    return _row_to_dict(row)


async def account_verification_run_get_effective(
    db: aiosqlite.Connection,
    *,
    account_id: int,
) -> dict[str, Any] | None:
    row = await (
        await db.execute(
            "SELECT * FROM account_verification_runs "
            "WHERE account_id=? AND run_status='completed' AND stale=0 "
            "ORDER BY id DESC LIMIT 1",
            (account_id,),
        )
    ).fetchone()
    return _row_to_dict(row)


async def account_verification_run_get_running(
    db: aiosqlite.Connection,
    *,
    account_id: int,
) -> dict[str, Any] | None:
    row = await (
        await db.execute(
            "SELECT * FROM account_verification_runs "
            "WHERE account_id=? AND run_status='running' ORDER BY id DESC LIMIT 1",
            (account_id,),
        )
    ).fetchone()
    return _row_to_dict(row)


async def account_verification_run_list_by_account(
    db: aiosqlite.Connection,
    *,
    account_id: int,
) -> list[dict[str, Any]]:
    rows = await (
        await db.execute(
            "SELECT * FROM account_verification_runs WHERE account_id=? ORDER BY id DESC",
            (account_id,),
        )
    ).fetchall()
    return _rows_to_list(rows)


async def account_verification_run_update(
    db: aiosqlite.Connection,
    *,
    verification_run_id: int,
    **fields: Any,
) -> dict[str, Any] | None:
    if not fields:
        return await account_verification_run_get_by_id(
            db, verification_run_id=verification_run_id
        )
    allowed = {
        "run_status",
        "stale",
        "stale_reason",
        "finished_at",
        "started_at",
    }
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if not filtered:
        return await account_verification_run_get_by_id(
            db, verification_run_id=verification_run_id
        )
    filtered["updated_at"] = _now()
    set_clause = ", ".join(f"{k}=?" for k in filtered)
    values = list(filtered.values()) + [verification_run_id]
    await db.execute(
        f"UPDATE account_verification_runs SET {set_clause} WHERE id=?",
        tuple(values),
    )
    await db.commit()
    return await account_verification_run_get_by_id(
        db, verification_run_id=verification_run_id
    )


async def account_verification_run_complete(
    db: aiosqlite.Connection,
    *,
    verification_run_id: int,
    capabilities: list[dict[str, Any]],
) -> dict[str, Any] | None:
    now = _now()
    await db.execute(
        "DELETE FROM account_platform_capabilities WHERE verification_run_id=?",
        (verification_run_id,),
    )
    for capability in capabilities:
        await db.execute(
            """INSERT INTO account_platform_capabilities
               (verification_run_id, platform_type, verify_status, market_state,
                detected_issue, last_verified_at, odds_synced, odds_count, odds_message,
                last_error, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                verification_run_id,
                capability.get("platform_type"),
                capability.get("verify_status", "unknown"),
                capability.get("market_state", "unknown"),
                capability.get("detected_issue"),
                capability.get("last_verified_at"),
                1 if capability.get("odds_synced") else 0,
                int(capability.get("odds_count") or 0),
                capability.get("odds_message"),
                capability.get("last_error"),
                now,
                now,
            ),
        )
    await db.execute(
        """UPDATE account_verification_runs
           SET run_status='completed', finished_at=?, updated_at=?
           WHERE id=?""",
        (now, now, verification_run_id),
    )
    await db.commit()
    return await account_verification_run_get_by_id(
        db, verification_run_id=verification_run_id
    )


async def account_verification_run_fail(
    db: aiosqlite.Connection,
    *,
    verification_run_id: int,
    run_status: str = "failed",
    stale_reason: str | None = None,
) -> dict[str, Any] | None:
    now = _now()
    update_fields: dict[str, Any] = {
        "run_status": run_status,
        "finished_at": now,
        "updated_at": now,
    }
    if stale_reason is not None:
        update_fields["stale_reason"] = stale_reason
    set_clause = ", ".join(f"{k}=?" for k in update_fields)
    values = list(update_fields.values()) + [verification_run_id]
    await db.execute(
        f"UPDATE account_verification_runs SET {set_clause} WHERE id=?",
        tuple(values),
    )
    await db.commit()
    return await account_verification_run_get_by_id(
        db, verification_run_id=verification_run_id
    )


async def account_verification_runs_mark_stale_by_account(
    db: aiosqlite.Connection,
    *,
    account_id: int,
    stale_reason: str,
) -> int:
    now = _now()
    cursor = await db.execute(
        """UPDATE account_verification_runs
           SET stale=1, stale_reason=?, updated_at=?
           WHERE account_id=? AND run_status='completed' AND stale=0""",
        (stale_reason, now, account_id),
    )
    return cursor.rowcount


async def account_verification_runs_refresh_stale(
    db: aiosqlite.Connection,
    *,
    account_id: int,
    current_game_type: str,
    current_platform_url: str | None,
    current_password: str,
    verification_ttl_minutes: int,
) -> None:
    now = _now()
    ttl_cutoff = _now_minus_minutes(verification_ttl_minutes)
    mutated = False

    ttl_cursor = await db.execute(
        """UPDATE account_verification_runs
           SET stale=1, stale_reason='ttl_expired', updated_at=?
           WHERE account_id=? AND run_status='completed' AND stale=0 AND started_at < ?""",
        (now, account_id, ttl_cutoff),
    )
    if ttl_cursor.rowcount > 0:
        mutated = True

    rows = await (
        await db.execute(
            """SELECT id, snapshot_game_type, snapshot_platform_url, snapshot_password_hash
               FROM account_verification_runs
               WHERE account_id=? AND run_status='completed' AND stale=0""",
            (account_id,),
        )
    ).fetchall()
    expected_hash = _password_hash(current_password)
    stale_ids: list[int] = []
    current_url = current_platform_url or None
    for row in rows:
        if (
            row["snapshot_game_type"] != current_game_type
            or (row["snapshot_platform_url"] or None) != current_url
            or row["snapshot_password_hash"] != expected_hash
        ):
            stale_ids.append(int(row["id"]))

    if stale_ids:
        placeholders = ", ".join("?" for _ in stale_ids)
        await db.execute(
            f"""UPDATE account_verification_runs
                SET stale=1, stale_reason='account_fields_changed', updated_at=?
                WHERE id IN ({placeholders})""",
            (now, *stale_ids),
        )
        mutated = True

    if mutated:
        await db.commit()


def _build_summary_status_reason(capabilities: list[dict[str, Any]]) -> str | None:
    if not capabilities:
        return "not_verified"

    supported_count = sum(1 for item in capabilities if item.get("verify_status") == "supported")
    unsupported_count = sum(1 for item in capabilities if item.get("verify_status") == "unsupported")
    probe_failed_count = sum(1 for item in capabilities if item.get("verify_status") == "probe_failed")

    if supported_count >= 1 and probe_failed_count == 0:
        return None
    if supported_count >= 1 and probe_failed_count >= 1:
        return "probe_partial_failure"
    if supported_count == 0 and unsupported_count >= 1 and probe_failed_count == 0:
        return "unsupported_only"
    if supported_count == 0 and probe_failed_count >= 1 and unsupported_count == 0:
        return "probe_failed_only"
    if supported_count == 0 and unsupported_count >= 1 and probe_failed_count >= 1:
        return "unsupported_with_probe_failed"
    return "not_verified"


async def _attach_account_verification_view(
    db: aiosqlite.Connection,
    account: dict[str, Any],
) -> dict[str, Any]:
    await account_verification_runs_refresh_stale(
        db,
        account_id=int(account["id"]),
        current_game_type=str(account["game_type"]),
        current_platform_url=account.get("platform_url"),
        current_password=str(account.get("password") or ""),
        verification_ttl_minutes=30,
    )

    latest_run = await account_verification_run_get_latest(db, account_id=int(account["id"]))
    effective_run = await account_verification_run_get_effective(db, account_id=int(account["id"]))
    running_run = await account_verification_run_get_running(db, account_id=int(account["id"]))
    latest_completed_run = await account_verification_run_get_latest_completed(
        db, account_id=int(account["id"])
    )

    capabilities: list[dict[str, Any]] = []
    if effective_run:
        raw_capabilities = await account_platform_capability_list_by_run(
            db,
            verification_run_id=int(effective_run["id"]),
        )
        capabilities = [
            {
                "platform_type": item.get("platform_type"),
                "verify_status": item.get("verify_status"),
                "market_state": item.get("market_state"),
                "detected_issue": item.get("detected_issue"),
                "last_verified_at": item.get("last_verified_at"),
                "odds_synced": bool(item.get("odds_synced")),
                "odds_count": int(item.get("odds_count") or 0),
                "odds_message": item.get("odds_message"),
                "last_error": item.get("last_error"),
            }
            for item in raw_capabilities
        ]

    verification_stale = bool(
        latest_completed_run and int(latest_completed_run.get("stale") or 0) == 1
    )
    allowed_strategy_platform_types = [
        str(item["platform_type"]).upper()
        for item in capabilities
        if item.get("verify_status") == "supported"
    ]

    account["latest_verification_run_id"] = latest_run["id"] if latest_run else None
    account["effective_verification_run_id"] = effective_run["id"] if effective_run else None
    account["verification_in_progress"] = running_run is not None
    account["verification_stale"] = verification_stale
    account["allowed_strategy_platform_types"] = allowed_strategy_platform_types
    account["platform_capabilities"] = capabilities
    account["summary_status_reason"] = _build_summary_status_reason(capabilities)
    return account


# 
# 2.2 account_platform_capabilities CRUD
# 

async def account_platform_capability_create(
    db: aiosqlite.Connection,
    *,
    verification_run_id: int,
    platform_type: str,
    verify_status: str,
    market_state: str,
    detected_issue: str | None = None,
    last_verified_at: str | None = None,
    odds_synced: bool = False,
    odds_count: int = 0,
    odds_message: str | None = None,
    last_error: str | None = None,
) -> dict[str, Any]:
    now = _now()
    cursor = await db.execute(
        """INSERT INTO account_platform_capabilities
           (verification_run_id, platform_type, verify_status, market_state, detected_issue,
            last_verified_at, odds_synced, odds_count, odds_message, last_error, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            verification_run_id,
            platform_type,
            verify_status,
            market_state,
            detected_issue,
            last_verified_at,
            1 if odds_synced else 0,
            odds_count,
            odds_message,
            last_error,
            now,
            now,
        ),
    )
    await db.commit()
    row = await (
        await db.execute(
            "SELECT * FROM account_platform_capabilities WHERE id=?",
            (cursor.lastrowid,),
        )
    ).fetchone()
    return _row_to_dict(row)  # type: ignore


async def account_platform_capability_list_by_run(
    db: aiosqlite.Connection,
    *,
    verification_run_id: int,
) -> list[dict[str, Any]]:
    rows = await (
        await db.execute(
            "SELECT * FROM account_platform_capabilities "
            "WHERE verification_run_id=? ORDER BY platform_type ASC",
            (verification_run_id,),
        )
    ).fetchall()
    return _rows_to_list(rows)


async def account_platform_capability_delete_by_run(
    db: aiosqlite.Connection,
    *,
    verification_run_id: int,
) -> int:
    cursor = await db.execute(
        "DELETE FROM account_platform_capabilities WHERE verification_run_id=?",
        (verification_run_id,),
    )
    await db.commit()
    return cursor.rowcount


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
    strategy_config: str | None = None,
    bet_timing: int = DEFAULT_BET_TIMING,
    simulation: int = 0,
    stop_loss: int | None = None,
    take_profit: int | None = None,
    gate_window_issues: int | None = None,
    platform_type: str = "JND28WEB",
) -> dict[str, Any]:
    now = _now()
    cursor = await db.execute(
        """INSERT INTO strategies
           (operator_id, account_id, name, type, play_code, base_amount,
            martin_sequence, strategy_config, bet_timing, simulation, stop_loss, take_profit,
            gate_window_issues, platform_type, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (operator_id, account_id, name, type, play_code, base_amount,
         martin_sequence, strategy_config, bet_timing, simulation, stop_loss, take_profit,
         gate_window_issues, platform_type, now, now),
    )
    await db.commit()
    row = await (await db.execute("SELECT * FROM strategies WHERE id=?", (cursor.lastrowid,))).fetchone()
    return _row_to_dict(row)  # type: ignore


def _merge_strategy_simulation_stats(row: dict[str, Any]) -> dict[str, Any]:
    """For simulation strategies, read PnL fields from simulation_strategy_stats."""
    sim_daily = row.pop("simulation_daily_pnl", None)
    sim_total = row.pop("simulation_total_pnl", None)
    sim_daily_date = row.pop("simulation_daily_pnl_date", None)
    if int(row.get("simulation") or 0) == 1:
        row["daily_pnl"] = int(sim_daily or 0)
        row["total_pnl"] = int(sim_total or 0)
        row["daily_pnl_date"] = sim_daily_date
    return row


async def strategy_get_by_id(
    db: aiosqlite.Connection, *, strategy_id: int, operator_id: int
) -> dict[str, Any] | None:
    row = await (await db.execute(
        """SELECT s.*,
                  ss.daily_pnl AS simulation_daily_pnl,
                  ss.total_pnl AS simulation_total_pnl,
                  ss.daily_pnl_date AS simulation_daily_pnl_date
           FROM strategies s
           LEFT JOIN simulation_strategy_stats ss
             ON ss.strategy_id = s.id
            AND ss.operator_id = s.operator_id
           WHERE s.id=? AND s.operator_id=?""",
        (strategy_id, operator_id),
    )).fetchone()
    row_dict = _row_to_dict(row)
    if row_dict is None:
        return None
    return _merge_strategy_simulation_stats(row_dict)


async def strategy_list_by_operator(
    db: aiosqlite.Connection, *, operator_id: int
) -> list[dict[str, Any]]:
    rows = await (await db.execute(
        """SELECT s.*,
                  ss.daily_pnl AS simulation_daily_pnl,
                  ss.total_pnl AS simulation_total_pnl,
                  ss.daily_pnl_date AS simulation_daily_pnl_date
           FROM strategies s
           LEFT JOIN simulation_strategy_stats ss
             ON ss.strategy_id = s.id
            AND ss.operator_id = s.operator_id
           WHERE s.operator_id=?
             AND s.deleted_at IS NULL
             AND s.status <> 'deleted'
           ORDER BY s.id""",
        (operator_id,),
    )).fetchall()
    return [_merge_strategy_simulation_stats(dict(row)) for row in rows]


async def strategy_running_exists_for_account_platform(
    db: aiosqlite.Connection,
    *,
    operator_id: int,
    account_id: int,
    platform_type: str,
) -> bool:
    normalized_platform_type = platform_type.strip().upper()
    row = await (
        await db.execute(
            """SELECT 1
                 FROM strategies
                WHERE operator_id=?
                  AND account_id=?
                  AND UPPER(platform_type)=?
                  AND status='running'
                  AND deleted_at IS NULL
                LIMIT 1""",
            (operator_id, account_id, normalized_platform_type),
        )
    ).fetchone()
    return row is not None


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
        "strategy_config",
        "bet_timing", "simulation", "status", "martin_level",
        "stop_loss", "take_profit", "daily_pnl", "total_pnl", "daily_pnl_date",
        "gate_window_issues", "platform_type",
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
    """Soft-delete a stopped strategy while preserving all order history."""
    del force  # kept for API compatibility; strategy history is never hard-deleted here.
    now = _now()
    cursor = await db.execute(
        """UPDATE strategies
              SET status='deleted', deleted_at=?, updated_at=?
            WHERE id=? AND operator_id=? AND deleted_at IS NULL""",
        (now, now, strategy_id, operator_id),
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


async def account_platform_session_get(
    db: aiosqlite.Connection,
    *,
    account_id: int,
    platform_type: str,
) -> dict[str, Any] | None:
    row = await (
        await db.execute(
            "SELECT * FROM account_platform_sessions WHERE account_id=? AND platform_type=?",
            (account_id, platform_type),
        )
    ).fetchone()
    return _row_to_dict(row)


async def account_platform_session_list(
    db: aiosqlite.Connection,
    *,
    account_id: int,
) -> list[dict[str, Any]]:
    rows = await (
        await db.execute(
            "SELECT * FROM account_platform_sessions WHERE account_id=? ORDER BY platform_type",
            (account_id,),
        )
    ).fetchall()
    return _rows_to_list(rows)


async def account_platform_session_upsert(
    db: aiosqlite.Connection,
    *,
    account_id: int,
    platform_type: str,
    **fields: Any,
) -> dict[str, Any]:
    allowed = {
        "status",
        "session_token",
        "login_fail_count",
        "last_login_at",
        "worker_lock_token",
        "worker_lock_ts",
    }
    filtered = {k: v for k, v in fields.items() if k in allowed}
    now = _now()

    insert_fields = {
        "account_id": account_id,
        "platform_type": platform_type,
        **filtered,
        "created_at": now,
        "updated_at": now,
    }
    columns = ", ".join(insert_fields.keys())
    placeholders = ", ".join("?" for _ in insert_fields)
    update_fields = {**filtered, "updated_at": now}
    update_clause = ", ".join(f"{key}=excluded.{key}" for key in update_fields)

    await db.execute(
        f"""INSERT INTO account_platform_sessions ({columns})
            VALUES ({placeholders})
            ON CONFLICT(account_id, platform_type) DO UPDATE SET {update_clause}""",
        tuple(insert_fields.values()),
    )
    await db.commit()
    row = await (
        await db.execute(
            "SELECT * FROM account_platform_sessions WHERE account_id=? AND platform_type=?",
            (account_id, platform_type),
        )
    ).fetchone()
    return _row_to_dict(row)  # type: ignore


async def account_platform_session_delete(
    db: aiosqlite.Connection,
    *,
    account_id: int,
    platform_type: str,
) -> bool:
    cursor = await db.execute(
        "DELETE FROM account_platform_sessions WHERE account_id=? AND platform_type=?",
        (account_id, platform_type),
    )
    await db.commit()
    return cursor.rowcount > 0


async def account_platform_session_clear_locks(
    db: aiosqlite.Connection,
) -> None:
    await db.execute(
        "UPDATE account_platform_sessions SET worker_lock_token=NULL, worker_lock_ts=NULL "
        "WHERE worker_lock_token IS NOT NULL"
    )
    await db.commit()


# 
# 4. bet_orders CRUD
# 

def _resolve_bet_order_ledger_sql(ledger: str = "real") -> tuple[str, str]:
    resolved = (ledger or "real").strip().lower()
    if resolved == "real":
        return (
            "bet_orders",
            """SELECT b.*, s.name AS strategy_name,
                      COALESCE(NULLIF(a.deleted_account_name, ''), a.account_name) AS account_name
                 FROM bet_orders b
                 LEFT JOIN strategies s
                   ON b.strategy_id = s.id AND b.operator_id = s.operator_id
                 LEFT JOIN gambling_accounts a
                   ON b.account_id = a.id AND b.operator_id = a.operator_id""",
        )
    if resolved == "simulation":
        return (
            "simulation_bet_orders",
            """SELECT b.*, 1 AS simulation, NULL AS martin_level,
                      s.name AS strategy_name,
                      COALESCE(NULLIF(a.deleted_account_name, ''), a.account_name) AS account_name
                 FROM simulation_bet_orders b
                 LEFT JOIN strategies s
                   ON b.strategy_id = s.id AND b.operator_id = s.operator_id
                 LEFT JOIN gambling_accounts a
                   ON b.account_id = a.id AND b.operator_id = a.operator_id""",
        )
    raise ValueError(f"invalid ledger={ledger}")


def _normalize_order_date_start(value: str) -> str:
    return f"{value} 00:00:00" if len(value) == 10 else value


def _normalize_order_date_end(value: str) -> str:
    return f"{value} 23:59:59" if len(value) == 10 else value


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
    actual_platform_type: str | None = None,
) -> dict[str, Any]:
    """idempotent_id  UNIQUE  IntegrityError"""
    now = _now()
    cursor = await db.execute(
        """INSERT INTO bet_orders
           (idempotent_id, operator_id, account_id, strategy_id, actual_platform_type,
            issue, key_code, amount, odds, status, simulation, martin_level, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (idempotent_id, operator_id, account_id, strategy_id, actual_platform_type,
         issue, key_code, amount, odds, status, simulation, martin_level, now),
    )
    await db.commit()
    row = await (await db.execute("SELECT * FROM bet_orders WHERE id=?", (cursor.lastrowid,))).fetchone()
    return _row_to_dict(row)  # type: ignore


async def bet_order_get_by_id(
    db: aiosqlite.Connection,
    *,
    order_id: int,
    operator_id: int,
    ledger: str = "real",
) -> dict[str, Any] | None:
    _, select_sql = _resolve_bet_order_ledger_sql(ledger)
    row = await (await db.execute(
        f"{select_sql} WHERE b.id=? AND b.operator_id=?",
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
    ledger: str = "real",
) -> tuple[list[dict[str, Any]], int]:
    """ (items, total)"""
    table_name, select_sql = _resolve_bet_order_ledger_sql(ledger)
    conditions = ["b.operator_id=?"]
    params: list[Any] = [operator_id]

    if date_from:
        conditions.append("b.created_at >= ?")
        params.append(_normalize_order_date_start(date_from))
    if date_to:
        conditions.append("b.created_at <= ?")
        params.append(_normalize_order_date_end(date_to))
    if strategy_id is not None:
        conditions.append("b.strategy_id=?")
        params.append(strategy_id)
    if status == "settled":
        conditions.append("b.status='settled'")
    elif status == "pending":
        conditions.append("b.status IN ('bet_success','settling','pending_match')")
    if account_id is not None:
        conditions.append("b.account_id=?")
        params.append(account_id)

    where = " AND ".join(conditions)

    # total count
    count_row = await (await db.execute(
        f"SELECT COUNT(*) as cnt FROM {table_name} b WHERE {where}", tuple(params)
    )).fetchone()
    total = count_row["cnt"] if count_row else 0

    # paginated results
    offset = (page - 1) * page_size
    rows = await (await db.execute(
        f"{select_sql} WHERE {where} ORDER BY b.created_at DESC LIMIT ? OFFSET ?",
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
    ledger: str = "real",
) -> dict[str, Any]:
    """返回筛选条件下的汇总统计：total_amount, total_payout"""
    table_name, _ = _resolve_bet_order_ledger_sql(ledger)
    conditions = ["b.operator_id=?"]
    params: list[Any] = [operator_id]

    if date_from:
        conditions.append("b.created_at >= ?")
        params.append(_normalize_order_date_start(date_from))
    if date_to:
        conditions.append("b.created_at <= ?")
        params.append(_normalize_order_date_end(date_to))
    if strategy_id is not None:
        conditions.append("b.strategy_id=?")
        params.append(strategy_id)
    if status == "settled":
        conditions.append("b.status='settled'")
    elif status == "pending":
        conditions.append("b.status IN ('bet_success','settling','pending_match')")
    if account_id is not None:
        conditions.append("b.account_id=?")
        params.append(account_id)

    where = " AND ".join(conditions)

    row = await (await db.execute(
        f"""SELECT COALESCE(SUM(amount), 0) as total_amount,
                   COALESCE(SUM(CASE WHEN status='settled' AND pnl IS NOT NULL
                                     THEN amount + pnl ELSE 0 END), 0) as total_payout
            FROM {table_name} b WHERE {where}""",
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
        """SELECT b.*, s.name AS strategy_name,
                  COALESCE(NULLIF(a.deleted_account_name, ''), a.account_name) AS account_name
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
    platform_type: str,
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
               (account_id, platform_type, key_code, odds_value, confirmed, fetched_at, confirmed_at)
               VALUES (?, ?, ?, ?, ?, datetime('now', '+8 hours'), {confirmed_at_expr})""",
            (account_id, platform_type, key_code, odds_value, confirmed_int),
        )
    await db.commit()


async def odds_list_by_account(
    db: aiosqlite.Connection,
    *,
    account_id: int,
    platform_type: str | None = None,
) -> list[dict[str, Any]]:
    """获取账号所有赔率记录，按 key_code 字母序排列。"""
    if platform_type:
        rows = await (await db.execute(
            "SELECT * FROM account_odds WHERE account_id=? AND platform_type=? "
            "ORDER BY key_code ASC",
            (account_id, platform_type),
        )).fetchall()
    else:
        rows = await (await db.execute(
            "SELECT * FROM account_odds WHERE account_id=? ORDER BY platform_type ASC, key_code ASC",
            (account_id,),
        )).fetchall()
    return _rows_to_list(rows)


async def odds_get_confirmed_map(
    db: aiosqlite.Connection,
    *,
    account_id: int,
    platform_type: str,
) -> dict[str, int] | None:
    """获取已确认赔率 dict。无记录→None；有未确认→None；全确认→{key_code: odds_value}。"""
    rows = await (await db.execute(
        "SELECT key_code, odds_value, confirmed FROM account_odds "
        "WHERE account_id=? AND platform_type=?",
        (account_id, platform_type),
    )).fetchall()

    if not rows:
        return None

    result: dict[str, int] = {}
    for r in rows:
        odds_value = int(r["odds_value"] or 0)
        if r["confirmed"] == 0 and odds_value <= 0:
            return None
        result[r["key_code"]] = odds_value
    return result


async def odds_get_latest_map(
    db: aiosqlite.Connection,
    *,
    account_id: int,
    platform_type: str,
) -> dict[str, int] | None:
    """Return the latest stored odds regardless of manual confirmation state."""
    rows = await (await db.execute(
        "SELECT key_code, odds_value FROM account_odds "
        "WHERE account_id=? AND platform_type=?",
        (account_id, platform_type),
    )).fetchall()

    if not rows:
        return None
    return {r["key_code"]: r["odds_value"] for r in rows}


async def odds_confirm_all(
    db: aiosqlite.Connection,
    *,
    account_id: int,
    platform_type: str,
) -> int:
    """确认该账号所有未确认赔率，返回更新行数。幂等。"""
    cursor = await db.execute(
        """UPDATE account_odds SET confirmed=1, confirmed_at=datetime('now', '+8 hours')
           WHERE account_id=? AND platform_type=? AND confirmed=0""",
        (account_id, platform_type),
    )
    await db.commit()
    return cursor.rowcount


async def odds_has_records(
    db: aiosqlite.Connection,
    *,
    account_id: int,
    platform_type: str | None = None,
) -> bool:
    """检查账号是否有赔率记录。"""
    if platform_type:
        row = await (await db.execute(
            "SELECT COUNT(*) as cnt FROM account_odds WHERE account_id=? AND platform_type=?",
            (account_id, platform_type),
        )).fetchone()
    else:
        row = await (await db.execute(
            "SELECT COUNT(*) as cnt FROM account_odds WHERE account_id=?",
            (account_id,),
        )).fetchone()
    return (row["cnt"] if row else 0) > 0


# ──────────────────────────────────────────────
# 10. shared market + simulation phase-1 contracts
# ──────────────────────────────────────────────


async def shared_market_group_resolve_by_url(
    db: aiosqlite.Connection,
    *,
    normalized_url: str,
    only_enabled: bool = True,
) -> dict[str, Any] | None:
    """Resolve shared market group by normalized URL."""
    sql = (
        "SELECT g.id AS shared_group_id, g.group_key, g.enabled, "
        "g.collector_platform_type, g.collector_account_name, "
        "g.collector_password_enc, g.freshness_threshold_sec, "
        "u.normalized_url, u.id AS url_id "
        "FROM shared_market_group_urls u "
        "JOIN shared_market_groups g ON g.id = u.shared_group_id "
        "WHERE u.normalized_url=?"
    )
    params: list[Any] = [normalized_url]
    if only_enabled:
        sql += " AND g.enabled=1"
    sql += " LIMIT 1"
    row = await (await db.execute(sql, tuple(params))).fetchone()
    return _row_to_dict(row)


_SNAPSHOT_MARKET_STATE_ALIASES = {
    "shared_ok": "shared_ok",
    "shared_error": "shared_error",
    "shared_stale": "shared_stale",
    "market_closed": "market_closed",
    # legacy aliases
    "shared_hit": "shared_ok",
    "local_fallback": "shared_error",
    "processing": "shared_ok",
}

_SNAPSHOT_DRAW_STATE_ALIASES = {
    "normal": "normal",
    "draw_pending": "draw_pending",
    "draw_wait_retry": "draw_wait_retry",
    # legacy aliases
    "processing": "draw_pending",
}


def _normalize_snapshot_market_data_state(
    value: object,
    *,
    source_status: object = None,
) -> str:
    text = str(value or "").strip().lower()
    if text in _SNAPSHOT_MARKET_STATE_ALIASES:
        return _SNAPSHOT_MARKET_STATE_ALIASES[text]
    source = str(source_status or "").strip().lower()
    if source in {"error", "failed", "offline"}:
        return "shared_error"
    if source in {"stale", "expired"}:
        return "shared_stale"
    if source in {"closed", "market_closed"}:
        return "market_closed"
    return "shared_ok"


def _normalize_snapshot_draw_state(value: object) -> str:
    text = str(value or "").strip().lower()
    return _SNAPSHOT_DRAW_STATE_ALIASES.get(text, "normal")


def _normalize_snapshot_source_status(value: object, *, market_data_state: str) -> str:
    text = str(value or "").strip().lower()
    if text:
        return text
    if market_data_state == "shared_error":
        return "error"
    if market_data_state == "shared_stale":
        return "stale"
    if market_data_state == "market_closed":
        return "closed"
    return "online"


def _normalize_snapshot_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    market_data_state = _normalize_snapshot_market_data_state(
        row.get("market_data_state"),
        source_status=row.get("source_status"),
    )
    row["market_data_state"] = market_data_state
    row["draw_state"] = _normalize_snapshot_draw_state(row.get("draw_state"))
    row["snapshot_version"] = int(row.get("snapshot_version") or 0)
    row["next_normal_refresh_at"] = row.get("next_normal_refresh_at")
    row["next_draw_retry_at"] = row.get("next_draw_retry_at")
    row["message_code"] = row.get("message_code")
    row["message_text"] = row.get("message_text")

    # Legacy compatibility during migration.
    if not row.get("current_issue"):
        row["current_issue"] = row.get("issue")
    if row.get("current_state") is None:
        row["current_state"] = row.get("state")
    if row.get("pre_result") is None:
        row["pre_result"] = row.get("open_result")
    row["shared_market_state"] = market_data_state
    return row


async def shared_market_snapshot_upsert(
    db: aiosqlite.Connection,
    *,
    shared_group_id: int,
    issue: str | None = None,
    state: str | None = None,
    close_countdown_sec: int | None = None,
    open_countdown_sec: int | None = None,
    pre_issue: str | None = None,
    open_result: str | None = None,
    fetched_at: str | None = None,
    source_status: str | None = None,
    last_error: str | None = None,
    market_data_state: str | None = None,
    draw_state: str | None = None,
    next_normal_refresh_at: str | None = None,
    next_draw_retry_at: str | None = None,
    snapshot_version: int | None = None,
    message_code: str | None = None,
    message_text: str | None = None,
) -> dict[str, Any]:
    now = _now()
    resolved_fetched_at = fetched_at or now
    existing_row = await (await db.execute(
        "SELECT snapshot_version FROM shared_market_snapshots WHERE shared_group_id=?",
        (shared_group_id,),
    )).fetchone()
    existing_version = int(existing_row["snapshot_version"] or 0) if existing_row else 0
    try:
        resolved_snapshot_version = (
            int(snapshot_version)
            if snapshot_version is not None
            else existing_version + 1
        )
    except (TypeError, ValueError):
        resolved_snapshot_version = existing_version + 1
    if resolved_snapshot_version < 0:
        resolved_snapshot_version = 0

    resolved_market_data_state = _normalize_snapshot_market_data_state(
        market_data_state,
        source_status=source_status,
    )
    resolved_draw_state = _normalize_snapshot_draw_state(draw_state)
    resolved_source_status = _normalize_snapshot_source_status(
        source_status,
        market_data_state=resolved_market_data_state,
    )

    await db.execute(
        """INSERT INTO shared_market_snapshots
           (shared_group_id, issue, state, snapshot_version, market_data_state, draw_state,
            close_countdown_sec, open_countdown_sec, pre_issue, open_result, fetched_at,
            next_normal_refresh_at, next_draw_retry_at, source_status, last_error,
            message_code, message_text, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(shared_group_id) DO UPDATE SET
               issue=excluded.issue,
               state=excluded.state,
               snapshot_version=excluded.snapshot_version,
               market_data_state=excluded.market_data_state,
               draw_state=excluded.draw_state,
               close_countdown_sec=excluded.close_countdown_sec,
               open_countdown_sec=excluded.open_countdown_sec,
               pre_issue=excluded.pre_issue,
               open_result=excluded.open_result,
               fetched_at=excluded.fetched_at,
               next_normal_refresh_at=excluded.next_normal_refresh_at,
               next_draw_retry_at=excluded.next_draw_retry_at,
               source_status=excluded.source_status,
               last_error=excluded.last_error,
               message_code=excluded.message_code,
               message_text=excluded.message_text,
               updated_at=excluded.updated_at
           WHERE excluded.snapshot_version > shared_market_snapshots.snapshot_version
              OR (
                  excluded.snapshot_version = shared_market_snapshots.snapshot_version
                  AND excluded.fetched_at >= shared_market_snapshots.fetched_at
              )""",
        (
            shared_group_id,
            issue,
            state,
            resolved_snapshot_version,
            resolved_market_data_state,
            resolved_draw_state,
            close_countdown_sec,
            open_countdown_sec,
            pre_issue,
            open_result,
            resolved_fetched_at,
            next_normal_refresh_at,
            next_draw_retry_at,
            resolved_source_status,
            last_error,
            message_code,
            message_text,
            now,
            now,
        ),
    )
    await db.commit()
    row = await (await db.execute(
        "SELECT * FROM shared_market_snapshots WHERE shared_group_id=?",
        (shared_group_id,),
    )).fetchone()
    return _normalize_snapshot_row(_row_to_dict(row))  # type: ignore


async def shared_market_snapshot_get_latest(
    db: aiosqlite.Connection,
    *,
    shared_group_id: int,
) -> dict[str, Any] | None:
    row = await (await db.execute(
        """SELECT * FROM shared_market_snapshots
           WHERE shared_group_id=?
           ORDER BY fetched_at DESC, id DESC
           LIMIT 1""",
        (shared_group_id,),
    )).fetchone()
    return _normalize_snapshot_row(_row_to_dict(row))


async def shared_market_group_url_exists(
    db: aiosqlite.Connection,
    *,
    normalized_url: str,
) -> dict[str, Any] | None:
    row = await (await db.execute(
        "SELECT * FROM shared_market_group_urls WHERE normalized_url=?",
        (normalized_url,),
    )).fetchone()
    return _row_to_dict(row)


async def shared_market_group_url_add(
    db: aiosqlite.Connection,
    *,
    shared_group_id: int,
    normalized_url: str,
) -> dict[str, Any]:
    now = _now()
    await db.execute(
        """INSERT INTO shared_market_group_urls
           (shared_group_id, normalized_url, created_at, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(normalized_url) DO UPDATE SET
              shared_group_id=excluded.shared_group_id,
              updated_at=excluded.updated_at""",
        (shared_group_id, normalized_url, now, now),
    )
    await db.commit()
    row = await (await db.execute(
        "SELECT * FROM shared_market_group_urls WHERE normalized_url=?",
        (normalized_url,),
    )).fetchone()
    return _row_to_dict(row)  # type: ignore


async def shared_market_uncovered_url_touch(
    db: aiosqlite.Connection,
    *,
    normalized_url: str,
    sample_raw_url: str | None = None,
    last_account_id: int | None = None,
    last_platform_type: str | None = None,
    platform_type: str | None = None,
    seen_at: str | None = None,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    resolved_platform_type = platform_type or last_platform_type
    at = seen_at or _now()
    await db.execute(
        """INSERT INTO shared_market_uncovered_urls
           (normalized_url, first_seen_at, last_seen_at, hit_count, sample_raw_url,
            last_account_id, last_platform_type, detection_status, status, detection_error, failure_reason)
           VALUES (?, ?, ?, 1, ?, ?, ?, 'pending', 'pending', ?, ?)
            ON CONFLICT(normalized_url) DO UPDATE SET
                last_seen_at=excluded.last_seen_at,
                hit_count=shared_market_uncovered_urls.hit_count + 1,
                last_account_id=COALESCE(excluded.last_account_id, shared_market_uncovered_urls.last_account_id),
                last_platform_type=COALESCE(excluded.last_platform_type, shared_market_uncovered_urls.last_platform_type),
                sample_raw_url=COALESCE(excluded.sample_raw_url, shared_market_uncovered_urls.sample_raw_url),
                detection_error=COALESCE(excluded.detection_error, shared_market_uncovered_urls.detection_error),
                failure_reason=COALESCE(excluded.failure_reason, shared_market_uncovered_urls.failure_reason),
                status='pending',
                detection_status='pending'""",
        (
            normalized_url,
            at,
            at,
            sample_raw_url,
            last_account_id,
            resolved_platform_type,
            failure_reason,
            failure_reason,
        ),
    )
    await db.commit()
    row = await (await db.execute(
        "SELECT * FROM shared_market_uncovered_urls WHERE normalized_url=?",
        (normalized_url,),
    )).fetchone()
    return _row_to_dict(row)  # type: ignore


async def shared_market_uncovered_url_list(
    db: aiosqlite.Connection,
    *,
    status: str = "pending",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    where_clause = ""
    params: list[Any] = []
    if status:
        where_clause = "WHERE (u.detection_status=? OR u.status=?)"
        params.append(status)
        params.append(status)
    count_row = await (await db.execute(
        f"SELECT COUNT(*) as cnt FROM shared_market_uncovered_urls u {where_clause}",
        tuple(params),
    )).fetchone()
    total = count_row["cnt"] if count_row else 0

    offset = (page - 1) * page_size
    rows = await (await db.execute(
        f"""
        SELECT u.*,
               g.group_key
        FROM shared_market_uncovered_urls u
        LEFT JOIN shared_market_groups g ON g.id = u.shared_group_id
        {where_clause}
        ORDER BY u.last_seen_at DESC
        LIMIT ? OFFSET ?
        """,
        (*params, page_size, offset),
    )).fetchall()
    return _rows_to_list(rows), total


async def shared_market_uncovered_url_list_pending(
    db: aiosqlite.Connection,
    *,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    return await shared_market_uncovered_url_list(
        db,
        status="pending",
        page=page,
        page_size=page_size,
    )


async def shared_market_uncovered_url_get(
    db: aiosqlite.Connection,
    *,
    row_id: int,
) -> dict[str, Any] | None:
    row = await (await db.execute(
        "SELECT * FROM shared_market_uncovered_urls WHERE id=?",
        (row_id,),
    )).fetchone()
    return _row_to_dict(row)


async def shared_market_uncovered_url_set_status(
    db: aiosqlite.Connection,
    *,
    row_id: int,
    status: str,
    failure_reason: str | None = None,
    reviewed_at: str | None = None,
) -> dict[str, Any] | None:
    at = _now() if reviewed_at is None else reviewed_at
    await db.execute(
        """UPDATE shared_market_uncovered_urls
           SET status=?, detection_status=?, review_status=?,
               failure_reason=?, detection_error=?,
               reviewed_at=?, last_checked_at=?
          WHERE id=?""",
        (
            status,
            status,
            status,
            failure_reason,
            failure_reason,
            at,
            at,
            row_id,
        ),
    )
    await db.commit()
    return await shared_market_uncovered_url_get(db, row_id=row_id)


async def shared_market_uncovered_url_mark_detecting(
    db: aiosqlite.Connection,
    *,
    row_id: int,
    checked_at: str | None = None,
) -> dict[str, Any] | None:
    at = checked_at or _now()
    await db.execute(
        """UPDATE shared_market_uncovered_urls
           SET detection_status='detecting',
               status='detecting',
               reviewed_at=?,
               last_checked_at=?
         WHERE id=?""",
        (at, at, row_id),
    )
    await db.commit()
    return await shared_market_uncovered_url_get(db, row_id=row_id)


async def shared_market_uncovered_url_mark_matched(
    db: aiosqlite.Connection,
    *,
    row_id: int,
    shared_group_id: int,
    reviewed_at: str | None = None,
) -> dict[str, Any] | None:
    at = reviewed_at or _now()
    row = await shared_market_uncovered_url_get(db, row_id=row_id)
    if row is None:
        return None

    await db.execute(
        """UPDATE shared_market_uncovered_urls
           SET detection_status='matched',
               status='matched',
               review_status='matched',
               matched_shared_group_id=?,
               shared_group_id=?,
               reviewed_at=?,
               last_checked_at=?,
               detection_error=NULL,
               failure_reason=NULL
         WHERE id=?""",
        (shared_group_id, shared_group_id, at, at, row_id),
    )
    await db.commit()
    normalized_url = row.get("normalized_url")
    if normalized_url:
        await shared_market_group_url_add(
            db,
            shared_group_id=shared_group_id,
            normalized_url=str(normalized_url),
        )
    return await shared_market_uncovered_url_get(db, row_id=row_id)


async def shared_market_uncovered_url_mark_review_required(
    db: aiosqlite.Connection,
    *,
    row_id: int,
    review_status: str = "review_required",
    failure_reason: str | None = None,
    checked_at: str | None = None,
) -> dict[str, Any] | None:
    at = checked_at or _now()
    await db.execute(
        """UPDATE shared_market_uncovered_urls
           SET detection_status='review_required',
               status='review_required',
               review_status=?,
               detection_error=?,
               failure_reason=?,
               reviewed_at=?,
               last_checked_at=?
         WHERE id=?""",
        (review_status, failure_reason, failure_reason, at, at, row_id),
    )
    await db.commit()
    return await shared_market_uncovered_url_get(db, row_id=row_id)


async def shared_market_uncovered_url_bind_group(
    db: aiosqlite.Connection,
    *,
    row_id: int,
    shared_group_id: int | None,
    failure_reason: str | None = None,
) -> dict[str, Any] | None:
    at = _now()
    await db.execute(
        """UPDATE shared_market_uncovered_urls
           SET shared_group_id=?,
               matched_shared_group_id=?,
               status='matched',
               detection_status='matched',
               review_status='matched',
               detection_error=NULL,
               failure_reason=?,
               reviewed_at=?,
               last_checked_at=?
          WHERE id=?""",
        (shared_group_id, shared_group_id, failure_reason, at, at, row_id),
    )
    await db.commit()
    if shared_group_id is not None:
        row = await shared_market_uncovered_url_get(db, row_id=row_id)
        if row is not None:
            normalized_url = row.get("normalized_url")
            if normalized_url:
                await shared_market_group_url_add(
                    db,
                    shared_group_id=shared_group_id,
                    normalized_url=str(normalized_url),
                )
    return await shared_market_uncovered_url_get(db, row_id=row_id)


async def shared_market_group_list(
    db: aiosqlite.Connection,
    *,
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT g.id, g.group_key, g.enabled, g.collector_platform_type, "
        "g.collector_account_name, g.collector_password_enc, "
        "g.freshness_threshold_sec, "
        "s.issue AS snapshot_issue, s.pre_issue AS snapshot_pre_issue, "
        "s.open_result AS snapshot_open_result, s.source_status, "
        "s.last_error, s.fetched_at AS snapshot_fetched_at, "
        "s.updated_at AS snapshot_updated_at, "
        "(SELECT u.normalized_url FROM shared_market_group_urls u "
        " WHERE u.shared_group_id=g.id ORDER BY u.id LIMIT 1) AS primary_url "
        "FROM shared_market_groups g "
        "LEFT JOIN shared_market_snapshots s ON s.shared_group_id=g.id"
    )
    if not include_disabled:
        sql += " WHERE g.enabled=1"
    rows = await (await db.execute(sql)).fetchall()
    return _rows_to_list(rows)


async def shared_market_group_get(
    db: aiosqlite.Connection,
    *,
    shared_group_id: int,
) -> dict[str, Any] | None:
    row = await (await db.execute(
        "SELECT g.*, "
        "(SELECT u.normalized_url FROM shared_market_group_urls u "
        " WHERE u.shared_group_id=g.id ORDER BY u.id LIMIT 1) AS primary_url "
        "FROM shared_market_groups g WHERE g.id=?",
        (shared_group_id,),
    )).fetchone()
    return _row_to_dict(row)

async def simulation_bet_order_create(
    db: aiosqlite.Connection,
    *,
    idempotent_id: str,
    operator_id: int,
    account_id: int,
    strategy_id: int,
    issue: str,
    platform_type: str,
    key_code: str,
    amount: int,
    odds: int | None = None,
    status: str = "pending",
    is_win: int | None = None,
    pnl: int | None = None,
    open_result: str | None = None,
    sum_value: int | None = None,
    fail_reason: str | None = None,
    bet_at: str | None = None,
    settled_at: str | None = None,
) -> dict[str, Any]:
    now = _now()
    cursor = await db.execute(
        """INSERT INTO simulation_bet_orders
           (idempotent_id, operator_id, account_id, strategy_id, issue,
            platform_type, key_code, amount, odds, status, is_win, pnl,
            open_result, sum_value, fail_reason, created_at, bet_at, settled_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            idempotent_id,
            operator_id,
            account_id,
            strategy_id,
            issue,
            platform_type,
            key_code,
            amount,
            odds,
            status,
            is_win,
            pnl,
            open_result,
            sum_value,
            fail_reason,
            now,
            bet_at,
            settled_at,
        ),
    )
    await db.commit()
    row = await (await db.execute(
        "SELECT * FROM simulation_bet_orders WHERE id=?",
        (cursor.lastrowid,),
    )).fetchone()
    return _row_to_dict(row)  # type: ignore


async def simulation_bet_order_update(
    db: aiosqlite.Connection,
    *,
    order_id: int,
    status: str,
    operator_id: int | None = None,
    **extra_fields: Any,
) -> dict[str, Any] | None:
    allowed_extra = {
        "odds",
        "is_win",
        "pnl",
        "open_result",
        "sum_value",
        "fail_reason",
        "bet_at",
        "settled_at",
    }
    updates: dict[str, Any] = {"status": status}
    for key, value in extra_fields.items():
        if key in allowed_extra:
            updates[key] = value

    set_clause = ", ".join(f"{k}=?" for k in updates)
    where_parts = ["id=?"]
    params: list[Any] = list(updates.values()) + [order_id]
    if operator_id is not None:
        where_parts.append("operator_id=?")
        params.append(operator_id)

    await db.execute(
        f"UPDATE simulation_bet_orders SET {set_clause} WHERE {' AND '.join(where_parts)}",
        tuple(params),
    )
    await db.commit()

    query = "SELECT * FROM simulation_bet_orders WHERE id=?"
    query_params: list[Any] = [order_id]
    if operator_id is not None:
        query += " AND operator_id=?"
        query_params.append(operator_id)
    row = await (await db.execute(query, tuple(query_params))).fetchone()
    return _row_to_dict(row)


async def simulation_strategy_stats_upsert(
    db: aiosqlite.Connection,
    *,
    strategy_id: int,
    operator_id: int,
    account_id: int,
    daily_pnl_delta: int = 0,
    total_pnl_delta: int = 0,
    bet_count_delta: int = 0,
    win_count_delta: int = 0,
    loss_count_delta: int = 0,
    daily_pnl_date: str | None = None,
    settled_at: str | None = None,
) -> dict[str, Any]:
    now = _now()
    resolved_settled_at = settled_at or now
    resolved_daily_pnl_date = daily_pnl_date or resolved_settled_at[:10]

    await db.execute(
        """INSERT INTO simulation_strategy_stats
           (strategy_id, operator_id, account_id, daily_pnl, total_pnl, daily_pnl_date,
            bet_count, win_count, loss_count, last_settled_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(strategy_id) DO UPDATE SET
               operator_id=excluded.operator_id,
               account_id=excluded.account_id,
               total_pnl=simulation_strategy_stats.total_pnl + excluded.total_pnl,
               daily_pnl=CASE
                   WHEN simulation_strategy_stats.daily_pnl_date = excluded.daily_pnl_date
                   THEN simulation_strategy_stats.daily_pnl + excluded.daily_pnl
                   ELSE excluded.daily_pnl
               END,
               daily_pnl_date=excluded.daily_pnl_date,
               bet_count=simulation_strategy_stats.bet_count + excluded.bet_count,
               win_count=simulation_strategy_stats.win_count + excluded.win_count,
               loss_count=simulation_strategy_stats.loss_count + excluded.loss_count,
               last_settled_at=COALESCE(excluded.last_settled_at, simulation_strategy_stats.last_settled_at),
               updated_at=excluded.updated_at""",
        (
            strategy_id,
            operator_id,
            account_id,
            daily_pnl_delta,
            total_pnl_delta,
            resolved_daily_pnl_date,
            bet_count_delta,
            win_count_delta,
            loss_count_delta,
            resolved_settled_at,
            now,
            now,
        ),
    )
    await db.commit()
    row = await (await db.execute(
        "SELECT * FROM simulation_strategy_stats WHERE strategy_id=?",
        (strategy_id,),
    )).fetchone()
    return _row_to_dict(row)  # type: ignore
