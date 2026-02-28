# Kiro Steering 配置草案 v4 — Git/GitHub 工作流融入 + 全量整合

> 版本：v4 | 日期：2026-02-28
> 基于：v3.2.8 | 状态：待审核（未写入任何 steering 文件）

---

## 修订日志

| 版本 | 变更摘要 |
|------|----------|
| v1 | 初始草案 |
| v2 | gpt 定位改为模型角色策略；Chrome 流程改为 evaluate_script |
| v3 | Probe hook 方案；工具名对齐；MCP 路径确认；fileMatch 保守方案 |
| v3.1 | Probe reload 覆盖首屏；数据上限+去重；testing 拆分；schemas steering |
| v3.2 | 新增前后端协同防返工体系：contract-first.md、dev-loop.md、api-mock-and-stub.md；联调验证步骤 |
| v3.2.1 | Proxy-Only 策略落地；前端端口改回 5173 |
| v3.2.2 | 质量门禁 + 统一请求层 + Pydantic v2；directBackendHitDetected 检测 |
| v3.2.3 | 请求层鲁棒性 + Mock Helper + 门禁微调 |
| v3.2.4 | isEnvelope 修正 + HTTP raw 保留 + text-first 解析 + API 绝对 URL 检测 |
| v3.2.5 | 4 个小补丁：path `/` 开头、异常信封化升级、后端交叉引用、Probe 注入脚本 |
| v3.2.6 | Scope Matrix + Verification Pack + autoApprove 来源验证 + #24 includeMcpJson |
| v3.2.7 | 来源可信度修正 + evaluate_script 审计 + CORS 兜底 |
| v3.2.8 | 契约漂移 CI + 异常信封化升级 + 一键联调自检 verify-dev.ps1 |
| **v4** | **GitHub/Git 工作流融入**：①新增 `git-workflow.md` steering（always-on）②中文 commit 规范（硬规则）③Git 原生 pre-commit hook 接入 verify-dev.ps1 静态检查子集 ④新增 `scripts/setup-repo.ps1` + `scripts/pre-commit-hook.ps1` ⑤新增 `docs/commit-convention.md` + `docs/dev-workflow.md` ⑥Scope Matrix 增加 Git 文件归类 ⑦Verification Pack 增加 B.7 Git 工作流验证 ⑧新增决策点 #25-#27 |
| **v4.1** | **补丁**：①AGENTS.md 加固（语言偏好+代码风格+安全底线+MCP 通则）②includeMcpJson 决策点恢复到 mcp-workflow.md + Verification Pack B.2 ③evaluate_script 审计闭环写回 mcp-workflow.md（Chrome smoke test + Probe 断言清单 + apiAbsoluteUrlHitDetected + 审计规则）④Verification Pack B.1 +安全底线验证 B.3 +entries 非空+绝对 URL 检测 |

---

## 本次新增/变更总览（v4）

| 类型 | 文件/章节 | inclusion | 说明 |
|------|-----------|-----------|------|
| 新增 | `.kiro/steering/git-workflow.md` | always | Git 分支策略 + 中文 commit 规范 + pre-commit hook 接入 |
| 新增 | `docs/commit-convention.md` | — | 中文 commit 规范详细文档 |
| 新增 | `docs/dev-workflow.md` | — | 开发工作流 runbook（GitHub 初始化 → 日常开发 → PR 合并） |
| 新增 | `scripts/setup-repo.ps1` | — | 一键初始化仓库 + 配置 git hook |
| 新增 | `scripts/pre-commit-hook.ps1` | — | pre-commit hook（调用 verify-dev.ps1 静态检查子集） |
| 更新 | 附录 A Scope Matrix | — | 增加 Git 相关文件的用户级/项目级归类 |
| 新增 | 附录 B.7 | — | Git 工作流验证（hook 阻断 + 正常放行 + commit 格式） |
| 新增 | 决策点 #25-#27 | — | 中文 commit 强制性 / hook 方案 / main 保护策略 |
| 补充 | Gate0 执行计划 | — | 增加 Git 初始化 + hook 配置步骤 |
| 无变更 | 原 18 个 steering 文件 | — | 与 v3.2.8 一致 |

> 文件总数：always-on **9 个**（+1）+ conditional 10 个 = **19 个** steering 文件。

---

## 一、文件内容骨架

### 1.1 `.kiro/steering/contract-first.md`（always-on，与 v3.2.8 一致，无变更）

```markdown
# API 契约先行规范

## 核心原则
契约先于实现。任何新接口必须先定义契约，再写业务代码。

## Pydantic 版本（v3.2.2 确定）
- 本项目统一使用 **Pydantic v2**（>= 2.0）
- 所有 schema 必须使用 v2 写法：`model_config = ConfigDict(...)` 替代 `class Config`
- 禁止使用 Pydantic v1 兼容层（`from pydantic.v1 import ...`）

## 契约定义流程

### 步骤 1：后端定义 Schema
- 在 `backend/app/schemas/` 中创建 Pydantic v2 schema
- 必须包含：request model + response model
- 必须提供示例 payload：至少 1 个 success + 1 个 error
  ```python
  # 示例：backend/app/schemas/user.py
  from pydantic import BaseModel, Field, ConfigDict

  class UserCreate(BaseModel):
      username: str = Field(..., description="用户名", examples=["zhangsan"])
      email: str = Field(..., description="邮箱", examples=["user@example.com"])

  class UserResponse(BaseModel):
      model_config = ConfigDict(
          json_schema_extra={
              "example": {"id": 1, "username": "zhangsan", "email": "user@example.com"}
          }
      )

      id: int
      username: str
      email: str
  ```

### 步骤 2：前端同步类型
- 在 `frontend/src/types/api/` 中创建对应 TypeScript interface
- 字段名、类型、可选性必须与 Pydantic schema 一一对应
  ```typescript
  // 示例：frontend/src/types/api/user.ts
  export interface UserCreate {
    username: string;
    email: string;
  }
  export interface UserResponse {
    id: number;
    username: string;
    email: string;
  }
  ```
- 可选升级路径：FastAPI 生成 OpenAPI spec → openapi-typescript 自动生成

### 步骤 3：实现端点与调用
- 后端在 `backend/app/api/` 实现端点（router 内只定义相对路径，prefix 在 main.py 统一挂载）
- 前端在 `frontend/src/api/` 实现调用封装（必须通过统一请求层，见 api-client.md）

## 成对提交规则

**硬规则**：契约变更必须成对提交。

- 改 schema / 改路由时，必须同时提交：
  - 后端：schema + endpoint 变更
  - 前端：types + api client 调用处变更
- **禁止**"只改后端不改前端类型"
- 例外：明确标记为 breaking change 并升级 API 版本号时，可先提交后端，但必须在同一迭代内完成前端适配

## 统一响应信封

所有接口必须使用统一信封：
```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

### 错误码分段（固定规则）

| 段 | 范围 | 含义 | 示例 |
|----|------|------|------|
| 0 | 0 | 成功 | 请求成功 |
| 1xxx | 1000-1999 | 参数错误 | 1001 缺少必填字段、1002 格式不合法 |
| 2xxx | 2000-2999 | 认证错误 | 2001 token 过期、2002 token 无效 |
| 3xxx | 3000-3999 | 权限错误 | 3001 无访问权限、3002 操作被禁止 |
| 4xxx | 4000-4999 | 业务错误 | 4001 资源不存在、4002 重复操作 |
| 5xxx | 5000-5999 | 服务端错误 | 5001 内部异常、5002 依赖服务不可用 |

