/**
 * 告警 API 契约类型
 * 与后端 backend/app/schemas/alert.py 一一对应
 */

export interface AlertInfo {
  id: number;
  operator_id: number;
  type: string;
  level: string;
  title: string;
  detail: string | null;
  is_read: number;
  created_at: string;
}
