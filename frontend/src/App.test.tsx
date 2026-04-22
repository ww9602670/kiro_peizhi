import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App';

vi.mock('@/hooks/useAuth', () => ({
  useAuth: vi.fn(),
}));

vi.mock('@/hooks/useAlerts', () => ({
  useAlerts: vi.fn(),
}));

vi.mock('@/pages/Login', () => ({
  default: () => <div>登录页</div>,
}));

vi.mock('@/pages/operator/Dashboard', () => ({
  default: ({ onCreateStrategy }: { onCreateStrategy?: () => void }) => (
    <div>
      <div>仪表盘页面</div>
      <button type="button" onClick={onCreateStrategy}>dashboard-create</button>
    </div>
  ),
}));

vi.mock('@/pages/operator/Accounts', () => ({
  default: ({ onCreateStrategy }: { onCreateStrategy: (accountId: number) => void }) => (
    <button type="button" onClick={() => onCreateStrategy(42)}>账号页</button>
  ),
}));

vi.mock('@/pages/operator/Strategies', () => ({
  default: ({ createIntent }: { createIntent?: { accountId?: number } | null }) => (
    <div>策略页:{createIntent?.accountId ?? 'empty'}</div>
  ),
}));

vi.mock('@/pages/operator/My', () => ({
  default: () => <div>我的页面</div>,
}));

vi.mock('@/pages/admin/Dashboard', () => ({
  default: () => <div>管理仪表盘</div>,
}));

vi.mock('@/pages/admin/Operators', () => ({
  default: () => <div>操作员管理</div>,
}));

import { useAlerts } from '@/hooks/useAlerts';
import { useAuth } from '@/hooks/useAuth';

const mockUseAuth = vi.mocked(useAuth);
const mockUseAlerts = vi.mocked(useAlerts);

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, '', '/dashboard');

  mockUseAuth.mockReturnValue({
    isAuthenticated: true,
    role: 'operator',
    operatorId: '1',
    login: vi.fn(),
    logout: vi.fn().mockResolvedValue(undefined),
    silentRefresh: vi.fn(),
  });

  mockUseAlerts.mockReturnValue({
    unreadCount: 0,
    alerts: [],
    total: 0,
    loading: false,
    initialized: true,
    error: '',
    fetchAlerts: vi.fn(),
    fetchUnreadCount: vi.fn(),
    markRead: vi.fn(),
    markAllRead: vi.fn(),
    startPolling: vi.fn(),
    stopPolling: vi.fn(),
  });
});

describe('App', () => {
  it('shows login page when user is not authenticated', () => {
    mockUseAuth.mockReturnValue({
      isAuthenticated: false,
      role: null,
      operatorId: null,
      login: vi.fn(),
      logout: vi.fn(),
      silentRefresh: vi.fn(),
    });

    render(<App />);
    expect(screen.getByText('登录页')).toBeInTheDocument();
  });

  it('replaces /login with /dashboard after successful auth state', () => {
    window.history.replaceState({}, '', '/login');
    render(<App />);
    expect(screen.getByText('仪表盘页面')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/dashboard');
  });

  it('syncs operator navigation with browser url', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getAllByRole('button', { name: '账号' })[0]);
    expect(window.location.pathname).toBe('/accounts');
    expect(screen.getByRole('button', { name: '账号页' })).toBeInTheDocument();

    await user.click(screen.getAllByRole('button', { name: '我的' })[0]);
    expect(window.location.pathname).toBe('/me');
    expect(screen.getByText('我的页面')).toBeInTheDocument();
  });

  it('opens strategy create flow from account page and updates url', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getAllByRole('button', { name: '账号' })[0]);
    await user.click(screen.getByRole('button', { name: '账号页' }));

    expect(window.location.pathname).toBe('/strategies');
    expect(screen.getByText('策略页:42')).toBeInTheDocument();
  });
});
