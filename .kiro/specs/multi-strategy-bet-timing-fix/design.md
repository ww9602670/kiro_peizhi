# 多策略独立下注时机修复设计文档

## 1. 设计目标

本设计用于修复同账号多策略在运行时未真正按各自 `bet_timing` 独立执行的问题，并补齐以下能力：

1. 普通玩法保存时自动规避同方向下注时机冲突。
2. 单账号单 worker 在单期内按多个目标时机分批执行。
3. 平台会话失效、异地登录后的受控恢复。
4. 账号登录/绑定/赔率刷新场景中的结构化提示。
5. 在共享调度层修复过程中，严格保护 DW3 已实现的核心语义。

## 1.1 上游契约依赖

- 本 spec 没有独立 `requirements.md`，本节作为需求等价约束；在账号绑定、账号验证、平台可用范围、策略平台门禁、账号摘要状态等共享语义上，必须以上游 `account-strategy-platform-decoupling` 为唯一事实源。
- 当前发布窗口的冻结基线维护者为 `Codex`（本分支上游实现 owner）；若调度修复与共享文件实现出现冲突，先回到上游契约对齐，不得在本 spec 内重开 `game_type -> platform`、`/accounts/{id}/login` 主契约、或静态平台推导。
- 上游查阅入口：
  - `.kiro/specs/account-strategy-platform-decoupling/requirements.md`
  - `.kiro/specs/account-strategy-platform-decoupling/design.md`
  - `.kiro/specs/account-strategy-platform-decoupling/tasks.md`
  - `backend/app/api/accounts.py`
  - `backend/app/api/strategies.py`
  - `backend/app/engine/manager.py`
  - `frontend/src/pages/operator/Accounts.tsx`
  - `frontend/src/pages/operator/StrategyForm.tsx`
  - `frontend/src/api/accounts.ts`
  - `frontend/src/types/api/account.ts`
- 若本 spec 发现契约缺口、恢复语义冲突或共享写集冲突，必须先在上游 `account-strategy-platform-decoupling` 补决议或补实现，再由本 spec 跟随消费，不得本地分叉账号/平台语义。

## 2. 非目标

以下内容不属于本设计：

1. 不重写任何策略的选号或信号算法。
2. 不做同方向金额合并或合单下注。
3. 不改下注协议与 `executor.py`。
4. 不改结算、对账、风控逻辑。
5. 不增加数据库字段。
6. 不借本轮需求修改 DW3 16 组、阀门、one-shot、赔率或结算方案。

## 3. DW3 兼容契约

### 3.1 契约结论

DW3 会受到本轮修复影响，但只允许受以下影响：

1. 保存时的基础 `bet_timing` 合法性校验。
2. 运行时按每策略 `bet_timing` 分批执行。
3. 会话失效与账号可用性状态对其外层调度的影响。

DW3 不允许受以下影响：

1. 16 组表达方式。
2. `gate_window_issues` 阀门判定。
3. `DW3_xxx` 展开与金额叠加。
4. `settingCode=DW3` 赔率路径。
5. one-shot payload 结构。
6. `getBetChecked` 结算依据。
7. `Result` 原值入库。

### 3.2 本轮 DW3 冲突策略

本设计默认采用执行方案 A：

1. DW3 不参加普通玩法“保存时自动错峰分配”。
2. DW3 只参加“运行时按自身 `bet_timing` 独立调度”。
3. 若后续业务要求 DW3 也参与同方向冲突规避，必须新开设计，按 DW3 展开后的具体号码覆盖集合定义冲突，而不是按组 token 文本交集拍脑袋判断。

原因：

1. 当前修复目标是共享调度层，不是 DW3 语义扩展。
2. DW3 组 token 与真实展开集合并非一一等价于普通玩法的 `play_code` key。
3. 把普通玩法规则直接套到 DW3 上，存在“误判冲突”或“漏判冲突”的风险。

## 4. 总体方案

### 4.1 保存链路

