import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createAccount,
  deleteAccount,
  listAccounts,
  loginAccount,
  updateKillSwitch,
  verifyAccount,
} from './accounts';

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
  it('requests /accounts', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: [] });

    const res = await listAccounts();

    expect(mockRequest).toHaveBeenCalledWith('/accounts');
    expect(res.data).toEqual([]);
  });
});

describe('createAccount', () => {
  it('posts account payload', async () => {
    const payload = {
      account_name: 'player001',
      password: 'mypassword',
      game_type: 'JND28' as const,
      platform_url: 'https://merchant.example',
    };
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: null });

    await createAccount(payload);

    expect(mockRequest).toHaveBeenCalledWith('/accounts', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  });
});

describe('deleteAccount', () => {
  it('deletes account by id', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: null });

    await deleteAccount(42);

    expect(mockRequest).toHaveBeenCalledWith('/accounts/42', {
      method: 'DELETE',
    });
  });
});

describe('verifyAccount', () => {
  it('uses the primary /verify endpoint', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: { id: 1 } });

    await verifyAccount(1);

    expect(mockRequest).toHaveBeenCalledWith('/accounts/1/verify', {
      method: 'POST',
    });
  });
});

describe('loginAccount compatibility alias', () => {
  it('delegates to /verify instead of /login', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: { id: 1 } });

    await loginAccount(1);

    expect(mockRequest).toHaveBeenCalledWith('/accounts/1/verify', {
      method: 'POST',
    });
  });
});

describe('updateKillSwitch', () => {
  it('posts kill-switch update', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: { id: 5, kill_switch: true },
    });

    await updateKillSwitch(5, { enabled: true });

    expect(mockRequest).toHaveBeenCalledWith('/accounts/5/kill-switch', {
      method: 'POST',
      body: JSON.stringify({ enabled: true }),
    });
  });
});
