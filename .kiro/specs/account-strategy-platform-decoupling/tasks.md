# 账号与策略平台类型彻底解耦任务

## 当前进度结论

本轮审查后，任务可以拆成两部分：

1. **已完成的底座改造**
2. **仍待完成的验证链路与平台能力来源改造**

当前测试阶段不做旧数据迁移与保留。

## 已完成的底座改造

### 已落地

- [x] 账号主模型已改为 `game_type`
- [x] `account_platform_sessions` 已存在并按 `(account_id, platform_type)` 存平台会话
- [x] `account_odds` 已按平台隔离
- [x] `bet_orders.actual_platform_type` 已存在
- [x] manager 已使用复合键 `RuntimeKey = (account_id, platform_type)`
- [x] worker 已按平台启动并持有固定 `platform_type`
- [x] risk 已按平台会话检查
- [x] odds 接口已要求显式 `platform_type`
- [x] 前端账号页绑定时不再要求选择 `JND28WEB/JND282`
- [x] 前端账号操作文案已切到“验证账号”

### 已完成项验证阀门

- [x] 同账号下不同平台可进入独立 runtime
- [x] 赔率刷新已能明确区分平台
- [x] 订单已能记录实际执行平台

## Phase 0. 审查与冻结

目标：

- [x] 确认当前默认平台回退仍残留在绑定与验证链路
- [x] 确认当前 `allowed_strategy_platform_types` 仍来自静态 `game_type` 映射
- [x] 确认当前策略启动前仍只看 `account.status`
- [x] 确认 `heartbeat` 不能作为平台能力探测或开盘状态探测依据
- [x] 冻结运行方案为方案 B：worker/runtime/session 继续按 `(account_id, platform_type)` 隔离
- [x] 写死 `verification_ttl_minutes = 30`
- [x] 写死 `verification_timeout_seconds = 90`
- [x] 定义 `/accounts/{id}/login` deprecated alias 生命周期
- [x] 冻结与 `operator-console-mobile-refactor`、`jnd-dw3-strategy-betting`、`multi-strategy-bet-timing-fix`、`lottery-countdown-display` 的串并行顺序与写集归属

本阶段结论：

- [x] 旧问题已定位清楚，文档级 Ready blocker 已收口，可进入实现排期

## 跨 spec 串并行顺序与独占写集

串行顺序：

1. `account-strategy-platform-decoupling` 必须先于 `operator-console-mobile-refactor` 合并。
2. `jnd-dw3-strategy-betting` 只有在本 spec 完成 Phase 4/5 并释放 `strategies.py / StrategyForm.tsx / jnd.py / manager.py` 写集后才能进入对应实现阶段。
3. `multi-strategy-bet-timing-fix` 只有在本 spec 完成账号验证契约改造后，才能进入 Phase 6 的账号提示链路实现。
4. `lottery-countdown-display` 可以消费既有状态/倒计时契约，但不得并行改写 `jnd.py` 中 `GetCurrentInstall?lotteryType=` 的状态语义。
5. `lottery-countdown-display` 在复用共享执行语义前，必须先冻结 `Confirmbet` 零重试与 `executor.py` 现有“一次赔率变更重试”是统一规则还是刻意分层差异；未冻结前不得并行漂移共享语义。

独占写集冻结：

| 文件 | owner | 冻结规则 |
| --- | --- | --- |
| `backend/app/api/accounts.py` | `account-strategy-platform-decoupling` | `multi-strategy-bet-timing-fix` 合并前不得并行改写 |
| `backend/app/schemas/account.py` | `account-strategy-platform-decoupling` | `multi-strategy-bet-timing-fix` 合并前不得并行改写 |
| `frontend/src/pages/operator/Accounts.tsx` | `account-strategy-platform-decoupling` | `multi-strategy-bet-timing-fix` 合并前不得并行改写 |
| `frontend/src/api/accounts.ts` | `account-strategy-platform-decoupling` | `multi-strategy-bet-timing-fix` 合并前不得并行改写 |
| `frontend/src/types/api/account.ts` | `account-strategy-platform-decoupling` | `multi-strategy-bet-timing-fix` 合并前不得并行改写 |
| `backend/app/api/strategies.py` | `account-strategy-platform-decoupling` 先冻结接口，再移交 `jnd-dw3-strategy-betting` | 下游不得在接口冻结前并行开工 |
| `frontend/src/pages/operator/StrategyForm.tsx` | `account-strategy-platform-decoupling` 先冻结接口，再移交 `jnd-dw3-strategy-betting` | 下游不得在接口冻结前并行开工 |
| `backend/app/engine/adapters/jnd.py` | `account-strategy-platform-decoupling` 先冻结验证语义，再移交下游 | `jnd-dw3-strategy-betting` 与 `lottery-countdown-display` 不得并行混写 |
| `backend/app/engine/manager.py` | `account-strategy-platform-decoupling` 先冻结启动门禁，再移交下游 | `jnd-dw3-strategy-betting` 不得提前并行改写 |

