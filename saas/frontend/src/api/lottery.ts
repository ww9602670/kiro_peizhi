/**
 * Lottery API
 */
import { request } from '@/api/request';
import type { CurrentInstall } from '@/types/api/lottery';

/**
 * Fetch current install information (with countdown)
 * 
 * Note: Backend returns Envelope format, request layer auto-unpacks data field
 */
export function fetchCurrentInstall(platformType: string = 'JND28WEB') {
  const query = platformType ? `?platform_type=${encodeURIComponent(platformType)}` : '';
  return request<CurrentInstall>(`/lottery/current-install${query}`);
}
