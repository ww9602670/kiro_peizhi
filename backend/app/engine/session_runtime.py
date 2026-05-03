"""Unified account session runtime.

This module centralizes login/recover/platform-call serialization for one
account + platform runtime key.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import aiosqlite

from app.engine.adapters.base import PlatformAdapter, RemoteLoginRequired
from app.engine.adapters.factory import create_platform_adapter
from app.engine.alert import AlertService
from app.engine.session import SessionManager

logger = logging.getLogger(__name__)

RuntimeKey = tuple[int, str]
RuntimeCallable = Callable[[], Awaitable[Any]]

SESSION_ONLINE = "session_online"
SESSION_RECONNECTING = "session_reconnecting"
SESSION_BLOCKED = "session_blocked"
PLATFORM_RATE_LIMITED = "platform_rate_limited"
SESSION_ALERT_RECONNECTING = "账号会话异常，系统正在重连。日志编号：SESSION-001。"
SESSION_ALERT_RECONNECT_FAILED = "账号重连失败，请联系管理员处理。日志编号：SESSION-002。"

_BLOCK_MARKERS = (
    "403",
    "cloudflare",
    "captcha",
    "验证码",
    "风控",
    "verify code",
    "verification code",
    "risk control",
    "account abnormal",
)


def _normalize_platform_type(platform_type: str | None) -> str:
    return (platform_type or "JND28WEB").strip().upper()


def _normalize_error_message(exc_or_text: object) -> str:
    if isinstance(exc_or_text, BaseException):
        return str(exc_or_text or "").strip()
    return str(exc_or_text or "").strip()


def _is_blocking_message(message: str) -> bool:
    normalized = (message or "").strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in _BLOCK_MARKERS)


@dataclass
class SessionRuntimeSnapshot:
    account_id: int
    platform_type: str
    state: str
    blocked_until: float | None
    blocked_reason: str | None
    last_login_at: float | None
    last_failure_reason: str | None


class AccountSessionRuntime:
    """Per-account unified runtime with login/request/recover serialization."""

    def __init__(
        self,
        *,
        db: aiosqlite.Connection,
        alert_service: AlertService,
        operator_id: int,
        account_id: int,
        account_name: str,
        password: str,
        platform_type: str,
        platform_url: str | None = None,
        adapter: PlatformAdapter | None = None,
    ) -> None:
        self.db = db
        self.alert_service = alert_service
        self.operator_id = operator_id
        self.account_id = account_id
        self.account_name = account_name
        self.password = password
        self.platform_type = _normalize_platform_type(platform_type)
        self.platform_url = platform_url
        self.adapter = adapter or create_platform_adapter(self.platform_type, platform_url)
        self.session = SessionManager(
            adapter=self.adapter,
            alert_service=self.alert_service,
            operator_id=operator_id,
            account_id=account_id,
            account_name=account_name,
            password=password,
            platform_type=self.platform_type,
            db=db,
        )
        self._login_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._recover_lock = asyncio.Lock()
        self._state: str = SESSION_ONLINE
        self._blocked_until: float | None = None
        self._blocked_reason: str | None = None
        self._last_login_at: float | None = None
        self._last_failure_reason: str | None = None

    def matches_identity(
        self,
        *,
        account_name: str,
        password: str,
        platform_url: str | None,
    ) -> bool:
        return (
            self.account_name == account_name
            and self.password == password
            and (self.platform_url or "") == (platform_url or "")
        )

    def snapshot(self) -> SessionRuntimeSnapshot:
        return SessionRuntimeSnapshot(
            account_id=self.account_id,
            platform_type=self.platform_type,
            state=self._state,
            blocked_until=self._blocked_until,
            blocked_reason=self._blocked_reason,
            last_login_at=self._last_login_at,
            last_failure_reason=self._last_failure_reason,
        )

    async def ensure_logged_in(self, reason: str) -> bool:
        """Ensure one account has a valid session with single-flight login."""
        _ = reason
        if self._is_blocked():
            self._state = SESSION_BLOCKED
            return False

        async with self._login_lock:
            if self.session.is_logged_in:
                self._state = SESSION_ONLINE
                self._clear_failure_state()
                return True

            if self._is_blocked():
                self._state = SESSION_BLOCKED
                return False

            try:
                ok = await self.session.ensure_session()
            except Exception as exc:
                self._record_failure(exc)
                raise

            if ok:
                self._state = SESSION_ONLINE
                self._last_login_at = time.time()
                self._clear_failure_state()
                return True

            self._record_failure("ensure_session returned False")
            return False

    async def run_platform_call(self, reason: str, func: RuntimeCallable) -> Any:
        """Run one platform call under account-level serialization."""
        if not await self.ensure_logged_in(f"{reason}:ensure_login"):
            raise RuntimeError(f"session unavailable account_id={self.account_id}")

        async with self._request_lock:
            try:
                return await func()
            except RemoteLoginRequired as exc:
                self._record_failure(exc)
                if self._is_blocked():
                    await self._notify_session_reconnect_failed(str(exc))
                    raise
                self._state = SESSION_RECONNECTING
                await self._notify_session_reconnecting(str(exc))
                recovered = await self.recover(f"{reason}:remote_login")
                if not recovered:
                    await self._notify_session_reconnect_failed("recover_after_api_failure returned False")
                    raise
                try:
                    return await func()
                except Exception as retry_exc:
                    self._record_failure(retry_exc)
                    if self._is_blocked():
                        await self._notify_session_reconnect_failed(str(retry_exc))
                    raise
            except Exception as exc:
                self._record_failure(exc)
                if self._is_blocked():
                    await self._notify_session_reconnect_failed(str(exc))
                raise

    async def recover(self, reason: str) -> bool:
        """Recover session with single-flight reconnect."""
        _ = reason
        if self._is_blocked():
            self._state = SESSION_BLOCKED
            await self._notify_session_reconnect_failed(self._blocked_reason)
            return False
        async with self._recover_lock:
            if self._is_blocked():
                self._state = SESSION_BLOCKED
                await self._notify_session_reconnect_failed(self._blocked_reason)
                return False

            self._state = SESSION_RECONNECTING
            try:
                ok = await self.session.recover_after_api_failure()
            except Exception as exc:
                self._record_failure(exc)
                if self._is_blocked():
                    await self._notify_session_reconnect_failed(str(exc))
                raise

            if ok:
                self._state = SESSION_ONLINE
                self._last_login_at = time.time()
                self._clear_failure_state()
                return True

            self._record_failure("recover_after_api_failure returned False")
            return False

    async def close(self) -> None:
        try:
            self.session.stop_heartbeat()
        except Exception:
            logger.exception(
                "runtime heartbeat stop failed account_id=%d platform=%s",
                self.account_id,
                self.platform_type,
            )
        try:
            await self.adapter.close()
        except Exception:
            logger.exception(
                "runtime adapter close failed account_id=%d platform=%s",
                self.account_id,
                self.platform_type,
            )

    def _is_blocked(self) -> bool:
        if self._blocked_until is None:
            return False
        if time.time() < self._blocked_until:
            return True
        self._blocked_until = None
        self._blocked_reason = None
        return False

    def _clear_failure_state(self) -> None:
        self._last_failure_reason = None
        self._blocked_until = None
        self._blocked_reason = None

    def _record_failure(self, reason: object) -> None:
        message = _normalize_error_message(reason) or "unknown_runtime_error"
        self._last_failure_reason = message
        if self._is_failure_blocking(reason, message):
            self._state = SESSION_BLOCKED
            self._blocked_reason = message
            # Block automated retries for 5 minutes on risk/captcha/403 markers.
            self._blocked_until = time.time() + 300
        else:
            self._state = PLATFORM_RATE_LIMITED

    def _is_failure_blocking(self, reason: object, message: str) -> bool:
        if isinstance(reason, RemoteLoginRequired):
            if reason.blocking:
                return True
            return _is_blocking_message(message)
        return _is_blocking_message(message)

    async def _notify_session_reconnecting(self, detail: str | None = None) -> None:
        await self._safe_send_alert(
            alert_type="session_reconnecting",
            title=SESSION_ALERT_RECONNECTING,
            detail=detail,
        )

    async def _notify_session_reconnect_failed(self, detail: str | None = None) -> None:
        await self._safe_send_alert(
            alert_type="session_reconnect_failed",
            title=SESSION_ALERT_RECONNECT_FAILED,
            detail=detail,
        )

    async def _safe_send_alert(self, *, alert_type: str, title: str, detail: str | None) -> None:
        try:
            await self.alert_service.send(
                operator_id=self.operator_id,
                alert_type=alert_type,
                title=title,
                detail=detail,
                account_id=self.account_id,
            )
        except Exception:
            logger.exception(
                "session runtime alert send failed account_id=%d platform=%s alert_type=%s",
                self.account_id,
                self.platform_type,
                alert_type,
            )


class SessionRuntimeRegistry:
    """Registry for account session runtimes."""

    def __init__(self) -> None:
        self._runtimes: dict[RuntimeKey, AccountSessionRuntime] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        *,
        db: aiosqlite.Connection,
        alert_service: AlertService,
        operator_id: int,
        account_id: int,
        account_name: str,
        password: str,
        platform_type: str,
        platform_url: str | None = None,
    ) -> AccountSessionRuntime:
        key = (account_id, _normalize_platform_type(platform_type))
        async with self._lock:
            runtime = self._runtimes.get(key)
            if runtime is not None and runtime.matches_identity(
                account_name=account_name,
                password=password,
                platform_url=platform_url,
            ):
                return runtime

            if runtime is not None:
                await runtime.close()

            runtime = AccountSessionRuntime(
                db=db,
                alert_service=alert_service,
                operator_id=operator_id,
                account_id=account_id,
                account_name=account_name,
                password=password,
                platform_type=key[1],
                platform_url=platform_url,
            )
            self._runtimes[key] = runtime
            return runtime

    def get(self, account_id: int, platform_type: str) -> AccountSessionRuntime | None:
        key = (account_id, _normalize_platform_type(platform_type))
        return self._runtimes.get(key)

    def find_by_account(self, account_id: int) -> AccountSessionRuntime | None:
        for key, runtime in self._runtimes.items():
            if key[0] == account_id:
                return runtime
        return None

    async def remove(self, account_id: int, platform_type: str) -> None:
        key = (account_id, _normalize_platform_type(platform_type))
        async with self._lock:
            runtime = self._runtimes.pop(key, None)
        if runtime is not None:
            await runtime.close()

    async def shutdown(self) -> None:
        async with self._lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
        for runtime in runtimes:
            await runtime.close()