## Phase 1. 新增“验证批次 + 平台能力事实源”

涉及文件：

- [backend/app/database.py](H:/d/bocai_web/backend/app/database.py)
- [backend/app/models/db_ops.py](H:/d/bocai_web/backend/app/models/db_ops.py)
- [backend/app/schemas/account.py](H:/d/bocai_web/backend/app/schemas/account.py)

任务：

- [ ] 新增 `account_verification_runs` 表
- [ ] 为 run 表增加 `run_status / snapshot_game_type / snapshot_platform_url / snapshot_password_hash / stale / stale_reason / started_at / finished_at`
- [ ] 为 run 表把 `run_status` 扩展为 `running / completed / failed / timed_out / cancelled`
- [ ] 为 run 表增加“同账号最多一个 `run_status=running`”的数据库级唯一约束
- [ ] 新增或重建 `account_platform_capabilities`，按 `verification_run_id` 挂载
- [ ] 为 capability 表增加 `verify_status / market_state / detected_issue / odds_synced / odds_message / last_verified_at / last_error`
- [ ] 提供 `account_verification_run_*` 与 `account_platform_capability_*` 读写函数
- [ ] 让 `allowed_strategy_platform_types` 改为从“最新 completed 且未失效 run”导出，而不是从 `game_type` 静态推导
- [ ] 明确 `probe_failed` 与 `unsupported` 的区别，不能共用一个状态

验证阀门：

- [ ] 不验证账号时，`allowed_strategy_platform_types` 为空
- [ ] 单平台账号验证后，只返回一个允许平台
- [ ] 双平台账号验证后，返回两个允许平台
- [ ] 临时探测失败不会把平台永久记成不支持
- [ ] 不同验证批次结果不会混写
- [ ] 多进程场景下也不会同时落下两个 `running` run

## Phase 2. 绑定与验证链路改造

涉及文件：

- [backend/app/api/accounts.py](H:/d/bocai_web/backend/app/api/accounts.py)
- [backend/app/engine/adapters/base.py](H:/d/bocai_web/backend/app/engine/adapters/base.py)
- [backend/app/engine/adapters/jnd.py](H:/d/bocai_web/backend/app/engine/adapters/jnd.py)
- [frontend/src/api/accounts.ts](H:/d/bocai_web/frontend/src/api/accounts.ts)
- [frontend/src/pages/operator/Accounts.tsx](H:/d/bocai_web/frontend/src/pages/operator/Accounts.tsx)

任务：

- [ ] 删除绑定账号时默认走 `JND28WEB` 登录校验的逻辑
- [ ] 把账号验证接口改成同步接口
- [ ] 同一账号验证增加互斥锁，重复点击返回 `409 verification_in_progress`
- [ ] 将数据库级“单账号单 running run”约束冲突统一翻译为 `409 verification_in_progress`
- [ ] 把账号验证接口改成“一次正式登录，多平台探测”
- [ ] 验证阶段先调用带 `lotteryType` 的状态接口，不再把 `heartbeat` 当平台探测
- [ ] 仅在 `market_state = open` 时拉赔率
- [ ] `closed / waiting / unknown` 时只写状态和说明，不拉赔率
- [ ] 为同步验证接口增加明确超时预算
- [ ] 超时批次写入 `run_status=timed_out`
- [ ] 超时时统一返回 `504 verification_timeout`
- [ ] 验证完成后原子性提交整批结果，并切换当前有效 run
- [ ] 失败、取消、超时 run 不覆盖上一批 completed 且未失效结果
- [ ] 返回平台级验证结果明细与账号摘要状态
- [ ] 返回 `latest_verification_run_id / effective_verification_run_id / verification_in_progress`
- [ ] 保留 `/accounts/{id}/login` 的临时兼容别名时，也统一按“验证账号”语义执行

