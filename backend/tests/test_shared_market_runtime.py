from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.engine.adapters.base import InstallInfo, LoginResult, RemoteLoginRequired
from app.engine.shared_market_runtime import (
    DEFAULT_COLLECTOR_INTERVAL_SECONDS,
    DEFAULT_SHARED_MARKET_FRESHNESS_SECONDS,
    PUBLIC_STATE_LOCAL_FALLBACK,
    PUBLIC_STATE_PROCESSING,
    PUBLIC_STATE_SHARED_HIT,
    SharedMarketRuntime,
    SharedMarketContracts,
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
        shared_groups: list[dict] | None = None,
    ) -> None:
        self.shared_group_id = shared_group_id
        self.snapshot = snapshot
        self.shared_groups = shared_groups or []
        self.upserts: list[tuple[int, str, str, str | None]] = []
        self.snapshot_errors: list[dict] = []
        self.admin_ids = [1]
        self.admin_alerts: list[dict] = []
        self.uncovered_touches: list[dict] = []
        self.uncovered_detecting: list[int] = []
        self.uncovered_matched: list[tuple[int, int]] = []
        self.uncovered_review_required: list[tuple[int, str | None]] = []
        self.group_url_adds: list[tuple[int, str]] = []
        self.last_row_id = 0

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
        last_error: str | None = None,
    ) -> None:
        self.upserts.append((shared_group_id, install.issue, source_status, last_error))

    async def snapshot_mark_error(
        self,
        *,
        shared_group_id: int,
        last_error: str,
        install: InstallInfo | None = None,
    ) -> None:
        self.snapshot_errors.append(
            {
                "shared_group_id": shared_group_id,
                "last_error": last_error,
                "issue": install.issue if install is not None else None,
                "source_status": "error",
            }
        )

    async def snapshot_get_latest(self, *, shared_group_id: int) -> SharedMarketSnapshot | None:
        return self.snapshot

    async def uncovered_url_touch(
        self,
        *,
        platform_type: str,
        normalized_url: str,
        sample_raw_url: str | None = None,
        last_account_id: int | None = None,
        last_platform_type: str | None = None,
    ) -> dict:
        self.last_row_id += 1
        self.uncovered_touches.append(
            {
                "platform_type": platform_type,
                "normalized_url": normalized_url,
                "sample_raw_url": sample_raw_url,
                "last_account_id": last_account_id,
                "last_platform_type": last_platform_type or platform_type,
                "id": self.last_row_id,
            },
        )
        return self.uncovered_touches[-1]

    async def shared_market_group_list(self, *, include_disabled: bool = False) -> list[dict]:
        del include_disabled
        return list(self.shared_groups)

    async def shared_market_group_get(self, *, shared_group_id: int) -> dict | None:
        for row in self.shared_groups:
            if row.get("id") == shared_group_id or row.get("shared_group_id") == shared_group_id:
                return row
        return None

    async def uncovered_url_mark_detecting(self, *, row_id: int) -> None:
        self.uncovered_detecting.append(row_id)

    async def uncovered_url_mark_matched(self, *, row_id: int, shared_group_id: int) -> None:
        self.uncovered_matched.append((row_id, shared_group_id))

    async def uncovered_url_mark_review_required(
        self,
        *,
        row_id: int,
        review_status: str = "review_required",
        failure_reason: str | None = None,
    ) -> None:
        del review_status
        self.uncovered_review_required.append((row_id, failure_reason))

    async def shared_market_group_url_add(
        self,
        *,
        shared_group_id: int,
        normalized_url: str,
    ) -> None:
        self.group_url_adds.append((shared_group_id, normalized_url))

    async def active_admin_operator_ids(self) -> list[int]:
        return list(self.admin_ids)

    async def admin_alert_create(
        self,
        *,
        operator_id: int,
        title: str,
        detail: str,
    ) -> None:
        self.admin_alerts.append(
            {
                "operator_id": operator_id,
                "title": title,
                "detail": detail,
            }
        )


def test_normalize_market_url_keeps_origin_and_path():
    normalized = normalize_market_url("HTTPS://EXAMPLE.com:443/abc/def/?a=1#frag")
    assert normalized == "https://example.com:443/abc/def"


def test_shared_collector_default_interval_and_freshness_are_aligned():
    assert DEFAULT_COLLECTOR_INTERVAL_SECONDS == 25.0
    assert DEFAULT_SHARED_MARKET_FRESHNESS_SECONDS > DEFAULT_COLLECTOR_INTERVAL_SECONDS


