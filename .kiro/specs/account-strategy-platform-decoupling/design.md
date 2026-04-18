# 账号与策略平台类型彻底解耦设计

## 结论

当前代码已经把 worker 主执行链路大体拆到了 `strategy.platform_type`，但账号验证和策略可选平台来源仍然是旧模型，主要问题有四个：

1. 绑定账号时仍然会默认落到某个平台做登录校验。
2. 验证账号时仍然会默认把 JND 账号当成 `JND28WEB`。
3. 策略可选平台仍然从 `game_type` 静态推导，而不是从真实验证结果读取。
4. 账号总状态和平台级事实仍混用，容易让“验证了一边、启动了另一边”这种语义错位继续存在。

本次落盘后的目标设计不是“再补一层默认值”，而是把链路明确拆成：

`绑定账号 -> 验证账号并探测真实平台集合 -> 策略只从验证结果里选平台 -> worker 只按策略平台执行`

另外，当前处于测试阶段，旧数据迁移和保留不在本轮范围内。

## 认知修正

本轮讨论后，以下业务认知作为后续实现的前提：

1. JND 的 `JND28WEB` 与 `JND282` 是同一站点同一账号下的两个平台页签，不是两个独立绑定账号。
2. “JND 账号理论候选平台有两个”不等于“该账号真实支持两个平台”；真实支持集合必须通过验证阶段探测得出。
3. 绑定阶段不需要探测平台能力，平台能力识别属于验证阶段职责。
4. 平台能力探测与平台开盘状态探测不是一回事，但两者都不能用 `heartbeat` 代替。
5. `heartbeat` 只能证明会员登录态是否在线，不能证明某个 `platform_type` 是否存在，也不能证明其当前是否开盘。
6. 带 `lotteryType` 的状态接口才是平台事实来源，赔率接口只应在确认开盘后调用。
7. 正常链路应当是“一次正式登录，多平台探测”，而不是按每个平台重复做正式登录。
8. worker 只负责执行策略平台，不负责在运行时猜测账号平台能力。

## 冻结结论与实施门禁

### 1. 方案裁决

本设计正式采用**方案 B**，并明确否决方案 A：

- 方案 A（否决）：同一个 JND 账号在任一时刻只允许运行一种 JND 平台类型。
- 方案 B（采用）：runtime / session / odds / 订单快照 / worker key 继续按 `(account_id, platform_type)` 隔离，策略创建、编辑、启动只以“当前有效验证批次”导出的 `allowed_strategy_platform_types` 为门禁。

裁决理由：

1. 当前代码底座已经按 `(account_id, platform_type)` 隔离 runtime、session、odds 与订单快照。
2. 本 spec 的目标是修正账号验证事实源与策略门禁，不是把已存在的平台隔离底座再回退成账号级单平台限制。
3. `operator-console-mobile-refactor` 与 `jnd-dw3-strategy-betting` 只能消费这个上游裁决，不得在各自 spec 内重新打开 A/B 决策。

### 2. 固定配置值

- `verification_ttl_minutes = 30`
- `verification_timeout_seconds = 90`

说明：

1. 30 分钟 TTL 用于限制“平台能力结果”的新鲜度，而不是要求运行中策略即时停机；TTL 失效后只阻断新建/启动与受控重登。
2. 90 秒同步超时预算覆盖“一次正式登录 + 多平台状态探测 + 开盘平台赔率同步”；超时时统一返回 `504 verification_timeout`。

### 3. `/accounts/{id}/login` 临时别名生命周期

1. MVP 期间允许保留 `/accounts/{id}/login` 作为 `POST /accounts/{account_id}/verify` 的 deprecated alias。
2. 新前端、新测试、新脚本、新 spec 一律只允许使用 `/accounts/{account_id}/verify`。
3. alias 只服务于存量调用迁移，不得新增任何首方调用依赖。
4. 当仓库内 `frontend/src/api/accounts.ts`、e2e、fixtures、脚本完成迁移并经搜索确认无首方调用后，alias 必须进入删除阶段。
5. `operator-console-mobile-refactor` 与 `jnd-dw3-strategy-betting` 不得以 alias 作为前置契约继续排期。

