import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import My from './My';

vi.mock('@/api/auth', () => ({
  fetchOperatorMe: vi.fn(),
  updateOperatorPassword: vi.fn(),
}));

vi.mock('@/api/request', () => ({
  isApiError: (error: unknown) => typeof error === 'object' && error !== null && 'message' in error,
}));

import { fetchOperatorMe, updateOperatorPassword } from '@/api/auth';

const mockFetchOperatorMe = vi.mocked(fetchOperatorMe);
const mockUpdateOperatorPassword = vi.mocked(updateOperatorPassword);

beforeEach(() => {
  vi.clearAllMocks();
  mockFetchOperatorMe.mockResolvedValue({
    code: 0,
    message: 'success',
    data: {
      username: 'testoperator',
      expire_date: '2026-12-31',
      max_accounts: 5,
      bound_accounts: 2,
      remaining_accounts: 3,
    },
  });
  mockUpdateOperatorPassword.mockResolvedValue({
    code: 0,
    message: 'success',
    data: null,
  });
});

describe('My page', () => {
  it('renders operator profile data from backend', async () => {
    render(<My />);

    await waitFor(() => {
      expect(screen.getByText('testoperator')).toBeInTheDocument();
    });

    expect(screen.getByText('2026-12-31')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getAllByText('3').length).toBeGreaterThan(0);
  });

  it('submits password change to backend', async () => {
    const user = userEvent.setup();
    render(<My />);

    await waitFor(() => {
      expect(screen.getByText('testoperator')).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText('当前密码'), 'oldpass');
    await user.type(screen.getByLabelText('新密码'), 'newpass123');
    await user.type(screen.getByLabelText('确认新密码'), 'newpass123');
    await user.click(screen.getByRole('button', { name: '确认修改' }));

    await waitFor(() => {
      expect(mockUpdateOperatorPassword).toHaveBeenCalledWith({
        old_password: 'oldpass',
        new_password: 'newpass123',
      });
    });

    expect(screen.getByText('密码修改成功。')).toBeInTheDocument();
  });

  it('blocks submit when the two new passwords do not match', async () => {
    const user = userEvent.setup();
    render(<My />);

    await waitFor(() => {
      expect(screen.getByText('testoperator')).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText('当前密码'), 'oldpass');
    await user.type(screen.getByLabelText('新密码'), 'newpass123');
    await user.type(screen.getByLabelText('确认新密码'), 'different123');
    await user.click(screen.getByRole('button', { name: '确认修改' }));

    expect(mockUpdateOperatorPassword).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('两次输入的新密码不一致。');
  });
});
