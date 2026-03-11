# Requirements Document

## Introduction

重新设计投注引擎的结算架构，将当前基于"轮询检测期号变化"的触发机制改为"倒计时驱动"模式。核心目标：

1. 结算触发时机确定性更强（倒计时归零 + 固定等待 → 主动拉取新期号和开奖结果）
2. 真实投注模式下，余额和投注记录直接从平台获取，消除对账 mismatch
3. 模拟投注模式下，保留本地自计算结算逻辑
4. 解决当前 poller 首次启动时 `last_issue=""` 导致跳过结算的问题

## Glossary

- **Worker**: AccountWorker，每个投注账号对应一个 Worker 实例，负责主循环调度
- **Settlement_Processor**: 结算处理器，负责根据开奖结果更新 bet_orders 的 is_win/pnl/status
- **Countdown_Timer**: 倒计时计时器，基于 OpenTimeStamp 驱动结算触发
- **Platform_Adapter**: 平台适配器接口，封装与投注平台的 HTTP API 交互
- **Install_Info**: 期号信息数据结构，包含当前期号、状态、倒计时、上期开奖结果等
- **Bet_Order**: 投注订单记录，存储在 bet_orders 表中
- **Reconciler**: 对账器，比较本地数据与平台数据的一致性
- **OpenTimeStamp**: 平台返回的距开奖剩余秒数
- **CloseTimeStamp**: 平台返回的距封盘剩余秒数
- **Topbetlist**: 平台 API，返回最近投注记录（含结算结果），每条记录包含 Installments/KeyCode/Amount/WinAmount 等字段
- **QueryResult**: 平台 API，返回账户余额（accountLimit 字段）
- **GetCurrentInstall**: 平台 API，返回当前期号、倒计时、上期开奖结果
- **Settlement_Wait_Seconds**: 开奖倒计时归零后额外等待的秒数，默认 30 秒，可配置（10-120 秒）
- **Real_Mode**: 真实投注模式，投注通过平台 API 提交，simulation=0
- **Simulation_Mode**: 模拟投注模式，投注仅在本地记录，simulation=1
- **Settle_Retry_Max**: 结算相关 API 调用最大重试次数，固定 3 次
- **Settle_Retry_Interval**: 结算重试间隔，固定 5 秒，无抖动
- **Match_Key**: 平台记录与本地订单的匹配键，由 (期号, KeyCode, Amount) 三元组组成；同一三元组存在多条记录时，按本地订单的 bet_at 升序与平台记录逐一配对消耗
- **Settle_Timeout_Cycles**: 未匹配订单等待的最大结算周期数，固定 2 次。一个"结算周期"定义为：一次倒计时归零后触发的完整结算流程（含 Topbetlist 拉取）
- **accountLimit**: 平台 QueryResult API 返回的账户余额字段，单位为元（浮点数）。转换规则：`int(accountLimit * 100)`，使用 Python 默认 `int()` 截断（floor toward zero）。若 accountLimit 为负值或非数值，视为 API 异常，不更新余额并记录日志
- **odds**: 赔率，以万分比整数存储（例如 1.9834 存为 19834）。含义为"返还倍数"：中奖时返还金额 = amount × odds // 10000（整数除法，向下取整），净利润 pnl = 返还金额 - amount
- **WinAmount**: 平台 Topbetlist 返回的中奖金额字段，单位为元（浮点数）。转换规则：`int(float(WinAmount) * 100)`，使用 `int()` 截断。WinAmount=0 表示未中奖，WinAmount>0 表示中奖（含本金返还）
- **TERMINAL_STATES**: 订单终态集合，包含 {settled, settle_timeout, settle_failed, bet_failed, reconcile_error}，处于终态的订单不再参与结算或匹配。settle_timeout 与 settle_failed 的区分：settle_timeout 表示"平台数据可达但未找到匹配记录"（匹配超时），settle_failed 表示"平台 API 不可达或开奖数据缺失"（系统故障）。两者互斥，同一订单只会进入其中一个终态
- **Max_Bets_Per_Issue**: 单个账号每期最大投注笔数上限，当前业务约束为 5-8 笔，Topbetlist(top=15) 足以覆盖最近 1-2 期的全部投注记录。若实际超出，运行时告警 topbetlist_coverage_warning 并降级为 pending_match

## Requirements

### Requirement 1: 倒计时驱动的主循环

**User Story:** As a 运维人员, I want Worker 主循环基于倒计时驱动而非轮询检测期号变化, so that 结算触发时机具有确定性且不会遗漏。

