/**
 * Lottery API types
 */

/**
 * Lottery state enum
 */
export const LotteryStateEnum = {
  UNKNOWN: 0,  // Unknown state
  OPEN: 1,     // Open for betting
  CLOSED: 2,   // Closed for betting
  DRAWING: 3,  // Drawing in progress
} as const;

export type LotteryStateEnum =
  (typeof LotteryStateEnum)[keyof typeof LotteryStateEnum];

export const MarketDataStateEnum = {
  SHARED_OK: 'shared_ok',
  SHARED_ERROR: 'shared_error',
  SHARED_STALE: 'shared_stale',
  MARKET_CLOSED: 'market_closed',
  SHARED_HIT: 'shared_hit',
  LOCAL_FALLBACK: 'local_fallback',
  PROCESSING: 'processing',
} as const;

export type MarketDataState =
  (typeof MarketDataStateEnum)[keyof typeof MarketDataStateEnum];

export const DrawStateEnum = {
  NORMAL: 'normal',
  DRAW_PENDING: 'draw_pending',
  DRAW_WAIT_RETRY: 'draw_wait_retry',
} as const;

export type DrawState = (typeof DrawStateEnum)[keyof typeof DrawStateEnum];

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
  market_data_state?: MarketDataState; // Public market data source state
  draw_state?: DrawState;         // Draw refresh stage state
  next_normal_refresh_at?: string; // Next normal refresh time from backend
  next_draw_retry_at?: string;    // Next draw refresh time from backend
  snapshot_version?: number;      // Snapshot version
  message_code?: string;          // Message code for UI
  message_text?: string;          // Message text for UI
}

/**
 * Compatibility payload shape from backend slices.
 */
export interface CurrentInstallWire extends Omit<
  Partial<CurrentInstall>,
  | 'market_data_state'
  | 'draw_state'
  | 'next_normal_refresh_at'
  | 'next_draw_retry_at'
  | 'snapshot_version'
  | 'message_code'
  | 'message_text'
> {
  issue?: string | number | null;
  State?: unknown;
  close_timestamp?: unknown;
  open_timestamp?: unknown;
  pre_issue?: string | number | null;
  pre_result?: string | null;
  templateCode?: string | null;
  market_data_state?: unknown;
  shared_market_state?: unknown;
  draw_state?: unknown;
  next_normal_refresh_at?: unknown;
  nextNormalRefreshAt?: unknown;
  next_draw_retry_at?: unknown;
  nextDrawRetryAt?: unknown;
  snapshot_version?: unknown;
  snapshotVersion?: unknown;
  message_code?: unknown;
  messageCode?: unknown;
  message_text?: unknown;
  messageText?: unknown;
}

export const DEFAULT_CURRENT_INSTALL: CurrentInstall = {
  installments: '',
  state: LotteryStateEnum.UNKNOWN,
  close_countdown_sec: 0,
  open_countdown_sec: 0,
  pre_lottery_result: '',
  pre_installments: '',
  template_code: '',
};

function toText(value: unknown): string {
  return value == null ? '' : String(value);
}

function toNonNegativeInt(value: unknown): number {
  const parsed = Number.parseInt(String(value ?? ''), 10);
  if (!Number.isFinite(parsed) || Number.isNaN(parsed) || parsed < 0) return 0;
  return parsed;
}

function firstPresent(source: CurrentInstallWire, keys: Array<keyof CurrentInstallWire>): unknown {
  for (const key of keys) {
    const value = source[key];
    if (value !== undefined && value !== null) return value;
  }
  return undefined;
}

export function normalizeLotteryState(value: unknown): LotteryStateEnum {
  const parsed = toNonNegativeInt(value);
  if (parsed === LotteryStateEnum.OPEN) return LotteryStateEnum.OPEN;
  if (parsed === LotteryStateEnum.CLOSED) return LotteryStateEnum.CLOSED;
  if (parsed === LotteryStateEnum.DRAWING) return LotteryStateEnum.DRAWING;
  return LotteryStateEnum.UNKNOWN;
}

