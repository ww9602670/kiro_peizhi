# 账号与策略平台类型彻底解耦

## 目标

把当前“账号绑定和验证默认落到某个固定平台，再由策略被动适配账号平台”的旧链路改成下面这套新模型：

1. 绑定账号时只保存账号、密码、游戏类型、站点地址，不选择平台类型，也不在绑定阶段探测平台能力。
2. 验证账号时建立一次会员会话，然后自动探测该账号真实支持的平台类型集合。
3. 只有当目标平台当前处于开盘状态时才拉赔率；封盘、停盘、等待开奖时只记录状态，不拉赔率。
4. 策略可选平台必须来自“验证得到的真实支持集合”，而不是静态 `game_type -> platform_type` 映射。
5. worker 只认 `strategy.platform_type`，执行、风控、赔率、下单、结算都按策略平台运行。
6. 当前处于测试阶段，不做旧数据迁移与保留。

## 术语

- `game_type`
  - `JND28`
  - `LUCKYSB`
- `platform_type`
  - `JND28WEB`
  - `JND282`
  - `LUCKYSB`
- `verification_run`
  - 一次“验证账号”动作产生的一整批平台探测结果快照
- `allowed_strategy_platform_types`
  - 仅由“最新 completed 且未失效的验证批次”导出的可建策略平台集合
- `market_state`
  - `open`
  - `closed`
  - `waiting`
  - `unknown`

规则：

1. `JND28` 账号理论候选平台是 `JND28WEB` 与 `JND282`，但最终允许创建策略的平台集合必须以后端验证结果为准。
2. `LUCKYSB` 当前仍只允许 `LUCKYSB`。
3. `heartbeat` 只用于已登录会话保活，不能作为平台能力探测或开盘状态探测依据。

## 已确认的业务事实与认知修正

1. JND 的 `JND28WEB` 与 `JND282` 不是两个需要分别绑定的账号主体，而是同一站点、同一账号下的两个平台页签。
2. `JND28` 不等于“这个账号一定双平台可用”，因为现实里有单平台账号，也有双平台账号；最终以验证结果为准。
3. 绑定账号阶段无需判断该账号是单平台还是双平台，这个判断应放在验证阶段完成。
4. 验证阶段的核心不是“再登录几次”，而是“建立一次登录态后，对候选平台逐个探测真实能力与当前状态”。
5. 平台能力探测不能依赖 `heartbeat`，因为 `heartbeat` 只能说明会员会话在线，不能说明某个 `platform_type` 是否存在，也不能说明是否开盘。
6. 平台开盘、封盘、等待开奖等状态应以带 `lotteryType` 的状态接口返回为准。
7. 赔率不是能力探测入口，而是状态探测后的下一步动作；只有平台当前处于开盘状态时才应拉赔率。
8. worker 的职责是执行策略，不负责决定账号支持哪个平台；平台选择必须在策略保存前就确定。

## 开工前冻结结论

1. 本 spec 正式冻结为**方案 B**：runtime、session、odds、订单快照、worker key 继续按 `(account_id, platform_type)` 隔离；不得回退到“同账号 JND 仅允许运行一种 JND 平台类型”的方案 A。
2. `operator-console-mobile-refactor` 与 `jnd-dw3-strategy-betting` 只能消费这个上游裁决，不得在各自 spec 内重新打开 A/B 选择。
3. 本 spec 固定默认值如下：
   - `verification_ttl_minutes = 30`
   - `verification_timeout_seconds = 90`
4. `/accounts/{id}/login` 只允许作为迁移期的 deprecated alias 指向 `POST /accounts/{account_id}/verify`：
   - 新前端、新测试、新脚本、新 spec 一律只允许使用 `/verify`
   - alias 只服务于存量调用迁移，不得新增任何首方调用依赖
   - 当仓库内首方调用迁移完成并经搜索确认无引用后，alias 必须进入删除阶段
