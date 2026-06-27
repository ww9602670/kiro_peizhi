# 阶段 8：掉线或不在房间后的既有自动回房链路审计与最小补缺

## 1. 审核结论

结论：通过，允许进入阶段 8 执行。

执行性质：只允许审计、补测试、补文档和最小安全阀门。默认不允许新增更激进的自动恢复动作。

本阶段不得把“自动回房”扩展成高频刷新、持续重进房、强制重启、强制刷新页面或 canvas 探针工程。若执行中发现必须修改自动恢复触发条件、频率或动作，必须立即停止，按高风险阶段重新审核。

## 2. 最新 exe、入口和核心路径复核结论

已复核最新已打包 exe 使用的源代码路径：

- `LightweightHedgeConsole.spec`：`Analysis(['bet_desktop\\ui\\run_lightweight_dashboard.py'])`，`console=False`。
- `bet_desktop/ui/run_lightweight_dashboard.py`：启动 `LightweightController`、`LightweightDashboard`、`LightweightProbeAdapter`。
- `bet_desktop/ui/lightweight_controller.py`：接收 runtime 事件、更新 UI 状态、控制器层 `_maybe_auto_reenter_room()` 自动回房。
- `bet_desktop/ui/lightweight_probe_adapter.py`：状态轮询、持续对冲等待、计划生成、发号前重试现有 headless 回房。
- `scripts/live_interval_acceptance_probe.py`：`collect_statuses()`、`write_status()`、`retry_headless_room_entries()`、`execute_round()`。
- `bet_desktop/ui/lightweight_browser_adapter.py`：`enter_room(account_ids, room_index)` 命令封装。
- `bet_desktop/tests/test_lightweight_hedge.py`：轻量路径主要回归测试。

已确认本阶段不能重复开发入口、打包、进房、状态读取或真实点击链路。当前工作区无未提交 diff。

最新回归结果：

- 命令：`python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q`
- 结果：`77 passed`

## 3. 当前自动回房链路代码级说明

### 3.1 控制器层自动回房

路径：`bet_desktop/ui/lightweight_controller.py`

- 事件来源：UI 定时器约每 1200ms 调用 `controller.poll_runtime_events()`，从 adapter 事件队列消费 runtime 事件。
- 触发入口：`_handle_runtime_event()` 收到 `event_type == "state"`。
- 触发条件：`_payload_hall_without_game(payload, safe_summary)` 为真，即状态显示 `hall_ready`，且没有 `game_ready` 或轻量房间证据。
- 动作：调用 `_maybe_auto_reenter_room(account_id, payload)`，再执行 `self.adapter.enter_room([account_id], room_index=room_index)`。
- 冷却：`AUTO_REENTRY_COOLDOWN_MS = 30_000`，同一账号 30 秒内最多触发一次。
- 是否需要首次进过房：当前代码不强制要求“曾经成功进过房”。只要收到大厅且非房间状态，就可能触发。
- 目标房间来源：优先 `_room_entry_requested[account_id]`，其次 payload 或 safe_summary 中的 `room_index` / `room_entry_expected_room_index`，最后回退到 `self.config.room_index`，最低为 1。
- 成功后清理：收到 `game_ready` 或 `room_entry == "game_ready"` 时清除该账号自动回房冷却。

### 3.2 adapter / headless 低频回房重试

路径：`bet_desktop/ui/lightweight_probe_adapter.py` + `scripts/live_interval_acceptance_probe.py`

- 周期来源：`LightweightProbeAdapter._periodic_status_loop()` 每 `status_poll_seconds` 轮询一次，默认 2.0 秒，最低 0.5 秒。
- 状态来源：`probe.collect_statuses()` / `probe.write_status()`，沿用现有轻量状态读取。
- 目标账号来源：`_room_entry_targets`，由 `_enter_room_one()` 在手动或批量进房时记录。
- 动作：按目标房间分组调用 `probe.retry_headless_room_entries(...)`。
- 触发条件：账号存在、状态为 `hall_ready` 且非 `game_ready`、runtime 为 `headless`。
- 冷却：`retry_headless_room_entries()` 使用 `last_room_retry_at`，同一账号 12 秒内最多重试一次。
- 目标房间来源：`_room_entry_targets` 或持续对冲配置传入的 `room_index`。

