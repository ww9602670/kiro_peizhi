"""Task 1.3.9  CRUD


-  CRUD 
-  A  B  CRUD 
- operator_id 
"""
import pytest
import aiosqlite

from app.database import DDL_STATEMENTS, INSERT_DEFAULT_ADMIN
from app.models.db_ops import (
    # operators
    operator_create, operator_get_by_id, operator_get_by_username,
    operator_list_all, operator_update, operator_update_status,
    # gambling_accounts
    account_create, account_get_by_id, account_list_by_operator,
    account_update, account_delete,
    # strategies
    strategy_create, strategy_get_by_id, strategy_list_by_operator,
    strategy_update, strategy_delete, strategy_update_status, strategy_update_pnl,
    # bet_orders
    bet_order_create, bet_order_get_by_id, bet_order_list_by_operator,
    bet_order_update_status,
    # alerts
    alert_create, alert_list_by_operator, alert_mark_read,
    alert_mark_all_read, alert_get_unread_count,
    # audit_logs
    audit_log_create, audit_log_list_by_operator,
    # lottery_results
    lottery_result_get_by_issue, lottery_result_list_recent, lottery_result_save,
    # reconcile_records
    reconcile_record_create, reconcile_record_list_by_account,
)


@pytest.fixture
async def db():
    """"""
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
async def two_operators(db):
    """"""
    op_a = await operator_create(db, username="op_a", password="pass_a", created_by=1)
    op_b = await operator_create(db, username="op_b", password="pass_b", created_by=1)
    return op_a, op_b


# 
# 1. operators CRUD
# 

class TestOperatorsCRUD:

    async def test_create_and_get_by_id(self, db):
        op = await operator_create(db, username="user1", password="pwd123", created_by=1)
        assert op["username"] == "user1"
        assert op["role"] == "operator"
        assert op["status"] == "active"

        fetched = await operator_get_by_id(db, operator_id=op["id"])
        assert fetched is not None
        assert fetched["username"] == "user1"

    async def test_get_by_username(self, db):
        await operator_create(db, username="findme", password="pwd", created_by=1)
        found = await operator_get_by_username(db, username="findme")
        assert found is not None
        assert found["username"] == "findme"

        not_found = await operator_get_by_username(db, username="ghost")
        assert not_found is None

    async def test_list_all(self, db):
        await operator_create(db, username="u1", password="p", created_by=1)
        await operator_create(db, username="u2", password="p", created_by=1)
        all_ops = await operator_list_all(db)
        # admin + u1 + u2
        assert len(all_ops) >= 3

    async def test_update(self, db):
        op = await operator_create(db, username="upd", password="p", created_by=1)
        updated = await operator_update(db, operator_id=op["id"], max_accounts=5)
        assert updated["max_accounts"] == 5

    async def test_update_status(self, db):
        op = await operator_create(db, username="st", password="p", created_by=1)
        updated = await operator_update_status(db, operator_id=op["id"], status="disabled")
        assert updated["status"] == "disabled"

    async def test_get_nonexistent(self, db):
        result = await operator_get_by_id(db, operator_id=99999)
        assert result is None


# 
# 2. gambling_accounts CRUD
# 

class TestAccountsCRUD:

    async def test_create_and_get(self, db, two_operators):
        op_a, _ = two_operators
        acc = await account_create(
            db, operator_id=op_a["id"],
            account_name="acc1", password="pwd", platform_type="JND28WEB",
        )
        assert acc["operator_id"] == op_a["id"]
        assert acc["account_name"] == "acc1"
        assert acc["status"] == "inactive"

        fetched = await account_get_by_id(db, account_id=acc["id"], operator_id=op_a["id"])
        assert fetched is not None

    async def test_list_by_operator(self, db, two_operators):
        op_a, _ = two_operators
        await account_create(db, operator_id=op_a["id"], account_name="a1", password="p", platform_type="JND28WEB")
        await account_create(db, operator_id=op_a["id"], account_name="a2", password="p", platform_type="JND282")
        accs = await account_list_by_operator(db, operator_id=op_a["id"])
        assert len(accs) == 2

    async def test_update(self, db, two_operators):
        op_a, _ = two_operators
        acc = await account_create(db, operator_id=op_a["id"], account_name="u", password="p", platform_type="JND28WEB")
        updated = await account_update(db, account_id=acc["id"], operator_id=op_a["id"], balance=50000)
        assert updated["balance"] == 50000

    async def test_delete(self, db, two_operators):
        op_a, _ = two_operators
        acc = await account_create(db, operator_id=op_a["id"], account_name="del", password="p", platform_type="JND28WEB")
        deleted = await account_delete(db, account_id=acc["id"], operator_id=op_a["id"])
        assert deleted is True
        assert await account_get_by_id(db, account_id=acc["id"], operator_id=op_a["id"]) is None

    async def test_get_by_id_wrong_operator(self, db, two_operators):
        """operator_id  None"""
        op_a, op_b = two_operators
        acc = await account_create(db, operator_id=op_a["id"], account_name="x", password="p", platform_type="JND28WEB")
        result = await account_get_by_id(db, account_id=acc["id"], operator_id=op_b["id"])
        assert result is None


