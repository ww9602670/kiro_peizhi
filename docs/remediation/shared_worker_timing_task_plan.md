# 共享数据、worker 时机与结算链路第一阶段任务清单

生成时间：2026-05-03

依据：

- `docs/remediation/phase1_current_chain_report.md`
- `docs/remediation/shared_worker_timing_design.md`

执行约束：本文件仅拆分任务，本轮禁止修改业务代码、禁止部署、禁止真实下注。

## 总体执行方式

后续进入开发阶段时，建议使用 `gpt5.3codex` agent 分工执行。每个 agent 必须只修改自己任务列出的文件和函数；如发现必须跨任务修改，停止并上报，不得自行扩大范围。

执行顺序建议：

1. 第一批：任务 T01、T06、T08 可并行。
2. 第二批：T02 依赖 T01，T03 依赖 T01/T02。
3. 第三批：T04、T05 均涉及 `worker.py`，必须串行，不能并行改同一文件。
4. 第四批：T07、T09 可在核心后端状态确定后执行。
5. 第五批：T10 统一补测试。
6. 最终门禁：T11 由总控复审、测试、git、部署和服务器自测。

所有 agent 自检通过后，只能汇报给总控。总控负责最终复审、测试、git、部署。部署前必须检查服务器是否有策略正在运行；如有运行策略，直接阻塞并上报，暂停更新。

## T01 共享快照字段契约与状态枚举

任务编号：T01

修改目标：

补齐共享快照字段、后端 schema、前端类型，使共享数据状态可以表达 `shared_ok`、`shared_error`、`shared_stale`、`market_closed`、`draw_pending`、`draw_wait_retry`。

涉及文件：

- `backend/app/database.py`
- `backend/app/models/db_ops.py`
- `backend/app/schemas/lottery.py`
- `frontend/src/types/api/lottery.ts`

涉及函数：

- `shared_market_snapshot_upsert`
- `shared_market_snapshot_get_latest`
- `CurrentInstallResponse`
- `normalizeMarketDataState`
- `normalizeCurrentInstall`

修改说明：

1. 扩展共享快照字段契约。
2. 增加 `market_data_state` 和 `draw_state`。
3. 增加 `next_normal_refresh_at`、`next_draw_retry_at`、`snapshot_version`、`message_code`、`message_text`。
4. 后端响应和前端类型必须一致。
5. 旧字段如果仍被使用，只能作为迁移读取来源，不能继续主导新逻辑。

禁止事项：

1. 不删除历史投注、结算、盈亏数据。
2. 不改变策略表、投注记录表业务含义。
3. 不引入无关状态。
4. 不把 `draw_pending` 当失败状态。

验收方式：

1. 后端 schema 能返回新字段。
2. 前端类型能识别新状态。
3. 旧快照数据不会导致接口崩溃。
4. 单元测试覆盖状态归一化。

回滚风险：

中等。涉及表结构和接口字段，回滚时需保留新增字段不影响旧代码。

是否需要前端联动：

需要。

是否需要后端测试：

需要。

## T02 共享采集器开奖专项刷新状态机

任务编号：T02

修改目标：

把共享采集器从固定 25 秒轮询升级为普通采集 + 开奖专项刷新状态机。

涉及文件：

- `backend/app/engine/shared_market_runtime.py`
- `backend/tests/test_shared_market_runtime.py`

涉及函数：

- `SharedMarketRuntime._collector_loop`
- `SharedMarketRuntime._ensure_collector`
- `SharedMarketContracts.snapshot_upsert`
- 需要新增的内部状态机辅助函数

修改说明：

1. 普通阶段保持 25 秒采集。
2. 开奖倒计时归零后，按 10/10/10/5/3/1 专项刷新。
3. 第 6 次仍未获取新数据，设置 `draw_wait_retry`，之后每 10 秒继续。
4. 专项刷新期间暂停重叠的普通 25 秒采集。
5. 检测到停盘时设置 `market_closed`。
6. 停盘后低频检测恢复，恢复后必须拿到有效期号和倒计时才能回 `shared_ok`。

