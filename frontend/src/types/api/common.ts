/** 通用 API 类型（与 backend/app/schemas/common.py 一一对应） */

export interface ApiResponse<T> {
  code: number;
  message: string;
  data: T | null;
}

export interface PagedData<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
