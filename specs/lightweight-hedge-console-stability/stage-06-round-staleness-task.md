# 阶段 6：局号停更、数据过期诊断与隔离审核任务

## 1. 审核结论

结论：通过，允许进入阶段 6 执行，但只允许执行本文限定的轻量诊断、标记和隔离任务。

阶段 6 是高风险阶段。任何默认实现都不得直接修改状态采集、局号采集、倒计时、坐标、预检和真实点击链路。执行 agent 开始前必须再次说明风险，并确认本次只做最小改动：识别局号停更、标记数据异常、把异常账号排除出发号判断、防止一个过期账号阻塞剩余账号。

不允许把阶段 6 做成恢复工程。不允许新增持续高频扫描、canvas 探针、强制刷新/重启、后台资源采集循环。恢复策略只能作为阶段 8 或后续单独高风险任务讨论。

## 2. 最新 exe、入口和核心路径复核结论

最新已打包 exe 仍使用轻量控制台路径：

- `LightweightHedgeConsole.spec`：`Analysis(['bet_desktop\\ui\\run_lightweight_dashboard.py'])`，并打包 `bet_desktop.ui.lightweight_probe_adapter`、`scripts.live_interval_acceptance_probe`、`scripts.live_fast_click_probe`、`bet_desktop.browser.live_runtime_state`。
- `dist/LightweightHedgeConsole/LightweightHedgeConsole.exe`：修改时间 `2026-06-26 16:19:01`，大小 `6622565`。
- `release-candidates/LightweightHedgeConsole_20260626_three_account_gate_fix/LightweightHedgeConsole/LightweightHedgeConsole.exe`：修改时间 `2026-06-26 16:19:01`，大小 `6622565`。

入口和核心路径：

- `bet_desktop/ui/run_lightweight_dashboard.py`：启动 `LightweightController`、`LightweightDashboard`、`LightweightProbeAdapter`。
- `bet_desktop/ui/lightweight_controller.py`：UI 状态汇总、数据过期展示、低频自动回房触发。
- `bet_desktop/ui/lightweight_probe_adapter.py`：轻量发号等待、计划生成、账号剔除、发号前二次检查。
- `scripts/live_interval_acceptance_probe.py`：状态读取、同房同局判断、低频 headless 回房。
- `bet_desktop/browser/live_runtime_state.py`：从页面运行时读取局号、房间、状态、倒计时等。
- `bet_desktop/tests/test_lightweight_hedge.py`：当前轻量路径的主要测试覆盖。

复核结论：阶段 6 不应重复开发入口、打包、倒计时、已有低频自动回房或人工剔除能力；应在现有轻量发号/展示路径上补“局号本身停更”的诊断和隔离。

## 3. “在房但局号不更新”的可能原因

以下是基于代码证据的可能原因，不能在执行前直接猜测成结论：

1. 整条状态已经过期，但 UI 仍保留旧房间和旧局号展示。`LightweightController` 当前用 `STATE_STALE_MS = 15_000` 判断状态快照过期，过期后显示“数据过期”并关闭 `betting_open`。
2. 状态快照仍在刷新，但局号字段没有变化。现有过期判断看的是快照时间戳，不是“局号连续多少时间未变化”，因此“状态新、局号旧”的情况可能不会被单独标记。
3. 局号来源可能来自多个字段，存在来源不一致风险。`status_from_snapshot` 会从 `canvas_game_no`、运行时 snapshot、guard、frontend batch 中选择展示局号，并保留 `raw_*` 字段；需要诊断停更时到底是哪一个来源旧、空或不一致。
4. 页面运行时模型可能仍在房间，但 `scene.gameNo` 或 `liveModel.gameNo` 未更新。`live_runtime_state.py` 从 scene/liveModel 读取 `game_no`、`action`、`timed`、`currentLoadType`；如果平台对象未更新，局号可能停在旧值。
5. UI 可能把“有房间证据”的页面判为在房。`status_from_snapshot` 里 `room_evidence` 加上局号或坐标可形成 `effective_game_ready`，所以要区分真实在房、回大厅但残留房间证据、页面切换未完成三种状态。
6. 发号等待要求参与账号同房、同局、倒计时满足，并且 `round_key != last_round`。如果某账号在旧局号，可能导致 `round_mismatch` 或持续等不到新轮，从而阻塞剩余账号。
7. 后 4 位差异不应导致同局失败。`public_game_no()` 明确去掉第三个 `-` 后的账号差异后缀；阶段 6 只需补测试防回归，不应改写该规则。
8. 已有低频恢复只针对回大厅场景。`retry_headless_room_entries` 对 headless 且 hall_ready 的账号有 12 秒冷却；`LightweightController` 自动回房有 30 秒冷却。它们不是局号停更默认修复手段。