禁止事项：

1. 不让多个同共享组采集请求并发。
2. 不让管理员手动检测打断开奖专项刷新。
3. 不把专项刷新失败立即标记共享账号异常。
4. 不在该任务中修改 worker 下注逻辑。

验收方式：

1. 单测验证普通 25 秒采集。
2. 单测验证开奖专项刷新节奏。
3. 单测验证第 6 次后进入 `draw_wait_retry`。
4. 单测验证停盘停止本期专项刷新。
5. 单测验证成功拿到新数据后重置 25 秒周期。

回滚风险：

中高。影响共享数据源主链路，必须有单测保护。

是否需要前端联动：

间接需要。前端要消费新状态。

是否需要后端测试：

需要。

## T03 worker 共享快照读取与本地回退边界

任务编号：T03

修改目标：

确保 worker 正常只读共享快照；共享异常时才 80 秒低频本地回退；本地回退结果不写共享池。

涉及文件：

- `backend/app/engine/shared_market_runtime.py`
- `backend/app/engine/poller.py`
- `backend/app/api/lottery.py`
- `backend/tests/test_poller.py`
- `backend/tests/test_shared_market_runtime.py`

涉及函数：

- `SharedMarketRuntime.resolve_install`
- `SharedMarketRuntime._fallback_local`
- `IssuePoller._fetch_install`
- `_resolve_account_current_install`
- `_fetch_local_account_install`

修改说明：

1. `draw_pending`、`draw_wait_retry` 不触发本地回退。
2. `shared_error`、`shared_stale` 才允许本地回退。
3. 本地回退频率限制为 80 秒一次。
4. 回退节流按 worker 或第三方账号，不按策略。
5. `_fallback_local()` 不再写共享快照。
6. `/lottery/current-install` 的操作者本地采集结果不得写共享池。

禁止事项：

1. 不改共享采集器专项刷新状态机。
2. 不改 worker 下注执行。
3. 不让前端 current-install 接口污染共享快照。
4. 不把共享等待状态升级为失败告警。

验收方式：

1. 单测验证缺失、过期、异常状态下的回退边界。
2. 单测验证 `draw_pending` 不回退。
3. 单测验证本地回退不调用 `snapshot_upsert`。
4. 单测验证前端接口本地采集不写共享池。

回滚风险：

中等。会改变共享异常时的数据来源，需要确保前端有明确提示。

是否需要前端联动：

需要展示 `SHARED-002`，但本任务只做后端边界。

是否需要后端测试：

需要。

## T04 worker 下注前快照新鲜度保护

任务编号：T04

修改目标：

移除 worker 下注前第三方平台倒计时重校验，改成本地共享快照新鲜度和期号窗口校验。

涉及文件：

- `backend/app/engine/worker.py`
- `backend/app/utils/logger.py`
- `backend/tests/test_worker.py`

涉及函数：

- `AccountWorker._revalidate_group_window`
- `AccountWorker._run_due_strategy_windows`
- `AccountWorker._should_bet`
- `log_countdown_validation`

修改说明：

1. `_revalidate_group_window()` 不再调用 `adapter.get_current_install()`。
2. 校验当前快照状态、期号、封盘截止时间、快照年龄。
3. 快照异常时跳过本期下注并记录中文日志编号。
4. 下注失败后才进入账号心跳、盘口状态、重登链路。

禁止事项：

1. 不改策略选号和下注金额。
2. 不改马丁和平注逻辑。
3. 不恢复平台倒计时强校验。
4. 不吞掉真实下注失败。

验收方式：

1. 单测断言下注前不调用 `adapter.get_current_install()`。
2. 单测覆盖快照过期跳过下注。
3. 单测覆盖期号变化跳过旧期。
4. 单测覆盖停盘不生成新下注。

回滚风险：

中高。直接影响真实下注时机，必须通过 worker 单测和仿真测试。

是否需要前端联动：

只需要提示展示，核心在后端。

是否需要后端测试：

需要。

## T05 结算独立分支与幂等补偿

任务编号：T05

修改目标：