# 
# 3. strategies CRUD
# 

class TestStrategiesCRUD:

    async def _make_account(self, db, operator_id):
        return await account_create(
            db, operator_id=operator_id,
            account_name=f"acc_{operator_id}", password="p", platform_type="JND28WEB",
        )

    async def test_create_and_get(self, db, two_operators):
        op_a, _ = two_operators
        acc = await self._make_account(db, op_a["id"])
        strat = await strategy_create(
            db, operator_id=op_a["id"], account_id=acc["id"],
            name="flat1", type="flat", play_code="DX1", base_amount=1000,
        )
        assert strat["name"] == "flat1"
        assert strat["status"] == "stopped"

        fetched = await strategy_get_by_id(db, strategy_id=strat["id"], operator_id=op_a["id"])
        assert fetched is not None

    async def test_list_by_operator(self, db, two_operators):
        op_a, _ = two_operators
        acc = await self._make_account(db, op_a["id"])
        await strategy_create(db, operator_id=op_a["id"], account_id=acc["id"], name="s1", type="flat", play_code="DX1", base_amount=100)
        await strategy_create(db, operator_id=op_a["id"], account_id=acc["id"], name="s2", type="martin", play_code="DS3", base_amount=200)
        strats = await strategy_list_by_operator(db, operator_id=op_a["id"])
        assert len(strats) == 2

    async def test_update_and_delete(self, db, two_operators):
        op_a, _ = two_operators
        acc = await self._make_account(db, op_a["id"])
        strat = await strategy_create(db, operator_id=op_a["id"], account_id=acc["id"], name="d", type="flat", play_code="DX1", base_amount=100)
        updated = await strategy_update(db, strategy_id=strat["id"], operator_id=op_a["id"], name="renamed")
        assert updated["name"] == "renamed"

        deleted = await strategy_delete(db, strategy_id=strat["id"], operator_id=op_a["id"])
        assert deleted is True

    async def test_update_status(self, db, two_operators):
        op_a, _ = two_operators
        acc = await self._make_account(db, op_a["id"])
        strat = await strategy_create(db, operator_id=op_a["id"], account_id=acc["id"], name="st", type="flat", play_code="DX1", base_amount=100)
        updated = await strategy_update_status(db, strategy_id=strat["id"], operator_id=op_a["id"], status="running")
        assert updated["status"] == "running"

    async def test_update_pnl(self, db, two_operators):
        op_a, _ = two_operators
        acc = await self._make_account(db, op_a["id"])
        strat = await strategy_create(db, operator_id=op_a["id"], account_id=acc["id"], name="pnl", type="flat", play_code="DX1", base_amount=100)
        updated = await strategy_update_pnl(
            db, strategy_id=strat["id"], operator_id=op_a["id"],
            daily_pnl=-5000, total_pnl=10000, daily_pnl_date="2025-01-01",
        )
        assert updated["daily_pnl"] == -5000
        assert updated["total_pnl"] == 10000


# 
# 4. bet_orders CRUD
# 

