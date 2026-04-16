"""

Phase 5.4: 
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Callable, Awaitable

import aiosqlite

from app.engine.adapters.base import PlatformAdapter, LoginResult
from app.engine.alert import AlertService
from app.models.db_ops import account_update
from app.utils.captcha import CaptchaError, CaptchaService

logger = logging.getLogger(__name__)

# 
RETRY_DELAYS: list[int] = [30, 60, 120]

# 
MAX_LOGIN_ATTEMPTS = 5
PAUSE_AFTER_FAILURES = 3
PAUSE_DURATION = 600  # 10 

# 
MAX_CAPTCHA_FAILURES = 5

# 
HEARTBEAT_INTERVAL = 75
HEARTBEAT_MAX_FAILS = 3


class SessionManager:
    """

    
    -  + 
    -  75 
    - token   
    """

    def __init__(
        self,
        *,
        adapter: PlatformAdapter,
        alert_service: AlertService,
        captcha_service: CaptchaService,
        operator_id: int,
        account_id: int,
        account_name: str,
        password: str,
        db: aiosqlite.Connection,
        on_status_change: Optional[Callable[[int, str], Awaitable[None]]] = None,
    ) -> None:
        self.adapter = adapter
        self.alert_service = alert_service
        self.captcha_service = captcha_service
        self.operator_id = operator_id
        self.account_id = account_id
        self.account_name = account_name
        self.password = password
        self.db = db
        self._on_status_change = on_status_change

        # 
        self.session_token: Optional[str] = None
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.login_fail_count: int = 0
        self.captcha_fail_count: int = 0
        self._login_error: bool = False  # 5 

    #   

    async def login(self) -> bool:
        """

        
        -  3  30s  60s  120s
        -  3  10  4 
        -  5  login_error

        Returns:
            True=, False=
        """
        if self._login_error:
            logger.warning(
                " %d  login_error",
                self.account_id,
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
                    
                    #   session_token 
                    await self._update_session_token_to_db()
                    
                    self._start_heartbeat()
                    logger.info(
                        " %d  (attempt=%d, token=%s)",
                        self.account_id,
                        attempt + 1,
                        self.session_token[:20] + "..." if self.session_token else "None",
                    )
                    return True
                else:
                    self.login_fail_count += 1
                    logger.warning(
                        " %d  (attempt=%d, reason=%s, fail_count=%d)",
                        self.account_id,
                        attempt + 1,
                        result.message,
                        self.login_fail_count,
                    )
            except CaptchaError as e:
                self.captcha_fail_count += 1
                logger.warning(
                    " %d  (attempt=%d, captcha_fail=%d): %s",
                    self.account_id,
                    attempt + 1,
                    self.captcha_fail_count,
                    e,
                )
                if self.captcha_fail_count >= MAX_CAPTCHA_FAILURES:
                    await self.alert_service.send(
                        operator_id=self.operator_id,
                        alert_type="captcha_fail",
                        title=f" {self.captcha_fail_count} ",
                        detail=f" {self.account_name} ",
                        account_id=self.account_id,
                    )
                    return False

            # 
            if attempt < len(RETRY_DELAYS):
                delay = RETRY_DELAYS[attempt]
                logger.info(
                    " %d  %ds  (attempt=%d)",
                    self.account_id,
                    delay,
                    attempt + 1,
                )
                await asyncio.sleep(delay)

            #  3  10 
            if attempt == 2:
                logger.info(
                    " %d  3  %ds",
                    self.account_id,
                    PAUSE_DURATION,
                )
                await asyncio.sleep(PAUSE_DURATION)

        #  5 
        await self._mark_login_error()
        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="login_fail",
            title=f" {self.login_fail_count} ",
            detail=f" {self.account_name}  {self.login_fail_count} ",
            account_id=self.account_id,
        )
        return False

    async def _attempt_login(self) -> LoginResult:
        """+验证码识别+登录"""
        # 如果 adapter 支持获取验证码，先获取并识别
        captcha_code: Optional[str] = None
        if hasattr(self.adapter, "get_captcha"):
            captcha_image = await self.adapter.get_captcha()
            if captcha_image:
                captcha_code = await self.captcha_service.recognize(captcha_image)

        # 登录（传入验证码，不需要的平台会忽略）
        result = await self.adapter.login(self.account_name, self.password, captcha_code=captcha_code)
        return result

    async def _update_session_token_to_db(self) -> None:
        """ session_token """
        try:
            await account_update(
                self.db,
                account_id=self.account_id,
                operator_id=self.operator_id,
                session_token=self.session_token,
            )
            logger.info(
                " session_token account_id=%d token=%s",
                self.account_id,
                self.session_token[:20] + "..." if self.session_token else "None",
            )
        except Exception as e:
            logger.error(
                "  session_token account_id=%d error=%s",
                self.account_id,
                e,
            )

    async def _mark_login_error(self) -> None:
        """ login_error """
        self._login_error = True
        if self._on_status_change:
            await self._on_status_change(self.account_id, "login_error")
        logger.error(
            " %d  login_error %d ",
            self.account_id,
            self.login_fail_count,
        )

    async def manual_login(self) -> bool:
        """ login_error """
        self._login_error = False
        self.login_fail_count = 0
        self.captcha_fail_count = 0
        return await self.login()

    #   

    def _start_heartbeat(self) -> None:
        """"""
        if self.heartbeat_task and not self.heartbeat_task.done():
            return  # 
        self.heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name=f"heartbeat-{self.account_id}",
        )

    def stop_heartbeat(self) -> None:
        """"""
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()
            self.heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        """ 75  adapter.heartbeat()

         3  reconnect
        """
        consecutive_fails = 0
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                ok = await self.adapter.heartbeat()
                if ok:
                    consecutive_fails = 0
                else:
                    consecutive_fails += 1
                    logger.warning(
                        " %d  (consecutive=%d)",
                        self.account_id,
                        consecutive_fails,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                consecutive_fails += 1
                logger.warning(
                    " %d  (consecutive=%d): %s",
                    self.account_id,
                    consecutive_fails,
                    e,
                )

            if consecutive_fails >= HEARTBEAT_MAX_FAILS:
                logger.warning(
                    " %d  %d ",
                    self.account_id,
                    consecutive_fails,
                )
                await self.alert_service.send(
                    operator_id=self.operator_id,
                    alert_type="session_lost",
                    title="",
                    detail=f" {self.account_name}  {consecutive_fails} ",
                    account_id=self.account_id,
                )
                await self._reconnect()
                consecutive_fails = 0

    #   

    async def _reconnect(self) -> None:
        """ token """
        self.stop_heartbeat()
        self.session_token = None

        #  token 
        if hasattr(self.adapter, "refresh_token"):
            try:
                new_token = await self.adapter.refresh_token()
                if new_token:
                    self.session_token = new_token
                    await self._update_session_token_to_db()
                    self._start_heartbeat()
                    logger.info(" %d token ", self.account_id)
                    return
            except Exception as e:
                logger.warning(
                    " %d token : %s",
                    self.account_id,
                    e,
                )

        # 
        success = await self.login()
        if not success:
            logger.error(" %d ", self.account_id)

    #   

    @property
    def is_logged_in(self) -> bool:
        """"""
        return self.session_token is not None

    @property
    def is_login_error(self) -> bool:
        """ login_error """
        return self._login_error

    async def ensure_session(self) -> bool:
        """"""
        if self.is_logged_in:
            return True
        if self._login_error:
            return False
        return await self.login()
