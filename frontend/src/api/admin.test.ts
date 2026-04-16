/**
 * 管理员 API 封装测试
 * - listOperators 路径和分页参数
 * - createOperator 请求体
 * - updateOperator 请求体
 * - updateOperatorStatus 请求体
 * - fetchAdminDashboard 路径
 * - setGlobalKillSwitch 请求体
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  listOperators,
  createOperator,
  updateOperator,
  updateOperatorStatus,
  fetchAdminDashboard,
  setGlobalKillSwitch,
} from './admin';

vi.mock('@/api/request', () => ({
  request: vi.fn(),
}));

import { request } from '@/api/request';
const mockRequest = vi.mocked(request);

beforeEach(() => {
  mockRequest.mockReset();
});

describe('listOperators', () => {
  it('无参数时调用 /admin/operators', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0, message: 'success',
      data: { items: [], total: 0, page: 1, page_size: 20 },
    });
    await listOperators();
    expect(mockRequest).toHaveBeenCalledWith('/admin/operators');
  });

  it('带分页参数', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0, message: 'success',
      data: { items: [], total: 0, page: 2, page_size: 10 },
    });
    await listOperators({ page: 2, page_size: 10 });
    expect(mockRequest).toHaveBeenCalledWith('/admin/operators?page=2&page_size=10');
  });
});

describe('createOperator', () => {
  it('调用正确路径、方法和请求体', async () => {
    const payload = { username: 'newop', password: 'pass123456', max_accounts: 3, expire_date: '2026-12-31' };
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: { id: 1, ...payload } });

    await createOperator(payload);

    expect(mockRequest).toHaveBeenCalledWith('/admin/operators', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  });

  it('expire_date 为 null 时正确传递', async () => {
    const payload = { username: 'op', password: 'pass123456', expire_date: null };
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: null });

    await createOperator(payload);

    const body = JSON.parse(mockRequest.mock.calls[0][1]!.body as string);
    expect(body.expire_date).toBeNull();
  });
});

describe('updateOperator', () => {
  it('调用正确路径和方法', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: null });

    await updateOperator(5, { max_accounts: 10 });

    expect(mockRequest).toHaveBeenCalledWith('/admin/operators/5', {
      method: 'PUT',
      body: JSON.stringify({ max_accounts: 10 }),
    });
  });
});

describe('updateOperatorStatus', () => {
  it('禁用操作者', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: null });

    await updateOperatorStatus(3, { status: 'disabled' });

    expect(mockRequest).toHaveBeenCalledWith('/admin/operators/3/status', {
      method: 'PUT',
      body: JSON.stringify({ status: 'disabled' }),
    });
  });

  it('启用操作者', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: null });

    await updateOperatorStatus(3, { status: 'active' });

    const body = JSON.parse(mockRequest.mock.calls[0][1]!.body as string);
    expect(body.status).toBe('active');
  });
});

describe('fetchAdminDashboard', () => {
  it('调用 /admin/dashboard', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: {} });
    await fetchAdminDashboard();
    expect(mockRequest).toHaveBeenCalledWith('/admin/dashboard');
  });
});

describe('setGlobalKillSwitch', () => {
  it('启用全局熔断', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: { enabled: true } });

    await setGlobalKillSwitch({ enabled: true });

    expect(mockRequest).toHaveBeenCalledWith('/admin/kill-switch', {
      method: 'POST',
      body: JSON.stringify({ enabled: true }),
    });
  });
});
