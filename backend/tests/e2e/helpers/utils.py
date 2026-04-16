"""
E2E测试辅助工具函数

提供等待条件、数据库验证、测试数据生成等工具函数。
"""
import asyncio
import time
import uuid
from typing import Callable, Awaitable
from dataclasses import dataclass


async def wait_for_condition(
    condition: Callable[[], Awaitable[bool]],
    timeout: int = 30,
    interval: float = 0.5,
    error_message: str = "条件未满足"
) -> None:
    """
    等待条件满足或超时
    
    Args:
        condition: 异步条件函数，返回bool
        timeout: 超时时间（秒）
        interval: 轮询间隔（秒）
        error_message: 超时错误信息
        
    Raises:
        TimeoutError: 超时时抛出
    """
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        if await condition():
            return
        await asyncio.sleep(interval)
    
    elapsed = time.time() - start_time
    raise TimeoutError(f"{error_message} (超时: {elapsed:.1f}秒)")



async def verify_db_state(
    db,
    table: str,
    conditions: dict,
    expected: dict
) -> None:
    """
    验证数据库状态
    
    Args:
        db: 数据库连接
        table: 表名
        conditions: 查询条件字典
        expected: 期望的字段值字典
        
    Raises:
        AssertionError: 验证失败时抛出
    """
    # 构建查询
    where_parts = []
    values = []
    for key, value in conditions.items():
        where_parts.append(f"{key}=?")
        values.append(value)
    
    where_clause = " AND ".join(where_parts)
    query = f"SELECT * FROM {table} WHERE {where_clause}"
    
    # 执行查询
    cursor = await db.execute(query, tuple(values))
    row = await cursor.fetchone()
    
    assert row is not None, f"未找到记录: {table} {conditions}"
    
    # 验证字段值
    for key, expected_value in expected.items():
        actual_value = row[key]
        assert actual_value == expected_value, \
            f"{table}.{key}: 期望 {expected_value}, 实际 {actual_value}"



@dataclass
class E2ETestData:
    """E2E测试数据"""
    username: str
    password: str
    account_name: str
    account_password: str
    platform_type: str
    strategy_name: str
    strategy_type: str
    play_code: str
    base_amount: float


def generate_unique_test_data(prefix: str = "e2e") -> E2ETestData:
    """
    生成唯一的测试数据
    
    Args:
        prefix: 测试数据前缀
        
    Returns:
        E2ETestData对象
    """
    uid = uuid.uuid4().hex[:8]
    
    return E2ETestData(
        username=f"{prefix}_user_{uid}",
        password="test123456",
        account_name=f"{prefix}_acc_{uid}",
        account_password="acc123456",
        platform_type="JND28WEB",
        strategy_name=f"{prefix}_strat_{uid}",
        strategy_type="flat",
        play_code="DX1",
        base_amount=10.0,
    )



async def cleanup_test_data(db, operator_id: int) -> None:
    """
    清理测试数据
    
    删除指定operator创建的所有数据，包括：
    - 策略
    - 投注订单
    - 账号
    - Operator本身
    
    Args:
        db: 数据库连接
        operator_id: Operator ID
    """
    # 1. 获取该operator的所有账号
    cursor = await db.execute(
        "SELECT id FROM accounts WHERE operator_id=?",
        (operator_id,)
    )
    account_rows = await cursor.fetchall()
    account_ids = [row["id"] for row in account_rows]
    
    # 2. 删除投注订单
    for account_id in account_ids:
        await db.execute(
            "DELETE FROM bet_orders WHERE account_id=?",
            (account_id,)
        )
    
    # 3. 删除策略
    await db.execute(
        "DELETE FROM strategies WHERE operator_id=?",
        (operator_id,)
    )
    
    # 4. 删除账号
    await db.execute(
        "DELETE FROM accounts WHERE operator_id=?",
        (operator_id,)
    )
    
    # 5. 删除operator
    await db.execute(
        "DELETE FROM operators WHERE id=?",
        (operator_id,)
    )
    
    await db.commit()
    print(f"✓ 清理测试数据完成: operator_id={operator_id}")
