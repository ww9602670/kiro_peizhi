/**
 * 回测 API 封装
 */
import { request } from './request';
import type { PagedData } from '@/types/api/common';

// ---------------------------------------------------------------------------
// 类型定义
// ---------------------------------------------------------------------------

export interface BacktestCreate {
  strategy_type: 'flat' | 'martin';
  key_codes: string[];
  base_amount: number;        // 元
  martin_sequence?: number[];
  odds_map: Record<string, number>;
  start_date: string;         // YYYY-MM-DD
  end_date: string;           // YYYY-MM-DD
}

export interface BacktestRecordSchema {
  issue: string;
  amount: number;             // 元
  pnl: number;                // 元
  cumulative_pnl: number;     // 元
  martin_level: number | null;
}

export interface BacktestResultSchema {
  total_pnl: number;
  total_bet_amount: number;
  total_issues: number;
  win_count: number;
  lose_count: number;
  win_rate: number;
  max_consecutive_loss: number;
  max_drawdown: number;
  skipped_issues: number;
  martin_level_dist: Record<number, number> | null;
  max_martin_level: number | null;
  records: BacktestRecordSchema[];
}

export interface BacktestInfo {
  id: number;
  strategy_type: string;
  key_codes: string[];
  base_amount: number;
  martin_sequence: number[] | null;
  start_issue: string;
  end_issue: string;
  status: string;
  error_message: string | null;
  total_issues: number;
  processed_issues: number;
  created_at: string;
  completed_at: string | null;
  result: BacktestResultSchema | null;
}

export interface HistoryDataStatus {
  total_count: number;
  min_issue: string | null;
  max_issue: string | null;
  min_time: string | null;
  max_time: string | null;
  gap_count: number;
  syncing: boolean;
  last_sync_time: string | null;
  last_sync_inserted: number | null;
}

export interface FillHistoryResult {
  total_inserted: number;
  days_processed: number;
  days_failed: string[];
}

// ---------------------------------------------------------------------------
// API 函数
// ---------------------------------------------------------------------------

export function createBacktestTask(data: BacktestCreate) {
  return request<BacktestInfo>('/backtest/tasks', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function listBacktestTasks(page = 1, pageSize = 20) {
  return request<PagedData<BacktestInfo>>(
    `/backtest/tasks?page=${page}&page_size=${pageSize}`,
  );
}

export function getBacktestTask(id: number) {
  return request<BacktestInfo>(`/backtest/tasks/${id}`);
}

export function getHistoryStatus() {
  return request<HistoryDataStatus>('/backtest/history-status');
}

export function fillHistory() {
  return request<FillHistoryResult>('/backtest/fill-history', {
    method: 'POST',
  });
}