阶段 6 的诊断必须输出最小证据：账号、房间、展示局号、原始局号来源、局号最后变化时间、状态快照年龄、是否在房、是否可下注、是否已从发号池隔离。

## 4. 阶段 6 独立目标

目标：查清并可解释“账号明明在房间里但局号不更新”的具体表现，同时让局号停更账号不会阻塞剩余可用账号继续运行。

默认交付只包括：

- 标记局号停更或数据过期。
- 在 UI 和诊断事件中显示原因。
- 将停更账号从同局、可下注、计划和发号判断中隔离。
- 保持剩余 3 个账号可继续发号。
- 保持轻量，不增加额外后台高频刷新。

## 5. 已有能力

- UI 已有整条状态过期判断：超过 15 秒显示“数据过期”，并关闭可下注展示。
- 局号展示和同局判断已有后缀裁剪规则，后 4 位不参与同局判断。
- 发号等待已有同房、同局、倒计时、可下注和发号前二次检查。
- 计划生成已有人工剔除、余额不足剔除、3 号降级、恢复失败不阻塞等能力。
- 已有低频回大厅自动进房链路，但只应作为后续可选恢复策略审计对象。
- 当前测试已有状态过期、同房同局、剔除账号不阻塞、低频回房冷却、canvas 探针关闭等覆盖。

## 6. 缺口

- 缺少“状态仍刷新但局号长期不变”的独立新鲜度判断。
- 缺少局号停更账号的明确隔离原因，例如 `局号停更`、`数据过期`、`局号来源过期`。
- 缺少停更诊断事件，无法快速判断停更来自 runtime、guard、frontend 还是 canvas/展示来源。
- 缺少“局号停更账号不阻塞剩余 3 号”的专门测试。
- 缺少“局号停更不会触发刷新、重进房、重启或高频探针”的专门测试。
- 缺少“后 4 位差异不参与同局判断”的阶段 6 防回归测试。

## 7. 允许修改文件范围

仅允许最小修改以下文件：

- `bet_desktop/ui/lightweight_probe_adapter.py`
  - 增加轻量的局号新鲜度状态记录。
  - 在计划/发号池中过滤局号停更账号。
  - 输出最小诊断字段。
- `bet_desktop/ui/lightweight_controller.py`
  - 接收并展示 `局号停更` 或 `数据过期` 原因。
  - 不改倒计时衰减和采集来源。
- `bet_desktop/tests/test_lightweight_hedge.py`
  - 增加阶段 6 验收测试。
- `specs/lightweight-hedge-console-stability/stage-06-round-staleness-task.md`
  - 记录执行结果和验收结论。

如执行中发现必须修改其他文件，必须停止并回报原因，不得自行扩大范围。

## 8. 禁止修改文件范围

阶段 6 默认禁止修改：

- `bet_desktop/browser/live_runtime_state.py`
- `scripts/live_interval_acceptance_probe.py`
- `scripts/live_fast_click_probe.py`
- 坐标识别、筹码坐标、下注区域坐标相关文件。
- 点击前检查、真实点击执行器、下注确认等待相关文件。
- `LightweightHedgeConsole.spec`
- `bet_desktop/ui/run_lightweight_dashboard.py`
- 打包输出目录 `dist/`、`release-candidates/`

除非用户在风险说明后再次明确要求，否则不得修改状态采集、局号采集、倒计时、坐标、预检、真实点击链路。

## 9. 最小实施任务

- [x] T6.1 只读复核最新 exe、入口和核心路径，确认没有重复实现。
- [x] T6.2 在现有状态对象旁边记录每个账号的“公开局号最后变化时间”和“最近原始局号来源摘要”，不得新增采集频率。
- [x] T6.3 定义局号停更条件：账号仍有在房证据，状态快照未整体过期，但公开局号超过阈值未变化，并且其他参与账号已经进入更新后的公开局号。
- [x] T6.4 将停更账号标记为 `局号停更` 或 `数据过期`，UI 只展示原因和年龄，不触发恢复动作。
- [x] T6.5 计划生成和发号等待只使用未停更账号；停更账号进入 `excluded_accounts`，原因写明 `局号停更`。
- [x] T6.6 剩余账号达到 3 个时继续运行；少于 3 个时沿用已有“少于 3 个账号，无法对冲”停止原因。
- [x] T6.7 在候选 ready 和发号前二次检查事件中加入最小诊断摘要，不打印长日志。
- [x] T6.8 补测试验证不新增 `refresh_headless`、`enter_room`、`restart`、canvas 探针或资源采集循环。