### 3.3 持续对冲等待阶段的回房重试

路径：`bet_desktop/ui/lightweight_probe_adapter.py`

- 触发入口：`_wait_planned_betting_round()`。
- 状态刷新周期：`coordinator_status_refresh_ms=500`。
- 轮询周期：`coordinator_poll_ms=100`。
- 重试对象：优先本轮 `planned_ids`，否则使用未被 `excluded_accounts` 排除的账号。
- 动作：调用 `retry_headless_room_entries()`，但仍受该函数 12 秒冷却限制。
- 点击保护：真实点击发生在 `_wait_planned_betting_round()` 返回 ready 之后的 `probe.execute_round()`；当前代码没有在 `execute_round()` 内插入回房动作。

## 4. 阶段 8 独立目标

阶段 8 的目标是把现有自动回房链路讲清楚、测稳、加最小阀门，回答以下用户关心点：

- 首次到达大厅是否自动进房。
- 是否必须进过一次房间后，掉回大厅才自动进房。
- 检测和重试周期分别是多少。
- 自动回房是否足够稳定支持持续下注等待。
- 是否保持轻量，没有新增激进刷新或重进房循环。

默认目标不是增强恢复强度，而是验证现有能力、补足可观察性和安全阀门。

## 5. 现有能力和已有测试

现有能力：

- 控制器层已有 `_maybe_auto_reenter_room()`，大厅且非房间时触发自动回房，30 秒冷却。
- headless 层已有 `retry_headless_room_entries()`，大厅且非房间时低频重试，12 秒冷却。
- adapter 状态轮询默认 2 秒一次。
- 持续对冲等待阶段状态刷新 500ms、轮询 100ms，但真实重进房仍受 12 秒冷却限制。
- 目标房间优先沿用账号进房记录，其次使用事件里的目标房间，最后使用当前配置房间。
- 房间证据会覆盖大厅标记，避免有坐标或房间证据时误判为大厅。
- 发号等待阶段已有排除账号逻辑，人工剔除或恢复失败账号不阻塞剩余 3 个账号。

已有测试：

- `test_runtime_state_auto_reenters_when_account_returns_to_hall`
- `test_runtime_room_evidence_overrides_hall_marker`
- `test_hall_ready_with_runtime_coordinates_is_not_hall`
- `test_probe_status_uses_lightweight_room_evidence_for_bet_gate`
- `test_round_stale_account_is_excluded_and_three_accounts_continue`
- `test_probe_manual_exclude_does_not_block_same_room_gate`
- `test_probe_manual_restore_failure_does_not_block_three_account_plan`
- `test_probe_round_executes_only_planned_accounts_when_one_is_excluded`
- `test_batch_enter_room_uses_config_room_index_for_button_click`
- `test_enter_room_all_includes_main_account`
- `test_probe_adapter_enter_room_starts_accounts_in_parallel`

## 6. 缺口清单

必须补的缺口：

- 缺少控制器层测试：人工剔除或恢复失败账号收到大厅状态时，不应触发 `_maybe_auto_reenter_room()`。
- 缺少控制器层测试：没有目标房间且配置房间无效时，不得乱进房；若沿用最低 1 房规则，必须在文档中明确并有测试覆盖。
- 缺少测试或阀门：自动回房不得在真实点击执行期触发。当前发号等待和点击执行结构上基本隔离，但需要测试或最小状态阀门证明。
- 缺少测试：首次到达大厅是否自动进房，需要用现状测试固定行为。当前结论是“会自动进房”，不是“必须进过房后才回房”。
- 缺少可观察性：UI 或事件中没有清晰区分“首次大厅自动进房”和“掉回大厅自动回房”。
- 缺少文档化：12 秒 headless 重试冷却、30 秒控制器冷却、2 秒 adapter 状态轮询、1.2 秒 UI 事件消费没有集中说明。

