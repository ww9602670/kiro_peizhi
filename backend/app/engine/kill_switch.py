"""

 EngineManager Phase 10
RiskController  API 
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 
_global_kill: bool = False


def get_global_kill() -> bool:
    """"""
    return _global_kill


def set_global_kill(enabled: bool) -> None:
    """"""
    global _global_kill
    _global_kill = enabled
    logger.info("enabled=%s", enabled)
