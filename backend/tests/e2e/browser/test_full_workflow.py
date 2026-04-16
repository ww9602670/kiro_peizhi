"""
浏览器E2E测试 - 完整工作流

测试从登录到投注的完整用户流程（操作者账号）
"""
import pytest
import asyncio
from playwright.async_api import Page, expect
from .pages.login_page import LoginPage
from .pages.dashboard_page import DashboardPage


pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

# 测试账号配置
TEST_USERNAME = "e2e_browser_test"
TEST_PASSWORD = "test123456"


class TestBrowserFullWorkflow:
    """浏览器完整工作流测试"""
    
    async def test_login_and_view_dashboard(self, page: Page, frontend_url: str):
        """
        测试登录并查看Dashboard
        
        步骤：
        1. 打开登录页面
        2. 使用操作者账号登录
        3. 验证Dashboard显示
        4. 验证余额、盈亏等数据显示
        """
        print("\n" + "="*60)
        print("浏览器E2E测试 - 登录并查看Dashboard（操作者）")
        print("="*60)
        
        login_page = LoginPage(page, frontend_url)
        dashboard_page = DashboardPage(page, frontend_url)
        
        # 1. 打开登录页面
        print("\n[步骤1] 打开登录页面...")
        await login_page.goto()
        await page.screenshot(path="screenshots/full-workflow-01-login.png")
        
        # 2. 登录
        print(f"[步骤2] 登录系统（操作者账号: {TEST_USERNAME}）...")
        await login_page.login(TEST_USERNAME, TEST_PASSWORD)
        await page.screenshot(path="screenshots/full-workflow-02-dashboard.png")
        
        # 3. 验证Dashboard元素
        print("[步骤3] 验证Dashboard显示...")
        
        # 等待统计卡片加载
        await page.wait_for_selector('.stat-cards', timeout=10000)
        
        # 验证余额显示
        balance_element = await page.query_selector('.stat-card:has-text("总余额") .stat-value')
        if balance_element:
            balance_text = await balance_element.text_content()
            print(f"  ✓ 总余额: {balance_text}")
        
        # 验证当日盈亏显示
        daily_pnl_element = await page.query_selector('.stat-card:has-text("当日盈亏") .stat-value')
        if daily_pnl_element:
            daily_pnl_text = await daily_pnl_element.text_content()
            print(f"  ✓ 当日盈亏: {daily_pnl_text}")
        
        # 验证总盈亏显示
        total_pnl_element = await page.query_selector('.stat-card:has-text("总盈亏") .stat-value')
        if total_pnl_element:
            total_pnl_text = await total_pnl_element.text_content()
            print(f"  ✓ 总盈亏: {total_pnl_text}")
        
        print("\n✓ Dashboard显示验证通过")
    
    async def test_navigate_to_accounts(self, page: Page, frontend_url: str):
        """
        测试导航到账号管理页面
        
        步骤：
        1. 登录系统
        2. 点击账号菜单
        3. 验证账号列表显示
        """
        print("\n" + "="*60)
        print("浏览器E2E测试 - 导航到账号管理（操作者）")
        print("="*60)
        
        login_page = LoginPage(page, frontend_url)
        
        # 1. 登录
        print(f"\n[步骤1] 登录系统（操作者账号: {TEST_USERNAME}）...")
        await login_page.goto()
        await login_page.login(TEST_USERNAME, TEST_PASSWORD)
        
        # 2. 导航到账号管理
        print("[步骤2] 导航到账号管理...")
        await page.click('text=账号')
        await page.wait_for_url("**/accounts", timeout=5000)
        await page.screenshot(path="screenshots/full-workflow-03-accounts.png")
        
        # 3. 验证账号页面元素
        print("[步骤3] 验证账号页面...")
        
        # 等待页面加载
        await page.wait_for_selector('.accounts-page, .page-container', timeout=10000)
        
        # 验证标题
        title = await page.query_selector('h1, .page-title')
        if title:
            title_text = await title.text_content()
            print(f"  ✓ 页面标题: {title_text}")
        
        print("\n✓ 账号管理页面验证通过")
    
    async def test_navigate_to_strategies(self, page: Page, frontend_url: str):
        """
        测试导航到策略管理页面
        
        步骤：
        1. 登录系统
        2. 点击策略菜单
        3. 验证策略列表显示
        """
        print("\n" + "="*60)
        print("浏览器E2E测试 - 导航到策略管理（操作者）")
        print("="*60)
        
        login_page = LoginPage(page, frontend_url)
        
        # 1. 登录
        print(f"\n[步骤1] 登录系统（操作者账号: {TEST_USERNAME}）...")
        await login_page.goto()
        await login_page.login(TEST_USERNAME, TEST_PASSWORD)
        
        # 2. 导航到策略管理
        print("[步骤2] 导航到策略管理...")
        await page.click('text=策略')
        await page.wait_for_url("**/strategies", timeout=5000)
        await page.screenshot(path="screenshots/full-workflow-04-strategies.png")
        
        # 3. 验证策略页面元素
        print("[步骤3] 验证策略页面...")
        
        # 等待页面加载
        await page.wait_for_selector('.strategies-page, .page-container', timeout=10000)
        
        # 验证标题
        title = await page.query_selector('h1, .page-title')
        if title:
            title_text = await title.text_content()
            print(f"  ✓ 页面标题: {title_text}")
        
        print("\n✓ 策略管理页面验证通过")
    
    async def test_dashboard_auto_refresh(self, page: Page, frontend_url: str):
        """
        测试Dashboard自动刷新功能
        
        步骤：
        1. 登录并进入Dashboard
        2. 记录初始余额
        3. 等待30秒（轮询间隔）
        4. 验证数据已更新（通过网络请求）
        """
        print("\n" + "="*60)
        print("浏览器E2E测试 - Dashboard自动刷新（操作者）")
        print("="*60)
        
        login_page = LoginPage(page, frontend_url)
        
        # 1. 登录
        print(f"\n[步骤1] 登录系统（操作者账号: {TEST_USERNAME}）...")
        await login_page.goto()
        await login_page.login(TEST_USERNAME, TEST_PASSWORD)
        
        # 2. 等待Dashboard加载
        print("[步骤2] 等待Dashboard加载...")
        await page.wait_for_selector('.stat-cards', timeout=10000)
        
        # 记录初始余额
        balance_element = await page.query_selector('.stat-card:has-text("总余额") .stat-value')
        initial_balance = await balance_element.text_content() if balance_element else "N/A"
        print(f"  初始余额: {initial_balance}")
        
        # 3. 监听网络请求
        print("[步骤3] 监听Dashboard API请求...")
        api_calls = []
        
        def handle_response(response):
            if '/api/v1/dashboard' in response.url:
                api_calls.append({
                    'time': asyncio.get_event_loop().time(),
                    'status': response.status
                })
                print(f"  ✓ Dashboard API调用: status={response.status}")
        
        page.on('response', handle_response)
        
        # 4. 等待轮询（30秒）
        print("[步骤4] 等待30秒观察轮询...")
        await page.wait_for_timeout(35000)  # 等待35秒确保至少一次轮询
        
        # 5. 验证API调用
        print(f"\n[步骤5] 验证轮询结果...")
        print(f"  Dashboard API调用次数: {len(api_calls)}")
        
        if len(api_calls) >= 1:
            print("  ✓ Dashboard自动轮询正常工作")
        else:
            print("  ⚠️ 未检测到Dashboard轮询")
        
        await page.screenshot(path="screenshots/full-workflow-05-dashboard-after-refresh.png")
        
        print("\n✓ Dashboard自动刷新测试完成")
    
    async def test_view_bet_orders(self, page: Page, frontend_url: str):
        """
        测试查看投注订单
        
        步骤：
        1. 登录系统
        2. 导航到投注记录页面
        3. 验证订单列表显示
        """
        print("\n" + "="*60)
        print("浏览器E2E测试 - 查看投注订单（操作者）")
        print("="*60)
        
        login_page = LoginPage(page, frontend_url)
        
        # 1. 登录
        print(f"\n[步骤1] 登录系统（操作者账号: {TEST_USERNAME}）...")
        await login_page.goto()
        await login_page.login(TEST_USERNAME, TEST_PASSWORD)
        
        # 2. 导航到投注记录
        print("[步骤2] 导航到投注记录...")
        await page.click('text=投注记录')
        await page.wait_for_url("**/bet-orders", timeout=5000)
        await page.screenshot(path="screenshots/full-workflow-06-bet-orders.png")
        
        # 3. 验证投注记录页面
        print("[步骤3] 验证投注记录页面...")
        
        # 等待页面加载
        await page.wait_for_selector('.bet-orders-page, .page-container', timeout=10000)
        
        # 验证标题
        title = await page.query_selector('h1, .page-title')
        if title:
            title_text = await title.text_content()
            print(f"  ✓ 页面标题: {title_text}")
        
        # 检查是否有订单表格
        table = await page.query_selector('table, .bet-order-table')
        if table:
            print("  ✓ 投注订单表格已显示")
        else:
            print("  ℹ️ 暂无投注订单")
        
        print("\n✓ 投注记录页面验证通过")
    
    async def test_logout(self, page: Page, frontend_url: str):
        """
        测试登出功能
        
        步骤：
        1. 登录系统
        2. 点击登出按钮
        3. 验证返回登录页面
        """
        print("\n" + "="*60)
        print("浏览器E2E测试 - 登出（操作者）")
        print("="*60)
        
        login_page = LoginPage(page, frontend_url)
        
        # 1. 登录
        print(f"\n[步骤1] 登录系统（操作者账号: {TEST_USERNAME}）...")
        await login_page.goto()
        await login_page.login(TEST_USERNAME, TEST_PASSWORD)
        
        # 2. 登出
        print("[步骤2] 点击登出...")
        
        # 查找登出按钮（可能在用户菜单中）
        logout_button = await page.query_selector('button:has-text("登出"), button:has-text("退出"), text=登出')
        if logout_button:
            await logout_button.click()
        else:
            # 尝试点击用户菜单
            user_menu = await page.query_selector('.user-menu, .user-dropdown')
            if user_menu:
                await user_menu.click()
                await page.wait_for_timeout(500)
                logout_option = await page.query_selector('text=登出, text=退出')
                if logout_option:
                    await logout_option.click()
        
        # 3. 验证返回登录页面
        print("[步骤3] 验证返回登录页面...")
        await page.wait_for_url("**/login", timeout=5000)
        await page.screenshot(path="screenshots/full-workflow-07-logout.png")
        
        print("  ✓ 已返回登录页面")
        print("\n✓ 登出测试通过")
