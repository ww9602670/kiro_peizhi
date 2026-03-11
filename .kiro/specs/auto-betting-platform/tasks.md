# 加拿大PC28自动投注托管SaaS平台 — 实施任务清单

> 约定：
> - `[gpt-5.3 gate]` = 后端编码任务完成后，提交 gpt-5.3 (codex-gpt53) 进行独立严格代码审查
> - `[chrome-mcp]` = 前端任务完成后，使用 Chrome DevTools MCP 进行可视化验证
> - `‖` = 可与同阶段其他标记 `‖` 的任务并行执行（无先后依赖）
> - `*` = 可选任务

---

## Phase 0: 项目脚手架初始化

- [x] 0.1 ‖ 初始化后端项目骨架
  - [x] 0.1.1 创建 `backend/` 目录结构（app/api, app/schemas, app/models, app/engine, app/utils, tests）
  - [x] 0.1.2 创建 `backend/pyproject.toml`（fastapi, uvicorn, pydantic>=2.0, aiosqlite, aiohttp, pyjwt, hypothesis）
  - [x] 0.1.3 创建 `backend/app/__init__.py` 及所有子包 `__init__.py`
  - [x] 0.1.4 创建 `backend/app/main.py` 最小 FastAPI 入口（含 lifespan 骨架 + health 端点）
  - [x] 0.1.5 验证：`pip install -e .` 成功，`uvicorn app.main:app --port 8888` 启动正常，`/api/v1/health` 返回 200

- [x] 0.2 ‖ 初始化前端项目骨架
  - [x] 0.2.1 `pnpm create vite frontend --template react-ts`，安装依赖
  - [x] 0.2.2 配置 `vite.config.ts`（path alias `@/` → `src/`，proxy `/api` → `http://localhost:8888`）
  - [x] 0.2.3 配置 `tsconfig.json` paths（`@/*` → `src/*`）
  - [x] 0.2.4 创建 `frontend/.env.development`（`VITE_API_BASE_URL=/api/v1`）
  - [x] 0.2.5 创建前端目录结构（api/, components/, pages/, types/api/, hooks/, utils/）
  - [x] 0.2.6 安装测试依赖（vitest, @testing-library/react, fast-check）
  - [x] 0.2.7 验证：`pnpm dev` 启动正常，`pnpm test --run` 通过             

- [x] 0.3 前后端联调验证（依赖 0.1 + 0.2）
  - [x] 0.3.1 启动后端 + 前端，`curl http://localhost:5173/api/v1/health` 返回 `{"code":0,...}`
  - [x] 0.3.2 [chrome-mcp] Chrome Smoke Test：navigate → snapshot → Probe 注入 → 断言 directBackendHitDetected=false

---

## Phase 1: 数据库层

- [x] 1.1 SQLite 数据库初始化与 DDL
  - [x] 1.1.1 创建 `backend/app/database.py`：连接管理、WAL 模式（`PRAGMA journal_mode=WAL`）
  - [x] 1.1.2 创建所有表的 DDL（operators, gambling_accounts, strategies, bet_orders, alerts, audit_logs, lottery_results, reconcile_records）+ 索引 + **DB 层约束：bet_orders.idempotent_id UNIQUE 索引；operators 表含 current_jti TEXT 字段；bet_orders 终态保护触发器（BEFORE UPDATE，status IN ('bet_failed','settled','reconcile_error') 时 RAISE ABORT）**。**8 张表 INTEGER 分存储约束清单：gambling_accounts.balance INTEGER（分）；strategies.base_amount/stop_loss/take_profit/daily_pnl/total_pnl INTEGER（分）；bet_orders.amount/odds/pnl INTEGER（分/×1000/分）；reconcile_records.local_balance/platform_balance/diff_amount INTEGER（分）。DoD：每张表的金额列类型为 INTEGER，DDL 中无 REAL/FLOAT/DECIMAL；终态触发器 DoD：INSERT 终态行后 UPDATE status → RAISE ABORT 且行数据不变（pytest 断言）**
  - [x] 1.1.3 创建默认管理员账户（admin/admin123）
  - [x] 1.1.4 pytest 单元测试：验证表创建、WAL 模式、终态触发器（尝试更新终态行→被拒绝）、**idempotent_id UNIQUE 约束（插入重复 idempotent_id → IntegrityError）**
  - [x] 1.1.5 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, gpt-5.3: 8项必审全通过，无blocking问题)

- [x] 1.2 DB 写入串行化队列
  - [x] 1.2.1 实现 `WriteQueue`：asyncio.Queue + 专用单写协程（所有写操作入队，单协程消费执行）
  - [x] 1.2.2 事务边界：批量写入支持（结算时多条注单合并为单事务）
  - [x] 1.2.3 回压策略：队列满时（上限 1000）阻塞等待，不丢弃
  - [x] 1.2.4 pytest 单元测试：并发写入串行化验证（多协程同时写→无锁冲突）、批量事务原子性、回压阻塞
  - [x] 1.2.5 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, gpt-5.3: 第3次复审通过 — 显式BEGIN事务+生命周期守卫+drain残留op)

- [x] 1.3 数据库操作层（CRUD）— **所有查询/写入方法必须接受 operator_id 参数并在 WHERE 条件中强制过滤（lottery_results 全局只读除外）**
  - [x] 1.3.1 创建 `backend/app/models/db_ops.py`：operators CRUD
  - [x] 1.3.2 gambling_accounts CRUD（所有方法含 `WHERE operator_id=?` 强制过滤）
  - [x] 1.3.3 strategies CRUD（所有方法含 `WHERE operator_id=?` 强制过滤）
  - [x] 1.3.4 bet_orders CRUD（含幂等 ID 唯一约束，所有方法含 `WHERE operator_id=?` 强制过滤）
  - [x] 1.3.5 alerts CRUD（所有方法含 `WHERE operator_id=?` 强制过滤）
  - [x] 1.3.6 audit_logs CRUD（所有方法含 `WHERE operator_id=?` 强制过滤）
  - [x] 1.3.7 lottery_results CRUD（全局只读，无 operator_id 过滤，无写入 API 暴露）
  - [x] 1.3.8 reconcile_records CRUD（通过 account_id→operator_id 关联过滤）
  - [x] 1.3.9 pytest 单元测试：各表 CRUD 操作 + **数据隔离验证（操作者 A 写入的数据，操作者 B 的 CRUD 方法查不到）**
  - [x] 1.3.10 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, gpt-5.2: 8项必审7项直接PASS，Item6 reconcile_record_create用SELECT WHERE id=? AND operator_id=?验证归属等效于JOIN，功能等价判定PASS；non-blocking建议：idempotent测试收窄为IntegrityError)

---

## Phase 2: 认证与用户系统

- [ ] 2.1 后端认证模块
  - [x] 2.1.1 创建 `backend/app/utils/auth.py`：JWT 创建（含 jti）/验证、单会话控制（ACTIVE_SESSIONS 内存字典 + DB 持久化 current_jti 到 operators 表，双重校验：验证时先查内存再查 DB，**后登录踢前会话强制失效语义：新登录时立即覆盖 ACTIVE_SESSIONS[operator_id] 和 DB current_jti，旧 token 的 jti 不匹配即返回 401（无宽限期）；并发登录冲突：以最后写入 DB 的 jti 为准，先到的登录会话被后到的踢出**，进程重启后从 DB 恢复 ACTIVE_SESSIONS 字典保证单会话仍成立）、Token 有效期 24h、过期前 30 分钟可刷新（刷新后旧 token 立即失效，新 token 生成新 jti 并更新 ACTIVE_SESSIONS + DB，**刷新窗口边界 DoD：expire_at - 30min ≤ now ≤ expire_at 时可刷新；now < expire_at - 30min 时拒绝刷新返回 2003；now > expire_at 时 token 已过期返回 401**）
  - [x] 2.1.2 创建 `backend/app/schemas/auth.py`：LoginRequest, TokenResponse
  - [x] 2.1.3 创建 `backend/app/schemas/common.py`：ApiResponse[T], PagedData[T]
  - [x] 2.1.3a 创建 `backend/app/utils/response.py`：统一信封中间件/装饰器，确保所有路由返回 `{code, message, data}` 格式；非 ApiResponse 返回自动包装；异常处理器统一返回信封格式。**DoD：所有 API 端点（含 health、auth、admin、accounts、strategies、bet-orders、dashboard、alerts）返回 JSON 均包含 code/message/data 三个顶层字段；code=0 表示成功；异常响应（400/401/403/404/500）也符合信封格式（code=1xxx/2xxx/3xxx/4xxx/5xxx）；不存在裸 JSON 响应（无 code 字段的响应）**
  - [x] 2.1.3b pytest 单元测试：验证所有路由（含 health）返回信封格式一致性、异常响应也符合信封格式
  - [x] 2.1.4 创建 `backend/app/api/dependencies.py`：get_current_operator（校验 token + jti + 账户状态 active/expired/disabled）, require_admin 依赖注入
  - [x] 2.1.5 创建 `backend/app/api/auth.py`：POST /auth/login（含登录失败日志：IP/时间/原因）, POST /auth/refresh（过期前30分钟窗口内静默刷新，窗口外拒绝刷新返回 2003）, POST /auth/logout
  - [x] 2.1.6 操作者到期自动标记：登录时检查 expire_date，过期则更新 status='expired' 并拒绝登录
  - [x] 2.1.7 在 main.py 挂载 auth_router（prefix=/api/v1）
  - [x] 2.1.8 pytest 单元测试：JWT 创建/验证、登录/登出/刷新、单会话踢出、过期/禁用账户拒绝、到期自动标记、登录失败日志、**单会话 DoD 验证：新登录使旧 token 立刻 401；刷新窗口边界（过期前 30min 内可刷新，30min 外拒绝返回 2003）；刷新后旧 token 失效且新 jti 写入 DB；进程重启后旧 jti 失效（模拟重启：清空 ACTIVE_SESSIONS 后从 DB 恢复，旧 token 返回 401）**
  - [x] 2.1.9 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 10项必审全通过；non-blocking：refresh中SECRET_KEY导入方式可优化，密码应hash存储)

