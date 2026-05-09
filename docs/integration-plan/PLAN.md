# 共享池增强版 + 随机马丁回测整合迭代计划

> 日期：2026-05-10  
> 工作分支：`claude/integration-shared-collector-random-martin`（基于 main）  
> 目标：把服务器上跑的"shared_collector 增强版"代码（仓库里没有）拉回本地作为基线，把本地正在做的随机马丁/AI 策略/已知 bug 修复合并进去，本地跑通后再同步仓库，最后才考虑部署服务器。

---

## 一、背景与现状

### 1.1 服务器代码 vs 仓库代码（已确认事实）

| 项目 | 仓库（main HEAD `6a28b05`） | 服务器 `/opt/bocai_web/current` |
|---|---|---|
| `backend/app/engine/shared_market_runtime.py` | **1586 行** | **2228 行**（多 642 行） |
| `backend/app/database.py` | 712 行 | 740 行（多 28 行） |
| `shared_market_groups` 字段数 | 8 列 | **17 列**（多 9 列：collector_health 系列） |
| `shared_market_uncovered_urls` 字段数 | 13 列 | **22 列**（多 9 列：detection_attempts/next_detect_at 等） |
| `account_shared_market_routes` 表 | ❌ 无 | ✅ 存在 |
| `shared_market_alert_dedupe` 表 | ❌ 无 | ✅ 存在 |
| `random_backtest_tasks` / `random_plan_sets` 表 | ✅ 有（worktree） | ❌ 无 |
| `random_martin` 策略代码 | ✅ 有（worktree） | ❌ 无 |

**结论**：服务器跑的是 release 名 `6a28b05_shared_collector_20260504_064834`，但其代码内容并非 commit `6a28b05` 的内容——是仓库里**没有任何提交记录**的增强版。这642 行代码是部署"丢失"的源头。

### 1.2 服务器 DB 实际状态

- 共享组 1 个：`jnd28:https://8783288200-bty.mm555.co`，collector=`ceshi11`
- ceshi11 / jiance11 / ceshi118 / ceshi119 等账号**全部已软删除**（status=deleted）
- shared_market_snapshots 仅 1 条，最后刷新 `2026-05-09 14:20:15`
- `shared_market_uncovered_urls` id=37（同一 URL）状态 `review_required` / `no_shared_group_matched`——与已绑定的 shared_group 1 冲突，**逻辑漏洞待查**

### 1.3 用户提出的 4 个问题（待修）

| # | 问题 | 已分析根因 | 修复要求 |
|---|---|---|---|
| 1 | 管理员页面无法授权"随机马丁" | `Operators.tsx:34` 漏加选项 | 加入选项，**label 改为"AI马丁"** |
| 2 | 回测只能跑一次 | Phase 4 缺少"换日期再测"出口 | 加按钮 + 保留"直接选组建策略" |
| 3 | 操作员一直 SHARED-002 + 管理员一直显示"重新检测" | URL 已绑定却又被标 uncovered | **必须查清楚 detection 流程为什么误判** |
| 4 | "重新检测"按钮无效 | 只改 status，没真正调 detect | 改为同步触发 `discover_and_bind_uncovered_url` |

---

## 二、整合后的最终代码版本应该具备什么

1. **服务器增强版的所有 shared_collector 能力**：
   - shared_market_groups 健康监控字段 + 索引
   - shared_market_uncovered_urls 探测调度字段 + 索引
   - account_shared_market_routes（账号→组路由表）
   - shared_market_alert_dedupe（告警去重表）
   - 完整 642 行 shared_market_runtime.py 的额外逻辑

2. **本地随机马丁回测全套功能**：
   - random_backtest_tasks / random_plan_sets DDL
   - api/random_backtest.py（含修复后的 asyncio progress_cb）
   - engine/random_backtest_engine.py
   - engine/random_plan_generator.py（**修过的新唯一性规则**：4.3 亿组合）
   - engine/strategies/random_martin.py
   - frontend RandomBacktest.tsx

