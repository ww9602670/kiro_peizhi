"""Phase 5.4.3  SessionManager 


1. 30s  60s  120s 
2. 3  10 
3. 5  login_error 
4.  0
5. 
6.  5 
7.  75  heartbeat
8.  3  reconnect
9. reconnect token 
10. AlertService.send login_fail/captcha_fail/session_lost
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.engine.adapters.base import LoginResult
from app.engine.session import (
    SessionManager,
    RETRY_DELAYS,
    MAX_LOGIN_ATTEMPTS,
    PAUSE_DURATION,
    MAX_CAPTCHA_FAILURES,
    HEARTBEAT_INTERVAL,
    HEARTBEAT_MAX_FAILS,
)
from app.utils.captcha import CaptchaError


# 
# Fixtures
# 

@pytest.fixture
def mock_adapter():
    adapter = AsyncMock()
    adapter.login = AsyncMock(return_value=LoginResult(success=True, token="tok123"))
    adapter.heartbeat = AsyncMock(return_value=True)
    return adapter


@pytest.fixture
def mock_alert_service():
    svc = AsyncMock()
    svc.send = AsyncMock(return_value=True)
    return svc


@pytest.fixture
def mock_captcha_service():
    svc = AsyncMock()
    svc.recognize = AsyncMock(return_value="1234")
    return svc


@pytest.fixture
def session(mock_adapter, mock_alert_service, mock_captcha_service, db):
    return SessionManager(
        adapter=mock_adapter,
        alert_service=mock_alert_service,
        captcha_service=mock_captcha_service,
        operator_id=1,
        account_id=10,
        account_name="testuser",
        password="testpass",
        db=db,
    )


# 
# 1. 
# 

@pytest.mark.asyncio
async def test_login_success_first_attempt(session, mock_adapter):
    """token """
    result = await session.login()

    assert result is True
    assert session.session_token == "tok123"
    assert session.login_fail_count == 0
    assert session.captcha_fail_count == 0
    assert session.is_logged_in is True
    assert session.is_login_error is False
    mock_adapter.login.assert_called_once()


@pytest.mark.asyncio
async def test_login_success_resets_counters(session, mock_adapter):
    """ 0"""
    # 
    session.login_fail_count = 3
    session.captcha_fail_count = 2

    result = await session.login()

    assert result is True
    assert session.login_fail_count == 0
    assert session.captcha_fail_count == 0


# 
# 2. 30s  60s  120s
# 

@pytest.mark.asyncio
async def test_retry_delays_are_strictly_increasing():
    """RETRY_DELAYS """
    assert RETRY_DELAYS == [30, 60, 120]
    for i in range(len(RETRY_DELAYS) - 1):
        assert RETRY_DELAYS[i] < RETRY_DELAYS[i + 1]


@pytest.mark.asyncio
async def test_login_retry_intervals(session, mock_adapter):
    """ 30s  60s  120s """
    #  5 
    mock_adapter.login.return_value = LoginResult(success=False, message="")

    sleep_calls = []
    original_sleep = asyncio.sleep

    async def mock_sleep(seconds):
        sleep_calls.append(seconds)

    with patch("app.engine.session.asyncio.sleep", side_effect=mock_sleep):
        result = await session.login()

    assert result is False

    #  sleep 
    # attempt 0   sleep(30)
    # attempt 1   sleep(60)
    # attempt 2   sleep(120) + sleep(600)  [310]
    # attempt 3   (no delay, attempt >= len(retry_delays))
    # attempt 4   (no delay)
    assert 30 in sleep_calls
    assert 60 in sleep_calls
    assert 120 in sleep_calls
    assert 600 in sleep_calls

    # 30  60 60  120 
    idx_30 = sleep_calls.index(30)
    idx_60 = sleep_calls.index(60)
    idx_120 = sleep_calls.index(120)
    idx_600 = sleep_calls.index(600)
    assert idx_30 < idx_60 < idx_120 < idx_600



# 
# 3. 3  10 
# 

@pytest.mark.asyncio
async def test_pause_10_minutes_after_3_failures(session, mock_adapter):
    """ 3  10 600 """
    mock_adapter.login.return_value = LoginResult(success=False, message="")

    sleep_calls = []

    async def mock_sleep(seconds):
        sleep_calls.append(seconds)

    with patch("app.engine.session.asyncio.sleep", side_effect=mock_sleep):
        await session.login()

    #  3 attempt==2 600 
    assert 600 in sleep_calls
    # 600  120 
    idx_120 = sleep_calls.index(120)
    idx_600 = sleep_calls.index(600)
    assert idx_600 > idx_120


# 
# 4. 5  login_error
# 

@pytest.mark.asyncio
async def test_5_failures_marks_login_error(session, mock_adapter, mock_alert_service):
    """ 5  login_error """
    mock_adapter.login.return_value = LoginResult(success=False, message="")

    with patch("app.engine.session.asyncio.sleep", new_callable=AsyncMock):
        result = await session.login()

    assert result is False
    assert session.is_login_error is True
    assert session.login_fail_count == 5

    # AlertService.send  login_fail
    mock_alert_service.send.assert_called()
    login_fail_calls = [
        c for c in mock_alert_service.send.call_args_list
        if c.kwargs.get("alert_type") == "login_fail"
        or (len(c.args) > 1 and c.args[1] == "login_fail")
    ]
    assert len(login_fail_calls) >= 1


@pytest.mark.asyncio
async def test_login_error_prevents_auto_retry(session, mock_adapter):
    """login_error  login()  False"""
    session._login_error = True

    result = await session.login()

    assert result is False
    mock_adapter.login.assert_not_called()


@pytest.mark.asyncio
async def test_5_failures_calls_on_status_change(mock_adapter, mock_alert_service, mock_captcha_service, db):
    """5  on_status_change """
    status_changes = []

    async def on_change(account_id, status):
        status_changes.append((account_id, status))

    sm = SessionManager(
        adapter=mock_adapter,
        alert_service=mock_alert_service,
        captcha_service=mock_captcha_service,
        operator_id=1,
        account_id=10,
        account_name="testuser",
        password="testpass",
        db=db,
        on_status_change=on_change,
    )
    mock_adapter.login.return_value = LoginResult(success=False, message="")

    with patch("app.engine.session.asyncio.sleep", new_callable=AsyncMock):
        await sm.login()

    assert (10, "login_error") in status_changes


# 
# 5. 
# 

@pytest.mark.asyncio
async def test_captcha_fail_count_independent(session, mock_adapter, mock_captcha_service):
    """"""
    #  1 
    #  2 
    #  3 
    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count in (1, 3):
            raise CaptchaError("")
        return LoginResult(success=False, message="")

    mock_adapter.login.side_effect = side_effect
    #  get_captcha 
    if hasattr(mock_adapter, "get_captcha"):
        delattr(mock_adapter, "get_captcha")

    with patch("app.engine.session.asyncio.sleep", new_callable=AsyncMock):
        await session.login()

    #  2  3 attempt 1,3,4  login  False
    assert session.captcha_fail_count == 2
    # login_fail_count  login  success=False 
    assert session.login_fail_count == 3


@pytest.mark.asyncio
async def test_captcha_5_failures_sends_alert(session, mock_adapter, mock_alert_service):
    """ 5  captcha_fail """
    #  CaptchaError
    mock_adapter.login.side_effect = CaptchaError("")
    if hasattr(mock_adapter, "get_captcha"):
        delattr(mock_adapter, "get_captcha")

    with patch("app.engine.session.asyncio.sleep", new_callable=AsyncMock):
        result = await session.login()

    assert result is False
    assert session.captcha_fail_count == 5

    #  captcha_fail 
    captcha_calls = [
        c for c in mock_alert_service.send.call_args_list
        if c.kwargs.get("alert_type") == "captcha_fail"
    ]
    assert len(captcha_calls) >= 1


@pytest.mark.asyncio
async def test_captcha_fail_returns_early_at_5(session, mock_adapter, mock_alert_service):
    """ 5  False"""
    call_count = 0

    async def login_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise CaptchaError("")

    mock_adapter.login.side_effect = login_side_effect
    if hasattr(mock_adapter, "get_captcha"):
        delattr(mock_adapter, "get_captcha")

    with patch("app.engine.session.asyncio.sleep", new_callable=AsyncMock):
        result = await session.login()

    assert result is False
    #  5  5  attempt
    assert session.captcha_fail_count == MAX_CAPTCHA_FAILURES


# 
# 6. 
# 

@pytest.mark.asyncio
async def test_success_after_failures_resets_all(session, mock_adapter):
    """"""
    call_count = 0

    async def login_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return LoginResult(success=False, message="")
        return LoginResult(success=True, token="tok_new")

    mock_adapter.login.side_effect = login_side_effect
    if hasattr(mock_adapter, "get_captcha"):
        delattr(mock_adapter, "get_captcha")

    with patch("app.engine.session.asyncio.sleep", new_callable=AsyncMock):
        result = await session.login()

    assert result is True
    assert session.login_fail_count == 0
    assert session.captcha_fail_count == 0
    assert session.session_token == "tok_new"



# 
# 7. 
# 

@pytest.mark.asyncio
async def test_heartbeat_calls_adapter(session, mock_adapter):
    """ 75  adapter.heartbeat()"""
    sleep_calls = []
    heartbeat_count = 0

    async def mock_sleep(seconds):
        nonlocal heartbeat_count
        sleep_calls.append(seconds)
        heartbeat_count += 1
        if heartbeat_count >= 3:
            raise asyncio.CancelledError()

    with patch("app.engine.session.asyncio.sleep", side_effect=mock_sleep):
        with pytest.raises(asyncio.CancelledError):
            await session._heartbeat_loop()

    #  sleep  75 
    assert all(s == HEARTBEAT_INTERVAL for s in sleep_calls)
    assert mock_adapter.heartbeat.call_count >= 2


# 
# 8.  3  reconnect
# 

@pytest.mark.asyncio
async def test_heartbeat_3_fails_triggers_reconnect(session, mock_adapter, mock_alert_service):
    """ 3  reconnect + session_lost """
    mock_adapter.heartbeat.return_value = False
    reconnect_called = False

    async def mock_reconnect():
        nonlocal reconnect_called
        reconnect_called = True

    session._reconnect = mock_reconnect

    call_count = 0

    async def mock_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count > HEARTBEAT_MAX_FAILS + 1:
            raise asyncio.CancelledError()

    with patch("app.engine.session.asyncio.sleep", side_effect=mock_sleep):
        with pytest.raises(asyncio.CancelledError):
            await session._heartbeat_loop()

    assert reconnect_called is True

    # session_lost 
    session_lost_calls = [
        c for c in mock_alert_service.send.call_args_list
        if c.kwargs.get("alert_type") == "session_lost"
    ]
    assert len(session_lost_calls) >= 1


@pytest.mark.asyncio
async def test_heartbeat_success_resets_fail_count(session, mock_adapter):
    """"""
    call_count = 0

    async def heartbeat_side_effect():
        nonlocal call_count
        call_count += 1
        #  2  3  4-5 
        if call_count in (1, 2):
            return False
        if call_count == 3:
            return True
        if call_count in (4, 5):
            return False
        raise asyncio.CancelledError()

    mock_adapter.heartbeat.side_effect = heartbeat_side_effect

    loop_count = 0

    async def mock_sleep(seconds):
        nonlocal loop_count
        loop_count += 1
        if loop_count > 6:
            raise asyncio.CancelledError()

    session._reconnect = AsyncMock()

    with patch("app.engine.session.asyncio.sleep", side_effect=mock_sleep):
        with pytest.raises(asyncio.CancelledError):
            await session._heartbeat_loop()

    # reconnect  3  2 
    session._reconnect.assert_not_called()


@pytest.mark.asyncio
async def test_heartbeat_exception_counts_as_failure(session, mock_adapter):
    """"""
    mock_adapter.heartbeat.side_effect = Exception("")
    reconnect_called = False

    async def mock_reconnect():
        nonlocal reconnect_called
        reconnect_called = True

    session._reconnect = mock_reconnect

    call_count = 0

    async def mock_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count > HEARTBEAT_MAX_FAILS + 1:
            raise asyncio.CancelledError()

    with patch("app.engine.session.asyncio.sleep", side_effect=mock_sleep):
        with pytest.raises(asyncio.CancelledError):
            await session._heartbeat_loop()

    assert reconnect_called is True


# 
# 9. reconnect token 
# 

@pytest.mark.asyncio
async def test_reconnect_tries_refresh_first(session, mock_adapter):
    """reconnect  token """
    mock_adapter.refresh_token = AsyncMock(return_value="new_token")
    session.session_token = "old_token"

    await session._reconnect()

    mock_adapter.refresh_token.assert_called_once()
    assert session.session_token == "new_token"
    # login 
    mock_adapter.login.assert_not_called()


@pytest.mark.asyncio
async def test_reconnect_falls_back_to_login(session, mock_adapter):
    """token """
    mock_adapter.refresh_token = AsyncMock(side_effect=Exception(""))
    mock_adapter.login.return_value = LoginResult(success=True, token="relogin_tok")

    with patch("app.engine.session.asyncio.sleep", new_callable=AsyncMock):
        await session._reconnect()

    mock_adapter.refresh_token.assert_called_once()
    mock_adapter.login.assert_called()
    assert session.session_token == "relogin_tok"


@pytest.mark.asyncio
async def test_reconnect_without_refresh_support(session, mock_adapter):
    """ refresh_token """
    #  refresh_token 
    del mock_adapter.refresh_token
    mock_adapter.login.return_value = LoginResult(success=True, token="relogin_tok")

    with patch("app.engine.session.asyncio.sleep", new_callable=AsyncMock):
        await session._reconnect()

    mock_adapter.login.assert_called()
    assert session.session_token == "relogin_tok"


# 
# 10. AlertService.send 
# 

@pytest.mark.asyncio
async def test_alert_login_fail_type(session, mock_adapter, mock_alert_service):
    """5  login_fail """
    mock_adapter.login.return_value = LoginResult(success=False, message="")

    with patch("app.engine.session.asyncio.sleep", new_callable=AsyncMock):
        await session.login()

    #  login_fail 
    send_calls = mock_alert_service.send.call_args_list
    alert_types = [c.kwargs.get("alert_type") for c in send_calls]
    assert "login_fail" in alert_types


@pytest.mark.asyncio
async def test_alert_captcha_fail_type(session, mock_adapter, mock_alert_service):
    """ 5  captcha_fail """
    mock_adapter.login.side_effect = CaptchaError("")
    if hasattr(mock_adapter, "get_captcha"):
        delattr(mock_adapter, "get_captcha")

    with patch("app.engine.session.asyncio.sleep", new_callable=AsyncMock):
        await session.login()

    send_calls = mock_alert_service.send.call_args_list
    alert_types = [c.kwargs.get("alert_type") for c in send_calls]
    assert "captcha_fail" in alert_types


@pytest.mark.asyncio
async def test_alert_session_lost_type(session, mock_adapter, mock_alert_service):
    """ 3  session_lost """
    mock_adapter.heartbeat.return_value = False

    # Mock reconnect to avoid actual login
    session._reconnect = AsyncMock()

    call_count = 0

    async def mock_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count > HEARTBEAT_MAX_FAILS + 1:
            raise asyncio.CancelledError()

    with patch("app.engine.session.asyncio.sleep", side_effect=mock_sleep):
        with pytest.raises(asyncio.CancelledError):
            await session._heartbeat_loop()

    send_calls = mock_alert_service.send.call_args_list
    alert_types = [c.kwargs.get("alert_type") for c in send_calls]
    assert "session_lost" in alert_types


@pytest.mark.asyncio
async def test_alert_includes_account_id(session, mock_adapter, mock_alert_service):
    """ account_id"""
    mock_adapter.login.return_value = LoginResult(success=False, message="")

    with patch("app.engine.session.asyncio.sleep", new_callable=AsyncMock):
        await session.login()

    for c in mock_alert_service.send.call_args_list:
        assert c.kwargs.get("account_id") == 10


# 
# 11. manual_login  login_error
# 

@pytest.mark.asyncio
async def test_manual_login_resets_error(session, mock_adapter):
    """manual_login  login_error """
    session._login_error = True
    session.login_fail_count = 5
    session.captcha_fail_count = 3

    mock_adapter.login.return_value = LoginResult(success=True, token="manual_tok")

    result = await session.manual_login()

    assert result is True
    assert session.is_login_error is False
    assert session.login_fail_count == 0
    assert session.captcha_fail_count == 0
    assert session.session_token == "manual_tok"


# 
# 12. ensure_session
# 

@pytest.mark.asyncio
async def test_ensure_session_when_logged_in(session):
    """ ensure_session  True"""
    session.session_token = "tok"
    result = await session.ensure_session()
    assert result is True


@pytest.mark.asyncio
async def test_ensure_session_when_login_error(session):
    """login_error  ensure_session  False"""
    session._login_error = True
    result = await session.ensure_session()
    assert result is False


@pytest.mark.asyncio
async def test_ensure_session_triggers_login(session, mock_adapter):
    """ error  ensure_session  login"""
    session.session_token = None
    mock_adapter.login.return_value = LoginResult(success=True, token="new_tok")

    result = await session.ensure_session()

    assert result is True
    assert session.session_token == "new_tok"


# 
# 13. _start_heartbeat / stop_heartbeat
# 

@pytest.mark.asyncio
async def test_start_heartbeat_creates_task(session):
    """_start_heartbeat """
    #  sleep 
    hang_forever = asyncio.Event()

    async def mock_sleep(seconds):
        await hang_forever.wait()

    with patch("app.engine.session.asyncio.sleep", side_effect=mock_sleep):
        session._start_heartbeat()

        assert session.heartbeat_task is not None
        assert not session.heartbeat_task.done()

    # 
    session.stop_heartbeat()
    await asyncio.sleep(0)  # 


@pytest.mark.asyncio
async def test_stop_heartbeat_cancels_task(session):
    """stop_heartbeat """
    hang_forever = asyncio.Event()

    async def mock_sleep(seconds):
        await hang_forever.wait()

    with patch("app.engine.session.asyncio.sleep", side_effect=mock_sleep):
        session._start_heartbeat()
        task = session.heartbeat_task

        session.stop_heartbeat()
        assert session.heartbeat_task is None

    await asyncio.sleep(0)  # 
    assert task.cancelled() or task.done()


# 
# PBT: P21 
# 

from hypothesis import given, settings as h_settings, strategies as st


class TestPBT_P21_RetryDelaysMonotonic:
    """P21: 

    **Validates: Requirements 2.2**

    Property: The RETRY_DELAYS sequence is strictly increasing:
      retry_delays[i] < retry_delays[i+1] for all valid i.
    """

    @given(st.just(None))
    @h_settings(max_examples=200)
    def test_pbt_retry_delays_strictly_increasing(self, _):
        """RETRY_DELAYS is strictly increasing across all hypothesis runs.

        **Validates: Requirements 2.2**
        """
        assert len(RETRY_DELAYS) >= 2, "RETRY_DELAYS must have at least 2 elements"
        for i in range(len(RETRY_DELAYS) - 1):
            assert RETRY_DELAYS[i] < RETRY_DELAYS[i + 1], (
                f"RETRY_DELAYS not strictly increasing at index {i}: "
                f"{RETRY_DELAYS[i]} >= {RETRY_DELAYS[i + 1]}"
            )

    @given(idx=st.integers(min_value=0, max_value=1))
    @h_settings(max_examples=200)
    def test_pbt_retry_delays_pairwise_monotonic(self, idx: int):
        """For any valid adjacent pair in RETRY_DELAYS, left < right.

        **Validates: Requirements 2.2**
        """
        # idx is bounded to [0, len(RETRY_DELAYS)-2] = [0, 1]
        assert RETRY_DELAYS[idx] < RETRY_DELAYS[idx + 1], (
            f"RETRY_DELAYS[{idx}]={RETRY_DELAYS[idx]} >= "
            f"RETRY_DELAYS[{idx + 1}]={RETRY_DELAYS[idx + 1]}"
        )

    @given(delays=st.permutations([30, 60, 120]))
    @h_settings(max_examples=200)
    def test_pbt_actual_constant_vs_permutations(self, delays: list[int]):
        """For any permutation of the delay values, only the actual
        RETRY_DELAYS constant maintains strict monotonicity.

        **Validates: Requirements 2.2**
        """
        # The actual constant must always be strictly increasing
        actual = RETRY_DELAYS
        for i in range(len(actual) - 1):
            assert actual[i] < actual[i + 1]

        # Check if this permutation is also strictly increasing
        is_sorted = all(delays[i] < delays[i + 1] for i in range(len(delays) - 1))
        if is_sorted:
            # Only [30, 60, 120] should be strictly increasing
            assert delays == [30, 60, 120], (
                f"Unexpected strictly increasing permutation: {delays}"
            )

    @given(
        extra_delays=st.lists(
            st.integers(min_value=1, max_value=1000),
            min_size=2, max_size=10,
        )
    )
    @h_settings(max_examples=200)
    def test_pbt_actual_delays_always_monotonic_regardless_of_random(self, extra_delays):
        """Regardless of any random sequence generated, the actual
        RETRY_DELAYS constant is always strictly monotonic.

        **Validates: Requirements 2.2**
        """
        # The actual constant is always strictly increasing
        for i in range(len(RETRY_DELAYS) - 1):
            assert RETRY_DELAYS[i] < RETRY_DELAYS[i + 1]

        # All actual delay values are positive
        for d in RETRY_DELAYS:
            assert d > 0, f"Delay must be positive, got {d}"
