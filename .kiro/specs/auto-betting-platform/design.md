# 加拿大PC28自动投注托管SaaS平台 — 设计文档

## 概述

本文档描述加拿大PC28自动投注托管SaaS平台的技术设计方案。平台作为第三方博彩网站的自动投注代理工具，核心功能包括：自动登录博彩网站、按预设策略自动下注、实时结算与对账、风控管理。

### 技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 前端 | React + TypeScript + Vite | pnpm 包管理，端口 5173 |
| 后端 | FastAPI + Python | Pydantic v2，端口 8888 |
| 数据库 | SQLite + WAL 模式（一期） | 写入串行化队列，二期迁移 PostgreSQL |
| 异步引擎 | Python asyncio + ThreadPoolExecutor | 投注引擎核心，阻塞调用走线程池 |
| 测试 | Vitest + fast-check / pytest + hypothesis | 前后端属性测试 |
| 开发模式 | Proxy-Only | Vite proxy → FastAPI |

### 架构总览

```
┌─────────────────────────────────────────────────────┐
│                    前端 (React)                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │ 操作者面板 │ │ 管理员面板 │ │ 策略配置  │ │ 告警中心 │ │
│  └──────────┘ └──────────┘ └──────────┘ └─────────┘ │
└───────────────────────┬─────────────────────────────┘
                        │ /api/v1 (Vite Proxy)
┌───────────────────────┴─────────────────────────────┐
│                  后端 (FastAPI)                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │ 用户API   │ │ 策略API   │ │ 投注API   │ │ 告警API │ │
│  └──────────┘ └──────────┘ └──────────┘ └─────────┘ │
│  ┌──────────────────────────────────────────────────┐ │
│  │              投注引擎 (asyncio)                    │ │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌───────────┐ │ │
│  │  │期号轮询 │ │策略计算 │ │下注执行 │ │结算引擎   │ │ │
│  │  └────────┘ └────────┘ └────────┘ └───────────┘ │ │
│  │  ┌────────┐ ┌────────┐ ┌────────┐               │ │
│  │  │风控模块 │ │会话管理 │ │告警模块 │               │ │
│  │  └────────┘ └────────┘ └────────┘               │ │
│  └──────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────┐ │
│  │           平台适配层 (ABC)                         │ │
│  │  ┌─────────────┐  ┌─────────────┐                │ │
│  │  │ JND28WEB    │  │ JND282      │                │ │
│  │  │ (网盘适配器) │  │ (2.0盘适配器)│                │ │
│  │  └─────────────┘  └─────────────┘                │ │
│  └──────────────────────────────────────────────────┘ │
└───────────────────────┬─────────────────────────────┘
                        │ HTTP (aiohttp)
┌───────────────────────┴─────────────────────────────┐
│              博彩平台 API                             │
│  (GetCurrentInstall / Loaddata / Confirmbet / ...)   │
└─────────────────────────────────────────────────────┘
```


---

## 1. 数据库设计

### 1.1 ER 关系

```
Admin(1) ──manages──> Operator(N)
Operator(1) ──owns──> GamblingAccount(N)
Operator(1) ──creates──> Strategy(N)
GamblingAccount(1) ──has──> BetOrder(N)
Strategy(1) ──produces──> BetOrder(N)
Operator(1) ──receives──> Alert(N)
GamblingAccount(1) ──has──> ReconcileRecord(N)
```

### 1.2 表结构

#### operators（操作者）
```sql
CREATE TABLE operators (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,
    password        TEXT NOT NULL,              -- 明文存储（用户明确要求）
    role            TEXT NOT NULL DEFAULT 'operator',  -- 'admin' | 'operator'
    status          TEXT NOT NULL DEFAULT 'active',    -- 'active' | 'expired' | 'disabled'
    max_accounts    INTEGER NOT NULL DEFAULT 1,
    expire_date     TEXT,                       -- ISO 8601 日期
    current_jti     TEXT,                       -- 当前活跃会话的 JWT ID（单会话控制）
    created_by      INTEGER REFERENCES operators(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

#### gambling_accounts（博彩账号）
```sql
CREATE TABLE gambling_accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id     INTEGER NOT NULL REFERENCES operators(id),
    account_name    TEXT NOT NULL,
    password        TEXT NOT NULL,              -- 明文存储（用户明确要求，三次确认）
    platform_type   TEXT NOT NULL,              -- 'JND28WEB' | 'JND282'
    status          TEXT NOT NULL DEFAULT 'inactive',  -- 'inactive'|'online'|'login_error'|'disabled'
    session_token   TEXT,                       -- 当前会话 token（内存优先，仅持久化用于重启恢复，启动后清除）
    balance         INTEGER DEFAULT 0,          -- 余额（单位：分，1元=100分）
    login_fail_count INTEGER DEFAULT 0,
    last_login_at   TEXT,
    kill_switch     INTEGER NOT NULL DEFAULT 0, -- 0=正常, 1=熔断
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(operator_id, account_name, platform_type)
);
```

#### strategies（策略）
```sql
CREATE TABLE strategies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id     INTEGER NOT NULL REFERENCES operators(id),
    account_id      INTEGER NOT NULL REFERENCES gambling_accounts(id),
    name            TEXT NOT NULL,
    type            TEXT NOT NULL,              -- 'flat' | 'martin'
    play_code       TEXT NOT NULL,              -- 玩法 KeyCode，如 'DX1'
    base_amount     INTEGER NOT NULL,            -- 基础金额（单位：分）
    martin_sequence TEXT,                       -- JSON 数组，马丁倍率序列，如 [1,2,4,8,16]
    bet_timing      INTEGER NOT NULL DEFAULT 30, -- 开盘后多少秒下注
    simulation      INTEGER NOT NULL DEFAULT 0,  -- 0=实盘, 1=模拟
    status          TEXT NOT NULL DEFAULT 'stopped', -- 'stopped'|'running'|'paused'|'error'
    martin_level    INTEGER NOT NULL DEFAULT 0, -- 当前马丁序列位置（0-based）
    stop_loss       INTEGER,                    -- 止损线（单位：分，NULL=未设置）
    take_profit     INTEGER,                    -- 止盈线（单位：分，NULL=未设置）
    daily_pnl       INTEGER NOT NULL DEFAULT 0, -- 当日净盈亏（单位：分）
    total_pnl       INTEGER NOT NULL DEFAULT 0, -- 总净盈亏（单位：分）
    daily_pnl_date  TEXT,                       -- 当日盈亏对应日期
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```


#### bet_orders（注单）
```sql
CREATE TABLE bet_orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotent_id   TEXT NOT NULL UNIQUE,       -- 格式: {期号}-{策略ID}-{KeyCode}
    operator_id     INTEGER NOT NULL REFERENCES operators(id),
    account_id      INTEGER NOT NULL REFERENCES gambling_accounts(id),
    strategy_id     INTEGER NOT NULL REFERENCES strategies(id),
    issue           TEXT NOT NULL,              -- 期号
    key_code        TEXT NOT NULL,              -- 玩法代码
    amount          INTEGER NOT NULL,            -- 下注金额（单位：分）
    odds            INTEGER,                    -- 下注时赔率（×1000 存储，如 2.053 存为 2053）
    status          TEXT NOT NULL DEFAULT 'pending',
        -- 'pending'|'betting'|'bet_success'|'bet_failed'|'settling'|'settled'|'reconcile_error'
    bet_response    TEXT,                       -- Confirmbet 原始响应 JSON
    open_result     TEXT,                       -- 开奖结果 "球1,球2,球3"
    sum_value       INTEGER,                    -- 和值
    is_win          INTEGER,                    -- 1=中奖, 0=未中, -1=退款
    pnl             INTEGER,                    -- 盈亏金额（单位：分，正=盈利, 负=亏损, 0=退款）
    simulation      INTEGER NOT NULL DEFAULT 0, -- 0=实盘, 1=模拟
    martin_level    INTEGER,                    -- 下注时马丁级别
    bet_at          TEXT,                       -- 下注时间
    settled_at      TEXT,                       -- 结算时间
    fail_reason     TEXT,                       -- 失败原因
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_bet_orders_issue ON bet_orders(issue);
CREATE INDEX idx_bet_orders_account ON bet_orders(account_id, issue);
CREATE INDEX idx_bet_orders_strategy ON bet_orders(strategy_id, created_at);

-- 终态保护触发器：禁止修改已终结的注单
CREATE TRIGGER trg_bet_orders_terminal_state
BEFORE UPDATE ON bet_orders
WHEN OLD.status IN ('bet_failed', 'settled', 'reconcile_error')
BEGIN
    SELECT RAISE(ABORT, '终态注单不可修改');
END;
```

#### alerts（告警）
```sql
CREATE TABLE alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id     INTEGER NOT NULL REFERENCES operators(id),
    type            TEXT NOT NULL,
        -- 'login_fail'|'captcha_fail'|'session_lost'|'bet_fail'|'reconcile_error'
        -- |'balance_low'|'stop_loss'|'take_profit'|'martin_reset'|'platform_limit'
        -- |'system_api_fail'|'consecutive_fail'
    level           TEXT NOT NULL DEFAULT 'warning', -- 'info'|'warning'|'critical'
    title           TEXT NOT NULL,
    detail          TEXT,                       -- JSON 格式详细信息
    is_read         INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_alerts_operator ON alerts(operator_id, is_read, created_at);
