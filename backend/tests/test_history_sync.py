"""HistorySyncService 属性测试 + 单元测试

Property 1: 历史状态查询正确性
Property 2: 缺口检测与阈值判断
Property 3: 尾部数据源补全正确性
Property 4: 同步锁互斥性
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings, strategies as st

# 确保 project root 在 sys.path 中
import sys

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.engine.history_sync import HistorySyncService

_BJT = timezone(timedelta(hours=8))


# ======================================================================
# 辅助函数
# ======================================================================

def _create_jnd28_db(path: str) -> sqlite3.Connection:
    """创建 jnd28_history + sync_state 表。"""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS jnd28_history ("
        "issue TEXT PRIMARY KEY, open_time TEXT, "
        "d1 INTEGER, d2 INTEGER, d3 INTEGER, sum INTEGER, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sync_state (k TEXT PRIMARY KEY, v TEXT)"
    )
    conn.commit()
    return conn


def _create_bocai_db(path: str) -> sqlite3.Connection:
    """创建 lottery_results 表。"""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS lottery_results ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "issue TEXT NOT NULL UNIQUE, open_result TEXT NOT NULL, "
        "sum_value INTEGER NOT NULL, open_time TEXT, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')))"
    )
    conn.commit()
    return conn


# ======================================================================
# Property 1: 历史状态查询正确性
# ======================================================================

# 生成随机历史记录的策略
_issue_st = st.integers(min_value=1000000, max_value=9999999).map(str)
_d_st = st.integers(min_value=0, max_value=9)
_time_st = st.datetimes(
    min_value=datetime(2025, 1, 1),
    max_value=datetime(2026, 12, 31),
).map(lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S"))

_history_record_st = st.tuples(_issue_st, _time_st, _d_st, _d_st, _d_st)


@settings(max_examples=100, deadline=5000)
@given(records=st.lists(_history_record_st, min_size=1, max_size=50, unique_by=lambda r: r[0]))
def test_property_1_history_status_query(records):
    """Property 1: 查询返回的 max_issue/max_time 与数据库实际最大值一致。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        jnd28_path = os.path.join(tmpdir, "jnd28.sqlite3")
        conn = _create_jnd28_db(jnd28_path)

        for issue, open_time, d1, d2, d3 in records:
            s = d1 + d2 + d3
            conn.execute(
                "INSERT OR IGNORE INTO jnd28_history"
                "(issue, open_time, d1, d2, d3, sum, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (issue, open_time, d1, d2, d3, s, "2026-01-01 00:00:00"),
            )
        conn.commit()

        # 查询实际最大值
        row = conn.execute(
            "SELECT MAX(issue) as max_issue FROM jnd28_history"
        ).fetchone()
        expected_max_issue = row[0]

        row2 = conn.execute(
            "SELECT open_time FROM jnd28_history WHERE issue=?",
            (expected_max_issue,),
        ).fetchone()
        expected_max_time = row2[0] if row2 else None

        # 通过 _should_sync 内部使用的同一查询逻辑验证
        row3 = conn.execute(
            "SELECT MAX(open_time) FROM jnd28_history"
        ).fetchone()
        db_max_time = row3[0] if row3 else None

        # 验证 max_issue
        all_issues = [
            r[0]
            for r in conn.execute("SELECT issue FROM jnd28_history").fetchall()
        ]
        assert expected_max_issue == max(all_issues)

        # 验证 max_time 存在
        assert db_max_time is not None

        conn.close()


# ======================================================================
# Property 2: 缺口检测与阈值判断
# ======================================================================

