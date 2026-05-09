# 本地集成测试报告

> 日期：2026-05-10  
> 分支：`feat/integration-shared-collector-random-martin`  
> 基线 commit：`d3b9b71` (snapshot/server-2026-05-04 服务器代码反向拉回)  
> 集成 HEAD：`8ea8cb5` (随机马丁 + 4 个 bug 修复 + DB 迁移)

---

## 一、阶段完成情况

| 阶段 | 内容 | 状态 |
|---|---|---|
| A.1 | SSH 拉服务器代码到本地（19 个差异文件，约 2570 行丢失代码） | ✅ |
| A.2 | snapshot 分支 commit `d3b9b71` push 到 origin + web 远程 | ✅ |
| A.3 | feat/integration 分支建立 | ✅ |
| B.1 | 随机马丁回测代码 cherry-pick 到集成分支（3 处冲突手工解决） | ✅ |
| B.2.1 | 管理员授权列表添加 `random_martin` (label=`AI马丁`) | ✅ |
| B.2.2 | Phase 4 加多次回测筛选（最多 5 层迭代）+ 直接建策略 | ✅ |
| B.2.3 | 共享池 URL 误标 uncovered 调研报告 | ✅ |
| B.2.4 | P0/P1/P2/P3 修复全部实施 | ✅ |
| B.3 | DB 自动迁移（`_auto_migrate` + DDL 重排序） | ✅ |

---

## 二、后端 pytest 结果（关键模块）

```
tests/test_database.py       — 全部通过
tests/test_admin.py          — 1 失败（pre-existing baseline）
tests/test_db_ops.py         — 全部通过
tests/test_db_ops_shared_market_phase1.py — 全部通过
tests/test_shared_market_runtime.py — 3 失败（pre-existing baseline）
tests/test_strategies.py     — 全部通过
tests/test_omission_random_strategy.py — 全部通过

总计: 228 passed, 4 failed
```

### 4 个失败的归因

| 失败 test | 失败原因 | 是否本次集成引入 |
|---|---|---|
| `test_admin_join_shared_market_uncovered_to_group` | 期望 `detection_status='matched'` 实际 `'success'` | ❌ baseline 预先存在 |
| `test_resolve_install_miss_success_joins_shared_group` | 期望 `install.issue='shared-hit'` 实际 `'fallback'` | ❌ baseline 预先存在 |
| `test_collector_lifecycle_released_with_owner` | `call_count >= 2` 实际 1 | ❌ baseline 预先存在 |
| `test_collector_marks_error_and_alerts_admin_without_last_install` | `source_status=='error'` 不通过 | ❌ baseline 预先存在 |

**验证方法**：临时把 `shared_market_runtime.py / admin.py / db_ops.py` 切回 `snapshot/server-2026-05-04` 仅基线状态，4 个 test 同样失败 → 确认是服务器代码本身引入的回归，与本次 P0-P3 集成无关。  
**处理建议**：放到独立的 `fix: cleanup pre-existing baseline test failures` PR 中处理，不阻塞本次集成。

---

## 三、关键功能验证（直接函数调用）

### P0 — 既有绑定优先查询
```
Bound URL   query → {'shared_group_id': 1, ...}  ✅
Unbound URL query → None                          ✅
```

### P1 — platform_type 等价类比较
```
JND28WEB vs JND282 → True   ✅
JND282   vs JND28WEB → True ✅
LUCKYSB  vs JND28WEB → False ✅
```

### P2 — shared_market_group_url_add 自动清理 uncovered_url
```
Before bind:  {status:'pending', detection_status:'untested'}
bind URL → group 1
After bind:   {status:'matched', detection_status:'matched',
               matched_shared_group_id:1}                      ✅ PASS
```

### 随机马丁 plan generator
```
K=5, N=3, G=700  → 700 组生成成功，700 唯一序列，组内 (ball,numbers) 不重复 ✅
K=5, N=3, G=1500 → 1500 组生成成功                                            ✅
K=10, N=4 (边界) → ValueError 正确报出（"3×C(10,10)=3 < 4"）                ✅
```

### DB 自动迁移
```
init_db() 执行后所有目标表/列存在：
  random_backtest_tasks            ✅
  random_plan_sets                 ✅
  shared_market_groups (18 列)     ✅
    .collector_health_state        ✅
    .collector_last_success_at     ✅
    .collector_consecutive_error_count ✅
    .collector_owner_key           ✅
  shared_market_group_urls         ✅
  shared_market_uncovered_urls (23 列) ✅
    .detection_attempts            ✅
    .next_detect_at                ✅
    .detecting_owner               ✅
    .last_success_at               ✅
  shared_market_alert_dedupe       ✅
  account_shared_market_routes     ✅
```

---

## 四、运行时验证

### 后端
```
uvicorn app.main:app --host 0.0.0.0 --port 8000
GET /api/v1/health → 200 {"code":0,"data":{"status":"ok","service":"bocai-backend"}}
```

### 前端
```
npx vite --port 5173
http://localhost:5173 → 200, 长度 823 字节
```

---

## 五、未测试的场景（需要部署/真账号）

| 场景 | 原因 |
|---|---|
| ceshi118 真实登录 → 验证 SHARED-002 不再误报 | 需要服务器 + 真平台账号 |
| 操作员通过 UI 创建 random_martin 策略并实盘 | 需要数据 + 真账号 |
| 管理员 UI "重新检测"按钮端到端 | 需要 admin 登录 + UI 操作 |
| 多次回测 5 层迭代真实数据 | 需要 jnd28.sqlite3 历史数据 |

这些都不属于本地集成测试范围，留给阶段 E（服务器部署）。

---

## 六、风险与已知问题

1. **4 个 baseline 预存 pytest 失败**：来自服务器代码本身，不影响本集成功能；建议另起 PR 单独修。
2. **服务器线上的 collector 账号已删除**（ceshi11/jiance11 全部 status='deleted'）：与本集成无关，是运维配置问题，需在阶段 E 修复。
3. **broken collection** `tests/test_countdown_quant_acceptance.py` 缺失 `probe_countdown_quant_acceptance` 模块——baseline 预存问题，不影响其他 test 运行。

---

## 七、结论

✅ **本地集成测试通过，功能完整**：
- 服务器代码 100% 反向拉回（snapshot 分支已同步到 origin + web）
- 随机马丁回测 + AI 同号策略 + 管理员授权 + 多次回测筛选全部就位
- 共享池 4 个 bug（P0-P3）修复 + 自动迁移机制
- 228/232 单元测试通过；剩余 4 个失败为 baseline 历史遗留

**可以进入阶段 D**（push 到仓库）。
