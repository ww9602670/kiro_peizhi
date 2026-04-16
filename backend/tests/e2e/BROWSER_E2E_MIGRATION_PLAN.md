# 浏览器E2E测试框架改造方案

## 概述

将当前基于API的E2E测试改造为基于浏览器的真正端到端测试，通过Chrome自动化来模拟用户在前端的实际操作。

## 技术选型

### 方案对比

| 方案 | 优点 | 缺点 | 推荐度 |
|------|------|------|--------|
| **Playwright (Python)** | 官方支持Python，API简洁，跨浏览器，速度快 | 需要安装额外依赖 | ⭐⭐⭐⭐⭐ |
| **Selenium** | 成熟稳定，社区大 | API较老，速度慢 | ⭐⭐⭐ |
| **Chrome DevTools (MCP)** | 已集成在Kiro中 | 仅支持Chrome，API较底层 | ⭐⭐⭐⭐ |

### 推荐方案：Playwright

理由：
1. 现代化的API设计
2. 内置等待机制，减少flaky tests
3. 自动截图和视频录制
4. 优秀的调试体验
5. Python原生支持

## 改造步骤

### 第1步：安装依赖

```bash
# 安装Playwright
pip install playwright pytest-playwright

# 安装浏览器驱动
playwright install chromium
```

### 第2步：创建浏览器测试基础类

创建 `tests/e2e/browser/base.py`:
```python
from playwright.async_api import async_playwright, Page, Browser
import pytest

class BrowserE2EBase:
    """浏览器E2E测试基类"""
    
    def __init__(self, page: Page, base_url: str = "http://localhost:5173"):
        self.page = page
        self.base_url = base_url
    
    async def goto(self, path: str = "/"):
        """导航到指定页面"""
        await self.page.goto(f"{self.base_url}{path}")
    
    async def login(self, username: str, password: str):
        """执行登录操作"""
        await self.goto("/login")
        await self.page.fill('input[name="username"]', username)
        await self.page.fill('input[name="password"]', password)
        await self.page.click('button[type="submit"]')
        await self.page.wait_for_url("**/dashboard")
    
    async def take_screenshot(self, name: str):
        """截图"""
        await self.page.screenshot(path=f"screenshots/{name}.png")
```

### 第3步：创建页面对象模型 (Page Object Model)

创建 `tests/e2e/browser/pages/login_page.py`:
```python
class LoginPage:
    def __init__(self, page):
        self.page = page
        self.username_input = 'input[name="username"]'
        self.password_input = 'input[name="password"]'
        self.submit_button = 'button[type="submit"]'
        self.error_message = '.error-message'
    
    async def login(self, username: str, password: str):
        await self.page.fill(self.username_input, username)
        await self.page.fill(self.password_input, password)
        await self.page.click(self.submit_button)
    
    async def get_error_message(self):
        return await self.page.text_content(self.error_message)
```

创建 `tests/e2e/browser/pages/accounts_page.py`:
```python
class AccountsPage:
    def __init__(self, page):
        self.page = page
        self.bind_button = 'button:has-text("绑定账号")'
        self.account_name_input = 'input[name="account_name"]'
        self.password_input = 'input[name="password"]'
        self.platform_select = 'select[name="platform_type"]'
        self.submit_button = 'button:has-text("确认")'
        self.login_button = lambda account_id: f'button[data-account-id="{account_id}"]:has-text("登录")'
    
    async def bind_account(self, account_name: str, password: str, platform: str = "JND28WEB"):
        await self.page.click(self.bind_button)
        await self.page.fill(self.account_name_input, account_name)
        await self.page.fill(self.password_input, password)
        await self.page.select_option(self.platform_select, platform)
        await self.page.click(self.submit_button)
        await self.page.wait_for_selector('.success-message')
    
    async def login_account(self, account_id: int):
        await self.page.click(self.login_button(account_id))
        await self.page.wait_for_selector('.account-status:has-text("online")')
```

