"""Browser page object for the operator shell."""

from playwright.async_api import Page


class DashboardPage:
    """Helpers for navigating the operator application shell."""

    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url
        self.dashboard_root = ".dashboard-page"
        self.accounts_menu = ".layout-sidebar .sidebar-nav-item:nth-of-type(2)"
        self.strategies_menu = ".layout-sidebar .sidebar-nav-item:nth-of-type(3)"
        self.me_menu = ".layout-sidebar .sidebar-nav-item:nth-of-type(4)"
        self.dashboard_menu = ".layout-sidebar .sidebar-nav-item:nth-of-type(1)"

    async def goto(self):
        await self.page.goto(f"{self.base_url}/dashboard")
        await self.page.wait_for_load_state("networkidle")

    async def wait_for_loaded(self):
        await self.page.wait_for_selector(self.dashboard_root, timeout=10000)

    async def navigate_to_accounts(self):
        await self.page.click(self.accounts_menu)
        await self.page.wait_for_url("**/accounts")

    async def navigate_to_strategies(self):
        await self.page.click(self.strategies_menu)
        await self.page.wait_for_url("**/strategies")

    async def navigate_to_me(self):
        await self.page.click(self.me_menu)
        await self.page.wait_for_url("**/me")

    async def navigate_to_dashboard(self):
        await self.page.click(self.dashboard_menu)
        await self.page.wait_for_url("**/dashboard")
