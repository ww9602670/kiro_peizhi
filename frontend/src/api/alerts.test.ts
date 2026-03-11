import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  listAlerts,
  markAlertRead,
  markAllAlertsRead,
  getUnreadCount,
} from './alerts';

vi.mock('@/api/request', () => ({
  request: vi.fn(),
}));

import { request } from '@/api/request';
const mockRequest = vi.mocked(request);

beforeEach(() => {
  mockRequest.mockReset();
});

const sampleAlert = {
  id: 1,
  operator_id: 2,
  type: 'login_fail',
  level: 'critical',
  title: '博彩账号登录失败',
  detail: '{"reason": "密码错误"}',
  is_read: 0,
  created_at: '2025-01-01 12:00:00',
};

describe('listAlerts', () => {
  it('无参数时调用 /alerts', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: { items: [], total: 0, page: 1, page_size: 20 },
    });

    await listAlerts();
    expect(mockRequest).toHaveBeenCalledWith('/alerts');
  });

  it('带 is_read 过滤参数', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: { items: [sampleAlert], total: 1, page: 1, page_size: 20 },
    });

    const res = await listAlerts({ is_read: 0, page: 1, page_size: 20 });
    expect(mockRequest).toHaveBeenCalledWith('/alerts?is_read=0&page=1&page_size=20');
    expect(res.data!.items).toHaveLength(1);
  });

  it('仅传 page 参数', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: { items: [], total: 0, page: 2, page_size: 20 },
    });

    await listAlerts({ page: 2 });
    expect(mockRequest).toHaveBeenCalledWith('/alerts?page=2');
  });
});

describe('markAlertRead', () => {
  it('调用正确路径和方法', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: null });

    await markAlertRead(42);
    expect(mockRequest).toHaveBeenCalledWith('/alerts/42/read', { method: 'PUT' });
  });
});

describe('markAllAlertsRead', () => {
  it('调用正确路径和方法', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: { marked_count: 5 },
    });

    const res = await markAllAlertsRead();
    expect(mockRequest).toHaveBeenCalledWith('/alerts/read-all', { method: 'PUT' });
    expect(res.data!.marked_count).toBe(5);
  });
});

describe('getUnreadCount', () => {
  it('调用正确路径并返回数量', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: { count: 3 },
    });

    const res = await getUnreadCount();
    expect(mockRequest).toHaveBeenCalledWith('/alerts/unread-count');
    expect(res.data!.count).toBe(3);
  });
});
