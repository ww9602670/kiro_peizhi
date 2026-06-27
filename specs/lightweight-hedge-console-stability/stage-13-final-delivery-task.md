# 阶段 13 最终交付封版任务编排文档

## 1. 审核结论

- 审核结论：通过。
- 是否允许执行：允许另一个独立 `gpt-5.5` 执行 agent 执行阶段 13。
- 任务性质：最终封版记录、最终 exe 重新打包、非登录 smoke test、交付给人工最终测试。
- 执行边界：阶段 13 不允许修改业务代码，不允许补改下注、坐标、余额、局号、状态机、倒计时/可下注、房间、限红、preflight 或真实点击链路。
- 人工边界：执行 agent 只做非登录 smoke，不得真实登录、不得进房、不得接管、不得下注。真实平台最终测试只能由人工完成。

## 2. 执行前复查清单

执行 agent 开始前必须重新复查并记录以下信息，不能直接沿用本文审核时刻数据：

- 当前工作目录：`H:\d\bocai_web`
- 当前 commit：执行前运行 `git rev-parse HEAD` 并记录完整 hash。
- 当前工作区状态：执行前运行 `git status --short`；若存在与阶段 13 无关的改动，不得覆盖或回退，必须先记录并停止确认。
- 最新 exe 路径：`dist\LightweightHedgeConsole\LightweightHedgeConsole.exe`
- 最新 exe 对应源码/入口路径：
  - `LightweightHedgeConsole.spec`
  - `bet_desktop\ui\run_lightweight_dashboard.py`
  - `bet_desktop\ui\lightweight_dashboard.py`
  - `bet_desktop\ui\lightweight_controller.py`
  - `bet_desktop\ui\lightweight_probe_adapter.py`
- 执行前必须核对上述文件的修改时间、大小和 hash，并确认打包入口仍指向轻量控制台，不是旧 UI。
- 本审核发布时参考状态：
  - HEAD：`50ad9d81e5b9acc256cc56dc18ae40dfc658b977`
  - 工作区：发布本文前为干净；发布本文后会出现阶段 13 文档新增改动。
  - 阶段 12 文档记录的执行基线 commit：`084cbe953a9ec45fee5c12e354990c2833e6cd70`

## 3. 保护链路边界

阶段 13 是交付和打包阶段，不是修复阶段。以下链路全部禁止修改：

- 下注计划、筹码拆分、下注金额和真实下注执行。
- 坐标识别、坐标缓存、坐标刷新时机和点击坐标。
- 余额读取、余额更新、低余额剔除和余额不足处理。
- 局号读取、局号停更标记和局号同步判断。
- 状态机、可下注状态、展示倒计时和真实点击前可下注检查。
- 房间号、目标房间、进房、回房、房间绑定和房间过滤。
- 限红读取、限红过滤和下注前限红判断。
- preflight、点击前检查、真实点击、确认等待、点击耗时记录。

如最终打包或 smoke 发现问题，执行 agent 只能记录失败和风险，不得自行进入业务代码修复。

## 4. 允许和禁止

允许：

- 更新本阶段 13 记录。
- 更新 `specs\lightweight-hedge-console-stability\tasks.md` 中阶段 13 的完成记录。
- 更新必要交付记录，例如记录最终 exe 路径、大小、生成时间、来源 commit、测试命令和 smoke 结果。
- 重新打包生成 `dist\LightweightHedgeConsole\LightweightHedgeConsole.exe` 及 PyInstaller 相关构建产物。

禁止：

- 修改业务代码、UI 运行逻辑、controller、adapter、下注核心、状态采集、坐标、preflight 或真实点击链路。
- 修改 `LightweightHedgeConsole.spec`，除非先停止并说明纯打包配置阻塞，且取得新的明确指令。
- 真实登录、打开真实账号会话、进房、接管浏览器或下注。
- 为了让 smoke 通过而删除检查、跳过安全阀门、伪造测试或伪造人工验证结果。

## 5. 阶段 13 最小执行任务

1. 执行前复查当前 commit、工作区、最新 exe 和五个核心路径。
2. 运行指定自动测试。
3. 使用现有 `LightweightHedgeConsole.spec` 重新打包最终 exe。
4. 检查最终 exe 是否存在，并记录大小和生成时间。
5. 启动/关闭 exe 做非登录 smoke test。
6. 确认 smoke 后无残留 `LightweightHedgeConsole.exe` 进程。
7. 更新阶段 13 完成记录和必要交付记录。
8. 创建中文 commit，记录阶段 13 最终交付封版。

