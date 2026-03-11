"""Phase 4.5.3  AlertService 


1. send()  alerts operator_id, type, level, title, detail
2. send()  True
3. send() 5  False operator_id + alert_type + account_id
4. send() 5  Truemock time
5.  alert_type  account_id 
6.  alert_type  warning 
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import aiosqlite

from app.database import DDL_STATEMENTS, INSERT_DEFAULT_ADMIN
from app.models.db_ops import operator_create
from app.engine.alert import AlertService, ALERT_LEVEL_MAP, _DEDUP_WINDOW


@pytest.fixture
async def db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")
    for stmt in DDL_STATEMENTS:
        await conn.execute(stmt)
    await conn.execute(INSERT_DEFAULT_ADMIN)
    await conn.commit()
    yield conn
    await conn.close()


@pytest.fixture
async def operator(db):
    op = await operator_create(db, username="test_op", password="pass123", created_by=1)
    return op


@pytest.fixture
def alert_service(db):
    return AlertService(db)


# 
# 1. send()  DB 
# 

@pytest.mark.asyncio
async def test_send_writes_to_db(db, operator, alert_service):
    """send()  alerts """
    result = await alert_service.send(
        operator_id=operator["id"],
        alert_type="login_fail",
        title="",
        detail="",
    )
    assert result is True

    row = await (await db.execute(
        "SELECT * FROM alerts WHERE operator_id=?", (operator["id"],)
    )).fetchone()
    assert row is not None
    assert row["operator_id"] == operator["id"]
    assert row["type"] == "login_fail"
    assert row["level"] == "critical"  # login_fail  critical
    assert row["title"] == ""
    assert row["detail"] == ""
    assert row["is_read"] == 0


@pytest.mark.asyncio
async def test_send_returns_true_on_first_call(operator, alert_service):
    """ True"""
    result = await alert_service.send(
        operator_id=operator["id"],
        alert_type="bet_fail",
        title="",
    )
    assert result is True


# 
# 2. /
# 

@pytest.mark.asyncio
async def test_dedup_within_5_minutes(db, operator, alert_service):
    """5  1 """
    r1 = await alert_service.send(
        operator_id=operator["id"],
        alert_type="login_fail",
        title=" #1",
    )
    r2 = await alert_service.send(
        operator_id=operator["id"],
        alert_type="login_fail",
        title=" #2",
    )
    assert r1 is True
    assert r2 is False

    rows = await (await db.execute(
        "SELECT * FROM alerts WHERE operator_id=?", (operator["id"],)
    )).fetchall()
    assert len(rows) == 1
    assert rows[0]["title"] == " #1"


@pytest.mark.asyncio
async def test_dedup_with_account_id(db, operator, alert_service):
    """ account_id """
    r1 = await alert_service.send(
        operator_id=operator["id"],
        alert_type="bet_fail",
        title=" #1",
        account_id=100,
    )
    r2 = await alert_service.send(
        operator_id=operator["id"],
        alert_type="bet_fail",
        title=" #2",
        account_id=100,
    )
    assert r1 is True
    assert r2 is False

    rows = await (await db.execute(
        "SELECT * FROM alerts WHERE operator_id=?", (operator["id"],)
    )).fetchall()
    assert len(rows) == 1


# 
# 3. 5 
# 

@pytest.mark.asyncio
async def test_send_after_5_minutes(db, operator, alert_service):
    """5  True"""
    base_time = 1000000.0

    with patch("app.engine.alert.time.time", return_value=base_time):
        r1 = await alert_service.send(
            operator_id=operator["id"],
            alert_type="login_fail",
            title=" #1",
        )
    assert r1 is True

    # 4 
    with patch("app.engine.alert.time.time", return_value=base_time + 240):
        r2 = await alert_service.send(
            operator_id=operator["id"],
            alert_type="login_fail",
            title=" #2",
        )
    assert r2 is False

    # 5 
    with patch("app.engine.alert.time.time", return_value=base_time + _DEDUP_WINDOW):
        r3 = await alert_service.send(
            operator_id=operator["id"],
            alert_type="login_fail",
            title=" #3",
        )
    assert r3 is True

    rows = await (await db.execute(
        "SELECT * FROM alerts WHERE operator_id=? ORDER BY id", (operator["id"],)
    )).fetchall()
    assert len(rows) == 2


# 
# 4.  alert_type / account_id 
# 

@pytest.mark.asyncio
async def test_different_alert_type_not_deduped(db, operator, alert_service):
    """ alert_type """
    r1 = await alert_service.send(
        operator_id=operator["id"],
        alert_type="login_fail",
        title="",
    )
    r2 = await alert_service.send(
        operator_id=operator["id"],
        alert_type="bet_fail",
        title="",
    )
    assert r1 is True
    assert r2 is True

    rows = await (await db.execute(
        "SELECT * FROM alerts WHERE operator_id=?", (operator["id"],)
    )).fetchall()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_different_account_id_not_deduped(db, operator, alert_service):
    """ account_id """
    r1 = await alert_service.send(
        operator_id=operator["id"],
        alert_type="bet_fail",
        title=" A",
        account_id=100,
    )
    r2 = await alert_service.send(
        operator_id=operator["id"],
        alert_type="bet_fail",
        title=" B",
        account_id=200,
    )
    assert r1 is True
    assert r2 is True

    rows = await (await db.execute(
        "SELECT * FROM alerts WHERE operator_id=?", (operator["id"],)
    )).fetchall()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_none_vs_int_account_id_not_deduped(db, operator, alert_service):
    """account_id=None  account_id=100  key"""
    r1 = await alert_service.send(
        operator_id=operator["id"],
        alert_type="bet_fail",
        title="",
        account_id=None,
    )
    r2 = await alert_service.send(
        operator_id=operator["id"],
        alert_type="bet_fail",
        title="100",
        account_id=100,
    )
    assert r1 is True
    assert r2 is True


# 
# 5. ALERT_LEVEL_MAP 
# 

@pytest.mark.asyncio
async def test_level_mapping(db, operator, alert_service):
    """ alert_type  level"""
    for alert_type, expected_level in ALERT_LEVEL_MAP.items():
        # 
        alert_service._dedup_cache.clear()
        await alert_service.send(
            operator_id=operator["id"],
            alert_type=alert_type,
            title=f" {alert_type}",
        )

    rows = await (await db.execute(
        "SELECT type, level FROM alerts WHERE operator_id=? ORDER BY id",
        (operator["id"],),
    )).fetchall()

    for row in rows:
        assert row["level"] == ALERT_LEVEL_MAP[row["type"]]


@pytest.mark.asyncio
async def test_unknown_alert_type_defaults_to_warning(db, operator, alert_service):
    """ alert_type  warning """
    result = await alert_service.send(
        operator_id=operator["id"],
        alert_type="unknown_type_xyz",
        title="",
    )
    assert result is True

    row = await (await db.execute(
        "SELECT * FROM alerts WHERE type='unknown_type_xyz'"
    )).fetchone()
    assert row is not None
    assert row["level"] == "warning"
