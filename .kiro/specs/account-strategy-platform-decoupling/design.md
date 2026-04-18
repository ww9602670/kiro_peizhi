# 账号与策略平台类型彻底解耦设计

## 结论

当前问题不是单点表单 bug，而是旧平台绑定模型残留在以下 5 层：

1. 数据库：账号表把 `platform_type` 当绑定主轴。
2. 绑定接口：账号创建时按 `platform_type` 选择 adapter。
3. 策略接口：强制 `strategy.platform_type == account.platform_type`。
4. 运行时：manager/worker 只支持“一个账号一个固定平台上下文”。
5. 会话与赔率：都按账号单桶存储，没有平台隔离。

如果只修前端报错，不改底层，后续一定会继续出现：

- 策略能建不能跑
- 网盘/2.0 串赔率
- 网盘/2.0 串期号
- 结算规则拿错平台
- 同账号混跑时只按第一个策略的平台执行

因此必须做结构级改造，不能再补丁式修。

## 影响评估清单

### 1. 数据库与持久化

受影响对象：

- [backend/app/database.py](H:/d/bocai_web/backend/app/database.py)
- [backend/app/models/db_ops.py](H:/d/bocai_web/backend/app/models/db_ops.py)

现状问题：

1. `gambling_accounts.platform_type` 绑定了账号平台。
2. `UNIQUE(operator_id, account_name, platform_type)` 允许同一个 JND 账号拆成两条记录。
3. `account_odds` 只按 `account_id` 存赔率。
4. `bet_orders` 没有订单级平台快照。
5. `gambling_accounts.session_token` 是单账号单 token 模型。

设计结论：

必须扩展为“账号静态信息”和“平台运行态信息”分离。

### 2. 绑定与账号接口

受影响对象：

- [backend/app/schemas/account.py](H:/d/bocai_web/backend/app/schemas/account.py)
- [backend/app/api/accounts.py](H:/d/bocai_web/backend/app/api/accounts.py)
- [frontend/src/pages/operator/Accounts.tsx](H:/d/bocai_web/frontend/src/pages/operator/Accounts.tsx)
- [frontend/src/types/api/account.ts](H:/d/bocai_web/frontend/src/types/api/account.ts)

现状问题：

1. 账号 schema 仍要求 `platform_type`。
2. 绑定账号时直接根据 `platform_type` 创建 adapter。
3. 前端账号页仍把账号层和平台运行类型绑死在一起。

设计结论：

账号接口改为 `game_type + platform_url + account_name + password`。

### 3. 策略接口

受影响对象：

- [backend/app/schemas/strategy.py](H:/d/bocai_web/backend/app/schemas/strategy.py)
- [backend/app/api/strategies.py](H:/d/bocai_web/backend/app/api/strategies.py)
- [frontend/src/pages/operator/StrategyForm.tsx](H:/d/bocai_web/frontend/src/pages/operator/StrategyForm.tsx)
- [frontend/src/types/api/strategy.ts](H:/d/bocai_web/frontend/src/types/api/strategy.ts)

现状问题：

1. 后端存在 `_validate_platform_type_matches_account_or_raise()`。
2. 创建和更新策略时都把策略平台强制回写为账号平台。
3. 前端三字定位页面仍混用展示值 `WEB/2.0` 和接口值。

设计结论：

策略层保留 `platform_type`，但校验规则改为“是否属于账号 `game_type` 可用平台集合”。

### 4. 运行时调度与 worker

受影响对象：

- [backend/app/engine/manager.py](H:/d/bocai_web/backend/app/engine/manager.py)
- [backend/app/engine/worker.py](H:/d/bocai_web/backend/app/engine/worker.py)
- [backend/app/engine/poller.py](H:/d/bocai_web/backend/app/engine/poller.py)
- [backend/app/engine/executor.py](H:/d/bocai_web/backend/app/engine/executor.py)
- [backend/app/engine/settlement.py](H:/d/bocai_web/backend/app/engine/settlement.py)
- [backend/app/engine/reconciler.py](H:/d/bocai_web/backend/app/engine/reconciler.py)
- [backend/app/engine/risk.py](H:/d/bocai_web/backend/app/engine/risk.py)

现状问题：

1. manager 恢复 worker 时直接取第一条运行中策略的平台。
2. worker 内只有一个 `_platform_type`。
3. poller、executor、settlement、reconciler 都默认整个账号只有一种运行平台。

设计结论：

运行时必须从“账号 worker”升级为“账号-平台 worker”。

推荐键：

- `RuntimeKey = (account_id, platform_type)`

### 5. JND adapter

受影响对象：

- [backend/app/engine/adapters/factory.py](H:/d/bocai_web/backend/app/engine/adapters/factory.py)
- [backend/app/engine/adapters/jnd.py](H:/d/bocai_web/backend/app/engine/adapters/jnd.py)

