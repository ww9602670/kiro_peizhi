---
inclusion: fileMatch
fileMatchPattern: "backend/app/api/**/*.py"
---

# 后端 API 路由规范

## 核心规则

### Router Prefix
- **硬规则**：router 内只定义相对路径
- prefix 在 `main.py` 的 `include_router` 统一管理
- 禁止在 router 文件中写完整路径（如 `/api/v1/health`）

### 响应格式
- 所有端点必须返回统一信封：`{"code": 0, "message": "success", "data": {...}}`
- 错误响应同样使用信封格式，code 按错误码分段规则

### 契约先行
- 新增端点前，必须先在 `backend/app/schemas/` 定义 Pydantic v2 schema
- schema 必须包含 request model + response model + 示例 payload
- 端点实现必须引用 schema 作为请求/响应类型注解

### 成对提交
- 修改路由时，必须同步检查前端 `frontend/src/api/` 和 `frontend/src/types/api/` 是否需要更新
- 禁止只改后端不改前端类型

### 异常处理
- 业务异常使用信封 code 表达，不使用 HTTP 状态码
- 422 验证错误应被全局异常处理器捕获并封装为信封格式
- 500 错误应被全局异常处理器捕获，dev 环境保留 traceback
