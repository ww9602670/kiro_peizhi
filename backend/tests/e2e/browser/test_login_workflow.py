"""Playwright smoke tests for the operator login shell."""

import pytest
from playwright.async_api import Page

from .pages.dashboard_page import DashboardPage
from .pages.login_page import LoginPage


pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

TEST_USERNAME = "testoperator"
TEST_PASSWORD = "test123"


class TestBrowserLoginWorkflow:
    async def test_successful_login(self, page: Page, frontend_url: str):
        login_page = LoginPage(page, frontend_url)
        dashboard_page = DashboardPage(page, frontend_url)

        await login_page.goto()
        await login_page.login(TEST_USERNAME, TEST_PASSWORD)
        await login_page.wait_for_redirect("/dashboard")
        await dashboard_page.wait_for_loaded()

        assert page.url.endswith("/dashboard"), f"Expected /dashboard, got {page.url}"

    async def test_failed_login(self, page: Page, frontend_url: str):
        login_page = LoginPage(page, frontend_url)

        await login_page.goto()
        await login_page.login("wrong_user", "wrong_password")

        error_message = await login_page.get_error_message()
        assert page.url.endswith("/login"), f"Should stay on /login, got {page.url}"
        assert error_message, "Expected login error message to be visible"

    async def test_navigation_after_login(self, page: Page, frontend_url: str):
        login_page = LoginPage(page, frontend_url)
        dashboard_page = DashboardPage(page, frontend_url)

        await login_page.goto()
        await login_page.login(TEST_USERNAME, TEST_PASSWORD)
        await login_page.wait_for_redirect("/dashboard")

        await dashboard_page.navigate_to_accounts()
        assert page.url.endswith("/accounts"), f"Expected /accounts, got {page.url}"

        await dashboard_page.navigate_to_strategies()
        assert page.url.endswith("/strategies"), f"Expected /strategies, got {page.url}"

        await dashboard_page.navigate_to_me()
        assert page.url.endswith("/me"), f"Expected /me, got {page.url}"

        await dashboard_page.navigate_to_dashboard()
        assert page.url.endswith("/dashboard"), f"Expected /dashboard, got {page.url}"
