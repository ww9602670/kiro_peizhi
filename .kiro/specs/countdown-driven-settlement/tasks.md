# Implementation Tasks

## Task 1: 数据库 Schema 变更与状态机实现

- [x] 1.1 在 `backend/app/database.py` 中添加 migration：bet_orders 表新增 `match_source TEXT DEFAULT NULL` 和 `pending_match_count INTEGER DEFAULT 0` 字段
- [x] 1.2 在 `backend/app/database.py` 中添加 migration：gambling_accounts 表新增 `worker_lock_token TEXT DEFAULT NULL` 和 `worker_lock_ts TEXT DEFAULT NULL` 字段
- [x] 1.3 在 `backend/app/database.py` 中添加 migration：新建 `bet_order_platform_records` 表（id, issue, key_code, amount, win_amount, raw_json, created_at），用于审计平台 Topbetlist 原始记录
- [x] 1.4 在 `backend/app/engine/settlement.py` 中更新 `VALID_TRANSITIONS` 字典，新增 `settling`、`pending_match`、`settle_timeout`、`settle_failed` 状态及其合法转换，包含终态覆盖路径（settle_timeout → settling, settle_failed → settling）
- [x] 1.5 更新 `TERMINAL_STATES` 集合，包含 `settle_timeout` 和 `settle_failed`
- [x] 1.6 在 `backend/app/engine/alert.py` 的 `ALERT_LEVEL_MAP` 中新增告警类型：`settlement_data_missing`(warning)、`settle_api_failed`(critical)、`settle_timeout`(warning)、`unsettled_orders`(warning)、`api_call_failed`(critical)、`settle_data_expired`(critical)、`topbetlist_coverage_warning`(warning)、`match_ambiguity`(warning)、`worker_lock_conflict`(critical)、`worker_lock_lost`(critical)
- [x] 1.7 在 `backend/app/models/db_ops.py` 的 `bet_order_update_status` 中将 `match_source` 和 `pending_match_count` 加入 `allowed_extra` 集合
- [x] 1.8 编写状态机属性测试（Property 1）
  - [x] 1.8.1 PBT: 对于所有合法 (current, target) 对，`_transition_status` 不抛异常
  - [x] 1.8.2 PBT: 对于所有非法 (current, target) 对，`_transition_status` 抛出 `IllegalStateTransition`

## Task 2: SettlementProcessor 结算路径分发与幂等性

- [x] 2.1 重写 `settle()` 方法：新增 `adapter` 和 `is_recovery` 参数，查询待结算订单（`_get_settleable_orders` 双模式），按 `simulation` 字段分为真实组和模拟组。DoD：settle() 接受 adapter 和 is_recovery 参数；正常模式仅查 bet_success/pending_match；恢复模式额外查 settle_timeout/settle_failed
- [x] 2.2 实现 `_get_settleable_orders(issue, include_recoverable=False)` 方法。DoD：include_recoverable=False 时返回 bet_success/pending_match 订单；include_recoverable=True 时额外返回 settle_timeout/settle_failed 订单；已在不可覆盖终态（settled/bet_failed/reconcile_error）的订单不返回
- [x] 2.3 实现执行顺序：先模拟后真实，两组在独立事务中完成
- [x] 2.4 实现 `_atomic_transition(order_id, from_status, to_status, **extra)` 方法：CAS 语义 WHERE status=current_status，返回 bool（rowcount > 0）。单个订单的状态更新、pnl 写入、余额变更（模拟模式）在同一数据库事务中完成（R9-AC2）。DoD：事务内任一步骤失败时全部回滚
- [x] 2.5 实现 `_atomic_transition_with_priority(order_id, from_status, to_status, source_priority, **extra)` 方法：非终态→终态直接执行；终态覆盖时检查 source_priority < current_priority。DoD：priority 1=platform > 2=local > 3=timeout/failed
- [x] 2.6 编写结算路径分发属性测试（Property 2）
  - [x] 2.6.1 PBT: 验证 simulation=0 进入真实路径，simulation=1 进入模拟路径，无遗漏无重复
