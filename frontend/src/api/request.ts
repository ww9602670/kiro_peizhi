/**
 * 统一请求层
 * - Base URL 从 import.meta.env.VITE_API_BASE_URL 读取（/api/v1）
 * - 统一信封解析：{ code, message, data }
 * - 非 code=0 抛出 ApiError
 * - 401 自动跳转登录页
 */

import type { ApiResponse } from '@/types/api/common';

const BASE_URL = import.meta.env.VITE_API_BASE_URL as string;

export interface ApiError {
  code: number;
  message: string;
  status?: number;
  raw?: unknown;
  traceId?: string;
}

export function isApiError(err: unknown): err is ApiError {
  return (
    typeof err === 'object' &&
    err !== null &&
    'code' in err &&
    'message' in err &&
    typeof (err as ApiError).code === 'number' &&
    typeof (err as ApiError).message === 'string'
  );
}

function isEnvelope(data: unknown): data is { code: number; message: string; data: unknown } {
  return (
    typeof data === 'object' &&
    data !== null &&
    'code' in data &&
    'message' in data &&
    'data' in data &&
    typeof (data as Record<string, unknown>).code === 'number' &&
    typeof (data as Record<string, unknown>).message === 'string'
  );
}

export async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<ApiResponse<T>> {
  if (!path.startsWith('/')) {
    throw {
      code: -1,
      message: `request path 必须以 / 开头，当前值: "${path}"`,
    } as ApiError;
  }

  const url = `${BASE_URL}${path}`;

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options?.headers as Record<string, string> | undefined),
  };

  // 自动附加 Authorization header
  const token = localStorage.getItem('token');
  if (token && !headers['Authorization']) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(url, { ...options, headers });

  const text = await res.text();
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    body = undefined;
  }

  // 401 → 自动跳转登录页
  if (res.status === 401) {
    localStorage.removeItem('token');
    localStorage.removeItem('expire_at');
    localStorage.removeItem('role');
    window.location.href = '/login';
    const envelope = isEnvelope(body) ? body : null;
    throw {
      code: envelope?.code ?? 2001,
      message: envelope?.message ?? '认证失败，请重新登录',
      status: 401,
      raw: body ?? text,
    } as ApiError;
  }

  if (!res.ok) {
    const envelope = isEnvelope(body) ? body : null;
    throw {
      code: envelope?.code ?? -1,
      message: envelope?.message ?? `HTTP ${res.status}: ${res.statusText}`,
      status: res.status,
      raw: body ?? text,
    } as ApiError;
  }

  if (body === undefined) {
    throw {
      code: -1,
      message: '响应体 JSON 解析失败',
      status: res.status,
      raw: text,
    } as ApiError;
  }

  if (!isEnvelope(body)) {
    throw {
      code: -1,
      message: '响应不符合 { code, message, data } 信封结构',
      status: res.status,
      raw: body,
    } as ApiError;
  }

  if (body.code !== 0) {
    throw {
      code: body.code,
      message: body.message,
      raw: body,
      traceId: (body as Record<string, unknown>).traceId as string | undefined,
    } as ApiError;
  }

  return body as ApiResponse<T>;
}