```

#### audit_logs（审计日志）
```sql
CREATE TABLE audit_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id     INTEGER REFERENCES operators(id),
    action          TEXT NOT NULL,              -- 'create_operator'|'disable_operator'|'login'|'bet'|'settle'|...
    target_type     TEXT,                       -- 'operator'|'account'|'strategy'|'bet_order'
    target_id       INTEGER,
    detail          TEXT,                       -- JSON 格式详细信息
    ip_address      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_audit_logs_operator ON audit_logs(operator_id, created_at);
```

#### lottery_results（开奖结果缓存）
```sql
CREATE TABLE lottery_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    issue           TEXT NOT NULL UNIQUE,       -- 期号
    open_result     TEXT NOT NULL,              -- "球1,球2,球3"
    sum_value       INTEGER NOT NULL,           -- 和值
    open_time       TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

#### reconcile_records（对账记录）
```sql
CREATE TABLE reconcile_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      INTEGER NOT NULL REFERENCES gambling_accounts(id),
    issue           TEXT NOT NULL,              -- 期号
    local_bet_count INTEGER NOT NULL,           -- 本地注单数
    platform_bet_count INTEGER,                 -- 平台注单数
    local_balance   INTEGER,                    -- 本地计算余额（分）
    platform_balance INTEGER,                   -- 平台实际余额（分）
    diff_amount     INTEGER,                    -- 差额（分）
    status          TEXT NOT NULL DEFAULT 'pending', -- 'pending'|'matched'|'mismatch'|'resolved'
    detail          TEXT,                       -- JSON 差异明细
    resolved_by     TEXT,                       -- 'auto'|'manual'
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_reconcile_account ON reconcile_records(account_id, issue);
```

### 1.3 金额精度策略

所有金额字段统一使用整数（单位：分），避免浮点精度问题：
- 数据库：INTEGER 类型存储分值
- 后端：Python `int` 运算，仅在 API 输入/输出时做元↔分转换
- 前端：显示时 `amount / 100` 转为元，提交时 `amount * 100` 转为分
- 赔率：×1000 存储为整数（如 2.053 → 2053），计算时 `pnl = amount * odds / 1000 - amount`
- 对账容差：±100 分（即 ±1 元），超出标记为 reconcile_error

#### 字段单位字典

| 表 | 字段 | 单位 | 说明 |
|----|------|------|------|
| gambling_accounts | balance | 分（INTEGER） | 1元=100分 |
| strategies | base_amount | 分（INTEGER） | 基础金额 |
| strategies | stop_loss | 分（INTEGER） | 止损线 |
| strategies | take_profit | 分（INTEGER） | 止盈线 |
| strategies | daily_pnl | 分（INTEGER） | 当日净盈亏 |
| strategies | total_pnl | 分（INTEGER） | 总净盈亏 |
| bet_orders | amount | 分（INTEGER） | 下注金额 |
| bet_orders | odds | ×1000（INTEGER） | 赔率，如 2.053→2053 |
| bet_orders | pnl | 分（INTEGER） | 盈亏金额 |
| reconcile_records | local_balance | 分（INTEGER） | 本地计算余额 |
| reconcile_records | platform_balance | 分（INTEGER） | 平台实际余额 |
| reconcile_records | diff_amount | 分（INTEGER） | 差额 |

### 1.4 注单状态机约束

```
合法状态转移（单向，不可回退）：
pending → betting → bet_success → settling → settled
                  → bet_failed (终态)
                                   settled → reconcile_error (终态)
```

状态转移规则：
- `pending → betting`：发送 Confirmbet 请求时
- `betting → bet_success`：Confirmbet 返回 succeed=1
- `betting → bet_failed`：Confirmbet 返回 succeed≠1 或超时
- `bet_success → settling`：开奖结算开始处理
- `settling → settled`：结算计算完成
- `settled → reconcile_error`：余额交叉校验不通过
- 终态（bet_failed / settled / reconcile_error）不可变更
- 所有状态变更通过 `_transition_status(order, new_status)` 方法，内部校验合法性

### 1.5 SQLite 写入策略

- 启用 WAL 模式：`PRAGMA journal_mode=WAL`
- 写入串行化：所有写操作通过单一 `asyncio.Queue` + 专用写协程执行
- 读操作可并发（WAL 模式支持读写并发）
- 批量写入：结算时多条注单更新合并为单事务
- 容量上限：单库 10GB / 100万注单，超出触发归档迁移


---

## 2. API 设计

### 2.1 统一响应信封

所有 API 遵循契约先行规范，使用统一信封：

```python
# backend/app/schemas/common.py
from pydantic import BaseModel, ConfigDict
from typing import TypeVar, Generic, Optional

T = TypeVar('T')

class ApiResponse(BaseModel, Generic[T]):
    code: int = 0
    message: str = "success"
    data: Optional[T] = None

class PagedData(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
```

### 2.2 API 端点清单

所有路由在 `main.py` 统一挂载 prefix `/api/v1`，router 内只定义相对路径。

HTTP 状态码规范：
- 200：成功（code=0）
- 400：参数错误（code=1xxx）
- 401：认证失败（code=2xxx）
- 403：权限不足（code=3xxx）
- 404：资源不存在（code=4001）
- 409：冲突/重复操作（code=4002）
- 500：服务端错误（code=5xxx）

分页规范：所有列表接口支持 `page`（默认1）和 `page_size`（默认20，最大100）查询参数。

幂等规范：写操作接口支持 `X-Idempotency-Key` 请求头，服务端去重方案：
- 落库：`idempotency_keys` 表存储 `key(UNIQUE) → request_hash → response_json → created_at`
- TTL：5 分钟内相同 key 直接返回缓存响应，不重复执行
- 超过 TTL 的记录定期清理（每小时）
- 下注接口的幂等 key = idempotent_id（{期号}-{策略ID}-{KeyCode}）

#### 认证模块

| 方法 | 路径 | 说明 | 请求体 | 响应 data |
|------|------|------|--------|-----------|
| POST | /auth/login | 操作者登录 | `{username, password}` | `{token, expire_at}` |
| POST | /auth/refresh | 刷新 Token | - (Header: Bearer) | `{token, expire_at}` |
| POST | /auth/logout | 登出 | - | `null` |

#### 操作者管理（管理员）

| 方法 | 路径 | 说明 | 请求体 | 响应 data |
|------|------|------|--------|-----------|
| GET | /admin/operators | 操作者列表 | - | `PagedData[OperatorInfo]` |
| POST | /admin/operators | 创建操作者 | `OperatorCreate` | `OperatorInfo` |
| PUT | /admin/operators/{id} | 修改操作者 | `OperatorUpdate` | `OperatorInfo` |
| PUT | /admin/operators/{id}/status | 禁用/启用 | `{status}` | `OperatorInfo` |
| GET | /admin/dashboard | 管理员仪表盘 | - | `AdminDashboard` |
| POST | /admin/kill-switch | 全局熔断 | - | `null` |

#### 博彩账号

| 方法 | 路径 | 说明 | 请求体 | 响应 data |
|------|------|------|--------|-----------|
| GET | /accounts | 我的博彩账号列表 | - | `list[AccountInfo]` |
| POST | /accounts | 绑定博彩账号 | `AccountCreate` | `AccountInfo` |
| DELETE | /accounts/{id} | 解绑博彩账号 | - | `null` |
| POST | /accounts/{id}/login | 手动触发登录 | - | `AccountInfo` |
| POST | /accounts/{id}/kill-switch | 账号级熔断 | `{enabled}` | `AccountInfo` |

#### 策略管理

| 方法 | 路径 | 说明 | 请求体 | 响应 data |
|------|------|------|--------|-----------|
| GET | /strategies | 我的策略列表 | - | `list[StrategyInfo]` |
| POST | /strategies | 创建策略 | `StrategyCreate` | `StrategyInfo` |
| PUT | /strategies/{id} | 修改策略 | `StrategyUpdate` | `StrategyInfo` |
| DELETE | /strategies/{id} | 删除策略 | - | `null` |
| POST | /strategies/{id}/start | 启动策略 | - | `StrategyInfo` |
| POST | /strategies/{id}/pause | 暂停策略 | - | `StrategyInfo` |
| POST | /strategies/{id}/stop | 停止策略 | - | `StrategyInfo` |

#### 投注记录

| 方法 | 路径 | 说明 | 请求体 | 响应 data |
|------|------|------|--------|-----------|
| GET | /bet-orders | 投注记录列表 | query: date_from, date_to, strategy_id, page, page_size | `PagedData[BetOrderInfo]` |
| GET | /bet-orders/{id} | 投注详情 | - | `BetOrderInfo` |

#### 仪表盘

| 方法 | 路径 | 说明 | 请求体 | 响应 data |
|------|------|------|--------|-----------|
| GET | /dashboard | 操作者仪表盘 | - | `OperatorDashboard` |
| GET | /dashboard/recent-bets | 最近投注 | - | `list[BetOrderInfo]` |