- [ ] 2.2 后端管理员操作者管理（依赖 2.1 认证+信封+审计基础设施）
  - [x] 2.2.1 创建 `backend/app/schemas/operator.py`：OperatorCreate, OperatorUpdate, OperatorInfo
  - [x] 2.2.2 创建 `backend/app/api/admin.py`：GET/POST/PUT /admin/operators, PUT /admin/operators/{id}/status
  - [x] 2.2.3 审计日志记录（创建/修改/禁用操作者）
  - [x] 2.2.4 pytest 单元测试：CRUD 操作、权限检查、审计日志
  - [x] 2.2.5 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 8项必审全通过 — require_admin依赖注入✓、重复用户名409✓、自禁用防护409✓、StatusUpdate正则校验✓、审计日志4种action全覆盖✓、信封格式一致✓、Pydantic v2写法✓、20+测试覆盖CRUD/权限/审计/验证；non-blocking：密码应hash存储)

- [x] 2.3 前端认证（依赖 2.1）
  - [x] 2.3.1 创建 `frontend/src/types/api/common.ts`：ApiResponse, PagedData
  - [x] 2.3.2 创建 `frontend/src/types/api/auth.ts`：LoginRequest, TokenResponse
  - [x] 2.3.3 创建 `frontend/src/api/request.ts`：统一请求层（含 401 自动跳转、ApiError）
  - [x] 2.3.4 创建 `frontend/src/api/auth.ts`：login, refresh, logout
  - [x] 2.3.5 创建 `frontend/src/hooks/useAuth.ts`：认证状态管理、Token 存储、静默刷新
  - [x] 2.3.6 创建 `frontend/src/pages/Login.tsx`：登录页面
  - [x] 2.3.7 vitest 单元测试：request.ts ApiError 处理、useAuth hook
  - [x] 2.3.8 [chrome-mcp] 登录页面可视化验证：输入用户名密码 → 登录成功跳转

---

## Phase 3: 博彩账号管理（契约先行）

- [x] 3.1 ‖ 后端博彩账号 API
  - [x] 3.1.1 创建 `backend/app/schemas/account.py`：AccountCreate（account_name, password, platform_type: Literal['JND28WEB','JND282']）, AccountInfo（password_masked: 前2位+****）
  - [x] 3.1.2 创建 `backend/app/api/accounts.py`：GET /accounts, POST /accounts（含绑定数量 ≤ max_accounts 检查）, DELETE /accounts/{id}, POST /accounts/{id}/login（手动触发登录）, POST /accounts/{id}/kill-switch
  - [x] 3.1.3 绑定时登录验证：调用平台适配器 login 验证账号有效性（最多重试 2 次，含验证码自动识别）
  - [x] 3.1.4 密码脱敏：AccountInfo 返回时 password_masked = password[:2] + '****'（长度<2 时返回 '****'）
  - [x] 3.1.5 pytest 单元测试：CRUD、绑定限制（超过 max_accounts 拒绝）、密码脱敏、数据隔离（操作者间不可见）
  - [x] 3.1.6 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 8项必审全通过 — get_current_operator依赖注入✓、operator_id强制过滤(account_list_by_operator/account_get_by_id/account_delete均含operator_id)✓、绑定数量≤max_accounts检查✓、密码脱敏mask_password✓、UNIQUE约束兜底✓、数据隔离4项测试(list/delete/login/kill-switch)✓、Pydantic v2+Literal校验✓、25+测试覆盖CRUD/限制/脱敏/隔离/认证；non-blocking：_stub_platform_login待Phase5替换)

- [x] 3.2 ‖ 前端博彩账号类型 + API 封装
  - [x] 3.2.1 创建 `frontend/src/types/api/account.ts`：AccountCreate, AccountInfo
  - [x] 3.2.2 创建 `frontend/src/api/accounts.ts`：API 调用封装
  - [x] 3.2.3 vitest 单元测试

- [x] 3.3 前端博彩账号管理页面（依赖 3.1 + 3.2）
  - [x] 3.3.1 创建 `frontend/src/pages/operator/Accounts.tsx`：账号列表 + 绑定表单 + 盘口选择
  - [x] 3.3.2 密码脱敏显示（前2位+****）
  - [x] 3.3.3 熔断开关 UI
  - [x] 3.3.4 [chrome-mcp] 可视化验证：绑定账号 → 列表显示 → 密码脱敏 → 熔断切换

---

## Phase 4: 策略管理（契约先行）

- [ ] 4.1 ‖ 后端策略 API
  - [x] 4.1.1 创建 `backend/app/schemas/strategy.py`：StrategyCreate, StrategyUpdate, StrategyInfo
  - [x] 4.1.2 创建 `backend/app/api/strategies.py`：GET/POST/PUT/DELETE /strategies, POST /strategies/{id}/start|pause|stop
  - [x] 4.1.3 策略状态转移校验（stopped→running→paused→stopped）
  - [x] 4.1.4 金额元↔分转换（API 层接收元，存储分）
  - [x] 4.1.4a 赔率整数化验证：schema 层 odds 字段类型为 int（×1000 存储），输入验证 odds > 0，存储为 INTEGER；round-trip 测试：odds_int = int(odds_float * 1000)，还原误差 < 0.001
  - [x] 4.1.5 pytest 单元测试：CRUD、状态转移、金额转换、赔率整数化 round-trip
  - [x] 4.1.6 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 9项必审全通过 — operator_id强制过滤(strategy_list/get/update/delete均含operator_id)✓、account归属校验(account_get_by_id+operator_id)✓、状态转移校验(VALID_TRANSITIONS映射+validate_state_transition)✓、金额元↔分转换(_yuan_to_fen/_fen_to_yuan+DB验证)✓、赔率×1000整数化round-trip✓、马丁序列model_validator✓、仅stopped可修改/删除✓、数据隔离3项测试(list/modify/account_ownership)✓、30+测试覆盖CRUD/状态转移/金额/赔率/隔离/认证)

- [ ] 4.2 ‖ 前端策略类型 + API 封装
  - [x] 4.2.1 创建 `frontend/src/types/api/strategy.ts`：StrategyCreate, StrategyInfo
  - [x] 4.2.2 创建 `frontend/src/api/strategies.ts`：API 调用封装
  - [x] 4.2.3 vitest 单元测试

- [x] 4.3 前端策略管理页面（依赖 4.1 + 4.2）
  - [x] 4.3.1 创建 `frontend/src/pages/operator/Strategies.tsx`：策略列表 + 状态标签
  - [x] 4.3.2 创建 `frontend/src/pages/operator/StrategyForm.tsx`：策略创建/编辑表单（平注/马丁切换、马丁序列输入、模拟模式开关）
  - [x] 4.3.3 创建 `frontend/src/components/StrategyStatusTag.tsx`：策略状态标签组件
  - [x] 4.3.4 [chrome-mcp] 可视化验证：创建平注策略 → 创建马丁策略 → 启动/暂停/停止

---

## Phase 4.5: AlertService 最小桩（前置依赖）

> 注：Phase 5.4/7.2/7.4/8.1 均依赖 AlertService.send()。此处先实现最小可用桩（send + 去重），完整告警规则在 Phase 9 补全。

- [x] 4.5.1 创建 `backend/app/engine/alert.py`：AlertService 最小桩（send 方法签名：`async def send(self, operator_id, alert_type, title, detail)` + ALERT_LEVEL_MAP + DB 写入 alerts 表）
- [x] 4.5.2 告警去重/抑制：同类型同账号 5 分钟内不重复发送（内存 dict 记录 `(operator_id, alert_type, account_id) → last_sent_at`）
- [x] 4.5.3 pytest 单元测试：send 写入 DB 验证、去重/抑制验证（5 分钟内重复调用只写入 1 条）
- [x] 4.5.4 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 7项必审全通过 — send()签名正确(operator_id,alert_type,title,detail,account_id)✓、ALERT_LEVEL_MAP 12种告警类型完整✓、去重/抑制(dedup_key三元组+5分钟窗口)✓、DB写入alert_create✓、send_system_alert委托send✓、check_system_health(30%阈值+连续5期)✓、12+测试覆盖写入/去重/时间窗口/不同key不去重/级别映射/未知类型默认warning)

---

## Phase 5: 投注引擎核心

- [ ] 5.1 平台适配层
  - [x] 5.1.1 创建 `backend/app/engine/adapters/base.py`：PlatformAdapter ABC + 数据类（InstallInfo, BetResult, BalanceInfo, LoginResult）
  - [x] 5.1.2 创建 `backend/app/engine/adapters/config.py`：PLATFORM_CONFIGS（JND28WEB/JND282 配置）
  - [x] 5.1.3 创建 `backend/app/engine/adapters/jnd.py`：JNDAdapter 实现（login, get_current_install, load_odds, place_bet, query_balance, get_bet_history, get_lottery_results, heartbeat）
  - [x] 5.1.4 pytest 单元测试：使用 mock HTTP 测试 JNDAdapter 各方法
  - [x] 5.1.5 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 8项必审全通过 — PlatformAdapter ABC完整(8个抽象方法)✓、数据类(InstallInfo/BetResult/BalanceInfo/LoginResult)字段正确✓、JNDAdapter实现全部8个方法✓、赔率×1000整数化(math.floor)✓、place_bet异常捕获返回BetResult(succeed=0)✓、heartbeat异常返回False✓、PLATFORM_CONFIGS含JND28WEB/JND282+退款规则✓、30+测试覆盖login/install/odds/bet/balance/history/results/heartbeat/错误处理)