### 前端错误处理
- API 封装层必须把非 code=0 统一映射为错误对象（详见 api-client.md 的 ApiError 定义）
- 所有 API 调用处必须处理 ApiError（至少 toast 提示 message）

## CORS 策略（v3.2.1 修订）
- **开发环境**：默认无需 CORS 配置（proxy-only 模式，前端请求同源）
- **生产环境**：按部署域名/网关配置 CORS（在 `app/main.py` 中集中管理）
- 如果开发环境出现 CORS 错误，说明有人绕过了 proxy 直连后端，属于违规操作，应修复调用方式而非添加 CORS 配置

## Base URL 约定（v3.2.1 修订）
- 开发环境：`/api/v1`（相对路径，通过 Vite proxy 转发到后端）
- 生产环境：由部署网关/域名决定（如 `https://api.example.com/api/v1`）
- 前端代码中统一使用 `import.meta.env.VITE_API_BASE_URL`，禁止硬编码

## 契约变更流程

### 非破坏性变更（新增可选字段）
1. 后端添加字段（带默认值）
2. 前端更新类型（字段标记为可选）
3. 成对提交

### 破坏性变更（删除/重命名/类型变更）
1. 升级 API 版本前缀（如 `/api/v1` → `/api/v2`）或提供兼容字段（标记 Deprecated）
2. 前端先支持新版本
3. 确认旧版本无调用后移除
4. 在 CHANGELOG.md 记录 breaking 项
```


### 1.2 `.kiro/steering/dev-loop.md`（always-on，与 v3.2.8 一致，无变更）

```markdown
# 本地联调开发规范

## ⚠️ 硬规则：Proxy-Only 是唯一开发模式（v3.2.2）

开发环境**仅允许** proxy-only 模式。这不是"推荐"，而是**唯一合法的开发网络模式**。
所有前端 API 请求必须通过 Vite dev server 代理到后端，不存在"直连模式"选项。

| 规则 | 说明 |
|------|------|
| `VITE_API_BASE_URL` 必须为 `/api/v1` | 相对路径，不得以 `/` 结尾 |
| **禁止**将 `VITE_API_BASE_URL` 配为绝对地址 | 如 `http://localhost:8888/api/v1` 属于违规 |
| **禁止**前端直连后端端口 | 浏览器中不得出现 `localhost:8888` 的请求 |
| **禁止**混用 proxy 与直连 | 同一环境只能走一种模式 |
| 后端开发环境默认不配 CORS | 因为同源代理，无需 CORS；若有人绕过 proxy 直连属于违规 |
| **不存在**"直连+CORS"开发模式 | 该选项已在 v3.2.1 中永久移除 |

## 统一本地联调入口

| 服务 | 地址 | 说明 |
|------|------|------|
| 前端 dev server | `http://localhost:5173` | Vite dev server（默认端口），**唯一浏览器入口** |
| 后端 dev server | `http://localhost:8888` | FastAPI uvicorn（仅 proxy 目标，不直接访问） |

## 前端配置

### .env.development
```
VITE_API_BASE_URL=/api/v1
```

### vite.config.ts
```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': resolve(__dirname, 'src') },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8888',
        changeOrigin: true,
      },
    },
  },
})
```

### 前端 API 调用示例
```typescript
// frontend/src/api/health.ts
import { request } from '@/api/request';
export function fetchHealth() {
  return request<{ status: string; service: string }>('/health');
}
```

## 后端配置

### 启动命令
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload
```

### Router Prefix 规范（v3.2.2）
**硬规则**：router 内只定义相对路径，prefix 在 `main.py` 的 `include_router` 统一管理。

```python
# backend/app/api/health.py
from fastapi import APIRouter
router = APIRouter()

@router.get("/health")
async def health():
    return {"code": 0, "message": "success", "data": {"status": "ok", "service": "bocai-backend"}}
```

```python
# backend/app/main.py
from fastapi import FastAPI
from app.api.health import router as health_router

app = FastAPI(title="Bocai Backend")
app.include_router(health_router, prefix="/api/v1", tags=["health"])
```

## 最小可运行联调样例（Contract Smoke）
```bash
# 通过前端代理验证
curl http://localhost:5173/api/v1/health
# 仅诊断：直访后端
curl http://localhost:8888/api/v1/health
```

## 环境变量与启动 Checklist

| 步骤 | 命令 | 说明 |
|------|------|------|
| 1. 启动后端 | `cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload` | 先启动后端 |
| 2. 启动前端 | `cd frontend && pnpm dev` | Vite 默认端口 5173 |
| 3. 验证 proxy | `curl http://localhost:5173/api/v1/health` | 必须返回 `{"code":0,...}` |
| 4. 浏览器验证 | 访问 `http://localhost:5173/` | 页面应显示 health 状态 |
| 5. Chrome MCP | navigate_page + Probe + 截图 | 输出联调报告 |

## 调试证据标准

| 证据项 | 来源 | 必须/可选 |
|--------|------|-----------|
| 请求 URL / method / request body | 浏览器 Network 或 Probe | 必须 |
| 响应 code / message / data | 浏览器 Network 或 Probe | 必须 |
| 前端 console error | Chrome MCP Probe 采集 | 必须 |
| 后端日志片段 | 终端日志 | 必须 |
| traceId（如有） | 响应 header 或 body | 可选（推荐） |
| 页面截图 | Chrome MCP take_screenshot | 可选（推荐） |
| 直连后端检测 | Probe directBackendHitDetected | 必须 |
```


### 1.3 `.kiro/steering/api-mock-and-stub.md`（conditional，与 v3.2.8 一致，无变更）

```markdown
---
inclusion: fileMatch
fileMatchPattern: "frontend/src/api/**/*.{ts,tsx}"
---

# API Mock 与 Stub 策略

> **后端同学注意**：本文件 fileMatch 仅触发于前端 api 目录，但 Mock 策略同样约束后端。
> 编辑 `backend/app/api/**` 或 `backend/app/schemas/**` 时，请同样遵守本文件中的 Helper 集中读取规则。

## 推荐方案：模式 A — 后端提供 Mock（待确认）
- 通过环境变量开关：`MOCK_MODE=true`
- mock 数据来源：schema 中的 `json_schema_extra`（Pydantic v2）

### ⚠️ 硬规则：Schema Example 读取必须集中到 Helper
- helper 固定放在 `backend/app/utils/mock_helper.py`
- 签名示例：`def get_schema_example(model: type[BaseModel]) -> dict`
- 当 schema 未定义 example 时，helper 应抛出明确错误或返回约定的空结构
- 所有 mock endpoint 必须通过此 helper 获取 example 数据

## 备选方案：模式 B — 前端 MSW Stub
- 前端使用 MSW，通过 `VITE_ENABLE_MSW=true` 开关

## 通用规则

| 项目 | 要求 |
|------|------|
| mock 数据来源 | 必须来自 schema example / types 定义 |
| 开关方式 | 环境变量，禁止代码中硬编码开关 |
| CI 中 mock | 单元测试可用 mock，E2E 测试禁止 mock |
| example 读取 | **必须通过集中 helper，禁止各 endpoint 复制取值逻辑** |
```

### 1.4 `.kiro/steering/spec-review-gate.md`（always-on，与 v3.2.8 一致，无变更）

```markdown
# Specs 审核门禁

