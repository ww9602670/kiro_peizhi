# Design Document

## 概述

将投注引擎的结算架构从"轮询检测期号变化"改为"倒计时驱动"模式，同时将真实投注的结算数据源从本地自计算切换为平台 Topbetlist API。

## 架构变更

### 当前架构（Before）

```
Worker._main_loop:
  while running:
    install = poller.poll()          # 每 5s 轮询
    if install.is_new_issue:         # 检测期号变化
      settler.settle(pre_issue)      # 100% 本地自计算
      reconciler.reconcile()         # 余额比较对账
    if should_bet:
      executor.execute(signals)
    sleep(poll_interval)
```

### 新架构（After）

```
Worker._main_loop:
  while running:
    install = fetch_install()
    # 投注阶段：等待封盘
    await bet_phase(install)
    # 等待开奖：sleep(OpenTimeStamp)
    await asyncio.sleep(install.open_countdown_sec)
    # 额外等待：sleep(settlement_wait_seconds)
    await asyncio.sleep(settlement_wait_seconds)
    # 结算阶段：拉取新数据 + 结算
    new_install = await fetch_with_retry()
    save_lottery_result(new_install)
    settler.settle(issue, balls, sum_value, adapter)
    reconciler.reconcile()
```

## 详细设计

### 1. Worker 主循环重写

**文件**: `backend/app/engine/worker.py`

#### 1.1 倒计时驱动循环

```python
async def _main_loop(self) -> None:
    await self.session.login()
    
    # 启动时补结算
    await self._recover_unsettled_orders()
    
    while self.running:
        # 1. 获取当前期号信息
        install = await self._fetch_install_with_retry()
        if install is None:
            await asyncio.sleep(60)
            continue
        
        # 2. 记录当前期号
        current_issue = install.issue
        pre_issue = install.pre_issue
        
        # 3. 投注阶段（State=1 时收集信号并下注）
        if install.state == 1 and self._should_bet(install):
            signals = self._collect_signals(install)
            if signals:
                await self.executor.execute(install, signals)
        
        # 4. 等待开奖倒计时归零
        if install.open_countdown_sec > 0:
            await asyncio.sleep(install.open_countdown_sec)
        
        # 5. 额外等待 settlement_wait_seconds
        await asyncio.sleep(self._settlement_wait_seconds)
        
        # 6. 拉取新期号 + 上期开奖结果
        new_install = await self._fetch_settlement_data(pre_issue)
        if new_install is None:
            continue  # 已发告警，跳过本期
        
        # 7. 持久化开奖结果
        balls, sum_value = _parse_result(new_install.pre_result)
        await self.settler._save_lottery_result(
            new_install.pre_issue,
            new_install.pre_result,
            sum_value,
        )
        
        # 8. 执行结算（正常模式，不包含可恢复终态）
        await self.settler.settle(
            issue=new_install.pre_issue,
            balls=balls,
            sum_value=sum_value,
            platform_type=self._platform_type,
            adapter=self.adapter,
        )
        
        # 9. 对账
        await self.reconciler.reconcile(
            issue=new_install.pre_issue,
            account_id=self.account_id,
        )
```

#### 1.2 配置参数

```python
# worker.py 新增常量
SETTLEMENT_WAIT_SECONDS_DEFAULT = 30
SETTLEMENT_WAIT_SECONDS_MIN = 10
SETTLEMENT_WAIT_SECONDS_MAX = 120

# 结算数据拉取重试
SETTLE_DATA_RETRY_MAX = 6
SETTLE_DATA_RETRY_INTERVAL = 5

# GetCurrentInstall 网络重试
API_RETRY_DELAYS = [5, 10, 30]
API_RETRY_MAX = 3
```

#### 1.3 补结算逻辑

