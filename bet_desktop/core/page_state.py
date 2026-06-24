"""Real Playwright page state assertions for canvas-heavy game tests."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .exceptions import AutomationCheckResult, AutomationErrorCode


class PageLike(Protocol):
    async def evaluate(self, expression: str, *args: Any) -> Any:
        """Evaluate JavaScript in a Playwright-like page."""

    async def screenshot(self, **kwargs: Any) -> bytes:
        """Return screenshot bytes."""

    async def wait_for_timeout(self, timeout_ms: float) -> None:
        """Wait without blocking the page."""


MaybeAsyncDetector = Callable[[PageLike], Any | Awaitable[Any]]


@dataclass(frozen=True)
class PageStateSnapshot:
    """Observed state for one browser instance."""

    is_processing: bool
    countdown_seconds: int
    batch_id: str
    modal_detected: bool = False
    disconnected: bool = False
    frozen: bool = False
    safe_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StateAssertionConfig:
    """Pre-submit state constraints."""

    min_safe_countdown_seconds: int = 6
    batch_stability_delay_ms: int = 120
    freeze_check_delay_ms: int = 180
    modal_selectors: tuple[str, ...] = (
        "[role='dialog']",
        ".modal",
        ".popup",
        ".dialog",
    )


class PageExceptionInterceptor:
    """Detect modal blocks, websocket interruption and page freeze symptoms."""

    def __init__(
        self,
        *,
        config: StateAssertionConfig | None = None,
        websocket_alive_detector: MaybeAsyncDetector | None = None,
    ) -> None:
        self.config = config or StateAssertionConfig()
        self.websocket_alive_detector = websocket_alive_detector

    async def check(self, page: PageLike) -> AutomationCheckResult:
        modal = await self._detect_modal(page)
        if modal:
            return AutomationCheckResult(
                ok=False,
                code=AutomationErrorCode.MODAL_BLOCKING,
                message="blocking modal detected",
                safe_summary={"selector": modal},
            )
        ws_alive = await self._websocket_alive(page)
        if ws_alive is False:
            return AutomationCheckResult(
                ok=False,
                code=AutomationErrorCode.WEBSOCKET_INTERRUPTED,
                message="websocket is not alive",
            )
        frozen = await self._looks_frozen(page)
        if frozen:
            return AutomationCheckResult(
                ok=False,
                code=AutomationErrorCode.PAGE_FROZEN,
                message="page screenshot did not change during freeze probe",
            )
        return AutomationCheckResult(ok=True)

    async def _detect_modal(self, page: PageLike) -> str | None:
        for selector in self.config.modal_selectors:
            try:
                visible = await page.evaluate(
                    """selector => {
                        const el = document.querySelector(selector);
                        if (!el) return false;
                        const style = getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 1 && rect.height > 1;
                    }""",
                    selector,
                )
            except Exception:
                visible = False
            if visible:
                return selector
        return None

    async def _websocket_alive(self, page: PageLike) -> bool | None:
        if self.websocket_alive_detector is None:
            return None
        value = self.websocket_alive_detector(page)
        if inspect.isawaitable(value):
            value = await value
        return bool(value)

    async def _looks_frozen(self, page: PageLike) -> bool:
        try:
            first = await page.screenshot(type="png", full_page=False)
            await page.wait_for_timeout(self.config.freeze_check_delay_ms)
            second = await page.screenshot(type="png", full_page=False)
        except Exception:
            return True
        return hashlib.sha256(first).digest() == hashlib.sha256(second).digest()


class PageStateAssertor:
    """Preflight gate for click-immediate canvas actions."""

    def __init__(
        self,
        *,
        detector: MaybeAsyncDetector,
        interceptor: PageExceptionInterceptor | None = None,
        config: StateAssertionConfig | None = None,
    ) -> None:
        self.detector = detector
        self.config = config or StateAssertionConfig()
        self.interceptor = interceptor or PageExceptionInterceptor(config=self.config)

    async def snapshot(self, page: PageLike) -> PageStateSnapshot:
        value = self.detector(page)
        if inspect.isawaitable(value):
            value = await value
        if isinstance(value, PageStateSnapshot):
            return value
        if isinstance(value, dict):
            return PageStateSnapshot(
                is_processing=bool(value.get("is_processing")),
                countdown_seconds=int(value.get("countdown_seconds", 0) or 0),
                batch_id=str(value.get("batch_id", "")),
                modal_detected=bool(value.get("modal_detected", False)),
                disconnected=bool(value.get("disconnected", False)),
                frozen=bool(value.get("frozen", False)),
                safe_summary=dict(value.get("safe_summary", {})),
            )
        raise TypeError("state detector must return PageStateSnapshot or dict")

    async def assert_ready(self, page: PageLike, *, expected_batch_id: str | None = None) -> AutomationCheckResult:
        exception_check = await self.interceptor.check(page)
        if not exception_check.ok:
            return exception_check

        first = await self.snapshot(page)
        await page.wait_for_timeout(self.config.batch_stability_delay_ms)
        second = await self.snapshot(page)
        if first.batch_id != second.batch_id:
            return AutomationCheckResult(
                ok=False,
                code=AutomationErrorCode.BATCH_SWITCHED,
                message="batch id changed during precheck",
                safe_summary={"first": first.batch_id, "second": second.batch_id},
            )
        if expected_batch_id is not None and second.batch_id != expected_batch_id:
            return AutomationCheckResult(
                ok=False,
                code=AutomationErrorCode.BATCH_SWITCHED,
                message="batch id does not match expected value",
                safe_summary={"expected": expected_batch_id, "actual": second.batch_id},
            )
        if not second.is_processing:
            return AutomationCheckResult(
                ok=False,
                code=AutomationErrorCode.PRECHECK_FAILED,
                message="page is not in processing/betting phase",
                safe_summary=second.safe_summary,
            )
        if second.countdown_seconds < self.config.min_safe_countdown_seconds:
            return AutomationCheckResult(
                ok=False,
                code=AutomationErrorCode.COUNTDOWN_UNSAFE,
                message="countdown is below safe threshold",
                safe_summary={
                    "countdown_seconds": second.countdown_seconds,
                    "threshold": self.config.min_safe_countdown_seconds,
                },
            )
        if second.modal_detected:
            return AutomationCheckResult(ok=False, code=AutomationErrorCode.MODAL_BLOCKING, message="modal detected")
        if second.disconnected:
            return AutomationCheckResult(ok=False, code=AutomationErrorCode.WEBSOCKET_INTERRUPTED, message="disconnected")
        if second.frozen:
            return AutomationCheckResult(ok=False, code=AutomationErrorCode.PAGE_FROZEN, message="page frozen")
        return AutomationCheckResult(
            ok=True,
            safe_summary={
                "batch_id": second.batch_id,
                "countdown_seconds": second.countdown_seconds,
                **second.safe_summary,
            },
        )


async def assert_all_ready(
    items: Sequence[tuple[str, PageStateAssertor, PageLike]],
    *,
    expected_batch_id: str | None = None,
) -> dict[str, AutomationCheckResult]:
    """Run preflight checks concurrently for many page instances."""

    import asyncio

    async def _run(account_id: str, assertor: PageStateAssertor, page: PageLike) -> tuple[str, AutomationCheckResult]:
        return account_id, await assertor.assert_ready(page, expected_batch_id=expected_batch_id)

    pairs = await asyncio.gather(*[_run(account_id, assertor, page) for account_id, assertor, page in items])
    return dict(pairs)