## Specs 路径
- `.kiro/specs/{feature-name}/`（kebab-case）
- 每个 spec 包含：`requirements.md`、`design.md`、`tasks.md`

## 审核门禁规则
**硬规则**：任何 specs 变更必须经过 gpt-5.2（codex-gpt52）审核，获得 PASS 后才能进入实施阶段。

### 必审项（FAIL 阻塞）
- requirements.md：需求完整性、可测试性、无歧义
- design.md：技术方案可行性、与现有架构一致性
- tasks.md：拆分合理性 + 依赖正确性 + 验收标准（DoD）

### 建议项（non-blocking）
- tasks.md 估时合理性

### 审核记录（推荐）
```markdown
## Review
| 日期 | 版本 | 结论 | 关键 P0 |
|------|------|------|---------|
| 2026-02-28 | v1 | PASS | 无 |
```
```

### 1.5 `.kiro/steering/api-client.md`（conditional，与 v3.2.8 一致，无变更）

```markdown
---
inclusion: fileMatch
fileMatchPattern: "frontend/src/api/**/*.{ts,tsx}"
---

# 统一请求层规范

**所有 API 调用必须经过统一请求层（`frontend/src/api/request.ts`），禁止在组件或页面中直接使用 `fetch` / `axios` / `ky` 等。**

### request.ts 核心逻辑（v3.2.5）

```typescript
const BASE_URL = import.meta.env.VITE_API_BASE_URL; // /api/v1

export interface ApiResponse<T> {
  code: number;
  message: string;
  data: T | null;
}

export interface ApiError {
  code: number;
  message: string;
  status?: number;
  raw?: unknown;
  traceId?: string;
}

export async function request<T>(path: string, options?: RequestInit): Promise<ApiResponse<T>> {
  if (!path.startsWith('/')) {
    throw { code: -1, message: `request path 必须以 / 开头，当前值: "${path}"` } as ApiError;
  }
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  const text = await res.text();
  let body: unknown;
  try { body = JSON.parse(text); } catch { body = undefined; }

  if (!res.ok) {
    const envelope = isEnvelope(body) ? body : null;
    throw { code: envelope?.code ?? -1, message: envelope?.message ?? `HTTP ${res.status}: ${res.statusText}`, status: res.status, raw: body ?? text } as ApiError;
  }
  if (body === undefined) throw { code: -1, message: '响应体 JSON 解析失败', status: res.status, raw: text } as ApiError;
  if (!isEnvelope(body)) throw { code: -1, message: '响应不符合 { code, message, data } 信封结构', status: res.status, raw: body } as ApiError;
  if (body.code !== 0) throw { code: body.code, message: body.message, raw: body, traceId: (body as any).traceId } as ApiError;
  return body as ApiResponse<T>;
}

function isEnvelope(data: unknown): data is { code: number; message: string; data: unknown } {
  return typeof data === 'object' && data !== null && 'code' in data && 'message' in data && 'data' in data
    && typeof (data as any).code === 'number' && typeof (data as any).message === 'string';
}
```

**关键设计**：
- isEnvelope 必须检查 `data` 字段（v3.2.4）
- ApiResponse.data 类型为 `T | null`（匹配后端 `data: T | None = None`）
- HTTP 错误分支始终保留 `raw`
- text-first JSON 解析（先 `res.text()` 再 `JSON.parse`）
- path 必须以 `/` 开头（v3.2.5）
- HTTP 错误时 `code`（业务码）与 `status`（HTTP 码）独立
```


### 1.6 `.kiro/steering/git-workflow.md`（always-on，v4 新增）

```markdown
# Git 工作流规范（v4）

## 分支策略

### 主分支
- `main`：唯一的稳定分支，仅通过 PR 合并
- **禁止**直接 push 到 main（需在 GitHub 仓库设置中配置 Branch Protection）

### 开发分支命名

| 前缀 | 用途 | 示例 |
|------|------|------|
| `feat/` | 新功能 | `feat/user-auth` |
| `fix/` | 修复 | `fix/health-endpoint-500` |
| `chore/` | 工程配置/依赖 | `chore/kiro-steering-git-workflow` |
| `docs/` | 文档 | `docs/commit-convention` |
| `refactor/` | 重构 | `refactor/request-layer` |
| `test/` | 测试 | `test/api-client-pbt` |

**规则**：
- 前缀使用英文（与 GitHub 生态一致）
- 分支名使用 kebab-case
- 从 `main` 创建分支，完成后通过 PR 合并回 `main`

## 中文 Commit 规范

**项目级硬规则**：所有 commit message 必须使用中文。

### 类型枚举（固定）

| 类型 | 含义 | 示例场景 |
|------|------|----------|
| 初始化 | 项目/模块初始化 | 项目脚手架、首次配置 |
| 配置 | 工程配置变更 | steering 文件、vite.config、pyproject.toml |
| 功能 | 新功能实现 | 新增 API 端点、新增页面 |
| 修复 | Bug 修复 | 修复 422 信封解析、修复 proxy 配置 |
| 重构 | 代码重构（不改变行为） | 提取公共函数、优化结构 |
| 测试 | 测试相关 | 新增单元测试、PBT 属性测试 |
| 文档 | 文档变更 | README、commit-convention、dev-workflow |
| 杂项 | 其他（依赖升级、CI 等） | 升级 pydantic、清理无用文件 |

### Commit Message 模板
```
<类型>：<简要描述>

<可选：详细说明>

<可选：关联 issue/spec>
```

### 示例
```
配置：新增 Git 工作流 steering 与 pre-commit hook

- 新增 git-workflow.md（always-on steering）
- 新增 scripts/setup-repo.ps1 一键初始化
- 新增 scripts/pre-commit-hook.ps1 接入 verify-dev.ps1 静态检查

关联：Kiro-Steering v4
```

```
功能：实现用户列表 API 端点

- 后端：backend/app/api/user.py + schemas/user.py
- 前端：frontend/src/api/user.ts + types/api/user.ts
- 成对提交（契约先行规范）
```

```
修复：修复 request.ts 对 422 响应的信封解析

isEnvelope 未检查 data 字段导致静默失败，
现已增加 'data' in obj 检查。
```

## 提交前验证（Pre-commit Hook）

### 机制
- 使用 Git 原生 hook（`.git/hooks/pre-commit`），不引入第三方工具
- hook 调用 `scripts/pre-commit-hook.ps1`
- 执行 verify-dev.ps1 的**静态检查子集**（不含 Proxy 链路验证，因提交时不一定启动了 dev server）

### 检查项

| # | 检查 | 来源 | 失败行为 |
|---|------|------|----------|
| 1 | Proxy-only 静态扫描 | verify-dev.ps1 B.6.1 | 阻断 commit |
| 2 | 契约漂移成对提交 | verify-dev.ps1 B.6.2 | 阻断 commit |

### 安装方式
- 运行 `pwsh scripts/setup-repo.ps1` 自动配置
- 或手动：将 `.git/hooks/pre-commit` 指向 `scripts/pre-commit-hook.ps1`

### 跳过 Hook（紧急情况）
- `git commit --no-verify` 可跳过 hook
- **仅限紧急修复**，事后必须补充验证

## PR 合并规则

### GitHub 仓库设置（手动操作）

在 GitHub 仓库 Settings → Branches → Branch protection rules 中配置：

1. Branch name pattern: `main`
2. ✅ Require a pull request before merging
3. ✅ Require approvals: 1（若为个人项目可设为 0，但仍必须走 PR）
4. ✅ Require status checks to pass before merging（可选：接入 CI 后启用）
5. ✅ Do not allow bypassing the above settings

