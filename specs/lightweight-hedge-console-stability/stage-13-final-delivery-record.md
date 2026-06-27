# 阶段 13 最终封版交付记录

- 执行 agent：gpt-5 Codex 执行 agent
- 执行时间：2026-06-27 12:16:23 +08:00
- 工作目录：`H:\d\bocai_web`
- 执行前 commit：`50ad9d81e5b9acc256cc56dc18ae40dfc658b977`
- 执行后 commit：见最终封版提交；同一提交内无法自引用最终 hash。
- 执行前工作区状态：仅有 `specs/lightweight-hedge-console-stability/stage-13-final-delivery-task.md` 未跟踪，属于阶段 13 已审核通过的输入文档。

## 源码路径复查

- `LightweightHedgeConsole.spec`：指向 `bet_desktop\ui\run_lightweight_dashboard.py`，输出名仍为 `LightweightHedgeConsole`。
- `bet_desktop\ui\run_lightweight_dashboard.py`：仍创建 `LightweightController(adapter=LightweightProbeAdapter())` 并加载 `LightweightDashboard`。
- 未修改 `LightweightHedgeConsole.spec`。
- 未修改业务代码。

源码路径 hash：

- `LightweightHedgeConsole.spec`：`1671C599FACBB4F13A4C577DA606D5E1EE37375C220FE5EBF12B5E8FCC05EA29`
- `bet_desktop\ui\run_lightweight_dashboard.py`：`1F1E19F4487803E68E2E61397F8D10A1B1C93ECE81DB9C10F3921CD0B7188879`
- `bet_desktop\ui\lightweight_dashboard.py`：`9AF749C2712B56D47B1407E92FA33BA3B03319DDA04D1B907EEC79E8D43ED886`
- `bet_desktop\ui\lightweight_controller.py`：`8CA89BB53F4D58A8AD7A90E16E2A777256084FDCE804DD52683B709866263C56`
- `bet_desktop\ui\lightweight_probe_adapter.py`：`457CED8EF4CE33B578348E6B0E6C2F1A23F8B86F3ABCF183066F8A08876CC86F`

## 自动测试

- 命令：`python -m pytest bet_desktop\tests\test_lightweight_hedge.py bet_desktop\tests\test_lightweight_resource_diagnostics.py -q`
- 结果：通过，`86 passed in 29.16s`。

## 最终打包

- 命令：`pyinstaller --clean --noconfirm LightweightHedgeConsole.spec`
- 结果：通过，退出码 `0`。
- exe 路径：`H:\d\bocai_web\dist\LightweightHedgeConsole\LightweightHedgeConsole.exe`
- exe 大小：`6,630,747 bytes`
- exe 生成时间：`2026-06-27 12:15:31`
- exe SHA256：`CD84B5433A1C806A166A4F84A2F72E6A72429DD56C8A849C6C7A430BF1BED325`

## 非登录 Smoke

- 操作：启动 `LightweightHedgeConsole.exe`，等待窗口创建后发送关闭窗口请求。
- 结果：通过。
- 进程 ID：`5308`
- 主窗口句柄：`12911940`
- 关闭方式：`CloseMainWindow()` 返回 `True`。
- 退出结果：进程已退出。
- 残留进程检查：`LightweightHedgeConsole.exe` 残留进程数为 `0`。
- 未登录、未打开真实账号登录页、未进房、未接管、未下注。

## 保护链路结论

- 是否修改业务代码：否。
- 是否触碰保护链路：否。
- 未修改下注、坐标、余额、局号、状态机、倒计时/可下注、房间、限红、preflight 或真实点击链路。
- 是否真实登录/进房/下注：否。

## 人工最终测试清单

以下项目保留给人工使用最终 exe 验证，执行 agent 未替代：

1. 登录配置：平台网址、账号、密码、房间、金额、点击间隔等配置读取正确。
2. 代理：a1/a2/a3/a4 各自代理配置和浏览器启动行为符合预期。
3. 打开登录页：人工点击打开登录页，确认页面可打开且配置未串号。
4. 批量填登录：人工确认批量填写账号密码可用。
5. 接管：人工登录后确认接管浏览器状态正常。
6. 1/2/3 房进房：人工测试进入 1 号房、2 号房、3 号房的真实页面动作。
7. 4 号和 3 号降级/恢复：人工验证 4 账号运行、剔除 1 个后 3 账号继续、恢复后回到 4 账号。
8. 局号/余额/可下注展示：人工观察真实平台局号、余额、可下注状态和倒计时展示。
9. 启动轻量控制真实下注：人工在确认配置和风险后启动真实下注观察，不由执行 agent 操作。
10. 资源采样：人工在登录后、进房后、启动轻量控制后分别触发或记录资源采样。

## 未自动验证项

- 真实平台登录。
- 真实账号登录页打开和批量填登录。
- 真实进房、接管和房间切换。
- 真实余额变化、真实局号变化、真实可下注窗口。
- 真实下注结果和真实点击闭环。
- 登录后、进房后、启动轻量控制后的真实资源采样。
- frozen exe 在真实平台深层流程中是否完全等同源码运行。

## 回退点

- 最新稳定 commit：`50ad9d81e5b9acc256cc56dc18ae40dfc658b977`
- 阶段 12 审核文档内记录的前一执行基线：`084cbe953a9ec45fee5c12e354990c2833e6cd70`
- 若人工测试发现真实登录、进房或下注闭环失败，回到阶段 7、8 或 9 对应范围做最小修复。
- 若人工测试发现 frozen exe 与源码行为不一致，回到阶段 10 重新打包和验证。

## 交付结论

- 自动测试通过。
- 最终 exe 重新打包通过。
- 非登录 smoke 通过。
- 允许交付人工最终测试。
