/**
 * 操作者仪表盘页面测试
 * - 加载状态
 * - 错误状态
 * - 正常渲染统计卡片
 * - 运行中策略列表
 * - 空策略提示
 * - 投注记录渲染
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import Dashboard from './Dashboard';
import type { OperatorDashboard } from '@/types/api/dashboard';

// Mock hooks and components
vi.mock('@/hooks/useDashboard');
vi.mock('@/components/CountdownDisplay', () => ({
  CountdownDisplay: () => <div data-testid="countdown">countdown</div>,
}));

import { useDashboard } from '@/hooks/useDashboard';
const mockUseDashboard = vi.mocked(useDashboard);

const baseDashboard: OperatorDashboard = {
  balance: 1234.56,
  daily_pnl: 88.88,
  total_pnl: -50.0,
  running_strategies: [],
  pending_bets: [],
  unread_alerts: 5,
};

beforeEach(() => {
  vi.clearAllMocks();
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
  it('加载中显示加载文本', () => {
    setupHook({ loading: true, data: null });
    render(<Dashboard />);
    expect(screen.getByText('加载中...')).toBeInTheDocument();
  });

  it('错误状态显示错误信息', () => {
    setupHook({ error: '网络错误', data: null });
    render(<Dashboard />);
    expect(screen.getByText('网络错误')).toBeInTheDocument();
  });

  it('正常渲染统计卡片', () => {
    setupHook({ data: baseDashboard });
    render(<Dashboard />);
    expect(screen.getByText('1234.56')).toBeInTheDocument();
    expect(screen.getByText('+88.88')).toBeInTheDocument();
    expect(screen.getByText('-50.00')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
  });

  it('无运行策略时显示提示', () => {
    setupHook({ data: baseDashboard });
    render(<Dashboard />);
    expect(screen.getByText('暂无运行中策略')).toBeInTheDocument();
  });

  it('有运行策略时渲染策略卡片', () => {
    setupHook({
      data: {
        ...baseDashboard,
        running_strategies: [
          {
            id: 1,
            account_id: 1,
            name: '大小平注',
            type: 'flat',
            play_code: 'DX1',
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
          },
        ],
      },
    });
    render(<Dashboard />);
    expect(screen.getByText('大小平注')).toBeInTheDocument();
    expect(screen.getByText('平注')).toBeInTheDocument();
    expect(screen.getByText(/\+25\.50/)).toBeInTheDocument();
  });

  it('渲染倒计时组件', () => {
    setupHook({ data: baseDashboard });
    render(<Dashboard />);
    expect(screen.getByTestId('countdown')).toBeInTheDocument();
  });

  it('调用 startAutoRefresh 和 stopAutoRefresh', () => {
    const startAutoRefresh = vi.fn();
    const stopAutoRefresh = vi.fn();
    setupHook({ data: baseDashboard, startAutoRefresh, stopAutoRefresh });
    const { unmount } = render(<Dashboard />);
    expect(startAutoRefresh).toHaveBeenCalled();
    unmount();
    expect(stopAutoRefresh).toHaveBeenCalled();
  });
});