#### 告警

| 方法 | 路径 | 说明 | 请求体 | 响应 data |
|------|------|------|--------|-----------|
| GET | /alerts | 告警列表 | query: is_read, page, page_size | `PagedData[AlertInfo]` |
| PUT | /alerts/{id}/read | 标记已读 | - | `null` |
| PUT | /alerts/read-all | 全部已读 | - | `null` |
| GET | /alerts/unread-count | 未读数量 | - | `{count}` |


### 2.3 核心 Schema 定义

```python
# backend/app/schemas/auth.py
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6)

class TokenResponse(BaseModel):
    token: str
    expire_at: str  # ISO 8601

# backend/app/schemas/operator.py
class OperatorCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6)
    max_accounts: int = Field(default=1, ge=1, le=100)
    expire_date: Optional[str] = None  # ISO 8601 date

class OperatorInfo(BaseModel):
    id: int
    username: str
    role: str
    status: str
    max_accounts: int
    expire_date: Optional[str]
    created_at: str

# backend/app/schemas/account.py
class AccountCreate(BaseModel):
    account_name: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    platform_type: Literal['JND28WEB', 'JND282']

class AccountInfo(BaseModel):
    id: int
    account_name: str
    password_masked: str  # 前2位+****
    platform_type: str
    status: str
    balance: float         # API 层返回元（int分 / 100）
    kill_switch: bool
    last_login_at: Optional[str]

# backend/app/schemas/strategy.py
class StrategyCreate(BaseModel):
    account_id: int
    name: str = Field(..., min_length=1, max_length=64)
    type: Literal['flat', 'martin']
    play_code: str  # KeyCode
    base_amount: float = Field(..., gt=0)
    martin_sequence: Optional[list[float]] = None  # 马丁倍率序列
    bet_timing: int = Field(default=30, ge=5, le=180)
    simulation: bool = False
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)

class StrategyInfo(BaseModel):
    id: int
    account_id: int
    name: str
    type: str
    play_code: str
    base_amount: float
    martin_sequence: Optional[list[float]]
    bet_timing: int
    simulation: bool
    status: str
    martin_level: int
    stop_loss: Optional[float]
    take_profit: Optional[float]
    daily_pnl: float
    total_pnl: float

# backend/app/schemas/bet_order.py
class BetOrderInfo(BaseModel):
    id: int
    idempotent_id: str
    issue: str
    key_code: str
    key_code_name: str  # 中文名
    amount: float
    odds: Optional[float]
    status: str
    open_result: Optional[str]
    sum_value: Optional[int]
    is_win: Optional[int]
    pnl: Optional[float]
    simulation: bool
    martin_level: Optional[int]
    bet_at: Optional[str]
    settled_at: Optional[str]
    fail_reason: Optional[str]

# backend/app/schemas/dashboard.py
class OperatorDashboard(BaseModel):
    balance: float
    daily_pnl: float
    total_pnl: float
    running_strategies: list[StrategyInfo]
    recent_bets: list[BetOrderInfo]
    unread_alerts: int

class AdminDashboard(BaseModel):
    total_operators: int
    active_operators: int
    operator_summaries: list[OperatorSummary]

class OperatorSummary(BaseModel):
    id: int
    username: str
    status: str
    daily_pnl: float
    total_pnl: float
    running_strategies: int
```


---

## 3. 投注引擎设计

### 3.1 引擎架构

投注引擎是后端的核心模块，基于 Python asyncio 实现。每个博彩账号运行一个独立的协程（AccountWorker），由 EngineManager 统一管理。

阻塞调用隔离策略：
- 所有 HTTP 请求使用 aiohttp（纯异步，无阻塞）
- 验证码识别（OCR）通过 `asyncio.to_thread()` 或 `loop.run_in_executor(ThreadPoolExecutor)` 隔离
- 数据库写入通过 aiosqlite（异步 SQLite 驱动）
- 任何潜在阻塞操作（文件 I/O、CPU 密集计算）均通过线程池隔离
- 每个 AccountWorker 内部异常不传播到其他 Worker（try/except + 自动重启）

```
EngineManager
├── AccountWorker (account_id=1)
│   ├── SessionManager      # 会话管理（登录/心跳/重连）
│   ├── IssuePoller          # 期号轮询（5秒/次）
│   ├── StrategyRunner[N]    # 策略运行器（每个策略一个）
│   ├── BetExecutor          # 下注执行器（合并+提交）
│   ├── SettlementProcessor  # 结算处理器
│   └── RiskController       # 风控检查器
├── AccountWorker (account_id=2)
│   └── ...
└── GlobalKillSwitch         # 全局熔断控制
```

### 3.2 AccountWorker 生命周期

```
创建 → 登录 → 主循环 → 停止
              ↓
         ┌─────────────────────────────────┐
         │ 主循环（每 5 秒）                 │
         │ 1. 轮询 GetCurrentInstall        │
         │ 2. 检测新期号 → 触发结算上期      │
         │ 3. 检测封盘状态                   │
         │ 4. 等待下注时机                   │
         │ 5. 收集所有策略的下注信号          │
         │ 6. 风控检查                       │
         │ 7. 合并下注 → Confirmbet          │
         │ 8. 记录注单                       │
         └─────────────────────────────────┘
```

下注时序与截止时间预算：
- 下注截止时间 = 封盘时间 - 10 秒安全余量
- 下注链路延迟预算：获取赔率(≤3s) + 风控检查(≤0.1s) + Confirmbet(≤5s) = 最大 8.1s
- 若 CloseTimeStamp ≤ 18 秒（8.1s链路 + 10s安全余量），跳过本期
  - 注：18s 是工程保护阈值（链路延迟预算 8.1s + 安全余量 10s = 18.1s，向下取整），业务边界仍是需求 5.1 的「最晚封盘前 10 秒」
- 使用平台 CloseTimeStamp 作为时间基准（不依赖本地时钟）
- trace_id 贯穿：每次下注生成 UUID trace_id，关联 API 请求→引擎→平台调用→注单记录
- 统一截止时间与取消传播：
  - 每次下注链路创建 `asyncio.timeout(deadline_seconds)` 上下文
  - deadline_seconds = CloseTimeStamp - 10（安全余量）
  - 任何外部调用（赔率/下注/OCR/DB）超过 deadline 自动取消
  - 超时后注单状态保持 pending（不进入半成功态），跳过本期
  - aiohttp 请求绑定 `timeout=aiohttp.ClientTimeout(total=per_call_limit)`
- 线程池配置：
  - 验证码 OCR 使用专用 `ThreadPoolExecutor(max_workers=10)`
  - 队列上限 100，超出直接拒绝（返回验证码服务繁忙）
  - 不与默认线程池共享，避免互相影响

### 3.3 核心类设计

```python
# backend/app/engine/manager.py
class EngineManager:
    """投注引擎管理器，FastAPI 启动时初始化"""
    workers: dict[int, AccountWorker]  # account_id → worker
    global_kill: bool

    async def start_worker(self, account_id: int) -> None: ...
    async def stop_worker(self, account_id: int) -> None: ...
    async def global_kill_switch(self) -> None: ...
    async def restore_workers_on_startup(self) -> None: ...

# backend/app/engine/worker.py
class AccountWorker:
    """单个博彩账号的投注协程"""
    account_id: int
    session: SessionManager
    poller: IssuePoller
    strategies: dict[int, StrategyRunner]
    executor: BetExecutor
    settler: SettlementProcessor
    risk: RiskController

    async def run(self) -> None:
        """主循环"""
        await self.session.login()
        while self.running:
            install = await self.poller.poll()
            if install.is_new_issue:
                await self.settler.settle(install.pre_issue, install.pre_result)
            if install.state != 1:
                await asyncio.sleep(30)  # 封盘探测
                continue
            await self._wait_bet_timing(install)
            signals = await self._collect_signals(install)
            if signals:
                await self.executor.execute(install, signals)

# backend/app/engine/session.py
class SessionManager:
    """博彩平台会话管理"""
    async def login(self) -> None: ...
    async def heartbeat(self) -> None: ...  # 每 75 秒
    async def refresh_token(self) -> None: ...
    async def ensure_session(self) -> bool: ...

# backend/app/engine/poller.py
class IssuePoller:
    """期号轮询器"""
    last_issue: str
    async def poll(self) -> InstallInfo: ...
    def is_known_downtime(self) -> bool: ...  # 19:56-20:33, 06:00-07:00

# backend/app/engine/strategy_runner.py
class StrategyRunner:
    """策略运行器"""
    strategy_id: int
    async def compute_signal(self, install: InstallInfo) -> Optional[BetSignal]: ...

class FlatStrategy(StrategyRunner):
    """平注策略"""
    async def compute_signal(self, install: InstallInfo) -> Optional[BetSignal]:
        return BetSignal(key_code=self.play_code, amount=self.base_amount)

class MartinStrategy(StrategyRunner):
    """马丁策略"""
    sequence: list[float]
    current_level: int
    async def compute_signal(self, install: InstallInfo) -> Optional[BetSignal]:
        multiplier = self.sequence[self.current_level]
        return BetSignal(key_code=self.play_code, amount=self.base_amount * multiplier)
    def on_win(self) -> None: self.current_level = 0
    def on_lose(self) -> None:
        self.current_level = (self.current_level + 1) % len(self.sequence)
    def on_refund(self) -> None: pass  # 不变
```


