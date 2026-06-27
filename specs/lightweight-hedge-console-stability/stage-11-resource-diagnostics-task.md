# 阶段 11：手动资源诊断和多套运行容量审核任务

## 1. 审核结论

- 审核结论：通过。
- 是否允许执行：允许进入阶段 11 执行，但只允许做人工触发或验收期一次性资源诊断报告。
- 执行边界：不得新增常驻资源监控线程、后台定时器、主界面资源曲线或资源刷新配置。
- 自动执行边界：自动执行阶段只允许采样系统总量、Python/PyInstaller 进程、Chromium/Chrome 进程，以及空闲 UI smoke 状态资源。
- 人工测试边界：4 账号进房后、启动下注控制后的资源数据如需真实平台，只能标记为“最终人工 exe 测试时采集”，不得由执行 agent 登录、进房、下注或编造。

结论：阶段 11 属于低风险诊断文档和一次性采样任务。只要执行 agent 严格限制在本文档范围内，可以执行。

## 2. 最新 exe、入口和核心路径复核结论

已复核当前轻量 exe 和源码入口：

- 最新 exe 路径：`dist/LightweightHedgeConsole/LightweightHedgeConsole.exe`
- 当前实际文件：存在。
- 当前 exe 修改时间：`2026-06-27 11:39:41`
- 当前 exe 大小：`6,630,710 bytes`
- 打包配置：`LightweightHedgeConsole.spec`
- spec 入口：`bet_desktop\ui\run_lightweight_dashboard.py`
- spec 输出名：`LightweightHedgeConsole`
- 启动入口：`bet_desktop/ui/run_lightweight_dashboard.py`
- 入口创建：`LightweightController(adapter=LightweightProbeAdapter())`
- 真实 UI：`bet_desktop/ui/lightweight_dashboard.py`
- 当前控制器：`bet_desktop/ui/lightweight_controller.py`
- 轻量核心适配：`bet_desktop/ui/lightweight_probe_adapter.py`
- 主回归测试：`bet_desktop/tests/test_lightweight_hedge.py`

复核结论：当前阶段不得重复开发 exe 入口，不得替换 `LightweightProbeAdapter`，不得回退旧 UI worker。资源诊断应作为独立一次性工具或报告输出，不应进入轻量主界面运行循环。

## 3. 已有资源能力复核结论

当前检查结论：

- `specs/lightweight-hedge-console-stability/stage-11-resource-diagnostics-task.md` 原先不存在，本文件为阶段 11 独立审核任务文档。
- 当前轻量 UI 源码未发现阶段 11 专用资源诊断脚本、主界面资源面板或容量估算报告入口。
- `live_logs/resource_monitor_*.jsonl` 是历史资源监控日志，只能作为“存在历史记录”的证据，不得直接当作当前阶段 11 验收数据。
- `artifacts/codex_backups/runtime_resource_20260624_004131/` 是历史备份痕迹，不得直接恢复或搬入轻量控制台。
- `scripts/manual_headless_handoff_monitor.py`、`scripts/monitor_live_execution.py` 属于运行/日志监视类脚本，不是阶段 11 的一次性系统资源容量报告，不得作为常驻资源监控方案复用。
- 当前轻量 UI 已有 `QTimer` 用于既有运行事件轮询；阶段 11 不得新增资源相关 QTimer 或后台循环。

复核结论：已有部分历史资料，但没有当前轻量控制台合规的一次性资源诊断报告能力。执行 agent 只允许补最小的一次性诊断工具和报告格式，不允许重复开发 UI 面板或常驻监控。

## 4. 阶段 11 独立目标

阶段 11 目标是回答用户两个问题：

- 当前运行时总体资源消耗是多少，包括系统总量、UI/Python/PyInstaller 进程、Chromium/Chrome 进程。
- 在本机上，按保守规则预计可同时启动多少套轻量 UI。

交付形式必须是一次性诊断报告：

- 可由命令行手动触发。
- 可在验收期对已启动的 exe 做一次 15 秒以上采样。
- 报告写入文件或控制台摘要。
- 不进入主界面常驻显示。
- 不新增后台刷新、后台采样、资源曲线、资源定时器。

## 5. 当前可自动采样项与必须人工采样项

可由执行 agent 自动采样：

- 系统总量：CPU、总内存、可用内存。
- Python/PyInstaller 进程：`python.exe`、`pythonw.exe`、`LightweightHedgeConsole.exe` 等相关进程的数量、内存、CPU。
- Chromium/Chrome 进程：`chrome.exe`、`chromium.exe`、Playwright 相关浏览器进程数量、内存、CPU。
- 空闲 UI smoke 资源：允许启动 `dist/LightweightHedgeConsole/LightweightHedgeConsole.exe`，只打开和关闭窗口，不登录、不进房、不启动轻量控制、不下注，采样窗口不少于 15 秒。
- 报告格式测试：用模拟或当前系统进程数据验证报告字段和容量公式。

