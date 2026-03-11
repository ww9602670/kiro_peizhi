"""   (VisitorLogin)


    cd backend
    set RUN_LIVE=1 && python -m pytest tests/test_live_connectivity.py -v -s --tb=short


    - 
    - 
    -  session
    -  RUN_LIVE=1 
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime

import pytest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SKIP_REASON = " RUN_LIVE=1 "
live = pytest.mark.skipif(not os.environ.get("RUN_LIVE"), reason=SKIP_REASON)

# Module-level shared state
_adapter = None
_login_success = False


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def adapter():
    global _adapter, _login_success
    from app.engine.adapters.jnd import JNDAdapter

    _adapter = JNDAdapter(platform_type="JND28WEB")
    logger.info("=" * 60)
    logger.info("  ")
    logger.info("base_url: %s", _adapter.base_url)
    logger.info("lottery_type: %s", _adapter.lottery_type)
    logger.info("=" * 60)

    result = await _adapter.login("visitor", "visitor")
    _login_success = result.success
    if result.success:
        logger.info("  | token=%s...", (result.token or "")[:20])
    else:
        logger.error("  | message=%s", result.message)

    yield _adapter
    await _adapter.close()


@live
@pytest.mark.asyncio(scope="module")
class TestLiveConnectivity:
    """ API """

    async def test_01_login(self, adapter):
        """[API 1] VisitorLogin  """
        assert _login_success, ""
        assert adapter._token is not None
        logger.info("  token: %s...", adapter._token[:30] if adapter._token else "None")

    async def test_02_get_current_install(self, adapter):
        """[API 2] GetCurrentInstall  /"""
        assert _login_success, ""
        info = await adapter.get_current_install()

        logger.info("  : %s", info.issue)
        logger.info("  : %s (%s)", info.state, "" if info.state == 1 else "")
        logger.info("  : %ds", info.close_timestamp)
        logger.info("  : %ds", info.open_timestamp)
        logger.info("  : %s : %s", info.pre_issue, info.pre_result)

        assert info.issue, ""
        assert isinstance(info.state, int)
        assert isinstance(info.close_timestamp, int)

        if info.pre_result:
            parts = info.pre_result.split(",")
            assert len(parts) == 3, f"3: {info.pre_result}"
            total = sum(int(p.strip()) for p in parts)
            logger.info("  : %d", total)

    async def test_03_load_odds(self, adapter):
        """[API 3] Loaddata  """
        assert _login_success, ""
        info = await adapter.get_current_install()
        odds = await adapter.load_odds(info.issue)

        logger.info("  : %d ", len(odds))

        expected = ["DX1", "DX2", "DS3", "DS4"]
        for key in expected:
            assert key in odds, f": {key}"
            logger.info("  %s = %.4f", key, odds[key] / 10000)

        assert odds.get("DX1", 0) > 10000, "DX1 "
        assert odds.get("DX1", 0) < 50000, "DX1 "

        logger.info("  ---  ---")
        for k in sorted(odds.keys())[:20]:
            logger.info("    %s: %.4f", k, odds[k] / 10000)

    async def test_04_query_balance(self, adapter):
        """[API 6] QueryResult  """
        assert _login_success, ""
        info = await adapter.query_balance()

        logger.info("  : %.2f", info.balance)
        logger.info("  : %s", json.dumps(info.raw_response, ensure_ascii=False, default=str))
        assert info.balance >= 0

    async def test_05_heartbeat(self, adapter):
        """[API 11] Online  """
        assert _login_success, ""
        ok = await adapter.heartbeat()
        logger.info("  : %s", "" if ok else "")
        assert ok, ""

    async def test_06_get_bet_history(self, adapter):
        """[API 7] Topbetlist  """
        assert _login_success, ""
        history = await adapter.get_bet_history(count=5)
        logger.info("  : %d", len(history))
        if history:
            logger.info("  : %s", json.dumps(history[0], ensure_ascii=False, default=str))
        assert isinstance(history, list)

    async def test_07_get_lottery_results(self, adapter):
        """[API 9] Lotteryresult  """
        assert _login_success, ""
        results = await adapter.get_lottery_results(count=5)
        logger.info("  : %d ", len(results))
        for r in results[:3]:
            logger.info("    =%s =%s", r.get("Installments", "?"), r.get("OpenResult", "?"))
        assert isinstance(results, list)
        assert len(results) > 0, "1"

    async def test_08_place_bet(self, adapter):
        """[API 5] Confirmbet  """
        assert _login_success, ""

        info = await adapter.get_current_install()
        if info.state != 1:
            pytest.skip(f" (state={info.state})")
        if info.close_timestamp < 20:
            pytest.skip(f" {info.close_timestamp}s")

        odds = await adapter.load_odds(info.issue)
        dx1_odds = odds.get("DX1", 0)
        if dx1_odds == 0:
            pytest.skip("DX1  0")

        betdata = [{"Amount": 1, "KeyCode": "DX1", "Odds": dx1_odds / 10000}]
        logger.info("  : %s | DX1() =1 =%.4f", info.issue, dx1_odds / 10000)

        result = await adapter.place_bet(info.issue, betdata)
        logger.info("  succeed: %d | msg: %s", result.succeed, result.message)
        logger.info("  raw: %s", json.dumps(result.raw_response, ensure_ascii=False, default=str))

        assert result.succeed in (1, 5, 10, 18), f" succeed: {result.succeed}"
        if result.succeed == 1:
            logger.info("   ")


@live
@pytest.mark.asyncio(scope="module")
async def test_99_summary(adapter):
    """"""
    logger.info("=" * 60)
    logger.info(" | : %s | : %s | : %s",
                adapter.base_url, "" if _login_success else "", datetime.now().isoformat())
    logger.info("=" * 60)
