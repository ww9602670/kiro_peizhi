"""
E2E余额扣减测试

测试投注后余额是否正确扣减，以及结算后余额是否正确更新。

注意：
- 此测试使用非模拟模式 (simulation=0)
- 会产生真实的余额变化
- 需要确保账号有足够余额
"""
import pytest
from .config import E2EConfig
from .helpers.context import E2ETestContext
from .helpers.balance_tracker import BalanceTracker


pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
class TestBalanceDeduction:
    """余额扣减测试类"""
    
    async def test_balance_deduction_on_bet(
        self,
        e2e_db,
        e2e_client,
        e2e_cleanup,
        e2e_test_user
    ):
        """
        测试投注时余额扣减
        
        验证：
        1. 投注前记录余额
        2. 执行投注
        3. 验证余额减少了投注金额
        4. 验证订单状态为bet_success
        """
        ctx = E2ETestContext(e2e_db, e2e_client, e2e_cleanup)
        
        print("\n" + "="*60)
        print("E2E余额扣减测试 - 投注时扣减")
        print("="*60)
        
        # 1. 登录
        await ctx.login(E2EConfig.TEST_USERNAME, E2EConfig.TEST_PASSWORD)
        
        # 2. 使用现有账号
        account_id = await ctx.bind_account(
            E2EConfig.TEST_ACCOUNT_NAME,
            E2EConfig.TEST_ACCOUNT_PASSWORD,
            E2EConfig.TEST_PLATFORM_TYPE
        )
        
        # 3. 账号登录
        await ctx.account_login(account_id)
        
        # 4. 初始化余额追踪器
        tracker = BalanceTracker(ctx, account_id)
        await tracker.snapshot("投注前")
        
        # 5. 创建策略（非模拟模式）
        strategy_id = await ctx.create_strategy({
            "account_id": account_id,
            "name": "E2E余额测试策略",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
            "simulation": 0,  # ⚠️ 非模拟模式，会产生真实余额变化
        })
        
        # 6. 启动策略
        await ctx.start_strategy(strategy_id)
        
        # 7. 等待投注
        print("\n等待投注...")
        order = await ctx.wait_for_bet(strategy_id, timeout=60)
        
        # 8. 验证投注成功
        assert order["status"] == "bet_success", f"投注失败: {order.get('fail_reason', 'N/A')}"
        assert order["simulation"] == False, "应该是非模拟模式"
        
        # 9. 记录投注后余额并验证扣减
        await tracker.snapshot("投注后")
        tracker.verify_deduction(order["amount"])
        tracker.print_history()
        
        # 10. 停止策略
        await ctx.stop_strategy(strategy_id)
    
    async def test_balance_update_on_settlement(
        self,
        e2e_db,
        e2e_client,
        e2e_cleanup,
        e2e_test_user
    ):
        """
        测试结算时余额更新
        
        验证：
        1. 投注前记录余额
        2. 执行投注
        3. 等待结算
        4. 验证余额根据输赢正确更新
        """
        ctx = E2ETestContext(e2e_db, e2e_client, e2e_cleanup)
        
        print("\n" + "="*60)
        print("E2E余额扣减测试 - 结算时更新")
        print("="*60)
        
        # 1. 登录
        await ctx.login(E2EConfig.TEST_USERNAME, E2EConfig.TEST_PASSWORD)
        
        # 2. 使用现有账号
        account_id = await ctx.bind_account(
            E2EConfig.TEST_ACCOUNT_NAME,
            E2EConfig.TEST_ACCOUNT_PASSWORD,
            E2EConfig.TEST_PLATFORM_TYPE
        )
        
        # 3. 账号登录
        await ctx.account_login(account_id)
        
        # 4. 初始化余额追踪器
        tracker = BalanceTracker(ctx, account_id)
        await tracker.snapshot("投注前")
        
        # 5. 创建策略（非模拟模式）
        strategy_id = await ctx.create_strategy({
            "account_id": account_id,
            "name": "E2E结算测试策略",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
            "simulation": 0,  # ⚠️ 非模拟模式
        })
        
        # 6. 启动策略
        await ctx.start_strategy(strategy_id)
        
        # 7. 等待投注
        print("\n等待投注...")
        order = await ctx.wait_for_bet(strategy_id, timeout=60)
        assert order["status"] == "bet_success"
        
        # 8. 记录投注后余额
        await tracker.snapshot("投注后")
        tracker.verify_deduction(order["amount"])
        
        # 9. 等待结算（开奖驱动）
        print("\n等待结算...")
        settled_order = await ctx.wait_for_settlement(order["id"], timeout=120)
        
        is_win = settled_order["is_win"]
        pnl = settled_order["pnl"]
        print(f"  结算结果: is_win={is_win}, pnl={pnl}")
        
        # 10. 记录结算后余额并验证
        await tracker.snapshot("结算后")
        tracker.verify_settlement(pnl)
        tracker.print_history()
        
        # 11. 停止策略
        await ctx.stop_strategy(strategy_id)
    
    async def test_simulation_mode_no_balance_change(
        self,
        e2e_db,
        e2e_client,
        e2e_cleanup,
        e2e_test_user
    ):
        """
        测试模拟模式不影响余额
        
        验证：
        1. 投注前记录余额
        2. 使用模拟模式投注
        3. 验证余额没有变化
        """
        ctx = E2ETestContext(e2e_db, e2e_client, e2e_cleanup)
        
        print("\n" + "="*60)
        print("E2E余额扣减测试 - 模拟模式不扣减")
        print("="*60)
        
        # 1. 登录
        await ctx.login(E2EConfig.TEST_USERNAME, E2EConfig.TEST_PASSWORD)
        
        # 2. 使用现有账号
        account_id = await ctx.bind_account(
            E2EConfig.TEST_ACCOUNT_NAME,
            E2EConfig.TEST_ACCOUNT_PASSWORD,
            E2EConfig.TEST_PLATFORM_TYPE
        )
        
        # 3. 账号登录
        await ctx.account_login(account_id)
        
        # 4. 初始化余额追踪器
        tracker = BalanceTracker(ctx, account_id)
        await tracker.snapshot("投注前")
        
        # 5. 创建策略（模拟模式）
        strategy_id = await ctx.create_strategy({
            "account_id": account_id,
            "name": "E2E模拟模式测试",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
            "simulation": 1,  # ✅ 模拟模式
        })
        
        # 6. 启动策略
        await ctx.start_strategy(strategy_id)
        
        # 7. 等待投注
        print("\n等待投注...")
        order = await ctx.wait_for_bet(strategy_id, timeout=60)
        assert order["simulation"] == True, "应该是模拟模式"
        
        print(f"  投注金额: {order['amount']} (模拟)")
        
        # 8. 记录投注后余额并验证没有变化
        await tracker.snapshot("投注后")
        
        # 验证余额没有变化（差异应该为0）
        snapshots = tracker.get_balance_history()
        balance_before = snapshots[0][1]
        balance_after = snapshots[1][1]
        balance_diff = abs(balance_after - balance_before)
        
        print(f"\n余额验证:")
        print(f"  投注前余额: {balance_before}")
        print(f"  投注后余额: {balance_after}")
        print(f"  差异: {balance_diff}")
        
        assert balance_diff < 0.01, f"模拟模式不应该改变余额！初始={balance_before}, 现在={balance_after}"
        
        print("✓ 模拟模式余额验证通过（余额未变化）")
        tracker.print_history()
        
        # 9. 停止策略
        await ctx.stop_strategy(strategy_id)
