/**
 * 仪表盘 API 契约类型
 * 与后端 backend/app/schemas/dashboard.py 一一对应
 */

import type { BetOrderInfo } from './bet-order';
import type { StrategyInfo } from './strategy';

export interface OperatorDashboard {
  balance: number;          // 元
  daily_pnl: number;        // 元
  total_pnl: number;        // 元
  running_strategies: StrategyInfo[];
  pending_bets: BetOrderInfo[];
  unread_alerts: number;
}

export interface OperatorSummary {
  id: number;
  username: string;
  status: string;
  daily_pnl: number;
  total_pnl: number;
  running_strategies: number;
}

export interface AdminDashboard {
  total_operators: number;
  active_operators: number;
  operator_summaries: OperatorSummary[];
}
