"""Phase 5.2.2  RateLimiter 


1.  API 
2. 
3.    2 
4. Confirmbet mock 
5. API  10s 15s
6. LIMITS 
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.engine.rate_limiter import (
    BET_API,
    READ_APIS,
    RateLimiter,
)


# 
# Fixtures
# 

@pytest.fixture
def limiter() -> RateLimiter:
    return RateLimiter()


# 
# 1. LIMITS 
# 

def test_limits_config():
    """LIMITS  API """
    limits = RateLimiter.LIMITS
    assert limits["GetCurrentInstall"] == 5
    assert limits["Loaddata"] == 3
    assert limits["Confirmbet"] is None
    assert limits["QueryResult"] == 10
    assert limits["Topbetlist"] == 10
    assert limits["Online"] == 75


def test_timeout_config():
    """ 10s 15s"""
    limiter = RateLimiter()
    assert limiter.get_timeout("Confirmbet") == 15.0
    assert limiter.get_timeout("GetCurrentInstall") == 10.0
    assert limiter.get_timeout("Loaddata") == 10.0
    assert limiter.get_timeout("QueryResult") == 10.0
    assert limiter.get_timeout("Topbetlist") == 10.0
    assert limiter.get_timeout("Online") == 10.0


def test_read_apis_set():
    """READ_APIS  API"""
    assert READ_APIS == {
        "GetCurrentInstall", "Loaddata", "QueryResult", "Topbetlist", "Online"
    }
    assert BET_API == "Confirmbet"


def test_retryable_classification(limiter: RateLimiter):
    """Confirmbet """
    for api in READ_APIS:
        assert limiter.is_retryable(api) is True, f"{api} "
    assert limiter.is_retryable("Confirmbet") is False


# 
# 2. 
# 

@pytest.mark.asyncio
async def test_acquire_rate_limiting(limiter: RateLimiter):
    """ acquire  API limit """
    api_name = "Loaddata"  # 3s 
    limit = RateLimiter.LIMITS[api_name]

    start = time.monotonic()
    await limiter.acquire(api_name)
    await limiter.acquire(api_name)
    elapsed = time.monotonic() - start

    #  3  0.5s 
    assert elapsed >= limit - 0.5, f" {limit}s {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_acquire_no_limit_for_confirmbet(limiter: RateLimiter):
    """Confirmbet  LIMITS  Noneacquire """
    start = time.monotonic()
    await limiter.acquire("Confirmbet")
    await limiter.acquire("Confirmbet")
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, "Confirmbet "


@pytest.mark.asyncio
async def test_acquire_unknown_api_no_limit(limiter: RateLimiter):
    """ API  LIMITS acquire """
    start = time.monotonic()
    await limiter.acquire("UnknownApi")
    await limiter.acquire("UnknownApi")
    elapsed = time.monotonic() - start

    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_different_apis_independent(limiter: RateLimiter):
    """ API """
    await limiter.acquire("Loaddata")
    #  API 
    start = time.monotonic()
    await limiter.acquire("QueryResult")
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, " API "


# 
# 3. 
# 

@pytest.mark.asyncio
async def test_queue_no_discard(limiter: RateLimiter):
    """"""
    call_count = 0

    async def mock_call():
        nonlocal call_count
        call_count += 1
        return call_count

    #  API 
    #  Loaddata  0.1s 
    original_limits = RateLimiter.LIMITS.copy()
    RateLimiter.LIMITS["Loaddata"] = 0.1

    try:
        tasks = [
            limiter.execute("Loaddata", mock_call)
            for _ in range(5)
        ]
        results = await asyncio.gather(*tasks)
        assert len(results) == 5, " 5 "
        assert call_count == 5, "mock_call  5 "
    finally:
        RateLimiter.LIMITS.update(original_limits)


# 
# 4.    2 
# 

@pytest.mark.asyncio
async def test_read_api_retries_on_timeout(limiter: RateLimiter):
    """ 2  3 """
    call_count = 0

    async def always_timeout():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(999)  # 

    # 
    original_timeout = RateLimiter.DEFAULT_TIMEOUT
    original_retry_interval = RateLimiter.RETRY_INTERVAL
    RateLimiter.DEFAULT_TIMEOUT = 0.05
    RateLimiter.RETRY_INTERVAL = 0.01

    try:
        with pytest.raises(asyncio.TimeoutError):
            await limiter.execute("GetCurrentInstall", always_timeout)

        # 1  + 2  = 3 
        assert call_count == 3, (
            f" 3 1+2 {call_count} "
        )
    finally:
        RateLimiter.DEFAULT_TIMEOUT = original_timeout
        RateLimiter.RETRY_INTERVAL = original_retry_interval


@pytest.mark.asyncio
async def test_read_api_no_third_retry(limiter: RateLimiter):
    """ 3  2 """
    call_count = 0

    async def always_timeout():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(999)

    original_timeout = RateLimiter.DEFAULT_TIMEOUT
    original_retry_interval = RateLimiter.RETRY_INTERVAL
    RateLimiter.DEFAULT_TIMEOUT = 0.05
    RateLimiter.RETRY_INTERVAL = 0.01

    try:
        with pytest.raises(asyncio.TimeoutError):
            await limiter.execute("Loaddata", always_timeout)

        #  3 
        assert call_count == 3, (
            f" 4  {call_count} "
        )
    finally:
        RateLimiter.DEFAULT_TIMEOUT = original_timeout
        RateLimiter.RETRY_INTERVAL = original_retry_interval


@pytest.mark.asyncio
async def test_read_api_succeeds_on_retry(limiter: RateLimiter):
    """"""
    call_count = 0

    async def succeed_on_second():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await asyncio.sleep(999)  # 
        return "success"

    original_timeout = RateLimiter.DEFAULT_TIMEOUT
    original_retry_interval = RateLimiter.RETRY_INTERVAL
    RateLimiter.DEFAULT_TIMEOUT = 0.05
    RateLimiter.RETRY_INTERVAL = 0.01

    try:
        result = await limiter.execute("QueryResult", succeed_on_second)
        assert result == "success"
        assert call_count == 2, " 2 "
    finally:
        RateLimiter.DEFAULT_TIMEOUT = original_timeout
        RateLimiter.RETRY_INTERVAL = original_retry_interval


# 
# 5. Confirmbet 
# 

@pytest.mark.asyncio
async def test_confirmbet_zero_retry_on_timeout(limiter: RateLimiter):
    """Confirmbet  1 """
    call_count = 0

    async def always_timeout():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(999)

    original_timeouts = RateLimiter.TIMEOUTS.copy()
    RateLimiter.TIMEOUTS["Confirmbet"] = 0.05

    try:
        with pytest.raises(asyncio.TimeoutError):
            await limiter.execute("Confirmbet", always_timeout)

        assert call_count == 1, (
            f"Confirmbet  1  {call_count} "
        )
    finally:
        RateLimiter.TIMEOUTS.update(original_timeouts)


@pytest.mark.asyncio
async def test_confirmbet_zero_retry_on_exception(limiter: RateLimiter):
    """Confirmbet """
    call_count = 0

    async def raise_error():
        nonlocal call_count
        call_count += 1
        raise ConnectionError("")

    with pytest.raises(ConnectionError):
        await limiter.execute("Confirmbet", raise_error)

    assert call_count == 1, "Confirmbet "


@pytest.mark.asyncio
async def test_confirmbet_no_retry_logic_in_code():
    """Confirmbet  retry 

     is_retryable('Confirmbet')  False
     max_attempts  1
    """
    limiter = RateLimiter()
    assert limiter.is_retryable("Confirmbet") is False

    #  max_attempts 
    retryable = limiter.is_retryable("Confirmbet")
    max_attempts = (1 + limiter.MAX_RETRIES) if retryable else 1
    assert max_attempts == 1, "Confirmbet  max_attempts  1"


# 
# 6. execute 
# 

@pytest.mark.asyncio
async def test_execute_normal_call(limiter: RateLimiter):
    """execute """
    async def mock_call():
        return {"status": "ok"}

    result = await limiter.execute("GetCurrentInstall", mock_call)
    assert result == {"status": "ok"}


@pytest.mark.asyncio
async def test_execute_passes_args(limiter: RateLimiter):
    """execute """
    async def mock_call(a, b, key=None):
        return {"a": a, "b": b, "key": key}

    result = await limiter.execute(
        "Loaddata", mock_call, 1, 2, key="test"
    )
    assert result == {"a": 1, "b": 2, "key": "test"}


@pytest.mark.asyncio
async def test_execute_non_timeout_exception_not_retried(limiter: RateLimiter):
    """"""
    call_count = 0

    async def raise_value_error():
        nonlocal call_count
        call_count += 1
        raise ValueError("")

    with pytest.raises(ValueError):
        await limiter.execute("GetCurrentInstall", raise_value_error)

    assert call_count == 1, ""


# 
# 7.  RateLimiter per-account
# 

@pytest.mark.asyncio
async def test_per_account_isolation():
    """ RateLimiter """
    limiter_a = RateLimiter()
    limiter_b = RateLimiter()

    await limiter_a.acquire("Loaddata")

    # limiter_b  limiter_a 
    start = time.monotonic()
    await limiter_b.acquire("Loaddata")
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, ""


# 
# PBT: P20  + 
# 

from hypothesis import given, settings as h_settings, strategies as st


class TestPBT_P20_QueueNoDrop:
    """P20:  + 

    **Validates: Requirements 2.5**

    Property: For any sequence of N requests submitted concurrently:
      - All N requests complete (no drops)
      - Total completed = N
    """

    @given(n=st.integers(min_value=1, max_value=20))
    @h_settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_pbt_queue_no_drop(self, n: int):
        """For any N concurrent requests, all N complete without drops.

        **Validates: Requirements 2.5**
        """
        limiter = RateLimiter()
        # Override LIMITS to use very short interval for fast tests
        original_limits = RateLimiter.LIMITS.copy()
        RateLimiter.LIMITS["Loaddata"] = 0.01

        completed = []

        async def mock_call(idx: int) -> int:
            completed.append(idx)
            return idx

        try:
            tasks = [
                limiter.execute("Loaddata", mock_call, i)
                for i in range(n)
            ]
            results = await asyncio.gather(*tasks)

            # All N requests completed
            assert len(results) == n, (
                f"Expected {n} results, got {len(results)}"
            )
            # All N calls were made
            assert len(completed) == n, (
                f"Expected {n} completions, got {len(completed)}"
            )
            # Each request returned its index (no corruption)
            assert set(results) == set(range(n)), (
                f"Results mismatch: expected {set(range(n))}, got {set(results)}"
            )
        finally:
            RateLimiter.LIMITS.update(original_limits)
