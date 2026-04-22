from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.legacy_platform_schema_migration import migrate_schema_to_current


LEGACY_DDL = [
    """
    CREATE TABLE operators (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'operator',
        status TEXT NOT NULL DEFAULT 'active',
        max_accounts INTEGER NOT NULL DEFAULT 1,
        expire_date TEXT,
        current_jti TEXT,
        created_by INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE gambling_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operator_id INTEGER NOT NULL,
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
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(operator_id, account_name, platform_type)
    );
    """,
    """
    CREATE TABLE strategies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operator_id INTEGER NOT NULL,
        account_id INTEGER NOT NULL,
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
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE bet_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        idempotent_id TEXT NOT NULL UNIQUE,
        operator_id INTEGER NOT NULL,
        account_id INTEGER NOT NULL,
        strategy_id INTEGER NOT NULL,
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
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operator_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        level TEXT NOT NULL DEFAULT 'warning',
        title TEXT NOT NULL,
        detail TEXT,
        is_read INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operator_id INTEGER,
        action TEXT NOT NULL,
        target_type TEXT,
        target_id INTEGER,
        detail TEXT,
        ip_address TEXT,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE lottery_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue TEXT NOT NULL UNIQUE,
        open_result TEXT NOT NULL,
        sum_value INTEGER NOT NULL,
        open_time TEXT,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE reconcile_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        issue TEXT NOT NULL,
        local_bet_count INTEGER NOT NULL,
        platform_bet_count INTEGER,
        local_balance INTEGER,
        platform_balance INTEGER,
        diff_amount INTEGER,
        status TEXT NOT NULL DEFAULT 'pending',
        detail TEXT,
        resolved_by TEXT,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE bet_order_platform_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue TEXT NOT NULL,
        key_code TEXT NOT NULL,
        amount INTEGER NOT NULL,
        win_amount INTEGER NOT NULL,
        raw_json TEXT,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE account_odds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        key_code TEXT NOT NULL,
        odds_value INTEGER NOT NULL,
        confirmed INTEGER NOT NULL DEFAULT 0,
        fetched_at TEXT NOT NULL,
        confirmed_at TEXT,
        UNIQUE(account_id, key_code)
    );
    """,
    """
    CREATE TABLE backtest_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operator_id INTEGER NOT NULL,
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
        created_at TEXT NOT NULL,
        completed_at TEXT
    );
    """,
]