把结算从 worker 主循环拆出，按账号维度串行聚合结算，确保结算延迟不阻塞下一期下注。

涉及文件：

- `backend/app/engine/worker.py`
- `backend/app/engine/settlement.py`
- `backend/app/models/db_ops.py`
- `backend/tests/test_worker.py`
- `backend/tests/test_settlement.py`

涉及函数：

- `AccountWorker._main_loop`
- `AccountWorker._fetch_settlement_data`
- `AccountWorker._recover_unsettled_orders`
- `SettlementProcessor.settle`
- 需要新增的结算分支或补偿任务辅助函数

修改说明：

1. 每期下注后登记待结算期号。
2. 开奖归零后 20 秒首取结算。
3. 后续按 10/5/8/之后每 10 秒退避。
4. 最长等待 10 分钟。
5. 10 分钟内保持 `settlement_pending`。
6. 同一第三方账号结算请求串行。
7. 多策略同账号结算聚合，不并发。
8. 重启后恢复未完成结算。

禁止事项：

1. 不提前标记 `settle_failed`。
2. 不让结算分支阻塞下一期下注。
3. 不重复累计盈亏。
4. 不改变已结算订单结果。

验收方式：

1. 单测验证结算不阻塞下一期下注。
2. 单测验证 20/10/5/8/10 退避。
3. 单测验证 10 分钟内保持待结算。
4. 单测验证幂等，重复返回不重复盈亏。
5. 单测验证服务重启恢复待结算。

回滚风险：

高。涉及订单状态和盈亏，必须严控修改范围。

是否需要前端联动：

需要展示待结算、结算中、结算失败。

是否需要后端测试：

需要。

## T06 策略生命周期与 verification_stale 解耦

任务编号：T06

修改目标：

策略创建、编辑、启动、重启恢复不再被 `verification_stale` 硬阻断。

涉及文件：

- `backend/app/api/strategies.py`
- `backend/app/engine/manager.py`
- `backend/tests/test_accounts.py`
- `backend/tests/test_strategy_startup.py`

涉及函数：

- `_validate_account_platform_gate_or_raise`
- `_validate_strategy_platform_type_for_account`
- `_transition_strategy`
- `_is_platform_verified_for_restore`
- `EngineManager.restore_workers_on_startup`

修改说明：

1. `verification_stale` 只作为提示或诊断字段。
2. 创建、编辑、启动策略不因 stale 阻断。
3. 重启恢复 running 策略不因 stale 跳过。
4. 保留账号不存在、账号禁用、共享检测账号、kill switch 等硬阻断。
5. 平台类型仍需可解析。

禁止事项：

1. 不绕过管理员策略授权。
2. 不允许无归属账号创建策略。
3. 不允许共享检测账号启动真实下注 worker。
4. 不删除验证记录。

验收方式：

1. 单测验证 stale 账号可创建策略。
2. 单测验证 stale 账号可启动策略并交给 worker 登录。
3. 单测验证服务重启恢复 stale 但 running 的策略。
4. 单测验证未授权策略仍被阻止。

回滚风险：

中等。可能影响策略启动门禁，但不改变下注算法。

是否需要前端联动：

可选。前端可保留提示但不能阻止操作。

是否需要后端测试：

需要。

## T07 账号被顶、会话异常与重登告警

任务编号：T07

修改目标：

补齐账号被顶和会话异常的错误归类、自动重登、退避和中文告警。

涉及文件：

- `backend/app/engine/session_runtime.py`
- `backend/app/engine/session.py`
- `backend/app/engine/adapters/base.py`
- `backend/app/engine/adapters/jnd.py`
- `backend/tests/test_session.py`

涉及函数：

- `AccountSessionRuntime.run_platform_call`
- `AccountSessionRuntime.recover`
- `SessionManager.recover_after_api_failure`
- `SessionManager._heartbeat_loop`
- `JndAdapter.get_current_install`

修改说明：

1. 保留 10 秒心跳和连续 3 次失败自动重连。
2. 对被顶、登录页、401、403、验证码、风控做分类。
3. 阻断类错误进入退避，不无限重登。
4. 输出 `SESSION-001`、`SESSION-002` 中文提示。
5. 结算分支也使用同一恢复链路。

