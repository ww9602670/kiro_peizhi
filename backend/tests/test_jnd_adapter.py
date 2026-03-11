"""JNDAdapter    mock HTTP """
from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engine.adapters.base import (
    BalanceInfo,
    BetResult,
    InstallInfo,
    LoginResult,
    PlatformAdapter,
)
from app.engine.adapters.config import PLATFORM_CONFIGS
from app.engine.adapters.jnd import JNDAdapter


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def adapter():
    """ mock session  JNDAdapter """
    return JNDAdapter(
        base_url="https://test.example.com",
        lottery_type="JND28WEB",
    )


# ------------------------------------------------------------------
# Helper: mock _post
# ------------------------------------------------------------------

def _patch_post(adapter: JNDAdapter, return_value: dict):
    """Patch adapter._post to return a given dict."""
    adapter._post = AsyncMock(return_value=return_value)


# ------------------------------------------------------------------
# 1. 
# ------------------------------------------------------------------

class TestBaseClasses:
    """ PlatformAdapter ABC """

    def test_platform_adapter_is_abstract(self):
        """PlatformAdapter """
        with pytest.raises(TypeError):
            PlatformAdapter()

    def test_jnd_adapter_is_platform_adapter(self, adapter):
        """JNDAdapter  PlatformAdapter """
        assert isinstance(adapter, PlatformAdapter)

    def test_install_info_defaults(self):
        info = InstallInfo(
            issue="123", state=1, close_countdown_sec=50,
            pre_issue="122", pre_result="1,2,3",
        )
        assert info.is_new_issue is False
        assert info.open_countdown_sec == 0

    def test_bet_result_defaults(self):
        result = BetResult(succeed=1, message="ok")
        assert result.raw_response == {}

    def test_balance_info_defaults(self):
        info = BalanceInfo(balance=100.5)
        assert info.raw_response == {}

    def test_login_result_defaults(self):
        result = LoginResult(success=True)
        assert result.token is None
        assert result.message == ""
        assert result.captcha_required is False


# ------------------------------------------------------------------
# 2. PLATFORM_CONFIGS 
# ------------------------------------------------------------------

class TestPlatformConfigs:
    """"""

    def test_jnd28web_config_exists(self):
        assert "JND28WEB" in PLATFORM_CONFIGS

    def test_jnd282_config_exists(self):
        assert "JND282" in PLATFORM_CONFIGS

    def test_jnd28web_has_required_fields(self):
        cfg = PLATFORM_CONFIGS["JND28WEB"]
        assert "base_url" in cfg
        assert "lottery_type" in cfg
        assert cfg["lottery_type"] == "JND28WEB"
        assert "downtime_ranges" in cfg
        assert "refund_rules" in cfg

    def test_jnd282_has_refund_rules(self):
        cfg = PLATFORM_CONFIGS["JND282"]
        rules = cfg["refund_rules"]
        assert 14 in rules
        assert 13 in rules
        assert "DX1" in rules[14]
        assert "DS4" in rules[14]
        assert "ZH8" in rules[14]
        assert "DX2" in rules[13]
        assert "DS3" in rules[13]
        assert "ZH9" in rules[13]

    def test_jnd28web_no_refund_rules(self):
        cfg = PLATFORM_CONFIGS["JND28WEB"]
        assert cfg["refund_rules"] == {}


# ------------------------------------------------------------------
# 3. login() 
# ------------------------------------------------------------------

