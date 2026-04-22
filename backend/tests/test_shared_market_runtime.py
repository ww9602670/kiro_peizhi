from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.engine.adapters.base import InstallInfo
from app.engine.shared_market_runtime import (
    PUBLIC_STATE_LOCAL_FALLBACK,
    PUBLIC_STATE_PROCESSING,
    PUBLIC_STATE_SHARED_HIT,
    SharedMarketRuntime,
    SharedMarketSnapshot,
    normalize_market_url,
)


def _make_install(
    *,
    issue: str = "20260421001",
    state: int = 1,
    close_countdown_sec: int = 45,
    open_countdown_sec: int = 30,
    pre_issue: str = "20260421000",
    pre_result: str = "1,2,3",
) -> InstallInfo:
    return InstallInfo(
        issue=issue,
        state=state,
        close_countdown_sec=close_countdown_sec,
        open_countdown_sec=open_countdown_sec,
        pre_issue=pre_issue,
        pre_result=pre_result,
    )


class _FakeContracts:
    def __init__(
        self,
        *,
        shared_group_id: int | None,
        snapshot: SharedMarketSnapshot | None = None,
    ) -> None:
        self.shared_group_id = shared_group_id
        self.snapshot = snapshot
        self.upserts: list[tuple[int, str, str]] = []
        self.uncovered_touches: list[tuple[str, str]] = []

    async def group_resolve_by_url(
        self,
        *,
        platform_type: str,
        normalized_url: str,
    ) -> int | None:
        return self.shared_group_id

    async def snapshot_upsert(
        self,
        *,
        shared_group_id: int,
        install: InstallInfo,
        source_status: str,
    ) -> None:
        self.upserts.append((shared_group_id, install.issue, source_status))

    async def snapshot_get_latest(self, *, shared_group_id: int) -> SharedMarketSnapshot | None:
        return self.snapshot

    async def uncovered_url_touch(
        self,
        *,
        platform_type: str,
        normalized_url: str,
    ) -> None:
        self.uncovered_touches.append((platform_type, normalized_url))


def test_normalize_market_url_keeps_origin_and_path():
    normalized = normalize_market_url("HTTPS://EXAMPLE.com:443/abc/def/?a=1#frag")
    assert normalized == "https://example.com:443/abc/def"


@pytest.mark.asyncio
async def test_resolve_install_uses_fresh_snapshot_without_local_poll():
    snapshot = SharedMarketSnapshot(
        shared_group_id=7,
        issue="20260421009",
        state=1,
        close_countdown_sec=41,
        open_countdown_sec=26,
        pre_issue="20260421008",
        pre_result="2,4,6",
        fetched_at=datetime.now(),
        source_status="ok",
    )
    contracts = _FakeContracts(shared_group_id=7, snapshot=snapshot)
    runtime = SharedMarketRuntime(db=AsyncMock(), contracts=contracts)
    fetch_local_install = AsyncMock(return_value=_make_install(issue="local"))

    install = await runtime.resolve_install(
        platform_type="JND28WEB",
        platform_url="https://example.com/abc?a=1",
        owner_key="100:JND28WEB",
        fetch_local_install=fetch_local_install,
    )

    assert install.issue == "20260421009"
    assert getattr(install, "market_data_state", None) == PUBLIC_STATE_SHARED_HIT
    fetch_local_install.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_install_miss_touches_uncovered_and_fallback():
    contracts = _FakeContracts(shared_group_id=None, snapshot=None)
    runtime = SharedMarketRuntime(db=AsyncMock(), contracts=contracts)
    fetch_local_install = AsyncMock(return_value=_make_install(issue="fallback"))

    install = await runtime.resolve_install(
        platform_type="JND28WEB",
        platform_url="https://example.com/path",
        owner_key="100:JND28WEB",
        fetch_local_install=fetch_local_install,
    )

    assert install.issue == "fallback"
    assert getattr(install, "market_data_state", None) == PUBLIC_STATE_LOCAL_FALLBACK
    fetch_local_install.assert_awaited_once()
    assert contracts.uncovered_touches == [("JND28WEB", "https://example.com/path")]


