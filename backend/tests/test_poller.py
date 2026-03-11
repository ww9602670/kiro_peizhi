"""IssuePoller 


- is_new_issue 
- 19:55-20:33, 05:59-07:00  1 
-  3  State1 
- State=1   
- /
- 5s
- 30s
"""
from __future__ import annotations

import logging
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.engine.adapters.base import InstallInfo, PlatformAdapter
from app.engine.poller import (
    IssuePoller,
    NORMAL_INTERVAL,
    RANDOM_DOWNTIME_THRESHOLD,
    SLOW_INTERVAL,
)
from app.engine.rate_limiter import RateLimiter


#  helpers 


def _make_install(
    issue: str = "20250101001",
    state: int = 1,
    close_countdown_sec: int = 60,
    pre_issue: str = "20250101000",
    pre_result: str = "3,5,7",
) -> InstallInfo:
    return InstallInfo(
        issue=issue,
        state=state,
        close_countdown_sec=close_countdown_sec,
        pre_issue=pre_issue,
        pre_result=pre_result,
    )


def _make_time_func(hour: int, minute: int, second: int = 0):
    """ time_func"""
    def _func() -> datetime:
        return datetime(2025, 7, 1, hour, minute, second)
    return _func


def _make_adapter(installs: list[InstallInfo]) -> PlatformAdapter:
    """ mock adapter installs"""
    adapter = AsyncMock(spec=PlatformAdapter)
    adapter.get_current_install = AsyncMock(side_effect=installs)
    return adapter


def _make_rate_limiter() -> RateLimiter:
    """ mock RateLimiter"""
    rl = AsyncMock(spec=RateLimiter)

    async def _execute(api_name, call, *args, **kwargs):
        return await call(*args, **kwargs)

    rl.execute = AsyncMock(side_effect=_execute)
    return rl


#   


class TestNewIssueDetection:
    """"""

    @pytest.mark.asyncio
    async def test_first_poll_not_new_issue(self):
        """ is_new_issue"""
        adapter = _make_adapter([_make_install(issue="001")])
        poller = IssuePoller(adapter, _make_rate_limiter())

        result = await poller.poll()

        assert result.is_new_issue is False
        assert poller.last_issue == "001"

    @pytest.mark.asyncio
    async def test_same_issue_not_new(self):
        """ is_new_issue"""
        adapter = _make_adapter([
            _make_install(issue="001"),
            _make_install(issue="001"),
        ])
        poller = IssuePoller(adapter, _make_rate_limiter())

        await poller.poll()
        result = await poller.poll()

        assert result.is_new_issue is False

    @pytest.mark.asyncio
    async def test_different_issue_is_new(self):
        """ is_new_issue=True"""
        adapter = _make_adapter([
            _make_install(issue="001"),
            _make_install(issue="002"),
        ])
        poller = IssuePoller(adapter, _make_rate_limiter())

        await poller.poll()  # 
        result = await poller.poll()  # 

        assert result.is_new_issue is True
        assert poller.last_issue == "002"

    @pytest.mark.asyncio
    async def test_multiple_issue_changes(self):
        """"""
        adapter = _make_adapter([
            _make_install(issue="001"),
            _make_install(issue="002"),
            _make_install(issue="002"),
            _make_install(issue="003"),
        ])
        poller = IssuePoller(adapter, _make_rate_limiter())

        r1 = await poller.poll()  #  001
        r2 = await poller.poll()  # 002 
        r3 = await poller.poll()  # 002 
        r4 = await poller.poll()  # 003 

        assert r1.is_new_issue is False
        assert r2.is_new_issue is True
        assert r3.is_new_issue is False
        assert r4.is_new_issue is True


#   


