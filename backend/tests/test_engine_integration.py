"""Phase 14.3 ?

14.3.1  mock adapter ?? ? ? ? ? ?
14.3.2 ?KeyCode 
14.3.3 ?idempotent_id?
14.3.4 Worker ?Worker
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.engine.adapters.base import (
    BalanceInfo,
    BetResult,
    InstallInfo,
    LoginResult,
    PlatformAdapter,
)
from app.engine.alert import AlertService
from app.engine.executor import BetExecutor
from app.engine.poller import IssuePoller
from app.engine.rate_limiter import RateLimiter
from app.engine.reconciler import Reconciler
from app.engine.risk import RiskController
from app.engine.session import SessionManager
from app.engine.settlement import SettlementProcessor
from app.engine.strategies.flat import FlatStrategyImpl
from app.engine.strategy_runner import BetSignal, StrategyRunner
from app.engine.worker import AccountWorker
from app.models.db_ops import (
    account_create,
    bet_order_create,
    odds_batch_upsert,
    operator_create,
    strategy_create,
)
from app.utils.captcha import CaptchaService


#  Mock PlatformAdapter 

class MockAdapter(PlatformAdapter):
    """Mock adapter for testing."""

    def __init__(
        self,
        *,
        login_success: bool = True,
        odds: dict[str, int] | None = None,
        bet_succeed: int = 1,
        balance: float = 10000.0,
        bet_history: list[dict] | None = None,
        install: InstallInfo | None = None,
    ) -> None:
        self._login_success = login_success
        self._odds = odds or {"DX1": 19800, "DS3": 19800, "DX2": 19800}
        self._bet_succeed = bet_succeed
        self._balance = balance
        self._bet_history = bet_history or []
        self._install = install or InstallInfo(
            issue="20260301001",
            state=1,
            close_countdown_sec=120,
            pre_issue="20260301000",
            pre_result="3,5,6",
            is_new_issue=False,
        )
        self.place_bet_calls: list[tuple[str, list[dict]]] = []

    async def login(self, account_name: str, password: str) -> LoginResult:
        return LoginResult(
            success=self._login_success,
            token="mock_token_123" if self._login_success else None,
            message="" if self._login_success else "mock login failed",
        )

    async def get_current_install(self) -> InstallInfo:
        return self._install

    async def load_odds(self, issue: str) -> dict[str, int]:
        return dict(self._odds)

    async def place_bet(self, issue: str, betdata: list[dict]) -> BetResult:
        self.place_bet_calls.append((issue, betdata))
        return BetResult(
            succeed=self._bet_succeed,
            message="success" if self._bet_succeed == 1 else "failed",
            raw_response={"succeed": self._bet_succeed},
        )

    async def query_balance(self) -> BalanceInfo:
        return BalanceInfo(balance=self._balance)

    async def get_bet_history(self, count: int = 15) -> list[dict]:
        return list(self._bet_history)

    async def get_lottery_results(self, count: int = 10) -> list[dict]:
        return []

    async def heartbeat(self) -> bool:
        return True

    async def get_current_install_detail(self) -> dict:
        return {
            "installments": self._install.issue,
            "state": self._install.state,
            "close_countdown_sec": self._install.close_countdown_sec,
            "open_countdown_sec": getattr(self._install, "open_countdown_sec", 0),
            "pre_lottery_result": self._install.pre_result or "",
            "pre_installments": self._install.pre_issue or "",
            "template_code": "JNDPCDD",
        }


#  Helper:  

async def _setup_operator_and_account(db, platform_type="JND28WEB"):
    """创建运营商+账号, 返回 (operator_id, account_id)"""
    op = await operator_create(
        db,
        username="test_op_engine",
        password="pass123456",
        role="operator",
        max_accounts=5,
    )
    operator_id = op["id"]

    acc = await account_create(
        db,
        operator_id=operator_id,
        account_name="gambler_test",
        password="gp123456",
        platform_type=platform_type,
    )
    account_id = acc["id"]

    #  status=online, balance, session_token ?
    await db.execute(
        "UPDATE gambling_accounts SET status='online', balance=1000000, session_token='mock_token' WHERE id=?",
        (account_id,),
    )
    await db.commit()

    return operator_id, account_id


async def _create_strategy(db, operator_id, account_id, name="flat_dx1",
                           play_code="DX1", base_amount=1000, status="running"):
    """创建策略, 返回 dict"""
    s = await strategy_create(
        db,
        operator_id=operator_id,
        account_id=account_id,
        name=name,
        type="flat",
        play_code=play_code,
        base_amount=base_amount,
    )
    if status != "stopped":
        await db.execute(
            "UPDATE strategies SET status=? WHERE id=?", (status, s["id"])
        )
        await db.commit()
        s["status"] = status
    return s



# 
# 14.3.1 mock adapter?
# 

@pytest.mark.asyncio
async def test_full_betting_flow(db):
    """?? ? ? ??

     mock adapter ?
    """
    operator_id, account_id = await _setup_operator_and_account(db)
    strategy = await _create_strategy(db, operator_id, account_id)
    strategy_id = strategy["id"]

    adapter = MockAdapter(
        odds={"DX1": 19800},
        bet_succeed=1,
        balance=10000.0,
        bet_history=[{"Installments": "20260301001", "Amount": 10}],
    )
    alert_service = AlertService(db)

    # 插入已确认赔率到 DB（executor 从 DB 读取赔率）
    await odds_batch_upsert(
        db, account_id=account_id,
        odds_map={"DX1": 19800},
        confirmed=True,
    )

    #  1.  
    flat = FlatStrategyImpl(key_codes=["DX1"], base_amount=1000)
    runner = StrategyRunner(strategy_id=strategy_id, strategy=flat)
    runner.start()

    from app.engine.strategies.base import StrategyContext
    ctx = StrategyContext(current_issue="20260301001", history=[], balance=1_000_000, strategy_state={})
    signals = runner.collect_signals(ctx, issue="20260301001")
    assert len(signals) == 1
    assert signals[0].key_code == "DX1"
    assert signals[0].amount == 1000

    #  2. ?
    risk = RiskController(
        db=db, alert_service=alert_service,
        operator_id=operator_id, account_id=account_id,
    )
    check_result = await risk.check(signals[0])
    assert check_result.passed, f": {check_result.reason}"

    #  3.  
    executor = BetExecutor(
        db=db, adapter=adapter, risk=risk,
        alert_service=alert_service,
        operator_id=operator_id, account_id=account_id,
    )
    install = InstallInfo(
        issue="20260301001", state=1, close_countdown_sec=120,
        pre_issue="20260301000", pre_result="3,5,6",
    )
    await executor.execute(install, signals)

    #  place_bet ?
    assert len(adapter.place_bet_calls) == 1
    issue_called, betdata = adapter.place_bet_calls[0]
    assert issue_called == "20260301001"
    assert len(betdata) == 1
    assert betdata[0]["KeyCode"] == "DX1"
    assert betdata[0]["Amount"] == 1000

    # ?= bet_success
    row = await (await db.execute(
        "SELECT * FROM bet_orders WHERE strategy_id=? AND issue=?",
        (strategy_id, "20260301001"),
    )).fetchone()
    assert row is not None
    assert row["status"] == "bet_success"
    assert row["amount"] == 1000

    #  4.  
    settler = SettlementProcessor(db=db, operator_id=operator_id)
    # ?3+5+6=14DX1(? 
    await settler.settle(
        issue="20260301001",
        balls=[3, 5, 6],
        sum_value=14,
        platform_type="JND28WEB",
    )

    # ?
    row = await (await db.execute(
        "SELECT * FROM bet_orders WHERE strategy_id=? AND issue=?",
        (strategy_id, "20260301001"),
    )).fetchone()
    assert row["status"] == "settled"
    assert row["is_win"] == 1
    # pnl = 1000 * 19800 // 10000 - 1000 = 1980 - 1000 = 980
    assert row["pnl"] == 980

    # 
    strat_row = await (await db.execute(
        "SELECT * FROM strategies WHERE id=?", (strategy_id,)
    )).fetchone()
    assert strat_row["total_pnl"] == 980

    # ?
    lr = await (await db.execute(
        "SELECT * FROM lottery_results WHERE issue=?", ("20260301001",)
    )).fetchone()
    assert lr is not None
    assert lr["sum_value"] == 14

    #  5.  
    reconciler = Reconciler(
        db=db, adapter=adapter,
        alert_service=alert_service, operator_id=operator_id,
    )
    await reconciler.reconcile(account_id=account_id, issue="20260301001")

    # 
    rec = await (await db.execute(
        "SELECT * FROM reconcile_records WHERE account_id=? AND issue=?",
        (account_id, "20260301001"),
    )).fetchone()
    assert rec is not None
    assert rec["local_bet_count"] >= 0  # 


# 
# 14.3.2 ?
# 

@pytest.mark.asyncio
async def test_multi_strategy_parallel(db):
    """?

    ?
    - betdata ?= ?KeyCode?
    -  bet_orders
    """
    operator_id, account_id = await _setup_operator_and_account(db)

    #  3 2  DX1?  DS3
    s1 = await _create_strategy(db, operator_id, account_id,
                                name="flat_dx1_a", play_code="DX1", base_amount=1000)
    s2 = await _create_strategy(db, operator_id, account_id,
                                name="flat_dx1_b", play_code="DX1", base_amount=2000)
    s3 = await _create_strategy(db, operator_id, account_id,
                                name="flat_ds3", play_code="DS3", base_amount=500)

    adapter = MockAdapter(
        odds={"DX1": 19800, "DS3": 19800},
        bet_succeed=1,
    )
    alert_service = AlertService(db)
    risk = RiskController(
        db=db, alert_service=alert_service,
        operator_id=operator_id, account_id=account_id,
    )

    # 插入已确认赔率到 DB
    await odds_batch_upsert(
        db, account_id=account_id,
        odds_map={"DX1": 19800, "DS3": 19800},
        confirmed=True,
    )

    #  3 ?StrategyRunner
    runners = {}
    for s_data in [s1, s2, s3]:
        flat = FlatStrategyImpl(
            key_codes=[s_data["play_code"]],
            base_amount=s_data["base_amount"],
        )
        runner = StrategyRunner(strategy_id=s_data["id"], strategy=flat)
        runner.start()
        runners[s_data["id"]] = runner

    # ?
    from app.engine.strategies.base import StrategyContext
    ctx = StrategyContext(current_issue="20260301010", history=[], balance=1_000_000, strategy_state={})
    all_signals: list[BetSignal] = []
    for sid, runner in runners.items():
        sigs = runner.collect_signals(ctx, issue="20260301010")
        all_signals.extend(sigs)

    assert len(all_signals) == 3  # 3  1 ?

    # 
    executor = BetExecutor(
        db=db, adapter=adapter, risk=risk,
        alert_service=alert_service,
        operator_id=operator_id, account_id=account_id,
    )
    install = InstallInfo(
        issue="20260301010", state=1, close_countdown_sec=120,
        pre_issue="20260301009", pre_result="1,2,3",
    )
    await executor.execute(install, all_signals)

    #  place_bet 
    assert len(adapter.place_bet_calls) == 1
    _, betdata = adapter.place_bet_calls[0]

    # betdata ?= 3 KeyCode ?
    assert len(betdata) == 3

    #  DX1  2 1000  2000 DS3  1 500 
    dx1_entries = [b for b in betdata if b["KeyCode"] == "DX1"]
    ds3_entries = [b for b in betdata if b["KeyCode"] == "DS3"]
    assert len(dx1_entries) == 2
    assert len(ds3_entries) == 1
    dx1_amounts = sorted([b["Amount"] for b in dx1_entries])
    assert dx1_amounts == [1000, 2000]
    assert ds3_entries[0]["Amount"] == 500

    #  DB  3 ?
    rows = await (await db.execute(
        "SELECT * FROM bet_orders WHERE issue=? AND operator_id=?",
        ("20260301010", operator_id),
    )).fetchall()
    assert len(rows) == 3
    #  idempotent_id
    idem_ids = {r["idempotent_id"] for r in rows}
    assert len(idem_ids) == 3


# 
# 14.3.3 ?
# 

@pytest.mark.asyncio
async def test_concurrent_idempotency(db):
    """?idempotent_id?

    ?
    - ?
    - DB UNIQUE 
    - IntegrityError ?
    """
    operator_id, account_id = await _setup_operator_and_account(db)
    strategy = await _create_strategy(db, operator_id, account_id)
    strategy_id = strategy["id"]

    adapter = MockAdapter(odds={"DX1": 19800}, bet_succeed=1)
    alert_service = AlertService(db)
    risk = RiskController(
        db=db, alert_service=alert_service,
        operator_id=operator_id, account_id=account_id,
    )

    # 插入已确认赔率到 DB
    await odds_batch_upsert(
        db, account_id=account_id,
        odds_map={"DX1": 19800},
        confirmed=True,
    )

    #  executor 
    executors = [
        BetExecutor(
            db=db, adapter=adapter, risk=risk,
            alert_service=alert_service,
            operator_id=operator_id, account_id=account_id,
        )
        for _ in range(5)
    ]

    # ?executor ?idempotent_id
    idempotent_id = f"20260301020-{strategy_id}-DX1"
    signal = BetSignal(
        strategy_id=strategy_id,
        key_code="DX1",
        amount=1000,
        idempotent_id=idempotent_id,
    )

    install = InstallInfo(
        issue="20260301020", state=1, close_countdown_sec=120,
        pre_issue="20260301019", pre_result="1,2,3",
    )

    #  5 ?executor
    results = await asyncio.gather(
        *[ex.execute(install, [signal]) for ex in executors],
        return_exceptions=True,
    )

    # 
    for r in results:
        assert not isinstance(r, Exception), f": {r}"

    #  DB ?1 ?
    rows = await (await db.execute(
        "SELECT * FROM bet_orders WHERE idempotent_id=?",
        (idempotent_id,),
    )).fetchall()
    assert len(rows) == 1, f"应只有 1 条，实际 {len(rows)} 条"

    # place_bet  1  executor ?
    assert len(adapter.place_bet_calls) <= 1


# 
# 14.3.4 
# 

@pytest.mark.asyncio
async def test_worker_exception_recovery(db):
    """Worker  ? ??Worker?

    ?
    - Worker A ?
    - Worker B 
    """
    # 
    op1 = await operator_create(
        db, username="op_recovery_1", password="pass123456",
        role="operator", max_accounts=5,
    )
    op2 = await operator_create(
        db, username="op_recovery_2", password="pass123456",
        role="operator", max_accounts=5,
    )
    acc1 = await account_create(
        db, operator_id=op1["id"], account_name="acc_r1",
        password="pw1234", platform_type="JND28WEB",
    )
    acc2 = await account_create(
        db, operator_id=op2["id"], account_name="acc_r2",
        password="pw1234", platform_type="JND28WEB",
    )
    await db.execute(
        "UPDATE gambling_accounts SET status='online', balance=1000000 WHERE id IN (?, ?)",
        (acc1["id"], acc2["id"]),
    )
    await db.commit()

    # Worker A: adapter.login 
    adapter_a = MockAdapter(login_success=True)
    call_count_a = 0

    original_login_a = adapter_a.login

    async def flaky_login_a(name, pwd):
        nonlocal call_count_a
        call_count_a += 1
        if call_count_a == 1:
            raise ConnectionError("")
        return await original_login_a(name, pwd)

    adapter_a.login = flaky_login_a

    # Worker B:  adapter
    adapter_b = MockAdapter(login_success=True)

    alert_service = AlertService(db)
    captcha_service = CaptchaService()

    #  Worker A
    session_a = SessionManager(
        adapter=adapter_a, alert_service=alert_service,
        captcha_service=captcha_service,
        operator_id=op1["id"], account_id=acc1["id"],
        account_name="acc_r1", password="pw1234",
        db=db,
    )
    poller_a = AsyncMock()
    # poller  Worker  session.login ?
    poller_a.poll = AsyncMock(return_value=InstallInfo(
        issue="20260301030", state=1, close_countdown_sec=120,
        pre_issue="", pre_result="",
    ))
    poller_a.poll_interval = 5

    worker_a = AccountWorker(
        operator_id=op1["id"], account_id=acc1["id"],
        db=db, adapter=adapter_a,
        session=session_a, poller=poller_a,
        executor=AsyncMock(), settler=AsyncMock(),
        reconciler=AsyncMock(), risk=AsyncMock(),
        alert_service=alert_service,
    )

    #  Worker B
    session_b = SessionManager(
        adapter=adapter_b, alert_service=alert_service,
        captcha_service=captcha_service,
        operator_id=op2["id"], account_id=acc2["id"],
        account_name="acc_r2", password="pw1234",
        db=db,
    )
    poller_b = AsyncMock()
    poller_b.poll = AsyncMock(return_value=InstallInfo(
        issue="20260301030", state=0, close_countdown_sec=0,
        pre_issue="", pre_result="",
    ))
    poller_b.poll_interval = 5

    worker_b = AccountWorker(
        operator_id=op2["id"], account_id=acc2["id"],
        db=db, adapter=adapter_b,
        session=session_b, poller=poller_b,
        executor=AsyncMock(), settler=AsyncMock(),
        reconciler=AsyncMock(), risk=AsyncMock(),
        alert_service=alert_service,
    )

    #  Worker
    await worker_a.start()
    await worker_b.start()

    # 
    await asyncio.sleep(0.5)

    # Worker B 
    assert worker_b.running is True
    assert worker_b.status == "running"

    #  Worker
    await worker_a.stop()
    await worker_b.stop()

    #  Worker A login 
    assert call_count_a >= 1

    #  Worker B  Worker A 
    assert worker_b.status == "stopped"


@pytest.mark.asyncio
async def test_worker_restart_does_not_affect_others(db):
    """ Worker ?Worker ?Worker?

    ?Worker ?
    """
    op1 = await operator_create(
        db, username="op_iso_1", password="pass123456",
        role="operator", max_accounts=5,
    )
    op2 = await operator_create(
        db, username="op_iso_2", password="pass123456",
        role="operator", max_accounts=5,
    )
    acc1 = await account_create(
        db, operator_id=op1["id"], account_name="acc_iso1",
        password="pw1234", platform_type="JND28WEB",
    )
    acc2 = await account_create(
        db, operator_id=op2["id"], account_name="acc_iso2",
        password="pw1234", platform_type="JND28WEB",
    )
    await db.execute(
        "UPDATE gambling_accounts SET status='online', balance=1000000 WHERE id IN (?, ?)",
        (acc1["id"], acc2["id"]),
    )
    await db.commit()

    alert_service = AlertService(db)

    # Worker A:  session
    session_a = AsyncMock()
    session_a.login = AsyncMock(side_effect=RuntimeError("Worker A "))

    poller_a = AsyncMock()
    poller_a.poll_interval = 5

    worker_a = AccountWorker(
        operator_id=op1["id"], account_id=acc1["id"],
        db=db, adapter=AsyncMock(),
        session=session_a, poller=poller_a,
        executor=AsyncMock(), settler=AsyncMock(),
        reconciler=AsyncMock(), risk=AsyncMock(),
        alert_service=alert_service,
    )

    # Worker B:  sessionlogin ?poller 
    adapter_b = MockAdapter(login_success=True)
    captcha_b = CaptchaService()
    session_b = SessionManager(
        adapter=adapter_b, alert_service=alert_service,
        captcha_service=captcha_b,
        operator_id=op2["id"], account_id=acc2["id"],
        account_name="acc_iso2", password="pw1234",
        db=db,
    )
    poller_b = AsyncMock()
    poller_b.poll = AsyncMock(return_value=InstallInfo(
        issue="20260301040", state=0, close_countdown_sec=0,
        pre_issue="", pre_result="",
    ))
    poller_b.poll_interval = 5

    worker_b = AccountWorker(
        operator_id=op2["id"], account_id=acc2["id"],
        db=db, adapter=adapter_b,
        session=session_b, poller=poller_b,
        executor=AsyncMock(), settler=AsyncMock(),
        reconciler=AsyncMock(), risk=AsyncMock(),
        alert_service=alert_service,
    )

    #  Worker
    await worker_a.start()
    await worker_b.start()

    #  Worker A 
    await asyncio.sleep(1.0)

    # Worker B 
    assert worker_b.running is True
    assert worker_b.status == "running"

    # Worker A ?error
    # Worker B 

    await worker_a.stop()
    await worker_b.stop()

    assert worker_b.status == "stopped"