### PR 流程

1. 从 `main` 创建功能分支
2. 开发 + 本地验证（pre-commit hook 自动执行）
3. push 到 origin
4. 创建 PR → 填写描述（中文）
5. 审查通过 → Squash and merge（保持 main 历史整洁）
6. 删除已合并的功能分支

### PR 描述模板（推荐）
```markdown
## 变更内容
<!-- 简要描述本次 PR 做了什么 -->

## 变更类型
- [ ] 功能
- [ ] 修复
- [ ] 配置
- [ ] 文档
- [ ] 重构
- [ ] 测试

## 验证方式
<!-- 如何验证这个变更是正确的 -->

## 关联
<!-- 关联的 spec / issue -->
```
```


### 1.7 其他 Steering 文件（与 v3.2.8 一致，无变更）

以下文件内容骨架与 v3.2.8 完全一致，此处仅列出不再重复全文：

- `frontend-components.md` / `frontend-pages.md`：含禁止直接 fetch 规则（v3.2.3）
- `api-schemas-backend.md`：Pydantic v2 Schema 规范
- `api-contracts.md` / `api-contracts-backend.md`：前后端契约
- `testing.md` / `testing-frontend.md` / `testing-backend.md`：测试规范
- `mcp-workflow.md`：MCP 闭环 + Probe 注入 + 直连检测 + API 绝对 URL 检测 + Specs Gate（含 v3.2.4-v3.2.5 全部修订）
- `tech.md`：技术栈 + 端口 + Pydantic v2
- `product.md`：产品定义（待填充）
- `structure.md`：目录结构 + 路径别名

---

## 二、v4 新增文档与脚本

### 2.1 `docs/commit-convention.md`（v4 新增）

```markdown
# 中文 Commit 规范

> 本文档是 `.kiro/steering/git-workflow.md` 的详细补充。

## 为什么用中文 Commit
- 团队主要使用中文沟通，中文 commit 降低认知切换成本
- Git log 直接可读，无需翻译
- 分支名保持英文前缀（与 GitHub 生态兼容）

## 类型枚举

| 类型 | 含义 | 英文对照（仅参考） |
|------|------|---------------------|
| 初始化 | 项目/模块初始化 | init |
| 配置 | 工程配置变更 | config / chore |
| 功能 | 新功能实现 | feat |
| 修复 | Bug 修复 | fix |
| 重构 | 代码重构 | refactor |
| 测试 | 测试相关 | test |
| 文档 | 文档变更 | docs |
| 杂项 | 其他 | chore |

## 格式
```
<类型>：<简要描述>

<可选正文>

<可选脚注>
```

**注意**：类型与描述之间使用中文全角冒号 `：`

## 示例

### 最小 commit
```
配置：更新 vite proxy 端口为 8888
```

### 带正文
```
修复：修复 isEnvelope 未检查 data 字段

后端返回 {code:0, message:"success"} 无 data 时，
旧版 isEnvelope 通过检查但 r.data 为 undefined，
导致前端静默失败。现增加 'data' in obj 检查。
```

### 带关联
```
功能：实现 health 端到端联调路径

- 后端 /api/v1/health 端点
- 前端 fetchHealth() 通过统一请求层调用
- Proxy 链路验证通过

关联：Kiro-Steering v4 Gate0
```

## 不合规示例
```
❌ feat: add user api          （英文 + 英文类型）
❌ 新增用户接口                  （缺少类型前缀）
❌ 功能:新增用户接口             （半角冒号 + 无空格）
❌ 功能： 新增用户接口           （冒号后多余空格）
```
```

### 2.2 `docs/dev-workflow.md`（v4 新增）

```markdown
# 开发工作流 Runbook

## 一、仓库初始化（一次性）

### 1.1 创建 GitHub 仓库
1. 在 GitHub 上创建空仓库（不勾选 README / .gitignore / LICENSE）
2. 记录仓库地址：`https://github.com/<owner>/<repo>.git`

### 1.2 本地初始化
```powershell
git init
git remote add origin https://github.com/<owner>/<repo>.git
pwsh scripts/setup-repo.ps1
```

### 1.3 配置 main 分支保护（GitHub UI 手动操作）

| 设置项 | 值 |
|--------|-----|
| Branch name pattern | `main` |
| Require a pull request before merging | ✅ |
| Required approvals | 1（个人项目可设 0，但仍走 PR） |
| Require status checks to pass | ✅（接入 CI 后启用） |
| Do not allow bypassing | ✅ |

### 1.4 用户级 Git 配置（手动执行，不入 git）
```powershell
git config --global user.name "[你的名字]"
git config --global user.email "[你的邮箱]"
# 可选：SSH key 参考 https://docs.github.com/en/authentication/connecting-to-github-with-ssh
```

## 二、日常开发流程

### 2.1 创建功能分支
```powershell
git checkout main
git pull origin main
git checkout -b feat/my-feature
```

### 2.2 开发 + 提交
```powershell
git add .
git commit -m "功能：实现 xxx 功能"
# pre-commit hook 自动执行静态检查
```

### 2.3 Push + 创建 PR
```powershell
git push origin feat/my-feature
```
在 GitHub 上创建 PR → 审查通过 → Squash and merge → 删除分支

## 三、紧急修复流程
```powershell
git checkout -b fix/urgent-issue
git commit -m "修复：紧急修复 xxx"
# 极端情况：git commit --no-verify -m "修复：紧急修复（跳过 hook，事后补验证）"
git push origin fix/urgent-issue
```

## 四、验证命令速查

| 场景 | 命令 |
|------|------|
| 完整联调自检 | `pwsh scripts/verify-dev.ps1` |
| 仅静态检查 | `pwsh scripts/pre-commit-hook.ps1` |
| 检查 hook 是否安装 | `Get-Content .git/hooks/pre-commit` |
| 重新安装 hook | `pwsh scripts/setup-repo.ps1` |
```


### 2.3 `scripts/setup-repo.ps1`（v4 新增）

```powershell
#!/usr/bin/env pwsh
# scripts/setup-repo.ps1 — 一键初始化仓库 + 配置 Git hook（v4）
# 用法：在项目根目录执行 pwsh scripts/setup-repo.ps1

$ErrorActionPreference = "Stop"
Write-Host "=== 仓库初始化开始 ===" -ForegroundColor Cyan

# --- 1. 检查 git 仓库 ---
if (-not (Test-Path ".git")) {
  Write-Host "初始化 git 仓库..." -ForegroundColor Yellow
  git init
}

# --- 2. 配置 pre-commit hook ---
Write-Host "`n配置 pre-commit hook..." -ForegroundColor Yellow
$hookDir = ".git/hooks"
$hookFile = "$hookDir/pre-commit"

if (-not (Test-Path $hookDir)) {
  New-Item -ItemType Directory -Path $hookDir -Force | Out-Null
}

$hookContent = @'
#!/bin/sh
# Git pre-commit hook — 调用 PowerShell 静态检查
# 由 scripts/setup-repo.ps1 自动生成
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/pre-commit-hook.ps1
exit $?
'@

Set-Content -Path $hookFile -Value $hookContent -Encoding UTF8
Write-Host "✅ pre-commit hook 已配置：$hookFile" -ForegroundColor Green