5. 串并行顺序冻结如下：
   - `account-strategy-platform-decoupling` 必须先于 `operator-console-mobile-refactor` 合并
   - `jnd-dw3-strategy-betting` 中涉及 `frontend/src/pages/operator/StrategyForm.tsx`、`backend/app/api/strategies.py`、`backend/app/engine/adapters/jnd.py`、`backend/app/engine/manager.py` 的任务，必须在本 spec 释放写集后再开工
   - `multi-strategy-bet-timing-fix` 不得与本 spec 并行改写 `backend/app/api/accounts.py`、`backend/app/schemas/account.py`、`frontend/src/pages/operator/Accounts.tsx`、`frontend/src/api/accounts.ts`、`frontend/src/types/api/account.ts`
   - `lottery-countdown-display` 可并行复用既有状态/倒计时契约，但不得并行改写 `backend/app/engine/adapters/jnd.py` 中 `GetCurrentInstall?lotteryType=` 的状态语义
   - 在 `lottery-countdown-display` 正式复用共享执行语义前，必须先冻结 `Confirmbet` 零重试与 `backend/app/engine/executor.py` 现有“一次赔率变更重试”是统一规则还是刻意分层差异，避免 `jnd.py` 共享语义继续漂移

## 必须满足的结果

### R1. 账号绑定阶段不选平台，也不做平台能力探测

1. 绑定账号时，前后端都只填写 `game_type`、`platform_url`、`account_name`、`password`。
2. 绑定阶段不能默认把 JND 账号落成 `JND28WEB` 或 `JND282`。
3. 绑定阶段不通过远端平台探测“单类型/双类型”，这一步只负责保存账号静态信息和本地校验。

### R2. 验证阶段自动探测真实平台能力，并以批次快照提交结果

1. 每次点击“验证账号”都必须生成一个新的 `verification_run`。
2. 账号验证时只建立一次会员登录态，不允许为了 `JND28WEB` 和 `JND282` 各自再做一次正式登录。
3. JND 账号验证后，后端必须按候选 `platform_type` 逐个探测实际支持情况。
4. 平台能力探测必须使用带 `lotteryType` 的状态接口，不能使用 `heartbeat` 代替。
5. 探测结果必须明确区分：
   - 已支持
   - 明确不支持
   - 临时探测失败
   - 尚未验证
6. `allowed_strategy_platform_types` 只能来自“最新 completed 且未失效的验证批次”。
7. 正在运行、失败、取消、超时的验证批次，不能覆盖上一批已完成的有效结果。
8. 同一账号验证必须互斥；当已有验证在运行时，重复触发应返回“验证进行中”，而不是并发写入两批结果。
9. 同一账号任意时刻只能存在一个 `run_status=running` 的验证批次；该互斥必须通过数据库级强制约束落地，不能只停留在接口描述。
10. 同步验证接口必须定义明确的超时预算；本 spec 默认 `verification_timeout_seconds = 90`；当超时发生时，当前批次状态必须记为 `timed_out`，并返回统一的 `504 verification_timeout` 语义。

### R3. 验证结果必须有明确失效规则

1. 当账号的 `password`、`platform_url` 或 `game_type` 发生变化时，最近一次已完成验证结果必须立即失效。
2. 当验证结果超过配置的有效期后，必须转为失效状态，不能继续用于建策略或启动策略；本 spec 默认 `verification_ttl_minutes = 30`。
3. 已失效的验证批次不得再导出 `allowed_strategy_platform_types`。
4. 失效后，系统必须要求重新验证；在重新验证完成前，禁止新建策略，也禁止启动依赖该账号的平台策略。
5. 当验证结果失效时，已经在运行中的策略不立即强制停止；系统必须告警并标记“需重验”。但如果运行中的策略后续需要重新登录、重建会话或做受控重登，则必须拒绝恢复并要求先完成重新验证。

### R4. 赔率拉取必须建立在平台状态探测之后

