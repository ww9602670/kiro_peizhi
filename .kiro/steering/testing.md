---
inclusion: fileMatch
fileMatchPattern: "**/*.test.*"
---

# 测试规范

## 通用规则
- 测试文件与源文件同名，后缀 `.test.ts` / `.test.tsx` / `test_*.py`
- 测试必须可独立运行，不依赖外部服务（除 E2E）
- 禁止在单元测试中使用真实网络请求

## 测试分层

| 层级 | 前端 | 后端 | 说明 |
|------|------|------|------|
| 单元测试 | Vitest | pytest | 函数/组件级别 |
| 属性测试 | fast-check | hypothesis | 核心逻辑验证 |
| 集成测试 | — | pytest + httpx | API 端点级别 |
| E2E 测试 | Playwright（可选） | — | 全链路验证 |

## Mock 规则
- 单元测试可使用 mock
- E2E 测试禁止 mock
- Mock 数据必须来自 schema example / types 定义

## 覆盖率
- 核心业务逻辑：建议 > 80%
- 工具函数：建议 > 90%
- 不追求 100% 覆盖率，优先覆盖关键路径
