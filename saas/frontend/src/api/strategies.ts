/**
 * 策略管理 API 封装
 * - listStrategies: 我的策略列表
 * - createStrategy: 创建策略
 * - updateStrategy: 修改策略
 * - deleteStrategy: 删除策略
 * - startStrategy: 启动策略
 * - pauseStrategy: 暂停策略
 * - stopStrategy: 停止策略
 */

import { request } from '@/api/request';
import type { StrategyCreate, StrategyUpdate, StrategyInfo } from '@/types/api/strategy';

export async function listStrategies() {
  return request<StrategyInfo[]>('/strategies');
}

export async function createStrategy(data: StrategyCreate) {
  return request<StrategyInfo>('/strategies', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateStrategy(id: number, data: StrategyUpdate) {
  return request<StrategyInfo>(`/strategies/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function deleteStrategy(id: number, force = false) {
  const query = force ? '?force=true' : '';
  return request<null>(`/strategies/${id}${query}`, {
    method: 'DELETE',
  });
}

export async function startStrategy(id: number) {
  return request<StrategyInfo>(`/strategies/${id}/start`, {
    method: 'POST',
  });
}

export async function pauseStrategy(id: number) {
  return request<StrategyInfo>(`/strategies/${id}/pause`, {
    method: 'POST',
  });
}

export async function stopStrategy(id: number) {
  return request<StrategyInfo>(`/strategies/${id}/stop`, {
    method: 'POST',
  });
}
