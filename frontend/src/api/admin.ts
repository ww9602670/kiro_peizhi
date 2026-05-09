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
import type {
  OperatorStrategyPermissionInfo,
  SharedMarketDataSourceState,
  SharedMarketDetectionStatus,
  SharedMarketGroupInfo,
  SharedMarketRouteInfo,
  SharedMarketUncoveredUrlInfo,
  StrategyPermissionType,
} from '@/types/api/strategy';

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

export async function listOperatorStrategyPermissions(operatorId: number) {
  return request<OperatorStrategyPermissionInfo>(`/admin/operators/${operatorId}/strategy-permissions`);
}

export async function updateOperatorStrategyPermissions(
  operatorId: number,
  strategyTypes: StrategyPermissionType[]
) {
  return request<OperatorStrategyPermissionInfo>(
    `/admin/operators/${operatorId}/strategy-permissions`,
    {
      method: 'PUT',
      body: JSON.stringify({ strategy_types: strategyTypes }),
    }
  );
}

export async function fetchAdminDashboard() {
  return request<AdminDashboard>('/admin/dashboard');
}

export async function listSharedMarketGroups(params?: { include_disabled?: boolean }) {
  const qs = params?.include_disabled ? '?include_disabled=true' : '';
  return request<SharedMarketGroupInfo[]>(`/admin/shared-market-groups${qs}`);
}

export async function listSharedMarketUncoveredUrls(params?: {
  page?: number;
  page_size?: number;
  status?: string;
  detection_status?: SharedMarketDetectionStatus | string;
}) {
  const parts: string[] = [];
  if (params?.page !== undefined) parts.push(`page=${params.page}`);
  if (params?.page_size !== undefined) parts.push(`page_size=${params.page_size}`);
  if (params?.status) parts.push(`status=${encodeURIComponent(params.status)}`);
  if (params?.detection_status) parts.push(`detection_status=${encodeURIComponent(params.detection_status)}`);
  const qs = parts.length > 0 ? `?${parts.join('&')}` : '';
  return request<PagedData<SharedMarketUncoveredUrlInfo>>(`/admin/shared-market-uncovered-urls${qs}`);
}

export async function listSharedMarketRoutes(params?: {
  page?: number;
  page_size?: number;
  state?: SharedMarketDataSourceState | string;
  operator_id?: number;
  account_id?: number;
  shared_group_id?: number;
}) {
  const parts: string[] = [];
  if (params?.page !== undefined) parts.push(`page=${params.page}`);
  if (params?.page_size !== undefined) parts.push(`page_size=${params.page_size}`);
  if (params?.state) parts.push(`state=${encodeURIComponent(params.state)}`);
  if (params?.operator_id !== undefined) parts.push(`operator_id=${params.operator_id}`);
  if (params?.account_id !== undefined) parts.push(`account_id=${params.account_id}`);
  if (params?.shared_group_id !== undefined) parts.push(`shared_group_id=${params.shared_group_id}`);
  const qs = parts.length > 0 ? `?${parts.join('&')}` : '';
  return request<PagedData<SharedMarketRouteInfo>>(`/admin/shared-market-routes${qs}`);
}

export async function ignoreSharedMarketUncoveredUrl(recordId: number) {
  return request<SharedMarketUncoveredUrlInfo>(`/admin/shared-market-uncovered-urls/${recordId}/ignore`, {
    method: 'POST',
  });
}

export async function recheckSharedMarketUncoveredUrl(recordId: number) {
  return request<SharedMarketUncoveredUrlInfo>(`/admin/shared-market-uncovered-urls/${recordId}/recheck`, {
    method: 'POST',
  });
}

export async function joinSharedMarketUncoveredUrlGroup(recordId: number, sharedGroupId: number) {
  return request<SharedMarketUncoveredUrlInfo>(`/admin/shared-market-uncovered-urls/${recordId}/join-shared-group`, {
    method: 'POST',
    body: JSON.stringify({ shared_group_id: sharedGroupId }),
  });
}
