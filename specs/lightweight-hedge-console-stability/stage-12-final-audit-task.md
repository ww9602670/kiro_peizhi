# 阶段12最终审核任务文档

## 1. 最终审核结论

- 审核结论：通过。
- 允许进入：最终交付给人工持续测试。
- 允许进入阶段 13：允许进入最终交付/封版记录阶段，但阶段 13 不得补改业务代码；如人工测试发现阻塞问题，必须回到对应阶段做最小修复。
- 本阶段性质：只做最终审核和文档记录；未启动登录、进房、持续运行或真实下注。

## 2. 源码、exe、commit、测试状态

- 工作区：`H:\d\bocai_web`
- 当前分支：`codex/lightweight-login-handoff-20260624`
- 阶段12写文档前工作区状态：干净。
- 最新 commit：`084cbe953a9ec45fee5c12e354990c2833e6cd70`
- 最新 commit 时间：`2026-06-27T11:57:33+08:00`
- 最新 commit 标题：`tools：添加轻量控制台一次性资源诊断报告`
- 最新 exe：`H:\d\bocai_web\dist\LightweightHedgeConsole\LightweightHedgeConsole.exe`
- exe 生成时间：`2026-06-27 11:39:41`
- exe 大小：`6,630,710 bytes`
- 打包入口：`LightweightHedgeConsole.spec`
- 真实运行入口：`bet_desktop\ui\run_lightweight_dashboard.py`
- 真实 PyQt UI：`bet_desktop\ui\lightweight_dashboard.py`
- 控制器：`bet_desktop\ui\lightweight_controller.py`
- 轻量适配器：`bet_desktop\ui\lightweight_probe_adapter.py`
- 阶段12复跑自动测试：`python -m pytest bet_desktop\tests\test_lightweight_hedge.py bet_desktop\tests\test_lightweight_resource_diagnostics.py -q`
- 阶段12测试结果：`86 passed in 30.38s`

## 3. T12.1-T12.10 逐项结论

### T12.1 文档与实现一致

- 结论：通过。
- 证据路径：
  - `specs\lightweight-hedge-console-stability\tasks.md`
  - `specs\lightweight-hedge-console-stability\stage-05-amount-chip-task.md`
  - `specs\lightweight-hedge-console-stability\stage-06-round-staleness-task.md`
  - `specs\lightweight-hedge-console-stability\stage-07-login-handoff-room-task.md`
  - `specs\lightweight-hedge-console-stability\stage-08-auto-room-reentry-task.md`
  - `specs\lightweight-hedge-console-stability\stage-09-live-bet-readiness-task.md`
  - `specs\lightweight-hedge-console-stability\stage-10-package-exe-task.md`
  - `specs\lightweight-hedge-console-stability\stage-11-resource-diagnostics-task.md`
- 最小证据：阶段 5-11 均有独立阶段文档；`tasks.md` 已记录阶段完成记录、测试命令、未验证项和风险结论。

### T12.2 V5 排版已进入真实 PyQt UI

- 结论：通过。
- 证据路径：
  - `bet_desktop\ui\run_lightweight_dashboard.py`
  - `bet_desktop\ui\lightweight_dashboard.py`
  - `LightweightHedgeConsole.spec`
- 最小证据：
  - `LightweightHedgeConsole.spec` 指向 `bet_desktop\ui\run_lightweight_dashboard.py`。
  - `run_lightweight_dashboard.py` 创建 `LightweightController(adapter=LightweightProbeAdapter())` 并加载 `LightweightDashboard`。
  - `lightweight_dashboard.py` 的真实运行页为三列：左侧运行控制/高级设置，中心账号状态/单账号盈亏，右侧本轮计划/运行摘要。
  - `下注流水累计` 和 `执行轮次` 已在高级折叠区，`单账号盈亏` 已在中心账号状态下方；该证据来自 PyQt 源码，不依赖 `docs/ui-drafts/lightweight-hedge-console-v5-layout.html`。

