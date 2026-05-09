/** Strategy API types (aligned with backend strategy schema). */

export type StrategyPlatformType = 'JND28WEB' | 'JND282' | 'LUCKYSB';
export type OmissionRandomCategory = 'ball1' | 'ball2' | 'ball3' | 'sum';
export type OmissionRandomPickCount = 3 | 4 | 5 | 6;

export interface OmissionRandomStrategyConfig {
  pick_count: OmissionRandomPickCount;
  categories: OmissionRandomCategory[];
  weight_mode?: 'omission_plus_random' | 'pure_random' | string;
  runtime_state?: Record<string, unknown>;
}

export type StrategyType =
  | 'flat'
  | 'martin'
  | 'red_wave_double_martin'
  | 'green_wave_single_martin'
  | 'omission_random_flat'
  | 'omission_random_martin'
  | 'ai_random_flat'
  | 'ai_random_martin'
  | 'ai_same_random_flat'
  | 'ai_same_random_martin';

export type StrategyPermissionType =
  | StrategyType
  | 'dw3_flat'
  | 'dw3_martin';

export interface StrategyCreate {
  account_id: number;
  name: string;
  type: StrategyType;
  play_code: string;
  base_amount: number;
  martin_sequence: number[] | null;
  strategy_config?: OmissionRandomStrategyConfig | null;
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
  strategy_config?: OmissionRandomStrategyConfig | null;
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
  strategy_config?: OmissionRandomStrategyConfig | null;
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

export interface OperatorStrategyPermissionInfo {
  operator_id: number;
  username: string;
  allowed_strategy_types: StrategyPermissionType[];
}

export type SharedMarketDataSourceState =
  | 'local'
  | 'shared_pending'
  | 'shared'
  | 'shared_error_local_fallback';

export type SharedMarketDetectionStatus =
  | 'untested'
  | 'detecting'
  | 'success'
  | 'failed'
  | 'ignored';

export type SharedMarketCollectorHealthState =
  | 'warming'
  | 'ok'
  | 'degraded'
  | 'failed'
  | 'market_closed';

export interface SharedMarketGroupInfo {
  id: number;
  group_key: string;
  enabled: boolean | number;
  collector_platform_type?: string | null;
  collector_account_name?: string | null;
  primary_url?: string | null;
  collector_owner_key?: string | null;
  collector_health_state?: SharedMarketCollectorHealthState | string | null;
  collector_last_success_at?: string | null;
  collector_last_error_at?: string | null;
  collector_last_error_class?: string | null;
  collector_last_error?: string | null;
  collector_consecutive_error_count?: number | null;
  collector_preheated_at?: string | null;
  source_status?: string | null;
  last_error?: string | null;
  snapshot_issue?: string | null;
  snapshot_pre_issue?: string | null;
  snapshot_open_result?: string | null;
  snapshot_fetched_at?: string | null;
  snapshot_updated_at?: string | null;
  provider_owner_key?: string | null;
  provider_kind?: string | null;
}

export interface SharedMarketUncoveredUrlInfo {
  id: number;
  normalized_url: string;
  first_seen_at: string;
  last_seen_at: string;
  hit_count: number;
  detection_status: SharedMarketDetectionStatus | string;
  detection_attempts?: number | null;
  next_detect_at?: string | null;
  last_checked_at?: string | null;
  last_account_id: number | null;
  last_platform_type: string | null;
  sample_raw_url: string | null;
  status: string;
  failure_reason: string | null;
  detection_error?: string | null;
  shared_group_id: number | null;
  shared_group_key: string | null;
  matched_shared_group_id?: number | null;
}

export interface SharedMarketRouteInfo {
  account_id: number;
  operator_id: number | null;
  operator_name: string | null;
  account_name: string;
  platform_type: string;
  normalized_url: string | null;
  data_source_state: SharedMarketDataSourceState | string;
  shared_group_id: number | null;
  shared_group_key: string | null;
  pending_shared_group_id: number | null;
  handoff_after_issue: string | null;
  handoff_confirmed_issue: string | null;
  fallback_reason: string | null;
  last_switch_at: string | null;
  updated_at: string | null;
}
