# 阶段 7 登录、接管、进房 UX 与状态联动任务文档

## 1. 审核结论

- 审核结论：通过。
- 是否允许执行：允许进入阶段 7 执行，但只能按本文档做最小补缺和回归验证。
- 当前判断：已有部分能力，只补缺口；禁止重复开发登录、接管、进房底层链路。
- 本阶段性质：中风险 UX 与状态联动验证；如执行中发现必须修改余额、局号、倒计时、房间号、限红、坐标、下注前检查或真实点击执行器，立即停止并重新提风险说明。

## 2. 最新 exe、入口、核心路径复核结论

已复核当前最新已打包 exe 相关路径：

- 打包 spec：`LightweightHedgeConsole.spec`
- 实际入口：`bet_desktop/ui/run_lightweight_dashboard.py`
- UI：`bet_desktop/ui/lightweight_dashboard.py`
- 控制器：`bet_desktop/ui/lightweight_controller.py`
- 轻量核心适配：`bet_desktop/ui/lightweight_probe_adapter.py`
- 浏览器命令适配：`bet_desktop/ui/lightweight_browser_adapter.py`
- 配置和模型：`bet_desktop/ui/lightweight_config_store.py`、`bet_desktop/ui/lightweight_models.py`
- 相关测试：`bet_desktop/tests/test_lightweight_hedge.py`

复核结论：

- `LightweightHedgeConsole.spec` 指向 `bet_desktop/ui/run_lightweight_dashboard.py`。
- `run_lightweight_dashboard.py` 导入 `LightweightController`、`LightweightDashboard`、`LightweightProbeAdapter`。
- 当前轻量入口不是旧 UI worker 事件流。
- `dist/LightweightHedgeConsole/LightweightHedgeConsole.exe` 和 `release-candidates/LightweightHedgeConsole_20260626_three_account_gate_fix/LightweightHedgeConsole/LightweightHedgeConsole.exe` 修改时间均为 `2026-06-26 16:19:01`，大小均为 `6622565`。
- 阶段 7 只审源码与测试，不重新打包；打包属于阶段 10。

## 3. 阶段 7 独立目标

目标是在不改动下注核心和状态采集核心的前提下，确认并补齐登录、代理、接管、进房的操作者体验：

- 打开登录页必须使用当前 UI 表单保存后的配置快照，避免 about:blank 或旧配置。
- a1/a2/a3/a4 的填写登录按钮都可用。
- 代理保持一账号一代理，格式 `IP|端口|代理账号|代理密码|到期时间` 能拆分并传入后端配置。
- 接管和进房进度清楚，失败能在账号卡片或简短状态中看到。
- 进房支持 1/2/3 房选择，选择值从 UI 到 controller、adapter、页面动作保持一致。
- 批量进房保持并行；接管沿用当前轻量实现的有限并发，不引入重逻辑。
- 已读取房间号和完整局号后，不再显示 `visual_loading` 或加载态。

## 4. 现有能力和已有测试

现有能力：

- UI 操作前会同步当前表单到 controller，打开登录页、填写登录、接管、单账号进房、批量进房、全部进房都会先走当前配置。
- `PlatformSlot` 和配置转换已保存登录网址、账号、密码、代理拆分字段。
- a1/a2/a3/a4 均有单账号填写入口；批量填写默认覆盖当前所有账号。
- UI 提供 `1房`、`2房`、`3房` 选择，并将选择值传给 controller。
- `LightweightProbeAdapter._enter_room()` 使用 `asyncio.gather()` 并行进房。
- `LightweightProbeAdapter._handoff_to_headless()` 已使用 2 并发限制，属于轻量并发接管，不是旧 worker 重事件流。
- 进房 progress 通过 health/state 事件映射为“准备进房、进房中、确认进房、房间已打开、进房超时”等状态。
- 读到房间状态后，controller 会把进房详情更新为“进房完成”，清掉 `visual_loading` 文案。

已运行验证：

- 命令：`python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q -k "dashboard_open_login_syncs_current_form_fields or single_account_fill_login_routes_every_account or dashboard_enter_room_uses_selected_room or batch_enter_room_uses_config_room_index_for_button_click or enter_room_all_includes_main_account or room_entry_progress_is_exposed_on_account_status or room_entry_loading_detail_is_cleared_after_room_state or batch_handoff_targets_follow_main_account or platform_slot_to_cluster_config_preserves_proxy_and_headed_mode or cluster_adapter"`
- 结果：`10 passed, 62 deselected`
- 命令：`python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q`
- 结果：`72 passed`

