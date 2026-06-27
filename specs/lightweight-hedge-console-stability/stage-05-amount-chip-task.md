# 阶段 5：计划金额和筹码规则修正独立任务编排

## 1. 审核结论：通过

结论：通过，允许进入执行，但只能按本文档做最小实现和补测试。

阶段 5 不允许重写轻量运行链路。当前代码已经具备 4 元筹码、每账号 5 步内拆分、3 号/4 号计划、低余额剔除后重排、最终计划账号下发执行等核心能力。本阶段执行重点是补齐限红/单号范围约束、补强无法拆分提前跳过和 20 轮副号不规律测试，并修正发现的最小缺口。

当前审核未修改运行代码。审核基线测试：`python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q`，结果 `59 passed`。

## 2. 最新 exe 相关源码复查结论

复查结论：已有部分能力，只补缺口。

最新打包入口和实际源码路径：

- `LightweightHedgeConsole.spec` 使用入口 `bet_desktop\ui\run_lightweight_dashboard.py`。
- `run_lightweight_dashboard.py` 实际加载 `LightweightController`、`LightweightDashboard`、`LightweightProbeAdapter`。
- 最新 exe 路径：
  - `dist\LightweightHedgeConsole\LightweightHedgeConsole.exe`
  - `release-candidates\LightweightHedgeConsole_20260626_three_account_gate_fix\LightweightHedgeConsole\LightweightHedgeConsole.exe`
- 两个 exe 本次复查修改时间均为 `2026/6/26 16:19:01`，大小均为 `6622565`。

源码路径判断：

- 持续轻量对冲计划由 `bet_desktop/ui/lightweight_probe_adapter.py` 的 `_build_plan()` 生成。
- 控制器把高级配置的 `amount_min`、`amount_max`、`min_balance_yuan` 传入 adapter。
- 真实点击仍由 `scripts/live_interval_acceptance_probe.py` 的 `execute_round()` 和 `execute_fast_click_with_trusted_preflight()` 执行。
- 最新 exe 不是旧 UI worker 重事件流入口，阶段 5 不得把旧 UI worker 计划逻辑搬回轻量 UI。

## 3. 阶段 5 独立目标

目标：确保下一局计划金额只使用平台可点击筹码组合，主号金额受高级设置和房间/单号限制约束，副号金额在合法范围内更不规律，且最终执行器只收到通过剔除、余额、筹码、房间/限红范围检查后的最终账号。

阶段 5 只处理计划金额和筹码规则，不处理 UI 排版、登录、进房、局号停更、自动回房、资源诊断或打包。

## 4. 已有能力清单

- 4 元筹码已有：`scripts/live_interval_acceptance_probe.py` 的 `DENOMINATIONS = (4, 10, 20, 50, 100, 200)`，adapter 的 `STABLE_BET_DENOMINATIONS` 直接复用。
- 每账号不超过 5 步已有：`decompose_value(..., max_steps=5)` 和 adapter 的 `_stable_chip_sequence()` 已限制。
- 无法拆分金额不会进入计划已有基础：`_build_plan()` 会先枚举 `amount_min` 到 `amount_max`，只保留可拆分 `valid_amounts`。
- 副号金额不规律已有基础：adapter 的 `_split_stable_sub_amounts()` 会生成非完全平均组合，并按轮次 seed 变化。
- 3 号/4 号模式已有：阶段 4 后，剔除一个账号时仍能生成 1 主 2 副；4 个账号时生成 1 主 3 副。
- 低余额剔除后计划仍合法已有基础：主号余额不足会继承，副号余额不足会剔除并重排；少于 3 个账号时停止。
- 执行器只收最终账号已有：`_run_one_hedge_round()` 只用 `planned_ids` 构造 `planned_accounts`、`planned_statuses` 并传给 `execute_round()`。
- 点击前检查仍在：`execute_fast_click_with_trusted_preflight()` 保留 trusted status、可下注、倒计时、局号、坐标检查。

## 5. 缺口清单

- 缺口 1：轻量计划生成目前没有明确执行房间限红/单号范围过滤；阶段 5 必须补规则或在发现无可靠限红来源时暂停并记录为不能执行。
- 缺口 2：现有测试缺少“生成计划金额不突破房间限红和单号金额范围”的测试门。
- 缺口 3：副号不规律测试只覆盖 6 轮，未达到需求中的最近 20 个完整计划验收口径。
- 缺口 4：缺少显式测试证明金额区间里遇到不可拆分金额会在计划阶段跳过，选择下一个合法金额，不能等到点击阶段失败。
- 缺口 5：`scripts/live_interval_acceptance_probe.py` 自身的独立 `build_plan()` 是固定序列，不能作为轻量 exe 的持续计划依据；执行 agent 不得误以为需要在这里重写持续计划。若只为直接 acceptance probe 补一致性测试，必须保持执行器接口不变。
- 缺口 6：限红/单号范围如果需要从状态中取得，只允许复用现有已采集字段或透传已有 snapshot/summary 字段；不得新增采集频率、页面扫描或坐标/状态识别逻辑。

