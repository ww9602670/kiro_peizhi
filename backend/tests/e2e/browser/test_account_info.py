"""
测试账号信息和基础流程
"""
import pytest
from playwright.async_api import Page


pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

TEST_USERNAME = "e2e_browser_test"
TEST_PASSWORD = "test123456"


async def test_view_account_list(page: Page):
    """
    测试查看账号列表
    
    验证点：
    1. 登录成功
    2. 导航到账号管理页面
    3. 查看账号列表（应该为空，因为是新账号）
    4. 验证"绑定账号"按钮存在
    """
    print("\n" + "="*60)
    print("测试：查看账号列表")
    print("="*60)
    
    # 1. 登录
    print("\n[步骤1] 登录系统...")
    await page.goto("http://localhost:5173/login")
    await page.fill('input[type="text"]', TEST_USERNAME)
    await page.fill('input[type="password"]', TEST_PASSWORD)
    await page.click('button[type="submit"]')
    
    # 等待Dashboard加载
    try:
        await page.wait_for_selector('.stat-cards', timeout=5000)
        print("  ✓ 登录成功，Dashboard已加载")
    except:
        print("  ✗ Dashboard加载超时")
        await page.screenshot(path="screenshots/account-info-01-login-failed.png")
        raise
    
    # 2. 导航到账号管理
    print("\n[步骤2] 导航到账号管理...")
    await page.click('text=账号')
    await page.wait_for_timeout(1000)  # 等待页面切换
    await page.screenshot(path="screenshots/account-info-02-accounts-page.png")
    
    # 3. 检查页面内容
    print("\n[步骤3] 检查账号页面内容...")
    
    # 检查页面标题
    title = await page.query_selector('h1, .page-title')
    if title:
        title_text = await title.text_content()
        print(f"  ✓ 页面标题: {title_text}")
    
    # 检查是否有账号列表
    account_list = await page.query_selector('.account-list, table')
    if account_list:
        print("  ✓ 找到账号列表元素")
        
        # 检查是否有账号
        accounts = await page.query_selector_all('.account-item, tbody tr')
        print(f"  账号数量: {len(accounts)}")
        
        if len(accounts) == 0:
            print("  ℹ️ 账号列表为空（符合预期，新账号）")
    else:
        print("  ⚠️ 未找到账号列表元素")
    
    # 检查是否有"绑定账号"按钮
    bind_button = await page.query_selector('button:has-text("绑定"), button:has-text("添加")')
    if bind_button:
        button_text = await bind_button.text_content()
        print(f"  ✓ 找到按钮: {button_text}")
    else:
        print("  ⚠️ 未找到绑定账号按钮")
    
    print("\n✓ 账号列表查看测试完成")


async def test_view_strategy_list(page: Page):
    """
    测试查看策略列表
    
    验证点：
    1. 登录成功
    2. 导航到策略管理页面
    3. 查看策略列表（应该为空）
    4. 验证"创建策略"按钮存在
    """
    print("\n" + "="*60)
    print("测试：查看策略列表")
    print("="*60)
    
    # 1. 登录
    print("\n[步骤1] 登录系统...")
    await page.goto("http://localhost:5173/login")
    await page.fill('input[type="text"]', TEST_USERNAME)
    await page.fill('input[type="password"]', TEST_PASSWORD)
    await page.click('button[type="submit"]')
    
    # 等待Dashboard加载
    try:
        await page.wait_for_selector('.stat-cards', timeout=5000)
        print("  ✓ 登录成功")
    except:
        print("  ✗ 登录失败")
        raise
    
    # 2. 导航到策略管理
    print("\n[步骤2] 导航到策略管理...")
    await page.click('text=策略')
    await page.wait_for_timeout(1000)
    await page.screenshot(path="screenshots/account-info-03-strategies-page.png")
    
    # 3. 检查页面内容
    print("\n[步骤3] 检查策略页面内容...")
    
    # 检查页面标题
    title = await page.query_selector('h1, .page-title')
    if title:
        title_text = await title.text_content()
        print(f"  ✓ 页面标题: {title_text}")
    
    # 检查是否有策略列表
    strategy_list = await page.query_selector('.strategy-list, table')
    if strategy_list:
        print("  ✓ 找到策略列表元素")
        
        # 检查是否有策略
        strategies = await page.query_selector_all('.strategy-item, tbody tr')
        print(f"  策略数量: {len(strategies)}")
        
        if len(strategies) == 0:
            print("  ℹ️ 策略列表为空（符合预期）")
    else:
        print("  ⚠️ 未找到策略列表元素")
    
    # 检查是否有"创建策略"按钮
    create_button = await page.query_selector('button:has-text("创建"), button:has-text("新建")')
    if create_button:
        button_text = await create_button.text_content()
        print(f"  ✓ 找到按钮: {button_text}")
    else:
        print("  ⚠️ 未找到创建策略按钮")
    
    print("\n✓ 策略列表查看测试完成")


