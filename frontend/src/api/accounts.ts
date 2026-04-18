/**
 * Account API wrappers.
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

export async function verifyAccount(id: number) {
  return request<AccountInfo>(`/accounts/${id}/verify`, {
    method: 'POST',
  });
}

/**
 * @deprecated Use verifyAccount. Kept only as a compatibility alias.
 */
export async function loginAccount(id: number) {
  return verifyAccount(id);
}

export async function logoutAccount(id: number) {
  return request<AccountInfo>(`/accounts/${id}/logout`, {
    method: 'POST',
  });
}

export async function updateKillSwitch(id: number, data: KillSwitchUpdate) {
  return request<AccountInfo>(`/accounts/${id}/kill-switch`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}