class TestBetOrdersCRUD:

    async def _setup(self, db, operator_id):
        acc = await account_create(
            db, operator_id=operator_id,
            account_name=f"acc_{operator_id}", password="p", platform_type="JND28WEB",
        )
        strat = await strategy_create(
            db, operator_id=operator_id, account_id=acc["id"],
            name="s", type="flat", play_code="DX1", base_amount=100,
        )
        return acc, strat

    async def test_create_and_get(self, db, two_operators):
        op_a, _ = two_operators
        acc, strat = await self._setup(db, op_a["id"])
        order = await bet_order_create(
            db, idempotent_id="001-1-DX1", operator_id=op_a["id"],
            account_id=acc["id"], strategy_id=strat["id"],
            issue="001", key_code="DX1", amount=1000,
        )
        assert order["idempotent_id"] == "001-1-DX1"
        assert order["status"] == "pending"

        fetched = await bet_order_get_by_id(db, order_id=order["id"], operator_id=op_a["id"])
        assert fetched is not None

    async def test_duplicate_idempotent_id(self, db, two_operators):
        op_a, _ = two_operators
        acc, strat = await self._setup(db, op_a["id"])
        await bet_order_create(
            db, idempotent_id="dup-1", operator_id=op_a["id"],
            account_id=acc["id"], strategy_id=strat["id"],
            issue="001", key_code="DX1", amount=1000,
        )
        with pytest.raises(Exception):  # IntegrityError
            await bet_order_create(
                db, idempotent_id="dup-1", operator_id=op_a["id"],
                account_id=acc["id"], strategy_id=strat["id"],
                issue="001", key_code="DX1", amount=2000,
            )

    async def test_list_with_pagination(self, db, two_operators):
        op_a, _ = two_operators
        acc, strat = await self._setup(db, op_a["id"])
        for i in range(5):
            await bet_order_create(
                db, idempotent_id=f"page-{i}", operator_id=op_a["id"],
                account_id=acc["id"], strategy_id=strat["id"],
                issue=f"00{i}", key_code="DX1", amount=100,
            )
        items, total = await bet_order_list_by_operator(db, operator_id=op_a["id"], page=1, page_size=3)
        assert total == 5
        assert len(items) == 3

        items2, _ = await bet_order_list_by_operator(db, operator_id=op_a["id"], page=2, page_size=3)
        assert len(items2) == 2

    async def test_list_with_strategy_filter(self, db, two_operators):
        op_a, _ = two_operators
        acc, strat = await self._setup(db, op_a["id"])
        strat2 = await strategy_create(
            db, operator_id=op_a["id"], account_id=acc["id"],
            name="s2", type="martin", play_code="DS3", base_amount=200,
        )
        await bet_order_create(
            db, idempotent_id="f1", operator_id=op_a["id"],
            account_id=acc["id"], strategy_id=strat["id"],
            issue="001", key_code="DX1", amount=100,
        )
        await bet_order_create(
            db, idempotent_id="f2", operator_id=op_a["id"],
            account_id=acc["id"], strategy_id=strat2["id"],
            issue="001", key_code="DS3", amount=200,
        )
        items, total = await bet_order_list_by_operator(
            db, operator_id=op_a["id"], strategy_id=strat2["id"],
        )
        assert total == 1
        assert items[0]["key_code"] == "DS3"

    async def test_update_status(self, db, two_operators):
        op_a, _ = two_operators
        acc, strat = await self._setup(db, op_a["id"])
        order = await bet_order_create(
            db, idempotent_id="upd-1", operator_id=op_a["id"],
            account_id=acc["id"], strategy_id=strat["id"],
            issue="001", key_code="DX1", amount=100,
        )
        updated = await bet_order_update_status(
            db, order_id=order["id"], operator_id=op_a["id"],
            status="betting",
        )
        assert updated["status"] == "betting"


# 
# 5. alerts CRUD
# 

class TestAlertsCRUD:

    async def test_create_and_list(self, db, two_operators):
        op_a, _ = two_operators
        alert = await alert_create(
            db, operator_id=op_a["id"], type="login_fail",
            level="critical", title="",
        )
        assert alert["type"] == "login_fail"
        assert alert["is_read"] == 0

        items, total = await alert_list_by_operator(db, operator_id=op_a["id"])
        assert total == 1

    async def test_mark_read(self, db, two_operators):
        op_a, _ = two_operators
        alert = await alert_create(db, operator_id=op_a["id"], type="bet_fail", title="")
        result = await alert_mark_read(db, alert_id=alert["id"], operator_id=op_a["id"])
        assert result is True

        count = await alert_get_unread_count(db, operator_id=op_a["id"])
        assert count == 0

    async def test_mark_all_read(self, db, two_operators):
        op_a, _ = two_operators
        await alert_create(db, operator_id=op_a["id"], type="a", title="t1")
        await alert_create(db, operator_id=op_a["id"], type="b", title="t2")
        assert await alert_get_unread_count(db, operator_id=op_a["id"]) == 2

        marked = await alert_mark_all_read(db, operator_id=op_a["id"])
        assert marked == 2
        assert await alert_get_unread_count(db, operator_id=op_a["id"]) == 0

    async def test_filter_by_read_status(self, db, two_operators):
        op_a, _ = two_operators
        await alert_create(db, operator_id=op_a["id"], type="a", title="t1")
        a2 = await alert_create(db, operator_id=op_a["id"], type="b", title="t2")
        await alert_mark_read(db, alert_id=a2["id"], operator_id=op_a["id"])

        unread, _ = await alert_list_by_operator(db, operator_id=op_a["id"], is_read=0)
        assert len(unread) == 1
        read, _ = await alert_list_by_operator(db, operator_id=op_a["id"], is_read=1)
        assert len(read) == 1