class TestKnownDowntime:
    """ 1 """

    @pytest.mark.asyncio
    async def test_normal_time_not_downtime(self):
        """"""
        poller = IssuePoller(
            _make_adapter([]),
            _make_rate_limiter(),
            time_func=_make_time_func(12, 0),
        )
        assert poller.is_known_downtime() is False

    @pytest.mark.asyncio
    async def test_evening_downtime_start_early(self):
        """19:55  1 """
        poller = IssuePoller(
            _make_adapter([]),
            _make_rate_limiter(),
            time_func=_make_time_func(19, 55),
        )
        assert poller.is_known_downtime() is True

    @pytest.mark.asyncio
    async def test_evening_before_early_entry(self):
        """19:54 """
        poller = IssuePoller(
            _make_adapter([]),
            _make_rate_limiter(),
            time_func=_make_time_func(19, 54),
        )
        assert poller.is_known_downtime() is False

    @pytest.mark.asyncio
    async def test_evening_downtime_middle(self):
        """20:00 """
        poller = IssuePoller(
            _make_adapter([]),
            _make_rate_limiter(),
            time_func=_make_time_func(20, 0),
        )
        assert poller.is_known_downtime() is True

    @pytest.mark.asyncio
    async def test_evening_downtime_end(self):
        """20:33 """
        poller = IssuePoller(
            _make_adapter([]),
            _make_rate_limiter(),
            time_func=_make_time_func(20, 33),
        )
        assert poller.is_known_downtime() is True

    @pytest.mark.asyncio
    async def test_evening_after_downtime(self):
        """20:34 """
        poller = IssuePoller(
            _make_adapter([]),
            _make_rate_limiter(),
            time_func=_make_time_func(20, 34),
        )
        assert poller.is_known_downtime() is False

    @pytest.mark.asyncio
    async def test_morning_downtime_early_entry(self):
        """05:59  1 """
        poller = IssuePoller(
            _make_adapter([]),
            _make_rate_limiter(),
            time_func=_make_time_func(5, 59),
        )
        assert poller.is_known_downtime() is True

    @pytest.mark.asyncio
    async def test_morning_before_early_entry(self):
        """05:58 """
        poller = IssuePoller(
            _make_adapter([]),
            _make_rate_limiter(),
            time_func=_make_time_func(5, 58),
        )
        assert poller.is_known_downtime() is False

    @pytest.mark.asyncio
    async def test_morning_downtime_middle(self):
        """06:30 """
        poller = IssuePoller(
            _make_adapter([]),
            _make_rate_limiter(),
            time_func=_make_time_func(6, 30),
        )
        assert poller.is_known_downtime() is True

    @pytest.mark.asyncio
    async def test_morning_downtime_end(self):
        """07:00 """
        poller = IssuePoller(
            _make_adapter([]),
            _make_rate_limiter(),
            time_func=_make_time_func(7, 0),
        )
        assert poller.is_known_downtime() is True

    @pytest.mark.asyncio
    async def test_morning_after_downtime(self):
        """07:01 """
        poller = IssuePoller(
            _make_adapter([]),
            _make_rate_limiter(),
            time_func=_make_time_func(7, 1),
        )
        assert poller.is_known_downtime() is False

    @pytest.mark.asyncio
    async def test_known_downtime_enters_slow_mode(self):
        """"""
        adapter = _make_adapter([_make_install(issue="001", state=0)])
        poller = IssuePoller(
            adapter,
            _make_rate_limiter(),
            time_func=_make_time_func(20, 0),
        )

        await poller.poll()

        assert poller._in_slow_mode is True
        assert poller.poll_interval == SLOW_INTERVAL


#   


class TestRandomDowntime:
    """"""

    @pytest.mark.asyncio
    async def test_single_non_open_no_slow_mode(self):
        """ State1 """
        adapter = _make_adapter([
            _make_install(issue="001", state=1),
            _make_install(issue="001", state=0),
        ])
        poller = IssuePoller(
            adapter, _make_rate_limiter(),
            time_func=_make_time_func(12, 0),
        )

        await poller.poll()
        await poller.poll()

        assert poller._in_slow_mode is False
        assert poller.non_open_count == 1

    @pytest.mark.asyncio
    async def test_two_non_open_no_slow_mode(self):
        """ 2  State1 """
        adapter = _make_adapter([
            _make_install(issue="001", state=1),
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),
        ])
        poller = IssuePoller(
            adapter, _make_rate_limiter(),
            time_func=_make_time_func(12, 0),
        )

        await poller.poll()
        await poller.poll()
        await poller.poll()

        assert poller._in_slow_mode is False
        assert poller.non_open_count == 2

    @pytest.mark.asyncio
    async def test_three_non_open_triggers_slow_mode(self):
        """ 3  State1   """
        adapter = _make_adapter([
            _make_install(issue="001", state=1),
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),
        ])
        poller = IssuePoller(
            adapter, _make_rate_limiter(),
            time_func=_make_time_func(12, 0),
        )

        await poller.poll()  # state=1, 
        await poller.poll()  # state=0, count=1
        await poller.poll()  # state=0, count=2
        await poller.poll()  # state=0, count=3  

        assert poller._in_slow_mode is True
        assert poller.poll_interval == SLOW_INTERVAL
        assert poller.non_open_count == RANDOM_DOWNTIME_THRESHOLD

    @pytest.mark.asyncio
    async def test_non_open_with_issue_change_resets_count(self):
        """State1 """
        adapter = _make_adapter([
            _make_install(issue="001", state=1),
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),
            _make_install(issue="002", state=0),  # 
            _make_install(issue="002", state=0),
        ])
        poller = IssuePoller(
            adapter, _make_rate_limiter(),
            time_func=_make_time_func(12, 0),
        )

        await poller.poll()  # 001, state=1
        await poller.poll()  # 001, state=0, count=1
        await poller.poll()  # 001, state=0, count=2
        await poller.poll()  # 002, state=0, count=1 ()
        await poller.poll()  # 002, state=0, count=2

        assert poller._in_slow_mode is False
        assert poller.non_open_count == 2

    @pytest.mark.asyncio
    async def test_open_state_resets_non_open_count(self):
        """State=1  non_open_count"""
        adapter = _make_adapter([
            _make_install(issue="001", state=1),
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=1),  # 
        ])
        poller = IssuePoller(
            adapter, _make_rate_limiter(),
            time_func=_make_time_func(12, 0),
        )

        await poller.poll()
        await poller.poll()  # count=1
        await poller.poll()  # count=2
        await poller.poll()  # state=1, count=0

        assert poller.non_open_count == 0


