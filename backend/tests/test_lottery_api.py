from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, Request

from app.api import lottery as lottery_api
from app.api.lottery import (
    _build_current_install_response,
    _default_current_install_response,
    _resolve_worker_snapshot,
    _select_running_adapter,
    get_current_install,
)
from app.engine.adapters.base import InstallInfo
from app.engine.shared_market_runtime import SharedMarketSnapshot


def test_current_install_response_clamps_negative_countdowns():
    response = _build_current_install_response(
        {
            "installments": "3397193",
            "state": "1",
            "close_countdown_sec": -47,
            "open_countdown_sec": "-9",
            "pre_lottery_result": "1,2,3",
            "pre_installments": "3397192",
            "template_code": "JNDPCDD",
        }
    )

    assert response.installments == "3397193"
    assert response.state == 1
    assert response.close_countdown_sec == 0
    assert response.open_countdown_sec == 0
    assert response.market_data_state == "local_fallback"


def test_default_current_install_response_is_frontend_safe():
    response = _default_current_install_response()

    assert response.installments == ""
    assert response.state == 0
    assert response.close_countdown_sec == 0
    assert response.open_countdown_sec == 0
    assert response.pre_lottery_result == ""
    assert response.pre_installments == ""
    assert response.template_code == ""
    assert response.market_data_state == "local_fallback"


def test_current_install_response_supports_legacy_field_mapping():
    response = _build_current_install_response(
        {
            "issue": 3397193,
            "state": 9,
            "close_timestamp": "37",
            "open_timestamp": 45,
            "pre_issue": 3397192,
            "pre_result": None,
            "templateCode": "PCDD",
        }
    )

    assert response.installments == "3397193"
    assert response.state == 0
    assert response.close_countdown_sec == 37
    assert response.open_countdown_sec == 45
    assert response.pre_installments == "3397192"
    assert response.pre_lottery_result == ""
    assert response.template_code == "PCDD"
    assert response.market_data_state == "local_fallback"


def test_current_install_response_supports_market_data_state_mapping():
    response = _build_current_install_response(
        {
            "installments": "3397193",
            "state": "1",
            "shared_market_state": "shared_hit",
        },
        default_market_data_state="processing",
    )

    assert response.market_data_state == "shared_hit"


def test_current_install_response_uses_fallback_market_data_state_on_invalid_value():
    response = _build_current_install_response(
        {
            "installments": "3397193",
            "state": "1",
            "market_data_state": "unknown_state",
        },
        default_market_data_state="processing",
    )

    assert response.market_data_state == "processing"


def test_select_running_adapter_prefers_requested_platform_for_operator():
    expected = object()
    fallback = object()
    workers = {
        (10, "JND282"): SimpleNamespace(
            operator_id=7, running=True, adapter=fallback, platform_type="JND282"
        ),
        (10, "JND28WEB"): SimpleNamespace(
            operator_id=7, running=True, adapter=expected, platform_type="JND28WEB"
        ),
    }

    selected = _select_running_adapter(
        workers,
        operator={"id": 7, "role": "operator"},
        requested_platform_type="JND28WEB",
    )

    assert selected is expected


def test_select_running_adapter_falls_back_to_operator_worker():
    fallback = object()
    workers = {
        (10, "JND282"): SimpleNamespace(
            operator_id=7, running=True, adapter=fallback, platform_type="JND282"
        )
    }

    selected = _select_running_adapter(
        workers,
        operator={"id": 7, "role": "operator"},
        requested_platform_type="JND28WEB",
    )

    assert selected is fallback


def test_select_running_adapter_allows_admin_cross_operator_fallback():
    cross_operator_adapter = object()
    workers = {
        (11, "JND282"): SimpleNamespace(
            operator_id=99,
            running=True,
            adapter=cross_operator_adapter,
            platform_type="JND282",
        )
    }

    selected = _select_running_adapter(
        workers,
        operator={"id": 1, "role": "admin"},
        requested_platform_type="JND282",
    )

    assert selected is cross_operator_adapter


def test_resolve_worker_snapshot_uses_poller_last_install():
    worker = SimpleNamespace(
        poller=SimpleNamespace(
            last_install=InstallInfo(
                issue="3424001",
                state=2,
                close_countdown_sec=0,
                open_countdown_sec=18,
                pre_issue="3424000",
                pre_result="1,2,3",
            )
        )
    )

    snapshot = _resolve_worker_snapshot(worker)

    assert snapshot == {
        "installments": "3424001",
        "state": 2,
        "close_countdown_sec": 0,
        "open_countdown_sec": 18,
        "pre_lottery_result": "1,2,3",
        "pre_installments": "3424000",
        "template_code": "",
    }


@pytest.mark.asyncio
async def test_get_current_install_reads_cached_snapshot_without_live_adapter_call():
    adapter = SimpleNamespace()

    async def _boom():
        raise AssertionError("live adapter fetch should not be used by page API")

    adapter.get_current_install_detail = _boom

    worker = SimpleNamespace(
        running=True,
        operator_id=7,
        platform_type="JND282",
        adapter=adapter,
        poller=SimpleNamespace(
            last_install=InstallInfo(
                issue="3424002",
                state=1,
                close_countdown_sec=33,
                open_countdown_sec=45,
                pre_issue="3424001",
                pre_result="4,5,6",
            ),
            shared_market_state="shared_hit",
        ),
    )

    class _Registry:
        async def all_workers(self):
            return {(10, "JND282"): worker}

    app = FastAPI()
    app.state.engine = SimpleNamespace(registry=_Registry())
    request = Request({"type": "http", "app": app})

    response = await get_current_install(
        request=request,
        platform_type="JND282",
        operator={"id": 7, "role": "operator"},
        db=object(),
    )

    assert response.data.installments == "3424002"
    assert response.data.close_countdown_sec == 33
    assert response.data.open_countdown_sec == 45
    assert response.data.pre_installments == "3424001"
    assert response.data.pre_lottery_result == "4,5,6"
    assert response.data.market_data_state == "shared_hit"


