"""API 

 RateLimiter  API 


1.  LIMITS  API 
2. 
3. API  10s (Confirmbet) 15s
4.  2  3sConfirmbet 
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


#  API 
READ_APIS: frozenset[str] = frozenset({
    "GetCurrentInstall",
    "Loaddata",
    "QueryResult",
    "Topbetlist",
    "Online",
})

#  API
BET_API: str = "Confirmbet"


class RateLimiter:
    """ API 

     RateLimiter 
    """

    # API None 
    LIMITS: dict[str, Optional[int]] = {
        "GetCurrentInstall": 5,     #  5  1 
        "Loaddata": 3,              #  3  1 
        "Confirmbet": None,         #  1  BetExecutor 
        "QueryResult": 10,          #  10  1 
        "Topbetlist": 10,           #  10  1 
        "Online": 75,               #  75  1 
    }

    # API 
    TIMEOUTS: dict[str, float] = {
        "Confirmbet": 15.0,         #  15s
    }
    DEFAULT_TIMEOUT: float = 10.0   #  10s

    # 
    MAX_RETRIES: int = 2            #  2 
    RETRY_INTERVAL: float = 3.0     #  3s

    def __init__(self) -> None:
        self._last_call: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def get_timeout(self, api_name: str) -> float:
        """ API """
        return self.TIMEOUTS.get(api_name, self.DEFAULT_TIMEOUT)

    def is_retryable(self, api_name: str) -> bool:
        """ API 

        Confirmbet 
        """
        return api_name in READ_APIS

    async def acquire(self, api_name: str) -> None:
        """ API

        
        """
        limit = self.LIMITS.get(api_name)
        if limit is None:
            return

        async with self._locks[api_name]:
            elapsed = time.monotonic() - self._last_call[api_name]
            if elapsed < limit:
                wait_time = limit - elapsed
                logger.debug(
                    "%s  %.2f ", api_name, wait_time
                )
                await asyncio.sleep(wait_time)
            self._last_call[api_name] = time.monotonic()

    async def execute(
        self,
        api_name: str,
        call: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """ API 

        Args:
            api_name: API  LIMITS 
            call: 
            *args, **kwargs:  call 

        Returns:
            API 

        Raises:
            asyncio.TimeoutError: 
            Exception: call 
        """
        # 1. 
        await self.acquire(api_name)

        timeout = self.get_timeout(api_name)
        retryable = self.is_retryable(api_name)
        max_attempts = (1 + self.MAX_RETRIES) if retryable else 1

        last_error: Optional[Exception] = None

        for attempt in range(max_attempts):
            try:
                return await asyncio.wait_for(
                    call(*args, **kwargs), timeout=timeout
                )
            except asyncio.TimeoutError as e:
                last_error = e
                if not retryable:
                    # Confirmbet 
                    logger.warning(
                        "%s %.1fs", api_name, timeout
                    )
                    raise
                if attempt < max_attempts - 1:
                    logger.warning(
                        "%s %.1fs %d  %d ",
                        api_name, timeout, attempt + 1, self.MAX_RETRIES,
                    )
                    await asyncio.sleep(self.RETRY_INTERVAL)
                else:
                    logger.error(
                        "%s  %d ", api_name, self.MAX_RETRIES
                    )

        # 
        raise last_error  # type: ignore[misc]
