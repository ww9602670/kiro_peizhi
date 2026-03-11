/** 策略管理 API 类型（与 backend/app/schemas/strategy.py 一一对应） */

export interface StrategyCreate {
  account_id: number;
  name: string;
  type: 'flat' | 'martin';
  play_code: string;
  base_amount: number; // 元
  martin_sequence: number[] | null;
  bet_timing: number;
  simulation: boolean;
  stop_loss: number | null;
  take_profit: number | null;
}

export interface StrategyUpdate {
  name?: string;
  base_amount?: number;
  martin_sequence?: number[] | null;
  bet_timing?: number;
  simulation?: boolean;
  stop_loss?: number | null;
  take_profit?: number | null;
}

export interface StrategyInfo {
  id: number;
  account_id: number;
  name: string;
  type: string;
  play_code: string;
  base_amount: number; // 元
  martin_sequence: number[] | null;
  bet_timing: number;
  simulation: boolean;
  status: string;
  martin_level: number;
  stop_loss: number | null; // 元
  take_profit: number | null; // 元
  daily_pnl: number; // 元
  total_pnl: number; // 元
}
