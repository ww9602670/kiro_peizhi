"""
Playwright fixtures for browser E2E tests
"""
import pytest
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

# 标记所有 browser E2E 测试，默认 pytest 不收集（需 -m e2e 显式运行）
pytestmark = pytest.mark.e2e


@pytest.fixture(scope="function")
async def browser():
    """创建浏览器实例（每个测试独立）"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,  # 设置为True可以无头模式运行
            slow_mo=500,     # 每个操作延迟500ms，便于观察
        )
        yield browser
        await browser.close()


@pytest.fixture
async def context(browser: Browser):
    """创建浏览器上下文（每个测试独立）"""
    context = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        locale="zh-CN",
    )
    yield context
    await context.close()


@pytest.fixture
async def page(context: BrowserContext):
    """创建页面实例（每个测试独立）"""
    page = await context.new_page()
    yield page
    await page.close()


@pytest.fixture
def frontend_url():
    """前端服务URL"""
    return "http://localhost:5173"


@pytest.fixture
def backend_url():
    """后端服务URL"""
    return "http://localhost:8888"
