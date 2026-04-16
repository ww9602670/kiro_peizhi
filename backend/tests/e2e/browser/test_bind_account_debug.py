"""
账号绑定调试测试

专门用于调试账号绑定失败的问题
"""
import pytest
from playwright.async_api import Page


pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

TEST_USERNAME = "e2e_browser_test"
TEST_PASSWORD = "test123456"


async def test_bind_account_with_debug(page: Page):
    """
    账号绑定调试测试 - 监听所有错误和网络请求
    """
    print("\n" + "="*80)
    print("账号绑定调试测试")
    print("="*80)
    
    # 监听控制台消息
    console_messages = []
    def handle_console(msg):
        console_messages.append({
            'type': msg.type,
            'text': msg.text
        })
        print(f"  [Console {msg.type}] {msg.text}")
    
    page.on('console', handle_console)
    
    # 监听网络请求
    requests = []
    responses = []
    
    def handle_request(request):
        if '/api/' in request.url:
            requests.append({
                'method': request.method,
                'url': request.url,
                'post_data': request.post_data
            })
            print(f"  [Request] {request.method} {request.url}")
    
    def handle_response(response):
        if '/api/' in response.url:
            responses.append({
                'status': response.status,
                'url': response.url
            })
            print(f"  [Response] {response.status} {response.url}")
    
    page.on('request', handle_request)
    page.on('response', handle_response)
    
    # 步骤1: 登录
    print("\n[步骤1] 登录系统...")
    await page.goto("http://localhost:5173/login")
    await page.fill('input[type="text"]', TEST_USERNAME)
    await page.fill('input[type="password"]', TEST_PASSWORD)
    await page.click('button[type="submit"]')
    
    await page.wait_for_selector('.stat-cards', timeout=5000)
    print("  ✓ 登录成功")
    
    # 步骤2: 导航到账号管理
    print("\n[步骤2] 导航到账号管理...")
    await page.click('text=账号')
    await page.wait_for_timeout(1000)
    print("  ✓ 已进入账号管理页面")
    
    # 步骤3: 点击绑定按钮
    print("\n[步骤3] 点击绑定按钮...")
    bind_button = await page.query_selector('button:has-text("绑定")')
    if not bind_button:
        print("  ✗ 未找到绑定按钮")
        raise Exception("未找到绑定按钮")
    
    await bind_button.click()
    await page.wait_for_timeout(1000)
    await page.screenshot(path="screenshots/debug-bind-01-dialog.png")
    print("  ✓ 绑定对话框已打开")
    
    # 步骤4: 填写表单
    print("\n[步骤4] 填写表单...")
    
    # 选择平台
    platform_select = await page.query_selector('select')
    if platform_select:
        await platform_select.select_option('JND28WEB')
        print("  ✓ 已选择JND28WEB平台")
    
    # 填写账号名 - 查找所有text类型的input
    text_inputs = await page.query_selector_all('input[type="text"]')
    print(f"  找到 {len(text_inputs)} 个text输入框")
    
    # 最后一个应该是账号名输入框
    if len(text_inputs) > 0:
        await text_inputs[-1].fill('testuser01')
        print("  ✓ 已填写账号名")
    
    # 填写密码
    password_inputs = await page.query_selector_all('input[type="password"]')
    print(f"  找到 {len(password_inputs)} 个password输入框")
    
    if len(password_inputs) > 0:
        await password_inputs[-1].fill('test166')
        print("  ✓ 已填写密码")
    
    await page.screenshot(path="screenshots/debug-bind-02-form-filled.png")
    
    # 步骤5: 提交表单
    print("\n[步骤5] 提交表单...")
    print("  清空之前的请求记录...")
    requests.clear()
    responses.clear()
    
    submit_button = await page.query_selector('button:has-text("确定"), button:has-text("提交"), button[type="submit"]')
    if submit_button:
        await submit_button.click()
        print("  ✓ 已点击提交按钮")
    else:
        print("  ✗ 未找到提交按钮")
        raise Exception("未找到提交按钮")
    
    # 等待请求完成
    print("\n[步骤6] 等待请求完成...")
    await page.wait_for_timeout(5000)
    
    # 分析请求和响应
    print("\n[步骤7] 分析网络请求...")
    print(f"  总请求数: {len(requests)}")
    print(f"  总响应数: {len(responses)}")
    
    # 查找账号相关的请求
    account_requests = [r for r in requests if 'account' in r['url'].lower()]
    account_responses = [r for r in responses if 'account' in r['url'].lower()]
    
    print(f"\n  账号相关请求: {len(account_requests)}")
    for req in account_requests:
        print(f"    {req['method']} {req['url']}")
        if req['post_data']:
            print(f"    Data: {req['post_data'][:200]}")
    
    print(f"\n  账号相关响应: {len(account_responses)}")
    for resp in account_responses:
        print(f"    {resp['status']} {resp['url']}")
    
    # 检查是否有错误响应
    error_responses = [r for r in responses if r['status'] >= 400]
    if error_responses:
        print(f"\n  ⚠️ 发现错误响应:")
        for resp in error_responses:
            print(f"    {resp['status']} {resp['url']}")
    
    # 检查控制台错误
    error_messages = [m for m in console_messages if m['type'] == 'error']
    if error_messages:
        print(f"\n  ⚠️ 发现控制台错误:")
        for msg in error_messages:
            print(f"    {msg['text']}")
    
    await page.screenshot(path="screenshots/debug-bind-03-after-submit.png")
    
    # 步骤8: 检查账号列表
    print("\n[步骤8] 检查账号列表...")
    await page.wait_for_timeout(2000)
    
    account_items = await page.query_selector_all('.account-item, tbody tr')
    print(f"  账号数量: {len(account_items)}")
    
    if len(account_items) > 0:
        print("  ✓ 账号绑定成功!")
    else:
        print("  ✗ 账号列表仍为空")
    
    await page.screenshot(path="screenshots/debug-bind-04-final.png")
    
    print("\n" + "="*80)
    print("调试测试完成")
    print("="*80)
