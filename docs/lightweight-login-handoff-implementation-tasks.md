# 轻量 UI 登录与接管接入任务文档

## 目标

在当前轻量分支 `codex/lightweight-hedge-console-20260624` 上，把新 UI 接入账号登录、填写登录、无头接管、进房、刷新无头状态、释放无头的最小可用能力。

本阶段只接入“账号会话控制”和“接管进房控制”，不接入完整下注执行，不改旧 UI 的重型刷新面板，不重写已稳定的状态采集、canvas、坐标、下注链路。

## 当前基线

- 新 UI 入口：`bet_desktop/ui/run_lightweight_dashboard.py`
- 新 UI 主界面：`bet_desktop/ui/lightweight_dashboard.py`
- 新 UI 控制器：`bet_desktop/ui/lightweight_controller.py`
- 新 UI 配置模型：`bet_desktop/ui/lightweight_models.py`
- 新 UI 配置保存：`bet_desktop/ui/lightweight_config_store.py`
- 当前轻量测试：`bet_desktop/tests/test_lightweight_hedge.py`
- 旧 worker 控制器：`bet_desktop/backend/cluster_process_worker.py`
- 旧 UI 可参考实现：`bet_desktop/ui/main_dashboard.py`

## 保护边界

以下链路是项目最高保护区，不允许在本任务里直接重构、优化或删除：

- 余额、局号、状态机、倒计时、房间号、限红、坐标定位链路。
- `cluster_process_worker.py` 里已稳定的 headless handoff、进房、标准窗口尺寸、坐标基础尺寸。
- canvas 文本采集、runtime scan、frontend probe 的核心解析逻辑。
- 真实下注执行函数、preflight、坐标点击、确认账本链路。

如果发现必须修改以上逻辑才能继续，停止实现并在最终报告里说明原因、风险和建议的最小改法。

## 轻量原则

- 新 UI 只做轻量调度和关键状态展示。
- 优先复用旧 worker 的命令协议，不搬旧 UI 的大面板和高频刷新。
- 登录/接管阶段只展示关键进度，不展示完整局内实时数据。
- 接管必须复用旧 worker 的标准窗口尺寸：
  - `BACCARAT_COORDINATE_BASE_SIZE = 960x620`
  - `--force-device-scale-factor=1`
- 对副号无头接管时，最多 2 路并发，复用旧 UI 的排队策略。

## 第一阶段任务：补齐 worker 最小依赖

### 背景

当前轻量分支可运行轻量 UI，但直接导入旧 worker 会失败：

- `ClusterProcessController` 导入失败，缺 `bet_desktop.browser.frontend_state_probe`
- `BrowserInstanceConfig` 导入失败，缺 `bet_desktop.core.page_state`

### 任务

从备份分支 `codex/backup-legacy-uncommitted-20260624` 只取旧 worker 运行必需模块，禁止整包搬旧 UI。

建议优先补齐以下最小集合：

- `bet_desktop/browser/frontend_state_probe.py`
- `bet_desktop/browser/game_launch_url.py`
- `bet_desktop/browser/live_runtime_state.py`
- `bet_desktop/browser/login_flow.py`
- `bet_desktop/core/page_state.py`
- `bet_desktop/core/runtime_bet_ledger.py`
- `bet_desktop/models/state_temporal_guard.py`
- `bet_desktop/page/real_detector.py`
- `bet_desktop/parsers/ws_protocol_parser.py`
- `bet_desktop/runtime_state_v2/*`

如果 import 继续报缺模块，只补 worker import 链实际需要的最小文件。

### 验收

以下脚本必须通过：

```powershell
@'
from bet_desktop.backend.cluster_process_worker import ClusterProcessController, ClusterWorkerConfig
from bet_desktop.browser.session_manager import AccountConfig, BrowserInstanceConfig, ProxyConfig
print("ok")
'@ | python -
```

## 第二阶段任务：新增轻量 cluster adapter

### 新增文件

建议新增：

- `bet_desktop/ui/lightweight_cluster_adapter.py`

### 职责

该 adapter 是新 UI 和旧 worker 的唯一桥接层。

必须实现：

- 从 `PlatformSlot` 生成 worker 配置。
- 启动指定账号的有头浏览器到登录页。
- 向已打开浏览器发送填写登录命令。
- 对副号发送无头接管命令。
- 对已接管账号发送进房命令。
- 刷新无头状态。
- 释放无头。
- 停止 worker。
- 轮询 worker 事件，并转成轻量 UI 需要的简化状态。

### 配置转换要求

`PlatformSlot` 到 `ClusterWorkerConfig` 的映射：

- `account_id` -> `instance_id`
- `login_url` -> `login_url`
- `account_username` -> `username`
- `account_password` -> `password`
- `proxy_host/proxy_port/proxy_username/proxy_password` -> `proxy`
- `headless=False`
- `browser_channel="chrome"`
- `user_data_dir=bet_desktop/artifacts/profiles/<account_id>`
- `viewport_width=960`
- `viewport_height=620`
- `runtime_shadow_interval_ms >= 1000`

登录/接管阶段优先低资源：

- `state_poll_interval_ms` 建议 `1000`
- `runtime_pipeline` 建议先用旧 worker 可接受的最低风险配置
- 若关闭 `enable_frontend_probe` 会破坏启动包捕获或接管，则保持开启；不要强行关闭导致接管失败
- `enable_canvas_probe` 和 `enable_runtime_scan` 只在登录/接管阶段不需要时考虑关闭

