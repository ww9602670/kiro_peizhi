import { describe, expect, it, vi, beforeEach } from 'vitest';
import { listOperators, createOperator, updateOperator, updateOperatorStatus, fetchAdminDashboard, setGlobalKillSwitch } from './admin';

vi.mock('@/api/request', () => ({
  request: vi.fn(),
  isApiError: vi.fn(),
}));

import { request } from '@/api/request';
const mockRequest = vi.mocked(request);

beforeEach(() => {
  vi.clearAllMocks();
});

describe('listOperators', () => {
  it('calls /admin/operators with no params', async () => {
    mockRequest.mockResolvedValue({ code: 0, message: 'success', data: { items: [], total: 0, page: 1, page_size: 20 } });
    await listOperators();
    expect(mockRequest).toHaveBeenCalledWith('/admin/operators');
  });

  it('builds query string with params', async () => {
    mockRequest.mockResolvedValue({ code: 0, message: 'success', data: { items: [], total: 0, page: 2, page_size: 10 } });
    await listOperators({ page: 2, page_size: 10 });
    expect(mockRequest).toHaveBeenCalledWith('/admin/operators?page=2&page_size=10');
  });
});

describe('createOperator', () => {
  it('POSTs to /admin/operators', async () => {
    mockRequest.mockResolvedValue({ code: 0, message: 'success', data: { id: 1 } });
    await createOperator({ username: 'test', password: '123456' });
    expect(mockRequest).toHaveBeenCalledWith('/admin/operators', {
      method: 'POST',
      body: JSON.stringify({ username: 'test', password: '123456' }),
    });
  });
});

describe('updateOperator', () => {
  it('PUTs to /admin/operators/:id', async () => {
    mockRequest.mockResolvedValue({ code: 0, message: 'success', data: { id: 1 } });
    await updateOperator(1, { max_accounts: 5 });
    expect(mockRequest).toHaveBeenCalledWith('/admin/operators/1', {
      method: 'PUT',
      body: JSON.stringify({ max_accounts: 5 }),
    });
  });
});

describe('updateOperatorStatus', () => {
  it('PUTs to /admin/operators/:id/status', async () => {
    mockRequest.mockResolvedValue({ code: 0, message: 'success', data: { id: 1 } });
    await updateOperatorStatus(1, { status: 'disabled' });
    expect(mockRequest).toHaveBeenCalledWith('/admin/operators/1/status', {
      method: 'PUT',
      body: JSON.stringify({ status: 'disabled' }),
    });
  });
});

describe('fetchAdminDashboard', () => {
  it('calls /admin/dashboard', async () => {
    mockRequest.mockResolvedValue({ code: 0, message: 'success', data: {} });
    await fetchAdminDashboard();
    expect(mockRequest).toHaveBeenCalledWith('/admin/dashboard');
  });
});

describe('setGlobalKillSwitch', () => {
  it('POSTs to /admin/kill-switch', async () => {
    mockRequest.mockResolvedValue({ code: 0, message: 'success', data: { enabled: true } });
    await setGlobalKillSwitch({ enabled: true });
    expect(mockRequest).toHaveBeenCalledWith('/admin/kill-switch', {
      method: 'POST',
      body: JSON.stringify({ enabled: true }),
    });
  });
});