#   


class TestRecoveryDetection:
    """"""

    @pytest.mark.asyncio
    async def test_recovery_from_random_downtime(self):
        """ State=1   """
        adapter = _make_adapter([
            _make_install(issue="001", state=1),
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),  # 
            _make_install(issue="002", state=1),  # 
        ])
        poller = IssuePoller(
            adapter, _make_rate_limiter(),
            time_func=_make_time_func(12, 0),
        )

        await poller.poll()
        await poller.poll()
        await poller.poll()
        await poller.poll()
        assert poller._in_slow_mode is True

        result = await poller.poll()
        assert poller._in_slow_mode is False
        assert poller.poll_interval == NORMAL_INTERVAL
        assert result.is_new_issue is True
        assert result.issue == "002"

    @pytest.mark.asyncio
    async def test_no_recovery_same_issue(self):
        """ State=1   """
        adapter = _make_adapter([
            _make_install(issue="001", state=1),
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),  # 
            _make_install(issue="001", state=1),  # State=1 
        ])
        poller = IssuePoller(
            adapter, _make_rate_limiter(),
            time_func=_make_time_func(12, 0),
        )

        await poller.poll()
        await poller.poll()
        await poller.poll()
        await poller.poll()
        assert poller._in_slow_mode is True

        await poller.poll()
        # State=1 
        assert poller._in_slow_mode is True

    @pytest.mark.asyncio
    async def test_recovery_from_known_downtime(self):
        """ State=1  """
        #  20:00 20:34
        times = [
            datetime(2025, 7, 1, 20, 0),   # 
            datetime(2025, 7, 1, 20, 34),  # 
        ]
        time_iter = iter(times)

        # time_func is_known_downtime + poll 
        # 
        call_count = [0]
        def time_func():
            # 
            if call_count[0] < 3:
                call_count[0] += 1
                return datetime(2025, 7, 1, 20, 0)
            call_count[0] += 1
            return datetime(2025, 7, 1, 20, 34)

        adapter = _make_adapter([
            _make_install(issue="001", state=0),
            _make_install(issue="002", state=1),
        ])
        poller = IssuePoller(
            adapter, _make_rate_limiter(),
            time_func=time_func,
        )

        await poller.poll()  # 
        assert poller._in_slow_mode is True

        result = await poller.poll()  #  + State=1
        assert poller._in_slow_mode is False
        assert poller.poll_interval == NORMAL_INTERVAL


#   


class TestPollInterval:
    """"""

    @pytest.mark.asyncio
    async def test_normal_interval(self):
        """ 5s"""
        poller = IssuePoller(
            _make_adapter([]),
            _make_rate_limiter(),
            time_func=_make_time_func(12, 0),
        )
        assert poller.poll_interval == NORMAL_INTERVAL
        assert NORMAL_INTERVAL == 5

    @pytest.mark.asyncio
    async def test_slow_interval(self):
        """ 30s"""
        adapter = _make_adapter([
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),
        ])
        poller = IssuePoller(
            adapter, _make_rate_limiter(),
            time_func=_make_time_func(12, 0),
        )

        await poller.poll()  # count=1
        await poller.poll()  # count=2
        await poller.poll()  # count=3  

        assert poller.poll_interval == SLOW_INTERVAL
        assert SLOW_INTERVAL == 30


