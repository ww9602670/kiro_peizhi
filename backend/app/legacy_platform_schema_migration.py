from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.database import DDL_STATEMENTS
from app.schemas.account import normalize_game_type

_BJT = timezone(timedelta(hours=8))

CURRENT_TABLE_ORDER = [
    "operators",
    "gambling_accounts",
    "account_platform_sessions",
    "account_verification_runs",
    "account_platform_capabilities",
    "strategies",
    "bet_orders",
    "alerts",
    "audit_logs",
    "lottery_results",
    "reconcile_records",
    "bet_order_platform_records",
    "account_odds",
    "backtest_tasks",
    "shared_market_groups",
    "shared_market_group_urls",
    "shared_market_snapshots",
    "shared_market_uncovered_urls",
    "simulation_bet_orders",
    "simulation_strategy_stats",
]

LEGACY_SOURCE_TABLES = [
    "operators",
    "gambling_accounts",
    "strategies",
    "bet_orders",
    "alerts",
    "audit_logs",
    "lottery_results",
    "reconcile_records",
    "bet_order_platform_records",
    "account_odds",
    "backtest_tasks",
]


class MigrationError(RuntimeError):
    pass


def _now_bjt() -> str:
    return datetime.now(_BJT).strftime("%Y-%m-%d %H:%M:%S")


def _password_hash(password: str) -> str:
    import hashlib

    return hashlib.sha256((password or "").encode("utf-8")).hexdigest()


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [str(row[1]) for row in rows]


def _copy_rows(
    conn: sqlite3.Connection,
    table_name: str,
    rows: list[dict[str, Any]],
    columns: list[str] | None = None,
) -> int:
    if not rows:
        return 0
    insert_columns = columns or list(rows[0].keys())
    placeholders = ", ".join("?" for _ in insert_columns)
    sql = (
        f"INSERT INTO {table_name} ({', '.join(insert_columns)}) "
        f"VALUES ({placeholders})"
    )
    conn.executemany(
        sql,
        [tuple(row.get(column) for column in insert_columns) for row in rows],
    )
    return len(rows)


def _read_all_rows(conn: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, table_name):
        return []
    rows = conn.execute(f"SELECT * FROM {table_name} ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def _detect_schema_kind(conn: sqlite3.Connection) -> str:
    columns = set(_table_columns(conn, "gambling_accounts"))
    if not columns:
        raise MigrationError("gambling_accounts table is missing")
    if "platform_type" in columns and "game_type" not in columns:
        return "legacy"
    if "game_type" in columns and "platform_type" not in columns:
        return "current"
    raise MigrationError(
        "Unsupported gambling_accounts schema; expected either legacy platform_type "
        "or current game_type layout"
    )


def _create_current_schema(conn: sqlite3.Connection) -> None:
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)
    conn.commit()


