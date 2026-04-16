"""
调试登录问题
"""
import pytest
from playwright.async_api import Page


pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


async def test_debug_login(page: Page):
    """调试登录流程"""
    
    print("\n" + "="*60)
    print("调试登录流程")
    print("="*60)
    
    # 1. 打开登录页面
    print("\n[步骤1] 打开登录页面...")
    await page.goto("http://localhost:5173/login")
    await page.wait_for_load_state("networkidle")
    print(f"  当前URL: {page.url}")
    
    # 2. 填写表单
    print("\n[步骤2] 填写登录表单...")
    await page.fill('input[type="text"]', "e2e_browser_test")
    await page.fill('input[type="password"]', "test123456")
    print("  ✓ 表单已填写")
    
    # 3. 监听网络请求
    print("\n[步骤3] 监听网络请求...")
    requests = []
    
    def handle_request(request):
        if '/api/v1/auth/login' in request.url:
            print(f"  → 登录请求: {request.method} {request.url}")
            requests.append(request)
    
    def handle_response(response):
        if '/api/v1/auth/login' in response.url:
            print(f"  ← 登录响应: {response.status}")
    
    page.on('request', handle_request)
    page.on('response', handle_response)
    
    # 4. 点击登录按钮
    print("\n[步骤4] 点击登录按钮...")
    await page.click('button[type="submit"]')
    
    # 5. 等待一段时间
    print("\n[步骤5] 等待响应...")
    await page.wait_for_timeout(3000)
    
    # 6. 检查当前状态
    print("\n[步骤6] 检查当前状态...")
    print(f"  当前URL: {page.url}")
    print(f"  登录请求数: {len(requests)}")
    
    # 7. 截图
    await page.screenshot(path="screenshots/debug-login-after-submit.png")
    print("  ✓ 截图已保存: screenshots/debug-login-after-submit.png")
    
    # 8. 检查页面内容
    print("\n[步骤7] 检查页面内容...")
    
    # 检查是否有错误消息
    error_msg = await page.query_selector('.error-message, .ant-message-error')
    if error_msg:
        error_text = await error_msg.text_content()
        print(f"  ✗ 错误消息: {error_text}")
    else:
        print("  ✓ 无错误消息")
    
    # 检查是否在Dashboard
    if '/dashboard' in page.url:
        print("  ✓ 已跳转到Dashboard")
    else:
        print(f"  ✗ 未跳转到Dashboard，当前在: {page.url}")
    
    # 9. 获取页面HTML（部分）
    print("\n[步骤8] 获取页面HTML...")
    html = await page.content()
    print(f"  HTML长度: {len(html)} 字符")
    
    # 检查是否有token存储
    local_storage = await page.evaluate("() => JSON.stringify(localStorage)")
    print(f"\n[步骤9] LocalStorage内容:")
    print(f"  {local_storage}")
    
    print("\n" + "="*60)
    print("调试完成")
    print("="*60)