现状问题：

1. adapter 创建时固定 `lottery_type`。
2. 运行时所有接口都用 `self.lottery_type`。
3. 当前 adapter 与 worker 绑定模式天然是“单平台上下文”。

设计结论：

JND adapter 保留“单实例单 `lottery_type`”是更安全的，不建议做一个 worker 内反复切换 `lottery_type` 的共享状态方案。

原因：

1. 期号、赔率、投注、结算都依赖 `lotteryType`。
2. 一个 worker 内动态切换会放大串状态风险。
3. 现有代码已经大量默认“worker 内部平台固定”。

## 目标模型

### 1. 账号静态模型

`gambling_accounts`

- `id`
- `operator_id`
- `account_name`
- `password`
- `game_type`
- `platform_url`
- `status` 仅保留聚合状态
- `balance`
- `kill_switch`

删除目标：

- `platform_type` 从账号主模型中移除
- `session_token`
- `login_fail_count`
- `last_login_at`
- `worker_lock_token`
- `worker_lock_ts`

这些迁移到平台运行态表。

### 2. 平台运行态模型

新增表：`account_platform_sessions`

建议字段：

- `id`
- `account_id`
- `platform_type`
- `session_token`
- `status`
- `login_fail_count`
- `last_login_at`
- `worker_lock_token`
- `worker_lock_ts`
- `created_at`
- `updated_at`

唯一键：

- `UNIQUE(account_id, platform_type)`

### 3. 策略模型

`strategies`

- 保留 `platform_type`
- 不再从账号强制覆盖
- `platform_type` 由用户选择并持久化

兼容关系：

- `JND28` -> `JND28WEB`, `JND282`
- `LUCKYSB` -> `LUCKYSB`

### 4. 赔率模型

升级 `account_odds`

新增字段：

- `platform_type`

唯一键改为：

- `UNIQUE(account_id, platform_type, key_code)`

这样网盘和 2.0 的赔率缓存彻底隔离。

### 5. 订单模型

升级 `bet_orders`

新增字段：

- `actual_platform_type`

作用：

1. 订单生成时记录真实执行平台。
2. 结算与对账优先使用订单快照。
3. 避免策略后续修改或账号迁移后反推错误。

## 运行时方案

## 推荐方案：每个平台一个独立 worker

### 为什么不用“单 worker 内切换 lotteryType”

这个方案看起来省代码，但风险高：

1. 同一个 worker 内要同时维护两套期号。
2. 同一个 worker 内要维护两套赔率缓存。
3. 同一个 worker 内要决定何时切换 adapter 的 `lottery_type`。
4. 一个遗漏就会把另一平台的单下到错误接口。
5. 结算、对账、重试窗口都会放大串平台风险。

因此不推荐。

### 推荐结构

一个账号下，按运行平台拆成多个 worker：

- `(account_id=12, platform_type=JND28WEB)`
- `(account_id=12, platform_type=JND282)`

每个 worker 拥有自己的：

- adapter
- session manager
- poller
- executor
- settlement processor
- reconciler
- risk controller

manager registry 也按复合键管理，而不是只按 `account_id`。

### manager 行为

1. 启动和恢复 worker 时，先把运行中策略按 `(account_id, platform_type)` 分组。
2. 每组策略各启动一个 worker。
3. 账号 kill switch 时，停止该账号名下的全部平台 worker。

### risk 行为

1. 风控检查从 `account_platform_sessions` 读取当前平台会话，而不是账号表单一 token。
2. 若当前 worker 平台会话失效，只阻断该平台策略。

### settlement / reconciler 行为

1. 优先读取订单 `actual_platform_type`。
2. 若历史老单没有该字段，过渡期允许回退到 `strategies.platform_type`。
3. 当历史单补齐完成后，删除回退逻辑。

## API 设计

## 账号接口

### `POST /accounts`

请求体改为：

- `account_name`
- `password`
- `game_type`
- `platform_url`

说明：

- 绑定阶段只做账号校验。
- 对 JND 来说，这一步不再固定为网盘或 2.0。

### `GET /accounts`

返回：

- `game_type`
- `platform_url`
- `status`
- `balance`
- `allowed_strategy_platform_types`

其中：

- `JND28` -> `[JND28WEB, JND282]`
- `LUCKYSB` -> `[LUCKYSB]`

## 策略接口

### `POST /strategies`

校验改为：

1. 账号存在。
2. 策略 `platform_type` 属于账号 `game_type` 可用集合。
3. 不再校验 `strategy.platform_type == account.platform_type`。

### `PUT /strategies/{id}`

同上，允许在停用状态下切换策略运行平台，但必须属于该账号允许集合。

## 赔率接口

以下接口必须显式接受 `platform_type`：

