/**
 * 投注记录 API 契约类型
 * 与后端 backend/app/schemas/bet_order.py 一一对应
 */

export interface BetOrderInfo {
  id: number;
  idempotent_id: string;
  strategy_id: number;
  account_id: number;
  issue: string;
  key_code: string;
  key_code_name: string;
  amount: number;       // 元
  odds: number | null;  // 小数赔率
  status: string;
  open_result: string | null;
  sum_value: number | null;
  is_win: number | null;  // 1=中奖, 0=未中, -1=退款
  pnl: number | null;     // 元
  simulation: boolean;
  martin_level: number | null;
  bet_at: string | null;
  settled_at: string | null;
  fail_reason: string | null;
  // 新增可选字段
  strategy_name?: string | null;
  account_name?: string | null;
  payout?: number | null;
}
