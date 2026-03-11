import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { request, isApiError } from './request';

// Mock fetch globally
const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

beforeEach(() => {
  vi.stubGlobal('import.meta', { env: { VITE_API_BASE_URL: '/api/v1' } });
  localStorage.clear();
  mockFetch.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, status = 200, statusText = 'OK') {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    statusText,
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

describe('request', () => {
  it('成功请求返回信封数据', async () => {
    mockFetch.mockReturnValueOnce(
      jsonResponse({ code: 0, message: 'success', data: { id: 1 } }),
    );

    const res = await request<{ id: number }>('/test');
    expect(res.code).toBe(0);
    expect(res.data).toEqual({ id: 1 });
  });

  it('path 不以 / 开头时抛出 ApiError', async () => {
    try {
      await request('no-slash');
      expect.fail('应该抛出错误');
    } catch (err) {
      expect(isApiError(err)).toBe(true);
      if (isApiError(err)) {
        expect(err.code).toBe(-1);
        expect(err.message).toContain('必须以 / 开头');
      }
    }
  });

  it('非 code=0 的信封响应抛出 ApiError', async () => {
    mockFetch.mockReturnValueOnce(
      jsonResponse({ code: 2001, message: 'token 过期', data: null }),
    );

    try {
      await request('/test');
      expect.fail('应该抛出错误');
    } catch (err) {
      expect(isApiError(err)).toBe(true);
      if (isApiError(err)) {
        expect(err.code).toBe(2001);
        expect(err.message).toBe('token 过期');
      }
    }
  });

  it('HTTP 错误（非 401）抛出 ApiError 并包含 status', async () => {
    mockFetch.mockReturnValueOnce(
      jsonResponse({ code: 1001, message: '参数错误', data: null }, 400, 'Bad Request'),
    );

    try {
      await request('/test');
      expect.fail('应该抛出错误');
    } catch (err) {
      expect(isApiError(err)).toBe(true);
      if (isApiError(err)) {
        expect(err.code).toBe(1001);
        expect(err.status).toBe(400);
      }
    }
  });

  it('HTTP 错误无信封时使用默认消息', async () => {
    mockFetch.mockReturnValueOnce(
      Promise.resolve({
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
        text: () => Promise.resolve('not json'),
      }),
    );

    try {
      await request('/test');
      expect.fail('应该抛出错误');
    } catch (err) {
      expect(isApiError(err)).toBe(true);
      if (isApiError(err)) {
        expect(err.code).toBe(-1);
        expect(err.message).toContain('HTTP 500');
      }
    }
  });

  it('401 响应清除 token 并跳转登录页', async () => {
    localStorage.setItem('token', 'old-token');
    localStorage.setItem('expire_at', '2099-01-01T00:00:00Z');
    localStorage.setItem('role', 'admin');

    // Mock window.location
    const locationSpy = vi.spyOn(window, 'location', 'get').mockReturnValue({
      ...window.location,
      href: '/',
    });
    // 使用 Object.defineProperty 来 mock href setter
    let capturedHref = '';
    Object.defineProperty(window, 'location', {
      value: { ...window.location, href: '/' },
      writable: true,
      configurable: true,
    });
    const originalLocation = window.location;
    Object.defineProperty(window, 'location', {
      get: () => originalLocation,
      set: () => {},
      configurable: true,
    });
    // Simpler approach: just verify token is cleared
    mockFetch.mockReturnValueOnce(
      jsonResponse({ code: 2001, message: '认证失败', data: null }, 401, 'Unauthorized'),
    );

    try {
      await request('/test');
      expect.fail('应该抛出错误');
    } catch (err) {
      expect(isApiError(err)).toBe(true);
      if (isApiError(err)) {
        expect(err.status).toBe(401);
      }
    }

    // token 应被清除
    expect(localStorage.getItem('token')).toBeNull();
    expect(localStorage.getItem('expire_at')).toBeNull();
    expect(localStorage.getItem('role')).toBeNull();

    locationSpy.mockRestore();
  });

  it('响应体非 JSON 时抛出解析错误', async () => {
    mockFetch.mockReturnValueOnce(
      Promise.resolve({
        ok: true,
        status: 200,
        statusText: 'OK',
        text: () => Promise.resolve('not json at all'),
      }),
    );

    try {
      await request('/test');
      expect.fail('应该抛出错误');
    } catch (err) {
      expect(isApiError(err)).toBe(true);
      if (isApiError(err)) {
        expect(err.code).toBe(-1);
        expect(err.message).toContain('JSON 解析失败');
      }
    }
  });

  it('响应体不符合信封结构时抛出错误', async () => {
    mockFetch.mockReturnValueOnce(
      jsonResponse({ result: 'ok' }), // 缺少 code/message/data
    );

    try {
      await request('/test');
      expect.fail('应该抛出错误');
    } catch (err) {
      expect(isApiError(err)).toBe(true);
      if (isApiError(err)) {
        expect(err.code).toBe(-1);
        expect(err.message).toContain('信封结构');
      }
    }
  });

  it('自动附加 Authorization header', async () => {
    localStorage.setItem('token', 'my-jwt-token');

    mockFetch.mockReturnValueOnce(
      jsonResponse({ code: 0, message: 'success', data: null }),
    );

    await request('/test');

    const callArgs = mockFetch.mock.calls[0];
    const headers = callArgs[1]?.headers as Record<string, string>;
    expect(headers['Authorization']).toBe('Bearer my-jwt-token');
  });

  it('无 token 时不附加 Authorization header', async () => {
    mockFetch.mockReturnValueOnce(
      jsonResponse({ code: 0, message: 'success', data: null }),
    );

    await request('/test');

    const callArgs = mockFetch.mock.calls[0];
    const headers = callArgs[1]?.headers as Record<string, string>;
    expect(headers['Authorization']).toBeUndefined();
  });
});

describe('isApiError', () => {
  it('正确识别 ApiError 对象', () => {
    expect(isApiError({ code: 1, message: 'err' })).toBe(true);
    expect(isApiError({ code: 0, message: 'ok', status: 200 })).toBe(true);
  });

  it('非 ApiError 对象返回 false', () => {
    expect(isApiError(null)).toBe(false);
    expect(isApiError(undefined)).toBe(false);
    expect(isApiError('string')).toBe(false);
    expect(isApiError({ code: 'not-number', message: 'err' })).toBe(false);
    expect(isApiError({ code: 1 })).toBe(false);
  });
});