class TestLogin:
    """ login """

    @pytest.mark.asyncio
    async def test_login_success(self, adapter):
        """mock  LoginResult"""
        mock_session = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        #  cookie_jar  token
        mock_cookie = MagicMock()
        mock_cookie.value = "test_token_123"
        mock_cookies = {"token": mock_cookie}

        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.cookie_jar = MagicMock()
        mock_session.cookie_jar.filter_cookies = MagicMock(return_value=mock_cookies)
        mock_session.closed = False

        adapter._session = mock_session

        result = await adapter.login("testuser", "testpass")

        assert result.success is True
        assert result.token == "test_token_123"
        assert "" in result.message

    @pytest.mark.asyncio
    async def test_login_no_token(self, adapter):
        """ token cookie"""
        mock_session = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.cookie_jar = MagicMock()
        mock_session.cookie_jar.filter_cookies = MagicMock(return_value={})
        mock_session.closed = False

        adapter._session = mock_session

        result = await adapter.login("testuser", "testpass")

        assert result.success is False
        assert "token" in result.message.lower()

    @pytest.mark.asyncio
    async def test_login_network_error(self, adapter):
        """"""
        import aiohttp

        mock_session = AsyncMock()
        mock_session.get = MagicMock(
            side_effect=aiohttp.ClientError("Connection refused")
        )
        mock_session.closed = False

        adapter._session = mock_session

        result = await adapter.login("testuser", "testpass")

        assert result.success is False
        assert result.token is None


# ------------------------------------------------------------------
# 4. get_current_install() 
# ------------------------------------------------------------------

class TestGetCurrentInstall:
    """ get_current_install """

    @pytest.mark.asyncio
    async def test_success(self, adapter):
        """mock  InstallInfo """
        _patch_post(adapter, {
            "Installments": "3397187",
            "State": 1,
            "CloseTimeStamp": 51,
            "OpenTimeStamp": 71,
            "PreLotteryResult": "7,5,0",
            "PreInstallments": "3397186",
            "TemplateCode": "JNDPCDD",
        })

        info = await adapter.get_current_install()

        assert isinstance(info, InstallInfo)
        assert info.issue == "3397187"
        assert info.state == 1
        assert info.close_timestamp == 51
        assert info.open_timestamp == 71
        assert info.pre_issue == "3397186"
        assert info.pre_result == "7,5,0"

    @pytest.mark.asyncio
    async def test_closed_state(self, adapter):
        """"""
        _patch_post(adapter, {
            "Installments": "3397188",
            "State": 0,
            "CloseTimeStamp": 0,
            "OpenTimeStamp": 10,
            "PreLotteryResult": "3,4,5",
            "PreInstallments": "3397187",
        })

        info = await adapter.get_current_install()

        assert info.state == 0
        assert info.close_timestamp == 0


# ------------------------------------------------------------------
# 5. load_odds() 
# ------------------------------------------------------------------

class TestLoadOdds:
    """ load_odds """

    @pytest.mark.asyncio
    async def test_odds_conversion_to_int(self, adapter):
        """ 10000 """
        _patch_post(adapter, {
            "DX1": 2.053,
            "DX2": 2.053,
            "DS3": 2.053,
            "HZ1": 955,
            "HZ14": 13.13,
            "BZ4": 99.17,
        })

        odds = await adapter.load_odds("3397187")

        assert isinstance(odds, dict)
        assert odds["DX1"] == 20530
        assert odds["DX2"] == 20530
        assert odds["DS3"] == 20530
        assert odds["HZ1"] == 9550000
        assert odds["HZ14"] == 131300
        assert odds["BZ4"] == 991700

    @pytest.mark.asyncio
    async def test_odds_nested_in_data(self, adapter):
        """ data """
        _patch_post(adapter, {
            "data": {
                "DX1": 2.053,
                "DS3": 2.053,
            }
        })

        odds = await adapter.load_odds("3397187")

        assert odds["DX1"] == 20530
        assert odds["DS3"] == 20530

    @pytest.mark.asyncio
    async def test_odds_zero_value(self, adapter):
        """ 0 """
        _patch_post(adapter, {
            "DX1": 2.053,
            "TMBS5": 0,
        })

        odds = await adapter.load_odds("3397187")

        assert odds["DX1"] == 20530
        assert odds["TMBS5"] == 0

    @pytest.mark.asyncio
    async def test_odds_skips_invalid_values(self, adapter):
        """"""
        _patch_post(adapter, {
            "DX1": 2.053,
            "invalid": "not_a_number",
        })

        odds = await adapter.load_odds("3397187")

        assert "DX1" in odds
        assert "invalid" not in odds


