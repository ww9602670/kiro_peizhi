---
inclusion: fileMatch
fileMatchPattern: "backend/app/schemas/**/*.py"
---

# Pydantic v2 Schema 规范

## 版本要求
- 本项目统一使用 **Pydantic v2**（>= 2.0）
- 禁止使用 v1 兼容层（`from pydantic.v1 import ...`）

## 写法规范

### 必须使用 v2 语法
```python
from pydantic import BaseModel, Field, ConfigDict

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

### 禁止 v1 写法
```python
# ❌ 禁止
class UserResponse(BaseModel):
    class Config:
        schema_extra = {...}
```

## 示例 Payload 要求
- 每个 schema 必须提供 `json_schema_extra` 中的 `example`
- 至少包含 1 个 success 示例
- Mock Helper（`backend/app/utils/mock_helper.py`）依赖此 example 数据

## 成对提交
- 修改 schema 时，必须同步更新 `frontend/src/types/api/` 下对应的 TypeScript interface
- 字段名、类型、可选性必须一一对应
