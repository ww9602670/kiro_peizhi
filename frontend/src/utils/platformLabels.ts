const PLATFORM_LABEL_MAP: Readonly<Record<string, string>> = Object.freeze({
  JND28WEB: '加拿大28网页版',
  JND282: '加拿大282.0版',
  LUCKYSB: '极速飞艇',
});

export function getPlatformLabel(platformType?: string | null): string {
  if (!platformType) return '-';
  return PLATFORM_LABEL_MAP[platformType] ?? platformType;
}
