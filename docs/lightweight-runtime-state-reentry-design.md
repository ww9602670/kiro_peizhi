# 轻量 UI 运行状态与自动回房设计

## Interfaces

### LightweightClusterAdapter

- 默认启用 `enable_runtime_scan=True`。
- 保持 `enable_frontend_probe=False` 和 `enable_canvas_probe=False`。
- 继续通过已有 worker 事件队列接收 `state`、`health`、`error`。

### LightweightController

- 保存每个账号最近一次 `state` 快照。
- 根据快照生成 `AccountStatusSummary`。
- 根据大厅态自动补发 `enter_room`。
- 自动补发只调用已有 `adapter.enter_room()`，不直接操作浏览器。

### LightweightDashboard

- 账号卡片增加“状态机”行。
- 原“待确认”行保留用于后续真实下注结果，当前显示 pending 金额或 `-`。

## Data Model

`AccountStatusSummary` 增加：

- `state_machine_label`: 面向操作者的状态机摘要。
- `stale`: 当前状态是否过期。
- `age_ms`: 最近状态年龄。

控制器内部增加：

- `_runtime_snapshots`: 最近 state 快照。
- `_auto_reentry_enabled`: 是否允许自动补进房。
- `_auto_reentry_cooldowns_ms`: 每个账号下一次允许自动补进房的时间。
- `_room_entry_requested`: 已主动要求进入的目标房间。

## Error Handling

1. worker `error` 事件进入账号异常状态和轻量日志。
2. 自动补进房失败不重试刷屏，只按冷却周期再次尝试。
3. 状态过期不当作可下注状态。

## Configuration

1. 轻量 runtime 扫描周期沿用 `BET_DESKTOP_RUNTIME_SHADOW_INTERVAL_MS` 与 `state_poll_interval_ms`，默认约 1 秒。
2. 自动回房冷却默认 30 秒。
3. canvas 探针默认关闭。

## Test Strategy

1. 单测验证 runtime scan 默认开启、canvas/front-end probe 默认关闭。
2. 单测验证 state 事件能映射房间、局号、余额、倒计时和状态机。
3. 单测验证倒计时基于快照时间递减。
4. 单测验证大厅态会触发一次自动补进房，冷却内不重复触发。
5. 运行 `python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q`。
