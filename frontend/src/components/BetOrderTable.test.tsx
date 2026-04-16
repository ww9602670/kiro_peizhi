import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import fc from 'fast-check';
import BetOrderTable from './BetOrderTable';
import type { BetOrderInfo } from '@/types/api/bet-order';

const EXPECTED_LABELS = ['期号', '玩法', '金额', '赔率', '状态', '开奖', '盈亏', '模拟', '时间'];

const STATUS_VALUES = [
  'pending', 'betting', 'bet_success', 'bet_failed',
  'settling', 'settled', 'pending_match', 'settle_timeout',
  'settle_failed', 'reconcile_error', 'cancelled',
];

const arbBetOrder: fc.Arbitrary<BetOrderInfo> = fc.record({
  id: fc.nat(),
  idempotent_id: fc.string({ minLength: 1, maxLength: 20 }),
  strategy_id: fc.nat(),
  account_id: fc.nat(),
  issue: fc.string({ minLength: 1, maxLength: 20 }),
  key_code: fc.string({ minLength: 1, maxLength: 10 }),
  key_code_name: fc.string({ minLength: 1, maxLength: 10 }),
  amount: fc.float({ min: Math.fround(0.01), max: Math.fround(99999), noNaN: true }),
  odds: fc.oneof(fc.constant(null), fc.float({ min: Math.fround(1), max: Math.fround(100), noNaN: true })),
  status: fc.constantFrom(...STATUS_VALUES),
  open_result: fc.oneof(fc.constant(null), fc.string({ minLength: 1, maxLength: 10 })),
  sum_value: fc.oneof(fc.constant(null), fc.integer({ min: 0, max: 27 })),
  is_win: fc.oneof(fc.constant(null), fc.constantFrom(0, 1, -1)),
  pnl: fc.oneof(fc.constant(null), fc.float({ min: Math.fround(-9999), max: Math.fround(9999), noNaN: true })),
  simulation: fc.boolean(),
  martin_level: fc.oneof(fc.constant(null), fc.nat({ max: 10 })),
  bet_at: fc.oneof(fc.constant(null), fc.string({ minLength: 1, maxLength: 20 })),
  settled_at: fc.oneof(fc.constant(null), fc.string({ minLength: 1, maxLength: 20 })),
  fail_reason: fc.oneof(fc.constant(null), fc.string({ minLength: 0, maxLength: 30 })),
});

/**
 * Property 1: BetOrderTable data-label 完整性
 * Validates: Requirements 7.1, 1.2
 */
describe('Property 1: BetOrderTable data-label 完整性', () => {
  it('每行的 9 个 td 应包含正确的 data-label 属性', () => {
    fc.assert(
      fc.property(fc.array(arbBetOrder, { minLength: 1, maxLength: 5 }), (orders) => {
        const { container } = render(<BetOrderTable orders={orders} />);
        const rows = container.querySelectorAll('tbody tr');
        expect(rows.length).toBe(orders.length);
        rows.forEach((row) => {
          const tds = row.querySelectorAll('td');
          expect(tds.length).toBe(9);
          tds.forEach((td, i) => {
            expect(td.getAttribute('data-label')).toBe(EXPECTED_LABELS[i]);
          });
        });
      }),
      { numRuns: 100 },
    );
  });
});


/**
 * Property 4: BetOrderTable 行样式逻辑正确性
 * Validates: Requirements 1.4
 */
describe('Property 4: BetOrderTable 行样式逻辑正确性', () => {
  it('行 className 应与 status/is_win 正确对应', () => {
    fc.assert(
      fc.property(arbBetOrder, (order) => {
        const { container } = render(<BetOrderTable orders={[order]} />);
        const row = container.querySelector('tbody tr')!;
        if (order.status === 'bet_failed') {
          expect(row.classList.contains('row-fail')).toBe(true);
        } else if (order.is_win === 1) {
          expect(row.classList.contains('row-win')).toBe(true);
        } else if (order.is_win === 0) {
          expect(row.classList.contains('row-lose')).toBe(true);
        } else {
          expect(row.classList.contains('row-fail')).toBe(false);
          expect(row.classList.contains('row-win')).toBe(false);
          expect(row.classList.contains('row-lose')).toBe(false);
        }
      }),
      { numRuns: 100 },
    );
  });
});
