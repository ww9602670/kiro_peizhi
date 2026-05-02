import hashlib
import uuid
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import odds as odds_api
from app.database import get_shared_db
from app.engine.adapters.base import InstallInfo, LoginResult
from app.main import app
from app.models.db_ops import (
    account_create,
    account_verification_run_complete,
    account_verification_run_create,
    odds_batch_upsert,
    operator_create,
)
from app.utils.auth import create_token, persist_jti, register_session

ODDS_ENDPOINTS = [
    ("get", "/api/v1/accounts/{account_id}/odds"),
    ("post", "/api/v1/accounts/{account_id}/odds/confirm"),
    ("post", "/api/v1/accounts/{account_id}/odds/refresh"),
]


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _create_operator_token(username: str) -> tuple[str, int]:
    db = await get_shared_db()
    op = await operator_create(
        db,
        username=username,
        password="pass123456",
        max_accounts=3,
        created_by=1,
    )
    token, jti, _ = create_token(op["id"], "operator")
    register_session(op["id"], jti)
    await persist_jti(db, op["id"], jti)
    return token, op["id"]


async def _create_account_with_verification(
    *,
    operator_id: int,
    account_name: str,
    supported_platforms: list[str] | None,
    stale: bool = False,
) -> int:
    db = await get_shared_db()
    account = await account_create(
        db,
        operator_id=operator_id,
        account_name=account_name,
        password="accpass",
        game_type="JND28",
        platform_url="https://jnd.example.com",
    )
    if supported_platforms is not None:
        run = await account_verification_run_create(
            db,
            account_id=account["id"],
            snapshot_game_type="JND28",
            snapshot_platform_url="https://jnd.example.com",
            snapshot_password_hash=hashlib.sha256("accpass".encode("utf-8")).hexdigest(),
        )
        await account_verification_run_complete(
            db,
            verification_run_id=run["id"],
            capabilities=[
                {
                    "platform_type": platform_type,
                    "verify_status": "supported",
                    "market_state": "open",
                    "detected_issue": None,
                    "last_verified_at": "2026-04-19 00:00:00",
                    "odds_synced": True,
                    "odds_count": 1,
                    "odds_message": "ok",
                    "last_error": None,
                }
                for platform_type in supported_platforms
            ],
        )
        if stale:
            await db.execute(
                "UPDATE account_verification_runs "
                "SET stale=1, stale_reason='test_stale' WHERE id=?",
                (run["id"],),
            )
            await db.commit()
    return int(account["id"])


async def _call_odds_endpoint(
    client: AsyncClient,
    *,
    method: str,
    path_template: str,
    account_id: int,
    token: str,
    platform_type: str,
):
    path = path_template.format(account_id=account_id)
    headers = {"Authorization": f"Bearer {token}"}
    if method == "get":
        return await client.get(path, headers=headers, params={"platform_type": platform_type})
    return await client.post(path, headers=headers, params={"platform_type": platform_type})


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path_template"), ODDS_ENDPOINTS)
async def test_odds_endpoints_reject_without_effective_verification_run(
    client,
    method: str,
    path_template: str,
):
    uid = _uid()
    token, operator_id = await _create_operator_token(f"odds_noverify_{uid}")
    account_id = await _create_account_with_verification(
        operator_id=operator_id,
        account_name=f"acc_noverify_{uid}",
        supported_platforms=None,
    )

    resp = await _call_odds_endpoint(
        client,
        method=method,
        path_template=path_template,
        account_id=account_id,
        token=token,
        platform_type="JND28WEB",
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 1002
    assert "effective_verification_run_id" in body["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path_template"), ODDS_ENDPOINTS)
async def test_odds_endpoints_reject_when_verification_stale(
    client,
    method: str,
    path_template: str,
):
    uid = _uid()
    token, operator_id = await _create_operator_token(f"odds_stale_{uid}")
    account_id = await _create_account_with_verification(
        operator_id=operator_id,
        account_name=f"acc_stale_{uid}",
        supported_platforms=["JND28WEB"],
        stale=True,
    )

    resp = await _call_odds_endpoint(
        client,
        method=method,
        path_template=path_template,
        account_id=account_id,
        token=token,
        platform_type="JND28WEB",
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 1002
    assert "verification_stale" in body["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path_template"), ODDS_ENDPOINTS)
