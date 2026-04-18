/**
 * 玩法列表 API
 */
import { request } from '@/api/request';
import type { PlayCodeGroup } from '@/types/api/play-code';

type ListPlayCodesOptions = boolean | {
  commonOnly?: boolean;
  platformType?: string;
};

export async function listPlayCodes(options: ListPlayCodesOptions = false) {
  const commonOnly = typeof options === 'boolean' ? options : options.commonOnly;
  const platformType = typeof options === 'boolean' ? undefined : options.platformType;
  const params = new URLSearchParams();
  if (commonOnly) params.set('common_only', 'true');
  if (platformType) params.set('platform_type', platformType);
  const query = params.toString() ? `?${params.toString()}` : '';
  return request<PlayCodeGroup[]>(`/play-codes${query}`);
}