3. **AI 同号策略**（仓库已有，但需确认服务器没冲突）

4. **4 个 bug 全部修复**

5. **DB 迁移机制**：把 ALTER TABLE 加列 + CREATE TABLE 写进 `database.py`，启动时自动迁移

---

## 三、阶段任务（A → B → C → D）

### 阶段 A：把服务器增强版代码反向拉回作为基线

**目标**：本地仓库出现一个 commit，包含服务器上线下来的全部 shared_collector 增强代码。

**步骤**：
1. 创建集成分支：从 main 切 `feat/integration-shared-collector-baseline`
2. SSH 拉取服务器关键文件到本地：
   - `backend/app/engine/shared_market_runtime.py`（2228 行）
   - `backend/app/database.py`（740 行）
   - `backend/app/api/admin.py`
   - `backend/app/engine/worker.py`
   - `backend/app/engine/alert.py`
   - `backend/app/models/db_ops.py`
   - `backend/app/schemas/lottery.py`
   - `backend/app/schemas/strategy.py`
   - `backend/app/main.py`（注册路由可能有变化）
   - `frontend/src/pages/admin/Operators.tsx`
   - `frontend/src/pages/admin/Operators.css`
   - `frontend/src/types/api/strategy.ts`
   - `frontend/src/api/admin.ts`
3. 用 `diff` 全部检查替换是否合理（不能误删 main 已有的功能）
4. 把服务器代码（CRLF 已规范化）覆盖本地
5. 跑 `python -c "import ast; ast.parse(open('...').read())"` 校验语法
6. **commit**: `feat: import shared_collector enhancements from production server (2026-05-04 release)`
7. **不 push**——本地基线先验证

**输出物**：
- 集成分支已建立
- 一份 `docs/integration-plan/IMPORT_DIFF.md`（自动生成）：列出每个被覆盖文件的行数变化和关键新增能力

**验收标准**：
- 后端能 `python -c "from app.main import app"` 导入成功
- 启动 uvicorn 不报 ImportError

---

### 阶段 B：把本地未提交的功能/修复合并到基线之上

**B.1 — 合并随机马丁回测功能**
- 把 worktree 当前所有 random_* 文件 cherry-pick / 复制到集成分支
- 包括我之前修过的：
  - `engine/random_plan_generator.py`（新唯一性规则）
  - `api/random_backtest.py`（asyncio progress_cb 修复）
  - `write_queue.py`（asyncio future 防护）
- DDL 新表加进 `database.py`

**B.2 — 修复 4 个 bug**

**B.2.1**（前端）— 在 `frontend/src/pages/admin/Operators.tsx` 的 `STRATEGY_PERMISSION_OPTIONS` 数组追加：
```ts
{ value: 'random_martin', label: 'AI马丁' }
```

**B.2.2**（前端）— `frontend/src/pages/operator/RandomBacktest.tsx` Phase 4：
- 保留现有"创建随机马丁策略"按钮（首次回测后直接选组建策略——已经是这个流程，确认即可）
- 新增按钮"换日期再回测（仅保留所选组）"：
  - 点击后回到 Phase 3（保留 selectedGroups）
  - Phase 3 标题切换为"二次回测：N 组"，提示当前是子集回测
  - 新建一个 plan_set（仅含选中组）或调用现有 API 时只传子集 plans
  - 完成后回到 Phase 4，展示新结果，supports 多次迭代
- 加一个"重置（回到初次方案集）"按钮便于跳回完整方案