# --- 3. 创建 .gitignore（如果不存在） ---
if (-not (Test-Path ".gitignore")) {
  Write-Host "`n创建 .gitignore..." -ForegroundColor Yellow
  $gitignore = @"
node_modules/
__pycache__/
*.pyc
.venv/
venv/
dist/
build/
*.egg-info/
.vscode/
.idea/
.env
.env.local
.env.*.local
!.env.development
.DS_Store
Thumbs.db
"@
  Set-Content -Path ".gitignore" -Value $gitignore -Encoding UTF8
  Write-Host "✅ .gitignore 已创建" -ForegroundColor Green
} else {
  Write-Host ".gitignore 已存在，跳过" -ForegroundColor Gray
}

# --- 4. 首次提交（如果没有任何 commit） ---
$hasCommits = git rev-parse HEAD 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "`n执行首次提交..." -ForegroundColor Yellow
  git add .
  git commit --no-verify -m "初始化：项目初始化 + Kiro Steering 配置 + Git 工作流"
  Write-Host "✅ 首次提交完成" -ForegroundColor Green
} else {
  Write-Host "`n已有 commit 历史，跳过首次提交" -ForegroundColor Gray
}

Write-Host "`n=== 仓库初始化完成 ===" -ForegroundColor Cyan
Write-Host @"

下一步：
1. 在 GitHub 创建空仓库
2. git remote add origin https://github.com/<owner>/<repo>.git
3. git push -u origin main
4. 在 GitHub Settings → Branches 配置 main 分支保护规则
   （详见 docs/dev-workflow.md）
"@ -ForegroundColor White
```

### 2.4 `scripts/pre-commit-hook.ps1`（v4 新增）

```powershell
#!/usr/bin/env pwsh
# scripts/pre-commit-hook.ps1 — Git pre-commit 静态检查（v4）
# 由 .git/hooks/pre-commit 调用
# 仅执行静态检查（不含 Proxy 链路验证）

$ErrorActionPreference = "Stop"
Write-Host "=== Pre-commit 检查 ===" -ForegroundColor Cyan

# --- 1. Proxy-only 静态扫描 ---
Write-Host "[1/2] Proxy-only 静态扫描..." -ForegroundColor Yellow
$hits = rg "https?://[^`"']+?/api/" --glob "!node_modules" --glob "!dist" --glob "!*.md" --glob "!scripts/*" --glob "!docs/*" -l 2>$null
if ($hits) {
  Write-Host "❌ 发现硬编码绝对 API URL（违反 Proxy-only 规则）：" -ForegroundColor Red
  $hits | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
  Write-Host "`n提交被阻断。请修复后重新提交。" -ForegroundColor Red
  exit 1
}
Write-Host "✅ 通过" -ForegroundColor Green

# --- 2. 契约漂移成对提交检查 ---
Write-Host "[2/2] 契约漂移成对提交检查..." -ForegroundColor Yellow
$staged = git diff --cached --name-only 2>$null

$backendSchemaChanged = $staged | Where-Object { $_ -match "^backend/app/schemas/" }
$frontendTypesChanged = $staged | Where-Object { $_ -match "^frontend/src/types/" }

if ($backendSchemaChanged -and -not $frontendTypesChanged) {
  Write-Host "❌ 契约漂移：backend/app/schemas/ 有变更但 frontend/src/types/ 无变更" -ForegroundColor Red
  $backendSchemaChanged | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
  Write-Host "`n提交被阻断。请同步更新前端类型后重新提交。" -ForegroundColor Red
  Write-Host "（紧急情况可用 git commit --no-verify 跳过，事后必须补充）" -ForegroundColor Yellow
  exit 1
}

if ($frontendTypesChanged -and -not $backendSchemaChanged) {
  Write-Host "⚠️ 注意：frontend/src/types/ 有变更但 backend/app/schemas/ 无变更（可能是前端独立类型，非违规但请确认）" -ForegroundColor Yellow
}

Write-Host "✅ 通过" -ForegroundColor Green
Write-Host "`n=== Pre-commit 检查全部通过 ===" -ForegroundColor Cyan
```


---

## 三、MCP 配置治理（与 v3.2.8 一致，无变更）

autoApprove 来源与验证、includeMcpJson 说明、MCP 字段白名单等内容与 v3.2.8 完全一致，此处不再重复。

---

## 四、更新后的完整文件清单

### always-on 文件（9 个，v4 +1）

| # | 文件 | 职责 | v4 状态 |
|---|------|------|---------|
| 1 | `~/.kiro/steering/AGENTS.md` | 个人通用偏好 | 无变更 |
| 2 | `.kiro/steering/product.md` | 产品定义 | 无变更 |
| 3 | `.kiro/steering/tech.md` | 技术栈（端口 + Pydantic v2） | 无变更 |
| 4 | `.kiro/steering/structure.md` | 目录结构与命名 | 无变更 |
| 5 | `.kiro/steering/mcp-workflow.md` | MCP 闭环 + Probe + 直连检测 + Specs Gate | 无变更 |
| 6 | `.kiro/steering/contract-first.md` | 契约先行规范 | 无变更 |
| 7 | `.kiro/steering/dev-loop.md` | Proxy-Only 联调规范 | 无变更 |
| 8 | `.kiro/steering/spec-review-gate.md` | Specs 审核门禁 | 无变更 |
| **9** | **`.kiro/steering/git-workflow.md`** | **Git 分支策略 + 中文 commit + pre-commit hook + PR 规则** | **v4 新增** |

### conditional 文件（10 个，无变更）

| # | 文件 | fileMatchPattern | v4 状态 |
|---|------|------------------|---------|
| 1 | `frontend-components.md` | `frontend/src/components/**/*.{ts,tsx}` | 无变更 |
| 2 | `frontend-pages.md` | `frontend/src/pages/**/*.{ts,tsx}` | 无变更 |
| 3 | `api-contracts.md` | `frontend/src/api/**/*.{ts,tsx}` | 无变更 |
| 4 | `api-contracts-backend.md` | `backend/app/api/**/*.py` | 无变更 |
| 5 | `api-schemas-backend.md` | `backend/app/schemas/**/*.py` | 无变更 |
| 6 | `testing.md` | `**/*.test.*` | 无变更 |
| 7 | `testing-frontend.md` | `frontend/tests/**` | 无变更 |
| 8 | `testing-backend.md` | `backend/tests/**/*.py` | 无变更 |
| 9 | `api-mock-and-stub.md` | `frontend/src/api/**/*.{ts,tsx}` | 无变更 |
| 10 | `api-client.md` | `frontend/src/api/**/*.{ts,tsx}` | 无变更 |

---

## 五、更新后的关键决策点

| # | 决策项 | 默认方案 | 备选 | 状态 |
|---|--------|----------|------|------|
| 14 | Mock 策略 | 模式 A：后端提供 Mock | 模式 B：前端 MSW Stub | 待确认 |
| 15 | 前端 dev 端口 | **5173（Vite 默认）** | 自定义端口 | **已确定** |
| 16 | 后端 dev 端口 | **8888** | ~~8000~~ | **已确定** |
| 17 | 契约变更是否需要版本号升级 | 破坏性变更必须升级 | 所有变更都升级 | 待确认 |
| 18 | traceId 是否强制 | 推荐但不强制 | 强制所有接口返回 | 待确认 |
| 19 | 开发环境网络模式 | **Proxy-only** | ~~直连+CORS~~ | **已确定** |
| 20 | Pydantic 版本 | **v2（>= 2.0）** | v1 兼容模式 | **已确定** |
| 21 | Specs 审核门禁 | **gpt-5.2 审核 PASS/FAIL** | 无门禁 | **已确定** |
| 22 | 前端请求方式 | **统一请求层（request.ts）** | 各处自由 fetch | **已确定** |
| 23 | Router prefix 管理 | **main.py include_router 统一 prefix** | router 内写完整路径 | **已确定** |
| 24 | includeMcpJson | **禁止写入配置**（未证实字段） | 仅保留为未来待评估项 | **已确定：禁止使用**（验证：B.2 #4-#6） |
| **25** | **中文 commit 强制性** | **硬规则（项目级）** | 推荐但不强制 | **已确定：硬规则** |
| **26** | **Pre-commit hook 方案** | **Git 原生 hook**（.git/hooks → pwsh 脚本） | pre-commit 框架 / husky | **已确定：原生 hook** |
| **27** | **main 分支保护** | **PR 合并 + 至少 1 approval**（GitHub UI 手动配置） | 无保护 | **已确定** |

**v4 新增决策理由**：
- #25 中文 commit 硬规则：团队中文沟通为主，降低认知切换成本。分支名保持英文前缀（GitHub 生态兼容）。
- #26 Git 原生 hook：不引入 pre-commit 框架或 husky 等第三方依赖，减少工具链复杂度。项目已有 PowerShell 脚本体系（verify-dev.ps1），直接复用。缺点是 `.git/hooks/` 不入 git，需要 `setup-repo.ps1` 初始化。
- #27 main 保护：GitHub Branch Protection 是标准做法，需手动在 GitHub UI 配置。


---

## 六、更新后的执行计划

### Gate0：项目初始化

- [ ] 前端初始化：`pnpm create vite frontend --template react-ts`
- [ ] 安装依赖：`cd frontend && pnpm install`
- [ ] 配置路径别名（tsconfig.json + vite.config.ts）
- [ ] 配置 vite.config.ts：proxy `/api` → `http://localhost:8888`
- [ ] 创建 `frontend/.env.development`：`VITE_API_BASE_URL=/api/v1`
- [ ] 创建 `frontend/src/api/request.ts`（统一请求层）
- [ ] 后端初始化：创建 `backend/pyproject.toml`（含 `pydantic>=2.0`）
- [ ] 创建 `backend/app/main.py`（include_router 统一 prefix="/api/v1"）
- [ ] 创建 `backend/app/api/health.py`（router 内只定义 `/health`）
- [ ] 创建 `backend/app/schemas/` 目录
- [ ] 创建 `backend/app/utils/mock_helper.py`
- [ ] 创建 `frontend/src/api/health.ts`
- [ ] 创建 `frontend/src/types/api/` 目录
- [ ] 创建 `frontend/tests/unit/` 和 `frontend/tests/e2e/` 目录
- [ ] 创建 `scripts/verify-dev.ps1`（一键联调自检脚本）
- [ ] **创建 `scripts/setup-repo.ps1`（一键初始化仓库 + hook 配置）**（v4 新增）
- [ ] **创建 `scripts/pre-commit-hook.ps1`（pre-commit 静态检查）**（v4 新增）
- [ ] **创建 `docs/commit-convention.md`（中文 commit 规范）**（v4 新增）
- [ ] **创建 `docs/dev-workflow.md`（开发工作流 runbook）**（v4 新增）
- [ ] **运行 `pwsh scripts/setup-repo.ps1`（初始化 git + 配置 hook）**（v4 新增）