@pytest.mark.asyncio
async def test_resolve_install_stale_snapshot_fallback_and_refresh():
    stale_snapshot = SharedMarketSnapshot(
        shared_group_id=9,
        issue="20260421011",
        state=1,
        close_countdown_sec=50,
        open_countdown_sec=20,
        pre_issue="20260421010",
        pre_result="3,3,3",
        fetched_at=datetime.now() - timedelta(seconds=60),
        source_status="ok",
    )
    contracts = _FakeContracts(shared_group_id=9, snapshot=stale_snapshot)
    runtime = SharedMarketRuntime(
        db=AsyncMock(),
        contracts=contracts,
        freshness_seconds=15,
    )
    fetch_local_install = AsyncMock(return_value=_make_install(issue="fresh_local"))

    install = await runtime.resolve_install(
        platform_type="JND28WEB",
        platform_url="https://example.com/path",
        owner_key="100:JND28WEB",
        fetch_local_install=fetch_local_install,
    )

    assert install.issue == "fresh_local"
    assert getattr(install, "market_data_state", None) == PUBLIC_STATE_PROCESSING
    fetch_local_install.assert_awaited_once()
    assert contracts.upserts
    assert contracts.upserts[-1][2] == "ok"


@pytest.mark.asyncio
async def test_resolve_install_error_snapshot_fallback():
    error_snapshot = SharedMarketSnapshot(
        shared_group_id=10,
        issue="20260421012",
        state=1,
        close_countdown_sec=50,
        open_countdown_sec=20,
        pre_issue="20260421011",
        pre_result="4,4,4",
        fetched_at=datetime.now(),
        source_status="error",
    )
    contracts = _FakeContracts(shared_group_id=10, snapshot=error_snapshot)
    runtime = SharedMarketRuntime(db=AsyncMock(), contracts=contracts)
    fetch_local_install = AsyncMock(return_value=_make_install(issue="local_after_error"))

    install = await runtime.resolve_install(
        platform_type="JND28WEB",
        platform_url="https://example.com/path",
        owner_key="100:JND28WEB",
        fetch_local_install=fetch_local_install,
    )

    assert install.issue == "local_after_error"
    assert getattr(install, "market_data_state", None) == PUBLIC_STATE_PROCESSING
    fetch_local_install.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_install_invalid_url_local_fallback_state():
    contracts = _FakeContracts(shared_group_id=20, snapshot=None)
    runtime = SharedMarketRuntime(db=AsyncMock(), contracts=contracts)
    fetch_local_install = AsyncMock(return_value=_make_install(issue="invalid-url-local"))

    install = await runtime.resolve_install(
        platform_type="JND28WEB",
        platform_url="not-a-url",
        owner_key="100:JND28WEB",
        fetch_local_install=fetch_local_install,
    )

    assert install.issue == "invalid-url-local"
    assert getattr(install, "market_data_state", None) == PUBLIC_STATE_LOCAL_FALLBACK
    fetch_local_install.assert_awaited_once()


@pytest.mark.asyncio
async def test_collector_lifecycle_released_with_owner():
    contracts = _FakeContracts(shared_group_id=20, snapshot=None)
    runtime = SharedMarketRuntime(
        db=AsyncMock(),
        contracts=contracts,
        collector_interval_seconds=0.05,
        collector_error_backoff_seconds=0.05,
    )
    call_count = 0

    async def fetch_local_install() -> InstallInfo:
        nonlocal call_count
        call_count += 1
        return _make_install(issue=f"20260421{call_count:03d}")

    await runtime.resolve_install(
        platform_type="JND28WEB",
        platform_url="https://example.com/path",
        owner_key="100:JND28WEB",
        fetch_local_install=fetch_local_install,
    )
    await asyncio.sleep(0.12)
    assert call_count >= 2

    await runtime.release_owner("100:JND28WEB")
    stopped_at = call_count
    await asyncio.sleep(0.12)
    assert call_count == stopped_at
