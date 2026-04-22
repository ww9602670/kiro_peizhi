/**
 * 认证 API 封装
 * - login: 用户名密码登录
 * - refresh: 静默刷新 Token
 * - logout: 登出
 */

import { request } from '@/api/request';
import type { LoginRequest, TokenResponse } from '@/types/api/auth';
import type { OperatorChangePasswordRequest, OperatorMeInfo } from '@/types/api/operator';

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

export async function fetchOperatorMe() {
  return request<OperatorMeInfo>('/operator/me');
}

export async function updateOperatorPassword(data: OperatorChangePasswordRequest) {
  return request<null>('/operator/me/password', {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}