# ------------------------------------------------------------------
# 6. place_bet() 
# ------------------------------------------------------------------

class TestPlaceBet:
    """ place_bet """

    @pytest.mark.asyncio
    async def test_bet_success(self, adapter):
        """mock  BetResult"""
        _patch_post(adapter, {
            "succeed": 1,
            "msg": "",
            "betList": [{"Amount": 5, "KeyCode": "DX1"}],
        })

        betdata = [
            {"Amount": 5, "KeyCode": "DX1", "Odds": 2.053},
        ]
        result = await adapter.place_bet("3397187", betdata)

        assert isinstance(result, BetResult)
        assert result.succeed == 1
        assert result.message == ""
        assert result.raw_response["betList"] is not None

    @pytest.mark.asyncio
    async def test_bet_odds_changed(self, adapter):
        """succeed=5"""
        _patch_post(adapter, {
            "succeed": 5,
            "msg": "",
        })

        result = await adapter.place_bet("3397187", [
            {"Amount": 10, "KeyCode": "HZ13", "Odds": 13.45},
        ])

        assert result.succeed == 5
        assert "" in result.message

    @pytest.mark.asyncio
    async def test_bet_multiple_items(self, adapter):
        """ betdata """
        _patch_post(adapter, {"succeed": 1, "msg": "ok"})

        betdata = [
            {"Amount": 5, "KeyCode": "DX1", "Odds": 2.053},
            {"Amount": 10, "KeyCode": "HZ13", "Odds": 13.45},
        ]
        result = await adapter.place_bet("3397187", betdata)

        assert result.succeed == 1
        #  _post  form_data  betdata
        call_args = adapter._post.call_args
        form_data = call_args.kwargs.get("data") or call_args[1].get("data") or call_args[0][1]
        assert "betdata[0][Amount]" in form_data
        assert "betdata[1][Amount]" in form_data
        assert form_data["betdata[0][KeyCode]"] == "DX1"
        assert form_data["betdata[1][KeyCode]"] == "HZ13"

    @pytest.mark.asyncio
    async def test_bet_network_error(self, adapter):
        """ BetResult"""
        adapter._post = AsyncMock(side_effect=Exception("timeout"))

        result = await adapter.place_bet("3397187", [
            {"Amount": 5, "KeyCode": "DX1", "Odds": 2.053},
        ])

        assert result.succeed == 0
        assert "" in result.message or "timeout" in result.message


# ------------------------------------------------------------------
# 7. query_balance() 
# ------------------------------------------------------------------

class TestQueryBalance:
    """ query_balance """

    @pytest.mark.asyncio
    async def test_balance_success(self, adapter):
        """mock  BalanceInfo"""
        _patch_post(adapter, {
            "accountLimit": 1234.56,
            "Result": -50.0,
            "UnResult": 100.0,
            "AccType": 1,
        })

        info = await adapter.query_balance()

        assert isinstance(info, BalanceInfo)
        assert info.balance == 1234.56
        assert info.raw_response["Result"] == -50.0

    @pytest.mark.asyncio
    async def test_balance_zero(self, adapter):
        """ 0"""
        _patch_post(adapter, {"accountLimit": 0})

        info = await adapter.query_balance()

        assert info.balance == 0.0


# ------------------------------------------------------------------
# 8. get_bet_history() 
# ------------------------------------------------------------------

class TestGetBetHistory:
    """ get_bet_history """

    @pytest.mark.asyncio
    async def test_history_list(self, adapter):
        """"""
        records = [
            {"issue": "3397186", "amount": 5, "key_code": "DX1"},
            {"issue": "3397185", "amount": 10, "key_code": "HZ13"},
        ]
        _patch_post(adapter, records)

        #  _post  list 
        #  _post  dict get_bet_history  list 
        adapter._post = AsyncMock(return_value=records)

        result = await adapter.get_bet_history(count=15)

        assert isinstance(result, list)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_history_nested_in_data(self, adapter):
        """ data """
        records = [{"issue": "3397186"}]
        _patch_post(adapter, {"data": records})

        result = await adapter.get_bet_history()

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_history_empty(self, adapter):
        """"""
        _patch_post(adapter, {"data": []})

        result = await adapter.get_bet_history()

        assert result == []

    @pytest.mark.asyncio
    async def test_history_custom_count(self, adapter):
        """"""
        _patch_post(adapter, [])
        adapter._post = AsyncMock(return_value=[])

        await adapter.get_bet_history(count=5)

        call_args = adapter._post.call_args
        form_data = call_args.kwargs.get("data") or call_args[0][1]
        assert form_data["top"] == "5"


