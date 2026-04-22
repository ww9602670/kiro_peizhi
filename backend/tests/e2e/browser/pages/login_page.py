"""Browser page object for the login screen."""

from playwright.async_api import Page


class LoginPage:
    """Encapsulates the operator login workflow."""

    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url
        self.username_input = 'input[type="text"]'
        self.password_input = 'input[type="password"]'
        self.submit_button = 'button[type="submit"]'
        self.error_message = '[role="alert"]'

    async def goto(self):
        await self.page.goto(f"{self.base_url}/login")
        await self.page.wait_for_load_state("networkidle")

    async def login(self, username: str, password: str):
        await self.page.fill(self.username_input, username)
        await self.page.fill(self.password_input, password)
        await self.page.click(self.submit_button)

    async def wait_for_redirect(self, expected_url: str = "/dashboard"):
        await self.page.wait_for_url(f"**{expected_url}", timeout=10000)

    async def get_error_message(self):
        try:
            return await self.page.text_content(self.error_message, timeout=3000)
        except Exception:
            return None

    async def is_logged_in(self):
        return self.page.url.endswith("/dashboard") or self.page.url.endswith("/accounts") or self.page.url.endswith("/strategies") or self.page.url.endswith("/me")
