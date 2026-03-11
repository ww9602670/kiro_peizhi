import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fetchDashboard, fetchRecentBets, fetchAdminDashboard } from './dashboard';

vi.mock('@/api/request', () => ({
  request: vi.fn(),
  isApiError: vi.fn(),
}));

import { request } from '@/api/request';
const mockRequest = vi.mocked(request);

beforeEach(() => {
  vi.clearAllMocks();
});

describe('fetchDashboard', () => {
  it('calls /dashboard', async () => {
    mockRequest.mockResolvedValue({ code: 0, message: 'success', data: {} });
    await fetchDashboard();
    expect(mockRequest).toHaveBeenCalledWith('/dashboard');
  });
});

describe('fetchRecentBets', () => {
  it('calls /dashboard/recent-bets', async () => {
    mockRequest.mockResolvedValue({ code: 0, message: 'success', data: [] });
    await fetchRecentBets();
    expect(mockRequest).toHaveBeenCalledWith('/dashboard/recent-bets');
  });
});

describe('fetchAdminDashboard', () => {
  it('calls /admin/dashboard', async () => {
    mockRequest.mockResolvedValue({ code: 0, message: 'success', data: {} });
    await fetchAdminDashboard();
    expect(mockRequest).toHaveBeenCalledWith('/admin/dashboard');
  });
});
