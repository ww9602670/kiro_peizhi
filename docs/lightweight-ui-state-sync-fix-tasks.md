# 轻量 UI 局号与可下注状态同步修复任务

## 执行原则

1. 最小改动。
2. 只修轻量状态输出和 UI 状态消费。
3. 不改真实下注核心。
4. 不恢复旧 UI 的重型状态采集。
5. 每个任务完成后必须跑对应测试。

## 保护阀门

### V1. 禁止触碰下注执行核心

以下函数和同类真实下注执行路径不得修改：

- `wait_betting_round`
- `coordinator_readiness`
- `execute_round`
- `prime_countdown_window_for_execution`
- `_execute_live_fire_bet_plan`
- 任何筹码拆分、下注坐标、点击执行函数

### V2. 禁止增加 UI 倒计时采集频率

UI 倒计时必须本地自走，不允许为了 UI 展示每秒读取浏览器。

### V3. 禁止把旧 UI 重状态面板搬进轻量 UI

本次只输出局号、房间、余额、可下注/不可下注和参考倒计时。

### V4. 局号不能被旧缓存覆盖

当 `canvas_game_no` 与 `label_internal_game_no` 冲突时，必须优先当前画面 `canvas_game_no`。

### V5. 可下注不能被大厅误判覆盖

当存在当前局号、房间证据、下注坐标或 `runtime_action=3` 时，不能因为 `hall_ready=True` 单独显示大厅。

### V6. 测试不过不得重启 UI

只有在语法检查和轻量测试通过后，才允许重启新轻量 UI。

## T1. 落档确认

- 状态：完成
- 文件：
  - `docs/lightweight-ui-state-sync-fix-requirements.md`
  - `docs/lightweight-ui-state-sync-fix-design.md`
  - `docs/lightweight-ui-state-sync-fix-tasks.md`
- 验收：文档明确目标、非目标、风险、保护阀门、测试命令。

## T2. 新增轻量显示态整理函数

- 状态：完成
- 文件责任：
  - `scripts/live_interval_acceptance_probe.py`
- 内容：
  - 新增一个小型 helper，从 `snapshot`、`trusted.safe_summary`、`guard` 整理 `display_*` 字段。
  - 局号优先级按设计文档执行。
  - 可下注优先识别 `runtime_action=3`、`frontend_runtime_action=3`。
  - 房间内证据成立时，避免被 `hall_ready=True` 覆盖。
- 验收：
  - 有单测覆盖局号优先级。
  - 有单测覆盖 action=3 可下注。
  - 有单测覆盖 hall_ready 冲突不覆盖房间内状态。

## T3. UI adapter 消费 `display_*`

- 状态：完成
- 文件责任：
  - `bet_desktop/ui/lightweight_probe_adapter.py`
- 内容：
  - `status_to_state_event()` 优先读取 `display_game_no`、`display_betting_open`、`display_room_label`、`display_balance_cents`。
  - 保留 `backend_countdown` 调试字段。
  - 可下注时只给 UI 12 秒参考倒计时。
- 验收：
  - 单测确认 `display_game_no` 优先于旧 `game_no`。
  - 单测确认 `display_betting_open=True` 触发 `exact_countdown=12`。

## T4. 控制器确认同局倒计时保护

- 状态：完成
- 文件责任：
  - `bet_desktop/ui/lightweight_controller.py`
- 内容：
  - 检查现有 `ui_countdown_started_ms` 锚点保护是否覆盖 `display_*` 状态。
  - 同局刷新不重置倒计时。
  - 倒计时归零后展示不可下注。
- 验收：
  - 现有倒计时测试继续通过。
  - 如发现 display 字段未覆盖，补最小测试和最小修复。

## T5. 测试补齐

- 状态：完成
- 文件责任：
  - `bet_desktop/tests/test_lightweight_hedge.py`
- 内容：
  - 增加局号优先级测试。
  - 增加 runtime action 状态测试。
  - 增加 hall_ready 冲突测试。
  - 增加 adapter display 字段优先测试。
- 验收：
  - `python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q` 通过。

## T6. 验证和交付

- 状态：完成
- 命令：
  - `python -m py_compile scripts\live_interval_acceptance_probe.py bet_desktop\ui\lightweight_probe_adapter.py bet_desktop\ui\lightweight_controller.py`
  - `python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q`
- 验收：
  - 编译通过。
  - 测试通过。
  - 最终回复列出改动文件、验证结果和是否需要重启 UI 现场测试。

## 执行结果

- 执行 agent：gpt-5.3-codex-spark。
- 代码结果：已新增 `display_*` 轻量显示态，UI adapter 已优先消费 `display_*`，controller 已避免房间内状态被大厅误判覆盖。
- 测试结果：`python -m py_compile scripts\live_interval_acceptance_probe.py bet_desktop\ui\lightweight_probe_adapter.py bet_desktop\ui\lightweight_controller.py` 通过。
- 测试结果：`python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q` 通过，29 个测试全部通过。
- 交付状态：需要重启新轻量 UI 后进行现场验证。