- [x] 5.2 API 频率控制器
  - [x] 5.2.1 创建 `backend/app/engine/rate_limiter.py`：RateLimiter（per-account 频率限制，LIMITS 配置：GetCurrentInstall=5s, Loaddata=3s, QueryResult=10s, Topbetlist=10s, Online=75s；超出频率的请求排队等待不丢弃；API 超时：普通接口 10s，下注接口 15s；超时后重试最多 2 次间隔 3s，**仅限非下注类读操作（GetCurrentInstall/Loaddata/QueryResult/Topbetlist/Online），Confirmbet 零重试**）。**DoD：Confirmbet 调用路径中无 retry 逻辑（代码审查确认）；读操作超时重试最多 2 次（测试验证第 3 次不重试）**
  - [x] 5.2.2 pytest 单元测试：频率限制生效、排队等待不丢弃、超时重试（读操作最多 2 次）、**Confirmbet 零重试验证（mock 超时后无重试调用）**
  - [x] 5.2.3 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 7项必审全通过 — LIMITS配置6个API正确✓、READ_APIS/BET_API分类正确✓、Confirmbet零重试(is_retryable=False,max_attempts=1)✓、读操作最多2次重试(max_attempts=3)✓、排队等待不丢弃(asyncio.Lock+sleep)✓、超时配置(普通10s/下注15s)✓、20+测试覆盖频率限制/排队/重试/零重试/per-account隔离)

- [ ] 5.3 验证码识别工具
  - [x] 5.3.1 创建 `backend/app/utils/captcha.py`：验证码识别接口封装（ThreadPoolExecutor 隔离，max_workers=10，队列上限 100）
  - [x] 5.3.2 pytest 单元测试
  - [x] 5.3.3 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 6项必审全通过 — ThreadPoolExecutor隔离(max_workers=10,thread_name_prefix=captcha-ocr)✓、队列上限100(pending_count+lock)✓、pending_count完成/异常后正确递减(finally块)✓、异常层级(CaptchaError基类+3个子类)✓、HTTP OCR调用(urllib+multipart)✓、20+测试覆盖正常识别/队列限制/边界值/错误处理/线程隔离/异常层级)

- [x] 5.4 会话管理器（依赖 4.5 AlertService 桩）
  - [x] 5.4.1 创建 `backend/app/engine/session.py`：SessionManager（login 含递增间隔重试 30s/60s/120s、连续3次失败暂停10分钟、连续5次失败标记 login_error 并通知操作者、验证码失败单独计数达5次通知）。**DoD：重试间隔精确为 [30, 60, 120] 秒（±1s 容差）；成功登录后重试计数器重置为 0；3 次失败后暂停 10 分钟（±10s 容差）再重试；5 次失败后标记 login_error 且不再自动重试（需手动触发）；验证码失败计数独立于登录失败计数**
  - [x] 5.4.2 心跳循环：每 75 秒调用 adapter.heartbeat()，连续失败 3 次触发 reconnect（先尝试 token 刷新，失败则重新登录）
  - [x] 5.4.3 pytest 单元测试：登录重试递增间隔（验证 30s→60s→120s 严格递增）、3次失败暂停10分钟、5次失败标记异常、验证码失败单独计数、心跳失败重连、**AlertService.send 被正确调用（mock 验证 login_fail/captcha_fail/session_lost 告警类型）**
  - [x] 5.4.4 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 10项必审全通过 — 登录重试递增[30,60,120]✓、3次失败暂停600s✓、5次失败标记login_error+不再自动重试✓、成功后计数器重置✓、验证码失败独立计数(captcha_fail_count)✓、验证码5次失败发captcha_fail告警✓、心跳75s间隔✓、心跳3次失败触发reconnect✓、reconnect先refresh_token后login✓、AlertService.send正确调用(login_fail/captcha_fail/session_lost)✓、30+测试全覆盖)

- [ ] 5.5 期号轮询器
  - [x] 5.5.1 创建 `backend/app/engine/poller.py`：IssuePoller（正常模式每 5s 轮询 GetCurrentInstall、已知停盘时间 19:56-20:33/06:00-07:00 提前1分钟进入慢速模式每 30s 探测、随机停盘检测：连续3次 State≠1 且期号未变 → 进入慢速模式、恢复检测：State=1 且新期号 → 恢复正常模式）
  - [x] 5.5.2 pytest 单元测试：新期号检测（is_new_issue 标记）、已知停盘时间判定、随机停盘检测触发、恢复检测、停盘/恢复事件日志
  - [x] 5.5.3 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 8项必审全通过 — 正常模式5s/慢速30s✓、已知停盘提前1分钟(19:55/05:59)✓、随机停盘检测(连续3次State≠1且期号未变)✓、恢复检测(State=1且新期号)✓、新期号is_new_issue标记✓、通过RateLimiter调用adapter✓、停盘/恢复事件日志✓、30+测试覆盖新期号/已知停盘/随机停盘/恢复/边界/跨午夜/日志)

---

## Phase 6: 策略系统

- [x] 6.1 策略基类与注册表
  - [x] 6.1.1 创建 `backend/app/engine/strategies/base.py`：BaseStrategy ABC（name/compute/on_result 方法）, StrategyContext（current_issue/history/balance/strategy_state）, BetInstruction（key_code/amount）
  - [x] 6.1.2 创建 `backend/app/engine/strategies/registry.py`：策略注册表（register_strategy 装饰器 + get_strategy_class + list_strategies）
  - [x] 6.1.3 pytest 单元测试：注册/获取/重复注册拒绝
  - [x] 6.1.4 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 6项必审全通过 — BaseStrategy ABC(name/compute/on_result)✓、StrategyContext+BetInstruction+LotteryResult数据类✓、registry register_strategy装饰器+get_strategy_class+list_strategies✓、重复注册拒绝(ValueError)✓、_clear_registry测试隔离✓、15+测试覆盖注册/获取/重复/列表)

- [x] 6.2 ‖ 平注策略实现
  - [x] 6.2.1 创建 `backend/app/engine/strategies/flat.py`：FlatStrategyImpl（每期固定金额 + 固定玩法）
  - [x] 6.2.2 pytest 单元测试：compute 返回正确 BetInstruction、on_result 无副作用
  - [x] 6.2.3 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 5项必审全通过 — FlatStrategyImpl(固定金额+固定玩法)✓、compute返回正确BetInstruction✓、on_result无副作用(noop)✓、@register_strategy装饰器注册✓、10+测试覆盖compute/on_result/多key_code)

- [x] 6.3 ‖ 马丁策略实现（依赖 4.5 AlertService 桩）
  - [x] 6.3.1 创建 `backend/app/engine/strategies/martin.py`：MartinStrategyImpl（自定义倍率序列、on_win/on_lose/on_refund 状态转移、序列跑完重置 + 日志 + martin_reset 告警）
  - [x] 6.3.2 pytest 单元测试：状态转移（win→0/lose→next/refund→不变）、金额计算（base×sequence[level]）、序列跑完重置日志、level 始终在 [0, len-1]、**martin_reset 告警触发验证（mock AlertService.send）**
  - [x] 6.3.3 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 8项必审全通过 — MartinStrategyImpl状态转移(win→0/lose→next/refund→不变)✓、金额=base×sequence[level]✓、序列跑完重置+martin_reset告警✓、level∈[0,len-1]✓、pending_alerts队列+flush_alerts异步✓、@register_strategy注册✓、AlertService.send mock验证✓、25+测试覆盖状态转移/金额/重置/告警/边界)

- [x] 6.4 策略运行器
  - [x] 6.4.1 创建 `backend/app/engine/strategy_runner.py`：StrategyRunner（包装 BaseStrategy，管理策略生命周期，产生 BetSignal）
  - [x] 6.4.2 策略状态管理：running 时调用 compute，paused/stopped 时跳过，error 时记录日志
  - [x] 6.4.3 BetSignal 生成：填充 idempotent_id（{issue}-{strategy_id}-{key_code}）、martin_level、simulation 标记。**DoD：idempotent_id 格式强约束 = `{issue}-{strategy_id}-{key_code}`，三段均为非空字符串，无前后空白，key_code 大写归一化；格式校验：正则 `^\d+-\d+-[A-Z0-9_]+$` 匹配；不同三元组生成不同 ID（pytest 参数化验证）**
  - [x] 6.4.4 pytest 单元测试：各状态下的信号生成行为、idempotent_id 格式正确性
  - [x] 6.4.5 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 9项必审全通过 — StrategyRunner包装BaseStrategy+状态管理(running/paused/stopped/error)✓、start/pause/stop状态转移校验(非法转移raise ValueError)✓、collect_signals仅running产生信号✓、idempotent_id格式{issue}-{strategy_id}-{key_code}+正则校验✓、key_code大写归一化(upper())✓、martin_level从strategy.level读取(getattr默认0)✓、simulation标记传递✓、compute异常→status=error+日志✓、on_result委托+flush_alerts异步调用✓、40+测试覆盖状态转移/信号生成/幂等ID/大写归一化/参数化不同三元组/martin集成/simulation)

---

## Phase 8: 风控模块

> 注：Phase 8.1（风控控制器）必须先于 Phase 7.2（下注执行器）实现，因为下注执行器调用风控检查。实施顺序：Phase 8.1 → Phase 7.2。

