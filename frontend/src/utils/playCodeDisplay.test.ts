import { describe, expect, it } from 'vitest';
import { getPlayCodeDisplay } from './playCodeDisplay';
import { getPlatformLabel } from './platformLabels';

describe('playCodeDisplay', () => {
  it('uses backend play_code_name when available', () => {
    expect(getPlayCodeDisplay('红波单', 'SB1')).toBe('红波单');
  });

  it('localizes JND play codes when play_code_name is empty', () => {
    expect(getPlayCodeDisplay('', 'DX1,DS4')).toBe('大、双');
  });

  it('localizes 三字定位 group tokens when play_code_name is empty', () => {
    expect(getPlayCodeDisplay('', 'DW3_BS_BBB,DW3_OE_EEE')).toBe('大大大、双双双');
  });
});

describe('platformLabels', () => {
  it('maps platform codes to Chinese labels', () => {
    expect(getPlatformLabel('JND28WEB')).toBe('加拿大28网页版');
    expect(getPlatformLabel('JND282')).toBe('加拿大282.0版');
    expect(getPlatformLabel('LUCKYSB')).toBe('极速飞艇');
    expect(getPlatformLabel('WEB')).toBe('WEB');
    expect(getPlatformLabel('2.0')).toBe('2.0');
  });
});
