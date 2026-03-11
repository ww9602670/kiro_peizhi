/** 博彩账号 API 类型（与 backend/app/schemas/account.py 一一对应） */

export interface AccountCreate {
  account_name: string;
  password: string;
  platform_type: 'JND28WEB' | 'JND282';
}

export interface AccountInfo {
  id: number;
  account_name: string;
  password_masked: string; // 前2位+****
  platform_type: string;
  status: string;
  balance: number; // API 层返回元
  kill_switch: boolean;
  last_login_at: string | null;
}

export interface KillSwitchUpdate {
  enabled: boolean;
}