1. 平台状态探测必须先得到当前 `market_state` 与期号。
2. 只有当 `market_state == open` 时，才允许拉取并同步该平台赔率。
3. 当平台状态为 `closed`、`waiting` 或 `unknown` 时，不能盲目拉赔率，而是要返回明确状态说明。
4. 双平台账号允许出现“一边有赔率，一边只有状态”的结果，前后端必须正确表达这种部分可用状态。

### R5. 策略可选平台必须来自验证结果，而不是静态映射

1. 创建策略时，后端校验必须基于 `effective_verification_run_id` 指向批次导出的 `allowed_strategy_platform_types`，不能只根据 `game_type` 静态推导。
2. 若账号只验证出单一平台，则策略页只有一个平台选项，并默认锁定为该值。
3. 若账号验证出双平台，则策略页允许操作者选择其中一个。
4. 若账号尚未验证出任何平台，则不能创建依赖该账号的平台策略。
5. 策略创建、编辑、启动、恢复门禁都必须面向 `effective_verification_run_id + 目标 platform_type` 的组合事实，不得退回到账号摘要状态、最新触发批次或静态候选平台。

### R6. 运行时必须继续按 `(account_id, platform_type)` 隔离

1. 同一个账号下的 `JND28WEB` 策略与 `JND282` 策略必须进入独立运行时上下文。
2. 每个运行时上下文都必须拥有自己的：
   - 期号轮询状态
   - 当前期执行计划
   - 赔率缓存
   - 会话状态
   - 告警上下文
3. worker 执行、风控、赔率读取、下单、结算、对账都必须只认 `strategy.platform_type`。

### R7. 会话、状态与风控必须区分“账号摘要”和“平台事实”

1. 账号页展示的 `status`、`balance`、`last_login_at` 只能作为摘要信息，不能再作为目标平台是否可运行的唯一事实源。
2. 平台会话状态必须能区分 `(account_id, platform_type)`。
3. 风控检查和策略启动检查必须面向 `effective_verification_run_id + 目标 platform_type`，而不是只看账号总状态。
4. 一个平台会话失效时，不能把另一平台运行时误判为掉线。

5. 账号接口必须明确区分并返回“最新触发批次”“当前有效批次”与 `verification_stale`，不得用单个模糊字段同时表示两种语义，也不得让前后端自行猜测 stale。

### R8. 赔率缓存与订单快照必须按平台隔离

1. `account_odds` 必须按 `(account_id, platform_type, key_code)` 隔离存储。
2. 网盘与 2.0 的赔率不能再共用同一份缓存。
3. `bet_orders` 必须记录下单时的 `actual_platform_type`。
4. 结算、对账、补偿、追查都必须优先使用订单快照，不得依赖当前账号配置或当前策略配置反推。

### R9. 前端模型必须按“验证结果”而不是“静态游戏类型”驱动

1. 账号页不再显示 JND 的 `JND28WEB/JND282` 绑定选项。
2. 账号页“验证账号”动作必须触发自动探测，而不是要求人工先选平台。
3. 创建策略页必须根据账号真实验证结果展示可选平台。
4. 前端不得再提交 `WEB` / `2.0` 这类纯展示值给后端。
5. 对双平台账号，账号列表可以隐藏具体平台名；但一旦出现部分可用，界面必须明确提示，而不能误导为“全可用”。
6. 账号摘要状态必须有统一判定规则，至少覆盖：
   - 未验证
   - 已验证
   - 部分可用
   - 验证失败
7. 当摘要状态为“验证失败”时，系统还必须返回次级说明，至少区分“验证失败-不支持”和“验证失败-探测异常”。

### R10. 当前阶段不做旧数据迁移，但旧链路仍必须删除

1. 当前属于测试阶段，不要求保留旧 JND 双账号样本，也不做历史数据合并迁移。
2. 实现时可以直接以新模型为准，不保留为旧数据兜底的长期兼容分支。
3. 以下旧链路必须进入删除清单：
   - 账号层默认平台绑定逻辑
   - `strategy.platform_type must match account.platform_type` 校验
   - manager 以账号为单位只启动一个固定平台 worker 的逻辑
   - `account_odds` 的单账号单缓存模型
   - 账号总状态作为唯一启动依据的逻辑
   - 前端 `WEB` / `2.0` 伪平台值

