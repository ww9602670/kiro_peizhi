""" JSON 


- JSON  JSON 
- contextvars  trace_id 
-  helperlog_bet / log_settlement / log_balance / log_strategy_state / log_login
"""

import json
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional

# ---------------------------------------------------------------------------
# trace_id 
# ---------------------------------------------------------------------------

_trace_id_var: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)


def set_trace_id(trace_id: str) -> None:
    """ trace_id"""
    _trace_id_var.set(trace_id)


def get_trace_id() -> Optional[str]:
    """ trace_id None"""
    return _trace_id_var.get()


def generate_trace_id() -> str:
    """ UUID4 trace_id"""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# JSON Formatter
# ---------------------------------------------------------------------------

class JSONFormatter(logging.Formatter):
    """ LogRecord  JSON"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        #  trace_id
        trace_id = get_trace_id()
        if trace_id is not None:
            log_entry["trace_id"] = trace_id

        #  extra 
        if hasattr(record, "ctx") and isinstance(record.ctx, dict):
            log_entry.update(record.ctx)

        # 
        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Logger 
# ---------------------------------------------------------------------------

_handler: Optional[logging.Handler] = None


def _ensure_handler() -> logging.Handler:
    """ JSON handlerStreamHandler  stderr"""
    global _handler
    if _handler is None:
        _handler = logging.StreamHandler()
        _handler.setFormatter(JSONFormatter())
    return _handler


def get_logger(name: str) -> logging.Logger:
    """ JSON  logger"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.addHandler(_ensure_handler())
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
    return logger


# ---------------------------------------------------------------------------
#  HelperTask 16.1.2 
# ---------------------------------------------------------------------------

def _log_structured(
    action: str,
    *,
    level: int = logging.INFO,
    message: str = "",
    **fields: Any,
) -> dict[str, Any]:
    """"""
    logger = get_logger(f"bocai.{action}")
    ctx: dict[str, Any] = {"action": action}
    ctx.update({k: v for k, v in fields.items() if v is not None})
    logger.log(level, message or action, extra={"ctx": ctx})
    return ctx


def log_bet(
    operator_id: int,
    account_id: int,
    issue: str,
    key_code: str,
    amount: int,
    result: str,
    duration_ms: Optional[float] = None,
    trace_id: Optional[str] = None,
) -> dict[str, Any]:
    """/"""
    if trace_id is not None:
        set_trace_id(trace_id)
    return _log_structured(
        "bet",
        message=f" issue={issue} key_code={key_code} amount={amount} result={result}",
        operator_id=operator_id,
        account_id=account_id,
        issue=issue,
        key_code=key_code,
        amount=amount,
        result=result,
        duration_ms=duration_ms,
    )


def log_settlement(
    operator_id: int,
    account_id: int,
    issue: str,
    key_code: str,
    is_win: int,
    pnl: int,
    trace_id: Optional[str] = None,
) -> dict[str, Any]:
    """"""
    if trace_id is not None:
        set_trace_id(trace_id)
    return _log_structured(
        "settlement",
        message=f" issue={issue} key_code={key_code} is_win={is_win} pnl={pnl}",
        operator_id=operator_id,
        account_id=account_id,
        issue=issue,
        key_code=key_code,
        is_win=is_win,
        pnl=pnl,
    )


def log_balance(
    operator_id: int,
    account_id: int,
    old_balance: int,
    new_balance: int,
    reason: str,
    trace_id: Optional[str] = None,
) -> dict[str, Any]:
    """"""
    if trace_id is not None:
        set_trace_id(trace_id)
    return _log_structured(
        "balance",
        message=f" {old_balance}  {new_balance} reason={reason}",
        operator_id=operator_id,
        account_id=account_id,
        old_balance=old_balance,
        new_balance=new_balance,
        reason=reason,
    )


def log_strategy_state(
    operator_id: int,
    strategy_id: int,
    old_status: str,
    new_status: str,
    reason: str,
) -> dict[str, Any]:
    """"""
    return _log_structured(
        "strategy_state",
        message=f" {old_status}  {new_status} reason={reason}",
        operator_id=operator_id,
        strategy_id=strategy_id,
        old_status=old_status,
        new_status=new_status,
        reason=reason,
    )


def log_login(
    operator_id: int,
    account_id: int,
    success: bool,
    fail_reason: Optional[str] = None,
    retry_count: int = 0,
) -> dict[str, Any]:
    """"""
    result = "success" if success else "fail"
    return _log_structured(
        "login",
        level=logging.INFO if success else logging.WARNING,
        message=f" account_id={account_id} result={result}",
        operator_id=operator_id,
        account_id=account_id,
        success=success,
        fail_reason=fail_reason,
        retry_count=retry_count,
    )
