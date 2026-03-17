"""

 BaseStrategy ABC
INTEGER1=100
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


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

    def on_result(self, is_win: Optional[int], pnl: int) -> None:
        """
        
        is_win: 1=, 0=, -1=
        pnl: 
        
        """
        pass
