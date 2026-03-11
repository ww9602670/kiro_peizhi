"""Task 1.1.4  


- 8 
- WAL 
-   RAISE ABORT
- idempotent_id UNIQUE   IntegrityError
- 
"""
import os
import tempfile

import pytest
import aiosqlite

from app.database import get_db, init_db, DDL_STATEMENTS, INSERT_DEFAULT_ADMIN

EXPECTED_TABLES = {
    "operators",
    "gambling_accounts",
    "strategies",
    "bet_orders",
    "alerts",
    "audit_logs",
    "lottery_results",
    "reconcile_records",
    "account_odds",
    "bet_order_platform_records",
}


@pytest.fixture
async def db():
    """ + """
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")

    for stmt in DDL_STATEMENTS:
        await conn.execute(stmt)
    await conn.execute(INSERT_DEFAULT_ADMIN)
    await conn.commit()

    yield conn
    await conn.close()


@pytest.fixture
async def file_db(tmp_path):
    """WAL """
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    conn = await get_db(db_path)
    yield conn
    await conn.close()


class TestTableCreation:
    """ 8 """

    async def test_all_tables_exist(self, db):
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        rows = await cursor.fetchall()
        table_names = {row["name"] for row in rows}
        assert EXPECTED_TABLES == table_names


class TestWALMode:
    """ WAL """

    async def test_wal_mode_enabled(self, file_db):
        cursor = await file_db.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        assert row[0] == "wal"


class TestDefaultAdmin:
    """"""

    async def test_admin_exists(self, db):
        cursor = await db.execute(
            "SELECT username, password, role, status FROM operators WHERE username = 'admin'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["username"] == "admin"
        assert row["password"] == "admin123"
        assert row["role"] == "admin"
        assert row["status"] == "active"

    async def test_admin_idempotent_insert(self, db):
        """INSERT OR IGNORE"""
        from app.database import INSERT_DEFAULT_ADMIN
        await db.execute(INSERT_DEFAULT_ADMIN)
        await db.commit()
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM operators WHERE username = 'admin'")
        row = await cursor.fetchone()
        assert row["cnt"] == 1


class TestTerminalStateTrigger:
    """bet_failed / settled / reconcile_error """

    async def _insert_bet_order(self, db, idempotent_id: str, status: str):
        """ operatoraccountstrategy"""
        #  admin (id=1)
        #  gambling_account
        await db.execute(
            """INSERT OR IGNORE INTO gambling_accounts
               (id, operator_id, account_name, password, platform_type)
               VALUES (1, 1, 'test_acc', 'pwd', 'JND28WEB')"""
        )
        #  strategy
        await db.execute(
            """INSERT OR IGNORE INTO strategies
               (id, operator_id, account_id, name, type, play_code, base_amount)
               VALUES (1, 1, 1, 'test_strat', 'flat', 'DX1', 1000)"""
        )
        #  bet_order
        await db.execute(
            """INSERT INTO bet_orders
               (idempotent_id, operator_id, account_id, strategy_id, issue, key_code, amount, status)
               VALUES (?, 1, 1, 1, '20250101001', 'DX1', 1000, ?)""",
            (idempotent_id, status),
        )
        await db.commit()

    @pytest.mark.parametrize("terminal_status", ["bet_failed", "settled", "reconcile_error"])
    async def test_update_terminal_state_rejected(self, db, terminal_status):
        """ UPDATE """
        idem_id = f"test-{terminal_status}"
        await self._insert_bet_order(db, idem_id, terminal_status)

        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                "UPDATE bet_orders SET status = 'pending' WHERE idempotent_id = ?",
                (idem_id,),
            )

    @pytest.mark.parametrize("terminal_status", ["bet_failed", "settled", "reconcile_error"])
    async def test_terminal_state_data_unchanged(self, db, terminal_status):
        """"""
        idem_id = f"unchanged-{terminal_status}"
        await self._insert_bet_order(db, idem_id, terminal_status)

        try:
            await db.execute(
                "UPDATE bet_orders SET status = 'pending' WHERE idempotent_id = ?",
                (idem_id,),
            )
        except aiosqlite.IntegrityError:
            pass

        cursor = await db.execute(
            "SELECT status FROM bet_orders WHERE idempotent_id = ?", (idem_id,)
        )
        row = await cursor.fetchone()
        assert row["status"] == terminal_status

    async def test_non_terminal_state_update_allowed(self, db):
        """"""
        await self._insert_bet_order(db, "non-terminal-test", "pending")
        await db.execute(
            "UPDATE bet_orders SET status = 'betting' WHERE idempotent_id = 'non-terminal-test'"
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT status FROM bet_orders WHERE idempotent_id = 'non-terminal-test'"
        )
        row = await cursor.fetchone()
        assert row["status"] == "betting"