## 验收标准

### A1. 绑定与验证

1. 新绑定 JND 账号时，不再要求选择 `JND28WEB/JND282`。
2. 绑定完成后，账号不会被默认标记为某个平台类型。
3. 验证 JND 账号时，系统会自动探测 `JND28WEB` 与 `JND282` 的真实可用性。
4. 验证不会因为双平台探测而重复执行两次正式登录。
5. 验证接口按同步方式返回；同一账号重复点击验证时，系统会明确提示“验证进行中”。
6. 失败或超时的验证不会污染上一批已完成的有效结果。
7. 同一账号即使在多进程场景下，也不会并发创建两个 `running` 验证批次。
8. 验证超时时，当前批次会被记为 `timed_out`，并统一返回 `504 verification_timeout`。

### A2. 平台状态与赔率

1. 平台状态探测能返回 `open / closed / waiting / unknown` 之一。
2. `open` 平台会同步赔率。
3. `closed / waiting / unknown` 平台不会盲目同步赔率，而是返回清晰说明。
4. 双平台账号允许一个平台同步赔率成功，另一个平台只返回状态说明。

### A3. 策略创建与编辑

1. 单平台账号的策略平台选项只有一个，并默认锁定。
2. 双平台账号的策略平台选项有两个，由操作者选择。
3. 策略保存后，后续 worker、赔率、下注、结算都按该策略平台执行。
4. 不再出现 `platform_type must match account platform_type`。
5. 当验证结果失效后，账号不能继续用于新建或启动平台策略，直到重新验证完成。
6. 当验证结果失效时，已在运行中的策略不会被立即强停；但后续一旦需要重新登录或恢复运行，系统会要求先重新验证。
7. 策略创建、编辑、启动、恢复都只消费 `effective_verification_run_id` 指向批次与目标 `platform_type`，不会误用 latest run、账号摘要状态或静态候选平台。

### A4. 执行与结算

1. 同账号下网盘策略只走网盘运行时。
2. 同账号下 2.0 策略只走 2.0 运行时。
3. 同期执行时，两类策略不会互相污染期号、赔率、下单、结算、对账结果。

### A5. 会话与状态

1. `heartbeat` 仅用于已登录会话保活，不参与平台能力判定。
2. 一个平台会话失效时，不会把另一平台运行时误判为掉线。
3. 策略启动检查面向 `effective_verification_run_id + 目标平台`，不再只看账号总状态。
4. 账号摘要状态在前后端使用统一真值表，不会出现同一组平台结果显示成两种文案。

5. 账号接口会同时区分“最新触发验证批次”“当前有效验证批次”“verification_stale”，前后端不会再混用单个模糊字段。
6. 当摘要状态为“验证失败”时，前后端还能进一步区分“验证失败-不支持”和“验证失败-探测异常”。

### A6. 测试阶段收口

1. 当前实现不要求保留旧双账号历史数据。
2. 仓库内不再存在新的调用链继续依赖账号层默认平台或静态 `game_type -> allowed_strategy_platform_types` 旧假设。
3. 同步验证接口的并发、超时、失效、运行中策略处理规则都已在 spec 内写死，不再依赖实现时临时约定。
4. 仓库内首方调用与测试已切到 `/accounts/{account_id}/verify`；`/accounts/{id}/login` 只剩 deprecated alias 或已进入删除阶段。
5. 排期上 `account-strategy-platform-decoupling` 已先于 `operator-console-mobile-refactor` 和涉及 `StrategyForm.tsx / strategies.py / jnd.py / manager.py` 的 `jnd-dw3-strategy-betting` 开工；`multi-strategy-bet-timing-fix` 未并行改写共享账号链路文件。
6. 仓库搜索阀门或 CI 已阻断新增 `/accounts/{id}/login` 首方调用与新的静态 `game_type -> allowed_strategy_platform_types` 导出。
