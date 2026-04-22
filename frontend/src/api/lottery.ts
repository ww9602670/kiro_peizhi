/**
 * Lottery API
 */
import { request } from '@/api/request';
import type { ApiResponse } from '@/types/api/common';
import {
  normalizeCurrentInstall,
  type CurrentInstall,
  type CurrentInstallWire,
} from '@/types/api/lottery';

/**
 * Fetch current install information (with countdown)
 */
export async function fetchCurrentInstall(
  platformType: string = 'JND28WEB',
): Promise<ApiResponse<CurrentInstall>> {
  const normalizedPlatformType = (platformType || 'JND28WEB').trim().toUpperCase();
  const query = normalizedPlatformType
    ? `?platform_type=${encodeURIComponent(normalizedPlatformType)}`
    : '';
  const response = await request<CurrentInstallWire>(`/lottery/current-install${query}`);
  return {
    ...response,
    data: response.data ? normalizeCurrentInstall(response.data) : null,
  };
}
