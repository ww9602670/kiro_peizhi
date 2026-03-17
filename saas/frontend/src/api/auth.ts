/**
 * 认证 API 封装
 * - login: 用户名密码登录
 * - refresh: 静默刷新 Token
 * - logout: 登出
 */

import { request } from '@/api/request';
import type { LoginRequest, TokenResponse } from '@/types/api/auth';

export async function login(data: LoginRequest) {
  return request<TokenResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function refresh() {
  return request<TokenResponse>('/auth/refresh', {
    method: 'POST',
  });
}

export async function logout() {
  return request<null>('/auth/logout', {
    method: 'POST',
  });
}