### 3.4 下注执行器

```python
# backend/app/engine/executor.py
class BetExecutor:
    """下注执行器：合并 + 风控检查 + 提交"""

    async def execute(self, install: InstallInfo, signals: list[BetSignal]) -> None:
        # 1. 幂等检查：过滤已下注的信号
        new_signals = [s for s in signals if not await self._is_duplicate(s)]

        # 2. 风控检查（逐个信号）
        approved = []
        for signal in new_signals:
            check = await self.risk.check(signal)
            if check.passed:
                approved.append(signal)
            else:
                await self._record_skip(signal, check.reason)

        if not approved:
            return

        # 3. 获取最新赔率
        odds = await self.adapter.load_odds(install.issue)

        # 4. 构建 betdata 并合并提交
        betdata = []
        for signal in approved:
            if odds.get(signal.key_code, 0) == 0:
                await self._record_skip(signal, "赔率为0，玩法暂停")
                continue
            betdata.append({
                "Amount": signal.amount,
                "KeyCode": signal.key_code,
                "Odds": odds[signal.key_code]
            })

        if betdata:
            result = await self.adapter.place_bet(install.issue, betdata)
            await self._process_result(result, approved)

@dataclass
class BetSignal:
    strategy_id: int
    key_code: str
    amount: float
    idempotent_id: str  # {期号}-{策略ID}-{KeyCode}
    martin_level: Optional[int] = None
    simulation: bool = False
```

### 3.5 结算引擎

```python
# backend/app/engine/settlement.py
class SettlementProcessor:
    """结算处理器"""

    async def settle(self, issue: str, open_result: str) -> None:
        """结算指定期号的所有注单"""
        balls = [int(x) for x in open_result.split(",")]
        sum_value = sum(balls)

        # 缓存开奖结果
        await self._save_lottery_result(issue, open_result, sum_value)

        # 查询该期所有待结算注单
        orders = await self._get_pending_orders(issue)

        for order in orders:
            result = self._calculate_result(order, balls, sum_value)
            await self._update_order(order, result)
            await self._update_strategy_pnl(order.strategy_id, result)

        # 余额交叉校验
        await self._cross_check_balance()

    def _calculate_result(self, order: BetOrder, balls: list[int], sum_value: int) -> SettleResult:
        """根据盘口类型和玩法计算结算结果"""
        platform_type = order.account.platform_type

        # JND282 退款规则
        if platform_type == 'JND282':
            if sum_value == 14 and order.key_code in ('DX1', 'DS4', 'ZH8'):
                return SettleResult(is_win=-1, pnl=0)  # 退款
            if sum_value == 13 and order.key_code in ('DX2', 'DS3', 'ZH9'):
                return SettleResult(is_win=-1, pnl=0)  # 退款

        # 判断是否中奖
        is_win = self._check_win(order.key_code, balls, sum_value)

        if is_win:
            pnl = order.amount * order.odds // 1000 - order.amount  # 整数分运算
        else:
            pnl = -order.amount  # 亏损

        return SettleResult(is_win=1 if is_win else 0, pnl=pnl)

    def _check_win(self, key_code: str, balls: list[int], sum_value: int) -> bool:
        """判断是否中奖"""
        # 大小
        if key_code == 'DX1': return sum_value >= 14
        if key_code == 'DX2': return sum_value <= 13
        # 单双
        if key_code == 'DS3': return sum_value % 2 == 1
        if key_code == 'DS4': return sum_value % 2 == 0
        # 极值
        if key_code == 'JDX5': return sum_value >= 22
        if key_code == 'JDX6': return sum_value <= 5
        # 组合
        if key_code == 'ZH7': return sum_value >= 14 and sum_value % 2 == 1  # 大单
        if key_code == 'ZH8': return sum_value >= 14 and sum_value % 2 == 0  # 大双
        if key_code == 'ZH9': return sum_value <= 13 and sum_value % 2 == 1  # 小单
        if key_code == 'ZH10': return sum_value <= 13 and sum_value % 2 == 0  # 小双
        # 和值
        if key_code.startswith('HZ'):
            target = int(key_code[2:]) - 1  # HZ1=和值0, HZ28=和值27
            return sum_value == target
        # 色波
        if key_code.startswith('SB'):
            return self._check_color(sum_value, key_code)
        # 豹子
        if key_code == 'BZ4': return balls[0] == balls[1] == balls[2]
        # 单球号码
        if key_code.startswith('B') and 'QH' in key_code:
            ball_idx = int(key_code[1]) - 1
            target_num = int(key_code.split('QH')[1])
            return balls[ball_idx] == target_num
        # 单球两面
        if key_code.startswith('B') and 'LM' in key_code:
            ball_idx = int(key_code[1]) - 1
            suffix = key_code.split('LM_')[1]
            return self._check_ball_lm(balls[ball_idx], suffix)
        # 龙虎和
        if key_code == 'LHH_L': return balls[0] > balls[2]
        if key_code == 'LHH_H': return balls[0] < balls[2]
        if key_code == 'LHH_HE': return balls[0] == balls[2]
        return False
```

### 3.5.1 对账闭环

```python
# backend/app/engine/reconciler.py
class Reconciler:
    """对账处理器：每期结算后执行"""

    async def reconcile(self, account_id: int, issue: str) -> None:
        """对账流程"""
        # 1. 拉取平台注单（Topbetlist）
        platform_bets = await self.adapter.get_bet_history(count=15)

        # 2. 获取本地该期注单
        local_orders = await self._get_local_orders(account_id, issue)

        # 3. 比对注单数量
        local_count = len(local_orders)
        platform_count = self._count_platform_bets(platform_bets, issue)

        # 4. 获取平台余额
        balance_info = await self.adapter.query_balance()
        platform_balance = int(balance_info.balance * 100)  # 转为分

        # 5. 计算本地理论余额
        local_balance = await self._calc_local_balance(account_id)

        # 6. 计算差额
        diff = abs(platform_balance - local_balance)

        # 7. 写入对账记录
        status = 'matched' if diff <= 100 else 'mismatch'  # 容差 ±1 元
        await self._save_reconcile_record(
            account_id, issue, local_count, platform_count,
            local_balance, platform_balance, diff, status
        )

        # 8. 差异处理
        if status == 'mismatch':
            # 标记相关注单为 reconcile_error
            for order in local_orders:
                await self._transition_status(order, 'reconcile_error')
            # 发送告警
            await self.alert_service.send(
                operator_id=self.operator_id,
                alert_type='reconcile_error',
                title=f'对账异常：期号 {issue}',
                detail={'diff': diff, 'local': local_balance, 'platform': platform_balance}
            )
```

对账策略：
- 每期结算完成后自动触发对账
- 余额快照取点：结算完成后（所有注单状态更新完毕）立即获取平台余额
- 在途单处理：未结算注单（bet_success 状态）的金额从本地余额中扣除后再比对
- 容差范围：单期 ±100 分（±1 元），累计差异超过 ±500 分（±5 元）触发 critical 告警
- mismatch 不自动停止策略，但在仪表盘醒目提示
- 连续 3 期 mismatch 自动暂停该账号所有策略，通知操作者
- 对账记录持久化到 reconcile_records 表
- 管理员可查看所有对账异常记录


### 3.6 风控模块

```python
# backend/app/engine/risk.py
class RiskController:
    """风控检查器"""

    async def check(self, signal: BetSignal) -> RiskCheckResult:
        """按顺序执行风控检查"""
        checks = [
            self._check_kill_switch,
            self._check_session,
            self._check_strategy_status,
            self._check_operator_status,
            self._check_balance,
            self._check_single_bet_limit,
            self._check_daily_limit,
            self._check_period_limit,
            self._check_stop_loss,
            self._check_take_profit,
        ]
        for check_fn in checks:
            result = await check_fn(signal)
            if not result.passed:
                return result
        return RiskCheckResult(passed=True)

    async def _check_kill_switch(self, signal) -> RiskCheckResult:
        """检查熔断状态"""
        if self.global_kill or self.account_kill:
            return RiskCheckResult(passed=False, reason="熔断已触发")
        return RiskCheckResult(passed=True)

    async def _check_stop_loss(self, signal) -> RiskCheckResult:
        """检查止损线"""
        strategy = await self._get_strategy(signal.strategy_id)
        if strategy.stop_loss and strategy.daily_pnl <= -strategy.stop_loss:
            await self._trigger_alert('stop_loss', strategy)
            return RiskCheckResult(passed=False, reason=f"触发止损线: {strategy.daily_pnl}")
        return RiskCheckResult(passed=True)

@dataclass
class RiskCheckResult:
    passed: bool
    reason: str = ""
```

### 3.7 会话管理