async def test_odds_endpoints_reject_platform_not_in_effective_verification(
    client,
    method: str,
    path_template: str,
):
    uid = _uid()
    token, operator_id = await _create_operator_token(f"odds_notallowed_{uid}")
    account_id = await _create_account_with_verification(
        operator_id=operator_id,
        account_name=f"acc_notallowed_{uid}",
        supported_platforms=["JND28WEB"],
    )

    resp = await _call_odds_endpoint(
        client,
        method=method,
        path_template=path_template,
        account_id=account_id,
        token=token,
        platform_type="JND282",
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 1002
    assert "platform_type=JND282" in body["message"]
    assert "effective_verification_run_id" in body["message"]


@pytest.mark.asyncio
async def test_list_and_confirm_odds_with_verified_platform(client):
    uid = _uid()
    token, operator_id = await _create_operator_token(f"odds_list_confirm_{uid}")
    account_id = await _create_account_with_verification(
        operator_id=operator_id,
        account_name=f"acc_list_confirm_{uid}",
        supported_platforms=["JND28WEB"],
    )
    db = await get_shared_db()
    await odds_batch_upsert(
        db,
        account_id=account_id,
        platform_type="JND28WEB",
        odds_map={"DX1": 20530, "DS3": 19840},
        confirmed=False,
    )

    headers = {"Authorization": f"Bearer {token}"}
    list_resp = await client.get(
        f"/api/v1/accounts/{account_id}/odds",
        headers=headers,
        params={"platform_type": "JND28WEB"},
    )
    assert list_resp.status_code == 200
    list_body = list_resp.json()
    assert list_body["code"] == 0
    assert list_body["data"]["platform_type"] == "JND28WEB"
    assert len(list_body["data"]["items"]) == 2
    assert list_body["data"]["has_unconfirmed"] is False

    confirm_resp = await client.post(
        f"/api/v1/accounts/{account_id}/odds/confirm",
        headers=headers,
        params={"platform_type": "JND28WEB"},
    )
    assert confirm_resp.status_code == 200
    confirm_body = confirm_resp.json()
    assert confirm_body["code"] == 0
    assert confirm_body["data"]["confirmed_count"] == 2

    list_after_confirm = await client.get(
        f"/api/v1/accounts/{account_id}/odds",
        headers=headers,
        params={"platform_type": "JND28WEB"},
    )
    assert list_after_confirm.status_code == 200
    after_body = list_after_confirm.json()["data"]
    assert after_body["has_unconfirmed"] is False
    assert all(item["confirmed"] is True for item in after_body["items"])


@pytest.mark.asyncio
async def test_refresh_odds_with_verified_platform(client, monkeypatch):
    uid = _uid()
    token, operator_id = await _create_operator_token(f"odds_refresh_{uid}")
    account_id = await _create_account_with_verification(
        operator_id=operator_id,
        account_name=f"acc_refresh_{uid}",
        supported_platforms=["JND28WEB"],
    )

    class FakeAdapter:
        async def get_current_install(self):
            return InstallInfo(
                issue="3403606",
                state=1,
                close_countdown_sec=30,
                pre_issue="3403605",
                pre_result="1,2,3",
                open_countdown_sec=40,
            )

        async def load_odds(self, issue):
            _ = issue
            return {"DX1": 20530, "DS3": 19840, "TMBS5": 0}

        async def close(self):
            return None

    monkeypatch.setattr(
        odds_api,
        "create_platform_adapter",
        lambda platform_type, platform_url=None: FakeAdapter(),
    )
    monkeypatch.setattr(
        odds_api,
        "_login_platform_account",
        AsyncMock(return_value=LoginResult(success=True, token="odds-token")),
    )
    sync_mock = AsyncMock()
    monkeypatch.setattr(odds_api, "_sync_odds", sync_mock)

    resp = await client.post(
        f"/api/v1/accounts/{account_id}/odds/refresh",
        headers={"Authorization": f"Bearer {token}"},
        params={"platform_type": "JND28WEB"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["platform_type"] == "JND28WEB"
    assert body["data"]["odds_synced"] is True
    assert body["data"]["odds_count"] == 2
    assert "odds synced: 2 items" in body["data"]["odds_message"]
    assert sync_mock.await_count == 1


@pytest.mark.asyncio
async def test_refresh_odds_reuses_running_session_runtime(client, monkeypatch):
    uid = _uid()
    token, operator_id = await _create_operator_token(f"odds_runtime_{uid}")
    account_id = await _create_account_with_verification(
        operator_id=operator_id,
        account_name=f"acc_runtime_{uid}",
        supported_platforms=["JND28WEB"],
    )

    class RuntimeAdapter:
        async def get_current_install(self):
            return InstallInfo(
                issue="3403606",
                state=1,
                close_countdown_sec=30,
                pre_issue="3403605",
                pre_result="1,2,3",
                open_countdown_sec=40,
            )

        async def load_odds(self, issue):
            _ = issue
            return {"DX1": 20530, "DS3": 19840}

    class RuntimeStub:
        def __init__(self):
            self.adapter = RuntimeAdapter()
            self.ensure_logged_in = AsyncMock(return_value=True)

        async def run_platform_call(self, _reason, func):
            return await func()

    runtime = RuntimeStub()
    engine_stub = SimpleNamespace(
        get_runtime_for_account=AsyncMock(return_value=runtime),
    )
    monkeypatch.setattr(app.state, "engine", engine_stub, raising=False)
    monkeypatch.setattr(
        odds_api,
        "_login_platform_account",
        AsyncMock(side_effect=AssertionError("runtime reuse should avoid relogin")),
    )
    monkeypatch.setattr(
        odds_api,
        "create_platform_adapter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("runtime reuse should avoid new adapter")),
    )
    sync_mock = AsyncMock()
    monkeypatch.setattr(odds_api, "_sync_odds", sync_mock)

    resp = await client.post(
        f"/api/v1/accounts/{account_id}/odds/refresh",
        headers={"Authorization": f"Bearer {token}"},
        params={"platform_type": "JND28WEB"},
    )

    assert resp.status_code == 200
    assert runtime.ensure_logged_in.await_count == 1
    assert sync_mock.await_count == 1
