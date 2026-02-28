---
inclusion: fileMatch
fileMatchPattern: "frontend/src/api/**/*.{ts,tsx}"
---

# 前端 API 契约规范

## 核心规则
- 所有 API 调用函数放在 `frontend/src/api/` 目录
- 必须通过统一请求层 `request.ts` 发起请求（详见 api-client.md）
- 禁止在此目录外直接调用 `fetch` / `axios`

## 类型对应
- 每个 API 模块的请求/响应类型必须在 `frontend/src/types/api/` 中定义
- 类型字段必须与后端 Pydantic schema 一一对应
- 字段名、类型、可选性不得有偏差

## 文件命名
- API 封装文件与后端路由模块同名：`user.ts` 对应 `backend/app/api/user.py`
- 类型文件同名：`frontend/src/types/api/user.ts` 对应 `backend/app/schemas/user.py`

## 成对提交
- 修改 API 调用时，必须同步检查后端 schema 是否有变更
- 修改前端类型时，必须确认后端 schema 已同步
