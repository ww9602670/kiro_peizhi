"""JND28  JND28WEB  JND282 """
from __future__ import annotations

import logging
import math
import json
import time
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
    RemoteLoginRequired,
)
from app.engine.adapters.config import DEFAULT_HEADERS, MID_CODES
from app.engine.adapters.jnd_dw3_profiles import get_jnd_dw3_profile

logger = logging.getLogger(__name__)


class InvalidInstallResponse(RuntimeError):
    """Raised when current-install response is missing required fields."""


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
        if platform_type and not base_url:
            from app.engine.adapters.config import PLATFORM_CONFIGS
            cfg = PLATFORM_CONFIGS.get(platform_type, {})
            base_url = cfg.get("base_url", "")
            lottery_type = cfg.get("lottery_type", platform_type)
        self.base_url = (base_url or "").rstrip("/")
        self.lottery_type = lottery_type or platform_type or "JND28WEB"
        self._session = session
        self._token: Optional[str] = None

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """Parse int from heterogeneous values with a fallback."""
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return default
            return int(value)
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                try:
                    parsed = float(raw)
                except ValueError:
                    return default
                if math.isnan(parsed) or math.isinf(parsed):
                    return default
                return int(parsed)
        return default

    @classmethod
    def _non_negative_int(cls, value: Any, default: int = 0) -> int:
        return max(0, cls._safe_int(value, default))

    @staticmethod
    def _safe_text(value: Any) -> str:
        return "" if value is None else str(value)

    @staticmethod
    def _format_amount_yuan(amount_fen: int) -> str:
        whole, cents = divmod(int(amount_fen), 100)
        if cents == 0:
            return str(whole)
        return f"{amount_fen / 100:.2f}".rstrip("0").rstrip(".")

    @staticmethod
    def _format_odds_value(odds_scaled: int) -> str:
        return f"{int(odds_scaled) / 10000:.4f}".rstrip("0").rstrip(".")

    @staticmethod
    def _make_bet_num() -> str:
        return str(time.time_ns())[-15:]

    @classmethod
    def _normalize_state(cls, value: Any) -> int:
        state = cls._safe_int(value, 0)
        return state if state in (1, 2, 3) else 0

    @classmethod
    def _safe_response_summary(cls, data: Any, max_len: int = 800) -> str:
        """Return a redacted response summary for diagnostics."""
        try:
            if isinstance(data, dict):
                redacted: dict[str, Any] = {}
                for key, value in data.items():
                    key_text = str(key)
                    lowered = key_text.lower()
                    sensitive_markers = (
                        "token",
                        "cookie",
                        "password",
                        "passwd",
                        "pwd",
                        "authorization",
                    )
                    if any(marker in lowered for marker in sensitive_markers):
                        redacted[key_text] = "***"
                    elif isinstance(value, (dict, list, tuple, set)):
                        redacted[key_text] = f"<{type(value).__name__}>"
                    else:
                        text_value = cls._safe_text(value)
                        redacted[key_text] = text_value[:120]
                summary = str(redacted)
            else:
                summary = cls._safe_text(data)
        except Exception:
            summary = "<unserializable-response>"
        return summary[:max_len]

    def _require_text(self, data: dict[str, Any], key: str) -> str:
        value = self._safe_text(data.get(key)).strip()
        if value:
            return value

        keys = sorted(str(k) for k in data.keys())
        logger.warning(
            "Invalid GetCurrentInstall response: missing key=%s state=%s msg=%s keys=%s summary=%s",
            key,
            data.get("State"),
            self._safe_text(data.get("Msg")),
            keys,
            self._safe_response_summary(data),
        )
        raise InvalidInstallResponse(
            f"missing required field '{key}' in GetCurrentInstall response"
        )

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

    @classmethod
    def _response_indicates_remote_login(cls, data: Any) -> bool:
        if not isinstance(data, dict):
            return False
        return cls._safe_int(data.get("State"), 0) < 0

    @staticmethod
    def _looks_like_login_page(text: str) -> bool:
        sample = (text or "").lstrip("\ufeff\r\n\t ").lower()
        return sample.startswith("<!doctype") or sample.startswith("<html")

    @classmethod
    def _classify_remote_login(cls, *, message: str, raw_state: int, status_code: int | None = None) -> tuple[str, bool]:
        normalized = cls._safe_text(message).strip().lower()
        if status_code == 401:
            return ("http_401", False)
        if status_code == 403:
            return ("http_403", True)
        if "captcha" in normalized or "验证码" in normalized or "verify code" in normalized:
            return ("captcha_required", True)
        if "risk" in normalized or "风控" in normalized or "cloudflare" in normalized:
            return ("risk_control", True)
        if "login page" in normalized:
            return ("login_page", False)
        if "remote login" in normalized or "session" in normalized or raw_state < 0:
            return ("remote_login", False)
        return ("session_invalid", False)

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
                text = await resp.text()
                if self._looks_like_login_page(text):
                    raise RemoteLoginRequired(
                        raw_state=-2,
                        message="platform returned login page",
                        category="login_page",
                    )
                payload = text.lstrip("\ufeff\r\n\t ")
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"JSON parse failed: {text[:200]}") from exc
                if self._response_indicates_remote_login(parsed):
                    raw_state = self._safe_int(parsed.get("State"), -2)
                    msg = self._safe_text(parsed.get("Msg")) or f"State={raw_state}"
                    category, blocking = self._classify_remote_login(message=msg, raw_state=raw_state)
                    raise RemoteLoginRequired(
                        raw_state=raw_state,
                        message=msg,
                        category=category,
                        blocking=blocking,
                    )
                return parsed
        except aiohttp.ClientResponseError as exc:
            status_code = int(getattr(exc, "status", 0) or 0)
            if status_code in (401, 403):
                message = f"http status {status_code}"
                category, blocking = self._classify_remote_login(
                    message=message,
                    raw_state=-status_code,
                    status_code=status_code,
                )
                raise RemoteLoginRequired(
                    raw_state=-status_code,
                    message=message,
                    category=category,
                    blocking=blocking,
                    status_code=status_code,
                ) from exc
            raise
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

    async def get_captcha(self) -> bytes:
        """Fetch captcha image bytes for formal platform login."""
        session = await self._ensure_session()
        url = f"{self.base_url}/Free/VCode"
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            return await resp.read()

    # ------------------------------------------------------------------
    # PlatformAdapter 
    # ------------------------------------------------------------------

    async def login(
        self,
        account_name: str,
        password: str,
        captcha_code: Optional[str] = None,
    ) -> LoginResult:
        """
        Formal platform login via AjaxLogin.

        This flow must not probe VisitorLogin during account binding or manual
        login. When no captcha code is provided, the caller should fetch a
        captcha first and retry with AjaxLogin credentials.
        """
        session = await self._ensure_session()
        if not captcha_code:
            return LoginResult(
                success=False,
                message="captcha required",
                captcha_required=True,
            )

        ajax_url = f"{self.base_url}/Member/AjaxLogin"
        form_data = {
            "account": account_name,
            "password": password,
            "code": captcha_code,
        }
        try:
            async with session.post(
                ajax_url,
                data=form_data,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                cookies = session.cookie_jar.filter_cookies(URL(self.base_url))
                token_value = None
                for key, cookie in cookies.items():
                    if key.lower() == "token":
                        token_value = cookie.value
                        break

                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    body = await resp.text()
                    return LoginResult(
                        success=False,
                        message=f"AjaxLogin JSON error: {body[:120]}",
                    )

                state = self._safe_int(data.get("State"), 0)
                msg = self._safe_text(data.get("Msg"))
                if state == 1:
                    token_value = (
                        token_value
                        or self._safe_text(data.get("Token")).strip()
                        or self._safe_text(data.get("token")).strip()
                    )
                    if not token_value:
                        for cookie in cookies.values():
                            token_value = cookie.value
                            break
                    self._token = token_value or "ajax-session"
                    return LoginResult(
                        success=True,
                        token=self._token,
                        message=msg,
                    )

                return LoginResult(
                    success=False,
                    message=msg or f"AjaxLogin State={state}",
                    captcha_required=state in (5, 6),
                )
        except aiohttp.ClientError as e:
            logger.error("AjaxLogin failed: %s", e)
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

        if not isinstance(data, dict):
            logger.warning(
                "Invalid GetCurrentInstall response type=%s summary=%s",
                type(data).__name__,
                self._safe_response_summary(data),
            )
            raise InvalidInstallResponse(
                "GetCurrentInstall response is not a JSON object"
            )

        raw_state = self._safe_int(data.get("State"), 0)
        if raw_state < 0:
            msg = self._safe_text(data.get("Msg")) or f"GetCurrentInstall State={raw_state}"
            logger.warning(
                "GetCurrentInstall requires re-login state=%s msg=%s summary=%s",
                raw_state,
                msg,
                self._safe_response_summary(data),
            )
            category, blocking = self._classify_remote_login(message=msg, raw_state=raw_state)
            raise RemoteLoginRequired(
                raw_state=raw_state,
                message=msg,
                category=category,
                blocking=blocking,
            )

        issue = self._require_text(data, "Installments")
        normalized_state = self._normalize_state(raw_state)
        close_countdown = self._non_negative_int(data.get("CloseTimeStamp", 0), 0)
        open_countdown = self._non_negative_int(data.get("OpenTimeStamp", 0), 0)

        close_raw = self._safe_int(data.get("CloseTimeStamp", 0), 0)
        open_raw = self._safe_int(data.get("OpenTimeStamp", 0), 0)
        if close_raw < 0 or open_raw < 0:
            logger.warning(
                "GetCurrentInstall returned negative countdown close=%s open=%s "
                "normalized_close=%s normalized_open=%s issue=%s",
                close_raw,
                open_raw,
                close_countdown,
                open_countdown,
                issue,
            )

        return InstallInfo(
            issue=issue,
            state=normalized_state,
            close_countdown_sec=close_countdown,
            pre_issue=self._safe_text(data.get("PreInstallments")),
            pre_result=self._safe_text(data.get("PreLotteryResult")),
            open_countdown_sec=open_countdown,
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

        if not isinstance(data, dict):
            logger.warning(
                "Invalid current-install detail response type=%s summary=%s",
                type(data).__name__,
                self._safe_response_summary(data),
            )
            data = {}

        normalized_state = self._normalize_state(data.get("State", 0))
        close_countdown = self._non_negative_int(data.get("CloseTimeStamp", 0), 0)
        open_countdown = self._non_negative_int(data.get("OpenTimeStamp", 0), 0)
        installments = self._safe_text(data.get("Installments")).strip()

        if not installments:
            logger.warning(
                "GetCurrentInstall detail missing Installments state=%s msg=%s "
                "keys=%s summary=%s",
                data.get("State"),
                self._safe_text(data.get("Msg")),
                sorted(str(k) for k in data.keys()),
                self._safe_response_summary(data),
            )

        return {
            "installments": installments,
            "state": normalized_state,
            "close_countdown_sec": close_countdown,
            "open_countdown_sec": open_countdown,
            "pre_lottery_result": self._safe_text(data.get("PreLotteryResult")),
            "pre_installments": self._safe_text(data.get("PreInstallments")),
            "template_code": self._safe_text(data.get("TemplateCode")),
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
        url = f"{self.base_url}/PlaceBet/Confirmbet?lotteryType={self.lottery_type}"
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
        if not isinstance(resp, dict):
            raise RemoteLoginRequired(
                raw_state=-2,
                message="QueryResult returned non-object response",
            )
        if "accountLimit" not in resp:
            raw_state = self._safe_int(resp.get("State"), -2)
            summary = self._safe_response_summary(resp)
            raise RemoteLoginRequired(
                raw_state=raw_state,
                message=f"QueryResult missing accountLimit: {summary}",
            )
        try:
            balance = float(resp["accountLimit"])
        except (TypeError, ValueError) as exc:
            raw_state = self._safe_int(resp.get("State"), -2)
            raise RemoteLoginRequired(
                raw_state=raw_state,
                message=f"QueryResult invalid accountLimit: {resp.get('accountLimit')!r}",
            ) from exc
        if not math.isfinite(balance) or balance < 0:
            raw_state = self._safe_int(resp.get("State"), -2)
            raise RemoteLoginRequired(
                raw_state=raw_state,
                message=f"QueryResult invalid accountLimit: {resp.get('accountLimit')!r}",
            )
        return BalanceInfo(
            balance=balance,
            raw_response=resp,
        )

    async def get_bet_history(self, count: int = 15) -> list[dict]:
        """"""
        url = f"{self.base_url}/BettingList/getBetChecked"
        form_data = {
            "startIndex": "0",
            "rows": str(count),
        }
        resp = await self._post(url, data=form_data)
        records = self._extract_bet_history_records(resp)
        return records

    @staticmethod
    def _extract_bet_history_records(resp: Any) -> list[dict]:
        """Extract settled bet records from supported platform response shapes."""
        if isinstance(resp, list):
            return [r for r in resp if isinstance(r, dict)]
        if not isinstance(resp, dict):
            return []

        for key in ("betList", "Records", "data"):
            value = resp.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
            if isinstance(value, dict):
                for nested_key in ("betList", "Records", "data"):
                    nested = value.get(nested_key)
                    if isinstance(nested, list):
                        return [r for r in nested if isinstance(r, dict)]
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

    # DW3-aware overrides are defined after legacy methods so the class uses the
    # verified payloads and odds loaders for both WEB and 2.0.
    async def load_odds(self, issue: str) -> dict[str, int]:
        odds = await self._load_odds_for_form(
            issue,
            {
                "itype": "-1",
                "midCode": MID_CODES,
                "oddstype": "A",
            },
        )
        dw3_profile = get_jnd_dw3_profile(self.lottery_type)
        if dw3_profile is not None:
            dw3_odds = await self._load_odds_for_form(
                issue,
                {
                    "itype": "-1",
                    "settingCode": dw3_profile.odds_setting_code,
                    "oddstype": "A",
                },
            )
            odds.update(dw3_odds)
        return odds

    async def _load_odds_for_form(
        self,
        issue: str,
        extra_form: dict[str, str],
    ) -> dict[str, int]:
        url = (
            f"{self.base_url}/PlaceBet/Loaddata"
            f"?lotteryType={self.lottery_type}"
        )
        form_data = {
            "lotteryType": self.lottery_type,
            "install": issue,
            **extra_form,
        }
        resp = await self._post(url, data=form_data)
        return self._extract_scaled_odds(resp)

    @staticmethod
    def _extract_scaled_odds(resp: Any) -> dict[str, int]:
        odds_raw: dict[str, Any] = resp if isinstance(resp, dict) else {}
        if "data" in odds_raw and isinstance(odds_raw["data"], dict):
            odds_raw = odds_raw["data"]

        odds: dict[str, int] = {}
        for key, value in odds_raw.items():
            try:
                float_val = float(value)
                odds[key] = round(float_val * 10000)
            except (ValueError, TypeError):
                continue
        return odds

    async def place_bet(self, issue: str, betdata: list[dict]) -> BetResult:
        url = f"{self.base_url}/PlaceBet/Confirmbet?lotteryType={self.lottery_type}"
        form_data: dict[str, str] = {}
        for i, bet in enumerate(betdata):
            amount_fen = self._safe_int(bet["Amount"], 0)
            odds_scaled = self._safe_int(bet["Odds"], 0)
            form_data[f"betdata[{i}][Amount]"] = self._format_amount_yuan(amount_fen)
            form_data[f"betdata[{i}][KeyCode]"] = str(bet["KeyCode"])
            form_data[f"betdata[{i}][Odds]"] = self._format_odds_value(odds_scaled)
        form_data["lotteryType"] = self.lottery_type
        form_data["betNum"] = self._make_bet_num()
        form_data["prompt"] = "false"
        form_data["gt"] = "A"
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

    async def get_bet_history(self, count: int = 15) -> list[dict]:
        url = f"{self.base_url}/BettingList/getBetChecked"
        form_data = {
            "startIndex": "0",
            "rows": str(count),
        }
        resp = await self._post(url, data=form_data)
        records = self._extract_bet_history_records(resp)
        expected_type = (self.lottery_type or "").upper()
        has_platform_type = any(
            isinstance(record, dict) and str(record.get("LotteryType", "")).strip()
            for record in records
        )
        if has_platform_type and expected_type:
            return [
                record
                for record in records
                if str(record.get("LotteryType", "")).upper() == expected_type
            ]
        return records

    async def heartbeat(self) -> bool:
        """"""
        url = f"{self.base_url}/Member/Online"
        try:
            resp = await self._post(url, timeout=10.0)
            return resp.get("State") == 1
        except Exception as e:
            logger.warning(": %s", e)
            return False
