/**
 * 操作者管理页面测试
 * - 列表渲染
 * - 分页
 * - 创建表单
 * - 禁用/启用按钮
 * - expired 状态显示（已知问题回归）
 * - admin 角色不显示操作按钮
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fc from 'fast-check';
import Operators from './Operators';

vi.mock('@/api/admin', () => ({
  listOperators: vi.fn(),
  createOperator: vi.fn(),
  updateOperatorStatus: vi.fn(),
}));
vi.mock('@/api/request', () => ({
  isApiError: (e: unknown) =>
    typeof e === 'object' && e !== null && 'code' in e && 'message' in e,
}));

import { listOperators } from '@/api/admin';
const mockList = vi.mocked(listOperators);

const operators = [
  { id: 1, username: 'admin', role: 'admin', status: 'active', max_accounts: 99, expire_date: null, created_at: '2026-01-01' },
  { id: 2, username: 'op1', role: 'operator', status: 'active', max_accounts: 3, expire_date: '2026-12-31', created_at: '2026-02-01' },
  { id: 3, username: 'op2', role: 'operator', status: 'disabled', max_accounts: 1, expire_date: null, created_at: '2026-03-01' },
  { id: 4, username: 'op3', role: 'operator', status: 'expired', max_accounts: 1, expire_date: '2025-12-31', created_at: '2026-01-15' },
];

beforeEach(() => {
  vi.clearAllMocks();
  mockList.mockResolvedValue({
    code: 0, message: 'success',
    data: { items: operators, total: 4, page: 1, page_size: 20 },
  });
});

describe('Operators', () => {
  it('渲染操作者列表', async () => {
    render(<Operators />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());
    expect(screen.getByText('op1')).toBeInTheDocument();
    expect(screen.getByText('op2')).toBeInTheDocument();
    expect(screen.getByText('op3')).toBeInTheDocument();
  });

  it('角色映射为中文', async () => {
    render(<Operators />);
    await waitFor(() => expect(screen.getByText('管理员')).toBeInTheDocument());
    expect(screen.getAllByText('操作者')).toHaveLength(3);
  });

  it('active 状态显示"活跃"', async () => {
    render(<Operators />);
    await waitFor(() => expect(screen.getAllByText('活跃')).toHaveLength(2));
  });

  it('disabled 状态显示"禁用"', async () => {
    render(<Operators />);
    await waitFor(() => {
      const badges = screen.getAllByText('禁用');
      // 状态列 + 操作按钮都可能有"禁用"
      expect(badges.length).toBeGreaterThanOrEqual(1);
    });
  });

  it('expired 状态回归测试：当前显示原始值 expired', async () => {
    render(<Operators />);
    await waitFor(() => expect(screen.getByText('expired')).toBeInTheDocument());
  });

  it('admin 角色不显示操作按钮', async () => {
    render(<Operators />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());
    // admin 行不应有禁用/启用按钮
    const adminRow = screen.getByText('admin').closest('tr')!;
    expect(adminRow.querySelector('.toggle-btn')).toBeNull();
  });

  it('operator 行显示禁用/启用按钮', async () => {
    render(<Operators />);
    await waitFor(() => expect(screen.getByText('op1')).toBeInTheDocument());
    const op1Row = screen.getByText('op1').closest('tr')!;
    expect(op1Row.querySelector('.toggle-btn')).not.toBeNull();
  });

  it('点击"+ 创建操作者"显示表单', async () => {
    render(<Operators />);
    await waitFor(() => expect(screen.getByText('+ 创建操作者')).toBeInTheDocument());
    await userEvent.click(screen.getByText('+ 创建操作者'));
    expect(screen.getByText('创建操作者')).toBeInTheDocument();
    expect(screen.getByText('确认创建')).toBeInTheDocument();
  });

  it('空列表显示提示', async () => {
    mockList.mockResolvedValueOnce({
      code: 0, message: 'success',
      data: { items: [], total: 0, page: 1, page_size: 20 },
    });
    render(<Operators />);
    await waitFor(() => expect(screen.getByText('暂无操作者')).toBeInTheDocument());
  });

  it('加载失败显示错误', async () => {
    mockList.mockRejectedValueOnce({ code: 5001, message: '服务端错误' });
    render(<Operators />);
    await waitFor(() => expect(screen.getByText('服务端错误')).toBeInTheDocument());
  });
});


const OP_EXPECTED_LABELS = ['ID', '用户名', '角色', '状态', '最大账号', '到期日期', '操作'];

const arbOperator = fc.record({
  id: fc.nat(),
  username: fc.string({ minLength: 1, maxLength: 10 }),
  role: fc.constantFrom('admin', 'operator'),
  status: fc.constantFrom('active', 'disabled', 'expired'),
  max_accounts: fc.nat({ max: 99 }),
  expire_date: fc.oneof(fc.constant(null), fc.constant('2026-12-31')),
  created_at: fc.constant('2026-01-01'),
});

/**
 * Property 3: Operators data-label 完整性
 * Validates: Requirements 7.3, 6.2
 */
describe('Property 3: Operators data-label 完整性', () => {
  it('操作者管理表格每行的 7 个 td 应包含正确的 data-label 属性', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(arbOperator, { minLength: 1, maxLength: 3 }),
        async (ops) => {
          mockList.mockResolvedValueOnce({
            code: 0,
            message: 'success',
            data: { items: ops, total: ops.length, page: 1, page_size: 20 },
          });
          const { container, unmount } = render(<Operators />);
          await waitFor(() => {
            const rows = container.querySelectorAll('.operators-table tbody tr');
            expect(rows.length).toBe(ops.length);
            rows.forEach((row) => {
              const tds = row.querySelectorAll('td');
              expect(tds.length).toBe(7);
              tds.forEach((td, i) => {
                expect(td.getAttribute('data-label')).toBe(OP_EXPECTED_LABELS[i]);
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