### 6.1 创建 Steering 文件

- [ ] 全局：`~/.kiro/steering/AGENTS.md`
- [ ] 工作区：`.kiro/steering/product.md`
- [ ] 工作区：`.kiro/steering/tech.md`（含端口号 + Pydantic v2）
- [ ] 工作区：`.kiro/steering/structure.md`（含路径别名前置条件）
- [ ] 工作区：`.kiro/steering/contract-first.md`（Pydantic v2）
- [ ] 工作区：`.kiro/steering/dev-loop.md`（Proxy-Only + router prefix）
- [ ] 工作区：`.kiro/steering/spec-review-gate.md`（review.md 推荐 + 最小模板）
- [ ] 工作区：`.kiro/steering/api-client.md`（isEnvelope 修正 + raw 保留 + text-first）
- [ ] **工作区：`.kiro/steering/git-workflow.md`（分支策略 + 中文 commit + hook + PR 规则）**（v4 新增）
- [ ] 工作区：`.kiro/steering/frontend-components.md`（含禁止直接 fetch）
- [ ] 工作区：`.kiro/steering/frontend-pages.md`（含禁止直接 fetch）
- [ ] 工作区：`.kiro/steering/api-contracts.md`
- [ ] 工作区：`.kiro/steering/api-contracts-backend.md`
- [ ] 工作区：`.kiro/steering/api-schemas-backend.md`（Pydantic v2）
- [ ] 工作区：`.kiro/steering/api-mock-and-stub.md`（Helper 路径锁定）
- [ ] 工作区：`.kiro/steering/testing.md`
- [ ] 工作区：`.kiro/steering/testing-frontend.md`
- [ ] 工作区：`.kiro/steering/testing-backend.md`
- [ ] 工作区：`.kiro/steering/mcp-workflow.md`（含联调验证 + Probe + Specs Gate）

### 6.2 验证步骤

#### Steering 加载验证（与 v3.2.8 一致）
- [ ] 确认 always-on 文件（9 个）在聊天中可被引用
- [ ] 编辑 `frontend/src/components/` 下文件 → 确认 `frontend-components.md` 加载
- [ ] 编辑 `backend/app/schemas/` 下文件 → 确认 `api-schemas-backend.md` 加载
- [ ] 编辑 `frontend/src/api/` 下文件 → 确认 `api-contracts.md` + `api-client.md` 加载
- [ ] 编辑 `frontend/src/pages/` 下文件 → 确认 `frontend-pages.md` 加载

#### 联调验证（与 v3.2.8 一致）
- [ ] 启动后端 + 前端
- [ ] Proxy 链路验证：`curl http://localhost:5173/api/v1/health`
- [ ] Chrome MCP 联调验证（Probe 注入 → 检测 → 截图）

#### Git 工作流验证（v4 新增）
- [ ] 运行 `pwsh scripts/setup-repo.ps1` → 确认 `.git/hooks/pre-commit` 存在
- [ ] 故意引入硬编码绝对 API URL → `git commit` → 确认被 hook 阻断
- [ ] 仅修改 `backend/app/schemas/` 不改 `frontend/src/types/` → `git commit` → 确认被 hook 阻断
- [ ] 正常修改 → `git commit -m "测试：验证 hook 放行"` → 确认 commit 成功
- [ ] `git log --oneline -3` → 确认 commit 格式符合 `<类型>：<描述>`

#### Specs 门禁验证（与 v3.2.8 一致）
- [ ] 创建测试 spec → 触发审核 → gpt-5.2 PASS/FAIL → 清理


---

## 七、后续增强 Gate（可选，不阻塞当前执行）