```python
async def _recover_unsettled_orders(self) -> None:
    """重启时补结算：扫描 bet_success/pending_match/settle_timeout/settle_failed 订单"""
    rows = await self.db.execute(
        "SELECT DISTINCT issue FROM bet_orders "
        "WHERE account_id=? AND operator_id=? "
        "AND status IN ('bet_success', 'pending_match', 'settle_timeout', 'settle_failed')",
        (self.account_id, self.operator_id),
    )
    issues = [r["issue"] for r in await rows.fetchall()]
    if not issues:
        return
    
    for issue in issues:
        # 从 Lotteryresult 获取历史开奖结果
        results = await self.adapter.get_lottery_results(count=50)
        result_map = {str(r["Installments"]): r["OpenResult"] for r in results}
        
        if issue in result_map:
            balls, sum_value = _parse_result(result_map[issue])
            await self.settler.settle(
                issue=issue, balls=balls, sum_value=sum_value,
                platform_type=self._platform_type, adapter=self.adapter,
                is_recovery=True,  # 补结算模式：包含可恢复终态
            )
```

#### 1.4 结算数据拉取（含重试）

```python
async def _fetch_settlement_data(self, expected_pre_issue: str) -> InstallInfo | None:
    """拉取新期号，验证 PreLotteryResult 有效性，最多重试 6 次"""
    for attempt in range(SETTLE_DATA_RETRY_MAX):
        install = await self._fetch_install_with_retry()
        if install is None:
            return None
        
        pre_result = install.pre_result
        if pre_result and pre_result.strip() and install.pre_issue == expected_pre_issue:
            return install
        
        await asyncio.sleep(SETTLE_DATA_RETRY_INTERVAL)
    
    # 6 次重试失败
    await self.alert_service.send(
        operator_id=self.operator_id,
        alert_type="settlement_data_missing",
        title=f"结算数据缺失 期号 {expected_pre_issue}",
        detail=f"重试 {SETTLE_DATA_RETRY_MAX} 次后仍无有效开奖结果",
        account_id=self.account_id,
    )
    return None
```

### 2. SettlementProcessor 重写

**文件**: `backend/app/engine/settlement.py`

#### 2.1 新状态机

```python
VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"betting"},
    "betting": {"bet_success", "bet_failed"},
    "bet_success": {"settling", "pending_match", "settle_timeout", "settle_failed"},
    "settling": {"settled", "settle_failed", "settle_timeout"},
    "pending_match": {"settling", "settle_timeout"},
    "settled": {"reconcile_error"},
    "bet_failed": set(),
    "settle_timeout": {"settling"},   # 高优先级来源可覆盖（补结算获取到平台数据）
    "settle_failed": {"settling"},    # 高优先级来源可覆盖（API 恢复后重新结算）
    "reconcile_error": set(),
}

TERMINAL_STATES = {"bet_failed", "settled", "settle_timeout", "settle_failed", "reconcile_error"}

# settle_timeout: 平台数据可达但未找到匹配记录（匹配超时）
# settle_failed: 平台 API 不可达或开奖数据缺失（系统故障）
# 两者互斥，同一订单只会进入其中一个终态
# settle_timeout/settle_failed 可被高优先级来源（平台数据匹配）覆盖
# settled/bet_failed/reconcile_error 不可覆盖

# pending_match wall-clock 超时：30 分钟
PENDING_MATCH_WALL_CLOCK_TIMEOUT = 30 * 60  # 秒
```

#### 2.2 结算入口（路径分发）

```python
async def settle(
    self,
    issue: str,
    balls: list[int],
    sum_value: int,
    platform_type: str,
    adapter: PlatformAdapter | None = None,
    is_recovery: bool = False,
) -> None:
    """结算入口：按 simulation 分发到真实/模拟路径
    
    is_recovery=True 时为补结算模式，额外包含 settle_timeout/settle_failed 订单
    """
    # 保存开奖结果
    open_result = ",".join(str(b) for b in balls)
    await self._save_lottery_result(issue, open_result, sum_value)
    
    # 查询待结算订单（补结算模式包含可恢复终态）
    orders = await self._get_settleable_orders(issue, include_recoverable=is_recovery)
    if not orders:
        return
    
    # 按 simulation 分组
    real_orders = [o for o in orders if o.get("simulation", 0) == 0]
    sim_orders = [o for o in orders if o.get("simulation", 0) == 1]
    
    # 先模拟后真实
    if sim_orders:
        await self._settle_simulated(sim_orders, balls, sum_value, platform_type, open_result)
    
    if real_orders and adapter is not None:
        await self._settle_real(real_orders, issue, adapter, open_result, sum_value)
```

