import operatorUpdatesMarkdown from '@/content/operator-updates.md?raw';

/**
 * 面向操作者的平台更新数据源。
 * 维护时只更新 `src/content/operator-updates.md`，页面会自动读取。
 */

export interface OperatorUpdateItem {
  id: string;
  date: string;
  title: string;
  summary: string;
  items: string[];
}

const ENTRY_HEADER_PATTERN = /^##\s+(\d{4}-\d{2}-\d{2})\s*\|\s*(.+)$/;

function slugify(title: string): string {
  return title
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9\u4e00-\u9fa5-]/g, '');
}

export function parseOperatorUpdates(markdown: string): OperatorUpdateItem[] {
  const entries: OperatorUpdateItem[] = [];
  const seen = new Map<string, number>();
  let currentDate = '';
  let currentTitle = '';
  let currentItems: string[] = [];

  const pushCurrent = () => {
    if (!currentDate || !currentTitle) {
      return;
    }
    const baseId = `${currentDate}-${slugify(currentTitle) || 'untitled'}`;
    const dup = seen.get(baseId) ?? 0;
    seen.set(baseId, dup + 1);
    const id = dup === 0 ? baseId : `${baseId}-${dup + 1}`;
    entries.push({
      id,
      date: currentDate,
      title: currentTitle,
      summary: currentItems[0] ?? '',
      items: [...currentItems],
    });
  };

  for (const rawLine of markdown.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) {
      continue;
    }

    const headerMatch = line.match(ENTRY_HEADER_PATTERN);
    if (headerMatch) {
      pushCurrent();
      currentDate = headerMatch[1];
      currentTitle = headerMatch[2];
      currentItems = [];
      continue;
    }

    if (line.startsWith('- ') && currentDate) {
      currentItems.push(line.slice(2).trim());
    }
  }

  pushCurrent();
  return entries;
}

export const operatorUpdates = parseOperatorUpdates(operatorUpdatesMarkdown);
