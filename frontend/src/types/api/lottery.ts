/**
 * Lottery API types
 */

/**
 * Lottery state enum
 */
export enum LotteryStateEnum {
  UNKNOWN = 0,  // Unknown state
  OPEN = 1,     // Open for betting
  CLOSED = 2,   // Closed for betting
  DRAWING = 3   // Drawing in progress
}

/**
 * Current install information
 */
export interface CurrentInstall {
  installments: string;           // Current issue number
  state: LotteryStateEnum;        // State: 1=open, 2=closed, 3=drawing, 0=unknown
  close_countdown_sec: number;    // Seconds until close
  open_countdown_sec: number;     // Seconds until draw
  pre_lottery_result: string;     // Previous draw result
  pre_installments: string;       // Previous issue number
  template_code: string;          // Template code
}

/**
 * State display configuration
 */
export interface LotteryStateDisplay {
  label: string;    // Display text
  color: string;    // Color: green/red/yellow/gray
}

/**
 * State display map
 */
export const STATE_DISPLAY_MAP: Record<LotteryStateEnum, LotteryStateDisplay> = {
  [LotteryStateEnum.OPEN]: { label: '开盘中', color: 'green' },
  [LotteryStateEnum.CLOSED]: { label: '封盘中', color: 'red' },
  [LotteryStateEnum.DRAWING]: { label: '开奖中', color: 'yellow' },
  [LotteryStateEnum.UNKNOWN]: { label: '未知', color: 'gray' },
};