### 4. 跨 spec 串并行顺序

1. `account-strategy-platform-decoupling` 必须先于 `operator-console-mobile-refactor` 合并。
2. `jnd-dw3-strategy-betting` 中涉及 `frontend/src/pages/operator/StrategyForm.tsx`、`backend/app/api/strategies.py`、`backend/app/engine/adapters/jnd.py`、`backend/app/engine/manager.py` 的任务，必须在本 spec 对应阶段完成并释放写集后再开工。
3. `multi-strategy-bet-timing-fix` 不得与本 spec 并行改写账号验证链路文件，也不得借机引入 verification-run 相关 DDL、字段或接口契约。
4. `lottery-countdown-display` 可继续复用 `GetCurrentInstall?lotteryType=`、`close_countdown_sec`、`open_countdown_sec` 与 State 语义，但不得并行改写 `jnd.py` 中的状态语义。
5. 在 `lottery-countdown-display` 正式复用共享执行语义前，必须先冻结 `Confirmbet` 零重试与 `backend/app/engine/executor.py` 现有“一次赔率变更重试”是统一规则还是刻意分层差异；未冻结前不得继续漂移共享执行语义。

### 5. 写集归属冻结

| 写集 | owner spec | 冻结规则 |
| --- | --- | --- |
| `backend/app/api/accounts.py` `backend/app/schemas/account.py` `frontend/src/api/accounts.ts` `frontend/src/types/api/account.ts` `frontend/src/pages/operator/Accounts.tsx` | `account-strategy-platform-decoupling` | `multi-strategy-bet-timing-fix` 在本 spec 合并前不得并行改写 |
| `backend/app/api/strategies.py` `frontend/src/pages/operator/StrategyForm.tsx` | `account-strategy-platform-decoupling` 先冻结接口，再移交 `jnd-dw3-strategy-betting` | 下游不得在接口冻结前并行开工 |
| `backend/app/engine/adapters/jnd.py` `backend/app/engine/manager.py` | `account-strategy-platform-decoupling` 先冻结验证/启动门禁语义，再移交下游 | `jnd-dw3-strategy-betting` 与 `lottery-countdown-display` 不得并行改写同一语义 |

## Interfaces

### `POST /accounts`

用途：

- 绑定账号静态信息

请求体：

- `account_name`
- `password`
- `game_type`
- `platform_url`

规则：

1. 不接收 `platform_type`。
2. 不在绑定阶段探测 JND 是单平台还是双平台。
3. 不在绑定阶段默认走 `JND28WEB` 或 `JND282` 登录校验。

### `POST /accounts/{account_id}/verify`

用途：

- 验证账号并自动探测真实平台能力

请求行为：

1. 该接口定义为同步接口。
2. 同一账号在任意时刻只允许一个验证批次运行。
3. 若同账号已有验证在运行，接口返回 `409 verification_in_progress`。
4. 该互斥不是纯接口约定；底层必须由数据库级唯一约束强制保证“同一账号最多一个 `run_status=running` 批次”，应用层只负责把约束冲突翻译成 `409 verification_in_progress`。
5. 创建新的 `verification_run`，并记录账号当前关键字段快照。
6. 建立一次会员登录态。
7. 根据 `game_type` 取候选平台集合。
8. 对每个候选 `platform_type` 调用平台级状态探测接口。
9. 对 `open` 状态的平台同步赔率。
10. 同步接口必须定义明确的超时预算；若超时发生，当前批次状态记为 `timed_out`，并返回 `504 verification_timeout`。
11. 在批次完成后，原子性提交本批全部平台结果，并将其标记为最新 completed run。
12. 若批次失败、取消或超时，必须保留上一批已完成有效结果不变。

兼容策略：

- `/accounts/{id}/login` 仅在迁移期内保留为 deprecated alias；生命周期与删除条件按“冻结结论与实施门禁”执行。
- 用户语义统一为“验证账号”。

### `GET /accounts`

返回新增/调整字段：

