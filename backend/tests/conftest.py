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
