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
