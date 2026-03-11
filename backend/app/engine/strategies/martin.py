"""


- compute():  base_amount  sequence[level] 
- on_result(): winlevel=0, loselevel+1, refund
- level   0 +  + martin_reset 

INTEGER1=100
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from app.engine.strategies.base import BaseStrategy, BetInstruction, StrategyContext
from app.engine.strategies.registry import register_strategy

logger = logging.getLogger(__name__)


@dataclass
class PendingAlert:
    """ on_result StrategyRunner"""
    alert_type: str
    title: str
    detail: str


@register_strategy("martin")
class MartinStrategyImpl(BaseStrategy):
    """

    Args:
        key_codes:  ["DX1"]
        base_amount: 
        sequence:  [1, 2, 4, 8, 16]
        alert_service: AlertService  martin_reset 
        operator_id:  ID
        strategy_name: /
    """

    def __init__(
        self,
        key_codes: list[str],
        base_amount: int,
        sequence: list[int | float],
        alert_service=None,
        operator_id: int = 0,
        strategy_name: str = "",
    ) -> None:
        if not key_codes:
            raise ValueError("key_codes ")
        if base_amount <= 0:
            raise ValueError("base_amount  0")
        if not sequence or len(sequence) == 0:
            raise ValueError("sequence ")
        if any(s <= 0 for s in sequence):
            raise ValueError("sequence  0")

        self._key_codes = key_codes
        self._base_amount = base_amount
        self._sequence = list(sequence)
        self._level: int = 0
        self._round_loss: int = 0  # 
        self._alert_service = alert_service
        self._operator_id = operator_id
        self._strategy_name = strategy_name or "martin"

        # on_result 
        self._pending_alerts: list[PendingAlert] = []

    def name(self) -> str:
        return "martin"

    @property
    def level(self) -> int:
        """0-based"""
        return self._level

    @property
    def round_loss(self) -> int:
        """"""
        return self._round_loss

    @property
    def pending_alerts(self) -> list[PendingAlert]:
        """"""
        return self._pending_alerts

    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        """base_amount  sequence[level]"""
        multiplier = self._sequence[self._level]
        amount = int(self._base_amount * multiplier)
        return [
            BetInstruction(key_code=kc, amount=amount)
            for kc in self._key_codes
        ]

    def on_result(self, is_win: Optional[int], pnl: int) -> None:
        """

        is_win=1    level=0 round_loss
        is_win=0   level+1 round_loss+
        is_win=-1  level round_loss 
        is_win=None  
        """
        if is_win == 1:
            #   
            self._level = 0
            self._round_loss = 0
        elif is_win == 0:
            #   
            self._round_loss += abs(pnl)
            next_level = self._level + 1
            if next_level >= len(self._sequence):
                #    +  + 
                logger.warning(
                    "=%soperator_id=%d"
                    "=%d=%d",
                    self._strategy_name,
                    self._operator_id,
                    len(self._sequence),
                    self._round_loss,
                )
                self._pending_alerts.append(
                    PendingAlert(
                        alert_type="martin_reset",
                        title=f"{self._strategy_name}",
                        detail=(
                            f"{self._strategy_name}"
                            f" {len(self._sequence)}"
                            f" {self._round_loss} "
                            f"{self._round_loss / 100:.2f} "
                            f" 1 "
                        ),
                    )
                )
                self._level = 0
                self._round_loss = 0
            else:
                self._level = next_level
        # is_win == -1 ()  is_win is None  

    async def flush_alerts(self) -> None:
        """"""
        if not self._alert_service or not self._pending_alerts:
            self._pending_alerts.clear()
            return

        for alert in self._pending_alerts:
            await self._alert_service.send(
                operator_id=self._operator_id,
                alert_type=alert.alert_type,
                title=alert.title,
                detail=alert.detail,
            )
        self._pending_alerts.clear()