- `allowed_strategy_platform_types`
- `platform_capabilities`
- `latest_verification_run_id`
- `effective_verification_run_id`
- `verification_in_progress`
- `verification_stale`
- `summary_status_reason`
- `status` 仅作摘要

规则：

1. `allowed_strategy_platform_types` 必须来自“最新 completed 且未失效的验证批次”。
2. `platform_capabilities` 至少包含：
   - `platform_type`
   - `verify_status`
   - `market_state`
   - `detected_issue`
   - `odds_synced`
   - `odds_message`
   - `last_verified_at`
3. `latest_verification_run_id` 表示最近一次触发的批次，不论其当前是 `running`、`completed`、`failed`、`timed_out` 还是 `cancelled`。
4. `effective_verification_run_id` 只表示当前真正生效的批次；它只能指向“最新 completed 且未失效”的 run。
5. `verification_in_progress` 是前端可直接消费的布尔摘要，等价于“当前是否存在 `run_status=running` 的批次”。
6. `verification_stale` 是前端可直接消费的布尔摘要；仅用于表达“最近一次 completed run 已因 TTL 或账号关键字段变更而失效”，前后端不得自行推断。
7. `summary_status_reason` 只在需要补充解释时返回，用于区分“验证失败-不支持”“验证失败-探测异常”等二级说明。

### `POST /strategies`

规则：

1. `platform_type` 必须属于账号当前 `effective_verification_run_id` 指向批次导出的 `allowed_strategy_platform_types`。
2. 不再接受“按 `game_type` 静态推导就默认允许”的旧逻辑。
3. 账号尚未验证出任何可用平台时，不允许创建依赖该账号的平台策略。

### `PUT /strategies/{id}`

规则：

1. 仅允许切换到该账号当前 `effective_verification_run_id` 指向批次已验证支持的平台。
2. 若账号当前只支持一个平台，则前端保持锁定，后端也必须拒绝其他值。

### `GET/POST /accounts/{account_id}/odds*`

以下接口继续要求显式 `platform_type`：

- `GET /accounts/{account_id}/odds`
- `POST /accounts/{account_id}/odds/refresh`
- `POST /accounts/{account_id}/odds/confirm`

原因：

- 赔率本身就是平台级数据，不能再做默认平台回退。

## Data Model

### 1. `gambling_accounts`

保留为账号静态信息与摘要信息：

- `id`
- `operator_id`
- `account_name`
- `password`
- `game_type`
- `platform_url`
- `status` 摘要状态
- `balance` 摘要余额
- `last_login_at` 摘要时间
- `kill_switch`

说明：

1. 账号表不再承载“这个账号真实支持哪些平台”的事实。
2. 账号表上的 `status`、`balance`、`last_login_at` 只能作为摘要展示，不再用于目标平台启动判断。

### 2. `account_verification_runs` 新表

用途：

- 记录每一次“验证账号”的批次元数据

建议字段：

- `id`
- `account_id`
- `run_status`：`running / completed / failed / timed_out / cancelled`
- `snapshot_game_type`
- `snapshot_platform_url`
- `snapshot_password_hash`
- `stale`
- `stale_reason`
- `started_at`
- `finished_at`
- `created_at`
- `updated_at`

索引建议：

- `(account_id, started_at DESC)`
- `UNIQUE(account_id) WHERE run_status = 'running'`
- `(account_id, run_status, stale)`

说明：

1. 每次验证请求先创建一条 run。
2. 只有 `completed + stale = false` 的 run 才有资格成为当前有效验证批次。
3. `failed / timed_out / cancelled` run 只保留审计价值，不能导出策略可选平台。
4. 新批次提交失败时，不得覆盖上一批 completed 且未失效的结果。
5. “同账号最多一个 running 批次”必须由上述唯一约束强制执行，不能只靠内存锁或前端防重。

### 3. `account_platform_capabilities` 新表

用途：

- 持久化某一验证批次下，每个平台的探测结果

建议字段：

- `id`
- `verification_run_id`
- `platform_type`
- `verify_status`：`supported / unsupported / probe_failed / unknown`
- `market_state`：`open / closed / waiting / unknown`
- `detected_issue`
- `last_verified_at`
- `odds_synced`
- `odds_count`
- `odds_message`
- `last_error`
- `created_at`
- `updated_at`