**B.2.3**（后端 + 调研报告）— shared market 误标 uncovered 问题
- **必须先输出调研报告** `docs/integration-plan/SHARED_URL_DETECTION_INVESTIGATION.md`：
  - 阅读阶段 A 拉回的 `shared_market_runtime.py` 中 `_discover_url_group_match` 全流程
  - 解释为什么 `https://8783288200-bty.mm555.co` 已经在 `shared_market_group_urls` 里却仍被 `discover_and_bind_uncovered_url` 标为 `no_shared_group_matched`
  - 候选原因列表（逐一确认/排除）：
    - platform_type 不匹配（ceshi118 是 JND28WEB，组是 JND282）
    - 路径/参数差异导致 normalized_url 不一致
    - 时序问题：URL 还没被绑定时就触发了 detection
    - 探测逻辑只查询 enabled=1 但配置错了
  - 输出"应该如何修"的具体代码改动建议
- 再根据报告做代码修复

**B.2.4**（后端 + 前端）— "重新检测"按钮真正触发检测
- 后端 `/admin/shared-market-uncovered-urls/{id}/recheck` 改为：
  ```python
  # 1. 把状态置 detecting
  # 2. 调用 shared_market_runtime.discover_and_bind_uncovered_url(...)
  # 3. 返回检测结果（matched 还是 review_required）
  ```
- 前端按钮提示文案改为"立即检测中..."loading 状态，结果出来后弹 toast 显示成功/失败原因

**B.3 — 编写迁移脚本**
- 在 `database.py` `init_db()` 中加入 `_migrate_*` 函数：
  - 检测 `shared_market_groups` 是否缺 collector_health 系列列 → ALTER ADD
  - 检测 `shared_market_uncovered_urls` 是否缺 detection_attempts 系列列 → ALTER ADD
  - 检测 `account_shared_market_routes` / `shared_market_alert_dedupe` 是否存在 → CREATE
- 启动时自动跑迁移，老 DB 平滑升级

**输出物**：
- 各 commit 颗粒清晰（一个 bug 一个 commit + 一个 feat 一个 commit）
- `SHARED_URL_DETECTION_INVESTIGATION.md` 报告

---

### 阶段 C：本地完整测试

**C.1 — 重置/初始化测试 DB**
- 备份当前 worktree 的 `backend/data/bocai.db`
- 启动后端，让 `init_db()` 自动跑迁移补齐 schema
- 验证：所有目标表存在，所有目标列存在

**C.2 — 创建测试数据（脚本化）**
- `scripts/seed_shared_market_test.py`：
  - 创建测试 operator `op_test`（密码 `test123`）
  - 创建 admin（如果没有）
  - 创建一个共享组：group_key=`jnd28:https://test-shared-mock.example`，collector=`jiance11_test`
  - 把 URL 绑定到该组
  - 不要插入 ceshi11/jiance11 真账号——使用 mock URL 避免连真平台

**C.3 — 后端单元测试**
- `pytest backend/tests/`，确认无回归
- 重点跑：
  - `test_database.py`（schema）
  - `test_admin.py`（recheck 接口）
  - `test_shared_market_runtime.py`（detection）

**C.4 — 端到端手工测试场景**

| 场景 | 步骤 | 预期 |
|---|---|---|
| 1. 管理员授权 AI马丁 | 登录 admin → 操作员管理 → 编辑 op_test → 勾选 "AI马丁" → 保存 | 保存成功；下拉列表里能看到该选项 |
| 2. 操作员看到 AI马丁选项 | 登录 op_test → 创建策略 → 类型选择 | 能选 AI马丁 |
| 3. 随机马丁回测 700 组 | 配置 G=700, K=5, N=3 | 能正常生成（验证唯一性新规则） |
| 4. 多次回测筛选 | 首次回测后 → 选 200 组 → 点"换日期再测" → 选另一天 | 只对 200 组重测，结果展示正确 |
| 5. 直接建策略 | 首次回测后 → 选组 → "创建AI马丁策略" | 跳到 StrategyForm，预填正确 |
| 6. 共享组健康可见 | 管理员 → 共享市场页 | 看到健康状态、最后采集时间 |
| 7. 重新检测立即生效 | 管理员 → uncovered_urls → 重新检测 | 立刻返回 matched/failed，不是仅"已标记" |
| 8. URL 误标问题不再发生 | 配置一个 URL 已绑定到组 → 用账号触发 detection | 不会进 uncovered_urls；如果之前误进了，重新检测能一次性修复 |

