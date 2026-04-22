""""""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class InstallInfo:
    """"""

    issue: str  # 
    state: int  # 1=, 2=, 3=, 0=
    close_countdown_sec: int  # 
    pre_issue: str  # 
    pre_result: str  #  "b1,b2,b3"
    is_new_issue: bool = False  # 
    open_countdown_sec: int = 0  # 

    # 
    @property
    def close_timestamp(self) -> int:
        """close_timestamp  close_countdown_sec"""
        return self.close_countdown_sec

    @property
    def open_timestamp(self) -> int:
        """open_timestamp  open_countdown_sec"""
        return self.open_countdown_sec


@dataclass
class BetResult:
    """"""

    succeed: int  # 1=, 0=
    message: str  # 
    raw_response: dict = field(default_factory=dict)

    @property
    def error_code(self) -> str:
        """ message  raw_response 

        Known categories:
        - odds changed -> ODDS_CHANGED
        - closed/stop betting -> CLOSED
        - issue mismatch -> INSTALLMENTS_MISMATCH
        - everything else -> UNKNOWN
        """
        if self.succeed == 5:
            return "ODDS_CHANGED"

        msg = (self.message or "").lower()
        if "赔率" in msg or "odds" in msg:
            return "ODDS_CHANGED"
        if "封盘" in msg or "截止" in msg or "closed" in msg:
            return "CLOSED"
        if "期号" in msg or "installments" in msg or "mismatch" in msg:
            return "INSTALLMENTS_MISMATCH"
        return "UNKNOWN"

    @property
    def is_retryable(self) -> bool:
        """

        Confirmbet may only do one controlled retry for explicit
        odds-changed responses.
        """
        return self.error_code == "ODDS_CHANGED"


@dataclass
class BalanceInfo:
    """"""
    balance: float            # 
    raw_response: dict = field(default_factory=dict)


@dataclass
class LoginResult:
    """"""
    success: bool
    token: Optional[str] = None
    message: str = ""
    captcha_required: bool = False


class RemoteLoginRequired(RuntimeError):
    """Raised when the platform indicates the current session is no longer valid."""

    def __init__(self, *, raw_state: int, message: str = "") -> None:
        self.raw_state = raw_state
        super().__init__(message or f"remote login required, state={raw_state}")


class PlatformAdapter(ABC):
    """

    
    """

    @abstractmethod
    async def login(self, account_name: str, password: str, captcha_code: Optional[str] = None) -> LoginResult:
        """"""
        ...

    @abstractmethod
    async def get_current_install(self) -> InstallInfo:
        """"""
        ...

    @abstractmethod
    async def get_current_install_detail(self) -> dict:
        """

        Returns:
            {
                "installments": "3403606",
                "state": 1,  # 1=2=3=0=
                "close_countdown_sec": 149,
                "open_countdown_sec": 159,
                "pre_lottery_result": "0,3,0",
                "pre_installments": "3403605",
                "template_code": "JNDPCDD"
            }
        """
        ...

    @abstractmethod
    async def load_odds(self, issue: str) -> dict[str, int]:
        """

        Returns:
            KeyCode → odds (10000 倍整数，如 2.053 → 20530)
        """
        ...

    @abstractmethod
    async def place_bet(self, issue: str, betdata: list[dict]) -> BetResult:
        """"""
        ...

    @abstractmethod
    async def query_balance(self) -> BalanceInfo:
        """"""
        ...

    @abstractmethod
    async def get_bet_history(self, count: int = 15) -> list[dict]:
        """"""
        ...

    @abstractmethod
    async def get_lottery_results(self, count: int = 10) -> list[dict]:
        """"""
        ...

    @abstractmethod
    async def heartbeat(self) -> bool:
        """"""
        ...
