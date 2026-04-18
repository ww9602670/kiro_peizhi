import { getDw3GroupLabel, isDw3GroupToken } from './dw3Groups';
import { getKeyCodeName } from './key-code-map';

function formatRawPlayCode(playCode: string): string {
  const trimmed = playCode.trim();
  if (!trimmed) return '-';

  return trimmed
    .split(',')
    .map((token) => token.trim())
    .filter(Boolean)
    .map((token) => (isDw3GroupToken(token) ? getDw3GroupLabel(token) : getKeyCodeName(token)))
    .join('、');
}

export function getPlayCodeDisplay(playCodeName?: string | null, playCode?: string | null): string {
  const preferredName = playCodeName?.trim();
  if (preferredName) return preferredName;
  return formatRawPlayCode(playCode ?? '');
}
