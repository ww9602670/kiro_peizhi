---
inclusion: fileMatch
fileMatchPattern: "frontend/src/pages/**/*.{ts,tsx}"
---

# 前端页面规范

## 硬规则

### 禁止直接 fetch
- 页面中**禁止**直接使用 `fetch` / `axios` / `ky` 等发起 API 请求
- 所有 API 调用必须通过 `@/api/` 下的封装函数（统一请求层）
- 违反此规则的代码不得通过 review

### 页面结构
- 页面组件放在 `frontend/src/pages/`
- 每个页面对应一个路由
- 页面文件使用 PascalCase 命名：`UserList.tsx`

### 数据获取
- 页面通过 `@/api/` 封装函数获取数据
- 错误处理：至少 toast 提示 ApiError.message
- 加载状态：必须展示 loading 指示器
