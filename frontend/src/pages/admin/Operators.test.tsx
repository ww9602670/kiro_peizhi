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
  listOperatorStrategyPermissions: vi.fn(),
  updateAccountStrategyPermissions: vi.fn(),
  listSharedMarketGroups: vi.fn(),
  listSharedMarketUncoveredUrls: vi.fn(),
  ignoreSharedMarketUncoveredUrl: vi.fn(),
  recheckSharedMarketUncoveredUrl: vi.fn(),
  joinSharedMarketUncoveredUrlGroup: vi.fn(),
}));
vi.mock('@/api/request', () => ({
  isApiError: (e: unknown) =>
    typeof e === 'object' && e !== null && 'code' in e && 'message' in e,
}));

import {
  listOperators,
  listOperatorStrategyPermissions,
  updateAccountStrategyPermissions,
  listSharedMarketGroups,
  listSharedMarketUncoveredUrls,
  ignoreSharedMarketUncoveredUrl,
  recheckSharedMarketUncoveredUrl,
  joinSharedMarketUncoveredUrlGroup,
} from '@/api/admin';
const mockList = vi.mocked(listOperators);
const mockListPermissions = vi.mocked(listOperatorStrategyPermissions);
const mockUpdatePermissions = vi.mocked(updateAccountStrategyPermissions);
const mockListSharedGroups = vi.mocked(listSharedMarketGroups);
const mockListUncovered = vi.mocked(listSharedMarketUncoveredUrls);
const mockIgnoreSharedUncovered = vi.mocked(ignoreSharedMarketUncoveredUrl);
const mockRecheckSharedUncovered = vi.mocked(recheckSharedMarketUncoveredUrl);
const mockJoinSharedGroup = vi.mocked(joinSharedMarketUncoveredUrlGroup);

const operators = [
  { id: 1, username: 'admin', role: 'admin', status: 'active', max_accounts: 99, expire_date: null, created_at: '2026-01-01' },
  { id: 2, username: 'op1', role: 'operator', status: 'active', max_accounts: 3, expire_date: '2026-12-31', created_at: '2026-02-01' },
  { id: 3, username: 'op2', role: 'operator', status: 'disabled', max_accounts: 1, expire_date: null, created_at: '2026-03-01' },
  { id: 4, username: 'op3', role: 'operator', status: 'expired', max_accounts: 1, expire_date: '2025-12-31', created_at: '2026-01-15' },
];

const sharedMarketGroups = [
  { id: 1, group_key: 'gm_test', enabled: 1 },
  { id: 2, group_key: 'gm_test_2', enabled: 1 },
];

const sharedMarketUncoveredRows = [
  {
    id: 101,
    normalized_url: 'https://shared.example.com/abc',
    first_seen_at: '2026-01-01 10:00:00',
    last_seen_at: '2026-01-01 12:00:00',
    hit_count: 3,
    detection_status: 'pending',
    status: 'pending',
    failure_reason: '检测失败',
    last_account_id: 9,
    last_platform_type: 'JND28WEB',
    sample_raw_url: 'https://shared.example.com/abc',
    shared_group_id: null,
    shared_group_key: null,
  },
];

function getOperatorTableRow(container: HTMLElement, username: string): HTMLTableRowElement {
  const rows = Array.from(container.querySelectorAll<HTMLTableRowElement>('.operators-table tbody tr'));
  const row = rows.find((item) =>
    Array.from(item.querySelectorAll('td')).some((cell) => cell.textContent === username)
  );
  if (!row) throw new Error(`Operator row not found: ${username}`);
  return row;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockList.mockResolvedValue({
    code: 0, message: 'success',
    data: { items: operators, total: 4, page: 1, page_size: 20 },
  });
  mockListSharedGroups.mockResolvedValue({
    code: 0,
    message: 'success',
    data: sharedMarketGroups,
  });
  mockListUncovered.mockResolvedValue({
    code: 0,
    message: 'success',
    data: {
      items: sharedMarketUncoveredRows,
      total: 1,
      page: 1,
      page_size: 10,
    },
  });
  mockListPermissions.mockResolvedValue({
    code: 0,
    message: 'success',
    data: [
      {
        operator_id: 2,
        account_id: 10,
        account_name: 'acc1',
        game_type: 'JND28',
        allowed_strategy_types: ['flat'],
      },
    ],
  });
  mockUpdatePermissions.mockResolvedValue({
    code: 0,
    message: 'success',
    data: {
      operator_id: 2,
      account_id: 10,
      account_name: 'acc1',
      game_type: 'JND28',
      allowed_strategy_types: ['flat', 'martin'],
    },
  });
  mockIgnoreSharedUncovered.mockResolvedValue({
    code: 0,
    message: 'success',
    data: { ...sharedMarketUncoveredRows[0], status: 'ignored' },
  });
  mockRecheckSharedUncovered.mockResolvedValue({
    code: 0,
    message: 'success',
    data: { ...sharedMarketUncoveredRows[0], status: 'pending' },
  });
  mockJoinSharedGroup.mockResolvedValue({
    code: 0,
    message: 'success',
    data: { ...sharedMarketUncoveredRows[0], shared_group_id: 1 },
  });
});