普通玩法策略在 `create/update/start` 前进入统一的时机分配器：

1. 解析新策略的方向 key。
2. 读取同账号运行中策略。
3. 仅对方向 key 存在交集的普通玩法策略做冲突检查。
4. 以用户给定 `bet_timing` 为起点，搜索合法时机。
5. 若找到合适时机，则回写实际保存值；否则拒绝保存。

DW3 的保存链路只做：

1. 基础字段合法性校验。
2. 保留用户传入的 `bet_timing`。
3. 不参加普通玩法自动错峰。

### 4.2 运行时调度链路

`EngineManager` 构建 worker 时，传递策略运行快照列表，每条快照至少包括：

1. `strategy_id`
2. `bet_timing`
3. `strategy_type`
4. `play_code`
5. `required_history_issues`
6. 其他 worker 采集和执行所需的只读元数据

`AccountWorker` 在单期内执行时：

1. 先构建本期 `IssueExecutionPlan`
2. 计划中按 `bet_timing` 生成多个 `TimingGroup`
3. 每个 `TimingGroup` 仅包含该时机应执行的策略集合
4. 到达目标时机后，仅对当前 group 调用 `_collect_signals(strategy_ids=...)`
5. 当前 group 执行完成后，把策略 id 放入本期已执行集合
6. 下一 group 继续执行，且不得重复触发前一 group 的策略

### 4.3 会话恢复链路

当 `GetCurrentInstall` 或同类安装信息接口返回：

1. `State=-2`
2. `Msg` 包含会话超时、异地登录等字样
3. 响应结构缺少 `Installments`

则适配器应把其归类为“会话失效”而不是普通解析失败。

worker 侧处理：

1. 捕获会话失效异常
2. 进入 `SessionManager` 受控重登流程
3. 若重登成功，则恢复主循环
4. 若连续失败达到阈值，则停止继续下注轮询，并发出可读告警

### 4.4 账号登录与赔率提示链路

账号绑定/登录/刷新赔率接口应统一返回结构化结果：

1. `odds_synced`
2. `odds_count`
3. `odds_message`
4. 可选的当前平台状态摘要

前端不再根据零散字符串猜状态，而是基于结构化字段显示：

1. 已登录且赔率已同步
2. 已登录但当前封盘，赔率未同步
3. 已绑定但尚未登录获取赔率

## 5. Interfaces

### 5.1 后端接口边界

建议保留并强化以下边界：

1. `backend/app/api/strategies.py`
   - 负责保存前时机分配调用
   - 负责把“保存后实际 `bet_timing`”回显给前端
2. `backend/app/engine/manager.py`
   - 负责把按策略展开的运行时 profile 传给 worker
3. `backend/app/engine/worker.py`
   - 负责单期多时机执行计划和分批执行
4. `backend/app/engine/session.py`
   - 负责心跳、重连、重登生命周期
5. `backend/app/engine/adapters/jnd.py`
   - 仅负责把平台响应正确分类为会话失效或普通失败

### 5.2 新增辅助模块

建议新增一个专用 helper，例如：

```text
backend/app/utils/strategy_timing.py
```

如果复用现有模块，则其职责必须明确为：

1. 解析普通玩法方向 key
2. 计算同方向策略冲突
3. 搜索可用 `bet_timing`
4. 返回结构化分配结果与失败原因

该模块默认不实现 DW3 的真实覆盖集合冲突推导。

### 5.3 前端接口边界

允许最小范围改动：

1. `Accounts.tsx`
2. `frontend/src/api/accounts.ts`
3. `frontend/src/types/api/account.ts`
4. `frontend/src/types/api/odds.ts`

前端职责仅限：

1. 展示后端返回的实际保存值或错误原因
2. 展示会话失效/重登失败告警的可读信息
3. 展示赔率同步状态提示

前端不承担时机分配或冲突计算逻辑。

## 6. Data Model

本轮不引入数据库 schema 变更。