验证阀门：

- [ ] 验证 JND 账号时不会为 `JND28WEB` 与 `JND282` 各自再执行一次正式登录
- [ ] 重复点击验证会得到 `verification_in_progress`
- [ ] `heartbeat` 只用于保活，不参与平台能力判定
- [ ] `open` 平台能同步赔率
- [ ] `closed / waiting / unknown` 平台不会盲拉赔率
- [ ] 双平台账号允许出现“部分可用”
- [ ] 失败 run 不会污染上一次有效验证结果
- [ ] 超时 run 会明确落成 `timed_out`
- [ ] 前端不需要猜当前是“最新批次”还是“有效批次”

失败即停：

- [ ] 仍然存在“不传平台就默认 JND28WEB”的新代码路径
- [ ] 仍然把 `heartbeat` 结果直接当作平台能力或开盘状态
- [ ] capability 结果跨 run 拼接

## Phase 3. 失效规则与当前有效批次

涉及文件：

- [backend/app/api/accounts.py](H:/d/bocai_web/backend/app/api/accounts.py)
- [backend/app/models/db_ops.py](H:/d/bocai_web/backend/app/models/db_ops.py)
- [backend/app/schemas/account.py](H:/d/bocai_web/backend/app/schemas/account.py)
- [backend/app/api/strategies.py](H:/d/bocai_web/backend/app/api/strategies.py)

任务：

- [ ] 当 `password / platform_url / game_type` 变更时，将当前有效 run 标记为 `stale`
- [ ] 增加验证 TTL，超时后自动视为 `stale`
- [ ] `stale` 状态下 `allowed_strategy_platform_types` 必须为空
- [ ] `stale` 状态下禁止新建策略
- [ ] `stale` 状态下禁止启动依赖该账号的平台策略
- [ ] 返回 `latest_verification_run_id / effective_verification_run_id / verification_stale`
- [ ] 失效后对已运行策略只告警，不立即强停
- [ ] 失效后若运行中策略需要重新登录、重建会话或受控重登，则必须阻断并要求先重验

验证阀门：

- [ ] 改密码后，不重验就不能继续建/启策略
- [ ] 改站点地址后，不重验就不能继续建/启策略
- [ ] 改 `game_type` 后，旧 run 不会继续生效
- [ ] TTL 过期后，账号明确进入“需重验”状态
- [ ] 已运行策略在账号 stale 后不会被立即硬停
- [ ] 已运行策略一旦需要受控重登，会被阻断并提示先重验

## Phase 4. 策略可选平台改为读取验证结果

涉及文件：

- [backend/app/api/strategies.py](H:/d/bocai_web/backend/app/api/strategies.py)
- [backend/app/schemas/strategy.py](H:/d/bocai_web/backend/app/schemas/strategy.py)
- [frontend/src/pages/operator/StrategyForm.tsx](H:/d/bocai_web/frontend/src/pages/operator/StrategyForm.tsx)
- [frontend/src/pages/operator/Strategies.tsx](H:/d/bocai_web/frontend/src/pages/operator/Strategies.tsx)
- [frontend/src/types/api/account.ts](H:/d/bocai_web/frontend/src/types/api/account.ts)

任务：

- [ ] 删除静态 `game_type -> allowed_strategy_platform_types` 直接输出
- [ ] 策略创建/编辑校验改为读取 `effective_verification_run_id` 指向 run 的 capability
- [ ] 单平台账号在前端只展示一个平台选项，并锁定
- [ ] 双平台账号在前端展示两个平台选项
- [ ] 未验证账号禁止创建依赖该账号的平台策略
- [ ] 策略展示继续用“网盘 / 2.0”中文文案，但提交值必须是接口值

验证阀门：

- [ ] 单平台账号只能创建单一平台策略
- [ ] 双平台账号可创建两类策略
- [ ] 不再出现 `platform_type must match account platform_type`
- [ ] 前端不再把静态候选平台误当成真实支持平台

## Phase 5. 启动检查与 worker 会话对齐

涉及文件：