### T12.3 低风险 UI 修改没有碰下注核心

- 结论：通过。
- 证据路径：
  - `specs\lightweight-hedge-console-stability\stage-10-package-exe-task.md`
  - `specs\lightweight-hedge-console-stability\stage-11-resource-diagnostics-task.md`
- 最小证据：阶段 10 和阶段 11 完成记录均明确未修改余额、局号、状态机、倒计时、房间号、限红、坐标定位、下注前检查或真实点击执行链路。

### T12.4 剔除/恢复逻辑有测试覆盖

- 结论：通过。
- 证据路径：
  - `bet_desktop\tests\test_lightweight_hedge.py`
  - `specs\lightweight-hedge-console-stability\stage-08-auto-room-reentry-task.md`
- 最小证据：
  - 覆盖手动剔除/恢复按钮状态、剔除账号不阻塞同房阀门、恢复失败不阻塞 3 账号计划、恢复仅在状态 ready 时重新加入、余额未知保持剔除、少于 3 个手动参与账号停止并给出原因。

### T12.5 3 号降级后不再等待被剔除账号

- 结论：通过。
- 证据路径：
  - `bet_desktop\tests\test_lightweight_hedge.py`
  - `specs\lightweight-hedge-console-stability\stage-05-amount-chip-task.md`
  - `specs\lightweight-hedge-console-stability\stage-06-round-staleness-task.md`
- 最小证据：已有测试覆盖低余额主号剔除后由最高余额账号继承、局号停更账号被隔离后剩余 3 账号继续、计划和执行账号集合只包含最终参与账号。

### T12.6 3 号恢复 4 号有明确按钮和状态

- 结论：通过。
- 证据路径：
  - `bet_desktop\ui\lightweight_dashboard.py`
  - `bet_desktop\ui\lightweight_controller.py`
  - `bet_desktop\tests\test_lightweight_hedge.py`
- 最小证据：本轮计划区每个账号有 `下局剔除/下局恢复` 动作；控制器状态包含 `待剔除`、`已剔除`、`待恢复`、`恢复失败`；测试覆盖恢复成功、恢复失败、余额未知不恢复。

### T12.7 运行统计不再 100 轮封顶

- 结论：通过。
- 证据路径：
  - `bet_desktop\ui\lightweight_controller.py`
  - `bet_desktop\ui\lightweight_dashboard.py`
  - `bet_desktop\tests\test_lightweight_hedge.py`
- 最小证据：控制器继续累计 `_round_results` 并更新运行摘要；测试 `test_round_summary_is_uncapped_but_tables_show_recent_rows` 覆盖累计统计不封顶、表格只显示最近行。

### T12.8 局号停更有诊断和安全隔离，不默认新增自动恢复

- 结论：通过。
- 证据路径：
  - `bet_desktop\ui\lightweight_probe_adapter.py`
  - `bet_desktop\ui\lightweight_controller.py`
  - `bet_desktop\tests\test_lightweight_hedge.py`
  - `specs\lightweight-hedge-console-stability\stage-06-round-staleness-task.md`
- 最小证据：源码有 `round_stale`、`round_stale_reason`、`round_stale_age_ms` 诊断字段；测试覆盖局号停更标记、局号停更账号不参与发号、剩余 3 账号继续、少于 3 账号停止、等待诊断字段记录；阶段 6 文档明确不默认主动刷新/重进房/重启浏览器。

### T12.9 打包 exe 已验证启动

- 结论：通过。
- 证据路径：
  - `specs\lightweight-hedge-console-stability\stage-10-package-exe-task.md`
  - `H:\d\bocai_web\dist\LightweightHedgeConsole\LightweightHedgeConsole.exe`
- 最小证据：阶段 10 记录 `pyinstaller --clean --noconfirm LightweightHedgeConsole.spec` 通过；exe 路径、大小、生成时间已记录；exe 版 smoke test 只启动/关闭窗口通过。