### 第4步：创建浏览器E2E测试用例

创建 `tests/e2e/browser/test_full_workflow.py`:
```python
import pytest
from playwright.async_api import async_playwright, expect

@pytest.fixture(scope="session")
async def browser():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # headless=False 可以看到浏览器操作
        yield browser
        await browser.close()

@pytest.fixture
async def page(browser):
    context = await browser.new_context()
    page = await context.new_page()
    yield page
    await context.close()

class TestBrowserE2EFullWorkflow:
    """浏览器E2E完整工作流测试"""
    
    async def test_full_workflow(self, page):
        """
        完整工作流测试：
        1. 打开登录页面
        2. 输入用户名密码，点击登录
        3. 验证跳转到Dashboard
        4. 导航到账号管理页面
        5. 绑定/使用现有账号
        6. 点击账号登录
        7. 导航到策略管理页面
        8. 创建新策略
        9. 启动策略
        10. 观察Dashboard数据变化
        11. 等待投注订单出现
        12. 验证结算结果
        """
        # 1. 登录
        await page.goto("http://localhost:5173/login")
        await page.fill('input[placeholder*="用户名"]', "admin")
        await page.fill('input[placeholder*="密码"]', "admin123")
        await page.click('button[type="submit"]')
        
        # 2. 验证跳转到Dashboard
        await page.wait_for_url("**/dashboard")
        await page.screenshot(path="screenshots/e2e-01-dashboard.png")
        
        # 3. 导航到账号管理
        await page.click('text=账号管理')
        await page.wait_for_selector('.accounts-page')
        await page.screenshot(path="screenshots/e2e-02-accounts.png")
        
        # 4. 绑定账号（如果需要）
        # ... 根据实际UI元素调整选择器
        
        # 5. 导航到策略管理
        await page.click('text=策略管理')
        await page.wait_for_selector('.strategies-page')
        await page.screenshot(path="screenshots/e2e-03-strategies.png")
        
        # 6. 创建策略
        await page.click('button:has-text("创建策略")')
        # ... 填写策略表单
        
        # 7. 启动策略
        # ... 点击启动按钮
        
        # 8. 验证Dashboard数据更新
        await page.click('text=Dashboard')
        await page.wait_for_timeout(5000)  # 等待数据刷新
        await page.screenshot(path="screenshots/e2e-04-dashboard-updated.png")
```

## 架构对比

### 当前架构（API测试）
```
pytest → httpx → Backend API → Database
```

### 目标架构（浏览器E2E测试）
```
pytest → Playwright/Chrome → Frontend UI → Backend API → Database
                ↓
          截图/视频录制
```

## 前置条件

运行浏览器E2E测试需要同时启动：
1. **后端服务**: `uvicorn app.main:app --host 0.0.0.0 --port 8888`
2. **前端服务**: `pnpm dev` (http://localhost:5173)
3. **测试执行**: `pytest tests/e2e/browser/ -v -s`

## 目录结构

```
tests/e2e/
├── browser/                    # 浏览器E2E测试（新增）
│   ├── __init__.py
│   ├── conftest.py             # Playwright fixtures
│   ├── base.py                 # 基础类
│   ├── pages/                  # Page Object Model
│   │   ├── __init__.py
│   │   ├── login_page.py
│   │   ├── dashboard_page.py
│   │   ├── accounts_page.py
│   │   ├── strategies_page.py
│   │   └── bet_orders_page.py
│   └── test_full_workflow.py   # 浏览器E2E测试用例
├── helpers/                    # API测试辅助（保留）
│   ├── context.py
│   └── utils.py
├── config.py
├── conftest.py
└── test_e2e_full_workflow.py   # API E2E测试（保留）
```

## 实施建议

1. **保留现有API测试**: 作为快速回归测试
2. **新增浏览器测试**: 覆盖用户交互场景
3. **分层测试策略**: API测试跑CI，浏览器测试跑手动/定期