```python
# backend/app/engine/session.py
class SessionManager:
    """博彩平台会话管理"""
    account_id: int
    adapter: PlatformAdapter
    session_token: Optional[str] = None
    heartbeat_task: Optional[asyncio.Task] = None
    login_fail_count: int = 0
    captcha_fail_count: int = 0

    async def login(self) -> bool:
        """登录博彩平台，含验证码识别和重试逻辑"""
        retry_delays = [30, 60, 120]  # 前3次递增等待
        for attempt in range(5):
            try:
                captcha = await self.adapter.get_captcha()
                code = await self._recognize_captcha(captcha)
                result = await self.adapter.login(self.account_name, self.password, code)
                if result.success:
                    self.session_token = result.token
                    self.login_fail_count = 0
                    self.captcha_fail_count = 0
                    self._start_heartbeat()
                    return True
                else:
                    self.login_fail_count += 1
            except CaptchaError:
                self.captcha_fail_count += 1
                if self.captcha_fail_count >= 5:
                    await self._alert('captcha_fail', count=self.captcha_fail_count)
                    return False

            # 重试等待策略
            if attempt < len(retry_delays):
                # 前3次：递增等待 30s → 60s → 120s
                await asyncio.sleep(retry_delays[attempt])
            if attempt == 2:
                # 连续3次失败后，额外暂停10分钟再继续第4次尝试
                await asyncio.sleep(600)

        # 连续5次失败，标记异常
        await self._mark_login_error()
        await self._alert('login_fail', count=self.login_fail_count)
        return False

    async def _heartbeat_loop(self) -> None:
        """心跳循环，每 75 秒"""
        consecutive_fails = 0
        while True:
            await asyncio.sleep(75)
            try:
                ok = await self.adapter.heartbeat()
                if ok:
                    consecutive_fails = 0
                else:
                    consecutive_fails += 1
            except Exception:
                consecutive_fails += 1

            if consecutive_fails >= 3:
                await self._reconnect()
                consecutive_fails = 0
```


---

## 4. 平台适配层

### 4.1 抽象基类

```python
# backend/app/engine/adapters/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

@dataclass
class InstallInfo:
    issue: str
    state: int
    close_countdown: int
    open_countdown: int
    pre_issue: str
    pre_result: str
    is_new_issue: bool = False

@dataclass
class BetResult:
    succeed: int
    message: str
    bet_list: Optional[list] = None

@dataclass
class BalanceInfo:
    balance: float
    today_pnl: float
    unsettled: float

class PlatformAdapter(ABC):
    """博彩平台适配器抽象基类"""

    @abstractmethod
    async def login(self, account: str, password: str, captcha: str) -> LoginResult: ...

    @abstractmethod
    async def get_current_install(self) -> InstallInfo: ...

    @abstractmethod
    async def load_odds(self, issue: str) -> dict[str, float]: ...

    @abstractmethod
    async def place_bet(self, issue: str, betdata: list[dict]) -> BetResult: ...

    @abstractmethod
    async def query_balance(self) -> BalanceInfo: ...

    @abstractmethod
    async def get_bet_history(self, count: int = 15) -> list[dict]: ...

    @abstractmethod
    async def get_lottery_results(self, start: int, rows: int) -> list[dict]: ...

    @abstractmethod
    async def heartbeat(self) -> bool: ...
```

### 4.2 JND 平台适配器

```python
# backend/app/engine/adapters/jnd.py
class JNDAdapter(PlatformAdapter):
    """JND28 平台适配器（支持 JND28WEB 和 JND282 两种盘口）"""

    def __init__(self, base_url: str, lottery_type: str):
        self.base_url = base_url
        self.lottery_type = lottery_type  # 'JND28WEB' or 'JND282'
        self.session: Optional[aiohttp.ClientSession] = None
        self.default_headers = {
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

    async def get_current_install(self) -> InstallInfo:
        url = f"{self.base_url}/PlaceBet/GetCurrentInstall?lotteryType={self.lottery_type}"
        data = await self._post(url)
        return InstallInfo(
            issue=data["Installments"],
            state=data["State"],
            close_countdown=data["CloseTimeStamp"],
            open_countdown=data["OpenTimeStamp"],
            pre_issue=data.get("PreInstallments", ""),
            pre_result=data.get("PreLotteryResult", ""),
        )

    async def place_bet(self, issue: str, betdata: list[dict]) -> BetResult:
        url = f"{self.base_url}/PlaceBet/Confirmbet"
        form_data = {}
        for i, bet in enumerate(betdata):
            form_data[f"betdata[{i}][Amount]"] = str(bet["Amount"])
            form_data[f"betdata[{i}][KeyCode]"] = bet["KeyCode"]
            form_data[f"betdata[{i}][Odds]"] = str(bet["Odds"])
        form_data["lotteryType"] = self.lottery_type
        form_data["install"] = issue
        resp = await self._post(url, data=form_data)
        return BetResult(
            succeed=resp.get("succeed", 0),
            message=resp.get("msg", ""),
            bet_list=resp.get("betList"),
        )
```

### 4.3 平台配置

```python
# backend/app/engine/adapters/config.py
PLATFORM_CONFIGS = {
    "JND28WEB": {
        "base_url": "https://3894703925-vvc0.mm555.co",
        "lottery_type": "JND28WEB",
        "template_code": "JNDPCDD",
        "downtime_ranges": [
            ("19:56", "20:33"),
            ("06:00", "07:00"),
        ],
        "refund_rules": {},  # 网盘无退款规则
    },
    "JND282": {
        "base_url": "https://3894703925-vvc0.mm555.co",
        "lottery_type": "JND282",
        "template_code": "JNDPCDD",
        "downtime_ranges": [
            ("19:56", "20:33"),
            ("06:00", "07:00"),
        ],
        "refund_rules": {
            14: {"DX1", "DS4", "ZH8"},   # 和值14退款的玩法
            13: {"DX2", "DS3", "ZH9"},   # 和值13退款的玩法
        },
    },
}
```


---

## 5. 前端设计

### 5.1 页面结构

```
frontend/src/
├── pages/
│   ├── Login.tsx                # 登录页
│   ├── operator/                # 操作者页面
│   │   ├── Dashboard.tsx        # 操作者仪表盘
│   │   ├── Accounts.tsx         # 博彩账号管理
│   │   ├── Strategies.tsx       # 策略管理
│   │   ├── StrategyForm.tsx     # 策略创建/编辑表单
│   │   ├── BetOrders.tsx        # 投注记录
│   │   └── Alerts.tsx           # 告警中心
│   └── admin/                   # 管理员页面
│       ├── Dashboard.tsx        # 管理员仪表盘
│       ├── Operators.tsx        # 操作者管理
│       └── OperatorDetail.tsx   # 操作者详情（下钻）
├── components/
│   ├── Layout.tsx               # 布局（侧边栏+顶栏）
│   ├── AlertBadge.tsx           # 未读告警徽标
│   ├── StrategyStatusTag.tsx    # 策略状态标签
│   └── BetOrderTable.tsx        # 投注记录表格（复用）
├── api/
│   ├── request.ts               # 统一请求层
│   ├── auth.ts                  # 认证 API
│   ├── accounts.ts              # 博彩账号 API
│   ├── strategies.ts            # 策略 API
│   ├── bet-orders.ts            # 投注记录 API
│   ├── dashboard.ts             # 仪表盘 API
│   ├── alerts.ts                # 告警 API
│   └── admin.ts                 # 管理员 API
├── types/api/
│   ├── common.ts                # ApiResponse, PagedData
│   ├── auth.ts                  # LoginRequest, TokenResponse
│   ├── operator.ts              # OperatorCreate, OperatorInfo
│   ├── account.ts               # AccountCreate, AccountInfo
│   ├── strategy.ts              # StrategyCreate, StrategyInfo
│   ├── bet-order.ts             # BetOrderInfo
│   ├── dashboard.ts             # OperatorDashboard, AdminDashboard
│   └── alert.ts                 # AlertInfo
├── hooks/
│   ├── useAuth.ts               # 认证状态管理
│   ├── useAlerts.ts             # 告警轮询
│   └── useDashboard.ts          # 仪表盘数据轮询
└── utils/
    ├── key-code-map.ts          # KeyCode → 中文名映射
    └── format.ts                # 金额/日期格式化
```

### 5.2 统一请求层

```typescript
// frontend/src/api/request.ts
import type { ApiResponse } from '@/types/api/common';

const BASE_URL = import.meta.env.VITE_API_BASE_URL;  // '/api/v1'

export class ApiError extends Error {
  code: number;
  constructor(code: number, message: string) {
    super(message);
    this.code = code;
  }
}

export async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const token = localStorage.getItem('token');

  const res = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  if (res.status === 401) {
    localStorage.removeItem('token');
    window.location.href = '/login';
    throw new ApiError(2001, 'Token 过期');
  }

  const body: ApiResponse<T> = await res.json();

  if (body.code !== 0) {
    throw new ApiError(body.code, body.message);
  }

  return body.data as T;
}
```

### 5.3 告警轮询

一期使用 HTTP 轮询实现站内通知（每 15 秒查询未读数量），二期可升级为 WebSocket/SSE。

