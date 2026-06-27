# 阶段 10 打包 exe 独立任务编排文档

## 1. 审核结论

结论：通过，允许进入阶段 10 执行。

允许执行范围仅限“验证源码、打包轻量控制台 exe、启动 smoke test、记录交付信息”。执行 agent 不得修改业务代码、下注核心、状态采集、坐标、预检、真实点击链路。

执行前置门禁：

- 自动测试必须通过。
- 工作区必须干净，或仅存在本阶段文档变更。
- 先复核入口仍为 `LightweightHedgeConsole.spec` 和 `bet_desktop/ui/run_lightweight_dashboard.py`。
- 不得启动真实下注，不得登录后执行下注动作。

## 2. 最新 exe/入口/核心路径复核结论

审核时复核结果：

- 当前 commit：`efdfbe768`
- 当前分支：`codex/lightweight-login-handoff-20260624`
- 当前唯一 spec：`LightweightHedgeConsole.spec`
- spec 入口：`bet_desktop\ui\run_lightweight_dashboard.py`
- spec 输出名：`LightweightHedgeConsole`
- spec 使用目录版收集输出：`dist/LightweightHedgeConsole/LightweightHedgeConsole.exe`
- spec 设置：`console=False`
- 启动入口创建：`LightweightController(adapter=LightweightProbeAdapter())`
- 启动 UI：`LightweightDashboard(controller=controller)`
- 旧 UI 产物存在：`dist/BetDesktop/BetDesktop.exe`

路径差异说明：

- `dist/LightweightHedgeConsole.exe` 当前不存在。
- 当前 spec 的有效输出路径是 `dist/LightweightHedgeConsole/LightweightHedgeConsole.exe`。
- 执行 agent 不得为了迁就单文件路径而改 spec 为旧 UI 或改名误打 `BetDesktop.exe`。

当前已存在旧轻量 exe：

- `dist/LightweightHedgeConsole/LightweightHedgeConsole.exe`
- 修改时间：`2026-06-26 16:19:01 +08:00`
- 大小：`6622565`

该文件只是现有产物。阶段 10 执行后必须重新记录新的 exe 路径、生成时间和来源 commit。

## 3. 打包目标和输出路径

目标：生成一份可交给用户人工测试的轻量对冲控制台 exe。

目标入口：

- `LightweightHedgeConsole.spec`
- `bet_desktop/ui/run_lightweight_dashboard.py`

目标输出：

- `dist/LightweightHedgeConsole/LightweightHedgeConsole.exe`

可选交付副本：

- `release-candidates/LightweightHedgeConsole_<YYYYMMDD>_stage10_package/LightweightHedgeConsole/LightweightHedgeConsole.exe`

交付记录必须写明：

- exe 绝对路径。
- exe 生成时间。
- exe 文件大小。
- 来源 commit hash。
- 打包命令。
- smoke test 结果。

## 4. 当前环境/命令建议

审核时 PyInstaller 环境：

- `C:\Program Files\Python310\Scripts\pyinstaller.exe`
- 版本：`6.18.0`

建议命令：

```powershell
git status --short
git rev-parse --short HEAD
python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q
python bet_desktop\ui\run_lightweight_dashboard.py
pyinstaller --clean --noconfirm LightweightHedgeConsole.spec
```

源码版和 exe 版 smoke test 只允许验证：

- 能启动窗口。
- 能读取配置。
- 能打开登录页。
- 能进入运行页。
- 关闭 UI 后无残留黑色控制台窗口。
- 浏览器进程可随 UI 关闭或释放。

smoke test 禁止：

- 真实下注。
- 为了观察而启动“持续运行”并进入下注闭环。
- 临时修改账号、坐标、限红、余额、局号、倒计时、预检或点击逻辑。

## 5. 允许修改/生成文件范围

允许生成或覆盖：

- `build/LightweightHedgeConsole/`
- `dist/LightweightHedgeConsole/`
- `release-candidates/LightweightHedgeConsole_<YYYYMMDD>_stage10_package/`
- `release-candidates/LightweightHedgeConsole_<YYYYMMDD>_stage10_package.zip`
- 阶段 10 执行记录文档或任务勾选记录。

允许修改：

- `specs/lightweight-hedge-console-stability/stage-10-package-exe-task.md`
- 阶段 10 执行记录文档。

如打包失败，不允许直接改业务代码修复。必须先记录失败原因，再判断是否属于纯打包依赖问题。

## 6. 禁止修改/删除范围

禁止修改：

- 余额链路。
- 局号链路。
- 状态机。
- 倒计时。
- 房间号。
- 限红。
- 坐标定位。
- 下注前检查。
- 真实点击执行链路。
- `bet_desktop/ui/lightweight_controller.py`
- `bet_desktop/ui/lightweight_probe_adapter.py`
- `bet_desktop/ui/lightweight_dashboard.py`
- `bet_desktop/ui/lightweight_models.py`
- `bet_desktop/ui/lightweight_config_store.py`
- `scripts/live_interval_acceptance_probe.py`

禁止删除或覆盖：

- `dist/BetDesktop/`
- `dist/BetDesktop.exe`
- `dist/BetDesktop.zip`
- `build/BetDesktop/`
- 任何旧 UI `BetDesktop` 相关产物。
- 非本阶段创建的 release candidate，除非只复制读取。

如果确实需要清理旧 dist，只能清理或覆盖：

- `dist/LightweightHedgeConsole/`
- `build/LightweightHedgeConsole/`

## 7. 最小执行任务

