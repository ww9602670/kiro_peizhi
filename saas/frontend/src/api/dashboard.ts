/**
 * 仪表盘 API 封装
 * - fetchDashboard: 操作者仪表盘
 * - fetchAdminDashboard: 管理员仪表盘
 */

import { request } from '@/api/request';
import type { OperatorDashboard, AdminDashboard } from '@/types/api/dashboard';
import type { BetOrderInfo } from '@/types/api/bet-order';

export async function fetchDashboard() {
  return request<OperatorDashboard>('/dashboard');
}

export async function fetchRecentBets() {
  return request<BetOrderInfo[]>('/dashboard/recent-bets');
}

export async function fetchAdminDashboard() {
  return request<AdminDashboard>('/admin/dashboard');
}
