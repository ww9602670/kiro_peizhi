---
inclusion: fileMatch
fileMatchPattern: "frontend/src/components/**/*.{ts,tsx}"
---

# 前端组件规范

## 硬规则

### 禁止直接 fetch
- 组件中**禁止**直接使用 `fetch` / `axios` / `ky` 等发起 API 请求
- 所有 API 调用必须通过 `@/api/` 下的封装函数（统一请求层）
- 违反此规则的代码不得通过 review

### 组件结构
- 通用组件放在 `frontend/src/components/`
- 页面级组件放在 `frontend/src/pages/`
- 组件文件使用 PascalCase 命名：`UserCard.tsx`

### 类型安全
- 所有 props 必须定义 TypeScript interface
- API 响应数据必须使用 `@/types/api/` 下的类型