def test_shared_snapshot_to_install_subtracts_snapshot_age():
    fetched_at = datetime(2026, 5, 1, 12, 0, 0)
    snapshot = SharedMarketSnapshot(
        shared_group_id=1,
        issue="20260501001",
        state=1,
        close_countdown_sec=41,
        open_countdown_sec=26,
        pre_issue="20260501000",
        pre_result="1,2,3",
        fetched_at=fetched_at,
        source_status="ok",
    )

    install = snapshot.to_install(now=fetched_at + timedelta(seconds=7))

    assert install.close_countdown_sec == 34
    assert install.open_countdown_sec == 19


@pytest.mark.asyncio
async def test_snapshot_get_latest_maps_open_result_to_pre_result(monkeypatch):
    async def fake_snapshot_get_latest(_db, *, shared_group_id: int) -> dict:
        return {
            "shared_group_id": shared_group_id,
            "issue": "20260430001",
            "state": 1,
            "close_countdown_sec": 35,
            "open_countdown_sec": 65,
            "pre_issue": "20260430000",
            "open_result": "9,1,1",
            "fetched_at": "2026-04-30 17:46:26",
            "source_status": "ok",
        }

    monkeypatch.setattr(
        "app.engine.shared_market_runtime.db_ops.shared_market_snapshot_get_latest",
        fake_snapshot_get_latest,
    )

    contracts = SharedMarketContracts(db=object())
    snapshot = await contracts.snapshot_get_latest(shared_group_id=1)

    assert snapshot is not None
    assert snapshot.pre_result == "9,1,1"
    assert snapshot.to_install().pre_result == "9,1,1"


@pytest.mark.asyncio
async def test_collector_marks_error_and_alerts_admin_without_last_install():
    contracts = _FakeContracts(shared_group_id=None)
    runtime = SharedMarketRuntime(
        db=AsyncMock(),
        contracts=contracts,
        collector_interval_seconds=0.5,
        collector_error_backoff_seconds=0.5,
    )
    stop_event = asyncio.Event()
    fetch_local_install = AsyncMock(side_effect=RuntimeError("session expired"))

    task = asyncio.create_task(
        runtime._collector_loop(
            shared_group_id=3,
            owner_key="shared-group-3",
            stop_event=stop_event,
            fetch_local_install=fetch_local_install,
        )
    )
    try:
        for _ in range(10):
            if contracts.snapshot_errors:
                break
            await asyncio.sleep(0.05)
    finally:
        stop_event.set()
        await asyncio.wait_for(task, timeout=1)

    assert contracts.snapshot_errors
    assert contracts.snapshot_errors[-1]["source_status"] == "error"
    assert contracts.snapshot_errors[-1]["issue"] is None
    assert "session expired" in contracts.snapshot_errors[-1]["last_error"]
    assert contracts.admin_alerts
    assert contracts.admin_alerts[-1]["title"] == "共享数据源异常"


@pytest.mark.asyncio
async def test_group_fetcher_relogs_in_once_after_remote_login_required(monkeypatch):
    class FakeAdapter:
        def __init__(self) -> None:
            self.login_calls = 0
            self.install_calls = 0

        async def login(self, account_name: str, account_password: str) -> LoginResult:
            del account_name
            del account_password
            self.login_calls += 1
            return LoginResult(success=True)

        async def get_current_install(self) -> InstallInfo:
            self.install_calls += 1
            if self.install_calls == 1:
                raise RemoteLoginRequired(raw_state=401, message="session expired")
            return _make_install(issue="after-relogin")

    adapter = FakeAdapter()

    def fake_create_platform_adapter(platform_type: str, platform_url: str | None = None):
        del platform_type
        del platform_url
        return adapter

    monkeypatch.setattr(
        "app.engine.adapters.factory.create_platform_adapter",
        fake_create_platform_adapter,
    )

    runtime = SharedMarketRuntime(db=AsyncMock(), contracts=_FakeContracts(shared_group_id=None))
    fetcher, _owner_key, _cleanup = runtime._build_group_fetcher(
        shared_group_id=11,
        shared_group={
            "collector_platform_type": "JND28WEB",
            "primary_url": "https://example.com",
            "collector_account_name": "ceshi11",
            "collector_password_enc": "Qq1122",
        },
    )

    assert fetcher is not None
    install = await fetcher()

    assert install.issue == "after-relogin"
    assert adapter.login_calls == 2
    assert adapter.install_calls == 2


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
    assert len(contracts.uncovered_touches) == 1
    touch = contracts.uncovered_touches[0]
    assert touch["platform_type"] == "JND28WEB"
    assert touch["normalized_url"] == "https://example.com/path"
    assert touch["sample_raw_url"] == "https://example.com/path"
    assert touch["last_account_id"] == 100
    assert contracts.uncovered_review_required == [(1, "no_shared_group_matched")]