#### Acceptance Criteria

1. WHEN Worker 启动并获取到当前 Install_Info, THE Countdown_Timer SHALL 读取 OpenTimeStamp 值：若 OpenTimeStamp > 0 则执行 asyncio.sleep(OpenTimeStamp) 休眠至开奖倒计时归零；若 OpenTimeStamp <= 0（已过期或接口延迟）则跳过休眠直接进入结算等待阶段
2. WHEN 开奖倒计时归零, THE Worker SHALL 执行 asyncio.sleep(Settlement_Wait_Seconds) 额外等待后调用 GetCurrentInstall 获取新期号和上期开奖结果
3. WHEN GetCurrentInstall 返回的 PreLotteryResult 为空字符串或与上一期相同, THE Worker SHALL 以 5 秒间隔重新调用 GetCurrentInstall，最多重试 6 次（共 30 秒），直到获取到有效的上期开奖结果
4. IF 重试 6 次后 PreLotteryResult 仍无效, THEN THE Worker SHALL 将该期所有 status='bet_success' 且 simulation=0 的订单标记为 status='settle_failed'，模拟订单（simulation=1）使用本地 check_win 降级结算；然后通过 AlertService 发送 alert_type="settlement_data_missing" 告警（detail 包含期号和受影响订单数），继续下一期循环
5. WHEN Worker 全新启动且数据库中无该账号的历史 bet_orders 记录, THE Worker SHALL 调用 GetCurrentInstall 获取当前期号，记录为 last_issue，从下一期开始正常结算循环。若数据库中已有当前期号的 bet_orders 记录（说明非全新启动），则按 AC1.6 补结算流程处理
6. WHEN Worker 重启且数据库中存在 status='bet_success' 或 status='pending_match' 的未结算订单, THE Worker SHALL 按以下流程执行补结算：(a) 查询所有 DISTINCT issue 列表；(b) 对每个 issue，调用平台 Lotteryresult API（count=50）获取历史开奖结果，50 期覆盖约 3 小时（每期约 3.5 分钟）；(c) 若该 issue 的开奖结果存在，则调用 settler.settle() 执行结算（真实/模拟路径按正常逻辑分发）；(d) 若该 issue 的开奖结果不存在（停机超过 3 小时导致平台历史记录已过期），则将该 issue 下所有 simulation=0 的订单标记为 settle_failed 并发送 alert_type="settle_data_expired" 告警（detail 包含 issue 和订单数），simulation=1 的订单同样标记为 settle_failed（无开奖结果无法本地计算）；(e) 补结算完成后进入正常倒计时循环。补结算期间不接受新的投注信号
7. IF GetCurrentInstall 调用因网络异常或 HTTP 错误失败, THEN THE Worker SHALL 以固定间隔（5s → 10s → 30s）重试，最多 3 次，全部失败后通过 AlertService 发送 alert_type="api_call_failed" 告警并等待 60 秒后重新进入循环

### Requirement 2: 真实投注模式结算

**User Story:** As a 运维人员, I want 真实投注模式下结算数据直接来自平台, so that 本地记录与平台完全一致，消除对账差异。

#### Acceptance Criteria

1. WHEN 真实投注模式触发结算, THE Settlement_Processor SHALL 调用 QueryResult API 获取 accountLimit 字段值，乘以 100 转为整数分，写入 gambling_accounts.balance
2. WHEN 真实投注模式触发结算, THE Settlement_Processor SHALL 调用 Topbetlist API（top=15）获取平台最近投注记录。由于单账号每期投注上限为 5-8 笔（见 Max_Bets_Per_Issue），top=15 足以覆盖最近 1-2 期全部记录。若结算时发现本地 bet_success 订单数超过 Topbetlist 返回的同期记录数，SHALL 发送 alert_type="topbetlist_coverage_warning" 告警并将未覆盖订单标记为 pending_match
3. WHEN Topbetlist 返回结算数据, THE Settlement_Processor SHALL 按 Requirement 5 AC5.1 定义的匹配算法（三元组 + bet_at 排序配对）匹配平台记录与本地 bet_orders，将平台的 WinAmount（单位元）乘以 100 转为整数分写入本地订单的 pnl 字段（pnl = WinAmount_fen - amount），根据 WinAmount > 0 设置 is_win=1，WinAmount = 0 设置 is_win=0
4. IF Topbetlist 调用因网络异常或 HTTP 错误失败, THEN THE Settlement_Processor SHALL 以 Settle_Retry_Interval（5 秒）间隔重试，最多 Settle_Retry_Max（3）次
5. IF 3 次重试全部失败, THEN THE Settlement_Processor SHALL 将该期所有 status='bet_success' 且 simulation=0 的订单标记为 status='settle_failed'，并通过 AlertService 发送 alert_type="settle_api_failed" 告警