#### 2.3 真实投注结算路径

```python
async def _settle_real(
    self,
    orders: list[dict],
    issue: str,
    adapter: PlatformAdapter,
    open_result: str,
    sum_value: int,
) -> None:
    """真实投注结算：从平台获取数据"""
    # 1. 更新余额（QueryResult）
    try:
        balance_info = await self._retry_api(adapter.query_balance)
        platform_balance = int(balance_info.balance * 100)
        await self._update_account_balance_absolute(
            orders[0]["account_id"], platform_balance
        )
    except Exception:
        logger.exception("QueryResult 失败")
    
    # 2. 获取平台投注记录（Topbetlist）
    try:
        platform_bets = await self._retry_api(
            lambda: adapter.get_bet_history(count=15)
        )
    except Exception:
        # 3 次重试全部失败
        await self._mark_orders_settle_failed(orders)
        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="settle_api_failed",
            title=f"Topbetlist 调用失败 期号 {issue}",
            detail="3 次重试全部失败",
        )
        return
    
    # 3. 匹配平台记录与本地订单
    await self._match_and_settle(orders, platform_bets, issue, open_result, sum_value)
```

#### 2.4 平台数据匹配算法

```python
async def _match_and_settle(
    self,
    orders: list[dict],
    platform_bets: list[dict],
    issue: str,
    open_result: str,
    sum_value: int,
) -> None:
    """使用 (Installments, KeyCode, Amount) 匹配，含歧义检测"""
    # 构建平台记录索引：(issue, key_code, amount_fen) -> [records]
    platform_index: dict[tuple, list[dict]] = {}
    for bet in platform_bets:
        bet_issue = str(bet.get("Installments", ""))
        key_code = str(bet.get("KeyCode", ""))
        # 平台金额单位为元，转为分
        amount_fen = int(float(bet.get("Amount", 0)) * 100)
        key = (bet_issue, key_code, amount_fen)
        platform_index.setdefault(key, []).append(bet)
    
    # 按 bet_at 升序排列本地订单（bet_at 相同时按 id 升序）
    sorted_orders = sorted(orders, key=lambda o: (o.get("bet_at", ""), o.get("id", 0)))
    
    # 按 Match_Key 分组本地订单，检测歧义
    local_groups: dict[tuple, list[dict]] = {}
    for order in sorted_orders:
        match_key = (issue, order["key_code"], order["amount"])
        local_groups.setdefault(match_key, []).append(order)
    
    strategy_pnl_map: dict[int, int] = {}
    
    for match_key, group_orders in local_groups.items():
        candidates = platform_index.get(match_key, [])
        
        # 歧义检测：同一 Match_Key 下本地订单数 >= 2 且平台记录 WinAmount 不全相同
        if len(group_orders) >= 2 and len(candidates) >= 2:
            win_amounts = [float(c.get("WinAmount", 0)) for c in candidates]
            if len(set(win_amounts)) > 1:
                # 歧义：WinAmount 不全相同，降级为 pending_match
                for order in group_orders:
                    await self._mark_pending_match(order)
                await self.alert_service.send(
                    operator_id=self.operator_id,
                    alert_type="match_ambiguity",
                    title=f"匹配歧义 期号 {issue} KeyCode={match_key[1]}",
                    detail=json.dumps({
                        "issue": issue,
                        "key_code": match_key[1],
                        "amount": match_key[2],
                        "local_count": len(group_orders),
                        "platform_count": len(candidates),
                        "win_amounts": win_amounts,
                    }),
                )
                continue  # 跳过该组，等待人工处理
        
        # 无歧义或单条订单：正常逐一配对
        for order in group_orders:
            if candidates:
                platform_record = candidates.pop(0)  # 消耗一条
                win_amount_yuan = float(platform_record.get("WinAmount", 0))
                win_amount_fen = int(win_amount_yuan * 100)
                
                is_win = 1 if win_amount_fen > 0 else 0
                pnl = win_amount_fen - order["amount"] if win_amount_fen > 0 else -order["amount"]
                
                # 更新订单
                await self._settle_order_from_platform(
                    order, is_win, pnl, open_result, sum_value, "platform"
                )
                
                sid = order["strategy_id"]
                strategy_pnl_map[sid] = strategy_pnl_map.get(sid, 0) + pnl
            else:
                # 未匹配，标记 pending_match
                await self._mark_pending_match(order)
    
    # 更新策略 PnL
    for sid, pnl_delta in strategy_pnl_map.items():
        await self._update_strategy_pnl(sid, pnl_delta)
```

