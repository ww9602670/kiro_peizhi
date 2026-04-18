"""

 BaseStrategy ABC
INTEGER1=100
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class LotteryResult:
    """"""
    issue: str
    balls: list[int]
    sum_value: int


@dataclass
class StrategyContext:
    """"""
    current_issue: str
    history: list[LotteryResult]  #  N 
    balance: int  # 
    strategy_state: dict = field(default_factory=dict)  # 


@dataclass
class BetInstruction:
    """"""
    key_code: str
    amount: int  # 
    martin_level: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyStopRequest:
    """Settlement feedback stop request from strategy."""
    should_stop: bool = False
    reason: str = ""


class BaseStrategy(ABC):
    """"""

    @abstractmethod
    def name(self) -> str:
        """"""
        ...

    @abstractmethod
    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        """"""
        ...

    def on_result(
        self,
        is_win: Optional[int],
        pnl: int,
        key_code: str | None = None,
        martin_level: int | None = None,
    ) -> Optional[StrategyStopRequest]:
        """
        
        is_win: 1=, 0=, -1=
        pnl: 
        
        """
        return None
