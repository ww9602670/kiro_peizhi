import type { AlertInfo } from './alert';
import type { BetOrderInfo } from './bet-order';
import type { StrategyInfo } from './strategy';

export interface RecentLotteryResult {
  id: number;
  issue: string;
  open_result: string;
  sum_value: number;
  open_time: string | null;
  created_at: string;
}

export interface OperatorDashboard {
  balance: number;
  daily_pnl: number;
  total_pnl: number;
  countdown_platform_type: string;
  running_strategies: StrategyInfo[];
  pending_bets: BetOrderInfo[];
  unread_alerts: number;
  recent_results: RecentLotteryResult[];
  recent_alerts: AlertInfo[];
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