describe('Operators', () => {
  it('渲染操作者列表', async () => {
    const { container } = render(<Operators />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());
    expect(getOperatorTableRow(container, 'op1')).toBeInTheDocument();
    expect(getOperatorTableRow(container, 'op2')).toBeInTheDocument();
    expect(getOperatorTableRow(container, 'op3')).toBeInTheDocument();
  });

  it('角色映射为中文', async () => {
    render(<Operators />);
    await waitFor(() => expect(screen.getByText('管理员')).toBeInTheDocument());
    expect(screen.getAllByText('操作者')).toHaveLength(3);
  });

  it('active 状态显示"活跃"', async () => {
    const { container } = render(<Operators />);
    await waitFor(() => expect(container.querySelectorAll('.operators-table .op-status-active')).toHaveLength(2));
  });

  it('disabled 状态显示"禁用"', async () => {
    const { container } = render(<Operators />);
    await waitFor(() => {
      const badges = container.querySelectorAll('.operators-table .op-status-disabled');
      // 状态列 + 操作按钮都可能有"禁用"
      expect(badges.length).toBeGreaterThanOrEqual(1);
    });
  });

  it('expired 状态回归测试：当前显示原始值 expired', async () => {
    const { container } = render(<Operators />);
    await waitFor(() => expect(container.querySelectorAll('.operators-table .op-status-expired')).toHaveLength(1));
  });

  it('admin 角色不显示操作按钮', async () => {
    const { container } = render(<Operators />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());
    // admin 行不应有禁用/启用按钮
    const adminRow = getOperatorTableRow(container, 'admin');
    expect(adminRow.querySelector('.toggle-btn')).toBeNull();
  });

  it('operator 行显示禁用/启用按钮', async () => {
    const { container } = render(<Operators />);
    await waitFor(() => expect(getOperatorTableRow(container, 'op1')).toBeInTheDocument());
    const op1Row = getOperatorTableRow(container, 'op1');
    expect(op1Row.querySelector('.toggle-btn')).not.toBeNull();
  });

  it('可以打开账号策略授权并保存勾选', async () => {
    const { container } = render(<Operators />);
    await waitFor(() => expect(getOperatorTableRow(container, 'op1')).toBeInTheDocument());

    const op1Row = getOperatorTableRow(container, 'op1');
    await userEvent.click(op1Row.querySelector('.permission-btn') as HTMLButtonElement);

    await waitFor(() => expect(screen.getByText('策略授权：op1')).toBeInTheDocument());
    expect(screen.getByText('acc1')).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText('马丁'));
    expect(mockUpdatePermissions).toHaveBeenCalledWith(2, 10, ['flat', 'martin']);
  });

  it('渲染共享网址审核区域', async () => {
    render(<Operators />);
    await waitFor(() => expect(screen.getByText('共享网址审核')).toBeInTheDocument());
    expect(screen.getByText('https://shared.example.com/abc')).toBeInTheDocument();
    expect(screen.getByText('JND28WEB')).toBeInTheDocument();
    expect(screen.getByText('待检测')).toBeInTheDocument();
  });

  it('可以点击忽略待审核记录', async () => {
    render(<Operators />);
    await waitFor(() => expect(screen.getByText('忽略')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: '忽略' }));
    expect(mockIgnoreSharedUncovered).toHaveBeenCalledWith(101);
  });

  it('可以点击重新检测', async () => {
    render(<Operators />);
    await waitFor(() => expect(screen.getByText('重新检测')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: '重新检测' }));
    expect(mockRecheckSharedUncovered).toHaveBeenCalledWith(101);
  });

  it('可以选择共享组并加入', async () => {
    render(<Operators />);
    await waitFor(() => expect(screen.getByRole('combobox')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByRole('combobox'), '2');
    await userEvent.click(screen.getByRole('button', { name: '加入共享组' }));
    expect(mockJoinSharedGroup).toHaveBeenCalledWith(101, 2);
  });

  it('无共享组时显示提示', async () => {
    mockListSharedGroups.mockResolvedValueOnce({ code: 0, message: 'success', data: [] });
    mockListUncovered.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: { items: sharedMarketUncoveredRows, total: 1, page: 1, page_size: 10 },
    });
    render(<Operators />);
    await waitFor(() => expect(screen.getByText('暂无可用共享组')).toBeInTheDocument());
  });

  it('shows a prominent strategy permission shortcut for operators', async () => {
    const { container } = render(<Operators />);
    await waitFor(() => expect(container.querySelectorAll('.permission-shortcut-card').length).toBeGreaterThan(0));

    const shortcut = container.querySelector('.permission-shortcut-card') as HTMLButtonElement;
    expect(shortcut).not.toBeNull();
    await userEvent.click(shortcut);

    await waitFor(() => expect(mockListPermissions).toHaveBeenCalledWith(2));
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
