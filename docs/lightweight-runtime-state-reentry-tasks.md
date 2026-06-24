# 轻量 UI 运行状态与自动回房任务

## T1. 文档固化

- 状态：完成
- 内容：新增需求、设计、任务文档。
- Acceptance: 文档明确不修改旧采集/坐标/下注保护核心。

## T2. 打开轻量低频 runtime 状态源

- 状态：完成
- 内容：轻量 cluster adapter 默认启用 runtime scan，仍关闭 frontend probe 和 canvas probe。
- Acceptance: 测试确认轻量 adapter 启动 worker 时 `enable_runtime_scan=True`，`enable_frontend_probe=False`，`enable_canvas_probe=False`。

## T3. 状态卡片补齐

- 状态：完成
- 内容：控制器映射状态机、数据年龄、过期状态；UI 增加“状态机”行。
- Acceptance: 单测确认状态机、局号、余额、倒计时能展示；过期状态不会显示为可下注。

## T4. 本地倒计时递减

- 状态：完成
- 内容：根据 state 快照时间计算显示倒计时，避免 UI 静止在旧秒数。
- Acceptance: 单测确认 12 秒快照在 3 秒后显示 9 秒。

## T5. 自动补进房

- 状态：完成
- 内容：账号收到大厅态且曾被要求进房时，按冷却自动重发 `enter_room` 命令。
- Acceptance: 单测确认大厅态触发补进房，冷却内不重复发送。

## T6. 验证和重启

- 状态：完成
- 内容：运行轻量测试、编译检查、重启新轻量 UI。
- Acceptance: `python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q` 通过 20 项；`python -m compileall ...` 通过；UI 重启后人工现场确认。
