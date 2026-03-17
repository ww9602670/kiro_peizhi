/**
 * 认证状态管理 Hook
 * - Token 存储（localStorage）
 * - 登录 / 登出
 * - 静默刷新（过期前 30 分钟窗口内自动刷新）
 * - isAuthenticated 状态
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import * as authApi from '@/api/auth';
import type { LoginRequest } from '@/types/api/auth';
import { isApiError } from '@/api/request';

const TOKEN_KEY = 'token';
const EXPIRE_KEY = 'expire_at';
const ROLE_KEY = 'role';
/** 过期前 30 分钟开始刷新 */
const REFRESH_WINDOW_MS = 30 * 60 * 1000;
/** 刷新检查间隔：每 60 秒检查一次 */
const CHECK_INTERVAL_MS = 60 * 1000;

function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

function getStoredExpireAt(): string | null {
  return localStorage.getItem(EXPIRE_KEY);
}

function getStoredRole(): string | null {
  return localStorage.getItem(ROLE_KEY);
}

function decodeBase64Url(value: string): string {
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/');
  const padding = normalized.length % 4;
  const padded = padding === 0 ? normalized : normalized.padEnd(normalized.length + (4 - padding), '=');
  return atob(padded);
}

/** 从 JWT payload 中提取 role（JWT 使用 base64url 编码） */
function extractRoleFromToken(token: string): string | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    const payload = JSON.parse(decodeBase64Url(parts[1]));
    return payload.role ?? null;
  } catch {
    return null;
  }
}

function isTokenValid(): boolean {
  const token = getStoredToken();
  const expireAt = getStoredExpireAt();
  if (!token || !expireAt) return false;
  return new Date(expireAt).getTime() > Date.now();
}

function shouldRefresh(): boolean {
  const expireAt = getStoredExpireAt();
  if (!expireAt) return false;
  const expireTime = new Date(expireAt).getTime();
  const now = Date.now();
  // 在过期前 30 分钟窗口内且尚未过期
  return now >= expireTime - REFRESH_WINDOW_MS && now < expireTime;
}

export function useAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState(isTokenValid);
  const [role, setRole] = useState<string | null>(getStoredRole);
  const refreshTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const isRefreshingRef = useRef(false);

  const saveToken = useCallback((token: string, expireAt: string) => {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(EXPIRE_KEY, expireAt);
    const tokenRole = extractRoleFromToken(token);
    if (tokenRole) {
      localStorage.setItem(ROLE_KEY, tokenRole);
    } else {
      localStorage.removeItem(ROLE_KEY);
    }
    setRole(tokenRole);
    setIsAuthenticated(true);
    window.dispatchEvent(new Event('auth-change'));
  }, []);

  const clearToken = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(EXPIRE_KEY);
    localStorage.removeItem(ROLE_KEY);
    setIsAuthenticated(false);
    setRole(null);
    window.dispatchEvent(new Event('auth-change'));
  }, []);

  const login = useCallback(
    async (data: LoginRequest) => {
      const res = await authApi.login(data);
      if (res.data) {
        saveToken(res.data.token, res.data.expire_at);
      }
      return res;
    },
    [saveToken],
  );

  const logoutFn = useCallback(async () => {
    try {
      await authApi.logout();
    } catch (err) {
      // 登出失败也清除本地状态
      if (isApiError(err)) {
        console.warn('登出请求失败:', err.message);
      }
    } finally {
      clearToken();
    }
  }, [clearToken]);

  const silentRefresh = useCallback(async () => {
    if (isRefreshingRef.current) return;
    if (!shouldRefresh()) return;

    isRefreshingRef.current = true;
    try {
      const res = await authApi.refresh();
      if (res.data) {
        saveToken(res.data.token, res.data.expire_at);
      }
    } catch (err) {
      // 刷新失败 → 清除 token，用户需重新登录
      if (isApiError(err)) {
        console.warn('Token 刷新失败:', err.message);
      }
      clearToken();
    } finally {
      isRefreshingRef.current = false;
    }
  }, [saveToken, clearToken]);

  // 监听 auth-change 事件（跨 hook 实例同步）
  useEffect(() => {
    const handleAuthChange = () => {
      const valid = isTokenValid();
      setIsAuthenticated(valid);
      setRole(getStoredRole());
    };
    window.addEventListener('auth-change', handleAuthChange);
    window.addEventListener('storage', handleAuthChange);
    return () => {
      window.removeEventListener('auth-change', handleAuthChange);
      window.removeEventListener('storage', handleAuthChange);
    };
  }, []);

  // 定时检查是否需要刷新
  useEffect(() => {
    if (!isAuthenticated) {
      if (refreshTimerRef.current) {
        clearInterval(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
      return;
    }

    refreshTimerRef.current = setInterval(() => {
      if (shouldRefresh()) {
        silentRefresh();
      }
      // 检查 token 是否已过期
      if (!isTokenValid()) {
        clearToken();
      }
    }, CHECK_INTERVAL_MS);

    return () => {
      if (refreshTimerRef.current) {
        clearInterval(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
    };
  }, [isAuthenticated, silentRefresh, clearToken]);

  return {
    isAuthenticated,
    role,
    login,
    logout: logoutFn,
    silentRefresh,
  };
}