- [backend/app/api/strategies.py](H:/d/bocai_web/backend/app/api/strategies.py)
- [backend/app/engine/session.py](H:/d/bocai_web/backend/app/engine/session.py)
- [backend/app/engine/worker.py](H:/d/bocai_web/backend/app/engine/worker.py)
- [backend/app/engine/risk.py](H:/d/bocai_web/backend/app/engine/risk.py)

任务：

- [ ] 启动策略前不再只检查 `account.status == online`
- [ ] 启动前检查是否存在 `effective_verification_run_id` 指向的当前有效 run
- [ ] 启动前检查目标平台是否属于当前 `allowed_strategy_platform_types`
- [ ] 重启恢复与受控重登同样只消费 `effective_verification_run_id + strategy.platform_type` 门禁
- [ ] worker 启动优先复用可用会话
- [ ] 会话不可复用时，允许受控重登作为恢复机制
- [ ] 账号摘要状态继续保留，但不再作为唯一启动依据
- [ ] 账号 stale 后，运行中策略仅告警不停机；但后续受控重登必须失败退出

验证阀门：

- [ ] 验证了 `JND28WEB` 但没验证 `JND282` 时，不能直接启动 `JND282` 策略
- [ ] 会话失效时只影响当前目标平台
- [ ] worker 仍然只认 `strategy.platform_type`
- [ ] 账号 stale 后，运行中的 runtime 不会被无条件强停
- [ ] 账号 stale 后，运行中的 runtime 一旦需要受控重登会被阻断

## Phase 6. 前端收口与状态表达

涉及文件：

- [frontend/src/pages/operator/Accounts.tsx](H:/d/bocai_web/frontend/src/pages/operator/Accounts.tsx)
- [frontend/src/pages/operator/StrategyForm.tsx](H:/d/bocai_web/frontend/src/pages/operator/StrategyForm.tsx)
- [frontend/src/pages/operator/Strategies.tsx](H:/d/bocai_web/frontend/src/pages/operator/Strategies.tsx)
- [frontend/src/types/api/account.ts](H:/d/bocai_web/frontend/src/types/api/account.ts)

任务：

- [ ] 账号列表增加“未验证 / 已验证 / 部分可用 / 验证失败”摘要状态
- [ ] 按统一真值表实现摘要状态映射
- [ ] 增加 `summary_status_reason` 到前端类型与渲染逻辑
- [ ] 双平台全部可用时可隐藏具体平台名
- [ ] 只要出现部分可用，必须明确提示
- [ ] 账号详情或弹层展示平台级状态明细
- [ ] 策略表单根据 `allowed_strategy_platform_types` 渲染可选项

验证阀门：

- [ ] 非程序员操作者能看懂当前账号是否可直接用
- [ ] 不会把“只支持一个平台”的账号误显示成双平台都可用
- [ ] “单平台 supported + closed” 显示为“已验证”
- [ ] “一边 supported 一边 probe_failed” 显示为“部分可用”
- [ ] “全部 unsupported 或全部 probe_failed 且无 supported” 显示为“验证失败”
- [ ] “验证失败”状态下还能继续区分“明确不支持”和“探测异常”

## Phase 7. 测试 / 夹具 / 脚本 / 文档清理

涉及文件：

- [backend/tests/e2e/browser/test_bind_account_fixed.py](H:/d/bocai_web/backend/tests/e2e/browser/test_bind_account_fixed.py)
- [frontend/src/api/accounts.test.ts](H:/d/bocai_web/frontend/src/api/accounts.test.ts)
- 其他 mocks / fixtures / README / 示例数据 / 操作文档

任务：

- [ ] 清理 e2e 里“绑定时选择平台”的旧流程
- [ ] 清理前端 API 测试里静态 `allowed_strategy_platform_types` 旧假设
- [ ] 清理前端 API、e2e、脚本里对 `/accounts/{id}/login` 的首方调用
- [ ] 清理 mocks / fixtures 中旧平台绑定样本
- [ ] 清理 README、示例数据、脚本中的旧绑定描述
- [ ] 清理旧 spec 或备注中仍残留的默认平台假设
- [ ] 增加仓库搜索阀门或 CI 检查，禁止新增 `/accounts/{id}/login` 首方调用
- [ ] 增加仓库搜索阀门或 CI 检查，禁止新增静态 `game_type -> allowed_strategy_platform_types` 导出
- [ ] 仓库首方调用迁移完成后删除 deprecated alias `/accounts/{id}/login`