#### 2.5 模拟投注结算路径

保留现有 `_calculate_result` 逻辑，增加 `match_source="local"` 标记。

#### 2.6 API 重试工具方法

```python
async def _retry_api(self, func, max_retries=3, interval=5):
    """通用 API 重试，固定间隔 5 秒，最多 3 次"""
    last_error = None
    for attempt in range(max_retries):
        try:
            return await func()
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                await asyncio.sleep(interval)
    raise last_error
```

### 3. 数据库变更

#### 3.1 bet_orders 表新增字段

```sql
ALTER TABLE bet_orders ADD COLUMN match_source TEXT DEFAULT NULL;
-- 值: "platform" | "local" | NULL(未结算)

ALTER TABLE bet_orders ADD COLUMN pending_match_count INTEGER DEFAULT 0;
-- 用于跟踪 pending_match 周期数
```

#### 3.2 新增订单状态

现有状态: `pending`, `betting`, `bet_success`, `bet_failed`, `settled`, `reconcile_error`

新增状态: `settling`, `pending_match`, `settle_timeout`, `settle_failed`

#### 3.3 AlertService 新增告警类型

```python
# alert.py ALERT_LEVEL_MAP 新增
"settlement_data_missing": "warning",
"settle_api_failed": "critical",
"settle_timeout": "warning",
"unsettled_orders": "warning",
"api_call_failed": "critical",
"settle_data_expired": "critical",
"topbetlist_coverage_warning": "warning",
"match_ambiguity": "warning",
"worker_lock_conflict": "critical",
"worker_lock_lost": "critical",
```

### 4. Reconciler 更新

**文件**: `backend/app/engine/reconciler.py`

#### 4.1 真实模式对账简化

```python
async def reconcile(self, account_id: int, issue: str) -> None:
    # 查询该期号所有订单
    all_orders = await self._get_all_orders_for_issue(account_id, issue)
    
    real_orders = [o for o in all_orders if o.get("simulation", 0) == 0]
    sim_orders = [o for o in all_orders if o.get("simulation", 0) == 1]
    
    # 真实模式：验证所有订单在终态
    if real_orders:
        await self._reconcile_real(account_id, issue, real_orders)
    
    # 模拟模式：保持现有余额比较逻辑
    if sim_orders:
        await self._reconcile_simulated(account_id, issue, sim_orders)
```

#### 4.2 真实模式对账

```python
async def _reconcile_real(self, account_id, issue, orders):
    non_terminal = [o for o in orders if o["status"] not in TERMINAL_STATES]
    if non_terminal:
        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="unsettled_orders",
            title=f"未结算订单 期号 {issue}",
            detail=json.dumps({
                "count": len(non_terminal),
                "issue": issue,
                "statuses": [o["status"] for o in non_terminal],
            }),
            account_id=account_id,
        )
```

### 5. PlatformAdapter 接口无需变更

现有 `get_bet_history()` 和 `query_balance()` 已满足需求。`JNDAdapter` 实现已包含 Topbetlist 和 QueryResult 调用。

### 6. 幂等性与并发控制

#### 6.1 结算幂等性保证

```python
async def _get_settleable_orders(self, issue: str, include_recoverable: bool = False) -> list[dict]:
    """查询可结算订单
    
    默认模式（正常结算）：status IN ('bet_success', 'pending_match')
    恢复模式（补结算）：额外包含 settle_timeout, settle_failed（可被高优先级覆盖）
    
    已在不可覆盖终态（settled, bet_failed, reconcile_error）的订单不会被查出。
    """
    statuses = ['bet_success', 'pending_match']
    if include_recoverable:
        statuses.extend(['settle_timeout', 'settle_failed'])
    
    placeholders = ','.join('?' * len(statuses))
    rows = await (
        await self.db.execute(
            f"SELECT * FROM bet_orders WHERE issue=? AND operator_id=? "
            f"AND status IN ({placeholders})",
            (issue, self.operator_id, *statuses),
        )
    ).fetchall()
    return [dict(r) for r in rows]
```