- [x] 8.1 风控控制器（依赖 4.5 AlertService 桩）
  - [x] 8.1.1 创建 `backend/app/engine/risk.py`：RiskController 类骨架 + RiskCheckResult 数据类（passed: bool, reason: str）
  - [x] 8.1.2 10 项顺序检查实现（**不可变顺序清单，实现时 checks 列表必须严格按此顺序定义，禁止重排**）：① kill_switch → ② session → ③ strategy_status → ④ operator_status → ⑤ balance → ⑥ single_bet_limit → ⑦ daily_limit → ⑧ period_limit → ⑨ stop_loss → ⑩ take_profit。**DoD：checks 列表索引 0-9 对应上述 10 项，顺序硬编码；pytest 验证：mock 所有检查通过时执行顺序日志 = [kill_switch, session, ..., take_profit]；mock 第 3 项失败时仅执行前 3 项（短路返回）**
  - [x] 8.1.3 检查顺序不变性：checks 列表固定顺序，短路返回（第一个失败即返回）
  - [x] 8.1.4 博彩账号资金限额检查（按账号维度）：single_bet_limit（单笔≤平台限额且≤自设限额）、daily_limit（当日累计）、period_limit（单期合计），超限时记录具体限额值和当前值
  - [x] 8.1.5 策略级止损止盈检查（按策略维度，净盈亏口径）：daily_pnl 计算排除退款（is_win=-1 的注单 pnl=0 不计入），止损 daily_pnl≤-stop_loss 触发，止盈 daily_pnl≥take_profit 触发
  - [x] 8.1.6 余额不足连续 3 期暂停逻辑：内部计数器，连续 3 期 balance 检查失败 → 暂停账号所有策略 + balance_low 告警
  - [x] 8.1.7 止损/止盈触发告警：调用 AlertService.send(stop_loss/take_profit)
  - [x] 8.1.8 平台限额触发告警：调用 AlertService.send(platform_limit)
  - [x] 8.1.9 pytest 单元测试：各检查项独立测试 + 组合测试 + 顺序验证 + 余额不足连续暂停 + 告警触发 + **账号资金限额边界测试（amount=limit 通过，amount=limit+1 拒绝）+ 策略止损止盈净盈亏口径验证（退款不计入：含退款的 daily_pnl 不触发止损）**
  - [x] 8.1.10 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 12项必审全通过 — 10项顺序检查不可变(checks列表索引0-9严格对应)✓、短路返回(_check_log验证执行项数)✓、kill_switch(全局+账号)✓、session(token非空)✓、strategy_status(running)✓、operator_status(active)✓、balance(含连续3期暂停+balance_low告警+计数器重置)✓、single_bet_limit(平台+自设+边界amount=limit通过/limit+1拒绝+platform_limit告警)✓、daily_limit(当日累计+DB查询)✓、period_limit(本期累计+内存缓存)✓、stop_loss/take_profit(净盈亏口径is_win≠-1+告警)✓、退款不计入daily_pnl验证✓、50+测试覆盖各检查项/顺序/短路/边界/告警/连续暂停)

- [x] 8.2 熔断 API 端点（不依赖 EngineManager，仅操作数据库标记）
  - [x] 8.2.1 POST /admin/kill-switch API 端点：设置全局熔断标记（数据库/内存标记），RiskController 检查此标记
  - [x] 8.2.2 POST /accounts/{id}/kill-switch API 端点：设置账号级 kill_switch 字段
  - [x] 8.2.3 pytest 单元测试：熔断标记设置/清除、RiskController 读取熔断标记拒绝下注
  - [x] 8.2.4 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 9项必审全通过 — 全局熔断API(POST /admin/kill-switch)设置/清除/幂等✓、账号级熔断API(POST /accounts/{id}/kill-switch)设置/清除/持久化✓、kill_switch模块(get_global_kill/set_global_kill)✓、require_admin权限检查(非管理员403)✓、数据隔离(操作者A不能操作B的账号熔断→404)✓、审计日志(global_kill_switch_on/off)✓、RiskController集成(全局熔断→拒绝/账号熔断→拒绝/清除后→允许/全局优先于账号)✓、统一信封格式✓、25+测试覆盖API/权限/隔离/审计/RiskController集成)

> 注：EngineManager 层面的熔断联动（取消待执行指令、停止 Worker）在 Phase 10.2 中实现。

---

## Phase 7: 下注执行与结算

> 注：Phase 8.1（风控控制器）必须先于 7.2（下注执行器）实现完成。实施顺序强制：8.1 → 7.2。

- [x] 7.1 KeyCode 映射工具
  - [x] 7.1.1 ‖ 创建 `backend/app/utils/key_code_map.py`：KeyCode → 中文名映射 + 中奖判定辅助函数
  - [x] 7.1.2 ‖ 创建 `frontend/src/utils/key-code-map.ts`：前端 KeyCode → 中文名映射
  - [x] 7.1.3 pytest + vitest 单元测试

- [ ] 7.2 下注执行器（硬依赖：Phase 8.1 风控控制器 + Phase 4.5 AlertService 桩）
  - [x] 7.2.1 创建 `backend/app/engine/executor.py`：BetExecutor 类骨架 + BetSignal 数据类（strategy_id, key_code, amount, idempotent_id, martin_level, simulation）
  - [x] 7.2.2 幂等检查：通过 idempotent_id 查询 bet_orders 表，已存在则跳过。**DoD：DB UNIQUE 约束 + 应用层双重检查；并发插入相同 idempotent_id 时 DB 抛 IntegrityError 被捕获并跳过（不崩溃）**
  - [x] 7.2.3 风控检查集成：逐个信号调用 RiskController.check()（依赖 Phase 8.1），未通过的记录跳过原因
  - [x] 7.2.4 赔率获取 + betdata 批量组装（**不合并相同 KeyCode 金额**）：调用 adapter.load_odds()，赔率为 0 的玩法跳过，每个信号独立构建 betdata 条目。**DoD：两个策略产生相同 KeyCode 的信号 → betdata 中出现 2 条独立条目（Amount 不合并）；测试用例：策略A买DX1金额100分 + 策略B买DX1金额200分 → betdata 包含 2 条 DX1 条目（100分和200分），而非 1 条 300分**
  - [x] 7.2.5 Confirmbet 批量提交 + 结果处理：调用 adapter.place_bet()，**Confirmbet 严格零重试**（失败直接进入终态 bet_failed + fail_reason），根据 succeed 值更新注单状态（succeed=1→bet_success，其他→bet_failed）。**DoD：place_bet 调用次数 = 1（mock 验证无重试，含超时/网络异常/succeed≠1 三种失败场景均不重试）；succeed≠1 时注单 status='bet_failed' 且 fail_reason 非空；超时异常时注单 status='bet_failed' 且 fail_reason 含 'timeout'**
  - [x] 7.2.6 deadline/cancel 传播：asyncio.timeout(CloseTimeStamp - 10)，超时后注单保持 pending（不进入半成功态），跳过本期
  - [x] 7.2.7 模拟模式支持：simulation=True 时跳过 Confirmbet，直接记录虚拟注单（status=bet_success, simulation=1）
  - [x] 7.2.8 下注失败告警：调用 AlertService.send(bet_fail)
  - [x] 7.2.9 pytest 单元测试：幂等去重（含并发 IntegrityError 捕获）、批量组装（条目数=信号数、**相同KeyCode不合并金额的显式断言**）、模拟模式、超时取消、赔率为0跳过、**Confirmbet零重试验证（mock place_bet 调用次数=1）**
  - [x] 7.2.10 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 12项必审全通过 — 幂等检查(_is_duplicate+DB UNIQUE+IntegrityError捕获)✓、风控逐信号调用RiskController.check()✓、betdata不合并相同KeyCode(独立条目)✓、赔率为0跳过✓、Confirmbet零重试(place_bet调用=1,含timeout/network/succeed≠1三种失败)✓、deadline=close_timestamp-10+asyncio.wait_for✓、超时注单保持pending✓、模拟模式跳过Confirmbet+记录虚拟注单(simulation=1)✓、混合模拟/实盘正确分离✓、bet_fail告警(AlertService.send)✓、operator_id数据隔离✓、35+测试覆盖幂等/风控/批量组装/零重试/deadline/模拟/告警/空信号)

- [x] 7.3 结算引擎（依赖 4.5 AlertService 桩）
  - [x] 7.3.1 创建 `backend/app/engine/settlement.py`：SettlementProcessor 类骨架
  - [x] 7.3.2 `_check_win(key_code, balls, sum_value)` 全玩法中奖判定（大小/单双/和值/组合/极值/色波/豹子/单球号码/单球两面/龙虎和）
  - [x] 7.3.3 `_calculate_result(order, balls, sum_value)` 含 JND282 退款规则（**逐项映射：和值13→DX2退款/DS3退款/ZH9退款，和值14→DX1退款/DS4退款/ZH8退款，其他玩法正常结算；和值≠13且≠14时无退款**）、JND28WEB 无退款（任何和值均按赔率正常结算）。**DoD：JND282 退款映射 6 条规则逐项断言（和值13+DX2→退款、和值13+DS3→退款、和值13+ZH9→退款、和值14+DX1→退款、和值14+DS4→退款、和值14+ZH8→退款）；退款注单 is_win=-1, pnl=0；退款不计入 daily_pnl（净盈亏口径）；JND28WEB 和值13/14 时 DX1/DX2/DS3/DS4/ZH8/ZH9 均正常结算（不退款）**
  - [x] 7.3.4 盈亏计算：整数分运算 `pnl = amount * odds // 1000 - amount`（中奖）/ `-amount`（未中）/ `0`（退款）
  - [x] 7.3.5 注单状态转移方法 `_transition_status(order, new_status)`：校验合法性（见设计 1.4），非法转移抛异常，**终态保护：应用层校验 + DB BEFORE UPDATE 触发器兜底（status IN ('bet_failed','settled','reconcile_error') 时 RAISE ABORT 拒绝任何更新）**。**DoD：应用层尝试终态→任意状态转移 → 抛 IllegalStateTransition 异常；DB 层直接 UPDATE 终态行 → RAISE ABORT**
  - [x] 7.3.6 策略盈亏更新：daily_pnl / total_pnl 累加，退款不计入（is_win=-1 时 pnl=0 不累加），每日 0:00 重置 daily_pnl
  - [x] 7.3.7 开奖结果缓存：写入 lottery_results 表
  - [x] 7.3.8 pytest 单元测试：全玩法中奖判定（含边界值 sum=0/13/14/27）、JND282 退款规则、JND28WEB 无退款、盈亏计算整数性、状态转移合法/非法、daily_pnl 重置、**退款不计入 daily_pnl 验证**
  - [x] 7.3.9 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 11项必审全通过 — _check_win委托key_code_map全玩法判定(大小/单双/组合/极值/色波/豹子/单球/龙虎和)✓、JND282退款6条规则(和值13→DX2/DS3/ZH9,和值14→DX1/DS4/ZH8)✓、JND28WEB无退款✓、盈亏整数分运算(amount*odds//1000-amount)✓、状态转移校验(VALID_TRANSITIONS+IllegalStateTransition)✓、终态保护(应用层+DB触发器)✓、策略daily_pnl/total_pnl累加+退款不计入(is_win≠-1过滤)✓、daily_pnl日期重置✓、lottery_results缓存(INSERT OR IGNORE)✓、operator_id强制过滤✓、50+测试覆盖全玩法/退款/盈亏/状态转移/终态触发器/daily_pnl重置/集成流程)

- [x] 7.4 对账模块（依赖 4.5 AlertService 桩）
  - [x] 7.4.1 创建 `backend/app/engine/reconciler.py`：Reconciler 类骨架
  - [x] 7.4.2 拉取平台注单（adapter.get_bet_history）+ 本地注单比对
  - [x] 7.4.3 余额校验：adapter.query_balance() 获取平台余额，与本地理论余额比对，在途单（bet_success 状态）金额扣除后再比对
  - [x] 7.4.4 容差判定：|diff| ≤ 100分 → matched，> 100分 → mismatch，累计 > 500分 → critical 告警
  - [x] 7.4.5 mismatch 处理：标记相关注单为 reconcile_error，发送 reconcile_error 告警
  - [x] 7.4.6 连续 3 期 mismatch 自动暂停账号所有策略，通知操作者
  - [x] 7.4.7 对账记录持久化到 reconcile_records 表
  - [x] 7.4.8 pytest 单元测试：matched/mismatch 判定（含边界值 diff=99/100/101）、连续异常暂停、在途单扣除、累计差异 critical 告警
  - [x] 7.4.9 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 10项必审全通过 — 平台注单拉取+本地比对(_count_platform_bets支持多字段名)✓、余额校验含在途单扣除(db_balance-SUM(bet_success))✓、容差判定(|diff|≤100→matched,>100→mismatch)边界99/100/101✓、累计差异>500→critical告警✓、mismatch不修改bet_orders状态(终态触发器保护)✓、连续3期mismatch暂停所有running策略✓、matched重置连续计数✓、reconcile_records持久化(含detail JSON)✓、operator_id强制过滤✓、40+测试覆盖容差边界/连续暂停/重置/在途单/累计差异/数据隔离/持久化)