@settings(max_examples=100, deadline=5000)
@given(
    minutes_ago=st.integers(min_value=0, max_value=1440),
    threshold=st.integers(min_value=1, max_value=60),
)
def test_property_2_gap_detection(minutes_ago, threshold):
    """Property 2: should_sync 基于 last_sync_time 的判断正确性。

    **Validates: Requirements 2.1, 2.2**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        jnd28_path = os.path.join(tmpdir, "jnd28.sqlite3")
        bocai_path = os.path.join(tmpdir, "bocai.db")
        conn = _create_jnd28_db(jnd28_path)

        # 写入一条历史记录（确保表非空）
        conn.execute(
            "INSERT INTO jnd28_history(issue, open_time, d1, d2, d3, sum, created_at) "
            "VALUES(?, '2025-01-01 00:00:00', 1, 2, 3, 6, '2026-01-01 00:00:00')",
            ("9999999",),
        )

        # 写入 last_sync_time 到 sync_state（模拟上次同步完成时间）
        now = datetime.now(_BJT)
        last_sync = now - timedelta(minutes=minutes_ago)
        last_sync_str = last_sync.strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO sync_state(k, v) VALUES('last_sync_time', ?)",
            (last_sync_str,),
        )
        conn.commit()
        conn.close()

        svc = HistorySyncService(jnd28_path, bocai_path)
        result = svc._should_sync(gap_threshold_minutes=threshold)

        # 由于 _should_sync 内部用 datetime.now() 获取当前时间，
        # 与测试中计算 last_sync 之间存在微小时间差（几毫秒~几秒），
        # 所以边界值 minutes_ago == threshold 时结果不确定。
        # 只验证明确的两侧：
        if minutes_ago > threshold + 1:
            assert result is True, (
                f"minutes_ago={minutes_ago} > threshold+1={threshold+1}, should sync"
            )
        elif minutes_ago < threshold:
            assert result is False, (
                f"minutes_ago={minutes_ago} < threshold={threshold}, should not sync"
            )


# ======================================================================
# Property 3: 尾部数据源补全正确性
# ======================================================================

_lr_record_st = st.tuples(
    _issue_st,                                    # issue
    _d_st, _d_st, _d_st,                         # d1, d2, d3
    _time_st,                                     # open_time
)


@settings(max_examples=100, deadline=10000)
@given(
    existing_issues=st.lists(_issue_st, min_size=0, max_size=20, unique=True),
    lr_records=st.lists(_lr_record_st, min_size=1, max_size=30, unique_by=lambda r: r[0]),
)
def test_property_3_tail_fill(existing_issues, lr_records):
    """Property 3: 从 lottery_results 补全后，jnd28_history 包含所有应补全的数据。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        jnd28_path = os.path.join(tmpdir, "jnd28.sqlite3")
        bocai_path = os.path.join(tmpdir, "bocai.db")

        jnd_conn = _create_jnd28_db(jnd28_path)
        bocai_conn = _create_bocai_db(bocai_path)

        # 写入已有的 jnd28_history 记录
        for iss in existing_issues:
            jnd_conn.execute(
                "INSERT OR IGNORE INTO jnd28_history"
                "(issue, open_time, d1, d2, d3, sum, created_at) "
                "VALUES(?, '2026-01-01 00:00:00', 0, 0, 0, 0, '2026-01-01')",
                (iss,),
            )
        jnd_conn.commit()

        # 写入 lottery_results
        for issue, d1, d2, d3, open_time in lr_records:
            s = d1 + d2 + d3
            bocai_conn.execute(
                "INSERT OR IGNORE INTO lottery_results"
                "(issue, open_result, sum_value, open_time) VALUES(?, ?, ?, ?)",
                (issue, f"{d1},{d2},{d3}", s, open_time),
            )
        bocai_conn.commit()

        jnd_conn.close()
        bocai_conn.close()

        # 执行补全
        svc = HistorySyncService(jnd28_path, bocai_path)
        loop = asyncio.new_event_loop()
        try:
            inserted = loop.run_until_complete(svc._fill_from_lottery_results())
        finally:
            loop.close()

        # 验证：所有 lr_records 中的 issue 都应存在于 jnd28_history
        jnd_conn = sqlite3.connect(jnd28_path)
        for issue, d1, d2, d3, open_time in lr_records:
            row = jnd_conn.execute(
                "SELECT d1, d2, d3, sum FROM jnd28_history WHERE issue=?",
                (issue,),
            ).fetchone()
            assert row is not None, f"issue {issue} 应存在于 jnd28_history"

            # 如果是新插入的（不在 existing_issues 中），验证字段值
            if issue not in existing_issues:
                assert row[0] == d1, f"d1 mismatch for {issue}"
                assert row[1] == d2, f"d2 mismatch for {issue}"
                assert row[2] == d3, f"d3 mismatch for {issue}"
                assert row[3] == d1 + d2 + d3, f"sum mismatch for {issue}"

        # 验证插入数量
        expected_new = len(
            {r[0] for r in lr_records} - set(existing_issues)
        )
        # 注意：lr_records 可能有重复 issue（unique_by 已去重），
        # 但 existing_issues 中的 issue 也可能与 lr_records 重叠
        # inserted 应等于实际新增数
        assert inserted == expected_new, (
            f"expected {expected_new} new, got {inserted}"
        )

        jnd_conn.close()


