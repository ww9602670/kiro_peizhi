# AGENTS.md

## Highest Preservation Rule

- The currently debugged and integrated runtime path for balance, round id, state machine, countdown, room id, table limit, and coordinate positioning is protected as the project's highest-priority preservation area.
- If a future instruction may touch code related to these working paths, do not modify it immediately. First explain the likely risks, including possible regressions in UI display, data-source filtering, state synchronization, countdown accuracy, room/limit binding, balance updates, or coordinate positioning.
- Only after discussion and a renewed explicit instruction to proceed may the code be changed. The implementation must then be the smallest possible change that satisfies the new instruction, with focused verification afterward.
- This rule applies even when the requested change is framed as cleanup, optimization, refactor, process reduction, browser/runtime restructuring, logging changes, or UI adjustment.

## 完整版源码备份与恢复

- 当前“完整版”打包所对应的完整源码基线已备份到远端分支：`origin/codex/完整版源码备份-20260702`。
- 对应功能源码提交：`8be365c2c 备份：完整版打包源码-随机计划与实际主号统计`。
- 如果后续代码被改乱，或用户要求基于“完整版”进行完整修改、升级、重构、重新打包，应先从该远端分支拉取干净基线，不要把未知本地改动混入恢复分支。
- 推荐恢复命令：

```powershell
git fetch origin
git switch -c codex/restore-full-version-YYYYMMDD origin/codex/完整版源码备份-20260702
```

- 打包入口仍为 `LightweightHedgeConsole.spec`，spec 指向 `bet_desktop\ui\run_lightweight_dashboard.py`，并由 PyInstaller 追踪入口和隐藏依赖。
- 重新打包“完整版”可使用：

```powershell
python -m PyInstaller --noconfirm --clean `
  --distpath "H:\d\bocai_web\dist\完整版_build" `
  --workpath "H:\d\bocai_web\build\完整版" `
  "H:\d\bocai_web\LightweightHedgeConsole.spec"
```

- 打包后将 `H:\d\bocai_web\dist\完整版_build\LightweightHedgeConsole\` 整理为 `H:\d\bocai_web\dist\完整版\`。
- Git 分支保存源码、测试和打包 spec；不保存 `dist\完整版` exe、本地账号配置、浏览器 profile、运行日志或其他外部环境状态。这些如需完全复现现场，应另行备份。

## Output Discipline

- You may read files, logs, diffs, and test output as needed for reasoning.
- Do not print full file contents, long logs, full diffs, or full test output to the CLI unless explicitly requested.
- Do not reprint archived, saved, or already-dumped content into the CLI unless explicitly requested.
- Default output must be limited to:
  - path
  - conclusion
  - minimal evidence excerpt only when necessary
  - next action
- Do not use full-file raw output as the default reporting style.
- Prefer targeted excerpts over full-file reads in the transcript.
- Prefer `git diff --name-only` before showing any detailed diff.
- For test output, default to:
  - command executed
  - pass/fail counts
  - failing test names
  - one-sentence root cause
  - minimal traceback excerpt only if necessary
- Prefer summaries over raw output.
- Keep CLI output compact and avoid transcript bloat.

## Evidence Discipline

- Do not omit critical evidence when evidence is required for correctness, debugging, or acceptance.
- When evidence must be shown, expand only the smallest excerpt necessary to support the conclusion.
- If a correct decision cannot be made without more raw evidence, say so explicitly and then expand minimally.

## Long-Task Discipline

- For long tasks, do not narrate every micro-step.
- Work in phases and summarize only at meaningful checkpoints.
- At each checkpoint, report only:
  - what was completed
  - which files changed
  - what was verified
  - current blocker or next step
- Do not repeatedly restate unchanged background.

## Non-Technical Progress Reporting

- The user is a non-programmer.
- At meaningful checkpoints, provide a short plain-language progress update that a non-programmer can understand.
- Do not use code-heavy or overly technical language unless necessary.
- Each progress update should be brief and include only:
  - what has been completed
  - what is currently being checked or fixed
  - whether there is any blocker
  - what the next step is
- Keep progress updates simple, concrete, and easy to understand.
- Do not turn progress reporting into long narration.
- If the task is long-running, provide checkpoint-style updates instead of step-by-step commentary.

## Prohibitions

- Do not use full file dumps as status updates.
- Do not use reprinted archived content as evidence.
- Do not flood the CLI with long code, long JSON, long HTML, or long logs unless explicitly required.
- Do not generate transcript noise just to appear active.

## Priority

Correctness first.  
Compact output second.  
Never sacrifice correctness, evidence, or acceptance quality just to reduce output.
- At the end of each phase or gate, include a short non-technical progress summary for the user.