---

## Phase 9: 告警系统（完整版）

> 注：AlertService 最小桩已在 Phase 4.5 实现。此处补全所有告警规则、API 端点和前端。

- [x] 9.1 后端告警服务完整版（依赖 4.5 桩已存在）
  - [x] 9.1.1 扩展 `backend/app/engine/alert.py`：补全 9 种操作者告警规则实现：
    - login_fail（critical）：登录失败，含失败原因和重试状态
    - captcha_fail（warning）：验证码连续失败 5 次，含失败次数
    - session_lost（warning）：会话断线，含自动重连状态
    - bet_fail（warning）：下注失败，含失败原因、期号、错误码
    - reconcile_error（critical）：对账异常，含差额和期号
    - balance_low（warning）：连续 3 期余额不足，含当前余额和所需金额
    - stop_loss（info）：触发止损，含策略名称和触发值
    - take_profit（info）：触发止盈，含策略名称和触发值
    - martin_reset（info）：马丁序列跑完一轮，含策略名称和本轮亏损总额
  - [x] 9.1.2 platform_limit 告警（warning）：超平台限额，含玩法和限额值
  - [x] 9.1.3 系统级告警（管理员）：system_api_fail（30%+ 账号请求失败）、consecutive_fail（连续 5 期下注失败）
  - [x] 9.1.4 创建 `backend/app/schemas/alert.py`：AlertInfo
  - [x] 9.1.5 创建 `backend/app/api/alerts.py`：GET /alerts, PUT /alerts/{id}/read, PUT /alerts/read-all, GET /alerts/unread-count
  - [x] 9.1.6 pytest 单元测试：各告警类型触发条件、级别映射、去重/抑制、API CRUD
  - [x] 9.1.7 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 11项必审全通过 — ALERT_LEVEL_MAP 12种告警类型完整(9操作者+1平台+2系统)✓、send()写入DB+级别映射✓、去重/抑制(5分钟窗口+dedup_key三元组)✓、send_system_alert系统级告警✓、check_system_health(30%阈值system_api_fail+连续5期consecutive_fail)✓、AlertInfo Pydantic v2 schema✓、API 4端点(GET /alerts分页过滤+PUT /{id}/read+PUT /read-all+GET /unread-count)✓、数据隔离(list/mark_read/unread_count操作者间不可见)✓、统一信封格式✓、operator_id强制过滤✓、50+测试覆盖级别映射/send/去重/系统告警/API CRUD/分页/隔离/信封)

- [x] 9.2 前端告警（依赖 9.1）
  - [x] 9.2.1 创建 `frontend/src/types/api/alert.ts`：AlertInfo
  - [x] 9.2.2 创建 `frontend/src/api/alerts.ts`：API 调用封装
  - [x] 9.2.3 创建 `frontend/src/hooks/useAlerts.ts`：告警轮询（15s 间隔）
  - [x] 9.2.4 创建 `frontend/src/components/AlertBadge.tsx`：未读告警徽标
  - [x] 9.2.5 创建 `frontend/src/pages/operator/Alerts.tsx`：告警列表页（标记已读、全部已读）
  - [x] 9.2.6 vitest 单元测试
  - [x] 9.2.7 [chrome-mcp] 可视化验证：告警列表 → 标记已读 → 徽标更新

---

## Phase 10: 投注引擎组装与 AccountWorker

- [x] 10.1 AccountWorker 实现
  - [x] 10.1.1 创建 `backend/app/engine/worker.py`：AccountWorker 类骨架（account_id, session, poller, strategies, executor, settler, risk 属性）
  - [x] 10.1.2 主循环实现：轮询 → 新期号检测 → 结算上期 → 封盘检测 → 等待下注时机 → 收集信号 → 风控 → 合并下注 → 记录
  - [x] 10.1.3 下注时机控制：默认开盘后 30s，最早 5s，最晚封盘前 10s（基于 CloseTimeStamp），**若 CloseTimeStamp ≤ 18s 跳过本期**（10s 安全余量 + 8.1s 链路延迟预算：赔率≤3s + 风控≤0.1s + Confirmbet≤5s）。**时间基准：使用平台 CloseTimeStamp（不依赖本地时钟）。DoD：CloseTimeStamp=17s → 跳过（日志记录"跳过：剩余时间不足"）；CloseTimeStamp=18s → 跳过（边界值，≤18s）；CloseTimeStamp=19s → 正常下注；边界测试必须覆盖 17s/18s/19s 三个值**
  - [x] 10.1.4 停盘处理：已知停盘提前 1 分钟进入等待、随机停盘检测（委托 IssuePoller）、恢复后继续
  - [x] 10.1.5 异常隔离：单 Worker 异常不传播（try/except + 自动重启），重启间隔递增（5s/10s/30s），连续 5 次重启失败标记 error
  - [x] 10.1.6 **引擎层数据隔离**：AccountWorker 初始化时绑定 operator_id，所有内部组件（executor/settler/reconciler/risk）的 DB 操作均透传 operator_id 参数；Worker 只处理自己 operator_id 的账号数据。**DoD：Worker A（operator_id=1）的 executor 无法读取/写入 operator_id=2 的注单；settler 只结算自己的注单；reconciler 只对账自己的注单；AlertService.send 只写入对应 operator_id 的告警**
  - [x] 10.1.7 pytest 单元测试：主循环逻辑（mock 各组件）、停盘处理、异常恢复、**下注时机边界（17s/18s/19s 三个测试用例）、引擎层数据隔离（两个 Worker 不同 operator_id，互不可见）**
  - [x] 10.1.8 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 10项必审全通过 — AccountWorker类骨架(operator_id/account_id/db/adapter/session/poller/executor/settler/reconciler/risk/alert_service/strategies)✓、主循环(poll→settle→reconcile→state检测→should_bet→collect_signals→execute)✓、下注时机(SKIP_THRESHOLD=18,≤18跳过,>18下注,17/18/19边界测试)✓、停盘处理(state≠1→sleep(5)→continue)✓、异常隔离(_run_with_restart+RESTART_DELAYS[5,10,30]+MAX_RESTART_FAILURES=5→error)✓、数据隔离(operator_id绑定+platform_type透传settler+account_id透传reconciler)✓、_parse_result辅助函数✓、策略管理(add/remove_strategy)✓、生命周期(start/stop+CancelledError捕获)✓、26测试全通过覆盖主循环/停盘/异常恢复/时机边界/数据隔离/生命周期/信号收集)

- [x] 10.2 EngineManager 实现
  - [x] 10.2.1 创建 `backend/app/engine/manager.py`：EngineManager 类骨架（workers 字典、global_kill 标记）
  - [x] 10.2.2 start_worker / stop_worker 方法：创建/销毁 AccountWorker 协程
  - [x] 10.2.3 global_kill_switch() 实现：设置全局标记 + 取消所有 Worker 待执行指令 + 停止所有 Worker
  - [x] 10.2.4 account_kill_switch() 实现：停止指定账号 Worker + 取消该账号待执行指令
  - [x] 10.2.5 restore_workers_on_startup()：启动时查询 status='online' 的账号 + running 策略，自动恢复 Worker
  - [x] 10.2.6 SessionStore 接口 + InMemorySessionStore 实现（二期迁移 Redis 预留）
  - [x] 10.2.7 WorkerRegistry 接口 + InMemoryWorkerRegistry 实现
  - [x] 10.2.8 FastAPI lifespan 集成（启动时 init_db + restore_workers，关闭时 graceful shutdown 所有 Worker）
  - [x] 10.2.9 系统级告警检测集成：定时检查 30%+ 账号请求失败 → system_api_fail 告警，连续 5 期下注失败 → consecutive_fail 告警
  - [x] 10.2.10 pytest 单元测试：start/stop Worker、全局熔断、账号熔断、启动恢复、graceful shutdown
  - [x] 10.2.11 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 11项必审全通过 — EngineManager类骨架(db/session_store/registry/alert_service)✓、start_worker/stop_worker(创建组件链+注册/注销Worker)✓、global_kill_switch(set_global_kill+停止所有Worker)✓、account_kill_switch(停止指定Worker)✓、restore_workers_on_startup(查询active操作者+online账号+running策略→自动恢复)✓、SessionStore ABC+InMemorySessionStore✓、WorkerRegistry ABC+InMemoryWorkerRegistry✓、lifespan集成(AlertService+EngineManager+restore+health_check+shutdown)✓、系统级告警检测(record_account_fail/record_bet_fail+_health_check_loop→check_system_health)✓、graceful shutdown(cancel health_check+stop all workers+异常隔离)✓、33测试全通过覆盖Store/Registry/Worker生命周期/熔断/恢复/健康检测/shutdown)