class TestIdempotentIdUnique:
    """ idempotent_id UNIQUE """

    async def _setup_refs(self, db):
        await db.execute(
            """INSERT OR IGNORE INTO gambling_accounts
               (id, operator_id, account_name, password, platform_type)
               VALUES (1, 1, 'test_acc', 'pwd', 'JND28WEB')"""
        )
        await db.execute(
            """INSERT OR IGNORE INTO strategies
               (id, operator_id, account_id, name, type, play_code, base_amount)
               VALUES (1, 1, 1, 'test_strat', 'flat', 'DX1', 1000)"""
        )
        await db.commit()

    async def test_duplicate_idempotent_id_raises(self, db):
        """ idempotent_id  IntegrityError"""
        await self._setup_refs(db)
        idem_id = "20250101001-1-DX1"
        await db.execute(
            """INSERT INTO bet_orders
               (idempotent_id, operator_id, account_id, strategy_id, issue, key_code, amount)
               VALUES (?, 1, 1, 1, '20250101001', 'DX1', 1000)""",
            (idem_id,),
        )
        await db.commit()

        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                """INSERT INTO bet_orders
                   (idempotent_id, operator_id, account_id, strategy_id, issue, key_code, amount)
                   VALUES (?, 1, 1, 1, '20250101001', 'DX1', 2000)""",
                (idem_id,),
            )

    async def test_different_idempotent_ids_ok(self, db):
        """ idempotent_id """
        await self._setup_refs(db)
        for i in range(3):
            await db.execute(
                """INSERT INTO bet_orders
                   (idempotent_id, operator_id, account_id, strategy_id, issue, key_code, amount)
                   VALUES (?, 1, 1, 1, '20250101001', 'DX1', 1000)""",
                (f"unique-id-{i}",),
            )
        await db.commit()
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM bet_orders")
        row = await cursor.fetchone()
        assert row["cnt"] == 3


# 
# PBT: P24   DB 
# 

from hypothesis import given, settings, strategies as st


# All valid bet_order statuses
ALL_STATUSES = [
    "pending", "betting", "bet_success", "bet_failed",
    "settling", "settled", "reconcile_error",
    "pending_match", "settle_timeout", "settle_failed",
]
TERMINAL_STATUSES = ["bet_failed", "settled", "reconcile_error"]


class TestPBT_P24_TerminalStateTrigger:
    """P24:  DB  UPDATE

    **Validates: Requirements 7.0**

    Property: For any terminal state and any target state, a direct SQL UPDATE
    on a terminal-state bet_order raises IntegrityError and leaves data unchanged.
    """

    @given(
        terminal_state=st.sampled_from(TERMINAL_STATUSES),
        target_state=st.sampled_from(ALL_STATUSES),
    )
    @settings(max_examples=200)
    def test_pbt_terminal_state_update_rejected(
        self, terminal_state: str, target_state: str
    ):
        """Direct SQL UPDATE on terminal-state row  RAISE ABORT, data unchanged.

        **Validates: Requirements 7.0**
        """
        import asyncio

        async def _run():
            conn = await aiosqlite.connect(":memory:")
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys=ON")
            for stmt in DDL_STATEMENTS:
                await conn.execute(stmt)
            await conn.execute(INSERT_DEFAULT_ADMIN)
            await conn.commit()

            # Setup: create required foreign key rows
            await conn.execute(
                """INSERT OR IGNORE INTO gambling_accounts
                   (id, operator_id, account_name, password, platform_type)
                   VALUES (1, 1, 'test_acc', 'pwd', 'JND28WEB')"""
            )
            await conn.execute(
                """INSERT OR IGNORE INTO strategies
                   (id, operator_id, account_id, name, type, play_code, base_amount)
                   VALUES (1, 1, 1, 'test_strat', 'flat', 'DX1', 1000)"""
            )

            # Insert a bet_order in terminal state
            idem_id = f"pbt-{terminal_state}-{target_state}"
            await conn.execute(
                """INSERT INTO bet_orders
                   (idempotent_id, operator_id, account_id, strategy_id,
                    issue, key_code, amount, status)
                   VALUES (?, 1, 1, 1, '20250101001', 'DX1', 1000, ?)""",
                (idem_id, terminal_state),
            )
            await conn.commit()

            # Attempt UPDATE  must raise IntegrityError
            with pytest.raises(aiosqlite.IntegrityError):
                await conn.execute(
                    "UPDATE bet_orders SET status=? WHERE idempotent_id=?",
                    (target_state, idem_id),
                )

            # Verify data unchanged
            cursor = await conn.execute(
                "SELECT status FROM bet_orders WHERE idempotent_id=?",
                (idem_id,),
            )
            row = await cursor.fetchone()
            assert row["status"] == terminal_state, (
                f"Expected status={terminal_state} unchanged, got {row['status']}"
            )

            await conn.close()

        asyncio.get_event_loop().run_until_complete(_run())