必须人工采样或最终人工 exe 测试时采集：

- 4 个账号登录后资源。
- 4 个账号进房后资源。
- 启动轻量控制后的资源。
- 真实平台页面长时间运行资源。
- 真实下注闭环中的 CPU、内存、浏览器进程变化。

人工采样项在自动报告中必须写为：

- `未采集：最终人工 exe 测试时采集`
- 不得填入推测值。
- 不得用历史 `live_logs/resource_monitor_*.jsonl` 冒充当前 exe 数据。

## 6. 允许修改或生成文件范围

允许新增或修改：

- `specs/lightweight-hedge-console-stability/stage-11-resource-diagnostics-task.md`
- `specs/lightweight-hedge-console-stability/tasks.md` 中的阶段 11 执行记录。
- `scripts/lightweight_resource_diagnostics.py` 或同等命名的一次性诊断脚本。
- `bet_desktop/tests/test_lightweight_resource_diagnostics.py` 或同等命名的报告格式/公式测试。
- `artifacts/resource-diagnostics/` 下的阶段 11 一次性报告文件。

允许读取但不修改：

- `LightweightHedgeConsole.spec`
- `bet_desktop/ui/run_lightweight_dashboard.py`
- `bet_desktop/ui/lightweight_dashboard.py`
- `bet_desktop/ui/lightweight_controller.py`
- `bet_desktop/ui/lightweight_probe_adapter.py`
- `bet_desktop/tests/test_lightweight_hedge.py`
- 历史 `live_logs/resource_monitor_*.jsonl`

## 7. 禁止修改或删除范围

禁止修改：

- 余额采集。
- 局号采集。
- 状态机判断。
- 倒计时或可下注信号判断。
- 房间号和限红读取。
- 坐标定位。
- 点击前检查。
- 真实点击执行器。
- `LightweightProbeAdapter` 的真实运行核心逻辑。
- 登录、接管、进房、下注相关控制逻辑。
- `LightweightHedgeConsole.spec` 的入口和输出名。
- 主界面新增资源面板、资源曲线、资源刷新间隔配置。

禁止删除：

- 既有安全阀门。
- 既有测试覆盖。
- 历史资源日志或历史备份文件，除非用户另行明确要求清理。

禁止行为：

- 为资源诊断启动登录。
- 为资源诊断进房。
- 为资源诊断启动下注控制。
- 为资源诊断使用真实账号动作。
- 编造 4 账号进房后或下注后的资源数据。
- 把一次性诊断脚本改成常驻循环服务。

## 8. 最小执行任务：一次性诊断报告，不常驻

执行 agent 最小任务：

- T11.1 复核最新 exe 路径：`dist/LightweightHedgeConsole/LightweightHedgeConsole.exe`。
- T11.2 复核入口仍为 `LightweightHedgeConsole.spec` 和 `bet_desktop/ui/run_lightweight_dashboard.py`。
- T11.3 检查当前代码是否已有合规一次性资源诊断工具，已有则只补缺口。
- T11.4 新增或完善一次性诊断脚本，采样窗口默认不少于 15 秒。
- T11.5 自动采样系统总量、UI/Python/PyInstaller 进程、Chromium/Chrome 进程。
- T11.6 如启动 exe，只允许空闲 smoke：启动窗口、等待采样、关闭窗口。
- T11.7 输出报告到 `artifacts/resource-diagnostics/`，文件名带时间戳。
- T11.8 报告中单独列出“自动已采集”和“最终人工 exe 测试时采集”。
- T11.9 按容量估算公式给出保守建议套数。
- T11.10 补报告格式和公式测试。
- T11.11 更新 `tasks.md` 阶段 11 完成记录。
- T11.12 创建中文 commit。

## 9. 验收阀门和必须测试

执行前阀门：

- 工作区状态必须先记录。
- 若已有未提交代码改动，不能覆盖或回退。
- 必须先复核最新 exe 路径和源码入口。
- 必须确认本阶段不修改保护链路。

执行中阀门：

- 自动采样窗口不少于 15 秒。
- 自动采样不得登录、进房、下注或启动真实账号动作。
- 若 exe 无法启动，只记录失败原因，不为资源诊断修改运行核心。
- 若发现资源数据无法区分本项目和其他 Chrome 进程，报告必须注明“含环境噪音”或改用更保守估算。

必须测试：