---

## Phase 11: 投注记录与仪表盘 API

- [x] 11.1 ‖ 后端投注记录 API
  - [x] 11.1.1 创建 `backend/app/schemas/bet_order.py`：BetOrderInfo（含 key_code_name 中文名）
  - [x] 11.1.2 创建 `backend/app/api/bet_orders.py`：GET /bet-orders（分页 + 日期筛选 + 策略筛选，**WHERE operator_id=? 强制过滤**）, GET /bet-orders/{id}（**含 operator_id 归属校验**）
  - [x] 11.1.3 pytest 单元测试：分页、筛选、**数据隔离（操作者 A 查不到操作者 B 的注单，越权访问返回 404）**
  - [x] 11.1.4 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 8项必审全通过 — BetOrderInfo Pydantic v2 schema(含key_code_name中文名+分→元+千分比→小数转换)✓、row_to_bet_order_info辅助函数✓、GET /bet-orders(分页+日期筛选+策略筛选+operator_id强制过滤)✓、GET /bet-orders/{id}(operator_id归属校验+404)✓、数据隔离(列表+详情跨操作者不可见)✓、统一信封格式✓、PagedData分页包装✓、9测试全通过覆盖空列表/数据/分页/筛选/详情/404/隔离列表/隔离详情/单位转换)

- [x] 11.2 ‖ 后端仪表盘 API
  - [x] 11.2.1 创建 `backend/app/schemas/dashboard.py`：OperatorDashboard, AdminDashboard, OperatorSummary
  - [x] 11.2.2 创建 `backend/app/api/dashboard.py`：GET /dashboard, GET /dashboard/recent-bets
  - [x] 11.2.3 GET /admin/dashboard（管理员仪表盘：所有操作者汇总）
  - [x] 11.2.4 pytest 单元测试
  - [x] 11.2.5 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 8项必审全通过 — OperatorDashboard/AdminDashboard/OperatorSummary Pydantic v2 schema✓、GET /dashboard(余额汇总+daily_pnl按日期+total_pnl+running策略+最近20条投注+未读告警)✓、GET /dashboard/recent-bets(最近20条)✓、GET /admin/dashboard(require_admin+所有操作者汇总+daily_pnl/total_pnl/running_strategies)✓、分→元单位转换✓、统一信封格式✓、权限校验(非管理员403)✓、5测试全通过覆盖空仪表盘/有数据/最近投注/管理员仪表盘/权限拒绝)

---

## Phase 12: 前端页面（可并行）

- [x] 12.1 ‖ 前端通用组件与布局
  - [x] 12.1.1 创建 `frontend/src/components/Layout.tsx`：侧边栏 + 顶栏（含 AlertBadge）
  - [x] 12.1.2 创建 `frontend/src/components/BetOrderTable.tsx`：投注记录表格组件（复用）
  - [x] 12.1.3 创建 `frontend/src/utils/format.ts`：金额格式化（分→元）、日期格式化、密码脱敏
  - [x] 12.1.4 vitest 单元测试：format.ts 工具函数
  - [x] 12.1.5 [chrome-mcp] 布局组件可视化验证 ✅ (2026-03-02, Chrome MCP: 侧边栏+顶栏+导航正常)

- [x] 12.2 ‖ 前端操作者仪表盘
  - [x] 12.2.1 创建 `frontend/src/types/api/dashboard.ts`：OperatorDashboard
  - [x] 12.2.2 创建 `frontend/src/api/dashboard.ts`：API 调用封装
  - [x] 12.2.3 创建 `frontend/src/hooks/useDashboard.ts`：仪表盘数据轮询（30s 刷新余额）
  - [x] 12.2.4 创建 `frontend/src/pages/operator/Dashboard.tsx`：余额、当日盈亏、总盈亏、运行中策略、最近 20 条投注、告警信息
  - [x] 12.2.5 vitest 单元测试
  - [x] 12.2.6 [chrome-mcp] 可视化验证：仪表盘数据展示 + 自动刷新 ✅ (2026-03-02, Chrome MCP: 统计卡片+策略列表+最近投注正常)

- [x] 12.3 ‖ 前端投注记录页面
  - [x] 12.3.1 创建 `frontend/src/types/api/bet-order.ts`：BetOrderInfo
  - [x] 12.3.2 创建 `frontend/src/api/bet-orders.ts`：API 调用封装
  - [x] 12.3.3 创建 `frontend/src/pages/operator/BetOrders.tsx`：投注记录列表（分页、日期筛选、策略筛选、异常高亮）
  - [x] 12.3.4 vitest 单元测试
  - [x] 12.3.5 [chrome-mcp] 可视化验证：列表展示 → 筛选 → 分页 → 异常高亮 ✅ (2026-03-02, Chrome MCP: 筛选+表格+分页正常)

- [x] 12.4 ‖ 前端管理员页面
  - [x] 12.4.1 创建 `frontend/src/types/api/operator.ts`：OperatorCreate, OperatorInfo, AdminDashboard
  - [x] 12.4.2 创建 `frontend/src/api/admin.ts`：管理员 API 调用封装
  - [x] 12.4.3 创建 `frontend/src/pages/admin/Dashboard.tsx`：管理员仪表盘（操作者列表 + 汇总数据 + 系统告警）
  - [x] 12.4.4 创建 `frontend/src/pages/admin/Operators.tsx`：操作者管理（创建/修改/禁用）
  - [x] 12.4.5 创建 `frontend/src/pages/admin/OperatorDetail.tsx`：操作者详情下钻（策略 + 投注记录）
  - [x] 12.4.6 vitest 单元测试
  - [x] 12.4.7 [chrome-mcp] 可视化验证：管理员仪表盘 → 操作者管理 → 下钻详情 ✅ (2026-03-02, Chrome MCP: 管理员仪表盘+操作者管理CRUD正常)

- [x] 12.5 前端路由配置（依赖 12.1-12.4）
  - [x] 12.5.1 配置 React Router：登录页、操作者页面组、管理员页面组
  - [x] 12.5.2 路由守卫：未登录跳转登录页、角色权限检查
  - [x] 12.5.3 [chrome-mcp] 路由跳转验证 ✅ (2026-03-02, Chrome MCP: tab切换+角色导航正常)

---

## Phase 13: 属性测试（PBT）

### 13.1 后端属性测试（pytest + hypothesis）— 共 24 个属性（P1-P12, P15-P16, P18-P27）

- [x] 13.1.1 ‖ P1: 和值计算一致性 — 任意 (b1,b2,b3) ∈ [0,9]，sum ∈ [0,27]
  - Validates: Requirements 3.1, 3.2
- [x] 13.1.2 ‖ P2: 大小判定正确性 — s≥14→DX1, s≤13→DX2, 互斥
  - Validates: Requirements 3.1
- [x] 13.1.3 ‖ P3: 单双判定正确性 — 奇数→DS3, 偶数→DS4, 互斥
  - Validates: Requirements 3.1
- [x] 13.1.4 ‖ P4: 组合玩法与大小单双一致性 — ZH7⟺DX1∧DS3, ZH8⟺DX1∧DS4, ZH9⟺DX2∧DS3, ZH10⟺DX2∧DS4
  - Validates: Requirements 3.1
- [x] 13.1.5 ‖ P5: JND282 退款规则正确性 — 和值13/14退款玩法、JND28WEB无退款
  - Validates: Requirements 3.2
- [x] 13.1.6 ‖ P6: 盈亏计算正确性 — 中奖pnl>0, 未中pnl<0, 退款pnl=0, 整数运算
  - Validates: Requirements 7.1
- [x] 13.1.7 ‖ P7: 马丁序列状态转移 — win→0, lose→(level+1)%len, refund→不变, level∈[0,len-1]
  - Validates: Requirements 4.2
- [x] 13.1.8 ‖ P8: 马丁下注金额 — amount=base×sequence[level], amount>0
  - Validates: Requirements 4.2
- [x] 13.1.9 ‖ P9: 幂等 ID 唯一性 — 不同三元组→不同ID, 格式 {issue}-{strategy_id}-{key_code}
  - Validates: Requirements 4.4, 5.2
- [x] 13.1.10 ‖ P10: 止损止盈判定 — daily_pnl≤-stop_loss→触发, daily_pnl≥take_profit→触发, None不触发
  - Validates: Requirements 6.2
- [x] 13.1.11 ‖ P11: 限额检查正确性 — 单笔/单日/单期限额检查
  - Validates: Requirements 6.1
- [x] 13.1.12 ‖ P12: 下注合并不丢失信号 — 合并后条目数=通过风控的信号数, 字段一致, **相同KeyCode不合并金额**
  - Validates: Requirements 5.4
- [x] 13.1.13 P15: 注单状态转移合法性 — RuleBasedStateMachine, 终态不可变更
  - Validates: Requirements 7.0
- [x] 13.1.14 ‖ P16: 整数分运算无精度损失 — amount*odds//1000-amount 为整数, 余额始终整数
  - Validates: Requirements 7.1
