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
  listOperatorStrategyPermissions,
  updateAccountStrategyPermissions,
  fetchAdminDashboard,
  setGlobalKillSwitch,
  listSharedMarketGroups,
  listSharedMarketUncoveredUrls,
  ignoreSharedMarketUncoveredUrl,
  recheckSharedMarketUncoveredUrl,
  joinSharedMarketUncoveredUrlGroup,
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

describe('account strategy permissions', () => {
  it('读取操作者账号策略授权', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: [] });

    await listOperatorStrategyPermissions(7);

    expect(mockRequest).toHaveBeenCalledWith('/admin/operators/7/strategy-permissions');
  });

  it('保存账号策略授权', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: null });

    await updateAccountStrategyPermissions(7, 12, ['flat', 'martin']);

    expect(mockRequest).toHaveBeenCalledWith(
      '/admin/operators/7/accounts/12/strategy-permissions',
      {
        method: 'PUT',
        body: JSON.stringify({ strategy_types: ['flat', 'martin'] }),
      },
    );
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

describe('shared market urls', () => {
  it('加载共享组', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: [] });

    await listSharedMarketGroups();

    expect(mockRequest).toHaveBeenCalledWith('/admin/shared-market-groups');
  });

  it('加载待审核共享网址', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: { items: [], total: 0, page: 1, page_size: 20 } });

    await listSharedMarketUncoveredUrls({ page: 1, page_size: 20, status: 'pending' });

    expect(mockRequest).toHaveBeenCalledWith('/admin/shared-market-uncovered-urls?page=1&page_size=20&status=pending');
  });

  it('忽略待审核记录', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: null });
    await ignoreSharedMarketUncoveredUrl(12);
    expect(mockRequest).toHaveBeenCalledWith('/admin/shared-market-uncovered-urls/12/ignore', { method: 'POST' });
  });

  it('重新检测待审核记录', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: null });
    await recheckSharedMarketUncoveredUrl(12);
    expect(mockRequest).toHaveBeenCalledWith('/admin/shared-market-uncovered-urls/12/recheck', { method: 'POST' });
  });

  it('加入共享组', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: null });
    await joinSharedMarketUncoveredUrlGroup(12, 9);
    expect(mockRequest).toHaveBeenCalledWith('/admin/shared-market-uncovered-urls/12/join-shared-group', {
      method: 'POST',
      body: JSON.stringify({ shared_group_id: 9 }),
    });
  });
});