正常结算流程调用 `_get_settleable_orders(issue)` — 仅处理 bet_success/pending_match，符合 R9 幂等性要求。
补结算流程调用 `_get_settleable_orders(issue, include_recoverable=True)` — 额外包含 settle_timeout/settle_failed，使优先级仲裁可达。

#### 6.2 状态转换原子性

所有状态转换使用条件更新（WHERE status=current_status），确保并发安全：

```python
async def _atomic_transition(self, order_id: int, from_status: str, to_status: str, **extra) -> bool:
    """原子状态转换，返回是否成功（CAS 语义）"""
    self._transition_status({"status": from_status}, to_status)  # 验证合法性
    
    set_parts = ["status=?"]
    values = [to_status]
    for k, v in extra.items():
        set_parts.append(f"{k}=?")
        values.append(v)
    
    values.extend([order_id, self.operator_id, from_status])
    cursor = await self.db.execute(
        f"UPDATE bet_orders SET {', '.join(set_parts)} "
        "WHERE id=? AND operator_id=? AND status=?",
        tuple(values),
    )
    await self.db.commit()
    return cursor.rowcount > 0  # 0 表示已被其他流程处理
```

#### 6.3 补结算与主循环互斥

`_recover_unsettled_orders()` 在主循环启动前同步执行（非并发），补结算完成后才进入倒计时循环。Worker 是单线程 asyncio，同一 Worker 实例内不存在并发结算。

**部署约束：单实例 Worker**

当前架构为单进程部署，每个 account_id 最多对应一个 AccountWorker 实例，由 EngineManager.WorkerRegistry 保证：
- `start_worker()` 在创建新 Worker 前检查并停止已有 Worker（见 manager.py）
- 不存在多实例/多进程同时操作同一 account_id 的场景
- 因此 `_atomic_transition` 的 CAS 语义足以保证并发安全（仅需防护 asyncio 协程级并发）
- 若未来需要多实例部署，需引入分布式锁（如 Redis SETNX）替代内存级 WorkerRegistry，此为后续迭代范围

**运行时防护**：
- `start_worker()` 在创建新 Worker 前调用 `registry.get(account_id)`，若已存在 running Worker 则先 stop 再创建（防止重复实例）
- 若因异常导致 Registry 状态不一致（如进程崩溃后重启），`restore_workers_on_startup()` 会重建所有 Worker，旧的孤儿 Worker 因进程已终止而自然消亡

**跨进程互斥（必选约束）**：
- Worker 启动时生成唯一 `lock_token`（UUID4），在 `gambling_accounts` 表写入 `worker_lock_token` 和 `worker_lock_ts` 字段
- 抢锁使用 CAS：`UPDATE gambling_accounts SET worker_lock_token=?, worker_lock_ts=datetime('now') WHERE id=? AND (worker_lock_token IS NULL OR worker_lock_ts < datetime('now', '-5 minutes'))` — 使用 DB 时间（`datetime('now')`），消除应用时钟漂移风险
- Worker 运行期间每 60 秒续约：`UPDATE gambling_accounts SET worker_lock_ts=datetime('now') WHERE id=? AND worker_lock_token=?` — 仅当前持锁者可续约。续约周期 60s << TTL 300s（TTL/5），满足 `<= TTL/3` 约束
- 续约失败处理（rowcount=0）：Worker 判定"已失锁"，立即设置 `self.running = False` 并发送 `alert_type="worker_lock_lost"` 告警，退出主循环。确保失锁后不再执行任何业务副作用（投注、结算、对账）
- Worker 停止时释放锁：`UPDATE gambling_accounts SET worker_lock_token=NULL, worker_lock_ts=NULL WHERE id=? AND worker_lock_token=?` — 仅当前持锁者可释放
- 若抢锁 CAS 失败（已有活跃锁），`start_worker()` 拒绝启动并发送 `alert_type="worker_lock_conflict"` 告警
- 竞态安全：A 超时后 B 抢锁成功，A 的续约因 `WHERE worker_lock_token=A_token` 不匹配返回 rowcount=0，A 检测到失锁并停止，不影响 B
- 此机制依赖 SQLite 条件更新（CAS）的原子性与事务边界，时间源统一使用 SQLite `datetime('now')` 函数
- 若未来迁移到多机部署，需替换为 Redis：`SET key token NX EX 300` 抢锁 + Lua 脚本 `compare-and-renew/compare-and-delete`（`if redis.call('get',KEYS[1])==ARGV[1] then ...`）保持持锁者校验语义

