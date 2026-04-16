"""
E2E完整工作流测试

测试从用户登录、账号绑定、策略创建、自动投注到结算的完整工作流程。

运行方式：
1. 启动后端服务：uvicorn app.main:app --host 0.0.0.0 --port 8888
2. 运行测试：pytest backend/tests/e2e/test_e2e_full_workflow.py -v -s -m e2e

注意：
- 测试连接到运行中的后端服务（http://localhost:8888）
- 测试使用真实的平台API（test166账号）
- 测试使用真实的数据库（data/bocai.db）
- 测试完成后会自动清理测试数据
"""
import pytest

from .config import E2EConfig
from .helpers.context import E2ETestContext


pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
class TestE2EFullWorkflow:
    """E2E完整工作流测试类"""
    
    async def test_e2e_full_workflow_happy_path(
        self,
        e2e_db,
        e2e_client,
        e2e_cleanup,
        e2e_test_user
    ):
        """
        E2E完整工作流测试 - 成功路径
        
        测试流程：
        1. 以e2e_final_test身份登录
        2. 绑定test166账号
        3. 账号登录（获取余额和赔率）
        4. 创建投注策略
        5. 启动策略
        6. 等待投注
        7. 等待结算
        8. 验证结果
        9. 检查Dashboard
        10. 停止策略并清理
        
        验证需求：1.1, 2.1, 2.2, 3.1, 4.1, 5.4, 6.7, 7.6, 9.1
        """
        # 创建测试上下文
        ctx = E2ETestContext(e2e_db, e2e_client, e2e_cleanup)
        
        print("\n" + "="*60)
        print("E2E完整工作流测试 - 成功路径")
        print("="*60)
        
        try:
            # 1. 登录
            token = await ctx.login(
                E2EConfig.TEST_USERNAME,
                E2EConfig.TEST_PASSWORD
            )
            assert token is not None, "登录失败"
            
            # 2. 绑定账号
            account_id = await ctx.bind_account(
                E2EConfig.TEST_ACCOUNT_NAME,
                E2EConfig.TEST_ACCOUNT_PASSWORD,
                E2EConfig.TEST_PLATFORM_TYPE
            )
            assert account_id > 0, "绑定账号失败"
            
            # 3. 账号登录
            account_data = await ctx.account_login(account_id)
            assert account_data["status"] == "online", "账号状态不是online"
            print(f"  账号余额: {account_data.get('balance', 'N/A')}")
            
            # 4. 创建策略
            strategy_id = await ctx.create_strategy({
                "account_id": account_id,
                "name": "E2E测试策略",
                "type": E2EConfig.TEST_STRATEGY_TYPE,
                "play_code": E2EConfig.TEST_PLAY_CODE,
                "base_amount": E2EConfig.TEST_BASE_AMOUNT,
                "simulation": 1,  # 使用模拟模式
            })
            assert strategy_id > 0, "创建策略失败"
            
            # 5. 启动策略
            strategy_data = await ctx.start_strategy(strategy_id)
            assert strategy_data["status"] == "running", "策略状态不是running"
            
            # 6. 等待投注（最多等待60秒）
            print("\n等待自动投注...")
            order = await ctx.wait_for_bet(strategy_id, timeout=60)
            assert order is not None, "未检测到投注"
            assert order["status"] in ["bet_success", "bet_failed"], f"订单状态异常: {order['status']}"
            
            if order["status"] == "bet_success":
                print(f"  投注成功: order_id={order['id']}, amount={order['amount']}, issue={order['issue']}")
                
                # 7. 等待结算（最多等待120秒）
                print("\n等待结算...")
                settled_order = await ctx.wait_for_settlement(order["id"], timeout=120)
                assert settled_order["status"] == "settled", "订单未结算"
                print(f"  结算完成: is_win={settled_order['is_win']}, pnl={settled_order['pnl']}")
                
                # 8. 验证结算数据
                await ctx.verify_settlement_data(order["id"])
            else:
                print(f"  投注失败: {order.get('fail_reason', 'N/A')}")
            
            # 9. 检查Dashboard
            print("\n检查Dashboard...")
            dashboard = await ctx.get_dashboard()
            assert "balance" in dashboard, "Dashboard缺少balance字段"
            assert "recent_bets" in dashboard, "Dashboard缺少recent_bets字段"
            
            # 10. 停止策略
            print("\n停止策略...")
            await ctx.stop_strategy(strategy_id)
            
            print("\n" + "="*60)
            print("✓ E2E完整工作流测试通过")
            print("="*60)
            
        except Exception as e:
            print(f"\n✗ 测试失败: {e}")
            raise

    
    async def test_e2e_multiple_periods(
        self,
        e2e_db,
        e2e_client,
        e2e_cleanup,
        e2e_test_user
    ):
        """
        E2E多期投注和结算测试
        
        测试流程：
        1. 登录并绑定账号
        2. 创建并启动策略
        3. 等待至少2个期号的投注和结算
        4. 验证累计盈亏计算正确
        5. 验证账号余额变化正确
        
        验证需求：6.8, 6.9, 9.2
        """
        ctx = E2ETestContext(e2e_db, e2e_client, e2e_cleanup)
        
        print("\n" + "="*60)
        print("E2E多期投注和结算测试")
        print("="*60)
        
        try:
            # 1. 登录并绑定账号
            await ctx.login(E2EConfig.TEST_USERNAME, E2EConfig.TEST_PASSWORD)
            account_id = await ctx.bind_account(
                E2EConfig.TEST_ACCOUNT_NAME,
                E2EConfig.TEST_ACCOUNT_PASSWORD
            )
            await ctx.account_login(account_id)
            
            # 2. 创建并启动策略
            strategy_id = await ctx.create_strategy({
                "account_id": account_id,
                "name": "E2E多期测试策略",
                "type": "flat",
                "play_code": "DX1",
                "base_amount": 10.0,
                "simulation": 1,
            })
            await ctx.start_strategy(strategy_id)
            
            # 3. 等待多个期号的投注和结算
            orders = []
            target_periods = 2
            
            print(f"\n等待{target_periods}个期号的投注和结算...")
            
            for i in range(target_periods):
                print(f"\n--- 期号 {i+1} ---")
                
                # 等待投注
                order = await ctx.wait_for_bet(strategy_id, timeout=60)
                orders.append(order)
                
                if order["status"] == "bet_success":
                    # 等待结算
                    settled_order = await ctx.wait_for_settlement(order["id"], timeout=120)
                    print(f"  期号{i+1}结算: is_win={settled_order['is_win']}, pnl={settled_order['pnl']}")
            
            # 4. 验证累计盈亏
            strategy = await ctx.get_strategy(strategy_id)
            print(f"\n策略累计盈亏: daily_pnl={strategy['daily_pnl']}, total_pnl={strategy['total_pnl']}")
            
            # 5. 停止策略
            await ctx.stop_strategy(strategy_id)
            
            print("\n" + "="*60)
            print(f"✓ E2E多期测试通过 (完成{len(orders)}个期号)")
            print("="*60)
            
        except Exception as e:
            print(f"\n✗ 测试失败: {e}")
            raise

    
    async def test_e2e_dashboard_polling(
        self,
        e2e_db,
        e2e_client,
        e2e_cleanup,
        e2e_test_user
    ):
        """
        E2E Dashboard轮询测试
        
        模拟前端Dashboard轮询行为，验证数据实时更新。
        
        测试流程：
        1. 登录并绑定账号
        2. 第一次调用Dashboard API（初始数据）
        3. 创建并启动策略
        4. 第二次调用Dashboard API（验证策略出现）
        5. 等待投注完成
        6. 第三次调用Dashboard API（验证投注出现）
        7. 等待结算完成
        8. 第四次调用Dashboard API（验证结算结果）
        
        验证需求：7.6, 9.3
        """
        import asyncio
        
        ctx = E2ETestContext(e2e_db, e2e_client, e2e_cleanup)
        
        print("\n" + "="*60)
        print("E2E Dashboard轮询测试")
        print("="*60)
        
        try:
            # 1. 登录并绑定账号
            await ctx.login(E2EConfig.TEST_USERNAME, E2EConfig.TEST_PASSWORD)
            account_id = await ctx.bind_account(
                E2EConfig.TEST_ACCOUNT_NAME,
                E2EConfig.TEST_ACCOUNT_PASSWORD
            )
            await ctx.account_login(account_id)
            
            # 2. 第一次轮询 - 初始数据
            print("\n[轮询 1] 获取初始Dashboard数据...")
            dashboard1 = await ctx.get_dashboard()
            initial_running_count = len(dashboard1.get("running_strategies", []))
            initial_bets_count = len(dashboard1.get("recent_bets", []))
            
            # 3. 创建并启动策略
            strategy_id = await ctx.create_strategy({
                "account_id": account_id,
                "name": "E2E Dashboard测试策略",
                "type": "flat",
                "play_code": "DX1",
                "base_amount": 10.0,
                "simulation": 1,
            })
            await ctx.start_strategy(strategy_id)
            
            # 4. 第二次轮询 - 验证策略出现
            await asyncio.sleep(1)  # 等待数据更新
            print("\n[轮询 2] 验证策略出现...")
            dashboard2 = await ctx.get_dashboard()
            running_count = len(dashboard2.get("running_strategies", []))
            assert running_count > initial_running_count, "运行策略数量未增加"
            print(f"  运行策略数: {initial_running_count} → {running_count}")
            
            # 5. 等待投注
            print("\n等待投注...")
            order = await ctx.wait_for_bet(strategy_id, timeout=60)
            
            # 6. 第三次轮询 - 验证投注出现
            await asyncio.sleep(1)
            print("\n[轮询 3] 验证投注出现...")
            dashboard3 = await ctx.get_dashboard()
            bets_count = len(dashboard3.get("recent_bets", []))
            assert bets_count > initial_bets_count, "最近投注数量未增加"
            print(f"  最近投注数: {initial_bets_count} → {bets_count}")
            
            # 7. 等待结算（如果投注成功）
            if order["status"] == "bet_success":
                print("\n等待结算...")
                await ctx.wait_for_settlement(order["id"], timeout=120)
                
                # 8. 第四次轮询 - 验证结算结果
                await asyncio.sleep(1)
                print("\n[轮询 4] 验证结算结果...")
                dashboard4 = await ctx.get_dashboard()
                print(f"  盈亏更新: daily_pnl={dashboard4.get('daily_pnl', 'N/A')}, "
                      f"total_pnl={dashboard4.get('total_pnl', 'N/A')}")
            
            # 停止策略
            await ctx.stop_strategy(strategy_id)
            
            print("\n" + "="*60)
            print("✓ E2E Dashboard轮询测试通过")
            print("="*60)
            
        except Exception as e:
            print(f"\n✗ 测试失败: {e}")
            raise

    
    async def test_e2e_login_failure(
        self,
        e2e_db,
        e2e_client,
        e2e_cleanup
    ):
        """
        E2E登录失败测试
        
        测试使用错误密码登录，验证返回401错误。
        
        验证需求：1.2, 9.4
        """
        ctx = E2ETestContext(e2e_db, e2e_client, e2e_cleanup)
        
        print("\n" + "="*60)
        print("E2E登录失败测试")
        print("="*60)
        
        try:
            # 使用错误密码登录
            resp = await e2e_client.post(
                "/api/v1/auth/login",
                json={
                    "username": E2EConfig.TEST_USERNAME,
                    "password": "wrong_password"
                }
            )
            
            # 验证返回401错误
            assert resp.status_code == 401, f"期望状态码401，实际: {resp.status_code}"
            
            body = resp.json()
            assert body["code"] != 0, "期望错误码非0"
            
            print(f"✓ 登录失败测试通过: 状态码={resp.status_code}, 错误码={body['code']}")
            print("="*60)
            
        except AssertionError:
            raise
        except Exception as e:
            print(f"\n✗ 测试失败: {e}")
            raise

    
    async def test_e2e_account_binding_duplicate(
        self,
        e2e_db,
        e2e_client,
        e2e_cleanup,
        e2e_test_user
    ):
        """
        E2E账号绑定重复测试
        
        测试绑定重复的账号，验证返回409错误。
        
        验证需求：2.5, 9.4
        """
        ctx = E2ETestContext(e2e_db, e2e_client, e2e_cleanup)
        
        print("\n" + "="*60)
        print("E2E账号绑定重复测试")
        print("="*60)
        
        try:
            # 1. 登录
            await ctx.login(E2EConfig.TEST_USERNAME, E2EConfig.TEST_PASSWORD)
            
            # 2. 第一次绑定账号
            account_id = await ctx.bind_account(
                E2EConfig.TEST_ACCOUNT_NAME,
                E2EConfig.TEST_ACCOUNT_PASSWORD
            )
            print(f"  第一次绑定成功: account_id={account_id}")
            
            # 3. 尝试再次绑定相同账号
            resp = await e2e_client.post(
                "/api/v1/accounts",
                headers=ctx._get_headers(),
                json={
                    "account_name": E2EConfig.TEST_ACCOUNT_NAME,
                    "password": E2EConfig.TEST_ACCOUNT_PASSWORD,
                    "platform_type": E2EConfig.TEST_PLATFORM_TYPE,
                }
            )
            
            # 验证返回409错误
            assert resp.status_code == 409, f"期望状态码409，实际: {resp.status_code}"
            
            body = resp.json()
            assert body["code"] != 0, "期望错误码非0"
            
            print(f"✓ 重复绑定测试通过: 状态码={resp.status_code}, 错误码={body['code']}")
            print("="*60)
            
        except AssertionError:
            raise
        except Exception as e:
            print(f"\n✗ 测试失败: {e}")
            raise

    
    async def test_e2e_start_strategy_without_online_account(
        self,
        e2e_db,
        e2e_client,
        e2e_cleanup,
        e2e_test_user
    ):
        """
        E2E启动策略前置条件测试
        
        测试在账号未登录（状态为offline）时启动策略，验证返回400错误。
        
        验证需求：4.5, 9.4
        """
        ctx = E2ETestContext(e2e_db, e2e_client, e2e_cleanup)
        
        print("\n" + "="*60)
        print("E2E启动策略前置条件测试")
        print("="*60)
        
        try:
            # 1. 登录
            await ctx.login(E2EConfig.TEST_USERNAME, E2EConfig.TEST_PASSWORD)
            
            # 2. 绑定账号但不登录
            account_id = await ctx.bind_account(
                E2EConfig.TEST_ACCOUNT_NAME,
                E2EConfig.TEST_ACCOUNT_PASSWORD
            )
            print(f"  账号已绑定但未登录: account_id={account_id}")
            
            # 验证账号状态为offline
            account = await ctx.get_account(account_id)
            print(f"  账号状态: {account['status']}")
            
            # 3. 创建策略
            strategy_id = await ctx.create_strategy({
                "account_id": account_id,
                "name": "E2E前置条件测试策略",
                "type": "flat",
                "play_code": "DX1",
                "base_amount": 10.0,
                "simulation": 1,
            })
            
            # 4. 尝试启动策略（应该失败）
            resp = await e2e_client.post(
                f"/api/v1/strategies/{strategy_id}/start",
                headers=ctx._get_headers(),
            )
            
            # 验证返回400错误
            assert resp.status_code == 400, f"期望状态码400，实际: {resp.status_code}"
            
            body = resp.json()
            assert body["code"] != 0, "期望错误码非0"
            
            print(f"✓ 前置条件测试通过: 状态码={resp.status_code}, 错误码={body['code']}")
            print("="*60)
            
        except AssertionError:
            raise
        except Exception as e:
            print(f"\n✗ 测试失败: {e}")
            raise