@pytest.mark.asyncio
async def test_get_current_install_uses_shared_snapshot_without_running_worker(monkeypatch):
    async def _fake_accounts(_db, *, operator_id: int):
        assert operator_id == 7
        return [
            {
                "id": 101,
                "game_type": "JND28",
                "platform_url": "https://pool.example.com",
                "allowed_strategy_platform_types": ["JND282"],
                "verification_stale": False,
            }
        ]

    async def _fake_sessions(_db, *, account_id: int):
        assert account_id == 101
        return [
            {
                "id": 1,
                "platform_type": "JND282",
                "status": "online",
                "session_token": "session-token",
            }
        ]

    monkeypatch.setattr(lottery_api, "account_list_by_operator", _fake_accounts)
    monkeypatch.setattr(lottery_api, "account_platform_session_list", _fake_sessions)
    monkeypatch.setattr(
        lottery_api,
        "create_platform_adapter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("shared snapshot hit should not build a live adapter")
        ),
    )

    now = datetime.now()
    fake_contracts = SimpleNamespace(
        group_resolve_by_url=AsyncMock(return_value=88),
        snapshot_get_latest=AsyncMock(
            return_value=SharedMarketSnapshot(
                shared_group_id=88,
                issue="3425001",
                state=1,
                close_countdown_sec=12,
                open_countdown_sec=24,
                pre_issue="3425000",
                pre_result="2,2,2",
                fetched_at=now - timedelta(seconds=2),
                source_status="ok",
            )
        ),
        uncovered_url_touch=AsyncMock(),
        snapshot_upsert=AsyncMock(),
    )

    class _Registry:
        async def all_workers(self):
            return {}

    app = FastAPI()
    app.state.engine = SimpleNamespace(
        registry=_Registry(),
        shared_market_runtime=SimpleNamespace(
            _contracts=fake_contracts,
            _freshness_seconds=15,
        ),
    )
    request = Request({"type": "http", "app": app})

    response = await get_current_install(
        request=request,
        platform_type="JND282",
        operator={"id": 7, "role": "operator"},
        db=object(),
    )

    assert response.data.installments == "3425001"
    assert response.data.close_countdown_sec == 12
    assert response.data.pre_installments == "3425000"
    assert response.data.pre_lottery_result == "2,2,2"
    assert response.data.market_data_state == "shared_hit"


@pytest.mark.asyncio
async def test_get_current_install_uses_operator_session_when_no_worker_exists(monkeypatch):
    async def _fake_accounts(_db, *, operator_id: int):
        assert operator_id == 7
        return [
            {
                "id": 202,
                "game_type": "JND28",
                "platform_url": "https://solo.example.com",
                "allowed_strategy_platform_types": ["JND282"],
                "verification_stale": False,
            }
        ]

    async def _fake_sessions(_db, *, account_id: int):
        assert account_id == 202
        return [
            {
                "id": 2,
                "platform_type": "JND282",
                "status": "online",
                "session_token": "session-token-202",
            }
        ]

    class _CookieJar:
        def __init__(self) -> None:
            self.update_cookies = Mock()

    class _FakeAdapter:
        def __init__(self) -> None:
            self.base_url = "https://solo.example.com"
            self._token = None
            self.cookie_jar = _CookieJar()

        async def _ensure_session(self):
            return SimpleNamespace(cookie_jar=self.cookie_jar)

        async def get_current_install(self):
            return InstallInfo(
                issue="3426002",
                state=1,
                close_countdown_sec=21,
                open_countdown_sec=35,
                pre_issue="3426001",
                pre_result="3,4,5",
            )

        async def close(self):
            return None

    fake_adapter = _FakeAdapter()
    fake_contracts = SimpleNamespace(
        group_resolve_by_url=AsyncMock(return_value=None),
        snapshot_get_latest=AsyncMock(),
        uncovered_url_touch=AsyncMock(),
        snapshot_upsert=AsyncMock(),
    )

    monkeypatch.setattr(lottery_api, "account_list_by_operator", _fake_accounts)
    monkeypatch.setattr(lottery_api, "account_platform_session_list", _fake_sessions)
    monkeypatch.setattr(
        lottery_api,
        "create_platform_adapter",
        lambda *_args, **_kwargs: fake_adapter,
    )

    class _Registry:
        async def all_workers(self):
            return {}

    app = FastAPI()
    app.state.engine = SimpleNamespace(
        registry=_Registry(),
        shared_market_runtime=SimpleNamespace(
            _contracts=fake_contracts,
            _freshness_seconds=15,
        ),
    )
    request = Request({"type": "http", "app": app})

    response = await get_current_install(
        request=request,
        platform_type="JND282",
        operator={"id": 7, "role": "operator"},
        db=object(),
    )

    assert response.data.installments == "3426002"
    assert response.data.close_countdown_sec == 21
    assert response.data.open_countdown_sec == 35
    assert response.data.pre_installments == "3426001"
    assert response.data.pre_lottery_result == "3,4,5"
    assert response.data.market_data_state == "local_fallback"
    fake_adapter.cookie_jar.update_cookies.assert_called()