## 6. 允许修改文件范围

优先允许：

- `bet_desktop/tests/test_lightweight_hedge.py`：补阶段 5 测试。
- `bet_desktop/ui/lightweight_probe_adapter.py`：仅限计划金额候选、筹码合法性、副号拆分选择、限红/单号范围过滤、计划 payload 的最小字段补充。

条件允许：

- `bet_desktop/ui/lightweight_models.py`：仅当需要保存已有高级参数或轻量配置字段，且不改变默认运行语义。
- `bet_desktop/ui/lightweight_controller.py`：仅当需要把已有配置或计划结果透传给 adapter/UI，不得新增后台刷新。
- `scripts/live_interval_acceptance_probe.py`：仅允许复用现有字段、补最小 helper 或保持 direct probe 与 adapter 测试一致；不得修改真实点击执行器行为。

## 7. 禁止修改文件范围

阶段 5 禁止修改：

- 坐标识别、坐标校准、筹码区域定位和下注区域定位。
- `execute_fast_click_with_trusted_preflight()` 的点击前安全检查语义。
- `execute_round()` 的真实点击下发语义，除非只是保持入参结构不变的测试适配。
- 浏览器状态采集主路径、WebSocket/Canvas/前端状态探针频率。
- 无头接管、启动浏览器、进房、自动回房、重启浏览器链路。
- 旧 UI worker 重事件流、旧 UI orchestrator 主逻辑。
- 主界面后台刷新、资源监控、日志流和高频轮询配置。

如果执行中发现必须修改以上范围，阶段 5 立即停止，输出风险说明，不能继续写代码。

## 8. 最小实现任务列表

- [x] T5.0 执行前复查最新 exe 入口、adapter、controller、models、probe、测试文件，输出结论：`已有部分能力，只补缺口`。
- [x] T5.1 补测试确认 4 元筹码可用，金额 `4/8/14/24/84` 等都能拆出合法筹码，单账号 `chips` 长度 `<= 5`。
- [x] T5.2 补测试确认主号金额区间只选择可拆分金额；不可拆分金额在计划生成阶段提前跳过或导致明确 `skipped`，不得进入点击阶段。
- [x] T5.3 补测试覆盖 4 号模式最近 20 个完整计划：同一副号至少出现 2 种合法金额组合，除非余额或配置只允许一种。
- [x] T5.4 补测试覆盖 3 号模式最近 20 个完整计划：副号金额合法、可拆、步数不超过 5，且不长期固定。
- [x] T5.5 补或调整规则，确保副号扰动不突破单号金额范围、余额限制和筹码可拆分限制。
- [x] T5.6 补测试确认低余额剔除后，剩余 3 号仍生成合法计划，主号继承和副号金额都满足筹码规则。
- [x] T5.7 补测试确认高级参数 `amount_min/amount_max` 修改后，新计划立即使用新区间。
- [x] T5.8 补限红/单号范围解析或过滤的最小实现：只读取已有 status 字段，例如 `limit_label`、`user_min_bet_cents`、`user_max_bet_cents`、`table_min`、`table_max` 或已有 summary 透传字段。
- [x] T5.9 补测试确认房间限红/单号范围不被突破；如果多个账号限红不一致，必须跳过本轮或只接受同一房间上下文内一致且覆盖计划金额的账号。
- [x] T5.10 补测试确认计划事件和执行事件的账号集合只包含最终账号，不包含已剔除账号。
- [x] T5.11 运行必跑测试门，确认未新增后台刷新、资源监控或旧 worker 事件流。

## 9. 验收阀门

阶段 5 通过条件：

- 所有计划腿的金额都能拆成 `4/10/20/50/100/200` 中的筹码。
- 每个账号单轮点击步数 `<= 5`。
- 金额无法拆分时，计划阶段提前跳过或明确停止，不把不可点击金额传给真实点击执行器。
- 4 号模式和 3 号模式都能生成合法计划。
- 低余额剔除后，剩余 3 号计划仍合法；少于 3 号时停止并给出明确原因。
- 副号金额在最近 20 个完整计划内不长期固定。
- 高级设置修改后，下一局计划使用新金额范围。
- 计划金额不突破房间限红和单号金额范围；没有可靠限红数据时不得假装已校验。
- 执行器只收到最终计划账号。
- 不新增后台刷新、前端高频刷新、常驻资源监控、旧 UI worker 重事件流。