### T12.10 无法完全自动验证的人工测试项已记录

- 结论：通过。
- 证据路径：
  - `specs\lightweight-hedge-console-stability\stage-09-live-bet-readiness-task.md`
  - `specs\lightweight-hedge-console-stability\stage-10-package-exe-task.md`
  - `specs\lightweight-hedge-console-stability\stage-11-resource-diagnostics-task.md`
  - `artifacts\resource-diagnostics\stage11_resource_diagnostics_20260627_115607.md`
- 最小证据：真实平台登录、真实进房、真实余额/局号变化、真实点击结果、4 账号登录/进房/启动轻量控制后的资源采样均记录为人工最终测试项。

## 4. 已确认未触碰的保护链路

阶段12只新增本审核文档，未修改业务代码。已确认本阶段未触碰：

- 余额采集。
- 局号采集。
- 状态机判断。
- 倒计时或可下注信号判断。
- 房间号。
- 限红。
- 坐标定位。
- 下注前检查。
- 真实点击执行器。
- 登录、进房、接管、真实下注流程。

## 5. 已知未自动验证项

- 未登录真实平台账号。
- 未打开真实登录页并填写账号。
- 未接管真实账号。
- 未进入真实 1/2/3 房。
- 未启动持续轻量控制。
- 未执行真实下注。
- 未观察真实余额变化、真实局号变化、真实点击结果。
- 未在 4 账号登录后、4 账号进房后、启动轻量控制后采集资源数据。
- 未验证 frozen exe 深层行为是否完全等同源码运行，包括读取配置、打开登录页、进入运行页和真实平台闭环。

## 6. 人工最终测试入口和步骤摘要

入口：

- `H:\d\bocai_web\dist\LightweightHedgeConsole\LightweightHedgeConsole.exe`

人工步骤摘要：

1. 启动 exe，确认进入轻量控制台。
2. 检查平台配置和 4 个账号配置是否读取正确。
3. 分别打开 a1/a2/a3/a4 登录页并填写登录信息。
4. 接管目标账号。
5. 选择 1/2/3 房并批量进房。
6. 观察账号状态、房间号、局号、余额、可下注状态、进房进度。
7. 启动轻量控制，观察至少 10 个发号周期。
8. 检查本轮计划、实际下注记录、点击耗时、缺口、流水累计、单账号盈亏。
9. 观察低余额剔除、3 账号降级、下局恢复、局号停更隔离、自动回房提示是否符合预期。
10. 使用阶段 11 资源诊断脚本补采 4 账号登录后、进房后、启动轻量控制后的资源数据。

## 7. 是否允许最终交付/阶段13封版记录

- 允许最终交付给人工持续测试。
- 允许进入阶段 13 封版记录。
- 阶段 13 建议只记录最终交付包、人工测试入口、未自动验证项和回退点，不修改业务代码。

## 8. 阻塞项和建议修复阶段

- 阻塞项：无。
- 非阻塞人工验证项：见第 5 节。
- 若人工测试发现真实登录、进房或下注闭环失败，建议回到阶段 7、8 或 9 对应范围做最小修复。
- 若人工测试发现 3 账号降级/恢复、局号停更隔离或发号阀门异常，建议回到阶段 5、6 或 8 对应范围做最小修复。
- 若人工测试发现 frozen exe 行为与源码不同，建议回到阶段 10 重新打包和验证。

## 9. 中文 commit 建议

建议 commit：

`docs：完成阶段12最终审核记录`

建议正文：

- 记录最新 commit、工作区状态、exe 路径和生成时间。
- 记录 V5 排版进入真实 PyQt UI 的源码证据。
- 逐项审核 T12.1-T12.10。
- 记录保护链路未触碰、未自动验证项和人工最终测试入口。
- 结论：允许进入最终交付和阶段 13 封版记录。
