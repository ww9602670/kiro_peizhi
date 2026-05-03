import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Dashboard from './Dashboard';
import type { OperatorDashboard } from '@/types/api/dashboard';
import { operatorUpdates } from '@/data/operatorUpdates';

vi.mock('@/hooks/useDashboard');
vi.mock('@/hooks/useAlertsContext');
vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({
    isAuthenticated: true,
    role: 'operator',
    operatorId: '1',
    login: vi.fn(),
    logout: vi.fn(),
    silentRefresh: vi.fn(),
  }),
}));
const mockCountdownDisplay = vi.fn((_: unknown) => <div data-testid="countdown">countdown</div>);
vi.mock('@/components/CountdownDisplay', () => ({
  CountdownDisplay: (props: unknown) => mockCountdownDisplay(props),
}));

import { useDashboard } from '@/hooks/useDashboard';
import { useAlertsContext } from '@/hooks/useAlertsContext';

const mockUseDashboard = vi.mocked(useDashboard);
const mockUseAlertsContext = vi.mocked(useAlertsContext);

const baseDashboard: OperatorDashboard = {
  balance: 1234.56,
  daily_pnl: 88.88,
  total_pnl: -50.0,
  countdown_platform_type: 'JND28WEB',
  running_strategies: [],
  pending_bets: [],
  unread_alerts: 5,
  recent_results: [
    {
      id: 1,
      issue: '202604190001',
      open_result: '1,2,3',
      sum_value: 6,
      open_time: '2026-04-19 00:01:00',
      created_at: '2026-04-19 00:01:10',
    },
  ],
  recent_alerts: [
    {
      id: 11,
      operator_id: 1,
      type: 'bet_fail',
      level: 'critical',
      title: '投注失败',
      detail: '测试告警',
      is_read: 0,
      created_at: '2026-04-19 00:02:00',
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  mockCountdownDisplay.mockClear();
  mockUseAlertsContext.mockReturnValue({
    alerts: baseDashboard.recent_alerts,
    unreadCount: baseDashboard.unread_alerts,
    total: baseDashboard.recent_alerts.length,
    loading: false,
    initialized: true,
    error: '',
    fetchAlerts: vi.fn().mockResolvedValue(undefined),
    fetchUnreadCount: vi.fn().mockResolvedValue(undefined),
    markRead: vi.fn().mockResolvedValue(undefined),
    markAllRead: vi.fn().mockResolvedValue(undefined),
    startPolling: vi.fn(),
    stopPolling: vi.fn(),
  });
});

function setupHook(overrides: Partial<ReturnType<typeof useDashboard>> = {}) {
  mockUseDashboard.mockReturnValue({
    data: null,
    loading: false,
    error: '',
    reload: vi.fn(),
    startAutoRefresh: vi.fn(),
    stopAutoRefresh: vi.fn(),
    ...overrides,
  });
}

describe('Dashboard', () => {
  it('renders stat cards', () => {
    setupHook({ data: baseDashboard });
    render(<Dashboard />);
    expect(screen.getByText('1234.56')).toBeInTheDocument();
    expect(screen.getByText('+88.88')).toBeInTheDocument();
    expect(screen.getByText('-50.00')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
  });

  it('renders alerts from context', () => {
    setupHook({ data: baseDashboard });
    render(<Dashboard />);
    expect(screen.getByText('投注失败')).toBeInTheDocument();
    expect(screen.getByText('测试告警')).toBeInTheDocument();
  });

  it('hides detail for coded alerts', () => {
    setupHook({ data: baseDashboard });
    mockUseAlertsContext.mockReturnValue({
      alerts: [
        {
          ...baseDashboard.recent_alerts[0],
          title: '数据更新变慢，可能影响投注，请联系管理员处理。日志编号：SHARED-002。',
          detail: 'raw backend detail',
        },
      ],
      unreadCount: 1,
      total: 1,
      loading: false,
      initialized: true,
      error: '',
      fetchAlerts: vi.fn().mockResolvedValue(undefined),
      fetchUnreadCount: vi.fn().mockResolvedValue(undefined),
      markRead: vi.fn().mockResolvedValue(undefined),
      markAllRead: vi.fn().mockResolvedValue(undefined),
      startPolling: vi.fn(),
      stopPolling: vi.fn(),
    });
    render(<Dashboard />);
    expect(screen.getByText('数据更新变慢，可能影响投注，请联系管理员处理。日志编号：SHARED-002。')).toBeInTheDocument();
    expect(screen.queryByText('raw backend detail')).not.toBeInTheDocument();
  });

  it('renders localized running strategy cards', () => {
    setupHook({
      data: {
        ...baseDashboard,
        running_strategies: [
          {
            id: 1,
            account_id: 1,
            name: '测试策略',
            type: 'flat',
            play_code: 'DX1,DW3_BS_BBB',
            play_code_name: '',
            base_amount: 10,
            martin_sequence: null,
            bet_timing: 30,
            simulation: false,
            status: 'running',
            martin_level: 0,
            stop_loss: null,
            take_profit: null,
            daily_pnl: 25.5,
            total_pnl: 100,
            platform_type: 'JND28WEB',
          },
        ],
      },
    });
    render(<Dashboard />);
    expect(screen.getByText('测试策略')).toBeInTheDocument();
    expect(
      screen.getByText((_, node) => node?.textContent === '今日 +25.50'),
    ).toBeInTheDocument();
  });

  it('renders the shared recent updates feed and opens full modal', async () => {
    const user = userEvent.setup();
    setupHook({ data: baseDashboard });
    render(<Dashboard />);

    expect(screen.getByText(operatorUpdates[0].title)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '查看全部' }));
    expect(screen.getByRole('dialog', { name: '平台更新详情' })).toBeInTheDocument();
  });

  it('renders the countdown block', () => {
    setupHook({ data: baseDashboard });
    render(<Dashboard />);
    expect(screen.getByTestId('countdown')).toBeInTheDocument();
    expect(mockCountdownDisplay).toHaveBeenCalledWith(
      expect.objectContaining({ platformType: 'JND28WEB' }),
    );
  });

  it('passes backend-selected countdown platform to CountdownDisplay', () => {
    setupHook({
      data: {
        ...baseDashboard,
        countdown_platform_type: 'JND282',
      },
    });
    render(<Dashboard />);
    expect(mockCountdownDisplay).toHaveBeenCalledWith(
      expect.objectContaining({ platformType: 'JND282' }),
    );
  });

  it('starts and stops auto refresh', () => {
    const startAutoRefresh = vi.fn();
    const stopAutoRefresh = vi.fn();
    setupHook({ data: baseDashboard, startAutoRefresh, stopAutoRefresh });
    const { unmount } = render(<Dashboard />);
    expect(startAutoRefresh).toHaveBeenCalled();
    unmount();
    expect(stopAutoRefresh).toHaveBeenCalled();
  });
});
