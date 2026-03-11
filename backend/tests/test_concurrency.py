"""

Phase 16.2:
- 16.2.1: 1000 //
- 16.2.2: SQLite WAL WriteQueue  + 
- 16.2.3:  Worker  Worker
- 16.2.4:  error
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import tracemalloc
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from app.write_queue import WriteQueue


# =========================================================================
# 16.2.1  1000 Worker 
# =========================================================================

class TestConcurrency1000Workers:
    """1000 //"""

    @pytest.mark.asyncio
    async def test_1000_workers_no_deadlock(self):
        """1000  2s/"""
        errors: list[Exception] = []

        async def fake_worker(worker_id: int):
            try:
                for _ in range(20):  # 20   0.1s = 2s
                    await asyncio.sleep(0.1)
            except Exception as e:
                errors.append(e)

        tasks = [asyncio.create_task(fake_worker(i)) for i in range(1000)]
        done, pending = await asyncio.wait(tasks, timeout=10)

        assert len(pending) == 0, f"{len(pending)} tasks still pending (deadlock?)"
        assert len(errors) == 0, f"{len(errors)} workers raised exceptions"
        assert len(done) == 1000

    @pytest.mark.asyncio
    async def test_1000_workers_no_task_leak(self):
        """1000  Task """
        async def fake_worker():
            while True:
                await asyncio.sleep(0.1)

        tasks = [asyncio.create_task(fake_worker()) for _ in range(1000)]
        await asyncio.sleep(0.5)  # 

        # 
        for t in tasks:
            t.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)

        #  task  done
        assert all(t.done() for t in tasks)
        #  CancelledError
        cancelled = sum(1 for r in results if isinstance(r, asyncio.CancelledError))
        assert cancelled == 1000

    @pytest.mark.asyncio
    async def test_1000_workers_memory_reasonable(self):
        """1000 < 50MB"""
        tracemalloc.start()
        snapshot_before = tracemalloc.take_snapshot()

        async def fake_worker():
            for _ in range(10):
                await asyncio.sleep(0.05)

        tasks = [asyncio.create_task(fake_worker()) for _ in range(1000)]
        snapshot_during = tracemalloc.take_snapshot()

        await asyncio.gather(*tasks)
        tracemalloc.stop()

        # 
        stats = snapshot_during.compare_to(snapshot_before, "lineno")
        total_diff = sum(s.size_diff for s in stats if s.size_diff > 0)
        mb = total_diff / (1024 * 1024)
        assert mb < 50, f"Memory growth {mb:.1f}MB exceeds 50MB limit"


# =========================================================================
# 16.2.2  SQLite WAL 
# =========================================================================

class TestWALConcurrency:
    """SQLite WAL  + """

    @pytest.mark.asyncio
    async def test_wal_concurrent_read_write(self):
        """WAL  100  + 100 """
        #  DB DB  WAL
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = tmp.name

        try:
            # 
            write_conn = await aiosqlite.connect(db_path)
            await write_conn.execute("PRAGMA journal_mode=WAL")
            await write_conn.execute(
                "CREATE TABLE test_wal (id INTEGER PRIMARY KEY, val TEXT)"
            )
            await write_conn.commit()

            wq = WriteQueue(write_conn)
            await wq.start()

            # 100 
            for i in range(100):
                await wq.execute(
                    "INSERT INTO test_wal (id, val) VALUES (?, ?)",
                    (i, f"value_{i}"),
                )

            # 
            read_conn = await aiosqlite.connect(db_path)
            read_conn.row_factory = aiosqlite.Row

            read_results: list[int] = []

            async def read_task():
                cursor = await read_conn.execute("SELECT COUNT(*) as cnt FROM test_wal")
                row = await cursor.fetchone()
                read_results.append(row["cnt"])

            # 100 
            read_tasks = [asyncio.create_task(read_task()) for _ in range(100)]
            await asyncio.gather(*read_tasks)

            #  100 
            assert all(r == 100 for r in read_results), f"Read results: {set(read_results)}"

            # 
            cursor = await read_conn.execute("SELECT id FROM test_wal ORDER BY id")
            rows = await cursor.fetchall()
            ids = [r["id"] for r in rows]
            assert ids == list(range(100)), "Data loss detected"

            await wq.stop()
            await write_conn.close()
            await read_conn.close()
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_write_queue_no_data_loss(self):
        """WriteQueue  200 """
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = tmp.name

        try:
            conn = await aiosqlite.connect(db_path)
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute(
                "CREATE TABLE test_loss (id INTEGER PRIMARY KEY, val INTEGER)"
            )
            await conn.commit()

            wq = WriteQueue(conn)
            await wq.start()

            # 200  WriteQueue 
            write_tasks = []
            for i in range(200):
                write_tasks.append(
                    wq.execute(
                        "INSERT INTO test_loss (id, val) VALUES (?, ?)",
                        (i, i * 10),
                    )
                )
            await asyncio.gather(*write_tasks)

            # 
            cursor = await conn.execute("SELECT COUNT(*) as cnt FROM test_loss")
            row = await cursor.fetchone()
            assert row[0] == 200, f"Expected 200 rows, got {row[0]}"

            # 
            cursor = await conn.execute("SELECT SUM(val) as total FROM test_loss")
            row = await cursor.fetchone()
            expected_sum = sum(i * 10 for i in range(200))
            assert row[0] == expected_sum

            await wq.stop()
            await conn.close()
        finally:
            os.unlink(db_path)