唯一键：

- `UNIQUE(verification_run_id, platform_type)`

说明：

1. `allowed_strategy_platform_types` 只从“最新 completed 且未失效 run”中 `verify_status = supported` 的记录导出。
2. `probe_failed` 不能被当成“不支持”。
3. `closed / waiting` 代表该平台存在，但当前不应盲拉赔率。
4. 同一账号的不同验证批次结果不能混写到同一组 capability 记录里。

### 4. `account_platform_sessions`

继续作为平台会话与 worker 锁表：

- `account_id`
- `platform_type`
- `status`
- `session_token`
- `login_fail_count`
- `last_login_at`
- `worker_lock_token`
- `worker_lock_ts`

说明：

1. 这是会话事实表，不是策略平台可选项的来源。
2. 会话失效不应直接抹掉“该平台受支持”这一事实。

### 5. `account_odds`

唯一键保持/升级为：

- `UNIQUE(account_id, platform_type, key_code)`

说明：

1. 网盘与 2.0 赔率必须彻底隔离。
2. 人工确认动作必须绑定到明确的 `platform_type`。

### 6. `bet_orders`

新增/保留字段：

- `actual_platform_type`

说明：

1. 订单必须记录实际执行平台。
2. 结算、对账、补偿优先依赖订单快照。

## 核心流程

### 1. 绑定账号

1. 前端提交 `game_type + platform_url + account_name + password`
2. 后端仅做本地字段校验、重复校验、数据保存
3. 返回“待验证”状态

说明：

- 绑定阶段不探测单平台/双平台
- 绑定阶段不拉赔率
- 绑定阶段不默认走任何 `platform_type`

### 2. 验证账号

1. 创建新的 `verification_run(run_status=running)`
2. 建立一次会员登录态
3. 读取候选平台集合：
   - `JND28 -> [JND28WEB, JND282]`
   - `LUCKYSB -> [LUCKYSB]`
4. 对每个候选平台调用平台级状态探测接口
5. 记录：
   - 是否支持
   - 当前开盘状态
   - 当前期号
   - 是否同步赔率
   - 失败原因
6. 若同步接口在预算时间内未完成，则将该 run 标记为 `timed_out`，返回 `504 verification_timeout`，并保留上一批有效结果不变
7. 全部平台探测完成后，统一写入该 run 的 capability 记录
8. 将 run 标记为 `completed`
9. 切换当前有效验证批次
10. 汇总生成账号摘要状态

关键约束：

1. `heartbeat` 只用于保活，不能用于能力探测。
2. 平台能力和开盘状态探测必须调用带 `lotteryType` 的状态接口。
3. 只有 `market_state = open` 时才拉赔率。
4. 若 run 失败、取消或超时，当前有效验证批次保持不变。
5. 任何 capability 导出逻辑都不能跨批次拼接结果。
6. 同账号重复触发验证时，先依赖数据库级唯一约束拒绝第二个 `running` 批次，再由应用层统一返回 `409 verification_in_progress`。

### 3. 策略创建与编辑

1. 前端从 `allowed_strategy_platform_types` 渲染平台选项
2. 单平台账号：
   - 只有一个选项
   - 默认选中
   - 禁止修改
3. 双平台账号：
   - 两个选项
   - 由操作者选择
4. 后端再次校验传入值属于 `effective_verification_run_id` 指向批次导出的当前验证结果

### 3.1 验证结果失效

以下情况必须立即把“当前有效验证批次”标为 `stale`：

1. 账号密码变更
2. 账号站点地址变更
3. `game_type` 变更
4. 验证结果超过配置 TTL

失效后的规则：

1. `allowed_strategy_platform_types` 为空
2. 不允许新建策略
3. 不允许启动依赖该账号的平台策略
4. 页面应提示先重新验证
5. 已在运行中的策略不立即强制停止，只做告警和“需重验”标记
6. 若运行中的策略后续需要重新登录、重建会话或执行受控重登，则必须拒绝恢复，并以“验证结果已失效，需要重新验证”结束该次恢复流程

