/**
 * 博彩账号 API 封装
 * - listAccounts: 我的博彩账号列表
 * - createAccount: 绑定博彩账号
 * - deleteAccount: 解绑博彩账号
 * - loginAccount: 手动触发登录
 * - updateKillSwitch: 账号级熔断开关
 */

import { request } from '@/api/request';
import type { AccountCreate, AccountInfo, KillSwitchUpdate } from '@/types/api/account';

export async function listAccounts() {
  return request<AccountInfo[]>('/accounts');
}

export async function createAccount(data: AccountCreate) {
  return request<AccountInfo>('/accounts', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function deleteAccount(id: number) {
  return request<null>(`/accounts/${id}`, {
    method: 'DELETE',
  });
}

export async function loginAccount(id: number) {
  return request<AccountInfo>(`/accounts/${id}/login`, {
    method: 'POST',
  });
}

export async function updateKillSwitch(id: number, data: KillSwitchUpdate) {
  return request<AccountInfo>(`/accounts/${id}/kill-switch`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}