| # | 增强项 | 说明 | 优先级 |
|---|--------|------|--------|
| 1 | traceId 提取策略 | request.ts 优先从 response header 读取，其次从 body | 中 |
| 2 | FastAPI 全局异常信封化 | 422/500 封装为统一信封。dev 环境最低要求：ApiError 保留 raw + status + traceId(optional) | **推荐 Gate0/1** |
| 3 | 契约漂移自动检查（CI 脚本） | `git diff --name-only` 断言成对提交（详见 B.6.2） | **中（含 CI 脚本）** |
| 4 | mock helper fallback | schema 没 example 时返回 `{"__missing_example__": true}` | 低 |
| 5 | proxy-only 静态扫描 | CI/lint 禁止硬编码绝对 API URL（详见 B.6.1） | 中（推荐 Gate0） |
| 6 | 开发期 CORS 兜底开关 | `ENABLE_DEV_CORS=false` 默认关闭 | 低（可选） |
| 7 | 一键联调自检脚本 | `scripts/verify-dev.ps1`（详见 B.6） | **推荐 Gate0** |
| **8** | **commit-msg hook** | **可选扩展：在 commit-msg hook 中校验 commit message 格式是否符合中文类型枚举** | **低（可选）** |

---

## 八、v3.2.8 → v4 变更汇总

| # | 类型 | 变更内容 | 影响文件/章节 |
|---|------|----------|---------------|
| 1 | 新增 steering | `git-workflow.md`（always-on）：Git 分支策略 + 中文 commit 规范 + pre-commit hook + PR 合并规则 | 新文件 |
| 2 | 新增文档 | `docs/commit-convention.md`：中文 commit 详细规范（类型枚举 + 模板 + 示例 + 不合规示例） | 新文件 |
| 3 | 新增文档 | `docs/dev-workflow.md`：开发工作流 runbook（初始化 → 日常开发 → PR → 紧急修复） | 新文件 |
| 4 | 新增脚本 | `scripts/setup-repo.ps1`：一键初始化仓库 + 配置 git hook + 创建 .gitignore + 首次提交 | 新文件 |
| 5 | 新增脚本 | `scripts/pre-commit-hook.ps1`：pre-commit 静态检查（Proxy-only 扫描 + 契约漂移检查，使用 staged files） | 新文件 |
| 6 | 更新 | Scope Matrix 增加 Git 工作流文件归类（项目级/用户级/本地生成） | 附录 A |
| 7 | 新增 | Verification Pack B.7 Git 工作流验证（hook 安装 + 阻断 + 放行 + commit 格式） | 附录 B |
| 8 | 新增 | 决策点 #25 中文 commit 硬规则 + #26 Git 原生 hook + #27 main 分支保护 | 决策点表 |
| 9 | 补充 | Gate0 执行计划增加 Git 初始化 + hook 配置 + 文档创建步骤 | 执行计划 |
| 10 | 补充 | 增强 Gate 新增 #8 commit-msg hook（可选扩展） | 增强 Gate 表 |

### 历史变更汇总（v3.2 → v3.2.8）

v3.2 至 v3.2.8 的完整变更历史已在 v3.2.8 草案中详细记录，此处不再重复。关键里程碑：

- v3.2：contract-first + dev-loop + api-mock-and-stub
- v3.2.2：质量门禁 + 统一请求层 + Pydantic v2
- v3.2.4：isEnvelope 修正 + text-first + API 绝对 URL 检测
- v3.2.6：Scope Matrix + Verification Pack
- v3.2.7：来源可信度修正 + evaluate_script 审计
- v3.2.8：契约漂移 CI + verify-dev.ps1

---

## 附录 A：Scope Matrix — 用户级 vs 项目级配置边界（v4 更新）

Kiro 配置合并优先级：**workspace > global**（项目级覆盖用户级）。

### 依据来源

| 配置类型 | 依据 | 来源级别 |
|----------|------|----------|
| Global steering（`~/.kiro/steering/`） | Kiro 官方描述：全局 steering 目录下的文件会被自动拾取 | 官方 |
| Conditional inclusion 语法 | Book of Kiro 社区文档 | 社区（已验证） |
| MCP 配置合并（user < workspace） | Kiro 系统提示词 | 官方 |
| MCP 字段（`disabled` / `autoApprove`） | Book of Kiro + 实践验证 | 社区 + 实践 |
| Steering `inclusion: manual` | Kiro 官方描述 | 官方 |

### Steering 文件

| 配置项 | 级别 | 路径 | 说明 |
|--------|------|------|------|
| AGENTS.md | **用户级** | `~/.kiro/steering/AGENTS.md` | 个人偏好，不入 git |
| product.md ~ testing-backend.md + **git-workflow.md**（18 个） | **项目级** | `.kiro/steering/*.md` | 项目规范，入 git |

### MCP 配置

| 配置项 | 级别 | 路径 | 说明 |
|--------|------|------|------|
| chrome-devtools | **项目级** | `.kiro/settings/mcp.json` | 联调必需，入 git |
| codex-gpt52 / codex-gpt53 | **用户级** | `~/.kiro/settings/mcp.json` | 个人模型策略，不入 git |

### Git 策略

| 文件 | 入 git | 级别 | 说明 |
|------|--------|------|------|
| `.kiro/steering/*.md`（18 个） | ✅ 是 | 项目级 | 项目规范，团队共享 |
| `.kiro/settings/mcp.json` | ✅ 是 | 项目级 | 项目 MCP 配置 |
| `~/.kiro/steering/AGENTS.md` | ❌ 否 | 用户级 | 个人偏好 |
| `~/.kiro/settings/mcp.json` | ❌ 否 | 用户级 | 个人 MCP 配置 |

### Git 工作流文件（v4 新增）

| 文件 | 入 git | 级别 | 说明 |
|------|--------|------|------|
| `.kiro/steering/git-workflow.md` | ✅ 是 | 项目级 | Git 分支策略 + 中文 commit + hook + PR |
| `docs/commit-convention.md` | ✅ 是 | 项目级 | 中文 commit 详细规范 |
| `docs/dev-workflow.md` | ✅ 是 | 项目级 | 开发工作流 runbook |
| `scripts/setup-repo.ps1` | ✅ 是 | 项目级 | 一键初始化仓库 + hook 配置 |
| `scripts/pre-commit-hook.ps1` | ✅ 是 | 项目级 | pre-commit 静态检查脚本 |
| `scripts/verify-dev.ps1` | ✅ 是 | 项目级 | 一键联调自检（v3.2.8 已有） |
| `.git/hooks/pre-commit` | ❌ 否 | 本地生成 | 由 setup-repo.ps1 生成，.git/ 不可入 git |
| `.gitignore` | ✅ 是 | 项目级 | 由 setup-repo.ps1 生成（如不存在） |
| `~/.gitconfig`（user.name/email） | ❌ 否 | 用户级 | 个人 Git 身份，不入 git |
| SSH key | ❌ 否 | 用户级 | 个人认证，仅在 docs 中提供配置指引链接 |


---

## 附录 B：Verification Pack — 关键配置验证清单（v4 更新）

### B.1 Steering 验证（v4.1 更新：+安全底线验证）

| # | 验证项 | 操作 | 预期结果 | 断言方式 |
|---|--------|------|----------|----------|
| 1 | always-on 加载 | 新建聊天，问 "当前项目技术栈是什么" | 回答包含 Vite/React/TS/FastAPI/8888/5173 | 人工确认 |
| 2 | fileMatch 触发 | 编辑 `frontend/src/api/request.ts` | 回答引用 api-client.md | 人工确认 |
| 3 | fileMatch 不误触发 | 编辑 `README.md` | 不应引用 api-client.md | 人工确认 |
| 4 | 用户级 steering | 问 "你的默认语言是什么" | 回答中文 | 人工确认 |
| 5 | 禁止 fetch 规则 | 编辑组件写 `fetch('/api/v1/...')` | Kiro 应提示违反规范 | 人工确认 |
| **6** | **安全底线** | **问 "安全底线是什么"** | **回答包含：禁止硬编码 / 环境变量 / 输入校验 / 错误处理** | **人工确认** |

