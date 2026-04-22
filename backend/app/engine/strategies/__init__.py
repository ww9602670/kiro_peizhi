"""

 @register_strategy 
"""

# 
from app.engine.strategies.flat import FlatStrategyImpl
from app.engine.strategies.martin import MartinStrategyImpl
from app.engine.strategies.red_wave_double import (
    GreenWaveSingleMartinStrategy,
    RedWaveDoubleMartinStrategy,
)

__all__ = [
    "FlatStrategyImpl",
    "MartinStrategyImpl",
    "GreenWaveSingleMartinStrategy",
    "RedWaveDoubleMartinStrategy",
]