- `python -m pytest bet_desktop\tests\test_lightweight_resource_diagnostics.py -q`，若新增该测试文件。
- `python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q`，确认未影响轻量核心。
- 一次性脚本 dry-run 或当前进程采样命令，确认能生成报告。

验收结果必须包含：

- 命令。
- 通过/失败。
- 失败原因一句话。
- 报告路径。
- 是否触碰保护链路。

## 10. 容量估算方法和保守规则

报告必须给出三项估算：

- 按内存可运行套数。
- 按 CPU 可运行套数。
- 按浏览器进程数量可运行套数。

基础公式：

- `内存套数 = floor((可用内存 * 0.7) / 单套增量内存)`
- `CPU套数 = floor(70 / 单套平均CPU百分比)`
- `浏览器套数 = floor(建议最大浏览器进程数 / 单套浏览器进程数)`
- `最终建议套数 = floor(min(内存套数, CPU套数, 浏览器套数) * 0.7)`

保守规则：

- 若无法测得单套增量内存，使用当前单套 UI 相关进程总内存作为单套增量内存。
- 若无法可靠区分浏览器进程归属，将所有同时间新增或可疑 Chrome/Chromium 进程计入单套浏览器进程。
- CPU 使用单套采样窗口内平均值；如平均值过低但峰值明显，报告同时列出峰值，并人工建议按峰值复核。
- `单套平均CPU百分比 <= 0` 时，不得给出无限套数；应标记 CPU 项无法估算，并由内存和浏览器项决定初步建议。
- 浏览器进程数量上限必须在报告中写明来源；没有项目历史上限时，只能作为“假设上限”参与估算。
- 最终建议套数必须取三项最小值后再乘以 0.7 安全系数并向下取整。
- 如果任一关键输入缺失，最终建议必须降级为“临时建议”，并列出需要人工补采的数据。

## 11. 风险监测点

执行 agent 必须重点监测：

- 是否误把资源诊断做进主界面或后台循环。
- 是否新增 QTimer、线程、async 常驻循环或后台刷新配置。
- 是否误启动登录、进房、下注、真实账号动作。
- 是否修改了余额、局号、状态机、倒计时、房间号、限红、坐标、预检、真实点击路径。
- 是否把历史资源日志当作当前 exe 采样结果。
- 是否遗漏 Chrome/Chromium 子进程导致容量估算偏乐观。
- 是否未扣除安全系数导致建议套数偏高。
- 是否把空闲 UI smoke 结果误写成 4 账号进房或下注运行结果。

## 12. 执行 agent 约束

执行 agent 必须遵守：

- 只按本文档执行阶段 11。
- 不得执行资源采样以外的业务动作。
- 不得改变轻量控制台运行核心。
- 不得修改真实下注、状态采集、坐标、预检或进房逻辑。
- 不得新增常驻监控 UI。
- 不得把旧 UI 的资源监控或历史 monitor 脚本搬入轻量主界面。
- 所有资源数据必须标注采样条件。
- 未采集的数据必须明确写“未采集”，不得推算成事实。
- 最终必须提交中文 commit。

阶段 11 完成记录格式：

```md
### 阶段 11 完成记录

- 完成时间：
- 变更文件：
- 最新 exe 相关源码复查结论：
- 是否触碰保护链路：
- 自动采样项：
- 人工保留采样项：
- 资源报告路径：
- 容量估算结论：
- 自动测试：
- 未验证项：
- 风险结论：
- commit hash：
- 下一步：
```

## 13. 中文 commit 建议

建议提交信息：

`docs: 发布阶段11资源诊断任务`

若执行 agent 后续实现一次性诊断脚本，建议提交信息：

`tools: 添加轻量控制台一次性资源诊断报告`

## 14. 执行记录

- 完成时间：2026-06-27 11:56:07。
- 执行结论：通过。已新增一次性资源诊断脚本、格式/公式测试和阶段 11 报告。
- 资源报告路径：`artifacts/resource-diagnostics/stage11_resource_diagnostics_20260627_115607.md`。
- 自动采样项：系统总量、Python/PyInstaller/`LightweightHedgeConsole.exe`、Chrome/Chromium、空闲 exe smoke。
- 人工保留采样项：4 账号登录后、4 账号进房后、启动轻量控制后、真实平台长时间运行、真实下注闭环资源；报告中均标记为“未采集：最终人工 exe 测试时采集”。
- 容量估算结论：空闲 smoke 合计约 315.9 MB；因未启动浏览器，最终建议套数无法估算，临时建议套数为 23。
- 合规结论：未新增常驻资源监控线程、后台定时器、主界面资源曲线或资源刷新配置；未登录、未进房、未启动下注控制；未修改下注核心、状态采集、坐标、预检或真实点击链路。