### 4. worker 执行

1. manager 继续按 `(account_id, platform_type)` 管理 runtime
2. worker 启动只吃 `strategy.platform_type`
3. risk / executor / settlement / reconciler 全链路只认这个平台值
4. 平台会话、赔率、订单快照全部按该平台读写

### 5. 会话复用与受控重登

目标链路：

1. 验证阶段完成一次正式登录
2. 后续同运行期内优先复用该登录态做平台探测与赔率同步
3. worker 启动优先复用可用会话

受控回退：

1. 如果会话已失效、服务重启或当前实现无法安全持久化 cookie jar，worker 允许做一次受控重登
2. 这种重登属于异常恢复，不是常态验证链路

## Error Handling

### 1. 平台探测结果分类

必须把平台探测结果区分为：

- `supported`
- `unsupported`
- `probe_failed`
- `unknown`

原因：

- 如果把临时失败直接当成“不支持”，策略选项会被误删。

### 2. 部分可用状态

双平台账号可能出现：

- 一边 `supported + open + 有赔率`
- 一边 `supported + closed/waiting + 无赔率`
- 一边 `supported`
- 一边 `probe_failed`

处理要求：

1. 后端必须保留平台级明细
2. 前端可以隐藏具体平台名，但一旦部分可用，必须明确提示“部分可用”

### 2.1 账号摘要状态真值表

账号摘要状态只根据“最新 completed 且未失效的验证批次”计算：

| 输入条件 | 摘要状态 | `summary_status_reason` |
| --- | --- | --- |
| 不存在 completed 且未失效 run | 未验证 | `not_verified` |
| `supported_count >= 1` 且 `probe_failed_count = 0` | 已验证 | `null` |
| `supported_count >= 1` 且 `probe_failed_count >= 1` | 部分可用 | `probe_partial_failure` |
| `supported_count = 0` 且 `unsupported_count >= 1` 且 `probe_failed_count = 0` | 验证失败 | `unsupported_only` |
| `supported_count = 0` 且 `probe_failed_count >= 1` 且 `unsupported_count = 0` | 验证失败 | `probe_failed_only` |
| `supported_count = 0` 且 `unsupported_count >= 1` 且 `probe_failed_count >= 1` | 验证失败 | `unsupported_with_probe_failed` |

补充说明：

1. “单平台 `supported + closed`”属于 `已验证`，因为平台能力已被确认，只是当前不开盘。
2. “一边 `supported`，一边 `unsupported`”属于 `已验证`，因为这是完整可解释的单平台账号结果，不是部分失败。
3. “一边 `supported`，一边 `probe_failed`”属于 `部分可用`。
4. “全部 `unsupported`”或“全部 `probe_failed` 且没有 supported”都归为 `验证失败`，但必须通过 `summary_status_reason` 继续区分“明确不支持”和“探测异常”。

### 3. 心跳语义

`heartbeat` 仅表示：

- 当前已登录会员会话是否在线

不能表示：

- 某个 `platform_type` 是否存在
- 某个 `platform_type` 当前是否开盘

### 4. 策略启动前检查

启动策略时不能只看 `account.status == online`。

正确检查顺序：

1. 是否存在 `effective_verification_run_id` 指向的当前有效验证批次
2. 目标 `platform_type` 是否属于该批次导出的 `allowed_strategy_platform_types`
3. 目标平台当前是否有可用会话；若无，则允许 worker 走受控重登

## Configuration

### 1. 候选平台映射

- `JND28 -> [JND28WEB, JND282]`
- `LUCKYSB -> [LUCKYSB]`

说明：

- 这只是“候选探测列表”，不是最终策略可选项。

### 2. 状态映射

平台状态统一映射为：

- `1 -> open`
- `2 -> closed`
- `3 -> waiting`
- 其他 -> `unknown`

### 3. 验证展示文案

账号页摘要状态建议支持：

- `未验证`
- `已验证`
- `部分可用`
- `验证失败`

### 4. 验证有效期