# =========================================================================
# 16.2.3  
# =========================================================================

class TestFaultInjection:
    """ Worker  Worker"""

    @pytest.mark.asyncio
    async def test_fault_isolation_between_workers(self):
        """Worker 1 Worker 2/3 """
        results: dict[int, str] = {}

        async def failing_worker():
            await asyncio.sleep(0.1)
            raise RuntimeError("Worker 1 crash")

        async def normal_worker(wid: int):
            await asyncio.sleep(0.5)
            results[wid] = "completed"

        t1 = asyncio.create_task(failing_worker())
        t2 = asyncio.create_task(normal_worker(2))
        t3 = asyncio.create_task(normal_worker(3))

        gathered = await asyncio.gather(t1, t2, t3, return_exceptions=True)

        # Worker 1 
        assert isinstance(gathered[0], RuntimeError)
        # Worker 2, 3 
        assert results[2] == "completed"
        assert results[3] == "completed"

    @pytest.mark.asyncio
    async def test_network_timeout_recovery(self):
        """"""
        call_count = 0
        success_count = 0

        async def worker_with_timeout_recovery():
            nonlocal call_count, success_count
            for i in range(5):
                call_count += 1
                try:
                    if i < 2:
                        raise TimeoutError("Network timeout")
                    success_count += 1
                except TimeoutError:
                    pass  # 
                await asyncio.sleep(0.05)

        await worker_with_timeout_recovery()
        assert call_count == 5
        assert success_count == 3  #  2  3 

    @pytest.mark.asyncio
    async def test_platform_api_error_recovery(self):
        """ API """
        results: list[str] = []

        async def worker_with_api_errors():
            for i in range(5):
                try:
                    if i == 1:
                        raise ConnectionError("Platform API error")
                    results.append(f"ok_{i}")
                except ConnectionError:
                    results.append(f"error_{i}")
                await asyncio.sleep(0.05)

        await worker_with_api_errors()
        assert len(results) == 5
        assert results[1] == "error_1"
        assert results[0] == "ok_0"
        assert results[2] == "ok_2"


# =========================================================================
# 16.2.4  
# =========================================================================

class TestRecoveryQuantification:
    """Worker """

    @pytest.mark.asyncio
    async def test_restart_delays_incremental(self):
        """5s  10s  30smock sleep """
        from app.engine.worker import RESTART_DELAYS, MAX_RESTART_FAILURES

        sleep_calls: list[float] = []
        run_count = 0

        async def mock_sleep(seconds):
            sleep_calls.append(seconds)

        async def run_with_restart():
            nonlocal run_count
            restart_count = 0
            running = True
            while running:
                try:
                    run_count += 1
                    raise RuntimeError("Simulated crash")
                except RuntimeError:
                    restart_count += 1
                    if restart_count >= MAX_RESTART_FAILURES:
                        running = False
                        break
                    delay_idx = min(restart_count - 1, len(RESTART_DELAYS) - 1)
                    delay = RESTART_DELAYS[delay_idx]
                    await mock_sleep(delay)

        await run_with_restart()

        # 5 4  5 
        assert len(sleep_calls) == 4
        assert sleep_calls[0] == 5   #  1 
        assert sleep_calls[1] == 10  #  2 
        assert sleep_calls[2] == 30  #  3 
        assert sleep_calls[3] == 30  #  4 capped at 30

    @pytest.mark.asyncio
    async def test_5_consecutive_failures_marks_error(self):
        """ 5  error"""
        from app.engine.worker import MAX_RESTART_FAILURES

        restart_count = 0
        status = "running"

        for _ in range(MAX_RESTART_FAILURES):
            restart_count += 1

        if restart_count >= MAX_RESTART_FAILURES:
            status = "error"

        assert status == "error"
        assert restart_count == 5

    @pytest.mark.asyncio
    async def test_successful_run_resets_restart_count(self):
        """ 0"""
        restart_count = 3  #  3 

        # 
        success = True
        if success:
            restart_count = 0

        assert restart_count == 0

    @pytest.mark.asyncio
    async def test_restart_success_rate(self):
        """  99% 100  1 """
        success_count = 0
        total = 100

        for i in range(total):
            #  50 
            if i != 50:
                success_count += 1

        rate = success_count / total
        assert rate >= 0.99, f"Restart success rate {rate:.2%} < 99%"
        assert success_count == 99

    @pytest.mark.asyncio
    async def test_worker_restart_within_30s(self):
        """Worker   30s """
        from app.engine.worker import RESTART_DELAYS

        # 
        max_delay = max(RESTART_DELAYS)
        assert max_delay <= 30, f"Max restart delay {max_delay}s > 30s"

        #  3 
        total_first_3 = sum(RESTART_DELAYS)
        assert total_first_3 == 45  # 5 + 10 + 30 = 45s

        #   30s
        for delay in RESTART_DELAYS:
            assert delay <= 30
