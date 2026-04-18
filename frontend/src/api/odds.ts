import { request } from '@/api/request';
import type { OddsListResponse, OddsConfirmResponse, OddsRefreshResponse } from '@/types/api/odds';

function buildOddsPath(accountId: number, platformType: string, suffix = '') {
  const params = new URLSearchParams({ platform_type: platformType });
  return `/accounts/${accountId}/odds${suffix}?${params.toString()}`;
}

export async function getAccountOdds(accountId: number, platformType: string) {
  return request<OddsListResponse>(buildOddsPath(accountId, platformType));
}

export async function confirmAccountOdds(accountId: number, platformType: string) {
  return request<OddsConfirmResponse>(buildOddsPath(accountId, platformType, '/confirm'), {
    method: 'POST',
  });
}

export async function refreshAccountOdds(accountId: number, platformType: string) {
  return request<OddsRefreshResponse>(buildOddsPath(accountId, platformType, '/refresh'), {
    method: 'POST',
  });
}