禁止事项：

1. 不提高登录重试频率。
2. 不绕过验证码或风控阻断。
3. 不在普通下注前主动重登。
4. 不改第三方平台适配器的下注接口语义。

验收方式：

1. 单测验证 RemoteLoginRequired 后自动 recover 并重试一次。
2. 单测验证阻断错误进入 5 分钟退避。
3. 单测验证中文告警编号。
4. 单测验证结算调用使用 session runtime。

回滚风险：

中等。影响登录恢复，但已有 session runtime 基础。

是否需要前端联动：

需要展示中文告警。

是否需要后端测试：

需要。

## T08 前端倒计时、开奖状态和告警展示

任务编号：T08

修改目标：

前端开奖倒计时归零后不再固定 30 秒刷新，改为按后端状态和刷新时间执行。

涉及文件：

- `frontend/src/hooks/useLotteryCountdown.ts`
- `frontend/src/types/api/lottery.ts`
- `frontend/src/components/CountdownDisplay.tsx`
- `frontend/src/components/CountdownDisplay.test.tsx`
- `frontend/src/api/lottery.test.ts`

涉及函数：

- `CountdownStore.start`
- `CountdownStore.fetchNow`
- `normalizeMarketDataState`
- `normalizeCurrentInstall`
- `CountdownDisplay`

修改说明：

1. 开奖归零后 10 秒首刷。
2. `draw_pending` 时每 5 秒请求本系统后端。
3. `draw_wait_retry` 时按 `next_draw_retry_at` 或默认 10 秒刷新。
4. `market_closed` 显示当前处于停盘。
5. `SHARED-002` 用弹窗提示。
6. 封盘倒计时为 0 显示封盘中；两个倒计时都为 0 显示等待开奖。

禁止事项：

1. 不让前端直接请求第三方平台。
2. 不改 Web 端无关布局。
3. 不把过程状态显示成失败。
4. 不增加高频无节制轮询。

验收方式：

1. 前端单测验证 10 秒首刷。
2. 前端单测验证 `draw_wait_retry` 后 10 秒刷新。
3. 前端单测验证停盘文案。
4. 前端单测验证 `SHARED-002` 弹窗。

回滚风险：

中等。影响前端展示和轮询节奏。

是否需要前端联动：

需要。

是否需要后端测试：

不需要，但依赖后端字段。

## T09 中文告警、日志编号和去重

任务编号：T09

修改目标：

统一操作者提示为简短中文 + 日志编号，后台保留详细过程，避免告警刷屏。

涉及文件：

- `backend/app/engine/alert.py`
- `backend/app/engine/worker.py`
- `backend/app/engine/shared_market_runtime.py`
- `backend/app/engine/session_runtime.py`
- `frontend/src/api/alerts.ts`
- 告警展示相关前端组件

涉及函数：

- `AlertService.send`
- `SharedMarketRuntime._notify_admin_shared_error`
- worker 中所有 `alert_service.send`
- session runtime 告警调用点

修改说明：

1. 增加固定日志编号。
2. 操作者端只显示简短中文。
3. 后端日志保留原始异常和平台返回摘要。
4. 状态变化才告警一次。
5. `draw_pending`、`draw_wait_retry` 不生成失败告警。

禁止事项：

1. 不删除后台历史告警。
2. 不隐藏真实下注失败。
3. 不把英文或乱码展示给操作者。
4. 不让同一状态持续刷屏。

验收方式：

1. 单测验证告警编号。
2. 单测验证状态不变不重复发告警。
3. 前端测试验证中文展示。
4. 人工检查关键提示无英文乱码。

回滚风险：

低到中。主要影响展示和日志。

是否需要前端联动：

需要。

是否需要后端测试：

需要。

## T10 回归测试补齐

任务编号：T10

修改目标：

为第一阶段全部核心链路补齐后端、前端和端到端回归测试。

涉及文件：