- [x] 2.7 编写终态覆盖优先级单元测试（Property 10 / P10）
  - [x] 2.7.1 测试：settle_timeout 订单被 platform 来源覆盖为 settled
  - [x] 2.7.2 测试：settle_failed 订单被 platform 来源覆盖为 settled
  - [x] 2.7.3 测试：settled 订单不可被任何来源覆盖
  - [x] 2.7.4 测试：同优先级来源不可覆盖（local 不可覆盖 local）

## Task 3: 平台数据匹配算法与歧义检测

依赖：Task 1（状态机 + DB schema）、Task 2（_atomic_transition）

- [x] 3.1 实现 `_match_and_settle()` 方法：使用 (Installments, KeyCode, Amount) 匹配，按 bet_at + id 升序消耗，含歧义检测。DoD：匹配成功的订单状态 bet_success → settling → settled；未匹配的标记 pending_match
- [x] 3.2 实现歧义检测逻辑：同一 Match_Key 下本地订单数 >= 2 且平台 WinAmount 不全相同时，全部标记 pending_match + 发送 match_ambiguity 告警。DoD：告警 detail 含 issue/key_code/amount/local_count/platform_count/win_amounts
- [x] 3.3 实现 Topbetlist 覆盖检测：本地 bet_success 订单数 > 平台同期记录数时，发送 topbetlist_coverage_warning 告警并将未覆盖订单标记为 pending_match。DoD：告警 detail 含本地订单数和平台记录数
- [x] 3.4 实现未匹配订单标记 `pending_match` 逻辑。DoD：订单 status 从 bet_success 转为 pending_match
- [x] 3.5 实现 `pending_match_count` 累加和 `settle_timeout` 转换逻辑：连续 2 个正常结算周期未匹配（API 失败/补结算不计入）或 wall-clock 30 分钟超时。DoD：pending_match_count 仅在正常结算周期中递增；超时后 status 转为 settle_timeout + 发送 settle_timeout 告警
- [x] 3.6 实现 `_settle_order_from_platform()` 方法：写入 WinAmount 转换后的 pnl/is_win，设置 match_source="platform"。DoD：pnl = int(WinAmount * 100) - amount；is_win = 1 if WinAmount > 0 else 0
- [x] 3.7 实现平台记录落库：将 Topbetlist 返回的原始记录写入 `bet_order_platform_records` 表供审计。DoD：每条 Topbetlist 记录对应一行，含 raw_json
- [x] 3.8 编写匹配唯一性属性测试（Property 3）
  - [x] 3.8.1 PBT: 每条平台记录最多消耗一次，每条本地订单最多匹配一条平台记录
- [x] 3.9 编写歧义检测单元测试（P9）
  - [x] 3.9.1 测试：WinAmount 全同时正常结算（无告警）
  - [x] 3.9.2 测试：WinAmount 不同时触发 match_ambiguity 告警并全部标记 pending_match

## Task 4: 真实投注结算路径实现

依赖：Task 3（_match_and_settle）

- [x] 4.1 实现 `_settle_real()` 方法：调用 QueryResult 更新余额 → 调用 Topbetlist 获取平台记录 → _match_and_settle() 匹配。DoD：余额写入 int(accountLimit * 100)；匹配成功订单 match_source="platform"
- [x] 4.2 实现 `_retry_api()` 通用重试方法：固定间隔 5 秒，最多 3 次
- [x] 4.3 实现 `_update_account_balance_absolute()` 方法：直接写入平台余额（int(accountLimit * 100)），accountLimit 为负值或非数值时不更新并记录日志
- [x] 4.4 实现 Topbetlist 3 次重试全部失败时：将该期所有 simulation=0 订单标记为 settle_failed + 发送 settle_api_failed 告警
- [x] 4.5 编写余额更新属性测试（Property 5）
  - [x] 4.5.1 PBT: 对于任意 accountLimit 浮点值 v >= 0，写入 balance == int(v * 100)

