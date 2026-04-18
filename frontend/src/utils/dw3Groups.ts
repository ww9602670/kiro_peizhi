export type Dw3GroupTabKey = 'bs' | 'oe';

export interface Dw3GroupOption {
  token: string;
  label: string;
}

export interface Dw3GroupTab {
  key: Dw3GroupTabKey;
  label: string;
  groups: readonly Dw3GroupOption[];
}

export const DW3_SIZE_GROUPS = [
  { token: 'DW3_BS_BBB', label: '大大大' },
  { token: 'DW3_BS_BBS', label: '大大小' },
  { token: 'DW3_BS_BSB', label: '大小大' },
  { token: 'DW3_BS_BSS', label: '大小小' },
  { token: 'DW3_BS_SBB', label: '小大大' },
  { token: 'DW3_BS_SBS', label: '小大小' },
  { token: 'DW3_BS_SSB', label: '小小大' },
  { token: 'DW3_BS_SSS', label: '小小小' },
] as const satisfies readonly Dw3GroupOption[];

export const DW3_ODD_EVEN_GROUPS = [
  { token: 'DW3_OE_OOO', label: '单单单' },
  { token: 'DW3_OE_OOE', label: '单单双' },
  { token: 'DW3_OE_OEO', label: '单双单' },
  { token: 'DW3_OE_OEE', label: '单双双' },
  { token: 'DW3_OE_EOO', label: '双单单' },
  { token: 'DW3_OE_EOE', label: '双单双' },
  { token: 'DW3_OE_EEO', label: '双双单' },
  { token: 'DW3_OE_EEE', label: '双双双' },
] as const satisfies readonly Dw3GroupOption[];

const DW3_GROUP_ORDER = [...DW3_SIZE_GROUPS, ...DW3_ODD_EVEN_GROUPS].map((item) => item.token);

const DW3_TAB_MAP: ReadonlyArray<Dw3GroupTab> = [
  { key: 'bs', label: '大小组合', groups: DW3_SIZE_GROUPS },
  { key: 'oe', label: '单双组合', groups: DW3_ODD_EVEN_GROUPS },
];

const DW3_GROUP_LABEL_MAP: Readonly<Record<string, string>> = Object.freeze(
  Object.fromEntries([...DW3_SIZE_GROUPS, ...DW3_ODD_EVEN_GROUPS].map((item) => [item.token, item.label]))
);

function splitAndNormalize(tokens: string[]): string[] {
  return tokens.map((item) => item.trim().toUpperCase()).filter(Boolean);
}

function splitPlayCode(playCode: string): string[] {
  if (!playCode.trim()) return [];
  return splitAndNormalize(playCode.split(','));
}

export function isDw3GroupToken(token: string): boolean {
  const normalized = token.trim().toUpperCase();
  return Object.prototype.hasOwnProperty.call(DW3_GROUP_LABEL_MAP, normalized);
}

export function hasOnlyDw3GroupTokens(playCode: string): boolean {
  const tokens = splitPlayCode(playCode);
  return tokens.length > 0 && tokens.every(isDw3GroupToken);
}

export function normalizeDw3GroupTokens(tokens: string[]): string[] {
  const deduped = new Set(splitAndNormalize(tokens));
  return DW3_GROUP_ORDER.filter((token) => deduped.has(token));
}

export function parseDw3PlayCode(playCode: string): {
  selectedBs: string[];
  selectedOe: string[];
  all: string[];
} {
  const normalized = normalizeDw3GroupTokens(splitPlayCode(playCode));
  return {
    selectedBs: DW3_SIZE_GROUPS.map((item) => item.token).filter((token) => normalized.includes(token)),
    selectedOe: DW3_ODD_EVEN_GROUPS.map((item) => item.token).filter((token) => normalized.includes(token)),
    all: normalized,
  };
}

export function buildDw3PlayCode(selectedBs: string[], selectedOe: string[]): string {
  return normalizeDw3GroupTokens([...selectedBs, ...selectedOe]).join(',');
}

export function getDw3GroupLabel(token: string): string {
  const normalized = token.trim().toUpperCase();
  return DW3_GROUP_LABEL_MAP[normalized] ?? token;
}

export function getDw3GroupTabs(): ReadonlyArray<Dw3GroupTab> {
  return DW3_TAB_MAP;
}

export function getDw3TheoreticalMaxStake(
  selectedBsCount: number,
  selectedOeCount: number,
  stageAmount: number
): number {
  if (!Number.isFinite(stageAmount) || stageAmount <= 0) return 0;
  const bsCount = Number.isFinite(selectedBsCount) ? Math.max(0, selectedBsCount) : 0;
  const oeCount = Number.isFinite(selectedOeCount) ? Math.max(0, selectedOeCount) : 0;
  return 125 * stageAmount * (bsCount + oeCount);
}