@pytest.mark.asyncio
async def test_resolve_install_miss_success_joins_shared_group():
    group_row = {
        "id": 2,
        "collector_platform_type": "JND28WEB",
        "collector_account_name": "jiance11",
        "collector_password_enc": "Qq1122",
    }
    contracts = _FakeContracts(shared_group_id=None, snapshot=None, shared_groups=[group_row])
    runtime = SharedMarketRuntime(db=AsyncMock(), contracts=contracts)
    runtime._probe_with_shared_account = AsyncMock(return_value=_make_install(issue="shared-hit"))

    fetch_local_install = AsyncMock(return_value=_make_install(issue="fallback"))

    install = await runtime.resolve_install(
        platform_type="JND28WEB",
        platform_url="https://example.com/path?a=9",
        owner_key="200:JND28WEB",
        fetch_local_install=fetch_local_install,
    )

    assert install.issue == "shared-hit"
    assert getattr(install, "market_data_state", None) == PUBLIC_STATE_SHARED_HIT
    runtime._probe_with_shared_account.assert_awaited_once()
    assert contracts.uncovered_matched == [(1, 2)]
    assert contracts.group_url_adds == [(2, "https://example.com/path")]


@pytest.mark.asyncio
async def test_discovery_failure_marks_review_required():
    group_row = {
        "id": 3,
        "collector_platform_type": "JND28WEB",
        "collector_account_name": "jiance11",
        "collector_password_enc": "bad-pass",
    }
    contracts = _FakeContracts(shared_group_id=None, snapshot=None, shared_groups=[group_row])
    runtime = SharedMarketRuntime(db=AsyncMock(), contracts=contracts)
    runtime._probe_with_shared_account = AsyncMock(side_effect=RuntimeError("probe failed"))

    shared_group_id, install = await runtime.discover_and_bind_uncovered_url(
        platform_type="JND28WEB",
        platform_url="https://example.com/path?a=1",
        account_id=300,
        sample_raw_url="https://example.com/path?a=1",
    )

    assert shared_group_id is None
    assert install is None
    assert contracts.uncovered_review_required == [(1, "no_shared_group_matched")]


@pytest.mark.asyncio
async def test_discovery_with_matched_install_marks_and_returns_alias():
    group_row = {
        "id": 4,
        "collector_platform_type": "JND28WEB",
        "collector_account_name": "jiance11",
        "collector_password_enc": "Qq1122",
    }
    contracts = _FakeContracts(shared_group_id=None, snapshot=None, shared_groups=[group_row])
    runtime = SharedMarketRuntime(db=AsyncMock(), contracts=contracts)
    runtime._probe_with_shared_account = AsyncMock(return_value=_make_install(issue="20260421x1"))

    shared_group_id, install = await runtime.discover_and_bind_uncovered_url(
        platform_type="JND28WEB",
        platform_url="https://example.com/path?a=2",
        account_id=301,
        sample_raw_url="https://example.com/path?a=2",
    )

    assert shared_group_id == 4
    assert install is not None
    assert install.issue == "20260421x1"
    assert contracts.uncovered_matched == [(1, 4)]
    assert contracts.group_url_adds == [(4, "https://example.com/path")]


