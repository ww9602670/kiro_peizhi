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

const ALERT_OPERATOR_COPY: Record<string, { code: string; message: string }> = {
  login_fail: { code: 'SESSION-003', message: '账号登录失败，请联系管理员处理。' },
  captcha_fail: { code: 'SESSION-004', message: '账号验证失败，请联系管理员处理。' },
  session_lost: { code: 'SESSION-002', message: '账号重连失败，请联系管理员处理。' },
  session_reconnecting: { code: 'SESSION-001', message: '账号会话异常，系统正在重连。' },
  session_reconnect_failed: { code: 'SESSION-002', message: '账号重连失败，请联系管理员处理。' },
  bet_fail: { code: 'BET-003', message: '本期下注失败，请检查账号和平台状态。' },
  platform_limit: { code: 'BET-002', message: '当前策略过多，可能引发风控导致下注失败。' },
  shared_market_error: { code: 'SHARED-002', message: '数据更新变慢，可能影响投注，请联系管理员处理。' },
  system_api_fail: { code: 'SYSTEM-001', message: '系统接口异常率升高，请联系管理员处理。' },
  consecutive_fail: { code: 'SYSTEM-002', message: '账号连续下注失败，请联系管理员处理。' },
  settlement_data_missing: { code: 'SETTLE-002', message: '结算数据暂未返回，系统将继续补偿。' },
  settle_timeout: { code: 'SETTLE-002', message: '结算数据暂未返回，系统将继续补偿。' },
  unsettled_orders: { code: 'SETTLE-001', message: '订单结算中，请稍后查看。' },
  settle_api_failed: { code: 'SETTLE-003', message: '结算失败，请联系管理员处理。' },
  api_call_failed: { code: 'SETTLE-003', message: '结算失败，请联系管理员处理。' },
  settle_data_expired: { code: 'SETTLE-003', message: '结算失败，请联系管理员处理。' },
  worker_lock_conflict: { code: 'WORKER-001', message: '任务执行冲突，系统已自动保护。' },
  worker_lock_lost: { code: 'WORKER-002', message: '任务执行锁异常，请联系管理员处理。' },
};

function normalizeAlertForOperator(alert: AlertInfo): AlertInfo {
  const copy = ALERT_OPERATOR_COPY[alert.type];
  if (!copy) {
    return alert;
  }
  return {
    ...alert,
    title: `${copy.message}日志编号：${copy.code}。`,
    detail: null,
  };
}

function buildQuery(params: ListAlertsParams): string {
  const parts: string[] = [];
  if (params.is_read !== undefined) parts.push(`is_read=${params.is_read}`);
  if (params.page !== undefined) parts.push(`page=${params.page}`);
  if (params.page_size !== undefined) parts.push(`page_size=${params.page_size}`);
  return parts.length > 0 ? `?${parts.join('&')}` : '';
}

export async function listAlerts(params: ListAlertsParams = {}) {
  const response = await request<PagedData<AlertInfo>>(`/alerts${buildQuery(params)}`);
  if (!response.data?.items) {
    return response;
  }
  return {
    ...response,
    data: {
      ...response.data,
      items: response.data.items.map(normalizeAlertForOperator),
    },
  };
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