```typescript
// frontend/src/hooks/useAlerts.ts
export function useAlerts() {
  const [unreadCount, setUnreadCount] = useState(0);

  useEffect(() => {
    const poll = async () => {
      try {
        const data = await fetchUnreadCount();
        setUnreadCount(data.count);
      } catch { /* ignore */ }
    };
    poll();
    const timer = setInterval(poll, 15000);
    return () => clearInterval(timer);
  }, []);

  return { unreadCount };
}
```


---

## 6. 策略注入接口规范

### 6.1 接口定义

```python
# backend/app/engine/strategies/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

@dataclass
class StrategyContext:
    """策略计算上下文"""
    current_issue: str
    history: list[LotteryResult]  # 最近 N 期开奖数据
    balance: float
    strategy_state: dict  # 策略自定义状态（如马丁级别）

@dataclass
class LotteryResult:
    issue: str
    balls: list[int]
    sum_value: int

@dataclass
class BetInstruction:
    key_code: str
    amount: float

class BaseStrategy(ABC):
    """策略基类，所有策略必须继承此类"""

    @abstractmethod
    def name(self) -> str:
        """策略名称"""
        ...

    @abstractmethod
    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        """
        计算本期下注指令。
        返回空列表表示本期不投注。
        """
        ...

    def on_result(self, is_win: Optional[int], pnl: float) -> None:
        """
        结算回调。
        is_win: 1=中奖, 0=未中, -1=退款
        pnl: 盈亏金额
        子类可覆盖此方法更新内部状态。
        """
        pass
```

### 6.2 策略注册

```python
# backend/app/engine/strategies/registry.py
_STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {}

def register_strategy(name: str):
    """装饰器：注册策略"""
    def decorator(cls):
        _STRATEGY_REGISTRY[name] = cls
        return cls
    return decorator

def get_strategy_class(name: str) -> type[BaseStrategy]:
    return _STRATEGY_REGISTRY[name]

# 内置策略注册
@register_strategy("flat")
class FlatStrategyImpl(BaseStrategy): ...

@register_strategy("martin")
class MartinStrategyImpl(BaseStrategy): ...
```

---

## 7. 告警系统设计

### 7.1 告警类型

| 类型 | 级别 | 触发条件 | 接收者 |
|------|------|---------|--------|
| login_fail | critical | 登录失败 | 操作者 |
| captcha_fail | warning | 验证码连续失败5次 | 操作者 |
| session_lost | warning | 会话断线 | 操作者 |
| bet_fail | warning | 下注失败 | 操作者 |
| reconcile_error | critical | 对账异常 | 操作者 |
| balance_low | warning | 连续3期余额不足 | 操作者 |
| stop_loss | info | 触发止损 | 操作者 |
| take_profit | info | 触发止盈 | 操作者 |
| martin_reset | info | 马丁序列跑完一轮 | 操作者 |
| platform_limit | warning | 超平台限额 | 操作者 |
| system_api_fail | critical | 30%+账号请求失败 | 管理员 |
| consecutive_fail | critical | 连续5期下注失败 | 管理员 |

### 7.2 告警发送

```python
# backend/app/engine/alert.py
class AlertService:
    async def send(self, operator_id: int, alert_type: str, title: str, detail: dict) -> None:
        level = ALERT_LEVEL_MAP.get(alert_type, 'warning')
        await db.execute(
            "INSERT INTO alerts (operator_id, type, level, title, detail) VALUES (?, ?, ?, ?, ?)",
            (operator_id, alert_type, level, title, json.dumps(detail, ensure_ascii=False))
        )
```


---

## 8. 认证与授权

### 8.1 JWT 方案

```python
# backend/app/utils/auth.py
import jwt
from datetime import datetime, timedelta

SECRET_KEY = "your-secret-key"  # 生产环境从环境变量读取
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24
REFRESH_WINDOW_MINUTES = 30

def create_token(operator_id: int, role: str) -> str:
    payload = {
        "sub": operator_id,
        "role": role,
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
```

### 8.2 数据隔离

所有操作者 API 通过 JWT 中的 `sub`（operator_id）自动过滤数据：

```python
# backend/app/api/dependencies.py
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer

security = HTTPBearer()

async def get_current_operator(credentials = Depends(security)) -> Operator:
    payload = verify_token(credentials.credentials)
    operator = await get_operator_by_id(payload["sub"])
    if not operator or operator.status != 'active':
        raise HTTPException(status_code=401, detail="账户不可用")
    return operator

async def require_admin(operator = Depends(get_current_operator)) -> Operator:
    if operator.role != 'admin':
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return operator
```

### 8.3 单会话控制

```python
# 登录时记录 token 的 jti（JWT ID），踢掉旧会话
# 使用内存字典存储活跃 session（一期足够，二期可迁移 Redis）
ACTIVE_SESSIONS: dict[int, str] = {}  # operator_id → jti

def create_token(operator_id: int, role: str) -> str:
    jti = str(uuid.uuid4())
    ACTIVE_SESSIONS[operator_id] = jti
    payload = {"sub": operator_id, "role": role, "jti": jti, ...}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str) -> dict:
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    if ACTIVE_SESSIONS.get(payload["sub"]) != payload["jti"]:
        raise HTTPException(status_code=401, detail="会话已被踢出")
    return payload
```

---

## 9. 后端目录结构

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI 入口，统一 prefix
│   ├── database.py              # SQLite 连接管理
│   ├── api/                     # API 路由
│   │   ├── __init__.py
│   │   ├── auth.py              # 认证路由
│   │   ├── accounts.py          # 博彩账号路由
│   │   ├── strategies.py        # 策略路由
│   │   ├── bet_orders.py        # 投注记录路由
│   │   ├── dashboard.py         # 仪表盘路由
│   │   ├── alerts.py            # 告警路由
│   │   └── admin.py             # 管理员路由
│   ├── schemas/                 # Pydantic v2 Schema
│   │   ├── __init__.py
│   │   ├── common.py            # ApiResponse, PagedData
│   │   ├── auth.py
│   │   ├── operator.py
│   │   ├── account.py
│   │   ├── strategy.py
│   │   ├── bet_order.py
│   │   ├── dashboard.py
│   │   └── alert.py
│   ├── models/                  # 数据库操作层
│   │   ├── __init__.py
│   │   └── db_ops.py            # CRUD 操作
│   ├── engine/                  # 投注引擎
│   │   ├── __init__.py
│   │   ├── manager.py           # EngineManager
│   │   ├── worker.py            # AccountWorker
│   │   ├── session.py           # SessionManager
│   │   ├── poller.py            # IssuePoller
│   │   ├── strategy_runner.py   # StrategyRunner
│   │   ├── executor.py          # BetExecutor
│   │   ├── settlement.py        # SettlementProcessor
│   │   ├── reconciler.py       # Reconciler（对账）
│   │   ├── risk.py              # RiskController
│   │   ├── alert.py             # AlertService
│   │   ├── adapters/            # 平台适配器
│   │   │   ├── __init__.py
│   │   │   ├── base.py          # PlatformAdapter ABC
│   │   │   ├── jnd.py           # JND 适配器
│   │   │   └── config.py        # 平台配置
│   │   └── strategies/          # 策略实现
│   │       ├── __init__.py
│   │       ├── base.py          # BaseStrategy ABC
│   │       ├── registry.py      # 策略注册表
│   │       ├── flat.py          # 平注策略
│   │       └── martin.py        # 马丁策略
│   └── utils/
│       ├── __init__.py
│       ├── auth.py              # JWT 工具
│       ├── response.py          # 统一信封中间件/装饰器
│       ├── logger.py            # 结构化 JSON 日志
│       ├── captcha.py           # 验证码识别
│       └── key_code_map.py      # KeyCode 映射
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # pytest fixtures
│   ├── test_settlement.py       # 结算逻辑测试（P1-P6, P16）
│   ├── test_martin.py           # 马丁策略测试（P7-P8）
│   ├── test_executor.py         # 下注执行器测试（P9, P12, P23, P26）
│   ├── test_risk.py             # 风控逻辑测试（P10-P11, P19）
│   ├── test_state_machine.py    # 注单状态机测试（P15）
│   ├── test_reconciler.py       # 对账逻辑测试（P18）
│   ├── test_rate_limiter.py     # 限频队列测试（P20）
│   ├── test_session.py          # 会话管理测试（P21）
│   ├── test_worker.py           # AccountWorker 测试（P22, P27）
│   ├── test_database.py         # DB 触发器测试（P24）
│   ├── test_db_ops.py           # DAO 数据隔离测试（P25）
│   └── test_concurrency.py      # 并发与可靠性测试
└── pyproject.toml
```


---

## 10. API 频率控制

```python
# backend/app/engine/rate_limiter.py
import asyncio
from collections import defaultdict
import time

