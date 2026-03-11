import { describe, expect, it } from 'vitest';
import { fenToYuan, formatDate, formatPnl, maskPassword } from './format';

describe('fenToYuan', () => {
  it('converts fen to yuan with 2 decimals', () => {
    expect(fenToYuan(100)).toBe('1.00');
    expect(fenToYuan(0)).toBe('0.00');
    expect(fenToYuan(1)).toBe('0.01');
    expect(fenToYuan(12345)).toBe('123.45');
    expect(fenToYuan(-500)).toBe('-5.00');
  });
});

describe('formatDate', () => {
  it('truncates to 19 chars', () => {
    expect(formatDate('2026-03-02 10:01:30.123')).toBe('2026-03-02 10:01:30');
    expect(formatDate('2026-03-02 10:01:30')).toBe('2026-03-02 10:01:30');
  });

  it('returns - for null/undefined/empty', () => {
    expect(formatDate(null)).toBe('-');
    expect(formatDate(undefined)).toBe('-');
    expect(formatDate('')).toBe('-');
  });
});

describe('maskPassword', () => {
  it('masks password with first 2 chars + ****', () => {
    expect(maskPassword('abcdef')).toBe('ab****');
    expect(maskPassword('xy')).toBe('xy****');
  });

  it('returns **** for short passwords', () => {
    expect(maskPassword('a')).toBe('****');
    expect(maskPassword('')).toBe('****');
  });
});

describe('formatPnl', () => {
  it('formats positive with + prefix', () => {
    expect(formatPnl(5.5)).toBe('+5.50');
    expect(formatPnl(0.01)).toBe('+0.01');
  });

  it('formats negative with - prefix', () => {
    expect(formatPnl(-3.2)).toBe('-3.20');
  });

  it('formats zero without prefix', () => {
    expect(formatPnl(0)).toBe('0.00');
  });

  it('returns - for null/undefined', () => {
    expect(formatPnl(null)).toBe('-');
    expect(formatPnl(undefined)).toBe('-');
  });
});


/**
 * P14: 密码脱敏正确性 — 属性测试
 *
 * **Validates: Requirements 1.3**
 *
 * 属性：
 * 1. len ≥ 2 → 结果为前2位 + '****'
 * 2. len < 2 → 结果为 '****'
 * 3. 第3位及之后的字符不会泄露到结果中
 */
describe('P14: 密码脱敏正确性 (PBT)', () => {
  it('对任意字符串密码，脱敏结果符合规则且不泄露后续字符', async () => {
    const fc = await import('fast-check');
    fc.assert(
      fc.property(fc.string(), (pwd) => {
        const result = maskPassword(pwd);
        if (pwd.length < 2) {
          return result === '****';
        }
        // len >= 2: first 2 chars + '****'
        return result === pwd.slice(0, 2) + '****';
      }),
      { numRuns: 500 },
    );
  });
});

/**
 * P17: 元分转换往返一致性 — 属性测试
 *
 * **Validates: Requirements 7.1**
 *
 * 属性：对任意整数分值，fenToYuan 转换后再解析回分值等于原值
 * Math.round(parseFloat(fenToYuan(fen)) * 100) === fen
 */
describe('P17: 元分转换往返一致性 (PBT)', () => {
  it('fenToYuan 往返转换一致', async () => {
    const fc = await import('fast-check');
    fc.assert(
      fc.property(
        fc.integer({ min: -10_000_000, max: 10_000_000 }),
        (fen) => {
          const yuan = fenToYuan(fen);
          const backToFen = Math.round(parseFloat(yuan) * 100);
          return backToFen === fen;
        },
      ),
      { numRuns: 500 },
    );
  });
});
