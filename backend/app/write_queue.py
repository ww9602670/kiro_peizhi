"""DB 

design.md 1.5
-  asyncio.Queue + 
- WAL 
- 
-  1000
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

import aiosqlite

logger = logging.getLogger(__name__)

# 
QUEUE_MAX_SIZE = 1000


@dataclass
class _WriteOp:
    """"""
    sql: str
    params: tuple[Any, ...] = ()
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_event_loop().create_future())


@dataclass
class _BatchWriteOp:
    """"""
    statements: list[tuple[str, tuple[Any, ...]]]
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_event_loop().create_future())


class WriteQueue:
    """SQLite 

    
     1000

    stopped  running  stopping  stopped
    - stopped/stopping  RuntimeError
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db
        self._queue: asyncio.Queue[_WriteOp | _BatchWriteOp | None] = asyncio.Queue(
            maxsize=QUEUE_MAX_SIZE
        )
        self._worker_task: asyncio.Task | None = None
        self._running = False  # 

    async def start(self) -> None:
        """"""
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        logger.info("WriteQueue worker ")

    async def stop(self) -> None:
        """drain  worker

        stop()  op  drain  RuntimeError 
        """
        if self._worker_task is None:
            return
        self._running = False  # 
        await self._queue.put(None)  # 
        await self._worker_task
        self._worker_task = None
        # drain  op op 
        self._drain_remaining()
        logger.info("WriteQueue worker ")

    def _drain_remaining(self) -> None:
        """drain  op RuntimeError  future"""
        while not self._queue.empty():
            try:
                op = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if op is None:
                continue
            if hasattr(op, "future") and not op.future.done():
                op.future.set_exception(RuntimeError("WriteQueue "))

    def _check_running(self) -> None:
        """"""
        if not self._running:
            raise RuntimeError("WriteQueue ")

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        """

        
         RuntimeError
        """
        self._check_running()
        loop = asyncio.get_running_loop()
        op = _WriteOp(sql=sql, params=params, future=loop.create_future())
        await self._queue.put(op)  # 
        return await op.future

    async def execute_batch(
        self, statements: Sequence[tuple[str, tuple[Any, ...]]]
    ) -> None:
        """

         BEGIN IMMEDIATE 
         RuntimeError
        """
        if not statements:
            return
        self._check_running()
        loop = asyncio.get_running_loop()
        op = _BatchWriteOp(
            statements=list(statements),
            future=loop.create_future(),
        )
        await self._queue.put(op)  # 
        return await op.future

    @property
    def qsize(self) -> int:
        """"""
        return self._queue.qsize()

    @property
    def full(self) -> bool:
        """"""
        return self._queue.full()

    #   worker 

    async def _worker(self) -> None:
        """"""
        logger.debug("WriteQueue worker ")
        while True:
            op = await self._queue.get()

            #   
            if op is None:
                self._queue.task_done()
                break

            try:
                if isinstance(op, _BatchWriteOp):
                    await self._exec_batch(op)
                else:
                    await self._exec_single(op)
            except Exception:
                logger.exception("WriteQueue worker ")
            finally:
                self._queue.task_done()

    async def _exec_single(self, op: _WriteOp) -> None:
        """"""
        try:
            await self._db.execute(op.sql, op.params)
            await self._db.commit()
            if not op.future.done():
                op.future.set_result(None)
        except Exception as exc:
            await self._safe_rollback()
            if not op.future.done():
                op.future.set_exception(exc)

    async def _exec_batch(self, op: _BatchWriteOp) -> None:
        """BEGIN IMMEDIATE    COMMIT/ROLLBACK"""
        try:
            await self._db.execute("BEGIN IMMEDIATE")
            for sql, params in op.statements:
                await self._db.execute(sql, params)
            await self._db.execute("COMMIT")
            if not op.future.done():
                op.future.set_result(None)
        except Exception as exc:
            await self._safe_rollback()
            if not op.future.done():
                op.future.set_exception(exc)

    async def _safe_rollback(self) -> None:
        """"""
        try:
            await self._db.rollback()
        except Exception:
            logger.exception("WriteQueue rollback ")