### Requirement 3: 模拟投注模式结算

**User Story:** As a 运维人员, I want 模拟投注模式下使用本地赔率和 check_win 自计算结算, so that 无需平台记录即可完成结算。

#### Acceptance Criteria

1. WHEN 模拟投注模式触发结算, THE Settlement_Processor SHALL 使用 bet_orders 表中存储的 odds 字段和 check_win 函数，根据开奖结果计算 is_win 和 pnl
2. WHEN 模拟投注结算计算出 is_win=1, THE Settlement_Processor SHALL 将 gambling_accounts.balance 增加 (amount * odds // 10000)，即返还本金加利润
3. WHEN 模拟投注结算计算出 is_win=0, THE Settlement_Processor SHALL 不修改 gambling_accounts.balance（下注时已扣减）
4. WHEN 模拟投注结算计算出 is_win=-1（退款）, THE Settlement_Processor SHALL 将 gambling_accounts.balance 增加 amount（返还本金）
5. WHEN 模拟投注结算完成, THE Settlement_Processor SHALL 累加 pnl 到对应 strategy 的 daily_pnl 和 total_pnl 字段

### Requirement 4: 结算路径分发

**User Story:** As a 开发者, I want 结算流程根据订单的 simulation 标志自动分发到对应路径, so that 同一账号下真实和模拟订单各自走正确的结算逻辑。

#### Acceptance Criteria

1. WHEN 结算流程启动, THE Settlement_Processor SHALL 查询 bet_orders 表中 issue=当前结算期号 且 status='bet_success' 的所有订单，按 simulation 字段分为两组：simulation=0 为真实订单组，simulation=1 为模拟订单组
2. WHEN 真实订单组非空, THE Settlement_Processor SHALL 调用真实投注结算路径处理该组订单
3. WHEN 模拟订单组非空, THE Settlement_Processor SHALL 调用模拟投注结算路径处理该组订单
4. WHEN 两组均非空, THE Settlement_Processor SHALL 先执行模拟订单结算，再执行真实订单结算，两组之间的数据库写操作在独立事务中完成

### Requirement 5: 平台数据匹配

**User Story:** As a 开发者, I want 真实投注结算时能准确匹配平台记录与本地订单, so that 每笔订单的结算结果来源可追溯。

#### Acceptance Criteria

1. WHEN 匹配平台投注记录与本地订单, THE Settlement_Processor SHALL 使用 (Installments, KeyCode, Amount) 作为初步匹配键，对于同一匹配键存在多条记录的情况，按下注时间（bet_at）升序排列后逐一配对消耗。若 bet_at 相同，则按订单 id 升序作为次级排序，确保匹配结果确定性
2. WHEN 本地订单在当前 Topbetlist 返回中无匹配, THE Settlement_Processor SHALL 将该订单标记为 pending_match 状态，在下一次结算周期再次尝试匹配
3. WHEN 一条订单连续 Settle_Timeout_Cycles（2）个结算周期仍未匹配, THE Settlement_Processor SHALL 将该订单标记为 status='settle_timeout'，并通过 AlertService 发送 alert_type="settle_timeout" 告警，告警 detail 包含 order_id、issue、key_code、amount。周期计数规则：按订单粒度计数（每条订单独立维护 pending_match_count 字段）；仅在正常倒计时结算流程中成功拉取 Topbetlist 且该订单未匹配时 pending_match_count+1；API 失败、服务停机、补结算阶段不计入周期；订单被成功匹配后计数重置为 0。此外，若订单在 pending_match 状态停留超过 30 分钟（wall-clock），无论 pending_match_count 值如何，SHALL 直接标记为 settle_timeout 并发送告警（防止 Topbetlist 持续拉取失败导致订单永不终态）
4. THE Settlement_Processor SHALL 在 bet_orders 表中记录 match_source 字段，值为 "platform" 表示来自 Topbetlist，值为 "local" 表示本地自计算

### Requirement 6: 真实投注模式对账简化

**User Story:** As a 运维人员, I want 真实投注模式下对账逻辑简化, so that 因为余额直接来自平台，对账仅需验证数据完整性。

#### Acceptance Criteria

1. WHEN 真实投注模式对账, THE Reconciler SHALL 查询该期号（当前结算期号）下所有 simulation=0 的订单，验证是否全部处于终态（settled / settle_timeout / settle_failed / bet_failed）。对账范围仅限当前结算期号，不扫描历史期号
2. WHEN 真实投注模式对账发现存在 status='bet_success' 或 status='pending_match' 的订单, THE Reconciler SHALL 通过 AlertService 发送 alert_type="unsettled_orders" 告警，告警 detail 包含未结算订单数量和期号。reconcile_error 状态仅由模拟模式对账余额不匹配时写入（现有逻辑），真实模式对账不写入 reconcile_error
3. WHEN 模拟投注模式对账, THE Reconciler SHALL 执行现有的余额比较逻辑（本地 balance vs 结算后预期值），容差阈值保持 TOLERANCE_SINGLE=100（1 元）。若差异超过容差，将相关订单标记为 reconcile_error 并发送告警

### Requirement 7: 开奖结果持久化

**User Story:** As a 开发者, I want 每期开奖结果被持久化到 lottery_results 表, so that 历史数据可供策略分析和审计使用。

#### Acceptance Criteria

1. WHEN GetCurrentInstall 返回有效的 PreLotteryResult（非空字符串）, THE Worker SHALL 执行 INSERT OR IGNORE 将 (issue, open_result, sum_value) 写入 lottery_results 表。lottery_results 表的 issue 字段具有 UNIQUE 约束，确保 INSERT OR IGNORE 语义正确
2. THE Worker SHALL 在调用 Settlement_Processor.settle() 之前完成开奖结果的持久化写入

### Requirement 8: 状态机完整性

**User Story:** As a 开发者, I want 订单状态转换遵循明确的状态机规则, so that 不会出现非法状态转换。

#### Acceptance Criteria

1. THE Settlement_Processor SHALL 执行以下正向状态转换：bet_success → settling → settled。此外，betting → bet_failed 由 BetExecutor 负责（不在 Settlement_Processor 范围内，但 bet_failed 属于 TERMINAL_STATES）
2. THE Settlement_Processor SHALL 允许以下异常状态转换：bet_success → settle_timeout, bet_success → settle_failed, settling → settle_failed, settling → settle_timeout
3. THE Settlement_Processor SHALL 允许以下匹配等待转换：bet_success → pending_match, pending_match → settling, pending_match → settle_timeout
4. IF 代码尝试不在上述列表中的状态转换, THEN THE Settlement_Processor SHALL 抛出 IllegalStateTransition 异常，异常消息包含 current_status 和 target_status 的值

### Requirement 9: 结算幂等性

**User Story:** As a 开发者, I want 结算操作具有幂等性, so that 重试、补结算、崩溃恢复不会导致重复记账。

#### Acceptance Criteria

1. THE Settlement_Processor SHALL 在更新订单状态前检查当前状态：若订单已处于 TERMINAL_STATES（settled / settle_timeout / settle_failed / bet_failed / reconcile_error），则跳过该订单不做任何写操作
2. THE Settlement_Processor SHALL 将单个订单的状态更新、pnl 写入、余额变更（模拟模式）在同一数据库事务中完成，确保原子性
3. WHEN settle() 被对同一 issue 重复调用, THE Settlement_Processor SHALL 仅处理 status='bet_success' 或 status='pending_match' 的订单，已结算订单不受影响
4. THE Settlement_Processor SHALL 在 strategy pnl 累加前验证该订单的 pnl 尚未被计入（通过检查订单状态是否刚从 settling 转为 settled），防止重复累加

## Review
| 日期 | 版本 | 结论 | 关键 P0 |
|------|------|------|---------|
| - | v1 | FAIL | 5 个 P0：匹配键冲突、状态机不完整、开奖结果门控缺失、首次启动/重启未区分、AC 冲突 |
| - | v2 | FAIL | 4 个 P0：AC2.3/AC5.1 匹配规则冲突、Topbetlist 覆盖不足、跳过本期订单状态缺失、补结算范围不完整 |
| - | v3 | FAIL | 2 个 P0：金额精度舍入规则未定义、结算幂等性缺失 |
| - | v4 | FAIL | 2 个 P0：Lotteryresult count=50 停机兜底缺失、top=15 无运行时护栏；5 个 P1 |
| - | v5 | FAIL | 0 P0, 2 P1：pending_match 长期不终态风险、settle_timeout/settle_failed 边界不明 |
| - | v6 | PASS | 0 P0, 0 P1。codex-gpt53 审核通过 |
