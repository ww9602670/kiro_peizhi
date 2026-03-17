"""JND28  JND28WEB  JND282 """
from __future__ import annotations

import logging
import math
from typing import Any, Optional
from urllib.parse import quote

import aiohttp
from yarl import URL

from app.engine.adapters.base import (
    BalanceInfo,
    BetResult,
    InstallInfo,
    LoginResult,
    PlatformAdapter,
)
from app.engine.adapters.config import DEFAULT_HEADERS, MID_CODES

logger = logging.getLogger(__name__)


class JNDAdapter(PlatformAdapter):
    """JND28 

     aiohttp  HTTP API 
     JND28WEB JND2822.0
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        lottery_type: Optional[str] = None,
        session: Optional[aiohttp.ClientSession] = None,
        *,
        platform_type: Optional[str] = None,
    ) -> None:
        # 
        # 1. JNDAdapter(base_url="...", lottery_type="JND28WEB")
        # 2. JNDAdapter(platform_type="JND28WEB")    PLATFORM_CONFIGS 
        # 3. JNDAdapter(base_url="...", platform_type="JND28WEB")  base_url 优先
        if platform_type and not base_url:
            from app.engine.adapters.config import PLATFORM_CONFIGS
            cfg = PLATFORM_CONFIGS.get(platform_type, {})
            base_url = cfg.get("base_url", "")
            lottery_type = cfg.get("lottery_type", platform_type)
        elif platform_type and base_url:
            from app.engine.adapters.config import PLATFORM_CONFIGS
            cfg = PLATFORM_CONFIGS.get(platform_type, {})
            lottery_type = lottery_type or cfg.get("lottery_type", platform_type)

        self.base_url = (base_url or "").rstrip("/")
        self.lottery_type = lottery_type or "JND28WEB"
        self._session = session
        self._token: Optional[str] = None

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """ aiohttp session """
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=DEFAULT_HEADERS)
        return self._session

    async def close(self) -> None:
        """ HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _post(
        self,
        url: str,
        data: Optional[dict[str, str]] = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """ POST  JSON 

        Raises:
            aiohttp.ClientError: /HTTP 
            ValueError:  JSON
        """
        session = await self._ensure_session()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            async with session.post(
                url,
                data=data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ContentTypeError:
            #  content-type
            async with session.post(
                url,
                data=data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                text = await resp.text()
                raise ValueError(f" JSON : {text[:200]}")

    # ------------------------------------------------------------------
    # PlatformAdapter 
    # ------------------------------------------------------------------

    async def login(self, account_name: str, password: str, captcha_code: Optional[str] = None) -> LoginResult:
        """

         VisitorLogin  token cookie
         txtName/txtPwd/txtVerify
         test166 平台不需要验证码，captcha_code 参数被忽略
        
        """
        session = await self._ensure_session()
        url = f"{self.base_url}/Member/VisitorLogin"
        try:
            async with session.get(
                url,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                #  cookies  token
                cookies = session.cookie_jar.filter_cookies(URL(self.base_url))
                token_value = None
                for key, cookie in cookies.items():
                    if key.lower() == "token":
                        token_value = cookie.value
                        break

                if token_value:
                    self._token = token_value
                    return LoginResult(
                        success=True,
                        token=token_value,
                        message="",
                    )
                else:
                    return LoginResult(
                        success=False,
                        message=" token cookie",
                    )
        except aiohttp.ClientError as e:
            logger.error(": %s", e)
            return LoginResult(
                success=False,
                message=f": {e}",
            )

    async def get_current_install(self) -> InstallInfo:
        """"""
        url = (
            f"{self.base_url}/PlaceBet/GetCurrentInstall"
            f"?lotteryType={self.lottery_type}"
        )
        data = await self._post(url)
        
        #  0
        raw_state = int(data.get("State", 0))
        normalized_state = raw_state if raw_state in [1, 2, 3] else 0
        
        return InstallInfo(
            issue=str(data["Installments"]),
            state=normalized_state,
            close_countdown_sec=int(data["CloseTimeStamp"]),
            pre_issue=str(data.get("PreInstallments", "")),
            pre_result=str(data.get("PreLotteryResult", "")),
            open_countdown_sec=int(data.get("OpenTimeStamp", 0)),
        )

    async def get_current_install_detail(self) -> dict:
        """

         authenticated aiohttp session httpx client

        Returns:
            {
                "installments": "3403606",
                "state": 1,  # 1=2=3=0=
                "close_countdown_sec": 149,  # 
                "open_countdown_sec": 159,   # 
                "pre_lottery_result": "0,3,0",
                "pre_installments": "3403605",
                "template_code": "JNDPCDD"
            }
        """
        url = f"{self.base_url}/PlaceBet/GetCurrentInstall?lotteryType={self.lottery_type}"

        #  authenticated session
        data = await self._post(url)

        #  0
        raw_state = data.get("State", 0)
        normalized_state = raw_state if raw_state in [1, 2, 3] else 0

        return {
            "installments": str(data.get("Installments", "")),
            "state": normalized_state,  # 
            "close_countdown_sec": int(data.get("CloseTimeStamp", 0)),
            "open_countdown_sec": int(data.get("OpenTimeStamp", 0)),
            "pre_lottery_result": str(data.get("PreLotteryResult", "")),
            "pre_installments": str(data.get("PreInstallments", "")),
            "template_code": str(data.get("TemplateCode", "")),
        }

    async def load_odds(self, issue: str) -> dict[str, int]:
        """

        Returns:
            KeyCode → odds (10000 倍整数)
        """
        url = (
            f"{self.base_url}/PlaceBet/Loaddata"
            f"?lotteryType={self.lottery_type}"
        )
        form_data = {
            "itype": "-1",
            "midCode": MID_CODES,
            "oddstype": "A",
            "lotteryType": self.lottery_type,
            "install": issue,
        }
        resp = await self._post(url, data=form_data)

        #  dict data 
        odds_raw: dict[str, Any] = resp if isinstance(resp, dict) else {}
        if "data" in odds_raw and isinstance(odds_raw["data"], dict):
            odds_raw = odds_raw["data"]

        # 转为 10000 倍整数存储（保留4位小数精度）
        odds: dict[str, int] = {}
        for key, value in odds_raw.items():
            try:
                float_val = float(value)
                odds[key] = round(float_val * 10000)
            except (ValueError, TypeError):
                continue

        return odds

    async def place_bet(self, issue: str, betdata: list[dict]) -> BetResult:
        """

        Args:
            issue: 期号
            betdata: 列表 Amount(分)/KeyCode/Odds(10000倍整数)

        说明:
            - Amount: 分，100 = 1元
            - Odds: 10000倍整数，如 19834 表示赔率 1.9834
        """
        url = f"{self.base_url}/PlaceBet/Confirmbet"
        form_data: dict[str, str] = {}
        for i, bet in enumerate(betdata):
            # 分转元，10000倍整数转浮点
            amount_yuan = bet["Amount"] / 100
            odds_float = bet["Odds"] / 10000
            form_data[f"betdata[{i}][Amount]"] = str(int(amount_yuan)) if amount_yuan == int(amount_yuan) else str(amount_yuan)
            form_data[f"betdata[{i}][KeyCode]"] = str(bet["KeyCode"])
            form_data[f"betdata[{i}][Odds]"] = str(odds_float)
        form_data["lotteryType"] = self.lottery_type
        form_data["install"] = issue

        try:
            resp = await self._post(url, data=form_data, timeout=15.0)
            return BetResult(
                succeed=int(resp.get("succeed", 0)),
                message=str(resp.get("msg", "")),
                raw_response=resp,
            )
        except Exception as e:
            logger.error(": %s", e)
            return BetResult(
                succeed=0,
                message=f": {e}",
                raw_response={},
            )

    async def query_balance(self) -> BalanceInfo:
        """"""
        url = (
            f"{self.base_url}/PlaceBet/QueryResult"
            f"?lotteryType={self.lottery_type}"
        )
        resp = await self._post(url)
        balance = float(resp.get("accountLimit", 0))
        return BalanceInfo(
            balance=balance,
            raw_response=resp,
        )

    async def get_bet_history(self, count: int = 15) -> list[dict]:
        """获取已结算投注记录（BettingList/getBetChecked）

        返回包含完整字段的已结算记录列表，包括：
        KeyCode, Installments, OddNo, BettingAmount, Result, Finished 等。
        """
        url = f"{self.base_url}/BettingList/getBetChecked"
        form_data = {
            "startIndex": "0",
            "rows": str(count),
        }
        resp = await self._post(url, data=form_data)
        if isinstance(resp, dict):
            bet_list = resp.get("betList", [])
            if isinstance(bet_list, list):
                return bet_list
        return []

    async def get_lottery_results(self, count: int = 10) -> list[dict]:
        """"""
        url = (
            f"{self.base_url}/ResultHistory/Lotteryresult"
            f"?lotterytype={self.lottery_type}"
        )
        form_data = {
            "start": "1",
            "rows": str(count),
            "query": "",
        }
        resp = await self._post(url, data=form_data)
        if isinstance(resp, dict):
            data = resp.get("data", {})
            if isinstance(data, dict):
                return data.get("Records", [])
            if isinstance(data, list):
                return data
        return []

    async def heartbeat(self) -> bool:
        """"""
        url = f"{self.base_url}/Member/Online"
        try:
            resp = await self._post(url, timeout=10.0)
            return resp.get("State") == 1
        except Exception as e:
            logger.warning(": %s", e)
            return False