## 6. 验收命令

必须执行并记录结论：

```powershell
python -m pytest bet_desktop\tests\test_lightweight_hedge.py bet_desktop\tests\test_lightweight_resource_diagnostics.py -q
```

```powershell
pyinstaller --clean --noconfirm LightweightHedgeConsole.spec
```

检查 exe 存在、大小、时间：

```powershell
Get-Item dist\LightweightHedgeConsole\LightweightHedgeConsole.exe | Select-Object FullName, Length, LastWriteTime
```

启动/关闭 exe smoke：

- 只允许打开窗口、确认无启动崩溃、关闭窗口。
- 不登录、不打开真实登录页、不进房、不接管、不下注。
- smoke 后必须确认无残留 `LightweightHedgeConsole.exe` 进程。

可用检查命令：

```powershell
Get-Process LightweightHedgeConsole -ErrorAction SilentlyContinue
```

## 7. 回退点

- 最新稳定 commit：`50ad9d81e5b9acc256cc56dc18ae40dfc658b977`，阶段 12 封版审核通过后的当前 HEAD。
- 阶段 12 commit：`50ad9d81e5b9acc256cc56dc18ae40dfc658b977`。
- 阶段 12 审核文档内记录的前一执行基线：`084cbe953a9ec45fee5c12e354990c2833e6cd70`。
- 如果阶段 13 打包失败或 smoke 失败，只记录失败并停止；不得回退或修改代码。后续由人工决定是否回到阶段 10 或对应问题阶段做最小修复。

## 8. 人工最终测试清单

以下项目必须交给人工使用最终 exe 测试，执行 agent 不得替代、不得伪造：

1. 登录配置：确认平台网址、账号、密码、房间、金额、点击间隔等配置读取正确。
2. 代理：确认 a1/a2/a3/a4 各自代理配置和浏览器启动行为符合预期。
3. 打开登录页：人工点击打开登录页，确认页面可打开且配置未串号。
4. 批量填登录：人工确认批量填写账号密码可用。
5. 接管：人工登录后确认接管浏览器状态正常。
6. 1/2/3 房进房：人工测试进入 1 号房、2 号房、3 号房的真实页面动作。
7. 4 号和 3 号降级/恢复：人工验证 4 账号运行、剔除 1 个后 3 账号继续、恢复后回到 4 账号。
8. 局号/余额/可下注展示：人工观察真实平台局号、余额、可下注状态和倒计时展示。
9. 启动轻量控制真实下注：人工在确认配置和风险后启动真实下注观察，不由执行 agent 操作。
10. 资源采样：人工在登录后、进房后、启动轻量控制后分别触发或记录资源采样。

## 9. 风险判断

- 打包风险：PyInstaller 可能因依赖、路径、资源文件或旧构建缓存导致最终 exe 与源码启动表现不同；阶段 13 通过 `--clean` 重新打包并记录产物信息降低风险，但不能消除全部风险。
- frozen exe 与源码运行差异风险：登录页、配置读取、浏览器接管、进房、真实平台长时间运行等深层行为仍可能与源码模式不同，必须由人工最终测试确认。
- 真实平台人工测试剩余风险：真实登录、真实房间、真实余额/局号变化、真实可下注窗口、真实下注结果和资源压力均未由自动阶段验证；不得伪造已经人工验证。

## 10. 执行 agent 完成记录格式

执行 agent 完成后必须在阶段 13 记录中写明：

- 执行 agent：
- 执行时间：
- 执行前 commit：
- 执行后 commit：
- 执行前工作区状态：
- 源码路径复查结论：
- 测试命令和结果：
- 打包命令和结果：
- exe 路径、大小、生成时间：
- smoke test 结论：
- 残留进程检查结论：
- 是否修改业务代码：必须为否。
- 是否真实登录/进房/下注：必须为否。
- 人工最终测试保留项：
- 风险结论：
- 是否允许交付人工测试：

## 11. 中文 commit 建议

```text
记录阶段13最终交付封版
```

## 12. 最终允许结论

允许另一个独立 `gpt-5.5` 执行 agent 执行阶段 13。执行 agent 只能按本文档完成最终 exe 重新打包、非登录 smoke、交付记录和中文 commit；不得执行真实平台人工测试，不得修改业务代码。
