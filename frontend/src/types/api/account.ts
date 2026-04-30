/** Account API types aligned with backend account schemas. */

import type { StrategyPermissionType } from './strategy';

export type AccountGameType = 'JND28' | 'LUCKYSB';
export type AccountPlatformType = 'JND28WEB' | 'JND282' | 'LUCKYSB';
export type AccountVerifyStatus = 'supported' | 'unsupported' | 'probe_failed' | 'unknown';
export type AccountMarketState = 'open' | 'closed' | 'waiting' | 'unknown';
export type AccountSummaryStatusReason =
  | 'not_verified'
  | 'probe_partial_failure'
  | 'unsupported_only'
  | 'probe_failed_only'
  | 'unsupported_with_probe_failed'
  | (string & {});
export type AccountFrontendSignal =
  | 'normal'
  | 'processing'
  | 'need_relogin'
  | 'need_confirm_odds';

export interface AccountCreate {
  account_name: string;
  password: string;
  game_type: AccountGameType;
  platform_url?: string;
}

export interface AccountPlatformCapability {
  platform_type: AccountPlatformType | string;
  verify_status: AccountVerifyStatus | string;
  market_state: AccountMarketState | string;
  detected_issue?: string | null;
  odds_synced?: boolean | null;
  odds_message?: string | null;
  last_verified_at?: string | null;
}

export interface AccountInfo {
  id: number;
  account_name: string;
  password_masked: string;
  game_type?: AccountGameType | string;
  allowed_strategy_platform_types?: Array<AccountPlatformType | string>;
  allowed_strategy_types?: StrategyPermissionType[];
  platform_capabilities?: AccountPlatformCapability[];
  latest_verification_run_id?: number | null;
  effective_verification_run_id?: number | null;
  verification_in_progress?: boolean;
  verification_stale?: boolean;
  summary_status_reason?: AccountSummaryStatusReason | null;
  frontend_signal?: AccountFrontendSignal;
  frontend_signal_reason?: string | null;
  // Legacy compatibility field.
  platform_type?: AccountPlatformType | string;
  platform_url?: string;
  status: string;
  balance: number;
  kill_switch: boolean;
  last_login_at: string | null;
  odds_synced?: boolean | null;
  odds_count?: number | null;
  odds_message?: string | null;
}

export interface KillSwitchUpdate {
  enabled: boolean;
}
