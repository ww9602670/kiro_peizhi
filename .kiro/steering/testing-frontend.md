---
inclusion: fileMatch
fileMatchPattern: "frontend/tests/**"
---

# 前端测试规范

## 工具链
- 测试框架：Vitest
- 组件测试：React Testing Library
- 属性测试：fast-check
- E2E（可选）：Playwright

## 目录结构
```
frontend/tests/
├── unit/          # 单元测试
└── e2e/           # 端到端测试
```

## 单元测试规则
- 测试统一请求层（request.ts）的各种边界情况
- 测试 API 封装函数的参数传递和错误处理
- 组件测试关注用户交互和渲染结果

## 属性测试（fast-check）
- 用于验证统一请求层的通用属性
- 用于验证数据转换函数的不变量
- 生成器应约束到合理的输入空间

## 禁止事项
- 禁止在测试中直接调用 `fetch`（应通过 mock 或 MSW）
- 禁止硬编码绝对 API URL