#### 6.4 终态仲裁规则：来源优先级

同一订单的终态由"来源优先级"仲裁，而非简单的"先到先得"。优先级从高到低：

| 优先级 | 来源 | 终态 | 说明 |
|--------|------|------|------|
| 1（最高） | 平台数据匹配 | settled (match_source=platform) | Topbetlist 成功匹配 |
| 2 | 本地自计算 | settled (match_source=local) | 模拟模式或降级结算 |
| 3（最低） | 超时/故障 | settle_timeout / settle_failed | 无数据可用 |

**仲裁规则**：
- 高优先级来源可以覆盖低优先级终态：若订单已处于 `settle_timeout`（优先级 3），后续补结算获取到平台数据（优先级 1），允许 `settle_timeout → settling → settled` 转换
- 同优先级或低优先级来源不可覆盖：已 `settled` 的订单不可被再次结算
- CAS 语义保证并发安全：`_atomic_transition` 的 WHERE status=current_status 确保只有一个流程成功

**状态机扩展**（支持终态覆盖）：
```python
VALID_TRANSITIONS: dict[str, set[str]] = {
    ...
    # 终态覆盖（仅限高优先级来源）
    "settle_timeout": {"settling"},   # 补结算获取到平台数据时可重新结算
    "settle_failed": {"settling"},    # API 恢复后可重新结算
    # settled 和 bet_failed 不可覆盖
    "settled": {"reconcile_error"},
    "bet_failed": set(),
    "reconcile_error": set(),
}
```

**覆盖条件检查**：
```python
async def _atomic_transition_with_priority(
    self, order_id: int, from_status: str, to_status: str,
    source_priority: int, **extra
) -> bool:
    """带优先级的原子状态转换
    
    source_priority: 1=platform, 2=local, 3=timeout/failed
    仅当 new_priority < current_priority 时允许终态覆盖
    """
    # 对于非终态 → 终态的正常转换，直接执行
    if from_status not in TERMINAL_STATES:
        return await self._atomic_transition(order_id, from_status, to_status, **extra)
    
    # 对于终态覆盖，检查优先级
    current_priority = self._get_source_priority(from_status, order_id)
    if source_priority >= current_priority:
        return False  # 同级或低优先级，不覆盖
    
    return await self._atomic_transition(order_id, from_status, to_status, **extra)
```

**设计理由**：
- "先到先得"可能固化错误终态（如网络抖动导致 settle_timeout，但平台数据实际可用）
- 来源优先级确保"更可靠的数据源"始终能修正"不可靠的判断"
- `settled` (match_source=platform) 是最终真相，不可被覆盖
- `bet_failed` 由 BetExecutor 写入，表示投注本身失败，不参与结算仲裁

### 7. 匹配冲突处理

#### 7.1 同值重复单场景

当同一期号、同一 KeyCode、同一 Amount 存在多条本地订单时：
- 本地订单按 (bet_at ASC, id ASC) 排序
- 平台记录按返回顺序（即平台下注时间顺序）排列
- 逐一配对消耗，确保 1:1 匹配
- 若平台记录数 < 本地订单数，多余订单进入 pending_match

#### 7.2 歧义检测机制（Ambiguity Detection）

当同一 Match_Key (期号, KeyCode, Amount) 存在 N≥2 条本地订单时，匹配结果可能因平台返回顺序与本地下注顺序不一致而错配。为此引入歧义检测：