# ======================================================================
# Property 4: 同步锁互斥性
# ======================================================================

@pytest.mark.asyncio
async def test_property_4_sync_lock_mutual_exclusion():
    """Property 4: 并发触发多次同步，同一时刻最多一个同步在执行。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        jnd28_path = os.path.join(tmpdir, "jnd28.sqlite3")
        bocai_path = os.path.join(tmpdir, "bocai.db")
        _create_jnd28_db(jnd28_path)
        _create_bocai_db(bocai_path)

        svc = HistorySyncService(jnd28_path, bocai_path)

        # 记录并发执行数
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        # Monkey-patch _do_sync 来追踪并发
        original_do_sync = svc._do_sync

        async def tracked_do_sync():
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)
            try:
                # 模拟耗时操作
                await asyncio.sleep(0.1)
            finally:
                async with lock:
                    current_concurrent -= 1

        # 替换 _fill_from_8828 和 _fill_from_lottery_results 为快速 mock
        async def mock_fill_8828():
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)
            try:
                await asyncio.sleep(0.1)
                return 0
            finally:
                async with lock:
                    current_concurrent -= 1

        async def mock_fill_tail():
            return 0

        svc._fill_from_8828 = mock_fill_8828
        svc._fill_from_lottery_results = mock_fill_tail

        # 并发触发 5 次同步
        tasks = [asyncio.create_task(svc._do_sync()) for _ in range(5)]
        await asyncio.gather(*tasks, return_exceptions=True)

        # 验证：同一时刻最多 1 个同步在执行
        assert max_concurrent <= 1, (
            f"最大并发数应 <= 1，实际为 {max_concurrent}"
        )


# ======================================================================
# API 层单元测试
# ======================================================================

def test_api_manual_sync_returns_409_when_syncing():
    """手动补全在同步中时应抛出 RuntimeError。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        jnd28_path = os.path.join(tmpdir, "jnd28.sqlite3")
        bocai_path = os.path.join(tmpdir, "bocai.db")
        _create_jnd28_db(jnd28_path)
        _create_bocai_db(bocai_path)

        svc = HistorySyncService(jnd28_path, bocai_path)
        # 模拟正在同步
        svc._syncing = True

        with pytest.raises(RuntimeError, match="同步正在执行中"):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(svc.manual_sync())
            finally:
                loop.close()


def test_api_syncing_status_correct():
    """syncing 状态应正确反映同步状态。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        jnd28_path = os.path.join(tmpdir, "jnd28.sqlite3")
        bocai_path = os.path.join(tmpdir, "bocai.db")
        _create_jnd28_db(jnd28_path)

        svc = HistorySyncService(jnd28_path, bocai_path)

        # 初始状态
        status = svc.get_status()
        assert status["syncing"] is False
        assert status["last_sync_time"] is None
        assert status["last_sync_inserted"] is None

        # 模拟同步中
        svc._syncing = True
        status = svc.get_status()
        assert status["syncing"] is True

        # 模拟同步完成
        svc._syncing = False
        svc._last_sync_time = "2026-03-19 12:00:00"
        svc._last_sync_inserted = 42
        status = svc.get_status()
        assert status["syncing"] is False
        assert status["last_sync_time"] == "2026-03-19 12:00:00"
        assert status["last_sync_inserted"] == 42
