"""Account API schemas."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.utils.platform_url import normalize_platform_url

PlatformType = Literal["JND28WEB", "JND282", "LUCKYSB"]
GameType = Literal["JND28", "LUCKYSB"]
FrontendSignal = Literal["normal", "processing", "need_relogin", "need_confirm_odds"]

GAME_TYPE_ALLOWED_PLATFORM_TYPES: dict[str, tuple[str, ...]] = {
    "JND28": ("JND28WEB", "JND282"),
    "LUCKYSB": ("LUCKYSB",),
}
LEGACY_PLATFORM_TO_GAME_TYPE: dict[str, str] = {
    "JND28WEB": "JND28",
    "JND282": "JND28",
    "LUCKYSB": "LUCKYSB",
}


def get_allowed_platform_types(game_type: str) -> list[str]:
    return list(GAME_TYPE_ALLOWED_PLATFORM_TYPES.get((game_type or "").upper(), ()))


def get_default_platform_type(game_type: str) -> str:
    allowed = get_allowed_platform_types(game_type)
    if not allowed:
        raise ValueError(f"Unsupported game_type: {game_type}")
    return allowed[0]


def normalize_game_type(
    game_type: str | None = None,
    legacy_platform_type: str | None = None,
) -> str:
    normalized_game_type = (game_type or "").strip().upper()
    if normalized_game_type:
        if normalized_game_type not in GAME_TYPE_ALLOWED_PLATFORM_TYPES:
            raise ValueError(f"Unsupported game_type: {normalized_game_type}")
        return normalized_game_type

    normalized_platform_type = (legacy_platform_type or "").strip().upper()
    mapped = LEGACY_PLATFORM_TO_GAME_TYPE.get(normalized_platform_type)
    if not mapped:
        raise ValueError("game_type is required")
    return mapped


def mask_password(password: str) -> str:
    if len(password) < 2:
        return "****"
    return password[:2] + "****"


class AccountCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_name": "player001",
                "password": "mypassword",
                "game_type": "JND28",
                "platform_url": "https://example.com",
            }
        }
    )

    account_name: str = Field(..., min_length=1, description="账号名")
    password: str = Field(..., min_length=1, description="密码")
    game_type: Optional[GameType] = Field(default=None, description="游戏类型")
    # Legacy compatibility only. Account binding no longer pre-binds platform type.
    platform_type: Optional[PlatformType] = Field(default=None, exclude=True)
    platform_url: Optional[str] = Field(default=None, description="平台地址")

    @model_validator(mode="after")
    def validate_platform_url(self):
        self.game_type = normalize_game_type(self.game_type, self.platform_type)
        platform_url = normalize_platform_url(self.platform_url)
        if not platform_url:
            raise ValueError("平台地址格式错误，请使用 http:// 或 https:// 开头的完整网址")
        self.platform_url = platform_url
        self.platform_type = None
        return self


class AccountInfo(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 1,
                "account_name": "player001",
                "password_masked": "my****",
                "game_type": "JND28",
                "allowed_strategy_platform_types": ["JND28WEB"],
                "allowed_strategy_types": ["flat", "martin"],
                "platform_capabilities": [
                    {
                        "platform_type": "JND28WEB",
                        "verify_status": "supported",
                        "market_state": "open",
                        "detected_issue": None,
                        "odds_synced": True,
                        "odds_message": "odds synced: 2 items",
                        "last_verified_at": "2026-01-01 12:00:00",
                    }
                ],
                "latest_verification_run_id": 10,
                "effective_verification_run_id": 10,
                "verification_in_progress": False,
                "verification_stale": False,
                "summary_status_reason": None,
                "frontend_signal": "normal",
                "frontend_signal_reason": None,
                "status": "inactive",
                "balance": 0.0,
                "kill_switch": False,
                "last_login_at": None,
                "odds_synced": False,
                "odds_count": 0,
                "odds_message": "账号已绑定，请先登录并同步赔率",
            }
        }
    )

    class PlatformCapability(BaseModel):
        platform_type: str
        verify_status: str
        market_state: str
        detected_issue: Optional[str] = None
        odds_synced: bool = False
        odds_message: Optional[str] = None
        last_verified_at: Optional[str] = None

    id: int
    account_name: str
    password_masked: str
    game_type: str
    allowed_strategy_platform_types: list[str]
    allowed_strategy_types: list[str] = Field(default_factory=list)
    platform_capabilities: list[PlatformCapability] = Field(default_factory=list)
    latest_verification_run_id: Optional[int] = None
    effective_verification_run_id: Optional[int] = None
    verification_in_progress: bool = False
    verification_stale: bool = False
    summary_status_reason: Optional[str] = None
    frontend_signal: FrontendSignal = Field(
        default="normal",
        description="front-end simplified status signal",
    )
    frontend_signal_reason: Optional[str] = Field(
        default=None,
        description="machine-readable reason for frontend_signal",
    )
    platform_url: Optional[str] = None
    status: str
    balance: float
    kill_switch: bool
    last_login_at: Optional[str] = None
    odds_synced: Optional[bool] = Field(default=None, description="最近一次赔率同步结果")
    odds_count: Optional[int] = Field(default=None, description="最近一次获取到的非零赔率数量")
    odds_message: Optional[str] = Field(default=None, description="最近一次赔率状态提示")


class KillSwitchUpdate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"enabled": True}})

    enabled: bool = Field(..., description="true=启用, false=关闭")
