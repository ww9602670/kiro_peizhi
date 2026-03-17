/**
 * 告警 API 封装
 * - listAlerts: 告警列表（分页，可选 is_read 过滤）
 * - markAlertRead: 标记单条已读
 * - markAllAlertsRead: 全部已读
 * - getUnreadCount: 未读数量
 */

import { request } from '@/api/request';
import type { AlertInfo } from '@/types/api/alert';
import type { PagedData } from '@/types/api/common';

export interface ListAlertsParams {
  is_read?: 0 | 1;
  page?: number;
  page_size?: number;
}

function buildQuery(params: ListAlertsParams): string {
  const parts: string[] = [];
  if (params.is_read !== undefined) parts.push(`is_read=${params.is_read}`);
  if (params.page !== undefined) parts.push(`page=${params.page}`);
  if (params.page_size !== undefined) parts.push(`page_size=${params.page_size}`);
  return parts.length > 0 ? `?${parts.join('&')}` : '';
}

export async function listAlerts(params: ListAlertsParams = {}) {
  return request<PagedData<AlertInfo>>(`/alerts${buildQuery(params)}`);
}

export async function markAlertRead(alertId: number) {
  return request<null>(`/alerts/${alertId}/read`, { method: 'PUT' });
}

export async function markAllAlertsRead() {
  return request<{ marked_count: number }>('/alerts/read-all', { method: 'PUT' });
}

export async function getUnreadCount() {
  return request<{ count: number }>('/alerts/unread-count');
}
