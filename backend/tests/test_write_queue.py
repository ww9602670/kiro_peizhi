"""Task 1.2.4  WriteQueue 


-   
-   
- 
"""
import asyncio

import pytest
import aiosqlite

from app.database import DDL_STATEMENTS, INSERT_DEFAULT_ADMIN
from app.write_queue import WriteQueue, QUEUE_MAX_SIZE


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
async def wq(db):
    """ WriteQueue"""
    queue = WriteQueue(db)
    await queue.start()
    yield queue
    await queue.stop()


class TestSingleWrite:
    """"""

    async def test_single_insert(self, db, wq):
        """ WriteQueue """
        await wq.execute(
            "INSERT INTO gambling_accounts (operator_id, account_name, password, platform_type) "
            "VALUES (1, 'acc1', 'pwd', 'JND28WEB')"
        )
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM gambling_accounts")
        row = await cursor.fetchone()
        assert row["cnt"] == 1

    async def test_single_write_with_params(self, db, wq):
        """"""
        await wq.execute(
            "INSERT INTO gambling_accounts (operator_id, account_name, password, platform_type) "
            "VALUES (?, ?, ?, ?)",
            (1, "acc_param", "pwd", "JND282"),
        )
        cursor = await db.execute(
            "SELECT account_name, platform_type FROM gambling_accounts WHERE account_name = ?",
            ("acc_param",),
        )
        row = await cursor.fetchone()
        assert row["account_name"] == "acc_param"
        assert row["platform_type"] == "JND282"

    async def test_single_write_error_propagates(self, db, wq):
        """"""
        with pytest.raises(aiosqlite.IntegrityError):
            # operator_id=999 
            await wq.execute(
                "INSERT INTO gambling_accounts (operator_id, account_name, password, platform_type) "
                "VALUES (999, 'bad', 'pwd', 'JND28WEB')"
            )


class TestConcurrentWriteSerialization:
    """  """

    async def test_concurrent_writes_no_conflict(self, db, wq):
        """50 """
        num_writers = 50

        async def writer(i: int):
            await wq.execute(
                "INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
                (1, "test", "info", f"alert-{i}"),
            )

        # 
        await asyncio.gather(*[writer(i) for i in range(num_writers)])

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM alerts")
        row = await cursor.fetchone()
        assert row["cnt"] == num_writers

    async def test_concurrent_writes_order_preserved(self, db, wq):
        """"""
        results = []
        num_writes = 20

        for i in range(num_writes):
            await wq.execute(
                "INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
                (1, "test", "info", f"seq-{i}"),
            )
            results.append(i)

        cursor = await db.execute(
            "SELECT title FROM alerts ORDER BY id"
        )
        rows = await cursor.fetchall()
        titles = [row["title"] for row in rows]
        assert titles == [f"seq-{i}" for i in range(num_writes)]


class TestBatchTransactionAtomicity:
    """  """

    async def test_batch_all_succeed(self, db, wq):
        """"""
        stmts = [
            (
                "INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
                (1, "test", "info", f"batch-{i}"),
            )
            for i in range(5)
        ]
        await wq.execute_batch(stmts)

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM alerts")
        row = await cursor.fetchone()
        assert row["cnt"] == 5

    async def test_batch_partial_failure_rolls_back(self, db, wq):
        """  """
        stmts = [
            #  1 
            (
                "INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
                (1, "test", "info", "batch-ok-1"),
            ),
            #  2 
            (
                "INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
                (1, "test", "info", "batch-ok-2"),
            ),
            #  3   
            (
                "INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
                (999, "test", "info", "batch-fail"),
            ),
        ]

        with pytest.raises(aiosqlite.IntegrityError):
            await wq.execute_batch(stmts)

        # 0 
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM alerts")
        row = await cursor.fetchone()
        assert row["cnt"] == 0

    async def test_batch_empty_is_noop(self, db, wq):
        """"""
        await wq.execute_batch([])

    async def test_batch_followed_by_single_works(self, db, wq):
        """worker """
        # 
        bad_stmts = [
            (
                "INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
                (999, "test", "info", "will-fail"),
            ),
        ]
        with pytest.raises(aiosqlite.IntegrityError):
            await wq.execute_batch(bad_stmts)

        # 
        await wq.execute(
            "INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
            (1, "test", "info", "after-batch-fail"),
        )
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM alerts")
        row = await cursor.fetchone()
        assert row["cnt"] == 1