验证阀门：

- [ ] 不再存在新的测试夹具继续依赖绑定阶段选平台
- [ ] 不再存在新的 mocks 把静态候选平台当真实支持平台
- [ ] 仓库首方调用不再依赖 `/accounts/{id}/login`
- [ ] 搜索阀门或 CI 会对新增旧契约引用直接失败
- [ ] 非代码文档与实际行为一致

## Phase 8. 删除残留旧链路

必须删除：

- [ ] 绑定账号时默认平台登录校验
- [ ] 验证账号时默认平台回退
- [ ] 账号返回里按 `game_type` 直接静态输出 `allowed_strategy_platform_types`
- [ ] 启动策略时只看 `account.status`
- [ ] 任何把 `heartbeat` 当能力探测的实现
- [ ] 前端 `WEB/2.0` 伪值提交
- [ ] 任何新的 `/accounts/{id}/login` 首方调用
- [ ] 任何旧测试、夹具、脚本中的默认平台假设

删除 Gate：

- [ ] 后端目标测试全绿
- [ ] 前端目标测试全绿
- [ ] 人工回归通过
- [ ] 代码搜索确认无新的默认平台回退
- [ ] 代码搜索确认无新的静态 `allowed_strategy_platform_types` 导出
- [ ] 代码搜索确认无新的仅看 `account.status` 的启动链路

## 不在本轮范围

- [x] 旧 JND 双账号历史数据迁移与合并
- [x] 为保留旧数据而做的长期兼容兜底

## 必跑验证

### 后端

- [ ] `tests/test_accounts.py`
- [ ] `tests/test_strategies.py`
- [ ] `tests/test_engine_manager.py`
- [ ] `tests/test_worker.py`
- [ ] `tests/test_session.py`
- [ ] `tests/test_risk.py`
- [ ] `backend/tests/e2e/browser/test_bind_account_fixed.py`
- [ ] 新增 verification run 原子切换测试
- [ ] 新增 verification run 单账号单 `running` 约束测试
- [ ] 新增 stale 失效规则测试
- [ ] 新增 capability 探测测试
- [ ] 新增验证阶段单次登录测试
- [ ] 新增平台状态与赔率联动测试
- [ ] 新增同步验证超时转 `timed_out` 测试
- [ ] 新增账号 stale 后运行中策略受控重登被阻断测试

### 前端

- [ ] `src/pages/operator/Accounts.test.tsx`
- [ ] `src/api/accounts.test.ts`
- [ ] `src/pages/operator/StrategyForm.test.tsx`
- [ ] `src/pages/operator/Strategies.test.tsx`
- [ ] 新增单平台锁定测试
- [ ] 新增部分可用状态展示测试
- [ ] 新增 `summary_status_reason` 展示测试

## 复审入口条件

- [ ] `operator-console-mobile-refactor` 已删除“JND 平台始终可选”的旧要求，并明确只消费上游有效验证结果
- [ ] `/verify`、`latest_verification_run_id`、`effective_verification_run_id`、`verification_stale`、`summary_status_reason` 的接口契约已冻结
- [ ] verification-run DDL、单账号单 `running` 约束、超时与 stale 规则的测试清单已落到代码任务，不再停留在 spec 文字
- [ ] 仓库首方 `/accounts/{id}/login` 调用与测试迁移计划已列清，且共享写集顺序已被下游 spec 接受

## 最终收口条件

- [ ] `allowed_strategy_platform_types` 已只来自最新 completed 且未失效 run
- [ ] 策略可选平台已完全来自验证结果
- [ ] 验证阶段已实现“一次登录，多平台探测”
- [ ] 验证 run 已支持数据库级互斥、超时语义、原子提交和失效规则
- [ ] 只有开盘平台会同步赔率
- [ ] worker / 风控 / 下单 / 结算已全链路只认策略平台
- [ ] 账号接口已明确区分最新触发批次与当前有效批次
- [ ] 账号 stale 后的运行中策略行为已按 spec 固定，不再靠实现时自定
- [ ] 旧默认平台回退已删除
- [ ] 仓库首方调用已不再依赖 `/accounts/{id}/login`
- [ ] 搜索阀门或 CI 已阻断新增 `/accounts/{id}/login` 首方调用与静态 `allowed_strategy_platform_types` 导出