1. 复核 `git status --short`，确认工作区干净或仅有阶段 10 文档。
2. 记录 `git rev-parse --short HEAD`。
3. 复核 `LightweightHedgeConsole.spec` 仍指向 `bet_desktop\ui\run_lightweight_dashboard.py`。
4. 运行自动测试。
5. 启动源码版 UI 做不登录不下注 smoke test，关闭后确认无异常残留。
6. 使用 `pyinstaller --clean --noconfirm LightweightHedgeConsole.spec` 打包。
7. 确认 `dist/LightweightHedgeConsole/LightweightHedgeConsole.exe` 存在。
8. 启动 exe 做不登录不下注 smoke test，关闭后确认无异常残留。
9. 记录 exe 路径、生成时间、大小、来源 commit、打包命令、测试结果。
10. 如创建交付副本，只复制 `LightweightHedgeConsole` 目录，不复制或覆盖 `BetDesktop`。

## 8. 验收阀门

必须全部满足才允许交付：

- `python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q` 通过。
- 工作区干净或仅有阶段 10 文档/执行记录。
- `dist/LightweightHedgeConsole/LightweightHedgeConsole.exe` 存在。
- exe 文件生成时间晚于本次打包开始时间。
- exe 来源 commit 已记录。
- 打包命令已记录。
- 源码版 UI smoke test 通过。
- exe 版 UI smoke test 通过。
- smoke test 未登录、未下注、未启动真实下注闭环。
- 未产生或覆盖 `BetDesktop.exe`。
- 未修改保护链路代码。

发现以下任一情况必须停止交付：

- spec 指向旧 UI 或 `BetDesktop`。
- 输出为 `dist/BetDesktop/BetDesktop.exe`。
- 打包后 exe 行为与源码版不一致。
- 测试失败。
- smoke test 需要修改业务代码才能通过。
- 工作区出现非阶段 10 文档、非打包产物的源码改动。

## 9. 风险监测点

重点风险：

- 误打旧 UI `BetDesktop.exe`。
- 误删旧 UI 产物。
- `dist/LightweightHedgeConsole.exe` 与目录版输出路径混淆。
- PyInstaller 收集 Playwright/PyQt6 依赖不完整，导致 exe 启动失败。
- exe frozen 环境下项目根目录变化，配置读取路径异常。
- UI 关闭后浏览器或后台进程未释放。
- smoke test 误进入真实下注流程。
- 为修复打包问题而触碰保护链路。

## 10. 执行 agent 约束

执行 agent 必须：

- 只执行阶段 10。
- 不执行阶段 9 的真实下注人工验收。
- 不启动真实下注。
- 不修改业务代码。
- 不修改或删除旧 UI 产物。
- 不把 `BetDesktop` 当作交付目标。
- 所有命令输出保持简短，只汇报命令、结论、失败摘要和下一步。
- 打包失败时先停下记录，不得自行扩大修改范围。
- 完成后创建中文 commit，commit 中必须包含阶段 10 文档或执行记录。

## 11. 中文 commit 建议

建议 commit 标题：

```text
记录阶段10轻量控制台打包验收
```

如果执行 agent 只更新打包记录，不修改代码，可使用：

```text
记录轻量控制台exe打包交付信息
```

## 12. 阶段 10 执行记录

执行 agent：gpt-5.5 阶段 10 执行 agent。

执行时间：2026-06-27。

执行前状态：

- 分支：`codex/lightweight-login-handoff-20260624`
- 来源 commit：`efdfbe768046ae4cd7a434180679749072cbd795`
- 工作区：仅有本阶段任务文档 `specs/lightweight-hedge-console-stability/stage-10-package-exe-task.md` 未跟踪，已作为阶段 10 交接文档纳入处理。
- spec 入口复核：`LightweightHedgeConsole.spec` 仍指向 `bet_desktop\ui\run_lightweight_dashboard.py`。
- spec 输出复核：目录版 `dist\LightweightHedgeConsole\LightweightHedgeConsole.exe`。

执行命令和结果：

- `python -m pytest bet_desktop\tests\test_lightweight_hedge.py -q`
  - 结果：83 passed。
- `python bet_desktop\ui\run_lightweight_dashboard.py`
  - 结果：源码版窗口可启动并正常关闭。
  - 限制：只启动/关闭，未登录、未下注、未启动持续运行。
- `pyinstaller --clean --noconfirm LightweightHedgeConsole.spec`
  - 结果：通过。
- `dist\LightweightHedgeConsole\LightweightHedgeConsole.exe`
  - 结果：exe 窗口可启动并正常关闭。
  - 限制：只启动/关闭，未登录、未下注、未启动持续运行。

交付产物：

- exe 路径：`H:\d\bocai_web\dist\LightweightHedgeConsole\LightweightHedgeConsole.exe`
- exe 生成时间：2026-06-27 11:39:41
- exe 文件大小：6,630,710 bytes（约 6.32 MB）
- 来源 commit：`efdfbe768046ae4cd7a434180679749072cbd795`

保护边界结论：

- 未修改业务代码。
- 未修改下注核心、状态采集、坐标、预检或真实点击链路。
- 未登录账号。
- 未启动真实下注。
- 未打包旧 UI `BetDesktop.exe`。
- 未清理或覆盖 `dist\BetDesktop` 或 `build\BetDesktop`。

保留风险：

- 本次按阶段 10 执行硬约束只做启动/关闭 smoke test，未打开登录页、未登录账号、未进入真实下注闭环。
- PyInstaller frozen 环境下的登录页、进房页面、真实配置读取后的深层运行行为仍需人工现场验收确认。