- `backend/tests/test_shared_market_runtime.py`
- `backend/tests/test_poller.py`
- `backend/tests/test_worker.py`
- `backend/tests/test_session.py`
- `backend/tests/test_settlement.py`
- `backend/tests/test_strategy_startup.py`
- `frontend/src/components/CountdownDisplay.test.tsx`
- `frontend/src/api/lottery.test.ts`

涉及函数：

覆盖 T01 至 T09 涉及的新增或修改函数。

修改说明：

1. 补后端状态机测试。
2. 补 worker 不平台重校验测试。
3. 补本地回退不写共享池测试。
4. 补结算独立分支测试。
5. 补 verification stale 解耦测试。
6. 补前端倒计时刷新测试。

禁止事项：

1. 不用跳过测试掩盖失败。
2. 不删除原有测试断言。
3. 不把真实账号写入测试。
4. 不依赖外网第三方平台。

验收方式：

1. 后端目标测试通过。
2. 前端目标测试通过。
3. 测试覆盖所有新增状态。
4. 失败测试必须说明根因，不允许忽略。

回滚风险：

低。仅测试。

是否需要前端联动：

需要。

是否需要后端测试：

需要。

## T11 总控复审、部署门禁和服务器自测

任务编号：T11

修改目标：

所有 agent 完成并自检 pass 后，由总控执行复审、测试、git、部署和服务器自测。

涉及文件：

- 所有 T01 至 T10 修改文件。
- 部署脚本和服务器运行状态检查命令。

涉及函数：

不限定函数。总控只做复审、测试、部署和验证，不扩大业务需求。

修改说明：

1. 汇总所有 agent 改动。
2. 检查是否越界修改。
3. 运行后端和前端测试。
4. git 整理提交。
5. 部署前登录服务器检测是否有策略在运行。
6. 如有运行策略，立即阻塞部署并上报。
7. 无运行策略时部署更新。
8. 使用 `shun666` 账号进行服务器自测。
9. 允许真实下注测试：AI推荐平注，球1，金额1，下注时机默认。
10. 观测本轮更新是否符合预期。

禁止事项：

1. 有策略运行时禁止部署。
2. 未完成测试禁止部署。
3. 未确认代码同步禁止真实下注。
4. 不擅自清理生产数据。
5. 不扩大真实下注测试金额和策略范围。

验收方式：

1. 本地测试通过。
2. 部署前确认无运行策略。
3. 服务重启后共享采集正常。
4. 前端开奖状态正常。
5. worker 下注前没有额外平台倒计时重校验。
6. 真实下注 1 期可成功执行。
7. 结算按新分支执行，不阻塞下一期。
8. 告警中文且带日志编号。
9. 输出完整测试报告。

回滚风险：

高。涉及生产部署和真实下注，必须严格门禁。

是否需要前端联动：

需要。

是否需要后端测试：

需要。

## agent 执行要求

每个 `gpt5.3codex` agent 必须遵守：

1. 只能修改任务列出的文件和函数。
2. 不能触碰任务外文件。
3. 不能自行新增功能。
4. 不能删减用户历史数据。
5. 不能改变策略算法。
6. 不能改变真实下注金额逻辑。
7. 如遇阻塞，停止并汇报。
8. 完成后必须说明修改文件、测试命令、测试结果和剩余风险。

阀门自检要求：

1. 后端任务至少运行对应 pytest。
2. 前端任务至少运行对应前端测试。
3. 涉及 schema 的任务必须验证前后端字段一致。
4. 涉及 worker 的任务必须验证不会额外调用平台倒计时接口。
5. 涉及结算的任务必须验证幂等和不阻塞下注。
6. 自检未 pass 不得提交给总控。

## 最终报告要求

所有任务完成、部署和服务器自测后，总控最终报告必须包含：

1. 本轮实现了哪些内容。
2. 哪些文件被修改。
3. 哪些测试通过。
4. 部署前是否有运行策略。
5. 部署和重启结果。
6. `shun666` 真实下注测试过程。
7. 是否出现告警。
8. 下注和结算是否符合预期。
9. 当前仍存在的风险点。
10. 是否建议继续观察。