## Task 5: 模拟投注结算路径更新

- [x] 5.1 将现有 `_calculate_result` + `_settle_order` 逻辑提取为 `_settle_simulated()` 方法
- [x] 5.2 结算完成后设置 `match_source="local"`
- [x] 5.3 编写模拟结算 PnL 属性测试（Property 4）
  - [x] 5.3.1 PBT: is_win=1 时 pnl == amount * odds // 10000 - amount；is_win=0 时 pnl == -amount；is_win=-1 时 pnl == 0

## Task 6: 开奖结果持久化（R7）

- [x] 6.1 实现 `_save_lottery_result()` 方法：INSERT OR IGNORE 写入 lottery_results 表（issue UNIQUE 约束）。DoD：重复 issue 不报错不覆盖
- [x] 6.2 确保 settle() 调用前完成开奖结果持久化（在 Worker 主循环中 settle 前调用）
- [x] 6.3 编写开奖结果持久化单元测试
  - [x] 6.3.1 测试：正常写入 (issue, open_result, sum_value)
  - [x] 6.3.2 测试：重复 issue INSERT OR IGNORE 不报错

## Task 7: Worker 主循环重写

依赖：Task 2（settle 新签名）、Task 6（开奖结果持久化）

- [x] 7.1 重写 `_main_loop()` 为倒计时驱动模式：fetch_install → bet_phase → sleep(OpenTimeStamp，<=0 时跳过) → sleep(settlement_wait_seconds) → fetch_settlement_data → save_lottery_result → settle → reconcile
- [x] 7.2 实现 `_fetch_install_with_retry()` 方法：网络异常时按 5s → 10s → 30s 重试，最多 3 次，全部失败发 api_call_failed 告警 + 等待 60s。DoD：重试间隔递增；3 次失败后发告警并 sleep(60)
- [x] 7.3 实现 `_fetch_settlement_data()` 方法：验证 PreLotteryResult 有效性，最多重试 6 次（间隔 5s）。全部失败时：real 订单标记 settle_failed + sim 订单用 check_win 降级结算 + 发送 settlement_data_missing 告警。DoD：real 订单 status=settle_failed；sim 订单正常结算（match_source=local）
- [x] 7.4 实现全新启动检测（AC1.5）：Worker 启动时查询该账号的 bet_orders 记录，若无记录则调用 GetCurrentInstall 获取当前期号记录为 last_issue，从下一期开始正常循环（跳过当前期结算）；若有当前期号记录则按补结算流程处理。DoD：全新启动时不触发结算，仅记录 last_issue
- [x] 7.5 实现 `_recover_unsettled_orders()` 方法：重启时扫描 status IN (bet_success, pending_match, settle_timeout, settle_failed) 的订单，按 DISTINCT issue 分组，调用 Lotteryresult(count=50) 获取历史开奖。有结果则 settle(is_recovery=True)；无结果则 real+sim 订单均标记 settle_failed + 发送 settle_data_expired 告警。DoD：所有未结算订单被处理或标记终态
- [x] 7.6 新增 `settlement_wait_seconds` 构造参数（默认 30，范围 10-120）。DoD：值被 clamp 到 [10, 120] 范围
- [x] 7.7 编写 Worker 主循环单元测试
  - [x] 7.7.1 测试：正常倒计时驱动流程（sleep → fetch → settle → reconcile）
  - [x] 7.7.2 测试：OpenTimeStamp <= 0 时跳过休眠直接进入结算等待
  - [x] 7.7.3 测试：PreLotteryResult 重试 6 次后 real 订单 settle_failed + sim 降级
  - [x] 7.7.4 测试：补结算流程（含 settle_timeout/settle_failed 恢复）
  - [x] 7.7.5 测试：补结算历史缺失时 settle_data_expired 告警
  - [x] 7.7.6 测试：全新启动时记录 last_issue 不触发结算（AC1.5）

## Task 8: Worker 跨进程互斥锁