@pytest.mark.asyncio
async def test_discovery_uses_detector_account_not_collector(monkeypatch):
    login_calls: list[tuple[str, str]] = []

    class FakeAdapter:
        def __init__(self, platform_url: str | None) -> None:
            self.platform_url = platform_url
            self.closed = False

        async def login(self, account_name: str, password: str, captcha_code=None):
            del captcha_code
            login_calls.append((account_name, password))
            return LoginResult(success=True)

        async def get_current_install(self) -> InstallInfo:
            assert self.platform_url == "https://example.com/path"
            return _make_install(issue="detected")

        async def close(self) -> None:
            self.closed = True

    def fake_create_platform_adapter(platform_type: str, platform_url: str | None = None):
        assert platform_type == "JND28WEB"
        return FakeAdapter(platform_url)

    monkeypatch.setattr(
        "app.engine.adapters.factory.create_platform_adapter",
        fake_create_platform_adapter,
    )
    monkeypatch.setattr(
        "app.engine.shared_market_runtime.BOCAI_SHARED_DETECTOR_ACCOUNT",
        "detector-user",
    )
    monkeypatch.setattr(
        "app.engine.shared_market_runtime.BOCAI_SHARED_DETECTOR_PASSWORD",
        "detector-pass",
    )

    group_row = {
        "id": 5,
        "collector_platform_type": "JND28WEB",
        "collector_account_name": "collector-user",
        "collector_password_enc": "collector-pass",
    }
    contracts = _FakeContracts(shared_group_id=None, snapshot=None, shared_groups=[group_row])
    runtime = SharedMarketRuntime(db=AsyncMock(), contracts=contracts)

    shared_group_id, install = await runtime.discover_and_bind_uncovered_url(
        platform_type="JND28WEB",
        platform_url="https://example.com/path?a=2",
        account_id=302,
        sample_raw_url="https://example.com/path?a=2",
    )

    assert shared_group_id == 5
    assert install is not None
    assert install.issue == "detected"
    assert login_calls == [("detector-user", "detector-pass")]
    assert contracts.uncovered_matched == [(1, 5)]


@pytest.mark.asyncio
async def test_ensure_shared_login_retries_with_captcha(monkeypatch):
    adapter = AsyncMock()
    adapter.login = AsyncMock(
        side_effect=[
            LoginResult(success=False, message="captcha required"),
            LoginResult(success=True, token="token"),
        ]
    )
    adapter.get_captcha = AsyncMock(return_value=b"captcha-image")
    captcha_service = AsyncMock()
    captcha_service.recognize = AsyncMock(return_value=" 1234 ")

    monkeypatch.setattr(
        "app.engine.shared_market_runtime.get_shared_captcha_service",
        lambda: captcha_service,
    )

    runtime = SharedMarketRuntime(db=AsyncMock(), contracts=_FakeContracts(shared_group_id=None))
    await runtime._ensure_shared_login(
        adapter=adapter,
        account_name="demo-user",
        account_password="demo-pass",
    )

    assert adapter.login.await_count == 2
    assert adapter.login.await_args_list[1].args == ("demo-user", "demo-pass", "1234")
    adapter.get_captcha.assert_awaited_once()
    captcha_service.recognize.assert_awaited_once_with(b"captcha-image")


@pytest.mark.asyncio
async def test_ensure_shared_login_raises_with_reason_when_captcha_retry_fails(monkeypatch):
    adapter = AsyncMock()
    adapter.login = AsyncMock(
        side_effect=[
            LoginResult(success=False, message="captcha required", captcha_required=True),
            LoginResult(success=False, message="wrong captcha", captcha_required=True),
            LoginResult(success=False, message="wrong captcha", captcha_required=True),
            LoginResult(success=False, message="wrong captcha", captcha_required=True),
        ]
    )
    adapter.get_captcha = AsyncMock(return_value=b"captcha-image")
    captcha_service = AsyncMock()
    captcha_service.recognize = AsyncMock(return_value="bad1")

    monkeypatch.setattr(
        "app.engine.shared_market_runtime.get_shared_captcha_service",
        lambda: captcha_service,
    )

    runtime = SharedMarketRuntime(db=AsyncMock(), contracts=_FakeContracts(shared_group_id=None))

    with pytest.raises(RuntimeError, match=r"shared login failed: wrong captcha"):
        await runtime._ensure_shared_login(
            adapter=adapter,
            account_name="demo-user",
            account_password="demo-pass",
        )

    assert adapter.login.await_count == 4
    assert adapter.get_captcha.await_count == 3


@pytest.mark.asyncio
async def test_ensure_shared_login_without_get_captcha_keeps_old_failure_behavior():
    adapter = AsyncMock()
    adapter.login = AsyncMock(return_value=LoginResult(success=False, message="bad credentials"))

    runtime = SharedMarketRuntime(db=AsyncMock(), contracts=_FakeContracts(shared_group_id=None))

    with pytest.raises(RuntimeError, match=r"shared login failed: bad credentials"):
        await runtime._ensure_shared_login(
            adapter=adapter,
            account_name="demo-user",
            account_password="demo-pass",
        )