## 10. 必跑测试门

必须运行：

- `python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q`

必须覆盖或新增/调整以下测试：

- `test_probe_plan_uses_configured_amount_and_chinese_side`
- `test_probe_plan_supports_four_yuan_chip_sequences`
- `test_probe_plan_sub_amounts_are_irregular_but_clickable`
- `test_probe_plan_three_account_sub_amounts_are_irregular`
- `test_interval_probe_uses_trusted_preflight_fast_click_executor`
- 新增：`test_probe_plan_skips_undecomposable_amounts_before_execution`
- 新增：`test_probe_plan_twenty_round_sub_amounts_are_irregular_in_four_account_mode`
- 新增：`test_probe_plan_twenty_round_sub_amounts_are_irregular_in_three_account_mode`
- 新增：`test_probe_plan_respects_single_account_amount_range_and_room_limit`
- 新增：`test_probe_plan_respects_room_limit_after_low_balance_exclusion`
- 新增或调整：`test_probe_round_executes_only_planned_accounts_when_one_is_excluded`

测试输出只汇报命令、通过/失败数量、失败测试名和最小失败原因。

## 11. 回退点和中文 commit 建议

回退点：

- 执行前记录当前分支、`git status --short`、`git diff --name-only`。
- 阶段 5 只允许一个独立提交。
- 若测试失败且无法在允许范围内修复，回退阶段 5 修改文件，保留审核文档和失败摘要。

中文 commit 建议：

- `修正轻量对冲计划金额和筹码规则`

提交前必须确认：

- 只包含阶段 5 允许范围内文件。
- 没有打包产物、日志、缓存、浏览器 profile 混入提交。
- 没有修改坐标、下注前检查、真实点击执行器、状态采集主路径或无头接管链路。

## 12. 对执行 agent 的明确约束

- 执行 agent 不能是本审核 agent。
- 不要重复开发已有 4 元筹码拆分、3 号降级、最终账号下发能力；已有能力优先回归验证。
- 不得为限红校验新增采集频率、页面扫描或后台刷新。
- 不得为了副号不规律引入随机不可复现导致测试不稳定；扰动应可由轮次或稳定 seed 推导。
- 不得把不可拆分金额传给 `execute_round()`。
- 不得让已剔除、恢复失败、低余额、不满足限红或不满足单号范围的账号进入 `planned_accounts`。
- 不得改变点击前检查、真实点击坐标、筹码点击顺序执行语义。
- 不得修改当前已验证的余额、局号、状态机、倒计时、房间号、坐标定位和无头接管主路径。
- 如果限红字段在轻量 status 中不存在，最多允许透传已经由现有 snapshot/summary 产生的字段；如果需要新增采集逻辑，立即停止并报告“涉及保护链路，暂停并请求确认”。
- 阶段完成记录必须写明：变更文件、最新 exe 相关源码复查结论、是否触碰保护链路、自动测试、人工未验证项、风险结论、下一步。

## 13. 是否允许进入执行

允许进入执行。

进入执行的前提是执行 agent 严格按本文档补测试和最小修正；如果实现限红/单号范围时触及状态采集、限红识别或坐标/点击链路，必须立即暂停，不能继续。

## 14. 阶段 5 完成记录

- 完成时间：2026-06-27
- 审核 agent 结论：通过，允许执行。
- 执行 agent 结论：完成阶段 5 最小修复。
- 变更文件：
  - `bet_desktop/ui/lightweight_probe_adapter.py`
  - `bet_desktop/tests/test_lightweight_hedge.py`
  - `specs/lightweight-hedge-console-stability/stage-05-amount-chip-task.md`
  - `specs/lightweight-hedge-console-stability/tasks.md`
- 最新 exe 相关源码复查结论：已有部分能力，只补缺口。
- 是否触碰保护链路：未修改余额、局号、状态机、倒计时、房间号采集、坐标定位、下注前检查或真实点击执行器。
- 自动测试：
  - `python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q`
  - 结果：66 passed。
- 人工验证：未执行真实下注和 exe 人工验证。
- 未验证项：真实平台限红字段是否稳定出现，仍需人工运行观察；若没有可靠字段，本阶段不会造数据强行校验。
- 风险结论：计划阶段会复用已有状态字段过滤金额；无字段时只保留余额和筹码可拆分约束，不新增采集。
- commit hash：以最终回复和 `git log -1 --oneline` 为准。
