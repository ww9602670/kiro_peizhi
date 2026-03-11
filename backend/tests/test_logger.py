""" JSON 


1. JSON timestamp, level, logger, message
2. trace_id set  
3. trace_id 
4. Helper log_bet, log_settlement, log_balance, log_strategy_state, log_login
5. duration_ms 
6. operator_id, account_id, issue
"""

import asyncio
import json
import logging
import uuid

import pytest

from app.utils.logger import (
    JSONFormatter,
    generate_trace_id,
    get_logger,
    get_trace_id,
    log_balance,
    log_bet,
    log_login,
    log_settlement,
    log_strategy_state,
    set_trace_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class _CaptureHandler(logging.Handler):
    """ handler JSON """

    def __init__(self) -> None:
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))


@pytest.fixture(autouse=True)
def _reset_trace_id():
    """ trace_id """
    from app.utils.logger import _trace_id_var
    token = _trace_id_var.set(None)
    yield
    _trace_id_var.reset(token)


@pytest.fixture()
def capture_logger():
    """ (logger, handler)handler.records  JSON """
    logger = logging.getLogger(f"test.{uuid.uuid4().hex[:8]}")
    logger.handlers.clear()
    handler = _CaptureHandler()
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger, handler


# ---------------------------------------------------------------------------
# 1. JSON 
# ---------------------------------------------------------------------------

class TestJSONFormat:
    """ JSON"""

    def test_output_is_valid_json(self, capture_logger):
        logger, handler = capture_logger
        logger.info("hello")
        assert len(handler.records) == 1
        parsed = json.loads(handler.records[0])
        assert isinstance(parsed, dict)

    def test_required_fields_present(self, capture_logger):
        logger, handler = capture_logger
        logger.warning("test message")
        parsed = json.loads(handler.records[0])
        assert "timestamp" in parsed
        assert "level" in parsed
        assert "logger" in parsed
        assert "message" in parsed

    def test_level_matches(self, capture_logger):
        logger, handler = capture_logger
        logger.error("err")
        parsed = json.loads(handler.records[0])
        assert parsed["level"] == "ERROR"

    def test_timestamp_is_iso8601(self, capture_logger):
        logger, handler = capture_logger
        logger.info("ts check")
        parsed = json.loads(handler.records[0])
        ts = parsed["timestamp"]
        # ISO 8601  T 
        assert "T" in ts

    def test_message_content(self, capture_logger):
        logger, handler = capture_logger
        logger.info("specific message")
        parsed = json.loads(handler.records[0])
        assert parsed["message"] == "specific message"

    def test_exception_included(self, capture_logger):
        logger, handler = capture_logger
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("caught error")
        parsed = json.loads(handler.records[0])
        assert "exception" in parsed
        assert "boom" in parsed["exception"]


# ---------------------------------------------------------------------------
# 2. trace_id 
# ---------------------------------------------------------------------------

class TestTraceIdPropagation:
    """set_trace_id  trace_id"""

    def test_trace_id_appears_in_log(self, capture_logger):
        logger, handler = capture_logger
        tid = "550e8400-e29b-41d4-a716-446655440000"
        set_trace_id(tid)
        logger.info("with trace")
        parsed = json.loads(handler.records[0])
        assert parsed["trace_id"] == tid

    def test_no_trace_id_when_unset(self, capture_logger):
        logger, handler = capture_logger
        logger.info("no trace")
        parsed = json.loads(handler.records[0])
        assert "trace_id" not in parsed

    def test_generate_trace_id_is_uuid4(self):
        tid = generate_trace_id()
        # UUID4 
        parsed = uuid.UUID(tid, version=4)
        assert str(parsed) == tid

    def test_get_trace_id_returns_set_value(self):
        tid = generate_trace_id()
        set_trace_id(tid)
        assert get_trace_id() == tid

    def test_get_trace_id_default_none(self):
        assert get_trace_id() is None


# ---------------------------------------------------------------------------
# 3. trace_id 
# ---------------------------------------------------------------------------