def _build_legacy_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        for stmt in LEGACY_DDL:
            conn.execute(stmt)

        conn.execute(
            """
            INSERT INTO operators
            (id, username, password, role, status, max_accounts, expire_date, current_jti, created_by, created_at, updated_at)
            VALUES
            (1, 'admin', 'admin123', 'admin', 'active', 10, NULL, NULL, NULL, '2026-04-20 10:00:00', '2026-04-20 10:00:00'),
            (13, 'jie01', 'pw1', 'operator', 'active', 3, NULL, NULL, 1, '2026-04-21 10:00:00', '2026-04-22 03:29:12')
            """
        )
        conn.execute(
            """
            INSERT INTO gambling_accounts
            (id, operator_id, account_name, password, platform_type, platform_url, status, session_token,
             balance, login_fail_count, last_login_at, kill_switch, single_bet_limit, daily_limit, period_limit,
             worker_lock_token, worker_lock_ts, created_at, updated_at)
            VALUES
            (14, 13, 'shua10', 'accpw', 'JND28WEB', 'https://8783288200-bty.mm555.co/', 'online', 'token-a',
             2100, 0, '2026-04-19 18:57:03', 0, NULL, NULL, NULL,
             'lock-token-1', '2026-04-22 06:52:15', '2026-04-17 18:34:46', '2026-04-22 03:29:34')
            """
        )
        conn.execute(
            """
            INSERT INTO strategies
            (id, operator_id, account_id, name, type, play_code, base_amount, martin_sequence, bet_timing,
             simulation, status, martin_level, stop_loss, take_profit, daily_pnl, total_pnl, daily_pnl_date,
             platform_type, created_at, updated_at)
            VALUES
            (72, 13, 14, '1,3,5,', 'red_wave_double_martin', 'B1LM_S,B2LM_S,B3LM_S,DS4', 300, '[1.0,3.0,9.0,20.0,30.0]',
             30, 0, 'running', 0, NULL, NULL, -89215, -89215, '2026-04-19', 'JND28WEB',
             '2026-04-19 13:20:57', '2026-04-22 03:29:34')
            """
        )
        conn.execute(
            """
            INSERT INTO bet_orders
            (id, idempotent_id, operator_id, account_id, strategy_id, issue, key_code, amount, odds, status,
             bet_response, open_result, sum_value, is_win, pnl, simulation, martin_level, bet_at, settled_at,
             fail_reason, match_source, pending_match_count, created_at)
            VALUES
            (1001, 'legacy-1001', 13, 14, 72, '3423366', 'DS4', 300, NULL, 'bet_failed',
             NULL, NULL, NULL, NULL, NULL, 0, 0, '2026-04-22 03:29:10', '2026-04-22 03:29:11',
             '余额不足', 'platform', 1, '2026-04-22 03:29:10')
            """
        )
        conn.execute(
            """
            INSERT INTO account_odds
            (id, account_id, key_code, odds_value, confirmed, fetched_at, confirmed_at)
            VALUES
            (1, 14, 'DS4', 1980, 1, '2026-04-19 18:57:05', '2026-04-19 18:57:06'),
            (2, 14, 'B1LM_S', 1980, 0, '2026-04-19 18:57:05', NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO alerts
            (id, operator_id, type, level, title, detail, is_read, created_at)
            VALUES (10, 13, 'info', 'warning', 'legacy alert', 'detail', 0, '2026-04-22 03:00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO backtest_tasks
            (id, operator_id, strategy_type, key_codes, base_amount, martin_sequence, odds_map, start_issue, end_issue,
             status, error_message, result_json, total_issues, processed_issues, created_at, completed_at)
            VALUES
            (5, 13, 'flat', 'DS4', 100, '[1,2,4]', '{\"DS4\":1980}', '100', '200', 'completed', NULL, '{}', 100, 100,
             '2026-04-20 00:00:00', '2026-04-20 00:10:00')
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_migrate_legacy_schema_builds_current_db(tmp_path):
    source_db = tmp_path / "legacy.db"
    target_db = tmp_path / "migrated.db"
    summary_json = tmp_path / "summary.json"
    _build_legacy_db(source_db)

    summary = migrate_schema_to_current(
        str(source_db),
        str(target_db),
        summary_out=str(summary_json),
    )

    assert summary["source_schema_kind"] == "legacy"
    assert summary["target_counts"]["gambling_accounts"] == 1
    assert summary["target_counts"]["account_platform_sessions"] == 1
    assert summary["target_counts"]["account_verification_runs"] == 1
    assert summary["target_counts"]["account_platform_capabilities"] == 1
    assert summary["target_counts"]["account_odds"] == 2

    conn = sqlite3.connect(target_db)
    conn.row_factory = sqlite3.Row
    try:
        account_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(gambling_accounts)").fetchall()
        }
        assert "game_type" in account_cols
        assert "platform_type" not in account_cols

        account_row = conn.execute(
            "SELECT id, account_name, game_type, status, balance FROM gambling_accounts WHERE id=14"
        ).fetchone()
        assert dict(account_row) == {
            "id": 14,
            "account_name": "shua10",
            "game_type": "JND28",
            "status": "online",
            "balance": 2100,
        }

        session_row = conn.execute(
            """
            SELECT account_id, platform_type, status, worker_lock_token, worker_lock_ts
            FROM account_platform_sessions
            WHERE account_id=14
            """
        ).fetchone()
        assert dict(session_row) == {
            "account_id": 14,
            "platform_type": "JND28WEB",
            "status": "online",
            "worker_lock_token": "lock-token-1",
            "worker_lock_ts": "2026-04-22 06:52:15",
        }

        capability_row = conn.execute(
            """
            SELECT c.platform_type, c.verify_status, c.odds_synced, c.odds_count
            FROM account_platform_capabilities c
            JOIN account_verification_runs r ON r.id = c.verification_run_id
            WHERE r.account_id=14
            """
        ).fetchone()
        assert dict(capability_row) == {
            "platform_type": "JND28WEB",
            "verify_status": "supported",
            "odds_synced": 1,
            "odds_count": 2,
        }

        bet_order_row = conn.execute(
            "SELECT actual_platform_type FROM bet_orders WHERE id=1001"
        ).fetchone()
        assert bet_order_row["actual_platform_type"] == "JND28WEB"

        odds_rows = conn.execute(
            "SELECT platform_type, key_code, odds_value FROM account_odds ORDER BY id"
        ).fetchall()
        assert [dict(row) for row in odds_rows] == [
            {"platform_type": "JND28WEB", "key_code": "DS4", "odds_value": 1980},
            {"platform_type": "JND28WEB", "key_code": "B1LM_S", "odds_value": 1980},
        ]

        new_account = conn.execute(
            """
            INSERT INTO gambling_accounts
            (operator_id, account_name, password, game_type, platform_url, status, balance, login_fail_count,
             kill_switch, created_at, updated_at)
            VALUES (13, 'newacc', 'pw2', 'JND28', 'https://example.com', 'inactive', 0, 0, 0,
                    '2026-04-22 07:00:00', '2026-04-22 07:00:00')
            """
        )
        conn.commit()
        assert new_account.lastrowid > 14
    finally:
        conn.close()

    summary_payload = json.loads(summary_json.read_text(encoding="utf-8"))
    assert summary_payload["target_counts"]["strategies"] == 1
    assert summary_payload["target_counts"]["bet_orders"] == 1


def test_migrate_legacy_schema_rejects_existing_target_without_overwrite(tmp_path):
    source_db = tmp_path / "legacy.db"
    target_db = tmp_path / "migrated.db"
    _build_legacy_db(source_db)
    target_db.write_bytes(b"placeholder")

    with pytest.raises(Exception):
        migrate_schema_to_current(str(source_db), str(target_db))
