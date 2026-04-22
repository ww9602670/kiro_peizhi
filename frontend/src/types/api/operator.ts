/** 操作者管理 API 类型（与 backend/app/schemas/operator.py 一一对应） */

export interface OperatorCreate {
  username: string;
  password: string;
  max_accounts?: number;
  expire_date?: string | null;
}

export interface OperatorUpdate {
  max_accounts?: number | null;
  expire_date?: string | null;
}

export interface OperatorInfo {
  id: number;
  username: string;
  role: string;
  status: string;
  max_accounts: number;
  expire_date: string | null;
  created_at: string;
}

export interface OperatorMeInfo {
  username: string;
  expire_date: string | null;
  max_accounts: number;
  bound_accounts: number;
  remaining_accounts: number;
}

export interface OperatorChangePasswordRequest {
  old_password: string;
  new_password: string;
}

export interface StatusUpdate {
  status: 'active' | 'disabled';
}
