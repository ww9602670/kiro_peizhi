"""Platform adapter factory."""
from __future__ import annotations

from typing import Optional

from app.engine.adapters.base import (
    BalanceInfo,
    BetResult,
    InstallInfo,
    LoginResult,
    PlatformAdapter,
)
from app.engine.adapters.jnd import JNDAdapter

JND_PLATFORM_TYPES = frozenset({"JND28WEB", "JND282"})
LUCKYSB_PLATFORM_TYPE = "LUCKYSB"
SUPPORTED_PLATFORM_TYPES = (*sorted(JND_PLATFORM_TYPES), LUCKYSB_PLATFORM_TYPE)


class MemberSiteAdapter(PlatformAdapter):
    """Explicit LUCKYSB placeholder until the member-site adapter lands."""

    def __init__(self, *, base_url: Optional[str] = None, platform_type: str = LUCKYSB_PLATFORM_TYPE) -> None:
        self.base_url = base_url
        self.platform_type = platform_type

    async def login(
        self,
        account_name: str,
        password: str,
        captcha_code: Optional[str] = None,
    ) -> LoginResult:
        return LoginResult(
            success=False,
            message="LUCKYSB MemberSiteAdapter is not implemented yet",
        )

    async def get_current_install(self) -> InstallInfo:
        raise NotImplementedError("LUCKYSB MemberSiteAdapter is not implemented yet")

    async def get_current_install_detail(self) -> dict:
        raise NotImplementedError("LUCKYSB MemberSiteAdapter is not implemented yet")

    async def load_odds(self, issue: str) -> dict[str, int]:
        raise NotImplementedError("LUCKYSB MemberSiteAdapter is not implemented yet")

    async def place_bet(self, issue: str, betdata: list[dict]) -> BetResult:
        raise NotImplementedError("LUCKYSB MemberSiteAdapter is not implemented yet")

    async def query_balance(self) -> BalanceInfo:
        raise NotImplementedError("LUCKYSB MemberSiteAdapter is not implemented yet")

    async def get_bet_history(self, count: int = 15) -> list[dict]:
        raise NotImplementedError("LUCKYSB MemberSiteAdapter is not implemented yet")

    async def get_lottery_results(self, count: int = 10) -> list[dict]:
        raise NotImplementedError("LUCKYSB MemberSiteAdapter is not implemented yet")

    async def heartbeat(self) -> bool:
        raise NotImplementedError("LUCKYSB MemberSiteAdapter is not implemented yet")

    async def close(self) -> None:
        return None


def _create_luckysb_adapter(platform_url: Optional[str]) -> PlatformAdapter:
    try:
        from app.engine.adapters.member_site import MemberSiteAdapter as RealMemberSiteAdapter
    except ModuleNotFoundError:
        return MemberSiteAdapter(base_url=platform_url or None)

    return RealMemberSiteAdapter(base_url=platform_url or None, platform_type=LUCKYSB_PLATFORM_TYPE)


def create_platform_adapter(platform_type: str, platform_url: Optional[str] = None) -> PlatformAdapter:
    """Create the adapter for a supported platform type."""
    normalized = (platform_type or "JND28WEB").upper()

    if normalized in JND_PLATFORM_TYPES:
        return JNDAdapter(
            base_url=platform_url or None,
            platform_type=normalized,
        )

    if normalized == LUCKYSB_PLATFORM_TYPE:
        return _create_luckysb_adapter(platform_url)

    raise ValueError(f"Unsupported platform type: {platform_type}")