**C.5 — 性能 / 回归确认**
- 启动 uvicorn 跑 30 分钟无 SHARED-002 误报（mock 共享组健康）
- 浏览器 console 无报错
- 后端 log 无未处理异常

**输出物**：
- `docs/integration-plan/LOCAL_TEST_REPORT.md`：每个场景的截图/日志/结论

---

### 阶段 D：同步到仓库

**前置条件**：阶段 C 全部场景 ✅，测试报告完整

1. `git rebase main`（解决冲突）
2. 集成分支 push 到 `origin` 和 `web` 两个 remote
3. PR 描述包含本 plan 摘要 + 测试报告链接
4. 用户 review → merge 到 main

---

### 阶段 E：服务器部署（**plan 范围之外，先不做**）

- 备份服务器 DB
- 部署新版 release
- 服务器启动后跑迁移
- 烟雾测试 ceshi118 真实登录场景

---

## 四、Agent 编排建议

按"先线性、关键节点回报"原则：

| 节点 | 谁来做 | 是否并行 | 备注 |
|---|---|---|---|
| A 拉服务器代码 + commit baseline | 主线 | 否 | 必须先有基线 |
| A 后立即做 IMPORT_DIFF 报告 | 主线 | 否 | 给用户 review |
| **暂停点 1**：用户 review baseline 是否接受 | 用户 | — | 同意后才进 B |
| B.1 合并随机马丁 | 主线 | 否 | 涉及 schema |
| B.2.1 加权限选项（"AI马丁"） | 子任务 | 可并行 B.1 完成后 | 简单 |
| B.2.2 多次回测 + 直接建策略 | 子任务 | 可并行 | 中等 |
| B.2.3 调研 SHARED 误标 | 子任务 | **必须先于 B.2.4** | 出报告 |
| B.2.4 recheck 真触发 detect | 子任务 | B.2.3 后 | 中等 |
| B.3 迁移脚本 | 子任务 | B.1 完成后 | 重要 |
| **暂停点 2**：用户 review SHARED 报告 | 用户 | — | 决定修复方向 |
| C 本地测试 | 主线 | 否 | 顺序跑 |
| **暂停点 3**：用户 review 测试报告 | 用户 | — | 通过才 push |
| D push 到仓库 | 主线 | 否 | |

---

## 五、风险与回滚

| 风险 | 缓解 |
|---|---|
| 服务器代码本身有 bug | 整合前先 import 后通读，发现明显问题先记录，本地测试时观察 |
| schema 迁移失败影响现有 DB | 启动前自动备份 `bocai.db.backup-$(date)`，迁移失败可一键还原 |
| 随机马丁 + 共享组运行时冲突 | C.4 场景 6+8 会跑到，集中观察 |
| commit 历史混乱 | 每个 task 单独 commit，命名前缀 `feat:` / `fix:` / `chore:` |

---

## 六、立即需要你确认的几点

1. **集成分支命名**用 `feat/integration-shared-collector-random-martin` 是否 OK？
2. **暂停点机制**：每个暂停点会发完整 diff/报告等你过目，确认后才继续，是否 OK？
3. **测试 DB**：是否同意重置当前 worktree 的 `backend/data/bocai.db`（会先备份）？
4. **scope 限制**：阶段 E（服务器部署）**不在本次 plan**，等 D 完成后另起 PR 或会话讨论部署，是否 OK？
5. **bug ② 实现细节**：Phase 4 的"换日期再回测"是想要"无限多次迭代"还是"最多再测 1 次"？多次迭代会让 UI 状态稍复杂，建议先做"最多迭代 5 层"。
6. **bug ③ 调研** 完成后如果发现服务器代码本身就是 bug 来源，我们是修复后再合并，还是先合并再修复？建议**先合并保持和线上一致，再用单独 commit 修 bug**。