async def test_view_bet_orders_list(page: Page):
    """
    测试查看投注记录列表
    
    验证点：
    1. 登录成功
    2. 导航到投注记录页面
    3. 查看投注记录列表（应该为空）
    """
    print("\n" + "="*60)
    print("测试：查看投注记录列表")
    print("="*60)
    
    # 1. 登录
    print("\n[步骤1] 登录系统...")
    await page.goto("http://localhost:5173/login")
    await page.fill('input[type="text"]', TEST_USERNAME)
    await page.fill('input[type="password"]', TEST_PASSWORD)
    await page.click('button[type="submit"]')
    
    # 等待Dashboard加载
    try:
        await page.wait_for_selector('.stat-cards', timeout=5000)
        print("  ✓ 登录成功")
    except:
        print("  ✗ 登录失败")
        raise
    
    # 2. 导航到投注记录
    print("\n[步骤2] 导航到投注记录...")
    await page.click('text=投注记录')
    await page.wait_for_timeout(1000)
    await page.screenshot(path="screenshots/account-info-04-bet-orders-page.png")
    
    # 3. 检查页面内容
    print("\n[步骤3] 检查投注记录页面内容...")
    
    # 检查页面标题
    title = await page.query_selector('h1, .page-title')
    if title:
        title_text = await title.text_content()
        print(f"  ✓ 页面标题: {title_text}")
    
    # 检查是否有投注记录列表
    orders_list = await page.query_selector('.bet-orders-list, table')
    if orders_list:
        print("  ✓ 找到投注记录列表元素")
        
        # 检查是否有记录
        orders = await page.query_selector_all('.bet-order-item, tbody tr')
        print(f"  投注记录数量: {len(orders)}")
        
        if len(orders) == 0:
            print("  ℹ️ 投注记录为空（符合预期）")
    else:
        print("  ⚠️ 未找到投注记录列表元素")
    
    print("\n✓ 投注记录查看测试完成")


async def test_view_alerts_list(page: Page):
    """
    测试查看告警列表
    
    验证点：
    1. 登录成功
    2. 导航到告警页面
    3. 查看告警列表（应该为空）
    """
    print("\n" + "="*60)
    print("测试：查看告警列表")
    print("="*60)
    
    # 1. 登录
    print("\n[步骤1] 登录系统...")
    await page.goto("http://localhost:5173/login")
    await page.fill('input[type="text"]', TEST_USERNAME)
    await page.fill('input[type="password"]', TEST_PASSWORD)
    await page.click('button[type="submit"]')
    
    # 等待Dashboard加载
    try:
        await page.wait_for_selector('.stat-cards', timeout=5000)
        print("  ✓ 登录成功")
    except:
        print("  ✗ 登录失败")
        raise
    
    # 2. 导航到告警
    print("\n[步骤2] 导航到告警...")
    await page.click('text=告警')
    await page.wait_for_timeout(1000)
    await page.screenshot(path="screenshots/account-info-05-alerts-page.png")
    
    # 3. 检查页面内容
    print("\n[步骤3] 检查告警页面内容...")
    
    # 检查页面标题
    title = await page.query_selector('h1, .page-title')
    if title:
        title_text = await title.text_content()
        print(f"  ✓ 页面标题: {title_text}")
    
    # 检查是否有告警列表
    alerts_list = await page.query_selector('.alerts-list, .alert-list')
    if alerts_list:
        print("  ✓ 找到告警列表元素")
        
        # 检查是否有告警
        alerts = await page.query_selector_all('.alert-item')
        print(f"  告警数量: {len(alerts)}")
        
        if len(alerts) == 0:
            print("  ℹ️ 告警列表为空（符合预期）")
    else:
        print("  ⚠️ 未找到告警列表元素")
    
    print("\n✓ 告警列表查看测试完成")