class TestBackpressure:
    """"""

    async def test_queue_max_size(self):
        """ 1000"""
        assert QUEUE_MAX_SIZE == 1000

    async def test_backpressure_blocks_when_full(self, db):
        """ execute """
        # 
        small_q = WriteQueue.__new__(WriteQueue)
        small_q._db = db
        small_q._queue = asyncio.Queue(maxsize=3)
        small_q._worker_task = None

        #  worker
        #  put 3 
        loop = asyncio.get_running_loop()
        for _ in range(3):
            op = object()  # 
            small_q._queue.put_nowait(op)

        assert small_q._queue.full()

        #  4  put 
        blocked = True

        async def try_put():
            nonlocal blocked
            await small_q._queue.put(object())
            blocked = False

        task = asyncio.create_task(try_put())
        await asyncio.sleep(0.05)  # 
        assert blocked, ""

        # 
        small_q._queue.get_nowait()
        await asyncio.sleep(0.05)
        assert not blocked, ""

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_no_writes_dropped_under_pressure(self, db, wq):
        """"""
        num_writes = 200

        async def writer(i: int):
            await wq.execute(
                "INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
                (1, "pressure", "info", f"p-{i}"),
            )

        await asyncio.gather(*[writer(i) for i in range(num_writes)])

        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM alerts WHERE type = 'pressure'"
        )
        row = await cursor.fetchone()
        assert row["cnt"] == num_writes, f" {num_writes}  {row['cnt']} "


class TestStartStop:
    """WriteQueue """

    async def test_start_stop(self, db):
        """"""
        wq = WriteQueue(db)
        await wq.start()
        assert wq._worker_task is not None
        await wq.stop()
        assert wq._worker_task is None

    async def test_double_start_idempotent(self, db):
        """ worker"""
        wq = WriteQueue(db)
        await wq.start()
        task1 = wq._worker_task
        await wq.start()
        task2 = wq._worker_task
        assert task1 is task2
        await wq.stop()

    async def test_stop_without_start(self, db):
        """"""
        wq = WriteQueue(db)
        await wq.stop()  # 

    async def test_qsize_and_full(self, db, wq):
        """qsize  full """
        assert wq.qsize == 0
        assert not wq.full


class TestLifecycleGuard:
    """/"""

    async def test_execute_before_start_raises(self, db):
        """ execute  RuntimeError"""
        wq = WriteQueue(db)
        with pytest.raises(RuntimeError, match=""):
            await wq.execute("INSERT INTO alerts (operator_id, type, level, title) VALUES (1,'t','info','x')")

    async def test_execute_after_stop_raises(self, db):
        """ execute  RuntimeError"""
        wq = WriteQueue(db)
        await wq.start()
        await wq.stop()
        with pytest.raises(RuntimeError, match=""):
            await wq.execute("INSERT INTO alerts (operator_id, type, level, title) VALUES (1,'t','info','x')")

    async def test_execute_batch_before_start_raises(self, db):
        """ execute_batch  RuntimeError"""
        wq = WriteQueue(db)
        with pytest.raises(RuntimeError, match=""):
            await wq.execute_batch([
                ("INSERT INTO alerts (operator_id, type, level, title) VALUES (1,'t','info','x')", ()),
            ])

    async def test_execute_batch_after_stop_raises(self, db):
        """ execute_batch  RuntimeError"""
        wq = WriteQueue(db)
        await wq.start()
        await wq.stop()
        with pytest.raises(RuntimeError, match=""):
            await wq.execute_batch([
                ("INSERT INTO alerts (operator_id, type, level, title) VALUES (1,'t','info','x')", ()),
            ])


class TestExplicitTransaction:
    """batch  BEGIN IMMEDIATE """

    async def test_batch_atomic_with_explicit_begin(self, db, wq):
        """"""
        stmts = [
            ("INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
             (1, "test", "info", "txn-ok-1")),
            ("INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
             (1, "test", "info", "txn-ok-2")),
            #   
            ("INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
             (999, "test", "info", "txn-fail")),
        ]
        with pytest.raises(aiosqlite.IntegrityError):
            await wq.execute_batch(stmts)

        # 0 
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM alerts")
        row = await cursor.fetchone()
        assert row["cnt"] == 0

    async def test_batch_success_commits(self, db, wq):
        """"""
        stmts = [
            ("INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
             (1, "test", "info", f"txn-{i}"))
            for i in range(5)
        ]
        await wq.execute_batch(stmts)
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM alerts")
        row = await cursor.fetchone()
        assert row["cnt"] == 5

    async def test_single_write_still_works_after_batch_fail(self, db, wq):
        """batch  worker """
        bad_stmts = [
            ("INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
             (999, "test", "info", "will-fail")),
        ]
        with pytest.raises(aiosqlite.IntegrityError):
            await wq.execute_batch(bad_stmts)

        # 
        await wq.execute(
            "INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
            (1, "test", "info", "recovery"),
        )
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM alerts")
        row = await cursor.fetchone()
        assert row["cnt"] == 1
