"""
E2E测试fixtures

提供E2E测试所需的数据库连接、HTTP客户端和测试数据清理功能。

注意：E2E测试连接到独立运行的后端服务（http://localhost:8888）
"""
import pytest
import aiosqlite
import httpx

from app.models.db_ops import operator_create
from .config import E2EConfig


# 标记E2E测试，使其跳过常规测试的自动数据库初始化
pytestmark = pytest.mark.e2e


@pytest.fixture
async def e2e_db():
    """
    E2E测试数据库fixture
    
    连接到真实的SQLite数据库（data/bocai.db）。
    """
    db_path = "data/bocai.db"
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")
    
    yield conn
    
    await conn.close()


@pytest.fixture
async def e2e_client():
    """
    E2E测试HTTP客户端fixture
    
    创建httpx.AsyncClient用于调用后端API。
    注意：需要后端服务已经启动（uvicorn app.main:app --host 0.0.0.0 --port 8888）
    """
    async with httpx.AsyncClient(
        base_url=E2EConfig.API_BASE_URL,
        timeout=30.0
    ) as client:
        yield client


@pytest.fixture
async def e2e_cleanup(e2e_client):
    """
    E2E测试清理fixture
    
    在测试完成后通过API清理测试数据。
    """
    # 记录测试开始前的数据状态
    cleanup_data = {
        "operator_ids": [],
        "account_ids": [],
        "strategy_ids": [],
        "token": None,  # 用于API调用的token
    }
    
    yield cleanup_data
    
    # 测试完成后通过API清理数据
    if not cleanup_data.get("token"):
        return  # 没有token，无法清理
    
    headers = {"Authorization": f"Bearer {cleanup_data['token']}"}
    
    # 删除策略
    if cleanup_data.get("strategy_ids"):
        for strategy_id in cleanup_data["strategy_ids"]:
            try:
                await e2e_client.delete(
                    f"/api/v1/strategies/{strategy_id}",
                    headers=headers
                )
            except Exception as e:
                print(f"清理策略失败 strategy_id={strategy_id}: {e}")
    
    # 删除账号
    if cleanup_data.get("account_ids"):
        for account_id in cleanup_data["account_ids"]:
            try:
                await e2e_client.delete(
                    f"/api/v1/accounts/{account_id}",
                    headers=headers
                )
            except Exception as e:
                print(f"清理账号失败 account_id={account_id}: {e}")


@pytest.fixture
async def e2e_test_user(e2e_db):
    """
    创建E2E测试用户fixture
    
    确保e2e_final_test用户存在。
    如果不存在则创建，如果已存在则返回现有用户。
    """
    # 检查用户是否已存在
    cursor = await e2e_db.execute(
        "SELECT id FROM operators WHERE username=?",
        (E2EConfig.TEST_USERNAME,)
    )
    row = await cursor.fetchone()
    
    if row:
        # 用户已存在
        operator_id = row["id"]
    else:
        # 创建新用户
        operator_id = await operator_create(
            e2e_db,
            username=E2EConfig.TEST_USERNAME,
            password=E2EConfig.TEST_PASSWORD,
            role="operator",
            max_accounts=10,
        )
        await e2e_db.commit()
    
    return operator_id