**检测条件**：同一 Match_Key 下本地订单数 N ≥ 2

**检测逻辑**：匹配完成后，对该 Match_Key 下所有已匹配的订单组，验证 WinAmount 模式一致性：
- 若该组内所有平台记录的 WinAmount 相同（全赢或全输），则无论配对顺序如何，每条订单的 pnl 结果相同 → 无歧义，正常结算
- 若该组内平台记录的 WinAmount 不全相同（有赢有输），则存在错配风险 → 触发歧义处理

**歧义处理**：
- 将该 Match_Key 下所有订单标记为 `pending_match`（即使已有匹配候选）
- 发送 `alert_type="match_ambiguity"` 告警，detail 包含 issue、key_code、amount、本地订单数、平台记录数、WinAmount 列表
- 运维人员收到告警后人工核实并通过管理接口手动结算（手动结算接口不在本 spec 范围内，后续迭代实现）

**设计理由**：
- 同期同码同额多注在当前业务中极少发生（策略通常每期每个 play_code 只投一注）
- 当 WinAmount 全部相同时，错配不影响结果正确性，无需干预
- 当 WinAmount 不同时，自动配对无法保证正确性，降级为人工处理是最安全的选择
- 此机制将"不可检测的错配"转化为"可检测且可干预的歧义"

**审计可追溯性**：
- 平台 Topbetlist 返回的每条记录落库到 `bet_order_platform_records` 表（issue, key_code, amount, win_amount, raw_json），供审计追溯
- 当前平台 API 不返回唯一订单标识（无 bet_id），因此无法做精确 1:1 身份映射，歧义检测是当前条件下的最优方案
- 若平台未来提供唯一标识，可升级为精确匹配，消除歧义检测的需要

#### 7.3 匹配正确性边界

Match_Key 三元组在当前业务场景下具有足够区分度：
- 同一期号内，同一 KeyCode + 同一 Amount 的重复投注极少（策略通常每期每个 play_code 只投一注）
- 即使出现重复，7.2 歧义检测机制确保错配可被发现并降级处理
- 无歧义场景（WinAmount 全同）下，bet_at + id 排序保证确定性配对

## 属性→机制→失败模式→测试映射

| # | 正确性属性 | 保障机制 | 失败模式 | 检测方式 |
|---|-----------|---------|---------|---------|
| P1 | 状态机合法性 | VALID_TRANSITIONS 白名单 + IllegalStateTransition | 非法转换被静默执行 | PBT: 穷举所有 (from, to) 对验证 |
| P2 | 结算路径分发 | simulation 字段分组 + 先模拟后真实 | 真实订单走模拟路径或反之 | PBT: 任意订单集验证分组完整性 |
| P3 | 匹配唯一性 | 逐一消耗 + pop(0) | 一条平台记录匹配多条订单 | PBT: 验证消耗后无重复 |
| P4 | 模拟 PnL 一致性 | check_win + odds 整数运算 | 浮点精度导致 pnl 偏差 | PBT: 任意 (amount, odds, is_win) 验证公式 |
| P5 | 余额更新正确性 | int(accountLimit * 100) 截断 | 浮点→整数转换错误 | PBT: 任意浮点值验证转换 |
| P6 | 终态验证 | TERMINAL_STATES 集合检查 | 非终态订单被漏检 | PBT: 任意状态集验证告警触发 |
| P7 | match_source 一致性 | 路径分发时写入 | 真实订单标记为 local | PBT: 验证 simulation↔match_source 映射 |
| P8 | 结算幂等性 | _get_settleable_orders 过滤终态 + CAS | 重复 settle 导致重复记账 | PBT: 重复调用验证状态/pnl 不变 |
| P9 | 歧义检测 | WinAmount 模式一致性检查 | 同值多单错配不可检测 | 单元测试: 全同/不同 WinAmount 场景 |
| P10 | 终态覆盖优先级 | source_priority 仲裁 | 低优先级覆盖高优先级终态 | 单元测试: 优先级覆盖场景 |
| P11 | pending_match 超时终态 | Settle_Timeout_Cycles=2 + wall-clock 30min | 订单永不终态 | 单元测试: 2周期后→settle_timeout, 30min后→settle_timeout |
| P12 | settle_timeout/settle_failed 互斥 | 状态机入口条件不同 | 同一订单同时进入两个终态 | PBT: 任意订单路径验证互斥 |
| P13 | 优先级覆盖路径可达性 | _recover + include_recoverable=True | settle_timeout/failed 订单无法被重新结算 | 单元测试: 补结算成功覆盖 settle_timeout→settled |