非必须缺口：

- 不要求新增更快检测。
- 不要求新增页面刷新。
- 不要求新增浏览器重启。
- 不要求新增 canvas 探针。
- 不要求新增无限重进房循环。

## 7. 允许修改文件范围

仅允许最小修改以下文件：

- `bet_desktop/ui/lightweight_controller.py`
  - 只允许补自动回房安全阀门和最小可观察状态。
  - 允许在 `_maybe_auto_reenter_room()` 前增加“账号是否可参与 / 是否真实点击中 / 是否有有效目标房间”的轻量判断。
- `bet_desktop/ui/lightweight_probe_adapter.py`
  - 只允许补现有重试的可观察字段或传递最小状态，不得改状态采集、预检、真实点击。
- `bet_desktop/tests/test_lightweight_hedge.py`
  - 补阶段 8 回归测试。
- `specs/lightweight-hedge-console-stability/stage-08-auto-room-reentry-task.md`
  - 记录执行结果。
- `specs/lightweight-hedge-console-stability/tasks.md`
  - 仅允许同步阶段 8 完成记录，不得改前序阶段结论。

如执行 agent 认为必须修改其他文件，必须停止并说明原因。

## 8. 禁止修改文件范围

默认禁止修改：

- `LightweightHedgeConsole.spec`
- `bet_desktop/ui/run_lightweight_dashboard.py`
- `bet_desktop/browser/live_runtime_state.py`
- `scripts/live_interval_acceptance_probe.py`
- `scripts/live_fast_click_probe.py`
- `bet_desktop/backend/cluster_process_worker.py`
- 坐标识别、筹码坐标、下注区坐标相关文件
- 点击前检查、真实点击执行器、下注确认等待相关文件
- 打包输出目录：`dist/`、`release-candidates/`

除非用户在风险说明后再次明确要求，否则不得修改余额、局号、倒计时、坐标、预检、真实点击链路。

## 9. 最小实施任务

- [x] T8.1 复核最新 exe 入口和核心路径，确认仍为轻量控制台路径，不重复开发。
- [x] T8.2 用测试固定当前行为：账号首次到达大厅且非房间时，控制器层会按当前配置或事件目标房间自动进房。
- [x] T8.3 用测试固定当前行为：账号曾经进过 2/3 房后掉回大厅时，优先回到之前记录的目标房间。
- [x] T8.4 用测试固定冷却：控制器层同一账号 30 秒内不重复 enter_room。
- [x] T8.5 用测试固定 headless 重试冷却：`retry_headless_room_entries()` 12 秒内不重复重试。
- [x] T8.6 补最小阀门：人工剔除、恢复失败、余额不足或不在当前计划池的账号不得被控制器层自动回房重新纳入。
- [x] T8.7 补最小阀门或测试：真实点击执行期不得触发自动回房；若已有结构保证，写测试证明。
- [x] T8.8 补最小可观察性：自动回房日志或账号进房详情需能区分“首次大厅自动进房”和“检测到回大厅后自动回房”，但不得新增主界面调试日志面板。
- [x] T8.9 更新本文件执行结果，列明修改文件、测试命令、是否触碰保护链路。

如果 T8.2-T8.5 证明现有链路已经足够，且 T8.6-T8.8 没有缺口，可转为只补测试和文档化。

## 10. 验收阀门和必须测试

执行前阀门：

- 必须先输出最新 exe 入口复核结论。
- 必须确认没有修改禁止文件。
- 必须确认没有新增高频刷新、canvas 探针、页面刷新、浏览器重启或无限重进房。
- 必须确认没有修改余额、局号、倒计时、坐标、预检、真实点击链路。

必须测试：

- `python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q`
- 新增或调整测试：首次大厅状态会按明确规则自动进房。
- 新增或调整测试：曾进房后掉回大厅优先回原目标房间。
- 新增或调整测试：30 秒控制器冷却阻止重复自动回房。
- 新增或调整测试：12 秒 headless 冷却阻止重复重试。
- 新增或调整测试：人工剔除 / 恢复失败账号不会被控制器自动回房重新纳入。
- 新增或调整测试：自动回房不在真实点击执行期触发。
- 新增或调整测试：房间证据覆盖大厅标记，避免误进房。

