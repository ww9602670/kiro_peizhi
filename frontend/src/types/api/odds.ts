/** 单条赔率记录，对应 backend/app/schemas/odds.py OddsItem */
export interface OddsItem {
  key_code: string;
  odds_value: number;
  confirmed: boolean;
  fetched_at: string;
  confirmed_at: string | null;
}

/** 赔率列表响应，对应 OddsListResponse */
export interface OddsListResponse {
  account_id: number;
  items: OddsItem[];
  has_unconfirmed: boolean;
}

/** 赔率确认响应，对应 OddsConfirmResponse */
export interface OddsConfirmResponse {
  confirmed_count: number;
}

/** 期号信息，对应 PeriodInfo */
export interface PeriodInfo {
  issue: string;
  state: number;
  state_label: string;
  close_countdown_sec: number;
  open_countdown_sec: number;
  pre_issue: string;
  pre_result: string;
}

/** 赔率刷新响应，对应 OddsRefreshResponse */
export interface OddsRefreshResponse {
  account_id: number;
  period: PeriodInfo | null;
  odds_count: number;
  synced: boolean;
  message: string;
}