## 5. 缺口清单

执行 agent 必须先按源码复查门确认缺口仍存在，再决定是否修改：

- 需要补一条“打开登录页使用保存后的当前 UI 配置快照，且不会因已有空启动页停留 about:blank”的回归测试或人工验收记录。
- 需要补一条“a1/a2/a3/a4 每个账号的代理独立拆分并传给登录/启动配置”的回归测试，避免一账号一代理退化为共享旧缓存。
- 需要补一条“UI 选择 1/2/3 房后，重启 UI 或重新接管不会回退默认房间”的测试或最小修正。
- 需要补一条“接管失败、进房失败在账号卡片能看到明确中文失败文本”的测试或最小修正。
- 需要补一条“接管/进房不会串号”的测试，重点验证命令目标账号和状态更新账号一致。
- 需要确认现有 batch 进房并行测试是否覆盖真实 `LightweightProbeAdapter._enter_room()` 的并发行为；若未覆盖，只补 adapter 级假异步测试，不改底层进房函数。
- 当前 controller 直接调用可传入大于 3 的 `room_index`；UI 已限制 1/2/3。阶段 7 只需保证 UI 操作链路支持 1/2/3，不得扩大为房间规则重构。

## 6. 允许修改文件范围

允许的最小范围：

- `bet_desktop/ui/lightweight_dashboard.py`
- `bet_desktop/ui/lightweight_controller.py`
- `bet_desktop/ui/lightweight_probe_adapter.py`，仅限登录/接管/进房的进度事件、失败文案、配置快照传递或并发封装小修。
- `bet_desktop/ui/lightweight_browser_adapter.py`，仅限命令记录或测试假适配器辅助，不改旧 UI worker。
- `bet_desktop/ui/lightweight_models.py`，仅限代理字段解析/快照测试需要的小修。
- `bet_desktop/ui/lightweight_config_store.py`，仅限房间选择或配置保存的最小修正。
- `bet_desktop/tests/test_lightweight_hedge.py`
- 本文档和 `specs/lightweight-hedge-console-stability/tasks.md` 的阶段 7 完成记录。

优先顺序：先补测试确认已有能力；只有测试暴露真实缺口时再做最小代码修改。

## 7. 禁止修改文件范围

禁止修改或重构：

- 坐标识别、筹码坐标、下注区域坐标。
- 下注前检查。
- 真实点击执行器。
- 余额、局号、状态机、倒计时、房间号、限红采集主路径。
- 对冲计划生成、金额拆分、剔除/恢复发号阀门。
- 旧 UI worker 事件流。
- canvas 探针、高频刷新、持续后台诊断、常驻资源采样。
- `LightweightHedgeConsole.spec`，除非阶段 10 打包任务明确要求。

禁止为了阶段 7 做如下行为：

- 把旧 UI 的 worker 事件流搬回轻量 UI。
- 新增后台高频刷新、canvas 探针或无用状态获取。
- 为了清理代码重构 `LightweightProbeAdapter` 的状态采集和下注执行路径。
- 修改下注坐标、预检、真实点击、局号或倒计时链路。

## 8. 最小实施任务

- [x] T7-A 执行前复查最新 exe 对应源码路径，并输出结论：`已有部分能力，只补缺口` 或 `已有能力，任务改为回归验证`。
- [x] T7-B 补齐或确认打开登录页使用当前 UI 配置：登录网址、账号、密码、代理都来自本次表单同步后的配置快照。
- [x] T7-C 补齐或确认 a1/a2/a3/a4 单账号填写按钮均可用，且不会串到其他账号。
- [x] T7-D 补齐或确认代理一账号一代理：每个账号独立拆分 `proxy_bundle`，后端配置使用对应账号字段。
- [x] T7-E 补齐或确认接管目标账号正确，批量接管只接管副号，单账号接管只影响该账号，失败显示明确文本。
- [x] T7-F 补齐或确认批量进房并行，不串行等待一个账号完成后才启动下一个账号。
- [x] T7-G 补齐或确认 UI 选择 `1房/2房/3房` 后，controller 配置、adapter 命令参数、进房事件、账号卡目标房间一致。
- [x] T7-H 补齐或确认保存配置后重启 UI 或重新接管，房间选择不回退默认房间。
- [x] T7-I 补齐或确认账号读到目标房间和完整局号后，底栏/账号卡不显示 `visual_loading`、`加载中` 或旧 loading 详情。
- [x] T7-J 更新阶段 7 完成记录：变更文件、测试命令、测试结果、是否触碰保护链路、未验证人工项。

