from __future__ import annotations

import asyncio

import pytest

from bet_desktop.coordinator.live_executor import LiveEnvironmentExecutor, LiveExecutionBlocked, LiveExecutorConfig
from bet_desktop.core.adapters.canvas import CanvasBetAdapter, CanvasMarkerCheck
from bet_desktop.core.exceptions import AutomationCheckResult
from bet_desktop.core.group_action import CanvasActionInstance, GroupActionRequest


class FakeMouse:
    def __init__(self) -> None:
        self.clicks = []

    async def click(self, x: float, y: float) -> None:
        self.clicks.append((x, y))


class FakePage:
    def __init__(self) -> None:
        self.mouse = FakeMouse()

    async def wait_for_timeout(self, timeout_ms: float) -> None:
        return None

    async def screenshot(self, **kwargs):
        return b"fake"

    async def evaluate(self, expression: str, *args):
        return False


class FakeStateAssertor:
    async def assert_ready(self, page, *, expected_batch_id=None):
        return AutomationCheckResult(ok=True)


def _instances():
    result = []
    for account_id, role in [("a1", "main"), ("a2", "sub"), ("a3", "sub"), ("a4", "sub")]:
        page = FakePage()
        adapter = CanvasBetAdapter(page, marker_verifier=lambda *_: CanvasMarkerCheck(ok=True))
        result.append(
            CanvasActionInstance(
                account_id=account_id,
                role=role,  # type: ignore[arg-type]
                page=page,
                adapter=adapter,
                state_assertor=FakeStateAssertor(),  # type: ignore[arg-type]
            )
        )
    return result


def _request() -> GroupActionRequest:
    return GroupActionRequest(
        batch_id="batch-1",
        main_account_id="a1",
        main_side="闲",
        opposite_side="庄",
        main_amount=40,
        denominations=(10, 20),
        sub_amounts=(10, 10, 20),
    )


def test_live_executor_calibrates_without_clicking() -> None:
    instances = _instances()
    executor = LiveEnvironmentExecutor(instances, jitter_provider=lambda _account_id: 0)

    async def _run() -> None:
        result = await executor.calibrate(_request())
        assert result.ok
        assert result.safe_summary["live_submission"] == "blocked"

    asyncio.run(_run())

    assert all(instance.page.mouse.clicks == [] for instance in instances)


def test_live_executor_blocks_live_submission() -> None:
    executor = LiveEnvironmentExecutor(_instances(), jitter_provider=lambda _account_id: 0)

    async def _run() -> None:
        with pytest.raises(LiveExecutionBlocked):
            await executor.execute_live_blocked(_request())

    asyncio.run(_run())


def test_live_executor_shadow_run_is_non_destructive_and_logs_steps() -> None:
    instances = _instances()
    executor = LiveEnvironmentExecutor(
        instances,
        config=LiveExecutorConfig(shadow_ack_window_ms=1, shadow_human_delay_ms=1),
        jitter_provider=lambda _account_id: 0,
    )
    logs: list[str] = []

    async def _run() -> None:
        result = await executor.execute_shadow_run(_request(), logger=logs.append)
        assert result.ok
        assert result.phase == "shadow_execute"
        assert result.safe_summary["live_clicks"] == 0
        assert result.safe_summary["physical_clicks"] == "blocked"
        assert result.safe_summary["concurrency"] == "asyncio.gather"
        assert result.safe_summary["serial_step_order"] == "log_probe_delay"
        assert result.safe_summary["ack_timeout_count"] == result.safe_summary["chip_step_count"]

    asyncio.run(_run())

    assert all(instance.page.mouse.clicks == [] for instance in instances)
    dry_logs = [item for item in logs if item.startswith("[SHADOW_DRY_RUN]") and "targeting" in item]
    assert len(dry_logs) == 5
    assert any("Instance a1 targeting" in item and "Coordinates(" in item for item in dry_logs)


def test_live_executor_shadow_run_observes_fake_outbound_rtt() -> None:
    instances = _instances()
    executor = LiveEnvironmentExecutor(
        instances,
        config=LiveExecutorConfig(shadow_ack_window_ms=50, shadow_human_delay_ms=1),
        jitter_provider=lambda _account_id: 0,
    )
    probe_calls: list[tuple[str, int]] = []

    async def fake_session_manager(*, account_id: str, packet_size: int):
        probe_calls.append((account_id, packet_size))
        await asyncio.sleep(0)
        return {"rtt_ms": 12.5}

    async def _run() -> None:
        result = await executor.execute_shadow_run(_request(), session_manager=fake_session_manager)
        assert result.safe_summary["ack_observed_count"] == result.safe_summary["chip_step_count"]
        assert result.safe_summary["ack_timeout_count"] == 0

    asyncio.run(_run())

    assert all(packet_size == 36 for _account_id, packet_size in probe_calls)
    assert all(instance.page.mouse.clicks == [] for instance in instances)
