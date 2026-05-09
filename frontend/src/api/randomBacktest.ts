/**
 * 随机马丁回测 API 封装
 */
import { request } from './request';
import type { PagedData } from '@/types/api/common';

// ---------------------------------------------------------------------------
// 类型定义
// ---------------------------------------------------------------------------

export interface PeriodBetSchema {
  period_index: number;
  ball: number;
  numbers: number[];
  mask: number;
}

export interface PlanSchema {
  group_id: number;
  periods: PeriodBetSchema[];
}

export interface GeneratePlanRequest {
  num_groups: number;
  N: number;
  K: number;
}

export interface GeneratePlanResponse {
  plan_set_id: number;
  plans: PlanSchema[];
  num_groups: number;
  periods_per_group: number;
  numbers_per_period: number;
}

export interface RandomBacktestConfig {
  fund_mode: 'shared_total' | 'per_group';
  initial_total_balance: number;
  initial_balance_per_group: number;
  base_unit: number;
  martin_multiplier: number;
  odds: number;
  chase_limit: number;
}

export interface RandomBacktestCreate {
  config: RandomBacktestConfig;
  plans: PlanSchema[];
  plan_set_id?: number | null;
  start_date: string;
  end_date: string;
  kline_window?: number;
}

export interface GroupResultSchema {
  group_id: number;
  abandoned: boolean;
  abandoned_issue: string;
  final_balance: number;
  total_pnl: number;
  win_count: number;
  lose_count: number;
  win_rate: number;
  max_consecutive_loss: number;
  max_drawdown: number;
}

export interface KlineBarSchema {
  index: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface AggregateResultSchema {
  total_equity_curve: number[];
  abandoned_amount_curve: number[];
  total_max_drawdown: number;
  total_win_count: number;
  total_lose_count: number;
  total_win_rate: number;
  group_results: GroupResultSchema[];
  kline_data: KlineBarSchema[];
  issues: string[];
  processed_issues: number;
}

export interface BacktestTaskInfo {
  id: number;
  status: string;
  total_issues: number;
  processed_issues: number;
  result: AggregateResultSchema | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

// ---------------------------------------------------------------------------
// API 函数
// ---------------------------------------------------------------------------

export function generateAndSavePlan(req: GeneratePlanRequest) {
  return request<GeneratePlanResponse>('/random-backtest/generate-plan', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export function createRandomBacktestTask(data: RandomBacktestCreate) {
  return request<BacktestTaskInfo>('/random-backtest/tasks', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function getRandomBacktestTask(id: number) {
  return request<BacktestTaskInfo>(`/random-backtest/tasks/${id}`);
}

export function cancelRandomBacktestTask(id: number) {
  return request<unknown>(`/random-backtest/tasks/${id}/cancel`, {
    method: 'POST',
  });
}

export function listRandomBacktestTasks(page = 1, pageSize = 20) {
  return request<PagedData<BacktestTaskInfo>>(
    `/random-backtest/tasks?page=${page}&page_size=${pageSize}`,
  );
}