@pytest.mark.asyncio
async def test_discover_and_bind_uncovered_url_retries_captcha_login(monkeypatch):
    login_calls: list[tuple[str, str, str | None]] = []

    class FakeAdapter:
        def __init__(self) -> None:
            self.closed = False

        async def login(self, account_name: str, password: str, captcha_code=None):
            login_calls.append((account_name, password, captcha_code))
            if captcha_code is None:
                return LoginResult(success=False, message="captcha required", captcha_required=True)
            return LoginResult(success=True)

        async def get_captcha(self) -> bytes:
            return b"captcha-image"

        async def get_current_install(self) -> InstallInfo:
            return _make_install(issue="detected-captcha")

        async def close(self) -> None:
            self.closed = True

    def fake_create_platform_adapter(platform_type: str, platform_url: str | None = None):
        del platform_type
        del platform_url
        return FakeAdapter()

    monkeypatch.setattr(
        "app.engine.adapters.factory.create_platform_adapter",
        fake_create_platform_adapter,
    )
    monkeypatch.setattr(
        "app.engine.shared_market_runtime.BOCAI_SHARED_DETECTOR_ACCOUNT",
        "jiance11",
    )
    monkeypatch.setattr(
        "app.engine.shared_market_runtime.BOCAI_SHARED_DETECTOR_PASSWORD",
        "detector-pass",
    )
    captcha_service = AsyncMock()
    captcha_service.recognize = AsyncMock(return_value="4321")
    monkeypatch.setattr(
        "app.engine.shared_market_runtime.get_shared_captcha_service",
        lambda: captcha_service,
    )

    group_row = {
        "id": 12,
        "collector_platform_type": "JND28WEB",
        "collector_account_name": "jiance11",
        "collector_password_enc": "Qq1122",
    }
    contracts = _FakeContracts(shared_group_id=None, snapshot=None, shared_groups=[group_row])
    runtime = SharedMarketRuntime(db=AsyncMock(), contracts=contracts)

    shared_group_id, install = await runtime.discover_and_bind_uncovered_url(
        platform_type="JND28WEB",
        platform_url="https://example.com/path?a=1",
        account_id=123,
        sample_raw_url="https://example.com/path?a=1",
    )

    assert shared_group_id == 12
    assert install is not None
    assert install.issue == "detected-captcha"
    assert login_calls == [("jiance11", "detector-pass", None), ("jiance11", "detector-pass", "4321")]
    assert contracts.uncovered_matched == [(1, 12)]
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
async def test_shared_market_runtime_starts_collectors_for_enabled_groups():
    groups = [
        {
            "id": 7,
            "collector_platform_type": "JND28WEB",
            "collector_account_name": "collector-7",
            "collector_password_enc": "pwd7",
        },
        {
            "id": 8,
            "collector_platform_type": "JND28WEB",
            "collector_account_name": "ceshi11",
            "collector_password_enc": "Jq2233",
        },
    ]
    contracts = _FakeContracts(shared_group_id=None, shared_groups=groups)
    runtime = SharedMarketRuntime(db=AsyncMock(), contracts=contracts)
    runtime._ensure_collector = AsyncMock()

    started = await runtime.ensure_enabled_collectors()

    assert started == 2
    assert runtime._ensure_collector.await_count == 2


@pytest.mark.asyncio
async def test_shared_market_runtime_skips_detector_account_collectors():
    groups = [
        {
            "id": 9,
            "collector_platform_type": "JND28WEB",
            "collector_account_name": "jiance11",
            "collector_password_enc": "Qq1122",
        },
        {
            "id": 10,
            "collector_platform_type": "JND28WEB",
            "collector_account_name": "ceshi11",
            "collector_password_enc": "Jq2233",
        },
    ]
    contracts = _FakeContracts(shared_group_id=None, shared_groups=groups)
    runtime = SharedMarketRuntime(db=AsyncMock(), contracts=contracts)
    runtime._ensure_collector = AsyncMock()

    started = await runtime.ensure_enabled_collectors()

    assert started == 1
    assert runtime._ensure_collector.await_count == 1
    runtime._ensure_collector.assert_awaited_once()
    assert runtime._ensure_collector.await_args.kwargs["shared_group_id"] == 10


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