## 9. 验收阀门和必须测试

必须自动测试：

- `python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q`

必须保留或补齐的测试点：

- `test_dashboard_open_login_syncs_current_form_fields`
- `test_single_account_fill_login_routes_every_account`
- `test_dashboard_enter_room_uses_selected_room`
- `test_batch_enter_room_uses_config_room_index_for_button_click`
- `test_enter_room_all_includes_main_account`
- `test_room_entry_progress_is_exposed_on_account_status`
- `test_room_entry_loading_detail_is_cleared_after_room_state`
- `test_batch_handoff_targets_follow_main_account`
- 新增或调整：打开登录页不使用旧缓存，不落到 about:blank。
- 新增或调整：a1/a2/a3/a4 代理独立拆分并进入对应账号配置。
- 新增或调整：批量进房 adapter 层并行启动。
- 新增或调整：接管/进房失败文本能落到对应账号状态。
- 新增或调整：保存并重启后，room_index 不回退默认房间。

人工验收建议：

- 源码版 UI 启动后修改 a1/a2/a3/a4 登录网址、账号、密码、代理，保存并分别打开登录页、填写登录。
- 选择 1/2/3 房分别执行副号进房和全部进房。
- 让一个账号进房失败或超时，确认其他账号进度仍可见且失败原因清楚。
- a2 作为有头主号，a1/a3/a4 作为无头副号时，只做计划/状态链路观察；真实下注验收留到阶段 9。

## 10. 风险监测点

- about:blank 风险：登录 URL 为空、未同步当前表单、或使用旧缓存配置。
- 代理串号风险：批量代理解析后字段进入错误账号。
- 接管串号风险：副号接管时误接管主号，或单账号接管影响其他账号。
- 进房串号风险：目标房间、controller 配置、adapter 参数、页面实际房间不一致。
- 并行风险：批量进房若退回串行，先进入账号可能长时间等待。
- 状态文案风险：完整局号和房间号已读到后仍显示 `visual_loading` 或“加载中”。
- 保护链路风险：任何为了修 UX 而触碰余额、局号、倒计时、房间号、限红、坐标、下注前检查、真实点击执行器，都必须停止。
- 重逻辑风险：不得新增常驻刷新、canvas 探针、后台资源采样或旧 UI worker 事件流。

## 11. 执行 agent 约束

- 只能执行阶段 7，不得顺手推进阶段 8、9、10。
- 先跑或补测试，再做最小实现。
- 已有能力只做回归验证，不得重写。
- 修改前必须说明本次只触碰的文件范围。
- 不得输出长 diff、长日志、完整文件内容。
- 每个检查点只报告：完成了什么、改了哪些文件、验证了什么、是否有 blocker、下一步。
- 如果发现需要修改保护链路，立即停止并说明风险，不得继续实现。
- 完成后必须更新 `tasks.md` 阶段 7 完成记录，并创建中文 commit。

## 12. 中文 commit 建议

建议 commit：

`ui: 验证并补齐轻量控制台登录接管进房体验`

若只有测试和文档：

`test: 补齐轻量控制台登录接管进房回归覆盖`

## 13. 执行完成记录

- 复查结论：最新 exe 对应源码路径仍为轻量控制台路径；已有部分能力，只补缺口。
- 变更文件：`bet_desktop/ui/lightweight_controller.py`、`bet_desktop/tests/test_lightweight_hedge.py`、本文档、`tasks.md`。
- 最小修复：接管/进房类错误统一落到对应账号卡失败详情，便于操作者看到明确中文失败文本。
- 补齐测试：当前登录页跳转不落 `about:blank`、四账号代理独立拆分、保存重开后房间不回默认、接管/进房失败文本按账号归属、adapter 批量进房并行启动。
- 已验证：阶段 7 相关回归 `16 passed`；完整指定测试 `python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q` 通过。
- 保护链路：未修改余额、局号、状态机、倒计时、房间号/限红采集、坐标定位、下注前检查、真实点击执行器。
- 未自动验证人工项：真实账号从登录页进大厅、真实 1/2/3 房页面点击结果、a2 有头主号与 a1/a3/a4 无头副号的真实下注观察；真实下注验收保留到阶段 9。