# 
# 6. audit_logs CRUD
# 

class TestAuditLogsCRUD:

    async def test_create_and_list(self, db, two_operators):
        op_a, _ = two_operators
        log = await audit_log_create(
            db, operator_id=op_a["id"], action="login",
            target_type="operator", target_id=op_a["id"],
            detail='{"ip": "127.0.0.1"}', ip_address="127.0.0.1",
        )
        assert log["action"] == "login"

        items, total = await audit_log_list_by_operator(db, operator_id=op_a["id"])
        assert total == 1
        assert items[0]["action"] == "login"


# 
# 7. lottery_results CRUD
# 

class TestLotteryResultsCRUD:

    async def test_save_and_get(self, db):
        result = await lottery_result_save(
            db, issue="20250101001", open_result="3,5,7", sum_value=15,
        )
        assert result["issue"] == "20250101001"
        assert result["sum_value"] == 15

        fetched = await lottery_result_get_by_issue(db, issue="20250101001")
        assert fetched is not None
        assert fetched["open_result"] == "3,5,7"

    async def test_save_idempotent(self, db):
        """INSERT OR IGNORE"""
        await lottery_result_save(db, issue="dup", open_result="1,2,3", sum_value=6)
        await lottery_result_save(db, issue="dup", open_result="4,5,6", sum_value=15)
        fetched = await lottery_result_get_by_issue(db, issue="dup")
        # 
        assert fetched["sum_value"] == 6

    async def test_list_recent(self, db):
        for i in range(5):
            await lottery_result_save(db, issue=f"issue_{i:03d}", open_result="1,2,3", sum_value=6)
        recent = await lottery_result_list_recent(db, limit=3)
        assert len(recent) == 3

    async def test_get_nonexistent(self, db):
        result = await lottery_result_get_by_issue(db, issue="nonexistent")
        assert result is None


# 
# 8. reconcile_records CRUD
# 

class TestReconcileRecordsCRUD:

    async def test_create_and_list(self, db, two_operators):
        op_a, _ = two_operators
        acc = await account_create(
            db, operator_id=op_a["id"],
            account_name="rec_acc", password="p", platform_type="JND28WEB",
        )
        rec = await reconcile_record_create(
            db, operator_id=op_a["id"], account_id=acc["id"],
            issue="001", local_bet_count=5, platform_bet_count=5,
            local_balance=100000, platform_balance=100050,
            diff_amount=50, status="matched",
        )
        assert rec["status"] == "matched"

        items, total = await reconcile_record_list_by_account(
            db, account_id=acc["id"], operator_id=op_a["id"],
        )
        assert total == 1

    async def test_create_wrong_operator_raises(self, db, two_operators):
        """account  operator  ValueError"""
        op_a, op_b = two_operators
        acc = await account_create(
            db, operator_id=op_a["id"],
            account_name="owned_by_a", password="p", platform_type="JND28WEB",
        )
        with pytest.raises(ValueError):
            await reconcile_record_create(
                db, operator_id=op_b["id"], account_id=acc["id"],
                issue="001", local_bet_count=1,
            )

    async def test_list_wrong_operator_empty(self, db, two_operators):
        """ operator_id """
        op_a, op_b = two_operators
        acc = await account_create(
            db, operator_id=op_a["id"],
            account_name="iso_acc", password="p", platform_type="JND28WEB",
        )
        await reconcile_record_create(
            db, operator_id=op_a["id"], account_id=acc["id"],
            issue="001", local_bet_count=1,
        )
        items, total = await reconcile_record_list_by_account(
            db, account_id=acc["id"], operator_id=op_b["id"],
        )
        assert total == 0
        assert len(items) == 0