def _collect_table_counts(
    conn: sqlite3.Connection,
    table_names: list[str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table_name in table_names:
        if not _table_exists(conn, table_name):
            counts[table_name] = 0
            continue
        row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {table_name}").fetchone()
        counts[table_name] = int(row["cnt"] if row else 0)
    return counts


def _copy_current_schema(
    source_conn: sqlite3.Connection,
    target_conn: sqlite3.Connection,
) -> dict[str, int]:
    copied_counts: dict[str, int] = {}
    for table_name in CURRENT_TABLE_ORDER:
        if not _table_exists(source_conn, table_name):
            copied_counts[table_name] = 0
            continue
        rows = _read_all_rows(source_conn, table_name)
        copied_counts[table_name] = _copy_rows(target_conn, table_name, rows)
    target_conn.commit()
    return copied_counts


def _migrate_legacy_schema(
    source_conn: sqlite3.Connection,
    target_conn: sqlite3.Connection,
) -> dict[str, Any]:
    copied_counts: dict[str, int] = {}

    operators = _read_all_rows(source_conn, "operators")
    copied_counts["operators"] = _copy_rows(target_conn, "operators", operators)

    legacy_accounts = _read_all_rows(source_conn, "gambling_accounts")
    odds_counts_by_account = {
        int(row["account_id"]): int(row["cnt"])
        for row in source_conn.execute(
            "SELECT account_id, COUNT(*) AS cnt FROM account_odds GROUP BY account_id"
        ).fetchall()
    }
    strategy_platform_by_id = {
        int(row["id"]): str(row["platform_type"]).upper()
        for row in source_conn.execute(
            "SELECT id, platform_type FROM strategies"
        ).fetchall()
    }

    migrated_accounts: list[dict[str, Any]] = []
    migrated_sessions: list[dict[str, Any]] = []
    migrated_runs: list[dict[str, Any]] = []
    migrated_capabilities: list[dict[str, Any]] = []

    run_id_seed = 1
    capability_id_seed = 1
    legacy_platform_by_account: dict[int, str] = {}

    for account in legacy_accounts:
        account_id = int(account["id"])
        platform_type = str(account["platform_type"]).upper()
        legacy_platform_by_account[account_id] = platform_type
        game_type = normalize_game_type(legacy_platform_type=platform_type)
        migrated_accounts.append(
            {
                "id": account_id,
                "operator_id": account["operator_id"],
                "account_name": account["account_name"],
                "password": account["password"],
                "game_type": game_type,
                "platform_url": account["platform_url"],
                "status": account["status"],
                "session_token": account["session_token"],
                "balance": account["balance"],
                "login_fail_count": account["login_fail_count"],
                "last_login_at": account["last_login_at"],
                "kill_switch": account["kill_switch"],
                "single_bet_limit": account["single_bet_limit"],
                "daily_limit": account["daily_limit"],
                "period_limit": account["period_limit"],
                "created_at": account["created_at"],
                "updated_at": account["updated_at"],
            }
        )
        migrated_sessions.append(
            {
                "account_id": account_id,
                "platform_type": platform_type,
                "status": account["status"],
                "session_token": account["session_token"],
                "login_fail_count": account["login_fail_count"],
                "last_login_at": account["last_login_at"],
                "worker_lock_token": account["worker_lock_token"],
                "worker_lock_ts": account["worker_lock_ts"],
                "created_at": account["created_at"],
                "updated_at": account["updated_at"],
            }
        )
        finished_at = (
            account["last_login_at"]
            or account["updated_at"]
            or account["created_at"]
            or _now_bjt()
        )
        odds_count = odds_counts_by_account.get(account_id, 0)
        odds_synced = 1 if odds_count > 0 else 0
        odds_message = (
            f"legacy migration imported {odds_count} odds rows"
            if odds_count > 0
            else "legacy migration pending odds sync"
        )
        migrated_runs.append(
            {
                "id": run_id_seed,
                "account_id": account_id,
                "run_status": "completed",
                "snapshot_game_type": game_type,
                "snapshot_platform_url": account["platform_url"],
                "snapshot_password_hash": _password_hash(str(account["password"] or "")),
                "stale": 0,
                "stale_reason": None,
                "started_at": finished_at,
                "finished_at": finished_at,
                "created_at": account["created_at"] or finished_at,
                "updated_at": account["updated_at"] or finished_at,
            }
        )
        migrated_capabilities.append(
            {
                "id": capability_id_seed,
                "verification_run_id": run_id_seed,
                "platform_type": platform_type,
                "verify_status": "supported",
                "market_state": "unknown",
                "detected_issue": None,
                "last_verified_at": finished_at,
                "odds_synced": odds_synced,
                "odds_count": odds_count,
                "odds_message": odds_message,
                "last_error": None,
                "created_at": account["created_at"] or finished_at,
                "updated_at": account["updated_at"] or finished_at,
            }
        )
        run_id_seed += 1
        capability_id_seed += 1

    copied_counts["gambling_accounts"] = _copy_rows(
        target_conn, "gambling_accounts", migrated_accounts
    )
    copied_counts["account_platform_sessions"] = _copy_rows(
        target_conn, "account_platform_sessions", migrated_sessions
    )
    copied_counts["account_verification_runs"] = _copy_rows(
        target_conn, "account_verification_runs", migrated_runs
    )
    copied_counts["account_platform_capabilities"] = _copy_rows(
        target_conn, "account_platform_capabilities", migrated_capabilities
    )

    for table_name in (
        "alerts",
        "audit_logs",
        "lottery_results",
        "reconcile_records",
        "bet_order_platform_records",
        "backtest_tasks",
    ):
        rows = _read_all_rows(source_conn, table_name)
        copied_counts[table_name] = _copy_rows(target_conn, table_name, rows)

    strategy_target_columns = _table_columns(target_conn, "strategies")
    strategy_source_columns = set(_table_columns(source_conn, "strategies"))
    strategy_rows: list[dict[str, Any]] = []
    for row in _read_all_rows(source_conn, "strategies"):
        migrated = {column: row.get(column) for column in strategy_target_columns}
        if "gate_window_issues" not in strategy_source_columns:
            migrated["gate_window_issues"] = None
        strategy_rows.append(migrated)
    copied_counts["strategies"] = _copy_rows(
        target_conn,
        "strategies",
        strategy_rows,
        columns=strategy_target_columns,
    )

    bet_order_target_columns = _table_columns(target_conn, "bet_orders")
    bet_order_source_columns = set(_table_columns(source_conn, "bet_orders"))
    bet_order_rows: list[dict[str, Any]] = []
    for row in _read_all_rows(source_conn, "bet_orders"):
        migrated = {column: row.get(column) for column in bet_order_target_columns}
        if "actual_platform_type" not in bet_order_source_columns:
            migrated["actual_platform_type"] = strategy_platform_by_id.get(
                int(row["strategy_id"]),
                legacy_platform_by_account.get(int(row["account_id"])),
            )
        bet_order_rows.append(migrated)
    copied_counts["bet_orders"] = _copy_rows(
        target_conn,
        "bet_orders",
        bet_order_rows,
        columns=bet_order_target_columns,
    )

    account_odds_target_columns = _table_columns(target_conn, "account_odds")
    account_odds_source_columns = set(_table_columns(source_conn, "account_odds"))
    migrated_odds: list[dict[str, Any]] = []
    for row in _read_all_rows(source_conn, "account_odds"):
        platform_type = row.get("platform_type")
        if "platform_type" not in account_odds_source_columns:
            platform_type = legacy_platform_by_account.get(int(row["account_id"]))
        migrated_odds.append(
            {
                "id": row["id"],
                "account_id": row["account_id"],
                "platform_type": platform_type,
                "key_code": row["key_code"],
                "odds_value": row["odds_value"],
                "confirmed": row["confirmed"],
                "fetched_at": row["fetched_at"],
                "confirmed_at": row["confirmed_at"],
            }
        )
    copied_counts["account_odds"] = _copy_rows(
        target_conn,
        "account_odds",
        migrated_odds,
        columns=account_odds_target_columns,
    )

    target_conn.commit()
    return {
        "copied_counts": copied_counts,
        "legacy_platforms_by_account": legacy_platform_by_account,
    }


def migrate_schema_to_current(
    source_db_path: str,
    target_db_path: str,
    *,
    overwrite: bool = False,
    summary_out: str | None = None,
) -> dict[str, Any]:
    source_path = Path(source_db_path)
    target_path = Path(target_db_path)

    if not source_path.exists():
        raise MigrationError(f"Source database does not exist: {source_path}")
    if target_path.exists():
        if not overwrite:
            raise MigrationError(f"Target database already exists: {target_path}")
        target_path.unlink()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    source_conn = _connect(str(source_path))
    target_conn = _connect(str(target_path))

    try:
        source_schema_kind = _detect_schema_kind(source_conn)
        source_counts = _collect_table_counts(
            source_conn,
            LEGACY_SOURCE_TABLES if source_schema_kind == "legacy" else CURRENT_TABLE_ORDER,
        )

        target_conn.execute("PRAGMA foreign_keys=OFF")
        _create_current_schema(target_conn)

        if source_schema_kind == "legacy":
            migration_payload = _migrate_legacy_schema(source_conn, target_conn)
            copied_counts = migration_payload["copied_counts"]
        else:
            copied_counts = _copy_current_schema(source_conn, target_conn)

        target_conn.commit()
        target_conn.execute("PRAGMA foreign_keys=ON")
        fk_errors = [
            tuple(row)
            for row in target_conn.execute("PRAGMA foreign_key_check").fetchall()
        ]
        if fk_errors:
            raise MigrationError(f"Foreign key check failed: {fk_errors[:5]}")

        target_counts = _collect_table_counts(target_conn, CURRENT_TABLE_ORDER)
        summary = {
            "source_db_path": str(source_path),
            "target_db_path": str(target_path),
            "source_schema_kind": source_schema_kind,
            "source_counts": source_counts,
            "target_counts": target_counts,
            "copied_counts": copied_counts,
            "generated_at": _now_bjt(),
        }
        if summary_out:
            summary_path = Path(summary_out)
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
        return summary
    finally:
        source_conn.close()
        target_conn.close()
