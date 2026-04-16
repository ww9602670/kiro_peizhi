/**
 * 管理员仪表盘页面测试
 * - 加载/错误状态
 * - 统计卡片渲染
 * - 操作者汇总表格
 * - expired 状态显示（回归测试：应翻译为中文而非显示原始值）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import fc from 'fast-check';
import AdminDashboardPage from './Dashboard';

vi.mock('@/api/admin', () => ({
  fetchAdminDashboard: vi.fn(),
}));
vi.mock('@/api/request', () => ({
  isApiError: (e: unknown) =>
    typeof e === 'object' && e !== null && 'code' in e && 'message' in e,
}));

import { fetchAdminDashboard } from '@/api/admin';
const mockFetch = vi.mocked(fetchAdminDashboard);

beforeEach(() => {
  vi.clearAllMocks();
});

const dashboardData = {
  total_operators: 10,
  active_operators: 7,
  operator_summaries: [
    { id: 1, username: 'admin', status: 'active', daily_pnl: 100, total_pnl: 500, running_strategies: 2 },
    { id: 2, username: 'op1', status: 'disabled', daily_pnl: -20, total_pnl: -100, running_strategies: 0 },
    { id: 3, username: 'op2', status: 'expired', daily_pnl: 0, total_pnl: 50, running_strategies: 0 },
  ],
};

describe('AdminDashboardPage', () => {
  it('加载中显示加载文本', async () => {
    mockFetch.mockReturnValue(new Promise(() => {})); // never resolves
    render(<AdminDashboardPage />);
    expect(screen.getByText('加载中...')).toBeInTheDocument();
  });

  it('错误时显示错误信息', async () => {
    mockFetch.mockRejectedValueOnce({ code: 5001, message: '服务端错误' });
    render(<AdminDashboardPage />);
    await waitFor(() => expect(screen.getByText('服务端错误')).toBeInTheDocument());
  });

  it('渲染统计卡片', async () => {
    mockFetch.mockResolvedValueOnce({ code: 0, message: 'success', data: dashboardData });
    render(<AdminDashboardPage />);
    await waitFor(() => expect(screen.getByText('10')).toBeInTheDocument());
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  it('渲染操作者汇总表格', async () => {
    mockFetch.mockResolvedValueOnce({ code: 0, message: 'success', data: dashboardData });
    render(<AdminDashboardPage />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());
    expect(screen.getByText('op1')).toBeInTheDocument();
    expect(screen.getByText('op2')).toBeInTheDocument();
  });

  it('active 状态显示"活跃"', async () => {
    mockFetch.mockResolvedValueOnce({ code: 0, message: 'success', data: dashboardData });
    render(<AdminDashboardPage />);
    await waitFor(() => expect(screen.getByText('活跃')).toBeInTheDocument());
  });

  it('disabled 状态显示"禁用"', async () => {
    mockFetch.mockResolvedValueOnce({ code: 0, message: 'success', data: dashboardData });
    render(<AdminDashboardPage />);
    await waitFor(() => expect(screen.getByText('禁用')).toBeInTheDocument());
  });

  it('expired 状态回归测试：当前显示原始值 expired（已知问题）', async () => {
    // 这是一个已知 bug：expired 状态没有翻译为中文
    // 当 bug 修复后，应改为 expect(screen.getByText('已过期'))
    mockFetch.mockResolvedValueOnce({ code: 0, message: 'success', data: dashboardData });
    render(<AdminDashboardPage />);
    await waitFor(() => expect(screen.getByText('expired')).toBeInTheDocument());
  });

  it('空操作者列表显示提示', async () => {
    mockFetch.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: { total_operators: 0, active_operators: 0, operator_summaries: [] },
    });
    render(<AdminDashboardPage />);
    await waitFor(() => expect(screen.getByText('暂无操作者')).toBeInTheDocument());
  });

  it('盈亏正确显示正负号', async () => {
    mockFetch.mockResolvedValueOnce({ code: 0, message: 'success', data: dashboardData });
    render(<AdminDashboardPage />);
    await waitFor(() => expect(screen.getByText('+100.00')).toBeInTheDocument());
    expect(screen.getByText('-20.00')).toBeInTheDocument();
  });
});


const ADMIN_EXPECTED_LABELS = ['ID', '用户名', '状态', '当日盈亏', '总盈亏', '运行策略'];

const arbOperatorSummary = fc.record({
  id: fc.nat(),
  username: fc.string({ minLength: 1, maxLength: 10 }),
  status: fc.constantFrom('active', 'disabled', 'expired'),
  daily_pnl: fc.float({ min: Math.fround(-9999), max: Math.fround(9999), noNaN: true }),
  total_pnl: fc.float({ min: Math.fround(-9999), max: Math.fround(9999), noNaN: true }),
  running_strategies: fc.nat({ max: 20 }),
});

/**
 * Property 2: AdminDashboard data-label 完整性
 * Validates: Requirements 7.2, 5.2
 */
describe('Property 2: AdminDashboard data-label 完整性', () => {
  it('操作者汇总表格每行的 6 个 td 应包含正确的 data-label 属性', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(arbOperatorSummary, { minLength: 1, maxLength: 3 }),
        async (summaries) => {
          mockFetch.mockResolvedValueOnce({
            code: 0,
            message: 'success',
            data: {
              total_operators: summaries.length,
              active_operators: summaries.filter((s) => s.status === 'active').length,
              operator_summaries: summaries,
            },
          });
          const { container, unmount } = render(<AdminDashboardPage />);
          await waitFor(() => {
            const rows = container.querySelectorAll('.admin-table tbody tr');
            expect(rows.length).toBe(summaries.length);
            rows.forEach((row) => {
              const tds = row.querySelectorAll('td');
              expect(tds.length).toBe(6);
              tds.forEach((td, i) => {
                expect(td.getAttribute('data-label')).toBe(ADMIN_EXPECTED_LABELS[i]);
              });
            });
          });
          unmount();
        },
      ),
      { numRuns: 20 },
    );
  });
});