class RateLimiter:
    """每个博彩账号的 API 调用频率控制"""

    LIMITS = {
        "GetCurrentInstall": 5,    # 最多每 5 秒 1 次
        "Loaddata": 3,             # 最多每 3 秒 1 次
        "Confirmbet": None,        # 每期最多 1 次（由 BetExecutor 控制）
        "QueryResult": 10,         # 最多每 10 秒 1 次
        "Topbetlist": 10,          # 最多每 10 秒 1 次
        "Online": 75,              # 固定每 75 秒 1 次
    }

    def __init__(self):
        self._last_call: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, api_name: str) -> None:
        """等待直到可以调用该 API"""
        limit = self.LIMITS.get(api_name)
        if limit is None:
            return
        async with self._locks[api_name]:
            elapsed = time.monotonic() - self._last_call[api_name]
            if elapsed < limit:
                await asyncio.sleep(limit - elapsed)
            self._last_call[api_name] = time.monotonic()
```

---

## 11. 停盘处理

```python
# backend/app/engine/poller.py (停盘检测逻辑)
class IssuePoller:
    KNOWN_DOWNTIMES = [("19:56", "20:33"), ("06:00", "07:00")]
    RANDOM_DOWNTIME_THRESHOLD = 3  # 连续3次非开盘状态

    def __init__(self):
        self.last_issue = ""
        self.non_open_count = 0

    async def poll(self) -> InstallInfo:
        if self._is_known_downtime():
            await asyncio.sleep(30)  # 已知停盘，30秒探测
            return await self._fetch_install()

        install = await self._fetch_install()

        if install.state != 1:
            self.non_open_count += 1
            if self.non_open_count >= self.RANDOM_DOWNTIME_THRESHOLD:
                # 随机停盘，切换到慢速探测
                await self._enter_downtime_mode()
        else:
            self.non_open_count = 0

        if install.issue != self.last_issue:
            install.is_new_issue = True
            self.last_issue = install.issue

        return install
```

---

## 12. 正确性属性（Correctness Properties）

以下属性用于 Property-Based Testing，确保系统核心逻辑的正确性。

### 12.1 结算正确性属性

**P1: 和值计算一致性**
- 对于任意三个球 (b1, b2, b3)，其中 0 ≤ bi ≤ 9，和值 = b1 + b2 + b3，范围 [0, 27]
- 测试框架：hypothesis
- 验证：Requirements 3.1, 3.2

**P2: 大小判定正确性**
- 对于任意和值 s ∈ [0, 27]：s ≥ 14 → DX1 中奖；s ≤ 13 → DX2 中奖
- DX1 和 DX2 互斥（不可能同时中奖）
- 测试框架：hypothesis
- 验证：Requirements 3.1

**P3: 单双判定正确性**
- 对于任意和值 s ∈ [0, 27]：s 为奇数 → DS3 中奖；s 为偶数 → DS4 中奖
- DS3 和 DS4 互斥
- 测试框架：hypothesis
- 验证：Requirements 3.1

**P4: 组合玩法与大小单双一致性**
- ZH7(大单) 中奖 ⟺ DX1(大) 中奖 ∧ DS3(单) 中奖
- ZH8(大双) 中奖 ⟺ DX1(大) 中奖 ∧ DS4(双) 中奖
- ZH9(小单) 中奖 ⟺ DX2(小) 中奖 ∧ DS3(单) 中奖
- ZH10(小双) 中奖 ⟺ DX2(小) 中奖 ∧ DS4(双) 中奖
- 测试框架：hypothesis
- 验证：Requirements 3.1

**P5: JND282 退款规则正确性**
- 和值=14 时：DX1/DS4/ZH8 → 退款（is_win=-1, pnl=0）
- 和值=13 时：DX2/DS3/ZH9 → 退款（is_win=-1, pnl=0）
- 和值≠13且≠14 时：无退款
- JND28WEB 盘口：任何和值都不退款
- 测试框架：hypothesis
- 验证：Requirements 3.2

**P6: 盈亏计算正确性**
- 中奖时：pnl = amount * odds // 1000 - amount（整数分运算），pnl > 0
- 未中奖时：pnl = -amount，pnl < 0
- 退款时：pnl = 0
- 所有运算结果为整数（分）
- 测试框架：hypothesis
- 验证：Requirements 7.1


### 12.2 马丁策略属性

**P7: 马丁序列状态转移正确性**
- 中奖 → level 重置为 0
- 未中奖 → level = (level + 1) % len(sequence)
- 退款 → level 不变
- 对于任意初始 level 和任意结果序列，level 始终在 [0, len(sequence)-1] 范围内
- 测试框架：hypothesis
- 验证：Requirements 4.2

**P8: 马丁下注金额正确性**
- 下注金额 = base_amount × sequence[current_level]
- 对于任意 base_amount > 0 和任意合法 sequence，下注金额 > 0
- 测试框架：hypothesis
- 验证：Requirements 4.2

### 12.3 幂等性属性

**P9: 幂等 ID 唯一性**
- 对于任意 (issue, strategy_id, key_code) 三元组，生成的幂等 ID 唯一
- 不同三元组生成不同的幂等 ID
- 格式：`{issue}-{strategy_id}-{key_code}`
- 测试框架：hypothesis
- 验证：Requirements 4.4, 5.2

### 12.4 风控属性

**P10: 止损止盈判定正确性**
- daily_pnl ≤ -stop_loss → 止损触发，策略暂停
- daily_pnl ≥ take_profit → 止盈触发，策略暂停
- -stop_loss < daily_pnl < take_profit → 不触发
- stop_loss/take_profit 为 None 时不触发
- 测试框架：hypothesis
- 验证：Requirements 6.2

**P11: 限额检查正确性**
- amount > single_bet_limit → 拒绝
- daily_total + amount > daily_limit → 拒绝
- period_total + amount > period_limit → 拒绝
- 所有限额检查通过 → 允许
- 测试框架：hypothesis
- 验证：Requirements 6.1

### 12.5 下注合并属性

**P12: 下注合并不丢失信号**
- 合并后的 betdata 条目数 = 通过风控检查的信号数（不合并相同 KeyCode）
- 每条 betdata 的 Amount/KeyCode/Odds 与原始信号一致
- 测试框架：hypothesis
- 验证：Requirements 5.4

### 12.6 前端属性

**P13: KeyCode 映射完整性**
- 所有已知 KeyCode 都有对应的中文名
- 映射函数对任意字符串输入不抛异常（未知 KeyCode 返回原始值）
- 测试框架：fast-check
- 验证：Requirements 3.1

**P14: 密码脱敏正确性**
- 对于任意长度 ≥ 2 的密码，脱敏结果 = 前2位 + "****"
- 对于长度 < 2 的密码，脱敏结果 = "****"
- 脱敏结果不包含原始密码的第3位及之后的字符
- 测试框架：fast-check
- 验证：Requirements 1.3

### 12.7 状态机属性

**P15: 注单状态转移合法性**
- 对于任意事件序列，注单状态只能按合法路径转移（见 1.4 状态机约束）
- 终态（bet_failed / settled / reconcile_error）不可变更
- 已结算注单的 pnl 不为 NULL，is_win 不为 NULL
- 测试框架：hypothesis（状态机测试，使用 RuleBasedStateMachine）
- 验证：Requirements 7.0

### 12.8 金额精度属性

**P16: 整数分运算无精度损失**
- 对于任意 amount（分）和 odds（×1000），盈亏计算 `amount * odds // 1000 - amount` 结果为整数
- 对于任意一系列下注和结算操作，余额始终为整数（分）
- 退款操作不改变余额（pnl=0）
- 测试框架：hypothesis
- 验证：Requirements 7.1

**P17: 元分转换往返一致性**
- 对于任意金额（元，精确到分），`to_fen(amount) → to_yuan(fen)` 往返转换后值不变
- `to_fen(1.23)` = 123，`to_yuan(123)` = 1.23
- 测试框架：hypothesis / fast-check
- 验证：Requirements 7.1

### 12.9 对账属性

**P18: 对账容差判定正确性**
- |local_balance - platform_balance| ≤ 100 → matched
- |local_balance - platform_balance| > 100 → mismatch
- 对于任意 local_balance 和 platform_balance，判定结果确定且一致
- 测试框架：hypothesis
- 验证：Requirements 7.1

### 12.10 风控顺序属性

**P19: 风控检查顺序不变性**
- checks 列表顺序固定：kill_switch → session → strategy_status → operator_status → balance → single_bet_limit → daily_limit → period_limit → stop_loss → take_profit
- 对于任意输入，执行顺序始终一致
- 短路返回（第一个失败即返回）不影响顺序定义
- 测试框架：hypothesis
- 验证：Requirements 6.1, 6.2

### 12.11 限频与会话属性

**P20: 限频队列不丢单 + 有界等待**
- 对于任意请求序列，排队请求数 = 总请求数 - 已执行数（不丢弃）
- 等待时间 ≤ LIMIT × 2（有界）
- 测试框架：hypothesis
- 验证：Requirements 2.5

**P21: 登录退避间隔单调递增**
- retry_delays 序列严格递增：retry_delays[i] < retry_delays[i+1]
- 具体值：30s → 60s → 120s
- 测试框架：hypothesis
- 验证：Requirements 2.2

### 12.12 Worker 恢复属性

**P22: Worker 异常恢复幂等性**
- 重启后状态与首次启动一致，无残留副作用
- 无重复注单（idempotent_id 去重）
- 无泄漏协程（asyncio task 计数不增长）
- 测试框架：hypothesis
- 验证：Requirements 10.2

