from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
import app.main as main_app

from app.main import app, lifespan


async def _run_lifespan() -> tuple[AsyncMock, object]:
    mock_db = AsyncMock()
    mock_engine = AsyncMock()
    mock_engine.shared_market_runtime = AsyncMock()
    mock_engine.shared_market_runtime.ensure_enabled_collectors = AsyncMock(return_value=3)
    mock_engine.restore_workers_on_startup = AsyncMock(return_value=0)
    mock_engine.start_health_check = AsyncMock()
    mock_engine.shutdown = AsyncMock()

    with (
        patch("app.main.init_db", AsyncMock()),
        patch("app.main.get_shared_db", AsyncMock(return_value=mock_db)),
        patch("app.main.restore_sessions", AsyncMock()),
        patch("app.main.EngineManager", return_value=mock_engine),
        patch("app.main.account_platform_session_clear_locks", AsyncMock()),
        patch("app.main.init_sync_service", Mock()),
        patch("app.main.shutdown_shared_captcha_service", Mock()),
        patch("app.main.close_shared_db", AsyncMock()),
    ):
        async with lifespan(app):
            pass

    return mock_engine, mock_db


@pytest.mark.asyncio
async def test_lifespan_starts_collectors_even_when_workers_restore_disabled(monkeypatch):
    monkeypatch.setattr(main_app, "BOCAI_RESTORE_WORKERS_ON_STARTUP", False, raising=False)

    engine, _ = await _run_lifespan()

    engine.restore_workers_on_startup.assert_not_called()
    engine.shared_market_runtime.ensure_enabled_collectors.assert_awaited_once()
    engine.start_health_check.assert_awaited_once()
    engine.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifespan_restores_workers_and_starts_collectors_once(monkeypatch):
    monkeypatch.setattr(main_app, "BOCAI_RESTORE_WORKERS_ON_STARTUP", True, raising=False)

    engine, _ = await _run_lifespan()

    engine.restore_workers_on_startup.assert_awaited_once()
    engine.shared_market_runtime.ensure_enabled_collectors.assert_awaited_once()
    engine.start_health_check.assert_awaited_once()
