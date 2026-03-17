"""

 @register_strategy 
 get_strategy_class / list_strategies 
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.engine.strategies.base import BaseStrategy

_STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {}


def register_strategy(name: str):
    """

     ValueError
    """
    def decorator(cls: type[BaseStrategy]) -> type[BaseStrategy]:
        if name in _STRATEGY_REGISTRY:
            raise ValueError(
                f" '{name}'  {_STRATEGY_REGISTRY[name].__name__}"
                f""
            )
        _STRATEGY_REGISTRY[name] = cls
        return cls
    return decorator


def get_strategy_class(name: str) -> type[BaseStrategy]:
    """ KeyError"""
    if name not in _STRATEGY_REGISTRY:
        raise KeyError(f" '{name}' ")
    return _STRATEGY_REGISTRY[name]


def list_strategies() -> list[str]:
    """"""
    return list(_STRATEGY_REGISTRY.keys())


def _clear_registry() -> None:
    """"""
    _STRATEGY_REGISTRY.clear()
