"""Exception boundary result models for canvas automation tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AutomationErrorCode(str, Enum):
    """High-level failures used by page state, visual and group actions."""

    PASS = "PASS"
    PRECHECK_FAILED = "PRECHECK_FAILED"
    CLICK_FAILED = "CLICK_FAILED"
    TARGET_MUTATED = "TARGET_MUTATED"
    WEBSOCKET_INTERRUPTED = "WEBSOCKET_INTERRUPTED"
    BALANCE_ASSERTION_FAILED = "BALANCE_ASSERTION_FAILED"
    MARKER_MISSING = "MARKER_MISSING"
    BATCH_SWITCHED = "BATCH_SWITCHED"
    COUNTDOWN_UNSAFE = "COUNTDOWN_UNSAFE"
    PAGE_FROZEN = "PAGE_FROZEN"
    MODAL_BLOCKING = "MODAL_BLOCKING"
    VALUE_DECOMPOSITION_FAILED = "VALUE_DECOMPOSITION_FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class AutomationCheckResult:
    """Uniform result for non-sensitive automation checks."""

    ok: bool
    code: AutomationErrorCode = AutomationErrorCode.PASS
    message: str = ""
    safe_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AutomationException(RuntimeError):
    """Machine-readable automation exception."""

    code: AutomationErrorCode
    message: str = ""
    safe_summary: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.code.value}: {self.message}" if self.message else self.code.value

