""" BaseStrategy BetSignal

StrategyRunner 
-  BaseStrategy 
- running/paused/stopped/error
-  running  strategy.compute() 
-  BetInstruction  BetSignal idempotent_idmartin_levelsimulation
-  error 

INTEGER1=100
idempotent_id {issue}-{strategy_id}-{key_code}key_code 
"""

from __future__ import annotations

import logging
import inspect
from dataclasses import dataclass

from app.engine.strategies.base import (
    BaseStrategy,
    BetInstruction,
    StrategyContext,
    StrategyStopRequest,
)

logger = logging.getLogger(__name__)


@dataclass
class BetSignal:
    """ StrategyRunner  BetExecutor """

    strategy_id: int
    key_code: str
    amount: int  # 
    idempotent_id: str  # {issue}-{strategy_id}-{key_code}
    martin_level: int = 0
    simulation: bool = False


class StrategyRunner:
    """ BaseStrategy BetSignal"""

    VALID_STATUSES = {"running", "paused", "stopped", "error"}

    def __init__(
        self,
        strategy_id: int,
        strategy: BaseStrategy,
        simulation: bool = False,
    ) -> None:
        self.strategy_id = strategy_id
        self.strategy = strategy
        self.simulation = simulation
        self.status: str = "stopped"

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    def start(self) -> None:
        """stopped/paused/error  running"""
        if self.status not in ("stopped", "paused", "error"):
            raise ValueError(
                f" {self.status}  stopped/paused/error "
            )
        self.status = "running"

    def pause(self) -> None:
        """running  paused"""
        if self.status != "running":
            raise ValueError(f" {self.status}  running ")
        self.status = "paused"

    def stop(self) -> None:
        """  stopped"""
        self.status = "stopped"

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    def collect_signals(
        self, ctx: StrategyContext, issue: str
    ) -> list[BetSignal]:
        """ running """
        if self.status != "running":
            return []

        try:
            instructions = self.strategy.compute(ctx)
            return [self._to_signal(inst, issue) for inst in instructions]
        except Exception:
            self.status = "error"
            logger.error(
                "strategy_id=%dissue=%s error",
                self.strategy_id,
                issue,
                exc_info=True,
            )
            return []

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    async def on_result(
        self,
        is_win: int | None,
        pnl: int,
        key_code: str | None = None,
        martin_level: int | None = None,
    ) -> StrategyStopRequest | None:
        """ flush_alerts"""
        kwargs = {"key_code": key_code}
        try:
            params = inspect.signature(self.strategy.on_result).parameters
            accepts_martin_level = "martin_level" in params or any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in params.values()
            )
        except (TypeError, ValueError):
            accepts_martin_level = True
        if accepts_martin_level and martin_level is not None:
            kwargs["martin_level"] = martin_level

        stop_request = self.strategy.on_result(
            is_win,
            pnl,
            **kwargs,
        )

        #  flush_alerts
        if hasattr(self.strategy, "flush_alerts"):
            await self.strategy.flush_alerts()

        if isinstance(stop_request, StrategyStopRequest):
            return stop_request
        return None

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    def _to_signal(self, inst: BetInstruction, issue: str) -> BetSignal:
        """ BetInstruction  BetSignal"""
        key_code_upper = inst.key_code.upper()
        martin_level = (
            inst.martin_level
            if inst.martin_level is not None
            else getattr(self.strategy, "level", 0)
        )
        return BetSignal(
            strategy_id=self.strategy_id,
            key_code=key_code_upper,
            amount=inst.amount,
            idempotent_id=f"{issue}-{self.strategy_id}-{key_code_upper}",
            martin_level=martin_level,
            simulation=self.simulation,
        )