- [x] 8.1 实现 `_acquire_lock()` 方法：生成 UUID4 lock_token，CAS 写入 gambling_accounts（使用 datetime('now')，TTL 5 分钟）。DoD：无锁或锁超时时成功返回 True；已有活跃锁时返回 False
- [x] 8.2 实现 `_renew_lock()` 方法：每 60s 续约（WHERE worker_lock_token=self._lock_token），rowcount=0 时设置 running=False + 发送 worker_lock_lost 告警。DoD：续约成功返回 True；失锁时 Worker 停止
- [x] 8.3 实现 `_release_lock()` 方法：停止时按 token 释放锁（WHERE worker_lock_token=self._lock_token）
- [x] 8.4 在 `_main_loop()` 中集成锁续约（每次循环迭代开始时调用 _renew_lock）
- [x] 8.5 在 `start()` 中调用 `_acquire_lock()`，失败时拒绝启动 + 发送 worker_lock_conflict 告警
- [x] 8.6 编写跨进程互斥单元测试
  - [x] 8.6.1 测试：正常抢锁/续约/释放流程
  - [x] 8.6.2 测试：锁超时后新 Worker 可抢锁
  - [x] 8.6.3 测试：续约失败后 Worker 停止（running=False）
  - [x] 8.6.4 测试：A 持锁卡顿 > TTL → B 抢锁 → A 续约失败停止

## Task 9: Reconciler 更新

- [x] 9.1 重写 `reconcile()` 方法：按 simulation 分发到真实/模拟对账路径
- [x] 9.2 实现 `_reconcile_real()` 方法：验证当前结算期号下所有 simulation=0 订单在终态，否则发送 unsettled_orders 告警（detail 含未结算订单数量/期号/状态列表）。DoD：不写入 reconcile_error 状态
- [x] 9.3 保留 `_reconcile_simulated()` 方法：现有余额比较逻辑，容差 TOLERANCE_SINGLE=100
- [x] 9.4 编写终态验证属性测试（Property 6）
  - [x] 9.4.1 PBT: 全部终态时无告警，存在非终态时触发 unsettled_orders 告警

## Task 10: 结算幂等性与正确性属性测试

- [x] 10.1 编写结算幂等性属性测试（Property 8）
  - [x] 10.1.1 PBT: 重复调用 settle() 后订单状态/pnl/balance 不变，strategy pnl 不重复累加
- [x] 10.2 编写 match_source 属性测试（Property 7）
  - [x] 10.2.1 PBT: simulation=0 已结算订单 match_source ∈ {"platform", "local"}，simulation=1 已结算订单 match_source="local"
- [x] 10.3 编写 pending_match 超时终态单元测试（P11）
  - [x] 10.3.1 测试：连续 2 个正常结算周期未匹配 → settle_timeout
  - [x] 10.3.2 测试：wall-clock 30 分钟超时 → settle_timeout
- [x] 10.4 编写 settle_timeout/settle_failed 互斥属性测试（P12）
  - [x] 10.4.1 PBT: 任意订单路径验证 settle_timeout 和 settle_failed 互斥（同一订单不会同时经历两者）
- [x] 10.5 编写优先级覆盖路径可达性单元测试（P13）
  - [x] 10.5.1 测试：补结算成功将 settle_timeout 订单覆盖为 settled

## Task 11: 集成测试

- [x] 11.1 编写端到端集成测试：模拟完整的倒计时 → 结算 → 对账流程（mock adapter）。DoD：真实订单 match_source=platform，模拟订单 match_source=local，对账无告警
- [x] 11.2 编写混合模式测试：同一期号下同时存在真实和模拟订单的结算。DoD：模拟先结算，真实后结算，各自路径正确
- [x] 11.3 编写补结算测试：Worker 重启后正确处理未结算订单（含 settle_timeout/settle_failed 恢复）。DoD：settle_timeout 订单被恢复为 settled
- [x] 11.4 编写锁竞态故障注入测试：A 持锁卡顿 > TTL → B 抢锁 → A 恢复后不产生副作用。DoD：A 的 running=False，B 正常运行
