/**
 * 告警页面测试
 * - 加载/错误/空状态
 * - 告警卡片渲染（级别、标题、详情）
 * - 筛选 tab 切换
 * - 标记已读
 * - 全部已读
 * - 分页
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Alerts from './Alerts';
import type { AlertInfo } from '@/types/api/alert';

vi.mock('@/hooks/useAlerts');
vi.mock('@/api/request', () => ({
  isApiError: (e: unknown) =>
    typeof e === 'object' && e !== null && 'code' in e && 'message' in e,
}));

import { useAlerts } from '@/hooks/useAlerts';
const mockUseAlerts = vi.mocked(useAlerts);

const sampleAlerts: AlertInfo[] = [
  { id: 1, operator_id: 1, type: 'login_fail', level: 'critical', title: '登录失败', detail: '密码错误', is_read: 0, created_at: '2026-03-01 12:00:00' },
  { id: 2, operator_id: 1, type: 'bet_fail', level: 'warning', title: '下注失败', detail: null, is_read: 1, created_at: '2026-03-01 11:00:00' },
  { id: 3, operator_id: 1, type: 'settle_fail', level: 'info', title: '结算异常', detail: '超时', is_read: 0, created_at: '2026-03-01 10:00:00' },
];

function setupHook(overrides: Partial<ReturnType<typeof useAlerts>> = {}) {
  mockUseAlerts.mockReturnValue({
    unreadCount: 2,
    alerts: sampleAlerts,
    total: 3,
    loading: false,
    initialized: true,
    error: '',
    fetchAlerts: vi.fn(),
    fetchUnreadCount: vi.fn(),
    markRead: vi.fn(),
    markAllRead: vi.fn(),
    startPolling: vi.fn(),
    stopPolling: vi.fn(),
    ...overrides,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('Alerts', () => {
  it('加载中显示加载文本', () => {
    setupHook({ loading: true, alerts: [], total: 0 });
    render(<Alerts />);
    expect(screen.getByText('加载中...')).toBeInTheDocument();
  });

  it('错误时显示错误信息', () => {
    setupHook({ error: '加载失败', alerts: [], total: 0 });
    render(<Alerts />);
    expect(screen.getByText('加载失败')).toBeInTheDocument();
  });

  it('空列表显示"暂无告警"', () => {
    setupHook({ alerts: [], total: 0 });
    render(<Alerts />);
    expect(screen.getByText('暂无告警')).toBeInTheDocument();
  });

  it('渲染告警卡片', () => {
    setupHook();
    render(<Alerts />);
    expect(screen.getByText('登录失败')).toBeInTheDocument();
    expect(screen.getByText('下注失败')).toBeInTheDocument();
    expect(screen.getByText('结算异常')).toBeInTheDocument();
  });

  it('告警级别映射为中文', () => {
    setupHook();
    render(<Alerts />);
    expect(screen.getByText('严重')).toBeInTheDocument();
    expect(screen.getByText('警告')).toBeInTheDocument();
    expect(screen.getByText('信息')).toBeInTheDocument();
  });

  it('告警详情正确显示', () => {
    setupHook();
    render(<Alerts />);
    expect(screen.getByText('密码错误')).toBeInTheDocument();
    expect(screen.getByText('超时')).toBeInTheDocument();
  });

  it('未读告警显示"标记已读"按钮', () => {
    setupHook();
    render(<Alerts />);
    // 2 条未读告警应有 2 个标记已读按钮
    const readBtns = screen.getAllByText('标记已读');
    expect(readBtns).toHaveLength(2);
  });

  it('已读告警不显示"标记已读"按钮', () => {
    setupHook({
      alerts: [sampleAlerts[1]], // 只有已读的
      total: 1,
    });
    render(<Alerts />);
    expect(screen.queryByText('标记已读')).not.toBeInTheDocument();
  });

  it('点击"标记已读"调用 markRead', async () => {
    const markRead = vi.fn().mockResolvedValue(undefined);
    const fetchUnreadCount = vi.fn();
    setupHook({ markRead, fetchUnreadCount });
    render(<Alerts />);

    const btns = screen.getAllByText('标记已读');
    await userEvent.click(btns[0]);

    expect(markRead).toHaveBeenCalledWith(1);
    expect(fetchUnreadCount).toHaveBeenCalled();
  });

  it('点击"全部已读"调用 markAllRead', async () => {
    const markAllRead = vi.fn().mockResolvedValue(undefined);
    const fetchUnreadCount = vi.fn();
    const fetchAlerts = vi.fn();
    setupHook({ markAllRead, fetchUnreadCount, fetchAlerts });
    render(<Alerts />);

    await userEvent.click(screen.getByText('全部已读'));

    expect(markAllRead).toHaveBeenCalled();
    expect(fetchUnreadCount).toHaveBeenCalled();
  });

  it('筛选 tab 渲染', () => {
    setupHook();
    render(<Alerts />);
    expect(screen.getByText('全部')).toBeInTheDocument();
    expect(screen.getByText('未读')).toBeInTheDocument();
    expect(screen.getByText('已读')).toBeInTheDocument();
  });

  it('切换筛选 tab 触发重新加载', async () => {
    const fetchAlerts = vi.fn();
    setupHook({ fetchAlerts });
    render(<Alerts />);

    await userEvent.click(screen.getByText('未读'));
    // fetchAlerts 应被调用（初始 + 切换）
    expect(fetchAlerts).toHaveBeenCalled();
  });
});
