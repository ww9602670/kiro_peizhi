import { describe, expect, it } from 'vitest';
import { operatorUpdates, parseOperatorUpdates } from './operatorUpdates';

describe('operatorUpdates', () => {
  it('parses markdown entries into operator-facing updates', () => {
    const parsed = parseOperatorUpdates(`
# 平台更新

## 2026-04-18 | 多策略下注更稳了
- 第一条
- 第二条
    `);

    expect(parsed).toEqual([
      {
        id: '2026-04-18-多策略下注更稳了',
        date: '2026-04-18',
        title: '多策略下注更稳了',
        summary: '第一条',
        items: ['第一条', '第二条'],
      },
    ]);
  });

  it('loads the current persisted update document', () => {
    expect(operatorUpdates.length).toBeGreaterThan(0);
    expect(operatorUpdates[0].title).toBeTruthy();
    expect(operatorUpdates[0].items.length).toBeGreaterThan(0);
  });
});
