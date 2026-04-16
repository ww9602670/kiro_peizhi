""" fixtures"""
import os

import pytest
import aiosqlite

from app.database import DDL_STATEMENTS, INSERT_DEFAULT_ADMIN

# 
os.environ["BOCAI_DB_PATH"] = ":memory:"


@pytest.fixture
async def db():
    """"""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")
    for stmt in DDL_STATEMENTS:
        await conn.execute(stmt)
    await conn.execute(INSERT_DEFAULT_ADMIN)
    await conn.commit()
    yield conn
    await conn.close()


@pytest.fixture(autouse=True)
async def _ensure_shared_db(request):
    """Ensure the shared in-memory database is initialized for API tests.

    httpx ASGITransport does not trigger FastAPI lifespan events,
    so we must initialize the shared DB before any test that uses
    get_shared_db() (via API endpoints or test helpers).

    Skips for tests that manage their own DB (e.g. TestFileDB) and E2E tests.
    """
    import app.database as db_mod

    # Skip for tests that have their own file_db fixture
    if "file_db" in request.fixturenames:
        yield
        return
    
    # Skip for E2E tests (they connect to a running backend service)
    if "e2e" in request.keywords:
        yield
        return

    if db_mod._shared_db is None:
        from app.database import init_db
        await init_db(":memory:")
    yield