# 
# 9. 
# 

class TestDataIsolation:
    """ A  B  CRUD """

    async def test_account_isolation(self, db, two_operators):
        op_a, op_b = two_operators
        acc_a = await account_create(db, operator_id=op_a["id"], account_name="a_acc", password="p", platform_type="JND28WEB")

        # B  A 
        assert await account_get_by_id(db, account_id=acc_a["id"], operator_id=op_b["id"]) is None
        assert await account_list_by_operator(db, operator_id=op_b["id"]) == []

        # B  A 
        result = await account_update(db, account_id=acc_a["id"], operator_id=op_b["id"], balance=99999)
        assert result is None

        # B  A 
        assert await account_delete(db, account_id=acc_a["id"], operator_id=op_b["id"]) is False

    async def test_strategy_isolation(self, db, two_operators):
        op_a, op_b = two_operators
        acc_a = await account_create(db, operator_id=op_a["id"], account_name="sa", password="p", platform_type="JND28WEB")
        strat_a = await strategy_create(
            db, operator_id=op_a["id"], account_id=acc_a["id"],
            name="iso", type="flat", play_code="DX1", base_amount=100,
        )

        # B  A 
        assert await strategy_get_by_id(db, strategy_id=strat_a["id"], operator_id=op_b["id"]) is None
        assert await strategy_list_by_operator(db, operator_id=op_b["id"]) == []

        # B / A 
        result = await strategy_update(db, strategy_id=strat_a["id"], operator_id=op_b["id"], name="hacked")
        assert result is None
        assert await strategy_delete(db, strategy_id=strat_a["id"], operator_id=op_b["id"]) is False

    async def test_bet_order_isolation(self, db, two_operators):
        op_a, op_b = two_operators
        acc_a = await account_create(db, operator_id=op_a["id"], account_name="ba", password="p", platform_type="JND28WEB")
        strat_a = await strategy_create(
            db, operator_id=op_a["id"], account_id=acc_a["id"],
            name="bo", type="flat", play_code="DX1", base_amount=100,
        )
        order_a = await bet_order_create(
            db, idempotent_id="iso-1", operator_id=op_a["id"],
            account_id=acc_a["id"], strategy_id=strat_a["id"],
            issue="001", key_code="DX1", amount=100,
        )

        # B  A 
        assert await bet_order_get_by_id(db, order_id=order_a["id"], operator_id=op_b["id"]) is None
        items, total = await bet_order_list_by_operator(db, operator_id=op_b["id"])
        assert total == 0

    async def test_alert_isolation(self, db, two_operators):
        op_a, op_b = two_operators
        await alert_create(db, operator_id=op_a["id"], type="test", title="A's alert")

        # B  A 
        items, total = await alert_list_by_operator(db, operator_id=op_b["id"])
        assert total == 0
        assert await alert_get_unread_count(db, operator_id=op_b["id"]) == 0

    async def test_alert_mark_read_isolation(self, db, two_operators):
        """B  A """
        op_a, op_b = two_operators
        alert = await alert_create(db, operator_id=op_a["id"], type="test", title="A's alert")
        result = await alert_mark_read(db, alert_id=alert["id"], operator_id=op_b["id"])
        assert result is False
        # A 
        assert await alert_get_unread_count(db, operator_id=op_a["id"]) == 1

    async def test_audit_log_isolation(self, db, two_operators):
        op_a, op_b = two_operators
        await audit_log_create(db, operator_id=op_a["id"], action="login")

        # B  A 
        items, total = await audit_log_list_by_operator(db, operator_id=op_b["id"])
        assert total == 0

    async def test_reconcile_record_isolation(self, db, two_operators):
        op_a, op_b = two_operators
        acc_a = await account_create(db, operator_id=op_a["id"], account_name="ra", password="p", platform_type="JND28WEB")
        await reconcile_record_create(
            db, operator_id=op_a["id"], account_id=acc_a["id"],
            issue="001", local_bet_count=1,
        )

        # B  JOIN  A 
        items, total = await reconcile_record_list_by_account(
            db, account_id=acc_a["id"], operator_id=op_b["id"],
        )
        assert total == 0


# 
# PBT: P25  DAO 
# 

from hypothesis import given, settings, strategies as st


