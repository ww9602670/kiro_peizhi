# 轻量 UI 局号与可下注状态同步修复设计

## Interfaces

### `scripts/live_interval_acceptance_probe.py`

允许修改范围：

- `status_from_snapshot()`。
- 仅新增辅助函数，用于从已有 `snapshot`、`trusted.safe_summary`、`guard` 中整理轻量显示态。

禁止修改范围：

- `wait_betting_round()`。
- `coordinator_readiness()`。
- `execute_round()`。
- `run_acceptance()` 的下注执行路径。
- `prime_countdown_window_for_execution()`。
- 任何点击、筹码拆分、坐标定位、真实下注前 preflight 函数。

输出新增字段：

- `display_game_no`
- `display_room_label`
- `display_balance_cents`
- `display_betting_open`
- `display_phase`
- `display_source`

旧字段 `game_no`、`countdown`、`betting_open` 可以继续保留，但轻量 UI 应优先消费 `display_*`。

### `bet_desktop/ui/lightweight_probe_adapter.py`

允许修改范围：

- `status_to_state_event()`。

职责：

- 只接收 `status_from_snapshot()` 整理后的轻量状态。
- 把 `display_betting_open=True` 转成 UI 12 秒参考倒计时启动信号。
- 保留 `backend_countdown` 作为调试字段，不用于 UI 展示倒计时。

禁止事项：

- 不直接读取浏览器。
- 不自行扫描 canvas。
- 不重新解释下注坐标。
- 不调用下注执行函数。

### `bet_desktop/ui/lightweight_controller.py`

允许修改范围：

- UI 展示态消费。
- 本地参考倒计时锚点保护。
- 过期状态判断。

职责：

- 同一局不重置 `ui_countdown_started_ms`。
- 参考倒计时归零后，`betting_open=False`。
- 收到明确不可下注事件时，立即切换为不可下注。

禁止事项：

- 不把 UI 倒计时用于真实下注发号。
- 不修改主副号对冲金额计算。
- 不修改连续对冲循环。

## Data Model

轻量状态包建议结构：

```python
{
    "game_no": "...",                  # 兼容旧字段
    "betting_open": True,              # 兼容旧字段
    "display_game_no": "...",          # UI 优先使用
    "display_room_label": "T001",
    "display_balance_cents": 316529,
    "display_betting_open": True,
    "display_phase": "betting_open",
    "display_source": "canvas_game_no+runtime_action",
}
```

UI payload 建议结构：

```python
{
    "batch_id": display_game_no,
    "exact_countdown": 12 or 0,
    "ui_countdown_started_ms": status_ts_ms,
    "backend_countdown": raw_countdown,
    "ocr_balance": "3165.29",
    "safe_summary": {
        "runtime_betting_open": display_betting_open,
        "ui_reference_countdown": True,
        "display_phase": display_phase,
        "display_source": display_source,
    },
}
```

## 状态判断规则

### 局号

优先级：

1. `summary.canvas_game_no`
2. `snapshot.game_no`
3. `guard.observed_batch_id`
4. `summary.frontend_batch_id`
5. `summary.frontend_short_batch_id` 仅作为辅助，不应单独替代完整局号

排除规则：

- `summary.label_internal_game_no` 只能作为低优先级 fallback。
- 当 `canvas_game_no` 与 `label_internal_game_no` 不一致时，优先当前画面 `canvas_game_no`。

### 房间内

以下证据可判定为房间内：

- `room_label` 或 `locked_room_label` 存在，并且存在可信局号。
- `runtime_coordinates.bet_regions` 存在。
- `runtime_coordinates.chips` 存在。

若房间内证据成立，则不允许单独根据 `hall_ready=True` 显示大厅。

### 可下注

优先级：

1. `runtime_action == 3`
2. `frontend_runtime_action == 3`
3. `snapshot.betting_open`
4. `guard.betting_open`

不可下注：

- `runtime_action` 明确不是 3，且没有其他可下注证据。
- 可下注参考倒计时归零。

## Error Handling

1. 没有可信局号时，UI 保持上一局但标记数据过期，不能显示可下注。
2. 没有房间证据时，UI 可以显示大厅或未同步。
3. 可下注状态来源冲突时，以“可下注证据必须强于大厅证据”为原则：有当前房间局号和 action=3 时，优先可下注。
4. 如果 `display_*` 缺失，UI 可退回旧字段，但测试应覆盖 fallback。

## Configuration

1. UI 参考倒计时固定为 12 秒，常量在轻量 UI adapter 中定义。
2. 后端状态轮询保持当前低频策略，不为 UI 倒计时增加采集频率。
3. 不新增用户可见配置项。

## Test Strategy

必须新增或更新测试：

1. `status_from_snapshot()` 或其辅助函数：`canvas_game_no` 优先于旧 `label_internal_game_no`。
2. `runtime_action=3` 映射为 `display_betting_open=True`。
3. `runtime_action=5` 映射为 `display_betting_open=False`。
4. 有房间坐标和局号时，`hall_ready=True` 不导致 UI 显示大厅。
5. `status_to_state_event()` 优先消费 `display_*` 字段。
6. UI 参考倒计时同一局不重置，归零关闭展示。

验证命令：

```powershell
python -m py_compile scripts\live_interval_acceptance_probe.py bet_desktop\ui\lightweight_probe_adapter.py bet_desktop\ui\lightweight_controller.py
python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q
```

## Rollback

如现场验证异常，回滚范围应只包含本修复涉及文件：

- `scripts/live_interval_acceptance_probe.py`
- `bet_desktop/ui/lightweight_probe_adapter.py`
- `bet_desktop/ui/lightweight_controller.py`
- `bet_desktop/tests/test_lightweight_hedge.py`

不得回滚用户或其他任务在同一分支上的无关修改。