现有字段使用原则：

1. `strategies.bet_timing` 继续作为唯一时机字段。
2. DW3 的 `play_code` 继续存组 token，不展开为 1000 项。
3. DW3 的 `gate_window_issues` 继续独立存储。
4. 账号登录/赔率同步状态如需返回额外信息，优先作为接口响应字段，不落新库字段。

## 7. 核心算法设计

### 7.1 普通玩法时机搜索

输入：

1. 用户请求的 `bet_timing`
2. 同账号运行中普通玩法策略列表
3. 最小间隔常量
4. 合法执行窗口上下界

输出：

1. `resolved_bet_timing`
2. `adjusted`
3. `conflict_strategy_ids`
4. `reason`

搜索原则：

1. 先验证请求值是否已合法且无冲突。
2. 若冲突，则按“距离原值最小”原则向两侧搜索。
3. 只接受落在执行窗口内且与所有冲突对象满足最小间隔的值。
4. 找不到合法值则失败。

### 7.2 worker 分批执行

单期执行计划中，每个 `TimingGroup` 至少包含：

1. `bet_timing`
2. `strategy_ids`
3. `remaining_seconds_target`

执行顺序：

1. 按 `bet_timing` 从大到小排序
2. 等待到对应窗口
3. 仅采集 group 内策略信号
4. 仅执行 group 内策略
5. 标记已执行策略
6. 进入下一个 group

### 7.3 DW3 历史期数要求保护

worker 的 group 级采集必须继续支持：

1. 按 group 内策略所需历史期数拉取足够开奖数据
2. 不因为同期多批次执行而减少 DW3 阀门需要的历史数据
3. 阀门跳过时只产生“跳过”语义，不推进下注状态机

## 8. Error Handling

### 8.1 保存时机分配失败

返回结构化错误，至少包含：

1. 失败原因
2. 冲突策略 id 列表
3. 用户输入值
4. 合法窗口约束说明

### 8.2 会话失效

系统必须区分：

1. 普通 API 调用失败
2. 平台会话失效
3. 重登失败达到阈值

告警文案应包含：

1. 投注平台账号名
2. 失败原因
3. 当前处理动作
4. 建议人工动作

### 8.3 赔率未同步

接口层必须能表达：

1. 登录成功且赔率已同步
2. 登录成功但当前封盘，赔率未同步
3. 赔率全 0，未写入

## 9. Configuration

沿用现有常量并统一从公共 helper 暴露：

1. `BET_TIMING_MIN`
2. `SAME_DIRECTION_MIN_GAP`
3. 会话重登最大重试次数
4. 会话失效与普通失败的判定关键字

本轮不新增面向业务配置的数据库参数。

## 10. Test Strategy

### 10.1 普通玩法

1. 时机分配单元测试
2. worker 分批执行测试
3. API 保存/更新/启动链路测试

### 10.2 DW3 回归

1. 单 DW3 策略执行不变
2. DW3 阀门跳过不推进 martingale
3. DW3 历史期数仍正确拉取
4. 方案 A 下，DW3 不被普通玩法自动错峰逻辑误处理
5. one-shot、赔率、结算相关 targeted tests 保持通过

### 10.3 会话与提示

1. 会话失效分类测试
2. 重登成功/失败路径测试
3. 账号登录与赔率提示接口测试
4. 前端展示测试

## 11. 明确禁止项

1. 不得修改 `backend/app/engine/executor.py`
2. 不得修改 `backend/app/engine/strategies/dw3.py`
3. 不得修改 `backend/app/engine/dw3_plan.py`
4. 不得修改 `backend/app/engine/dw3_signal_state.py`
5. 不得改 DW3 `settingCode=DW3` 路径
6. 不得把 DW3 冲突判定偷偷降级为组 token 文本交集并直接上线
7. 不得在未升级设计的前提下把 DW3 纳入自动错峰分配
8. 不得通过新增数据库字段来保存“中间状态补丁”