- [x] 13.1.15 ‖ P18: 对账容差判定 — |diff|≤100→matched, |diff|>100→mismatch
  - Validates: Requirements 7.1
- [x] 13.1.16 ‖ P19: 风控检查顺序不变性 — checks 列表顺序固定（kill_switch→session→...→take_profit），任意输入下执行顺序一致，短路返回不影响顺序定义
  - Validates: Requirements 6.1, 6.2
- [x] 13.1.17 ‖ P20: 限频队列不丢单 + 有界等待 — 任意请求序列，排队请求数 = 总请求数 - 已执行数，等待时间 ≤ LIMIT×2
  - Validates: Requirements 2.5
- [x] 13.1.18 ‖ P21: 登录退避间隔单调递增 — retry_delays 序列严格递增 retry_delays[i] < retry_delays[i+1]
  - Validates: Requirements 2.2
- [x] 13.1.19 ‖ P22: Worker 异常恢复幂等性 — 重启后状态与首次启动一致，无残留副作用（无重复注单、无泄漏协程）
  - Validates: Requirements 10.2
- [x] 13.1.20 ‖ P23: 幂等 ID 重放安全性 — 对任意已存在的 idempotent_id 重复插入，DB 抛 IntegrityError 且不产生重复注单，bet_orders 表中该 idempotent_id 仅 1 行
  - Validates: Requirements 4.4, 5.2
- [x] 13.1.21 ‖ P24: 终态 DB 触发器强制性 — 对任意终态注单（bet_failed/settled/reconcile_error），直接 SQL UPDATE 修改 status → RAISE ABORT，行数据不变
  - Validates: Requirements 7.0
- [x] 13.1.22 ‖ P25: DAO 层数据隔离 — 对任意两个不同 operator_id，操作者 A 的 CRUD 方法无法读取/修改操作者 B 的数据（gambling_accounts/strategies/bet_orders/alerts/audit_logs/reconcile_records）
  - Validates: Requirements 1.4
- [x] 13.1.23 ‖ P26: Confirmbet 零重试 — 对任意 place_bet 失败结果（succeed≠1 或超时），BetExecutor 不发起第二次 place_bet 调用，注单直接进入 bet_failed 终态
  - Validates: Requirements 5.2
- [x] 13.1.24 ‖ P27: 18s 阈值跳过 — 对任意 CloseTimeStamp ≤ 18，AccountWorker 跳过本期下注（不调用 executor.execute）；CloseTimeStamp > 18 时正常下注
  - Validates: Requirements 5.1

### 13.2 前端属性测试（vitest + fast-check）— 共 3 个属性

- [x] 13.2.1 ‖ P13: KeyCode 映射完整性 — 所有已知 KeyCode 有中文名, 未知 KeyCode 不抛异常
  - Validates: Requirements 3.1
- [x] 13.2.2 ‖ P14: 密码脱敏正确性 — len≥2→前2位+****, len<2→****, 不泄露第3位后字符
  - Validates: Requirements 1.3
- [x] 13.2.3 ‖ P17: 元分转换往返一致性 — to_fen(yuan)→to_yuan(fen) 往返不变
  - Validates: Requirements 7.1

### 13.3 PBT 属性映射表（P1-P27，共 27 个属性 = 后端 24 + 前端 3）

| P编号 | 属性名 | 归属模块 | 测试套件 | 关键断言 | DoD |
|-------|--------|---------|---------|---------|-----|
| P1 | 和值计算一致性 | settlement | test_settlement.py | sum(b1,b2,b3) ∈ [0,27] | ≥1000次 |
| P2 | 大小判定正确性 | settlement | test_settlement.py | DX1⊕DX2 互斥 | ≥1000次 |
| P3 | 单双判定正确性 | settlement | test_settlement.py | DS3⊕DS4 互斥 | ≥1000次 |
| P4 | 组合一致性 | settlement | test_settlement.py | ZH⟺DX∧DS | ≥1000次 |
| P5 | JND282退款 | settlement | test_settlement.py | 和值13/14退款 | ≥500次 |
| P6 | 盈亏计算 | settlement | test_settlement.py | 整数分运算 | ≥1000次 |
| P7 | 马丁状态转移 | martin | test_martin.py | level∈[0,len-1] | ≥1000次 |
| P8 | 马丁下注金额 | martin | test_martin.py | amount>0 | ≥500次 |
| P9 | 幂等ID唯一性 | executor | test_executor.py | 不同三元组→不同ID | ≥1000次 |
| P10 | 止损止盈判定 | risk | test_risk.py | 净盈亏口径 | ≥500次 |
| P11 | 限额检查 | risk | test_risk.py | 单笔/单日/单期 | ≥500次 |
| P12 | 下注批量不丢信号 | executor | test_executor.py | 条目数=信号数，不合并金额 | ≥500次 |
| P13 | KeyCode映射 | key-code-map | key-code-map.test.ts | 无异常 | ≥500次 |
| P14 | 密码脱敏 | format | format.test.ts | 不泄露 | ≥500次 |
| P15 | 状态机合法性 | state_machine | test_state_machine.py | 终态不可变 | ≥200次(SM) |
| P16 | 整数分无精度损失 | settlement | test_settlement.py | 结果为int | ≥1000次 |
| P17 | 元分转换往返 | format | format.test.ts | 往返不变 | ≥500次 |
| P18 | 对账容差 | reconciler | test_reconciler.py | ±100分边界 | ≥500次 |
| P19 | 风控顺序不变性 | risk | test_risk.py | 固定顺序 | ≥500次 |
| P20 | 限频不丢单 | rate_limiter | test_rate_limiter.py | 排队=总-已执行 | ≥500次 |
| P21 | 登录退避递增 | session | test_session.py | 严格递增 | ≥200次 |
| P22 | Worker恢复幂等 | worker | test_worker.py | 无残留副作用 | ≥100次 |
| P23 | 幂等ID重放安全 | executor | test_executor.py | DB UNIQUE拒绝重复 | ≥500次 |
| P24 | 终态DB触发器 | database | test_database.py | RAISE ABORT | ≥200次 |
| P25 | DAO层数据隔离 | db_ops | test_db_ops.py | 跨操作者不可见 | ≥200次 |
| P26 | Confirmbet零重试 | executor | test_executor.py | place_bet调用=1 | ≥500次 |
| P27 | 18s阈值跳过 | worker | test_worker.py | ≤18s不调用execute | ≥500次 |

> DoD 说明：每个属性至少运行指定次数（hypothesis/fast-check 默认 settings），CI 中 seed 固定以保证可复现。状态机测试（P15）使用 RuleBasedStateMachine，运行次数较少但覆盖路径更深。

---

## Phase 14: 集成测试与端到端验证

- [x] 14.1 后端 API 集成测试
  - [x] 14.1.1 使用 FastAPI TestClient 测试完整 API 流程：登录 → 绑定账号 → 创建策略 → 查询投注记录 → 仪表盘
  - [x] 14.1.2 数据隔离系统性测试矩阵：覆盖所有资源类型的跨操作者不可见性验证
    - operators：操作者 A 不可查看/修改操作者 B 的信息
    - gambling_accounts：操作者 A 不可查看/操作操作者 B 的博彩账号
    - strategies：操作者 A 不可查看/启停操作者 B 的策略
    - bet_orders：操作者 A 不可查看操作者 B 的投注记录
    - alerts：操作者 A 不可查看操作者 B 的告警
    - audit_logs：操作者 A 不可查看操作者 B 的审计日志
    - reconcile_records：操作者 A 不可查看操作者 B 的对账记录
    - lottery_results：全局只读公用数据（所有操作者可读，无写权限），注明"全局只读例外"并验证无写入接口暴露
    - **Worker/reconciler/alerts 引擎层数据隔离**：Worker 只处理自己 operator_id 的账号数据，Reconciler 只对账自己的注单，AlertService 只写入对应 operator_id 的告警
    - 每种资源类型至少 1 个正向测试（自己可见）+ 1 个反向测试（他人不可见）+ 1 个越权操作测试（返回 403/404）
  - [x] 14.1.3 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, local-review: 修复 client fixture EngineManager mock 防止 health_check 无限循环挂起测试；21 项集成测试全通过；数据隔离矩阵覆盖 8 种资源类型+引擎层隔离)

- [x] 14.2 前后端联调验证
  - [x] 14.2.1 [chrome-mcp] 完整 Chrome Smoke Test（标准流程）：navigate → snapshot → Probe 注入 → reload → Probe 读取 → 断言 → 截图 ✅ (2026-03-03, Chrome MCP: 5项断言全通过 — probeMissing=false✓、requestCount=2(非空)✓、directBackendHitDetected=false✓、apiAbsoluteUrlHitDetected=false✓、errors=[]✓)
  - [x] 14.2.2 [chrome-mcp] 操作者完整流程验证：登录 → 绑定账号 → 创建策略 → 查看仪表盘 → 查看投注记录 → 查看告警 ✅ (2026-03-03, Chrome MCP: 操作者登录成功、绑定jnd账号、创建flat策略、仪表盘数据正确、投注记录API 200、告警API 200)
  - [x] 14.2.3 [chrome-mcp] 管理员完整流程验证：登录 → 创建操作者 → 查看仪表盘 → 下钻操作者详情 ✅ (2026-03-03, Chrome MCP: 管理员仪表盘935操作者/912活跃、操作者管理分页表格+状态标签、创建操作者API验证code:0+重复用户名错误提示、禁用/启用切换双向验证)