人工验收：

- 真实账号首次到大厅时，若当前规则允许自动进房，UI 能看到进入目标房间过程。
- 真实账号掉回大厅时，自动回到目标房间，不出现高频反复点击。
- 连续运行时没有额外刷新、重启、canvas 探针或资源监控循环。
- 自动回房失败只记录原因，不阻塞剩余 3 个可用账号继续等待和发号。

## 11. 风险监测点

- UI 显示风险：自动回房状态不得覆盖真实房间、局号、余额、倒计时显示。
- 数据过滤风险：人工剔除、恢复失败、余额不足账号不得被自动回房重新拉回参与池。
- 状态同步风险：首次大厅自动进房和掉回大厅自动回房必须可区分，避免误判。
- 倒计时风险：自动回房不得重置或伪造 UI 参考倒计时。
- 房间绑定风险：必须使用明确 room_index，不能在无目标房间时乱进房。
- 下注稳定风险：真实点击执行期不得插入进房动作。
- 资源风险：不得增加高频状态采集、canvas 探针、刷新、重启或无限循环。
- 回归风险：房间证据覆盖大厅标记的已有能力必须保留。

## 12. 执行 agent 约束

- 执行 agent 不是本审核 agent。
- 开始前必须重新读取 AGENTS.md、manifest、project context、requirements、design、tasks 和本文件。
- 只能执行阶段 8 本文件限定的任务。
- 默认先补测试，测试暴露缺口后再做最小实现。
- 不得边测真实下注边改代码。
- 不得输出长 diff、长日志、整文件内容。
- 每个阶段汇报只给路径、结论、最小证据、下一步。
- 若发现必须修改保护链路，立即停止并请求用户重新明确授权。

## 13. 中文 commit 建议

建议 commit：

`运行时：补自动回房审计阀门和回归测试`

或：

`轻量控制台：固定自动回房冷却与剔除账号保护`

## 14. 执行结果

- 完成时间：2026-06-27
- 最新 exe 相关源码复查结论：已有部分能力，只补缺口；入口仍为 `LightweightHedgeConsole.spec` -> `bet_desktop/ui/run_lightweight_dashboard.py`，运行路径仍为 `LightweightController` + `LightweightDashboard` + `LightweightProbeAdapter`。
- 变更文件：
  - `bet_desktop/ui/lightweight_controller.py`
  - `bet_desktop/ui/lightweight_probe_adapter.py`
  - `bet_desktop/tests/test_lightweight_hedge.py`
  - `specs/lightweight-hedge-console-stability/stage-08-auto-room-reentry-task.md`
  - `specs/lightweight-hedge-console-stability/tasks.md`
- 已实现：
  - 控制器层自动回房跳过待恢复、人工剔除、恢复失败、当前计划已排除或不在当前计划池的账号。
  - 真实点击执行期通过最小执行状态事件暂停控制器层自动回房。
  - 账号进房详情和日志区分“首次大厅自动进房”和“检测到回大厅后自动回房”。
  - 用测试固定控制器 30 秒冷却、headless 12 秒冷却、房间证据覆盖大厅标记、计划外账号不自动回房。
- 未新增能力：
  - 未新增高频刷新、canvas 探针、页面刷新、浏览器重启或无限重进房。
  - 未修改余额、局号、倒计时、坐标、下注前检查或真实点击链路。
- 自动测试：
  - `python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q -k "auto_reentry or auto_reenters or headless_room_retry or room_evidence or hall_ready_with_runtime_coordinates or lightweight_room_evidence or executes_only_planned"`，结果 10 passed。
  - `python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q`，结果 83 passed。
- 人工验证：未启动真实平台、未执行真实下注。
- 风险结论：本阶段只收紧控制器层自动回房参与边界和补执行期暂停信号；不改变既有低频恢复频率、状态采集和点击执行。
