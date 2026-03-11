import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useAuth } from './useAuth';

// Mock auth API module
vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  refresh: vi.fn(),
  logout: vi.fn(),
}));

import * as authApi from '@/api/auth';

const mockLogin = vi.mocked(authApi.login);
const mockRefresh = vi.mocked(authApi.refresh);
const mockLogout = vi.mocked(authApi.logout);

function createJwt(payload: Record<string, unknown>): string {
  const header = { alg: 'HS256', typ: 'JWT' };
  const encode = (value: object) =>
    btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
  return `${encode(header)}.${encode(payload)}.signature`;
}

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
  mockLogin.mockReset();
  mockRefresh.mockReset();
  mockLogout.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('useAuth', () => {
  it('初始状态：无 token 时 isAuthenticated=false', () => {
    const { result } = renderHook(() => useAuth());
    expect(result.current.isAuthenticated).toBe(false);
  });

  it('初始状态：有有效 token 时 isAuthenticated=true', () => {
    localStorage.setItem('token', 'valid-token');
    localStorage.setItem('expire_at', new Date(Date.now() + 3600000).toISOString());

    const { result } = renderHook(() => useAuth());
    expect(result.current.isAuthenticated).toBe(true);
  });

  it('初始状态：token 已过期时 isAuthenticated=false', () => {
    localStorage.setItem('token', 'expired-token');
    localStorage.setItem('expire_at', new Date(Date.now() - 1000).toISOString());

    const { result } = renderHook(() => useAuth());
    expect(result.current.isAuthenticated).toBe(false);
  });

  it('login 成功后保存 token 并设置 isAuthenticated=true', async () => {
    const expireAt = new Date(Date.now() + 86400000).toISOString();
    mockLogin.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: { token: 'new-token', expire_at: expireAt },
    });

    const { result } = renderHook(() => useAuth());
    expect(result.current.isAuthenticated).toBe(false);

    await act(async () => {
      await result.current.login({ username: 'admin', password: 'admin123' });
    });

    expect(result.current.isAuthenticated).toBe(true);
    expect(localStorage.getItem('token')).toBe('new-token');
    expect(localStorage.getItem('expire_at')).toBe(expireAt);
  });

  it('login stores role from a base64url JWT payload', async () => {
    const expireAt = new Date(Date.now() + 86400000).toISOString();
    const token = createJwt({
      sub: '1',
      role: 'admin',
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    mockLogin.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: { token, expire_at: expireAt },
    });

    const { result } = renderHook(() => useAuth());

    await act(async () => {
      await result.current.login({ username: 'admin', password: 'admin123' });
    });

    expect(result.current.role).toBe('admin');
    expect(localStorage.getItem('role')).toBe('admin');
  });

  it('login 失败时不改变状态', async () => {
    mockLogin.mockRejectedValueOnce({ code: 2001, message: '用户名或密码错误' });

    const { result } = renderHook(() => useAuth());

    await act(async () => {
      try {
        await result.current.login({ username: 'bad', password: 'bad' });
      } catch {
        // expected
      }
    });

    expect(result.current.isAuthenticated).toBe(false);
    expect(localStorage.getItem('token')).toBeNull();
  });

  it('logout 清除 token 并设置 isAuthenticated=false', async () => {
    localStorage.setItem('token', 'valid-token');
    localStorage.setItem('expire_at', new Date(Date.now() + 3600000).toISOString());
    mockLogout.mockResolvedValueOnce({ code: 0, message: 'success', data: null });

    const { result } = renderHook(() => useAuth());
    expect(result.current.isAuthenticated).toBe(true);

    await act(async () => {
      await result.current.logout();
    });

    expect(result.current.isAuthenticated).toBe(false);
    expect(localStorage.getItem('token')).toBeNull();
    expect(localStorage.getItem('expire_at')).toBeNull();
  });

  it('logout 请求失败时仍清除本地状态', async () => {
    localStorage.setItem('token', 'valid-token');
    localStorage.setItem('expire_at', new Date(Date.now() + 3600000).toISOString());
    mockLogout.mockRejectedValueOnce({ code: 5001, message: '服务端错误' });

    const { result } = renderHook(() => useAuth());

    await act(async () => {
      await result.current.logout();
    });

    expect(result.current.isAuthenticated).toBe(false);
    expect(localStorage.getItem('token')).toBeNull();
  });

  it('silentRefresh 在刷新窗口内成功刷新 token', async () => {
    // 设置 token 在 20 分钟后过期（在 30 分钟刷新窗口内）
    const expireAt = new Date(Date.now() + 20 * 60 * 1000).toISOString();
    localStorage.setItem('token', 'old-token');
    localStorage.setItem('expire_at', expireAt);

    const newExpireAt = new Date(Date.now() + 86400000).toISOString();
    mockRefresh.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: { token: 'refreshed-token', expire_at: newExpireAt },
    });

    const { result } = renderHook(() => useAuth());

    await act(async () => {
      await result.current.silentRefresh();
    });

    expect(localStorage.getItem('token')).toBe('refreshed-token');
    expect(localStorage.getItem('expire_at')).toBe(newExpireAt);
  });

  it('silentRefresh 不在刷新窗口内时不刷新', async () => {
    // 设置 token 在 2 小时后过期（不在 30 分钟刷新窗口内）
    const expireAt = new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString();
    localStorage.setItem('token', 'valid-token');
    localStorage.setItem('expire_at', expireAt);

    const { result } = renderHook(() => useAuth());

    await act(async () => {
      await result.current.silentRefresh();
    });

    // refresh API 不应被调用
    expect(mockRefresh).not.toHaveBeenCalled();
    expect(localStorage.getItem('token')).toBe('valid-token');
  });

  it('silentRefresh 失败时清除 token', async () => {
    const expireAt = new Date(Date.now() + 20 * 60 * 1000).toISOString();
    localStorage.setItem('token', 'old-token');
    localStorage.setItem('expire_at', expireAt);

    mockRefresh.mockRejectedValueOnce({ code: 2003, message: '刷新窗口外' });

    const { result } = renderHook(() => useAuth());

    await act(async () => {
      await result.current.silentRefresh();
    });

    expect(result.current.isAuthenticated).toBe(false);
    expect(localStorage.getItem('token')).toBeNull();
  });
});
