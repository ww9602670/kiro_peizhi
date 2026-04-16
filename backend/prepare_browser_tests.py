"""准备浏览器E2E测试环境

停止所有运行中的策略，清理测试数据，准备新的测试账号
"""
import asyncio
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import aiosqlite
from app.models import db_ops


async def prepare_environment():
    """准备测试环境"""
    db = await aiosqlite.connect('data/bocai.db')
    db.row_factory = aiosqlite.Row
    
    try:
        print("\n" + "=" * 60)
        print("准备浏览器E2E测试环境")
        print("=" * 60)
        
        # 1. 停止所有运行中的策略
        print("\n[步骤1] 停止所有运行中的策略...")
        cursor = await db.execute("""
            UPDATE strategies 
            SET status = 'stopped'
            WHERE status = 'running'
        """)
        await db.commit()
        print(f"  ✓ 已停止 {cursor.rowcount} 个策略")
        
        # 2. 清除所有 worker 锁
        print("\n[步骤2] 清除所有 worker 锁...")
        cursor = await db.execute("""
            UPDATE gambling_accounts
            SET worker_lock_token = NULL,
                worker_lock_ts = NULL
        """)
        await db.commit()
        print(f"  ✓ 已清除 {cursor.rowcount} 个账号的 worker 锁")
        
        # 3. 查找或创建测试运营商
        print("\n[步骤3] 准备测试运营商...")
        test_username = "e2e_browser_test"
        
        # 检查是否存在
        cursor = await db.execute("""
            SELECT * FROM operators WHERE username = ?
        """, (test_username,))
        operator = await cursor.fetchone()
        
        if operator:
            operator_id = operator['id']
            print(f"  ✓ 使用现有运营商: {test_username} (ID: {operator_id})")
            
            # 清理该运营商的旧数据
            print("\n[步骤4] 清理旧测试数据...")
            
            # 删除投注记录
            cursor = await db.execute("""
                DELETE FROM bet_orders WHERE operator_id = ?
            """, (operator_id,))
            print(f"  ✓ 删除 {cursor.rowcount} 条投注记录")
            
            # 删除对账记录
            cursor = await db.execute("""
                DELETE FROM reconcile_records 
                WHERE account_id IN (
                    SELECT id FROM gambling_accounts WHERE operator_id = ?
                )
            """, (operator_id,))
            print(f"  ✓ 删除 {cursor.rowcount} 条对账记录")
            
            # 删除告警
            cursor = await db.execute("""
                DELETE FROM alerts WHERE operator_id = ?
            """, (operator_id,))
            print(f"  ✓ 删除 {cursor.rowcount} 条告警")
            
            # 删除策略
            cursor = await db.execute("""
                DELETE FROM strategies WHERE operator_id = ?
            """, (operator_id,))
            print(f"  ✓ 删除 {cursor.rowcount} 个策略")
            
            # 删除账号
            cursor = await db.execute("""
                DELETE FROM gambling_accounts WHERE operator_id = ?
            """, (operator_id,))
            print(f"  ✓ 删除 {cursor.rowcount} 个账号")
            
        else:
            # 创建新运营商
            cursor = await db.execute("""
                INSERT INTO operators (username, password, role, status, max_accounts)
                VALUES (?, ?, 'operator', 'active', 10)
            """, (test_username, "test123456"))
            operator_id = cursor.lastrowid
            print(f"  ✓ 创建新运营商: {test_username} (ID: {operator_id})")
        
        await db.commit()
        
        # 5. 显示测试账号信息
        print("\n" + "=" * 60)
        print("测试账号信息")
        print("=" * 60)
        print(f"\n运营商账号: {test_username}")
        print(f"运营商密码: test123456")
        print(f"运营商ID: {operator_id}")
        print("\n说明:")
        print("  1. 使用此账号登录前端进行测试")
        print("  2. 可以绑定真实平台账号 (test166/testuser01)")
        print("  3. 也可以使用 mock 平台进行测试")
        
        # 6. 显示下一步操作
        print("\n" + "=" * 60)
        print("下一步操作")
        print("=" * 60)
        print("\n1. 重启后端服务:")
        print("   cd backend")
        print("   uvicorn app.main:app --host 0.0.0.0 --port 8888")
        print("\n2. 重启前端服务:")
        print("   cd frontend")
        print("   pnpm dev")
        print("\n3. 安装 Playwright (如果未安装):")
        print("   cd backend")
        print("   pip install playwright pytest-playwright")
        print("   playwright install chromium")
        print("\n4. 创建截图目录:")
        print("   mkdir screenshots")
        print("\n5. 运行浏览器测试:")
        print("   pytest tests/e2e/browser/test_full_workflow.py -v -s")
        
    finally:
        await db.close()


if __name__ == '__main__':
    asyncio.run(prepare_environment())