可选后续任务，不属于阶段 6 默认实现：

- 审计是否沿用已有低频自动进房/接管链路恢复局号停更账号。
- 如需新增恢复动作，另开高风险任务，执行前重新说明风险并取得明确指令。

## 10. 验收阀门和必须测试

执行前阀门：

- 必须确认没有修改禁止文件。
- 必须说明是否触碰局号、状态机、倒计时、坐标、预检、真实点击链路。
- 必须确认没有新增后台高频刷新、canvas 探针、强制刷新、重启或资源监测循环。

必须测试：

- `python -m pytest bet_desktop/tests/test_lightweight_hedge.py`
- 新增或调整测试：状态未整体过期但局号连续不变时，只标记 `局号停更`，不触发刷新/重启/重进房。
- 新增或调整测试：局号停更账号不参与同局、可下注、计划和真实发号名单。
- 新增或调整测试：局号停更账号不阻塞剩余 3 个账号继续发号。
- 新增或调整测试：剩余少于 3 个账号时仍停止并显示既有原因。
- 新增或调整测试：后 4 位差异不参与同局判断。
- 新增或调整测试：候选 ready 和发号前二次检查事件包含最小诊断字段。

人工验收：

- 真实账号观察到“在房但局号不更新”时，UI 能显示明确原因。
- 诊断能说明该账号的房间、公开局号、原始局号来源、最后变化时间、状态年龄。
- 其他 3 个正常账号不被停更账号阻塞。
- 未出现额外刷新、重启、频繁进房或资源采集。

## 11. 风险监测点

- UI 显示回归：房间号、局号、余额、倒计时、状态文字是否仍正确。
- 数据源过滤回归：不能把后 4 位后缀当作不同局。
- 状态同步回归：不能把正常不可下注误判为局号停更。
- 倒计时回归：不能重置同一局 UI 参考倒计时。
- 房间/限红绑定回归：不能混用旧房间上下文。
- 余额更新回归：不能把余额未知误恢复为可参与。
- 坐标定位回归：不得触碰坐标刷新和点击坐标。
- 发号安全回归：停更账号不得进入真实点击名单。
- 资源回归：不能引入更高频轮询、canvas 探针或后台采集循环。

## 12. 执行 agent 约束

- 只执行阶段 6 默认任务，不做恢复工程。
- 先复核代码，再写最小补丁。
- 每个阶段只报告路径、结论、最小证据和下一步。
- 不输出长 diff、长日志、全文件内容。
- 不改禁止文件；如必须扩大范围，停止并请求用户确认。
- 不启动真实下注；测试只跑自动测试和可控 mock。
- 不打包 exe；打包属于后续阶段。
- 不把阶段 8 自动回房审计提前并入阶段 6。
- 最终必须说明：改了哪些文件、是否触碰保护链路、测试结果、是否允许进入下一阶段。

## 13. 中文 commit 建议

建议 commit：

`runtime: 标记局号停更账号并隔离发号`

或中文提交：

`运行时：标记局号停更并避免阻塞剩余账号`

## 14. 执行结果

- 代码范围：仅修改 `bet_desktop/ui/lightweight_probe_adapter.py`、`bet_desktop/ui/lightweight_controller.py`、`bet_desktop/tests/test_lightweight_hedge.py` 和本阶段文档。
- 保护链路：未修改 `live_runtime_state.py`、`live_interval_acceptance_probe.py`、`live_fast_click_probe.py`、坐标、下注前检查、真实点击执行器、打包入口或 exe 输出。
- 实现结论：状态刷新仍沿用现有频率；局号停更只做标记、诊断事件和发号池隔离；剩余 3 个账号可继续发号，少于 3 个账号沿用既有停止原因。
- 自动测试：`python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q`，结果 `72 passed`。
- 人工验证：未启动真实下注，未执行真实平台现场观察；仍需人工观察真实账号出现“在房但局号不更新”时 UI 是否显示 `局号停更`，且没有额外刷新、重启或频繁进房。
