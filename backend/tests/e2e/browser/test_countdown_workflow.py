"""Playwright E2E tests for countdown visibility and polling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.async_api import Page

from .pages.dashboard_page import DashboardPage
from .pages.login_page import LoginPage


pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

TEST_USERNAME = "testoperator"
TEST_PASSWORD = "test123"
ARTIFACT_DIR = Path(__file__).resolve().parents[4] / ".codex-runtime"


def _current_install_payload(*, issue: str, state: int, close_sec: int, open_sec: int) -> dict:
    return {
        "code": 0,
        "message": "success",
        "data": {
            "installments": issue,
            "state": state,
            "close_countdown_sec": close_sec,
            "open_countdown_sec": open_sec,
            "pre_lottery_result": "1,2,3",
            "pre_installments": "20260418099",
            "template_code": "JND",
        },
    }


class TestBrowserCountdownWorkflow:
    async def test_countdown_visible_and_ticks_locally_across_operator_shell(
        self, page: Page, frontend_url: str
    ):
        login_page = LoginPage(page, frontend_url)
        dashboard_page = DashboardPage(page, frontend_url)
        captured_requests: list[str] = []
        mocked_payloads = [
            _current_install_payload(issue="20260419001", state=1, close_sec=25, open_sec=40),
        ]

        async def handle_current_install(route):
            request_index = min(len(captured_requests), len(mocked_payloads) - 1)
            captured_requests.append(route.request.url)
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(mocked_payloads[request_index]),
            )

        async def wait_for_request_count(expected: int, timeout_ms: int = 10000):
            deadline = timeout_ms / 1000
            elapsed = 0.0
            while len(captured_requests) < expected and elapsed < deadline:
                await page.wait_for_timeout(100)
                elapsed += 0.1
            assert len(captured_requests) >= expected, (
                f"Expected at least {expected} countdown requests, got {len(captured_requests)}"
            )

        async def read_countdown_value(index: int) -> int:
            text = await page.locator(".countdown-display .cd-value").nth(index).inner_text()
            digits = "".join(char for char in text if char.isdigit())
            return int(digits) if digits else -1

        await page.route("**/api/v1/lottery/current-install**", handle_current_install)

        await login_page.goto()
        await login_page.login(TEST_USERNAME, TEST_PASSWORD)
        await login_page.wait_for_redirect("/dashboard")
        await dashboard_page.wait_for_loaded()

        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

        await page.wait_for_selector(".countdown-display", timeout=10000)
        await wait_for_request_count(1)
        await page.wait_for_selector(".countdown-display .issue-current", timeout=10000)
        dashboard_state_class = await page.locator(".countdown-display .state-value").get_attribute("class")
        assert dashboard_state_class and "state-" in dashboard_state_class
        initial_issue = await page.locator(".countdown-display .issue-current").inner_text()
        initial_close_countdown = await read_countdown_value(0)
        assert initial_issue == "20260419001"
        assert initial_close_countdown >= 0
        await page.screenshot(path=str(ARTIFACT_DIR / "browser-countdown-dashboard.png"))

        await page.wait_for_function(
            """
            (initialValue) => {
              const node = document.querySelectorAll('.countdown-display .cd-value')[0];
              if (!node) return false;
              const match = node.textContent?.match(/\\d+/);
              if (!match) return false;
              return Number(match[0]) < initialValue;
            }
            """,
            arg=initial_close_countdown,
        )
        after_tick_close_countdown = await read_countdown_value(0)
        assert after_tick_close_countdown < initial_close_countdown

        await dashboard_page.navigate_to_strategies()
        await page.wait_for_selector(".countdown-display", timeout=10000)
        strategies_state_class = await page.locator(".countdown-display .state-value").get_attribute("class")
        strategies_issue = await page.locator(".countdown-display .issue-current").inner_text()
        strategies_close_countdown = await read_countdown_value(0)
        assert strategies_state_class and "state-" in strategies_state_class
        assert strategies_issue == initial_issue
        assert strategies_close_countdown >= 0
        await page.screenshot(path=str(ARTIFACT_DIR / "browser-countdown-strategies.png"))

        assert len(captured_requests) >= 1, "Expected at least one countdown request to be captured"