- `GET /accounts/{account_id}/odds`
- `POST /accounts/{account_id}/odds/confirm`
- `POST /accounts/{account_id}/odds/refresh`

否则无法判断取哪一套赔率。

## 数据迁移方案

## Phase 0: 审计与冻结

目标：

1. 找出所有 JND 重复账号。
2. 找出所有依赖账号层 `platform_type` 的接口、测试、脚本。
3. 在代码改造期间，禁止继续新增同站点同账号的 JND 双账号样本。

## Phase 1: 扩表，不切流

只做 schema 扩展，不改主行为：

1. `gambling_accounts` 新增 `game_type`
2. `account_platform_sessions` 新建
3. `account_odds` 新增 `platform_type`
4. `bet_orders` 新增 `actual_platform_type`

这一步要求线上逻辑仍可运行。

## Phase 2: 双写

新代码开始：

1. 账号写 `game_type`
2. 订单写 `actual_platform_type`
3. 赔率写 `(account_id, platform_type, key_code)`
4. 会话写 `account_platform_sessions`

过渡期间允许保留旧字段写入，但必须有明确删除计划。

## Phase 3: 旧 JND 双账号合并

合并规则建议：

1. 以 `(operator_id, account_name, normalized_platform_url, game_type=JND28)` 为一组。
2. 组选一个 canonical account：
   - 优先保留最早创建的账号
   - 若存在更多活跃策略，则保留活跃策略更多者
3. 把重复账号下的下列数据迁到 canonical account：
   - strategies.account_id
   - bet_orders.account_id
   - reconcile_records.account_id
   - account_odds.account_id
4. `strategies.platform_type` 必须保留原值，不能因为合并被改掉。

余额处理原则：

1. 不做求和。
2. 保留 canonical 账号余额。
3. 合并完成后触发一次余额与赔率人工刷新。

## Phase 4: 切运行时

1. manager registry 改为复合键。
2. restore/start/stop 全部改成按 `(account_id, platform_type)` 工作。
3. 风控、赔率、结算、对账切到新模型。

## Phase 5: 删旧链路

满足以下全部条件后删除：

1. 新 worker 模型已稳定。
2. 新 session/odds/order 快照已稳定。
3. 数据迁移完成。
4. 所有 fallback 计数为 0。
5. 相关测试与人工回归通过。

删除对象：

1. 账号层 `platform_type` 绑定
2. 旧 mismatch 校验函数
3. 账号单 token 风控链路
4. 单账号赔率缓存链路
5. manager “取第一条策略平台”逻辑
6. 前端 `WEB` / `2.0` 伪值

## 旧链路清理判定标准

只有在下面四项同时成立时，才允许删兼容代码：

1. 代码搜索确认无正式入口继续调用旧字段。
2. 迁移脚本跑完并输出成功报告。
3. fallback 命中统计在完整回归周期内为 0。
4. 关键场景人工验收通过：
   - 绑定 JND28 账号
   - 同账号建网盘策略
   - 同账号建 2.0 策略
   - 同账号双平台同时运行
   - 赔率刷新
   - 结算对账

## 需要清理或废弃的旧规格

现有 [requirements.md](H:/d/bocai_web/.kiro/specs/premium-dual-platform/requirements.md) 只有半成品需求，没有后续设计与任务拆解，不适合作为当前落地蓝图。

处理建议：

1. 当前新规格落地后，明确标记该旧规格为 superseded。
2. 不再按该旧规格继续补丁式实现。

## 测试策略

### 后端

至少新增或重写：

1. 账号 schema / API：`game_type` 与地址校验
2. 策略 API：JND 账号可创建两类策略
3. manager：按 `(account_id, platform_type)` 恢复 worker
4. worker：双平台不会串 `platform_type`
5. risk：按平台会话检查
6. odds：按平台存取
7. settlement：优先使用 `actual_platform_type`
8. migration：重复 JND 账号合并

### 前端

至少新增或重写：

1. 账号页只显示游戏类型
2. 创建策略页根据 `game_type` 展示平台选项
3. 三字定位不再提交 `WEB/2.0`
4. 同账号双平台策略创建与编辑
5. 赔率刷新按平台工作

## 关键风险与对应处理

### 风险 1：旧数据重复账号合并出错

处理：

1. 迁移前导出映射表。
2. 合并脚本必须支持 dry-run。
3. 每次只处理一批账号并产出报告。

### 风险 2：双平台 worker 导致会话相互踢下线

处理：

1. 第一版就把会话状态按平台隔离。
2. 加入会话冲突监控。
3. 若平台侧确认单 token 共用更稳，再评估是否把两平台 worker 改为共享登录态，而不是第一版就做共享。

### 风险 3：旧回退长期残留

处理：

1. 所有 fallback 代码必须带埋点和日志。
2. tasks 中明确设置删除 Gate。
3. 删除 Gate 不通过，不允许收口。