## 正确性属性（Correctness Properties）

以下属性使用 hypothesis 框架进行属性测试。

### Property 1: 状态机合法性
**Validates: Requirements 8.1, 8.2, 8.3, 8.4**

对于所有 (current_status, target_status) 对：
- 若 target_status ∈ VALID_TRANSITIONS[current_status]，则 `_transition_status` 不抛异常
- 若 target_status ∉ VALID_TRANSITIONS[current_status]，则 `_transition_status` 抛出 `IllegalStateTransition`

### Property 2: 结算路径分发正确性
**Validates: Requirements 4.1, 4.2, 4.3**

对于任意订单集合 orders（每个订单有 simulation ∈ {0, 1}）：
- simulation=0 的订单全部进入真实结算路径
- simulation=1 的订单全部进入模拟结算路径
- 两组的并集 == 原始集合（无遗漏无重复）

### Property 3: 平台数据匹配唯一性
**Validates: Requirements 5.1, 5.2**

对于任意平台记录集 P 和本地订单集 L：
- 每条平台记录最多被消耗一次
- 每条本地订单最多匹配一条平台记录
- 未匹配的本地订单状态为 pending_match

### Property 4: 模拟结算 PnL 计算一致性
**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

对于任意 (key_code, balls, sum_value, amount, odds)：
- is_win=1 时：pnl == amount * odds // 10000 - amount
- is_win=0 时：pnl == -amount
- is_win=-1 时：pnl == 0

### Property 5: 余额更新正确性（真实模式）
**Validates: Requirements 2.1**

对于任意 accountLimit 浮点值 v（v >= 0）：
- 写入的 balance == int(v * 100)

### Property 6: 终态验证（真实模式对账）
**Validates: Requirements 6.1, 6.2**

对于任意订单状态集合 S：
- 若 S ⊆ TERMINAL_STATES，则对账通过（无告警）
- 若 ∃s ∈ S, s ∉ TERMINAL_STATES，则对账触发 unsettled_orders 告警

### Property 7: match_source 标记一致性
**Validates: Requirements 5.4**

对于所有已结算订单：
- simulation=0 且 status=settled → match_source ∈ {"platform", "local"}
- simulation=1 且 status=settled → match_source == "local"

### Property 8: 结算幂等性
**Validates: Requirements 9.1, 9.3**

对于任意已结算订单集合（status ∈ TERMINAL_STATES）：
- 对同一 issue 重复调用 settle() 后，订单状态、pnl、balance 均不变
- strategy 的 daily_pnl/total_pnl 不会被重复累加

## 测试框架

- 单元测试: pytest
- 属性测试: hypothesis
- 测试文件位置: `backend/tests/`

## Review
| 日期 | 版本 | 结论 | 关键 P0 |
|------|------|------|---------|
| - | v1 | FAIL | 4 P0：幂等性不闭环、匹配冲突、终态仲裁缺失、告警映射 |
| - | v2 | FAIL | 2 P0：错配不可检测、终态固化错误；2 P1：单实例约束未说明、属性映射缺失 |
| - | v3 | FAIL | 1 P0：优先级仲裁不可达（settle_timeout/failed 订单未被补结算扫描）；3 P1 |
| - | v4 | FAIL | 0 P0; 1 P1：单实例约束跨进程防护不充分 |
| - | v5 | FAIL | 1 P0：持锁者校验不完整（PID 无 token，续约/释放可覆盖他人锁）；2 P1 |
| - | v6 | FAIL | 1 P0：续约失败后 Worker 未停止（双执行风险）；2 P1：时间源/Redis 方案 |
| - | v7 | PASS | 0 P0, 0 P1。codex-gpt53 审核通过 |
