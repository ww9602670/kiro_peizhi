import { request } from '@/api/request';
import type { OddsListResponse, OddsConfirmResponse, OddsRefreshResponse } from '@/types/api/odds';

export async function getAccountOdds(accountId: number) {
  return request<OddsListResponse>(`/accounts/${accountId}/odds`);
}

export async function confirmAccountOdds(accountId: number) {
  return request<OddsConfirmResponse>(`/accounts/${accountId}/odds/confirm`, {
    method: 'POST',
  });
}

export async function refreshAccountOdds(accountId: number) {
  return request<OddsRefreshResponse>(`/accounts/${accountId}/odds/refresh`, {
    method: 'POST',
  });
}