### 事件映射

worker `health` 事件映射到轻量状态：

- `login_auto_fill` -> `已填写登录`
- `game_launch_context=captured` -> `已捕获启动包`
- `headless_launching` -> `启动无头浏览器`
- `document_opened` -> `已打开真实 URL`
- `locating_hall_buttons` -> `检测大厅入口`
- `headless_ready` -> `无头大厅就绪`
- `headless_status` + `ready.game_ready` -> `无头在线：已在房间`
- `headless_released` -> `未接管`
- `room_entry=entering` -> `进房中`
- `room_entry=click_confirmed` -> `已点击入口`
- `room_entry=game_ready` -> `游戏页已打开`
- `room_entry=timeout` -> `进房超时`

worker `error` 事件必须进入轻量日志，并更新对应账号状态为 `异常`。

### 验收

- adapter 有单元测试覆盖配置转换。
- Fake 模式下不启动真实浏览器也能验证命令调用顺序。
- 导入 `lightweight_cluster_adapter.py` 不报错。

## 第三阶段任务：接入轻量 controller

### 修改文件

- `bet_desktop/ui/lightweight_controller.py`
- `bet_desktop/ui/lightweight_browser_adapter.py` 如仍需要保留 fake 协议，可调整但不要破坏现有测试。
- `bet_desktop/tests/test_lightweight_hedge.py`

### 行为要求

新增或改造以下 controller 方法：

- `open_login_pages_clicked(account_ids=None)`
- `fill_login_clicked(account_ids=None)`
- `batch_handoff_clicked()`
- `batch_enter_room_clicked(room_index=1)`
- `refresh_headless_clicked(account_ids=None)`
- `batch_release_clicked(account_ids=None)`
- `poll_runtime_events()` 或等价机制
- `shutdown_runtime()`

主号规则：

- 当前主号保持有头观察。
- 副号默认作为无头接管对象。
- 若用户切换主号，接管对象自动变成另外 3 个账号。

状态规则：

- 绿色 `启动轻量控制` 仍然只启动“对冲系统总开关”，不能启动 a1/a2/a3/a4。
- 打开登录页是单独动作，不和总开关混淆。
- 接管、进房、释放都必须产生简短日志。

### 验收

- 点击系统启动不会调用 worker 启动账号。
- 打开登录页才会启动账号 worker。
- 接管只针对副号。
- 切换主号后，接管按钮文案和接管目标同步变化。
- 单元测试覆盖主号切换后的目标账号。

## 第四阶段任务：接入新 UI 按钮

### 修改文件

- `bet_desktop/ui/lightweight_dashboard.py`

### UI 调整

配置页批量按钮建议调整为：

- `打开登录页`
- `填写登录`
- `接管副号`
- `批量进房`
- `释放无头`
- `停止浏览器`

运行控制页高级设置：

- `a1/a3/a4 接管` 启用，调用真实接管。
- `批量进房` 启用，默认房间号先使用配置里的 `room_index`。
- `同步/刷新无头` 可作为调试按钮。
- `打开观察窗` 暂不实现，保持禁用。

账号卡片：

- 主号按钮显示 `观察`
- 副号按钮显示 `接管`
- 每个账号显示：
  - 登录/接管状态
  - 房间
  - 局号/倒计时/余额仍可为空，第一阶段不强求

### 验收

- UI 按钮点击有日志反馈。
- 真实未接入或条件不足时，提示原因，不静默失败。
- 不出现“启动轻量控制 = 启动 a1,a2,a3,a4”的误导日志。

## 第五阶段任务：人工测试路径

### 测试 1：打开登录页

前置：

- 4 个平台配置填写登录页、账号、密码、代理。

步骤：

1. 点击 `打开登录页`
2. 应看到 4 个有头浏览器或对应已启动状态。
3. 日志显示启动数量。

验收：

- 不自动无头。
- 不自动进房。
- 代理配置传入 worker。

### 测试 2：填写登录

步骤：

1. 点击 `填写登录`
2. worker 向登录页填账号密码。

验收：

- 日志显示每个账号是否提交。
- 若验证码出现，提示人工处理。

### 测试 3：副号接管

前置：

- 主号保留有头观察。
- 副号已人工到百家乐大厅。

步骤：

1. 点击 `接管副号`
2. 副号进入无头接管队列，最多 2 路并发。

验收：

- 状态依次显示：捕获启动包、启动无头、打开真实 URL、检测大厅入口、大厅就绪。
- 保留 `960x620` 标准尺寸。
- 主号不被接管。

### 测试 4：进房

步骤：

1. 副号大厅就绪后，点击 `批量进房`

验收：

- 只对无头大厅就绪账号发送进房。
- 日志显示点击入口和进房结果。

### 测试 5：释放

步骤：

1. 点击 `释放无头`

验收：

- 无头浏览器关闭。
- 状态回到未接管。

## 不做事项

本任务不做：

- 真实下注执行接入。
- 完整局内实时数据展示。
- 降级或重写旧状态采集核心。
- 改下注坐标和 preflight。
- 搬旧 UI 的大面板、高频日志、高频刷新。

## 最终交付要求

开发完成后必须提供：

- 修改文件清单。
- 哪些旧 worker 依赖被带入轻量分支。
- 哪些按钮已接真实功能。
- 哪些按钮仍是占位。
- 测试命令和结果。
- 是否需要人工到大厅后再接管。
- 是否有任何触碰保护区的地方；如果有，必须说明原因和风险。