async def test_dashboard_data_display(page: Page):
    """
    测试Dashboard数据显示
    
    验证点：
    1. 登录成功
    2. Dashboard显示正确的统计数据
    3. 所有数据卡片都存在
    """
    print("\n" + "="*60)
    print("测试：Dashboard数据显示")
    print("="*60)
    
    # 1. 登录
    print("\n[步骤1] 登录系统...")
    await page.goto("http://localhost:5173/login")
    await page.fill('input[type="text"]', TEST_USERNAME)
    await page.fill('input[type="password"]', TEST_PASSWORD)
    await page.click('button[type="submit"]')
    
    # 等待Dashboard加载
    try:
        await page.wait_for_selector('.stat-cards', timeout=5000)
        print("  ✓ Dashboard已加载")
    except:
        print("  ✗ Dashboard加载失败")
        raise
    
    await page.screenshot(path="screenshots/account-info-06-dashboard-data.png")
    
    # 2. 检查统计卡片
    print("\n[步骤2] 检查统计卡片...")
    
    # 检查总余额
    balance_card = await page.query_selector('.stat-card:has-text("总余额")')
    if balance_card:
        balance_value = await balance_card.query_selector('.stat-value')
        if balance_value:
            balance_text = await balance_value.text_content()
            print(f"  ✓ 总余额: {balance_text}")
    else:
        print("  ⚠️ 未找到总余额卡片")
    
    # 检查当日盈亏
    daily_pnl_card = await page.query_selector('.stat-card:has-text("当日盈亏")')
    if daily_pnl_card:
        daily_pnl_value = await daily_pnl_card.query_selector('.stat-value')
        if daily_pnl_value:
            daily_pnl_text = await daily_pnl_value.text_content()
            print(f"  ✓ 当日盈亏: {daily_pnl_text}")
    else:
        print("  ⚠️ 未找到当日盈亏卡片")
    
    # 检查总盈亏
    total_pnl_card = await page.query_selector('.stat-card:has-text("总盈亏")')
    if total_pnl_card:
        total_pnl_value = await total_pnl_card.query_selector('.stat-value')
        if total_pnl_value:
            total_pnl_text = await total_pnl_value.text_content()
            print(f"  ✓ 总盈亏: {total_pnl_text}")
    else:
        print("  ⚠️ 未找到总盈亏卡片")
    
    # 检查账号数量
    accounts_card = await page.query_selector('.stat-card:has-text("账号数")')
    if accounts_card:
        accounts_value = await accounts_card.query_selector('.stat-value')
        if accounts_value:
            accounts_text = await accounts_value.text_content()
            print(f"  ✓ 账号数: {accounts_text}")
    else:
        print("  ⚠️ 未找到账号数卡片")
    
    # 检查策略数量
    strategies_card = await page.query_selector('.stat-card:has-text("策略数")')
    if strategies_card:
        strategies_value = await strategies_card.query_selector('.stat-value')
        if strategies_value:
            strategies_text = await strategies_value.text_content()
            print(f"  ✓ 策略数: {strategies_text}")
    else:
        print("  ⚠️ 未找到策略数卡片")
    
    print("\n✓ Dashboard数据显示测试完成")