export function normalizeMarketDataState(value: unknown): MarketDataState {
  const normalized = toText(value).trim().toLowerCase();
  if (normalized === MarketDataStateEnum.SHARED_OK) return MarketDataStateEnum.SHARED_OK;
  if (normalized === MarketDataStateEnum.SHARED_ERROR) return MarketDataStateEnum.SHARED_ERROR;
  if (normalized === MarketDataStateEnum.SHARED_STALE) return MarketDataStateEnum.SHARED_STALE;
  if (normalized === MarketDataStateEnum.MARKET_CLOSED) return MarketDataStateEnum.MARKET_CLOSED;
  if (normalized === MarketDataStateEnum.SHARED_HIT) return MarketDataStateEnum.SHARED_OK;
  if (normalized === MarketDataStateEnum.LOCAL_FALLBACK) return MarketDataStateEnum.SHARED_ERROR;
  if (normalized === MarketDataStateEnum.PROCESSING) return MarketDataStateEnum.SHARED_OK;
  return MarketDataStateEnum.SHARED_OK;
}

export function normalizeDrawState(value: unknown): DrawState {
  const normalized = toText(value).trim().toLowerCase();
  if (normalized === DrawStateEnum.NORMAL) return DrawStateEnum.NORMAL;
  if (normalized === DrawStateEnum.DRAW_PENDING) return DrawStateEnum.DRAW_PENDING;
  if (normalized === DrawStateEnum.DRAW_WAIT_RETRY) return DrawStateEnum.DRAW_WAIT_RETRY;
  if (normalized === 'processing') return DrawStateEnum.DRAW_PENDING;
  return DrawStateEnum.NORMAL;
}

export function normalizeCurrentInstall(value: CurrentInstallWire | null | undefined): CurrentInstall {
  if (!value || typeof value !== 'object') {
    return { ...DEFAULT_CURRENT_INSTALL };
  }

  const normalized: CurrentInstall = {
    installments: toText(firstPresent(value, ['installments', 'issue'])),
    state: normalizeLotteryState(firstPresent(value, ['state', 'State'])),
    close_countdown_sec: toNonNegativeInt(
      firstPresent(value, ['close_countdown_sec', 'close_timestamp']),
    ),
    open_countdown_sec: toNonNegativeInt(
      firstPresent(value, ['open_countdown_sec', 'open_timestamp']),
    ),
    pre_lottery_result: toText(
      firstPresent(value, ['pre_lottery_result', 'pre_result']),
    ),
    pre_installments: toText(
      firstPresent(value, ['pre_installments', 'pre_issue']),
    ),
    template_code: toText(firstPresent(value, ['template_code', 'templateCode'])),
  };

  const marketDataState = firstPresent(value, ['market_data_state', 'shared_market_state']);
  if (marketDataState !== undefined && marketDataState !== null) {
    normalized.market_data_state = normalizeMarketDataState(marketDataState);
  }

  const drawState = firstPresent(value, ['draw_state']);
  if (drawState !== undefined && drawState !== null) {
    normalized.draw_state = normalizeDrawState(drawState);
  }

  const nextNormalRefreshAt = firstPresent(value, ['next_normal_refresh_at', 'nextNormalRefreshAt']);
  if (nextNormalRefreshAt !== undefined && nextNormalRefreshAt !== null) {
    normalized.next_normal_refresh_at = toText(nextNormalRefreshAt).trim();
  }

  const nextDrawRetryAt = firstPresent(value, ['next_draw_retry_at', 'nextDrawRetryAt']);
  if (nextDrawRetryAt !== undefined && nextDrawRetryAt !== null) {
    normalized.next_draw_retry_at = toText(nextDrawRetryAt).trim();
  }

  const snapshotVersion = firstPresent(value, ['snapshot_version', 'snapshotVersion']);
  if (snapshotVersion !== undefined && snapshotVersion !== null) {
    normalized.snapshot_version = toNonNegativeInt(snapshotVersion);
  }

  const messageCode = firstPresent(value, ['message_code', 'messageCode']);
  if (messageCode !== undefined && messageCode !== null) {
    normalized.message_code = toText(messageCode).trim();
  }

  const messageText = firstPresent(value, ['message_text', 'messageText']);
  if (messageText !== undefined && messageText !== null) {
    normalized.message_text = toText(messageText).trim();
  }

  return normalized;
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
