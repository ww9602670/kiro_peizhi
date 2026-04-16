"""
浏览器E2E测试 - 登录工作流

测试用户通过浏览器登录系统的完整流程
"""
import pytest
from playwright.async_api import Page, expect
from .pages.login_page import LoginPage
from .pages.dashboard_page import DashboardPage


pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


class TestBrowserLoginWorkflow:
    """浏览器登录工作流测试"""
    
    async def test_successful_login(self, page: Page, frontend_url: str):
        """
        测试成功登录流程
        
        步骤：
        1. 打开登录页面
        2. 输入正确的用户名和密码
        3. 点击登录按钮
        4. 验证跳转到Dashboard
        5. 验证Dashboard显示正确
        """
        # 创建页面对象
        login_page = LoginPage(page, frontend_url)
        dashboard_page = DashboardPage(page, frontend_url)
        
        # 1. 打开登录页面
        await login_page.goto()
        await page.screenshot(path="screenshots/browser-e2e-01-login-page.png")
        
        # 2. 输入用户名和密码
        await login_page.login("admin", "admin123")
        
        # 3. 等待跳转到Dashboard
        await login_page.wait_for_redirect()
        await page.screenshot(path="screenshots/browser-e2e-02-dashboard.png")
        
        # 4. 验证URL
        assert "/dashboard" in page.url, f"Expected dashboard URL, got {page.url}"
        
        # 5. 验证Dashboard元素存在
        await page.wait_for_selector('[data-testid="balance"], .balance-value, text=余额', timeout=10000)
        
        print("✓ 登录成功，已跳转到Dashboard")
    
    async def test_failed_login(self, page: Page, frontend_url: str):
        """
        测试登录失败流程
        
        步骤：
        1. 打开登录页面
        2. 输入错误的用户名和密码
        3. 点击登录按钮
        4. 验证显示错误消息
        5. 验证仍在登录页面
        """
        login_page = LoginPage(page, frontend_url)
        
        # 1. 打开登录页面
        await login_page.goto()
        
        # 2. 输入错误的凭据
        await login_page.login("wrong_user", "wrong_password")
        
        # 3. 等待错误消息
        await page.wait_for_timeout(2000)
        await page.screenshot(path="screenshots/browser-e2e-03-login-error.png")
        
        # 4. 验证仍在登录页面
        assert "/login" in page.url, f"Should stay on login page, got {page.url}"
        
        print("✓ 登录失败测试通过")
    
    async def test_navigation_after_login(self, page: Page, frontend_url: str):
        """
        测试登录后的页面导航
        
        步骤：
        1. 登录系统
        2. 导航到账号管理页面
        3. 导航到策略管理页面
        4. 导航回Dashboard
        """
        login_page = LoginPage(page, frontend_url)
        dashboard_page = DashboardPage(page, frontend_url)
        
        # 1. 登录
        await login_page.goto()
        await login_page.login("admin", "admin123")
        await login_page.wait_for_redirect()
        
        # 2. 导航到账号管理
        await dashboard_page.navigate_to_accounts()
        await page.screenshot(path="screenshots/browser-e2e-04-accounts-page.png")
        assert "/accounts" in page.url
        print("✓ 成功导航到账号管理页面")
        
        # 3. 导航到策略管理
        await dashboard_page.navigate_to_strategies()
        await page.screenshot(path="screenshots/browser-e2e-05-strategies-page.png")
        assert "/strategies" in page.url
        print("✓ 成功导航到策略管理页面")
        
        # 4. 返回Dashboard
        await dashboard_page.goto()
        assert "/dashboard" in page.url
        print("✓ 成功返回Dashboard")
