/**
 * 投注记录 API 封装
 * - listBetOrders: 投注记录列表（分页 + 筛选）
 * - getBetOrder: 投注详情
 */

import { request } from '@/api/request';
import type { BetOrderInfo } from '@/types/api/bet-order';

export interface ListBetOrdersParams {
  page?: number;
  page_size?: number;
  date_from?: string;
  date_to?: string;
  strategy_id?: number;
  status?: string;
  account_id?: number;
}

function buildQuery(params: ListBetOrdersParams): string {
  const parts: string[] = [];
  if (params.page !== undefined) parts.push(`page=${params.page}`);
  if (params.page_size !== undefined) parts.push(`page_size=${params.page_size}`);
  if (params.date_from) parts.push(`date_from=${params.date_from}`);
  if (params.date_to) parts.push(`date_to=${params.date_to}`);
  if (params.strategy_id !== undefined) parts.push(`strategy_id=${params.strategy_id}`);
  if (params.status) parts.push(`status=${params.status}`);
  if (params.account_id !== undefined) parts.push(`account_id=${params.account_id}`);
  return parts.length > 0 ? `?${parts.join('&')}` : '';
}

export interface BetOrdersResponse {
  paged: {
    items: BetOrderInfo[];
    total: number;
    page: number;
    page_size: number;
  };
  summary: {
    total_amount: number;
    total_payout: number;
  };
}

export async function listBetOrders(params: ListBetOrdersParams = {}) {
  return request<BetOrdersResponse>(`/bet-orders${buildQuery(params)}`);
}

export async function getBetOrder(id: number) {
  return request<BetOrderInfo>(`/bet-orders/${id}`);
}