# ------------------------------------------------------------------
# 9. get_lottery_results() 
# ------------------------------------------------------------------

class TestGetLotteryResults:
    """ get_lottery_results """

    @pytest.mark.asyncio
    async def test_results_success(self, adapter):
        """"""
        _patch_post(adapter, {
            "data": {
                "Records": [
                    {"Installments": "3397186", "OpenResult": "7,5,0", "OpenTime": "2026-02-28 12:00:00"},
                    {"Installments": "3397185", "OpenResult": "3,4,5", "OpenTime": "2026-02-28 11:55:00"},
                ],
                "TotalCount": 50000,
            }
        })

        result = await adapter.get_lottery_results(count=10)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["Installments"] == "3397186"
        assert result[0]["OpenResult"] == "7,5,0"

    @pytest.mark.asyncio
    async def test_results_empty(self, adapter):
        """"""
        _patch_post(adapter, {"data": {"Records": [], "TotalCount": 0}})

        result = await adapter.get_lottery_results()

        assert result == []


# ------------------------------------------------------------------
# 10. heartbeat() 
# ------------------------------------------------------------------

class TestHeartbeat:
    """ heartbeat """

    @pytest.mark.asyncio
    async def test_heartbeat_success(self, adapter):
        """"""
        _patch_post(adapter, {"State": 1})

        result = await adapter.heartbeat()

        assert result is True

    @pytest.mark.asyncio
    async def test_heartbeat_failure_state(self, adapter):
        """ 1 """
        _patch_post(adapter, {"State": 0})

        result = await adapter.heartbeat()

        assert result is False

    @pytest.mark.asyncio
    async def test_heartbeat_network_error(self, adapter):
        """ False"""
        adapter._post = AsyncMock(side_effect=Exception("connection lost"))

        result = await adapter.heartbeat()

        assert result is False


# ------------------------------------------------------------------
# 11. 
# ------------------------------------------------------------------

class TestErrorHandling:
    """"""

    @pytest.mark.asyncio
    async def test_get_install_propagates_error(self, adapter):
        """get_current_install """
        adapter._post = AsyncMock(side_effect=Exception("network error"))

        with pytest.raises(Exception, match="network error"):
            await adapter.get_current_install()

    @pytest.mark.asyncio
    async def test_load_odds_propagates_error(self, adapter):
        """load_odds """
        adapter._post = AsyncMock(side_effect=Exception("timeout"))

        with pytest.raises(Exception, match="timeout"):
            await adapter.load_odds("3397187")

    @pytest.mark.asyncio
    async def test_query_balance_propagates_error(self, adapter):
        """query_balance """
        adapter._post = AsyncMock(side_effect=Exception("server error"))

        with pytest.raises(Exception, match="server error"):
            await adapter.query_balance()

    @pytest.mark.asyncio
    async def test_place_bet_handles_error_gracefully(self, adapter):
        """place_bet  BetResult"""
        adapter._post = AsyncMock(side_effect=Exception("connection reset"))

        result = await adapter.place_bet("3397187", [
            {"Amount": 5, "KeyCode": "DX1", "Odds": 2.053},
        ])

        assert result.succeed == 0

    @pytest.mark.asyncio
    async def test_heartbeat_handles_error_gracefully(self, adapter):
        """heartbeat  False"""
        adapter._post = AsyncMock(side_effect=Exception("timeout"))

        result = await adapter.heartbeat()

        assert result is False