### B.2 MCP 验证（v4.1 更新：+includeMcpJson 验证 + disabled 覆盖）

| # | 验证项 | 操作 | 预期结果 | 断言方式 |
|---|--------|------|----------|----------|
| 1 | chrome-devtools 连接 | 打开 MCP Server 面板 | 状态 `connected` | UI 确认 |
| 2 | autoApprove 生效 | 调用 `navigate_page` | 直接执行，无确认弹窗 | 观察 |
| 3 | autoApprove 边界 | 调用 `click` | 弹出确认对话框 | 观察 |
| **4** | **includeMcpJson 未使用** | **检查 `.kiro/settings/mcp.json`** | **不含 `includeMcpJson` 字段** | **脚本断言** |
| **5** | **用户级 server 隔离** | **MCP 面板检查** | **仅项目级 server（chrome-devtools）显示** | **UI 确认** |
| **6** | **disabled 覆盖** | **项目级设 `"disabled": true`** | **该 server 在面板中显示 disabled 且不可调用** | **观察** |

### B.3 Proxy 链路验证（v4.1 更新：+apiAbsoluteUrlHitDetected + entries 非空）

| # | 验证项 | 操作 | 预期结果 | 断言方式 |
|---|--------|------|----------|----------|
| 1 | 后端直连 | `curl http://localhost:8888/api/v1/health` | `{"code":0,...}` | JSON 断言 |
| 2 | Proxy 转发 | `curl http://localhost:5173/api/v1/health` | 同上 | JSON 断言 |
| 3 | Probe 注入 | evaluate_script 注入 → reload → 读取 | `probeMissing: false` | 脚本返回值 |
| 4 | Probe entries 非空 | 读取 Probe 数据 | `requests` 数组至少 1 条 | 脚本返回值 |
| 5 | 直连检测 | evaluate_script 检测脚本 | `directBackendHitDetected: false` | 脚本返回值 |
| **6** | **绝对 URL 检测** | **evaluate_script 检测脚本** | **`apiAbsoluteUrlHitDetected: false`** | **脚本返回值** |

### B.4 请求层验证（与 v3.2.8 一致）

| # | 验证项 | 操作 | 预期结果 | 断言方式 |
|---|--------|------|----------|----------|
| 1 | path 前缀校验 | `request('users/1')` | 抛出 ApiError "必须以 / 开头" | 单元测试 |
| 2 | isEnvelope 检查 data | 返回 `{code:0, message:"success"}` 无 data | 抛出 "非信封结构" | 单元测试 |
| 3 | text-first 解析 | 返回 HTML | 抛出 ApiError，raw 含 HTML | 单元测试 |
| 4 | HTTP 错误 raw 保留 | 返回 422 + 信封 body | status=422，raw 含完整 body | 单元测试 |

### B.5 安全审计（与 v3.2.8 一致）

| # | 验证项 | 操作 | 预期结果 | 断言方式 |
|---|--------|------|----------|----------|
| 1 | evaluate_script 脚本审计 | 检查采集脚本源码 | 不含写操作 | 代码审查 |
| 2 | mcp.json 字段白名单 | 字段检查 | 仅含白名单字段 | 脚本断言 |
| 3 | autoApprove 工具集审计 | 检查列表 | 仅含低风险工具 | 代码审查 |

### B.6 自动化脚本验证（与 v3.2.8 一致）

B.6.1 Proxy-only 静态扫描、B.6.2 契约漂移成对提交检查、B.6.3 Proxy 链路双向验证、verify-dev.ps1 完整脚本 — 内容与 v3.2.8 完全一致，此处不再重复。

### B.7 Git 工作流验证（v4 新增）

| # | 验证项 | 操作 | 预期结果 | 断言方式 |
|---|--------|------|----------|----------|
| 1 | hook 安装 | `pwsh scripts/setup-repo.ps1` 后检查 `.git/hooks/pre-commit` | 文件存在且引用 `scripts/pre-commit-hook.ps1` | 脚本断言 |
| 2 | hook 阻断（Proxy-only） | 代码中写入 `http://localhost:8888/api/v1/users`，执行 `git add . && git commit -m "测试：测试 hook"` | commit 被阻断，提示"硬编码绝对 API URL" | 观察 |
| 3 | hook 阻断（契约漂移） | 仅修改 `backend/app/schemas/user.py`，不改 `frontend/src/types/`，执行 `git add . && git commit -m "测试：测试契约漂移"` | commit 被阻断，提示"契约漂移" | 观察 |
| 4 | hook 放行 | 正常修改（无违规），执行 `git commit -m "测试：验证 hook 放行"` | commit 成功 | 观察 |
| 5 | --no-verify 跳过 | `git commit --no-verify -m "测试：跳过 hook"` | commit 成功（绕过 hook） | 观察 |
| 6 | commit 格式 | `git log --oneline -5` | 所有 commit 符合 `<类型>：<描述>` 格式 | 人工确认 |
| 7 | setup-repo.ps1 幂等 | 多次运行 `pwsh scripts/setup-repo.ps1` | 不报错，hook 文件被覆盖更新 | 观察 |

---

## 附录 C：v4 完整 Steering 文件体系图

```
~/.kiro/steering/
└── AGENTS.md                          [always] 个人通用偏好

项目/.kiro/steering/
├── product.md                         [always] 产品定义
├── tech.md                            [always] 技术栈 + 端口 + Pydantic v2
├── structure.md                       [always] 目录结构 + 路径别名
├── contract-first.md                  [always] 契约先行（Pydantic v2）
├── dev-loop.md                        [always] 联调规范（Proxy-Only + router prefix）
├── mcp-workflow.md                    [always] MCP 闭环 + Probe + 直连检测 + Specs Gate
├── spec-review-gate.md                [always] Specs 审核门禁
├── git-workflow.md                    [always] Git 分支策略 + 中文 commit + hook + PR ← v4 新增
├── frontend-components.md             [fileMatch: components/**]
├── frontend-pages.md                  [fileMatch: pages/**]
├── api-contracts.md                   [fileMatch: frontend/api/**]
├── api-contracts-backend.md           [fileMatch: backend/api/**]
├── api-schemas-backend.md             [fileMatch: backend/schemas/**]
├── api-mock-and-stub.md               [fileMatch: frontend/api/**]
├── api-client.md                      [fileMatch: frontend/api/**]
├── testing.md                         [fileMatch: **/*.test.*]
├── testing-frontend.md                [fileMatch: frontend/tests/**]
└── testing-backend.md                 [fileMatch: backend/tests/**]

项目/scripts/
├── verify-dev.ps1                     一键联调自检（v3.2.8）
├── setup-repo.ps1                     一键初始化仓库 + hook（v4 新增）
└── pre-commit-hook.ps1                pre-commit 静态检查（v4 新增）

项目/docs/
├── commit-convention.md               中文 commit 规范（v4 新增）
├── dev-workflow.md                    开发工作流 runbook（v4 新增）
└── Kiro-Steering-配置草案-v4.md       本草案

总计：1 全局 + 18 工作区 = 19 个 steering 文件
always-on：9 个 | conditional：10 个
```

> v4 变更完成。等待审核。
> **Phase 0 声明**：本草案不写入任何 steering 文件、不改配置、不执行命令。等待"批准执行"后进入 Phase 1。
