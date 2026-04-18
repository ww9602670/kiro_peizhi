import { describe, expect, it } from 'vitest';
import {
  buildDw3PlayCode,
  getDw3GroupLabel,
  getDw3TheoreticalMaxStake,
  hasOnlyDw3GroupTokens,
  isDw3GroupToken,
  parseDw3PlayCode,
} from './dw3Groups';

describe('dw3Groups', () => {
  it('recognizes valid DW3 group tokens', () => {
    expect(isDw3GroupToken('DW3_BS_BBB')).toBe(true);
    expect(isDw3GroupToken('dw3_oe_eee')).toBe(true);
    expect(isDw3GroupToken('DX1')).toBe(false);
  });

  it('parses and rebuilds play_code with stable ordering', () => {
    const parsed = parseDw3PlayCode('DW3_OE_OEO,DW3_BS_SSS,DW3_BS_BBB');
    expect(parsed.selectedBs).toEqual(['DW3_BS_BBB', 'DW3_BS_SSS']);
    expect(parsed.selectedOe).toEqual(['DW3_OE_OEO']);

    expect(buildDw3PlayCode(parsed.selectedBs, parsed.selectedOe)).toBe(
      'DW3_BS_BBB,DW3_BS_SSS,DW3_OE_OEO',
    );
  });

  it('detects valid DW3-only play_code values', () => {
    expect(hasOnlyDw3GroupTokens('DW3_BS_BBB,DW3_OE_OOO')).toBe(true);
    expect(hasOnlyDw3GroupTokens('DW3_BS_BBB,DX1')).toBe(false);
    expect(hasOnlyDw3GroupTokens('')).toBe(false);
  });

  it('computes theoretical max stake and Chinese labels', () => {
    expect(getDw3TheoreticalMaxStake(2, 3, 10)).toBe(6250);
    expect(getDw3TheoreticalMaxStake(2, 3, 0)).toBe(0);
    expect(getDw3GroupLabel('DW3_OE_EEE')).toBe('双双双');
    expect(getDw3GroupLabel('UNKNOWN')).toBe('UNKNOWN');
  });
});
