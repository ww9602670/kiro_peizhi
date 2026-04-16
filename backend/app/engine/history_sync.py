"""历史数据懒加载同步服务（模块级单例）

将历史同步从 EngineManager 的 Worker 生命周期中解耦，
改为用户打开回测页面时自动检测缺口并按需补全。

核心设计：
- 模块级 asyncio.Lock 保证同一时刻只有一个同步操作
- 双数据源：8828 API（主）+ bocai.db lottery_results（尾部）
- check_and_sync() 由 history-status API 调用，自动判断是否需要同步
- manual_sync() 由 fill-history API 调用，手动触发
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_BJT = timezone(timedelta(hours=8))


class HistorySyncService:
    """历史数据懒加载同步服务"""

    def __init__(self, jnd28_db_path: str, bocai_db_path: str):
        self._jnd28_db_path = jnd28_db_path
        self._bocai_db_path = bocai_db_path
        self._lock = asyncio.Lock()
        self._syncing = False
        self._last_sync_time: str | None = None
        self._last_sync_inserted: int | None = None

    # ------------------------------------------------------------------
    # 公开属性
    # ------------------------------------------------------------------

    @property
    def syncing(self) -> bool:
        return self._syncing

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    async def check_and_sync(self, gap_threshold_minutes: int = 10) -> None:
        """检查数据缺口，超过阈值则触发后台同步（非阻塞）。

        由 GET /backtest/history-status 调用。
        判断逻辑：基于 last_sync_time（上次同步完成时间）而非 open_time（开奖时间）。
        - 如果距上次同步完成不超过 gap_threshold_minutes，跳过
        - 冷启动时从 sync_state 表读取持久化的 last_sync_time
        """
        if self._syncing:
            return

        should = await asyncio.to_thread(
            self._should_sync, gap_threshold_minutes
        )
        if should:
            asyncio.create_task(self._do_sync())

    async def manual_sync(self) -> dict:
        """手动触发同步。若已在同步中则抛出 RuntimeError。"""
        if self._syncing:
            raise RuntimeError("同步正在执行中，请稍候")
        return await self._do_sync()

    def get_status(self) -> dict:
        """返回同步元数据（内存中的快照）。"""
        return {
            "syncing": self._syncing,
            "last_sync_time": self._last_sync_time,
            "last_sync_inserted": self._last_sync_inserted,
        }

    # ------------------------------------------------------------------
    # 内部：缺口检测
    # ------------------------------------------------------------------

    def _should_sync(self, gap_threshold_minutes: int = 10) -> bool:
        """基于 last_sync_time 判断是否需要同步（在线程池中执行）。

        逻辑：
        1. 优先使用内存中的 _last_sync_time
        2. 冷启动时从 sync_state 表读取持久化的 last_sync_time
        3. 如果从未同步过，触发同步
        4. 如果距上次同步完成时间超过阈值，触发同步
        """
        # 1) 确定 last_sync_time：优先内存，其次 DB
        last_sync_str = self._last_sync_time
        if not last_sync_str:
            try:
                conn = sqlite3.connect(self._jnd28_db_path)
                try:
                    row = conn.execute(
                        "SELECT v FROM sync_state WHERE k='last_sync_time'"
                    ).fetchone()
                    if row and row[0]:
                        last_sync_str = row[0]
                        # 回填到内存，避免每次都读 DB
                        self._last_sync_time = last_sync_str
                finally:
                    conn.close()
            except Exception:
                logger.exception("读取 sync_state 失败")

        # 2) 从未同步过 → 需要同步
        if not last_sync_str:
            return True

        # 3) 解析时间并比较
        try:
            last_sync = datetime.strptime(last_sync_str, "%Y-%m-%d %H:%M:%S")
            last_sync = last_sync.replace(tzinfo=_BJT)
        except ValueError:
            return True

        now = datetime.now(_BJT)
        gap = now - last_sync
        return gap > timedelta(minutes=gap_threshold_minutes)

    # ------------------------------------------------------------------
    # 内部：执行同步
    # ------------------------------------------------------------------

    async def _do_sync(self) -> dict:
        """执行同步：8828 主数据源 + lottery_results 尾部数据源。"""
        async with self._lock:
            self._syncing = True
            try:
                # 1) 主数据源：8828
                inserted_8828 = await self._fill_from_8828()

                # 2) 尾部数据源：bocai.db lottery_results
                inserted_tail = await self._fill_from_lottery_results()

                total_inserted = inserted_8828 + inserted_tail

                # 3) 更新 sync_state
                now_str = datetime.now(_BJT).strftime("%Y-%m-%d %H:%M:%S")
                await asyncio.to_thread(
                    self._save_sync_state, now_str, total_inserted
                )

                self._last_sync_time = now_str
                self._last_sync_inserted = total_inserted

                if total_inserted > 0:
                    logger.info(
                        "历史同步完成：8828=%d 尾部=%d 共=%d",
                        inserted_8828, inserted_tail, total_inserted,
                    )
                else:
                    logger.debug("历史同步完成：无新增数据")

                return {
                    "total_inserted": total_inserted,
                    "inserted_8828": inserted_8828,
                    "inserted_tail": inserted_tail,
                }
            except Exception:
                logger.exception("历史同步失败")
                raise
            finally:
                self._syncing = False

    # ------------------------------------------------------------------
    # 内部：8828 主数据源
    # ------------------------------------------------------------------

    async def _fill_from_8828(self) -> int:
        """调用 fill_missing_from_8828 补全历史数据，返回新增条数。"""
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        db_path = self._jnd28_db_path

        def _do():
            conn = sqlite3.connect(db_path)
            before = conn.execute("SELECT COUNT(*) FROM jnd28_history").fetchone()[0]
            conn.close()

            from history_collector import fill_missing_from_8828
            fill_missing_from_8828(db_path=db_path)

            conn = sqlite3.connect(db_path)
            after = conn.execute("SELECT COUNT(*) FROM jnd28_history").fetchone()[0]
            conn.close()
            return after - before

        try:
            return await asyncio.to_thread(_do)
        except Exception:
            logger.exception("8828 数据源补全失败")
            return 0

    # ------------------------------------------------------------------
    # 内部：尾部数据源（lottery_results）
    # ------------------------------------------------------------------

    async def _fill_from_lottery_results(self) -> int:
        """从 bocai.db 的 lottery_results 读取 jnd28_history 中不存在的记录并写入。"""
        bocai_path = self._bocai_db_path
        jnd28_path = self._jnd28_db_path

        if not os.path.exists(bocai_path):
            logger.debug("bocai.db 不存在，跳过尾部补全: %s", bocai_path)
            return 0

        def _do():
            bocai_conn = sqlite3.connect(bocai_path)
            jnd28_conn = sqlite3.connect(jnd28_path)
            try:
                # 读取 lottery_results 中的所有期号
                lr_rows = bocai_conn.execute(
                    "SELECT issue, open_result, sum_value, open_time "
                    "FROM lottery_results ORDER BY issue"
                ).fetchall()

                if not lr_rows:
                    return 0

                inserted = 0
                now_str = datetime.now(_BJT).strftime("%Y-%m-%d %H:%M:%S")

                for issue, open_result, sum_value, open_time in lr_rows:
                    # 检查 jnd28_history 中是否已存在
                    exists = jnd28_conn.execute(
                        "SELECT 1 FROM jnd28_history WHERE issue=?", (issue,)
                    ).fetchone()
                    if exists:
                        continue

                    # 解析 open_result "d1,d2,d3"
                    parts = [p.strip() for p in open_result.split(",")]
                    if len(parts) != 3:
                        continue
                    try:
                        d1, d2, d3 = int(parts[0]), int(parts[1]), int(parts[2])
                    except ValueError:
                        continue

                    jnd28_conn.execute(
                        "INSERT OR IGNORE INTO jnd28_history"
                        "(issue, open_time, d1, d2, d3, sum, created_at) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?)",
                        (issue, open_time, d1, d2, d3, sum_value, now_str),
                    )
                    inserted += 1

                jnd28_conn.commit()
                return inserted
            finally:
                bocai_conn.close()
                jnd28_conn.close()

        try:
            return await asyncio.to_thread(_do)
        except Exception:
            logger.exception("尾部数据源补全失败")
            return 0

    # ------------------------------------------------------------------
    # 内部：sync_state 持久化
    # ------------------------------------------------------------------

    def _save_sync_state(self, now_str: str, total_inserted: int) -> None:
        """写入 sync_state 表（同步线程中调用）。"""
        conn = sqlite3.connect(self._jnd28_db_path)
        try:
            conn.execute(
                "INSERT INTO sync_state(k,v) VALUES('last_sync_time',?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (now_str,),
            )
            conn.execute(
                "INSERT INTO sync_state(k,v) VALUES('last_sync_inserted',?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (str(total_inserted),),
            )
            conn.commit()
        finally:
            conn.close()


# ======================================================================
# 模块级单例
# ======================================================================

_sync_service: HistorySyncService | None = None


def init_sync_service(jnd28_db_path: str, bocai_db_path: str) -> HistorySyncService:
    """初始化同步服务单例（应用启动时调用）。"""
    global _sync_service
    _sync_service = HistorySyncService(jnd28_db_path, bocai_db_path)
    logger.info("HistorySyncService 已初始化")
    return _sync_service


def get_sync_service() -> HistorySyncService:
    """获取同步服务单例。未初始化时抛出 RuntimeError。"""
    if _sync_service is None:
        raise RuntimeError("HistorySyncService 未初始化，请先调用 init_sync_service()")
    return _sync_service
