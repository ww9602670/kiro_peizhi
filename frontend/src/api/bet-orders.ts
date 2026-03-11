/**
 * 投注记录 API 封装
 * - listBetOrders: 投注记录列表（分页 + 筛选）
 * - getBetOrder: 投注详情
 */

import { request } from '@/api/request';
import type { BetOrderInfo } from '@/types/api/bet-order';
import type { PagedData } from '@/types/api/common';

export interface ListBetOrdersParams {
  page?: number;
  page_size?: number;
  date_from?: string;
  date_to?: string;
  strategy_id?: number;
}

function buildQuery(params: ListBetOrdersParams): string {
  const parts: string[] = [];
  if (params.page !== undefined) parts.push(`page=${params.page}`);
  if (params.page_size !== undefined) parts.push(`page_size=${params.page_size}`);
  if (params.date_from) parts.push(`date_from=${params.date_from}`);
  if (params.date_to) parts.push(`date_to=${params.date_to}`);
  if (params.strategy_id !== undefined) parts.push(`strategy_id=${params.strategy_id}`);
  return parts.length > 0 ? `?${parts.join('&')}` : '';
}

export async function listBetOrders(params: ListBetOrdersParams = {}) {
  return request<PagedData<BetOrderInfo>>(`/bet-orders${buildQuery(params)}`);
}

export async function getBetOrder(id: number) {
  return request<BetOrderInfo>(`/bet-orders/${id}`);
}
