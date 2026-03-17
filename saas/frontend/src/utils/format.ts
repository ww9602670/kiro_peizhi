/**
 * 通用格式化工具
 * - fenToYuan: 分→元
 * - formatDate: 日期格式化
 * - maskPassword: 密码脱敏
 * - formatPnl: 盈亏格式化（带正负号）
 */

/** 分→元，保留 2 位小数 */
export function fenToYuan(fen: number): string {
  return (fen / 100).toFixed(2);
}

/** 日期格式化：截取 YYYY-MM-DD HH:mm:ss */
export function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '-';
  return dateStr.slice(0, 19);
}

/** 密码脱敏：前2位 + **** */
export function maskPassword(pwd: string): string {
  if (pwd.length < 2) return '****';
  return pwd.slice(0, 2) + '****';
}

/** 盈亏格式化：正数带 +，负数自带 -，null 显示 - */
export function formatPnl(pnl: number | null | undefined): string {
  if (pnl == null) return '-';
  const str = pnl.toFixed(2);
  return pnl > 0 ? `+${str}` : str;
}
