---
inclusion: fileMatch
fileMatchPattern: "backend/tests/**/*.py"
---

# 后端测试规范

## 工具链
- 测试框架：pytest
- 属性测试：hypothesis
- HTTP 客户端：httpx（用于 FastAPI TestClient）

## 目录结构
```
backend/tests/
├── test_api/          # API 端点测试
├── test_schemas/      # Schema 验证测试
└── conftest.py        # 共享 fixtures
```

## 测试规则
- 使用 `httpx.AsyncClient` + FastAPI `TestClient` 测试端点
- 验证响应符合统一信封格式：`{"code": 0, "message": "...", "data": ...}`
- 验证错误码符合分段规则

## 属性测试（hypothesis）
- 用于验证 schema 序列化/反序列化的不变量
- 用于验证业务逻辑的通用属性
- 策略应约束到合理的输入空间

## 禁止事项
- 禁止在测试中硬编码绝对 API URL
- 禁止跳过信封格式验证
