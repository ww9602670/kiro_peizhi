# 本地联调开发规范

## ⚠️ 硬规则：Proxy-Only 是唯一开发模式（v3.2.2）

开发环境**仅允许** proxy-only 模式。这不是"推荐"，而是**唯一合法的开发网络模式**。
所有前端 API 请求必须通过 Vite dev server 代理到后端，不存在"直连模式"选项。

| 规则 | 说明 |
|------|------|
| `VITE_API_BASE_URL` 必须为 `/api/v1` | 相对路径，不得以 `/` 结尾 |
| **禁止**将 `VITE_API_BASE_URL` 配为绝对地址 | 如 `http://localhost:8888/api/v1` 属于违规 |
| **禁止**前端直连后端端口 | 浏览器中不得出现 `localhost:8888` 的请求 |
| **禁止**混用 proxy 与直连 | 同一环境只能走一种模式 |
| 后端开发环境默认不配 CORS | 因为同源代理，无需 CORS；若有人绕过 proxy 直连属于违规 |
| **不存在**"直连+CORS"开发模式 | 该选项已在 v3.2.1 中永久移除 |

## 统一本地联调入口

| 服务 | 地址 | 说明 |
|------|------|------|
| 前端 dev server | `http://localhost:5173` | Vite dev server（默认端口），**唯一浏览器入口** |
| 后端 dev server | `http://localhost:8888` | FastAPI uvicorn（仅 proxy 目标，不直接访问） |

## 前端配置

### .env.development
```
VITE_API_BASE_URL=/api/v1
```

### vite.config.ts
```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': resolve(__dirname, 'src') },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8888',
        changeOrigin: true,
      },
    },
  },
})
```

### 前端 API 调用示例
```typescript
// frontend/src/api/health.ts
import { request } from '@/api/request';
export function fetchHealth() {
  return request<{ status: string; service: string }>('/health');
}
```

## 后端配置

### 启动命令
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload
```

### Router Prefix 规范（v3.2.2）
**硬规则**：router 内只定义相对路径，prefix 在 `main.py` 的 `include_router` 统一管理。

```python
# backend/app/api/health.py
from fastapi import APIRouter
router = APIRouter()

@router.get("/health")
async def health():
    return {"code": 0, "message": "success", "data": {"status": "ok", "service": "bocai-backend"}}
```

```python
# backend/app/main.py
from fastapi import FastAPI
from app.api.health import router as health_router

app = FastAPI(title="Bocai Backend")
app.include_router(health_router, prefix="/api/v1", tags=["health"])
```

## 最小可运行联调样例（Contract Smoke）
```bash
# 通过前端代理验证
curl http://localhost:5173/api/v1/health
# 仅诊断：直访后端
curl http://localhost:8888/api/v1/health
```

## 环境变量与启动 Checklist

| 步骤 | 命令 | 说明 |
|------|------|------|
| 1. 启动后端 | `cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload` | 先启动后端 |
| 2. 启动前端 | `cd frontend && pnpm dev` | Vite 默认端口 5173 |
| 3. 验证 proxy | `curl http://localhost:5173/api/v1/health` | 必须返回 `{"code":0,...}` |
| 4. 浏览器验证 | 访问 `http://localhost:5173/` | 页面应显示 health 状态 |
| 5. Chrome MCP | navigate_page + Probe + 截图 | 输出联调报告 |

## 调试证据标准

| 证据项 | 来源 | 必须/可选 |
|--------|------|-----------|
| 请求 URL / method / request body | 浏览器 Network 或 Probe | 必须 |
| 响应 code / message / data | 浏览器 Network 或 Probe | 必须 |
| 前端 console error | Chrome MCP Probe 采集 | 必须 |
| 后端日志片段 | 终端日志 | 必须 |
| traceId（如有） | 响应 header 或 body | 可选（推荐） |
| 页面截图 | Chrome MCP take_screenshot | 可选（推荐） |
| 直连后端检测 | Probe directBackendHitDetected | 必须 |