固定默认值：

- `verification_ttl_minutes = 30`
- `verification_timeout_seconds = 90`

规则：

1. 超过 TTL 的 completed run 自动转为 `stale`
2. stale run 不再导出 `allowed_strategy_platform_types`
3. stale run 保留查询，但只作历史记录
4. 超过同步验证超时预算的 run 必须转为 `timed_out`
5. `timed_out` run 返回统一的 `504 verification_timeout`
6. 如需调整以上默认值，必须回到 spec 评审并同步更新 requirements / tasks / 下游依赖 spec

## Test Strategy

### 后端

至少新增或重写：

1. 绑定账号不再默认走 `JND28WEB`
2. 验证阶段只做一次正式登录
3. 同账号重复点击验证时返回 `verification_in_progress`
4. 同账号在数据库层不会同时存在两个 `running` 验证批次
5. `verification_run` 失败不会覆盖上一批 completed 且未失效结果
6. `verification_run` 超时会转为 `timed_out` 并返回 `504 verification_timeout`
7. `GetCurrentInstall` 成功时能正确产出 `supported + market_state`
8. `heartbeat` 不参与平台能力判定
9. `open` 状态才同步赔率
10. `closed / waiting / unknown` 不同步赔率但写入状态
11. `allowed_strategy_platform_types` 来自最新 completed 且未失效 run
12. `latest_verification_run_id` 与 `effective_verification_run_id` 语义明确且不会混用
13. 密码 / 地址 / game_type 变更后，run 变 stale
14. 验证结果 stale 后，已运行策略不立即强停，但后续受控重登会被阻断并要求先重验
15. 账号摘要状态按真值表输出，并带 `summary_status_reason`
16. 策略启动检查面向目标平台而不是账号总状态
17. worker 继续按 `(account_id, platform_type)` 运行

### 前端

至少新增或重写：

1. 账号绑定页不再出现 JND 平台选择
2. 账号验证后，双平台/单平台结果展示正确
3. 单平台账号的策略平台选项锁定
4. 双平台账号的策略平台选项可切换
5. 部分可用状态展示不误导
6. 摘要状态与平台明细不会相互矛盾
7. “验证失败”状态会继续区分“明确不支持”和“探测异常”
8. 前端不会混用 `latest_verification_run_id` 与 `effective_verification_run_id`

## 风险与处理

### 风险 1：当前适配器还缺少“明确不支持”结构化结果

问题：

- 现有返回更偏消息字符串，容易把临时失败误判成“不支持”

处理：

1. 增加平台探测结果类型化返回
2. 在 `unsupported` 与 `probe_failed` 之间做严格区分

### 风险 2：验证会话复用能力还不完整

问题：

- 当前系统主要持久化 `session_token`，没有完整 cookie jar 复用能力

处理：

1. 先把验证链路改成一次登录、多平台探测
2. worker 启动优先复用可用会话
3. 服务重启或会话失效时允许受控重登

### 风险 3：账号摘要状态继续被误当成平台事实

问题：

- 若只看 `account.status`，会继续出现“验证了一边，启动另一边”的语义错位

处理：

1. 策略可选项只看能力表
2. 启动检查只看目标平台
3. 账号摘要状态只用于展示

### 风险 4：旧默认平台回退残留

问题：

- 只要还存在“不传平台就默认 JND28WEB”的逻辑，链路就会继续偏

处理：

1. 删除默认平台回退
2. 删除静态 `game_type -> allowed_strategy_platform_types` 直接输出
3. 回归测试覆盖默认回退路径

### 风险 5：不同验证批次结果被混写

问题：

- 如果 capability 记录没有验证批次边界，并发验证或半成功写入会把不同批次结果拼在一起

处理：

1. 引入 `account_verification_runs`
2. capability 记录按 `verification_run_id` 挂载
3. 只允许从最新 completed 且未失效 run 导出策略可选平台

## Out Of Scope

1. 旧 JND 双账号历史数据迁移与合并
2. 为保留旧数据而设计的长期双轨兼容
3. 在本轮内重做 LUCKYSB 未实现适配器
