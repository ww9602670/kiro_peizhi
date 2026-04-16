/**
 * 玩法列表 API
 */
import { request } from '@/api/request';
import type { PlayCodeGroup } from '@/types/api/play-code';

export async function listPlayCodes(commonOnly = false) {
  const query = commonOnly ? '?common_only=true' : '';
  return request<PlayCodeGroup[]>(`/play-codes${query}`);
}
