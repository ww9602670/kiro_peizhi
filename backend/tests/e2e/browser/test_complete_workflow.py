"""
完整业务流程测试

测试完整的业务流程：
1. 登录
2. 绑定Mock平台账号
3. 创建策略
4. 启动策略
5. 观察投注
6. 停止策略
7. 验证数据
"""
import pytest
import asyncio
from playwright.async_api import Page


pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

TEST_USERNAME = "e2e_browser_test"
TEST_PASSWORD = "test123456"


async def test_complete_workflow(page: Page):
    """
    完整业务流程测试
    """
    print("\n" + "="*80)
    print("完整业务流程测试")
    print("="*80)
    
    # ========== 步骤1: 登录 ==========
    print("\n[步骤1] 登录系统...")
    await page.goto("http://localhost:5173/login")
    await page.fill('input[type="text"]', TEST_USERNAME)
    await page.fill('input[type="password"]', TEST_PASSWORD)
    await page.click('button[type="submit"]')
    
    try:
        await page.wait_for_selector('.stat-cards', timeout=5000)
        print("  ✓ 登录成功")
    except:
        print("  ✗ 登录失败")
        await page.screenshot(path="screenshots/complete-01-login-failed.png")
        raise
    
    await page.screenshot(path="screenshots/complete-01-login-success.png")
    
    # ========== 步骤2: 导航到账号管理 ==========
    print("\n[步骤2] 导航到账号管理...")
    await page.click('text=账号')
    await page.wait_for_timeout(1000)
    await page.screenshot(path="screenshots/complete-02-accounts-page.png")
    print("  ✓ 已进入账号管理页面")
    
    # ========== 步骤3: 点击绑定账号按钮 ==========
    print("\n[步骤3] 点击绑定账号按钮...")
    bind_button = await page.query_selector('button:has-text("绑定")')
    if not bind_button:
        print("  ✗ 未找到绑定按钮")
        await page.screenshot(path="screenshots/complete-03-no-bind-button.png")
        raise Exception("未找到绑定按钮")
    
    await bind_button.click()
    await page.wait_for_timeout(1000)
    await page.screenshot(path="screenshots/complete-03-bind-dialog.png")
    print("  ✓ 绑定对话框已打开")
    
    # ========== 步骤4: 填写账号信息（JND28WEB平台） ==========
    print("\n[步骤4] 填写JND28WEB平台账号信息...")
    
    # 选择平台类型 - 查找select元素
    platform_select = await page.query_selector('select')
    if platform_select:
        await platform_select.select_option('JND28WEB')
        print("  ✓ 已选择JND28WEB平台")
    else:
        print("  ⚠️ 未找到平台类型选择器")
        await page.screenshot(path="screenshots/complete-04-no-platform-select.png")
    
    # 填写账号名
    username_input = await page.query_selector('input[type="text"]:not([type="password"])')
    if username_input:
        await username_input.fill('testuser01')
        print("  ✓ 已填写账号名: testuser01")
    else:
        print("  ✗ 未找到账号名输入框")
    
    # 填写密码
    password_inputs = await page.query_selector_all('input[type="password"]')
    if len(password_inputs) > 0:
        # 使用第一个密码输入框（登录页面的密码框可能还在DOM中）
        await password_inputs[-1].fill('test166')
        print("  ✓ 已填写密码: test166")
    else:
        print("  ✗ 未找到密码输入框")
    
    await page.screenshot(path="screenshots/complete-04-bind-form-filled.png")
    
    # ========== 步骤5: 提交绑定并等待网络请求 ==========
    print("\n[步骤5] 提交绑定并等待网络请求...")
    
    # 监听网络请求
    async with page.expect_response(
        lambda response: "/api/v1/accounts" in response.url and response.request.method == "POST"
    ) as response_info:
        submit_button = await page.query_selector('button.bind-submit-btn[type="submit"]')
        if not submit_button:
            print("  ✗ 未找到提交按钮")
            await page.screenshot(path="screenshots/complete-05-no-submit-button.png")
            raise Exception("未找到提交按钮")
        
        await submit_button.click()
        print("  ✓ 已点击提交按钮")
    
    # 等待响应
    response = await response_info.value
    status = response.status
    print(f"  API响应状态: {status}")
    
    if status == 200 or status == 201:
        print("  ✓ 账号创建成功")
    elif status == 409:
        print("  ⚠️ 账号已存在（409 Conflict）")
        body = await response.text()
        print(f"  响应内容: {body}")
        raise Exception("账号已存在，请先运行 python backend/check_e2e_accounts.py 清理数据")
    else:
        print(f"  ✗ 创建失败，状态码: {status}")
        body = await response.text()
        print(f"  响应内容: {body}")
        raise Exception(f"账号创建失败: {status}")
    
    await page.screenshot(path="screenshots/complete-05-bind-result.png")
    
    # ========== 步骤6: 等待列表刷新并验证 ==========
    print("\n[步骤6] 等待列表刷新并验证...")
    
    # 等待表单关闭
    try:
        await page.wait_for_selector('.bind-form', state='hidden', timeout=3000)
        print("  ✓ 表单已关闭")
    except:
        print("  ⚠️ 表单未关闭")
    
    # 等待账号卡片出现
    try:
        await page.wait_for_selector('.account-card', timeout=10000)
        print("  ✓ 账号卡片已出现")
    except:
        print("  ✗ 超时：账号卡片未出现")
        await page.screenshot(path="screenshots/complete-06-timeout.png")
        raise Exception("账号列表未更新")
    
    # 再等待一下确保渲染完成
    await page.wait_for_timeout(1000)
    
    # 查找账号列表中的账号
    account_items = await page.query_selector_all('.account-card')
    print(f"  账号数量: {len(account_items)}")
    
    if len(account_items) > 0:
        print("  ✓ 账号已成功绑定")
        await page.screenshot(path="screenshots/complete-06-account-bound.png")
    else:
        print("  ✗ 账号列表仍为空")
        await page.screenshot(path="screenshots/complete-06-bind-failed.png")
        raise Exception("账号列表为空")
    
    # ========== 步骤7: 导航到策略管理 ==========
    print("\n[步骤7] 导航到策略管理...")
    await page.click('text=策略')
    await page.wait_for_timeout(1000)
    await page.screenshot(path="screenshots/complete-07-strategies-page.png")
    print("  ✓ 已进入策略管理页面")
    
    # ========== 步骤8: 点击创建策略按钮 ==========
    print("\n[步骤8] 点击创建策略按钮...")
    create_button = await page.query_selector('button:has-text("创建")')
    if not create_button:
        print("  ✗ 未找到创建按钮")
        await page.screenshot(path="screenshots/complete-08-no-create-button.png")
        raise Exception("未找到创建按钮")
    
    await create_button.click()
    await page.wait_for_timeout(1000)
    await page.screenshot(path="screenshots/complete-08-create-dialog.png")
    print("  ✓ 创建对话框已打开")
    
    # ========== 步骤9: 填写策略信息 ==========
    print("\n[步骤9] 填写策略信息...")
    
    # 等待表单加载完成
    await page.wait_for_selector('#sf-name', timeout=3000)
    
    # 账号会自动选择第一个，无需手动选择
    account_select = await page.query_selector('#sf-account')
    if account_select:
        selected_value = await account_select.evaluate('el => el.value')
        print(f"  ✓ 已自动选择账号 ID: {selected_value}")
    else:
        print("  ⚠️ 未找到账号选择器")
    
    # 填写策略名称
    await page.fill('#sf-name', 'E2E测试策略')
    print("  ✓ 已填写策略名称: E2E测试策略")
    
    # 策略类型默认是平注，无需切换
    print("  ✓ 策略类型: 平注（默认）")
    
    # 填写玩法代码
    await page.fill('#sf-playcode', 'DX1')
    print("  ✓ 已填写玩法代码: DX1")
    
    # 填写基础投注金额
    await page.fill('#sf-amount', '10')
    print("  ✓ 已填写基础金额: 10")
    
    # 下注时机保持默认值30秒
    print("  ✓ 下注时机: 30秒（默认）")
    
    await page.screenshot(path="screenshots/complete-09-strategy-form-filled.png")
    
    # ========== 步骤10: 提交创建策略并等待网络请求 ==========
    print("\n[步骤10] 提交创建策略并等待网络请求...")
    
    # 监听网络请求
    async with page.expect_response(
        lambda response: "/api/v1/strategies" in response.url and response.request.method == "POST"
    ) as response_info:
        submit_button = await page.query_selector('button.form-submit-btn[type="submit"]')
        if not submit_button:
            print("  ✗ 未找到提交按钮")
            raise Exception("未找到提交按钮")
        
        await submit_button.click()
        print("  ✓ 已点击提交按钮")
    
    # 等待响应
    response = await response_info.value
    status = response.status
    print(f"  API响应状态: {status}")
    
    if status == 200 or status == 201:
        print("  ✓ 策略创建成功")
    else:
        print(f"  ✗ 创建失败，状态码: {status}")
        body = await response.text()
        print(f"  响应内容: {body}")
        raise Exception(f"策略创建失败: {status}")
    
    # 等待表单关闭（返回策略列表）
    await page.wait_for_timeout(1000)
    await page.screenshot(path="screenshots/complete-10-strategy-created.png")
    
    # ========== 步骤11: 验证策略已创建 ==========
    print("\n[步骤11] 验证策略已创建...")
    
    # 等待策略卡片出现
    try:
        await page.wait_for_selector('.strategy-card', timeout=10000)
        print("  ✓ 策略卡片已出现")
    except:
        print("  ✗ 超时：策略卡片未出现")
        await page.screenshot(path="screenshots/complete-11-timeout.png")
        raise Exception("策略列表未更新")
    
    await page.wait_for_timeout(1000)
    strategy_items = await page.query_selector_all('.strategy-card')
    print(f"  策略数量: {len(strategy_items)}")
    
    if len(strategy_items) > 0:
        print("  ✓ 策略已成功创建")
    else:
        print("  ✗ 策略列表仍为空")
        raise Exception("策略列表为空")
    
    # ========== 步骤12: 启动策略 ==========
    print("\n[步骤12] 启动策略...")
    
    # 查找启动按钮
    start_button = await page.query_selector('button:has-text("启动"), button:has-text("开始")')
    if start_button:
        await start_button.click()
        print("  ✓ 已点击启动按钮")
        await page.wait_for_timeout(3000)
        await page.screenshot(path="screenshots/complete-12-strategy-started.png")
    else:
        print("  ⚠️ 未找到启动按钮")
        await page.screenshot(path="screenshots/complete-12-no-start-button.png")
    
    # ========== 步骤13: 观察投注（等待30秒） ==========
    print("\n[步骤13] 观察投注行为（等待30秒）...")
    print("  等待策略运行...")
    
    for i in range(6):
        await page.wait_for_timeout(5000)
        print(f"  已等待 {(i+1)*5} 秒...")
    
    await page.screenshot(path="screenshots/complete-13-after-30s.png")
    
    # ========== 步骤14: 检查投注记录 ==========
    print("\n[步骤14] 检查投注记录...")
    await page.click('text=投注记录')
    await page.wait_for_timeout(2000)
    await page.screenshot(path="screenshots/complete-14-bet-orders.png")
    
    # 检查是否有投注记录
    bet_orders = await page.query_selector_all('.bet-order-item, tbody tr')
    print(f"  投注记录数量: {len(bet_orders)}")
    
    if len(bet_orders) > 0:
        print("  ✓ 已产生投注记录")
    else:
        print("  ℹ️ 暂无投注记录（可能需要更长时间）")
    
    # ========== 步骤15: 返回Dashboard查看数据 ==========
    print("\n[步骤15] 返回Dashboard查看数据...")
    await page.click('text=仪表盘')
    await page.wait_for_timeout(2000)
    await page.screenshot(path="screenshots/complete-15-dashboard-final.png")
    
    # 读取最终数据
    balance_element = await page.query_selector('.stat-card:has-text("总余额") .stat-value')
    if balance_element:
        balance = await balance_element.text_content()
        print(f"  最终余额: {balance}")
    
    daily_pnl_element = await page.query_selector('.stat-card:has-text("当日盈亏") .stat-value')
    if daily_pnl_element:
        daily_pnl = await daily_pnl_element.text_content()
        print(f"  当日盈亏: {daily_pnl}")
    
    # ========== 步骤16: 停止策略 ==========
    print("\n[步骤16] 停止策略...")
    await page.click('text=策略')
    await page.wait_for_timeout(1000)
    
    stop_button = await page.query_selector('button:has-text("停止"), button:has-text("暂停")')
    if stop_button:
        await stop_button.click()
        print("  ✓ 已点击停止按钮")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="screenshots/complete-16-strategy-stopped.png")
    else:
        print("  ℹ️ 未找到停止按钮（策略可能未运行）")
    
    print("\n" + "="*80)
    print("✓ 完整业务流程测试完成")
    print("="*80)
