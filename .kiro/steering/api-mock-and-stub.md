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
