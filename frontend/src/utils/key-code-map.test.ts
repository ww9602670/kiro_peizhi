/**
 * KeyCode 映射工具单元测试
 *
 * 覆盖：KEY_CODE_MAP 完整性、getKeyCodeName 已知/未知 KeyCode。
 */
import { describe, it, expect } from 'vitest'
import { KEY_CODE_MAP, getKeyCodeName } from './key-code-map'

describe('KEY_CODE_MAP 完整性', () => {
  it('应包含大小 DX1/DX2', () => {
    expect(KEY_CODE_MAP['DX1']).toBe('大')
    expect(KEY_CODE_MAP['DX2']).toBe('小')
  })

  it('应包含单双 DS3/DS4', () => {
    expect(KEY_CODE_MAP['DS3']).toBe('单')
    expect(KEY_CODE_MAP['DS4']).toBe('双')
  })

  it('应包含极值 JDX5/JDX6', () => {
    expect(KEY_CODE_MAP['JDX5']).toBe('极大')
    expect(KEY_CODE_MAP['JDX6']).toBe('极小')
  })

  it('应包含组合 ZH7/ZH8/ZH9/ZH10', () => {
    expect(KEY_CODE_MAP['ZH7']).toBe('大单')
    expect(KEY_CODE_MAP['ZH8']).toBe('大双')
    expect(KEY_CODE_MAP['ZH9']).toBe('小单')
    expect(KEY_CODE_MAP['ZH10']).toBe('小双')
  })

  it('应包含色波 SB1/SB2/SB3', () => {
    expect(KEY_CODE_MAP['SB1']).toBe('红波')
    expect(KEY_CODE_MAP['SB2']).toBe('绿波')
    expect(KEY_CODE_MAP['SB3']).toBe('蓝波')
  })

  it('应包含豹子 BZ4', () => {
    expect(KEY_CODE_MAP['BZ4']).toBe('豹子')
  })

  it('应包含龙虎和 LHH_L/LHH_H/LHH_HE', () => {
    expect(KEY_CODE_MAP['LHH_L']).toBe('龙')
    expect(KEY_CODE_MAP['LHH_H']).toBe('虎')
    expect(KEY_CODE_MAP['LHH_HE']).toBe('和')
  })

  it('应包含和值 HZ1~HZ28 → 和值0~和值27', () => {
    for (let i = 1; i <= 28; i++) {
      expect(KEY_CODE_MAP[`HZ${i}`]).toBe(`和值${i - 1}`)
    }
  })

  it('应包含单球号码 B{n}QH{d}（共 30 条）', () => {
    for (let n = 1; n <= 3; n++) {
      for (let d = 0; d <= 9; d++) {
        const key = `B${n}QH${d}`
        expect(KEY_CODE_MAP[key]).toBeDefined()
      }
    }
    expect(KEY_CODE_MAP['B1QH0']).toBe('第一球0')
    expect(KEY_CODE_MAP['B2QH5']).toBe('第二球5')
    expect(KEY_CODE_MAP['B3QH9']).toBe('第三球9')
  })

  it('应包含单球两面 B{n}LM_{suffix}（共 12 条）', () => {
    const suffixes = ['DA', 'X', 'D', 'S']
    for (let n = 1; n <= 3; n++) {
      for (const s of suffixes) {
        expect(KEY_CODE_MAP[`B${n}LM_${s}`]).toBeDefined()
      }
    }
    expect(KEY_CODE_MAP['B1LM_DA']).toBe('第一球大')
    expect(KEY_CODE_MAP['B1LM_X']).toBe('第一球小')
    expect(KEY_CODE_MAP['B2LM_D']).toBe('第二球单')
    expect(KEY_CODE_MAP['B3LM_S']).toBe('第三球双')
  })

  it('映射表总条目数 >= 87', () => {
    // 2+2+2+4+3+1+3+28+30+12 = 87
    expect(Object.keys(KEY_CODE_MAP).length).toBeGreaterThanOrEqual(87)
  })
})

describe('getKeyCodeName', () => {
  it('已知 KeyCode 返回中文名', () => {
    expect(getKeyCodeName('DX1')).toBe('大')
    expect(getKeyCodeName('HZ14')).toBe('和值13')
    expect(getKeyCodeName('B1QH0')).toBe('第一球0')
    expect(getKeyCodeName('LHH_L')).toBe('龙')
  })

  it('未知 KeyCode 返回原值，不抛异常', () => {
    expect(getKeyCodeName('UNKNOWN')).toBe('UNKNOWN')
    expect(getKeyCodeName('')).toBe('')
    expect(getKeyCodeName('XYZ123')).toBe('XYZ123')
  })
})


/**
 * P13: KeyCode 映射完整性 — 属性测试
 *
 * **Validates: Requirements 3.1**
 *
 * 属性：
 * 1. 对任意字符串输入，getKeyCodeName 永不抛异常
 * 2. 所有 KEY_CODE_MAP 中的已知 KeyCode 对应非空中文名
 */
describe('P13: KeyCode 映射完整性 (PBT)', () => {
  it('getKeyCodeName 对任意字符串输入永不抛异常，且返回字符串', async () => {
    const fc = await import('fast-check')
    fc.assert(
      fc.property(fc.string(), (s) => {
        const result = getKeyCodeName(s)
        return typeof result === 'string'
      }),
      { numRuns: 500 },
    )
  })

  it('所有已知 KeyCode 对应非空中文名', () => {
    for (const [key, name] of Object.entries(KEY_CODE_MAP)) {
      expect(name.length).toBeGreaterThan(0)
      expect(getKeyCodeName(key)).toBe(name)
    }
  })
})
