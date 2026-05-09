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
from app.engine.strategies.omission_random import (
    AiRandomFlatStrategy,
    AiRandomMartinStrategy,
    AiSameRandomFlatStrategy,
    AiSameRandomMartinStrategy,
    OmissionRandomFlatStrategy,
    OmissionRandomMartinStrategy,
)
from app.engine.strategies.random_martin import RandomMartinStrategy

__all__ = [
    "FlatStrategyImpl",
    "MartinStrategyImpl",
    "GreenWaveSingleMartinStrategy",
    "RedWaveDoubleMartinStrategy",
    "AiRandomFlatStrategy",
    "AiRandomMartinStrategy",
    "AiSameRandomFlatStrategy",
    "AiSameRandomMartinStrategy",
    "OmissionRandomFlatStrategy",
    "OmissionRandomMartinStrategy",
    "RandomMartinStrategy",
]
