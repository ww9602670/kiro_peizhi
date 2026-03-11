/**
 * 管理员 API 封装
 * - setGlobalKillSwitch: 全局熔断开关
 * - listOperators: 操作者列表
 * - createOperator: 创建操作者
 * - updateOperator: 修改操作者
 * - updateOperatorStatus: 禁用/启用操作者
 * - fetchAdminDashboard: 管理员仪表盘
 */

import { request } from '@/api/request';
import type { GlobalKillSwitchInfo, GlobalKillSwitchRequest } from '@/types/api/kill-switch';
import type { OperatorCreate, OperatorInfo, OperatorUpdate, StatusUpdate } from '@/types/api/operator';
import type { AdminDashboard } from '@/types/api/dashboard';
import type { PagedData } from '@/types/api/common';

export async function setGlobalKillSwitch(data: GlobalKillSwitchRequest) {
  return request<GlobalKillSwitchInfo>('/admin/kill-switch', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function listOperators(params?: { page?: number; page_size?: number }) {
  const parts: string[] = [];
  if (params?.page !== undefined) parts.push(`page=${params.page}`);
  if (params?.page_size !== undefined) parts.push(`page_size=${params.page_size}`);
  const qs = parts.length > 0 ? `?${parts.join('&')}` : '';
  return request<PagedData<OperatorInfo>>(`/admin/operators${qs}`);
}

export async function createOperator(data: OperatorCreate) {
  return request<OperatorInfo>('/admin/operators', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateOperator(id: number, data: OperatorUpdate) {
  return request<OperatorInfo>(`/admin/operators/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function updateOperatorStatus(id: number, data: StatusUpdate) {
  return request<OperatorInfo>(`/admin/operators/${id}/status`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function fetchAdminDashboard() {
  return request<AdminDashboard>('/admin/dashboard');
}