#   


class TestEventLogging:
    """/"""

    @pytest.mark.asyncio
    async def test_random_downtime_log(self, caplog):
        """"""
        adapter = _make_adapter([
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),
        ])
        poller = IssuePoller(
            adapter, _make_rate_limiter(),
            time_func=_make_time_func(12, 0),
        )

        with caplog.at_level(logging.INFO, logger="app.engine.poller"):
            await poller.poll()
            await poller.poll()
            await poller.poll()

        assert any("" in r.message for r in caplog.records)
        assert any("" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_known_downtime_log(self, caplog):
        """"""
        adapter = _make_adapter([_make_install(issue="001", state=0)])
        poller = IssuePoller(
            adapter, _make_rate_limiter(),
            time_func=_make_time_func(20, 0),
        )

        with caplog.at_level(logging.INFO, logger="app.engine.poller"):
            await poller.poll()

        assert any("" in r.message for r in caplog.records)
        assert any("" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_recovery_log(self, caplog):
        """"""
        adapter = _make_adapter([
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),
            _make_install(issue="002", state=1),
        ])
        poller = IssuePoller(
            adapter, _make_rate_limiter(),
            time_func=_make_time_func(12, 0),
        )

        with caplog.at_level(logging.INFO, logger="app.engine.poller"):
            await poller.poll()
            await poller.poll()
            await poller.poll()
            await poller.poll()

        assert any("" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_new_issue_log(self, caplog):
        """"""
        adapter = _make_adapter([
            _make_install(issue="001"),
            _make_install(issue="002"),
        ])
        poller = IssuePoller(
            adapter, _make_rate_limiter(),
            time_func=_make_time_func(12, 0),
        )

        with caplog.at_level(logging.INFO, logger="app.engine.poller"):
            await poller.poll()
            await poller.poll()

        assert any("" in r.message for r in caplog.records)


#  RateLimiter  


class TestRateLimiterIntegration:
    """ poller  RateLimiter  adapter"""

    @pytest.mark.asyncio
    async def test_uses_rate_limiter(self):
        """poll  rate_limiter.execute  adapter"""
        adapter = _make_adapter([_make_install(issue="001")])
        rl = _make_rate_limiter()
        poller = IssuePoller(adapter, rl, time_func=_make_time_func(12, 0))

        await poller.poll()

        rl.execute.assert_called_once()
        call_args = rl.execute.call_args
        assert call_args[0][0] == "GetCurrentInstall"


#   


class TestEdgeCases:
    """"""

    @pytest.mark.asyncio
    async def test_downtime_at_midnight_boundary(self):
        """"""
        poller = IssuePoller(
            _make_adapter([]),
            _make_rate_limiter(),
            downtime_ranges=[("23:55", "00:10")],
            time_func=_make_time_func(23, 56),
        )
        assert poller.is_known_downtime() is True

    @pytest.mark.asyncio
    async def test_downtime_at_midnight_early_entry(self):
        """ 1 """
        poller = IssuePoller(
            _make_adapter([]),
            _make_rate_limiter(),
            downtime_ranges=[("23:55", "00:10")],
            time_func=_make_time_func(23, 54),
        )
        assert poller.is_known_downtime() is True

    @pytest.mark.asyncio
    async def test_downtime_at_midnight_after(self):
        """"""
        poller = IssuePoller(
            _make_adapter([]),
            _make_rate_limiter(),
            downtime_ranges=[("23:55", "00:10")],
            time_func=_make_time_func(0, 5),
        )
        assert poller.is_known_downtime() is True

    @pytest.mark.asyncio
    async def test_empty_downtime_ranges(self):
        """"""
        poller = IssuePoller(
            _make_adapter([]),
            _make_rate_limiter(),
            downtime_ranges=[],
            time_func=_make_time_func(20, 0),
        )
        assert poller.is_known_downtime() is False

    @pytest.mark.asyncio
    async def test_slow_mode_not_entered_twice(self):
        """"""
        adapter = _make_adapter([
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),
            _make_install(issue="001", state=0),  #  4 
        ])
        poller = IssuePoller(
            adapter, _make_rate_limiter(),
            time_func=_make_time_func(12, 0),
        )

        await poller.poll()
        await poller.poll()
        await poller.poll()  # 
        assert poller._in_slow_mode is True

        await poller.poll()  # 
        assert poller._in_slow_mode is True
        assert poller._slow_mode_reason == ""
