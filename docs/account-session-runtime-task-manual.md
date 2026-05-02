# 第三方账号统一会话整改任务执行手册

日期：2026-05-02

## 执行角色

实现 agent：`gpt-5.3-codex`

总控与复审：主控 Codex

执行 agent 不是独立决策者，只能按本手册和设计文档实现。遇到阻塞、冲突、范围扩大，必须停止并上报，不得自行扩展。

## 必读文档

执行前必须阅读：

1. `docs/account-session-runtime-requirements.md`
2. `docs/account-session-runtime-design.md`
3. `docs/account-session-runtime-task-manual.md`
4. `docs/multi-account-betting-root-cause.md`
5. `docs/odds-change-betting-failure.md`
6. `docs/shared-market-data-source-note.md`

## 允许修改范围

执行 agent 仅允许修改以下文件：

1. `backend/app/engine/session_runtime.py`，允许新增。
2. `backend/app/engine/manager.py`
3. `backend/app/engine/session.py`
4. `backend/app/engine/worker.py`
5. `backend/app/engine/executor.py`
6. `backend/app/engine/settlement.py`
7. `backend/app/api/accounts.py`
8. `backend/app/api/odds.py`
9. `backend/app/api/strategies.py`
10. `backend/app/utils/strategy_timing.py`
11. `backend/tests/test_session.py`
12. `backend/tests/test_worker.py`
13. `backend/tests/test_engine_manager.py`
14. `backend/tests/test_accounts.py`
15. `backend/tests/test_odds_api.py`
16. `backend/tests/test_strategies.py`
17. `backend/tests/test_executor.py`
18. `backend/tests/test_settlement.py`

如果发现必须修改其他文件，立即停止并上报。

## 禁止触碰范围

执行 agent 禁止修改：

1. `frontend/**`
2. `deploy/**`
3. `scripts/**`
4. `backend/app/database.py`
5. `backend/app/engine/strategies/**`
6. `backend/app/engine/shared_market_runtime.py`
7. `backend/app/engine/adapters/**`
8. 任何本地数据库文件
9. 任何 nginx、systemd、SSL、域名相关配置
10. 任何历史报告或已归档文档

如果测试证明必须触碰禁止范围，停止并上报，由主控重新确认。

## 实现任务

### 任务 1：统一会话运行时

目标：

1. 新增账号会话运行时模块。
2. 提供账号级登录锁、请求锁、恢复锁、冷却和熔断能力。
3. 统一封装平台调用，防止同账号并发平台请求。

验收：

1. 同账号并发调用 `ensure_logged_in` 时只发生一次实际登录。
2. 403 或验证码异常后进入熔断，不继续自动重试。
3. 测试覆盖通过。

### 任务 2：worker 接入统一会话

目标：

1. worker 启动不再无条件独立登录。
2. worker 使用统一运行态做登录、心跳恢复、拉期号、下注、结算。
3. 保留现有 worker lock。

验收：

1. 同账号多策略仍只有一个 worker。
2. worker 运行时不会被验证账号另起登录顶掉。
3. worker 预提交校验失败不会无限重试。

### 任务 3：账号验证与赔率刷新接入统一会话

目标：

1. 绑定账号不变。
2. 验证账号通过统一运行态。
3. worker 正在运行时验证和刷新赔率复用运行会话。
4. 不清空已有可信余额。

验收：

1. 验证账号正常能同步余额与盘口能力。
2. worker 运行时验证账号不会新建 adapter 登录。
3. 刷新赔率不会顶掉 worker。

### 任务 4：余额异常处理

目标：

1. 余额接口异常时先检查会话。
2. 会话异常时走统一恢复。
3. 恢复失败保留上次可信余额。
4. 不把异常余额写成 0。

验收：

1. 模拟余额接口异常，数据库余额不变。
2. 有明确 `balance_sync_failed` 或等价状态/告警。

### 任务 5：下注时机与多策略适配

目标：

1. 创建、编辑、启动策略都检测下注时机。
2. 同账号、同盘口、同方向真实策略间隔 20 秒。
3. 默认 88 秒，自动分配最近合法时机。
4. 无合法时机时返回明确错误。

验收：

1. 创建两个同方向策略时自动分配不同下注时机。
2. 编辑策略造成冲突时自动调整或返回明确错误。
3. 启动停止后的策略时重新检测。

### 任务 6：测试补齐

目标：

1. 为会话、worker、账号验证、赔率刷新、结算、策略时机增加测试。
2. 关键行为必须有单元测试或集成测试覆盖。

最低测试命令：

```powershell
cd backend
python -m pytest tests/test_session.py tests/test_worker.py tests/test_engine_manager.py tests/test_accounts.py tests/test_odds_api.py tests/test_strategies.py tests/test_executor.py tests/test_settlement.py
python -c "import app.main; print('BACKEND_IMPORT_OK')"
```

如果修改了前端，必须额外执行：

```powershell
cd frontend
pnpm build
```

但本手册禁止执行 agent 修改前端。

## 自检清单

提交给主控前，执行 agent 必须确认：

1. 没有修改禁止触碰范围。
2. 没有新增外部依赖。
3. 没有新增数据库 migration。
4. 没有删除历史订单或结算记录。
5. 没有修改策略算法。
6. 同账号并发登录已被单飞控制。
7. worker 运行时验证账号不会另起登录。
8. 余额异常不会写 0。
9. 下注时机自动分隔有测试。
10. 指定测试通过。

## 主控复审门禁

主控复审必须检查：

1. `git diff --name-only` 是否只包含允许范围。
2. 是否存在绕过统一会话的新增登录。
3. 是否存在无上限重试。
4. 是否存在直接写余额 0 的异常路径。
5. 是否存在前端、部署、数据库结构的越界修改。
6. 本地测试是否通过。
7. 服务器部署前是否确认当前真实投注状态。

## 部署与测试门禁

部署前必须：

1. 确认本地测试通过。
2. 确认没有 schema change。
3. 确认服务器当前是否有真实投注策略运行。
4. 备份生产数据库。
5. 使用标准发布脚本或现有发布流程。

部署后必须：

1. 检查 `/api/v1/health`。
2. 登录管理员账号。
3. 登录操作者账号。
4. 验证账号状态读取。
5. 启动测试策略。
6. 观察至少 3 期：
   - 是否只使用一条会话。
   - 是否按下注时机执行。
   - 是否成功下注。
   - 是否成功结算。
   - 是否没有无关赔率告警。
   - 是否没有高频重登。

最终报告必须以审查员视角给出：

1. 本次实际实现了什么。
2. 哪些测试通过。
3. 服务器测试结果。
4. 是否存在风险。
5. 是否建议继续开放真实运行。