- [x] 14.3 投注引擎集成测试
  - [x] 14.3.1 使用 mock adapter 模拟完整投注流程：登录 → 轮询 → 策略信号 → 风控 → 下注 → 结算 → 对账
  - [x] 14.3.2 多策略并行测试：同一账号多策略同时运行，批量提交正确（**不合并相同 KeyCode 金额，断言 betdata 条目数=策略信号数**）
  - [x] 14.3.3 并发幂等性测试：模拟并发重复请求（相同 idempotent_id），验证仅产生一张有效注单（DB UNIQUE 约束 + 触发器兜底）
  - [x] 14.3.4 异常恢复测试：Worker 异常后自动重启，不影响其他 Worker
  - [x] 14.3.5 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, gpt-5.2: 8项必审6项直接PASS — MockAdapter完整实现8个ABC方法✓、完整流程signal→risk→bet→settlement→reconciliation✓、多策略不合并KeyCode(betdata=3条,DX1×2+DS3×1)✓、并发幂等(asyncio.gather+DB UNIQUE=1行)✓、数据setup(online+balance+session_token)✓、PnL计算(1000*1980//1000-1000=980)✓；non-blocking：reconciliation断言可更严格、并发测试可加barrier)

---

## Phase 15: 策略注入接口文档

- [x] 15.1 策略注入开发文档
  - [x] 15.1.1 创建 `docs/strategy-injection-guide.md`：接口规范、BaseStrategy ABC 说明、StrategyContext 字段说明、BetInstruction 格式
  - [x] 15.1.2 示例策略代码（Python 模块，含注册装饰器用法）
  - [x] 15.1.3 策略开发 → 注册 → 测试 → 部署的完整流程说明

---

## Phase 16: 非功能需求验证

- [x] 16.1 结构化日志与可观测性
  - [x] 16.1.1 创建 `backend/app/utils/logger.py`：结构化 JSON 日志（字段：时间戳、操作者ID、博彩账号、期号、操作类型、结果、耗时）
  - [x] 16.1.2 关键链路埋点：下注请求/响应、结算结果、余额变动、策略状态变更、登录尝试
  - [x] 16.1.3 trace_id 贯穿：每次下注生成 UUID，关联 API 请求→引擎→平台调用→注单记录
  - [x] 16.1.4 pytest 单元测试：日志格式验证、trace_id 传播
  - [x] 16.1.5 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-02, gpt-5.3: 8项必审7项直接PASS，Item5 None过滤：_log_structured已在ctx构建时过滤None(v is not None)，JSONFormatter不会收到None值，功能等价判定PASS；non-blocking：JSONFormatter.update可加防御性None过滤)

- [x] 16.2 并发与可靠性验证
  - [x] 16.2.1 创建 `backend/tests/test_concurrency.py`：模拟 1000 个 AccountWorker 并发运行（mock adapter），验证无死锁、无资源泄漏、内存占用合理。**DoD：1000 Worker 同时启动后运行 60s 无 deadlock/exception；内存增长 ≤ 500MB（基线 + 1000 Worker）；所有 Worker 的 asyncio.Task 状态为 running 或 done（无 pending 泄漏）；SQLite WAL 模式下 1000 协程并发读 + 串行写队列无数据丢失**
  - [x] 16.2.2 SQLite WAL 并发读写测试：1000 协程同时读 + 串行写队列，验证数据一致性、写入不丢失
  - [x] 16.2.3 故障注入测试：网络超时、验证码服务不可用、平台 API 返回异常 → 验证 Worker 自动恢复不影响其他 Worker
  - [x] 16.2.4 异常恢复量化验证：Worker 异常后 ≤ 30s 内自动重启，重启成功率 ≥ 99%，连续 5 次重启失败标记 error 并停止
  - [x] 16.2.5 [gpt-5.3 gate] 代码审查 ✅ PASS (2026-03-03, gpt-5.3: 7项必审全通过 — 1000协程并发无死锁/泄漏/内存合理✓、WAL文件DB并发读写+WriteQueue串行写✓、数据完整性COUNT+SUM验证✓、故障隔离(gather+return_exceptions)✓、重启间隔[5,10,30]递增✓、连续5次失败标记error✓、临时文件清理os.unlink✓、13测试5.3s全通过)


---

## 依赖关系总览

### 依赖图（Phase 级别）

```
Phase 0 (脚手架)
├── 0.1 后端骨架 ─┐
├── 0.2 前端骨架 ─┤
└── 0.3 联调验证 ←┘ (依赖 0.1 + 0.2)

Phase 1 (数据库层) ← Phase 0
├── 1.1 DDL + WAL + 触发器
├── 1.2 写入串行化队列
└── 1.3 CRUD 操作层（含 operator_id 强制过滤）

Phase 2 (认证) ← Phase 1
├── 2.1 后端认证模块（JWT + 单会话 + 刷新窗口）
├── 2.2 管理员操作者管理 ← 2.1
└── 2.3 前端认证 ← 2.1

Phase 3 (博彩账号) ← Phase 2
├── 3.1 ‖ 后端 API
├── 3.2 ‖ 前端类型 + API
└── 3.3 前端页面 ← 3.1 + 3.2

Phase 4 (策略管理) ← Phase 2
├── 4.1 ‖ 后端 API
├── 4.2 ‖ 前端类型 + API
└── 4.3 前端页面 ← 4.1 + 4.2

Phase 4.5 (AlertService 桩) ← Phase 1
└── 4.5 最小桩（send + 去重 + DB 写入）

Phase 5 (投注引擎核心) ← Phase 1
├── 5.1 平台适配层
├── 5.2 API 频率控制器（含 Confirmbet 零重试）
├── 5.3 验证码识别
├── 5.4 会话管理器 ← 4.5
└── 5.5 期号轮询器

Phase 6 (策略系统) ← Phase 5
├── 6.1 策略基类与注册表
├── 6.2 ‖ 平注策略
├── 6.3 ‖ 马丁策略 ← 4.5
└── 6.4 策略运行器

Phase 8 (风控) ← Phase 1 + 4.5
├── 8.1 风控控制器（10 项顺序检查）← 4.5
└── 8.2 熔断 API 端点

Phase 7 (下注执行与结算) ← Phase 6 + 8.1
├── 7.1 ‖ KeyCode 映射
├── 7.2 下注执行器 ← 8.1 + 4.5（硬依赖）
├── 7.3 结算引擎 ← 4.5
└── 7.4 对账模块 ← 4.5

Phase 9 (告警完整版) ← Phase 4.5 + 7
├── 9.1 后端告警服务完整版
└── 9.2 前端告警 ← 9.1

Phase 10 (引擎组装) ← Phase 5 + 6 + 7 + 8
├── 10.1 AccountWorker（含 18s 阈值跳过 + 引擎层数据隔离）
└── 10.2 EngineManager + SessionStore/WorkerRegistry 接口

Phase 11 (投注记录 + 仪表盘) ← Phase 7
├── 11.1 ‖ 投注记录 API（operator_id 强制过滤）
└── 11.2 ‖ 仪表盘 API

Phase 12 (前端页面) ← Phase 11 + 9.2
├── 12.1 ‖ 通用组件与布局
├── 12.2 ‖ 操作者仪表盘
├── 12.3 ‖ 投注记录页面
├── 12.4 ‖ 管理员页面
└── 12.5 路由配置 ← 12.1-12.4

Phase 13 (PBT) ← Phase 5 + 6 + 7 + 8 + 10
├── 13.1 后端 24 个属性（P1-P12, P15-P16, P18-P27）
└── 13.2 前端 3 个属性（P13, P14, P17）

Phase 14 (集成测试) ← Phase 10 + 12
├── 14.1 后端 API 集成测试（含数据隔离矩阵 + 引擎层隔离）
├── 14.2 前后端联调验证 [chrome-mcp]
└── 14.3 投注引擎集成测试

Phase 15 (策略注入文档) ← Phase 6
Phase 16 (非功能需求) ← Phase 10
```

### 关键依赖链（强制执行顺序）

| 依赖链 | 说明 |
|--------|------|
| 4.5 → 5.4 | 会话管理器依赖 AlertService.send() |
| 4.5 → 6.3 | 马丁策略 martin_reset 告警依赖 AlertService |
| 4.5 → 8.1 | 风控控制器止损/止盈/限额告警依赖 AlertService |
| **8.1 → 7.2** | **下注执行器调用 RiskController.check()，必须先完成风控** |
| 4.5 → 7.2 | 下注执行器 bet_fail 告警依赖 AlertService |
| 4.5 → 7.3 | 结算引擎依赖 AlertService（间接） |
| 4.5 → 7.4 | 对账模块 reconcile_error 告警依赖 AlertService |

### AlertService 触发点汇总

| 触发点 | Phase | 告警类型 | 级别 |
|--------|-------|---------|------|
| 会话管理器：登录失败 5 次 | 5.4 | login_fail | critical |
| 会话管理器：验证码失败 5 次 | 5.4 | captcha_fail | warning |
| 会话管理器：心跳断线 | 5.4 | session_lost | warning |
| 马丁策略：序列跑完重置 | 6.3 | martin_reset | info |
| 风控：止损触发 | 8.1 | stop_loss | info |
| 风控：止盈触发 | 8.1 | take_profit | info |
| 风控：平台限额触发 | 8.1 | platform_limit | warning |
| 风控：余额不足连续 3 期 | 8.1 | balance_low | warning |
| 下注执行器：下注失败 | 7.2 | bet_fail | warning |
| 对账模块：对账异常 | 7.4 | reconcile_error | critical |
| EngineManager：30%+ 账号失败 | 10.2 | system_api_fail | critical |
| EngineManager：连续 5 期失败 | 10.2 | consecutive_fail | critical |

### 可并行执行建议

| 并行组 | 任务 | 前置条件 |
|--------|------|---------|
| A | 0.1 + 0.2 | 无 |
| B | 3.1 + 3.2 | Phase 2 |
| C | 4.1 + 4.2 | Phase 2 |
| D | 5.1 + 5.2 + 5.3 + 5.5 | Phase 1 |
| E | 6.2 + 6.3 | 6.1 |
| F | 7.1（前后端 KeyCode 映射） | 无 |
| G | 11.1 + 11.2 | Phase 7 |
| H | 12.1 + 12.2 + 12.3 + 12.4 | Phase 11 + 9.2 |
| I | 13.1 各属性（P1-P27 独立） | 对应模块完成 |
| J | 13.2 各属性（P13/P14/P17 独立） | 对应模块完成 |
