"""

 + 
INTEGER1=100
"""

from typing import Optional

from app.engine.strategies.base import BaseStrategy, BetInstruction, StrategyContext
from app.engine.strategies.registry import register_strategy


@register_strategy("flat")
class FlatStrategyImpl(BaseStrategy):
    """ + 

    Args:
        key_codes:  ["DX1", "DS3"]
        base_amount: 
    """

    def __init__(self, key_codes: list[str], base_amount: int) -> None:
        if not key_codes:
            raise ValueError("key_codes ")
        if base_amount <= 0:
            raise ValueError("base_amount  0")
        self._key_codes = key_codes
        self._base_amount = base_amount

    def name(self) -> str:
        return "flat"

    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        """ key_code """
        return [
            BetInstruction(key_code=kc, amount=self._base_amount)
            for kc in self._key_codes
        ]

    def on_result(self, is_win: Optional[int], pnl: int) -> None:
        """on_result """
        pass
