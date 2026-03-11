import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  listStrategies,
  createStrategy,
  updateStrategy,
  deleteStrategy,
  startStrategy,
  pauseStrategy,
  stopStrategy,
} from './strategies';

vi.mock('@/api/request', () => ({
  request: vi.fn(),
}));

import { request } from '@/api/request';
const mockRequest = vi.mocked(request);

beforeEach(() => {
  mockRequest.mockReset();
});

const sampleStrategy = {
  id: 1,
  account_id: 1,
  name: '大小平注',
  type: 'flat',
  play_code: 'DX1',
  base_amount: 10.0,
  martin_sequence: null,
  bet_timing: 30,
  simulation: false,
  status: 'stopped',
  martin_level: 0,
  stop_loss: null,
  take_profit: null,
  daily_pnl: 0.0,
  total_pnl: 0.0,
};

describe('listStrategies', () => {
  it('调用正确路径', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: [] });

    const res = await listStrategies();

    expect(mockRequest).toHaveBeenCalledWith('/strategies');
    expect(res.data).toEqual([]);
  });

  it('返回策略列表数据', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: [sampleStrategy] });

    const res = await listStrategies();

    expect(res.data).toHaveLength(1);
    expect(res.data![0].name).toBe('大小平注');
    expect(res.data![0].type).toBe('flat');
  });
});

describe('createStrategy', () => {
  it('平注策略：调用正确路径、方法和请求体', async () => {
    const payload = {
      account_id: 1,
      name: '大小平注',
      type: 'flat' as const,
      play_code: 'DX1',
      base_amount: 10.0,
      martin_sequence: null,
      bet_timing: 30,
      simulation: false,
      stop_loss: null,
      take_profit: null,
    };
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: sampleStrategy });

    const res = await createStrategy(payload);

    expect(mockRequest).toHaveBeenCalledWith('/strategies', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    expect(res.data!.id).toBe(1);
  });

  it('马丁策略：martin_sequence 正确传递', async () => {
    const payload = {
      account_id: 1,
      name: '马丁追注',
      type: 'martin' as const,
      play_code: 'DX2',
      base_amount: 5.0,
      martin_sequence: [1, 2, 4, 8, 16],
      bet_timing: 45,
      simulation: true,
      stop_loss: 100.0,
      take_profit: 200.0,
    };
    const martinStrategy = {
      ...sampleStrategy,
      id: 2,
      name: '马丁追注',
      type: 'martin',
      play_code: 'DX2',
      base_amount: 5.0,
      martin_sequence: [1, 2, 4, 8, 16],
      bet_timing: 45,
      simulation: true,
      stop_loss: 100.0,
      take_profit: 200.0,
    };
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: martinStrategy });

    const res = await createStrategy(payload);

    const body = JSON.parse(mockRequest.mock.calls[0][1]!.body as string);
    expect(body.martin_sequence).toEqual([1, 2, 4, 8, 16]);
    expect(body.simulation).toBe(true);
    expect(res.data!.martin_sequence).toEqual([1, 2, 4, 8, 16]);
  });
});

describe('updateStrategy', () => {
  it('调用正确路径、方法和请求体', async () => {
    const update = { name: '新策略名', base_amount: 20.0 };
    const updated = { ...sampleStrategy, name: '新策略名', base_amount: 20.0 };
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: updated });

    const res = await updateStrategy(1, update);

    expect(mockRequest).toHaveBeenCalledWith('/strategies/1', {
      method: 'PUT',
      body: JSON.stringify(update),
    });
    expect(res.data!.name).toBe('新策略名');
  });
});

describe('deleteStrategy', () => {
  it('调用正确路径和方法', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: null });

    await deleteStrategy(42);

    expect(mockRequest).toHaveBeenCalledWith('/strategies/42', {
      method: 'DELETE',
    });
  });
});

describe('startStrategy', () => {
  it('调用正确路径和方法', async () => {
    const running = { ...sampleStrategy, status: 'running' };
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: running });

    const res = await startStrategy(1);

    expect(mockRequest).toHaveBeenCalledWith('/strategies/1/start', {
      method: 'POST',
    });
    expect(res.data!.status).toBe('running');
  });
});

describe('pauseStrategy', () => {
  it('调用正确路径和方法', async () => {
    const paused = { ...sampleStrategy, status: 'paused' };
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: paused });

    const res = await pauseStrategy(1);

    expect(mockRequest).toHaveBeenCalledWith('/strategies/1/pause', {
      method: 'POST',
    });
    expect(res.data!.status).toBe('paused');
  });
});

describe('stopStrategy', () => {
  it('调用正确路径和方法', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: sampleStrategy });

    const res = await stopStrategy(1);

    expect(mockRequest).toHaveBeenCalledWith('/strategies/1/stop', {
      method: 'POST',
    });
    expect(res.data!.status).toBe('stopped');
  });
});