class TestTraceIdIsolation:
    """ asyncio  trace_id """

    @pytest.mark.asyncio
    async def test_async_tasks_have_independent_trace_ids(self):
        results: dict[str, str | None] = {}

        async def task_a():
            set_trace_id("trace-aaa")
            await asyncio.sleep(0.01)
            results["a"] = get_trace_id()

        async def task_b():
            set_trace_id("trace-bbb")
            await asyncio.sleep(0.01)
            results["b"] = get_trace_id()

        #  asyncio.create_task + copy_context 
        import contextvars
        ctx_a = contextvars.copy_context()
        ctx_b = contextvars.copy_context()

        loop = asyncio.get_event_loop()
        ta = loop.create_task(ctx_a.run(task_a))
        tb = loop.create_task(ctx_b.run(task_b))
        await asyncio.gather(ta, tb)

        assert results["a"] == "trace-aaa"
        assert results["b"] == "trace-bbb"

    @pytest.mark.asyncio
    async def test_parent_trace_id_not_affected_by_child(self):
        set_trace_id("parent-trace")

        import contextvars
        child_ctx = contextvars.copy_context()

        async def child():
            set_trace_id("child-trace")
            return get_trace_id()

        loop = asyncio.get_event_loop()
        t = loop.create_task(child_ctx.run(child))
        child_result = await t

        assert child_result == "child-trace"
        assert get_trace_id() == "parent-trace"


# ---------------------------------------------------------------------------
# 4. Helper 
# ---------------------------------------------------------------------------

@pytest.fixture()
def capture_helpers():
    """ _handler  handler handler

    helper  get_logger  _ensure_handler  handler
     handler  helper 
    """
    import app.utils.logger as logger_mod

    handler = _CaptureHandler()
    handler.setFormatter(JSONFormatter())

    old_handler = logger_mod._handler
    logger_mod._handler = handler

    #  bocai.* logger  handler handler
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("bocai."):
            lg = logging.getLogger(name)
            lg.handlers.clear()

    yield handler

    # 
    logger_mod._handler = old_handler
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("bocai."):
            lg = logging.getLogger(name)
            lg.handlers.clear()


class TestLogBet:
    def test_log_bet_fields(self, capture_helpers):
        log_bet(
            operator_id=1,
            account_id=10,
            issue="20250101001",
            key_code="DX1",
            amount=1000,
            result="success",
            duration_ms=123.4,
            trace_id="tid-bet-1",
        )
        assert len(capture_helpers.records) >= 1
        parsed = json.loads(capture_helpers.records[-1])
        assert parsed["action"] == "bet"
        assert parsed["operator_id"] == 1
        assert parsed["account_id"] == 10
        assert parsed["issue"] == "20250101001"
        assert parsed["key_code"] == "DX1"
        assert parsed["amount"] == 1000
        assert parsed["result"] == "success"
        assert parsed["duration_ms"] == 123.4
        assert parsed["trace_id"] == "tid-bet-1"

    def test_log_bet_without_duration(self, capture_helpers):
        log_bet(
            operator_id=1,
            account_id=10,
            issue="001",
            key_code="DX2",
            amount=500,
            result="fail",
        )
        parsed = json.loads(capture_helpers.records[-1])
        assert "duration_ms" not in parsed


class TestLogSettlement:
    def test_log_settlement_fields(self, capture_helpers):
        log_settlement(
            operator_id=2,
            account_id=20,
            issue="20250101002",
            key_code="DS3",
            is_win=1,
            pnl=2000,
            trace_id="tid-settle-1",
        )
        parsed = json.loads(capture_helpers.records[-1])
        assert parsed["action"] == "settlement"
        assert parsed["operator_id"] == 2
        assert parsed["account_id"] == 20
        assert parsed["is_win"] == 1
        assert parsed["pnl"] == 2000
        assert parsed["trace_id"] == "tid-settle-1"


