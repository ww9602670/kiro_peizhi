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

        
        - ""  ODDS_CHANGED
        - "" / ""  CLOSED
        - "" / ""  INSTALLMENTS_MISMATCH
        -   UNKNOWN
        """
        msg = self.message.lower()
        if "" in msg and "" in msg:
            return "ODDS_CHANGED"
        if "" in msg or "" in msg:
            return "CLOSED"
        if "" in msg:
            return "INSTALLMENTS_MISMATCH"
        return "UNKNOWN"

    @property
    def is_retryable(self) -> bool:
        """

        ODDS_CHANGED, CLOSED, INSTALLMENTS_MISMATCH
        
        """
        return self.error_code not in ["ODDS_CHANGED", "CLOSED", "INSTALLMENTS_MISMATCH"]


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
