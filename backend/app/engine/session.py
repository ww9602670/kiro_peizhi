"""Session lifecycle management for one account-platform runtime."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

import aiosqlite

from app.engine.adapters.base import LoginResult, PlatformAdapter
from app.engine.alert import AlertService
from app.models.db_ops import account_platform_session_upsert, account_update
from app.utils.captcha import CaptchaError, CaptchaService, get_shared_captcha_service

logger = logging.getLogger(__name__)

RETRY_DELAYS: list[int] = [30, 60, 120]
MAX_LOGIN_ATTEMPTS = 5
PAUSE_AFTER_FAILURES = 3
PAUSE_DURATION = 600
MAX_CAPTCHA_FAILURES = 5
HEARTBEAT_INTERVAL = 75
HEARTBEAT_MAX_FAILS = 3
_BJT = timezone(timedelta(hours=8))


class SessionManager:
    """Manage one account login session, heartbeat, and reconnect behavior."""

    def __init__(
        self,
        *,
        adapter: PlatformAdapter,
        alert_service: AlertService,
        operator_id: int,
        account_id: int,
        account_name: str,
        password: str,
        platform_type: str = "JND28WEB",
        db: aiosqlite.Connection,
        captcha_service: CaptchaService | None = None,
        on_status_change: Optional[Callable[[int, str], Awaitable[None]]] = None,
    ) -> None:
        self.adapter = adapter
        self.alert_service = alert_service
        self._captcha_service = captcha_service
        self.operator_id = operator_id
        self.account_id = account_id
        self.account_name = account_name
        self.password = password
        self.platform_type = platform_type
        self.db = db
        self._on_status_change = on_status_change

        self.session_token: Optional[str] = None
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.login_fail_count = 0
        self.captcha_fail_count = 0
        self._login_error = False

    def _get_captcha_service(self) -> CaptchaService:
        if self._captcha_service is None:
            self._captcha_service = get_shared_captcha_service()
        return self._captcha_service

    def _now(self) -> str:
        return datetime.now(_BJT).strftime("%Y-%m-%d %H:%M:%S")

    async def _persist_platform_session(self, *, status: str) -> None:
        await account_platform_session_upsert(
            self.db,
            account_id=self.account_id,
            platform_type=self.platform_type,
            status=status,
            session_token=self.session_token,
            login_fail_count=self.login_fail_count,
            last_login_at=self._now() if self.session_token else None,
        )

    async def login(self) -> bool:
        if self._login_error:
            logger.warning(
                "Account %d platform=%s is blocked by login_error",
                self.account_id,
                self.platform_type,
            )
            return False

        for attempt in range(MAX_LOGIN_ATTEMPTS):
            try:
                result = await self._attempt_login()
                if result.success:
                    self.session_token = result.token
                    self.login_fail_count = 0
                    self.captcha_fail_count = 0
                    self._login_error = False
                    await self._persist_platform_session(status="online")
                    await account_update(
                        self.db,
                        account_id=self.account_id,
                        operator_id=self.operator_id,
                        status="online",
                        last_login_at=self._now(),
                        login_fail_count=0,
                    )
                    self._start_heartbeat()
                    logger.info(
                        "Account %d platform=%s login succeeded on attempt %d",
                        self.account_id,
                        self.platform_type,
                        attempt + 1,
                    )
                    return True

                self.login_fail_count += 1
                await self._persist_platform_session(status="login_failed")
                logger.warning(
                    "Account %d platform=%s login failed on attempt %d: %s",
                    self.account_id,
                    self.platform_type,
                    attempt + 1,
                    result.message,
                )
            except CaptchaError as exc:
                self.captcha_fail_count += 1
                logger.warning(
                    "Account %d platform=%s captcha failure on attempt %d (%d/%d): %s",
                    self.account_id,
                    self.platform_type,
                    attempt + 1,
                    self.captcha_fail_count,
                    MAX_CAPTCHA_FAILURES,
                    exc,
                )
                if self.captcha_fail_count >= MAX_CAPTCHA_FAILURES:
                    await self.alert_service.send(
                        operator_id=self.operator_id,
                        alert_type="captcha_fail",
                        title=f"Captcha failed {self.captcha_fail_count} times",
                        detail=f"Account {self.account_name} platform={self.platform_type} captcha recognition failed",
                        account_id=self.account_id,
                    )
                    return False

            if attempt < len(RETRY_DELAYS):
                await asyncio.sleep(RETRY_DELAYS[attempt])
            if attempt == PAUSE_AFTER_FAILURES - 1:
                await asyncio.sleep(PAUSE_DURATION)

        await self._mark_login_error()
        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="login_fail",
            title=f"Login failed {self.login_fail_count} times",
            detail=f"Account {self.account_name} platform={self.platform_type} reached retry limit",
            account_id=self.account_id,
        )
        return False

    async def _attempt_login(self) -> LoginResult:
        captcha_code: str | None = None
        if hasattr(self.adapter, "get_captcha"):
            captcha_image = await self.adapter.get_captcha()
            if captcha_image:
                captcha_code = await self._get_captcha_service().recognize(captcha_image)

        if captcha_code is None:
            return await self.adapter.login(self.account_name, self.password)
        return await self.adapter.login(
            self.account_name,
            self.password,
            captcha_code=captcha_code,
        )

    async def _mark_login_error(self) -> None:
        self._login_error = True
        await self._persist_platform_session(status="login_error")
        if self._on_status_change:
            await self._on_status_change(self.account_id, "login_error")
        logger.error(
            "Account %d platform=%s entered login_error after %d failures",
            self.account_id,
            self.platform_type,
            self.login_fail_count,
        )

    async def manual_login(self) -> bool:
        self._login_error = False
        self.login_fail_count = 0
        self.captcha_fail_count = 0
        return await self.login()

    def _start_heartbeat(self) -> None:
        if self.heartbeat_task and not self.heartbeat_task.done():
            return
        self.heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name=f"heartbeat-{self.account_id}-{self.platform_type}",
        )

    def stop_heartbeat(self) -> None:
        if self.heartbeat_task and not self.heartbeat_task.done():
            try:
                current_task = asyncio.current_task()
            except RuntimeError:
                current_task = None
            if self.heartbeat_task is current_task:
                self.heartbeat_task = None
                return
            self.heartbeat_task.cancel()
            self.heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        consecutive_fails = 0
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                ok = await self.adapter.heartbeat()
                if ok:
                    consecutive_fails = 0
                else:
                    consecutive_fails += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                consecutive_fails += 1
                logger.warning(
                    "Heartbeat failed for account %d platform=%s (%d/%d): %s",
                    self.account_id,
                    self.platform_type,
                    consecutive_fails,
                    HEARTBEAT_MAX_FAILS,
                    exc,
                )

            if consecutive_fails >= HEARTBEAT_MAX_FAILS:
                reconnect_ok = await self._reconnect()
                if not reconnect_ok:
                    await self.alert_service.send(
                        operator_id=self.operator_id,
                        alert_type="session_lost",
                        title="会话已断开，自动重连失败",
                        detail=(
                            f"账号 {self.account_name}（{self.platform_type}）"
                            f"连续 {HEARTBEAT_MAX_FAILS} 次心跳失败，自动重连未成功，"
                            "请检查账号登录状态或手动重新登录。"
                        ),
                        account_id=self.account_id,
                    )
                return

    async def _reconnect(self) -> bool:
        self.stop_heartbeat()
        await account_platform_session_upsert(
            self.db,
            account_id=self.account_id,
            platform_type=self.platform_type,
            status="reconnecting",
        )

        if hasattr(self.adapter, "refresh_token"):
            try:
                new_token = await self.adapter.refresh_token()
                if new_token:
                    self.session_token = new_token
                    await self._persist_platform_session(status="online")
                    await account_update(
                        self.db,
                        account_id=self.account_id,
                        operator_id=self.operator_id,
                        status="online",
                        last_login_at=self._now(),
                        login_fail_count=0,
                    )
                    self._start_heartbeat()
                    return True
            except Exception as exc:
                logger.warning(
                    "Refresh token failed for account %d platform=%s: %s",
                    self.account_id,
                    self.platform_type,
                    exc,
                )

        success = await self.login()
        if not success:
            self.session_token = None
            await self._persist_platform_session(status="login_error")
            logger.error(
                "Reconnect failed for account %d platform=%s",
                self.account_id,
                self.platform_type,
            )
        return success

    @property
    def is_logged_in(self) -> bool:
        return self.session_token is not None

    @property
    def is_login_error(self) -> bool:
        return self._login_error

    async def ensure_session(self) -> bool:
        if self.is_logged_in:
            return True
        if self._login_error:
            return False
        return await self.login()
