"""
登录页面对象
"""
from playwright.async_api import Page, expect


class LoginPage:
    """登录页面"""
    
    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url
        
        # 页面元素选择器
        self.username_input = 'input[type="text"]'
        self.password_input = 'input[type="password"]'
        self.submit_button = 'button[type="submit"]'
        self.error_message = '.error-message, .ant-message-error'
    
    async def goto(self):
        """导航到登录页面"""
        await self.page.goto(f"{self.base_url}/login")
        await self.page.wait_for_load_state("networkidle")
    
    async def login(self, username: str, password: str):
        """执行登录操作"""
        await self.page.fill(self.username_input, username)
        await self.page.fill(self.password_input, password)
        
        # 点击提交按钮
        await self.page.click(self.submit_button)
        
        # 等待登录完成 - 等待Dashboard元素出现或URL变化
        try:
            # 方式1: 等待Dashboard特征元素出现
            await self.page.wait_for_selector('.stat-cards, .layout-container', timeout=5000)
        except:
            # 方式2: 等待一段时间让React重新渲染
            await self.page.wait_for_timeout(2000)
    
    async def wait_for_redirect(self, expected_url: str = "/dashboard"):
        """等待登录成功后的重定向"""
        await self.page.wait_for_url(f"**{expected_url}", timeout=10000)
    
    async def get_error_message(self):
        """获取错误消息"""
        try:
            return await self.page.text_content(self.error_message, timeout=3000)
        except:
            return None
    
    async def is_logged_in(self):
        """检查是否已登录（通过URL判断）"""
        return "/dashboard" in self.page.url or "/accounts" in self.page.url
