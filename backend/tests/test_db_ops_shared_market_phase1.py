import aiosqlite
import pytest

from app.database import DDL_STATEMENTS, INSERT_DEFAULT_ADMIN
from app.models.db_ops import (
    shared_market_group_resolve_by_url,
    shared_market_snapshot_get_latest,
    shared_market_snapshot_upsert,
    shared_market_uncovered_url_touch,
    simulation_bet_order_create,
    simulation_bet_order_update,
    simulation_strategy_stats_upsert,
)


@pytest.fixture
async def db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")
    for stmt in DDL_STATEMENTS:
        await conn.execute(stmt)
    await conn.execute(INSERT_DEFAULT_ADMIN)
    await conn.commit()
    yield conn
    await conn.close()


async def _seed_operator_account_strategy(
    db: aiosqlite.Connection,
) -> tuple[int, int, int]:
    now = "2026-04-20 00:00:00"
    op_cursor = await db.execute(
        "INSERT INTO operators (username, password, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("phase1-op", "pwd", now, now),
    )
    operator_id = int(op_cursor.lastrowid)

    account_cursor = await db.execute(
        """INSERT INTO gambling_accounts
           (operator_id, account_name, password, game_type, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (operator_id, "phase1-acc", "pwd", "JND28WEB", now, now),
    )
    account_id = int(account_cursor.lastrowid)

    strategy_cursor = await db.execute(
        """INSERT INTO strategies
           (operator_id, account_id, name, type, play_code, base_amount, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (operator_id, account_id, "phase1-strategy", "flat", "DX1", 100, now, now),
    )
    strategy_id = int(strategy_cursor.lastrowid)
    await db.commit()
    return operator_id, account_id, strategy_id


async def _seed_shared_group(
    db: aiosqlite.Connection,
    *,
    group_key: str,
    enabled: int = 1,
) -> int:
    now = "2026-04-20 00:00:00"
    cursor = await db.execute(
        """INSERT INTO shared_market_groups
           (group_key, enabled, collector_platform_type, collector_account_name,
            collector_password_enc, freshness_threshold_sec, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (group_key, enabled, "JND28WEB", "collector", "enc_pwd", 30, now, now),
    )
    await db.commit()
    return int(cursor.lastrowid)


class TestPhase1Schema:
    async def test_phase1_tables_exist(self, db):
        rows = await (
            await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ).fetchall()
        table_names = {row["name"] for row in rows}
        assert {
            "shared_market_groups",
            "shared_market_group_urls",
            "shared_market_snapshots",
            "shared_market_uncovered_urls",
            "simulation_bet_orders",
            "simulation_strategy_stats",
        }.issubset(table_names)


class TestSharedMarketContracts:
    async def test_shared_market_group_resolve_by_url(self, db):
        enabled_group_id = await _seed_shared_group(db, group_key="enabled-group", enabled=1)
        disabled_group_id = await _seed_shared_group(db, group_key="disabled-group", enabled=0)
        now = "2026-04-20 01:00:00"
        await db.execute(
            """INSERT INTO shared_market_group_urls
               (shared_group_id, normalized_url, created_at, updated_at)
               VALUES (?, ?, ?, ?)""",
            (enabled_group_id, "https://pool.example/a", now, now),
        )
        await db.execute(
            """INSERT INTO shared_market_group_urls
               (shared_group_id, normalized_url, created_at, updated_at)
               VALUES (?, ?, ?, ?)""",
            (disabled_group_id, "https://pool.example/disabled", now, now),
        )
        await db.commit()

        hit = await shared_market_group_resolve_by_url(
            db,
            normalized_url="https://pool.example/a",
        )
        assert hit is not None
        assert hit["shared_group_id"] == enabled_group_id

        miss = await shared_market_group_resolve_by_url(
            db,
            normalized_url="https://pool.example/miss",
        )
        assert miss is None

        disabled_miss = await shared_market_group_resolve_by_url(
            db,
            normalized_url="https://pool.example/disabled",
        )
        assert disabled_miss is None

        disabled_hit = await shared_market_group_resolve_by_url(
            db,
            normalized_url="https://pool.example/disabled",
            only_enabled=False,
        )
        assert disabled_hit is not None
        assert disabled_hit["shared_group_id"] == disabled_group_id

    async def test_shared_market_snapshot_upsert_and_get_latest(self, db):
        group_id = await _seed_shared_group(db, group_key="snapshot-group", enabled=1)

        first = await shared_market_snapshot_upsert(
            db,
            shared_group_id=group_id,
            issue="20260420001",
            state="OPEN",
            close_countdown_sec=20,
            open_countdown_sec=90,
            pre_issue="20260420000",
            open_result="1,2,3",
            fetched_at="2026-04-20 10:00:00",
            source_status="online",
        )
        assert first["issue"] == "20260420001"

        second = await shared_market_snapshot_upsert(
            db,
            shared_group_id=group_id,
            issue="20260420002",
            state="CLOSE",
            close_countdown_sec=5,
            open_countdown_sec=60,
            pre_issue="20260420001",
            open_result="2,2,2",
            fetched_at="2026-04-20 10:01:00",
            source_status="stale",
            last_error="timeout",
        )
        assert second["issue"] == "20260420002"
        assert second["source_status"] == "stale"

        latest = await shared_market_snapshot_get_latest(db, shared_group_id=group_id)
        assert latest is not None
        assert latest["issue"] == "20260420002"
        assert latest["last_error"] == "timeout"

        count_row = await (
            await db.execute(
                "SELECT COUNT(*) AS cnt FROM shared_market_snapshots WHERE shared_group_id=?",
                (group_id,),
            )
        ).fetchone()
        assert count_row is not None
        assert count_row["cnt"] == 1

    async def test_shared_market_uncovered_url_touch_aggregates(self, db):
        first = await shared_market_uncovered_url_touch(
            db,
            normalized_url="https://unknown.example/route",
            sample_raw_url="https://unknown.example/route?a=1",
            last_account_id=101,
            last_platform_type="JND28WEB",
            seen_at="2026-04-20 09:00:00",
        )
        assert first["hit_count"] == 1
        assert first["first_seen_at"] == "2026-04-20 09:00:00"
        assert first["last_seen_at"] == "2026-04-20 09:00:00"

        second = await shared_market_uncovered_url_touch(
            db,
            normalized_url="https://unknown.example/route",
            sample_raw_url="https://unknown.example/route?a=2",
            last_account_id=102,
            last_platform_type="JND282",
            seen_at="2026-04-20 10:00:00",
        )
        assert second["hit_count"] == 2
        assert second["first_seen_at"] == "2026-04-20 09:00:00"
        assert second["last_seen_at"] == "2026-04-20 10:00:00"
        assert second["last_account_id"] == 102
        assert second["last_platform_type"] == "JND282"


class TestSimulationContracts:
    async def test_simulation_bet_order_create_and_update(self, db):
        operator_id, account_id, strategy_id = await _seed_operator_account_strategy(db)

        created = await simulation_bet_order_create(
            db,
            idempotent_id="sim-order-001",
            operator_id=operator_id,
            account_id=account_id,
            strategy_id=strategy_id,
            issue="20260420008",
            platform_type="JND28WEB",
            key_code="DX1",
            amount=1000,
            odds=1980,
            status="pending",
        )
        assert created["status"] == "pending"
        assert created["idempotent_id"] == "sim-order-001"

        updated = await simulation_bet_order_update(
            db,
            order_id=created["id"],
            operator_id=operator_id,
            status="settled",
            is_win=1,
            pnl=980,
            open_result="1,3,6",
            sum_value=10,
            settled_at="2026-04-20 11:00:00",
        )
        assert updated is not None
        assert updated["status"] == "settled"
        assert updated["is_win"] == 1
        assert updated["pnl"] == 980

        wrong_operator = await simulation_bet_order_update(
            db,
            order_id=created["id"],
            operator_id=99999,
            status="bet_success",
        )
        assert wrong_operator is None

    async def test_simulation_strategy_stats_upsert_aggregate(self, db):
        operator_id, account_id, strategy_id = await _seed_operator_account_strategy(db)

        first = await simulation_strategy_stats_upsert(
            db,
            strategy_id=strategy_id,
            operator_id=operator_id,
            account_id=account_id,
            daily_pnl_delta=100,
            total_pnl_delta=100,
            bet_count_delta=1,
            win_count_delta=1,
            daily_pnl_date="2026-04-20",
            settled_at="2026-04-20 12:00:00",
        )
        assert first["daily_pnl"] == 100
        assert first["total_pnl"] == 100
        assert first["bet_count"] == 1

        second = await simulation_strategy_stats_upsert(
            db,
            strategy_id=strategy_id,
            operator_id=operator_id,
            account_id=account_id,
            daily_pnl_delta=-30,
            total_pnl_delta=-30,
            bet_count_delta=1,
            loss_count_delta=1,
            daily_pnl_date="2026-04-20",
            settled_at="2026-04-20 12:30:00",
        )
        assert second["daily_pnl"] == 70
        assert second["total_pnl"] == 70
        assert second["bet_count"] == 2
        assert second["win_count"] == 1
        assert second["loss_count"] == 1

        third = await simulation_strategy_stats_upsert(
            db,
            strategy_id=strategy_id,
            operator_id=operator_id,
            account_id=account_id,
            daily_pnl_delta=20,
            total_pnl_delta=20,
            bet_count_delta=1,
            win_count_delta=1,
            daily_pnl_date="2026-04-21",
            settled_at="2026-04-21 09:00:00",
        )
        assert third["daily_pnl_date"] == "2026-04-21"
        assert third["daily_pnl"] == 20
        assert third["total_pnl"] == 90
        assert third["bet_count"] == 3
        assert third["win_count"] == 2
        assert third["loss_count"] == 1
        assert third["last_settled_at"] == "2026-04-21 09:00:00"
