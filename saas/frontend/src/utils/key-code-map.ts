/**
 * KeyCode → 中文名映射
 *
 * 覆盖所有 PC28 玩法，编码规则参照 PLATFORM_API_REFERENCE.md。
 */

const BALL_NAMES: Record<number, string> = { 1: '第一球', 2: '第二球', 3: '第三球' }
const LM_NAMES: Record<string, string> = { DA: '大', X: '小', D: '单', S: '双' }

function buildKeyCodeMap(): Record<string, string> {
  const m: Record<string, string> = {
    // 大小
    DX1: '大',
    DX2: '小',
    // 单双
    DS3: '单',
    DS4: '双',
    // 极值
    JDX5: '极大',
    JDX6: '极小',
    // 组合
    ZH7: '大单',
    ZH8: '大双',
    ZH9: '小单',
    ZH10: '小双',
    // 色波
    SB1: '红波',
    SB2: '绿波',
    SB3: '蓝波',
    // 豹子
    BZ4: '豹子',
    // 龙虎和
    LHH_L: '龙',
    LHH_H: '虎',
    LHH_HE: '和',
  }

  // 和值 HZ1~HZ28 → 和值0~和值27
  for (let i = 1; i <= 28; i++) {
    m[`HZ${i}`] = `和值${i - 1}`
  }

  // 单球号码 B{n}QH{d}
  for (let n = 1; n <= 3; n++) {
    for (let d = 0; d <= 9; d++) {
      m[`B${n}QH${d}`] = `${BALL_NAMES[n]}${d}`
    }
  }

  // 单球两面 B{n}LM_{suffix}
  for (let n = 1; n <= 3; n++) {
    for (const [suffix, label] of Object.entries(LM_NAMES)) {
      m[`B${n}LM_${suffix}`] = `${BALL_NAMES[n]}${label}`
    }
  }

  return m
}

/** 完整的 KeyCode → 中文名映射表 */
export const KEY_CODE_MAP: Record<string, string> = buildKeyCodeMap()

/**
 * 获取 KeyCode 对应的中文名。
 * 未知 KeyCode 返回原值，不抛异常。
 */
export function getKeyCodeName(keyCode: string): string {
  return Object.prototype.hasOwnProperty.call(KEY_CODE_MAP, keyCode)
    ? KEY_CODE_MAP[keyCode]
    : keyCode
}