### 12.13 高风险补充属性（P23-P27）

**P23: 幂等 ID 重放安全性**
- 对任意已存在的 idempotent_id，重复插入 bet_orders 表 → DB 抛 IntegrityError
- 不产生重复注单：bet_orders 表中该 idempotent_id 仅 1 行
- 并发场景：多协程同时插入相同 idempotent_id → 仅 1 条成功，其余被 UNIQUE 约束拒绝
- 测试框架：hypothesis
- 验证：Requirements 4.4, 5.2

**P24: 终态 DB 触发器强制性**
- 对任意终态注单（status ∈ {bet_failed, settled, reconcile_error}），直接 SQL UPDATE 修改 status → RAISE ABORT
- 行数据不变（UPDATE 前后 SELECT 结果一致）
- 应用层 _transition_status 也拒绝终态变更（抛 IllegalStateTransition）
- 测试框架：hypothesis
- 验证：Requirements 7.0

**P25: DAO 层数据隔离**
- 对任意两个不同 operator_id（A ≠ B），操作者 A 的 CRUD 方法无法读取/修改操作者 B 的数据
- 覆盖资源：gambling_accounts, strategies, bet_orders, alerts, audit_logs, reconcile_records
- lottery_results 为全局只读例外（所有操作者可读，无写入 API）
- 测试框架：hypothesis
- 验证：Requirements 1.4

**P26: Confirmbet 零重试**
- 对任意 place_bet 失败结果（succeed ≠ 1 或超时），BetExecutor 不发起第二次 place_bet 调用
- 注单直接进入 bet_failed 终态，fail_reason 非空
- mock 验证：place_bet 调用次数 = 1
- 测试框架：hypothesis
- 验证：Requirements 5.2

**P27: 18s 阈值跳过**
- 对任意 CloseTimeStamp ≤ 18，AccountWorker 跳过本期下注（不调用 executor.execute）
- 对任意 CloseTimeStamp > 18，AccountWorker 正常下注（调用 executor.execute）
- 边界值：17s → 跳过，18s → 跳过（≤18s），19s → 正常下注
- 时间基准：使用平台 CloseTimeStamp（不依赖本地时钟）
- 测试框架：hypothesis
- 验证：Requirements 5.1

---

## 13. 测试策略

### 13.1 后端测试（pytest + hypothesis）

| 测试文件 | 覆盖范围 | 属性 |
|---------|---------|------|
| test_settlement.py | 结算逻辑 | P1-P6, P16 |
| test_martin.py | 马丁策略 | P7-P8 |
| test_executor.py | 下注执行 | P9, P12, P23, P26 |
| test_risk.py | 风控模块 | P10-P11, P19 |
| test_state_machine.py | 注单状态机 | P15 |
| test_reconciler.py | 对账逻辑 | P18 |
| test_rate_limiter.py | 限频队列 | P20 |
| test_session.py | 会话管理 | P21 |
| test_worker.py | AccountWorker | P22, P27 |
| test_database.py | DB 触发器 | P24 |
| test_db_ops.py | DAO 数据隔离 | P25 |

### 13.2 前端测试（vitest + fast-check）

| 测试文件 | 覆盖范围 | 属性 |
|---------|---------|------|
| key-code-map.test.ts | KeyCode 映射 | P13 |
| format.test.ts | 密码脱敏、金额转换 | P14, P17 |

### 13.3 集成测试

- API 端点测试：使用 FastAPI TestClient
- 投注引擎测试：使用 mock adapter 模拟博彩平台响应
- 数据隔离测试：验证操作者间数据不可见

---

## 14. 部署与配置

### 14.1 环境变量

```
# backend/.env
SECRET_KEY=your-jwt-secret-key
DATABASE_URL=sqlite:///./bocai.db
PLATFORM_BASE_URL=https://3894703925-vvc0.mm555.co
CAPTCHA_SERVICE_URL=http://localhost:9000  # 验证码识别服务
```

### 14.2 数据库初始化

```python
# backend/app/database.py
import aiosqlite

DB_PATH = "bocai.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA_SQL)
        # 创建默认管理员
        await db.execute(
            "INSERT OR IGNORE INTO operators (username, password, role) VALUES (?, ?, ?)",
            ("admin", "admin123", "admin")
        )
        await db.commit()
```

### 14.3 FastAPI 启动集成

```python
# backend/app/main.py
from fastapi import FastAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时
    await init_db()
    engine = EngineManager()
    app.state.engine = engine
    await engine.restore_workers_on_startup()
    yield
    # 关闭时
    await engine.shutdown()

app = FastAPI(title="Bocai Backend", lifespan=lifespan)

# 统一 prefix 挂载
app.include_router(auth_router, prefix="/api/v1", tags=["auth"])
app.include_router(accounts_router, prefix="/api/v1", tags=["accounts"])
app.include_router(strategies_router, prefix="/api/v1", tags=["strategies"])
app.include_router(bet_orders_router, prefix="/api/v1", tags=["bet-orders"])
app.include_router(dashboard_router, prefix="/api/v1", tags=["dashboard"])
app.include_router(alerts_router, prefix="/api/v1", tags=["alerts"])
app.include_router(admin_router, prefix="/api/v1", tags=["admin"])
```


---

## 15. 二期架构预留

### 15.1 数据库迁移路径
- 一期使用 SQLite + WAL，表结构设计兼容 PostgreSQL
- 避免 SQLite 特有语法：不使用 `AUTOINCREMENT`（改用 `INTEGER PRIMARY KEY`，SQLite 自动递增）
- JSON 字段使用 TEXT 存储（PostgreSQL 迁移时改为 JSONB）
- 迁移触发条件：单库超过 10GB 或 100 万注单
- 迁移方案：停机迁移（一期单实例，可接受短暂停机）
  1. 导出 SQLite 数据为 SQL/CSV
  2. 创建 PostgreSQL schema（DDL 已兼容）
  3. 导入数据
  4. 更换连接驱动（aiosqlite → asyncpg）
  5. 验证数据完整性
- 回滚方案：保留 SQLite 文件，切回旧驱动

### 15.2 多实例扩展路径
- 一期：单进程 asyncio + 内存字典（ACTIVE_SESSIONS、EngineManager.workers）
- 二期迁移抽象：
  - `SessionStore` 接口：一期实现 `InMemorySessionStore`，二期实现 `RedisSessionStore`
  - `WorkerRegistry` 接口：一期实现 `InMemoryWorkerRegistry`，二期实现 `RedisWorkerRegistry`
  - 任务分配：二期引入 Redis 队列 + 分片（按 account_id hash 分配到不同实例）
- 代码中通过依赖注入使用接口，不直接引用内存字典

### 15.3 多平台扩展
- PlatformAdapter ABC 已定义，新平台只需实现子类
- PLATFORM_CONFIGS 字典支持动态加载
- gambling_accounts 表的 platform_type 字段支持扩展
- 适配器版本管理：每个适配器类包含 `VERSION` 属性，平台改版时新建子类而非修改原类

### 15.4 告警渠道扩展
- AlertService 可扩展为多渠道分发（Telegram、邮件等）
- 一期仅实现数据库写入 + 前端轮询
- 二期：AlertService 改为发布-订阅模式，渠道作为订阅者注册

### 15.5 AI 策略接入
- BaseStrategy ABC + 策略注册表已就绪
- AI 策略只需继承 BaseStrategy 并注册即可
- StrategyContext 提供历史数据和账户状态

---

## 16. 关键设计决策记录

| # | 决策 | 理由 |
|---|------|------|
| D1 | 一期使用 SQLite + WAL | 轻量级，单机部署足够，WAL 支持读写并发，写入串行化队列避免锁冲突 |
| D2 | 密码明文存储 | 用户三次确认，博彩账号密码安全性非本平台关注点（详见需求 1.3） |
| D3 | 告警使用 HTTP 轮询 | 一期简单可靠，二期可升级 WebSocket/SSE |
| D4 | 单进程 asyncio + 线程池 | 1000 账号并发用协程足够，阻塞调用（验证码 OCR）走线程池隔离 |
| D5 | 下注失败不重试 | 主要失败原因是封盘，重试无意义（需求 5.2 确认） |
| D6 | 相同 KeyCode 不合并金额 | 不同策略的下注需求独立，合并会破坏策略追踪（需求 4.4 确认） |
| D7 | 合并请求失败不拆单 | 失败主因是封盘，拆单也无法成功（需求 5.4 确认） |
| D8 | 内存存储活跃 session | 一期单实例，二期通过 SessionStore 接口迁移 Redis |
| D9 | 验证码识别外部服务 | 验证码 OCR 独立部署，不耦合主服务，降级时暂停自动登录 |
| D10 | 止损止盈按策略维度 | 操作者需要精细控制每个策略的风险（需求 6.2 确认） |
| D11 | 金额使用整数分存储 | 避免浮点精度问题，赔率×1000 存储，所有运算为整数运算 |
| D12 | 每期自动对账 | 结算后立即拉取平台余额交叉校验，容差 ±1 元，异常不停策略但告警 |
