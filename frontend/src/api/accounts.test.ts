import { describe, it, expect, vi, beforeEach } from 'vitest';
import { listAccounts, createAccount, deleteAccount, loginAccount, updateKillSwitch } from './accounts';

// Mock request module
vi.mock('@/api/request', () => ({
  request: vi.fn(),
}));

import { request } from '@/api/request';
const mockRequest = vi.mocked(request);

beforeEach(() => {
  mockRequest.mockReset();
  localStorage.clear();
});

describe('listAccounts', () => {
  it('调用正确路径和方法', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: [] });

    const res = await listAccounts();

    expect(mockRequest).toHaveBeenCalledWith('/accounts');
    expect(res.data).toEqual([]);
  });

  it('返回账号列表数据', async () => {
    const accounts = [
      {
        id: 1,
        account_name: 'player001',
        password_masked: 'pl****',
        platform_type: 'JND28WEB',
        status: 'inactive',
        balance: 100.5,
        kill_switch: false,
        last_login_at: null,
      },
    ];
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: accounts });

    const res = await listAccounts();

    expect(res.data).toEqual(accounts);
    expect(res.data![0].password_masked).toBe('pl****');
  });
});

describe('createAccount', () => {
  it('调用正确路径、方法和请求体', async () => {
    const payload = { account_name: 'player001', password: 'mypassword', platform_type: 'JND28WEB' as const };
    const created = {
      id: 1,
      account_name: 'player001',
      password_masked: 'my****',
      platform_type: 'JND28WEB',
      status: 'inactive',
      balance: 0,
      kill_switch: false,
      last_login_at: null,
    };
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: created });

    const res = await createAccount(payload);

    expect(mockRequest).toHaveBeenCalledWith('/accounts', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    expect(res.data).toEqual(created);
  });

  it('支持 JND282 盘口类型', async () => {
    const payload = { account_name: 'test', password: 'pw', platform_type: 'JND282' as const };
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: null });

    await createAccount(payload);

    const body = JSON.parse(mockRequest.mock.calls[0][1]!.body as string);
    expect(body.platform_type).toBe('JND282');
  });
});

describe('deleteAccount', () => {
  it('调用正确路径和方法', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: null });

    await deleteAccount(42);

    expect(mockRequest).toHaveBeenCalledWith('/accounts/42', {
      method: 'DELETE',
    });
  });
});

describe('loginAccount', () => {
  it('调用正确路径和方法', async () => {
    const account = {
      id: 1,
      account_name: 'player001',
      password_masked: 'pl****',
      platform_type: 'JND28WEB',
      status: 'online',
      balance: 500,
      kill_switch: false,
      last_login_at: '2025-01-01T00:00:00Z',
    };
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: account });

    const res = await loginAccount(1);

    expect(mockRequest).toHaveBeenCalledWith('/accounts/1/login', {
      method: 'POST',
    });
    expect(res.data!.status).toBe('online');
  });
});

describe('updateKillSwitch', () => {
  it('启用熔断：调用正确路径、方法和请求体', async () => {
    const account = {
      id: 5,
      account_name: 'player005',
      password_masked: 'pl****',
      platform_type: 'JND282',
      status: 'inactive',
      balance: 0,
      kill_switch: true,
      last_login_at: null,
    };
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: account });

    const res = await updateKillSwitch(5, { enabled: true });

    expect(mockRequest).toHaveBeenCalledWith('/accounts/5/kill-switch', {
      method: 'POST',
      body: JSON.stringify({ enabled: true }),
    });
    expect(res.data!.kill_switch).toBe(true);
  });

  it('关闭熔断', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: { id: 5, kill_switch: false },
    });

    await updateKillSwitch(5, { enabled: false });

    const body = JSON.parse(mockRequest.mock.calls[0][1]!.body as string);
    expect(body.enabled).toBe(false);
  });
});
