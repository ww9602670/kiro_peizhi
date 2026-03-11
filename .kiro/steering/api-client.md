---
inclusion: fileMatch
fileMatchPattern: "frontend/src/api/**/*.{ts,tsx}"
---

# 统一请求层规范

**所有 API 调用必须经过统一请求层（`frontend/src/api/request.ts`），禁止在组件或页面中直接使用 `fetch` / `axios` / `ky` 等。**

### request.ts 核心逻辑（v3.2.5）

```typescript
const BASE_URL = import.meta.env.VITE_API_BASE_URL; // /api/v1

export interface ApiResponse<T> {
  code: number;
  message: string;
  data: T | null;
}

export interface ApiError {
  code: number;
  message: string;
  status?: number;
  raw?: unknown;
  traceId?: string;
}

export async function request<T>(path: string, options?: RequestInit): Promise<ApiResponse<T>> {
  if (!path.startsWith('/')) {
    throw { code: -1, message: `request path 必须以 / 开头，当前值: "${path}"` } as ApiError;
  }
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  const text = await res.text();
  let body: unknown;
  try { body = JSON.parse(text); } catch { body = undefined; }

  if (!res.ok) {
    const envelope = isEnvelope(body) ? body : null;
    throw { code: envelope?.code ?? -1, message: envelope?.message ?? `HTTP ${res.status}: ${res.statusText}`, status: res.status, raw: body ?? text } as ApiError;
  }
  if (body === undefined) throw { code: -1, message: '响应体 JSON 解析失败', status: res.status, raw: text } as ApiError;
  if (!isEnvelope(body)) throw { code: -1, message: '响应不符合 { code, message, data } 信封结构', status: res.status, raw: body } as ApiError;
  if (body.code !== 0) throw { code: body.code, message: body.message, raw: body, traceId: (body as any).traceId } as ApiError;
  return body as ApiResponse<T>;
}

function isEnvelope(data: unknown): data is { code: number; message: string; data: unknown } {
  return typeof data === 'object' && data !== null && 'code' in data && 'message' in data && 'data' in data
    && typeof (data as any).code === 'number' && typeof (data as any).message === 'string';
}
```

**关键设计**：
- isEnvelope 必须检查 `data` 字段（v3.2.4）
- ApiResponse.data 类型为 `T | null`（匹配后端 `data: T | None = None`）
- HTTP 错误分支始终保留 `raw`
- text-first JSON 解析（先 `res.text()` 再 `JSON.parse`）
- path 必须以 `/` 开头（v3.2.5）
- HTTP 错误时 `code`（业务码）与 `status`（HTTP 码）独立
