"""
账号绑定测试 - 修复版

修复要点:
1. 等待网络请求完成
2. 等待列表更新（使用更长的超时时间）
3. 更精确的选择器
"""
import pytest
from playwright.async_api import Page


pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

TEST_USERNAME = "e2e_browser_test"
TEST_PASSWORD = "test123456"


async def test_bind_account_with_network_wait(page: Page):
    """
    测试账号绑定 - 等待网络请求和列表更新
    """
    print("\n" + "="*80)
    print("账号绑定测试（修复版）")
    print("="*80)
    
    # ========== 步骤1: 登录 ==========
    print("\n[步骤1] 登录系统...")
    await page.goto("http://localhost:5173/login")
    await page.fill('input[type="text"]', TEST_USERNAME)
    await page.fill('input[type="password"]', TEST_PASSWORD)
    await page.click('button[type="submit"]')
    
    await page.wait_for_selector('.stat-cards', timeout=5000)
    print("  ✓ 登录成功")
    
    # ========== 步骤2: 导航到账号管理 ==========
    print("\n[步骤2] 导航到账号管理...")
    await page.click('text=账号')
    await page.wait_for_timeout(1000)
    print("  ✓ 已进入账号管理页面")
    
    # ========== 步骤3: 检查初始账号列表 ==========
    print("\n[步骤3] 检查初始账号列表...")
    initial_accounts = await page.query_selector_all('.account-card')
    print(f"  初始账号数量: {len(initial_accounts)}")
    await page.screenshot(path="screenshots/bind-fixed-01-initial-list.png")
    
    # ========== 步骤4: 点击绑定按钮 ==========
    print("\n[步骤4] 点击绑定按钮...")
    bind_button = await page.query_selector('button.bind-toggle-btn')
    if not bind_button:
        print("  ✗ 未找到绑定按钮")
        raise Exception("未找到绑定按钮")
    
    await bind_button.click()
    await page.wait_for_timeout(500)
    
    # 等待表单出现
    await page.wait_for_selector('.bind-form', timeout=3000)
    print("  ✓ 绑定表单已打开")
    await page.screenshot(path="screenshots/bind-fixed-02-form-opened.png")
    
    # ========== 步骤5: 填写表单 ==========
    print("\n[步骤5] 填写表单...")
    
    # 选择平台
    await page.select_option('#bind-platform', 'JND28WEB')
    print("  ✓ 已选择平台: JND28WEB")
    
    # 填写账号名
    await page.fill('#bind-name', 'testuser01')
    print("  ✓ 已填写账号名: testuser01")
    
    # 填写密码
    await page.fill('#bind-password', 'test166')
    print("  ✓ 已填写密码: test166")
    
    await page.screenshot(path="screenshots/bind-fixed-03-form-filled.png")
    
    # ========== 步骤6: 提交并等待网络请求 ==========
    print("\n[步骤6] 提交表单并等待网络请求...")
    
    # 监听网络请求
    async with page.expect_response(
        lambda response: "/api/v1/accounts" in response.url and response.request.method == "POST"
    ) as response_info:
        # 点击提交按钮
        submit_button = await page.query_selector('button.bind-submit-btn[type="submit"]')
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
        print("  ✓ 账号创建成功")
    elif status == 409:
        print("  ⚠️ 账号已存在（409 Conflict）")
        body = await response.text()
        print(f"  响应内容: {body}")
        raise Exception("账号已存在，请先清理数据库")
    else:
        print(f"  ✗ 创建失败，状态码: {status}")
        body = await response.text()
        print(f"  响应内容: {body}")
        raise Exception(f"账号创建失败: {status}")
    
    await page.screenshot(path="screenshots/bind-fixed-04-after-submit.png")
    
    # ========== 步骤7: 等待列表刷新 ==========
    print("\n[步骤7] 等待列表刷新...")
    
    # 等待表单关闭
    try:
        await page.wait_for_selector('.bind-form', state='hidden', timeout=3000)
        print("  ✓ 表单已关闭")
    except:
        print("  ⚠️ 表单未关闭（可能仍在显示）")
    
    # 等待列表更新 - 使用更长的超时时间
    print("  等待账号卡片出现...")
    try:
        await page.wait_for_selector('.account-card', timeout=10000)
        print("  ✓ 账号卡片已出现")
    except:
        print("  ✗ 超时：账号卡片未出现")
        await page.screenshot(path="screenshots/bind-fixed-05-timeout.png")
        raise Exception("账号列表未更新")
    
    # 再等待一下确保渲染完成
    await page.wait_for_timeout(1000)
    
    # ========== 步骤8: 验证账号已添加 ==========
    print("\n[步骤8] 验证账号已添加...")
    final_accounts = await page.query_selector_all('.account-card')
    print(f"  最终账号数量: {len(final_accounts)}")
    
    await page.screenshot(path="screenshots/bind-fixed-06-final-list.png")
    
    if len(final_accounts) > len(initial_accounts):
        print("  ✓ 账号已成功添加到列表")
        
        # 检查账号详情
        last_card = final_accounts[-1]
        account_name = await last_card.query_selector('.account-name')
        if account_name:
            name_text = await account_name.text_content()
            print(f"  账号名称: {name_text}")
            
            if 'testuser01' in name_text:
                print("  ✓ 账号名称匹配")
            else:
                print(f"  ⚠️ 账号名称不匹配，期望: testuser01, 实际: {name_text}")
    else:
        print("  ✗ 账号列表未更新")
        print(f"  初始: {len(initial_accounts)}, 最终: {len(final_accounts)}")
        raise Exception("账号列表未更新")
    
    print("\n" + "="*80)
    print("✓ 账号绑定测试完成")
    print("="*80)
