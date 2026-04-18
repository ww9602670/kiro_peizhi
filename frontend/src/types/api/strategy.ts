/** Strategy API types (aligned with backend strategy schema). */

export type StrategyPlatformType = 'JND28WEB' | 'JND282' | 'LUCKYSB';

export interface StrategyCreate {
  account_id: number;
  name: string;
  type: 'flat' | 'martin' | 'red_wave_double_martin';
  play_code: string;
  base_amount: number;
  martin_sequence: number[] | null;
  bet_timing: number;
  simulation: boolean;
  stop_loss: number | null;
  take_profit: number | null;
  platform_type?: StrategyPlatformType | string;
  gate_window_issues?: number | null;
}

export interface StrategyUpdate {
  name?: string;
  base_amount?: number;
  play_code?: string;
  martin_sequence?: number[] | null;
  bet_timing?: number;
  simulation?: boolean;
  stop_loss?: number | null;
  take_profit?: number | null;
  platform_type?: StrategyPlatformType | string;
  gate_window_issues?: number | null;
}

export interface StrategyInfo {
  id: number;
  account_id: number;
  name: string;
  type: string;
  play_code: string;
  play_code_name?: string;
  base_amount: number;
  martin_sequence: number[] | null;
  bet_timing: number;
  simulation: boolean;
  status: string;
  martin_level: number;
  stop_loss: number | null;
  take_profit: number | null;
  daily_pnl: number;
  total_pnl: number;
  account_name?: string;
  platform_type?: StrategyPlatformType | string;
  gate_window_issues?: number | null;
}
