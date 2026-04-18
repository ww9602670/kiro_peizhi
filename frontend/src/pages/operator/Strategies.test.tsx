import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import Strategies from './Strategies';

vi.mock('@/api/strategies', () => ({
  listStrategies: vi.fn(),
  deleteStrategy: vi.fn(),
  startStrategy: vi.fn(),
  pauseStrategy: vi.fn(),
  stopStrategy: vi.fn(),
}));

vi.mock('@/api/request', () => ({
  isApiError: () => false,
}));

vi.mock('@/components/CountdownDisplay', () => ({
  CountdownDisplay: () => <div data-testid="countdown" />,
}));

const strategyFormSpy = vi.hoisted(() => vi.fn());

vi.mock('./StrategyForm', () => ({
  default: (props: { initialAccountId?: number }) => {
    strategyFormSpy(props);
    return <div data-testid="strategy-form">account:{props.initialAccountId}</div>;
  },
}));

import { listStrategies } from '@/api/strategies';

const mockListStrategies = vi.mocked(listStrategies);

beforeEach(() => {
  vi.clearAllMocks();
  mockListStrategies.mockResolvedValue({ code: 0, message: 'success', data: [] });
});

describe('Strategies create intent', () => {
  it('opens create form with the intended account and consumes the intent', async () => {
    const onConsumed = vi.fn();

    render(
      <Strategies
        createIntent={{ accountId: 42, nonce: 1 }}
        onCreateIntentConsumed={onConsumed}
      />,
    );

    expect(await screen.findByTestId('strategy-form')).toHaveTextContent('account:42');
    expect(strategyFormSpy).toHaveBeenCalledWith(
      expect.objectContaining({ initialAccountId: 42 }),
    );
    await waitFor(() => expect(onConsumed).toHaveBeenCalledTimes(1));
  });
});