class TestLogBalance:
    def test_log_balance_fields(self, capture_helpers):
        log_balance(
            operator_id=3,
            account_id=30,
            old_balance=100000,
            new_balance=98000,
            reason="bet_deducted",
            trace_id="tid-bal-1",
        )
        parsed = json.loads(capture_helpers.records[-1])
        assert parsed["action"] == "balance"
        assert parsed["old_balance"] == 100000
        assert parsed["new_balance"] == 98000
        assert parsed["reason"] == "bet_deducted"


class TestLogStrategyState:
    def test_log_strategy_state_fields(self, capture_helpers):
        log_strategy_state(
            operator_id=4,
            strategy_id=40,
            old_status="stopped",
            new_status="running",
            reason="user_start",
        )
        parsed = json.loads(capture_helpers.records[-1])
        assert parsed["action"] == "strategy_state"
        assert parsed["strategy_id"] == 40
        assert parsed["old_status"] == "stopped"
        assert parsed["new_status"] == "running"
        assert parsed["reason"] == "user_start"


class TestLogLogin:
    def test_log_login_success(self, capture_helpers):
        log_login(
            operator_id=5,
            account_id=50,
            success=True,
        )
        parsed = json.loads(capture_helpers.records[-1])
        assert parsed["action"] == "login"
        assert parsed["success"] is True
        assert parsed["level"] == "INFO"

    def test_log_login_failure(self, capture_helpers):
        log_login(
            operator_id=5,
            account_id=50,
            success=False,
            fail_reason="invalid_password",
            retry_count=2,
        )
        parsed = json.loads(capture_helpers.records[-1])
        assert parsed["action"] == "login"
        assert parsed["success"] is False
        assert parsed["fail_reason"] == "invalid_password"
        assert parsed["retry_count"] == 2
        assert parsed["level"] == "WARNING"


# ---------------------------------------------------------------------------
# 5. duration_ms 
# ---------------------------------------------------------------------------

class TestDurationField:
    def test_duration_included_when_provided(self, capture_helpers):
        log_bet(
            operator_id=1, account_id=1, issue="001",
            key_code="DX1", amount=100, result="ok",
            duration_ms=42.5,
        )
        parsed = json.loads(capture_helpers.records[-1])
        assert parsed["duration_ms"] == 42.5

    def test_duration_zero(self, capture_helpers):
        log_bet(
            operator_id=1, account_id=1, issue="001",
            key_code="DX1", amount=100, result="ok",
            duration_ms=0,
        )
        parsed = json.loads(capture_helpers.records[-1])
        assert parsed["duration_ms"] == 0


# ---------------------------------------------------------------------------
# 6. 
# ---------------------------------------------------------------------------

class TestContextFields:
    def test_operator_id_in_output(self, capture_helpers):
        log_bet(operator_id=99, account_id=1, issue="x", key_code="DX1", amount=1, result="ok")
        parsed = json.loads(capture_helpers.records[-1])
        assert parsed["operator_id"] == 99

    def test_account_id_in_output(self, capture_helpers):
        log_bet(operator_id=1, account_id=88, issue="x", key_code="DX1", amount=1, result="ok")
        parsed = json.loads(capture_helpers.records[-1])
        assert parsed["account_id"] == 88

    def test_issue_in_output(self, capture_helpers):
        log_bet(operator_id=1, account_id=1, issue="20250601123", key_code="DX1", amount=1, result="ok")
        parsed = json.loads(capture_helpers.records[-1])
        assert parsed["issue"] == "20250601123"


# ---------------------------------------------------------------------------
# 7. get_logger 
# ---------------------------------------------------------------------------

class TestGetLogger:
    def test_returns_logger_with_json_handler(self):
        logger = get_logger("test.factory")
        assert len(logger.handlers) >= 1
        assert isinstance(logger.handlers[0].formatter, JSONFormatter)

    def test_same_name_returns_same_logger(self):
        a = get_logger("test.same")
        b = get_logger("test.same")
        assert a is b

    def test_no_propagation(self):
        logger = get_logger("test.noprop")
        assert logger.propagate is False
