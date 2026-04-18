/** 博彩账号 API 类型（与 backend/app/schemas/account.py 一一对应） */

export type AccountGameType = 'JND28' | 'LUCKYSB';
export type AccountPlatformType = 'JND28WEB' | 'JND282' | 'LUCKYSB';

export interface AccountCreate {
  account_name: string;
  password: string;
  game_type: AccountGameType;
  platform_url?: string;
}

export interface AccountInfo {
  id: number;
  account_name: string;
  password_masked: string; // 前2位+****
  game_type?: AccountGameType | string;
  allowed_strategy_platform_types?: Array<AccountPlatformType | string>;
  platform_type?: AccountPlatformType | string;
  platform_url?: string;
  status: string;
  balance: number; // API 层返回元
  kill_switch: boolean;
  last_login_at: string | null;
  // Structured odds sync status (if backend provides it).
  odds_synced?: boolean | null;
  odds_count?: number | null;
  odds_message?: string | null;
}

export interface KillSwitchUpdate {
  enabled: boolean;
}
