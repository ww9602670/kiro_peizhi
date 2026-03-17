/** 认证 API 类型（与 backend/app/schemas/auth.py 一一对应） */

export interface LoginRequest {
  username: string;
  password: string;
}

export interface TokenResponse {
  token: string;
  expire_at: string; // ISO 8601
}