class TestPBT_P25_DAODataIsolation:
    """P25:  operator_id A  CRUD / B 

    **Validates: Requirements 1.4**

    Property: For any two different operator_ids, data created by operator A
    is invisible to operator B across all 6 tables:
    gambling_accounts, strategies, bet_orders, alerts, audit_logs, reconcile_records.
    """

    @given(
        op_a_id=st.integers(min_value=100, max_value=500),
        op_b_id=st.integers(min_value=501, max_value=999),
    )
    @settings(max_examples=200)
    def test_pbt_data_isolation_across_tables(
        self, op_a_id: int, op_b_id: int
    ):
        """Operator A's data is invisible to operator B across all 6 tables.

        **Validates: Requirements 1.4**
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

            # Create two operators
            now = "2025-01-01 00:00:00"
            await conn.execute(
                """INSERT INTO operators
                   (id, username, password, role, status, created_at, updated_at)
                   VALUES (?, ?, 'pwd', 'operator', 'active', ?, ?)""",
                (op_a_id, f"op_a_{op_a_id}", now, now),
            )
            await conn.execute(
                """INSERT INTO operators
                   (id, username, password, role, status, created_at, updated_at)
                   VALUES (?, ?, 'pwd', 'operator', 'active', ?, ?)""",
                (op_b_id, f"op_b_{op_b_id}", now, now),
            )
            await conn.commit()

            # 1. gambling_accounts  create for A, verify B can't see
            acc_a = await account_create(
                conn, operator_id=op_a_id,
                account_name=f"acc_{op_a_id}", password="p",
                platform_type="JND28WEB",
            )
            b_accounts = await account_list_by_operator(conn, operator_id=op_b_id)
            assert len(b_accounts) == 0, "B should not see A's accounts"
            assert await account_get_by_id(
                conn, account_id=acc_a["id"], operator_id=op_b_id
            ) is None, "B should not get A's account by ID"

            # 2. strategies  create for A, verify B can't see
            strat_a = await strategy_create(
                conn, operator_id=op_a_id, account_id=acc_a["id"],
                name="s_a", type="flat", play_code="DX1", base_amount=1000,
            )
            b_strategies = await strategy_list_by_operator(conn, operator_id=op_b_id)
            assert len(b_strategies) == 0, "B should not see A's strategies"
            assert await strategy_get_by_id(
                conn, strategy_id=strat_a["id"], operator_id=op_b_id
            ) is None, "B should not get A's strategy by ID"

            # 3. bet_orders  create for A, verify B can't see
            idem_id = f"pbt-iso-{op_a_id}-{op_b_id}"
            order_a = await bet_order_create(
                conn, idempotent_id=idem_id, operator_id=op_a_id,
                account_id=acc_a["id"], strategy_id=strat_a["id"],
                issue="001", key_code="DX1", amount=1000,
            )
            b_orders, b_total = await bet_order_list_by_operator(
                conn, operator_id=op_b_id
            )
            assert b_total == 0, "B should not see A's bet orders"
            assert await bet_order_get_by_id(
                conn, order_id=order_a["id"], operator_id=op_b_id
            ) is None, "B should not get A's bet order by ID"

            # 4. alerts  create for A, verify B can't see
            await alert_create(
                conn, operator_id=op_a_id, type="test",
                level="info", title="A's alert",
            )
            b_alerts, b_alert_total = await alert_list_by_operator(
                conn, operator_id=op_b_id
            )
            assert b_alert_total == 0, "B should not see A's alerts"
            assert await alert_get_unread_count(
                conn, operator_id=op_b_id
            ) == 0, "B should have 0 unread alerts"

            # 5. audit_logs  create for A, verify B can't see
            await audit_log_create(
                conn, operator_id=op_a_id, action="test_action",
            )
            b_logs, b_log_total = await audit_log_list_by_operator(
                conn, operator_id=op_b_id
            )
            assert b_log_total == 0, "B should not see A's audit logs"

            # 6. reconcile_records  create for A, verify B can't see
            await reconcile_record_create(
                conn, operator_id=op_a_id, account_id=acc_a["id"],
                issue="001", local_bet_count=1,
            )
            b_recs, b_rec_total = await reconcile_record_list_by_account(
                conn, account_id=acc_a["id"], operator_id=op_b_id,
            )
            assert b_rec_total == 0, "B should not see A's reconcile records"

            await conn.close()

        asyncio.get_event_loop().run_until_complete(_run())
