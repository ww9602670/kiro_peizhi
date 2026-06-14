"""Live environment integration harness.

This module wires real Playwright pages into the existing group-action
architecture for calibration and assertions. It intentionally blocks automatic
production click submission because the target canvas click is irreversible.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from bet_desktop.core.exceptions import AutomationCheckResult, AutomationErrorCode, AutomationException
from bet_desktop.core.group_action import (
    CanvasActionInstance,
    GroupActionCoordinator,
    GroupActionRequest,
    GroupActionResult,
    InstanceActionResult,
    InstanceActionPlan,
)


DisasterNotifier = Callable[[dict[str, Any]], None | Awaitable[None]]
ShadowLogger = Callable[[str], None | Awaitable[None]]


async def _immediate_probe_result(value: Any) -> Any:
    return value


class LiveExecutionBlocked(AutomationException):
    """Raised when code attempts to submit irreversible live clicks."""

    def __init__(self) -> None:
        super().__init__(
            AutomationErrorCode.CLICK_FAILED,
            "automatic live click submission is blocked; use calibration or manual execution",
            {"live_clicks": "blocked"},
        )


@dataclass(frozen=True)
class BalanceEquationResult:
    """Result of post-action hedge equation checks."""

    ok: bool
    main_delta: float
    sub_delta_sum: float
    tolerance: float
    safe_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LiveCalibrationResult:
    """Non-destructive live-page integration result."""

    ok: bool
    plans: tuple[InstanceActionPlan, ...]
    precheck_results: Mapping[str, AutomationCheckResult]
    preview_results: Mapping[str, Any]
    safe_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ShadowProbeResult:
    """Passive outbound packet probe result for one virtual click step."""

    status: str
    rtt_ms: float | None = None
    safe_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LiveExecutorConfig:
    """Live integration safety and assertion settings."""

    marker_verify_after_ms: int = 200
    balance_tolerance: float = 0.01
    block_live_submission: bool = True
    shadow_ack_window_ms: int = 150
    shadow_human_delay_ms: int = 200


class LiveEnvironmentExecutor:
    """Calibrate and assert real Playwright pages without auto-submitting clicks."""

    def __init__(
        self,
        instances: Sequence[CanvasActionInstance],
        *,
        config: LiveExecutorConfig | None = None,
        disaster_notifier: DisasterNotifier | None = None,
        jitter_provider=None,
    ) -> None:
        self.instances = {item.account_id: item for item in instances}
        self.config = config or LiveExecutorConfig()
        self.disaster_notifier = disaster_notifier
        self.group_coordinator = GroupActionCoordinator(
            instances,
            jitter_provider=jitter_provider,
        )

    async def calibrate(self, request: GroupActionRequest) -> LiveCalibrationResult:
        """Run live prechecks and return click plans without touching the page."""

        plans = self.group_coordinator.build_plans(request)
        prechecks = await self.group_coordinator.prepare(request=request, plans=plans)
        preview_results: dict[str, Any] = {}
        for plan in plans:
            instance = self.instances[plan.account_id]
            chip_previews = []
            for chip in plan.chip_sequence:
                preview = await instance.adapter.preview_canvas_bet(
                    side=plan.side,
                    amount=chip,
                    issue=request.batch_id,
                )
                chip_previews.append(
                    {
                        "ok": preview.ok,
                        "code": preview.code.value,
                        "message": preview.message,
                        "data": preview.data,
                        "safe_summary": preview.safe_summary,
                    }
                )
            preview_results[plan.account_id] = chip_previews

        ok = all(result.ok for result in prechecks.values()) and all(
            all(item["ok"] for item in preview_results[account_id])
            for account_id in preview_results
        )
        return LiveCalibrationResult(
            ok=ok,
            plans=tuple(plans),
            precheck_results=prechecks,
            preview_results=preview_results,
            safe_summary={"mode": "calibration", "live_submission": "blocked"},
        )

    async def execute_live_blocked(self, request: GroupActionRequest) -> GroupActionResult:
        """Explicitly block irreversible live commit attempts."""

        if self.config.block_live_submission:
            raise LiveExecutionBlocked()
        raise LiveExecutionBlocked()

    async def execute_shadow_run(
        self,
        request: GroupActionRequest,
        *,
        session_manager: Any = None,
        logger: ShadowLogger | None = None,
    ) -> GroupActionResult:
        """Run a non-destructive chip-sequence stress test.

        The four account plans are started concurrently. Inside each account the
        chip steps stay serial: dry-run log, passive 150 ms outbound probe, then
        a 200 ms human-like delay before the next virtual click.
        """

        run_started = time.perf_counter()
        build_started = time.perf_counter()
        plans = self.group_coordinator.build_plans(request)
        build_plan_ms = (time.perf_counter() - build_started) * 1000

        async def _run_plan(plan: InstanceActionPlan) -> tuple[str, InstanceActionResult]:
            instance = self.instances[plan.account_id]
            step_summaries: list[dict[str, Any]] = []
            plan_started = time.perf_counter()
            for index, chip in enumerate(plan.chip_sequence, start=1):
                steps = instance.adapter.build_click_plan(side=plan.side, amount=chip)
                side_step = next((step for step in steps if step.action == "click_side"), steps[-1])
                chip_step = next((step for step in steps if step.action == "select_chip"), steps[0])
                await self._emit_shadow_log(
                    logger,
                    (
                        f"[SHADOW_DRY_RUN] Instance {plan.account_id} targeting {plan.side} "
                        f"with {chip} at Coordinates({side_step.x}, {side_step.y})"
                    ),
                )
                probe = await self._probe_shadow_ack(
                    session_manager=session_manager,
                    account_id=plan.account_id,
                    packet_size=36,
                    timeout_ms=self.config.shadow_ack_window_ms,
                )
                if probe.status == "ACK_OBSERVED":
                    await self._emit_shadow_log(
                        logger,
                        f"[SHADOW_DRY_RUN] Instance {plan.account_id} outbound 36B RTT={probe.rtt_ms:.1f}ms",
                    )
                else:
                    await self._emit_shadow_log(
                        logger,
                        f"[SHADOW_DRY_RUN] Instance {plan.account_id} outbound 36B {probe.status}",
                    )
                step_summaries.append(
                    {
                        "step": index,
                        "chip": chip,
                        "side": plan.side,
                        "chip_coordinate": {"x": chip_step.x, "y": chip_step.y},
                        "target_coordinate": {"x": side_step.x, "y": side_step.y},
                        "probe": probe.safe_summary,
                    }
                )
                await asyncio.sleep(max(0, self.config.shadow_human_delay_ms) / 1000)

            elapsed_ms = (time.perf_counter() - plan_started) * 1000
            return plan.account_id, InstanceActionResult(
                account_id=plan.account_id,
                ok=True,
                safe_summary={
                    "mode": "shadow_dry_run",
                    "side": plan.side,
                    "amount": plan.amount,
                    "chip_steps": len(plan.chip_sequence),
                    "elapsed_ms": round(elapsed_ms, 3),
                    "steps": tuple(step_summaries),
                    "live_clicks": 0,
                },
            )

        action_results = dict(await asyncio.gather(*[_run_plan(plan) for plan in plans]))
        elapsed_ms = (time.perf_counter() - run_started) * 1000
        timeout_count = sum(
            1
            for result in action_results.values()
            for step in result.safe_summary.get("steps", ())
            if step.get("probe", {}).get("status") == "ACK_TIMEOUT"
        )
        observed_count = sum(
            1
            for result in action_results.values()
            for step in result.safe_summary.get("steps", ())
            if step.get("probe", {}).get("status") == "ACK_OBSERVED"
        )
        return GroupActionResult(
            ok=True,
            phase="shadow_execute",
            plans=tuple(plans),
            action_results=action_results,
            safe_summary={
                "mode": "shadow_dry_run",
                "live_clicks": 0,
                "physical_clicks": "blocked",
                "account_count": len(plans),
                "chip_step_count": sum(len(plan.chip_sequence) for plan in plans),
                "build_plan_ms": round(build_plan_ms, 3),
                "total_elapsed_ms": round(elapsed_ms, 3),
                "ack_timeout_count": timeout_count,
                "ack_observed_count": observed_count,
                "concurrency": "asyncio.gather",
                "serial_step_order": "log_probe_delay",
                "ack_window_ms": self.config.shadow_ack_window_ms,
                "human_delay_ms": self.config.shadow_human_delay_ms,
            },
        )

    async def _emit_shadow_log(self, logger: ShadowLogger | None, message: str) -> None:
        if logger is None:
            return
        result = logger(message)
        if inspect.isawaitable(result):
            await result

    async def _probe_shadow_ack(
        self,
        *,
        session_manager: Any,
        account_id: str,
        packet_size: int,
        timeout_ms: int,
    ) -> ShadowProbeResult:
        timeout_seconds = max(0, timeout_ms) / 1000
        started = time.perf_counter()
        awaitable = self._build_shadow_probe_awaitable(
            session_manager=session_manager,
            account_id=account_id,
            packet_size=packet_size,
        )
        try:
            if awaitable is None:
                await asyncio.sleep(timeout_seconds)
                raise TimeoutError()
            packet = await asyncio.wait_for(awaitable, timeout=timeout_seconds)
        except (asyncio.TimeoutError, TimeoutError):
            return ShadowProbeResult(
                status="ACK_TIMEOUT",
                safe_summary={"status": "ACK_TIMEOUT", "packet_size": packet_size, "window_ms": timeout_ms},
            )

        rtt_ms = self._extract_probe_rtt_ms(packet, started)
        return ShadowProbeResult(
            status="ACK_OBSERVED",
            rtt_ms=rtt_ms,
            safe_summary={
                "status": "ACK_OBSERVED",
                "packet_size": packet_size,
                "window_ms": timeout_ms,
                "rtt_ms": round(rtt_ms, 3),
            },
        )

    def _build_shadow_probe_awaitable(
        self,
        *,
        session_manager: Any,
        account_id: str,
        packet_size: int,
    ) -> Awaitable[Any] | None:
        if session_manager is None:
            return None
        if callable(session_manager):
            result = session_manager(account_id=account_id, packet_size=packet_size)
            return result if inspect.isawaitable(result) else _immediate_probe_result(result)
        for method_name in ("wait_for_outbound", "wait_for_outbound_packet", "next_outbound"):
            method = getattr(session_manager, method_name, None)
            if method is None:
                continue
            try:
                result = method(account_id=account_id, packet_size=packet_size)
            except TypeError:
                result = method(account_id, packet_size)
            return result if inspect.isawaitable(result) else _immediate_probe_result(result)
        return None

    @staticmethod
    def _extract_probe_rtt_ms(packet: Any, started: float) -> float:
        if isinstance(packet, Mapping):
            for key in ("rtt_ms", "latency_ms", "elapsed_ms"):
                value = packet.get(key)
                if value is not None:
                    return float(value)
        value = getattr(packet, "rtt_ms", None)
        if value is not None:
            return float(value)
        return (time.perf_counter() - started) * 1000

    async def verify_markers_after_manual_action(self, plans: Sequence[InstanceActionPlan]) -> dict[str, AutomationCheckResult]:
        """After a manual action, verify markers through each adapter verifier."""

        await asyncio.sleep(max(0, self.config.marker_verify_after_ms) / 1000)
        results: dict[str, AutomationCheckResult] = {}
        for plan in plans:
            instance = self.instances[plan.account_id]
            verifier = instance.adapter.marker_verifier
            if verifier is None:
                results[plan.account_id] = AutomationCheckResult(
                    ok=False,
                    code=AutomationErrorCode.MARKER_MISSING,
                    message="marker verifier is not configured",
                )
                continue
            checks = []
            for chip in plan.chip_sequence:
                check = verifier(instance.page, plan.side, chip, instance.adapter.config)
                if hasattr(check, "__await__"):
                    check = await check
                checks.append(check)
            failed = [check for check in checks if not check.ok]
            results[plan.account_id] = AutomationCheckResult(
                ok=not failed,
                code=AutomationErrorCode.PASS if not failed else AutomationErrorCode.MARKER_MISSING,
                message="" if not failed else "one or more marker checks failed",
                safe_summary={"checks": [dict(check.safe_summary) for check in checks]},
            )
        return results

    async def verify_balance_equation(
        self,
        *,
        before_balances: Mapping[str, float],
        after_balances: Mapping[str, float],
        main_account_id: str,
        sub_account_ids: Sequence[str],
    ) -> BalanceEquationResult:
        """Verify main/sub deltas satisfy the expected hedge equation."""

        main_delta = float(after_balances[main_account_id]) - float(before_balances[main_account_id])
        sub_delta_sum = sum(float(after_balances[account_id]) - float(before_balances[account_id]) for account_id in sub_account_ids)
        ok = abs(abs(main_delta) - abs(sub_delta_sum)) <= self.config.balance_tolerance
        result = BalanceEquationResult(
            ok=ok,
            main_delta=main_delta,
            sub_delta_sum=sub_delta_sum,
            tolerance=self.config.balance_tolerance,
            safe_summary={
                "main_account_id": main_account_id,
                "sub_count": len(sub_account_ids),
                "main_delta": round(main_delta, 4),
                "sub_delta_sum": round(sub_delta_sum, 4),
                "tolerance": self.config.balance_tolerance,
            },
        )
        if not ok:
            await self._notify_disaster(
                {
                    "event": "balance_equation_failed",
                    **result.safe_summary,
                }
            )
        return result

    async def _notify_disaster(self, payload: dict[str, Any]) -> None:
        if self.disaster_notifier is None:
            return
        value = self.disaster_notifier(payload)
        if hasattr(value, "__await__"):
            await value
